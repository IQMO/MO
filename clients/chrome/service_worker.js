const HOST_NAME = "com.mo_agent.connected_tab";
const PROTOCOL = 1;
const ALLOWED_METHODS = new Set([
  "Runtime.evaluate",
  "Page.navigate",
  "Page.captureScreenshot",
  "Input.insertText",
  "Input.dispatchKeyEvent",
]);

let nativePort = null;
let nativeReconnectTimer = null;
let nativeReconnectAttempt = 0;
const NATIVE_RECONNECT_MAX_DELAY_MS = 1000;
// One promise per tab owns both an in-flight attach and the live attachment.
const attachments = new Map();
const stoppedTabs = new Set();
const ready = chrome.storage.session.get("stoppedTabIds").then((stored) => {
  for (const id of stored.stoppedTabIds || []) stoppedTabs.add(id);
});

const tabRef = (tabId) => `tab-${tabId}`;

async function persistStoppedTabs() {
  await chrome.storage.session.set({ stoppedTabIds: Array.from(stoppedTabs) });
}

function post(message) {
  if (!nativePort) return;
  try {
    nativePort.postMessage({ protocol: PROTOCOL, ...message });
  } catch (error) {
    nativePort = null;
    scheduleNativeReconnect();
  }
}

async function tabCatalog() {
  await ready;
  const [targets, tabs] = await Promise.all([chrome.debugger.getTargets(), chrome.tabs.query({})]);
  const ordinaryTabs = new Set(tabs.filter((tab) => !tab.incognito).map((tab) => tab.id));
  return targets.filter((target) =>
    target.type === "page" && ordinaryTabs.has(target.tabId) &&
    /^https?:\/\//i.test(String(target.url || "")) && !stoppedTabs.has(target.tabId) &&
    (!target.attached || attachments.has(target.tabId))
  ).map((target) => ({
      ref: tabRef(target.tabId),
      title: String(target.title || "").slice(0, 200),
      url: String(target.url || "").slice(0, 500),
  })).sort((a, b) => a.ref.localeCompare(b.ref));
}

async function attachTab(tabId) {
  await ready;
  if (stoppedTabs.has(tabId)) throw new Error("MO access to this tab was stopped in Chrome");
  if (!attachments.has(tabId)) {
    const pending = (async () => {
      if (!(await tabCatalog()).some((target) => target.ref === tabRef(tabId))) {
        throw new Error("requested Chrome tab is unavailable, protected, or held by another debugger");
      }
      await chrome.debugger.attach({ tabId }, "1.3");
      await setBadge(tabId, "live");
    })();
    attachments.set(tabId, pending);
    pending.catch(() => attachments.delete(tabId));
  }
  await attachments.get(tabId);
}

async function announce(event = "catalog", target = "") {
  post({ type: "event", event, target, targets: await tabCatalog() });
}

function scheduleNativeReconnect() {
  if (nativeReconnectTimer !== null) return;
  const delay = Math.min(
    NATIVE_RECONNECT_MAX_DELAY_MS,
    100 * (2 ** Math.min(nativeReconnectAttempt, 4))
  );
  nativeReconnectAttempt += 1;
  nativeReconnectTimer = setTimeout(() => {
    nativeReconnectTimer = null;
    void ensureNativePort().catch(scheduleNativeReconnect);
  }, delay);
}

async function ensureNativePort() {
  if (nativePort) return nativePort;
  const port = chrome.runtime.connectNative(HOST_NAME);
  nativePort = port;
  port.onMessage.addListener((message) => {
    nativeReconnectAttempt = 0;
    void handleNativeRequest(message);
  });
  port.onDisconnect.addListener(() => {
    // Read lastError so Chrome does not report an unhandled native disconnect.
    void chrome.runtime.lastError;
    const owned = nativePort === port;
    if (owned) nativePort = null;
    if (owned) scheduleNativeReconnect();
  });
  post({ type: "hello", targets: await tabCatalog() });
  return port;
}

async function setBadge(tabId, state, detail = "") {
  const live = state === "live";
  const stopped = state === "stopped";
  const color = live ? "#146C43" : stopped ? "#5f6368" : "#b3261e";
  const text = live ? "LIVE" : stopped ? "OFF" : "NO";
  const title = live
    ? "MO Connected is attached to this tab; click to stop access"
    : stopped
      ? "MO access is stopped on this tab; click to reconnect"
      : String(detail || "MO could not connect this tab");
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setBadgeText({ tabId, text });
    await chrome.action.setTitle({ tabId, title });
  } catch (_) {
    // The tab may already be gone.
  }
}

async function handleNativeRequest(message) {
  if (!message || message.type !== "request" || !message.id) return;
  const reply = (ok, value) => post({
    type: "response",
    id: String(message.id),
    ok,
    ...(ok ? { result: value } : { error: String(value?.message || value || "request failed") }),
  });
  try {
    // Older hosts omit the deadline; newer hosts bound queued requests.
    if (message.expires_at !== undefined &&
        (!Number.isFinite(message.expires_at) || Date.now() >= message.expires_at)) {
      throw new Error("Connected Tab request expired before dispatch.");
    }
    if (message.operation === "catalog") {
      reply(true, await tabCatalog());
      return;
    }
    if (message.operation !== "cdp") throw new Error("unsupported bridge operation");
    const ref = String(message.target || "");
    const method = String(message.method || "");
    if (!ALLOWED_METHODS.has(method)) throw new Error("CDP method is not allowed");
    if (!/^tab-\d+$/.test(ref)) throw new Error("an exact Chrome tab ref is required");
    const tabId = Number(ref.slice(4));
    const params = message.params && typeof message.params === "object" ? message.params : {};
    const keys = Object.keys(params).sort();
    if (method === "Runtime.evaluate") {
      if (!params.expression || String(params.expression).length > 200000 ||
          JSON.stringify(keys) !== JSON.stringify(["awaitPromise", "expression", "returnByValue"]) ||
          params.awaitPromise !== true || params.returnByValue !== true) {
        throw new Error("Runtime.evaluate parameters exceed the connected-tab boundary");
      }
    } else if (method === "Page.navigate") {
      const url = String(params.url || "");
      if (keys.length !== 1 || keys[0] !== "url" || url.length > 4096 || !/^https?:\/\//i.test(url)) {
        throw new Error("Page.navigate accepts one bounded http:// or https:// URL");
      }
    } else if (method === "Input.insertText") {
      if (keys.length !== 1 || keys[0] !== "text" || typeof params.text !== "string" || params.text.length > 200000) {
        throw new Error("Input.insertText accepts one bounded text string");
      }
    } else if (method === "Input.dispatchKeyEvent") {
      if (JSON.stringify(keys) !== JSON.stringify(["code", "key", "modifiers", "text", "type", "windowsVirtualKeyCode"]) ||
          !["keyDown", "keyUp"].includes(params.type) ||
          ["key", "code"].some(key => typeof params[key] !== "string" || !params[key].length || params[key].length > 64) ||
          !Number.isInteger(params.modifiers) || params.modifiers < 0 || params.modifiers > 15 ||
          !Number.isInteger(params.windowsVirtualKeyCode) || params.windowsVirtualKeyCode < 0 || params.windowsVirtualKeyCode > 255 ||
          typeof params.text !== "string" || params.text.length > 1) {
        throw new Error("Input.dispatchKeyEvent accepts one bounded page key event");
      }
    } else if (JSON.stringify(keys) !== JSON.stringify(["captureBeyondViewport", "format", "fromSurface"]) ||
               params.format !== "png" || params.fromSurface !== true || params.captureBeyondViewport !== false) {
      throw new Error("Page.captureScreenshot is restricted to the visible PNG viewport");
    }
    await attachTab(tabId);
    if (stoppedTabs.has(tabId) || (message.expires_at !== undefined && Date.now() >= message.expires_at)) {
      throw new Error("Connected Tab request stopped or expired before dispatch.");
    }
    const result = await chrome.debugger.sendCommand({ tabId }, method, params);
    reply(true, result || {});
  } catch (error) {
    reply(false, error);
  }
}

chrome.action.onClicked.addListener((tab) => void (async () => {
  if (!Number.isInteger(tab.id)) return;
  await ready;
  try {
    if (attachments.has(tab.id)) {
      stoppedTabs.add(tab.id);
      await persistStoppedTabs();
      await attachments.get(tab.id);
      attachments.delete(tab.id);
      await chrome.debugger.detach({ tabId: tab.id });
      await setBadge(tab.id, "stopped");
      await announce("detached", tabRef(tab.id));
    } else {
      stoppedTabs.delete(tab.id);
      await persistStoppedTabs();
      await attachTab(tab.id);
      await announce();
    }
  } catch (error) {
    const detail = String(error?.message || error || "").trim().slice(0, 160);
    await setBadge(tab.id, "unavailable", `MO could not connect this tab: ${detail}`);
  }
})());

chrome.debugger.onDetach.addListener((source, reason) => void (async () => {
  if (!Number.isInteger(source.tabId)) return;
  const ref = tabRef(source.tabId);
  if (!attachments.delete(source.tabId)) return;
  if (reason === "canceled_by_user") {
    stoppedTabs.add(source.tabId);
    await persistStoppedTabs();
  }
  await setBadge(
    source.tabId,
    stoppedTabs.has(source.tabId) ? "stopped" : "unavailable",
    reason === "replaced_with_devtools"
      ? "Close page DevTools before MO can reconnect"
      : "MO access ended; click to allow this tab again"
  );
  await ensureNativePort();
  await announce("detached", ref);
})());

chrome.tabs.onRemoved.addListener((tabId) => void (async () => {
  await ready;
  attachments.delete(tabId);
  if (stoppedTabs.delete(tabId)) await persistStoppedTabs();
  await announce("detached", tabRef(tabId));
})());

chrome.tabs.onUpdated.addListener((tabId) => void (async () => {
  if (attachments.has(tabId)) await announce();
})());

// Native messaging keeps this worker alive. No approval restoration, startup
// reset, or Page.enable round-trip is needed to observe the requested tab.
chrome.runtime.onStartup.addListener(() => {
  void ensureNativePort().catch(scheduleNativeReconnect);
});
void ensureNativePort().catch(scheduleNativeReconnect);
