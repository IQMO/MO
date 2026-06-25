import inspect
import re

from core.agent.agent import Agent
from interface.command_palette import PALETTE_CATEGORIES
from interface.slash_commands import SLASH_ALIASES, SLASH_COMMAND_HELP, SLASH_COMMANDS, SLASH_SUBCOMMANDS


def _runtime_slash_handlers() -> set[str]:
    source = inspect.getsource(Agent.process_slash_command)
    match = re.search(r"handlers\s*=\s*\{(.*?)\n\s*\}", source, re.S)
    assert match, "could not find Agent.process_slash_command handlers dict"
    return set(re.findall(r'"(/[^"]+)"\s*:', match.group(1)))


def test_runtime_commands_have_interface_registry_or_alias_metadata():
    runtime = _runtime_slash_handlers()
    registered = set(SLASH_COMMANDS) | set(SLASH_ALIASES)

    assert runtime - registered == set()


def test_interface_registry_commands_have_runtime_handlers():
    runtime = _runtime_slash_handlers()

    assert set(SLASH_COMMANDS) - runtime == set()


def test_aliases_and_subcommands_target_registered_commands():
    assert set(SLASH_ALIASES.values()) - set(SLASH_COMMANDS) == set()
    assert set(SLASH_SUBCOMMANDS) - set(SLASH_COMMANDS) == set()


def test_palette_commands_target_registered_commands():
    palette_roots = {
        command.split()[0]
        for _category, items in PALETTE_CATEGORIES
        for command, _description in items
    }

    assert palette_roots - set(SLASH_COMMANDS) == set()


def test_goal_is_single_registry_visible_everywhere():
    assert "/goal" in SLASH_COMMANDS
    assert SLASH_ALIASES["/g"] == "/goal"
    assert "/goal" in SLASH_SUBCOMMANDS
    assert "/goal" in SLASH_COMMAND_HELP
    assert any(command == "/goal" for _category, items in PALETTE_CATEGORIES for command, _description in items)


def test_gp_pg_removed_prompt_enhancer_moved_to_ctrl_e():
    # Prompt enhancement is now the Ctrl+E keybinding; the slash commands are gone.
    assert "/gp" not in SLASH_COMMANDS
    assert "/pg" not in SLASH_ALIASES
