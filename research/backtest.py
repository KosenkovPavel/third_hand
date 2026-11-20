#!/usr/bin/env python3
"""
Бэктест стратегий распределения активов для «рядового инвестора» на РФ-рынке.

Модель инвестора: пополнение 10 000 ₽/мес, ребалансировка прежде всего
взносами, полная ребалансировка раз в год (январь) при отклонении > 5 п.п.
Комиссия 0,05% с оборота.

Обёртки счетов:
  broker     — НДФЛ 13% на дивиденды/купоны ежегодно (drag) + 13% на прирост
               при изъятии;
  broker_ldv — то же, но прирост акций освобождён (ЛДВ, >3 лет владения;
               приближение: налог на долю прибыли не из акций);
  iis        — вычет 13% на взносы (до 400 тыс./год) реинвестируется,
               при закрытии прибыль освобождена до 30 млн.

Запуск:  python research/backtest.py
Результат: research/REPORT.md, research/figures/*.png
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).parent / "data"
FIG = Path(__file__).parent / "figures"
REPORT = Path(__file__).parent / "REPORT.md"

MONTHLY_CONTRIB = 10_000.0
IIS_DEDUCTION_RATE = 0.13
IIS_DEDUCTION_BASE = 400_000.0
IIS_EXEMPT_CAP = 30_000_000.0
NDFL = 0.13
COMMISSION = 0.0005
REBAL_BAND = 0.05
GOAL_REAL = 5_000_000.0
MC_PATHS = 1000
MC_MONTHS = 240
MC_BLOCK = 12
SEED = 42

# Инфляция г/г до начала ряда ЦБ (сен 2013) — годовые значения Росстата,
# продублированные по месяцам (приближение).
CPI_BACKFILL = {1997: 11.0, 1998: 84.4, 1999: 36.5, 2000: 20.2, 2001: 18.6,
                2002: 15.1, 2003: 12.0, 2004: 11.7, 2005: 10.9, 2006: 9.0,
                2007: 11.9, 2008: 13.3, 2009: 8.8, 2010: 8.8, 2011: 6.1,
                2012: 6.6, 2013: 6.5}


# ------------------------------------------------------------- данные ----
def load_returns() -> pd.DataFrame:
    lv = pd.read_csv(DATA / "monthly_levels.csv", index_col=0, parse_dates=True)
    lv.index = lv.index.to_period("M")
    eq = lv["MCFTR"].pct_change()
    # До старта MCFTR (2003-02) — ценовой IMOEX + оценка дивидендов 1,5%/год
    imoex = lv["IMOEX"].pct_change()
    pre = imoex[imoex.index < eq.first_valid_index()] + (1.015 ** (1 / 12) - 1)
    eq = pd.concat([pre, eq.dropna()])
    ret = pd.DataFrame({
        "EQ": eq,
        "OFZ": lv["RGBITR"].pct_change(),
        "GOLD": lv["GOLD_RUB_G"].pct_change(),
    })
    # Сшивка корп. облигаций: RUCBITR до 2023-05, дальше RUCBTRNS
    old = pd.read_csv(DATA / "raw" / "CORP_TR.csv", index_col=0, parse_dates=True).iloc[:, 0]
    new = pd.read_csv(DATA / "raw" / "RUCBTRNS.csv", index_col=0, parse_dates=True).iloc[:, 0]
    old_m = old.groupby(old.index.to_period("M")).last().pct_change()
    new_m = new.groupby(new.index.to_period("M")).last().pct_change()
    corp = pd.concat([old_m[old_m.index <= "2023-05"],
                      new_m[new_m.index > "2023-05"]])
    ret["CORP"] = corp
    rt = pd.read_csv(DATA / "monthly_returns.csv", index_col=0, parse_dates=True)
    rt.index = rt.index.to_period("M")
    ret["CASH"] = rt["CASH_PROXY"]
    ret["USDRUB"] = lv["USDRUB"].pct_change()  # не актив: для сигналов и МК
    # Дивидендная доходность акций: trailing 12 мес из спреда MCFTR/IMOEX
    ratio = lv["MCFTR"] / lv["IMOEX"]
    ret["DIV_YIELD"] = ratio / ratio.shift(12) - 1
    return ret


def build_cpi(end="2026-12"):
    cpi = pd.read_csv(DATA / "inflation_yoy.csv", index_col=0, parse_dates=True).iloc[:, 0]
    cpi = cpi[cpi.index.notna()]
    cpi.index = cpi.index.to_period("M")
    cpi = cpi.dropna()
    idx = pd.period_range("1997-01", max(cpi.index.max(), pd.Period(end, "M")), freq="M")
    yoy = pd.Series(index=idx, dtype=float)
    for y, v in CPI_BACKFILL.items():
        yoy.loc[str(y)] = v
    yoy.loc[cpi.index] = cpi.values
    yoy = yoy.ffill()  # хвост без свежего CPI — последнее известное значение
    mom = (1 + yoy / 100) ** (1 / 12) - 1          # приближение без сезонности
    cpi_index = (1 + mom).cumprod()
    return yoy, mom, cpi_index


def build_key_rate():
    """Денежная ставка (сшивка рефинансирования + ключевой), месячные средние."""
    p = DATA / "raw" / "MONETARY_RATE.csv"
    if not p.exists():
        p = DATA / "raw" / "KEY_RATE.csv"
    kr = pd.read_csv(p, index_col=0, parse_dates=True).iloc[:, 0]
    return kr.groupby(kr.index.to_period("M")).mean()


def make_drags(ret, cpi_yoy):
    """Ежемесячный налоговый drag брокерского счёта: дивиденды и купоны 13%."""
    eq_drag = (NDFL * ret["DIV_YIELD"].clip(lower=0) / 12).fillna(0.0)
    kr = build_key_rate() / 100
    ofz12 = ((1 + ret["OFZ"]).rolling(12).apply(np.prod) - 1)
    cy = kr.reindex(ret.index).fillna(ofz12)        # купонная доходность ≈ ключевая ставка
    bond_drag = (NDFL * cy.clip(lower=0) / 12).fillna(0.0)
    return eq_drag, bond_drag, kr


# --------------------------------------------------------- стратегии ----
# Интерфейс: wfn(rets, pos, t, ctx) -> dict asset -> weight.
# Разрешено смотреть только rets.iloc[:pos] (данные ДО текущего месяца).

def static_fn(eq=0.0, ofz=0.0, gold=0.0, corp=0.0, cash=0.0):
    w = {"EQ": eq, "OFZ": ofz, "GOLD": gold, "CORP": corp, "CASH": cash}
    return lambda rets, pos, t, ctx: w


def glide_fn(rets, pos, t, ctx):
    """Доля акций 90% -> 40% линейно к концу горизонта."""
    horizon = ctx.get("horizon_months", MC_MONTHS)
    k = ctx.get("elapsed", 0)
    eq = 0.9 - 0.5 * min(k / horizon, 1.0)
    return {"EQ": eq, "OFZ": 1 - eq}


def risk_parity_fn(rets, pos, t, ctx):
    cols = ["EQ", "OFZ", "GOLD"]
    win = rets[cols].iloc[max(0, pos - 36):pos]
    if len(win) < 12:
        return {"EQ": 0.6, "OFZ": 0.3, "GOLD": 0.1}
    vol = win.std()
    inv = 1.0 / vol.replace(0, np.nan)
    w = (inv / inv.sum())
    if w["EQ"] > 0.6:                               # кап на акции
        rest = inv[["OFZ", "GOLD"]]
        w = pd.Series({"EQ": 0.6,
                       "OFZ": 0.4 * rest["OFZ"] / rest.sum(),
                       "GOLD": 0.4 * rest["GOLD"] / rest.sum()})
    return w.to_dict()


def momentum_fn(lookback=12, risky=0.8, safe=0.5, peek=False):
    """12-мес моментум акций: выше нуля -> 80/20, ниже -> 50/50 (уход в ОФЗ)."""
    def wfn(rets, pos, t, ctx):
        lb = lookback + (1 if peek else 0)
        end = pos + (1 if peek else 0)              # peek=True — подглядывание (тест)
        eq = rets["EQ"].iloc[max(0, end - lb):end]
        if len(eq) < lookback:
            return {"EQ": 0.7, "OFZ": 0.3}
        mom = (1 + eq).prod() - 1
        e = risky if mom > 0 else safe
        return {"EQ": e, "OFZ": 1 - e}
    return wfn


def realrate_fn(core_eq=0.7, band=0.10, hi=4.0, lo=1.0):
    """Реальная ключевая ставка (ставка - CPI г/г): высокая -> больше облигаций."""
    def wfn(rets, pos, t, ctx):
        rr = ctx["real_rate"]
        prev = rr.loc[:t - 1].dropna()
        if prev.empty:
            return {"EQ": core_eq, "OFZ": 1 - core_eq}
        v = prev.iloc[-1]
        if v > hi:
            e = core_eq - band
        elif v < lo:
            e = core_eq + band
        else:
            e = core_eq
        return {"EQ": e, "OFZ": 1 - e}
    return wfn


def build_regime_signals(ret, cpi_yoy, rate_m, erp_off=False):
    """Сигналы режимов на месячной сетке ret.index (только прошлые данные).

    ret: доходности с колонками EQ, USDRUB (OFZ — для прокси в МК).
    rate_m: денежная ставка в % (None — прокси из 12м ОФЗ − 12м CPI).
    """
    sig = pd.DataFrame(index=ret.index)
    eq_level = (1 + ret["EQ"].fillna(0)).cumprod()
    sig["eq_dd"] = eq_level / eq_level.rolling(12, min_periods=6).max() - 1
    sig["fx_3m"] = (1 + ret["USDRUB"].fillna(0)).rolling(3).apply(np.prod) - 1
    cpi_yoy_a = cpi_yoy.reindex(ret.index).ffill()
    if rate_m is not None:
        sig["real_rate"] = rate_m.reindex(ret.index).ffill() - cpi_yoy_a
        sig["erp"] = ret["DIV_YIELD"].reindex(ret.index).fillna(0.015) * 100 \
            - rate_m.reindex(ret.index).ffill() if "DIV_YIELD" in ret else -1.0
    else:
        # МК-прокси: реальная доходность ОФЗ (12м TR − 12м CPI)
        ofz12 = ((1 + ret["OFZ"].fillna(0)).rolling(12).apply(np.prod) - 1) * 100
        sig["real_rate"] = ofz12 - cpi_yoy_a
        sig["erp"] = -1.0  # в МК отключён
    return sig


def regime_fn(crisis_dd=-0.30, hi_rate=4.0, lo_rate=1.0, fx_shock=0.15,
              peek=False, use_gold=True):
    """Режимная стратегия: правила зафиксированы 2026-11-20 по факторному
    анализу (factor_tables.md) ДО бэктеста:
      кризис (DD<-30%) → откуп акций (+100% след.12м, статзначимо);
      FX-шок → перевес акций (+102% след.12м, статзначимо);
      высокая реальная ставка → перевес ОФЗ (+10.9% vs +6.0%);
      низкая → перевес золота (+29% при <1 п.п.);
      ERP>0 (дивы выше ставки) → минус 10 п.п. ОФЗ (значимый минус ОФЗ).
    Гистерезис: выход из режима после 2 «чистых» месяцев. Состояние — в ctx,
    поэтому между simulate()-прогонами не утекает.
    """
    base = {"normal": (0.50, 0.30, 0.20), "crisis": (0.65, 0.20, 0.15),
            "fx": (0.60, 0.25, 0.15), "hi_rate": (0.45, 0.40, 0.15),
            "lo_rate": (0.55, 0.20, 0.25)} if use_gold else \
           {"normal": (0.60, 0.40, 0.0), "crisis": (0.75, 0.25, 0.0),
            "fx": (0.70, 0.30, 0.0), "hi_rate": (0.50, 0.50, 0.0),
            "lo_rate": (0.70, 0.30, 0.0)}

    def wfn(rets, pos, t, ctx):
        sig = ctx["regime_signals"]
        tt = t if peek else t - 1
        hist = sig.loc[:tt]
        if hist.empty:
            e, o, g = base["normal"]
            return {"EQ": e, "OFZ": o, "GOLD": g}
        row = hist.iloc[-1]
        st = ctx.setdefault("_regime", {"r": "normal", "clear": 0})
        if not np.isfinite(row.get("eq_dd", np.nan)):
            cur = "normal"
        elif row["eq_dd"] < crisis_dd:
            cur = "crisis"
        elif row.get("fx_3m", 0) > fx_shock:
            cur = "fx"
        elif row.get("real_rate", np.nan) > hi_rate:
            cur = "hi_rate"
        elif row.get("real_rate", np.nan) < lo_rate:
            cur = "lo_rate"
        else:
            cur = "normal"
        if cur == "normal" and st["r"] != "normal":
            st["clear"] += 1
            if st["clear"] >= 2:
                st["r"] = "normal"
        elif cur != "normal":
            st["r"], st["clear"] = cur, 0
        e, o, g = base[st["r"]]
        if row.get("erp", -1) > 0:  # ERP-модификатор: −10 п.п. ОФЗ в акции
            e, o = min(e + 0.10, 0.75), max(o - 0.10, 0.10)
        e = min(max(e, 0.30), 0.75)
        return {"EQ": e, "OFZ": o, "GOLD": g}
    return wfn


STRATEGIES = {
    # имя: (функция, старт исторического окна)
    "100/0":            (static_fn(eq=1.0), "2003-02"),
    "80/20":            (static_fn(eq=0.8, ofz=0.2), "2003-02"),
    "70/30":            (static_fn(eq=0.7, ofz=0.3), "2003-02"),
    "60/40":            (static_fn(eq=0.6, ofz=0.4), "2003-02"),
    "50/50":            (static_fn(eq=0.5, ofz=0.5), "2003-02"),
    "40/60":            (static_fn(eq=0.4, ofz=0.6), "2003-02"),
    "70/30 корп":       (static_fn(eq=0.7, corp=0.3), "2003-02"),
    "60/30/10 золото":  (static_fn(eq=0.6, ofz=0.3, gold=0.1), "2008-02"),
    "50/30/20 золото":  (static_fn(eq=0.5, ofz=0.3, gold=0.2), "2008-02"),
    "risk parity":      (risk_parity_fn, "2008-02"),
    "glide 90->40":     (glide_fn, "2003-02"),
    "моментум 12м":     (momentum_fn(), "2004-02"),
    "реальная ставка":  (realrate_fn(), "2013-10"),
    "100% ОФЗ":         (static_fn(ofz=1.0), "2003-02"),
    "100% корп":        (static_fn(corp=1.0), "2003-02"),
    "100% кэш":         (static_fn(cash=1.0), "2013-10"),
}

MC_STRATEGIES = ["100/0", "70/30", "50/30/20 золото", "risk parity",
                 "glide 90->40", "моментум 12м", "100% ОФЗ"]


# ------------------------------------------------------------ движок ----
def simulate(rets, wfn, ctx, start, end, wrapper="broker",
             contrib=MONTHLY_CONTRIB, turnover_cap=0.0):
    """Месячная симуляция. Возвращает dict с nav, финалом и налогом.

    contrib — сумма или callable(Period) -> сумма (лимп-графики взносов).
    turnover_cap — доля или callable(Period, ctx) -> доля: ежемесячная
    ребалансировка к целевым весам с продажами не более доли портфеля.
    ctx["inject"] и ctx["V"] доступны wfn (взнос и стоимость портфеля месяца).
    """
    idx = rets.loc[start:end].index
    assets = [c for c in rets.columns if c not in ("DIV_YIELD", "USDRUB")]
    h = {a: 0.0 for a in assets}
    units, nav_prev = 0.0, 1.0
    navs = []
    contributed = 0.0
    prev_year_contrib = 0.0
    eq_w_cum, n_m = 0.0, 0
    horizon = len(idx)
    for k, t in enumerate(idx):
        inject = contrib(t) if callable(contrib) else contrib
        if t.month == 1:
            if wrapper == "iis" and k > 0:
                inject += IIS_DEDUCTION_RATE * min(prev_year_contrib,
                                                   IIS_DEDUCTION_BASE)
            prev_year_contrib = 0.0
        prev_year_contrib += contrib(t) if callable(contrib) else contrib
        contributed += inject
        units += inject / nav_prev
        V = sum(h.values()) + inject
        ctx["elapsed"] = k
        ctx["inject"] = inject
        ctx["V"] = V
        ctx.setdefault("horizon_months", horizon)
        w = wfn(rets, rets.index.get_loc(t), t, ctx)
        w = {a: max(0.0, float(w.get(a, 0.0))) for a in assets}
        s = sum(w.values()) or 1.0
        w = {a: wi / s for a, wi in w.items()}
        # взнос — в недовешенные классы
        deficit = {a: max(0.0, w[a] * V - h[a]) for a in assets}
        ds = sum(deficit.values())
        buy = ({a: inject * deficit[a] / ds for a in assets} if ds > 1e-9
               else {a: inject * w[a] for a in assets})
        fee = COMMISSION * sum(buy.values())
        scale = (inject - fee) / inject if inject > 0 else 1.0
        for a in assets:
            h[a] += buy[a] * scale
        # ежемесячная ребалансировка с ограничением оборота (режим Б)
        V2 = sum(h.values())
        cap = turnover_cap(t, ctx) if callable(turnover_cap) else turnover_cap
        if cap > 0 and V2 > 0:
            dev = max(abs(h[a] / V2 - w[a]) for a in assets)
            if dev > REBAL_BAND:
                deltas = {a: w[a] * V2 - h[a] for a in assets}
                sell_vol = sum(-d for d in deltas.values() if d < 0)
                if sell_vol > 1e-9:
                    sc = min(1.0, cap * V2 / sell_vol)
                    fee_r = COMMISSION * sell_vol * sc
                    for a in assets:
                        h[a] += deltas[a] * sc
                    Vt = sum(h.values())
                    for a in assets:
                        h[a] *= (Vt - fee_r) / Vt
        # годовая ребалансировка
        V2 = sum(h.values())
        if t.month == 1 and V2 > 0:
            dev = max(abs(h[a] / V2 - w[a]) for a in assets)
            if dev > REBAL_BAND:
                turnover = sum(abs(w[a] * V2 - h[a]) for a in assets) / 2
                feer = COMMISSION * turnover
                for a in assets:
                    h[a] = w[a] * (V2 - feer)
        # доходность месяца
        for a in assets:
            if h[a] == 0:
                continue
            r = rets.at[t, a]
            r = 0.0 if not np.isfinite(r) else float(r)
            drag = 0.0
            if wrapper != "iis":
                if a == "EQ":
                    drag = ctx["eq_drag"].get(t, 0.0) if isinstance(ctx["eq_drag"], pd.Series) else ctx["eq_drag"]
                elif a in ("OFZ", "CORP"):
                    drag = ctx["bond_drag"].get(t, 0.0) if isinstance(ctx["bond_drag"], pd.Series) else ctx["bond_drag"]
                elif a == "CASH":
                    drag = NDFL * max(r, 0.0)
            h[a] *= (1 + r - drag)
        V3 = sum(h.values())
        nav = V3 / units if units > 0 else 1.0
        nav_prev = nav
        navs.append(nav)
        eq_w_cum += (h["EQ"] / V3 if V3 > 0 else 0.0)
        n_m += 1
    final = vals_end = sum(h.values())
    gain = final - contributed
    if wrapper == "broker":
        tax = NDFL * max(0.0, gain)
    elif wrapper == "broker_ldv":
        eq_share = eq_w_cum / max(1, n_m)
        tax = NDFL * max(0.0, gain) * (1 - eq_share)
    else:  # iis
        tax = NDFL * max(0.0, gain - IIS_EXEMPT_CAP)
    return {
        "nav": pd.Series(navs, index=idx),
        "final": final,
        "final_net": final - tax,
        "tax": tax,
        "contributed": contributed,
        "gain": gain,
    }


# ------------------------------------------------------------ метрики ----
def metrics(nav: pd.Series) -> dict:
    m = nav.pct_change().dropna()
    dd = nav / nav.cummax() - 1
    n = len(nav)
    return {
        "CAGR": float(nav.iloc[-1] ** (12 / n) - 1),
        "maxDD": float(dd.min()),
        "ulcer": float(np.sqrt((dd ** 2).mean()) * 100),
        "CVaR95": float(m[m <= m.quantile(0.05)].mean()),
        "Sharpe": float(m.mean() / m.std() * np.sqrt(12)) if m.std() > 0 else np.nan,
    }


def md_table(rows, headers, fmts=None):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        cells = []
        for j, v in enumerate(row):
            if fmts and fmts[j] and isinstance(v, (int, float)) and np.isfinite(v):
                cells.append(format(v, fmts[j]))
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ------------------------------------------------------ монте-карло ----
def sample_mc_paths(joint: pd.DataFrame, n=MC_PATHS, months=MC_MONTHS,
                    block=MC_BLOCK, seed=SEED):
    rng = np.random.default_rng(seed)
    vals = joint.values
    T = len(vals)
    nb = months // block
    starts = rng.integers(0, T - block, size=(n, nb))
    paths = np.empty((n, months, vals.shape[1]))
    for i in range(n):
        rows = np.concatenate([np.arange(s, s + block) for s in starts[i]])
        paths[i] = vals[rows]
    return paths


def run_mc(ret, cpi_mom, eq_drag_med, bond_drag_med):
    cols = ["EQ", "OFZ", "CORP", "GOLD"]
    joint = ret[cols].join(cpi_mom.rename("CPI")).dropna().loc["2008-02":]
    paths = sample_mc_paths(joint)
    results = {}
    idx = pd.period_range("2000-01", periods=MC_MONTHS, freq="M")
    for name in MC_STRATEGIES:
        wfn = STRATEGIES[name][0]
        for wrapper in ("iis", "broker"):
            finals, dds = [], []
            for p in range(MC_PATHS):
                arr = paths[p]
                df = pd.DataFrame(arr[:, :4], index=idx, columns=cols)
                ctx = {"eq_drag": eq_drag_med, "bond_drag": bond_drag_med,
                       "real_rate": pd.Series(dtype=float),
                       "horizon_months": MC_MONTHS}
                r = simulate(df, wfn, ctx, idx[0], idx[-1], wrapper=wrapper)
                nav = r["nav"]
                dd = (nav / nav.cummax() - 1).min()
                cpi_path = np.cumprod(1 + arr[:, 4])
                finals.append(r["final_net"] / cpi_path[-1])
                dds.append(dd)
            finals = np.array(finals)
            results[(name, wrapper)] = {
                "p5": float(np.percentile(finals, 5)),
                "p50": float(np.percentile(finals, 50)),
                "p95": float(np.percentile(finals, 95)),
                "p_goal": float((finals >= GOAL_REAL).mean()),
                "dd50": float(np.percentile(dds, 50)),
                "dd5": float(np.percentile(dds, 5)),
                "cap30m_hit": None,
            }
            results[(name, wrapper)]["_finals"] = finals
    return results


# ------------------------------------------------------------ отчёты ----
def historical_table(ret, ctx, window=None):
    rows, navs = [], {}
    for name, (wfn, start0) in STRATEGIES.items():
        start = start0
        end = ret.index[-1]
        if window:
            start = max(pd.Period(start0, "M"), pd.Period(window[0], "M")).strftime("%Y-%m")
        for wrapper in ("iis", "broker"):
            r = simulate(ret, wfn, dict(ctx), start, end, wrapper=wrapper)
            m = metrics(r["nav"])
            cpi0 = ctx["cpi_index"].loc[:pd.Period(start, "M") - 1].iloc[-1]
            cpi1 = ctx["cpi_index"].loc[r["nav"].index[-1]]
            real_final = r["final_net"] / cpi1 * cpi0
            rows.append((name, wrapper, str(r["nav"].index[0]),
                         r["contributed"], r["final_net"], real_final,
                         m["CAGR"], m["maxDD"], m["ulcer"], m["Sharpe"]))
            if wrapper == "iis":
                navs[name] = r["nav"]
    return rows, navs


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG.mkdir(exist_ok=True)
    ret = load_returns()
    cpi_yoy, cpi_mom, cpi_index = build_cpi()
    eq_drag, bond_drag, kr = make_drags(ret, cpi_yoy)
    real_rate = (kr * 100 - cpi_yoy).dropna()  # kr здесь в долях, CPI — в процентах
    ctx = {"eq_drag": eq_drag, "bond_drag": bond_drag, "real_rate": real_rate,
           "cpi_index": cpi_index}

    print("=== 1. Контроль look-ahead (моментум: честный vs подглядывающий) ===")
    honest = simulate(ret, momentum_fn(), dict(ctx), "2004-02", ret.index[-1], wrapper="broker")
    peek = simulate(ret, momentum_fn(peek=True), dict(ctx), "2004-02", ret.index[-1], wrapper="broker")
    print(f"честный NAV={honest['nav'].iloc[-1]:.2f}, peek NAV={peek['nav'].iloc[-1]:.2f}")
    assert peek["nav"].iloc[-1] > honest["nav"].iloc[-1], \
        "look-ahead контроль не сработал: peek не лучше honest — проверьте движок"

    print("\n=== 2. Исторический бэктест, максимальное окно ===")
    rows_full, navs_full = historical_table(ret, ctx)
    t1 = md_table(rows_full,
                  ["Стратегия", "Счёт", "Старт", "Внесено", "Финал нетто, ₽",
                   "Финал реал., ₽", "CAGR", "МаксПрос", "Ulcer", "Sharpe"],
                  [None, None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%", ".1f", ".2f"])
    print(t1)

    print("\n=== 3. Общее окно 2013-10+ (все стратегии) ===")
    rows_common, navs_common = historical_table(ret, ctx, window=("2013-10",))
    t2 = md_table(rows_common,
                  ["Стратегия", "Счёт", "Старт", "Внесено", "Финал нетто, ₽",
                   "Финал реал., ₽", "CAGR", "МаксПрос", "Ulcer", "Sharpe"],
                  [None, None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%", ".1f", ".2f"])
    print(t2)

    print("\n=== 4. Монте-Карло (block bootstrap, 1000 путей × 20 лет) ===")
    eq_drag_med = float(eq_drag[eq_drag > 0].median())
    bond_drag_med = float(bond_drag[bond_drag > 0].median())
    mc = run_mc(ret, cpi_mom, eq_drag_med, bond_drag_med)
    rows_mc = []
    for (name, wrapper), s in mc.items():
        rows_mc.append((name, wrapper, s["p5"], s["p50"], s["p95"],
                        s["p_goal"], s["dd50"], s["dd5"]))
    t3 = md_table(rows_mc,
                  ["Стратегия", "Счёт", "5% реал.", "Медиана реал.",
                   "95% реал.", "P(≥5 млн)", "МаксПрос мед.", "МаксПрос 5%"],
                  [None, None, ",.0f", ",.0f", ",.0f", ".1%", ".1%", ".1%"])
    print(t3)

    print("\n=== 5. Стресс-окна (мин. просадка unitized NAV, ИИС) ===")
    windows = {"2008–09": ("2008-05", "2009-06"), "2014–15": ("2014-06", "2015-03"),
               "2020": ("2020-01", "2020-06"), "2022": ("2022-01", "2022-12")}
    stress_names = ["100/0", "70/30", "50/50", "50/30/20 золото", "risk parity",
                    "моментум 12м", "100% ОФЗ"]
    rows_st = []
    for name in stress_names:
        nav = navs_full[name]
        row = [name]
        for wname, (a, b) in windows.items():
            seg = nav.loc[a:b]
            row.append(float((seg / seg.cummax() - 1).min()) if len(seg) else np.nan)
        rows_st.append(row)
    t4 = md_table(rows_st, ["Стратегия"] + list(windows.keys()),
                  [None] + [".1%"] * len(windows))
    print(t4)

    print("\n=== 6. Walk-forward: моментум ===")
    grid = [(lb, rk, sf) for lb in (6, 9, 12) for rk in (0.7, 0.8, 0.9)
            for sf in (0.3, 0.5)]
    train_rows = []
    for lb, rk, sf in grid:
        r = simulate(ret, momentum_fn(lb, rk, sf), dict(ctx), "2004-02", "2014-12",
                     wrapper="iis")
        train_rows.append(((lb, rk, sf), metrics(r["nav"])["CAGR"]))
    best = max(train_rows, key=lambda x: x[1])[0]
    wf_rows = []
    for params, tag in [(best, f"лучшие на train {best}"), ((12, 0.8, 0.5), "дефолт (12,0.8,0.5)")]:
        r = simulate(ret, momentum_fn(*params), dict(ctx), "2015-01", ret.index[-1],
                     wrapper="iis")
        m = metrics(r["nav"])
        wf_rows.append((tag, m["CAGR"], m["maxDD"], m["Sharpe"]))
    t5 = md_table(wf_rows, ["Параметры", "CAGR test 2015+", "МаксПрос", "Sharpe"],
                  [None, ".1%", ".1%", ".2f"])
    print(t5)

    # --- графики
    plt.figure(figsize=(10, 6))
    for name in ["100/0", "70/30", "50/50", "50/30/20 золото", "risk parity",
                 "моментум 12м", "100% ОФЗ", "100% кэш"]:
        if name in navs_common:
            navs_common[name].to_timestamp().plot(label=name, logy=True)
    plt.title("Unitized NAV (лог), общее окно 2013-10+, ИИС")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(FIG / "fig_nav.png", dpi=110)
    plt.close()

    plt.figure(figsize=(10, 6))
    for name in ["100/0", "70/30", "50/30/20 золото", "risk parity", "моментум 12м"]:
        nav = navs_common[name]
        dd = (nav / nav.cummax() - 1)
        dd.to_timestamp().plot(label=name)
    plt.title("Просадки, общее окно, ИИС")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(FIG / "fig_dd.png", dpi=110)
    plt.close()

    plt.figure(figsize=(10, 6))
    for name in ["100/0", "70/30", "50/30/20 золото", "risk parity", "моментум 12м"]:
        f = mc[(name, "iis")]["_finals"]
        plt.hist(f / 1e6, bins=40, alpha=0.5, label=name)
    plt.axvline(GOAL_REAL / 1e6, color="k", ls="--", label="цель 5 млн реал.")
    plt.title("Монте-Карло: реальный капитал через 20 лет, млн ₽ (ИИС)")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(FIG / "fig_mc.png", dpi=110)
    plt.close()

    # --- REPORT.md
    for v in mc.values():
        v.pop("_finals", None)
    doc = f"""# Исследование: стратегия для рядового инвестора (РФ, ИИС-3 vs брокерский)

Данные: MOEX ISS (MCFTR, RGBITR, RUCBITR→RUCBTRNS, IMOEX), ЦБ (золото, USD/RUB,
ключевая ставка, CPI). Период: 2003-02 — {ret.index[-1]}.
Модель: 10 000 ₽/мес, ребалансировка взносами + годовая при отклонении >5 п.п.,
комиссия 0,05%. Налоги: 13%; на брокерском — дивиденды/купоны ежегодно
(drag по реальной дивдоходности MCFTR/IMOEX и купону ≈ ключевая ставка),
на ИИС-3 — вычет реинвестируется, прибыль освобождена до 30 млн.
«Финал реал.» — финальный капитал после налогов в рублях начала окна.

## Контроль look-ahead
Честный моментум NAV={honest['nav'].iloc[-1]:.2f} vs подглядывающий {peek['nav'].iloc[-1]:.2f}
(peek лучше — значит, движок различает будущее и прошлое, утечки нет).

## Исторический бэктест, максимальное окно
{t1}

## Общее окно 2013-10+ (сравнение «в лоб»)
{t2}

## Монте-Карло: block bootstrap (12 мес), 1000 путей × 20 лет, реальные ₽
Медианные drag'и брокерского счёта: дивиденды {eq_drag_med:.4%}/мес, купоны {bond_drag_med:.4%}/мес.
{t3}

## Стресс-окна (мин. просадка внутри окна, unitized NAV, ИИС)
{t4}

## Walk-forward: моментум (подбор на 2004–2014, проверка на 2015+)
{t5}

## Графики
![NAV](figures/fig_nav.png)
![Просадки](figures/fig_dd.png)
![Монте-Карло](figures/fig_mc.png)

## Выводы
(заполняются по результатам прогона)
"""
    REPORT.write_text(doc, encoding="utf-8")
    print(f"\nОтчёт: {REPORT}")


if __name__ == "__main__":
    main()
