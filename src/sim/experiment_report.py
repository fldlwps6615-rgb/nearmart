"""반복 결과를 ③·⑥ 입력과 검증표로 집계한다."""

from dataclasses import asdict
import json
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT


KEY = ["hub_type", "rider_class", "radius_min", "orders_per_day", "day_type"]
DELIVERY = ["avg_delivery_min", "p90_delivery_min", "ontime_rate"]
COST = ["rider_cost_per_order", "picker_cost_per_order", "employed_share", "contract_hourly_income",
        *DELIVERY, *(f"{name}_corrected" for name in DELIVERY),
        "contract_rider_cost_per_order_krw", "employed_rider_cost_per_order_krw", "external_quick_share"]
CAP_KEY = ["hub_type", "rider_class", "day_type", "radius_min"]
LATE_KEY = ["radius_min", "travel_min_bin", "day_type"]


def interval_summary(values, columns):
    summary = {}
    for metric in columns:
        series = values[metric]
        summary[metric] = series.mean()
        summary[f"{metric}_p05"] = series.quantile(.05)
        summary[f"{metric}_p95"] = series.quantile(.95)
    return summary


def aggregate_cases(config, results):
    repetitions = config["sim"]["repetitions"]
    target = config["sim"]["ontime_target"]
    runs, costs, plans, all_bins = [], [], [], []
    results = sorted(results, key=lambda item: tuple(getattr(item[0], key) for key in KEY))
    for case, records, bins, metadata, plan in results:
        observed = pd.DataFrame(records)
        complete = observed.loc[observed.completed]
        if len(observed) != repetitions or not observed.repetition.is_unique:
            raise ValueError(f"{case}: 반복 누락 또는 중복")
        if len(complete) and not complete.delivered_orders.eq(case.orders_per_day).all():
            raise ValueError(f"{case}: 완료 반복의 주문 합계 불일치")
        rule_count = int(complete.rule_violations.gt(0).sum()) if len(complete) else 0
        supply_count = int((~complete.supply_ok.astype(bool)).sum()) if len(complete) else 0
        feasible = len(complete) == repetitions and rule_count == supply_count == 0
        base = asdict(case)
        daily = complete.reindex(columns=COST)
        summary = interval_summary(daily, COST)
        costs.append(dict(**base, **summary, complete_repetitions=len(complete), rule_violation_repetitions=rule_count,
                          supply_shortfall_repetitions=supply_count, staffing_feasible=feasible,
                          service_ok_raw=bool(feasible and summary["ontime_rate"] >= target),
                          service_ok_corrected=bool(feasible and summary["ontime_rate_corrected"] >= target)))
        runs.extend(dict(**base, **row) for row in records)
        plans.append(dict(**metadata, plan_json=json.dumps(plan, ensure_ascii=False, sort_keys=True)))
        if feasible:
            if len(bins) != repetitions:
                raise ValueError(f"{case}: 지연 구간 집계 누락")
            for rep, counts in enumerate(bins, 1):
                if counts[:, 0].sum() != case.orders_per_day:
                    raise ValueError(f"{case}: 이동시간 구간별 주문 합계 불일치")
                all_bins.append((case.radius_min, case.day_type, rep, counts))
    return pd.DataFrame(runs), pd.DataFrame(costs), pd.DataFrame(plans), all_bins


def capacity_table(cost, target):
    rows = []
    for key, group in cost.groupby(CAP_KEY, sort=True):
        raw = group.loc[group.service_ok_raw, "orders_per_day"]
        corrected = group.loc[group.service_ok_corrected, "orders_per_day"]
        best = corrected.max() if len(corrected) else np.nan
        rows.append(dict(zip(CAP_KEY, key), max_orders_per_day=best,
                         max_orders_per_day_raw=raw.max() if len(raw) else np.nan,
                         ontime_target=target, tested_levels=len(group),
                         lowest_tested_orders=group.orders_per_day.min(), highest_tested_orders=group.orders_per_day.max(),
                         corrected_pass_at_grid_max=bool(best == group.orders_per_day.max()),
                         staffing_feasible_levels=int(group.staffing_feasible.sum())))
    return pd.DataFrame(rows)


def late_table(entries, settings):
    daily = {}
    for radius, day, repetition, array in entries:
        key = (radius, day, repetition)
        if key in daily:
            daily[key] += array
        else:
            daily[key] = array.copy()
    raw = []
    delta = settings["correction_mean_min"]
    edges = settings["travel_bin_edges"]
    for (radius, day, repeat), counts in daily.items():
        for (start, end), (orders, duration, late, corrected) in zip(zip(edges, edges[1:]), counts):
            if orders:
                raw.append(dict(radius_min=radius, day_type=day, repetition=repeat,
                                travel_min_bin=f"{start}-{end}", n_orders=int(orders),
                                late_rate=late / orders, avg_delivery_min=duration / orders,
                                late_rate_corrected=corrected / orders, avg_delivery_min_corrected=duration / orders + delta))
    frame = pd.DataFrame(raw)
    metrics = ["late_rate", "avg_delivery_min", "late_rate_corrected", "avg_delivery_min_corrected"]
    rows = []
    for key, values in frame.groupby(LATE_KEY, sort=True):
        rows.append(dict(zip(LATE_KEY, key), **interval_summary(values, metrics),
                         n_orders=int(values.n_orders.sum()), repetitions=len(values)))
    return pd.DataFrame(rows), frame


def compare_trends(cost, capacity):
    records = []

    def monotonic(name, keys, dimension, metric, increasing):
        for key, group in cost.groupby(keys, sort=True):
            group = group.sort_values(dimension)
            for before, after in zip(group.iloc[:-1].itertuples(), group.iloc[1:].itertuples()):
                if not (before.staffing_feasible and after.staffing_feasible):
                    continue
                a, b = getattr(before, metric), getattr(after, metric)
                records.append(dict(check=name, group="|".join(map(str, key)),
                                    level_from=getattr(before, dimension), level_to=getattr(after, dimension),
                                    metric=metric, before=a, after=b, difference=b-a,
                                    matches=(b >= a - 1e-10 if increasing else b <= a + 1e-10)))

    monotonic("주문량 증가", CAP_KEY, "orders_per_day", "ontime_rate_corrected", False)
    monotonic("권역 확대", ["hub_type", "rider_class", "day_type", "orders_per_day"],
              "radius_min", "rider_cost_per_order", True)
    comparable = cost.loc[cost.staffing_feasible].pivot(index=["hub_type", "rider_class", "radius_min", "orders_per_day"],
                                                       columns="day_type", values="ontime_rate_corrected")
    if {"weekday_clear", "weekday_rain"} <= set(comparable.columns):
        for key, row in comparable.dropna(subset=["weekday_clear", "weekday_rain"]).iterrows():
            a, b = row.weekday_clear, row.weekday_rain
            records.append(dict(check="평일 비", group="|".join(map(str, key)), level_from="clear", level_to="rain",
                                metric="ontime_rate_corrected", before=a, after=b, difference=b-a,
                                matches=bool(b <= a + 1e-10)))
    return pd.DataFrame(records)


def expected_ranges(config, cost, capacity):
    ex = config["sim"]["experiment"]["expectations"]
    sample = cost.query("hub_type == 'M' and rider_class == 'A' and day_type == 'weekday_clear'")
    rows = []
    for radius in (8, 12, 15, 20):
        row = capacity.query("hub_type == 'M' and rider_class == 'A' and day_type == 'weekday_clear' and radius_min == @radius").iloc[0]
        n = row.max_orders_per_day
        rows.append(dict(check="M형 A등급 평일 맑음 상한", radius_min=radius, metric="max_orders_per_day", value=n,
                         reference_low=ex["m_capacity"][0], reference_high=ex["m_capacity"][1],
                         matches=bool(ex["m_capacity"][0] <= n <= ex["m_capacity"][1]), staffing_feasible=pd.notna(n)))
        grid = sample.loc[sample.radius_min.eq(radius) & sample.order_level.eq(ex["cost_comparison_order_level"])]
        if len(grid):
            case = grid.iloc[0]
            baseline = ex["rider_cost_short_krw"] if radius <= 12 else ex["rider_cost_long_min_krw"]
            rows.append(dict(check="M형 A등급 평일 맑음 라이더 비용", radius_min=radius, metric="rider_cost_per_order",
                             value=case.rider_cost_per_order, reference_low=baseline, reference_high=np.nan,
                             matches=(bool(case.rider_cost_per_order >= baseline) if radius > 12 and case.staffing_feasible else np.nan),
                             staffing_feasible=case.staffing_feasible, orders_per_day=case.orders_per_day,
                             employed_share=case.employed_share))
    return pd.DataFrame(rows)


def plot(config, runs, cost, capacity, figures):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import PercentFormatter

    font = config["pilot_diagnosis"]["font_path"]
    font_manager.fontManager.addfont(font)
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=font).get_name(),
                         "axes.spines.top": False, "axes.spines.right": False, "axes.unicode_minus": False})
    selected = runs.query("hub_type == 'M' and rider_class == 'A' and day_type == 'weekday_clear' and completed")
    radii = sorted(selected.radius_min.unique())
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
    for axis, metric, title in zip(axes.flat,
            ["avg_delivery_min_corrected", "p90_delivery_min_corrected", "ontime_rate_corrected", "rider_cost_per_order"],
            ["평균 배송시간 (분)", "90분위 배송시간 (분)", "정시율", "라이더 비용 (원/건)"]):
        data = [selected.loc[selected.radius_min.eq(r) & selected.order_level.eq(1.0), metric] for r in radii]
        axis.boxplot(data, tick_labels=[str(r) for r in radii], whis=(5, 95), showmeans=True, showfliers=False,
                     patch_artist=True, boxprops={"facecolor": "#86b9b3"}, medianprops={"color": "#245451"})
        axis.set(title=title, xlabel="피크 이동시간 기준 반경 (분)")
        axis.grid(axis="y", alpha=.2)
        if metric == "ontime_rate_corrected":
            axis.yaxis.set_major_formatter(PercentFormatter(1))
            axis.axhline(config["sim"]["ontime_target"], color="#a85436", linestyle="--", linewidth=1)
    fig.suptitle("M형 · A등급 · 평일 맑음 · 1차 상한 주문량", fontsize=16, fontweight="bold")
    fig.savefig(figures / "sim_experiment_boxplots.png", dpi=150, facecolor="white")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), layout="constrained")
    sample = cost.query("hub_type == 'M' and rider_class == 'A' and day_type == 'weekday_clear'")
    for radius, group in sample.groupby("radius_min"):
        group = group.sort_values("orders_per_day")
        usable = group.loc[group.staffing_feasible]
        for axis, metric in zip(axes, ["rider_cost_per_order", "employed_share"]):
            axis.plot(usable.orders_per_day, usable[metric], marker="o", label=f"{radius}분")
    axes[0].set(ylabel="라이더 비용 (원/건)", xlabel="일 주문 (건)")
    axes[1].set(ylabel="직고용 배송 비중", xlabel="일 주문 (건)")
    axes[1].yaxis.set_major_formatter(PercentFormatter(1))
    for axis in axes:
        axis.grid(alpha=.2)
        axis.legend(title="권역 반경")
    fig.suptitle("M형 · A등급 · 평일 맑음 인력 제약 충족 조합", fontsize=15, fontweight="bold")
    fig.savefig(figures / "sim_cost_and_employment.png", dpi=150, facecolor="white")
    plt.close(fig)


def write_results(config, results, started, reduced, full_estimate, reduced_estimate, workers):
    sim = config["sim"]
    settings = sim["experiment"]
    outputs = ROOT / config["paths"]["outputs"]
    figures = ROOT / config["pilot_diagnosis"]["figures_dir"]
    runs, cost, plans, bins = aggregate_cases(config, results)
    capacity = capacity_table(cost, sim["ontime_target"])
    late, late_daily = late_table(bins, settings)
    trends = compare_trends(cost, capacity)
    ranges = expected_ranges(config, cost, capacity)
    for amount in ("rider_cost_per_order", "picker_cost_per_order", "contract_hourly_income",
                   "contract_rider_cost_per_order_krw", "employed_rider_cost_per_order_krw"):
        for suffix in ("", "_p05", "_p95"):
            cost[f"{amount}{suffix}_eok"] = cost[f"{amount}{suffix}"] / 1e8
    ordered = [*KEY, *COST]
    cost = cost.reindex(columns=ordered + [c for c in cost if c not in ordered])
    late = late.reindex(columns=[*LATE_KEY, "late_rate", "avg_delivery_min", "late_rate_corrected",
                                "avg_delivery_min_corrected", *[c for c in late if c not in {
                                    *LATE_KEY, "late_rate", "avg_delivery_min", "late_rate_corrected", "avg_delivery_min_corrected"}]])
    tables = dict(sim_capacity=capacity, sim_cost_curve=cost, sim_late_rate=late,
                  sim_experiment_repetitions=runs, sim_experiment_plans=plans, sim_late_rate_repetitions=late_daily,
                  sim_trend_check=trends, sim_expectation_check=ranges)
    for name, table in tables.items():
        table.to_csv(outputs / f"{name}.csv", index=False, encoding="utf-8-sig")
    plot(config, runs, cost, capacity, figures)
    from .experiment_verify import verify_saved
    checks = verify_saved(config, reduced)
    checks.to_csv(outputs / "sim_experiment_verification.csv", index=False, encoding="utf-8-sig")
    elapsed = perf_counter() - started
    runtime = pd.DataFrame([dict(grid="reduced" if reduced else "full", workers=workers, combinations=len(results),
                                 repetitions_per_case=sim["repetitions"], attempted_repetitions=len(runs),
                                 completed_repetitions=int(runs.completed.sum()), feasible_cases=int(cost.staffing_feasible.sum()),
                                 estimated_full_seconds=full_estimate, estimated_reduced_seconds=reduced_estimate,
                                 execution_seconds=elapsed, runtime_limit_seconds=sim["runtime_limit_sec"],
                                 checks_passed=int(checks.passed.sum()), checks_total=len(checks))])
    runtime.to_csv(outputs / "sim_experiment_runtime.csv", index=False, encoding="utf-8-sig")
    print(runtime.to_string(index=False), flush=True)
    if not checks.passed.all():
        raise ValueError("저장 결과 검산 실패: outputs/sim_experiment_verification.csv")
