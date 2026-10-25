# Changelog

## 0.1.0 (2026-10-25)

First release, targeting the Open Responses specification 2026-04-24.

- 40 rules across transport, stream framing, item lifecycle, response object, errors and tools, each tied to a spec section and requirement level.
- `check` runs seven live scenarios over HTTP; `lint` validates saved SSE streams and JSON offline.
- `mock` serves a spec-correct reference endpoint (including `/responses/compact`) with 41 injectable faults for testing clients.
- The clean mock passes all 10 official HTTP compliance tests; the official suite misses 25 of the 41 faults, all of which this tool detects.
- Found and documented a gap in the official suite: correctly prefixed extension events are rejected.
- Console, JSON, Markdown and shields.io badge output; `--min-grade` and `--fail-on` CI gates; zero runtime dependencies.
