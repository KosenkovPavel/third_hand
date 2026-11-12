"""Бухгалтерия: анализ истории операций, денежный поток, налоговая база."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .money import to_decimal
from .providers.tinkoff import TinkoffClient

OPERATION_TYPES = {
    "Input": "Ввод средств",
    "Output": "Вывод средств",
    "Buy": "Покупка",
    "BuyCard": "Покупка по карте",
    "Sell": "Продажа",
    "Dividend": "Дивиденды",
    "Coupon": "Купон",
    "Tax": "Налог",
    "TaxCoupon": "Налог на купоны",
    "TaxDividend": "Налог на дивиденды",
    "TaxBack": "Возврат налога",
    "BrokerCommission": "Комиссия брокера",
    "ServiceCommission": "Комиссия за обслуживание",
    "PayIn": "Пополнение брокерского счёта",
    "PayOut": "Вывод средств",
}

# тип операции (type) соответствует числовым кодам, но они не стабильны —
# поэтому группируем по строковому полю type т-API.
# В этой версии полагаемся на приходящие строки type + operation_type.

# классификация по строковому типу операции
INFLOW_KW = ("пополнение", "пополнить", "ввод средств", "ввод денежных средств", "input", "payin")
OUTFLOW_KW = ("вывод", "output", "payout")


def classify_operation(op_type: str) -> str:
    """Категория операции по строковому типу из API:
    tax / fee / dividend / coupon / out / in / buy / sell / other."""
    kl = (op_type or "").lower()
    if "налог" in kl:
        return "tax"
    if "комисс" in kl:
        return "fee"
    if "дивид" in kl:
        return "dividend"
    if "купон" in kl:
        return "coupon"
    if any(s in kl for s in OUTFLOW_KW):
        return "out"
    if any(s in kl for s in INFLOW_KW):
        return "in"
    if "покупка" in kl or "buy" in kl:
        return "buy"
    if "продажа" in kl or "sell" in kl:
        return "sell"
    return "other"


@dataclass
class Operation:
    date: datetime
    kind: str
    op_type: str
    payment: Decimal
    quantity: int
    figi: str | None
    ticker: str | None
    name: str = ""
    instrument_uid: str | None = None


@dataclass(frozen=True)
class ReturnBreakdown:
    """Разбивка общего дохода: реализованный/нереализованный P&L, доход и издержки."""

    net_contrib: Decimal       # чистые взносы = внесено − выведено
    total_return: Decimal      # общий доход, ₽
    realized_pnl: Decimal      # реализованный P&L закрытых сделок, ₽
    unrealized_pnl: Decimal    # нереализованный P&L открытых позиций, ₽
    income: Decimal            # дивиденды + купоны, ₽
    costs: Decimal             # комиссии + налоги, ₽

    @property
    def total_return_pct(self) -> Decimal | None:
        """Простой доход к чистым взносам, %."""
        if not self.net_contrib:
            return None
        return (self.total_return / self.net_contrib * 100).quantize(Decimal("0.01"))


@dataclass
class AccountingReport:
    account_id: str
    total_in: Decimal = Decimal("0")
    total_out: Decimal = Decimal("0")
    total_buy: Decimal = Decimal("0")
    total_sell: Decimal = Decimal("0")
    total_dividends: Decimal = Decimal("0")
    total_coupons: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_taxes: Decimal = Decimal("0")
    operations_count: int = 0
    by_instrument: dict[str, dict] = field(default_factory=dict)
    ops: list[Operation] = field(default_factory=list)

    def monthly_inflows(self) -> dict[str, Decimal]:
        """Сумма пополнений («in»-операций) по месяцам YYYY-MM."""
        out: dict[str, Decimal] = {}
        for o in self.ops:
            if o.kind == "in":
                month = o.date.strftime("%Y-%m")
                out[month] = out.get(month, Decimal("0")) + o.payment
        return out

    def avg_monthly_inflow(self, last_months: int = 12) -> Decimal:
        """Средний приток за последние N полных месяцев (по фактическим операциям).

        Считается по календарным месяцам окна: месяцы без пополнений идут как 0.
        Месяцы до первого известного пополнения не учитываются.
        """
        inflows = self.monthly_inflows()
        if not inflows:
            return Decimal("0")
        now = datetime.now()
        y, m = now.year, now.month - 1
        if m == 0:
            y, m = y - 1, 12
        first = min(inflows)
        months: list[str] = []
        while len(months) < last_months and f"{y:04d}-{m:02d}" >= first:
            months.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        if not months:
            # первое пополнение в текущем месяце — полных месяцев окна ещё нет
            return Decimal("0")
        total = sum((inflows.get(mm, Decimal("0")) for mm in months), Decimal("0"))
        return (total / len(months)).quantize(Decimal("0.01"))

    def return_breakdown(
        self, current_value: Decimal, unrealized_pnl: Decimal
    ) -> ReturnBreakdown:
        """Общий доход и реализованный P&L — по закону сохранения денег.

        Тождество: стоимость = взносы + (дивиденды + купоны) − (комиссии + налоги)
        + реализованный P&L + нереализованный P&L. Реализованный P&L получаем
        вычитанием известных слагаемых — точный агрегат без FIFO-сопоставления.
        """
        net_contrib = self.total_in - self.total_out
        total_return = current_value - net_contrib
        income = self.total_dividends + self.total_coupons
        costs = self.total_fees + self.total_taxes
        realized = total_return - unrealized_pnl - income + costs
        return ReturnBreakdown(
            net_contrib=net_contrib,
            total_return=total_return,
            realized_pnl=realized,
            unrealized_pnl=unrealized_pnl,
            income=income,
            costs=costs,
        )


class Accountant:
    def __init__(self, client: TinkoffClient, account_id: str) -> None:
        self.client = client
        self.account_id = account_id

    def report(self, from_: datetime | None = None, to: datetime | None = None) -> AccountingReport:
        to = to or datetime.now()
        from_ = from_ or (to - timedelta(days=365 * 3))

        raw = self.client.get_operations(
            account_id=self.account_id, from_=from_, to=to
        )

        r = AccountingReport(account_id=self.account_id)
        for op in raw.operations:
            op_type = op.type or ""
            payment = to_decimal(op.payment)
            if payment == 0:
                continue
            r.operations_count += 1

            o = Operation(
                date=op.date,
                kind=op_type,
                op_type=str(op.operation_type or ""),
                payment=payment,
                quantity=op.quantity,
                figi=op.figi,
                ticker=None,
                instrument_uid=getattr(op, "instrument_uid", None),
            )
            r.ops.append(o)

        # классификация
        uid_cache: dict[str, str] = {}

        for o in r.ops:
            o.ticker = ""
            k = o.kind
            if o.instrument_uid:
                if o.instrument_uid in uid_cache:
                    o.ticker = uid_cache[o.instrument_uid]
                else:
                    try:
                        info = self.client.get_instrument(o.instrument_uid)
                        uid_cache[o.instrument_uid] = info.instrument.ticker
                        o.ticker = info.instrument.ticker
                    except Exception:
                        o.ticker = o.figi or "?"
            elif o.figi and o.figi not in uid_cache:
                try:
                    info = self.client.get_instrument(o.figi)
                    uid_cache[o.figi] = info.instrument.ticker
                    o.ticker = info.instrument.ticker
                except Exception:
                    o.ticker = o.figi

            kl = k.lower()
            kind = classify_operation(kl)
            if kind == "tax":
                # «Возврат налога» приходит с положительным payment и уменьшает сумму налогов
                r.total_taxes += -o.payment
            elif kind == "fee":
                r.total_fees += -o.payment
            elif kind == "dividend":
                r.total_dividends += o.payment
            elif kind == "coupon":
                r.total_coupons += o.payment
            elif kind == "out":
                r.total_out += -o.payment
            elif kind == "in":
                r.total_in += o.payment
            elif kind == "buy":
                r.total_buy += -o.payment
            elif kind == "sell":
                r.total_sell += o.payment

            o.kind = kind

            if o.ticker:
                d = r.by_instrument.setdefault(
                    o.ticker, {"buy": Decimal("0"), "sell": Decimal("0"), "div": Decimal("0")}
                )
                if kind == "buy":
                    d["buy"] += -o.payment
                elif kind == "sell":
                    d["sell"] += o.payment
                elif kind in ("dividend", "coupon"):
                    d["div"] += o.payment

        return r