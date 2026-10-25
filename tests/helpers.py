import json

from open_responses_conformance.mock import MockConfig, build_events, render_sse
from open_responses_conformance.sse import parse_sse
from open_responses_conformance.stream import validate_stream

TEXT_REQ = {"model": "m", "input": "hi", "stream": True}
TOOL_REQ = {"model": "m", "input": "weather?", "stream": True, "tool_choice": "required",
            "tools": [{"type": "function", "name": "get_weather",
                       "parameters": {"type": "object", "properties": {"location": {"type": "string"}},
                                      "required": ["location"]}}]}


def mock_stream(req=TEXT_REQ, faults=(), extensions=False):
    cfg = MockConfig(faults, extensions=extensions)
    return render_sse(build_events(dict(req), cfg), cfg)


def check(text):
    return validate_stream(parse_sse(text))


def rule_ids(outcome):
    return sorted({f.rule_id for f in outcome.findings})


def sse(*events, done=True):
    """Build an SSE body from (type, fields) tuples, numbering sequence_number automatically."""
    out = []
    for i, (t, fields) in enumerate(events):
        data = dict(fields)
        data.setdefault("type", t)
        data.setdefault("sequence_number", i)
        out.append("event: %s\ndata: %s\n\n" % (t, json.dumps(data)))
    if done:
        out.append("data: [DONE]\n\n")
    return "".join(out)


def response(status="completed", output=None, rid="resp_1"):
    return {"id": rid, "object": "response", "created_at": 1, "status": status, "model": "m",
            "output": output or []}
