---
name: "Design conventions (MO DNA)"
description: "MO's build/design rules R1-R14 — apply when building UI/visual/front-end work"
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
Detect the project's existing design DNA before writing; extend it, do not decorate from a catalog.

- R1 Detect before write: read existing design/build DNA first.
- R2 Tokens over raw: use existing tokens before raw values.
- R3 Scale over arbitrary: use detected spacing/type scale.
- R4 Type discipline: match fonts, weights, line heights.
- R5 State coverage: loading/empty/error/ideal/hover/focus/active/disabled when applicable.
- R6 Motion presets: named/detected durations/easings plus reduced motion.
- R7 No new dependencies: no UI/animation/icon deps unless present or approved.
- R8 Accessibility floor: focus, keyboard, touch size, contrast.
- R9 Responsive by default: mobile/tablet/desktop via existing breakpoints.
- R10 Performance check: avoid heavy DOM/scroll JS; prefer transform/opacity.
- R11 Premium iconography and physics: CSS/SVG/canvas over emoji/checklists.
- R12 Aesthetic direction: purpose, audience, tone, signature idea, stances before composing.
- R13 Anti-generic gate: avoid purple SaaS, stock glass, emoji-primary, hero+cards, timid palettes.
- R14 Alignment/research: use project evidence, an Alignment Map if present, or bounded source evidence.

Quality bar: the smallest complete high-quality slice, not cheap output. Static design warnings are caveats unless a hard safety/scope/dependency boundary is hit.
