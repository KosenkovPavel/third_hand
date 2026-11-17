"""Тесты фундаментального скоринга: расширенные поля StatisticResponse (фаза 7)
и конфигурируемые метрики/веса (фаза 8)."""

from types import SimpleNamespace

from src.analytics import score_fundamentals
from src.metrics import DEFAULT_WEIGHTS, METRICS, metric_score, weighted_score


def _fake_f(**overrides):
    fields = dict(
        pe_ratio_ttm=5.0,
        price_to_book_ttm=1.0,
        price_to_sales_ttm=2.0,
        ev_to_ebitda_mrq=3.0,
        roe=20.0,
        roa=10.0,
        roic=15.0,
        net_margin_mrq=25.0,
        dividend_yield_daily_ttm=5.0,
        dividend_payout_ratio_fy=50.0,
        one_year_annual_revenue_growth_rate=12.0,
        three_year_annual_revenue_growth_rate=8.0,
        five_year_annual_revenue_growth_rate=6.0,
        total_debt_to_equity_mrq=30.0,
        total_debt_to_ebitda_mrq=1.5,
        beta=1.1,
        market_capitalization=100_000_000_000.0,
        free_cash_flow_ttm=2_000_000_000.0,
        eps_ttm=10.0,
        free_float=80.0,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_score_fundamentals_fills_extended_fields():
    rec = score_fundamentals(
        ticker="SBER", name="Сбер", instrument_type="share", f=_fake_f()
    )
    assert rec.ps == 2.0
    assert rec.ev_ebitda == 3.0
    assert rec.roa == 10.0
    assert rec.roic == 15.0
    assert rec.net_margin == 25.0
    assert rec.debt_ebitda == 1.5
    assert rec.market_cap == 100_000_000_000.0
    assert rec.fcf == 2_000_000_000.0
    assert rec.eps == 10.0
    assert rec.growth_3y == 8.0
    assert rec.growth_5y == 6.0
    assert rec.free_float == 80.0


def test_metric_score_pe_and_weighted_score():
    pts, note, risk = metric_score("pe", 5.0)
    assert pts == 100
    assert "недорого" in note
    assert risk == ""


def test_weighted_score_is_configurable():
    values = {"pe": 5.0, "div_yield": 5.0, "roe": 20.0}
    # только P/E — максимум
    score, _, _ = weighted_score(values, {"pe": 1})
    assert score == 100
    # разные веса дают взвешенное среднее
    score2, _, _ = weighted_score(values, {"pe": 1, "div_yield": 1, "roe": 1})
    assert 0 <= score2 <= 100
    # метрики с нулевым весом игнорируются
    score3, reasons3, _ = weighted_score(values, {"pe": 1, "div_yield": 0})
    assert score3 == 100
    assert all("P/E" in r for r in reasons3)


def test_default_weights_reference_known_metrics():
    for m in DEFAULT_WEIGHTS:
        assert m in METRICS, f"неизвестная метрика в DEFAULT_WEIGHTS: {m}"


def test_score_fundamentals_custom_weights():
    rec = score_fundamentals(
        ticker="SBER", name="Сбер", instrument_type="share", f=_fake_f(),
        weights={"pe": 1},
    )
    assert rec.score == 100  # P/E 5.0 → 100 баллов
