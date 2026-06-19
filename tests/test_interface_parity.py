"""Interface parity / referential-integrity guardrails.

These lock in invariants that IFDEV05 kept rediscovering by hand (and that MO
once broke): every TUI mixin that probes terminal size must actually expose the
shared helper; every `class:` style referenced in the UI must exist in the
theme; and the slash-command registry must be internally consistent and fully
dispatchable. Catching these in pytest means they can't silently regress
between interface audits.
"""
from __future__ import annotations

import glob
import re

import pytest


# ── 1. Terminal-metrics availability (would have caught the AttributeError
#       MO introduced when it half-finished the column-getter dedup) ──

def test_terminal_metrics_helper_present_on_all_size_probing_mixins():
    from interface.terminal_metrics import TerminalMetricsMixin
    from interface.display_delegates import DisplayDelegatesMixin
    from interface.response_mixin import ResponseMixin
    from interface.transcript_state import TranscriptStateMixin

    for mixin in (DisplayDelegatesMixin, ResponseMixin, TranscriptStateMixin):
        assert issubclass(mixin, TerminalMetricsMixin), (
            f"{mixin.__name__} calls self._terminal_columns()/_terminal_rows() "
            "but does not inherit TerminalMetricsMixin — it will AttributeError "
            "in any context that isn't the full MoTui composition (e.g. tests)."
        )
        assert callable(getattr(mixin, "_terminal_columns", None))
        assert callable(getattr(mixin, "_terminal_rows", None))


def test_motui_inherits_terminal_metrics_via_mixins():
    from interface.main_terminal import MoTui
    from interface.terminal_metrics import TerminalMetricsMixin

    assert issubclass(MoTui, TerminalMetricsMixin)
    # Single definition — not re-declared on MoTui itself (the dedup point).
    assert "_terminal_columns" not in vars(MoTui)


# ── 2. Theme referential integrity: every class: reference resolves ──

def test_every_referenced_theme_class_is_defined():
    from interface.theme import TUI_STYLE_DICT

    defined = set(TUI_STYLE_DICT.keys())
    referenced: set[str] = set()
    dynamic_prefixes: set[str] = set()
    for path in glob.glob("interface/*.py"):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.findall(r"class:([a-zA-Z0-9_\-]+)", text):
            referenced.add(m)
        # f-string built classes like f"class:prt-{severity}" — record the stem
        for m in re.findall(r'class:([a-zA-Z0-9_\-]*)\{', text):
            dynamic_prefixes.add(m)

    missing = {
        r for r in referenced
        if r not in defined
        and not any(r.startswith(p) for p in dynamic_prefixes if p)
    }
    assert not missing, f"class: references with no theme key: {sorted(missing)}"


# ── 3. Slash-command registry integrity (all dispatchable, no dangling
#       aliases/subcommands) — the "do all slash commands work" check ──

def test_slash_command_registry_is_consistent():
    from interface import command_registry as cr

    slash = {k.lstrip("/") for k in cr.SLASH_COMMANDS}
    by_name = {k.lstrip("/") for k in cr.COMMAND_BY_NAME}
    assert slash == by_name, (
        f"SLASH_COMMANDS vs COMMAND_BY_NAME drift: "
        f"only-slash={slash - by_name}, only-by-name={by_name - slash}"
    )

    for alias, target in cr.SLASH_ALIASES.items():
        assert target.lstrip("/") in slash, f"alias {alias!r} -> unknown command {target!r}"

    for parent in cr.SLASH_SUBCOMMANDS:
        assert parent.lstrip("/") in slash, f"subcommands for unknown parent {parent!r}"


def test_every_slash_command_is_referenced_in_dispatch_code():
    from interface import command_registry as cr

    sources = []
    for path in glob.glob("core/**/*.py", recursive=True) + glob.glob("interface/*.py"):
        try:
            with open(path, encoding="utf-8") as fh:
                sources.append(fh.read())
        except OSError:
            continue
    blob = "\n".join(sources)

    undispatched = [
        name for name in (k.lstrip("/") for k in cr.SLASH_COMMANDS)
        if f'"/{name}"' not in blob and f"'/{name}'" not in blob
        and f'"{name}"' not in blob and f"'{name}'" not in blob
    ]
    assert not undispatched, f"slash commands never referenced in dispatch code: {undispatched}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
