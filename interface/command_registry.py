"""Canonical MO slash-command inventory for dispatch and every UI surface.

Handlers live on ``AgentSlashCommands`` and are resolved from each registered
command name. This registry owns command identity, dispatch naming, aliases,
presentation, help, completion, subcommands, and palette categories.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    description: str
    category: str = ""
    presentation: str = "report"  # report | notice; controls override this at runtime
    aliases: tuple[str, ...] = ()
    subcommands: tuple[tuple[str, str], ...] = ()
    palette_description: str | None = None
    palette_entries: tuple[tuple[str, str], ...] = ()  # additional submenu actions, never duplicate top-level rows
    busy_args: tuple[str, ...] = ()  # safe argument patterns; "*" admits every argument
    help_lines: tuple[str, ...] = ()
    palette: bool = True
    help: bool = True  # False = keep out of /help (still dispatchable); default: shown
    palette_submenu: bool = True  # False = selecting in the palette runs it, no drill-down (e.g. /session)
    palette_input: tuple[str, str, str] | None = None  # leading "type free text" row: (prefill, label, desc)

    @property
    def handler_name(self) -> str:
        """Agent method derived from this canonical command root."""
        return f"_cmd_{self.name.removeprefix('/').replace('-', '_')}"

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
        category="Overview",
        aliases=("/h",),
        busy_args=("",),
        palette_description="show all commands",
        help_lines=("/help, /h         show this help",),
    ),
    SlashCommandSpec(
        name="/init",
        description="initialize/check private MO home",
        category="System",
        palette_description="initialize/check ~/.mo private runtime",
        help_lines=("/init             initialize/check private MO home",),
    ),
    SlashCommandSpec(
        name="/doctor",
        busy_args=("layout", "personalization", "personalization --json"),
        description="health and personalization diagnostics",
        category="System",
        subcommands=(
            ("layout", "read-only private-home layout report"),
            ("personalization", "read-only profile, learning, memory, session, cleanup, and recurrence audit"),
            ("--json", "machine-readable general health JSON output"),
        ),
        palette_description="one-shot diagnostics (offline-safe)",
        help_lines=("/doctor           health check; add layout, personalization, or --json for detail",),
    ),
    SlashCommandSpec(
        name="/credentials",
        busy_args=("", "all", "providers", "telegram", "everywhere"),
        description="value-free credential readiness",
        category="System",
        subcommands=(("providers", "provider, image, and embedding credentials"), ("telegram", "Telegram bot credential"), ("everywhere", "Everywhere device credential")),
        palette_description="check credential presence without exposing values",
        help_lines=("/credentials       safe credential status; optionally add a service name",),
    ),
    SlashCommandSpec(
        name="/mail",
        busy_args=("", "status"),
        description="Gmail connection and count status",
        category="Communication",
        subcommands=(("status", "connection and unread count"), ("connect", "connect locally with Google consent"), ("sync", "refresh count and notices"), ("disconnect", "remove local Gmail authority")),
        help_lines=("/mail             Gmail status; connect, sync, or disconnect locally",),
    ),
    SlashCommandSpec(
        name="/update",
        description="fast-forward this checkout when upstream has updates",
        category="System",
        palette_description="fast-forward MO checkout update",
        help_lines=("/update           fast-forward this checkout when upstream has updates",),
    ),
    SlashCommandSpec(
        name="/exit",
        description="quit MO",
        category="System",
        aliases=("/quit", "/q"),
        busy_args=("",),
        help_lines=("/exit, /quit, /q  quit MO",),
    ),
    SlashCommandSpec(
        name="/clear",
        description="clear conversation",
        category="Sessions",
        presentation="notice",
        aliases=("/c",),
        help_lines=("/clear, /c        clear conversation",),
    ),
    SlashCommandSpec(
        name="/status",
        busy_args=("",),
        description="MO/session status with direct work and learning counts",
        category="Overview",
        help_lines=("/status           show MO/session status and work/learning state",),
    ),
    SlashCommandSpec(
        name="/now",
        busy_args=("",),
        description="current work snapshot",
        category="Overview",
        aliases=("/continuity",),
        help_lines=("/now              show current work continuity snapshot",),
    ),
    SlashCommandSpec(
        name="/dashboard",
        busy_args=("", "status", "show"),
        description="MO brain dashboard",
        category="Overview",
        aliases=("/brain",),
        subcommands=(
            ("show", "open connected Dashboard in your browser"),
            ("status", "show bounded dashboard summary"),
            ("checks", "inspect project LSP and recorded verification evidence"),
            ("html", "write private HTML dashboard"),
            ("map", "open MO's interactive code + brain map in the browser"),
        ),
        palette_description="MO Dashboard summary/browser/HTML/map",
        help_lines=(
            "/dashboard, /brain  show MO brain dashboard",
            "                  /dashboard show  open connected MO Dashboard",
            "                  /dashboard html  write private dashboard HTML",
            "                  /dashboard map   open MO's interactive code + brain map",
        ),
    ),
    SlashCommandSpec(
        name="/usage",
        busy_args=("",),
        description="token usage + context savings",
        category="Overview",
        help_lines=("/usage            show token usage, cache hits, and context savings",),
    ),
    SlashCommandSpec(
        name="/heartbeat",
        busy_args=("", "status", "context", "surfaces", "instances", "terminals"),
        description="heartbeat and surface continuity status",
        category="Devices",
        subcommands=(
            ("status", "show latest heartbeat"),
            ("now", "record a heartbeat now"),
            ("context", "show recent surface continuity context"),
            ("instances", "show other recent terminal instances"),
        ),
        help_lines=(
            "/heartbeat        show heartbeat/surface continuity status",
            "                  /heartbeat now     record heartbeat now",
            "                  /heartbeat context show recent surface context",
            "                  /heartbeat instances show other terminal instances",
        ),
    ),
    SlashCommandSpec(
        name="/everywhere",
        description="cross-device continuity and profile-sync setup/status",
        category="Devices",
        subcommands=(
            ("status", "show continuity, coordinator, Android, and profile-sync state"),
            ("setup", "read-only setup and prerequisite plan"),
            ("reconcile", "compare curated profile state without choosing a winner"),
            ("pair android", "create a full-authority Android QR on the serving hub"),
            ("pair android --phone-control", "pair Android with explicit on-device control authority"),
            ("devices", "list paired devices and exact scopes"),
            ("trace", "join continuity delivery, binding, coordinator, and sync evidence"),
            ("disable", "engage the device-local Everywhere kill switch"),
        ),
        help_lines=(
            "/everywhere       cross-device continuity/profile-sync status",
            "                  /everywhere setup | reconcile | pair android [--phone-control] | devices | trace | disable",
        ),
    ),
    SlashCommandSpec(
        name="/send",
        description="send a file through MO's resumable transfer pipe",
        category="Devices",
        palette_input=("/send ", "file :: device…", "send a local file to a paired device"),
        help_lines=(
            "/send <path> :: <device>  send a file to one stable paired target",
        ),
    ),
    SlashCommandSpec(
        name="/transfers",
        description="show, accept, cancel, or retry file transfers",
        category="Devices",
        subcommands=(
            ("accept <id> [path]", "confirm and receive an offered file"),
            ("cancel <transfer-id>", "cancel one queued or hub transfer by opaque ID"),
            ("retry <outbox-id>", "retry one failed sender-custody outbox ID"),
        ),
        help_lines=(
            "/transfers                       show bounded state (never private paths)",
            "/transfers accept <id> [path]    confirm receipt; path is required for named-path cargo",
            "/transfers cancel <transfer-id>  cancel queued custody or a hub transfer",
            "/transfers retry <outbox-id>      retry retained sender custody",
        ),
    ),
    SlashCommandSpec(
        name="/telegram",
        description="Telegram remote gateway status/approval",
        category="Devices",
        subcommands=(
            ("status", "show gateway status"),
            ("queue", "show queued Telegram work"),
            ("chats", "show Telegram chat mappings"),
            ("approve <code>", "approve a pairing code"),
            ("start", "start enabled gateway if its canonical token is present"),
            ("trace", "join Telegram session, provider, tool, heartbeat, and turn evidence"),
            ("disable", "disable gateway for this process"),
        ),
        help_lines=(
            "/telegram         Telegram gateway status/approval",
            "                  /telegram approve <code>",
            "                  /telegram queue | chats | start | trace | disable",
        ),
    ),
    SlashCommandSpec(
        name="/structural-graph",
        busy_args=("", "status", "stats"),
        description="show/build MO structural code graph",
        category="Work",
        aliases=("/sg",),
        subcommands=(
            ("status", "show structural graph status"),
            ("build", "build MO's persisted structural graph"),
            ("refresh", "refresh MO's persisted structural graph"),
            ("export", "write an explicit sanitized graph export"),
            ("explain <node>", "explain a graph node"),
            ("neighbors <node>", "show nearby graph nodes"),
            ("path <source> -> <target>", "show a graph path"),
            ("stats", "show graph stats"),
        ),
        palette_description="structural code graph status/build/query/export",
        help_lines=(
            "/structural-graph, /sg  show/build structural code graph",
            "                  /structural-graph build",
            "                  /structural-graph refresh",
            "                  /structural-graph export [path] [--compat]",
            "                  /structural-graph explain|neighbors|path|stats",
        ),
    ),
    SlashCommandSpec(
        name="/knowledge",
        busy_args=("", "status", "query"),
        description="inspect and query automatically maintained project knowledge",
        category="Work",
        subcommands=(
            ("status", "show automatically maintained knowledge status"),
            ("query <text>", "query indexed docs, capabilities, commands, and graph nodes"),
        ),
        palette_description="automatically maintained source-linked project knowledge",
        help_lines=(
            "/knowledge        automatically maintained source-linked project knowledge",
            "                  /knowledge status | query <text>",
        ),
    ),
    SlashCommandSpec(
        name="/model",
        busy_args=("",),
        description="choose and save provider, model, and thinking",
        category="Personalize",
        presentation="notice",
        subcommands=(),
        help_lines=(
            "/model            choose provider/source, model, and thinking level",
        ),
    ),
    SlashCommandSpec(
        name="/projects",
        busy_args=("",),
        description="list project history",
        category="Sessions",
        palette_description="list project history",
        help_lines=("/projects         list project history",),
    ),
    SlashCommandSpec(
        name="/new",
        description="start new session",
        category="Sessions",
        presentation="notice",
        help_lines=("/new              start a new session",),
    ),
    SlashCommandSpec(
        name="/profile",
        busy_args=("", "status", "facts", "facts *"),
        description="show or edit profile",
        category="Personalize",
        aliases=("/p",),
        subcommands=(
            ("name <name>", "set operator name"),
            ("tools <tool,...>", "set preferred tools"),
            ("provider <provider/model>", "set favorite provider metadata (does not switch runtime)"),
            ("status", "show profile wiring and fact counts"),
            ("facts", "browse, edit, or forget saved profile notes"),
            ("fact add", "add a saved profile note"),
            ("archive-section <heading>", "losslessly archive stale operator prose"),
            ("mine", "review safe learning updates"),
            ("export", "export learning bundle for another MO instance"),
            ("import <path>", "import a learning bundle (dry-run; --confirm applies)"),
        ),
        help_lines=(
            "/profile, /p      show or edit profile",
            "                  /profile status | facts",
            "                  /profile fact add <category> :: <fact>",
            "                  /profile fact update <id> <category> :: <fact>",
            "                  /profile fact forget <id> --confirm",
            "                  /profile archive-section <heading> [--confirm]",
            "                  /profile name <name>[/<alias>]",
            "                  /profile tools <tool,...>",
            "                  /profile provider <provider/model>",
            "                  /profile mine    review safe learning updates",
            "                  /profile export [path] | import <path> [--confirm]",
        ),
    ),
    SlashCommandSpec(
        name="/learning",
        description="see what MO learned and review new suggestions",
        category="Personalize",
        subcommands=(
            ("status", "show active learning and pending suggestions"),
            ("more", "optional scanning, consolidation and skill imports"),
            ("suggestions", "scan recent feedback for suggestions"),
            ("details", "explain one pending suggestion by number"),
            ("confirm", "approve one pending suggestion by number"),
            ("dismiss", "dismiss one pending suggestion by number"),
            ("reconcile", "consolidate confirmed learning"),
            ("inspect", "stage and risk-scan a skill source"),
            ("use", "use a safe staged source for one turn"),
            ("promote", "promote a reviewed candidate to a pack"),
            ("candidates", "list staged skill imports"),
        ),
        help_lines=(
            "/learning         select a suggestion, review it, then Approve or Dismiss",
            "                  Active learning: review or undo accepted suggestions",
            "                  More actions: optional scans, consolidation and imports",
            "                  /learning status | suggestions",
            "                  /learning details <number>",
            "                  /learning confirm <number> | dismiss <number>",
            "                  /learning reconcile [deep]",
            "                  /learning inspect|use|promote <source-or-id>",
            "                  /learning candidates  list staged skill imports",
        ),
    ),
    SlashCommandSpec(
        name="/goal",
        description="autonomous goal mode",
        category="Work",
        aliases=("/g",),
        busy_args=("", "status", "info", "stop", "cancel", "abort"),
        subcommands=(
            ("stop", "stop active goal"),
            ("status", "show goal progress"),
        ),
        palette_input=("/goal ", "new goal…", "type autonomous goal"),
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
        busy_args=("*",),  # Existing independently admitted review worker.
        description="Project Review Team — report worktree evidence; correct confirmed committed violations",
        palette_description="auto: report uncommitted work; otherwise check and correct HEAD",
        category="Work",
        subcommands=(
            (".", "report current uncommitted changes in this project"),
            ("report", "show target-aware review activity"),
            ("<target>", "committed refs correct confirmed findings; paths report"),
        ),
        help_lines=(
            "/prt              pre-commit report; clean HEAD correction and reassessment",
            "                  /prt .           report current uncommitted changes",
            "                  /prt report      show target-aware PRT history",
            "                  /prt <target>    committed refs correct; paths report",
        ),
    ),
    SlashCommandSpec(
        name="/role",
        busy_args=("",),
        description="activate, inspect, or run work governed by a named role",
        category="Work",
        help_lines=(
            "/role activate <name>      activate a conversational role",
            "/role show                 open the live Project Architect workspace",
            "/role status               show the active role and team checklist",
            "/role off                  leave the active role",
            "/role <name> <objective>  run a role-governed background worker",
            "                  /role             list available roles and syntax",
        ),
    ),
    SlashCommandSpec(
        name="/schedule",
        busy_args=("", "list", "status", "show *"),
        description="manage persistent automations and compute jobs",
        category="Automations",
        aliases=("/cron",),
        subcommands=(
            ("list", "list scheduled tasks"),
            ("add <when> :: <task>", "create a scheduled MO task"),
            ("edit <id> <when> :: <task>", "update a scheduled task"),
            ("pause <id>", "pause a task"),
            ("resume <id>", "resume a task"),
            ("run <id>", "run on the next scheduler tick"),
            ("remove <id>", "remove a task"),
        ),
        palette_description="manage scheduled tasks and automations",
        help_lines=(
            "/schedule         list persistent scheduled tasks",
            "                  /schedule add <when> :: <task>",
            "                  /schedule edit <id> <when> :: <task>",
            "                  /schedule pause|resume|run|remove <id>",
        ),
    ),
    SlashCommandSpec(
        name="/visualize",
        palette_input=("/visualize ", "file or directory…", "choose a path to diagram"),
        description="render a file or directory as a Mermaid/ASCII diagram",
        category="Work",
        help_lines=(
            "/visualize <file-or-dir> [mermaid|ascii]   diagram JSON/YAML/TOML/Markdown or a tree",
        ),
    ),
    SlashCommandSpec(
        name="/show",
        busy_args=("",),
        description="toggle and save transcript visibility (reasoning / tool calls)",
        category="Personalize",
        subcommands=(
            ("reasoning", "toggle reasoning visibility"),
            ("tools", "toggle tool-call visibility"),
            ("all", "toggle reasoning and tools visibility"),
        ),
        help_lines=(
            "/show                      show current visibility state",
            "                  /show <reasoning|tools|all> [on|off]   toggle visibility",
        ),
    ),
    SlashCommandSpec(
        name="/desktop",
        description="summon the MO Desktop companion (also Win+Alt+M)",
        category="Devices",
        subcommands=(
            ("window", "launch/show the MO Desktop window"),
            ("trace", "show joined MO Desktop lifecycle, session, turn, heartbeat, provider, and tool evidence"),
        ),
        palette_description="summon the MO Desktop companion",
        help_lines=(
            "/desktop          summon the MO Desktop companion window",
            "                  /desktop window    launch/show MO Desktop (also Win+Alt+M)",
            "                  /desktop trace     show joined private desktop diagnostics",
        ),
    ),
    SlashCommandSpec(
        name="/hints",
        description="Toggle and save rotating hint tips on the idle line.",
        category="Personalize",
        subcommands=(
            ("on", "show rotating hints on idle line"),
            ("off", "hide hints, show normal idle"),
        ),
        palette_description="toggle rotating idle hints",
        help_lines=(
            "/hints            Toggle rotating hint tips on the idle line.",
            "                  /hints on        enable idle hints",
            "                  /hints off       disable idle hints",
            "                  Non-comment lines in the optional MO-home hints.txt replace defaults; restart MO to reload.",
        ),
    ),
    SlashCommandSpec(
        name="/activity",
        busy_args=("", "status"),
        description="Show, hide, and save true Goal, Background, and PRT worker activity.",
        category="Personalize",
        subcommands=(
            ("on", "show true worker activity"),
            ("off", "hide the worker activity panel"),
            ("status", "list active true workers"),
        ),
        palette_description="control the true-worker activity panel",
        help_lines=(
            "/activity         show worker activity status",
            "                  /activity on|off toggle the panel",
        ),
    ),
    SlashCommandSpec(
        name="/workspace",
        description="Split this TUI into MO and ordinary terminal panes.",
        category="Sessions",
        subcommands=(
            ("new", "choose This machine or MO host"),
            ("status", "list terminal panes and states"),
            ("focus <number>", "focus a pane number"),
            ("next", "focus the next pane"),
            ("prev", "focus the previous pane"),
            ("close", "close only the focused terminal pane"),
            ("single", "close this project's terminal panes and return to main MO"),
        ),
        palette_description="manage split terminal panes in this window",
        help_lines=(
            "/workspace        manage the selected project's terminal panes",
            "                  /workspace new choose This machine or MO host",
            "                  /workspace new local add a local blank terminal",
            "                  /workspace status list terminal panes",
            "                  /workspace focus N focus pane N",
            "                  /workspace next|prev move focus",
            "                  /workspace close close focused terminal pane",
            "                  /workspace single close project panes and return to main MO",
            "                  Alt+Left/Right move pane focus",
            "                  Alt+Up/Down, Alt+Home/End scroll focused pane history",
            "                  Alt+F toggle focused pane full window",
            "                  Alt+X close the focused added terminal",
            "                  Ctrl+B show or hide the terminal side panel",
            "                  In the side panel, / focuses a pane and starts slash input",
        ),
    ),
    SlashCommandSpec(
        name="/terminal",
        description="Open a real terminal inside MO (codex/claude/shell/any command).",
        category="Sessions",
        aliases=("/term",),
        palette_description="run a real terminal/command inside MO",
        help_lines=(
            "/terminal         open a shell inside MO (also Ctrl+T)",
            "                  /terminal codex   run codex, then return to MO",
            "                  /terminal <cmd>   run any command, then return to MO",
        ),
    ),
    SlashCommandSpec(
        name="/undo",
        description="remove last exchange from conversation context (scrollback stays)",
        category="Sessions",
        presentation="notice",
        aliases=("/u",),
        help_lines=("/undo, /u         remove last exchange from context; scrollback stays",),
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
        description="manage local and explicitly portable sessions",
        category="Sessions",
        aliases=("/s",),
        palette_description="open the saved-session picker",
        subcommands=(
            ("save", "save current session"),
            ("list", "list saved sessions"),
            ("remove", "remove a session"),
            ("share", "make one named session portable"),
            ("unshare", "keep one portable session local again"),
        ),
        help_lines=(
            "/session, /s      manage local saved sessions",
            "                  /session share|unshare <name>  control portable access",
        ),
    ),
    SlashCommandSpec(
        name="/resume",
        description="resume this or the latest Terminal session",
        category="Sessions",
        palette_description="resume Terminal session",
        help_lines=("/resume           resume this or the latest Terminal session",),
    ),
    SlashCommandSpec(
        name="/game",
        description="manage the active Terminal Game Collaboration project",
        category="Projects",
        subcommands=(
            ("start", "start or bind the current project"),
            ("status", "show project state without changing it"),
            ("review", "review decisions, questions, and proposals"),
            ("pause", "pause game context and preserve the record"),
            ("resume", "resume a saved project explicitly"),
            ("ask", "record one focused project question"),
            ("decide", "record an answer to a project question"),
            ("propose", "record a proposed implementation scope"),
            ("approve", "approve one exact proposal"),
            ("reject", "reject one proposal without deleting history"),
            ("stop", "leave the mode without deleting the project"),
        ),
        palette_description="manage Terminal-only Game Collaboration",
        help_lines=(
            "/game             manage the active Terminal Game Collaboration project",
            "                  /game start|status|review|pause|resume|stop",
            "                  /game ask|decide|propose|approve|reject <id> ...",
        ),
    ),
    SlashCommandSpec(
        name="/reload",
        description="reload config, providers, prompts, and profile",
        category="System",
        presentation="notice",
        palette_description="reload runtime configuration",
        help_lines=("/reload           reload config, providers, prompts, and profile",),
    ),
    SlashCommandSpec(
        name="/settings",
        busy_args=("",),
        description="show current settings",
        category="Personalize",
        help_lines=("/settings         show current settings",),
    ),
    SlashCommandSpec(
        name="/skin",
        busy_args=("",),
        description="Switch UI theme",
        category="Personalize",
        presentation="notice",
        palette_description="show or switch UI theme",
        help_lines=("/skin [name]      show or switch UI theme",),
    ),
    SlashCommandSpec(
        name="/skills",
        description="list active local skill packs",
        category="Personalize",
        palette_description="list packs or select one for your next request",
        help_lines=("/skills [name]    list packs or inspect one",),
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


# ── Canonical taxonomy: ONE source for both /help and the command palette ──
# Seven user-meaningful groups replace the old implementation-oriented buckets.
# Help and palette tabs are both derived from these registered categories.
CATEGORY_ORDER: tuple[str, ...] = (
    "Overview",
    "Work",
    "Sessions",
    "Personalize",
    "Devices",
    "Automations",
    "System",
)


def _ordered_categories(command_by_name: dict[str, SlashCommandSpec]) -> list[str]:
    """Canonical categories first, then any extension-only category, in order."""
    ordered = list(CATEGORY_ORDER)
    for spec in command_by_name.values():
        category = spec.category or "Overview"
        if category not in ordered:
            ordered.append(category)
    return ordered


def resolve_slash_command(command: str, *, include_extensions: bool = True) -> str:
    """Resolve one command or alias to its canonical registered root."""
    root = str(command or "").strip().split()[0] if str(command or "").strip() else ""
    if not root:
        return ""
    aliases = {
        alias: spec.name
        for spec in _all_commands(include_extensions=include_extensions)
        for alias in spec.aliases
    }
    return aliases.get(root, root)


def slash_command_spec(command: str, *, include_extensions: bool = True) -> SlashCommandSpec | None:
    """Return canonical metadata for one command or alias."""
    root = resolve_slash_command(command, include_extensions=include_extensions)
    return _command_by_name(include_extensions=include_extensions).get(root)


def _command_hidden(command: str) -> bool:
    """Return True for commands that should not appear in user-facing recents."""
    spec = slash_command_spec(command)
    return spec is None or not spec.palette


def slash_command_exists(command: str, *, include_extensions: bool = True) -> bool:
    """Return True when *command* is a known command or alias."""
    return slash_command_spec(command, include_extensions=include_extensions) is not None


def slash_aliases(*, include_extensions: bool = True, visible_only: bool = True) -> dict[str, str]:
    """Return alias -> canonical command mappings for completion/display."""
    aliases: dict[str, str] = {}
    for spec in _all_commands(include_extensions=include_extensions):
        if visible_only and not spec.palette:
            continue
        for alias in spec.aliases:
            aliases[alias] = spec.name
    return aliases


def slash_command_description(command: str, *, include_extensions: bool = True) -> str:
    spec = slash_command_spec(command, include_extensions=include_extensions)
    return spec.description if spec else ""


def _command_root(command: str, *, include_extensions: bool = True) -> str:
    return resolve_slash_command(command, include_extensions=include_extensions)


def subcommands_for(command: str, *, agent: Any = None, include_extensions: bool = True) -> list[tuple[str, str]]:
    """Return subcommands for a canonical command or alias."""
    root = _command_root(command, include_extensions=include_extensions)
    spec = _command_by_name(include_extensions=include_extensions).get(root)
    dynamic = dynamic_argument_subcommands(command, agent=agent, include_extensions=include_extensions)
    static = list(spec.subcommands) if spec else []
    return dynamic + [item for item in static if item[0] not in {arg for arg, _desc in dynamic}]


def dynamic_argument_items(command: str, *, agent: Any = None, include_extensions: bool = True) -> list[tuple[str, str, str, str]]:
    """Return dynamic argument rows for command discovery.

    Rows are ``(argument, label, description, kind)``. They are read-only UI
    metadata: command execution still belongs to Agent slash handlers. Keep
    dynamic runtime sources here so inline Tab completion and the command
    palette cannot drift.
    """
    root = _command_root(command, include_extensions=include_extensions)
    if root == "/profile" and str(command or "").strip() == "/profile facts":
        from core.profile.facts import list_profile_facts
        facts = list_profile_facts(profile=getattr(agent, "profile", None), config=getattr(agent, "config", None))
        return [
            (f"facts {item.id}", item.fact, item.category.title(), "command") for item in facts
        ] + [("fact add", "Add a note…", "choose a category and write a saved note", "command")]
    if root == "/learning":
        from core.learning.review import describe_review_item, learning_review_items

        value = str(command or "").strip()
        action = value.partition(" ")[2].strip()
        if action == "more":
            return [
                ("suggestions", "Scan recent feedback", "look for new suggestions", "command"),
                ("reconcile", "Consolidate learning", "merge repeated learning", "command"),
                ("candidates", "Skill imports", "show staged external sources", "command"),
                ("inspect ", "Inspect a source…", "type a source to stage", "insert"),
                ("use ", "Use a source…", "type a source for one turn", "insert"),
                ("promote ", "Promote an import…", "type a reviewed import reference", "insert"),
                ("review", "Back to learning", "browse suggestions", "submenu"),
            ]
        if action not in {"", "review", "pending", "list", "active", "details", "confirm", "dismiss"}:
            return []
        active = action == "active"
        items = learning_review_items(
            getattr(agent, "profile", None), config=getattr(agent, "config", {}) or {}, active=active,
        )
        rows = []
        for index, item in enumerate(items, 1):
            detail = describe_review_item(item)
            rows.append((f"details {detail['ref']}", f"{index}. {detail['label']}", detail["summary"], "command"))
        if not items:
            rows.append(("status", "No active learning" if active else "Nothing pending", "show learning status", "command"))
        rows.append(("review" if active else "active", "Pending suggestions" if active else "Active learning", "browse learning items", "submenu"))
        rows.extend([
            ("status", "Learning status", "automatic learning, active and pending", "command"),
            ("more", "More actions", "optional scans, consolidation and imports", "submenu"),
        ])
        return rows
    if root == "/skin":
        try:
            from .theming import available_skins, get_skin_name

            current = get_skin_name()
            rows: list[tuple[str, str, str, str]] = []
            for name in available_skins():
                desc = "current skin" if name == current else "switch UI theme"
                rows.append((name, name, desc, "command"))
            return rows
        except Exception:
            return []
    if root == "/role":
        action = str(command or "").strip().partition(" ")[2].split(maxsplit=1)
        activate = "use" if action and action[0] == "use" else "activate"
        from core.skills import default_skill_roots, list_roles

        project = getattr(agent, "project_cwd", None)
        roots = default_skill_roots(
            project, getattr(agent, "runtime_home", None),
            profile=getattr(agent, "profile", None),
            config=getattr(agent, "config", None), maintain=False,
        )
        roles = list_roles(roots, profile=getattr(agent, "profile", None), project_cwd=project)
        rows = []
        seen = set()
        for role in roles:
            identity = role.role.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            rows.append((f"{activate} {role.role}", role.name, role.description, "command"))
        rows.extend([
            ("show", "Open role workspace", "show Project Architect's live specialist view", "command"),
            ("status", "Active role and team", "show the current role and specialist checklist", "command"),
            ("off", "Leave role", "return this conversation to MO", "command"),
        ])
        return rows
    if root == "/model":
        try:
            from core.provider.model_catalog import model_command_menu_items

            return [
                (item.value, item.label, item.desc, item.kind)
                for item in model_command_menu_items(command, agent)
            ]
        except Exception:
            return []
    if root == "/session":
        manager = getattr(agent, "_sessions", None) if agent is not None else None
        if manager is None:
            return []
        sessions = list(manager.list_sessions(surface="terminal"))
        current = str(getattr(manager, "current_name", "") or "")
        value = str(command or "").strip()
        remainder = value[len(root):].strip() if value.lower().startswith(root) else ""
        action = remainder.split(maxsplit=1)[0].lower() if remainder else ""

        def describe(session: dict[str, Any]) -> str:
            turns = int(session.get("turns") or 0)
            details = [
                str(session.get("age") or "unknown"),
                f"{turns} turn{'s' if turns != 1 else ''}",
            ]
            preview = str(session.get("preview") or "").strip()
            if preview:
                details.append(f"First: {preview}")
            name = str(session["name"])
            if name == current:
                details.append("current")
            elif session.get("portable"):
                details.append("portable")
            return " · ".join(details)

        if action in {"remove", "share", "unshare"}:
            rows = []
            for session in sessions:
                name = str(session["name"])
                portable = bool(session.get("portable"))
                if action == "share" and portable:
                    continue
                if action == "share":
                    from core.session.sessions import PortableConversationValidationError, portable_session_name
                    try:
                        portable_session_name(name)
                    except PortableConversationValidationError:
                        continue
                if action == "unshare" and not portable:
                    continue
                rows.append((f"{action} {name}", name, describe(session), "command"))
            return rows

        rows = [("save", "Save current", "save the current conversation", "command")]
        for session in sessions:
            name = str(session["name"])
            if name == current:
                continue
            rows.append((name, name, describe(session), "command"))
        if sessions:
            rows.extend(
                (
                    ("share", "Share session…", "make a saved session portable", "submenu"),
                    ("unshare", "Unshare session…", "keep a portable session local", "submenu"),
                    ("remove", "Remove session…", "delete a saved session", "submenu"),
                )
            )
        return rows
    if root == "/skills":
        try:
            from core.skills import default_skill_roots, visible_skill_packs

            cfg = getattr(agent, "config", {}) if agent is not None else {}
            cfg = cfg if isinstance(cfg, dict) else {}
            roots = default_skill_roots(
                getattr(agent, "project_cwd", None),
                getattr(agent, "runtime_home", None),
                profile=getattr(agent, "profile", None),
                config=cfg,
                maintain=False,
            )
            rows = visible_skill_packs(
                roots,
                profile=getattr(agent, "profile", None),
                config=cfg,
            )
            return [
                (
                    row.name,
                    row.name,
                    (
                        f"use on next request · {row.description}"
                        if row.active and row.description
                        else "use on next request"
                        if row.active
                        else f"unavailable · {'; '.join(row.issues)}"
                    ),
                    "insert" if row.active else "command",
                )
                for row in rows
            ]
        except Exception:
            return []
    return []


def dynamic_argument_subcommands(command: str, *, agent: Any = None, include_extensions: bool = True) -> list[tuple[str, str]]:
    return [
        (argument, description)
        for argument, _label, description, _kind in dynamic_argument_items(
            command,
            agent=agent,
            include_extensions=include_extensions,
        )
    ]


def dynamic_palette_children(command: str, *, agent: Any = None, include_extensions: bool = True) -> list[tuple[str, str, str, str]]:
    root = _command_root(command, include_extensions=include_extensions)
    return [
        (
            # Selecting an active skill is a composer action, not another
            # slash-command detail view. Prefill its exact loader-visible name
            # so the existing text-trigger selector owns the next turn.
            f"{argument}: " if root == "/skills" and kind == "insert" else f"{root} {argument}",
            label,
            description,
            kind,
        )
        for argument, label, description, kind in dynamic_argument_items(
            command,
            agent=agent,
            include_extensions=include_extensions,
        )
    ]


def build_help_text(*, include_extensions: bool = True) -> str:
    lines = ["MO Agent commands:"]
    command_by_name = _command_by_name(include_extensions=include_extensions)
    for category in _ordered_categories(command_by_name):
        specs = [
            spec for spec in command_by_name.values()
            if (spec.category or "Overview") == category and spec.help and spec.help_lines
        ]
        if not specs:
            continue
        lines.append("")
        lines.append(category)
        for spec in specs:
            for line in spec.help_lines:
                lines.append(f"  {line}")
    return "\n".join(lines)


SLASH_COMMAND_HELP = build_help_text(include_extensions=False)


def build_palette_categories(*, include_extensions: bool = True) -> list[tuple[str, list[tuple[str, str]]]]:
    """Palette tabs DERIVED from the canonical taxonomy (see CATEGORY_ORDER).

    ``Recent`` is a synthetic first tab filled at runtime from usage. Other tabs
    list palette-visible commands only; internal commands can remain callable
    without being promoted in the UI."""
    command_by_name = _command_by_name(include_extensions=include_extensions)
    categories: list[tuple[str, list[tuple[str, str]]]] = [("Recent", [])]
    for category in _ordered_categories(command_by_name):
        entries: list[tuple[str, str]] = []
        for spec in command_by_name.values():
            if (spec.category or "Overview") != category or not spec.palette:
                continue
            entries.append((spec.name, spec.palette_desc))
        if entries:
            categories.append((category, entries))
    return categories


def palette_children(command_root: str, *, agent: Any = None, include_extensions: bool = True) -> list[tuple[str, str, str, str]]:
    """Palette submenu rows derived from the canonical command registry.

    Rows are ``(value, label, description, kind)``. Runtime-backed trees such as
    models, skins, and saved sessions come from ``dynamic_palette_children`` so
    the palette and inline completion cannot drift into separate implementations.
    """
    root = _command_root(command_root, include_extensions=include_extensions)
    spec = _command_by_name(include_extensions=include_extensions).get(root)
    if not spec or not spec.palette_submenu:
        return []
    value = str(command_root or "").strip()
    parts = value.split(maxsplit=1)
    value = root + (f" {parts[1]}" if len(parts) > 1 else "")
    if root == "/help":
        return []  # The interactive help endpoint opens the canonical tabs.
    if root == "/session" and value not in {root, f"{root} remove", f"{root} share", f"{root} unshare"}:
        return []  # A saved session or complete action is a leaf, not another menu.
    # Ordinary registry subcommands are terminal actions. Re-expanding their
    # root menu makes a fully typed `/cmd arg` require extra Enter/arrow steps.
    # Runtime-backed multi-level trees resolve their own leaf actions.
    dynamic_roots = {"/model", "/session", "/learning", "/profile"}
    if value != root and root not in dynamic_roots:
        return []
    dynamic = (
        dynamic_palette_children(value, agent=agent, include_extensions=include_extensions)
        if root in dynamic_roots or value == root
        else []
    )
    if dynamic or root == "/learning":
        return dynamic
    if value != root:
        return []
    if not spec.subcommands and not spec.palette_input and not spec.palette_entries:
        return []
    rows: list[tuple[str, str, str, str]] = []
    if spec.palette_input:
        prefill, label, desc = spec.palette_input
        rows.append((prefill, label, desc, "insert"))
    rows.append((spec.name, spec.name, spec.palette_desc, "command"))
    for label, desc in spec.subcommands:
        prefix, required, _tail = label.partition("<")
        value = f"{spec.name} {prefix.rstrip()}".rstrip() + (" " if required else "")
        rows.append((value, label, desc, "insert" if required else "command"))
    seen = {row[0].strip() for row in rows}
    for value, desc in spec.palette_entries:
        if value.strip() not in seen:
            rows.append((value, value.removeprefix(spec.name).strip(), desc, "command"))
            seen.add(value.strip())
    return rows


PALETTE_CATEGORIES = build_palette_categories(include_extensions=False)
DEFAULT_PALETTE_CATEGORY = 1


def slash_command_names() -> list[str]:
    commands = _all_commands()
    names = [spec.name for spec in commands]
    names.extend(alias for spec in commands for alias in spec.aliases)
    return sorted(set(names))


def slash_command_with_desc(*, visible_only: bool = True) -> list[tuple[str, str]]:
    """Return (command, description) pairs for suggestion display."""
    specs = _all_commands()
    if visible_only:
        specs = tuple(spec for spec in specs if spec.palette)
    return [(spec.name, spec.description) for spec in specs]
