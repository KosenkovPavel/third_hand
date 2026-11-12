"""Тесты ИИС: вычет на взносы, сроки владения ИИС-3, прогресс, календарь (без сети)."""

from datetime import date, datetime
from decimal import Decimal

from src.accounting import AccountingReport, Operation
from src.iis import (
    TYPE_A_LIMIT,
    annual_deduction,
    deduction_claim_statuses,
    deduction_progress,
    holding_term,
    iis_deduction,
    min_holding_years,
    tax_calendar_notes,
    tax_report_text,
)


def _in_op(year: int, amount: str) -> Operation:
    return Operation(
        date=datetime(year, 6, 15),
        kind="in",
        op_type="Пополнение",
        payment=Decimal(amount),
        quantity=0,
        figi=None,
        ticker=None,
    )


class TestIisDeduction:
    def test_cap_and_rate(self):
        acct = AccountingReport(
            account_id="t",
            ops=[_in_op(2025, "100000"), _in_op(2026, "500000")],
        )
        years = iis_deduction(acct, rate=Decimal("0.13"))
        assert [y.year for y in years] == [2025, 2026]
        assert years[0].capped == Decimal("100000")
        assert years[0].potential_return == Decimal("13000.00")
        assert not years[0].over_limit
        assert years[1].capped == TYPE_A_LIMIT
        assert years[1].potential_return == Decimal("52000.00")
        assert years[1].over_limit

    def test_progressive_rate(self):
        acct = AccountingReport(account_id="t", ops=[_in_op(2026, "400000")])
        y = iis_deduction(acct, rate=Decimal("0.15"))[0]
        assert y.potential_return == Decimal("60000.00")

    def test_no_contributions(self):
        assert iis_deduction(AccountingReport(account_id="t")) == []


class TestAnnualDeduction:
    def test_below_limit(self):
        assert annual_deduction(Decimal("10000")) == Decimal("15600.00")

    def test_above_limit_capped(self):
        assert annual_deduction(Decimal("50000")) == Decimal("52000.00")


class TestHoldingTerm:
    def test_min_years_by_open_year(self):
        assert min_holding_years(2024) == 5
        assert min_holding_years(2026) == 5
        assert min_holding_years(2027) == 6
        assert min_holding_years(2031) == 10
        assert min_holding_years(2035) == 10  # потолок

    def test_term_not_expired(self):
        term = holding_term(date(2025, 12, 12), today=date(2026, 9, 28))
        assert term.min_years == 5
        assert term.mature_date == date(2030, 12, 12)
        assert not term.expired
        assert (term.years_left, term.months_left) == (4, 2)

    def test_term_expired(self):
        term = holding_term(date(2025, 12, 12), today=date(2030, 12, 13))
        assert term.expired
        assert (term.years_left, term.months_left) == (0, 0)

    def test_no_open_date(self):
        assert holding_term(None) is None


class TestDeductionProgress:
    def test_progress(self):
        year = datetime.now().year
        acct = AccountingReport(account_id="t", ops=[_in_op(year, "89538.84")])
        p = deduction_progress(acct, year=year)
        assert p.remaining == TYPE_A_LIMIT - Decimal("89538.84")
        assert p.missing_return == (p.remaining * Decimal("0.13")).quantize(Decimal("0.01"))

    def test_none_without_contributions(self):
        assert deduction_progress(AccountingReport(account_id="t"), year=2026) is None


class TestTaxCalendarNotes:
    def test_static_notes(self):
        notes = tax_calendar_notes(today=date(2026, 9, 28))
        assert any("за 2026 год заявляется в 2027" in n for n in notes)
        # окно «последние 3 года» согласовано с deduction_claim_statuses:
        # за 2026 заявляем в 2027, значит окно — 2023–2025
        assert any("за последние 3 года: 2023–2025" in n for n in notes)
        days_left = (date(2026, 12, 31) - date(2026, 9, 28)).days
        assert any(f"До конца года {days_left} дн." in n for n in notes)

    def test_last_day_of_year(self):
        notes = tax_calendar_notes(today=date(2026, 12, 31))
        assert not any("До конца года" in n for n in notes)


class TestClaimStatuses:
    TODAY = date(2026, 9, 28)

    def _years(self):
        acct = AccountingReport(
            account_id="t",
            ops=[
                _in_op(2022, "100000"),
                _in_op(2025, "100000"),
                _in_op(2026, "50000"),
            ],
        )
        return iis_deduction(acct)

    def test_available_early_expired(self):
        statuses = {cs.year: cs.status for cs in deduction_claim_statuses(self._years(), [], today=self.TODAY)}
        # 2022: окно заявления 2023–2025 истекло → сгорел
        assert statuses[2022] == "expired"
        # 2025: заявляется с 2026 → можно уже сейчас
        assert statuses[2025] == "available"
        # 2026: текущий год → заявляется в 2027
        assert statuses[2026] == "early"

    def test_claimed_skipped(self):
        statuses = deduction_claim_statuses(self._years(), [2022, 2025], today=self.TODAY)
        assert [cs.year for cs in statuses] == [2026]

    def test_amounts(self):
        statuses = {cs.year: cs for cs in deduction_claim_statuses(self._years(), [], today=self.TODAY)}
        assert statuses[2025].potential_return == Decimal("13000.00")

    def test_claim_window_by_date_not_year(self):
        # взнос в 2022 заявляется до 30 апреля 2026; 15 января 2026 — ещё можно
        acct = AccountingReport(account_id="t", ops=[_in_op(2022, "100000")])
        years = iis_deduction(acct)
        jan = {cs.year: cs.status for cs in deduction_claim_statuses(years, [], today=date(2026, 1, 15))}
        assert jan[2022] == "available"
        may = {cs.year: cs.status for cs in deduction_claim_statuses(years, [], today=date(2026, 5, 1))}
        assert may[2022] == "expired"


class TestTaxReportText:
    def test_report(self):
        acct = AccountingReport(account_id="t", ops=[_in_op(2026, "89538.84")])
        text = tax_report_text(acct, 2026)
        assert "Год: 2026" in text
        assert "89 538.84 ₽" in text
        assert "11 640.05 ₽" in text
        assert "219.1" in text

    def test_no_contributions(self):
        assert tax_report_text(AccountingReport(account_id="t"), 2026) is None
