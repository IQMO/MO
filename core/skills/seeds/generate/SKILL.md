---
name: "Generate"
description: "Generate music, images and videos through the user's configured Kie account in MO's existing composer"
role: "Generate"
triggers:
  - "Suno"
  - "Seedance"
  - "Seedream"
  - "generate a song"
  - "cover this song"
  - "extend this track"
  - "create music"
provenance: "seed"
approval: "shipped"
---

Use the native `media` tool for requested Kie generation. Discover it with
`tool_search` if deferred. `core/media/README.md` is the current product contract.
Keep existing still-image routes and the native explainer renderer available;
honor the operator's chosen method instead of substituting this one.

1. Read `media action=catalog` for readiness and supported models. Desktop Generate
   controls and reference purposes belong to the submitted request, not the
   conversational language model. Respect them. Missing setup belongs in
   Settings → Tools & connections → Generate; never ask for a key in chat.
2. Convert the requested idea into the supported operation and options. Routine
   preparation, progress checks and local saving are MO's work. Do not require
   the user to run servers, commands, manage URLs or repeat settings. Ask only
   for a genuinely missing creative decision, exact parent/variation when
   ambiguous, incompatible inputs, or new spending outside the request.
3. Use only selected/authorized local references. Do not inspect them with a
   model merely to transport them. Their purpose can come from the user's text
   or the composer's reference labels. Never read private prompt/media stores.
   A reference song inspiring a new composition uses `music` non-custom mode;
   a melody-preserving cover uses `cover`. A theme is not literal lyrics: custom
   mode's prompt is sung as lyrics. Non-custom generation also needs a style,
   lyrics or references. Infer an appropriate style only when the brief permits.
4. Explain the external processing boundary before the first reference request.
   Selected prepared copies are temporarily reachable through Cloudflare;
   anyone with a complete live link can fetch them. Kie/downstream providers
   receive the content. MO's cleanup revokes local links, not downstream copies.
   No silent hosted-upload fallback. Original files and saved results are kept.
   Optional sharing admission does not authorize unrelated uploads or jobs.
5. Submit once. Use the returned MO job ID for `wait`, repeating bounded waits
   automatically until locally delivered, a real failure or operator stop.
   `status` and `list` are local recovery views; `wait` resumes the provider
   lookup/download. Never create again after an uncertain submission or a local
   timeout. Stopping local waiting does not cancel the provider or refund credits.
6. Report actual stage and elapsed time. Remaining time is unavailable unless
   the provider supplies usable evidence. `credits` is a timestamped account
   balance, not a price quote. Actual per-task consumption may arrive in status.
   Requested music duration is not a guaranteed length or spending cap; report
   the saved track's actual duration if the provider returns a different length.
7. Preserve each music variation and its returned track ID. Music continuation
   is unavailable when polling returns no track ID; do not derive an ID from a
   URL. Use an exact parent/output and the same model; an explicit continuation
   point must be greater than zero and before the measured track endpoint.
   Video continuation decodes
   the parent's actual final frame: it anchors the NEW first frame, not the new
   last frame. With additional references use reference mode and identify the
   starting-frame reference in the prompt; continuity is then best-effort. Strict
   first/last-frame modes cannot mix with other references. Do not silently
   discard inputs, promise exact motion, switch to Kling, or call segments joined.
8. Provider success is not local delivery. Use validated saved outputs and show
   each result/open location. Technical decoding does not prove creative quality,
   exact motion, seamless audio or a successful listening review.
9. Reference access expires automatically and is revoked on observed terminal
   completion. Cleanup notices identify the exact job and retained outputs.
   `cleanup_review` opens a review, not deletion approval. Originals and saved
   continuation sources are not deleted by reference cleanup. Do not claim
   provider erasure: current Kie docs say generated media is kept 14 days and
   text/metadata logs two months; fetched-input deletion is not verified.

Personal singing-voice enrollment is a later phase. Never send MO's local speech
clone automatically, manufacture a verification recording, or claim that a
reference song plus the user's cloned voice is already verified working.
