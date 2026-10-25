"""Semantic validation of an Open Responses event stream.

Schema validation answers "is each event well formed?". This module answers
"do the events, taken together, obey the protocol?": framing, sequence
numbers, exactly one terminal event, the item and content-part lifecycle,
that deltas add up to the done values, and that the final response matches
what was streamed.
"""

import json

from .objects import TERMINAL_STATUS, check_item, check_response, is_prefixed
from .rules import Finding

_ITEM_EV = ("type", "sequence_number", "item_id", "output_index")
EVENT_REQUIRED = {
    "response.created": ("type", "sequence_number", "response"),
    "response.queued": ("type", "sequence_number", "response"),
    "response.in_progress": ("type", "sequence_number", "response"),
    "response.completed": ("type", "sequence_number", "response"),
    "response.failed": ("type", "sequence_number", "response"),
    "response.incomplete": ("type", "sequence_number", "response"),
    "response.output_item.added": ("type", "sequence_number", "output_index", "item"),
    "response.output_item.done": ("type", "sequence_number", "output_index", "item"),
    "response.content_part.added": _ITEM_EV + ("content_index", "part"),
    "response.content_part.done": _ITEM_EV + ("content_index", "part"),
    "response.output_text.delta": _ITEM_EV + ("content_index", "delta"),
    "response.output_text.done": _ITEM_EV + ("content_index", "text"),
    "response.output_text.annotation.added": _ITEM_EV + ("content_index", "annotation_index", "annotation"),
    "response.refusal.delta": _ITEM_EV + ("content_index", "delta"),
    "response.refusal.done": _ITEM_EV + ("content_index", "refusal"),
    "response.reasoning.delta": _ITEM_EV + ("content_index", "delta"),
    "response.reasoning.done": _ITEM_EV + ("content_index", "text"),
    "response.reasoning_summary_part.added": _ITEM_EV + ("summary_index", "part"),
    "response.reasoning_summary_part.done": _ITEM_EV + ("summary_index", "part"),
    "response.reasoning_summary_text.delta": _ITEM_EV + ("summary_index", "delta"),
    "response.reasoning_summary_text.done": _ITEM_EV + ("summary_index", "text"),
    "response.function_call_arguments.delta": _ITEM_EV + ("delta",),
    "response.function_call_arguments.done": _ITEM_EV + ("arguments",),
    "error": ("type", "sequence_number", "error"),
}
# delta event -> (index field, done event, done field)
TEXT_STREAMS = {
    "response.output_text.delta": ("content_index", "response.output_text.done", "text"),
    "response.refusal.delta": ("content_index", "response.refusal.done", "refusal"),
    "response.reasoning.delta": ("content_index", "response.reasoning.done", "text"),
    "response.reasoning_summary_text.delta": ("summary_index", "response.reasoning_summary_text.done", "text"),
}
TEXT_DONE = {v[1]: (v[0], v[2]) for v in TEXT_STREAMS.values()}
PART_EVENTS = {
    "response.content_part.added": ("content_index", True),
    "response.content_part.done": ("content_index", False),
    "response.reasoning_summary_part.added": ("summary_index", True),
    "response.reasoning_summary_part.done": ("summary_index", False),
}


class _Part(object):
    def __init__(self, ptype):
        self.type = ptype
        self.deltas = []
        self.text_done = None
        self.done = False

    def streamed(self):
        return self.text_done if self.text_done is not None else "".join(self.deltas)


class _Item(object):
    def __init__(self, index, item, order):
        self.index = index
        self.order = order
        self.id = item.get("id")
        self.type = item.get("type")
        self.name = item.get("name")
        self.parts = {}
        self.args_deltas = []
        self.args_done = None
        self.done = False
        self.final = None

    def args(self):
        return self.args_done if self.args_done is not None else "".join(self.args_deltas)


class StreamOutcome(object):
    def __init__(self):
        self.findings = []
        self.final_response = None
        self.terminal = None
        self.event_count = 0
        self.items = []  # final item dicts in output order

    def output(self):
        if self.final_response and isinstance(self.final_response.get("output"), list):
            return self.final_response["output"]
        return self.items


def _where(n, ev_type):
    return "event #%d (%s)" % (n, ev_type)


def validate_stream(parsed, where="stream"):
    """Validate an SSEParseResult. Returns a StreamOutcome."""
    v = _Validator(where)
    for ev in parsed.events:
        v.feed(ev)
    v.finish(parsed.truncated)
    return v.outcome


class _Validator(object):
    def __init__(self, where):
        self.where = where
        self.outcome = StreamOutcome()
        self.raw = []
        self.last_seq = None
        self.terminals = 0
        self.after_terminal_reported = False
        self.pending_error = False
        self.response_id = None
        self.items = {}
        self.ids = {}
        self.order = 0

    # ------------------------------------------------------------------
    def add(self, rule, msg, n=None, ev_type=None):
        w = self.where if n is None else "%s %s" % (self.where, _where(n, ev_type))
        self.raw.append(Finding(rule, msg, w))

    def feed(self, ev):
        n = ev.index + 1
        self.outcome.event_count += 1
        if ev.data.strip() == "[DONE]":
            if self.outcome.terminal is None:
                self.add("OR108", "[DONE] arrived before any terminal event", n, "[DONE]")
            return
        try:
            data = json.loads(ev.data)
        except ValueError:
            self.add("OR004", "data is not JSON: %s" % ev.data[:60], n, ev.event or "?")
            return
        t = data.get("type") if isinstance(data, dict) else None
        if not isinstance(t, str) or not t:
            self.add("OR102", "event body has no type", n, ev.event or "?")
            return
        if ev.event is not None and ev.event != t:
            self.add("OR101", "event: %r but type %r" % (ev.event, t), n, t)
        if ev.id is not None:
            self.add("OR104", "SSE id field is set (%r)" % ev.id, n, t)
        if self.outcome.terminal is not None and not self.after_terminal_reported:
            self.add("OR107", "%s arrived after %s" % (t, self.outcome.terminal), n, t)
            self.after_terminal_reported = True
        seq = data.get("sequence_number")
        if not isinstance(seq, int) or isinstance(seq, bool):
            self.add("OR103", "sequence_number is missing", n, t)
        else:
            if self.last_seq is not None and seq <= self.last_seq:
                self.add("OR103", "sequence_number %d does not increase after %d" % (seq, self.last_seq), n, t)
            self.last_seq = seq

        if t in EVENT_REQUIRED:
            missing = [k for k in EVENT_REQUIRED[t] if k not in data]
            if missing:
                self.add("OR110", "%s lacks %s" % (t, ", ".join(missing)), n, t)
        elif is_prefixed(t):
            return  # extension event: clients must be able to ignore it
        else:
            self.add("OR111", "unknown event type %r has no implementor prefix" % t, n, t)
            return

        if t == "error":
            self.pending_error = True
            return
        if t.startswith("response.") and "response" in EVENT_REQUIRED.get(t, ()) and t != "response.output_item.added":
            self._response_event(t, data, n)
            return
        self._item_event(t, data, n)

    # ------------------------------------------------------------------
    def _response_event(self, t, data, n):
        resp = data.get("response")
        if isinstance(resp, dict):
            rid = resp.get("id")
            if rid is not None:
                if self.response_id is None:
                    self.response_id = rid
                elif rid != self.response_id:
                    self.add("OR306", "response id changed from %r to %r" % (self.response_id, rid), n, t)
        if t in TERMINAL_STATUS:
            self.terminals += 1
            if self.terminals > 1:
                self.add("OR106", "second terminal event %s after %s" % (t, self.outcome.terminal), n, t)
                return
            self.outcome.terminal = t
            if t == "response.failed":
                self.pending_error = False
            if isinstance(resp, dict):
                self.outcome.final_response = resp
                for f in check_response(resp, "%s %s" % (self.where, _where(n, t)),
                                        expect_status=TERMINAL_STATUS[t]):
                    self.raw.append(f)

    def _item_event(self, t, data, n):
        idx = data.get("output_index")
        if t == "response.output_item.added":
            item = data.get("item") if isinstance(data.get("item"), dict) else {}
            if idx in self.items:
                self.add("OR206", "output_index %r was already used" % idx, n, t)
            missing = [k for k in ("id", "type", "status") if k not in item]
            if item.get("type") == "message" and "role" not in item:
                missing.append("role")
            if item.get("type") == "function_call" and "name" not in item:
                missing.append("name")
            if missing:
                self.add("OR207", "added item lacks %s" % ", ".join(missing), n, t)
            for other in self.items.values():
                if other.done and isinstance(other.final, dict) and other.final.get("status") == "incomplete":
                    self.add("OR208", "item added after incomplete item at output_index %r" % other.index, n, t)
                    break
            st = _Item(idx, item, self.order)
            self.order += 1
            self.items[idx] = st
            if st.id is not None:
                if st.id in self.ids and self.ids[st.id] != idx:
                    self.add("OR206", "item id %r reused at output_index %r" % (st.id, idx), n, t)
                self.ids[st.id] = idx
            return

        st = self.items.get(idx)
        if st is None:
            self.add("OR201", "%s for output_index %r, which was never added" % (t, idx), n, t)
            return
        item_id = data.get("item_id")
        if item_id is not None and st.id is not None and item_id != st.id:
            self.add("OR206", "item_id %r does not match %r at output_index %r" % (item_id, st.id, idx), n, t)
        if st.done:
            self.add("OR203", "%s after output_item.done for output_index %r" % (t, idx), n, t)
            return

        if t == "response.output_item.done":
            self._item_done(st, data, n, t)
        elif t in PART_EVENTS:
            field, added = PART_EVENTS[t]
            key = (field, data.get(field))
            part = data.get("part") if isinstance(data.get("part"), dict) else {}
            if added:
                if key in st.parts:
                    self.add("OR206", "%s %r was already added" % (field, key[1]), n, t)
                st.parts[key] = _Part(part.get("type"))
            else:
                p = st.parts.get(key)
                if p is None:
                    self.add("OR202", "part done for %s %r that was never added" % (field, key[1]), n, t)
                    return
                if p.done:
                    self.add("OR203", "part %r closed twice" % (key[1],), n, t)
                    return
                if p.deltas and p.text_done is None:
                    self.add("OR204", "part %r closed without its *.done text event" % (key[1],), n, t)
                text = part.get("text", part.get("refusal"))
                if isinstance(text, str) and (p.deltas or p.text_done is not None) and text != p.streamed():
                    self.add("OR205", "content_part.done text differs from streamed text", n, t)
                p.done = True
        elif t in TEXT_STREAMS:
            field = TEXT_STREAMS[t][0]
            p = st.parts.get((field, data.get(field)))
            if p is None:
                self.add("OR202", "%s before its content part was added" % t, n, t)
                return
            if p.done or p.text_done is not None:
                self.add("OR203", "%s after the part's text was done" % t, n, t)
                return
            if isinstance(data.get("delta"), str):
                p.deltas.append(data["delta"])
        elif t in TEXT_DONE:
            field, value_field = TEXT_DONE[t]
            p = st.parts.get((field, data.get(field)))
            if p is None:
                self.add("OR202", "%s before its content part was added" % t, n, t)
                return
            if p.text_done is not None or p.done:
                self.add("OR203", "%s repeated" % t, n, t)
                return
            text = data.get(value_field)
            if isinstance(text, str):
                if (p.deltas or text) and "".join(p.deltas) != text:
                    self.add("OR205", "joined deltas and done text differ %s"
                             % diverge("".join(p.deltas), text), n, t)
                p.text_done = text
        elif t == "response.function_call_arguments.delta":
            if st.args_done is not None:
                self.add("OR203", "argument delta after arguments were done", n, t)
                return
            if isinstance(data.get("delta"), str):
                st.args_deltas.append(data["delta"])
        elif t == "response.function_call_arguments.done":
            if st.args_done is not None:
                self.add("OR203", "function_call_arguments.done repeated", n, t)
                return
            args = data.get("arguments")
            if isinstance(args, str):
                if st.args_deltas and "".join(st.args_deltas) != args:
                    self.add("OR209", "joined argument deltas and done arguments differ %s"
                             % diverge("".join(st.args_deltas), args), n, t)
                st.args_done = args
        elif t == "response.output_text.annotation.added":
            if (("content_index", data.get("content_index"))) not in st.parts:
                self.add("OR202", "annotation for a content part that was never added", n, t)

    def _item_done(self, st, data, n, t):
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        st.done = True
        st.final = item
        if item.get("status") == "in_progress":
            self.add("OR211", "item %r is done but its status is in_progress" % st.id, n, t)
        if st.id is not None and item.get("id") not in (None, st.id):
            self.add("OR206", "done item id %r differs from added id %r" % (item.get("id"), st.id), n, t)
        for key, p in sorted(st.parts.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            if not p.done:
                self.add("OR204", "%s %r was never closed with its part done event" % key, n, t)
        if item.get("type") == "message" and isinstance(item.get("content"), list):
            for ci, part in enumerate(item["content"]):
                p = st.parts.get(("content_index", ci))
                text = part.get("text", part.get("refusal")) if isinstance(part, dict) else None
                if p is not None and isinstance(text, str) and (p.deltas or p.text_done is not None) \
                        and text != p.streamed():
                    self.add("OR205", "done item content[%d] differs from streamed text" % ci, n, t)
        if item.get("type") == "function_call" and isinstance(item.get("arguments"), str):
            if (st.args_deltas or st.args_done is not None) and item["arguments"] != st.args():
                self.add("OR209", "done item arguments differ from streamed arguments", n, t)
        for f in check_item(item, "%s %s" % (self.where, _where(n, t)), final=True):
            self.raw.append(f)

    # ------------------------------------------------------------------
    def finish(self, truncated):
        o = self.outcome
        if o.terminal is None:
            why = "stream ended mid-event" if truncated else "stream ended"
            self.raw.append(Finding("OR105", "%s without a terminal response event" % why, self.where))
        if self.pending_error:
            self.raw.append(Finding("OR109", "error event was not followed by response.failed", self.where))
        ordered = sorted(self.items.values(), key=lambda s: s.order)
        if o.terminal in ("response.completed", "response.incomplete"):
            for st in ordered:
                if not st.done:
                    self.raw.append(Finding("OR204", "output item %r (output_index %r) was never closed"
                                            % (st.id, st.index), self.where))
        # An incomplete item followed by another item is reported when the later item is added.
        incomplete = [s for s in ordered if isinstance(s.final, dict) and s.final.get("status") == "incomplete"]
        if incomplete:
            if o.terminal and o.terminal != "response.incomplete":
                self.raw.append(Finding("OR208", "an item ended incomplete but the stream ended with %s"
                                        % o.terminal, self.where))
        o.items = [s.final for s in ordered if isinstance(s.final, dict)]
        if o.final_response is not None and o.terminal != "response.failed":
            self._compare_final(ordered)
        o.findings = collapse(self.raw)

    def _compare_final(self, ordered):
        output = self.outcome.final_response.get("output")
        if not isinstance(output, list):
            return
        streamed = [s for s in ordered if s.done]
        if len(output) != len(streamed):
            self.raw.append(Finding("OR304", "final output has %d items but %d were streamed"
                                    % (len(output), len(streamed)), self.where))
            return
        for i, (final, st) in enumerate(zip(output, streamed)):
            if not isinstance(final, dict):
                continue
            if final.get("id") != st.id or final.get("type") != st.type:
                self.raw.append(Finding("OR304", "final output[%d] is %s %r but streamed item was %s %r"
                                        % (i, final.get("type"), final.get("id"), st.type, st.id), self.where))
                return
            if st.type == "message" and isinstance(final.get("content"), list):
                for ci, part in enumerate(final["content"]):
                    p = st.parts.get(("content_index", ci))
                    text = part.get("text") if isinstance(part, dict) else None
                    if p is not None and isinstance(text, str) and text != p.streamed():
                        self.raw.append(Finding("OR304", "final output[%d] text differs from the streamed text"
                                                % i, self.where))
                        return
            if st.type == "function_call" and isinstance(final.get("arguments"), str) \
                    and (st.args_deltas or st.args_done is not None) and final["arguments"] != st.args():
                self.raw.append(Finding("OR304", "final output[%d] arguments differ from the streamed arguments"
                                        % i, self.where))
                return


def collapse(findings):
    """Keep the first finding per (rule, where-prefix) and count repeats."""
    seen, out = {}, []
    for f in findings:
        key = (f.rule_id, f.message.split(" ")[0])
        if f.rule_id in ("OR101", "OR103", "OR104", "OR110", "OR111", "OR205", "OR305"):
            key = (f.rule_id,)
        if key in seen:
            seen[key][1] += 1
            continue
        seen[key] = [f, 0]
        out.append(f)
    for f in out:
        key = (f.rule_id,) if f.rule_id in ("OR101", "OR103", "OR104", "OR110", "OR111", "OR205", "OR305") \
            else (f.rule_id, f.message.split(" ")[0])
        extra = seen[key][1]
        if extra:
            f.message = "%s (and %d more)" % (f.message, extra)
    return out


def _clip(s, n=40):
    return s if len(s) <= n else s[:n] + "..."


def diverge(a, b, ctx=18):
    """Describe where two strings first differ: "at char 42: '...mock server.' vs '...mock server. (edited)'"."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    start = max(0, i - ctx)
    lead = "..." if start else ""
    return "at char %d: %r vs %r" % (i, lead + a[start:i + ctx], lead + b[start:i + ctx])
