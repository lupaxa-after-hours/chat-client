"""SSH echo stripping and the remembered host key."""

from __future__ import annotations

from pathlib import Path


def pty_options(columns: int, rows: int) -> dict[str, object]:
    """Return the process options that request a terminal of this size."""
    return {"term_type": "xterm", "term_size": (columns, rows), "encoding": None}


def _erase_span(incoming: bytearray) -> int:
    """Return erase-echo bytes to consume. A negative value means wait."""
    if not incoming.startswith(b"\b"):
        return 0
    if incoming.startswith(b"\b \b"):
        return 3
    if len(incoming) == 2 and incoming.startswith(b"\b "):
        return -1
    return 1


def _tail_after_newline(incoming: bytearray) -> int | None:
    """Return the index after the last CR or LF, when this chunk has one."""
    last = -1
    for index, byte in enumerate(incoming):
        if byte in (10, 13):
            last = index
    if last < 0:
        return None
    return last + 1


def _decode_pending(pending: bytearray, data: bytes) -> str:
    """Decode ``data`` as UTF-8, keeping a character split across chunks."""
    pending.extend(data)
    parts: list[str] = []
    while pending:
        try:
            parts.append(pending.decode("utf-8", errors="strict"))
            pending.clear()
            break
        except UnicodeDecodeError as exc:
            if exc.reason == "unexpected end of data":
                if exc.start:
                    parts.append(pending[: exc.start].decode("utf-8", errors="strict"))
                    del pending[: exc.start]
                break
            if exc.start:
                parts.append(pending[: exc.start].decode("utf-8", errors="strict"))
            parts.append("\ufffd")
            del pending[: exc.start + 1]
    return "".join(parts)


class EchoStripper:
    """Remove bytes the server echoed and notice when it stops."""

    local_echo = False

    def __init__(self) -> None:
        """Start with nothing sent and echo visible."""
        self._owed = bytearray()
        self._echoed = bytearray()
        self._hidden = False
        self._held = bytearray()
        self._text_pending = bytearray()
        self._echo_pending = bytearray()

    @property
    def hidden(self) -> bool:
        """True after sent bytes were not echoed."""
        return self._hidden

    def sent(self, data: bytes) -> None:
        """Remember ``data`` so a later feed can peel it off."""
        self._owed.extend(data)

    def send_key(self, name: str) -> bytes:
        """Return one character to send. Mark it as owed echo."""
        if len(name) != 1:
            return b""
        data = name.encode("utf-8")
        self.sent(data)
        return data

    def send_erase(self) -> bytes:
        """Return the erase byte and remember to expect its echo."""
        data = b"\x7f"
        self.sent(data)
        return data

    def send_enter(self) -> bytes:
        """Return the enter bytes and mark them as owed echo."""
        data = b"\r\n"
        self.sent(data)
        return data

    def feed(self, data: bytes) -> str:
        """Return server text. Matching owed bytes become echo, not text.

        Owed echo matches at the start of what remains, or as the whole
        remainder after a newline. When enter is owed and the chunk starts
        with CR or LF, earlier owed bytes were not echoed and are dropped.
        Hidden stays set only while an owed byte is still unanswered.
        """
        incoming = self._held
        self._held = bytearray()
        incoming.extend(data)
        server = bytearray()
        mismatched = False
        while incoming:
            if self._drop_unshown_before_enter(incoming):
                continue
            if self._owed[:1] == b"\x7f":
                span = _erase_span(incoming)
                if span < 0:
                    self._held = incoming
                    break
                if span > 0:
                    del incoming[:span]
                    del self._owed[:1]
                    self._echoed.append(8)
                    continue
            matched = self._match_len(incoming, 0)
            if matched:
                self._keep_echo(incoming[:matched])
                del incoming[:matched]
                del self._owed[:matched]
                continue
            if not self._owed:
                server.extend(incoming)
                break
            index = self._tail_echo(incoming)
            if index is None:
                server.extend(incoming)
                mismatched = True
                break
            server.extend(incoming[:index])
            del incoming[:index]
        if not self._owed:
            self._hidden = False
        elif mismatched:
            self._hidden = True
        return _decode_pending(self._text_pending, bytes(server))

    def take_echo(self) -> str:
        """Return echoed characters and clear them. Backspace deletes one."""
        text = _decode_pending(self._echo_pending, bytes(self._echoed))
        self._echoed.clear()
        return text

    def _keep_echo(self, data: bytearray) -> None:
        for byte in data:
            if byte not in (10, 13):
                self._echoed.append(byte)

    def _match_len(self, incoming: bytearray, index: int) -> int:
        """Return how many owed bytes match at ``index``."""
        if not self._owed or self._owed[0] == 127:
            return 0
        size = len(incoming) - index
        limit = min(len(self._owed), size)
        taken = 0
        while taken < limit and incoming[index + taken] == self._owed[taken]:
            taken += 1
        if taken == 0:
            return 0
        if taken < len(self._owed) and taken < size:
            return 0
        return taken

    def _drop_unshown_before_enter(self, incoming: bytearray) -> bool:
        """Drop owed bytes before enter when the chunk starts with the enter echo."""
        if not incoming or incoming[0] not in (10, 13):
            return False
        for index, byte in enumerate(self._owed):
            if byte in (10, 13):
                if index == 0:
                    return False
                del self._owed[:index]
                return True
        return False

    def _tail_echo(self, incoming: bytearray) -> int | None:
        """Return where the remainder after a newline is exactly the owed echo."""
        tail = _tail_after_newline(incoming)
        if tail is None:
            return None
        if self._owed[:1] == b"\x7f":
            return self._erase_tail(incoming, tail)
        matched = self._match_len(incoming, tail)
        if matched > 0 and tail + matched == len(incoming):
            return tail
        return None

    def _erase_tail(self, incoming: bytearray, tail: int) -> int | None:
        """Return ``tail`` when the remainder is an erase echo and nothing follows."""
        span = _erase_span(incoming[tail:])
        if span < 0 or (span > 0 and tail + span == len(incoming)):
            return tail
        return None


class KnownHosts:
    """Remember one fingerprint per host and port."""

    def __init__(self, path: Path) -> None:
        """Load ``path``. A missing file starts empty."""
        self._path = path
        self._keys: dict[tuple[str, int], str] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                host, port_text, fingerprint = parts[0], parts[1], parts[2]
                self._keys[(host, int(port_text))] = fingerprint

    def decide(self, host: str, port: int, fingerprint: str) -> str:
        """Return ``remember``, ``accept``, or ``refuse`` for this key."""
        current = self._keys.get((host, port))
        if current is None:
            self._keys[(host, port)] = fingerprint
            self._write()
            return "remember"
        if current == fingerprint:
            return "accept"
        return "refuse"

    def _write(self) -> None:
        lines = [f"{host} {port} {fingerprint}" for (host, port), fingerprint in self._keys.items()]
        text = "\n".join(lines)
        if text:
            text += "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(text, encoding="utf-8")
