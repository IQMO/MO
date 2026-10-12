# MO Phone

MO Phone is the compact native Desktop workspace for a selected Android device.
It follows the current Desktop appearance and keeps the selected phone's
controls together in one workspace.

## Controls and evidence

- Select and Refresh use current discovered device rows. USB, Wi-Fi,
  authorization, offline, loading and missing-tool states are distinct.
  Several phones may be connected; the selector addresses one exact ADB
  connection. USB and Wi-Fi rows can represent the same physical phone.
- Mirror and Full screen call the existing scrcpy owner. An active mirror stays
  stoppable when selection changes or the selected device becomes unavailable.
  Audio uses AAC so phones without an Opus encoder can still mirror with sound.
  Mirror and Trackpad each have one active session; their cards name the
  original phone even after the selection changes.
- Trackpad requires a compatible Android client and connects through the
  selected ADB transport. See the [Android guide](../../ANDROID.md) for current
  public availability; a Desktop control alone does not establish client support. Waiting for the phone and connected input are distinct states. Stop
  and close clean up the session's original transport, independently of the
  current selection.
- Keep a frame uses the existing attachment writer and provenance index. Its
  count describes frames kept in this workspace session, not the whole catalog.
- Files opens the Desktop's existing MO Files instance. Success requires the
  parent callback's acknowledgement. Phone contains no additional file browser.
- Wi-Fi runs the existing private-network pairing/recovery owner. The interface
  selects the verified wireless endpoint before reporting that the cable can
  come out. An explicit selection cannot pair another USB phone or reconnect
  an unrelated saved endpoint. It never automatically opts an unrelated USB
  phone into debugging. The existing private Wi-Fi record retains an address
  and one-way USB identity binding for each deliberately paired phone. Updating
  one phone preserves the others. A bound endpoint must return the same phone
  identity before reconnect or recovery reports success; a ready IP alone is
  insufficient. Existing unbound single-address records remain readable.
- Connection and diagnostics report the selected device, latest discovery and
  actual tool availability. Open diagnostics update as discovery finishes.
  Hub health, the authenticated Desktop coordinator and the Desktop Live Control
  host remain independent. Access explains
  Android's existing consent owners and says **Check on phone**; an ADB/Hub link
  cannot establish Accessibility, storage, Live Control or Shizuku grants.

## Setup and QR pairing

Android pairing is optional. Install the paid Android app through its available
Google Play channel, then use **MO Phone → Add phone** when you want to connect
it. Purchase eligibility is handled by Google Play; MO does not ask you to prove
a purchase or grant Hub access from payment. See the [Android guide](../../ANDROID.md)
for current availability and the separate Hub requirements.

**Add phone** and the paired/ADB count open **Phones & access**. Its setup check
verifies the Desktop host's connection and pairing with the Hub.
A readable credential file is not reported as authenticated. A rejected host
credential says **Host needs pairing**, even when the serving Hub is reachable.
The drawer provides concrete steps for new Desktop setup, unreachable Hub
service/proxy, missing coordinator scopes, QR dependencies and separate host
pairing. **Refresh paired phones** refreshes those observations on request. USB/ADB
mirroring and Trackpad remain independent of Everywhere pairing.

The same drawer lists active Android registrations from the serving Hub. It shows labels, opaque
identity suffixes, last-seen timestamps and companion/phone-host grants.
Unavailable inventory is distinct from a verified empty list. Older Hub
versions can still verify the coordinator and create a QR when their inventory
route is unavailable. No local phone registry or idle inventory poller exists.
Counts describe Hub registrations, not unique hardware or live phone hosts;
revoked and unknown-kind registrations are excluded. Re-pairing may create a
new registration for the same hardware rather than revoke its older identity.

To add another local phone, connect and authorize it by USB, Refresh, then
select its ADB connection. To add another Everywhere phone, create a fresh QR
for that phone. Each receives independent credentials; existing phones remain
paired. Refresh paired phones after accepting the QR to observe registration.

**Create pairing QR** requests a grant from the authenticated Hub coordinator. No grant is created during startup, discovery or a setup check.
The authenticated coordinator needs `notify` with exact `continuity_read` and
`continuity_sync` scopes. The ordinary QR is a five-minute, one-use companion
grant; it does not silently add `remote_host`. Open the Android app's pairing
scanner to review and accept it. Existing phones can use their pairing screen
again. Separate phone-host control and Android permissions retain their current
explicit grant and consent flows. MO's existing `everywhere_readiness` tool
with `view=phones` reads the same inventory and exact grants. Semantic phone
tools retain authenticated origin-phone routing: selecting an ADB connection
in Desktop does not redirect an MO turn to that phone. Paired credentials,
phone-host grants, a connected host and Android permissions are distinct facts.

QR pixels are sent only to this local renderer, excluded from snapshots,
command/status pipes, attachments, provider context and lifecycle telemetry.
Replacing, dismissing, hiding or closing clears the displayed image and its
private PNG; canonical expiry cleanup remains in the shared QR owner. The UI
also clears expired pixels. Dismissing the image does not revoke an issued grant;
its one-use/expiry authority remains on the Hub. There is no pairing-completion
poller, so displaying a QR never claims that the phone has paired successfully.

## Work and lifecycle

Device actions are serialized and repeated refresh requests are combined.
MO checks session liveness only while a mirror or Trackpad is active.
An idle open or minimized host waits for work rather than continuously scanning devices.
Hub health, coordinator and inventory checks run once during initial discovery and then
only for explicit connection checks or QR requests. A
retained window has no idle network polling or duplicate Hub registry.
Errors remain in the workspace; finite motion reflects connection and successful
capture/input changes and respects reduced motion.

Closing stops Trackpad after any in-flight start finishes, then exits the native
host when no mirror is running, releasing its WebView processes. A separately
running scrcpy mirror keeps its existing hidden owner until it ends; reopening
cancels that pending exit and retains its controls and device selection. After
an idle close, reopening creates a fresh workspace and discovers devices again.
Theme changes retain a running workspace. Desktop shutdown
or command-pipe loss rejects queued work, completes Trackpad cleanup and releases
the host and shared native effect layer. Minimizing retains active sessions.

Colors, typography and controls follow Desktop Settings. The window entrance
starts from the main cubes and respects reduced motion; its final position
does not depend on the source animation coordinates.

Host start, ready and stop events use the existing backend resource monitor.
They describe process/resource lifecycle, not completion of a device operation.
No frame, gesture, nonce, device serial or credential is added to that telemetry.
Device failures are also exposed in the UI rather than inferred from lifecycle
events. An open workspace alone does not prove that a device operation succeeded.
