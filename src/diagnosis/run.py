"""파일럿 운영 진단. 저장소 루트에서 python -m src.diagnosis.run."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.diagnosis.figures import plot_all


COMPONENTS = {
    "pick_wait": ("order_ts", "pick_start_ts"),
    "pick": ("pick_start_ts", "pick_end_ts"),
    "depart_wait": ("pick_end_ts", "depart_ts"),
    "depart_to_delivery": ("depart_ts", "delivered_ts"),
}
TRIP_KEY = ["hub_id", "date", "rider_id", "trip_id"]
RIDER_DAY_KEY = ["hub_id", "date", "rider_id"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cv(counts):
    return counts.std(ddof=0) / counts.mean() if len(counts) and counts.mean() else np.nan


def divide(numerator, denominator):
    return numerator / denominator if denominator else np.nan


def date_key(day):
    return f"date={day:%Y-%m-%d}"


class Metrics:
    def __init__(self):
        self.rows = []

    def add(self, hub, segment, **values):
        self.rows.extend((hub, name, segment, value) for name, value in values.items())

    def frame(self):
        frame = pd.DataFrame(self.rows, columns=["hub_id", "metric", "segment", "value"])
        require(not frame.duplicated(["hub_id", "metric", "segment"]).any(), "KPI 식별 키 중복")
        return frame


def load_pilot(cfg):
    orders = load_file("05_pilot_orders.csv", config=cfg)
    riders = load_file("05b_pilot_rider_log.csv", config=cfg)
    calendar = load_file("06_pilot_calendar_weather.csv", config=cfg)
    dong = load_file("01_dong_master.csv", config=cfg)
    od = load_file("13_od_site_dong.csv", config=cfg)
    for col in ("order_ts", "pick_start_ts", "pick_end_ts", "depart_ts", "delivered_ts"):
        orders[col] = pd.to_datetime(orders[col], errors="raise")
    orders = orders.loc[orders.demand_included.eq(1) & orders.status.eq("DELIVERED")].copy()
    orders["date"] = orders.order_ts.dt.normalize()
    orders["hour"] = orders.order_ts.dt.hour
    calendar["date"] = pd.to_datetime(calendar.date, errors="raise")
    orders = orders.merge(calendar[["date", "weather"]], how="left", on="date", validate="many_to_one")
    require(orders.weather.notna().all(), "달력에 없는 주문일")
    require(orders.order_id.is_unique, "주문 ID 중복")
    riders["date"] = pd.to_datetime(riders.date, errors="raise")
    riders["hours"] = (pd.to_datetime(riders.logoff_ts) - pd.to_datetime(riders.login_ts)).dt.total_seconds() / 3600
    require(riders.hours.gt(0).all(), "접속시간이 0 이하인 라이더 행")
    require(not riders.duplicated(RIDER_DAY_KEY).any(), "라이더·영업일 키 중복")
    return orders, riders, calendar, dong, od


def delivery_stats(metrics, hub, segment, group):
    n = len(group)
    late = int(group.late.sum())
    metrics.add(hub, segment, delivery_orders=n, late_orders=late, on_time=divide(n-late, n),
                late_rate=divide(late, n), avg_delivery_min=group.delivery_min.mean(),
                p90_delivery_min=group.delivery_min.quantile(.9),
                **{f"avg_{name}_min": group[name].mean() for name in COMPONENTS})


def concentration(metrics, hub, segment, eligible, demand_n, eligible_total, demand_total, late_total):
    late = int(eligible.late.sum())
    kpi_share = divide(len(eligible), eligible_total)
    demand_share = divide(demand_n, demand_total)
    late_share = divide(late, late_total)
    metrics.add(hub, segment, demand_orders=demand_n, kpi_order_share=kpi_share,
                demand_order_share=demand_share, late_order_share=late_share,
                late_to_kpi_share_ratio=divide(late_share, kpi_share))


def analyze(cfg):
    p = cfg["pilot_diagnosis"]
    orders, riders, calendar, dong, od = load_pilot(cfg)
    hubs = list(p["opening_dates"])
    require(set(hubs) == set(orders.hub_id) == set(riders.hub_id), "설정·주문·라이더 거점 목록 불일치")
    sla = p["sla_minutes"]
    peak_start, peak_end = p["peak_hours"]
    first_hour = cfg["cleaning"]["service_start_min"] // 60
    last_hour = (cfg["cleaning"]["service_end_min"] - 1) // 60
    order_hours = range(first_hour, last_hour + 1)

    non_quick = orders[orders.rider_id.ne(cfg["cleaning"]["external_rider_id"])]
    internal = non_quick.loc[non_quick.rider_trip_eligible.eq(1)].copy()
    delivery = non_quick.loc[non_quick.delivery_kpi_eligible.eq(1)].copy()
    delivery["delivery_min"] = (delivery.delivered_ts - delivery.order_ts).dt.total_seconds() / 60
    delivery["late"] = delivery.delivery_min.gt(sla)
    for name, (start, end) in COMPONENTS.items():
        delivery[name] = (delivery[end] - delivery[start]).dt.total_seconds() / 60
    component_values = delivery[list(COMPONENTS)]
    require(component_values.ge(0).all().all(), "배송 KPI 구성요소에 결측 또는 음수")
    require(np.allclose(component_values.sum(axis=1), delivery.delivery_min), "배송 구성요소 합 불일치")

    require(internal[TRIP_KEY + ["depart_ts", "rider_pay_alloc_krw"]].notna().all().all(), "내부 트립 키·출발·지급 기록 결측")
    trips = internal.groupby(TRIP_KEY).agg(
        orders=("order_id", "size"), depart=("depart_ts", "first"), depart_values=("depart_ts", "nunique"),
    ).reset_index()
    require(trips.depart_values.eq(1).all(), "동일 트립의 출발시각 불일치")
    trips["hour"] = trips.depart.dt.hour
    trips["peak"] = trips.hour.between(peak_start, peak_end - 1)
    by_rider = trips.groupby(RIDER_DAY_KEY).agg(trips=("orders", "size"), orders=("orders", "sum"))
    linked = riders.merge(by_rider.rename(columns={"trips": "trips_05"}), how="outer",
                          on=RIDER_DAY_KEY, validate="one_to_one")
    require(linked.trips.eq(linked.trips_05).all() and linked.orders_delivered.eq(linked.orders).all(),
            "05와 05b의 라이더·일별 트립/배송 건수 불일치")

    metrics = Metrics()
    daily_tables, rider_tables, hourly_tables, distance_tables = {}, {}, {}, {}
    weather_groups = list(dict.fromkeys([*p["weather_labels"], *calendar.weather.unique()]))
    for hub in [*hubs, "ALL"]:
        demand = orders if hub == "ALL" else orders[orders.hub_id.eq(hub)]
        eligible = delivery if hub == "ALL" else delivery[delivery.hub_id.eq(hub)]
        late_total = int(eligible.late.sum())
        delivery_stats(metrics, hub, "all", eligible)
        concentration(metrics, hub, "all", eligible, len(demand), len(eligible), len(demand), late_total)
        for weather in weather_groups:
            wg = eligible[eligible.weather.eq(weather)]
            wd = demand[demand.weather.eq(weather)]
            segment = f"weather={weather}"
            delivery_stats(metrics, hub, segment, wg)
            concentration(metrics, hub, segment, wg, len(wd), len(eligible), len(demand), late_total)
            for hour in order_hours:
                wh = wg[wg.hour.eq(hour)]
                whd = wd[wd.hour.eq(hour)]
                s = f"{segment}|order_hour={hour:02d}"
                delivery_stats(metrics, hub, s, wh)
                concentration(metrics, hub, s, wh, len(whd), len(eligible), len(demand), late_total)
        for hour in order_hours:
            hg = eligible[eligible.hour.eq(hour)]
            hd = demand[demand.hour.eq(hour)]
            segment = f"order_hour={hour:02d}"
            delivery_stats(metrics, hub, segment, hg)
            concentration(metrics, hub, segment, hg, len(hd), len(eligible), len(demand), late_total)
        if hub == "ALL":
            continue

        opened = pd.Timestamp(p["opening_dates"][hub])
        dates = calendar[calendar.date.ge(opened)].copy()
        dates["elapsed"] = (dates.date - opened).dt.days
        require(dates.elapsed.tolist() == list(range(len(dates))), f"{hub} 영업 달력 공백 또는 개점일 누락")
        require(demand.date.isin(dates.date).all(), f"{hub} 개점 전 주문 또는 달력 누락")
        counts = demand.groupby("date").size()
        dates["orders"] = dates.date.map(counts).fillna(0).astype(int)
        for row in dates.itertuples():
            promotion = row.promotion if pd.notna(row.promotion) else "없음"
            s = f"{date_key(row.date)}|elapsed_day={row.elapsed}|weekday={row.weekday}|weather={row.weather}|promotion={promotion}"
            metrics.add(hub, s, daily_orders=row.orders)
        require(dates.orders.sum() == len(demand), f"{hub} 일별 주문 합 불일치")
        daily_tables[hub] = dates

        ht = trips[trips.hub_id.eq(hub)]
        hi = internal[internal.hub_id.eq(hub)]
        hr = riders[riders.hub_id.eq(hub)]
        require(hr.date.isin(dates.date).all(), f"{hub} 라이더의 영업일 누락")
        require(ht.orders.sum() == len(hi), f"{hub} 트립별 주문 합 불일치")
        metrics.add(hub, "all", trips=len(ht), orders_per_trip=ht.orders.mean(),
                    single_trip_share=ht.orders.eq(1).mean(),
                    contract_pay_per_order_krw=hi.rider_pay_alloc_krw.mean(),
                    order_alloc_pay_sum_krw=hi.rider_pay_alloc_krw.sum(),
                    rider_log_pay_sum_krw=hr.pay_krw.sum(),
                    rider_log_minus_order_alloc_krw=hr.pay_krw.sum()-hi.rider_pay_alloc_krw.sum())
        for size in range(1, int(trips.orders.max()) + 1):
            n = int(ht.orders.eq(size).sum())
            metrics.add(hub, f"trip_size={size}", trip_count=n, trip_share=divide(n, len(ht)))

        hours = sorted(set(order_hours) | set(ht.hour))
        hourly = pd.DataFrame(index=pd.Index(hours, name="hour"))
        hourly["orders_per_day"] = demand.groupby("hour").size().reindex(hours, fill_value=0) / len(dates)
        hourly["orders_per_trip"] = ht.groupby("hour").orders.mean().reindex(hours)
        for hour, group in ht.groupby("hour"):
            metrics.add(hub, f"depart_hour={hour:02d}", trips=len(group), orders_per_trip=group.orders.mean(),
                        single_trip_share=group.orders.eq(1).mean())
        for hour, row in hourly.iterrows():
            metrics.add(hub, f"order_hour={hour:02d}", orders_per_operating_day=row.orders_per_day)
        peak_orders = int(demand.hour.between(peak_start, peak_end - 1).sum())
        metrics.add(hub, "peak", orders=peak_orders, operating_days=len(dates),
                    orders_per_hour=divide(peak_orders, len(dates) * (peak_end - peak_start)))
        for label, group in [("peak", ht[ht.peak]), ("off_peak", ht[~ht.peak])]:
            metrics.add(hub, label, trips=len(group), orders_per_trip=group.orders.mean(),
                        single_trip_share=group.orders.eq(1).mean())
        hourly_tables[hub] = hourly

        daily_rider = hr.groupby("date").agg(hours=("hours", "sum"), pay=("pay_krw", "sum"),
                                             orders=("orders_delivered", "sum"), riders=("rider_id", "size"),
                                             workload_cv=("orders_delivered", cv))
        daily_rider["earnings"] = daily_rider.pay / daily_rider.hours
        daily_rider["below"] = daily_rider.earnings.lt(p["contract_min_earnings_krw_per_hour"])
        for date, row in daily_rider.iterrows():
            metrics.add(hub, date_key(date), rider_pay_krw=row.pay, rider_hours=row.hours,
                        rider_count=row.riders, rider_delivered_orders=row.orders,
                        rider_earnings_krw_per_hour=row.earnings, rider_below_threshold=int(row.below),
                        rider_orders_per_hour=row.orders / row.hours, rider_workload_cv=row.workload_cv)
        metrics.add(hub, "all", rider_observed_days=len(daily_rider),
                    rider_below_threshold_days=daily_rider.below.sum(),
                    rider_below_threshold_share=daily_rider.below.mean(),
                    rider_earnings_krw_per_hour=daily_rider.earnings.mean(),
                    rider_earnings_pooled_krw_per_hour=hr.pay_krw.sum() / hr.hours.sum(),
                    rider_orders_per_hour=hr.orders_delivered.sum() / hr.hours.sum(),
                    rider_workload_cv=cv(hr.groupby("rider_id").orders_delivered.sum()),
                    rider_daily_workload_cv_mean=daily_rider.workload_cv.mean())
        rider_tables[hub] = daily_rider

        radius = p["service_radius_km"][hub]
        coverage = od.loc[od.site_id.eq(hub) & od.road_km.le(radius), ["adm_cd2", "road_km"]]
        coverage = coverage.merge(dong[["adm_cd2", "households_2026"]], on="adm_cd2", how="left", validate="one_to_one")
        require(coverage.households_2026.gt(0).all(), f"{hub} 권역 세대수 누락")
        require(demand.adm_cd2.isin(coverage.adm_cd2).all(), f"{hub} 반경 밖 주문: 서비스 범위 확인 필요")
        coverage["orders"] = coverage.adm_cd2.map(demand.groupby("adm_cd2").size()).fillna(0)
        edges = p["distance_bin_edges_km"]
        require(edges[0] == 0 and edges[-1] > radius, f"{hub} 거리 구간이 권역을 포함해야 함")
        labels = [f"{a:g}–{b:g}" for a, b in zip(edges, edges[1:])]
        coverage["band"] = pd.cut(coverage.road_km, edges, labels=labels, right=False)
        distance = coverage.groupby("band", observed=False).agg(
            orders=("orders", "sum"), households=("households_2026", "sum"), dongs=("adm_cd2", "size"))
        require(distance.orders.sum() == len(demand), f"{hub} 거리 구간 주문 합 불일치")
        distance["rate"] = distance.orders.div(distance.households.where(distance.households.gt(0)))
        distance["rate_daily"] = distance.rate / len(dates)
        for band, row in distance.iterrows():
            metrics.add(hub, f"road_km={band}", distance_orders=row.orders, distance_households=row.households,
                        distance_dongs=row.dongs, orders_per_household=row.rate,
                        daily_orders_per_household=row.rate_daily)
        distance_tables[hub] = distance

    metrics.add("ALL", "all",
                rider_below_threshold_share=sum(r.below.sum() for r in rider_tables.values()) / sum(map(len, rider_tables.values())),
                rider_below_threshold_days=sum(r.below.sum() for r in rider_tables.values()),
                rider_observed_days=sum(map(len, rider_tables.values())))
    frame = metrics.frame()
    require(len(delivery) + int(non_quick.delivered_ts.isna().sum()) == len(non_quick), "배송 KPI 분모 검산 실패")
    require(len(orders) - len(non_quick) == int(orders.rider_id.eq(cfg["cleaning"]["external_rider_id"]).sum()),
            "외부 퀵 제외 분모 검산 실패")
    return frame, daily_tables, rider_tables, hourly_tables, distance_tables


def make_reports(frame, cfg, output):
    p = cfg["pilot_diagnosis"]
    values = frame.set_index(["hub_id", "metric", "segment"]).value

    def value(hub, name, segment="all"):
        return float(values.loc[(hub, name, segment)])

    expected = p["expected"]
    comparisons = []

    def compare(hub, metric, segment, target, explanation, actual=None, pooled=None):
        if actual is None:
            actual = value(hub, metric, segment)
        comparisons.append(dict(hub_id=hub, metric=metric, segment=segment, expected=target,
                                actual=actual, actual_minus_expected=actual-target,
                                pooled_actual=pooled, interpretation=explanation))

    compare("ALL", "avg_delivery_min", "all", expected["avg_delivery_min"], "60분 약속 대상의 주문별 가중 평균")
    for hub in p["opening_dates"]:
        compare(hub, "orders_per_trip", "all", expected["trip_orders"][hub], "거점·영업일·라이더·트립 ID 기준")
        compare(hub, "rider_earnings_krw_per_hour", "all", expected["rider_earnings"][hub],
                "기대값에 가까운 거점·일 시급의 단순평균. pooled_actual은 기간 전체 지급액 합 ÷ 접속시간 합",
                pooled=value(hub, "rider_earnings_pooled_krw_per_hour"))
    compare("ALL", "late_rate", "all", expected["late_rate"], "지연 건수 ÷ 배송 KPI 대상 건수")
    rain_late_share = sum(value("ALL", "late_order_share", f"weather={w}") for w in ("rain", "heavy_rain"))
    compare("ALL", "late_order_share", "weather=rain_or_heavy_rain", expected["late_rain_share"],
            "비·폭우 지연 건수 ÷ 전체 지연 건수", actual=rain_late_share)
    peak_start, peak_end = p["peak_hours"]
    compare("P1", "orders_per_hour", "peak", expected["p1_peak_hourly_orders"],
            f"{peak_start}시 이상 {peak_end}시 미만 접수 주문 수 ÷ (운영일 × {peak_end-peak_start}시간)")
    pd.DataFrame(comparisons).to_csv(output / "pilot_expectation_check.csv", index=False, encoding=cfg["io"]["encoding"])

    late = int(value("ALL", "late_orders"))
    rain_demand = sum(value("ALL", "demand_order_share", f"weather={w}") for w in ("rain", "heavy_rain"))
    hour_segments = [s for h, m, s in values.index if h == "ALL" and m == "late_orders" and s.startswith("order_hour=")]
    worst_hour = max(hour_segments, key=lambda s: value("ALL", "late_orders", s))
    worst_label = worst_hour.split("=")[1]
    threshold = p["contract_min_earnings_krw_per_hour"]
    demand_peak = value("P1", "orders_per_hour", "peak")
    p1_peak = value("P1", "orders_per_trip", "peak")
    p1_off = value("P1", "orders_per_trip", "off_peak")
    p1_one = value("P1", "single_trip_share", "peak")
    p2_one = value("P2", "single_trip_share")
    p1_below = int(value("P1", "rider_below_threshold_days"))
    p2_below = int(value("P2", "rider_below_threshold_days"))
    p1_days = int(value("P1", "rider_observed_days"))
    p2_days = int(value("P2", "rider_observed_days"))
    near_far = {}
    for hub in ("P1", "P2"):
        rates = [(s.removeprefix("road_km="), v) for (h,m,s),v in values.items()
                 if h == hub and m == "daily_orders_per_household" and pd.notna(v)]
        near_far[hub] = (rates[0], rates[-1])
    (p1_near, p1_far), (p2_near, p2_far) = near_far.values()
    source = "출처: pilot_kpi.csv"
    report = f"""# 파일럿 운영 진단 초안

1. 비·폭우일과 늦은 저녁에 지연이 몰렸기 때문에 전체 평균만으로는 이 시간대의 우천 위험을 파악하기 어렵다(근거: 평균 배송시간={value("ALL", "avg_delivery_min"):.2f}분, {p["sla_minutes"]}분 초과={late}건, 비·폭우 지연 비중={rain_late_share:.1%}·수요 비중={rain_demand:.1%}, 지연 최다 접수 시간={worst_label}시대 {value("ALL", "late_orders", worst_hour):.0f}건·지연 비중 {value("ALL", "late_order_share", worst_hour):.1%}·수요 비중 {value("ALL", "demand_order_share", worst_hour):.1%}). {source}의 ALL 전체·날씨별·접수 시간별 지표.
2. P1은 저녁 피크에도 단건 트립이 많았기 때문에 묶음 규모가 작다(근거: {peak_start}–{peak_end}시 접수 시간당 주문={demand_peak:.2f}건, 피크 출발 단건 트립 비율={p1_one:.1%}, 피크={p1_peak:.3f}건/트립·그 외={p1_off:.3f}건/트립). {source}의 P1 peak·off_peak.
3. P2는 거의 모든 트립에 주문이 한 건씩 실렸기 때문에 묶음으로 트립 비용을 나누는 경우가 드물다(근거: 단건 트립 비율={p2_one:.1%}, 평균={value("P2", "orders_per_trip"):.3f}건/트립, 위탁 지급액={value("P2", "contract_pay_per_order_krw"):,.0f}원/건({value("P2", "contract_pay_per_order_krw")/1e8:.8f}억 원/건)). {source}의 P2 전체·트립 크기별 지표.
4. 접속시간 대비 지급액이 낮았기 때문에 위탁 라이더 수급 기준을 충족한 날은 두 거점 모두 적다(근거: {threshold:,}원/시간({threshold/1e8:.5f}억 원/시간) 미달=P1 {p1_below}/{p1_days}일·P2 {p2_below}/{p2_days}일, 접속시간당 배송=P1 {value("P1", "rider_orders_per_hour"):.2f}건·P2 {value("P2", "rider_orders_per_hour"):.2f}건). {source}의 거점·일 시급 및 전체 배송 생산성.
5. 가장 먼 구간의 세대당 주문이 가까운 구간보다 낮았기 때문에 권역 평균을 쓰면 관측 수요의 거리별 차이가 가려진다(근거: 세대당 하루 주문=P1 {p1_near[0]}km {p1_near[1]:.4f} → {p1_far[0]}km {p1_far[1]:.4f}, P2 {p2_near[0]}km {p2_near[1]:.4f} → {p2_far[0]}km {p2_far[1]:.4f}). {source}의 road_km 구간, 01 세대수·13 도로거리.

기대값 비교는 pilot_expectation_check.csv 참조. 시급의 actual은 거점·일 시급의 단순평균이며 pooled_actual은 운영 기간 총 지급액을 총 접속시간으로 나눈 값이다. 피크는 주문 접수와 트립 출발에 각각 같은 시각 범위를 적용했다. 거리 지표는 문제지의 거점별 서비스 반경 안에서 계산했다.

지급액은 주문당 지표에 05, 접속시간당 지표에 05b를 사용했다. 05b 지급액 합계에서 05 배분액 합계를 빼면 P1 {value("P1", "rider_log_minus_order_alloc_krw"):,.0f}원({value("P1", "rider_log_minus_order_alloc_krw")/1e8:.8f}억 원), P2 {value("P2", "rider_log_minus_order_alloc_krw"):,.0f}원({value("P2", "rider_log_minus_order_alloc_krw")/1e8:.8f}억 원)이다. 차이의 원인은 입력 자료에서 확인되지 않았다. 출처: pilot_kpi.csv의 rider_log_minus_order_alloc_krw.
"""
    (output / "pilot_diagnosis.md").write_text(report, encoding="utf-8")
    return pd.DataFrame(comparisons)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    cfg = load_config(args.config)
    result, daily, riders, hourly, distance = analyze(cfg)
    output = ROOT / cfg["paths"]["outputs"]
    output.mkdir(parents=True, exist_ok=True)
    result.to_csv(output / "pilot_kpi.csv", index=False, encoding=cfg["io"]["encoding"])
    comparisons = make_reports(result, cfg, output)
    plot_all(cfg, result, daily, riders, hourly, distance)
    print(comparisons.to_string(index=False))
    print(f"KPI {len(result):,}행. 주문·트립 분할, 05b 대조, 배송시간 구성합, 거리구간 주문 합 검산 통과.")


if __name__ == "__main__":
    main()
