"""⑤ 조합별 인력 계획 탐색 및 P2 운영 반복."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import copy
from dataclasses import asdict
from hashlib import sha256
import math
import os
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.dispatch import DispatchEngine, DispatchInputs
from src.dispatch.engine import EPS
from src.dispatch.inputs import Geometry
from src.travel_time import TravelTime
from .experiment_inputs import grid, load_experiment, make_locations
from .orders import generate_orders
from .staffing import candidate_plans, clearance_plan, reserve_plan


_WORKER = None


def init_worker(config, context):
    global _WORKER
    _WORKER = (config, DispatchInputs(config), context)


def run_day(engine, plan, travel_seed, quick_seed, quick_residual, settings, *, diagnose=False):
    travel_rng, quick_rng = np.random.default_rng(travel_seed), np.random.default_rng(quick_seed)
    ids = {key: i for i, key in enumerate(engine.order_ids)}
    geometry = engine.geometry

    def external(order, pick_end):
        ready = pick_end + quick_rng.choice(quick_residual)
        index = ids[order["order_id"]]
        direct = geometry.minutes(geometry.hub, index, ready)
        return ready + direct * TravelTime.stochastic_multiplier(quick_rng) + geometry.handover[index]

    result = engine.simulate(plan, external_delivery=external,
                             travel_multiplier=lambda: TravelTime.stochastic_multiplier(travel_rng),
                             allow_incomplete=diagnose)
    kpi = result.kpi
    order_ids = list(result.t3.order_id) + list(result.external.get("order_id", ()))
    missing = kpi.get("undelivered_orders", 0)
    if len(order_ids) + missing != len(ids) or len(set(order_ids)) != len(order_ids) or not set(order_ids) <= ids.keys():
        raise ValueError("생성 주문과 내부·외부 배송 주문 불일치")
    delivered = np.full(len(ids), np.nan)
    for table in (result.t3, result.external):
        if len(table):
            delivered[[ids[key] for key in table.order_id]] = table.delivered_min
    duration = delivered - engine.arrival
    if np.isinf(duration).any() or np.nanmin(duration) < 0:
        raise ValueError("전체 주문 배송시간의 결측 또는 음수")
    if not result.trips.weight_kg.le(engine.cost["trip_max_kg"] + EPS).all() or not result.trips.orders.le(
            engine.cost["trip_max_orders"]).all():
        raise ValueError("트립의 중량·건수 상한 위반")
    n = len(duration)
    delta = settings["correction_mean_min"]
    late = duration > engine.settings["sla_min"] + EPS
    corrected_late = duration + delta > engine.settings["sla_min"] + EPS
    if int(late.sum()) != kpi["late_orders"] + kpi["external_late_orders"]:
        raise ValueError("전체 주문 지연 재계산 불일치")
    contract_cost = kpi["contract_rider_cost_per_order_krw"]
    employed_cost = kpi["employed_rider_cost_per_order_krw"]
    avg, p90 = float(np.nanmean(duration)), float(np.nanquantile(duration, .9))
    metrics = dict(
        delivered_orders=n-missing, undelivered_orders=missing, external_orders=len(result.external), external_quick_share=len(result.external) / n,
        contract_rider_cost_per_order_krw=contract_cost, employed_rider_cost_per_order_krw=employed_cost,
        rider_cost_per_order=contract_cost + employed_cost,
        picker_cost_per_order=kpi["picker_cost_per_order_krw"],
        employed_share=float(result.t3.rider_type.eq("employed").sum()) / n,
        contract_hourly_income=kpi["rider_hourly"], avg_delivery_min=avg, p90_delivery_min=p90,
        ontime_rate=1 - (int(late.sum()) + missing) / n, avg_delivery_min_corrected=avg + delta,
        p90_delivery_min_corrected=p90 + settings["correction_p90_min"],
        ontime_rate_corrected=1 - (int(corrected_late.sum()) + missing) / n, late_orders=int(late.sum()),
        late_orders_corrected=int(corrected_late.sum()), supply_ok=kpi["supply_ok"],
        supply_shortfall=kpi["earnings_shortfall_krw"], bay_max=kpi["bay_max"],
        rule_violations=kpi["rule_violations"], contract_hours=kpi["contract_hours"],
        employed_hours=kpi["employed_hours"], picker_hours=kpi["picker_hours"],
        cost_per_order=kpi["cost_per_order"], contract_pay_krw=kpi["contract_pay_krw"],
        distance_max_km=float(engine.orders.road_km.max()),
    )
    if diagnose:
        from .staffing_search import staffing_diagnostics
        metrics.update(staffing_diagnostics(engine, result, duration, corrected_late))
    indices = np.searchsorted(settings["travel_bin_edges"][1:], engine.orders.peak_min, side="left")
    count = len(settings["travel_bin_edges"]) - 1
    if (indices >= count).any():
        raise ValueError("피크 이동시간 구간 상한 초과")
    bins = np.column_stack((
        np.bincount(indices, minlength=count),
        np.bincount(indices, weights=duration, minlength=count),
        np.bincount(indices, weights=late, minlength=count),
        np.bincount(indices, weights=corrected_late, minlength=count),
    ))
    return metrics, bins


def plan_rank(metrics, target):
    # 서비스·공급·공간을 먼저 보고, 통과한 후보에서는 필요한 직고용과 위탁시간을 줄인다.
    return (
        metrics["rule_violations"], not metrics["supply_ok"],
        round(max(0, target - metrics["ontime_rate_corrected"]), 12),
        metrics["employed_hours"], metrics["contract_hours"], metrics["rider_cost_per_order"],
    )


def select_plan(case, policy, hourly, attrs, peak_mean, meta, inputs, settings, engine, seeds, quick):
    candidates = list(candidate_plans(case, policy, hourly, attrs, peak_mean, meta, inputs, settings))
    # 처리량이 높은 후보를 우선하고, 직고용만으로 처리하는 대안도 확인한다.
    def priority(plan):
        hours = sum(plan["contract"].values())
        employed = sum(b["hours"] * b["count"] for b in plan["employed_blocks"])
        return -(hours + employed), -hours

    candidates.sort(key=priority)
    direct = next(p for p in candidates if not any(p["contract"].values()))
    shortlist = candidates[:settings["pilot_candidates"] - 1]
    if direct not in shortlist:
        shortlist.append(direct)
    best = None
    attempts = 0
    failures = []
    for plan in shortlist:
        attempts += 1
        try:
            metrics, _ = run_day(engine, plan, *seeds[1:], quick, settings)
        except ValueError as exc:
            if not str(exc).startswith(("계획 종료 후 미배송 ", "계획 종료까지 모든 주문을 피킹할 수 없음")):
                raise
            failures.append(str(exc))
            continue
        rank = plan_rank(metrics, settings["search_target"])
        if best is None or rank < best[0]:
            best = rank, plan, metrics
    if best is None:
        plan = clearance_plan(direct, case, inputs, meta)
        attempts += 1
        try:
            metrics, _ = run_day(engine, plan, *seeds[1:], quick, settings)
            best = plan_rank(metrics, settings["search_target"]), plan, metrics
        except ValueError as exc:
            if not str(exc).startswith("계획 종료 후 미배송 "):
                raise
            best = None, plan, {"ontime_rate_corrected": np.nan}
    return best[1], best[2], attempts


def run_case(case, repetitions=None, *, failed_rows=None, saved_plan=None):
    started = perf_counter()
    config, inputs, context = _WORKER
    settings = config["sim"]["experiment"]
    sites, hubs, attrs_by_group, profiles, order_profiles, effects, policies, location_pools, quick = context
    site = sites[case.hub_type, case.rider_class]
    spec = settings["days"][case.day_type]
    radius_km, locations, area = location_pools[case.hub_type, case.rider_class][case.radius_min]
    group = spec["day"], area
    attrs, hourly = attrs_by_group[group], profiles[group]
    meta = dict(instance=f"{case.hub_type}-{case.rider_class}-{case.day_type}-{case.radius_min}-{case.orders_per_day}",
                hub_lat=site.lat, hub_lon=site.lon, hub_gu=site.gu, bike_bays=int(site.bike_bays),
                rider_area_class=case.rider_class, weather=spec["weather"])
    inputs = copy(inputs)
    year_factor = (1 + inputs.cost["contract_fee_growth"]) ** (settings["year"] - config["sim"]["replay"]["fee_base_year"])
    inputs.settings = dict(inputs.settings, max_pickers=int(hubs.loc[case.hub_type, "max_pickers"]),
                           min_contract_hourly_krw=inputs.cost["contract_min_hourly_earning_krw"])
    # 비용 곡선은 재무 계약의 2027년 가격이다. 연도 변경은 수급 판정의 명목 수입에 적용한다.
    day_count = config["sim"]["repetitions"] if repetitions is None else repetitions
    key = (config["sim"]["seed"], case.hub_type, case.rider_class, case.day_type, case.radius_min)
    seed = int.from_bytes(sha256(repr(key).encode()).digest()[:8], "little")
    seeds = np.random.default_rng(seed).integers(0, 2**63, size=(day_count + 1, 3))

    def make_engine(order_seed):
        cost = inputs.cost
        orders = generate_orders(case.orders_per_day, hourly, locations, attrs, radius_km,
                                 np.random.default_rng(order_seed),
                                 open_min=inputs.settings["open_hour"] * 60, close_min=inputs.settings["last_order_min"],
                                 pick_base=cost["pick_time_base_min"], pick_per_item=cost["pick_time_per_item_min"],
                                 pick_items_cap=config["sim"]["pick_items_cap"])
        peak = locations.set_index(["cust_lat", "cust_lon"]).peak_min
        if peak.index.has_duplicates:
            peak = peak[~peak.index.duplicated()]
        orders["peak_min"] = peak.reindex(pd.MultiIndex.from_frame(orders[["cust_lat", "cust_lon"]])).to_numpy()
        if not orders.peak_min.le(case.radius_min + EPS).all():
            raise ValueError("생성 고객의 공용 피크 이동시간이 권역을 초과")
        return DispatchEngine(inputs, orders, meta)

    if failed_rows is not None:
        recovered = []
        for row in failed_rows:
            repeat = int(row["repetition"])
            order_seed, travel_seed, quick_seed = seeds[repeat]
            engine = make_engine(order_seed)
            missing = int(row["reason"].split()[-1].removesuffix("건"))
            recovery, start, hours = reserve_plan(saved_plan, inputs, meta, missing)
            metrics, _ = run_day(engine, recovery, travel_seed, quick_seed, quick, settings)
            recovered.append(dict(**asdict(case), repetition=repeat, completed=True, **metrics,
                                  recovery_used=True, original_unserved_orders=missing,
                                  recovery_start_hour=start, recovery_staff_hours=hours,
                                  original_failure=row["reason"]))
        for method in (Geometry.road, Geometry.straight, Geometry.minute_at_hour):
            method.cache_clear()
        return recovered

    engine = make_engine(seeds[0, 0])
    plan, pilot, attempts = select_plan(case, policies[case.day_type], hourly, attrs, locations.peak_min.mean(),
                                       meta, inputs, settings, engine, seeds[0], quick)
    rows, all_bins = [], []
    for repeat, (order_seed, travel_seed, quick_seed) in enumerate(seeds[1:], 1):
        engine = make_engine(order_seed)
        try:
            metrics, bins = run_day(engine, plan, travel_seed, quick_seed, quick, settings)
        except ValueError as exc:
            if not str(exc).startswith(("계획 종료 후 미배송 ", "계획 종료까지 모든 주문을 피킹할 수 없음")):
                raise
            rows.append(dict(repetition=repeat, completed=False, reason=str(exc)))
            continue
        rows.append(dict(repetition=repeat, completed=True, **metrics))
        all_bins.append(bins)
    for method in (Geometry.road, Geometry.straight, Geometry.minute_at_hour):
        method.cache_clear()
    plan_row = dict(**asdict(case), site_id=site.site_id, bike_bays=meta["bike_bays"], radius_km=radius_km,
                    area_type=area, policy=plan["policy"], target=plan["target"], wait=plan["wait"], margin=plan["margin"],
                    search_attempts=attempts, search_ontime_corrected=pilot["ontime_rate_corrected"],
                    supply_threshold_krw=inputs.settings["min_contract_hourly_krw"] * year_factor,
                    nominal_income_factor=year_factor, weather_demand_multiplier=effects.loc[("weather", spec["weather"]), "multiplier"],
                    profile_basket_krw=order_profiles.loc[group, "avg_basket_krw"], seconds=perf_counter() - started)
    return case, rows, all_bins, plan_row, plan


def context_for(config):
    inputs, dongs, hubs, attrs, radial, quick, sites, profiles, orders, effects, threshold, plans = load_experiment(config)
    settings = config["sim"]["experiment"]
    boundaries = load_file("02_dong_boundary.geojson", config=config)
    seeds = np.random.SeedSequence(config["sim"]["seed"]).spawn(len(sites))
    locations = {}
    for (key, site), seed in zip(sites.items(), seeds):
        pools = make_locations(site, boundaries, dongs, inputs.travel, settings, radial, seed)
        locations[key] = {radius: (*pool, "single_high" if dongs.at[site.adm_cd2, "single_hh_ratio"] >= threshold
                                   else "single_low") for radius, pool in pools.items()}
    return sites, hubs, attrs, profiles, orders, effects, plans, locations, quick


def run(config, mode):
    from .experiment_report import write_results

    output = ROOT / config["paths"]["outputs"]
    gate = output / "sim_bm2_consistency_check.csv"
    if not gate.exists() or not pd.read_csv(gate, encoding="utf-8-sig").gate_passed.eq(True).all():
        raise ValueError("BM2 일관성 검사 통과 결과를 확인한 뒤 전체 실험을 실행합니다.")
    started = perf_counter()
    cfg = config["sim"]
    settings = cfg["experiment"]
    context = context_for(config)
    full, reduced = list(grid(config, False)), list(grid(config, True))
    workers = min(settings["workers"], os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(config, context)) as pool:
        sample = [next(c for c in reduced if c.hub_type == hub and c.rider_class == rider and
                       c.day_type == day and c.order_level == level and c.radius_min == radius)
                  for hub, rider, day, level, radius in [
                      ("S", "A", "weekday_clear", .6, 12), ("M", "A", "weekday_clear", 1., 15),
                      ("M", "A", "weekday_rain", 1.2, 20), ("L", "B", "weekday_rain", 1.2, 20),
                      ("S", "C", "weekend_clear", 1.2, 20), ("L", "C", "weekend_clear", 1., 12)]]
        sample_started = perf_counter()
        futures = [pool.submit(run_case, case, 3) for case in sample]
        measured = [future.result() for future in futures]
        sample_seconds = perf_counter() - sample_started
        per_case = sample_seconds / math.ceil(len(sample) / workers) * (cfg["repetitions"] + settings["pilot_candidates"]) / (
            3 + settings["pilot_candidates"])
        full_estimate = per_case * math.ceil(len(full) / workers)
        reduced_estimate = per_case * math.ceil(len(reduced) / workers)
        chosen = reduced if mode == "reduced" or (mode == "auto" and full_estimate > cfg["runtime_limit_sec"]) else full
        print(f"시간 예측 전체 {full_estimate:.0f}초·축소 {reduced_estimate:.0f}초, 선택 {len(chosen)}개 × {cfg['repetitions']}회",
              flush=True)
        futures = [pool.submit(run_case, case) for case in chosen]
        results = []
        for done in as_completed(futures):
            results.append(done.result())
            if len(results) % 12 == 0 or len(results) == len(chosen):
                print(f"완료 {len(results)}/{len(chosen)}조합, {perf_counter() - started:.1f}초", flush=True)
    write_results(config, results, started, len(chosen) != len(full), full_estimate, reduced_estimate, workers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--grid", choices=("auto", "full", "reduced"), default="auto")
    args = parser.parse_args()
    run(load_config(args.config), args.grid)


if __name__ == "__main__":
    main()
