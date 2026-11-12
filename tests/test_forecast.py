"""Тесты прогноза: сценарии, баланс на горизонте, защита от ≤ −100% (без сети)."""

from decimal import Decimal

import pytest

from src.forecast import balance_at, base_scenario, forecast, forecast_scenarios
from src.portfolio import PortfolioSnapshot


def _snapshot(total: str = "100000") -> PortfolioSnapshot:
    return PortfolioSnapshot(account_id="t", total=Decimal(total), cash=Decimal("0"))


class TestForecast:
    def test_growth_with_contributions(self):
        fc = forecast(_snapshot(), monthly=Decimal("10000"), annual_return_pct=Decimal("10"))
        first = fc.points[0]
        assert first.years == 1
        # база за год: 100k * 1.1 + взносы с процентами > вложенного
        assert first.balance > first.contributed == Decimal("220000.00")
        # монотонный рост по горизонтам
        balances = [p.balance for p in fc.points]
        assert balances == sorted(balances)

    def test_value_error_below_minus_100(self):
        with pytest.raises(ValueError):
            forecast(_snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("-100"))
        with pytest.raises(ValueError):
            forecast(_snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("-150"))

    def test_zero_return(self):
        fc = forecast(_snapshot(), monthly=Decimal("1000"), annual_return_pct=Decimal("0"))
        assert fc.points[0].balance == Decimal("112000.00")

    def test_inflation_real_balance(self):
        fc = forecast(
            _snapshot(),
            monthly=Decimal("0"),
            annual_return_pct=Decimal("10"),
            inflation_pct=Decimal("7"),
        )
        p = fc.points[0]
        expected = (Decimal("110000") / Decimal("1.07")).quantize(Decimal("0.01"))
        assert abs(p.balance_real - expected) < Decimal("1")

    def test_deduction_adds_to_balance(self):
        with_d = forecast(
            _snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("10"),
            annual_deduction=Decimal("13000"),
        )
        without = forecast(_snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("10"))
        assert with_d.points[0].deduction_gain == Decimal("13000.00")
        assert with_d.points[0].balance > without.points[0].balance


class TestBalanceAt:
    def test_matches_simple_zero_rate(self):
        b = balance_at(Decimal("100000"), Decimal("1000"), Decimal("0"), Decimal("2"))
        assert b == Decimal("124000.0")

    def test_fractional_years(self):
        b = balance_at(Decimal("100000"), Decimal("0"), Decimal("12"), Decimal("0.5"))
        # полгода при 12% годовых: ~(1.12)^0.5 - 1 ≈ 5.83%
        assert Decimal("105000") < b < Decimal("107000")

    def test_value_error_below_minus_100(self):
        with pytest.raises(ValueError):
            balance_at(Decimal("1"), Decimal("0"), Decimal("-100"), Decimal("1"))


class TestScenarios:
    def test_three_ordered_scenarios(self):
        sc = forecast_scenarios(
            _snapshot(), monthly=Decimal("10000"), annual_return_pct=Decimal("10"),
        )
        assert [s.label for s in sc] == ["Пессимистичный", "Базовый", "Оптимистичный"]
        assert [s.annual_return_pct for s in sc] == [Decimal("5"), Decimal("10"), Decimal("15")]
        finals = [s.forecast.final.balance for s in sc]
        assert finals == sorted(finals)

    def test_custom_spread(self):
        sc = forecast_scenarios(
            _snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("10"),
            spread=Decimal("2"),
        )
        assert [s.annual_return_pct for s in sc] == [Decimal("8"), Decimal("10"), Decimal("12")]

    def test_base_scenario_picks_base_not_first(self):
        sc = forecast_scenarios(
            _snapshot(), monthly=Decimal("0"), annual_return_pct=Decimal("10"),
        )
        assert base_scenario(sc).label == "Базовый"
        assert base_scenario(sc).annual_return_pct == Decimal("10")
