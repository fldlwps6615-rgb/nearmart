"""P0 정책으로 평일 맑음 파일럿을 재현하고 관측 일별 지표와 대조한다."""

import argparse
from copy import copy
import math
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.dispatch.engine import DispatchEngine, max_overlap
from src.dispatch.inputs import DispatchInputs
from src.travel_time import TravelTime

from .orders import ORDER_ATTRIBUTES, generate_orders


METRICS = {
    "orders_per_day": ("하루 주문 수", "건"),
    "avg_delivery_min": ("평균 배송시간", "분"),
    "p90_delivery_min": ("90분위 배송시간", "분"),
    "ontime_rate": ("정시율", "비율"),
    "orders_per_trip": ("트립당 주문 수", "건"),
    "contract_hourly_income": ("위탁 시간당 수입", "원/시간"),
}
COMPONENTS = ("pick_wait_min", "pick_min", "depart_wait_min", "after_depart_min")
ACTUAL = "05b_actual"
FALLBACK = "supply_cap_fallback"
CONTRACT_FEES = ("contract_base_krw", "contract_extra_order_krw", "contract_km_rate_krw",
                 "contract_peak_surcharge_krw", "contract_weather_surcharge_rain_krw",
                 "contract_weather_surcharge_heavy_krw")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def minute_since(ts, date):
    return (pd.to_datetime(ts) - pd.Timestamp(date)).dt.total_seconds() / 60


def distribution(values):
    return dict(mean=values.mean(), p05=values.quantile(.05), p95=values.quantile(.95))


def session_rows(log, date):
    if log.empty:
        return []
    sessions = log[["rider_id"]].rename(columns={"rider_id": "worker_id"}).assign(
        start_min=minute_since(log.login_ts, date), end_min=minute_since(log.logoff_ts, date))
    require(not sessions.isna().any().any() and (sessions.end_min > sessions.start_min).all()
            and not sessions.worker_id.duplicated().any(), f"05b 접속 구간 오류: {date}")
    return sessions.to_dict("records")


def staffing(inputs, meta, sessions):
    settings = inputs.settings
    close = math.ceil(settings["last_order_min"] / 60)
    hours = range(settings["open_hour"], settings["planning_end_hour"])
    pickers = {str(h): settings["max_pickers"] if h < close else settings["tail_pickers"] for h in hours}
    contract = {str(h): 0 if sessions else inputs.cap(meta, h) for h in hours}
    return dict(policy=meta["policy"], target=settings["p0_max_orders"], wait=0,
                wait_by_hour={}, margin=0, pickers=pickers, contract=contract, employed_blocks=[])


def actual_day(orders, sessions, logs, sla):
    complete = orders.loc[orders.delivery_kpi_eligible.eq(1)]
    delivery = complete.loc[~complete.is_quick]
    trip_orders = orders.loc[orders.rider_trip_eligible.eq(1)]
    hours = sum(s["end_min"] - s["start_min"] for s in sessions) / 60
    times = [pd.to_datetime(delivery[key]) for key in
             ("order_ts", "pick_start_ts", "pick_end_ts", "depart_ts", "delivered_ts")]
    duration = (times[-1] - times[0]).dt.total_seconds() / 60
    components = {(key): (end - start).dt.total_seconds().mean() / 60
                  for key, start, end in zip(COMPONENTS, times, times[1:])}
    trips = trip_orders.groupby(["rider_id", "trip_id"]).ngroups
    return dict(orders_per_day=len(orders), avg_delivery_min=duration.mean(),
                p90_delivery_min=duration.quantile(.9), ontime_rate=duration.le(sla).mean(),
                orders_per_trip=len(trip_orders)/trips, contract_hourly_income=logs.pay_krw.sum()/hours if hours else np.nan,
                contract_hours=hours, contract_pay_krw=logs.pay_krw.sum(), delivery_kpi_orders=len(delivery),
                trip_orders=len(trip_orders), trips=trips, external_orders=int(orders.is_quick.sum()),
                **all_delivery(complete.order_ts, complete.delivered_ts, sla), **components)


def all_delivery(start, end, sla):
    duration = (pd.to_datetime(end) - pd.to_datetime(start)).dt.total_seconds() / 60
    return overall_duration(duration, sla)


def overall_duration(duration, sla):
    require(duration.notna().all() and duration.ge(0).all(), "전체 배송시간 결측 또는 음수")
    return dict(all_avg_delivery_min=duration.mean(), all_p90_delivery_min=duration.quantile(.9),
                all_ontime_rate=duration.le(sla + 1e-8).mean())


def simulate_day(inputs, meta, generated, plan, sessions, quick_times, travel_seed, quick_seed):
    travel_rng = np.random.default_rng(travel_seed)
    quick_rng = np.random.default_rng(quick_seed)

    engine = DispatchEngine(inputs, generated, meta)
    order_index = {key: i for i, key in enumerate(engine.orders.order_id)}

    def external_delivery(order, pick_end):
        ready = pick_end + quick_rng.choice(quick_times)
        j = order_index[order["order_id"]]
        leg = engine.geometry.minutes(engine.geometry.hub, j, ready)
        return ready + leg * TravelTime.stochastic_multiplier(quick_rng) + engine.geometry.handover[j]

    result = engine.simulate(plan, external_delivery=external_delivery, contract_shifts=sessions or None,
                             travel_multiplier=lambda: TravelTime.stochastic_multiplier(travel_rng))
    delivery = result.t3.merge(generated[["order_id", "order_min", "pick_std_min"]], on="order_id", validate="one_to_one")
    timeline = [delivery[key] for key in ("order_min", "pick_start_min", "pick_end_min", "depart_min", "delivered_min")]
    components = {key: (end-start).mean() for key, start, end in zip(COMPONENTS, timeline, timeline[1:])}
    k = result.kpi
    external = result.external.reindex(columns=["order_id", "delivered_min"]).merge(
        generated[["order_id", "order_min"]], on="order_id", validate="one_to_one")
    durations = pd.concat([delivery.delivered_min - delivery.order_min,
                           external.delivered_min - external.order_min], ignore_index=True)
    delivered_ids = set(delivery.order_id) | set(result.external.get("order_id", []))
    require(k["orders"] == len(generated) == len(delivered_ids) and delivered_ids == set(generated.order_id), "전체 주문 배송 검산 실패")
    require(math.isclose(sum(components.values()), k["avg_min"], abs_tol=1e-8), "배송시간 구성 검산 실패")
    require(np.allclose(delivery.pick_end_min-delivery.pick_start_min, delivery.pick_std_min), "피킹 표준시간 검산 실패")
    require(len(result.external) == int(generated.weight_kg.gt(inputs.cost["trip_max_kg"]).sum()), "외부 퀵 분리 검산 실패")
    require(result.trips.orders.le(inputs.settings["p0_max_orders"]).all()
            and result.trips.weight_kg.le(inputs.cost["trip_max_kg"] + 1e-8).all(), "트립 한도 검산 실패")
    require(k["bay_max"] == max_overlap(zip(result.bays.start_min, result.bays.end_min)), "대기 공간 검산 실패")
    contract = result.work.loc[result.work.role.eq("contract")]
    intervals = list(zip(contract.start_min, contract.end_min))
    cap_excess = sum(
        max_overlap((max(a, 60*h), min(b, 60*(h+1))) for a, b in intervals) > inputs.cap(meta, h)
        for h in range(inputs.settings["open_hour"], math.ceil(contract.end_min.max()/60))
    )
    require(k["contract_cap_violations"] == cap_excess, "시간대별 위탁 공급 상한 검산 실패")
    require(math.isclose(k["rider_hourly"], result.trips.pay_krw.sum()/k["contract_hours"], abs_tol=1e-8), "위탁 시급 검산 실패")
    require(all(generated.road_km.le(meta["radius_km"])), "생성 위치의 서비스 반경 검산 실패")
    return dict(orders_per_day=k["orders"], avg_delivery_min=k["avg_min"], p90_delivery_min=k["p90_min"],
                ontime_rate=k["on_time"], orders_per_trip=k["orders_per_trip"], contract_hourly_income=k["rider_hourly"],
                contract_hours=k["contract_hours"], contract_pay_krw=k["contract_pay_krw"],
                delivery_kpi_orders=len(delivery), trip_orders=len(delivery), trips=len(result.trips),
                external_orders=len(result.external), late_orders=k["late_orders"],
                all_late_orders=k["late_orders"] + k["external_late_orders"],
                bay_max=k["bay_max"], bay_violation=k["bay_max"] > meta["bike_bays"],
                supply_ok=k["supply_ok"], supply_cap_violation_hours=k["contract_cap_violations"],
                **overall_duration(durations, inputs.settings["sla_min"]), **components)


def pilot_samples(orders, site, travel, radius, config, cost):
    columns = ["cust_lat", "cust_lon", "gu", "adm_cd2"]
    locations = orders[columns].copy()
    require(not locations.isna().any().any(), "파일럿 위치 필수값 결측")
    locations["road_km"] = [travel.road(site.lat, site.lon, site.gu, r.cust_lat, r.cust_lon, r.gu)[3]
                            for r in locations.itertuples(index=False)]
    unresolved = orders.weight_analysis_eligible.eq(0)
    require(orders.loc[unresolved, "weight_unresolved"].eq(1).all(),
            "승인된 예외 밖 주문 속성 제외 발견")
    attributes = orders.loc[~unresolved, ORDER_ATTRIBUTES]
    require(not attributes.isna().any().any() and not attributes[["items", "weight_kg", "basket_krw"]].lt(0).any().any(),
            "주문 속성 표본 결측 또는 음수")
    quick = orders.loc[orders.is_quick & orders.delivery_kpi_eligible.eq(1)].copy()
    delay = (pd.to_datetime(quick.delivered_ts) - pd.to_datetime(quick.pick_end_ts)).dt.total_seconds() / 60
    residual = delay.to_numpy() - np.array([
        travel.minutes(site.lat, site.lon, site.gu, r.cust_lat, r.cust_lon, r.gu,
                       pd.Timestamp(r.depart_ts).hour, config["sim"]["replay"]["weather"])
        + cost["handover_apt_min" if r.building_type == "아파트" else "handover_other_min"]
        for r in quick.itertuples()])
    require(len(residual) > 0 and np.isfinite(residual).all() and (residual >= 0).all(),
            "외부 퀵 피킹 후 잔여 시간 표본 확인 필요")
    return locations, attributes, residual, int(locations.road_km.gt(radius).sum()), int(unresolved.sum())


def replay_hub(config, base_inputs, site, orders, logs, dongs, profile, rng):
    settings = config["sim"]
    replay = settings["replay"]
    inputs = copy(base_inputs)
    inputs.settings = dict(base_inputs.settings)
    hub_types = load_file("04_hub_types.csv", config=config).set_index("hub_type")
    inputs.settings["max_pickers"] = int(hub_types.loc[site.hub_type, "max_pickers"])
    groups = orders.groupby("date", sort=True)
    dates = list(groups.groups)
    log_groups = logs.groupby("date")
    log_rows = {day: log_groups.get_group(day) for day in log_groups.groups}
    sessions = {day: session_rows(log_rows.get(day, pd.DataFrame()), day) for day in dates}
    radius = config["pilot_diagnosis"]["service_radius_km"][site.site_id]
    rider_classes = dongs.loc[dongs.gu.eq(site.gu), "rider_area_class"].unique()
    require(len(rider_classes) == 1, f"위탁 등급 불일치: {site.gu}")
    labels = dongs.set_index("adm_cd2").single_hh_ratio
    areas = orders.adm_cd2.map(labels)
    require(areas.notna().all(), "파일럿 주문의 동별 시간대 유형 누락")
    high_share = areas.ge(labels.median()).mean()
    hour = profile.loc[profile.day_type.eq(replay["day_type"])].pivot(index="hour", columns="area_type", values="share")
    hourly = hour.single_high * high_share + hour.single_low * (1-high_share)
    locations, attrs, quick, outside, unresolved = pilot_samples(orders, site, inputs.travel, radius, config, inputs.cost)
    meta = dict(instance=site.site_id, hub_lat=site.lat, hub_lon=site.lon, hub_gu=site.gu,
                rider_area_class=rider_classes[0], bike_bays=int(site.bike_bays), radius_km=radius,
                weather=replay["weather"], policy=replay["policy"])

    actual = []
    for day, group in groups:
        row = actual_day(group, sessions[day], log_rows.get(day, pd.DataFrame(columns=["pay_krw"])), inputs.settings["sla_min"])
        actual.append(dict(hub_id=site.site_id, date=day, **row))

    draws = rng.integers(0, len(dates), size=settings["repetitions"])
    runs = []
    cost = base_inputs.cost

    def run_day(draw, seeds):
        day = dates[draw]
        n = len(groups.groups[day])
        year = pd.Timestamp(day).year
        fee_factor = (1 + cost["contract_fee_growth"]) ** (year - replay["fee_base_year"])
        inputs.cost = cost | {key: cost[key] * fee_factor for key in CONTRACT_FEES}
        inputs.settings["min_contract_hourly_krw"] = cost["contract_min_hourly_earning_krw"] * fee_factor
        order_seed, travel_seed, quick_seed = seeds
        generated = generate_orders(n, hourly, locations, attrs, radius, np.random.default_rng(order_seed),
                                    open_min=inputs.settings["open_hour"]*60, close_min=inputs.settings["last_order_min"],
                                    pick_base=cost["pick_time_base_min"], pick_per_item=cost["pick_time_per_item_min"],
                                    pick_items_cap=settings["pick_items_cap"])
        plan = staffing(inputs, meta, sessions[day])
        metrics = simulate_day(inputs, meta, generated, plan, sessions[day], quick, travel_seed, quick_seed)
        return dict(matched_date=day, contract_source=ACTUAL if sessions[day] else FALLBACK,
                    observed_contract_hours=actual[draw]["contract_hours"], fee_year=year,
                    supply_threshold_krw=inputs.settings["min_contract_hourly_krw"], **metrics)

    seeds = rng.integers(0, 2**63, size=(settings["repetitions"], 3))
    for repeat, (draw, day_seeds) in enumerate(zip(draws, seeds), 1):
        try:
            metrics = run_day(draw, day_seeds)
        except ValueError as error:
            raise ValueError(f"{site.site_id} 재현 {repeat}회차 ({dates[draw]}): {error}") from error
        runs.append(dict(hub_id=site.site_id, repeat=repeat, bike_bays=int(site.bike_bays), **metrics))
    require(all(runs[0][key] == value for key, value in run_day(draws[0], seeds[0]).items()),
            "같은 시드 재실행 결과 불일치")
    info = dict(radius_km=radius, observed_days=len(dates), actual_log_days=sum(bool(s) for s in sessions.values()),
                source_orders=len(orders), location_outside_radius=outside, unresolved_weight=unresolved,
                missing_delivery_rows=int(orders.delivered_time_missing.sum()),
                observed_external_le_35kg=int((orders.is_quick & orders.weight_kg.le(cost["trip_max_kg"])).sum()),
                quick_timing_samples=len(quick), verified_fixed_seed=True,
                high_area_share=high_share, max_pickers=inputs.settings["max_pickers"])
    return pd.DataFrame(actual), pd.DataFrame(runs), info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    config = load_config(args.config)
    run(config)


def run(config):
    from .report import write_results

    started = perf_counter()
    inputs = DispatchInputs(config)
    settings = config["sim"]
    replay = settings["replay"]
    outputs = ROOT / config["paths"]["outputs"]
    profile = pd.read_csv(outputs / "hour_profile.csv", encoding="utf-8-sig")
    calendar = load_file("06_pilot_calendar_weather.csv", config=config)
    require(not calendar.date.duplicated().any() and replay["day_type"] == "weekday", "재현 달력·대표일 확인 필요")
    dates = calendar.loc[calendar.weather.eq(replay["weather"])
                         & pd.to_datetime(calendar.date).dt.day_of_week.lt(5), "date"]
    orders = load_file("05_pilot_orders.csv", config=config).query("demand_included == 1").copy()
    orders["is_quick"] = orders.rider_id.eq(config["cleaning"]["external_rider_id"])
    orders["date"] = orders.order_ts.str.slice(0, 10)
    logs = load_file("05b_pilot_rider_log.csv", config=config)
    dongs = load_file("01_dong_master.csv", config=config)
    sites = inputs.sites.set_index("site_id", drop=False)
    rng = np.random.default_rng(settings["seed"])
    frames = {}
    for hub in replay["hubs"]:
        o = orders.loc[orders.hub_id.eq(hub) & orders.date.isin(dates)]
        log = logs.loc[logs.hub_id.eq(hub) & logs.date.isin(dates)]
        frames[hub] = replay_hub(config, inputs, sites.loc[hub], o, log, dongs, profile, rng)
        print(f"{hub}: 재현 {len(frames[hub][1])}회 배송 및 건별 검산 완료", flush=True)
    write_results(config, outputs, frames, started)


if __name__ == "__main__":
    main()
