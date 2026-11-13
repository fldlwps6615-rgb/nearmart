"""직접 검산: 파일럿 재고 양쪽 경로, 배정 최단시간, 수요 수지, 재무 재호출."""
from copy import deepcopy

import numpy as np
import pandas as pd

from src.finance.engine import FinanceEngine
from src.network.model import require
from src.network.search import canonical


def check_row(rows, alternative, metric, actual, expected, passed, detail=""):
    rows.append(dict(alternative=alternative, metric=metric, actual=actual, expected=expected,
                     passed=int(bool(passed)), detail=detail))
    require(passed, f"검산 실패 {alternative}/{metric}: {actual} vs {expected}")


def inventory_checks(model):
    rows = []
    legacy_cfg = deepcopy(model.cfg)
    legacy_cfg["finance"].pop("pilot_initial_inventory_krw", None)
    legacy = FinanceEngine(legacy_cfg)
    none = ()
    ops = model.operating(none, 15, "base", "mid")
    new, _ = model.engine.evaluate(model.plan_frame(none), ops)
    old, _ = legacy.evaluate(model.plan_frame(none), ops)
    recovery = model.inventory.initial_inventory_krw.sum()*model.engine.par("inventory_recovery_at_close")
    observed = new.iloc[0].cash_flow-old.iloc[0].cash_flow
    check_row(rows, "ALL", "pilot_exit_recovery", observed, recovery, np.isclose(observed, recovery), "기존 계산 대비 t=0 CF 증가=동일 추정 재고×80%")
    plan = canonical([(i, int(model.years[0]), model.end) for i in model.pilots])
    ops = model.operating(plan, 15, "base", "mid")
    kept, _ = model.engine.evaluate(model.plan_frame(plan), ops)
    first_cogs = kept.loc[kept.year.eq(model.years[0]), "cogs"].iloc[0]
    expected = first_cogs/model.days_per_year*model.engine.par("inventory_days_of_cogs")-model.inventory.initial_inventory_krw.sum()
    observed = kept.loc[kept.year.eq(model.years[0]), "inventory_investment"].iloc[0]
    check_row(rows, "ALL", "pilot_keep_same_initial_stock", observed, expected, np.isclose(observed, expected), "계속 운영 2027 재고투입=목표 재고−동일 2026 기초 재고")
    check_row(rows, "ALL", "pilot_initial_balance", kept.iloc[0].inventory_balance, model.inventory.initial_inventory_krw.sum(),
              np.isclose(kept.iloc[0].inventory_balance, model.inventory.initial_inventory_krw.sum()))
    for i in model.pilots:
        sid = model.ids[i]
        plan = ((i, int(model.years[0]), int(model.years[0])+1),)
        ops = model.operating(plan, 15, "base", "mid")
        closed, _ = model.engine.evaluate(model.plan_frame(plan), ops)
        last = closed.loc[closed.year.eq(model.years[0])].iloc[0]
        expected = last.cogs/model.days_per_year*model.engine.par("inventory_days_of_cogs")*model.engine.par("inventory_recovery_at_close")
        check_row(rows, "ALL", f"{sid}_later_close_inventory", last.inventory_recovery, expected, np.isclose(last.inventory_recovery, expected))
    # WGS84 이동시간 재계산 없이 제공 OD의 경계값으로 구간 단가를 검산.
    points = np.array([0, 8, 8.01, 12, 12.01, 15, 15.01, 20, 20.01])
    values = np.array(model.p["rider_cost_2027"])[np.searchsorted(model.p["rider_bin_max"], points, side="left")]
    check_row(rows, "ALL", "rider_band_boundaries", str(values.tolist()), str([3200,3200,3400,3400,3600,3600,4500,4500,6000]),
              np.array_equal(values, [3200,3200,3400,3400,3600,3600,4500,4500,6000]))
    return rows


def verify_plan(model, alternative, plan, state, annual, kpi, summary, rows, *, require_funding=True):
    p = model.p["alternatives"][alternative]
    T = p["T"]
    if require_funding:
        check_row(rows, alternative, "funding_limit", kpi["max_funding_eok"], model.limit/model.engine.unit,
                  kpi["max_funding"] <= model.limit)
    violations = sum(o < model.sites.iloc[i].available_from_year for i, o, _ in plan)
    check_row(rows, alternative, "availability", violations, 0, violations == 0)
    max_time, nearest_errors = 0., 0
    for y, year in enumerate(model.years):
        active = [i for i, o, c in plan if o <= year < c]
        selected = state["assigned"][y]
        valid = selected >= 0
        if active:
            minimum = model.peak[active].min(axis=0)
            expected_valid = minimum <= T
            nearest_errors += int(np.count_nonzero(valid != expected_valid))
            times = model.peak[selected[valid], np.flatnonzero(valid)]
            if len(times):
                max_time = max(max_time, times.max())
                nearest_errors += int(np.count_nonzero(~np.isclose(times, minimum[valid])))
    check_row(rows, alternative, "nearest_assignment", nearest_errors, 0, nearest_errors == 0)
    check_row(rows, alternative, "max_assigned_peak_min", max_time, T, max_time <= T)
    ratio = state["hub_served"]/model.capacity
    check_row(rows, alternative, "capacity_served_ratio_max", ratio.max(), 1, ratio.max() <= 1+1e-12)
    balance = abs(state["requested"].sum(axis=1)-state["served"].sum(axis=1)-summary.lost_orders_per_day).max()
    check_row(rows, alternative, "demand_balance", balance, 0, np.isclose(balance, 0, atol=1e-8))
    for year in [2027, 2037]:
        y = int(year-model.years[0])
        codes = model.dong.loc[state["assigned"][y] >= 0, "adm_cd2"]
        pop = model.population[model.population.year.eq(year)][["gu", "households_index"]]
        source = model.dong.merge(pop, on="gu", validate="many_to_one")
        source["h"] = source.households_2026*source.households_index
        manual = source.loc[source.adm_cd2.isin(codes), "h"].sum()/source.h.sum()
        reported = summary.set_index("year").loc[year, "coverage"]
        check_row(rows, alternative, f"coverage_{year}", reported, manual, np.isclose(reported, manual, rtol=1e-12))
    if p["max_hubs_2027"] is not None:
        hubs = summary.loc[summary.year.eq(2027), "active_hubs"].iloc[0]
        check_row(rows, alternative, "staged_hubs_2027", int(hubs), p["max_hubs_2027"], hubs <= p["max_hubs_2027"])
    cash_orders = annual[annual.year.isin(model.years)].orders.to_numpy()
    expected = state["served"].sum(axis=1)*model.days_per_year
    check_row(rows, alternative, "finance_order_total", abs(cash_orders-expected).max(), 0, np.allclose(cash_orders, expected, rtol=1e-12))
    check_row(rows, alternative, "npv_sum", annual.discounted_cf.sum(), kpi["npv"], np.isclose(annual.discounted_cf.sum(), kpi["npv"]))


def hub_table(model, plan, state):
    rows = []
    for i, o, c in plan:
        for y in np.flatnonzero((model.years >= o) & (model.years < c)):
            mask = state["assigned"][y] == i
            q = state["served"][y, mask]
            times = model.delivery[i, mask]
            rows.append(dict(year=model.years[y], site_id=model.ids[i], hub_type=model.sites.iloc[i].hub_type,
                             assigned_dongs=mask.sum(), ramp_factor=state["hub_ramp"][y, i],
                             requested_per_day=state["hub_demand"][y, i], accepted_per_day=state["hub_served"][y, i],
                             capacity_per_day=model.capacity[i], requested_capacity_ratio=state["hub_demand"][y,i]/model.capacity[i],
                             accepted_capacity_ratio=state["hub_served"][y,i]/model.capacity[i],
                             lost_per_day=state["hub_demand"][y,i]-state["hub_served"][y,i],
                             max_peak_min=model.peak[i,mask].max() if mask.any() else 0.,
                             avg_delivery_min=np.average(times,weights=q) if q.sum() else np.nan,
                             rider_cost_2027=state["hub_rider_cost"][y,i]))
    return pd.DataFrame(rows)
