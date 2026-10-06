# MO Shell

MO Shell is a separate Windows host for the existing MO terminal. It opens as
a movable two-cube pair; clicking the pair expands a titlebar-free terminal
with the cube pair at the Shell's inside top-left and the existing icon strip
at its inside top-right. The space between them is an invisible drag handle for
the whole window, not a second visible title bar. The transcript remains
full-height with a small top inset, and frame outlines are deliberately faint.
A translucent, skin-colored gradient behind each control group dims terminal
text as it scrolls beneath the controls and fades back to clear; it reuses the
native painter, not a blur or capture layer. The attached application's top
inset keeps the header controls and drag handle reachable. The strip opens
the window picker, changes the attachment mode, adjusts terminal zoom,
maximizes/restores, collapses, or closes the host. Each control exposes a short native tooltip.
The picker uses each opened window's Windows icon instead of duplicating its
name in another text control. Ordinary taskbar windows remain first; tray and
utility windows that expose a visible attachable surface are grouped below a
labeled separator. DWM-cloaked placeholders are omitted because Windows cannot
render them in the pane. The divider changes the terminal/attachment split.
Clicking the cube pair again hides the selected window and returns the host
to its compact form. Layout changes once while a short, eased visual fold connects
the two states. Text stays at its natural size while the rounded body reveals it;
the terminal and attached app are not resized on each animation frame.
Dragging the pair moves the whole group. The expanded
Shell resizes from the visible terminal-body edges and corners rather than from
its transparent rectangular bounds. While a window is attached, its outer top,
right, and bottom inset preserves that same native resize hit strip instead of
covering it. A new attachment starts with the two panes evenly divided and
remains directly adjustable from the divider. If the selected application
enforces a larger native minimum, the existing divider owner adopts that exact
width and height so the application stays inside the Shell instead of spilling
past it. Move, resize, restore, and compact/expanded transitions stay inside the
current monitor's Windows work area. Display and work-area changes refit the
existing form; maximized mode continues to fill that area. A target whose native
minimum cannot fit alongside the terminal is restored and rejected with a visible
error.

Dragging exactly one Windows executable (`.exe`) or Windows shortcut (`.lnk`)
that resolves to an existing `.exe` onto the compact pair is the other explicit
attachment path. The pair reuses Desktop's file-drop gape, twitch, and swallow
motion, launches the file, then uses the existing managed-window owner and
expands once when the exact executable window appears. A shortcut is
launched as the shortcut so Windows preserves its arguments and working
directory. Shell uses the process identity Windows returns from that launch,
falling back to the shortcut's resolved executable only when Windows returns no
process identity. Exact PID or executable path plus the pre-launch HWND set
covers direct launches, launcher shortcuts, and single-instance applications
without guessing by title or process name. Other files and ambiguous windows
are ignored. The launched process is not killed or persisted by Shell.

This is presentation, not another Agent. The child process is the canonical
mo.py entry point inside the existing ConPTY implementation. Agent, Gateway,
sessions, tools, task evidence, and provider configuration stay in their
current owners.

Multiple collapsed Shells form a compact group with a luminous connection. A new Shell keeps
its own terminal and attachment, takes a lighter or darker shade of the active character
color, and appears above the earlier Shell. Drag a pair or its connection to
move the group; hold a pair briefly and drag to pull that Shell out. Move collapsed
controls close together to combine them again. Clicking a member opens only
its terminal; collapsing it returns to its surviving group. Hover shows the
same **MO Agent** title and attached-window title used by a single Shell.
The fold lands directly in its group slot, and opening one member keeps the
remaining pair in place.
The luminous connection follows the actual cube positions during extraction,
carrying a short charge before thinning and fading. This is visual feedback only;
it does not change the hold, movement, release, or nearby-merge gestures.

When every grouped Shell is idle and there has been no input for 30 seconds,
a brief electric pulse travels through the connection and lights both pairs,
then settles. The connection remains softly visible without continuous motion.
There is no filled container, extra cube, or expanding scene. The footprint and
Desktop's skin-defined cube size stay fixed. Touch or terminal work cancels the
charge through the existing surface clock; it runs once per idle period.

A launcher may hand the next Shell's first terminal its start: MO Desktop writes a one-shot
file of allowlisted `mo.py` flags (`--startup-goal-file`, `--startup-turn-file`, `--startup-panes`) and names it in
`MO_SHELL_STARTUP_FILE`; the first bridge renames it before reading, so one terminal gets
it and every later Shell opens normally.

## Window modes

- **Managed** is the default. The selected top-level window keeps its native
  chrome while MO Shell temporarily becomes its Windows owner and positions,
  sizes, hides, restores, and detaches it as part of one focus and z-order
  group. The selected window appears immediately above the Shell; minimizing
  and restoring is unnecessary. Native move/resize attempts, including
  a title-bar drag, are corrected in the location-event dispatch rather than a
  later UI-queue turn, avoiding a visible away-and-back blink. The target keeps
  the owner-established z-order while pane resizing changes only
  its bounds, so the Shell does not cover it during the resize.
  A minimized or maximized selection is shown at the attachment size and
  returns to its exact
  prior Windows placement state when detached. Visible tray/tool windows also
  retain their exact normal rectangle and topmost state, including after Shell
  has collapsed and temporarily promoted its owned window.
- **Embedded** is explicit. MO Shell reparents a compatible Win32 window and
  removes its top-level chrome while attached. Windows may reject this for
  elevated, modern, GPU-heavy, or DPI-incompatible applications; failure is
  shown in the shell and the original parent, styles, rectangle, and visibility
  are restored.

Only one selected external window is owned at a time, and two Shell instances
cannot claim the same window. A rejected claim leaves the previous attachment
intact; the live reservation is released on detach, target closure, or process
exit and is never persisted. The two-cube pair is the
Shell's visual handle: the first cube represents the MO terminal and the second
represents the attachment side, dimmed while it is empty. It reuses the Desktop
character's material without duplicating its 2-by-2 silhouette. Window handles are
session-bound and are never persisted across restarts.

The canonical terminal receives a bounded live orientation fact for that one
selection. MO can therefore refer to the window currently available in its own
Shell by the current Windows title. The title is untrusted availability context,
not evidence that MO observed the window's pixels or contents; content claims
still use the existing computer-observation path. No attachment is copied into
memory, the graph, task truth, or another surface.

## Visual ownership

MO Shell uses the semantic payload produced by mo_desktop.visuals; it defines
no MO skin or skin selector. mo_shell.bridge refreshes the persisted canonical
skin across the process boundary, so /skin changes repaint the already-open
host. The same payload carries Desktop typography, the configured MO character,
its canonical form ratio, and the shared four-cube taskbar icon. The Character
Size control in Desktop Settings therefore sizes the Shell pair too; no
Shell-only scale is persisted. The native form derives its visible cube,
control, body, and attachment regions from those same metrics. Its body remains
opaque behind a managed window, so native frame margins never expose another
application through the Shell. The existing Desktop outside-effect renderer
follows the expanded body or compact pair, with a transparent interior, so the
configured shadow/glow remains soft without covering an attached window or
inventing another effect.
The native surface retains the last valid payload if a later refresh fails.

MO Shell renders the existing terminal screen's styled fragments, including
Unicode cell widths and ANSI foreground/emphasis. Its shared Desktop
mono font is measured and painted through one native text path; emoji-bearing
runs use the native Windows emoji face while retaining the bridge-provided cell
width. Styled glyphs therefore follow the same grid without compressing the
font during a resize.
Terminal cell backgrounds do not create a second set of rectangular canvases;
the Shell body keeps the canonical background role. Because
this surface projects the current VT screen rather than a
terminal emulator's native scrollback, its existing host identity selects MO's
existing managed transcript viewport. Resizing or attaching a window therefore
repaints committed rows instead of reflowing them above the visible screen;
the canonical TUI fills this private projected screen so its footer stays on
the final row. Ordinary MO terminals retain their inline layout and configured
native-scrollback behavior. Mouse-wheel movement over the Shell terminal is
forwarded as the standard PageUp/PageDown input already owned by that managed
viewport. Terminal-standard modified navigation keys keep the existing
workspace shortcuts in their TUI owner. Terminal zoom stays inside the fixed
Shell frame. The visible attachment divider uses a wider interaction target
without changing its painted width. MO Shell is also an ordinary
**MO Shell** action in the existing MO Desktop tray; the tray does not become
another Shell process, renderer, or session owner.

`Ctrl+V` pastes Unicode clipboard text (retrying briefly while a clipboard history holds
the clipboard just after a copy) through the existing terminal input route
as one bracketed paste, so multiline or large text remains under the canonical
TUI's unsent composer/holder behavior. Empty or unavailable clipboard text does
not send a raw control byte. `Ctrl+C` copies a selected terminal range and
otherwise remains the terminal interrupt key.

`F6` moves between terminal input and the control strip. Within the strip,
arrows or `Tab` select a control and `Enter` or `Space` activates it; `Escape`
returns to terminal input. The window picker uses arrows, `Home`, `End`, and
`Tab` to select windows, `Enter` to attach, and `Escape` to close. The compact
handle expands with `Enter` or `Space`. Windows accessibility exposes the
same controls and window choices, including their current enabled states.
The compact window grows with Character Size so both cubes remain visible.

The Shell identity reuses the canonical OSC title's existing active-work bit.
Work runs the existing cadence across the pair's skin-colored inset frames;
idle is still. Hover brightens both cubes. No status bubble, side icon, activity
service, or second toolbar is introduced. The compact pair stays topmost,
so it remains available,
while the expanded Shell returns to ordinary window order. Hovering the compact
pair quickly fades an unboxed title in on its right: **MO Agent**, or
**MO Agent + &lt;attached window&gt;** when a window is selected. The title reuses
the delivered Desktop `body` typography role, text color, and spacing. It has
no background box or outline. The compact surface uses per-pixel alpha for
smooth cube edges, soft light, and transparent space between the connected pairs.
Hover does not enlarge a background or surrounding effect.

The toolbar's maximize/restore control uses the current display work area and
keeps the same Shell renderer, attachment, region, and effect owners. Collapse
and expansion publish only their final compact or expanded geometry. There is
no bounds-animation owner or intermediate resized frame that can stretch,
duplicate, or offset the cube identity or terminal pixels.

Windows taskbar Jump Lists belong to the application whose icon was clicked.
MO Shell therefore does not register an attach command inside Chrome, Explorer,
or another application's taskbar menu. Explicit attachment remains in the
Shell's icon picker or one-application drop, where the selected HWND can be
captured and restored by the existing attachment owner.

The native host is deliberately C#/.NET Windows Forms plus Win32. This meets
the current titlebar-free surface, double-buffered rendering, HWND, and
ConPTY requirements without adding Windows App SDK as a product dependency.
The framework choice is not a second layout or window owner.

## Build and run

On Windows with .NET 8:

    dotnet build mo_shell/native/MoShell.Native.csproj -c Release
    python -m mo_shell

A launch whose native build is missing or older than its C# source builds it
first with the installed .NET 8 SDK (MO Desktop does this in the background and
says "Updating MO Shell" on the cubes), so an update never leaves MO Shell
unavailable; without the SDK the launch names the build command instead.

An explicit profile remains available through python -m mo_shell --config
PATH. Otherwise the canonical MO_CONFIG/default-profile resolution is
inherited unchanged.

MO_PYTHON may select a different Python executable and MO_PROJECT_CWD may
select the working project when launching the native executable directly.

See [MAINTAINING.md](MAINTAINING.md) for invariants,
[PROTOCOL.md](PROTOCOL.md) for the native bridge, and
[native/README.md](native/README.md) for the build boundary.
