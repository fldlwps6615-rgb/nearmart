"""저장된 선택 결과를 변조해 독립 검증기의 실패 탐지를 시험한다."""
import json
from pathlib import Path
import shutil
import tempfile
import pandas as pd
from src.common.io import ROOT
from .inputs import DispatchInputs
from .verify_t3 import read_csv, verify_saved
from .engine import DispatchEngine
from .search import make_plan


def main():
    inputs=DispatchInputs()
    selection=json.loads((ROOT/'outputs/dispatch/selection.json').read_text(encoding='utf-8'))
    kpis=read_csv(ROOT/'outputs/bm_kpi.csv')
    instance='BM1'; policy=selection[instance]
    reported=kpis[(kpis.instance==instance)&(kpis.policy==policy)].iloc[0].to_dict()
    original=ROOT/'outputs/dispatch'/instance/policy
    rows=[]

    def run(name,filename,mutate,expected):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs/dispatch') as tmp:
            folder=Path(tmp)
            for source in original.glob('*.csv'):
                shutil.copyfile(source,folder/source.name)
            frame=read_csv(folder/filename)
            changed=mutate(frame)
            changed.to_csv(folder/filename,index=False,encoding='utf-8-sig')
            checks,_=verify_saved(inputs,instance,policy,folder,reported)
            failed=set(checks.loc[checks.status=='FAIL','check'])
            passed=expected in failed
            rows.append(dict(test=name,expected_failure=expected,detected=passed,failures='|'.join(sorted(failed))))
            if not passed:
                raise AssertionError(f'{name}: {expected} 실패를 탐지하지 못함')

    run('중복 주문','T3.csv',lambda t:pd.concat([t,t.iloc[:1]],ignore_index=True),'a')
    run('누락 주문','T3.csv',lambda t:t.iloc[1:],'a')
    def time_shift(t):
        t.loc[0,'delivered_min']+=1
        return t
    run('구간 전달시각 변조','T3.csv',time_shift,'f')
    def pick_duration(t):
        t.loc[0,'pick_start_min']-=0.5
        return t
    run('피킹 표준시간 변조','T3.csv',pick_duration,'h')
    def broken_seq(t):
        t.loc[0,'trip_seq']=99
        return t
    run('트립 순번 변조','T3.csv',broken_seq,'c')
    def staffing_change(t):
        t.loc[0,'contract_riders']+=1
        return t
    run('시간별 접속인원 변조','staffing.csv',staffing_change,'d')
    def logoff_change(t):
        i=t.index[t.role=='contract'][0]
        t.loc[i,'end_min']=t.loc[i,'start_min']+1
        return t
    run('복귀 전 로그아웃','work.csv',logoff_change,'work')
    def pay_change(t):
        t.loc[0,'pay_krw']+=800
        return t
    run('트립 지급액 로그 변조','trips.csv',pay_change,'derived_logs')
    altered=dict(reported); altered['cost_per_order']+=1
    checks,_=verify_saved(inputs,instance,policy,original,altered)
    detected=bool(((checks.check=='i')&(checks.status=='FAIL')).any())
    assert detected
    rows.append(dict(test='보고 KPI 비용 변조',expected_failure='i',detected=detected,failures='i'))
    # 외부 퀵은 T3의 계약/직고용 유형으로 위장하지 않고 별도 반환한다.
    orders,meta=inputs.instance(instance)
    sample=orders.iloc[:1].copy()
    sample.loc[:,'weight_kg']=36.
    engine=DispatchEngine(inputs,sample,meta)
    plan=make_plan(engine,'P2',0,1,0,1.2,1.,[],1)
    try:
        engine.simulate(plan)
        raise AssertionError('외부 운송모형 없이 퀵 시각을 생성함')
    except ValueError as exc:
        assert '외부 퀵' in str(exc)
    rows.append(dict(test='외부 퀵 완료시각 모형 누락 거부',expected_failure='external_model_required',detected=True,failures='expected_error'))
    quick=engine.simulate(plan,external_delivery=lambda order,end:order['order_min']+70)
    passed=(len(quick.t3)==0 and len(quick.external)==1 and quick.kpi['orders']==1 and
            quick.kpi['external_quick_krw']==inputs.cost['external_quick_krw'] and
            quick.kpi['late_coupon_krw']==inputs.cost['late_coupon_krw'] and pd.isna(quick.kpi['on_time']))
    assert passed
    rows.append(dict(test='외부 퀵 비용·쿠폰·정시율 제외',expected_failure='none',detected=passed,failures=''))
    # 17:59 상차 시작 → 18:01 실제 출발: 피크 할증은 출발시각에 붙어야 한다.
    rainy,rain_meta=inputs.instance('BM3')
    rainy=rainy.iloc[:2].copy()
    rainy.loc[:,'order_min']=1078.
    rainy.loc[:,'pick_std_min']=1.
    rain_engine=DispatchEngine(inputs,rainy,rain_meta)
    rain_plan=make_plan(rain_engine,'P2',0,2,0,1.2,1.,[],2)
    rain_plan['pickers']['17']=2
    rain_plan['pickers']['18']=2
    rain_out=rain_engine.simulate(rain_plan)
    trip=rain_out.trips.iloc[0]
    cost=inputs.cost
    base=cost['contract_base_krw']+cost['contract_extra_order_krw']+cost['contract_km_rate_krw']*max(0,trip.route_km-cost['contract_km_free'])
    surcharge=2*(cost['contract_peak_surcharge_krw']+cost['contract_weather_surcharge_rain_krw'])
    assert len(rain_out.trips)==1 and abs(trip.depart_min-1081)<1e-8 and abs(trip.pay_krw-base-surcharge)<1e-7
    rows.append(dict(test='18시 경계 실제 출발·비 할증 합산',expected_failure='none',detected=True,failures=''))
    assert inputs.cap(rain_meta,18)==17 and inputs.cap(rain_meta,21)==11 and inputs.cap(rain_meta,25)==11
    assert abs(inputs.travel.speed(18,'관악구','관악구','rain')/inputs.travel.speed(18,'관악구','관악구','clear')-.88)<1e-10
    rows.append(dict(test='BM3 공급 내림·자정 연장·속도 0.88',expected_failure='none',detected=True,failures=''))
    output=ROOT/'outputs/dispatch/validation_tests.csv'
    pd.DataFrame(rows).to_csv(output,index=False,encoding='utf-8-sig')
    print(json.dumps({'tests':len(rows),'passed':sum(r['detected'] for r in rows)},ensure_ascii=False))


if __name__=='__main__':
    main()
