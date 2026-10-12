# Native Generate

`media` owns cloud music, image and video jobs. Desktop **Generate** is a role in
the existing composer, not a separate application. Terminal uses the same job
records with textual progress and local result paths. Images also use the existing
`core.imagegen` backend selected in Settings: ChatGPT subscription through Codex,
or a configured image API. The `generate_image` tool and local explainer renderer
remain available.

## Setup and use

1. For Kie, Settings → Models & providers → Generate provider · Kie: save a Kie API key in the
   password field and enable creation. The native broker writes directly to the
   profile's canonical `credentials/providers.env` (`KIE_API_KEY` by default).
   Never paste keys in chat. Key values do not enter the Settings event pipe.
   For ChatGPT or an image API, use the existing Image generation settings instead;
   this image route needs no Kie setup or reference helper.
2. For references, read the sharing notice, explicitly install the optional
   reference helper and enable sharing. MO verifies the official Cloudflare
   release's published SHA-256 digest. No account, domain, manually run server
   or upload bucket is needed. Installation/enabling starts no tunnel or job.
3. Choose Generate through the composer role switcher. Beside the role sit the
   kind, the selected source and (for Kie) the credits balance; nothing is reserved under
   Auto, a picked kind's choices take only the rows they need, a progress line
   appears only while a job or Refine reports, and like the normal composer it
   grows with the prompt, gliding to each new line. Pick the kind first
   (Auto, Song, Image or Video; Auto lets the request's words decide); only that
   kind's choices then appear, in order (type, its model: Suno, Seedream or
   Seedance, then duration/resolution/aspect/sound, quality/aspect or vocals).
   The placeholder follows the selected operation. The footer's compact count menu starts
   at **1×** and offers **1, 2 or 4** separate generations. Each spends credits or
   subscription quota; song jobs may return multiple tracks per generation.
   Image's source menu also offers the configured ChatGPT or image API backend,
   with its model and size choices. That existing route accepts text only;
   attached references must be removed or used with a compatible Kie operation.
   Files dropped on the open composer or picked together with its attach button all
   join the request. The sparkle beside the attach button refines the draft
   with MO's own prompt enhancer (the Terminal's Ctrl+E), guided by the Generate
   skill's "Refining a request" rules: same goal and scope, precise for the
   chosen generator; press it again for your own words. Attached files get
   one References pill whose menu sets each file's purpose or removes it; the
   small "!" beside the icons by Send explains privacy and a footer button lists saved
   results. Describe the result
   and send. Choices are captured at submission, including queued requests, and
   enforced independently of model prose.
4. MO prepares copies, submits once, checks progress and saves results. Completed
   outputs from all jobs stay together in the panel's clickable results list.
   Open Jobs / saved results in Generate to revisit a variation, open/play
   it in MO for songs or the system's local viewer for other media, save another
   copy or prepare a continuation. Song rows and their large cards share playback,
   measured equalizers and a seekable timeline.
   Audio/video cards are not an embedded player or an actual waveform/poster.
   Preparing a continuation does not itself spend credits.

Windows currently owns crash-safe optional reference sharing. Other platforms
can use text-only jobs; reference jobs fail closed. Images use existing Pillow;
audio/video work requires FFmpeg and FFprobe on PATH. An MO package containing
the media tools exposes them to its own processes automatically; it does not
change the system PATH. Readiness reports missing
tools without silently installing them; missing delivery tools block before
paid submission. Desktop Generate admits as many references per request as the
largest generator supports, up to
500 MB per file; ordinary attachments retain eight per turn and 20 MB.
Smaller provider limits are checked when files are dropped and again before
any paid step, including reference counts and Seedance reference durations. MO never silently trims,
resizes, changes models, discards references or switches upload hosts.

## Operations

| Operation | Supported contract |
| --- | --- |
| New song | Suno V6, V6_MINI, V6_WILD through Kie. Non-custom ideas also require style, lyrics or references. Custom mode supports literal lyrics/advanced options, not attachments. |
| Cover | One source song; melody-preserving cover, not merely inspiration. |
| Extend track | Exact saved variation, returned track ID and matching model. No invented ID or upload fallback. |
| Image / reference edit | Seedream 4.5 text-to-image or 1–14 image references. |
| Image through ChatGPT / Image API | The configured `core.imagegen` source and model; text-to-image with local PNG custody. No Kie key or Kie enablement required. |
| Video / motion | Seedance 2.0/2.5 with supported subject-image, motion-video and audio references. A sound's purpose is a reference, the voice the subject lip-syncs or the music the motion follows; all go to Seedance as reference audio, and sound needs at least one picture or clip beside it (checked before any paid step). `generate_audio` (Desktop's Sound pill, on by default) asks for the video's own synchronized sound. Exact motion is an acceptance target, not a guarantee. The prompt can name references as [Image1], [Video1] or [Audio1], numbered per kind in the order sent (Desktop's composer chips); Seedance reads them. |
| Continue video | Decode the parent's actual final frame locally. Alone it becomes the NEW first frame; with extra references it becomes the first image reference, explicitly described in the prompt. The latter is best-effort, not strict first-frame continuity. |

Seedance 2.0 supports 4–15 seconds or automatic duration and up to 4K; 2.5
supports 4–30 seconds or automatic with its documented 480p/720p/1080p enum.
First/last-frame mode cannot mix with other references; a last frame alone is
invalid. Incompatible controls stay visible and are rejected, not downgraded.
Personal singing-voice enrollment is a later phase: MO's local speech clone is
not a Suno singing identity. Kling is not a fallback.

## Privacy and custody

- Originals remain untouched. Prepared copies under private `run/media/` remove
  ordinary image/container metadata, not identifying pixels/audio content.
- A Cloudflare Quick Tunnel relays only the narrow loopback reference service.
  Cloudflare, Kie and downstream providers process transmitted content. This is
  not local-only or end-to-end-hidden processing.
- A file has a random 256-bit capability URL, 30-minute lease and bounded fetches.
  Anyone holding the complete live URL can fetch its copy. There is no directory,
  upload, control or arbitrary-file API. GET/HEAD and single byte ranges work;
  request concurrency is bounded.
- Observed provider completion/failure, lease expiry and normal shutdown revoke
  access and remove prepared copies. A Windows job object binds the helper to
  its parent process. A crash stops access but can leave private scratch files;
  MO does not silently sweep shared state after a crash.
- Quick Tunnels have no uptime guarantee. Setup failure submits nothing. A later
  outage preserves the task identity and never causes automatic paid resubmission.
  Failed local DNS resolution is reported without exposing the temporary hostname.
  MO does not change DNS settings or silently bypass the system resolver. Its first
  lookup of the temporary hostname waits about 6 s after the tunnel registers: asked
  earlier, a home router can keep a "does not exist" answer for minutes.
- Reference/result URLs and raw provider payloads do not enter job records or
  model results. Credentials go only to the fixed Kie API origin. Downloads carry
  no API credentials and check policy, redirects and public DNS addresses at the
  connection boundary. The helper uses scrubbed environment/isolated configuration.
- Private requests and task IDs live in `memory/media/jobs/`; outputs live in
  `media/generated/<job-id>/`, with separate numbered files for variations and
  hashes/parent relationships in the private job record. These paths follow the
  active user's private state root (or their explicit development-state override).
  Existing device-sync policy for generated artifacts remains
  unchanged. Job records and reference scratch are never synchronized.
- Cleanup review binds an exact conversation/job/result set to a fresh receipt.
  It revokes references only. Originals and saved results stay available for
  continuation. It does not delete or claim to erase provider copies.

Kie documents **14 days for generated files** and **two months for text/metadata
logs**; its generic task docs also warn result links can expire earlier.
The current public API index documents no purge endpoint. Closing an MO link or
running cleanup cannot delete copies already fetched by Kie or a downstream provider.
Fetched-input retention is not verified. The composer's privacy notice follows
the selected source; ChatGPT and image API requests use that provider's retention
rules, without a promise of provider-side deletion.
MO cannot offer zero retention. Leave sharing disabled if that is required.

## Progress and recovery

`preparing → submitting → waiting/queuing/generating → downloading → ready`

Ready means locally saved, structurally validated output with a SHA-256 receipt,
not merely provider success. Decoding/metadata validation does not prove creative
quality, listening review or seamless continuity. Progress shows real stage and
elapsed time, with no invented ETA/percentage. Account credits are timestamped,
not a price quote; actual per-job usage is shown only if returned by Kie.

Submission intent precedes the provider call. Uncertainty records
`submission_unknown`; a crash during submission can leave `submitting` with a
warning. Check the selected source before another paid request. Same-turn identical
requests reuse the job; this is not provider-side exactly-once delivery.
Multiple generations keep separate job IDs, saved outputs and submission receipts.
A failure, uncertain submission or stop prevents remaining submissions. Retrying
an identical request in the same turn reuses its existing jobs. Image backends
return a locally verified result directly; an interrupted attempt is not retried.
`list`/`status` are local. `wait` resumes an existing task's lookup/download,
never submission. The Generate skill repeats bounded waits while its turn runs.
After restart, choose Jobs → Resume status or ask MO to resume. There is no
startup polling daemon. Stop halts local waiting, not the provider or billing;
expired reference links are not silently regenerated.

Music without a returned track ID is saved, but cannot be extended; MO never
invents one.
An explicit continuation point must be greater than zero and strictly before
the selected track's measured endpoint.

Music duration is a requested provider setting, not a guaranteed duration or
spending cap. Live generation can return substantially longer tracks. Receipts
retain each downloaded file's actual measured duration and reported job usage.

## External contracts

Generation counts and retention reviewed 2026-10-12. Kie's **20 requests per
10 seconds** is a submission rate limit, not 20 images per job. The supported
Seedream/Seedance/Suno payloads have no requested output-count parameter; MO uses
separate jobs. Suno's `variety` controls stylistic variation, not output count.

- [Kie rate limits/retention](https://docs.kie.ai/getting-started), [API index](https://docs.kie.ai/llms.txt), [task details](https://docs.kie.ai/market/common/get-task-detail), [credits](https://docs.kie.ai/common-api/get-account-credits).
- [Suno generation](https://docs.kie.ai/suno-api/generate-music), [cover](https://docs.kie.ai/suno-api/upload-and-cover-audio), [extension](https://docs.kie.ai/suno-api/extend-music), [audio-record callbacks](https://docs.kie.ai/suno-api/generate-music-callbacks).
- [Seedance 2.0](https://docs.kie.ai/market/bytedance/seedance-2), [2.5](https://docs.kie.ai/market/bytedance/seedance-2-5).
- [Seedream text](https://docs.kie.ai/market/seedream/4-5-text-to-image), [edit](https://docs.kie.ai/market/seedream/4-5-edit).
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
