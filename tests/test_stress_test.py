"""Тесты стресс-теста: повтор кризисов на портфеле."""

from decimal import Decimal
from types import SimpleNamespace

from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.stress_test import replay_portfolio


class _FakeClient:
    """Свечи: SBER падает вдвое, IMOEX −30%, остальные — без данных."""

    def daily_interval(self):
        return "day"

    def find_instrument_uid(self, query, ticker):
        return f"uid:{ticker}"

    def get_candles(self, uid, start, end, interval):
        closes = {"uid:SBER": (100, 50), "uid:IMOEX": (100, 70), "uid:RGBI": (100, 95)}
        if uid == "uid:TGLD":
            return SimpleNamespace(candles=[])  # TGLD не существовал в 2008
        pair = closes.get(uid)
        if pair is None:
            return SimpleNamespace(candles=[])
        candles = [SimpleNamespace(close=SimpleNamespace(units=u, nano=0)) for u in pair]
        return SimpleNamespace(candles=candles)


def _snapshot() -> PortfolioSnapshot:
    pos = Position(
        ticker="SBER", name="Сбербанк", instrument_type="share", type_name="Акции",
        quantity=Decimal("100"), current_price=Decimal("100"), avg_price=Decimal("100"),
        amount=Decimal("60000"), invested=Decimal("60000"), pnl=Decimal("0"),
        pnl_pct=Decimal("0"), expected_yield=Decimal("0"), daily_yield=Decimal("0"),
        instrument_uid="uid:SBER",
    )
    bond = Position(
        ticker="SU26233", name="ОФЗ", instrument_type="bond", type_name="Облигации",
        quantity=Decimal("40"), current_price=Decimal("1000"), avg_price=Decimal("1000"),
        amount=Decimal("40000"), invested=Decimal("40000"), pnl=Decimal("0"),
        pnl_pct=Decimal("0"), expected_yield=Decimal("0"), daily_yield=Decimal("0"),
        instrument_uid="uid:SU26233",  # свечей нет → fallback на RGBI
    )
    return PortfolioSnapshot(
        account_id="t", total=Decimal("100000"), cash=Decimal("0"),
        total_pnl=Decimal("0"), positions=[pos, bond],
        allocation=[Allocation("Акции", Decimal("60000")),
                    Allocation("Облигации", Decimal("40000"))],
    )


def test_replay_uses_own_candles_and_fallback():
    results = replay_portfolio(_FakeClient(), _snapshot())
    assert len(results) == 4
    cr = results[0]
    # SBER −50% (вес 60%), ОФЗ через RGBI −5% (вес 40%): −50*0.6 − 5*0.4 = −32
    assert cr.portfolio_return_pct == Decimal("-32.0")
    assert cr.covered_pct == Decimal("100.00")
    # цель 50/30/20: TGLD в 2008 не существовал → fallback на цены золота ЦБ
    from src.stress_test import CRISES, _GOLD_CSV, _gold_cbr_return
    if _GOLD_CSV.exists():
        from datetime import datetime
        a, b = CRISES[cr.name]
        gold = _gold_cbr_return(
            datetime.fromisoformat(a).replace(tzinfo=__import__("datetime").timezone.utc),
            datetime.fromisoformat(b).replace(tzinfo=__import__("datetime").timezone.utc),
        )
        expected = (Decimal("50") * Decimal("-30") + Decimal("30") * Decimal("-5")
                    + Decimal("20") * gold) / 100
        assert cr.target_return_pct == expected.quantize(Decimal("0.1"))
    else:
        assert cr.target_return_pct is None
    tickers = {r.ticker for r in cr.replays}
    assert tickers == {"SBER", "SU26233"}


def test_replay_empty_snapshot():
    empty = PortfolioSnapshot(account_id="t", total=Decimal("0"), cash=Decimal("0"))
    assert replay_portfolio(_FakeClient(), empty) == []
