"""Персональный Монте-Карло: вероятность достичь целей из профиля.

Block bootstrap (12 мес) по месячным доходностям классов активов
(акции MCFTR / облигации RGBITR / золото ЦБ / денежный рынок — прокси
ключевой ставки) из research/data (закоммичены). Веса — текущая структура
портфеля, взносы — из профиля. Итог — в реальных (сегодняшних) рублях:
дефляция по CPI г/г (приближение (1+yoy)**(1/12)).

Честная рамка: это распределение сценариев, а не обещание доходности.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .portfolio import PortfolioSnapshot
from .profile import UserProfile

_RETURNS_CSV = Path(__file__).resolve().parent.parent / "research" / "data" / "monthly_returns.csv"
_CPI_CSV = Path(__file__).resolve().parent.parent / "research" / "data" / "inflation_yoy.csv"

BLOCK = 12
PATHS = 500
SEED = 42

# класс портфеля → колонка доходности в research-данных
CLASS_TO_COLUMN = {
    "Акции": "MCFTR",
    "Облигации": "RGBITR",
    "Золото": "GOLD_RUB_G",
    "ETF": "CASH_PROXY",
    "Валюта": "CASH_PROXY",
}


@dataclass(frozen=True)
class GoalMonteCarlo:
    name: str
    target: Decimal
    years: int
    p5: Decimal
    p50: Decimal
    p95: Decimal
    p_success: Decimal  # вероятность достичь цели, %


def _load_class_returns():
    """Месячные ряды (eq, ofz, gold, cash, cpi_mom) — из research-данных.
    Строки без полного набора (напр. без CASH_PROXY до 2013) пропускаются."""
    if not _RETURNS_CSV.exists() or not _CPI_CSV.exists():
        return None
    cpi = {}
    with _CPI_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                yoy = float(row["CPI_YOY_PCT"])
                cpi[row["Дата"][:7]] = (1 + yoy / 100) ** (1 / 12) - 1
            except (TypeError, ValueError, KeyError):
                continue
    out = []
    with _RETURNS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                rets = tuple(float(row[c]) for c in
                             ("MCFTR", "RGBITR", "GOLD_RUB_G", "CASH_PROXY"))
            except (TypeError, ValueError, KeyError):
                continue
            if any(v != v for v in rets):  # NaN
                continue
            date_key = row[""][:7]
            # до 2013 CPI нет в файле — приближение 7% годовых
            out.append((*rets, cpi.get(date_key, (1.07) ** (1 / 12) - 1)))
    return out if len(out) >= 60 else None


def _weights(snapshot: PortfolioSnapshot) -> tuple[float, float, float, float]:
    """Веса портфеля в порядке (акции, облигации, золото, кэш)."""
    total = float(snapshot.total)
    if total <= 0:
        return (0.5, 0.3, 0.2, 0.0)
    w = {c: 0.0 for c in ("MCFTR", "RGBITR", "GOLD_RUB_G", "CASH_PROXY")}
    for a in snapshot.allocation:
        col = CLASS_TO_COLUMN.get(a.asset_class, "CASH_PROXY")
        w[col] += float(a.amount) / total
    s = sum(w.values()) or 1.0
    return (w["MCFTR"] / s, w["RGBITR"] / s, w["GOLD_RUB_G"] / s, w["CASH_PROXY"] / s)


def monte_carlo_goal(snapshot: PortfolioSnapshot, profile: UserProfile,
                     name: str, target: Decimal, years: int,
                     returns=None, paths: int = PATHS, seed: int = SEED) -> GoalMonteCarlo | None:
    """Bootstrap-симуляция: распределение реального капитала к горизонту цели."""
    data = returns if returns is not None else _load_class_returns()
    if data is None:
        return None
    months = int(years) * 12
    if months <= 0:
        return None
    weq, wofz, wg, wcash = _weights(snapshot)
    monthly = float(profile.monthly_total())
    initial = float(snapshot.total)
    rng = random.Random(seed)
    n_blocks = (months + BLOCK - 1) // BLOCK
    max_start = len(data) - BLOCK
    finals = []
    for _ in range(paths):
        value = initial
        cpi = 1.0
        for _b in range(n_blocks):
            start = rng.randrange(max_start)
            for i in range(start, min(start + BLOCK, len(data))):
                eq, ofz, gold, cash, cpi_mom = data[i]
                value += monthly
                value *= 1 + weq * eq + wofz * ofz + wg * gold + wcash * cash
                cpi *= 1 + cpi_mom
        finals.append(value / cpi)
    finals.sort()
    p_success = sum(1 for v in finals if v >= float(target)) / len(finals) * 100

    def _q(p):
        return Decimal(str(round(finals[int(p * (len(finals) - 1))]))).quantize(Decimal("1"))

    return GoalMonteCarlo(
        name=name, target=target, years=int(years),
        p5=_q(0.05), p50=_q(0.50), p95=_q(0.95),
        p_success=Decimal(str(round(p_success, 1))),
    )


def monte_carlo_goals(snapshot: PortfolioSnapshot, profile: UserProfile,
                      returns=None) -> list[GoalMonteCarlo]:
    out = []
    for goal in profile.goals:
        mc = monte_carlo_goal(snapshot, profile, goal.name, goal.target,
                              goal.years, returns=returns)
        if mc is not None:
            out.append(mc)
    return out
