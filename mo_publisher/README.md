# MO publisher service

This optional process owns the public home, support, privacy and deletion pages,
and the Android schema-1 AI-report receiver. It has no Hub, Agent, provider,
customer-account, purchase, or remote-control authority. Do not mount it into
`mo_everywhere.app` or supply a Hub profile. Full customer identity and commerce
remain a separate future boundary.

## Ownership and operation

- `pages.py`: escaped public copy and configuration validation; CSS consumes
  the canonical default skin contract, with no scripts, cookies or analytics.
  The home page introduces MO Agent (an example terminal session where switching
  the model changes only the route, the map's differentiators and pillars, and
  install), then carries the Android companion section; its model switch and
  install tabs are CSS radio inputs, so no script is needed. Support, privacy and
  deletion stay Android-focused. The customer-facing copy must match the current
  Google Play feature boundary; distinguish the published closed-test baseline
  from unreleased corrections.
  The Android section leads with the mobile companion to MO Agent: host-backed work, projects,
  scheduling, explicit shared conversations and configured tools. Distinguish
  scoped Hub access from full-profile replication, which uses a separate private
  SSH/Git lane between trusted computers. Remote access, configurable providers and selected images are shared source
  features; do not present private owner-device capabilities as Store features. Deploy
  new feature claims together with verified release availability.
- `app.py`: bounded HTTP admission and public routes. Existing optional
  `requirements-everywhere.txt` supplies FastAPI/Uvicorn; no new dependency.
- `reports.py`: strict Android payload validation, transactional receipts,
  duplicate handling, queue capacity, review status and secure SQLite deletion.
- `manage.py`: local/SSH review and deletion. No public read, admin or deletion
  route exposes stored reports.

Run `uvicorn mo_publisher.app:create_app --factory --host 127.0.0.1` with a
deployment-selected port, `--no-access-log --no-proxy-headers`, one worker, and
an independent absolute `MO_STATE_HOME`. Restrict the service OS user to this
home and public source.
Runtime data uses `core.state.paths.resolve_state_path()`; reports are never
checked into source or placed in a publicly served directory.

`MO_PUBLISHER_CONFIG` is an absolute private UTF-8 JSON file containing exactly
`publisher_name`, `support_email`, `effective_date` (ISO date), `hosting`,
`backups`, and `support_retention`. The final three fields are factual public
privacy paragraphs, verified against the deployment. Keep individual identity,
actual domain, email, server paths and infrastructure records out of this repo.
There are no example credentials or default operational claims.
`publisher_name` is the truthful public operating name; it must not imply a
legal entity the operator has not established, and it does not replace any
legal-identity disclosure required for the actual deployment.

The HTTPS proxy must route only the four public pages, `/style.css`, and
`POST /api/publisher/ai-reports` to this process. Keep `/health` loopback-only.
Reject other publisher paths. Existing private `/api/mo/` routes retain their
authentication and owner. Set a 256 KiB body ceiling, bounded body/read
timeouts, no request-body spooling, disabled access logs and no report-body
logging. Add per-address admission limits (10 initial reports, one/minute
thereafter, HTTP 429) in the reverse proxy; Android additionally limits local
submissions to 10/hour. The service caps
the persistent queue at 10,000 reports and new admissions at 500/hour. Native
reports require no CORS, cookie, shared APK secret or authorization header.

## Reports and retention

Receipts are issued only after a successful durable transaction. An identical
retry with the same report ID returns the same receipt; changed content using
that ID returns 409. Unknown fields, credentials, invalid hashes, invalid JSON,
duplicate fields, unconsented content and excessive text are rejected without
echoing input. Conversation inclusion is optional; explanation text can itself
contain personal data and is subject to the same retention.

All report fields and review outcomes expire 30 days after submission. Startup,
admission and review remove expired records. **Also install and monitor an
hourly independent `python -m mo_publisher.manage purge` timer**, so expiry
continues when HTTP traffic stops or the web process is unavailable. A release
requires timer and deletion evidence. SQLite uses secure deletion and rollback
journals, not WAL; filesystem snapshots, exports, mail and backups need their
own verified expiry and privacy disclosure. Do not promise that SQLite deletion
erases an infrastructure snapshot.

Using the service's dedicated environment, the operator runs:

```text
python -m mo_publisher.manage pending
python -m mo_publisher.manage export <report-id> --output <absolute-private-file>
python -m mo_publisher.manage review <report-id> <outcome>
python -m mo_publisher.manage delete <receipt>
python -m mo_publisher.manage purge
```

Routine listing omits report content and receipts. Human review exports one
record to a newly created private file; delete that copy immediately afterward.
Outcomes are `action_required`, `resolved`, `no_action`, or
`insufficient_context`. `action_required` remains in the work queue. A status
change does not implement a safety fix: investigate affected provider/Hub/app
behavior, reproduce safely in isolated state, correct it and verify before
closing. Never paste customer report text into coding-agent logs. Do not retain
conversation text in issue trackers, operational notes or corrective tests.

Review new reports regularly, verify receipt-based deletion requests, and test
reporting failure/deletion/expiry using disposable records. Public support
needs a monitored mailbox and a maintained process; a working endpoint alone
does not satisfy AI-output prevention or Google Play approval.

## Release integration

The configured HTTPS routes are passed to Android through its existing four
publisher URL properties after live acceptance. Public distribution remains
subject to the [Android availability boundary](../ANDROID.md#availability).
Never copy this service's private state, secrets or deployment configuration
into the app. Keep policy copy, data-safety declarations, actual review and
retention operations aligned. Registration or app purchase does not grant
access to an operator's private Hub.

Android integration has separate private client and real-device reporting
acceptance.
Do not infer preview accuracy, stable client retries or usable receipt retention
from server-only tests. An operated deployment also needs confirmed hosting,
applicable processing/transfer arrangements, logs/backups/mail retention and a
monitored support path. Keep those private deployment facts in the operator's
current task record, not in this reusable service guide.
