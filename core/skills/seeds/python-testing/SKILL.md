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
Follow the active project's verification contract and the operator's scope.
Start with affected tests and direct callers: `python -m pytest <target> -q`.
Reuse completed evidence for an unchanged candidate. A commit, push, deployment,
or broad code change does not itself authorize a full suite. Do not run broad
pytest for Markdown-only changes.

In MO's checkout, `AGENTS.md` owns complete-gate eligibility. For an authorized
direct broad pytest sweep, first run `python -m core.diagnostics.test_preflight
--collect`. For MO's complete gate, run `python -m core.diagnostics.test_suite
--workers 2` once on the stable candidate WITHOUT that separate collection:
the gate owns preflight and parallel/serial collection. Never default to
`-n auto`; other projects use their own declared dependencies and worker bounds.
Follow existing pending test work instead of starting a duplicate suite.

Reproduce a regression before fixing it, then verify the corrected interaction;
a passing mock proves only its modeled boundary. Keep fixtures deterministic
and use pytest-owned temporary paths and isolated `MO_STATE_HOME` outside the
checkout. Remove only state the test created, never shared caches, another
session's scratch, or a live profile.

Report actual results and remaining runtime acceptance separately. A skipped,
failed, or incomplete check is not a pass.
