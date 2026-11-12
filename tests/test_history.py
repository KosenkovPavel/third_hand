"""Тесты истории снимков: обратная совместимость, max drawdown, бэкапы (без сети)."""

import json
from datetime import date
from decimal import Decimal

from src.backup import backup_file
from src.history import HistoryPoint, load_history, max_drawdown, save_point, time_weighted_return
from src.portfolio import PortfolioSnapshot


def _point(date: str, total: str, **kw) -> HistoryPoint:
    return HistoryPoint(
        date=date,
        total=Decimal(total),
        cash=Decimal("0"),
        invested=Decimal(kw.get("invested", "100")),
        pnl_pct=Decimal("0"),
        net_contrib=kw.get("net_contrib"),
    )


def _snapshot(total: str, collected_at: str) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        account_id="t", total=Decimal(total), cash=Decimal("0"), collected_at=collected_at
    )


class TestLoadHistory:
    def test_legacy_point_without_net_contrib(self, tmp_path):
        src = tmp_path / "history.json"
        src.write_text(
            json.dumps([{"date": "2026-01-10", "total": "100000", "cash": "0",
                         "invested": "90000", "pnl_pct": "11.11"}]),
            encoding="utf-8",
        )
        points = load_history(src)
        assert len(points) == 1
        assert points[0].net_contrib is None
        assert points[0].total == Decimal("100000")

    def test_missing_file(self, tmp_path):
        assert load_history(tmp_path / "nope.json") == []


class TestSavePoint:
    def test_same_day_replaced(self, tmp_path):
        dest = tmp_path / "history.json"
        save_point(_snapshot("100000", "2026-09-28T10:00:00"), path=dest)
        save_point(_snapshot("101000", "2026-09-28T18:00:00"), path=dest)
        points = load_history(dest)
        assert len(points) == 1
        assert points[0].total == Decimal("101000")

    def test_sorted_output(self, tmp_path):
        dest = tmp_path / "history.json"
        save_point(_snapshot("100000", "2026-09-28T10:00:00"), path=dest)
        save_point(_snapshot("90000", "2026-09-01T10:00:00"), path=dest)
        assert [p.date for p in load_history(dest)] == ["2026-09-01", "2026-09-28"]


class TestTimeWeightedReturn:
    def test_without_flows(self):
        points = [_point("2026-01-01", "100"), _point("2026-01-11", "110")]
        assert time_weighted_return(points, []) == Decimal("10.00")

    def test_contribution_does_not_count_as_return(self):
        points = [_point("2026-01-01", "100"), _point("2026-01-11", "120")]
        flows = [(date(2026, 1, 6), Decimal("20"))]
        assert time_weighted_return(points, flows) == Decimal("0.00")

    def test_flow_timing_affects_return(self):
        points = [_point("2026-01-01", "100"), _point("2026-01-11", "132")]
        flows = [(date(2026, 1, 6), Decimal("20"))]
        assert time_weighted_return(points, flows) == Decimal("10.91")

    def test_intervals_are_compounded(self):
        points = [
            _point("2026-01-01", "100"),
            _point("2026-01-11", "120"),
            _point("2026-01-21", "132"),
        ]
        flows = [(date(2026, 1, 11), Decimal("20"))]
        assert time_weighted_return(points, flows) == Decimal("10.00")

    def test_flows_outside_history_are_ignored(self):
        points = [_point("2026-01-01", "100"), _point("2026-01-11", "110")]
        flows = [
            (date(2025, 12, 31), Decimal("1000")),
            (date(2026, 1, 12), Decimal("1000")),
        ]
        assert time_weighted_return(points, flows) == Decimal("10.00")

    def test_not_enough_points(self):
        assert time_weighted_return([], []) is None
        assert time_weighted_return([_point("2026-01-01", "100")], []) is None


class TestMaxDrawdown:
    def test_drawdown(self):
        points = [
            _point("2026-01-01", "100000"),
            _point("2026-02-01", "120000"),
            _point("2026-03-01", "90000"),
            _point("2026-04-01", "115000"),
        ]
        dd = max_drawdown(points)
        assert dd is not None
        assert dd[0] == Decimal("-25.00")  # 90k от пика 120k
        assert dd[1] == "2026-03-01"

    def test_no_drawdown(self):
        points = [_point("2026-01-01", "100000"), _point("2026-02-01", "110000")]
        assert max_drawdown(points) is None

    def test_too_few_points(self):
        assert max_drawdown([_point("2026-01-01", "100000")]) is None
        assert max_drawdown([]) is None

    def test_contribution_does_not_create_drawdown(self):
        points = [_point("2026-01-01", "100"), _point("2026-01-11", "150")]
        flows = [(date(2026, 1, 11), Decimal("50"))]
        assert max_drawdown(points, flows) is None

    def test_drawdown_after_contribution_uses_twr(self):
        points = [
            _point("2026-01-01", "100"),
            _point("2026-01-11", "150"),
            _point("2026-01-21", "135"),
        ]
        flows = [(date(2026, 1, 11), Decimal("50"))]
        assert max_drawdown(points, flows) == (Decimal("-10.00"), "2026-01-21")


class TestBackup:
    def test_rotation_keeps_last_n(self, tmp_path):
        src = tmp_path / "data.json"
        src.write_text("{}", encoding="utf-8")
        for _ in range(4):
            backup_file(src, keep=2)
        backups = list((tmp_path / "backups").glob("data.*.json"))
        assert len(backups) == 2

    def test_missing_source(self, tmp_path):
        assert backup_file(tmp_path / "missing.json") is None
