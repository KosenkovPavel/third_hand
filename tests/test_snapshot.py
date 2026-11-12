"""Золотой тест снапшота: полная семантика сборки PortfolioSnapshot.

Это эталон для Фазы 1.2 (перенос сборки снапшота в адаптер провайдера):
рефакторинг корректен, если этот тест остаётся зелёным. Фиксирует:
- НКД входит в стоимость облигации, но не во «вложено»;
- P&L берётся из expected_yield брокера (деньги), а не пересчитывается;
- структура активов — из total_amount_* (сходится с итогом);
- портфельный expected_yield — проценты, а не деньги.
"""

import dataclasses
from decimal import Decimal
from types import SimpleNamespace

from src.portfolio import Position
from src.providers.tinkoff import build_snapshot


def _mv(units: int, nano: int = 0) -> SimpleNamespace:
    return SimpleNamespace(units=units, nano=nano)


# Сырые данные позиций (модель ответа T-Invest API).
POSITIONS = [
    dict(  # корпоративная облигация с НКД
        ticker="RU000A", instrument_uid="uid-bond-a", figi="FIGI-A",
        quantity=_mv(10), current_price=_mv(100), current_nkd=_mv(10),
        average_position_price=_mv(95), expected_yield=_mv(140), daily_yield=_mv(0),
        itype="bond", name="Корпоративная", asset_uid="asset-a",
    ),
    dict(  # ОФЗ с НКД
        ticker="SU26238", instrument_uid="uid-ofz", figi="FIGI-OFZ",
        quantity=_mv(20), current_price=_mv(90), current_nkd=_mv(5),
        average_position_price=_mv(88), expected_yield=_mv(130), daily_yield=_mv(0),
        itype="bond", name="ОФЗ 26238", asset_uid="asset-ofz",
    ),
    dict(  # акция
        ticker="SBER", instrument_uid="uid-sber", figi="FIGI-SBER",
        quantity=_mv(5), current_price=_mv(300), current_nkd=_mv(0),
        average_position_price=_mv(280), expected_yield=_mv(100), daily_yield=_mv(0),
        itype="share", name="Сбербанк", asset_uid="asset-sber",
    ),
    dict(  # валюта
        ticker="RUB000UTSTOM", instrument_uid="uid-rub", figi="RUB000UTSTOM",
        quantity=_mv(200), current_price=_mv(1), current_nkd=_mv(0),
        average_position_price=_mv(1), expected_yield=_mv(0), daily_yield=_mv(0),
        itype="currency", name="Рубль", asset_uid="asset-rub",
    ),
]


class FakeClient:
    def get_portfolio(self, account_id):
        raw_positions = [
            SimpleNamespace(
                ticker=p["ticker"],
                instrument_uid=p["instrument_uid"],
                figi=p["figi"],
                quantity=p["quantity"],
                current_price=p["current_price"],
                current_nkd=p["current_nkd"],
                average_position_price=p["average_position_price"],
                expected_yield=p["expected_yield"],
                daily_yield=p["daily_yield"],
            )
            for p in POSITIONS
        ]
        return SimpleNamespace(
            total_amount_portfolio=_mv(4700),
            total_amount_currencies=_mv(200),
            total_amount_shares=_mv(1500),
            total_amount_bonds=_mv(3000),
            total_amount_etf=_mv(0),
            total_amount_futures=_mv(0),
            total_amount_options=_mv(0),
            expected_yield=_mv(3, 530_000_000),  # 3.53% — относительная доходность
            positions=raw_positions,
        )

    def get_instrument(self, uid):
        for p in POSITIONS:
            if p["instrument_uid"] == uid:
                return SimpleNamespace(
                    instrument=SimpleNamespace(
                        name=p["name"],
                        instrument_type=p["itype"],
                        asset_uid=p["asset_uid"],
                        figi=p["figi"],
                        uid=uid,
                    )
                )
        raise KeyError(uid)

    def get_bond(self, uid):
        return SimpleNamespace(instrument=SimpleNamespace(risk_level=1))

    def get_dividends(self, figi, from_, to):
        return SimpleNamespace(dividends=[])


def test_snapshot_golden():
    snap = build_snapshot(FakeClient(), "acc")

    bond_a, ofz, sber, rub = snap.positions

    # облигация: amount включает НКД, invested — нет
    assert bond_a.amount == Decimal("1100.00")   # (100 + 10) * 10
    assert bond_a.invested == Decimal("950.00")  # 95 * 10
    assert bond_a.pnl_derived == Decimal("150.00")
    assert bond_a.pnl == Decimal("140.00")       # expected_yield брокера, не amount−invested

    # ОФЗ
    assert ofz.amount == Decimal("1900.00")      # (90 + 5) * 20
    assert ofz.invested == Decimal("1760.00")
    assert ofz.pnl == Decimal("130.00")

    # акция: без НКД
    assert sber.amount == Decimal("1500.00")     # 300 * 5
    assert sber.invested == Decimal("1400.00")
    assert sber.pnl == Decimal("100.00")

    # валюта: вложено == стоимость
    assert rub.amount == Decimal("200.00")
    assert rub.invested == Decimal("200.00")
    assert rub.pnl == Decimal("0.00")

    # итоги снапшота
    assert snap.total == Decimal("4700.00")
    assert snap.cash == Decimal("200.00")
    assert snap.total_pnl == Decimal("370.00")   # 140 + 130 + 100 + 0
    assert snap.total_yield_pct == Decimal("3.53")

    # структура из total_amount_* сходится с total
    alloc = {a.asset_class: a.amount for a in snap.allocation}
    assert alloc == {
        "Облигации": Decimal("3000.00"),
        "Акции": Decimal("1500.00"),
        "Валюта": Decimal("200.00"),
    }
    assert sum(alloc.values()) == snap.total


def test_canonical_model_has_no_nkd_field():
    fields = {f.name for f in dataclasses.fields(Position)}
    assert "current_nkd" not in fields
    assert {"amount", "invested", "pnl", "pnl_derived"} <= fields
