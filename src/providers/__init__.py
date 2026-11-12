"""Провайдеры брокеров: реестр и выбор клиента."""

from __future__ import annotations

from .tinkoff import TinkoffClient

# Реестр: имя провайдера → класс адаптера.
PROVIDERS = {
    "tinkoff": TinkoffClient,
}


def get_client(provider: str, token: str):
    """Создать клиент провайдера по имени и токену."""
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Неизвестный провайдер: {provider!r}. Доступны: {', '.join(PROVIDERS)}"
        )
    return cls(token)
