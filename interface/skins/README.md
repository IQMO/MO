# MO skins

Choose `default`, `dracula`, `silver` or `cold` with `/skin`, the Terminal's
Command Center or Desktop Settings. The selected skin supplies colors across
Terminal, Desktop, Everywhere, charts, the code map and MO Design.

Desktop Settings can preview, save, select and delete custom palettes. The
separator/input-border color can be selected independently; leaving it unset
keeps the palette's derived color. The skin's glow color also applies to
Desktop's Glow and Hybrid window effects. Size, spacing, fonts and effect
intensity remain separate Settings controls.

The active MO state home stores the selected ID in `skin` and custom palettes
in `skins.json`. These paths honor `MO_STATE_HOME`. Custom palettes contain
validated color data and do not execute Python code.

In an ordinary Terminal session, MO can ask the host to match its background
to the selected skin. This applies to the current session; it does not edit
Windows Terminal profiles, opacity, scrollbars or title-bar appearance.
