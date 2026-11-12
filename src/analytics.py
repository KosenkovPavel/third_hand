"""Фундаментальный анализ и рекомендации по позициям портфеля.

Для акций/ETF берутся мультипликаторы из T-Invest API (get_asset_fundamentals)
и выставляется числовая оценка (0–100) с рекомендацией: «Покупать», «Держать»,
«Продавать» или «Нет данных».

Облигации не оцениваются — они купонные инструменты, их доходность
рассматривается в блоке дивидендной доходности отчёта.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from decimal import Decimal

from .portfolio import PortfolioSnapshot, Position
from .providers.tinkoff import TinkoffClient

logger = logging.getLogger(__name__)

# Стресс-режим: секторный тилт. Совпадение по подстроке без учёта регистра
# (сырые сектора API латинские, в данных могут встречаться и русские).
STRESS_DEFENSIVE_SECTORS = (
    "utilities", "telecom", "energy", "materials", "metals", "staple",
    "электроэнерг", "энергетика", "связь", "сырь", "металл",
)
STRESS_CYCLICAL_SECTORS = (
    "financial", "real_estate", "transport", "consumer", "discretionary",
    "финанс", "недвижим", "транспорт", "потреб",
)
STRESS_SECTOR_TILT = 10


def sector_stress_tilt(sector: str | None) -> int:
    """Тилт сектора в стресс-режиме: +10 защитным, −10 циклическим, 0 нейтральным."""
    s = (sector or "").lower()
    if not s:
        return 0
    if any(k in s for k in STRESS_DEFENSIVE_SECTORS):
        return STRESS_SECTOR_TILT
    if any(k in s for k in STRESS_CYCLICAL_SECTORS):
        return -STRESS_SECTOR_TILT
    return 0


@dataclass(frozen=True)
class Recommendation:
    ticker: str
    name: str
    instrument_type: str
    action: str
    score: int
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    forward_dividend_income: Decimal = Decimal("0")
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None
    div_yield: float | None = None
    payout: float | None = None
    rev_growth: float | None = None
    debt_eq: float | None = None
    beta: float | None = None


def analyze(
    client: TinkoffClient,
    snapshot: PortfolioSnapshot,
    issues: list[str] | None = None,
) -> list[Recommendation]:
    """Собирает фундаментальные данные по акциям/ETF и строит рекомендации."""
    targets = [
        p
        for p in snapshot.positions
        if p.asset_uid and p.instrument_type in ("share", "etf")
    ]
    if not targets:
        return []

    uids = [p.asset_uid for p in targets]
    ticker_by_uid = {p.asset_uid: p.ticker for p in targets}
    fund_by_uid: dict[str, object] = {}

    try:
        resp = client.get_asset_fundamentals(uids)
        for f in resp.fundamentals:
            fund_by_uid[f.asset_uid] = f
    except Exception:
        logger.debug("Batch fundamentals failed, fetching one by one")
        for uid in uids:
            try:
                resp = client.get_asset_fundamentals([uid])
                for f in resp.fundamentals:
                    fund_by_uid[f.asset_uid] = f
            except Exception as exc:
                logger.debug("Could not fetch fundamentals for %s", uid)
                if issues is not None:
                    issues.append(
                        f"{ticker_by_uid[uid]}: фундаментальные данные не загружены ({exc})"
                    )

    recs: list[Recommendation] = []
    for p in targets:
        f = fund_by_uid.get(p.asset_uid)
        recs.append(_build_recommendation(p, f))
    return recs


def apply_portfolio_rules(
    recommendations: list[Recommendation],
    snapshot: PortfolioSnapshot,
    rules,
    sector_by_uid: dict[str, str] | None = None,
) -> list[Recommendation]:
    """Запрещает докупку при нарушении позиционного или секторного лимита."""
    shares = {
        p.ticker: p
        for p in snapshot.positions
        if p.instrument_type == "share" and p.amount > 0
    }
    sector_by_uid = sector_by_uid or {}
    sector_totals: dict[str, Decimal] = {}
    for p in shares.values():
        sector = sector_by_uid.get(p.instrument_uid, "")
        if sector:
            sector_totals[sector] = sector_totals.get(sector, Decimal("0")) + p.amount

    constrained = []
    for rec in recommendations:
        position = shares.get(rec.ticker)
        if rec.action != "Покупать" or position is None or snapshot.total <= 0:
            constrained.append(rec)
            continue
        restrictions = []
        position_pct = position.amount / snapshot.total * 100
        if position_pct > rules.max_position_pct:
            restrictions.append(
                f"Доля {position_pct:.1f}% выше лимита {rules.max_position_pct:.0f}% — не докупать"
            )
        sector = sector_by_uid.get(position.instrument_uid, "")
        if sector and sector_totals[sector] / snapshot.total * 100 > rules.max_sector_pct:
            sector_pct = sector_totals[sector] / snapshot.total * 100
            restrictions.append(
                f"Сектор {sector_pct:.1f}% выше лимита {rules.max_sector_pct:.0f}% — не докупать"
            )
        if restrictions:
            rec = replace(rec, action="Держать", risks=[*rec.risks, *restrictions])
        constrained.append(rec)
    return constrained


# ─────────────────────────────────────────────
#  Scoring helpers
# ─────────────────────────────────────────────

def score_fundamentals(
    *,
    ticker: str,
    name: str,
    instrument_type: str,
    f,
    forward_dividend: Decimal | None = None,
    sector: str | None = None,
    stress: bool = False,
) -> Recommendation:
    """Выставляет фундаментальную оценку (0–100) по данным get_asset_fundamentals.

    Пригодна как для позиций портфеля, так и для любых бумаг из универсума.
    В стресс-режиме (``stress=True``): выше вес дивидендной доходности,
    секторный тилт (защитные +, циклические −), предпочтение низкой беты.
    """
    if f is None:
        return Recommendation(
            ticker=ticker,
            name=name,
            instrument_type=instrument_type,
            action="Нет данных",
            score=0,
            reasons=["Фундаментальные данные недоступны"],
            forward_dividend_income=forward_dividend or Decimal("0"),
        )

    pe = f.pe_ratio_ttm or 0.0
    pb = f.price_to_book_ttm or 0.0
    roe = f.roe or 0.0
    div_yield = f.dividend_yield_daily_ttm or 0.0
    payout = f.dividend_payout_ratio_fy or 0.0
    rev_growth = f.one_year_annual_revenue_growth_rate or 0.0
    debt_eq = f.total_debt_to_equity_mrq or 0.0
    beta = f.beta or 0.0

    if not any((pe, pb, roe, div_yield, rev_growth)):
        note = (
            "Фундаментальный скоринг неприменим к ETF"
            if instrument_type == "etf"
            else "Фундаментальные данные отсутствуют"
        )
        return Recommendation(
            ticker=ticker,
            name=name,
            instrument_type=instrument_type,
            action="Нет данных",
            score=0,
            reasons=[note],
            forward_dividend_income=forward_dividend or Decimal("0"),
        )

    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    # --- P/E ---
    if pe > 0:
        if pe <= 10:
            score += 25
            reasons.append(f"P/E {pe:.1f} — недорого")
        elif pe <= 18:
            score += 18
            reasons.append(f"P/E {pe:.1f} — адекватно")
        elif pe <= 30:
            score += 8
            reasons.append(f"P/E {pe:.1f} — дорого")
        else:
            reasons.append(f"P/E {pe:.1f} — очень дорого")
    elif pe < 0:
        score -= 15
        risks.append("Убыточная компания (P/E < 0)")

    # --- Див. доходность ---
    if div_yield >= 8:
        score += 25
        reasons.append(f"Див. доходность {div_yield:.1f}% — отличная")
    elif div_yield >= 5:
        score += 20
        reasons.append(f"Див. доходность {div_yield:.1f}% — хорошая")
    elif div_yield >= 3:
        score += 12
        reasons.append(f"Див. доходность {div_yield:.1f}%")
    elif div_yield > 0:
        score += 4
        reasons.append(f"Див. доходность {div_yield:.1f}% — невысокая")
    else:
        reasons.append("Дивиденды не начисляются")

    # --- ROE ---
    if roe >= 15:
        score += 15
        reasons.append(f"ROE {roe:.1f}% — отличная")
    elif roe >= 8:
        score += 8
        reasons.append(f"ROE {roe:.1f}% — нормальная")
    elif roe > 0:
        score += 2
        reasons.append(f"ROE {roe:.1f}% — слабая")
    elif roe < 0:
        score -= 15
        risks.append(f"ROE {roe:.1f}% — отрицательная")

    # --- Выручка Y/Y ---
    if rev_growth > 10:
        score += 10
        reasons.append(f"Выручка +{rev_growth:.1f}% за год — растёт")
    elif rev_growth > 0:
        score += 6
        reasons.append(f"Выручка +{rev_growth:.1f}% за год")
    elif rev_growth > -5:
        if rev_growth < 0:
            score -= 5
            reasons.append(f"Выручка {rev_growth:.1f}% за год — снижается")
    else:
        score -= 12
        risks.append(f"Выручка {rev_growth:.1f}% за год — резко падает")

    # --- Payout ---
    if payout > 0:
        if 30 <= payout <= 80:
            score += 10
            reasons.append(f"Payout {payout:.0f}% — устойчивый")
        elif 0 < payout < 30:
            score += 5
            reasons.append(f"Payout {payout:.0f}% — консервативный")
        elif payout > 100:
            score -= 10
            risks.append(f"Payout {payout:.0f}% — выплачивают больше, чем заработали")
        else:
            score += 3
            reasons.append(f"Payout {payout:.0f}% — высокий")

    # --- Долг/Собственный капитал ---
    if debt_eq > 0:
        if debt_eq <= 50:
            score += 5
            reasons.append(f"Долг/капитал {debt_eq:.0f}% — низкий")
        elif debt_eq <= 150:
            score += 2
        elif debt_eq <= 300:
            score -= 3
            risks.append(f"Долг/капитал {debt_eq:.0f}% — высокий")
        else:
            score -= 10
            risks.append(f"Долг/капитал {debt_eq:.0f}% — критический")

    # --- Стресс-режим: дивиденды важнее роста, тилт по сектору и бете ---
    if stress:
        if div_yield >= 5:
            score += 10
            reasons.append(f"Стресс: див. доходность {div_yield:.1f}% — «деньги сейчас»")
        elif div_yield >= 3:
            score += 5
            reasons.append(f"Стресс: див. доходность {div_yield:.1f}% — плюс")
        tilt = sector_stress_tilt(sector)
        if tilt > 0:
            score += tilt
            reasons.append("Стресс: защитный сектор")
        elif tilt < 0:
            score += tilt
            risks.append("Стресс: циклический сектор")
        if beta > 0:
            if beta <= 0.9:
                score += 5
                reasons.append(f"Стресс: низкая бета (β {beta:.2f})")
            elif beta >= 1.5:
                score -= 5
                risks.append(f"Стресс: высокая бета (β {beta:.2f})")

    score = max(0, min(score, 100))

    if score >= 65:
        action = "Покупать"
    elif score >= 40:
        action = "Держать"
    else:
        action = "Продавать"

    return Recommendation(
        ticker=ticker,
        name=name,
        instrument_type=instrument_type,
        action=action,
        score=score,
        reasons=reasons,
        risks=risks,
        forward_dividend_income=forward_dividend or Decimal("0"),
        pe=pe or None,
        pb=pb or None,
        roe=roe or None,
        div_yield=div_yield or None,
        payout=payout or None,
        rev_growth=rev_growth or None,
        debt_eq=debt_eq or None,
        beta=beta or None,
    )


def _build_recommendation(p: Position, f) -> Recommendation:
    return score_fundamentals(
        ticker=p.ticker,
        name=p.name,
        instrument_type=p.instrument_type,
        f=f,
        forward_dividend=_div_income(p),
    )


def _div_income(p: Position) -> Decimal:
    """Прогноз годового дивидендного дохода: выплаты за последние 365 дней × количество."""
    if p.dividend_amount > 0:
        return (p.dividend_amount * p.quantity).quantize(Decimal("0.01"))
    return Decimal("0")
