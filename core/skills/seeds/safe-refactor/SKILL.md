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
For consolidation, use code_search's history. Resolve new wording to existing
responsibilities. Retained decisions are revisable: reuse, improve or replace
by current evidence and required outcomes, not age. Preserve behavior, not a
particular implementation.
After checking an owner and consumers, use `project_history action=record`
to retain analysis without scratch files. Inspect by ID for changed evidence;
trace only linked sources. Reuse owner/symbol; use its ID if moved. Add aliases
or source_refs by ID without evidence_paths to preserve original freshness.
The tool schema and `core/MAINTAINING.md`, "History finding inputs", own capture.
Never invent user intent or unsaved-chat IDs. Ask when sources cannot settle intent.

History is orientation, not current verification or permission. Recheck changed
evidence and unknown consumers. `python -m core.graph.history status` reports
coverage; explicitly `build` only when needed, not each session.

Refactoring preserves behavior, not redesigns. Separate bugs from refactors;
remove no real feature or guard without evidence. Prefer targeted, smallest edits.
Use bounded structural graph/caller/neighbour queries, then check source and
dynamic references (`getattr`, monkeypatches, config and prompts). Refresh a
missing/stale graph explicitly; never dump it into context. Map the whole project
only when needed. `structural_graph` is the persisted graph owner; `code_graph`
is its extractor. Episodic recall and human maps have distinct purposes.

Update all callers in the same change. Preserve public names/import locations
unless every monkeypatch site is updated too. After edits, run affected and
caller tests within the authorized scope; investigate failures, do not just
rewrite tests to match.
