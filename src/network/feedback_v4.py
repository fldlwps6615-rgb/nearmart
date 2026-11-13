"""08-3 실행: python -B -m src.network.feedback_v4 --config config_feedback_v4.json."""
import argparse
from pathlib import Path
from time import monotonic

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.network.feedback import FeedbackModel, compare_row, existing_plan, prepare, verify_updated
from src.network.feedback_basis import DAY_TYPES, calendar_counts
from src.network.feedback_v3 import KIND, UNITS, add_eok, break_even, choose_plans, digest, publish, search_all
from src.network.model import NetworkModel, read_output, require
from src.network.verify import check_row
from src.sim.staffing_frontier import run_staffing, summarize


VARIABLE = ["cogs", "payment_fee", "packaging", "cold_packaging", "shrink", "ongoing_marketing", "it_cost",
            "external_quick", "contract_rider_cost", "employed_rider_cost", "picker_cost"]


def contribution_margin(model, year):
    plan = existing_plan(model, "B")
    radius = model.p["alternatives"]["B"]["T"]
    _, annual, _, _ = model.evaluate(plan, radius, "base", "mid", details=True)
    row = annual.set_index("year").loc[year]
    require(row.orders > 0, "공헌이익 산출 기준 연도의 주문이 없습니다")
    value = (row.revenue-row[VARIABLE].sum())/row.orders
    return value, pd.DataFrame([dict(plan="B", plan_version="initial", market="base", demand_band="mid", year=year,
                                     annual_orders=row.orders, annual_revenue_krw=row.revenue,
                                     **{f"annual_{k}_krw": row[k] for k in VARIABLE},
                                     annual_variable_cost_krw=row[VARIABLE].sum(), contribution_per_order_krw=value)])


def operating_basis(reference, validated):
    previous = read_output(reference.out / "feedback_basis_v3.csv").set_index("T")
    counts = calendar_counts(reference.cfg, reference.cfg["feedback"])
    scales = previous[[f"scale_{kind}" for kind in KIND]].iloc[0]
    cells, summary = [], []
    for radius in previous.index:
        for day in DAY_TYPES:
            within = validated[validated.radius_min.eq(radius) & validated.day_type.eq(day) & validated.certified]
            record = dict(T=radius, day_type=day, calendar_days=counts[day], weight=counts[day]/counts.sum())
            if within.empty:
                cells.append(dict(**record, evidence="통과점 없음", capacity_orders=0))
                continue
            selected = within.sort_values("orders", ascending=False).iloc[0]
            cells.append(dict(**record, evidence="독립 검증 통과", capacity_orders=selected.orders,
                              **{k: selected[k] for k in ("instance", "order_level", "candidate", "repetitions", "on_time",
                                                          "cost_per_order_krw", *UNITS)}))
    cells = pd.DataFrame(cells)
    for radius, group in cells.groupby("T"):
        usable = group[group.capacity_orders.gt(0)]
        require(len(usable) > 0, f"{radius}분 검증 통과점 없음")
        capacities = group.set_index("day_type").capacity_orders
        capacity_m = float(group.weight @ group.capacity_orders)
        # 검증된 대표일에 한해 기존 달력일 가중을 사용한다.
        weights = usable.weight / usable.weight.sum()
        metrics = weights @ usable[[*UNITS, "on_time", "cost_per_order_krw"]]
        summary.append(dict(T=radius, verified_day_types=len(usable), calendar_days_with_verified_ops=usable.calendar_days.sum(),
                            **{f"days_{d}": counts[d] for d in DAY_TYPES},
                            **{f"capacity_m_{d}": capacities[d] for d in DAY_TYPES},
                            **scales.to_dict(),
                            **{f"capacity_per_day_{kind}": capacity_m*scales[f"scale_{kind}"] for kind in KIND},
                            **{f"capacity_per_year_{kind}": capacity_m*scales[f"scale_{kind}"]*reference.days_per_year
                               for kind in KIND}, **metrics.to_dict(), late_rate=1-metrics.on_time))
    return pd.DataFrame(summary).set_index("T"), cells


def verify_staffing(frontier, training, days, attempts, settings, reference, contribution, checks):
    key = ["instance", "radius_min", "orders"]
    coupon = reference.engine.par("late_coupon_krw")
    penalty = reference.p["late_demand_penalty"]
    check_row(checks, "STAFF", "cell_count", len(frontier), 18, len(frontier) == 18 and not frontier.duplicated(key).any())
    check_row(checks, "STAFF", "independent_runs", len(days), 20*len(attempts), len(days) == 20*len(attempts))
    for row in frontier.itertuples():
        mask = lambda frame: frame.instance.eq(row.instance) & frame.radius_min.eq(row.radius_min) & frame.orders.eq(row.orders)
        trials = attempts[mask(attempts)]
        cell_days = days[mask(days)]
        match = cell_days[cell_days.attempt.eq(row.attempt)]
        records = match.to_dict("records")
        again = summarize(records, settings["minimum_mean_ontime"], row.bike_bays, coupon, penalty, contribution)
        selected = training[training.instance.eq(row.instance) & training.radius_min.eq(row.radius_min) & training.orders.eq(row.orders)]
        picked = selected[selected.selected]
        valid = selected[selected.feasible].sort_values(["objective_per_order_krw", "candidate"])
        optimal = (valid.empty or trials.candidate.tolist() == valid.head(len(trials)).candidate.tolist())
        passed = (len(match) == 20 and match.seed.nunique() == 20 and match.candidate.eq(row.candidate).all()
                  and cell_days.seed.nunique() == len(cell_days) and not trials.iloc[:-1].certified.any()
                  and match.delivered.add(match.undelivered).eq(row.orders).all() and selected.repetitions.eq(5).all()
                  and len(picked) == 1 and picked.candidate.item() == row.candidate and optimal
                  and again["feasible"] == row.feasible and np.isclose(again["on_time"], row.on_time)
                  and np.isclose(again["objective_per_order_krw"], row.objective_per_order_krw, equal_nan=True))
        check_row(checks, row.instance, f"{row.radius_min}_{row.orders}_selection_validation", len(match), 20, passed)


def comparison(out, updated, reference):
    previous = read_output(out / "feedback_compare_v3.csv")
    rows = []
    for name in reference.p["alternatives"]:
        key = previous[previous.version.eq("v3") & previous.design.eq(name)].alternative.item()
        scenarios = updated[updated.alternative.eq(name)].set_index("scenario")
        base, low = scenarios.loc["base"], scenarios.loc["low"]
        rows.append(dict(alternative=key, phase="v4", version="v4", design=name, T=base["T"],
                         radius_evidence="지연 비용 반영 독립 검증", max_funding_low_mid_krw=low.max_funding,
                         max_funding_low_low_krw=low.max_funding_low, max_funding_base_mid_krw=base.max_funding,
                         funding_within_limit_low_mid=low.funding_within_limit,
                         funding_within_limit_base_mid=base.funding_within_limit,
                         **{f"npv_{s}_krw": scenarios.loc[s, "npv"] for s in scenarios.index},
                         **{k: base[k] for k in ("hubs_2027", "hubs_2032", "hubs_2037", "hubs_2042", "coverage_2027",
                                                "coverage_2037", "ebitda_positive_year", "on_time", "cost_per_order_krw")}))
    return pd.concat([previous, add_eok(pd.DataFrame(rows), reference.engine.unit)], ignore_index=True).sort_values(
        ["alternative", "version"], kind="stable")


def run(settings):
    started = monotonic()
    deadline = started + settings["runtime_limit_seconds"]
    network_settings = load_config(ROOT / settings["network_settings"])
    full = load_config(ROOT / network_settings["base_config"])
    config = prepare(full)
    sim = {**config, "sim": full["sim"], "dispatch": full["dispatch"]}
    reference = NetworkModel(config)
    out = reference.out
    files = [p for folder in (out, ROOT / "data") for p in folder.rglob("*") if p.is_file()]
    protected = digest(files)
    value, formula = contribution_margin(reference, settings["contribution_year"])
    print(f"2029 주문당 공헌이익 {value:.2f}원", flush=True)
    frontier, training, days, attempts = run_staffing(sim, settings, out, value, started + settings["staffing_limit_seconds"])
    checks = []
    verify_staffing(frontier, training, days, attempts, settings, reference, value, checks)
    basis, cells = operating_basis(reference, frontier)
    print(basis[["verified_day_types", "capacity_per_day_M", "cost_per_order_krw", "on_time"]].to_string(), flush=True)
    previous = {name: existing_plan(reference, name + "_v3") for name in reference.p["alternatives"]}
    branches = search_all(config, network_settings, reference, basis, previous, deadline)
    chosen = choose_plans(branches, reference)
    models = {r: FeedbackModel(config, r, basis.loc[r]) for r in basis.index}
    rows, tables = [], {}
    for name, result in chosen.items():
        model = models[reference.p["alternatives"][name]["T"]]
        tables[f"network_plan_{name}_v4.csv"] = model.plan_frame(result["plan"], explicit_pilots=True)
        for scenario in reference.p["scenario_order"]:
            row, details = compare_row(model, result, scenario, True)
            row["radius_evidence"] = "지연 비용 반영 독립 검증"
            row["ops_input_on_time"] = basis.loc[model.radius, "on_time"]
            row["ops_input_cost_per_order_krw"] = basis.loc[model.radius, "cost_per_order_krw"]
            row["funding_within_limit_low_mid"] = result["kpi"]["funding_within_limit"]
            rows.append(row)
            if scenario == "base":
                verify_updated(model, result, row, details, checks)
            if scenario == network_settings["funding_scenario"]:
                actual = result["kpi"]["max_funding"]
                check_row(checks, name, "low_funding", actual, row["max_funding"], np.isclose(actual, row["max_funding"]))
    updated = pd.DataFrame(rows)
    compared = comparison(out, updated, reference)
    roots = pd.DataFrame([row for name in ("B", "D15") for row in
                          break_even(models[reference.p["alternatives"][name]["T"]], chosen[name]["plan"], network_settings, name)])
    roots["plan_version"] = "v4"
    for row in roots.itertuples():
        ok = abs(row.metric_at_threshold_krw-row.target_value_krw) < 1000 if row.status == "도달" else (
            np.isclose(row.current_metric_krw, row.metric_at_zero_cost_krw) if row.status == "배송 주문 0건" else
            row.metric_at_zero_cost_krw < row.target_value_krw if row.target == "npv_zero" else row.metric_at_zero_cost_krw > row.target_value_krw)
        check_row(checks, row.alternative, row.target, row.metric_at_threshold_krw, row.target_value_krw, ok)
    for radius, group in cells.groupby("T"):
        expected = group.weight @ group.capacity_orders
        check_row(checks, str(radius), "calendar_capacity_m", basis.loc[radius, "capacity_per_day_M"], expected,
                  np.isclose(basis.loc[radius, "capacity_per_day_M"], expected))
    check_row(checks, "ALL", "original_file_hashes", len(protected), len(protected), protected == digest(files))
    check_row(checks, "ALL", "comparison_rows", len(compared), 20, len(compared) == 20)
    trace = pd.DataFrame([dict(**r, selected_branch=branch is chosen[branch["alternative"]])
                          for branch in branches for r in branch["trace"]])
    issues = read_output(out / "data_issues_log_v3.csv")
    gaps = [dict(rule_id="FEEDBACK_V4_UNVERIFIED_DAY", file="staffing_frontier.csv", n_rows=1,
                 check="검증 통과점 없음", example=f"{r.T}분/{r.day_type}",
                 action="검증된 처리능력 0으로 산입. 비용·정시율은 같은 반경의 검증된 대표일 비중으로 재정규화")
            for r in cells[cells.capacity_orders.eq(0)].itertuples()]
    unit = reference.engine.unit
    tables.update({"staffing_frontier.csv": add_eok(pd.concat([training, attempts], ignore_index=True), unit),
                   "staffing_validation_v4.csv": add_eok(days, unit), "staffing_contribution_v4.csv": add_eok(formula, unit),
                   "feedback_calendar_cells_v4.csv": add_eok(cells, unit), "feedback_basis_v4.csv": add_eok(basis.reset_index(), unit),
                   "alt_compare_v4.csv": updated, "feedback_compare_v4.csv": compared,
                   "breakeven_cost_v4.csv": add_eok(roots, unit), "network_search_trace_v4.csv": trace,
                   "network_verification_v4.csv": pd.DataFrame(checks),
                   "data_issues_log_v4.csv": pd.concat([issues, pd.DataFrame(gaps)], ignore_index=True)})
    require(monotonic() < deadline, "08-3 실행시간 상한 초과")
    publish(out, tables)
    print(compared.query("alternative == 'B'")[["version", "cost_per_order_krw", "on_time", "npv_base_eok",
                                                "max_funding_low_mid_eok"]].to_string(index=False), flush=True)
    print(f"네트워크 평가 {sum(r['evaluations'] for r in branches):,}회 | 검산 {len(checks)}/{len(checks)} | {monotonic()-started:.1f}초", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config_feedback_v4.json")
    run(load_config(parser.parse_args().config))


if __name__ == "__main__":
    main()
