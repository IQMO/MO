# MO SystemCare

SystemCare is a compact maintenance workspace over one
`core.systemcare.service.SystemCareService`. Desktop, Dashboard and deferred Agent tools
share its observations, calibration, exact plans, recovery originals and receipts.
Files and Phone remain separate apps.

## Native workspace

The window uses the installed MO Design WebView renderer, current Desktop skin,
canonical glyphs and the shared four-cube SystemCare entrance. Files, Phone and
SystemCare reuse `mo_desktop/app_window.py` for the process, theme and launch-source
handshake; their feature owners remain separate. Phone and SystemCare use
`mo_desktop/app_controls.css` for common buttons, switches and glyph masks;
their colors, fonts and geometry come from the same Desktop visual state.

Opening displays saved results immediately and refreshes lightweight machine
resources. Deep scans and native checks start on request. Overview, Clean,
Manage, Repair, Registry and History expose scoped results and exact-plan review.
Overview also projects the canonical Game Session journal as one compact card:
session/recovery state, elapsed time, start/current CPU-memory readings, applied
items and the verified restoration result. The card reuses the existing visible
15-second resource refresh; it creates no long-running sampler or telemetry loop.
Settings include notifications, scope, retention, global Game Mode and optional
scheduling. Closing an idle window exits its native host. Closing during work
asks whether to stop at the next safe boundary or finish in the background.
Background work remains reachable through MO's existing tray, retains its
journal, reports completion according to the notification preference, then exits.
Reopening before completion cancels the pending exit. Native close and the
workspace close control use the same choice; Desktop shutdown or control-pipe
loss cancels owned work and waits for its safe boundary before disposal.
Completion notices use the existing tray notification owner and reopen saved
results when clicked. The notification switch controls all SystemCare notices,
including scheduled care. The default scope reports background results; visible
section inspections update their saved results without completion popups.
Settings can include errors while visible, or explicitly enable all result notices.
Partial, unavailable and failed checks retain their limits; lightweight resource
refreshes do not generate completion notifications.
Settings group related controls in a scrollable drawer with reachable Save and
Recalibrate actions. Native checkboxes retain keyboard behavior and switch
semantics. Typography comes from the existing Desktop theme roles, converted
from points to CSS pixels with a 10% workspace scale; relative text sizes follow
the body role and live theme updates. There is no separate SystemCare font
preference or palette.
Native hide and minimize pause the page's existing polling and resource-refresh
timers; reopen resumes them. Browser visibility alone does not represent a
hidden native window. The shared native visual controller also hides the passive
shadow on native visibility changes and refreshes it when reopening; queued
refreshes use the effect owner's HWND visibility check. Loss of Desktop's control
pipe follows the same safe shutdown as an explicit request, so it does not leave
an orphan host.

Scan follows the selected section. When a server or project is required,
choosing the target continues that requested inspection. Changing a target on
its own shows saved evidence. Section navigation resets the content scroll;
refreshing the current section retains it. The Scan control shows Cancel during
work while retaining its icon.
The persistent scan panel shows the current check, recorded/queued counts,
grouped coverage, timestamps and links to individual results. Completed,
limited, unavailable, failed, stopped and unrun checks remain distinct;
"checked" describes an inspection, not a repair or a machine-health verdict.
Saved checks survive navigation and reopening through the existing scan history
and section observations. Older evidence uses the observation owner's existing
five-minute freshness window. Section tabs also show inspection status. Repair details reuse the same result
renderer; failed and unavailable evidence never appears as an empty successful
inspection. Stale or unavailable results retain details without offering changes.

**Scan all** explicitly runs the enabled machine scan and existing inspection
sections under one operation lock. Its Advanced checkbox selects deeper checks;
Scope lists the actual catalog selected by current categories and Advanced
settings. Safe excludes the deeper update, registry and system-file checks.
Advanced batch checks report missing administrator access without starting a
permission prompt. Selected native checks retain their existing permission flow.
Root/volume-specific storage and file-system checks still require their targets.
Scanning applies no cleanup or repair. The Agent's existing `systemcare_scan`
tool exposes the same optional `all_checks` argument.

Each progress event retains the bounded per-check ledger, so fast completed
steps are not lost between UI polls. Results use the same ledger in persisted
scan records, and per-check timing/outcome rows use the existing SystemCare
audit monitor. The panel reuses active-operation polling and native visibility
pause/resume; it adds no background scan, model call or refresh timer.
Non-machine Overview displays its saved health inspection, including incomplete,
unavailable and stale evidence; a completed request does not imply full health.

| Context | Current inspection boundary |
| --- | --- |
| This machine | Windows resources, storage, startup registrations/tasks, services, apps, WinGet updates, drivers/packages, Windows-offered updates, registry references and fixed native checks |
| MO runtime | Existing offline doctor, retention/index diagnostics and trace declarations; loaded-process and task evidence remain separate |
| MO servers | Selected configured SSH alias: host resources, matching loaded and installed system/user MO systemd units, startup enablement and dependency properties, journal evidence and retention readiness; custom deployments are not inferred |
| Projects | Existing profile project declarations or an explicit selected root: folder/graph health, separate manifest evidence, generated-state inventory and bounded storage discovery; installed dependencies and build/test health are not inferred |

Ask MO in section results, individual item reviews, exact item evidence, scan
findings and plan review returns to the existing MO conversation and canonical
tools. Missing, unavailable and older inspection evidence can be discussed without
granting action permission. Item requests retain the observation revision; changed
items and changed plan digests require a new review. Service analysis uses this
same handoff and asks for dependency/usage evidence rather than name-based safety.
Recommendations prepare a reviewable plan; maintenance waits for approval of that
exact plan. SystemCare adds no second agent, provider connection, project catalog,
index or server mutation owner.

Projects and MO servers show the latest saved inspections for their selected
target in History. Machine receipts and restore controls remain in This machine.
Project cache/storage discovery has file, directory, result and time bounds;
unreadable entries and incomplete discovery stay explicit. Linked roots and the
opaque personal home are excluded. A generated directory name never authorizes
deletion, and scans execute no project code or package manager.

Machine Startup groups recorded Run registrations, startup-trigger tasks and
startup-folder entries while preserving exact item selection. Remote MO startup
belongs to Service & startup under the selected host, with system/user scope.
Installed-app and startup-shortcut rows load their available Windows icons through
Desktop's shared shell icon owner as rows become visible. Resource indices are
preserved, native handles are released, and canonical glyphs remain when a local
icon is unavailable. The opaque personal home is excluded from icon reads;
no background icon scan or application launch is required.

## Machine actions and recovery

Safe and Advanced scans remain read-only. Selected actions are separate:

- Generated-file cleanup rechecks exact root, age, metadata and fingerprint.
  Closed-browser cache cleanup preserves credentials, cookies, history and
  sessions. Active app owners and reparse ancestors block cleanup.
- Browser care separately reviews local history and download-history records in
  closed supported Chrome, Edge and Brave profiles. A bounded SQLite snapshot,
  exclusive transaction and post-check protect the selected category; reopening,
  pending journals, changed state or unknown schemas refuse the change.
  Passwords, bookmarks, cookies, site/session data and downloaded files stay
  protected. Firefox Places and cloud/sync copies remain with their native
  browser owners; this is not a claim of secure erasure or synchronized removal.
- Windows Recycle Bin inspection uses the current account's Shell namespace,
  independently of MO Files' private trash. Each selected Shell identity and
  bounded non-redirected metadata snapshot is revalidated before permanent removal
  via [IFileOperation](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-deleteitem).
  Discovery has a 10-second budget plus the current bounded item read.
  There is no whole-bin emptying. Unreviewable/redirected contents are excluded.
- Service state/startup type, startup Run values and tasks use captured originals.
  Bounded Startup-folder shortcuts use Windows' actual folder locations and
  retain their contents and timestamps before disabling. Restoration refuses
  redirected roots or later files; file security metadata is not an exact undo.
  Registration presence does not prove Task Manager approval or measured impact.
  Startup inspection separately reads at most 40 available Windows boot and
  application-degradation events. Historical durations and event identity remain
  distinct from a predicted impact rating for each registration; missing or
  inaccessible events are explicit.
  Critical services and running dependents block disruptive changes. Requested
  MO analysis reuses the Gateway and requires current dependency/use evidence.
- Registry inspection covers bounded declared families. Only eligible exact
  missing-target value references can be removed; uncertain registrations remain
  inspectable. Shared-library reference counts and merged class links are
  checked separately. Coverage shows keys/references read, access exclusions,
  depth/count limits and the traversal budget. Key trees are never deleted.
  Typed originals precede mutation.
  Eligible values include literal files, unambiguous quoted executable targets,
  Windows font files and exact missing merged class links. Both class views are
  checked; inaccessible references remain protected. Application-command
  traversal includes the declared command depth. Installers, services and
  uncertain ownership keep their separate owners.
- PATH repair removes only the selected literal duplicate in the exact User or
  Machine value, preserving its type and all other entries. The complete original
  is retained for restoration and later external changes block undo. Windows is
  notified for future processes; already running processes keep their environment.
- Game Session is the UI projection of Global Game Mode's canonical recovery
  journal. Starting it captures Game Bar and power originals plus one calibrated
  CPU-memory baseline, optionally selects an existing power scheme and pauses MO
  scheduled work. Current readings reuse the already-visible workspace refresh;
  stopping records one final sample and restores the captured originals.
- The Care cube exposes a small SystemCare hover shortcut that opens the same
  non-actuating Game Session plan review. While a session is active, the launcher
  folds to one dimmed top-right cube; clicking it opens a compact status/actions
  panel and **Stop & restore** returns through the canonical plan confirmation.
- Game Session has no per-game profile, FPS/frametime collector, app/window
  closer or Windows-service stopper. CPU-memory readings are observations, not
  proof that the session improved performance.
- Desktop refresh, SFC/DISM repair, component cleanup, media-aware volume
  optimization and DNS-cache reset use fixed native owners. Output is retained;
  completion alone does not prove health or improved performance.
  Volume plans compare the exact fixed-volume identity; changing free space is
  a measurement rather than a mutation identity. Drive-usage results offer a
  selected-volume, read-only CHKDSK through the existing permission worker.
  Its output and incomplete results are retained; it schedules no repair or
  restart. Active-volume writes can affect its findings. See
  [Microsoft's CHKDSK contract](https://learn.microsoft.com/windows-server/administration/windows-commands/chkdsk).
  Network inspection records adapter addresses, DNS and default gateways;
  configuration evidence does not establish external connectivity.
- Registered uninstallers, current-account Windows package removal, exact WinGet updates and unused third-party driver
  package removal require current selection. Protected/in-use packages are
  excluded; force removal and automatic leftover deletion are absent.
  App leftovers are separately reviewed from retained, verified SystemCare
  uninstall receipts and their captured InstallLocation. Current registrations
  and running processes are checked again. Only selected bounded binaries and
  generated files can be removed; app configuration, saves, databases, Windows,
  user data and the active MO checkout/runtime remain protected. No guessed
  app-data path or recursive folder removal is used.
  Framework, resource, system-signed and nonremovable Windows packages stay
  inspect-only. Package removal requires explicit acknowledgment of app/data
  removal; it never removes provisioning or another account's registration.
- Windows Update Agent supplies software/driver updates under managed source
  policy. Installation targets one exact identity/revision with already accepted
  licensing and noninteractive ownership. Restart requirements are recorded;
  SystemCare does not restart the machine.

Apply requires an immutable unexpired catalog plan/digest, current calibration
and item revalidation. Permanent actions require non-undo acknowledgment.
Desktop requests Windows permission for a selected protected operation through
a one-shot worker using the same service. Agent tools retain normal confirmation
gates and require an elevated process when applicable.

Receipts retain applied/skipped/failed steps, real before/after state and output.
Partial failures retain originals and uncertain post-state without verified
success. Restoration rejects later external changes. Unrestored reversible
originals protect their plans and receipts from pruning. Cancellation takes
effect at the next safe boundary; a native command may finish first.

## Private state and automation

`memory/systemcare.sqlite`, bounded `logs/systemcare.jsonl` and cross-process
`run/systemcare/` state live under the active profile or `MO_STATE_HOME`.
Preferences use `mo_desktop.systemcare`. Public projections omit private
paths/originals. Opaque `personal/` and curated learning remain outside this owner.
The native host and one-shot elevated worker activate MO's existing backend
monitor. The existing audit rows feed its registered `systemcare_operation`
event. Operation timing uses canonical runtime phases, with operation identity,
context and section also available on failures. Native command phases record
the executable basename, duration, exit code, timeout and truncation state;
arguments, command output and private target paths stay out of these events.
Native readiness/shutdown take two resource snapshots through the existing
component resource owner. Game Session records one start and one final resource
sample; its current values reuse the native workspace's existing visible refresh.
Operation logs include host process CPU time; this includes other threads and
excludes child-process CPU. There is no telemetry poller, resource cap or
process-termination optimization.
Each selected apply step adds one bounded audit summary with timing, result
counts and error class; native commands inherit its step/action identity.
This includes file-cleanup steps without logging every candidate or file path.
Returned/raised boundaries are distinct from verified action outcomes.
Native monitor files use the same profile monitor directory, or the inherited
trace directory when the app belongs to that traced process tree. A Shell trace
does not automatically cover an independently running Desktop app.

Optional Safe scans reuse MO's scheduler. Current preferences, idle, power,
Game Mode and active-operation checks gate each run. Scheduled cleanup needs
explicit authorization and is limited to fixed old-temp/thumbnail/DirectX caches.
Unchanged settings preserve the existing schedule and next-run time.
Restored and irreversible originals follow configured retention after their
receipts expire; unrestored reversible originals remain protected.

## Coverage and ownership

Results retain their scope rather than inventing an overall health score.
MO index inspection uses the existing structural graph and episodic-memory
diagnostics. Readable counts retain stale and fallback-search states; the full
report remains available as evidence. Retention inspection uses existing file
health owners. Trace declarations show source errors and unregistered event types
separately from loaded-process evidence. Bounded Git metadata and source listing
share file-backed output capture with SystemCare's native read commands. Helpers
receive no input from the GUI command channel, and completion does not wait for
inherited output-pipe writers after the helper exits.
Registry/file discovery is bounded. Recorded boot durations, app-leftover
review, selected PATH repair and DNS reset are distinct from automatic
optimization. Arbitrary deployment maintenance is not authorized by inventory
alone. Unavailable owners are explicit. Superiority over another maintenance app is not established.

`core/systemcare/` owns policy, native inspection/mutation, state and verification.
`mo_desktop/systemcare/` owns presentation and one background worker. Read both
maintenance contracts before extending these boundaries.
