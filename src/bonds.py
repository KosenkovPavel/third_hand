"""Скринер облигаций: подбор ОФЗ и корпоративных бондов по доходности.

Доходность к погашению (YTM) считается как IRR по фактическому графику
купонов до даты погашения (численное решение бисекцией).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .money import to_decimal
from .risk import SECTOR_NAMES_RU
from .providers.tinkoff import TinkoffClient

COUPON_FWD_DAYS = 425
YEAR_DAYS = Decimal("365.25")
MAX_YEARS = 10


@dataclass(frozen=True)
class BondFilters:
    ytm_min: float | None = None
    ytm_max: float | None = None
    coupon_min: float | None = None
    min_years_left: float | None = None
    max_items: int = 60


@dataclass
class BondRow:
    ticker: str
    name: str
    sector: str
    price: Decimal
    price_pct: Decimal
    nominal: Decimal
    annual_coupon: Decimal
    coupon_yield_pct: Decimal
    ytm_approx_pct: Decimal | None
    coupon_count_year: int
    first_coupon: Decimal
    first_coupon_date: datetime | None
    maturity_date: datetime | None
    years_left: Decimal
    floatable: bool
    amortized: bool
    subordinated: bool
    notes: list[str] = field(default_factory=list)


def bond_screen(client: TinkoffClient, filters: BondFilters) -> list[BondRow]:
    """Возвращает облигации, отсортированные по приближённой доходности."""
    now = datetime.now(timezone.utc)

    candidates = []
    for b in client.get_bonds():
        if b.currency != "rub" or b.country_of_risk != "RU":
            continue
        if not b.buy_available_flag or not b.liquidity_flag:
            continue
        if b.perpetual_flag:
            continue
        if b.maturity_date and b.maturity_date < now + timedelta(days=60):
            continue
        if filters.min_years_left is not None and b.maturity_date:
            years = (b.maturity_date - now).days / YEAR_DAYS
            if years < filters.min_years_left:
                continue
        candidates.append(b)

    candidates.sort(key=lambda b: b.issue_size or 0, reverse=True)
    candidates = candidates[: filters.max_items]

    by_figi = {b.figi: b for b in candidates}
    figis = list(by_figi)
    prices = _last_prices(client, figis)
    coupons = _coupons(client, figis, now, by_figi)

    rows: list[BondRow] = []
    for b in candidates:
        price = prices.get(b.figi)
        events = coupons.get(b.figi)
        if price is None or events is None:
            continue
        row = _build_row(b, price, events, now)
        if filters.ytm_min is not None and (row.ytm_approx_pct is None or row.ytm_approx_pct < Decimal(filters.ytm_min)):
            continue
        if filters.ytm_max is not None and row.ytm_approx_pct is not None and row.ytm_approx_pct > Decimal(filters.ytm_max):
            continue
        if filters.coupon_min is not None and row.coupon_yield_pct < Decimal(filters.coupon_min):
            continue
        # флоатеры и амортизируемые (YTM=None) остаются в выдаче с пометкой
        rows.append(row)

    rows.sort(key=lambda r: (r.ytm_approx_pct is not None, r.ytm_approx_pct or Decimal("0")), reverse=True)
    return rows[:15]


def _last_prices(client: TinkoffClient, figis: list[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    if not figis:
        return out
    for batch in [figis[i : i + 30] for i in range(0, len(figis), 30)]:
        try:
            resp = client.get_last_prices(batch)
        except Exception:
            continue
        for p in resp.last_prices:
            if p.price is not None:
                out[p.figi] = to_decimal(p.price)
    return out


def _coupons(
    client: TinkoffClient, figis: list[str], now: datetime, by_figi: dict[str, object] | None = None
) -> dict[str, list]:
    """Купоны с сегодня до погашения (или до +MAX_YEARS, если нет даты)."""
    by_figi = by_figi or {}
    out: dict[str, list] = {}
    for figi in figis:
        try:
            b = by_figi.get(figi)
            if b is None:
                b = next(b for b in client.get_bonds() if b.figi == figi)
            to = b.maturity_date if b.maturity_date else now + timedelta(days=MAX_YEARS * 365)
            resp = client.get_bond_coupons(figi, now, to)
            out[figi] = list(resp.events)
        except Exception:
            continue
    return out


def _solve_ytm(price: Decimal, cashflows: list[tuple[Decimal, float]]) -> Decimal | None:
    """Численно решает YTM (IRR) бисекцией по годовым ставкам.

    ``cashflows`` — список (сумма, долей года от сегодня). Ожидается, что
    последний поток включает номинал в дату погашения.
    """
    if price <= 0 or not cashflows:
        return None

    def npv(rate: float) -> float:
        return sum(float(cf) / (1 + rate) ** t for cf, t in cashflows)

    lo, hi = Decimal("-0.5"), Decimal("3")  # −50% … 300% годовых
    for _ in range(200):
        mid = (lo + hi) / 2
        pv = Decimal(str(npv(float(mid))))
        if pv > price:
            lo = mid
        else:
            hi = mid
    y = float((lo + hi) / 2) * 100
    return Decimal(str(round(y, 2)))


def _build_row(b, price: Decimal, events: list, now: datetime) -> BondRow:
    nominal = to_decimal(b.nominal) if b.nominal else Decimal("0")

    ticker = (b.ticker or "").upper()
    name = (b.name or "").upper()
    # ОФЗ (тикер SU…) котируются в % от номинала, корпоративные — в рублях.
    # Порог «цена < 50% номинала» ненадёжен для дефолтных бумаг — определяем по классу.
    if nominal and (ticker.startswith("SU") or "ОФЗ" in name):
        price_rub = price / 100 * nominal
    else:
        price_rub = price
    price = price_rub

    year_end = now + timedelta(days=int(YEAR_DAYS))

    year_events = [e for e in events if e.coupon_date <= year_end]
    first = next((e for e in events if e.coupon_date >= now), None)

    annual_coupon = sum((to_decimal(e.pay_one_bond) for e in year_events if e.pay_one_bond), Decimal("0"))
    coupon_count_year = len(year_events)

    coupon_yield = (annual_coupon / price * 100) if price else Decimal("0")

    maturity = b.maturity_date
    years_left = Decimal("0")
    ytm: Decimal | None = None
    if maturity and maturity > now:
        years_left = Decimal((maturity - now).days) / YEAR_DAYS

    if not b.floating_coupon_flag and not b.amortization_flag:
        # точный YTM только для фиксированного купона без амортизации:
        # у флоатеров ставка будущих купонов неизвестна, у амортизируемых —
        # номинал гасится частями (расчёт с полным номиналом завышает YTM).
        # Покупка идёт по полной цене: чистая цена + НКД (aci_value).
        aci = to_decimal(getattr(b, "aci_value", None))
        cashflows: list[tuple[Decimal, float]] = []
        for e in events:
            if e.coupon_date and e.coupon_date >= now and e.pay_one_bond:
                t = float((e.coupon_date - now).days / YEAR_DAYS)
                cashflows.append((to_decimal(e.pay_one_bond), t))
        if maturity and maturity > now:
            cashflows.append(
                (nominal, float((maturity - now).days / YEAR_DAYS))
            )
        ytm = _solve_ytm(price_rub + aci, cashflows)

    notes: list[str] = []
    if b.floating_coupon_flag:
        notes.append("плавающий купон")
    if b.amortization_flag:
        notes.append("амортизация")
    if b.subordinated_flag:
        notes.append("субординированная")
    if b.for_iis_flag:
        notes.append("можно в ИИС")

    return BondRow(
        ticker=b.ticker,
        name=b.name,
        sector=SECTOR_NAMES_RU.get(b.sector or "", b.sector or "—"),
        price=price.quantize(Decimal("0.01")),
        price_pct=(price / nominal * 100).quantize(Decimal("0.01")) if nominal else Decimal("0"),
        nominal=nominal,
        annual_coupon=annual_coupon.quantize(Decimal("0.01")),
        coupon_yield_pct=coupon_yield.quantize(Decimal("0.01")),
        ytm_approx_pct=ytm.quantize(Decimal("0.01")) if ytm is not None else None,
        coupon_count_year=coupon_count_year,
        first_coupon=to_decimal(first.pay_one_bond).quantize(Decimal("0.01")) if first and first.pay_one_bond else Decimal("0"),
        first_coupon_date=first.coupon_date if first else None,
        maturity_date=maturity,
        years_left=years_left.quantize(Decimal("0.1")),
        floatable=b.floating_coupon_flag,
        amortized=b.amortization_flag,
        subordinated=b.subordinated_flag,
        notes=notes,
    )