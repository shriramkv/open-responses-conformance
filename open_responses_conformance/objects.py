"""Shape checks for response objects, output items and error payloads.

These cover the fields a client relies on. For exhaustive JSON Schema
validation, run the official compliance suite alongside this tool.
"""

import json
import re

from .rules import Finding

RESPONSE_CORE = ("id", "object", "created_at", "status", "model", "output")
RESPONSE_STATUSES = ("queued", "in_progress", "completed", "failed", "incomplete", "cancelled")
TERMINAL_STATUS = {
    "response.completed": "completed",
    "response.failed": "failed",
    "response.incomplete": "incomplete",
}
ITEM_STATUSES = ("in_progress", "completed", "incomplete", "failed")
STANDARD_ITEM_TYPES = (
    "message", "function_call", "function_call_output", "reasoning", "compaction_summary",
    "item_reference",
)
ITEM_REQUIRED = {
    "message": ("id", "type", "status", "role", "content"),
    "function_call": ("id", "type", "call_id", "name", "arguments", "status"),
    "reasoning": ("id", "type"),
}
PART_REQUIRED = {
    "output_text": ("type", "text", "annotations"),
    "refusal": ("type", "refusal"),
}
PREFIXED_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*:[A-Za-z0-9_.:-]+$")

# The spec's error-type table says invalid_request; its own example says
# invalid_request_error. Both are accepted.
ERROR_TYPE_STATUS = {
    "server_error": 500, "invalid_request": 400, "invalid_request_error": 400,
    "not_found": 404, "model_error": 500, "too_many_requests": 429,
}


def is_prefixed(type_name):
    return bool(PREFIXED_RE.match(type_name or ""))


def check_response(resp, where, expect_status=None):
    out = []
    if not isinstance(resp, dict):
        return [Finding("OR301", "response is %s, not an object" % type(resp).__name__, where)]
    missing = [k for k in RESPONSE_CORE if k not in resp]
    if missing:
        out.append(Finding("OR301", "response lacks %s" % ", ".join(missing), where))
    if "object" in resp and resp.get("object") != "response":
        out.append(Finding("OR301", "object is %r, expected 'response'" % resp.get("object"), where))
    status = resp.get("status")
    if status is not None and status not in RESPONSE_STATUSES:
        out.append(Finding("OR302", "status %r is not a known response status" % status, where))
    if expect_status and status is not None and status != expect_status:
        out.append(Finding("OR303", "terminal event implies status %r but response has %r"
                           % (expect_status, status), where))
    output = resp.get("output")
    if output is not None and not isinstance(output, list):
        out.append(Finding("OR301", "output is not an array", where))
    elif isinstance(output, list):
        for i, item in enumerate(output):
            out.extend(check_item(item, "%s output[%d]" % (where, i), final=True))
    return out


def check_item(item, where, final=False):
    out = []
    if not isinstance(item, dict):
        return [Finding("OR305", "item is not an object", where)]
    t = item.get("type")
    if not isinstance(t, str) or not t:
        return [Finding("OR305", "item has no type", where)]
    if t not in STANDARD_ITEM_TYPES and not is_prefixed(t):
        out.append(Finding("OR307", "item type %r has no implementor prefix (e.g. acme:%s)" % (t, t), where))
    required = ITEM_REQUIRED.get(t, ("id", "type") if t in STANDARD_ITEM_TYPES else ("id", "type", "status"))
    missing = [k for k in required if k not in item]
    if missing:
        out.append(Finding("OR305", "%s item lacks %s" % (t, ", ".join(missing)), where))
    status = item.get("status")
    if status is not None and status not in ITEM_STATUSES:
        out.append(Finding("OR305", "%s item status %r is not a known item status" % (t, status), where))
    if t == "message" and isinstance(item.get("content"), list):
        for j, part in enumerate(item["content"]):
            out.extend(check_part(part, "%s content[%d]" % (where, j)))
    if t == "function_call" and final and isinstance(item.get("arguments"), str):
        if not is_json_document(item["arguments"]):
            out.append(Finding("OR210", "arguments of %r are not JSON: %s"
                               % (item.get("name"), _clip(item["arguments"])), where))
    return out


def check_part(part, where):
    if not isinstance(part, dict):
        return [Finding("OR305", "content part is not an object", where)]
    req = PART_REQUIRED.get(part.get("type"))
    if not req:
        return []
    missing = [k for k in req if k not in part]
    if missing:
        return [Finding("OR305", "%s part lacks %s" % (part.get("type"), ", ".join(missing)), where)]
    return []


def check_error_body(status_code, content_type, body_text, where):
    out = []
    if status_code < 400 or status_code >= 500:
        out.append(Finding("OR401", "invalid request returned HTTP %d, expected 4xx" % status_code, where))
    if status_code < 400:
        return out
    if "application/json" not in (content_type or "").lower():
        out.append(Finding("OR404", "error Content-Type is %r" % (content_type or "missing"), where))
    try:
        body = json.loads(body_text)
    except ValueError:
        out.append(Finding("OR402", "error body is not JSON: %s" % _clip(body_text), where))
        return out
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        out.append(Finding("OR402", "body has no top-level error object", where))
        return out
    lacking = [k for k in ("message", "type") if not isinstance(err.get(k), str) or not err.get(k)]
    if lacking:
        out.append(Finding("OR402", "error object lacks %s" % ", ".join(lacking), where))
    absent = [k for k in ("code", "param") if k not in err]
    if absent:
        out.append(Finding("OR403", "error object omits %s" % ", ".join(absent), where))
    return out


def function_calls(output):
    return [i for i in (output or []) if isinstance(i, dict) and i.get("type") == "function_call"]


def is_json_document(text):
    try:
        json.loads(text)
        return True
    except ValueError:
        return False


def _clip(text, n=60):
    text = str(text).replace("\n", " ")
    return text if len(text) <= n else text[:n] + "..."
