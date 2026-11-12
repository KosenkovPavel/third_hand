"""Журнал рекомендаций: дедупликация и оценка горизонтов без сети."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from src.analytics import Recommendation
from src.portfolio import PortfolioSnapshot, Position
from src.recommendation_history import (
    JournalEntry,
    evaluate_recommendations,
    load_recommendations,
    save_recommendations,
)


def _snapshot(price: str = "100") -> PortfolioSnapshot:
    position = Position(
        ticker="SBER",
        name="Сбербанк",
        instrument_type="share",
        type_name="Акции",
        quantity=Decimal("1"),
        current_price=Decimal(price),
        avg_price=Decimal(price),
        amount=Decimal(price),
        invested=Decimal(price),
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        instrument_uid="sber-uid",
    )
    return PortfolioSnapshot(
        account_id="test",
        total=Decimal(price),
        cash=Decimal("0"),
        positions=[position],
        collected_at="2026-01-01T12:00:00",
    )


def _recommendation(score: int) -> Recommendation:
    return Recommendation(
        ticker="SBER", name="Сбербанк", instrument_type="share", action="Держать", score=score
    )


def test_same_day_recommendation_is_replaced(tmp_path):
    path = tmp_path / "recommendations.json"
    save_recommendations([_recommendation(70)], _snapshot(), path)
    save_recommendations([_recommendation(60)], _snapshot("101"), path)
    entries = load_recommendations(path)
    assert len(entries) == 1
    assert entries[0].score == 60
    assert entries[0].price == Decimal("101")


class FakeMarketClient:
    def find_instrument_uid(self, query, ticker):
        return "imoex-uid"

    def daily_interval(self):
        return None  # фейк — интервал в get_candles не используется

    def get_candles(self, uid, from_, to, interval):
        if uid == "sber-uid":
            value = 120
        else:
            value = 100 if from_.date() == date(2025, 1, 1) else 110
        candle = SimpleNamespace(close=SimpleNamespace(units=value, nano=0))
        return SimpleNamespace(candles=[candle])


def test_matured_recommendation_compared_with_imoex():
    entry = JournalEntry(
        date="2025-01-01",
        ticker="SBER",
        instrument_uid="sber-uid",
        price=Decimal("100"),
        score=80,
        action="Покупать",
    )
    outcomes = evaluate_recommendations(
        FakeMarketClient(), [entry], today=date(2026, 1, 1)
    )
    assert [outcome.horizon_days for outcome in outcomes] == [365, 180, 90]
    assert all(outcome.return_pct == Decimal("20.00") for outcome in outcomes)
    assert all(outcome.benchmark_pct == Decimal("10.00") for outcome in outcomes)
    assert all(outcome.excess_pct == Decimal("10.00") for outcome in outcomes)


def test_immature_recommendation_makes_no_api_calls():
    entry = JournalEntry(
        date="2025-01-01",
        ticker="SBER",
        instrument_uid="sber-uid",
        price=Decimal("100"),
        score=80,
        action="Покупать",
    )
    assert evaluate_recommendations(None, [entry], today=date(2025, 1, 2)) == []
