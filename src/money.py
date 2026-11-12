"""Вспомогательные функции для работы с денежными типами T-Invest."""

from __future__ import annotations

from decimal import Decimal


def to_decimal(value) -> Decimal:
    """Преобразует MoneyValue/Quotation (units+nano) в Decimal (целые = единицы валюты).

    nano — всегда дробная часть с тем же знаком, что и units (может быть отрицательным).
    """
    nano = Decimal(getattr(value, "nano", 0))
    units = Decimal(getattr(value, "units", 0))
    return (units + nano / Decimal(1_000_000_000)).quantize(Decimal("0.01"))


def to_ratio(value) -> Decimal:
    """Quotation (units+nano) без квантования — для долей и коэффициентов."""
    nano = Decimal(getattr(value, "nano", 0))
    units = Decimal(getattr(value, "units", 0))
    return units + nano / Decimal(1_000_000_000)


def to_quantity(value) -> Decimal:
    """Количество бумаг без квантования — для дробных долей (ПИФ/ETF, дроби акций).

    В отличие от ``to_decimal`` не округляет до копеек: количество может быть
    дробным с точностью до 1/1e9, и его нельзя квантовать до 0.01, иначе
    стоимость и P&L позиции посчитаются неверно.
    """
    nano = Decimal(getattr(value, "nano", 0))
    units = Decimal(getattr(value, "units", 0))
    return units + nano / Decimal(1_000_000_000)


# Единое число дней в году для годовых ставок (ACT/365.25 — совпадает с XIRR).
YEAR_DAYS = Decimal("365.25")


def fmt_money(value, currency: str = "₽") -> str:
    d = to_decimal(value)
    return f"{d:,} {currency}".replace(",", " ")


def fmt_quantity(value) -> Decimal:
    return to_quantity(value)