"""로그 선형 모형과 램프업. 수요 계산에는 관측 배송시간만 사용한다."""
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import t as student_t


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ramp(days, a, tau):
    return 1 - a * np.exp(-np.asarray(days, dtype=float) / tau)


def fit_daily(daily, cfg):
    """공통 a·tau, 거점별 절편, 요일 6개/날씨/공휴일/행사 효과."""
    hubs = sorted(daily.hub_id.unique())
    columns = [(f"hub:{hub}", daily.hub_id.eq(hub).to_numpy(float)) for hub in hubs]
    columns += [(f"weekday:{day}", daily.weekday_num.eq(day).to_numpy(float)) for day in range(1, 7)]
    columns += [(f"weather:{weather}", daily.weather.eq(weather).to_numpy(float))
                for weather in sorted(daily.weather.unique()) if weather != "clear"]
    columns += [("holiday:present", daily.holiday.notna().to_numpy(float)),
                ("promotion:present", daily.promotion.notna().to_numpy(float))]
    names, vectors = zip(*columns)
    X = np.column_stack(vectors)
    require(np.linalg.matrix_rank(X) == X.shape[1], "일별 효과 설계행렬 비식별: 달력 범주를 확인하세요")
    require(daily.orders.gt(0).all(), "0건 영업일 발견: 로그에 임의 상수를 더하지 않고 중단")
    y = np.log(daily.orders.to_numpy(float))
    rows, fits = [], []
    for tau in cfg["tau_days"]:
        def solve(a):
            log_ramp = np.log(ramp(daily.elapsed, a, tau))
            beta = np.linalg.lstsq(X, y - log_ramp, rcond=None)[0]
            residual = y - X @ beta - log_ramp
            return np.mean(residual**2), beta, residual
        result = minimize_scalar(lambda a: solve(a)[0], bounds=cfg["a_bounds"], method="bounded",
                                 options={"xatol": 1e-12})
        require(result.success, f"tau={tau} 적합 실패")
        a = min([result.x, *cfg["a_bounds"]], key=lambda v: solve(v)[0])
        mse, beta, residual = solve(a)
        smear = np.exp(residual).mean()
        weekday = np.array([1., *[np.exp(beta[names.index(f"weekday:{i}")]) for i in range(1, 7)]])
        week_mean = weekday.mean()
        calendar_effect = np.exp(X[:, len(hubs):] @ beta[len(hubs):]) / week_mean
        mature = {hub: np.exp(beta[i]) * week_mean * smear for i, hub in enumerate(hubs)}
        last_levels = {hub: ramp(daily.loc[daily.hub_id.eq(hub)].tail(cfg["last_week_days"]).elapsed, a, tau).mean()
                       for hub in hubs}
        overall = sum(mature.values()) / sum(mature[h] * last_levels[h] for h in hubs)
        predicted = np.exp(X @ beta) * ramp(daily.elapsed, a, tau) * smear
        row = dict(tau=tau, a=a, rmse_log=np.sqrt(mse), rmse_orders=np.sqrt(np.mean((daily.orders-predicted)**2)),
                   level_day90=float(ramp(cfg["reference_day"], a, tau)),
                   mature_div_day90=float(1/ramp(cfg["reference_day"], a, tau)), mature_div_last_week=overall)
        for hub in hubs:
            row[f"{hub}_mature_div_last_week"] = 1 / last_levels[hub]
        rows.append(row)
        fits.append(dict(beta=beta, names=names, calendar_effect=calendar_effect, predicted=predicted,
                         weekday=weekday/week_mean, mature=mature, smear=smear))
    candidates = pd.DataFrame(rows)
    best = candidates.rmse_log.idxmin()
    candidates["accepted"] = (candidates.rmse_log <= candidates.loc[best, "rmse_log"] * cfg["rmse_accept_ratio"]).astype(int)
    candidates["selected"] = (candidates.index == best).astype(int)
    return candidates, fits[best]


def design(data, variables):
    return np.column_stack([np.ones(len(data)), *[data[v].to_numpy(float) for v in variables]])


def linear_fit(data, variables):
    X = design(data, variables)
    require(np.linalg.matrix_rank(X) == X.shape[1], "주문율 설계행렬 비식별")
    y = np.log(data.base_rate_observed.to_numpy(float))
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    residual = y-X @ beta
    df = len(y)-X.shape[1]
    require(df > 1, "주문율 예측구간의 자유도 부족")
    inv = np.linalg.inv(X.T @ X)
    leverage = np.einsum("ij,jk,ik->i", X, inv, X)
    require((leverage < 1-1e-8).all(), "LOOCV 불가능한 단독 특성 표본")
    return dict(variables=list(variables), beta=beta, residual=residual, df=df,
                sigma2=float(residual @ residual / df), inv=inv, smear=float(np.exp(residual).mean()),
                cv_rmse_log=float(np.sqrt(np.mean((residual/(1-leverage))**2))))


def select_rate_model(training, cfg):
    candidates = []
    for size in range(cfg["max_variables"]+1):
        for variables in combinations(cfg["candidate_variables"], size):
            X = design(training, variables)
            if np.linalg.matrix_rank(X) < X.shape[1]:
                continue
            fitted = linear_fit(training, variables)
            candidates.append((fitted["cv_rmse_log"], variables, fitted))
    candidates.sort(key=lambda x: (x[0], len(x[1]), x[1]))
    table = pd.DataFrame([dict(variables="|".join(v) or "intercept_only", n_variables=len(v), cv_rmse_log=e,
                               selected=int(i == 0)) for i, (e, v, _) in enumerate(candidates)])
    return candidates[0][2], table


def predict_rate(data, model, coverage):
    X = design(data, model["variables"])
    mid = np.exp(X @ model["beta"]) * model["smear"]
    leverage = np.maximum(np.einsum("ij,jk,ik->i", X, model["inv"], X), 0)
    halfwidth = student_t.ppf((1+coverage)/2, model["df"]) * np.sqrt(model["sigma2"]*(1+leverage))
    return mid*np.exp(-halfwidth), mid, mid*np.exp(halfwidth)


def competition(dong, competitors, year, rules, cfg):
    """WGS84 대표점의 대권 직선거리. 배송 이동시간 계산에는 사용하지 않는다."""
    active = competitors.open_year.le(year) & (competitors.close_year.isna() | competitors.close_year.gt(year))
    kind = competitors.competitor.eq("A")
    if cfg["w2"] and year >= cfg["w2_start_year"]:
        kind |= competitors.competitor.eq("B(what-if)")
    sites = competitors[active & kind]
    lat1, lon1 = np.radians(dong.lat.to_numpy())[:, None], np.radians(dong.lon.to_numpy())[:, None]
    lat2, lon2 = np.radians(sites.lat.to_numpy())[None, :], np.radians(sites.lon.to_numpy())[None, :]
    h = np.sin((lat2-lat1)/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin((lon2-lon1)/2)**2
    distance = 2*cfg["earth_radius_km"]*np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    counts = (distance <= cfg["competition_radius_km"]).sum(axis=1)
    values = rules[rules.rule.eq("competition_multiplier")].set_index("key").value
    factors = np.select([counts == 0, counts == 1], [float(values["0개"]), float(values["1개"])], float(values["2개 이상"]))
    return counts, factors
