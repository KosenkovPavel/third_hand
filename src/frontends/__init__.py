"""Фасады: CLI, TUI, (MCP — позже).

Каждый фасад оркеструет по-своему (CLI — один проход, TUI — диалог), но рендерит
один и тот же ``ReportData`` через общий rich-рендер ``src.report.print_all``.
"""

from __future__ import annotations

from src.config import get_account_id, get_token
from src.profile import load_profile
from src.providers import get_client


def connect(console, account_id: str | None = None):
    """Общий setup: token → client → accounts → account → profile.

    Возвращает ``(client, account, profile)`` или ``None`` при ошибке
    (сообщение уже выведено в ``console``).
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
        accounts = client.accounts()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[bold red]Ошибка подключения к API:[/] {exc}")
        return None

    account_id = account_id or get_account_id()
    if not account_id:
        console.print("[bold yellow]Не указан ACCOUNT_ID. Доступные счета:[/]")
        for acc in accounts:
            console.print(f"  [cyan]{acc.id}[/] — {acc.name}")
        return None

    matches = [a for a in accounts if a.id == account_id]
    if not matches:
        console.print(f"[yellow]Счёт {account_id} не найден в списке доступных.[/]")
        return None
    account = matches[0]
    console.print(f"Счёт: [bold cyan]{account.name}[/] ({account_id})")

    console.print("Загружаю профиль пользователя...")
    try:
        profile = load_profile()
    except ValueError as exc:
        console.print(f"[bold red]Ошибка в data/profile.json:[/] {exc}")
        return None

    return client, account, profile
