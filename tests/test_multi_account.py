"""Тесты мультисчёта: merge_snapshots и merge_accounting."""

from decimal import Decimal

from src.accounting import AccountingReport, merge_accounting
from src.portfolio import Allocation, PortfolioSnapshot, Position, merge_snapshots


def _pos(ticker: str, amount: str, invested: str, uid: str) -> Position:
    a = Decimal(amount)
    i = Decimal(invested)
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type="share",
        type_name="Акции",
        quantity=Decimal("1"),
        current_price=a,
        avg_price=i,
        amount=a,
        invested=i,
        pnl=a - i,
        pnl_pct=Decimal("0"),
        expected_yield=a - i,
        daily_yield=Decimal("0"),
        instrument_uid=uid,
    )


def test_merge_snapshots_sums_and_merges_positions():
    a = PortfolioSnapshot(
        account_id="a",
        total=Decimal("100"),
        cash=Decimal("10"),
        total_pnl=Decimal("10"),
        positions=[_pos("SBER", "40", "30", "uid1")],
        allocation=[Allocation("Акции", Decimal("90"))],
    )
    b = PortfolioSnapshot(
        account_id="b",
        total=Decimal("50"),
        cash=Decimal("5"),
        total_pnl=Decimal("20"),
        positions=[
            _pos("SBER", "30", "20", "uid1"),
            _pos("LQDT", "15", "10", "uid2"),
        ],
        allocation=[Allocation("Акции", Decimal("40")), Allocation("ETF", Decimal("5"))],
    )

    merged = merge_snapshots([a, b])

    assert merged.total == Decimal("150")
    assert merged.cash == Decimal("15")
    assert merged.total_pnl == Decimal("30")
    # SBER объединился (один uid), LQDT — отдельно
    assert len(merged.positions) == 2
    sber = next(p for p in merged.positions if p.ticker == "SBER")
    assert sber.amount == Decimal("70")
    assert sber.invested == Decimal("50")
    assert sber.pnl == Decimal("20")  # 10 + 10
    alloc = {x.asset_class: x.amount for x in merged.allocation}
    assert alloc["Акции"] == Decimal("130")
    assert alloc["ETF"] == Decimal("5")


def test_merge_snapshots_single_is_identity():
    s = PortfolioSnapshot(account_id="a", total=Decimal("1"), cash=Decimal("0"))
    assert merge_snapshots([s]) is s


def test_merge_accounting_sums():
    a = AccountingReport(
        account_id="a", total_in=Decimal("100"), total_fees=Decimal("5"), operations_count=2
    )
    a.by_instrument["SBER"] = {"buy": Decimal("50"), "sell": Decimal("0"), "div": Decimal("1")}
    b = AccountingReport(
        account_id="b", total_in=Decimal("200"), total_fees=Decimal("3"), operations_count=3
    )
    b.by_instrument["SBER"] = {"buy": Decimal("20"), "sell": Decimal("0"), "div": Decimal("2")}

    m = merge_accounting([a, b])

    assert m.total_in == Decimal("300")
    assert m.total_fees == Decimal("8")
    assert m.operations_count == 5
    assert m.by_instrument["SBER"]["buy"] == Decimal("70")
    assert m.by_instrument["SBER"]["div"] == Decimal("3")
