"""Command line interface."""

import argparse
import json
import os
import sys

from . import SPEC_VERSION, __version__
from .live import SCENARIO_IDS, ScenarioResult, run_scenarios
from .mock import FAULTS, MockConfig, make_server, running_mock
from .objects import check_error_body, check_response
from .report import RENDERERS, Report, render_rules
from .rules import GRADE_ORDER, SEVERITY_RANK
from .sse import parse_sse
from .stream import validate_stream

COMMANDS = ("check", "lint", "mock", "faults", "rules", "demo", "version")


def _csv(value):
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def _output_args(p):
    p.add_argument("--format", choices=sorted(RENDERERS), default="console")
    p.add_argument("-o", "--output", help="write the report to this file")
    p.add_argument("-q", "--quiet", action="store_true", help="console: one line per scenario")


def _gate_args(p):
    p.add_argument("--min-grade", choices=list("ABCDF"), help="exit 1 if the grade is below this")
    p.add_argument("--fail-on", choices=["critical", "high", "medium", "low"],
                   help="exit 1 if any finding is at or above this severity")


def build_parser():
    p = argparse.ArgumentParser(
        prog="open-responses-conformance",
        description="Semantic conformance checks and a fault-injecting mock server for Open Responses "
                    "(spec %s)." % SPEC_VERSION)
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("check", help="run live scenarios against an endpoint")
    c.add_argument("base_url", help="base URL; requests go to <base_url>/responses, e.g. http://localhost:8080/v1")
    c.add_argument("--model", default=os.environ.get("OPENRESPONSES_MODEL"),
                   help="model to request (default: $OPENRESPONSES_MODEL)")
    c.add_argument("--api-key-env", default="OPENRESPONSES_API_KEY",
                   help="environment variable holding the API key (default OPENRESPONSES_API_KEY)")
    c.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="extra request header")
    c.add_argument("--timeout", type=float, default=60.0, help="seconds per request (default 60)")
    c.add_argument("--only", default="", help="comma-separated scenario ids to run (%s)" % ", ".join(SCENARIO_IDS))
    c.add_argument("--skip", default="", help="comma-separated scenario ids to skip")
    c.add_argument("--save", metavar="DIR", help="save raw responses and streams for later 'lint'")
    _output_args(c)
    _gate_args(c)

    l = sub.add_parser("lint", help="validate saved SSE streams (.sse) or JSON responses offline")
    l.add_argument("files", nargs="+")
    _output_args(l)
    _gate_args(l)

    m = sub.add_parser("mock", help="serve a spec-correct mock endpoint, optionally with faults")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8080)
    m.add_argument("--fault", action="append", default=[], choices=sorted(FAULTS), metavar="FAULT",
                   help="inject a fault (repeatable); see the 'faults' command")
    m.add_argument("--reply", default=None, help="text the mock answers with")
    m.add_argument("--extensions", action="store_true",
                   help="also emit a prefixed mock:trace_event extension event, to test client tolerance")
    m.add_argument("-v", "--verbose", action="store_true", help="log requests")

    f = sub.add_parser("faults", help="list the mock's fault modes")
    f.add_argument("--format", choices=["console", "json", "markdown"], default="console")

    r = sub.add_parser("rules", help="list every rule")
    r.add_argument("--format", choices=["console", "json", "markdown"], default="console")

    d = sub.add_parser("demo", help="check the mock clean, then with faults")
    d.add_argument("--fault", action="append", default=[], choices=sorted(FAULTS), metavar="FAULT",
                   help="faults for the second run (default: text-mismatch, sse-id, allowed-tools-violation)")
    _output_args(d)

    sub.add_parser("version", help="print the version")
    return p


def _emit(rep, args):
    if args.format == "console":
        text = RENDERERS["console"](rep, verbose=not args.quiet)
    else:
        text = RENDERERS[args.format](rep)
    if getattr(args, "output", None):
        with open(args.output, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def _gates(rep, args):
    failed = False
    if args.min_grade and GRADE_ORDER[rep.grade] < GRADE_ORDER[args.min_grade]:
        sys.stderr.write("grade %s is below --min-grade %s\n" % (rep.grade, args.min_grade))
        failed = True
    worst = rep.worst_severity()
    if args.fail_on and worst and SEVERITY_RANK[worst] >= SEVERITY_RANK[args.fail_on]:
        sys.stderr.write("found %s findings (--fail-on %s)\n" % (worst, args.fail_on))
        failed = True
    return 1 if failed else 0


def lint_file(path):
    res = ScenarioResult(os.path.basename(path), "Offline: %s" % path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    stripped = text.lstrip()
    if path.endswith(".json") or stripped.startswith("{"):
        try:
            body = json.loads(text)
        except ValueError:
            from .rules import Finding
            res.findings.append(Finding("OR004", "file is not JSON", res.id))
            return res
        if isinstance(body, dict) and "error" in body and "object" not in body:
            res.findings.extend(f for f in check_error_body(400, "application/json", text, res.id)
                                if f.rule_id != "OR401")
            res.notes.append("error payload")
        else:
            res.findings.extend(check_response(body, res.id))
            res.notes.append("response object")
    else:
        outcome = validate_stream(parse_sse(text), where=res.id)
        res.findings.extend(outcome.findings)
        res.notes.append("%d events, terminal %s" % (outcome.event_count, outcome.terminal or "none"))
    return res


def _print_faults(fmt):
    if fmt == "json":
        sys.stdout.write(json.dumps([{"fault": k, "description": v[0], "detected_by": v[1]}
                                     for k, v in sorted(FAULTS.items())], indent=2) + "\n")
    elif fmt == "markdown":
        rows = ["| Fault | What the mock does | Detected by |", "|---|---|---|"]
        rows += ["| `%s` | %s | %s |" % (k, v[0], ", ".join(v[1])) for k, v in sorted(FAULTS.items())]
        sys.stdout.write("\n".join(rows) + "\n")
    else:
        for k, v in sorted(FAULTS.items()):
            sys.stdout.write("  %-28s %-58s %s\n" % (k, v[0], ",".join(v[1])))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    cmd = args.command
    if cmd is None:
        parser.print_help()
        return 2
    if cmd == "version":
        print("open-responses-conformance %s (spec %s)" % (__version__, SPEC_VERSION))
        return 0
    if cmd == "rules":
        sys.stdout.write(render_rules(args.format))
        return 0
    if cmd == "faults":
        _print_faults(args.format)
        return 0
    if cmd == "mock":
        cfg = MockConfig(args.fault, extensions=args.extensions,
                         **({"reply": args.reply} if args.reply else {}))
        srv = make_server(args.host, args.port, cfg, verbose=args.verbose)
        host, port = srv.server_address[:2]
        sys.stderr.write("Open Responses mock on http://%s:%d/v1/responses  faults: %s\n"
                         % (host, port, ", ".join(sorted(cfg.faults)) or "none"))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
        return 0
    if cmd == "demo":
        with running_mock() as m:
            clean = Report("mock (no faults)", run_scenarios(m.base_url, "mock-model"), mode="demo")
        faults = args.fault or ["text-mismatch", "sse-id", "allowed-tools-violation"]
        with running_mock(faults) as m:
            broken = Report("mock with faults: %s" % ", ".join(faults), run_scenarios(m.base_url, "mock-model"),
                            mode="demo")
        _emit(clean, args)
        if args.format == "console":
            sys.stdout.write("\n")
        _emit(broken, args)
        return 0
    if cmd == "lint":
        for p in args.files:
            if not os.path.isfile(p):
                sys.stderr.write("file not found: %s\n" % p)
                return 2
        rep = Report(", ".join(args.files), [lint_file(p) for p in args.files], mode="lint")
        _emit(rep, args)
        return _gates(rep, args)

    # check
    if not args.model:
        sys.stderr.write("--model is required (or set OPENRESPONSES_MODEL)\n")
        return 2
    only, skip = _csv(args.only), _csv(args.skip)
    unknown = [s for s in only + skip if s not in SCENARIO_IDS]
    if unknown:
        sys.stderr.write("unknown scenario(s): %s\n" % ", ".join(unknown))
        return 2
    headers = {}
    for h in args.header:
        if ":" not in h:
            sys.stderr.write("--header must look like NAME:VALUE\n")
            return 2
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()
    results = run_scenarios(args.base_url, args.model, api_key=os.environ.get(args.api_key_env),
                            headers=headers, timeout=args.timeout, only=only, skip=skip, save_dir=args.save)
    rep = Report(args.base_url, results)
    _emit(rep, args)
    return _gates(rep, args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
