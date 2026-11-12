"""Красивый вывод отчёта в консоль через rich."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .accounting import AccountingReport, ReturnBreakdown
from .analytics import Recommendation
from .contribution_plan import ContributionPlan
from .bonds import BondRow
from .calendar import PaymentCalendar
from .forecast import Forecast, ScenarioForecast, base_scenario
from .goals import GoalReport, GoalStatus, stock_share_hint
from .history import HistoryPoint, max_drawdown, sparkline
from .iis import (
    IIS_TYPE_NAMES,
    TYPE_A_LIMIT,
    ClaimStatus,
    IisInfo,
    IisProgress,
    IisYear,
)
from .market import MarketOpinion
from .news import NewsEvent
from .picks import Pick
from .portfolio import PortfolioSnapshot
from .profile import UserProfile
from .rebalance import RebalancePlan
from .recommendation_history import RecommendationOutcome
from .report_data import ReportData
from .risk import (
    BOND_CORP,
    BOND_HY,
    BOND_OFZ,
    HY_BOND_ALERT_PCT,
    RiskReport,
    bond_risk_breakdown,
    rule_signals,
)
from .screener import ScreenRow
from .simulation import IndexSimulation


def _money(d: Decimal, currency: str = "₽") -> str:
    sign = "-" if d < 0 else ""
    return f"{sign}{abs(d):,.2f} {currency}".replace(",", " ")


def _pct(d: Decimal) -> str:
    return f"{d:+.2f}%"


SIGNAL_DEVIATION_PP = Decimal("5")  # отклонение доли класса активов от цели для сигнала
SIGNAL_PAYMENT_DAYS = 7             # горизонт «скорой» выплаты


def _years_label(years: int) -> str:
    if 10 <= years % 100 <= 20:
        return f"{years} лет"
    last = years % 10
    if last == 1:
        return f"{years} год"
    if 2 <= last <= 4:
        return f"{years} года"
    return f"{years} лет"


def print_all(report: ReportData, console: Console | None = None):
    console = console or Console()

    snapshot = report.snapshot
    acct = report.acct
    recommendations = report.recommendations
    recommendation_entries_count = report.recommendation_entries_count
    recommendation_outcomes = report.recommendation_outcomes
    rebalance_plan = report.rebalance_plan
    contribution_plan = report.contribution_plan
    calendar = report.calendar
    forecast_data = report.forecast_data
    risks = report.risks
    market = report.market
    iis_years = report.iis_years
    iis_progress = report.iis_progress
    iis_info = report.iis_info
    iis_warnings = report.iis_warnings
    tax_notes = report.tax_notes
    claim_statuses = report.claim_statuses
    xirr_pct = report.xirr_pct
    return_breakdown = report.return_breakdown
    twr_pct = report.twr_pct
    benchmark_pct = report.benchmark_pct
    all_time_benchmark_pct = report.all_time_benchmark_pct
    index_simulation = report.index_simulation
    account_opened = report.account_opened
    stress = report.stress
    usdrub = report.usdrub
    key_rate = report.key_rate
    rgbi = report.rgbi
    deposit_pct = report.deposit_pct
    deposit_forecast = report.deposit_forecast
    scenarios = report.scenarios
    goal_statuses = report.goal_statuses
    sector_by_uid = report.sector_by_uid
    screen_rows = report.screen_rows
    history_points = report.history_points
    history_flows = report.history_flows
    bond_rows = report.bond_rows
    picks = report.picks
    news_events = report.news_events
    goal_report = report.goal_report
    profile = report.profile

    # XIRR показываем только когда счёту не меньше 3 лет: на коротком горизонте
    # годовая (money-weighted) доходность волатильна и вводит в заблуждение.
    show_xirr = False
    if account_opened is not None:
        opened = (
            account_opened
            if account_opened.tzinfo is not None
            else account_opened.replace(tzinfo=timezone.utc)
        )
        show_xirr = (datetime.now(timezone.utc) - opened) >= timedelta(days=3 * 365)

    # --- Профиль пользователя ---
    if profile and (profile.target or profile.contributions):
        t = Table(show_header=False, box=None, title="Профиль", title_justify="left")
        t.add_column("Параметр", style="bold cyan")
        t.add_column("Значение")
        t.add_row("Целевая структура", "; ".join(f"{k} {v:.0f}%" for k, v in profile.target.items()))
        if profile.contributions:
            rules = ", ".join(
                f"{c.day}-го {_money(c.amount)}" for c in profile.contributions
            )
            t.add_row("Пополнения", rules)
        console.print(Panel(t, border_style="dim"))

    # --- Сводка портфеля ---
    summary = Table(show_header=False, box=None, title="Портфель (итог)", title_justify="left")
    summary.add_column("Показатель", style="bold cyan")
    summary.add_column("Значение", justify="right")
    summary.add_row("Стоимость портфеля", _money(snapshot.total))
    summary.add_row("Деньги на счету", _money(snapshot.cash))
    summary.add_row("Инвестировано", _money(snapshot.invested))

    pnl = snapshot.total_pnl
    summary.add_row("P&L (нереализованный)", _money(pnl), style=("bold red" if pnl < 0 else "bold green"))
    # Доходность — относительная (в %), из API; фолбэк — P&L / вложено.
    yield_pct = snapshot.total_yield_pct or (
        pnl / snapshot.invested * 100 if snapshot.invested else None
    )
    if yield_pct is not None:
        summary.add_row("Нереализованная доходность", _pct(yield_pct))
    console.print(Panel(summary, border_style="green"))

    # --- Общий доход: реализованный + нереализованный + доходы − издержки ---
    if return_breakdown is not None:
        total_style = "bold green" if return_breakdown.total_return >= 0 else "bold red"
        t = Table(show_header=False, box=None, title="Доходность (итог)", title_justify="left")
        t.add_column("Показатель", style="bold cyan")
        t.add_column("Значение", justify="right")
        total_line = f"[{total_style}]{_money(return_breakdown.total_return)}[/{total_style}]"
        pct = return_breakdown.total_return_pct
        if pct is not None:
            total_line += f"  ({pct:+.2f}% к взносам)"
        t.add_row("Общий доход", total_line)
        if xirr_pct is not None and show_xirr:
            t.add_row("Годовая доходность (XIRR)", f"{xirr_pct:+.2f}%")
        t.add_row("  Реализованный P&L", _money(return_breakdown.realized_pnl))
        t.add_row("  Нереализованный P&L", _money(return_breakdown.unrealized_pnl))
        t.add_row("  Дивиденды + купоны", _money(return_breakdown.income))
        t.add_row("  Комиссии + налоги", _money(-return_breakdown.costs))
        console.print(Panel(t, border_style="green"))
    context = []
    if usdrub is not None:
        context.append(f"USD/RUB {usdrub:.2f} ₽")
    if key_rate is not None:
        context.append(f"Ключевая ставка ЦБ: {key_rate:.2f}%")
    if rgbi is not None:
        context.append(f"RGBI: {rgbi:.2f}")
    if context:
        console.print(f"[dim]Контекст: {' | '.join(context)}[/dim]")

    # --- Позиции ---
    if snapshot.positions:
        t = Table(title="Позиции", header_style="bold yellow")
        t.add_column("Инструмент")
        t.add_column("Тип")
        t.add_column("Кол-во", justify="right")
        t.add_column("Цена", justify="right")
        t.add_column("Средняя", justify="right")
        t.add_column("Стоимость", justify="right")
        t.add_column("Вложено", justify="right")
        t.add_column("P&L", justify="right")
        t.add_column("P&L %", justify="right")
        t.add_column("Див./год", justify="right", style="cyan")

        for p in sorted(snapshot.positions, key=lambda x: -x.amount):
            pnl_color = "green" if p.pnl >= 0 else "red"
            t.add_row(
                f"[bold]{p.ticker}[/bold] {p.name}",
                p.type_name,
                f"{p.quantity:g}",
                _money(p.current_price),
                _money(p.avg_price),
                _money(p.amount),
                _money(p.invested),
                f"[{pnl_color}]{_money(p.pnl)}[/{pnl_color}]",
                f"[{pnl_color}]{_pct(p.pnl_pct)}[/{pnl_color}]",
                _money(p.dividend_amount),
            )
        console.print(Panel(t, border_style="yellow"))

    # --- Сверка P&L с брокером (наш amount − invested vs expected_yield) ---
    _diverged = [
        p for p in snapshot.positions
        if p.invested and abs(p.pnl - p.pnl_derived) > Decimal("1.00")
    ]
    if _diverged:
        lines = []
        for p in _diverged:
            delta = p.pnl - p.pnl_derived
            hint = " — ≈ накопленный НКД" if p.instrument_type == "bond" else ""
            lines.append(
                f"{p.ticker}: брокер {_money(p.pnl)} vs наш расчёт {_money(p.pnl_derived)} "
                f"(Δ {_money(delta)}){hint}"
            )
        console.print(Panel("\n".join(lines), title="Сверка P&L с брокером", border_style="dim"))

    # --- Структура ---
    if snapshot.allocation:
        t = Table(title="Структура активов", header_style="bold magenta")
        t.add_column("Класс активов")
        t.add_column("Сумма", justify="right")
        t.add_column("Доля", justify="right")
        for a in snapshot.allocation:
            share = a.amount / snapshot.total * 100 if snapshot.total else Decimal("0")
            t.add_row(a.asset_class, _money(a.amount), f"{share:.1f}%")
        console.print(Panel(t, border_style="magenta"))
        bond_breakdown = bond_risk_breakdown(snapshot)
        if bond_breakdown:
            parts = [
                f"{cat} {_money(bond_breakdown[cat])}"
                for cat in (BOND_OFZ, BOND_CORP)
                if bond_breakdown.get(cat)
            ]
            hy = bond_breakdown.get(BOND_HY)
            hy_str = ""
            if hy and parts:
                hy_str = f" (высокий риск {_money(hy)})"
            elif hy:
                parts.append(f"{BOND_HY} {_money(hy)}")
            console.print(f"[dim]Облигации: {' / '.join(parts)}{hy_str}[/dim]")

    # --- Бухгалтерия за период ---
    t = Table(title="Бухгалтерия за период", header_style="bold blue")
    t.add_column("Показатель")
    t.add_column("Сумма", justify="right")
    t.add_row("Внесено на счёт", _money(acct.total_in))
    t.add_row("Выведено", _money(acct.total_out))
    t.add_row("Объём покупок", _money(acct.total_buy))
    t.add_row("Объём продаж", _money(acct.total_sell), style="green")
    t.add_row("Дивиденды", _money(acct.total_dividends), style="cyan")
    t.add_row("Купоны", _money(acct.total_coupons), style="cyan")
    t.add_row("Комиссии", _money(acct.total_fees))
    t.add_row("Налоги", _money(acct.total_taxes))
    t.add_row("Операций", str(acct.operations_count), style="bold white")

    cashflow = (
        acct.total_in
        + acct.total_dividends
        + acct.total_coupons
        - acct.total_out
        - acct.total_fees
        - acct.total_taxes
    )
    t.add_row("Чистый денежный поток", _money(cashflow), style=("bold red" if cashflow < 0 else "bold green"))
    console.print(Panel(t, border_style="blue"))

    # --- По инструментам ---
    if acct.by_instrument:
        t = Table(title="Сделки по инструментам", header_style="bold cyan")
        t.add_column("Инструмент")
        t.add_column("Куплено", justify="right")
        t.add_column("Продано", justify="right")
        t.add_column("Дивиденды/купоны", justify="right")
        for ticker, d in sorted(acct.by_instrument.items(), key=lambda x: x[1]["buy"], reverse=True):
            t.add_row(
                ticker,
                _money(d["buy"]),
                _money(d["sell"]),
                _money(d["div"]),
            )
        console.print(Panel(t, border_style="cyan"))

    # --- Дивиденды от текущих позиций (прогноз) ---
    div_positions = [
        p for p in snapshot.positions
        if p.dividend_share > 0 or p.dividend_amount > 0
    ]
    if div_positions:
        t = Table(
            title="Дивидендная доходность позиций (выплаты за последние 365 дней)",
            header_style="bold green",
        )
        t.add_column("Инструмент")
        t.add_column("Див. на акцию (год)", justify="right")
        t.add_column("Див. доходность", justify="right")
        for p in div_positions:
            per_share_year = p.dividend_amount or p.dividend_share
            if per_share_year > 0:
                yield_pct = per_share_year / p.current_price * 100 if p.current_price else Decimal("0")
                t.add_row(p.ticker, _money(per_share_year), f"{yield_pct:.2f}%")
        console.print(Panel(
            t,
            border_style="green",
            subtitle="Суммы — нетто (после удержания НДФЛ при выплате)",
        ))

    # --- Аналитика: фундаментальные показатели ---
    recs = list(recommendations) if recommendations else []
    if recs:
        t = Table(title="Фундаментальные показатели (TTM)", header_style="bold cyan")
        t.add_column("Инструмент")
        t.add_column("P/E")
        t.add_column("P/B")
        t.add_column("ROE %")
        t.add_column("Див. доход. %")
        t.add_column("Payout %")
        t.add_column("Выручка Y/Y %")
        t.add_column("Долг/Кап. %")
        for r in recs:
            t.add_row(
                f"[bold]{r.ticker}[/bold] {r.name}",
                f"{r.pe:.1f}" if r.pe is not None else "—",
                f"{r.pb:.1f}" if r.pb is not None else "—",
                f"{r.roe:.1f}" if r.roe is not None else "—",
                f"{r.div_yield:.1f}" if r.div_yield is not None else "—",
                f"{r.payout:.0f}" if r.payout is not None else "—",
                f"{r.rev_growth:+.1f}" if r.rev_growth is not None else "—",
                f"{r.debt_eq:.0f}" if r.debt_eq is not None else "—",
            )
        console.print(Panel(t, border_style="cyan"))

    # --- Аналитика: рекомендации ---
    if recs:
        action_style = {
            "Покупать": "bold green",
            "Держать": "bold yellow",
            "Продавать": "bold red",
            "Нет данных": "dim",
        }
        t = Table(title="Рекомендации", header_style="bold yellow")
        t.add_column("Инструмент")
        t.add_column("Действие")
        t.add_column("Оценка", justify="right")
        t.add_column("Прогноз див. дохода/год")
        t.add_column("За", max_width=40)
        t.add_column("Риски", max_width=30)
        for r in recs:
            style = action_style.get(r.action, "bold")
            score_str = f"[{style}]{r.score}[/{style}]" if r.action != "Нет данных" else "—"
            risk_text = "; ".join(r.risks) if r.risks else "(не выделены)"
            t.add_row(
                f"[bold]{r.ticker}[/bold] {r.name}",
                f"[{style}]{r.action}[/{style}]",
                score_str,
                _money(r.forward_dividend_income),
                "; ".join(r.reasons) if r.reasons else "—",
                risk_text,
            )
        console.print(Panel(t, border_style="yellow"))

    # --- Журнал эффективности рекомендаций ---
    if recommendation_entries_count:
        if recommendation_outcomes:
            t = Table(title="Проверка рекомендаций против IMOEX", header_style="bold cyan")
            t.add_column("Тикер")
            t.add_column("Дата")
            t.add_column("Действие")
            t.add_column("Горизонт", justify="right")
            t.add_column("Доходность", justify="right")
            t.add_column("IMOEX", justify="right")
            t.add_column("Разница", justify="right")
            for outcome in recommendation_outcomes[:15]:
                excess_style = "green" if outcome.excess_pct >= 0 else "red"
                t.add_row(
                    outcome.ticker,
                    outcome.recommendation_date,
                    outcome.action,
                    f"{outcome.horizon_days} дн.",
                    f"{outcome.return_pct:+.2f}%",
                    f"{outcome.benchmark_pct:+.2f}%",
                    f"[{excess_style}]{outcome.excess_pct:+.2f}%[/{excess_style}]",
                )
            console.print(Panel(t, border_style="cyan"))
        else:
            console.print(
                f"[dim]Журнал рекомендаций: {recommendation_entries_count} записей. "
                "Сравнение с IMOEX появится через 90, 180 и 365 дней.[/dim]"
            )

    # --- Ребалансировка ---
    if rebalance_plan and rebalance_plan.recommendations:
        t = Table(title="Ребалансировка", header_style="bold magenta")
        t.add_column("Класс активов")
        t.add_column("Сейчас", justify="right")
        t.add_column("Цель", justify="right")
        t.add_column("Отклонение", justify="right")
        t.add_column(f"К покупке {_money(rebalance_plan.invest_amount)}", justify="right")
        for r in rebalance_plan.recommendations:
            deviation = r.current_pct - r.target_pct
            style = "red" if deviation > 1 else ("green" if deviation < -1 else "default")
            invest_str = _money(r.invest_amount) if r.invest_amount > 0 else "—"
            t.add_row(
                r.asset_class,
                f"{r.current_pct:.1f}%",
                f"{r.target_pct:.0f}%",
                f"[{style}]{deviation:+.1f}%[/{style}]",
                invest_str,
            )
        budget_sub = (
            f"Бюджет: остаток на счёте {_money(rebalance_plan.cash)} + "
            f"пополнение {_money(rebalance_plan.contrib)}"
        )
        console.print(Panel(t, border_style="magenta", subtitle=budget_sub))
        if rebalance_plan.unallocated > 0:
            console.print(
                f"[dim]Остаток сверх целевой структуры: {_money(rebalance_plan.unallocated)} — "
                f"оставьте на счёте до следующего пополнения[/dim]"
            )

    # --- Виртуальный план пополнения ---
    if contribution_plan and contribution_plan.budget > 0:
        plan = contribution_plan
        t = Table(title="Виртуальный план пополнения — заявки не отправляются", header_style="bold green")
        t.add_column("Инструмент")
        t.add_column("Класс")
        t.add_column("Цена", justify="right")
        t.add_column("Лоты", justify="right")
        t.add_column("Кол-во", justify="right")
        t.add_column("Сумма", justify="right")
        t.add_column("Комиссия ~0,3%", justify="right")
        for item in plan.items:
            t.add_row(
                f"[bold]{item.ticker}[/bold] {item.name}",
                item.asset_class,
                _money(item.price),
                f"{item.lots} × {item.lot}",
                f"{item.quantity:g}",
                _money(item.amount),
                _money(item.commission),
            )
        subtitle = (
            f"Бюджет {_money(plan.budget)} | покупки {_money(plan.spent)} | "
            f"комиссия {_money(plan.commission)} | остаток {_money(plan.unallocated)}"
        )
        console.print(Panel(t, border_style="green", subtitle=subtitle))
        final_total = snapshot.total + plan.contribution
        if final_total > 0:
            structure = "; ".join(
                f"{item.asset_class} {item.amount / final_total * 100:.1f}%"
                for item in plan.final_allocation
            )
            console.print(f"[dim]Структура после виртуального пополнения: {structure}[/dim]")
        for note in plan.notes:
            console.print(f"[yellow]{note}[/yellow]")

    # --- Календарь выплат ---
    if calendar and calendar.events:
        t = Table(title="Календарь выплат (ближайшие 365 дней)", header_style="bold cyan")
        t.add_column("Дата")
        t.add_column("Инструмент")
        t.add_column("Тип")
        t.add_column("На единицу", justify="right")
        t.add_column("Кол-во", justify="right")
        t.add_column("Сумма", justify="right")
        t.add_column("Нетто", justify="right")
        for e in calendar.events:
            t.add_row(
                e.date.strftime("%d.%m.%Y"),
                f"{e.ticker} {e.name}",
                e.kind,
                _money(e.per_unit),
                f"{e.quantity:g}",
                _money(e.amount),
                _money(e.amount_net) if e.amount_net is not None else "—",
            )
        if calendar.by_month():
            t.add_section()
            for month, amount in sorted(calendar.by_month().items()):
                t.add_row(f"[bold]{month}[/bold]", "", "ИТОГО", "", "", f"[bold]{_money(amount)}[/bold]", "")
            t.add_row(f"[bold]Всего[/bold]", "", "", "", "", f"[bold]{_money(calendar.total)}[/bold]", "")
        console.print(Panel(
            t,
            border_style="cyan",
            subtitle="Дивиденды — уже нетто (после НДФЛ); «Нетто» для купонов — оценка за вычетом НДФЛ",
        ))

    # --- Прогноз ---
    if forecast_data:
        t = Table(title="Прогноз роста портфеля", header_style="bold green")
        t.add_column("Горизонт")
        t.add_column("Баланс", justify="right")
        t.add_column("Вложено", justify="right")
        t.add_column("Доход", justify="right")
        if forecast_data.inflation_pct is not None:
            t.add_column("В сегодняшних деньгах", justify="right")
        for p in forecast_data.points:
            row = [
                _years_label(p.years),
                _money(p.balance),
                _money(p.contributed),
                f"[{'green' if p.profit >= 0 else 'red'}]{_money(p.profit)}[/{'green' if p.profit >= 0 else 'red'}]",
            ]
            if forecast_data.inflation_pct is not None:
                row.append(_money(p.balance_real) if p.balance_real is not None else "—")
            t.add_row(*row)
        subtitle = f"Пополнения {_money(forecast_data.monthly)}/мес, доходность {forecast_data.annual_return_pct:.1f}% в год"
        if forecast_data.annual_deduction > 0:
            subtitle += (
                f"; реинвест. вычета ИИС {_money(forecast_data.annual_deduction)}/год"
                f" → +{_money(forecast_data.final.deduction_gain)} к итогу"
            )
        if forecast_data.inflation_pct is not None:
            subtitle += f"; инфляция {forecast_data.inflation_pct:.0f}% (оценка)"
        console.print(Panel(
            t,
            border_style="green",
            subtitle=subtitle,
        ))

    # --- Прогноз: сценарии ---
    if scenarios:
        base = base_scenario(scenarios)
        spread = scenarios[-1].annual_return_pct - base.annual_return_pct
        t = Table(title="Сценарии прогноза", header_style="bold green")
        t.add_column("Горизонт")
        for s in scenarios:
            t.add_column(f"{s.label} ({s.annual_return_pct:.0f}%)", justify="right")
        if base.forecast.inflation_pct is not None:
            t.add_column("База в сегод. деньгах", justify="right")
        for i, years in enumerate([p.years for p in base.forecast.points]):
            row = [_years_label(years)]
            row += [_money(s.forecast.points[i].balance) for s in scenarios]
            if base.forecast.inflation_pct is not None:
                real = base.forecast.points[i].balance_real
                row.append(_money(real) if real is not None else "—")
            t.add_row(*row)
        console.print(Panel(
            t,
            border_style="green",
            subtitle=f"Доходность базового сценария ±{spread:.0f} п.п.; остальные параметры те же",
        ))

    # --- Прогноз: альтернатива вкладу ---
    if forecast_data and deposit_forecast and key_rate is not None:
        horizon = forecast_data.final.years
        console.print(
            f"[dim]Альтернатива: вклад под ключевую ставку ({key_rate:.2f}%) дал бы "
            f"{_money(deposit_forecast.final.balance)} за {_years_label(horizon)} "
            f"(против {_money(forecast_data.final.balance)} по базовому сценарию)[/dim]"
        )

    # --- Траектория к пенсии ---
    if goal_report:
        t = Table(title="Траектория к пенсии", header_style="bold magenta")
        t.add_column("Параметр")
        t.add_column("Значение", justify="right")
        t.add_row("Расходы сегодня (цель пассивного дохода)", _money(goal_report.income))
        t.add_row("Норма изъятия", f"{goal_report.withdraw_pct:.0f}% в год")
        t.add_row("Нужный капитал (реальные деньги)", _money(goal_report.target_real), style="bold")
        t.add_row("Сейчас в портфеле", _money(goal_report.start))
        t.add_row("Пройдено траектории", f"[bold]{goal_report.progress_pct:.1f}%[/bold]")
        t.add_row("Вклад в месяц (реальные)", _money(goal_report.monthly))
        if goal_report.annual_deduction > 0:
            t.add_row(
                "Реинвестирование вычета ИИС",
                f"[bold green]+{_money(goal_report.annual_deduction)} в год[/bold green]",
            )
        console.print(Panel(t, border_style="magenta"))

        t = Table(title="Годы до цели (реальные деньги)", header_style="bold magenta")
        t.add_column("Инфляция")
        t.add_column("Реальная доходность")
        t.add_column("Лет до цели")
        t.add_column("Номинал к дате", justify="right")
        for s in goal_report.scenarios:
            years = f"[bold]{s.years_to_goal:.0f}[/bold]" if s.years_to_goal is not None else "> 60"
            nominal = _money(s.nominal_target) if s.nominal_target else "—"
            t.add_row(f"{s.inflation_pct:.0f}%", f"{s.real_return_pct:.1f}%", years, nominal)
        console.print(Panel(
            t,
            border_style="magenta",
            subtitle=f"Доходность номинальная {goal_report.return_pct:.0f}% в год",
        ))

        if goal_report.rows:
            year_rows = goal_report.rows[::5]
            if goal_report.rows[-1] not in year_rows:
                year_rows.append(goal_report.rows[-1])
            t = Table(title="Баланс по годам (инфляция {:.0f}%)".format(goal_report.base_inflation_pct), header_style="bold magenta")
            t.add_column("Год")
            t.add_column("Баланс (реальн.)", justify="right")
            t.add_column("Номинал ({:.0f}%)".format(goal_report.base_inflation_pct), justify="right")
            t.add_column("Пассив/мес (реальн.)", justify="right")
            for r in year_rows:
                t.add_row(str(r.year), _money(r.balance_real), _money(r.nominal_base), _money(r.passive_monthly))
            console.print(Panel(t, border_style="magenta"))

    # --- Цели из профиля ---
    if goal_statuses:
        t = Table(title="Цели из профиля", header_style="bold magenta")
        t.add_column("Цель")
        t.add_column("Сумма", justify="right")
        t.add_column("Горизонт")
        t.add_column("Прогноз", justify="right")
        t.add_column("Статус")
        t.add_column("Доля акций")
        for gs in goal_statuses:
            status = (
                "[bold green]успеваете[/bold green]"
                if gs.feasible
                else (
                    f"[bold red]не успеваете[/bold red] — нужно {_money(gs.needed_monthly)}/мес"
                    if gs.needed_monthly is not None
                    else "[bold red]не успеваете[/bold red]"
                )
            )
            t.add_row(
                gs.goal.name,
                _money(gs.goal.target),
                _years_label(gs.goal.years),
                _money(gs.projected),
                status,
                stock_share_hint(gs.goal.years),
            )
        console.print(Panel(
            t,
            border_style="magenta",
            subtitle="Прогноз — при текущем пополнении и базовой доходности; каждая цель оценивается отдельно от всего портфеля",
        ))

    # --- Мнение рынка: консенсус аналитиков ---
    if market and market.analysts:
        rec_style = {"Покупать": "bold green", "Держать": "bold yellow", "Продавать": "bold red"}
        t = Table(title="Консенсус аналитиков", header_style="bold cyan")
        t.add_column("Инструмент")
        t.add_column("Рекомендация")
        t.add_column("Цена", justify="right")
        t.add_column("Цель", justify="right")
        t.add_column("Потенциал", justify="right")
        t.add_column("Диапазон цели")
        t.add_column("Аналитиков", justify="right")
        for a in market.analysts:
            style = rec_style.get(a.recommendation, "default")
            upside = a.change_rel_pct
            up_str = f"[{'green' if upside and upside > 0 else 'red'}]{upside:+.1f}%[/{'green' if upside and upside > 0 else 'red'}]" if upside is not None else "—"
            range_str = f"{_money(a.min_target)}–{_money(a.max_target)}" if a.min_target is not None and a.max_target is not None else "—"
            t.add_row(
                f"[bold]{a.ticker}[/bold] {a.name}",
                f"[{style}]{a.recommendation}[/{style}]",
                _money(a.current_price),
                _money(a.target_price),
                up_str,
                range_str,
                str(a.n_targets),
            )
        console.print(Panel(t, border_style="cyan"))

    # --- Мнение рынка: сделки инсайдеров ---
    if market and market.insiders:
        t = Table(title="Сделки инсайдеров (последние 365 дней)", header_style="bold cyan")
        t.add_column("Дата")
        t.add_column("Инструмент")
        t.add_column("Инсайдер")
        t.add_column("Должность")
        t.add_column("Направление")
        t.add_column("Кол-во", justify="right")
        t.add_column("Цена", justify="right")
        for e in market.insiders:
            dir_style = "green" if e.direction == "Покупка" else ("red" if e.direction == "Продажа" else "default")
            t.add_row(
                e.disclosure_date.strftime("%d.%m.%Y") if e.disclosure_date else "—",
                e.ticker,
                e.investor_name,
                e.position,
                f"[{dir_style}]{e.direction}[/{dir_style}]",
                f"{e.quantity:,}".replace(",", " "),
                _money(e.price),
            )
        console.print(Panel(t, border_style="cyan"))

    # --- Мнение рынка: RSI ---
    if market and market.tech:
        t = Table(title="RSI (14, дневной)", header_style="bold cyan")
        t.add_column("Инструмент")
        t.add_column("RSI", justify="right")
        t.add_column("Зона")
        for v in market.tech:
            if v.rsi is None:
                t.add_row(v.ticker, "—", "нет данных")
                continue
            if v.rsi >= 70:
                zone, zs = "Перекупленность", "red"
            elif v.rsi <= 30:
                zone, zs = "Перепроданность", "green"
            else:
                zone, zs = "Нейтрально", "default"
            t.add_row(v.ticker, f"{v.rsi:.1f}", f"[{zs}]{zone}[/{zs}]")
        console.print(Panel(t, border_style="cyan"))

    # --- Риски и концентрация ---
    if risks and risks.items:
        t = Table(title="Концентрация и риски", header_style="bold red")
        t.add_column("Тип")
        t.add_column("Объект")
        t.add_column("Сумма", justify="right")
        t.add_column("Доля", justify="right")
        for r in risks.items:
            style = "red" if r.alert else "default"
            t.add_row(
                r.kind,
                f"[{style}]{r.name}[/{style}]",
                _money(r.amount),
                f"[{style}]{r.pct:.1f}%[/{style}]",
            )
        beta_row = []
        if risks.weighted_beta is not None:
            beta_row.append(f"Средний β портфеля: {risks.weighted_beta}")
        extra = "; ".join(beta_row)
        console.print(Panel(t, border_style="red", subtitle=extra or None))

        if risks.alerts:
            t = Table(title="Предупреждения", header_style="bold red", show_header=False, box=None)
            for a in risks.alerts:
                t.add_row(f"[bold red]⚠ {a}[/bold red]")
            console.print(Panel(t, border_style="red"))

    # --- ИИС-вычет ---
    if iis_years or iis_info:
        ndfl = iis_info.ndfl_rate if iis_info else Decimal("0.13")
        title = "ИИС: взносы и налоговый вычет (тип А)"
        if iis_info:
            type_name = IIS_TYPE_NAMES.get(iis_info.iis_type, iis_info.iis_type)
            auto = " [dim](тип определён автоматически)[/dim]" if iis_info.type_auto else ""
            title = f"{type_name}: взносы и налоговые вычеты{auto}"
        t = Table(title=title, header_style="bold blue")
        t.add_column("Год")
        t.add_column("Внесено", justify="right")
        t.add_column("В зачёт лимита", justify="right")
        t.add_column(f"Возврат {ndfl:.0%}", justify="right")
        for y in iis_years or []:
            over = " [red]↑ сверх лимита[/red]" if y.over_limit else ""
            t.add_row(
                f"{y.year}",
                _money(y.contributed) + over,
                _money(y.capped),
                f"[bold green]{_money(y.potential_return)}[/bold green]",
            )
        subtitle = (
            f"Вычет на взносы: база до {_money(TYPE_A_LIMIT)}/год, "
            f"возврат до {_money((TYPE_A_LIMIT * ndfl).quantize(Decimal('0.01')))} при ставке {ndfl:.0%}"
        )
        if iis_info and iis_info.iis_type == "iis3":
            subtitle += "; вычет на доход: освобождение до 30 млн ₽ при закрытии после мин. срока (кроме дивидендов/купонов)"
        console.print(Panel(
            t,
            border_style="blue",
            subtitle=subtitle,
        ))
        if iis_info and iis_info.iis_type == "iis3":
            term = iis_info.term
            if term is None:
                console.print(
                    "[yellow]Дата открытия ИИС неизвестна — укажите iis.open_date "
                    "в data/profile.json, чтобы видеть срок владения ИИС-3.[/]"
                )
            elif term.expired:
                console.print(
                    f"[blue]Минимальный срок владения ИИС-3 ({term.min_years} л.) истёк — "
                    f"закрытие без потери вычетов.[/]"
                )
            else:
                left = []
                if term.years_left:
                    left.append(_years_label(term.years_left))
                if term.months_left or not left:
                    left.append(f"{term.months_left} мес.")
                console.print(
                    f"[blue]Минимальный срок владения ИИС-3 ({term.min_years} л.) истекает "
                    f"{term.mature_date:%d.%m.%Y} — осталось {' '.join(left)}[/]"
                )
        if tax_notes:
            for note in tax_notes:
                console.print(f"[dim]📅 {note}[/dim]")
        for warning in iis_warnings or []:
            console.print(f"[bold red]⚠ {warning}[/bold red]")
        if claim_statuses:
            console.print("[bold blue]Незаявленные вычеты:[/bold blue]")
            for cs in claim_statuses:
                if cs.status == "early":
                    note = f"заявляется в {cs.year + 1}"
                elif cs.status == "available":
                    note = "можно заявить уже сейчас"
                else:
                    note = "срок заявления истёк (3 года) — вычет сгорел"
                line = f"  {cs.year} — {_money(cs.potential_return)} ({note})"
                style = "bold red" if cs.status == "expired" else "blue"
                console.print(f"[{style}]{line}[/{style}]")

    # --- ИИС: прогресс до лимита вычета за текущий год ---
    if iis_progress:
        t = Table(title=f"ИИС: прогресс вычета за {iis_progress.year} год", header_style="bold blue")
        t.add_column("Показатель")
        t.add_column("Сумма", justify="right")
        t.add_row("Внесено за год", _money(iis_progress.contributed))
        t.add_row("Лимит вычета", _money(iis_progress.limit))
        t.add_row("Осталось до лимита", _money(iis_progress.remaining))
        t.add_row(
            f"Недобрано вычета ({(iis_info.ndfl_rate if iis_info else Decimal('0.13')):.0%})",
            f"[bold green]{_money(iis_progress.missing_return)}[/bold green]",
        )
        console.print(Panel(t, border_style="blue"))

    # --- Screener ---
    if screen_rows:
        t = Table(title="Screener: подходящие по фильтрам", header_style="bold green")
        t.add_column("Тикер")
        t.add_column("Название")
        t.add_column("Сектор")
        t.add_column("P/E", justify="right")
        t.add_column("Див. %", justify="right")
        t.add_column("ROE %", justify="right")
        t.add_column("Выр. Y/Y %", justify="right")
        t.add_column("Долг/кап %", justify="right")
        t.add_column("Кап, млрд ₽", justify="right")
        for r in screen_rows:
            t.add_row(
                f"[bold cyan]{r.ticker}[/bold cyan]",
                r.name,
                r.sector,
                f"{r.pe:.1f}" if r.pe is not None else "—",
                f"[bold]{r.div_yield:.1f}[/bold]" if r.div_yield is not None else "—",
                f"{r.roe:.1f}" if r.roe is not None else "—",
                f"{r.rev_growth:+.1f}" if r.rev_growth is not None else "—",
                f"{r.debt_eq:.0f}" if r.debt_eq is not None else "—",
                f"{r.mcap_bln:.1f}" if r.mcap_bln is not None else "—",
            )
        console.print(Panel(t, border_style="green"))

    # --- Новостной фон ---
    if news_events:
        t = Table(title="Новостной фон", header_style="bold cyan")
        t.add_column("Сигнал")
        t.add_column("Тикер")
        t.add_column("Когда")
        t.add_column("Событие")
        for e in news_events:
            tone_style = {"позитив": "green", "негатив": "red", "нейтр": "dim"}.get(e.tone or "", "dim")
            when = e.ts.strftime("%d.%m %H:%M") if e.ts else "—"
            t.add_row(
                e.kind,
                ("⚠ " + e.ticker) if e.alert else e.ticker,
                when,
                f"[{tone_style}]{e.text}[/{tone_style}]",
            )
        console.print(Panel(t, border_style="cyan"))
        alert_tickers = {e.ticker for e in news_events if e.alert}
    else:
        alert_tickers = set()

    # --- Подбор акций к покупке ---
    if picks:
        t = Table(title="Подбор акций к покупке (бленд: фундамент + консенсус + RSI)", header_style="bold green")
        t.add_column("Тикер")
        t.add_column("Название", max_width=16)
        t.add_column("Сектор", max_width=10)
        t.add_column("Фунд.", justify="right")
        t.add_column("Рейт.", justify="right")
        t.add_column("P/E", justify="right")
        t.add_column("Див. %", justify="right")
        t.add_column("Цель, %", justify="right")
        t.add_column("RSI", justify="right")
        t.add_column("Действие")
        t.add_column("Заметки", overflow="fold", max_width=30)
        for p in picks:
            rating = (
                f"[bold]{p.blend_score}[/bold]"
                if p.blend_score is not None
                else f"[dim]{p.fund_score}[/dim]"
            )
            act_style = {"Покупать": "bold green", "Держать": "yellow", "Продавать": "bold red"}.get(p.action, "")
            act = f"[{act_style}]{p.action}[/{act_style}]" if act_style else p.action
            notes = f"[dim]{', '.join(p.notes)}[/dim]" if p.notes else ""
            ticker_cell = f"[bold cyan]{p.ticker}[/bold cyan]"
            if p.ticker in alert_tickers:
                ticker_cell += " [red]⚠[/red]"
            t.add_row(
                ticker_cell,
                p.name[:18],
                p.sector,
                f"{p.fund_score}",
                rating,
                f"{p.pe:.1f}" if p.pe is not None else "—",
                f"{p.div_yield:.1f}" if p.div_yield is not None else "—",
                f"{p.potential_pct:+.1f}" if p.potential_pct is not None else "—",
                f"{p.rsi:.0f}" if p.rsi is not None else "—",
                act,
                notes,
            )
        subtitle = "Вес: 50% фундамент / 30% консенсус / 20% RSI"
        if stress:
            subtitle += (
                " | СТРЕСС-РЕЖИМ: +10 защитным секторам, −10 циклическим, "
                "выше вес дивидендов и низкой беты"
            )
        console.print(
            Panel(
                t,
                border_style="green",
                subtitle=subtitle,
            )
        )

# --- История снимков ---
    if history_points and len(history_points) > 1:
        t = Table(title="История портфеля", header_style="bold yellow")
        t.add_column("Дата")
        t.add_column("Стоимость", justify="right")
        t.add_column("Вложено", justify="right")
        t.add_column("Внесено", justify="right")
        t.add_column("Доход, %", justify="right")
        for h in history_points:
            d = (
                f"[green]+{h.pnl_pct:.2f}%[/green]"
                if h.pnl_pct >= 0
                else f"[red]{h.pnl_pct:.2f}%[/red]"
            )
            t.add_row(
                h.date,
                _money(h.total),
                _money(h.invested),
                _money(h.net_contrib) if h.net_contrib is not None else "—",
                d,
            )
        console.print(Panel(t, border_style="yellow"))
        spark = sparkline(history_points)
        if spark:
            console.print(f"[dim]{spark}[/dim]")
        last = history_points[-1]
        if last.net_contrib is not None and last.net_contrib > 0:
            growth = last.total - last.net_contrib
            growth_pct = growth / last.net_contrib * 100
            prefix = (
                f"С даты открытия счёта ({account_opened:%d.%m.%Y})"
                if account_opened
                else "За доступную историю"
            )
            console.print(
                f"[dim]{prefix}: чистые взносы {_money(last.net_contrib)} | "
                f"доход {_money(growth)} ({growth_pct:+.2f}% к взносам)[/dim]"
            )
        if all_time_benchmark_pct is not None:
            console.print(
                f"[dim]IMOEX со дня открытия счёта до {last.date}: "
                f"{all_time_benchmark_pct:+.2f}% (без дивидендов)[/dim]"
            )
        if index_simulation is not None:
            simulation = index_simulation
            console.print(
                f"[dim]Те же взносы в IMOEX: {_money(simulation.final_value)} "
                f"({simulation.profit_pct:+.2f}% к взносам) | "
                f"реальный портфель минус симуляция: {_money(simulation.difference)}[/dim]"
            )
            console.print("[dim]Симуляция без дивидендов и комиссий; реальные данные не изменены[/dim]")
        if twr_pct is not None:
            console.print(
                f"[dim]Ваша доходность (TWR) за {history_points[0].date} — {last.date}: "
                f"{twr_pct:+.2f}%[/dim]"
            )
        if benchmark_pct is not None:
            console.print(
                f"[dim]Индекс МосБиржи (IMOEX, без дивидендов) за тот же период: "
                f"{benchmark_pct:+.2f}%[/dim]"
            )
        if xirr_pct is not None and show_xirr:
            console.print(
                f"[dim]XIRR за всю историю счёта: {xirr_pct:+.2f}% годовых[/dim]"
            )
        if deposit_pct is not None and key_rate is not None:
            console.print(
                f"[dim]Вклад под ключевую ставку ({key_rate:.2f}%) за тот же период: "
                f"+{deposit_pct:.2f}% (простой процент)[/dim]"
            )
        dd = max_drawdown(history_points, history_flows)
        if dd is not None:
            label = "макс. просадка TWR" if history_flows is not None else "макс. просадка"
            console.print(f"[dim]Худший момент ({label}): {dd[0]:.2f}% ({dd[1]})[/dim]")

    # --- Подбор облигаций ---
    if bond_rows:
        t = Table(title="Подбор облигаций по доходности (YTM)", header_style="bold green")
        t.add_column("Тикер")
        t.add_column("Название")
        t.add_column("Сектор")
        t.add_column("Цена, %", justify="right")
        t.add_column("Купон, ₽/год", justify="right")
        t.add_column("Купон %", justify="right")
        t.add_column("К погаш. %", justify="right")
        t.add_column("След. купон", justify="right")
        t.add_column("Погашение")
        t.add_column("Лет", justify="right")
        t.add_column("Заметки")
        for r in bond_rows:
            ytm = (
                f"[bold]{r.ytm_approx_pct:.2f}[/bold]" if r.ytm_approx_pct is not None else "—"
            )
            first_c = ""
            if r.first_coupon_date:
                first_c = (
                    f"{r.first_coupon_date:%d.%m.%Y} | {r.first_coupon:.2f} ₽"
                )
            mat = f"{r.maturity_date:%m.%Y}" if r.maturity_date else "—"
            notes = ", ".join(r.notes) if r.notes else ""
            t.add_row(
                r.ticker,
                r.name[:26],
                r.sector,
                f"{r.price_pct:.2f}",
                f"{r.annual_coupon:.2f}",
                f"{r.coupon_yield_pct:.2f}",
                ytm,
                first_c,
                mat,
                f"{r.years_left:.1f}",
                notes,
            )
        console.print(
            Panel(
                t,
                border_style="green",
                subtitle="YTM по купонам до погашения; флоатеры и амортизация — без YTM (помечены, в конце списка)",
            )
        )

    # --- Сигналы ---
    signals: list[str] = []
    if profile:
        signals.extend(rule_signals(snapshot, profile.rules, sector_by_uid))
    if profile and snapshot.total:
        actual_by_class = {a.asset_class: a.amount for a in snapshot.allocation}
        for cls in set(actual_by_class) | set(profile.target):
            actual_pct = actual_by_class.get(cls, Decimal("0")) / snapshot.total * 100
            target_pct = profile.target.get(cls, Decimal("0"))
            dev = actual_pct - target_pct
            if abs(dev) > SIGNAL_DEVIATION_PP:
                signals.append(
                    f"Доля «{cls}» {actual_pct:.1f}% при цели {target_pct:.0f}% "
                    f"(отклонение {dev:+.1f} п.п.) — пора ребалансировать"
                )
    if usdrub is not None and profile and usdrub > profile.usdrub_alert:
        signals.append(f"USD/RUB {usdrub:.2f} ₽ выше порога {profile.usdrub_alert:.0f} ₽")
    hy_amount = bond_risk_breakdown(snapshot).get(BOND_HY)
    if hy_amount and snapshot.total:
        hy_pct = hy_amount / snapshot.total * 100
        if hy_pct > HY_BOND_ALERT_PCT:
            signals.append(
                f"Высокодоходные облигации {hy_pct:.1f}% портфеля ({_money(hy_amount)}) — "
                "это не защитная часть"
            )
    if calendar and calendar.events:
        now = datetime.now(timezone.utc)
        for e in calendar.events:
            days = (e.date - now).days
            if 0 <= days < SIGNAL_PAYMENT_DAYS:
                signals.append(
                    f"Скоро выплата: {e.ticker} {e.kind} {e.date:%d.%m.%Y} — {_money(e.amount)}"
                )
    if profile and not profile.emergency_fund_ok and iis_info and iis_info.iis_type == "iis3":
        term = iis_info.term
        if term and not term.expired:
            signals.append(
                f"Деньги на ИИС-3 заморожены до {term.mature_date:%d.%m.%Y} — убедитесь, "
                "что подушка 3–6 мес. расходов лежит вне ИИС "
                '(подтверждается флагом "emergency_fund_ok": true в data/profile.json)'
            )
    if signals:
        t = Table(title="Сигналы", header_style="bold yellow", show_header=False, box=None)
        for s in signals:
            t.add_row(f"[bold yellow]⚠ {s}[/bold yellow]")
        console.print(Panel(t, border_style="yellow"))