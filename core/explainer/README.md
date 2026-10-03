# MO Explainer

`mo --explainer guide` prints this reference from any directory.

## Authoring

Show recognizable subjects and their action when the brief depends on objects;
typography can suit other briefs. Reuse authorized media, create local artwork,
or use `generate_image` for needed assets within cost/method limits. Generation
is optional; avoid speculative alternatives. Ingest with `add-media --origin
mo-generated`. Accepts PNG/JPEG/WebP and muted MP4/MOV/MKV/WebM, not SVG; export
vector art to a supported image with an available renderer.

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
| All | `x`, `y`, `color` (theme name or #rrggbb), optional `start`/`end` in scene seconds, `animation`. |
| text | `text`, `width`, `size` (8-300; rendered min 12), `align` left/center/right, `weight` regular/bold. |
| box | `width`, `height`, `radius`, `stroke`, `fill`, `fill_color`, `fill_opacity` (0-1, default 52/255). |
| circle | `radius` around x/y; same fill/stroke fields. |
| line/arrow | `x2`, `y2`, `stroke`. |
| bar | `width`, `height`, `value` (0-1). |
| image/video | `asset_id`, `width`, `height`, `fit` contain/cover, `opacity` (0-1). |
| media motion | `move` below; `zoom`/`zoom_to` (1-4), `pan_x`/`pan_to_x`, `pan_y`/`pan_to_y` (-1 to 1). |
| video playback | `trim_start`, `trim_end`, `loop`; `muted:true`. Max 30s. |
| callout | `text`, `width`, `height`, `target_x`, `target_y`, `size` (12-72). |

Example after ingesting `subject` (retain its asset entry):
```json
{"type":"image","asset_id":"subject","x":80,"y":120,"width":180,"height":120,
 "fit":"contain","animation":"none","move":{"x":640,"y":280,"start":1,"end":3}}
```

`move` owns image/video position, easing to x/y then holding. Supply either
axis; omitted axes stay unchanged. start/end use scene seconds, default to the
element window and must fit inside it with start < end. Pan/zoom spans visibility.
`animation:none` disables entrance/exit presets, not explicit media motion;
fade/rise/slide_left/slide_right/scale/draw remain presets. `fill_opacity:1` is
opaque; `fill:false` is outline only. No arbitrary paths, grouped keyframes,
gradients, shadows or custom fonts (only `system-sans`); use media for these.

### Verification and delivery

After `narrate`, compare speech_duration and duration in `timings.json`. Total
time is 0.2s lead plus sum(max(scene duration, speech_duration + 0.65s)). Optional
project `voice.speed` (0.5-2, default 1) slows speech below 1, speeds it above 1.
Shortening text may not slow speech. Adjust pace deliberately from measured
timings; warnings are advisory, not a target for repeated regeneration. Changing
text, durations or speed needs `narrate` to rebind audio. `check` covers schema,
assets, layout and pacing, not artistry. Reuse unchanged checks; skip `validate`.
Inspect `sheet` with `perceive`. For work over one minute, render a short preview
first (`--preview-seconds 20`). Sample final MP4 action/scene changes into a
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
