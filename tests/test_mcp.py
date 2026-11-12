"""Тесты MCP-фасада: регистрация инструментов и сериализация в JSON."""

import json
from decimal import Decimal

import src.frontends.mcp as mcp_mod


def test_six_tools_registered():
    names = {t.name for t in mcp_mod.mcp._tool_manager.list_tools()}
    assert names == {
        "portfolio_report",
        "rebalance_plan",
        "xirr",
        "iis_deductions",
        "forecast",
        "tax_report",
    }


def test_dec_serialization():
    assert mcp_mod._dec(None) is None
    assert mcp_mod._dec(Decimal("3.53")) == "3.53"
    assert mcp_mod._dec(Decimal("128430.55")) == "128430.55"


def _fake_report():
    from src.accounting import AccountingReport, ReturnBreakdown
    from src.portfolio import Allocation, PortfolioSnapshot, Position
    from src.profile import UserProfile
    from src.report_data import ReportData

    pos = Position(
        ticker="SBER", name="Сбербанк", instrument_type="share", type_name="Акции",
        quantity=Decimal("1"), current_price=Decimal("100"), avg_price=Decimal("90"),
        amount=Decimal("100"), invested=Decimal("90"), pnl=Decimal("10"),
        pnl_pct=Decimal("11.11"), expected_yield=Decimal("10"), daily_yield=Decimal("0"),
    )
    snap = PortfolioSnapshot(
        account_id="t", total=Decimal("100"), cash=Decimal("10"),
        total_pnl=Decimal("10"), total_yield_pct=Decimal("11.11"),
        positions=[pos], allocation=[Allocation("Акции", Decimal("100"))],
    )
    acct = AccountingReport(account_id="t")
    breakdown = ReturnBreakdown(
        net_contrib=Decimal("90"), total_return=Decimal("10"),
        realized_pnl=Decimal("5"), unrealized_pnl=Decimal("5"),
        income=Decimal("0"), costs=Decimal("0"),
    )
    return ReportData(
        snapshot=snap, acct=acct, profile=UserProfile(),
        acct_full=acct, ndfl_rate=Decimal("0.13"),
        return_breakdown=breakdown, xirr_pct=Decimal("10.00"),
    )


def test_tools_emit_json(monkeypatch):
    monkeypatch.setattr(mcp_mod, "_report", lambda flags: _fake_report())

    r = mcp_mod.portfolio_report()
    assert r["total"] == "100"
    assert r["pnl_unrealized"] == "10"
    assert r["realized_pnl"] == "5"
    assert r["positions"][0]["ticker"] == "SBER"
    json.dumps(r)  # сериализуется без ошибок

    x = mcp_mod.xirr()
    assert x["xirr_pct"] == "10.00"
    assert x["total_return"] == "10"
    json.dumps(x)
