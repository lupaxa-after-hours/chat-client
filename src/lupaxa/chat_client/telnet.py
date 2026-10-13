"""Telnet bytes in, visible text out."""

from __future__ import annotations


def _naws_pair(value: int) -> bytes:
    """Return a 16-bit size. A byte of 255 is sent as 255 255."""
    high = (value >> 8) & 255
    low = value & 255
    encoded = bytearray()
    for byte in (high, low):
        encoded.append(byte)
        if byte == 255:
            encoded.append(255)
    return bytes(encoded)


class TelnetParser:
    """Strip negotiation and remember whether input is hidden."""

    local_echo = True

    def __init__(self) -> None:
        """Start with echo on and no partial command."""
        self._pending = bytearray()
        self._utf8_pending = bytearray()
        self._hidden = False

    @property
    def hidden(self) -> bool:
        """True after the server sends will-echo."""
        return self._hidden

    def _decode_visible(self, out: bytearray) -> str:
        """Decode visible bytes as UTF-8, keeping an incomplete sequence."""
        self._utf8_pending.extend(out)
        text_parts: list[str] = []
        buf = self._utf8_pending
        while buf:
            try:
                text_parts.append(buf.decode("utf-8", errors="strict"))
                buf.clear()
                break
            except UnicodeDecodeError as exc:
                if exc.reason == "unexpected end of data":
                    if exc.start:
                        text_parts.append(buf[: exc.start].decode("utf-8", errors="strict"))
                        del buf[: exc.start]
                    break
                if exc.start:
                    text_parts.append(buf[: exc.start].decode("utf-8", errors="strict"))
                text_parts.append("\ufffd")
                del buf[: exc.start + 1]
        return "".join(text_parts)

    def feed(self, data: bytes) -> str:
        """Return the visible text in ``data``. Keep an unfinished command."""
        self._pending.extend(data)
        out = bytearray()
        index = 0
        pending = self._pending
        while index < len(pending):
            if pending[index] != 255:
                out.append(pending[index])
                index += 1
                continue
            if index + 1 >= len(pending):
                break
            command = pending[index + 1]
            if command == 255:
                out.extend(b"\xc3\xbf")
                index += 2
                continue
            if command in (251, 252, 253, 254):
                if index + 2 >= len(pending):
                    break
                option = pending[index + 2]
                if option == 1 and command == 251:
                    self._hidden = True
                elif option == 1 and command == 252:
                    self._hidden = False
                index += 3
                continue
            if command == 250:
                end = pending.find(bytes([255, 240]), index + 2)
                if end == -1:
                    break
                index = end + 2
                continue
            index += 2
        del pending[:index]
        return self._decode_visible(out)

    def window_size(self, columns: int, rows: int) -> bytes:
        """Return the NAWS command for ``columns`` by ``rows``."""
        return bytes([255, 250, 31]) + _naws_pair(columns) + _naws_pair(rows) + bytes([255, 240])

    def send_line(self, line: str) -> bytes:
        """Return ``line`` as UTF-8 with a CRLF."""
        return line.encode("utf-8") + b"\r\n"

    def send_key(self, name: str) -> bytes:
        """Telnet does not send a key until enter. ``name`` is ignored."""
        del name
        return b""
