"""Тест НКД в облигационной позиции портфеля (фейк-клиент, без сети).

Проверяет: НКД входит в стоимость, но не во «вложено»; P&L позиции берётся из
``expected_yield`` (деньги); портфельный ``expected_yield`` — относительная
доходность в %, а не деньги; структура активов — из ``total_amount_*`` API.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from src.providers.tinkoff import build_snapshot


def _mv(units: int, nano: int = 0) -> SimpleNamespace:
    return SimpleNamespace(units=units, nano=nano)


class FakeClient:
    def get_portfolio(self, account_id):
        return SimpleNamespace(
            total_amount_portfolio=_mv(1100),
            total_amount_currencies=_mv(0),
            total_amount_shares=_mv(0),
            total_amount_bonds=_mv(1100),
            total_amount_etf=_mv(0),
            total_amount_futures=_mv(0),
            total_amount_options=_mv(0),
            # Портфельный expected_yield — ОТНОСИТЕЛЬНАЯ доходность в % (не деньги!)
            expected_yield=_mv(3, 530_000_000),  # 3.53%
            positions=[
                SimpleNamespace(
                    ticker="SU26238",
                    instrument_uid="uid-1",
                    figi="FIGI1",
                    quantity=_mv(10),
                    current_price=_mv(100),       # чистая цена
                    current_nkd=_mv(10),          # НКД на облигацию
                    average_position_price=_mv(98),
                    expected_yield=_mv(110),      # 110 ₽ — P&L позиции в деньгах
                    daily_yield=_mv(0),
                )
            ],
        )

    def get_instrument(self, uid):
        return SimpleNamespace(
            instrument=SimpleNamespace(
                name="ОФЗ 26238", instrument_type="bond", asset_uid="", figi="FIGI1", uid=uid
            )
        )

    def get_bond(self, uid):
        return SimpleNamespace(instrument=SimpleNamespace(risk_level=1))


def test_nkd_included_in_amount_not_in_invested():
    snap = build_snapshot(FakeClient(), "acc")
    p = snap.positions[0]
    # amount = (чистая цена + НКД) * кол-во = (100 + 10) * 10
    assert p.amount == Decimal("1100.00")
    # НКД не попадает во «вложено»: invested = 98 * 10
    assert p.invested == Decimal("980.00")
    # наш расчёт P&L = amount − invested
    assert p.pnl_derived == Decimal("120.00")
    # P&L берём из expected_yield позиции (деньги)
    assert p.pnl == Decimal("110.00")
    # allocation сходится с точной суммой по классам API
    assert snap.allocation[0].asset_class == "Облигации"
    assert snap.allocation[0].amount == Decimal("1100.00")


def test_portfolio_expected_yield_is_percent_not_money():
    """Портфельный expected_yield = % (3.53), а не рубли. Деньги = сумма позиций."""
    snap = build_snapshot(FakeClient(), "acc")
    # P&L в деньгах — сумма позиционных expected_yield, НЕ портфельное 3.53
    assert snap.total_pnl == Decimal("110.00")
    # Относительная доходность — из портфельного поля, в %
    assert snap.total_yield_pct == Decimal("3.53")
    # Портфельное поле не должно попадать в деньги
    assert snap.total_pnl != snap.total_yield_pct


def test_fractional_quantity_not_quantized():
    """Дробное количество (долевые ПИФ/ETF) не округляется до 0.01."""
    from src.money import to_decimal, to_quantity

    frac = _mv(0, 123_400_000)  # 0.1234
    assert to_quantity(frac) == Decimal("0.1234")
    # to_decimal квантует до копеек — для количества это была бы ошибка
    assert to_decimal(frac) == Decimal("0.12")
