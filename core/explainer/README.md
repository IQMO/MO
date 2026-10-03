# MO Explainer

`mo --explainer guide` prints this reference from any directory.

## Authoring

Show recognizable subjects and their action when the brief depends on objects;
typography can suit other briefs. Reuse authorized media or create a coherent
set of local artwork. For composited product motion, prefer isolated transparent
elements in one visual system; use `edit_image` to crop, resize, rotate, or
convert them, then let the explainer own composition, depth, and movement. If the
chosen method explicitly uses `generate_image`, generate only the needed reusable
assets or element sheet rather than a pre-composited environment. Ingest with
`add-media --origin mo-generated`. Accepts PNG/JPEG/WebP and muted
MP4/MOV/MKV/WebM, not SVG; export vector art to a supported image with an
available renderer.

Inspect a generated kit against its brief before cropping or ingesting it.
Dimensions, transparency, and file custody prove only that an image arrived;
they do not prove the requested subjects, count, layout, or visual language.
Reject prompt drift instead of salvaging unrelated assets with geometric edits.

Build one visual hierarchy rather than a wall of cards: background atmosphere,
world/subject, action/evidence, then trusted title/caption overlays. Reserve
brand color for meaning, focus, or a decision; vary displacement and blur across
the first three layers for deliberate parallax. Reuse the canonical product
mark as media instead of redrawing it. The native `prism` supplies generic
projected volume for product objects and depth accents, not a replacement logo.
Keep data displays bounded and let position/length carry magnitude before adding
more colors. Static grids, light pools, and contact shadows should be composed
once and moved as layers; do not spend every frame rebuilding visual noise.

```text
mo --explainer init --title "<title>" --layout process
mo --explainer add-media "<project>" "<image.png>" --id subject --origin mo-generated
mo --explainer narrate "<project>"
mo --explainer check "<project>"
mo --explainer sheet "<project>"
mo --explainer render "<project>"
```

Layouts: `explanation`, `process`, `comparison`, `product-demo` are editable
starters. `init` preserves projects. `project.json` owns composition/narration/
timing; `narration.md` is a draft. Optional
`brief` accepts purpose (`explain`, `introduce`, `promote`, `story`), audience,
language, tone, call_to_action, target_duration_seconds (5-600). Origins for
`add-media`: `user`, `captured`, `mo-generated`, `licensed-local`.

Scenes need unique `id`, `kind`, `duration` (1-60s), `narration`, `elements`.
Kinds: `title`, `concept`, `process`, `comparison`, `summary`. Use `factual:false`
for original non-empirical suggestions. Factual claims require `claims` with
`text` and `source_ids` referencing project `sources`; verify support and record
it in `research.md`. Citations remain visible. Elements paint back to front.
Scene `transition:{"type":"crossfade","duration":0.35}` blends from the
previous scene within the new scene's time; default `cut`. Crossfades replace
element opacity fades at scene edges; inset element windows keep their fades.

### Style and elements

Projects snapshot MO's active skin/four-cube brand. Customize theme/style with
`style.source:"custom"`; global Settings stay unchanged.
`style.layout` alone does not recompose scenes; `style.skin` does not recolor
`theme`. Optional `style.decorations` booleans `grid`, `title`, `scene_badge`,
`timeline` default true; any can be false while `style.brand.enabled` stays true.
Use JSON numbers. Keep the saved subtitle lane clear.

| Element | Fields |
| --- | --- |
| All | `x`, `y`, `color` (theme name or #rrggbb), optional `start`/`end` in scene seconds, `animation`, `opacity`, `scale`, `rotation`, `blur`, `anchor` top_left/center, and `keyframes`. |
| text | `text`, `width`, `size` (8-300; rendered min 12), `align` left/center/right, `weight` regular/bold. |
| box | `width`, `height`, `radius`, `stroke`, `fill`, `fill_color`, `fill_opacity` (0-1, default 52/255), optional `gradient`. |
| circle | `radius` around x/y; same fill/stroke and optional gradient fields. |
| prism | `width`, `height`, `depth`; optional `fill_color`, `top_color`, `side_color`, `fill_opacity`. A projected three-face volume. |
| line/arrow | `x2`, `y2`, `stroke`, optional `curve`; arrows also support `head` triangle/chevron/none and `head_size`. |
| bar | `width`, `height`, `value` (0-1), optional `gradient`. |
| image/video | `asset_id`, `width`, `height`, `fit` contain/cover, `radius`, `border_width`, `border_color`. |
| media motion | `move` below; `zoom`/`zoom_to` (1-4), `pan_x`/`pan_to_x`, `pan_y`/`pan_to_y` (-1 to 1). |
| video playback | `trim_start`, `trim_end`, `loop`; `muted:true`. Max 30s. |
| callout | `text`, `width`, `height`, `target_x`, `target_y`, `size` (12-72), optional `curve` and `gradient`. |

`shadow` and `glow` are optional effect objects with `color`, `blur`, and
`opacity`; shadow also accepts `x`/`y` offsets. `keyframes` contain at least two
absolute scene-time rows. Rows may animate `x`, `y`, `scale`, `opacity`, or
`rotation`; line, arrow, and bar rows may also animate `draw`. A row's `ease`
controls the segment arriving at that row: `linear`, `ease_in`, `ease_out`,
`ease_in_out`, or `ease_out_back`. Sparse properties hold their prior value.

Example after ingesting `subject` (retain its asset entry):
```json
{"type":"image","asset_id":"subject","x":80,"y":120,"width":180,"height":120,
 "fit":"contain","animation":"none","move":{"x":640,"y":280,"start":1,"end":3}}
```

`move` owns image/video position, easing to x/y then holding. Supply either
axis; omitted axes stay unchanged. start/end use scene seconds, default to the
element window and must fit inside it with start < end. Pan/zoom spans visibility.
`move` and `keyframes` are mutually exclusive. `animation:none` disables
entrance/exit presets, not explicit motion; fade/rise/slide_left/slide_right/
scale/draw remain presets. `fill_opacity:1` is opaque; `fill:false` is outline
only. There are no arbitrary paths, grouped transforms, or custom fonts (only
`system-sans`); use an ingested element when those are essential.

Optional `style.motion.blur_samples` (1-8) and `shutter_angle` (0-360) provide
deterministic encoded-frame motion blur. Optional `style.render` controls
`supersampling` (1-4), `bloom`, `bloom_radius`, `vignette`, and restrained
`grain`. High supersampling and multiple blur samples multiply render cost. For
diagnosis, shorten the scene or reduce only output dimensions while retaining
the intended quality controls; restore delivery dimensions for the final render.

### Verification and delivery

After `narrate`, compare speech_duration and duration in `timings.json`. Total
time is 0.2s lead plus sum(max(scene duration, speech_duration + 0.65s)). Optional
project `voice.speed` (0.5-2, default 1) slows speech below 1, speeds it above 1.
Shortening text may not slow speech. Adjust pace deliberately from measured
timings; warnings are advisory, not a target for repeated regeneration. `check`
also warns when raster media will be enlarged beyond its recorded source pixels;
replace or reduce that element when edge quality matters. Changing
text, durations or speed needs `narrate` to rebind audio. `check` covers schema,
assets, layout and pacing, not artistry. Reuse unchanged checks; skip `validate`.
Inspect `sheet` with `perceive`. For work over one minute, render a short preview
first (`--preview-seconds 20`). Add `--preview-width 480` for a faster
screen-size-only diagnostic; it preserves the project's supersampling, shutter
samples, and optical finish and cannot replace the final render. Sample final MP4 action/scene changes into a
task-owned scratch directory; remove internal samples after inspection, retaining
requested deliverables. Quote paths:
```text
ffmpeg -v error -ss <seconds> -i "<project>/explainer.mp4" -frames:v 1 "<review-temp>/frame.png"
ffmpeg -v error -ss <start> -i "<project>/explainer.mp4" -vf "fps=4,scale=320:-1,tile=4x2" -frames:v 1 "<review-temp>/strip.png"
```
Review samples with `perceive`: subjects, change, composition, readability.
Correct mismatches; image delivery is not a review verdict. Stills/FFprobe cannot
establish smooth playback or
voice quality. Disclose listening limits; captions are approximately timed.
Use the render receipt's path/hash/duration/audio/status. `status <project>` is
for missing/stale evidence; `status` checks prerequisites. Link the final local
MP4 using its file URI. Report technical verification separately from observed
visual strengths/limits; no blanket visual pass for unmet creative requirements.

## Runtime and verification contract

MO uses the existing optional Pillow computer-use installation, configured Piper
voice runtime, and PATH-resolved FFmpeg/FFprobe. Imports remain lazy. The native
renderer does not call a generative-video service or bundle third-party template
engines; authorized external assets can be ingested explicitly.

Projects default to `~/.mo/media/explainers/<slug>/`. Ingested media is copied
under `media/`, measured and hash-bound; clips are muted because narration owns
audio. Native `add-media` asset IDs are labels, so `--id wallet` is valid; sandbox
checks still protect the actual file operands, including `wallet.dat` and other
credential paths. A trim ends at measured clip duration by default; loops repeat that trim.
Output dimensions must be even for encoding. `status.json` owns phase/progress;
contact sheets, previews and final MP4s are derived, each MP4 with a matching
`.render.json` FFprobe receipt. Contact sheets draw JSON scene midpoints and do
not decode the final video. Deterministic QC checks geometry, captions, source
references and pacing; it does not certify visual quality or factual support.

During CLI work, Terminal's existing activity lane shows measured progress,
saved style, available artifacts, and verification with its MO method effect.
Desktop's existing glance shows the current phase and percentage. The normal
CLI result carries the full artifact report; neither surface needs a model
request solely to update progress. Repeated live progress is summarized in the
model's tool result; failure diagnostics and the final receipt remain available.

`narrate` reuses MO's configured installed Piper model. Project `voice.speed`
scales that model's default duration setting through its existing worker;
default-speed Desktop calls are unchanged. It records the effective speed,
narration digest, WAV digest, measured speech duration, and the honest caption
mode in `timings.json`. Caption chunks are distributed within measured speech;
they are not claimed as word-aligned. A supplied WAV must be non-empty 16-bit
mono/stereo PCM and its timing map must bind both current narration and audio
hashes. Full audio/timeline duration must agree. Rendering without audio produces
a silent MP4 and reports it as silent.

FFmpeg and FFprobe resolve from `PATH`; the explainer neither bundles nor
hardcodes another application's copies. Encoding writes a unique sibling stage.
Only a result with measured frame, geometry, frame-rate, stream, and duration
checks replaces the previous successful video: H.264, with AAC when narrated.
Encoding, validation, cancellation, or handled publication failure preserves the
previous verified video/receipt pair. The new receipt is prepared and promoted
first, then the MP4 is the final commit point; a failed video promotion restores
its prior receipt when possible and retains recovery evidence if restoration
fails. A process crash between replacements can still leave a temporary mismatch.
Status hash-checks the pair and reports any mismatch as unchecked.
