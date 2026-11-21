"""MCP-фасад: JSON-инструменты для AI-агентов (read-only).

Агент рассуждает и объясняет, а здесь — только вычисления поверх данных брокера.
Мы НЕ дублируем официальный T-Invest MCP (сырые данные: портфель, свечи, новости),
а добавляем то, чего там нет: XIRR, план ребалансировки, разбивку реализованного/
нереализованного P&L, ИИС-вычеты, прогноз, справку 3-НДФЛ.

Запуск: `python -m src.frontends.mcp` (транспорт stdio, для MCP-клиентов).
"""

from __future__ import annotations

import sys
from decimal import Decimal

from mcp.server.fastmcp import FastMCP
from rich.console import Console

from src.iis import tax_report_text
from src.report_builder import build_multi_report

from . import connect
from .cli import build_parser

mcp = FastMCP("third-hand")

# Прогресс и ошибки — в stderr, чтобы не ломать MCP-транспорт (stdout = JSON-RPC).
_console = Console(file=sys.stderr)
_parser = build_parser()
_setup_cache = None


def _dec(v) -> str | None:
    """Decimal → строка: точные деньги/проценты, без float-округления."""
    if v is None:
        return None
    return str(v)


def _jsonable(v):
    """Рекурсивно приводит Decimal к строке для JSON-сериализации MCP."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _setup():
    """Ленивое подключение (токен → счёт → профиль), кэшируется."""
    global _setup_cache
    if _setup_cache is None:
        _setup_cache = connect(_console)
    return _setup_cache


def _report(flags: list[str]):
    setup = _setup()
    if setup is None:
        raise RuntimeError("Не удалось подключиться к брокеру (проверьте токен и счёт в .env)")
    client, accounts, profile = setup
    args = _parser.parse_args(flags)
    return build_multi_report(client, accounts, args, profile, _console)


@mcp.tool()
def portfolio_report() -> dict:
    """Сводка портфеля: стоимость, P&L, разбивка реализованного/нереализованного
    дохода, дивиденды/купоны, комиссии/налоги и список позиций.

    КОГДА ЗВАТЬ: любой вопрос о текущем состоянии («как дела», «сколько у меня»,
    «почему по облигации минус» — смотри pnl и pnl_pct позиций). Поле
    crisis_signal — состояние «кризисного откупа» по индексу (правило из
    бэктеста): crisis — направлять новые взносы в акции до 65–75%; recovery —
    возврат к целевой структуре; stress/normal — правило не активно. Все числа
    вычислены детерминированным кодом — НЕ пересчитывай их сам, используй как есть.
    """
    report = _report([])
    snap = report.snapshot
    b = report.return_breakdown
    crisis = report.crisis_signal
    return {
        "total": _dec(snap.total),
        "cash": _dec(snap.cash),
        "invested": _dec(snap.invested),
        "pnl_unrealized": _dec(snap.total_pnl),
        "yield_pct": _dec(snap.total_yield_pct),
        "crisis_signal": (
            {"state": crisis["state"], "dd_pct": _dec(crisis["dd_pct"]),
             "comment": {
                 "crisis": "Кризисный откуп: взносы направляйте в акции до 65–75% "
                           "(правило из бэктеста 1997–2026)",
                 "recovery": "Выход из режима откупа: возвращайте взносы к целевой структуре",
                 "stress": "Режим откупа не активен (включается при просадке глубже −30%)",
             }.get(crisis["state"], "Вне режима откупа")}
            if crisis else None
        ),
        "self_validation": _jsonable(report.self_validation),
        "total_return": _dec(b.total_return) if b else None,
        "total_return_pct": _dec(b.total_return_pct) if b else None,
        "realized_pnl": _dec(b.realized_pnl) if b else None,
        "unrealized_pnl": _dec(b.unrealized_pnl) if b else None,
        "income": _dec(b.income) if b else None,
        "costs": _dec(b.costs) if b else None,
        "positions": [
            {
                "ticker": p.ticker,
                "name": p.name,
                "type": p.instrument_type,
                "quantity": _dec(p.quantity),
                "price": _dec(p.current_price),
                "amount": _dec(p.amount),
                "invested": _dec(p.invested),
                "pnl": _dec(p.pnl),
                "pnl_pct": _dec(p.pnl_pct),
            }
            for p in snap.positions
        ],
    }


@mcp.tool()
def rebalance_plan(target_json: str | None = None, invest: int = 0) -> dict:
    """План ребалансировки под целевую структуру. target_json — необязательный
    JSON вида {"Акции": 60, "Облигации": 40}; по умолчанию цель из профиля
    пользователя. invest — дополнительное пополнение в рублях сверх остатка
    на счёте.

    КОГДА ЗВАТЬ: «что купить на пополнение», «как привести портфель к цели».
    Отдаёт конкретный список покупок по лотам с комиссией. План ВИРТУАЛЬНЫЙ:
    заявки никогда не отправляются — исполняет пользователь. Продажи не
    предлагаются — только распределение новых денег. Поле crisis_signal
    интерпретируй как в portfolio_report.
    """
    flags = ["--rebalance"] if target_json is None else ["--rebalance", target_json]
    if invest:
        flags += ["--invest", str(invest)]
    report = _report(flags)
    plan = report.rebalance_plan
    if plan is None:
        return {"rebalance": None}
    crisis = report.crisis_signal
    result = {
        "crisis_signal": (
            {"state": crisis["state"], "dd_pct": _dec(crisis["dd_pct"])}
            if crisis else None
        ),
        "invest_amount": _dec(plan.invest_amount),
        "recommendations": [
            {
                "asset_class": r.asset_class,
                "current_pct": _dec(r.current_pct),
                "target_pct": _dec(r.target_pct),
                "invest_amount": _dec(r.invest_amount),
            }
            for r in plan.recommendations
        ],
    }
    if report.contribution_plan:
        result["purchases"] = [
            {
                "ticker": i.ticker,
                "name": i.name,
                "asset_class": i.asset_class,
                "lots": i.lots,
                "quantity": _dec(i.quantity),
                "amount": _dec(i.amount),
                "commission": _dec(i.commission),
            }
            for i in report.contribution_plan.items
        ]
        result["unallocated"] = _dec(report.contribution_plan.unallocated)
    return result


@mcp.tool()
def xirr() -> dict:
    """Годовая доходность портфеля (money-weighted, XIRR) и простой доход к взносам.

    КОГДА ЗВАТЬ: «какая у меня доходность годовых». Если xirr_pct — None,
    счёту меньше 3 лет: на коротком горизонте XIRR волатилен и обманчив,
    скажи пользователю об этом честно вместо выдумывания числа.
    """
    report = _report([])
    b = report.return_breakdown
    return {
        "xirr_pct": _dec(report.xirr_pct),
        "total_return": _dec(b.total_return) if b else None,
        "total_return_pct": _dec(b.total_return_pct) if b else None,
    }


@mcp.tool()
def iis_deductions() -> dict:
    """ИИС: взносы по годам, налоговый вычет, прогресс до лимита и незаявленные вычеты.

    КОГДА ЗВАТЬ: любые вопросы про вычеты ИИС («сколько верну», «успею ли до
    лимита», «что сгорит»). progress — текущий год: сколько внесено, лимит
    (400 тыс.), сколько осталось. claim_statuses — по каждому году: можно ли
    заявить вычет сейчас, сгорел ли срок (3 года), когда заявлять. Это
    вычисления по правилам НК РФ, а не оценки LLM — используй как есть.
    """
    report = _report([])
    result = {
        "years": [
            {
                "year": y.year,
                "contributed": _dec(y.contributed),
                "capped": _dec(y.capped),
                "return": _dec(y.potential_return),
                "over_limit": y.over_limit,
            }
            for y in report.iis_years
        ],
    }
    if report.iis_progress:
        p = report.iis_progress
        result["progress"] = {
            "year": p.year,
            "contributed": _dec(p.contributed),
            "limit": _dec(p.limit),
            "remaining": _dec(p.remaining),
        }
    if report.claim_statuses:
        result["claim_statuses"] = [
            {"year": c.year, "return": _dec(c.potential_return), "status": c.status}
            for c in report.claim_statuses
        ]
    return result


@mcp.tool()
def forecast(return_pct: float = 10, monthly: int | None = None) -> dict:
    """Прогноз роста портфеля (сложный процент) и три сценария (пессимистичный/
    базовый/оптимистичный). return_pct — ожидаемая доходность %/год.

    КОГДА ЗВАТЬ: «сколько будет через N лет», «хватит ли на цель». Это
    иллюстрация сложного процента при заданной доходности, а НЕ обещание
    доходности — всегда проговаривай это пользователю и опирайся на все три
    сценария, а не только базовый.
    """
    flags = ["--forecast", "--return", str(return_pct)]
    if monthly is not None:
        flags += ["--monthly", str(monthly)]
    report = _report(flags)
    fc = report.forecast_data
    if fc is None:
        return {"forecast": None}
    result = {
        "initial": _dec(fc.initial),
        "monthly": _dec(fc.monthly),
        "annual_return_pct": _dec(fc.annual_return_pct),
        "points": [
            {
                "years": p.years,
                "balance": _dec(p.balance),
                "contributed": _dec(p.contributed),
                "profit": _dec(p.profit),
            }
            for p in fc.points
        ],
    }
    if report.scenarios:
        result["scenarios"] = [
            {
                "label": s.label,
                "annual_return_pct": _dec(s.annual_return_pct),
                "final_balance": _dec(s.forecast.final.balance),
            }
            for s in report.scenarios
        ]
    return result


@mcp.tool()
def tax_report(year: int) -> dict:
    """Справка для декларации 3-НДФЛ по взносам на ИИС за указанный год.

    КОГДА ЗВАТЬ: «подготовь справку для налоговой», «сколько внесено в 20XX
    для декларации». Текст готов к подаче в ФНС — отдавай пользователю как есть.
    """
    report = _report([])
    text = tax_report_text(report.acct_full, year, rate=report.ndfl_rate)
    if text is None:
        return {"year": year, "text": None, "message": "за этот год взносов не найдено"}
    return {"year": year, "text": text}


if __name__ == "__main__":
    mcp.run()
