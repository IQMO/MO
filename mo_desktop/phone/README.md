# MO Phone

MO Phone is the compact native Desktop workspace for a selected Android device.
The launcher class in `window.py` uses `NativeAppWindow`'s existing process and
command pipe. `app.py` composes the installed MO Design WebView host and native
visual controller; `workspace.html`, `.css` and `.js` own its presentation.
There is no Tk fallback.

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
- Trackpad opens the existing Direct Android Activity and nonce-bound loopback
  tunnel through the selected ADB transport. Waiting for the phone and connected
  input are distinct states. Stop and close clean up the session's original
  transport, independently of the current selection. The Play client does not
  supply Trackpad.
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

**Add phone** and the paired/ADB count open **Phones & access**. Its setup check reuses
`core.state.everywhere_readiness` and the authenticated `/api/mo/device` owner.
A readable credential file is not reported as authenticated. A rejected host
credential says **Host needs pairing**, even when the serving Hub is reachable.
The drawer provides concrete steps for new Desktop setup, unreachable Hub
service/proxy, missing coordinator scopes, QR dependencies and separate host
pairing. **Refresh paired phones** refreshes those observations on request. USB/ADB
mirroring and Trackpad remain independent of Everywhere pairing.

The same drawer lists active Android registrations from the serving Hub's
existing registry through `/api/mo/devices/android`. It shows labels, opaque
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

**Create pairing QR** uses the existing coordinator endpoint and
`mo_everywhere.pairing_qr.create_android_pairing_image`, also used by Desktop's
pairing tool. No grant is created during startup, discovery or a setup check.
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

`PhoneBridge` owns one serialized worker. Device operations never run on the GUI
or pointer-input thread. Refreshes coalesce; snapshots perform no ADB/tool search.
The model, configuration, parent receipts and host/lifecycle callbacks are private
to the bridge; WebView's automatic API discovery cannot expose those internal
owners as a second device/action path.
The worker watches session liveness only while a mirror or Trackpad is active.
An idle cached host waits for work rather than continuously scanning devices.
Hub health, coordinator and inventory checks run once during initial discovery and then
only for explicit connection checks or QR requests on that same worker. A
cached window has no idle network polling or duplicate Hub registry.
Errors remain in the workspace; finite motion reflects connection and successful
capture/input changes and respects reduced motion.

Closing hides the same native host and stops Trackpad after any in-flight start
finishes. It preserves the separately running scrcpy mirror. Reopening and
theme changes retain the same controls and device selection. Desktop shutdown
or command-pipe loss rejects queued work, completes Trackpad cleanup and releases
the host and shared native effect layer. Minimizing retains active sessions.

Colors, typography and control geometry come from the resolved Desktop visual
state. `app_controls.css` supplies the shared native app buttons, switch and
glyph masks. Phone's purpose entrance uses the live main-cube snapshot and the
existing finite animation owner; source coordinates do not determine the final
window position.

Host start, ready and stop events use the existing backend resource monitor.
They describe process/resource lifecycle, not completion of a device operation.
No frame, gesture, nonce, device serial or credential is added to that telemetry.
Device failures are also exposed in the UI rather than inferred from lifecycle
events. Native UI checks and external device checks provide separate evidence.
