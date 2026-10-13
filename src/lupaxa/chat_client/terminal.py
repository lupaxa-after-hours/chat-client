"""Server history and the local prompt lines."""

from __future__ import annotations

import re

_SPECIAL = frozenset(
    {"left", "right", "backspace", "delete", "enter", "pageup", "pagedown", "end", "up", "down"}
)
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_RESET = "\x1b[0m"


def _visible_len(text: str) -> int:
    """Return the length of ``text`` once colour codes are ignored."""
    return len(_SGR.sub("", text))


def _fit(text: str, columns: int) -> str:
    """Return ``text`` cut or padded to ``columns`` visible characters."""
    if columns <= 0:
        return ""
    shown = _wrap(text, columns)[0] if text else ""
    gap = columns - _visible_len(shown)
    if gap > 0:
        shown += " " * gap
    return shown


def _wrap(line: str, columns: int) -> list[str]:
    """Split ``line`` into pieces of ``columns`` visible characters."""
    if columns <= 0:
        return []
    if line == "":
        return [""]
    pieces: list[str] = []
    current = ""
    visible = 0
    pending = ""
    pos = 0
    while pos < len(line):
        match = _SGR.match(line, pos)
        if match is not None:
            code = match.group()
            current += code
            pending = "" if code == _RESET else pending + code
            pos = match.end()
            continue
        if visible == columns:
            if pending:
                current += _RESET
            pieces.append(current)
            current = pending
            visible = 0
        current += line[pos]
        visible += 1
        pos += 1
    pieces.append(current)
    return pieces


class Screen:
    """Redrawable server area, bar, and three prompt lines."""

    def __init__(self, columns: int, rows: int) -> None:
        """Start from an empty history at ``columns`` by ``rows``."""
        self.columns = columns
        self.rows = rows
        self._lines: list[str] = []
        self._offset = 0
        self._sent: list[str] = ["", ""]
        self._input = ""
        self._cursor = 0
        self._prompt = ""
        self._hidden = False
        self._partial = ""
        self._cr_pending = False
        self._sealed_prompt = False
        self.pending_bell = False

    @property
    def hidden(self) -> bool:
        """Whether the bottom line hides what is typed."""
        return self._hidden

    def resize(self, columns: int, rows: int) -> None:
        """Store the new size. Wrapping is redone from the original lines."""
        self.columns = columns
        self.rows = rows

    def add_server_text(self, text: str) -> None:
        """Append logical server lines. A trailing newline does not add a blank line.

        Carriage return plus line feed, and a lone carriage return, become a
        newline before the text is stored. A carriage return at the end of one
        chunk and a line feed at the start of the next are that same newline.
        A chunk with no trailing newline stays open and the next chunk appends
        to it, until :meth:`seal_prompt` marks that open line as the talker's
        prompt. The next text then replaces the prompt. One leading newline on
        that text is the echoed enter and is not stored. The open line is the
        bottom prompt once it is sealed, and it is not drawn in the history.
        When the user has paged up, the offset grows with the new
        wrapped lines so the same rows stay on screen.
        """
        if text == "":
            return
        if "\a" in text:
            self.pending_bell = True
            text = text.replace("\a", "")
        if text == "":
            return
        dropped = False
        if self._sealed_prompt:
            self._sealed_prompt = False
            self._partial = ""
            self._cr_pending = False
            dropped = True
        text = self._join_split_crlf(text)
        if dropped and text.startswith("\n"):
            text = text[1:]
        if text == "":
            return
        before = len(self._wrapped())
        combined = self._partial + text
        parts = combined.split("\n")
        if combined.endswith("\n"):
            self._partial = ""
            self._lines.extend(parts[:-1])
        else:
            self._partial = parts[-1]
            self._lines.extend(parts[:-1])
        if self._offset > 0:
            self._offset += len(self._wrapped()) - before

    def _join_split_crlf(self, text: str) -> str:
        """Turn CR LF into one newline, including when the pair is split."""
        if self._cr_pending:
            self._cr_pending = False
            if text.startswith("\n"):
                text = text[1:]
        if text.endswith("\r"):
            self._cr_pending = True
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def seal_prompt(self) -> None:
        """Use the open line as the bottom prompt, and drop it from later history."""
        if self._partial:
            self._prompt = self._partial
            self._sealed_prompt = True

    def take_bell(self) -> bool:
        """Return whether a bell is pending, and clear that flag."""
        pending = self.pending_bell
        self.pending_bell = False
        return pending

    def set_hidden(self, hidden: bool) -> None:
        """Hide or show the characters on the bottom line."""
        self._hidden = hidden

    def echo(self, text: str) -> None:
        """Insert server-confirmed characters. A backspace deletes one."""
        for char in text:
            self.key("backspace" if char == "\b" else char)

    def cursor_column(self) -> int:
        """Return the 1-based column of the input cursor on the prompt line."""
        return self._bottom()[1]

    def cursor_at_end(self) -> bool:
        """Return whether the input cursor is at the end of the line."""
        return self._cursor == len(self._input)

    def key(self, name: str) -> str | None:
        """Apply ``name``. Return the finished line on enter."""
        if name == "enter":
            line = self._input
            self._input = ""
            self._cursor = 0
            if not self._hidden:
                self._sent = [*self._sent[1:], self._prompt + line]
            return line
        if name == "end":
            self._offset = 0
            return None
        if name == "pageup":
            self._offset = min(self._offset + self._height(), self._max_offset())
            return None
        if name == "pagedown":
            self._offset = max(0, self._offset - self._height())
            return None
        if name in ("up", "down"):
            return None
        if name == "left":
            self._cursor = max(0, self._cursor - 1)
            return None
        if name == "right":
            self._cursor = min(len(self._input), self._cursor + 1)
            return None
        if name == "backspace":
            if self._cursor > 0:
                self._input = self._input[: self._cursor - 1] + self._input[self._cursor :]
                self._cursor -= 1
            return None
        if name == "delete":
            self._input = self._input[: self._cursor] + self._input[self._cursor + 1 :]
            return None
        if name in _SPECIAL or len(name) != 1:
            return None
        self._input = self._input[: self._cursor] + name + self._input[self._cursor :]
        self._cursor += 1
        return None

    def render(self) -> str:
        """Return the whole screen, including the bar and the three prompts."""
        height = self._height()
        wrapped = self._wrapped()
        if height == 0:
            window: list[str] = []
        elif self._offset == 0:
            window = wrapped[-height:]
        else:
            end = len(wrapped) - self._offset
            window = wrapped[max(0, end - height) : end]
        window = [""] * (height - len(window)) + window
        chrome = [
            "-" * self.columns,
            _fit(self._sent[0], self.columns),
            _fit(self._sent[1], self.columns),
            self._prompt_line(),
        ]
        visible = [*window, *chrome]
        if len(visible) > self.rows:
            visible = visible[-self.rows :]
        return "\n".join(_fit(line, self.columns) for line in visible)

    def _height(self) -> int:
        return max(0, self.rows - 4)

    def _max_offset(self) -> int:
        extra = len(self._wrapped()) - self._height()
        return max(0, extra)

    def _wrapped(self) -> list[str]:
        wrapped: list[str] = []
        for line in self._lines:
            wrapped.extend(_wrap(line, self.columns))
        return wrapped

    def _prompt_line(self) -> str:
        return self._bottom()[0]

    def _bottom(self) -> tuple[str, int]:
        """Return the bottom row and the 1-based cursor column on it."""
        columns = self.columns
        if columns <= 0:
            return "", 1
        prefix = self._prompt
        shown = prefix if self._hidden else prefix + self._input
        cursor = len(prefix) if self._hidden else len(prefix) + self._cursor
        start = cursor - columns + 1 if cursor >= columns else 0
        column = cursor - start + 1
        if column > columns:
            column = columns
        return _fit(shown[start : start + columns], columns), column
