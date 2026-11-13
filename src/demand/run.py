"""실행: python -m src.demand.run --config config.json (저장소 루트)."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.io import ROOT, active_od, load_config, load_file
from src.demand.model import (competition, fit_daily, linear_fit, predict_rate,
                              ramp, require, select_rate_model)


class Output:
    def __init__(self, cfg):
        self.path = ROOT / cfg["paths"]["outputs"]
        self.path.mkdir(parents=True, exist_ok=True)
        self.issues = []
        self.checks = []

    def csv(self, name, frame):
        frame.to_csv(self.path / name, index=False, encoding="utf-8-sig")

    def issue(self, rule, file, n, detail, action="OBSERVE"):
        self.issues.append(dict(rule_id=f"DEMAND_{rule}", file=file, check=detail, n_rows=int(n), example="",
                                action=f"{action} | {detail}"))

    def flush_issues(self):
        path = self.path / "data_issues_log.csv"
        existing = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        existing = existing[~existing.rule_id.str.startswith("DEMAND_")]
        self.csv("data_issues_log.csv", pd.concat([existing, pd.DataFrame(self.issues)], ignore_index=True))

    def check(self, metric, actual, expected, passed, detail=""):
        self.checks.append(dict(metric=metric, actual=actual, expected=expected, passed=int(bool(passed)), detail=detail))
        require(passed, f"검산 실패: {metric}: {actual} / {expected}")


def load_inputs(cfg, output):
    status = json.loads((output.path / "clean_run_status.json").read_text(encoding="utf-8-sig"))
    require(status["status"] == "complete", "정제 단계 완료 상태가 아닙니다")
    names = ["01_dong_master.csv", "05_pilot_orders.csv", "06_pilot_calendar_weather.csv",
             "07_market_scenarios.csv", "08_population_projection.csv", "09_competitor_hubs.csv",
             "10_market_rules.csv", "04_hub_types.csv"]
    dong, orders, calendar, market_sections, population, competitors, rules, types = [load_file(n, config=cfg) for n in names]
    market = next(v for k, v in market_sections.items() if k.startswith("[B]"))
    od = active_od(cfg)
    require(dong.adm_cd2.is_unique and calendar.date.is_unique, "동 또는 달력 키 중복")
    orders = orders[orders.demand_included.eq(1) & orders.status.eq("DELIVERED")].copy()
    require(orders.order_id.is_unique, "수요 주문 중복")
    require(set(orders.adm_cd2) <= set(dong.adm_cd2), "동 마스터에 없는 주문")
    for col in ["order_ts", "delivered_ts"]:
        orders[col] = pd.to_datetime(orders[col], errors="raise")
    orders["date"] = orders.order_ts.dt.normalize()
    orders["hour"] = orders.order_ts.dt.hour
    calendar["date"] = pd.to_datetime(calendar.date, errors="raise")
    calendar["weekday_num"] = calendar.date.dt.weekday
    require(set(orders.date) <= set(calendar.date), "달력에 없는 주문일")
    require(calendar.weather.notna().all(), "날씨 결측")
    dong["log_household_density"] = np.log(dong.households_2026 / dong.area_km2)
    require(np.isfinite(dong[cfg["demand"]["candidate_variables"]].to_numpy(float)).all(), "동 특성 결측/무한대")
    require(orders.basket_krw.notna().all() and orders.building_type.notna().all(), "주문 프로필 결측")
    output.issue("TIMESTAMP_EXCLUSION", "05_pilot_orders.csv", orders.delivered_ts.isna().sum(),
                 "전달시각 결측은 수요·프로필 포함, 배송시간 계수용 평균에서만 제외", "FLAG")
    output.issue("EXTERNAL_TIME", "05_pilot_orders.csv", orders.rider_id.eq(cfg["cleaning"]["external_rider_id"]).sum(),
                 "외부 퀵은 수요·프로필 포함, 기존 delivery_kpi_eligible 규약에 따라 평균 배송시간에서 제외", "FLAG")
    return dong, orders, calendar, market, population, competitors, rules, types, od


def make_daily(orders, calendar, cfg):
    counts = orders.groupby(["hub_id", "date"]).size().rename("orders").reset_index()
    daily = []
    for hub, opened in cfg["pilot_diagnosis"]["opening_dates"].items():
        part = calendar[calendar.date.ge(pd.Timestamp(opened))].copy()
        part["hub_id"] = hub
        part["elapsed"] = (part.date-pd.Timestamp(opened)).dt.days
        part = part.merge(counts, on=["hub_id", "date"], how="left", validate="one_to_one")
        # 달력의 영업일에 대응하는 주문 행이 없으면 실제 주문 수 0이다. 결측 주문을 추정하지 않는다.
        part["orders"] = part.orders.fillna(0).astype(int)
        daily.append(part)
    return pd.concat(daily, ignore_index=True).sort_values(["hub_id", "date"]).reset_index(drop=True)


def rate_training(dong, orders, daily, candidates, cfg, competitors, rules, types, output):
    settings = cfg["demand"]
    selected = candidates.loc[candidates.selected.eq(1)].iloc[0]
    daily["ramp_level"] = ramp(daily.elapsed, selected.a, selected.tau)
    daily["effective_exposure"] = daily.calendar_factor * daily.ramp_level
    exposure = daily.groupby("hub_id").agg(effective_days=("effective_exposure", "sum"), operating_days=("date", "size"))
    training = orders.groupby(["hub_id", "adm_cd2"]).size().rename("n_orders").reset_index()
    # 현재 파일럿 동은 한 거점씩만 관측된다. 중복 권역에서는 관측 노출일의 재정의가 필요하다.
    require(training.adm_cd2.is_unique, "한 동에 여러 파일럿 거점: 중복 노출 처리 결정 필요")
    eligible = orders[orders.delivery_kpi_eligible.eq(1) & orders.rider_id.ne(cfg["cleaning"]["external_rider_id"])].copy()
    eligible["delivery_min"] = (eligible.delivered_ts-eligible.order_ts).dt.total_seconds()/60
    require(eligible.delivery_min.notna().all() and eligible.delivery_min.ge(0).all(), "배송시간 표본 결측/음수")
    times = eligible.groupby("adm_cd2").agg(avg_delivery_min=("delivery_min", "mean"), delivery_n=("delivery_min", "size"))
    counts, factors = competition(dong, competitors, settings["base_year"], rules, {**settings, "w2": False})
    reference = dong.assign(competition_count=counts, competition_factor=factors)
    training = training.merge(reference, on="adm_cd2", validate="one_to_one").merge(exposure, on="hub_id", validate="many_to_one")
    training = training.merge(times, on="adm_cd2", how="left", validate="one_to_one")
    keep = training.n_orders.ge(settings["minimum_dong_orders"])
    output.issue("LOW_ORDER_DONG", "05_pilot_orders.csv", (~keep).sum(),
                 f"기간 주문 {settings['minimum_dong_orders']}건 미만 동은 주문율 적합에서 제외; 주문 자체와 프로필에서는 보존", "FILTER")
    output.csv("demand_excluded_dong.csv", training.loc[~keep, ["hub_id", "adm_cd2", "n_orders"]])
    training = training[keep].copy()
    require(training.avg_delivery_min.notna().all(), "학습 동의 관측 배송시간 없음: 계수를 임의 보충할 수 없음")
    rule_map = rules[rules.rule.eq("delivery_time_multiplier")].set_index("key").value
    # [0,25), [25,35), [35,45), [45,55], (55,inf): 55분 초과라는 원문을 보존.
    bins = np.searchsorted(settings["delivery_bin_edges"], training.avg_delivery_min, side="right")
    bins[training.avg_delivery_min.eq(settings["delivery_bin_edges"][-1])] = len(settings["delivery_bin_edges"])-1
    training["delivery_factor"] = np.array([float(rule_map[k]) for k in settings["delivery_bin_keys"]])[bins]
    assortment = float(types.set_index("hub_type").loc[settings["pilot_hub_type"], "assortment_demand_factor"])
    training["assortment_factor"] = assortment
    training["mature_daily_orders"] = training.n_orders / training.effective_days
    training["base_rate_observed"] = training.mature_daily_orders / training.households_2026 / training.competition_factor / training.delivery_factor / training.assortment_factor
    rebuilt = training.base_rate_observed * training.households_2026 * training.competition_factor * training.delivery_factor * training.assortment_factor
    output.check("three_factor_division", float(np.max(np.abs(rebuilt-training.mature_daily_orders))), 0,
                 np.allclose(rebuilt, training.mature_daily_orders, atol=1e-10),
                 "성숙 하루 주문 ÷ 세대수 ÷ 경쟁 계수 ÷ 배송시간 계수 ÷ 구색 계수; 역산 최대 절대오차(건/일)")
    return training


def rate_outputs(dong, training, candidates, cfg, output):
    p = cfg["demand"]
    model, models = select_rate_model(training, p)
    output.csv("demand_model_selection.csv", models)
    accepted = candidates[candidates.accepted.eq(1)]
    central = candidates[candidates.selected.eq(1)].iloc[0]
    relative = [accepted.mature_div_last_week.min()/central.mature_div_last_week, 1.,
                accepted.mature_div_last_week.max()/central.mature_div_last_week]
    low, mid, high = predict_rate(dong, model, p["prediction_coverage"])
    # 외삽은 선택된 모형 설명변수 각각의 학습 범위를 기준으로 한다.
    extrapolated = np.zeros(len(dong), dtype=bool)
    ranges = []
    for var in model["variables"]:
        lower, upper = training[var].min(), training[var].max()
        extrapolated |= ~dong[var].between(lower, upper).to_numpy()
        ranges.append(dict(variable=var, pilot_min=lower, pilot_max=upper, seoul_min=dong[var].min(), seoul_max=dong[var].max(),
                           outside_count=int((~dong[var].between(lower, upper)).sum())))
    output.csv("demand_feature_ranges.csv", pd.DataFrame(ranges, columns=["variable", "pilot_min", "pilot_max", "seoul_min", "seoul_max", "outside_count"]))
    components = pd.DataFrame(dict(adm_cd2=dong.adm_cd2, model_rate_low=low, model_rate_mid=mid, model_rate_high=high,
                                   ramp_relative_low=relative[0], ramp_relative_mid=1., ramp_relative_high=relative[2],
                                   extrapolated=extrapolated.astype(int)))
    output.csv("demand_rate_components.csv", components)
    rates = pd.DataFrame(dict(adm_cd2=dong.adm_cd2, rate_low=low*relative[0], rate_mid=mid, rate_high=high*relative[2],
                              extrapolated=extrapolated.astype(int)))
    output.csv("base_rate_dong.csv", rates)
    # 선택된 변수 고정 LOOCV 외에, 변수 선택도 각 훈련 fold 안에서 다시 하는 nested LOOCV를 함께 제공.
    fitted = predict_rate(training, model, p["prediction_coverage"])[1]
    validation = training[["hub_id", "adm_cd2", "n_orders", "base_rate_observed"]].copy()
    validation["fitted_rate"] = fitted
    fixed_cv, nested_cv, nested_vars = [], [], []
    for i in range(len(training)):
        fold = training.drop(training.index[i])
        heldout = training.iloc[[i]]
        fixed = linear_fit(fold, model["variables"])
        nested, _ = select_rate_model(fold, p)
        fixed_cv.append(predict_rate(heldout, fixed, p["prediction_coverage"])[1][0])
        nested_cv.append(predict_rate(heldout, nested, p["prediction_coverage"])[1][0])
        nested_vars.append("|".join(nested["variables"]))
    validation["loocv_rate"] = fixed_cv
    validation["nested_loocv_rate"] = nested_cv
    validation["nested_variables"] = nested_vars
    output.csv("demand_pilot_predictions.csv", validation)
    model_json = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in model.items()}
    model_json.update(prediction_coverage=p["prediction_coverage"], ramp_relative=relative,
                      nested_cv_rmse_log=float(np.sqrt(np.mean(np.log(validation.base_rate_observed/validation.nested_loocv_rate)**2))))
    (output.path / "demand_model.json").write_text(json.dumps(model_json, ensure_ascii=False, indent=2), encoding="utf-8")
    return rates, model, relative, validation


def forecast(dong, population, competitors, market, rules, model, relative, cfg, output):
    p = cfg["demand"]
    frames, decomposed = [], []
    years = [p["base_year"], *range(p["forecast_start_year"], p["forecast_end_year"]+1)]
    require(not population.duplicated(["gu", "year"]).any() and market.year.is_unique, "예측 입력 키 중복")
    for year in years:
        current = dong.merge(population[population.year.eq(year)], on="gu", how="left", validate="many_to_one")
        require(current.households_index.notna().all() and current.single_hh_ratio_change_pp.notna().all(), f"{year} 인구추계 결측")
        households = current.households_2026 * current.households_index
        if "single_hh_ratio" in model["variables"]:
            current["single_hh_ratio"] += current.single_hh_ratio_change_pp/100
            require(current.single_hh_ratio.between(0, 1).all(), "장래 1인가구 비율 범위 이탈: 자동 절단하지 않음")
        low, mid, high = predict_rate(current, model, p["prediction_coverage"])
        low, high = low*relative[0], high*relative[2]
        counts, factors = competition(current, competitors, year, rules, p)
        m = market.loc[market.year.eq(year)]
        require(len(m) == 1, f"{year} 시장지수 없음")
        for scenario in p["scenarios"]:
            index = float(m.iloc[0][f"index_{scenario}"])
            orders = pd.DataFrame(dict(scenario=scenario, year=year, adm_cd2=current.adm_cd2,
                                       orders_low=households*low*index*factors,
                                       orders_mid=households*mid*index*factors,
                                       orders_high=households*high*index*factors))
            frames.append(orders)
            decomposed.append(dict(scenario=scenario, year=year, market_index=index, households=households.sum(),
                                   competition_hh_weighted=np.average(factors, weights=households),
                                   orders_before_competition=(households*mid*index).sum(),
                                   orders_low=orders.orders_low.sum(), orders_mid=orders.orders_mid.sum(), orders_high=orders.orders_high.sum(),
                                   orders_without_market=(households*mid*factors).sum()))
    demand = pd.concat(frames, ignore_index=True)
    yearly = pd.DataFrame(decomposed)
    output.csv("demand_dong_year.csv", demand)
    output.csv("demand_year_summary.csv", yearly)
    return demand, yearly


def profiles(dong, orders, cfg, output):
    threshold = dong.single_hh_ratio.median()
    labels = dong[["adm_cd2"]].assign(area_type=np.where(dong.single_hh_ratio.ge(threshold), "single_high", "single_low"))
    x = orders.merge(labels, on="adm_cd2", validate="many_to_one")
    x["day_type"] = np.where(x.date.dt.weekday.ge(5), "weekend", "weekday")
    start, end = cfg["cleaning"]["service_start_min"]//60, cfg["cleaning"]["service_end_min"]//60
    require(x.hour.between(start, end).all(), "시간대 범위 밖 주문")
    index = pd.MultiIndex.from_product([["weekday", "weekend"], ["single_low", "single_high"], range(start, end+1)],
                                      names=["day_type", "area_type", "hour"])
    hourly = x.groupby(["day_type", "area_type", "hour"]).size().reindex(index, fill_value=0).rename("n_orders").reset_index()
    totals = hourly.groupby(["day_type", "area_type"]).n_orders.transform("sum")
    require(totals.gt(0).all(), "빈 시간대 패턴 그룹: 다른 그룹의 패턴으로 대체하지 않음")
    hourly["share"] = hourly.n_orders/totals
    output.csv("hour_profile.csv", hourly[["day_type", "area_type", "hour", "share"]])
    output.csv("demand_hour_counts.csv", hourly)
    rows = []
    groups = [(('ALL', 'ALL'), x), *list(x.groupby(["day_type", "area_type"]))]
    for (day, area), group in groups:
        rows.append(dict(day_type=day, area_type=area, n_orders=len(group), avg_basket_krw=group.basket_krw.mean(),
                         avg_basket_eok=group.basket_krw.mean()/100000000,
                         under_40000_share=group.basket_krw.lt(40000).mean(), chilled_share=group.has_chilled.eq(1).mean(),
                         frozen_share=group.has_frozen.eq(1).mean(), cold_order_share=(group.has_chilled.eq(1)|group.has_frozen.eq(1)).mean(),
                         apt_share=group.building_type.eq("아파트").mean(),
                         external_quick_share=group.rider_id.eq(cfg["cleaning"]["external_rider_id"]).mean(), avg_items=group['items'].mean()))
    profile = pd.DataFrame(rows)
    output.csv("order_profile.csv", profile)
    return hourly, profile, threshold


def effects(daily_fit, candidates, model, relative, cfg, output):
    chosen = candidates[candidates.selected.eq(1)].iloc[0]
    rows = []
    def add(effect, level, value):
        rows.append(dict(effect=effect, level=level, multiplier=float(value)))
    for day, value in enumerate(daily_fit["weekday"]):
        add("weekday", str(day), value)
    add("weather", "clear", 1)
    for name, beta in zip(daily_fit["names"], daily_fit["beta"]):
        effect, level = name.split(":")
        if effect in {"weather", "holiday", "promotion"}:
            add(effect, level, np.exp(beta))
    for hub, value in daily_fit["mature"].items():
        add("hub_mature_orders", hub, value)
    add("ramp", "tau_days", chosen.tau)
    add("ramp", "a", chosen.a)
    add("ramp", "level_day90", chosen.level_day90)
    accepted = candidates[candidates.accepted.eq(1)]
    for name in ["mature_div_last_week", "mature_div_day90"]:
        for level, value in zip(["low", "mid", "high"], [accepted[name].min(), chosen[name], accepted[name].max()]):
            add(name, level, value)
    for level, value in zip(["low", "mid", "high"], relative):
        add("ramp_relative", level, value)
    for name, beta in zip(["intercept", *model["variables"]], model["beta"]):
        add("rate_log_coefficient", name, beta)
    add("rate_smearing", "all", model["smear"])
    add("calendar_reference", "rain_day_share_unused", cfg["demand"]["rain_day_share"])
    frame = pd.DataFrame(rows)
    output.csv("demand_effects.csv", frame)
    return frame


def pilot_comparison(daily, orders, dong, od, cfg):
    """분자 주문과 분모 세대에 같은 행정동 범위를 적용한다."""
    period = daily.groupby("hub_id").date.agg(["min", "max"])
    common_start, common_end = period["min"].max(), period["max"].min()
    last_week_days = cfg["demand"]["last_week_days"]
    expected = cfg["demand"]["expected"]
    rows = []
    for hub, radius in cfg["pilot_diagnosis"]["service_radius_km"].items():
        service_dongs = set(od.loc[od.site_id.eq(hub) & od.road_km.le(radius), "adm_cd2"])
        hub_orders = orders[orders.hub_id.eq(hub)]
        observed_dongs = set(hub_orders.adm_cd2)
        hub_dates = daily.loc[daily.hub_id.eq(hub), "date"]
        windows = [
            ("full_service_radius", service_dongs, hub_dates),
            ("full_observed_dongs", observed_dongs, hub_dates),
            ("common_dates_service_radius", service_dongs, hub_dates[hub_dates.between(common_start, common_end)]),
            ("last_week_service_radius", service_dongs, hub_dates.tail(last_week_days)),
        ]
        for name, codes, days in windows:
            households = dong.loc[dong.adm_cd2.isin(codes), "households_2026"].sum()
            require(len(days) > 0 and households > 0, f"{hub} {name}: 비교할 영업일 또는 세대수 없음")
            within_period = hub_orders[hub_orders.date.isin(days)]
            n_orders = within_period.adm_cd2.isin(codes).sum()
            expected_low, expected_high = expected[f"{hub}_orders_per_1000hh"]
            rate = n_orders / len(days) / households * 1000
            rows.append(dict(hub_id=hub, definition=name, start_date=days.min().date(), end_date=days.max().date(),
                             operating_days=len(days), road_radius_km=radius if name != "full_observed_dongs" else np.nan,
                             n_dongs=len(codes), households=households, n_orders=n_orders,
                             orders_outside_dongs=len(within_period)-n_orders, same_dong_sets=int(service_dongs == observed_dongs),
                             orders_per_1000hh_day=rate, expected_low=expected_low, expected_high=expected_high,
                             difference_to_expected_low=rate-expected_low))
    return pd.DataFrame(rows)


def ramp_sensitivity(seoul_orders, fitted_multiplier, expected):
    """같은 90일차 수요에서 성숙 환산 배수만 바꾸는 민감도 비교."""
    prior = expected["mature_div_day90"][1]
    upper = expected["seoul_orders_2026"][1]
    anchor = seoul_orders / fitted_multiplier
    prior_orders = anchor * prior
    return pd.DataFrame([
        ("fitted_mature_div_day90", fitted_multiplier, "배"),
        ("prior_mature_div_day90", prior, "배"),
        ("same_day90_anchor", anchor, "건/일"),
        ("with_prior_ramp", prior_orders, "건/일"),
        ("with_fitted_ramp", seoul_orders, "건/일"),
        ("fitted_minus_prior", seoul_orders-prior_orders, "건/일"),
        ("expected_upper", upper, "건/일"),
        ("prior_minus_upper", prior_orders-upper, "건/일"),
        ("fitted_minus_upper", seoul_orders-upper, "건/일"),
    ], columns=["metric", "value", "unit"])


def expectation_checks(training, candidates, yearly, effect, pilot, cfg, output):
    p = cfg["demand"]
    expect = p["expected"]
    central = candidates[candidates.selected.eq(1)].iloc[0]
    accepted = candidates[candidates.accepted.eq(1)]
    values = effect.set_index(["effect", "level"]).multiplier
    rows = []
    def add(metric, actual, bounds, explanation, expected_mid=np.nan):
        lo, hi = bounds
        rows.append(dict(metric=metric, expected_low=lo, expected_mid=expected_mid, expected_high=hi, actual=actual,
                         in_range=int(lo <= actual <= hi), interpretation=explanation))
    def point(metric, actual, expected, why):
        tolerance = expect["relative_tolerance"]
        add(metric, actual, [expected*(1-tolerance), expected*(1+tolerance)], why, expected)
    add("ramp_level_day90", central.level_day90, expect["level_day90"], "공통 a·τ의 개점 후 90일 수준")
    for label, value in [("low", accepted.mature_div_day90.min()), ("mid", central.mature_div_day90), ("high", accepted.mature_div_day90.max())]:
        prior = dict(zip(["low", "mid", "high"], expect["mature_div_day90"]))[label]
        add(f"mature_div_day90_{label}", value, [expect["mature_div_day90"][0], expect["mature_div_day90"][2]],
            f"사전 {label}={prior}; 최저 로그 RMSE의 1.10배 이내 τ만 포함, 사전값에 맞추지 않음", prior)
    weekdays = [values[("weekday", str(i))] for i in range(7)]
    point("weekend_vs_weekday", np.mean(weekdays[5:])/np.mean(weekdays[:5]), expect["weekend_factor"], "다른 효과를 통제한 주말 2일 평균 ÷ 평일 5일 평균")
    for weather in ["rain", "heavy_rain"]:
        point(weather, values[("weather", weather)], expect[weather], "맑음 대비 효과; 연간 수요에는 미적용")
    for category in ["holiday", "promotion"]:
        point(category, values[(category, "present")], expect[category], "유무 효과; 짧은 기간의 날씨·램프업과 함께 추정")
    for row in pilot[pilot.definition.eq("full_service_radius")].itertuples():
        hub = row.hub_id
        add(f"{hub}_observed_orders_per_1000hh", row.orders_per_1000hh_day, expect[f"{hub}_orders_per_1000hh"],
            "문제지 서비스 권역의 주문 ÷ 거점 실제 영업일 ÷ 같은 권역의 세대수. 사전 범위의 별도 집계 정의는 미확인")
        mature = training.loc[training.hub_id.eq(hub), "mature_daily_orders"].sum()/row.households*1000
        rows.append(dict(metric=f"{hub}_mature_orders_per_1000hh", expected_low=np.nan, expected_mid=np.nan,
                         expected_high=np.nan, actual=mature, in_range=np.nan, interpretation="성숙·표준 주간·맑음·비공휴일·비행사: 관측 비교값과 구분"))
    seoul = yearly[yearly.scenario.eq("base") & yearly.year.eq(p["base_year"])]
    add("seoul_2026_orders", seoul.orders_mid.iloc[0], expect["seoul_orders_2026"],
        "경쟁 반영·배송시간/구색 적용 전. 90일차 성숙 배수 차이의 영향은 demand_ramp_sensitivity.csv 참조")
    output.csv("demand_ramp_sensitivity.csv", ramp_sensitivity(seoul.orders_mid.iloc[0], central.mature_div_day90, expect))
    c27 = yearly[(yearly.scenario == "base") & yearly.year.eq(p["forecast_start_year"])].competition_hh_weighted.iloc[0]
    point("competition_2027", c27, expect["competition_2027"], "장래 세대수 가중; open_year≤연도<close_year, 대표점 직선 2.5km")
    table = pd.DataFrame(rows)
    output.csv("demand_expectation_check.csv", table)
    return table


def verify(dong, orders, daily, training, rates, demand, yearly, hourly, profile, candidates, cfg, output):
    p = cfg["demand"]
    output.check("daily_order_total", int(daily.orders.sum()), len(orders), daily.orders.sum() == len(orders))
    output.check("dong_count", len(rates), len(dong), len(rates) == len(dong) and set(rates.adm_cd2) == set(dong.adm_cd2))
    expected = len(dong)*len(p["scenarios"])*(p["forecast_end_year"]-p["forecast_start_year"]+2)
    output.check("forecast_rows", len(demand), expected, len(demand) == expected and not demand.duplicated(["scenario", "year", "adm_cd2"]).any())
    output.check("rate_interval_order", int((rates.rate_low.le(rates.rate_mid)&rates.rate_mid.le(rates.rate_high)&rates.rate_low.gt(0)).sum()), len(rates),
                 (rates.rate_low.le(rates.rate_mid)&rates.rate_mid.le(rates.rate_high)&rates.rate_low.gt(0)).all())
    output.check("forecast_interval_order", int((demand.orders_low.le(demand.orders_mid)&demand.orders_mid.le(demand.orders_high)).sum()), len(demand),
                 (demand.orders_low.le(demand.orders_mid)&demand.orders_mid.le(demand.orders_high)).all())
    sums = hourly.groupby(["day_type", "area_type"]).share.sum()
    output.check("hour_share_sum", float(abs(sums-1).max()), 0, np.allclose(sums, 1), "4개 그룹 각각 합계 1")
    output.check("hour_order_total", int(hourly.n_orders.sum()), len(orders), hourly.n_orders.sum() == len(orders))
    overall = profile[(profile.day_type == "ALL") & (profile.area_type == "ALL")].iloc[0]
    rebuilt = overall.avg_basket_krw*overall.n_orders
    output.check("basket_total_rebuilt", float(rebuilt), float(orders.basket_krw.sum()), np.isclose(rebuilt, orders.basket_krw.sum()))
    eligible = candidates.rmse_log.le(candidates.rmse_log.min()*p["rmse_accept_ratio"])
    output.check("ramp_accepted_tau", int(candidates.accepted.sum()), int(eligible.sum()), np.array_equal(candidates.accepted.astype(bool), eligible))
    ratio = yearly.orders_mid/yearly.orders_without_market
    output.check("market_multiplication", float(abs(ratio-yearly.market_index).max()), 0, np.allclose(ratio, yearly.market_index), "인구·주문율·경쟁을 통제하면 합계 비율=07[B] 시장지수")
    base = demand[demand.year.eq(p["base_year"]) & demand.scenario.eq("base")].merge(rates, on="adm_cd2").merge(dong, on="adm_cd2")
    # 동일한 competition 함수를 다시 쓰지 않고 원래 학습 동에서 저장한 계수로 독립 역산.
    matched = base.merge(training[["adm_cd2", "competition_factor"]], on="adm_cd2")
    reconstructed = matched.households_2026*matched.rate_mid*matched.competition_factor
    error = abs(reconstructed-matched.orders_mid).max()
    output.check("base_year_no_delivery_assortment_ramp", float(error), 0, np.allclose(reconstructed, matched.orders_mid),
                 "2026년 잠재 주문=세대수×최종 주문율×경쟁만; 배송시간·구색·램프업 수준 추가 곱 없음")
    # 파일 저장 후 재읽어 키·숫자 손실 검증.
    reloaded = pd.read_csv(output.path / "demand_dong_year.csv", encoding="utf-8-sig", dtype={"adm_cd2": str})
    group_key = ["scenario", "year"]
    order_cols = ["orders_low", "orders_mid", "orders_high"]
    check = reloaded.groupby(group_key)[order_cols].sum().sort_index()
    saved = yearly.set_index(group_key)[order_cols].sort_index()
    require(check.index.equals(saved.index), "CSV 재읽기 후 시나리오·연도 키 불일치")
    difference = (check - saved).abs().to_numpy()
    magnitude = saved.abs().to_numpy()
    relative_error = np.divide(difference, magnitude, out=np.full_like(difference, np.inf), where=magnitude > 0)
    relative_error[(difference == 0) & (magnitude == 0)] = 0
    max_relative_error = float(relative_error.max())
    tolerance = p["csv_total_rtol"]
    output.check("csv_year_totals", max_relative_error, tolerance, max_relative_error <= tolerance,
                 f"시나리오·연도 키로 정렬 후 하한·중앙·상한 비교, 최대 절대차 {difference.max():.3e}건/일")


def run(cfg):
    output = Output(cfg)
    status_path = output.path / "demand_run_status.json"
    status_path.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    raw_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "data").rglob('*') if p.is_file()}
    try:
        dong, orders, calendar, market, population, competitors, rules, types, od = load_inputs(cfg, output)
        daily = make_daily(orders, calendar, cfg)
        candidates, daily_fit = fit_daily(daily, cfg["demand"])
        output.csv("demand_ramp_candidates.csv", candidates)
        daily["calendar_factor"] = daily_fit["calendar_effect"]
        daily["predicted_orders"] = daily_fit["predicted"]
        training = rate_training(dong, orders, daily, candidates, cfg, competitors, rules, types, output)
        output.csv("demand_pilot_dong.csv", training)
        output.csv("demand_daily.csv", daily)
        rates, model, relative, validation = rate_outputs(dong, training, candidates, cfg, output)
        demand, yearly = forecast(dong, population, competitors, market, rules, model, relative, cfg, output)
        hourly, profile, threshold = profiles(dong, orders, cfg, output)
        effect = effects(daily_fit, candidates, model, relative, cfg, output)
        pilot = pilot_comparison(daily, orders, dong, od, cfg)
        output.csv("demand_pilot_comparison.csv", pilot)
        expectations = expectation_checks(training, candidates, yearly, effect, pilot, cfg, output)
        verify(dong, orders, daily, training, rates, demand, yearly, hourly, profile, candidates, cfg, output)
        from src.demand.figures import plot_all
        plot_all(cfg, dong, training, rates, candidates, validation, yearly, model, output)
        preserved = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in raw_hashes.items())
        output.check("raw_files_unchanged", len(raw_hashes), len(raw_hashes), preserved)
        output.csv("demand_verification.csv", pd.DataFrame(output.checks))
        from src.demand.report import write_report
        write_report(cfg, output, training, rates, model, threshold)
        output.flush_issues()
        status_path.write_text(json.dumps(dict(status="complete", config_sha256=hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
                                              rows=len(demand), verification_passed=len(output.checks)), indent=2), encoding="utf-8")
        print(pd.DataFrame(output.checks).to_string(index=False))
        print(candidates.to_string(index=False))
        print(expectations.to_string(index=False))
    except Exception as exc:
        output.issue("BLOCKER", "demand", 1, str(exc), "STOP")
        output.flush_issues()
        status_path.write_text(json.dumps(dict(status="blocked", error=str(exc)), ensure_ascii=False, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    run(load_config(parser.parse_args().config))
