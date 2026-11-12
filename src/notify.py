"""Отправка краткой сводки в Telegram через Bot API (urllib, без зависимостей)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .config import get_telegram_credentials

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str) -> bool:
    """Отправляет сообщение в чат. False — нет креда или ошибка сети/API."""
    token, chat_id = get_telegram_credentials()
    if not token or not chat_id:
        return False
    try:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(API_URL.format(token=token), data=body)
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        return bool(payload.get("ok"))
    except Exception:  # noqa: BLE001
        return False
