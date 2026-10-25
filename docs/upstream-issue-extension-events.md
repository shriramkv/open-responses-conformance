# Draft issue for openresponses/openresponses

Ready to file at https://github.com/openresponses/openresponses/issues. Check that it still
reproduces on the latest commit first.

---

**Title:** Compliance tests reject correctly prefixed extension streaming events

**Summary**

The streaming compliance test fails when a server emits a non-standard streaming event, even when
the event follows the extension rules in the specification.

The specification (2026-04-24, "Extending streaming events") says:

- implementors MAY introduce non-standard streaming event types
- such events MUST be prefixed with the implementor slug, for example `acme:trace_event`
- each such event MUST include `type` and `sequence_number`
- clients MUST be able to ignore unknown extension events

**Steps to reproduce**

1. Run a server that adds one extension event to an otherwise valid stream:

   ```text
   event: acme:trace_event
   data: {"type":"acme:trace_event","sequence_number":2,"detail":"planning output"}
   ```

   One way is the reference mock in [open-responses-conformance](https://github.com/shriramkv/open-responses-conformance):
   `open-responses-conformance mock --port 8080 --extensions`

2. Run `bun run bin/compliance-test.ts -u http://localhost:8080/v1 -k x -m mock-model --filter streaming-response`

**Expected:** the test passes, with the extension event ignored.

**Actual:** `Event validation failed for acme:trace_event: [{"code":"invalid_union", ...}]`. The SSE
parser validates every event against the union of standard event schemas, so any prefixed event
fails.

**Suggested fix**

In `src/lib/sse-parser.ts`, when an event's `type` contains a `:` (an implementor slug), check only
that `type` is a string and `sequence_number` is an integer, and skip the standard-event union.
Optionally reject unprefixed unknown types, since the specification requires the prefix.

Happy to open a PR with this change and a test.
