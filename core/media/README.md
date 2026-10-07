# Native Create through Kie

`media` owns cloud music, image and video jobs. Desktop **Create** is a role in
the existing composer, not a separate application. Terminal uses the same job
records with textual progress and local result paths. Existing `generate_image`
backends and local explainer rendering remain independent.

## Setup and use

1. Settings → Tools & connections → Create setup: save a Kie API key in the
   password field and enable creation. The native broker writes directly to the
   profile's canonical `credentials/providers.env` (`KIE_API_KEY` by default).
   Never paste keys in chat. Key values do not enter the Settings event pipe.
2. For references, read the sharing notice, explicitly install the optional
   reference helper and enable sharing. MO verifies the official Cloudflare
   release's published SHA-256 digest. No account, domain, manually run server
   or upload bucket is needed. Installation/enabling starts no tunnel or job.
3. Choose Create through the composer role switcher. Choose operation/model,
   attach or drop selected files, click reference chips to cycle purpose/remove,
   describe the result and send. Choices are captured at submission, including
   queued requests, and enforced independently of model prose.
4. MO prepares copies, submits once, checks progress and saves results. The ready
   job card keeps image previews or audio/video file cards together with result
   choices. Open Jobs / saved results in Create to revisit a variation, open/play
   it with the system's local viewer, save another copy or prepare a continuation.
   Audio/video cards are not an embedded player or an actual waveform/poster.
   Preparing a continuation does not itself spend credits.

Windows currently owns crash-safe optional reference sharing. Other platforms
can use text-only jobs; reference jobs fail closed. Images use existing Pillow;
audio/video work requires FFmpeg and FFprobe on PATH. Readiness reports missing
tools without silently installing them; missing delivery tools block before
paid submission. Desktop admits eight references per
request, up to 500 MB per file in Create; ordinary attachments retain 20 MB.
Smaller provider limits are validated separately. MO never silently trims,
resizes, changes models, discards references or switches upload hosts.

## Operations

| Operation | Supported contract |
| --- | --- |
| New song | Suno V6, V6_MINI, V6_WILD through Kie. Non-custom ideas also require style, lyrics or references. Custom mode supports literal lyrics/advanced options, not attachments. |
| Cover | One source song; melody-preserving cover, not merely inspiration. |
| Extend track | Exact saved variation, returned track ID and matching model. No invented ID or upload fallback. |
| Image / reference edit | Seedream 4.5 text-to-image or 1–14 image references; Desktop currently admits eight. |
| Video / motion | Seedance 2.0/2.5 with supported subject-image, motion-video and audio references. Exact motion is an acceptance target, not a guarantee. |
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
  MO does not change DNS settings or silently bypass the system resolver.
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
Fetched-input retention and a usable provider deletion API are not verified.
MO cannot offer zero retention. Leave sharing disabled if that is required.

## Progress and recovery

`preparing → submitting → waiting/queuing/generating → downloading → ready`

Ready means locally saved, structurally validated output with a SHA-256 receipt,
not merely provider success. Decoding/metadata validation does not prove creative
quality, listening review or seamless continuity. Progress shows real stage and
elapsed time, with no invented ETA/percentage. Account credits are timestamped,
not a price quote; actual per-job usage is shown only if returned by Kie.

Submission intent precedes the single POST. Uncertainty records
`submission_unknown`; a crash during submission can leave `submitting` with a
warning. Check Kie history before another paid request. Same-turn identical
requests reuse the job; this is not provider-side exactly-once delivery.
`list`/`status` are local. `wait` resumes an existing task's lookup/download,
never submission. The Create skill repeats bounded waits while its turn runs.
After restart, choose Jobs → Resume status or ask MO to resume. There is no
startup polling daemon. Stop halts local waiting, not the provider or billing;
expired reference links are not silently regenerated.

Unified Suno polling can return `resultJson` containing `{code: 200, data: [...]}`
with separate `audio_url` and `id` fields for each variation. The parser preserves
those returned track IDs, as well as the marketplace result-object/URL contracts.
Music without a track ID is saved, but cannot be extended; MO never invents one.
An explicit continuation point must be greater than zero and strictly before
the selected track's measured endpoint.

Music duration is a requested provider setting, not a guaranteed duration or
spending cap. Live generation can return substantially longer tracks. Receipts
retain each downloaded file's actual measured duration and reported job usage.

## Owners and maintenance

`catalog.py` validates operation contracts; `kie.py` owns HTTP; `preparation.py`
owns file-only decoding; `references.py` owns temporary access and helper life;
`jobs.py` owns session custody/recovery/cleanup; `setup.py` owns explicit verified
installation. `tools/media.py` is a thin adapter. Existing Gateway/Agent callbacks,
composer, Settings, credentials, attachment catalog and notice spine are reused.
`core/skills/seeds/create/SKILL.md` owns the shipped MO workflow.

No new SDK, Node dependency, browser automation or general upload service.
Imports do no network, installation, process startup or heavy SDK loading.
Maintainer tests belong to the ignored overlay. Scoped synthetic tests do not
prove live Desktop acceptance, provider output quality or paid generation.

## External contracts

Reviewed 2026-10-07; recheck before changing model contracts:

- [Kie overview/retention](https://docs.kie.ai/), [task details](https://docs.kie.ai/market/common/get-task-detail), [credits](https://docs.kie.ai/common-api/get-account-credits).
- [Suno generation](https://docs.kie.ai/suno-api/generate-music), [cover](https://docs.kie.ai/suno-api/upload-and-cover-audio), [extension](https://docs.kie.ai/suno-api/extend-music), [audio-record callbacks](https://docs.kie.ai/suno-api/generate-music-callbacks).
- [Seedance 2.0](https://docs.kie.ai/market/bytedance/seedance-2), [2.5](https://docs.kie.ai/market/bytedance/seedance-2-5).
- [Seedream text](https://docs.kie.ai/market/seedream/4-5-text-to-image), [edit](https://docs.kie.ai/market/seedream/4-5-edit).
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
