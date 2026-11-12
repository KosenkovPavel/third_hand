"""Сборка отчёта: оркестрация всех вычислений в единый ReportData."""

from __future__ import annotations

import ast
import json
from datetime import timedelta, datetime, timezone
from decimal import Decimal

from rich.console import Console

from .accounting import Accountant
from .analytics import analyze, apply_portfolio_rules
from .bonds import BondFilters, bond_screen
from .calendar import build_calendar
from .contribution_plan import build_contribution_plan, load_purchase_candidates
from .forecast import balance_at, forecast, forecast_scenarios
from .goals import build_goal_report, evaluate_goal
from .history import load_history, time_weighted_return
from .iis import (
    DEFAULT_NDFL_RATE,
    IIS3_INCOME_EXEMPT_LIMIT,
    annual_deduction,
    deduction_claim_statuses,
    deduction_progress,
    iis_deduction,
    resolve_iis,
    tax_calendar_notes,
)
from .macro import deposit_return_pct
from .macro import key_rate as cbr_key_rate
from .market import build_market_opinion
from .news import build_news_background
from .picks import PickFilters, pick_stocks
from .rebalance import calculate_rebalance
from .recommendation_history import evaluate_recommendations, load_recommendations
from .report_data import ReportData
from .risk import calculate_risks, load_sectors
from .screener import ScreenFilters, screen
from .simulation import simulate_index_contributions
from .providers.tinkoff import ACCOUNT_TYPE_IIS, TinkoffClient
from .xirr import flows_from_operations, xirr


class ReportError(Exception):
    """Ошибка сборки отчёта — сообщение уже готово для показа пользователю."""


def fmt_money(value: Decimal) -> str:
    return f"{value:,.2f} ₽".replace(",", " ")


def build_report(
    client: TinkoffClient,
    account,
    args,
    profile,
    console: Console,
) -> ReportData:
    """Собирает все вычисления отчёта в один ``ReportData``.

    Побочные эффекты (``--save``, ``--tax``, ``--telegram``) сюда не входят —
    их выполняет вызывающий код поверх результата.
    """
    data_warnings: list[str] = []
    account_id = account.id
    is_iis = account.account_type == ACCOUNT_TYPE_IIS
    reinvest_deduction = args.reinvest_deduction or is_iis

    console.print("Собираю портфель и операции...")

    snapshot = client.snapshot(account_id)

    to = datetime.now(timezone.utc)
    from_ = to - timedelta(days=args.history_days)
    accountant = Accountant(client, account_id)
    acct = accountant.report(from_=from_, to=to)

    # Для XIRR и «внесено/рост» — операции за всю жизнь счёта, если дата открытия известна
    acct_full = acct
    full_window = True
    opened = account.opened_date
    if opened and opened < from_:
        try:
            acct_full = accountant.report(from_=opened, to=to)
        except Exception:  # noqa: BLE001
            full_window = False
    elif not opened:
        full_window = False
    if not full_window:
        console.print(
            f"[dim]Полная история операций недоступна — XIRR, «внесено/рост» и взносы ИИС "
            f"посчитаны за последние {args.history_days} дн.[/dim]"
        )

    iis_info = resolve_iis(profile.iis, account.opened_date, is_iis)
    ndfl_rate = iis_info.ndfl_rate if iis_info else (profile.iis.ndfl_rate or DEFAULT_NDFL_RATE)
    stress_mode = args.stress or profile.stress
    cbr_rate = cbr_key_rate()

    if args.rebalance == "__profile__":
        args.rebalance = str({k: float(v) for k, v in profile.target.items()})

    monthly = (
        args.monthly
        if args.monthly is not None
        else (acct.avg_monthly_inflow() or profile.monthly_total())
    )

    console.print("Собираю фундаментальные данные и рекомендации...")
    recommendations = analyze(client, snapshot, issues=data_warnings)

    payment_calendar = None
    if args.calendar:
        console.print("Собираю календарь выплат...")
        payment_calendar = build_calendar(client, snapshot, ndfl_rate=ndfl_rate)

    forecast_data = None
    scenarios = None
    deposit_forecast = None
    deduction_year = annual_deduction(monthly, rate=ndfl_rate) if reinvest_deduction else Decimal("0")
    if args.forecast:
        try:
            forecast_data = forecast(
                snapshot,
                monthly=monthly,
                annual_return_pct=args.annual_return,
                annual_deduction=deduction_year,
                inflation_pct=profile.inflation_pct,
            )
            scenarios = forecast_scenarios(
                snapshot,
                monthly=monthly,
                annual_return_pct=args.annual_return,
                annual_deduction=deduction_year,
                inflation_pct=profile.inflation_pct,
            )
            if cbr_rate is not None:
                deposit_forecast = forecast(
                    snapshot,
                    monthly=monthly,
                    annual_return_pct=cbr_rate,
                )
        except ValueError as exc:
            raise ReportError(f"Ошибка в --forecast: {exc}") from exc

    risk_report = None
    sector_by_uid = load_sectors(client)  # uid → сектор: для --risk и сигналов по лимитам
    if not sector_by_uid:
        console.print(
            "[dim]Карта секторов недоступна — секторные лимиты и концентрация по секторам не проверяются.[/dim]"
        )
    recommendations = apply_portfolio_rules(
        recommendations, snapshot, profile.rules, sector_by_uid
    )
    if args.risk:
        console.print("Оцениваю риски и концентрацию...")
        risk_report = calculate_risks(
            snapshot,
            sector_by_uid=sector_by_uid,
            beta_by_ticker={r.ticker: r.beta for r in recommendations},
        )

    market_opinion = None
    if args.market:
        console.print("Собираю мнение рынка (аналитики, инсайдеры, RSI)...")
        market_opinion = build_market_opinion(client, snapshot, issues=data_warnings)

    rebalance_plan = None
    contribution_plan = None
    if args.rebalance:
        try:
            target_raw = ast.literal_eval(args.rebalance)
            target = {str(k): Decimal(str(v)) for k, v in target_raw.items()}
            total_pct = sum(target.values())
            if abs(total_pct - 100) > 0.01:
                raise ReportError("Сумма целевых долей должна быть 100%.")
            buying_power = snapshot.cash + args.invest
            console.print(
                f"Бюджет на покупку: [bold]{fmt_money(snapshot.cash)}[/] (остаток на счёте, "
                f"включая дивиденды/купоны) + {fmt_money(args.invest)} (пополнение) "
                f"= [bold]{fmt_money(buying_power)}[/]"
            )
            rebalance_plan = calculate_rebalance(
                snapshot,
                target,
                invest_amount=buying_power,
                cash=snapshot.cash,
                contrib=args.invest,
            )
            candidates = load_purchase_candidates(
                client, snapshot, recommendations, sector_by_uid, issues=data_warnings
            )
            contribution_plan = build_contribution_plan(
                snapshot,
                candidates,
                {r.asset_class: r.invest_amount for r in rebalance_plan.recommendations},
                budget=buying_power,
                contribution=args.invest,
                rules=profile.rules,
                commission_pct=profile.commission_pct,
                sector_by_uid=sector_by_uid,
            )
        except ReportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"Ошибка в --rebalance: {exc}") from exc

    history_points = None
    history_flows = [
        (op.date.date(), op.payment)
        for op in acct_full.ops
        if op.kind in ("in", "out")
    ]
    twr_pct = None
    benchmark_pct = None
    all_time_benchmark_pct = None
    deposit_pct = None
    index_simulation = None
    recommendation_entries = []
    recommendation_outcomes = None
    if args.history:
        history_points = load_history()
        if not history_points:
            console.print("[dim]История пуста. Используй --save, чтобы накапливать снимки.[/dim]")
        else:
            twr_pct = time_weighted_return(history_points, history_flows)
            benchmark_pct = client.imoex_return(
                history_points[0].date, history_points[-1].date, issues=data_warnings
            )
            if opened:
                all_time_benchmark_pct = client.imoex_return(
                    opened.date().isoformat(), history_points[-1].date,
                    issues=data_warnings,
                )
            days = (
                datetime.fromisoformat(history_points[-1].date)
                - datetime.fromisoformat(history_points[0].date)
            ).days
            if days > 0 and cbr_rate is not None:
                deposit_pct = deposit_return_pct(cbr_rate, days)
        index_simulation = simulate_index_contributions(
            client, acct_full.ops, snapshot.total, end_date=to.date(), issues=data_warnings
        )
        recommendation_entries = load_recommendations()
        recommendation_outcomes = evaluate_recommendations(
            client, recommendation_entries, issues=data_warnings
        )

    screen_rows = None
    if args.screen:
        try:
            raw = json.loads(args.screen)
            valid = {"pe_max", "div_min", "roe_min", "mcap_min_bln", "debt_max"}
            unknown = sorted(set(raw) - valid)
            if unknown:
                console.print(
                    f"[yellow]Неизвестные параметры --screen проигнорированы: {', '.join(unknown)}[/]"
                )
            filters = ScreenFilters(**{k: float(v) for k, v in raw.items() if k in valid})
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"Ошибка в --screen: {exc}") from exc
        tickers = [t.strip() for t in args.universe.split(",") if t.strip()] or None
        console.print(f"Скрининг {', '.join(tickers) if tickers else 'RUB-акций MOEX'}...")
        screen_rows = screen(client, filters, tickers=tickers)
        if not screen_rows:
            console.print(
                "[yellow]Screener: никто не прошёл фильтры — попробуйте смягчить условия.[/]"
            )

    bond_rows = None
    if args.bonds:
        try:
            raw = json.loads(args.bonds)
            valid = {"ytm_min", "ytm_max", "coupon_min", "min_years_left", "max_items"}
            unknown = sorted(set(raw) - valid)
            if unknown:
                console.print(
                    f"[yellow]Неизвестные параметры --bonds проигнорированы: {', '.join(unknown)}[/]"
                )
            kwargs = {
                k: (int(v) if k == "max_items" else float(v))
                for k, v in raw.items()
                if k in valid
            }
            bond_filters = BondFilters(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"Ошибка в --bonds: {exc}") from exc
        console.print("Подбираю облигации по доходности...")
        bond_rows = bond_screen(client, bond_filters)

    picks = None
    if args.picks:
        try:
            raw = json.loads(args.picks)
            valid = {"blend", "top_n", "max_items", "score_min", "pe_max", "div_min"}
            unknown = sorted(set(raw) - valid)
            if unknown:
                console.print(
                    f"[yellow]Неизвестные параметры --picks проигнорированы: {', '.join(unknown)}[/]"
                )
            kwargs: dict = {}
            for k, v in raw.items():
                if k == "blend":
                    kwargs[k] = (
                        v
                        if isinstance(v, bool)
                        else str(v).strip().lower() not in ("false", "0", "no", "нет")
                    )
                elif k in ("top_n", "max_items"):
                    kwargs[k] = int(v)
                elif k in ("score_min", "pe_max", "div_min"):
                    kwargs[k] = float(v)
            pick_filters = PickFilters(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"Ошибка в --picks: {exc}") from exc
        console.print("Подбираю акции к покупке (фундамент + консенсус + RSI)...")
        portfolio_tickers = {p.ticker for p in snapshot.positions if p.instrument_type == "share"}
        budget = (snapshot.cash + args.invest) if args.rebalance else (monthly or Decimal("0")) / 2
        picks = pick_stocks(
            client,
            pick_filters,
            stress=stress_mode,
            portfolio_tickers=portfolio_tickers,
            portfolio_total=snapshot.total,
            budget=budget,
            min_position_pct=profile.rules.min_position_pct,
        )

    goal_report = None
    if args.goal:
        try:
            raw = json.loads(args.goal)
            kwargs: dict = {}
            goal_kwargs = {"income": "income", "withdraw": "withdraw_pct", "return": "return_pct", "monthly": "monthly"}
            for key, param in goal_kwargs.items():
                if key in raw:
                    kwargs[param] = Decimal(str(raw[key]))
            if "monthly" not in kwargs and monthly:
                kwargs["monthly"] = monthly
            if "inflation" in raw:
                inv = raw["inflation"]
                kwargs["inflations"] = tuple(
                    Decimal(str(float(x))) for x in (inv if isinstance(inv, list) else [inv])
                )
            goal_report = build_goal_report(
                Decimal(str(snapshot.total)),
                reinvest_deduction=reinvest_deduction,
                ndfl_rate=ndfl_rate,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            raise ReportError(f"Ошибка в --goal: {exc}") from exc

    # ИИС/3-НДФЛ — по полному окну операций (от даты открытия счёта, если доступно)
    iis_years = iis_deduction(acct_full, rate=ndfl_rate) if is_iis else []
    iis_progress = deduction_progress(acct_full, rate=ndfl_rate) if is_iis else None
    claim_statuses = (
        deduction_claim_statuses(iis_years, profile.iis.claimed_years) if is_iis else None
    )

    # XIRR: взносы и выводы из операций + текущая стоимость портфеля
    flows = flows_from_operations(acct_full.ops)
    flows.append((datetime.now().date(), snapshot.total))
    xirr_pct = xirr(flows)

    # Общий доход и разбивка на реализованный/нереализованный P&L
    return_breakdown = acct_full.return_breakdown(snapshot.total, snapshot.total_pnl)

    usdrub = client.usd_rub()
    rgbi = client.rgbi()

    goal_statuses = []
    if profile.goals:
        try:
            goal_statuses = [
                evaluate_goal(g, snapshot.total, monthly, args.annual_return) for g in profile.goals
            ]
        except ValueError as exc:
            raise ReportError(f"Ошибка в целях профиля (--return {args.annual_return}): {exc}") from exc

    tax_notes = tax_calendar_notes() if iis_info else None

    iis_warnings: list[str] = []
    if iis_info and iis_info.iis_type == "iis3" and iis_info.term and not iis_info.term.expired:
        days_to_mature = (iis_info.term.mature_date - datetime.now().date()).days
        if days_to_mature > 0:
            years_to_mature = Decimal(days_to_mature) / Decimal("365.25")
            try:
                projected = balance_at(
                    snapshot.total, monthly, args.annual_return, years_to_mature, deduction_year
                )
            except ValueError as exc:
                raise ReportError(
                    f"Ошибка в прогнозе ИИС (--return {args.annual_return}): {exc}"
                ) from exc
            # доход = прогноз − (текущая стоимость + будущие взносы + реинвест. вычеты)
            contributed = (
                snapshot.total
                + monthly * 12 * years_to_mature
                + deduction_year * int(years_to_mature)
            )
            income = projected - contributed
            if income > IIS3_INCOME_EXEMPT_LIMIT:
                iis_warnings.append(
                    f"Прогнозируемый доход к дате закрытия ИИС-3 ({iis_info.term.mature_date:%d.%m.%Y}) "
                    f"≈ {income:,.0f} ₽ — выше лимита освобождения 30 млн ₽, "
                    "сверхлимит облагается НДФЛ".replace(",", " ")
                )

    news_events = None
    if args.news:
        console.print("Собираю новостной фон (цели аналитиков, инсайдеры, RSI)...")
        news_calendar = payment_calendar or build_calendar(client, snapshot, ndfl_rate=ndfl_rate)
        extra = [p.ticker for p in picks] if picks else []
        news_events, rss_ok = build_news_background(
            client, snapshot, extra_tickers=extra, calendar=news_calendar
        )
        if not rss_ok and news_events:
            console.print("[dim]RSS-лента недоступна — внешние новости пропущены.[/dim]")

    return ReportData(
        snapshot=snapshot,
        acct=acct,
        profile=profile,
        acct_full=acct_full,
        ndfl_rate=ndfl_rate,
        recommendations=recommendations,
        recommendation_entries_count=len(recommendation_entries) if args.history else 0,
        recommendation_outcomes=recommendation_outcomes if args.history else None,
        rebalance_plan=rebalance_plan,
        contribution_plan=contribution_plan,
        calendar=payment_calendar,
        forecast_data=forecast_data,
        scenarios=scenarios,
        deposit_forecast=deposit_forecast,
        risks=risk_report,
        market=market_opinion,
        iis_years=iis_years,
        iis_progress=iis_progress,
        iis_info=iis_info,
        iis_warnings=iis_warnings,
        tax_notes=tax_notes,
        claim_statuses=claim_statuses,
        xirr_pct=xirr_pct,
        return_breakdown=return_breakdown,
        twr_pct=twr_pct,
        benchmark_pct=benchmark_pct,
        all_time_benchmark_pct=all_time_benchmark_pct,
        index_simulation=index_simulation,
        account_opened=opened,
        screen_rows=screen_rows,
        history_points=history_points,
        history_flows=history_flows,
        bond_rows=bond_rows,
        picks=picks,
        news_events=news_events,
        goal_report=goal_report,
        goal_statuses=goal_statuses,
        sector_by_uid=sector_by_uid,
        stress=stress_mode,
        usdrub=usdrub,
        key_rate=cbr_rate,
        rgbi=rgbi,
        deposit_pct=deposit_pct,
        data_warnings=data_warnings,
    )
