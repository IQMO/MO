# MO Shell

MO Shell is a Windows host for the MO terminal alongside one selected
application window. It starts as a movable two-cube pair. Click the pair to
expand the terminal; click it again to collapse and hide the attached window.
The first cube represents MO and the second the attachment, dimmed when empty.
Each Shell has its own terminal conversation and attachment.

## Open and control Shell

Use **MO Shell** in the MO Desktop tray. Where folder integration is configured,
**Open with MO** opens one Shell in that folder; the command is
`mo --shell <folder>`. Shell is included in the
[Windows preview](../README.md#installation-availability); the unfinished
Windows 11 main-menu integration is excluded and remains deferred.

The expanded window has the cube pair at the top-left and controls at the
top-right. Drag the space between them to move the whole window. Resize from
the outer edges or corners. The control strip opens the window picker, changes
attachment mode, adjusts terminal zoom, maximizes/restores, collapses or closes
Shell. Each control has a short tooltip.

The strip fades when the pointer enters terminal content. It returns over the
header, with `F6`, or briefly after activation and restore. An open picker or
keyboard control selection keeps it visible; the cube pair remains available.
The divider changes the terminal/application split. A new attachment starts
with the panes evenly divided. Terminal zoom changes the transcript size within
the existing Shell frame.

Move, resize, restore and expansion stay within the current display's Windows
work area. Maximized Shell follows display/work-area changes. If an application
requires more space than can fit alongside the terminal, Shell shows an error
and restores it rather than placing it outside the display.

## Attach an application

Choose a window in the picker, which shows its Windows icon. Ordinary taskbar
windows appear first; visible tray/utility windows appear below a separator.
Windows that Windows cannot display in the pane are omitted.

Alternatively, drop exactly one `.exe` or one `.lnk` resolving to an existing
`.exe` onto the compact pair. Shell launches it and expands when it can identify
the exact application window. A shortcut retains its arguments and working
directory. Other file types and ambiguous windows are ignored. Shell does not
kill the launched application on detach or save its launch path.

- **Managed** is the default. The application keeps its native window frame
  while Shell moves, resizes, hides and restores it as part of the combined
  window. Moving its title bar keeps it attached to the pane. Detaching restores
  its prior placement, including minimized/maximized and topmost state.
- **Embedded** is an explicit choice for compatible Win32 applications. The
  selected window's own frame is removed while attached. Elevated, modern,
  GPU-heavy or DPI-incompatible applications may reject embedding. Shell shows
  the failure and restores the original window state.

Only one application can be attached to a Shell at a time, and two Shells
cannot claim the same window. A rejected selection keeps the previous
attachment. Closing the selected application removes only that attachment.
Detach or exit restores the selected window; attachments are not saved across
restarts.

MO receives the attached window's current title as orientation. In a fresh
conversation without a task it can name the app and ask how to help. Attachment
does not itself grant permission to read content, and the title does not prove
that MO has seen the window's pixels. Observing content still requires the
normal computer-observation action.

## Keyboard, clipboard and accessibility

| Control | Action |
| --- | --- |
| `F6` | Move between terminal input and the control strip |
| Strip arrows or `Tab` | Select a control |
| Strip `Enter` or `Space` | Activate the selected control |
| Strip `Escape` | Return to terminal input |
| Picker arrows, `Home`, `End`, `Tab` | Select a window |
| Picker `Enter` / `Escape` | Attach the selected window / close the picker |
| Compact `Enter` or `Space` | Expand Shell |
| Mouse drag over terminal text | Select text |
| `Ctrl+C` | Copy a selection; otherwise interrupt the terminal |
| `Ctrl+V` | Paste Unicode clipboard text |
| Mouse wheel over the terminal | Scroll its managed transcript viewport |

Multiline and large pastes use the Terminal's normal unsent composer/holder
behavior; empty or unavailable clipboard text sends nothing. Windows
accessibility exposes the same controls, window choices and enabled states.
The [Terminal guide](../interface/README.md) covers commands, workspace keys,
queueing, steering and cancellation.

## Appearance and motion

Shell follows MO's active skin and Desktop typography. `/skin` repaints an
already-open Shell. Desktop Settings' **Character Size** also sizes the cube
pair. The configured shadow/glow follows the compact pair or expanded body.
Shell keeps the last valid appearance if a later settings refresh fails.

The compact pair stays on top; expanded Shell uses ordinary window order.
During work, the pair's skin-colored frames animate; a lone idle pair stays
still. Hover brightens the cubes and fades an unboxed **MO Agent** title to
their right, adding the attached window's title when present.

Collapse and expansion use a short eased fold. Text stays at its normal size
while the body reveals it. Minimizing hides the transition and attached window;
restore resumes the current Shell and reveals its controls. The terminal
retains Unicode widths and text emphasis, and its footer stays at the bottom
of the managed viewport. Images use the Terminal's color-block preview.

Multiple collapsed Shells form a compact group with a luminous connection.
New members keep independent terminals and attachments, use lighter/darker
shades of the active character color and appear above existing members. Drag a
pair or connection to move the group. Hold a pair briefly, then drag to pull
that Shell out; move compact controls close together to combine them again.
Clicking a member opens only its terminal. Collapsing returns it to its group.

After 30 seconds without input or work in the group, one short electric pulse
travels through the connection, then settles. Touch or terminal work cancels
the pulse. It does not change the group's size or cube count.

Windows taskbar menus belong to their applications. Shell's attachment controls
are in its picker and application-drop interaction; it does not add an attach
command to another application's taskbar menu.
