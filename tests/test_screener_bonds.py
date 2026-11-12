"""Тесты сортировки скринера и строк облигаций (чистая логика, без сети)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.bonds import _build_row
from src.screener import ScreenRow, _row_sort_key


def _row(ticker: str, div_yield) -> ScreenRow:
    return ScreenRow(
        ticker=ticker, name=ticker, sector="", pe=None, pb=None, roe=None,
        div_yield=div_yield, rev_growth=None, debt_eq=None, mcap_bln=None, passed=True,
    )


class TestRowSortKey:
    def test_none_div_yield_last_not_crash(self):
        rows = [_row("NODIV", None), _row("HIGH", 12.0), _row("LOW", 3.0)]
        rows.sort(key=_row_sort_key)
        assert [r.ticker for r in rows] == ["HIGH", "LOW", "NODIV"]


def _fake_bond(**kw) -> SimpleNamespace:
    defaults = dict(
        ticker="T", name="Test Bond", sector="", nominal=None,
        maturity_date=datetime.now(timezone.utc) + timedelta(days=365 * 3),
        floating_coupon_flag=False, amortization_flag=False,
        subordinated_flag=False, for_iis_flag=True,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _money_value(units: int) -> SimpleNamespace:
    return SimpleNamespace(units=units, nano=0)


class TestBuildRow:
    NOW = datetime.now(timezone.utc)

    def test_floaters_no_ytm_but_present(self):
        b = _fake_bond(
            floating_coupon_flag=True,
            nominal=_money_value(1000),
        )
        row = _build_row(b, Decimal("1000"), [], self.NOW)
        assert row.ytm_approx_pct is None
        assert "плавающий купон" in row.notes

    def test_amortized_no_ytm_but_present(self):
        b = _fake_bond(
            amortization_flag=True,
            nominal=_money_value(1000),
        )
        row = _build_row(b, Decimal("1000"), [], self.NOW)
        assert row.ytm_approx_pct is None
        assert "амортизация" in row.notes

    def test_plain_bond_has_ytm(self):
        b = _fake_bond(nominal=_money_value(1000))
        coupon = SimpleNamespace(
            coupon_date=self.NOW + timedelta(days=180),
            pay_one_bond=_money_value(40),
        )
        row = _build_row(b, Decimal("1000"), [coupon], self.NOW)
        assert row.ytm_approx_pct is not None

    def test_ytm_with_aci_lower_than_without(self):
        # НКД при покупке — часть полной цены, YTM по ней ниже
        coupons = [
            SimpleNamespace(
                coupon_date=self.NOW + timedelta(days=d),
                pay_one_bond=_money_value(40),
            )
            for d in (180, 360, 540, 720)
        ]
        without = _build_row(_fake_bond(nominal=_money_value(1000)), Decimal("950"), coupons, self.NOW)
        with_aci = _build_row(
            _fake_bond(nominal=_money_value(1000), aci_value=_money_value(35)),
            Decimal("950"),
            coupons,
            self.NOW,
        )
        assert without.ytm_approx_pct is not None and with_aci.ytm_approx_pct is not None
        assert with_aci.ytm_approx_pct < without.ytm_approx_pct

    def test_negative_ytm_for_premium_bond(self):
        # цена выше суммы потоков → YTM отрицательный (не «залипает» на 0.01%)
        b = _fake_bond(nominal=_money_value(1000))
        coupon = SimpleNamespace(
            coupon_date=self.NOW + timedelta(days=180),
            pay_one_bond=_money_value(10),
        )
        row = _build_row(b, Decimal("1100"), [coupon], self.NOW)
        assert row.ytm_approx_pct is not None
        assert row.ytm_approx_pct < 0

    def test_ofz_price_is_percent_of_nominal(self):
        b = _fake_bond(ticker="SU26238", name="ОФЗ 26238", nominal=_money_value(1000))
        row = _build_row(b, Decimal("95.5"), [], self.NOW)
        assert row.price == Decimal("955.00")      # 95.5% от 1000
        assert row.price_pct == Decimal("95.50")

    def test_corporate_bond_price_in_rubles_even_below_half_nominal(self):
        b = _fake_bond(ticker="RU000A10DTA2", name="Корпоративная", nominal=_money_value(1000))
        row = _build_row(b, Decimal("450"), [], self.NOW)
        # 450 ₽ — не пересчитываем в %, даже если ниже 50% номинала
        assert row.price == Decimal("450.00")


class TestScreenerSelection:
    def test_top_by_mcap(self):
        from src.screener import _top_by_mcap

        shares = [SimpleNamespace(ticker=t, asset_uid=f"u{t}") for t in ("A", "B", "C")]
        funds = {
            "uA": SimpleNamespace(market_capitalization=1e9),
            "uB": SimpleNamespace(market_capitalization=3e9),
            "uC": SimpleNamespace(market_capitalization=2e9),
        }
        top = _top_by_mcap(shares, funds, 2)
        assert [s.ticker for s in top] == ["B", "C"]

    def test_passed_rows_empty_when_nobody_passed(self):
        from src.screener import _passed_rows

        failed = _row("FAIL", 5.0)
        failed.passed = False
        assert _passed_rows([failed]) == []
