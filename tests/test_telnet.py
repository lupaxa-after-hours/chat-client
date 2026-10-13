"""Telnet negotiation, echo, and window size."""

from __future__ import annotations

from lupaxa.chat_client.telnet import TelnetParser


def test_plain_text_passes_through() -> None:
    """Bytes that are not negotiation are returned as text."""
    parser = TelnetParser()
    assert parser.feed(b"Hello\n") == "Hello\n"
    assert parser.local_echo is True


def test_negotiation_is_removed() -> None:
    """A will-echo command is not part of the visible text."""
    parser = TelnetParser()
    assert parser.feed(b"Hi" + bytes([255, 251, 1]) + b"!\n") == "Hi!\n"


def test_partial_sequence_is_held_back() -> None:
    """A split negotiation command stays buffered until it is complete."""
    parser = TelnetParser()
    assert parser.feed(b"A" + bytes([255])) == "A"
    assert parser.feed(bytes([251, 1]) + b"B") == "B"
    assert parser.hidden is True


def test_echo_off_hides_and_echo_on_shows() -> None:
    """Will-echo hides input. Wont-echo shows it again."""
    parser = TelnetParser()
    parser.feed(bytes([255, 251, 1]))
    assert parser.hidden is True
    parser.feed(bytes([255, 252, 1]))
    assert parser.hidden is False


def test_doubled_iac_is_one_byte() -> None:
    """Two 255 bytes are a single literal 255 in the text."""
    parser = TelnetParser()
    assert parser.feed(bytes([255, 255])) == "\u00ff"


def test_window_size_matches_the_server() -> None:
    """The reported size is the NAWS sequence the server already reads."""
    parser = TelnetParser()
    assert parser.window_size(80, 24) == bytes([255, 250, 31, 0, 80, 0, 24, 255, 240])


def test_window_size_doubles_a_byte_of_255() -> None:
    """A width or height byte of 255 is sent as 255 255."""
    parser = TelnetParser()
    assert parser.window_size(255, 24) == bytes([255, 250, 31, 0, 255, 255, 0, 24, 255, 240])
    assert parser.window_size(80, 255) == bytes([255, 250, 31, 0, 80, 0, 255, 255, 255, 240])


def test_send_line_ends_with_crlf() -> None:
    """Enter sends the line and a carriage return plus line feed."""
    parser = TelnetParser()
    assert parser.send_line("look") == b"look\r\n"
    assert parser.send_key("l") == b""


def test_utf8_text_is_decoded() -> None:
    """Visible bytes from the server are decoded as UTF-8."""
    parser = TelnetParser()
    assert parser.feed(b"caf\xc3\xa9\n") == "caf\u00e9\n"
