"""Rule registry, findings and scoring.

Each rule cites the section of the Open Responses specification it enforces
and whether the spec wording is MUST, SHOULD or a schema requirement.
"""

from collections import namedtuple

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
PENALTY = {"critical": 40, "high": 20, "medium": 8, "low": 3}
CRITICAL_CAP = 69

Rule = namedtuple("Rule", "id slug severity category level section summary")

_RULES = [
    # ---- transport ----
    Rule("OR001", "request-failed", "critical", "transport", "MUST", "HTTP Responses",
         "A valid request did not succeed (connection error or non-2xx status)"),
    Rule("OR002", "content-type-json", "high", "transport", "MUST", "HTTP Responses",
         "Non-streaming response is not served as application/json"),
    Rule("OR003", "content-type-sse", "high", "transport", "MUST", "HTTP Responses",
         "Streaming response is not served as text/event-stream"),
    Rule("OR004", "invalid-json", "critical", "transport", "MUST", "HTTP Responses",
         "Body or SSE data line is not valid JSON"),
    # ---- stream framing ----
    Rule("OR101", "event-type-mismatch", "high", "stream", "MUST", "HTTP Responses",
         "SSE event: field does not match the type in the event body"),
    Rule("OR102", "missing-type", "high", "stream", "MUST", "Streaming",
         "Streaming event has no type"),
    Rule("OR103", "sequence-number", "high", "stream", "schema", "Streaming",
         "sequence_number is missing or does not increase"),
    Rule("OR104", "sse-id-used", "low", "stream", "SHOULD NOT", "HTTP Responses",
         "Server sets the SSE id field"),
    Rule("OR105", "no-terminal-event", "critical", "stream", "MUST", "HTTP Responses",
         "Stream ended without response.completed, response.failed or response.incomplete"),
    Rule("OR106", "multiple-terminal-events", "high", "stream", "MUST", "HTTP Responses",
         "Stream carries more than one terminal response event"),
    Rule("OR107", "events-after-terminal", "high", "stream", "MUST", "HTTP Responses",
         "Events arrive after the terminal response event"),
    Rule("OR108", "done-before-terminal", "high", "stream", "MAY / MUST", "HTTP Responses",
         "[DONE] arrives before the terminal response event"),
    Rule("OR109", "error-without-failed", "high", "stream", "MUST", "Errors",
         "A streamed error event is not followed by response.failed"),
    Rule("OR110", "missing-event-field", "high", "stream", "schema", "Streaming",
         "Streaming event lacks a field the schema requires"),
    Rule("OR111", "unprefixed-extension", "medium", "stream", "MUST", "Extending streaming events",
         "Non-standard event type is not prefixed with an implementor slug"),
    # ---- item lifecycle ----
    Rule("OR201", "item-not-added", "high", "lifecycle", "MUST", "Items are streamable",
         "Event refers to an output item before response.output_item.added"),
    Rule("OR202", "part-not-added", "high", "lifecycle", "MUST", "Items are streamable",
         "Content delta or done arrives before response.content_part.added"),
    Rule("OR203", "update-after-done", "high", "lifecycle", "MUST NOT", "Items are state machines",
         "Item or content part receives updates after it is done"),
    Rule("OR204", "item-not-closed", "high", "lifecycle", "MUST", "Items are streamable",
         "Item or content part is never closed with its done event"),
    Rule("OR205", "text-mismatch", "high", "lifecycle", "MUST", "Items are streamable",
         "Concatenated deltas differ from the done text or the done part"),
    Rule("OR206", "index-mismatch", "medium", "lifecycle", "schema", "Items are streamable",
         "output_index or item_id is inconsistent across an item's events"),
    Rule("OR207", "added-item-incomplete", "medium", "lifecycle", "MUST", "Items are streamable",
         "response.output_item.added item lacks id, type, status or its key fields"),
    Rule("OR208", "incomplete-not-last", "high", "lifecycle", "MUST", "Items are state machines",
         "An incomplete item is not last, or the response is not incomplete"),
    Rule("OR209", "arguments-mismatch", "high", "lifecycle", "MUST", "Items are streamable",
         "Function call argument deltas differ from the done arguments or the item"),
    Rule("OR210", "arguments-not-json", "medium", "lifecycle", "schema", "Tools",
         "Function call arguments are not a JSON document"),
    Rule("OR211", "done-item-status", "medium", "lifecycle", "MUST", "Items are state machines",
         "response.output_item.done item is still in_progress"),
    # ---- response object ----
    Rule("OR301", "response-shape", "high", "response", "schema", "HTTP Responses",
         "Response object lacks core fields or object is not 'response'"),
    Rule("OR302", "response-status", "medium", "response", "schema", "State machines",
         "Response status is not a known value"),
    Rule("OR303", "terminal-status-mismatch", "high", "response", "MUST", "State machines",
         "Terminal event type does not match the response status"),
    Rule("OR304", "final-output-mismatch", "high", "response", "MUST", "Items are streamable",
         "Final response output differs from the items that were streamed"),
    Rule("OR305", "item-shape", "medium", "response", "MUST", "Extending Items",
         "Output item lacks required fields for its type"),
    Rule("OR306", "response-id-changed", "medium", "response", "schema", "Streaming",
         "Response id changes between lifecycle events"),
    Rule("OR307", "unprefixed-item-type", "medium", "response", "MUST", "Items are extensible",
         "Non-standard item type is not prefixed with an implementor slug"),
    # ---- errors ----
    Rule("OR401", "error-status-code", "medium", "errors", "schema", "Errors",
         "Invalid request did not get a 4xx status"),
    Rule("OR402", "error-shape", "high", "errors", "schema", "Errors",
         "Error body lacks error.message or error.type"),
    Rule("OR403", "error-fields", "low", "errors", "schema", "Errors",
         "Error object omits code or param (they may be null but should be present)"),
    Rule("OR404", "error-not-json", "medium", "errors", "MUST", "HTTP Responses",
         "Error body is not application/json"),
    # ---- tools ----
    Rule("OR501", "required-tool-not-called", "high", "tools", "MUST", "tool_choice",
         "tool_choice 'required' or a named function, but no function_call was produced"),
    Rule("OR502", "allowed-tools-violated", "high", "tools", "MUST", "allowed_tools",
         "A tool outside allowed_tools was called"),
    Rule("OR503", "tool-choice-none-violated", "high", "tools", "MUST NOT", "tool_choice",
         "tool_choice 'none' but a function_call was produced"),
]

RULES = {r.id: r for r in _RULES}
SLUGS = {r.slug: r.id for r in _RULES}


class Finding(object):
    __slots__ = ("rule_id", "message", "where")

    def __init__(self, rule_id, message, where=None):
        if rule_id not in RULES:
            raise KeyError(rule_id)
        self.rule_id = rule_id
        self.message = message
        self.where = where

    @property
    def rule(self):
        return RULES[self.rule_id]

    @property
    def severity(self):
        return RULES[self.rule_id].severity

    def to_dict(self):
        r = self.rule
        return {"rule": r.id, "slug": r.slug, "severity": r.severity, "category": r.category,
                "level": r.level, "section": r.section, "message": self.message, "where": self.where}

    def __repr__(self):
        return "Finding(%s, %r, %r)" % (self.rule_id, self.message, self.where)


def resolve_rule(token):
    t = token.strip()
    if t.upper() in RULES:
        return t.upper()
    if t.lower() in SLUGS:
        return SLUGS[t.lower()]
    raise KeyError(token)


def score(findings):
    total = sum(PENALTY[f.severity] for f in findings)
    s = max(0, 100 - total)
    if any(f.severity == "critical" for f in findings):
        s = min(s, CRITICAL_CAP)
    return s


def grade_for(s):
    return "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 70 else "D" if s >= 60 else "F"


GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
