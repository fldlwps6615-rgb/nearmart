"""거친 격자 → 상위 후보 주변 세밀 격자. 인스턴스별 wall-clock 제한."""
import copy
import itertools
import json
import math
import time
import pandas as pd
from .engine import SearchDeadline


def rank(k):
    return (k['rule_violations'], k['late_orders'], not k['supply_ok'],
            k['earnings_shortfall_krw'], k['cost_per_order'], k['contract_hours'])


def make_plan(engine, policy, wait, target, margin, picker_factor, rider_factor, blocks, tail):
    settings = engine.settings
    orders = engine.orders.copy()
    orders['hour'] = (orders.order_min//60).astype(int)
    pick = orders.groupby('hour').pick_std_min.sum().to_dict()
    plan = dict(policy=policy, wait=float(wait), wait_by_hour={}, target=int(target), margin=float(margin),
                picker_factor=float(picker_factor), rider_factor=float(rider_factor), tail=int(tail),
                pickers={}, contract={}, employed_blocks=copy.deepcopy(blocks))
    for h in range(settings['open_hour'], settings['planning_end_hour']):
        duration = 30 if h == 23 else 60
        plan['pickers'][str(h)] = min(settings['max_pickers'], max(1, math.ceil(pick.get(h,0)/duration*picker_factor))) if h<24 else settings['tail_pickers']
        cap = engine.inputs.cap(engine.meta,h)
        if policy in ['P0','P1']:
            contract = cap
        else:
            subset = orders.index[orders.hour==h].tolist()
            if subset:
                workload = sum(engine.geometry.handover[i] +
                               2*engine.geometry.minutes(engine.geometry.hub,i,h*60)/target +
                               settings['staffing_intercustomer_min']*(target-1)/target for i in subset)
                employed = sum(b['count'] for b in blocks if b['start_hour']<=h<b['start_hour']+b['hours'])
                contract = max(settings['min_contract_riders'], math.ceil(workload/duration*rider_factor)-employed)
            else:
                contract = tail
        plan['contract'][str(h)] = max(0, min(cap, contract))
    return plan


def search_instance(engine, progress=None):
    cfg = engine.settings['search']
    start = time.perf_counter()
    end = start + cfg['seconds_per_instance']
    seen, log, candidates = set(), [], []
    best_policy = {}
    timed_out = False
    last_progress = start

    def evaluate(plan, stage):
        nonlocal timed_out, last_progress
        signature = json.dumps(plan, sort_keys=True)
        if signature in seen:
            return
        seen.add(signature)
        if time.perf_counter() >= end:
            timed_out = True
            raise SearchDeadline()
        try:
            result = engine.simulate(plan, deadline_clock=end)
        except ValueError as exc:
            log.append(dict(candidate=len(log)+1, stage=stage, policy=plan['policy'], status='incomplete', reason=str(exc),
                            elapsed_sec=time.perf_counter()-start, plan_json=signature))
            if progress and time.perf_counter()-last_progress>=cfg['progress_interval_sec']:
                progress(dict(instance=engine.meta['instance'],candidates=len(log),elapsed_sec=round(time.perf_counter()-start,1),status='incomplete_candidate',reason=str(exc)))
                last_progress=time.perf_counter()
            return
        row = dict(candidate=len(log)+1, stage=stage, status='complete', **result.kpi,
                   elapsed_sec=time.perf_counter()-start, plan_json=signature)
        log.append(row)
        candidates.append((row, plan))
        old = best_policy.get(plan['policy'])
        if old is None or rank(result.kpi) < rank(old.kpi):
            best_policy[plan['policy']] = result
        if progress and time.perf_counter()-last_progress>=cfg['progress_interval_sec']:
            current = min((x[0] for x in candidates), key=rank)
            progress(dict(instance=engine.meta['instance'], candidates=len(log), elapsed_sec=round(time.perf_counter()-start,1),
                          best={k:current[k] for k in ['policy','rule_violations','late_orders','rider_hourly','cost_per_order']}))
            last_progress = time.perf_counter()

    try:
        # 기준 정책은 먼저 확보한다. 상한 접속은 종료 후도 24시까지 동일하게 유지한다.
        for policy in ['P0','P1']:
            evaluate(make_plan(engine,policy,**cfg['baseline_plans'][policy]), 'baseline')
        for anchor in cfg.get('anchor_candidates',[]):
            evaluate(make_plan(engine,'P2',**anchor),'coarse')
        axes = list(itertools.product(cfg['coarse_wait'], cfg['coarse_target'], cfg['coarse_margin'],
                                     cfg['coarse_picker_factor'], cfg['coarse_rider_factor'],
                                     cfg['coarse_blocks'], cfg['coarse_tail']))
        # 격자를 결정론적으로 섞어 시간 제한에서도 특정 축의 앞부분만 탐색하지 않는다.
        import random
        random.Random(cfg['shuffle_seed']).shuffle(axes)
        for wait,target,margin,pf,rf,blocks,tail in axes[:cfg['max_coarse_candidates']]:
            evaluate(make_plan(engine,'P2',wait,target,margin,pf,rf,blocks,tail),'coarse')
        seeds = sorted((pair for pair in candidates if pair[1]['policy']=='P2'), key=lambda pair:rank(pair[0]))[:cfg['refine_top']]
        for _, seed in seeds:
            variants = []
            for delta in cfg['fine_wait_delta']:
                p=copy.deepcopy(seed); p['wait']=max(0,p['wait']+delta); variants.append(p)
            for delta in cfg['fine_margin_delta']:
                p=copy.deepcopy(seed); p['margin']=max(0,p['margin']+delta); variants.append(p)
            for target in cfg['coarse_target']:
                p=copy.deepcopy(seed); p['target']=target; variants.append(p)
            # 시간대별 이산 근방을 독립적으로 평가한다.
            for field in ['contract','pickers','wait_by_hour']:
                for h in range(engine.settings['open_hour'], engine.settings['refinement_end_hour']):
                    for delta in [-1,1]:
                        p=copy.deepcopy(seed)
                        if field=='wait_by_hour':
                            p[field][str(h)] = max(0,p['wait']+delta*cfg['fine_hour_wait_step'])
                        else:
                            upper = engine.inputs.cap(engine.meta,h) if field=='contract' else engine.settings['max_pickers']
                            lower = 0 if field=='contract' else 1
                            p[field][str(h)] = max(lower,min(upper,p[field][str(h)]+delta))
                        variants.append(p)
            for i,block in enumerate(seed['employed_blocks']):
                for field,delta in [('start_hour',-1),('start_hour',1),('count',-1),('count',1),('hours',4 if block['hours']==4 else -4)]:
                    p=copy.deepcopy(seed); p['employed_blocks'][i][field]+=delta
                    b=p['employed_blocks'][i]
                    if b['count']>=0 and b['start_hour']+b['hours']<=engine.settings['planning_end_hour']:
                        variants.append(p)
            for p in variants:
                evaluate(p,'fine')
        # 현재 실행 가능한 최선점에서 시간별 인원을 한 명씩 줄여 다시 평가한다.
        if 'P2' in best_policy and best_policy['P2'].kpi['feasible']:
            for sweep in range(cfg['reduction_sweeps']):
                for field in ['contract','pickers']:
                    for h in range(engine.settings['open_hour'],engine.settings['refinement_end_hour']):
                        p=copy.deepcopy(best_policy['P2'].plan)
                        lower=0 if field=='contract' else 1
                        if p[field][str(h)]>lower:
                            p[field][str(h)]-=1
                            evaluate(p,'reduce_hours')
    except SearchDeadline:
        timed_out = True
    if not best_policy:
        raise RuntimeError('완료된 조합 없음: 입력/근무 horizon 확인 필요')
    ordered = sorted(candidates,key=lambda pair:rank(pair[0]))
    selected = min(best_policy.values(),key=lambda result:rank(result.kpi))
    return dict(selected=selected, policies=best_policy, log=pd.DataFrame(log),
                closest=pd.DataFrame([x[0] for x in ordered[:3]]),
                status=dict(instance=engine.meta['instance'], elapsed_sec=time.perf_counter()-start,
                            timed_out=timed_out, evaluated=len(log), feasible=selected.kpi['feasible'],
                            selected_policy=selected.plan['policy'], configured_limit_sec=cfg['seconds_per_instance']))
