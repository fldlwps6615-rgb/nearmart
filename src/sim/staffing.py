"""반복 실행 전에 조합의 시간대별 인력 계획을 정한다."""

from itertools import product

import numpy as np
from src.dispatch.engine import build_roster


def employed_blocks(required, first, block_hours):
    """시간대별 부족분을 4시간 블록으로 덮고 연속 블록을 8시간으로 합친다."""
    remaining = required.copy()
    blocks = []
    for i, need in enumerate(remaining):
        count = int(max(need, 0))
        if count:
            remaining[i:i + block_hours] -= count
            blocks.append(dict(start_hour=first + i, hours=block_hours, count=count))
    combined = []
    by_start = {block["start_hour"]: block for block in blocks}
    for block in blocks:
        following = by_start.get(block["start_hour"] + block_hours)
        if following:
            count = min(block["count"], following["count"])
            if count:
                combined.append(dict(start_hour=block["start_hour"], hours=2 * block_hours, count=count))
                block["count"] -= count
                following["count"] -= count
        if block["count"]:
            combined.append(block)
    return combined


def candidate_plans(case, policy, hourly, attrs, peak_mean, meta, inputs, settings):
    first = inputs.settings["open_hour"]
    last = inputs.settings["last_order_min"]
    close = (last + 59) // 60
    block_hours = int(inputs.cost["employed_shift_block_h"])
    end = inputs.settings["planning_end_hour"]
    hours = np.arange(first, end)
    fractions = hourly.reindex(hours, fill_value=0).to_numpy(float)
    workload_n = case.orders_per_day * fractions
    effective_minutes = np.minimum(np.maximum(last - hours * 60, 0), 60)
    effective_minutes[effective_minutes == 0] = 60
    cost = inputs.cost
    item_time = cost["pick_time_base_min"] + cost["pick_time_per_item_min"] * attrs["items"].clip(
        upper=inputs.config["sim"]["pick_items_cap"]).mean()
    pickers = np.ceil(workload_n * item_time * settings["picker_buffer"] / effective_minutes).astype(int)
    pickers = np.clip(pickers, int(cost["picker_min_per_open_hour"]), inputs.settings["max_pickers"])
    pickers[hours >= close] = max(1, pickers[close - first - 1] // 2)
    # 저수요 시간에는 P2 대기 창에 유입되는 주문이 적어 묶음 크기도 작다.
    target = np.minimum(policy["target"], 1 + policy["wait"] * workload_n / effective_minutes)
    handover = np.where(attrs.building_type.eq("아파트"), cost["handover_apt_min"],
                        cost["handover_other_min"]).mean()
    day_speed = np.array([inputs.travel.speed(h, meta["hub_gu"], meta["hub_gu"], meta["weather"]) for h in hours])
    peak_speed = inputs.travel.speed(settings["peak_hour"], meta["hub_gu"], meta["hub_gu"])
    ride = peak_mean * peak_speed / day_speed
    effort = handover + (cost["load_time_min"] + 2 * ride
                         + inputs.settings["staffing_intercustomer_min"] * (target - 1)) / target
    rider_load = workload_n * effort / effective_minutes
    caps = np.array([inputs.cap(meta, int(h)) for h in hours])
    bay = meta["bike_bays"]

    seen = set()
    for factor, contractor in product(settings["rider_load_factors"], settings["contract_load_factors"]):
        desired = np.maximum(1, np.ceil(rider_load * factor)).astype(int)
        desired[hours >= close] = max(3, desired[close - first - 1] // 2)
        # 최대 대기면을 맞추기 쉬운 후보와 피크에 운행 중인 인원을 활용하는 후보를 함께 비교한다.
        limit = bay if factor <= 1 else bay + max(1, bay // 4)
        desired = np.minimum(desired, limit)
        contract = np.minimum(caps, np.ceil(desired * contractor).astype(int))
        if contractor > 0:
            expected_pay = cost["contract_base_krw"] + (target - 1) * cost["contract_extra_order_krw"]
            expected_pay += np.where((hours >= 18) & (hours < 21), cost["contract_peak_surcharge_krw"] * target, 0)
            if meta["weather"] == "rain":
                expected_pay += cost["contract_weather_surcharge_rain_krw"] * target
            payable = np.floor(workload_n / target * expected_pay * settings["contract_income_buffer"]
                               / inputs.settings["min_contract_hourly_krw"]).astype(int)
            contract[hours < close] = np.minimum(contract[hours < close], payable[hours < close])
        residual = desired - contract
        blocks = employed_blocks(residual, first, block_hours)
        direct = np.array([sum(b["count"] for b in blocks if b["start_hour"] <= h < b["start_hour"] + b["hours"])
                           for h in hours])
        contract = np.minimum(contract, np.maximum(0, limit - direct))
        # 연장 블록이 전체를 가리고 나면 계획의 남은 시간은 정리용이다.
        signature = (tuple(contract), tuple((b["start_hour"], b["hours"], b["count"]) for b in blocks))
        if signature in seen:
            continue
        seen.add(signature)
        plan = dict(policy, pickers=dict(zip(map(str, hours), map(int, pickers))),
                    contract=dict(zip(map(str, hours), map(int, contract))), employed_blocks=blocks)
        yield plan


def clearance_plan(plan, case, inputs, meta):
    """초과 수요도 배송해 지연을 관측할 수 있게 잔여 처리 인원을 둔다."""
    block = int(inputs.cost["employed_shift_block_h"])
    close = (inputs.settings["last_order_min"] + 59) // 60
    end = inputs.settings["planning_end_hour"]
    start = max(inputs.settings["open_hour"], close - block)
    count = max(2, meta["bike_bays"] // 2)
    return dict(plan, employed_blocks=plan["employed_blocks"] + [
        dict(start_hour=h, hours=2 * block, count=count) for h in range(start, end - 2 * block + 1, 2 * block)])


def reserve_plan(plan, inputs, meta, missing):
    """기존 계획이 끝난 뒤 추가 교대만 붙여 남은 주문을 배송한다."""
    start = int(max(shift["end_min"] for shift in build_roster(plan)) // 60)
    size = int(inputs.cost["employed_shift_block_h"]) * 2
    riders = int(meta["bike_bays"])
    # 60분/단건의 여유를 둔 청산 능력. 결과는 배송 보존 검산을 별도로 통과해야 한다.
    blocks = max(1, (2 * missing + riders * size - 1) // (riders * size))
    extra = [dict(start_hour=start + k * size, hours=size, count=riders) for k in range(blocks)]
    return dict(plan, employed_blocks=plan["employed_blocks"] + extra), start, riders * size * blocks
