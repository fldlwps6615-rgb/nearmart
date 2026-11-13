"""순차 추가 및 사전안 시작의 제거·교체·추가 탐색."""
from itertools import product
from pathlib import Path
import hashlib
import json

import numpy as np

from src.network.model import NetworkModel


def canonical(rows):
    return tuple(sorted(rows))


class Search:
    def __init__(self, model, alternative, family, pilots):
        self.m = model
        self.name = alternative
        self.family = family
        self.spec = model.p["alternatives"][alternative]
        self.T = self.spec["T"]
        self.pilots = pilots
        self.branch = "".join(str(int(i in pilots)) for i in model.pilots)
        self.trace = []
        self.phase_stats = []

    def score(self, plan):
        return self.m.evaluate(plan, self.T)

    def priority(self, score):
        return score["npv"]

    def legal(self, plan):
        cap = self.spec["max_hubs_2027"]
        if cap is not None and sum(o <= 2027 < c for _, o, c in plan) > cap:
            return False
        return all(o >= self.m.available_years[i] and o < c for i, o, c in plan)

    def better(self, current, candidate):
        threshold = self.m.p["npv_improvement_min_krw"]
        if candidate["npv"] <= current["npv"]+threshold:
            return False
        if current["max_funding"] <= self.m.limit:
            return candidate["max_funding"] <= self.m.limit
        return candidate["max_funding"] < current["max_funding"]-threshold

    def record(self, year, action, before, after, changed):
        a, b = self.score(before), self.score(after)
        self.trace.append(dict(alternative=self.name, search=self.family, pilot_branch=self.branch, year=year,
                               action=action, changed=changed, npv_before=a["npv"], npv_after=b["npv"],
                               delta_npv=b["npv"]-a["npv"], funding_before=a["max_funding"], funding_after=b["max_funding"],
                               entered_limit=int(a["max_funding"] > self.m.limit >= b["max_funding"])))

    def available(self, plan, year):
        existing = {i for i, _, _ in plan}
        return [i for i in range(len(self.m.ids)) if i not in existing and i not in self.m.pilots
                and self.m.available_years[i] <= year]

    def add_pass(self, plan, year):
        while True:
            current, best_plan, best_score = self.score(plan), None, None
            for i in self.available(plan, year):
                trial = canonical([*plan, (i, year, self.m.end)])
                if not self.legal(trial):
                    continue
                score = self.score(trial)
                if self.better(current, score) and (best_score is None or self.priority(score) > self.priority(best_score)):
                    best_plan, best_score = trial, score
            if best_plan is None:
                return plan
            changed = ",".join(self.m.ids[i] for i, _, _ in set(best_plan)-set(plan))
            self.record(year, "add", plan, best_plan, changed)
            plan = best_plan

    @staticmethod
    def remove_at(plan, site, year):
        result = []
        for i, o, c in plan:
            if i == site:
                if o < year:
                    result.append((i, o, year))
            else:
                result.append((i, o, c))
        return canonical(result)

    def remove_pass(self, plan, year, action="remove"):
        for i in [i for i, o, c in plan if o <= year < c]:
            trial = self.remove_at(plan, i, year)
            if self.better(self.score(plan), self.score(trial)):
                self.record(year, action, plan, trial, self.m.ids[i])
                plan = trial
        return plan

    def swap_pass(self, plan, year):
        for old in [i for i, o, c in plan if o <= year < c]:
            current = self.score(plan)
            best_plan, best_score, selected = None, None, None
            reduced = self.remove_at(plan, old, year)
            for new in self.available(plan, year):
                trial = canonical([*reduced, (new, year, self.m.end)])
                if not self.legal(trial):
                    continue
                score = self.score(trial)
                if self.better(current, score) and (best_score is None or self.priority(score) > self.priority(best_score)):
                    best_plan, best_score, selected = trial, score, new
            if best_plan is not None:
                self.record(year, "swap", plan, best_plan, f"{self.m.ids[old]}->{self.m.ids[selected]}")
                plan = best_plan
        return plan

    def initial(self):
        plan = canonical([(i, 2027, self.m.end) for i in self.pilots])
        if self.family == "greedy":
            return plan
        seed = [self.m.index[sid] for sid in self.m.p["seed_sites"] if sid in self.m.index and self.m.index[sid] not in self.m.pilots]
        deferred = []
        for i in seed:
            first = next(y for y in self.m.p["phases"] if y >= self.m.available_years[i])
            if first > 2027:
                deferred.append((i, first, self.m.end))
        eligible = [i for i in seed if self.m.available_years[i] <= 2027]
        cap = self.spec["max_hubs_2027"]
        if cap is None:
            return canonical([*plan, *[(i, 2027, self.m.end) for i in eligible], *deferred])
        # B2의 상위 8곳은 파일럿 유지 수를 포함해 순차 한계 NPV로 선정한다.
        while eligible and len(plan) < cap:
            best = max(eligible, key=lambda i: self.score(canonical([*plan, (i, 2027, self.m.end)]))["npv"])
            plan = canonical([*plan, (best, 2027, self.m.end)])
            eligible.remove(best)
        return canonical([*plan, *[(i, 2032, self.m.end) for i in eligible], *deferred])

    def run(self):
        plan = self.initial()
        initial = plan
        for year in self.m.p["phases"]:
            before_count = self.m.evaluations
            plan = self.remove_pass(plan, year, "close_review")
            if self.family == "greedy":
                plan = self.add_pass(plan, year)
                plan = self.remove_pass(plan, year)
                plan = self.swap_pass(plan, year)
            else:
                plan = self.swap_pass(plan, year)
                plan = self.add_pass(plan, year)
            score = self.score(plan)
            self.phase_stats.append(dict(alternative=self.name, search=self.family, pilot_branch=self.branch, year=year,
                                         evaluations=self.m.evaluations-before_count, **score))
            print(f"{self.name}/{self.family}/{self.branch} {year}: NPV {score['npv_eok']:.2f}억, 자금 {score['max_funding_eok']:.2f}억", flush=True)
        return dict(plan=plan, initial=initial, kpi=self.score(plan), alternative=self.name, search=self.family,
                    pilot_branch=self.branch, trace=self.trace, phases=self.phase_stats)


def run_family(model, alternative, family):
    branches = []
    for mask in product([False, True], repeat=len(model.pilots)):
        pilots = [i for i, keep in zip(model.pilots, mask) if keep]
        branches.append(Search(model, alternative, family, pilots).run())
    feasible = [b for b in branches if b["kpi"]["max_funding"] <= model.limit]
    best = max(feasible or branches, key=lambda b: b["kpi"]["npv"])
    return best, branches


def family_worker(cfg, alternative, family, fingerprint):
    """독립 프로세스별 모델을 사용하고 완료 결과만 체크포인트로 공개한다."""
    model = NetworkModel(cfg)
    path = model.out / f"network_checkpoint_{alternative}_{family}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("fingerprint") == fingerprint:
            branches = cached["branches"]
            for b in branches:
                b["plan"] = canonical(tuple(row) for row in b["plan"])
                b["initial"] = canonical(tuple(row) for row in b["initial"])
            return branches[cached["best_index"]], branches, 0
    best, branches = run_family(model, alternative, family)
    payload = dict(fingerprint=fingerprint, best_index=next(i for i,b in enumerate(branches) if b is best), branches=branches)
    def native(value):
        if isinstance(value,np.generic):
            return value.item()
        raise TypeError(type(value).__name__)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload,ensure_ascii=False,default=native),encoding="utf-8")
    temporary.replace(path)
    return best, branches, model.evaluations
