# MO Settings

Settings uses the shared native Desktop WebView host. Open it from the cube
launcher or tray. It replaces the Tk Settings window; selectors stay inline,
slider movement previews live, and release saves through the existing owner.
Page, scroll position and focused controls survive live theme changes.

## Preferences and application

| Page | Native controls | Owner and application |
| --- | --- | --- |
| General | Startup, movement, executable allowlist, updates, reset/restart | Desktop live setters; startup shortcut owner; authored startup options apply on restart |
| Appearance | Skin gallery, Desktop/Terminal samples, custom palettes, cube/panel geometry, effects, Terminal display | `interface.theming`, `DesktopVisualState`, typed Desktop settings; visuals live, Terminal display on reload |
| Models & providers | Desktop choice or follow-provider, saved Terminal default, observed running Terminals and individual model changes, request/context budgets | Model catalog, preference overlay, existing heartbeat and exact terminal handoff; target and apply scope are explicit |
| Voice & roles | Listening, speech, role, pace/output, recognition engine/model/device | Companion voice/role owners; live conversation controls, engine configuration on restart |
| Tools & connections | Chrome bridge lifecycle, MCP admission, profile extensions, image backend, Telegram service/policy | Existing domain owners; authored options on reload/restart; account/server setup retains its dedicated owner |
| Projects & checks | Per-project LSP On/Off/Default, language-server command/arguments, diagnostic defaults, graph preferences | `LspManager`, typed preferences and authored configuration; Dashboard retains read-only checks |
| Permissions & privacy | Access mode, execution safeguards, network, screen capture and Desktop pixel routing | Existing access/sandbox/admission owners; reload/restart, no implicit authorization grant |
| Memory & learning | Promotion, materialization, capture, skill admission/matching/decay, embedding options | Existing learning/skills/embedding readers; reload/restart; records and Undo remain in Dashboard |
| Automation | Scheduler enabled/interval and worker capacity | Existing service configuration; restart/reload; jobs remain in Dashboard Life |
| Devices | Presence, continuity, curated sync, Live Control host and file policies | Existing runtime/device owners; restart/reload; pairing, grants and device-local preferences remain in Phone/Everywhere |

The [coverage map](COVERAGE.md) distinguishes these native editors from remaining
advanced configuration and dedicated operations. A discovered option or link is
not counted as an editable setting.

## State and implementation

`core.state.configuration` owns fresh, locked, surgical YAML updates. The
Desktop scoped writer delegates to it. Declared configuration fields save to disk
without silently changing the current Agent; the page says reload/restart.
Resetting a field removes its override and leaves the runtime default with its
owner. `core.state.preferences` owns typed model, display, graph, mail and
project choices. Skins remain with `interface.theming`. No browser storage,
raw configuration projection or second preference database is used.

Each authored control shows its actual default and the current configuration
loaded in the Desktop process. A saved difference remains marked until that
process reloads; configured policy does not imply a service is running. Scalar
defaults shared with runtime readers live in `core.state.configuration_defaults`;
typed settings and dependency rules stay with their existing domain owners.
Short explanations describe the benefit and relevant cost, privacy, resource or
behavior impact beside each control.

The model page distinguishes Desktop's next-request choice, its last observed
request, saved Terminal defaults and recent running Terminal observations.
Changing one idle Terminal uses the existing exact instance/PID/project/slot
handoff and waits for that instance's correlated heartbeat receipt. A queued
request is never labeled applied. Busy Terminals refuse it without replacing a
draft or taking focus. Older instances require reload before live selection.
The UI polls only while awaiting an explicit model request, with a bounded wait.

`../settings_panel.py` is the adapter. `catalog.py` declares supported controls
and validates bridge input; it does not own runtime defaults. `app.py` reuses
`NativeAppWindow`, Design native visuals, `studio_theme`, `app_controls.css`, and
shared cube/glyph assets. Request, readiness and window actions use the value-free
stdout transport. The separate `save_media_key` password-field method writes
directly through the private credential broker; keys never enter that transport,
snapshots or conversations. Slow operations use the existing pipe reader; live Desktop
changes use its GUI queue. Failed persistence is visibly unsaved.

## Verification and Design

Create setup lives in Tools & connections: private key entry, readiness, creation
and sharing opt-ins, music/video defaults and explicit reference-helper install.
Only `media.*` configuration changes apply to the next request without reload;
submitted provider options are already captured. Credit balance is not a quote.
See [Create custody and requirements](../../core/media/README.md).

Generate the Settings Design artifact from production `assets()` and safe sample
data. The artifact's simulated adapter must say it does not save real settings.
Verify the artifact in native Design; it is presentation evidence, not proof of
runtime persistence or service health.

Scoped checks cover input validation, unrelated-state preservation, concurrent
writers, deferred application, model routing/receipts, LSP client lifecycle and
native launch/theme behavior. Native acceptance additionally exercises inline
selectors, keyboard input, search, minimum-size scrolling, skin selection,
save/reopen and failure feedback. Retire tests for removed Tk widgets and old
Dashboard writers with those paths.
