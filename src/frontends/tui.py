"""TUI-фасад: полноэкранный интерфейс на Textual (в стиле Claude Code).

Запускается, когда `python main.py` вызван без аргументов. На старте — крупный
ASCII-логотип по центру, скроллящийся вывод отчёта в центре, окно ввода внизу,
спиннер во время сбора.
"""

from __future__ import annotations

from functools import partial
from io import StringIO

from rich.console import Console
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Header, Input, RichLog, Static

from src import report as report_mod
from src.report_builder import ReportError, build_report

from . import connect
from .cli import build_parser

SECTIONS = {
    "портфель": [],
    "иис": [],
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

# Кнопки быстрого доступа (основные разделы) — чтобы не запоминать слова.
# id — латиницей (Textual требует ASCII в идентификаторах), label — по-русски,
# choice — ключ в SECTIONS.
BUTTONS = [
    ("portfolio", "Портфель", "портфель"),
    ("rebalance", "Ребаланс", "ребаланс"),
    ("iis", "ИИС", "иис"),
    ("forecast", "Прогноз", "прогноз"),
    ("history", "История", "история"),
    ("risk", "Риски", "риски"),
]

# Рендереры «просто» и «детально» по секциям. Секции вне кнопок (рынок, календарь,
# новости, акции, облигации) доступны через поле ввода и рендерятся полным отчётом.
SIMPLE_RENDERERS = {
    "портфель": report_mod.print_summary,
    "иис": report_mod.print_iis_simple,
    "ребаланс": report_mod.print_rebalance_simple,
    "прогноз": report_mod.print_forecast_simple,
    "история": report_mod.print_history_simple,
    "риски": report_mod.print_risk_simple,
}
DETAIL_RENDERERS = {
    "портфель": report_mod.print_all,
    "иис": report_mod.print_iis_detail,
    "ребаланс": report_mod.print_rebalance_detail,
    "прогноз": report_mod.print_forecast_detail,
    "история": report_mod.print_history_detail,
    "риски": report_mod.print_risk_detail,
}

# Прогресс build_report не должен лезть в stdout (сломает экран Textual).
NULL_CONSOLE = Console(file=StringIO())

# ASCII-логотип «THIRD HAND» (шрифт pyfiglet «standard»). Кириллицу ASCII-генераторы
# (figlet/pyfiglet/art) не поддерживают, поэтому крупный логотип — латиницей, а
# русский бренд «Третья рука» — в подзаголовке под ним. Сгенерировано один раз,
# чтобы не тащить pyfiglet в зависимости.
LOGO = r"""
 _____ _   _ ___ ____  ____    _   _    _    _   _ ____
|_   _| | | |_ _|  _ \|  _ \  | | | |  / \  | \ | |  _ \
  | | | |_| || || |_) | | | | | |_| | / _ \ |  \| | | | |
  | | |  _  || ||  _ <| |_| | |  _  |/ ___ \| |\  | |_| |
  |_| |_| |_|___|_| \_\____/  |_| |_/_/   \_\_| \_|____/
""".strip("\n")

LOGO_WIDTH = max(len(line) for line in LOGO.splitlines())


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
    #buttons {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    #buttons Button {
        margin-right: 1;
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
        self.detail = False          # False — «просто», True — «детально»
        self.last_report = None
        self.last_choice: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", markup=True, wrap=True, highlight=True)
        yield Static("", id="status")
        with Horizontal(id="buttons"):
            for btn_id, label, _choice in BUTTONS:
                yield Button(label, id=btn_id)
            yield Button("Детально", id="btn-mode", variant="primary")
        yield Input(
            placeholder=(
                "или напиши: рынок · календарь · новости · акции · облигации · выход"
            ),
            id="input",
        )

    def on_mount(self) -> None:
        self.title = "Третья рука"
        self._refresh_mode_ui()
        out = self.query_one("#output", RichLog)

        # Центрируем по ширине окна (минус боковой padding RichLog в 1 символ).
        width = max(LOGO_WIDTH, (self.size.width or 80) - 2)
        pad = max(0, (width - LOGO_WIDTH) // 2)

        def centered(text: str, visible: int) -> str:
            return " " * max(0, (width - visible) // 2) + text

        banner = Text()
        banner.append("\n")
        for line in LOGO.splitlines():
            banner.append(" " * pad + line, style="bold")
            banner.append("\n")
        banner.append("\n")
        banner.append(centered("Третья рука", len("Третья рука")), style="bold")
        banner.append("\n")
        banner.append(
            centered("честная бухгалтерия портфеля", len("честная бухгалтерия портфеля")),
            style="dim",
        )
        banner.append("\n\n")
        banner.append(
            centered("Введи, что показать — например «портфель» или «ребаланс»",
                     len("Введи, что показать — например «портфель» или «ребаланс»")),
            style="dim",
        )
        banner.append("\n")
        banner.append(
            centered("Для выхода: «выход»", len("Для выхода: «выход»")),
            style="italic dim",
        )
        banner.append("\n")

        out.write(banner)
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "btn-mode":
            self._toggle_mode()
            return
        for btn_id, _label, choice in BUTTONS:
            if btn_id == button_id:
                self._run(choice)
                return

    def _refresh_mode_ui(self) -> None:
        """Синхронизирует шапку и кнопку-переключатель с текущим режимом."""
        self.sub_title = f"{self.account.name} · {'детально' if self.detail else 'просто'}"
        mode_button = self.query_one("#btn-mode", Button)
        mode_button.label = "Просто" if self.detail else "Детально"

    def _toggle_mode(self) -> None:
        self.detail = not self.detail
        self._refresh_mode_ui()
        if self.last_report is not None:
            self._render_report(self.last_report)

    def _run(self, choice: str) -> None:
        self.last_choice = choice
        self.query_one("#status", Static).update(f"⟳ Собираю «{choice}»…")
        # run_worker вызывает work() без аргументов — оборачиваем в partial.
        self.run_worker(partial(self._build, choice), thread=True)

    def _build(self, choice: str) -> None:
        args = self.parser.parse_args(SECTIONS[choice])
        try:
            report = build_report(self.client, self.account, args, self.profile, NULL_CONSOLE)
        except ReportError as exc:
            self.call_from_thread(self._show_error, str(exc))
            return
        self.call_from_thread(self._show_report, report)

    def _show_report(self, report) -> None:
        self.last_report = report
        self._render_report(report)

    def _render_report(self, report) -> None:
        out = self.query_one("#output", RichLog)
        out.clear()
        width = max(80, self.size.width or 80)
        console = _CaptureConsole(width)
        renderers = DETAIL_RENDERERS if self.detail else SIMPLE_RENDERERS
        renderer = renderers.get(self.last_choice or "портфель", report_mod.print_all)
        renderer(report, console=console)
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
