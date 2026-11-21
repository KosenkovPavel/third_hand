#!/usr/bin/env python3
"""
Сбор исторических данных для бэктеста портфелей на российском рынке.

Источники (открытые, без ключей):
  * MOEX ISS  — индексы полной доходности: акции (MCFTR), ОФЗ (RGBITR),
                корпоративные облигации (ищется автоматически), IMOEX (ценовой)
  * ЦБ РФ     — учётная цена золота (руб/г), курс USD/RUB, ключевая ставка,
                инфляция (г/г)

Запуск:
    python research/collect_data.py                    # всё с 2003 года
    python research/collect_data.py --discover         # показать найденные индексы облигаций
    python research/collect_data.py --corp SECID       # задать индекс корп. облигаций вручную
    python research/collect_data.py --start 2010-01-01 --out research/data

Результат (папка research/data/):
    raw/*.csv              — дневные ряды как есть
    monthly_levels.csv     — значения на конец месяца
    monthly_returns.csv    — месячные доходности (+ прокси денежного рынка)
    inflation_yoy.csv      — инфляция г/г (если удалось получить)
    quality_report.txt     — с какой даты ряд, сколько точек, что не удалось скачать
"""
import argparse
import io
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (portfolio-research-script)"}
ISS = "https://iss.moex.com/iss"
CBR = "https://www.cbr.ru"

REPORT = []


def log(msg):
    print(msg)
    REPORT.append(msg)


def get(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


# ---------------------------------------------------------------- MOEX ----
def moex_index_history(secid, start, end):
    """Дневные значения индекса (CLOSE) через ISS с постраничной загрузкой."""
    url = f"{ISS}/history/engines/stock/markets/index/securities/{secid}.json"
    rows, cols, offset = [], None, 0
    while True:
        r = get(url, {"from": start, "till": end, "start": offset, "iss.meta": "off"})
        block = r.json()["history"]
        cols = block["columns"]
        data = block["data"]
        if not data:
            break
        rows += data
        offset += len(data)
    if not rows:
        return pd.Series(dtype=float, name=secid)
    df = pd.DataFrame(rows, columns=cols)
    df["TRADEDATE"] = pd.to_datetime(df["TRADEDATE"])
    df["CLOSE"] = pd.to_numeric(df["CLOSE"], errors="coerce")
    s = (df.dropna(subset=["CLOSE"])
           .drop_duplicates("TRADEDATE")
           .set_index("TRADEDATE")["CLOSE"].sort_index())
    s.name = secid
    return s


def moex_find_indices(query):
    """Поиск индексов по названию. Возвращает DataFrame secid/shortname/name."""
    r = get(f"{ISS}/securities.json", {"q": query, "iss.meta": "off", "limit": 100})
    block = r.json()["securities"]
    df = pd.DataFrame(block["data"], columns=block["columns"])
    if "group" in df.columns:
        df = df[df["group"] == "stock_index"]
    return df[["secid", "shortname", "name"]].drop_duplicates("secid")


def discover_bond_indices():
    frames = []
    for q in ["облигаций полной доходности", "корпоративных облигаций", "государственных облигаций"]:
        try:
            frames.append(moex_find_indices(q))
        except Exception as e:  # noqa: BLE001
            log(f"[!] поиск индексов '{q}': {e}")
    if not frames:
        return pd.DataFrame(columns=["secid", "shortname", "name"])
    return pd.concat(frames).drop_duplicates("secid").reset_index(drop=True)


def resolve_corp_index():
    """Пытаемся найти индекс корпоративных облигаций полной доходности."""
    df = discover_bond_indices()
    if df.empty:
        return None
    n = df["name"].str.lower()
    mask = n.str.contains("корпоратив") & n.str.contains("полной доходности")
    mask &= ~n.str.contains("кредитн|высокодоходн|субфед|муниципал")
    cand = df[mask]
    if cand.empty:
        return None
    log("[i] кандидаты на индекс корп. облигаций:")
    log(cand.to_string(index=False))
    return cand.iloc[0]["secid"]


# ----------------------------------------------------------------- CBR ----
def _num(x):
    return float(str(x).replace("\xa0", "").replace(" ", "").replace(",", "."))


def cbr_gold(start_year, end_year):
    """Учётная цена золота, руб/г (Code=1)."""
    out = {}
    for y in range(start_year, end_year + 1):
        params = {"date_req1": f"01/01/{y}", "date_req2": f"31/12/{y}"}
        try:
            r = get(f"{CBR}/scripts/xml_metall.asp", params)
            root = ET.fromstring(r.content)
        except Exception as e:  # noqa: BLE001
            log(f"[!] золото ЦБ {y}: {e}")
            continue
        for rec in root.findall("Record"):
            if rec.get("Code") != "1":
                continue
            d = pd.to_datetime(rec.get("Date"), format="%d.%m.%Y")
            out[d] = _num(rec.find("Buy").text)
    s = pd.Series(out, name="GOLD_RUB_G", dtype=float).sort_index()
    return s


def cbr_usd(start, end):
    params = {
        "date_req1": pd.Timestamp(start).strftime("%d/%m/%Y"),
        "date_req2": pd.Timestamp(end).strftime("%d/%m/%Y"),
        "VAL_NM_RQ": "R01235",
    }
    r = get(f"{CBR}/scripts/XML_dynamic.asp", params)
    root = ET.fromstring(r.content)
    out = {}
    for rec in root.findall("Record"):
        d = pd.to_datetime(rec.get("Date"), format="%d.%m.%Y")
        out[d] = _num(rec.find("Value").text) / _num(rec.find("Nominal").text)
    return pd.Series(out, name="USDRUB", dtype=float).sort_index()


def _cbr_table(path, start, end):
    params = {
        "UniDbQuery.Posted": "True",
        "UniDbQuery.From": pd.Timestamp(start).strftime("%d.%m.%Y"),
        "UniDbQuery.To": pd.Timestamp(end).strftime("%d.%m.%Y"),
    }
    r = get(f"{CBR}{path}", params)
    tables = pd.read_html(io.StringIO(r.text), decimal=",", thousands=" ")
    if not tables:
        raise ValueError("таблица не найдена")
    return tables[0]


RU_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
             "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
             "ноября": 11, "декабря": 12}


def _parse_ru_date(s):
    """'14 сентября 2012 г.' -> Timestamp; '1 января 2016 г.' без 'С' тоже."""
    import re
    s = str(s).replace("\xa0", " ").strip()
    s = re.sub(r"^[СC]\s+", "", s)          # 'С 1 января 2016 г.'
    s = re.sub(r"\s*г\.?\s*$", "", s)       # хвост ' г.' — не трогая 'г' внутри ('августа')
    parts = s.split()
    if len(parts) != 3:
        return pd.NaT
    day, mon, year = parts
    try:
        return pd.Timestamp(int(year), RU_MONTHS[mon.lower()], int(day))
    except (ValueError, KeyError):
        return pd.NaT


def cbr_ref_rate():
    """Ставка рефинансирования 1992–2015 с официальной страницы ЦБ
    (cbr.ru/statistics/idkp_br/refinancing_rates1/). Дневной ряд."""
    r = get("https://cbr.ru/statistics/idkp_br/refinancing_rates1/")
    t = pd.read_html(io.StringIO(r.text), decimal=",", thousands=" ")[0]
    out = {}
    for _, row in t.iterrows():
        period = str(row.iloc[0])
        try:
            rate = float(row.iloc[1])
        except (TypeError, ValueError):
            continue  # строка «соответствует ключевой ставке»
        left = period.split("–")[0].strip()
        d = _parse_ru_date(left)
        if pd.isna(d):
            continue
        out[d] = rate
    s = pd.Series(out, name="REF_RATE", dtype=float).sort_index()
    return s


def splice_monetary_rate(ref, key):
    """Сшивка: ставка рефинансирования до 2013-09-16, ключевая с 2013-09-17."""
    key = key.copy()
    key.name = "MONETARY_RATE"
    ref = ref.copy()
    ref.name = "MONETARY_RATE"
    first_key = key.index.min()
    spliced = pd.concat([ref[ref.index < first_key], key])
    spliced = spliced[~spliced.index.duplicated(keep="last")].sort_index()
    return spliced


def cbr_key_rate(start, end):
    df = _cbr_table("/hd_base/KeyRate/", start, end)
    d = pd.to_datetime(df.iloc[:, 0], format="%d.%m.%Y", errors="coerce")
    v = pd.to_numeric(df.iloc[:, 1].astype(str).str.replace(",", "."), errors="coerce")
    s = pd.Series(v.values, index=d, name="KEY_RATE").dropna().sort_index()
    return s[~s.index.duplicated()]


def cbr_inflation(start, end):
    """Инфляция г/г из таблицы «Инфляция и ключевая ставка»."""
    df = _cbr_table("/hd_base/infl/", start, end)
    cols = [str(c) for c in df.columns]
    idx = next(i for i, c in enumerate(cols) if "нфляц" in c and "Цель" not in c and "цель" not in c)
    d = pd.to_datetime(df.iloc[:, 0].astype(str), format="%m.%Y", errors="coerce")
    v = pd.to_numeric(df.iloc[:, idx].astype(str).str.replace(",", "."), errors="coerce")
    s = pd.Series(v.values, index=d, name="CPI_YOY_PCT").dropna().sort_index()
    return s[~s.index.duplicated()]


# --------------------------------------------------------------- build ----
def to_month_end(s):
    """Последнее значение месяца (PeriodIndex, чтобы не зависеть от версии pandas)."""
    g = s.dropna().groupby(s.dropna().index.to_period("M")).last()
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1997-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--out", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--corp", default=None, help="SECID индекса корп. облигаций полной доходности")
    ap.add_argument("--discover", action="store_true", help="только показать найденные индексы облигаций")
    a = ap.parse_args()

    if a.discover:
        pd.set_option("display.max_colwidth", 90)
        print(discover_bond_indices().to_string(index=False))
        return

    out = Path(a.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    daily = {}

    # --- MOEX
    moex_ids = {"MCFTR": "MCFTR", "RGBITR": "RGBITR", "IMOEX": "IMOEX"}
    corp = a.corp or resolve_corp_index()
    if corp:
        moex_ids["CORP_TR"] = corp
        log(f"[i] индекс корп. облигаций: {corp} (проверьте, что это именно полная доходность)")
    else:
        log("[!] индекс корп. облигаций не найден: запустите --discover и передайте --corp SECID")

    for name, secid in moex_ids.items():
        try:
            s = moex_index_history(secid, a.start, a.end)
            s.name = name
            if s.empty:
                log(f"[!] {name} ({secid}): пустой ответ")
                continue
            daily[name] = s
        except Exception as e:  # noqa: BLE001
            log(f"[!] {name} ({secid}): {e}")

    # --- ЦБ
    y0, y1 = pd.Timestamp(a.start).year, pd.Timestamp(a.end).year
    for name, fn in [
        ("GOLD_RUB_G", lambda: cbr_gold(max(y0, 2008), y1)),
        ("USDRUB", lambda: cbr_usd(a.start, a.end)),
        ("KEY_RATE", lambda: cbr_key_rate(a.start, a.end)),
    ]:
        try:
            s = fn()
            if s.empty:
                log(f"[!] {name}: пустой ряд")
            else:
                daily[name] = s
        except Exception as e:  # noqa: BLE001
            log(f"[!] {name}: {e}")

    cpi = None
    try:
        cpi = cbr_inflation(a.start, a.end)
        cpi.to_csv(out / "inflation_yoy.csv", header=True)
    except Exception as e:  # noqa: BLE001
        log(f"[!] инфляция ЦБ: {e}. Положите свой inflation_yoy.csv (date, CPI_YOY_PCT) вручную")

    for name, s in daily.items():
        s.to_csv(out / "raw" / f"{name}.csv", header=True)

    # --- денежная ставка: сшивка рефинансирования (1992+) и ключевой (2013+)
    if "KEY_RATE" in daily:
        try:
            ref = cbr_ref_rate()
            ref.to_csv(out / "raw" / "REF_RATE.csv", header=True)
            monetary = splice_monetary_rate(ref, daily["KEY_RATE"])
            daily["MONETARY_RATE"] = monetary
            log(f"[i] денежная ставка сшита: {monetary.index.min().date()} -> "
                f"{monetary.index.max().date()}, переход на ключевую {daily['KEY_RATE'].index.min().date()}")
        except Exception as e:  # noqa: BLE001
            log(f"[!] ставка рефинансирования: {e} — используется только ключевая")
            daily["MONETARY_RATE"] = daily["KEY_RATE"]
    if "MONETARY_RATE" in daily:
        daily["MONETARY_RATE"].to_csv(out / "raw" / "MONETARY_RATE.csv", header=True)

    # --- месячные ряды
    levels = {k: to_month_end(v) for k, v in daily.items()
              if k not in ("KEY_RATE", "MONETARY_RATE")}
    lv = pd.DataFrame(levels).sort_index()
    lv.index = lv.index.to_timestamp("M")
    lv.to_csv(out / "monthly_levels.csv")

    rets = lv.pct_change()
    if "MONETARY_RATE" in daily:
        kr = daily["MONETARY_RATE"].resample("D").ffill()
        kr_m = kr.groupby(kr.index.to_period("M")).mean()
        kr_m.index = kr_m.index.to_timestamp("M")
        # ПРОКСИ денежного рынка: ставка/12. Это грубо; замените на RUSFAR/RUONIA при наличии
        rets["CASH_PROXY"] = (kr_m / 100 / 12).reindex(rets.index)
    rets.to_csv(out / "monthly_returns.csv")

    # --- отчёт о качестве
    log("\n=== Покрытие рядов ===")
    for name, s in daily.items():
        log(f"{name:12s} {s.index.min().date()} -> {s.index.max().date()}  n={len(s)}")
    if cpi is not None and not cpi.empty:
        log(f"{'CPI_YOY':12s} {cpi.index.min().date()} -> {cpi.index.max().date()}  n={len(cpi)}")
    log("\nЗамечания:")
    log("- Золото ЦБ доступно примерно с 2008 г., общий период для портфеля с золотом короче.")
    log("- CASH_PROXY построен из ключевой ставки (приближение).")
    log("- CPI дан как г/г; для реальной доходности нужен месячный ИПЦ (Росстат), при необходимости добавьте вручную.")
    log("- Индексы MOEX 'полной доходности' включают дивиденды/купоны, но не налоги и комиссии: учитывайте их в симуляции.")
    (out / "quality_report.txt").write_text("\n".join(REPORT), encoding="utf-8")
    print(f"\nГотово. Файлы в: {out.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
