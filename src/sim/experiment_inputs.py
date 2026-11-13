"""대표 후보지, 파일럿 경험분포와 대표일별 입력."""

import json
from dataclasses import dataclass
from itertools import product
from types import SimpleNamespace

import numpy as np
import pandas as pd
from matplotlib.path import Path as Polygon

from src.common.io import ROOT, load_file
from src.dispatch import DispatchInputs
from src.travel_time import EARTH_R_KM
from .orders import ORDER_ATTRIBUTES


@dataclass(frozen=True)
class Case:
    hub_type: str
    rider_class: str
    day_type: str
    radius_min: int
    orders_per_day: int
    order_level: float


def grid(config, reduced):
    settings = config["sim"]["experiment"]
    for (hub, cap), rider, day in product(config["network"]["capacity_per_day"].items(),
                                          settings["rider_classes"], settings["days"]):
        thin = reduced and (hub, rider) != ("M", "A")
        levels = settings["reduced_order_levels" if thin else "order_levels"]
        radii = settings["reduced_radius_min" if thin else "radius_min"]
        for radius, level in product(radii, levels):
            yield Case(hub, rider, day, radius, round(cap * level), level)


def representative_sites(sites, dongs):
    types = dongs[["gu", "rider_area_class"]].drop_duplicates()
    joined = sites.loc[sites.status.eq("AVAILABLE")].merge(types, on="gu", validate="many_to_one")
    result = {}
    for key, group in joined.groupby(["hub_type", "rider_area_class"]):
        group = group.assign(bay_gap=(group.bike_bays - group.bike_bays.median()).abs())
        result[key] = SimpleNamespace(**group.sort_values(["bay_gap", "site_id"]).iloc[0].to_dict())
    return result


def candidate_points(site, reach, count, rng):
    """거점 주위의 구면 좌표를 균일 면적에 가깝게 추출."""
    distance = reach * np.sqrt(rng.random(count)) / EARTH_R_KM
    heading = rng.uniform(0, 2 * np.pi, count)
    lat, lon = np.radians([site.lat, site.lon])
    y = np.arcsin(np.sin(lat) * np.cos(distance) + np.cos(lat) * np.sin(distance) * np.cos(heading))
    x = lon + np.arctan2(np.sin(heading) * np.sin(distance) * np.cos(lat),
                         np.cos(distance) - np.sin(lat) * np.sin(y))
    return np.degrees(np.column_stack((x, y)))


def locate_points(points, boundaries):
    assigned = np.full(len(points), None, dtype=object)
    for feature in boundaries["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = [polygons]
        for ring, *holes in polygons:
            xy = np.asarray(ring)[:, :2]
            low, high = xy.min(axis=0), xy.max(axis=0)
            possible = np.flatnonzero(((points >= low) & (points <= high)).all(axis=1))
            inside = Polygon(xy).contains_points(points[possible])
            for hole in holes:
                inside &= ~Polygon(np.asarray(hole)[:, :2]).contains_points(points[possible])
            assigned[possible[inside]] = feature["properties"]["adm_cd2"]
    return assigned


def make_locations(site, boundaries, dongs, travel, settings, radial, seed, *, road_radius_km=None):
    hour = settings["peak_hour"]
    # 가장 빠른 지역 조합의 최대 직선 도달거리를 후보 생성 범위로 사용한다.
    reach = road_radius_km if road_radius_km is not None else (
        max(settings["radius_min"]) * max(travel.speed(hour, site.gu, gu) for gu in travel.gu_factor) / 60
    )
    rng = np.random.default_rng(seed)
    points = candidate_points(site, reach, settings["location_candidates"], rng)
    codes = locate_points(points, boundaries)
    valid = pd.notna(codes)
    points, codes = points[valid], codes[valid]
    locations = pd.DataFrame(dict(cust_lon=points[:, 0], cust_lat=points[:, 1], adm_cd2=codes))
    locations["gu"] = locations.adm_cd2.map(dongs.gu)
    origin = (site.lat, site.lon, site.gu)
    values = [travel.road(*origin, r.cust_lat, r.cust_lon, r.gu)[3] for r in locations.itertuples()]
    locations["road_km"] = values
    speeds = locations.gu.map({gu: travel.speed(hour, site.gu, gu) for gu in travel.gu_factor})
    locations["peak_min"] = locations.road_km / speeds * 60
    # 같은 구 기준 도로거리 반경과 고객 구별 실제 피크 시간 기준을 모두 적용한다.
    base_speed = travel.speed(hour, site.gu, site.gu)
    bounds = [(None, road_radius_km)] if road_radius_km is not None else [
        (radius, radius * base_speed / 60) for radius in settings["radius_min"]
    ]
    pools = {}
    for radius, radius_km in bounds:
        pool = locations.loc[locations.road_km.le(radius_km)]
        if radius is not None:
            pool = pool.loc[pool.peak_min.le(radius)]
        if len(pool) < 20:
            raise ValueError(f"{site.site_id} 반경 {radius}분의 위치 후보 부족: {len(pool)}")
        # 도로거리의 10개 구간별로 실제 파일럿의 표본 빈도를 맞춘다.
        bins = np.linspace(0, 1, 11)
        weights = np.histogram(radial, bins=bins)[0]
        group = np.minimum((pool.road_km.to_numpy() / radius_km * 10).astype(int), 9)
        available = np.unique(group)
        nearest = available[np.abs(np.arange(10)[:, None] - available).argmin(axis=1)]
        redistributed = np.bincount(nearest, weights=weights, minlength=10)
        density = redistributed[group] / np.bincount(group, minlength=10)[group]
        sampled = rng.choice(len(pool), size=len(pool), replace=True, p=density / density.sum())
        pools[radius] = (radius_km, pool.iloc[sampled].reset_index(drop=True))
    return pools


def pilot_distributions(config, inputs, dongs):
    orders = load_file("05_pilot_orders.csv", config=config)
    orders = orders.loc[orders.demand_included.eq(1)].copy()
    labels = dongs.single_hh_ratio
    orders["day_type"] = np.where(pd.to_datetime(orders.order_ts).dt.day_of_week.ge(5), "weekend", "weekday")
    orders["area_type"] = np.where(orders.adm_cd2.map(labels).ge(labels.median()), "single_high", "single_low")
    attrs = orders.loc[orders.weight_analysis_eligible.eq(1)]
    if not orders.loc[~orders.index.isin(attrs.index), "weight_unresolved"].eq(1).all():
        raise ValueError("승인된 중량 미해결 규칙 밖의 주문 표본 제외")
    if attrs[ORDER_ATTRIBUTES].isna().any().any() or attrs[["items", "weight_kg", "basket_krw"]].lt(0).any().any():
        raise ValueError("파일럿 주문 속성 결측 또는 음수")
    groups = {key: group[ORDER_ATTRIBUTES].reset_index(drop=True)
              for key, group in attrs.groupby(["day_type", "area_type"])}
    radial, residual = [], []
    for hub, radius in config["pilot_diagnosis"]["service_radius_km"].items():
        site = inputs.sites.set_index("site_id").loc[hub]
        origin = (site.lat, site.lon, site.gu)
        sample = orders.loc[orders.hub_id.eq(hub)]
        coords = ["cust_lat", "cust_lon", "gu"]
        points = sample[coords].drop_duplicates().copy()
        points["road_km"] = [inputs.travel.road(*origin, *x)[3] for x in points.itertuples(index=False, name=None)]
        road = sample[coords].merge(points, on=coords, validate="many_to_one").road_km.to_numpy()
        radial.extend(road[road <= radius] / radius)
        quick = sample.loc[sample.rider_id.eq(config["cleaning"]["external_rider_id"])
                           & sample.delivery_kpi_eligible.eq(1)]
        for row in quick.itertuples():
            dep = pd.Timestamp(row.depart_ts)
            elapsed = (pd.Timestamp(row.delivered_ts) - pd.Timestamp(row.pick_end_ts)).total_seconds() / 60
            travel = inputs.travel.minutes(*origin, row.cust_lat, row.cust_lon, row.gu, dep.hour)
            handover = inputs.cost["handover_apt_min" if row.building_type == "아파트" else "handover_other_min"]
            residual.append(elapsed - travel - handover)
    # 기상별 실제 속도로 잔여시간을 구하기 위해 맑은 날의 기준을 사용한다.
    # 음수인 표본이 나오면 묵시적으로 잘라내지 않고 입력을 확인한다.
    if not residual or not np.isfinite(residual).all() or min(residual) < 0:
        raise ValueError("외부 퀵 피킹 후 잔여시간 표본 확인 필요")
    return groups, np.asarray(radial), np.asarray(residual)


def load_experiment(config):
    settings = config["sim"]["experiment"]
    output = ROOT / config["paths"]["outputs"]
    inputs = DispatchInputs(config)
    dongs = load_file("01_dong_master.csv", config=config).set_index("adm_cd2")
    hubs = load_file("04_hub_types.csv", config=config).set_index("hub_type")
    attrs, radial, quick = pilot_distributions(config, inputs, dongs)
    sites = representative_sites(inputs.sites, dongs)
    profiles = pd.read_csv(output / "hour_profile.csv", encoding="utf-8-sig")
    profiles = {key: group.set_index("hour").share.sort_index()
                for key, group in profiles.groupby(["day_type", "area_type"])}
    orders = pd.read_csv(output / "order_profile.csv", encoding="utf-8-sig").set_index(["day_type", "area_type"])
    effects = pd.read_csv(output / "demand_effects.csv", encoding="utf-8-sig").set_index(["effect", "level"])
    threshold = dongs.single_hh_ratio.median()
    plans = {}
    selection = json.loads((output / "dispatch/selection.json").read_text(encoding="utf-8-sig"))
    for day, spec in settings["days"].items():
        instance = spec["benchmark"]
        policy = selection[instance]
        if policy != "P2":
            raise ValueError(f"{instance}: P2 정책 선택 기록 확인 필요")
        path = output / "dispatch" / instance / policy / "plan.json"
        policy_plan = json.loads(path.read_text(encoding="utf-8-sig"))
        staff = pd.read_csv(path.with_name("staffing.csv"), encoding="utf-8-sig").set_index("hour")
        plans[day] = dict(
            **{key: policy_plan[key] for key in ("policy", "wait", "wait_by_hour", "target", "margin")},
            staffing=staff[["pickers", "contract_riders", "employed_riders"]].to_dict("index"),
            blocks=policy_plan["employed_blocks"],
            n_orders=int(inputs.meta.set_index("instance").at[instance, "n_orders"]),
        )
    return inputs, dongs, hubs, attrs, radial, quick, sites, profiles, orders, effects, threshold, plans
