# Safe refactor

description: Refactor without changing behavior or losing work
triggers: refactor, rename, restructure, extract, move function, split file, clean up, simplify, deduplicate
---
Refactoring preserves behavior — it is not a redesign and not a feature change. If you
find a bug while refactoring, note it separately; don't silently "fix" it inside the
refactor where it hides in the diff.

Always edit_file (targeted exact-text replacements) over rewriting a whole file with
write_file — a full rewrite loses unread changes, wastes tokens, and risks truncation.
Split large changes into several small edits.

Before moving or renaming a symbol, map its blast radius with find_callers /
find_callers (and grep for string/dynamic references the call graph can't see, e.g.
getattr, monkeypatch targets in tests, names in config or prompts). Update every call
site in the same change.

This codebase's tests monkeypatch by name and patch module-level symbols by their
import path — preserve public method/symbol names and their import locations unless you
update every patch site too. Keep module-level symbols importable where tests expect
them.

After refactoring, run the affected tests (and callers' tests) and confirm green before
claiming done. A refactor that changes test results changed behavior — investigate
rather than updating the test to match.

Prefer the smallest change that achieves the goal. Removing duplication or a real
workaround is good; removing a real feature or "might-need-later" guard without proof
it is dead is not.
