"""Новостной фон: событийные дельты от брокера + опционально внешние RSS.

Источники событий (работают без внешней сети):
    - изменение целевой цены аналитиков между запусками (data/targets_history.json);
    - сделки инсайдеров;
    - RSI-перегрев/перепроданность;
    - ближайшие дивиденды/купоны из календаря.

Опционально: RSS-лента Google News по названию компании (без API-ключа,
std-lib urllib). Если сеть недоступна — блок просто пропускается.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from .market import _analyst_view, _insider_views, _rsi
from .portfolio import PortfolioSnapshot
from .providers.tinkoff import TinkoffClient

TARGET_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "targets_history.json"

TARGET_CHANGE_MIN_PCT = Decimal("1")
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
INSIDER_WINDOW_DAYS = 90
RSS_WINDOW_DAYS = 30
RSS_TIMEOUT = 5
RSS_MAX_TITLES = 2

SENTIMENT_POS = (
    "рост", "выросл", "увелич", "повышен", "рекорд", "прибыль", "успех",
    "укреп", "хорош", "дивид", "рекоменд", "покупк", "выигр", "благоприят",
    "сильн", "положит", "лучше",
)
SENTIMENT_NEG = (
    "падение", "упал", "снизил", "убыток", "кризис", "штраф", "судеб",
    "санкц", "отзыв", "проблем", "увольн", "сократ", "негатив", "риск",
    "обвал", "продаж", "потерял", "дефолт", "высокорисков",
)


@dataclass
class NewsEvent:
    kind: str  # Цель / Инсайдер / RSI / Отсечка / RSS
    ticker: str
    text: str
    ts: datetime | None = None
    tone: str | None = None  # позитив / негатив / нейтр
    alert: bool = False


def build_news_background(
    client: TinkoffClient,
    snapshot: PortfolioSnapshot | None,
    extra_tickers: list[str] | None = None,
    calendar=None,
    with_rss: bool = True,
) -> tuple[list[NewsEvent], bool]:
    """Собирает события по портфелю (+доп. тикеры) и обновляет историю целей.

    Возвращает (события, rss_ok).
    """
    now = datetime.now(timezone.utc)
    positions = list(snapshot.positions) if snapshot else []
    tickers = [p.ticker for p in positions]
    for t in (extra_tickers or []):
        if t and t not in tickers:
            tickers.append(t)

    events: list[NewsEvent] = []
    targets = _load_targets()

    for ticker in tickers:
        pos = next((p for p in positions if p.ticker == ticker), None)
        if pos and pos.instrument_type in ("share", "etf"):
            prev = targets.get(ticker)
            cur = _current_target(client, pos, now)
            if cur is not None:
                targets[ticker] = cur
            events.extend(_target_delta_events(prev, cur, ticker, now))
            events.extend(_insider_events(client, pos, ticker, now))
            events.extend(_rsi_events(client, pos, ticker))
        if calendar is not None:
            ev = _payment_event(calendar, ticker, now)
            if ev:
                events.append(ev)

    _save_targets(targets)

    rss_ok = False
    if with_rss:
        rss_events, rss_ok = _rss_events(positions, tickers)
        events.extend(rss_events)

    events.sort(key=lambda e: e.ts or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return events, rss_ok


def _current_target(client, pos, now) -> dict | None:
    if pos is None:
        return None
    try:
        av = _analyst_view(client, pos)
    except Exception:
        return None
    if av is None or av.target_price is None:
        return None
    return {"target": str(av.target_price), "ts": now.isoformat(), "n": av.n_targets}


def _target_delta_events(prev, cur, ticker, now: datetime) -> list:
    """Сравнивает последнюю и новую целевую цену аналитиков."""
    if prev is None or not prev.get("target") or cur is None or not cur.get("target"):
        return []
    try:
        cur_v = Decimal(cur["target"])
        prev_v = Decimal(prev["target"])
    except (InvalidOperation, TypeError):
        return []
    if not prev_v:
        return []
    change = (cur_v - prev_v) / prev_v * 100
    if abs(change) < TARGET_CHANGE_MIN_PCT:
        return []
    tone = "позитив" if change > 0 else "негатив"
    return [
        NewsEvent(
            kind="Цель",
            ticker=ticker,
            text=(
                f"Цель аналитиков: {cur_v:.2f} ₽ vs {prev_v:.2f} ₽ "
                f"({change:+.1f}%), аналитиков: {cur.get('n', '—')}"
            ),
            ts=now,
            tone=tone,
            alert=change < 0,
        )
    ]


def _insider_events(client, pos, ticker, now: datetime) -> list:
    if pos is None:
        return []
    out = []
    try:
        views = _insider_views(client, pos)
    except Exception:
        return []
    for v in views:
        if v.disclosure_date is None or now - v.disclosure_date > timedelta(days=INSIDER_WINDOW_DAYS):
            continue
        alert = v.direction == "Продажа"
        tone = "позитив" if v.direction == "Покупка" else ("негатив" if v.direction == "Продажа" else None)
        text = f"Инсайдер «{v.investor_name}» ({v.position}): {v.direction.lower()} {v.quantity:,} шт"
        if v.price is not None:
            text += f" по {v.price:.2f} ₽"
        out.append(
            NewsEvent(
                kind="Инсайдер",
                ticker=ticker,
                text=text,
                ts=v.disclosure_date,
                tone=tone,
                alert=alert,
            )
        )
    return out


def _rsi_events(client, pos, ticker) -> list:
    if pos is None:
        return []
    rsi = _rsi(client, pos)
    if rsi is None:
        return []
    if rsi >= RSI_OVERBOUGHT:
        return [NewsEvent(kind="RSI", ticker=ticker, text=f"RSI {rsi:.0f} — перегрев", tone="негатив", alert=True)]
    if rsi <= RSI_OVERSOLD:
        return [NewsEvent(kind="RSI", ticker=ticker, text=f"RSI {rsi:.0f} — перепроданность, зона ценности", tone="позитив")]
    return []


def _payment_event(calendar, ticker: str, now: datetime):
    for e in calendar.events:
        if e.ticker != ticker:
            continue
        if e.date < now or e.date > now + timedelta(days=90):
            continue
        tone = "позитив" if e.amount and e.amount > 0 else None
        label = "Дивиденд" if e.kind == "ДИВ" else "Купон"
        return NewsEvent(
            kind="Отсечка",
            ticker=ticker,
            text=f"{label} {e.date:%d.%m.%Y}: {e.amount:.2f} ₽ на счёт",
            ts=e.date,
            tone=tone,
        )
    return None


def _load_targets() -> dict:
    if not TARGET_HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(TARGET_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_targets(targets: dict) -> None:
    from .backup import backup_file

    TARGET_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_file(TARGET_HISTORY_PATH)
    TARGET_HISTORY_PATH.write_text(
        json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─────────────────────────────────────────────
#  RSS (optional, graceful fallback)
# ─────────────────────────────────────────────

def _rss_events(positions, tickers):
    query_by_ticker = {p.ticker: p.name for p in positions}
    out = []
    for ticker in tickers:
        name = query_by_ticker.get(ticker, ticker)
        items = _rss_fetch(name)
        if items is None:
            return out, False
        if not items:
            continue
        titles = [t for _, t in items]
        tone = _tone_of(titles)
        sample = " · ".join(titles[:RSS_MAX_TITLES])
        out.append(
            NewsEvent(
                kind="RSS",
                ticker=ticker,
                text=f"{len(titles)} новостей за {RSS_WINDOW_DAYS} дн., фон «{tone}». {sample}",
                ts=items[0][0],
                tone=tone,
                alert=tone == "негатив",
            )
        )
    return out, True


def _rss_fetch(name: str):
    query = f'"{name}"'
    url = (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=ru&gl=RU&ceid=RU:ru"
    )
    try:
        with urlopen(url, timeout=RSS_TIMEOUT) as resp:
            raw = resp.read()
    except Exception:
        return None
    try:
        root = ET.fromstring(raw)
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        date_el = item.find("pubDate")
        if title_el is None or not title_el.text:
            continue
        title = " ".join(title_el.text.split())
        ts = None
        if date_el is not None and date_el.text:
            try:
                ts = parsedate_to_datetime(date_el.text)
            except Exception:
                ts = None
        if ts is not None and now - ts > timedelta(days=RSS_WINDOW_DAYS):
            continue
        items.append((ts or now, title))
    items.sort(key=lambda x: x[0], reverse=True)
    return items[:15]


def _tone_of(titles: list[str]) -> str:
    if not titles:
        return "нейтр"
    low = " ".join(titles).lower()
    pos = sum(1 for w in SENTIMENT_POS if w in low)
    neg = sum(1 for w in SENTIMENT_NEG if w in low)
    if pos > neg:
        return "позитив"
    if neg > pos:
        return "негатив"
    return "нейтр"