from interface.theme import TUI_STYLE_DICT


EXPECTED_STYLE_KEYS = [
    "separator",
    "footer",
    "footer-logo",
    "spinner",
    "activity",
    "goal-detail",
    "task-done",
    "task-active",
    "task-blocked",
    "task-pending",
    "task-info",
    "logo",
    "user-msg",
    "mo-marker",
    "mo-response",
    "response-heading",
    "response-bullet-marker",
    "response-bullet-head",
    "response-bullet-rest",
    "response-code",
    "palette-title",
    "palette-category",
    "palette-selected",
    "palette-command",
    "palette-desc",
    "palette-hint",
    "ghost-frame",
    "ghost-hint",
    "ghost-user",
    "ghost-thinking",
    "ghost-gap",
    "ghost-response",
    "ghost-route",
    "ghost-route-blocked",
    "dim",
    "info",
    "input-placeholder",
    "notification-idle",
    "notification-prt",
    "notification-goal",
    "notification-worker",
    "notification-critical",
    "prt-header",
    "prt-critical",
    "prt-major",
    "prt-minor",
    "prt-info",
    "prt-clean",
    "prt-summary",
]


def test_tui_theme_preserves_visual_freeze_style_keys():
    assert list(TUI_STYLE_DICT) == EXPECTED_STYLE_KEYS


def test_tui_theme_preserves_critical_visual_values():
    assert TUI_STYLE_DICT["footer-logo"] == "#00cccc bold"
    assert TUI_STYLE_DICT["user-msg"] == "bg:#1a3a4a #dddddd"
    assert TUI_STYLE_DICT["mo-marker"] == "#00cccc bold"
    assert TUI_STYLE_DICT["ghost-response"] == "#8fa7b8"
    assert TUI_STYLE_DICT["notification-idle"] == "#00cccc italic"
    assert TUI_STYLE_DICT["notification-prt"] == "#bb86fc bold"
    assert TUI_STYLE_DICT["notification-goal"] == "#ffae42 bold"
    assert TUI_STYLE_DICT["notification-worker"] == "#888888"
    assert TUI_STYLE_DICT["notification-critical"] == "#ff4444 bold"
    assert TUI_STYLE_DICT["prt-header"] == "#bb86fc bold"
    assert TUI_STYLE_DICT["prt-major"] == "#ffd166 bold"
    assert TUI_STYLE_DICT["prt-clean"] == "#00cc88 bold"
