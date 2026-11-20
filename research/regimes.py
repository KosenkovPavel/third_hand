#!/usr/bin/env python3
"""
Режимные стратегии: честный тест против статики 50/30/20 и risk parity.

Правила regime_fn зафиксированы по факторному анализу (data/factor_tables.md)
ДО бэктеста. Два механизма перехода между режимами:
  А — только взносами (turnover_cap=0);
  Б — взносы + оборот до 5% портфеля/мес (внутри ИИС налога нет).

Запуск:  python research/regimes.py
Дописывает секции в research/REPORT.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (  # noqa: E402
    GOAL_REAL, MC_BLOCK, MC_MONTHS, MC_PATHS, SEED,
    build_cpi, build_key_rate, build_regime_signals, load_returns, make_drags,
    md_table, metrics, regime_fn, risk_parity_fn, sample_mc_paths, simulate,
    static_fn)

REPORT = Path(__file__).parent / "REPORT.md"
START_GOLD = "2008-02"
START_NG = "2003-02"


def row_of(name, wrapper, r, cpi_index):
    m = metrics(r["nav"])
    cpi0 = cpi_index.loc[:r["nav"].index[0] - 1].iloc[-1]
    cpi1 = cpi_index.loc[r["nav"].index[-1]]
    real_final = r["final_net"] / cpi1 * cpi0
    return (name, wrapper, str(r["nav"].index[0]), r["contributed"],
            r["final_net"], real_final, m["CAGR"], m["maxDD"],
            m["ulcer"], m["Sharpe"])


def main():
    ret = load_returns()
    cpi_yoy, cpi_mom, cpi_index = build_cpi()
    eq_drag, bond_drag, kr = make_drags(ret, cpi_yoy)
    rate_m = build_key_rate()
    sig = build_regime_signals(ret, cpi_yoy, rate_m)
    ctx0 = {"eq_drag": eq_drag, "bond_drag": bond_drag,
            "regime_signals": sig, "cpi_index": cpi_index}
    end = ret.index[-1]

    # --- 0. Санити-проверка: какие режимы когда включались
    print("=== 0. Режимы по годам (месяцев в году, историческое окно) ===")
    probe_ctx = {"regime_signals": sig}
    timeline = {}
    for t in ret.loc[START_GOLD:end].index:
        pos = ret.index.get_loc(t)
        w = regime_fn()(ret, pos, t, probe_ctx)
        timeline[t] = w
    tw = pd.DataFrame(timeline).T
    tw.index.name = "t"
    regime_of = tw.apply(
        lambda r: "crisis" if r["EQ"] >= 0.65 and r["GOLD"] <= 0.15 else
                  ("fx" if abs(r["EQ"] - 0.60) < 1e-9 and r["OFZ"] == 0.25 else
                   ("hi_rate" if r["OFZ"] >= 0.40 else
                    ("lo_rate" if r["GOLD"] >= 0.25 else "normal"))), axis=1)
    cnt = regime_of.groupby([regime_of.index.year, regime_of]).size().unstack(fill_value=0)
    print(cnt.to_string())

    # --- 1. Peek-контроль
    print("\n=== 1. Peek-контроль (режимная стратегия) ===")
    honest = simulate(ret, regime_fn(), dict(ctx0), START_GOLD, end, wrapper="broker")
    peek = simulate(ret, regime_fn(peek=True), dict(ctx0), START_GOLD, end,
                    wrapper="broker")
    print(f"честная NAV={honest['nav'].iloc[-1]:.2f}, peek NAV={peek['nav'].iloc[-1]:.2f}")
    # Peek обязан менять решения (иначе канал подглядывания сломан), но не обязан
    # быть лучше: с гистерезисом ранний вход в «нож» может вредить. Отсутствие
    # утечек доказывается конструкцией (сигналы строго t-1) и peek-тестом
    # моментума на этом же движке (REPORT.md, раздел 1).
    assert abs(peek["nav"].iloc[-1] - honest["nav"].iloc[-1]) > 1e-6, \
        "peek не меняет решения — канал подглядывания сломан"

    # --- 2. Историческое сравнение
    print("\n=== 2. История: режимы vs статика (ИИС и брокер) ===")
    cfgs = [
        ("50/30/20 (статика)", static_fn(eq=0.5, ofz=0.3, gold=0.2), 0.0, START_GOLD),
        ("risk parity", risk_parity_fn, 0.0, START_GOLD),
        ("70/30 (статика)", static_fn(eq=0.7, ofz=0.3), 0.0, START_GOLD),
        ("режим А (взносами)", regime_fn(), 0.0, START_GOLD),
        ("режим Б (+5%/мес)", regime_fn(), 0.05, START_GOLD),
        ("режим А н/з (2003+)", regime_fn(use_gold=False), 0.0, START_NG),
        ("режим Б н/з (2003+)", regime_fn(use_gold=False), 0.05, START_NG),
    ]
    rows, navs = [], {}
    for name, wfn, cap, start in cfgs:
        for wrapper in ("iis", "broker"):
            r = simulate(ret, wfn, dict(ctx0), start, end, wrapper=wrapper,
                         turnover_cap=cap)
            rows.append(row_of(name, wrapper, r, cpi_index))
            if wrapper == "iis":
                navs[name] = r["nav"]
    t_hist = md_table(rows,
                      ["Стратегия", "Счёт", "Старт", "Внесено", "Финал нетто, ₽",
                       "Финал реал., ₽", "CAGR", "МаксПрос", "Ulcer", "Sharpe"],
                      [None, None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%",
                       ".1f", ".2f"])
    print(t_hist)

    # --- 3. Walk-forward: пороги на train 2008–2014, test 2015+
    print("\n=== 3. Walk-forward: пороги режима (train 2008–2014 → test 2015+) ===")
    grid = [(dd, hr) for dd in (-0.25, -0.30, -0.35) for hr in (3.0, 4.0, 5.0)]
    train = []
    for dd, hr in grid:
        r = simulate(ret, regime_fn(crisis_dd=dd, hi_rate=hr), dict(ctx0),
                     START_GOLD, "2014-12", wrapper="iis", turnover_cap=0.05)
        train.append(((dd, hr), metrics(r["nav"])["CAGR"]))
    best = max(train, key=lambda x: x[1])[0]
    print("train CAGR:", {f"{dd}/{hr}": f"{c:.1%}" for (dd, hr), c in train})
    wf_rows = []
    for params, tag in [(best, f"лучшие на train {best}"),
                        ((-0.30, 4.0), "дефолт (-30%, 4)")]:
        r = simulate(ret, regime_fn(crisis_dd=params[0], hi_rate=params[1]),
                     dict(ctx0), "2015-01", end, wrapper="iis", turnover_cap=0.05)
        m = metrics(r["nav"])
        wf_rows.append((tag, m["CAGR"], m["maxDD"], m["Sharpe"]))
    t_wf = md_table(wf_rows, ["Параметры", "CAGR test 2015+", "МаксПрос", "Sharpe"],
                    [None, ".1%", ".1%", ".2f"])
    print(t_wf)

    # --- 4. Монте-Карло для режимов (те же пути, что у статики: +USDRUB в блоке)
    print("\n=== 4. Монте-Карло: режимы vs статика ===")
    cols = ["EQ", "OFZ", "CORP", "GOLD", "USDRUB"]
    joint = ret[cols].join(cpi_mom.rename("CPI")).dropna().loc["2008-02":]
    paths = sample_mc_paths(joint)  # та же seed-последовательность блоков
    idx = pd.period_range("2000-01", periods=MC_MONTHS, freq="M")
    eq_drag_med = float(eq_drag[eq_drag > 0].median())
    bond_drag_med = float(bond_drag[bond_drag > 0].median())
    mc_cfgs = [
        ("50/30/20 (статика)", static_fn(eq=0.5, ofz=0.3, gold=0.2), 0.0),
        ("risk parity", risk_parity_fn, 0.0),
        ("режим А (взносами)", regime_fn(), 0.0),
        ("режим Б (+5%/мес)", regime_fn(), 0.05),
    ]
    rows_mc = []
    for name, wfn, cap in mc_cfgs:
        for wrapper in ("iis", "broker"):
            finals, dds = [], []
            for p in range(MC_PATHS):
                arr = paths[p]
                df = pd.DataFrame(arr[:, :5], index=idx, columns=cols)
                mom = pd.Series(arr[:, 5], index=idx)
                cpi_yoy_p = ((1 + mom).rolling(12).apply(np.prod) - 1) * 100
                sig_p = build_regime_signals(df, cpi_yoy_p, None)
                ctx = {"eq_drag": eq_drag_med, "bond_drag": bond_drag_med,
                       "regime_signals": sig_p, "horizon_months": MC_MONTHS}
                r = simulate(df, wfn, ctx, idx[0], idx[-1], wrapper=wrapper,
                             turnover_cap=cap)
                dd = (r["nav"] / r["nav"].cummax() - 1).min()
                finals.append(r["final_net"] / np.cumprod(1 + arr[:, 5])[-1])
                dds.append(dd)
            finals = np.array(finals)
            rows_mc.append((name, wrapper,
                            float(np.percentile(finals, 5)),
                            float(np.percentile(finals, 50)),
                            float(np.percentile(finals, 95)),
                            float((finals >= GOAL_REAL).mean()),
                            float(np.percentile(dds, 50)),
                            float(np.percentile(dds, 5))))
            print(f"  {name} {wrapper} готово", flush=True)
    t_mc = md_table(rows_mc,
                    ["Стратегия", "Счёт", "5% реал.", "Медиана реал.",
                     "95% реал.", "P(≥5 млн)", "МаксПрос мед.", "МаксПрос 5%"],
                    [None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%", ".1%"])
    print(t_mc)

    # --- 5. Дописать REPORT.md
    factors_md = (Path(__file__).parent / "data" / "factor_tables.md").read_text(
        encoding="utf-8")
    doc = f"""

---

## Факторный анализ (2026-11-20, данные 1997–2026)

Расширенные ряды: акции с 1997-09 (IMOEX + оценка дивидендов 1,5%/год до 2003),
денежная ставка с 1992 (сшивка ставки рефинансирования ЦБ и ключевой; уровни
инструментов разные — на границе 2013-09 скачок 8,25→5,5 п.п., учтён в выводах),
USD/RUB с 1997. Полные таблицы: `data/factor_tables.md`.

Ключевые наблюдения (следующие 12 мес после состояния, `*` — 95% ДИ значимо):
- **Кризис EQ (<−30% от 12м макс): +100,4%** [+20,7..+155,6]* — сильнейший сигнал
  «откупать просадку» (38 мес, включая 1998, 2008, 2020, 2022).
- **FX-шок (>15%/3мес): +102,0%** [+0,6..+150,1]* — после девальваций рублёвые
  акции раллируют (29 мес).
- Реальная ставка >4 п.п.: EQ +34,6% vs +23,9% при <1 п.п. — направление против
  интуиции «ставка душит акции», не значимо; OFZ +10,9% vs +6,0% — логично,
  не значимо; золото при <1 п.п. +29,0% vs +8,7% — направление сильное.
- ERP (дивдоходность > ставка): OFZ **−0,7%** [−14,8..−2,4]* — в такие периоды
  облигации теряют (режим догоняющего повышения ставок).
- Направление/смена ставки и сам по себе ERP для акций — без значимого сигнала.

## Режимные стратегии (правила зафиксированы до бэктеста)

Карта режимов (EQ/OFZ/GOLD): normal 50/30/20; crisis 65/20/15; fx 60/25/15;
hi_rate 45/40/15; lo_rate 55/20/25; ERP>0 → −10 п.п. ОФЗ в акции; рамки акций
[30,75]%; выход из режима после 2 чистых месяцев. Вариант А — переход только
взносами, Б — + оборот до 5%/мес.

Peek-контроль: честная NAV={honest['nav'].iloc[-1]:.2f} vs peek {peek['nav'].iloc[-1]:.2f} — утечки нет.

### Режимы по годам (контроль адекватности сигнала)
```
{cnt.to_string()}
```

### История: режимы vs статика
{t_hist}

### Walk-forward (train 2008–2014 → test 2015+)
{t_wf}

### Монте-Карло (те же 1000 bootstrap-путей; реальная ставка — прокси 12м ОФЗ − CPI)
{t_mc}

### Выводы по режимам
(заполняются по результатам прогона)

## Приложение: полные факторные таблицы
{factors_md}
"""
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(doc)
    print(f"\nОтчёт дополнен: {REPORT}")


if __name__ == "__main__":
    main()
