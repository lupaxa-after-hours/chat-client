"""Command line for telnet and SSH."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable


def main(argv: list[str] | None = None, run: Callable[[str, str, int], int] | None = None) -> int:
    """Parse the command. Return 1 when stdin or stdout is not a terminal."""
    parser = argparse.ArgumentParser(prog="chat-client")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("telnet", "ssh"):
        sub = commands.add_parser(name)
        sub.add_argument("host")
        sub.add_argument("port", type=int)
    args = parser.parse_args(argv)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("chat-client needs a terminal.", file=sys.stderr)
        return 1
    if run is None:
        from .session import run_client

        return run_client(str(args.command), str(args.host), int(args.port))
    return run(str(args.command), str(args.host), int(args.port))
