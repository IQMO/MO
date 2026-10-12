# MO Settings

Open Settings from the cube launcher or tray. Search its pages to find a control.
Selectors stay inline; moving an appearance slider previews the result, and
releasing it saves the change. Page, scroll position and focused controls survive
live theme changes.

`mo setup` opens the same native window before a provider or Desktop is running.
Choose from MO's existing provider/model/reasoning catalog, or use **Connect
Codex (ChatGPT subscription)** to complete the existing device sign-in in your
browser. Setup has no new API-key or custom-endpoint editor. An existing
configured provider remains available; a new Codex login is stored privately
for MO without replacing another client's auth file.

Setup keeps the selected provider and model visible beside its connection
summary. **Codex session saved** means a session is stored on this device;
completed sign-in is confirmed separately. Neither status verifies a live model
response or silently sends a test request. Reopening setup restores the saved
choice and makes its launch actions available without saving it again. Unsaved
changes remain marked and must be saved before launching with that choice.

Setup also offers Desktop enablement and optional Windows sign-in startup.
Save a changed selection before opening Desktop or Terminal. Saving settings is separate from a
successful first conversation. Closing discards unsaved selections and cancels
pending sign-in; an already completed sign-in remains saved. Android pairing
can be completed later through **MO Phone → Add phone**, once the Hub is ready.

The setup selector saves the default for new Terminals. Normal Settings keeps
its existing target-aware model controls for Desktop, the Terminal default and
running Terminals; its Codex sign-in action changes authentication, not those
model choices.

## Preferences and application

| Page | Working native editor | Scope / boundary | Remaining dedicated or advanced setup |
| --- | --- | --- | --- |
| General | Startup shortcut, Desktop startup/tray, update checks, movement and executable allowlist, cube gestures (each corner's hold), reset/restart | Account startup and Desktop; live setters vs next process are distinct | SystemCare preferences remain in its existing Settings |
| Appearance | Skin gallery and paired previews, custom palette, cube/panel geometry/effects, Terminal display | Shared skin; live Desktop visuals; Terminal on reload | Appearance stays shared across MO |
| Models & providers | Existing provider/model selection and Codex subscription sign-in; independent Desktop choice or follow-provider, saved Terminal default, each observed controllable Terminal, request/context budgets | Validated choices; sign-in saves separately; Desktop changes next request; live Terminal changes require confirmation from that exact instance | API provider creation, endpoints, model catalogs, fallback routes and other credentials retain authored configuration/provider setup |
| Voice & roles | Manual/continuous listening, typed speech, pace/output, spoken-reply provider, roles/default, recognition engine/model/device/beam/idle; Your voice: speaking voice (MO's, your own, a configured voice file), pitch, Arabic voice, voice status, microphone, Record my voice (guided lines and free talk, each clip checked), trainer found, Make my voice (background training, Stop/Continue) | Conversation changes live; a voice choice restarts a running speech worker once; worker configuration on restart; saving does not install an engine | Custom recognition paths, the clone backend and advanced engine arguments remain authored configuration |
| Tools & connections | Connected Chrome; MCP/profile admission; image backend; Telegram policy; Kie Generate key entry, readiness, opt-ins, model defaults and verified helper install | Saved configuration; Generate settings apply next request; install starts no tunnel/job | FFmpeg/FFprobe must already be installed for audio/video; reference sharing currently Windows-only; Email setup remains in Dashboard |
| Projects & checks | Selected project LSP On/Off/Default; server executable/argument editor; global LSP default/timeout; graph enabled, initial build and turn context | Selected project only; command changes on reload; graph choices used on next operation | Dashboard owns read-only recorded checks, rules/knowledge views. Advanced LSP options remain authored configuration |
| Permissions & privacy | Filesystem mode, execution/environment/secret safeguards, web/shell network, capture and pixel policy | Access/sandbox restrictions after reload/restart; no new permission authority | Root/host allowlists and exact provider routing stay authored; consent and grants remain explicit |
| Memory & learning | Actual promotion/materialization/capture controls, skills/admission/matching/decay, semantic recall/backend/worker | Changes apply after reload/restart | Learning review, import and Undo remain in Dashboard; profile content uses existing profile editors; embedding endpoint/model setup stays authored |
| Automation | Scheduler enabled/interval; worker capacity | Service configuration after reload/restart; configured does not mean running | Jobs, pause/resume/cancel and run evidence stay in Dashboard Life |
| Devices | Presence/interval, Everywhere role, continuity/profile sync, Live Control host admission, cargo and browsing policies | After restart/reload; controls do not grant device scopes | Pairing/revocation/trackpad in Phone/Everywhere; transport and path policies remain authored; Android-local preferences stay on Android |

These are the supported native editors, not an exhaustive editor for every
provider, server, credential or transport option. Remaining advanced setup is
identified in the interface and remains available through the configuration-file
action. A saved option does not establish that its service is running.

## Saving and reload

Controls show their actual default and the configuration currently loaded by
Desktop. Missing fields use that default, not an assumed Off. A saved difference
stays marked until the process reloads; each control states whether it applies
live, on the next request, or after reload/restart. Reset removes the override.
A failed save stays visibly unsaved. Explanations beside controls describe their
benefit and relevant cost, privacy, resource or behavior impact.

Closing waits for pending saves before releasing the window. Minimizing keeps
the current view; reopening after closing loads saved settings. Appearance
changes preserve the current page and active app sessions.

General → Cube gestures includes the lower-right single-click folder choice:
the most recently added launcher folder, a particular saved folder, or the usual
cube click. With no saved folder, the usual click applies. Appearance → Cube
offers idle Reach/Square and computer-action Gyro/Square; both apply live.

## Models and running Terminals

A Desktop model save affects its next request, including when a turn is active.
Following the Terminal provider uses that provider's optional `mo_desktop_model`
and lowest supported effort. A saved Terminal default affects new/reloaded
Terminals. Running selections show when they were last observed.

Changing one idle Terminal waits for confirmation from that exact instance.
Queued requests are never labelled applied. Busy Terminals refuse the change
without replacing a draft or taking focus. Missing receipts remain unconfirmed;
older instances must reload. The UI waits only for an explicit model request
and stops waiting after a bounded interval.

## Projects and other surfaces

Graph preference values sit below explicit `MO_STRUCTURAL_GRAPH`,
`MO_STRUCTURAL_GRAPH_AUTOBUILD` and `MO_CODE_GRAPH` environment overrides. Settings
shows that environment ownership and disables the corresponding editor.
Initial build is distinct from automatic refresh of an existing graph:
`MO_STRUCTURAL_GRAPH_AUTO_UPDATE` remains the existing environment control.
Opening Settings never builds a graph or starts an idle maintenance loop.

LSP On/Off/Default changes only the selected project's overlay and stops its
clients in the Settings host; other processes read the policy on next use.
Server configuration does not install or start software. Dashboard retains
read-only diagnostic evidence.

Focus dimming and its optional power timer, launcher arrangement, Shell layout,
Files navigation, Design artifacts and Board content remain with those surfaces.
They reuse shared appearance. Session actions, imports, scans, pairing and job execution remain with those
surfaces rather than the Settings pages.
Publisher and private extension setup remain separate.

## Credentials and Generate

Settings summaries never expose stored credential values, device tokens, raw
provider/server configuration or private source content. Unknown or malformed
values are not echoed; replace them with an admitted value.

Generate's provider is in Models & providers: **Generate provider · Kie** offers
private key entry, readiness, creation and reference-sharing opt-ins, music/video
defaults and explicit reference-helper installation. A newly entered key goes
directly to private credential storage, not a conversation or status snapshot.
Only `media.*` changes apply to the next request without reload; already submitted
jobs retain their options. Credit balance is not a quote. Helper installation
starts no tunnel or generation job. See [Generate custody and requirements](../../core/media/README.md).
