# MO Everywhere

MO Everywhere is MO Agent's optional cross-device hub, continuity layer, and
native-client API. It lets a trusted Android phone, workstation, terminal, or
MO Desktop continue bounded work through one serving MO runtime.

Desktop's **MO Phone → Add phone** also exposes the existing one-use Android
companion QR, with new/existing-user setup steps. It checks Hub health and the
Desktop coordinator separately from the Live Control host, and uses the same
grant endpoint and private QR image/expiry owner as Desktop's pairing tool.
A reachable Hub does not establish host authorization or Android permissions.

For Android users, this means staying involved while away from the desk: Hub
chat reaches the working MO Agent and its configured coding, research and file
tools; Control starts phone-owned work and scheduled turns; Dashboard exposes
the serving owners' available work/project actions. Authorized live hosts add
manual Desktop, terminal and Files access. The Hub and relevant hosts must stay
running and reachable. See the [Android user guide](../ANDROID.md#what-the-app-provides).

It does not create a second brain, Gateway, taskboard, transcript owner, profile
authority, or renderer theme. Each surface keeps its own conversation by
default. A user may explicitly select a named portable conversation, but the
existing core `SessionManager` remains its only catalog and transcript owner.
The hub routes turns through the existing Agent and Gateway and stores only the
bounded state owned by each feature.

Android also has an explicit **This phone** chat authority that is outside the
Everywhere topology. That mode stores a separate encrypted phone-local
OpenAI-compatible provider profile/API key and bounded visible transcript, then
sends direct Chat Completions requests from the phone. It does not use hub
credentials, does not fall back to Hub, and does not become a second MO runtime.

## Topology

```text
terminal(s) / Desktop / Telegram
        │ bounded continuity events
        ▼
device-local journal ── resident coordinator ── private HTTPS/WSS hub
                                                     │
                           ┌─────────────────────────┴──────────────┐
                           ▼                                        ▼
                    Android client                           trusted device

curated profile files ── separate private Git/SSH lane ── trusted MO device
```

One explicit `device_role: server` machine owns the registry, API, and hub
ledger. That server can be a workstation or VPS. A headless server does not
pretend to own a screen: Desktop and terminal control lanes exist only while
the real host process is connected.

Multiple terminals are first-class. Every process has its own stable identity,
session slot, and continuity thread. Desktop stores one explicit terminal
binding; it never guesses from a global "latest terminal".

## Surface responsibilities

| Surface | What it owns |
| --- | --- |
| Android | Resident cube, compact Dashboard/Chat/Control/Files with explicit portable conversations, background work, file cargo, schedules, terminal workspaces, native Live Control, and separate phone-local direct chat |
| MO Terminal | Its own TUI/session, file-send/status commands, and an optional outbound terminal Live Control host |
| MO Desktop | Its isolated conversation, explicit drop destination, and optional outbound primary-display host |
| Everywhere hub | Pairing, rotating credentials, device authority, job/continuity/transfer ledgers, routing, and in-memory Live Control relay |

Android owns its safe areas, touch behavior, resident lifecycle, and full-screen
control. Shared skin tokens and lifecycle meanings do not merge UI owners.

## Use MO from a local ChatGPT Desktop MCP connection

This optional connection lets ChatGPT Desktop ask your running MO Agent to do
work and retrieve or cancel that request. MO uses its existing provider and
permissions. Your Codex/ChatGPT subscription sign-in inside MO is separate and
does not change. No new provider API key or public server registration is
required for this local connection.

Use an already configured, running Everywhere hub. On its trusted terminal,
create a dedicated **control** pairing with no additional scopes:

```text
mo --mo-helper everywhere pair --capability control
```

Keep the one-use code out of chat. On the machine running ChatGPT Desktop,
redeem it into the separate Agent connection:

```text
mo --mo-helper everywhere join --as-agent-client --hub http://127.0.0.1:8765 --code ONE_USE_CODE --label ChatGPT
mo --mo-helper agent-mcp --print-config
```

The loopback address above is for a hub running on that same machine; use your
configured HTTPS origin for another hub. The Agent pairing uses its own private
credential file and device identity, preserving other MO device pairings.
Pairing grants access to MO Agent work and can cause file or app changes; it
does not grant file-transfer, conversation-sharing, or remote-control scopes.

In ChatGPT Desktop **Settings → MCP servers → Add server**, choose **STDIO**
and use the command and arguments printed by MO. Save and restart the
connection. MO prints configuration; it never edits ChatGPT settings for you.
See [OpenAI's MCP setup guide](https://learn.chatgpt.com/docs/extend/mcp).

The connection exposes three tools:

| Tool | Result |
| --- | --- |
| `mo_request` | Submits your text with a stable request ID and returns its job ID. Retry the same request with the same ID; use a new ID for a new message. |
| `mo_result` | Reads that owned job's current state and visible reply. A reply may be a question or approval request. |
| `mo_cancel` | Requests cooperative cancellation of that owned job; its returned state shows whether cancellation has finished. |

MO keeps one isolated conversation for this paired connection. Forward your
explicit response when MO asks for approval; the connection does not approve
actions automatically or adopt another terminal's waiting taskboard. A job
finishing means its turn ended, not that every requested action succeeded.
The hub must remain running. After a connection restart, the same saved pairing
can read its retained jobs; a hub restart interrupts unfinished work honestly.

Disable the MCP server in ChatGPT to stop new tool calls. Cancel any active job
first if you also want to stop that work. Revoke the dedicated device through
MO's existing Everywhere device controls to withdraw its authority. This local
connection does not publish a ChatGPT directory plugin or enable ChatGPT web
or mobile, and it does not synchronize those applications' conversations.

## Android Phone-Local Provider Boundary

The Android phone-local provider lane is not an Everywhere route. It is a local
Android chat authority selected in the app's Provider settings:

- **Hub** remains the full MO authority. Turns go through the serving Agent and
  Gateway; provider catalog, credentials, OAuth state, tools, profile,
  taskboard, portable conversations, Control, Files, background work, and
  continuity stay on the hub.
- **This phone** stores one HTTPS OpenAI-compatible Chat Completions base URL,
  model, API key, and optional image-input flag under Android Keystore-backed
  no-backup storage. Its visible transcript is encrypted separately, bounded,
  and tied to protocol, normalized base URL, and model.

The two modes never fall back to each other and never mix credentials. Phone
mode accepts only direct OpenAI-compatible chat. Codex Responses/OAuth remains
hub-only because it depends on the workstation's OAuth/session owner. Phone
image input is explicit provider metadata, not a model-name guess: Android may
send up to four JPEG, PNG, or WebP images with an 8 MiB total ceiling only when
the saved phone provider enables image input.

Pairing can happen before or after phone-provider setup. Hub unpairing or hub
credential invalidation clears hub authority only; the independent phone
provider, phone transcript, and phone-mode draft are reset from Android Provider
settings.

## Board and Phone Trackpad boundary

MO's shared Board and Phone Trackpad are not Everywhere payload lanes. Board is
the existing private MO Design artifact rendered by Desktop or a standalone
Board window. The Phone Trackpad, in both the Google Play and Direct Android
builds, reaches Desktop through an authenticated ADB tunnel and does not use the
Everywhere hub. Raw motion, strokes,
pressure, screenshots, and Board scenes never traverse or persist in the hub;
Everywhere pairing is neither required nor sufficient to enable that private
local integration.

When a terminal opens Board, its runtime-only binding identifies that exact
local terminal. A linked drawing follow-up reads the current Board locally;
Accept/Reject returns only the draft decision through the private terminal
queue. Neither path publishes a portable conversation, continuity event,
screenshot, or implementation goal.
Remote Live Control may expose a consented host screen under its existing exact
scopes, but it does not become the Board artifact, semantic, or input owner.

## Dashboard projection

The authenticated Android overview consumes the bounded
`mo-dashboard-projection-v1` user projection. A control principal may also
receive an optional remote-safe operations projection; view-only principals do
not. The projection includes bounded status, metrics, sections, action IDs,
privacy flags, and the MO Cube brand. The hub reuses one short-lived
read-only local snapshot across frequent pushes, then copies only bounded work,
learning, direct work/learning state, graph, runtime, profile, presence, and
Gmail readiness/unread count into the remote projection. Gmail sender, subject,
body, message identifiers, and account identity stay on the serving host.
Outlook Connected Tab has no remote readiness/count projection; authorized Hub
chat can use the serving Agent's browser mail route, with fetched content sent
directly to that live operator response rather than saved Hub job history.
Curated-profile file count is distinct from exact learned
facts, terms, profile-learning events, active behavior rules, pending/confirmed
suggestions, generated learning skills, and workflow candidates. Task titles,
local paths, profile prose, provider/model details, transcripts, prompts,
credentials, and learning text never cross this boundary.

Android validates each optional `user` or `operations`/`android` projection,
Cube brand, text and collection bounds, tones, meters, unique IDs, and every
action's exact ID, kind, target, owner, and confirmation policy before rendering
it in the resident Dashboard tab. An invalid nested perspective is discarded
without discarding the valid overview or the other perspective. A missing or
invalid user projection produces an explicit unavailable Dashboard; Android no
longer synthesizes a second local source of dashboard truth.

Dashboard actions reuse existing owners. Work, Goals, Learning, Schedules,
Profile, Skills, Connections, and Project map use their exact slash commands;
Projects and Servers re-enter the authenticated Gateway turn; Files opens the
existing resident MO Files surface. Android Dashboard does not expose **Talk to
MO** or **Open Control**, because its shared Cube already owns Chat and Control
navigation. MO Files retains revision checks, scope checks, atomic writes, and
file policy; the Dashboard never offers direct mutation of raw memory/learning
stores or MO's system prompt.
Learning proposals and confirmations stay in their established conversation
flow.

## Enable and set up

Everywhere requires a configured serving host. The
[Windows preview](../README.md#installation-availability) includes the existing
Hub controls; installing MO does not enable the Hub or pair a device. Public
standalone server packages and container images are not available yet.

Everywhere is disabled by default. Copy only the needed values from
[`config.example.yaml`](../config.example.yaml) into private
`~/.mo/config.yaml`. Credentials, origins, server paths, device labels, and
private Git remotes belong only in private configuration.

Useful MO commands:

```text
/everywhere status
/everywhere setup
/everywhere reconcile
/everywhere pair android
/everywhere devices
/everywhere trace
/everywhere disable
```

`/everywhere setup` is read-only. It reports hub authority, dependencies,
provider readiness, API/TLS health, registry state, coordinator ownership,
profile divergence, Live Control readiness, and one next action. Setup changes
state only with the explicit documented confirmation form.

The Hub and required Live Control hosts must be running and reachable through
the configured HTTPS/WSS origin. Ordinary health success does not establish
that Live Control sockets or file uploads work. `/everywhere setup` reports
these capabilities separately; retain their actual readiness limits when using
the connected surfaces.

### Pair Android

The supported path is:

1. Establish the one serving hub and verify `/everywhere setup`.
2. Install from Google Play when eligible for the available track. The
   [Android user guide](../ANDROID.md#availability) records closed-test and
   Production availability; GitHub does not supply an Android APK. The app
   may also be configured for phone-local direct chat without pairing, but
   that does not enable Everywhere features.
3. Run `/everywhere pair android` on the hub, scan the one-use QR inside MO
   Everywhere, review the HTTPS origin and exact grant, then confirm.
4. Complete Android's resident notification/overlay permission flow.
5. Pair additional exact-scope Live Control hosts only if wanted.

An already-paired coordinator Desktop/workstation with exact `notify`,
`continuity_read`, and `continuity_sync` scopes may request the same fixed
five-minute Android grant from the hub. It cannot initialize another registry
or broaden the grant. Android stores only the accepted hub device credential
behind Keystore protection; no provider key enters the APK or QR.

Repeat the one-use QR flow for each additional phone. Each Android registration
has its own identity and credentials; adding another does not replace or revoke
the existing phone. A coordinator can read active Android registration metadata
from `GET /api/mo/devices/android` with the same exact authority required to
request the QR. It returns only identities, labels, capabilities, scopes and
last-seen timestamps, excluding revoked and unknown-kind registrations.
This is a registration inventory, not proof of unique physical phones, a live
phone host or Android permission grants. Re-pairing the same phone may leave an
older identity until the hub administrator revokes it.

Desktop Phone's **Phones & access** drawer and MO's existing
`everywhere_readiness` tool (`view=phones`) reuse that inventory. The tool reads
the canonical registry directly on the serving Hub and uses the authenticated
client on a workstation. Phone-control tools continue to route only to the
authenticated Android device that originates the MO turn; Desktop's local ADB
selector does not change that authority.

See [the Android user guide](../ANDROID.md) for app behavior and availability.
Android client source, signing, and release work remain in private local custody.

### Pair another coordinator or Live Control host

In an installed Windows build, the existing Everywhere administration command
is `mo --mo-helper everywhere`. It uses the same private configuration and
device registry as MO. Create a least-authority one-time code on the trusted
hub, then redeem it on the intended device:

```powershell
# trusted hub
mo --mo-helper everywhere pair --capability notify --scope continuity_read --scope continuity_sync

# trusted workstation
mo --mo-helper everywhere join --hub https://your-private-origin --code ONE_TIME_CODE --label "My workstation"
```

A terminal or Desktop host uses a separate credential slot and exact
`remote_host` scope:

The host needs an outbound WebSocket transport supplied by its installation.
A headless host does not need the Desktop interface. Installing the application
does not enable a listener or grant control. A public packaged server
distribution is not yet available; see [installation availability](../README.md#installation-availability).

```powershell
# trusted hub
mo --mo-helper everywhere pair --capability notify --scope remote_host

# actual terminal/Desktop host
mo --mo-helper everywhere join --as-live-host --hub https://your-private-origin --code ONE_TIME_CODE --label "My MO host"
```

Enable both `consistent_everywhere.live_control.enabled` on the hub and
`consistent_everywhere.live_control.host.enabled` on the intended host. A
running host detects the newly paired credential without restart.

## Authority model

Capabilities are ordered ceilings: `notify < view < control`. Exact scopes are
independent grants:

- `attachment_upload`
- `conversation_read`
- `conversation_write`
- `continuity_read`
- `continuity_sync`
- `file_browse`
- `file_manage`
- `file_transfer`
- `remote_control`
- `remote_host`

`control` does not imply an exact scope. Every HTTP and WebSocket operation
rechecks its own requirement. Access tokens are short-lived, refresh tokens
rotate under a process-wide mutex, server token material is hashed, and device
revocation wins over refresh recovery. An invalid or expired refresh lineage
returns HTTP 401, allowing clients to destroy the rejected local credential and
return to explicit pairing instead of replaying it.

The default Android QR grants `control` plus the fixed companion scopes, but it
does not grant `remote_host`. Origin-phone semantic control requires a separate
trusted-terminal upgrade, exact `remote_control` and `remote_host`, the
default-off hub gate, and Android's explicit local consent.

Existing paired devices never gain a newly introduced exact scope silently.
Add `conversation_read`, `conversation_write`, `file_browse`, `file_manage`,
and/or `file_transfer` through an explicit hub-side grant replacement or
re-pair the device and review the replacement grant before using portable
conversations, MO Files, or cargo.

On the trusted hub, inspect the current grant first and preserve every scope
that should remain when replacing it:

```powershell
mo --mo-helper everywhere devices
mo --mo-helper everywhere grant-scopes DEVICE_ID attachment_upload conversation_read conversation_write continuity_read continuity_sync file_browse file_manage file_transfer remote_control
```

The overview's renderer-neutral `CubePresentationV1` projection is UI guidance,
not authorization. Clients intersect its advertised actions with locally
derived authority; the server still authenticates the eventual operation.
Unknown or malformed cube data is discarded without granting an action.

### Device pairing and Android purchase

Pairing authorizes a device to use your private MO runtime. Device IDs, pairing
codes and access tokens identify that device and its granted scopes; they do
not verify an Android purchase or create a shared account across MO users.

Google Play manages the Android app's purchase and availability. Pair the phone
with your own Hub separately. Buying the app does not supply Hub credentials,
pair with another person's runtime or grant computer-control permissions.
See [Android availability and pairing](../ANDROID.md#availability).

Hub access uses explicitly authorized native-client credentials. Its device
registry is separate from website accounts and provider sign-in.

## Continuity, portable conversations, and profile sync

Everywhere has three independent durable payload lanes. The portable
conversation handoff ledger is a bounded durable control plane over one of
them; it stores no transcript. Live Control is a separate memory-only relay and
moves none of them.

| Lane | Payload | Store/transport | Conflict behavior |
| --- | --- | --- | --- |
| Conversation continuity | Lifecycle, bounded intent/outcome/next step, opaque repository evidence | Local SQLite journal → authenticated hub SQLite ledger | TTL and row bounds; newest event per selected thread; ambiguity fails closed |
| Portable named conversation | One explicitly selected saved conversation; bounded user/assistant visible text for clients | Existing core saved-conversation store through authenticated hub routes | Positive optimistic revision; stale mutation or turn returns conflict |
| Portable conversation handoff | Opaque conversation/request IDs, expected revision, exact source/target principals, mode, bounded lifecycle/reason, and expiry; no transcript | Everywhere hub SQLite registry | Participant isolation, 10-minute expiry, exact target acknowledgements, and legal monotonic transitions |
| Curated profile sync | The six canonical profile Markdown files, `skills/`, `skin`, `skins.json`, and `preferences.json` | Separate private bare Git directory over SSH | Fast-forward and exact-tree validation; same-path conflicts stop |

Continuity never carries transcripts, messages, tool calls/results, screenshots,
reasoning traces, credentials, raw private paths, or taskboard mutations. A
receiving surface treats an event as dated orientation and re-verifies live
files, runtime, and task truth.

Portable conversations are opt-in. Save a local terminal conversation, then
run `/session share <name>`; `/session unshare <name>` removes device access
while retaining the local saved conversation. An Android client with `control`
capability plus exact `conversation_read` and `conversation_write`
scopes may instead create a new portable name. Clients never auto-select the
first result and do not list unshared or automatic session slots.

The client projection contains at most 100 user/assistant text messages and
120,000 characters, with 20,000 characters per message. System prompts, tool
calls/results, media, reasoning, taskboards, and continuity events are omitted.
The durable named session itself uses the normal saved-session retention owner;
only its client projection is bounded. Every turn or mutation supplies the
current positive revision, and a stale writer receives `409` and must reload.
Authority and revocation are revalidated before provider work and persistence.
**Keep local** unshares without deleting the saved transcript; **Delete**
permanently removes that named session.

The native Android adapter can offer the selected portable conversation to an
exact eligible paired Android device, the serving hub terminal, an exact live
MO Desktop host, or an active authorized Telegram private chat. Android targets
may use confirm or automatic fixed recovery; the hub and Desktop terminal
targets are automatic only. Desktop is listed only while its authenticated
host is live and the source phone has exact `remote_control` authority. The
host accepts only the fixed portable-MO launch operation and waits for the new
terminal to validate the expected revision and persist its ready marker.

Telegram targets are confirm-only and use a persisted random opaque target
key, never a chat ID. The bot sends only the source label and a short handoff
reference; `/handoff accept <ref>` or `/handoff refuse <ref>` applies only to
that exact authorized private chat and current request. Acceptance binds the
chat to the canonical portable session; its first successful normal turn marks
that exact handoff running. The handoff ledger and Telegram notice contain no
transcript. Participant authority, target availability, expected revision,
expiry, and legal state transitions are revalidated throughout. No adapter
accepts a generic program, shell command, path, environment value, or argument
vector from the phone.

One resource-locked coordinator per machine delivers events frequently and
profile changes on a slower independent backoff. Terminals append local events;
they do not create polling workers. `/everywhere reconcile` reports the
one-sided safe union and applies it only after explicit confirmation. Profile
sync remains separate from MO's application files and source repository.

Private state resolves through MO's central state-path owner. The main stores
are:

- `memory/surfaces/everywhere-device.sqlite` — local event journal and bindings
- `memory/surfaces/everywhere.sqlite` — hub registry, event/job ledgers, audit
- `memory/sessions/conversations/` — core-owned local and explicitly portable named sessions
- `memory/transfers.sqlite` — transfer ledger and sender retry outbox
- `memory/transfers/` — bounded hub-owned verified chunk spool
- `credentials/everywhere.json` — rotating device credential
- `credentials/everywhere-transfer.json` — separate least-privilege workstation cargo credential
- `credentials/everywhere-files.json` — separate least-privilege workstation controller credential for MO Files and MO-host workspace panes
- `credentials/everywhere-live-host.json` — separate Live Control host credential
- `sync/repo.git` — curated-profile Git metadata
- `run/everywhere.disabled` — immediate local Everywhere gate
- `run/everywhere-remote.disabled` — immediate remote-control gate

All are under the selected private MO state home, never the repository.

## File cargo and turn attachments

Turn attachments remain bounded conversation context and keep their existing
20 MiB upload contract. They are catalog-only and complete through that
existing turn intake even when cargo auto-accept is off. File cargo is a
separate purpose over one resumable transfer spine shared by Terminal, Desktop,
Telegram, and Android; it does not create another attachment catalog.

Cargo is disabled by default and requires `control` plus exact
`file_transfer`. A create request binds one stable sender and target device ID,
name, size, whole-file digest, destination, and idempotency key. Chunks are
individually verified, resumable, and bounded by configuration. The sender
keeps the source until hub completion; the hub keeps its spool until the target
accepts, verifies, records the categorized catalog entry or named destination,
and sends a receipt. Labels never own delivery, and ambiguity fails closed.

Cargo `auto_accept` applies only to categorized-catalog cargo. Named paths
require explicit acceptance unless the separate `auto_accept_named_paths`
policy is enabled; either way, the receiving Python surface resolves them
inside its own current allowed filesystem roots. Sender-side path hints grant
no filesystem authority. Audit rows contain only opaque transfer/chunk
identifiers, never file names or paths. Native Android and Python receivers
retain verified local partials and resume at their next complete chunk. If a
named destination was fully installed but receipt confirmation was interrupted,
retry confirms that verified prior custody before honoring a newly selected
destination and never deletes a non-empty prior result.

## MO Files

MO Files is a bounded organization view over existing state and transfer
custody, not another storage server. Native Desktop and Android use the same
`core.files.FileManagerService` boundary. The source list starts with
the serving hub and adds each currently connected Desktop or terminal host
that advertises `files_v1`; Android adds its own separately consented phone
source locally. A source selection is an opaque current host identity, never a
presentation label. Public requests carry that explicit `source_id`, an opaque
`location_id`, and a normalized relative path; responses never expose an
absolute state, project, personal, or drive root.

Native Desktop and Android may render two independent browse panes side by
side with a vertical divider. Each pane owns its source/location/path/search
snapshot, while the established file and transfer services remain the only
mutation/custody owners. A hold-drag between panes is a normal source-local
move only when both panes share the exact source and the destination location
permits it. Cross-source drag is the existing verified send/receipt path and
may preselect only canonical target identities such as the hub or the current
paired device; a browse host ID or label is never guessed into a transfer ID.

`file_browse` allows location/list/text-read and bounded native-preview
operations. `file_manage` is an
independent mutation grant and never works without `control` plus
`file_browse`; send also requires `file_transfer`. MO home hides its transfer
catalog index and lock, a consented phone additionally serves the same
bounded contract as a read-only phone source over `files_v1`. That phone
source exposes exactly the one root already owned by its selected-tree or
all-files engine, advertises only list/read, and may omit a file digest only on
non-editable listing metadata; bounded text reads supply the exact UTF-8 byte
count and SHA-256. It never infers an SD-card location or accepts a remote
mutation, preview, Trash, or send request. Project remains subordinate
to the selected host's current filesystem-access policy, and Personal roots
exist only through explicit private configuration. A selected host exposes its
available drive roots only when that host already runs with
`access.mode: full`; project mode continues to expose only its guarded project
roots. Dot/control/secret/key files and symlinks fail closed. Text is bounded
UTF-8, edits and file mutations require the selected SHA-256 revision, writes
are atomic, folder creation is destination-guarded, and file/folder copy or
cross-location move is bounded and verified. Delete writes a private bounded
destination manifest and moves one exact file or folder tree into recoverable
Trash. Restore returns it only to that exact still-configured location/path and
fails on a collision; pre-manifest Trash remains preserved but is not
presented as safely restorable. Permanent recursive deletion is not offered.
Image and PDF preview is read-only and opt-in per advertised location. The
selected source returns only one allowlisted file no larger than 8 MiB, with
its exact MIME class, byte count, SHA-256 digest, and base64 payload. Clients
must revalidate the digest and render from private temporary custody; arbitrary
binaries are metadata-only and preview never executes a file or returns its
machine path.

MO Files uses canonical HTTPS pairing and revocable device authority for
connected MO hosts. Desktop's explicit browser Quick transfer is a separate
five-minute local Wi-Fi session with a random QR bearer URL, exact local folder,
bounded selected files, and no device credential or raw-path API. It opens
only on a private address selected in MO Files and stops with the window or
operator dismissal. It uses unencrypted HTTP and requires a trusted private
network; expiry cannot retract files already transferred or revoke an existing
Hub pairing. It does not grant access to other MO sources. Desktop and terminal
`files_v1` requests share the
authenticated outbound Live Control socket but use an independent bounded
request queue and do not create or consume a screen/terminal control lease.
Native clients retain one bounded plain-text Hub rejection detail when present,
so a failed remote source reports the actual safe protocol cause instead of an
opaque HTTP status; raw, oversized, structured, and control-character response
bodies remain hidden.
Compact clients may multi-select operations, but the server still validates
every item independently. Copy/move stays within the selected source;
cross-source movement is the existing verified send/receipt path to an explicit
paired target.

Incoming/Outgoing activity is the existing transfer ledger and sender outbox.
Clear creates a per-device tombstone for completed hub rows and removes only
finished local outbox rows plus their staged custody. Queued, staging, sending,
offered, claimed, and delivered transfers remain visible and untouched.

Non-hub workstations keep cargo authority in
`credentials/everywhere-transfer.json`, separate from the notify-only
coordinator and Live Control host identities. Create a one-use trusted-hub
grant with exact `control` plus `file_transfer`, then redeem it locally with
`mo --mo-helper everywhere join ... --as-transfer-client`. Tokens stay
opaque, use the platform credential-protection owner, and never belong in
tracked configuration or command output.

Workstation control clients use `credentials/everywhere-files.json`, never the
notify coordinator, cargo, or Live Control host identity. MO Files and split
workspace **MO host** panes reuse this one revocable controller slot rather than
creating parallel credentials. On the trusted hub, issue a one-use controller
grant with the exact scopes needed by the enabled surfaces; on the workstation,
redeem it into the fixed slot:

```powershell
mo --mo-helper everywhere pair --capability control --scope file_browse --scope file_manage
mo --mo-helper everywhere join --hub https://YOUR_HUB --code ONE_TIME_CODE --label "MO Files" --as-files-client
```

Add `--scope remote_control` when this workstation should open MO-host workspace
panes, and add `--scope file_transfer` only when this same controller should send
through the existing cargo owner. Existing paired controllers require an
explicit hub-side scope replacement that preserves their current file scopes;
they never gain `remote_control` silently. Tokens remain opaque and revocable.

A workstation **MO host** pane starts its terminal through the serving hub's
bounded terminal supervisor and then uses the existing `mo_session` host/lease
protocol. Desktop `host_actions_v1` remains a separate actuator for portable
and external-control callers; its presence does not redirect the workspace's
MO-host destination back to the workstation. The transient terminal `host_id`
remains lease authority and is resolved again after transport reconnects. The
hub pins its resolved runtime home, project, extension root, config, and Codex
auth-file path into each new `tmux` session so an older multiplexer environment
cannot redirect the terminal to another profile. These are non-secret routing
paths; the workstation controller credential authorizes start and live control
through the existing exact scopes, and no provider credential crosses that boundary.

The same authenticated terminal endpoint advertises the serving host's start
folder and curated profile project directories. If the Hub starts in MO's own
application directory, that directory is not offered as a project; the start
folder is then the host user's home, listed as **Home**.
Named projects without a local folder remain visible in the local rail but are
excluded from this host launch catalog; recent launch history is not ownership. An optional `project_path` on terminal
start must exactly match that host navigation; client-local paths and matching
names do not establish authority. The supervisor sets both the tmux working
directory and MO project routing to the selected directory and returns it with
the terminal identity. Reusing an existing terminal ID with another project is
rejected. Older callers may omit the selection and get that start folder. The Ctrl+B rail keeps local and host project entries distinct; its host
launch verifies the returned project before leasing the terminal. Existing
terminal instances join their project only when the same host catalog supplies
that association. Attaching a terminal with unknown project evidence uses a separate attached
group and never assigns it the selected local project's identity. The host's
terminal resource cap remains independent of the UI's pane count.

`POST /api/mo/terminals/{terminal_id}/turn` with `{"text": ...}` hands one
normal turn to a running hub terminal (MO Desktop's "on the MO host" handoff).
The hub finds that terminal's live heartbeat (its instance id is the terminal
id) and queues the text through the same heartbeat-proven file handoff a local
Terminal claims, so the terminal runs it as its own next turn. An unknown or
stopped terminal answers 404; one still publishing its heartbeat after start,
or one that already holds a pending handoff, answers 409. The body accepts no
other field, and the same exact `remote_control` scope applies.

Terminal hosts advertise command-menu support in their registration; the
`mo_session` lease response carries that capability to the workspace.
The workspace's slash input and `F4` request bounded host-owned choices; arrows
and Enter navigate them or invoke the host's existing command dispatcher.
`terminal_command` and `terminal_command_result` share the existing authenticated
socket, lease and sequence checks. Replies also match the current request ID;
commands are never replayed after a disconnect. Ordinary text messages remain
separate from explicit command selections. The hub, host terminal and workspace
must all support these messages; an older host shows an unavailable-menu result.

## Turns, background work, terminals, and schedules

Phone turns return `202` with a durable job ID. One active job is accepted per
device; history is bounded, reconnect-safe, and cancelable. Cancellation wakes
the exact in-flight provider request; if provider work still finishes after
cancellation, its reply is discarded and lifecycle remains cancelled.

Each paired device also has one deterministic isolated API session, loaded and
saved around every completed turn. That lets the same phone continue its own
conversation across app reconnects without merging another surface's
transcript. A continuity selection adds only the bounded event described above
to the next provider turn. Clients can explicitly reset that selection to the
device's own thread through `DELETE /api/mo/continuity/bind`.

Native submissions use a bounded per-device `client_request_id`. Retrying the
same ID with the same text and ordered attachment IDs returns the existing job;
changing the fingerprint returns `409`. The raw ID and request contents are not
written to audit logs.

Android can also:

- start and follow its own bounded background workers through the existing
  worker runtime;
- start/list/stop MO terminals on the hub host, where the exact returned
  terminal ID—not a label—owns stop authority. A controller may supply that
  opaque ID before start for idempotent recovery; exact missing-tolerant stop is
  available only to verify cleanup of that already-owned request, and hub
  lifecycle serialization prevents cleanup from overtaking an in-flight start;
- create/list/delete its own timed turns through the existing scheduler;
- group 2–6 connected terminal hosts into one of three phone-local visual
  workspaces without creating another terminal or shell.

A phone-started background worker may carry one optional typed rule:
`worker_finished` or `worker_failed`. The hub persists the once-only rule under
that authenticated device and exact worker ID, records the terminal worker
event through the existing runtime callback, includes up to eight pending
device-owned notices in that principal's overview/event projection, and
requires an exact acknowledgment or revocation. Rows expire and remain bounded.

A phone-created schedule may likewise request `schedule_finished` for every
terminal run or `schedule_failed` for failed runs. The existing scheduler's
canonical run record publishes the result; recurring schedule rules remain
active until the schedule is deleted, the rule expires, or its owner revokes
it. There is no polling-derived task-completion count.

Worker and scheduler events are the only notification owners. Trading and
other Desktop integration domains never reach the phone: the app carries no
trading surface, rule type, or event lane by explicit operator decision.

No arbitrary condition text is evaluated, no notice crosses devices, and no
offline cloud-push transport is claimed. Task-count rules remain unavailable
until a principal-bound task-event owner exists.

Phone-created schedules retain the requesting device's private owner marker.
List/delete cannot expose another phone's records or terminal-owned roles,
scripts, and prompts. API subprocess work is offloaded from the async loop.

Attachments are device-owned opaque IDs. Uploads are bounded to 20 MiB each,
eight per turn, and 50 MiB pending per device; responses never expose private
saved paths.

## Native MO Live Control

MO Live Control is a bounded relay between one connected host and one short
controller lease. The hub keeps host, session, and socket state in memory only.
It is not a remote shell and does not start another agent.

### Terminal lane

A terminal host sends a bounded logical transcript snapshot, activity state,
and optional approval-attention signal. Accepted text and Escape enter that
same running TUI queue. A controller re-resolves the terminal instance after a
host reconnect because the new socket has fresh `host_id` authority. Closing a
phone workspace releases only the phone lease; it does not terminate the remote
MO process.

### Resource observations

An authorized controller may request `/api/mo/live/status?resources=true` for
bounded machine CPU/memory and each native host's own process-tree readings.
The existing `control` plus exact `remote_control` checks still apply. A native
host advertises optional `resources_v1` support; the hub sends a fixed
`resource_request` on that host's existing socket at most once per three seconds.
This is a rate limit, not a polling timer. The terminal workspace requests one
refresh when Ctrl+B opens its panel; a second Ctrl+B hides it. Opening Health
and repainting it do not rediscover terminals or request host samples. The
host's existing request worker measures a one-second CPU interval off the socket/input loop and
returns a sequenced `resource_snapshot` containing only numeric readings and
observation states. No process IDs, paths, command lines, logs or provider credentials cross
this boundary. Memory for a tree is a sum of working sets/RSS, not unique physical
memory. Missing values remain unknown.

These observations neither prepare nor occupy a Live Control session. The status
request waits at most 2.5 seconds for replies after bounded socket sends; missing
replies leave cached readings with their original age. Hosts do not start a
periodic monitoring service. The hub retains only the latest sample for the
current socket and reports its age
using receipt time plus the source's sample age. Reconnection discards the prior
generation; 15-second-old readings are stale in Health. Hosts without the optional
capability remain connected with resource readings unsupported. The public health
endpoint and the default Live Control status response retain their existing scope.

### Desktop lane

A Desktop host sends changed, bounded primary-display JPEG frames at the
configured 1–8 fps. Input requires the correct owner target, bounds, session
sequence, fresh pixel observation, and live one-controller lease. Android
offers touch and virtual-cursor modes over the same protocol.

### Origin-phone lane

A compatible installed Android client can serve an explicitly selected folder through the
existing `phone_files_v1` and `files_v1` lanes. A file-only host needs folder
consent and exact host/controller/file authority, but no Accessibility service,
UI-text consent, or `phone_semantic_enabled` gate. It cannot advertise or execute
semantic/system actions. Play owns this lifetime in its user-enabled resident
service; removing the folder or stopping the service stops sharing.
This requires the corresponding updated Hub: older versions require a semantic
lane for every phone host. Existing pairing grants do not gain `remote_host`
automatically; use the normal reviewed grant update before hosting a folder.

The public Store client does not expose phone automation or privileged tool
lanes. Private owner-device builds are outside public distribution, and their
release evidence remains separate from Store claims.

The separately enabled `phone_semantic_v1` host is available only to a turn
originating from that exact Android device. It supports bounded semantic
observe/click/text/scroll/back/home operations. UI text can leave the device
only after explicit UI-text consent and AccessibilityService enablement.

The same phone may independently advertise:

- `phone_files_v1` with an active selected-folder or separately consented
  all-shared-files grant; operations remain path-bounded and locked-device
  denied.
- `phone_system_v1` with separate privileged-tools consent and an authorized
  Shizuku binder; read-only status is distinct from independently gated system
  changes, destructive package actions, and arbitrary shell.

High-impact operations still require fresh capability evidence, exact
confirmation, target re-resolution, and drift rejection. The hub cannot enable
Android consent, Shizuku authority, root, device owner, or keyguard bypass.

### Wire contract

Prepared screen leases advertise `screen_visibility_supported`. A supporting
controller sends sequenced `screen_visibility` with an exact boolean `visible`
after session readiness and on foreground changes. The existing broker drops
hidden screen metadata and binary frames while retaining the lease/heartbeat;
updated Desktop handlers pause capture and release held input until visibility
returns. Resume forces a fresh frame even for an unchanged display. Legacy
controllers retain their existing behavior, and current Android sends no new
message to a hub without the flag. Terminal lanes reject this screen-only
message. A full host input queue closes the lease rather than losing visibility
and continuing hidden capture.

Lifecycle and input messages are WebSocket text frames. Each screen frame is
one binary message containing a **32-byte ASCII session id** followed by a
bounded JPEG. With the optional `websocket-client` adapter, byte payloads must
use **`send_binary()`**; generic `send(bytes)` selects a text opcode and is
rejected as invalid UTF-8.

An outbound host restart is a new input-worker generation: stop wakes and joins
the prior workers and drains both input/request queues before start accepts new
keyboard, pointer, text, or file requests. A stale shutdown sentinel must never
survive a restart and silently discard controller text.

The access token authenticates a new socket. An established socket continues
past ordinary token expiry only while its fixed identity, capability, exact
scope, and revocation status remain valid. Heartbeat loss, replacement,
revocation, lease/idle expiry, or either kill switch releases held input and
fails pending requests. Host hello and heartbeat sends are required liveness
operations: a failed send closes the half-open connection and enters the
bounded reconnect loop so local status cannot outlive hub presence.

Every supported host hello includes a bounded instance key. The broker combines
it with the authenticated host principal into an opaque reconnect-stable
`host_key` returned by status and session preparation. A new socket always gets
a fresh `host_id`, which remains the only authority accepted by prepare and
relay operations; `host_key` is routing continuity only. Re-registering the same
instance replaces its stale socket and leases. During the rolling upgrade, the
broker recognizes the exact legacy Desktop, terminal-instance, and phone labels
used by supported pre-key hosts; that label bridge can be removed after all
supported host processes have restarted from a key-sending release.

Current native hosts may also send an opaque `machine_key`, derived from MO's
existing stable local device identity, plus allowlisted host kind, platform
family, architecture, version, and process start time. These fields are display
and same-machine grouping hints only: they never replace the transient
`host_id` authority or reconnect `host_key`, and they disclose no hostname,
user, path, IP address, or credential. A managed hub-terminal lease joins its
live row only by exact `instance_id`, never by label.

A Desktop host may advertise `host_actions_v1`. Its fixed operations are
`start_mo_terminal`, `start_portable_mo_terminal`, and `stop_mo_terminal`: one
controller with exact `remote_control` sends an exact host ID and 32-hex
idempotency key. The ordinary operation opens MO's fixed interactive entrypoint
with a fresh instance ID. The portable operation additionally accepts only an
exact conversation ID and positive expected revision, then waits for that
trusted terminal entrypoint to validate the revision and persist the
handoff-ready marker. Stop accepts only an exact instance ID and the Desktop
actuator rejects any process it did not launch and retain; no PID crosses the
wire. No caller-provided executable, arbitrary argument, working directory,
environment value, or shell reaches the host. Results are bounded, memory-only,
audited at the hub, and a new process becomes an ordinary `mo_session` host.

A paired controller with exact `remote_control` may POST one bounded device
presence report (`power_state`, `power_source`, `physical_link`) drawn from a
closed vocabulary. The hub stamps and stores it per device with a short expiry
and mirrors only the validated facts plus an opaque 64-hex `presence_key` to
connected desktop-kind hosts, so the Desktop cube can loop its recharge emote
while a phone verifiably charges over USB. The report and mirror carry no
device id, label, serial, or credential; presence grants no authority, and
consumers apply their own expiry so a stale fact can never keep animating.

## API map

| Route family | Minimum authority |
| --- | --- |
| `/api/mo/health` | Public origin; health reports kill-switch state |
| `/api/mo/pair`, `/api/mo/refresh` | One-use code or rotating refresh token |
| `/api/mo/device` | Current authenticated device |
| `/api/mo/pairing/android` | Exact coordinator credential described above |
| `/api/mo/devices/android` read | The same exact coordinator; active Android registration metadata only |
| `/api/mo/overview`, `/api/mo/provider` read | `view` |
| `/api/mo/presence` | `control` plus exact `remote_control`; one bounded typed power/attachment report, mirrored opaquely to desktop hosts |
| `/api/mo/provider` write | `control`; validated per-device selection only |
| `/api/mo/events` WebSocket | `view` |
| `/api/mo/turn`, `/api/mo/jobs/...` | `control`, device-owned jobs |
| `/api/mo/conversations...` reads | `control` plus exact `conversation_read`; explicitly portable names only |
| `/api/mo/conversations...` create/rename/unshare/delete and portable `/api/mo/turn` | `control` plus exact `conversation_read` and `conversation_write`; positive revision for existing conversations |
| `/api/mo/conversation-handoffs...` | `control` plus exact `conversation_read` and `conversation_write`; exact eligible target, participant isolation, expected positive revision, bounded lifecycle |
| `/api/mo/workers` | `control`, device-owned workers |
| `/api/mo/notification-rules`, `/api/mo/notifications/{id}/ack` | `control`; typed device-owned worker/scheduler rules — list, revocation, and delivery acknowledgement only |
| `/api/mo/terminals`, `/api/mo/schedules` | `control` plus exact `remote_control` |
| `/api/mo/attachments...` | exact `attachment_upload`; mutation also requires `control` |
| `/api/mo/jobs/{job_id}/attachments/{attachment_id}` | `control` plus exact `attachment_upload`; current device must own the job and the attachment must be bound to that exact job |
| `/api/mo/transfers...`, `/api/mo/transfers/history/clear` | `control` plus exact `file_transfer`; participant isolation, stable target IDs, terminal-history-only clear |
| `/api/mo/files/sources`, `/api/mo/files/locations`, `/api/mo/files`, `/api/mo/files/text`, `/api/mo/files/preview` reads | `control` plus exact `file_browse`; explicit source, opaque locations, relative paths only, and an 8 MiB allowlisted image/PDF preview ceiling |
| `/api/mo/files/text`, `/api/mo/files/folders`, `/api/mo/files/rename`, `/api/mo/files/copy`, `/api/mo/files/move`, `/api/mo/files`, `/api/mo/files/trash`, `/api/mo/files/restore` mutation/recovery | `control` plus exact `file_browse` and `file_manage`; source-local revision-checked bounded mutation and manifest-backed recovery |
| `/api/mo/files/send` | `control` plus exact `file_browse` and `file_transfer`; explicit source and target through existing custody |
| `/api/mo/continuity/...` | exact read/sync scopes; binding also requires `control` |
| `/api/mo/android/update...` | `notify`; hash-verified configured artifact only |
| `/api/mo/live/status`, `/api/mo/live/session`, `/api/mo/live/host-actions` | `control` plus exact `remote_control`; host actions additionally require the exact advertised Desktop action lane and fixed operation |
| `/api/mo/live/client/{session_id}` WebSocket | Preparing controller for that lease |
| `/api/mo/live/host` WebSocket | exact `remote_host`; phone lanes add their local/scoped gates |

The API is same-origin, sends no-store/security headers, and fails closed when
the local kill switch is active. Readiness accepts only the current Everywhere
API identity, explicit server role, hub ownership, and active healthy state; a
generic `{"ok": true}` is not proof.

Per-device Hub provider selection stores only validated source/model/thinking
identifiers. Gateway serializes the temporary selection and restores prior
provider state in `finally`, preventing cross-device leakage. Catalog responses
never expose credentials or custom endpoint URLs. Android's phone-local provider
profile is separate app-local state and is not read by, written by, or routed
through these Hub provider APIs.

The authenticated Android update endpoint is retained only for compatible
private local clients; it is not a publication or upload API and gives the hub
no signing authority. Public installation and updates come only from
Google Play under the
[Android availability contract](../ANDROID.md#availability).

## Security and privacy summary

- Keep the hub private behind verified HTTPS/WSS; never expose loopback Uvicorn.
- Pair from a trusted terminal and review exact capability/scopes.
- Revoke a lost device from the hub.
- Portable conversation access is opt-in and revocation-checked; unshared local
  sessions and non-text/tool/system/media content are never projected.
- Hub provider secrets remain in MO's normal secret broker and never enter
  device credentials, QR payloads, continuity, profile Git, or APKs. Android
  phone-local provider keys are separate app-local encrypted no-backup state and
  are never hub credentials.
- Hub registries persist only SHA-256 token digests. Android stores its
  credential in Android Keystore; Windows stores the workstation credential as
  user-bound DPAPI ciphertext.
- Tokens, frames, input, UI text, attachment paths, prompts, and private profile
  prose are excluded from public status and bounded audit records.
- Transfer audit/status never exposes source or saved paths. Verified spool
  bytes expire by policy and are removed after the target receipt.
- Live Control is outbound from real hosts, one-controller, short-lease,
  memory-only, and separately kill-switchable.
- Presence is scoped to this hub and never proves a connected control host.
