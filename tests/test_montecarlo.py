"""Тесты персонального Монте-Карло по целям."""

from decimal import Decimal

from src.montecarlo import _weights, monte_carlo_goal, monte_carlo_goals
from src.portfolio import Allocation, PortfolioSnapshot
from src.profile import ProfileGoal, UserProfile


def _flat_returns(growth: float, months: int = 200):
    """Детерминированный ряд: все классы растут на growth в месяц, CPI ~0."""
    return [(growth, growth, growth, growth, 0.0)] * months


def _snapshot(total="100000"):
    return PortfolioSnapshot(
        account_id="t", total=Decimal(total), cash=Decimal("0"), total_pnl=Decimal("0"),
        allocation=[Allocation("Акции", Decimal("50000")),
                    Allocation("Облигации", Decimal("30000")),
                    Allocation("Золото", Decimal("20000"))],
    )


def test_weights_from_allocation():
    weq, wofz, wg, wcash = _weights(_snapshot())
    assert weq == 0.5 and wofz == 0.3 and wg == 0.2 and wcash == 0.0


def test_deterministic_growth_reaches_goal():
    profile = UserProfile(contributions=[])
    mc = monte_carlo_goal(_snapshot(), profile, "Цель", Decimal("200000"), 5,
                          returns=_flat_returns(0.01))
    # 100к * 1.01^60 ≈ 181к < 200к
    assert mc.p_success == Decimal("0.0")
    mc2 = monte_carlo_goal(_snapshot(), profile, "Цель", Decimal("150000"), 5,
                           returns=_flat_returns(0.01))
    assert mc2.p_success == Decimal("100.0")


def test_contributions_raise_success_chance():
    profile_none = UserProfile(contributions=[])
    from src.profile import ContributionRule
    profile_rich = UserProfile(contributions=[ContributionRule(day=1, amount=Decimal("50000"))])
    a = monte_carlo_goal(_snapshot(), profile_none, "Цель", Decimal("500000"), 10,
                         returns=_flat_returns(0.005))
    b = monte_carlo_goal(_snapshot(), profile_rich, "Цель", Decimal("500000"), 10,
                         returns=_flat_returns(0.005))
    assert b.p_success >= a.p_success
    assert b.p50 > a.p50


def test_no_returns_no_result():
    profile = UserProfile(goals=[ProfileGoal(name="X", target=Decimal("1"), years=5)])
    assert monte_carlo_goals(_snapshot(), profile, returns=None) != []
    # без файлов данных monte_carlo_goal честно вернёт None только если returns=None
    # и файла нет; здесь returns задан явно — проверяем пустые цели
    profile2 = UserProfile()
    assert monte_carlo_goals(_snapshot(), profile2) == []
