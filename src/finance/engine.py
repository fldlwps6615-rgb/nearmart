"""부록 B 재무 엔진. 금액은 원, 비율은 0~1, CSV는 공용 정제 로더 사용.

evaluate(plan, ops, scenario) -> (annual: DataFrame, kpi: dict)
annual에는 초기 투자 현금흐름을 위한 2026년 말 행도 포함한다.
정상 평가에서는 파일을 쓰지 않는다. 잘못된 입력은 이슈 로그 후 거부한다.
"""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config, load_file

PLAN_COLUMNS = ["site_id", "hub_type", "open_year", "close_year", "relocated_from"]
OPS_COLUMNS = [
    "year", "site_id", "orders_per_day", "avg_basket_krw",
    "under_40000_share", "cold_order_share", "contract_rider_cost_per_order_krw",
    "employed_rider_cost_per_order_krw", "picker_cost_per_order_krw",
    "late_rate", "external_quick_share",
]
OP_INDEX = {name: i for i, name in enumerate(OPS_COLUMNS[2:])}
FLOW_COLUMNS = [
    "orders", "product_revenue", "delivery_revenue", "cogs", "payment_fee",
    "packaging", "cold_packaging", "shrink", "ongoing_marketing", "it_cost",
    "late_coupon", "external_quick", "contract_rider_cost", "employed_rider_cost",
    "picker_cost", "rent", "maintenance", "fixed_staff", "utilities", "replenish",
    "launch_marketing", "restoration", "capex_initial", "capex_refresh",
    "depreciation", "deposit_paid", "deposit_returned", "inventory_investment",
    "inventory_recovery", "inventory_balance", "asset_book_value",
    "unamortized_book_value_at_close", "expected_relocation_cost", "active_hubs",
]


class FinanceEngine:
    """설정·정제 데이터를 초기화 시 한 번 읽는다. 입력 DataFrame은 변경하지 않는다."""

    def __init__(self, config=None):
        self.config = deepcopy(config) if isinstance(config, dict) else load_config(config)
        self.f = self.config["finance"]
        self.issue_path = ROOT / self.f["issues_log_path"]
        status = ROOT / self.config["paths"]["outputs"] / "clean_run_status.json"
        if not status.exists() or json.loads(status.read_text(encoding="utf-8-sig")).get("status") != "complete":
            self._fail("CLEAN_STATUS", "정제 실행 완료 상태가 필요합니다.", "clean_run_status.json")
        costs = load_file("15_cost_parameters.csv", config=self.config)
        if costs.parameter.duplicated().any():
            self._fail("PARAM_DUPLICATE", "15번 parameter 중복", "15_cost_parameters.csv")
        self.p = dict(zip(costs.parameter, costs.value))
        self.start, self.end = map(int, str(self.p["horizon"]).split("~"))
        self.years = np.arange(self.start - 1, self.end + 1)
        self.t = np.arange(len(self.years))
        self.days = self.f["days_per_year"]
        self.months = self.f["months_per_year"]
        self.unit = self.f["krw_per_eok"]
        self.sites = load_file("03_candidate_sites.csv", config=self.config).set_index("site_id", verify_integrity=True)
        self.types = load_file("04_hub_types.csv", config=self.config).set_index("hub_type", verify_integrity=True)
        sections = load_file("07_market_scenarios.csv", config=self.config)
        self.market = next(v for v in sections.values() if "index_base" in v).set_index("year", verify_integrity=True)
        self.pilots = set(self.sites.index[self.sites.status.eq("PILOT")])
        # ③이 추정한 2026년 말 재고를 넘기면 철수와 계속 운영에 같은 잔액을 사용한다.
        # 생략한 기존 호출은 종전의 기초재고 계산을 유지한다.
        self.pilot_inventory = self.f.get("pilot_initial_inventory_krw", {})
        if self.pilot_inventory:
            if set(self.pilot_inventory) != self.pilots:
                self._fail("PILOT_INVENTORY", "기초 재고는 모든 파일럿의 잔액을 명시해야 합니다.")
            if any(not np.isfinite(float(v)) or float(v) < 0 for v in self.pilot_inventory.values()):
                self._fail("PILOT_INVENTORY", "파일럿 기초 재고는 유한한 비음수여야 합니다.")
        self._validate_sources()
        self.site_records = {k: SimpleNamespace(**v) for k, v in self.sites.to_dict("index").items()}
        self.type_records = {k: SimpleNamespace(**v) for k, v in self.types.to_dict("index").items()}

    def _fail(self, rule, message, file="finance_inputs", n=1, example=""):
        self.issue_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.issue_path.exists() and self.issue_path.stat().st_size > 0
        with self.issue_path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            if not existed:
                writer.writerow(["rule_id", "file", "check", "n_rows", "example", "action"])
            writer.writerow(["FIN_" + rule, file, message, n, str(example), "STOP | 입력 수정 필요; 보간·삭제 안 함"])
        raise ValueError(message)

    def par(self, name):
        try:
            value = float(self.p[name])
        except (KeyError, TypeError, ValueError):
            self._fail("PARAM", f"15번 파라미터 누락/비수치: {name}", "15_cost_parameters.csv")
        if not np.isfinite(value):
            self._fail("PARAM", f"15번 유한값 필요: {name}", "15_cost_parameters.csv")
        return value

    def rate(self, name):
        value = self.f[name]
        return self.par(value) if isinstance(value, str) else float(value)

    def _validate_sources(self):
        for table, cols, name in [
            (self.sites, ["area_m2", "monthly_rent_krw", "deposit_krw", "maintenance_krw_per_month", "available_from_year", "nonrenewal_prob_at_10y"], "03"),
            (self.types, ["capex_krw", "fixed_staff_cost_krw_per_month", "utilities_krw_per_month", "replenish_cost_krw_per_day", "launch_marketing_krw"], "04"),
            (self.market, ["index_low", "index_base", "index_high"], "07"),
        ]:
            a = table[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)
            bad = (~np.isfinite(a) | (a < 0)).any(axis=1)
            if bad.any():
                self._fail("SOURCE", f"{name} 필수 재무값 결측/음수", name, int(bad.sum()), list(table.index[bad]))
        if not set(range(self.start - 1, self.end + 1)) <= set(self.market.index):
            self._fail("MARKET_YEARS", "07 수요지수 연도 누락", "07")
        if not self.sites.truck_dock.isin(["Y", "N"]).all() or not self.sites.hub_type.isin(self.types.index).all():
            self._fail("SITE_ENUM", "03 접안/유형 값 오류", "03")
        if not self.sites.nonrenewal_prob_at_10y.between(0, 1).all():
            self._fail("NONRENEWAL_PROB", "03 거절 확률 범위 오류", "03")
        choices = {
            "nonrenewal_mode": {"expected", "forced"},
            "refresh_timing": {"year_end", "year_start"},
            "pilot_refresh_basis": {"sunk_zero", "type_cost"},
            "inventory_initial_timing": {"operating_year_end", "opening"},
            "demand_basis": {"scenario_applied", "index_2026"},
            "close_year_convention": {"first_inactive_year", "last_operating_year"},
        }
        for key, allowed in choices.items():
            if self.f[key] not in allowed:
                self._fail("CONFIG", f"finance.{key}는 {sorted(allowed)} 중 하나여야 합니다.", "config.json")
        if self.rate("discount_rate") <= -1:
            self._fail("DISCOUNT", "할인율은 -1보다 커야 합니다.", "config.json")

    def _frame(self, value, columns, label):
        frame = value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
        if frame.empty and not len(frame.columns):
            return pd.DataFrame(columns=columns)
        missing = set(columns) - set(frame.columns)
        if missing:
            self._fail("COLUMNS", f"{label} 필수 컬럼 누락: {sorted(missing)}", label)
        if not frame.site_id.map(lambda v: isinstance(v, str) and bool(v.strip())).all():
            self._fail("SITE_ID", f"{label}.site_id는 비어 있지 않은 문자열이어야 합니다.", label)
        return frame

    def _plan(self, plan):
        frame = self._frame(plan, PLAN_COLUMNS, "plan")
        if frame.site_id.duplicated().any():
            self._fail("PLAN_DUPLICATE", "같은 후보지의 중복 개설은 지원하지 않습니다.", "plan")
        rows = []
        for raw in frame.to_dict("records"):
            site = raw["site_id"]
            if site not in self.sites.index:
                self._fail("UNKNOWN_SITE", f"정제 후보지에 없음: {site}", "plan")
            row = dict(raw)
            for key in ("open_year", "close_year"):
                v = row[key]
                if key == "close_year" and (pd.isna(v) or v == ""):
                    row[key] = self.end + 1
                    continue
                try:
                    number = float(v)
                    if not np.isfinite(number) or number != int(number):
                        raise ValueError
                    row[key] = int(number)
                except (TypeError, ValueError):
                    self._fail("YEAR", f"{site}.{key}: 정수 연도 필요", "plan")
                if key == "close_year" and self.f["close_year_convention"] == "last_operating_year":
                    row[key] += 1
            if pd.isna(row["relocated_from"]) or row["relocated_from"] == "":
                row["relocated_from"] = ""
            elif not isinstance(row["relocated_from"], str):
                self._fail("RELOCATION_ID", "relocated_from은 문자열이어야 합니다.", "plan")
            rows.append(row)
        # R06: forced는 지정된 원거점을 계약 만료 시 닫고 지정 후보지를 실제로 연다.
        if self.f["nonrenewal_mode"] == "forced":
            mappings = self.f["forced_relocations"]
            if not mappings:
                self._fail("FORCED_TARGET", "forced 모드에는 forced_relocations 지정이 필요합니다.", "config.json")
            lookup = {r["site_id"]: r for r in rows}
            seen = set()
            for spec in mappings:
                old, new = spec["from_site_id"], spec["to_site_id"]
                if old in seen or old not in lookup or new not in self.sites.index or new == old:
                    self._fail("FORCED_MAPPING", f"강제 이전 매핑 오류: {old} -> {new}", "config.json")
                seen.add(old)
                source = lookup[old]
                when = source["open_year"] + int(self.par("renewal_right_years"))
                if when > self.end or source["close_year"] < when:
                    self._fail("FORCED_YEAR", f"{old}: 평가기간 내 재계약 사건이 아님", "plan")
                source["close_year"] = when
                if new in lookup:
                    target = lookup[new]
                    if target["open_year"] != when or target["relocated_from"] != old:
                        self._fail("FORCED_PLAN", f"{new} 개점연도/relocated_from이 강제 이전과 충돌", "plan")
                else:
                    target = dict(site_id=new, hub_type=self.sites.loc[new, "hub_type"], open_year=when,
                                  close_year=self.end + 1, relocated_from=old)
                    rows.append(target)
                    lookup[new] = target
        lookup = {r["site_id"]: r for r in rows}
        for row in rows:
            site, opening, closing = row["site_id"], row["open_year"], row["close_year"]
            if row["hub_type"] != self.sites.loc[site, "hub_type"]:
                self._fail("HUB_TYPE", f"{site}: 면적으로 정해진 유형과 불일치", "plan")
            if not self.start <= opening <= self.end or not opening <= closing <= self.end + 1:
                self._fail("PLAN_YEARS", f"{site}: 개폐점 연도 범위/순서 오류", "plan")
            if opening == closing and not (site in self.pilots and opening == self.start):
                self._fail("ZERO_LIFETIME", f"{site}: 영업기간이 없음", "plan")
            if site in self.pilots and opening != self.start:
                self._fail("PILOT_OPEN", f"{site}: 파일럿 개점 간주 연도는 {self.start}", "plan")
            if opening < self.sites.loc[site, "available_from_year"] and not self.f["allow_early_open_for_stress_test"]:
                self._fail("AVAILABILITY", f"{site}: 입주 가능 연도 이전 개점", "plan")
            old = row["relocated_from"]
            if old:
                if old not in lookup or lookup[old]["close_year"] != opening or lookup[old]["open_year"] >= opening:
                    self._fail("RELOCATION", f"{site}: 이전 원거점/폐점연도 연결 오류", "plan")
        return rows

    def _ops(self, ops, plan, scenario):
        frame = self._frame(ops, OPS_COLUMNS, "ops")
        if "scenario" in frame:
            if frame.scenario.isna().any() or not frame.scenario.isin(self.f["scenarios"]).all():
                self._fail("OPS_SCENARIO", "ops.scenario 누락/알 수 없는 값", "ops")
            frame = frame.loc[frame.scenario.eq(scenario)].copy()
        numeric = [c for c in OPS_COLUMNS if c != "site_id"]
        a = frame[numeric].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        bad = (~np.isfinite(a) | (a < 0)).any(axis=1)
        if bad.any():
            self._fail("OPS_VALUE", "ops 필수 숫자 결측/비유한값/음수", "ops", int(bad.sum()))
        frame[numeric] = a
        ratios = [c for c in numeric if c.endswith("share") or c == "late_rate"]
        if (frame[ratios] > 1).any().any():
            self._fail("OPS_RATIO", "ops 비율은 0~1이어야 합니다.", "ops")
        if (frame.year != np.floor(frame.year)).any() or frame.duplicated(["site_id", "year"]).any():
            self._fail("OPS_KEY", "ops 연도는 정수, 거점×연도는 유일해야 합니다.", "ops")
        ordered = [(r["site_id"], y) for r in plan for y in range(r["open_year"], r["close_year"])]
        expected = set(ordered)
        actual = set(zip(frame.site_id, frame.year.astype(int)))
        if expected != actual:
            missing, extra = expected - actual, actual - expected
            self._fail("OPS_COVERAGE", "ops는 실제 영업 거점×연도와 정확히 일치해야 합니다.", "ops",
                       len(missing) + len(extra), f"누락={sorted(missing)[:5]}, 초과={sorted(extra)[:5]}")
        # 거점별 pandas 조회 대신 한 번 정렬한 연속 배열을 사용한다.
        return frame.set_index(["site_id", "year"]).reindex(ordered)[OPS_COLUMNS[2:]].to_numpy(float)

    def evaluate(self, plan, ops, scenario=None):
        """R01: 초기시점 포함 연도별 손익·현금흐름 및 KPI 반환. 반복 호출 가능."""
        scenario = scenario or self.f["default_scenario"]
        if scenario not in self.f["scenarios"]:
            self._fail("SCENARIO", f"지원하지 않는 시나리오: {scenario}", "config.json")
        rows = self._plan(plan)
        op = self._ops(ops, rows, scenario)
        out = {name: np.zeros(len(self.years)) for name in FLOW_COLUMNS}
        def add(name, ix, value):
            out[name][ix] += value

        # R13: 계획에 없는 파일럿 및 2027년 초 철수 파일럿의 t=0 정산.
        continued = {r["site_id"] for r in rows if r["close_year"] > self.start}
        for site in self.pilots - continued:
            data = self.sites.loc[site]
            add("deposit_returned", 0, data.deposit_krw)
            add("restoration", 0, data.area_m2 * self.par("restoration_krw_per_m2"))
            add("inventory_recovery", 0, float(self.pilot_inventory.get(site, 0)) * self.par("inventory_recovery_at_close"))
        cursor = 0
        for row in rows:
            sid, opening, closing = row["site_id"], row["open_year"], row["close_year"]
            if opening == closing:
                continue
            site, kind = self.site_records[sid], self.type_records[row["hub_type"]]
            pilot = sid in self.pilots
            years = np.arange(opening, closing)
            ix = years - (self.start - 1)
            age, elapsed = years - opening, years - self.start
            vals = op[cursor:cursor + len(years)]
            cursor += len(years)
            q = vals[:, OP_INDEX["orders_per_day"]] * self.days
            if self.f["demand_basis"] == "index_2026":
                q *= self.market.loc[years, "index_" + scenario].to_numpy(float)
            inflation = (1 + self.par("price_inflation")) ** elapsed
            # R02: 입력 장바구니는 2027년 가격, 주문 수는 기본적으로 시나리오 반영 완료.
            product = q * vals[:, OP_INDEX["avg_basket_krw"]] * inflation
            delivery = q * vals[:, OP_INDEX["under_40000_share"]] * self.par("delivery_fee_krw") * inflation
            cogs = product * (1 - self.par("gross_margin_rate"))
            add("active_hubs", ix, 1)
            add("orders", ix, q)
            add("product_revenue", ix, product)
            add("delivery_revenue", ix, delivery)
            add("cogs", ix, cogs)
            # R03: 외부 퀵 비용은 별도. rider/picker 단가는 전체 주문수로 나눈 비용.
            add("payment_fee", ix, (product + delivery) * self.par("payment_fee_rate"))
            add("packaging", ix, q * self.par("packaging_krw") * inflation)
            add("cold_packaging", ix, q * vals[:, OP_INDEX["cold_order_share"]] * self.par("packaging_cold_krw") * inflation)
            add("shrink", ix, product * self.par("shrink_rate"))
            add("ongoing_marketing", ix, product * self.par("marketing_rate"))
            add("it_cost", ix, q * self.par("it_cost_per_order_krw") * (1 + self.rate("it_growth")) ** elapsed)
            add("late_coupon", ix, q * vals[:, OP_INDEX["late_rate"]] * self.par("late_coupon_krw") * (1 + self.rate("coupon_growth")) ** elapsed)
            add("external_quick", ix, q * vals[:, OP_INDEX["external_quick_share"]] * self.par("external_quick_krw") * (1 + self.rate("quick_growth")) ** elapsed)
            # R14: 위탁과 직고용을 분리하고 엔진에서 각 상승률을 한 번만 적용.
            for label, column, growth in [
                ("contract_rider_cost", "contract_rider_cost_per_order_krw", "contract_fee_growth"),
                ("employed_rider_cost", "employed_rider_cost_per_order_krw", "wage_growth"),
                ("picker_cost", "picker_cost_per_order_krw", "wage_growth"),
            ]:
                add(label, ix, q * vals[:, OP_INDEX[column]] * (1 + self.par(growth)) ** elapsed)
            # R04: 04의 고정 인력 비용은 거점 전체 월액. FTE를 다시 곱하지 않음.
            add("maintenance", ix, site.maintenance_krw_per_month * self.months * (1 + self.rate("maintenance_growth")) ** elapsed)
            add("fixed_staff", ix, kind.fixed_staff_cost_krw_per_month * self.months * (1 + self.par("wage_growth")) ** elapsed)
            add("utilities", ix, kind.utilities_krw_per_month * self.months * (1 + self.par("utilities_growth")) ** elapsed)
            dock_factor = 1 + (site.truck_dock == "N") * self.par("replenish_no_dock_surcharge")
            add("replenish", ix, kind.replenish_cost_krw_per_day * self.days * dock_factor * (1 + self.par("replenish_cost_growth")) ** elapsed)
            # R05: 5년 갱신, 10년 시장임대료 재설정, 15년 갱신.
            term, rights = int(self.par("lease_term_years")), int(self.par("renewal_right_years"))
            rent0 = site.monthly_rent_krw * (1 + self.par("market_rent_growth")) ** (opening - self.start)
            rent = np.full(len(years), rent0)
            rent[age >= term] = rent0 * (1 + self.par("renewal_rent_increase_cap"))
            renewal_rent = site.monthly_rent_krw * (1 + self.par("market_rent_growth")) ** (opening + rights - self.start)
            rent[age >= rights] = renewal_rent
            rent[age >= rights + term] = renewal_rent * (1 + self.rate("renewal_15y_increase"))
            add("rent", ix, rent * self.months)
            if self.f["nonrenewal_mode"] == "expected" and opening + rights < closing:
                when = opening + rights - (self.start - 1)
                replacement = kind.capex_krw * (1 + self.par("capex_price_growth")) ** (opening + rights - self.start)
                risk = site.nonrenewal_prob_at_10y * (site.area_m2 * self.par("restoration_krw_per_m2") + replacement + kind.launch_marketing_krw)
                add("expected_relocation_cost", when, risk)
            # R07/R08: 최초 투자 t-1 말, 개점 마케팅은 t 말 비용. 파일럿은 매몰 0.
            initial = 0.0 if pilot else kind.capex_krw * (1 + self.par("capex_price_growth")) ** (opening - self.start)
            opening_ix = opening - (self.start - 1)
            add("capex_initial", opening_ix - 1, initial)
            if not pilot:
                add("launch_marketing", opening_ix, kind.launch_marketing_krw)
            life = int(self.par("depreciation_years"))
            dep = np.where(age < life, initial / life, 0.0)
            refresh_basis = kind.capex_krw if pilot and self.f["pilot_refresh_basis"] == "type_cost" else initial
            refresh_year = opening + life - 1
            refresh = 0.0
            refresh_start = refresh_year + (self.f["refresh_timing"] == "year_end")
            if refresh_year < closing and (not self.f["skip_refresh_when_closing"] or refresh_start < closing):
                refresh = refresh_basis * self.par("capex_refresh_ratio_10y")
                cash_year = refresh_year - (self.f["refresh_timing"] == "year_start")
                add("capex_refresh", cash_year - (self.start - 1), refresh)
                dep += np.where((years >= refresh_start) & (years < refresh_start + life), refresh / life, 0)
            add("depreciation", ix, dep)
            asset_balance = initial + np.where(years >= refresh_year, refresh, 0) - np.cumsum(dep)
            residual = max(0.0, float(asset_balance[-1]))
            add("unamortized_book_value_at_close", ix[-1], residual)
            asset_balance[-1] = 0
            add("asset_book_value", ix, np.maximum(0, asset_balance))
            # R09: 기준 재고와 증감, 파일럿 기초재고는 2027년 원가 9일분(매몰).
            inventory = cogs / self.days * self.par("inventory_days_of_cogs")
            before = float(self.pilot_inventory.get(sid, inventory[0])) if pilot else 0.0
            if pilot:
                add("inventory_balance", 0, before)
            changes = np.diff(np.r_[before, inventory])
            if not pilot and self.f["inventory_initial_timing"] == "opening":
                add("inventory_investment", opening_ix - 1, changes[0])
                changes[0] = 0
            add("inventory_investment", ix, changes)
            add("inventory_recovery", ix[-1], inventory[-1] * self.par("inventory_recovery_at_close"))
            inventory[-1] = 0
            add("inventory_balance", ix, inventory)
            # R10/R12: 폐점 직전 말 또는 2046년 말 보증금 반환·원상복구.
            deposit = site.deposit_krw * (1 + self.par("market_rent_growth")) ** (opening - self.start)
            if not pilot:
                add("deposit_paid", opening_ix - 1, deposit)
            add("deposit_returned", ix[-1], deposit)
            add("restoration", ix[-1], site.area_m2 * self.par("restoration_krw_per_m2"))
        # 본사비는 운영 거점 수와 무관하며 t=0에는 없음.
        out["hq_cost"] = np.r_[0, self.par("hq_fixed_krw_per_year_2027") * (1 + self.par("hq_fixed_growth")) ** np.arange(self.end - self.start + 1)]
        out["revenue"] = out["product_revenue"] + out["delivery_revenue"]
        expense_names = ["cogs", "payment_fee", "packaging", "cold_packaging", "shrink", "ongoing_marketing", "it_cost", "late_coupon", "external_quick", "contract_rider_cost", "employed_rider_cost", "picker_cost", "rent", "maintenance", "fixed_staff", "utilities", "replenish", "launch_marketing", "restoration", "hq_cost"]
        out["ebitda"] = out["revenue"] - sum(out[c] for c in expense_names)
        out["ebit"] = out["ebitda"] - out["depreciation"]
        # R11: 기본은 부록 B의 EBITDA-정기상각만. 미상각 장부가 추가 공제는 명시 옵션.
        out["tax_book_value_deduction"] = out["unamortized_book_value_at_close"] * bool(self.f["deduct_unamortized_book_value_on_close"])
        out["pretax_for_tax"] = out["ebit"] - out["tax_book_value_deduction"]
        out["taxable_profit"] = np.zeros(len(self.years))
        out["tax_loss_used"] = np.zeros(len(self.years))
        out["tax_loss_carryforward"] = np.zeros(len(self.years))
        carry = 0.0
        for i, profit in enumerate(out["pretax_for_tax"]):
            used = min(carry, max(0.0, profit))
            out["tax_loss_used"][i] = used
            out["taxable_profit"][i] = max(0.0, profit - used)
            carry += max(0.0, -profit) - used
            out["tax_loss_carryforward"][i] = carry
        out["tax"] = out["taxable_profit"] * self.par("tax_rate")
        out["net_income"] = out["pretax_for_tax"] - out["tax"]
        out["capex"] = out["capex_initial"] + out["capex_refresh"]
        out["operating_cf"] = out["ebitda"] - out["tax"] - out["inventory_investment"] + out["inventory_recovery"]
        out["cash_flow"] = out["operating_cf"] - out["capex"] - out["deposit_paid"] + out["deposit_returned"] - out["expected_relocation_cost"]
        out["cum_cash_flow"] = np.cumsum(out["cash_flow"])
        out["discounted_cf"] = out["cash_flow"] / (1 + self.rate("discount_rate")) ** self.t
        out["cum_discounted_cf"] = np.cumsum(out["discounted_cf"])
        annual = pd.DataFrame({"year": self.years, **out})
        for name in ("revenue", "ebitda", "capex", "cash_flow", "cum_cash_flow", "discounted_cf"):
            annual[name + "_eok"] = annual[name] / self.unit
        # KPI: 회수기간은 지시서의 '최초 0 이상'을 t=0 포함해 그대로 적용.
        recovered = self.years[out["cum_discounted_cf"] >= 0]
        profitable = self.years[(self.years >= self.start) & (out["ebitda"] > 0)]
        funding = max(0.0, -float(out["cum_cash_flow"].min()))
        npv = float(out["discounted_cf"].sum())
        kpi = {
            "scenario": scenario, "npv": npv, "npv_eok": npv / self.unit,
            "max_funding": funding, "max_funding_eok": funding / self.unit,
            "funding_limit": self.par("funding_limit_krw"), "funding_within_limit": funding <= self.par("funding_limit_krw"),
            "payback_year": int(recovered[0]) if len(recovered) else "미회수",
            "ebitda_positive_year": int(profitable[0]) if len(profitable) else "미전환",
            "discount_rate": self.rate("discount_rate"),
        }
        return annual, kpi


_CACHE = None
_STAMP = None


def evaluate(plan, ops, scenario=None):
    """config.json 변경 시 다시 초기화. 명시적 고속 반복에는 FinanceEngine 권장."""
    global _CACHE, _STAMP
    stamp = (ROOT / "config.json").stat().st_mtime_ns
    if _CACHE is None or _STAMP != stamp:
        _CACHE, _STAMP = FinanceEngine(), stamp
    return _CACHE.evaluate(plan, ops, scenario)
