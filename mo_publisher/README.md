# MO publisher service

This optional process owns the public home, support, privacy and deletion pages,
and the Android schema-1 AI-report receiver. It has no Hub, Agent, provider,
customer-account, purchase, or remote-control authority. Using public support
or submitting a report does not grant access to a private MO Hub. Customer
registration and purchases are not currently provided by this service.

## Public pages and availability

The home page introduces MO Agent and its Android companion. Support, privacy
and deletion pages currently cover Android. Pages use no scripts, cookies or
analytics. The published privacy page identifies the operating publisher and
describes hosting, backups and support retention for that deployment.

Get the Windows preview from the
[installation guide](../README.md#installation-availability). Android retains
its separate [Google Play availability](../ANDROID.md#availability). Remote Hub access uses
explicit device authorization. Full-profile replication between trusted
computers remains a separate private connection.

The footer links the current [PolyForm Shield 1.0.0 license](../LICENSE). It
does not grant additional rights or change those terms.

The Android report endpoint accepts native schema-1 reports. It has no public
report-listing, administration or deletion endpoint; deletion requests use the
published support route. Requests above 256 KiB are not accepted. Admission
limits can return HTTP 429; Android also limits local submissions to ten per
hour. Reports require no cookie, shared APK secret or Hub authorization token.

## Reports and retention

Receipts are issued only after a successful durable transaction. An identical
retry with the same report ID returns the same receipt; changed content using
that ID returns 409. Unknown fields, credentials, invalid hashes, invalid JSON,
duplicate fields, unconsented content and excessive text are rejected without
echoing input. Conversation inclusion is optional; explanation text can itself
contain personal data and is subject to the same retention.

All report fields and review outcomes have a 30-day retention period. This
requires the operated service's expiry and deletion processes; the actual
privacy page also explains backup and support retention. Deletion from the
report database does not establish deletion from infrastructure snapshots,
exports or email.

A receipt confirms durable report storage, not human review or a completed
safety fix. Keep it for support or a deletion request. Review outcomes are
`action_required`, `resolved`, `no_action`, or `insufficient_context`; a status
change alone does not establish that underlying product behavior was fixed.

## Android connection

Public installation and updates follow the
[Android availability boundary](../ANDROID.md#availability). A report receiver
being reachable does not by itself establish that an Android release is
available or that its reporting flow works end to end. Registration or app
purchase does not grant access to an operator's private Hub.
