"""Screen state, wrapping, and the local prompts."""

from __future__ import annotations

from lupaxa.chat_client.terminal import Screen


def _lines(screen: Screen) -> list[str]:
    rendered = screen.render()
    lines = rendered.split("\n")
    assert len(lines) == screen.rows
    assert all(len(line) == screen.columns for line in lines)
    return lines


def test_blank_screen_keeps_the_bar_and_three_prompts() -> None:
    """Four reserved rows sit under an empty server area."""
    lines = _lines(Screen(10, 6))
    assert lines[2] == "----------"
    assert lines[3:] == ["          ", "          ", "          "]


def test_server_text_sits_above_the_bar() -> None:
    """A new server line is the last line of the server area."""
    screen = Screen(10, 6)
    screen.add_server_text("Hello\n")
    assert _lines(screen)[1] == "Hello     "
    assert _lines(screen)[2] == "----------"


def test_server_line_does_not_touch_a_half_typed_word() -> None:
    """Incoming text leaves the bottom prompt unchanged."""
    screen = Screen(10, 6)
    screen.key("h")
    screen.key("i")
    screen.add_server_text("Room\n")
    lines = _lines(screen)
    assert lines[1] == "Room      "
    assert lines[5] == "hi        "


def test_page_up_holds_the_view_while_more_lines_arrive() -> None:
    """Paging up stays put when later server lines are stored."""
    screen = Screen(10, 6)
    screen.add_server_text("One\nTwo\nThree\nFour\n")
    screen.key("pageup")
    held = _lines(screen)[:2]
    screen.add_server_text("Five\n")
    assert _lines(screen)[:2] == held


def test_end_returns_to_the_latest_line() -> None:
    """End shows the newest server lines and does not move the input cursor."""
    screen = Screen(10, 6)
    screen.add_server_text("One\nTwo\nThree\nFour\n")
    screen.key("a")
    screen.key("left")
    screen.key("pageup")
    screen.key("end")
    lines = _lines(screen)
    assert lines[0] == "Three     "
    assert lines[1] == "Four      "
    screen.key("b")
    assert _lines(screen)[5] == "ba        "


def test_enter_shifts_the_two_prompt_lines() -> None:
    """Enter keeps the last two commands and clears the bottom line."""
    screen = Screen(10, 6)
    screen.key("l")
    screen.key("o")
    screen.key("o")
    screen.key("k")
    assert screen.key("enter") == "look"
    screen.key("n")
    assert screen.key("enter") == "n"
    lines = _lines(screen)
    assert lines[3] == "look      "
    assert lines[4] == "n         "
    assert lines[5] == "          "


def test_empty_enter_is_sent() -> None:
    """Enter on a blank prompt returns an empty line and shifts it up."""
    screen = Screen(10, 6)
    assert screen.key("enter") == ""
    assert _lines(screen)[4] == "          "


def test_hidden_password_is_not_shifted_up() -> None:
    """A hidden line is returned for sending and does not stay on screen."""
    screen = Screen(10, 6)
    screen.key("l")
    screen.key("o")
    screen.key("o")
    screen.key("k")
    screen.key("enter")
    screen.set_hidden(True)
    screen.key("s")
    screen.key("e")
    screen.key("c")
    assert "sec" not in screen.render()
    assert screen.key("enter") == "sec"
    lines = _lines(screen)
    assert lines[3] == "          "
    assert lines[4] == "look      "
    assert "sec" not in screen.render()
    assert screen.hidden is True


def test_width_change_rewraps_from_the_original_line() -> None:
    """Narrowing and widening uses the stored line, not the old wrap."""
    screen = Screen(10, 6)
    screen.add_server_text("abcdefghijXY\n")
    screen.resize(5, 6)
    assert _lines(screen)[0] == "fghij"
    assert _lines(screen)[1] == "XY   "
    screen.resize(12, 6)
    assert _lines(screen)[1] == "abcdefghijXY"


def test_long_input_stays_inside_the_bottom_prompt() -> None:
    """A long line scrolls inside the bottom row and does not move the bar."""
    screen = Screen(6, 6)
    for char in "abcdef":
        screen.key(char)
    lines = _lines(screen)
    assert lines[2] == "------"
    assert lines[5] == "bcdef "
    assert len(lines[5]) == 6


def test_short_terminal_still_reserves_the_prompts() -> None:
    """Fewer than four rows shows no server area and keeps the bottom prompt."""
    screen = Screen(8, 2)
    screen.add_server_text("Hello\n")
    lines = _lines(screen)
    assert "Hello" not in lines[0]
    assert lines[1].strip() == ""


def test_split_carriage_return_and_line_feed_are_one_newline() -> None:
    """A CR at the end of one chunk and an LF at the start of the next are one break."""
    screen = Screen(20, 8)
    screen.add_server_text("Welcome\r")
    screen.add_server_text("\nNext\n")
    rows = [line.rstrip(" ") for line in _lines(screen)]
    welcome = rows.index("Welcome")
    assert rows[welcome + 1] == "Next"


def test_bottom_line_uses_the_server_prompt() -> None:
    """The name, the password, and the player prompt are the bottom line."""
    screen = Screen(40, 8)
    screen.add_server_text("Welcome\nPlease enter your name: ")
    screen.seal_prompt()
    for char in "Tim":
        screen.key(char)
    assert _lines(screen)[-1].startswith("Please enter your name: Tim")
    assert screen.key("enter") == "Tim"
    assert _lines(screen)[-2].startswith("Please enter your name: Tim")
    screen.add_server_text("Password: ")
    screen.seal_prompt()
    screen.set_hidden(True)
    screen.key("s")
    screen.key("e")
    screen.key("c")
    assert _lines(screen)[-1].startswith("Password: ")
    assert "sec" not in _lines(screen)[-1]
    assert screen.key("enter") == "sec"
    screen.set_hidden(False)
    screen.add_server_text("What? > ")
    screen.seal_prompt()
    screen.key("l")
    assert _lines(screen)[-1].startswith("What? > l")


def test_talker_prompt_stays_out_of_the_server_area() -> None:
    """The talker's prompt is not history. The next message replaces it."""
    screen = Screen(40, 8)
    screen.add_server_text("Welcome\n\nPlease enter your name: ")
    rows = [line.rstrip(" ") for line in _lines(screen)]
    assert "Welcome" in rows
    assert all("name" not in row for row in rows)
    screen.seal_prompt()
    assert _lines(screen)[-1].startswith("Please enter your name: ")
    screen.add_server_text("You say 'hi'\nWhat? > ")
    screen.seal_prompt()
    rows = [line.rstrip(" ") for line in _lines(screen)]
    assert "You say 'hi'" in rows
    assert rows[-1].startswith("What? >")
    assert all("name" not in row and "What?" not in row for row in rows[:-1])


def test_echoed_enter_does_not_keep_the_talker_prompt() -> None:
    """The enter echo is not a blank row, and the old prompt is not kept."""
    screen = Screen(40, 6)
    screen.add_server_text("One\nWhat? > ")
    screen.seal_prompt()
    screen.add_server_text("\r\nTwo\r\nWhat? > ")
    rows = [line.rstrip(" ") for line in _lines(screen)]
    assert rows[0] == "One"
    assert rows[1] == "Two"
    assert rows[-1].startswith("What? >")


def test_open_line_appends_until_newline() -> None:
    """A chunk with no newline stays open and the next chunk continues it."""
    screen = Screen(20, 8)
    screen.add_server_text("Wel")
    screen.add_server_text("come\n")
    rows = [line.rstrip(" ") for line in _lines(screen)]
    assert "Welcome" in rows
    assert "Wel" not in rows
    assert "come" not in rows


def test_cursor_column_follows_the_input() -> None:
    """The reported column is where the next character will be drawn."""
    screen = Screen(10, 6)
    assert screen.cursor_column() == 1
    assert screen.cursor_at_end() is True
    screen.key("a")
    assert screen.cursor_column() == 2
    screen.key("left")
    assert screen.cursor_column() == 1
    assert screen.cursor_at_end() is False


def test_up_and_down_do_not_recall_commands() -> None:
    """Up and Down leave the prompt and the sent commands alone."""
    screen = Screen(10, 6)
    screen.key("l")
    screen.key("o")
    screen.key("o")
    screen.key("k")
    screen.key("enter")
    screen.key("up")
    screen.key("down")
    assert _lines(screen)[5] == "          "
