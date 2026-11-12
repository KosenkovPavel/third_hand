"""Тесты бухгалтерии: классификация операций и средний приток (без сети)."""

from datetime import datetime
from decimal import Decimal

from src.accounting import AccountingReport, Operation, classify_operation


def _op(year: int, month: int, amount: str, kind: str = "in") -> Operation:
    return Operation(
        date=datetime(year, month, 10),
        kind=kind,
        op_type="",
        payment=Decimal(amount),
        quantity=0,
        figi=None,
        ticker=None,
    )


class TestClassifyOperation:
    def test_inflow(self):
        assert classify_operation("Пополнение брокерского счета") == "in"
        assert classify_operation("Ввод средств") == "in"

    def test_outflow(self):
        assert classify_operation("Вывод средств") == "out"

    def test_trades(self):
        assert classify_operation("Покупка ценных бумаг") == "buy"
        assert classify_operation("Buy") == "buy"
        assert classify_operation("Продажа ценных бумаг") == "sell"
        assert classify_operation("Sell") == "sell"

    def test_income_and_charges(self):
        assert classify_operation("Дивиденды") == "dividend"
        assert classify_operation("Купон") == "coupon"
        assert classify_operation("Налог") == "tax"
        assert classify_operation("Возврат налога") == "tax"
        assert classify_operation("Комиссия брокера") == "fee"

    def test_unknown(self):
        assert classify_operation("Что-то непонятное") == "other"
        assert classify_operation("") == "other"


class TestAvgMonthlyInflow:
    def test_empty(self):
        assert AccountingReport(account_id="t").avg_monthly_inflow() == Decimal("0")

    def test_single_full_month(self):
        now = datetime.now()
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        r = AccountingReport(account_id="t", ops=[_op(y, m, "10000")])
        assert r.avg_monthly_inflow() == Decimal("10000.00")

    def test_zero_months_count(self):
        now = datetime.now()
        months = []
        y, m = now.year, now.month - 1
        if m == 0:
            y, m = y - 1, 12
        for _ in range(3):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        first_y, first_m = months[-1]
        r = AccountingReport(account_id="t", ops=[_op(first_y, first_m, "9000")])
        # пополнение было 3 месяца назад: окно = 3 месяца, два из них нулевые
        assert r.avg_monthly_inflow() == Decimal("3000.00")

    def test_first_inflow_current_month_no_division_by_zero(self):
        now = datetime.now()
        r = AccountingReport(account_id="t", ops=[_op(now.year, now.month, "5000")])
        # полных месяцев в окне ещё нет — должен быть 0, а не InvalidOperation
        assert r.avg_monthly_inflow() == Decimal("0")


class TestReturnBreakdown:
    def _acct(self) -> AccountingReport:
        return AccountingReport(
            account_id="t",
            total_in=Decimal("100000"),
            total_out=Decimal("0"),
            total_dividends=Decimal("5000"),
            total_coupons=Decimal("2000"),
            total_fees=Decimal("700"),
            total_taxes=Decimal("300"),
        )

    def test_breakdown_identity(self):
        # стоимость 110 000, нереализованный P&L 3 000
        b = self._acct().return_breakdown(Decimal("110000"), Decimal("3000"))
        # общий доход = стоимость − чистые взносы
        assert b.total_return == Decimal("10000.00")
        assert b.net_contrib == Decimal("100000")
        # доходы и издержки
        assert b.income == Decimal("7000.00")
        assert b.costs == Decimal("1000.00")
        # реализованный P&L: 10000 − 3000 − 7000 + 1000
        assert b.realized_pnl == Decimal("1000.00")
        # тождество: сумма частей = общий доход
        assert b.realized_pnl + b.unrealized_pnl + b.income - b.costs == b.total_return

    def test_total_return_pct(self):
        b = self._acct().return_breakdown(Decimal("110000"), Decimal("3000"))
        assert b.total_return_pct == Decimal("10.00")

    def test_no_net_contrib_gives_none_pct(self):
        acct = AccountingReport(account_id="t", total_in=Decimal("1000"), total_out=Decimal("1000"))
        b = acct.return_breakdown(Decimal("0"), Decimal("0"))
        assert b.total_return_pct is None

    def test_realized_can_be_negative(self):
        # стоимость ниже взносов → общий убыток, реализованный P&L отрицателен
        acct = AccountingReport(account_id="t", total_in=Decimal("100000"))
        b = acct.return_breakdown(Decimal("90000"), Decimal("0"))
        assert b.total_return == Decimal("-10000.00")
        assert b.realized_pnl == Decimal("-10000.00")
