# MO Shell native host

This is the sole MO Shell layout and selected-window owner. It is a
titlebar-free .NET 8 Windows Forms/Win32 process with a double-buffered
two-cube terminal/attachment handle,
one synchronous expansion/collapse layout commit with a short eased visual fold,
compact icon controls with native tooltips, a
picker using real Windows application icons with taskbar candidates before one
tray/utility section, one terminal/attachment divider, real
terminal zoom, and reversible managed or embedded HWND control. The normal
Windows taskbar identity uses the canonical MO icon and **MO Shell** title.
Managed targets temporarily use Shell as their Windows owner and remain locked
to the pane through one target-scoped native location event, so focus and their
own title bar cannot separate them from the Shell group.

The compact pair also accepts exactly one dropped `.exe` or `.lnk` resolving to
an existing `.exe`. Its existing surface timer reuses Desktop's
gape/twitch/swallow language; Program launches the file, resolves only the exact
PID or executable path returned by Windows through the existing enumerator and
attachment watcher, and expands once the exact window is attached. A
shortcut is launched as-is so Windows preserves its arguments and working
directory; its resolved target is only the fallback when the launch returns no
process identity. No other file type, title/process-name guess, second poller,
or persisted launch registry is involved.

Windows Forms is intentional here: the requested behavior is available from
the installed .NET desktop runtime and Win32 without introducing Windows App
SDK or another rendering/windowing layer. MO skins still come only from the
canonical Python visual projection. Styled terminal fragments come from the
existing TerminalScreen projection, and the shared Desktop mono font is measured
and painted through one native text path. Emoji-bearing runs select the native
Windows emoji face but keep TerminalScreen's projected cell width. The body owns
one canonical background while terminal foreground and emphasis remain projected. The native
form's one Windows region is derived from the same panel and character metrics;
one rounded-path helper and inset outlines keep visible edges inside that
region. In expanded mode the adjacent cube pair sits inside the Shell's top-left
and the icon strip inside its top-right. One invisible header hit area reuses
the existing whole-form movement path; controls, cube collapse clicks, outer
resize grips, and transcript selection below it retain their own hit targets.
The terminal rectangle starts immediately below the shared header and keeps
the full bottom extent. Its small inner inset is shared by painting, cursor
placement, selection and the projected grid.
Frame strokes use faint alpha rather than another skin color. The attached
application uses that same content top, so it cannot cover the controls or drag
handle. The existing painter draws terminal text first, then clipped
skin-colored fades beneath the two control groups and the translucent toolbar
background, without live blur, capture, or another effect owner. The body stays
opaque behind managed targets so window margins do not reveal unrelated
applications; embedded children remain inside the host region. The bridge
reuses Desktop's existing passive outside-effect renderer for the expanded
body and compact pair rather than implementing another shadow or glow renderer
here.

`ShellTransitions.cs` reuses the compact alpha blitter and surface animation clock
for fold and extraction visuals. A single temporary, passive layer cannot take
focus or input. Fold snapshots contain only Shell-owned pixels; the real attached
window is hidden during the fold and revealed once at the settled expanded bounds.
The native frame follows Desktop's rounded-shell/content-reveal pattern without
scaling text. The real form is hidden before its single layout commit. Group
return geometry comes from the actual destination slot, and membership motion
starts at current screen positions so a separate landing jump is unnecessary.
The connection uses the same skin-colored filament painter in grouped and released
states, follows both native cube positions, and stops after a finite fade. No
extraction threshold, terminal lifecycle, or attachment ownership depends on it.

A Desktop launch restores the ordinary four cubes first. Once Shell has its
native window, theme and terminal bridge ready, it requests two current source sprites through the
existing launch pipes. The environment carries only a deferred-source marker;
startup never freezes an earlier cube pose. Shell centers its expanded bounds
on the source monitor's work area and keeps its initial pair alignment. Each
Desktop launch creates an independent Shell; running-app rows focus its exact
process. Shell supplies its own final cube pixels and screen geometry to the existing
Python visual bridge. That bridge drives the finite separation/fall entrance
on its normal loop, reusing Desktop's fading trace painter and passive layered
window. The first published frame acknowledges the captured pose, immediately
releasing the four cubes. The final frame stays visible until the native pair
acknowledges reveal. Completion, failure and early exit use the same stdout
receipt; a failed source read restores the native surface. Launch checks the
selected native DLL against source timestamps so a temporary test build cannot
silently leave the normal executable stale.
After native source changes, isolated verification builds do not refresh the
launcher executable. Build the normal Release output with the command below
before handing the app back for use, and verify normal startup. Preserve
the freshness check; an isolated test pass is not evidence that the launcher's
binary is current.

The surface uses one native resize-grip width for hit-testing and the outer
attachment edges. The top inset also clears the existing header bounds. A
selected window therefore cannot cover the Shell controls, drag handle, or
edges that resize the combined window. Its divider-side frame
remains one pixel, and split sizing stays with the existing divider owner.
The borderless form retains Windows' `WS_THICKFRAME` style so those hit tests
enter the native resize loop. Its nonclient size calculation keeps the client
at the outer window edge; otherwise Windows adds a frame inset and clips the
compact cubes and expanded surface.

Collapsed grouping lives in the existing form/surface partials, ShellGrouping.cs
and ShellGroupSurface.cs. Only controls combine; each original native form,
bridge, terminal, and attachment stays independent. The same pair painter and
hover-title painter render the members, with shades of the active character color and a brief
fixed-size electric connection and brief idle charge. The [maintenance contract](../MAINTAINING.md) owns the peer boundary,
capture lifecycle, finite idle conditions, and verification requirements.

The same surface consumes the bridge screen event's bounded `busy` hint and
moves the existing cadence across the active-skin inset frames and cube fill.
The attachment cube is dim while empty, a lone idle pair stays still, and hover brightens
the pair; there is no state bubble or second activity owner. The compact form
stays topmost and its existing surface timer quickly fades a vertically centered, unboxed title to the right showing **MO Agent** plus the selected window title only
while the pair is hovered. The pair consumes Desktop's existing Character Size
and canonical cube edge ratio. The title uses the delivered Desktop `body`
typography role, text color, and spacing. `ShellComposition.cs` presents the
same painter on the same HWND through per-pixel alpha, using coalesced surface
invalidations. It adds no animation clock or input surface. Expanded rendering
releases the layered style according to the
[Win32 layered-window contract](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features#layered-windows).

The same control strip exposes maximize/restore through the form's existing
work-area geometry owner. Managed targets that enforce a native minimum report
their actual width and height back to the existing split calculation. The form's
existing work-area bounds owner limits move, resize, restore, and expansion,
and handles Windows display/settings notifications without a separate watcher.
Maximized mode refills the current work area after those changes; an incompatible
minimum is rejected and restored instead of overflowing the monitor. One process-lifetime kernel reservation excludes duplicate HWND claims
across Shell instances without a persisted registry or second attachment owner.

The painted terminal accepts mouse drag selection directly. `Ctrl+C` copies an
active selection and otherwise remains the terminal interrupt key. `Ctrl+V`
reads Unicode clipboard text and forwards one bracketed paste through the same
terminal transport; the canonical TUI owns paste normalization, limits and
unsent large-paste state.

Keyboard and Windows accessibility access reuse the surface's painted control
bounds, labels, enabled states, and actions; the [Shell guide](../README.md)
owns the key bindings. Compact geometry comes from those same cube metrics.

The form also publishes its one live selected HWND as a Win32 property and
passes only its own HWND to the exact bridge child. The Python side can expose
the current bounded window title through MO's existing environment context;
there is no persisted attachment registry or second agent/session owner.

Build:

    dotnet build mo_shell/native/MoShell.Native.csproj -c Release

Run through python -m mo_shell so the native process receives the active
Python executable, project working directory and MO's checkout (`MO_AGENT_ROOT`,
where the bridge starts so it imports from any project). Direct launches may set
MO_PYTHON, MO_PROJECT_CWD and MO_AGENT_ROOT.
