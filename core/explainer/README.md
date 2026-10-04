# MO Explainer

`mo --explainer guide` prints this reference from any directory.

## Authoring

Reuse authorized media or one coherent set of isolated transparent elements
(`edit_image` edits them). With `generate_image`, make only
reusable assets or an element sheet, never a pre-composited scene, and inspect it
against the brief before ingesting. Ingest with `add-media --origin mo-generated`.
Accepts PNG/JPEG/WebP and muted MP4/MOV/MKV/WebM (no SVG). Compose one hierarchy
(atmosphere, subject, action/evidence, then trusted overlays), not a wall of
cards; reuse the canonical product mark as media.

```text
mo --explainer init --title "<title>" --layout process
mo --explainer add-media "<project>" "<image.png>" --id subject --origin mo-generated
mo --explainer narrate "<project>"
mo --explainer check "<project>"
mo --explainer sheet "<project>"
mo --explainer render "<project>"
```

Layouts (`explanation`, `process`, `comparison`, `product-demo`) are editable
starters; `init` preserves projects. `project.json` owns composition, narration and
timing. Optional `brief`: purpose (`explain`, `introduce`, `promote`, `story`),
audience, language, tone, call_to_action, target_duration_seconds (5-600).
`add-media` origins: `user`, `captured`, `mo-generated`, `licensed-local`.

Scenes need unique `id`, `kind` (`title`, `concept`, `process`, `comparison`,
`summary`), `duration` (1-60s), `narration`, `elements` (painted back to front).
`factual:false` marks original suggestions; factual claims need `claims` with `text`
and `source_ids` into project `sources`, verified and recorded in `research.md`. Scene
`transition:{"type":"crossfade","duration":0.35}` blends from the previous scene
within the new scene's time (default `cut`), replacing element opacity fades at
scene edges.

### Style and elements

Projects snapshot MO's active skin/four-cube brand; customize with
`style.source:"custom"` (global Settings stay unchanged). `style.layout` alone does
not recompose scenes; `style.skin` does not recolor `theme`. `style.decorations`
booleans `grid`, `title`, `scene_badge`, `timeline` default true. Use JSON numbers;
keep the subtitle lane clear.

All elements take `x`, `y`, `color` (theme name or #rrggbb), optional `start`/`end` in scene seconds, `animation`, `opacity`, `scale`, `rotation`, `blur`, `anchor` top_left/center, and `keyframes`. Per type:
- text: `text`, `width`, `size` (8-300; rendered min 12), `align` left/center/right, `weight` regular/bold.
- box: `width`, `height`, `radius`, `stroke`, `fill`, `fill_color`, `fill_opacity` (0-1, default 52/255), optional `gradient`.
- circle: `radius` around x/y; same fill/stroke and optional gradient fields.
- prism: `width`, `height`, `depth`; optional `fill_color`, `top_color`, `side_color`, `fill_opacity`. A projected three-face volume.
- line/arrow: `x2`, `y2`, `stroke`, optional `curve`; arrows also support `head` triangle/chevron/none and `head_size`.
- bar: `width`, `height`, `value` (0-1), optional `gradient`.
- image/video: `asset_id`, `width`, `height`, `fit` contain/cover, `radius`, `border_width`, `border_color`.
- media motion: `move` below; `zoom`/`zoom_to` (1-4), `pan_x`/`pan_to_x`, `pan_y`/`pan_to_y` (-1 to 1).
- video playback: `trim_start`, `trim_end`, `loop`; `muted:true`. Max 30s.
- callout: `text`, `width`, `height`, `target_x`, `target_y`, `size` (12-72), optional `curve` and `gradient`.

`shadow` and `glow` are optional effect objects with `color`, `blur`, `opacity`;
shadow also accepts `x`/`y`. `keyframes` are at least two absolute scene-time rows
animating `x`, `y`, `scale`, `opacity`, or `rotation` (line, arrow, and bar rows
may also animate `draw`); a row's `ease` (`linear`, `ease_in`, `ease_out`,
`ease_in_out`, `ease_out_back`) shapes the segment arriving at it. Sparse
properties hold their prior value.

Example (retain the asset entry):
```json
{"type":"image","asset_id":"subject","x":80,"y":120,"width":180,"height":120,
 "fit":"contain","animation":"none","move":{"x":640,"y":280,"start":1,"end":3}}
```

`move` owns image/video position, easing to x/y then holding; either axis may be
omitted; start/end are scene seconds inside the element window, start < end. It
excludes `keyframes`. `animation:none` disables entrance/exit presets, not explicit
motion (presets: fade/rise/slide_left/slide_right/scale/draw). `fill:false` is
outline only. No arbitrary paths, grouped transforms, or custom fonts (`system-sans`).

Optional `style.motion.blur_samples` (1-8) and `shutter_angle` (0-360) give
deterministic motion blur; `style.render` controls `supersampling` (1-4; `init`
writes 2 because 1 moves in whole-pixel steps), `bloom`,
`bloom_radius`, `vignette`, `grain`. Both multiply render cost (preview below).

### Verification and delivery

After `narrate`, compare speech_duration and duration in `timings.json`; total
time is 0.2s lead plus sum(max(scene duration, speech_duration + 0.65s)). Optional
project `voice.speed` (0.5-2, default 1) slows speech below 1; shortening text may
not slow it. `check` covers schema, assets, layout, pacing, and raster media
enlarged beyond source pixels, not artistry; skip `validate`. Changed text,
durations or speed need `narrate`. Preview work
over one minute first (`--preview-seconds 20`; `--preview-width 480` is faster,
keeping supersampling and finish); its receipt's `final_estimate.seconds`
approximates the full render. Sample final MP4
action into a task-owned scratch directory (ffmpeg `fps=4,scale=320:-1,tile=4x2` strips), review with `perceive`, remove samples.

Stills/FFprobe cannot establish smooth playback or voice quality; disclose listening
limits (captions are approximately timed). Report the render receipt and
`status <project>` evidence separately from visual strengths and limits.

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
Output dimensions must be even for encoding. Renders of 48 or more frames spread
frames over hidden below-normal-priority worker processes (half the logical
cores, at most 8) that reuse the parent's decoded clips; their frames are
byte-identical to in-process rendering, and each receipt records
`render.frame_workers` and `render.render_seconds`. `status.json` owns phase/progress;
contact sheets, previews and final MP4s are derived, each MP4 with a matching
`.render.json` FFprobe receipt. Contact sheets draw JSON scene midpoints and do
not decode the final video. Deterministic QC checks geometry, captions, source
references and pacing; it does not certify visual quality or factual support.

MO's `shell` tool gives the literal `mo --explainer render|narrate` forms at
least 3600 s instead of its 60 s default; a larger requested `timeout` still
applies. During CLI work, Terminal's existing activity lane shows measured progress,
saved style, available artifacts, and verification with its MO method effect.
Desktop's existing glance shows the current phase and percentage. The normal
CLI result carries the full artifact report; neither surface needs a model
request solely to update progress. Repeated live progress is summarized in the
model's tool result; failure diagnostics and the final receipt remain available.

`narrate` reuses MO's configured installed Piper model. Project `voice.speed`
scales that model's default duration setting through its existing worker;
default-speed Desktop calls are unchanged. It records the effective speed,
narration digest, WAV digest, and measured speech duration in `timings.json`.
The renderer splits captions at sentence ends into balanced chunks of at most
12 words and gives each chunk a share of the measured speech by its length; the
render receipt names that mode, and captions are not word-aligned. A supplied WAV must be non-empty 16-bit
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
