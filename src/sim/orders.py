"""파일럿 표본으로 하루 주문을 생성한다."""

import numpy as np


ORDER_ATTRIBUTES = ["building_type", "items", "weight_kg", "basket_krw", "has_chilled", "has_frozen"]
LOCATION_ATTRIBUTES = ["cust_lat", "cust_lon", "gu", "adm_cd2", "road_km"]


def generate_orders(n, hourly, locations, attributes, radius_km, rng, *,
                    open_min, close_min, pick_base, pick_per_item, pick_items_cap):
    """시간·위치는 독립 추출하고 주문 속성은 파일럿 한 행을 함께 추출한다."""
    in_radius = locations.loc[locations.road_km.le(radius_km), LOCATION_ATTRIBUTES]
    if in_radius.empty or attributes.empty or n <= 0:
        raise ValueError("주문 생성에 양의 주문 수와 반경 내 위치·주문 속성 표본이 필요합니다.")
    hours = hourly.index.to_numpy(dtype=int)
    shares = hourly.to_numpy(dtype=float)
    starts = np.maximum(hours * 60, open_min)
    ends = np.minimum((hours + 1) * 60, close_min)
    if (not np.isfinite(shares).all() or (shares < 0).any() or not np.isclose(shares.sum(), 1)
            or (starts >= ends).any()):
        raise ValueError("영업시간 범위 또는 시간대 비중을 확인하세요.")

    hour_index = rng.choice(len(hours), size=n, p=shares / shares.sum())
    orders = in_radius.iloc[rng.integers(len(in_radius), size=n)].reset_index(drop=True)
    chosen = attributes[ORDER_ATTRIBUTES].iloc[rng.integers(len(attributes), size=n)]
    orders[ORDER_ATTRIBUTES] = chosen.reset_index(drop=True)
    orders["order_id"] = [f"O{i:05d}" for i in range(1, n + 1)]
    orders["order_min"] = rng.uniform(starts[hour_index], ends[hour_index])
    orders["pick_std_min"] = pick_base + pick_per_item * orders["items"].clip(upper=pick_items_cap)
    return orders
