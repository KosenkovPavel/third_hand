"""Прогноз стоимости портфеля: сложный процент + регулярные пополнения."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .portfolio import PortfolioSnapshot

YEARS_TO_SHOW = (1, 3, 5, 10, 20)


@dataclass(frozen=True)
class ForecastPoint:
    years: int
    balance: Decimal
    contributed: Decimal
    profit: Decimal
    deduction_gain: Decimal = Decimal("0")
    balance_real: Decimal | None = None  # баланс в сегодняшних деньгах (с учётом инфляции)


@dataclass(frozen=True)
class Forecast:
    initial: Decimal
    monthly: Decimal
    annual_return_pct: Decimal
    points: tuple[ForecastPoint, ...]
    annual_deduction: Decimal = Decimal("0")
    inflation_pct: Decimal | None = None

    @property
    def final(self) -> ForecastPoint:
        return self.points[-1]


@dataclass(frozen=True)
class ScenarioForecast:
    """Один сценарий прогноза (пессимистичный / базовый / оптимистичный)."""

    label: str
    annual_return_pct: Decimal
    forecast: Forecast


def base_scenario(scenarios: list["ScenarioForecast"]) -> "ScenarioForecast":
    """Базовый сценарий из списка (по label; фолбэк — первый)."""
    return next((s for s in scenarios if s.label == "Базовый"), scenarios[0])


def forecast(
    snapshot: PortfolioSnapshot,
    monthly: Decimal,
    annual_return_pct: Decimal,
    annual_deduction: Decimal = Decimal("0"),
    inflation_pct: Decimal | None = None,
) -> Forecast:
    """Экстраполяция роста портфеля при заданных пополнениях и доходности.

    Месячная ставка выводится из годовой геометрически: rm = (1+r)^(1/12) - 1.
    Формула аннуитета: FV = PV*(1+rm)^n + M*[((1+rm)^n - 1)/rm].
    ``annual_deduction`` — ежегодное реинвестирование вычета ИИС (в конце года).
    ``inflation_pct`` — если задана, для каждой точки считается баланс
    «в сегодняшних деньгах»: balance / (1+i)^years.
    """
    pv = snapshot.total
    monthly = monthly or Decimal("0")
    annual_deduction = annual_deduction or Decimal("0")
    if annual_return_pct <= Decimal("-100"):
        raise ValueError("Доходность -100% и ниже обнуляет портфель — прогноз бессмысленен.")
    r = annual_return_pct / Decimal("100")

    points: list[ForecastPoint] = []
    for years in YEARS_TO_SHOW:
        fv = balance_at(pv, monthly, annual_return_pct, Decimal(years), annual_deduction)
        n = Decimal(years) * 12
        gain = Decimal("0")
        if annual_deduction > 0:
            if r != 0:
                gain = (annual_deduction * ((Decimal("1") + r) ** years - 1) / r).quantize(Decimal("0.01"))
            else:
                gain = (annual_deduction * years).quantize(Decimal("0.01"))
        contributed = (pv + monthly * n).quantize(Decimal("0.01"))
        real = (
            (fv / (Decimal("1") + inflation_pct / Decimal("100")) ** years).quantize(Decimal("0.01"))
            if inflation_pct is not None
            else None
        )
        points.append(
            ForecastPoint(
                years=years,
                balance=fv,
                contributed=contributed,
                profit=(fv - contributed).quantize(Decimal("0.01")),
                deduction_gain=gain,
                balance_real=real,
            )
        )

    return Forecast(
        initial=pv,
        monthly=monthly,
        annual_return_pct=annual_return_pct,
        points=tuple(points),
        annual_deduction=annual_deduction,
        inflation_pct=inflation_pct,
    )


def balance_at(
    pv: Decimal,
    monthly: Decimal,
    annual_return_pct: Decimal,
    years: Decimal,
    annual_deduction: Decimal = Decimal("0"),
) -> Decimal:
    """Баланс портфеля через ``years`` лет (может быть дробным) — та же модель,
    что и в forecast(): помесячный сложный процент + вычет ИИС в конце года."""
    pv = pv or Decimal("0")
    monthly = monthly or Decimal("0")
    annual_deduction = annual_deduction or Decimal("0")
    if annual_return_pct <= Decimal("-100"):
        raise ValueError("Доходность -100% и ниже обнуляет портфель — прогноз бессмысленен.")
    r = float(annual_return_pct) / 100
    rm = (1 + r) ** (1 / 12) - 1
    n = int(round(float(years) * 12))
    if rm != 0:
        factor = (1 + rm) ** n
        fv = float(pv) * factor + float(monthly) * (factor - 1) / rm
    else:
        fv = float(pv) + float(monthly) * n
    full_years = n // 12
    if annual_deduction > 0 and full_years > 0:
        if r != 0:
            fv += float(annual_deduction) * ((1 + r) ** full_years - 1) / r
        else:
            fv += float(annual_deduction) * full_years
    return Decimal(str(round(fv, 2)))


def forecast_scenarios(
    snapshot: PortfolioSnapshot,
    monthly: Decimal,
    annual_return_pct: Decimal,
    annual_deduction: Decimal = Decimal("0"),
    inflation_pct: Decimal | None = None,
    spread: Decimal = Decimal("5"),
) -> list[ScenarioForecast]:
    """Три сценария: пессимистичный (доходность −spread п.п.), базовый, оптимистичный.

    Сценарий, падающий с ValueError (например, доходность ≤ −100%), пропускается.
    """
    variants = (
        ("Пессимистичный", annual_return_pct - spread),
        ("Базовый", annual_return_pct),
        ("Оптимистичный", annual_return_pct + spread),
    )
    out: list[ScenarioForecast] = []
    for label, r in variants:
        try:
            fc = forecast(
                snapshot,
                monthly=monthly,
                annual_return_pct=r,
                annual_deduction=annual_deduction,
                inflation_pct=inflation_pct,
            )
        except ValueError:
            continue
        out.append(ScenarioForecast(label=label, annual_return_pct=r, forecast=fc))
    return out