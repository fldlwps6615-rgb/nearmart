"""FIFO 피킹과 온라인 배차의 이산사건 시뮬레이션."""
from dataclasses import dataclass
import heapq
import math
import time
import numpy as np
import pandas as pd
from .inputs import Geometry, T3_COLUMNS

EPS = 1e-8


class SearchDeadline(Exception):
    pass


def hourly_roster(counts, role, prefix):
    """같은 번호의 연속된 시간대를 하나의 근무구간으로 합친다."""
    result = []
    hours = sorted(int(h) for h in counts)
    for number in range(1, max(counts.values(), default=0) + 1):
        start = None
        for h in range(min(hours), max(hours) + 2):
            active = counts.get(str(h), counts.get(h, 0)) >= number
            if active and start is None:
                start = h * 60.
            if not active and start is not None:
                result.append(dict(worker_id=f'{prefix}{number:02d}', role=role,
                                   start_min=start, end_min=h * 60.))
                start = None
    return result


def build_roster(plan):
    result = hourly_roster(plan['pickers'], 'picker', 'K')
    result += hourly_roster(plan['contract'], 'contract', 'C')
    index = 0
    for block in plan['employed_blocks']:
        for _ in range(block['count']):
            index += 1
            result.append(dict(worker_id=f'E{index:03d}', role='employed',
                               start_min=60. * block['start_hour'],
                               end_min=60. * (block['start_hour'] + block['hours'])))
    return result


def max_overlap(intervals):
    events = {}
    for a, b in intervals:
        if b > a + EPS:
            events[a] = events.get(a, 0) + 1
            events[b] = events.get(b, 0) - 1
    count, highest = 0, 0
    for t in sorted(events):
        count += events[t]
        highest = max(highest, count)
    return highest


@dataclass
class SimulationResult:
    t3: pd.DataFrame
    trips: pd.DataFrame
    work: pd.DataFrame
    picking: pd.DataFrame
    bays: pd.DataFrame
    external: pd.DataFrame
    staffing: pd.DataFrame
    kpi: dict
    plan: dict


class DispatchEngine:
    def __init__(self, inputs, orders, meta):
        self.inputs, self.meta = inputs, meta
        self.orders = orders.sort_values(['order_min', 'order_id']).reset_index(drop=True)
        self.cost, self.settings = inputs.cost, inputs.settings
        required = ['order_id', 'order_min', 'weight_kg', 'pick_std_min', 'gu', 'cust_lat', 'cust_lon', 'building_type']
        if self.orders[required].isna().any().any() or self.orders.order_id.duplicated().any():
            raise ValueError('주문 키/필수값 결함: 값을 추정하거나 제거하지 않습니다.')
        if (self.orders[['weight_kg','pick_std_min']] < 0).any().any():
            raise ValueError('음수 중량/피킹시간')
        self.geometry = Geometry(self.orders, meta, inputs.travel, self.cost)
        self.order_ids = self.orders.order_id.to_numpy()
        self.arrival = self.orders.order_min.to_numpy(float)
        self.weight = self.orders.weight_kg.to_numpy(float)
        self.deadline = self.arrival + self.settings['sla_min']
        self.pick_cache = {}

    def picking_schedule(self, roster, plan):
        key = tuple(sorted(plan['pickers'].items()))
        if key in self.pick_cache:
            return self.pick_cache[key].copy()
        shifts = [dict(r, available=r['start_min']) for r in roster if r['role'] == 'picker']
        rows = []
        previous_start = -math.inf
        for i, row in enumerate(self.orders.itertuples()):
            release = max(row.order_min, previous_start)
            candidates = [(max(s['available'], release), j) for j, s in enumerate(shifts)
                          if max(s['available'], release) + row.pick_std_min <= s['end_min'] + EPS]
            if not candidates:
                raise ValueError('계획 종료까지 모든 주문을 피킹할 수 없음')
            start, j = min(candidates)
            previous_start = start
            end = start + row.pick_std_min
            shifts[j]['available'] = end
            rows.append(dict(order_index=i, order_id=row.order_id, picker_id=shifts[j]['worker_id'],
                             pick_start_min=start, pick_end_min=end))
        frame = pd.DataFrame(rows)
        self.pick_cache[key] = frame
        return frame.copy()

    def simulate(self, plan, *, deadline_clock=None, external_delivery=None,
                 contract_shifts=None, travel_multiplier=None, allow_incomplete=False):
        """계획 고정 후 미래 주문 좌표를 배차 결정에 노출하지 않는다.

        external_delivery(order_record, pick_end_min)는 외부 퀵 전달 완료 시각을
        반환해야 한다. 35kg 초과 자료에 외부 운송모형이 없으면 명시적으로 거부한다.
        contract_shifts는 실제 로그인/로그아웃의 분 단위 근무구간이다.
        travel_multiplier는 출발 후보 트립의 구간별로 호출한다. 확률 이동을
        반영한 복귀시간이 근무 종료 이내인 라이더에게 배정한다.
        allow_incomplete는 인력 탐색 진단용으로 미배송 주문을 남긴 결과를 반환한다.
        """
        roster = build_roster(plan)
        if contract_shifts is not None:
            if any(plan['contract'].values()):
                raise ValueError('시간제 위탁 계획과 실제 위탁 근무기록을 함께 지정할 수 없음')
            roster.extend(dict(shift, role='contract') for shift in contract_shifts)
        picks = self.picking_schedule(roster, plan)
        riders = [dict(r, available=r['start_min'], count=0, shift=r) for r in roster if r['role'] != 'picker']
        pick_start = picks.pick_start_min.to_numpy()
        pick_end = picks.pick_end_min.to_numpy()
        events, pending, traces, trip_rows, quick = [], set(), [], [], []
        for i, end in enumerate(pick_end):
            if self.weight[i] > self.cost['trip_max_kg']:
                if external_delivery is None:
                    raise ValueError('35kg 초과 주문의 외부 퀵 전달시각 모형이 필요합니다.')
                delivered = float(external_delivery(self.orders.iloc[i].to_dict(), end))
                if delivered < end:
                    raise ValueError('외부 퀵 전달시각이 피킹 완료보다 이름')
                quick.append(dict(order_id=self.order_ids[i], delivered_min=delivered,
                                  pick_start_min=pick_start[i], pick_end_min=end,
                                  cost_krw=self.cost['external_quick_krw']))
            else:
                heapq.heappush(events, (float(end), 1, i))
        for j, r in enumerate(riders):
            heapq.heappush(events, (r['start_min'], 0, j))
            heapq.heappush(events, (r['end_min'], 0, j))
        event_count = 0
        rider_trips = {}
        while events:
            now = events[0][0]
            if deadline_clock and time.perf_counter() >= deadline_clock:
                raise SearchDeadline()
            while events and events[0][0] <= now + EPS:
                _, kind, index = heapq.heappop(events)
                if kind == 1:
                    pending.add(index)
            event_count += 1
            while pending:
                available = [r for r in riders if r['start_min'] <= now + EPS and
                             r['available'] <= now + EPS and r['end_min'] > now + EPS]
                if not available:
                    break
                available.sort(key=lambda r: (r['role'] == 'employed', r['count'], r['worker_id']))
                seed = min(pending, key=lambda j: (self.deadline[j], j))
                batch = [seed]
                limit = plan['target'] if plan['policy'] != 'P0' else self.settings['p0_max_orders']
                limit = min(limit, int(self.cost['trip_max_orders']))
                # P0의 직선거리 제한은 배차 묶음 후보 판정에만 사용한다.
                if now >= self.deadline[seed]:
                    candidates = ()
                elif plan['policy'] == 'P0':
                    candidates = sorted(pending - {seed}, key=lambda j: (self.arrival[j], j))
                else:
                    candidates = sorted(pending - {seed}, key=lambda j: (self.geometry.road(seed, j), self.deadline[j], j))
                    candidates = candidates[:self.settings['candidate_neighbors']]
                batch_weight = self.weight[seed]
                for candidate in candidates:
                    if len(batch) >= limit:
                        break
                    if batch_weight + self.weight[candidate] > self.cost['trip_max_kg'] + EPS:
                        continue
                    if plan['policy'] == 'P0' and any(self.geometry.straight(j, candidate) > self.settings['p0_radius_km'] for j in batch):
                        continue
                    trial = self.geometry.route(batch + [candidate])
                    delivery, returned, _ = self.geometry.itinerary(trial, now)
                    margin = 0 if plan['policy'] == 'P0' else plan['margin']
                    if all(t <= self.deadline[j] - margin + EPS for j, t in zip(trial, delivery)) and any(returned <= r['end_min'] + EPS for r in available):
                        batch = trial
                        batch_weight += self.weight[candidate]
                route = self.geometry.route(batch)
                delivery, returned, distance = self.geometry.itinerary(route, now)
                if plan['policy'] != 'P0' and len(route) < limit:
                    wait = plan['wait_by_hour'].get(str(int(now // 60)), plan['wait'])
                    wake = min(pick_end[seed] + wait,
                               now + min(self.deadline[j] - plan['margin'] - t for j, t in zip(route, delivery)))
                    if wake > now + EPS:
                        heapq.heappush(events, (wake, 0, -1))
                        break
                eligible = [r for r in available if returned <= r['end_min'] + EPS]
                if not eligible:
                    route = [seed]
                    delivery, returned, distance = self.geometry.itinerary(route, now)
                    eligible = [r for r in available if returned <= r['end_min'] + EPS]
                if not eligible:
                    break
                if travel_multiplier is not None:
                    delivery, returned, distance = self.geometry.itinerary(route, now, travel_multiplier)
                    eligible = [r for r in available if returned <= r['end_min'] + EPS]
                    if not eligible:
                        break
                rider = eligible[0]
                rider['available'] = returned
                rider['count'] += len(route)
                trip_id = f"{plan['policy']}-{len(trip_rows)+1:05d}"
                depart = now + self.cost['load_time_min']
                pay = 0.
                if rider['role'] == 'contract':
                    pay = self.cost['contract_base_krw'] + self.cost['contract_extra_order_krw'] * (len(route)-1)
                    pay += self.cost['contract_km_rate_krw'] * max(0, distance-self.cost['contract_km_free'])
                    if 18 <= (depart // 60) % 24 < 21:
                        pay += self.cost['contract_peak_surcharge_krw'] * len(route)
                    weather = self.meta['weather']
                    if weather == 'rain':
                        pay += self.cost['contract_weather_surcharge_rain_krw'] * len(route)
                    elif weather in ['heavy_rain', 'snow']:
                        pay += self.cost['contract_weather_surcharge_heavy_krw'] * len(route)
                trip = dict(trip_id=trip_id, rider_id=rider['worker_id'], rider_type=rider['role'],
                                      load_start_min=now, depart_min=depart, return_min=returned,
                                      route_km=distance, orders=len(route), weight_kg=float(self.weight[route].sum()), pay_krw=pay)
                trip_rows.append(trip)
                rider_trips.setdefault(rider['worker_id'], []).append(trip)
                for seq, (j, delivered) in enumerate(zip(route, delivery), 1):
                    traces.append(dict(instance=self.meta['instance'], order_id=self.order_ids[j],
                                       rider_id=rider['worker_id'], rider_type=rider['role'], trip_id=trip_id,
                                       trip_seq=seq, pick_start_min=pick_start[j], pick_end_min=pick_end[j],
                                       depart_min=depart, delivered_min=delivered))
                    pending.remove(j)
                heapq.heappush(events, (returned, 0, -1))
        if pending and not allow_incomplete:
            raise ValueError(f'계획 종료 후 미배송 {len(pending)}건')
        # 영업 종료 뒤에는 잔여 피킹/복귀를 완료한 다음 정시에 시간제 접속을 종료한다.
        # 직고용 4/8시간 블록은 줄이지 않고 모두 유급으로 남긴다.
        if self.settings.get('shutdown_after_completion', False):
            picker_stop = max(24*60., math.ceil(max(pick_end)/60)*60.)
            rider_stop = max(24*60., math.ceil(max((t['return_min'] for t in trip_rows), default=0)/60)*60.)
            roster = [dict(r, end_min=min(r['end_min'], picker_stop if r['role']=='picker' else rider_stop))
                      if r['role']=='picker' or (r['role']=='contract' and contract_shifts is None)
                      else r for r in roster]
            roster = [r for r in roster if r['start_min'] < r['end_min']]
            if contract_shifts is None:
                riders = [dict(r, end_min=min(r['end_min'], rider_stop)) if r['role']=='contract' else r for r in riders]
            riders = [r for r in riders if r['start_min'] < r['end_min']]
        t3 = pd.DataFrame(traces, columns=T3_COLUMNS).sort_values('order_id').reset_index(drop=True)
        trips = pd.DataFrame(trip_rows, columns=['trip_id','rider_id','rider_type','load_start_min',
                                               'depart_min','return_min','route_km','orders','weight_kg','pay_krw'])
        work = pd.DataFrame(roster)
        bays = []
        for r in riders:
            own = [t for t in rider_trips.get(r['worker_id'], []) if
                   t['load_start_min'] >= r['start_min'] - EPS and t['return_min'] <= r['end_min'] + EPS]
            cursor = r['start_min']
            for trip in sorted(own, key=lambda t: t['load_start_min']):
                if trip['depart_min'] > cursor + EPS:
                    bays.append(dict(rider_id=r['worker_id'], start_min=cursor, end_min=trip['depart_min']))
                cursor = trip['return_min']
            if r['end_min'] > cursor + EPS:
                bays.append(dict(rider_id=r['worker_id'], start_min=cursor, end_min=r['end_min']))
        bayframe = pd.DataFrame(bays, columns=['rider_id','start_min','end_min'])
        staffing = []
        for h in sorted(int(x) for x in plan['pickers']):
            staffing.append(dict(instance=self.meta['instance'], policy=plan['policy'], hour=h,
                                 pickers=sum(r['role']=='picker' and r['start_min']<=h*60<r['end_min'] for r in roster),
                                 contract_riders=sum(r['role']=='contract' and r['start_min']<=h*60<r['end_min'] for r in roster),
                                 employed_riders=sum(r['start_min'] <= h*60 < r['end_min'] for r in riders if r['role']=='employed')))
        staffing = pd.DataFrame(staffing)
        kpi = self.metrics(t3, trips, work, bayframe, quick, staffing, plan,
                           exact_contract_caps=contract_shifts is not None)
        kpi['events'] = event_count
        if allow_incomplete:
            kpi['undelivered_orders'] = len(pending)
        return SimulationResult(t3, trips, work, picks, bayframe, pd.DataFrame(quick), staffing, kpi, plan)

    def metrics(self, t3, trips, work, bays, quick, staffing, plan, *, exact_contract_caps=False):
        durations = t3.merge(self.orders[['order_id','order_min']], on='order_id')
        durations = (durations.delivered_min - durations.order_min).to_numpy()
        late = int((durations > self.settings['sla_min'] + EPS).sum())
        hours = (work.end_min-work.start_min)/60
        contract_h = float(hours[work.role=='contract'].sum())
        employed_h = float(hours[work.role=='employed'].sum())
        picker_h = float(hours[work.role=='picker'].sum())
        pay = float(trips.pay_krw.sum()) if len(trips) else 0.
        employed_orders = int((t3.rider_type=='employed').sum())
        employed_cost = employed_h*(self.cost['employed_wage_krw_per_h_2027']*self.cost['labor_load_factor']+
                                    self.cost['employed_vehicle_krw_per_month']/self.settings['fte_hours_per_month'])
        employed_cost += employed_orders*self.cost['employed_incentive_krw']
        picker_cost = picker_h*self.cost['picker_wage_krw_per_h_2027']*self.cost['labor_load_factor']
        quick_cost = len(quick)*self.cost['external_quick_krw']
        arrival_map=self.orders.set_index('order_id').order_min.to_dict()
        external_late=sum(r['delivered_min']-arrival_map[r['order_id']]>self.settings['sla_min']+EPS for r in quick)
        coupon = (late+external_late)*self.cost['late_coupon_krw']
        n = len(t3)+len(quick)
        counts = t3.groupby('rider_id').size().reindex(work.loc[work.role!='picker','worker_id'].unique(), fill_value=0)
        income = pay/contract_h if contract_h else float('nan')
        supply_ok = bool(income >= self.settings['min_contract_hourly_krw']) if contract_h else True
        bay_max = max_overlap(zip(bays.start_min,bays.end_min))
        if exact_contract_caps:
            contract = work[work.role == 'contract']
            intervals = list(zip(contract.start_min, contract.end_min))
            cap_violations = sum(
                max_overlap((max(a, 60*h), min(b, 60*(h+1))) for a, b in intervals) > self.inputs.cap(self.meta, h)
                for h in range(self.settings['open_hour'], math.ceil(contract.end_min.max()/60))
            ) if intervals else 0
        else:
            cap_violations = sum(row.contract_riders > self.inputs.cap(self.meta,row.hour) for row in staffing.itertuples())
        picker_violations = sum(row.pickers > self.settings['max_pickers'] or (9<=row.hour<24 and row.pickers<1) for row in staffing.itertuples())
        block_violations = sum(b['hours'] not in [4,8] for b in plan['employed_blocks'])
        rule_violations = int(bay_max > self.meta['bike_bays']) + cap_violations + picker_violations + block_violations
        total = pay+employed_cost+picker_cost+coupon+quick_cost
        return dict(instance=self.meta['instance'], policy=plan['policy'], orders=n, late_orders=late, external_late_orders=int(external_late),
                    on_time=float((durations<=self.settings['sla_min']+EPS).mean()) if len(durations) else float('nan'),
                    avg_min=float(np.mean(durations)) if len(durations) else float('nan'),
                    p90_min=float(np.quantile(durations,.9)) if len(durations) else float('nan'),
                    max_min=float(max(durations)) if len(durations) else float('nan'),
                    orders_per_trip=len(t3)/len(trips) if len(trips) else float('nan'), cost_per_order=total/n,
                    rider_hourly=income, workload_cv=float(counts.std(ddof=0)/counts.mean()) if counts.mean()>0 else float('nan'),
                    supply_ok=supply_ok, earnings_shortfall_krw=max(0., self.settings['min_contract_hourly_krw']-income) if contract_h else 0.,
                    rule_violations=rule_violations, bay_max=bay_max, contract_cap_violations=cap_violations,
                    contract_pay_krw=pay, employed_cost_krw=employed_cost, picker_cost_krw=picker_cost,
                    late_coupon_krw=coupon, external_quick_krw=quick_cost, total_cost_krw=total,
                    total_cost_eok=total/1e8, contract_hours=contract_h, employed_hours=employed_h,
                    picker_hours=picker_h, contract_rider_cost_per_order_krw=pay/n,
                    employed_rider_cost_per_order_krw=employed_cost/n, picker_cost_per_order_krw=picker_cost/n,
                    feasible=bool(rule_violations==0 and late==0 and supply_ok))
