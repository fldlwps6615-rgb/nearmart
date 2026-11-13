"""검증 a~f, 경계 검증 및 검산 워크북 입력 생성. python -m src.finance.verify"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from .engine import FinanceEngine, OPS_COLUMNS, PLAN_COLUMNS


def make_plan(engine, ids, opening=None, closing=None):
    return pd.DataFrame([
        dict(site_id=s, hub_type=engine.sites.loc[s, "hub_type"],
             open_year=opening or engine.start, close_year=closing or engine.end + 1, relocated_from="")
        for s in ids
    ], columns=PLAN_COLUMNS)


def make_ops(engine, plan, orders=None):
    """명시적인 임시 운영값. 실제 운영 추정치가 아니며 config에서만 변경."""
    v = engine.f["validation"]
    batch = v["batch_size"]
    contract = (engine.par("contract_base_krw") + (batch - 1) * engine.par("contract_extra_order_krw")) / batch
    minutes = v["employed_minutes_per_order"]
    employed = (engine.par("employed_wage_krw_per_h_2027") * engine.par("labor_load_factor") * minutes / v["minutes_per_hour"]
                + engine.par("employed_incentive_krw")
                + engine.par("employed_vehicle_krw_per_month") * minutes / (v["fte_hours_per_month"] * v["minutes_per_hour"]))
    pick_minutes = engine.par("pick_time_base_min") + engine.par("pick_time_per_item_min") * v["items_per_order"]
    picker = engine.par("picker_wage_krw_per_h_2027") * engine.par("labor_load_factor") * pick_minutes / v["minutes_per_hour"]
    if not np.isclose(v["contract_order_share"] + v["employed_order_share"] + v["external_quick_share"], 1):
        raise ValueError("검산용 배송 수단 비율 합계는 1이어야 합니다.")
    rows = []
    for r in plan.to_dict("records"):
        for year in range(r["open_year"], r["close_year"]):
            rows.append(dict(
                year=year, site_id=r["site_id"], orders_per_day=v["orders_per_day"] if orders is None else orders,
                avg_basket_krw=v["avg_basket_krw"], under_40000_share=v["under_40000_share"],
                cold_order_share=v["cold_order_share"], contract_rider_cost_per_order_krw=contract * v["contract_order_share"],
                employed_rider_cost_per_order_krw=employed * v["employed_order_share"], picker_cost_per_order_krw=picker,
                late_rate=v["late_rate"], external_quick_share=v["external_quick_share"],
            ))
    return pd.DataFrame(rows, columns=OPS_COLUMNS)


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "data").rglob("*")) if p.is_file()}


def run(config=None):
    before = hashes()
    cfg = load_config(config)
    e = FinanceEngine(cfg)
    target = ROOT / "outputs" / "finance_validation"
    target.mkdir(parents=True, exist_ok=True)
    results = []

    def check(name, actual, expected, tolerance=0.0001, note=""):
        if isinstance(expected, (str, bool)):
            passed = actual == expected
            difference = ""
        else:
            difference = float(actual) - float(expected)
            passed = abs(difference) <= tolerance
        results.append(dict(check=name, actual=actual, expected=expected, difference=difference, passed=bool(passed), note=note))
        if not passed:
            raise AssertionError(f"{name}: {actual} != {expected}")

    annual, kpi = e.evaluate([], [], "base")
    hq_pv = -e.par("hq_fixed_krw_per_year_2027") / (1 + e.rate("discount_rate")) * sum(
        ((1 + e.par("hq_fixed_growth")) / (1 + e.rate("discount_rate"))) ** i for i in range(e.end - e.start + 1))
    pilots = e.sites.loc[sorted(e.pilots)]
    withdrawal = float((pilots.deposit_krw - pilots.area_m2 * e.par("restoration_krw_per_m2")).sum())
    check("a_hq_pv", float(-annual.hq_cost.div((1 + e.rate('discount_rate')) ** e.t).sum()), hq_pv)
    check("a_pilot_withdrawal", float(annual.iloc[0].cash_flow), withdrawal)
    check("a_npv", kpi["npv"], hq_pv + withdrawal)
    check("a_tax_zero", float(annual.tax.sum()), 0)
    annual.to_csv(ROOT / "outputs/finance_zero_base.csv", index=False, encoding="utf-8-sig")
    zero_kpi = kpi

    p1 = make_plan(e, ["P1"])
    op1 = make_ops(e, p1)
    p1a, p1k = e.evaluate(p1, op1, "base")
    p1a.to_csv(ROOT / "outputs/finance_p1_base.csv", index=False, encoding="utf-8-sig")
    p1.to_csv(target / "plan_p1.csv", index=False, encoding="utf-8-sig")
    op1.to_csv(target / "ops_p1.csv", index=False, encoding="utf-8-sig")
    row27, row28, final = (p1a.loc[p1a.year.eq(y)].iloc[0] for y in (e.start, e.start + 1, e.end))
    check("b_fixed_daily_orders", float(row27.orders), e.f["validation"]["orders_per_day"] * e.days)
    check("split_contract_inflation", float(row28.contract_rider_cost / row27.contract_rider_cost), 1 + e.par("contract_fee_growth"))
    check("split_employed_inflation", float(row28.employed_rider_cost / row27.employed_rider_cost), 1 + e.par("wage_growth"))
    check("split_picker_inflation", float(row28.picker_cost / row27.picker_cost), 1 + e.par("wage_growth"))

    feasible = list(e.sites.index[e.sites.available_from_year.le(e.start)])
    profitable = make_plan(e, feasible)
    profit_ops = make_ops(e, profitable)
    profa, profk = e.evaluate(profitable, profit_ops, "base")
    cfg16 = deepcopy(cfg)
    cfg16["finance"]["discount_rate"] = e.f["validation"]["sensitivity_discount_rate"]
    _, k16 = FinanceEngine(cfg16).evaluate(profitable, profit_ops, "base")
    check("c_positive_operating_cf", bool((profa.loc[profa.year.ge(e.start), "cash_flow"] > 0).all()), True)
    check("c_npv_decreases", k16["npv"] < profk["npv"], True,
          note=f"12%={profk['npv']}, 16%={k16['npv']}; 실입주 가능한 2027년 후보지만")
    profa.to_csv(ROOT / "outputs/finance_feasible_base.csv", index=False, encoding="utf-8-sig")

    check("d_deposit_return", float(final.deposit_returned), float(e.sites.loc["P1", "deposit_krw"]))
    check("d_inventory_recovery", float(final.inventory_recovery), float(final.cogs / e.days * e.par("inventory_days_of_cogs") * e.par("inventory_recovery_at_close")))
    check("d_restoration", float(final.restoration), float(e.sites.loc["P1", "area_m2"] * e.par("restoration_krw_per_m2")))
    cf_without_close = (final.revenue - (final.revenue - final.ebitda - final.restoration) - final.tax
                        - final.inventory_investment - final.capex - final.deposit_paid - final.expected_relocation_cost)
    check("d_terminal_cf_bridge", float(final.cash_flow - cf_without_close), float(final.deposit_returned + final.inventory_recovery - final.restoration))
    check("d_inventory_zero", float(final.inventory_balance), 0)
    check("d_asset_book_zero", float(final.asset_book_value), 0)

    candidate = next(s for s in feasible if s not in e.pilots)
    early = make_plan(e, [candidate])
    later_year = e.start + int(e.par("lease_term_years"))
    late = make_plan(e, [candidate], opening=later_year)
    ea, _ = e.evaluate(early, make_ops(e, early), "base")
    la, _ = e.evaluate(late, make_ops(e, late), "base")
    factor = (1 + e.par("market_rent_growth")) ** (later_year - e.start)
    check("e_rent_factor", float(la.loc[la.year.eq(later_year), "rent"].iloc[0] / ea.loc[ea.year.eq(e.start), "rent"].iloc[0]), factor)
    check("e_deposit_factor", float(la.deposit_paid.sum() / ea.deposit_paid.sum()), factor)
    check("e_capex_factor", float(la.capex_initial.sum() / ea.capex_initial.sum()), factor)
    la.to_csv(ROOT / "outputs/finance_late_open_base.csv", index=False, encoding="utf-8-sig")

    stress_cfg = deepcopy(cfg)
    stress_cfg["finance"]["allow_early_open_for_stress_test"] = True
    stress = FinanceEngine(stress_cfg)
    all_plan = make_plan(stress, list(stress.sites.index))
    all_ops = make_ops(stress, all_plan)
    all_a, all_k = stress.evaluate(all_plan, all_ops, "base")
    all_a.to_csv(ROOT / "outputs/finance_all_2027_stress_base.csv", index=False, encoding="utf-8-sig")
    all_plan.to_csv(target / "plan_all_2027_stress.csv", index=False, encoding="utf-8-sig")
    all_ops.to_csv(target / "ops_all_2027_stress.csv", index=False, encoding="utf-8-sig")
    check("f_funding", all_k["max_funding"], max(0, -float(np.cumsum(all_a.cash_flow.to_numpy()).min())))
    check("f_hub_count", float(all_a.loc[all_a.year.eq(e.start), "active_hubs"].iloc[0]), len(e.sites))

    # 세무 옵션: 정상적인 연간 상각과 잔여 장부가의 현금 지출을 구분한다.
    tax_ops = make_ops(e, late, orders=e.f["validation"]["orders_per_day"] * e.par("depreciation_years"))
    no_writeoff, _ = e.evaluate(late, tax_ops, "base")
    tax_cfg = deepcopy(cfg)
    tax_cfg["finance"]["deduct_unamortized_book_value_on_close"] = True
    writeoff, _ = FinanceEngine(tax_cfg).evaluate(late, tax_ops, "base")
    closing = no_writeoff.iloc[-1]
    check("terminal_residual_positive", closing.unamortized_book_value_at_close > 0, True)
    check("terminal_default_no_deduction", float(closing.tax_book_value_deduction), 0)
    expected_saving = closing.unamortized_book_value_at_close * e.par("tax_rate")
    check("terminal_writeoff_tax_saving", float(no_writeoff.tax.sum() - writeoff.tax.sum()), float(expected_saving), tolerance=0.001)
    check("terminal_writeoff_only_tax_cash", float(writeoff.cash_flow.sum() - no_writeoff.cash_flow.sum()), float(expected_saving), tolerance=0.001)
    check("terminal_no_salvage", float(closing.asset_book_value), 0)
    loss_ops = tax_ops.copy()
    loss_ops.loc[loss_ops.year.eq(later_year), "orders_per_day"] = 0
    loss_a, _ = e.evaluate(late, loss_ops, "base")
    loss_first = loss_a.loc[loss_a.year.eq(later_year)].iloc[0]
    loss_next = loss_a.loc[loss_a.year.eq(later_year + 1)].iloc[0]
    check("tax_first_loss_no_tax", float(loss_first.tax), 0)
    deductible_loss = min(loss_first.tax_loss_carryforward, max(0, loss_next.ebit))
    check("tax_loss_carry_used", float(loss_next.tax_loss_used), float(deductible_loss))
    check("tax_loss_saving", float(loss_next.ebit * e.par("tax_rate") - loss_next.tax), float(deductible_loss * e.par("tax_rate")), tolerance=0.001)
    check("tax_unused_loss_preserved", float(loss_next.tax_loss_carryforward), float(loss_first.tax_loss_carryforward - deductible_loss))

    r = ea.set_index("year")
    check("rent_5y", float(r.loc[e.start + 5, "rent"] / r.loc[e.start, "rent"]), 1 + e.par("renewal_rent_increase_cap"))
    check("rent_10y", float(r.loc[e.start + 10, "rent"] / r.loc[e.start, "rent"]), (1 + e.par("market_rent_growth")) ** 10)
    check("rent_15y", float(r.loc[e.start + 15, "rent"] / r.loc[e.start + 10, "rent"]), 1 + e.rate("renewal_15y_increase"))
    initial = float(ea.capex_initial.sum())
    check("refresh_10th_end", float(r.loc[e.start + 9, "capex_refresh"]), initial * e.par("capex_refresh_ratio_10y"))
    check("refresh_depreciation_next_year", float(r.loc[e.start + 10, "depreciation"]), initial * e.par("capex_refresh_ratio_10y") / e.par("depreciation_years"))
    check("initial_prior_year", float(r.loc[e.start - 1, "capex_initial"]), initial)
    check("pilot_renewal_2037", bool((p1a.loc[p1a.expected_relocation_cost.gt(0), "year"].to_numpy() == e.start + 10).all()), True)

    forced_cfg = deepcopy(cfg)
    forced_cfg["finance"].update(nonrenewal_mode="forced", forced_relocations=[dict(from_site_id="P1", to_site_id=candidate)])
    forced_e = FinanceEngine(forced_cfg)
    transformed = pd.DataFrame(forced_e._plan(p1))
    forced_ops = make_ops(forced_e, transformed)
    forced_a, _ = forced_e.evaluate(p1, forced_ops, "base")
    explicit_a, _ = e.evaluate(transformed, forced_ops, "base")
    check("forced_explicit_relocation_match", float(np.max(np.abs(forced_a.cash_flow - explicit_a.cash_flow))), 0)
    check("forced_no_expected_double_count", float(forced_a.expected_relocation_cost.sum()), 0)
    check("forced_new_capex", float(forced_a.capex_initial.sum()), float(e.types.loc[e.sites.loc[candidate, 'hub_type'], 'capex_krw'] * (1 + e.par('capex_price_growth')) ** 10))

    # 의도적 오류 입력은 실제 데이터 이슈와 분리한 테스트 로그에 기록한다.
    bad_cfg = deepcopy(cfg)
    bad_cfg["finance"]["issues_log_path"] = "outputs/finance_validation/rejected_input_cases.csv"
    bad_e = FinanceEngine(bad_cfg)
    cases = {"missing": op1.iloc[:-1], "duplicate": pd.concat([op1, op1.iloc[:1]]),
             "nan": op1.assign(avg_basket_krw=np.nan), "ratio": op1.assign(late_rate=1.01),
             "negative": op1.assign(orders_per_day=-1)}
    for label, bad_ops in cases.items():
        try:
            bad_e.evaluate(p1, bad_ops, "base")
        except ValueError:
            check("reject_" + label, True, True)
        else:
            check("reject_" + label, False, True)
    unavailable = next(s for s in e.sites.index if e.sites.loc[s, "available_from_year"] > e.start)
    bad_plan = make_plan(e, [unavailable])
    try:
        bad_e.evaluate(bad_plan, make_ops(e, bad_plan), "base")
    except ValueError:
        check("reject_early_open", True, True)
    else:
        check("reject_early_open", False, True)

    # 전체 68개 거점, 1,360개 ops행으로 데이터/검증/계산/반환을 모두 포함하여 측정.
    times = []
    for _ in range(e.f["validation"]["benchmark_repetitions"]):
        t0 = perf_counter()
        stress.evaluate(all_plan, all_ops, "base")
        times.append(perf_counter() - t0)
    timing = {"calls": len(times), "sites": len(all_plan), "ops_rows": len(all_ops), "mean_seconds": float(np.mean(times)),
              "p95_seconds": float(np.percentile(times, 95)), "max_seconds": float(np.max(times)),
              "target_seconds": e.f["validation"]["performance_target_seconds"]}
    timing["all_calls_within_target"] = timing["max_seconds"] <= timing["target_seconds"]
    check("source_hash_unchanged", before == hashes(), True)
    payload = {"parameters": {k: (float(v) if k not in ('horizon', 'cash_timing') else v) for k, v in e.p.items()},
               "finance_config": e.f, "pilot1": e.sites.loc["P1"].to_dict(), "pilot2": e.sites.loc["P2"].to_dict(),
               "type": e.types.loc["M"].to_dict(), "ops": op1.iloc[0].to_dict(),
               "annual": p1a.loc[p1a.year.le(e.start + 1)].to_dict("records")}
    (target / "workbook_inputs.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "a": {**zero_kpi, "hq_npv_eok": hq_pv / e.unit, "pilot_withdrawal_eok": withdrawal / e.unit},
        "b": {**p1k, "workbook_check": "pending until build_check.mjs runs"},
        "c": {"npv_12_eok": profk["npv_eok"], "npv_16_eok": k16["npv_eok"], "sites": len(feasible)},
        "d": {"deposit_returned": float(final.deposit_returned), "inventory_recovery": float(final.inventory_recovery), "restoration": float(final.restoration)},
        "e": {"site_id": candidate, "factor": factor},
        "f": {**all_k, "sites": len(all_plan), "early_open_sites": int(e.sites.available_from_year.gt(e.start).sum()),
              "note": "전 후보지 2027년 개점 스트레스 검산. 입주 가능 연도만 해제한 비실행 계획; 임시 하루 900건/거점."},
        "terminal_writeoff": {"residual_book_value": float(closing.unamortized_book_value_at_close), "tax_saving_if_elected": float(expected_saving)},
        "timing": timing, "checks_passed": len(results), "source_files_unchanged": len(before),
    }
    pd.DataFrame(results).to_csv(target / "checks.csv", index=False, encoding="utf-8-sig")
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    run(parser.parse_args().config)
