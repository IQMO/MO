# MO Connected Tab

MO Connected Tab connects MO's canonical computer tools to the Chrome profile
you already use. Ask MO to work on a page: it discovers ordinary tabs, selects
the requested tab, and attaches automatically on its first observation. There
is no per-tab activation click and no second browser or profile.

## One-time setup

The extension is currently source-distributed, not published in the Chrome Web
Store. In `chrome://extensions`, enable **Developer mode**, choose **Load
unpacked**, and select this `clients/chrome` folder. **Copy extension folder**
in MO Desktop's **Settings → System → Connected Chrome** provides that path.
Reload the extension there after updating its source. Restart running MO
terminals after Python tool/bridge changes so both sides use the updated code.

MO browser tools automatically verify and repair the native-host registration
on first use. Settings reports **Bridge ready** and offers **Repair bridge**
for explicit recovery. The same lifecycle is available without Desktop:

```powershell
python -m core.browser_bridge install
python -m core.browser_bridge status
python -m core.browser_bridge uninstall
```

These commands manage MO's native host, not Chrome's extension list. They do
not overwrite another installation's registration. A future Web Store package
may replace unpacked installation; it is not required for automatic attachment.

## Use

1. Ask MO to work on an ordinary HTTP(S) page in the connected Chrome profile.
2. MO uses `computer_targets kind=browser` to discover the intended tab, then
   `computer_observe kind=browser target=<tab-ref>` to connect and inspect it.
   Discovery reads tab titles/URLs only; it does not attach to every tab.
3. MO retains that target across actions. Each action returns a fresh
   observation; an unchanged result needs no extra observation call.

Personal Outlook Mail chat actions reuse this transport through the `mail`
tool. MO opens the Outlook Mail URL through its existing desktop action when
no tab is present; the signed-in Chrome profile and extension must be available.
Keep only one Outlook Mail tab to avoid an ambiguous target. The adapter extracts
bounded inbox/message fields locally and sends them straight to the operator's
chat result; general browser snapshots are not used for private mail content.
`core/mail/README.md` owns its current actions, approval, and privacy limits.

When MO Shell has a window attached, MO may receive its title as an untrusted
availability cue; this is not page content and does not trigger automatic
inspection. If a task needs browser-page content, MO uses the exact-target
discovery and observation flow above. This does not grant access to all tabs;
stopped or protected tabs remain excluded.

Chrome's `debugger` permission supplies discovery and attachment; `activeTab`
and its click gesture are not used. Private/protected pages, non-page targets,
and tabs held by another debugger are excluded. A missing target is reported
as missing, never silently replaced with a different tab.

The toolbar action is optional: it can connect a tab directly or stop an
existing connection without closing the page. Chrome's own debugger stop also
ends access. An explicitly stopped tab stays excluded until re-enabled from
the toolbar or until Chrome clears the extension's session storage. No model
tool reverses that stop. Closing a tab removes its stop state.

| Badge | Meaning |
| --- | --- |
| none | Available for request-initiated connection. |
| `LIVE` | MO has an active debugger attachment to this exact tab; click to stop access. |
| `OFF` | Access to this tab was stopped; click to allow it again. |
| `NO` | The tab is unavailable or MO could not connect; hover for the reason. |

## Capabilities and ownership

- Bounded DOM/page text, visible-viewport screenshots, navigation, clicking,
  typing, page shortcuts, and waiting use the existing browser tools.
- Connected actions stay in the selected tab, including when it is in the
  background. You can use other tabs or applications: MO does not activate
  Chrome or take over the system keyboard, mouse, or clipboard.
- `computer_act kind=browser action=type ref=<element-ref> text=...` replaces
  the editable field/document through Chrome's page input, including rich
  editors with virtual textareas. Text is inserted in bulk, not typed character
  by character. `action=key keys=ctrl+enter` sends a page shortcut; an optional
  `ref` focuses that control inside the tab. Neither action switches to native
  input when the tab is unavailable.
- Arbitrary page evaluation retains the existing high-impact confirmation.
- Cookies, browser history, bookmarks, downloads, browser shutdown, private
  tabs, and arbitrary CDP commands are not exposed.
- Terminal and Desktop keep separate conversations, target refs, and task
  ownership. They reuse the same browser transport, not each other's sessions.
- Design may observe a requested tab but gains no actuation or acting lease.
- Actions release the acting lease after their result observation. Page text
  and pixels remain untrusted evidence; refs are checked against the observed
  control before input.

When native-window interaction satisfies the request, the existing native
computer tools remain available. They are not a substitute for an explicitly
requested Connected Tab workflow and do not provide browser DOM authority.
Desktop's point-and-explain workflow remains independent of Chrome discovery.

## Connection recovery and status

One native channel stays ready while Chrome runs, including with no attached
tab. Disconnects use one reconnect timer with backoff capped at one second,
preserving attachments. Recovery remains available while Chrome runs; it does
not stop after a fixed number of failures. Extension/browser restarts need no
per-tab approval restoration.

Protocol 1, authenticated local transport, bounded CDP parameters, request
deadlines, and cancellation remain unchanged. Expired queued requests do not
dispatch. Terminal cancellation stops the remaining commands; Chrome's stop
ends tab access and prevents subsequent input. Neither can undo an already
executed action, including a bulk insertion. A stopped tab is not a reason to
continue through native keyboard input.

`status` distinguishes installed/registered native files, a live native host
(`bridge_live`), and its extension handshake (`extension_connected`). A ready
empty catalog is different from a disconnected extension.

To remove MO Connected Tab, remove the extension in Chrome and uninstall the
native bridge through Settings or the CLI. The CLI removes only MO's owned
registration and generated launcher/manifest files.

## Source owners

- `service_worker.js`: tab discovery, request-initiated attachment, optional
  stop control, native messaging, and bounded CDP dispatch.
- `manifest.json` and `icons/`: stable extension identity and MO branding.
- `core/browser_bridge.py`: native lifecycle, authentication, transport, and
  acting lease; Settings is only its presentation adapter.
- `tools/browser.py`: target selection, page operations, and fresh evidence.

Chrome documents [debugger access](https://developer.chrome.com/docs/extensions/reference/api/debugger),
[unpacked installation](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked),
and [native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging).
