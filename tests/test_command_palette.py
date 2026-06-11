from types import SimpleNamespace

from interface.command_palette import PaletteItem, model_palette_items, palette_children_for_item


def test_model_palette_items_reflect_agent_provider_chain_and_active_provider():
    agent = SimpleNamespace(
        providers=[
            SimpleNamespace(name="opencode", model="deepseek-v4-pro"),
            SimpleNamespace(name="gemini", model="gemini-flash"),
        ],
        provider_index=1,
    )

    items = model_palette_items(agent)

    assert items == [
        PaletteItem("/model 1", "[1] opencode", "opencode / deepseek-v4-pro"),
        PaletteItem("/model 2", "[2] gemini", "current · gemini / gemini-flash"),
    ]


def test_palette_children_preserve_protected_command_drilldowns():
    agent = SimpleNamespace(providers=[], provider_index=0)

    assert palette_children_for_item(PaletteItem("/session", "/session", "manage sessions"), agent) == []
    assert palette_children_for_item(PaletteItem("/think", "/think", "reasoning"), agent) == [
        PaletteItem("/think high", "high", "set reasoning high"),
        PaletteItem("/think medium", "medium", "set reasoning medium"),
        PaletteItem("/think low", "low", "set reasoning low"),
    ]
    assert palette_children_for_item(PaletteItem("/goal", "/goal", "autonomous goal mode"), agent)[0] == PaletteItem(
        "/goal ", "new goal…", "type autonomous goal", "insert"
    )
    assert palette_children_for_item(PaletteItem("/ghost", "/ghost", "removed public command"), agent) == []
    assert palette_children_for_item(PaletteItem("/gp", "/gp", "prompt enhancer"), agent)[0] == PaletteItem(
        "/gp ", "enhance prompt…", "type rough prompt", "insert"
    )
    assert palette_children_for_item(PaletteItem("/handoff", "/handoff", "context handoff"), agent) == []


def test_palette_children_unknown_command_has_no_submenu():
    agent = SimpleNamespace(providers=[], provider_index=0)

    assert palette_children_for_item(PaletteItem("/status", "/status", "agent status"), agent) == []
