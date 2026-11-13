"""실행: python -B -m src.whatif.run --plan-version v3"""
import argparse
from time import monotonic

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from src.finance.engine import FinanceEngine
from src.network.feedback import FeedbackModel, prepare
from src.network.model import read_output, require
from src.travel_time import TravelTime
from src.whatif.scenarios import competition_demands, relocation_mappings, wage_ops, wage_rates
from src.whatif.summary import add_comparisons, make_summary


LABOR = ["contract_rider_cost", "employed_rider_cost", "picker_cost"]
KEY = ["alternative", "scenario"]


def as_network_plan(model, rows):
    return tuple(sorted((model.index[row["site_id"]], row["open_year"], row["close_year"])
                        for row in rows if row["open_year"] < row["close_year"]))


def evaluate(plan, ops, market, engine):
    annual, kpi = engine.evaluate(plan, ops, market)
    annual = annual.set_index("year")
    np.testing.assert_allclose(annual.orders, ops.groupby("year").orders_per_day.sum()
                               .reindex(annual.index, fill_value=0) * engine.days, rtol=1e-12, atol=1e-5)
    np.testing.assert_allclose(kpi["npv"], sum(cf / (1 + kpi["discount_rate"]) ** t
                                             for t, cf in enumerate(annual.cash_flow)), rtol=1e-12, atol=1e-4)
    np.testing.assert_allclose(kpi["max_funding"], max(0., -annual.cash_flow.cumsum().min()),
                               rtol=1e-12, atol=1e-4)
    return annual, kpi


def result_row(plan_version, alternative, market, band, case, radius, annual, kpi, yearly, settings):
    order_year = settings["orders_year"]
    return dict(plan_version=plan_version, alternative=alternative, case=case, scenario=market, demand_band=band,
                T=radius, discount_rate=kpi["discount_rate"], npv=kpi["npv"], npv_eok=kpi["npv_eok"],
                max_funding=kpi["max_funding"], max_funding_eok=kpi["max_funding_eok"],
                ebitda_positive_year=kpi["ebitda_positive_year"],
                **{f"coverage_{year}": yearly.loc[year, "coverage"] for year in settings["coverage_years"]},
                **{f"orders_{order_year}": yearly.loc[order_year, "orders_per_day"],
                   f"orders_{order_year}_annual": annual.loc[order_year, "orders"]})


def verify_baseline(results, path):
    expected = read_output(path).query("selected == 1").set_index(KEY).sort_index()
    actual = results.query("case == 'BASE'").set_index(KEY).sort_index()
    require(expected.index.equals(actual.index), "버전별 기본 비교표와 대안·시장 키 불일치")
    numeric = ["T", "discount_rate", "npv", "max_funding"]
    numeric += [column for column in ("coverage_2027", "coverage_2037", "orders_2036") if column in actual]
    np.testing.assert_allclose(actual[numeric], expected[numeric], rtol=1e-12, atol=0.01)
    require(actual.ebitda_positive_year.astype(str).equals(expected.ebitda_positive_year.astype(str)),
            "버전별 기본 EBITDA 흑자 연도 불일치")
    return float((actual.npv - expected.npv).abs().max())


def evaluate_alternative(name, path, model, settings, version, travel, shocked_demands):
    radius, engine = model.radius, model.engine
    source = read_output(path)
    rows = engine._plan(source)
    plan = as_network_plan(model, rows)
    mappings = relocation_mappings(model, rows, travel, settings["forced_relocation"])
    forced_engine, forced_plan = engine, plan
    if mappings:
        finance = dict(model.cfg["finance"], nonrenewal_mode="forced", forced_relocations=mappings)
        forced_engine = FinanceEngine({**model.cfg, "finance": finance})
        forced_rows = forced_engine._plan(source)
        forced_plan = as_network_plan(model, forced_rows)
        by_site = {row["site_id"]: row for row in forced_rows}
        for move in mappings:
            origin, destination = by_site[move["from_site_id"]], by_site[move["to_site_id"]]
            require(origin["close_year"] == destination["open_year"] == move["year"] and
                    destination["relocated_from"] == origin["site_id"], "강제 이전 개폐점 불일치")

    band = settings["demand_band"]
    result = []
    for market in settings["markets"]:
        ops, state = model.operating(plan, radius, market, band, details=True)
        yearly = model.summaries(plan, state).set_index("year")
        base, base_kpi = evaluate(source, ops, market, engine)
        cases = [("BASE", base, base_kpi, yearly)]

        wages, wage_kpi = evaluate(source, wage_ops(ops, settings["w1"]), market, engine)
        factor = np.where(base.index >= settings["w1"]["start_year"], 1 + settings["w1"]["increase"], 1.)
        np.testing.assert_allclose(wages[LABOR], base[LABOR].mul(factor, axis=0), rtol=1e-12, atol=1e-4)
        np.testing.assert_allclose(wages.ebitda - base.ebitda, -(wages[LABOR] - base[LABOR]).sum(axis=1),
                                   rtol=1e-12, atol=1e-4)
        cases.append(("W1", wages, wage_kpi, yearly))

        key = market, band
        original = model.demands[key]
        try:
            model.demands[key] = shocked_demands[key]
            w2_ops, w2_state = model.operating(plan, radius, market, band, details=True)
        finally:
            model.demands[key] = original
        w2, w2_kpi = evaluate(source, w2_ops, market, engine)
        np.testing.assert_array_equal(w2_state["assigned"], state["assigned"])
        pd.testing.assert_frame_equal(w2.loc[w2.index < settings["w2"]["start_year"]],
                                      base.loc[base.index < settings["w2"]["start_year"]])
        require((w2.orders <= base.orders + 1e-5).all(), "W2 배송 주문이 기본보다 증가")
        cases.append(("W2", w2, w2_kpi, model.summaries(plan, w2_state).set_index("year")))

        forced_ops, forced_state = model.operating(forced_plan, radius, market, band, details=True)
        forced, forced_kpi = evaluate(source, forced_ops, market, forced_engine)
        require(forced.expected_relocation_cost.eq(0).all(), "강제 이전 비용과 재계약 기댓값 중복")
        cases.append(("NONRENEWAL", forced, forced_kpi,
                      model.summaries(forced_plan, forced_state).set_index("year")))

        # 할인율은 재무 엔진의 할인 단계에만 전달한다. 임시 설정은 다음 호출 전에 복원한다.
        base_rate = engine.f["discount_rate"]
        try:
            for case, rate in settings["discount_rates"].items():
                engine.f["discount_rate"] = rate
                discounted, discount_kpi = evaluate(source, ops, market, engine)
                pd.testing.assert_series_equal(discounted.cash_flow, base.cash_flow)
                cases.append((case, discounted, discount_kpi, yearly))
        finally:
            engine.f["discount_rate"] = base_rate

        result.extend(result_row(version, name, market, band, case, radius, annual, kpi, metrics, settings)
                      for case, annual, kpi, metrics in cases)
    return result, [dict(plan_version=version, alternative=name, **m) for m in mappings]


def run(settings, version):
    started = monotonic()
    current = load_config()
    config = prepare(current)
    out = ROOT / config["paths"]["outputs"]
    plans = {name: out / f"network_plan_{name}_{version}.csv" for name in config["network"]["alternatives"]}
    basis_path, compare_path = out / f"feedback_basis_{version}.csv", out / f"alt_compare_{version}.csv"
    missing = [path.name for path in [*plans.values(), basis_path, compare_path] if not path.is_file()]
    require(not missing, f"{version} 입력 준비 필요: {', '.join(missing)}")
    config["finance"]["issues_log_path"] = str(out / "whatif_data_issues_log.csv")
    require(config["finance"]["nonrenewal_mode"] == "expected", "기본 비교는 재계약 기댓값 모드 필요")
    basis = read_output(basis_path).set_index("T")
    require(basis.index.is_unique, f"{basis_path.name} 반경 키 중복")
    models = {radius: FeedbackModel(config, radius, basis.loc[radius])
              for radius in sorted({spec["T"] for spec in config["network"]["alternatives"].values()})}
    model = next(iter(models.values()))
    band, markets = settings["demand_band"], settings["markets"]
    require(set(markets) == set(model.p["scenario_order"]) and band == "mid", "시장 3종·중앙 수요 필요")
    w2, affected_dong_years = competition_demands(model, current["demand"], settings["w2"], markets, band)
    rates = wage_rates(model.engine, settings["w1"])
    rates.insert(0, "plan_version", version)
    travel = TravelTime(ROOT / config["paths"]["clean"])
    rows, moves = [], []
    for name, path in plans.items():
        radius = config["network"]["alternatives"][name]["T"]
        case_rows, case_moves = evaluate_alternative(name, path, models[radius], settings, version, travel, w2)
        rows.extend(case_rows)
        moves.extend(case_moves)

    results = add_comparisons(pd.DataFrame(rows), settings)
    expected = len(plans) * len(markets) * (4 + len(settings["discount_rates"]))
    require(len(results) == expected and not results.duplicated([*KEY, "case"]).any(), "결과 행 수·키 검산 실패")
    baseline_error = verify_baseline(results, compare_path)
    move_columns = ["plan_version", "alternative", "from_site_id", "to_site_id", "year", "hub_type", "relocation_travel_min"]
    moves = pd.DataFrame(moves, columns=move_columns)
    elapsed = monotonic() - started
    require(elapsed < 300, "계산 시간 300초 상한 초과")
    summary = make_summary(results, settings, moves, rates, affected_dong_years, baseline_error, elapsed)
    tables = {"whatif_results.csv": results, "whatif_w1_rates.csv": rates, "whatif_nonrenewal_moves.csv": moves}
    pending = []
    try:
        for name, frame in tables.items():
            target = out / name
            temporary = target.with_suffix(".csv.tmp")
            pending.append((temporary, target))
            frame.to_csv(temporary, index=False, encoding="utf-8-sig")
            require(len(read_output(temporary)) == len(frame), f"{name} 저장 행 수 검산 실패")
        target = out / "whatif_summary.md"
        temporary = target.with_suffix(".md.tmp")
        pending.append((temporary, target))
        temporary.write_text(summary, encoding="utf-8")
        for temporary, target in pending:
            temporary.replace(target)
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
    print(f"{version} | 결과 {len(results)}행 | 기본 NPV 최대 오차 {baseline_error:.6f}원 | 이전 {len(moves)}건 | {monotonic() - started:.2f}초")


def main():
    settings = load_config(ROOT / "src/whatif/config.json")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-version", choices=("v3", "v4"), default=settings["default_plan_version"])
    args = parser.parse_args()
    try:
        run(settings, args.plan_version)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"what-if 중단 | {exc}\n")


if __name__ == "__main__":
    main()
