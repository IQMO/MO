# Python testing

description: How to write and run pytest effectively in this project
triggers: test, pytest, tests, coverage, regression, unit test, failing test, test suite
---
Run tests with `python -m pytest -q`. With `pytest-xdist` installed (in
requirements-dev.txt) `python -m pytest -q -n auto` runs the full suite ~2-3x faster.

Scope first, full suite second: for a focused change, run only the affected test files
or `-k` expression. Reserve the full suite for broad/behavioral changes. Never run the
full suite for docs-only or markdown-only edits — verify those by reading/diffing.

Match verification to the change: if you touched module X, run X's tests and the tests
of its direct callers (use find_callers to discover them) before claiming it works.

When adding a regression test for a fixed bug, make it fail before the fix and pass
after — a test that passes pre-fix proves nothing. Keep it only if it genuinely guards
the behavior.

Prefer deterministic tests: no network, no real time/UUID dependence, no reliance on
external state. Mirror the existing tests' structure and fixtures rather than inventing
a new harness. Assert on behavior and real output, not on internal incidental detail
that will churn.

Report the actual result: paste the pass/fail summary. If a test fails, show the
failure — do not claim green without the evidence.
