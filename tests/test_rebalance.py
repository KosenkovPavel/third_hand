"""Тесты ребалансировки: без перелёта целевой доли, остаток сверх структуры (без сети)."""

from decimal import Decimal

from src.portfolio import Allocation, PortfolioSnapshot
from src.rebalance import calculate_rebalance


def _snapshot(stocks: str, bonds: str) -> PortfolioSnapshot:
    total = Decimal(stocks) + Decimal(bonds)
    return PortfolioSnapshot(
        account_id="t",
        total=total,
        cash=Decimal("0"),
        allocation=[Allocation("Акции", Decimal(stocks)), Allocation("Облигации", Decimal(bonds))],
    )


TARGET = {"Акции": Decimal("50"), "Облигации": Decimal("50")}


def _by_class(plan) -> dict:
    return {r.asset_class: r for r in plan.recommendations}


class TestNoOvershoot:
    def test_big_budget_does_not_exceed_target(self):
        # 50k кэша уже внутри портфеля + 10k пополнение: недостача до цели
        # всего 10k — распределять весь бюджет 60k пропорционально нельзя
        snap = _snapshot("48000", "52000")
        snap.cash = Decimal("50000")
        plan = calculate_rebalance(
            snap, TARGET,
            invest_amount=Decimal("60000"), cash=Decimal("50000"), contrib=Decimal("10000"),
        )
        recs = _by_class(plan)
        # акции: цель 50% от 110k = 55k, не хватает 7k; облигации: не хватает 3k
        assert recs["Акции"].invest_amount == Decimal("7000.00")
        assert recs["Облигации"].invest_amount == Decimal("3000.00")
        assert plan.unallocated == Decimal("50000.00")

    def test_normal_5k_unchanged(self):
        plan = calculate_rebalance(
            _snapshot("60000", "40000"), {"Акции": Decimal("70"), "Облигации": Decimal("30")},
            invest_amount=Decimal("5000"), cash=Decimal("0"), contrib=Decimal("5000"),
        )
        recs = _by_class(plan)
        # недостача акций 13 500 ₽ >> бюджет → весь бюджет в акции, как и раньше
        assert recs["Акции"].invest_amount == Decimal("5000.00")
        assert recs["Облигации"].invest_amount == Decimal("0")
        assert plan.unallocated == Decimal("0")
