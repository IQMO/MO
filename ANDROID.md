# MO Everywhere for Android

MO Everywhere is the Google Play companion for an MO Agent running on your own
computer or server. It keeps the same agent, projects, tools, tasks, and private
state on that host; the phone does not run a second full MO runtime.

## Availability

Google Play is the only public installation and update channel. The current
published build is `0.1.62` (`65`) in closed Alpha testing for eligible accounts;
Production access is not active. A listing or opt-in link does not make an
account eligible for a closed test.

- [Closed-test opt-in](https://play.google.com/apps/testing/app.moagent.mobile)

The public repository contains no Android package downloads, Android client
source, signing material, or Android release automation. Availability outside
Google Play is never presented as Store availability.

## What the app provides

| Need | Capability | Requirement |
| --- | --- | --- |
| Continue work away from the desk | Hub chat, shared named conversations, tasks, schedules, and progress | Your reachable MO Hub and the grants you review during pairing |
| Check the same MO runtime | Dashboard and connected work status | A running Hub and its existing project/task owners |
| Reach a computer | Manual Desktop and terminal Live Control | A running host, Live Control enabled, and exact device scopes |
| Browse and move files | Files sources, previews, and verified transfers | Source access plus the relevant browse, manage, or transfer scope |
| Share one phone folder | Read-only selected-folder hosting for authorized Hub devices | Explicit Android folder choice, an unlocked phone, and reviewed host authority |
| Chat without a Hub | Direct chat through a configurable HTTPS OpenAI-compatible provider | Your provider endpoint, model, and API key; provider charges may apply |

The installed Store version, live connection, device scopes, host state, Android
permissions, and provider capabilities determine what is actually available.
Pairing alone never grants computer control, file access, or phone permissions.

## Requirements

- Android 8.0 or newer.
- An eligible Google Play account while the app remains in closed testing.
- For connected features, an MO installation with the Everywhere dependencies,
  a reachable HTTPS Hub, and the relevant host processes kept running.
- For phone-only chat, a supported provider account and the provider's own API
  access. This mode has no Hub tools or host project access.

Provider subscriptions, network hosting, and Google Play eligibility are not
included with MO.

## Install and pair

1. Install MO Everywhere from Google Play using an eligible account.
2. On the computer or server that will host your Hub, install
   `requirements-everywhere.txt` and complete `/everywhere setup`.
3. On that serving Hub, run `/everywhere pair android`.
4. In the Android app, scan the one-use QR or use its accessible manual fallback.
5. Review the HTTPS origin, device role, and exact requested scopes before
   confirming.
6. Enable optional Android permissions only for the feature that needs them.

The pairing QR is short-lived and contains no provider key. A Desktop that is
already an authorized Hub coordinator can display the same Hub-owned pairing
flow; it does not create another registry or a broader grant.

Phone-only chat is independent of pairing. Choose **Use a provider on this
phone**, then configure the provider endpoint, model, and key in the app. The Hub
credential and phone-provider credential are stored separately and are never
silently copied or mixed.

## Privacy and authority

- Hub provider keys and the full MO profile stay on the serving host.
- Android protects its Hub credential and optional phone-provider profile with
  Keystore-backed encrypted storage.
- Named-conversation sharing is explicit; pairing does not mirror every terminal
  transcript or the whole profile.
- Connected operations are authorized again by the Hub. Visible controls are not
  authority tokens.
- Settings and sensitive recovery require Android authentication. Control and
  chat surfaces protect sensitive captures where supported.
- Stopping resident mode, revoking the device on the Hub, unpairing, or removing
  an Android grant removes the corresponding access.
- Your selected AI provider still processes the content sent to it.

Never publish a pairing QR, device credential, provider key, private Hub URL,
personal conversation, or unsanitized diagnostic log in a GitHub issue.

## Limits

- The phone is a companion, not a hosted MO Agent or offline replacement for the
  computer/server runtime.
- Connected work needs the Hub and relevant host to remain reachable.
- Closed-test approval, Store publication, account eligibility, pairing, and
  device permission are separate states.
- The repository does not provide sideloadable APK or AAB files.
- Features absent from the current Store build are not public availability
  promises.

For Hub topology, TLS, pairing, scopes, and self-hosting, see
[MO Everywhere](mo_everywhere/README.md). For Desktop Live Control, see
[MO Desktop](mo_desktop/README.md).
