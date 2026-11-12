"""Headless-тесты Textual-TUI: монтирование приложения и рендер отчёта в RichLog."""

import asyncio
from decimal import Decimal

import pytest

from src.accounting import AccountingReport
from src.frontends.cli import build_parser
from src.frontends.tui import ThirdHandApp, _CaptureConsole
from src.portfolio import PortfolioSnapshot
from src.profile import UserProfile
from src.report import print_all
from src.report_data import ReportData

pytest.importorskip("textual")


class _FakeAccount:
    id = "test"
    name = "ИИС"


def _report() -> ReportData:
    snapshot = PortfolioSnapshot(
        account_id="test",
        total=Decimal("100000"),
        cash=Decimal("5000"),
        total_pnl=Decimal("5000"),
    )
    return ReportData(
        snapshot=snapshot,
        acct=AccountingReport(account_id="test"),
        profile=UserProfile(),
    )


def _make_app():
    return ThirdHandApp(object(), _FakeAccount(), UserProfile(), build_parser())


def test_capture_console_collects_renderables_without_printing():
    """print_all пишет в наш Console-перехватчик, а не в stdout."""
    console = _CaptureConsole(width=80)
    print_all(_report(), console=console)
    assert console.renderables, "ничего не собрано"
    # только Panel-обёртки, без плоских сегментов
    assert all(type(r).__name__ == "Panel" for r in console.renderables)


def test_app_mounts_headlessly():
    async def check() -> None:
        async with _make_app().run_test() as pilot:
            await pilot.pause()
            assert pilot.app.query_one("#output") is not None
            assert pilot.app.query_one("#input") is not None
            assert pilot.app.query_one("#status") is not None

    asyncio.run(check())


def test_show_report_renders_into_richlog():
    async def check() -> None:
        async with _make_app().run_test() as pilot:
            await pilot.pause()
            pilot.app._show_report(_report())
            await pilot.pause()
            out = pilot.app.query_one("#output")
            assert len(out.lines) > 0
            text = " ".join(str(strip) for strip in out.lines)
            assert "Портфель" in text
            assert "100 000" in text  # _money форматирует 100000 → «100 000.00 ₽»

    asyncio.run(check())
