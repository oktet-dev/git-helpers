"""Entry point for the gg CLI tool."""

from __future__ import annotations

import argparse
import sys

from gg import rbt, sync


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(prog="gg", description="git-helpers CLI")
    sub = parser.add_subparsers(dest="command")
    rbt.add_parser(sub)
    sync.add_parser(sub)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)
