"""ИИС: вычеты на взносы и на доход, сроки владения ИИС-3, прогресс до лимита."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .accounting import AccountingReport

# Вычет на взносы: база 400 000 ₽/год, возврат по ставке НДФЛ инвестора
TYPE_A_LIMIT = Decimal("400000")
DEFAULT_NDFL_RATE = Decimal("0.13")

# ИИС-3: минимальный срок владения зависит от года открытия договора
IIS3_FIRST_YEAR = 2024
IIS3_MIN_YEARS_BASE = 5  # договоры 2024–2026 гг.; далее +1 год за год открытия
IIS3_MIN_YEARS_MAX = 10
IIS3_INCOME_EXEMPT_LIMIT = Decimal("30000000")  # освобождение дохода при закрытии

IIS_TYPE_NAMES = {"iis3": "ИИС-3", "iis_a": "ИИС (тип А)"}


@dataclass
class IisYear:
    year: int
    contributed: Decimal
    capped: Decimal
    potential_return: Decimal
    over_limit: bool


def iis_deduction(acct: AccountingReport, rate: Decimal = DEFAULT_NDFL_RATE) -> list[IisYear]:
    """Год за годом: взносы и максимальный возврат по вычету на взносы."""
    year_totals: dict[int, Decimal] = {}
    for op in acct.ops:
        if op.kind == "in":
            yr = op.date.year
            year_totals[yr] = year_totals.get(yr, Decimal("0")) + op.payment

    if not year_totals:
        return []

    result: list[IisYear] = []
    for yr, total in sorted(year_totals.items()):
        capped = min(total, TYPE_A_LIMIT)
        over = total > TYPE_A_LIMIT
        result.append(
            IisYear(
                year=yr,
                contributed=total,
                capped=capped,
                potential_return=(capped * rate).quantize(Decimal("0.01")),
                over_limit=over,
            )
        )
    return result


def annual_deduction(monthly: Decimal, rate: Decimal = DEFAULT_NDFL_RATE) -> Decimal:
    """Годовой вычет на взносы при регулярных пополнениях: rate от взносов в пределах лимита."""
    yearly = (monthly or Decimal("0")) * 12
    return (min(yearly, TYPE_A_LIMIT) * rate).quantize(Decimal("0.01"))


@dataclass
class IisProgress:
    year: int
    contributed: Decimal
    limit: Decimal
    remaining: Decimal
    missing_return: Decimal


def deduction_progress(
    acct: AccountingReport,
    year: int | None = None,
    rate: Decimal = DEFAULT_NDFL_RATE,
) -> IisProgress | None:
    """Прогресс до лимита вычета за текущий год."""
    year = year or datetime.now().year
    contributed = sum(
        (op.payment for op in acct.ops if op.kind == "in" and op.date.year == year),
        Decimal("0"),
    )
    if contributed <= 0:
        return None
    remaining = max(Decimal("0"), TYPE_A_LIMIT - contributed)
    return IisProgress(
        year=year,
        contributed=contributed,
        limit=TYPE_A_LIMIT,
        remaining=remaining,
        missing_return=(remaining * rate).quantize(Decimal("0.01")),
    )


def tax_calendar_notes(today: date | None = None) -> list[str]:
    """Статичные напоминания про налоговый вычет по текущей дате."""
    today = today or datetime.now().date()
    year = today.year
    days_left = (date(year, 12, 31) - today).days
    notes = [
        f"Вычет за {year} год заявляется в {year + 1} году (через декларацию 3-НДФЛ)",
        f"Вычет можно заявить за последние 3 года: {year - 3}–{year - 1}",
    ]
    if days_left > 0:
        notes.append(
            f"До конца года {days_left} дн. — пополнения до 31.12.{year} идут в базу вычета за {year} год"
        )
    return notes


# Вычет на взносы за год Y заявляется с года Y+1 и в течение 3 лет
CLAIM_WINDOW_YEARS = 3

CLAIM_STATUS_NAMES = {
    "available": "можно заявить уже сейчас",
    "early": "рано",
    "expired": "сгорел",
}


@dataclass(frozen=True)
class ClaimStatus:
    """Незаявленный вычет за год: сумма и статус срока заявления."""

    year: int
    potential_return: Decimal
    status: str  # "available" | "early" | "expired"


def deduction_claim_statuses(
    years: list[IisYear],
    claimed: list[int] | None,
    today: date | None = None,
) -> list[ClaimStatus]:
    """Годы с взносами, по которым вычет ещё не заявлен (claimed — из профиля).

    За год Y вычет заявляется с Y+1 в течение 3 лет; позже — сгорает.
    """
    today = today or datetime.now().date()
    claimed_set = set(claimed or [])
    out: list[ClaimStatus] = []
    for y in years:
        if y.year in claimed_set:
            continue
        if today.year <= y.year:
            status = "early"
        elif today <= date(y.year + CLAIM_WINDOW_YEARS + 1, 4, 30):
            # вычет за год Y заявляется с Y+1 до 30 апреля Y+4 (3-НДФЛ)
            status = "available"
        else:
            status = "expired"
        out.append(ClaimStatus(year=y.year, potential_return=y.potential_return, status=status))
    return out


def tax_report_text(
    acct: AccountingReport,
    year: int,
    rate: Decimal = DEFAULT_NDFL_RATE,
) -> str | None:
    """Текст справки для декларации 3-НДФЛ за год; None — взносов не было."""
    by_year = {y.year: y for y in iis_deduction(acct, rate=rate)}
    y = by_year.get(year)
    if y is None:
        return None

    def money(v: Decimal) -> str:
        return f"{v:,.2f} ₽".replace(",", " ")

    return "\n".join(
        [
            f"Справка для декларации 3-НДФЛ — инвестиционный вычет на взносы ИИС",
            f"Год: {y.year}",
            f"Внесено на ИИС за год: {money(y.contributed)}",
            f"База вычета (не более {money(TYPE_A_LIMIT)}): {money(y.capped)}",
            f"НДФЛ к возврату (ставка {rate:.0%}): {money(y.potential_return)}",
            "",
            "Шаблон для декларации:",
            f"«Заявляю инвестиционный налоговый вычет по пп. 2 п. 1 ст. 219.1 НК РФ",
            f"на сумму взносов {money(y.capped)}. К вычету: {money(y.potential_return)}.",
            "Подтверждающие документы: договор ИИС, справка брокера о зачислении средств.»",
        ]
    )


def min_holding_years(open_year: int) -> int:
    """Минимальный срок владения ИИС-3 по году открытия договора."""
    if open_year <= 2026:
        return IIS3_MIN_YEARS_BASE
    return min(IIS3_MIN_YEARS_MAX, IIS3_MIN_YEARS_BASE + open_year - 2026)


@dataclass
class IisTerm:
    opened: date
    min_years: int
    mature_date: date
    expired: bool
    years_left: int
    months_left: int


def holding_term(opened: date | None, today: date | None = None) -> IisTerm | None:
    """Дата, с которой закрытие ИИС-3 не лишает вычетов, и сколько до неё осталось."""
    if opened is None:
        return None
    today = today or datetime.now().date()
    years = min_holding_years(opened.year)
    try:
        mature = opened.replace(year=opened.year + years)
    except ValueError:  # 29 февраля
        mature = opened.replace(year=opened.year + years, day=28)
    expired = mature <= today
    if expired:
        years_left, months_left = 0, 0
    else:
        months = (mature.year - today.year) * 12 + mature.month - today.month
        if mature.day < today.day:
            months -= 1
        years_left, months_left = divmod(months, 12)
    return IisTerm(
        opened=opened,
        min_years=years,
        mature_date=mature,
        expired=expired,
        years_left=years_left,
        months_left=months_left,
    )


@dataclass
class IisInfo:
    iis_type: str  # "iis3" | "iis_a"
    type_auto: bool  # тип определён автоматически (не задан в профиле)
    ndfl_rate: Decimal
    open_date: date | None
    term: IisTerm | None


def resolve_iis(settings, opened_date: datetime | None, is_iis: bool) -> IisInfo | None:
    """Сводит настройки профиля (iis-секция) и данные счёта в итоговые параметры ИИС."""
    if not is_iis:
        return None
    open_dt: date | None = None
    if settings.open_date:
        try:
            open_dt = date.fromisoformat(str(settings.open_date))
        except ValueError:
            open_dt = None
    if open_dt is None and opened_date is not None and opened_date.year > 1970:
        open_dt = opened_date.date()
    iis_type = settings.type
    type_auto = iis_type not in IIS_TYPE_NAMES
    if type_auto:
        iis_type = "iis3" if (open_dt is None or open_dt >= date(IIS3_FIRST_YEAR, 1, 1)) else "iis_a"
    term = holding_term(open_dt) if iis_type == "iis3" else None
    return IisInfo(
        iis_type=iis_type,
        type_auto=type_auto,
        ndfl_rate=settings.ndfl_rate,
        open_date=open_dt,
        term=term,
    )
