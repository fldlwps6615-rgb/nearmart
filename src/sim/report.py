"""재현 비교표, 반복별 검산 자료 및 상자그림을 저장한다."""

from pathlib import Path
from time import perf_counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator, PercentFormatter, StrMethodFormatter
import numpy as np
import pandas as pd

from src.common.io import ROOT

from .replay import ACTUAL, COMPONENTS, METRICS, distribution, require

DIAGNOSTICS = (*COMPONENTS, "contract_hours", "contract_pay_krw", "external_orders",
               "all_avg_delivery_min", "all_p90_delivery_min", "all_ontime_rate")


def explanation(metric, actual, simulated):
    if metric == "orders_per_day":
        return "실측 일평균 주문을 정수로 반올림해 매회 적용했다. 실제 범위는 일별 수요 변동을 포함한다."
    if metric == "avg_delivery_min":
        changes = simulated[list(COMPONENTS)].mean() - actual[list(COMPONENTS)].mean()
        labels = ["피킹 대기", "피킹", "출발 대기", "출발~전달"]
        return "단계별 평균 차이(분) " + ", ".join(f"{label} {delta:+.2f}" for label, delta in zip(labels, changes))
    if metric == "p90_delivery_min":
        return "시간대별 주문과 반경 내 고객 위치를 다시 추출했다. 평균 배송시간 행에 구간별 차이를 제시했다. 90분위 차이의 요인별 기여는 미측정이다."
    if metric == "ontime_rate":
        return (f"선택된 실측일의 최대 일별 지연율 {1-actual.ontime_rate.min():.2%}, "
                f"재현 {simulated.late_orders.sum()}건 지연. 외부 퀵은 양쪽 배송 지표에서 제외했다.")
    if metric == "orders_per_trip":
        observed = actual.trips.sum() / actual.trip_orders.sum()
        modeled = simulated.trips.sum() / simulated.trip_orders.sum()
        return (f"내부 주문당 트립 수 실측 {observed:.3f}, 재현 {modeled:.3f}. "
                "시간대별 주문·고객 조합을 재추출하고 P0의 즉시 출발과 근거리 묶음 규칙을 적용했다.")
    if metric != "contract_hourly_income":
        return ""
    actual_pay = actual.contract_pay_krw.sum() / actual.trip_orders.sum()
    sim_pay = simulated.contract_pay_krw.sum() / simulated.trip_orders.sum()
    return (f"내부 주문당 지급액 실측 {actual_pay:,.0f}원, 재현 {sim_pay:,.0f}원. "
            f"일평균 접속시간 실측 {actual.contract_hours.mean():.2f}, 재현 {simulated.contract_hours.mean():.2f}시간. "
            "실측 지급은 05b, 재현은 15번 표기 단가다.")


def compare(frames):
    rows = []
    for hub, (actual, simulated, _) in frames.items():
        matched = actual.set_index("date").loc[simulated.matched_date].reset_index(drop=True)
        require(np.array_equal(matched.orders_per_day, simulated.orders_per_day), "날짜별 주문 수 대조 실패")
        require(np.allclose(matched.contract_hours, simulated.observed_contract_hours), "05b 접속시간 대조 실패")
        actual_runs = int(simulated.contract_source.eq(ACTUAL).sum())
        counts = dict(contract_source="+".join(simulated.contract_source.unique()),
                      actual_roster_runs=actual_runs, fallback_roster_runs=len(simulated)-actual_runs,
                      bike_bays=int(simulated.bike_bays.iloc[0]), bay_exceeded_runs=int(simulated.bay_violation.sum()),
                      supply_threshold_krw=simulated.supply_threshold_krw.iloc[0],
                      income_shortfall_runs=int((~simulated.supply_ok).sum()),
                      supply_cap_exceeded_runs=int(simulated.supply_cap_violation_hours.gt(0).sum()))
        for metric in (*METRICS, *DIAGNOSTICS):
            observed = distribution(actual[metric])
            replayed = distribution(simulated[metric])
            row = dict(hub_id=hub, metric=metric, actual_days=len(actual), simulation_runs=len(simulated), **counts,
                       **{f"actual_{key}": value for key, value in observed.items()},
                       **{f"simulated_{key}": value for key, value in replayed.items()},
                       simulated_minus_actual=replayed["mean"]-observed["mean"],
                       actual_matched_mean=matched[metric].mean(),
                       simulated_minus_actual_matched=replayed["mean"]-matched[metric].mean(),
                       reason=explanation(metric, matched, simulated))
            if metric in {"contract_hourly_income", "contract_pay_krw"}:
                row.update({f"{key}_eok": row[key]/1e8 for key in (
                    "actual_mean", "actual_p05", "actual_p95", "actual_matched_mean", "simulated_mean",
                    "simulated_p05", "simulated_p95", "simulated_minus_actual", "simulated_minus_actual_matched")})
            rows.append(row)
    return pd.DataFrame(rows)


def boxplots(actual, simulated, config):
    style = config["pilot_diagnosis"]
    font = Path(style["font_path"])
    font_manager.fontManager.addfont(font)
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False})
    hubs = list(actual.hub_id.unique())
    values = [(frame, hub) for hub in hubs for frame in (actual, simulated)]
    labels = [f"{hub}\n{source}" for hub in hubs for source in ("실측", "재현")]
    fig, axes = plt.subplots(2, 3, figsize=(12, 7), layout="constrained")
    fig.suptitle("평일 맑음 파일럿 재현", fontsize=17, fontweight="bold")
    for axis, (metric, (label, unit)) in zip(axes.flat, METRICS.items()):
        series = [frame.loc[frame.hub_id.eq(hub), metric].dropna().to_numpy() for frame, hub in values]
        boxes = axis.boxplot(series, whis=(5, 95), patch_artist=True, tick_labels=labels,
                             showmeans=True, meanprops={"marker": "D", "markersize": 4, "markerfacecolor": "#222b32"},
                             medianprops={"color": "#252c34"}, flierprops={"markersize": 3, "alpha": .5})
        for patch, color in zip(boxes["boxes"], ["#c4d1d7", "#68a6a1"] * len(hubs)):
            patch.set_facecolor(color)
        axis.set(title=label, ylabel=unit)
        axis.grid(axis="y", alpha=.2)
        axis.set_axisbelow(True)
        if metric == "ontime_rate":
            axis.yaxis.set_major_formatter(PercentFormatter(1, decimals=1))
            axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
            axis.set_ylim(max(0, min(map(np.min, series)) - .005), 1.0005)
        if metric == "contract_hourly_income":
            axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    fig.supxlabel("상자 25~75% · 수염 5~95% · 마름모 평균", fontsize=10)
    target = ROOT / style["figures_dir"] / "sim_replay_boxplot.png"
    fig.savefig(target, dpi=160, facecolor="white")
    plt.close(fig)
    require(target.stat().st_size > 0, "재현 상자그림 저장 실패")


def record_issues(path, audit, encoding):
    issues = pd.read_csv(path, encoding=encoding)
    rules = (
        ("OUTSIDE_RADIUS", "location_outside_radius", "도로거리 반경 밖 위치",
         "생성 위치 표본에서 제외. 실제 수요·성과 비교와 주문 속성 모집단에 유지"),
        ("WEIGHT_UNRESOLVED", "unresolved_weight", "중량 분석 부적격",
         "기승인 규칙대로 실제 수요에 포함하고 생성 속성 표본에서 제외"),
        ("DELIVERY_MISSING", "missing_delivery_rows", "실제 전달시각 결측",
         "기승인 규칙대로 실제 수요에 포함하고 배송시간·정시율 분모에서 제외"),
        ("QUICK_LE_35KG", "observed_external_le_35kg", "35kg 이하의 실측 외부 퀵",
         "실제는 외부 퀵 로그 그대로 분류. 생성 주문의 배차에는 35kg 초과 규칙 적용"),
    )
    records = pd.DataFrame([
        dict(rule_id=f"SIM_REPLAY_{rule}", file="05_pilot_orders.csv", check=check,
             n_rows=getattr(row, count), example=row.hub_id, action=action)
        for rule, count, check, action in rules for row in audit.itertuples()
    ])
    updated = pd.concat([issues[~issues.rule_id.isin(records.rule_id)], records], ignore_index=True)
    updated.to_csv(path, index=False, encoding=encoding)


def write_results(config, outputs, frames, started):
    encoding = "utf-8-sig"
    simulated = pd.concat([values[1] for values in frames.values()], ignore_index=True)
    actual = pd.concat([values[0] for values in frames.values()], ignore_index=True)
    comparison = compare(frames)
    expected = config["sim"]["repetitions"]
    require(simulated.groupby("hub_id").size().eq(expected).all(), "반복 수 검산 실패")
    require(len(comparison) == len(frames)*(len(METRICS) + len(DIAGNOSTICS))
            and not comparison.duplicated(["hub_id", "metric"]).any(),
            "비교표 키·행 수 검산 실패")
    boxplots(actual, simulated, config)
    output = outputs / "sim_replay_check.csv"
    simulated.to_csv(outputs / "sim_replay_repetitions.csv", index=False, encoding=encoding)
    actual.to_csv(outputs / "sim_replay_observed_days.csv", index=False, encoding=encoding)
    comparison.to_csv(output, index=False, encoding=encoding)
    saved = pd.read_csv(output, encoding=encoding)
    require(len(saved) == len(comparison) and np.allclose(saved.simulated_mean, comparison.simulated_mean)
            and np.allclose(saved.simulated_minus_actual, saved.simulated_mean-saved.actual_mean), "저장된 비교표 검산 실패")
    audit = pd.DataFrame([
        dict(hub_id=hub, **info, repetitions=len(runs), simulated_orders=runs.orders_per_day.sum(),
             simulated_external_orders=runs.external_orders.sum(), simulated_late_orders=runs.all_late_orders.sum(),
             bay_peak_min=runs.bay_max.min(), bay_peak_max=runs.bay_max.max(),
             bike_bays=runs.bike_bays.iloc[0], bay_exceeded_runs=int(runs.bay_violation.sum()),
             supply_threshold_krw=runs.supply_threshold_krw.iloc[0], supply_met_runs=int(runs.supply_ok.sum()),
             supply_cap_exceeded_runs=int(runs.supply_cap_violation_hours.gt(0).sum()))
        for hub, (_, runs, info) in frames.items()
    ])
    record_issues(outputs / "data_issues_log.csv", audit, encoding)
    audit["execution_seconds"] = perf_counter() - started
    audit.to_csv(outputs / "sim_replay_audit.csv", index=False, encoding=encoding)
    print(f"{len(saved)}행 비교표 저장 및 재읽기 검산 완료, 총 {audit.execution_seconds.iloc[0]:.2f}초", flush=True)
