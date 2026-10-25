# AGENTS.md

Guidance for coding agents working in this repository.

## Project

`open-responses-conformance` checks Open Responses servers against the semantic rules of the
specification (version pinned in `open_responses_conformance/__init__.py` as `SPEC_VERSION`) and
ships a fault-injecting mock server. Zero runtime dependencies, Python 3.9+.

- `sse.py` parses event streams.
- `stream.py` is the stream state machine (framing, lifecycle, delta consistency, final-vs-streamed).
- `objects.py` checks response objects, items and error payloads.
- `rules.py` is the single registry of rules, each tied to a spec section and requirement level.
- `mock.py` is the reference mock. Faults are transforms applied to a correct event plan.
- `live.py` holds the HTTP scenarios; `report.py` and `cli.py` render output and apply gates.

## Commands

- Test: `python -m unittest discover -s tests -t .`
- Demo: `python -m open_responses_conformance demo`
- Regenerate README tables: `python scripts/build_readme.py`
- Compare with the official suite: `python scripts/compare_official.py /path/to/openresponses`

## Rules for changes

- You must keep the package free of runtime dependencies.
- You must keep Python 3.9 compatibility.
- Every new rule needs a spec section and level in `rules.py`, and a mock fault in `FAULTS` that triggers it. `tests/test_mock_matrix.py` fails if any rule is never exercised.
- Every new fault must list the rules that detect it, and the matrix test must stay green.
- The clean mock must keep passing both this tool (grade A) and the official HTTP compliance tests.
- When the specification version changes, update `SPEC_VERSION`, re-check every rule's section, and rerun the comparison script.
- Do not print API keys or request headers in reports or saved transcripts.
- Do not use em dashes in documentation.
