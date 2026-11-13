"""실행: python -m src.network.run --config config.json."""
import argparse
import hashlib
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.network.model import NetworkModel, read_output, require
from src.network.search import family_worker
from src.network.verify import check_row, hub_table, inventory_checks, verify_plan


def csv(path, frame):
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def add_issue(output, rule, n, detail):
    path = output / "data_issues_log.csv"
    existing = read_output(path)
    rule_id = "NETWORK_"+rule
    existing = existing[existing.rule_id.ne(rule_id)]
    row = dict(rule_id=rule_id, file="config.json/network.seed_sites", check=detail, n_rows=n, example="", action="FLAG | "+detail)
    csv(path, pd.concat([existing, pd.DataFrame([row])], ignore_index=True))


def comparison_row(model, name, result, scenario, chosen):
    plan, family = result["plan"], result["search"]
    T = model.p["alternatives"][name]["T"]
    kpi, annual, ops, state = model.evaluate(plan, T, scenario, "mid", details=True)
    yearly = model.summaries(plan, state)
    y = yearly.set_index("year")
    row = dict(alternative=name, search=family, pilot_branch=result["pilot_branch"], selected=int(chosen), scenario=scenario,
               T=T, **{k: v for k, v in kpi.items() if k != "scenario"})
    row["discounted_payback_years"] = kpi["payback_year"]-2026 if isinstance(kpi["payback_year"], int) else np.nan
    for year in model.p["phases"]:
        row[f"hubs_{year}"] = y.loc[year, "active_hubs"]
    for year in [2027, 2037]:
        row[f"coverage_{year}"] = y.loc[year, "coverage"]
    for year in [2027, 2031, 2036, 2046]:
        row[f"orders_{year}"] = y.loc[year, "orders_per_day"]
        row[f"lost_orders_per_day_{year}"] = y.loc[year, "lost_orders_per_day"]
    row["lost_orders_total"] = yearly.lost_orders_annual.sum()
    row["lost_revenue_krw"] = yearly.lost_revenue_krw.sum()
    row["lost_revenue_eok"] = row["lost_revenue_krw"]/model.engine.unit
    for band in ["low", "high"]:
        bk, ba, _, _ = model.evaluate(plan, T, scenario, band, details=True)
        row[f"npv_{band}"] = bk["npv"]
        row[f"npv_{band}_eok"] = bk["npv_eok"]
        row[f"max_funding_{band}"] = bk["max_funding"]
        row[f"max_funding_{band}_eok"] = bk["max_funding_eok"]
        row[f"funding_within_limit_{band}"] = bk["funding_within_limit"]
        if chosen:
            csv(model.out / f"finance_{name}_{scenario}_rate_{band}.csv", ba)
    if chosen:
        csv(model.out / f"finance_{name}_{scenario}.csv", annual)
        csv(model.out / f"network_ops_{name}_{scenario}.csv", ops)
        csv(model.out / f"network_year_{name}_{scenario}.csv", yearly)
    return row


def run(cfg):
    out = ROOT / cfg["paths"]["outputs"]
    status_path = out / "network_run_status.json"
    status_path.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    raw_hash = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").rglob('*') if p.is_file()}
    start = perf_counter()
    try:
        model = NetworkModel(cfg)
        csv(out / "network_pilot_inventory.csv", model.inventory)
        (out / "network_effective_config.json").write_text(json.dumps(model.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        missing = [s for s in model.p["seed_sites"] if s not in model.index]
        add_issue(out, "SEED_EXCLUDED", len(missing), f"정제 제외 후보지 {missing}는 사전안 출발점에서 제외; 정제 후보지 미사용 제약 적용")
        checks = inventory_checks(model)
        csv(out / "network_verification.csv", pd.DataFrame(checks))
        comparisons, all_branches, trace, phases, selected_results = [], [], [], [], {}
        seed_audit = []
        for sid in model.p["seed_sites"]:
            seed_audit.append(dict(site_id=sid, valid=int(sid in model.index),
                                   available_from_year=model.sites.iloc[model.index[sid]].available_from_year if sid in model.index else np.nan,
                                   action="출발점 후보" if sid in model.index else "정제 제외로 미사용"))
        csv(out / "network_seed_audit.csv", pd.DataFrame(seed_audit))
        # 체크포인트는 입력 파일·계산 코드·설정이 같을 때만 재사용한다.
        hasher = hashlib.sha256(json.dumps(cfg,sort_keys=True).encode())
        sources = [*sorted((ROOT / "src/network").glob("*.py")), ROOT / "src/finance/engine.py",
                   *sorted((ROOT / cfg["paths"]["clean"]).glob("*.csv")),
                   *[out / name for name in ["demand_dong_year.csv","demand_effects.csv","order_profile.csv","demand_daily.csv"]]]
        for source in sources:
            hasher.update(source.read_bytes())
        fingerprint = hasher.hexdigest()
        executor = ProcessPoolExecutor(max_workers=model.p["search_workers"])
        futures = {(name,family):executor.submit(family_worker,cfg,name,family,fingerprint)
                   for name in model.p["alternatives"] for family in ["greedy","seed"]}
        worker_evaluations = 0
        for name in model.p["alternatives"]:
            results = []
            for family in ["greedy", "seed"]:
                result, branches, evaluated = futures[name,family].result()
                worker_evaluations += evaluated
                results.append(result)
                csv(out / f"network_plan_{name}_{family}.csv", model.plan_frame(result["plan"], explicit_pilots=True))
                csv(out / f"network_start_{name}_{family}.csv", model.plan_frame(result["initial"], explicit_pilots=True))
                for b in branches:
                    all_branches.append(dict(alternative=name, search=family, pilot_branch=b["pilot_branch"], **b["kpi"]))
                    trace.extend(b["trace"])
                    phases.extend(b["phases"])
                csv(out / "network_search_branches.csv", pd.DataFrame(all_branches))
                csv(out / "network_search_trace.csv", pd.DataFrame(trace))
                csv(out / "network_search_phases.csv", pd.DataFrame(phases))
            feasible = [r for r in results if r["kpi"]["max_funding"] <= model.limit]
            selected = max(feasible, key=lambda r:r["kpi"]["npv"]) if feasible else None
            for r in results:
                for scenario in model.p["scenario_order"]:
                    comparisons.append(comparison_row(model, name, r, scenario, r is selected))
            csv(out / "alt_compare.csv", pd.DataFrame(comparisons))
            if selected is None:
                add_issue(out, f"NO_FEASIBLE_{name}", 1, f"{name}: 두 탐색 모두 자금 한도 내 최종안 없음; 탐색결과만 저장")
                continue
            selected_results[name] = selected
            plan = selected["plan"]
            kpi, annual, _, state = model.evaluate(plan, model.p["alternatives"][name]["T"], details=True)
            summary = model.summaries(plan, state)
            verify_plan(model, name, plan, state, annual, kpi, summary, checks)
            csv(out / f"network_plan_{name}.csv", model.plan_frame(plan, explicit_pilots=True))
            assignments = pd.concat([model.assignment_frame(state, y) for y in model.p["assignment_years"]], ignore_index=True)
            csv(out / f"assignment_{name}.csv", assignments)
            csv(out / f"network_hub_demand_{name}.csv", hub_table(model, plan, state))
            from src.network.report import maps
            maps(model, name, plan, state)
            csv(out / "network_verification.csv", pd.DataFrame(checks))
        # 저장된 배정과 선택안 합계를 독립 재읽기 검산.
        for name in selected_results:
            assignment = read_output(out / f"assignment_{name}.csv")
            year = read_output(out / f"network_year_{name}_base.csv").set_index("year")
            totals = assignment.groupby("year").expected_orders_per_day.sum()
            check_row(checks, name, "assignment_saved_rows", len(assignment), len(model.dong)*len(model.p["assignment_years"]),
                      len(assignment) == len(model.dong)*len(model.p["assignment_years"]) and not assignment.duplicated(["year","adm_cd2"]).any())
            errors = abs(totals-year.loc[totals.index, "orders_per_day"])
            check_row(checks, name, "assignment_saved_sum", errors.max(), 0,
                      np.allclose(totals, year.loc[totals.index, "orders_per_day"], rtol=model.p["numeric_rtol"]))
        executor.shutdown()
        unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p,h in raw_hash.items())
        check_row(checks, "ALL", "raw_files_unchanged", len(raw_hash), len(raw_hash), unchanged)
        csv(out / "network_verification.csv", pd.DataFrame(checks))
        from src.network.report import write_report
        write_report(model, pd.DataFrame(comparisons), selected_results, checks)
        final_status = "complete" if len(selected_results) == len(model.p["alternatives"]) else "complete_with_infeasible_alternatives"
        status_path.write_text(json.dumps(dict(status=final_status, selected_alternatives=list(selected_results),
                                              evaluations=model.evaluations+worker_evaluations, seconds=perf_counter()-start,
                                              config_sha256=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),
                                              checks_passed=len(checks)), ensure_ascii=False, indent=2), encoding="utf-8")
        print(pd.DataFrame(comparisons).query("selected == 1 and scenario == 'base'")[["alternative","search","npv_eok","max_funding_eok","hubs_2027","coverage_2027","orders_2027"]].to_string(index=False), flush=True)
    except Exception as exc:
        status_path.write_text(json.dumps(dict(status="blocked",error=str(exc)),ensure_ascii=False,indent=2),encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    run(load_config(parser.parse_args().config))
