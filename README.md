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

| Fault | What the mock does | Detected by |
|---|---|---|
| `added-item-incomplete` | output_item.added item has no status or role | OR207 |
| `allowed-tools-violation` | A tool outside allowed_tools is called | OR502 |
| `args-mismatch` | Function argument deltas differ from the done arguments | OR209 |
| `args-not-json` | Function arguments are not JSON | OR210 |
| `bad-response-object` | Response object has the wrong object and no created_at | OR301 |
| `delta-before-item` | Text deltas arrive before output_item.added | OR201 |
| `delta-before-part` | Text deltas arrive before content_part.added | OR202 |
| `done-before-terminal` | [DONE] is sent before response.completed | OR108 |
| `done-item-in-progress` | output_item.done item is still in_progress | OR211 |
| `double-terminal` | response.completed is sent twice | OR106 |
| `error-200` | Invalid requests get HTTP 200 | OR401 |
| `error-fields` | Error object omits code and param | OR403 |
| `error-not-json` | Error body is plain text | OR404 |
| `error-shape` | Error body has no top-level error object | OR402 |
| `error-without-failed` | An error event is followed by response.completed | OR109 |
| `event-type-mismatch` | SSE event: name differs from the body type | OR101 |
| `events-after-terminal` | A delta arrives after response.completed | OR107 |
| `final-mismatch` | Final response text differs from the streamed text | OR304 |
| `incomplete-not-last` | Item ends incomplete but the response completes | OR208 |
| `index-mismatch` | Deltas carry the wrong item_id | OR206 |
| `invalid-json` | One SSE data line is not JSON | OR004 |
| `item-shape` | Final message item has no role | OR305 |
| `missing-field` | content_part.added has no item_id | OR110 |
| `missing-sequence` | One event has no sequence_number | OR103 |
| `missing-terminal` | Stream ends without response.completed | OR105 |
| `missing-type` | One event body has no type | OR102 |
| `response-id-changed` | Response id changes between created and completed | OR306 |
| `sequence-regress` | sequence_number goes backwards | OR103 |
| `server-error` | Every valid request returns HTTP 500 | OR001 |
| `skip-required-tool` | tool_choice 'required' is answered with text | OR501 |
| `sse-id` | Server sets the SSE id field on every event | OR104 |
| `terminal-status-mismatch` | response.completed carries status in_progress | OR303 |
| `text-mismatch` | output_text.done text differs from the deltas | OR205 |
| `tool-choice-none-violation` | A tool is called despite tool_choice 'none' | OR503 |
| `truncated-stream` | Stream is cut off in the middle of an event | OR105 |
| `unclosed-part` | content_part.done is never sent | OR204 |
| `unknown-response-status` | Final response status is 'done' | OR302 |
| `unprefixed-extension` | Extension event type has no implementor prefix | OR111 |
| `unprefixed-item-type` | Output includes a 'search_result' item with no prefix | OR307 |
| `update-after-done` | A delta arrives after output_item.done | OR203 |
| `wrong-content-type` | Responses and streams are served as text/plain | OR002, OR003 |

## Grading

Each distinct rule that fires costs points once, however many scenarios hit it: critical 40,
high 20, medium 8, low 3. Any critical finding caps the grade at D. Grades: A 90+, B 80+, C 70+,
D 60+, F below 60.

## Rules

Every rule names the section of the 2026-04-24 specification it enforces and the requirement
level used there.

| Rule | Slug | Severity | Spec level | Spec section | Checks |
|---|---|---|---|---|---|
| OR001 | `request-failed` | critical | MUST | HTTP Responses | A valid request did not succeed (connection error or non-2xx status) |
| OR002 | `content-type-json` | high | MUST | HTTP Responses | Non-streaming response is not served as application/json |
| OR003 | `content-type-sse` | high | MUST | HTTP Responses | Streaming response is not served as text/event-stream |
| OR004 | `invalid-json` | critical | MUST | HTTP Responses | Body or SSE data line is not valid JSON |
| OR101 | `event-type-mismatch` | high | MUST | HTTP Responses | SSE event: field does not match the type in the event body |
| OR102 | `missing-type` | high | MUST | Streaming | Streaming event has no type |
| OR103 | `sequence-number` | high | schema | Streaming | sequence_number is missing or does not increase |
| OR104 | `sse-id-used` | low | SHOULD NOT | HTTP Responses | Server sets the SSE id field |
| OR105 | `no-terminal-event` | critical | MUST | HTTP Responses | Stream ended without response.completed, response.failed or response.incomplete |
| OR106 | `multiple-terminal-events` | high | MUST | HTTP Responses | Stream carries more than one terminal response event |
| OR107 | `events-after-terminal` | high | MUST | HTTP Responses | Events arrive after the terminal response event |
| OR108 | `done-before-terminal` | high | MAY / MUST | HTTP Responses | [DONE] arrives before the terminal response event |
| OR109 | `error-without-failed` | high | MUST | Errors | A streamed error event is not followed by response.failed |
| OR110 | `missing-event-field` | high | schema | Streaming | Streaming event lacks a field the schema requires |
| OR111 | `unprefixed-extension` | medium | MUST | Extending streaming events | Non-standard event type is not prefixed with an implementor slug |
| OR201 | `item-not-added` | high | MUST | Items are streamable | Event refers to an output item before response.output_item.added |
| OR202 | `part-not-added` | high | MUST | Items are streamable | Content delta or done arrives before response.content_part.added |
| OR203 | `update-after-done` | high | MUST NOT | Items are state machines | Item or content part receives updates after it is done |
| OR204 | `item-not-closed` | high | MUST | Items are streamable | Item or content part is never closed with its done event |
| OR205 | `text-mismatch` | high | MUST | Items are streamable | Concatenated deltas differ from the done text or the done part |
| OR206 | `index-mismatch` | medium | schema | Items are streamable | output_index or item_id is inconsistent across an item's events |
| OR207 | `added-item-incomplete` | medium | MUST | Items are streamable | response.output_item.added item lacks id, type, status or its key fields |
| OR208 | `incomplete-not-last` | high | MUST | Items are state machines | An incomplete item is not last, or the response is not incomplete |
| OR209 | `arguments-mismatch` | high | MUST | Items are streamable | Function call argument deltas differ from the done arguments or the item |
| OR210 | `arguments-not-json` | medium | schema | Tools | Function call arguments are not a JSON document |
| OR211 | `done-item-status` | medium | MUST | Items are state machines | response.output_item.done item is still in_progress |
| OR301 | `response-shape` | high | schema | HTTP Responses | Response object lacks core fields or object is not 'response' |
| OR302 | `response-status` | medium | schema | State machines | Response status is not a known value |
| OR303 | `terminal-status-mismatch` | high | MUST | State machines | Terminal event type does not match the response status |
| OR304 | `final-output-mismatch` | high | MUST | Items are streamable | Final response output differs from the items that were streamed |
| OR305 | `item-shape` | medium | MUST | Extending Items | Output item lacks required fields for its type |
| OR306 | `response-id-changed` | medium | schema | Streaming | Response id changes between lifecycle events |
| OR307 | `unprefixed-item-type` | medium | MUST | Items are extensible | Non-standard item type is not prefixed with an implementor slug |
| OR401 | `error-status-code` | medium | schema | Errors | Invalid request did not get a 4xx status |
| OR402 | `error-shape` | high | schema | Errors | Error body lacks error.message or error.type |
| OR403 | `error-fields` | low | schema | Errors | Error object omits code or param (they may be null but should be present) |
| OR404 | `error-not-json` | medium | MUST | HTTP Responses | Error body is not application/json |
| OR501 | `required-tool-not-called` | high | MUST | tool_choice | tool_choice 'required' or a named function, but no function_call was produced |
| OR502 | `allowed-tools-violated` | high | MUST | allowed_tools | A tool outside allowed_tools was called |
| OR503 | `tool-choice-none-violated` | high | MUST NOT | tool_choice | tool_choice 'none' but a function_call was produced |

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
