"""Каноническая модель портфеля: Position, PortfolioSnapshot, Allocation.

Сборка снапшота (включая тиньковскую НКД-математику) вынесена в адаптер
провайдера (``TinkoffClient.snapshot``) — см. ADR-0003. Здесь только модель,
которую провайдер наполняет, а доменный слой и фасады потребляют.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
