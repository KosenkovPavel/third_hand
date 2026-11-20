#!/usr/bin/env python3
"""
Факторный анализ: что исторически предсказывало доходности на РФ-рынке.

Для каждого сигнала (состояние мира на конец месяца t, только прошлые данные)
считаем распределение ПОСЛЕДУЮЩИХ 3/6/12-мес доходностей классов активов.
Статзначимость — block bootstrap (12 мес): 95% ДИ разницы средних
«состояние vs вся выборка».

Запуск:  python research/factors.py
Результат: research/data/factor_tables.md + печать в stdout
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import (DATA, build_cpi, build_key_rate, load_returns)  # noqa: E402

OUT = Path(__file__).parent / "data" / "factor_tables.md"
SEED = 42
N_BOOT = 2000
BLOCK = 12


# --------------------------------------------------------- сигналы ----
def forward_returns(ret: pd.Series, horizons=(3, 6, 12)) -> pd.DataFrame:
    """Доходность следующих h месяцев после t (без данных самого t)."""
    out = {}
    for h in horizons:
        # prod(1+r[t+1..t+h]) - 1: reversed rolling по будущему окну
        fwd = (1 + ret).iloc[::-1].rolling(h).apply(np.prod).iloc[::-1].shift(-1) - 1
        out[h] = fwd
    return pd.DataFrame(out)


def build_signals(ret, cpi_yoy):
    rate = build_key_rate()
    sig = pd.DataFrame(index=ret.index)
    sig["rate"] = rate.reindex(ret.index).ffill()
    sig["real_rate"] = sig["rate"] - cpi_yoy.reindex(ret.index)
    sig["rate_chg6m"] = sig["rate"] - sig["rate"].shift(6)
    # просадка EQ от 12-мес максимума (по уровням через кумулятивную доходность)
    eq_level = (1 + ret["EQ"].fillna(0)).cumprod()
    sig["eq_dd"] = eq_level / eq_level.rolling(12, min_periods=6).max() - 1
    sig["fx_3m"] = (1 + ret["USDRUB"].fillna(0)).rolling(3).apply(np.prod) - 1 \
        if "USDRUB" in ret else np.nan
    # до 2004 дивдоходность не вычисляется из MCFTR/IMOEX — та же оценка 1,5%/год
    sig["erp"] = ret["DIV_YIELD"].fillna(0.015) * 100 - sig["rate"]  # в п.п.
    return sig


def state_labels(sig):
    states = {}
    rr = sig["real_rate"]
    states["Реальная ставка"] = pd.Series(
        np.where(rr > 4, "высокая (>4)", np.where(rr < 1, "низкая (<1)", "норма")),
        index=sig.index)
    dd = sig["eq_dd"]
    states["Просадка EQ"] = pd.Series(
        np.where(dd > -0.15, "норма (0..-15%)",
                 np.where(dd > -0.30, "стресс (-15..-30%)", "кризис (<-30%)")),
        index=sig.index)
    fx = sig["fx_3m"]
    states["FX-шок"] = pd.Series(np.where(fx > 0.15, "девальвация >15%/3мес", "нет"),
                                 index=sig.index)
    rc = sig["rate_chg6m"]
    states["Ставка 6 мес"] = pd.Series(
        np.where(rc > 0.5, "повышение", np.where(rc < -0.5, "снижение", "пауза")),
        index=sig.index)
    erp = sig["erp"]
    states["ERP (дивы − ставка)"] = pd.Series(
        np.where(erp > 0, "акции дёшевы (>0)", "акции дороги (<0)"), index=sig.index)
    return states


# ------------------------------------------------- статистика ----
def boot_ci_diff(states_bin, fwd, n=N_BOOT, seed=SEED):
    """95% ДИ разницы средних (состояние vs вся выборка), block bootstrap."""
    rng = np.random.default_rng(seed)
    mask = ~np.isnan(fwd)
    s, f = states_bin[mask], fwd[mask]
    n = len(f)
    if n < 36 or s.sum() < 12:
        return np.nan, np.nan
    nb = n // BLOCK
    diffs = []
    f_all_mean = f.mean()
    for _ in range(N_BOOT):
        starts = rng.integers(0, n - BLOCK, size=nb)
        idx = np.concatenate([np.arange(x, x + BLOCK) for x in starts])
        sel = s[idx]
        if sel.sum() < 6:
            continue
        diffs.append(f[idx][sel].mean() - f_all_mean)
    if len(diffs) < 100:
        return np.nan, np.nan
    return tuple(np.percentile(diffs, [2.5, 97.5]))


def factor_table(fwd: pd.DataFrame, state: pd.Series, asset_name: str):
    rows = []
    full = {h: fwd[h].mean() for h in fwd.columns}
    for label in state.dropna().unique():
        m = (state == label).values
        row = [label, int(m.sum())]
        for h in fwd.columns:
            f = fwd[h].values.astype(float)
            cond = f[m & ~np.isnan(f)]
            row.append(f"{cond.mean():+.1%}" if len(cond) else "—")
        lo, hi = boot_ci_diff(m, fwd[12].values.astype(float)
                              if 12 in fwd.columns else np.array([]))
        sig_mark = "*" if np.isfinite(lo) and (lo > 0 or hi < 0) else ""
        row.append(f"{lo:+.1%}..{hi:+.1%}{sig_mark}" if np.isfinite(lo) else "—")
        rows.append(row)
    rows.append(["вся выборка", int(state.notna().sum())] +
                [f"{full[h]:+.1%}" for h in fwd.columns] + [""])
    hdr = ["Состояние", "n мес"] + [f"след.{h}м" for h in fwd.columns] + \
          ["95% ДИ Δ средн. (12м)"]
    return md_table(rows, hdr)


def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def main():
    ret = load_returns()
    # USD/RUB для FX-сигнала
    lv = pd.read_csv(DATA / "monthly_levels.csv", index_col=0, parse_dates=True)
    lv.index = lv.index.to_period("M")
    ret["USDRUB"] = lv["USDRUB"].pct_change()
    cpi_yoy, _, _ = build_cpi()
    sig = build_signals(ret, cpi_yoy)
    states = state_labels(sig)

    doc = ["# Факторный анализ: условные доходности (1997–2026)",
           "",
           "Состояние мира на конец месяца t → средние ПОСЛЕДУЮЩИЕ доходности.",
           "95% ДИ — block bootstrap разницы средних «состояние vs вся выборка» "
           "для 12-мес горизонта; `*` — статзначимое отличие.", ""]
    for asset, start in [("EQ", "1997-10"), ("OFZ", "2003-02"), ("GOLD", "2008-02")]:
        fwd = forward_returns(ret[asset].loc[start:])
        sig_a = sig.loc[fwd.index]
        doc.append(f"\n## {asset} (с {start})\n")
        for sname, s in states.items():
            doc.append(f"\n### {sname}\n")
            doc.append(factor_table(fwd, s.loc[fwd.index], asset))
    text = "\n".join(doc)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
