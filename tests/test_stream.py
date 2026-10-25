import unittest

from tests.helpers import TOOL_REQ, check, mock_stream, response, rule_ids, sse

MSG_ADDED = {"output_index": 0, "item": {"type": "message", "id": "m1", "status": "in_progress",
                                         "role": "assistant", "content": []}}
PART = {"item_id": "m1", "output_index": 0, "content_index": 0,
        "part": {"type": "output_text", "text": "", "annotations": []}}


def ev(item_fields):
    base = {"item_id": "m1", "output_index": 0, "content_index": 0}
    base.update(item_fields)
    return base


def final_msg(text, status="completed"):
    return {"type": "message", "id": "m1", "status": status, "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def text_stream(deltas=("Hel", "lo"), done_text="Hello", part_text="Hello", item_text="Hello",
                final_text="Hello", terminal="response.completed", status="completed", extra=()):
    events = [
        ("response.created", {"response": response("in_progress")}),
        ("response.output_item.added", MSG_ADDED),
        ("response.content_part.added", PART),
    ]
    events += [("response.output_text.delta", ev({"delta": d})) for d in deltas]
    events += [
        ("response.output_text.done", ev({"text": done_text})),
        ("response.content_part.done", ev({"part": {"type": "output_text", "text": part_text, "annotations": []}})),
        ("response.output_item.done", {"output_index": 0, "item": final_msg(item_text, status)}),
    ]
    events += list(extra)
    events.append((terminal, {"response": response(terminal.split(".")[1], [final_msg(final_text, status)])}))
    return sse(*events)


class CleanStreams(unittest.TestCase):
    def test_handbuilt_clean(self):
        o = check(text_stream())
        self.assertEqual(o.findings, [])
        self.assertEqual(o.terminal, "response.completed")

    def test_mock_text_and_tool_streams_are_clean(self):
        self.assertEqual(check(mock_stream()).findings, [])
        o = check(mock_stream(TOOL_REQ))
        self.assertEqual(o.findings, [])
        self.assertEqual(o.output()[0]["name"], "get_weather")

    def test_prefixed_extension_events_are_tolerated(self):
        # The spec says clients MUST be able to ignore prefixed extension events.
        self.assertEqual(check(mock_stream(extensions=True)).findings, [])

    def test_done_marker_is_optional(self):
        self.assertEqual(check(text_stream().replace("data: [DONE]\n\n", "")).findings, [])


class FramingRules(unittest.TestCase):
    def test_terminal_rules(self):
        body = text_stream()
        self.assertIn("OR105", rule_ids(check(body.split("event: response.completed")[0])))
        no_seq_body = body.replace("data: [DONE]\n\n", "")
        twice = no_seq_body + no_seq_body.split("\n\n")[-2].replace('"sequence_number": 7', '"sequence_number": 99') + "\n\n"
        self.assertIn("OR106", rule_ids(check(twice)))

    def test_done_before_terminal(self):
        body = text_stream()
        moved = "data: [DONE]\n\n" + body.replace("data: [DONE]\n\n", "")
        self.assertIn("OR108", rule_ids(check(moved)))

    def test_error_needs_failed(self):
        err = ("error", {"error": {"type": "server_error", "message": "x", "code": None, "param": None}})
        self.assertIn("OR109", rule_ids(check(text_stream(extra=[err]))))
        ok = sse(("response.created", {"response": response("in_progress")}), err,
                 ("response.failed", {"response": response("failed")}))
        self.assertNotIn("OR109", rule_ids(check(ok)))

    def test_unprefixed_unknown_event(self):
        self.assertIn("OR111", rule_ids(check(text_stream(extra=[("trace_event", {})]))))
        self.assertNotIn("OR111", rule_ids(check(text_stream(extra=[("acme:trace_event", {})]))))


class LifecycleRules(unittest.TestCase):
    def test_text_mismatch_reports_divergence(self):
        o = check(text_stream(done_text="Help"))
        f = [f for f in o.findings if f.rule_id == "OR205"][0]
        self.assertIn("at char 3", f.message)

    def test_part_and_item_and_final_mismatch(self):
        self.assertIn("OR205", rule_ids(check(text_stream(part_text="Hi"))))
        self.assertIn("OR205", rule_ids(check(text_stream(item_text="Hi"))))
        self.assertEqual(rule_ids(check(text_stream(final_text="Hi"))), ["OR304"])

    def test_incomplete_item_needs_incomplete_response(self):
        self.assertIn("OR208", rule_ids(check(text_stream(status="incomplete"))))
        self.assertEqual(rule_ids(check(text_stream(status="incomplete", terminal="response.incomplete"))), [])

    def test_tool_args_rules(self):
        self.assertIn("OR209", rule_ids(check(mock_stream(TOOL_REQ, ["args-mismatch"]))))
        self.assertIn("OR210", rule_ids(check(mock_stream(TOOL_REQ, ["args-not-json"]))))

    def test_failed_response_skips_closure_and_final_checks(self):
        body = sse(("response.created", {"response": response("in_progress")}),
                   ("response.output_item.added", MSG_ADDED),
                   ("response.failed", {"response": response("failed")}))
        self.assertEqual(rule_ids(check(body)), [])

    def test_repeated_findings_collapse(self):
        o = check(mock_stream(faults=["sse-id"]))
        ids = [f.rule_id for f in o.findings]
        self.assertEqual(ids.count("OR104"), 1)
        self.assertIn("more)", o.findings[0].message)


if __name__ == "__main__":
    unittest.main()
