"""Dependency-free, bounded VT screen model for split-terminal panes.

This is intentionally a focused terminal host, not a claim of complete xterm
emulation.  It owns the behavior required by interactive shells and coding
agents while keeping parser state, scrollback, and escape payloads bounded.
"""
from __future__ import annotations

import unicodedata
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Iterable

_MAX_ESCAPE_CHARS = 4_096
_ANSI_NAMES = (
    "ansiblack",
    "ansired",
    "ansigreen",
    "ansiyellow",
    "ansiblue",
    "ansimagenta",
    "ansicyan",
    "ansigray",
    "ansibrightblack",
    "ansibrightred",
    "ansibrightgreen",
    "ansibrightyellow",
    "ansibrightblue",
    "ansibrightmagenta",
    "ansibrightcyan",
    "ansiwhite",
)


@dataclass(frozen=True)
class CellStyle:
    """One terminal rendition, expressed without prompt-toolkit imports."""

    fg: str | None = None
    bg: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    hidden: bool = False
    strike: bool = False


@dataclass(frozen=True)
class Cell:
    char: str = " "
    style: CellStyle = CellStyle()
    continuation: bool = False


def _blank_row(columns: int, style: CellStyle | None = None) -> list[Cell]:
    cell = Cell(style=style or CellStyle())
    return [cell for _ in range(columns)]


def _char_width(char: str) -> int:
    if not char or unicodedata.combining(char):
        return 0
    if unicodedata.category(char).startswith("C"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def _indexed_colour(index: int) -> str:
    index = max(0, min(255, int(index)))
    if index < 16:
        return _ANSI_NAMES[index]
    if index < 232:
        value = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        red = levels[value // 36]
        green = levels[(value // 6) % 6]
        blue = levels[value % 6]
    else:
        red = green = blue = 8 + (index - 232) * 10
    return f"#{red:02x}{green:02x}{blue:02x}"


class TerminalScreen:
    """Incremental VT screen with bounded history and query responses."""

    def __init__(
        self,
        columns: int,
        rows: int,
        *,
        history: int = 1_000,
        write_response: Callable[[str], object] | None = None,
    ) -> None:
        self.columns = max(1, int(columns))
        self.rows = max(1, int(rows))
        self._history = deque(maxlen=max(0, int(history)))
        self._display_cache: tuple[tuple[tuple[str, str], ...], ...] | None = None
        self._write_response = write_response
        self._main = [_blank_row(self.columns) for _ in range(self.rows)]
        self._alternate = [_blank_row(self.columns) for _ in range(self.rows)]
        self._alternate_active = False
        self._saved_main_state: tuple[int, int, CellStyle] | None = None
        self.cursor_x = 0
        self.cursor_y = 0
        self._saved_cursor: tuple[int, int, CellStyle] = (0, 0, CellStyle())
        self.style = CellStyle()
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.wrap_enabled = True
        self.insert_mode = False
        self.cursor_visible = True
        self._wrap_pending = False
        self.title = ""
        self._state = "ground"
        self._csi = ""
        self._osc = ""
        self._string = ""

    @property
    def alternate_active(self) -> bool:
        return self._alternate_active

    @property
    def history_size(self) -> int:
        return len(self._history)

    @property
    def _buffer(self) -> list[list[Cell]]:
        return self._alternate if self._alternate_active else self._main

    def feed(self, text: str) -> None:
        """Consume one decoded terminal-output fragment."""
        if text:
            self._display_cache = None
        for char in str(text or ""):
            if self._state == "ground":
                self._feed_ground(char)
            elif self._state == "escape":
                self._feed_escape(char)
            elif self._state == "charset":
                self._state = "ground"
            elif self._state == "csi":
                self._feed_csi(char)
            elif self._state == "osc":
                self._feed_osc(char)
            elif self._state == "osc_escape":
                if char == "\\":
                    self._finish_osc()
                else:
                    self._append_osc("\x1b" + char)
                    self._state = "osc"
            elif self._state == "string":
                if char == "\x1b":
                    self._state = "string_escape"
                elif len(self._string) < _MAX_ESCAPE_CHARS:
                    self._string += char
            elif self._state == "string_escape":
                if char == "\\":
                    self._state = "ground"
                    self._string = ""
                else:
                    self._state = "string"

    def _feed_ground(self, char: str) -> None:
        if char == "\x1b":
            self._state = "escape"
        elif char == "\x9b":
            self._state = "csi"
            self._csi = ""
        elif char in {"\x9d"}:
            self._state = "osc"
            self._osc = ""
        elif char in {"\n", "\x0b", "\x0c"}:
            self._index()
        elif char == "\r":
            self.cursor_x = 0
            self._wrap_pending = False
        elif char == "\b":
            self.cursor_x = max(0, self.cursor_x - 1)
            self._wrap_pending = False
        elif char == "\t":
            self.cursor_x = min(self.columns - 1, ((self.cursor_x // 8) + 1) * 8)
            self._wrap_pending = False
        elif char in {"\x00", "\x05", "\x07", "\x0e", "\x0f", "\x7f"}:
            return
        elif ord(char) >= 0x20:
            self._put_char(char)

    def _feed_escape(self, char: str) -> None:
        self._state = "ground"
        if char == "[":
            self._state = "csi"
            self._csi = ""
        elif char == "]":
            self._state = "osc"
            self._osc = ""
        elif char in {"P", "^", "_"}:
            self._state = "string"
            self._string = ""
        elif char in {"(", ")", "*", "+", "-", ".", "/", "%"}:
            self._state = "charset"
        elif char == "7":
            self._save_cursor()
        elif char == "8":
            self._restore_cursor()
        elif char == "D":
            self._index()
        elif char == "E":
            self.cursor_x = 0
            self._index()
        elif char == "M":
            self._reverse_index()
        elif char == "c":
            self.reset()
        elif char == "Z":
            self._respond("\x1b[?1;2c")

    def _feed_csi(self, char: str) -> None:
        if "@" <= char <= "~":
            payload = self._csi
            self._csi = ""
            self._state = "ground"
            self._dispatch_csi(payload, char)
            return
        if len(self._csi) >= 256:
            self._csi = ""
            self._state = "ground"
            return
        self._csi += char

    def _feed_osc(self, char: str) -> None:
        if char == "\x07":
            self._finish_osc()
        elif char == "\x1b":
            self._state = "osc_escape"
        else:
            self._append_osc(char)

    def _append_osc(self, text: str) -> None:
        if len(self._osc) < _MAX_ESCAPE_CHARS:
            self._osc = (self._osc + text)[:_MAX_ESCAPE_CHARS]

    def _finish_osc(self) -> None:
        payload = self._osc
        self._osc = ""
        self._state = "ground"
        command, separator, value = payload.partition(";")
        if separator and command in {"0", "1", "2"}:
            self.title = value[:512]

    @staticmethod
    def _params(payload: str) -> tuple[str, list[int | None]]:
        private = ""
        while payload and payload[0] in "?<=>!":
            private += payload[0]
            payload = payload[1:]
        payload = payload.replace(":", ";")
        values: list[int | None] = []
        for item in payload.split(";"):
            clean = "".join(char for char in item if char.isdigit() or char == "-")
            try:
                values.append(int(clean) if clean else None)
            except ValueError:
                values.append(None)
        return private, values or [None]

    @staticmethod
    def _value(values: list[int | None], index: int = 0, default: int = 1) -> int:
        if index >= len(values) or values[index] in {None, 0}:
            return default
        return max(0, int(values[index] or default))

    def _dispatch_csi(self, payload: str, final: str) -> None:
        private, values = self._params(payload)
        amount = self._value(values)
        if final == "A":
            self._move(y=self.cursor_y - amount)
        elif final in {"B", "e"}:
            self._move(y=self.cursor_y + amount)
        elif final in {"C", "a"}:
            self._move(x=self.cursor_x + amount)
        elif final == "D":
            self._move(x=self.cursor_x - amount)
        elif final == "E":
            self._move(x=0, y=self.cursor_y + amount)
        elif final == "F":
            self._move(x=0, y=self.cursor_y - amount)
        elif final in {"G", "`"}:
            self._move(x=amount - 1)
        elif final in {"H", "f"}:
            row = self._value(values, 0) - 1
            column = self._value(values, 1) - 1
            self._move(x=column, y=row)
        elif final == "d":
            self._move(y=amount - 1)
        elif final == "J":
            self._erase_display(values[0] or 0)
        elif final == "K":
            self._erase_line(values[0] or 0)
        elif final == "@":
            self._insert_chars(amount)
        elif final == "P":
            self._delete_chars(amount)
        elif final == "X":
            self._erase_chars(amount)
        elif final == "L":
            self._insert_lines(amount)
        elif final == "M":
            self._delete_lines(amount)
        elif final == "S":
            self._scroll_up(amount)
        elif final == "T":
            self._scroll_down(amount)
        elif final == "r" and not private:
            top = self._value(values, 0) - 1
            bottom = self._value(values, 1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top, self.scroll_bottom = top, bottom
            else:
                self.scroll_top, self.scroll_bottom = 0, self.rows - 1
            self._move(x=0, y=0)
        elif final == "m":
            self._set_rendition(values)
        elif final in {"h", "l"}:
            self._set_modes(private, values, enabled=final == "h")
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        elif final == "n":
            self._device_status(private, values[0] or 0)
        elif final == "c":
            if private == ">":
                self._respond("\x1b[>0;1;0c")
            else:
                self._respond("\x1b[?1;2c")

    def _move(self, *, x: int | None = None, y: int | None = None) -> None:
        if x is not None:
            self.cursor_x = max(0, min(self.columns - 1, int(x)))
        if y is not None:
            self.cursor_y = max(0, min(self.rows - 1, int(y)))
        self._wrap_pending = False

    def _put_char(self, char: str) -> None:
        width = _char_width(char)
        if width == 0:
            if self.cursor_x > 0 or self._wrap_pending:
                index = min(
                    self.columns - 1,
                    self.cursor_x if self._wrap_pending else self.cursor_x - 1,
                )
                cell = self._buffer[self.cursor_y][index]
                if cell.continuation and index > 0:
                    index -= 1
                    cell = self._buffer[self.cursor_y][index]
                self._buffer[self.cursor_y][index] = replace(cell, char=cell.char + char)
            return
        if self._wrap_pending and self.wrap_enabled:
            self.cursor_x = 0
            self._index()
        self._wrap_pending = False
        if width == 2 and self.cursor_x == self.columns - 1:
            if self.wrap_enabled:
                self.cursor_x = 0
                self._index()
            else:
                width = 1
        row = self._buffer[self.cursor_y]
        if self.insert_mode:
            for index in range(self.columns - 1, self.cursor_x + width - 1, -1):
                row[index] = row[index - width]
        row[self.cursor_x] = Cell(char=char, style=self.style)
        if width == 2 and self.cursor_x + 1 < self.columns:
            row[self.cursor_x + 1] = Cell(char="", style=self.style, continuation=True)
        target = self.cursor_x + width
        if target >= self.columns:
            self.cursor_x = self.columns - 1
            self._wrap_pending = self.wrap_enabled
        else:
            self.cursor_x = target

    def _index(self) -> None:
        self._wrap_pending = False
        if self.cursor_y == self.scroll_bottom:
            self._scroll_up(1)
        else:
            self.cursor_y = min(self.rows - 1, self.cursor_y + 1)

    def _reverse_index(self) -> None:
        self._wrap_pending = False
        if self.cursor_y == self.scroll_top:
            self._scroll_down(1)
        else:
            self.cursor_y = max(0, self.cursor_y - 1)

    def _scroll_up(self, amount: int) -> None:
        buffer = self._buffer
        for _ in range(min(max(1, amount), self.scroll_bottom - self.scroll_top + 1)):
            removed = buffer.pop(self.scroll_top)
            if not self._alternate_active and self.scroll_top == 0:
                # History never changes after a row leaves the screen. Store
                # its presentation once instead of reformatting every saved
                # cell on every workspace redraw.
                self._history.append(self._fragment_row(removed))
            buffer.insert(self.scroll_bottom, _blank_row(self.columns, self.style))

    def _scroll_down(self, amount: int) -> None:
        buffer = self._buffer
        for _ in range(min(max(1, amount), self.scroll_bottom - self.scroll_top + 1)):
            buffer.pop(self.scroll_bottom)
            buffer.insert(self.scroll_top, _blank_row(self.columns, self.style))

    def _erase_display(self, mode: int) -> None:
        if mode in {2, 3}:
            self._replace_rows(0, self.rows - 1)
            if mode == 3:
                self._history.clear()
        elif mode == 1:
            for row in range(0, self.cursor_y):
                self._buffer[row] = _blank_row(self.columns, self.style)
            self._erase_row_segment(self.cursor_y, 0, self.cursor_x)
        else:
            self._erase_row_segment(self.cursor_y, self.cursor_x, self.columns - 1)
            for row in range(self.cursor_y + 1, self.rows):
                self._buffer[row] = _blank_row(self.columns, self.style)

    def _erase_line(self, mode: int) -> None:
        if mode == 1:
            self._erase_row_segment(self.cursor_y, 0, self.cursor_x)
        elif mode == 2:
            self._erase_row_segment(self.cursor_y, 0, self.columns - 1)
        else:
            self._erase_row_segment(self.cursor_y, self.cursor_x, self.columns - 1)

    def _replace_rows(self, start: int, end: int) -> None:
        for index in range(max(0, start), min(self.rows - 1, end) + 1):
            self._buffer[index] = _blank_row(self.columns, self.style)

    def _erase_row_segment(self, row: int, start: int, end: int) -> None:
        blank = Cell(style=self.style)
        for index in range(max(0, start), min(self.columns - 1, end) + 1):
            self._buffer[row][index] = blank

    def _insert_chars(self, amount: int) -> None:
        row = self._buffer[self.cursor_y]
        amount = min(max(1, amount), self.columns - self.cursor_x)
        row[self.cursor_x:self.cursor_x] = _blank_row(amount, self.style)
        del row[self.columns:]

    def _delete_chars(self, amount: int) -> None:
        row = self._buffer[self.cursor_y]
        amount = min(max(1, amount), self.columns - self.cursor_x)
        del row[self.cursor_x:self.cursor_x + amount]
        row.extend(_blank_row(amount, self.style))

    def _erase_chars(self, amount: int) -> None:
        self._erase_row_segment(
            self.cursor_y,
            self.cursor_x,
            min(self.columns - 1, self.cursor_x + max(1, amount) - 1),
        )

    def _insert_lines(self, amount: int) -> None:
        if not self.scroll_top <= self.cursor_y <= self.scroll_bottom:
            return
        buffer = self._buffer
        for _ in range(min(max(1, amount), self.scroll_bottom - self.cursor_y + 1)):
            buffer.insert(self.cursor_y, _blank_row(self.columns, self.style))
            buffer.pop(self.scroll_bottom + 1)

    def _delete_lines(self, amount: int) -> None:
        if not self.scroll_top <= self.cursor_y <= self.scroll_bottom:
            return
        buffer = self._buffer
        for _ in range(min(max(1, amount), self.scroll_bottom - self.cursor_y + 1)):
            buffer.pop(self.cursor_y)
            buffer.insert(self.scroll_bottom, _blank_row(self.columns, self.style))

    def _save_cursor(self) -> None:
        self._saved_cursor = (self.cursor_x, self.cursor_y, self.style)

    def _restore_cursor(self) -> None:
        x, y, style = self._saved_cursor
        self.style = style
        self._move(x=x, y=y)

    def _set_modes(self, private: str, values: Iterable[int | None], *, enabled: bool) -> None:
        for raw in values:
            value = int(raw or 0)
            if private == "?":
                if value == 7:
                    self.wrap_enabled = enabled
                elif value == 25:
                    self.cursor_visible = enabled
                elif value == 1048:
                    self._save_cursor() if enabled else self._restore_cursor()
                elif value in {47, 1047, 1049}:
                    self._set_alternate(enabled, save_cursor=value == 1049)
            elif value == 4:
                self.insert_mode = enabled

    def _set_alternate(self, enabled: bool, *, save_cursor: bool) -> None:
        if enabled and not self._alternate_active:
            if save_cursor:
                self._saved_main_state = (self.cursor_x, self.cursor_y, self.style)
            self._alternate = [_blank_row(self.columns) for _ in range(self.rows)]
            self._alternate_active = True
            self.cursor_x = self.cursor_y = 0
            self.style = CellStyle()
        elif not enabled and self._alternate_active:
            self._alternate_active = False
            if self._saved_main_state is not None:
                self.cursor_x, self.cursor_y, self.style = self._saved_main_state
            self._saved_main_state = None
        self._wrap_pending = False

    def _set_rendition(self, values: list[int | None]) -> None:
        params = [0 if value is None else int(value) for value in values]
        index = 0
        while index < len(params):
            value = params[index]
            if value == 0:
                self.style = CellStyle()
            elif value == 1:
                self.style = replace(self.style, bold=True)
            elif value == 2:
                self.style = replace(self.style, dim=True)
            elif value == 3:
                self.style = replace(self.style, italic=True)
            elif value == 4:
                self.style = replace(self.style, underline=True)
            elif value == 7:
                self.style = replace(self.style, inverse=True)
            elif value == 8:
                self.style = replace(self.style, hidden=True)
            elif value == 9:
                self.style = replace(self.style, strike=True)
            elif value == 22:
                self.style = replace(self.style, bold=False, dim=False)
            elif value == 23:
                self.style = replace(self.style, italic=False)
            elif value == 24:
                self.style = replace(self.style, underline=False)
            elif value == 27:
                self.style = replace(self.style, inverse=False)
            elif value == 28:
                self.style = replace(self.style, hidden=False)
            elif value == 29:
                self.style = replace(self.style, strike=False)
            elif 30 <= value <= 37:
                self.style = replace(self.style, fg=_ANSI_NAMES[value - 30])
            elif value == 39:
                self.style = replace(self.style, fg=None)
            elif 40 <= value <= 47:
                self.style = replace(self.style, bg=_ANSI_NAMES[value - 40])
            elif value == 49:
                self.style = replace(self.style, bg=None)
            elif 90 <= value <= 97:
                self.style = replace(self.style, fg=_ANSI_NAMES[8 + value - 90])
            elif 100 <= value <= 107:
                self.style = replace(self.style, bg=_ANSI_NAMES[8 + value - 100])
            elif value in {38, 48}:
                colour, consumed = self._extended_colour(params[index + 1:])
                if colour is not None:
                    self.style = replace(
                        self.style,
                        **({"fg": colour} if value == 38 else {"bg": colour}),
                    )
                index += consumed
            index += 1

    @staticmethod
    def _extended_colour(values: list[int]) -> tuple[str | None, int]:
        if len(values) >= 2 and values[0] == 5:
            return _indexed_colour(values[1]), 2
        if len(values) >= 4 and values[0] == 2:
            red, green, blue = (max(0, min(255, value)) for value in values[1:4])
            return f"#{red:02x}{green:02x}{blue:02x}", 4
        return None, 0

    def _device_status(self, private: str, value: int) -> None:
        if value == 5 and not private:
            self._respond("\x1b[0n")
        elif value == 6:
            prefix = "?" if private == "?" else ""
            self._respond(f"\x1b[{prefix}{self.cursor_y + 1};{self.cursor_x + 1}R")

    def _respond(self, text: str) -> None:
        if self._write_response is not None:
            try:
                self._write_response(text)
            except Exception:
                pass

    def resize(self, *, columns: int, rows: int) -> None:
        columns = max(1, int(columns))
        rows = max(1, int(rows))
        if (columns, rows) == (self.columns, self.rows):
            return
        self._display_cache = None
        for buffer in (self._main, self._alternate):
            for row in buffer:
                if columns > self.columns:
                    row.extend(_blank_row(columns - self.columns))
                else:
                    del row[columns:]
            if rows > self.rows:
                buffer.extend(_blank_row(columns) for _ in range(rows - self.rows))
            else:
                del buffer[rows:]
        self.columns, self.rows = columns, rows
        self.cursor_x = min(self.cursor_x, columns - 1)
        self.cursor_y = min(self.cursor_y, rows - 1)
        self.scroll_top, self.scroll_bottom = 0, rows - 1
        self._wrap_pending = False

    def reset(self) -> None:
        self._display_cache = None
        self._main = [_blank_row(self.columns) for _ in range(self.rows)]
        self._alternate = [_blank_row(self.columns) for _ in range(self.rows)]
        self._alternate_active = False
        self._saved_main_state = None
        self.cursor_x = self.cursor_y = 0
        self._saved_cursor = (0, 0, CellStyle())
        self.style = CellStyle()
        self.scroll_top, self.scroll_bottom = 0, self.rows - 1
        self.wrap_enabled = True
        self.insert_mode = False
        self.cursor_visible = True
        self._wrap_pending = False

    @staticmethod
    def _line_text(row: Iterable[Cell]) -> str:
        return "".join(cell.char for cell in row if not cell.continuation).rstrip()

    def display_lines(self) -> tuple[str, ...]:
        rows = [self._line_text(row) for row in self._buffer]
        while rows and not rows[-1]:
            rows.pop()
        return tuple(rows)

    @staticmethod
    def _prompt_style(style: CellStyle) -> str:
        foreground, background = style.fg, style.bg
        if style.inverse:
            foreground, background = background, foreground
        # ANSI black is commonly emitted as a terminal's default canvas. Inside
        # the workspace that canvas belongs to the active skin; explicit RGB
        # black and every other semantic background remain child-owned.
        if background == "ansiblack":
            background = None
        pieces = ["class:workspace-terminal"]
        if foreground:
            pieces.append(f"fg:{foreground}")
        if background:
            pieces.append(f"bg:{background}")
        if style.bold:
            pieces.append("bold")
        if style.dim:
            pieces.append("dim")
        if style.italic:
            pieces.append("italic")
        if style.underline:
            pieces.append("underline")
        if style.strike:
            pieces.append("strike")
        return " ".join(pieces)

    def display_fragments(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        if self._display_cache is None:
            rows = [self._fragment_row(row) for row in self._buffer]
            while rows and not rows[-1]:
                rows.pop()
            self._display_cache = tuple(rows)
        return self._display_cache

    def scrollback_fragments(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Return bounded main-buffer history plus the current visible screen."""
        if self._alternate_active:
            return self.display_fragments()
        rows = [*self._history, *self.display_fragments()]
        while rows and not rows[-1]:
            rows.pop()
        return tuple(rows)

    def _fragment_row(
        self,
        row: Iterable[Cell],
    ) -> tuple[tuple[str, str], ...]:
        visible = list(row)
        while visible and (visible[-1].continuation or visible[-1].char == " "):
            visible.pop()
        fragments: list[tuple[str, str]] = []
        current_style = ""
        current_text = ""
        for cell in visible:
            if cell.continuation:
                continue
            text = " " if cell.style.hidden else cell.char
            style = self._prompt_style(cell.style)
            if style != current_style and current_text:
                fragments.append((current_style, current_text))
                current_text = ""
            current_style = style
            current_text += text
        if current_text:
            fragments.append((current_style, current_text))
        return tuple(fragments)


__all__ = ["Cell", "CellStyle", "TerminalScreen"]
