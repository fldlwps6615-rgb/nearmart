"""④ 벤치마크 실행 및 결과 저장. python -m src.dispatch.run --config config.json"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from src.common.io import ROOT, load_config
from .inputs import DispatchInputs
from .engine import DispatchEngine
from .search import search_instance


def save(frame,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    frame.to_csv(path,index=False,encoding='utf-8-sig',float_format='%.12f')


def hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'data').rglob('*')) if p.is_file()}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config.json')
    args=parser.parse_args()
    config=load_config(args.config)
    inputs=DispatchInputs(config)
    root=ROOT/'outputs/dispatch'
    root.mkdir(parents=True,exist_ok=True)
    before=hashes()
    travel_hash=hashlib.sha256((ROOT/'src/travel_time.py').read_bytes()).hexdigest()
    started=time.perf_counter()
    kpis=[]; staffing=[]; chosen=[]; selections={}; statuses=[]; expected=[]
    (root/'run_status.json').write_text(json.dumps({'status':'running'},indent=2),encoding='utf-8')
    (root/'config_snapshot.json').write_text(json.dumps(config['dispatch'],ensure_ascii=False,indent=2),encoding='utf-8')
    for instance in inputs.meta.instance:
        orders,meta=inputs.instance(instance)
        engine=DispatchEngine(inputs,orders,meta)
        print(json.dumps({'instance':instance,'status':'search_started'},ensure_ascii=False),flush=True)
        result=search_instance(engine,progress=lambda row:print(json.dumps(row,ensure_ascii=False),flush=True))
        save(result['log'],root/instance/'search.csv')
        save(result['closest'],root/instance/'closest_three.csv')
        for policy,outcome in result['policies'].items():
            folder=root/instance/policy
            for name in ['t3','trips','work','picking','bays','staffing']:
                save(getattr(outcome,name),folder/('T3.csv' if name=='t3' else name+'.csv'))
            (folder/'plan.json').write_text(json.dumps(outcome.plan,ensure_ascii=False,indent=2),encoding='utf-8')
            kpis.append(outcome.kpi); staffing.append(outcome.staffing)
        best=result['selected']
        chosen.append(best.t3); selections[instance]=best.plan['policy']; statuses.append(result['status'])
        # 피킹 대기/묶음 대기 없이 단건 직행하는 기대값 비교(선택안 KPI와 분리).
        solo=[]
        for i,row in enumerate(orders.itertuples()):
            start=row.order_min+row.pick_std_min
            delivered,_,_=engine.geometry.itinerary([i],start)
            solo.append(delivered[0]-row.order_min)
        for metric,value in [('mean',float(np.mean(solo))),('max',float(max(solo)))]:
            reference=config['dispatch'][f'expected_solo_{metric}_min'][instance]
            expected.append(dict(instance=instance,metric='solo_'+metric+'_min',actual=value,expected=reference,difference=value-reference,
                                 definition='접수 즉시 피킹 시작·피킹 후 상차 2분·단건 직행·고객 전달 완료'))
        print(json.dumps(dict(**result['status'],kpi=best.kpi),ensure_ascii=False),flush=True)
    save(pd.DataFrame(kpis),ROOT/'outputs/bm_kpi.csv')
    save(pd.concat(staffing,ignore_index=True),ROOT/'outputs/bm_staffing.csv')
    save(pd.concat(chosen,ignore_index=True),ROOT/'report/T3_benchmark_dispatch.csv')
    save(pd.DataFrame(expected),ROOT/'outputs/bm_expectation_check.csv')
    (root/'selection.json').write_text(json.dumps(selections,indent=2),encoding='utf-8')
    after=hashes()
    hash_rows=[dict(file=name,sha256_before=value,sha256_after=after.get(name),unchanged=value==after.get(name)) for name,value in before.items()]
    save(pd.DataFrame(hash_rows),root/'source_hashes.csv')
    travel_unchanged=travel_hash==hashlib.sha256((ROOT/'src/travel_time.py').read_bytes()).hexdigest()
    successful=all(row['feasible'] for row in statuses)
    status=dict(status='complete_pending_verification' if successful else 'needs_review',instances=statuses,
                elapsed_sec=time.perf_counter()-started,source_unchanged=before==after,travel_module_unchanged=travel_unchanged,
                report_ready=False,reason='독립 검증 후 확정' if successful else '지연 0·수급·규칙을 함께 만족한 조합 없음; closest_three.csv 검토 후 중단')
    (root/'run_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False),flush=True)
    return 0 if successful else 2


if __name__=='__main__':
    raise SystemExit(main())
