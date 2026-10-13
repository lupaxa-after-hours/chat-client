"""Key decoding, terminal restore, and connection startup."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from lupaxa.chat_client.session import (
    decode_keys,
    host_error,
    open_process,
    raw_terminal,
    run_client,
)
from lupaxa.chat_client.ssh import pty_options
from lupaxa.chat_client.telnet import TelnetParser


def test_decode_keys_maps_editing_and_holds_a_partial_escape() -> None:
    """Known sequences become key names. A split escape waits for the rest."""
    keys, rest = decode_keys(b"a\x03\x04\x7f\r")
    assert keys == ["a", "ctrl-c", "ctrl-d", "backspace", "enter"]
    assert rest == b""
    keys, rest = decode_keys(b"\x1b")
    assert keys == []
    assert rest == b"\x1b"
    keys, rest = decode_keys(rest + b"[5~")
    assert keys == ["pageup"]
    assert rest == b""
    keys, rest = decode_keys(b"\x1b[5~\x1b[6~\x1b[F")
    assert keys == ["pageup", "pagedown", "end"]
    assert rest == b""
    keys, rest = decode_keys(b"\x1b[A\x1b[B\x1b[C\x1b[D")
    assert keys == ["up", "down", "right", "left"]
    assert rest == b""
    keys, rest = decode_keys(b"\x1b[3")
    assert keys == []
    assert rest == b"\x1b[3"
    keys, rest = decode_keys(rest + b"~")
    assert keys == ["delete"]
    assert rest == b""
    keys, rest = decode_keys(b"[A[F[5~OF")
    assert keys == ["[", "A", "[", "F", "[", "5", "~", "O", "F"]
    assert rest == b""


def test_raw_terminal_restores_after_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaving the context manager puts the old attributes back."""
    saved: dict[str, object] = {}
    monkeypatch.setattr("lupaxa.chat_client.session.termios.tcgetattr", lambda fd: ["old"])
    monkeypatch.setattr("lupaxa.chat_client.session.tty.setraw", lambda fd: None)

    def tcsetattr(fd: int, when: int, attr: object) -> None:
        del fd, when
        saved["attr"] = attr

    monkeypatch.setattr("lupaxa.chat_client.session.termios.tcsetattr", tcsetattr)
    try:
        with raw_terminal():
            raise RuntimeError("boom")
    except RuntimeError:
        assert saved["attr"] == ["old"]


def test_changed_host_key_is_an_error(tmp_path: Path) -> None:
    """A new fingerprint is stored. A different one returns the refusal line."""
    path = tmp_path / "known_hosts"
    assert host_error(path, "127.0.0.1", 9022, "aaa") is None
    assert host_error(path, "127.0.0.1", 9022, "bbb") == (
        "Host key for 127.0.0.1:9022 has changed."
    )


def test_open_process_requests_the_pty() -> None:
    """The SSH process is opened with the terminal options."""

    class _Conn:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        async def create_process(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "process"

    conn = _Conn()
    assert asyncio.run(open_process(conn, 80, 24)) == "process"
    assert conn.kwargs == pty_options(80, 24)


def _refuse(address: object, timeout: object = None) -> None:
    """Stand in for a refused TCP connect."""
    del address, timeout
    raise ConnectionRefusedError("no")


def test_telnet_connect_sends_the_window_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new telnet session sends the current window size before it reads."""
    sent: list[bytes] = []

    class _Sock:
        def setblocking(self, _blocking: bool) -> None:
            return None

        def sendall(self, blob: bytes) -> None:
            sent.append(blob)

        def recv(self, size: int) -> bytes:
            del size
            return b""

        def close(self) -> None:
            return None

    sock = _Sock()

    def connect(address: object, timeout: object = None) -> _Sock:
        del address, timeout
        return sock

    monkeypatch.setattr("lupaxa.chat_client.session.socket.create_connection", connect)
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    monkeypatch.setattr(
        "lupaxa.chat_client.session.shutil.get_terminal_size",
        lambda fallback=(80, 24): os.terminal_size((80, 24)),
    )
    monkeypatch.setattr(
        "lupaxa.chat_client.session.select.select",
        lambda reads, writes, errors, timeout=None: ([sock], [], []),
    )
    assert run_client("telnet", "127.0.0.1", 9000) == 1
    assert sent[:1] == [TelnetParser().window_size(80, 24)]


def _interrupt(address: object, timeout: object = None) -> None:
    """Stand in for Ctrl-C during connect."""
    del address, timeout
    raise KeyboardInterrupt


class _RecordingRaw:
    """Remember whether the terminal was put into raw mode."""

    def __init__(self) -> None:
        self.entered = False

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        self.entered = True
        yield


@pytest.mark.parametrize("error", [ConnectionResetError, BlockingIOError, BrokenPipeError])
def test_telnet_recv_errors_are_connection_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: type[OSError],
) -> None:
    """Recv failures close the socket, report it, and exit 1."""
    closed: list[bool] = []

    class _Sock:
        def setblocking(self, _blocking: bool) -> None:
            return None

        def sendall(self, _blob: bytes) -> None:
            return None

        def recv(self, size: int) -> bytes:
            del size
            raise error

        def close(self) -> None:
            closed.append(True)

    sock = _Sock()

    def connect(address: object, timeout: object = None) -> _Sock:
        del address, timeout
        return sock

    monkeypatch.setattr("lupaxa.chat_client.session.socket.create_connection", connect)
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    monkeypatch.setattr(
        "lupaxa.chat_client.session.select.select",
        lambda reads, writes, errors, timeout=None: ([sock], [], []),
    )
    assert run_client("telnet", "127.0.0.1", 9000) == 1
    assert closed == [True]
    assert "Connection closed." in capsys.readouterr().out


def test_telnet_first_flush_error_closes_the_socket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A window-size send that fails still closes the socket."""
    closed: list[bool] = []

    class _Sock:
        def setblocking(self, _blocking: bool) -> None:
            return None

        def sendall(self, blob: bytes) -> None:
            del blob
            raise BrokenPipeError("gone")

        def close(self) -> None:
            closed.append(True)

    sock = _Sock()

    def connect(address: object, timeout: object = None) -> _Sock:
        del address, timeout
        return sock

    monkeypatch.setattr("lupaxa.chat_client.session.socket.create_connection", connect)
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    assert run_client("telnet", "127.0.0.1", 9000) == 1
    assert closed == [True]
    assert "Connection closed." in capsys.readouterr().out


def test_ctrl_c_during_telnet_connect_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C while connecting exits 130 and does not enter raw mode."""
    raw = _RecordingRaw()
    monkeypatch.setattr("lupaxa.chat_client.session.socket.create_connection", _interrupt)
    monkeypatch.setattr("lupaxa.chat_client.session.raw_terminal", raw)
    assert run_client("telnet", "127.0.0.1", 9) == 130
    assert raw.entered is False


def test_ctrl_c_during_ssh_connect_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C while SSH connects exits 130 and does not enter raw mode."""
    import asyncssh

    async def connect(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise KeyboardInterrupt

    raw = _RecordingRaw()
    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr("lupaxa.chat_client.session.raw_terminal", raw)
    assert run_client("ssh", "127.0.0.1", 9022) == 130
    assert raw.entered is False


def test_refused_telnet_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused socket exits 1 and still enters the restore context."""
    raw = _RecordingRaw()
    monkeypatch.setattr("lupaxa.chat_client.session.socket.create_connection", _refuse)
    monkeypatch.setattr("lupaxa.chat_client.session.raw_terminal", raw)
    assert run_client("telnet", "127.0.0.1", 9) == 1
    assert raw.entered is True


def test_paint_places_the_cursor_on_the_input_line(capsys: pytest.CaptureFixture[str]) -> None:
    """After a redraw the cursor sits on the prompt at the input column."""
    from lupaxa.chat_client.session import _paint
    from lupaxa.chat_client.terminal import Screen

    screen = Screen(10, 6)
    screen.key("a")
    _paint(screen)
    assert capsys.readouterr().out.endswith("\x1b[6;2H")


def test_telnet_idle_timeout_does_not_repaint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A select timeout does not redraw. A later socket read still does."""
    paints: list[int] = []

    def paint(screen: object) -> None:
        del screen
        paints.append(1)

    class _Sock:
        def sendall(self, _blob: bytes) -> None:
            return None

        def recv(self, size: int) -> bytes:
            del size
            return b""

        def close(self) -> None:
            return None

    sock = _Sock()
    calls = {"n": 0}

    def select(
        reads: list[object],
        writes: list[object],
        errors: list[object],
        timeout: float | None = None,
    ) -> tuple[list[object], list[object], list[object]]:
        del reads, errors, timeout
        if writes:
            return ([], list(writes), [])
        calls["n"] += 1
        if calls["n"] == 1:
            return ([], [], [])
        return ([sock], [], [])

    monkeypatch.setattr("lupaxa.chat_client.session._paint", paint)
    monkeypatch.setattr(
        "lupaxa.chat_client.session.socket.create_connection",
        lambda address, timeout=None: sock,
    )
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    monkeypatch.setattr("lupaxa.chat_client.session.select.select", select)
    assert run_client("telnet", "127.0.0.1", 9000) == 1
    assert paints == [1, 1]


def test_telnet_write_retries_a_short_or_blocking_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short write and a blocking write are retried. The socket stays open."""
    written = bytearray()
    reads: list[int] = []

    class _Sock:
        def __init__(self) -> None:
            self._writes = 0

        def send(self, blob: bytes) -> int:
            self._writes += 1
            data = bytes(blob)
            if self._writes == 1:
                raise BlockingIOError
            if self._writes == 2:
                written.extend(data[:1])
                return 1
            written.extend(data)
            return len(data)

        def recv(self, size: int) -> bytes:
            reads.append(size)
            return b""

        def close(self) -> None:
            return None

    sock = _Sock()

    def select(
        reads_ready: list[object],
        writes: list[object],
        errors: list[object],
        timeout: float | None = None,
    ) -> tuple[list[object], list[object], list[object]]:
        del reads_ready, errors, timeout
        if writes:
            return ([], list(writes), [])
        return ([sock], [], [])

    monkeypatch.setattr(
        "lupaxa.chat_client.session.socket.create_connection",
        lambda address, timeout=None: sock,
    )
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    monkeypatch.setattr("lupaxa.chat_client.session.select.select", select)
    assert run_client("telnet", "127.0.0.1", 9000) == 1
    assert reads
    assert written[:1] == bytes([255])


def test_ssh_checks_the_host_key_during_the_handshake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The handshake refuses a changed key and does not offer user keys."""
    import asyncssh

    path = tmp_path / "known_hosts"
    opened: list[str] = []

    class _Key:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def export_public_key(self) -> bytes:
            return self._raw

    async def connect(*args: object, **kwargs: object) -> None:
        del args
        assert kwargs["client_keys"] is None
        assert kwargs["agent_path"] is None
        assert kwargs["known_hosts"] == b"\n"
        factory = kwargs["client_factory"]
        assert callable(factory)
        client = factory()
        assert client.validate_host_public_key("other", "addr", 1, _Key(b"one")) is True
        assert client.validate_host_public_key("other", "addr", 1, _Key(b"two")) is False
        raise asyncssh.HostKeyNotVerifiable("Host key is not trusted")

    async def open_process(*args: object, **kwargs: object) -> None:
        del args, kwargs
        opened.append("yes")

    monkeypatch.setattr("lupaxa.chat_client.session._known_hosts", lambda: path)
    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr("lupaxa.chat_client.session.open_process", open_process)
    monkeypatch.setattr(
        "lupaxa.chat_client.session.raw_terminal",
        __import__("contextlib").nullcontext,
    )
    assert run_client("ssh", "127.0.0.1", 9022) == 1
    assert opened == []
    assert "Host key for 127.0.0.1:9022 has changed." in capsys.readouterr().out


def test_ssh_stdin_error_stops_the_read_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OSError on SSH stdin exits once and does not spin."""
    from lupaxa.chat_client.app import App
    from lupaxa.chat_client.session import _read_ssh
    from lupaxa.chat_client.ssh import EchoStripper
    from lupaxa.chat_client.terminal import Screen

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"x")
    calls = {"n": 0}

    def boom(fd: int, size: int) -> bytes:
        del fd, size
        calls["n"] += 1
        raise OSError(5, "boom")

    class _Stdin:
        def fileno(self) -> int:
            return read_fd

    class _In:
        def write(self, _blob: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    class _Out:
        async def read(self, size: int) -> bytes:
            del size
            await asyncio.sleep(30)
            return b""

    class _Proc:
        stdin = _In()
        stdout = _Out()

        def change_terminal_size(self, _columns: int, _rows: int) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("lupaxa.chat_client.session.os.read", boom)
    monkeypatch.setattr("lupaxa.chat_client.session.sys.stdin", _Stdin())
    screen = Screen(20, 8)
    app = App(screen, EchoStripper(), lambda: None)
    try:
        code = asyncio.run(asyncio.wait_for(_read_ssh(_Proc(), screen, app), 2))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert code == 1
    assert calls["n"] < 10
    assert app.exited is True
