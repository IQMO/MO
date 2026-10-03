# MO Shell host protocol

The native renderer launches:

    python -m mo_shell.bridge --cwd <project> [--config <path>]

Only that bridge process tree inherits `MO_SHELL_HOST_HWND`, the native form
handle. While a target is selected, the form exposes its live HWND through
the `MO_SHELL_ATTACHED_WINDOW_V1` Win32 property. `mo_shell.context` uses this
read-only association to resolve a bounded current window title for the
terminal's existing environment context. Neither handle is persisted, and the
title is availability orientation rather than observed-content evidence.

Standard input and output are UTF-8 JSON lines. Output events are bounded:

| Event | Meaning |
| --- | --- |
| ready | The canonical child terminal exists; includes instance_id. |
| screen | Current bounded styled VT fragments, cursor, dimensions, title, liveness, and the derived `busy` boolean. |
| theme | Complete canonical Desktop tokens, panel metrics, typography, character settings, and taskbar icon. |
| error | A sanitized bridge or theme-delivery error. |
| recovered | Clears only the matching transient bridge error after its owner succeeds. |

Accepted input:

    {"type":"input","text":"hello"}
    {"type":"resize","columns":120,"rows":36}
    {"type":"snapshot"}
    {"type":"window_visual","hwnd":123,"owner_width":1080,"owner_height":680,"x":8,"y":80,"width":1064,"height":592,"radius":8,"visible":true}
    {"type":"close"}

Clipboard paste uses the existing `input` event, wrapped in terminal bracketed
paste markers (`ESC[200~` / `ESC[201~`). The canonical TUI remains responsible
for newline normalization, paste limits, and holding large pastes unsent; there
is no separate clipboard event or composer in Shell.

The bridge does not choose or persist shell geometry or a selected foreign
HWND. Native code owns those because it already owns the form and client
coordinates. `window_visual` is limited to the Shell form's bounded body or
compact-identity rectangle and lets the existing Desktop effect renderer paint
its configured shadow/glow outside that owned surface. It cannot select, move, or control another
window. The bridge accepts no arbitrary command, environment mutation, or
process ID; the host handle is fixed by the native parent before launch.

`theme.payload.tokens`, `panel`, and `typography` (including the body, title,
and mono roles consumed by the native surface) come from
`mo_desktop.visuals`; `character` comes from canonical Desktop settings and
includes the current Desktop cube form's edge ratio for native sizing; and
`brand_icon` is the serialized `interface.desktop_brand` mark. A changed
persisted semantic payload produces a later theme event; an equal payload
produces no event or icon regeneration.

The initial theme event is delivered before the terminal child starts so the
compact native handle can paint without waiting for terminal initialization.

Each screen row is a list of fragments. A fragment contains `text`, its
terminal `columns`, and only the styles already present in the canonical
TerminalScreen projection that this native surface renders: foreground plus
bold, dim, italic, underline, or strike. Emoji-bearing runs additionally carry
`emoji: true` so the Windows renderer can select its native emoji face without
changing the canonical cell count. The Shell body owns its one canonical
background, and trailing blank cells are omitted.

`busy` is parsed from the canonical animated MO OSC title already held by
`TerminalScreen`. It is a presentation hint only: the native surface uses it
for the cube-rail working cadence and never persists or treats it as Agent or
task truth.

Collapsed Shell grouping does not extend this JSON bridge. The native forms
publish process-lifetime peer, group, compact, color, and canonical busy hints
as HWND properties. Bounded `WM_COPYDATA` commands accept only registered peers
whose process executable matches the receiver. A group paints collapsed controls
on one existing surface; terminal transport and attachment ownership remain
with each original process. No peer handles or group membership are persisted.
