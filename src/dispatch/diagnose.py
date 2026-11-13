"""사전 기대값의 시간대 기준 및 P0 실행 불가 원인을 결과와 대조한다."""
import pandas as pd
from src.common.io import ROOT
from .inputs import DispatchInputs
from .engine import DispatchEngine
from .verify_t3 import read_csv


def main():
    inputs=DispatchInputs()
    timing=[]; policies=[]
    kpis=read_csv(ROOT/'outputs/bm_kpi.csv')
    staffing=read_csv(ROOT/'outputs/bm_staffing.csv')
    for name in inputs.meta.instance:
        orders,meta=inputs.instance(name)
        engine=DispatchEngine(inputs,orders,meta)
        for i,row in enumerate(orders.itertuples()):
            depart=row.order_min+row.pick_std_min+inputs.cost['load_time_min']
            for clock,start in [('actual_departure',depart),('order_time',row.order_min),('pick_end',row.order_min+row.pick_std_min)]:
                travel=engine.geometry.minutes(engine.geometry.hub,i,start)
                duration=row.pick_std_min+inputs.cost['load_time_min']+travel+engine.geometry.handover[i]
                timing.append(dict(instance=name,order_id=row.order_id,clock=clock,order_min=row.order_min,
                                   depart_min=depart,travel_min=travel,delivery_min=duration))
        baseline=kpis[(kpis.instance==name)&(kpis.policy=='P0')].iloc[0]
        staff=staffing[(staffing.instance==name)&(staffing.policy=='P0')]
        cap_bad=sum(r.contract_riders>inputs.cap(meta,r.hour) for r in staff.itertuples())
        actual=bool(cap_bad or not baseline.supply_ok)
        policies.append(dict(instance=name,expected_cap_or_income_failure=True,actual_cap_or_income_failure=actual,
                             cap_exceed_hours=cap_bad,rider_hourly=baseline.rider_hourly,late_orders=baseline.late_orders,
                             bay_max=baseline.bay_max,bike_bays=meta['bike_bays'],feasible=baseline.feasible,
                             interpretation='실제 접속 상한은 지키며 공급 부족을 지연으로 관측; 대기 공간 초과는 별도 실행 불가 원인'))
    pd.DataFrame(timing).to_csv(ROOT/'outputs/dispatch/solo_timing_diagnostic.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(policies).to_csv(ROOT/'outputs/dispatch/expectation_policy.csv',index=False,encoding='utf-8-sig')
    print(pd.DataFrame(timing).groupby(['instance','clock']).delivery_min.agg(['mean','max']).to_string())


if __name__=='__main__':
    main()
