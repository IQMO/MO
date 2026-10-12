# Mologrthim

Mologrthim is MO's operations floor: one native window that shows what MO is
really doing across heartbeat-proven MOs, and the place to assign work, hire a
proposed specialist or pause a goal. It is a projection of records. It never
runs an Agent, never dispatches a specialist directly and never stops work.

## What the floor shows

| On the floor | What it represents |
| --- | --- |
| One bay per running MO | Terminal or Desktop's current request and taskboard |
| Brain and goal ring | Current goal step, pausing or paused status |
| Specialist stations and rank plates | Recorded work, verified reports and measured difficulty |
| Hiring pads and CVs | Proposed specialists and why they were proposed |
| Beams between MOs | Messages between running MOs |
| Taskboard and power walls | Task progress and observed resources |
| Learning wall and archive shelves | Learning status and recent indexing activity |
| Brain panel | Profile, memory, adopted lessons and pending reviews; refreshed every ten minutes |
| MO Care | Recent MO problems and their report cards |
| Review gate | Verified or refused worker reports |
| Profile commands | Shown only when your private profile supplies them |

Rank is earned, never guessed: points = each report the architect verified ×
how hard the task was (simple 1, moderate 2, complex 3, measured from the work
the worker did) − 2 × each correction; tiers at 5, 15 and 35 points. A report
nobody checked counts as a run only. An accepted report without measured difficulty
is marked ungraded and earns no points; difficulty is never guessed. A specialist
with no checked work says so.
Worker events written before the project root was recorded match a specialist
only when exactly one open project has that role. Work still open when its MO
closed or crashed shows as stopped, rather than continuing to appear active.

## Acting from the floor

Work requests reach the selected running MO for handling in its own conversation:

- **Assign** sends a normal request to the chosen MO; its lead decides who works
  on it, dispatches the specialist and checks the report, so rank stays true.
  "Ask <specialist>" phrases the request for that specialist; it still goes
  through the lead. "New MO" starts a normal Terminal and sends the assignment as
  its first normal request. Assignments preserve paragraphs and length within
  the Terminal's request limits.
- **Hire** sends "hire <name>" to that project's MO, which handles it locally.
- **Pause after this step** and **Resume** send `/goal pause` and `/goal resume`
  as that MO's own commands; the running step always finishes.
- **Open terminal** brings that MO's terminal forward. MO Desktop takes requests
  in its own composer.
- **Investigate** on a MO Care report opens the separate report terminal;
  **Dismiss** marks it in MO Care's record.
- The **+** in the title bar starts a fresh normal MO Terminal.

## Visible records and refresh

The observer uses the existing heartbeat ledger (up to 16 current records). The
scene pages through six MO bays, follows the selected MO's page, and fits five
specialist stations per project bay and four hiring pads. The brain's directory
and taskboard links reach every record in the snapshot; overflow is not lost or
overlaid on another station. Recent indexing is historical activity, not proof
that an indexer is running now. Refresh failures remain visible without clearing
the last good snapshot or claiming that no MO is running.

Actions revalidate the exact Terminal heartbeat and process identity before
queueing. Opening the floor starts no work or console. The **+**, **New MO**, and
**Investigate** controls intentionally open their normal Terminal routes.
## Visibility and closing

The floor refreshes every two seconds while visible. Hiding or minimizing pauses
observation, resource sampling and animation. It follows the active skin and
respects reduced motion. Selected conversation excerpts are redacted.

The floor writes no state of its own except a dismissed MO Care report. Closing
it releases its window and never stops an MO or its workers. Opening it starts
no work; use an explicit action to assign or resume work.
