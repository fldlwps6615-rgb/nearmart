"""대안 D만 실행: python -m src.network.staged.run --config config.json."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import re

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.demand.report import md_table
from src.network.model import NetworkModel, read_output, require
from src.network.report import maps
from src.network.run import comparison_row, csv
from src.network.search import Search, canonical
from src.network.verify import check_row, hub_table


class StagedModel(NetworkModel):
    """동일한 운영 계산을 두 서비스 기준으로 나누어 적용한다."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.stage = self.p["staged_expansion"]
        self.first_expansion = self.stage["expansion_years"][0]
        initial_name = self.stage["initial_alternative"]
        self.initial_T = self.p["alternatives"][initial_name]["T"]
        self.expanded_T = self.stage["expanded_T"]
        self.p["alternatives"]["D"] = {"T": self.expanded_T, "max_hubs_2027": None}
        source = read_output(self.out / f"network_plan_{initial_name}.csv")
        fixed = source[source.open_year.eq(self.years[0])].copy()
        require(len(fixed) > 0 and fixed.site_id.is_unique, "B의 첫해 계획을 확인할 수 없습니다")
        self.fixed_plan = canonical(
            (self.index[r.site_id], int(r.open_year), self.end if pd.isna(r.close_year) or r.close_year >= self.first_expansion else int(r.close_year))
            for r in fixed.itertuples() if pd.isna(r.close_year) or r.close_year > r.open_year
        )
        self.initial_state = {}

    def threshold(self, year):
        return self.initial_T if year < self.first_expansion else self.expanded_T

    def operating(self, plan, T, scenario, band, details=False):
        key = scenario, band
        if key not in self.initial_state:
            self.initial_state[key] = super().operating(self.fixed_plan, self.initial_T, scenario, band, True)
        early_ops, early_state = self.initial_state[key]
        late_ops, state = super().operating(plan, self.expanded_T, scenario, band, True)
        ops = pd.concat([early_ops[early_ops.year.lt(self.first_expansion)], late_ops[late_ops.year.ge(self.first_expansion)]], ignore_index=True)
        if not details:
            return ops
        early = self.years < self.first_expansion
        for field, values in state.items():
            values[early] = early_state[field][early]
        return ops, state


class StagedSearch(Search):
    def initial(self):
        return self.m.fixed_plan

    def better(self, current, candidate):
        return candidate["max_funding"] <= self.m.limit and super().better(current, candidate)


def run_search(model):
    pilots = [i for i, _, _ in model.fixed_plan if i in model.pilots]
    search = StagedSearch(model, "D", "greedy", pilots)
    require(search.score(model.fixed_plan)["max_funding"] <= model.limit, "D의 고정 출발안이 자금 한도를 초과합니다")
    phases = model.p["phases"]
    try:
        model.p["phases"] = model.stage["expansion_years"]
        return search.run()
    finally:
        model.p["phases"] = phases


def verify(model, result, kpi, annual, state, coverage):
    rows = []
    def check(metric, actual, expected, passed):
        check_row(rows, "D", metric, actual, expected, passed)
    plan = result["plan"]
    early = model.years < model.first_expansion
    for y in model.years[early]:
        active = {i for i, o, c in plan if o <= y < c}
        fixed = {i for i, o, c in model.fixed_plan if o <= y < c}
        check(f"B_fixed_active_{y}", len(active), len(fixed), active == fixed)
    base_assignment = read_output(model.out / f"assignment_{model.stage['initial_alternative']}.csv")
    for year in sorted(set(base_assignment.year) & set(model.years[early])):
        old = base_assignment[base_assignment.year.eq(year)].sort_values("adm_cd2")
        new = model.assignment_frame(state, year).sort_values("adm_cd2")
        match = old.site_id.fillna("").tolist() == new.site_id.fillna("").tolist()
        diff = abs(old.expected_orders_per_day.to_numpy()-new.expected_orders_per_day.to_numpy()).max()
        check(f"B_saved_assignment_{year}", diff, 0, match and np.isclose(diff, 0, atol=1e-9))
    maximum = {"initial": 0., "expanded": 0.}
    errors = 0
    for j, year in enumerate(model.years):
        active = [i for i, o, c in plan if o <= year < c]
        selected = state["assigned"][j]
        mask = selected >= 0
        minimum = model.peak[active].min(axis=0) if active else np.full(len(selected), np.inf)
        errors += np.count_nonzero(mask != (minimum <= model.threshold(year)))
        distances = model.peak[selected[mask], np.flatnonzero(mask)]
        errors += np.count_nonzero(distances != minimum[mask])
        stage = "initial" if early[j] else "expanded"
        maximum[stage] = max(maximum[stage], distances.max(initial=0.))
    check("nearest_assignment_errors", errors, 0, errors == 0)
    check("max_peak_before_expansion", maximum["initial"], model.initial_T, maximum["initial"] <= model.initial_T)
    check("max_peak_after_expansion", maximum["expanded"], model.expanded_T, maximum["expanded"] <= model.expanded_T)
    violations = sum(o < model.available_years[i] for i, o, _ in plan)
    check("availability_violations", violations, 0, violations == 0)
    capacity = (state["hub_served"]/model.capacity).max()
    check("accepted_capacity_ratio_max", capacity, 1, capacity <= 1+1e-12)
    check("funding_krw", kpi["max_funding"], model.limit, kpi["max_funding"] <= model.limit)
    trace = pd.DataFrame(result["trace"])
    if len(trace):
        check("all_moves_under_funding_limit", trace.funding_after.max(), model.limit, trace.funding_after.le(model.limit).all())
        check("all_moves_after_fixed_period", trace.year.min(), model.first_expansion, trace.year.ge(model.first_expansion).all())
    check("npv_cash_flow_sum", annual.discounted_cf.sum(), kpi["npv"], np.isclose(annual.discounted_cf.sum(), kpi["npv"]))
    for r in coverage.itertuples():
        y = int(r.year-model.years[0])
        assigned = model.dong.loc[state["assigned"][y] >= 0, "adm_cd2"]
        pop = model.population[model.population.year.eq(r.year)][["gu","households_index"]]
        master = model.dong.merge(pop, on="gu", validate="many_to_one")
        households = master.households_2026*master.households_index
        ratio = households[master.adm_cd2.isin(assigned)].sum()/households.sum()
        check(f"coverage_{r.year}", r.coverage, ratio, np.isclose(r.coverage, ratio, rtol=1e-12))
    return rows


def protected_hashes(model):
    folders = [model.out, ROOT / model.cfg["pilot_diagnosis"]["figures_dir"]]
    return {path:sha256(path.read_bytes()).hexdigest() for folder in folders for path in folder.iterdir()
            if path.is_file() and re.search(r"(?:^|_)(A|B|C|B2)(?:_|\.|$)", path.name)}


def append_comparison(path, rows):
    """기존 행의 문자열을 보존하고 이전 D 행만 교체한다."""
    existing = read_output(path)
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    require(len(lines) == len(existing)+1, "비교표 CSV의 물리적 행 구조가 달라졌습니다")
    require(set(rows.columns) == set(existing.columns), "D 비교표 컬럼 불일치")
    legacy = b"".join(line for line in lines if not line.startswith(b"D,"))
    newline = "\r\n" if lines[0].endswith(b"\r\n") else "\n"
    if not legacy.endswith(b"\n"):
        legacy += newline.encode()
    temporary = path.with_suffix(".D.tmp")
    appended = rows.reindex(columns=existing.columns).to_csv(index=False, header=False, lineterminator=newline).encode("utf-8")
    temporary.write_bytes(legacy+appended)
    reloaded = read_output(temporary)
    require(reloaded[reloaded.alternative.ne("D")].reset_index(drop=True).equals(existing[existing.alternative.ne("D")].reset_index(drop=True)), "기존 비교표 값 변경")
    temporary.replace(path)
    return len(existing[existing.alternative.ne("D")])


def run(cfg):
    cfg = deepcopy(cfg)
    model = StagedModel(cfg)
    out = model.out
    status = out / "network_run_status_D.json"
    status.write_text('{"status":"running"}', encoding="utf-8")
    old = protected_hashes(model)
    try:
        require(json.loads((out / "network_run_status.json").read_text(encoding="utf-8"))["status"] == "complete", "기존 대안 실행 완료가 필요합니다")
        before = read_output(out / "alt_compare.csv")
        result = run_search(model)
        plan = result["plan"]
        kpi, annual, _, state = model.evaluate(plan, model.expanded_T, details=True)
        yearly = model.summaries(plan, state)
        coverage = yearly[yearly.year.isin(model.stage["coverage_years"])][["year", "active_hubs", "households_served", "households_total", "coverage", "orders_per_day"]].copy()
        coverage.insert(1,"T",coverage.year.map(model.threshold))
        checks = verify(model, result, kpi, annual, state, coverage)
        csv(out / "network_plan_D.csv", model.plan_frame(plan, explicit_pilots=True))
        csv(out / "network_start_D.csv", model.plan_frame(model.fixed_plan, explicit_pilots=True))
        csv(out / "network_coverage_D.csv", coverage)
        csv(out / "network_search_trace_D.csv", pd.DataFrame(result["trace"]))
        csv(out / "network_search_phases_D.csv", pd.DataFrame(result["phases"]))
        csv(out / "network_hub_demand_D.csv", hub_table(model, plan, state))
        years = sorted(set(model.p["assignment_years"]) | set(model.stage["coverage_years"]))
        assignments = pd.concat([model.assignment_frame(state, y) for y in years], ignore_index=True)
        csv(out / "assignment_D.csv", assignments)
        result_rows = pd.DataFrame([comparison_row(model,"D",result,s,True) for s in model.p["scenario_order"]])
        c = before[before.alternative.eq(model.stage["comparison_alternative"]) & before.selected.eq(1)].set_index("scenario",verify_integrity=True)
        deltas = []
        for r in result_rows.itertuples():
            for band,suffix in [("low","_low"),("mid",""),("high","_high")]:
                c_npv = float(c.loc[r.scenario,"npv"+suffix])
                d_npv = getattr(r,"npv"+suffix)
                delta = c_npv-d_npv
                deltas.append(dict(scenario=r.scenario,demand_band=band,npv_C_krw=c_npv,npv_D_krw=d_npv,investor_condition_cost_krw=delta,
                                   npv_C_eok=c_npv/model.engine.unit,npv_D_eok=d_npv/model.engine.unit,investor_condition_cost_eok=delta/model.engine.unit))
        cost = pd.DataFrame(deltas)
        csv(out / "investor_condition_cost.csv", cost)
        # 기존 지도 함수는 연도별 T를 제목에 읽는다. D 전용 설정만 바꿔 호출한다.
        for y in years:
            model.p["assignment_years"] = [y]
            model.p["alternatives"]["D"]["T"] = model.threshold(y)
            maps(model,"D",plan,state)
        model.p["assignment_years"] = years
        model.p["alternatives"]["D"]["T"] = model.expanded_T
        unchanged = sum(path.is_file() and sha256(path.read_bytes()).hexdigest() == digest for path,digest in old.items())
        check_row(checks,"D","legacy_files_unchanged",unchanged,len(old),unchanged == len(old))
        n_old = append_comparison(out / "alt_compare.csv", result_rows)
        check_row(checks,"D","legacy_comparison_rows_preserved",n_old,len(before[before.alternative.ne("D")]),n_old == len(before[before.alternative.ne("D")]))
        csv(out / "network_verification_D.csv", pd.DataFrame(checks))
        (out / "network_validation_D.md").write_text(
            "# 대안 D: B로 시작한 뒤 서비스 범위 확대\n\n"
            f"{model.first_expansion-1}년까지 B의 2027년 개설 상태와 T={model.initial_T}분을 유지한다. "
            f"{model.first_expansion}년부터 T={model.expanded_T}분으로 넓히고 구간 시작 때 거점을 추가·폐점·교체한다. "
            "base·중앙 수요에서 개선된 변경은 모두 500억 원 이하다. 확정 계획을 시장 3개 × 주문율 3개에 적용한다. "
            "비교표의 D.T는 확장 이후 값이다.\n\n"
            "## 커버리지\n\n"+md_table(coverage)+"\n\n## C와의 NPV 차이\n\n"
            "C−D가 양수이면 D의 NPV가 그만큼 작다. 음수이면 해당 조합에서 D의 NPV가 더 크다.\n\n"
            +md_table(cost[["scenario","demand_band","npv_C_eok","npv_D_eok","investor_condition_cost_eok"]])
            +"\n\n## 검산\n\n"+md_table(pd.DataFrame(checks))+"\n", encoding="utf-8")
        status.write_text(json.dumps(dict(status="complete",evaluations=model.evaluations,checks_passed=len(checks),assignment_rows=len(assignments),maps=len(years)),indent=2),encoding="utf-8")
        print(coverage.to_string(index=False), flush=True)
        print(cost.to_string(index=False), flush=True)
    except Exception as exc:
        status.write_text(json.dumps(dict(status="blocked", error=str(exc)), ensure_ascii=False),encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    run(load_config(parser.parse_args().config))
