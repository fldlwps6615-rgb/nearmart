"""저장된 계획과 운영 근거를 공용 네트워크·재무 엔진에 연결한다."""

from copy import deepcopy
import json

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_file
from src.demand.model import competition
from src.finance.engine import FinanceEngine
from src.network.feedback import COST, FeedbackModel
from src.network.feedback_basis import validated_runs
from src.network.model import NetworkModel, read_output, require

ASSIGNMENT_YEARS = (2027, 2031, 2036, 2041, 2046)
MARKETS = ("low", "base", "high")
DELIVERY_COSTS = ("contract_rider_cost", "employed_rider_cost", "picker_cost", "late_coupon", "external_quick")
ISSUE_COLUMNS = ("rule_id", "file", "check", "n_rows", "example", "action")


def write_csv(path, frame):
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def read_issues(path):
    rows = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    require(tuple(rows) == ISSUE_COLUMNS, f"{path.name} 품질 로그 컬럼 불일치")
    require(rows.rule_id.ne("").all() and rows.n_rows.str.fullmatch(r"\d+").all()
            and rows.action.ne("").all(), f"{path.name} 규칙·건수·처리 확인 필요")
    return rows


def plan_input(model, name):
    frame = read_output(model.out / f"network_plan_{name}.csv")
    rows = model.engine._plan(frame)
    plan = tuple(sorted((model.index[r["site_id"]], r["open_year"], r["close_year"])
                        for r in rows if r["open_year"] < r["close_year"]))
    return frame, plan


def evaluate(model, frame, plan, radius, market, band="mid", *, engine=None, wage_shock=None):
    ops, state = model.operating(plan, radius, market, band, details=True)
    if wage_shock is not None:
        year, rate, employed_wage_unit = wage_shock
        after = ops.year.ge(year)
        paid_employed = ops[COST[1]].gt(0)
        ops.loc[after, [COST[0], COST[2]]] *= 1 + rate
        ops.loc[after & paid_employed, COST[1]] += rate * employed_wage_unit
    annual, kpi = (engine or model.engine).evaluate(frame, ops, market)
    yearly = model.summaries(plan, state).set_index("year")
    require(np.isclose(annual.discounted_cf.sum(), kpi["npv"], atol=0.01, rtol=0)
            and np.isclose(max(0, -annual.cum_cash_flow.min()), kpi["max_funding"], atol=0.01, rtol=0)
            and np.allclose(annual.iloc[1:].orders, yearly.orders_per_day * model.days_per_year),
            "NPV·최대 자금·전달 주문 합계 검산 실패")
    return annual, kpi, ops, state, yearly


def assignment(model, state, yearly):
    frame = pd.concat([model.assignment_frame(state, year) for year in ASSIGNMENT_YEARS], ignore_index=True)
    totals = frame.groupby("year").expected_orders_per_day.sum()
    require(len(frame) == len(model.dong) * len(ASSIGNMENT_YEARS)
            and not frame.duplicated(["year", "adm_cd2"]).any()
            and np.allclose(totals, yearly.loc[totals.index, "orders_per_day"]), "T2 키 또는 주문 합계 검산 실패")
    return frame


def selected_rows(output, version):
    comparison = read_output(output / f"alt_compare_{version}.csv")
    selected = comparison.loc[comparison.selected.eq(1)].set_index(["alternative", "scenario"], verify_integrity=True)
    for name in selected.index.get_level_values("alternative").unique():
        require(set(selected.loc[name].index) == set(MARKETS), f"{name} 공통 시장 시나리오 누락")
    require(not selected.empty, "선택 계획 누락")
    return selected


def feedback_models(config, version):
    output = ROOT / config["paths"]["outputs"]
    rows = selected_rows(output, version)
    basis = read_output(output / f"feedback_basis_{version}.csv").set_index("T", verify_integrity=True)
    models = {radius: FeedbackModel(deepcopy(config), radius, basis.loc[radius])
              for radius in rows["T"].unique()}
    return rows, models


def benchmark_hours(config, version, model):
    """대표일별 지급액·접속시간과 직고용 비용 중 임금 성분을 주문당 단위로 환산한다."""
    output = model.out
    radius = model.radius
    wage = model.engine.par("employed_wage_krw_per_h_2027") * model.engine.par("labor_load_factor")
    if version == "v4":
        cells = read_output(output / "feedback_calendar_cells_v4.csv").query("T == @radius and capacity_orders > 0")
        days = read_output(output / "staffing_validation_v4.csv")
        require(not cells.empty, f"{radius}분 위탁 접속시간 근거 누락")
        frontier = read_output(output / "staffing_frontier.csv")
        pay, hours, employed = 0.0, 0.0, 0.0
        for cell in cells.itertuples():
            runs = days.query("instance == @cell.instance and radius_min == @radius and orders == @cell.capacity_orders "
                              "and candidate == @cell.candidate")
            require(len(runs) == cell.repetitions and runs.undelivered.eq(0).all(), "v4 위탁 근거 검산 실패")
            weight = cell.weight / cells.weight.sum()
            pay += weight * runs.contract_unit_krw.mean()
            hours += weight * (runs.contract_hours / runs.orders).mean()
            selected = frontier.query("instance == @cell.instance and radius_min == @radius and orders == @cell.capacity_orders "
                                      "and candidate == @cell.candidate and phase == 'validation' and certified == True")
            employed += weight * wage * paid_hours(selected.plan_json.item()) / cell.capacity_orders
        require(hours > 0 and np.isclose(pay, model.contract_unit), "v4 위탁 지급액·접속시간 검산 실패")
        return pay / hours, employed

    runs, _, _ = validated_runs(model, config["feedback"])
    runs = runs[runs.radius_type.ne("original")].copy()
    plans = read_output(output / "radius_bm_v2_plans.csv")
    keys = ["instance", "radius_type", "orders"]
    plans = plans.loc[plans.plan_json.notna(), [*keys, "plan_json"]].copy()
    plans["employed_hours"] = plans.pop("plan_json").map(paid_hours)
    runs = runs.merge(plans, on=keys, validate="many_to_one", how="left")
    require(runs.employed_hours.notna().all(), "직고용 교대 기록 누락")
    runs["employed_wage_unit"] = wage * runs.employed_hours / runs.orders
    paid = runs.contract_unit.gt(0)
    require(runs.loc[paid, "contract_hourly"].gt(0).all(), "위탁 접속시간 환산 불가")
    runs["hours_per_order"] = 0.0
    runs.loc[paid, "hours_per_order"] = runs.loc[paid, "contract_unit"] / runs.loc[paid, "contract_hourly"]
    metrics = ["contract_unit", "hours_per_order", "employed_wage_unit"]
    if version == "v2":
        totals = runs.loc[runs.radius_type.eq(str(radius)), metrics].mean()
    else:
        cells = read_output(output / "feedback_calendar_cells_v3.csv").query("T == @radius")
        require(len(cells) == 3 and np.isclose(cells.weight.sum(), 1), "v3 대표일 달력 가중 검산 실패")
        totals = pd.Series(0.0, index=metrics)
        for cell in cells.itertuples():
            source = str(cell.source_radius_min)
            match = runs.query("radius_type == @source and day_type == @cell.day_type and orders == @cell.source_orders")
            require(len(match) == cell.validation_rows, "v3 대표일 위탁 근거 회차 불일치")
            totals += cell.weight * match[metrics].mean()
    require(totals.hours_per_order > 0 and np.isclose(totals.contract_unit, model.contract_unit)
            and 0 <= totals.employed_wage_unit <= model.employed_unit, "위탁 지급액·직고용 임금 성분 검산 실패")
    return totals.contract_unit / totals.hours_per_order, totals.employed_wage_unit


def paid_hours(value):
    return sum(block["hours"] * block["count"] for block in json.loads(value)["employed_blocks"])


def competitor_model(model):
    config = model.cfg
    settings = config["demand"]
    alternatives = {**settings, "w2": True}
    competitors = load_file("09_competitor_hubs.csv", config=config)
    rules = load_file("10_market_rules.csv", config=config)
    result = deepcopy(model)
    for index, year in enumerate(model.years):
        _, before = competition(model.dong, competitors, year, rules, settings)
        _, after = competition(model.dong, competitors, year, rules, alternatives)
        require((after <= before).all(), "W2 경쟁 계수 방향 검산 실패")
        for key in result.demands:
            result.demands[key][index] *= after / before
    return result


def ref_rate(model, year):
    return (1 + model.engine.par("contract_fee_growth")) ** (year - model.engine.start)


def city_metrics(model, annual, yearly, assignments, hourly, year=2031):
    city = annual.set_index("year").loc[year]
    require(city.orders > 0, "도시 운영 KPI 분모가 0입니다")
    served = assignments.query("year == @year and expected_orders_per_day > 0")
    require(served.avg_delivery_min.notna().all() and served.late_rate.between(0, 1).all(), "도시 운영 KPI 결측")
    weights = served.expected_orders_per_day
    require(np.isclose(weights.sum(), yearly.loc[year, "orders_per_day"]), "도시 운영 KPI 분모 불일치")
    return dict(on_time=1 - np.average(served.late_rate, weights=weights),
                avg_delivery_min=np.average(served.avg_delivery_min, weights=weights),
                delivery_cost_per_order=city[list(DELIVERY_COSTS)].sum() / city.orders,
                contract_hourly=hourly * ref_rate(model, year))


def discount_engine(model, rate):
    cfg = deepcopy(model.cfg)
    cfg["finance"]["discount_rate"] = rate
    return FinanceEngine(cfg)
