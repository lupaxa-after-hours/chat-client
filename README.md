<p align="center">
  <a href="https://github.com/lupaxa-after-hours">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/organisations/after-hours/readme-logo.png" alt="After Hours" />
  </a>
</p>

<h1 align="center">Chat Client</h1>

A terminal client for the [chat server](https://github.com/lupaxa-after-hours/chat-server).
Text from the server stays in the upper part of the screen. The line you are
typing stays at the bottom, and new text does not clear it.

The bottom line is the prompt the server just sent: the name prompt, a
password prompt, or the player's own prompt. The two lines above it are the
last two lines you sent, still showing the prompt that was in use then. A
bar separates that area from the history. The server's prompt is not copied
into the history.

## Install

```bash
pip install lupaxa-chat-client
```

Requires Python 3.10+.

> **Note:** `chat-client` only runs in a terminal. It stops before connecting
> when standard input or standard output is not a terminal.

## Run

The host and the port are required. Use the port the server is listening on.

```bash
chat-client telnet 127.0.0.1 9000
chat-client ssh 127.0.0.1 9022
```

Telnet and SSH both show the server's normal text. On telnet the line is
edited locally and sent when you press Enter. On SSH each character is sent
as you type, and the server echoes it onto the prompt. SSH signs in as the
user `chat`.

The first SSH host key is saved in `~/.chat-client/known_hosts`. A key that
no longer matches is refused.

## The Screen

Server lines wrap to the width of the terminal. Narrowing or widening the
window wraps them again from the original lines. Page Up keeps the same rows
on screen while more text arrives. End, or Page Down back to the bottom,
shows the newest lines.

A password prompt hides the characters you type. Ctrl-C leaves and restores
the terminal.

## Keys

Scrolling and editing stay on this client. They are not sent to the server.

| Key            | What it Does                                    |
| -------------- | ----------------------------------------------- |
| Page Up        | Scroll the history up by one screen.            |
| Page Down      | Scroll the history down by one screen.          |
| End            | Jump back to the newest server lines.           |
| Left and Right | Move the cursor along the line you are typing.  |
| Backspace      | Delete the character before the cursor.         |
| Delete         | Delete the character at the cursor.             |
| Enter          | Send the line.                                  |
| Ctrl-C         | Leave and restore the terminal.                 |

Up and Down do not recall earlier lines. Ctrl-D does not quit.

<a href="https://github.com/the-lupaxa-project">
    <img src="https://raw.githubusercontent.com/the-lupaxa-project/brand-assets/master/logos/components/footer-for-child-orgs.svg" alt="The Lupaxa Project Footer" width="100%" />
</a>
