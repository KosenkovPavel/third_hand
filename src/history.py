"""История снимков портфеля: накопление точек во времени и вывод мини-графика."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .backup import backup_file
from .portfolio import PortfolioSnapshot


def _history_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "history.json"


@dataclass
class HistoryPoint:
    date: str
    total: Decimal
    cash: Decimal
    invested: Decimal
    pnl_pct: Decimal
    net_contrib: Decimal | None = None


def save_point(
    snapshot: PortfolioSnapshot,
    path: Path | None = None,
    net_contrib: Decimal | None = None,
) -> HistoryPoint:
    """Добавляет снимок в историю (без дубликатов за тот же день)."""
    dest = path or _history_path()
    points = load_history(dest)

    day = snapshot.collected_at[:10]
    point = HistoryPoint(
        date=day,
        total=snapshot.total,
        cash=snapshot.cash,
        invested=snapshot.invested,
        pnl_pct=(
            snapshot.total_pnl / snapshot.invested * 100
            if snapshot.invested
            else Decimal("0")
        ).quantize(Decimal("0.01")),
        net_contrib=net_contrib,
    )

    points = [p for p in points if p.date != day]
    points.append(point)
    points.sort(key=lambda p: p.date)
    backup_file(dest)
    dest.write_text(
        json.dumps([asdict(p) for p in points], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return point


def load_history(path: Path | None = None) -> list[HistoryPoint]:
    src = path or _history_path()
    if not src.exists():
        return []
    try:
        with open(src, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        import sys

        print(f"Предупреждение: не удалось прочитать историю {src}: {exc}", file=sys.stderr)
        return []
    out: list[HistoryPoint] = []
    for item in raw:
        net = item.get("net_contrib")
        out.append(
            HistoryPoint(
                date=str(item["date"]),
                total=Decimal(str(item["total"])),
                cash=Decimal(str(item.get("cash", "0"))),
                invested=Decimal(str(item.get("invested", "0"))),
                pnl_pct=Decimal(str(item.get("pnl_pct", "0"))),
                net_contrib=Decimal(str(net)) if net is not None else None,
            )
        )
    return out


def _interval_returns(
    points: list[HistoryPoint],
    flows: Iterable[tuple[date, Decimal]],
) -> list[Decimal] | None:
    if len(points) < 2:
        return None
    ordered_points = sorted(points, key=lambda p: p.date)
    ordered_flows = sorted(flows, key=lambda flow: flow[0])
    returns = []

    for start, end in zip(ordered_points, ordered_points[1:]):
        start_date = date.fromisoformat(start.date)
        end_date = date.fromisoformat(end.date)
        days = (end_date - start_date).days
        if days <= 0 or start.total <= 0:
            return None
        interval_flows = [
            (flow_date, amount)
            for flow_date, amount in ordered_flows
            if start_date < flow_date <= end_date
        ]
        net_flow = sum((amount for _, amount in interval_flows), Decimal("0"))
        weighted_flows = sum(
            (
                amount * Decimal((end_date - flow_date).days) / Decimal(days)
                for flow_date, amount in interval_flows
            ),
            Decimal("0"),
        )
        denominator = start.total + weighted_flows
        if denominator <= 0:
            return None
        returns.append((end.total - start.total - net_flow) / denominator)
    return returns


def time_weighted_return(
    points: list[HistoryPoint],
    flows: Iterable[tuple[date, Decimal]],
) -> Decimal | None:
    """TWR за период снимков: Modified Dietz между точками с объединением звеньев.

    Во flows положительные суммы — пополнения, отрицательные — выводы.
    Потоки до первой и после последней точки не участвуют в расчёте.
    """
    returns = _interval_returns(points, flows)
    if returns is None:
        return None
    factor = Decimal("1")
    for interval_return in returns:
        factor *= Decimal("1") + interval_return
    return ((factor - Decimal("1")) * 100).quantize(Decimal("0.01"))


def max_drawdown(
    points: list[HistoryPoint],
    flows: Iterable[tuple[date, Decimal]] | None = None,
) -> tuple[Decimal, str] | None:
    """Максимальная просадка по стоимости или по TWR-индексу при переданных потоках."""
    if len(points) < 2:
        return None
    ordered_points = sorted(points, key=lambda p: p.date)
    if flows is None:
        values = [p.total for p in ordered_points]
    else:
        returns = _interval_returns(ordered_points, flows)
        if returns is None:
            return None
        values = [Decimal("1")]
        for interval_return in returns:
            values.append(values[-1] * (Decimal("1") + interval_return))

    peak = values[0]
    worst = Decimal("0")
    worst_date = ""
    for point, value in zip(ordered_points, values):
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (value / peak - 1) * 100
            if drawdown < worst:
                worst = drawdown
                worst_date = point.date
    if worst >= 0:
        return None
    return worst.quantize(Decimal("0.01")), worst_date


def sparkline(points: list[HistoryPoint], width: int = 40) -> str:
    """Компактный график изменения стоимости портфеля блочными символами."""
    if len(points) < 2:
        return ""
    values = [p.total for p in points]
    mn, mx = min(values), max(values)
    span = mx - mn or Decimal("1")
    blocks = "▁▂▃▄▅▆▇█"
    n = min(width, len(points))
    step = len(points) / n
    selected = [points[int(i * step)] for i in range(n)]
    selected[-1] = points[-1]
    bars = "".join(
        blocks[int((p.total - mn) / span * (len(blocks) - 1))] for p in selected
    )
    return f"- min {mn:.0f} ₽ | max {mx:.0f} ₽ -\n    {bars}\n    {points[0].date} … {points[-1].date}"