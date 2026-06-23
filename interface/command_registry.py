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
    operator_only: bool = False

    @property
    def palette_desc(self) -> str:
        return self.description if self.palette_description is None else self.palette_description


def _operator_protocols_visible() -> bool:
    """True when the operator's private protocol pack is installed.

    Operator-only commands stay fully dispatchable for everyone, but are hidden
    from user-facing help/palette/completion on a public build (no pack), so a
    user never sees operator-only machinery advertised. Imported lazily to avoid
    an interface->core import cycle and to stay monkeypatchable in tests.
    """
    try:
        from core.owner_protocols import operator_protocols_installed

        return bool(operator_protocols_installed())
    except Exception:
        return False


def _command_hidden(name: str) -> bool:
    """True when *name* (or its root command) is operator-only and not installed."""
    root = name.split()[0] if " " in name else name
    spec = COMMAND_BY_NAME.get(root)
    return bool(spec and spec.operator_only and not _operator_protocols_visible())


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
        name="/gp",
        description="enhance prompt in input before sending",
        category="Tasks",
        aliases=("/pg",),
        palette_description="prompt enhancer preview",
        help_lines=("/gp <prompt>      enhance prompt in input; press Enter to send",),
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
        name="/vs05",
        description="VS05 comparison/adoption mode",
        category="Tasks",
        palette_description="compare MO against a reference system",
        help_lines=(
            "/vs05             VS05 comparison/adoption mode",
            "                  /vs05 <current-path> <reference-path>",
        ),
        # Operator-only protocol: dispatchable for all, but hidden from
        # user-facing help/palette/completion unless the protocol pack is installed.
        operator_only=True,
    ),
    SlashCommandSpec(
        name="/ghost",
        description="toggle Ghost side-check mode on/off or ask Ghost",
        category="Tasks",
        aliases=("/gh",),
        subcommands=(
            ("on", "enable Ghost mode — all messages route to Ghost"),
            ("off", "disable Ghost mode — messages route to MO"),
        ),
        palette_description="toggle Ghost mode on/off or side-chat",
        palette_entries=(
            ("/ghost on", "enable Ghost mode"),
            ("/ghost off", "disable Ghost mode"),
        ),
        help_lines=(
            "/ghost, /gh       toggle Ghost on/off or ask Ghost",
            "                  /ghost on        enable Ghost mode",
            "                  /ghost off       disable Ghost mode",
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
        name="/companion",
        description="toggle the on-screen Ghost desktop window",
        category="Work",
        palette_description="show/hide the Ghost desktop window",
        help_lines=("/companion        toggle the on-screen Ghost desktop window",),
    ),
)

COMMAND_BY_NAME: dict[str, SlashCommandSpec] = {spec.name: spec for spec in COMMANDS}
SLASH_COMMANDS: dict[str, str] = {spec.name: spec.description for spec in COMMANDS}
SLASH_ALIASES: dict[str, str] = {alias: spec.name for spec in COMMANDS for alias in spec.aliases}
SLASH_SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
    spec.name: list(spec.subcommands)
    for spec in COMMANDS
    if spec.subcommands or spec.name in {"/model"}
}


HELP_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Work", ("/status", "/goal", "/ghost", "/gp", "/prt", "/vs05", "/structural-graph", "/learning", "/usage", "/companion")),
    ("Sessions", ("/projects", "/sessions", "/session", "/new", "/resume", "/clear", "/undo", "/retry")),
    ("Settings", ("/help", "/init", "/doctor", "/migrate", "/model", "/profile", "/moon", "/hints", "/reload", "/think", "/settings")),
    ("Remote", ("/heartbeat", "/telegram")),
    ("Exit", ("/exit",)),
)

HELP_ORDER: tuple[str, ...] = tuple(command for _section, commands in HELP_SECTIONS for command in commands)


def build_help_text() -> str:
    lines = ["MO Agent commands:"]
    for section, commands in HELP_SECTIONS:
        visible = [name for name in commands if not _command_hidden(name)]
        if not visible:
            continue
        lines.append("")
        lines.append(section)
        for name in visible:
            spec = COMMAND_BY_NAME[name]
            for line in spec.help_lines:
                lines.append(f"  {line}")
    return "\n".join(lines)


SLASH_COMMAND_HELP = build_help_text()


PALETTE_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Recent", ()),
    ("Tasks", ("/goal", "/ghost", "/gp", "/prt", "/vs05", "/structural-graph", "/status", "/usage", "/heartbeat", "/companion")),
    ("Sessions", ("/projects", "/session", "/session save", "/resume", "/new", "/clear", "/undo", "/retry")),
    ("Settings", ("/settings", "/init", "/migrate", "/model", "/think", "/reload", "/profile", "/telegram", "/help")),
    ("Exit", ("/exit",)),
)


def _palette_entry(command: str) -> tuple[str, str]:
    if " " in command:
        root = command.split()[0]
        spec = COMMAND_BY_NAME[root]
        for entry, desc in spec.palette_entries:
            if entry == command:
                return entry, desc
    spec = COMMAND_BY_NAME[command]
    return spec.name, spec.palette_desc


def build_palette_categories() -> list[tuple[str, list[tuple[str, str]]]]:
    categories: list[tuple[str, list[tuple[str, str]]]] = []
    for name, commands in PALETTE_ORDER:
        entries = [_palette_entry(command) for command in commands if not _command_hidden(command)]
        categories.append((name, entries))
    return categories


PALETTE_CATEGORIES = build_palette_categories()
DEFAULT_PALETTE_CATEGORY = 1


def slash_command_names() -> list[str]:
    names = list(SLASH_COMMANDS.keys()) + list(SLASH_ALIASES.keys())
    return sorted({name for name in names if not _command_hidden(name)})


def slash_command_with_desc() -> list[tuple[str, str]]:
    """Return (command, description) pairs for suggestion display."""
    return [(cmd, desc) for cmd, desc in SLASH_COMMANDS.items() if not _command_hidden(cmd)]
