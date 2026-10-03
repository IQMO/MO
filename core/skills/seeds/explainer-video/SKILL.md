---
name: "Explainer video"
description: "Create an original, evidence-backed explainer or product video with MO's native renderer"
triggers:
  - "explainer video"
  - "educational video"
  - "tutorial video"
  - "animated explanation"
  - "motion graphics"
  - "turn this into a video"
  - "make a video explaining"
provenance: "seed"
approval: "shipped"
mastery_uses: 0
mastery_successes: 0
mastery_corrections: 0
---
Use for explanations, presentations and demos when native motion graphics fit.
Preserve a chosen external method. Use `mo --explainer` through `shell`;
discover only missing tools by name.
`mo --explainer guide` supplies authoring fields and review commands when needed.

1. Choose visuals for the brief. Show recognizable subjects and meaningful change
   when the idea depends on objects/actions. Typography can suit other briefs.
2. Run `mo --explainer init --title "<title>" --layout
   <explanation|process|comparison|product-demo>`. Keep this private project as owner.
   `project.json` owns narration, timing and composition. Reuse authorized media,
   create local artwork, or choose `generate_image` for a needed asset within the
   user's cost/method limits. Ingest its returned file with `add-media --origin
   mo-generated`; the guide shows image composition. Generation is optional.
3. Support factual claims with primary sources in `research.md` and scene source
   IDs; verify support. Original suggestions need no invented citations.
4. Keep the saved MO skin/four-cube brand unless the brief differs. Branding does
   not require a grid, title, badge, timeline or identical layouts; project
   `style.decorations` controls them. Customize project theme/style/brand with
   `style.source: custom`; global Settings remain unchanged.
5. Run `narrate` for configured Piper, or supply bound 16-bit PCM WAV and timings.
   Compare measured speech and scene durations; project `voice.speed` adjusts
   pace. Budget content and holds together. Captions are approximate. Run `check`;
   fix errors and assess warnings, not regenerate until none remain. Reuse
   unchanged checks; skip `validate`. Inspect `sheet` with `perceive`.
6. Render after checks; for work over one minute, inspect a short preview first.
   Review decoded final-MP4 samples of action and scene changes with `perceive`
   against the brief, correcting visible mismatches. Image delivery is not a
   review verdict. Remove internal review scratch afterward. Stills/FFprobe do not prove
   smooth playback or voice quality; check audio timing and disclose listening limits.
7. Use the render result's path/hash/duration/audio/status receipt. Call `status`
   only when that evidence is absent or stale. Report technical checks separately
   from visual review or listening. Never call a silent output narrated.
