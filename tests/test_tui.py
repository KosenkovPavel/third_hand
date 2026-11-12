"""Headless-тесты Textual-TUI: монтирование, рендер отчёта и режимы детализации."""

import asyncio
from decimal import Decimal
from io import StringIO

import pytest
from rich.console import Console

from src.accounting import AccountingReport
from src.frontends.cli import build_parser
from src.frontends.tui import BUTTONS, ThirdHandApp, _CaptureConsole
from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.profile import UserProfile
from src.report import print_all, print_summary
from src.report_data import ReportData

pytest.importorskip("textual")


class _FakeAccount:
    id = "test"
    name = "ИИС"


def _position(ticker: str, amount: Decimal, typ: str = "share") -> Position:
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type=typ,
        type_name="Акции" if typ == "share" else "Облигации",
        quantity=Decimal("1"),
        current_price=amount,
        avg_price=amount,
        amount=amount,
        invested=amount,
        pnl=Decimal("100"),
        pnl_pct=Decimal("1"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        instrument_uid=ticker,
    )


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


def _full_report() -> ReportData:
    snapshot = PortfolioSnapshot(
        account_id="test",
        total=Decimal("100000"),
        cash=Decimal("5000"),
        total_pnl=Decimal("5000"),
        positions=[_position("SBER", Decimal("40000")), _position("GAZP", Decimal("30000"))],
        allocation=[Allocation("Акции", Decimal("70000")), Allocation("Облигации", Decimal("25000"))],
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


def test_print_summary_is_concise():
    """Сводка (режим «просто») короче полного отчёта и без бухгалтерии."""
    console = Console(record=True, file=StringIO(), width=80)
    print_summary(_full_report(), console=console)
    text = console.export_text()
    assert "Стоимость" in text
    assert "Структура" in text
    assert "Крупнейшие позиции" in text
    assert "Бухгалтерия за период" not in text


def test_app_mounts_headlessly():
    async def check() -> None:
        async with _make_app().run_test() as pilot:
            await pilot.pause()
            assert pilot.app.query_one("#output") is not None
            assert pilot.app.query_one("#input") is not None
            assert pilot.app.query_one("#status") is not None
            # приветственный экран: логотип + бренд, без эмодзи в шапке
            assert pilot.app.title == "Третья рука"
            text = " ".join(str(strip) for strip in pilot.app.query_one("#output").lines)
            assert "Третья рука" in text
            assert "честная бухгалтерия портфеля" in text
            assert "\U0001F9BE" not in text  # 🦾 больше не используется

    asyncio.run(check())


def test_buttons_present():
    async def check() -> None:
        async with _make_app().run_test() as pilot:
            await pilot.pause()
            ids = {b.id for b in pilot.app.query("Button")}
            assert "btn-mode" in ids
            for btn_id, _label, _choice in BUTTONS:
                assert btn_id in ids

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


def test_mode_toggle_switches_summary_and_detail():
    async def check() -> None:
        async with _make_app().run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app = pilot.app
            # по умолчанию — «просто»: сводка, без бухгалтерии
            app.last_choice = "портфель"
            app._show_report(_full_report())
            await pilot.pause()
            simple = " ".join(str(s) for s in app.query_one("#output").lines)
            assert "Стоимость" in simple
            assert "Бухгалтерия за период" not in simple
            # переключаем в «детально»: полный отчёт
            app._toggle_mode()
            await pilot.pause()
            detail = " ".join(str(s) for s in app.query_one("#output").lines)
            assert "Бухгалтерия за период" in detail
            assert app.sub_title.endswith("детально")

    asyncio.run(check())


def test_run_section_collects_in_worker_and_renders(monkeypatch):
    """Регрессия: run_worker вызывает _build без аргументов — обёртка partial.

    Раньше ``run_worker(self._build, choice)`` передавал ``choice`` в параметр
    ``name``, а не в ``_build``, и воркер падал с ``missing 1 required argument``.
    """

    monkeypatch.setattr(
        "src.frontends.tui.build_report",
        lambda client, account, args, profile, console: _report(),
    )

    async def check() -> None:
        async with _make_app().run_test() as pilot:
            await pilot.pause()
            pilot.app._run("портфель")
            # ждём завершения воркера: _show_report очищает статус
            for _ in range(200):
                await asyncio.sleep(0.02)
                if pilot.app.query_one("#status")._Static__content == "":
                    break
            out = pilot.app.query_one("#output")
            text = " ".join(str(s) for s in out.lines)
            assert "Стоимость" in text

    asyncio.run(check())
