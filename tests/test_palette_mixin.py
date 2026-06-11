from types import SimpleNamespace

from interface.command_palette import PaletteItem
from interface.palette_mixin import PaletteMixin


class PaletteHarness(PaletteMixin):
    def __init__(self):
        self.agent = SimpleNamespace(
            providers=[
                SimpleNamespace(name="opencode", model="deepseek-v4-pro"),
                SimpleNamespace(name="gemini", model="gemini-flash"),
            ],
            provider_index=0,
        )


def test_palette_mixin_preserves_children_and_model_wrappers():
    harness = PaletteHarness()

    assert harness._palette_children_for_item(PaletteItem("/session", "/session", "manage sessions")) == []
    assert harness._palette_children_for_item(PaletteItem("/goal", "/goal", "goal"))[0].value == "/goal "
    assert harness._model_palette_items()[1].value == "/model 2"
