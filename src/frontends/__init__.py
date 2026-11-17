"""Фасады: CLI, TUI, (MCP — позже).

Каждый фасад оркеструет по-своему (CLI — один проход, TUI — диалог), но рендерит
один и тот же ``ReportData`` через общий rich-рендер ``src.report.print_all``.
"""

from __future__ import annotations

from src.config import get_token
from src.profile import load_profile
from src.providers import get_client

# Инвестиционные типы счетов Т-Инвестиций (см. AccountType в SDK):
# 1 = брокерский, 2 = ИИС, 3 = инвесткопилка.
INVESTMENT_ACCOUNT_TYPES = (1, 2, 3)
ACCOUNT_TYPE_IIS = 2


def primary_account(accounts):
    """ИИС (если есть), иначе первый счёт — для налоговой логики (вычеты, срок ИИС)."""
    for a in accounts:
        if a.account_type == ACCOUNT_TYPE_IIS:
            return a
    return accounts[0]


def accounts_label(accounts) -> str:
    """Короткая подпись счетов для шапки TUI: «ИИС + Брокерский счёт»."""
    return " + ".join(a.name for a in accounts)


def connect(console, account_id: str | None = None):
    """Общий setup: token → client → accounts → profile.

    Возвращает ``(client, accounts, profile)`` — **список** инвестиционных счетов
    (брокерский, ИИС, инвесткопилка). При явном ``account_id`` — один счёт.
    ``None`` при ошибке (сообщение уже выведено в ``console``).
    """
    token = get_token()
    if not token:
        console.print(
            "[bold red]TINKOFF_TOKEN не задан.[/] "
            "Скопируй .env.example → .env и впиши токен из настроек Т-Инвестиций."
        )
        return None

    client = get_client("tinkoff", token)

    try:
        all_accounts = client.accounts()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]Ошибка подключения к API:[/] {exc}")
        return None

    if account_id:
        accounts = [a for a in all_accounts if a.id == account_id]
        if not accounts:
            console.print(f"[yellow]Счёт {account_id} не найден в списке доступных.[/]")
            return None
    else:
        # Мультисчёт по умолчанию: все инвестиционные счета.
        accounts = [a for a in all_accounts if a.account_type in INVESTMENT_ACCOUNT_TYPES]
        if not accounts:
            console.print("[yellow]Нет инвестиционных счетов у брокера.[/]")
            return None

    names = ", ".join(a.name for a in accounts)
    console.print(f"Счета: [bold cyan]{names}[/]")

    console.print("Загружаю профиль пользователя...")
    try:
        profile = load_profile()
    except ValueError as exc:
        console.print(f"[bold red]Ошибка в data/profile.json:[/] {exc}")
        return None

    return client, accounts, profile
