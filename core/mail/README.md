# Agent chat mail capability

MO's serving Agent exposes one `mail` tool in Terminal, Desktop chat, and
authorized Hub chat. Gmail uses `core.mail.service.MailService` and the
profile's Gmail OAuth connection. Personal Outlook/Hotmail uses the signed-in
Outlook Mail page in an ordinary Chrome tab shared through MO Connected Tab.
It has no MO OAuth client, Microsoft app registration, Graph token, sync cursor,
or extra email window. Mail replies use each chat surface's existing Markdown
bold, spacing, and theme colors. Desktop's existing generic new-mail count
continues to come from Gmail sync; Outlook browser mail does not produce
background notifications.

For a first-time connection request in Agent chat, `mail action=connect` uses
the same owners as Dashboard setup. Gmail enables its existing private mail
preference, then starts browser OAuth and the initial sync if an operator-owned
Desktop client is ready. Otherwise MO tells the operator which Google project
step remains and directs them to Dashboard → Email to save the client ID and
secret privately. Outlook prepares the existing native bridge and, after MO
Connected Tab is loaded, opens or checks the signed-in Outlook Mail tab. The
operator still controls Google/Microsoft account sign-in, consent, and Chrome's
one-time Load unpacked step. `mail action=status` reports current readiness;
when Connected Tab is ready, its Outlook check can open the mail tab. The model
receives a bounded setup state, not secrets, mail content, or the local extension
path; detailed steps are shown only in
the direct operator result. No second credential store or setup service runs.

## Gmail setup

1. In connected Dashboard → Email, select Gmail and choose **Enable Gmail**.
   This writes the opt-in to the existing private runtime preferences. The
   equivalent authored setting is `mail.enabled: true` in private MO config.
2. Create an operator-owned Google Cloud OAuth client of type **Desktop app**,
   enable Gmail API, and configure consent for `gmail.modify`. Enter that
   client in the Dashboard form; MO writes the values only to the canonical
   private `credentials/gmail.env`. Never put credential values in a chat or
   this checkout. A shared public client has separate Google verification and
   data-handling requirements.
3. Choose **Continue with Google** and approve consent in the system browser.
   MO stores the token encrypted and sets the initial unread baseline. The
   existing `/mail connect` and `/mail sync` Terminal commands remain available.
   Existing mail does not generate new notices.

`/mail status` reports connection and count state. `/mail disconnect` removes
local authority and sync state; revoke access in Google Account settings if
desired. Google OAuth apps in Testing status can have short-lived refresh tokens.
Windows user-bound encryption owns the local token and bounded cursor. Other
hosts report `secure_storage_unavailable` until an equivalent store exists.

## Outlook/Hotmail setup and use

1. In connected Dashboard → Email, select Outlook.com and choose **Prepare
   Connected Tab** if prompted. Then load MO Connected Tab in Chrome as
   described in `clients/chrome/README.md` and sign in to personal Outlook
   Mail. The Dashboard shows the extension folder and an Outlook Mail link.
2. When no Outlook Mail tab is available, MO uses its existing desktop-open
   action to open `https://outlook.live.com/mail/` in the default browser and
   waits briefly for MO Connected Tab to attach. The signed-in Chrome profile
   and extension still need to be available. Keep only one ordinary Outlook
   Mail tab; MO refuses an ambiguous choice. Chrome can stop access. A tab held
   by another debugger is unavailable to the extension.
3. Ask MO in chat to list or search Outlook mail, read the latest or a visible
   result number (1–20), draft to one address, send that MO-created draft,
   archive, mark read, move to an existing folder, or delete a message. The
   `mail` tool uses `provider=outlook` for Outlook, Hotmail, and Live.com
   requests. Gmail remains the default for unnamed mail requests.

No Microsoft app registration, billing verification, OAuth credential, or
Graph API setup is needed for this browser path. MO acts on the current signed-in
Outlook tab. Inbox and search result numbers refer to the currently visible
list and may change when the mailbox changes; switch Focused/Other in Outlook
when needed. A search query can also be supplied with `read_latest` to open the
first result.

When Outlook leaves its list or search results unrendered in a hidden tab, MO
reports that the view did not load; zero rendered rows do not prove an empty
mailbox. A live background-tab search exposed this limit on 2026-09-29.

Opening a message can mark it read in Outlook. A move uses one
exact existing folder name in Outlook's own picker; duplicate or unavailable
names fail closed. A successful browser click is not a server-side move or
delivery receipt. Drafts remain in the Outlook compose UI; a new mail
draft belongs to its creating MO conversation and current browser tab. Send
requires a fresh `approve <code>` response in the same conversation within
three minutes. Before clicking Send, MO rechecks the exact committed recipient,
subject, body digest, and tab. Deleting also needs later-turn approval and a
recheck of the open message path and content digest. Check Sent Items or Deleted
Items after the corresponding click. Outlook categories/labels, Cc/Bcc,
attachments, and background notices are not supported by this browser path.
Message text is bounded to 30,000
characters and a longer read is explicitly marked truncated.

## Privacy and presentation

For an explicit Agent mail read or review, the configured model receives the
fetched result and can summarize it in the live reply. `review` supplies at
most 20 visible subjects and short previews per account; a full-message
summary requires a separate relevant read. The answer must say what was
inspected, not imply that a visible slice covers the whole mailbox. Connection
and change receipts still use the direct operator result. Dashboard Email
reads stay in the authenticated local view. Outlook page reads use fixed,
bounded extraction inside `core/mail/outlook_browser.py`; the general browser
snapshot/read tools are not
used for mail content. Incoming mail is untrusted data, never an instruction or
approval. Gmail follows Google's
[Workspace Limited Use policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy#limited_use_of_user_data).

Mail turns are omitted from saved MO session transcripts and profile learning.
Ordinary tool audit retains an action name, not mail arguments. Provider
monitor previews, Gateway continuity, and Hub job records omit mail content;
Hub jobs retain a placeholder. The live answer remains visible in that turn.
Gmail send/trash and Outlook send/trash require exact later-turn approval.

For read requests, MO uses its existing Markdown subject, sender, recipient,
and body labels. Terminal and Hub use their existing rich text renderers.
Desktop's existing reply card tints the mail title from its active visual
state; it has no mail-only palette, panel, app window, or skin.
Desktop notices use the existing generic count, emote, and chat activation
route. Android This Phone mail, Telegram mail delivery, accounting writes,
and automatic model triage are not provided here.

## Personalization, tasks, and dashboard

`core/learning/operator_messages.py` learns from direct operator statements;
`core/profile/facts.py` writes accepted short facts inside the private profile.
Mail content is external, untrusted input. The mail adapter does not promote
sender claims, dates, contact details, or inferred preferences into operator
facts, learning, or conversation memory. It does not create a second mail index:
Gmail API and Outlook Mail search remain the account search owners. MO can
summarize fetched results on an explicit Agent mail request. The connected
Email view groups its currently visible rows by literal wording in Gmail
subjects and short API snippets or
Outlook previews, including a tentative case/paperwork group. These are local
review hints, not a summary of a whole mailbox or a priority decision. An
explicit operator statement outside a mail turn can use the normal profile
owner; do not assume a sender's claim is an
operator fact. For an explicit organization request, `mail action=review`
shows the same bounded wording groups and short previews in Agent chat, with
up to 20 visible rows. The report marks Gmail's own Important, Unread, and Spam
labels when present; it does not turn those labels into MO importance judgments.
The configured model receives these bounded rows on an Agent review. The
operator can search more mail and confirm a Life item after
reading it.

`core/runtime/scheduler.py` owns existing scheduled tasks and reminders. An
operator can request one with explicit details through the existing scheduler;
the mail adapter never silently creates a task, appointment, or reminder from
an email. Confirmed commitments use `core/life/items.py`; dates there are
display fields and do not create scheduler jobs. There is no native calendar
appointment owner in this checkout.
Desktop's compact Dashboard shows Outlook.com and Gmail in its You view, with
Gmail unread status and up to two recent Gmail subjects beside current work on
Home. Its Outlook/Gmail quick links submit inbox requests through the existing
Agent chat. The connected full Dashboard has an Email tab with an on-demand
ten-row inbox, account search, a local message reader, and direct archive/move
controls through the existing mail owners. Move offers an inline dropdown of
existing Gmail user labels or visible Outlook folders; selection still goes
through the owner's exact destination check. Gmail shows its unread count,
estimated Inbox/search count, and an encrypted notification on/off setting;
turning notices off clears queued new-mail notices without disconnecting the
account or stopping sync. Outlook reads the signed-in Connected Tab when
selected. Its visible list number is checked against a preview digest before
read/archive/move; a changed row asks for refresh. No Outlook total, unread
count, or background sync is inferred. New email and delete route to the exact
Dashboard host's MO conversation for the normal approval flow, never an
arbitrary idle terminal. A Dashboard delete shortcut asks the operator to
choose the exact message again in chat; it does not include a fetched ID in
the model request. The last-action line is only current-session
presentation, not a new durable history. Mail text stays in the live local
display and never enters the canonical Dashboard snapshot, projection, or saved
conversation. A Dashboard shortcut that requests Agent mail reading uses the
configured model. The Workspace graph no longer shares space
with the mail list. The connected Life view lists operator-confirmed items by
group, date and status, with edit, done/reopen and forget controls. It also
shows bounded mail wording groups for review and existing scheduled tasks as
separate source projections, without turning either into a confirmed item. Email's
local reader can open a prefilled Life form for review. Gmail rows mark items
linked by message ID. Outlook has no durable browser ID, so a saved Outlook
item offers a title search rather than an exact source link or tracked badge.
Life details stay in the private state home and are excluded from the canonical
Dashboard snapshot and Agent tool result.
Everywhere and Android remain on Gmail's encrypted sync status only. Outlook
has no browser-independent unread cursor or background count, so no surface
invents one. Gmail's generic Desktop new-mail notification remains the only
background mail notice.

## Integration ownership

| Need | Existing owner |
| --- | --- |
| Gmail OAuth, sync, and API actions | `core/mail/gmail.py`, `service.py`, `secure_store.py` |
| Outlook current tab and bounded DOM actions | `core/mail/outlook_browser.py`, `tools/browser.py`, `core/browser_bridge.py` |
| Tool schema and Agent request routing | `tools/definitions.py`, `tools/__init__.py`, `core/agent/agent_turn_dispatch.py` |
| Mail-turn privacy and placeholders | `core/mail/intent.py`, `core/session/`, `core/gateway.py`, `mo_everywhere/jobs.py` |
| Gmail local commands and notifications | `core/agent/agent_slash.py`, `interface/command_registry.py`, `mo_desktop/companion_dashboard.py` |
| Connected full Dashboard Email tab | `core/dashboard/server.py`, `dashboard.html`, `dashboard.js`, `dashboard.css` |
| Accepted operator facts and scheduled reminders | `core/learning/operator_messages.py`, `core/profile/facts.py`, `core/runtime/scheduler.py` |
| Operator-confirmed life commitments and visible mail wording hints | `core/life/items.py`, `core/life/candidates.py` |

Add actions in these owners only when a concrete requested interaction needs
them. Review the current contracts before changing a surface. Keep profile
accounts, credentials, and private acceptance notes in the operator profile.

## Verification truth

The ignored maintainer overlay in `tests/` contains focused policy and local
integration tests. `tests/test_mail_local_e2e.py` uses a fake Google service;
`tests/test_mail_normal_model_e2e.py` is opt-in and uses synthetic mail with
the normal configured model. `tests/test_life_mail_normal_model_e2e.py` is
another opt-in check of a natural mail-review request, explicit confirmed
plan, and linked outgoing in an isolated profile. It checks that fetched
previews reach the configured model without automatically creating a Life item.
`tests/test_outlook_live_e2e.py` is a separate
opt-in real-account read/search through MO Connected Tab and the normal model, with
an isolated test state. It compares the operator reply and provider requests
without printing mail content. Neither synthetic nor live read/search checks
prove a rendered Desktop/phone view, actual move/archive/delete, or email
delivery. A live folder-picker dry run stops before the Move click. Record live
draft review and a deliberately approved send separately. Do not claim delivery from a successful
click. The personal Gmail OAuth Testing setup is separate from a public client
release and its verification requirements. A public Gmail connection using
`gmail.modify` requires Google's restricted-scope verification. The current
Outlook Chrome extension is loaded unpacked by each operator; general Chrome
distribution requires a published Web Store extension or an eligible managed
enterprise deployment. Neither external publication step is implied by a
successful personal connection or local test. See Google's
[Gmail scope guide](https://developers.google.com/workspace/gmail/api/auth/scopes)
and Chrome's [distribution guide](https://developer.chrome.com/docs/extensions/how-to/distribute).
Google's [current Gmail quota and pricing guide](https://developers.google.com/workspace/gmail/api/reference/quota)
says standard API use has no additional cost within its daily billing threshold;
larger use may be charged when Google activates that pricing.
