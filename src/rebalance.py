"""Ребалансировка: распределение нового пополнения для сближения с целевым балансом.

Пользователь задаёт целевую структуру портфеля в процентах по классам активов
(напр. ``{"Акции": 60, "Облигации": 40}``), а модуль вычисляет, сколько
направить в каждый класс при следующем пополнении, чтобы приблизиться к цели.

Продажи не предлагаются — только распределение нового капитала.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class RebalanceRecommendation:
    asset_class: str
    current_pct: Decimal
    target_pct: Decimal
    current_value: Decimal
    target_value: Decimal
    diff: Decimal
    invest_amount: Decimal


@dataclass
class RebalancePlan:
    total_value: Decimal
    invest_amount: Decimal
    target: dict[str, Decimal]
    recommendations: list[RebalanceRecommendation]
    overallocated: list[str]
    underallocated: list[str]
    cash: Decimal = Decimal("0")
    contrib: Decimal = Decimal("0")
    unallocated: Decimal = Decimal("0")  # бюджет сверх целевой структуры


def calculate_rebalance(
    snapshot: PortfolioSnapshot,
    target: dict[str, Decimal],
    invest_amount: Decimal = Decimal("0"),
    cash: Decimal = Decimal("0"),
    contrib: Decimal = Decimal("0"),
) -> RebalancePlan:
    """Рассчитать распределение нового пополнения для ребалансировки.

    ``target`` — словарь {название класса активов: целевой процент от итого}.
    Проценты должны в сумме давать 100.

    ``invest_amount`` — полный бюджет на покупку (деньги на счёте + пополнение).
    ``contrib`` — новое пополнение: только оно увеличивает итоговую стоимость,
    т.к. кэш уже входит в ``snapshot.total``.
    ``cash`` и ``contrib`` сохраняются в план только для отображения разбивки.
    """
    total = snapshot.total
    post_total = total + contrib

    current_values: dict[str, Decimal] = {}
    for a in snapshot.allocation:
        current_values[a.asset_class] = a.amount

    recommendations: list[RebalanceRecommendation] = []
    overallocated: list[str] = []
    underallocated: list[str] = []

    for cls, pct in target.items():
        current = current_values.get(cls, Decimal("0"))
        target_val = (post_total * pct / Decimal("100")).quantize(Decimal("0.01"))
        current_pct = (current / total * 100).quantize(Decimal("0.1")) if total else Decimal("0")
        diff = (target_val - current).quantize(Decimal("0.01"))

        if diff < 0:
            overallocated.append(cls)
        elif diff > 0:
            underallocated.append(cls)

        # распределяем invest_amount пропорционально недостающим классам,
        # но не больше diff — перелёт целевой доли не предлагаем
        invest_for_class = Decimal("0")
        if invest_amount > 0 and diff > 0:
            total_under = sum(
                max(
                    Decimal("0"),
                    (post_total * t / Decimal("100")).quantize(Decimal("0.01"))
                    - current_values.get(c, Decimal("0")),
                )
                for c, t in target.items()
            )
            if total_under > 0:
                proportional = (diff / total_under * invest_amount).quantize(Decimal("0.01"))
                invest_for_class = min(diff, proportional)

        recommendations.append(
            RebalanceRecommendation(
                asset_class=cls,
                current_pct=current_pct,
                target_pct=pct,
                current_value=current,
                target_value=target_val,
                diff=diff,
                invest_amount=invest_for_class,
            )
        )

    return RebalancePlan(
        total_value=total,
        invest_amount=invest_amount,
        target=target,
        recommendations=recommendations,
        overallocated=overallocated,
        underallocated=underallocated,
        cash=cash,
        contrib=contrib,
        unallocated=invest_amount
        - sum((r.invest_amount for r in recommendations), Decimal("0")),
    )
