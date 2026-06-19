# Skills

Local best-practice packs MO reads **before acting** on a matching task. Each pack is
detail too long or situational for the always-on system prompt; it loads only when a
turn matches its triggers (keeping the prompt lean and quality high where it counts).

Not a marketplace and not a public command — just markdown packs. Add your own here or
under `~/.mo/skills`. Format:

```
# Title
description: one-line summary
triggers: keyword, another phrase, terms
---
<best-practice body>
```

`triggers` are case-insensitive substrings matched against the user's request; a pack
with no triggers is never selected. The most relevant 1–2 packs are injected per turn.
`README.md`/`index.md` are ignored by the loader.
