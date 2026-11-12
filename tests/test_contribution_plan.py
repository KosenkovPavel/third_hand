"""План пополнения: лоты, бюджет, комиссия и портфельные лимиты."""

from decimal import Decimal

from src.contribution_plan import PurchaseCandidate, build_contribution_plan
from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.profile import Rules


def _position(ticker: str, amount: str, instrument_type: str = "share") -> Position:
    value = Decimal(amount)
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type=instrument_type,
        type_name="Акции" if instrument_type == "share" else "Облигации",
        quantity=Decimal("1"),
        current_price=value,
        avg_price=value,
        amount=value,
        invested=value,
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        instrument_uid=ticker,
    )


def _candidate(ticker: str, price: str, lot: int, score: int = 80, sector: str = "it"):
    return PurchaseCandidate(
        ticker=ticker,
        name=ticker,
        asset_class="Акции",
        instrument_uid=ticker,
        price=Decimal(price),
        lot=lot,
        score=score,
        sector=sector,
    )


def _rules() -> Rules:
    rules = Rules()
    rules.max_position_pct = Decimal("100")
    rules.max_sector_pct = Decimal("100")
    return rules


def test_lots_and_commission_stay_within_budget():
    snapshot = PortfolioSnapshot(account_id="t", total=Decimal("0"), cash=Decimal("0"))
    plan = build_contribution_plan(
        snapshot,
        [_candidate("AAA", "100", 1)],
        {"Акции": Decimal("1000")},
        budget=Decimal("1000"),
        contribution=Decimal("1000"),
        rules=_rules(),
    )
    assert plan.items[0].lots == 9
    assert plan.spent == Decimal("900.00")
    assert plan.commission == Decimal("2.70")
    assert plan.unallocated == Decimal("97.30")
    assert plan.spent + plan.commission + plan.unallocated == Decimal("1000.00")


def test_over_limit_position_is_not_bought():
    positions = [
        _position("SBER", "41"),
        _position("SU1", "59", "bond"),
    ]
    snapshot = PortfolioSnapshot(
        account_id="t",
        total=Decimal("100"),
        cash=Decimal("0"),
        positions=positions,
        allocation=[Allocation("Акции", Decimal("41")), Allocation("Облигации", Decimal("59"))],
    )
    candidates = [
        _candidate("SBER", "1", 1, score=100, sector="financial"),
        _candidate("AAA", "10", 1, score=80),
    ]
    plan = build_contribution_plan(
        snapshot,
        candidates,
        {"Акции": Decimal("100")},
        budget=Decimal("100"),
        contribution=Decimal("100"),
        rules=Rules(),
    )
    assert [item.ticker for item in plan.items] == ["AAA"]
    assert plan.items[0].lots == 3
    assert plan.items[0].amount == Decimal("30.00")
    allocation = {item.asset_class: item.amount for item in plan.final_allocation}
    assert allocation["Акции"] == Decimal("71.00")
    assert allocation["Валюта"] == Decimal("69.91")


def test_insufficient_budget_for_one_lot_returns_cash():
    snapshot = PortfolioSnapshot(account_id="t", total=Decimal("0"), cash=Decimal("0"))
    plan = build_contribution_plan(
        snapshot,
        [_candidate("AAA", "101", 10)],
        {"Акции": Decimal("1000")},
        budget=Decimal("1000"),
        contribution=Decimal("1000"),
        rules=_rules(),
    )
    assert plan.items == []
    assert plan.unallocated == Decimal("1000")
    assert plan.notes
