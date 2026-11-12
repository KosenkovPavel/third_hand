"""Screener: поиск акций по фундаментальным фильтрам (P/E, див. доходность и т.д.).

Универсум — RUB-акции MOEX (по умолчанию до 40 шт.) или пользовательский
список тикеров через ``tickers``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .risk import SECTOR_NAMES_RU
from .providers.tinkoff import TinkoffClient


@dataclass(frozen=True)
class ScreenFilters:
    pe_max: float | None = None
    div_min: float | None = None
    roe_min: float | None = None
    mcap_min_bln: float | None = None
    debt_max: float | None = None


@dataclass
class ScreenRow:
    ticker: str
    name: str
    sector: str
    pe: float | None
    pb: float | None
    roe: float | None
    div_yield: float | None
    rev_growth: float | None
    debt_eq: float | None
    mcap_bln: float | None
    passed: bool
    fails: list[str] = field(default_factory=list)


def screen(
    client: TinkoffClient,
    filters: ScreenFilters,
    tickers: list[str] | None = None,
    max_items: int = 40,
) -> list[ScreenRow]:
    """Скрининг акций по фильтрам. Возвращает до 20 прошедших (или ближайших)."""
    shares_resp = client.get_shares()
    by_ticker = {s.ticker: s for s in shares_resp.instruments if s.ticker}

    if tickers:
        candidates = [by_ticker[t.upper()] for t in tickers if t.upper() in by_ticker]
        uids = [s.asset_uid for s in candidates if s.asset_uid]
        fund_by_uid = _fetch_fundamentals(client, uids)
    else:
        candidates = [
            s
            for s in shares_resp.instruments
            if s.currency == "rub" and s.buy_available_flag and s.country_of_risk == "RU" and s.asset_uid
        ]
        # предфильтр по размеру выпуска, финальный отбор — по капитализации
        candidates.sort(key=lambda s: s.issue_size or 0, reverse=True)
        candidates = candidates[: max_items * 2]
        fund_by_uid = _fetch_fundamentals(client, [s.asset_uid for s in candidates])
        candidates = _top_by_mcap(candidates, fund_by_uid, max_items)

    rows = [_to_row(s, fund_by_uid.get(s.asset_uid), filters) for s in candidates]
    return _passed_rows(rows)


def _top_by_mcap(candidates: list, fund_by_uid: dict, max_items: int) -> list:
    """Топ по капитализации из уже загруженной фундаменталки."""
    return sorted(
        candidates,
        key=lambda s: getattr(fund_by_uid.get(s.asset_uid), "market_capitalization", 0) or 0,
        reverse=True,
    )[:max_items]


def _passed_rows(rows: list[ScreenRow], limit: int = 20) -> list[ScreenRow]:
    """Только прошедшие фильтры, по убыванию див. доходности. Пусто → []."""
    passed = [r for r in rows if r.passed]
    passed.sort(key=_row_sort_key)
    return passed[:limit]


def _row_sort_key(r: ScreenRow) -> tuple:
    """Сортировка выдачи: по див. доходности (убыв.), None — в конец, затем тикер."""
    return (-(r.div_yield or 0), r.ticker)


def _fetch_fundamentals(client: TinkoffClient, uids: list[str]) -> dict[str, object]:
    fund_by_uid: dict[str, object] = {}
    if not uids:
        return fund_by_uid
    try:
        resp = client.get_asset_fundamentals(uids)
        for f in resp.fundamentals:
            fund_by_uid[f.asset_uid] = f
    except Exception:
        for uid in uids:
            try:
                r = client.get_asset_fundamentals([uid])
                for f in r.fundamentals:
                    fund_by_uid[f.asset_uid] = f
            except Exception:
                continue
    return fund_by_uid


def _to_row(s, f, filters: ScreenFilters) -> ScreenRow:
    pe = getattr(f, "pe_ratio_ttm", None) if f else None
    pb = getattr(f, "price_to_book_ttm", None) if f else None
    roe = getattr(f, "roe", None) if f else None
    div = getattr(f, "dividend_yield_daily_ttm", None) if f else None
    rev = getattr(f, "one_year_annual_revenue_growth_rate", None) if f else None
    debt = getattr(f, "total_debt_to_equity_mrq", None) if f else None
    cap = getattr(f, "market_capitalization", None) if f else None
    mcap_bln = round(cap / 1e9, 1) if cap else None

    fails: list[str] = []
    if filters.pe_max is not None and pe is not None and pe > filters.pe_max:
        fails.append(f"P/E {pe:.1f} > {filters.pe_max}")
    if filters.div_min is not None and div is not None and div < filters.div_min:
        fails.append(f"Див. {div:.1f}% < {filters.div_min}")
    if filters.roe_min is not None and roe is not None and roe < filters.roe_min:
        fails.append(f"ROE {roe:.1f}% < {filters.roe_min}")
    if filters.mcap_min_bln is not None and mcap_bln is not None and mcap_bln < filters.mcap_min_bln:
        fails.append(f"Кап ₽{mcap_bln:.0f} млрд < {filters.mcap_min_bln}")
    if filters.debt_max is not None and debt is not None and debt > filters.debt_max:
        fails.append(f"Долг/кап {debt:.0f}% > {filters.debt_max}")

    return ScreenRow(
        ticker=s.ticker,
        name=s.name,
        sector=SECTOR_NAMES_RU.get(s.sector or "", s.sector or "—"),
        pe=pe,
        pb=pb,
        roe=roe,
        div_yield=div,
        rev_growth=rev,
        debt_eq=debt,
        mcap_bln=mcap_bln,
        passed=not fails and bool(pe or div),
        fails=fails,
    )