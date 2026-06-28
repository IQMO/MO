"""Single source of truth for MO slash command UI metadata.

Runtime execution still lives in core.agent.Agent.process_slash_command; this
module owns the interface metadata used for help text, completion, aliases,
subcommands, and command-palette categories.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    description: str
    category: str = ""
    aliases: tuple[str, ...] = ()
    subcommands: tuple[tuple[str, str], ...] = ()
    palette_description: str | None = None
    palette_entries: tuple[tuple[str, str], ...] = ()
    help_lines: tuple[str, ...] = ()
    palette: bool = True
    legacy: bool = False

    @property
    def palette_desc(self) -> str:
        return self.description if self.palette_description is None else self.palette_description


def _extension_specs() -> tuple[SlashCommandSpec, ...]:
    """Return slash commands supplied by a profile-owned local extension."""
    try:
        from core.local_extensions import command_specs
    except Exception:
        return ()
    specs: list[SlashCommandSpec] = []
    fields = set(SlashCommandSpec.__dataclass_fields__)
    for item in command_specs():
        if not isinstance(item, dict):
            continue
        try:
            data = {key: value for key, value in item.items() if key in fields}
            if "name" not in data or "description" not in data:
                continue
            specs.append(SlashCommandSpec(**data))
        except Exception:
            continue
    return tuple(specs)


def _all_commands(*, include_extensions: bool = True) -> tuple[SlashCommandSpec, ...]:
    if not include_extensions:
        return COMMANDS
    return COMMANDS + _extension_specs()


def _command_by_name(*, include_extensions: bool = True) -> dict[str, SlashCommandSpec]:
    return {spec.name: spec for spec in _all_commands(include_extensions=include_extensions)}


COMMANDS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec(
        name="/help",
        description="show commands",
        category="Settings",
        aliases=("/h",),
        palette_description="show all commands",
        help_lines=("/help, /h         show this help",),
    ),
    SlashCommandSpec(
        name="/init",
        description="initialize/check private MO home",
        category="Settings",
        palette_description="initialize/check ~/.mo private runtime",
        help_lines=("/init             initialize/check private MO home",),
    ),
    SlashCommandSpec(
        name="/doctor",
        description="health check: env, config, providers, imports",
        category="Settings",
        subcommands=(("--json", "machine-readable JSON output"),),
        palette_description="one-shot health check (offline-safe)",
        help_lines=("/doctor           health check; add --json for machine-readable output",),
    ),
    SlashCommandSpec(
        name="/update",
        description="fast-forward this checkout when upstream has updates",
        category="Settings",
        palette_description="fast-forward MO checkout update",
        help_lines=("/update           fast-forward this checkout when upstream has updates",),
    ),
    SlashCommandSpec(
        name="/migrate",
        description="dry-run/apply legacy state migration to private home",
        category="Settings",
        subcommands=(
            ("dry-run", "show legacy memory/logs migration plan"),
            ("apply --confirm", "copy missing legacy state into ~/.mo"),
            ("move --confirm", "copy then remove copied legacy files"),
        ),
        palette_description="migrate legacy checkout-local state to ~/.mo",
        palette_entries=(
            ("/migrate", "dry-run legacy state migration"),
            ("/migrate apply --confirm", "copy missing legacy state into ~/.mo"),
        ),
        help_lines=(
            "/migrate         dry-run legacy memory/logs migration",
            "                  /migrate apply --confirm | move --confirm",
        ),
    ),
    SlashCommandSpec(
        name="/exit",
        description="quit MO",
        category="Exit",
        aliases=("/quit", "/q"),
        help_lines=("/exit, /quit, /q  quit MO",),
    ),
    SlashCommandSpec(
        name="/clear",
        description="clear conversation",
        category="Sessions",
        aliases=("/c",),
        help_lines=("/clear, /c        clear conversation",),
    ),
    SlashCommandSpec(
        name="/status",
        description="MO/session status",
        category="Tasks",
        help_lines=("/status           show MO/session status",),
    ),
    SlashCommandSpec(
        name="/usage",
        description="token usage + compression savings",
        category="Tasks",
        help_lines=("/usage            show token usage and compression savings",),
    ),
    SlashCommandSpec(
        name="/heartbeat",
        description="heartbeat and surface continuity status",
        category="Tasks",
        subcommands=(
            ("status", "show latest heartbeat"),
            ("now", "record a heartbeat now"),
            ("context", "show recent surface continuity context"),
        ),
        palette_entries=(("/heartbeat status", "show latest heartbeat"),),
        help_lines=(
            "/heartbeat        show heartbeat/surface continuity status",
            "                  /heartbeat now     record heartbeat now",
            "                  /heartbeat context show recent surface context",
        ),
    ),
    SlashCommandSpec(
        name="/telegram",
        description="Telegram remote gateway status/approval",
        category="Settings",
        subcommands=(
            ("status", "show gateway status"),
            ("queue", "show queued Telegram work"),
            ("chats", "show Telegram chat mappings"),
            ("approve", "approve a pairing code"),
            ("start", "start enabled gateway if token env is present"),
            ("disable", "disable gateway for this process"),
        ),
        palette_entries=(("/telegram status", "show Telegram gateway status"),),
        help_lines=(
            "/telegram         Telegram gateway status/approval",
            "                  /telegram approve <code>",
            "                  /telegram queue | chats | start | disable",
        ),
    ),
    SlashCommandSpec(
        name="/structural-graph",
        description="show/build MO structural code graph",
        category="Tasks",
        aliases=("/sg",),
        subcommands=(
            ("status", "show structural graph status"),
            ("build", "build MO's local community code map"),
            ("refresh", "rebuild MO's local community code map"),
        ),
        palette_description="structural code graph status/build",
        palette_entries=(
            ("/structural-graph status", "show structural graph status"),
            ("/structural-graph build", "build structural graph"),
        ),
        help_lines=(
            "/structural-graph, /sg  show/build structural code graph",
            "                  /structural-graph build",
            "                  /structural-graph refresh",
        ),
    ),
    SlashCommandSpec(
        name="/model",
        description="show or switch model",
        category="Settings",
        subcommands=(),
        help_lines=("/model            show current model",),
    ),
    SlashCommandSpec(
        name="/projects",
        description="list project history",
        category="Sessions",
        palette_description="list project history",
        help_lines=("/projects         list project history",),
    ),
    SlashCommandSpec(
        name="/sessions",
        description="legacy alias for /projects",
        palette=False,
        legacy=True,
        help_lines=("/sessions         legacy alias for /projects",),
    ),
    SlashCommandSpec(
        name="/new",
        description="start new session",
        category="Sessions",
        help_lines=("/new              start a new session",),
    ),
    SlashCommandSpec(
        name="/profile",
        description="show or edit profile",
        category="Settings",
        aliases=("/p",),
        subcommands=(
            ("name", "set operator name"),
            ("tools", "set preferred tools"),
            ("provider", "set favorite provider"),
            ("mine", "review safe learning updates"),
            ("export", "export learning bundle for another MO instance"),
            ("import", "import a learning bundle (dry-run; --confirm applies)"),
        ),
        help_lines=(
            "/profile, /p      show or edit profile",
            "                  /profile name <name>[/<alias>]",
            "                  /profile tools <tool,...>",
            "                  /profile provider <provider/model>",
            "                  /profile mine    review safe learning updates",
            "                  /profile export [path] | import <path> [--confirm]",
        ),
    ),
    SlashCommandSpec(
        name="/learning",
        description="learning health/status",
        category="Tasks",
        subcommands=(
            ("status", "show deterministic learning status"),
            ("suggestions", "find safe learning suggestions"),
            ("pending", "show pending learning suggestions"),
            ("confirm", "confirm a suggestion by id"),
            ("dismiss", "dismiss a suggestion by id"),
        ),
        palette_entries=(("/learning status", "show learning status"),),
        help_lines=(
            "/learning         show learning status",
            "                  /learning suggestions  find safe suggestions",
            "                  /learning pending      show pending suggestions",
            "                  /learning confirm <id> / dismiss <id>",
        ),
    ),
    SlashCommandSpec(
        name="/goal",
        description="autonomous goal mode",
        category="Tasks",
        aliases=("/g",),
        subcommands=(
            ("stop", "stop active goal"),
            ("status", "show goal progress"),
        ),
        palette_description="autonomous goal mode",
        help_lines=(
            "/goal, /g         autonomous goal mode",
            "                  /goal <task>     start goal",
            "                  /goal            continue active goal",
            "                  /goal stop       stop active goal",
            "                  /goal status     show progress",
            "                  Ctrl+G           background/foreground toggle",
        ),
    ),
    SlashCommandSpec(
        name="/prt",
        description="Project Review Team — deep review & auto-fix",
        category="Tasks",
        subcommands=(
            ("fix", "run PRT and auto-fix findings"),
        ),
        help_lines=(
            "/prt              run a deep codebase review",
            "                  /prt fix         run review and auto-fix findings",
            "                  /prt <files...>  review specific files",
        ),
    ),
    SlashCommandSpec(
        name="/ghost",
        description="toggle Ghost side-check mode on/off or ask Ghost",
        category="Tasks",
        aliases=("/gh",),
        subcommands=(
            ("on", "enable Ghost mode — all messages route to Ghost"),
            ("off", "disable Ghost mode — messages route to MO"),
            ("window", "show/hide the desktop Ghost window"),
        ),
        palette_description="toggle Ghost mode on/off or side-chat",
        palette_entries=(
            ("/ghost on", "enable Ghost mode"),
            ("/ghost off", "disable Ghost mode"),
            ("/ghost window", "show/hide the desktop Ghost window"),
        ),
        help_lines=(
            "/ghost, /gh       toggle Ghost on/off or ask Ghost",
            "                  /ghost on        enable Ghost mode",
            "                  /ghost off       disable Ghost mode",
            "                  /ghost window    show/hide the desktop Ghost window",
            "                  /ghost <question> ask Ghost a side-question",
            "                  Alt+G            toggle Ghost mode",
        ),
    ),
    SlashCommandSpec(
        name="/moon",
        description="Toggle animated glowing MO logo.",
        category="Settings",
        subcommands=(
            ("on", "enable glowing logo"),
            ("off", "disable glowing logo"),
        ),
        palette_description="toggle glowing logo",
        help_lines=(
            "/moon             Toggle animated glowing MO logo.",
            "                  /moon on         enable glowing logo",
            "                  /moon off        disable glowing logo",
        ),
    ),
    SlashCommandSpec(
        name="/hints",
        description="Toggle rotating hint tips on the idle line.",
        category="Settings",
        subcommands=(
            ("on", "show rotating hints on idle line"),
            ("off", "hide hints, show normal idle"),
        ),
        palette_description="toggle rotating idle hints",
        help_lines=(
            "/hints            Toggle rotating hint tips on the idle line.",
            "                  /hints on        enable idle hints",
            "                  /hints off       disable idle hints",
            "                  Edit ~/.mo/hints.txt to add your own hints.",
        ),
    ),
    SlashCommandSpec(
        name="/undo",
        description="remove last exchange",
        category="Sessions",
        aliases=("/u",),
        help_lines=("/undo, /u         remove last exchange",),
    ),
    SlashCommandSpec(
        name="/retry",
        description="re-run last prompt",
        category="Sessions",
        aliases=("/r",),
        help_lines=("/retry, /r        re-run last prompt",),
    ),
    SlashCommandSpec(
        name="/session",
        description="manage saved sessions (save/list/remove/switch)",
        category="Sessions",
        aliases=("/s",),
        palette_description="manage saved sessions",
        palette_entries=(("/session save", "save current session"),),
        subcommands=(
            ("save", "save current session"),
            ("list", "list saved sessions"),
            ("remove", "remove a session"),
        ),
        help_lines=("/session, /s      manage saved sessions",),
    ),
    SlashCommandSpec(
        name="/resume",
        description="resume last saved session",
        category="Sessions",
        palette_description="resume last session",
        help_lines=("/resume           resume last saved session",),
    ),
    SlashCommandSpec(
        name="/reload",
        description="reload config and system prompt",
        category="Settings",
        palette_description="reload config and prompts",
        help_lines=("/reload           reload config and system prompt",),
    ),
    SlashCommandSpec(
        name="/think",
        description="set reasoning level (high/medium/low)",
        category="Settings",
        palette_description="reasoning level (high/medium/low)",
        subcommands=(
            ("high", "maximum reasoning"),
            ("medium", "balanced reasoning"),
            ("low", "fast reasoning"),
        ),
        help_lines=("/think            set reasoning level (high/medium/low)",),
    ),
    SlashCommandSpec(
        name="/settings",
        description="show current settings",
        category="Settings",
        help_lines=("/settings         show current settings",),
    ),
    SlashCommandSpec(
        name="/skin",
        description="Switch UI theme",
        category="Settings",
        palette_description="Switch UI theme (e.g. default, dracula)",
        help_lines=("/skin             Switch UI theme. /skin dracula | /skin default",),
    ),
    SlashCommandSpec(
        name="/companion",
        description="alias of /ghost window — toggle the desktop Ghost window",
        category="Work",
        palette_description="alias of /ghost window (desktop Ghost window)",
        help_lines=("/companion        alias of /ghost window — toggle the desktop Ghost window",),
    ),
)

COMMAND_BY_NAME: dict[str, SlashCommandSpec] = _command_by_name(include_extensions=False)
SLASH_COMMANDS: dict[str, str] = {spec.name: spec.description for spec in COMMANDS}
SLASH_ALIASES: dict[str, str] = {alias: spec.name for spec in COMMANDS for alias in spec.aliases}
SLASH_SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
    spec.name: list(spec.subcommands)
    for spec in COMMANDS
    if spec.subcommands or spec.name in {"/model"}
}


HELP_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Work", ("/status", "/goal", "/ghost", "/prt", "/structural-graph", "/learning", "/usage", "/companion")),
    ("Sessions", ("/projects", "/sessions", "/session", "/new", "/resume", "/clear", "/undo", "/retry")),
    ("Settings", ("/help", "/init", "/doctor", "/update", "/migrate", "/model", "/profile", "/skin", "/moon", "/hints", "/reload", "/think", "/settings")),
    ("Remote", ("/heartbeat", "/telegram")),
    ("Exit", ("/exit",)),
)

HELP_ORDER: tuple[str, ...] = tuple(command for _section, commands in HELP_SECTIONS for command in commands)


def _command_hidden(command: str) -> bool:
    """Return True for commands that should not appear in user-facing recents."""
    root = str(command or "").strip().split()[0] if str(command or "").strip() else ""
    spec = _command_by_name().get(root)
    if spec is None:
        return True
    return bool(spec.legacy or not spec.palette)


def build_help_text(*, include_extensions: bool = True) -> str:
    lines = ["MO Agent commands:"]
    command_by_name = _command_by_name(include_extensions=include_extensions)
    rendered: set[str] = set()
    for section, commands in HELP_SECTIONS:
        visible = [name for name in commands if name in command_by_name]
        if not visible:
            continue
        lines.append("")
        lines.append(section)
        for name in visible:
            spec = command_by_name[name]
            rendered.add(spec.name)
            for line in spec.help_lines:
                lines.append(f"  {line}")
    extension_by_category: dict[str, list[SlashCommandSpec]] = {}
    if include_extensions:
        for spec in _extension_specs():
            if spec.name in rendered or not spec.help_lines:
                continue
            extension_by_category.setdefault(spec.category or "Tasks", []).append(spec)
    for section, specs in extension_by_category.items():
        lines.append("")
        lines.append(section)
        for spec in specs:
            for line in spec.help_lines:
                lines.append(f"  {line}")
    return "\n".join(lines)


SLASH_COMMAND_HELP = build_help_text(include_extensions=False)


PALETTE_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Recent", ()),
    ("Tasks", ("/goal", "/ghost", "/prt", "/structural-graph", "/status", "/usage", "/heartbeat", "/companion")),
    ("Sessions", ("/projects", "/session", "/session save", "/resume", "/new", "/clear", "/undo", "/retry")),
    ("Settings", ("/settings", "/init", "/doctor", "/update", "/migrate", "/model", "/think", "/reload", "/profile", "/telegram", "/help")),
    ("Exit", ("/exit",)),
)


def _palette_entry(command: str, *, include_extensions: bool = True) -> tuple[str, str]:
    command_by_name = _command_by_name(include_extensions=include_extensions)
    if " " in command:
        root = command.split()[0]
        spec = command_by_name[root]
        for entry, desc in spec.palette_entries:
            if entry == command:
                return entry, desc
    spec = command_by_name[command]
    return spec.name, spec.palette_desc


def build_palette_categories(*, include_extensions: bool = True) -> list[tuple[str, list[tuple[str, str]]]]:
    categories: list[tuple[str, list[tuple[str, str]]]] = []
    command_by_name = _command_by_name(include_extensions=include_extensions)
    for name, commands in PALETTE_ORDER:
        entries = [
            _palette_entry(command, include_extensions=include_extensions)
            for command in commands
            if (command.split()[0] if " " in command else command) in command_by_name
        ]
        categories.append((name, entries))
    if include_extensions:
        extension_entries: dict[str, list[tuple[str, str]]] = {}
        for spec in _extension_specs():
            if not spec.palette:
                continue
            entries = list(spec.palette_entries) if spec.palette_entries else [(spec.name, spec.palette_desc)]
            extension_entries.setdefault(spec.category or "Tasks", []).extend(entries)
        if extension_entries:
            existing = {name: entries for name, entries in categories}
            for name, entries in extension_entries.items():
                if name in existing:
                    existing[name].extend(entries)
                else:
                    categories.append((name, entries))
    return categories


PALETTE_CATEGORIES = build_palette_categories(include_extensions=False)
DEFAULT_PALETTE_CATEGORY = 1


def slash_command_names() -> list[str]:
    commands = _all_commands()
    names = [spec.name for spec in commands]
    names.extend(alias for spec in commands for alias in spec.aliases)
    return sorted(set(names))


def slash_command_with_desc() -> list[tuple[str, str]]:
    """Return (command, description) pairs for suggestion display."""
    return [(spec.name, spec.description) for spec in _all_commands()]
