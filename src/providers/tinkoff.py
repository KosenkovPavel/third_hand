"""Тонкая обёртка над T-Invest Python SDK (t-tech-investments)."""

import logging
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from t_tech.invest import Client

from ..money import to_decimal, to_quantity, to_ratio
from ..portfolio import (
    INSTRUMENT_TYPE_NAMES,
    Allocation,
    PortfolioSnapshot,
    Position,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("t_tech").setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    account_type: int
    status: int
    opened_date: datetime | None = None


ACCOUNT_TYPE_IIS = 2  # users.AccountType.ACCOUNT_TYPE_TINKOFF_IIS


class TinkoffClient:
    """Синхронный клиент с нужными для нас методами."""

    def __init__(self, token: str) -> None:
        self._token = token

    def snapshot(self, account_id: str) -> PortfolioSnapshot:
        """Канонический снимок портфеля (метод протокола ``AccountClient``)."""
        return build_snapshot(self, account_id)

    def accounts(self) -> list[Account]:
        with Client(self._token) as client:
            resp = client.users.get_accounts()
            return [
                Account(
                    id=acc.id,
                    name=acc.name,
                    account_type=acc.type,
                    status=acc.status,
                    opened_date=acc.opened_date or None,
                )
                for acc in resp.accounts
            ]

    def get_portfolio(self, account_id: str):
        with Client(self._token) as client:
            return client.operations.get_portfolio(account_id=account_id)

    def get_operations(
        self,
        account_id: str,
        from_: datetime,
        to: datetime,
    ):
        with Client(self._token) as client:
            return client.operations.get_operations(
                account_id=account_id,
                from_=from_,
                to=to,
            )

    def get_instrument(self, instrument_id: str, id_type=None):
        """Получить инструмент по FIGI или UID. Если id_type не задан — определяется автоматически."""
        from t_tech.invest import schemas

        if id_type is None:
            id_type = (
                schemas.InstrumentIdType.INSTRUMENT_ID_TYPE_UID
                if "-" in instrument_id
                else schemas.InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI
            )
        with Client(self._token) as client:
            return client.instruments.get_instrument_by(
                id_type=id_type,
                id=instrument_id,
            )

    def get_candles(self, instrument_id: str, from_: datetime, to: datetime, interval):
        with Client(self._token) as client:
            return client.market_data.get_candles(
                instrument_id=instrument_id,
                from_=from_,
                to=to,
                interval=interval,
            )

    def find_instrument(self, query: str, ticker: str):
        """InstrumentShort по точному совпадению тикера в поиске (индексы, валюты)."""
        with Client(self._token) as client:
            resp = client.instruments.find_instrument(query=query)
            for i in resp.instruments:
                if i.ticker == ticker:
                    return i
        return None

    def find_instrument_uid(self, query: str, ticker: str) -> str | None:
        """UID инструмента по точному совпадению тикера в поиске (индексы и пр.)."""
        instr = self.find_instrument(query, ticker)
        return instr.uid if instr else None

    def daily_interval(self):
        """Дневной интервал свечей — прячет схемы SDK от доменного слоя."""
        from t_tech.invest import schemas

        return schemas.CandleInterval.CANDLE_INTERVAL_DAY

    def get_dividends(self, figi: str, from_: datetime, to: datetime):
        with Client(self._token) as client:
            return client.instruments.get_dividends(
                figi=figi, from_=from_, to=to
            )

    def get_shares(self):
        """Полный список акций (uid → Share) для маппинга секторов."""
        with Client(self._token) as client:
            return client.instruments.shares()

    def get_forecast_by(self, instrument_id: str):
        from t_tech.invest import schemas

        req = schemas.GetForecastRequest(instrument_id=instrument_id)
        with Client(self._token) as client:
            return client.instruments.get_forecast_by(request=req)

    def get_insider_deals(self, instrument_id: str, limit: int = 10):
        from t_tech.invest import schemas

        req = schemas.GetInsiderDealsRequest(instrument_id=instrument_id, limit=limit)
        with Client(self._token) as client:
            return client.instruments.get_insider_deals(request=req)

    def get_rsi(self, instrument_uid: str, days: int = 30, length: int = 14) -> float | None:
        from datetime import timedelta, timezone

        from t_tech.invest import schemas

        now = datetime.now(timezone.utc)
        req = schemas.GetTechAnalysisRequest(
            indicator_type=schemas.IndicatorType.INDICATOR_TYPE_RSI,
            instrument_uid=instrument_uid,
            from_=now - timedelta(days=days),
            to=now,
            interval=schemas.IndicatorInterval.INDICATOR_INTERVAL_ONE_DAY,
            type_of_price=schemas.TypeOfPrice.TYPE_OF_PRICE_CLOSE,
            length=length,
        )
        with Client(self._token) as client:
            resp = client.market_data.get_tech_analysis(request=req)
        items = resp.technical_indicators
        if not items:
            return None
        last = items[-1].signal
        if last is None:
            return None
        return round(to_decimal(last), 1)

    def get_bond_coupons(self, instrument_id: str, from_: datetime, to: datetime):
        with Client(self._token) as client:
            return client.instruments.get_bond_coupons(
                instrument_id=instrument_id, from_=from_, to=to
            )

    def get_bond(self, instrument_id: str):
        """Полные данные облигации (в т.ч. risk_level) по UID/FIGI."""
        from t_tech.invest import schemas

        id_type = (
            schemas.InstrumentIdType.INSTRUMENT_ID_TYPE_UID
            if "-" in instrument_id
            else schemas.InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI
        )
        with Client(self._token) as client:
            return client.instruments.bond_by(id_type=id_type, id=instrument_id)

    def get_bonds(self):
        with Client(self._token) as client:
            resp = client.instruments.bonds()
            return list(resp.instruments)

    def get_last_prices(self, figis: list[str]):
        with Client(self._token) as client:
            return client.market_data.get_last_prices(figi=figis)

    def get_asset_fundamentals(self, asset_uids: list[str]):
        from t_tech.invest import schemas

        req = schemas.GetAssetFundamentalsRequest()
        req.assets = []
        for uid in asset_uids:
            req.assets.append(uid)
        with Client(self._token) as client:
            return client.instruments.get_asset_fundamentals(request=req)

    def imoex_return(
        self,
        since: str,
        until: str,
        issues: list[str] | None = None,
    ) -> Decimal | None:
        """Доходность IMOEX между датами первой и последней точек истории."""
        try:
            from t_tech.invest import schemas

            uid = self.find_instrument_uid("IMOEX", "IMOEX")
            if not uid:
                if issues is not None:
                    issues.append("IMOEX: инструмент не найден")
                return None
            start = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(until).replace(tzinfo=timezone.utc)
            day = schemas.CandleInterval.CANDLE_INTERVAL_DAY
            first = self.get_candles(uid, start, start + timedelta(days=10), day)
            last = self.get_candles(uid, end - timedelta(days=10), end + timedelta(days=1), day)
            if not first.candles or not last.candles:
                if issues is not None:
                    issues.append(f"IMOEX: нет свечей за {since} — {until}")
                return None
            a = to_decimal(first.candles[0].close)
            b = to_decimal(last.candles[-1].close)
            if not a:
                return None
            return ((b / a - 1) * 100).quantize(Decimal("0.01"))
        except Exception as exc:  # noqa: BLE001
            if issues is not None:
                issues.append(f"IMOEX: данные за {since} — {until} не загружены ({exc})")
            return None

    def rgbi(self) -> Decimal | None:
        """Индекс гособлигаций RGBI (последнее закрытие) — контекстная строка."""
        try:
            from t_tech.invest import schemas

            uid = self.find_instrument_uid("RGBI", "RGBI")
            if not uid:
                return None
            now = datetime.now(timezone.utc)
            day = schemas.CandleInterval.CANDLE_INTERVAL_DAY
            last = self.get_candles(uid, now - timedelta(days=10), now, day)
            if not last.candles:
                return None
            value = to_decimal(last.candles[-1].close)
            return value if value > 0 else None
        except Exception:  # noqa: BLE001
            return None

    def usd_rub(self) -> Decimal | None:
        """Курс USD/RUB TOM по последней цене (контекстная строка в отчёте)."""
        try:
            instr = self.find_instrument("USD000UTSTOM", "USD000UTSTOM")
            if instr is None:
                return None
            resp = self.get_last_prices(figis=[instr.figi])
            if not resp.last_prices:
                return None
            price = to_decimal(resp.last_prices[0].price)
            return price if price > 0 else None
        except Exception:  # noqa: BLE001
            return None

def _bond_risk(client, instrument_uid: str, cache: dict) -> str | None:
    """Уровень риска облигации ("low"/"moderate"/"high"), кэш по UID."""
    if not instrument_uid:
        return None
    if instrument_uid in cache:
        return cache[instrument_uid]
    risk: str | None = None
    try:
        resp = client.get_bond(instrument_uid)
        level = resp.instrument.risk_level
        # SDK может отдать как enum (name="RISK_LEVEL_HIGH"), так и int (1/2/3)
        name = getattr(level, "name", "") or {1: "LOW", 2: "MODERATE", 3: "HIGH"}.get(
            int(level) if isinstance(level, (int, float)) else 0, ""
        )
        if "HIGH" in name:
            risk = "high"
        elif "MODERATE" in name:
            risk = "moderate"
        elif "LOW" in name:
            risk = "low"
    except Exception:  # noqa: BLE001
        risk = None
    cache[instrument_uid] = risk
    return risk


def build_snapshot(client, account_id: str) -> PortfolioSnapshot:
    """Канонический снимок портфеля: сырые данные API → Position/PortfolioSnapshot.

    Тиньковская специфика (НКД облигаций) остаётся здесь, в адаптере:
    доменный слой получает готовый ``amount`` и не знает про ``current_nkd``.
    """
    raw = client.get_portfolio(account_id=account_id)
    now = datetime.now().isoformat(timespec="seconds")
    bond_risk_cache: dict[str, str | None] = {}

    positions: list[Position] = []
    total = to_decimal(raw.total_amount_portfolio)
    cash = to_decimal(raw.total_amount_currencies)
    # Портфельный expected_yield — относительная доходность в %, НЕ деньги.
    total_yield_pct = to_ratio(getattr(raw, "expected_yield", None))

    for pos in raw.positions:
        ticker = pos.ticker
        info = client.get_instrument(pos.instrument_uid)
        instr = info.instrument
        name = instr.name
        itype = instr.instrument_type
        asset_uid = instr.asset_uid or ""
        figi = pos.figi or instr.figi or ""

        quantity = to_quantity(pos.quantity)
        cur_price = to_decimal(pos.current_price)
        avg_price = to_decimal(pos.average_position_price)
        # у облигаций НКД — часть рыночной стоимости позиции (в сумму,
        # но не во «вложено»: pnl = amount - invested остаётся корректным)
        nkd = to_decimal(pos.current_nkd) if itype == "bond" else Decimal("0")
        amount = (cur_price + nkd) * quantity
        invested = avg_price * quantity if avg_price else Decimal("0")
        expected_yield = to_decimal(getattr(pos, "expected_yield", None))
        daily_yield = to_decimal(pos.daily_yield)
        pnl_derived = amount - invested
        # P&L брокера авторитетнее нашего amount − invested: он учитывает НКД,
        # комиссии и методику средней цены (FIFO/взвешенная).
        pnl = expected_yield
        pnl_pct = (pnl / invested * 100) if invested else Decimal("0")

        dividend_share = Decimal("0")
        dividend_amount = Decimal("0")
        if itype in ("share", "etf"):
            try:
                div_from = datetime.now() - timedelta(days=365)
                div_to = datetime.now()
                resp = client.get_dividends(
                    figi=pos.figi or instr.figi, from_=div_from, to=div_to
                )
                if resp.dividends:
                    last = resp.dividends[-1]
                    if last.dividend_net:
                        dividend_share = to_decimal(last.dividend_net)
                    dividend_amount = sum(
                        to_decimal(d.dividend_net or 0) for d in resp.dividends
                    )
            except Exception:
                pass

        bond_risk = _bond_risk(client, pos.instrument_uid, bond_risk_cache) if itype == "bond" else None

        positions.append(
            Position(
                ticker=ticker or pos.figi or "—",
                name=name,
                instrument_type=itype,
                type_name=INSTRUMENT_TYPE_NAMES.get(itype, itype),
                asset_uid=asset_uid,
                instrument_uid=pos.instrument_uid or instr.uid or "",
                figi=figi,
                quantity=quantity,
                current_price=cur_price,
                avg_price=avg_price,
                amount=amount,
                invested=invested,
                pnl=pnl,
                pnl_derived=pnl_derived,
                pnl_pct=pnl_pct,
                expected_yield=expected_yield,
                daily_yield=daily_yield,
                dividend_share=dividend_share,
                dividend_amount=dividend_amount,
                bond_risk=bond_risk,
            )
        )

    allocation: dict[str, Decimal] = {}
    # Точные суммы по классам активов из API (не сумма квантованных позиций).
    for name, mv in (
        ("Акции", getattr(raw, "total_amount_shares", None)),
        ("Облигации", getattr(raw, "total_amount_bonds", None)),
        ("ETF", getattr(raw, "total_amount_etf", None)),
        ("Валюта", getattr(raw, "total_amount_currencies", None)),
        ("Фьючерсы", getattr(raw, "total_amount_futures", None)),
        ("Опционы", getattr(raw, "total_amount_options", None)),
    ):
        value = to_decimal(mv)
        if value > 0:
            allocation[name] = value
    # Фолбэк (например, в тестах без полной разбивки): складываем из позиций.
    if not allocation:
        for p in positions:
            allocation[p.type_name] = allocation.get(p.type_name, Decimal("0")) + p.amount

    # P&L в деньгах — сумма позиционных expected_yield (портфельный — проценты).
    total_pnl = sum(p.pnl for p in positions)

    return PortfolioSnapshot(
        account_id=account_id,
        total=total,
        cash=cash,
        total_pnl=total_pnl,
        total_yield_pct=total_yield_pct,
        positions=positions,
        allocation=[Allocation(k, v) for k, v in sorted(allocation.items(), key=lambda x: -x[1])],
        collected_at=now,
    )