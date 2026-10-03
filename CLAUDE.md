@AGENTS.md

## Claude Code

This stays a thin Claude bridge. `AGENTS.md` above is the complete shared
repository contract — product boundaries, testing cadence, the ignored
`tests/` overlay rules, verification gates, and graph-orientation routing all
live there. Follow it directly rather than a summary here.

Claude-specific mapping: apply its "targeted edits" rule with this harness's
exact-text Edit tool rather than whole-file rewrites, and satisfy its graph
routing with the `mo-graph` MCP when exposed (`core/graph/structural_graph.py`
owns the persisted graph; never dump it into context).
