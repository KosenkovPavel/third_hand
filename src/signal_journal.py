"""Журнал сигналов «кризисного откупа»: фиксация факта сигнала с датой,
чтобы через EVAL_HORIZON_DAYS честно оценить его результат против IMOEX.

Append-only журнал в ``data/signal_journal.jsonl`` (не коммитится: data/ в
gitignore). Это витрина самопроверки советника: мы не просим веры в правило —
мы показываем, как оно отработало в наших же сигналах.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .config import DATA_DIR

EVAL_HORIZON_DAYS = 90
JOURNALED_STATES = ("crisis", "stress", "recovery")


def _path() -> Path:
    return DATA_DIR / "signal_journal.jsonl"


def load_journal(path: Path | None = None) -> list[dict]:
    src = path or _path()
    if not src.exists():
        return []
    entries = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def record_signal(state: str, dd_pct: Decimal, close: Decimal,
                  today: date | None = None, path: Path | None = None) -> bool:
    """Дописывает сигнал в журнал (дедуп по дате). «normal» не журналируем."""
    if state not in JOURNALED_STATES:
        return False
    today = today or datetime.now(timezone.utc).date()
    entries = load_journal(path)
    if any(e.get("date") == today.isoformat() for e in entries):
        return False
    src = path or _path()
    src.parent.mkdir(parents=True, exist_ok=True)
    with src.open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"date": today.isoformat(), "state": state,
             "dd_pct": str(dd_pct), "close": str(close)},
            ensure_ascii=False,
        ) + "\n")
    return True


def journal_summary(entries: list[dict], current_close: Decimal,
                    today: date | None = None) -> dict:
    """Сводка по журналу: сколько записей, сколько crisis-сигналов созрело
    для оценки и сколько из них в плюсе по IMOEX на сегодня."""
    today = today or datetime.now(timezone.utc).date()
    crisis = [e for e in entries if e.get("state") == "crisis"]
    outcomes = []
    if current_close and current_close > 0:
        for e in crisis:
            try:
                signal_close = Decimal(str(e["close"]))
            except Exception:  # noqa: BLE001
                continue
            if signal_close <= 0:
                continue
            if date.fromisoformat(e["date"]) + timedelta(days=EVAL_HORIZON_DAYS) > today:
                continue
            outcomes.append({
                "date": e["date"],
                "return_pct": ((current_close / signal_close - 1) * 100).quantize(Decimal("0.1")),
            })
    wins = sum(1 for o in outcomes if o["return_pct"] > 0)
    return {
        "records": len(entries),
        "crisis_records": len(crisis),
        "first_date": entries[0]["date"] if entries else None,
        "matured": len(outcomes),
        "wins": wins,
        "status": "ok" if outcomes else "мало данных",
        "outcomes": outcomes[-5:],
    }
