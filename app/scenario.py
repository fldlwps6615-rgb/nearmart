"""Cached financial what-if calculations; no demand redistribution or file writes.

Success: (annual DataFrame, KPI dict). Validation failure: (None, {"error": str}).
The UI must display the error and stop using that result.
"""
from dataclasses import dataclass, field
import math
from numbers import Real

import pandas as pd
import streamlit as st

from app import data

LABOR_COLUMNS = (
    "contract_rider_cost_per_order_krw",
    "employed_rider_cost_per_order_krw",
    "picker_cost_per_order_krw",
)


def _default_rate() -> float:
    reference = data.load_alt_compare()
    rates = reference.loc[reference["selected"].eq(True), "discount_rate"].dropna().unique()
    if len(rates) != 1:
        raise ValueError("기준표에 단일 기본 할인율이 필요합니다.")
    return float(rates.item())


@dataclass
class Conditions:
    alt: str
    scen: str
    discount_rate: float = field(default_factory=_default_rate)
    w1_on: bool = False
    # The requested W1 control default equals the baseline rate, not a wage forecast.
    w1_rate: float = field(default_factory=_default_rate)
    excluded_sites: list[str] = field(default_factory=list)
    # 주문당 운영비(라이더+피커) 배수. 1.0 = 시뮬레이션 기반 강건 운영비(15분 5,642원/건, 2027년 단가)
    cost_factor: float = 1.0
    # 연간 운영비 개선율(가설): 2028년부터 매년 라이더·피커 주문당 비용이 이 비율만큼 누적 감소(생산성 향상)
    annual_cut: float = 0.0
    # 거점 이전 시뮬레이션: ((기존 거점, 이전할 후보지), ...)
    relocations: tuple = ()


def _numeric_rate(rate, label: str) -> float:
    if isinstance(rate, bool) or not isinstance(rate, Real) or not math.isfinite(rate):
        raise ValueError(f"{label}은 유한한 숫자여야 합니다.")
    return float(rate)


def _shock_start_year() -> int:
    # W1 begins in the second forecast year: current output starts in 2027.
    return int(data.load_effective_config()["demand"]["forecast_start_year"]) + 1


def apply_labor_shock(ops: pd.DataFrame, rate: float) -> pd.DataFrame:
    rate = _numeric_rate(rate, "인건비 변화율")
    multiplier = 1 + rate
    if multiplier < 0:
        raise ValueError("인건비 변화 후 단가는 음수일 수 없습니다.")
    result = ops.copy(deep=True)
    required = {"year", *LABOR_COLUMNS}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(f"인건비 계산 필수 열 누락: {sorted(missing)}")
    if multiplier == 1:
        return result
    # CSV cost columns can be int64. Fractional shocks require a float copy;
    # explicitly promote instead of relying on pandas' prohibited implicit cast.
    result = result.astype({column: float for column in LABOR_COLUMNS})
    affected = result["year"].ge(_shock_start_year())
    result.loc[affected, list(LABOR_COLUMNS)] = result.loc[affected, list(LABOR_COLUMNS)] * multiplier
    return result


def exclude_sites(plan: pd.DataFrame, ops: pd.DataFrame, sites) -> tuple[pd.DataFrame, pd.DataFrame]:
    if isinstance(sites, (str, bytes)):
        raise ValueError("제외 거점은 site_id 목록으로 전달해야 합니다.")
    excluded = set(sites)
    if any(not isinstance(site, str) for site in excluded):
        raise ValueError("제외 거점의 site_id는 문자열이어야 합니다.")
    unknown = excluded - set(plan["site_id"])
    if unknown:
        raise ValueError(f"계획에 없는 제외 거점: {sorted(unknown)}")
    # Keep remaining demand untouched. Removing a relocation source can trigger
    # the engine's original relationship validation; surface it without repair.
    return (
        plan.loc[~plan["site_id"].isin(excluded)].copy(deep=True),
        ops.loc[~ops["site_id"].isin(excluded)].copy(deep=True),
    )


@st.cache_data(show_spinner=False)
def run(cond: Conditions):
    try:
        rate = _numeric_rate(cond.discount_rate, "할인율")
        relocations = tuple(tuple(pair) for pair in cond.relocations)
        plan = data.plan_for(cond.alt, relocations)
        ops = data.ops_for(cond.alt, cond.scen, relocations)
        excluded = [site for site in cond.excluded_sites if site in set(plan["site_id"])]
        plan, ops = exclude_sites(plan, ops, excluded)
        factor = _numeric_rate(cond.cost_factor, "운영비 배수")
        if factor != 1:
            ops = ops.astype({column: float for column in LABOR_COLUMNS})
            ops.loc[:, list(LABOR_COLUMNS)] = ops.loc[:, list(LABOR_COLUMNS)] * factor
        cut = _numeric_rate(cond.annual_cut, "연간 운영비 개선율")
        if cut:
            ops = ops.astype({column: float for column in LABOR_COLUMNS})
            decay = ops["year"].map(lambda year: (1 - cut) ** max(0, int(year) - 2027))
            ops.loc[:, list(LABOR_COLUMNS)] = ops.loc[:, list(LABOR_COLUMNS)].mul(decay, axis=0)
        if cond.w1_on:
            ops = apply_labor_shock(ops, cond.w1_rate)
        engine = data.get_finance_engine(rate)
        return engine.evaluate(plan, ops, cond.scen)
    except ValueError as exc:
        return None, {"error": str(exc)}


@st.cache_data(show_spinner=False)
def baseline(cond: Conditions):
    """Ignore every condition except alternative/scenario; reuse cached defaults."""
    return run(Conditions(alt=cond.alt, scen=cond.scen))

