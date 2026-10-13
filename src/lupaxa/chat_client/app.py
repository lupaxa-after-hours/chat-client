"""Connect server bytes and keys to the screen."""

from __future__ import annotations

from collections.abc import Callable

from .ssh import EchoStripper
from .telnet import TelnetParser
from .terminal import Screen

Transport = TelnetParser | EchoStripper


class App:
    """One session. Exit always restores the terminal once."""

    def __init__(self, screen: Screen, transport: Transport, restore: Callable[[], None]) -> None:
        """Keep the screen, the transport, and the restore callback."""
        self.screen = screen
        self._transport = transport
        self._restore = restore
        self.outgoing: list[bytes] = []
        self.exited = False
        self.restored = False

    def server_data(self, data: bytes) -> None:
        """Append visible server text and apply echo or the hidden flag."""
        text = self._transport.feed(data)
        self.screen.set_hidden(self._transport.hidden)
        if isinstance(self._transport, EchoStripper):
            echoed = self._transport.take_echo()
            if echoed:
                self.screen.echo(echoed)
        if text:
            self.screen.add_server_text(text)

    def seal_prompt(self) -> None:
        """Drop the idle server prompt from the next line of history."""
        self.screen.seal_prompt()

    def key(self, name: str) -> None:
        """Edit, send, or quit. Ctrl-C restores and adds no failure line."""
        if self.exited:
            return
        if name == "ctrl-c":
            self._exit()
            return
        if name == "ctrl-d":
            return
        if isinstance(self._transport, TelnetParser):
            if name == "enter":
                line = self.screen.key(name)
                self.outgoing.append(self._transport.send_line(line or ""))
                return
            data = self._transport.send_key(name)
            if data:
                self.outgoing.append(data)
            self.screen.key(name)
            return
        if name in {"pageup", "pagedown", "end", "left", "right", "up", "down"}:
            self.screen.key(name)
            return
        if name == "backspace":
            self.outgoing.append(self._transport.send_erase())
            return
        if name == "delete":
            if self.screen.cursor_at_end():
                self.outgoing.append(self._transport.send_erase())
            else:
                self.screen.key("delete")
            return
        if name == "enter":
            self.screen.key(name)
            enter = self._transport.send_enter()
            if enter:
                self.outgoing.append(enter)
            return
        data = self._transport.send_key(name)
        if data:
            self.outgoing.append(data)

    def fail(self, message: str) -> None:
        """Show ``message`` above the bar, then restore and exit."""
        if self.exited:
            return
        self.screen.add_server_text(message + "\n")
        self._exit()

    def resize(self, columns: int, rows: int) -> None:
        """Resize the screen and send the telnet window size when that transport is in use."""
        self.screen.resize(columns, rows)
        if isinstance(self._transport, TelnetParser):
            self.outgoing.append(self._transport.window_size(columns, rows))

    def _exit(self) -> None:
        if not self.restored:
            self._restore()
            self.restored = True
        self.exited = True
