"""Live scenarios run against an Open Responses endpoint over HTTP."""

import json
import os
import time

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import HTTPError, Request, URLError, urlopen  # type: ignore

from .objects import check_error_body, check_response, function_calls
from .rules import Finding
from .sse import parse_sse
from .stream import validate_stream

NONEXISTENT_MODEL = "open-responses-conformance-nonexistent-model"

WEATHER_TOOL = {
    "type": "function", "name": "get_weather", "description": "Get the current weather for a city.",
    "parameters": {"type": "object", "properties": {"location": {"type": "string", "description": "City name"}},
                   "required": ["location"], "additionalProperties": False},
    "strict": True,
}
TIME_TOOL = {
    "type": "function", "name": "get_time", "description": "Get the current time in a city.",
    "parameters": {"type": "object", "properties": {"location": {"type": "string", "description": "City name"}},
                   "required": ["location"], "additionalProperties": False},
    "strict": True,
}


class HttpResult(object):
    def __init__(self, status, content_type, body, elapsed, error=None):
        self.status = status
        self.content_type = content_type or ""
        self.body = body
        self.elapsed = elapsed
        self.error = error


class Client(object):
    def __init__(self, base_url, api_key=None, headers=None, timeout=60.0):
        self.url = base_url.rstrip("/") + "/responses"
        self.api_key = api_key
        self.headers = dict(headers or {})
        self.timeout = timeout

    def post(self, body, stream=False):
        data = json.dumps(body).encode("utf-8")
        h = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer %s" % self.api_key
        h.update(self.headers)
        req = Request(self.url, data=data, headers=h, method="POST")
        start = time.time()
        try:
            resp = urlopen(req, timeout=self.timeout)
            raw = resp.read()
            return HttpResult(resp.status, resp.headers.get("Content-Type"), raw.decode("utf-8", "replace"),
                              time.time() - start)
        except HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            return HttpResult(exc.code, exc.headers.get("Content-Type") if exc.headers else "",
                              raw.decode("utf-8", "replace"), time.time() - start)
        except (URLError, OSError, ValueError) as exc:
            return HttpResult(0, "", "", time.time() - start, error=str(getattr(exc, "reason", exc)))


class ScenarioResult(object):
    def __init__(self, sid, title):
        self.id = sid
        self.title = title
        self.findings = []
        self.notes = []
        self.elapsed = 0.0
        self.skipped = None
        self.transcript = None

    @property
    def passed(self):
        return self.skipped is None and not self.findings

    def to_dict(self):
        return {"id": self.id, "title": self.title, "passed": self.passed, "skipped": self.skipped,
                "elapsed_ms": int(self.elapsed * 1000), "notes": self.notes,
                "findings": [f.to_dict() for f in self.findings]}


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _request_ok(r, res):
    if r.error:
        res.findings.append(Finding("OR001", "request failed: %s" % r.error, res.id))
        return False
    if not 200 <= r.status < 300:
        res.findings.append(Finding("OR001", "HTTP %d: %s" % (r.status, r.body[:120].replace("\n", " ")), res.id))
        return False
    return True


def _json_response(r, res):
    if "application/json" not in r.content_type.lower():
        res.findings.append(Finding("OR002", "Content-Type is %r" % (r.content_type or "missing"), res.id))
    try:
        return json.loads(r.body)
    except ValueError:
        res.findings.append(Finding("OR004", "body is not JSON: %s" % r.body[:60], res.id))
        return None


def _stream(r, res):
    if "text/event-stream" not in r.content_type.lower():
        res.findings.append(Finding("OR003", "Content-Type is %r" % (r.content_type or "missing"), res.id))
    outcome = validate_stream(parse_sse(r.body), where=res.id)
    res.findings.extend(outcome.findings)
    res.notes.append("%d events, terminal %s" % (outcome.event_count, outcome.terminal or "none"))
    return outcome


def _text_of(output):
    texts = []
    for item in output or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    return "".join(texts)


# ----------------------------------------------------------------------
# scenarios
# ----------------------------------------------------------------------

def s_basic_text(c, model, res):
    r = c.post({"model": model, "input": "Reply with one short sentence."})
    res.elapsed = r.elapsed
    res.transcript = r
    if not _request_ok(r, res):
        return
    body = _json_response(r, res)
    if body is None:
        return
    res.findings.extend(check_response(body, res.id))
    if isinstance(body, dict):
        text = _text_of(body.get("output"))
        res.notes.append("status %s, %d chars of text" % (body.get("status"), len(text)))


def s_streaming_text(c, model, res):
    r = c.post({"model": model, "input": "Reply with one short sentence.", "stream": True}, stream=True)
    res.elapsed = r.elapsed
    res.transcript = r
    if _request_ok(r, res):
        _stream(r, res)


def s_streaming_tool_call(c, model, res):
    r = c.post({"model": model, "input": "What is the weather in Coimbatore? Use the tool.",
                "tools": [WEATHER_TOOL], "tool_choice": "required", "stream": True}, stream=True)
    res.elapsed = r.elapsed
    res.transcript = r
    if not _request_ok(r, res):
        return
    outcome = _stream(r, res)
    calls = function_calls(outcome.output())
    if not calls:
        res.findings.append(Finding("OR501", "tool_choice 'required' produced no function_call", res.id))
    else:
        res.notes.append("called %s(%s)" % (calls[0].get("name"), str(calls[0].get("arguments"))[:40]))


def s_tool_choice_none(c, model, res):
    r = c.post({"model": model, "input": "What is the weather in Coimbatore?",
                "tools": [WEATHER_TOOL], "tool_choice": "none"})
    res.elapsed = r.elapsed
    res.transcript = r
    if not _request_ok(r, res):
        return
    body = _json_response(r, res)
    if isinstance(body, dict):
        calls = function_calls(body.get("output"))
        if calls:
            res.findings.append(Finding("OR503", "tool_choice 'none' but %s was called" % calls[0].get("name"), res.id))


def s_allowed_tools(c, model, res):
    r = c.post({"model": model, "input": "Call get_time for Coimbatore right now.",
                "tools": [WEATHER_TOOL, TIME_TOOL],
                "tool_choice": {"type": "allowed_tools", "tools": [{"type": "function", "name": "get_weather"}]}})
    res.elapsed = r.elapsed
    res.transcript = r
    if not _request_ok(r, res):
        return
    body = _json_response(r, res)
    if isinstance(body, dict):
        bad = [f.get("name") for f in function_calls(body.get("output")) if f.get("name") != "get_weather"]
        if bad:
            res.findings.append(Finding("OR502", "called %s, which is not in allowed_tools" % bad[0], res.id))
        res.findings.extend(f for f in check_response(body, res.id) if f.rule_id in ("OR305", "OR307"))


def s_invalid_request(c, model, res):
    r = c.post({"model": model, "input": 12345})
    res.elapsed = r.elapsed
    res.transcript = r
    if r.error:
        res.findings.append(Finding("OR001", "request failed: %s" % r.error, res.id))
        return
    res.notes.append("HTTP %d" % r.status)
    res.findings.extend(check_error_body(r.status, r.content_type, r.body, res.id))


def s_unknown_model(c, model, res):
    r = c.post({"model": NONEXISTENT_MODEL, "input": "Hello"})
    res.elapsed = r.elapsed
    res.transcript = r
    if r.error:
        res.findings.append(Finding("OR001", "request failed: %s" % r.error, res.id))
        return
    res.notes.append("HTTP %d" % r.status)
    res.findings.extend(check_error_body(r.status, r.content_type, r.body, res.id))


SCENARIOS = [
    ("basic-text", "Non-streaming text response", s_basic_text),
    ("streaming-text", "Streaming text response", s_streaming_text),
    ("streaming-tool-call", "Streaming function call (tool_choice required)", s_streaming_tool_call),
    ("tool-choice-none", "tool_choice none is honoured", s_tool_choice_none),
    ("allowed-tools", "allowed_tools is enforced", s_allowed_tools),
    ("invalid-request", "Invalid request returns a structured 4xx error", s_invalid_request),
    ("unknown-model", "Unknown model returns a structured 4xx error", s_unknown_model),
]
SCENARIO_IDS = [s[0] for s in SCENARIOS]


def run_scenarios(base_url, model, api_key=None, headers=None, timeout=60.0, only=None, skip=None,
                  save_dir=None):
    client = Client(base_url, api_key=api_key, headers=headers, timeout=timeout)
    results = []
    for sid, title, fn in SCENARIOS:
        res = ScenarioResult(sid, title)
        if (only and sid not in only) or (skip and sid in skip):
            res.skipped = "not selected"
            results.append(res)
            continue
        fn(client, model, res)
        if save_dir and res.transcript is not None:
            _save(save_dir, res)
        results.append(res)
    return results


def _save(save_dir, res):
    os.makedirs(save_dir, exist_ok=True)
    t = res.transcript
    ext = "sse" if "event-stream" in t.content_type else "json"
    with open(os.path.join(save_dir, "%s.%s" % (res.id, ext)), "w") as fh:
        fh.write(t.body)
    with open(os.path.join(save_dir, "%s.meta.json" % res.id), "w") as fh:
        json.dump({"status": t.status, "content_type": t.content_type, "elapsed_ms": int(t.elapsed * 1000),
                   "error": t.error}, fh, indent=2)
