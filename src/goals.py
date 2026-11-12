"""Траектория к пенсии: целевой реальный капитал и сколько лет до него.

Всё считается в реальных деньгах (покупательной способности сегодня):
    цель = расходы × 12 / норма изъятия   (по умолчанию изъятие 4% → ×300)

Номинальные суммы показываются отдельно — они зависят от заложенной инфляции
и равны «реальная цель × (1+инфляция)^лет».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from .iis import DEFAULT_NDFL_RATE, annual_deduction
from .forecast import balance_at
from .profile import ProfileGoal

DEFAULT_INCOME = Decimal("100000")      # расходы сегодня, реальных ₽/мес
DEFAULT_WITHDRAW_PCT = Decimal("4")     # норма изъятия капитала в год
DEFAULT_RETURN_PCT = Decimal("15")      # номинальная доходность, %/год
DEFAULT_MONTHLY = Decimal("20000")      # вклад в месяц, реальных ₽
DEFAULT_INFLATIONS = (Decimal("7"), Decimal("10"))
MAX_YEARS = 60


@dataclass(frozen=True)
class GoalScenario:
    inflation_pct: Decimal
    real_return_pct: Decimal
    years_to_goal: Decimal | None
    nominal_target: Decimal | None


@dataclass(frozen=True)
class YearRow:
    year: int
    balance_real: Decimal
    nominal_base: Decimal
    passive_monthly: Decimal


@dataclass
class GoalReport:
    income: Decimal
    withdraw_pct: Decimal
    return_pct: Decimal
    monthly: Decimal
    start: Decimal
    target_real: Decimal
    base_inflation_pct: Decimal
    scenarios: list[GoalScenario] = field(default_factory=list)
    rows: list[YearRow] = field(default_factory=list)
    progress_pct: Decimal = Decimal("0")
    annual_deduction: Decimal = Decimal("0")


def required_monthly(
    start: Decimal,
    target: Decimal,
    annual_return_pct: Decimal,
    years: int,
) -> Decimal | None:
    """Пополнение в месяц, при котором цель достигается за ``years`` лет.

    Та же модель, что в forecast(): месячная ставка из годовой геометрически.
    None — если цель недостижима математически (уже достигнута или ставка ≤ −100%).
    """
    if target <= start:
        return None
    if annual_return_pct <= Decimal("-100"):
        return None
    r = float(annual_return_pct) / 100
    rm = (1 + r) ** (1 / 12) - 1
    n = years * 12
    factor = (1 + rm) ** n
    rest = float(target) - float(start) * factor
    if rm == 0:
        m = rest / n
    else:
        m = rest * rm / (factor - 1)
    # округление вверх — чтобы при получившемся пополнении цель точно накрывалась
    return Decimal(str(max(m, 0.0))).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def stock_share_hint(years: int) -> str:
    """Рекомендуемая доля акций по горизонту цели (текст-эвристика)."""
    if years < 3:
        return "0–20% акций (горизонт короткий — облигации/деньги)"
    if years <= 7:
        return "40–60% акций"
    return "как в профиле (длинный горизонт)"


@dataclass(frozen=True)
class GoalStatus:
    """Оценка достижимости цели из профиля при текущем темпе пополнений."""

    goal: ProfileGoal
    projected: Decimal          # прогнозируемый баланс к горизонту
    feasible: bool              # проекция покрывает цель
    needed_monthly: Decimal | None  # требуемое пополнение/мес, если не успеваем


def evaluate_goal(
    goal: ProfileGoal,
    start: Decimal,
    monthly: Decimal,
    annual_return_pct: Decimal,
) -> GoalStatus:
    projected = balance_at(start, monthly, annual_return_pct, Decimal(goal.years))
    feasible = projected >= goal.target
    needed = None if feasible else required_monthly(start, goal.target, annual_return_pct, goal.years)
    return GoalStatus(goal=goal, projected=projected, feasible=feasible, needed_monthly=needed)


def real_return(nominal_pct: Decimal, inflation_pct: Decimal) -> Decimal:
    """Реальная доходность по Фишеру: (1+r)/(1+i)−1."""
    return ((1 + nominal_pct / 100) / (1 + inflation_pct / 100) - 1) * 100


def _yearly_balances(
    start: Decimal,
    monthly: Decimal,
    real_annual_pct: Decimal,
    target: Decimal,
    annual_bonus: float = 0.0,
) -> tuple[list[float], float | None]:
    """Помесячная симуляция; возвращает баланс на конец каждого года и число лет до цели.

    ``annual_bonus`` — ежегодный приток в конце года (реинвестирование вычета ИИС).
    """
    real = float(real_annual_pct)
    g = (1 + real / 100) ** (1 / 12)
    balance = float(start)
    years: float | None = None
    balances: list[float] = []
    month = 0
    while month < MAX_YEARS * 12:
        balance = balance * g + float(monthly)
        month += 1
        if month % 12 == 0:
            balance += annual_bonus
        if balance >= float(target):
            years = month / 12.0
            balances.append(balance)
            break
        if month % 12 == 0:
            balances.append(balance)
    return balances, years


def build_goal_report(
    start: Decimal,
    income: Decimal = DEFAULT_INCOME,
    withdraw_pct: Decimal = DEFAULT_WITHDRAW_PCT,
    return_pct: Decimal = DEFAULT_RETURN_PCT,
    monthly: Decimal = DEFAULT_MONTHLY,
    inflations: tuple[Decimal, ...] = DEFAULT_INFLATIONS,
    reinvest_deduction: bool = False,
    ndfl_rate: Decimal = DEFAULT_NDFL_RATE,
) -> GoalReport:
    """Считает траекторию: сколько лет идти до реального целевого капитала.

    ``reinvest_deduction`` — реинвестировать вычет ИИС (ставка ``ndfl_rate``
    от взносов за год).
    """
    inflations = tuple(inflations) if inflations else DEFAULT_INFLATIONS
    target = (income * 12 / (withdraw_pct / 100)).quantize(Decimal("1"))
    deduction = annual_deduction(monthly, rate=ndfl_rate) if reinvest_deduction else Decimal("0")
    bonus = float(deduction)

    scenarios: list[GoalScenario] = []
    for i in inflations:
        real = real_return(return_pct, i)
        _, yrs = _yearly_balances(start, monthly, real, target, bonus)
        nominal = (
            Decimal(str(round(float(target) * (1 + float(i) / 100) ** yrs)))
            if yrs is not None
            else None
        )
        scenarios.append(
            GoalScenario(
                inflation_pct=i,
                real_return_pct=real.quantize(Decimal("0.1")),
                years_to_goal=Decimal(str(round(yrs))) if yrs is not None else None,
                nominal_target=nominal,
            )
        )

    base_inflation = inflations[0]
    base_real = real_return(return_pct, base_inflation)
    balances, _ = _yearly_balances(start, monthly, base_real, target, bonus)

    rows = [
        YearRow(
            year=year,
            balance_real=Decimal(str(round(bal))),
            nominal_base=Decimal(str(round(bal * (1 + float(base_inflation) / 100) ** year))),
            passive_monthly=Decimal(str(round(bal * float(withdraw_pct) / 100 / 12))),
        )
        for year, bal in enumerate(balances, start=1)
    ]

    progress = (start / target * 100).quantize(Decimal("0.1")) if target else Decimal("0")

    return GoalReport(
        income=income,
        withdraw_pct=withdraw_pct,
        return_pct=return_pct,
        monthly=monthly,
        start=start,
        target_real=target,
        base_inflation_pct=base_inflation,
        scenarios=scenarios,
        rows=rows,
        progress_pct=progress,
        annual_deduction=deduction,
    )