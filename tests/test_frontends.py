"""Тесты фасадов: детект интерактива и валидность маппинга секций TUI."""

import main
from src.frontends.cli import build_parser
from src.frontends.tui import SECTIONS


class TestIsInteractive:
    def test_no_args_is_interactive(self):
        assert main.is_interactive(["main.py"]) is True
        assert main.is_interactive([]) is True

    def test_flags_are_cli(self):
        assert main.is_interactive(["main.py", "--forecast"]) is False
        assert main.is_interactive(["main.py", "--version"]) is False
        assert main.is_interactive(["main.py", "--save"]) is False


class TestTuiSections:
    def test_every_section_has_valid_flags(self):
        """Каждый пункт меню даёт флаги, которые принимает argparse."""
        parser = build_parser()
        for word, flags in SECTIONS.items():
            args = parser.parse_args(flags)
            assert args is not None
            assert word in SECTIONS

    def test_rebalance_is_supported(self):
        assert SECTIONS["ребаланс"] == ["--rebalance"]
        assert SECTIONS["портфель"] == []
