"""MO image editing — transform an existing image FILE (resize/crop/rotate/flip/convert).

This is the EDIT leg of MO's image capability model:
  read  -> perceive / computer_observe    (an image INTO the model)
  make  -> generate_image (core.imagegen) (a NEW image from a prompt)
  show  -> show_image (core.visualize)    (an image TO the operator)
  edit  -> here                           (transform a file the operator has)

Pure Pillow transforms. Each op writes a NEW file (the source is never
overwritten unless the caller names it explicitly via ``out``) and returns the
saved path plus the new pixel size. Pillow is imported lazily so importing this
module stays light (MO's startup rule).
"""
from __future__ import annotations

from pathlib import Path

# Output formats convert() will write to (Pillow-native).
SUPPORTED_OUT_FORMATS = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tiff", "tif", "ico"}


def available() -> bool:
    """True when Pillow is importable — the edit ops need it."""
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def _default_out(src: Path, suffix: str, *, new_ext: str | None = None) -> Path:
    ext = ("." + new_ext.lstrip(".")) if new_ext else src.suffix
    stem = f"{src.stem}{suffix}" if suffix else f"{src.stem}_converted"
    candidate = src.with_name(f"{stem}{ext}")
    index = 2
    while candidate.resolve() == src.resolve() or candidate.exists():
        candidate = src.with_name(f"{stem}_{index}{ext}")
        index += 1
    return candidate


def _load(path: str):
    from PIL import Image

    return Image.open(path)


def _save(img, out_path: Path) -> Path:
    """Save, coercing modes a target format can't hold (e.g. RGBA -> JPEG)."""
    fmt = out_path.suffix.lstrip(".").lower()
    if fmt in {"jpg", "jpeg"} and img.mode in {"RGBA", "P", "LA"}:
        img = img.convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def resize(path: str, *, width=None, height=None, scale=None, out=None) -> tuple[str, tuple[int, int]]:
    """Resize by ``scale`` (percent) or to ``width``/``height``.

    With only one of width/height, the other is derived to keep aspect ratio.
    """
    src = Path(path)
    img = _load(str(src))
    w, h = img.size
    if scale:
        nw, nh = max(1, round(w * float(scale) / 100)), max(1, round(h * float(scale) / 100))
    elif width and height:
        nw, nh = int(width), int(height)
    elif width:
        nw = int(width)
        nh = max(1, round(h * nw / w))
    elif height:
        nh = int(height)
        nw = max(1, round(w * nh / h))
    else:
        raise ValueError("resize needs scale, width, and/or height")
    from PIL import Image

    resized = img.resize((nw, nh), Image.LANCZOS)
    out_path = Path(out) if out else _default_out(src, "_resized")
    return str(_save(resized, out_path)), (nw, nh)


def crop(path: str, *, x, y, width, height, out=None) -> tuple[str, tuple[int, int]]:
    """Crop to the rectangle (x, y, width, height); clamped to the image bounds."""
    src = Path(path)
    img = _load(str(src))
    iw, ih = img.size
    left = max(0, int(x))
    top = max(0, int(y))
    right = min(iw, left + int(width))
    bottom = min(ih, top + int(height))
    if right <= left or bottom <= top:
        raise ValueError("crop rectangle is empty or outside the image")
    cropped = img.crop((left, top, right, bottom))
    out_path = Path(out) if out else _default_out(src, "_cropped")
    return str(_save(cropped, out_path)), cropped.size


def rotate(path: str, *, degrees, out=None) -> tuple[str, tuple[int, int]]:
    """Rotate clockwise by ``degrees`` (90/180/270), expanding so nothing is cropped."""
    src = Path(path)
    img = _load(str(src))
    angle = int(degrees)
    if angle not in {90, 180, 270}:
        raise ValueError("rotate degrees must be 90, 180, or 270")
    # PIL rotates counter-clockwise; negate for the clockwise convention users expect.
    rotated = img.rotate(-angle, expand=True)
    out_path = Path(out) if out else _default_out(src, "_rotated")
    return str(_save(rotated, out_path)), rotated.size


def flip(path: str, *, direction, out=None) -> tuple[str, tuple[int, int]]:
    """Mirror the image horizontally or vertically."""
    from PIL import Image

    src = Path(path)
    img = _load(str(src))
    d = str(direction).lower()
    if d in {"h", "horizontal"}:
        flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
    elif d in {"v", "vertical"}:
        flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
    else:
        raise ValueError("flip direction must be horizontal or vertical")
    out_path = Path(out) if out else _default_out(src, "_flipped")
    return str(_save(flipped, out_path)), flipped.size


def convert(path: str, *, to_format, out=None) -> tuple[str, tuple[int, int]]:
    """Save the image in a different format (png/jpg/webp/bmp/gif/tiff/ico)."""
    src = Path(path)
    fmt = str(to_format).lstrip(".").lower()
    if fmt not in SUPPORTED_OUT_FORMATS:
        raise ValueError(f"unsupported target format: {to_format}")
    img = _load(str(src))
    out_path = Path(out) if out else _default_out(src, "", new_ext=fmt)
    return str(_save(img, out_path)), img.size
