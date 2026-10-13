"""SSH echo stripping, the PTY request, and known hosts."""

from __future__ import annotations

from pathlib import Path

from lupaxa.chat_client.ssh import EchoStripper, KnownHosts, pty_options


def test_pty_options_request_a_terminal() -> None:
    """The server learns the window size only when a terminal is requested."""
    assert pty_options(80, 24) == {
        "term_type": "xterm",
        "term_size": (80, 24),
        "encoding": None,
    }


def test_echoed_keystrokes_are_removed_from_the_server_stream() -> None:
    """Bytes the server echoes are not returned as server text."""
    stripper = EchoStripper()
    assert stripper.local_echo is False
    sent = stripper.send_key("h")
    assert sent == b"h"
    assert stripper.feed(b"hRoom\n") == "Room\n"
    assert stripper.take_echo() == "h"
    assert stripper.hidden is False


def test_missing_echo_leaves_nothing_to_show() -> None:
    """Server text that is not an echo is kept, and the typed byte is not echoed."""
    stripper = EchoStripper()
    stripper.sent(b"a")
    assert stripper.feed(b"Welcome\n") == "Welcome\n"
    assert stripper.take_echo() == ""
    assert stripper.hidden is True
    assert stripper.feed(b"a") == ""
    assert stripper.take_echo() == "a"
    assert stripper.hidden is False


def test_enter_sends_crlf() -> None:
    """Enter sends a carriage return and line feed, which echo is stripped."""
    stripper = EchoStripper()
    assert stripper.send_enter() == b"\r\n"
    assert stripper.feed(b"\r\nOk\n") == "Ok\n"
    assert stripper.take_echo() == ""


def test_known_host_is_remembered_then_refused_when_it_changes(tmp_path: Path) -> None:
    """The first key is stored. A different key for the same host and port is refused."""
    path = tmp_path / "known_hosts"
    hosts = KnownHosts(path)
    assert hosts.decide("127.0.0.1", 9022, "aaa") == "remember"
    assert hosts.decide("127.0.0.1", 9022, "aaa") == "accept"
    assert hosts.decide("127.0.0.1", 9022, "bbb") == "refuse"
    assert path.read_text(encoding="utf-8") == "127.0.0.1 9022 aaa\n"


def test_blank_and_short_known_host_lines_are_skipped(tmp_path: Path) -> None:
    """A blank line or a line with too few fields is ignored."""
    path = tmp_path / "known_hosts"
    path.write_text("\nshort\n127.0.0.1 9022 aaa\n", encoding="utf-8")
    hosts = KnownHosts(path)
    assert hosts.decide("127.0.0.1", 9022, "aaa") == "accept"


def test_split_utf8_is_one_character() -> None:
    """A UTF-8 character split across two chunks stays one character."""
    stripper = EchoStripper()
    assert stripper.feed(b"caf\xc3") == "caf"
    assert stripper.feed(b"\xa9\n") == "\u00e9\n"


def test_password_bytes_drop_when_enter_is_echoed() -> None:
    """Password bytes vanish once enter is echoed, then the next letter is echo."""
    stripper = EchoStripper()
    stripper.send_key("p")
    stripper.send_key("w")
    stripper.send_enter()
    assert "Welcome" in stripper.feed(b"\r\nWelcome\r\n")
    assert stripper.take_echo() == ""
    assert stripper.hidden is False
    stripper.send_key("l")
    assert stripper.feed(b"l") == ""
    assert stripper.take_echo() == "l"


def test_owed_echo_is_not_taken_from_the_middle_of_a_line() -> None:
    """An owed letter inside a server line stays in that line."""
    stripper = EchoStripper()
    stripper.send_key("e")
    assert stripper.feed(b"Bob: hello\r\n") == "Bob: hello\r\n"
    assert stripper.take_echo() == ""
    assert stripper.feed(b"e") == ""
    assert stripper.take_echo() == "e"


def test_server_text_before_the_echo_keeps_it() -> None:
    """Owed echo after server text is still echo, and the prompt is not hidden."""
    stripper = EchoStripper()
    stripper.sent(b"h")
    assert stripper.feed(b"Room\nh") == "Room\n"
    assert stripper.take_echo() == "h"
    assert stripper.hidden is False
