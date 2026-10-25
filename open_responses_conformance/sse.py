"""A small, strict Server-Sent Events parser (WHATWG event-stream format)."""


class SSEEvent(object):
    __slots__ = ("event", "data", "id", "index")

    def __init__(self, event, data, id_, index):
        self.event = event  # value of the event: field, or None
        self.data = data    # joined data lines (str)
        self.id = id_       # value of the id: field, or None
        self.index = index  # 0-based position in the stream

    def __repr__(self):
        return "SSEEvent(%r, %r)" % (self.event, self.data[:40])


class SSEParseResult(object):
    def __init__(self):
        self.events = []
        self.truncated = False  # stream ended in the middle of an event


def parse_sse(text):
    """Parse an event-stream body. Accepts LF, CRLF or CR line endings."""
    result = SSEParseResult()
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # A body that ends without a trailing blank line leaves a pending event.
    ends_cleanly = text.endswith("\n\n") or text.endswith("\r\n\r\n") or not text.strip()
    event, data, id_ = None, [], None
    seen_field = False
    for line in lines:
        if line == "":
            if seen_field and data:
                result.events.append(SSEEvent(event, "\n".join(data), id_, len(result.events)))
            event, data, id_, seen_field = None, [], None, False
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""
        seen_field = True
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            id_ = value
        # retry and unknown fields are ignored per the SSE spec
    if seen_field and data:
        joined = "\n".join(data)
        if joined.strip() == "[DONE]" or ends_cleanly:
            # Many servers end with "data: [DONE]\n" and no final blank line.
            result.events.append(SSEEvent(event, joined, id_, len(result.events)))
        else:
            result.truncated = True
    return result
