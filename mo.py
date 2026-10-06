#!/usr/bin/env python3
# ruff: noqa: E402
"""MO — Provider-first local agent runtime."""

from __future__ import annotations

import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from core.runtime._bootstrap import bootstrap

AGENT_ROOT = bootstrap(__file__, invoked_as="mo")
CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()

Console = None
HAS_RICH = False
create_agent = None
Gateway = None
initialize_mo = None
render_init_report = None
default_config_path = None


class ConfigLoadError(Exception):
    def __init__(self, message: str, path: str = ""):
        super().__init__(message)
        self.message = message
        self.path = path


class ProviderError(Exception):
    pass


def clean_provider_error(message: str) -> str:
    return str(message)


def _load_rich_console():
    global Console, HAS_RICH
    if Console is not None or HAS_RICH:
        return Console, HAS_RICH
    try:
        from rich.console import Console as loaded_console
        Console = loaded_console
        HAS_RICH = True
    except ImportError:
        Console = None
        HAS_RICH = False
    return Console, HAS_RICH


def _load_agent_runtime():
    global create_agent, Gateway
    if create_agent is None:
        from core.agent.agent import create_agent as loaded_create_agent
        create_agent = loaded_create_agent
    if Gateway is None:
        from core.gateway import Gateway as loaded_gateway
        Gateway = loaded_gateway
    return create_agent, Gateway


def _load_initializer():
    global initialize_mo, render_init_report
    if initialize_mo is None or render_init_report is None:
        from core.state.initializer import initialize_mo as loaded_initialize_mo, render_init_report as loaded_render_init_report
        initialize_mo = loaded_initialize_mo
        render_init_report = loaded_render_init_report
    return initialize_mo, render_init_report


def _default_config_path(*, agent_root: str, caller_cwd: str) -> str:
    global default_config_path
    if default_config_path is None:
        from core.state.paths import default_config_path as loaded_default_config_path
        default_config_path = loaded_default_config_path
    return default_config_path(agent_root=agent_root, caller_cwd=caller_cwd)


def _load_provider_errors():
    global ConfigLoadError, ProviderError, clean_provider_error
    from core.provider.provider import (
        ConfigLoadError as loaded_config_error,
        ProviderError as loaded_provider_error,
        clean_provider_error as loaded_clean_provider_error,
    )
    ConfigLoadError = loaded_config_error
    ProviderError = loaded_provider_error
    clean_provider_error = loaded_clean_provider_error
    return ConfigLoadError, ProviderError, clean_provider_error


# MO Desktop launch helpers live in mo_desktop.desktop_launch. The
# package init is intentionally light, so these lazy wrappers do not import the
# companion GUI/tray/voice stack during terminal startup.
def _mo_desktop_config_block(config) -> dict:
    from mo_desktop.desktop_launch import mo_desktop_config_block
    return mo_desktop_config_block(config)


def _terminal_should_register_mo_desktop_launcher(agent) -> bool:
    block = _mo_desktop_config_block(getattr(agent, "config", None))
    if not block.get("enabled", False):
        return False
    return block.get("hotkey_launcher", True) is not False


def _mo_desktop_running() -> bool:
    from mo_desktop.desktop_launch import mo_desktop_running
    return mo_desktop_running()


def _launch_mo_desktop_detached() -> None:
    from mo_desktop.desktop_launch import launch_mo_desktop_detached
    launch_mo_desktop_detached()  # no-arg → caller (launcher) already gated on enabled


def _start_mo_desktop_hotkey_launcher_if_enabled(agent):
    """Register a light Win+Alt+M launcher without importing MO Desktop."""
    if not _terminal_should_register_mo_desktop_launcher(agent):
        return None
    # A resident Desktop process owns the summon hotkey itself. Registering it
    # again in every terminal wastes startup time and creates duplicate global
    # keyboard hooks without adding any behavior.
    if _mo_desktop_running():
        return None
    try:
        from core.runtime.lock import acquire_runtime_lock, release_runtime_lock
        resource_lock = acquire_runtime_lock(
            lock_name="mo-desktop-hotkey-launcher.lock",
            label="MO Desktop terminal hotkey launcher",
            quiet=True,
        )
    except Exception:
        return None
    if resource_lock is None:
        return None
    try:
        import keyboard
    except ImportError:
        release_runtime_lock(resource_lock)
        return None

    def _on_hotkey() -> None:
        if _mo_desktop_running():
            return
        try:
            _launch_mo_desktop_detached()
        except Exception:
            return

    try:
        handle = keyboard.add_hotkey("win+alt+m", _on_hotkey)
        return handle, resource_lock
    except Exception:
        release_runtime_lock(resource_lock)
        return None


def _stop_mo_desktop_hotkey_launcher(handle) -> None:
    if not handle:
        return
    keyboard_handle = handle
    resource_lock = None
    if isinstance(handle, tuple) and len(handle) == 2:
        keyboard_handle, resource_lock = handle
    try:
        import keyboard
        keyboard.remove_hotkey(keyboard_handle)
    except Exception:
        pass
    if resource_lock is not None:
        try:
            from core.runtime.lock import release_runtime_lock
            release_runtime_lock(resource_lock)
        except Exception:
            pass


def _prompt_arg(args: list[str]) -> str | None:
    """Return the value of a one-shot ``-p``/``--prompt`` flag, or None if absent.

    ``-p`` with no following value returns "" (an explicit usage error upstream),
    distinct from None (flag not given).
    """
    for flag in ("-p", "--prompt"):
        if flag in args:
            idx = args.index(flag)
            return args[idx + 1] if idx + 1 < len(args) else ""
    return None


def _prompt_file_arg(args: list[str]) -> str | None:
    """Return the value of ``--prompt-file``, or None if absent."""
    flag = "--prompt-file"
    if flag in args:
        idx = args.index(flag)
        return args[idx + 1] if idx + 1 < len(args) else ""
    return None


def _startup_goal_file_arg(args: list[str]) -> str | None:
    """Return the trusted visible-terminal goal file, or None when absent."""
    flag = "--startup-goal-file"
    if flag in args:
        idx = args.index(flag)
        return args[idx + 1] if idx + 1 < len(args) else ""
    return None


def _startup_panes_input(args: list[str]) -> str:
    """``--startup-panes N[:local|host]`` from a trusted launcher (MO Desktop) becomes the
    Terminal's first command, ``/workspace open N local|host``; "" when absent."""
    import re

    flag = "--startup-panes"
    if flag not in args:
        return ""
    index = args.index(flag)
    value = args[index + 1].strip().lower() if index + 1 < len(args) else ""
    match = re.fullmatch(r"([1-6])(?::(local|host))?", value)
    if not match:
        raise ValueError("startup panes must be 1-6, optionally :local or :host")
    return f"/workspace open {match.group(1)} {match.group(2) or 'local'}"


def _goal_prompt_file_arg(args: list[str]) -> str | None:
    """Return the trusted headless GoalRunner prompt file, or None when absent."""
    flag = "--goal-prompt-file"
    if flag in args:
        idx = args.index(flag)
        return args[idx + 1] if idx + 1 < len(args) else ""
    return None


def _runtime_lane_arg(args: list[str]) -> str:
    """Return the fixed internal lane accepted by trusted one-shot launchers."""
    flag = "--runtime-lane"
    if flag not in args:
        return ""
    try:
        value = args[args.index(flag) + 1].strip()
    except (IndexError, AttributeError):
        raise ValueError("runtime lane value is missing") from None
    if value != "mo-design":
        raise ValueError("unsupported runtime lane")
    return value


def _config_arg(args: list[str]) -> str:
    """Return an explicit config path supplied by a trusted launcher."""
    flag = "--config"
    if flag not in args:
        return ""
    try:
        value = args[args.index(flag) + 1].strip()
    except (IndexError, AttributeError):
        raise ValueError("config path is missing") from None
    if not value:
        raise ValueError("config path is missing")
    return value


def _design_completion_id() -> str:
    """Return the fixed private completion id supplied by MO Desktop."""
    value = os.environ.get("MO_DESIGN_COMPLETION_ID", "").strip().lower()
    if not value:
        return ""
    if len(value) != 24 or not all(char in "0123456789abcdef" for char in value):
        raise ValueError("MO Design completion id is invalid")
    return value


def _write_design_completion(completion_id: str, state: str) -> bool:
    """Publish one small private marker for the resident Desktop broker."""
    if not completion_id:
        return False
    try:
        from core.state.paths import MO_DESIGN_HANDOFF_DIR, resolve_state_path
        from core.utils.atomic_write import atomic_write_json

        requested_dir = os.environ.get("MO_DESIGN_COMPLETION_DIR", "").strip()
        completion_dir = Path(resolve_state_path(
            requested_dir or f"{MO_DESIGN_HANDOFF_DIR}/completions",
        ))
        target = completion_dir / f"{completion_id}.json"
        atomic_write_json(
            target,
            {"state": state, "completed_at": time.time()},
            indent=None,
            ensure_ascii=False,
        )
        return True
    except OSError:
        return False


def _portable_startup_args(args: list[str]) -> tuple[str, int] | None:
    """Read the fixed portable-conversation startup pair used by trusted launchers."""
    id_flag = "--portable-conversation"
    revision_flag = "--portable-revision"
    if id_flag not in args and revision_flag not in args:
        return None
    if id_flag not in args or revision_flag not in args:
        raise ValueError("portable conversation and revision must be supplied together")
    try:
        conversation_id = args[args.index(id_flag) + 1].strip()
        raw_revision = args[args.index(revision_flag) + 1].strip()
    except (IndexError, AttributeError):
        raise ValueError("portable conversation startup value is missing") from None
    if not conversation_id:
        raise ValueError("portable conversation startup value is missing")
    try:
        revision = int(raw_revision)
    except ValueError:
        raise ValueError("portable conversation revision must be positive") from None
    if revision < 1:
        raise ValueError("portable conversation revision must be positive")
    return conversation_id, revision


def _handoff_ready_id(args: list[str], target: tuple[str, int] | None) -> str:
    flag = "--handoff-ready-id"
    if flag not in args:
        return ""
    if target is None:
        raise ValueError("handoff readiness requires a portable conversation")
    try:
        value = args[args.index(flag) + 1].strip()
    except (IndexError, AttributeError):
        raise ValueError("handoff readiness id is missing") from None
    if not value or len(value) > 64 or not all(char.isalnum() or char in "-_" for char in value):
        raise ValueError("handoff readiness id is invalid")
    return value


def _bind_startup_portable(
    agent,
    target: tuple[str, int] | None,
    *,
    ready_id: str = "",
) -> None:
    if target is None:
        return
    manager = getattr(agent, "_sessions", None)
    if manager is None:
        raise ValueError("portable conversations are unavailable")
    conversation_id, revision = target
    try:
        name, _data = manager.load_portable(
            conversation_id,
            expected_revision=revision,
        )
    except Exception as exc:
        from core.session.sessions import PortableConversationError

        if isinstance(exc, PortableConversationError):
            raise ValueError(str(exc)) from None
        raise
    manager.switch(name, agent.session, save_current=False)
    agent._pristine_new_session = None
    restore = getattr(agent, "_restore_loaded_session_meta", None)
    if callable(restore):
        restore()
    if ready_id:
        if ready_id != str(getattr(agent, "instance_id", "") or ""):
            raise ValueError("handoff readiness id does not match this MO instance")
        from core.state.paths import resolve_state_path

        ready_path = Path(resolve_state_path(
            f"run/terminal-handoffs/{ready_id}.ready",
            getattr(agent, "config", None),
        ))
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path = ready_path.with_suffix(".pending")
        pending_path.write_text("ready\n", encoding="utf-8")
        os.replace(pending_path, ready_path)


def _read_prompt_file(path: str) -> str:
    prompt_path = Path(path).expanduser()
    return prompt_path.read_text(encoding="utf-8", errors="replace")


def _run_one_shot(prompt: str, config_path: str, runtime_lane: str = "") -> str:
    """Run ONE non-interactive turn in-process and return its final text.

    Builds the agent, dispatches native slash commands locally, or runs one provider
    turn for normal chat. Used by `mo -p`/`--prompt` for scripting and piping;
    stdout carries only the final result.
    """
    from core.agent.slash_result import command_failure, command_result as coerce_slash_result

    agent_factory, gateway_cls = _load_agent_runtime()
    agent = agent_factory(config_path)
    # Native commands that normally hand work to daemon threads must complete in
    # this process: a one-shot exits as soon as this function returns.
    agent._noninteractive = True
    gateway = gateway_cls(agent)

    def run() -> str:
        if prompt.strip().startswith("/"):
            try:
                raw_result = agent.process_slash_command(prompt)
            except Exception as exc:
                raw_result = command_failure(prompt, exc)
            if raw_result is None:
                return f"Unknown command: {prompt.strip().split()[0]}"
            result = coerce_slash_result(raw_result)
            if result.action == "exit":
                return ""
            if result.action in {"retry", "run_turn"}:
                attr = "_retry_pending_input" if result.action == "retry" else "_slash_pending_input"
                pending = str(getattr(agent, attr, "") or "").strip()
                setattr(agent, attr, "")
                return gateway.run_turn(pending, route_source="user") if pending else "Nothing to run."
            if result.action in {"goal_start", "goal_continue", "terminal"}:
                return "This command requires an interactive MO session."
            return result.plain_text
        return gateway.run_turn(prompt, route_source="user")

    lane_scope = (
        agent.lane_scope(runtime_lane)
        if runtime_lane and callable(getattr(agent, "lane_scope", None))
        else nullcontext()
    )
    with lane_scope:
        return run()


def _run_goal_one_shot(objective: str, config_path: str) -> str:
    """Run one complete GoalRunner lifecycle for a trusted background handoff."""
    agent_factory, gateway_cls = _load_agent_runtime()
    agent = agent_factory(config_path)
    agent._noninteractive = True
    gateway_cls(agent)
    from core.goal import GoalRunner

    runner = GoalRunner(agent)
    result = runner.start(str(objective or "").strip())
    while getattr(agent, "_goal_active", False):
        result = runner.continue_goal()
    plan = getattr(agent, "_goal_plan", None)
    if str(getattr(plan, "state", "") or "") != "completed":
        reason = str(getattr(plan, "stop_reason", "") or result or "goal needs attention")
        raise RuntimeError(reason)
    return str(result or "")



def _print_cli_help() -> None:
    from interface.command_registry import build_help_text

    print("MO — local provider-first coding agent")
    print()
    print("Usage:")
    print("  mo                                  # interactive TUI")
    print("  mo -p \"prompt\" | --prompt \"prompt\"  # run one non-interactive turn (scriptable)")
    print("  mo --prompt-file path.txt           # run one non-interactive turn from a prompt file")
    print("  mo [--init]")
    print("  mo --init-project-docs              # create absent public project-doc starters")
    print("  mo [--help|--version|--update]")
    print("  mo --dashboard       Open the MO Dashboard with the normal MO terminal")
    print("  mo --explainer <init|validate|check|narrate|sheet|render|status> ...")
    print()
    print("Startup:")
    print("  Run `mo` from a project folder. MO preserves that project cwd and keeps private state under ~/.mo or MO_HOME.")
    print()
    print(build_help_text())


def _print_cli_version() -> None:
    from core.update.version import current_version

    print(current_version())


def _run_cli_update() -> None:
    from core.update.apply import apply_update

    print(apply_update())


def _run_explainer_cli(args: list[str]) -> int:
    """Reuse the installed MO launcher so Explainer works from any project cwd."""
    from core.explainer.cli import main as explainer_main

    return int(explainer_main(args))


def main(argv: list[str] | None = None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--explainer"]:
        code = _run_explainer_cli(args[1:])
        if code:
            raise SystemExit(code)
        return
    if any(arg in {"--help", "-h", "help"} for arg in args):
        _print_cli_help()
        return
    if any(arg in {"--version", "version"} for arg in args):
        _print_cli_version()
        return
    if any(arg in {"--update", "update"} for arg in args):
        _run_cli_update()
        return
    if "--init" in args or "init" in args:
        init_mo, render_init = _load_initializer()
        print(render_init(init_mo(project_path=CALLER_CWD)))
        return
    if "--init-project-docs" in args or "init-project-docs" in args:
        from core.context.project_docs import create_project_docs_starter, render_project_docs_starter_report

        report = create_project_docs_starter(CALLER_CWD)
        print(render_project_docs_starter_report(report))
        return
    try:
        portable_startup = _portable_startup_args(args)
        handoff_ready_id = _handoff_ready_id(args, portable_startup)
        runtime_lane = _runtime_lane_arg(args)
        explicit_config_path = _config_arg(args)
        startup_panes = _startup_panes_input(args)
    except ValueError as exc:
        print(f"MO startup error: {exc}", file=sys.stderr)
        sys.exit(2)
    config_path = explicit_config_path or _default_config_path(agent_root=AGENT_ROOT, caller_cwd=CALLER_CWD)
    if not os.path.exists(config_path):
        init_mo, render_init = _load_initializer()
        print(render_init(init_mo(project_path=CALLER_CWD)))
        print("\nRun `python mo.py` again after adding provider keys to ~/.mo/credentials/providers.env.")
        return
    prompt = _prompt_arg(args)
    prompt_file = _prompt_file_arg(args)
    startup_goal_file = _startup_goal_file_arg(args)
    goal_prompt_file = _goal_prompt_file_arg(args)
    selected_inputs = sum(value is not None for value in (prompt, prompt_file, startup_goal_file, goal_prompt_file))
    if selected_inputs > 1:
        print("Usage: choose one prompt or goal input mode", file=sys.stderr)
        sys.exit(2)
    if prompt_file is not None:
        if not prompt_file.strip():
            print("Usage: mo --prompt-file path.txt", file=sys.stderr)
            sys.exit(2)
        try:
            prompt = _read_prompt_file(prompt_file)
        except OSError as exc:
            print(f"Could not read prompt file: {type(exc).__name__}", file=sys.stderr)
            sys.exit(2)
    startup_goal = ""
    if startup_goal_file is not None:
        if not startup_goal_file.strip():
            print("MO startup error: startup goal file is missing", file=sys.stderr)
            sys.exit(2)
        try:
            startup_goal = _read_prompt_file(startup_goal_file).strip()
        except OSError as exc:
            print(f"Could not read startup goal file: {type(exc).__name__}", file=sys.stderr)
            sys.exit(2)
        if not startup_goal:
            print("MO startup error: startup goal is empty", file=sys.stderr)
            sys.exit(2)
    if goal_prompt_file is not None:
        if not goal_prompt_file.strip():
            print("MO startup error: background goal file is missing", file=sys.stderr)
            sys.exit(2)
        try:
            goal_prompt = _read_prompt_file(goal_prompt_file).strip()
        except OSError as exc:
            print(f"Could not read background goal file: {type(exc).__name__}", file=sys.stderr)
            sys.exit(2)
        try:
            completion_id = _design_completion_id()
        except ValueError as exc:
            print(f"MO startup error: {exc}", file=sys.stderr)
            sys.exit(2)
        try:
            text = _run_goal_one_shot(goal_prompt, config_path)
        except Exception:
            if _write_design_completion(completion_id, "needs_attention"):
                sys.exit(1)
            raise
        completion_published = _write_design_completion(completion_id, "completed")
        if text and not completion_published:
            print(text)
        return
    if prompt is not None:
        if not prompt.strip():
            print('Usage: mo -p "your prompt"', file=sys.stderr)
            sys.exit(2)
        if portable_startup is not None:
            print("MO startup error: portable conversations require an interactive terminal", file=sys.stderr)
            sys.exit(2)
        try:
            completion_id = _design_completion_id() if prompt_file is not None else ""
        except ValueError as exc:
            print(f"MO startup error: {exc}", file=sys.stderr)
            sys.exit(2)
        try:
            text = (
                _run_one_shot(prompt, config_path, runtime_lane)
                if runtime_lane else _run_one_shot(prompt, config_path)
            )
        except Exception:
            if _write_design_completion(completion_id, "needs_attention"):
                sys.exit(1)
            raise
        completion_published = _write_design_completion(completion_id, "completed")
        if text and not completion_published:
            print(text)
        return
    if runtime_lane:
        print("MO startup error: runtime lane requires a one-shot prompt", file=sys.stderr)
        sys.exit(2)
    if "--ux" in args or os.environ.get("MO_NEXT_UX", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("MO UX surface was removed; use `mo` for the interface terminal TUI.", file=sys.stderr)
        sys.exit(2)
    config_error_cls, provider_error_cls, provider_error_cleaner = _load_provider_errors()
    agent_factory, gateway_cls = _load_agent_runtime()
    try:
        agent = agent_factory(config_path)
        _bind_startup_portable(agent, portable_startup, ready_id=handoff_ready_id)
    except config_error_cls as exc:
        print(f"MO config error: {exc.message}", file=sys.stderr)
        print(f"  path: {exc.path}", file=sys.stderr)
        print("Fix the YAML or run `mo --init` to regenerate a private config.", file=sys.stderr)
        sys.exit(2)
    except provider_error_cls as exc:
        print(f"MO provider error: {provider_error_cleaner(str(exc))}", file=sys.stderr)
        print(f"  config: {config_path}", file=sys.stderr)
        print("Fix provider credentials or run `mo --init` to regenerate a private config.", file=sys.stderr)
        sys.exit(2)
    except ValueError as exc:
        print(f"MO startup error: {exc}", file=sys.stderr)
        sys.exit(2)
    # No instance/handoff notice in the startup banner -- it was noisy clutter
    # (live/stale pids, "isolated instance", "Singleton resources locked") that
    # made startup look unprofessional, and it's irrelevant to single-instance
    # use. The multi-instance resource locking still happens regardless; the
    # full instance list is available on demand via `/session list`.
    gateway = gateway_cls(agent)
    telegram = None
    heartbeat = None
    try:
        # Only import the telegram stack when it's actually enabled — the import
        # alone is ~100ms, and from_agent() eagerly builds SQLite stores. Gating
        # on the config flag here keeps a disabled telegram off the startup path.
        if (agent.config.get("telegram") or {}).get("enabled"):
            from core.telegram import start_telegram_gateway_if_enabled
            telegram = start_telegram_gateway_if_enabled(agent, gateway)
    except Exception as exc:
        print(f"MO: Telegram gateway failed to start: {exc}", file=sys.stderr)
        telegram = None
    try:
        from core.runtime.heartbeat import start_heartbeat_service_if_enabled
        heartbeat = start_heartbeat_service_if_enabled(agent, gateway, surface="terminal")
    except Exception as exc:
        print(f"MO: Heartbeat service failed to start: {exc}", file=sys.stderr)
        heartbeat = None
    mo_desktop_hotkey = _start_mo_desktop_hotkey_launcher_if_enabled(agent)
    try:
        from interface.terminal_loop import run_main_loop

        run_main_loop(
            agent,
            gateway,
            startup_notice="",
            startup_input=(("/goal " + startup_goal) if startup_goal else startup_panes
                           or ("/dashboard show" if "--dashboard" in args else "")),
        )
    finally:
        _stop_mo_desktop_hotkey_launcher(mo_desktop_hotkey)
        if telegram and hasattr(telegram, "stop"):
            telegram.stop()
        if heartbeat and hasattr(heartbeat, "stop"):
            heartbeat.stop()


if __name__ == "__main__":
    main()
