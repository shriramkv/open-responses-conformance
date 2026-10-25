"""Reproduce docs/official-suite-comparison.md.

Runs the official Open Responses compliance CLI (bin/compliance-test.ts) against
the mock, once clean and once per fault, and records whether the official suite
reports a failure. Needs Bun and a checkout of openresponses/openresponses with
`bun install && bun run generate:zod` done.

    python scripts/compare_official.py /path/to/openresponses
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from open_responses_conformance.live import run_scenarios  # noqa: E402
from open_responses_conformance.mock import FAULTS, running_mock  # noqa: E402

# The official HTTP tests. The WebSocket tests are left out because WebSocket
# is an optional (MAY) transport that the mock does not implement.
HTTP_TESTS = ("basic-response,assistant-phase,response-output-phase-schema,streaming-response,system-prompt,"
              "tool-calling,image-input,multi-turn,compact-response,compact-missing-model")


def official(repo, base_url):
    p = subprocess.run(["bun", "run", "bin/compliance-test.ts", "--base-url", base_url, "--api-key", "x",
                        "--model", "mock-model", "--filter", HTTP_TESTS],
                       cwd=repo, capture_output=True, text=True, timeout=180)
    out = re.sub(r"\x1b\[[0-9;]*m", "", p.stdout + p.stderr)
    m = re.search(r"Results: (\d+) passed, (\d+) failed", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, -1)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    repo = sys.argv[1]
    commit = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%h %cs"], capture_output=True,
                            text=True).stdout.strip()
    rows, missed = [], []
    with running_mock() as m:
        print("clean mock: official %s passed / %s failed" % official(repo, m.base_url))
    for fault, (desc, expected) in sorted(FAULTS.items()):
        with running_mock([fault]) as m:
            _, failed = official(repo, m.base_url)
            ours = {f.rule_id for r in run_scenarios(m.base_url, "mock-model") for f in r.findings}
        caught_ours = all(e in ours for e in expected)
        if failed == 0:
            missed.append(fault)
        rows.append("| `%s` | %s | %s | %s |" % (fault, desc, "yes" if failed > 0 else "**no**",
                                                 "yes (%s)" % ", ".join(expected) if caught_ours else "NO"))
    print("official suite commit %s" % commit)
    print("| Fault | What the mock does | Official suite fails | open-responses-conformance |")
    print("|---|---|---|---|")
    print("\n".join(rows))
    print("\nOfficial suite missed %d of %d faults." % (len(missed), len(FAULTS)))


if __name__ == "__main__":
    main()
