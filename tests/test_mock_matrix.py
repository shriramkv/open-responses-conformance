"""The checker's acceptance test: the clean mock grades A, and every fault is detected."""

import json
import unittest

from open_responses_conformance.live import run_scenarios
from open_responses_conformance.mock import FAULTS, MockConfig, handle_request, running_mock
from open_responses_conformance.report import Report
from open_responses_conformance.rules import RULES


def found(faults=(), **kw):
    with running_mock(faults, **kw) as m:
        results = run_scenarios(m.base_url, "mock-model", timeout=15)
    return results, {f.rule_id for r in results for f in r.findings}


class CleanMock(unittest.TestCase):
    def test_clean_mock_grades_a(self):
        results, rules = found()
        self.assertEqual(rules, set(), [f for r in results for f in r.findings])
        rep = Report("mock", results)
        self.assertEqual((rep.grade, rep.score), ("A", 100))
        self.assertTrue(all(r.passed for r in results))

    def test_clean_mock_with_extensions_still_grades_a(self):
        self.assertEqual(found(extensions=True)[1], set())


class FaultMatrix(unittest.TestCase):
    def test_every_fault_is_detected(self):
        coverage = set()
        for fault, (_desc, expected) in sorted(FAULTS.items()):
            with self.subTest(fault=fault):
                _, rules = found([fault])
                coverage |= rules
                for rule in expected:
                    self.assertIn(rule, rules, "%s should trigger %s, got %s" % (fault, rule, sorted(rules)))
        self.assertEqual(sorted(set(RULES) - coverage), [], "rules no fault exercises")

    def test_every_expected_rule_exists(self):
        for fault, (_desc, expected) in FAULTS.items():
            for rule in expected:
                self.assertIn(rule, RULES, fault)


class MockUnit(unittest.TestCase):
    def call(self, body, faults=(), compact=False):
        return handle_request(json.dumps(body).encode(), MockConfig(faults), compact=compact)

    def test_errors(self):
        status, ctype, body = self.call({"input": "x"})
        self.assertEqual((status, json.loads(body)["error"]["param"]), (400, "model"))
        status, _, body = self.call({"model": "a-nonexistent-model", "input": "x"})
        self.assertEqual((status, json.loads(body)["error"]["code"]), (404, "model_not_found"))
        self.assertEqual(self.call({"model": "m", "input": 5})[0], 400)
        self.assertEqual(handle_request(b"{oops", MockConfig())[0], 400)

    def test_tool_selection(self):
        tools = [{"type": "function", "name": "a"}, {"type": "function", "name": "b"}]
        out = lambda tc, f=(): json.loads(self.call({"model": "m", "input": "x", "tools": tools,
                                                      "tool_choice": tc}, f)[2])["output"][0]
        self.assertEqual(out({"type": "function", "name": "b"})["name"], "b")
        self.assertEqual(out({"type": "allowed_tools", "tools": [{"type": "function", "name": "b"}]})["name"], "b")
        self.assertEqual(out("none")["type"], "message")
        self.assertEqual(out("none", ["tool-choice-none-violation"])["type"], "function_call")

    def test_tools_echo_fills_strict(self):
        body = json.loads(self.call({"model": "m", "input": "x",
                                     "tools": [{"type": "function", "name": "a", "parameters": {}}]})[2])
        self.assertEqual(body["tools"][0]["strict"], True)
        self.assertIn("description", body["tools"][0])

    def test_compaction(self):
        status, _, body = self.call({"model": "m", "input": [{"type": "message", "role": "user", "content": "x"}]},
                                    compact=True)
        data = json.loads(body)
        self.assertEqual((status, data["object"], data["output"][0]["type"]), (200, "response.compaction", "compaction"))
        self.assertEqual(self.call({"input": "x"}, compact=True)[0], 400)

    def test_unknown_fault_rejected(self):
        with self.assertRaises(ValueError):
            MockConfig(["nope"])


if __name__ == "__main__":
    unittest.main()
