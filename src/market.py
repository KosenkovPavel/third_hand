"""Мнение рынка: консенсус аналитиков, сделки инсайдеров, технический RSI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from .money import to_decimal, to_ratio
from .portfolio import PortfolioSnapshot, Position
from .providers.tinkoff import TinkoffClient

INSIDER_WINDOW_DAYS = 365

RECOMMENDATION_NAMES = {
    1: "Покупать",
    2: "Держать",
    3: "Продавать",
    None: "—",
}


@dataclass(frozen=True)
class AnalystView:
    ticker: str
    name: str
    recommendation: str
    current_price: Decimal | None
    target_price: Decimal | None
    min_target: Decimal | None
    max_target: Decimal | None
    change_rel_pct: Decimal | None
    n_targets: int


@dataclass(frozen=True)
class InsiderView:
    ticker: str
    investor_name: str
    position: str
    direction: str  # Покупка / Продажа
    quantity: int
    price: Decimal | None
    disclosure_date: datetime | None


@dataclass(frozen=True)
class TechView:
    ticker: str
    rsi: float | None


@dataclass
class MarketOpinion:
    analysts: list[AnalystView] = field(default_factory=list)
    insiders: list[InsiderView] = field(default_factory=list)
    tech: list[TechView] = field(default_factory=list)


def build_market_opinion(
    client: TinkoffClient,
    snapshot: PortfolioSnapshot,
    issues: list[str] | None = None,
) -> MarketOpinion:
    """Собирает консенсус аналитиков, сделки инсайдеров и RSI по акциям/ETF."""
    targets = [p for p in snapshot.positions if p.instrument_type in ("share", "etf") and p.instrument_uid]
    opinion = MarketOpinion()

    for p in targets:
        analyst = _analyst_view(client, p, issues)
        if analyst:
            opinion.analysts.append(analyst)

        opinion.insiders.extend(_insider_views(client, p, issues))

        opinion.tech.append(
            TechView(ticker=p.ticker, rsi=_rsi(client, p, issues))
        )

    opinion.insiders.sort(key=lambda e: e.disclosure_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return opinion


def _analyst_view(
    client: TinkoffClient, p: Position, issues: list[str] | None = None
) -> AnalystView | None:
    try:
        resp = client.get_forecast_by(p.instrument_uid)
    except Exception as exc:
        if issues is not None:
            issues.append(f"{p.ticker}: консенсус аналитиков не загружен ({exc})")
        return None
    c = resp.consensus
    if c is None or not c.ticker:
        return None
    return AnalystView(
        ticker=p.ticker,
        name=p.name,
        recommendation=RECOMMENDATION_NAMES.get(int(c.recommendation), "—"),
        current_price=to_decimal(c.current_price) if _quotation_set(c.current_price) else None,
        target_price=to_decimal(c.consensus) if _quotation_set(c.consensus) else None,
        min_target=to_decimal(c.min_target) if _quotation_set(c.min_target) else None,
        max_target=to_decimal(c.max_target) if _quotation_set(c.max_target) else None,
        change_rel_pct=consensus_change_pct(c),
        n_targets=len(resp.targets),
    )


def consensus_change_pct(consensus) -> Decimal | None:
    """Потенциал к консенсусу в процентах; price_change_rel API уже приходит в %."""
    current = (
        to_decimal(consensus.current_price)
        if _quotation_set(consensus.current_price)
        else None
    )
    target = (
        to_decimal(consensus.consensus)
        if _quotation_set(consensus.consensus)
        else None
    )
    if current is not None and target is not None and current > 0:
        return ((target / current - 1) * 100).quantize(Decimal("0.01"))
    if _quotation_set(consensus.price_change_rel):
        return to_ratio(consensus.price_change_rel)
    return None


def _quotation_set(q) -> bool:
    return q is not None and bool(q.units or q.nano)


def _insider_views(
    client: TinkoffClient, p: Position, issues: list[str] | None = None
) -> list[InsiderView]:
    try:
        resp = client.get_insider_deals(p.instrument_uid, limit=10)
    except Exception as exc:
        if issues is not None:
            issues.append(f"{p.ticker}: сделки инсайдеров не загружены ({exc})")
        return []
    now = datetime.now(timezone.utc)
    out: list[InsiderView] = []
    for d in resp.insider_deals:
        if d.disclosure_date and now - d.disclosure_date > timedelta(days=INSIDER_WINDOW_DAYS):
            continue
        direction = "Покупка" if int(d.direction) == 1 else ("Продажа" if int(d.direction) == 2 else "—")
        out.append(
            InsiderView(
                ticker=d.ticker or p.ticker,
                investor_name=d.investor_name or "—",
                position=d.investor_position or "—",
                direction=direction,
                quantity=d.quantity,
                price=to_decimal(d.price) if d.price else None,
                disclosure_date=d.disclosure_date,
            )
        )
    return out


def _rsi(
    client: TinkoffClient, p: Position, issues: list[str] | None = None
) -> float | None:
    try:
        return client.get_rsi(p.instrument_uid)
    except Exception as exc:
        if issues is not None:
            issues.append(f"{p.ticker}: RSI не загружен ({exc})")
        return None