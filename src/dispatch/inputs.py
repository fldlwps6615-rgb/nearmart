"""정제 입력 및 이동시간 어댑터. 이동시간 공식은 travel_time.py만 사용."""
from functools import lru_cache
import math
import pandas as pd
from src.common.io import ROOT, load_config, load_file
from src.travel_time import TravelTime, haversine_km

T3_COLUMNS = ['instance', 'order_id', 'rider_id', 'rider_type', 'trip_id',
              'trip_seq', 'pick_start_min', 'pick_end_min', 'depart_min', 'delivered_min']


class CachedTravel(TravelTime):
    # 원 모듈의 road/minutes 결과만 캐시. 시간대별 출발속도는 매 구간 적용한다.
    @lru_cache(maxsize=300000)
    def road(self, *args):
        return super().road(*args)

    @lru_cache(maxsize=40000)
    def speed(self, depart_hour, from_gu, to_gu, weather="clear"):
        return super().speed(depart_hour, from_gu, to_gu, weather)


class DispatchInputs:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.settings = self.config['dispatch']
        self.orders = load_file('16_vrp_benchmark_orders.csv', config=self.config)
        self.meta = load_file('16b_vrp_benchmark_meta.csv', config=self.config)
        self.sites = load_file('03_candidate_sites.csv', config=self.config)
        costs = load_file('15_cost_parameters.csv', config=self.config)
        self.cost = {r.parameter: float(r.value) for r in costs.itertuples()
                     if r.category in ['labor', 'rider', 'variable']}
        supply = load_file('14_rider_supply.csv', config=self.config)
        self.supply = next(t for t in supply.values() if 'area_class' in t)
        self.weather_caps = next(t for t in supply.values() if 'cap_multiplier' in t).set_index('weather').cap_multiplier.to_dict()
        self.supply_by_hour = {
            (row.area_class, hour): row.max_contract_riders_per_hub
            for row in self.supply.itertuples()
            for hour in range(row.hour_from, row.hour_to)
        }
        self.travel = CachedTravel(ROOT / self.config['paths']['clean'])

    def cap(self, meta, hour):
        h = min(hour, 23)  # 자정 이후 21~24시 상한 연장
        key = (meta['rider_area_class'], h)
        if key not in self.supply_by_hour:
            raise ValueError(f'위탁 상한 없음: {meta}, {hour}')
        return math.floor(self.supply_by_hour[key] * self.weather_caps[meta['weather']])

    def instance(self, name):
        meta = self.meta.set_index('instance').loc[name].to_dict()
        meta['instance'] = name
        meta['bike_bays'] = int(self.sites.set_index('site_id').loc[meta['hub_site_id'], 'bike_bays'])
        orders = self.orders[self.orders.instance == name].sort_values(['order_min', 'order_id']).reset_index(drop=True)
        return orders, meta


class Geometry:
    def __init__(self, orders, meta, travel, cost):
        self.orders, self.meta, self.travel, self.cost = orders, meta, travel, cost
        self.points = [(r.cust_lat, r.cust_lon, r.gu) for r in orders.itertuples()]
        self.points.append((meta['hub_lat'], meta['hub_lon'], meta['hub_gu']))
        self.hub = len(orders)
        self.handover = [cost['handover_apt_min'] if r.building_type == '아파트'
                         else cost['handover_other_min'] for r in orders.itertuples()]

    @lru_cache(maxsize=200000)
    def road(self, a, b):
        return self.travel.road(*self.points[a], *self.points[b])[3]

    @lru_cache(maxsize=200000)
    def straight(self, a, b):
        x, y = self.points[a], self.points[b]
        return float(haversine_km(x[0], x[1], y[0], y[1]))

    @lru_cache(maxsize=400000)
    def minute_at_hour(self, a, b, hour):
        return self.travel.minutes(*self.points[a], *self.points[b], hour, self.meta['weather'])

    def minutes(self, a, b, start):
        return self.minute_at_hour(a, b, int(start // 60) % 24)

    def itinerary(self, route, load_start, multiplier=None):
        t = load_start + self.cost['load_time_min']
        distance, previous, delivered = 0., self.hub, []
        for order in route:
            distance += self.road(previous, order)
            leg = self.minutes(previous, order, t)
            t += (leg if multiplier is None else leg * multiplier()) + self.handover[order]
            delivered.append(t)
            previous = order
        leg = self.minutes(previous, self.hub, t)
        returned = t + (leg if multiplier is None else leg * multiplier())
        return delivered, returned, distance

    def route(self, members):
        remaining = set(members)
        path, prev = [], self.hub
        while remaining:
            nxt = min(remaining, key=lambda j: (self.road(prev, j), j))
            path.append(nxt)
            remaining.remove(nxt)
            prev = nxt
        # 복귀를 포함한 도로거리 경로를 2-opt로 개선하고, 전달시각은 별도로 확인한다.
        def length(p):
            full = [self.hub, *p, self.hub]
            return sum(self.road(a, b) for a, b in zip(full, full[1:]))
        best = length(path)
        improved = True
        while improved:
            improved = False
            for a in range(len(path) - 1):
                for b in range(a + 1, len(path)):
                    trial = path[:a] + path[a:b+1][::-1] + path[b+1:]
                    score = length(trial)
                    if score < best - 1e-10:
                        path, best, improved = trial, score, True
        return path
