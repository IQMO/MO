# MO Desktop

### Generate: music, images and video

Choose **Generate** through the existing composer role switcher. Beside the role sit the kind
(Auto, Song, Image, Video), the provider (Kie; its menu opens Settings → Models & providers) and
the credits balance (read when MO Desktop starts and Generate opens, shown at once, "Credits —"
when there is none; a click reads it again and shows the exact balance in the pill, or says beside
the cubes why there is none),
all filled drop-down pills with no stroke; a small "!" beside the icons by Send dims the panel at its size,
exactly as browsing earlier replies does, and says plainly in a lit band what happens to attached files. Nothing is reserved under Auto: a picked kind's choices (its model, type and options) take
only the rows they need, a progress line appears only while a job or Refine reports, and the
composer grows with the prompt and while a drop-down needs room, gliding each time. A References pill for
purpose-labelled files (each dropped or picked file also sits in the sentence at the caret as a
chip named [Video1], [Image1] or [Audio1] with its role, filled with no stroke like the pills and
marked with MO's media disc: a play mark for a clip, a short level for sound, a sun over a hill for a
picture (the picture itself shows in the preview). For a video the
first clip defaults to motion (one clip drives the motion; the next is a reference) and a picture to
subject; a sound's purpose steps through reference, voice (the subject lip-syncs it) and music (the
motion follows it). Picking another kind gives the files that kind's roles. Generate takes as many
files as the largest generator does (Seedance 2.5: 50); files beyond what the chosen generator takes
(Seedream: 14 pictures; Seedance 2.0: 9 pictures, 3 clips, 3 sounds, 15 s of clips and 15 s of sound)
are not added and MO says why, and a new choice that cannot take files already there names them to
remove before Send. Clicking a chip opens References, deleting it removes the file and the
other names stay on their files; resting the pointer on a chip shows a small preview in the card
— the picture, a clip's first frame or the media mark, with name, length, size and an X that removes the file; Seedance reads
the names), a Sound pill for a video's own synchronized sound (on by default), a Saved results button that opens a compact list in the same panel (a click on a
row plays the result; its icons save a copy, continue it and open the job's card) and a
Refine sparkle by Send that rewrites the draft through MO's prompt enhancer under
the Generate skill's refining rules (press again to restore it), and real
stage/elapsed progress. The
role selector uses the same composer drop-down. MO prepares, submits, waits and saves results.
Ready result cards retain image previews or audio/video file cards beside result
choices. Jobs in that same conversation offers local open/play, save-copy,
continuation and exact-job cleanup review. Audio/video playback uses the system
viewer, not an embedded player. Outputs stay under the user's private
`media/generated/<job-id>/`; originals are not rewritten. Setup is in
Settings → Tools & connections.
Reference sharing is optional: temporary Cloudflare links grant access to selected
prepared copies; cleanup revokes local access, not provider retention. Originals
and saved results stay. See [Generate's complete contract](../core/media/README.md)
for supported Suno/Seedream/Seedance models, privacy, setup and verification limits.

MO Desktop is an opt-in resident assistant and companion for MO Agent. It places MO's
cube character, conversation card, screen guidance, voice controls, and selected
apps on the Windows desktop while reusing the same Agent, Gateway,
provider configuration, tools, policy, and private state as the terminal.
It is optimized for fast, request-local assistance rather than agentic diagnosis,
project planning, or engineering orchestration.

It is a separate resident process with an isolated `mo-desktop` conversation.
MO Terminal remains the agentic engineering and diagnostic surface and can use the same
canonical target/observe tools directly for explicit local screen requests;
Desktop is not a prerequisite or routing dependency for that path. Desktop
never owns the terminal taskboard or turns visual control into a policy bypass.
An explicit software-project implementation request is handed to the newest
live MO Terminal for that same project, or opens a Terminal when none is live.
An explicitly active Project Architect remains the other project-work owner;
ordinary Desktop conversation continues to own assistance, machine work,
screen help, visualization, files, apps, browser, and devices.
Its process has its own Agent/provider instances while sharing their canonical
implementation and configuration; there is no parallel provider implementation.

## Start here

First complete the [base MO setup](../README.md#quickstart), including a working
provider. On the Windows computer that will display Desktop, install its optional
packages with the same virtual environment's Python:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-computer-use.txt
```

Run this from the MO checkout. These packages are not needed on a headless
server. Voice and MO Design windows have separate optional installations below.

MO Desktop is disabled by default. Enable it in the private MO configuration:

```yaml
mo_desktop:
  enabled: true
  action_receipt_seconds: 300
```

Then use either:

```powershell
.\.venv\Scripts\python.exe -m mo_desktop
```

or `/desktop` from MO Terminal. **Win+Alt+M** summons the existing resident.
Strict process locking prevents a second resident from starting. On Windows,
the lock, readiness, launch, and summon markers share the stable per-user
LocalAppData temp directory, so a sandboxed terminal and the normal resident
cannot split ownership. `/desktop trace` defaults to the latest saved reply and
joins its turn-correlated conversation and provider/tool evidence. Available
reply timestamps and instance IDs distinguish turns across restarts; missing
metadata remains explicitly unpinned. Current resident, lifecycle, and Everywhere
state are shown separately from historical reply evidence.

A visible resident overlay can be absent from an external interaction helper's
window list. That omission alone does not establish a Desktop failure. Use
`/desktop trace` to check runtime evidence separately; tests that cannot target
the overlay do not verify physical clicks or file drops inside it.

Double-click a visible cube to expand the same four cubes outward from their
shared center into four square app cubes in the existing cube window. Work,
Devices, Care and Your apps appear inside them without a backing panel. Closing
the launcher shrinks those cubes back to their live cluster. Their color and
corner shape follow the live cube settings. The launcher projects app actions
from Desktop's declarative catalog, with profile-owned apps under Your apps. The
optional tray popup is a compact control projection rather than a second app
launcher. Hovering an app shows at most three quick actions as small buttons at
the right end of its own row; MO Phone's are Trackpad and Mirror, each started
on the ready phone in one step and answered by the cubes' label (Trackpad has no
separate launcher entry). Titles are drawn at 1x on the finished faces so the
font's hinting keeps them crisp, and the cubes away from focus darken without
turning see-through. Edit
gently animates the app rows and lets
them move between cubes. Outside Edit, hold a group heading to see its movable
state and available landing slots, then drag to swap whole cubes.
Hold an app to reveal its Remove button; removal affects the launcher entry,
never the app or target file. The launcher gear offers Add file/folder, Reset
layout, and the existing Appearance settings. Reset restores hidden built-in
apps and group order while retaining added shortcuts. Folder menus use the
shared antialiased card, with keyboard navigation, filtering, and scrolling.
Hovering **MO SystemCare** in the Care cube reveals a small Game Session control;
selecting it opens SystemCare's canonical plan review and never applies the plan
by itself. During an active or recovery-required session, the launcher yields to
one dimmed cube docked at the top-right of the current monitor. Clicking that
cube opens the compact session status/actions panel; stopping still requires the
same reviewed SystemCare restoration plan. Ending the session restores the
previous cube position, opacity, movement and launcher behavior.
After ten seconds without input the cubes give a brief Buzz; five seconds later
the idle launcher folds away. Input or an active file picker resets that delay.
When MO apps are running, hovering the cubes
shows their icons and names in the existing volume/activity bubble. The bubble
follows the cubes and its rows can focus those apps. Volume and notices use the
same holder and active visual settings. Closed app windows leave the list; minimized
windows stay available to restore. Hovering never starts an app. Launching or
focusing an app gives the cubes one short heartbeat in that app's active-skin
color. New windows center on the monitor containing the cubes. Their entrances
start at the current cube pixels and unfold into the window's normal bounds:
Dashboard panes, Files folder layers, Phone handset, Design frame, SystemCare
shield, and Settings sliders. Shell retains its two-piece
fall and fading trace. Startup does not freeze the cubes; successful launch
finishes with a small geometric settling motion. Thinking uses the same cube
motion library. The optional tray popup keeps Show / Hide and Settings; session
toggles for Focus mode, Voice Chat and Run at Startup; Advanced controls (Action Log and
Edit config.yaml); and Panic Stop, restart and exit. Open Settings from Your
apps or the launcher gear's Appearance action. Restart waits for the resident to
release its lock before launching one replacement.

### Window presentation

Settings, Phone, SystemCare, Files, Design/Board and Dashboard share a compact
title bar and the same skin-colored window buttons. Pin uses an outlined
pushpin, filled when the window stays on top. Existing app-specific minimize,
close and save-confirmation behavior stays with each app.

Desktop's resident cube, composer, launcher, tray and screen selection use native
alpha windows and a single Windows message/timer loop. Clipboard, pointer and
screen services no longer require Tk. Private profile Tk apps keep their
widgets in an on-demand host on the same GUI thread; normal Desktop startup
neither imports Tk nor creates an interpreter. Settings, Phone, Files, SystemCare
[Mologrthim](mologrthim/README.md) and [Inventory](inventory/README.md) use the shared native WebView renderer. Shell owns its native control
strip; Windows file pickers and attached third-party windows retain their own chrome.

## What the companion can do

Desktop supplies the lightweight manifest in `mo_desktop/capabilities.py` when
the operator asks about Desktop behavior or capability. Ordinary conversation
does not pay to resend the complete inventory. The manifest is the single
Desktop-facing summary of supported user features, canonical owners, and
important limits; update it with the user-facing table and `CAP-DESKTOP`
contract when a Desktop feature changes. It describes product capability only
and never proves that a resident window, device, or optional runtime is
currently available.

| Area | Behavior |
| --- | --- |
| Conversation | Compact reply/composer card, structured choices, copy, keyboard focus, Arabic/RTL shaping, and Desktop-only conversation history |
| Project implementation | Preserve Desktop as the resident assistant: send an explicit software change to a live same-project MO Terminal, open one when absent, or keep it in an explicitly active Project Architect role |
| Screen help | Observe the current screen, explain it, point at verified targets, or run a bounded walkthrough |
| Desktop action | Resolve an unknown application/window target once, choose useful UI Automation evidence or direct pixels for the task, then perform one target-owned action and inspect its returned observation. Reuse fresh evidence while the target is unchanged |
| Connected Chrome | Optionally set up or remove the native bridge from Settings. **MO Connected Tab** discovers ordinary tabs and attaches to the requested tab automatically on observation, providing bounded DOM/page text and visible-viewport control; Chrome's optional stop control remains effective. Without one, Desktop may continue through its existing exact native-window path when sufficient, with no connected-tab DOM authority and no second browser/profile transport |
| Voice | Double-Alt STT capture, optional local Piper speech, output-device selection, and an explicit Voice Chat loop |
| Dashboard | Tray opens or focuses the full Dashboard using the resident Agent, without starting a terminal; cube right-click opens the mini Dashboard (Now, You, System) that links to it |
| MO Design | Open a compact native Studio for a generated `.modesign` concept, load an observed current-state baseline or refine it in place with an optional read-only project, then explicitly hand implementation to a current terminal, Background, or a new terminal |
| MO Files | One pinnable, skin-themed browser over the shared file service: select this computer, the hub/profile, or another online Desktop/terminal source; optionally split two independent locations, use capability-gated right-click actions, and follow existing Incoming/Outgoing transfer activity |
| MO SystemCare | Native, skin-themed maintenance workspace over one core service: saved results, machine/MO/host/project inspection, exact native action plans, actual receipts/recovery, global Game Mode and optional existing-scheduler automation |
| MO Phone | Explicit Android device discovery, pairing/status, mirroring, and frame capture for a selected local device; optional input extensions require a compatible private local client and are not part of the current Google Play release |
| Explainer video | The existing `mo --explainer` workflow produces narrated explainers and product videos, with local rendering/voice dependencies and final-media verification |
| MO Shell | Each launch opens an independent floating Shell with one canonical terminal and one explicitly selected window; running-app rows focus each Shell, and attached titles supply availability context, not observed contents |
| Focus mode | Resident tray toggle that expands the lower-right cube into a freely movable window list with hover previews, Explorer tray and clock; click the cube to collapse the list, double-click to exit, and restore prior taskbar visibility on exit |
| Window appearance | Settings selects None, Shadow, Glow, or Hybrid for every MO-owned window and controls them with one shared intensity slider; glow color follows the active skin. With Hybrid (the default) Desktop's panels are slightly see-through over the screen blurred by Windows itself (one click-through backdrop per panel, clipped inside the card, shown once the panel settles, in or out of captures like its panel) instead of a drop shadow |
| Continuity | Follow one live terminal, import bounded current focus on request, show Everywhere state, and display lifecycle notices |
| Live Control | Advertise the running primary display to a paired Android controller through the native, short-lease MO Live Control protocol |

### Focus mode

On Windows, hold the lower-right cube for two seconds to enter **Focus mode**,
or use MO's existing resident tray menu to turn it on or off.
The lower-left cube keeps its screen-capture hold with the same timing and feedback.
If Desktop was already running when MO was updated, first use **Restart MO
Desktop** in that same tray menu to load the updated code. Focus never starts a
second tray owner. It is session-only: there is no launcher entry, Settings
preference, automatic activation, or new hotkey.

The lower-right cube expands into a compact icon-and-title window list. The
composer similarly expands upward from the upper-right cube, using its existing
input card, draft, controls and transition clock. As text wraps it grows upward a line at a time, gliding over the
panel transition time instead of jumping; nothing in the card is scaled. Roles come from the current profile catalog and are selected in a compact opaque dropdown at the role control. The composer's own cube is its search switch: each click turns it, one at a time, from MO chat into Google, YouTube and Google Translate, shown by their brand marks (the same text and default-browser action; the selected service colors the edge), and the composer returns to MO chat each time it opens. Its controls are icons without strokes: a paperclip (Windows' file picker, imported like a drop) and two history buttons (earlier, later) next to Send. The empty composer shows the chosen role's own hint in italic, a little dimmer (the role's `role_hint`: Book Writer "Let's write… ✍️", Generate "Describe a video, image or song…", Project Architect "What are we building?"), and a search mode's words ("Search Google…", "Search YouTube…", "Translate with Google…"); the default stays "Type a message…". Up/Down or the history buttons show MO's earlier replies in the composer itself, which never changes size: a short reply shows whole, a long one its head, the rest of the panel goes dark around that line (never the screen) and is not clickable (a click there returns to normal; a click on the lit message opens it in full), and the history button appears at the top; typing, Enter, Escape or Down past the newest returns to the draft. With both open, the two left
cubes remain visible. Its small cube control folds the composer back. The single
cube at the top of Focus folds the list
back into the four-cube form. Its lower-right cube is then slightly larger and
shows only the time and total open-window count. Click it to expand again, drag
it anywhere on screen, or double-click to leave Focus. The complete group is
clamped to the monitor. A focused cube control also accepts arrow keys.
Folding the list resumes the cube's existing chase, movement and idle behavior; its trail uses the folded face's actual size. Expanding holds the group in place. Turning Focus off keeps the chosen position. Focus follows the main cubes' fullscreen visibility, including dismissing previews while they are hidden.

The expanded face shows Windows and the total, a compact local search field,
time/date, Focus settings and the system tray. Search replaces the window rows
with installed apps and files from Windows' own index; clearing it restores the
window list. Windows controls indexing coverage. No recursive disk scan or
background indexer is added. The dimmed, borderless bottom strip reads Explorer's actual
pinned taskbar shortcuts, scrolls independently, and shows a hovered app's title
in a muted strip at the bottom of the window list. The time, settings and tray
sit below the pinned apps. Search has no enclosing stroke; its X clears the
query and restores the window list.

The clock opens the calendar. Focus settings stay inside this face; their dimming
slider previews immediately and is session-only. Clicking outside returns to
the window list and retains the adjustment. Compact Sleep, Restart and Shut
down icons require an inline confirmation. The optional timer starts off;
when enabled, choose its delay before selecting an action. A confirmed countdown
continues outside Settings and can be cancelled there. Leaving Focus cancels it.
The countdown is colored and cancellable directly in the expanded or folded
face. MO never forces applications closed or schedules a separate Windows countdown.
On classic Explorer shells,
the tray combines promoted/system and hidden icons in a compact flyout, anchored
below its button when space permits. Icons and actions come from Explorer's live
notification controls, including MO's original tray menu. XAML shells use their
native notification flyout. Focus never moves, crops or reparents the taskbar.

Rows retain their order when the foreground changes. The active window carries an
accent bar and full-strength title; the hovered row has its own plate, which glides
to a neighbouring row (about 80 ms) instead of jumping; other titles are muted.
Scroll for overflow; click
a row to switch, or click the active window again to minimize it. A close button appears on hover and requests the application's normal close flow, retaining its unsaved-work prompts. A brief hover opens an aspect-aware Windows DWM preview beside
the list when space permits, avoiding the full cube group and aligning with the
top and bottom of the expanded Focus and composer faces. Attachment preview
cards use those same left and right edges.
Scroll over the preview to browse windows in the same holder; click to switch
to the displayed window. Only that
hovered preview allocates a native composition host. Minimized windows are
labelled; Windows may provide frozen or unavailable content. Enumeration and
verified activation reuse Phone Trackpad's Windows owner, with up to 256 rows.

Focus uses the existing alpha-card renderer, visual settings, typography,
glyphs, outside effects and cube animation clock. The fourth face grows from
its actual cube sprite in 200ms. The same four pieces expand into the launcher
in 180ms, including the currently displayed fourth face. The collapsed cube
uses the original cube's sprite renderer, glow, rounding, brightness and motion.
Its trail follows the visible cube without leaving copies of expanded faces. Expanded surfaces follow
the panel and control rounding settings. The remaining cubes keep their
normal composer, Dashboard and launcher input. Both expansions yield their actual
pixels to that same launcher and return when it closes. Idle Focus defaults to 42% opacity and restores clarity on interaction; the inline slider adjusts this session's dimming. Settled previews need no active-rate frames.

Focus temporarily hides Windows taskbars and expands the actual Windows work
area using `SPI_SETWORKAREA`. Exit restores only the visibility and work-area
reservations that Focus changed. It preserves auto-hide preferences, does not
force an already-hidden taskbar visible, and does not restart Explorer.

During Desktop's own native computer actions, the normal four-cube character
remains the passive indicator while its window yields input and stays out of
screen captures. The panel stays visible too: it lets MO's clicks through and
stays out of captures. There is no separate computer-use overlay, and ordinary actions ask for no approval (deleting, sending,
paying or discarding unsaved work still does). If Windows cannot apply click-through
and capture exclusion, the cube or panel hides as a fail-safe rather than intercepting
input or appearing in captured pixels. While any local MO Terminal uses the
computer there is no overlay either: the cubes grow, glide to the freest of the
four corners of their screen (one the window MO is working in leaves free, then
the least covered, then the farthest from the pointer MO is moving; re-checked
every few seconds, so they move on calmly when the work reaches them) and show MO
Terminal's rhythm in light only: the four cubes light in reading order like
characters printing, then the last one blinks like a block cursor (no bubble, no
movement, distinct from thinking and from Desktop's own acting wave); the cubes let
input through and stay out of captures. When it is done they return to their place
and size. Desktop's own use comes first: with a docked face, the launcher, expanded
Focus, a Desktop turn, listening or speaking, the cubes go back to their normal size
where they are and show Desktop's own state, so its panels dock as always; they grow
back into the corner with the beat and label once Desktop is idle. A cube you moved
meanwhile stays where you put it.

One computer has one driver, and you come first. MO Terminal's native actions
(clicks, keys, focusing or launching windows) wait while you use the keyboard or
mouse, or while MO Desktop has focus; once you pause it looks at the screen again
before acting, since what it saw may have changed. Input MO itself sent never counts. After a minute of waiting the action reports that MO is waiting
for you instead of acting. The working cubes let clicks through, so use
**Win+Alt+M** to talk to Desktop meanwhile; Esc stops the Terminal. Desktop's own
actions act for you and do not wait. External
automation that does not publish MO activity has no such signal.

### Settings

Settings opens in MO's shared native WebView window, with searchable pages,
inline choices, and live appearance controls. Closing Settings waits for pending
saves and exits its renderer, releasing its WebView processes. Minimizing keeps
it available; reopening after closing starts a fresh view from saved settings. It replaces the former Tk Settings
window and retains its cube, movement, panel, skin, voice, startup, Chrome bridge,
and maintenance actions. It also edits declared Agent, learning, service, device
and policy preferences through the shared configuration writer, with explicit
reload/restart scope. Speech engines labels `cpu`/`cuda`/`auto` as recognition
compute, not as a microphone source. Capture opens the audio runtime's default
input when recording starts; current Settings does not claim an active microphone
identity. Projects & checks owns project LSP selection, server setup
and graph preferences; Dashboard retains recorded checks. Models distinguishes
Desktop, saved Terminal defaults and observed running Terminals. A failed save
is shown as unsaved. The [coverage map](settings_app/COVERAGE.md) records supported
editors, apply boundaries and advanced setup that retains dedicated owners.

### Model selection and reply wording

Settings can save an independent Desktop model/reasoning choice for its next
request. By default Desktop follows Terminal's saved `/model` provider (or the
configured active provider when no choice is saved). Set `mo_desktop_model`
on that provider's existing configuration row to name its lightweight model.
Gateway applies the explicit choice, or the followed variant with lowest supported effort, through
the existing request-local model scope; Terminal's model and preferences do not
change. If the lightweight choice is unset, the selected model is retained;
Desktop never guesses speed from model names. Unavailable routes fail visibly
without cross-provider fallback. Desktop's process-local Agent/provider instances
share the canonical provider implementation and configuration; there is no separate
provider stack, startup selector or keyword escalation. Settings can separately
save Terminal defaults or ask an observed idle Terminal to change its actual
model, with confirmation from that instance and no draft/focus change.

Replies stay concise by default but retain requested detail in the existing scrollable card. There is no Desktop-only first-response token ceiling, reply-character cap, or rolling conversation-message cap; normal provider output, context-pressure, safety, and evidence limits still apply. Read-only answers and honest tool limitations do not trigger compulsory action retries.

A reply card uses the composer's footer: a small "Reply to MO…" field, the same two history
buttons and the send button. Type in the field and press Enter (or send) to answer MO
without leaving the answer: the answer folds back into the cubes and the next one grows from
them. The history buttons step through earlier replies; the conversation-history button sits at
the top beside Copy. Replies retain their own choices and attachment presentation when recalled or
restored after restart. Independent choices support multi-selection; exclusive
destinations and approvals remain single-select. Long choice lists scroll inside
the card with Submit still visible; selecting a row preserves its full detail,
even when another row uses the same label. Reply always permits a typed
follow-up. Dropping an image imports it locally and opens only its preview.
**Send to MO** explicitly submits the displayed image to the Desktop conversation;
the drop itself starts no model turn. Other file drops retain their inspection flow.
One compact row below the image contains Send to MO, Share, Tools, Folder and Path
in opaque themed button containers.
Folder opens the saved image's containing folder; Path copies its absolute local
path. The image uses a narrow inset, while these controls reuse the Desktop skin
and shared glyphs. Share lists up to six
recent live MO terminals and places the image's local path
in the chosen terminal's current conversation composer for review before Enter.
It never sends a turn automatically, replaces an existing draft, or redirects
to another terminal when that instance or conversation changes.

Before a final reply is displayed and saved, Desktop normalizes transient
capability wording such as “in this turn” or “for the current turn” to “right
now.” The underlying refusal, approval, or explanation remains unchanged.

### MO SystemCare

MO SystemCare starts only when opened or explicitly requested. It reuses current
private device calibration and opens saved results immediately in a native
WebView workspace with lightweight resource refresh. Deeper Safe/Advanced scans
and selected checks wait for a request. Machine, MO runtime, configured hosts
and selected projects retain separate evidence. Selected native maintenance
requires a fresh exact catalog plan, captured-state revalidation, applicable
Windows permission and explicit permanent-action acknowledgment. Receipts and
typed originals provide actual results and eligible restoration. Optional
automation uses MO's existing scheduler. An advice request from SystemCare is
answered in the Desktop conversation as a reviewable plan; it is never handed to
MO Terminal as implementation. Dashboard Scan/Open/Cancel route to the
same service/window. Game Session reuses the global Game Mode recovery journal,
shows a compact start/current resource projection in SystemCare, and uses the
launcher cube as a low-overhead active/recovery control without adding another
worker or telemetry loop. See [SystemCare](systemcare/README.md) for action
coverage and limits; opaque `personal/` remains outside this owner.

The **MO Files** desktop window uses the installed MO Design WebView renderer
and current Desktop skin. It opens with the Dashboard's four-cube entrance
until the first folder is ready. The connected-place menu beside each pane's
Back/Forward controls distinguishes
this computer, MO Hub, and online Desktop, terminal, or consented phone hosts
by opaque identity. A phone that is not connected appears as an inert cue.

The folder map emphasizes the current folder and puts its children below it.
Earlier folders stay dimmed and clickable in the breadcrumb line. A top-right
control switches to a familiar file list with the same actions and navigation.
File-size bars compare only files directly visible in the selected folder.
Circular controls above the focused folder or selection, plus a right-click
menu, expose actions allowed for the current location. Active transfers appear
on the board; the top transfer control opens history, and the idle lane collapses.
If transfer status cannot refresh, retained progress is marked as last known
and the transfer control/history shows the error. File-operation results report
completed items and partial errors even if the folder cannot refresh afterward.
Optional split view holds two independent locations. Each pane sorts by name,
type, size, or date. Mouse Back/Forward buttons, breadcrumbs, and the path
control navigate within a location. The source button beside each pane's
Back/Forward controls switches that pane between connected places; the active
split pane is marked. Opening a place shows its destination while the request
completes, and opening split view immediately copies the current folder into
the second pane with independent navigation history.

Drag selected items onto a folder to choose Move or Copy at the drop point
through the existing guarded file owner. Dropping files on another source
confirms Send using its exact paired transfer identity. Local files can be
dragged to Windows Explorer, then the originals can be kept or moved to
recoverable MO Trash after Explorer accepts a copy. Files already inside an
allowed MO Files location can be dragged in from Explorer. Cut, copy, and paste
stay within one source and use the existing guarded file operations. The window
may be pinned, minimized, closed, and reopened without owning transfer custody.

**Quick transfer** shows a QR that any device with a browser can scan on the
same Wi-Fi or LAN. Select up to eight local files to offer for download, or
leave the selection empty to receive files into the current local folder.
QR generation uses MO's existing optional Segno encoder, installed with
`requirements-everywhere.txt`; it grants no Hub role to this Desktop.
Each file is limited to 64 MiB. Choose the network interface, scan the QR,
and use the browser page to take or send files. The session ends after five
minutes, when **Stop sharing** is clicked, or when MO Files closes. Connected
MO hosts still use the paired transfer route; browser QR transfer currently
uses this computer's selected local folder. This local browser connection uses
unencrypted HTTP, so use it only on a trusted private network; closing the
session revokes the URL and aborts unfinished uploads but cannot erase files
already received by either device. Existing MO Hub pairing remains active until
separately revoked.

A phone source remains read-only and exposes only its real selected-folder or
all-files root. Drive roots appear only when that host already uses
`access.mode: full`. The board does not expose absolute roots, raw private
state, credentials, symlinks, or hidden control files. Delete moves one exact
file or bounded folder tree to recoverable private Trash.
When a remote request fails, the window may show the Hub's bounded plain-text
protocol reason beside the status code, but never a raw response body.

### MO Design

MO Design is a Desktop app for clarifying an idea visually before implementation.
Install its optional renderer once:

```powershell
python -m pip install -r requirements-design.txt
```

Open **MO Design** from the cube launcher to reopen the most recent concept or create the
first one. During an agent turn, the `mo_design` tool creates the artifact first
and streams later HTML/CSS/script updates into the same window. A saved artifact
can also run on its own:

```powershell
python -m mo_desktop.mo_renderer path/to/concept.modesign
```

Each `.modesign` file is self-contained: one validated YAML document carries the rendered visual,
window preferences, shared Board, design decisions, constraints, acceptance criteria, and
project references across Windows, macOS, and supported Linux desktops. It is
not a packaged native application and it never embeds private profile prose or
credentials. The receiving agent checks graph hints against current source and
tests before implementation.
For a new app, product, or showcase request, Design creates an interactive
prototype by default unless the user explicitly requests a static concept. Its
primary controls and proportionate screens/states work inside the sandbox, while
the same revision records the objective, acceptance outcomes, visual decisions,
and evidence limits. This is still a prototype, not the final application.

Every design has its own private session folder. The compact switcher changes
the active design inside the same Studio process, so reopening the tray focuses
the existing window instead of multiplying WebView runtimes. A design may bind
one project through the native folder picker or stay in **No project** mode;
switching or clearing that binding affects only the active design. Its bounded
conversation, pending Design request, and last completed revision persist in the
private `session.json` beside the artifact and resume on switch or relaunch.
That sidecar is not portable: private conversation text never enters the Design artifact.
The compact **+** action starts a clean design/conversation while retaining the
current optional read-only project. The **Historical** control opens
real revision history rather than presenting the head `rN` as a release label:
Studio keeps up to 40 snapshots, and selecting an older one previews that exact
snapshot without mutating the artifact or its history. The selected and current
revision remain explicit inside that history. The next completed update writes
the normal next head revision.

Full Studio opens on **Preview**; it changes to **Board** only when the user selects
that tab, while an explicit diagram request from an MO terminal opens the same
artifact and Canvas alone in a pin-or-close window. One exact terminal cannot own
conflicting standalone Boards. The user can sketch with pen pressure, select/move,
erase, add simple shapes/arrows/text, undo/redo, pan, and zoom. MO reads the bounded
declarative scene and may propose marks, but those remain a visibly distinct draft
until the user accepts or rejects them. Immediate ink is private working state and
settled bursts enter normal Design revision history. **Download** checkpoints and
exports the current portable revision. A compact badge identifies the exact live
terminal on both Preview and Board. On the next drawing follow-up there, MO reads
the current Board; Accept/Reject returns only the draft decision. The badge does
not imply automatic synchronization, and Board chrome does not start another
terminal turn.

Selecting a project does not authorize changes to it. A request to load its
current surface is a baseline request: MO observes current pixels when available
(including the requested exact Chrome tab as read-only
Design evidence), verifies routes, components, theme, layout, tests, and graph
hints against source,
and reproduces the existing state without adding proposed improvements. Dynamic
badges, toggles, expanded sections, and status values are shown only when observed
or directly proven. Without pixels, Studio labels the result as a source
reconstruction rather than claiming visual accuracy. A request containing an
explicit visual change is routed as refinement. After either update, the same
Design worker binds one exact Studio target, performs one bounded visual QA
observation (including a non-activating render of a covered, non-minimized
Windows target when supported), and may make at most one correction followed by one re-observation
through MO's existing computer/vision owner; it does not start another worker or
continuous screen capture. Until Handoff,
the runtime's `mo-design` sandbox lane permits durable writes only to the same
private `.modesign` artifact. Studio shows when a project is merely attached, when it
has verified file/symbol mapping, and what evidence is still missing.

The generated artifact runs inside an iframe with network and local-file access
blocked. Scripts are off by default and can run only when the document explicitly
opts in; generated markup is sanitized independently. The trusted Studio shell
reuses the active MO skin, panel spacing, four-cube brand, and the compact
borderless title-bar treatment used by MO Files and MO Phone. Its trusted fixed
controls own drag, minimize, maximize/restore, close, and the bottom-corner resize
affordance; the generated iframe cannot reach them. Ordinary conversation remains
inside Studio and updates only the same Design artifact; destination choices never
appear in its composer. Implementation is a separate, explicit **Handoff** step.
The enlarged cube-branded activity line sits below the preview, uses the shared
skin and character preferences, and stays static when idle.
While a response is incomplete, Studio preserves the last finished preview
instead of showing partial markup. The compact refresh icon beside **Source**
safely reloads that finished visual, and a 15-minute stale request becomes a
visible retry state rather than an endless spinner.
Ordinary **Send** remains the iterative visual conversation. **Handoff** is the
eventual approval step and opens a compact sheet that recaps the actual saved
title, summary, revision, Static/Interactive state, optional source-aware brief,
and project context. It does not ask the user to restate the concept, fill a
readiness form, or add a final note; it asks only which MO recipient gets the
approved work. **Current terminal** returns the handoff to the exact originating
live terminal as a normal interactive turn, never `/goal`. **Background** requires
an explicit safe project and starts a headless goal with the established cube/
tray/platform completion notice. **New terminal** opens a visible goal in a new
interactive MO. If no project was selected, the interactive receiving MO confirms
the target before writing rather than assuming its startup directory is the
implementation target. After implementing the accepted revision, the receiving
MO verifies behavior and visual alignment, then uses the existing completion
lifecycle to mark that exact revision **Resolved**. Studio displays **Resolved · rN**
whenever that exact revision is selected, including after reopening. Reopening
reactivates the conversation without erasing the resolved revision; a newer revision
remains unmarked until its own implementation and verification complete.
Desktop owns the tray, Studio host, authenticated broker, and notification fallback;
it is never treated as the implementing agent. See the
[`.modesign` artifact guide](../core/design/README.md) and the
[Studio runtime guide](design_studio/README.md).

When an enabled artifact script fails during testing, Studio keeps the visible
preview error and records the last control descriptor without reading form-field
values. From the Desktop-owned Studio it queues one design-only diagnostic refinement
for that design revision and shows the work in the same conversation without a
completion notification. Repeating the same failure does not
create another worker. A standalone renderer without Desktop records the issue
locally and asks the user to reopen it from the cube launcher; it never silently launches
a terminal.

### Request-local action admission

Request classification primes tool discovery and presentation for three ordinary
cases; it does not authorize execution or veto the model's reading of the current
conversation:

- Conversation stays conversational; it does not capture or point at the screen.
- Screen questions may observe, but cannot actuate. Pointing occurs only when
  the operator explicitly asks to point, highlight, or run a walkthrough. One
  immediate referential explanation follow-up continues the preceding
  walkthrough as read-only guidance; it does not inherit an earlier open, click,
  or other action. The initial hint recognizer looks for a visible UI target such
  as the screen, a window, a button, or a toolbar; incidental wording such as
  `where can this be checked` alone does not select a screen hint. The model
  still interprets the request in its conversation context.
- Explicit action requests may use the normal Gateway actuation lane, sandbox,
  confirmation rules, and see → target → act → verify evidence. When materially
  different sources or targets remain plausible, Desktop first uses its existing
  pick-one cards; the selected card then re-enters the same action path as
  explicit authority instead of creating another router or execution mode.
  Natural control demonstrations and follow-ups use this same route without a
  required Connected Chrome trigger phrase.

There is no user-facing or configurable guide/action mode. A selected action
card is explicit authority for that selection. A compatible “again” continuation
can reuse only a bounded successful same-session receipt; missing, stale, failed,
or conflicting evidence asks once for the target. A fresh target-bound
observation also keeps an immediate natural UI follow-up on the computer-control
route for the target lease. A whole-screen observation is only a routing hint:
MO must still discover and verify the exact window before acting.

An admitted native-action turn directly exposes `computer_targets`,
`computer_observe`, and `computer_act`; an ordinary visual read starts with the
first two. A visual walkthrough starts with `computer_observe` and
`point_on_screen`; an explicitly compound request may add its admitted action.
A point may carry the target's `box` (the whole window or control from
`computer_targets`/`computer_observe`): MO Desktop then outlines it in the skin
accent and dims the rest of that screen with one click-through, capture-excluded
layer below the cubes, which clears when the point ends or the turn stops. A
`number` prefixes a walkthrough step's label, and `zoom` shows a small control
enlarged beside its outline. When MO is unsure which window or part is meant
("capture the Chrome window" with two open, "this part of the screen"), it
outlines its best candidate and asks with a single choice (Yes, this one / No,
another one); the outline stays on until that question is answered, by click,
typing or voice, or its card is closed. On "no" MO outlines the next candidate;
when it stays unclear MO asks for the cube's screen selection instead.
These paths do not spend a provider round rediscovering the same tools. Each
action returns its own fresh evidence, and MO reads it to decide the next step;
no extra verification round is forced. If the last action's result is still
unknown when MO answers, the reply says so plainly instead of implying success.
The initial list does not authorize execution. Plain conversation keeps the small core catalog, and
`computer_act` still requires current action authority.
Terminal keeps its
normal agentic diagnostic catalog and work context.
Application launch accepts either a plain installed-application name in the same
`computer_act` call or a current exact ref from deterministic discovery. A plain
name launches only one uniquely best catalog match; ambiguity launches nothing.
The launch result returns verified owned-window evidence when correlation is
unique and otherwise reports an explicit unknown outcome. UIA, screen, raw
pointer/keyboard, and recipe
compatibility names route through the same target/action owner; the
isolated-browser process/profile path is retired and does not create an alternate
backend.

A visual explanation or walkthrough of what is already visible takes one direct
whole-screen observation. That image goes straight to an image-capable model; a
text-only model receives the one bounded observer result instead. The same image
supplies every pointer coordinate, so Desktop does not first enumerate browser
or native windows, run UIA context/find, or ask another vision path. A failed
capture may select one suitable fallback from the error, never probe all routes.

Connected Chrome adds bounded DOM/viewport access to ordinary tabs in the
operator's existing browser. Desktop uses that path for DOM reading or browser
interaction that the visible-screen path cannot satisfy: it discovers the exact
requested tab, attaches automatically on its first observation, retains it, and
consumes each action's returned observation. It does not combine that route with
native-window or UIA discovery. Protected pages and pages already owned by page
DevTools show `NO` and remain unavailable. Opening DevTools on an attached page
cleanly detaches MO; after it closes, a later observation can attach again. The
toolbar action is an optional direct connect/stop control. An explicitly stopped
tab stays unavailable until the operator re-enables it or Chrome clears the
extension's session storage; no model tool reverses that stop. If no suitable tab
is available, Desktop may use one exact native-window path when sufficient,
without claiming DOM access or repeatedly probing the extension. Inspecting the
extension's service worker does not claim a page debugger. One native channel
stays ready while Chrome runs, including when no tab is attached.
After an explicit website open, Desktop chooses the one remaining evidence path:
direct screen pixels for a visual walkthrough, or Connected Tab/native evidence
for interaction—not both.

### One character and one panel

The character is one skin-aware cluster of four cubes by default. Free mode
wanders; Lock mode follows the pointer. Thinking, listening, success, warnings,
notices, and authored emotes change the existing cluster instead of creating
new windows.

The four cubes lead a turn: Send folds the composer back into its cube, the cubes
turn a quarter at a time while MO works, and what MO is doing ("got it…", "opening
Paint…") shows on their small label. The answer then grows out of the cubes into a
card sized to the answer: a short answer stays small, and a long answer and its
choices scroll inside a capped height while the buttons stay in place. Escape folds
the answer back into the cubes.
The same card carries input, choices, files and
Dashboard, with icon controls. Only one surface shows at a time: while a card
is open, MO's progress stays off the screen (the cubes show it is working) and
new notices wait until the card closes. The cube-side glance keeps volume, sync
and notices when no panel is open: a compact notifier pill in the skin's card
colour (never tinted by the cubes' shade, so not green while listening) with a
status dot (the accent; the warning colour for a warn notice), the title and a
notice's short detail inline. A glance never shows over the open launcher: it
sits beside the launcher on the side with room, and returns beside the cubes when
the launcher closes.

Simple requests receive concise final replies; requested detail remains intact.
During a walkthrough the larger reply card stays hidden while the pointer shows
one numbered verified point and explanation at a time; one final recap opens
only after every point finishes.
Choices appear only when a real decision remains. One fresh whole-screen
observation may supply every coordinate in an unchanged walkthrough. Target
changes or missing action evidence require another observation; old pixels or
conversation prose never do.

### Compact Dashboard

This cube-attached companion is distinct from the
[full Dashboard](../core/dashboard/README.md). The cube launcher's **Dashboard** action
opens or focuses one native Dashboard using Desktop's already-running Agent,
project and configuration. It does not start a terminal or another Agent.
The cube's right-click gesture still opens the compact panel. Voice remains in Desktop; the full
Dashboard does not add another voice interface.
Voice shows on the four cubes, never as extra text. While you speak, your microphone
level lifts and lights the cubes one after another (each answers a moment after the one
before) and they settle into a slow ripple in every pause; when MO has heard you they nod
once. While MO speaks, the level of the audio it is actually playing lifts the cubes and
opens the cluster a little with each syllable, and they settle between words.

Right-clicking the cube opens the mini Dashboard on the cube's docked face: a glance
that jumps into MO's main Dashboard app ("Open Dashboard ↗"), never a copy of it. It
applies the active Desktop skin, keeps the MO Cube mark visible and has three views on
one fixed-size card. **Now**: open tasks, live MO terminals and unread mail as figures,
then the terminals running now and what needs you (a signed-out Gmail, learning reviews).
**You**: profile files, learned skills and learning as figures, then mail and your own
Desktop apps. **System**: the MO host, surfaces and PC health, then where MO is running
(model, session) and two checks, the project map and SystemCare's own status (its row opens
SystemCare), with Scan PC (Cancel scan while one runs) when SystemCare is available. Lists
show "+N" instead of growing, and figures the snapshot does not know show a dash. Every
row and chip re-enters an existing owner (the terminal switch, the inbox request, the
learning or work command, Gmail reconnect, SystemCare, the app itself); the Dashboard
implements no CRUD or confirmation of its own, and no persistent badge or extra panel.

The gesture paints cached bounded data immediately, then performs at most one
background refresh while the Dashboard remains open, with no model call or
persistent copy. The Outlook and Gmail rows submit the corresponding inbox request
to Agent chat without requiring typed input. The compact card
starts no dashboard timer, persistence store, or additional panel. The Dashboard hosts no profile editor —
personalization stays with the existing terminal `/profile` command — and it
does not create a raw memory, learning, prompt, rule, or credential editor.

### Interaction map

| Input | Result |
| --- | --- |
| **Win+Alt+M** | Summon and open input |
| **Alt**, then hold **Alt** | Tap Alt once, press and hold Alt a second time to listen, then release that second press to transcribe and send. This also resumes a paused Voice Chat |
| **Ctrl, Ctrl** | Toggle Free and Lock movement. While chasing, the cubes step aside once when the pointer comes at them over text (the I-beam cursor) with no button held, so text under them stays selectable; an arrow-cursor approach still catches them |
| Left-click cube | Open input; click again to close it back into the cubes (the draft is kept); while docked at a terminal, sync after selection |
| Hold a cube for two seconds | That cube brightens in the active skin's accent as the hold completes, then runs what Settings → General → Cube gestures chose for its corner: nothing, screen selection or one of MO's own apps and toggles. Defaults: top-left Clipboard, top-right nothing, bottom-left screen selection (the selected area darkens while dragging, and the full-resolution PNG opens in the existing image preview), bottom-right Focus. A change applies at the next hold |
| Right-click cube | Open the mini Dashboard (Now, You, System), docked over the two left cubes: beside an open composer (upper-right cube) and Focus (lower-right cube) it forms one block, and all stay open together; it always keeps one size, on every tab and whatever opens beside it: only the composer grows (upward, as you type) while the Dashboard stays lined up with the composer's top as it opened; right-click again to close it back into the cubes |
| Double-click cube | Expand the four grouped MO app tiles; click one to open or focus it |
| Mouse wheel over cube | Adjust Windows master volume |
| **Shift** + mouse wheel over cube | Brightness of the display under the cubes: a built-in panel that Windows drives gets its real brightness; any other display gets MO's own dim layer (click-through, excluded from screen capture, below the cubes) |
| **Win+Shift+Z** (or holding the top-left cube, or the Clipboard app) | Open the clipboard history in the one panel: newest first, text, images and file lists; click a row to copy it again, ask MO about it, remove it, or Clear all. Each copy is read a moment later, once per burst, so the app that copied and a paste right after it go first. Kept in memory only, never on disk; a copy an app marks as not for clipboard history (password managers do) is never recorded, and one that looks like a secret is masked and never offered to MO. Windows' own Win+V is untouched |
| **Esc** while MO acts on the computer | Stop it: a Desktop turn gets Panic Stop; a local MO Terminal using the computer gets its own Esc (the typed stop control). An Esc MO itself presses while acting, or one pressed in an MO Desktop panel (closing the composer), never stops anything |
| Drop files on the cube or the open panel | Attach locally (several at once; the composer's attach button also picks several); MO reads them and answers in the one panel. While the composer is open (any role, including Book Writer) or through its attach button, each file instead joins the sentence at the caret as a chip ([Image1], [File1]; point at it for a small preview with an X that removes the file, click for Remove, Backspace deletes it with its file) and goes with **Send**, MO told which file each name means; under Generate they are the request's references. An image shows its preview first and goes to MO with **Send** (image Tools); a separate paired-device transfer stays an explicit choice |

Screen selections are saved under the active private profile's ordinary
`media/attachments/gallery` catalog and appear in MO Files. Escape or right-click
cancels selection. The selection overlay is transient and leaves the rest of
the screen visually unchanged.

The card supports mouse and keyboard operation. Tab/Shift+Tab and arrow keys
move through visible controls, Enter/Space activates, and Escape closes. In the
composer, common selection, edit, paste, Home/End, and navigation keys behave
normally. The reply card's arrows browse MO replies (the composer browses in place); the clock opens only the
isolated Desktop conversation namespace. `/new` archives a non-empty Desktop
conversation and never clears a terminal session.

## Terminal and Everywhere continuity

Desktop discovers live terminals from their real heartbeats and remembers one
selected binding. One live terminal can be selected automatically; multiple
terminals produce an explicit choice. An offline selection stays visibly
offline instead of silently changing to another process. When asked how many
terminals are live, what each is doing, or whether one is stuck or done, Desktop
injects a bounded current count, focus and board progress ("1/2 tasks done; now:
...") from that same native route. With no Terminal open, "what was my last
Terminal work?" is answered from the newest saved Terminal conversation's last
request and its age. One immediate detail follow-up reuses the route; shell
parsing, screenshots, and window counts do not.

Sync transfers bounded current focus into the Desktop conversation as context.
It does not copy a terminal transcript or taskboard and does not invent a
synthetic user message inside a tool chain.

Project implementation handoff is separate from sync. Desktop queues the exact
request as one normal turn to a heartbeat-proven Terminal in the same project.
If no such Terminal is live, Desktop opens one visible Terminal with that
request as its first normal turn (as a live Terminal gets it; MO Design's Build
starts a goal instead), in MO Shell (MO's own window for the same Terminal) when
it is built, else in a console. A request that names where to run it ("... on
the server", "... on the MO host"; a server topic such as "fix the server
timeout" stays local) goes to the paired MO host instead: Desktop uses the host's
running Terminal for the project the host lists under the same folder name, or
starts one there, hands it the request as one normal turn
(`/api/mo/terminals/{id}/turn`, see `mo_everywhere/README.md`) and opens MO Shell
with a pane on that exact Terminal. A local path is never sent; when the host
lists no such project, Desktop says so and names the projects it has. On a PC
paired with an MO host, the first request for a project that names neither side
asks once, **This PC** or **MO host**, runs it there and remembers the pick for
that project in the private runtime preferences (keyed by a hash of the project
path, beside its language-server choice); later requests go straight there, and
"... on this PC" or "... on the server" in a request still wins. Any other reply
lets the question lapse. An unpaired PC never asks. The handoff does not make Desktop a taskboard worker and does
not copy Terminal progress back into the companion conversation; for an hour
after it, a question about that work without naming the Terminal ("is mo stuck
with that goal?", "is it done?") gets the same native status. Asked to "open
one terminal with 4 panes" (or "run mo in 4 splits", "... on the server"),
Desktop opens MO Terminal the same way, already split with
`/workspace open N local|host`. A message that only asks to open MO ("run mo
for me", "open a new MO Terminal", "... on the server") opens one MO Terminal
the same way (on the server, with one MO host pane) instead of a status answer.

Everywhere lifecycle updates reuse the glance label. Active remote work stays
quiet; completed, paused, cancelled, or failed work may produce one bounded
notice. An explicit Android-pairing request uses the serving hub's canonical
one-use pairing owner and displays the QR in the existing attachment panel.
Provider prose cannot mint a grant.

### MO Phone

**MO Phone** is a separate Desktop app in the same shape as MO Files, opened
from the cube launcher beside the other apps. Its compact native WebView workspace
shows the selected device, separate Mirror and Trackpad controls, Full screen,
Keep a frame, and a shortcut to the existing MO Files app. Connection, access and
diagnostics open in a contextual details drawer. Colors, fonts, buttons, switches
and window effects follow the current Desktop visual state; the shared cube
entrance forms Phone's handset before revealing the normally placed window.
There is no Tk Phone renderer or embedded file browser.

Device discovery and actions run on one serialized worker. Repeated refreshes
coalesce, busy controls show the current operation, and tool/authorization
failures remain visible. Discovery, Trackpad and frame capture need ADB;
scrcpy availability affects only mirroring. Separate indicators show verified
Hub health and the Desktop Live Control host; neither claims Android permissions.
**Add phone** opens **Phones & access**, showing paired Android registrations
separately from local USB/Wi-Fi connections. Create a fresh QR for each additional
phone, or connect and authorize it by USB, Refresh and select its ADB connection.
The existing **Create pairing QR** action reuses the authenticated coordinator
grant, QR renderer and private expiry cleanup. A rejected host is labelled
**Host needs pairing**, with steps to restore its separate identity. Setup checks
do not issue grants, and no idle network polling is added. Closing stops the
original Trackpad session after any current device boundary finishes, then exits
the host if no mirror is running. A separate scrcpy mirror keeps its existing
hidden owner until it ends. Reopening during mirroring keeps that workspace;
after an idle close it opens a fresh one. Desktop exit or loss of its control pipe disposes the host
and Trackpad resources. See [Phone's contract](phone/README.md).

Mirroring is scrcpy's job and MO does not reimplement it. scrcpy streams
hardware-encoded video on one channel and carries mouse and keyboard on
another, which is why it is smooth and responsive and why a second frame-grab
path inside MO would only compete with it. MO finds the tools, lists devices,
and runs the session:

```bash
winget install --id Genymobile.scrcpy
```

The phone needs USB debugging allowed. Over USB it is plug-and-go. For Wi-Fi,
plug the cable in once and press **Wi-Fi**: MO reads the phone's private LAN
address, enables wireless debugging, remembers the address, selects the verified
wireless adb endpoint, and reconnects to it on every later refresh even if USB
is also present. The cable can then come out, and an MO, Desktop, or PC restart
does not require it again while the phone's wireless listener remains active.

Android 10 and lower reset that listener when the phone reboots. The physical
cable is therefore still required once after a phone reboot, but after one
pairing with the current MO Phone, reconnecting that same phone over USB is
enough: MO recognizes it from a one-way private device binding, re-arms Wi-Fi,
and selects the wireless transport automatically. A router-assigned address
change is recovered through that same exact-phone USB path. An unrelated USB
phone is never opted into network debugging automatically. A phone that is not
on a private network is refused rather than exposing its debug bridge, and a
phone that has not accepted the debugging prompt is still listed and says so.
Each deliberately paired phone retains its own saved Wi-Fi endpoint and identity
binding. A bound endpoint must return that same phone identity before MO accepts
the reconnect; updating one phone's address preserves the other saved phones.

The session window is titled *MO Phone*, so a streaming tool can capture it by
title as an ordinary window source — which is why MO needs no virtual camera
driver to put a phone camera into a stream.

**Keep frame** saves one screenshot as an ordinary MO artifact through the
existing attachment home and provenance index, landing in
`media/attachments/gallery` and appearing in MO Files. That single frame is what
lets MO check a phone it is building for instead of inferring from logs. It is
taken on demand and never streamed.

Video recording stays with Windows or the streaming tool that captures the
scrcpy window; MO does not create another recorder.

The Desktop host retains a local authenticated Trackpad/Board-input integration
for compatible private client builds. It is not present in the current Google
Play client and is therefore not part of the public Android installation or
support path. Its client-side behavior and release evidence stay with the
private Android authority; the public Desktop code does not make it Store
available.

### MO Live Control

When Everywhere, native MO Live Control, and
`consistent_everywhere.live_control.host.enabled` are enabled, Desktop starts
one outbound host worker. It waits for a separately paired exact `remote_host`
credential and connects without opening an inbound port.

The host exposes only the primary display as changed, bounded JPEG frames. It
accepts input only from the matching short controller lease after a current
pixel observation and target validation. JSON control traffic uses WebSocket
text frames; JPEG data uses binary frames. Frames, pointer paths, keys, and
credentials are never persisted.

Host loss, lease expiry, revocation, Desktop exit, or the local remote-control
kill switch releases held input and invalidates the target. The access token
authenticates the WebSocket handshake; the live socket continues only while the
fixed device identity, exact scope, and revocation state remain valid.

When the host advertises `host_actions_v1`, an authorized controller may request
only the fixed start, portable-conversation start, or exact-owned-terminal stop
actions. Desktop launches the canonical MO entrypoint in a fresh interactive
console with a fresh instance ID; the caller cannot supply a command, arbitrary
arguments, working directory, environment, PID, or shell. The resulting
terminal advertises the ordinary `mo_session` lane. The split workspace may
reuse this actuator only when its opaque local `machine_key` matches exactly.

On Windows, ordinary text is injected as Unicode through the complete native
`INPUT` ABI rather than keyboard-layout-dependent key names. The Android
controller keeps its Keys and text dock above stable navigation-bar geometry,
including legacy immersive-mode devices.

See [MO Everywhere](../mo_everywhere/README.md) and the
[Android guide](../ANDROID.md) for pairing and controller
behavior.

## Voice

Voice is optional and lazy:

- Double-Alt hold-to-talk starts capture when the second Alt is pressed and
  sends when that same Alt is released. It uses `sounddevice` plus either
  `faster-whisper` or Windows SAPI. With **Hold to talk** off nothing listens:
  double-Alt only says to turn it on in Settings. A second Alt released at once
  is a stray tap and is dropped without a transcription or a "No speech
  detected." card.
- A spoken request is answered by the voice conversation layer, not by a full
  MO turn. One fast request to `voice.conversation_provider` (thinking off, a
  small prompt) streams the reply, and each sentence is spoken as soon as it
  is written while later ones are still arriving. Stop, repeat and "what are
  you doing" are answered without a model. Requests that need tools, apps,
  files, the screen, mail, memory or the web get a short spoken acknowledgement
  and are handed, in the operator's own words, to a normal MO voice turn, which
  does the work and speaks its result. While it works, MO says briefly what it is
  doing ("Opening it now.", "Checking the screen.") instead of going silent, and
  you can keep talking: questions are answered right away, and new work waits
  its turn behind the running task.
- Local speech output uses an isolated Piper worker and the configured output
  device. **Speak typed replies** extends that output to requests entered as text.
- **Your own voice (optional).** MO ships no cloned voice. Settings → Voice →
  **Your voice** lists MO's voice, a voice made from your recordings
  (`<voice root>/profiles/my-voice/`) and a trained RVC voice you set in
  `voice.clone_model` yourself; choosing one takes the `.index` beside it, the
  **Pitch** row shifts it, and **Status** says whether it loaded. **Record my
  voice** in the same group shows one line at a time (then a few free-talk
  prompts in your own language): Record, read, Stop. MO records at the
  microphone's own rate, checks each clip (too short, quiet, loud or noisy, with
  what to do) and keeps only good clips, privately, in
  `<voice root>/profiles/my-voice/recordings/`; about five minutes of good
  speech is enough to make a voice. **Make my voice** then does the rest with
  the trainer installed on this computer (`voice.trainer_path`, an installed
  Applio; the **Trainer** row says what was found): one background process at
  below-normal priority prepares the good clips, extracts and trains (RVC v2,
  40 kHz, HiFi-GAN, from Applio's pretrained voice), copies the voice and its
  index into `profiles/my-voice`, saves it as your speaking voice and removes
  its own training folder. It survives a Desktop restart; Stop or an
  interruption can be continued from the last checkpoint. When the voice is
  ready MO switches to it and says a sentence in it. Every spoken
  sentence is then converted through audio.cpp's `rvc` family. MO
  keeps speaking in its plain voice while the clone loads (a minute or two on
  a small GPU), and for any sentence the clone cannot convert. The clone runs in
  one resident `audiocpp_server` process owned by the voice worker; it ends with
  the worker, even after a crash. Place an audio.cpp release under
  `<voice root>/engines/audio.cpp-<version>/<backend>/` and its RVC base package
  at `<voice root>/models/audiocpp/RVC-GGUF/rvc-f16.gguf`. Checkpoints that store
  their pitch flag as `True` get a fixed copy under `<voice root>/cache/clone/`;
  the original is never modified. On a small laptop GPU conversion takes about
  as long as the sentence itself, so the first word comes 2–3 s later than with
  the plain voice. MO says beside the cubes when your voice is ready, or why it
  did not load (it then keeps its own voice), and logs it; asking MO whether
  your voice works answers from that state. Use only a voice you have the right to use.
- Continuous Voice Chat explicitly loops listen → reply → speak → listen while
  its separate switch is enabled. Manual double-Alt capture does not enable that
  loop. Voice Chat re-arms even when a turn produces no audio.
- Whisper inference lives in one supervised worker that starts on the first
  voice-capture demand while the parent records audio. Repeated requests reuse
  it within the bounded idle lease; disabling Voice, lease expiry, or Desktop
  shutdown closes the exact worker. Transcription may use a bounded prompt made
  from the three most recent operator utterances; assistant and tool text are
  excluded.
- One playback thread owns the audio device; stop/cancel is checked between
  bounded slices.
- Desktop records transcription time, accepted-answer time, and first-audible-
  PCM time in its private trace. Piper's worker-start event is synthesis state;
  the cube enters speaking state only after the first PCM slice reaches the
  output device. An MO turn's speech starts from its accepted final reply so
  rejected drafts, tool-call text, and completion-gate rewrites are never spoken
  as answers. The conversation layer speaks only its own reply text, sentence by
  sentence; tool-call arguments never reach speech, and emotion tags only select
  delivery and the cube's reaction. Both drop command/code detail and Markdown
  punctuation from speech while the complete text remains visible in the reply
  bubble. The installed Piper voice is English, so Arabic replies stay visible but
  unspoken unless you choose an Arabic Piper voice you placed in
  `<voice root>/models/piper/` (Settings → Voice → **Arabic voice**, saved as
  `voice.arabic_model`); then each reply is spoken with the voice for its main
  script, and a voice clone converts both. The English voice never reads Arabic.

Voice dependencies and models live in private state, not the product checkout.
The core companion still runs when optional voice packages are absent.

The standalone installer owns readiness, disclosure, update, and removal:

```text
python -m mo_desktop.voice.install status
python -m mo_desktop.voice.install prepare
python -m mo_desktop.voice.install install
python -m mo_desktop.voice.install update
python -m mo_desktop.voice.install uninstall --yes
```

`status` always identifies the pinned Piper/Joe package and licenses and
verifies the installed model against the digest recorded after a successful
warm load. An older complete install whose marker predates digests remains
available but is reported as needing one explicit `update`; a present but
wrong digest fails closed. Install/update downloads into managed temporary
storage, validates the model plus required sidecar, warms it, and only then
promotes it. Exit MO Desktop before update or uninstall. Uninstall removes only
voice-owned runtime, model, cache, temporary, and profile directories;
unrelated entries under a custom root are preserved, and so are your
recordings and the voice made from them (`profiles/my-voice`) unless you add
`--include-my-voice`.

## MCP, profile apps, and roles

MCP servers expose their tools through the existing MCP manager and its exact
allowlists. Desktop maps MCP presence through MO skin tokens and character
primitives; servers cannot inject a palette, renderer, or agent authority.

`core/local_extensions.py` is the profile-extension boundary. An admitted
profile may contribute Desktop apps with their own cached windows, reached
through the cube launcher and styled with the exact active Desktop visual state.

Profile-authored reviewer roles are persistent tool/lane scopes, not personas.
An explicitly selected role stays active until dismissal. Each role chosen in the
composer keeps its own conversation: choosing it opens that role's thread (its
history, its replies to browse), Default role returns to the original one, and the
thread that was open reopens after a restart. New conversation starts that role's
thread fresh. A role named inside a request still applies within the current thread.
The [Desktop maintenance contract](MAINTAINING.md) owns the detailed boundaries.

## Configuration

**Settings → Tools & connections → Connected Chrome** is a lifecycle adapter over
`core.browser_bridge`: it reports the current bridge state, repairs or removes
this checkout's native-host registration, and copies the extension folder for
Chrome. Browser tools automatically verify and repair that registration on
first use; a valid registration renders as neutral, disabled **Bridge ready**,
while **Repair bridge** remains an explicit recovery action. It does not add a
`mo_desktop` setting or background service. Chrome still owns the one-time
**Load unpacked** installation. Browser tools attach the requested ordinary tab
on first observation; the toolbar action remains an optional direct connect or
stop control. The card reuses the active Desktop palette, typography, spacing,
button styles, corners, and responsive content width. See the
[MO Connected Tab guide](../clients/chrome/README.md).

The private `mo_desktop:` block is the single configuration source.
`settings.py` owns typed visual defaults and validation; the Settings panel
applies supported visual changes live and persists them through the same
configuration. `mo_desktop.visuals` publishes one immutable, strict visual
state containing the active skin, panel/button metrics, and derived spacing.
The companion, MO Files, MO Phone, Settings, tray/dialog surfaces, pointer
overlays, and MO Design all consume that exact state. A change repaints their
existing owners in place and preserves page, pane, editor, transfer, device,
mirror, trackpad, and capability state; equal saves do not repaint.

Common settings:

| Key | Default | Purpose |
| --- | --- | --- |
| `enabled` | `false` | Master switch |
| `tray_enabled` | `true` | Show the resident tray icon |
| `action_receipt_seconds` | `300` | Successful same-session action continuation window (30–1800 seconds) |
| `character.size` | `84` | Cluster edge in pixels |
| `character.cube_count` | `4` | Four cubes; five adds a center cube |
| `character.glow` | `0.5` | Glow strength from 0 to 1 |
| `character.color_mode` | `skin` | Current MO skin or one `#rrggbb` value |
| `character.corner_radius` | `0.22` | Cube rounding |
| `behavior.default_mode` | `free` | `free` or `lock` |
| `behavior.follow_distance` | `64` | Lock-mode trailing distance |
| `behavior.follow_ease` | `0.16` | Lock-mode spring |
| `behavior.keep_above_apps` | `[]` | Exact executable basenames allowed to lift MO above their overlays. MO Shell is always included, and while MO itself acts on the computer the cubes stay above any always-on-top window |
| `behavior.dim_level` | `0.0` | MO's dim layer level (0 to 0.9) on displays without hardware brightness; saved after Shift + wheel, also set in Settings |
| `behavior.clipboard_items` | `50` | Clipboard history size (0 to 200, memory only); `0` turns the listener off |
| `panel.padding` | `16` | Shared Desktop panel padding |
| `panel.corner_radius` | `12` | Shared Desktop window/panel radius |
| `panel.button_padding` | `8` | Shared Desktop action padding |
| `panel.button_corner_radius` | `6` | Shared Desktop action radius |
| `voice.stt_enabled` | `false` | Double-Alt hold-to-talk request with a spoken reply |
| `voice.stt_idle_seconds` | `180` | Whisper worker idle lease |
| `voice.stt_languages` | `""` | Languages the operator speaks (`"en, ar"`); Whisper picks among them instead of guessing any language. Empty detects any |
| `voice.stt_worker_timeout_seconds` | `180` | Whisper worker request ceiling |
| `voice.tts_enabled` | `false` | Also speak replies to typed requests |
| `voice.speech_rate` | `1.0` | Piper speaking pace multiplier (0.5–2.0; 1.0 preserves the installed voice's default pace) |
| `voice.spoken_max_chars` | `320` | How much of a reply is spoken (80–4000); longer replies end with "The full answer is in the bubble." Raise it to hear whole passages, such as a book read aloud. The visible reply never changes |
| `voice.chat_enabled` | `false` | Continuous listen → reply → listen mode, separate from manual double-Alt input |
| `voice.conversation_provider` | `""` | Configured provider name that answers spoken requests (a fast, non-reasoning model works best); empty uses MO's active provider. Settings → Voice → **Spoken replies** changes it live |
| `voice.arabic_model` | `""` | Absolute path to an Arabic Piper voice (`.onnx` with its `.onnx.json`) you placed yourself; empty keeps Arabic unspoken |
| `voice.clone_model` | `""` | Absolute path to your own trained RVC voice (`.pth`); empty keeps MO's plain voice |
| `voice.clone_index` | `""` | Optional matching retrieval index (`.index`) |
| `voice.clone_pitch` | `0` | Pitch shift in semitones (−24…24) from the plain voice to the clone |
| `voice.clone_backend` | `vulkan` | Which installed audio.cpp build runs the clone: `vulkan`, `cuda` or `cpu` |
| `voice.trainer_path` | `""` | Folder of an installed Applio that Make my voice runs (its own `core.py` and interpreter); empty means no trainer |

The complete disabled example is in
[`config.example.yaml`](../config.example.yaml). Additional runtime fields are
validated at their feature owners; do not copy machine paths, accounts,
credentials, or private role names into public configuration or documentation.
New built-in colorways use the one-file template and registry documented in
[`interface/skins/README.md`](../interface/skins/README.md); Desktop Settings
discovers that registry automatically, while panel/button geometry remains in
the settings above.

## Safety and privacy

- Desktop uses the same Gateway, sandbox, evidence gates, confirmations, and
  provider secret broker as MO Terminal.
- The `mo-desktop` session is isolated. Persisted history keeps user-facing
  conversation, not screenshots, tool payloads, or transient gate controls.
- Screen pixels are routed according to the configured pixel policy. Desktop
  never silently upgrades a local-only policy to cloud vision.
- Attachments are copied to private state and represented by bounded metadata;
  paths and contents do not enter Dashboard summaries.
- Exact executable basenames are required for opt-in topmost compatibility.
  The public default is empty.
- Panic Stop cancels the active Gateway turn and returns the card to one
  canonical stopped state.
- Native Live Control is outbound, short-lived, exact-scope, and memory-only.

## Architecture

The major ownership boundaries are:

| Source | Owner |
| --- | --- |
| `__main__.py`, `desktop_launch.py` | Lightweight launch, strict singleton, console-less resident |
| `companion.py` | GUI/turn orchestration and shared panel selection |
| `intent.py`, `persona.py` | Desktop intent and isolated turn context |
| `cube*.py`, `layered.py`, `card.py` | Character, movement, labels, and shared layered rendering |
| `reply_bubble.py`, `dashboard_card.py` | Conversation and compact Dashboard cards |
| `settings.py`, `settings_panel.py` | Validated settings and UI |
| `visuals.py`, `../interface/desktop_ui.py`, `../interface/desktop_widgets.py` | One strict visual state, semantic geometry roles, and shared Tk/native adapters |
| `voice/` | Optional capture, STT, local speech, and isolated storage |
| `live_control.py` | Guarded primary-display host |
| `everywhere.py`, `home.py` | Surface continuity, live terminals, and home docking |
| `mcp_visuals.py` | Neutral lifecycle-to-skin defaults for MCP presence |
| `design_studio/`, `mo_renderer.py` | MO Design shell, renderer bridge, Desktop-hosted command broker, shared theming, and native window entrypoint |
| `diagnostics.py`, `desktop_log.py` | Redacted trace and private lifecycle evidence |

The Windows renderer uses Pillow-backed sprites for the cube, label, and card.
Those surfaces, the launcher and screen selection use the shared native
per-pixel-alpha window adapter. File drops use Windows OLE and the launcher
opens Windows file/folder pickers. There is no chroma-key canvas fallback.
The resident uses `gui_loop.py` to wait for native messages, posted work and
monotonic timer deadlines. `tk_host.py` loads only for a private profile Tk
app; that optional host and its widgets are cleaned up on
the GUI thread at shutdown. Tk has not been eliminated from the whole project. A missing required visual adapter fails explicitly.
Optional `keyboard`, `pystray`,
`pywin32`, RTL, voice, and volume packages load only when needed; there is no
Node runtime.

Maintainers must read [MAINTAINING.md](MAINTAINING.md) before changing Desktop,
Live Control, profile apps, role, voice, or cross-surface behavior.
