"""Отдельная симуляция: те же денежные потоки направляются в IMOEX."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .money import to_decimal


@dataclass(frozen=True)
class IndexSimulation:
    final_value: Decimal
    actual_value: Decimal
    difference: Decimal
    units: Decimal
    net_contrib: Decimal
    profit_pct: Decimal
    operations_count: int


def simulate_index_contributions(
    client,
    operations,
    actual_value: Decimal,
    end_date: date | None = None,
    issues: list[str] | None = None,
) -> IndexSimulation | None:
    """Покупает IMOEX на взносы и продаёт на выводы по дневным свечам.

    Дивиденды индекса и комиссии не моделируются. Реальные данные портфеля
    не изменяются: результат существует только в возвращаемом объекте.
    """
    flows = sorted(
        (op for op in operations if op.kind in ("in", "out") and op.payment),
        key=lambda op: op.date,
    )
    if not flows:
        return None
    end_date = end_date or datetime.now(timezone.utc).date()
    try:
        uid = client.find_instrument_uid("IMOEX", "IMOEX")
    except Exception as exc:
        if issues is not None:
            issues.append(f"Симуляция IMOEX: инструмент недоступен ({exc})")
        return None
    if not uid:
        if issues is not None:
            issues.append("Симуляция IMOEX: инструмент не найден")
        return None
    interval = client.daily_interval()

    price_cache: dict[tuple[date, str], Decimal | None] = {}

    def price_at(target: date, side: str = "first") -> Decimal | None:
        key = (target, side)
        if key in price_cache:
            return price_cache[key]
        moment = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
        start = moment if side == "first" else moment - timedelta(days=10)
        stop = moment + timedelta(days=10 if side == "first" else 1)
        try:
            candles = client.get_candles(uid, start, stop, interval).candles
            price = to_decimal(candles[0 if side == "first" else -1].close) if candles else None
        except Exception:
            price = None
        price_cache[key] = price
        return price

    units = Decimal("0")
    net_contrib = Decimal("0")
    for op in flows:
        flow_date = op.date.date()
        if flow_date > end_date:
            continue
        price = price_at(flow_date)
        if price is None or price <= 0:
            if issues is not None:
                issues.append(f"Симуляция IMOEX: нет цены на {flow_date}")
            return None
        units += op.payment / price
        net_contrib += op.payment
        if units < 0:
            if issues is not None:
                issues.append(f"Симуляция IMOEX: вывод {flow_date} превышает виртуальный капитал")
            return None
    if not net_contrib:
        return None
    final_price = price_at(end_date, "last")
    if final_price is None or final_price <= 0:
        if issues is not None:
            issues.append(f"Симуляция IMOEX: нет конечной цены на {end_date}")
        return None
    final_value = (units * final_price).quantize(Decimal("0.01"))
    profit_pct = (
        (final_value - net_contrib) / abs(net_contrib) * 100
        if net_contrib
        else Decimal("0")
    ).quantize(Decimal("0.01"))
    return IndexSimulation(
        final_value=final_value,
        actual_value=actual_value,
        difference=(actual_value - final_value).quantize(Decimal("0.01")),
        units=units,
        net_contrib=net_contrib,
        profit_pct=profit_pct,
        operations_count=len(flows),
    )
