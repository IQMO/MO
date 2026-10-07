# Inventory

Inventory is a basket of everything MO did with you, today first: conversations,
taskboards, goals, the files MO changed, generated pictures, songs and videos,
attachments, MO Design documents, lessons MO adopted and your projects' recent
commits. Search it, filter it, pick several items (click, or drag a card) and
drop them on a running MO Terminal along the bottom.

The picked items arrive in that Terminal's composer as one prepared message,
each with its record reference ("From my Inventory, I picked these: ..."). You
add what MO should do and press Enter there; nothing runs on its own.

## Owners

- `snapshot.py`: `InventoryReader` reads only existing records:
  `memory/sessions/conversations`, the taskboard ledger, `memory/work/goals`,
  `logs/file_operations.jsonl` (the newest 30 changed files), `media/generated`
  and the attachment index (thumbnails for the newest pictures), MO Design
  (`core.design.service.list_designs`, loaded off the refresh path), lessons
  still in force from `memory/learning/suggestions.jsonl`, and `git log` for the
  running MOs' projects. `running_terminals()` reads the heartbeat ledger.
- `bridge.py`: reads every four seconds only while the window is visible; `send`
  uses the existing `request` Terminal handoff
  (`core.design.terminal_handoff.queue_terminal_control`).
- `app.py`, `window.py`: the native WebView host on the shared window, theme and
  entrance owners, opened from the four-cube launcher's Work group.
- `basket.html`, `basket.css`, `basket.js`: the basket. Cards fall in and settle
  on springs; search matches rise above the rim; what does not fit waits behind
  a filter or a search. Reduced motion places cards without animation.

Inventory is not diagnostic and writes nothing; closing it never affects an MO.
