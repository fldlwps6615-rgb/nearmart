"""저장된 대안으로 제공 양식 T1~T4를 생성한다."""

import argparse
from copy import deepcopy
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import perf_counter
import json
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file
from src.network.model import read_output, require
from src.submission.evaluation import (MARKETS, assignment, benchmark_hours, city_metrics, evaluate,
                                       feedback_models, plan_input, write_csv)

TEMPLATES = ("T1_network_plan.csv", "T2_dong_assignment.csv", "T3_benchmark_dispatch.csv", "T4_kpi_summary.csv")


def benchmark(config):
    output = ROOT / config["paths"]["outputs"]
    dispatch = output / "dispatch"
    status = load_config(dispatch / "run_status.json")
    require(status["status"] == "complete" and status["report_ready"], "독립 검증을 완료한 T3가 필요합니다")
    selection = load_config(dispatch / "selection.json")
    require(set(selection) == {"BM1", "BM2", "BM3"}, "BM 선택안 누락")
    t3 = read_output(ROOT / "report" / TEMPLATES[2])
    source = load_file("16_vrp_benchmark_orders.csv", config=config)
    keys = ["instance", "order_id"]
    joined = t3.merge(source[[*keys, "order_min"]], on=keys, how="outer", validate="one_to_one", indicator=True)
    require(joined._merge.eq("both").all(), "T3 벤치마크 원주문 키 불일치")
    summaries = read_output(output / "bm_kpi_recalculated.csv").set_index(["instance", "policy"], verify_integrity=True)
    sla = config["dispatch"]["sla_min"]
    tolerance = config["dispatch"]["verification_tolerance"]
    metrics = {}
    for instance, policy in selection.items():
        saved = read_output(dispatch / instance / policy / "T3.csv")
        pd.testing.assert_frame_equal(t3[t3.instance.eq(instance)].reset_index(drop=True), saved)
        row = summaries.loc[(instance, policy)]
        orders = joined[joined.instance.eq(instance)]
        ontime = (orders.delivered_min - orders.order_min).le(sla + tolerance).mean()
        require(row.feasible and len(orders) == row.orders and np.isclose(ontime, row.on_time)
                and np.isclose(row.total_cost_krw / row.orders, row.cost_per_order), f"{instance} T3/T4 KPI 검산 실패")
        metrics.update({f"{instance}_ontime_pct": ontime * 100,
                        f"{instance}_cost_per_order_krw": row.cost_per_order})
    return t3, metrics


def build(config, alternative, version):
    rows, models = feedback_models(config, version)
    saved = rows.loc[alternative]
    base = saved.loc["base"]
    model = models[base["T"]]
    t1, plan = plan_input(model, f"{alternative}_{version}")
    kpis = {}
    for market in MARKETS:
        annual, kpi, _, state, yearly = evaluate(model, t1, plan, model.radius, market)
        expected = saved.loc[market]
        require(np.isclose(kpi["npv"], expected.npv, rtol=0, atol=0.05)
                and np.isclose(kpi["max_funding"], expected.max_funding, rtol=0, atol=0.05),
                f"{alternative} {version} {market} 저장 NPV·최대 자금과 재계산값 불일치")
        kpis[f"NPV_{market}_억원"] = kpi["npv_eok"]
        if market == "base":
            base_annual, base_state, base_yearly, base_kpi = annual, state, yearly, kpi
    t2 = assignment(model, base_state, base_yearly)
    hourly, _ = benchmark_hours(config, version, model)
    city = city_metrics(model, base_annual, base_yearly, t2, hourly)
    t3, bm = benchmark(config)
    kpis.update(peak_funding_need_억원=base_kpi["max_funding_eok"],
                discounted_payback_year=base_kpi["payback_year"],
                ebitda_breakeven_year=base_kpi["ebitda_positive_year"],
                ontime_rate_city_2031_pct=city["on_time"] * 100,
                avg_delivery_min_city_2031=city["avg_delivery_min"],
                delivery_cost_per_order_2031_krw=city["delivery_cost_per_order"],
                contract_rider_hourly_earning_2031_krw=city["contract_hourly"], **bm)
    for year in (2027, 2032, 2037, 2042):
        kpis[f"hubs_{year}"] = int(base_yearly.loc[year, "active_hubs"])
    for year in (2027, 2037):
        kpis[f"household_coverage_{year}_pct"] = base_yearly.loc[year, "coverage"] * 100
    for year in (2027, 2031, 2036, 2046):
        kpis[f"orders_per_day_{year}"] = base_yearly.loc[year, "orders_per_day"]
    return (t1, t2, t3), kpis


def generate(config, alternative, version, destination):
    started = perf_counter()
    frames, metrics = build(config, alternative, version)
    templates = [read_output(ROOT / "data" / "submission_templates" / name) for name in TEMPLATES]
    keys = templates[-1].kpi
    require(keys.is_unique and set(keys) == set(metrics), "T4 KPI 키가 제공 양식과 다릅니다")
    t4 = pd.DataFrame({"kpi": keys, "value": keys.map(metrics)})
    require(t4.value.notna().all(), "T4 KPI 값이 비어 있습니다")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="templates_", dir=destination.parent) as temp:
        stage = Path(temp)
        for name, template, frame in zip(TEMPLATES, templates, (*frames, t4), strict=True):
            require(list(frame) == list(template), f"{name} 컬럼 또는 순서가 제공 양식과 다릅니다")
            write_csv(stage / name, frame)
            saved = read_output(stage / name)
            require(len(saved) == len(frame) and list(saved) == list(template), f"{name} 저장 후 검산 실패")
        destination.mkdir(exist_ok=True)
        for name in TEMPLATES:
            (stage / name).replace(destination / name)
    counts = {name.split("_")[0]: len(frame) for name, frame in zip(TEMPLATES, (*frames, t4), strict=True)}
    print(f"{alternative} {version} | {destination} | {counts} | {perf_counter() - started:.2f}초", flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alt", required=True)
    parser.add_argument("--version", required=True, choices=("v2", "v3", "v4"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", args.alt):
        parser.error("대안 코드는 B, B2, D15처럼 입력하세요.")
    destination = args.output_dir or ROOT / "report" / f"{args.alt}_{args.version}"
    if destination.resolve().is_relative_to(ROOT / "data"):
        parser.error("data/ 밖의 출력 경로가 필요합니다.")
    config = deepcopy(load_config(args.config))
    config["finance"]["issues_log_path"] = str(destination.parent / "submission_input_errors.csv")
    try:
        generate(config, args.alt, args.version, destination)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"제출 양식 생성 실패: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
