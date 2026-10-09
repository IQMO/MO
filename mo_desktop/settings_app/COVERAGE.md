# Settings coverage and ownership

Settings is the preference interface; domain owners still supply defaults,
effective behavior and state. This map is checked against the
[capability ledger](../../CAPABILITIES.md), [configuration examples](../../config.example.yaml),
[Core maintenance](../../core/MAINTAINING.md), and [Desktop maintenance](../MAINTAINING.md).
Examples are not an exhaustive schema. Configuration is not service health.

## State boundaries

- `core.state.configuration` merges declared authored YAML changes from fresh
  disk under the shared byte lock and atomic writer. Desktop persistence uses
  this same owner. Settings saves deferred changes without applying them to an
  active turn; a reload/restart message remains explicit.
- `core.state.preferences` owns the typed private overlay: Terminal model/display,
  optional Desktop model, graph choices, mail opt-in and per-project LSP.
- `interface.theming` owns shared skins and private custom palettes. Desktop
  geometry/effects come from typed settings and `DesktopVisualState`.
- Device grants, credentials, learning records, jobs and SystemCare plans keep
  their existing domain stores. They are not alternative preference databases.

Native configuration cards read fresh authored values. An absent field means
the displayed runtime default, never an assumed Off. Cards distinguish loaded
current values from saved changes awaiting reload, and explain the practical
benefit and relevant impact. Scalar defaults share the runtime readers' data-only
`core.state.configuration_defaults`; typed and dependent policies use their
domain owners. These are configuration observations, not service-health claims.
Unknown or malformed values are not echoed and can
be replaced only with an admitted value. Snapshots never contain raw provider
or server blocks, stored credential values, device tokens or private source content.
Generate's password field passes a newly entered Kie key directly to the local
credential broker, not to the stdout request transport or MO conversation.

## Coverage by page

| Page | Working native editor | Scope / boundary | Remaining dedicated or advanced setup |
| --- | --- | --- | --- |
| General | Startup shortcut, Desktop startup/tray, update checks, movement and executable allowlist, cube gestures (each corner's hold), reset/restart | Account startup and Desktop; live setters vs next process are distinct | SystemCare preferences remain in its existing Settings |
| Appearance | Skin gallery and paired previews, custom palette, cube/panel geometry/effects, Terminal display | Shared skin; live Desktop visuals; Terminal on reload | No parallel theme store |
| Models & providers | Independent Desktop choice or follow-provider, saved Terminal default, each observed controllable Terminal, request/context budgets | Model catalog validates; Gateway owns Desktop request scope; exact live Terminal handoff and correlated receipt | Provider creation, endpoints, model catalogs, fallback routes and credentials retain authored configuration/canonical setup |
| Voice & roles | Manual/continuous listening, typed speech, pace/output, spoken-reply provider, roles/default, recognition engine/model/device/beam/idle; Your voice: speaking voice (MO's, your own, a configured voice file), pitch, Arabic voice, voice status, microphone, Record my voice (guided lines and free talk, each clip checked) | Conversation changes live; a voice choice restarts a running speech worker once; worker configuration on restart; saving does not install an engine | Custom recognition paths, the clone backend and advanced engine arguments remain authored configuration |
| Tools & connections | Connected Chrome; MCP/profile admission; image backend; Telegram policy; Kie Generate key entry, readiness, opt-ins, model defaults and verified helper install | Existing owners; Generate settings apply next request; install starts no tunnel/job | FFmpeg/FFprobe must already be installed for audio/video; reference sharing currently Windows-only; Email setup remains in Dashboard |
| Projects & checks | Selected project LSP On/Off/Default; server executable/argument editor; global LSP default/timeout; graph enabled, initial build and turn context | LSP manager and project preference writer; command changes on reload; graph choices used on next operation | Dashboard owns read-only recorded checks, rules/knowledge views. Advanced LSP options remain authored configuration |
| Permissions & privacy | Filesystem mode, execution/environment/secret safeguards, web/shell network, capture and pixel policy | Existing access/sandbox gates after reload/restart; no new permission authority | Root/host allowlists and exact provider routing stay authored; consent and grants remain explicit |
| Memory & learning | Actual promotion/materialization/capture controls, skills/admission/matching/decay, semantic recall/backend/worker | Existing owners after reload/restart; no invented `learning.enabled` | Learning review, import and Undo remain in Dashboard; profile content uses existing profile editors; embedding endpoint/model setup stays authored |
| Automation | Scheduler enabled/interval; worker capacity | Service configuration after reload/restart; configured does not mean running | Jobs, pause/resume/cancel and run evidence stay in Dashboard Life |
| Devices | Presence/interval, Everywhere role, continuity/profile sync, Live Control host admission, cargo and browsing policies | Existing readers after restart/reload; controls do not grant device scopes | Pairing/revocation/trackpad in Phone/Everywhere; transport and path policies remain authored; Android-local preferences stay on Android |

This is a native editor for the declared preferences above, not an exhaustive
editor for every provider, server, credential or transport configuration field.
Those remaining setup boundaries are stated explicitly in the interface and
retain the existing configuration-file action. They are not counted as completed
native editors. Adding one requires tracing its reader, normalization, scope and
write/apply boundary first.

## Runtime details

A Desktop model save affects its next request, including when a turn is active.
Following the Terminal provider uses that row's optional `mo_desktop_model` and
lowest supported effort. A saved Terminal default affects new/reloaded Terminals.
Actual running selections come from instance heartbeats and carry observation
age. Live changes require a matching instance, PID, project and session slot,
are rejected while busy, and preserve draft text, focus and saved defaults.
Missing receipts remain unconfirmed; older instances must reload.

Graph preference values sit below explicit `MO_STRUCTURAL_GRAPH`,
`MO_STRUCTURAL_GRAPH_AUTOBUILD` and `MO_CODE_GRAPH` environment overrides. Settings
shows that environment ownership and disables the corresponding editor.
Initial build is distinct from automatic refresh of an existing graph:
`MO_STRUCTURAL_GRAPH_AUTO_UPDATE` remains the existing environment control.
Opening Settings never builds a graph or starts an idle maintenance loop.

LSP On/Off/Default changes only the selected project's overlay and stops its
clients in the Settings host; other processes read the policy on next use.
Server configuration does not install or start software. Dashboard's former
LSP save route and advertised toggle are removed; diagnostic evidence stays there.

Focus dimming and its optional power timer, launcher arrangement, Shell layout,
Files navigation, Design artifacts and Board content remain with those surfaces.
They reuse shared appearance. Session actions, imports, scans, pairing and job
execution are operations, not reasons to add more global settings switches.
Publisher and private extension internals remain outside Desktop authority.

## Acceptance

Verify each changed save through its actual owner and persisted result, including
failure feedback, unrelated-state preservation and reload behavior. Use isolated
state for synthetic checks. Deterministic tests cover protocol/state boundaries;
native checks cover real selectors, keyboard, layout, themes, save/reopen and
existing destinations. Generate the Design artifact from production assets,
label its sample adapter and verify its native rendering. Keep this map and the
local verification overlay aligned with the delivered candidate.
