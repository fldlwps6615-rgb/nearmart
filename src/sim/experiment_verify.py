"""저장된 ⑤ 실험 결과를 반복 및 인력 계획으로 재검산."""

import json
import math

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_file
from src.dispatch import DispatchInputs
from .experiment_inputs import grid
from .experiment_report import CAP_KEY, COST, KEY, LATE_KEY


def verify_saved(config, reduced):
    output = ROOT / config["paths"]["outputs"]

    def read(name):
        return pd.read_csv(output / f"sim_{name}.csv", encoding="utf-8-sig", dtype={"site_id": str})

    cost, capacity, late = read("cost_curve"), read("capacity"), read("late_rate")
    runs, plans, late_daily = read("experiment_repetitions"), read("experiment_plans"), read("late_rate_repetitions")
    results = []

    def check(name, ok, detail):
        results.append(dict(check=name, passed=bool(ok), detail=detail))

    settings = config["sim"]["experiment"]
    expected = {tuple(getattr(case, key) for key in KEY) for case in grid(config, reduced)}
    for name, table in [("비용", cost), ("인력", plans)]:
        check(f"{name} 조합 일치", len(table) == len(expected) and set(table[KEY].itertuples(index=False, name=None)) == expected,
              f"{len(table)}행, 예정 {len(expected)}개")
    reps = config["sim"]["repetitions"]
    check("20회 반복 키", runs.groupby(KEY).size().eq(reps).all() and not runs.duplicated([*KEY, "repetition"]).any()
          and len(runs) == len(expected) * reps, f"{len(runs)}회")
    complete = runs.loc[runs.completed]
    check("완료 반복 주문 보존", complete.delivered_orders.eq(complete.orders_per_day).all(),
          f"배송 {int(complete.delivered_orders.sum()):,}건")
    corrected = complete.late_orders_corrected.ge(complete.late_orders).all()
    corrected &= np.allclose(complete.ontime_rate_corrected, 1 - complete.late_orders_corrected / complete.orders_per_day)
    corrected &= np.allclose(complete.ontime_rate, 1 - complete.late_orders / complete.orders_per_day)
    corrected &= np.allclose(complete.avg_delivery_min_corrected - complete.avg_delivery_min, settings["correction_mean_min"])
    corrected &= np.allclose(complete.p90_delivery_min_corrected - complete.p90_delivery_min, settings["correction_p90_min"])
    check("보정과 SLA 재판정", corrected, "주문 수·지연 수·평균 +1.2분·90분위 +2.7분")
    check("라이더 비용 분해", np.allclose(complete.rider_cost_per_order,
          complete.contract_rider_cost_per_order_krw + complete.employed_rider_cost_per_order_krw),
          "위탁+직고용 비용, 전체 전달 주문 분모")
    raw = complete.groupby(KEY)[COST]
    stored = cost.set_index(KEY)
    for label, rederived, suffix in [("평균", raw.mean(), ""), ("5분위", raw.quantile(.05), "_p05"),
                                      ("95분위", raw.quantile(.95), "_p95")]:
        actual = stored.reindex(rederived.index)[[m + suffix for m in COST]]
        check(f"비용표 {label}", np.allclose(actual.to_numpy(), rederived.to_numpy(), equal_nan=True), f"{len(rederived)}조합")
    recovered = runs.get("recovery_used", pd.Series(False, index=runs.index)).eq(True)
    initial = runs.loc[~recovered]
    grouped = initial.groupby(KEY).agg(n=("completed", "sum"), rules=("rule_violations", "sum"),
                                     bad_supply=("supply_ok", lambda x: x.eq(False).sum()))
    grouped = grouped.reindex(stored.index, fill_value=0)
    feasible = grouped.n.eq(reps) & grouped.rules.eq(0) & grouped.bad_supply.eq(0)
    check("실행 가능 인력 판정", stored.staffing_feasible.reindex(feasible.index).equals(feasible.rename("staffing_feasible")),
          f"{int(feasible.sum())}개")
    target = config["sim"]["ontime_target"]
    valid = True
    for row in capacity.itertuples(index=False):
        subset = cost
        for k in CAP_KEY:
            subset = subset.loc[subset[k].eq(getattr(row, k))]
        for metric, max_field in (("ontime_rate", "max_orders_per_day_raw"),
                                  ("ontime_rate_corrected", "max_orders_per_day")):
            n = subset.loc[subset.staffing_feasible & subset[metric].ge(target), "orders_per_day"].max()
            value = getattr(row, max_field)
            valid &= bool(n == value or (pd.isna(n) and pd.isna(value)))
    check("용량 최대값", valid and not capacity.duplicated(CAP_KEY).any(), f"{len(capacity)}개 그룹")
    eligible = int(cost.loc[cost.staffing_feasible, "orders_per_day"].sum() * reps)
    late_valid = not late.duplicated(LATE_KEY).any() and late.n_orders.sum() == eligible
    late_valid &= late.late_rate_corrected.ge(late.late_rate - 1e-12).all()
    late_valid &= np.allclose(late.avg_delivery_min_corrected - late.avg_delivery_min, settings["correction_mean_min"])
    expected_late = late_daily.groupby(LATE_KEY)[["late_rate", "avg_delivery_min", "late_rate_corrected",
                                                 "avg_delivery_min_corrected"]].mean()
    late_valid &= np.allclose(late.set_index(LATE_KEY).reindex(expected_late.index)[expected_late.columns], expected_late)
    check("이동구간별 지연", late_valid, f"{len(late)}구간, {eligible:,}건")
    inputs = DispatchInputs(config)
    hubs = load_file("04_hub_types.csv", config=config).set_index("hub_type")
    site_table = inputs.sites.set_index("site_id")
    legal = True
    for row in plans.itertuples(index=False):
        plan = json.loads(row.plan_json)
        meta = dict(rider_area_class=row.rider_class, weather=settings["days"][row.day_type]["weather"])
        legal &= row.bike_bays == site_table.loc[row.site_id, "bike_bays"] and plan["policy"] == "P2"
        legal &= max(plan["pickers"].values()) <= hubs.loc[row.hub_type, "max_pickers"]
        legal &= all(0 <= n <= inputs.cap(meta, int(h)) for h, n in plan["contract"].items())
        legal &= all(b["hours"] in (4, 8) and b["count"] > 0 for b in plan["employed_blocks"])
    check("계획 상한·블록 검산", legal, f"{len(plans)}개 인력안")
    if "recovery_used" in runs:
        repair = runs.loc[recovered]
        check("전체 배송 완료", runs.completed.all() and complete.delivered_orders.sum() == runs.orders_per_day.sum(),
              f"{int(complete.delivered_orders.sum()):,}건")
        check("잔여 근무 및 원실패 추적", repair.recovery_staff_hours.gt(0).all() and
              repair.original_unserved_orders.gt(0).all() and repair.original_failure.notna().all(),
              f"{len(repair):,}회")
    return pd.DataFrame(results)
