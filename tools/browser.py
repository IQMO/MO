"""Native browser control for MO computer-use (raw Chrome DevTools Protocol).

MO drives a real Chrome over CDP — no third-party automation framework. Chrome
is launched with a dedicated debug profile (isolated from the operator's normal
browser) and controlled via a websocket to the DevTools endpoint. Pages are
surfaced to the model as a compact, numbered element list (token-light, like an
accessibility snapshot) so the model clicks/types by stable refs (``e1``, ``e2``)
instead of pixels.

Tools: browser_open, browser_snapshot, browser_click, browser_type,
browser_eval, browser_close. All run through MO's normal tool dispatch + sandbox.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

DEBUG_PORT = 9222
DEBUG_HOST = "127.0.0.1"
_HTTPX: Any | None = None
_WS: Any | None = None
_WS_LOADED = False


def _httpx() -> Any:
    global _HTTPX
    if _HTTPX is None:
        import httpx as httpx_mod
        _HTTPX = httpx_mod
    return _HTTPX


def _websocket() -> Any | None:
    global _WS, _WS_LOADED
    if not _WS_LOADED:
        _WS_LOADED = True
        try:
            import websocket as websocket_mod  # websocket-client (sync)
            _WS = websocket_mod
        except Exception:  # pragma: no cover
            _WS = None
    return _WS

# JS that tags every interactive element with a stable ref and returns a compact
# list. Stored on window.__mo_refs so click/type can re-find an element by ref.
_SNAPSHOT_JS = r"""
(() => {
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], [onclick], [contenteditable=true]';
  const nodes = Array.from(document.querySelectorAll(sel));
  window.__mo_refs = {};
  const out = [];
  let i = 0;
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;            // skip hidden
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    i += 1;
    const ref = 'e' + i;
    window.__mo_refs[ref] = el;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || tag;
    let name = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                el.value || el.innerText || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ');
    if (name.length > 80) name = name.slice(0, 77) + '...';
    out.push({ref, role, tag, name, type: el.getAttribute('type') || ''});
    if (out.length >= 200) break;
  }
  return JSON.stringify({url: location.href, title: document.title, elements: out});
})()
"""


class BrowserManager:
    """Owns one debug Chrome process + a CDP websocket to the active page."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.ws: Any = None
        self._id = 0
        self._profile_dir: str | None = None

    # ── Chrome lifecycle ────────────────────────────────────────────────
    @staticmethod
    def _chrome_path() -> str | None:
        for p in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("google-chrome") or "",
            shutil.which("chromium") or "",
            shutil.which("chrome") or "",
        ):
            if p and os.path.exists(p):
                return p
        return None

    def _debug_alive(self) -> bool:
        try:
            _httpx().get(f"http://{DEBUG_HOST}:{DEBUG_PORT}/json/version", timeout=2.0)
            return True
        except Exception:
            return False

    def ensure_chrome(self) -> str | None:
        try:
            _httpx()
        except Exception as exc:
            return f"browser HTTP client not available: {exc}"
        if self._debug_alive():
            return None
        chrome = self._chrome_path()
        if not chrome:
            return "Chrome not found (install Google Chrome)."
        self._profile_dir = tempfile.mkdtemp(prefix="mo_chrome_")
        self.proc = subprocess.Popen(
            [chrome, f"--remote-debugging-port={DEBUG_PORT}",
             # Chrome 111+ rejects CDP websocket upgrades unless the connecting
             # origin is allow-listed. The debug endpoint is bound to localhost on
             # an isolated throwaway profile, so allowing the loopback origin is safe.
             f"--remote-allow-origins=http://{DEBUG_HOST}:{DEBUG_PORT}",
             f"--user-data-dir={self._profile_dir}", "--no-first-run",
             "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            if self._debug_alive():
                return None
            time.sleep(0.1)
        return "Chrome started but DevTools endpoint did not come up."

    # ── CDP plumbing ────────────────────────────────────────────────────
    def _active_page_ws_url(self) -> str | None:
        try:
            targets = _httpx().get(f"http://{DEBUG_HOST}:{DEBUG_PORT}/json", timeout=3.0).json()
        except Exception:
            return None
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        return pages[0]["webSocketDebuggerUrl"] if pages else None

    def _connect(self) -> str | None:
        ws_mod = _websocket()
        if ws_mod is None:
            return "websocket-client not available."
        url = self._active_page_ws_url()
        if not url:
            # open a fresh tab
            try:
                _httpx().put(f"http://{DEBUG_HOST}:{DEBUG_PORT}/json/new?about:blank", timeout=3.0)
            except Exception:
                pass
            url = self._active_page_ws_url()
        if not url:
            return "No DevTools page target available."
        try:
            self.ws = ws_mod.create_connection(url, timeout=20)
            return None
        except Exception as exc:
            return f"CDP connect failed: {exc}"

    def _cmd(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        if self.ws is None:
            err = self._connect()
            if err:
                raise RuntimeError(err)
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.1, deadline - time.time()))
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result") or {}
            # else: a CDP event — ignore and keep reading
        raise TimeoutError(f"CDP timeout waiting for {method}")

    def _eval(self, expression: str, timeout: float = 20.0) -> Any:
        res = self._cmd("Runtime.evaluate",
                        {"expression": expression, "returnByValue": True, "awaitPromise": True},
                        timeout=timeout)
        result = res.get("result") or {}
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description", "JS error"))
        return result.get("value")

    # ── high-level ops ──────────────────────────────────────────────────
    def open(self, url: str) -> str:
        err = self.ensure_chrome()
        if err:
            return f"Error: {err}"
        err = self._connect() if self.ws is None else None
        if err:
            return f"Error: {err}"
        if url and not url.startswith(("http://", "https://", "about:", "file:", "data:")):
            url = "https://" + url
        self._cmd("Page.enable")
        self._cmd("Page.navigate", {"url": url})
        time.sleep(1.2)  # let the page settle; snapshot re-reads live DOM anyway
        try:
            title = self._eval("document.title") or ""
            cur = self._eval("location.href") or url
        except Exception:
            title, cur = "", url
        return f"Opened {cur}\nTitle: {title}\nUse browser_snapshot to list interactive elements."

    def snapshot(self) -> str:
        try:
            raw = self._eval(_SNAPSHOT_JS)
        except Exception as exc:
            return f"Error: snapshot failed: {exc}"
        try:
            data = json.loads(raw)
        except Exception:
            return "Error: snapshot returned no data (no page open?)."
        lines = [f"{data.get('title','')} — {data.get('url','')}", "Interactive elements:"]
        for el in data.get("elements", []):
            extra = f" type={el['type']}" if el.get("type") else ""
            lines.append(f"  [{el['ref']}] {el['role']}{extra}: {el['name'] or '(no label)'}")
        if len(lines) == 2:
            lines.append("  (none found)")
        return "\n".join(lines)

    def click(self, ref: str) -> str:
        ref = str(ref or "").strip()
        try:
            ok = self._eval(
                f"(() => {{ const el = (window.__mo_refs||{{}})['{ref}']; "
                f"if(!el) return 'missing'; el.scrollIntoView({{block:'center'}}); el.click(); return 'ok'; }})()")
        except Exception as exc:
            return f"Error: click failed: {exc}"
        if ok == "missing":
            return f"Error: ref {ref!r} not found — run browser_snapshot first (refs reset on navigation)."
        time.sleep(0.6)
        return f"Clicked {ref}. Re-run browser_snapshot to see the resulting page."

    def type_text(self, ref: str, text: str, submit: bool = False) -> str:
        ref = str(ref or "").strip()
        js_text = json.dumps(str(text))
        try:
            ok = self._eval(
                f"(() => {{ const el=(window.__mo_refs||{{}})['{ref}']; if(!el) return 'missing'; "
                f"el.focus(); el.value={js_text}; "
                f"el.dispatchEvent(new Event('input',{{bubbles:true}})); "
                f"el.dispatchEvent(new Event('change',{{bubbles:true}})); return 'ok'; }})()")
        except Exception as exc:
            return f"Error: type failed: {exc}"
        if ok == "missing":
            return f"Error: ref {ref!r} not found — run browser_snapshot first."
        if submit:
            try:
                self._eval(f"(() => {{ const el=(window.__mo_refs||{{}})['{ref}']; "
                           f"if(el && el.form) el.form.submit(); }})()")
            except Exception:
                pass
        return f"Typed into {ref}." + (" Submitted form." if submit else "")

    def evaluate(self, expression: str) -> str:
        try:
            val = self._eval(str(expression))
        except Exception as exc:
            return f"Error: eval failed: {exc}"
        out = json.dumps(val) if not isinstance(val, str) else val
        return out[:4000] if out else "(no value)"

    def close(self) -> str:
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.ws = None
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None
        return "Browser closed."


_MANAGER: BrowserManager | None = None


def _manager() -> BrowserManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BrowserManager()
    return _MANAGER


def execute_browser_open(arguments: dict[str, Any]) -> str:
    url = str(arguments.get("url", "") or "").strip()
    if not url:
        return "Error: browser_open requires a 'url'."
    return _manager().open(url)


def execute_browser_snapshot(arguments: dict[str, Any]) -> str:
    return _manager().snapshot()


def execute_browser_click(arguments: dict[str, Any]) -> str:
    return _manager().click(str(arguments.get("ref", "")))


def execute_browser_type(arguments: dict[str, Any]) -> str:
    return _manager().type_text(
        str(arguments.get("ref", "")),
        str(arguments.get("text", "")),
        submit=bool(arguments.get("submit", False)),
    )


def execute_browser_eval(arguments: dict[str, Any]) -> str:
    expr = str(arguments.get("expression", "") or "").strip()
    if not expr:
        return "Error: browser_eval requires 'expression'."
    return _manager().evaluate(expr)


def execute_browser_close(arguments: dict[str, Any]) -> str:
    return _manager().close()
