"""Тесты оценки целей из профиля: требуемое пополнение, достижимость (без сети)."""

from decimal import Decimal

from src.forecast import balance_at
from src.goals import build_goal_report, evaluate_goal, required_monthly, stock_share_hint
from src.profile import ProfileGoal


class TestRequiredMonthly:
    def test_roundtrip(self):
        target, years = Decimal("1000000"), 5
        m = required_monthly(Decimal("100000"), target, Decimal("10"), years)
        assert m is not None
        projected = balance_at(Decimal("100000"), m, Decimal("10"), Decimal(years))
        assert projected >= target

    def test_zero_rate(self):
        m = required_monthly(Decimal("0"), Decimal("120000"), Decimal("0"), 10)
        assert m == Decimal("1000.0")

    def test_already_reached(self):
        assert required_monthly(Decimal("2000000"), Decimal("1000000"), Decimal("10"), 5) is None


class TestEvaluateGoal:
    def test_feasible(self):
        g = ProfileGoal(name="Машина", target=Decimal("200000"), years=5)
        st = evaluate_goal(g, Decimal("100000"), Decimal("10000"), Decimal("10"))
        assert st.feasible
        assert st.needed_monthly is None

    def test_not_feasible(self):
        g = ProfileGoal(name="Квартира", target=Decimal("10000000"), years=3)
        st = evaluate_goal(g, Decimal("100000"), Decimal("10000"), Decimal("10"))
        assert not st.feasible
        assert st.needed_monthly is not None and st.needed_monthly > Decimal("10000")


class TestStockShareHint:
    def test_boundaries(self):
        assert "0–20%" in stock_share_hint(2)
        assert "40–60%" in stock_share_hint(5)
        assert "профиле" in stock_share_hint(10)


class TestGoalNdflRate:
    def test_deduction_uses_profile_rate(self):
        rep = build_goal_report(
            Decimal("100000"),
            monthly=Decimal("10000"),
            reinvest_deduction=True,
            ndfl_rate=Decimal("0.15"),
        )
        # 10 000 × 12 × 15% = 18 000 ₽ в год
        assert rep.annual_deduction == Decimal("18000.00")

    def test_default_rate_13(self):
        rep = build_goal_report(Decimal("0"), monthly=Decimal("10000"), reinvest_deduction=True)
        assert rep.annual_deduction == Decimal("15600.00")
