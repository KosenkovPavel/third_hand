"""Оценка рисков портфеля: концентрация по инструментам, секторам, β."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .portfolio import PortfolioSnapshot
from .providers.tinkoff import TinkoffClient

# пороги тревоги, %
INSTRUMENT_ALERT_PCT = 25
SECTOR_ALERT_PCT = 40
HY_BOND_ALERT_PCT = 10  # доля ВДО в портфеле для сигнала

# категории облигаций по кредитному риску
BOND_OFZ = "ОФЗ"
BOND_CORP = "Корпоративные"
BOND_HY = "ВДО"


def classify_bond(ticker: str, name: str, risk_level: str | None) -> str:
    """Категория облигации: ОФЗ / Корпоративные / ВДО (высокодоходные).

    ОФЗ определяются по тикеру (SU…) или имени; остальные — по risk_level
    из API ("low"/"moderate"/"high"); без данных — корпоративные.
    """
    t = (ticker or "").upper()
    if t.startswith("SU") or "ОФЗ" in (name or "").upper():
        return BOND_OFZ
    if (risk_level or "").lower() == "high":
        return BOND_HY
    return BOND_CORP


def bond_risk_breakdown(snapshot: PortfolioSnapshot) -> dict[str, Decimal]:
    """Суммы по категориям кредитного риска облигационных позиций."""
    out: dict[str, Decimal] = {}
    for p in snapshot.positions:
        if p.instrument_type != "bond" or p.amount <= 0:
            continue
        cat = classify_bond(p.ticker, p.name, p.bond_risk)
        out[cat] = out.get(cat, Decimal("0")) + p.amount
    return out


def rule_signals(
    snapshot: PortfolioSnapshot,
    rules,
    sector_by_uid: dict[str, str] | None = None,
) -> list[str]:
    """Сигналы по правилам дисциплины из профиля (rules): лимиты позиции,
    сектора и числа акций. Считается по снимку портфеля, без API-вызовов
    (секторная проверка — только если передана карта uid → сектор)."""
    total = snapshot.total
    if not total:
        return []
    signals: list[str] = []

    shares = [p for p in snapshot.positions if p.instrument_type == "share" and p.amount > 0]

    for p in sorted(shares, key=lambda x: -x.amount):
        pct = p.amount / total * 100
        if pct > rules.max_position_pct:
            signals.append(
                f"{p.ticker}: {pct:.1f}% портфеля — выше лимита {rules.max_position_pct:.0f}% "
                "(не докупаем, разбавляем притоком)"
            )

    if sector_by_uid:
        sector_totals: dict[str, Decimal] = {}
        for p in shares:
            sector = sector_by_uid.get(p.instrument_uid, "")
            if sector:
                sector_totals[sector] = sector_totals.get(sector, Decimal("0")) + p.amount
        for sector, amount in sorted(sector_totals.items(), key=lambda x: -x[1]):
            pct = amount / total * 100
            if pct > rules.max_sector_pct:
                label = SECTOR_NAMES_RU.get(sector, sector)
                signals.append(
                    f"Сектор «{label}»: {pct:.1f}% портфеля — выше лимита {rules.max_sector_pct:.0f}%"
                )

    if len(shares) > rules.max_stock_positions:
        signals.append(
            f"Акций {len(shares)} при лимите {rules.max_stock_positions} — "
            "не открывайте новые позиции"
        )
    return signals

SECTOR_NAMES_RU = {
    "financial": "Финансы",
    "energy": "Энергетика",
    "materials": "Сырьё и материалы",
    "it": "IT",
    "consumer": "Потребительский",
    "consumer_staples": "Потребительский",
    "consumer_discretionary": "Потреб. (дискрец.)",
    "healthcare": "Здравоохранение",
    "industrials": "Промышленность",
    "utilities": "Электроэнергетика",
    "telecommunications": "Связь",
    "real_estate": "Недвижимость",
    "transport": "Транспорт",
    "metals": "Металлы",
}


@dataclass(frozen=True)
class RiskItem:
    kind: str  # "Инструмент" / "Сектор" / "Класс активов"
    name: str
    amount: Decimal
    pct: Decimal
    alert: bool


@dataclass
class RiskReport:
    items: list[RiskItem] = field(default_factory=list)
    weighted_beta: Decimal | None = None
    alerts: list[str] = field(default_factory=list)
    count_instruments: int = 0
    count_sectors: int = 0


def load_sectors(client: TinkoffClient) -> dict[str, str]:
    """uid инструмента → сектор. Возвращает пустой dict при ошибке."""
    try:
        resp = client.get_shares()
        return {s.uid: s.sector for s in resp.instruments if s.sector}
    except Exception:
        return {}


def calculate_risks(
    snapshot: PortfolioSnapshot,
    sector_by_uid: dict[str, str] | None = None,
    beta_by_ticker: dict[str, float | None] | None = None,
) -> RiskReport:
    total = snapshot.total or Decimal("1")
    sector_by_uid = sector_by_uid or {}
    beta_by_ticker = beta_by_ticker or {}

    items: list[RiskItem] = []
    alerts: list[str] = []

    # --- концентрация по инструментам ---
    big_positions = 0
    for p in sorted(snapshot.positions, key=lambda x: -x.amount):
        pct = (p.amount / total * 100).quantize(Decimal("0.1"))
        alert = pct >= INSTRUMENT_ALERT_PCT
        if alert:
            alerts.append(f"{p.ticker}: {pct:.1f}% портфеля — высокая концентрация (> {INSTRUMENT_ALERT_PCT}%)")
            big_positions += 1
        items.append(RiskItem("Инструмент", f"{p.ticker} {p.name}", p.amount, pct, alert))
    if big_positions >= 3:
        alerts.append("Три и более позиции превышают 25% портфеля — распределение слишком кучное")

    # --- концентрация по секторам (акции) ---
    sector_totals: dict[str, Decimal] = {}
    for p in snapshot.positions:
        sector = sector_by_uid.get(p.instrument_uid, "")
        if sector:
            sector_totals[sector] = sector_totals.get(sector, Decimal("0")) + p.amount
    for name, amount in sorted(sector_totals.items(), key=lambda x: -x[1]):
        label = SECTOR_NAMES_RU.get(name, name)
        pct = (amount / total * 100).quantize(Decimal("0.1"))
        alert = pct >= SECTOR_ALERT_PCT
        if alert:
            alerts.append(f"Сектор «{label}»: {pct:.1f}% — перевес (> {SECTOR_ALERT_PCT}%)")
        items.append(RiskItem("Сектор", label, amount, pct, alert))

    # --- классы активов ---
    for a in snapshot.allocation:
        pct = (a.amount / total * 100).quantize(Decimal("0.1"))
        items.append(RiskItem("Класс активов", a.asset_class, a.amount, pct, False))

    # --- средний β ---
    weighted_beta: Decimal | None = None
    if beta_by_ticker:
        num = Decimal("0")
        den = Decimal("0")
        for p in snapshot.positions:
            b = beta_by_ticker.get(p.ticker)
            if b is None:
                continue
            num += p.amount * Decimal(str(b))
            den += p.amount
        if den > 0:
            weighted_beta = (num / den).quantize(Decimal("0.01"))

    return RiskReport(
        items=items,
        weighted_beta=weighted_beta,
        alerts=alerts,
        count_instruments=len(snapshot.positions),
        count_sectors=len(sector_totals),
    )