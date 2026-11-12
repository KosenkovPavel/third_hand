"""TUI-фасад: полноэкранный интерфейс на Textual (в стиле Claude Code).

Запускается, когда `python main.py` вызван без аргументов. Логотип 🦾 в шапке,
скроллящийся вывод отчёта в центре, окно ввода внизу, спиннер во время сбора.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.panel import Panel
from textual.app import App, ComposeResult
from textual.widgets import Header, Input, RichLog, Static

from src.report import print_all
from src.report_builder import ReportError, build_report

from . import connect
from .cli import build_parser

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

# Прогресс build_report не должен лезть в stdout (сломает экран Textual).
NULL_CONSOLE = Console(file=StringIO())


class _CaptureConsole(Console):
    """Console, который не пишет в терминал, а собирает renderables.

    ``print_all`` вызывает только ``console.print(renderable)``, поэтому
    достаточно перехватить ``print``. Обычный ``Console(record=True)`` не
    годится: он всё равно пишет в stdout и хранит плоские сегменты
    (``_record_buffer``), а не renderables — в ``RichLog`` их не отдать.
    """

    def __init__(self, width: int) -> None:
        super().__init__(file=StringIO(), width=width)
        self.renderables: list[object] = []

    def print(self, *objects: object, **kwargs: object) -> None:
        del kwargs  # sep/end/style — не важны: мы только собираем объекты
        self.renderables.extend(objects)


class ThirdHandApp(App):
    TITLE = "Третья рука"

    CSS = """
    #output {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    #status {
        height: 1;
        color: $warning;
        padding: 0 1;
    }
    #input {
        height: 3;
        border: round $accent;
    }
    """

    def __init__(self, client, account, profile, parser) -> None:
        super().__init__()
        self.client = client
        self.account = account
        self.profile = profile
        self.parser = parser

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", markup=True, wrap=True, highlight=True)
        yield Static("", id="status")
        yield Input(
            placeholder=(
                "портфель · ребаланс · прогноз · риски · рынок · история · "
                "календарь · новости · акции · облигации"
            ),
            id="input",
        )

    def on_mount(self) -> None:
        self.title = "🦾 Третья рука"
        self.sub_title = self.account.name
        out = self.query_one("#output", RichLog)
        out.write(
            Panel(
                "[bold]Третья рука[/bold] — честная бухгалтерия портфеля.\n\n"
                "Введи, что показать, например [bold]портфель[/bold] или [bold]ребаланс[/bold].\n"
                "[dim]Для выхода: «выход»[/dim]",
                title="🦾 Третья рука",
                border_style="green",
            )
        )
        self.query_one("#input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        choice = event.value.strip().lower()
        event.input.value = ""
        if choice in ("выход", "exit", "quit", "q"):
            self.exit()
            return
        if choice not in SECTIONS:
            self.query_one("#output", RichLog).write(
                f"[red]Не знаю «{choice}». Доступно: {', '.join(SECTIONS)}, выход.[/red]"
            )
            return
        self._run(choice)

    def _run(self, choice: str) -> None:
        self.query_one("#status", Static).update(f"⟳ Собираю «{choice}»…")
        self.run_worker(self._build, choice, thread=True)

    def _build(self, choice: str) -> None:
        args = self.parser.parse_args(SECTIONS[choice])
        try:
            report = build_report(self.client, self.account, args, self.profile, NULL_CONSOLE)
        except ReportError as exc:
            self.call_from_thread(self._show_error, str(exc))
            return
        self.call_from_thread(self._show_report, report)

    def _show_report(self, report) -> None:
        out = self.query_one("#output", RichLog)
        width = max(80, self.size.width or 80)
        console = _CaptureConsole(width)
        print_all(report, console=console)
        for renderable in console.renderables:
            out.write(renderable)
        self.query_one("#status", Static).update("")

    def _show_error(self, message: str) -> None:
        self.query_one("#output", RichLog).write(f"[bold red]Ошибка:[/] {message}")
        self.query_one("#status", Static).update("")


def run_tui(parser, console) -> int:
    setup = connect(console)
    if setup is None:
        return 1
    client, account, profile = setup
    ThirdHandApp(client, account, profile, parser).run()
    return 0
