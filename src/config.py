import os
from pathlib import Path

from dotenv import dotenv_values

DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def get_config() -> dict[str, str | None]:
    """Настройки из .env в корне проекта; переменные окружения имеют приоритет."""
    values = dict(dotenv_values(_ENV_PATH))
    for key in ("TINKOFF_TOKEN", "ACCOUNT_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def get_token() -> str | None:
    return get_config().get("TINKOFF_TOKEN")


def get_account_id() -> str | None:
    return get_config().get("ACCOUNT_ID")


def get_telegram_credentials() -> tuple[str | None, str | None]:
    """TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID из .env/окружения (оба или None)."""
    cfg = get_config()
    return cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID")
