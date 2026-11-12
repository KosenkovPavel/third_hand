"""Макроконтекст: ключевая ставка ЦБ РФ с кэшем на день.

Источник — официальный SOAP-сервис ЦБ (DailyInfoWebServ/KeyRate).
Сеть не требуется для тестов: разбор ответа вынесен в чистую функцию.
При любой ошибке сети/разбора возвращается последнее кэшированное значение
(или None, если кэша ещё нет) — отчёт просто пропускает макростроку.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date
from decimal import Decimal

from .config import DATA_DIR
from .money import YEAR_DAYS

CBR_KEYRATE_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
CACHE_PATH = DATA_DIR / "cbr_cache.json"

# Оценка инфляции для расчёта «в сегодняшних деньгах» (у ЦБ нет простого
# открытого API по инфляции — используем константу с пометкой в выводе).
DEFAULT_INFLATION_PCT = Decimal("7")

_SOAP_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>"
    '<KeyRate xmlns="http://web.cbr.ru/">'
    "<fromDate>{from_date}T00:00:00</fromDate>"
    "<ToDate>{to_date}T00:00:00</ToDate>"
    "</KeyRate>"
    "</soap:Body></soap:Envelope>"
)


def _extract_latest_rate(xml_text: str) -> Decimal | None:
    """Последняя (самая свежая) ставка из SOAP-ответа KeyRate."""
    pairs = re.findall(r"<DT>([^<]+)</DT>\s*<Rate>([^<]+)</Rate>", xml_text)
    if not pairs:
        return None
    latest = max(pairs, key=lambda p: p[0])
    try:
        return Decimal(latest[1])
    except Exception:  # noqa: BLE001
        return None


def _read_cache() -> dict | None:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(rate: Decimal, day: date) -> None:
    try:
        CACHE_PATH.write_text(
            json.dumps({"date": day.isoformat(), "key_rate": str(rate)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def key_rate(today: date | None = None) -> Decimal | None:
    """Ключевая ставка ЦБ РФ, % годовых. Кэшируется на день в data/cbr_cache.json.

    При недоступности сети возвращает кэш любой давности; если кэша нет — None.
    """
    today = today or date.today()
    cached = _read_cache()
    if cached and cached.get("date") == today.isoformat():
        try:
            return Decimal(str(cached["key_rate"]))
        except Exception:  # noqa: BLE001
            pass

    rate: Decimal | None = None
    try:
        body = _SOAP_BODY.format(
            from_date=today.replace(day=1).isoformat(),
            to_date=today.isoformat(),
        ).encode("utf-8")
        req = urllib.request.Request(
            CBR_KEYRATE_URL,
            data=body,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "http://web.cbr.ru/KeyRate",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            rate = _extract_latest_rate(resp.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        rate = None

    if rate is not None:
        _write_cache(rate, today)
        return rate

    if cached:
        try:
            return Decimal(str(cached["key_rate"]))
        except Exception:  # noqa: BLE001
            return None
    return None


def deposit_return_pct(rate_pct: Decimal, days: int) -> Decimal:
    """Доходность вклада под ключевую ставку за период (простой процент)."""
    return (rate_pct * days / YEAR_DAYS).quantize(Decimal("0.01"))
