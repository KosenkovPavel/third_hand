"""Журнал рекомендаций и сравнение их результата с IMOEX через 3/6/12 месяцев."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .analytics import Recommendation
from .backup import backup_file
from .config import DATA_DIR
from .money import to_decimal
from .portfolio import PortfolioSnapshot

HORIZON_DAYS = (90, 180, 365)


@dataclass(frozen=True)
class JournalEntry:
    date: str
    ticker: str
    instrument_uid: str
    price: Decimal
    score: int
    action: str


@dataclass(frozen=True)
class RecommendationOutcome:
    ticker: str
    recommendation_date: str
    evaluation_date: str
    horizon_days: int
    action: str
    score: int
    return_pct: Decimal
    benchmark_pct: Decimal
    excess_pct: Decimal


def _default_path() -> Path:
    return DATA_DIR / "recommendations_history.json"


def load_recommendations(path: Path | None = None) -> list[JournalEntry]:
    src = path or _default_path()
    if not src.exists():
        return []
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        return [
            JournalEntry(
                date=str(item["date"]),
                ticker=str(item["ticker"]),
                instrument_uid=str(item["instrument_uid"]),
                price=Decimal(str(item["price"])),
                score=int(item["score"]),
                action=str(item["action"]),
            )
            for item in raw
        ]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return []


def save_recommendations(
    recommendations: list[Recommendation],
    snapshot: PortfolioSnapshot,
    path: Path | None = None,
) -> list[JournalEntry]:
    """Сохраняет по одной записи на тикер в день, обновляя повторный запуск."""
    dest = path or _default_path()
    positions = {
        p.ticker: p
        for p in snapshot.positions
        if p.instrument_type in ("share", "etf") and p.instrument_uid and p.current_price > 0
    }
    day = snapshot.collected_at[:10] or date.today().isoformat()
    new_entries = [
        JournalEntry(
            date=day,
            ticker=rec.ticker,
            instrument_uid=positions[rec.ticker].instrument_uid,
            price=positions[rec.ticker].current_price,
            score=rec.score,
            action=rec.action,
        )
        for rec in recommendations
        if rec.ticker in positions
    ]
    replaced = {(entry.date, entry.ticker) for entry in new_entries}
    entries = [
        entry
        for entry in load_recommendations(dest)
        if (entry.date, entry.ticker) not in replaced
    ]
    entries.extend(new_entries)
    entries.sort(key=lambda entry: (entry.date, entry.ticker))
    backup_file(dest)
    dest.write_text(
        json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return new_entries


def evaluate_recommendations(
    client,
    entries: list[JournalEntry],
    today: date | None = None,
    issues: list[str] | None = None,
) -> list[RecommendationOutcome]:
    """Считает ценовую доходность рекомендаций и IMOEX к каждому созревшему горизонту."""
    today = today or datetime.now(timezone.utc).date()
    matured = [
        (entry, horizon, date.fromisoformat(entry.date) + timedelta(days=horizon))
        for entry in entries
        for horizon in HORIZON_DAYS
        if date.fromisoformat(entry.date) + timedelta(days=horizon) <= today
    ]
    if not matured:
        return []

    try:
        imoex_uid = client.find_instrument_uid("IMOEX", "IMOEX")
    except Exception as exc:
        if issues is not None:
            issues.append(f"Журнал рекомендаций: IMOEX недоступен ({exc})")
        return []
    if not imoex_uid:
        if issues is not None:
            issues.append("Журнал рекомендаций: IMOEX не найден")
        return []
    day_interval = client.daily_interval()

    close_cache: dict[tuple[str, str, str], Decimal | None] = {}

    def close_at(uid: str, target: date, side: str) -> Decimal | None:
        key = (uid, target.isoformat(), side)
        if key in close_cache:
            return close_cache[key]
        end = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
        start = end if side == "first" else end - timedelta(days=10)
        stop = end + timedelta(days=10 if side == "first" else 1)
        try:
            candles = client.get_candles(uid, start, stop, day_interval).candles
            value = to_decimal(candles[0 if side == "first" else -1].close) if candles else None
        except Exception:
            value = None
        close_cache[key] = value
        return value

    outcomes = []
    failed: set[str] = set()
    for entry, horizon, evaluation_date in matured:
        asset_close = close_at(entry.instrument_uid, evaluation_date, "last")
        benchmark_start = close_at(imoex_uid, date.fromisoformat(entry.date), "first")
        benchmark_end = close_at(imoex_uid, evaluation_date, "last")
        if (
            asset_close is None
            or benchmark_start is None
            or benchmark_end is None
            or entry.price <= 0
            or benchmark_start <= 0
        ):
            if issues is not None and entry.ticker not in failed:
                issues.append(f"{entry.ticker}: результат рекомендации пока недоступен")
                failed.add(entry.ticker)
            continue
        asset_return = (asset_close / entry.price - 1) * 100
        benchmark_return = (benchmark_end / benchmark_start - 1) * 100
        outcomes.append(
            RecommendationOutcome(
                ticker=entry.ticker,
                recommendation_date=entry.date,
                evaluation_date=evaluation_date.isoformat(),
                horizon_days=horizon,
                action=entry.action,
                score=entry.score,
                return_pct=asset_return.quantize(Decimal("0.01")),
                benchmark_pct=benchmark_return.quantize(Decimal("0.01")),
                excess_pct=(asset_return - benchmark_return).quantize(Decimal("0.01")),
            )
        )
    outcomes.sort(key=lambda outcome: (outcome.evaluation_date, outcome.ticker), reverse=True)
    return outcomes
