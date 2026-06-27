---
name: "Python testing"
description: "How to write and run pytest effectively in this project"
triggers:
  - "test"
  - "pytest"
  - "tests"
  - "coverage"
  - "regression"
  - "unit test"
  - "failing test"
  - "test suite"
provenance: "seed"
approval: "shipped"
mastery_uses: 0
mastery_successes: 0
mastery_corrections: 0
---
Run focused tests with `python -m pytest <target> -q`. Before broad/full local
sweeps, run `python -m core.diagnostics.test_preflight --collect` so boundary
guards and collection fail fast before the expensive suite. With `pytest-xdist`
installed in requirements-dev.txt, `python -m pytest -q -n 4 --dist loadfile`
runs the full suite in bounded parallel mode. Do not default to `-n auto`;
it can consume every CPU thread and slow the operator's machine.

Scope first, full suite second: for a focused change, run only the affected test
files or `-k` expression. Reserve the full suite for broad behavioral changes.
Never run the full suite for docs-only or markdown-only edits; verify those by
reading and diffing.

Match verification to the change: if you touched module X, run X's tests and
the tests of its direct callers before claiming it works.

When adding a regression test for a fixed bug, make it fail before the fix and
pass after. A test that passes pre-fix proves nothing.

Prefer deterministic tests: no network, no real time or UUID dependence, and no
reliance on external state. Mirror the existing tests' structure and fixtures.

Report the actual result. If a test fails, show the failure instead of claiming
green without evidence.
