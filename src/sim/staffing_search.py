"""생성 주문의 시간대별 부하로 시작해 위탁·직고용 근무를 조정한다."""

from collections import deque
from copy import deepcopy
import math

import numpy as np

from src.dispatch.engine import EPS, max_overlap


def staffing_diagnostics(engine, result, duration, late):
    """지연 주문의 피킹 완료 시각과 시간대별 위탁 지급·대기 공간."""
    close = math.ceil(engine.settings["last_order_min"] / 60)
    picked = result.picking.sort_values("order_index")
    internal = engine.weight <= engine.cost["trip_max_kg"]
    hours = np.clip(picked.pick_end_min.to_numpy().astype(int) // 60, 0, close)
    missing = np.isnan(duration)
    delay = np.maximum(np.nan_to_num(duration) - engine.settings["sla_min"], 0)
    pressure = np.bincount(hours, weights=((1 + delay / 60) * late + 4 * missing) * internal, minlength=close + 1)
    pay = result.trips.assign(hour=(result.trips.depart_min // 60).astype(int)).groupby("hour").pay_krw.sum()
    bays = list(zip(result.bays.start_min, result.bays.end_min))
    peaks = {h: max_overlap((max(a, h * 60), min(b, (h + 1) * 60)) for a, b in bays)
             for h in range(engine.settings["open_hour"], close + 1)}
    return dict(rider_pressure=pressure.tolist(), pay_by_hour=pay.to_dict(), bay_by_hour=peaks)


def starting_plans(inputs, meta, policy, n_orders, hourly, attributes, locations, experiment):
    settings, cost = inputs.settings, inputs.cost
    first = settings["open_hour"]
    last = settings["last_order_min"]
    close = math.ceil(last / 60)
    hours = np.arange(first, close)
    expected_orders = n_orders * hourly.reindex(hours, fill_value=0).to_numpy(float)
    pick_minutes = (cost["pick_time_base_min"] + cost["pick_time_per_item_min"]
                    * attributes["items"].clip(upper=inputs.config["sim"]["pick_items_cap"]).mean())
    minimum = int(cost["picker_min_per_open_hour"])
    pick_load = expected_orders * pick_minutes
    pick = np.clip(np.ceil(pick_load * experiment["picker_buffer"] / 60), minimum, settings["max_pickers"])
    internal_share = attributes.weight_kg.le(cost["trip_max_kg"]).mean()
    order_rate = expected_orders * internal_share / np.minimum(last - hours * 60, 60)
    waits = np.array([policy["wait_by_hour"].get(str(h), policy["wait"]) for h in hours])
    orders_per_trip = np.clip(1 + waits * order_rate / 2, 1, policy["target"])
    trips = expected_orders * internal_share / orders_per_trip
    origin = meta["hub_gu"]
    group_distance = locations.groupby("gu").road_km.sum() / len(locations)
    one_way = np.array([sum(distance / inputs.travel.speed(h, origin, gu, meta["weather"])
                            for gu, distance in group_distance.items()) * 60 for h in hours])
    handover = np.where(attributes.building_type.eq("아파트"), cost["handover_apt_min"],
                        cost["handover_other_min"]).mean()
    between = settings["staffing_intercustomer_min"] * (orders_per_trip - 1)
    trip_minutes = cost["load_time_min"] + 2 * one_way + between + orders_per_trip * handover
    demand = np.ceil(trips * trip_minutes / 60).astype(int)
    cap = np.array([inputs.cap(meta, int(h)) for h in hours])
    distance = locations.road_km.mean() + between * np.divide(
        locations.road_km.mean(), one_way, out=np.zeros_like(one_way), where=one_way > 0)
    trip_pay = (cost["contract_base_krw"] + cost["contract_extra_order_krw"] * (orders_per_trip - 1)
                + cost["contract_km_rate_krw"] * np.maximum(0, distance - cost["contract_km_free"]))
    trip_pay += np.where((hours >= 18) & (hours < 21), orders_per_trip * cost["contract_peak_surcharge_krw"], 0)
    if meta["weather"] == "rain":
        trip_pay += orders_per_trip * cost["contract_weather_surcharge_rain_krw"]
    expected_pay = trips * trip_pay
    budget = np.maximum(1, np.floor(expected_pay / settings["min_contract_hourly_krw"])).astype(int)
    tail = range(close, settings["planning_end_hour"])
    pickers = dict(zip(map(str, hours), map(int, pick)))
    pickers.update({str(h): min(settings["max_pickers"], settings["tail_pickers"]) for h in tail})
    policy_fields = {key: policy[key] for key in ("policy", "wait", "wait_by_hour", "target", "margin")}
    plans = []
    for staffing in (np.minimum(demand, budget), demand, np.ceil(demand * 1.15), budget, cap):
        contract = dict(zip(map(str, hours), map(int, np.clip(staffing, 1, cap))))
        contract.update({str(h): min(inputs.cap(meta, h), settings["tail_pickers"]) for h in tail})
        plans.append(dict(policy_fields, pickers=pickers, contract=contract, employed_blocks=[]))
    workload = [dict(hour=int(h), expected_orders=float(n), expected_pick_minutes=float(load),
                     expected_trips=float(t), expected_contract_pay=float(pay), initial_pickers=int(k),
                     initial_contract=plans[0]["contract"][str(h)])
                for h, n, load, t, pay, k in zip(hours, expected_orders, pick_load, trips, expected_pay, pick)]
    return plans, close, workload


def plan_score(metrics, target, min_income):
    if not metrics:
        return (2, math.inf, math.inf, math.inf, math.inf, math.inf)
    constraints = max(m["rule_violations"] for m in metrics)
    income = min(m["contract_hourly_income"] if m["contract_hours"] else math.inf for m in metrics)
    shortfall = max(0., (min_income - income) / min_income)
    service = float(np.mean([m["search_ontime"] for m in metrics]))
    gap = max(0., target - service)
    missing = sum(m["undelivered_orders"] for m in metrics)
    return (int(constraints > 0 or shortfall > EPS or missing > 0), missing, gap,
            constraints + shortfall,
            float(np.mean([m["employed_hours"] for m in metrics])),
            float(np.mean([m["cost_per_order"] for m in metrics])))


def plan_key(plan):
    return (tuple(plan["pickers"].values()), tuple(plan["contract"].values()),
            tuple(sorted((b["start_hour"], b["hours"], b["count"]) for b in plan["employed_blocks"])))


def estimate_cut(plan, metrics, threshold, first, close, *, buffer=1.):
    """낮은 위탁 수입 또는 대기 공간 초과의 시간대에서 위탁을 우선 줄인다."""
    new = deepcopy(plan)
    counts = new["contract"]
    if not metrics:
        return new
    if any(m["undelivered_orders"] for m in metrics):
        # 일부 배송에 대한 지급액으로 종일 인원을 추정하면 인력을 과하게 줄인다.
        return new
    pay = min(m["contract_pay_krw"] for m in metrics)
    actual = max(m["contract_hours"] for m in metrics)
    target = math.floor(pay / (threshold * buffer))
    to_cut = max(0, math.ceil(actual - target))
    used = {h: max(m["bay_by_hour"].get(h, 0) for m in metrics) for h in range(first, close + 1)}
    bay = metrics[0]["bike_bays"]
    for h, maximum in used.items():
        cut = min(max(0, counts[str(h)] - 1), max(0, maximum - bay))
        counts[str(h)] -= cut
        to_cut = max(0, to_cut - cut)
    pay_by_hour = {h: float(np.mean([m["pay_by_hour"].get(h, 0) for m in metrics])) for h in range(first, close)}
    while to_cut:
        available = [h for h in range(first, close) if counts[str(h)] > 1]
        if not available:
            break
        hour = min(available, key=lambda h: (pay_by_hour[h] / counts[str(h)], h))
        counts[str(hour)] -= 1
        to_cut -= 1
    return new


def targeted_blocks(plan, metrics, first, close, block):
    """지연이 관측된 4시간 구간에 한해 직고용 후보를 만든다."""
    if not metrics:
        return []
    pressure = np.mean([m["rider_pressure"] for m in metrics], axis=0)
    starts = sorted(range(first, close + 1),
                    key=lambda h: (-float(pressure[h:h + block].sum()), h))
    starts = [h for h in starts if pressure[h:h + block].sum() > 0]
    variants = []
    for start in starts[:3]:
        for count in (1, 2):
            blocks = deepcopy(plan["employed_blocks"])
            matching = next((b for b in blocks if b["start_hour"] == start and b["hours"] == block), None)
            if matching is None:
                blocks.append(dict(start_hour=start, hours=block, count=count))
            else:
                matching["count"] += count
            variants.append(dict(plan, employed_blocks=blocks))
    return variants


def trim_low_yield(plan, metrics, first, close):
    counts = plan["contract"]
    pressure = np.mean([m["rider_pressure"] for m in metrics], axis=0)
    pay = {h: float(np.mean([m["pay_by_hour"].get(h, 0) for m in metrics])) for h in range(first, close)}
    hours = [h for h in range(first, close) if counts[str(h)] > 1]
    if not hours:
        return plan
    weakest = sorted(hours, key=lambda h: (pressure[h] > 0, pay[h] / counts[str(h)], h))
    return dict(plan, contract={h: count - int(int(h) in weakest[:2]) for h, count in counts.items()})


def neighboring_plans(plan, metrics, first, close, maximum, caps):
    """시간대별 인력 대안을 만든다. 피킹과 위탁을 각각 한 명씩 조정한다."""
    hours = list(range(first, close))
    if metrics:
        pressure = np.mean([m["rider_pressure"] for m in metrics], axis=0)
        hours.sort(key=lambda h: (-pressure[h], h))
    for h in hours:
        key = str(h)
        for name, bound, change in (("pickers", maximum, 1), ("contract", caps[key], 1), ("contract", 1, -1)):
            count = plan[name][key] + change
            if (change > 0 and count > bound) or (change < 0 and count < bound):
                continue
            yield dict(plan, **{name: dict(plan[name], **{key: count})})


def search_staffing(inputs, meta, policy, n_orders, hourly, attributes, locations, settings, target, evaluate):
    """서로 다른 인력안을 평가하고, 순수 위탁을 먼저 개선한다."""
    options = settings["staffing_search"]
    first = inputs.settings["open_hour"]
    threshold = inputs.settings["min_contract_hourly_krw"]
    block = int(inputs.cost["employed_shift_block_h"])
    seeds, close, workload = starting_plans(inputs, meta, policy, n_orders, hourly, attributes, locations, settings)
    anchor, ceiling = seeds[0], seeds[-1]
    queue = deque(seeds)
    seen, history = set(), []
    best = None
    while len(history) < options["max_trials"]:
        if not queue:
            base = best[1] if best else anchor
            metrics = best[2] if best else []
            queue.extend(p for p in neighboring_plans(
                base, metrics, first, close, inputs.settings["max_pickers"], ceiling["contract"]) if plan_key(p) not in seen)
            if not queue:
                break
        plan = queue.popleft()
        key = plan_key(plan)
        if key in seen:
            continue
        seen.add(key)
        metrics = evaluate(plan)
        score = plan_score(metrics, target, threshold)
        history.append((plan, metrics, score))
        if best is None or score < best[0]:
            best = score, plan, metrics
        if metrics and plan is best[1]:
            cut = estimate_cut(plan, metrics, threshold, first, close, buffer=options["supply_buffer"])
            if plan_key(cut) == key and score[0] and any(not m["supply_ok"] for m in metrics):
                cut = trim_low_yield(plan, metrics, first, close)
            if score[2] > EPS:
                # 위탁 감축 후에도 부족한 구간을 확인하고 필요 시 직고용을 더한다.
                base = cut if plan_key(cut) != key else plan
                variants = targeted_blocks(base, metrics, first, close, block)
                queue.extend(variants)
            if score[0] and plan_key(cut) != key:
                queue.append(cut)
        if len(history) >= options["min_trials"] and best[0][0] == 0 and best[0][2] <= EPS:
            break
    if len(history) < options["min_trials"]:
        raise ValueError(f"서로 다른 인력 탐색 {len(history)}회, 최소 {options['min_trials']}회 미충족")
    return best[1], history, workload
