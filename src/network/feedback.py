"""08 재설계. 실행: python -B -m src.network.feedback --config config.json."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
from itertools import product
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from time import monotonic

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.network.feedback_basis import load_basis
from src.network.model import NetworkModel, read_output, require
from src.network.run import csv
from src.network.search import Search, canonical
from src.network.verify import check_row, verify_plan


COST = ["contract_rider_cost_per_order_krw", "employed_rider_cost_per_order_krw", "picker_cost_per_order_krw"]


class FeedbackModel(NetworkModel):
    def __init__(self, config, radius, basis):
        config = deepcopy(config)
        p = config["network"]
        p.update(capacity_per_day={kind: basis[f"capacity_per_day_{kind}"] for kind in ["S", "M", "L"]},
                 rider_cost_2027=[basis["contract_unit_krw"] + basis["employed_unit_krw"]] * len(p["rider_cost_2027"]),
                 picker_cost_2027=basis["picker_unit_krw"], late_rate=basis["late_rate"])
        super().__init__(config)
        self.radius = radius
        self.contract_unit = basis["contract_unit_krw"]
        self.employed_unit = basis["employed_unit_krw"]

    def rider_units(self, weighted_cost):
        # v2의 라이더 단가는 외부 퀵을 제외한 비용을 전체 주문수로 나눈 값이다.
        if weighted_cost == 0:
            return 0., 0.
        return self.contract_unit, self.employed_unit

    def operating(self, plan, T, scenario, band, details=False):
        require(T == self.radius, f"운영 단가 반경 {self.radius}분과 배정 상한 {T}분 불일치")
        return super().operating(plan, T, scenario, band, details)


class FixedStartSearch(Search):
    def __init__(self, model, alternative, family, pilots, fixed):
        super().__init__(model, alternative, family, pilots)
        self.fixed = fixed

    def initial(self):
        return self.fixed


def existing_plan(model, alternative):
    source = read_output(model.out / f"network_plan_{alternative}.csv")
    require(source.site_id.is_unique and source.relocated_from.isna().all(), f"{alternative} 기존 계획 키 또는 이전 이력 확인 필요")
    rows = []
    for row in source.itertuples():
        opening = int(row.open_year)
        closing = model.end if pd.isna(row.close_year) else int(row.close_year)
        i = model.index[row.site_id]
        require(row.hub_type == model.hub_types[i], f"{row.site_id} 유형 불일치")
        if opening == closing == model.years[0] and i in model.pilots:
            continue
        require(model.available_years[i] <= opening < closing <= model.end, f"{row.site_id} 개폐점 연도 오류")
        rows.append((i, opening, closing))
    return canonical(rows)


def search_worker(config, basis, alternative, family, mask, fixed):
    radius = config["network"]["alternatives"][alternative]["T"]
    model = FeedbackModel(config, radius, basis)
    if alternative == "D15":
        model.p["phases"] = model.p["staged_expansion"]["expansion_years"]
        pilots = [i for i, _, _ in fixed if i in model.pilots]
        search = FixedStartSearch(model, alternative, family, pilots, fixed)
    else:
        pilots = [i for i, keep in zip(model.pilots, mask) if keep]
        search = Search(model, alternative, family, pilots)
    result = search.run()
    return result, model.evaluations


def search_plans(config, basis, reference, old_plans, deadline):
    stage = config["network"]["staged_expansion"]
    initial_year = int(reference.years[0])
    first_expansion = stage["expansion_years"][0]
    fixed = canonical((i, o, c if c < first_expansion else reference.end)
                      for i, o, c in old_plans[stage["initial_alternative"]] if o == initial_year)
    require(fixed and stage["expansion_years"] == reference.p["phases"][1:], "D15 초기 계획 또는 탐색 연도 확인 필요")
    workers = config["feedback"]["search_workers"]
    pool = ProcessPoolExecutor(max_workers=workers)
    try:
        jobs = [("D15", "greedy", None, fixed)]
        jobs += [("C15", family, mask, None) for family in ("greedy", "seed")
                 for mask in product([False, True], repeat=len(reference.pilots))]
        futures = []
        for name, family, mask, initial in jobs:
            radius = config["network"]["alternatives"][name]["T"]
            futures.append(pool.submit(search_worker, config, basis.loc[radius].to_dict(), name, family, mask, initial))
        results = [future.result() for future in as_completed(futures, timeout=max(0, deadline - monotonic() - 60))]
    except BaseException:
        pool.terminate_workers()
        raise
    finally:
        pool.shutdown()
    branches = sorted((result for result, _ in results), key=lambda r: (r["alternative"], r["search"], r["pilot_branch"]))
    finalists = []
    for family in ("greedy", "seed"):
        candidates = [r for r in branches if r["alternative"] == "C15" and r["search"] == family]
        finalists.append(max(candidates, key=lambda r: (r["kpi"]["max_funding"] <= reference.limit,
                                                       r["kpi"]["npv"], r["pilot_branch"])))
    winner = max(finalists, key=lambda r: (r["kpi"]["max_funding"] <= reference.limit, r["kpi"]["npv"]))
    d15 = next(r for r in branches if r["alternative"] == "D15")
    return finalists + [d15], winner, d15, branches, sum(count for _, count in results)


def prepare(settings):
    spec = settings["feedback"]
    config = load_config(ROOT / spec["network_source"])
    config["feedback"] = spec
    p = config["network"]
    p["staged_expansion"] = deepcopy(settings["network"]["staged_expansion"])
    p["staged_expansion"]["expanded_T"] = spec["radius_limit_min"]
    p["alternatives"] = {**{a: p["alternatives"][a] for a in spec["fixed_alternatives"]},
                         "C15": dict(p["alternatives"]["C"], T=spec["radius_limit_min"]),
                         "D15": {"T": spec["radius_limit_min"], "max_hubs_2027": None}}
    require(all(a["T"] <= spec["radius_limit_min"] for a in p["alternatives"].values()), "재설계 T 상한 위반")
    return config


def input_hashes(output):
    files = [p for p in output.rglob("*") if p.is_file() and not p.stem.endswith("_v2")
             and p.name != "feedback_compare.csv"]
    files += [p for p in (ROOT / "data").rglob("*") if p.is_file()]
    return {p: sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def unit_metrics(ops, year):
    rows = ops[ops.year.eq(year)]
    orders = rows.orders_per_day
    if orders.sum() == 0:
        return dict(on_time=np.nan, cost_per_order_krw=np.nan)
    return dict(on_time=1-np.average(rows.late_rate, weights=orders),
                cost_per_order_krw=np.average(rows[COST].sum(axis=1), weights=orders))


def compare_row(model, result, scenario, chosen):
    name, plan = result["alternative"], result["plan"]
    radius = model.p["alternatives"][name]["T"]
    kpi, annual, ops, state = model.evaluate(plan, radius, scenario, "mid", details=True)
    yearly = model.summaries(plan, state).set_index("year")
    row = dict(alternative=name, search=result["search"], pilot_branch=result["pilot_branch"], selected=int(chosen),
               T=radius, **kpi)
    row["discounted_payback_years"] = kpi["payback_year"]-2026 if isinstance(kpi["payback_year"], int) else np.nan
    row.update({f"hubs_{y}": yearly.loc[y, "active_hubs"] for y in model.p["phases"]})
    row.update({f"coverage_{y}": yearly.loc[y, "coverage"] for y in (2027, 2037)})
    for y in (2027, 2031, 2036, 2046):
        row[f"orders_{y}"] = yearly.loc[y, "orders_per_day"]
        row[f"lost_orders_per_day_{y}"] = yearly.loc[y, "lost_orders_per_day"]
    row["lost_orders_total"] = yearly.lost_orders_annual.sum()
    row["lost_revenue_krw"] = yearly.lost_revenue_krw.sum()
    row["lost_revenue_eok"] = row["lost_revenue_krw"]/model.engine.unit
    for band in ("low", "high"):
        other = model.evaluate(plan, radius, scenario, band)
        for metric in ("npv", "npv_eok", "max_funding", "max_funding_eok", "funding_within_limit"):
            if metric.endswith("_eok"):
                row[f"{metric[:-4]}_{band}_eok"] = other[metric]
            else:
                row[f"{metric}_{band}"] = other[metric]
    row.update(unit_metrics(ops, int(model.years[0])))
    return row, (annual, ops, state, yearly)


def feedback_rows(previous, updated, out, unit, limit):
    rows = []
    for base_name in ("A", "B", "B2", "C", "D"):
        new_name = {"C": "C15", "D": "D15"}.get(base_name, base_name)
        for phase, design, source in (("before", base_name, previous), ("after", new_name, updated)):
            selected = source[source.alternative.eq(design) & source.selected.eq(1)].set_index("scenario")
            require(selected.index.is_unique, f"{design} 선택안 시장 키 중복")
            low, base = selected.loc["low"], selected.loc["base"]
            metrics = {k: base[k] for k in ("on_time", "cost_per_order_krw")} if phase == "after" else unit_metrics(
                read_output(out / f"network_ops_{design}_base.csv"), 2027)
            radius = base["T"]
            row = dict(alternative=base_name, phase=phase, design=design, T=radius,
                       radius_evidence="근거 없음" if radius > limit else ("v2 반영" if phase == "after" else "1차 가정"),
                       **{k: base[k] for k in ("hubs_2027", "hubs_2032", "hubs_2037", "hubs_2042",
                                               "coverage_2027", "coverage_2037", "ebitda_positive_year")},
                       **{f"npv_{s}_krw": selected.loc[s, "npv"] for s in ("low", "base", "high")},
                       max_funding_low_mid_krw=low.max_funding, max_funding_low_low_krw=low.max_funding_low,
                       max_funding_base_mid_krw=base.max_funding,
                       funding_within_limit_base_mid=base.funding_within_limit,
                       **metrics)
            row.update({k.removesuffix("_krw")+"_eok": v/unit for k, v in row.items() if k.endswith("_krw")})
            rows.append(row)
    return pd.DataFrame(rows)


def investor_cost(updated, unit):
    rows = []
    selected = updated[updated.selected.eq(1)].set_index(["alternative", "scenario"])
    require(selected.index.is_unique, "투자자 조건 비교 키 중복")
    for scenario, band in product(("low", "base", "high"), ("low", "mid", "high")):
        metric = "npv" if band == "mid" else f"npv_{band}"
        c, d = (selected.loc[(alt, scenario), metric] for alt in ("C15", "D15"))
        rows.append(dict(scenario=scenario, demand_band=band, npv_C_krw=c, npv_D_krw=d,
                         investor_condition_cost_krw=c-d, npv_C_eok=c/unit, npv_D_eok=d/unit,
                         investor_condition_cost_eok=(c-d)/unit))
    return pd.DataFrame(rows)


def verify_updated(model, result, row, details, checks):
    name, plan = result["alternative"], result["plan"]
    annual, ops, state, yearly = details

    def check(metric, actual, expected, passed):
        check_row(checks, name, metric, actual, expected, passed)

    verify_plan(model, name, plan, state, annual, row, yearly.reset_index(), checks, require_funding=False)
    within_limit = row["max_funding"] <= model.limit
    check("funding_flag", int(row["funding_within_limit"]), int(within_limit), row["funding_within_limit"] == within_limit)
    base_orders = state["requested"].sum()
    without_late = 0.
    for y, assigned in enumerate(state["assigned"]):
        dongs = np.flatnonzero(assigned >= 0)
        sites = assigned[dongs]
        without_late += np.sum(model.demands["base", "mid"][y, dongs] * model.delivery_factor[sites, dongs]
                               * model.assortment[sites] * state["hub_ramp"][y, sites])
    late = model.p["late_rate"]
    factor = 1 - model.p["late_demand_penalty"] * late
    observed_factor = base_orders / without_late if without_late else np.nan
    check("late_demand_factor", observed_factor, factor, np.isclose(base_orders, without_late * factor))
    coupon = annual.orders.sum() * late * model.engine.par("late_coupon_krw")
    check("coupon_krw", annual.late_coupon.sum(), coupon, np.isclose(annual.late_coupon.sum(), coupon))
    unit = model.contract_unit + model.employed_unit + model.p["picker_cost_2027"]
    error = (ops.loc[ops.orders_per_day.gt(0), COST].sum(axis=1) - unit).abs().max() if ops.orders_per_day.gt(0).any() else 0.
    check("ops_unit_max_error_krw", error, 0, error < 1e-8)
    check("funding_recompute_krw", row["max_funding"], max(0, -annual.cum_cash_flow.min()),
          np.isclose(row["max_funding"], max(0, -annual.cum_cash_flow.min())))


def publish(directory, output, tables, plans, fixed_names, trace, checks):
    for name, frame in tables.items():
        csv(directory / name, frame)
    for name, frame in plans.items():
        csv(directory / f"network_plan_{name}_v2.csv", frame)
    for name in fixed_names:
        copyfile(output / f"network_plan_{name}.csv", directory / f"network_plan_{name}_v2.csv")
    csv(directory / "network_search_trace_v2.csv", trace)
    csv(directory / "network_verification_v2.csv", checks)
    for path in directory.glob("*.csv"):
        path.replace(output / path.name)


def run(settings):
    started = monotonic()
    config = prepare(settings)
    spec = config["feedback"]
    deadline = started + spec["runtime_limit_seconds"]
    reference = NetworkModel(config)
    out = reference.out
    hashes = input_hashes(out)
    previous = read_output(out / "alt_compare.csv")
    old_plans = {name: existing_plan(reference, name) for name in spec["fixed_alternatives"]}
    basis, audit = load_basis(reference, spec)
    print(basis[["capacity_per_day_M", "cost_per_order_krw", "on_time", "validation_rows"]].to_string(), flush=True)
    finalists, c15, d15, branches, evaluations = search_plans(config, basis, reference, old_plans, deadline)
    fixed = []
    for name, plan in old_plans.items():
        meta = previous[previous.alternative.eq(name) & previous.scenario.eq("base") & previous.selected.eq(1)].iloc[0]
        fixed.append(dict(alternative=name, plan=plan, search=meta.search, pilot_branch=meta.pilot_branch))
    winners = fixed + [c15, d15]
    models = {r: FeedbackModel(config, r, basis.loc[r]) for r in basis.index}
    comparisons, checks, plans = [], [], {}
    for result in fixed + finalists:
        name = result["alternative"]
        radius = reference.p["alternatives"][name]["T"]
        model = models[radius]
        chosen = any(result is winner for winner in winners)
        if chosen and name not in old_plans:
            plans[name] = model.plan_frame(result["plan"], explicit_pilots=True)
        for scenario in model.p["scenario_order"]:
            row, details = compare_row(model, result, scenario, chosen)
            row["radius_evidence"] = "v2 반영"
            comparisons.append(row)
            if scenario == "base" and chosen:
                verify_updated(model, result, row, details, checks)
    first_expansion = reference.p["staged_expansion"]["expansion_years"][0]
    for year in reference.years[reference.years < first_expansion]:
        b = {i for i, o, c in old_plans["B"] if o == reference.years[0] and o <= year < c}
        d = {i for i, o, c in d15["plan"] if o <= year < c}
        check_row(checks, "D15", f"fixed_B_{year}", len(d ^ b), 0, d == b)
    stages = d15["trace"]
    transitions_valid = all(r["funding_after"] <= reference.limit if r["funding_before"] <= reference.limit
                            else r["funding_after"] < r["funding_before"] for r in stages)
    check_row(checks, "D15", "funding_transitions", int(transitions_valid), 1, transitions_valid)
    updated = pd.DataFrame(comparisons)
    comparison = feedback_rows(previous, updated, out, reference.engine.unit, spec["radius_limit_min"])
    investment = investor_cost(updated, reference.engine.unit)
    check_row(checks, "ALL", "feedback_rows", len(comparison), 10, len(comparison) == 10)
    check_row(checks, "ALL", "investor_rows", len(investment), 9, len(investment) == 9)
    check_row(checks, "ALL", "historical_unsupported", comparison.radius_evidence.eq("근거 없음").sum(), 2,
              comparison.radius_evidence.eq("근거 없음").sum() == 2)
    unchanged = hashes == input_hashes(out)
    check_row(checks, "ALL", "original_input_hashes", len(hashes) if unchanged else 0, len(hashes), unchanged)
    verification = pd.DataFrame(checks)
    require(monotonic() < deadline, "08 실행시간 상한 초과")
    trace = pd.DataFrame([row for branch in branches for row in branch["trace"]])
    radius_basis = basis.reset_index()
    for column in [c for c in radius_basis if c.endswith("_krw")]:
        radius_basis[column.removesuffix("_krw")+"_eok"] = radius_basis[column] / reference.engine.unit
    issue_log = read_output(out / "data_issues_log.csv")
    fallback_rows = [dict(rule_id="FEEDBACK_CAPACITY_FALLBACK", file="service_radius_basis.csv",
                          check="독립 20회 통과 처리능력의 대표일 공백", n_rows=1, example=f"{radius}분/{target}",
                          action=f"APPROVED | 같은 반경의 {source} 통과 상한 적용")
                     for radius, mappings in spec["capacity_fallback"].items() for target, source in mappings.items()]
    issue_log = pd.concat([issue_log, pd.DataFrame(fallback_rows)], ignore_index=True)
    tables = {"feedback_compare.csv": comparison, "alt_compare_v2.csv": updated,
              "investor_condition_cost_v2.csv": investment, "feedback_basis_v2.csv": radius_basis,
              "feedback_bm1_audit_v2.csv": audit, "data_issues_log_v2.csv": issue_log}
    with TemporaryDirectory(prefix="feedback_", dir=out) as directory:
        publish(Path(directory), out, tables, plans, old_plans, trace, verification)
    print(comparison[["design", "phase", "hubs_2027", "hubs_2037", "npv_base_eok", "on_time", "cost_per_order_krw"]].to_string(index=False), flush=True)
    print(f"탐색 평가 {evaluations:,}회 | 검산 {len(checks)}/{len(checks)} | {monotonic()-started:.1f}초", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    run(load_config(args.config))


if __name__ == "__main__":
    main()
