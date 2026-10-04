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
Use for explainers, presentations, and product demos when native motion graphics
fit. Preserve a chosen external method. Start native work with
`mo --explainer init` through `shell`;
`mo --explainer guide` owns authoring and review commands; `guide elements`
owns element, style and motion fields.

1. Choose visuals that show recognizable subjects and meaningful change when the
   brief depends on objects or actions.
2. Initialize the fitting layout. `project.json` owns composition, narration, and
   timing. Reuse authorized media or build one coherent transparent element kit;
   use `edit_image` for crops/resizes/conversions and ingest with `add-media`. If
   the chosen method uses `generate_image`, create only needed reusable assets or
   an element sheet—not a full-scene substitute for native composition. Inspect
   generated subjects, count, isolation, and style before cropping or ingesting;
   alpha and dimensions alone are not a visual pass. Reject prompt drift.
3. Support factual claims with verified primary sources in `research.md` and scene
   source IDs. Original suggestions need no invented citations.
4. Keep the saved MO brand unless the brief differs. Compose primitives, media,
   curves, gradients, prisms, depth effects, and sparse keyframes as one scene.
   Establish background, subject, action/evidence, then overlay hierarchy; use
   brand color for meaning and move depth layers at different rates instead of
   filling the frame with repeated cards. Reuse the canonical mark as media.
   Use supersampling and motion blur deliberately; use preview duration/width to
   diagnose quickly while retaining intended quality settings.
5. Run `narrate`; compare measured speech and scene timing. Run `check`, fix errors,
   and assess pacing and source-upscaling warnings. Reuse unchanged checks; inspect
   `sheet` with `perceive`.
6. Render after checks; preview long work first. Review decoded MP4 action samples
   against the brief and correct mismatches. Stills/FFprobe do not prove smooth
   playback or voice quality. Report the render receipt, technical checks, visual
   review, and listening separately. Never call a silent output narrated.
