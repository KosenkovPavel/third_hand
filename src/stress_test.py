"""Стресс-тест: повтор исторических кризисов на текущем портфеле.

Проигрывает окна кризисов (2008, 2014, 2020, 2022) на текущих позициях через
дневные свечи; если по инструменту нет данных — fallback на индекс класса
(Акции→IMOEX, Облигации→RGBI), рублевая позиция считается нулевой. Для
сравнения — таргет-стратегия 50/30/20 по тем же окнам.

Считается по ценам, БЕЗ купонов и дивидендов — иллюстрация устойчивости,
не прогноз. Для золотой ноги таргет-стратегии в окнах до появления TGLD
используется учётная цена золота ЦБ (research/data/raw/GOLD_RUB_G.csv —
публичные данные, закоммичены в репозиторий).
"""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .money import to_decimal
from .portfolio import PortfolioSnapshot

_GOLD_CSV = Path(__file__).resolve().parent.parent / "research" / "data" / "raw" / "GOLD_RUB_G.csv"
_LEVELS_CSV = Path(__file__).resolve().parent.parent / "research" / "data" / "monthly_levels.csv"


def _gold_cbr_return(start: datetime, end: datetime) -> Decimal | None:
    """Доходность золота за окно по учётным ценам ЦБ (fallback для окон,
    где золотых ETF ещё не существовало)."""
    if not _GOLD_CSV.exists():
        return None
    dates, values = [], []
    with _GOLD_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = datetime.fromisoformat(row[""].split()[0]).date()
            if start.date() <= d <= end.date():
                dates.append(d)
                values.append(Decimal(row["GOLD_RUB_G"]))
    if len(values) < 2:
        return None
    return ((values[-1] / values[0] - 1) * 100).quantize(Decimal("0.01"))


def _research_monthly_return(column: str, start: datetime, end: datetime) -> Decimal | None:
    """Доходность за окно по месячным уровням из research-данных (fallback,
    когда API не отдаёт старую историю свечей, напр. RGBI до ~2010-х)."""
    if not _LEVELS_CSV.exists():
        return None
    values = []
    with _LEVELS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row.get(column) or ""
            if not raw:
                continue
            d = datetime.fromisoformat(row[""].split()[0]).date()
            if start.date() <= d <= end.date():
                values.append(Decimal(raw))
    if len(values) < 2:
        return None
    return ((values[-1] / values[0] - 1) * 100).quantize(Decimal("0.01"))

CRISES: dict[str, tuple[str, str]] = {
    "2008–09": ("2008-05-01", "2009-06-30"),
    "2014–15": ("2014-06-01", "2015-03-31"),
    "2020": ("2020-01-01", "2020-06-30"),
    "2022": ("2022-01-01", "2022-12-31"),
}

# fallback-индексы классов при отсутствии свечей по инструменту
CLASS_FALLBACK_INDEX = {"Акции": "IMOEX", "Облигации": "RGBI"}


@dataclass(frozen=True)
class PositionReplay:
    ticker: str
    weight_pct: Decimal
    return_pct: Decimal | None  # None — нет данных по окну


@dataclass(frozen=True)
class CrisisReplay:
    name: str
    portfolio_return_pct: Decimal | None
    target_return_pct: Decimal | None
    covered_pct: Decimal
    replays: list[PositionReplay]


def _candles(client, uid: str, start: datetime, end: datetime) -> list:
    day = client.daily_interval()
    out = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=350), end)
        out.extend(client.get_candles(uid, cur, nxt, day).candles)
        cur = nxt
    return out


def _window_return(client, uid: str, start: datetime, end: datetime) -> Decimal | None:
    try:
        candles = _candles(client, uid, start, end)
    except Exception:  # noqa: BLE001
        return None
    closes = [c.close for c in candles if c.close is not None]
    if len(closes) < 2:
        return None
    first, last = to_decimal(closes[0]), to_decimal(closes[-1])
    if first <= 0:
        return None
    return (last / first - 1) * 100


def _index_uid_cache(client) -> dict[str, str | None]:
    cache: dict[str, str | None] = {}

    def get(name: str) -> str | None:
        if name not in cache:
            try:
                cache[name] = client.find_instrument_uid(name, name)
            except Exception:  # noqa: BLE001
                cache[name] = None
        return cache[name]

    return get


def _target_return(client, start: datetime, end: datetime,
                   index_uid) -> Decimal | None:
    """Реплей стратегии 50/30/20: IMOEX / RGBI / золото (TGLD)."""
    parts: list[tuple[Decimal, Decimal | None]] = []
    # fallback на research-ряды, если API не отдаёт старую историю свечей
    fallback_col = {"IMOEX": "MCFTR", "RGBI": "RGBITR", "TGLD": None}
    for weight, name in ((Decimal("50"), "IMOEX"), (Decimal("30"), "RGBI"),
                       (Decimal("20"), "TGLD")):
        uid = index_uid(name)
        ret = _window_return(client, uid, start, end) if uid else None
        if ret is None and name == "TGLD":
            ret = _gold_cbr_return(start, end)  # ETF не существовал → цена ЦБ
        if ret is None and fallback_col[name]:
            ret = _research_monthly_return(fallback_col[name], start, end)
        if ret is None:
            return None  # честно: без полного покрытия цель не считаем
        parts.append((weight, ret))
    total = sum((w * r for w, r in parts), Decimal("0")) / 100
    return total.quantize(Decimal("0.1"))


def _return_with_fallback(client, position, start, end, index_uid) -> Decimal | None:
    if position.instrument_type == "currency":
        return Decimal("0")
    ret = _window_return(client, position.instrument_uid, start, end)
    if ret is not None:
        return ret
    idx = CLASS_FALLBACK_INDEX.get(position.type_name)
    if idx:
        uid = index_uid(idx)
        if uid:
            return _window_return(client, uid, start, end)
    return None


def replay_portfolio(client, snapshot: PortfolioSnapshot,
                     issues: list[str] | None = None) -> list[CrisisReplay]:
    """Проигрывает все окна CRISES на позициях снапшота (веса — текущие)."""
    total = snapshot.total
    if not total or total <= 0:
        return []
    index_uid = _index_uid_cache(client)
    work = [(p, (p.amount / total * 100).quantize(Decimal("0.01")))
            for p in snapshot.positions if p.amount > 0]

    results: list[CrisisReplay] = []
    for name, (a, b) in CRISES.items():
        start = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(b).replace(tzinfo=timezone.utc)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                (p, w, pool.submit(_return_with_fallback, client, p, start, end, index_uid))
                for p, w in work
            ]
            replays = []
            for p, w, fut in futures:
                try:
                    ret = fut.result()
                except Exception as exc:  # noqa: BLE001
                    if issues is not None:
                        issues.append(f"{p.ticker}: стресс-тест недоступен ({exc})")
                    ret = None
                replays.append(PositionReplay(p.ticker, w, ret))
        covered = sum((r.weight_pct for r in replays if r.return_pct is not None),
                      Decimal("0"))
        port_ret = None
        if covered > 0:
            port_ret = (sum(
                (r.return_pct * r.weight_pct for r in replays if r.return_pct is not None),
                Decimal("0"),
            ) / covered).quantize(Decimal("0.1"))
        replays.sort(key=lambda r: (r.return_pct is None, r.return_pct or Decimal("0")))
        results.append(CrisisReplay(
            name=name,
            portfolio_return_pct=port_ret,
            target_return_pct=_target_return(client, start, end, index_uid),
            covered_pct=covered.quantize(Decimal("0.1")),
            replays=replays,
        ))
    return results
