"""Подбор акций к покупке: двухфазный скрининг с бленд-рейтингом.

Фаза 1 (дёшево): универсум RUB-акций MOEX, топ по капитализации,
один батч фундаментальных данных -> фундаментальная оценка 0-100.
Фаза 2 (точечно): для короткого списка - консенсус аналитиков и RSI.

Бленд-рейтинг: 50% фундамент + 30% консенсус + 20% RSI.
Вес отсутствующих компонент перераспределяется на доступные.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .analytics import Recommendation, score_fundamentals, sector_stress_tilt
from .market import consensus_change_pct
from .risk import SECTOR_NAMES_RU
from .screener import _fetch_fundamentals
from .providers.tinkoff import TinkoffClient

WEIGHTS = {"fund": 50, "consensus": 30, "rsi": 20}


@dataclass(frozen=True)
class PickFilters:
    score_min: float | None = 65
    pe_max: float | None = None
    div_min: float | None = None
    blend: bool = True
    top_n: int = 15
    max_items: int = 40


@dataclass
class Pick:
    ticker: str
    name: str
    sector: str
    fund_score: int
    action: str
    blend_score: int | None
    potential_pct: Decimal | None
    rsi: float | None
    pe: float | None
    div_yield: float | None
    roe: float | None
    payout: float | None
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    in_portfolio: bool = False


# бонус докупки при сортировке: в пределах ±2 пунктов рейтинга докупка выше новой
ADDON_SORT_BONUS = 2


def apply_portfolio_context(
    picks: list[Pick],
    portfolio_tickers: set[str] | None,
    portfolio_total: Decimal | None,
    budget: Decimal | None,
    min_position_pct: Decimal,
) -> list[Pick]:
    """Метки «докупка»/«новая» и сортировка с приоритетом докупок (±2 рейтинга).

    ``budget`` — типичный бюджет покупки; если новая позиция на него окажется
    меньше min_position_pct портфеля — предупреждаем в заметках.
    """
    if portfolio_tickers is None:
        return picks
    min_amount = (
        (portfolio_total * min_position_pct / 100).quantize(Decimal("1"))
        if portfolio_total
        else None
    )
    for p in picks:
        p.in_portfolio = p.ticker in portfolio_tickers
        if p.in_portfolio:
            p.notes.append("докупка")
        else:
            if budget and min_amount and budget < min_amount:
                p.notes.append(
                    f"новая: бюджет ниже мин. позиции (~{min_amount:.0f} ₽) — "
                    "лучше докупить имеющееся"
                )
            elif min_amount:
                p.notes.append(f"новая (мин. позиция ~{min_amount:.0f} ₽)")
    picks.sort(
        key=lambda p: (
            p.blend_score is not None,
            (p.blend_score if p.blend_score is not None else p.fund_score)
            + (ADDON_SORT_BONUS if p.in_portfolio else 0),
        ),
        reverse=True,
    )
    return picks


def pick_stocks(
    client: TinkoffClient,
    filters: PickFilters,
    stress: bool = False,
    portfolio_tickers: set[str] | None = None,
    portfolio_total: Decimal | None = None,
    budget: Decimal | None = None,
    min_position_pct: Decimal = Decimal("5"),
) -> list[Pick]:
    """Двухфазный подбор акций к покупке, отсортированный по рейтингу.

    ``stress=True`` — защитный режим: тилт в защитные сектора и дивиденды.
    ``portfolio_tickers`` — тикеры акций уже в портфеле: метки «докупка»/«новая»
    и приоритет докупок при близком рейтинге.
    """
    shares_resp = client.get_shares()
    candidates = [
        s
        for s in shares_resp.instruments
        if s.currency == "rub"
        and s.buy_available_flag
        and s.country_of_risk == "RU"
        and s.asset_uid
    ]
    candidates.sort(key=lambda s: s.issue_size or 0, reverse=True)
    candidates = candidates[: filters.max_items * 2]

    fund_by_uid = _fetch_fundamentals(client, [s.asset_uid for s in candidates])
    candidates.sort(
        key=lambda s: getattr(fund_by_uid.get(s.asset_uid), "market_capitalization", 0) or 0,
        reverse=True,
    )
    candidates = candidates[: filters.max_items]

    scored: list[tuple] = []
    for s in candidates:
        f = fund_by_uid.get(s.asset_uid)
        if f is None:
            continue
        rec = score_fundamentals(
            ticker=s.ticker,
            name=s.name,
            instrument_type="share",
            f=f,
            sector=s.sector,
            stress=stress,
        )
        if filters.score_min is not None and rec.score < filters.score_min:
            continue
        if filters.pe_max is not None and rec.pe is not None and rec.pe > filters.pe_max:
            continue
        if filters.div_min is not None and rec.div_yield is not None and rec.div_yield < filters.div_min:
            continue
        scored.append((s, rec))

    scored.sort(key=lambda x: x[1].score, reverse=True)
    short = scored[: filters.top_n]

    picks: list[Pick] = []
    for s, rec in short:
        potential, rsi = _market_inputs(client, s)
        notes: list[str] = []
        if stress:
            tilt = sector_stress_tilt(s.sector)
            if tilt > 0:
                notes.append("защитный сектор")
            elif tilt < 0:
                notes.append("циклический сектор")
        blend_score = None
        if filters.blend:
            blend_score, blend_notes = _blend(rec.score, potential, rsi)
            notes.extend(blend_notes)
        picks.append(
            Pick(
                ticker=s.ticker,
                name=s.name,
                sector=SECTOR_NAMES_RU.get(s.sector or "", s.sector or "—"),
                fund_score=rec.score,
                action=rec.action,
                blend_score=blend_score,
                potential_pct=potential.quantize(Decimal("0.1")) if potential is not None else None,
                rsi=rsi,
                pe=rec.pe,
                div_yield=rec.div_yield,
                roe=rec.roe,
                payout=rec.payout,
                reasons=rec.reasons,
                risks=rec.risks,
                notes=notes,
            )
        )

    picks.sort(
        key=lambda p: (
            p.blend_score is not None,
            p.blend_score if p.blend_score is not None else p.fund_score,
        ),
        reverse=True,
    )
    return apply_portfolio_context(
        picks, portfolio_tickers, portfolio_total, budget, min_position_pct
    )


def _market_inputs(client: TinkoffClient, s):
    """Потенциал к цели аналитиков (%) и RSI для одной бумаги."""
    potential = None
    try:
        resp = client.get_forecast_by(s.uid)
        c = resp.consensus
        if c is not None and c.ticker:
            potential = consensus_change_pct(c)
    except Exception:
        pass

    rsi = None
    try:
        rsi = client.get_rsi(s.uid)
    except Exception:
        pass

    return potential, rsi


def _score_from_potential(potential: Decimal) -> float:
    """Потенциал к цели: +25% -> 100, 0% -> 50, -20% -> 10."""
    value = 50.0 + float(potential) * 2.0
    return max(0.0, min(100.0, value))


def _score_from_rsi(rsi: float) -> float:
    """RSI 14: зона 40-55 комфортна, >65 перегрев, <30 ловить нож не поощряем."""
    if rsi < 30:
        return 70.0
    if rsi < 40:
        return 90.0
    if rsi <= 55:
        return 100.0
    if rsi <= 65:
        return 90.0
    if rsi <= 70:
        return 60.0
    if rsi <= 80:
        return 30.0
    return 10.0


def _blend(fund_score, potential, rsi) -> tuple[int, list[str]]:
    parts: list[tuple[float, float]] = [(WEIGHTS["fund"], float(fund_score))]
    notes: list[str] = []
    if potential is not None:
        parts.append((WEIGHTS["consensus"], _score_from_potential(potential)))
    else:
        notes.append("нет консенсуса")
    if rsi is not None:
        parts.append((WEIGHTS["rsi"], _score_from_rsi(rsi)))
    else:
        notes.append("нет RSI")

    wsum = sum(w for w, _ in parts)
    value = sum(w * s for w, s in parts) / wsum
    return round(value), notes