"""Small command-palette compatibility wrappers for `MoTui`."""
from __future__ import annotations

from .command_palette import PaletteItem, model_palette_items, palette_children_for_item


class PaletteMixin:
    def _palette_children_for_item(self, item: PaletteItem) -> list[PaletteItem]:
        return palette_children_for_item(item, self.agent)

    def _model_palette_items(self) -> list[PaletteItem]:
        return model_palette_items(self.agent)
