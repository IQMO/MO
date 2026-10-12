# MO Terminal

MO Terminal is the conversation and command interface for MO. The footer shows
the current project, provider and model; the taskboard shows recorded work and
verification. Activity, a running process or a final reply alone does not mean
that a task succeeded.

Conversation lines share `transcript_grammar.py`: user messages start with `❯`,
MO replies and working updates with `■`, and context/tool notices keep the `│`
rail. Wrapped replies and paragraph breaks continue that rail beneath the
square; `└` closes each completed reply. Messages sent while MO works have no
blank separator, while fresh turns retain one.
One blank row separates the conversation from the live activity or tip; the
composer and footer stay at the bottom when the window is resized.

## Commands and the composer

Type `/`, use `/help`, or press `F4` to open the Command Center. Navigate with
arrows and Enter. Commands with additional choices open a submenu; commands
that need free-form text prepare the composer without submitting placeholders.
Reports and errors stay in the Command Center. Successful controls return to
the conversation with a notice.

- `Ctrl+E` enhances the current draft without sending it. The immediate preview
  corrects the text, then the selected provider/model can refine it. `Esc`
  restores the original.
- Large pastes remain unsent in a holder. Follow its Enter/Esc hint: Enter
  submits the held text once; Esc restores the preceding draft.
- `/terminal` or `Ctrl+T` opens an internal terminal for an ordinary command.
- `/skin` selects a [skin](skins/README.md). `/settings` opens Settings.
- `/usage` and `/status` show usage and runtime details. `capped ~…t` is an
  estimate for capped tool text; `compacted …ch` counts serialized characters.
  Neither is a provider-reported saved-token percentage.

## Queue, steer and stop

While MO works, Enter queues an ordinary message. It appears once in the
conversation and the footer says **Queued**. A second Enter offers that message
to the current turn at its next safe checkpoint; the footer says
**Steering · next checkpoint**. A late, unconsumed steer remains queued for the
next turn. There is no third-Enter stop shortcut.

`Alt+Up`, or Up on an empty editor outside the split workspace, restores the
latest pending message for editing. An unconsumed queue/steer is removed from
pending work; a steer MO already consumed is copied as a corrective follow-up.
Steering has a bounded capacity; if it is full, the message stays queued.

- The first `Esc` cancels the latest still-pending queued message or steer.
  Three Esc presses stop MO; incoming messages do not reset that stop count.
- `Ctrl+C` immediately stops the current turn while preserving queued
  follow-ups. Dashboard and connected-surface **Stop** do the same.
- A direct `wait`, `stop`, `pause` or `cancel` message also stops the turn.
  Requests such as “don't stop” or “stop Blender playback” remain ordinary work.
  With no active turn, a direct stop request returns **Nothing to stop**.

Inspection commands such as `/status`, `/projects` and `/settings` remain
available during work. Other commands wait in order. A `/model` choice made
during a turn applies after that turn and before the next queued input;
it does not change the in-flight request.

## Transcript and activity

In an ordinary terminal, use the host's mouse wheel, Shift+PageUp/PageDown,
drag selection and copy controls for full-session scrollback. Ordinary arrows
and PageUp/PageDown retain editor/history behavior. MO's split workspace and
terminal rail are keyboard-only.

The compatibility setting `runtime.scrollback_transcript: false` uses MO's
bounded managed viewport, where Up/Down and PageUp/PageDown scroll that viewport.
[MO Shell](../mo_shell/README.md) uses this managed viewport automatically;
its mouse wheel scrolls the terminal through PageUp/PageDown.

The activity line distinguishes current work, supplied **Knowledge**, graph
refreshes and verification. Provider activity shows **Waiting for model**,
**Reasoning received**, **Responding** or **Retrying model** when the corresponding
signal arrives; a connection or accepted request does not prove active reasoning.
The clock remains the elapsed turn time. Dashboard **Checks** shows the supplied
sources for the selected conversation. A supplied source does not prove MO used it,
and a skipped check is not a passed check. Edit locations describe the intended
change; their appearance is not proof that an edit succeeded. Failed commands
remain visible. Goal milestones and learning events remain in the transcript.

Images use Sixel pixel graphics in Windows Terminal native scrollback. MO Shell
and managed viewports use a portable color-block preview. The original image
file retains its full quality. Tables wrap or split into smaller column groups
when the terminal is narrow; right-to-left text keeps its logical copied order.

`/hints off` disables rotating discovery tips. Optional `hints.txt` in the active
MO state home replaces the built-in tips after restart. A short reminder about
uncommitted project changes is separate from rotating hints.

## Sessions, projects and reviews

Choose a saved session in the Command Center or use `/resume` to restore its
conversation and saved taskboard. Restoring a board does not execute it.
`/new` starts a new conversation. `/undo` removes conversation context while
leaving historical terminal scrollback visible.

After an interrupted or forcibly closed turn, saved requests and recorded
tool results can be restored. A missing tool result means execution is unknown;
check the actual state before retrying. Writes still in progress at exit may
not survive. `/goal resume` needs a paused worker in the current process;
after a restart, continue explicitly from the saved conversation.

`/projects` lists recent working folders with the current-folder marker,
session counts and last-opened times. It does not switch an existing MO
conversation's project. The Ctrl+B workspace rail selects project groups.

`/prt` reviews uncommitted changes when present, otherwise HEAD. `/prt .`
selects current changes, `/prt report` opens recorded reviews, and custom
commit/range/path targets remain available. Worktree/path reviews report
findings. Confirmed findings on committed targets can start correction and
focused verification, followed by reassessment. Reports distinguish **clean**,
**unresolved** and **incomplete**; those labels alone do not establish delivery
readiness. A review finishing during a turn becomes available at the next
provider checkpoint without interrupting the current response.

## Split workspace and Health

`Ctrl+B` shows or hides the terminal rail from the main composer, an MO-host
composer or a local terminal pane. The rail lists **+ New terminal**, **Health**,
**RUNNING** terminals and **PROJECTS**. Projects without a usable folder remain
visible as **no folder** and cannot start a terminal. Switching project groups
preserves their processes and drafts; the main MO conversation keeps its
original project.

New terminals are ordinary blank terminals on **This machine** or the **MO host**.
The rail can also attach an already-running MO-host terminal or bring another
local MO window to the front. The selected project's source is the default for
new terminals. Choosing another machine uses its matching project when
available, then its first listed project; on this machine, MO's starting folder
is the local fallback.

| Control | Action |
| --- | --- |
| Rail arrows / `1`–`9` | Select a row / switch to that RUNNING terminal |
| Rail Enter | Focus or attach a terminal, select a project, or open the selected action |
| Rail `x` | Close the selected attached pane |
| `Alt+Left` / `Alt+Right` | Move between the selected project's panes |
| `Alt+F` | Toggle the focused pane between the grid and full workspace size |
| `Alt+Up` / `Alt+Down` | Scroll the focused transcript or local history |
| `Alt+Home` / `Alt+End` | Jump to the oldest/newest history boundary |
| `Alt+X` | Close the focused added terminal; main MO remains open |
| `Ctrl+C` | Interrupt only the focused local terminal or MO-host turn |

Full-screen programs in a local pane receive their own PageUp/PageDown and
Home/End keys for those scrolling shortcuts, and own their remaining raw keys,
paste and `F4`. MO-host panes use a composer: Enter submits a complete message,
and `/` or `F4` opens that host's command menu. Typing `/` on a highlighted rail
pane selects it and opens that pane's command interface. On Windows, opening
an added terminal applies one host zoom-out step and closing it restores the
matching step; other hosts retain their native zoom behavior.

`/workspace` opens a terminal when the selected project's workspace is inactive
and reports status when it is active. Explicit controls are:

```text
/workspace new local|host
/workspace attach <terminal id>
/workspace next|prev
/workspace focus N
/workspace close [N]
/workspace single
/workspace status
```

`new local|host` requires a project on that source. `single` closes only the
selected project's added panes; other project groups keep running. Closing a
pane started on the MO host stops that terminal. Closing an attachment to an
already-running host terminal releases the attachment and leaves it running.

**Health** replaces the grid with machine and terminal resource readings while
keeping the rail. Alt+Up/Down and Alt+Home/End scroll it without moving pane
focus. Readings show their age; warming, partial, unavailable and stale results
remain explicit. Reopening Health requests a fresh reading. `/status` provides
the detailed runtime report. `/activity` separately controls the optional
Goal/Background/PRT panel.

## Roles, learning and personal settings

- `/role` selects a role for the current project, including Book Writer and
  Project Architect. `/role show` opens Mologrthim at this Terminal's bay;
  with no active role it selects Project Architect first. It does not start a
  task or replace another active role. `/role status` shows state and `/role
  off` leaves the role. Closing its view keeps the conversation.
- `/profile facts` browses saved notes, supports editing and confirms forgetting.
  `/schedule list` offers details, edit, pause/resume, next-tick execution and
  confirmed removal. Tab or Left/Right selects a result action, Up/Down scrolls,
  and Enter activates it. Confirmations start on Cancel; mutations wait during work.
- The learning menu shows pending review, accepted suggestions and status.
  **More actions** contains optional scanning, consolidation and skill imports.
  Automatic learning does not require opening the menu. `/profile mine` opens
  learning review; `/profile` and `/skills` retain other rules and authored skills.
  Undo retires generated learning packs recoverably; a failed retirement stays
  visibly active.
- `/doctor` and `/doctor --json` show general health. `/doctor layout` shows the
  read-only state layout. `/doctor personalization [--json]` audits profile
  structure, learning, recall, retention and project-file health without
  applying changes or claiming that saved guidance is semantically current.

## File transfers

`/send <path> :: <device>` selects a transfer target; ambiguous device names
require a more specific choice. `/transfers` combines transfers with local
queued/failed sends. `accept <id> [path]` confirms receipt on the target,
`cancel <id>` cancels a transfer, and `retry <outbox-id>` retries from the
retained sender copy. Transfer status appears in the normal notices/footer.

## Game Collaboration mode

Game Collaboration is an explicit, Terminal-only project context for users
building software games. It is off by default and does not activate from words
such as “game”, “level” or “character”.

Use `/game start` to bind the current project, `/game status` to inspect its
record, `/game review` for a read-only consistency summary, and `/game stop` to
leave without deleting the record. `/game pause` preserves the record while
disabling its context. `/game resume [project]` reloads a saved record in a later
Terminal session; this differs from `/resume`, which restores a conversation.
Explicit conversation selection/resume can restore its saved active or paused
Game Collaboration binding.

Record questions and proposals with `/game ask <id> <question>`, `/game decide
<id> <answer>` and `/game propose <id> <summary>`. Approval remains a separate
action: `/game approve <proposal-id>` or `/game reject <proposal-id>`. Answers
and proposals do not execute tools. Conflicting concurrent changes are rejected
rather than overwriting a newer decision.

Game records are private under the active MO state home's
`memory/work/game-collaboration`. Their context is used only in active Terminal
turns; Desktop, Board, Design, API, Telegram, scheduler and ordinary non-game
turns do not receive it.

## Dashboard connection

`/dashboard show` or startup `mo --dashboard` opens the native WebView Dashboard
attached to this Terminal. Dashboard commands and prepared requests select the
main MO pane while preserving other drafts. Prepared requests and shared images
still wait for Enter in the normal composer. Sharing an image keeps existing
text and appends its path once; a changed conversation is rejected before
modifying the composer. The Dashboard connection closes with its Terminal.

Design requests keep their own context, including text beginning with `/`.
They are not interpreted as Dashboard controls. See the
[Dashboard guide](../core/dashboard/README.md) for its controls and connection
behavior.
