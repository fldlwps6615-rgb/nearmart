"""08-2 실행: python -B -m src.network.feedback_v3 --config config_feedback_v3.json."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.network.feedback import (COST, FeedbackModel, FixedStartSearch, compare_row, existing_plan, prepare,
                                  verify_updated)
from src.network.feedback_basis import DAY_TYPES, calendar_counts, validated_runs
from src.network.model import NetworkModel, read_output, require
from src.network.run import csv
from src.network.search import canonical
from src.network.verify import check_row


KIND = ("S", "M", "L")
UNITS = ["contract_unit_krw", "employed_unit_krw", "picker_unit_krw"]


def weighted_basis(reference, previous):
    spec = reference.cfg["feedback"]
    runs, maxima, _ = validated_runs(reference, spec)
    days = calendar_counts(reference.cfg, spec)
    cells = []
    updated = previous.copy()
    for radius in updated.index:
        rows = []
        for day in DAY_TYPES:
            eligible = [int(r) for r, d in maxima.index if r != "original" and d == day and int(r) <= radius]
            require(eligible, f"{radius}분 {day}의 동일 대표일 대체값이 없습니다")
            source = max(eligible)
            orders = maxima.loc[str(source), day]
            used = runs[runs.radius_type.eq(str(source)) & runs.day_type.eq(day) & runs.orders.eq(orders)]
            require(len(used) == 20, f"{source}분 {day} 최대 수준의 검증 회차 수 오류")
            unit = used[["contract_unit", "employed_unit", "picker_cost_per_order"]].mean().to_numpy()
            rows.append(dict(T=radius, day_type=day, calendar_days=days[day], weight=days[day]/days.sum(),
                             source_radius_min=source, substitute=source != radius, source_orders=orders,
                             capacity_orders_used=previous.loc[radius, f"capacity_m_{day}"], validation_rows=len(used),
                             **dict(zip(UNITS, unit)), cost_per_order_krw=unit.sum(), on_time=used.ontime_corrected.mean()))
        group = pd.DataFrame(rows)
        columns = [*UNITS, "cost_per_order_krw", "on_time"]
        updated.loc[radius, columns] = group.weight @ group[columns]
        cells.extend(rows)
    updated["late_rate"] = 1 - updated.on_time
    updated["cost_minus_05_per_order_krw"] = updated.cost_per_order_krw - updated.pilot_cost_per_order_krw
    return updated, pd.DataFrame(cells)


class LiquiditySearch(FixedStartSearch):
    def __init__(self, model, alternative, family, fixed, settings):
        pilots = [i for i, _, _ in fixed if i in model.pilots]
        super().__init__(model, alternative, family, pilots, fixed)
        self.funding_scenario = settings["funding_scenario"]
        self.funding_band = settings["funding_band"]
        self.capacity_trials = []

    def score(self, plan):
        base = super().score(plan)
        low = self.m.evaluate(plan, self.T, self.funding_scenario, self.funding_band)
        return {**base, "max_funding": low["max_funding"], "max_funding_eok": low["max_funding_eok"],
                "funding_within_limit": low["funding_within_limit"]}

    def priority(self, score):
        excess = max(0, score["max_funding"] - self.m.limit)
        return -excess, score["npv"]

    def better(self, current, candidate):
        old_excess, old_npv = self.priority(current)
        new_excess, new_npv = self.priority(candidate)
        tolerance = self.m.p["npv_improvement_min_krw"]
        return new_excess > old_excess + tolerance or (new_excess >= old_excess and new_npv > old_npv + tolerance)

    def initial(self):
        """기존 계획에서 상한 초과 동을 가져올 수 있는 추가 후보도 출발점으로 평가한다."""
        plan = super().initial()
        if self.family != "capacity_start":
            return plan
        m = self.m
        year = m.p["phases"][0]
        y = year - m.years[0]
        _, state = m.operating(plan, self.T, m.p["optimization_scenario"], m.p["optimization_band"], True)
        assigned = state["assigned"][y]
        lost = state["requested"][y] - state["served"][y]
        valid = np.flatnonzero((assigned >= 0) & (lost > 1e-8))
        if not len(valid):
            return plan
        incumbent = m.peak[assigned[valid], valid]
        choices = []
        for i in self.available(plan, year):
            trial = canonical([*plan, (i, year, m.end)])
            if not self.legal(trial):
                continue
            attracted = (m.peak[i, valid] < incumbent) & (m.peak[i, valid] <= self.T)
            if not attracted.any():
                continue
            score = self.score(trial)
            self.capacity_trials.append(dict(alternative=self.name, year=year, site_id=m.ids[i],
                                             hub_type=m.hub_types[i], lost_orders_per_day_in_target_dongs=lost[valid[attracted]].sum(),
                                             npv_base_krw=score["npv"], max_funding_low_mid_krw=score["max_funding"],
                                             used_as_seed=False))
            choices.append((self.priority(score), i, trial))
        if not choices:
            return plan
        _, selected, trial = max(choices)
        self.capacity_trials[[r["site_id"] for r in self.capacity_trials].index(m.ids[selected])]["used_as_seed"] = True
        self.record(year, "capacity_seed_add", plan, trial, m.ids[selected])
        return trial


def run_branch(config, settings, basis, name, family, start):
    radius = config["network"]["alternatives"][name]["T"]
    model = FeedbackModel(config, radius, basis)
    if name == "D15":
        model.p["phases"] = model.p["staged_expansion"]["expansion_years"]
    search = LiquiditySearch(model, name, family, start, settings)
    result = search.run()
    result.update(evaluations=model.evaluations, capacity_trials=search.capacity_trials)
    return result


def search_all(config, settings, reference, basis, previous_plans, deadline):
    year = int(reference.years[0])
    expansion = config["network"]["staged_expansion"]["expansion_years"][0]
    fixed = canonical((i, o, c if c < expansion else reference.end)
                      for i, o, c in existing_plan(reference, "B") if o == year)
    jobs = [("D15", "staged_start", fixed), ("D15", "capacity_start", fixed),
            ("D15", "warm_start", previous_plans["D15"])]
    for name, plan in previous_plans.items():
        if name == "D15":
            continue
        jobs.extend([(name, "warm_start", plan), (name, "capacity_start", plan)])
        for mask in product((False, True), repeat=len(reference.pilots)):
            start = canonical((i, year, reference.end) for i, keep in zip(reference.pilots, mask) if keep)
            jobs.append((name, "greedy", start))
    pool = ProcessPoolExecutor(max_workers=settings["search_workers"])
    try:
        pending = [pool.submit(run_branch, config, settings, basis.loc[config["network"]["alternatives"][name]["T"]].to_dict(),
                               name, family, start) for name, family, start in jobs]
        results = [future.result() for future in as_completed(pending, timeout=max(0, deadline - monotonic() - 90))]
    except BaseException:
        pool.terminate_workers()
        raise
    finally:
        pool.shutdown()
    return sorted(results, key=lambda r: (r["alternative"], r["search"], r["pilot_branch"]))


def add_eok(frame, unit):
    for column in [c for c in frame if c.endswith("_krw")]:
        frame[column.removesuffix("_krw") + "_eok"] = frame[column] / unit
    return frame


def decomposition(config, old_basis, weighted, reference):
    plan = existing_plan(reference, "B")
    radius = reference.p["alternatives"]["B"]["T"]
    initial = reference.p
    start = reference.evaluate(plan, radius, "base", "mid")["npv"]
    rows = [dict(case="baseline", cost_changed=False, capacity_changed=False, late_rate_changed=False,
                 npv_base_krw=start, delta_from_baseline_krw=0.)]
    for case, cost, capacity, late in (("cost_only", True, False, False), ("capacity_only", False, True, False),
                                      ("late_rate_only", False, False, True), ("all_08", True, True, True),
                                      ("all_08_2_weighted", True, True, True)):
        basis = (weighted if case.endswith("weighted") else old_basis).loc[radius].copy()
        if not capacity:
            for kind in KIND:
                basis[f"capacity_per_day_{kind}"] = initial["capacity_per_day"][kind]
        if not late:
            basis["late_rate"] = initial["late_rate"]
        if cost:
            model = FeedbackModel(config, radius, basis)
        else:
            mixed = deepcopy(config)
            mixed["network"]["capacity_per_day"] = {kind: basis[f"capacity_per_day_{kind}"] for kind in KIND}
            mixed["network"]["late_rate"] = basis["late_rate"]
            model = NetworkModel(mixed)
        npv = model.evaluate(plan, radius, "base", "mid")["npv"]
        rows.append(dict(case=case, cost_changed=cost, capacity_changed=capacity, late_rate_changed=late,
                         npv_base_krw=npv, delta_from_baseline_krw=npv-start))
    frame = pd.DataFrame(rows)
    frame["interaction_vs_sum_krw"] = np.nan
    frame.loc[frame.case.eq("all_08"), "interaction_vs_sum_krw"] = (frame.loc[4, "delta_from_baseline_krw"]
                                                                   - frame.loc[1:3, "delta_from_baseline_krw"].sum())
    return add_eok(frame, reference.engine.unit)


def break_even(model, plan, settings, name):
    radius = model.radius
    parts = np.array([model.contract_unit, model.employed_unit, model.p["picker_cost_2027"]])
    total = parts.sum()
    rows = []
    for target, scenario, key, threshold in (("npv_zero", "base", "npv", 0.),
                                              ("funding_limit", "low", "max_funding", model.limit)):
        frame = model.plan_frame(plan)
        original = model.operating(plan, radius, scenario, "mid")
        sign = 1 if key == "npv" else -1

        def evaluate(unit):
            ops = original.copy()
            ops[COST] = ops[COST] * (unit / total)
            return model.engine.evaluate(frame, ops, scenario)[1][key]

        lower, upper = 0., total
        at_zero = evaluate(0.)
        current = evaluate(total)
        if original.orders_per_day.sum() == 0:
            root, value, status = np.nan, at_zero, "배송 주문 0건"
        elif sign*(at_zero - threshold) < 0:
            root, value, status = np.nan, np.nan, "비음수 단가에서 도달 불가"
        elif np.isclose(at_zero, threshold, rtol=0, atol=1e-3):
            root, value, status = 0., at_zero, "도달"
        else:
            while sign*(evaluate(upper)-threshold) > 0:
                upper *= 2
            while upper-lower > settings["root_cost_tolerance_krw"]:
                mid = (lower+upper)/2
                if sign*(evaluate(mid)-threshold) >= 0:
                    lower = mid
                else:
                    upper = mid
            root = (lower+upper)/2
            value, status = evaluate(root), "도달"
        rows.append(dict(alternative=name, plan_version="v3", target=target, scenario=scenario, demand_band="mid",
                         status=status, target_value_krw=threshold, threshold_cost_per_order_krw=root,
                         current_cost_per_order_krw=total, cost_reduction_required_krw=total-root,
                         current_metric_krw=current, metric_at_zero_cost_krw=at_zero, metric_at_threshold_krw=value))
    return rows


def capacity_sensitivity(config, basis, names, settings):
    rows = []
    for name, plan in names.items():
        radius = config["network"]["alternatives"][name]["T"]
        for m_capacity in settings["capacity_sensitivity_m"]:
            inputs = basis.loc[radius].copy()
            for kind in KIND:
                inputs[f"capacity_per_day_{kind}"] = m_capacity * inputs[f"scale_{kind}"]
            model = FeedbackModel(config, radius, inputs)
            base = model.evaluate(plan, radius, "base", "mid")
            low = model.evaluate(plan, radius, "low", "mid")
            rows.append(dict(alternative=name, plan_version="v3", T=radius, demand_band="mid", capacity_m_per_day=m_capacity,
                             capacity_s_per_day=inputs.capacity_per_day_S, capacity_l_per_day=inputs.capacity_per_day_L,
                             npv_base_krw=base["npv"], npv_low_krw=low["npv"], max_funding_base_mid_krw=base["max_funding"],
                             max_funding_low_mid_krw=low["max_funding"], funding_within_limit_low_mid=low["funding_within_limit"]))
    return pd.DataFrame(rows)


def choose_plans(branches, reference):
    chosen = {}
    for name in reference.p["alternatives"]:
        candidates = [r for r in branches if r["alternative"] == name]

        def rank(result):
            kpi = result["kpi"]
            return -max(0, kpi["max_funding"] - reference.limit), kpi["npv"]

        chosen[name] = max(candidates, key=rank)
    return chosen


def feedback_comparison(out, updated, reference):
    old = read_output(out / "feedback_compare.csv")
    rows = []
    for name in reference.p["alternatives"]:
        for phase in ("before", "after", "v3"):
            previous = old[old.design.eq(name) & old.phase.eq("after")].iloc[0]
            if phase != "v3":
                row = old[old.alternative.eq(previous.alternative) & old.phase.eq(phase)].iloc[0].to_dict()
                row["version"] = "v1" if phase == "before" else "v2"
                row["funding_within_limit_low_mid"] = row["max_funding_low_mid_krw"] <= reference.limit
            else:
                scenarios = updated[updated.alternative.eq(name)].set_index("scenario")
                base, low = scenarios.loc["base"], scenarios.loc["low"]
                row = dict(alternative=previous.alternative, phase=phase, version="v3", design=name, T=base["T"],
                           radius_evidence="v2 대표일 가중", max_funding_low_mid_krw=low.max_funding,
                           max_funding_low_low_krw=low.max_funding_low, max_funding_base_mid_krw=base.max_funding,
                           funding_within_limit_low_mid=low.funding_within_limit,
                           funding_within_limit_base_mid=base.funding_within_limit,
                           **{f"npv_{s}_krw": scenarios.loc[s, "npv"] for s in scenarios.index},
                           **{k: base[k] for k in ("hubs_2027", "hubs_2032", "hubs_2037", "hubs_2042", "coverage_2027",
                                                   "coverage_2037", "ebitda_positive_year", "on_time", "cost_per_order_krw")})
            rows.append(row)
    return add_eok(pd.DataFrame(rows), reference.engine.unit)


def digest(paths):
    return {path: sha256(path.read_bytes()).digest() for path in paths}


def publish(output, tables):
    with TemporaryDirectory(prefix="feedback_v3_", dir=output) as directory:
        for name, frame in tables.items():
            csv(Path(directory) / name, frame)
        require(not any((output / name).exists() for name in tables), "기존 08-2 산출물이 있습니다")
        for name in tables:
            (Path(directory) / name).rename(output / name)


def run(settings):
    started = monotonic()
    deadline = started + settings["runtime_limit_seconds"]
    config = prepare(load_config(ROOT / settings["base_config"]))
    reference = NetworkModel(config)
    out = reference.out
    files = [p for folder in (out, ROOT / "data") for p in folder.rglob("*") if p.is_file()]
    hashes = digest(files)
    old_basis = read_output(out / "feedback_basis_v2.csv").set_index("T")
    basis, cells = weighted_basis(reference, old_basis)
    print(basis[["capacity_per_day_M", "cost_per_order_krw", "on_time"]].to_string(), flush=True)
    previous = {name: existing_plan(reference, name + "_v2") for name in reference.p["alternatives"]}
    parts = decomposition(config, old_basis, basis, reference)
    branches = search_all(config, settings, reference, basis, previous, deadline)
    chosen = choose_plans(branches, reference)
    models = {r: FeedbackModel(config, r, basis.loc[r]) for r in basis.index}
    comparisons, checks, tables = [], [], {}
    summaries = []
    for name, result in chosen.items():
        model = models[reference.p["alternatives"][name]["T"]]
        tables[f"network_plan_{name}_v3.csv"] = model.plan_frame(result["plan"], explicit_pilots=True)
        for scenario in reference.p["scenario_order"]:
            row, details = compare_row(model, result, scenario, True)
            row["radius_evidence"] = "v2 대표일 가중"
            row["funding_within_limit_low_mid"] = result["kpi"]["funding_within_limit"]
            comparisons.append(row)
            if scenario == "base":
                verify_updated(model, result, row, details, checks)
            if scenario == settings["funding_scenario"]:
                funding = result["kpi"]["max_funding"]
                check_row(checks, name, "search_funding_low_mid_krw", funding, row["max_funding"], np.isclose(funding, row["max_funding"]))
    for branch in branches:
        name, kpi = branch["alternative"], branch["kpi"]
        summaries.append(dict(alternative=name, search=branch["search"], pilot_branch=branch["pilot_branch"],
                              selected=int(branch is chosen[name]), evaluations=branch["evaluations"],
                              npv_base_krw=kpi["npv"], max_funding_low_mid_krw=kpi["max_funding"],
                              funding_within_limit_low_mid=kpi["funding_within_limit"]))
    updated = pd.DataFrame(comparisons)
    comparison = feedback_comparison(out, updated, reference)
    plans = {name: chosen[name]["plan"] for name in ("B", "D15")}
    roots = pd.DataFrame([r for name, plan in plans.items()
                          for r in break_even(models[reference.p["alternatives"][name]["T"]], plan, settings, name)])
    sensitivity = capacity_sensitivity(config, basis, plans, settings)
    for row in roots.itertuples():
        ok = (abs(row.metric_at_threshold_krw - row.target_value_krw) < 1000 if row.status == "도달"
              else (row.metric_at_zero_cost_krw < row.target_value_krw if row.target == "npv_zero"
                    else row.metric_at_zero_cost_krw > row.target_value_krw))
        check_row(checks, row.alternative, f"{row.target}_target_krw", row.metric_at_threshold_krw,
                  row.target_value_krw, ok)
    base, all_08 = (parts.set_index("case").loc[c, "npv_base_krw"] for c in ("baseline", "all_08"))
    historical = read_output(out / "feedback_compare.csv").query("alternative == 'B'").set_index("phase")
    for phase, value in (("before", base), ("after", all_08)):
        expected = historical.loc[phase, "npv_base_krw"]
        check_row(checks, "B", f"decomposition_{phase}", value, expected, np.isclose(value, expected, rtol=0, atol=0.01))
    fixed = existing_plan(reference, "B")
    expansion = config["network"]["staged_expansion"]["expansion_years"][0]
    for year in reference.years[reference.years < expansion]:
        initial = {i for i, o, c in fixed if o == reference.years[0] and o <= year < c}
        final = {i for i, o, c in chosen["D15"]["plan"] if o <= year < c}
        check_row(checks, "D15", f"fixed_B_{year}", len(initial ^ final), 0, initial == final)
    for radius, group in cells.groupby("T"):
        check_row(checks, str(radius), "calendar_days", group.calendar_days.sum(), 91, group.calendar_days.sum() == 91)
        check_row(checks, str(radius), "validation_rows", group.validation_rows.sum(), 60, group.validation_rows.eq(20).all())
    check_row(checks, "ALL", "original_file_hashes", len(hashes), len(hashes), hashes == digest(files))
    check_row(checks, "ALL", "comparison_rows", len(comparison), 15, len(comparison) == 15)
    check_row(checks, "ALL", "sensitivity_rows", len(sensitivity), 6, len(sensitivity) == 6)
    trace = pd.DataFrame([dict(**r, selected_branch=branch is chosen[branch["alternative"]])
                          for branch in branches for r in branch["trace"]])
    targeted = pd.DataFrame([r for branch in branches for r in branch["capacity_trials"]])
    issues = read_output(out / "data_issues_log_v2.csv")
    additions = [dict(rule_id="FEEDBACK_V3_RADIUS_FALLBACK", file="service_radius_basis.csv", n_rows=1,
                      check="동일 반경·대표일의 독립 통과값 공백", example=f"{r.T}분/{r.day_type}",
                      action=f"동일 대표일 {r.source_radius_min}분 최대 통과점의 비용과 정시율 사용")
                 for r in cells[cells.substitute].itertuples()]
    unit = reference.engine.unit
    tables.update({"alt_compare_v3.csv": updated, "feedback_compare_v3.csv": comparison,
                   "feedback_decomposition.csv": parts, "breakeven_cost.csv": add_eok(roots, unit),
                   "capacity_sensitivity.csv": add_eok(sensitivity, unit), "feedback_basis_v3.csv": add_eok(basis.reset_index(), unit),
                   "feedback_calendar_cells_v3.csv": add_eok(cells, unit), "network_search_branches_v3.csv": add_eok(pd.DataFrame(summaries), unit),
                   "network_search_trace_v3.csv": trace, "network_capacity_candidates_v3.csv": add_eok(targeted, unit),
                   "network_verification_v3.csv": pd.DataFrame(checks),
                   "data_issues_log_v3.csv": pd.concat([issues, pd.DataFrame(additions)], ignore_index=True)})
    require(monotonic() < deadline, "08-2 실행시간 상한 초과")
    publish(out, tables)
    print(comparison.query("version == 'v3'")[["design", "hubs_2027", "hubs_2037", "npv_base_eok",
                                              "max_funding_low_mid_eok"]].to_string(index=False), flush=True)
    print(f"탐색 평가 {sum(r['evaluations'] for r in branches):,}회 | 검산 {len(checks)}/{len(checks)} | {monotonic()-started:.1f}초", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config_feedback_v3.json")
    run(load_config(parser.parse_args().config))


if __name__ == "__main__":
    main()
