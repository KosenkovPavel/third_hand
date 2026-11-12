"""Тесты макромодуля: разбор ответа ЦБ и расчёт доходности вклада (без сети)."""

from decimal import Decimal

from src.macro import _extract_latest_rate, deposit_return_pct

SOAP_SAMPLE = (
    '<?xml version="1.0"?>'
    "<KeyRate>"
    "<KR><DT>2026-09-21T00:00:00+03:00</DT><Rate>14.00</Rate></KR>"
    "<KR><DT>2026-09-25T00:00:00+03:00</DT><Rate>14.50</Rate></KR>"
    "<KR><DT>2026-09-23T00:00:00+03:00</DT><Rate>14.00</Rate></KR>"
    "</KeyRate>"
)


class TestExtractLatestRate:
    def test_picks_latest_by_date(self):
        assert _extract_latest_rate(SOAP_SAMPLE) == Decimal("14.50")

    def test_empty_or_broken(self):
        assert _extract_latest_rate("<html>error</html>") is None
        assert _extract_latest_rate("") is None


class TestDepositReturn:
    def test_full_year(self):
        # 365 дней ≈ 0.9993 года по ACT/365.25
        assert deposit_return_pct(Decimal("14"), 365) == Decimal("13.99")

    def test_partial_period(self):
        assert deposit_return_pct(Decimal("14"), 100) == Decimal("3.83")
