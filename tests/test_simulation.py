"""Симуляция взносов в индекс на искусственных ценах."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.accounting import Operation
from src.simulation import simulate_index_contributions


def _flow(day: date, amount: str, kind: str = "in") -> Operation:
    return Operation(
        date=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        kind=kind,
        op_type=kind,
        payment=Decimal(amount),
        quantity=0,
        figi=None,
        ticker=None,
    )


class FakeIndexClient:
    def __init__(self, prices):
        self.prices = prices

    def find_instrument_uid(self, query, ticker):
        return "imoex"

    def daily_interval(self):
        return None  # фейк — интервал в get_candles не используется

    def get_candles(self, uid, from_, to, interval):
        target = from_.date() if (to - from_).days == 10 else to.date() - timedelta(days=1)
        price = self.prices.get(target)
        candles = [] if price is None else [SimpleNamespace(close=SimpleNamespace(units=price, nano=0))]
        return SimpleNamespace(candles=candles)


def test_same_contributions_in_index():
    prices = {date(2026, 1, 1): 100, date(2026, 1, 11): 200, date(2026, 1, 21): 250}
    flows = [_flow(date(2026, 1, 1), "100"), _flow(date(2026, 1, 11), "100")]
    result = simulate_index_contributions(
        FakeIndexClient(prices), flows, Decimal("400"), end_date=date(2026, 1, 21)
    )
    assert result is not None
    assert result.units == Decimal("1.5")
    assert result.final_value == Decimal("375.00")
    assert result.difference == Decimal("25.00")
    assert result.profit_pct == Decimal("87.50")


def test_withdrawal_sells_index_units():
    prices = {date(2026, 1, 1): 100, date(2026, 1, 11): 100, date(2026, 1, 21): 100}
    flows = [
        _flow(date(2026, 1, 1), "100"),
        _flow(date(2026, 1, 11), "-50", kind="out"),
    ]
    result = simulate_index_contributions(
        FakeIndexClient(prices), flows, Decimal("60"), end_date=date(2026, 1, 21)
    )
    assert result is not None
    assert result.final_value == Decimal("50.00")
    assert result.net_contrib == Decimal("50")


def test_missing_price_invalidates_simulation():
    issues = []
    result = simulate_index_contributions(
        FakeIndexClient({}),
        [_flow(date(2026, 1, 1), "100")],
        Decimal("100"),
        end_date=date(2026, 1, 21),
        issues=issues,
    )
    assert result is None
    assert issues


def test_no_flows():
    assert simulate_index_contributions(None, [], Decimal("100")) is None
