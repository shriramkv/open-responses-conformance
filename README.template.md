# open-responses-conformance

Semantic conformance checks and a fault-injecting reference mock for
[Open Responses](https://www.openresponses.org/specification), the open multi-provider LLM API
specification hosted by the [Agentic AI Foundation (AAIF)](https://aaif.io/projects).

[![CI](https://github.com/shriramkv/open-responses-conformance/actions/workflows/ci.yml/badge.svg)](https://github.com/shriramkv/open-responses-conformance/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Spec](https://img.shields.io/badge/spec-2026--04--24-informational)
![License](https://img.shields.io/badge/license-MIT-green)

Open Responses defines one request format, one response object and one stream of semantic events,
so that a client written once works against any provider. That promise only holds if servers obey
the rules *between* events, not just the shape of each one:

- every item opens with `response.output_item.added` and closes with `response.output_item.done`
- deltas sit inside a content part and must add up to the `done` text
- nothing updates an item after it is done
- the stream carries exactly one terminal event, and an `error` is followed by `response.failed`
- the final response matches what was streamed
- `allowed_tools` and `tool_choice: "none"` are enforced

## Why another conformance tool

The official repository already has a schema-based compliance suite, and you should run it. This
tool is built to sit next to it:

| | Official compliance suite | open-responses-conformance |
|---|---|---|
| Validates each event and response against the JSON schema | yes | core fields only |
| Item and content-part lifecycle, delta consistency, final-vs-streamed | | yes |
| Terminal-event, `[DONE]` and error-then-failed rules | partly | yes |
| `tool_choice` and `allowed_tools` enforcement | | yes |
| Error body shape and status codes | partly | yes |
| Runs from a terminal or CI without a browser or CORS | Bun CLI | Python, zero dependencies |
| Offline linting of recorded streams | | yes |
| Fault-injecting mock server for testing **clients** | | yes, 41 faults |
| WebSocket transport, compaction | yes | not covered |

We ran the official suite against the mock with each fault switched on. **It missed 25 of the 41
faults; this tool caught all 41.** The clean mock passes all 10 official HTTP tests. The full table
and method are in [docs/official-suite-comparison.md](docs/official-suite-comparison.md).

Along the way we found that the official streaming test rejects correctly prefixed extension
events, which the specification says clients must ignore. A ready-to-file issue is in
[docs/upstream-issue-extension-events.md](docs/upstream-issue-extension-events.md).

## Install

```bash
pip install git+https://github.com/shriramkv/open-responses-conformance
```

No runtime dependencies. Python 3.9 or newer.

## Check a server

```bash
export OPENRESPONSES_API_KEY=...            # sent as "Authorization: Bearer ..."
open-responses-conformance check https://api.example.com/v1 --model my-model
```

Requests go to `<base_url>/responses`. Seven scenarios run:

| Scenario | What it does |
|---|---|
| `basic-text` | Non-streaming request; checks Content-Type and the response object |
| `streaming-text` | Streaming request; runs the full stream state machine |
| `streaming-tool-call` | Streaming with `tool_choice: "required"`; checks the function call lifecycle |
| `tool-choice-none` | `tool_choice: "none"` with a tool available; no call may be made |
| `allowed-tools` | Two tools, one allowed, prompt asks for the other; it must not be called |
| `invalid-request` | `input` of the wrong type; expects a structured 4xx error |
| `unknown-model` | A model that does not exist; expects a structured 4xx error |

Useful options:

```bash
--only streaming-text,streaming-tool-call    # or --skip ...
--header "OpenAI-Organization: org_123"      # repeatable
--save runs/                                 # keep raw bodies and streams for later
--format markdown -o report.md               # also json, badge (shields.io endpoint)
--min-grade B  --fail-on high                # exit 1 in CI
```

Exit codes: `0` passed, `1` a gate failed, `2` usage error.

Two scenarios depend on how the model behaves (`streaming-tool-call` needs a tool call,
`allowed-tools` asks for the forbidden tool). Against a real model, a single run is a strong signal
rather than proof. Run them a few times before reporting a provider.

## Lint recorded traffic

```bash
open-responses-conformance lint runs/streaming-text.sse runs/basic-text.json
```

`.sse` files go through the stream state machine. JSON files are checked as a response object, or
as an error payload when they hold a top-level `error`. Useful for bug reports and for CI fixtures.

## Test a client against a misbehaving server

```bash
open-responses-conformance mock --port 8080                       # spec-correct
open-responses-conformance mock --port 8080 --fault text-mismatch --fault double-terminal
open-responses-conformance mock --port 8080 --extensions          # adds an acme-style extension event
open-responses-conformance faults                                 # list all faults
```

Point your client library at `http://localhost:8080/v1` and see whether it copes. The mock is
deterministic: it answers text requests with a fixed sentence, calls the first eligible tool when
tools are offered (respecting `tool_choice` and `allowed_tools`), returns 400 for malformed
requests, 404 for any model containing `nonexistent`, and also serves `/responses/compact`.

### Faults

<!-- FAULTS -->

## Grading

Each distinct rule that fires costs points once, however many scenarios hit it: critical 40,
high 20, medium 8, low 3. Any critical finding caps the grade at D. Grades: A 90+, B 80+, C 70+,
D 60+, F below 60.

## Rules

Every rule names the section of the 2026-04-24 specification it enforces and the requirement
level used there.

<!-- RULES -->

`OR108` is labelled "MAY / MUST" because sending `[DONE]` is optional, but if it is sent it MUST come
after the terminal event. Error `type` accepts both `invalid_request` (the spec's table) and
`invalid_request_error` (the spec's own example).

## Demo

```text
$ open-responses-conformance demo -q
```

runs the seven scenarios against the clean mock (grade A) and then against the mock with three
faults switched on (grade F, with each finding tied to its spec section).

## Part of an AAIF toolchain

This tool sits alongside the author's other open source checkers for AAIF projects, including
[`agent-skills-lint`](https://github.com/shriramkv/agent-skills-lint) for Agent Skills,
[`mcp-scorecard`](https://github.com/shriramkv/mcp-scorecard) for MCP servers and
[`agentsmd-lint`](https://github.com/shriramkv/agentsmd-lint) for AGENTS.md. The full index is in
[`aaif-agentic-toolkit`](https://github.com/shriramkv/aaif-agentic-toolkit).

## Development

```bash
python -m unittest discover -s tests -t .
python scripts/build_readme.py                  # regenerate README tables
python scripts/compare_official.py ../openresponses   # rerun the official-suite comparison
```

The acceptance test (`tests/test_mock_matrix.py`) starts the mock once per fault and requires the
checker to report the fault's rule, and it requires every rule to be exercised by some fault.

## License

MIT. Copyright (c) 2026 Dr. Shriram K Vasudevan.
