"""Keyboard bytes, terminal restore, and the connection loop."""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import shutil
import signal
import socket
import sys
import termios
import tty
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .app import App
from .ssh import EchoStripper, KnownHosts, pty_options
from .telnet import TelnetParser
from .terminal import Screen

_SEQUENCES = {
    b"[A": "up",
    b"[B": "down",
    b"[C": "right",
    b"[D": "left",
    b"[3~": "delete",
    b"[5~": "pageup",
    b"[6~": "pagedown",
    b"[F": "end",
    b"OF": "end",
}
_BYTES = {
    3: "ctrl-c",
    4: "ctrl-d",
    8: "backspace",
    127: "backspace",
    13: "enter",
    10: "enter",
}
_Event = tuple[str, bytes]


def _sequence_at(data: bytes, index: int) -> tuple[str | None, int]:
    """Match one escape at ``index``. A negative length is an unfinished escape."""
    if data[index] != 27:
        return None, 0
    window = data[index + 1 :]
    if window == b"":
        return None, -1
    for sequence, name in _SEQUENCES.items():
        if window.startswith(sequence):
            return name, 1 + len(sequence)
    if any(sequence.startswith(window) for sequence in _SEQUENCES):
        return None, -1
    return None, 0


def decode_keys(data: bytes) -> tuple[list[str], bytes]:
    """Return key names parsed from ``data`` and any unfinished escape tail."""
    keys: list[str] = []
    index = 0
    while index < len(data):
        name, consumed = _sequence_at(data, index)
        if consumed < 0:
            return keys, data[index:]
        if name is not None:
            keys.append(name)
            index += consumed
            continue
        byte = data[index]
        mapped = _BYTES.get(byte)
        if mapped is not None:
            keys.append(mapped)
        elif 32 <= byte < 127:
            keys.append(chr(byte))
        index += 1
    return keys, b""


def _stdin_fd() -> int:
    """Return stdin's descriptor. A captured stream falls back to descriptor 0."""
    try:
        return int(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError):
        return 0


@contextlib.contextmanager
def raw_terminal() -> Iterator[None]:
    """Put stdin in raw mode and restore the previous attributes."""
    fd = _stdin_fd()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def host_error(path: Path, host: str, port: int, fingerprint: str) -> str | None:
    """Remember a new key. Return the refusal sentence when it changed."""
    decision = KnownHosts(path).decide(host, port, fingerprint)
    if decision == "refuse":
        return f"Host key for {host}:{port} has changed."
    return None


async def open_process(conn: Any, columns: int, rows: int) -> Any:  # noqa: ANN401
    """Open an SSH process with a terminal of this size."""
    return await conn.create_process(**pty_options(columns, rows))


def _paint(screen: Screen) -> None:
    """Redraw the whole screen and leave the cursor on the input line."""
    text = screen.render().replace("\n", "\r\n")
    row = max(screen.rows, 1)
    column = screen.cursor_column()
    sys.stdout.write(f"\x1b[H\x1b[J{text}\x1b[{row};{column}H")
    sys.stdout.flush()


def _write_all(sock: socket.socket, blob: bytes) -> None:
    """Write ``blob``. Retry a short write or a blocking write."""
    send = getattr(sock, "send", None)
    if not callable(send):
        while True:
            try:
                sock.sendall(blob)
            except BlockingIOError:
                select.select([], [sock], [])
                continue
            return
    view = memoryview(blob)
    while len(view):
        try:
            sent = send(view)
        except BlockingIOError:
            select.select([], [sock], [])
            continue
        if not isinstance(sent, int) or sent <= 0:
            raise BrokenPipeError
        view = view[sent:]


def _flush(app: App, sock: socket.socket) -> None:
    """Send every queued blob and clear the queue."""
    for blob in app.outgoing:
        _write_all(sock, blob)
    app.outgoing.clear()


def _known_hosts() -> Path:
    """Return the path that remembers SSH host keys."""
    return Path.home() / ".chat-client" / "known_hosts"


def _winch() -> int | None:
    """Return the window-change signal, when this system has one."""
    number = getattr(signal, "SIGWINCH", None)
    if isinstance(number, int):
        return number
    return None


_SOCKET_GONE = (ConnectionResetError, BlockingIOError, BrokenPipeError)


def _paint_failure(app: App, screen: Screen, message: str) -> None:
    """Show ``message`` in raw mode, then restore the terminal."""
    app.fail(message)
    with raw_terminal():
        _paint(screen)


def _connection_closed(app: App, screen: Screen) -> int:
    """Report a dropped telnet socket and return 1."""
    app.fail("Connection closed.")
    _paint(screen)
    return 1


def run_client(command: str, host: str, port: int) -> int:
    """Connect, redraw, and restore the terminal on the way out."""
    size = shutil.get_terminal_size(fallback=(80, 24))
    screen = Screen(size.columns, size.lines)
    if command == "ssh":
        return _run_ssh(host, port, screen)
    return _run_telnet(host, port, screen)


def _run_telnet(host: str, port: int, screen: Screen) -> int:
    """Open the socket, then read it from a raw terminal."""
    try:
        sock = socket.create_connection((host, port), timeout=10)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        message = "Connection refused." if isinstance(exc, ConnectionRefusedError) else str(exc)
        _paint_failure(App(screen, TelnetParser(), lambda: None), screen, message)
        return 1
    return _telnet_session(sock, screen)


def _telnet_session(sock: socket.socket, screen: Screen) -> int:
    """Send the window size, then read until the session exits."""
    app = App(screen, TelnetParser(), lambda: None)
    pending = b""
    resized = False

    def _mark_resized(signum: int, frame: object | None) -> None:
        nonlocal resized
        del signum, frame
        resized = True

    winch = _winch()
    watched: int | None = None
    previous = None
    with raw_terminal():
        try:
            if winch is not None:
                previous = signal.signal(winch, _mark_resized)
                watched = winch
            app.resize(screen.columns, screen.rows)
            _flush(app, sock)
            _paint(screen)
            while not app.exited:
                if resized:
                    size = shutil.get_terminal_size(fallback=(screen.columns, screen.rows))
                    app.resize(size.columns, size.lines)
                    _flush(app, sock)
                    resized = False
                    _paint(screen)
                readable, _, _ = select.select([sys.stdin, sock], [], [], 0.2)
                if not readable:
                    continue
                updated = False
                if sock in readable and _read_telnet(app, sock):
                    return _connection_closed(app, screen)
                if sock in readable:
                    updated = True
                if sys.stdin in readable:
                    pending += os.read(sys.stdin.fileno(), 64)
                    names, pending = decode_keys(pending)
                    for name in names:
                        app.key(name)
                    _flush(app, sock)
                    updated = True
                if updated and not app.exited:
                    _paint(screen)
        except _SOCKET_GONE:
            return _connection_closed(app, screen)
        finally:
            if watched is not None:
                signal.signal(watched, signal.SIG_DFL if previous is None else previous)
            sock.close()
    return 0


def _run_ssh(host: str, port: int, screen: Screen) -> int:
    """Open SSH, refuse a changed host key, then read until exit or EOF."""
    try:
        return asyncio.run(_ssh_main(host, port, screen))
    except KeyboardInterrupt:
        return 130


def _fingerprint_error(host: str, port: int, key: object) -> str | None:
    """Return the refusal sentence, or None when this key is remembered."""
    import hashlib

    export = getattr(key, "export_public_key", None)
    if not callable(export):
        return "SSH failed."
    raw = export()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        return "SSH failed."
    fingerprint = hashlib.sha256(raw).hexdigest()
    return host_error(_known_hosts(), host, port, fingerprint)


def _host_checker(host: str, port: int) -> Any:  # noqa: ANN401
    """Return a client that checks the host key during the handshake."""
    import asyncssh

    class _Gate(asyncssh.SSHClient):
        """Accept a new or known key and refuse a changed one."""

        def __init__(self) -> None:
            """Start with no refusal."""
            self.refusal: str | None = None

        def validate_host_public_key(
            self,
            host_name: str,
            addr: str,
            port_number: int,
            key: object,
        ) -> bool:
            """Remember a first key. Refuse a key that does not match."""
            del host_name, addr, port_number
            refusal = _fingerprint_error(host, port, key)
            if refusal is not None:
                self.refusal = refusal
                return False
            return True

    return _Gate()


async def _ssh_main(host: str, port: int, screen: Screen) -> int:
    """Connect, check the host key, and keep a successful session open."""
    import asyncssh

    app = App(screen, EchoStripper(), lambda: None)
    checker = _host_checker(host, port)
    conn = None
    try:
        try:
            conn = await asyncssh.connect(
                host,
                port=port,
                username="chat",
                client_keys=None,
                agent_path=None,
                known_hosts=b"\n",
                client_factory=lambda: checker,
            )
        except KeyboardInterrupt:
            return 130
        except (OSError, asyncssh.Error) as exc:
            message = checker.refusal or str(exc) or "SSH failed."
            _paint_failure(app, screen, message)
            return 1
        try:
            process = await open_process(conn, screen.columns, screen.rows)
        except KeyboardInterrupt:
            return 130
        except (OSError, asyncssh.Error) as exc:
            _paint_failure(app, screen, str(exc) or "SSH failed.")
            return 1
        with raw_terminal():
            return await _read_ssh(process, screen, app)
    except KeyboardInterrupt:
        return 130
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


def _watch_resize(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
    """Listen for a terminal resize. Missing support is ignored."""
    number = _winch()
    if number is None:
        return
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.add_signal_handler(number, callback)


def _ignore_resize(loop: asyncio.AbstractEventLoop) -> None:
    """Stop listening for a terminal resize."""
    number = _winch()
    if number is None:
        return
    with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
        loop.remove_signal_handler(number)


def _stdin_chunk(fd: int) -> tuple[str, bytes]:
    """Return stdin bytes, or an error marker when the read fails."""
    try:
        return "in", os.read(fd, 64)
    except OSError:
        return "err", b""


async def _read_ssh(process: Any, screen: Screen, app: App) -> int:  # noqa: ANN401
    """Read process stdout and write key bytes until ctrl-c or EOF."""
    loop = asyncio.get_running_loop()
    keys_in: asyncio.Queue[bytes] = asyncio.Queue()
    server_out: asyncio.Queue[bytes] = asyncio.Queue()
    stdin_error = asyncio.Event()
    fd = sys.stdin.fileno()

    def on_stdin() -> None:
        kind, chunk = _stdin_chunk(fd)
        if kind == "err":
            stdin_error.set()
            loop.remove_reader(fd)
            keys_in.put_nowait(b"")
            return
        if chunk == b"":
            loop.remove_reader(fd)
            return
        keys_in.put_nowait(chunk)

    def on_resize() -> None:
        size = shutil.get_terminal_size(fallback=(screen.columns, screen.rows))
        app.resize(size.columns, size.lines)
        process.change_terminal_size(size.columns, size.lines)
        _paint(screen)

    loop.add_reader(fd, on_stdin)
    _watch_resize(loop, on_resize)
    pump = asyncio.create_task(_copy_stdout(process, server_out))
    try:
        return await _ssh_turns(process, screen, app, keys_in, server_out, stdin_error)
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        loop.remove_reader(fd)
        _ignore_resize(loop)
        process.close()


async def _copy_stdout(process: Any, queue: asyncio.Queue[bytes]) -> None:  # noqa: ANN401
    """Push stdout chunks. An empty chunk means EOF."""
    while True:
        data = await _read_chunk(process)
        if data is None:
            continue
        await queue.put(data)
        if data == b"":
            return


async def _read_chunk(process: Any) -> bytes | None:  # noqa: ANN401
    """Return stdout bytes, None to retry, or empty bytes at EOF."""
    import asyncssh

    try:
        data = await process.stdout.read(4096)
    except (asyncssh.TerminalSizeChanged, asyncssh.SignalReceived, asyncssh.BreakReceived):
        return None
    except (asyncssh.Error, OSError):
        return b""
    if isinstance(data, bytes):
        return data
    return b""


async def _ssh_turns(
    process: Any,  # noqa: ANN401
    screen: Screen,
    app: App,
    keys_in: asyncio.Queue[bytes],
    server_out: asyncio.Queue[bytes],
    stdin_error: asyncio.Event,
) -> int:
    """Apply keyboard and server bytes until ctrl-c or stdout EOF."""
    pending = b""
    _paint(screen)
    while not app.exited:
        if stdin_error.is_set():
            app.fail("Connection closed.")
            _paint(screen)
            return 1
        events = await _wait_ssh(keys_in, server_out)
        if stdin_error.is_set():
            app.fail("Connection closed.")
            _paint(screen)
            return 1
        if not events:
            continue
        pending = _apply_keys(app, events, pending)
        if app.exited:
            await _write_process(app, process)
            _paint(screen)
            return 0
        if _take_server(app, server_out, events):
            app.fail("Connection closed.")
            _paint(screen)
            return 1
        if not await _write_process(app, process):
            app.fail("Connection closed.")
            _paint(screen)
            return 1
        _paint(screen)
    return 0


def _apply_keys(app: App, events: list[_Event], pending: bytes) -> bytes:
    """Feed keyboard chunks through ``decode_keys`` and ``App.key``."""
    for kind, data in events:
        if kind != "in":
            continue
        pending += data
        names, pending = decode_keys(pending)
        for name in names:
            app.key(name)
    return pending


def _read_telnet(app: App, sock: socket.socket) -> bool:
    """Read every waiting chunk. Return True when the socket has closed."""
    while True:
        data = sock.recv(4096)
        if data == b"":
            return True
        app.server_data(data)
        _flush(app, sock)
        ready, _, _ = select.select([sock], [], [], 0)
        if not ready:
            app.seal_prompt()
            return False


def _take_server(app: App, queue: asyncio.Queue[bytes], events: list[_Event]) -> bool:
    """Apply queued stdout. Return True at EOF. Seal the prompt when idle."""
    if _apply_server(app, events):
        return True
    saw = any(kind == "out" for kind, _data in events)
    while True:
        try:
            data = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if data == b"":
            return True
        app.server_data(data)
        saw = True
    if saw:
        app.seal_prompt()
    return False


def _apply_server(app: App, events: list[_Event]) -> bool:
    """Feed stdout into the app. Return True when stdout reached EOF."""
    for kind, data in events:
        if kind != "out":
            continue
        if data == b"":
            return True
        app.server_data(data)
    return False


async def _wait_ssh(
    keys_in: asyncio.Queue[bytes],
    server_out: asyncio.Queue[bytes],
) -> list[_Event]:
    """Wait briefly for keyboard bytes or a stdout chunk."""
    incoming = asyncio.create_task(keys_in.get())
    outgoing = asyncio.create_task(server_out.get())
    done, pending = await asyncio.wait(
        {incoming, outgoing},
        timeout=0.2,
        return_when=asyncio.FIRST_COMPLETED,
    )
    events: list[_Event] = []
    if incoming in done:
        events.append(("in", incoming.result()))
    if outgoing in done:
        events.append(("out", outgoing.result()))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return events


async def _write_process(app: App, process: Any) -> bool:  # noqa: ANN401
    """Write queued bytes to process stdin. Return False if the write fails."""
    import asyncssh

    if not app.outgoing:
        return True
    try:
        for blob in app.outgoing:
            process.stdin.write(blob)
        app.outgoing.clear()
        await process.stdin.drain()
    except (asyncssh.Error, OSError):
        app.outgoing.clear()
        return False
    return True
