"""Тесты правил дисциплины: дефолты профиля, сигналы, метки докупка/новая (без сети)."""

import json
from decimal import Decimal

from src.analytics import Recommendation, apply_portfolio_rules
from src.picks import Pick, apply_portfolio_context
from src.portfolio import Allocation, PortfolioSnapshot, Position
from src.profile import Rules, load_profile
from src.risk import rule_signals


class TestRulesDefaults:
    def test_defaults_without_section(self, tmp_path):
        p = tmp_path / "profile.json"
        p.write_text(json.dumps({"target": {"Акции": 70, "Облигации": 30}}), encoding="utf-8")
        rules = load_profile(p).rules
        assert rules.max_stock_positions == 10
        assert rules.min_position_pct == Decimal("5")
        assert rules.max_position_pct == Decimal("20")
        assert rules.max_sector_pct == Decimal("30")

    def test_custom_values(self, tmp_path):
        p = tmp_path / "profile.json"
        p.write_text(
            json.dumps({"rules": {"max_stock_positions": 5, "max_position_pct": 15}}),
            encoding="utf-8",
        )
        rules = load_profile(p).rules
        assert rules.max_stock_positions == 5
        assert rules.max_position_pct == Decimal("15")
        assert rules.min_position_pct == Decimal("5")  # остальные — дефолты

    def test_invalid_values_raise_friendly(self, tmp_path):
        import pytest

        bad_profiles = [
            {"target": {"Акции": "abc"}},
            {"usdrub_alert": "дорого"},
            {"iis": {"ndfl_rate": "много"}},
            {"iis": {"claimed_years": ["двадцать"]}},
            {"rules": {"max_position_pct": "пятая"}},
            {"rules": {"max_stock_positions": 0}},
            {"rules": {"max_sector_pct": 120}},
            {"rules": {"min_position_pct": -1}},
            {"contributions": [{"day": 5, "amount": -1000}]},
            {"goals": [{"name": "X", "target": 1000, "years": 0}]},
            {"goals": [{"name": "X", "target": -5, "years": 3}]},
        ]
        for i, raw in enumerate(bad_profiles):
            p = tmp_path / f"bad{i}.json"
            p.write_text(json.dumps(raw), encoding="utf-8")
            with pytest.raises(ValueError):
                load_profile(p)


def _share(ticker: str, amount: str, uid: str = "") -> Position:
    return _pos(ticker, amount, "share", "Акции", uid)


def _bond(ticker: str, amount: str) -> Position:
    return _pos(ticker, amount, "bond", "Облигации", ticker)


def _pos(ticker: str, amount: str, itype: str, type_name: str, uid: str) -> Position:
    return Position(
        ticker=ticker,
        name=ticker,
        instrument_type=itype,
        type_name=type_name,
        quantity=Decimal("1"),
        current_price=Decimal(amount),
        avg_price=Decimal(amount),
        amount=Decimal(amount),
        invested=Decimal(amount),
        pnl=Decimal("0"),
        pnl_pct=Decimal("0"),
        expected_yield=Decimal("0"),
        daily_yield=Decimal("0"),
        instrument_uid=uid or ticker,
    )


def _snapshot(positions: list[Position]) -> PortfolioSnapshot:
    total = sum((p.amount for p in positions), Decimal("0"))
    return PortfolioSnapshot(
        account_id="t",
        total=total,
        cash=Decimal("0"),
        positions=positions,
        allocation=[Allocation("Акции", total)],
    )


class TestRuleSignals:
    def test_position_limit(self):
        snap = _snapshot([_share("SBER", "41000"), _share("LKOH", "15000"), _bond("SU26238", "44000")])
        signals = rule_signals(snap, Rules())
        assert any("SBER" in s and "лимита 20%" in s and "разбавляем притоком" in s for s in signals)
        assert not any("LKOH" in s and "лимита" in s for s in signals)

    def test_sector_limit(self):
        snap = _snapshot([_share("SBER", "35000", uid="u1"), _share("LKOH", "65000", uid="u2")])
        sectors = {"u1": "financial", "u2": "financial"}
        signals = rule_signals(snap, Rules(), sector_by_uid=sectors)
        assert any("Сектор «Финансы»" in s and "лимита 30%" in s for s in signals)

    def test_sector_check_skipped_without_map(self):
        snap = _snapshot([_share("SBER", "35000", uid="u1")])
        signals = rule_signals(snap, Rules(), sector_by_uid=None)
        assert not any("Сектор" in s for s in signals)

    def test_too_many_stocks(self):
        rules = Rules()
        rules.max_stock_positions = 2
        snap = _snapshot([_share("A", "300"), _share("B", "300"), _share("C", "400")])
        signals = rule_signals(snap, rules)
        assert any("Акций 3 при лимите 2" in s and "не открывайте новые" in s for s in signals)

    def test_no_signals_within_limits(self):
        snap = _snapshot([_share("A", "10000"), _share("B", "10000"), _bond("SU26238", "80000")])
        assert rule_signals(snap, Rules()) == []


def _recommendation(ticker: str) -> Recommendation:
    return Recommendation(
        ticker=ticker,
        name=ticker,
        instrument_type="share",
        action="Покупать",
        score=80,
    )


class TestRecommendationRules:
    def test_position_limit_blocks_buy(self):
        snap = _snapshot([_share("SBER", "41000"), _bond("SU26238", "59000")])
        rec = apply_portfolio_rules([_recommendation("SBER")], snap, Rules())[0]
        assert rec.action == "Держать"
        assert any("не докупать" in risk for risk in rec.risks)

    def test_sector_limit_blocks_buy(self):
        snap = _snapshot([_share("SBER", "35000", uid="u1"), _share("LKOH", "65000", uid="u2")])
        rec = apply_portfolio_rules(
            [_recommendation("SBER")], snap, Rules(), {"u1": "financial", "u2": "financial"}
        )[0]
        assert rec.action == "Держать"
        assert any("Сектор" in risk for risk in rec.risks)

    def test_buy_allowed_within_limits(self):
        snap = _snapshot([_share("SBER", "10000"), _bond("SU26238", "90000")])
        rec = apply_portfolio_rules([_recommendation("SBER")], snap, Rules())[0]
        assert rec.action == "Покупать"


def _pick(ticker: str, blend: int) -> Pick:
    return Pick(
        ticker=ticker,
        name=ticker,
        sector="IT",
        fund_score=blend,
        action="Покупать",
        blend_score=blend,
        potential_pct=None,
        rsi=None,
        pe=None,
        div_yield=None,
        roe=None,
        payout=None,
    )


class TestPortfolioContext:
    def test_labels(self):
        picks = [_pick("SBER", 80), _pick("IRAO", 79)]
        out = apply_portfolio_context(
            picks, {"SBER"}, Decimal("100000"), Decimal("5000"), Decimal("5")
        )
        by_ticker = {p.ticker: p for p in out}
        assert by_ticker["SBER"].in_portfolio
        assert "докупка" in by_ticker["SBER"].notes
        assert not by_ticker["IRAO"].in_portfolio
        assert any("новая (мин. позиция ~5000 ₽)" in n for n in by_ticker["IRAO"].notes)

    def test_new_position_below_min_budget(self):
        picks = [_pick("IRAO", 80)]
        out = apply_portfolio_context(
            picks, {"SBER"}, Decimal("200000"), Decimal("5000"), Decimal("5")
        )
        # мин. позиция 5% от 200k = 10 000 ₽, бюджет 5 000 ₽ — ниже порога
        assert any("ниже мин. позиции" in n and "докупить имеющееся" in n for n in out[0].notes)

    def test_addon_sort_bonus(self):
        # докупка 79 должна встать выше новой 80 (бонус +2), но ниже новой 82+
        picks = [_pick("NEW1", 80), _pick("NEW2", 82), _pick("OLD", 79)]
        out = apply_portfolio_context(
            picks, {"OLD"}, Decimal("100000"), None, Decimal("5")
        )
        assert [p.ticker for p in out] == ["NEW2", "OLD", "NEW1"]

    def test_no_context(self):
        # без контекста портфеля порядок не меняется и меток нет
        picks = [_pick("A", 70), _pick("B", 80)]
        out = apply_portfolio_context(picks, None, None, None, Decimal("5"))
        assert [p.ticker for p in out] == ["A", "B"]
        assert all(p.notes == [] for p in out)
