"""Тесты рыночных показателей без сети."""

from decimal import Decimal
from types import SimpleNamespace

from src.market import consensus_change_pct


def _q(units: int, nano: int = 0):
    return SimpleNamespace(units=units, nano=nano)


def test_consensus_change_calculated_from_prices():
    consensus = SimpleNamespace(
        current_price=_q(300),
        consensus=_q(360),
        price_change_rel=_q(999),
    )
    assert consensus_change_pct(consensus) == Decimal("20.00")


def test_api_price_change_is_already_percent():
    consensus = SimpleNamespace(
        current_price=_q(0),
        consensus=_q(0),
        price_change_rel=_q(28, 70000000),
    )
    assert consensus_change_pct(consensus) == Decimal("28.07")


def test_consensus_change_without_data():
    consensus = SimpleNamespace(
        current_price=_q(0),
        consensus=_q(0),
        price_change_rel=_q(0),
    )
    assert consensus_change_pct(consensus) is None
