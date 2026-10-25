"""A deterministic, spec-correct Open Responses mock server with fault injection.

Use it to test clients: point a client library at the mock, switch on a
fault, and check that the client copes (or fails loudly). The checker's own
test-suite uses it the other way round: every fault must be detected.

    open-responses-conformance mock --port 8080
    open-responses-conformance mock --port 8080 --fault text-mismatch --fault sse-id
"""

import copy
import json
import re
import threading
import time
import uuid

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # pragma: no cover
    from BaseHTTPServer import BaseHTTPRequestHandler  # type: ignore
    ThreadingHTTPServer = None

DEFAULT_REPLY = "Hello from the Open Responses mock server."
NONEXISTENT_MARKER = "nonexistent"

# name -> (description, expected rule ids the checker must report)
FAULTS = {
    "server-error": ("Every valid request returns HTTP 500", ["OR001"]),
    "wrong-content-type": ("Responses and streams are served as text/plain", ["OR002", "OR003"]),
    "invalid-json": ("One SSE data line is not JSON", ["OR004"]),
    "event-type-mismatch": ("SSE event: name differs from the body type", ["OR101"]),
    "missing-type": ("One event body has no type", ["OR102"]),
    "missing-sequence": ("One event has no sequence_number", ["OR103"]),
    "sequence-regress": ("sequence_number goes backwards", ["OR103"]),
    "sse-id": ("Server sets the SSE id field on every event", ["OR104"]),
    "missing-terminal": ("Stream ends without response.completed", ["OR105"]),
    "truncated-stream": ("Stream is cut off in the middle of an event", ["OR105"]),
    "double-terminal": ("response.completed is sent twice", ["OR106"]),
    "events-after-terminal": ("A delta arrives after response.completed", ["OR107"]),
    "done-before-terminal": ("[DONE] is sent before response.completed", ["OR108"]),
    "error-without-failed": ("An error event is followed by response.completed", ["OR109"]),
    "missing-field": ("content_part.added has no item_id", ["OR110"]),
    "unprefixed-extension": ("Extension event type has no implementor prefix", ["OR111"]),
    "delta-before-item": ("Text deltas arrive before output_item.added", ["OR201"]),
    "delta-before-part": ("Text deltas arrive before content_part.added", ["OR202"]),
    "update-after-done": ("A delta arrives after output_item.done", ["OR203"]),
    "unclosed-part": ("content_part.done is never sent", ["OR204"]),
    "text-mismatch": ("output_text.done text differs from the deltas", ["OR205"]),
    "index-mismatch": ("Deltas carry the wrong item_id", ["OR206"]),
    "added-item-incomplete": ("output_item.added item has no status or role", ["OR207"]),
    "incomplete-not-last": ("Item ends incomplete but the response completes", ["OR208"]),
    "args-mismatch": ("Function argument deltas differ from the done arguments", ["OR209"]),
    "args-not-json": ("Function arguments are not JSON", ["OR210"]),
    "done-item-in-progress": ("output_item.done item is still in_progress", ["OR211"]),
    "bad-response-object": ("Response object has the wrong object and no created_at", ["OR301"]),
    "unknown-response-status": ("Final response status is 'done'", ["OR302"]),
    "terminal-status-mismatch": ("response.completed carries status in_progress", ["OR303"]),
    "final-mismatch": ("Final response text differs from the streamed text", ["OR304"]),
    "item-shape": ("Final message item has no role", ["OR305"]),
    "response-id-changed": ("Response id changes between created and completed", ["OR306"]),
    "unprefixed-item-type": ("Output includes a 'search_result' item with no prefix", ["OR307"]),
    "error-200": ("Invalid requests get HTTP 200", ["OR401"]),
    "error-shape": ("Error body has no top-level error object", ["OR402"]),
    "error-fields": ("Error object omits code and param", ["OR403"]),
    "error-not-json": ("Error body is plain text", ["OR404"]),
    "skip-required-tool": ("tool_choice 'required' is answered with text", ["OR501"]),
    "allowed-tools-violation": ("A tool outside allowed_tools is called", ["OR502"]),
    "tool-choice-none-violation": ("A tool is called despite tool_choice 'none'", ["OR503"]),
}

_ERROR_TYPES = {400: "invalid_request", 404: "not_found", 500: "server_error"}


def _id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:24])


class MockConfig(object):
    def __init__(self, faults=(), reply=DEFAULT_REPLY, extensions=False):
        unknown = [f for f in faults if f not in FAULTS]
        if unknown:
            raise ValueError("unknown fault(s): %s" % ", ".join(unknown))
        self.faults = set(faults)
        self.reply = reply
        self.extensions = extensions


# ----------------------------------------------------------------------
# request -> output plan
# ----------------------------------------------------------------------

def _function_tools(req):
    return [t for t in (req.get("tools") or []) if isinstance(t, dict) and t.get("type") == "function"
            and isinstance(t.get("name"), str)]


def _choose_tool(req, cfg):
    """Return the tool dict to call, or None to answer with text."""
    tools = _function_tools(req)
    choice = req.get("tool_choice", "auto")
    if not tools:
        return None
    if choice == "none":
        return tools[0] if "tool-choice-none-violation" in cfg.faults else None
    if choice == "required" and "skip-required-tool" in cfg.faults:
        return None
    by_name = {t["name"]: t for t in tools}
    if isinstance(choice, dict):
        if choice.get("type") == "function" and choice.get("name") in by_name:
            return by_name[choice["name"]]
        if choice.get("type") == "allowed_tools":
            allowed = [c.get("name") for c in choice.get("tools") or [] if isinstance(c, dict)]
            if "allowed-tools-violation" in cfg.faults:
                outside = [t for t in tools if t["name"] not in allowed]
                if outside:
                    return outside[0]
            permitted = [t for t in tools if t["name"] in allowed]
            return permitted[0] if permitted else None
    return tools[0]


def _arguments_for(tool, cfg):
    if "args-not-json" in cfg.faults:
        return "location=Coimbatore"
    params = tool.get("parameters") or {}
    props = params.get("properties") or {}
    required = params.get("required") or list(props)
    args = {}
    for name in required[:3]:
        spec = props.get(name, {})
        args[name] = {"integer": 1, "number": 1.5, "boolean": True}.get(spec.get("type"), "Coimbatore")
    return json.dumps(args)


def plan_output(req, cfg):
    tool = _choose_tool(req, cfg)
    if tool is not None:
        return [{
            "type": "function_call", "id": _id("fc"), "call_id": _id("call"), "name": tool["name"],
            "arguments": _arguments_for(tool, cfg), "status": "completed",
        }]
    return [{
        "type": "message", "id": _id("msg"), "status": "completed", "role": "assistant",
        "content": [{"type": "output_text", "text": cfg.reply, "annotations": [], "logprobs": []}],
    }]


def _echo_tools(req):
    """Echo request tools in the response with every non-nullable field filled in."""
    out = []
    for t in req.get("tools") or []:
        if isinstance(t, dict) and t.get("type") == "function":
            out.append({"type": "function", "name": t.get("name"), "description": t.get("description"),
                        "parameters": t.get("parameters"), "strict": t.get("strict", True)})
        elif isinstance(t, dict):
            out.append(t)
    return out


def _usage(n_out):
    return {"input_tokens": 8, "output_tokens": n_out, "total_tokens": 8 + n_out,
            "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}


def build_compaction(req):
    blob = json.dumps(req.get("input"), sort_keys=True).encode("utf-8")
    import base64
    cid = _id("cmp")
    return {"id": cid, "object": "response.compaction", "created_at": int(time.time()),
            "output": [{"type": "compaction", "id": cid, "encrypted_content": base64.b64encode(blob).decode("ascii")}],
            "usage": _usage(4)}


def build_response(req, rid, status, output, created_at):
    done = status in ("completed", "failed", "incomplete")
    text_len = sum(len(json.dumps(i)) for i in output)
    return {
        "id": rid, "object": "response", "created_at": created_at,
        "completed_at": int(time.time()) if done else None, "status": status,
        "incomplete_details": None, "model": req.get("model"),
        "previous_response_id": req.get("previous_response_id"), "instructions": req.get("instructions"),
        "output": output, "error": None, "tools": _echo_tools(req),
        "tool_choice": req.get("tool_choice", "auto"), "truncation": req.get("truncation", "disabled"),
        "parallel_tool_calls": req.get("parallel_tool_calls", True), "text": {"format": {"type": "text"}},
        "top_p": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0, "top_logprobs": 0,
        "temperature": 1.0, "reasoning": None, "usage": _usage(max(1, text_len // 8)),
        "max_output_tokens": req.get("max_output_tokens"), "max_tool_calls": None,
        "store": bool(req.get("store", False)), "background": False, "service_tier": "default",
        "metadata": req.get("metadata") or {}, "safety_identifier": None, "prompt_cache_key": None,
    }


def _apply_final_faults(resp, cfg):
    if "bad-response-object" in cfg.faults:
        resp["object"] = "chat.completion"
        resp.pop("created_at", None)
    if "unknown-response-status" in cfg.faults:
        resp["status"] = "done"
    if "item-shape" in cfg.faults:
        for item in resp["output"]:
            if item.get("type") == "message":
                item.pop("role", None)
    if "unprefixed-item-type" in cfg.faults:
        resp["output"].append({"type": "search_result", "id": _id("sr"), "status": "completed"})
    return resp


# ----------------------------------------------------------------------
# streaming
# ----------------------------------------------------------------------

def _chunks(text, n=None):
    if n:
        size = max(1, len(text) // n)
        return [text[i:i + size] for i in range(0, len(text), size)] or [""]
    return re.findall(r"\S+\s*|\s+", text) or [""]


def build_events(req, cfg):
    """Return the stream as a list of [event_name, data, sse_id] (data is a dict or '[DONE]')."""
    rid = _id("resp")
    created_at = int(time.time())
    output = plan_output(req, cfg)
    if "incomplete-not-last" in cfg.faults:
        output[0]["status"] = "incomplete"
    ev = []

    def emit(data):
        ev.append([data["type"], data, None])

    emit({"type": "response.created", "response": build_response(req, rid, "in_progress", [], created_at)})
    emit({"type": "response.in_progress", "response": build_response(req, rid, "in_progress", [], created_at)})
    if cfg.extensions:
        emit({"type": "mock:trace_event", "detail": "planning output"})
    for idx, item in enumerate(output):
        added = {k: v for k, v in item.items()}
        added["status"] = "in_progress"
        if item["type"] == "message":
            added["content"] = []
            emit({"type": "response.output_item.added", "output_index": idx, "item": added})
            part = item["content"][0]
            emit({"type": "response.content_part.added", "item_id": item["id"], "output_index": idx,
                  "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
            for d in _chunks(part["text"]):
                emit({"type": "response.output_text.delta", "item_id": item["id"], "output_index": idx,
                      "content_index": 0, "delta": d, "logprobs": []})
            emit({"type": "response.output_text.done", "item_id": item["id"], "output_index": idx,
                  "content_index": 0, "text": part["text"], "logprobs": []})
            emit({"type": "response.content_part.done", "item_id": item["id"], "output_index": idx,
                  "content_index": 0, "part": copy.deepcopy(part)})
        else:
            added["arguments"] = ""
            emit({"type": "response.output_item.added", "output_index": idx, "item": added})
            for d in _chunks(item["arguments"], 3):
                emit({"type": "response.function_call_arguments.delta", "item_id": item["id"],
                      "output_index": idx, "delta": d})
            emit({"type": "response.function_call_arguments.done", "item_id": item["id"],
                  "output_index": idx, "arguments": item["arguments"]})
        emit({"type": "response.output_item.done", "output_index": idx, "item": copy.deepcopy(item)})
    final_status = "incomplete" if any(i.get("status") == "incomplete" for i in output) and \
        "incomplete-not-last" not in cfg.faults else "completed"
    final = build_response(req, rid, final_status, copy.deepcopy(output), created_at)
    emit({"type": "response.%s" % final_status, "response": _apply_final_faults(final, cfg)})
    ev.append(["__done__", "[DONE]", None])
    _apply_stream_faults(ev, cfg, rid)
    seq = 0
    for e in ev:
        if isinstance(e[1], dict) and "sequence_number" not in e[1] and not e[1].get("_no_seq"):
            e[1]["sequence_number"] = seq
            seq += 1
        if isinstance(e[1], dict):
            e[1].pop("_no_seq", None)
    _apply_sequence_faults(ev, cfg)
    return ev


def _find(ev, t, nth=0):
    hits = [i for i, e in enumerate(ev) if isinstance(e[1], dict) and e[1].get("type") == t]
    return hits[nth] if len(hits) > nth else None


def _first_delta(ev):
    for t in ("response.output_text.delta", "response.function_call_arguments.delta"):
        i = _find(ev, t)
        if i is not None:
            return i
    return None


def _terminal(ev):
    for t in ("response.completed", "response.incomplete", "response.failed"):
        i = _find(ev, t)
        if i is not None:
            return i
    return None


def _apply_stream_faults(ev, cfg, rid):
    f = cfg.faults
    d = _first_delta(ev)
    term = _terminal(ev)
    if "event-type-mismatch" in f and d is not None:
        ev[d][0] = "message"
    if "missing-type" in f and d is not None:
        ev[d][1] = {k: v for k, v in ev[d][1].items() if k != "type"}
    if "invalid-json" in f and d is not None:
        ev[d][1] = '{"type": "response.output_text.delta", "delta": oops}'
    if "missing-field" in f:
        i = _find(ev, "response.content_part.added")
        if i is not None:
            ev[i][1].pop("item_id", None)
    if "unprefixed-extension" in f:
        i = _find(ev, "mock:trace_event")
        if i is None:
            ev.insert(2, ["trace_event", {"type": "trace_event", "detail": "x"}, None])
        else:
            ev[i][0] = "trace_event"
            ev[i][1]["type"] = "trace_event"
    if "added-item-incomplete" in f:
        i = _find(ev, "response.output_item.added")
        ev[i][1]["item"].pop("status", None)
        ev[i][1]["item"].pop("role", None)
        ev[i][1]["item"].pop("name", None)
    if "done-item-in-progress" in f:
        i = _find(ev, "response.output_item.done")
        ev[i][1]["item"]["status"] = "in_progress"
    if "text-mismatch" in f:
        i = _find(ev, "response.output_text.done")
        if i is not None:
            ev[i][1]["text"] = ev[i][1]["text"] + " (edited)"
    if "args-mismatch" in f:
        i = _find(ev, "response.function_call_arguments.done")
        if i is not None:
            ev[i][1]["arguments"] = '{"location": "Chennai"}'
    if "index-mismatch" in f:
        for e in ev:
            if isinstance(e[1], dict) and e[1].get("type") in ("response.output_text.delta",
                                                               "response.function_call_arguments.delta"):
                e[1]["item_id"] = "wrong_item_id"
    if "terminal-status-mismatch" in f and term is not None:
        ev[term][1]["response"]["status"] = "in_progress"
    if "final-mismatch" in f and term is not None:
        for item in ev[term][1]["response"]["output"]:
            if item.get("type") == "message":
                item["content"][0]["text"] = "A different final answer."
            elif item.get("type") == "function_call":
                item["arguments"] = '{"location": "Madurai"}'
    if "response-id-changed" in f and term is not None:
        ev[term][1]["response"]["id"] = _id("resp")
    if "unclosed-part" in f:
        i = _find(ev, "response.content_part.done")
        if i is not None:
            del ev[i]
    if "delta-before-part" in f:
        i = _find(ev, "response.content_part.added")
        if i is not None:
            part = ev.pop(i)
            ev.insert(_find(ev, "response.output_text.delta") + 1, part)
    if "delta-before-item" in f:
        i = _find(ev, "response.output_item.added")
        item = ev.pop(i)
        ev.insert(_first_delta(ev) + 1, item)
    if "update-after-done" in f:
        i = _find(ev, "response.output_item.done")
        extra = copy.deepcopy(ev[_first_delta(ev)])
        ev.insert(i + 1, extra)
    if "error-without-failed" in f:
        ev.insert(_terminal(ev), ["error", {"type": "error", "error": {
            "type": "server_error", "code": None, "message": "simulated failure", "param": None}}, None])
    if "events-after-terminal" in f:
        extra = copy.deepcopy(ev[_first_delta(ev)])
        ev.insert(_terminal(ev) + 1, extra)
    if "double-terminal" in f:
        t = _terminal(ev)
        ev.insert(t + 1, copy.deepcopy(ev[t]))
    if "done-before-terminal" in f:
        done = ev.pop()
        ev.insert(_terminal(ev), done)
    if "missing-terminal" in f or "truncated-stream" in f:
        ev[:] = [e for e in ev if not (isinstance(e[1], dict) and e[1].get("type") in
                                       ("response.completed", "response.incomplete", "response.failed"))
                 and e[0] != "__done__"]
    if "sse-id" in f:
        for n, e in enumerate(ev):
            e[2] = str(n)


def _apply_sequence_faults(ev, cfg):
    if "missing-sequence" in cfg.faults:
        d = _first_delta(ev)
        if d is not None and isinstance(ev[d][1], dict):
            ev[d][1].pop("sequence_number", None)
    if "sequence-regress" in cfg.faults:
        i = _find(ev, "response.output_item.done")
        if i is not None:
            ev[i][1]["sequence_number"] = 0


def render_sse(ev, cfg):
    out = []
    for name, data, sid in ev:
        lines = []
        if name != "__done__":
            lines.append("event: %s" % name)
        if sid is not None:
            lines.append("id: %s" % sid)
        body = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        lines.append("data: %s" % body)
        out.append("\n".join(lines) + "\n\n")
    text = "".join(out)
    if "truncated-stream" in cfg.faults:
        text += 'event: response.output_text.delta\ndata: {"type":"response.output'
    return text


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------

def error_body(status, message, param=None, code=None, cfg=None):
    faults = cfg.faults if cfg else set()
    if "error-shape" in faults:
        return {"message": message}
    err = {"message": message, "type": _ERROR_TYPES.get(status, "invalid_request")}
    if "error-fields" not in faults:
        err["param"] = param
        err["code"] = code
    return {"error": err}


def handle_request(req_body, cfg, compact=False):
    """Pure request handler. Returns (status, content_type, body_text)."""
    try:
        req = json.loads(req_body or b"{}")
    except ValueError:
        return _error(400, "Request body is not valid JSON.", None, "invalid_json", cfg)
    if not isinstance(req, dict):
        return _error(400, "Request body must be a JSON object.", None, None, cfg)
    model = req.get("model")
    if not isinstance(model, str) or not model:
        return _error(400, "model is required and must be a string.", "model", "missing_required_parameter", cfg)
    if NONEXISTENT_MARKER in model:
        return _error(404, "The requested model '%s' does not exist." % model, "model", "model_not_found", cfg)
    inp = req.get("input")
    if "input" in req and not isinstance(inp, (str, list)):
        return _error(400, "input must be a string or an array of items.", "input", "invalid_type", cfg)
    if "server-error" in cfg.faults:
        return 500, "application/json", json.dumps(error_body(500, "Simulated server error.", cfg=cfg))
    if compact:
        return 200, "application/json", json.dumps(build_compaction(req))
    if req.get("stream"):
        ctype = "text/plain" if "wrong-content-type" in cfg.faults else "text/event-stream"
        return 200, ctype, render_sse(build_events(req, cfg), cfg)
    rid = _id("resp")
    output = plan_output(req, cfg)
    resp = _apply_final_faults(build_response(req, rid, "completed", output, int(time.time())), cfg)
    ctype = "text/plain" if "wrong-content-type" in cfg.faults else "application/json"
    return 200, ctype, json.dumps(resp)


def _error(status, message, param, code, cfg):
    if "error-200" in cfg.faults:
        status = 200
    if "error-not-json" in cfg.faults:
        return status, "text/plain", "Error: %s" % message
    return status, "application/json", json.dumps(error_body(status, message, param, code, cfg))


class _Handler(BaseHTTPRequestHandler):
    server_version = "OpenResponsesMock/0.1"
    cfg = None  # set by make_server

    def log_message(self, fmt, *args):  # quiet by default
        if getattr(self.server, "verbose", False):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def _send(self, status, ctype, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/health") or self.path == "/":
            self._send(200, "application/json", json.dumps({"status": "ok", "faults": sorted(self.cfg.faults)}))
        else:
            self._send(404, "application/json", json.dumps(error_body(404, "Not found", cfg=self.cfg)))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if path.endswith("/responses/compact"):
            self._send(*handle_request(body, self.cfg, compact=True))
        elif path.endswith("/responses"):
            self._send(*handle_request(body, self.cfg))
        else:
            self._send(404, "application/json", json.dumps(error_body(404, "Unknown path %s" % path, cfg=self.cfg)))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def make_server(host="127.0.0.1", port=0, cfg=None, verbose=False):
    cfg = cfg or MockConfig()
    handler = type("Handler", (_Handler,), {"cfg": cfg})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    srv.verbose = verbose
    return srv


class running_mock(object):
    """Context manager: start the mock on a free port in a background thread."""

    def __init__(self, faults=(), **kw):
        self.cfg = MockConfig(faults, **kw)
        self.server = None

    def __enter__(self):
        self.server = make_server(cfg=self.cfg)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[:2]
        self.base_url = "http://%s:%d/v1" % (host, port)
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
