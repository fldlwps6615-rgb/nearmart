"""④ P2와 벤치마크 주문을 사용해 확률 이동에 견디는 인력 계획을 탐색한다."""

import argparse
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from copy import deepcopy
from itertools import product
import json
import math
import os
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.dispatch import DispatchEngine, DispatchInputs
from src.dispatch.engine import EPS, SearchDeadline
from src.dispatch.inputs import Geometry
from src.dispatch.search import make_plan
from .experiment_inputs import make_locations, pilot_distributions
from .radius_bm import COLUMNS, ENCODING, LOCATION, protected_hashes, stochastic_day


KEY = ["instance", "order_level", "radius_type"]
PRICE = ["cost_per_order", "rider_cost_per_order", "picker_cost_per_order"]


def make_jobs(config, inputs, names, output):
    sim = config["sim"]
    spec = sim["radius_bm_v2"]
    radii = spec["radius_min"]
    options = dict(sim["experiment"], radius_min=radii)
    dongs = load_file("01_dong_master.csv", config=config).set_index("adm_cd2")
    boundaries = load_file("02_dong_boundary.geojson", config=config)
    _, radial, _ = pilot_distributions(config, inputs, dongs)
    sites = inputs.sites.set_index("site_id", drop=False)
    jobs = []
    for name in names:
        original, meta = inputs.instance(name)
        if len(original) != meta["n_orders"] or original.weight_kg.gt(inputs.cost["trip_max_kg"]).any():
            raise ValueError(f"{name}: 원주문 수 또는 트립 중량 계약 불일치")
        plan_path = output / "dispatch" / name / sim["radius_bm"]["policy"] / "plan.json"
        reference = json.loads(plan_path.read_text(encoding=ENCODING))
        master = np.random.SeedSequence(sim["seed"], spawn_key=(int(name.removeprefix("BM")), 2))
        location_seed, draw_seed, subset_seed, evaluation_seed = master.spawn(4)
        pools = make_locations(sites.loc[meta["hub_site_id"]], boundaries, dongs, inputs.travel,
                               options, radial, location_seed)
        # 동일 BM의 주문량과 반경 비교에 공통 표본과 난수열을 사용한다.
        permutation = np.random.default_rng(subset_seed).permutation(len(original))
        variants = [(None, meta["service_radius_road_km"], original)]
        for radius, seed in zip(radii, draw_seed.spawn(len(radii))):
            km, pool = pools[radius]
            locations = pool.iloc[np.random.default_rng(seed).integers(len(pool), size=len(original))].reset_index(drop=True)
            if not locations.road_km.le(km + EPS).all() or not locations.peak_min.le(radius + EPS).all():
                raise ValueError(f"{name} {radius}분: 위치 표본 권역 초과")
            orders = original.copy()
            orders[LOCATION] = locations[LOCATION]
            pd.testing.assert_frame_equal(orders.drop(columns=LOCATION), original.drop(columns=LOCATION), check_exact=True)
            variants.append((radius, km, orders))
        seeds = evaluation_seed.generate_state(spec["training_repetitions"] + sim["repetitions"], dtype=np.uint64)
        if len(np.unique(seeds)) != len(seeds):
            raise ValueError("탐색·검증 시드 중복")
        for level, (radius, km, orders) in product(spec["order_levels"], variants):
            n = round(len(original) * level)
            sample = orders.iloc[permutation[:n]].sort_values(["order_min", "order_id"]).reset_index(drop=True)
            base = dict(instance=name, day_type=sim["radius_bm"]["instances"][name]["day_type"],
                        radius_type="original" if radius is None else str(radius), radius_min=radius, radius_km=km,
                        order_level=level, orders=n, version="v2")
            jobs.append((base, sample, meta, reference, seeds))
    return jobs


def signature(plan):
    keys = ("policy", "wait", "target", "margin", "wait_by_hour", "pickers", "contract", "employed_blocks")
    return json.dumps({key: plan[key] for key in keys}, sort_keys=True, ensure_ascii=False)


def scaled_plan(engine, reference, ratio):
    plan = deepcopy(reference)
    for field in ("pickers", "contract"):
        for h, count in plan[field].items():
            cap = (engine.settings["max_pickers"] if field == "pickers" else engine.inputs.cap(engine.meta, int(h)))
            plan[field][h] = min(cap, math.ceil(count * ratio))
    for block in plan["employed_blocks"]:
        block["count"] = math.ceil(block["count"] * ratio)
    return plan


def initial_plans(engine, reference, ratio, original_km, radius_km):
    anchor = scaled_plan(engine, reference, ratio)
    yield "scaled_original", anchor
    first = engine.settings["open_hour"]
    end = math.ceil(engine.settings["last_order_min"] / 60)
    # 시작 후보는 ④ 정책의 대기·묶음·여유분과 시간대별 부하 산식을 사용한다.
    block_hours = int(engine.cost["employed_shift_block_h"])
    block_patterns = [
        anchor["employed_blocks"],
        [dict(start_hour=10, hours=2 * block_hours, count=2),
         dict(start_hour=17, hours=2 * block_hours, count=3)],
        [dict(start_hour=10, hours=2 * block_hours, count=4),
         dict(start_hour=17, hours=2 * block_hours, count=5)],
        [dict(start_hour=9, hours=2 * block_hours, count=5),
         dict(start_hour=16, hours=2 * block_hours, count=6),
         dict(start_hour=21, hours=block_hours, count=3)],
        [dict(start_hour=9, hours=2 * block_hours, count=8),
         dict(start_hour=16, hours=2 * block_hours, count=8),
         dict(start_hour=21, hours=block_hours, count=4)],
        [],
    ]
    for extra, new_wait, margin in product((0, 1), (reference["wait"], 8, 12), (4,)):
        plan = deepcopy(anchor)
        plan["wait"], plan["margin"] = new_wait, margin
        for h in range(first, end):
            key = str(h)
            plan["pickers"][key] = min(engine.settings["max_pickers"], plan["pickers"][key] + extra)
        yield "original_neighbor", plan
    patterns = sorted(enumerate(block_patterns), key=lambda pair: abs(pair[0] - (2 if radius_km > original_km else 1)))
    for (index, blocks), wait, factor in product(patterns, (8, 12, 4), (1.45, 1.8)):
        plan = make_plan(engine, "P2", wait, 5, 4, 1.2, factor, blocks, 3)
        yield f"workload_{index}", plan


def refinements(plan, training, engine, feasible):
    """가용 라이더·피커·교대를 조정하며 현재 계획의 주변을 탐색한다."""
    first = engine.settings["open_hour"]
    last = math.ceil(engine.settings["last_order_min"] / 60)
    fields = ("contract", "pickers")
    if not feasible:
        for wait, margin in ((8, 6), (12, 5), (6, 5), (10, 3)):
            yield dict(plan, wait=wait, margin=margin)
        for delta in (1, 2, -1):
            candidate = deepcopy(plan)
            candidate["employed_blocks"] = [dict(b, count=max(1, b["count"] + delta)) for b in plan["employed_blocks"]]
            yield candidate
        # 대기 공간과 수급 여유가 작은 경우 위탁 접속을 시간대별로 한 명 줄인다.
        if any(not run["supply_ok"] or run["rule_violations"] for run in training):
            yield dict(plan, contract={h: max(1, n - 1) if first <= int(h) < last and n else n
                                       for h, n in plan["contract"].items()})
    for h in range(first, last):
        for field in fields:
            key = str(h)
            lower = 1 if field == "pickers" else 0
            upper = engine.settings["max_pickers"] if field == "pickers" else engine.inputs.cap(engine.meta, h)
            delta = -1 if feasible else 1
            value = plan[field][key] + delta
            if lower <= value <= upper:
                yield dict(plan, **{field: dict(plan[field], **{key: value})})


def qualifies(runs, target, bays, expected):
    return len(runs) == expected and all(
        day["ontime_corrected"] >= target - EPS and day["undelivered"] == 0 and day["supply_ok"]
        and day["bay_max"] <= bays and day["rule_violations"] == 0
        for day in runs
    )


def run_case(config, job, deadline):
    started = perf_counter()
    base, orders, meta, reference, seeds = job
    sim = config["sim"]
    spec = sim["radius_bm_v2"]
    n_train, n_verify = spec["training_repetitions"], sim["repetitions"]
    cutoff = min(started + spec["search_seconds_per_case"], deadline - 30)
    engine = DispatchEngine(DispatchInputs(config), orders, meta)
    target, bays = sim["ontime_target"], meta["bike_bays"]
    correction = sim["experiment"]["correction_mean_min"]
    train_seeds, verify_seeds = seeds[:n_train], seeds[n_train:]
    seen, candidates, best, closest = set(), [], None, None

    def evaluate(label, plan):
        nonlocal best, closest
        key = signature(plan)
        if key in seen:
            return False
        if perf_counter() >= cutoff:
            raise SearchDeadline()
        seen.add(key)
        runs = []
        try:
            for i, seed in enumerate(train_seeds, 1):
                runs.append(stochastic_day(engine, plan, int(seed), base, i, correction, cutoff))
        except ValueError as error:
            if str(error) != "계획 종료까지 모든 주문을 피킹할 수 없음":
                raise
        ok = qualifies(runs, target, bays, n_train)
        corrected = min((r["ontime_corrected"] for r in runs), default=np.nan)
        cost = float(np.mean([r["cost_per_order"] for r in runs])) if len(runs) == n_train else np.nan
        number = len(candidates) + 1
        candidates.append(dict(**base, candidate=number, source=label, evaluated_seeds=len(runs), passed=ok,
                               ontime_corrected_min=corrected, cost_mean=cost,
                               undelivered=sum(r["undelivered"] for r in runs),
                               bay_max=max((r["bay_max"] for r in runs), default=np.nan),
                               supply_ok=bool(runs) and all(r["supply_ok"] for r in runs)))
        if ok and (best is None or cost < best[0]):
            best = cost, number, plan, runs
        if len(runs) == n_train:
            rank = (max(0, bays - bays + max(r["bay_max"] for r in runs) - bays),
                    sum(not r["supply_ok"] for r in runs), sum(r["undelivered"] for r in runs), -corrected)
            if closest is None or rank < closest[0]:
                closest = rank, number, plan, runs
        return True

    search_capped = False
    try:
        ratio = len(orders) / meta["n_orders"]
        for label, plan in initial_plans(engine, reference, ratio, meta["service_radius_road_km"], base["radius_km"]):
            evaluate(label, plan)
            if len(candidates) >= spec["initial_candidates"]:
                break
        seed = best or closest
        if seed is not None:
            examined = 0
            for candidate in refinements(seed[2], seed[3], engine, best is not None):
                examined += evaluate("refinement", candidate)
                if examined >= spec["refinement_candidates"]:
                    break
    except SearchDeadline:
        search_capped = True
    selected = best or closest
    row = dict(**base, feasible=False, training_passed=best is not None, validation_repetitions=0,
               candidates=len(candidates), qualifying_candidates=sum(r["passed"] for r in candidates),
               search_capped=search_capped, bike_bays=bays,
               status="no_qualifying_plan" if best is None else "validation_pending")
    plan_row = dict(**base, selected_candidate=None, plan_json="", search_seconds=perf_counter()-started)
    if best is None:
        if selected is not None:
            row["training_ontime_corrected_min"] = min(r["ontime_corrected"] for r in selected[3])
        return row, [], candidates, plan_row
    _, number, plan, training = selected
    plan_row.update(selected_candidate=number, plan_json=signature(plan))
    days = [dict(phase="training", **r) for r in training]
    deterministic = engine.simulate(plan, deadline_clock=deadline, allow_incomplete=True)
    validation = [stochastic_day(engine, plan, int(seed), base, i, correction, deadline)
                  for i, seed in enumerate(verify_seeds, 1)]
    days.extend(dict(phase="validation", **r) for r in validation)
    frame = pd.DataFrame(validation)
    feasible = qualifies(validation, target, bays, n_verify)
    row.update(ontime_det=deterministic.kpi["on_time"] * deterministic.kpi["orders"] / len(orders),
               training_ontime_corrected_min=min(r["ontime_corrected"] for r in training),
               validation_repetitions=len(frame), ontime_stoch_mean=frame.ontime.mean(),
               ontime_stoch_p05=frame.ontime.quantile(.05), ontime_stoch_corrected=frame.ontime_corrected.mean(),
               ontime_stoch_corrected_min=frame.ontime_corrected.min(), undelivered=int(frame.undelivered.sum()),
               bay_max=int(frame.bay_max.max()), supply_ok=bool(frame.supply_ok.all()),
               contract_hourly=frame.contract_hourly.mean(), contract_hourly_min=frame.contract_hourly.min(),
               employed_share=frame.employed_share.mean(), orders_per_trip=frame.orders_per_trip.mean(),
               feasible=feasible, status="passed" if feasible else "validation_failed")
    if frame.undelivered.eq(0).all():
        row.update(frame[PRICE].mean().to_dict())
    for method in (Geometry.road, Geometry.straight, Geometry.minute_at_hour):
        method.cache_clear()
    return row, days, candidates, plan_row


def capacity_basis(rows, target):
    keys = ["instance", "day_type", "radius_type"]
    records = []
    for _, group in rows.groupby(keys, sort=False):
        first = group.iloc[0]
        passed = group.loc[group.feasible]
        winner = passed.sort_values("orders", ascending=False).iloc[0] if len(passed) else None
        row = {k: first[k] for k in [*keys, "radius_min", "radius_km", "version"]}
        row.update(tested_order_levels=len(group), passing_order_levels=len(passed),
                   ontime_target=target, feasible=winner is not None)
        row.update(max_orders_per_day=np.nan, order_level=np.nan, rider_cost_per_order=np.nan, picker_cost_per_order=np.nan)
        if winner is not None:
            row.update(max_orders_per_day=int(winner.orders), order_level=winner.order_level,
                       rider_cost_per_order=winner.rider_cost_per_order, picker_cost_per_order=winner.picker_cost_per_order,
                       ontime_stoch_corrected_min=winner.ontime_stoch_corrected_min, validation_repetitions=winner.validation_repetitions)
        records.append(row)
    return pd.DataFrame(records)


def verify(rows, days, candidates, basis, config):
    sim = config["sim"]
    n_train = sim["radius_bm_v2"]["training_repetitions"]
    validated = 0
    for key, case in rows.set_index(KEY).iterrows():
        subset = candidates.set_index(KEY).loc[[key]]
        assert len(subset) == case.candidates and subset.iloc[0].source == "scaled_original"
        assert subset.passed.sum() == case.qualifying_candidates
        if not case.training_passed:
            assert not case.feasible and case.validation_repetitions == 0
            continue
        evidence = days.set_index(KEY).loc[[key]]
        training = evidence.loc[evidence.phase.eq("training")]
        validation = evidence.loc[evidence.phase.eq("validation")]
        assert training.delivered.add(training.undelivered).eq(case.orders).all()
        assert validation.delivered.add(validation.undelivered).eq(case.orders).all()
        assert qualifies(training.to_dict("records"), sim["ontime_target"], case.bike_bays, n_train)
        assert np.isclose(training.cost_per_order.mean(), subset.loc[subset.passed, "cost_mean"].min())
        assert len(validation) == sim["repetitions"] and validation.repetition.is_unique
        assert bool(case.feasible) == qualifies(validation.to_dict("records"), sim["ontime_target"], case.bike_bays, sim["repetitions"])
        if case.feasible:
            assert np.allclose(case[PRICE].to_numpy(float), validation[PRICE].mean().to_numpy(float))
        validated += 1
    for row in basis.itertuples(index=False):
        matching = rows.loc[rows.instance.eq(row.instance) & rows.radius_type.eq(row.radius_type) & rows.feasible]
        assert (pd.isna(row.max_orders_per_day) if matching.empty else row.max_orders_per_day == matching.orders.max())
    return validated


def save_table(output, name, frame):
    path = output / f"{name}.csv"
    stage = path.with_suffix(".tmp")
    frame.to_csv(stage, index=False, encoding=ENCODING)
    stage.replace(path)


def run(config):
    started = perf_counter()
    sim, spec = config["sim"], config["sim"]["radius_bm_v2"]
    deadline = started + spec["runtime_limit_seconds"] - spec["publish_reserve_seconds"]
    output = ROOT / config["paths"]["outputs"]
    protected = protected_hashes(output)
    inputs = DispatchInputs(config)
    if inputs.settings["min_contract_hourly_krw"] != sim["radius_bm"]["min_contract_hourly_krw"]:
        raise ValueError("④ 수급 기준과 불일치")
    existing = pd.read_csv(output / "radius_bm_check.csv", encoding=ENCODING, dtype={"radius_type": str})
    if "version" not in existing:
        existing["version"] = "v1"
    prior = existing.loc[existing.version.ne("v2")]
    names = spec["required_instances"] + spec["optional_instances"]
    jobs = make_jobs(config, inputs, names, output)
    workers = min(spec["workers"], os.cpu_count() or 1)
    case_rows, day_rows, candidate_rows, plan_rows = [], [], [], []
    incomplete = []
    executor = ProcessPoolExecutor(max_workers=workers)
    timed_out = False
    try:
        for stage in (spec["required_instances"], spec["optional_instances"]):
            batch = [job for job in jobs if job[0]["instance"] in stage]
            if not batch or perf_counter() >= deadline:
                break
            futures = {executor.submit(run_case, config, job, deadline): job[0] for job in batch}
            print(f"{'/'.join(stage)} {len(batch)}개 조합 · {workers}개 프로세스", flush=True)
            try:
                for future in as_completed(futures, timeout=max(0, deadline - perf_counter())):
                    try:
                        row, days, candidates, plan = future.result()
                    except SearchDeadline:
                        incomplete.append(futures[future])
                        continue
                    case_rows.append(row)
                    day_rows.extend(days)
                    candidate_rows.extend(candidates)
                    plan_rows.append(plan)
                    print(f"{row['instance']} {row['order_level']:.0%} {row['radius_type']} · {row['status']} · "
                          f"{row['candidates']}안 · {perf_counter()-started:.1f}초", flush=True)
            except TimeoutError:
                timed_out = True
                incomplete.extend(base for future, base in futures.items() if not future.done())
                break
    except BaseException:
        executor.terminate_workers()
        raise
    else:
        if timed_out:
            executor.terminate_workers()
        else:
            executor.shutdown()
    if not case_rows:
        raise RuntimeError("저장할 완료 조합이 없습니다.")
    if protected_hashes(output) != protected:
        raise ValueError("④ 또는 원본 파일이 변경되었습니다.")
    rows = pd.DataFrame(case_rows).reindex(columns=[*COLUMNS, *[k for k in pd.DataFrame(case_rows) if k not in COLUMNS]])
    rows = rows.sort_values(["instance", "order_level", "radius_min"], ascending=[True, False, True], na_position="first")
    days, candidates, plans = map(pd.DataFrame, (day_rows, candidate_rows, plan_rows))
    basis = capacity_basis(rows, sim["ontime_target"])
    validated = verify(rows, days, candidates, basis, config)
    combined = pd.concat([prior, rows], ignore_index=True)
    save_table(output, "radius_bm_check", combined)
    save_table(output, "service_radius_basis", basis)
    save_table(output, "radius_bm_v2_repetitions", days)
    save_table(output, "radius_bm_v2_candidates", candidates)
    save_table(output, "radius_bm_v2_plans", plans)
    elapsed = perf_counter() - started
    runtime = pd.DataFrame([dict(execution_seconds=elapsed, runtime_limit_seconds=spec["runtime_limit_seconds"],
                                 scheduled_cases=len(jobs), finished_cases=len(rows), trained_cases=validated,
                                 passed_cases=int(rows.feasible.sum()), workers=workers, stopped_at_limit=timed_out or bool(incomplete),
                                 unfinished_cases=len(jobs)-len(rows), protected_hashes=len(protected),
                                 evaluated_candidates=len(candidates))])
    save_table(output, "radius_bm_v2_runtime", runtime)
    reread = pd.read_csv(output / "radius_bm_check.csv", encoding=ENCODING, dtype={"radius_type": str})
    pd.testing.assert_frame_equal(reread.loc[reread.version.ne("v2"), prior.columns].reset_index(drop=True),
                                  prior.reset_index(drop=True), check_dtype=False, check_exact=False)
    print(runtime.to_string(index=False), flush=True)
    print(basis.to_string(index=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    run(load_config(parser.parse_args().config))


if __name__ == "__main__":
    main()
