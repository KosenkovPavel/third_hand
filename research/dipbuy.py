#!/usr/bin/env python3
"""
Эксперименты: форма взносов 400к/год и «вливания на дне» (dip-deploy).

1) Форма взносов 400 тыс./год: равномерно / лимп в январе / «терпеливый лимп»
   (годовой взнос паркуется в ОФЗ и разворачивается при DD < -30% или в декабре
   — дедлайн вычета ИИС) + маршрутизация сверх базы вычета (600к/год).
2) «Порох на дне»: резерв в ОФЗ, перелив в акции по честному триггеру
   DD < -30% (статзначимый сигнал из факторного анализа), транши через
   ограничение оборота. Варианты: резерв 10 п.п., экстремальный тайминг
   (100% ОФЗ -> 100% акции), «вечное ожидание» (негативный контроль).
3) Дизайн новостного скрининга — письменный раздел в отчёте.

Запуск:  python research/dipbuy.py
Дописывает секции в research/REPORT.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (  # noqa: E402
    GOAL_REAL, MC_MONTHS, MC_PATHS, build_cpi, build_key_rate,
    build_regime_signals, load_returns, make_drags, md_table, metrics,
    regime_fn, sample_mc_paths, simulate, static_fn)
from regimes import row_of  # noqa: E402

REPORT = Path(__file__).parent / "REPORT.md"
START_GOLD = "2008-02"
START_NG = "2003-02"
CRISIS_DD = -0.30


# --------------------------------------------------------- helpers ----
def dd_of(ctx, t, peek=False):
    sig = ctx["regime_signals"]
    hist = sig.loc[:t if peek else t - 1]
    if hist.empty:
        return np.nan
    return hist["eq_dd"].iloc[-1]


def dip_deploy_fn(base=(0.45, 0.40, 0.15), crisis=(0.75, 0.10, 0.15),
                  thr=CRISIS_DD, exit_dd=-0.15, exit_months=None, peek=False):
    """Резервная стратегия: base в норме, crisis по триггеру DD < thr.
    Выход: 2 чистых месяца выше exit_dd (и не раньше exit_months, если задано).
    """
    def wfn(rets, pos, t, ctx):
        st = ctx.setdefault("_dip", {"on": False, "clear": 0, "months": 0})
        dd = dd_of(ctx, t, peek)
        crisis_now = np.isfinite(dd) and dd < thr
        if not st["on"]:
            if crisis_now:
                st["on"], st["clear"], st["months"] = True, 0, 0
        else:
            st["months"] += 1
            recovered = np.isfinite(dd) and dd > exit_dd
            long_enough = exit_months is None or st["months"] >= exit_months
            if recovered := (recovered and long_enough):
                st["clear"] += 1
                if st["clear"] >= 2:
                    st["on"] = False
            else:
                st["clear"] = 0
        st["fast"] = st["on"] or st["clear"] > 0
        e, o, g = crisis if st["on"] else base
        return {"EQ": e, "OFZ": o, "GOLD": g}
    return wfn


def eternal_wait_fn(thr=CRISIS_DD, peek=False):
    """Негативный контроль: всё в ОФЗ, при триггере — навсегда 100% акции."""
    def wfn(rets, pos, t, ctx):
        st = ctx.setdefault("_wait", {"on": False})
        if not st["on"]:
            dd = dd_of(ctx, t, peek)
            if np.isfinite(dd) and dd < thr:
                st["on"] = True
        st["fast"] = st["on"]
        return {"EQ": 1.0, "OFZ": 0.0} if st["on"] else {"EQ": 0.0, "OFZ": 1.0}
    return wfn


def patient_lump_fn(base=(0.5, 0.3, 0.2), thr=CRISIS_DD, peek=False):
    """Терпеливый лимп: взнос паркуется в ОФЗ; развёртывание при DD < thr
    или принудительно в декабре (дедлайн вычета ИИС)."""
    def wfn(rets, pos, t, ctx):
        st = ctx.setdefault("_park", {"amt": 0.0})
        st["amt"] += ctx.get("inject", 0.0)
        crisis = np.isfinite(dd_of(ctx, t, peek)) and dd_of(ctx, t, peek) < thr
        if crisis or t.month == 12:
            st["amt"] = 0.0
        V = ctx.get("V", 0.0)
        park_w = min(st["amt"] / V, 0.5) if V > 0 else 0.0
        st["fast"] = bool(crisis or (t.month == 12 and park_w > 0.01))
        e, o, g = base
        s = 1 - park_w
        return {"EQ": e * s, "OFZ": o * s + park_w, "GOLD": g * s}
    return wfn


def dip_cap(fast=0.34, slow=0.05):
    def cap(t, ctx):
        for key in ("_dip", "_wait", "_park"):
            st = ctx.get(key)
            if st and st.get("fast"):
                return fast
        return slow
    return cap


LUMP_JAN = lambda t: 400_000.0 if t.month == 1 else 0.0          # noqa: E731
EVEN_400 = lambda t: 400_000.0 / 12
EVEN_120 = lambda t: 10_000.0


def mc_run(ret, cpi_mom, cfgs, eq_drag_med, bond_drag_med):
    cols = ["EQ", "OFZ", "CORP", "GOLD", "USDRUB"]
    joint = ret[cols].join(cpi_mom.rename("CPI")).dropna().loc["2008-02":]
    paths = sample_mc_paths(joint)
    idx = pd.period_range("2000-01", periods=MC_MONTHS, freq="M")
    rows = []
    for name, wfn, cap, contrib in cfgs:
        finals, dds = [], []
        for p in range(MC_PATHS):
            arr = paths[p]
            df = pd.DataFrame(arr[:, :5], index=idx, columns=cols)
            mom = pd.Series(arr[:, 5], index=idx)
            cpi_yoy_p = ((1 + mom).rolling(12).apply(np.prod) - 1) * 100
            ctx = {"eq_drag": eq_drag_med, "bond_drag": bond_drag_med,
                   "regime_signals": build_regime_signals(df, cpi_yoy_p, None),
                   "horizon_months": MC_MONTHS}
            r = simulate(df, wfn, ctx, idx[0], idx[-1], wrapper="iis",
                         contrib=contrib, turnover_cap=cap)
            dds.append((r["nav"] / r["nav"].cummax() - 1).min())
            finals.append(r["final_net"] / np.cumprod(1 + arr[:, 5])[-1])
        finals = np.array(finals)
        rows.append((name, float(np.percentile(finals, 5)),
                     float(np.percentile(finals, 50)),
                     float(np.percentile(finals, 95)),
                     float((finals >= GOAL_REAL).mean()),
                     float(np.percentile(dds, 50))))
        print(f"  МК: {name} готово", flush=True)
    return md_table(rows, ["Стратегия (ИИС)", "5% реал.", "Медиана реал.",
                           "95% реал.", "P(≥5 млн)", "МаксПрос мед."],
                    [None, ",.0f", ",.0f", ",.0f", ".1%", ".1%"])


def main():
    ret = load_returns()
    cpi_yoy, cpi_mom, cpi_index = build_cpi()
    eq_drag, bond_drag, kr = make_drags(ret, cpi_yoy)
    sig = build_regime_signals(ret, cpi_yoy, build_key_rate())
    ctx0 = {"eq_drag": eq_drag, "bond_drag": bond_drag,
            "regime_signals": sig, "cpi_index": cpi_index}
    end = ret.index[-1]
    base_5030 = static_fn(eq=0.5, ofz=0.3, gold=0.2)

    # --- 0. Санити: нулевая волатильность — лимп в январе не хуже равномерного
    print("=== 0. Санити-проверки ===")
    idx_s = pd.period_range("2010-01", periods=60, freq="M")
    flat = pd.DataFrame({c: [0.008] * 60 for c in
                         ["EQ", "OFZ", "GOLD", "CORP", "CASH"]}, index=idx_s)
    flat["USDRUB"] = 0.0
    sig_flat = build_regime_signals(flat, pd.Series(5.0, index=idx_s),
                                    pd.Series(7.0, index=idx_s))
    ctx_flat = {"eq_drag": 0.0, "bond_drag": 0.0, "regime_signals": sig_flat}
    lump = simulate(flat, base_5030, dict(ctx_flat), idx_s[0], idx_s[-1],
                    wrapper="iis", contrib=LUMP_JAN)
    even = simulate(flat, base_5030, dict(ctx_flat), idx_s[0], idx_s[-1],
                    wrapper="iis", contrib=EVEN_400)
    print(f"нулевая волатильность: лимп-январь {lump['final_net']:,.0f} vs "
          f"равномерно {even['final_net']:,.0f} (лимп должен быть не хуже)")
    assert lump["final_net"] >= even["final_net"] - 1

    # «вечное ожидание» без кризиса (2016–2019) должно проиграть DCA
    w_wait = simulate(ret, eternal_wait_fn(), dict(ctx0), "2016-01", "2019-12",
                      wrapper="iis", turnover_cap=dip_cap())
    w_dca = simulate(ret, base_5030, dict(ctx0), "2016-01", "2019-12",
                     wrapper="iis")
    print(f"2016–2019 (без кризиса): вечное ожидание NAV {w_wait['nav'].iloc[-1]:.2f} "
          f"vs 50/30/20 {w_dca['nav'].iloc[-1]:.2f} (ожидание должно проиграть)")

    # peek-контроль
    honest = simulate(ret, dip_deploy_fn(), dict(ctx0), START_GOLD, end,
                      wrapper="iis", turnover_cap=dip_cap())
    peek = simulate(ret, dip_deploy_fn(peek=True), dict(ctx0), START_GOLD, end,
                    wrapper="iis", turnover_cap=dip_cap())
    print(f"peek-контроль dip-deploy: честная {honest['nav'].iloc[-1]:.2f} vs "
          f"peek {peek['nav'].iloc[-1]:.2f} (обязаны различаться)")
    assert abs(peek["nav"].iloc[-1] - honest["nav"].iloc[-1]) > 1e-6

    # --- 1. Форма взносов 400к/год
    print("\n=== 1. Форма взносов при 400 тыс./год (ИИС, 2008+) ===")
    rows = []
    cfgs1 = [
        ("50/30/20 равномерно", base_5030, 0.0, EVEN_400),
        ("50/30/20 лимп-январь", base_5030, 0.0, LUMP_JAN),
        ("50/30/20 терпеливый лимп", patient_lump_fn(), dip_cap(), LUMP_JAN),
        ("режим А равномерно", regime_fn(), 0.0, EVEN_400),
        ("режим А лимп-январь", regime_fn(), 0.0, LUMP_JAN),
        ("режим А терпеливый", patient_lump_fn(), dip_cap(), LUMP_JAN),
    ]
    navs1 = {}
    for name, wfn, cap, contrib in cfgs1:
        r = simulate(ret, wfn, dict(ctx0), START_GOLD, end, wrapper="iis",
                     contrib=contrib, turnover_cap=cap)
        rows.append(row_of(name, "iis", r, cpi_index))
        navs1[name] = r["nav"]
    t1 = md_table(rows, ["Стратегия", "Счёт", "Старт", "Внесено",
                         "Финал нетто, ₽", "Финал реал., ₽", "CAGR",
                         "МаксПрос", "Ulcer", "Sharpe"],
                  [None, None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%",
                   ".1f", ".2f"])
    print(t1)

    print("\n--- Сверх базы вычета: 600 тыс./год ---")
    r_iis = simulate(ret, base_5030, dict(ctx0), START_GOLD, end,
                     wrapper="iis", contrib=lambda t: 400_000.0 / 12)
    r_br = simulate(ret, base_5030, dict(ctx0), START_GOLD, end,
                    wrapper="broker", contrib=lambda t: 200_000.0 / 12)
    r_all_br = simulate(ret, base_5030, dict(ctx0), START_GOLD, end,
                        wrapper="broker", contrib=lambda t: 600_000.0 / 12)
    r_all_iis = simulate(ret, base_5030, dict(ctx0), START_GOLD, end,
                         wrapper="iis", contrib=lambda t: 600_000.0 / 12)
    t1b = md_table([
        ("гибрид 400к ИИС + 200к брокер", r_iis["final_net"] + r_br["final_net"],
         r_iis["tax"] + r_br["tax"]),
        ("всё 600к на брокерский", r_all_br["final_net"], r_all_br["tax"]),
        ("всё 600к на ИИС (вычет всё равно с 400к)", r_all_iis["final_net"],
         r_all_iis["tax"]),
    ], ["Маршрут 600к/год", "Финал нетто, ₽", "Налог, ₽"], [None, ",.0f", ",.0f"])
    print(t1b)

    # --- 2. Порох на дне
    print("\n=== 2. «Порох на дне» (ИИС и брокер, 2008+; экстремум — с 2003) ===")
    cfgs2 = [
        ("50/30/20 (бенчмарк)", base_5030, 0.0, START_GOLD),
        ("режим А (бенчмарк)", regime_fn(), 0.0, START_GOLD),
        ("резерв-10 (45/40/15→75/10/15)", dip_deploy_fn(), dip_cap(), START_GOLD),
        ("экстремальный тайминг", dip_deploy_fn(base=(0.0, 1.0, 0.0),
         crisis=(1.0, 0.0, 0.0), exit_months=12), dip_cap(), START_GOLD),
        ("вечное ожидание", eternal_wait_fn(), dip_cap(), START_GOLD),
        ("экстремум н/з (2003+)", dip_deploy_fn(base=(0.0, 1.0, 0.0),
         crisis=(1.0, 0.0, 0.0), exit_months=12), dip_cap(), START_NG),
        ("вечное ожидание н/з (2003+)", eternal_wait_fn(), dip_cap(), START_NG),
    ]
    rows2 = []
    for name, wfn, cap, start in cfgs2:
        for wrapper in ("iis", "broker"):
            r = simulate(ret, wfn, dict(ctx0), start, end, wrapper=wrapper,
                         turnover_cap=cap)
            rows2.append(row_of(name, wrapper, r, cpi_index))
    t2 = md_table(rows2, ["Стратегия", "Счёт", "Старт", "Внесено",
                          "Финал нетто, ₽", "Финал реал., ₽", "CAGR",
                          "МаксПрос", "Ulcer", "Sharpe"],
                  [None, None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%",
                   ".1f", ".2f"])
    print(t2)

    # walk-forward порога развёртывания
    print("\n--- Walk-forward порога (train 2008–2014 → test 2015+) ---")
    train = []
    for thr in (-0.25, -0.30, -0.35):
        r = simulate(ret, dip_deploy_fn(thr=thr), dict(ctx0), START_GOLD,
                     "2014-12", wrapper="iis", turnover_cap=dip_cap())
        train.append((thr, metrics(r["nav"])["CAGR"]))
    best_thr = max(train, key=lambda x: x[1])[0]
    print("train CAGR:", {f"{thr}": f"{c:.1%}" for thr, c in train})
    wf_rows = []
    for thr, tag in [(best_thr, f"лучший на train {best_thr}"), (-0.30, "дефолт -30%")]:
        r = simulate(ret, dip_deploy_fn(thr=thr), dict(ctx0), "2015-01", end,
                     wrapper="iis", turnover_cap=dip_cap())
        m = metrics(r["nav"])
        wf_rows.append((tag, m["CAGR"], m["maxDD"], m["Sharpe"]))
    t2b = md_table(wf_rows, ["Порог", "CAGR test 2015+", "МаксПрос", "Sharpe"],
                   [None, ".1%", ".1%", ".2f"])
    print(t2b)

    # --- 3. Монте-Карло (ИИС)
    print("\n=== 3. Монте-Карло (1000 путей × 20 лет, ИИС) ===")
    eq_drag_med = float(eq_drag[eq_drag > 0].median())
    bond_drag_med = float(bond_drag[bond_drag > 0].median())
    t3 = mc_run(ret, cpi_mom, [
        ("50/30/20 равномерно 10к", base_5030, 0.0, EVEN_120),
        ("50/30/20 лимп-январь 400к", base_5030, 0.0, LUMP_JAN),
        ("терпеливый лимп 400к", patient_lump_fn(), dip_cap(), LUMP_JAN),
        ("резерв-10 10к", dip_deploy_fn(), dip_cap(), EVEN_120),
        ("экстремальный тайминг 10к", dip_deploy_fn(base=(0.0, 1.0, 0.0),
         crisis=(1.0, 0.0, 0.0), exit_months=12), dip_cap(), EVEN_120),
        ("вечное ожидание 10к", eternal_wait_fn(), dip_cap(), EVEN_120),
    ], eq_drag_med, bond_drag_med)
    print(t3)

    # --- 4. REPORT.md
    doc = f"""

---

## Форма взносов при 400 тыс./год (окно 2008+, ИИС)

Санити: при нулевой волатильности лимп-январь ≥ равномерного ({lump['final_net']:,.0f} vs {even['final_net']:,.0f}) — даты взносов корректны.

{t1}

### Маршрутизация сверх базы вычета (600 тыс./год, 2008+)
{t1b}

## «Порох на дне»: резерв под кризис (честный триггер DD < −30%)

Контроль: «вечное ожидание» в безкризисные 2016–2019 проигрывает DCA
({w_wait['nav'].iloc[-1]:.2f} vs {w_dca['nav'].iloc[-1]:.2f}) — модель штрафует
за сидение в кэше. Peek-контроль: честная {honest['nav'].iloc[-1]:.2f} vs
peek {peek['nav'].iloc[-1]:.2f} (решения различаются, утечек нет).

{t2}

### Walk-forward порога развёртывания
{t2b}

### Монте-Карло
{t3}

## Новостной скрининг критических состояний: дизайн (без бэктеста)

Количественная проверка невозможна честно: размеченного архива новостей
с 1997 года нет, а подгонять правила по подвыборке заголовков — значит
выдумать результат. Поэтому рекомендация архитектурная:

1. **LLM — слой объяснения, не слой сигнала.** Аллокацию меняют только
   механические правила с доказанной статистикой (DD < −30%, FX-шок).
   Это продолжение принципа проекта «агент не считает»: агент не решает.
2. **Что строить:** сбор (RSS RBC/Интерфакс/Коммерсантъ, ведомости, ЦБ) →
   дедупликация → LLM-дайджест два раза в день со структурированным блоком:
   `системный_риск: низкий/средний/высокий`, `затронутые сектора`,
   `суть в 3 предложениях`, `что было при похожих событиях (из нашего
   факторного анализа)`. Пользователь видит флаг рядом с механическим режимом.
3. **Валидация без бэктеста:** журнал флагов (jsonl с датой и уровнем) +
   автоматическая ретроспективная сверка через 3/6/12 мес — тот же формат
   условных доходностей, что и в факторном анализе. Через 1–2 года журнала
   появятся честные данные для решения, достоин ли флаг веса в аллокации.
4. **Что уже есть:** `src/news.py` (дельты целей аналитиков, инсайдеры, RSI,
   RSS Google News с тональностью по словарю). Для хакатона этого достаточно
   как демо-витрина; словарную тональность заменить на LLM-разметку —
   косметическое изменение `_tone_of`.

### Выводы по взносам и «пороху»
(заполняются по результатам прогона)
"""
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(doc)
    print(f"\nОтчёт дополнен: {REPORT}")


if __name__ == "__main__":
    main()
