# Life records

## Fresh profile path

A new profile does not need a file per email, payment, or case. MO creates the
declared private Life JSON records on the first confirmed write; the connected
Dashboard reads those same owners. Connecting Gmail or opening Outlook only
grants access to the account's current mail. It does not import mail into Life,
profile prose, conversation recall, or a second search index. Account search
remains with Gmail or Outlook. A user can inspect a visible message in Email,
review its wording hint, then confirm a Life item in the local form; a direct
chat request can record an item or money movement when the operator supplies
its details outside a mail turn. Explicit reminders use the scheduler separately.

This path does not turn a new user's mailbox into a complete personal plan:
the visible wording groups cover only the current bounded result list, Agent
chat can summarize mail it explicitly reads, and no payment amount or
obligation is recorded from a sender without operator confirmation. The
Dashboard does not treat an empty Life store as proof that the user's life is
organized.

`core.life.items` owns one private list of commitments the operator chose to
track. It lives at `memory/life/items.json` under the resolved MO state home.
The record contains a short title, one group, optional confirmed date and
notes, open/done status, revision, and source. A payment or subscription can
also describe an operator-confirmed expected amount and currency, repeat
interval, and total installment count. These fields describe a plan; they do
not record a payment, advance a date, mark an item done, or create a reminder.
A case uses this same record with an optional operator-chosen area (medical,
municipality, work, paperwork, or a custom label), reference, and up to 100
dated updates. Each update has a kind (conversation, paperwork, step, note),
brief operator-confirmed summary, and optional reference. Its open/done state
appears as Open/Resolved in the Dashboard; only an explicit operator action
resolves it. References are labels, not credentials or copies of documents.
This private Life owner is separate from the opaque `personal/` area,
credentials, project taskboards, profile facts, and learning stores.
A Gmail source keeps its message ID; Outlook has no durable browser message
ID here, so its source is only identified as Outlook. The file is locked across MO processes and written
atomically. The Dashboard and Agent tool call this owner; neither keeps another
life-item ledger.

The connected Dashboard **Life** view is the local reader and editor. Its
**Email** view uses `core.life.candidates.visible_mail_signals` on the ten
currently visible Gmail subjects and short API snippets or Outlook previews.
Case and paperwork words can select the same tentative group as a confirmed
Life case, without creating one. These literal wording hints are navigation
aids. They do not read full message bodies, decide that a debt or appointment
exists, rate urgency, create a life item,
move mail, or write profile facts. Gmail rows can say whether their durable
message ID was explicitly tracked. Outlook rows cannot claim that status.
When Life opens, it reuses the authenticated Email glance to show bounded
possible groups for review and routes each group to its Email filter. The
review display does not add records. Life also reads the existing scheduler
job owner for a separate name/status/next-run list, without exposing job
prompts or treating scheduled work as confirmed commitments. Curated profile
facts and taskboards retain their existing meanings and do not silently
become Life items.

Natural tracking and case requests route to the `life_item` Agent tool on the first
provider request. The tool accepts direct operator requests and returns only
counts, IDs, revisions and status to the model. A change can select by the
exact title the operator supplied; matching happens in this local owner and
ambiguous matches fail without revealing stored titles. It cannot run during a mail
turn or mutate from a scheduled turn. Private titles, dates, and notes appear
in the authenticated Dashboard and in a direct local Agent chat result after
the model turn; they are absent from the tool result sent to the model.
The `show` action displays one case's recorded history locally; `add_update`
appends a confirmed event with the same revision check. This is a timeline of
supplied events, not an inferred summary of email or unrecorded conversations.
The Dashboard keeps case history collapsed until opened so many cases do not
crowd the page. A date in a life item does not schedule an alert. The Life
Scheduled tasks section can create a plain reminder (local Desktop notice,
without a model) or an Agent task (configured model), and can queue, pause,
resume, or remove it. A Life row's Remind control only pre-fills the task text;
the operator still chooses when and saves it. These controls use the existing
`core.runtime.scheduler` store and require the private `scheduler.enabled`
setting plus a running service to execute. Resident MO Desktop starts that
existing service when enabled and stops it on exit; the scheduler's singleton
lock prevents a second MO runtime from running the same jobs. Gmail new-mail
notices already use Email settings; a scheduled Agent mail check uses the
existing mail tools and ephemeral-turn boundary. Outlook still requires a
signed-in Connected Tab.

`core.life.money` owns a second, distinct record under the same private Life
domain: income and outgoings the operator explicitly records. A money entry
has a title, positive decimal amount, three-letter currency, date, custom
category, optional notes, exact revision, and operator provenance. An outgoing
can optionally reference an existing confirmed payment or subscription by its
Life item ID; the local Agent can resolve an exact operator-supplied title.
The Dashboard derives the recorded installment count from this money store,
without copying payments into the commitment record. Forgetting a commitment
does not erase its recorded money history. It is not
an inferred email fact, an open bill, a scheduled reminder, a bank transaction
feed, or an account balance. `memory/life/money.json` is locked and written
atomically like confirmed commitments. Monthly totals use decimal arithmetic
and stay separate by currency, without conversion. The Dashboard Life view
shows the month's exact totals, outgoing category breakdown and editable
entries; its top overview charts six months of recorded income/outgoings in
separate currency groups, with a zero baseline before the first record. The
month view and timeline come from one locked read through the authenticated
local route. The
`life_money` Agent tool uses the same direct operator boundary as `life_item`:
the model receives only counts and opaque IDs/revisions; local chat displays
the recorded details after the provider turn. Exact title selection is local
and ambiguous titles fail. Scheduled and mail-sensitive turns cannot create
or edit money records.

These Life stores are transaction and commitment sources for the current
operator, not a new profile fact or learning ledger. `Profile`, accepted
operator-message learning, and the scheduler keep their current owners. Direct
operator statements about durable preferences may follow those existing
owners; mail wording and recorded entries do not automatically write facts,
learning, reminders, or one another. A future bank connection would need its
own consent, provenance, reconciliation, and import authority before any
entry could be called bank-synced.

When changing this workflow, update its Life record owner first, then the
Dashboard or Agent adapter that needs the change. Preserve explicit operator
confirmation, mail-turn transcript masking, profile fact and learning ownership,
scheduled tasks, Dashboard skin tokens, and the browser's single current-tab
boundary. Keep new deterministic checks in the ignored `tests/` overlay and
verify the real connected Dashboard for interaction claims. An Agent summary
is limited to the messages actually retrieved; a sender's claim remains
unconfirmed until the operator decides to track it.
