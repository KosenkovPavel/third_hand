"""CLI-фасад: флаги argparse, сбор отчёта, побочные эффекты."""

from __future__ import annotations

import argparse
from decimal import Decimal

from src import __version__
from src.config import DATA_DIR, get_telegram_credentials
from src.history import save_point
from src.iis import tax_report_text
from src.notify import send_telegram
from src.portfolio import save_snapshot
from src.rebalance import calculate_rebalance
from src.recommendation_history import save_recommendations
from src.report import print_all
from src.report_builder import ReportError, build_report, fmt_money

from . import connect


def _telegram_summary(report, cbr_rate: Decimal | None, usdrub: Decimal | None) -> str:
    """Краткая сводка для Telegram: итог портфеля, ребаланс пополнения, макро."""
    snapshot = report.snapshot
    profile = report.profile
    pnl = snapshot.total_pnl
    pnl_pct = (pnl / snapshot.invested * 100) if snapshot.invested else Decimal("0")
    lines = [
        "invest-helper: сводка",
        f"Портфель: {fmt_money(snapshot.total)} (P&L {fmt_money(pnl)}, {pnl_pct:+.2f}%)",
    ]
    try:
        contrib = (
            profile.contributions[0].amount if profile.contributions else Decimal("5000")
        )
        plan = calculate_rebalance(
            snapshot,
            profile.target,
            invest_amount=snapshot.cash + contrib,
            cash=snapshot.cash,
            contrib=contrib,
        )
        buys = [
            f"{r.asset_class} → {fmt_money(r.invest_amount)}"
            for r in plan.recommendations
            if r.invest_amount > 0
        ]
        if buys:
            lines.append(f"Пополнение {fmt_money(contrib)}: " + ", ".join(buys))
    except Exception:  # noqa: BLE001
        pass
    macro = []
    if usdrub is not None:
        macro.append(f"USD/RUB {usdrub:.2f} ₽")
    if cbr_rate is not None:
        macro.append(f"ставка ЦБ {cbr_rate:.2f}%")
    if macro:
        lines.append(" | ".join(macro))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Инвестиционный помощник: сбор статистики по портфелю Т-Инвестиций"
    )
    parser.add_argument("--account", default=None, help="ID счёта (по умолчанию из .env)")
    parser.add_argument("--history-days", type=int, default=365 * 3, help="Глубина анализа операций, дней")
    parser.add_argument("--save", action="store_true", help="Сохранить снимок портфеля в data/portfolio.json")
    parser.add_argument(
        "--rebalance",
        metavar="JSON",
        nargs="?",
        const="__profile__",
        help='Целевая структура: {"Акции": 60, "Облигации": 40} (без значения — цель из data/profile.json)',
    )
    parser.add_argument(
        "--invest",
        type=Decimal,
        default=Decimal("0"),
        help="Дополнительное пополнение ₽ сверх остатка на счёте (для ребалансировки)",
    )
    parser.add_argument(
        "--calendar",
        action="store_true",
        help="Показать календарь дивидендов и купонов на 12 месяцев вперёд",
    )
    parser.add_argument(
        "--forecast",
        action="store_true",
        help="Показать прогноз роста портфеля (сложный процент)",
    )
    parser.add_argument(
        "--monthly",
        type=Decimal,
        default=None,
        help="Регулярное пополнение в месяц, ₽ (по умолчанию — фактическое из операций)",
    )
    parser.add_argument(
        "--return",
        dest="annual_return",
        type=Decimal,
        default=Decimal("10"),
        help="Ожидаемая доходность, %% в год (по умолчанию 10)",
    )
    parser.add_argument(
        "--risk",
        action="store_true",
        help="Показать концентрацию и риски портфеля",
    )
    parser.add_argument(
        "--market",
        action="store_true",
        help="Показать мнение рынка: консенсус аналитиков, инсайдеры, RSI",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Показать историю снимков портфеля из data/history.json",
    )
    parser.add_argument(
        "--screen",
        metavar="JSON",
        help='Screener: {"pe_max": 10, "div_min": 5, "roe_min": 12}',
    )
    parser.add_argument(
        "--universe",
        default="",
        help="Тикеры для скрининга через запятую (по умолчанию — RUB-акции MOEX)",
    )
    parser.add_argument(
        "--bonds",
        metavar="JSON",
        help='Подбор облигаций: {"ytm_min": 11, "coupon_min": 9, "max_items": 60}',
    )
    parser.add_argument(
        "--picks",
        metavar="JSON",
        help='Подбор акций к покупке: {"score_min": 65, "blend": true, "top_n": 15}',
    )
    parser.add_argument(
        "--stress",
        action="store_true",
        help="Стресс-режим подбора акций: тилт в защитные сектора, дивиденды и низкую бету "
        "(также включается настройкой «stress» в data/profile.json)",
    )
    parser.add_argument("--version", action="version", version=f"invest-helper {__version__}")
    parser.add_argument(
        "--tax",
        type=int,
        metavar="YEAR",
        help="Выгрузить справку для декларации 3-НДФЛ за год в data/tax_report_YEAR.txt",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Отправить краткую сводку в Telegram (нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env)",
    )
    parser.add_argument(
        "--news",
        action="store_true",
        help="Новостной фон: дельты целей аналитиков, инсайдеры, RSI, отсечки (+ RSS)",
    )
    parser.add_argument(
        "--goal",
        metavar="JSON",
        help=(
            'Траектория к пенсии: {"income": 100000, "withdraw": 4, "inflation": [7, 10], '
            '"return": 15, "monthly": 20000}'
        ),
    )
    parser.add_argument(
        "--reinvest-deduction",
        action="store_true",
        help="Реинвестировать вычет ИИС (13%% от взносов) в прогноз/траекторию "
        "(по умолчанию — включено автоматически для счёта ИИС)",
    )
    return parser


def _apply_side_effects(report, args, console) -> None:
    """Побочные эффекты: --save и --tax (запись файлов)."""
    if args.save:
        dest = save_snapshot(report.snapshot)
        console.print(f"[dim]Снимок сохранён: {dest}[/dim]")
        save_point(
            report.snapshot,
            net_contrib=report.acct_full.total_in - report.acct_full.total_out,
        )
        console.print("[dim]Точка добавлена в data/history.json[/dim]")
        journal_entries = save_recommendations(report.recommendations, report.snapshot)
        console.print(
            f"[dim]В журнал рекомендаций добавлено/обновлено: {len(journal_entries)}[/dim]"
        )

    if args.tax:
        text = tax_report_text(report.acct_full, args.tax, rate=report.ndfl_rate)
        if text is None:
            console.print(f"[yellow]За {args.tax} год взносов на счёт не найдено — файл не создан.[/]")
        else:
            tax_path = DATA_DIR / f"tax_report_{args.tax}.txt"
            tax_path.write_text(text + "\n", encoding="utf-8")
            console.print(f"[dim]Справка для 3-НДФЛ сохранена: {tax_path}[/dim]")


def _send_telegram(report, console) -> None:
    tg_token, tg_chat = get_telegram_credentials()
    if not tg_token or not tg_chat:
        console.print(
            "[yellow]--telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы "
            "(см. .env.example) — отправка пропущена.[/]"
        )
        return
    text = _telegram_summary(report, report.key_rate, report.usdrub)
    if send_telegram(text):
        console.print("[dim]Сводка отправлена в Telegram.[/dim]")
    else:
        console.print("[yellow]Не удалось отправить сводку в Telegram.[/]")


def run_cli(parser: argparse.ArgumentParser, console) -> int:
    """CLI: один проход — сбор, отрисовка, побочные эффекты."""
    args = parser.parse_args()

    setup = connect(console, account_id=args.account)
    if setup is None:
        return 1
    client, account, profile = setup

    try:
        report = build_report(client, account, args, profile, console)
    except ReportError as exc:
        console.print(f"[bold red]{exc}[/]")
        return 1

    _apply_side_effects(report, args, console)

    if report.data_warnings:
        console.print("[bold yellow]Проблемы загрузки данных:[/]")
        for warning in dict.fromkeys(report.data_warnings):
            console.print(f"[yellow]  {warning}[/]")

    print_all(report, console=console)

    if args.telegram:
        _send_telegram(report, console)

    return 0
