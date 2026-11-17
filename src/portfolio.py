"""Каноническая модель портфеля: Position, PortfolioSnapshot, Allocation.

Сборка снапшота (включая тиньковскую НКД-математику) вынесена в адаптер
провайдера (``TinkoffClient.snapshot``) — см. ADR-0003. Здесь только модель,
которую провайдер наполняет, а доменный слой и фасады потребляют.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal

INSTRUMENT_TYPE_NAMES = {
    "share": "Акции",
    "bond": "Облигации",
    "etf": "ETF",
    "currency": "Валюта",
    "future": "Фьючерсы",
    "option": "Опционы",
}

EXCHANGE = "Московская биржа"


@dataclass
class Position:
    ticker: str
    name: str
    instrument_type: str
    type_name: str
    quantity: Decimal
    current_price: Decimal
    avg_price: Decimal
    amount: Decimal
    invested: Decimal
    pnl: Decimal
    pnl_pct: Decimal
    expected_yield: Decimal
    daily_yield: Decimal
    asset_uid: str = ""
    instrument_uid: str = ""
    figi: str = ""
    dividend_share: Decimal = Decimal("0")
    dividend_amount: Decimal = Decimal("0")
    bond_risk: str | None = None  # "low" / "moderate" / "high" — уровень риска облигации
    pnl_derived: Decimal = Decimal("0")  # наш расчёт amount − invested (для сверки)


@dataclass
class Allocation:
    asset_class: str
    amount: Decimal


@dataclass
class PortfolioSnapshot:
    account_id: str
    total: Decimal
    cash: Decimal
    total_pnl: Decimal = Decimal("0")
    total_yield_pct: Decimal = Decimal("0")
    positions: list[Position] = field(default_factory=list)
    allocation: list[Allocation] = field(default_factory=list)
    collected_at: str = ""

    @property
    def invested(self) -> Decimal:
        return sum(p.invested for p in self.positions if p.invested)


def save_snapshot(snapshot: PortfolioSnapshot, path=None):
    """Сохранить снимок в data/portfolio.json (с ротационным бэкапом)."""
    from pathlib import Path

    from .backup import backup_file

    dest = path
    if dest is None:
        dest = Path(__file__).resolve().parent.parent / "data" / "portfolio.json"
    backup_file(dest)
    dest.write_text(
        json.dumps(asdict(snapshot), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return dest


def merge_snapshots(snapshots: list[PortfolioSnapshot]) -> PortfolioSnapshot:
    """Объединяет снимки нескольких счетов в один сводный.

    Суммирует стоимость, деньги, P&L и позиции (по ``instrument_uid``/тикеру),
    а структуру активов — по классам. Per-share поля (дивиденды на акцию,
    текущая/средняя цена) не суммируются, а пересчитываются от суммарных величин.
    """
    if not snapshots:
        raise ValueError("нет снимков для объединения")
    if len(snapshots) == 1:
        return snapshots[0]

    zero = Decimal("0")
    total = sum((s.total for s in snapshots), zero)
    cash = sum((s.cash for s in snapshots), zero)
    total_pnl = sum((s.total_pnl for s in snapshots), zero)

    by_key: dict[str, Position] = {}
    for s in snapshots:
        for p in s.positions:
            key = p.instrument_uid or p.figi or p.ticker or "?"
            if key in by_key:
                prev = by_key[key]
                by_key[key] = replace(
                    prev,
                    quantity=prev.quantity + p.quantity,
                    amount=prev.amount + p.amount,
                    invested=prev.invested + p.invested,
                    pnl=prev.pnl + p.pnl,
                    pnl_derived=prev.pnl_derived + p.pnl_derived,
                    expected_yield=prev.expected_yield + p.expected_yield,
                    daily_yield=prev.daily_yield + p.daily_yield,
                )
            else:
                by_key[key] = replace(p)

    positions: list[Position] = []
    for p in by_key.values():
        quantity = p.quantity if p.quantity > 0 else Decimal("1")
        positions.append(
            replace(
                p,
                current_price=p.amount / quantity if p.amount else p.current_price,
                avg_price=p.invested / quantity if p.invested else p.avg_price,
                pnl_pct=(p.pnl / p.invested * 100) if p.invested else Decimal("0"),
            )
        )

    alloc: dict[str, Decimal] = {}
    for s in snapshots:
        for a in s.allocation:
            alloc[a.asset_class] = alloc.get(a.asset_class, zero) + a.amount
    allocation = [Allocation(k, v) for k, v in sorted(alloc.items(), key=lambda x: -x[1])]

    invested_total = sum((p.invested for p in positions), zero)
    total_yield_pct = (total_pnl / invested_total * 100) if invested_total else zero

    return PortfolioSnapshot(
        account_id="+".join(s.account_id for s in snapshots),
        total=total,
        cash=cash,
        total_pnl=total_pnl,
        total_yield_pct=total_yield_pct,
        positions=positions,
        allocation=allocation,
        collected_at=max(s.collected_at for s in snapshots),
    )
