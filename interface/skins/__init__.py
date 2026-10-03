"""Authoritative built-in MO skin registry."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .cold import SKIN as COLD
from .default import SKIN as DEFAULT
from .dracula import SKIN as DRACULA
from .model import Skin
from .silver import SKIN as SILVER


BUILTIN_SKINS: Mapping[str, Skin] = MappingProxyType({
    "default": DEFAULT,
    "dracula": DRACULA,
    "silver": SILVER,
    "cold": COLD,
})


__all__ = (
    "BUILTIN_SKINS",
    "COLD",
    "DEFAULT",
    "DRACULA",
    "SILVER",
    "Skin",
)
