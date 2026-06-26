#!/usr/bin/env python3
# ruff: noqa: E402
"""MO — Provider-first local agent runtime."""

from __future__ import annotations

import importlib
import os
import sys

# Redirect Python's bytecode cache OUT of the checkout instead of disabling it.
# The original dont-write-bytecode setting kept the working tree clean but
# forced a full in-memory recompile of all ~370 modules on EVERY launch (~7s cold
# on a fresh deploy, never improving because nothing was ever cached). Pointing
# pycache_prefix at ~/.mo gives the same clean checkout while caching bytecode, so
# cold start drops ~10x (≈7s → ≈0.7s) and survives restarts. A read-only home just
# degrades to no-cache, never an error.
_MO_HOME = os.environ.get("MO_HOME") or os.path.join(os.path.expanduser("~"), ".mo")
sys.pycache_prefix = os.path.join(_MO_HOME, "pycache")

from core.text_safety import configure_utf8_stdio

configure_utf8_stdio()

CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
AGENT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
os.environ.setdefault("MO_INVOKED_AS", os.path.splitext(os.path.basename(sys.argv[0] or "mo"))[0] or "mo")
os.chdir(AGENT_ROOT)
sys.path.insert(0, AGENT_ROOT)

Console = None
HAS_RICH = False
create_agent = None
Gateway = None
initialize_mo = None
render_init_report = None
default_config_path = None
apply_state_migration = None
parse_migration_request = None
plan_state_migration = None
render_state_migration_report = None
render_existing_instances_notice = None


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
        from core.initializer import initialize_mo as loaded_initialize_mo, render_init_report as loaded_render_init_report
        initialize_mo = loaded_initialize_mo
        render_init_report = loaded_render_init_report
    return initialize_mo, render_init_report


def _default_config_path(*, agent_root: str, caller_cwd: str) -> str:
    global default_config_path
    if default_config_path is None:
        from core.path_defaults import default_config_path as loaded_default_config_path
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


def _load_state_migration():
    global apply_state_migration, parse_migration_request, plan_state_migration, render_state_migration_report
    if parse_migration_request is None:
        from core.state_migration import (
            apply_state_migration as loaded_apply_state_migration,
            parse_migration_request as loaded_parse_migration_request,
            plan_state_migration as loaded_plan_state_migration,
            render_state_migration_report as loaded_render_state_migration_report,
        )
        apply_state_migration = loaded_apply_state_migration
        parse_migration_request = loaded_parse_migration_request
        plan_state_migration = loaded_plan_state_migration
        render_state_migration_report = loaded_render_state_migration_report
    return apply_state_migration, parse_migration_request, plan_state_migration, render_state_migration_report


def _render_existing_instances_notice(config: dict) -> str:
    global render_existing_instances_notice
    if render_existing_instances_notice is None:
        from core.instance import render_existing_instances_notice as loaded_render_existing_instances_notice
        render_existing_instances_notice = loaded_render_existing_instances_notice
    return render_existing_instances_notice(config)


def _acquire_lock() -> bool:
    """Prevent duplicate MO Agent processes. Returns True if lock acquired."""
    from core.runtime_lock import acquire_runtime_lock
    return acquire_runtime_lock(label="MO Agent") is not None


def _migration_args(args: list[str]) -> list[str] | None:
    for marker in ("--migrate-state", "migrate-state"):
        if marker in args:
            idx = args.index(marker)
            return args[idx + 1:]
    return None


def _run_state_migration(args: list[str]) -> None:
    loaded_apply, loaded_parse, loaded_plan, loaded_render = _load_state_migration()
    action, confirm = loaded_parse(args)
    plan = loaded_plan(source_root=AGENT_ROOT)
    if action == "dry-run":
        print(loaded_render(plan))
        return
    if not confirm:
        print(loaded_render(plan))
        print("\nApply not run: add `--confirm` to copy/move legacy state.")
        return
    result = loaded_apply(plan, confirm=True, remove_source=(action == "move"))
    print(loaded_render(plan, result))


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


def _run_one_shot(prompt: str, config_path: str) -> str:
    """Run ONE non-interactive turn in-process and return its final text.

    Builds the agent, runs a single turn, returns the answer — same cost as a normal
    launch but without the TUI. Used by `mo -p`/`--prompt` for scripting and piping;
    stdout carries only the answer.
    """
    agent_factory, gateway_cls = _load_agent_runtime()
    agent = agent_factory(config_path)
    gateway = gateway_cls(agent)
    return gateway.run_turn(prompt, route_source="user")


def _next_ux_requested(args: list[str]) -> bool:
    env_value = os.environ.get("MO_NEXT_UX", "").strip().lower()
    return "--ux" in args or env_value in {"1", "true", "yes", "on"}


def _run_next_ux(args: list[str]) -> None:
    ux_args = [arg for arg in args if arg != "--ux"]
    explicit_mode = any(arg in {"--live", "--read-only", "--smoke", "--help", "-h"} for arg in ux_args)
    if not explicit_mode:
        ux_args.insert(0, "--live")
    module = importlib.import_module("UX.shell.app")
    module.main(ux_args)


def _print_cli_help() -> None:
    from interface.command_registry import SLASH_COMMAND_HELP

    print("MO — local provider-first coding agent")
    print()
    print("Usage:")
    print("  mo                                  # interactive TUI")
    print("  mo --ux                             # interactive next UX preview, live runtime")
    print("  mo -p \"prompt\" | --prompt \"prompt\"  # run one non-interactive turn (scriptable)")
    print("  mo [--init]")
    print("  mo [--migrate-state [dry-run|apply|move] [--confirm]]")
    print("  mo [--help|--version]")
    print()
    print("Startup:")
    print("  Run `mo` from a project folder. MO preserves that project cwd and keeps private state under ~/.mo or MO_HOME.")
    print()
    print(SLASH_COMMAND_HELP)


def _print_cli_version() -> None:
    print("MO v1.0")


def main(argv: list[str] | None = None):
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg in {"--help", "-h", "help"} for arg in args):
        _print_cli_help()
        return
    if any(arg in {"--version", "version"} for arg in args):
        _print_cli_version()
        return
    migration_args = _migration_args(args)
    if migration_args is not None:
        _run_state_migration(migration_args)
        return
    if "--init" in args or "init" in args:
        init_mo, render_init = _load_initializer()
        print(render_init(init_mo(project_path=CALLER_CWD)))
        return
    config_path = _default_config_path(agent_root=AGENT_ROOT, caller_cwd=CALLER_CWD)
    if not os.path.exists(config_path):
        init_mo, render_init = _load_initializer()
        print(render_init(init_mo(project_path=CALLER_CWD)))
        print("\nRun `python mo.py` again after adding provider keys to ~/.mo/.env or your shell environment.")
        return
    prompt = _prompt_arg(args)
    if prompt is not None:
        if not prompt.strip():
            print('Usage: mo -p "your prompt"', file=sys.stderr)
            sys.exit(2)
        text = _run_one_shot(prompt, config_path)
        if text:
            print(text)
        return
    if _next_ux_requested(args):
        _run_next_ux(args)
        return
    config_error_cls, provider_error_cls, provider_error_cleaner = _load_provider_errors()
    agent_factory, gateway_cls = _load_agent_runtime()
    try:
        agent = agent_factory(config_path)
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
    notice = _render_existing_instances_notice(getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {})
    if notice:
        print(notice)
    gateway = gateway_cls(agent)
    telegram = None
    heartbeat = None
    companion = None
    try:
        from core.telegram import start_telegram_gateway_if_enabled
        telegram = start_telegram_gateway_if_enabled(agent, gateway)
    except Exception:
        telegram = None
    try:
        from core.heartbeat import start_heartbeat_service_if_enabled
        heartbeat = start_heartbeat_service_if_enabled(agent, gateway, surface="terminal")
    except Exception:
        heartbeat = None
    try:
        from interface.ghost_desktop import start_companion_if_enabled
        companion = start_companion_if_enabled(agent, gateway)
        if companion:
            try:
                setattr(agent, "_companion", companion)
            except Exception:
                pass
    except Exception:
        companion = None
    console_cls, has_rich = _load_rich_console()
    console = console_cls() if has_rich and console_cls is not None else None
    try:
        from interface.terminal_loop import run_main_loop

        run_main_loop(agent, gateway, console, has_rich)
    finally:
        if companion and hasattr(companion, "stop"):
            companion.stop()
        if telegram and hasattr(telegram, "stop"):
            telegram.stop()
        if heartbeat and hasattr(heartbeat, "stop"):
            heartbeat.stop()


if __name__ == "__main__":
    main()
