"""BM2 조건에서 생성기로 만든 독립 20일의 운영 결과를 비교한다."""

import argparse
import json
from time import perf_counter
import re

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.dispatch import DispatchEngine
from src.dispatch.engine import EPS
from .experiment import run_day
from .experiment_inputs import load_experiment, make_locations
from .orders import generate_orders
from .staffing_search import search_staffing


def simulation_inputs(config):
    settings = config["sim"]["experiment"]
    check = settings["consistency"]
    inputs, dongs, types, attrs, radial, quick, _, profiles, _, _, median, benchmarks = load_experiment(config)
    spec = settings["days"][check["day_type"]]
    _, meta = inputs.instance(spec["benchmark"])
    n = int(meta["n_orders"])
    radius = float(meta["service_radius_road_km"])
    inputs.settings = dict(inputs.settings, max_pickers=int(types.at[meta["hub_type"], "max_pickers"]),
                           min_contract_hourly_krw=check["min_contract_hourly_krw"])
    meta["bike_bays"] = min(meta["bike_bays"], check["max_bike_bays"])
    site = inputs.sites.set_index("site_id", drop=False).loc[meta["hub_site_id"]]
    area = "single_high" if dongs.at[site.adm_cd2, "single_hh_ratio"] >= median else "single_low"
    group = spec["day"], area
    boundaries = load_file("02_dong_boundary.geojson", config=config)
    rng = np.random.SeedSequence(check["seed"])
    location_seed, search_seed, validation_seed = rng.spawn(3)
    locations = make_locations(site, boundaries, dongs, inputs.travel, settings, radial, location_seed,
                               road_radius_km=radius)[None][1]
    return inputs, meta, n, radius, group, attrs[group], profiles[group], locations, quick, benchmarks[check["day_type"]], search_seed, validation_seed


def make_engine(config, inputs, meta, n, hourly, attrs, locations, radius, seed):
    cost = inputs.cost
    orders = generate_orders(n, hourly, locations, attrs, radius, np.random.default_rng(seed),
                             open_min=inputs.settings["open_hour"] * 60, close_min=inputs.settings["last_order_min"],
                             pick_base=cost["pick_time_base_min"], pick_per_item=cost["pick_time_per_item_min"],
                             pick_items_cap=config["sim"]["pick_items_cap"])
    peak = locations.drop_duplicates(["cust_lat", "cust_lon"]).set_index(["cust_lat", "cust_lon"]).peak_min
    orders["peak_min"] = peak.reindex(pd.MultiIndex.from_frame(orders[["cust_lat", "cust_lon"]])).to_numpy()
    if len(orders) != n or not orders.road_km.le(radius + EPS).all() or orders.peak_min.isna().any():
        raise ValueError("생성 주문의 개수·반경·피크 시간 검산 실패")
    return DispatchEngine(inputs, orders, meta)


def evaluate_day(engine, plan, travel_seed, quick_seed, quick, settings, *, diagnose=False):
    try:
        metrics, _ = run_day(engine, plan, travel_seed, quick_seed, quick, settings, diagnose=diagnose)
    except ValueError as exc:
        missing = re.fullmatch(r"계획 종료 후 미배송 ([1-9][0-9]*)건", str(exc))
        if missing:
            n = int(missing[1])
            return dict(completed=False, failure_reason=str(exc), undelivered_orders=n,
                        delivered_orders=len(engine.orders) - n)
        if str(exc) == "계획 종료까지 모든 주문을 피킹할 수 없음":
            return dict(completed=False, failure_reason=str(exc))
        raise
    return dict(completed=metrics["undelivered_orders"] == 0, bike_bays=engine.meta["bike_bays"], **metrics)


def check(config):
    started = perf_counter()
    settings = config["sim"]["experiment"]
    limits = settings["consistency"]
    output = ROOT / config["paths"]["outputs"]
    inputs, meta, n, radius, group, attrs, hourly, locations, quick, benchmark, search_seed, validation_seed = simulation_inputs(config)
    if meta["instance"] != "BM2" or meta["weather"] != "clear" or group[0] != "weekend":
        raise ValueError("BM2 주말 맑음 기준 설정 확인 필요")
    plan_days = settings["staffing_search"]["planning_days"]

    def sample(seed, count):
        seeds = np.random.default_rng(seed).integers(0, 2**63, size=(count, 3))
        return [(make_engine(config, inputs, meta, n, hourly, attrs, locations, radius, order), travel, quick_seed)
                for order, travel, quick_seed in seeds]

    planning = sample(search_seed, plan_days)

    def evaluate(plan):
        results = [evaluate_day(engine, plan, t, q, quick, settings, diagnose=True) for engine, t, q in planning]
        if not all("ontime_rate" in row for row in results):
            return []
        return [dict(row, search_ontime=row["ontime_rate"]) for row in results]

    target = limits["min_raw_ontime"]
    plan, history, workload = search_staffing(inputs, meta, benchmark, n, hourly, attrs, locations, settings, target, evaluate)
    search_rows = []
    for trial, (candidate, metrics, score) in enumerate(history, 1):
        search_rows.append(dict(
            trial=trial, selected=candidate is plan, complete_training_days=sum(m["completed"] for m in metrics),
            undelivered_training_orders=sum(m["undelivered_orders"] for m in metrics) if metrics else np.nan,
            ontime_rate=float(np.mean([m["ontime_rate"] for m in metrics])) if metrics else np.nan,
            cost_per_order=float(np.mean([m["cost_per_order"] for m in metrics])) if metrics else np.nan,
            contract_hourly_income_min=min((m["contract_hourly_income"] for m in metrics), default=np.nan),
            rule_violations_max=max((m["rule_violations"] for m in metrics), default=np.nan),
            employed_hours=sum(b["count"] * b["hours"] for b in candidate["employed_blocks"]),
            rank=list(score), plan_json=json.dumps(candidate, ensure_ascii=False, sort_keys=True),
        ))
    num = config["sim"]["repetitions"]
    rows = []
    for repeat, (engine, travel, quick_seed) in enumerate(sample(validation_seed, num), 1):
        result = evaluate_day(engine, plan, travel, quick_seed, quick, settings, diagnose=True)
        for field in ("rider_pressure", "pay_by_hour", "bay_by_hour"):
            result.pop(field, None)
        if not result["completed"]:
            result["cost_per_order"] = np.nan
        rows.append(dict(repetition=repeat, generated_orders=n, **result))
    days = pd.DataFrame(rows)
    complete = days.loc[days.completed]
    source = pd.read_csv(output / "bm_kpi.csv", encoding="utf-8-sig")
    baseline = source.loc[source.instance.eq(meta["instance"]) & source.policy.eq(benchmark["policy"])].iloc[0]
    low, high = limits["min_cost_krw"], limits["max_cost_krw"]
    complete_all = len(complete) == num
    constraints = complete_all and complete.rule_violations.eq(0).all()
    metrics = [
        ("ontime_rate_raw", "ontime_rate", float(baseline.on_time), target, 1.),
        ("cost_per_order_krw", "cost_per_order", float(baseline.cost_per_order), low, high),
        ("undelivered_orders", "undelivered_orders", 0, 0, 0),
        ("bike_bays_max", "bay_max", int(baseline.bay_max), 0, limits["max_bike_bays"]),
        ("contract_hourly_income_krw", "contract_hourly_income", float(baseline.rider_hourly),
         limits["min_contract_hourly_krw"], np.inf),
    ]
    comparison = []
    for label, column, reference, lower, upper in metrics:
        source_days = complete if column == "cost_per_order" else days
        sample_values = source_days.get(column, pd.Series(dtype=float))
        average = sample_values.mean()
        measured = sample_values.min() if column == "contract_hourly_income" else (
            sample_values.max() if column in {"bay_max", "undelivered_orders"} else average)
        comparison.append(dict(
            metric=label, bm2_value=reference, lower=lower, upper=upper, simulation_mean=average,
            simulation_p05=sample_values.quantile(.05), simulation_p95=sample_values.quantile(.95),
            observed_min=sample_values.min(), observed_max=sample_values.max(), evaluated_value=measured,
            statistic_scope="completed_repetitions_only" if column == "cost_per_order" and not complete_all else "all_repetitions",
            passed=bool(len(sample_values.dropna()) == num and lower - EPS <= measured <= upper + EPS),
        ))
    summary = pd.DataFrame(comparison)
    summary = summary.assign(
        site_id=meta["hub_site_id"], radius_km=radius, orders_per_day=n, policy=benchmark["policy"],
        n_repetitions=num, completed_repetitions=len(complete), staffing_trials=len(history),
        legal_staffing=bool(constraints), gate_passed=bool(constraints and summary.passed.all()),
        delivered_orders=int(days.delivered_orders.sum()) if "delivered_orders" in days and days.delivered_orders.notna().all() else np.nan,
        undelivered_orders=int(days.undelivered_orders.sum()) if "undelivered_orders" in days and days.undelivered_orders.notna().all() else np.nan,
        execution_seconds=perf_counter() - started,
    )
    if complete_all and (not complete.delivered_orders.eq(n).all() or not complete.distance_max_km.le(radius + EPS).all()):
        raise ValueError("BM2 주문 배송 또는 반경 검산 실패")
    for name, frame in [("check", summary), ("repetitions", days), ("staffing_search", pd.DataFrame(search_rows)),
                        ("workload", pd.DataFrame(workload))]:
        frame.to_csv(output / f"sim_bm2_consistency_{name}.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False), flush=True)
    if "bay_max" in days:
        print(f"대기 공간 초과 {int(days.bay_max.gt(meta['bike_bays']).sum())}회, "
              f"수급 미달 {int(days.supply_ok.eq(False).sum())}회", flush=True)
    return bool(summary.gate_passed.all())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    check(load_config(args.config))


if __name__ == "__main__":
    main()
