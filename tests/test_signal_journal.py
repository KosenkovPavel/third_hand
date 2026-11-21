"""Тесты журнала сигналов «кризисного откупа» (самопроверка советника)."""

from datetime import date, timedelta
from decimal import Decimal

from src.signal_journal import journal_summary, load_journal, record_signal


def test_record_and_dedup_by_date(tmp_path):
    path = tmp_path / "journal.jsonl"
    today = date(2026, 11, 20)
    assert record_signal("crisis", Decimal("-35"), Decimal("2000"), today=today, path=path)
    # повтор в тот же день не пишется; «normal» не журналируется
    assert not record_signal("crisis", Decimal("-36"), Decimal("1990"), today=today, path=path)
    assert not record_signal("normal", Decimal("-5"), Decimal("2400"), today=today, path=path)
    entries = load_journal(path)
    assert len(entries) == 1
    assert entries[0]["state"] == "crisis"


def test_journal_summary_matured_outcomes(tmp_path):
    path = tmp_path / "journal.jsonl"
    today = date.today()
    old = today - timedelta(days=120)   # созрел (> 90 дней)
    fresh = today - timedelta(days=10)  # не созрел
    record_signal("crisis", Decimal("-35"), Decimal("2000"), today=old, path=path)
    record_signal("crisis", Decimal("-32"), Decimal("2100"), today=fresh, path=path)
    s = journal_summary(load_journal(path), Decimal("2600"), today=today)
    assert s["records"] == 2
    assert s["matured"] == 1
    assert s["wins"] == 1  # 2000 → 2600 это +30%
    assert s["status"] == "ok"
    assert s["outcomes"][0]["return_pct"] == Decimal("30.0")


def test_journal_summary_empty_and_immature(tmp_path):
    path = tmp_path / "journal.jsonl"
    s = journal_summary(load_journal(path), Decimal("2600"))
    assert s["records"] == 0
    assert s["matured"] == 0
    assert s["status"] == "мало данных"
    fresh = date.today() - timedelta(days=10)
    record_signal("stress", Decimal("-18"), Decimal("2369"), today=fresh, path=path)
    s = journal_summary(load_journal(path), Decimal("2600"))
    assert s["matured"] == 0  # stress не оценивается, только crisis
    assert s["first_date"] == fresh.isoformat()
