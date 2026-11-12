"""XIRR — годовая доходность портфеля с учётом сроков взносов и выводов.

Честная метрика для сравнения с вкладом/индексом: простой «P&L / вложено»
искажается моментами пополнений, XIRR — нет.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

RATE_MIN = -0.9999  # полный убыток — асимптота
RATE_MAX = 10.0     # 1000% годовых — достаточно
_ITERATIONS = 200


def flows_from_operations(ops) -> list[tuple[date, Decimal]]:
    """Потоки для XIRR из операций: взносы (in) отрицательные, выводы (out)
    положительные. У out-операций payment отрицательный в API, поэтому
    знак единый: -payment."""
    return [
        (op.date.date(), -op.payment)
        for op in ops
        if op.kind in ("in", "out")
    ]


def xirr(flows: list[tuple[date, Decimal]]) -> Decimal | None:
    """Годовая доходность, % — корень уравнения NPV(r) = 0, бисекция.

    ``flows``: (дата, сумма); взносы отрицательные, выводы и финальная
    стоимость портфеля — положительные. None, если потоков < 2 или ставка
    не находится (нет смены знака NPV в диапазоне).
    """
    flows = [(d, a) for d, a in flows if a != 0]
    if len(flows) < 2:
        return None
    if not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None

    d0 = min(d for d, _ in flows)

    def npv(r: float) -> float:
        total = 0.0
        for d, a in flows:
            years = (d - d0).days / 365.25
            total += float(a) / (1 + r) ** years
        return total

    lo, hi = RATE_MIN, RATE_MAX
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(_ITERATIONS):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return Decimal(str(round((lo + hi) / 2 * 100, 2)))
