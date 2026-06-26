"""Direct tests for interface.command_registry — the single source of truth."""
from interface.command_registry import (
    COMMAND_BY_NAME,
    COMMANDS,
    DEFAULT_PALETTE_CATEGORY,
    HELP_ORDER,
    HELP_SECTIONS,
    PALETTE_CATEGORIES,
    PALETTE_ORDER,
    SLASH_ALIASES,
    SLASH_COMMAND_HELP,
    SLASH_COMMANDS,
    SLASH_SUBCOMMANDS,
    SlashCommandSpec,
    build_help_text,
    build_palette_categories,
    slash_command_names,
    slash_command_with_desc,
)


# ── spec integrity ──────────────────────────────────────────────

def test_all_specs_are_valid():
    for spec in COMMANDS:
        assert isinstance(spec, SlashCommandSpec)
        assert spec.name.startswith("/")
        assert spec.description
        assert isinstance(spec.aliases, tuple)
        assert isinstance(spec.subcommands, tuple)
        assert isinstance(spec.help_lines, tuple) and len(spec.help_lines) >= 0


def test_no_duplicate_command_names():
    names = [spec.name for spec in COMMANDS]
    assert len(names) == len(set(names))


def test_all_aliases_target_existing_commands():
    for alias, target in SLASH_ALIASES.items():
        assert target in SLASH_COMMANDS, f"alias {alias} -> {target} (missing)"


def test_all_subcommands_target_existing_commands():
    for cmd, subs in SLASH_SUBCOMMANDS.items():
        assert cmd in SLASH_COMMANDS
        for sub_name, _desc in subs:
            # subcommand name is just a string like "stop", "status", etc.
            assert sub_name


# ── derived data structures ──────────────────────────────────────

def test_slash_commands_derived_from_all_specs():
    assert len(SLASH_COMMANDS) == len(COMMANDS)
    for spec in COMMANDS:
        assert SLASH_COMMANDS[spec.name] == spec.description


def test_command_by_name_covers_all():
    assert len(COMMAND_BY_NAME) == len(COMMANDS)
    for spec in COMMANDS:
        assert COMMAND_BY_NAME[spec.name] is spec


def test_slash_command_help_is_non_empty():
    text = SLASH_COMMAND_HELP
    assert "MO Agent commands" in text
    assert len(text) > 50


def test_build_help_text_uses_help_order():
    text = build_help_text()
    for name in HELP_ORDER:
        assert name in text


# ── ordering structures ─────────────────────────────────────────

def test_help_order_references_only_registered_commands():
    for name in HELP_ORDER:
        assert name in COMMAND_BY_NAME, f"HELP_ORDER references unknown {name}"


def test_help_sections_reference_each_visible_command_once():
    sectioned = [name for _section, commands in HELP_SECTIONS for name in commands]
    assert sectioned == list(HELP_ORDER)
    assert len(sectioned) == len(set(sectioned))
    assert set(sectioned) == set(COMMAND_BY_NAME)


def test_help_text_uses_mo_native_section_headers():
    text = SLASH_COMMAND_HELP
    for section in ("Work", "Sessions", "Settings", "Remote", "Exit"):
        assert f"\n\n{section}\n" in text
    assert text.index("\n\nWork\n") < text.index("\n\nSessions\n") < text.index("\n\nSettings\n")
    assert text.index("\n\nRemote\n") < text.index("\n\nExit\n")


def test_palette_order_references_only_registered_commands():
    for _category, commands in PALETTE_ORDER:
        for cmd in commands:
            root = cmd.split()[0]
            assert root in COMMAND_BY_NAME, f"PALETTE_ORDER references unknown {root}"


def test_palette_categories_is_valid():
    assert len(PALETTE_CATEGORIES) > 0
    for category, entries in PALETTE_CATEGORIES:
        assert isinstance(category, str) and category
        for name, desc in entries:
            assert name and desc


def test_default_palette_category_is_valid_index():
    assert 0 <= DEFAULT_PALETTE_CATEGORY < len(PALETTE_CATEGORIES)


# ── utility functions ───────────────────────────────────────────

def test_slash_command_names_returns_sorted_list():
    names = slash_command_names()
    assert names == sorted(names)
    assert "/help" in names
    assert "/h" in names  # alias


def test_slash_command_with_desc_returns_pairs():
    pairs = slash_command_with_desc()
    assert len(pairs) >= len(COMMANDS)
    assert ("/goal", "autonomous goal mode") in pairs
    assert ("/learning", "learning health/status") in pairs


def test_ghost_is_a_public_slash_command_with_help_and_palette():
    assert "/ghost" in SLASH_COMMANDS
    assert "/gh" in SLASH_ALIASES
    assert "/ghost" in SLASH_COMMAND_HELP
    found_in_help = False
    for _section, commands in HELP_SECTIONS:
        if "/ghost" in commands:
            found_in_help = True
            break
    assert found_in_help, "/ghost must appear in HELP_SECTIONS"
    found_in_palette = False
    for _category, commands in PALETTE_ORDER:
        if any(cmd.split()[0] == "/ghost" for cmd in commands):
            found_in_palette = True
            break
    assert found_in_palette, "/ghost must appear in PALETTE_ORDER"


def test_owner_comparison_is_operator_only_dispatchable_but_hidden_from_users(monkeypatch):
    # /owner_comparison is an operator-only protocol command: always dispatchable, but
    # advertised only when the operator protocol pack is installed. A public
    # user (no pack) must not see it in help / palette / completion.
    assert "/owner_comparison" in SLASH_COMMANDS  # still resolvable/dispatchable for everyone
    assert COMMAND_BY_NAME["/owner_comparison"].operator_only is True

    def _palette_has_owner_comparison() -> bool:
        return any(
            cmd.split()[0] == "/owner_comparison"
            for _cat, entries in build_palette_categories()
            for cmd, _desc in entries
        )

    # Operator build (pack installed) — visible.
    monkeypatch.setattr(
        "core.owner_protocols.operator_protocols_installed", lambda: True
    )
    assert "/owner_comparison" in slash_command_names()
    assert "OWNER_COMPARISON comparison/improvement mode" in build_help_text()
    assert _palette_has_owner_comparison()

    # Public build (no pack) — hidden from all user-facing surfaces.
    monkeypatch.setattr(
        "core.owner_protocols.operator_protocols_installed", lambda: False
    )
    assert "/owner_comparison" not in slash_command_names()
    assert "/owner_comparison" not in build_help_text()
    assert not _palette_has_owner_comparison()


def test_projects_is_preferred_project_history_and_sessions_is_legacy_alias():
    assert SLASH_COMMANDS["/projects"] == "list project history"
    assert SLASH_COMMANDS["/sessions"] == "legacy alias for /projects"
    assert "/projects         list project history" in SLASH_COMMAND_HELP
    assert "/sessions         legacy alias for /projects" in SLASH_COMMAND_HELP
    assert "list saved sessions" not in SLASH_COMMAND_HELP
    assert "/session, /s      manage saved sessions" in SLASH_COMMAND_HELP
    assert "/projects" in dict(PALETTE_CATEGORIES[2][1])


def test_help_uses_plain_learning_and_telegram_chat_words():
    text = SLASH_COMMAND_HELP
    assert "/telegram queue | chats | start | disable" in text
    assert "telegram queue | sessions" not in text
    assert "/profile mine    review safe learning updates" in text
    assert "/learning suggestions  find safe suggestions" in text
    assert "inert" not in text


def test_internal_handoff_commands_are_not_user_facing():
    for hidden in ("/handoff", "/compact"):
        assert hidden not in SLASH_COMMANDS
        assert hidden not in COMMAND_BY_NAME
        assert hidden not in HELP_ORDER
        assert hidden not in SLASH_COMMAND_HELP
        assert hidden not in slash_command_names()
    for _category, commands in PALETTE_ORDER:
        assert "/handoff" not in commands
        assert "/compact" not in commands


# ── critical commands present ───────────────────────────────────

def test_goal_is_registered_everywhere():
    assert "/goal" in SLASH_COMMANDS
    assert "/g" in SLASH_ALIASES
    assert SLASH_ALIASES["/g"] == "/goal"
    assert "/goal" in SLASH_SUBCOMMANDS
    assert "/goal" in COMMAND_BY_NAME
    spec = COMMAND_BY_NAME["/goal"]
    assert any(sub[0] == "stop" for sub in spec.subcommands)
    assert any(sub[0] == "status" for sub in spec.subcommands)
