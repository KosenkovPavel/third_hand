"""Тесты пресетов стратегий, стилей скрининга и класса «Золото»."""

import json
from decimal import Decimal

import pytest

from src.metrics import DEFAULT_WEIGHTS, SCORING_STYLES, merge_style_weights
from src.portfolio import GOLD_CLASS, classify_gold, classify_position
from src.profile import STRATEGY_PRESETS, UserProfile, load_profile


def _write(tmp_path, data: dict):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_presets_sum_to_100():
    for key, preset in STRATEGY_PRESETS.items():
        assert sum(preset["target"].values()) == 100, f"{key}: сумма долей ≠ 100"


def test_load_profile_reads_strategy(tmp_path):
    path = _write(tmp_path, {
        "strategy": "balanced",
        "target": dict(STRATEGY_PRESETS["balanced"]["target"]),
        "scoring_styles": ["dividend", "value"],
    })
    profile = load_profile(path)
    assert profile.strategy == "balanced"
    assert profile.scoring_styles == ["dividend", "value"]
    assert profile.target["Золото"] == Decimal("20")
    assert "Сбалансированная" in profile.describe()


def test_load_profile_rejects_unknown_strategy(tmp_path):
    path = _write(tmp_path, {"strategy": "лунная", "target": {"Акции": 100}})
    with pytest.raises(ValueError, match="неизвестная стратегия"):
        load_profile(path)


def test_load_profile_without_strategy_backwards_compatible(tmp_path):
    path = _write(tmp_path, {"target": {"Акции": 70, "Облигации": 30}})
    profile = load_profile(path)
    assert profile.strategy is None
    assert profile.scoring_styles == []
    assert profile.target == {"Акции": Decimal("70"), "Облигации": Decimal("30")}


def test_load_profile_custom_strategy(tmp_path):
    path = _write(tmp_path, {"strategy": "custom", "target": {"Акции": 60, "Облигации": 40}})
    assert load_profile(path).strategy == "custom"


def test_merge_style_weights_empty_is_default():
    assert merge_style_weights([]) == dict(DEFAULT_WEIGHTS)
    assert merge_style_weights(["нет_такого"]) == dict(DEFAULT_WEIGHTS)


def test_merge_style_weights_averages():
    merged = merge_style_weights(["dividend", "quality"])
    assert merged["roe"] == (15 + 25) / 2
    # метрика только одного стиля делится пополам
    assert merged["div_yield"] == 35 / 2
    # все ключи известных стилей присутствуют в каталоге весов
    for key in merged:
        assert any(key in style["weights"] for style in SCORING_STYLES.values())


def test_classify_gold():
    assert classify_gold("TGLD", "etf", "ETF") == GOLD_CLASS
    assert classify_gold("akgd", "etf", "ETF") == GOLD_CLASS
    assert classify_gold("GOLD-12.26", "future", "Фьючерсы") == GOLD_CLASS
    assert classify_gold("GLDRUBF", "future", "Фьючерсы") == GOLD_CLASS
    assert classify_gold("SBER", "share", "Акции") == "Акции"
    assert classify_gold("LQDT", "etf", "ETF") == "ETF"
    assert classify_gold("", "etf", "ETF") == "ETF"


def test_classify_position_index_etfs():
    assert classify_position("TMOS", "etf", "ETF") == "Акции"
    assert classify_position("EQMX", "etf", "ETF") == "Акции"
    assert classify_position("SUGB", "etf", "ETF") == "Облигации"
    assert classify_position("VTBB", "etf", "ETF") == "Облигации"
    assert classify_position("TGLD", "etf", "ETF") == GOLD_CLASS
    # неизвестные ETF остаются в классе ETF
    assert classify_position("LQDT", "etf", "ETF") == "ETF"
    # акции и облигации не трогаем
    assert classify_position("SBER", "share", "Акции") == "Акции"
    assert classify_position("SU26233", "bond", "Облигации") == "Облигации"
