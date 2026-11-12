"""Тесты XIRR: известные кейсы и вырожденные входы (без сети)."""

from datetime import date, datetime
from decimal import Decimal

from src.accounting import Operation
from src.xirr import flows_from_operations, xirr


class TestXirr:
    def test_single_contribution_one_year(self):
        # взнос 100 000, ровно через год портфель 110 000 → ~10% годовых
        r = xirr([(date(2025, 9, 29), Decimal("-100000")), (date(2026, 9, 29), Decimal("110000"))])
        assert r is not None
        assert abs(r - Decimal("10")) < Decimal("0.1")

    def test_two_contributions(self):
        # два взноса по 50 000 с разницей в год, итог 110 000 через 2 года
        r = xirr([
            (date(2024, 9, 29), Decimal("-50000")),
            (date(2025, 9, 29), Decimal("-50000")),
            (date(2026, 9, 29), Decimal("110000")),
        ])
        assert r is not None
        # средневзвешенный срок ~1.5 года: ставка между 5% и 8%
        assert Decimal("5") < r < Decimal("8")

    def test_loss(self):
        r = xirr([(date(2025, 9, 29), Decimal("-100000")), (date(2026, 9, 29), Decimal("90000"))])
        assert r is not None
        assert r < 0

    def test_degenerate(self):
        assert xirr([]) is None
        assert xirr([(date(2026, 1, 1), Decimal("-100"))]) is None
        # только взносы — нет смены знака
        assert xirr([(date(2026, 1, 1), Decimal("-100")), (date(2026, 6, 1), Decimal("-100"))]) is None
        # только положительные
        assert xirr([(date(2026, 1, 1), Decimal("100")), (date(2026, 6, 1), Decimal("100"))]) is None
        # нулевые суммы отбрасываются
        assert xirr([(date(2026, 1, 1), Decimal("0")), (date(2026, 6, 1), Decimal("0"))]) is None


class TestFlowsFromOperations:
    def test_out_payment_sign(self):
        # в API у выводов payment отрицательный: вывод должен стать положительным потоком
        ops = [
            Operation(
                date=datetime(2025, 10, 14), kind="in", op_type="Пополнение",
                payment=Decimal("100000"), quantity=0, figi=None, ticker=None,
            ),
            Operation(
                date=datetime(2026, 4, 14), kind="out", op_type="Вывод средств",
                payment=Decimal("-20000"), quantity=0, figi=None, ticker=None,
            ),
        ]
        flows = flows_from_operations(ops)
        assert flows[0][1] == Decimal("-100000")
        assert flows[1][1] == Decimal("20000")
        # внесено 100k, выведено 20k полгода назад, сейчас портфель 95k → доходность > 0
        flows.append((date(2026, 10, 14), Decimal("95000")))
        r = xirr(flows)
        assert r is not None and r > 0
