"""Календарь будущих выплат: дивиденды и купоны по текущим позициям."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .money import to_decimal
from .portfolio import PortfolioSnapshot, Position
from .providers.tinkoff import TinkoffClient

HORIZON_DAYS = 365


@dataclass(frozen=True)
class PaymentEvent:
    date: datetime
    ticker: str
    name: str
    kind: str  # "ДИВ" / "КУПОН"
    per_unit: Decimal
    quantity: Decimal
    amount: Decimal
    amount_net: Decimal | None = None


@dataclass
class PaymentCalendar:
    events: list[PaymentEvent] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((e.amount for e in self.events), Decimal("0"))

    def by_month(self) -> dict[str, Decimal]:
        res: dict[str, Decimal] = {}
        for e in self.events:
            res[e.date.strftime("%Y-%m")] = res.get(e.date.strftime("%Y-%m"), Decimal("0")) + e.amount
        return res


def build_calendar(
    client: TinkoffClient,
    snapshot: PortfolioSnapshot,
    ndfl_rate: Decimal | None = None,
) -> PaymentCalendar:
    now = datetime.now(timezone.utc)
    to = now + timedelta(days=HORIZON_DAYS)
    events: list[PaymentEvent] = []

    for p in snapshot.positions:
        if p.quantity <= 0:
            continue
        if p.instrument_type in ("share", "etf"):
            events.extend(_share_dividends(client, p, now, to, ndfl_rate))
        elif p.instrument_type == "bond":
            events.extend(_bond_coupons(client, p, now, to, ndfl_rate))

    events.sort(key=lambda e: e.date)
    return PaymentCalendar(events=events)


def _net(amount: Decimal, ndfl_rate: Decimal | None) -> Decimal | None:
    """Сумма к получению после удержания НДФЛ при выплате (оценка)."""
    if not ndfl_rate or amount <= 0:
        return None
    return (amount * (1 - ndfl_rate)).quantize(Decimal("0.01"))


def _share_dividends(
    client: TinkoffClient,
    p: Position,
    from_: datetime,
    to: datetime,
    ndfl_rate: Decimal | None = None,
) -> list[PaymentEvent]:
    if not p.figi:
        return []
    try:
        resp = client.get_dividends(figi=p.figi, from_=from_, to=to)
    except Exception:
        return []

    out: list[PaymentEvent] = []
    for d in resp.dividends:
        pay = d.payment_date or d.record_date
        if pay and pay.year <= 1970:
            pay = d.record_date
        if pay and pay.year <= 1970:
            pay = None
        if pay and pay >= from_:
            # dividend_net — уже после вычета НДФЛ; брутто в этом SDK нет.
            # Повторно налог не применяем, поэтому amount_net не заполняем.
            per_share = to_decimal(d.dividend_net) if d.dividend_net else Decimal("0")
            amount = (per_share * p.quantity).quantize(Decimal("0.01"))
            if amount <= 0:
                continue
            out.append(
                PaymentEvent(
                    date=pay,
                    ticker=p.ticker,
                    name=p.name,
                    kind="ДИВ",
                    per_unit=per_share,
                    quantity=p.quantity,
                    amount=amount,
                )
            )
    return out


def _bond_coupons(
    client: TinkoffClient,
    p: Position,
    from_: datetime,
    to: datetime,
    ndfl_rate: Decimal | None = None,
) -> list[PaymentEvent]:
    instrument_id = p.figi or p.instrument_uid
    if not instrument_id:
        return []
    try:
        resp = client.get_bond_coupons(instrument_id=instrument_id, from_=from_, to=to)
    except Exception:
        return []

    out: list[PaymentEvent] = []
    for c in resp.events:
        if c.coupon_date and c.coupon_date >= from_:
            per_bond = to_decimal(c.pay_one_bond) if c.pay_one_bond else Decimal("0")
            amount = (per_bond * p.quantity).quantize(Decimal("0.01"))
            if amount <= 0:
                continue
            out.append(
                PaymentEvent(
                    date=c.coupon_date,
                    ticker=p.ticker,
                    name=p.name,
                    kind="КУПОН",
                    per_unit=per_bond,
                    quantity=p.quantity,
                    amount=amount,
                    amount_net=_net(amount, ndfl_rate),
                )
            )
    return out