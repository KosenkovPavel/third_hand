"""Точка входа: выбор фасада (CLI/TUI) и запуск."""

from __future__ import annotations

import sys

from rich.console import Console

from src.frontends.cli import build_parser, run_cli
from src.frontends.tui import run_tui


def is_interactive(argv: list[str]) -> bool:
    """TUI запускается, когда нет ни одного аргумента (только имя скрипта)."""
    return len(argv) <= 1


def main() -> int:
    parser = build_parser()
    console = Console()
    if is_interactive(sys.argv):
        return run_tui(parser, console)
    return run_cli(parser, console)


if __name__ == "__main__":
    sys.exit(main())
