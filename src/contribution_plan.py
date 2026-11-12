"""Виртуальный план пополнения по лотам без изменения реального портфеля."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR

from .portfolio import Allocation, PortfolioSnapshot


@dataclass(frozen=True)
class PurchaseCandidate:
    ticker: str
    name: str
    asset_class: str
    instrument_uid: str
    price: Decimal
    lot: int
    score: int = 0
    sector: str = ""


@dataclass(frozen=True)
class PurchasePlanItem:
    ticker: str
    name: str
    asset_class: str
    price: Decimal
    lot: int
    lots: int
    quantity: Decimal
    amount: Decimal
    commission: Decimal
    total_cost: Decimal


@dataclass
class ContributionPlan:
    budget: Decimal
    contribution: Decimal
    items: list[PurchasePlanItem] = field(default_factory=list)
    spent: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    unallocated: Decimal = Decimal("0")
    final_allocation: list[Allocation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_contribution_plan(
    snapshot: PortfolioSnapshot,
    candidates: list[PurchaseCandidate],
    class_budgets: dict[str, Decimal],
    budget: Decimal,
    contribution: Decimal,
    rules,
    commission_pct: Decimal = Decimal("0.3"),
    sector_by_uid: dict[str, str] | None = None,
) -> ContributionPlan:
    """Жадно распределяет бюджет по рейтингу с учётом лотов и лимитов портфеля."""
    budget_remaining = max(budget, Decimal("0"))
    class_remaining = {name: max(value, Decimal("0")) for name, value in class_budgets.items()}
    position_amounts = {p.ticker: p.amount for p in snapshot.positions}
    sector_by_uid = sector_by_uid or {}
    candidate_sectors = {candidate.instrument_uid: candidate.sector for candidate in candidates}
    sector_amounts: dict[str, Decimal] = {}
    for position in snapshot.positions:
        sector = sector_by_uid.get(position.instrument_uid) or candidate_sectors.get(
            position.instrument_uid, ""
        )
        if sector:
            sector_amounts[sector] = sector_amounts.get(sector, Decimal("0")) + position.amount

    final_total = snapshot.total + contribution
    plan = ContributionPlan(budget=budget, contribution=contribution)
    rate = commission_pct / Decimal("100")

    for candidate in sorted(candidates, key=lambda item: -item.score):
        if candidate.price <= 0 or candidate.lot <= 0 or candidate.asset_class not in class_remaining:
            continue
        if class_remaining[candidate.asset_class] <= 0 or budget_remaining <= 0:
            continue
        current_position = position_amounts.get(candidate.ticker, Decimal("0"))
        position_remaining = max(
            Decimal("0"),
            final_total * rules.max_position_pct / 100 - current_position,
        )
        allowed = min(
            budget_remaining,
            class_remaining[candidate.asset_class],
            position_remaining,
        )
        if candidate.sector:
            sector_remaining = max(
                Decimal("0"),
                final_total * rules.max_sector_pct / 100
                - sector_amounts.get(candidate.sector, Decimal("0")),
            )
            allowed = min(allowed, sector_remaining)
        lot_cost = candidate.price * candidate.lot * (Decimal("1") + rate)
        lots = int((allowed / lot_cost).to_integral_value(rounding=ROUND_FLOOR))
        if lots <= 0:
            continue
        amount = (candidate.price * candidate.lot * lots).quantize(Decimal("0.01"))
        commission = (amount * rate).quantize(Decimal("0.01"))
        total_cost = amount + commission
        item = PurchasePlanItem(
            ticker=candidate.ticker,
            name=candidate.name,
            asset_class=candidate.asset_class,
            price=candidate.price,
            lot=candidate.lot,
            lots=lots,
            quantity=Decimal(candidate.lot * lots),
            amount=amount,
            commission=commission,
            total_cost=total_cost,
        )
        plan.items.append(item)
        plan.spent += amount
        plan.commission += commission
        budget_remaining -= total_cost
        class_remaining[candidate.asset_class] -= total_cost
        position_amounts[candidate.ticker] = current_position + amount
        if candidate.sector:
            sector_amounts[candidate.sector] = (
                sector_amounts.get(candidate.sector, Decimal("0")) + amount
            )

    plan.unallocated = budget_remaining
    allocation = {item.asset_class: item.amount for item in snapshot.allocation}
    for item in plan.items:
        allocation[item.asset_class] = allocation.get(item.asset_class, Decimal("0")) + item.amount
    cash_after = snapshot.cash + contribution - plan.spent - plan.commission
    if cash_after > 0 or "Валюта" in allocation:
        allocation["Валюта"] = cash_after
    plan.final_allocation = [
        Allocation(asset_class, amount)
        for asset_class, amount in sorted(allocation.items(), key=lambda item: -item[1])
        if amount > 0
    ]
    if not plan.items and budget > 0:
        plan.notes.append("Нет кандидатов, которых можно купить с учётом бюджета, лотов и лимитов")
    elif plan.unallocated > 0:
        plan.notes.append("Остаток не распределён из-за размера лотов, целевой структуры или лимитов")
    return plan


def load_purchase_candidates(
    client,
    snapshot: PortfolioSnapshot,
    recommendations,
    sector_by_uid: dict[str, str] | None = None,
    issues: list[str] | None = None,
) -> list[PurchaseCandidate]:
    """Текущие позиции, которые прошли итоговые рекомендации и кредитный фильтр."""
    rec_by_ticker = {rec.ticker: rec for rec in recommendations}
    sector_by_uid = sector_by_uid or {}
    candidates = []
    for position in snapshot.positions:
        if position.instrument_type == "share":
            rec = rec_by_ticker.get(position.ticker)
            if rec is None or rec.action != "Покупать":
                continue
            score = rec.score
        elif position.instrument_type == "bond":
            if position.bond_risk == "high":
                continue
            score = 0
        else:
            continue
        try:
            instrument = client.get_instrument(position.instrument_uid).instrument
            lot = int(instrument.lot or 1)
        except Exception as exc:
            if issues is not None:
                issues.append(f"{position.ticker}: размер лота не загружен ({exc})")
            continue
        candidates.append(
            PurchaseCandidate(
                ticker=position.ticker,
                name=position.name,
                asset_class=position.type_name,
                instrument_uid=position.instrument_uid,
                price=position.current_price,
                lot=lot,
                score=score,
                sector=sector_by_uid.get(position.instrument_uid, ""),
            )
        )
    return candidates
