"""Reports: aggregation, scoring and rendering."""

import json

from . import SPEC_VERSION, __version__
from .rules import RULES, SEVERITY_RANK, grade_for, score

BADGE_COLORS = {"A": "brightgreen", "B": "green", "C": "yellow", "D": "orange", "F": "red"}


class Report(object):
    """Scores by unique rule: a protocol bug counts once however many scenarios hit it."""

    def __init__(self, target, scenarios, mode="check"):
        self.target = target
        self.scenarios = scenarios
        self.mode = mode

    def findings(self):
        for s in self.scenarios:
            for f in s.findings:
                yield s, f

    def unique(self):
        first = {}
        for _, f in self.findings():
            first.setdefault(f.rule_id, f)
        return list(first.values())

    @property
    def score(self):
        return score(self.unique())

    @property
    def grade(self):
        return grade_for(self.score)

    def counts(self):
        c = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in self.unique():
            c[f.severity] += 1
        return c

    def worst_severity(self):
        worst = None
        for f in self.unique():
            if worst is None or SEVERITY_RANK[f.severity] > SEVERITY_RANK[worst]:
                worst = f.severity
        return worst


def render_console(rep, verbose=True):
    out = ["open-responses-conformance %s | spec %s | %s" % (__version__, SPEC_VERSION, rep.target), ""]
    for s in rep.scenarios:
        if s.skipped:
            out.append("skip  %-22s %s" % (s.id, s.skipped))
            continue
        mark = "pass" if s.passed else "FAIL"
        note = ("  [%s]" % "; ".join(s.notes)) if s.notes else ""
        out.append("%s  %-22s %s%s" % (mark, s.id, s.title, note))
        if verbose:
            for f in s.findings:
                r = f.rule
                out.append("        %-8s %s %-26s %s" % (r.severity.upper(), r.id, r.slug, f.message))
                out.append("                 spec: %s (%s)" % (r.section, r.level))
    c = rep.counts()
    run = [s for s in rep.scenarios if not s.skipped]
    out.append("")
    out.append("Scenarios: %d passed, %d failed, %d skipped"
               % (sum(s.passed for s in run), sum(not s.passed for s in run), len(rep.scenarios) - len(run)))
    out.append("Distinct issues: %d critical, %d high, %d medium, %d low"
               % (c["critical"], c["high"], c["medium"], c["low"]))
    out.append("Grade: %s (%d)" % (rep.grade, rep.score))
    return "\n".join(out) + "\n"


def to_dict(rep):
    return {"tool": "open-responses-conformance", "version": __version__, "spec_version": SPEC_VERSION,
            "target": rep.target, "mode": rep.mode,
            "overall": {"score": rep.score, "grade": rep.grade, "counts": rep.counts()},
            "scenarios": [s.to_dict() for s in rep.scenarios]}


def render_json(rep):
    return json.dumps(to_dict(rep), indent=2) + "\n"


def badge_url(rep):
    return "https://img.shields.io/badge/open%%20responses-%s%%20%%28%d%%29-%s" % (
        rep.grade, rep.score, BADGE_COLORS[rep.grade])


def render_badge(rep):
    return json.dumps({"schemaVersion": 1, "label": "open responses",
                       "message": "%s (%d)" % (rep.grade, rep.score), "color": BADGE_COLORS[rep.grade]}) + "\n"


def _e(s):
    return str(s).replace("|", "\\|")


def render_markdown(rep):
    c = rep.counts()
    out = ["## Open Responses conformance report", "",
           "![open responses](%s)" % badge_url(rep), "",
           "Target `%s`, spec %s. **Grade %s (%d).** Distinct issues: %d critical, %d high, %d medium, %d low."
           % (rep.target, SPEC_VERSION, rep.grade, rep.score, c["critical"], c["high"], c["medium"], c["low"]),
           "", "| Scenario | Result | Notes |", "|---|---|---|"]
    for s in rep.scenarios:
        res = "skipped" if s.skipped else ("pass" if s.passed else "**fail**")
        out.append("| %s | %s | %s |" % (_e(s.title), res, _e("; ".join(s.notes))))
    rows = [(s, f) for s, f in rep.findings()]
    if rows:
        out += ["", "| Severity | Rule | Scenario | Finding | Spec |", "|---|---|---|---|---|"]
        for s, f in rows:
            r = f.rule
            out.append("| %s | %s `%s` | %s | %s | %s (%s) |" % (r.severity, r.id, r.slug, s.id, _e(f.message),
                                                              r.section, r.level))
    return "\n".join(out) + "\n"


def render_rules(fmt="console"):
    rules = sorted(RULES.values(), key=lambda r: r.id)
    if fmt == "json":
        return json.dumps([r._asdict() for r in rules], indent=2) + "\n"
    if fmt == "markdown":
        out = ["| Rule | Slug | Severity | Spec level | Spec section | Checks |", "|---|---|---|---|---|---|"]
        for r in rules:
            out.append("| %s | `%s` | %s | %s | %s | %s |" % (r.id, r.slug, r.severity, r.level, r.section, _e(r.summary)))
        return "\n".join(out) + "\n"
    out, cat = [], None
    for r in rules:
        if r.category != cat:
            cat = r.category
            out.append("\n[%s]" % cat)
        out.append("  %s %-26s %-8s %-10s %s" % (r.id, r.slug, r.severity, r.level, r.summary))
    return "\n".join(out).lstrip("\n") + "\n"


RENDERERS = {"console": render_console, "json": render_json, "markdown": render_markdown, "badge": render_badge}
