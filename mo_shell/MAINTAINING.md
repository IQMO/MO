# MO Shell Maintenance Contract

Read the root AGENTS.md first. MO Shell owns only its native presentation,
selected-window lifecycle, and the transport to one existing MO terminal.

## Owners

- native/ShellSurface.cs owns pixels and direct interaction: the two-cube
  terminal/attachment handle, its hover title, exact visible window region,
  compact icon strip, window picker,
  terminal/attachment rectangles, terminal zoom, and divider.
  Its ShellGroupSurface.cs partial reuses the same pair painter, hover label,
  theme metrics, accessibility bounds, and finite animation timer for collapsed
  groups, their luminous connections and finite idle charge.
  Control size, spacing and hover treatment come from `WINDOW_CHROME` through
  `mo_desktop.visuals.desktop_visual_payload()` and `ThemeState.cs`. The native
  painter is an adapter for the Shell control strip, not a second title bar.
  Its finite control hover joins the existing identity animation clock; it
  must not add an idle timer or alter collapse/attachment semantics.
- native/WindowAttachment.cs owns visible-window enumeration and one
  reversible managed or embedded HWND transaction. Picker identity comes from
  the selected window's real Windows icon, with the executable-associated icon
  as the bounded fallback. Its target-scoped Windows location event keeps a
  managed selection inside the pane without a polling loop.
- native/Program.cs owns the shell form, compact/expanded switch, group
  movement, attachment synchronization, effect settle cadence, and UTF-8
  bridge process.
  Its ShellGrouping.cs partial coordinates collapsed native peers with transient
  HWND properties and bounded messages validated against the same executable.
  The visible member paints the group; each hidden member retains its original
  form, terminal, and attachment. No group service or persisted registry exists.
  Its ShellTransitions.cs partial owns only the finite presentation fold and
  connection release, sharing ShellComposition.cs's alpha blitter and the existing
  surface clock. One passive layer is reused, hidden when settled and disposed on
  exit; it has no input, focus, timer, or independent layout authority.
- bridge.py owns one canonical mo.py ConPTY child, its bounded VT projection,
  canonical styled VT projection, semantic theme delivery, and the thin body
  geometry adapter to Desktop's shared outside-effect owner.

There is no parallel Python pane model, controller, HWND adapter, or persisted
raw-window state. Agent, Gateway, sessions, task evidence, provider selection,
and Desktop skin definitions remain outside this package.

## Invariants

- An external window is attached only after the operator selects it from the
  visible titled-window list or drops exactly one `.exe` or `.lnk` resolving to
  an existing `.exe` onto the compact pair. The drop path launches that file
  and accepts only the exact PID or executable path Windows returns from the
  launch, one newly visible HWND with that path, or the sole exact-path window
  after a single-instance launcher exits. The resolved shortcut target is the
  fallback only when Windows returns no process identity. Never guess by title
  or process name, accept other file types or non-executable shortcuts, or
  persist the path or HWND.
- Managed mode is the safe default and temporarily assigns the Shell as the
  selected top-level window's Windows owner. Embedded mode is explicit. Both
  snapshot owner/parent, styles, extended styles, exact rectangle, Windows
  placement, and visibility before mutation. Placement preserves
  minimized/maximized state; the rectangle and extended style preserve exact
  normal/tool-window geometry and topmost state. Selections are normalized only
  while attached, then return to that captured state on detach.
- A managed target remains in the Shell focus and z-order group while attached.
  Initial attachment establishes the target above its owner once, so it is
  visible immediately without a minimize/restore cycle. Detach explicitly
  restores the original topmost level, including after a compact Shell promoted
  its owned target.
  Pane resizing preserves that owner-established z-order instead of repeatedly
  ordering the target relative to its owner.
  Its exact target-scoped
  WinEvent is the only move/resize listener; a native title-bar drag or
  application-driven geometry change resynchronizes the existing pane in that
  event dispatch rather than posting a later visible correction or creating a
  second layout owner.
- The picker derives taskbar versus tray/utility grouping from current Win32
  ownership and extended styles. It keeps ordinary taskbar windows first and
  renders the remaining candidates below one separator; it never hardcodes
  application names. DWM-cloaked placeholders are not visible attachable
  windows and stay out of both sections.
- Failed embedded attachment rolls back the snapshot. Detach and shell exit
  restore it. Closing the selected application removes only that attachment.
- Collapse hides the selected window and collapse/expansion switch the native
  form, surface state, bounds, and region synchronously. A 180 ms eased alpha
  fold follows Desktop's `card.fold_frame` pattern: a rounded shell reveals
  natural-size content while the canonical pair travels without stretching.
  The real form is hidden before changing layout, so it cannot flash its target
  geometry before the fold. The native layout and terminal grid are committed
  once. At completion the actual form
  and expanded attachment are revealed. The shared clock uses its 16 ms budget
  only during these finite presentation transitions and returns to 33 ms afterward.
  Shell/divider movement resynchronizes the selection, and the terminal process
  is not rebuilt.
- Window handles are never persisted. One shell session owns at most one
  external window.
- The native form publishes that one live target HWND as
  `MO_SHELL_ATTACHED_WINDOW_V1` on its own HWND. Only the Shell bridge process
  tree inherits `MO_SHELL_HOST_HWND`; `mo_shell.context` validates both handles
  and contributes the current bounded Windows title to the existing surface
  environment context. This is untrusted availability orientation, never pixel
  evidence, task truth, memory, graph input, or cross-surface state.
- The native renderer accepts only a complete canonical semantic theme
  payload, typography, character settings, and canonical taskbar icon. It
  defines no MO palette, font fallback table, or second brand mark; the one
  Windows-native emoji face is selected only for bridge-marked emoji runs.
  Equal semantic payloads do not repaint or regenerate the icon.
- Shell working state comes only from the canonical animated OSC title already
  projected by `TerminalScreen`. `ShellSurface` applies that existing cadence
  to the two inset cube frames and their canonical-color fill, and keeps no
  persisted activity state. Collapsed peers publish only that same live busy
  bit for group presentation; the group never infers Agent task status.
- Compact identity is one horizontal pair: the first cube is the MO terminal
  and the second is the attachment side, dimmed while empty. It reuses the
  configured Desktop cube size, canonical form ratio, color, rounding, and glow
  without duplicating the
  2-by-2 Desktop character, sampling attachment pixels, or adding side
  witnesses or a status bubble. Hover brightens the pair and uses the existing
  surface animation timer to fade a vertically centered title using Desktop's canonical
  `body` typography role to its right; no tooltip, local font size, or second
  compact window owns that identity.
- `native/ShellComposition.cs` presents the existing compact painter through
  per-pixel alpha on the same native form. It coalesces surface invalidations,
  owns no timer, and releases the layered style before expanded rendering.
  Region rectangles bound input; pixel alpha owns rounded edge coverage and
  transparent-area hit testing. Hover text has no panel fill or outline, keeps
  its natural font height, and never enlarges the surrounding window effect.
- Additional collapsed Shells derive a noticeable lighter/darker shade of the current character
  color, appear above the first, and join its compact linked group. Only collapsed
  controls group; expanded terminals remain independent. Dragging a pair or connection moves
  the group; hold a member briefly then drag extracts that Shell. The originating
  UI thread retains mouse capture through release, and the revealed native peer
  does not activate and steal it. Dropping nearby merges collapsed controls.
  A leader exit releases or hands off the remaining controls without terminating
  their bridges. Each hovered member uses the existing identity/title painter.
  Opening a member preserves the remaining pair's screen position. Collapse
  targets the surviving group's actual slot directly; joining starts from the
  incoming pair's current pixels instead of a second displaced landing pose.
  Extraction keeps the same filament painter, mixed skin color, and continuous
  charge phase. Both endpoints follow the actual cube geometry, including leader
  handoff. The release weakens over at most 550 ms with a bounded visual extent;
  that extent never determines extraction or merge behavior.
- After 30 seconds without input, hovering, or any grouped terminal work, the
  existing surface clock sends one brief electric charge through the connections
  and brightens the original pairs. A static skin-colored connection and local
  cube glow remain between pulses. No filled container, group-wide background
  effect, extra cube, nod displacement, or scene renderer remains. The footprint
  and cube count stay fixed; edges use Desktop's character size times form ratio
  without button padding. Touch or work cancels within 140 ms. The frame clock
  stops afterward, until another interaction starts a new idle period.
- A valid application drag reuses that surface's existing animation timer and
  Desktop's gape, twitch, and swallow motion constants. Invalid drags do not
  animate or launch. Program reuses its existing attachment watcher to resolve
  the launched window and the same synchronous state switch to expand; it owns no
  second drag renderer, process poller, or attachment transaction.
- Another application's Windows taskbar Jump List is outside Shell ownership.
  Shell does not modify or hook foreign AppUserModelIDs; explicit selection
  remains in the existing surface-owned picker.
- The JSON-lines bridge accepts terminal input, resize, snapshot, close, and
  the Shell form's bounded owned-surface geometry. It is not an arbitrary window-control
  API.
- Native input preserves terminal-standard modifier sequences and forwards them
  to the existing TUI key bindings. `F6` selects the surface's existing control
  strip; its keyboard navigation and Windows accessibility children reuse the
  painted bounds, labels, enabled states, and mouse actions. Picker navigation
  skips group headings and scrolls the selected window into view.
  The same painted terminal surface owns mouse drag selection; `Ctrl+C` copies
  an active selection and otherwise retains its terminal interrupt meaning.
- The compact form takes its minimum size from the surface's existing cube
  geometry, including the identity margin; changing character size must not
  clip either cube or shift it when the hover label opens.
- Terminal zoom changes the transcript font and grid without resizing the
  Shell form.
- The bridge's existing `MO_SHELL_TERMINAL` host identity selects MO's existing
  managed transcript viewport. Shell projects only the current VT screen and
  cannot expose a terminal emulator's native scrollback; selecting that existing
  path keeps committed rows repaintable across pane-width changes. The same
  identity makes the canonical TUI fill this private projected screen so its
  footer occupies the final row, while ordinary terminals retain their inline
  layout, native scrollback, and profile behavior.
- The canonical VT projection trims trailing blank cells, and Shell paints no
  competing per-cell background canvases. Native measurement and drawing use
  one GDI path and the shared Desktop mono role; a temporarily smaller surface
  clips complete rows from the top instead of compressing glyph height.
- The native form exposes only the cube pair, control strip, terminal body,
  divider-side attachment frame, reachable outer resize frame, and divider in
  its Windows region. Empty header space remains a real opening. The body stays
  opaque behind managed targets;
  embedded children remain inside the host region. Desktop's existing passive
  effect layer follows the expanded body or compact pair, keeps its interior
  transparent, and pumps its native messages in the bridge; it never fills the
  header opening.
- The expanded form has no transparency key. Its settled painted body is opaque;
  wallpaper remains visible through the intentionally absent header regions.
  Compact mode uses per-pixel alpha, and the finite fold temporarily hides the
  form while the passive layer presents its transition.
- The body fill and form region use the same rounded-path helper. The one-pixel
  body frame and control-strip outline are inset so region clipping cannot cut
  away the outer half of a corner stroke.
- The surface's single edge hit-test hands a body-frame drag directly to the
  native form resize loop. Its one resize-grip width is also the attachment
  inset on the outer top, right, and bottom edges, so the selected window cannot
  cover those hit targets. All four edges and corners retain native Windows
  resize behavior while compact mode remains a draggable topmost cube pair.
- The attachment divider keeps one layout owner: its interaction target may be
  wider than the painted line, but dragging updates the existing split state.
  After a selected native application clamps a requested pane width, the HWND
  adapter reports its actual rectangle and this same owner adopts that minimum;
  no application-specific size table or parallel pane model is allowed.
- The titlebar-free toolbar's maximize/restore action uses the current Windows
  work area through Program's existing form owner. A maximized surface disables
  edge resize and header dragging while preserving collapse, attachment, region,
  terminal, and outside-effect ownership.
- The centered picker remains surface-owned. While it is open, Program hides
  the attached target through the existing attachment lifecycle and restores it
  through the normal show/sync path so the owned window cannot cover the picker.
- The Desktop tray action lazily launches this separate owner with the active
  config. It does not import Shell rendering into Desktop or share a terminal
  session. Each launch may create another Shell; every bridge supplies its own
  `MO_INSTANCE_ID` and canonical session slot. Desktop's running list focuses
  exact live Shell processes rather than replaying their launch action.
  Initial expanded bounds center on the current cube monitor's work area,
  carried in the existing fresh-source receipt; source cube coordinates own
  only the entrance path. The pair keeps its initial header alignment.

## Verification

During iteration, build the native project and run only the shell/bridge tests.
The final Windows acceptance for a changed interaction verifies:

1. compact topmost pair launch, movement drag, one-`.exe` and one executable-`.lnk`
   drop reaction and managed attach/expansion, rejection of a non-application
   drop, idle/working frame motion, Desktop Character
   Size updates, quick hover-title fade with `MO Agent` plus attached-window title,
   clean expansion/collapse without a copied or offset intermediate frame,
   maximize/restore, toolbar tooltips, and clean close;
2. one canonical MO terminal rendered with Unicode widths and ANSI styles;
3. explicit picker selection and managed attach/move/resize/hide/restore/detach,
   including a native target title-bar drag that remains locked to the pane;
4. explicit embedded attach of a compatible Win32 target and visible,
   reversible failure for an incompatible target;
5. divider resizing both panes and the ConPTY projection;
6. /skin repainting the already-open host;
7. target closure removing only its attachment;
8. the normal Windows taskbar showing the MO icon and **MO Shell** name, plus
   one launch from the existing Desktop tray.

Reuse an unchanged acceptance result. Do not turn device-boundary checks into
per-edit ceremony.
