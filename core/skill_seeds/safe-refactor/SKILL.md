---
name: "Safe refactor"
description: "Refactor without changing behavior or losing work"
triggers:
  - "refactor"
  - "rename"
  - "restructure"
  - "extract"
  - "move function"
  - "split file"
  - "clean up"
  - "simplify"
  - "deduplicate"
provenance: "seed"
approval: "shipped"
mastery_uses: 0
mastery_successes: 0
mastery_corrections: 0
---
Refactoring preserves behavior. It is not a redesign and not a feature change.
If you find a bug while refactoring, note it separately; do not hide the fix
inside the refactor.

Use targeted edits over rewriting a whole file. Full rewrites lose unread
changes, waste tokens, and risk truncation. Split large changes into several
small edits.

Before moving or renaming a symbol, map its blast radius with call search and
text search for dynamic references such as `getattr`, monkeypatch targets,
names in config, and prompt references. Update every call site in the same
change.

This codebase's tests monkeypatch by name and patch module-level symbols by
their import path. Preserve public method and symbol names, and their import
locations, unless you update every patch site too.

After refactoring, run the affected tests and callers' tests. A refactor that
changes test results changed behavior; investigate instead of updating tests to
match.

Prefer the smallest change that achieves the goal. Removing real duplication is
good; removing a real feature or guard without proof it is dead is not.
