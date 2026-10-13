"""Keys, server bytes, and exit restore the terminal."""

from __future__ import annotations

import pytest

from lupaxa.chat_client.app import App
from lupaxa.chat_client.cli import main
from lupaxa.chat_client.ssh import EchoStripper
from lupaxa.chat_client.telnet import TelnetParser
from lupaxa.chat_client.terminal import Screen


class _Restore:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def test_sealed_prompt_does_not_prefix_the_next_line() -> None:
    """After the server goes idle, its prompt is not part of the next line."""
    screen = Screen(40, 8)
    app = App(screen, TelnetParser(), _Restore())
    app.server_data(b"Welcome\nPlease enter your name: ")
    app.seal_prompt()
    app.server_data(b"Hello\nWhat? > ")
    app.seal_prompt()
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert "Hello" in rows
    assert rows[-1].startswith("What? >")
    assert all("name" not in row and "What?" not in row for row in rows[:-1])


def test_telnet_enter_sends_the_line_and_a_server_line_keeps_the_prompt() -> None:
    """Enter queues the line. A later server line does not clear the next prompt."""
    restore = _Restore()
    screen = Screen(20, 8)
    app = App(screen, TelnetParser(), restore)
    app.key("h")
    app.key("i")
    app.server_data(b"Room\n")
    assert "hi" in screen.render().split("\n")[-1]
    app.key("enter")
    assert app.outgoing == [b"hi\r\n"]
    assert app.exited is False


def test_connection_failure_writes_a_line_and_restores() -> None:
    """A failure is one server line, then the same restore Ctrl-C uses."""
    restore = _Restore()
    screen = Screen(40, 8)
    app = App(screen, TelnetParser(), restore)
    app.fail("Connection closed.")
    assert "Connection closed." in screen.render()
    assert app.exited is True
    assert restore.calls == 1
    app.fail("again")
    assert restore.calls == 1


def test_ctrl_c_restores_without_a_failure_line() -> None:
    """Ctrl-C exits and does not add a connection-closed line."""
    restore = _Restore()
    screen = Screen(20, 8)
    app = App(screen, TelnetParser(), restore)
    app.key("ctrl-c")
    assert app.exited is True
    assert restore.calls == 1
    assert "Connection closed." not in screen.render()


def test_ctrl_d_does_not_quit() -> None:
    """Ctrl-D is not an exit key."""
    restore = _Restore()
    app = App(Screen(20, 8), TelnetParser(), restore)
    app.key("ctrl-d")
    assert app.exited is False
    assert restore.calls == 0


def test_resize_queues_the_telnet_window_size() -> None:
    """A resize updates the screen and sends NAWS."""
    app = App(Screen(20, 8), TelnetParser(), _Restore())
    app.resize(100, 40)
    assert app.screen.columns == 100
    assert app.outgoing[-1] == bytes([255, 250, 31, 0, 100, 0, 40, 255, 240])


def test_ssh_crlf_server_line_shows_the_text() -> None:
    """A server line ending in carriage return and line feed is visible."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.server_data(b"Welcome\r\n")
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert "Welcome" in rows
    assert all("\r" not in line for line in rows)


def test_ssh_page_up_stays_on_the_client() -> None:
    """Page Up, Page Down, and End move the local view and are not sent."""
    screen = Screen(10, 6)
    app = App(screen, EchoStripper(), _Restore())
    app.server_data(b"One\nTwo\nThree\nFour\n")
    app.key("pageup")
    assert app.outgoing == []
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert rows[0] == "One"
    assert rows[1] == "Two"
    app.key("pagedown")
    app.key("end")
    assert app.outgoing == []
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert rows[0] == "Three"
    assert rows[1] == "Four"


def test_ssh_backspace_erases_when_the_server_echoes_it() -> None:
    """Backspace is byte 127. An erase echo deletes one character."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.key("z")
    app.server_data(b"z")
    app.key("backspace")
    assert app.outgoing == [b"z", b"\x7f"]
    app.server_data(b"\b \b")
    bottom = screen.render().split("\n")[-1]
    assert bottom.strip() == ""
    assert "z" not in bottom
    assert "\b" not in screen.render()
    app.key("q")
    app.server_data(b"q")
    app.key("backspace")
    app.server_data(b"\b")
    assert "q" not in screen.render().split("\n")[-1]


def test_ssh_arrows_are_local_and_delete_sends_only_at_the_end() -> None:
    """Left and Right stay local. Delete is sent only at the end of the line."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.key("a")
    app.server_data(b"a")
    app.key("b")
    app.server_data(b"b")
    app.key("left")
    app.key("right")
    app.key("left")
    assert app.outgoing == [b"a", b"b"]
    app.key("delete")
    assert app.outgoing == [b"a", b"b"]
    assert screen.render().split("\n")[-1].startswith("a")
    assert "b" not in screen.render().split("\n")[-1]
    app.key("delete")
    assert app.outgoing[-1] == b"\x7f"


def test_ssh_password_then_letter_stays_on_the_prompt() -> None:
    """After a password, the next echoed letter is the prompt and not a server row."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.key("p")
    app.key("w")
    app.key("enter")
    app.server_data(b"\r\nWelcome\r\n")
    app.key("l")
    app.server_data(b"l")
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert rows[-1].startswith("l")
    assert not any(row == "l" for row in rows[:-1])
    assert "Welcome" in rows


def test_interleaved_ssh_text_keeps_the_owed_echo() -> None:
    """Server text before an owed character does not hide the prompt."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.key("h")
    app.server_data(b"Room\nh")
    rows = [line.rstrip(" ") for line in screen.render().split("\n")]
    assert "Room" in rows
    assert "h" not in rows[:-1]
    assert rows[-1].startswith("h")
    assert screen.hidden is False


def test_ssh_echo_updates_the_prompt_and_hides_when_echo_stops() -> None:
    """Echoed SSH bytes appear on the prompt. Unrelated text hides it."""
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), _Restore())
    app.key("h")
    assert app.outgoing == [b"h"]
    app.server_data(b"h")
    assert screen.render().split("\n")[-1].startswith("h")
    app.key("x")
    app.server_data(b"Nope\n")
    assert "x" not in screen.render().split("\n")[-1]
    assert "Nope" in screen.render()


def test_main_requires_host_and_port(capsys: pytest.CaptureFixture[str]) -> None:
    """A command without a host and port does not run."""
    del capsys
    try:
        code = main(["telnet"])
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 2
    assert code == 2


def test_main_refuses_a_non_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdout that is not a terminal returns 1 and does not connect."""
    called: list[str] = []

    def run(command: str, host: str, port: int) -> int:
        called.append(command)
        del host, port
        return 0

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert main(["telnet", "127.0.0.1", "9000"], run=run) == 1
    assert called == []


def test_main_refuses_a_non_terminal_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdin that is not a terminal returns 1 and does not connect."""
    called: list[str] = []

    def run(command: str, host: str, port: int) -> int:
        called.append(command)
        del host, port
        return 0

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["telnet", "127.0.0.1", "9000"], run=run) == 1
    assert called == []


def test_main_calls_run_for_telnet_and_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both commands pass the host and port through when stdout is a terminal."""
    seen: list[tuple[str, str, int]] = []

    def run(command: str, host: str, port: int) -> int:
        seen.append((command, host, port))
        return 0

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["telnet", "127.0.0.1", "9000"], run=run) == 0
    assert main(["ssh", "127.0.0.1", "9022"], run=run) == 0
    assert seen == [("telnet", "127.0.0.1", 9000), ("ssh", "127.0.0.1", 9022)]
