"""Профиль пользователя: целевая структура портфеля и правила пополнений.

Профиль хранится в ``data/profile.json`` (пример — ``data/profile.example.json``).
Если файла нет, используются консервативные значения по умолчанию.

Поля ``contributions`` описывают регулярные пополнения:
    [{"day": 5, "amount": 5000}, {"day": 20, "amount": 5000}]

``target`` — целевая структура портфеля по классам активов, в сумме 100%.

Секция ``iis`` описывает параметры ИИС:
    {"type": "iis3" | "iis_a", "ndfl_rate": 0.13, "open_date": "YYYY-MM-DD",
     "claimed_years": [2025]}
``type`` по умолчанию определяется автоматически по дате открытия счёта,
``open_date`` — из данных счёта в API, ``ndfl_rate`` — ставка НДФЛ (0.13–0.22),
``claimed_years`` — годы, за которые вычет на взносы уже заявлен.

``stress``: true — стресс-режим подбора акций (тилт в защитные сектора,
дивиденды и низкую бету); включается также флагом CLI ``--stress``.

``emergency_fund_ok``: true — подтверждение, что подушка безопасности
(3–6 мес. расходов) лежит вне ИИС; без флага отчёт показывает напоминание.

``usdrub_alert``: порог курса USD/RUB для сигнала в блоке «Сигналы» (по умолчанию 100).

``goals`` — необязательный список долгосрочных целей:
    [{"name": "Квартира", "target": 3000000, "years": 7}, ...]
Для каждой цели отчёт показывает, успеваете ли при текущем темпе пополнений,
и рекомендуемую долю акций по горизонту.

``rules`` — правила портфельной дисциплины (все поля с дефолтами):
    {"max_stock_positions": 10, "min_position_pct": 5,
     "max_position_pct": 20, "max_sector_pct": 30}
Используются в блоке «Сигналы» (лимиты позиции/сектора/числа акций)
и в подборе акций (метки «докупка»/«новая», минимальный размер позиции).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .config import DATA_DIR

PROFILE_PATH = DATA_DIR / "profile.json"

DEFAULT_TARGET: dict[str, Decimal] = {"Акции": Decimal("70"), "Облигации": Decimal("30")}

IIS_TYPES = ("iis3", "iis_a")


def _decimal(raw, field_name: str) -> Decimal:
    """Decimal из значения профиля с понятной ошибкой вместо InvalidOperation."""
    try:
        return Decimal(str(raw))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"невалидное число в поле «{field_name}»: {raw!r}") from exc


@dataclass
class IisSettings:
    """Настройки ИИС: тип (iis3/iis_a, пусто — авто), ставка НДФЛ, дата открытия,
    годы с уже заявленным вычетом."""

    type: str | None = None
    ndfl_rate: Decimal = Decimal("0.13")
    open_date: str | None = None
    claimed_years: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ContributionRule:
    day: int
    amount: Decimal

    @classmethod
    def from_dict(cls, raw: dict) -> "ContributionRule":
        rule = cls(day=int(raw["day"]), amount=_decimal(raw.get("amount", 0), "contributions.amount"))
        if rule.amount < 0:
            raise ValueError(f"contributions: сумма пополнения не может быть отрицательной ({rule.amount})")
        return rule


@dataclass(frozen=True)
class ProfileGoal:
    """Долгосрочная цель из профиля: название, целевая сумма ₽, горизонт в годах."""

    name: str
    target: Decimal
    years: int

    @classmethod
    def from_dict(cls, raw: dict) -> "ProfileGoal":
        goal = cls(
            name=str(raw.get("name", "Цель")),
            target=_decimal(raw.get("target", 0), "goals.target"),
            years=int(raw.get("years", 0)),
        )
        if goal.target <= 0:
            raise ValueError(f"goals.{goal.name}: целевая сумма должна быть > 0")
        if goal.years <= 0:
            raise ValueError(f"goals.{goal.name}: горизонт years должен быть > 0")
        return goal


DEFAULT_USDRUB_ALERT = Decimal("100")
DEFAULT_COMMISSION_PCT = Decimal("0.3")
DEFAULT_INFLATION_PCT = Decimal("7")


@dataclass
class Rules:
    """Правила портфельной дисциплины (проценты — от стоимости портфеля)."""

    max_stock_positions: int = 10      # максимум акций в портфеле
    min_position_pct: Decimal = Decimal("5")   # ниже — новую позицию не открывать
    max_position_pct: Decimal = Decimal("20")  # максимум на одну компанию
    max_sector_pct: Decimal = Decimal("30")    # максимум на сектор

    @classmethod
    def from_dict(cls, raw: dict) -> "Rules":
        rules = cls()
        if raw.get("max_stock_positions") is not None:
            rules.max_stock_positions = int(raw["max_stock_positions"])
        if rules.max_stock_positions < 1:
            raise ValueError("rules.max_stock_positions должен быть >= 1")
        for key in ("min_position_pct", "max_position_pct", "max_sector_pct"):
            if raw.get(key) is not None:
                value = _decimal(raw[key], f"rules.{key}")
                if not (Decimal("0") <= value <= Decimal("100")):
                    raise ValueError(f"rules.{key} должен быть в диапазоне 0–100, получено {value}")
                setattr(rules, key, value)
        return rules


@dataclass
class UserProfile:
    target: dict[str, Decimal] = field(
        default_factory=lambda: dict(DEFAULT_TARGET)
    )
    contributions: list[ContributionRule] = field(default_factory=list)
    iis: IisSettings = field(default_factory=IisSettings)
    stress: bool = False
    emergency_fund_ok: bool = False
    usdrub_alert: Decimal = DEFAULT_USDRUB_ALERT
    goals: list[ProfileGoal] = field(default_factory=list)
    rules: Rules = field(default_factory=Rules)
    commission_pct: Decimal = DEFAULT_COMMISSION_PCT
    inflation_pct: Decimal = DEFAULT_INFLATION_PCT

    def monthly_total(self) -> Decimal:
        return sum((c.amount for c in self.contributions), Decimal("0"))

    def split_amount(self, amount: Decimal) -> dict[str, Decimal]:
        """Разбивает сумму пополнения по целевым классам активов.

        Недоходные классы (кэш) игнорируются; сумма распределяется
        пропорционально целевым весам активных классов.
        """
        base = {k: v for k, v in self.target.items() if v and v > 0}
        total_w = sum(base.values())
        if total_w <= 0:
            return {}
        return {
            k: (v / total_w * amount).quantize(Decimal("0.01"))
            for k, v in base.items()
        }

    def describe(self) -> str:
        parts = [f"{k} {v:.0f}%" for k, v in self.target.items()]
        if self.contributions:
            parts.append(
                "пополнение: " + ", ".join(f"{c.day}-го {c.amount:.0f} ₽" for c in self.contributions)
            )
        return "; ".join(parts)


def load_profile(path: Path | None = None) -> UserProfile:
    path = path or PROFILE_PATH
    if not path.exists():
        return UserProfile()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Не удалось прочитать профиль {path}: {exc}") from exc

    target = DEFAULT_TARGET
    if isinstance(raw.get("target"), dict):
        t = {}
        for k, v in raw["target"].items():
            d = _decimal(v, f"target.{k}")
            if d > 0:
                t[str(k)] = d
        if t:
            target = t

    contributions = [
        ContributionRule.from_dict(c)
        for c in raw.get("contributions", [])
        if isinstance(c, dict) and "day" in c
    ]

    iis = IisSettings()
    raw_iis = raw.get("iis")
    if isinstance(raw_iis, dict):
        if raw_iis.get("type") in IIS_TYPES:
            iis.type = raw_iis["type"]
        if raw_iis.get("ndfl_rate") is not None:
            rate = _decimal(raw_iis["ndfl_rate"], "iis.ndfl_rate")
            if not (Decimal("0.13") <= rate <= Decimal("0.22")):
                raise ValueError(
                    f"iis.ndfl_rate должен быть в диапазоне 0.13–0.22, получено {rate}"
                )
            iis.ndfl_rate = rate
        if raw_iis.get("open_date"):
            iis.open_date = str(raw_iis["open_date"])
        raw_claimed = raw_iis.get("claimed_years")
        if isinstance(raw_claimed, list):
            try:
                iis.claimed_years = [int(y) for y in raw_claimed if isinstance(y, (int, str))]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"невалидный год в iis.claimed_years: {raw_claimed!r}") from exc

    goals = [
        ProfileGoal.from_dict(g)
        for g in raw.get("goals", [])
        if isinstance(g, dict)
    ]

    usdrub_alert = DEFAULT_USDRUB_ALERT
    if raw.get("usdrub_alert") is not None:
        usdrub_alert = _decimal(raw["usdrub_alert"], "usdrub_alert")

    commission_pct = DEFAULT_COMMISSION_PCT
    if raw.get("commission_pct") is not None:
        commission_pct = _decimal(raw["commission_pct"], "commission_pct")
    inflation_pct = DEFAULT_INFLATION_PCT
    if raw.get("inflation_pct") is not None:
        inflation_pct = _decimal(raw["inflation_pct"], "inflation_pct")

    rules = Rules.from_dict(raw["rules"]) if isinstance(raw.get("rules"), dict) else Rules()

    return UserProfile(
        target=target,
        contributions=contributions,
        iis=iis,
        stress=bool(raw.get("stress", False)),
        emergency_fund_ok=bool(raw.get("emergency_fund_ok", False)),
        usdrub_alert=usdrub_alert,
        goals=goals,
        rules=rules,
        commission_pct=commission_pct,
        inflation_pct=inflation_pct,
    )