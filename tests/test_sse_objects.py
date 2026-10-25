import json
import unittest

from open_responses_conformance.objects import check_error_body, check_item, check_response
from open_responses_conformance.sse import parse_sse
from tests.helpers import response


class SSEParserTests(unittest.TestCase):
    def test_basic_and_multiline(self):
        r = parse_sse("event: a\ndata: {\"x\":\ndata: 1}\n\n: keep-alive\n\ndata: [DONE]\n\n")
        self.assertEqual([(e.event, e.data) for e in r.events], [("a", '{"x":\n1}'), (None, "[DONE]")])
        self.assertFalse(r.truncated)

    def test_crlf_and_id(self):
        r = parse_sse("event: a\r\nid: 7\r\ndata: {}\r\n\r\n")
        self.assertEqual((r.events[0].event, r.events[0].id), ("a", "7"))

    def test_truncated(self):
        r = parse_sse('event: a\ndata: {}\n\nevent: b\ndata: {"type":"resp')
        self.assertEqual(len(r.events), 1)
        self.assertTrue(r.truncated)

    def test_done_without_trailing_blank_line(self):
        r = parse_sse("data: {}\n\ndata: [DONE]\n")
        self.assertEqual(r.events[-1].data, "[DONE]")
        self.assertFalse(r.truncated)

    def test_space_after_colon_is_optional(self):
        self.assertEqual(parse_sse("data:{}\n\n").events[0].data, "{}")


class ObjectTests(unittest.TestCase):
    def ids(self, findings):
        return sorted({f.rule_id for f in findings})

    def test_clean_response(self):
        msg = {"type": "message", "id": "m1", "status": "completed", "role": "assistant",
               "content": [{"type": "output_text", "text": "hi", "annotations": []}]}
        self.assertEqual(check_response(response(output=[msg]), "x"), [])

    def test_response_shape_and_status(self):
        r = response(status="done")
        r["object"] = "chat.completion"
        del r["created_at"]
        self.assertEqual(self.ids(check_response(r, "x", expect_status="completed")), ["OR301", "OR302", "OR303"])

    def test_item_rules(self):
        self.assertIn("OR305", self.ids(check_item({"type": "function_call", "id": "f"}, "x")))
        self.assertIn("OR307", self.ids(check_item({"type": "search_result", "id": "s", "status": "completed"}, "x")))
        self.assertEqual(check_item({"type": "acme:search_result", "id": "s", "status": "completed"}, "x"), [])
        bad_args = {"type": "function_call", "id": "f", "call_id": "c", "name": "n", "arguments": "a=b",
                    "status": "completed"}
        self.assertIn("OR210", self.ids(check_item(bad_args, "x", final=True)))

    def test_error_bodies(self):
        good = json.dumps({"error": {"message": "m", "type": "invalid_request", "param": None, "code": None}})
        self.assertEqual(check_error_body(400, "application/json", good, "x"), [])
        legacy = json.dumps({"error": {"message": "m", "type": "invalid_request_error", "param": "model",
                                       "code": "model_not_found"}})
        self.assertEqual(check_error_body(404, "application/json; charset=utf-8", legacy, "x"), [])
        self.assertEqual(self.ids(check_error_body(200, "application/json", good, "x")), ["OR401"])
        self.assertEqual(self.ids(check_error_body(500, "application/json", good, "x")), ["OR401"])
        self.assertEqual(self.ids(check_error_body(400, "application/json", '{"message":"m"}', "x")), ["OR402"])
        self.assertEqual(self.ids(check_error_body(400, "text/plain", "nope", "x")), ["OR402", "OR404"])
        partial = json.dumps({"error": {"message": "m", "type": "invalid_request"}})
        self.assertEqual(self.ids(check_error_body(400, "application/json", partial, "x")), ["OR403"])


if __name__ == "__main__":
    unittest.main()
