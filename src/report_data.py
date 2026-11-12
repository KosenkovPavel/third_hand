"""ReportData — единый контракт между вычислением и отображением отчёта.

Фасады (CLI/TUI/MCP) рендерят один и тот же ``ReportData`` по-своему: rich-текст
для терминала, JSON для агентов. Сборкой занимается ``report_builder.build_report``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .accounting import AccountingReport, ReturnBreakdown
from .analytics import Recommendation
from .bonds import BondRow
from .calendar import PaymentCalendar
from .contribution_plan import ContributionPlan
from .forecast import Forecast, ScenarioForecast
from .goals import GoalReport, GoalStatus
from .history import HistoryPoint
from .iis import ClaimStatus, IisInfo, IisProgress, IisYear
from .market import MarketOpinion
from .news import NewsEvent
from .picks import Pick
from .portfolio import PortfolioSnapshot
from .profile import UserProfile
from .rebalance import RebalancePlan
from .recommendation_history import RecommendationOutcome
from .risk import RiskReport
from .screener import ScreenRow
from .simulation import IndexSimulation


@dataclass
class ReportData:
    snapshot: PortfolioSnapshot
    acct: AccountingReport
    profile: UserProfile
    acct_full: AccountingReport | None = None
    ndfl_rate: Decimal = Decimal("0.13")

    recommendations: list[Recommendation] = field(default_factory=list)
    iis_years: list[IisYear] = field(default_factory=list)
    iis_warnings: list[str] = field(default_factory=list)
    goal_statuses: list[GoalStatus] = field(default_factory=list)
    history_flows: list[tuple[date, Decimal]] = field(default_factory=list)
    sector_by_uid: dict[str, str] = field(default_factory=dict)
    data_warnings: list[str] = field(default_factory=list)

    return_breakdown: ReturnBreakdown | None = None
    recommendation_entries_count: int = 0
    recommendation_outcomes: list[RecommendationOutcome] | None = None
    rebalance_plan: RebalancePlan | None = None
    contribution_plan: ContributionPlan | None = None
    calendar: PaymentCalendar | None = None
    forecast_data: Forecast | None = None
    scenarios: list[ScenarioForecast] | None = None
    deposit_forecast: Forecast | None = None
    risks: RiskReport | None = None
    market: MarketOpinion | None = None
    iis_progress: IisProgress | None = None
    iis_info: IisInfo | None = None
    tax_notes: list[str] | None = None
    claim_statuses: list[ClaimStatus] | None = None
    xirr_pct: Decimal | None = None
    twr_pct: Decimal | None = None
    benchmark_pct: Decimal | None = None
    all_time_benchmark_pct: Decimal | None = None
    index_simulation: IndexSimulation | None = None
    account_opened: datetime | None = None
    screen_rows: list[ScreenRow] | None = None
    history_points: list[HistoryPoint] | None = None
    bond_rows: list[BondRow] | None = None
    picks: list[Pick] | None = None
    news_events: list[NewsEvent] | None = None
    goal_report: GoalReport | None = None
    stress: bool = False
    usdrub: Decimal | None = None
    key_rate: Decimal | None = None
    rgbi: Decimal | None = None
    deposit_pct: Decimal | None = None
