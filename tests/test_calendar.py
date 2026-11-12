"""Тесты календаря выплат: дивиденды — нетто без повторного НДФЛ, купоны — с НДФЛ."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.calendar import _bond_coupons, _share_dividends
from src.portfolio import Position


def _mv(units: int, nano: int = 0) -> SimpleNamespace:
    return SimpleNamespace(units=units, nano=nano)


NOW = datetime.now(timezone.utc)
PAY = NOW + timedelta(days=30)


def _position(instrument_type: str, figi: str = "FIGI1") -> Position:
    return Position(
        ticker="PLZL",
        name="Полюс",
        instrument_type=instrument_type,
        type_name="Акции" if instrument_type == "share" else "Облигации",
        quantity=Decimal("10"),
        current_price=Decimal("100"),
        avg_price=Decimal("100"),
        amount=Decimal("1000"),
        invested=Decimal("1000"),
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        figi=figi,
    )


class FakeClient:
    def get_dividends(self, figi, from_, to):
        return SimpleNamespace(
            dividends=[
                SimpleNamespace(dividend_net=_mv(10), payment_date=PAY, record_date=PAY)
            ]
        )

    def get_bond_coupons(self, instrument_id, from_, to):
        return SimpleNamespace(
            events=[SimpleNamespace(coupon_date=PAY, pay_one_bond=_mv(50))]
        )


def test_dividend_is_net_no_double_tax():
    events = _share_dividends(
        FakeClient(), _position("share"), NOW, PAY + timedelta(days=1), Decimal("0.13")
    )
    assert len(events) == 1
    e = events[0]
    # dividend_net = 10 ₽ нетто на акцию → 10 × 10 = 100, повторно налог не вычитаем
    assert e.amount == Decimal("100.00")
    assert e.amount_net is None


def test_coupon_taxed_once():
    events = _bond_coupons(
        FakeClient(), _position("bond"), NOW, PAY + timedelta(days=1), Decimal("0.13")
    )
    assert len(events) == 1
    e = events[0]
    # купон 50 ₽ брутто на облигацию → 50 × 10 = 500, нетто = 500 × (1 − 0.13)
    assert e.amount == Decimal("500.00")
    assert e.amount_net == Decimal("435.00")
