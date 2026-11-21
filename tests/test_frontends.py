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


def test_telegram_summary_actionable_lines():
    """Сводка отвечает «действовать ли»: сигнал, ближайший взнос, отклонение."""
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from src.frontends.cli import _next_contribution_date, _telegram_summary
    from src.profile import ContributionRule

    snapshot = SimpleNamespace(
        total=Decimal("100000"), cash=Decimal("5000"),
        total_pnl=Decimal("5000"), invested=Decimal("90000"),
        allocation=[
            SimpleNamespace(asset_class="Акции", amount=Decimal("70000")),
            SimpleNamespace(asset_class="Облигации", amount=Decimal("25000")),
        ],
    )
    profile = SimpleNamespace(
        target={"Акции": Decimal("50"), "Облигации": Decimal("30"), "Золото": Decimal("20")},
        contributions=[ContributionRule(day=20, amount=Decimal("10000"))],
    )
    report = SimpleNamespace(
        snapshot=snapshot, profile=profile,
        crisis_signal={"state": "crisis", "dd_pct": Decimal("-35.0")},
    )
    text = _telegram_summary(report, Decimal("14.0"), Decimal("81.5"))
    assert "РЕЖИМ ОТКУПА" in text
    assert "Ближайший взнос" in text
    assert "Отклонение структуры" in text and "Акции" in text
    # stress — мягкая формулировка
    report.crisis_signal = {"state": "stress", "dd_pct": Decimal("-18.0")}
    assert "пока не активно" in _telegram_summary(report, None, None)
    # ближайшая дата взноса — сегодня или позже, в пределах ~40 дней
    nxt = _next_contribution_date(profile, date.today())
    assert nxt is not None and 0 <= (nxt - date.today()).days <= 40
