"""Regenerate README.md from README.template.md with the current rule and fault tables."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from open_responses_conformance.mock import FAULTS  # noqa: E402
from open_responses_conformance.report import render_rules  # noqa: E402


def faults_table():
    rows = ["| Fault | What the mock does | Detected by |", "|---|---|---|"]
    rows += ["| `%s` | %s | %s |" % (k, v[0], ", ".join(v[1])) for k, v in sorted(FAULTS.items())]
    return "\n".join(rows)


def main():
    with open(os.path.join(ROOT, "README.template.md")) as fh:
        text = fh.read()
    text = text.replace("<!-- RULES -->", render_rules("markdown").rstrip("\n"))
    text = text.replace("<!-- FAULTS -->", faults_table())
    with open(os.path.join(ROOT, "README.md"), "w") as fh:
        fh.write(text)
    print("README.md written")


if __name__ == "__main__":
    main()
