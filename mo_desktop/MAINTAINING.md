# MO Desktop Maintenance Contract

This file owns detailed Desktop, Android-companion, and Desktop-role
invariants. Read it before changing `mo_desktop/` or a cross-surface flow that
affects Desktop. The root `AGENTS.md` owns repository-wide working rules;
the public [`ANDROID.md`](../ANDROID.md) owns user-facing Android behavior, while
the private local `clients/android/MAINTAINING.md` owns client source and releases
when that source tree is present.

## Process and lane boundaries

Desktop session snapshots retain shared input/output and cache hit/miss/write
accounting through save and reload. `companion_session` still owns the separate
Desktop slot and its conversation sanitizer; transient tool and provider replay
content is not restored as Desktop conversation memory.

- The connected Dashboard is a separate instance-owned WebView surface documented
  in `core/dashboard/README.md`; it has no Desktop voice UI or embedded terminal.
  Cube launcher **Dashboard** reuses Desktop's resident Agent and the same DashboardServer,
  serializes opens through the GUI lane, and focuses its existing renderer. It
  must not start a terminal, new Agent, or model turn just to display a Dashboard.
  A Desktop host is never labelled a terminal; terminal creation remains an
  explicit Open terminal action. Cube gestures retain `_display_dashboard`.
  Desktop listening feedback uses the original travelling brightness-and-alpha
  ripple, driven by its existing microphone level and animation clock. Preserve
  its continuous alpha motion; an opaque brightness-only substitute becomes
  visibly stepped through the bounded sprite cache. Cube sprites are placed on
  quarter pixels (`_subpixel_sprite`, resampled in premultiplied alpha, cached
  per sprite and offset, cleared with the sprite set), including the window's
  whole-pixel remainder; whole-pixel placement turned the ~3 px idle bob and
  the end of a chase into visible ticks.
  Its optional native host reuses `mo_renderer`; window chrome never becomes a
  second Agent/data bridge. App opens keep the normal launcher collapse back to
  four cubes. Each native host requests `DesktopCube.capture_launch_origin()`
  once its window is created. It paints the current native pixel positions,
  sprites and alpha, and holds that exact pose only until the first published
  entrance frame or a failure receipt. Startup and page loading never hold the
  cubes; an old receipt cannot release a newer pose. The existing launch pipes
  carry source, first-frame and completion messages; Files uses its existing
  command reader. The snapshot includes the source monitor's work area. Windows
  center within that area through `centre_desktop_window`; Shell's native owner
  centers its expanded bounds and retains the pair's initial header alignment.
  Capture coordinates determine motion, never the final window position.
  `mo_renderer` owns the finite purpose frames and reuses
  `NativeLayeredWindow`: Shell's two pieces fall with the shared cube trace into
  its native header pair; Dashboard unfolds four panes, Files opens folder
  layers, Phone forms a handset and scan, Design draws its frame, SystemCare
  forms a shield, and Settings opens
  slider tracks. Purpose forms travel with the real cubes before unfolding
  into the actual window bounds. Departure eases from rest, and the purpose form
  starts at the captured cluster's dimensions before unfolding. Colors and radius come from the active visual
  state and existing app color roles. Thin strokes and corners are antialiased
  locally rather than resampling a full window on every frame. The existing
  native visual controller supplies the finite WinForms clock. Closing early or a failed source
  handoff cleans up the passive layer and reports failure. Focusing an existing
  app does not replay startup. Opens without a visible source use normal reveal.
  Dashboard and Files keep their page invisible until its first ready receipt;
  there is no continuous loading animation or empty-page reveal.
  On Windows, native opacity owns this reveal; the WebView hidden bootstrap
  must not reset it to opaque before MO's first-frame/page-ready boundary.
  WebView2 color-key transparency is not a supported substitute. Native rounded regions,
  edge/effect rendering and move/resize refresh reuse the Design visual controller.
  The native Dashboard projects that same resolved radius and border colour into
  its HTML edge; native GDI painting alone does not survive WebView repaints.
  Its existing state refresh also reloads canonical visual settings and skin;
  changed settings update that native region, outside effect and HTML edge together.
  There is no Dashboard-specific corner preference or additional visual timer.
  Dashboard controls also consume that state's button padding and radius; its
  graph pauses when the view is hidden, and unchanged polling results must not
  replace lists or discard focused/expanded source controls. Direct source
  actions and their boundaries are owned by `core/dashboard/README.md`.
  Compact Desktop tabs use one content-derived
  extent across their current payloads, not independent tab resizes or zero tiles.

- MO Desktop is a separate, opt-in MO-branded companion (`mo_desktop.enabled: true`), summoned with **Win+Alt+M** or launched with `/desktop`. It survives closing the terminal. Keep it fast and request-local: Terminal owns agentic diagnosis, project planning, and engineering orchestration; Desktop directly fulfills ordinary companion requests through the smallest canonical route and must not grow a planner, evaluator, or diagnostic loop.
- Focus hover is cheap by construction: moving between window rows redraws only the rows it
  leaves and enters (`focus_paint.row_hover_patch`: each band is finished with real neighbour
  pixels, so the result equals a full `cube_face` render), and the face's card surface and scaled
  icons are built once. Any other hover or state change renders the whole face. Keep new row
  drawing inside `_draw_row`, which both paths use.
- Focus follows the main character's fullscreen visibility policy. Fullscreen
  suppression fades its expanded or collapsed face and dismisses its previews,
  calendar, and tray popup; it does not disable Focus or discard its position.
  The classifier excludes the exact shell desktop classes and layered,
  click-through cover windows before applying full-primary-screen bounds; genuine
  opaque or interactive fullscreen content still suppresses the character. The
  existing explicit Chase and foreground-compatibility exceptions remain owned
  by the main character policy.
- Desktop lock acquisition is strict and precedes Agent/Gateway/GUI imports: a fresh incomplete lock file is an in-flight claim, never stale state, so concurrent startup/restart callers cannot create two residents or duplicate heavy initialization. On Windows, the lock plus readiness and launch/summon markers use the stable per-user LocalAppData temp directory rather than an ambient `TEMP` or `MO_STATE_HOME` override, so a sandboxed caller and the normal resident cannot split ownership or IPC. Logs, conversations, and other runtime state remain under their resolved private profile. The resident stays console-less, and every captured internal console child reuses `core.runtime.subprocess_flags.apply_windows_hidden_process_flags()` instead of assuming it can inherit an invisible parent console.
- Initial detached launch forwards the canonical active config path, just like restart. Source freshness includes actual dirty content and relevant tools, not just changed path names; failed inspection remains unknown.
- Native MO computer-tool capture/input uses `core.desktop.runtime.native_desktop_scope` and the shared file lock. Serialize validation, input, and recording as one native operation, not whole turns or wait sleeps. Publish cross-process invalidation at the actual input primitive after non-mutating validation; a refused action must not stale a peer's observation. Input that partly executes or crashes still invalidates older observations. This coordinates MO tool owners, not arbitrary operator input.
- Desktop companion actuation keeps the normal four-cube character visible only
  when its native window successfully becomes click-through and capture-excluded;
  otherwise it hides fail-safe. The reply panel yields independently, companion
  tool heartbeat is not a visibility owner, and companion, bound-Terminal, and
  screen-selection yield flags must not release one another. Retain the bound
  Terminal's existing activity cue; do not add a companion-only overlay, arbiter,
  polling loop, or approval step.
  A focus request reports success only when the target's native handle matches
  the observed foreground window. Missing confirmation or a different window
  is an error; the caller must observe the current target before further input.
- Publish GUI readiness before inspecting the checkout revision. Resolve the source stamp off the GUI startup lane and replace the marker atomically, so slow Git never delays first interaction and readers never observe a partially rewritten marker.
- Keep the `mo_desktop` package import light. Launcher/config helpers must not import GUI, tray, voice, or optional `keyboard`, `pystray`, or `pywin32` dependencies until needed.
- The declarative Desktop app catalog and optional tray may launch the separate native MO Shell through
  `CompanionSurface.open_mo_shell()` with the active config. Keep that seam
  lazy: Desktop does not own Shell pixels, attachments, ConPTY, lifecycle, or
  terminal sessions, and Shell does not create a second tray.
  Each explicit Shell launch starts a separate native process and canonical
  terminal instance. Desktop retains only its live process handles; the existing
  running-app rows focus each exact process, and never create another Shell.
- `CompanionTray.item_specs()` is the single Desktop app action catalog. The
  launcher projects every app action. The optional themed tray popup projects
  Settings plus Show/Hide, toggles, Advanced and danger rows; it is not
  a second app launcher. The cube's double-click expands those same four cubes
  from the live cluster's
  center into square app-bearing cubes in the existing cube window, even if the
  optional tray icon is disabled;
  no new launcher window or backing panel is created. The enlarged corners follow
  the cube character setting and their colors follow the cube's resolved skin or
  custom color. The old double-click emote dispatch is gone. Cube hover
  shows only this Desktop instance's live window/process handles and never
  invokes an app. Cached Tk windows in the withdrawn state are closed, while
  iconified windows remain switchable. The running list focuses through the
  same catalog; individual Shell rows carry their exact process handle.
  Focusing an app pulses the existing cube briefly in a color derived from the
  active Desktop skin and restores its normal cube color afterward. A held
  launch source suppresses that competing pulse until its first frame is published.
  Successful entrance completion plays the existing finite `app_heartbeat`
  emote as a small geometric release, counter-twist and return. Thinking reuses
  the same formation motion from the existing emote library and cube clock;
  neither feedback requires another renderer, timer, or brightness flash.
  The launcher expands and collapses on the existing native GUI loop, restoring
  the cube window's original size after the reverse frames. Running-app hover
  reuses `DesktopCube._label_win`, the same layered card and skin metrics used
  by volume and notices; it becomes clickable only while app rows are shown.
  The existing cube reposition moves that painted label without rebuilding its
  pixels every frame. There is no second hover window, background process scan,
  new Agent, or extra idle animation loop. Expanded-launcher hover reuses the
  cube's frame clock and pointer sample: other tiles dim over 160ms using bounded
  per-tile cached levels, app rows use a skin-rounded translucent fill over 100ms,
  and the native face carries a quiet stationary edge plus a softly faded
  four-second edge lap. Expansion/collapse use a 180ms ease-out and
  frame-budgeted scheduling; their geometry must meet the final tile size without
  a last-frame jump. Clear the launcher painter when returning to the cluster.
  The current reply/composer/attachment card yields while the launcher is open;
  content updates cannot reveal it over the cubes. Closing restores that same
  visible card. The settings glyph is part of the Work cube's cached artwork;
  its input bounds and menu anchor follow the cube's actual animated position.
  One gear menu owns Edit, distinct Add file/Add folder actions, Reset layout
  and the existing Appearance entry. Edit animates cached app-row artwork and
  enables movement between tiles. Holding an app enters that same Edit state and
  reveals a cube-colored Remove button; its wiggle also signals that it can move.
  App rows move between tiles in Edit; group headings drag whole tiles outside
  Edit. Holding a heading animates that cube and marks available destinations;
  a brighter inset identifies the current landing slot. Both settle from their actual release positions over 230ms on the cube
  clock. Group order and hidden built-in IDs share the existing layout record;
  reset restores those defaults while retaining added shortcuts.
  The held Remove button updates the existing config writer and dims in its
  cube's pixel pass. The old bin control and drop-removal path are removed.
  The target itself is preserved. There is no removal popup or confirmation.
  Folder dropdown reveal and dismissal fade the complete card with a small
  translation using the shared panel duration and cube easing on the existing
  frame clock. Navigation retains the card while its directory loads, changes
  content without replaying opening, and publishes rows before native icons.
  Inputs follow the visible card; hover blends the skin's card/input
  colors without punching transparency through the menu. Navigation and removal
  glyphs come from the shared anti-aliased icon factory.
  Add file and Add folder open their respective native Windows pickers;
  cancel leaves the layout unchanged. Shortcuts use Windows file/folder icons,
  and the existing Desktop configuration writer saves IDs and ordering under
  `mo_desktop.launcher.layout`. Removing a shortcut never removes its target.
  Each group scrolls independently. Folder dropdowns load only the current
  directory off the GUI lane, retain every entry, and provide scrolling,
  keyboard navigation and filtering; ten visible rows are a viewport, not a
  content limit. Menus reuse the shared card's panel radius, typography and
  supersampling, with button radius on rows. Initial loading paints immediately;
  navigation retains the last card and stale worker results cannot replace it.
  Ten seconds of input inactivity triggers the existing finite shake emote;
  at fifteen seconds the launcher closes. Pointer movement, keyboard, dragging
  and the active file picker reset idle time. No additional timer is created.
- A stationary two-second left-button hold uses one timer and accent ramp in
  `cube_interaction.py`: lower-left invokes capture, lower-right invokes the
  resident tray's Focus toggle. Release after a successful hold never dispatches
  a second click. Movement, release, launcher ownership or computer use cancels
  the gesture. There is no separate Focus hold timer.
- The existing `ReplyBubble` input card expands upward from the upper-right
  cube and suppresses only that sprite. It retains draft, search, history,
  clipboard, keyboard and accessibility behavior. Its original finite transition
  clock blends the actual cube source into the card; its cube control reverses
  that reveal. `cube_geometry` clamps both right-hand faces as one group. The
  launcher captures this actual upper face before it yields, then restores it.
  Reply, history and compact Dashboard remain owned by the same card. Composer role choices form a compact opaque dropdown inside that card, using the current profile catalog and conversation role owner. Selected Google/YouTube/Translate borders use the shared brand palette and stationary card edge; the default uses the active theme. There is no gradient animation clock. `card.py` reuses stationary edge artwork by exact canvas geometry, stroke and palette, with at most eight entries and 8 MiB of retained RGBA pixels; larger edges render without retention. Content edits must not rebuild an unchanged edge.
  Expanded Focus takes its total width from the same composer design and active
  panel padding. Pinned apps occupy a dimmed, borderless bottom strip inside
  that width, above the time/settings/tray row. Search's clear control appears
  only for nonempty input and reuses the native EDIT's clear operation.
  Both right-hand faces align; attachment cards align to their edges,
  excluding the left cube sprites from centering.
- Focus is a session-only expansion of the resident cube's lower-right face,
  owned by the tray and existing cube clock. The other three sprites stay with
  the same cube renderer. `DesktopCube.launch_piece` supplies actual source pixels
  for composer, Focus, launcher, and app entrances, including expanded right faces.
  Focus owns one native input/pixel projection for that face and suppresses only
  the original fourth sprite. It adds no tray owner, app row, Agent, scanner,
  independent timer, or screenshot cache. The old edge dock and generic Focus
  app-entrance path are removed.
  Dragging the single cube control moves the group freely and clamps its full allocation to the
  monitor. Native presses are queued to the GUI lane; anchor drag coordinates to
  the original press, never the later cursor position. Single-click folds
  the list into a 40px lower-right cube showing time and total window count; double-click exits through the
  tray owner. The single action waits for Windows' double-click interval.
  The folded face reuses `DesktopCube._render_sprite_set` and the projected
  fourth cube state: the same skin, corners, halo, brightness and animated pose.
  Its 32px visible cube fits inside the 40px input surface; no separate panel
  shadow is painted. The existing trail follows that visible footprint, omits
  expanded faces, and clears old footprints when folding changes the shape.
  Expanded Focus freezes ambient movement; dragging moves both projections
  together. Normal cube composer, Dashboard, launcher, guidance, and drop owners remain.
  Composer and popovers choose available space around the actual face. Hover
  previews additionally avoid the visible composer and align their top and bottom
  with the combined expanded faces. The face yields to the
  launcher's surface and restores when it closes. Closing Focus keeps the freely
  selected position and restores normal motion fields.
  `card.py` owns supersampling and fonts; `focus_paint.py` paints the face, hover,
  and calendar with the active `DesktopVisualState`. `NativeLayeredWindow`
  supplies alpha surfaces and named Windows buttons. Native callbacks post to
  the existing GUI queue and never dispatch GUI work inside a native input callback. Computer use yields
  input immediately, independently of the visual fade.
  DWM cannot compose into a per-pixel bitmap: only the hovered `WindowPreview`
  owns a redirected click-through HWND. Closing or switching the hover releases
  that registration and host; settled previews need no active-rate clock.
  Icons reuse the borrowed-HICON rasterizer. Enumeration and verified activation
  reuse Phone Trackpad's Windows owner; failures stay on the relevant control.
  Window rows and hover previews never take foreground activation; clicking an
  active row minimizes through the same verified switcher owner.
  Closing restores and activates that exact target before posting its normal
  close message, so inline save prompts remain visible even for minimized apps.
  Classic Explorer notification areas share one on-demand Focus flyout: the
  native adapter reads bounded current toolbar records and borrowed HICONs,
  deduplicates promoted/hidden items, and invokes revalidated Explorer buttons.
  Remote toolbar buffers and process handles are released in the same call.
  XAML shells retain their original native flyout as a distinct platform adapter.
  Never move, crop or reparent the taskbar. Place the flyout below its actual
  button when room permits, then use the shared monitor/avoidance geometry.
  MO remains an ordinary native tray item. Closing Focus releases its native resources once.
  The Windows header owns a native EDIT for text/IME/selection; the alpha card
  paints its field. Search is debounced, admits one worker and discards stale
  generations. The installed-app catalog read never replaces an Agent's launch
  refs. Windows SystemIndex owns file coverage; no filesystem crawler is added.
  Pins read actual Explorer shortcuts and reuse the shared shell-icon renderer.
  Hover exposes the application's normal WM_CLOSE action and inline pin titles.
  Inline Focus settings reuse this face and fade clock for session-only dimming.
  Clicking away retains dimming and returns to the list. The compact power row
  uses the existing Windows owner with explicit inline confirmation. Delay is
  off by default. Confirmed countdowns use the cube clock, remain visible with
  cancellation in expanded and folded faces, and end when Focus closes.
  Shutdown/restart use a zero OS delay without forcing apps; sleep restores any
  privilege adjustment. No OS-scheduled shutdown or second timer remains behind.
  Preview scrolling cycles the current window list without activation, retaining
  the native preview host and its dimensions while replacing only its DWM source.
  Clicking activates that currently displayed source.
  Its native visibility scope records taskbars that it hides and restores only
  those windows on exit. It never rewrites Explorer's auto-hide preferences or
  shows an already-hidden taskbar. Normal Desktop teardown closes that scope.
  The scope records and broadcasts a temporary `SPI_SETWORKAREA`, reasserts its
  rectangle if Explorer reclaims it during that broadcast, and restores only a
  rectangle it still owns. It does not persist settings or restart Explorer.
- `CompanionSurface` and `ReplyBubble` compose family mixins (`companion_dashboard`/`companion_session`/`companion_voice`, `reply_panel_tools`/`reply_secondary_views`). The mixin modules are verbatim method families, not second owners: state, lifecycle, and composition stay with the host class, `companion_session` owns the desktop session-slot constants, and `reply_panel_tools` owns the panel-tool tables. Mixin code reads colors from the host's exact `DesktopVisualState`; never restore module-global palette snapshots.
- Keep visual work on the resident GUI thread but keep filesystem/SQLite notice collection off it. The one in-flight notice worker posts its bounded result back through `_post_gui_call`; do not add a second poller or touch GUI windows from that worker.
- Shared panel colors project the skin's `bg_surface` and `text_primary` through
  `interface.theming.skin_to_desktop_vars`; `bg_deepest` remains the transparency
  key. Keep those roles distinct. `interface.desktop_ui` owns the common font
  sizes and default corner geometry; saved panel preferences still take priority.
  `interface.desktop_widgets.render_desktop_switch` owns the tray switch
  artwork; `TrayPopup` owns its finite transition on the existing animation
  clock. WebView switches use `app_controls.css`. The capsule is structural
  geometry; panel/control containers use their respective active radius roles.
  Retain switch state, cancel pending work on destruction, and schedule no
  frames after settling. Focus reaches MO through its original notification icon and existing `TrayPopup`, never a second menu owner.
- `computer_activity.py` paints a cached, input-transparent bottom cue on the
  existing cube clock. The cue and existing cube are capture-excluded; the cube
  yields input while its existing four sprites carry the activity motion. The
  input hold and actual activity lifetime differ: the Desktop GUI reads its Agent's
  current computer activity, and the existing terminal heartbeat reader retains
  its exact-instance binding and expiry. There is no second scanner, animation
  thread, cube, or idle overlay. A settled cue changes native alpha only; fading
  out releases its HWND. Third-party automation must publish a real MO activity
  signal before it can be represented by this cue.
- Gmail uses `CompanionDashboardMixin._mail_notice_source()` in that existing
  worker. Its notice has a generic count, uses the established `notify_email`
  emote, and opens the resident Agent chat; the Gmail Communication row opens
  the same chat. `core/mail/README.md` owns the account and verification map.
  The Outlook Communication row also summons that Agent chat. The compact
  Home card labels Outlook/Gmail shortcuts, and You consumes the local
  Communication projection rather than a separate mail state. Outlook mail
  uses MO Connected Tab; it has no background notice or separate Desktop panel.
  Opened mail uses the existing rich-text renderer: its title accent derives
  from the active visual state and the Body label uses ordinary bold text.
- The composer key path must not rebuild a full 3x supersampled card. Text edits, selection, and cursor navigation all pass through `_finish_input_edit()` for one 1x repaint; coalesced streaming repaints use 2x, and the existing 240 ms settle performs the single crisp 3x resting render. Morph transitions remain crisp and reuse their prepared base. A successful reply-card blit caches its exact position and size so unchanged key, settle, and cached-caret frames update pixels without reapplying identical window geometry. Preserve this latency/quality split and the one layered-window owner rather than adding a typing timer, another renderer, or a background GUI thread.
- Composer word wrap (`ReplyBubble._wrap_spans`) extends the current line's width as each word is added instead of re-measuring the whole candidate line; a line's width is the sum of its span widths, so the result is identical. Profiled, measurement was about a third of a typing repaint and the change cut a wrapped repaint's CPU time by roughly a quarter. Keep it incremental when touching wrapping. Span widths are memoised per font (`ReplyBubble._text_width`), a card's static shadow, fill and edge base is cached per geometry and palette (`_card_base`; it is copied before drawing and never drawn on), and the caret reuses the body's wrap when the cursor is at the end. Together they cut a wrapped repaint's CPU time by roughly 40% with byte-identical pixels (checked against the previous renderer over 41 typing states); what remains is glyph rasterization and the per-frame text composite. A change that makes the cached base depend on content must key it on that content.
- Image and media previews scale with the active 1x/2x/3x card pass, with image dimensions rounded in logical pixels first. A fast repaint and its crisp settle must keep the same native panel geometry. The image preview has a narrow inset and one action row below it: Send to MO, Share, Tools, Folder and Path. Their icons reuse `interface.desktop_brand` and active visual-state colors. A missing source path cannot open Explorer or populate the clipboard. Image drops import through the existing attachment catalog and display locally, without adding a conversation message or submitting a turn. Send to MO routes the currently displayed paths through the existing Desktop request owner; busy turns remain protected. Non-image drops retain automatic inspection. The automatic image-inspection path is removed.
- Desktop utility surfaces—Settings, tray popup, MO Files, MO Phone, MO Design and pointer overlays—consume the exact active `DesktopVisualState` from `mo_desktop.visuals` through `interface.desktop_ui`; no surface owns a palette, spacing copy, token-completion fallback, or alternate widget renderer. `mo_desktop.settings.PanelSettings` owns the only persisted panel/button geometry and window-effect fields. Settings is the only visual writer and applies a successful change transactionally to existing windows; an adapter failure restores both the active registry and already-painted surfaces. Settings selects skins only through `interface.theming`, and its previews construct the same strict runtime state. The retained Tk lifecycle surfaces reuse `interface.desktop_widgets` for native corners, hidden reveal, and outside effects. Retired Tk utility dialogs, control wrappers, title bars and ttk styles have no parallel renderer; MO Files, Phone, SystemCare and Settings use the MO Design WebView/native visual controller with those same active visual settings; Phone, SystemCare and Settings share native app controls in `app_controls.css`. Keep the exact Dashboard four-cube mark from `interface.desktop_brand`; do not replace it with another brand, native light dialogs, native tray menus, square render substitutes, module-global palette snapshots, per-window persistence, or per-surface fallbacks.
- Native WebView app hosts (Files, Phone, SystemCare, Settings) compose their page with `mo_renderer.workspace_document()` and read their Desktop init line with `mo_renderer.read_host_init()`. The strict offline CSP exists only in `_WORKSPACE_CSP`; a host never writes its own policy string. `NativeAppWindow` writes the pipe as UTF-8 on every Windows locale, so `read_host_init()` switches the host's stdin and stdout to UTF-8 as well; a host that decodes with the locale code page corrupts non-ASCII profile, device and path names. Keep these helpers after `_initial_studio_theme_css` in `mo_renderer.py`: moving code above it re-aligns the token windows of a registered clone in `core/diagnostics/redundancy_allowlist.json` and turns the gate red.
- The compact Dashboard is one fixed renderer inside `ReplyBubble`, not another window or dashboard owner. `core.dashboard.projection.build_dashboard_projection()` supplies the canonical bounded user/operations semantics; `CompanionSurface` maps those values into Home/Work/You/Systems tiles, rows, and at most four visible delegated controls per view. Command and request controls re-enter the existing `_submit_text_request` Gateway path, Files opens the established Files panel, and destructive work retains the selected owner's fresh-evidence and confirmation rules. The active Desktop skin supplies all colors, the MO Cube mark stays visible, and switching views is local repaint-only. Opening paints cached data and permits one background refresh; never add a Dashboard timer, model call, persistence path, filesystem worker, mutation facade, or second panel. `ReplyBubble` also owns the one 200 ms reveal clock shared by Dashboard, replies, composer, history, and attachments: prepare the crisp base while hidden, paint progress zero, then start the clock after the successful first blit and align later frame starts to that clock rather than adding a full interval after each paint. Reply-recall chevrons retain their accessible labels and show a compact current/total position in the same footer rather than creating another history surface. Dashboard snapshot work starts only after the final frame; same-state content arriving during the reveal waits for that frame, an equal Dashboard projection updates actions without repainting, and completion reuses the prepared base unless newer content requires one full-quality render. Never spend the transition budget rendering off-screen, allow background snapshot/projection work to compete with it, interrupt it with a second content render, or skip its final frame.
- The Dashboard hosts no editor or business-state owner. Its Profile, Learning, Skills, Projects, Files, and server controls only open or request the existing owner; they never expose or edit raw profile prose, episodic memory, learning stores, prompts, task evidence, credentials, or private roots in the Dashboard. `core.files` remains the MO Files mutation owner and its revision/path/scope checks remain intact.
- Always-on-top compatibility is opt-in through `behavior.keep_above_apps`. Match exact executable basenames only, keep the public default empty, and never hardcode a machine-local app. MO's own always-on-top MO Shell (`MoShell.Native.exe`) is the one built-in entry, and while the cube reports an actuation yield (MO acting; cubes click-through and capture-excluded) any topmost window moving above MO is answered, so the cubes stay over the window MO acts on. A WinEvent reorder/show hook lifts the existing cube/trace/label/reply/settings surfaces as one non-activating batch when a configured overlay moves above them; the home poll is only a slow missed-event fallback. Never add a competing worker, per-frame scan, path match, or unconditional topmost loop. An explicitly configured foreground app also overrides the normal fullscreen fade.
- The hidden-console rule above has four verified exceptions; they are correct, not drift, and re-flagging them wastes a pass. `core/runtime/backend_monitor.py` deliberately opens `CREATE_NEW_CONSOLE` for a monitor window the operator is meant to see; `tools/shell.py`'s background lane applies the flags to `popen_kwargs` one function above the `Popen`; `interface/internal_terminal.py` hands the real console to the child on purpose; and `mo_desktop/voice/install.py` is an operator-invoked CLI whose pip/download progress must stay visible in the console it inherits. Any *new* captured internal console child still uses the helper.
- Optional voice keeps three invariants. The isolated voice environment is **appended** to `sys.path` (`mo_desktop.voice.storage.activate_voice_dependencies`), never prepended, so an optional add-on's copies of shared packages cannot outrank MO's own runtime; `mo_desktop.voice.storage` is the single owner of the voice layout (worker interpreter and model path included) and the controller, installer, and doctor consume it instead of re-deriving paths; and exactly one thread writes to the audio device, so `abort()`/`close()` are never called across a thread that is mid-write. Manual voice uses the existing single global keyboard hook and `_on_voice_input` lifecycle: tap Alt once, press and hold Alt a second time to capture, and release that same second Alt to stop, transcribe, and send. The hook records those press/release edges while the GUI queue applies them in order against current capture state, so a fast release cannot race the queued start. Playback slices each sentence-sized chunk so a stop lands at the next slice. A worker `started` event means synthesis began; publish `speaking` only after the player successfully writes the first PCM slice. A worker `done` event means synthesis ended, not that sound ended: queue its completion marker behind the request's PCM, drain the device from the player thread, and only then publish `idle` or re-arm Voice Chat. Keep microphone capture and hotkeys in the Desktop parent, but load and run Whisper in one supervised, demand-started worker. First demand may overlap worker loading with capture; repeated use shares the bounded lease, and cancellation, load failure, crash, disable, expiry, and shutdown must close or recover that exact owned process without a watcher or second model owner. Trace accepted voice turns at transcription completion, accepted final, TTS submission, and first audible PCM; trace conversation replies at first token and first queued clause. For an MO turn, speak only a speech-safe projection of the accepted final: raw provider deltas may contain tool calls or text later rejected or rewritten by completion gates, and command/code detail plus Markdown punctuation remain visible instead of being narrated.
- Whisper may receive only a bounded projection of the three most recent operator utterances as its transcription prompt. Assistant text, tool output, and internal continuations never enter it.
- Voice readiness additionally requires the worker, Joe model, Piper JSON
  sidecar, and successful-install marker. A new/update install also requires a
  matching recorded model digest. A marker without a digest is `incomplete`
  and a wrong digest is `changed`; both fail closed and `update` reinstalls.
  Install/update stages and
  warm-loads artifacts before promotion; uninstall requires explicit
  confirmation and removes only layout-owned directories. Do not reintroduce a
  second readiness or path calculation in controller, doctor, settings, or docs.
- The Voice Chat loop must be re-armed from turn completion, not only from a spoken reply's `idle` event. Any path that ends a turn without speech — output disabled, worker lost, playback error, rejected transcript — re-opens the mic itself; a visibly enabled Voice Chat that has silently stopped listening is a defect.
- Keep manual voice and continuous Voice Chat explicit and separate in Settings, tray text, and the example config: `voice.stt_enabled` admits one Alt, Alt+hold voice turn and that accepted voice turn answers aloud; `voice.tts_enabled` additionally speaks typed turns; `voice.chat_enabled` keeps reopening the microphone between turns. The second Alt press starts wake plus listening feedback and its release queues stop after start even when both edges arrive before the GUI drains them; tests must retain that production queue rather than replacing it with immediate callbacks. A voice turn asks for a short natural opening before any visible technical detail, and its speech projection must not narrate Markdown markers, commands, paths, or code. A voice-status question receives bounded current input, gesture, capture, and speech-worker state in the existing Desktop surface policy; do not make the model inspect the screen or guess about application focus. Live validation that changes a private profile switch must restore the prior value before closeout; do not leave continuous listening enabled as a substitute for testing manual hold-to-talk. Settings must describe `voice.stt_device` as recognition compute (`cpu`/`cuda`/`auto`), never as the microphone source. `PushToTalkRecorder` currently opens the audio runtime's default input without exposing an authoritative input-device identity to Settings, so do not infer, hardcode, or label one as an active stream.
- Spoken requests are answered by the voice conversation layer (`mo_desktop/voice/conversation.py`), not by an MO turn. Capture still finishes before local transcription; then one fast request to `voice.conversation_provider` (thinking off, a small static-prefixed prompt) streams the reply, and each clause goes to speech through `SpeechOutput.begin`/`say`/`finish` while later clauses are still being written. Its speech boundary: only its own reply text reaches speech, clause by clause through `speech_safe_text`; tool-call arguments never do, and emotion tags only choose delivery and the cube emote. Work that needs tools is delegated through `start_task` in the operator's own words to a normal voice turn, which keeps the accepted-final rule above. Submitting that turn keeps the acknowledgement playing, and while it runs `_voice_progress` turns its activity into a short spoken line (`progress_line`) at most every few seconds, never over MO's own speech and never for typed turns; the final result supersedes it. While an MO turn runs, the operator can keep talking: the conversation layer still answers (its task state names the running objective and current step), writes no Desktop session meanwhile, and any work it starts joins the follow-up FIFO, claiming its objective and progress lines when its own turn starts. It is not full-duplex yet: listening stays closed while MO speaks, and barge-in, streaming recognition and echo handling are later phases. Do not describe the loop as full-duplex.
- A user's own voice clone is one optional stage inside the Piper worker, owned by `mo_desktop/voice/clone.py`; `SpeechOutput.start` passes the resolved `clone_settings` and nothing else in MO knows about it. The worker converts one Piper sentence at a time through a resident audio.cpp `rvc` server (a server-local WAV path per sentence; inline audio is newer than installed builds), falls back to the plain sentence whenever the clone is loading or fails, and binds the server to itself with a kill-on-close job object; it also exits when MO closes its pipe. Engine and base-model locations belong to `voice_layout()` in `storage.py`. The product never ships, downloads or names a voice: the model paths are the operator's private configuration. Arabic follows the same rule: it is spoken only through an operator-configured Arabic Piper voice (`arabic_model`, resolved by `arabic_voice_model` in `output.py`); the worker picks the voice per request by main script, and `_voice_can_speak` keeps the English voice from ever reading Arabic, for conversation replies and MO turn replies alike.
- A stopped capture claims the existing transcription owner before its worker starts. Manual hold-to-talk and Voice Chat both refuse to open another capture while that owner is active, so rapid gestures cannot run parallel Whisper jobs or build a hidden transcription backlog.
- `/desktop trace` joins existing private lifecycle, conversation, provider/tool, and monitor evidence. By default it selects the latest saved reply; a reported older reply uses its exact session/turn IDs. Canonical message presentation metadata supplies the turn, instance, and start timestamp. Missing metadata is explicitly unpinned history, never a turn timestamp inferred from the snapshot save time. Exact-ID traces must not substitute a newer conversation or process; bounded missing evidence stays explicit. Sort monitor events by event time across files. Current resident/lifecycle status is separately labeled, not evidence of which historical process produced the reply.
- Desktop's optional Settings selection is resolved by `core.provider.model_catalog.runtime_model_selection`; otherwise it follows the saved `/model` provider, or configured active provider when no choice is saved. The initialized provider carries its row's optional `mo_desktop_model`. Gateway applies the explicit choice or followed variant/lowest supported effort through `Agent.model_selection_scope`; model slots consume that scope without selecting again. Desktop saves affect the next request and preserve Terminal state. Running Terminal selections come from existing heartbeat observations; exact handoff requests require an idle matching instance and publish correlated receipts without changing drafts, focus or saved defaults. No new provider stack, startup selector or semantic escalation is introduced.
- Desktop session compaction preserves user-facing conversation plus owned Terminal-sync and core momentum context; it has no Desktop-only rolling message cap. Canonical Session context-pressure limits remain authoritative. Transient
  final-gate continuation controls are removed by the core session owner; the
  active `mo-desktop` slot is the restart target. Historical Desktop snapshots use
  only the `mo-desktop-<stable-session-id>` namespace. The icon-only in-panel history
  picker is reachable from both reply and composer states, loads that namespace
  dynamically from SessionManager's canonical preview/age/turn rows without reopening each conversation. Paint cached history first and start one background refresh after the panel reveal, never disk work on the GUI callback. The picker archives a non-empty current conversation before switching or creating,
  and restores the selected snapshot into the active slot. Back to the composer restores
  its stashed draft. Summoning an already-open composer preserves its live text,
  caret, selection, and scroll position rather than consuming an empty stash.
  Exact `/new` owns the same lifecycle and never clears or switches a terminal session.
  The composer's Google and YouTube icon controls select one mutually exclusive
  composer routing mode; activating the selected icon again returns to MO chat.
  While selected, Enter and the primary Search action URL-encode the current draft
  through the Dashboard's canonical default-browser owner. A confirmed launch consumes
  and closes the composer; browser failure preserves it for retry. Search never submits
  a Gateway turn or attaches Connected Chrome, and logs only the fixed destination label
  rather than the query.
  Leaving a conversation releases only its owner-scoped target, UIA-control,
  application, confirmation, and capture caches. Never clear another live owner.
  Desktop context carries only a dated canonical prior-snapshot reference, not
  the old task's instructions. Recall can read that snapshot when relevant;
  it is not restricted to a phrase detector. The reference includes the prior
  session ID and saved time.
  Diagnose the active `memory/sessions/conversations/mo-desktop.json` session ID
  and the requested reply's `_mo_presentation.turn_id` through `/desktop trace`
  or `core.diagnostics.surface_trace.build_surface_trace`. Match both IDs in
  monitor payloads and audit rows; log-file age and resident PID do not identify
  a conversation. An empty active slot has no prior reply to diagnose. Archived
  sessions and earlier incident reports never establish a current defect.
  Desktop cleaner also rejects tagged controls and known legacy leaked gate
  prompts during migration. Within one operator turn, retain the last plain
  assistant final plus any useful tool-bound walkthrough labels; evolving plain
  provider guesses are neither durable conversation truth nor a reason to keep
  contradictory bubbles. Before display and persistence, final reply cleanup
  normalizes exact per-turn capability phrases to `right now` while preserving
  the refusal, approval, explanation, and structured-option meaning.
- `DesktopActionAdmission` supplies a request-local routing hint, not tool authority or a second interpretation that overrides conversation. Canonical read-only computer tools are directly available without an initial `tool_search` round only for routed visual or admitted native-action turns. Plain Desktop conversation starts with only `tool_search`; it does not carry unrelated health or file schemas, and a search exposes exactly the requested semantic escape on the next provider request. A visual walkthrough narrows the provider working set to `computer_observe` plus `point_on_screen`, with only an explicitly admitted compound action added; target discovery, DOM/UIA, and engineering tools stay out of that fast path. Other Desktop turns retain request-relevant `computer_targets` access and lazy discovery for unrelated capabilities. A selected option submits its full label and detail and is not automatically promoted to native actuation. A live steer replaces earlier admission before stale tools dispatch. One immediate referential follow-up may retain a recognized walkthrough's point-only presentation, including the bounded no-space/transposed spelling variants normalized by that one recognizer; it never inherits actuation from the prior turn. Role, sandbox, confirmation, exact-target ownership, and evidence gates enforce real boundaries. Do not select a read-only lane merely because a request mentions screen guidance, grow an open-ended English synonym list, or build a second assistant executor. Explicit read-only roles/lanes remain enforced. Each computer action returns its own fresh evidence and the model reads it; there is no forced verification round. The completion gate only states plainly, in the final reply, when the last state-changing action's result is still unknown; read-only answers, clarification, and honest unavailable outcomes finish normally. A spoken task is admitted from the voice layer's objective rather than the operator's loose wording. Except for the explicit project-implementation ownership boundary below, route selection belongs to the model's conversation, not phrase-triggered completion overrides.
- Admitted native actions start with exactly `computer_targets`, `computer_observe`, and `computer_act`; ordinary visual reads start with target discovery and observation. A fresh target-bound observation may carry its target class into a natural UI follow-up within the native lease. Whole-screen evidence carries only a `screen` routing hint: it neither identifies an application nor grants control, and execution must still discover the exact target. A window ref stays usable while that window lives (rebound by handle and process); element refs expire with their snapshot, and a ref that names nothing current fails at once instead of searching the desktop.
- Terminal and Desktop reuse the canonical computer owners: discover an unknown target once, observe, act, and inspect the fresh observation returned by that action. A successful native mutation automatically observes the same target; separate observation is needed only when result evidence is missing or state changes. Read-only discovery does not discard the selected window or still-valid refs. Image-capable models receive screenshots directly; text-only models use the existing bounded observer without changing the home provider. DOM/UIA controls are useful exact inputs, not mandatory predecessors to visual work. `tools/screen.py` owns pixels: render the exact non-minimized native window without activation, or crop that same visible foreground window if rendering fails. Target-relative regions stay within that window; failure never widens to the display. Explicit captures always produce fresh pixels and require no reason/refresh ceremony. Raw input remains bound to fresh target geometry and foreground identity. A browser viewport is page evidence, not native desktop coordinates.
- MO Connected Tab discovers ordinary non-private tabs without attaching; the first observation attaches automatically to the requested exact tab. Chrome owns extension installation and optional stop controls, not a required per-tab activation click. The extension keeps one native channel ready and no persistent approval/restore state. Pages held by another debugger and explicitly stopped tabs are unavailable. No model tool disconnects a tab or creates a second browser/profile. Terminal, Desktop, and read-only Design reuse `core/browser_bridge.py` and `tools/browser.py`; DOM refs are private to each owner and validated atomically with input. Observations do not claim an acting lease; actions release it after their result observation. Cancellation and request deadlines reach the transport and queued extension command. A Desktop visual walkthrough uses one fresh whole-screen observation for pointer coordinates after the page is stable. It does not require Connected Tab discovery or DOM inspection. Tasks that need page structure may use the shared browser tools through their existing owner; an unchanged target does not require repeated observation, and native pixels never claim DOM authority. Application launch accepts either a current owner-scoped discovery ref or one plain installed-application name resolved inside the same action. Name resolution launches only one uniquely best catalog match; ambiguity launches nothing. Process dispatch alone is not completion: return a verified owner-scoped window and fresh observation when unique, otherwise an explicit unknown outcome. Opening a new default-browser URL does not prove task completion. A compound open-and-walkthrough request authorizes only that explicit open before point-only guidance; explanation or pointing never inherits another action from an earlier turn. A browser viewport capture does not authorize desktop pointing coordinates. Helper enumeration limits never justify changing the overlay's tool-window/taskbar/Alt-Tab design.
- Routing classification stays in tool priming and diagnostics, not model-facing instructions. Never restore a classification-derived admission banner: spelling and follow-up phrasing cannot become authorization boundaries. The per-turn surface policy supplies the artifact destination and relevant native/profile context; the existing persona owns conversational and walkthrough guidance. Role, sandbox, target, freshness, and high-impact checks remain authoritative at execution. The one controller-owned surface boundary is an explicit software-project implementation request: an active Project Architect keeps it; otherwise Desktop queues the exact text as one normal turn to the newest heartbeat-proven same-project Terminal, including an idle Terminal without a saved conversation, or opens one visible Terminal when absent. This handoff grants no extra mutation authority.
- Desktop is not a taskboard worker. It does not use taskboard tools, render terminal taskboard progress, or own/complete the terminal session's task truth. Project implementation reuses `core.design.terminal_handoff.queue_terminal_turn`; do not add a second queue, transcript copy, worker, or progress projection to Desktop.
- The System Settings Connected Chrome card is a presentation adapter over
  `core.browser_bridge.install()`, `status()`, and `uninstall()`. Actual browser
  tool use idempotently verifies and repairs that same native-host registration;
  Settings retains **Repair bridge** only as an explicit recovery action. Do not
  persist a Desktop enabled flag, add another bridge controller, or claim to
  mutate Chrome's unpacked-extension list. Bind readiness and its visual role
  directly to `status()["installed"]`: a valid registration is a neutral,
  disabled **Bridge ready** state. Chrome owns Load unpacked/removal and the
  optional per-tab stop action; `clients/chrome/README.md` owns the user guide. The
  current extension has no Chrome Web Store release: native-host readiness must
  never be presented as extension installation. A future signed Web Store
  package may replace the unpacked installation step and provide Chrome-owned
  updates, but removing the per-tab action would remain a separate permission,
  transport, product-policy, and privacy redesign rather than a Desktop setup
  fix.

## Desktop visual system

The visual contract has one persisted input, one immutable runtime value, and
one publication path:

| Boundary | Owner and rule |
| --- | --- |
| Persisted visuals | `mo_desktop.settings.PanelSettings`; panel/button geometry plus one window-effect type and intensity |
| Active skin | `interface.theming`; Settings is the only selector/writer |
| Runtime value | `interface.desktop_ui.DesktopVisualState`; complete, validated palette, metrics, window effects, and spacing |
| Load/publish/listen | `mo_desktop.visuals`; equal states retain object identity and do not repaint |
| App title bars | `interface.desktop_brand.WINDOW_CHROME`, `window_chrome_css()` and `window_controls_html()`; one 44 px header, 32 px controls and shared glyph factory |
| Tk/native adapters | `interface.desktop_widgets`; shared semantic roles, one idempotent Windows region path, and one passive effect layer per window |

Settings, Phone, SystemCare, Files, Design/Board and Dashboard compose that same
title-bar stylesheet and action markup. App styles own workspace tools and
responsive metadata, not a second window-button design. The outlined/filled
pushpin, maximize/restore and close glyphs come from `make_glyph_icon()`.
Button colors and corners still come from the active visual state. Native
activation and resize events project actual window state through the existing
WebView controller; pin action receipts update selection without polling.
WinForms has no `TopMostChanged` event. Keep native subscriptions to real Form
events and release finite animation work when its owning window closes.
The shared purpose-entrance painter keeps small strokes in one buffer and uses
disjoint strips for large diagonal color buffers. A single monochrome mask
preserves the original raster coverage and strip boundaries stay on whole
output pixels; do not change geometry, alpha or add an animation timer for this
allocation optimization.

Shell consumes those same metrics and glyphs in its existing native theme
payload and control-strip painter; it retains its cube geometry, terminal
lifecycle and finite animation clock. There is no retained Tk title-bar adapter.

Built-in skin authoring lives only in `interface/skins/`: one validated file
per skin, the copy-ready `template.py.example`, and one immutable registry.
Settings reads that registry through `interface.theming`; it never imports an
individual skin. The private `skin` state file is a selected ID, not another
definition source.

Use four geometry roles. Windows, cards, and media frames use the panel role;
buttons, fields, selects, tabs, and selectable rows use the button role; table
rows are flat; purely structural glyphs, circular marks, and documented status
chips may use only the literals registered in
`DESKTOP_STRUCTURAL_RADIUS_WHITELIST` or
`DESKTOP_STRUCTURAL_CSS_RADIUS_WHITELIST`. A new literal outside those sets is
drift, not a new role. The source guard enforces this boundary across Python and
MO Design CSS. The default 12 px panel and 6 px button radii keep window, menu,
selector, and action silhouettes compact while Settings retains the full
supported adjustment range. Those defaults belong to the shared projection;
utility surfaces must not cap or replace them locally. A rounded role with an
explicit Tk highlight border paints that same configured color and thickness
through the shared native adapter, because clipping Tk's rectangular highlight
cannot produce the curved corner strokes.

Live visual changes must preserve existing native windows, the active Settings
page, MO Files WebView panes/navigation/transfers/open editor, and MO Phone device,
capability, mirror, and trackpad state. New open requests are serialized by the
existing owner. Do not destroy/reopen a utility window to repaint it. Resident
readiness is valid only when lock and ready records agree on PID and generation;
an incomplete or inconsistent record fails closed rather than launching a
second resident.

Pointer and UIA annotation labels share `interface.desktop_label`: one
`card.surface_canvas`/`card.finish` painter and passive `NativeLayeredWindow`
lifetime. The existing `render_desktop_window_effect` supplies the configured
outside treatment, composited into that same window. Labels are click-through,
non-activating and positioned within the
target monitor work area, including negative desktop coordinates. Each short-lived
process paints once, waits for native messages or expiry without a repaint
clock, and destroys its owned windows in `finally`. The resident pointer route
and the fallback command-line entry points remain unchanged.

Retained native Tk lifecycle windows are constructed withdrawn, fully laid out and
positioned, then mapped once through `reveal_desktop_window()`. The shared
corner adapter must defer while a withdrawn `Toplevel` has no Windows wrapper;
rounding its temporary client HWND clips the real popup and is forbidden. Do
not expose Tk's default top-left construction frame or add a second reveal path.
First visibility is one guarded transaction: map with the window's prior alpha
preserved behind temporary zero opacity, suppress Map/Expose retries, settle the
real wrapper's region, frame, descendant roles, and hidden effect exactly once,
then publish the window and its cached effect. Never render or show the outside
effect as a separate visible launch stage. Shared title-bar controls stay inset
from the top and right outer frame; their hover paint must remain inside their
button regions and cannot own or repair the window stroke.
That same adapter owns the complete outer frame: after Tk paints, it traces the
exact native rounded region with the active skin border color so the corner arcs
cannot disappear behind square client widgets. Surfaces must not add their own
corner strokes or a competing native-frame path. Descendant Configure events
must not trigger a native refresh; descendant Expose events may coalesce only a
frame repaint after child painting, never a region update or descendant-role
scan. Corner owners cancel their queued Tcl callbacks before command deletion,
including callbacks superseded by a direct refresh. Destroy releases the
controller's widget reference on the GUI thread; leaving a widget/controller
cycle for worker-thread garbage collection can abort Tcl. Scene teardown also
releases canvas hit-action closures and image references on that same thread.
The same registered window controller owns the outside treatment for
every mapped MO utility `Toplevel`: Settings exposes exactly one type selector
(`None`, `Shadow`, `Glow`, or `Hybrid`) and one intensity control, while the
active skin supplies the glow color. The layer is click-through,
non-activating, absent from taskbar/Alt-Tab, hidden with its window, and reused
instead of recreated during movement or effect-only changes. MO Design disables
pywebview's fixed shadow and consumes this adapter from the same serialized
visual state. A non-Tk owner may bound the same layer to one content rectangle
and clear its covered interior; its owning thread must pump the helper HWND's
native messages. No utility window may add its own shadow/glow setting or
native effect implementation. Settings is the stable control surface for
panel-metric edits: every page keeps one themed scrollable viewport so minimum
window geometry never hides controls, and the preview and other open surfaces
update live while the open Settings child tree is never rebuilt by its own
panel edit or persistence refresh. Settings uses the shared native WebView host
and `app_controls.css`; its former Tk window is removed. The bounded control
catalog supplies presentation and validation, while existing owners still supply
defaults, live setters, and persistence. See [Settings coverage](settings_app/COVERAGE.md)
before adding a control or claiming complete agent configuration coverage.
Declared authored preferences use `core.state.configuration`, also used by the
Desktop scoped writer. The allowlisted editors read fresh saved values and save
for reload/restart without silently changing a current turn. Missing fields
mean runtime default, never Off; raw credentials/server blocks are not projected. Learning
uses promotion, materialization and capture controls, not `learning.enabled`.
Terminal activity resolves through its existing presentation owner. Detailed
Email, project-check and schedule links open the full resident Dashboard;
the compact cube Dashboard is a different gesture-owned surface.
Raw slider motion is coalesced into semantic values in an ordered per-control
lane; the native range control's change event commits the final value after any
in-flight preview. The footer reports live-apply and persistence failures instead
of claiming an unsaved value is durable. Reloading an equal saved visual state
never repaints. Window-effect strength reuses the style's rendered bitmap and
changes native alpha only, so strength changes never resize, reposition, or
re-blur the outside layer. Settings updates its shared CSS/native metrics in place
without replacing controls or opening a selector window. Its Python renderer API
exposes only request, readiness, and window controls, never the native window object.
Close flushes the ordered save lanes before disposing the Settings host; an
unsaved preview failure stays visible. It does not retain a hidden WebView tree.
Minimize preserves the running view; reopening after close loads saved settings.

Verify visual changes with the focused Desktop UI/lifecycle/source-guard tests
and one final native Windows acceptance across the supported metric extremes,
skins, and scaling cases. Do not substitute screenshots for identity and
lifecycle assertions.

## Desktop capability knowledge

Dashboard Home delegates Project checks to `/dashboard checks`; its views retain
read-only LSP status and recorded evidence. Native Settings owns project
On/Off/Default through `LspManager.set_project_selection`, plus declared server
configuration through the shared authored writer. The former Dashboard LSP
mutation route and toggle are removed. Missing configuration opens guidance
rather than installing a server; no additional status poller is introduced.

`mo_desktop/capabilities.py` is the lightweight, read-only feature manifest supplied with Desktop's surface policy only for an explicit behavior or capability question; ordinary conversation does not resend the full inventory. Keep each entry aligned with its canonical owner, user-visible actions, and safety/runtime limits; it is descriptive only and must not become a second execution or state owner. Update the manifest, the user-facing companion table, the `CAP-DESKTOP` ledger row, and focused tests together when a feature changes.

## Desktop app packages

MO Files, MO Phone, and MO SystemCare are Desktop *apps*: self-contained packages under
`mo_desktop/` owning their window, view model and helpers behind one exported
class. Keep new surfaces of that size in their own package rather than growing
`companion.py`, and reach them the way the existing ones are reached — a tray
item calling a `companion.open_*_panel()` seam that lazily imports the package
and holds one instance. The lazy import is load-bearing: it keeps the package,
and anything it pulls in, off the startup path.

An app package owns no authority of its own. It routes to existing owners: the
file service, the Everywhere clients, the attachment home. If a window needs a
new capability, extend that capability's owner and call it, rather than opening
a second path from inside the app.

MO SystemCare is the native WebView maintenance adapter over
`core.systemcare.SystemCareService`. Files and SystemCare share only the
`app_window.py` process/theme/source handshake. SystemCare reuses the Design
renderer, active skin and shared four-cube entrance; it has no Tk fallback.
Opening shows cached results and refreshes lightweight resources. Deep scans
and selected checks wait for a request. The window separates machine, MO,
configured-host and selected-project evidence; MO maintenance review returns
through the existing Desktop conversation/Gateway. The view model owns one
background worker. Selected protected checks/actions use the same core service
in a one-shot Windows permission worker. Closing hides the host while work
retains its journal. Dashboard reads side-effect-free status and routes
Scan/Open/Cancel to this service/window. Preferences use
`mo_desktop.systemcare`; persistence is acknowledged before changing the local
projection, and optional automation reuses the existing scheduler. Apply and
restore results come from persisted core receipts/originals. See
`systemcare/README.md` for the current action and coverage boundaries.

`CompanionSurface` and `ReplyBubble` already had their stable behavior families
extracted into the mixins named above. Their host files remain the composition,
state, and lifecycle owners; file size alone is not evidence of duplicate
ownership or a reason to split the same families again.

`ReplyBubble` prepares shared mode, controls, and option state through
`_prepare_panel_show`. Its `_repaint_for_panel_show` owns successful focus,
cube hold, click-away arming, and composer blinking after the existing reveal
decision. Public show methods supply their content and callbacks. Companion
calls the current cube, reply, and tray signatures directly; a presentation
failure uses its one surface rebuild path with the original arguments, so
attachment restrictions and notification actions cannot disappear in a retry.

MO Phone specifically: mirroring has exactly one owner and it is scrcpy. It
streams hardware-encoded video and carries input on its own channel, so a
capture engine, a frame relay or a recorder inside MO would be a slower second
path competing with it — do not add one. MO's part is finding the tools,
listing devices and running one session. The single on-demand frame MO keeps is
written through the shared attachment home and provenance index so MO Files
shows it; the app never invents a store. The trackpad is the one place a phone
drives this computer, and it stays on loopback reached through `adb reverse`:
never bind it to a routable address, and keep the port unshareable, because on
Windows `SO_REUSEADDR` lets a second process bind over a live listener and take
the phone's gestures. The loopback tunnel rides the selected ADB transport, so
it can be physically carried by USB or Wi-Fi without creating a second network
listener. Reconnect the saved private endpoint even when USB is also listed and
prefer it after verified pairing. Automatic USB re-arm may apply only to the
same deliberately paired phone through its private one-way device binding;
never enable network debugging on an unrelated attached device. Feel and the
compact settings/button layout belong on the phone so tuning never becomes a
desktop policy. The only added gesture vocabulary is bounded `t: z`, mapped by
Desktop to ordinary Ctrl/Command-scroll; do not add another transport or zoom
owner. A session that drops must release any held button rather than leave one
stuck down.

MO Phone uses the shared `app_window.py` process/theme/source handshake, Design
native visual controller and `app_controls.css` primitives also consumed by
SystemCare. Its Tk presentation is removed. `phone/bridge.py` owns one serialized
device worker, coalesced refreshes and query-free snapshots; the existing
`PhoneMirrorModel` still owns ADB, scrcpy, wireless recovery, Trackpad and captures.
Loading is coherent before reveal, and shutdown prevents late work from
publishing. Closing serializes cleanup of the original Trackpad transport after
any in-flight start, then exits the host when no mirror is active. An active
mirror retains its hidden owner until it ends; the existing liveness check then
exits without another timer. Reopening cancels the pending exit and retheming
preserves that running workspace. After an idle exit, reopening discovers devices
in a fresh host. Minimizing does not request an exit. Android-owned grants remain explicitly
unverified in the access drawer. The Desktop parent acknowledges Files opening
through its existing callback; only that acknowledgement produces success.
Resource lifecycle events reuse the backend monitor and contain no device
serial, input, frame or nonce. See `phone/README.md` for the complete boundary.
Phone projects canonical Hub readiness separately from Desktop Live Control
status and verifies coordinator authority through `/api/mo/device`. Initial
discovery, explicit checks and QR requests reuse its single worker; no idle
health or pairing-completion poller is added. The
WebView API exposes only the page commands, with model/configuration and
parent-receipt/lifecycle seams kept private. Its inline QR and the existing
Desktop tool share `mo_everywhere.pairing_qr.create_android_pairing_image` for
issuance, rendering, private storage and expiry. Ordinary pairing never adds
phone-host authority. QR pixels remain local to the renderer, absent from state
snapshots, pipes, attachments and telemetry; hide/dismiss/replacement clears them.
MO Files prepares its native entrance
before showing the host and reveals the WebView after its first browse result
or visible failure. Its WebView bridge runs file/network work outside the resident GUI
lane and returns results through the existing pywebview API.
Files' transfer display pauses its existing refresh timer while hidden and
refreshes immediately on visibility return, coalescing an in-flight request.
This only controls presentation reads; transfer workers retain their lifecycle.

An admitted private profile may contribute bounded Desktop app metadata through
the neutral local-extension bridge. The public launcher catalog contains no private app
name, implementation, server, credential, or domain branch. App code loads only
after its explicit launcher tile is selected, receives an on-demand Tk root on the resident GUI thread plus the
exact active `DesktopVisualState` and notification seams, and must expose one
cached window with `show`, `apply_visual_state`, and optional `shutdown` methods.
It receives no Agent, Gateway, model, tool, or conversation authority. Empty or
disabled profiles contribute no rows and import no private code.

## Native Live Control and controller ownership

- Native **MO Live Control** is the only remote Desktop/terminal control path.
  Android and the workstation split workspace are controllers over that one
  broker; neither owns a parallel transport.
- Live Control requires Everywhere, the intended host, and separate exact-scope credentials. A controller needs `control` plus exact `remote_control`; each terminal/Desktop host has a distinct `remote_host` identity and connects outbound to the paired WSS hub.
- Desktop advertises the fixed `desktop` host-instance key. The hub derives the
  opaque stable routing key from that value plus the authenticated host
  principal; the visible `MO Desktop` label is presentation only and must never
  become Android task or lease authority.
- `DesktopTerminalLauncher` owns all fixed `host_actions_v1` operations:
  `start_mo_terminal`, `start_portable_mo_terminal`, and `stop_mo_terminal`. It resolves the
  repository's canonical `mo.py`, current interpreter, optional exact current
  config path, and a fresh `MO_INSTANCE_ID` itself. The portable variant accepts
  only an exact conversation ID and positive expected revision, appends trusted
  portable-session flags, and waits for `mo.py` to persist the exact handoff
  ready marker. Stop accepts only an exact instance ID retained by this
  launcher and terminates that owned Windows process tree; it accepts no PID
  and cannot stop a terminal launched by another Desktop process. The controller
  supplies only an exact Desktop host, fixed action name, 32-hex idempotency
  key, and the bounded operation identifiers. Never add an executable,
  arbitrary argument, working-directory, environment, PID, or shell input to
  this boundary.
- Terminal attachment enters the running TUI queue. Desktop exposes only a bounded primary-display lane and reuses target revision, lease, and input guards. Frame geometry comes from the captured primary-display image; capture must not import the mouse/keyboard actuator merely to rediscover those dimensions. JSON uses WebSocket text frames; JPEG uses binary frames.
- The existing screen lane consumes negotiated `screen_visibility` input through
  the ordered host worker. Hidden screens pause the capture event and release
  held inputs while preserving the lease; resume clears the frame digest and
  captures a fresh observation. The broker independently suppresses hidden
  metadata/pixels. An overloaded input queue ends the lease instead of silently
  dropping its visibility transition. Do not add a second capture or polling owner.
- Windows Live Control text uses `KEYEVENTF_UNICODE` with the complete native
  mouse/keyboard/hardware `INPUT` union. Keep pointer-sized metadata numeric and
  preserve the ABI-size regression (40 bytes on 64-bit, 28 on 32-bit); a
  keyboard-only union can return a successful phone send while Windows rejects
  every character.
- A Desktop drop imports directly through the existing attachment catalog off
  the GUI thread, using its byte/count limits. Show the preview immediately
  after import and retain it with Tools, the final answer, and options. Preserve
  attachment-only checkpoints and canonical reply presentation across restart.
  Classify attachment paths identically before and after serialization. Automatic
  attachment turns name `perceive` for image/PDF model input and `read_file` for
  text; `show_image` is operator display, not inspection evidence.
  Native image edits run off the GUI lane and keep their original source. Explicit Send
  uses `core.transfer.TransferOutbox` and a stable device ID without another
  catalog copy or transfer queue. Late edit/destination results cannot replace
  a newer interaction. Sending never blocks or precedes local attachment use.
- The cube's bottom-left left-button hold uses its existing press/release owner
  and one two-second native GUI timer. The held cube alone ramps into the cached
  skin-derived accent on the existing frame loop, which runs at active cadence
  only for the hold and keeps that cube under the pointer. It opens one transient
  native alpha screen selection from a single original-resolution desktop snapshot. The
  unchanged snapshot is the background, and only the dragged rectangle is
  shaded. Escape/right-click cancels. Save the cropped PNG off the GUI lane into
  `core.state.attachments` and reopen the existing image card. The image card's
  Share control uses live terminal
  heartbeats and the exact PID/instance control spool to prepare the local path
  as an unsent composer draft; check the conversation slot again at receipt.
  Never infer a target from window titles, overwrite a draft, send automatically,
  or add a resident capture poller or separate image panel.
- The tray-owned **MO Files** window is a native WebView board launched by the
  Desktop companion. Its bridge remains a thin adapter over
  `core.files.FileManagerService`, `core.transfer.TransferOutbox`, and the
  existing authenticated transfer service/client. It merges the local source
  with hub/host discovery, deduplicates this Desktop by its opaque stable host
  key, and routes a remote selection through that source's exact current ID.
  Startup prepares the Dashboard's finite native four-cube artwork before the
  WinForms show event, then reveals the WebView only after the first folder is
  listed; a cold Pillow import on that event can stall WebView2 initialization.
  An empty HTML loading page is never shown. The connected-place menu and aligned folder map use the current Desktop
  skin and shared four-cube mark. Each pane's source button beside Back/Forward
  opens the same source menu for that exact pane; the active split pane is
  marked, and browse shows the destination and loading state while its bounded
  request completes. Opening a split pane clones the current browse snapshot
  and starts independent history without a duplicate network browse. The top-right view control switches the same
  browsing state between the folder map and a familiar file list. The current
  folder stays prominent; earlier
  folders remain dimmed and clickable in the breadcrumb line. If no phone
  Files host is online, the menu shows an inert "Not connected" phone cue,
  never a synthetic source ID or location. File bars compare only directly listed
  file sizes, never invented recursive folder totals. Circular actions above
  the focused folder or selected item are gated by the current location's
  operations and selection. Back/Forward mouse buttons, Alt+arrows, breadcrumbs,
  and an editable relative path navigate within that location. The board's
  cut/copy/paste clipboard uses guarded move/copy and stays within one source.
  Optional split view keeps independent source/location/path/search snapshots.
  Sort by name, type, size, or modified date applies to the current pane.
  Right-click exposes the same gated actions at the click point. Hold-drag
  shows the selected file size and waits for a move/copy choice at the drop
  point before calling the existing guarded owner. A cross-source file drop
  waits for Send confirmation and uses only a canonical paired
  transfer target; folders are not sent. Local file drag-out offers Windows
  file-drop paths to Explorer; after Explorer accepts a copy, the operator may
  keep the originals or confirm their removal through recoverable MO Trash.
  Explorer drag-in can move an item already inside an
  allowed MO Files location; importing arbitrary external paths would require
  a separate file-boundary owner and is not silently added by the UI.
  Active transfers appear inline on the same board with progress, cancel, and
  retry. A failed poll marks retained progress as last known and exposes its
  error in the transfer control/history while local browsing remains usable.
  Batch results retain the completed count and operation error even when the
  subsequent folder refresh fails; refresh targets the originating pane.
  The title control opens completed history and clearing through the
  existing owner. An idle transfer lane consumes no board height. The established Live
  Control socket may advertise the independent `files_v1` request lane, but
  file requests use their own bounded queue/worker and never acquire a Remote
  screen lease. Full drive locations come only from the selected host's
  existing `access.mode: full`; otherwise its configured project roots remain
  the boundary. Explicit Quick transfer opens one five-minute browser session
  bound to an operator-selected private IPv4 interface. Its QR contains a
  random bearer URL that is never logged; the browser may download only up to
  eight selected local files of at most 64 MiB each and upload up to eight files
  into the exact currently allowed local folder through
  `FileManagerService.import_stream`. The local file owner validates names,
  destination scope, and exclusive creation. The listener stops on expiry,
  operator dismissal, or window close. No QR grants raw browsing, an enduring
  device credential, or remote host file authority. This LAN HTTP lane is
  unencrypted, so the UI identifies the trusted-network requirement; it must
  never be described as confidential on an untrusted network. Closing or stopping
  aborts unfinished uploads and removes their temporary files; completed files
  remain with the receiver, and persistent Hub pairing is unaffected. Close
  destroys the native window and its effect layer; reopen
  launches a fresh one. Minimize uses the OS; pin toggles only that window's
  topmost state. Never add a second catalog,
  transfer queue, raw-root picker, permanent LAN share, or GUI-thread
  filesystem/network worker.
- MO Files discovery uses `credentials/everywhere-files.json`, a dedicated
  controller identity with exact `control` plus `file_browse` and optional
  `file_manage` / `file_transfer`. Never reuse or broaden the coordinator,
  Live Control host, or cargo credentials. Pairing absence, hub unavailability,
  and bounded Hub rejection details remain visible in the native window while
  the local source stays usable. A remote phone source advertises only
  list/read over its one real selected-folder or all-files location; never
  synthesize removable roots or surface mutation/preview/send controls for it.
- Native folder creation, editable-text opening, allowlisted Windows document/
  media opening, path navigation, client-side search/sort, and manifest-backed
  Trash restore remain adapters over `FileManagerService`; no raw path or
  permanent-delete shortcut belongs in the WebView. Remote binary opening requires the
  existing verified transfer owner first.
- A non-hub Desktop uses the dedicated Everywhere transfer credential slot.
  Never broaden or replace its notify-only Android-pairing coordinator token;
  cargo requires a separately paired exact `control` plus `file_transfer`
  identity.
- An access token authenticates a new WebSocket. Established sockets revalidate fixed device capability, exact scope, and revocation without disconnecting only because the handshake token expired.
- Host hello and heartbeat sends are required liveness operations. A failed send must tear down the half-open socket and enter the bounded reconnect loop; local Desktop status must not remain connected after hub presence expires.
- Integration domain facts stay on the Desktop. The retired `domain_events_v1` lane existed only to feed phone trading notifications, which were an over-read of an operator example; do not reintroduce a Desktop-to-hub integration event bridge without an explicit new operator decision.
- The hub and clients never persist frames, pointer paths, keys, terminal input, or device-auth material. Presence is not proof that a control host exists; a headless server never advertises a screen it does not own.
- Device power presence is a bounded cosmetic fact, never authority. The hub mirrors only validated typed `power_state`/`power_source`/`physical_link` values plus an opaque presence key to desktop-kind hosts; the companion's `on_presence` handler drives `DesktopCube.set_phone_charging`, which reuses the dock's `charging` loop with its own monotonic expiry. Never toggle the dock's `_charging` flag from presence, extend the vocabulary ad hoc, or let a stale fact keep the emote alive — expiry ends the loop at a cycle boundary.
- MO Everywhere Android is resident-first after pairing. The collapsed transparent cube owns gestures, emotes, and notices. Expansion transfers visual ownership to one compact authenticated Dashboard/Chat/Control/Files panel; closing restores the cube.
- Daily Dashboard, Chat, background Work controls, Files, and exact-host remote launch belong in that panel. Dashboard validates the optional canonical projection, adds bounded local phone capability indicators, and routes to existing destinations; it adds no Activity, service, permission, timer, database, provider, or state owner. The launcher is Settings/recovery-first with explicit fallback Chat. Settings owns resident preferences, control status/configuration, device authority, and unpair, but does not duplicate remote launch. Native terminal/screen control opens full-screen in the normal app task, never inside the compact resident task.
- Android pairing reuses the serving hub's one-use registry through `/everywhere pair android`. An already-paired coordinator Desktop/workstation may request the same fixed five-minute Android grant through its existing exact `notify` plus `continuity_read` and `continuity_sync` credential; Android/control and live-host-only credentials remain rejected. The QR renders through the existing image panel, remains out of model/log text, and its private temporary PNG is scheduled for deletion at expiry or normal exit while stale inactive QR files are purged before another request. Workstations cannot initialize a second registry. The unpaired Activity fills the safe viewport and keeps QR plus manual recovery reachable in one internal scroll owner.
- An explicit Android pairing request on Desktop is dispatched deterministically to that canonical
  tool before Gateway; provider prose never decides whether the already-authorized surface action
  runs. Explanation-only requests remain conversation, and extra phone-control host authority
  is requested only when the operator names it. The image panel may change caption/scale, but must
  never expose the secret/path, mint another credential, or add a second registry/authorization path.
- `core/state/everywhere_readiness.py` is the single value-free topology/readiness authority for role, hub/API/registry ownership, dependencies, credential presence, health, blockers, public/private Android installation guidance, and next setup action. Prose, a database file, `continuity.hub_local`, or API enablement never grants hub authority.
- Public Android distribution is Google Play only; no production Play release is claimed until the Android authority records it. Private owner-device Direct builds remain local maintenance tooling and preserve the established package/signer. Installation and pairing remain separate serving-hub actions.
- Android release acceptance exercises the real notification/overlay permission round trip on a supported physical device, then proves process survival, foreground service, cube visibility, and explicit disable. Keep authenticated `FragmentActivity` compatible with Compose Activity Result launchers; do not replace the instrumentation compatibility test with mocked permission-ready rendering.
- Public Android behavior, requirements, and availability are recorded in [`ANDROID.md`](../ANDROID.md). In a maintainer checkout where the ignored private client tree is present, `clients/android/MAINTAINING.md` owns source/release maintenance and `clients/android/CHANGELOG.md` owns versioned package history and actual-device evidence. Every Android behavior, UI, permission, protocol, dependency, compatibility, packaging, or release change updates those matching public/private authorities together. Never turn planned coverage into a shipped claim.

## Rendering and motion ownership

| Surface | Existing owner | Motion and resource boundary |
| --- | --- | --- |
| Main cubes, chase, gestures, emotes, trail | `cube.py`, `cube_motion.py`, `cube_interaction.py`, `cube_panel.py` | One resident frame lane; elapsed-time springs and bounded sprite/trace caches. |
| Four-cube launcher and folder menus | `tray.CubeLauncher` | Uses the resident cube's pixels and surface; finite open/close callbacks, then the resident tick for interaction. |
| Composer, replies, attachment cards | `ReplyBubble`, `card.py` | One cached base per reveal; finite callbacks stop at completion. Closing during opening reverses its current pose. |
| Focus face and popovers | `focus.py`, `focus_paint.py`, `focus_native.py` | Uses the cube tick; layout/opacity settle, stationary artwork is cached, and only the current hover owns a DWM preview. |
| Tray controls | `tray.TrayPopup`, `interface.desktop_widgets` | Shared switch artwork; the existing finite callback stops after opacity and switches settle. |
| Native app entrances | `mo_renderer`, `NativeLayeredWindow` | One finite WinForms clock per opening; stops while waiting for content and disposes on completion, failure or close. |
| Shared WebView controls | `app_controls.css` | Owns buttons, switches, toast/fade keyframes and reduced-motion rules for Phone, Settings and SystemCare. App-specific scenes remain local. |
| Shell folds, linked pairs, hover and drop feedback | `mo_shell/native/ShellSurface.cs` and its partials | Separate native presentation owner; see [Shell's contract](../mo_shell/MAINTAINING.md). |

Presentation timestamps, gestures and finite deadlines on the resident lane use
`time.perf_counter()`, the same precise monotonic clock as `NativeGuiLoop`.
Do not mix them with `time.monotonic()` values: older Windows Python versions
back that API with a coarse clock and a different epoch. Service polling,
retry deadlines and wall-clock dates retain their own independent clocks.
The resident's 16/42/125 ms active/passive/hidden frame budgets include time
already spent doing work. A missed budget yields at least 1 ms; it never queues
catch-up frames. Native app entrances request 15 ms WinForms ticks to avoid
rounding a 16 ms request into two default Windows timer quanta. These are
requested budgets, not a guarantee of presented frames under load.

Frame-rate loops on the resident lane (the active cube tick, launcher expand,
tray popup, composer reveal) schedule with `frame=True`. `NativeGuiLoop` then
releases the callback on the DirectComposition compositor clock
(`DCompositionWaitForCompositorClock`) at the display tick nearest the requested
deadline, so a 16 ms budget on a 144 Hz display becomes every second refresh
instead of a free-running timer's 2/3-refresh beat. The wait is bounded, wakes
for queued GUI work, and releases on timeout so a locked or sleeping display
never strands a final paint; without the API the flag is an ordinary timer.
Within two refreshes of a frame deadline the loop waits on the compositor clock
instead of `QS_ALLINPUT`, so window messages are pumped at the next tick: at
most one refresh of added input latency during motion (about 7 ms at 144 Hz,
17 ms at 60 Hz).
Do not substitute `DwmFlush`: measured here its returns jitter between 3 and
11 ms and it skips a pass when called late in the period. A frame that costs
about a refresh or more (a full composer re-render) does not benefit; cut its
cost instead of adding a second pacing mechanism. Measured on a 144 Hz display
with the compositor clock as ground truth, sub-millisecond frames went from about
40% irregular presented intervals to about 6%; this is cadence evidence only, not
visible smoothness acceptance.
Native app entrances run in the WebView host's WinForms clock, outside `NativeGuiLoop`, so they keep the 15 ms tick but each frame sleeps to the display tick (`gui_loop.display_tick()`) and is posed for `tick + display_period()`, the moment it is actually on screen. Measured against the compositor clock, the pose-versus-display error fell from about 2.3 ms to about 0.7 ms (standard deviation), so a fast entrance no longer wobbles even when frame spacing varies.

Per-frame work on the resident lane must stay cheap even when it is only a poll. A live `py-spy` sample of the idle resident found the bound-terminal computer-activity poll at 56% of the GUI thread because it JSON-parsed every row of the heartbeat ledger (hundreds of rows) on each poll, then discarded the rows outside its 15 s window. `recent_instance_snapshots` now reads the append-only ledger newest-first and stops at the first row older than the window plus slack. Profile a changed idle path against the running resident before and after, never by inspection alone.

Retarget finite motion from its current pose. Composer collapse and launcher
menu dismissal reverse their existing symmetric curve without a full-size or
full-opacity flash; repeated collapse does not restart it. Keep completed
artwork cached, preserve premultiplied-alpha boundaries, and publish the final
frame before releasing its transition state. Theme, radius, typography and
glyphs still come from the shared visual owners above; timing fixes do not
introduce alternative palettes or renderers.

Verification distinguishes deterministic state/pixel checks, hidden native
handle/timer checks and visible acceptance on the running Desktop. Passing
the first two does not establish DWM frame pacing or the final visual feel.
Measure finite interaction separately from passive and hidden work; a faster
interaction clock must not become an always-running background animation.

## Activity, replies, and walkthroughs

- One living panel: a turn's status ("got it…", the concise tool step) is the panel's one-line `PanelState.STATUS` row, grown from the cubes through the panel's existing reveal, and the reply later grows from that same surface; `_present_activity` owns the routing. One surface at a time: a preserved selected/attachment card stays the only panel for its turn, with no status label beside it (the cubes' working spinner shows the turn); the cube-side glance label carries turn status only when no panel is open, and notices that arrive while the panel is open wait in `_held_notices` until it closes. Docked faces consume cubes: the composer cube 1, Focus cube 3, the compact Dashboard cubes 0 and 2 (its own `ReplyBubble(face="dashboard")`, never the composer's panel). `DesktopCube.docked_faces()` is the one list every owner passes to `focus.cube_geometry`, so the block clamps as one; a click on another docked face is not a click away (`ReplyBubble._inside_docked_group`). A face that opens, closes or resizes calls `DesktopCube.relayout_docked_faces` so the others are placed again; the docked Dashboard keeps one height (the Focus column, else a constant) with a fixed header and scrolling body. Every panel reveal is the composer's: the card grows out of a cube's own pixels (`ReplyBubble._capture_cube_source`) and docked faces collapse back with the reverse morph (`collapse_to_cube`); launcher and Focus popup motion use the same `DesktopPanelDesign.transition_ms`. A pointer label is the one surface while it shows: any turn that pointed, walkthrough or not, holds its streamed and final reply through `_walkthrough_recap_must_wait` and opens the panel once the label has had its time. Volume and other answers to the operator's own gestures still use the label at once; a visible reply is never overwritten by status. During native actions the panel stays visible through `ReplyBubble.set_input_yield` (click-through, capture-excluded, click-away paused) and hides only where Windows cannot apply that. Panel controls are icons; Send and Submit keep their labels. Raw provider phases remain diagnostic-only and map to a small deterministic vocabulary, never raw provider text or hidden reasoning.
- Native Explainer progress reuses the current turn's tracked shell output: Terminal projects measured phase, style, artifacts and verification with its existing method pulse; Desktop's existing poll projects phase/progress in the glance label. Final artifact delivery stays in the normal result path. These presentation adapters never add status-only model calls or make child output task authority.
- `ReplyBubble` owns actual panel visibility and the dock side selected from the rendered card width. Companion may mirror that state for turn guards, but toggle and label placement query the renderer; do not re-estimate panel width or infer visibility from an earlier request.
- With no open panel, the cube chooses the label side from current screen space. With an open panel, the label uses the actual opposite dock side. Never force a fixed side at a screen edge.
- The default character remains one layered cluster and one shared panel. Each constituent cube may react to pointer distance and retain its press/release index, but an optional per-cube callback must fall through to the established shared gestures unless it explicitly consumes the click. Do not create per-cube windows or panels without a separately approved interaction design.
- `gui_loop.py` owns the resident's single native message/deadline loop and Windows clipboard/screen/pointer services. Worker posts wake its Windows event; deadlines use the monotonic `perf_counter()` clock and a one-shot high-resolution Windows waitable timer (ordinary waitable timers on older Windows). The loop never raises the global timer resolution, cancellation releases callback captures, and it sleeps until input or work is due. Do not substitute the coarse `GetTickCount64`-backed `monotonic()` clock used by older Python versions for these sub-frame deadlines. There is no resident Tk root. The parked Mologrthim and admitted profile Tk apps alone use `tk_host.py`, created on explicit app use on that same thread; no Tk import/interpreter is needed at normal startup. Its cached widgets and interpreter are torn down on the GUI thread. Hidden optional Tk apps are serviced at a reduced cadence. Cube, composer/reply, shared label, trail, launcher and screen-selection windows use `NativeLayeredWindow`; no Tk canvas/chroma fallback remains for them. `native_files.py` owns the OLE file-drop registration and Windows IFileOpenDialog boundary. Drop paths arrive as exact Unicode filenames, not Tcl lists; COPY feedback does not bypass the cube's final drop-radius check. Native events snapshot coordinates/text before posting to the existing GUI queue. Contiguous pointer moves coalesce without crossing click/key boundaries, and destroyed HWNDs discard pending input. Idle cost belongs to cube cadence and layered compositing, not styling. Visible idle animation targets about 24 Hz so the existing passive-frame cache can reuse slow breathing frames, interaction targets about 60 Hz, and hidden residency stays about 8 Hz. A failed cube tick logs its traceback, pauses only that tick path for five seconds, and retries on the same loop; never swallow it or add a recovery worker. Use monotonic visual clocks and elapsed-time-scaled easing; do not add a second renderer/thread or restore frame-count-dependent steps, binary emote switches, or cusp-shaped motion.
- Normal Desktop finals use the existing wider reply card. Prefer concise answers for simple requests, but retain the full accepted answer and requested detail; never clip semantic content to a character count or rewrite an evidence-blocked result for presentation. Long content scrolls with a subtle position indicator. Cache only the current body's wrapped lines by text, width, fonts, and render scale; no second renderer. Provider setup narration belongs only in the compact activity label, including when tool metadata arrives later. During a walkthrough, suppress all evolving provider prose from the reply card; numbered verified pointer labels are the only in-flight body, the wider card stays hidden for the entire sequence, and `_set_result` schedules it exactly once as the final recap after every point. This rule never triggers a second tool call or changes specialized panels, structured choices, or terminal answers.
- Model-driven choices retain their originating canonical assistant content. Recall and restart reparse that same options block and restore its attachment presentation; only navigation and current selections belong to the UI cache. Independent choices use multi-select; exclusive alternatives and approvals use single-select. Labels and details wrap; oversized lists scroll within the available card height while Submit stays visible. Submission binds the displayed attachments and selected rows' full labels/details to that turn; later browsing or drops cannot rebind its reply. Only an image produced by that turn may replace its attachment presentation. Do not add a fixed extra conversational row, strip numbered prose, or retain the newer reply's action callback when browsing an older reply.
- A pointed walkthrough may use the larger guidance variant of the same glance renderer, showing one numbered title/explanation at a time. Ordinary thinking, observation, volume, sync, notices, and routine activity remain compact; do not add another panel or targeting path.
- Guidance holds label priority for its bounded reading window. Ordinary activity may not preempt it; preserve a queued notice and replay it after guidance instead of replacing the point or dropping the event.
- Every walkthrough proves one fresh whole-screen observation followed by real pointing. While the target is unchanged, that observation supplies every coordinate and all `point_on_screen` calls are issued together so the local FIFO presents one numbered explanation at a time; target drift or missing result evidence requires another observation. Its final waits for the point sequence, restores normal Reply/Submit controls, and gives a useful synthesis rather than substituting a text-only list for the walkthrough. Structured choices are optional when there is a real decision; completed guidance never retries merely to force a menu. Intersect every proposed choice with the active persistent role's real lane before rendering it; a review-only role must never offer a file/app action it will block on selection, and an impossible selected action gets the explicit persistent-role boundary before Gateway. Do not repeat the point list or hardcode guide/artifact/image-generation choices.

## Persistent Desktop roles

- Terminal `/role activate` and conversational `role_work activate/off` share
  Agent's role lifecycle. Discovery and `role_work list` work before activation;
  specialist registration, dispatch and reports still require Project Architect.
  Desktop text and voice reuse the same role packs and their isolated session
  metadata; a tool selection updates that Desktop role without changing Terminal.
  The Life Story Book Writer resumes its manuscript
  from the selected workspace's `BOOK-STATE.md`; a Design preview is separate
  from a verified PDF, EPUB or print export.
- Project Architect specialists are project-bound skill packs. `role_work`
  reuses the skill writer and worker runtime, with existing concurrency and
  evidence limits. Worker records carry their project identity; another
  project's report cannot appear in this project's checklist or status lookup.
  Reports remain untrusted until the orchestrator checks their evidence.
- Role activation and orientation do not start project calibration or worker
  assignments. Calibration belongs to an assigned or explicitly resumed task.
  `role_work show` (also `/role show`) selects Project Architect when no role is
  active and opens the actual native view. It does not replace another active
  role implicitly; activate Project Architect explicitly first. A Design prototype requires a
  separate design request and must never stand in for live workers.
- `mologrthim/snapshot.py` projects that roster and worker state; the extracted
  `mologrthim/app.py` owns the frameless native Mologrthim workroom, with
  skin-derived room and character artwork in `mologrthim/scene.py`. Its current
  scene and backend boundary are documented in [Mologrthim](mologrthim/README.md).
  The Work cube opens this same room dimmed with the live-Terminal chooser;
  choosing a running Terminal opens an observation-only view with no composer.
  The plus beside Close launches one fresh Terminal with Talk to MO enabled;
  it starts no message or work automatically. Discovery, exact typed handoff,
  startup and correlated acknowledgement reuse their existing owners. The
  chooser never creates a Desktop conversation. This does not change the active role; the
  existing role lifecycle and execution admission remain authoritative. The native
  window owns no conversation or execution ledger. Terminal
  `/role show` pumps this same view on its existing UI event loop, bound to its exact
  conversation and project; Desktop reuses its resident GUI loop. Unsupported
  nonlocal/background surfaces report unavailability without launching an
  Agent except the explicitly requested new normal Terminal. Terminal teardown
  and conversation/project changes close only
  that Terminal's view. Tool completion
  refreshes the cached roster off the GUI thread. The existing GUI clock drives
  finite emote motion, while hidden windows skip refresh and unchanged snapshots
  reuse their rendering. The room and bounded role/state sprites are cached.
  Shared colors, pixel-sized typography, character settings, control radii,
  brand glyphs and emote sampling supply the scene. Theme changes invalidate
  artwork. Employees select a scrollable assignment/evidence/report inspector;
  larger rosters page four employees at a time. Blank-room dragging moves the
  frameless window; its exit control or Escape closes only the view. First open uses
  `centre_desktop_window` with the Desktop cube's monitor anchor when present.
  Placement uses the settled window dimensions, falling back to requested
  dimensions only before layout. Theme rebuilds and reopening preserve the
  chosen size and position; inspector text wraps using the measured font width.
  The embedded conversation reads retained visible user/MO messages from the
  bound session. Send uses the owning surface's existing input/queue path;
  rejected submissions retain the draft. Internal continuation controls and
  runtime summaries are excluded. Open conversation returns to the host surface.
  Session identity includes the session ID, so resetting an object in place
  cannot silently redirect a stale window. The wall board reads Terminal's
  exact active board or Desktop's Gateway board callback, never a different
  conversation's board. Monitor receipts require exact session/run correlation;
  learning counts remain explicitly profile-wide. Single-inflight background
  observations run at most every two seconds while visible, with unchanged
  monitor tails cached. Worker/conversation snapshots refresh at 300 ms.
  The shared host's CPU/memory, all observed Terminal totals and system load
  reuse the native resource sampler and Health bands. Exclusive process roots
  prevent double-counting nested Terminals. Shared process cost is explicitly
  labelled; the app never invents a separate window-only memory figure.
  Resource inspection and high-load advice offer releasing the visuals while
  focusing the Terminal, never stopping it. Reads share the two-second observer.
  Explicitly activating the role again reopens its workspace. Role exit closes
  a role-opened view; independently opened observers do not require a role. Terminal roster
  reads occur on role operations, never in animation ticks. Hidden cadence
  is 500 ms with no source refresh or painting. Visible native input
  pumps every 50 ms on the Terminal loop, temporarily 16 ms during finite motion;
  no extra GUI thread is created. Opening or hiding the view never dispatches
  or stops a worker.
- Profile-authored Desktop roles explicitly selected by the operator are persistent tool/lane scopes, not voice personas. Their allowlist and lane remain enforced until dismissal.
- Tray Settings conversation-role choices reuse the existing profile-role inventory, always include
  Default/empty, and show the active conversation role. Choosing a named pack
  activates its existing skill scope for text and voice and saves the same
  session metadata used by conversational activation. Reselecting Project
  Architect reopens its workspace. Default or Role active off leaves the role;
  unmatched manual text remains a bounded persona. A named active role uses
  only the shared Agent role overlay, without an additional persona banner.
- A reviewer may maintain one explicitly owned profile skill only through the approval-gated ownership tool. It gains no authority to write unrelated profile data or operate apps. Activation is visibly confirmed.
- While an owned-skill reviewer is active, domain corrections remain episodic until the reviewer stages and the user approves the exact proposal. Generic profile/term/workflow learning cannot bypass that boundary. Structured options are optional affordances, not a replacement for normal conversation.
- MCP servers combining reads and mutations are registered agent-side with an exact fail-closed `allow_tools` list. Desktop presentation cannot widen that allowlist or convert a human-panel action into conversational authority.

## MO Design

- The [artifact guide](../core/design/README.md) owns manual `design.edits` and
  portable image bundles; the [Studio guide](design_studio/README.md) owns the
  Edit design / Done flow and resource policy. Preserve user edits in normal
  MO refinement and pin the reviewed revision on every final Handoff route.
  `preview_editor.js` is composed inline by the existing renderer; it introduces
  no build system or extra runtime. Board properties and Preview editing remain
  within the same artifact and trusted bridge, with the generated frame sandboxed.
  Stop binds the displayed Design/request identity to that worker's cancellation
  event; completion releases pending state only after the worker finishes. Keep
  saved edits and unfinished revisions, and never accept a different request's
  late callback. Startup and tool failures remain visible in the same conversation.

- `core/design/schema.py` owns the strict `design/v2` `.modesign` contract; a
  document that declares any other schema is rejected;
  the explicit suffix keeps Design artifacts distinct from the `~/.mo` private
  runtime/profile home. `core/design/service.py` is the artifact writer and
  `core/design/session.py` owns the bounded non-portable conversation sidecar.
  Artifacts live atomically under `media/designs/<id>/<id>.modesign`. Unknown
  fields, oversized documents, invalid identifiers, and non-declarative shapes
  fail closed. Each sibling `session.json` may contain only validated user/MO
  messages, pending Design-request metadata, one bounded truthful activity
  phase, the last completed revision, and a bounded attention result. Never
  put that transcript, provider prose, credentials, or private profile guidance
  into the portable artifact.
- `mo_desktop/mo_renderer.py` owns the standalone pywebview process and
  `design_studio/` owns its trusted shell. The GUI process uses the GUI Python
  interpreter without console-hiding startup flags, because those flags also
  suppress the native window on Windows. The window is frameless so the trusted
  shell can match MO Files/Phone chrome; only fixed minimize, maximize/restore,
  bounded resize, and close operations cross its bridge. Drag regions and the
  bottom-right resize affordance must remain outside the generated iframe. Its
  native icon is materialized from
  `interface.desktop_brand.make_four_cube_icon`; do not introduce a second
  Design-specific logo or allow the window to inherit the Python executable.
  A terminal `view=board` launch is a presentation mode of this same renderer,
  not another app: it hides Design conversation/library/Handoff, keeps pin,
  close, resize, Download, autosave, and Board tools, and rejects minimize.
  Terminal-launched Preview carries `terminal_synced` through the existing
  Desktop summon and renderer launch/focus channels. It hides the entire
  conversation/Handoff rail and library controls, including history Handoff,
  while preserving manual editing and Download. This flag is per-open state,
  never inferred from an artifact's historical origin. Preview errors remain
  visible locally and cannot start hidden Desktop repair workers in this mode.
- The trusted shell consumes the active shared skin, panel geometry, and
  character cube preferences through `design_studio/theme.py`. The shared WebView
  adapter disables the system backdrop and detaches pywebview's OS-theme backdrop
  writer: Windows Mica paints behind the full rectangular bounds and must not fill
  MO's clipped corners. MO's skin, native region and outside-effect owner remain
  unchanged; do not replace them with child-window clipping or a second
  transparency renderer. The first frame
  receives those values before WebView paint, the loader and activity mark obey
  the configured cube count/color/corners/glow, and hidden animation stops.
  While Studio remains open, its bridge reloads those same owners when the
  configuration or active skin changes; it must not snapshot a parallel theme
  or geometry model at process start.
  The single enlarged activity line belongs below the preview; it stays static
  while idle, renders safe phases from the real worker callback while active,
  and replaces any top-toolbar or floating work indicator.
  Generated preview defaults receive the same validated tokens through CSS
  variables; do not add a second palette, character setting, or idle animation.
- Generated content stays in the artifact iframe. Its sandbox and CSP deny
  network, forms, top navigation, same-origin authority, and local files;
  generated markup is sanitized, while explicit script text runs only when
  `runtime.allow_scripts` is true. Never expose filesystem, shell, network,
  Desktop control, or general Python objects through the pywebview bridge.
- `core/design/board.py` owns the only `board/v1` validator, operation engine,
  and private working/draft sidecar. Board is trusted shell Canvas2D, never
  generated iframe content and never a second project editor. User gestures may
  write immediate private state; settled bursts use the normal Design revision
  owner. MO writes only proposal operations, and Accept/Reject remains trusted
  user chrome. Pending proposals block Board mutation and Handoff.
  Its bounded spatial summary is the placement authority for MO drafts. New
  geometry rejects material overlap by default; only an explicitly intentional
  annotation may opt in. Trusted Studio owns the discoverable keyboard mapping,
  and shortcuts must ignore text inputs and modal dialogs.
  Selected arrows expose endpoint and bend handles plus an explicit properties
  dialog; their existing points store either a straight segment or one quadratic
  curve. Board image selection imports directly through the shared attachment
  catalog and validates the current target after the picker returns, preserving
  pending chat attachments. Preview limits match the accepted image size limit.
- Read-only Desktop questions about live terminal count or focus use
  `terminal_session_candidates` as their native evidence owner. The turn receives
  only bounded, redacted status/focus/outcome summaries—never paths, instance IDs,
  or transcripts—and one immediate detail follow-up may reuse that route. The
  candidate scan is shared with `terminal_binding_status` inside the request so
  the answer does not rescan heartbeats or fall back to shell, screenshots, or
  window counting.
- Exact terminal continuation is distinct from implementation Handoff.
  `terminal-contexts` carries PID/instance-bound handoffs. Design messages are
  normal turns and the TUI must not prefix `/goal`. Dashboard controls use a
  separate typed marker in this same owner and revalidate their project on receipt;
  they never convert ordinary context into slash commands. Pickup and busy-turn queueing preserve that exact MO
  recipient, regardless of the focused workspace child; context is a normal
  turn even when its text starts with a slash. Ordinary composer routing stays
  with the selected pane. Accept/Reject uses it only for a Board-decision notice, and
  final Current-terminal Handoff reuses the same owner; Background GoalRunner and
  New-terminal startup goals remain the separate autonomous goal mechanisms.
  `terminal-boards` is the separate token-bound runtime reservation: one exact
  terminal may own one live standalone Board, same-design show focuses it,
  conflicting designs fail closed, and only the matching renderer releases it.
  Never guess a replacement origin or serialize terminal identity into
  `.modesign`. Trusted chrome shows only a bounded runtime connection label;
  the label is status, not synchronization or a content-return action. On the
  user's next linked drawing follow-up, `board_read` supplies current Board state.
  Accept or Reject returns one decision-only normal turn to the same exact live
  origin so the waiting conversation can continue; it never includes the Board
  contents, guesses another terminal, or grants implementation authority.
  Explicit Board-only open/show/read/propose calls may cross an investigate,
  review lane because they mutate only the private Design
  artifact, never the selected project. Full Preview open/show/update remains
  blocked there. MO Desktop primes the same `mo_design` capability, resolves its
  selected live terminal through the existing routing owner, and otherwise
  launches a saved unbound standalone Board with no terminal binding.
  A direct Board launch is classified as boardless work before greeting/chat
  routing, exposes only `mo_design` on Terminal and Desktop, and is complete
  only after a successful `open`/`show` event with `view=board`. The generic
  Desktop observe-after-act gate must not own or substitute for this semantic
  launch.
  Once a Board is linked, a drawing follow-up resolves `board_read` and
  `board_propose` from that exact binding and exposes no source/search tools;
  `board_read` returns the validator-owned operation contract. Add
  `computer_observe` only when the request explicitly depends on the current
  visible terminal, screen, window, or interface. Semantic Board diagrams do
  not require pixel capture.
- While Board is active, full Studio publishes `MO Design Board — …`; the
  Board-only presentation publishes `MO Board — …`. Phone pen routing requires
  one exact foreground prefix and maps normalized input to that presentation's
  stable Board-stage grid. Preview, a background window, or any other foreground
  window must fail closed.
- Preview failure diagnosis is evidence-only and bounded. Ordinary successful
  interactions are not recorded. On a script error the iframe may expose only
  capped error text and a semantic control descriptor, never form-field values.
  The bridge deduplicates the same failure per artifact revision and permits at
  most one pending Studio Design request; standalone renderers record the issue
  locally and never improvise a terminal or provider route. Diagnosis resolves
  through the same committed-revision evidence as ordinary refinement and does
  not emit an implementation-completion notification.
- `core/design/streaming.py` accepts partial provider arguments only for the
  single `mo_design` tool and publishes bounded HTML/CSS/script deltas to the
  design's private live-preview path. Token-time writes are throttled, the final
  snapshot is flushed, but trusted chrome must keep the last finished visual
  visible until the artifact revision commits. Never promote token-incomplete
  markup into the user-facing iframe. The preview refresh action reloads the
  last finished state while work is pending and the committed state while idle.
  The bridge reparses only when the committed file or live-preview stamp changes.
- `meta.revision` is per-design committed history, not a product version.
  `core/design/service.py` keeps at most 40 private snapshots. Revision selection
  is non-mutating and must drive the visible preview, source, native download,
  handoff, and next Design visual source without creating a revision. Only a
  completed Design update writes the normal next head. The existing pending
  session row binds the selected source revision so `mo_design read` and update
  cannot fall through to newer visual or conversation state. Deletion is explicit,
  confirmation-gated in Studio, and limited to a non-current snapshot. The Studio **+** action creates a clean
  sibling design/session while preserving only the optional read-only project
  binding and DNA mode. A pending request older than 15 minutes clears into a
  visible attention state without changing the artifact.
- `core/design/context.py` sends ordinary chat through one locked visual-work
  contract with the user's original words intact; it never routes from isolated
  keywords before the selected model reads the request. The model may answer,
  advise, compare, or clarify without updating the artifact; the broker accepts
  that only through the explicit `Design response:` final contract. A requested
  visual update instead requires a changed revision and may return a concise
  `Design delivered:` explanation to chat. Current-surface work
  completes one evidence pass before its first artifact update, observes current
  pixels through the existing computer/vision route when available, and verifies
  source and tests. It must not invent dynamic values, preserve proposal-only
  fields from an earlier draft, add future-state panels, or treat future/deferred
  refinement wording as present change authority. An unobserved result is labelled
  `Source reconstruction — live visual not observed`. Direct Terminal preview
  creation separately persists an explicit origin intent for its first complete
  revision: a selected project defaults to `current_state`, while `refinement`
  and `new_concept` require an explicit operator request. The adapter leaves
  content/evidence interpretation to that conversation and uses the existing
  schema and save owner; it has no separate current-state content validator.
  Automatic preview repair is the only separate deterministic request kind.
  After an artifact update, the same worker performs one bounded observation of
  one exact rendered Studio preview target once and corrects material
  discrepancies before finishing. Reuse `computer_targets`, `computer_observe`, and the text-only
  provider's bounded vision observer; never add a second Design worker, screen
  watcher, parallel perception path, or repeated capture/update loop. At most one
  complete correction and one re-observation follow the initial QA observation.
  When the preview cannot be observed,
  the result must not claim visual verification.
  Read the artifact's already mapped files and symbols before any narrow search
  and do not render source
  reports or evidence panels inside an as-is target-surface preview.
- The `mo_design update` tool accepts changed fields. The existing save owner
  retains omitted visual and brief fields from the selected revision and commits
  the complete artifact atomically. Streaming exposes bounded partial arguments;
  one bounded post-commit QA correction may save a further revision.
  Only the broker's worker-finish callback may resolve ordinary Design work, and
  it requires both a newer revision and a changed HTML/CSS/script/runtime visual
  signature. An explicit brief-only request is the narrow exception: the Handoff
  signature must actually change and the worker must return `Design brief updated`;
  arbitrary metadata remains insufficient. The bridge must never infer completion
  from a file revision.
- The provider catalog for the `mo-design` lane is limited to the existing
  visual, read-only project evidence, graph-query, perception, and `mo_design`
  tools. Do not restore the full product tool catalog here: the dispatch sandbox
  remains the hard authority, while this provider-side projection prevents
  irrelevant schemas and blocked tool round-trips from making Design heavy.
- MO Design owns no model chooser or last-used model state. At dispatch the
  broker reads the one saved Terminal `/model` preference through
  `core.provider.model_catalog.runtime_model_selection()` and revalidates it
  through the shared activation owner before starting the worker. The choice
  stays out of renderer transport, artifact/session state, argv, environment,
  generated preview content, and credential storage; with no saved preference,
  the resident Agent's validated config baseline remains active. The worker uses
  the distinct `mo_design` provider surface. A configured selection may traverse
  only the later explicit `model.default`/`model.fallback` selector, while a
  manual selection outside that chain surfaces its own failure instead of
  entering another model route without operator consent.
- The compact composer attachment button owns Design message file ingress.
  Its shared native pywebview picker stages at most eight non-empty files of at most
  20 MiB each, exposes only safe names/categories/sizes and opaque IDs to trusted
  shell JavaScript, and imports selected files through
  `core.state.attachments` only when the user sends that message. The broker
  resolves the contained catalog copies, adds only those exact paths to the
  worker's read roots, and labels content as untrusted evidence. Original source
  paths never enter generated content, command spools, or Design sidecars;
  cancel/remove leaves no imported message attachment. Board image insertion
  uses that same picker with Board intent and imports one image immediately into
  the catalog, without staging or consuming message attachments.
- Design work and implementation are different intents. Ordinary Design chat has
  exactly one route: Studio. It runs in the `mo-design` sandbox with the selected
  project as a read-only workspace and may mutate only the same private Design
  artifact through validated `mo_design` actions. It never targets Desktop, a
  terminal, or Background, and it never emits a completion notice. A completed
  turn without either an explicit conversational response or a newer changed
  visual clears into a visible attention state instead of announcing a false
  update. A new app/product/showcase request defaults to an interactive prototype
  unless the operator explicitly asks for a static concept; it must be described
  as a prototype, expose working primary controls and proportionate screens/states,
  and populate meaningful objective/acceptance/decisions/evidence in the same
  revision. Existing-project Design work must make the
  preview and brief source-aware: verified files, symbols, reusable mechanisms,
  and preserved invariants are evidence, while graph context remains orientation.
- In Studio, explicit **Handoff** creates implementation authority. An explicit
  instruction to implement an identified design in its ordinary Terminal
  conversation already authorizes that Terminal; it needs no redundant Studio
  transfer. A saved Terminal or historical revision does not require a Studio
  worker-completion marker to be handed off, and Handoff never attests visual QA.
  Pending Studio work and unresolved attention still block the route sheet.
  Ordinary **Send**
  remains inside the iterative Design conversation; Handoff is the eventual
  final step, never a per-message destination choice. Its compact sheet recaps
  the actual saved title, summary, revision, Static/Interactive state, optional
  brief, and project context, then asks only which MO recipient gets that work.
  Objective, acceptance, decisions, constraints, and verified mappings are
  context generated during Design, not a final intake form or blocking checklist.
  A pending Board proposal must still be accepted or rejected because only the
  user can settle that draft. The sheet offers Current terminal, Background, and
  New terminal. Desktop is the
  tray/window/broker host and notification fallback, never an agent destination.
- Current terminal first revalidates the exact terminal that opened the Design
  and, only when no origin was recorded, uses the existing safe live-terminal
  resolver, then queues one
  PID-bound normal conversation turn for that instance. It must never prefix or
  simulate `/goal`. Background launches the canonical headless GoalRunner only
  for an explicit safe project and is the only route whose completion uses the
  existing cube-first then tray/platform notifier; its action focuses the exact
  saved design. New terminal opens one visible interactive MO with an injected
  `/goal` and no background-completion notice. If no project was selected, its
  prompt requires target resolution before writes; none of the routes may treat
  an unrelated cwd as implementation authority or accept executable content from
  the artifact.
- The authenticated command spool connects the separate renderer to the resident
  Desktop broker. Current-terminal normal-turn pickup and background completion reuse
  bounded existing loops and private one-shot files; they add no per-design
  process watcher. A signed, renderer-PID-addressed focus request switches only
  the requested artifact window. Closing Desktop invalidates its
  in-memory secret, stops the broker, and terminates only renderer children it
  owns. Companion tracks its workspace separately from explicit artifact children
  by path and presentation, prunes exited handles on open, and reuses a matching
  window. Tray focuses that workspace without touching other standalone or
  terminal-linked work. Welcome creates no artifact until Start; New/Open enter
  the session in the same renderer, and its saved library is read on demand.
  Terminal-linked presentation uses the dispatcher's actual conversation surface,
  never the process's launch name; a Desktop-launched terminal is still terminal.
  Board geometry never reads a disposed native handle; renderer shutdown must
  still release its exact terminal binding after the native window closes.
  The trusted switcher loads
  another `media/designs/<id>/<id>.modesign` document in that process, while
  project choose/clear mutates only the active artifact through
  `core.design.service`; generated content never receives the native picker.
