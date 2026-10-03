"""Bounded embedded-PDF text extraction via lazily imported ``pypdf``.

Lazy, optional adapter: `pypdf` is imported only when a PDF is actually
extracted, so it stays off the base install and the light agent-startup path.
This adapter reads only selectable text already present in the PDF. Image-only
pages return an honest empty-text warning rather than a future-engine promise.
"""
from __future__ import annotations

# Page/character caps so a huge PDF can't blow up context; the tool result is
# additionally capped by the normal tool-result path.
MAX_PAGES = 50
MAX_CHARS_PER_PAGE = 4000


def available() -> bool:
    """True when the PDF text engine (pypdf) is importable."""
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:
        return False


def _parse_pages(pages: object, total: int) -> list[int]:
    """Resolve a `pages` argument (None / int / 'a-b' / list) to 0-based indexes."""
    if pages is None:
        return list(range(min(total, MAX_PAGES)))
    idx: list[int] = []
    if isinstance(pages, int):
        pages = [pages]
    if isinstance(pages, str):
        parts: list[int] = []
        for chunk in pages.replace(" ", "").split(","):
            if "-" in chunk:
                a, _, b = chunk.partition("-")
                if a.isdigit() and b.isdigit():
                    start, stop = int(a), int(b)
                    if start <= stop:
                        parts.extend(range(start, min(stop, start + MAX_PAGES - 1) + 1))
            elif chunk.isdigit():
                parts.append(int(chunk))
            if len(parts) >= MAX_PAGES:
                break
        pages = parts
    if isinstance(pages, (list, tuple)):
        for p in pages:
            try:
                one = int(p) - 1  # callers pass 1-based page numbers
            except (TypeError, ValueError):
                continue
            if 0 <= one < total:
                idx.append(one)
    return (idx or list(range(min(total, MAX_PAGES))))[:MAX_PAGES]


def extract_pdf_text(path: str, pages: object = None) -> str:
    """Extract bounded embedded text and return provider-safe plain text.

    Raises ImportError if pypdf is unavailable (callers check `available()` first
    for a precise optional-dependency error). The returned evidence is always
    provider-safe text — never a claim that pixels were seen.
    """
    import pypdf

    reader = pypdf.PdfReader(path)
    total = len(reader.pages)
    want = _parse_pages(pages, total)

    blocks: list[tuple[int, str]] = []
    warnings: list[str] = []
    got_text = False
    for i in want:
        try:
            text = (reader.pages[i].extract_text() or "").strip()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"page {i + 1}: extract failed ({type(exc).__name__})")
            continue
        if text:
            got_text = True
            if len(text) > MAX_CHARS_PER_PAGE:
                text = text[:MAX_CHARS_PER_PAGE] + " …[truncated]"
            blocks.append((i + 1, text))
        else:
            blocks.append((i + 1, "[no embedded text]"))

    if total > MAX_PAGES or (pages is not None and len(want) == MAX_PAGES):
        warnings.append(f"PDF has {total} pages; at most {MAX_PAGES} pages were read")
    if not got_text:
        warnings.append(
            "no embedded text found; this PDF cannot be read by the embedded-text extractor"
        )

    lines = ["[perception: embedded PDF text via pypdf]"]
    lines.extend(f"— page {page} —\n{text}" for page, text in blocks)
    if warnings:
        lines.append("[warnings] " + " | ".join(warnings))
    return "\n".join(lines)
