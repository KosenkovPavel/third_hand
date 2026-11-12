"""Сквозной тест: позиции и денежные потоки → ограничения → текст отчёта."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from rich.console import Console

from src.accounting import AccountingReport, ReturnBreakdown
from src.analytics import Recommendation, apply_portfolio_rules
from src.history import HistoryPoint, time_weighted_return
from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.profile import Rules, UserProfile
from src.report import print_all
from src.report_data import ReportData


def _position(ticker: str, amount: Decimal, instrument_type: str) -> Position:
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type=instrument_type,
        type_name="Акции" if instrument_type == "share" else "Облигации",
        quantity=Decimal("1"),
        current_price=amount,
        avg_price=amount,
        amount=amount,
        invested=amount,
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        instrument_uid=ticker,
    )


def _history_point(day: str, total: str) -> HistoryPoint:
    return HistoryPoint(
        date=day,
        total=Decimal(total),
        cash=Decimal("0"),
        invested=Decimal(total),
        pnl_pct=Decimal("0"),
        net_contrib=Decimal(total),
    )


def test_report_uses_constrained_recommendation_and_flow_adjusted_return():
    snapshot = PortfolioSnapshot(
        account_id="test",
        total=Decimal("100"),
        cash=Decimal("0"),
        positions=[
            _position("SBER", Decimal("41"), "share"),
            _position("SU26238", Decimal("59"), "bond"),
        ],
        allocation=[Allocation("Акции", Decimal("41")), Allocation("Облигации", Decimal("59"))],
    )
    recommendation = Recommendation(
        ticker="SBER", name="SBER", instrument_type="share", action="Покупать", score=85
    )
    recommendations = apply_portfolio_rules([recommendation], snapshot, Rules())
    points = [_history_point("2026-01-01", "100"), _history_point("2026-01-11", "120")]
    twr = time_weighted_return(points, [(date(2026, 1, 6), Decimal("20"))])
    console = Console(record=True, width=200)

    report = ReportData(
        snapshot=snapshot,
        acct=AccountingReport(account_id="test"),
        profile=UserProfile(),
        recommendations=recommendations,
        history_points=points,
        twr_pct=twr,
        benchmark_pct=Decimal("1.25"),
        xirr_pct=Decimal("8.00"),
        account_opened=datetime(2023, 1, 1),
    )
    print_all(report, console=console)

    output = console.export_text()
    assert "Держать" in output
    assert "не докупать" in output
    assert "Ваша доходность (TWR) за 2026-01-01 — 2026-01-11: +0.00%" in output
    assert "Индекс МосБиржи (IMOEX, без дивидендов) за тот же период: +1.25%" in output
    assert "XIRR за всю историю счёта: +8.00% годовых" in output


def test_xirr_hidden_until_account_is_3_years_old():
    def render(opened: datetime | None) -> str:
        snapshot = PortfolioSnapshot(account_id="test", total=Decimal("100"), cash=Decimal("0"))
        breakdown = ReturnBreakdown(
            net_contrib=Decimal("100"),
            total_return=Decimal("10"),
            realized_pnl=Decimal("5"),
            unrealized_pnl=Decimal("5"),
            income=Decimal("0"),
            costs=Decimal("0"),
        )
        console = Console(record=True, width=200)
        report = ReportData(
            snapshot=snapshot,
            acct=AccountingReport(account_id="test"),
            profile=UserProfile(),
            return_breakdown=breakdown,
            xirr_pct=Decimal("32.00"),
            account_opened=opened,
        )
        print_all(report, console=console)
        return console.export_text()

    now = datetime.now(timezone.utc)
    young = render(now - timedelta(days=365))          # счёту ~1 год
    old = render(now - timedelta(days=3 * 365 + 10))   # счёту > 3 лет
    no_opened = render(None)

    # до 3 лет XIRR не показываем
    assert "XIRR" not in young
    assert "XIRR" not in no_opened
    # после 3 лет — показываем
    assert "XIRR" in old
    # остальное (общий доход и разбивка) видно всегда
    assert "Общий доход" in young
    assert "Реализованный P&L" in young
    assert "Нереализованный P&L" in young
