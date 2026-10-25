import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from open_responses_conformance.cli import main
from open_responses_conformance.mock import running_mock


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orc-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_demo(self):
        code, out, _ = run("demo", "-q")
        self.assertEqual(code, 0)
        self.assertIn("Grade: A (100)", out)
        self.assertIn("allowed-tools", out)

    def test_check_json_and_gates(self):
        with running_mock() as m:
            code, out, _ = run("check", m.base_url, "--model", "mock-model", "--format", "json",
                               "--min-grade", "A", "--fail-on", "low")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["overall"]["grade"], "A")
        with running_mock(["text-mismatch"]) as m:
            self.assertEqual(run("check", m.base_url, "--model", "x", "--min-grade", "B")[0], 1)
            self.assertEqual(run("check", m.base_url, "--model", "x", "--fail-on", "high")[0], 1)
            self.assertEqual(run("check", m.base_url, "--model", "x")[0], 0)

    def test_only_and_skip(self):
        with running_mock(["server-error"]) as m:
            code, out, _ = run("check", m.base_url, "--model", "x", "--only", "invalid-request,unknown-model",
                               "--fail-on", "critical")
        self.assertEqual(code, 0)
        self.assertIn("skip  basic-text", out)

    def test_save_then_lint_roundtrip(self):
        save = os.path.join(self.tmp, "runs")
        with running_mock(["update-after-done"]) as m:
            run("check", m.base_url, "--model", "x", "--save", save)
        files = sorted(os.listdir(save))
        self.assertIn("streaming-text.sse", files)
        self.assertIn("basic-text.json", files)
        code, out, _ = run("lint", os.path.join(save, "streaming-text.sse"), "--fail-on", "high")
        self.assertEqual(code, 1)
        self.assertIn("OR203", out)
        code, out, _ = run("lint", os.path.join(save, "basic-text.json"), os.path.join(save, "unknown-model.json"),
                           "--min-grade", "A")
        self.assertEqual(code, 0, out)

    def test_markdown_badge_rules_faults(self):
        with running_mock() as m:
            _, md, _ = run("check", m.base_url, "--model", "x", "--format", "markdown")
            _, badge, _ = run("check", m.base_url, "--model", "x", "--format", "badge")
        self.assertIn("| Scenario | Result | Notes |", md)
        self.assertEqual(json.loads(badge)["message"], "A (100)")
        from open_responses_conformance.mock import FAULTS
        from open_responses_conformance.rules import RULES
        self.assertEqual(len(json.loads(run("rules", "--format", "json")[1])), len(RULES))
        self.assertEqual(len(json.loads(run("faults", "--format", "json")[1])), len(FAULTS))

    def test_usage_errors(self):
        self.assertEqual(run("check", "http://127.0.0.1:9/v1")[0], 2)  # no --model
        self.assertEqual(run("check", "http://127.0.0.1:9/v1", "--model", "x", "--only", "nope")[0], 2)
        self.assertEqual(run("check", "http://127.0.0.1:9/v1", "--model", "x", "--header", "bad")[0], 2)
        self.assertEqual(run("lint", os.path.join(self.tmp, "missing.sse"))[0], 2)

    def test_unreachable_server_is_critical(self):
        code, out, _ = run("check", "http://127.0.0.1:9/v1", "--model", "x", "--timeout", "2",
                           "--only", "basic-text", "--fail-on", "critical")
        self.assertEqual(code, 1)
        self.assertIn("OR001", out)


if __name__ == "__main__":
    unittest.main()
