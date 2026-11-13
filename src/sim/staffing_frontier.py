"""08-3 인력 후보의 비용·지연 평가와 독립 검증."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from itertools import product
import json
import math
from time import monotonic

import numpy as np
import pandas as pd

from src.dispatch import DispatchEngine, DispatchInputs
from src.dispatch.engine import EPS, max_overlap
from src.dispatch.search import make_plan
from src.network.model import read_output, require
from src.sim.radius_bm_v2 import make_jobs, scaled_plan, signature
from src.travel_time import TravelTime


def build_jobs(config, settings, output):
    inputs = DispatchInputs(config)
    options = config["sim"]["radius_bm_v2"]
    names = options["required_instances"] + options["optional_instances"]
    v2 = read_output(output / "radius_bm_v2_plans.csv")
    v2 = v2[v2.plan_json.notna()].copy()
    check = read_output(output / "radius_bm_check.csv").query("version == 'v2'")
    v2 = v2.merge(check[["instance", "radius_type", "orders", "feasible"]], on=["instance", "radius_type", "orders"],
                  validate="one_to_one")
    v2["numeric_radius"] = pd.to_numeric(v2.radius_type, errors="coerce")
    v2 = v2[v2.numeric_radius.isna() | v2.numeric_radius.le(15)]
    for base, orders, meta, deterministic, _ in make_jobs(config, inputs, names, output):
        radius = base["radius_min"]
        if radius not in (12, 15):
            continue
        plans = v2[v2.instance.eq(base["instance"])].assign(
            radius_gap=lambda x: (x.numeric_radius.fillna(0) - radius).abs(),
            order_gap=lambda x: (x.orders - len(orders)).abs())
        plans = plans.sort_values(["radius_gap", "order_gap", "feasible", "orders"], ascending=[True, True, False, False])
        exact = plans[plans.radius_gap.eq(0) & plans.order_gap.eq(0)]
        passing = plans[plans.feasible]
        selected = pd.concat([exact, passing.head(2), plans.head(1)]).drop_duplicates(["radius_type", "orders"])
        robust = [(json.loads(r.plan_json), int(r.orders), f"{r.radius_type}/{r.orders}") for r in selected.itertuples()]
        seeds = np.random.SeedSequence(settings["staffing_seed"], spawn_key=(int(base["instance"][-1]),))
        total = settings["training_repetitions"] + settings["validation_attempts"] * settings["validation_repetitions"]
        seeds = seeds.generate_state(total, dtype=np.uint64)
        require(len(set(seeds)) == len(seeds), "인력 탐색과 검증 시드 중복")
        yield dict(base, version="v4"), orders, meta, deterministic, robust, seeds


def interpolate(engine, deterministic, robust, fraction):
    plan = deepcopy(robust if fraction >= 0.5 else deterministic)
    for field in ("pickers", "contract"):
        left, right = deterministic[field], robust[field]
        plan[field] = {h: round((1-fraction)*left.get(h, 0) + fraction*right.get(h, 0))
                       for h in sorted(left.keys() | right.keys(), key=int)}
    blocks = []
    for parent, weight in ((deterministic, 1-fraction), (robust, fraction)):
        blocks.extend((b["start_hour"], b["hours"], b["count"] * weight) for b in parent["employed_blocks"])
    totals = {}
    for start, length, count in blocks:
        totals[start, length] = totals.get((start, length), 0) + count
    plan["employed_blocks"] = [dict(start_hour=h, hours=n, count=round(c)) for (h, n), c in sorted(totals.items()) if round(c)]
    for field in ("wait", "margin", "target"):
        plan[field] = (1-fraction)*deterministic[field] + fraction*robust[field]
    plan["target"] = round(plan["target"])
    return scaled_plan(engine, plan, 1.)


def day(engine, plan, seed, correction, deadline, diagnostics=False):
    rng = np.random.default_rng(seed)
    result = engine.simulate(plan, deadline_clock=deadline, allow_incomplete=True,
                             travel_multiplier=lambda: TravelTime.stochastic_multiplier(rng))
    kpi = result.kpi
    n = len(engine.orders)
    arrivals = result.t3.order_id.map(engine.orders.set_index("order_id").order_min).to_numpy()
    duration = result.t3.delivered_min.to_numpy() - arrivals
    corrected = int(np.count_nonzero(duration + correction <= engine.settings["sla_min"] + EPS))
    raw = int(np.count_nonzero(duration <= engine.settings["sla_min"] + EPS))
    delivered = len(duration)
    require(delivered + kpi["undelivered_orders"] == n and raw <= delivered and corrected <= raw
            and (duration >= 0).all(), "인력 검증 주문 또는 배송시간 수지 오류")
    row = dict(seed=str(seed), delivered=delivered, undelivered=n-delivered, ontime_orders=raw, ontime_corrected_orders=corrected,
               ontime=raw/n, ontime_corrected=corrected/n, contract_hourly=kpi["rider_hourly"],
               contract_hours=kpi["contract_hours"], bay_max=kpi["bay_max"], supply_ok=kpi["supply_ok"],
               rule_violations=kpi["rule_violations"], contract_unit_krw=kpi["contract_rider_cost_per_order_krw"],
               employed_unit_krw=kpi["employed_rider_cost_per_order_krw"], picker_unit_krw=kpi["picker_cost_per_order_krw"])
    if diagnostics:
        pay = result.trips.assign(hour=(result.trips.depart_min // 60).astype(int)).groupby("hour").pay_krw.sum().to_dict()
        bays = list(zip(result.bays.start_min, result.bays.end_min))
        close = math.ceil(engine.settings["last_order_min"]/60)
        peak = {h: max_overlap((max(a, h*60), min(b, (h+1)*60)) for a, b in bays if a < (h+1)*60 and b > h*60)
                for h in range(engine.settings["open_hour"], close+3)}
        pressure = np.bincount(np.minimum(close, (arrivals // 60).astype(int)),
                               weights=np.maximum(duration + correction - engine.settings["sla_min"], 0), minlength=close+1)
        row.update(pay=pay, bay_by_hour=peak, pressure=pressure)
    return row


def summarize(runs, target, bays, coupon, penalty, contribution):
    worst_income = min((r["contract_hourly"] for r in runs if r["contract_hours"] > 0), default=np.nan)
    on_time = float(np.mean([r["ontime_corrected"] for r in runs]))
    raw = float(np.mean([r["ontime"] for r in runs]))
    missing = sum(r["undelivered"] for r in runs)
    violations = sum(r["rule_violations"] for r in runs)
    supply = all(r["supply_ok"] for r in runs)
    bay = max(r["bay_max"] for r in runs)
    parts = {name: float(np.mean([r[name] for r in runs])) if not missing else np.nan
             for name in ("contract_unit_krw", "employed_unit_krw", "picker_unit_krw")}
    cost = sum(parts.values())
    late = 1-on_time
    lost = penalty*late*contribution
    return dict(repetitions=len(runs), on_time=on_time, on_time_raw=raw, late_rate=late, undelivered=missing,
                failed_supply_runs=sum(not r["supply_ok"] for r in runs), min_contract_hourly_krw=worst_income,
                bay_max=bay, rule_violations=violations, **parts, cost_per_order_krw=cost,
                coupon_per_order_krw=coupon*late, demand_loss_per_order_krw=lost,
                objective_per_order_krw=cost+coupon*late+lost,
                feasible=missing == 0 and violations == 0 and supply and bay <= bays and on_time >= target-EPS)


def initial_candidates(engine, baseline, anchors, settings):
    yield "04_scaled", baseline
    for anchor, label in anchors:
        for fraction in settings["interpolation"]:
            plan = interpolate(engine, baseline, anchor, fraction)
            yield f"interpolation_{label}_{fraction:g}", plan
            if fraction in (0.5, 1):
                yield f"batch5_{label}_{fraction:g}", dict(plan, target=5, wait=12., margin=4., wait_by_hour={})
    # 동일 피킹 부하에서 위탁과 고용 비중을 다르게 두고 배치 주변을 탐색한다.
    for factor, wait, blocks in product((1.15, 1.35, 1.55), (8., 12.),
                                        [[], [dict(start_hour=17, hours=8, count=2)],
                                         [dict(start_hour=10, hours=8, count=2), dict(start_hour=17, hours=8, count=3)]]):
        yield "workload_neighbor", make_plan(engine, "P2", wait, 5, 4, 1.1, factor, blocks, 3)


def neighbors(engine, plan, runs, summary, settings):
    first = engine.settings["open_hour"]
    close = math.ceil(engine.settings["last_order_min"]/60)
    min_income = settings["min_contract_hourly_krw"]
    counts = plan["contract"]
    yield "batching", dict(plan, target=5, wait=10., margin=2., wait_by_hour={})
    yield "batching", dict(plan, target=5, wait=14., margin=4., wait_by_hour={})
    yield "batching", dict(plan, target=5, wait=12., margin=0., wait_by_hour={})
    used = {h: max(r["bay_by_hour"].get(h, 0) for r in runs) for h in range(first, close+2)}
    pressure = np.mean([r["pressure"] for r in runs], axis=0)
    by_yield = sorted(range(first, close), key=lambda h: (
        -max(0, used[h]-engine.meta["bike_bays"]), pressure[h] > 0,
        np.mean([r["pay"].get(h, 0) for r in runs])/max(1, counts[str(h)]), h))
    active = [h for h in by_yield if counts[str(h)] > 1]
    deficit = max((r["contract_hours"] * (1-r["contract_hourly"]/min_income)
                   for r in runs if r["contract_hours"] > 0), default=0)
    for amount in sorted({1, 2, 4, max(1, math.ceil(deficit)), max(2, math.ceil(deficit*1.2))}):
        reduced = counts.copy()
        for h in active[:amount]:
            reduced[str(h)] -= 1
        yield "targeted_contract_trim", dict(plan, contract=reduced)
    for scale in (0.86, 0.93, 1.07):
        changed = {h: (min(engine.inputs.cap(engine.meta, int(h)), max(1, round(n*scale))) if n else 0)
                   for h, n in counts.items()}
        yield "contract_scale", dict(plan, contract=changed)
    for h in sorted(used, key=lambda h: (-used[h], h))[:3]:
        if counts[str(h)] > 0:
            yield "bay_hour_trim", dict(plan, contract=dict(counts, **{str(h): counts[str(h)]-1}))
    busiest = sorted(range(first, close), key=lambda h: (-pressure[h], h))
    peak = sorted(range(first, close), key=lambda h: -np.count_nonzero((engine.arrival//60) == h))
    for h in dict.fromkeys(busiest[:2]+peak[:2]):
        for field in ("pickers", "contract"):
            values = plan[field]
            cap = engine.settings["max_pickers"] if field == "pickers" else engine.inputs.cap(engine.meta, h)
            for delta in (-1, 1):
                if 1 <= values[str(h)] + delta <= cap:
                    yield f"hourly_{field}", dict(plan, **{field: dict(values, **{str(h): values[str(h)]+delta})})
    shift = int(engine.cost["employed_shift_block_h"])
    for h in dict.fromkeys((busiest[0], peak[0], close-shift+1)):
        start = min(h, close-shift+1)
        for n in (1, 2):
            block = dict(start_hour=start, hours=shift, count=n)
            yield "employed_block_add", dict(plan, employed_blocks=plan["employed_blocks"] + [block])
    if summary["feasible"] and plan["employed_blocks"]:
        for index, block in enumerate(plan["employed_blocks"]):
            new = deepcopy(plan["employed_blocks"])
            new[index]["count"] = block["count"] - 1
            yield "employed_block_trim", dict(plan, employed_blocks=[b for b in new if b["count"]])


def robust_neighborhood(engine, plan):
    counts = plan["contract"]
    first = engine.settings["open_hour"]
    close = math.ceil(engine.settings["last_order_min"]/60)
    hours = range(first, close)
    load = {h: np.count_nonzero((engine.arrival // 60) == h) for h in hours}
    quiet = sorted(hours, key=lambda h: load[h]/max(1, counts[str(h)]))
    for n in (1, 2, 3, 5, 7):
        reduced = {h: c-int(int(h) in quiet[:n] and c > 1) for h, c in counts.items()}
        yield "robust_trim", dict(plan, contract=reduced)
    for factor in (0.92, 0.96, 1.04):
        scaled = {h: min(engine.inputs.cap(engine.meta, int(h)), round(c*factor)) for h,c in counts.items()}
        yield "robust_scale", dict(plan, contract=scaled)
    for wait, margin in ((8, 4), (10, 4), (14, 4), (12, 2), (12, 0)):
        yield "robust_batch", dict(plan, target=5, wait=wait, margin=margin, wait_by_hour={})
    yield "robust_picker", dict(plan, pickers={h: min(engine.settings["max_pickers"], c + int(first <= int(h) < close))
                                               for h, c in plan["pickers"].items()})


def search_case(config, settings, job, contribution, deadline):
    base, orders, meta, reference, robust, seeds = job
    inputs = DispatchInputs(config)
    require(inputs.settings["min_contract_hourly_krw"] == settings["min_contract_hourly_krw"], "위탁 시급 기준 불일치")
    engine = DispatchEngine(inputs, orders, meta)
    baseline = scaled_plan(engine, reference, len(orders)/meta["n_orders"])
    anchors = [(scaled_plan(engine, plan, len(orders)/count), label) for plan, count, label in robust]
    target = settings["minimum_mean_ontime"]
    correction = config["sim"]["experiment"]["correction_mean_min"]
    coupon = inputs.cost["late_coupon_krw"]
    penalty = config["network"]["late_demand_penalty"]
    history, seen = [], set()
    count = settings["training_repetitions"]

    def evaluate(source, plan):
        key = signature(plan)
        if key in seen or len(history) >= settings["candidate_budget"]:
            return
        seen.add(key)
        runs = [day(engine, plan, seed, correction, deadline, diagnostics=True) for seed in seeds[:count]]
        summary = summarize(runs, target, meta["bike_bays"], coupon, penalty, contribution)
        history.append((dict(**base, **summary, candidate=len(history)+1, source=source, plan_json=key,
                             bike_bays=meta["bike_bays"], phase="training"), plan, runs))

    def rank(entry):
        row = entry[0]
        objective = row["objective_per_order_krw"]
        return (not row["feasible"], row["undelivered"], row["rule_violations"], row["failed_supply_runs"],
                max(0., target-row["on_time"]), max(0., settings["min_contract_hourly_krw"]-row["min_contract_hourly_krw"]),
                objective if np.isfinite(objective) else np.inf, row["candidate"])

    for source, plan in initial_candidates(engine, baseline, anchors, settings):
        evaluate(source, plan)
    if anchors:
        for index, (source, plan) in enumerate(robust_neighborhood(engine, anchors[0][0])):
            if index >= settings["robust_neighborhood_candidates"]:
                break
            evaluate(source, plan)
    parents = set()
    for _ in range(settings["staffing_rounds"]):
        row, plan, runs = next(item for item in sorted(history, key=rank) if item[0]["candidate"] not in parents)
        parents.add(row["candidate"])
        for source, trial in neighbors(engine, plan, runs, row, settings):
            evaluate(source, trial)
    ranked = sorted(history, key=rank)
    eligible = [item for item in ranked if item[0]["feasible"]]
    attempts, days = [], []
    n = settings["validation_repetitions"]
    for attempt, (winner, plan, _) in enumerate((eligible or ranked[:1])[:settings["validation_attempts"]], 1):
        start = count + (attempt-1)*n
        validation = [day(engine, plan, seed, correction, deadline) for seed in seeds[start:start+n]]
        summary = summarize(validation, target, meta["bike_bays"], coupon, penalty, contribution)
        independent = dict(**base, **summary, candidate=winner["candidate"], source=winner["source"],
                           plan_json=winner["plan_json"], phase="validation", attempt=attempt,
                           bike_bays=meta["bike_bays"], training_feasible=winner["feasible"],
                           certified=winner["feasible"] and summary["feasible"])
        attempts.append(independent)
        days.extend(dict(**base, candidate=winner["candidate"], attempt=attempt, repetition=i, **r)
                    for i, r in enumerate(validation, 1))
        if independent["certified"]:
            break
    training = [dict(**row, selected=row["candidate"] == winner["candidate"]) for row, _, _ in history]
    independent["selected"] = True
    return independent, training, days, attempts


def run_staffing(config, settings, output, contribution, deadline):
    jobs = list(build_jobs(config, settings, output))
    require(len(jobs) == 18, "대표일×반경×주문량 18칸 필요")
    pool = ProcessPoolExecutor(max_workers=settings["staffing_workers"])
    try:
        futures = [pool.submit(search_case, config, settings, job, contribution, deadline) for job in jobs]
        results = []
        for future in as_completed(futures, timeout=max(0, deadline-monotonic())):
            row, training, days, attempts = future.result()
            results.append((row, training, days, attempts))
            print(f"{row['instance']} {row['radius_min']}분 {row['orders']}건: {row['certified']} "
                  f"정시 {row['on_time']:.4f}, 미배송 {row['undelivered']}, 후보 {len(training)}", flush=True)
    except BaseException:
        pool.terminate_workers()
        raise
    finally:
        pool.shutdown()
    results.sort(key=lambda item: (item[0]["instance"], item[0]["radius_min"], -item[0]["orders"]))
    return tuple(pd.DataFrame(rows) for rows in ([item[0] for item in results], [r for item in results for r in item[1]],
                                                [r for item in results for r in item[2]], [r for item in results for r in item[3]]))
