"""Видимые предупреждения при частичной недоступности API."""

from decimal import Decimal

from src.analytics import analyze
from src.market import build_market_opinion
from src.portfolio import PortfolioSnapshot, Position


class BrokenClient:
    def get_asset_fundamentals(self, uids):
        raise RuntimeError("fundamentals unavailable")

    def get_forecast_by(self, uid):
        raise RuntimeError("forecast unavailable")

    def get_insider_deals(self, uid, limit):
        raise RuntimeError("insiders unavailable")

    def get_rsi(self, uid):
        raise RuntimeError("RSI unavailable")


def _snapshot():
    position = Position(
        ticker="SBER",
        name="Сбербанк",
        instrument_type="share",
        type_name="Акции",
        quantity=Decimal("1"),
        current_price=Decimal("300"),
        avg_price=Decimal("250"),
        amount=Decimal("300"),
        invested=Decimal("250"),
        pnl=Decimal("50"),
        pnl_pct=Decimal("20"),
        expected_yield=Decimal("50"),
        daily_yield=Decimal("0"),
        asset_uid="asset-1",
        instrument_uid="instrument-1",
    )
    return PortfolioSnapshot(
        account_id="test",
        total=Decimal("300"),
        cash=Decimal("0"),
        positions=[position],
    )


def test_fundamentals_warning_contains_ticker_and_cause():
    issues = []
    recommendations = analyze(BrokenClient(), _snapshot(), issues=issues)
    assert recommendations[0].action == "Нет данных"
    assert issues == ["SBER: фундаментальные данные не загружены (fundamentals unavailable)"]


def test_market_warnings_do_not_stop_partial_report():
    issues = []
    opinion = build_market_opinion(BrokenClient(), _snapshot(), issues=issues)
    assert opinion.tech[0].ticker == "SBER"
    assert len(issues) == 3
    assert any("forecast unavailable" in issue for issue in issues)
    assert any("insiders unavailable" in issue for issue in issues)
    assert any("RSI unavailable" in issue for issue in issues)
