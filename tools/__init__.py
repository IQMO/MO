"""MO Agent — tool implementations and provider-facing definitions.

Code-search and caller/callee tools expose MO's graph as first-class tools
instead of shell one-liners. The agent keeps the full catalog locally and applies
the configured full-static, deferred, or capability-routed provider view;
tool_search activates deferred schemas. Sandbox gates still enforce every
dispatch via core.tooling.sandbox.guard_tool_call(). Stateful set_plan and
complete_task calls are implemented only by AgentTaskBoard against the live
board, so they are intentionally absent from the stateless TOOL_EXECUTORS map.
Agent-runtime-only map_project and role_work have fail-closed executor sentinels;
their real implementations still run through the owning Agent dispatch paths.
"""

import os
import sys
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.tooling.sandbox import (
    redact_sensitive_text,
    secret_read_path_kind as secret_read_path_kind,
)
from core.utils.text_utils import cap_text_evidence


from .desktop import (
    execute_desktop_sync,
    execute_point_on_screen,
)
from core.tooling.untrusted import fence_untrusted_web_content


def execute_computer_targets(arguments: dict[str, Any]) -> str:
    from .computer import execute_computer_targets as _execute
    return _execute(arguments)


def execute_media(arguments: dict[str, Any]) -> str:
    from .media import execute_media as _execute
    return _execute(arguments)


def execute_computer_observe(arguments: dict[str, Any]) -> str:
    from .computer import execute_computer_observe as _execute
    return _execute(arguments)


def execute_computer_act(arguments: dict[str, Any]) -> str:
    from .computer import execute_computer_act as _execute
    return _execute(arguments)


def execute_phone_context(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_context as _execute
    return _execute(arguments)


def execute_phone_click(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_click as _execute
    return _execute(arguments)


def execute_phone_set_text(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_set_text as _execute
    return _execute(arguments)


def execute_phone_scroll(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_scroll as _execute
    return _execute(arguments)


def execute_phone_key(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_key as _execute
    return _execute(arguments)


def execute_phone_files(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_files as _execute
    return _execute(arguments)


def execute_phone_storage_report(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_storage_report as _execute
    return _execute(arguments)


def execute_phone_file_read(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_file_read as _execute
    return _execute(arguments)


def execute_phone_file_delete(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_file_delete as _execute
    return _execute(arguments)


def execute_phone_capabilities(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_capabilities as _execute
    return _execute(arguments)


def execute_phone_system_status(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_system_status as _execute
    return _execute(arguments)


def execute_phone_cache_report(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_cache_report as _execute
    return _execute(arguments)


def execute_phone_cache_trim(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_cache_trim as _execute
    return _execute(arguments)


def execute_phone_packages(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_packages as _execute
    return _execute(arguments)


def execute_phone_package_action(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_package_action as _execute
    return _execute(arguments)


def execute_phone_shell(arguments: dict[str, Any]) -> str:
    from .phone_semantic import execute_phone_shell as _execute
    return _execute(arguments)


def execute_systemcare_inspect(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_inspect as _execute
    return _execute(arguments)


def execute_systemcare_status(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_status as _execute
    return _execute(arguments)


def execute_systemcare_calibrate(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_calibrate as _execute
    return _execute(arguments)


def execute_systemcare_scan(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_scan as _execute
    return _execute(arguments)


def execute_systemcare_plan(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_plan as _execute
    return _execute(arguments)


def execute_systemcare_apply(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_apply as _execute
    return _execute(arguments)


def execute_systemcare_rollback(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_rollback as _execute
    return _execute(arguments)


def execute_systemcare_cancel(arguments: dict[str, Any]) -> str:
    from .systemcare import execute_systemcare_cancel as _execute
    return _execute(arguments)


# File/search executors and the schema catalog live in sibling modules;
# re-exported so tools.<name> import paths and patch seams stay stable.
from .definitions import TOOL_DEFINITIONS as TOOL_DEFINITIONS
from .files import (
    SKIP_PATH_PARTS as SKIP_PATH_PARTS,
    MAX_GREP_SCANNED_FILES as MAX_GREP_SCANNED_FILES,
    MAX_GREP_FILE_BYTES as MAX_GREP_FILE_BYTES,
    _skip_path as _skip_path,
    _iter_unskipped_files as _iter_unskipped_files,
    _numbered_lines as _numbered_lines,
    _coerce_positive_int as _coerce_positive_int,
    execute_read_file,
    execute_write_file,
    execute_edit_file,
    execute_find_files,
    _glob_hint_matches as _glob_hint_matches,
    execute_grep,
)
from .shell import (
    _bounded_pytest_command as _bounded_pytest_command,
    _cancel_requested as _cancel_requested,
    _command_tokens as _command_tokens,
    _configured_shell as _configured_shell,
    _direct_windows_command as _direct_windows_command,
    _empty_output_note as _empty_output_note,
    _execute_shell_background as _execute_shell_background,
    _looks_like_backgroundable as _looks_like_backgroundable,
    _looks_like_pytest_command as _looks_like_pytest_command,
    _output_exit_code as _output_exit_code,
    _output_succeeded as _output_succeeded,
    _preflight_command as _preflight_command,
    _pytest_argument_tokens as _pytest_argument_tokens,
    _pytest_missing_targets as _pytest_missing_targets,
    _pytest_missing_targets_message as _pytest_missing_targets_message,
    _pytest_positional_targets as _pytest_positional_targets,
    _pytest_preflight_needed as _pytest_preflight_needed,
    _pytest_worker_count as _pytest_worker_count,
    _shell_command as _shell_command,
    _shell_quote as _shell_quote,
    _test_runner_timeout as _test_runner_timeout,
    _tool_timeout as _tool_timeout,
    _verification_command_reuse_family as _verification_command_reuse_family,
    _wait_for_shell_process as _wait_for_shell_process,
    execute_shell as execute_shell,
    safe_env as safe_env,
)

MAX_WEB_FETCH_BYTES = 2_000_000

def runtime_tool_definitions(
    definitions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return provider schemas with facts that are only known at runtime.

    The turn environment already names the configured shell, but that context can
    be far from the ``shell`` schema after a long work prompt.  Put the exact
    syntax contract next to the command argument too, so the model cannot
    reasonably interpret "ambient shell" as the terminal that launched MO.
    Only the shell definition is copied; the global catalog remains immutable.
    """
    catalog = list(TOOL_DEFINITIONS if definitions is None else definitions)
    shell_exe = _configured_shell()
    shell_name = Path(shell_exe).name or str(shell_exe or "shell")
    shell_key = shell_name.casefold()
    if sys.platform == "win32" and shell_key in {
        "pwsh", "pwsh.exe", "powershell", "powershell.exe",
    }:
        contract = (
            f"Current tool shell: {shell_name} (PowerShell). Use PowerShell syntax; "
            "CMD-only commands may fail."
        )
    elif sys.platform == "win32":
        contract = (
            f"Current tool shell: {shell_name}. Use CMD syntax only; PowerShell "
            "cmdlets such as Remove-Item will fail."
        )
    else:
        contract = f"Current tool shell: {shell_name}. Use its native POSIX syntax."

    rendered: list[dict[str, Any]] = []
    for definition in catalog:
        fn = definition.get("function") if isinstance(definition, dict) else None
        if not isinstance(fn, dict) or str(fn.get("name") or "") != "shell":
            rendered.append(definition)
            continue
        shell_definition = dict(definition)
        shell_function = dict(fn)
        shell_function["description"] = (
            str(fn.get("description") or "").rstrip() + " " + contract
        ).strip()
        parameters = fn.get("parameters")
        if isinstance(parameters, dict):
            shell_parameters = dict(parameters)
            properties = parameters.get("properties")
            if isinstance(properties, dict) and isinstance(properties.get("command"), dict):
                shell_properties = dict(properties)
                command_property = dict(properties["command"])
                command_property["description"] = (
                    str(command_property.get("description") or "").rstrip()
                    + " "
                    + contract
                ).strip()
                shell_properties["command"] = command_property
                shell_parameters["properties"] = shell_properties
            shell_function["parameters"] = shell_parameters
        shell_definition["function"] = shell_function
        rendered.append(shell_definition)
    return rendered


def execute_git_status(arguments: dict[str, Any]) -> str:
    cwd = str(Path(arguments.get("workdir") or os.getcwd()))
    action = str(arguments.get("action") or "status").strip().lower()
    if action not in {"status", "check_ignore"}:
        return "Error: git_status action must be status or check_ignore"
    command = ["git", "status", "--short", "--branch"]
    timeout = 20
    if action == "check_ignore":
        paths_raw = str(arguments.get("paths") or "").split()
        if not paths_raw:
            return "Error: git_status check_ignore requires paths"
        command = ["git", "check-ignore", *paths_raw]
        timeout = 15
    try:
        kwargs = {"cwd": cwd, "text": True, "capture_output": True, "timeout": timeout}
        apply_windows_hidden_process_flags(kwargs)
        proc = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired:
        return f"Error: git {action} timed out"
    except Exception as exc:
        return f"Error running git {action}: {exc}"
    output = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if action == "status":
        body = output + (("\n[stderr]\n" + stderr) if stderr else "")
        return (body.strip() or "[clean/no output]") + f"\n[exit code {proc.returncode}]"
    if proc.returncode == 1 and not output:
        return f"[none ignored] (all paths are tracked or don't exist)\n[exit code {proc.returncode}]"
    if proc.returncode == 0:
        return (output or "[no output — all paths ignored?]") + f"\n[exit code {proc.returncode}]"
    body = "\n".join(part for part in (output, stderr) if part)
    return (body or "[empty]") + f"\n[exit code {proc.returncode}]"


def execute_test_runner(arguments: dict[str, Any]) -> str:
    command = _bounded_pytest_command(arguments.get("command", ""))
    workdir = arguments.get("workdir")
    clean_env = arguments.get("_clean_env", True)
    cancel_event = arguments.get("_cancel_event")
    timeout = _test_runner_timeout(command, arguments.get("timeout"))
    missing_targets = _pytest_missing_targets_message(command, workdir)
    if missing_targets:
        return missing_targets
    if _pytest_preflight_needed(command):
        preflight_timeout = int(os.environ.get("MO_TEST_PREFLIGHT_TIMEOUT", "180") or 180)
        preflight = execute_shell({
            "command": _preflight_command(preflight_timeout),
            "workdir": workdir,
            "timeout": preflight_timeout,
            "_clean_env": clean_env,
            "_cancel_event": cancel_event,
            "_allow_background": False,
        })
        if not _output_succeeded(preflight):
            return "[pytest preflight failed — full suite not run]\n" + preflight
        full = execute_shell({
            "command": command,
            "workdir": workdir,
            "timeout": timeout,
            "_clean_env": clean_env,
            "_env_overrides": {"MO_SKIP_PUBLIC_PRIVATE_PREFLIGHT": "1"},
            "_cancel_event": cancel_event,
            "_allow_background": True,
            "_worker_callback": arguments.get("_worker_callback"),
        })
        # Pass through background status if the full suite was backgrounded
        if isinstance(full, str) and full.startswith("[BACKGROUND_ACTIVE|"):
            return "[pytest preflight passed]\n" + full
        return "[pytest preflight passed]\n" + preflight.rstrip() + "\n\n[pytest full suite]\n" + full
    full = execute_shell({
        "command": command,
        "workdir": workdir,
        "timeout": timeout,
        "_clean_env": clean_env,
        "_cancel_event": cancel_event,
        "_allow_background": True,
        "_worker_callback": arguments.get("_worker_callback"),
    })
    # Pass through background status
    return full


def execute_project_bridge(arguments: dict[str, Any]) -> str:
    target = Path(arguments.get("path") or os.getcwd())
    limit = max(800, min(int(arguments.get("limit", 4000)), 20000))
    from core.context.project_context import load_project_context_files, render_project_context_files

    files = load_project_context_files(target)
    if not files:
        return "[no AGENTS.md found in project path]"
    return render_project_context_files(files, max_chars=limit, title="### Project bridge instructions")


def execute_map_project(arguments: dict[str, Any]) -> str:
    return "Error: map_project must run through MO's agent runtime."


def execute_role_work(arguments: dict[str, Any]) -> str:
    """Fail closed outside AgentTurnDispatchMixin's conversation owner."""
    return "[ROLE WORK BLOCKED] role_work must run through the owning Agent conversation."


_SNAPSHOT_DROP_TAGS = ("script", "style", "noscript", "svg", "head", "nav", "header", "footer", "aside", "form")


def _extract_readable(html: str) -> str:
    """Heuristic main-content extraction → clean markdown-ish text. Zero-dep.

    Drops boilerplate (nav/header/footer/aside/script/style), prefers a <main> or
    <article> region when present, and preserves heading/list/paragraph structure
    instead of collapsing the whole page to a single line.
    """
    import html as _htmllib
    import re as _re

    title_match = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE | _re.DOTALL)
    title = _htmllib.unescape(title_match.group(1).strip()) if title_match else ""

    work = html
    for tag in _SNAPSHOT_DROP_TAGS:
        work = _re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", work, flags=_re.DOTALL | _re.IGNORECASE)
    work = _re.sub(r"<!--.*?-->", " ", work, flags=_re.DOTALL)

    # Prefer the marked main-content region when the page provides one.
    region = _re.search(r"<(main|article)\b[^>]*>(.*?)</\1>", work, _re.DOTALL | _re.IGNORECASE)
    body = region.group(2) if region else work

    # Preserve structure: headings → markdown, list items + block ends → newlines.
    body = _re.sub(r"<h([1-6])\b[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"</h[1-6]>", "\n", body, flags=_re.IGNORECASE)
    body = _re.sub(r"<li\b[^>]*>", "\n- ", body, flags=_re.IGNORECASE)
    body = _re.sub(r"</?(p|div|section|tr|ul|ol|table|br)\b[^>]*>", "\n", body, flags=_re.IGNORECASE)

    text = _re.sub(r"<[^>]+>", " ", body)            # strip remaining tags
    text = _htmllib.unescape(text)                   # decode entities
    text = _re.sub(r"[ \t]+", " ", text)             # collapse inline whitespace
    text = _re.sub(r" *\n *", "\n", text)            # trim around newlines
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()  # collapse blank runs

    # Prepend the title as a heading, but don't duplicate it when the body already
    # opens with a matching heading (common when <title> == <h1>).
    first_heading = text.lstrip().split("\n", 1)[0].lstrip("# ").strip() if text else ""
    if title and first_heading.lower() != title.lower():
        return f"# {title}\n\n{text}".strip()
    return text


def execute_inspect_repo(arguments: dict[str, Any]) -> str:
    """Inspect a GitHub repo into an inert, approval-gated skill candidate."""
    from core.skills.importing import pipeline as _pipeline

    url = (arguments.get("url") or "").strip()
    if not url:
        return "Error: inspect_repo requires a 'url'."

    try:
        result = _pipeline.inspect(url, network_allowed=True)
    except Exception as exc:
        return f"Error inspecting repo: {type(exc).__name__}: {exc}"

    return json.dumps(result, indent=2, default=str)


def execute_use_repo(arguments: dict[str, Any]) -> str:
    """Fetch a GitHub repo as temporary turn context (no install/persistence)."""
    from core.skills.importing import pipeline as _pipeline

    url = (arguments.get("url") or "").strip()
    if not url:
        return "Error: use_repo requires a 'url'."

    try:
        result = _pipeline.use(url, network_allowed=True)
    except Exception as exc:
        return f"Error using repo: {type(exc).__name__}: {exc}"

    return json.dumps(result, indent=2, default=str)


def execute_web_fetch(arguments: dict[str, Any]) -> str:
    """Fetch bounded HTTP content with validated redirects and source provenance."""
    url = arguments["url"]
    mode = str(arguments.get("mode") or "raw").strip().lower()
    if mode not in {"raw", "readable"}:
        return "Error: web_fetch mode must be raw or readable"
    method = str(arguments.get("method") or "GET").upper()
    headers_value = arguments.get("headers")
    if mode == "readable" and method != "GET":
        return "Error: web_fetch readable mode supports GET only"
    try:
        import httpx
    except ImportError:
        return "Error: httpx not installed"
    headers = {}
    if headers_value:
        try:
            headers = json.loads(headers_value) if isinstance(headers_value, str) else headers_value
            if not isinstance(headers, dict):
                return "Error: headers must be a JSON object"
        except (TypeError, json.JSONDecodeError):
            return "Error: Invalid headers JSON"

    config = arguments.get("_mo_config") or {}
    allowed_hosts = {
        str(host).strip().lower()
        for host in (config.get("sandbox", {}).get("web_fetch_allowed_hosts") or [])
        if str(host).strip()
    }
    current_url = str(url)
    current_headers = headers
    redirect_count = 0
    try:
        while True:
            with httpx.stream(method, current_url, headers=current_headers, timeout=30, follow_redirects=False) as response:
                next_request = response.next_request
                if next_request is not None:
                    next_url = next_request.url
                    host = next_url.host.lower()
                    if next_url.scheme not in {"http", "https"} or (allowed_hosts and host not in allowed_hosts):
                        return f"Error fetching {url}: redirect target host not allowed: {host or '?'}"
                    redirect_count += 1
                    if redirect_count > 5:
                        return f"Error fetching {url}: too many redirects"
                    # HTTPX owns redirect methods, normalized origins, and sensitive
                    # headers. Retain this tool's explicit same-origin Cookie behavior.
                    source_url = response.request.url
                    if (source_url.scheme, source_url.host, source_url.port) == (next_url.scheme, next_url.host, next_url.port):
                        cookie = response.request.headers.get("cookie")
                        if cookie:
                            next_request.headers["Cookie"] = cookie
                    current_url = str(next_url)
                    current_headers = next_request.headers
                    method = next_request.method
                    continue
                try:
                    content_length = int(response.headers.get("content-length") or 0)
                except Exception:
                    content_length = 0
                if content_length > MAX_WEB_FETCH_BYTES:
                    return f"Error fetching {url}: response too large ({content_length} bytes)"
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_WEB_FETCH_BYTES:
                        return f"Error fetching {url}: response too large"
                    chunks.append(chunk)
                body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                status_code = response.status_code
                content_type = response.headers.get("content-type", "")
            break
        clean_body = _extract_readable(body) if mode == "readable" else body
        clean_body = redact_sensitive_text(clean_body)
        clean_body = cap_text_evidence(clean_body, 10_000 if mode == "readable" else 50_000)
        provenance = (
            f"[WEB SOURCE]\nrequested_url={url}\nfinal_url={current_url}\n"
            f"content_type={content_type or 'unknown'}\nredirects={redirect_count}"
        )
        return f"[HTTP {status_code}]\n{fence_untrusted_web_content(clean_body)}\n{provenance}"
    except Exception as e:
        return f"Error fetching {url}: {e}"


# ── Tool executor map ──────────────────────────────────────────────

def _format_search_results(
    items: list[dict],
    *,
    url_key: str,
    summary_key: str,
) -> str:
    lines = []
    for item in items:
        title = str(item.get("title") or "").strip()
        url = str(item.get(url_key) or "").strip()
        summary = str(item.get(summary_key) or "").strip()[:200]
        lines.append(f"- {title}\n  {url}" + (f"\n  {summary}" if summary else ""))
    return "\n".join(lines)


def _format_brave_results(data: dict, limit: int) -> str:
    items = ((data.get("web") or {}).get("results") or [])[:limit]
    return _format_search_results(items, url_key="url", summary_key="description")


def _format_serper_results(data: dict, limit: int) -> str:
    items = (data.get("organic") or [])[:limit]
    return _format_search_results(items, url_key="link", summary_key="snippet")


def _web_search_keyed(query: str, limit: int, config: dict[str, Any] | None = None) -> str | None:
    """Real web search via an optional, operator-set API key (no Python dependency).

    Reads MO_WEB_SEARCH_PROVIDER (brave|serper) from the environment and resolves
    MO_WEB_SEARCH_API_KEY through the provider credential scope.
    Returns formatted results, or None to fall through to the keyless default. Opt-in:
    absent key → None → DuckDuckGo fallback (current behavior preserved).
    """
    provider = os.environ.get("MO_WEB_SEARCH_PROVIDER", "").strip().lower()
    from core.state.secrets import resolve_secret

    key = resolve_secret("MO_WEB_SEARCH_API_KEY", config=config, service="providers").strip()
    if not key or provider not in {"brave", "serper"}:
        return None
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        if provider == "brave":
            url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={limit}"
            req = urllib.request.Request(url, headers={"X-Subscription-Token": key, "Accept": "application/json", "User-Agent": "MO-Agent/1.0"})
            resp = urllib.request.urlopen(req, timeout=12)
            formatted = _format_brave_results(_json.loads(resp.read().decode("utf-8")), limit)
        else:  # serper
            body = _json.dumps({"q": query, "num": limit}).encode("utf-8")
            req = urllib.request.Request("https://google.serper.dev/search", data=body, headers={"X-API-KEY": key, "Content-Type": "application/json", "User-Agent": "MO-Agent/1.0"})
            resp = urllib.request.urlopen(req, timeout=12)
            formatted = _format_serper_results(_json.loads(resp.read().decode("utf-8")), limit)
        return formatted
    except Exception as exc:
        # The caller owns fallback and its disclosure; absence of credentials
        # must not be indistinguishable from a failed configured service.
        raise RuntimeError(f"Configured web search failed ({type(exc).__name__})") from exc


def _web_search_duckduckgo(query: str, limit: int) -> str:
    """Keyless fallback via DuckDuckGo's Instant Answer API (no key, no dependency).

    This is an encyclopedia/instant-answer lookup, NOT a ranked web search: it
    returns nothing for most ordinary queries. Real results require an operator-set
    key (see _web_search_keyed). Kept as an honest last resort rather than a
    scraping path, because DDG's HTML endpoints serve bot challenges.
    """
    try:
        import urllib.request
        import urllib.parse
        import json as _json
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&no_redirect=1"
        req = urllib.request.Request(url, headers={"User-Agent": "MO-Agent/1.0"})
        resp = urllib.request.urlopen(req, timeout=12)
        data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        lines: list[str] = []
        abstract = str(data.get("AbstractText") or "").strip()
        if abstract:
            source = str(data.get("AbstractURL") or "").strip()
            lines.append(f"- {data.get('Heading') or query}\n  {source}\n  {abstract}"[:400])
        for topic in (data.get("RelatedTopics") or [])[:limit]:
            if isinstance(topic, dict) and topic.get("Text"):
                lines.append(f"- {topic['Text'][:200]}\n  {topic.get('FirstURL') or ''}")
            if len(lines) >= limit:
                break
        return "\n".join(lines[:limit])
    except Exception as exc:
        raise RuntimeError(f"DuckDuckGo search failed ({type(exc).__name__})") from exc


def execute_web_search(arguments: dict[str, Any]) -> str:
    """Search the web. Real ranked results require an operator-set API key
    (Brave/Serper) via MO_WEB_SEARCH_PROVIDER + MO_WEB_SEARCH_API_KEY; without one,
    falls back to DuckDuckGo's limited keyless Instant Answer. No Python dependency."""
    query = str(arguments.get("query", "")).strip()
    limit = min(max(int(arguments.get("limit", 5) or 5), 1), 10)
    if not query:
        return "Error: empty search query"
    fallback_note = ""
    try:
        keyed = _web_search_keyed(query, limit, arguments.get("_mo_config") or {})
    except RuntimeError as exc:
        fallback_note = f"{exc}; using the keyless backend.\n"
        keyed = None
    if keyed is not None:
        return fence_untrusted_web_content(keyed) if keyed else "No results returned by the configured full web search."
    try:
        result = _web_search_duckduckgo(query, limit)
    except RuntimeError as exc:
        return f"Error: {exc}. {fallback_note.strip()}".strip()
    return fallback_note + (
        fence_untrusted_web_content(result) if result else "No results returned; this does not establish that sources do not exist."
    )


def _graph_empty_message(healthy: str) -> str:
    """Say WHY a graph query came back empty, and name the remedy.

    "no graph built here", "the graph is stale" and "a healthy graph has no match" are three
    states with three different next actions. Collapsing them into "fall back to grep/read_file"
    told MO to abandon the graph at the exact moment it was deciding whether to use it — and a
    tool result at the decision point beats every system-prompt line telling it to reach for the
    graph first. A fresh clone or worktree has no graph, so that advice was self-fulfilling.

    Only reached when a query returns nothing, so reading graph status here costs nothing on the
    hot path.
    """
    try:
        from core.graph.structural_graph import graph_status

        status = graph_status(os.getcwd())
    except Exception:
        return healthy
    if not status.get("available"):
        return (
            "No structural graph is built at this project root, so this tool cannot answer yet. "
            "For ordinary foreground work, continue with bounded grep/read_file evidence. "
            "build_graph can queue a background refresh; do not wait/retry it in this turn."
        )
    if status.get("stale"):
        reasons = ", ".join(str(r) for r in [*(status.get("stale_reasons") or []), *(status.get("stale_sample") or [])]) or "files changed"
        return (
            f"The structural graph is stale ({reasons}), so this result may be wrong. "
            "Use it only as orientation and verify affected source directly. build_graph can queue "
            f"a background refresh, but do not wait/retry it in this turn. {healthy}"
        )
    return healthy


def _graph_trust_line() -> str:
    try:
        from core.graph.structural_graph import graph_status

        status = graph_status(os.getcwd())
    except Exception:
        return ""
    if not status.get("available"):
        return "Graph trust: unavailable."
    if status.get("stale"):
        reasons = ", ".join(str(item) for item in [*(status.get("stale_reasons") or []), *(status.get("stale_sample") or [])]) or "files changed"
        return (
            f"Graph trust: stale orientation only ({reasons}); verify affected source directly. "
            "Do not block this foreground turn waiting for graph refresh."
        )
    quality = status.get("quality") if isinstance(status.get("quality"), dict) else {}
    return (
        "Graph trust: fresh orientation; "
        f"qualified_symbols={quality.get('qualified_symbol_nodes', 0)}, "
        f"resolved_calls={quality.get('resolved_calls', 0)}, ambiguous_calls={quality.get('ambiguous_calls', 0)}."
    )


def _format_graph_hits(hits: list[dict[str, Any]], fields: list[tuple[str, str]], limit: int = 20) -> str:
    lines: list[str] = []
    for hit in hits[:limit]:
        parts = [f"{label}={hit.get(key)}" for label, key in fields if hit.get(key) not in (None, "")]
        lines.append("- " + ", ".join(parts))
    more = len(hits) - limit
    if more > 0:
        lines.append(f"... (+{more} more)")
    return "\n".join(lines)


def execute_build_graph(arguments: dict[str, Any]) -> str:
    """Build/refresh the native structural graph for the current project root.

    Read tools never build implicitly. Unrequested agent maintenance queues the
    isolated worker; an operator-requested foreground build may wait for it.
    """
    try:
        from core.graph.structural_graph import (
            build_structural_graph_isolated,
            graph_status,
            maybe_update_graph_async,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: structural graph unavailable: {exc}"
    if bool(arguments.get("_mo_background")):
        try:
            status = graph_status(os.getcwd())
            if status.get("available") and not status.get("stale"):
                return (
                    f"Structural graph up_to_date: {status.get('nodes')} nodes, "
                    f"{status.get('edges')} edges across {status.get('indexed_files')} file(s)."
                )
            started = maybe_update_graph_async(
                root=os.getcwd(),
                reason="foreground-agent-request",
            )
        except Exception as exc:  # noqa: BLE001
            return (
                f"Graph refresh was not started: {exc}. Continue this turn with "
                "bounded grep/read_file evidence; do not wait or retry build_graph."
            )
        state = "stale" if status.get("available") else "unavailable"
        if started:
            return (
                f"Structural graph refresh queued in a below-normal-priority background worker "
                f"because the graph is {state}. Do not wait or retry build_graph in this turn; "
                "the active provider loop will supply one fresh bounded graph slice when ready. "
                "Continue with bounded source evidence meanwhile."
            )
        current_status = graph_status(os.getcwd())
        if current_status.get("refreshing"):
            return (
                f"Structural graph is {state}; its refresh worker is already running. Do not wait "
                "or retry build_graph in this turn; the active provider loop will supply one fresh "
                "bounded graph slice when ready. Continue with bounded source evidence meanwhile."
            )
        return (
            f"Structural graph is {state}; automatic refresh is disabled or unavailable. Do not "
            "wait or retry build_graph in this turn. Continue with bounded source evidence."
        )
    try:
        result = build_structural_graph_isolated(os.getcwd())
    except Exception as exc:  # noqa: BLE001
        return f"Error running build_graph: {exc}"
    if not result.get("built"):
        return f"build_graph did not build the graph: {result.get('reason') or 'unknown reason'}."
    return (
        f"Structural graph {result.get('status') or 'built'}: {result.get('nodes')} nodes, "
        f"{result.get('edges')} edges across {result.get('files')} file(s). "
        "code_search, find_callers and find_callees can answer now."
    )


def execute_code_search(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "") or "").strip()
    if not query:
        return "Error: code_search requires a 'query'."
    top_n = arguments.get("top_n")
    try:
        top_n = int(top_n) if top_n else 10
    except (TypeError, ValueError):
        top_n = 10
    hits = []
    display_limit = 20
    graph_error = ""
    try:
        from core.graph.search import search
        hits = search(query, cwd=os.getcwd(), top_n=top_n)
    except Exception as exc:
        graph_error = f"Error running code_search: {exc}"
    from core.graph.history import history_context

    history = history_context(
        query,
        os.getcwd(),
        paths=[str(hit.get("source_file") or "") for hit in hits[:5]],
        max_chars=1000,
    )
    try:
        from core.knowledge import query_manifest, render_knowledge

        result = query_manifest(query, os.getcwd(), limit=min(top_n, 12), existing_graph_hits=hits[:display_limit])
        knowledge = render_knowledge(result) if result.get("results") or not result.get("manifest_current") else ""
    except Exception:
        knowledge = "Project knowledge unavailable; graph and history results remain independent."
    if graph_error:
        body = graph_error
    elif not hits:
        body = _graph_empty_message(
            f"No recorded code-graph matches for {query!r}. "
            "Use grep/read_file to inspect sources outside this result."
        )
    else:
        body = "\n".join(part for part in (
            _graph_trust_line(),
            _format_graph_hits(
                hits,
                [("file", "source_file"), ("symbol", "label"), ("at", "source_location"), ("match", "match_type"), ("score", "score")],
                limit=display_limit,
            ),
        ) if part)
    return "\n\n".join(part for part in (body, knowledge, history) if part)


def execute_project_history(arguments: dict[str, Any]) -> str:
    """Native transport for the existing project-history owner, not new state."""
    from core.graph.history import history_status, record_finding, trace_finding

    action = str(arguments.get("action") or "").strip().lower()
    try:
        if action == "status":
            result = history_status(os.getcwd())
        elif action == "record":
            result = record_finding(arguments, os.getcwd())
        elif action in {"inspect", "trace"}:
            source = arguments.get("source") if action == "trace" else None
            if action == "trace" and source is None:
                return "Error: trace requires an explicit zero-based source index; use inspect for the finding itself."
            result = trace_finding(str(arguments.get("id") or ""), source, os.getcwd())
        else:
            return "Error: project_history action must be status, inspect, record or trace."
    except Exception as exc:
        from core.utils.text_safety import redact_secret_values

        return f"Error running project_history: {redact_secret_values(str(exc))}"
    return json.dumps(result, ensure_ascii=True)


def execute_find_callers(arguments: dict[str, Any]) -> str:
    symbol = str(arguments.get("symbol", "") or "").strip()
    if not symbol:
        return "Error: find_callers requires a 'symbol'."
    max_depth = arguments.get("max_depth")
    try:
        max_depth = int(max_depth) if max_depth else 2
    except (TypeError, ValueError):
        max_depth = 2
    try:
        from core.graph.callgraph import get_callers
    except Exception as exc:
        return f"Error: code graph unavailable: {exc}"
    try:
        match = get_callers(symbol, cwd=os.getcwd(), max_depth=max_depth, return_meta=True)
    except Exception as exc:
        return f"Error running find_callers: {exc}"
    if match.get("ambiguous"):
        return "\n".join(part for part in (
            _graph_trust_line(),
            f"Ambiguous symbol {symbol!r}; use a qualified name. Candidates: {', '.join(match.get('matched_ids') or [])}",
        ) if part)
    hits = match.get("results") or []
    if not hits:
        return _graph_empty_message(
            f"No recorded callers found for {symbol!r}. "
            "Static coverage is incomplete; inspect imports and source before concluding it is unused."
        )
    body = _format_graph_hits(
        hits,
        [("caller", "caller_label"), ("file", "caller_file"), ("relation", "relation"), ("depth", "depth")],
    )
    return "\n".join(part for part in (_graph_trust_line(), body) if part)


def execute_find_callees(arguments: dict[str, Any]) -> str:
    symbol = str(arguments.get("symbol", "") or "").strip()
    if not symbol:
        return "Error: find_callees requires a 'symbol'."
    max_depth = arguments.get("max_depth")
    try:
        max_depth = int(max_depth) if max_depth else 2
    except (TypeError, ValueError):
        max_depth = 2
    try:
        from core.graph.callgraph import get_callees
    except Exception as exc:
        return f"Error: code graph unavailable: {exc}"
    try:
        match = get_callees(symbol, cwd=os.getcwd(), max_depth=max_depth, return_meta=True)
    except Exception as exc:
        return f"Error running find_callees: {exc}"
    if match.get("ambiguous"):
        return "\n".join(part for part in (
            _graph_trust_line(),
            f"Ambiguous symbol {symbol!r}; use a qualified name. Candidates: {', '.join(match.get('matched_ids') or [])}",
        ) if part)
    hits = match.get("results") or []
    if not hits:
        return _graph_empty_message(
            f"No recorded callees found for {symbol!r}. "
            "Static coverage is incomplete; inspect the source before concluding it makes no calls."
        )
    body = _format_graph_hits(
        hits,
        [("callee", "callee_label"), ("file", "callee_file"), ("relation", "relation"), ("depth", "depth")],
    )
    return "\n".join(part for part in (_graph_trust_line(), body) if part)


def execute_redundancy_scan(arguments: dict[str, Any]) -> str:
    """Project the canonical redundancy detector as bounded read-only evidence."""
    args = arguments or {}
    root = Path(str(args.get("root") or os.getcwd())).expanduser().resolve()
    if not root.is_dir():
        return f"Error: redundancy_scan root is not a directory: {root}"
    try:
        max_findings = max(1, min(100, int(args.get("max_findings") or 20)))
    except (TypeError, ValueError):
        return "Error: redundancy_scan max_findings must be an integer from 1 to 100."
    try:
        from core.diagnostics.redundancy_check import (
            evaluate_allowlist, load_allowlist, scan_redundancy,
        )
        from core.diagnostics.source_inventory import inventory_summary, load_source_inventory

        inventory = load_source_inventory(
            root,
            include_untracked=args.get("include_untracked", True) is not False,
            include_test_overlay=bool(args.get("include_test_overlay", False)),
        )
        result = scan_redundancy(inventory)
        candidates = list(result.findings)
        problems = list(result.problems)
        allowlist_path = root / "core" / "diagnostics" / "redundancy_allowlist.json"
        if allowlist_path.is_file():
            allowances, allowlist_problems = load_allowlist(allowlist_path)
            candidates, allowance_problems = evaluate_allowlist(result.findings, allowances)
            problems.extend(allowlist_problems)
            problems.extend(allowance_problems)
        summary = inventory_summary(inventory)
    except Exception as exc:
        return f"Error running redundancy_scan: {exc}"

    lines = [
        "MO redundancy scan (read-only similarity candidates; inspect before conclusions):",
        f"- files: {summary.get('total', 0)}",
        f"- candidates: {len(candidates)}",
        f"- problems: {len(problems)}",
    ]
    lines.extend(f"- {finding.render()}" for finding in candidates[:max_findings])
    if len(candidates) > max_findings:
        lines.append(f"- truncated: {len(candidates) - max_findings} candidate(s) omitted")
    lines.extend(f"- problem: {problem.render()}" for problem in problems[:20])
    if len(problems) > 20:
        lines.append(f"- problems truncated: {len(problems) - 20} omitted")
    return "\n".join(lines)


def _graph_node_text(node: dict[str, Any]) -> str:
    parts = [
        str(node.get("label") or node.get("id") or ""),
        str(node.get("source_file") or ""),
        str(node.get("source_location") or ""),
        f"community={node.get('community_label') or node.get('community')}" if node.get("community") is not None else "",
    ]
    return " ".join(part for part in parts if part).strip()


def _format_graph_edges(edges: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    lines: list[str] = []
    for edge in edges[:limit]:
        src = edge.get("source_label") or edge.get("source") or "?"
        tgt = edge.get("target_label") or edge.get("target") or "?"
        rel = edge.get("relation") or "related"
        conf = edge.get("confidence") or ""
        direction = " (reverse)" if edge.get("reverse") else ""
        lines.append(f"- {src} --{rel} [{conf}]{direction}--> {tgt}")
    more = len(edges) - limit
    if more > 0:
        lines.append(f"... (+{more} more)")
    return lines


def execute_graph_explain(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "") or "").strip()
    if not query:
        return "Error: graph_explain requires a 'query'."
    try:
        from core.graph.query import explain
        result = explain(query, cwd=os.getcwd(), limit=arguments.get("limit") or 12)
    except Exception as exc:
        return f"Error running graph_explain: {exc}"
    if not result.get("available"):
        return f"MO graph explain unavailable: {result.get('reason') or 'graph unavailable'}."
    if not result.get("matched"):
        if result.get("ambiguous"):
            candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
            names = ", ".join(_graph_node_text(item) for item in candidates[:5] if isinstance(item, dict))
            return "\n".join(part for part in (
                _graph_trust_line(),
                f"Ambiguous graph query {query!r}; use a qualified name. Candidates: {names}",
            ) if part)
        return "\n".join(part for part in (
            _graph_trust_line(),
            f"No MO graph node matched {query!r}. Orientation only; fall back to code_search/grep/read_file.",
        ) if part)
    node = result.get("node") if isinstance(result.get("node"), dict) else {}
    lines = [
        "MO graph explain (orientation only):",
        f"- {_graph_trust_line()}",
        f"- match: {result.get('match_type')}",
        f"- node: {_graph_node_text(node)}",
        f"- degree: {result.get('degree', 0)}",
        f"- confidence: {result.get('confidence_breakdown') or {}}",
    ]
    edges = result.get("neighbours") if isinstance(result.get("neighbours"), list) else []
    if edges:
        lines.append("- neighbours:")
        lines.extend(_format_graph_edges(edges, limit=12))
    return "\n".join(lines)


def execute_graph_neighbors(arguments: dict[str, Any]) -> str:
    query = str(arguments.get("query", "") or "").strip()
    if not query:
        return "Error: graph_neighbors requires a 'query'."
    try:
        from core.graph.query import neighbors
        result = neighbors(
            query,
            cwd=os.getcwd(),
            depth=arguments.get("depth") or 1,
            limit=arguments.get("limit") or 20,
            relation_filter=str(arguments.get("relation_filter") or "").strip() or None,
        )
    except Exception as exc:
        return f"Error running graph_neighbors: {exc}"
    if not result.get("available"):
        return f"MO graph neighbors unavailable: {result.get('reason') or 'graph unavailable'}."
    if not result.get("matched"):
        if result.get("ambiguous"):
            return "\n".join(part for part in (
                _graph_trust_line(),
                f"Ambiguous graph query {query!r}; use a qualified name.",
            ) if part)
        return "\n".join(part for part in (
            _graph_trust_line(),
            f"No MO graph node matched {query!r}.",
        ) if part)
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    lines = [
        "MO graph neighbors (orientation only):",
        f"- {_graph_trust_line()}",
        f"- query: {query}",
        f"- depth: {result.get('depth')}",
        f"- returned_edges: {len(edges)}",
    ]
    lines.extend(_format_graph_edges(edges, limit=20))
    if result.get("truncated"):
        lines.append("- truncated: true")
    return "\n".join(lines)


def execute_graph_path(arguments: dict[str, Any]) -> str:
    source = str(arguments.get("source", "") or "").strip()
    target = str(arguments.get("target", "") or "").strip()
    if not source or not target:
        return "Error: graph_path requires 'source' and 'target'."
    try:
        from core.graph.query import shortest_path
        result = shortest_path(source, target, cwd=os.getcwd(), max_hops=arguments.get("max_hops") or 8)
    except Exception as exc:
        return f"Error running graph_path: {exc}"
    if not result.get("available"):
        return f"MO graph path unavailable: {result.get('reason') or 'graph unavailable'}."
    if not result.get("found"):
        return "\n".join(part for part in (
            _graph_trust_line(),
            f"No MO graph path found from {source!r} to {target!r}: {result.get('reason') or 'not found'}.",
        ) if part)
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    labels = " -> ".join(str(node.get("label") or node.get("id") or "?") for node in nodes if isinstance(node, dict))
    lines = [
        "MO graph path (orientation only):",
        f"- {_graph_trust_line()}",
        f"- hops: {result.get('hops')}",
        f"- path: {labels}",
    ]
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    if edges:
        lines.append("- edges:")
        lines.extend(_format_graph_edges(edges, limit=12))
    return "\n".join(lines)


def execute_graph_stats(arguments: dict[str, Any]) -> str:
    try:
        from core.graph.query import stats
        result = stats(cwd=os.getcwd(), limit=arguments.get("limit") or 10)
    except Exception as exc:
        return f"Error running graph_stats: {exc}"
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    if not result.get("available"):
        return f"MO graph stats unavailable: {status or result.get('reason') or 'graph unavailable'}."
    lines = [
        "MO graph stats (orientation only):",
        f"- nodes: {status.get('nodes', 0)}",
        f"- edges: {status.get('edges', 0)}",
        f"- path_groups: {status.get('groups', status.get('communities', 0))}",
        f"- stale: {status.get('stale')}",
        f"- trust: {status.get('trust')}",
        f"- quality: {status.get('quality') or {}}",
        f"- confidence: {status.get('confidence_breakdown') or {}}",
        f"- provenance: {status.get('provenance_breakdown') or {}}",
    ]
    gods = result.get("god_nodes") if isinstance(result.get("god_nodes"), list) else []
    if gods:
        lines.append("- god_nodes: " + "; ".join(
            f"{item.get('label') or item.get('id')} degree={item.get('degree')}"
            for item in gods[:5] if isinstance(item, dict)
        ))
    return "\n".join(lines)


def execute_tool_search(arguments: dict[str, Any]) -> str:
    """Fallback catalog search for non-Agent dispatch contexts.

    Agent dispatch replaces this with a per-turn registry so activations affect
    the next provider request.  This fallback still returns the same result shape
    for tests, diagnostics, and standalone tool contexts.
    """
    try:
        from core.tooling.tool_registry import DeferredToolRegistry
        return DeferredToolRegistry(TOOL_DEFINITIONS).search(arguments or {})
    except Exception as exc:
        return f"Error running tool_search: {exc}"


def execute_record_convention(arguments: dict[str, Any]) -> str:
    """MO records a durable, code-location-scoped convention it has learned (autonomous)."""
    try:
        from core.skills import write_convention
        path = write_convention(
            name=str(arguments.get("name", "") or "").strip(),
            rule=str(arguments.get("rule", "") or "").strip(),
            scope=str(arguments.get("scope", "") or "").strip(),
            evidence=str(arguments.get("evidence", "") or "").strip(),
            confidence=(str(arguments.get("confidence", "high") or "high").strip() or "high"),
            profile=arguments.get("_mo_profile"),
            config=arguments.get("_mo_config"),
            project_cwd=arguments.get("_mo_project_cwd"),
        )
        return (
            f"Convention recorded ({path}). Scope: {arguments.get('scope')}. "
            "It will auto-surface only when later work matches those files in this project. "
            "This is not an issue/task registry and will not surface at startup "
            "or on unrelated surfaces."
        )
    except ValueError as exc:
        return (f"Convention NOT recorded: {exc}. A convention needs a concrete rule AND a file-glob "
                "scope (e.g. 'core/tasking/*'). A behavioral style with no code location is not a convention.")
    except Exception as exc:
        return f"Convention write failed: {exc}"


def execute_record_profile_fact(arguments: dict[str, Any]) -> str:
    """Persist one safe fact through the profile-owned exact-ID lifecycle."""
    from core.profile.facts import record_profile_fact

    runtime = dict(arguments or {})
    profile = runtime.pop("_mo_profile", None)
    config = runtime.pop("_mo_config", None)
    try:
        state, entry = record_profile_fact(
            str(runtime.get("category") or ""),
            str(runtime.get("fact") or ""),
            str(runtime.get("evidence") or ""),
            profile=profile,
            config=config,
        )
    except Exception as exc:
        return f"Fact write failed: {exc}"
    if state == "recorded" and entry is not None:
        return f"Recorded operator fact {entry.id} [{entry.category}]: {entry.fact}"
    if state == "already recorded" and entry is not None:
        return f"Fact already recorded ({entry.id}): {entry.fact}"
    return f"Fact NOT recorded: {state}."


def execute_credential_status(arguments: dict[str, Any]) -> str:
    """Return a value-free credential inventory for model-safe diagnostics."""
    from core.state.paths import codex_auth_path
    from core.state.secrets import secret_status

    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    requested = str(runtime.get("service") or "all").strip().lower()
    allowed = {"all", "providers", "telegram", "everywhere"}
    if requested not in allowed:
        return "Credential status error: unknown service."

    rows: list[str] = []
    if requested in {"all", "providers"}:
        keys: list[str] = []
        external = False
        for provider in config.get("providers") or []:
            if not isinstance(provider, dict):
                continue
            key = str(provider.get("api_key_env") or "").strip()
            if key:
                keys.append(key)
            if str(provider.get("type") or provider.get("api_mode") or "").lower() == "codex_responses" or str(provider.get("name") or "").lower() == "openai-codex":
                external = external or Path(codex_auth_path(provider.get("auth_path"))).expanduser().is_file()
        for section in ("embeddings", "image", "media"):
            cfg = config.get(section) if isinstance(config.get(section), dict) else {}
            key = str(cfg.get("api_key_env") or ("KIE_API_KEY" if section == "media" and cfg.get("enabled") else "")).strip()
            if key:
                keys.append(key)
        for key in dict.fromkeys(keys):
            status = secret_status(key, config=config, service="providers")
            rows.append(f"providers · {key} · {'present' if status.present else 'missing'}" + (f" · {status.source}" if status.source else ""))
        if external:
            rows.append("providers · Codex OAuth · external")
        if not keys and not external:
            rows.append("providers · no credential-backed provider configured")

    if requested in {"all", "telegram"}:
        telegram = config.get("telegram") if isinstance(config.get("telegram"), dict) else {}
        key = str(telegram.get("bot_token_env") or "TELEGRAM_BOT_TOKEN")
        status = secret_status(key, config=config, service="telegram")
        rows.append(f"telegram · {key} · {'present' if status.present else 'missing'}" + (f" · {status.source}" if status.source else ""))

    if requested in {"all", "everywhere"}:
        from core.state.everywhere_readiness import build_everywhere_readiness

        credentials = build_everywhere_readiness(config, check_health=False).credentials
        rows.append(f"everywhere · primary device · {credentials.primary} · structured rotating credential")
        rows.append(f"everywhere · Live Control host · {credentials.live_host} · separate exact-scope credential")

    return "Credential status (values hidden):\n" + "\n".join(f"- {row}" for row in rows)


def execute_system_health(arguments: dict[str, Any]) -> str:
    """Return exact-scope, value-free evidence for MO self-diagnosis."""
    from core.state.paths import mo_home

    runtime = dict(arguments or {})
    agent = runtime.pop("_mo_agent", None)
    config = runtime.pop("_mo_config", None) or {}
    state_root = runtime.pop("_mo_runtime_home", None) or mo_home(config)
    project_root = runtime.pop("_mo_project_cwd", None) or os.getcwd()
    scope = str(runtime.pop("scope", "runtime") or "runtime").strip().lower()
    if scope not in {"runtime", "personalization"}:
        return "Blocked: system_health scope must be runtime or personalization."
    if scope == "personalization":
        from core.diagnostics.personalization import build_personalization_report, render_personalization_json

        return render_personalization_json(build_personalization_report(
            state_root=state_root,
            project_root=project_root,
        ))

    from core.diagnostics.doctor import build_doctor_report
    from core.diagnostics.system_health import check_graph_health
    from core.runtime.backend_monitor import active_monitor_path, current_monitor_context, economy_summary

    doctor = build_doctor_report(
        home=state_root,
        project_path=project_root,
        config=config,
    )
    checks = {check.name: check.status for check in doctor.checks}
    provider_check = next(
        (check for check in doctor.checks if check.name == "providers"),
        None,
    )
    graph = check_graph_health(str(project_root))
    monitor_path = active_monitor_path()
    activity = economy_summary(monitor_path) if monitor_path is not None else {}
    turn_id = str(current_monitor_context().get("turn_id") or "").strip()
    current_turn_usage = {
        "available": monitor_path is not None and bool(turn_id),
        "scope": (
            "current turn so far; provider tokens include recorded response receipts only; "
            "unrecorded usage and this tool's unfinished result are excluded"
        ),
    }
    if current_turn_usage["available"]:
        turn_activity = economy_summary(monitor_path, turn_ids={turn_id})
        current_turn_usage["turn_id"] = turn_id
        current_turn_usage.update({
            key: int(turn_activity.get(key) or 0)
            for key in (
                "provider_requests", "provider_responses", "provider_errors",
                "input_tokens", "output_tokens", "total_tokens",
                "cache_hit_tokens", "cache_miss_tokens", "cache_write_tokens",
                "tool_calls", "tool_errors", "sandbox_blocked",
            )
        })
    else:
        current_turn_usage["reason"] = "no exact active monitor or current turn identity"

    payload = {
        "scope": "exact active Gateway monitor plus offline local diagnostics",
        "doctor": {
            "worst": doctor.worst,
            "checks": checks,
        },
        "provider_configuration": {
            "status": str(getattr(provider_check, "status", "unknown") or "unknown"),
            "configured": len(config.get("providers") or []),
            "live_connectivity_tested": False,
        },
        "active_monitor": {
            "available": monitor_path is not None,
            "provider_requests": int(activity.get("provider_requests") or 0),
            "provider_responses": int(activity.get("provider_responses") or 0),
            "provider_errors": int(activity.get("provider_errors") or 0),
            "tool_calls": int(activity.get("tool_calls") or 0),
            "tool_errors": int(activity.get("tool_errors") or 0),
            "sandbox_blocked": int(activity.get("sandbox_blocked") or 0),
        },
        "current_turn_usage": current_turn_usage,
        "current_work": _current_work_status(agent),
        "structural_graph": {
            "available": bool(graph.get("exists")),
            "nodes": int(graph.get("nodes") or 0),
            "edges": int(graph.get("edges") or 0),
            "communities": int(graph.get("communities") or 0),
            "stale": bool(graph.get("stale")),
        },
        "not_verified": [
            "live connectivity of every configured provider",
            "test-suite health",
            "ownership of every operating-system process",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _current_work_status(agent: Any) -> dict[str, Any]:
    """Return bounded canonical Goal, worker, and taskboard status."""
    if agent is None:
        return {
            "available": False,
            "reason": "no active agent runtime",
            "goal": [],
            "workers": [],
            "taskboard": None,
            "taskboard_source": None,
        }

    from core.context.coordination_state import goal_summary_lines, worker_summary_lines
    from core.runtime.backend_monitor import redact_monitor_text

    goal = goal_summary_lines(agent, limit=8, include_evidence=False)
    workers = worker_summary_lines(agent, limit=6)
    goal_board = (
        getattr(agent, "_goal_task_board", None)
        if getattr(agent, "_goal_plan", None) is not None
        else None
    )
    foreground_board = getattr(agent, "_active_task_board", None)
    board = foreground_board or goal_board
    taskboard_source = (
        "foreground" if foreground_board is not None
        else "goal" if goal_board is not None
        else None
    )
    taskboard = None
    if board is not None and hasattr(board, "summary"):
        try:
            summary = board.summary()
            task_id = str(
                summary.get("active_task_id")
                or summary.get("ready_task_id")
                or ""
            )
            task = board.task(task_id) if task_id else None
            taskboard = {
                "state": str(summary.get("state") or "active"),
                "done": int(summary.get("done") or 0),
                "total": int(summary.get("total") or 0),
                "open": int(summary.get("open") or 0),
                "current_task_id": task_id,
                "current_task": redact_monitor_text(
                    getattr(task, "title", "") if task is not None else "",
                    180,
                ),
            }
        except Exception:
            taskboard = {"state": "unavailable"}
    return {
        "available": True,
        "goal": goal,
        "workers": workers,
        "taskboard": taskboard,
        "taskboard_source": taskboard_source,
    }


def execute_everywhere_readiness(arguments: dict[str, Any]) -> str:
    """Return the canonical read-only Everywhere renderer for model self-knowledge."""
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    view = str(runtime.get("view") or "status").strip().lower()
    if view == "phones":
        import sqlite3
        from core.state.everywhere_readiness import everywhere_authority
        from mo_everywhere.client import ContinuityClient, EverywhereClientError
        from mo_everywhere.registry import DeviceRegistry

        try:
            devices = (DeviceRegistry(config, read_only=True).list_android_devices()
                       if everywhere_authority(config).registry_admin_allowed
                       else ContinuityClient(config, timeout=4).android_devices())
        except (EverywhereClientError, OSError, sqlite3.Error) as exc:
            return f"Error: paired phone inventory unavailable: {exc}"
        return json.dumps({"paired_android_count": len(devices), "devices": devices,
                           "routing": "Phone tools address only the authenticated phone originating this turn. Pairing/grants do not establish a live host or Android permissions."})
    from core.state.everywhere_setup import render_everywhere_status, render_setup_plan

    return render_setup_plan(config) if view == "setup" else render_everywhere_status(config)


def execute_everywhere_pair_android(arguments: dict[str, Any]) -> str:
    """Show one server-issued, short-lived Android pairing QR on MO Desktop."""
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    surface = str(runtime.pop("_mo_surface", "") or "").strip().lower().replace("-", "_")
    if surface not in {"mo_desktop", "companion"}:
        return (
            "Blocked: Android pairing QR presentation is available only in MO Desktop. "
            "On the serving hub terminal, use /everywhere pair android."
        )
    phone_control = runtime.get("phone_control", False)
    if not isinstance(phone_control, bool):
        return "Blocked: phone_control must be true or false."
    try:
        from core.visualize.operator_visual import emit_operator_ephemeral_image
        from mo_everywhere.client import EverywhereClientError
        from mo_everywhere.pairing_qr import PairingQrError, create_android_pairing_image

        target, expiry = create_android_pairing_image(config, phone_control=phone_control)
        minutes = max(1, int((expiry - time.time() + 59) // 60))
        authority = "full phone-control" if phone_control else "companion"
        return emit_operator_ephemeral_image(
            str(target),
            label=f"One-use Android {authority} pairing QR; expires in about {minutes} minute(s)",
        )
    except EverywhereClientError as exc:
        if "(403)" in str(exc):
            return (
                "Blocked: this Desktop is not paired to the serving hub as an "
                "Everywhere coordinator; no pairing grant was created."
            )
        return f"Blocked: {exc}"
    except (PairingQrError, OSError, TypeError, ValueError) as exc:
        return f"Blocked: {exc}"


def execute_file_transfer(arguments: dict[str, Any]) -> str:
    action = str((arguments or {}).get("action") or "list").strip().lower()
    if action == "send":
        return _execute_file_transfer_send(arguments)
    return _execute_file_transfer_control(arguments, action=action)


def _execute_file_transfer_send(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    surface = str(runtime.pop("_mo_surface", "") or "agent").strip().lower()
    allowed_roots = runtime.pop("_mo_allowed_roots", None)
    target_text = str(runtime.get("target") or "").strip()
    path_text = str(runtime.get("path") or "").strip()
    path_hint = str(runtime.get("destination_path") or "").strip()
    if not path_text or not target_text:
        return "Blocked: file_transfer send requires path and target."
    from core.transfer import TransferOutbox, safe_transfer_error
    from core.transfer.addressing import resolve_transfer_target, transfer_targets

    targets, hub_local = transfer_targets(config)
    if not targets:
        return (
            "Blocked: file transfer is disabled or Everywhere authority is unavailable."
        )
    if path_hint and not Path(path_hint).expanduser().is_absolute():
        return "Blocked: destination_path must be absolute."
    try:
        path = Path(path_text).expanduser().resolve(strict=True)
        from core.tooling.sandbox import path_allowed

        if not path_allowed(path, allowed_roots):
            return "Blocked: file transfer source is outside the approved project roots."
        target = resolve_transfer_target(target_text, targets)
        record = TransferOutbox(config).send_now(
            path,
            target_device_id=target["device_id"],
            target_label=target["label"],
            source_surface=surface,
            hub_local=hub_local,
            destination="named_path" if path_hint else "catalog",
            path_hint=path_hint,
        )
        state = (
            "hub accepted"
            if record.state == "done"
            else f"queued for retry: {record.error}"
        )
    except Exception as exc:
        return f"Blocked: file transfer failed: {safe_transfer_error(exc)[:180]}"
    return (
        f"File transfer: {path.name} -> {target['label']} "
        f"({state or 'offered'})."
    )


def _execute_file_transfer_control(arguments: dict[str, Any], *, action: str) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    allowed_roots = runtime.pop("_mo_allowed_roots", None)
    direction = str(runtime.get("direction") or "all").strip().lower()
    transfer_id = str(runtime.get("transfer_id") or "").strip()
    destination_path = str(runtime.get("destination_path") or "").strip() or None
    if action not in {"list", "accept", "cancel", "retry"}:
        return "Blocked: transfer action must be send, list, accept, cancel, or retry."
    if direction not in {"all", "incoming", "outgoing"}:
        return "Blocked: transfer direction must be all, incoming, or outgoing."
    if action != "list" and not transfer_id:
        return f"Blocked: {action} requires an opaque transfer ID."
    if destination_path and not Path(destination_path).expanduser().is_absolute():
        return "Blocked: destination_path must be absolute."
    try:
        limit = max(1, min(100, int(runtime.get("limit") or 20)))
    except (TypeError, ValueError):
        return "Blocked: transfer limit must be an integer."
    try:
        from core.transfer import (
            TransferOutbox,
            TransferService,
            safe_transfer_error,
        )
        from core.transfer.addressing import transfer_targets

        targets, hub_local = transfer_targets(config)
        if not targets:
            return "File transfer is disabled or Everywhere authority is unavailable."
        outbox = TransferOutbox(config)
        if action != "list":
            try:
                local = outbox.get(transfer_id)
            except Exception:
                local = None
            if action == "retry":
                if local is None:
                    return "Blocked: only a sender-custody outbox ID can be retried."
                result: Any = outbox.retry(transfer_id)
            elif action == "accept":
                if local is not None:
                    return "Blocked: sender-custody outbox IDs cannot be accepted."
                if hub_local:
                    result = TransferService(config).receive_local(
                        transfer_id,
                        "hub",
                        destination_path=destination_path,
                        allowed_roots=allowed_roots,
                    )
                else:
                    from mo_everywhere.client import TransferClient

                    result = TransferClient(config).receive_one(
                        transfer_id,
                        destination_path=destination_path,
                        allowed_roots=allowed_roots,
                    )
            elif local is not None:
                result = outbox.cancel(transfer_id)
            elif hub_local:
                result = TransferService(config).cancel(transfer_id, "hub")
            else:
                from mo_everywhere.client import TransferClient

                result = TransferClient(config).cancel(transfer_id)
            state = (
                result.state
                if hasattr(result, "state")
                else str(result.get("state") or action)
            )
            return f"Transfer {transfer_id} is {state}."
        pending = (
            []
            if direction == "incoming"
            else [
                item.public()
                for item in outbox.list_pending(limit=limit)
            ]
        )
        if hub_local:
            records = [
                item.public()
                for item in TransferService(config).list_for(
                    "hub",
                    direction=direction,
                    purposes=("cargo",),
                    limit=limit,
                )
            ]
        else:
            from mo_everywhere.client import TransferClient

            records = TransferClient(config).transfers(
                direction=direction, limit=limit
            )
        records = (pending + records)[:limit]
    except Exception as exc:
        from core.transfer import safe_transfer_error

        return f"Blocked: transfer status unavailable: {safe_transfer_error(exc)[:180]}"
    if not records:
        return "No recent file transfers."
    lines = ["Recent file transfers:"]
    for item in records:
        row_id = str(item.get("outbox_id") or item.get("transfer_id") or "?")
        lines.append(
            f"- {row_id} · {item.get('name') or 'file'} · "
            f"{item.get('state') or '?'} · "
            f"{int(float(item.get('progress') or 0) * 100)}%"
            + (
                " · named path needs an explicit destination"
                if item.get("destination") == "named_path"
                else ""
            )
        )
    return "\n".join(lines)


def execute_migrate(arguments: dict[str, Any]) -> str:
    """Inspect, plan, or apply a peer-agent migration through one explicit action."""
    action = str(arguments.get("action") or "").strip().lower()
    source = str(arguments.get("source") or "").strip()
    path = str(arguments.get("path") or "").strip() or None
    if action not in {"inspect", "plan", "apply"}:
        return "Error: migrate needs an action (inspect | plan | apply)."
    if not source:
        return "Error: migrate needs a source (for example openclaw or hermes)."
    try:
        if action == "inspect":
            from core.migrate import inspect_source, render_inspect

            return render_inspect(inspect_source(source, home_override=path))
        project = str(arguments.get("_mo_project_cwd") or "").strip() or None
        if action == "plan":
            from core.migrate import build_coverage, render_coverage

            return render_coverage(
                build_coverage(source, home_override=path, project_cwd=project)
            )
        asset = str(arguments.get("asset") or "").strip()
        if not asset:
            return "Error: migrate apply needs an asset (profile | rules | skills | provider | all)."
        from core.migrate import apply_asset, render_apply

        return render_apply(
            apply_asset(source, asset, home_override=path, project_cwd=project)
        )
    except Exception as exc:
        return f"Error: migrate {action} failed: {type(exc).__name__}: {exc}"


def execute_show_image(arguments: dict[str, Any]) -> str:
    """Show an image FILE on the operator's active surface.

    Emits the operator-image marker carrying the file path — the SAME channel
    generate_image/edit_image use — so every surface renders it natively:
    terminal inline ANSI (captioned [Image #N]), MO Desktop in its cube panel,
    Telegram as a photo. (Previously terminal-only via the ANSI channel.) The
    (text-only) model gets only a confirmation; the marker + path are stripped
    before it sees the result — it never needs the pixels, only the operator does.
    """
    from core.visualize.terminal_image import available, image_dimensions
    from core.visualize.operator_visual import emit_operator_image

    path = str(arguments.get("path") or "").strip()
    if not path:
        return "Error: show_image needs a 'path'."
    p = Path(path)
    if not p.exists() or not p.is_file():
        return f"Error: image not found: {path}"
    if not available():
        return "Error: image rendering needs Pillow (it ships with MO computer-use: pip install Pillow)."
    dims = image_dimensions(str(p))
    if not dims:
        return f"Error: not a readable image: {path}"
    w, h = dims
    return emit_operator_image(str(p), label=f"{p.name} ({w}x{h})")


def execute_generate_image(arguments: dict[str, Any]) -> str:
    """Generate an image from a prompt, save it under MO state, and show it.

    Backend selection lives in core.imagegen (Codex quota or an image API key);
    output is written through resolve_state_path so it never lands in the
    checkout. On success the saved file is rendered to the operator by default,
    reusing execute_show_image — no second render stack.
    """
    import hashlib
    import threading
    import time

    from core import imagegen
    from core.provider.provider import load_config
    from core.provider.provider_audit import append_provider_audit
    from core.state.paths import MEDIA_GENERATED_DIR, resolve_state_path

    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return "Error: generate_image needs a 'prompt' describing the image."
    size = str(arguments.get("size") or "1024x1024").strip() or "1024x1024"
    show = arguments.get("show")
    show = True if show is None else bool(show)
    on_activity = arguments.get("_on_activity")
    cancel_event = arguments.get("_cancel_event")
    activity_interval = max(0.01, float(arguments.get("_activity_interval") or 1.0))

    def _cancelled() -> bool:
        try:
            return bool(cancel_event is not None and cancel_event.is_set())
        except Exception:
            return False

    def _emit(msg: str) -> None:
        if callable(on_activity):
            try:
                on_activity(msg)
            except Exception:
                pass

    supplied_config = arguments.get("_mo_config")
    config = supplied_config if isinstance(supplied_config, dict) else load_config()
    if not imagegen.available(config):
        return ("Error: no image backend is configured. Enable one under `image:` in "
                "config — either backend: codex (uses a local Codex CLI login, no API "
                "key) or backend: openai_compatible with api_key_env set.")

    slug = f"{hashlib.sha1(prompt.encode('utf-8')).hexdigest()[:12]}-{uuid.uuid4().hex[:8]}"
    out_path = resolve_state_path(f"{MEDIA_GENERATED_DIR}/{slug}.png", config)
    backend_name = imagegen.backend_label(config)
    if _cancelled():
        return f"Error: image generation cancelled before starting ({backend_name})."

    # Generation is one blocking backend call (codex reasons for minutes, an API
    # call takes seconds) with no mid-progress signal — so run it on a worker and
    # emit an ELAPSED heartbeat (never a countdown: the duration is unknown). The
    # footer/desktop activity lane reads these `image gen:` lines live.
    box: dict[str, Any] = {}

    def _work() -> None:
        generate_kwargs = {"size": size, "config": config}
        if cancel_event is not None:
            generate_kwargs["cancel_event"] = cancel_event
        box["result"] = imagegen.generate(prompt, out_path, **generate_kwargs)

    _emit(f"image gen: starting ({backend_name}, {size})")
    worker = threading.Thread(target=_work, name="mo-image-gen", daemon=True)
    started = time.time()
    worker.start()
    while worker.is_alive():
        worker.join(timeout=activity_interval)
        if _cancelled():
            _emit(f"image gen: cancelled ({backend_name})")
            return f"Error: image generation cancelled ({backend_name})."
        if worker.is_alive():
            elapsed = int(time.time() - started)
            mins, secs = divmod(elapsed, 60)
            clock = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
            _emit(f"image gen: generating via {backend_name}… {clock}")
    if _cancelled():
        return f"Error: image generation cancelled ({backend_name})."
    result = box.get("result") or {
        "ok": False, "error": "generation produced no result", "backend": backend_name,
    }

    try:
        append_provider_audit(
            "image_generate",
            model=(result.get("backend") or imagegen.backend_label(config)),
            reason="generate_image",
            ok=bool(result.get("ok")),
        )
    except Exception:
        pass

    if not result.get("ok"):
        return f"Error: image generation failed ({result.get('backend')}): {result.get('error')}"

    saved = result.get("path") or out_path
    backend = result.get("backend") or imagegen.backend_label(config)
    normalized = f"; {result['normalized']}" if result.get("normalized") else ""
    # Model-facing text MUST carry the full save path (and backend) so MO can tell
    # the operator where the file is and which source made it — not just re-describe
    # the prompt.
    if show:
        # Emit the operator-image marker carrying the ORIGINAL png path; the active
        # surface renders it natively (terminal inline, MO Desktop in its cube
        # panel). One marker, surface-owned display — no terminal-only ANSI stash.
        # The label (which emit_operator_image wraps) carries the full path so MO
        # can tell the operator where the file is and which backend made it.
        from core.visualize.operator_visual import emit_operator_image
        return emit_operator_image(saved, label=f"Generated image at {saved} (backend: {backend}, size {size}){normalized}")
    return f"Generated image saved: {saved} (backend: {backend}, size {size}){normalized}"


def execute_edit_image(arguments: dict[str, Any]) -> str:
    """Transform an image file (resize/crop/rotate/flip/convert) with Pillow, save a
    NEW file, and show it on the active surface.

    The EDIT leg of MO's image model (read=perceive, make=generate_image,
    show=show_image). Reuses the operator-image channel so terminal + MO Desktop
    render the result natively; the (text-only) model gets only the saved path.
    """
    from core import imageedit
    from core.visualize.operator_visual import emit_operator_image

    if not imageedit.available():
        return "Error: image editing needs Pillow (it ships with MO computer-use: pip install Pillow)."
    path = str(arguments.get("path") or "").strip()
    if not path:
        return "Error: edit_image needs a 'path'."
    p = Path(path)
    if not p.exists() or not p.is_file():
        return f"Error: image not found: {path}"
    op = str(arguments.get("op") or "").strip().lower()
    out = arguments.get("out")
    out = str(out).strip() if out else None
    show = arguments.get("show")
    show = True if show is None else bool(show)

    try:
        if op == "resize":
            if arguments.get("scale") is None and arguments.get("width") is None and arguments.get("height") is None:
                return "Error: resize needs 'scale' (percent) or 'width'/'height'."
            saved, (w, h) = imageedit.resize(
                str(p), width=arguments.get("width"), height=arguments.get("height"),
                scale=arguments.get("scale"), out=out)
        elif op == "crop":
            for key in ("x", "y", "width", "height"):
                if arguments.get(key) is None:
                    return "Error: crop needs x, y, width, and height."
            saved, (w, h) = imageedit.crop(
                str(p), x=arguments["x"], y=arguments["y"],
                width=arguments["width"], height=arguments["height"], out=out)
        elif op == "rotate":
            if arguments.get("degrees") is None:
                return "Error: rotate needs 'degrees' (90, 180, or 270)."
            saved, (w, h) = imageedit.rotate(str(p), degrees=arguments["degrees"], out=out)
        elif op == "flip":
            if not arguments.get("direction"):
                return "Error: flip needs 'direction' (horizontal or vertical)."
            saved, (w, h) = imageedit.flip(str(p), direction=arguments["direction"], out=out)
        elif op == "convert":
            if not arguments.get("format"):
                return "Error: convert needs 'format' (png, jpg, webp, …)."
            saved, (w, h) = imageedit.convert(str(p), to_format=arguments["format"], out=out)
        else:
            return f"Error: unknown op '{op}'. Use resize | crop | rotate | flip | convert."
    except Exception as exc:
        return f"Error: edit_image {op} failed: {exc}"

    if show:
        return emit_operator_image(saved, label=f"{op}: {saved} ({w}x{h})")
    return f"Saved ({op}): {saved} ({w}x{h})"


_MERMAID_DECLARATIONS = frozenset({
    "architecture-beta",
    "block-beta",
    "classdiagram",
    "erdiagram",
    "flowchart",
    "gantt",
    "gitgraph",
    "graph",
    "journey",
    "kanban",
    "mindmap",
    "packet-beta",
    "pie",
    "quadrantchart",
    "radar-beta",
    "requirementdiagram",
    "sankey-beta",
    "sequencediagram",
    "statediagram",
    "statediagram-v2",
    "timeline",
    "xychart-beta",
})


def _raw_mermaid_document(content: object) -> str | None:
    """Return a normalized Mermaid fence when ``content`` is Mermaid DSL."""
    text = str(content or "").strip()
    if not text:
        return None
    if text.lower().startswith("```mermaid"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            return None
        body = "\n".join(lines[1:-1]).strip()
    else:
        body = text
    first = next(
        (line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith("%%")),
        "",
    )
    declaration = first.split(maxsplit=1)[0].lower() if first else ""
    if declaration not in _MERMAID_DECLARATIONS:
        return None
    return f"```mermaid\n{body}\n```"


def execute_show_viz(arguments: dict[str, Any]) -> str:
    """Render structured content or data through one operator-visual tool."""
    from core.visualize import viz
    from core.visualize.operator_visual import emit_ansi

    kind = str(arguments.get("kind") or "").strip().lower()
    title = str(arguments.get("title") or "")
    unit = str(arguments.get("unit") or "")
    data = arguments.get("data")
    try:
        if kind in {"tree", "outline", "mermaid"}:
            content = arguments.get("content")
            if not content:
                return f"Error: show_viz {kind} needs 'content'."
            if kind == "mermaid":
                raw_mermaid = _raw_mermaid_document(content)
                if raw_mermaid is not None:
                    return raw_mermaid
            from core.visualize import visualize

            content_kind = str(arguments.get("content_kind") or "").strip().lower()
            if not content_kind:
                content_kind = "markdown" if kind == "outline" else "auto"
            fmt = "mermaid" if kind == "mermaid" else "ascii"
            diagram = visualize(
                str(content),
                kind=content_kind,
                format=fmt,
                title=title,
                allow_fs=False,
            )
            if diagram.startswith("visualize error"):
                return diagram
            if fmt == "mermaid":
                return diagram
            from rich.text import Text

            rendered = viz.render_ansi(Text(diagram, style=viz.series_palette()[0]))
            return emit_ansi(rendered, label=(title or "structure"))
        if kind in ("bar", "barchart", "bar_chart", "chart"):
            if not isinstance(data, dict) or not data:
                return "Error: show_viz bar needs 'data' as {label: number}."
            ansi = viz.bar_chart({str(k): float(v) for k, v in data.items()}, title=title, unit=unit)
        elif kind in ("table", "kv"):
            if not isinstance(data, dict) or not data:
                return "Error: show_viz table needs 'data' as {key: value}."
            ansi = viz.kv_table(data, title=title)
        elif kind in ("sparkline", "spark"):
            values = arguments.get("values")
            if not values and isinstance(data, dict):
                values = list(data.values())
            if not values:
                return "Error: show_viz sparkline needs 'values' as a list of numbers."
            ansi = viz.sparkline([float(v) for v in values], label=title)
        elif kind == "panel":
            body = str(arguments.get("text") or "")
            if not body:
                return "Error: show_viz panel needs 'text'."
            ansi = viz.panel(body, title=title)
        elif kind in ("progress", "ratio", "coverage", "gauge"):
            if not isinstance(data, dict) or not data:
                return ("Error: show_viz progress needs 'data' as {label: fraction} "
                        "(0..1 or 0..100) or {label: [value, total]}.")
            ansi = viz.progress(data, title=title)
        elif kind in ("compare", "delta", "beforeafter", "before_after"):
            if not isinstance(data, dict) or not data:
                return "Error: show_viz compare needs 'data' as {label: [before, after]}."
            ansi = viz.compare(data, title=title, unit=unit)
        else:
            return (f"Error: unknown show_viz kind '{kind}' "
                    "(use tree, outline, mermaid, bar, table, sparkline, panel, progress, or compare).")
    except Exception as exc:
        return f"Error rendering show_viz: {type(exc).__name__}: {exc}"
    return emit_ansi(ansi, label=(title or f"{kind} visual"))


_PERCEIVE_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
)
_PERCEIVE_TEXT_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".rst", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".csv", ".log", ".html", ".css", ".sh", ".c",
        ".h", ".cpp", ".go", ".rs", ".java", ".xml", ".sql",
    }
)
_MAX_PERCEIVE_IMAGE_BYTES = 40 * 1024 * 1024
_MAX_PERCEIVE_PDF_BYTES = 100 * 1024 * 1024


def execute_perceive(arguments: dict[str, Any]) -> str:
    """Give the model a local image or bounded embedded PDF text."""
    from .screen import SCREEN_IMAGE_MARKER

    source = str(arguments.get("source") or "").strip()
    if not source:
        return "Error: perceive needs a local image or PDF path."

    if not os.path.isfile(source):
        return f"Error: perceive source not found: {source}"
    try:
        size_bytes = os.path.getsize(source)
    except OSError as exc:
        return f"Error: perceive could not read source metadata ({type(exc).__name__})."

    suffix = os.path.splitext(source)[1].lower()
    if suffix in _PERCEIVE_TEXT_SUFFIXES:
        return f"perceive does not read text/code files; use read_file for '{source}'."

    if suffix in _PERCEIVE_IMAGE_SUFFIXES:
        if size_bytes > _MAX_PERCEIVE_IMAGE_BYTES:
            return "Error: image exceeds the 40 MiB perceive limit."
        question = str(arguments.get("question") or "").strip()
        head = f"[perceived image {os.path.basename(source)}]"
        if question:
            head += f" question: {question}"
        return f"{head}\n{SCREEN_IMAGE_MARKER}:{source}"

    if suffix == ".pdf":
        if size_bytes > _MAX_PERCEIVE_PDF_BYTES:
            return "Error: PDF exceeds the 100 MiB perceive limit."
        from core.perception.adapters import documents as _docs

        if not _docs.available():
            return (
                "Error: embedded PDF text extraction requires the optional pypdf "
                "dependency (pip install -r requirements-perception.txt)."
            )
        try:
            return _docs.extract_pdf_text(source, pages=arguments.get("pages"))
        except Exception as exc:  # noqa: BLE001
            return (
                f"Error: PDF extraction failed for '{os.path.basename(source)}' "
                f"({type(exc).__name__})."
            )

    return (
        "Error: perceive supports local image files and PDFs with embedded text. "
        "Use computer_observe for live screens and read_file for text/code."
    )


def execute_schedule_job(arguments: dict[str, Any]) -> str:
    args = dict(arguments or {})
    agent = args.pop("_mo_agent", None)
    if agent is None:
        return "Error: schedule_job requires an active MO agent"
    action = str(args.get("action") or "list").strip().lower()
    surface = str(getattr(agent, "_provider_surface", lambda: "")() or "").lower()
    if surface == "scheduler" and action != "list":
        return "Error: scheduled turns cannot create or mutate scheduled tasks"
    try:
        from core.runtime.scheduler import format_scheduler_jobs, manage_scheduler_jobs

        return format_scheduler_jobs(manage_scheduler_jobs(agent, action, args))
    except (TypeError, ValueError, OSError) as exc:
        return f"Error: {exc}"


def execute_mail(arguments: dict[str, Any]) -> str:
    from core.mail.service import execute

    args = dict(arguments or {})
    agent = args.pop("_mo_agent", None)
    config = args.pop("_mo_config", None)
    return execute(args, agent=agent, config=config)


def execute_life_item(arguments: dict[str, Any]) -> str:
    from core.life.items import add_case_update, create_item, find_item_by_title, forget_item, list_items, summary, update_item

    args = dict(arguments or {})
    agent = args.pop("_mo_agent", None)
    if agent is None:
        return "Error: life_item requires an active MO agent"
    action = str(args.get("action") or "list").strip().lower()
    if action != "list" and str(getattr(agent, "_provider_surface", lambda: "")() or "").lower() == "scheduler":
        return "Error: scheduled turns cannot change life items"
    config = getattr(agent, "config", {})
    try:
        def target() -> tuple[str, int]:
            item_id = str(args.get("id") or "").strip()
            if item_id:
                return item_id, int(args.get("revision") or 0)
            item = find_item_by_title(args.get("match_title"), config=config)
            return str(item["id"]), int(item["revision"])

        if action == "list":
            items = list_items(config=config)
            return json.dumps({"summary": summary(config=config),
                "items": [{"id": item["id"], "revision": item["revision"],
                    "status": item["status"]} for item in items]}, ensure_ascii=False)
        if action == "show":
            item_id, _ = target()
            item = next((entry for entry in list_items(config=config) if entry["id"] == item_id), None)
            if item is None:
                raise ValueError("Life item not found")
            return json.dumps({"id": item["id"], "revision": item["revision"],
                "status": item["status"]}, ensure_ascii=False)
        if action == "create":
            item = create_item(title=args.get("title"), category=args.get("category"),
                due_date=args.get("due_date"), notes=args.get("notes"),
                case_area=args.get("case_area"), reference=args.get("reference"),
                expected_amount=args.get("expected_amount"), currency=args.get("currency"),
                frequency=args.get("frequency"), installments_total=args.get("installments_total"),
                config=config)
        elif action in {"update", "complete", "reopen"}:
            changes = {key: args[key] for key in ("title", "category", "due_date", "notes",
                "expected_amount", "currency", "frequency", "installments_total",
                "case_area", "reference") if key in args}
            if action in {"complete", "reopen"}:
                changes["status"] = "done" if action == "complete" else "open"
            item_id, revision = target()
            item = update_item(item_id, expected_revision=revision, changes=changes, config=config)
        elif action == "add_update":
            item_id, revision = target()
            item = add_case_update(item_id, expected_revision=revision,
                date_value=args.get("date"), kind=args.get("kind"),
                summary_text=args.get("summary"), reference=args.get("update_reference"),
                config=config)
        elif action == "forget":
            item_id, revision = target()
            forget_item(item_id, expected_revision=revision, config=config)
            return json.dumps({"state": "forgotten"})
        else:
            return "Error: Unknown life-item action"
        return json.dumps({"state": "saved", "id": item["id"], "revision": item["revision"]})
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


def execute_life_money(arguments: dict[str, Any]) -> str:
    from core.life.money import create_entry, forget_entry, list_entries, month_view, update_entry
    from core.life.items import find_item_by_title

    args = dict(arguments or {})
    agent = args.pop("_mo_agent", None)
    if agent is None:
        return "Error: life_money requires an active MO agent"
    action = str(args.get("action") or "list").strip().lower()
    if action != "list" and str(getattr(agent, "_provider_surface", lambda: "")() or "").lower() == "scheduler":
        return "Error: scheduled turns cannot change money entries"
    config = getattr(agent, "config", {})
    try:
        def life_link() -> str:
            title = args.get("match_life_title")
            item_id = str(args.get("life_item_id") or "").strip()
            if title and item_id:
                raise ValueError("Choose a Life item ID or exact title, not both")
            return str(find_item_by_title(title, config=config)["id"]) if title else item_id

        def target() -> tuple[str, int]:
            entry_id = str(args.get("id") or "").strip()
            if entry_id:
                return entry_id, int(args.get("revision") or 0)
            match_title = " ".join(str(args.get("match_title") or "").split()).casefold()
            if not match_title:
                raise ValueError("Give an exact money title or an entry ID and revision")
            matches = [entry for entry in list_entries(config=config)
                       if str(entry.get("title") or "").casefold() == match_title]
            if len(matches) != 1:
                raise ValueError("No unique money entry matched; review the connected Dashboard Life view")
            return str(matches[0]["id"]), int(matches[0]["revision"])

        if action == "list":
            month = str(args.get("month") or "")
            view = month_view(month=month, config=config)
            summary, entries = view["summary"], view["entries"]
            return json.dumps({"state": "listed", "month": summary["month"],
                "count": summary["count"], "entries": [
                    {"id": entry["id"], "revision": entry["revision"]} for entry in entries[:20]
                ]}, ensure_ascii=False)
        if action == "create":
            entry = create_entry(title=args.get("title"), kind=args.get("kind"),
                amount=args.get("amount"), currency=args.get("currency"),
                date=args.get("date"), category=args.get("category"),
                notes=args.get("notes"), life_item_id=life_link(), config=config)
        elif action == "update":
            changes = {key: args[key] for key in (
                "title", "kind", "amount", "currency", "date", "category", "notes", "life_item_id") if key in args}
            if "match_life_title" in args:
                changes["life_item_id"] = life_link()
            entry_id, revision = target()
            entry = update_entry(entry_id, expected_revision=revision, changes=changes, config=config)
        elif action == "forget":
            entry_id, revision = target()
            forget_entry(entry_id, expected_revision=revision, config=config)
            return json.dumps({"state": "forgotten"})
        else:
            return "Error: Unknown money-entry action"
        return json.dumps({"state": "saved", "id": entry["id"], "revision": entry["revision"]})
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


def execute_mo_design(arguments: dict[str, Any]) -> str:
    from .mo_design import execute_mo_design as _execute

    return _execute(arguments)


TOOL_EXECUTORS = {
    "mail": execute_mail,
    "life_item": execute_life_item,
    "life_money": execute_life_money,
    "mo_design": execute_mo_design,
    "show_image": execute_show_image,
    "generate_image": execute_generate_image,
    "edit_image": execute_edit_image,
    "show_viz": execute_show_viz,
    "tool_search": execute_tool_search,
    "system_health": execute_system_health,
    "credential_status": execute_credential_status,
    "media": execute_media,
    "everywhere_readiness": execute_everywhere_readiness,
    "everywhere_pair_android": execute_everywhere_pair_android,
    "file_transfer": execute_file_transfer,
    "record_profile_fact": execute_record_profile_fact,
    "migrate": execute_migrate,
    "perceive": execute_perceive,
    "schedule_job": execute_schedule_job,
    "desktop_sync": execute_desktop_sync,
    "point_on_screen": execute_point_on_screen,
    "computer_targets": execute_computer_targets,
    "computer_observe": execute_computer_observe,
    "computer_act": execute_computer_act,
    "phone_context": execute_phone_context,
    "phone_click": execute_phone_click,
    "phone_set_text": execute_phone_set_text,
    "phone_scroll": execute_phone_scroll,
    "phone_key": execute_phone_key,
    "phone_files": execute_phone_files,
    "phone_storage_report": execute_phone_storage_report,
    "phone_file_read": execute_phone_file_read,
    "phone_file_delete": execute_phone_file_delete,
    "phone_capabilities": execute_phone_capabilities,
    "phone_system_status": execute_phone_system_status,
    "phone_cache_report": execute_phone_cache_report,
    "phone_cache_trim": execute_phone_cache_trim,
    "phone_packages": execute_phone_packages,
    "phone_package_action": execute_phone_package_action,
    "phone_shell": execute_phone_shell,
    "systemcare_status": execute_systemcare_status,
    "systemcare_inspect": execute_systemcare_inspect,
    "systemcare_calibrate": execute_systemcare_calibrate,
    "systemcare_scan": execute_systemcare_scan,
    "systemcare_plan": execute_systemcare_plan,
    "systemcare_apply": execute_systemcare_apply,
    "systemcare_rollback": execute_systemcare_rollback,
    "systemcare_cancel": execute_systemcare_cancel,
    "read_file": execute_read_file,
    "write_file": execute_write_file,
    "edit_file": execute_edit_file,
    "shell": execute_shell,
    "find_files": execute_find_files,
    "grep": execute_grep,
    "git_status": execute_git_status,
    "test_runner": execute_test_runner,
    "project_bridge": execute_project_bridge,
    "map_project": execute_map_project,
    "role_work": execute_role_work,
    "web_fetch": execute_web_fetch,
    "web_search": execute_web_search,
    "inspect_repo": execute_inspect_repo,
    "use_repo": execute_use_repo,
    "code_search": execute_code_search,
    "project_history": execute_project_history,
    "find_callers": execute_find_callers,
    "find_callees": execute_find_callees,
    "redundancy_scan": execute_redundancy_scan,
    "graph_explain": execute_graph_explain,
    "graph_neighbors": execute_graph_neighbors,
    "graph_path": execute_graph_path,
    "graph_stats": execute_graph_stats,
    "build_graph": execute_build_graph,
    "record_convention": execute_record_convention,
}
