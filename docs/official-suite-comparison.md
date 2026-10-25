# Comparison with the official compliance suite

The official Open Responses repository ships a compliance runner (`bin/compliance-test.ts`) that
validates each response and each streaming event against the JSON schema. This tool checks the
rules that sit *between* events and requests. The two are complementary, and the numbers below
show how little they overlap.

## Method

1. Start the mock from this repository with no faults and run the official HTTP tests against it.
2. Start the mock with exactly one fault, run the official HTTP tests, and record whether any test
   fails. Repeat for every fault.
3. Run `open-responses-conformance check` against the same faulty mock and confirm the expected
   rule fires.

The official WebSocket tests are left out because WebSocket is an optional (MAY) transport that
the mock does not implement. Reproduce with `python scripts/compare_official.py /path/to/openresponses`.

## Result

- clean mock: official 10 passed / 0 failed
- official suite commit 8907bc3 2026-10-21

| Fault | What the mock does | Official suite fails | open-responses-conformance |
|---|---|---|---|
| `added-item-incomplete` | output_item.added item has no status or role | yes | yes (OR207) |
| `allowed-tools-violation` | A tool outside allowed_tools is called | **no** | yes (OR502) |
| `args-mismatch` | Function argument deltas differ from the done arguments | **no** | yes (OR209) |
| `args-not-json` | Function arguments are not JSON | **no** | yes (OR210) |
| `bad-response-object` | Response object has the wrong object and no created_at | yes | yes (OR301) |
| `delta-before-item` | Text deltas arrive before output_item.added | **no** | yes (OR201) |
| `delta-before-part` | Text deltas arrive before content_part.added | **no** | yes (OR202) |
| `done-before-terminal` | [DONE] is sent before response.completed | yes | yes (OR108) |
| `done-item-in-progress` | output_item.done item is still in_progress | **no** | yes (OR211) |
| `double-terminal` | response.completed is sent twice | **no** | yes (OR106) |
| `error-200` | Invalid requests get HTTP 200 | yes | yes (OR401) |
| `error-fields` | Error object omits code and param | **no** | yes (OR403) |
| `error-not-json` | Error body is plain text | **no** | yes (OR404) |
| `error-shape` | Error body has no top-level error object | **no** | yes (OR402) |
| `error-without-failed` | An error event is followed by response.completed | **no** | yes (OR109) |
| `event-type-mismatch` | SSE event: name differs from the body type | **no** | yes (OR101) |
| `events-after-terminal` | A delta arrives after response.completed | **no** | yes (OR107) |
| `final-mismatch` | Final response text differs from the streamed text | **no** | yes (OR304) |
| `incomplete-not-last` | Item ends incomplete but the response completes | **no** | yes (OR208) |
| `index-mismatch` | Deltas carry the wrong item_id | **no** | yes (OR206) |
| `invalid-json` | One SSE data line is not JSON | yes | yes (OR004) |
| `item-shape` | Final message item has no role | yes | yes (OR305) |
| `missing-field` | content_part.added has no item_id | yes | yes (OR110) |
| `missing-sequence` | One event has no sequence_number | yes | yes (OR103) |
| `missing-terminal` | Stream ends without response.completed | yes | yes (OR105) |
| `missing-type` | One event body has no type | **no** | yes (OR102) |
| `response-id-changed` | Response id changes between created and completed | **no** | yes (OR306) |
| `sequence-regress` | sequence_number goes backwards | **no** | yes (OR103) |
| `server-error` | Every valid request returns HTTP 500 | yes | yes (OR001) |
| `skip-required-tool` | tool_choice 'required' is answered with text | **no** | yes (OR501) |
| `sse-id` | Server sets the SSE id field on every event | **no** | yes (OR104) |
| `terminal-status-mismatch` | response.completed carries status in_progress | yes | yes (OR303) |
| `text-mismatch` | output_text.done text differs from the deltas | **no** | yes (OR205) |
| `tool-choice-none-violation` | A tool is called despite tool_choice 'none' | **no** | yes (OR503) |
| `truncated-stream` | Stream is cut off in the middle of an event | yes | yes (OR105) |
| `unclosed-part` | content_part.done is never sent | **no** | yes (OR204) |
| `unknown-response-status` | Final response status is 'done' | yes | yes (OR302) |
| `unprefixed-extension` | Extension event type has no implementor prefix | yes | yes (OR111) |
| `unprefixed-item-type` | Output includes a 'search_result' item with no prefix | yes | yes (OR307) |
| `update-after-done` | A delta arrives after output_item.done | **no** | yes (OR203) |
| `wrong-content-type` | Responses and streams are served as text/plain | yes | yes (OR002, OR003) |


**Official suite missed 25 of 41 faults.** This tool detects all 41.

## Reading the result

The faults the official suite does not catch are all cross-event semantics. Each individual event
is well formed, but the stream as a whole breaks a MUST in the specification. Examples:

- deltas that do not concatenate to the `done` text (`text-mismatch`, `args-mismatch`)
- events for an item after `response.output_item.done` (`update-after-done`)
- a second terminal event, or events after it (`double-terminal`, `events-after-terminal`)
- an `error` event not followed by `response.failed` (`error-without-failed`)
- the final response disagreeing with what was streamed (`final-mismatch`)
- `allowed_tools` and `tool_choice: "none"` being ignored

None of this is a criticism of the official suite, which is built around schema validation and
does that thoroughly. The point is that a server can pass it and still break clients.

## A gap found in the official suite

With the mock's optional extension event switched on (`mock --extensions`), the official
"Streaming Response" test fails on `mock:trace_event`. The event is correctly prefixed, and the
specification says clients MUST be able to ignore such events. See
[upstream-issue-extension-events.md](upstream-issue-extension-events.md) for a ready-to-file issue.
