"""벤치마크 주문에서 고객 위치만 교체해 P2의 서비스 반경을 검증한다."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.dispatch import DispatchEngine, DispatchInputs
from src.dispatch.engine import EPS, SearchDeadline
from src.dispatch.search import search_instance
from src.dispatch.verify_t3 import read_csv
from src.travel_time import TravelTime
from .experiment_inputs import make_locations, pilot_distributions


LOCATION = ["cust_lat", "cust_lon", "adm_cd2", "gu"]
COLUMNS = [
    "instance", "day_type", "radius_min", "radius_km", "orders", "ontime_det",
    "ontime_stoch_mean", "ontime_stoch_p05", "ontime_stoch_corrected", "undelivered",
    "cost_per_order", "rider_cost_per_order", "picker_cost_per_order", "employed_share",
    "contract_hourly", "orders_per_trip", "bay_max", "supply_ok", "feasible",
]
ENCODING = "utf-8-sig"


def protected_hashes(output):
    paths = [ROOT / "report/T3_benchmark_dispatch.csv", ROOT / "src/travel_time.py"]
    paths += list((ROOT / "src/dispatch").glob("*.py"))
    paths += list((ROOT / "data").rglob("*"))
    paths += list((output / "dispatch").rglob("*"))
    paths += list(output.glob("bm_*.csv")) + list(output.glob("dispatch_*.csv"))
    return {path: sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}


def deterministic_row(result):
    kpi = result.kpi
    return dict(
        ontime_det=kpi["on_time"], cost_per_order=kpi["cost_per_order"],
        rider_cost_per_order=kpi["contract_rider_cost_per_order_krw"] + kpi["employed_rider_cost_per_order_krw"],
        picker_cost_per_order=kpi["picker_cost_per_order_krw"], employed_share=result.t3.rider_type.eq("employed").mean(),
        contract_hourly=kpi["rider_hourly"], orders_per_trip=kpi["orders_per_trip"],
        bay_max=kpi["bay_max"], supply_ok=kpi["supply_ok"], feasible=kpi["feasible"],
    )


def baseline_rows(config, inputs, output, selection, tolerance):
    """원위치·원인력으로 재실행하고 ④의 제출 주문, 인력표 및 KPI를 대조."""
    original_t3 = read_csv(ROOT / "report/T3_benchmark_dispatch.csv")
    original_kpi = read_csv(output / "bm_kpi.csv").set_index(["instance", "policy"])
    rows, checks = [], []
    for name, spec in config["sim"]["radius_bm"]["instances"].items():
        policy = selection[name]
        if policy != config["sim"]["radius_bm"]["policy"]:
            raise ValueError(f"{name}: ④ 선택 정책이 지정한 P2와 다릅니다.")
        folder = output / "dispatch" / name / policy
        plan = json.loads((folder / "plan.json").read_text(encoding=ENCODING))
        orders, meta = inputs.instance(name)
        result = DispatchEngine(inputs, orders, meta).simulate(plan)
        reference_t3 = original_t3.loc[original_t3.instance.eq(name)].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(result.t3, reference_t3, check_exact=False, rtol=0, atol=tolerance)
        pd.testing.assert_frame_equal(result.staffing, read_csv(folder / "staffing.csv"), check_exact=True)
        reference = original_kpi.loc[(name, policy)]
        for key, value in reference.items():
            actual = result.kpi[key]
            if isinstance(value, (bool, np.bool_)):
                equal = value == actual
            else:
                equal = math.isclose(float(value), float(actual), rel_tol=1e-10, abs_tol=tolerance)
            if not equal:
                raise ValueError(f"{name} 기준 KPI {key} 불일치: ④ {value}, 재실행 {actual}")
        rows.append(dict(instance=name, day_type=spec["day_type"], radius_km=meta["service_radius_road_km"],
                         orders=len(orders), undelivered=0, **deterministic_row(result)))
        checks.append(dict(check="원래 BM 위치·T3·인력·KPI 일치", instance=name, passed=True,
                           detail=f"T3 {len(result.t3)}행, KPI {len(reference)}개"))
    return rows, checks


def case_inputs(config, inputs):
    dongs = load_file("01_dong_master.csv", config=config).set_index("adm_cd2")
    boundaries = load_file("02_dong_boundary.geojson", config=config)
    _, radial, _ = pilot_distributions(config, inputs, dongs)
    settings = config["sim"]["experiment"]
    instances = config["sim"]["radius_bm"]["instances"]
    sites = inputs.sites.set_index("site_id", drop=False)
    pools = {}
    source_seeds = np.random.SeedSequence(config["sim"]["seed"]).spawn(len(instances))
    for (name, spec), master in zip(instances.items(), source_seeds):
        orders, meta = inputs.instance(name)
        if not orders.weight_kg.le(inputs.cost["trip_max_kg"]).all():
            raise ValueError(f"{name}: ④ BM 입력 계약 밖인 외부 퀵 주문이 있습니다.")
        location_seed, sample_seed, travel_seed = master.spawn(3)
        site = sites.loc[meta["hub_site_id"]]
        locations = make_locations(site, boundaries, dongs, inputs.travel, settings, radial, location_seed)
        for radius, draw_seed, run_seed in zip(spec["radius_min"], sample_seed.spawn(len(spec["radius_min"])),
                                               travel_seed.spawn(len(spec["radius_min"]))):
            radius_km, pool = locations[radius]
            sample = pool.iloc[np.random.default_rng(draw_seed).integers(len(pool), size=len(orders))].reset_index(drop=True)
            changed = orders.copy()
            changed[LOCATION] = sample[LOCATION]
            pd.testing.assert_frame_equal(orders.drop(columns=LOCATION), changed.drop(columns=LOCATION), check_exact=True)
            if len(changed) != meta["n_orders"] or not (sample.road_km.le(radius_km + EPS).all()
                                                        and sample.peak_min.le(radius + EPS).all()):
                raise ValueError(f"{name} {radius}분: 주문수 또는 위치 반경 검산 실패")
            base = dict(instance=name, day_type=spec["day_type"], radius_min=radius, radius_km=radius_km,
                        orders=len(orders))
            pools[name, radius] = (base, changed, meta, run_seed, sample)
    return pools


def stochastic_day(engine, plan, seed, base, repetition, correction, deadline):
    rng = np.random.default_rng(seed)
    result = engine.simulate(plan, deadline_clock=deadline, allow_incomplete=True,
                             travel_multiplier=lambda: TravelTime.stochastic_multiplier(rng))
    kpi = result.kpi
    delivery = result.t3.merge(engine.orders[["order_id", "order_min"]], on="order_id", validate="one_to_one")
    duration = delivery.delivered_min - delivery.order_min
    n = base["orders"]
    if len(delivery) + kpi["undelivered_orders"] != n or not duration.ge(0).all():
        raise ValueError(f"{base['instance']}: 배송·미배송 합 또는 배송시간 오류")
    sla = engine.settings["sla_min"]
    ontime = int(duration.le(sla + EPS).sum())
    corrected = int((duration + correction).le(sla + EPS).sum())
    if n - ontime != kpi["late_orders"] + kpi["undelivered_orders"] or corrected > ontime:
        raise ValueError(f"{base['instance']}: 정시율 재계산 오류")
    return dict(**base, repetition=repetition, delivered=len(delivery), undelivered=kpi["undelivered_orders"],
                ontime_orders=ontime, ontime_corrected_orders=corrected, ontime=ontime / n,
                ontime_corrected=corrected / n, contract_hourly=kpi["rider_hourly"], bay_max=kpi["bay_max"],
                supply_ok=kpi["supply_ok"], rule_violations=kpi["rule_violations"],
                cost_per_order=kpi["cost_per_order"] if len(delivery) == n else np.nan,
                rider_cost_per_order=(kpi["contract_rider_cost_per_order_krw"]
                                      + kpi["employed_rider_cost_per_order_krw"]) if len(delivery) == n else np.nan,
                picker_cost_per_order=kpi["picker_cost_per_order_krw"] if len(delivery) == n else np.nan,
                employed_share=result.t3.rider_type.eq("employed").mean(), orders_per_trip=kpi["orders_per_trip"])


def run_case(config, item, deadline):
    base, orders, meta, seed = item
    inputs = DispatchInputs(config)
    options = config["sim"]["radius_bm"]
    remaining = deadline - perf_counter()
    if remaining <= 0:
        raise SearchDeadline("반경 실험 전체 시간 한도")
    limit = min(options["search_seconds_per_case"], remaining)
    inputs.settings = deepcopy(inputs.settings)
    inputs.settings["search"]["seconds_per_instance"] = limit
    meta = dict(meta, instance=f"{base['instance']}-r{base['radius_min']}")
    engine = DispatchEngine(inputs, orders, meta)
    search_started = perf_counter()
    try:
        search = search_instance(engine)
    except RuntimeError as exc:
        if str(exc) != "완료된 조합 없음: 입력/근무 horizon 확인 필요":
            raise
        elapsed = perf_counter() - search_started
        stats = dict(**base, search_seconds=elapsed, search_timed_out=elapsed >= limit,
                     evaluated=np.nan, completed_p2_candidates=0, status="no_complete_p2",
                     plan_json="", reason=str(exc))
        return dict(**base, feasible=False), [], stats
    selected = search["policies"].get(options["policy"])
    stats = dict(**base, search_seconds=search["status"]["elapsed_sec"],
                 search_timed_out=search["status"]["timed_out"], evaluated=search["status"]["evaluated"])
    p2 = search["log"].loc[search["log"].policy.eq(options["policy"])]
    stats["completed_p2_candidates"] = int(p2.status.eq("complete").sum())
    if selected is None:
        stats.update(status="no_complete_p2", plan_json="")
        return dict(**base, feasible=False), [], stats
    stats.update(status="evaluated", plan_json=json.dumps(selected.plan, ensure_ascii=False, sort_keys=True),
                 deterministic_feasible=selected.kpi["feasible"], deterministic_late=selected.kpi["late_orders"],
                 deterministic_rule_violations=selected.kpi["rule_violations"])
    if len(selected.t3) != base["orders"] or selected.t3.order_id.duplicated().any():
        raise ValueError(f"{meta['instance']}: 결정론적 P2 주문 보존 오류")
    correction = config["sim"]["experiment"]["correction_mean_min"]
    runs = [stochastic_day(engine, selected.plan, repeat_seed, base, i, correction, deadline)
            for i, repeat_seed in enumerate(seed.spawn(config["sim"]["repetitions"]), 1)]
    observed = pd.DataFrame(runs)
    row = dict(**base, **deterministic_row(selected))
    row.update(ontime_stoch_mean=observed.ontime.mean(), ontime_stoch_p05=observed.ontime.quantile(.05),
               ontime_stoch_corrected=observed.ontime_corrected.mean(), undelivered=int(observed.undelivered.sum()),
               bay_max=int(observed.bay_max.max()), supply_ok=bool(observed.supply_ok.all()))
    row["feasible"] = bool(row["supply_ok"] and row["undelivered"] == 0 and observed.rule_violations.eq(0).all()
                           and row["ontime_stoch_corrected"] >= config["sim"]["ontime_target"])
    return row, runs, stats


def radius_basis(config, result, reps, plans):
    options = config["sim"]["radius_bm"]
    name = options["decision_instance"]
    threshold = config["sim"]["ontime_target"]
    rain = result.loc[result.instance.eq(name) & result.radius_min.notna()].copy()
    passed = rain.loc[rain.feasible, "radius_min"]
    maximum = passed.max() if len(passed) else np.nan
    evidence = []
    for row in rain.itertuples(index=False):
        days = reps.loc[reps.instance.eq(name) & reps.radius_min.eq(row.radius_min)]
        plan = plans.loc[plans.instance.eq(name) & plans.radius_min.eq(row.radius_min)].iloc[0]
        reasons = []
        if days.empty:
            reasons.append("완료된 결정론적 P2 인력안 없음")
        else:
            if row.ontime_stoch_corrected < threshold:
                reasons.append("보정 정시율 미달")
            if row.undelivered:
                reasons.append("미배송 발생")
            if not row.supply_ok:
                reasons.append("위탁 수급 미달")
            if row.bay_max > plan.bike_bays:
                reasons.append("대기 공간 초과")
            if days.rule_violations.gt(0).any() and row.bay_max <= plan.bike_bays:
                reasons.append("운영 제약 위반")
        evidence.append(dict(instance=name, day_type=row.day_type, radius_min=row.radius_min, radius_km=row.radius_km,
                             selected=bool(row.radius_min == maximum), max_service_radius_min=maximum,
                             feasible=row.feasible, ontime_target=threshold, ontime_stoch_corrected=row.ontime_stoch_corrected,
                             undelivered=row.undelivered, bay_max=row.bay_max, bike_bays=plan.bike_bays,
                             supply_ok=row.supply_ok, min_contract_hourly_krw=options["min_contract_hourly_krw"],
                             contract_hourly_stoch_min=days.contract_hourly.min(), repetitions=len(days),
                             rider_cost_per_order=row.rider_cost_per_order,
                             reason="; ".join(reasons) or "평일 비의 운영 기준 충족"))
    return pd.DataFrame(evidence)


def verification_rows(result, reps, checks, config):
    cases = result.loc[result.radius_min.notna()]
    for row in cases.itertuples(index=False):
        group = reps.loc[reps.instance.eq(row.instance) & reps.radius_min.eq(row.radius_min)]
        if group.empty:
            checks.append(dict(check="확률 반복 완료", instance=row.instance, radius_min=row.radius_min,
                               passed=False, detail="평가 가능한 P2 계획 없음"))
            continue
        matches = len(group) == config["sim"]["repetitions"] and group.repetition.is_unique
        matches &= group.delivered.add(group.undelivered).eq(row.orders).all()
        matches &= int(group.undelivered.sum()) == row.undelivered
        matches &= np.allclose(
            [group.ontime.mean(), group.ontime.quantile(.05), group.ontime_corrected.mean()],
            [row.ontime_stoch_mean, row.ontime_stoch_p05, row.ontime_stoch_corrected], atol=1e-12, rtol=0)
        checks.append(dict(check="확률 반복·주문 보존·정시율 재집계", instance=row.instance, radius_min=row.radius_min,
                           passed=bool(matches), detail=f"{len(group)}회"))
        if not matches:
            raise ValueError(f"{row.instance} {row.radius_min}분: 확률 결과 재집계 불일치")
    tol = config["sim"]["radius_bm"]["cost_tolerance_krw"]
    for name, group in cases.groupby("instance"):
        records = list(group.sort_values("radius_min").itertuples(index=False))
        for before, after in zip(records, records[1:]):
            for metric in ("cost_per_order", "rider_cost_per_order"):
                previous, current = getattr(before, metric), getattr(after, metric)
                difference = current - previous
                checks.append(dict(check="반경 확대 비용 비감소", instance=name, radius_min=after.radius_min,
                                   radius_from_min=before.radius_min, metric=metric,
                                   value_from=previous, value_to=current, difference_krw=difference,
                                   passed=bool(difference >= -tol) if pd.notna(difference) else None,
                                   detail="같은 BM의 인접 반경, 비용 기준 2027년 원/건"))
    return pd.DataFrame(checks)


def run(config):
    started = perf_counter()
    sim, options = config["sim"], config["sim"]["radius_bm"]
    deadline = started + options["runtime_limit_seconds"]
    output = ROOT / config["paths"]["outputs"]
    protected = protected_hashes(output)
    inputs = DispatchInputs(config)
    if inputs.settings["min_contract_hourly_krw"] != options["min_contract_hourly_krw"]:
        raise ValueError("④ 수급 기준과 반경 실험 수급 기준이 일치해야 합니다.")
    selection = json.loads((output / "dispatch/selection.json").read_text(encoding=ENCODING))
    rows, checks = baseline_rows(config, inputs, output, selection, inputs.settings["verification_tolerance"])
    print("원위치 기준 BM1~3의 T3·인력·KPI 일치", flush=True)
    jobs = case_inputs(config, inputs)
    if len(jobs) != sum(len(spec["radius_min"]) for spec in options["instances"].values()):
        raise ValueError("반경 실행 조합 누락")
    workers = min(options["workers"], len(jobs), os.cpu_count() or 1)
    print(f"반경 {len(jobs)}조합·{workers}병렬, 조합별 탐색 {options['search_seconds_per_case']}초", flush=True)
    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(run_case, config, item[:4], deadline): key for key, item in jobs.items()}
        days, search_rows = [], []
        for future in as_completed(futures, timeout=max(0, deadline - perf_counter())):
            key = futures[future]
            row, runs, stats = future.result()
            sample = jobs[key][-1]
            stats.update(bike_bays=jobs[key][2]["bike_bays"], max_road_km=sample.road_km.max(),
                         max_peak_min=sample.peak_min.max())
            rows.append(row)
            days.extend(runs)
            search_rows.append(stats)
            print(f"{key[0]} {key[1]}분 탐색 종료 · P2 완료 {stats['completed_p2_candidates']}안 · {perf_counter() - started:.1f}초",
                  flush=True)
    except BaseException:
        executor.terminate_workers()
        raise
    else:
        executor.shutdown()
    result = pd.DataFrame(rows).reindex(columns=COLUMNS).sort_values(["instance", "radius_min"], na_position="first")
    plans = pd.DataFrame(search_rows).sort_values(["instance", "radius_min"])
    reps = pd.DataFrame(days, columns=["instance", "day_type", "radius_min", "radius_km", "orders", "repetition",
                                     "delivered", "undelivered", "ontime_orders", "ontime_corrected_orders", "ontime",
                                     "ontime_corrected", "contract_hourly", "bay_max", "supply_ok", "rule_violations"])
    reps = reps.sort_values(["instance", "radius_min", "repetition"])
    basis = radius_basis(config, result, reps, plans)
    if protected_hashes(output) != protected:
        raise ValueError("④ 파일 또는 원본 파일의 해시가 실행 중 변경되었습니다.")
    checks.append(dict(check="④ 파일 및 원본 보존", passed=True, detail=f"{len(protected)}개 SHA256 일치"))
    for row in plans.itertuples(index=False):
        if row.max_road_km > row.radius_km + EPS or row.max_peak_min > row.radius_min + EPS:
            raise ValueError("권역 반경 검산 실패")
    checks.append(dict(check="위치 외 주문 필드 및 권역 상한", passed=True, detail=f"{len(plans)}조합"))
    verified = verification_rows(result, reps, checks, config)
    if perf_counter() >= deadline:
        raise SearchDeadline("반경 실험 전체 시간 한도")
    files = {"radius_bm_check": result, "service_radius_basis": basis, "radius_bm_repetitions": reps,
             "radius_bm_plans": plans, "radius_bm_verification": verified}
    for name, frame in files.items():
        frame.to_csv(output / f"{name}.csv", index=False, encoding=ENCODING)
    saved = pd.read_csv(output / "radius_bm_check.csv", encoding=ENCODING)
    pd.testing.assert_frame_equal(saved, result.reset_index(drop=True), check_exact=False, check_dtype=False)
    saved_basis = pd.read_csv(output / "service_radius_basis.csv", encoding=ENCODING)
    pd.testing.assert_frame_equal(saved_basis, basis, check_exact=False, check_dtype=False)
    elapsed = perf_counter() - started
    pd.DataFrame([dict(execution_seconds=elapsed, runtime_limit_seconds=options["runtime_limit_seconds"],
                       cases=len(jobs), baseline_rows=len(selection), stochastic_repetitions=len(reps),
                       workers=workers, seed=sim["seed"])]).to_csv(
        output / "radius_bm_runtime.csv", index=False, encoding=ENCODING)
    print(result.to_string(index=False), flush=True)
    print(basis.to_string(index=False), flush=True)
    print(f"전체 실행 {elapsed:.2f}초", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    run(load_config(parser.parse_args().config))


if __name__ == "__main__":
    main()
