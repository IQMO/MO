---
name: "Design evidence and verification"
description: "Evidence-led implementation checks for UI and frontend work"
triggers:
  - "design"
  - "ui"
  - "css"
  - "html"
  - "component"
  - "layout"
  - "style"
  - "animation"
  - "frontend"
  - "responsive"
provenance: "seed"
approval: "shipped"
scope: "*.html *.css *.scss *.tsx *.jsx *.vue *.svelte *.astro"
mastery_uses: 0
mastery_successes: 0
mastery_corrections: 0
---
Treat the selected provider as the primary visual reasoner. Use this skill only to preserve evidence and verify implementation quality; it does not prescribe an MO palette, layout, shape language, or aesthetic.

- Inspect the existing product's source, tokens, components, states, and responsive behavior before editing.
- Preserve verified project visuals and interactions unless the operator explicitly requests a change.
- For new concepts, derive visual direction from the current request and approved operator preferences; project evidence and the current request win.
- Reuse existing components and dependencies before adding another owner or package.
- Cover applicable loading, empty, error, hover, focus, active, and disabled states.
- Preserve keyboard access, focus visibility, touch targets, contrast, and reduced-motion behavior.
- Verify responsive layout and performance with the project's existing checks and rendered evidence when available.
- Treat advisory static findings as caveats, not aesthetic authority.

Quality means a complete, evidence-backed implementation of the requested direction—not conformity to a built-in MO look.
