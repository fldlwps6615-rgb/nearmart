"""⑤의 독립 반복과 06 달력으로 08 운영 입력을 계산한다."""
import json

import numpy as np
import pandas as pd

from src.common.io import load_file
from src.network.model import read_output, require


KEY = ["instance", "radius_type", "orders"]
DAY_TYPES = ["weekday_clear", "weekend_clear", "weekday_rain"]


def calendar_counts(config, spec):
    calendar = load_file("06_pilot_calendar_weather.csv", config=config)
    dates = pd.to_datetime(calendar.date, format="%Y-%m-%d")
    rain = calendar.weather.isin(spec["rain_weather"])
    require(dates.notna().all() and dates.is_unique and (rain | calendar.weather.eq("clear")).all(),
            "06 날짜가 중복·누락되었거나 대표일로 매핑되지 않은 날씨가 있습니다")
    day_type = np.select([rain, dates.dt.day_of_week.ge(5)], ["weekday_rain", "weekend_clear"],
                         default="weekday_clear")
    return pd.Series(day_type).value_counts().reindex(DAY_TYPES, fill_value=0)


def pilot_cost(model):
    orders = load_file("05_pilot_orders.csv", config=model.cfg).query("demand_included == 1")
    start = pd.to_datetime(orders.pick_start_ts, format="mixed")
    end = pd.to_datetime(orders.pick_end_ts, format="mixed")
    hours = (end - start).dt.total_seconds() / 3600
    pay = orders.rider_pay_alloc_krw
    require(hours.ge(0).all() and pay.ge(0).all(), "05 라이더 지급액 또는 피킹시간에 결측·음수가 있습니다")
    wage = model.engine.par("picker_wage_krw_per_h_2027") * model.engine.par("labor_load_factor")
    return dict(pilot_orders=len(orders), pilot_picker_active_hours=hours.sum(),
                pilot_rider_cost_per_order_krw=pay.mean(), pilot_picker_cost_per_order_krw=hours.mean()*wage)


def validated_runs(model, spec):
    out = model.out
    basis = read_output(out / "service_radius_basis.csv").query("version == 'v2'")
    checks = read_output(out / "radius_bm_check.csv").query("version == 'v2'")
    days = read_output(out / "radius_bm_v2_repetitions.csv").query("version == 'v2' and phase == 'validation'")
    plans = read_output(out / "radius_bm_v2_plans.csv").query("version == 'v2'").set_index(KEY)
    require(plans.index.is_unique and not checks.duplicated(KEY).any(), "v2 조합 키 중복")
    engine = model.engine
    employed_hourly = (engine.par("employed_wage_krw_per_h_2027") * engine.par("labor_load_factor")
                       + engine.par("employed_vehicle_krw_per_month") / model.cfg["dispatch"]["fte_hours_per_month"])
    incentive = engine.par("employed_incentive_krw")
    groups = days.groupby(KEY)
    passed_rows = []
    for row in checks.itertuples():
        key = row.instance, row.radius_type, row.orders
        target = basis.loc[basis.instance.eq(row.instance) & basis.radius_type.eq(row.radius_type), "ontime_target"].item()
        runs = groups.get_group(key).copy() if key in groups.groups else days.iloc[:0]
        observed = (len(runs) == 20 and set(runs.repetition) == set(range(1, 21))
                    and runs.ontime_corrected.ge(target - 1e-8).all() and runs.undelivered.eq(0).all()
                    and runs.delivered.eq(row.orders).all() and runs.supply_ok.eq(True).all()
                    and runs.bay_max.le(row.bike_bays).all() and runs.rule_violations.eq(0).all())
        require(observed == (row.feasible == True), f"v2 독립 검증 판정 불일치: {key}")
        if not observed:
            continue
        require(np.allclose(runs.ontime_corrected_orders / row.orders, runs.ontime_corrected),
                f"v2 보정 정시율 재계산 불일치: {key}")
        metrics = ["rider_cost_per_order", "picker_cost_per_order", "ontime_corrected"]
        require(np.allclose(runs[metrics].mean(), [row.rider_cost_per_order, row.picker_cost_per_order,
                                                 row.ontime_stoch_corrected]), f"v2 반복 평균 불일치: {key}")
        # 직고용 교대는 전부 유급이다. BM1~3의 외부 퀵은 0건이다.
        plan = json.loads(plans.loc[key, "plan_json"])
        paid_hours = sum(b["count"] * b["hours"] for b in plan["employed_blocks"])
        employed_orders = runs.employed_share * row.orders
        require(np.allclose(employed_orders, employed_orders.round()), f"직고용 배송 건수 불일치: {key}")
        runs["employed_unit"] = (paid_hours * employed_hourly + employed_orders.round() * incentive) / row.orders
        runs["contract_unit"] = runs.rider_cost_per_order - runs.employed_unit
        require(runs.contract_unit.ge(0).all(), f"위탁 비용 음수: {key}")
        passed_rows.append(runs)

    passed = pd.concat(passed_rows, ignore_index=True)
    maxima = passed.groupby(["radius_type", "day_type"]).orders.max()
    recorded = basis.set_index(["radius_type", "day_type"]).max_orders_per_day.dropna().sort_index()
    require(recorded.index.equals(maxima.index) and np.array_equal(recorded, maxima), "서비스 반경 상한 재계산 불일치")
    benchmark = load_file("16_vrp_benchmark_orders.csv", config=model.cfg)
    require(benchmark.weight_kg.le(engine.par("trip_max_kg")).all(), "BM 외부 퀵 분모 검산 실패")
    require(all(r == "original" or int(r) <= spec["radius_limit_min"] for r, _ in maxima.index),
            "서비스 상한 밖 v2 통과 조합이 발견되었습니다")
    return passed, maxima, checks


def load_basis(model, spec):
    passed, maxima, checks = validated_runs(model, spec)
    counts = calendar_counts(model.cfg, spec)
    bays = model.sites.groupby("hub_type").bike_bays.median()
    pickers = model.types.max_pickers
    scales = pd.concat([bays / bays["M"], pickers / pickers["M"]], axis=1).min(axis=1)
    baseline = pilot_cost(model)
    baseline["pilot_cost_per_order_krw"] = baseline["pilot_rider_cost_per_order_krw"] + baseline["pilot_picker_cost_per_order_krw"]
    rows = []
    for radius in sorted({str(alt["T"]) for alt in model.p["alternatives"].values()}):
        subset = passed[passed.radius_type.eq(radius)]
        require(len(subset) > 0, f"{radius}분의 독립 통과 조합이 없습니다")
        fallback = spec["capacity_fallback"].get(radius, {})
        require(all((radius, target) not in maxima.index for target in fallback), f"{radius}분에 대체 대상의 통과 조합이 있습니다")
        limits = pd.Series({day: maxima.loc[radius, fallback.get(day, day)] for day in counts.index})
        m_capacity = float(limits @ counts / counts.sum())
        contract, employed, picker, on_time = subset[["contract_unit", "employed_unit", "picker_cost_per_order",
                                                      "ontime_corrected"]].mean()
        total = contract + employed + picker
        rows.append(dict(T=int(radius), passing_combinations=subset[KEY].drop_duplicates().shape[0],
                         validation_rows=len(subset), **{f"days_{day}": counts[day] for day in DAY_TYPES},
                         **{f"capacity_m_{day}": limits[day] for day in DAY_TYPES},
                         capacity_m_weekday_clear_source=fallback.get("weekday_clear", "weekday_clear"),
                         bm3_original_max_orders=maxima.loc["original", "weekday_rain"],
                         **{f"median_bike_bays_{kind}": bays[kind] for kind in scales.index},
                         **{f"max_pickers_{kind}": pickers[kind] for kind in scales.index},
                         **{f"scale_{kind}": scales[kind] for kind in scales.index},
                         **{f"capacity_per_day_{kind}": m_capacity * scales[kind] for kind in scales.index},
                         **{f"capacity_per_year_{kind}": m_capacity * scales[kind] * model.days_per_year for kind in scales.index},
                         contract_unit_krw=contract, employed_unit_krw=employed, picker_unit_krw=picker,
                         cost_per_order_krw=total, on_time=on_time, late_rate=1-on_time, **baseline,
                         cost_minus_05_per_order_krw=total-baseline["pilot_cost_per_order_krw"]))
    audit = checks.set_index(KEY).loc[[('BM1', 'original', 886), ('BM1', '12', 886)]].reset_index()
    return pd.DataFrame(rows).set_index("T"), audit
