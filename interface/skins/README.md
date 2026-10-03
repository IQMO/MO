# MO skins

This directory is the only built-in skin-definition authority. Each skin owns
one file and exports one complete, validated `SKIN`. The immutable registry in
`__init__.py` is the only registration list.

To add a built-in skin:

1. Copy `template.py.example` to `<skin_id>.py`; use a lowercase identifier
   containing only letters, numbers, and underscores.
2. Replace every semantic `#RRGGBB` value. `brand_glow` automatically colors
   Desktop's Glow and Hybrid window effects. Do not add padding, radius, effect
   intensity, font, or per-surface constants; those belong to Settings and the
   surface bridges.
3. Import `SKIN` under a clear constant name in `__init__.py` and add the ID and
   constant to `BUILTIN_SKINS` once.
4. Run the focused interface and Desktop visual tests. The model rejects
   incomplete or malformed colors at import, and the Desktop state rejects an
   incomplete bridge projection.

That single registration makes the skin available to `/skin`, the terminal
palette, Desktop Settings previews and live switching, Everywhere tokens,
charts, the code map, and MO Design. Do not add another registry or a
surface-specific color branch.

Custom palettes use the existing [`theming.py`](../theming.py) owner and the
same validated `Skin` model. Desktop Settings can preview, save, select, and
delete these palettes without adding a Python module or registry entry.
`build_custom_skin()` accepts the color roles in `_CUSTOM_PALETTE_KEYS`, including
an independent `separator` color; omitting it retains the derived color.

The active private state home stores the selected ID in `skin` and validated
custom palette records in `skins.json`. Both paths honor `MO_STATE_HOME` through
the canonical state-path owner. Built-in definitions remain in this directory;
private palette records contain color data and never load Python code.
