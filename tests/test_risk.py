"""Тесты классификации облигаций по кредитному риску (без сети)."""

from decimal import Decimal

from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.risk import (
    BOND_CORP,
    BOND_HY,
    BOND_OFZ,
    bond_risk_breakdown,
    classify_bond,
    crisis_buy_signal,
)


class TestClassifyBond:
    def test_ofz_by_ticker(self):
        assert classify_bond("SU26238RMFS4", "ОФЗ 26238", None) == BOND_OFZ

    def test_ofz_by_name(self):
        assert classify_bond("RU000A10", "ОФЗ-ПД 29006", "low") == BOND_OFZ

    def test_high_yield(self):
        assert classify_bond("RU000A106", "Селигдар БО-002Р", "high") == BOND_HY

    def test_corporate(self):
        assert classify_bond("RU000A105", "ГТЛК БО", "moderate") == BOND_CORP
        assert classify_bond("RU000A105", "ГТЛК БО", "low") == BOND_CORP
        assert classify_bond("RU000A105", "ГТЛК БО", None) == BOND_CORP


def _bond(ticker: str, amount: str, risk: str | None) -> Position:
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type="bond",
        type_name="Облигации",
        quantity=Decimal("1"),
        current_price=Decimal(amount),
        avg_price=Decimal(amount),
        amount=Decimal(amount),
        invested=Decimal(amount),
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        bond_risk=risk,
    )


class TestBondRiskBreakdown:
    def test_groups(self):
        snap = PortfolioSnapshot(
            account_id="t",
            total=Decimal("1000"),
            cash=Decimal("0"),
            positions=[
                _bond("SU26238", "500", None),
                _bond("SELG", "300", "high"),
                _bond("GTLK", "200", "moderate"),
            ],
            allocation=[Allocation("Облигации", Decimal("1000"))],
        )
        bd = bond_risk_breakdown(snap)
        assert bd[BOND_OFZ] == Decimal("500")
        assert bd[BOND_HY] == Decimal("300")
        assert bd[BOND_CORP] == Decimal("200")

    def test_no_bonds(self):
        snap = PortfolioSnapshot(account_id="t", total=Decimal("0"), cash=Decimal("0"))
        assert bond_risk_breakdown(snap) == {}


def _closes(points: list[float]) -> list[Decimal]:
    return [Decimal(str(p)) for p in points]


class TestCrisisBuySignal:
    def test_flat_market_is_normal(self):
        sig = crisis_buy_signal(_closes([100] * 100))
        assert sig["state"] == "normal"
        assert sig["dd_pct"] == Decimal("0.0")

    def test_deep_drawdown_is_crisis(self):
        sig = crisis_buy_signal(_closes([100] * 50 + [65] * 20))
        assert sig["state"] == "crisis"
        assert sig["dd_pct"] == Decimal("-35.0")

    def test_mild_drawdown_is_stress(self):
        sig = crisis_buy_signal(_closes([100] * 50 + [80] * 20))
        assert sig["state"] == "stress"
        assert sig["dd_pct"] == Decimal("-20.0")

    def test_recovery_after_recent_bottom(self):
        # дно −40% 30 дней назад, восстановление до −12% от пика
        closes = [100] * 40 + [60] * 30 + [88] * 10
        sig = crisis_buy_signal(_closes(closes))
        assert sig["state"] == "recovery"

    def test_no_recovery_if_bottom_long_ago(self):
        # дно −45% было давно, последние ~3 месяца индекс около −10% от пика —
        # в окне CRISIS_RECOVERY_WINDOW_DAYS дна нет, режим считается отработанным
        closes = [100] * 40 + [55] * 20 + [90] * 80
        sig = crisis_buy_signal(_closes(closes))
        assert sig["state"] == "normal"

    def test_too_short_history_returns_none(self):
        assert crisis_buy_signal(_closes([100, 95])) is None
        assert crisis_buy_signal([]) is None
