"""고정 계획에 적용할 운영 충격과 재계약 실패 이전 목록."""
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from src.common.io import load_file
from src.demand.model import competition
from src.network.feedback import COST
from src.network.model import read_output, require


WAGES = {"picker_wage_krw_per_h_2027", "employed_wage_krw_per_h_2027"}
CONTRACT_RATES = [
    "contract_base_krw", "contract_extra_order_krw", "contract_km_rate_krw",
    "contract_peak_surcharge_krw", "contract_weather_surcharge_rain_krw",
    "contract_weather_surcharge_heavy_krw", "contract_min_hourly_earning_krw",
]


def wage_rates(engine, settings):
    """15번의 단가와 수급 기준에 연도별 기본 상승률 및 W1 배수를 적용한다."""
    years = np.arange(engine.start, engine.end + 1)
    factor = np.where(years >= settings["start_year"], 1 + settings["increase"], 1.)
    rows = []
    for parameter in [*sorted(WAGES), *CONTRACT_RATES]:
        growth = engine.par("wage_growth" if parameter in WAGES else "contract_fee_growth")
        base = engine.par(parameter) * (1 + growth) ** (years - engine.start)
        rows.append(pd.DataFrame({"year": years, "parameter": parameter, "base_krw": base,
                                  "w1_krw": base * factor, "shock_factor": factor}))
    return pd.concat(rows, ignore_index=True)


def wage_ops(ops, settings):
    shocked = ops.copy()
    affected = shocked.year.ge(settings["start_year"])
    shocked.loc[affected, COST] *= 1 + settings["increase"]
    return shocked


def competition_demands(model, demand_config, settings, markets, band):
    competitors = load_file("09_competitor_hubs.csv", config=model.cfg)
    rules = load_file("10_market_rules.csv", config=model.cfg)
    entrants = competitors.competitor.eq("B(what-if)")
    require(entrants.sum() == settings["competitor_count"] and competitors.comp_hub_id.is_unique,
            "09 경쟁 거점 수 또는 키 오류")
    require(competitors.loc[entrants, "close_year"].isna().all() and
            competitors.loc[entrants, "open_year"].notna().all(), "09 진입 거점 개폐점 연도 확인 필요")
    competitors.loc[entrants, "open_year"] = settings["start_year"]
    base_cfg = {**demand_config, "w2": False}
    shock_cfg = {**demand_config, "w2": True, "w2_start_year": settings["start_year"]}
    before, after = [], []
    for year in model.years:
        _, base_factor = competition(model.dong, competitors, year, rules, base_cfg)
        _, shock_factor = competition(model.dong, competitors, year, rules, shock_cfg)
        before.append(base_factor)
        after.append(shock_factor)
    before, after = np.asarray(before), np.asarray(after)
    require(((0 < after) & (after <= before)).all(), "W2 경쟁 계수 범위 오류")
    require(np.array_equal(after[model.years < settings["start_year"]],
                           before[model.years < settings["start_year"]]), "W2 개점 전 계수 불일치")

    # 이미 A를 반영한 전망을 A+B로 교체한다. 시장지수와 수요율은 한 번만 반영된다.
    published = read_output(model.out / "demand_year_summary.csv").set_index(["scenario", "year"])
    result = {}
    for market in markets:
        demand = model.demands[market, band]
        potential = demand / before
        if band == "mid":
            np.testing.assert_allclose(potential.sum(axis=1),
                                       published.loc[market].loc[model.years, "orders_before_competition"],
                                       rtol=1e-10, atol=1e-6)
        result[market, band] = demand * (after / before)
    return result, int((after < before).sum())


def relocation_mappings(model, rows, travel, settings):
    """같은 유형의 미사용 입주 후보에 1:1로 배정하고 총 이전 이동시간을 최소화한다."""
    rights = int(model.engine.par("renewal_right_years"))
    used = {row["site_id"] for row in rows}
    candidates = model.sites[model.sites.status.eq("AVAILABLE") & ~model.sites.site_id.isin(used)]
    origins = sorted((row["open_year"] + rights, row["site_id"], row["hub_type"])
                     for row in rows if row["open_year"] + rights < row["close_year"])
    if not origins:
        return []
    sites = model.sites.set_index("site_id")
    minutes = np.full((len(origins), len(candidates)), np.inf)
    for i, (year, source, hub_type) in enumerate(origins):
        start = sites.loc[source]
        for j, target in enumerate(candidates.itertuples(index=False)):
            if target.hub_type == hub_type and target.available_from_year <= year:
                minutes[i, j] = travel.minutes(start.lat, start.lon, start.gu, target.lat, target.lon, target.gu,
                                                settings["depart_hour"], settings["weather"])
    try:
        matched, targets = linear_sum_assignment(minutes)
    except ValueError as exc:
        raise ValueError("10년 재계약 실패: 입주 가능한 동일 유형의 미사용 이전지 부족") from exc
    require(len(matched) == len(origins), "10년 재계약 실패: 이전지 수 부족")
    return [dict(from_site_id=origins[i][1], to_site_id=candidates.iloc[j].site_id,
                 year=origins[i][0], hub_type=origins[i][2], relocation_travel_min=minutes[i, j])
            for i, j in zip(matched, targets)]
