"""연도×동 배정·수요·운영비 계산과 재사용 가능한 재무 평가."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd

from src.common.io import ROOT, active_od, load_file
from src.finance.engine import FinanceEngine, OPS_COLUMNS


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_output(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"adm_cd2": str, "adm_cd": str, "site_id": str})


class NetworkModel:
    def __init__(self, cfg):
        self.cfg = deepcopy(cfg)
        self.p = cfg["network"]
        self.out = ROOT / cfg["paths"]["outputs"]
        status = json.loads((self.out / "demand_run_status.json").read_text(encoding="utf-8-sig"))
        require(status["status"] == "complete", "수요예측 완료 상태가 필요합니다")
        self.sites = load_file("03_candidate_sites.csv", config=cfg).sort_values("site_id").reset_index(drop=True)
        self.dong = load_file("01_dong_master.csv", config=cfg).sort_values("adm_cd2").reset_index(drop=True)
        self.types = load_file("04_hub_types.csv", config=cfg).set_index("hub_type")
        self.population = load_file("08_population_projection.csv", config=cfg)
        self.rider_supply = load_file("14_rider_supply.csv", config=cfg)
        self.ids = self.sites.site_id.to_numpy()
        self.hub_types = self.sites.hub_type.to_numpy()
        self.available_years = self.sites.available_from_year.to_numpy(int)
        self.index = {sid: i for i, sid in enumerate(self.ids)}
        self.pilots = tuple(i for i, sid in enumerate(self.ids) if self.sites.iloc[i].status == "PILOT")
        require(self.sites.site_id.is_unique and self.dong.adm_cd2.is_unique, "후보지 또는 동 키 중복")
        require(self.sites.area_unit.eq("㎡").all(), "정제 후보지 면적 단위 오류")
        area = self.sites.area_m2.to_numpy()
        inferred = np.select([area < 330, area < 660], ["S", "M"], "L")
        require(np.array_equal(inferred, self.sites.hub_type) and ((area >= 150) & (area <= 1000)).all(), "면적·유형 불일치")
        od = active_od(cfg)
        require(not od.duplicated(["site_id", "adm_cd2"]).any(), "OD 중복")
        self.peak = od.pivot(index="site_id", columns="adm_cd2", values="time_peak_min").reindex(index=self.ids, columns=self.dong.adm_cd2).to_numpy(float)
        self.offpeak = od.pivot(index="site_id", columns="adm_cd2", values="time_offpeak_min").reindex(index=self.ids, columns=self.dong.adm_cd2).to_numpy(float)
        require(np.isfinite(self.peak).all() and np.isfinite(self.offpeak).all(), "활성 후보지 OD 결측")
        self.delivery = self.p["delivery_base_min"] + self.p["offpeak_weight"]*self.offpeak + self.p["peak_weight"]*self.peak
        self.delivery_factor = np.asarray(self.p["delivery_multipliers"])[np.searchsorted(self.p["delivery_bin_max"], self.delivery, side="left")]
        self.rider_cost = np.asarray(self.p["rider_cost_2027"])[np.searchsorted(self.p["rider_bin_max"], self.peak, side="left")]
        self.capacity = self.sites.hub_type.map(self.p["capacity_per_day"]).to_numpy(float)
        self.assortment = self.sites.hub_type.map(self.types.assortment_demand_factor).to_numpy(float)
        profile = read_output(self.out / "order_profile.csv").query("day_type == 'ALL' and area_type == 'ALL'").iloc[0]
        self.profile = SimpleNamespace(**profile.to_dict())
        initial_engine = FinanceEngine(cfg)
        self.years = np.arange(initial_engine.start, initial_engine.end+1)
        self.end = initial_engine.end+1
        self.limit = initial_engine.par("funding_limit_krw")
        self.days_per_year = initial_engine.days
        self.price_growth = initial_engine.par("price_inflation")
        self.basket_2027 = self.profile.avg_basket_krw * (1+self.price_growth)
        self.fee_2027 = initial_engine.par("delivery_fee_krw") * self.profile.under_40000_share
        self.inventory = self.estimate_inventory(initial_engine)
        self.cfg["finance"]["pilot_initial_inventory_krw"] = dict(zip(self.inventory.site_id, self.inventory.initial_inventory_krw))
        self.engine = FinanceEngine(self.cfg)
        require(self.cfg["finance"]["demand_basis"] == "scenario_applied", "시장지수 중복 반영 방지: scenario_applied 필요")
        require(not self.cfg["finance"]["allow_early_open_for_stress_test"], "네트워크에는 조기 입주 예외를 적용할 수 없습니다")
        require(self.cfg["finance"]["close_year_convention"] == "first_inactive_year", "폐점연도는 첫 미영업 연도 필요")
        demand = read_output(self.out / "demand_dong_year.csv")
        require(not demand.duplicated(["scenario", "year", "adm_cd2"]).any(), "수요 키 중복")
        self.demands = {}
        for scenario in self.p["scenario_order"]:
            for band in self.p["demand_bands"]:
                matrix = demand[demand.scenario.eq(scenario)].pivot(index="year", columns="adm_cd2", values=f"orders_{band}").reindex(index=self.years, columns=self.dong.adm_cd2).to_numpy(float)
                require(np.isfinite(matrix).all() and (matrix >= 0).all(), "연도별 동 수요 결측/음수")
                self.demands[scenario, band] = matrix
        self.households = []
        for year in self.years:
            pop = self.population[self.population.year.eq(year)].set_index("gu").households_index
            h = self.dong.households_2026 * self.dong.gu.map(pop)
            require(h.notna().all(), "세대수 추계 누락")
            self.households.append(h.to_numpy())
        self.households = np.asarray(self.households)
        effects = read_output(self.out / "demand_effects.csv").set_index(["effect", "level"]).multiplier
        self.a, self.tau = effects.loc[("ramp", "a")], effects.loc[("ramp", "tau_days")]
        self.ramp_cache = {}
        self.assignment_cache = {}
        self.score_cache = {}
        self.evaluations = 0

    def estimate_inventory(self, engine):
        daily = read_output(self.out / "demand_daily.csv")
        daily["date"] = pd.to_datetime(daily.date)
        rows = []
        for i in self.pilots:
            sid = self.ids[i]
            last = daily[daily.hub_id.eq(sid)].sort_values("date").tail(self.p["pilot_inventory_days_observed"])
            require(len(last) == self.p["pilot_inventory_days_observed"], "파일럿 재고 추정용 마지막 주 부족")
            orders = last.orders.mean()
            value = orders*self.profile.avg_basket_krw*(1-engine.par("gross_margin_rate"))*engine.par("inventory_days_of_cogs")
            rows.append(dict(site_id=sid, start_date=last.date.min().date(), end_date=last.date.max().date(),
                             observed_days=len(last), observed_orders=last.orders.sum(), orders_per_day=orders,
                             basket_2026_krw=self.profile.avg_basket_krw, cogs_ratio=1-engine.par("gross_margin_rate"),
                             inventory_days=engine.par("inventory_days_of_cogs"), initial_inventory_krw=value,
                             initial_inventory_eok=value/engine.unit,
                             immediate_recovery_krw=value*engine.par("inventory_recovery_at_close")))
        return pd.DataFrame(rows)

    def ramp(self, i, opening):
        key = i, opening
        if key not in self.ramp_cache:
            if i in self.pilots:
                opened = date.fromisoformat(self.cfg["pilot_diagnosis"]["opening_dates"][self.ids[i]])
            else:
                opened = date(opening, 1, 1)
            levels = []
            for year in self.years:
                start = (date(int(year), 1, 1)-opened).days
                count = (date(int(year)+1, 1, 1)-date(int(year), 1, 1)).days
                levels.append(float(np.mean(1-self.a*np.exp(-np.arange(start, start+count)/self.tau))) if year >= opening else 0.)
            self.ramp_cache[key] = np.asarray(levels)
        return self.ramp_cache[key]

    def plan_frame(self, plan, explicit_pilots=False):
        records = [dict(site_id=self.ids[i], hub_type=self.hub_types[i], open_year=o,
                        close_year=c if c < self.end else "", relocated_from="", note="파일럿 계속 운영" if i in self.pilots else "휴리스틱 선택")
                   for i, o, c in plan]
        if explicit_pilots:
            included = {i for i, _, _ in plan}
            records += [dict(site_id=self.ids[i], hub_type=self.hub_types[i], open_year=int(self.years[0]),
                             close_year=int(self.years[0]), relocated_from="", note="파일럿 2027년 초 철수")
                        for i in self.pilots if i not in included]
        return pd.DataFrame(records, columns=["site_id", "hub_type", "open_year", "close_year", "relocated_from", "note"])

    def assignments(self, active, T):
        key = tuple(active), T
        if key not in self.assignment_cache:
            if len(active):
                nearest = np.argmin(self.peak[active], axis=0)
                selected = np.asarray(active)[nearest]
                valid = self.peak[selected, np.arange(len(self.dong))] <= T
                selected = np.where(valid, selected, -1)
            else:
                selected = np.full(len(self.dong), -1, dtype=int)
            self.assignment_cache[key] = selected
        return self.assignment_cache[key]

    def rider_units(self, weighted_cost):
        return weighted_cost*(1-self.profile.external_quick_share), 0.

    def operating(self, plan, T, scenario, band, details=False):
        n_year, n_site, n_dong = len(self.years), len(self.ids), len(self.dong)
        assigned = np.full((n_year, n_dong), -1, dtype=int)
        requested = np.zeros((n_year, n_dong))
        served = np.zeros_like(requested)
        hub_demand = np.zeros((n_year, n_site))
        hub_served = np.zeros_like(hub_demand)
        hub_cost = np.zeros_like(hub_demand)
        hub_ramp = np.zeros_like(hub_demand)
        for i, o, c in plan:
            hub_ramp[:, i] = self.ramp(i, o)
        demand = self.demands[scenario, band]
        for y, year in enumerate(self.years):
            active = [i for i, o, c in plan if o <= year < c]
            selected = self.assignments(active, T)
            assigned[y] = selected
            valid = selected >= 0
            sites, dongs = selected[valid], np.flatnonzero(valid)
            q = demand[y, valid]*self.delivery_factor[sites, dongs]*self.assortment[sites]*hub_ramp[y, sites]
            q *= 1-self.p["late_demand_penalty"]*self.p["late_rate"]
            requested[y, valid] = q
            total = np.bincount(sites, weights=q, minlength=n_site)
            hub_demand[y] = total
            ratio = np.minimum(1, np.divide(self.capacity, total, out=np.ones(n_site), where=total > 0))
            received = q*ratio[sites]
            served[y, valid] = received
            hub_served[y] = np.bincount(sites, weights=received, minlength=n_site)
            cost = np.bincount(sites, weights=received*self.rider_cost[sites, dongs], minlength=n_site)
            hub_cost[y] = np.divide(cost, hub_served[y], out=np.zeros(n_site), where=hub_served[y] > 0)
        ops_rows = []
        for i, o, c in plan:
            for y in np.flatnonzero((self.years >= o) & (self.years < c)):
                contract, employed = self.rider_units(hub_cost[y, i])
                ops_rows.append([self.years[y], self.ids[i], hub_served[y, i], self.basket_2027,
                                 self.profile.under_40000_share, self.profile.cold_order_share,
                                 contract, employed, self.p["picker_cost_2027"],
                                 self.p["late_rate"], self.profile.external_quick_share])
        ops = pd.DataFrame(ops_rows, columns=OPS_COLUMNS)
        if not details:
            return ops
        return ops, dict(assigned=assigned, requested=requested, served=served, hub_demand=hub_demand,
                         hub_served=hub_served, hub_rider_cost=hub_cost, hub_ramp=hub_ramp)

    def evaluate(self, plan, T, scenario=None, band=None, details=False):
        scenario = scenario or self.p["optimization_scenario"]
        band = band or self.p["optimization_band"]
        key = plan, T, scenario, band
        if not details and key in self.score_cache:
            return self.score_cache[key]
        operating = self.operating(plan, T, scenario, band, details)
        ops = operating[0] if details else operating
        annual, kpi = self.engine.evaluate(self.plan_frame(plan), ops, scenario)
        self.evaluations += 1
        self.score_cache[key] = kpi
        if details:
            return kpi, annual, ops, operating[1]
        return kpi

    def summaries(self, plan, state):
        rows = []
        for y, year in enumerate(self.years):
            valid = state["assigned"][y] >= 0
            lost = float((state["requested"][y]-state["served"][y]).sum())
            inflation = (1+self.price_growth)**(year-self.years[0])
            rows.append(dict(year=year, active_hubs=sum(o <= year < c for _, o, c in plan),
                             households_served=self.households[y, valid].sum(), households_total=self.households[y].sum(),
                             coverage=self.households[y, valid].sum()/self.households[y].sum(),
                             requested_per_day=state["requested"][y].sum(), orders_per_day=state["served"][y].sum(),
                             lost_orders_per_day=lost, lost_orders_annual=lost*self.days_per_year,
                             lost_product_revenue_krw=lost*self.days_per_year*self.basket_2027*inflation,
                             lost_delivery_revenue_krw=lost*self.days_per_year*self.fee_2027*inflation,
                             lost_revenue_krw=lost*self.days_per_year*(self.basket_2027+self.fee_2027)*inflation))
        return pd.DataFrame(rows)

    def assignment_frame(self, state, year):
        y = int(year-self.years[0])
        selected = state["assigned"][y]
        valid = selected >= 0
        ids = np.full(len(self.dong), "", dtype=object)
        ids[valid] = self.ids[selected[valid]]
        delivery = np.full(len(self.dong), np.nan)
        delivery[valid] = self.delivery[selected[valid], np.flatnonzero(valid)]
        return pd.DataFrame(dict(year=year, adm_cd2=self.dong.adm_cd2, site_id=ids,
                                 expected_orders_per_day=state["served"][y], avg_delivery_min=delivery,
                                 late_rate=np.where(valid, self.p["late_rate"], np.nan)))
