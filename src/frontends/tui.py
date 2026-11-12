"""TUI-фасад: диалог на rich.prompt — «окошко», где пользователь вводит ответ.

Запускается, когда `python main.py` вызван без аргументов. Каждая секция строит
тот же ``ReportData`` и рендерит его тем же ``print_all``, что и CLI.
"""

from __future__ import annotations

from rich.prompt import IntPrompt, Prompt

from src.report import print_all
from src.report_builder import ReportError, build_report

from . import connect

# Слово → флаги для parser.parse_args. Секции с JSON-фильтрами работают с дефолтами
# (пустой {} → фильтры по умолчанию).
SECTIONS = {
    "портфель": [],
    "ребаланс": ["--rebalance"],
    "прогноз": ["--forecast"],
    "риски": ["--risk"],
    "рынок": ["--market"],
    "история": ["--history"],
    "календарь": ["--calendar"],
    "новости": ["--news"],
    "акции": ["--picks", "{}"],
    "облигации": ["--bonds", "{}"],
}

CHOICES = list(SECTIONS) + ["выход"]


def _flags_for(choice: str, console) -> list[str]:
    """Флаги для выбранной секции + доп. вопросы по параметрам."""
    flags = list(SECTIONS[choice])
    if choice == "ребаланс":
        invest = IntPrompt.ask("Сколько пополняем? (₽)", default=0, console=console)
        if invest:
            flags += ["--invest", str(invest)]
    return flags


def run_tui(parser, console) -> int:
    console.print("[bold]invest-helper[/] — интерактивный режим")

    setup = connect(console)
    if setup is None:
        return 1
    client, account, profile = setup

    while True:
        choice = Prompt.ask(
            "Что показать?", choices=CHOICES, default="портфель", console=console
        )
        if choice == "выход":
            break

        flags = _flags_for(choice, console)
        args = parser.parse_args(flags)

        try:
            report = build_report(client, account, args, profile, console)
        except ReportError as exc:
            console.print(f"[bold red]{exc}[/]")
            continue

        print_all(report, console=console)

    return 0
