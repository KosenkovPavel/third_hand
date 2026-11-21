"""Каталог фундаментальных метрик и конфигурируемый скоринг (ADR-0011, фаза 8).

Каждая метрика — правило превращения значения из ``StatisticResponse`` в баллы
0–100 (100 = отлично, 0 = плохо). Пользователь выбирает набор метрик и их веса;
итоговый рейтинг компании — взвешенное среднее выбранных метрик.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    description: str


METRICS: dict[str, Metric] = {
    "pe": Metric("pe", "P/E", "низкий P/E — акция недорого"),
    "pb": Metric("pb", "P/B", "низкий P/B — дёшево относительно активов"),
    "ps": Metric("ps", "P/S", "низкий P/S — дёшево относительно выручки"),
    "ev_ebitda": Metric("ev_ebitda", "EV/EBITDA", "низкий — компания недорого"),
    "roe": Metric("roe", "ROE", "высокий ROE — отдача на капитал"),
    "roa": Metric("roa", "ROA", "высокий ROA — эффективность активов"),
    "roic": Metric("roic", "ROIC", "высокий ROIC — отдача на инвест. капитал"),
    "net_margin": Metric("net_margin", "Маржа", "высокая чистая маржа — прибыльность"),
    "div_yield": Metric("div_yield", "Див. доходность", "высокая — «деньги сейчас»"),
    "payout": Metric("payout", "Payout", "30–80% — устойчивые выплаты"),
    "rev_growth": Metric("rev_growth", "Рост выручки 1г", "рост выручки — бизнес растёт"),
    "growth_3y": Metric("growth_3y", "Рост выручки 3г", "стабильный рост за 3 года"),
    "debt_eq": Metric("debt_eq", "Долг/Капитал", "низкий долг — меньше риска"),
    "debt_ebitda": Metric("debt_ebitda", "Долг/EBITDA", "низкий — долг обслуживаемый"),
    "beta": Metric("beta", "Бета", "низкая бета — меньше волатильность"),
}

# Веса по умолчанию — повторяют прежний захардкоженный скоринг
# (P/E 25, дивы 25, ROE 15, рост 10, payout 10, долг 5). Остальные метрики
# доступны в каталоге, но по умолчанию не участвуют — пользователь может добавить.
DEFAULT_WEIGHTS: dict[str, float] = {
    "pe": 25,
    "div_yield": 25,
    "roe": 15,
    "rev_growth": 10,
    "payout": 10,
    "debt_eq": 5,
}

# Стили скрининга для визарда первого запуска: пользователь выбирает, на что
# ориентироваться при подборе акций; итоговые веса — среднее выбранных стилей.
# «balanced» — дефолт («самое важное»: цена, дивиденды, качество, рост, долг).
SCORING_STYLES: dict[str, dict] = {
    "balanced": {"label": "Сбалансированный", "weights": DEFAULT_WEIGHTS},
    "dividend": {
        "label": "Дивидендный",
        "weights": {"div_yield": 35, "payout": 20, "pe": 15, "roe": 15,
                    "debt_eq": 10, "beta": 5},
    },
    "value": {
        "label": "Недооценка",
        "weights": {"pe": 30, "pb": 20, "ps": 15, "ev_ebitda": 20, "debt_eq": 15},
    },
    "quality": {
        "label": "Качество",
        "weights": {"roe": 25, "roic": 25, "net_margin": 15, "growth_3y": 20,
                    "debt_eq": 15},
    },
    "growth": {
        "label": "Рост",
        "weights": {"rev_growth": 30, "growth_3y": 30, "roe": 15, "pe": 15,
                    "net_margin": 10},
    },
    "calm": {
        "label": "Спокойствие",
        "weights": {"beta": 30, "div_yield": 20, "debt_eq": 20, "payout": 15,
                    "roe": 15},
    },
}
DEFAULT_SCORING_STYLE = "balanced"

# Бытовые варианты шага «скрининг» в визарде: пользователь выбирает намерение,
# а не метрики. Порядок списка = нумерация на экране (Enter = первый вариант).
SCORING_PLAIN_OPTIONS: list[dict] = [
    {
        "label": "Не знаю / выбирайте за меня (рекомендуем)",
        "hint": "проверенный набор: недорогие, с дивидендами, качественные, растущие компании",
        "styles": ["balanced"],
    },
    {
        "label": "Чтобы регулярно платили",
        "hint": "дивидендные акции",
        "styles": ["dividend"],
    },
    {
        "label": "Дёшево и надёжно",
        "hint": "недооценённые качественные компании",
        "styles": ["value", "quality"],
    },
    {
        "label": "Рост любой ценой",
        "hint": "быстрорастущие компании, может быть дорого",
        "styles": ["growth"],
    },
]


def merge_style_weights(styles: list[str]) -> dict[str, float]:
    """Среднее весов выбранных стилей (взвешенный скоринг сам нормирует)."""
    chosen = [SCORING_STYLES[s]["weights"] for s in styles if s in SCORING_STYLES]
    if not chosen:
        return dict(DEFAULT_WEIGHTS)
    merged: dict[str, float] = {}
    for weights in chosen:
        for key, value in weights.items():
            merged[key] = merged.get(key, 0.0) + value / len(chosen)
    return merged


def metric_score(metric: str, value: float) -> tuple[float, str, str]:
    """Баллы 0–100 для значения метрики + причина + риск.

    Возвращает ``(score, note, risk)``: ``note`` — короткое пояснение,
    ``risk`` — предупреждение (или пустая строка).
    """
    if metric == "pe":
        if value <= 10:
            return 100, f"P/E {value:.1f} — недорого", ""
        if value <= 18:
            return 70, f"P/E {value:.1f} — адекватно", ""
        if value <= 30:
            return 40, f"P/E {value:.1f} — дорого", ""
        if value > 0:
            return 10, f"P/E {value:.1f} — очень дорого", ""
        return 0, "", "Убыточная компания (P/E < 0)"

    if metric == "pb":
        if value <= 1:
            return 100, f"P/B {value:.1f} — недорого", ""
        if value <= 2:
            return 70, f"P/B {value:.1f} — адекватно", ""
        if value <= 4:
            return 40, f"P/B {value:.1f} — дорого", ""
        return 10, f"P/B {value:.1f} — очень дорого", ""

    if metric == "ps":
        if value <= 1:
            return 100, f"P/S {value:.1f} — недорого", ""
        if value <= 2:
            return 70, f"P/S {value:.1f} — адекватно", ""
        if value <= 4:
            return 40, f"P/S {value:.1f} — дорого", ""
        return 10, f"P/S {value:.1f} — очень дорого", ""

    if metric == "ev_ebitda":
        if value <= 5:
            return 100, f"EV/EBITDA {value:.1f} — недорого", ""
        if value <= 10:
            return 70, f"EV/EBITDA {value:.1f} — адекватно", ""
        if value <= 15:
            return 40, f"EV/EBITDA {value:.1f} — дорого", ""
        return 10, f"EV/EBITDA {value:.1f} — очень дорого", ""

    if metric == "roe":
        if value >= 20:
            return 100, f"ROE {value:.1f}% — отличная", ""
        if value >= 15:
            return 80, f"ROE {value:.1f}% — хорошая", ""
        if value >= 8:
            return 50, f"ROE {value:.1f}% — нормальная", ""
        if value > 0:
            return 20, f"ROE {value:.1f}% — слабая", ""
        return 0, "", f"ROE {value:.1f}% — отрицательная"

    if metric == "roa":
        if value >= 15:
            return 100, f"ROA {value:.1f}% — отличная", ""
        if value >= 8:
            return 70, f"ROA {value:.1f}% — хорошая", ""
        if value >= 3:
            return 40, f"ROA {value:.1f}% — средняя", ""
        return 10, f"ROA {value:.1f}% — слабая", ""

    if metric == "roic":
        if value >= 20:
            return 100, f"ROIC {value:.1f}% — отличный", ""
        if value >= 12:
            return 70, f"ROIC {value:.1f}% — хороший", ""
        if value >= 6:
            return 40, f"ROIC {value:.1f}% — средний", ""
        return 10, f"ROIC {value:.1f}% — слабый", ""

    if metric == "net_margin":
        if value >= 20:
            return 100, f"Маржа {value:.1f}% — высокая", ""
        if value >= 10:
            return 70, f"Маржа {value:.1f}% — хорошая", ""
        if value >= 5:
            return 40, f"Маржа {value:.1f}% — средняя", ""
        return 10, f"Маржа {value:.1f}% — низкая", ""

    if metric == "div_yield":
        if value >= 8:
            return 100, f"Див. доходность {value:.1f}% — отличная", ""
        if value >= 5:
            return 80, f"Див. доходность {value:.1f}% — хорошая", ""
        if value >= 3:
            return 50, f"Див. доходность {value:.1f}%", ""
        if value > 0:
            return 20, f"Див. доходность {value:.1f}% — невысокая", ""
        return 0, "Дивиденды не начисляются", ""

    if metric == "payout":
        if 30 <= value <= 80:
            return 100, f"Payout {value:.0f}% — устойчивый", ""
        if value > 100:
            return 10, "", f"Payout {value:.0f}% — выплачивают больше, чем заработали"
        if 0 < value < 30:
            return 60, f"Payout {value:.0f}% — консервативный", ""
        return 40, f"Payout {value:.0f}%", ""

    if metric == "rev_growth":
        if value > 20:
            return 100, f"Выручка +{value:.1f}% за год — растёт", ""
        if value > 10:
            return 75, f"Выручка +{value:.1f}% за год — растёт", ""
        if value > 0:
            return 50, f"Выручка +{value:.1f}% за год", ""
        if value > -5:
            return 20, f"Выручка {value:.1f}% за год — снижается", ""
        return 0, "", f"Выручка {value:.1f}% за год — резко падает"

    if metric == "growth_3y":
        if value > 15:
            return 100, f"Рост 3г {value:.1f}% — сильный", ""
        if value > 8:
            return 70, f"Рост 3г {value:.1f}% — хороший", ""
        if value > 0:
            return 40, f"Рост 3г {value:.1f}%", ""
        return 10, f"Рост 3г {value:.1f}% — слабый", ""

    if metric == "debt_eq":
        if value <= 50:
            return 100, f"Долг/капитал {value:.0f}% — низкий", ""
        if value <= 150:
            return 60, f"Долг/капитал {value:.0f}% — умеренный", ""
        if value <= 300:
            return 30, "", f"Долг/капитал {value:.0f}% — высокий"
        return 0, "", f"Долг/капитал {value:.0f}% — критический"

    if metric == "debt_ebitda":
        if value <= 2:
            return 100, f"Долг/EBITDA {value:.1f} — низкий", ""
        if value <= 4:
            return 60, f"Долг/EBITDA {value:.1f} — умеренный", ""
        if value <= 6:
            return 30, "", f"Долг/EBITDA {value:.1f} — высокий"
        return 0, "", f"Долг/EBITDA {value:.1f} — критический"

    if metric == "beta":
        if value <= 0.8:
            return 100, f"Бета {value:.2f} — низкая", ""
        if value <= 1.2:
            return 60, f"Бета {value:.2f} — умеренная", ""
        if value <= 1.5:
            return 30, f"Бета {value:.2f} — высокая", ""
        return 10, "", f"Бета {value:.2f} — очень высокая"

    return 50, "", ""


def weighted_score(values: dict, weights: dict) -> tuple[float, list[str], list[str]]:
    """Взвешенный скоринг компании.

    ``values`` — {метрика: значение}, ``weights`` — {метрика: вес}.
    Возвращает ``(score 0–100, причины, риски)``. Метрики с весом ≤ 0 или
    отсутствующим значением пропускаются.
    """
    total_w = 0.0
    total = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    for metric, weight in weights.items():
        if weight <= 0 or metric not in values:
            continue
        value = values[metric]
        if value is None:
            continue
        pts, note, risk = metric_score(metric, value)
        total += weight * pts
        total_w += weight
        if note:
            reasons.append(note)
        if risk:
            risks.append(risk)
    if total_w <= 0:
        return 50.0, reasons, risks
    return total / total_w, reasons, risks
