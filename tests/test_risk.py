"""Тесты классификации облигаций по кредитному риску (без сети)."""

from decimal import Decimal

from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.risk import BOND_CORP, BOND_HY, BOND_OFZ, bond_risk_breakdown, classify_bond


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
