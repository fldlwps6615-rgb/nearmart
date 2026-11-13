"""저장된 T3/원입력/보조 파일을 다시 읽는 독립 검증. 시뮬레이터 호출 없음."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from src.common.io import ROOT, load_config
from .inputs import DispatchInputs, T3_COLUMNS


def read_csv(path):
    return pd.read_csv(path, encoding='utf-8-sig', dtype={c:str for c in
                       ['instance','order_id','rider_id','trip_id','worker_id','picker_id','site_id','adm_cd2','adm_cd']})


def peak(intervals):
    changes = {}
    for start,end in intervals:
        if end > start+1e-8:
            changes[start] = changes.get(start,0)+1
            changes[end] = changes.get(end,0)-1
    count = maximum = 0
    anchor = None
    delta = 0
    for t in sorted(changes):
        if anchor is not None and t-anchor>1e-8:
            count += delta
            maximum = max(maximum,count)
            delta = 0
            anchor = t
        if anchor is None:
            anchor = t
        delta += changes[t]
    count += delta
    maximum = max(maximum,count)
    return maximum


def verify_saved(inputs, instance, policy, folder, reported, *, t3_path=None):
    folder = Path(folder)
    orders, meta = inputs.instance(instance)
    source = orders.set_index('order_id')
    t3 = read_csv(t3_path or folder/'T3.csv')
    if t3_path:
        t3 = t3[t3.instance==instance].copy()
    work, staffing = read_csv(folder/'work.csv'), read_csv(folder/'staffing.csv')
    picking, saved_bays, saved_trips = read_csv(folder/'picking.csv'), read_csv(folder/'bays.csv'), read_csv(folder/'trips.csv')
    c, s, tt = inputs.cost, inputs.settings, inputs.travel
    tol = s['verification_tolerance']
    results=[]

    def record(group,check,n,detail):
        results.append(dict(instance=instance,policy=policy,group=group,check=check,
                            status='PASS' if n==0 else 'FAIL',violations=int(n),detail=detail))

    keys=set(source.index)
    missing=keys-set(t3.order_id)
    extra=set(t3.order_id)-keys
    duplicate=int(t3.order_id.duplicated().sum())
    invalid_cols=int(list(t3.columns)!=T3_COLUMNS)
    record('T3+고정입력','a',len(missing)+len(extra)+duplicate+invalid_cols+int(len(orders)!=meta['n_orders']),
           f'원주문 {len(orders)}; T3 {len(t3)}; 누락 {len(missing)}, 외부키 {len(extra)}, 중복 {duplicate}; 열순서 검사')
    if missing or extra or duplicate or invalid_cols:
        return pd.DataFrame(results), {}
    joined=t3.merge(orders,on=['instance','order_id'],validate='one_to_one')
    finite=np.isfinite(joined[['pick_start_min','pick_end_min','depart_min','delivered_min']].to_numpy(float)).all(axis=1)
    durations=(joined.delivered_min-joined.order_min).to_numpy()
    late=int((durations>s['sla_min']+tol).sum())
    record('T3+고정입력','b',late,f'60분 초과 {late}; 정시율 {np.mean(durations<=s["sla_min"]+tol):.12f}')
    h_bad=(~finite)|(joined.pick_start_min<joined.order_min-tol)|((joined.pick_end_min-joined.pick_start_min-joined.pick_std_min).abs()>tol)
    record('T3+고정입력','h',int(h_bad.sum()),'접수 이후 피킹 및 pick_std_min 그대로 적용')
    # FIFO 피킹은 같은 접수시각에서 order_id를 타이브레이크로 사용한다.
    ordered=joined.sort_values(['order_min','order_id'])
    record('T3+고정입력','picking_fifo',int((ordered.pick_start_min.diff().fillna(0)<-tol).sum()),'접수순 피킹 시작')
    trip_data=[]
    route_bad,trip_bad=0,0
    origin=(meta['hub_lat'],meta['hub_lon'],meta['hub_gu'])
    for trip_id,group in joined.groupby('trip_id',sort=False):
        group=group.sort_values('trip_seq')
        first=group.iloc[0]
        shape_bad=(list(group.trip_seq)!=list(range(1,len(group)+1)) or group.rider_id.nunique()!=1 or
                   group.rider_type.nunique()!=1 or group.depart_min.nunique()!=1 or first.rider_type not in ['contract','employed'])
        trip_bad+=int(len(group)>c['trip_max_orders'] or group.weight_kg.sum()>c['trip_max_kg']+tol or shape_bad)
        depart=float(first.depart_min)
        load=depart-c['load_time_min']
        route_bad+=int((group.pick_end_min>load+tol).any())
        t,prev,distance=depart,origin,0.
        for row in group.itertuples():
            dest=(row.cust_lat,row.cust_lon,row.gu)
            distance+=tt.road(*prev,*dest)[3]
            t+=tt.minutes(*prev,*dest,t/60,meta['weather'])
            t+=c['handover_apt_min'] if row.building_type=='아파트' else c['handover_other_min']
            route_bad+=int(abs(t-row.delivered_min)>tol)
            prev=dest
        returned=t+tt.minutes(*prev,*origin,t/60,meta['weather'])
        pay=0.
        if first.rider_type=='contract':
            pay=c['contract_base_krw']+(len(group)-1)*c['contract_extra_order_krw']+c['contract_km_rate_krw']*max(0,distance-c['contract_km_free'])
            if 18<=int(depart//60)%24<21:
                pay+=len(group)*c['contract_peak_surcharge_krw']
            if meta['weather']=='rain':
                pay+=len(group)*c['contract_weather_surcharge_rain_krw']
            elif meta['weather'] in ['heavy_rain','snow']:
                pay+=len(group)*c['contract_weather_surcharge_heavy_krw']
        trip_data.append(dict(trip_id=trip_id,rider_id=first.rider_id,rider_type=first.rider_type,
                              load_start_min=load,depart_min=depart,return_min=returned,
                              route_km=distance,pay_krw=pay,orders=len(group),weight_kg=float(group.weight_kg.sum())))
    trips=pd.DataFrame(trip_data)
    record('T3+고정입력','c',trip_bad,'트립당 5건·35kg·연속 순번·동일 라이더')
    lower_bays=[]
    for rider,group in trips.groupby('rider_id'):
        group=group.sort_values('load_start_min')
        previous_return=None
        for row in group.itertuples():
            if previous_return is not None:
                route_bad+=int(row.load_start_min<previous_return-tol)
            # 트립 사이 로그아웃 여부는 T3만으로 알 수 없다. 확실한 상차 구간만 하한이다.
            lower_bays.append((row.load_start_min,row.depart_min))
            previous_return=row.return_min
    record('T3+고정입력','f',route_bad,'구간별 출발시각 속도·날씨·상차·전달·복귀·라이더 트립 비중첩')
    lower_peak=peak(lower_bays)
    record('T3+고정입력','e',int(lower_peak>meta['bike_bays']),
           f'T3 상차 구간 동시 최대 {lower_peak}/{meta["bike_bays"]}; 트립 사이 접속/대기·무배송 라이더는 e_full로 보완')
    pick_peak=peak(zip(joined.pick_start_min,joined.pick_end_min))
    record('T3+고정입력','g',int(pick_peak>s['max_pickers']),
           f'피킹 동시 최대 {pick_peak}/{s["max_pickers"]}; 시간별 계획 한도와 담당 피커는 g_staffing으로 보완')
    # 근무구간 유효성, 근무자 중첩, 각 트립의 복귀까지 근무 범위 확인
    work_bad=int((work.end_min<=work.start_min).sum())
    work_bad+=int((~work.role.isin(['contract','employed','picker'])).sum())
    for worker,group in work.groupby('worker_id'):
        work_bad+=int(peak(zip(group.start_min,group.end_min))>1)
        work_bad+=int(group.role.nunique()!=1)
    employed=work[work.role=='employed']
    work_bad+=int((~((employed.end_min-employed.start_min)/60).isin([4.,8.])).sum())
    work_bad+=int(((work[['start_min','end_min']]%60).abs()>tol).any(axis=1).sum())
    for row in trips.itertuples():
        matching=work[(work.worker_id==row.rider_id)&(work.role==row.rider_type)&
                      (work.start_min<=row.load_start_min+tol)&(work.end_min>=row.return_min-tol)]
        work_bad+=int(len(matching)!=1)
    record('인력표·근무기록','work',work_bad,'근무 비중첩·정시 경계·직고용 4/8시간·복귀까지 근무 포함')
    d_bad=0
    for row in staffing.itertuples():
        current=work[(work.start_min<=row.hour*60+tol)&(work.end_min>row.hour*60+tol)]
        d_bad+=int(len(current[current.role=='contract'])!=row.contract_riders)
        d_bad+=int(row.contract_riders>inputs.cap(meta,row.hour))
        d_bad+=int(len(current[current.role=='employed'])!=row.employed_riders)
        d_bad+=int(len(current[current.role=='picker'])!=row.pickers)
        d_bad+=int(row.pickers>s['max_pickers'] or (9<=row.hour<24 and row.pickers<1))
    expected_hours=set(range(s['open_hour'],s['planning_end_hour']))
    d_bad+=int(set(staffing.hour)!=expected_hours or staffing.hour.duplicated().any())
    record('인력표·근무기록','d',d_bad,'시간별 접속/근무 인원 일치·A등급 상한×날씨계수 내림·피커 정원')
    full_bays=[]
    for shift in work[work.role!='picker'].itertuples():
        own=trips[(trips.rider_id==shift.worker_id)&(trips.load_start_min>=shift.start_min-tol)&
                  (trips.return_min<=shift.end_min+tol)].sort_values('load_start_min')
        cursor=shift.start_min
        for trip in own.itertuples():
            if trip.depart_min>cursor+tol:
                full_bays.append((shift.worker_id,cursor,trip.depart_min))
            cursor=trip.return_min
        if shift.end_min>cursor+tol:
            full_bays.append((shift.worker_id,cursor,shift.end_min))
    full_peak=peak([(a,b) for _,a,b in full_bays])
    record('인력표·근무기록','e_full',int(full_peak>meta['bike_bays']),f'모든 접속 라이더 대기·상차 포함 최대 {full_peak}/{meta["bike_bays"]}')
    g_bad=0
    for row in staffing.itertuples():
        intervals=[(max(a,row.hour*60),min(b,(row.hour+1)*60)) for a,b in zip(joined.pick_start_min,joined.pick_end_min)
                   if a<(row.hour+1)*60 and b>row.hour*60]
        g_bad+=int(peak(intervals)>row.pickers)
    if set(picking.order_id)!=keys or picking.order_id.duplicated().any():
        g_bad+=1
    else:
        check=picking.merge(t3,on='order_id',suffixes=('_log','_t3'),validate='one_to_one')
        g_bad+=int(((check.pick_start_min_log-check.pick_start_min_t3).abs()>tol).sum())
        g_bad+=int(((check.pick_end_min_log-check.pick_end_min_t3).abs()>tol).sum())
        for _,group in picking.groupby('picker_id'):
            g_bad+=int(peak(zip(group.pick_start_min,group.pick_end_min))>1)
        for row in picking.itertuples():
            matching=work[(work.worker_id==row.picker_id)&(work.role=='picker')&
                          (work.start_min<=row.pick_start_min+tol)&(work.end_min>=row.pick_end_min-tol)]
            g_bad+=int(len(matching)!=1)
    record('인력표·근무기록','g_staffing',g_bad,'시간별 피커 수·개인 중첩·피킹 기록·근무 범위')
    # 저장된 파생 로그도 T3로부터 재계산된 값과 비교한다.
    log_bad=0
    if set(saved_trips.trip_id)!=set(trips.trip_id) or saved_trips.trip_id.duplicated().any():
        log_bad+=1
    else:
        compare=saved_trips.set_index('trip_id').loc[trips.trip_id]
        for col in ['load_start_min','depart_min','return_min','route_km','pay_krw','orders','weight_kg']:
            log_bad+=int((np.abs(compare[col].to_numpy()-trips[col].to_numpy())>tol).sum())
    computed_bays=pd.DataFrame(full_bays,columns=['rider_id','start_min','end_min']).sort_values(['rider_id','start_min']).reset_index(drop=True)
    saved_bays=saved_bays.sort_values(['rider_id','start_min']).reset_index(drop=True)
    if len(computed_bays)!=len(saved_bays):
        log_bad+=1
    else:
        log_bad+=int((computed_bays.rider_id!=saved_bays.rider_id).sum())
        log_bad+=int((np.abs(computed_bays[['start_min','end_min']].to_numpy()-saved_bays[['start_min','end_min']].to_numpy())>tol).sum())
    record('인력표·근무기록','derived_logs',log_bad,'저장된 트립·체류 기록 = T3+근무 기록 재구성')
    hours=(work.end_min-work.start_min)/60
    contract_h=float(hours[work.role=='contract'].sum())
    employed_h=float(hours[work.role=='employed'].sum())
    picker_h=float(hours[work.role=='picker'].sum())
    pay=float(trips.pay_krw.sum())
    employed_cost=employed_h*(c['employed_wage_krw_per_h_2027']*c['labor_load_factor']+c['employed_vehicle_krw_per_month']/s['fte_hours_per_month'])+int((t3.rider_type=='employed').sum())*c['employed_incentive_krw']
    picker_cost=picker_h*c['picker_wage_krw_per_h_2027']*c['labor_load_factor']
    coupon=late*c['late_coupon_krw']
    income=pay/contract_h if contract_h else float('nan')
    supply=bool(income>=s['min_contract_hourly_krw']) if contract_h else True
    record('인력표·근무기록','supply',int(not supply),f'위탁 지급 {pay:.9f}원 / 접속 {contract_h}시간 = {income:.9f}원; 기준 {s["min_contract_hourly_krw"]}')
    record('인력표·근무기록','hours',int(abs(contract_h-staffing.contract_riders.sum())>tol), '위탁 접속시간 = 시간별 접속 인원 합; 무배송 인원 포함')
    counts=t3.groupby('rider_id').size().reindex(work.loc[work.role!='picker','worker_id'].unique(),fill_value=0)
    n=len(t3)
    total=pay+employed_cost+picker_cost+coupon
    kpi=dict(orders=n,late_orders=late,on_time=float(np.mean(durations<=s['sla_min']+tol)),
             avg_min=float(np.mean(durations)),p90_min=float(np.quantile(durations,.9)),max_min=float(max(durations)),
             orders_per_trip=n/len(trips),cost_per_order=total/n,rider_hourly=income,
             workload_cv=float(counts.std(ddof=0)/counts.mean()),supply_ok=supply,bay_max=full_peak,
             contract_pay_krw=pay,employed_cost_krw=employed_cost,picker_cost_krw=picker_cost,
             late_coupon_krw=coupon,external_quick_krw=0.,total_cost_krw=total,total_cost_eok=total/1e8,
             contract_hours=contract_h,employed_hours=employed_h,picker_hours=picker_h,
             contract_rider_cost_per_order_krw=pay/n,employed_rider_cost_per_order_krw=employed_cost/n,picker_cost_per_order_krw=picker_cost/n,
             earnings_shortfall_krw=max(0,s['min_contract_hourly_krw']-income) if contract_h else 0.)
    cap_bad=sum(row.contract_riders>inputs.cap(meta,row.hour) for row in staffing.itertuples())
    picker_bad=sum(row.pickers>s['max_pickers'] or (9<=row.hour<24 and row.pickers<1) for row in staffing.itertuples())
    block_bad=int((~((employed.end_min-employed.start_min)/60).isin([4.,8.])).sum())
    violations=int(full_peak>meta['bike_bays'])+cap_bad+picker_bad+block_bad
    kpi.update(rule_violations=violations,feasible=bool(violations==0 and late==0 and supply),external_late_orders=0)
    differences=[]
    for key,value in kpi.items():
        saved=reported.get(key,0 if key=='external_late_orders' else None)
        if pd.isna(value) and pd.isna(saved):
            continue
        if not math.isclose(float(value),float(saved),abs_tol=tol,rel_tol=1e-10):
            differences.append(f'{key}:{saved}->{value}')
    record('인력표·근무기록','i',len(differences),'재계산 KPI 비교 '+('; '.join(differences) if differences else f'{len(kpi)}항목 일치'))
    return pd.DataFrame(results), dict(instance=instance,policy=policy,**kpi)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--root',default='outputs/dispatch')
    parser.add_argument('--report-t3',default='report/T3_benchmark_dispatch.csv')
    args=parser.parse_args()
    inputs=DispatchInputs(load_config(args.config))
    kpis=read_csv(ROOT/'outputs/bm_kpi.csv')
    selected=json.loads((ROOT/args.root/'selection.json').read_text(encoding='utf-8'))
    results=[]; computed=[]; selected_results=[]
    for row in kpis.to_dict('records'):
        instance,policy=row['instance'],row['policy']
        folder=ROOT/args.root/instance/policy
        checks,kpi=verify_saved(inputs,instance,policy,folder,row)
        results.append(checks); computed.append(kpi)
        if selected[instance]==policy:
            report_checks,_=verify_saved(inputs,instance,policy,folder,row,t3_path=ROOT/args.report_t3)
            selected_results.append(report_checks)
    all_checks=pd.concat(results,ignore_index=True)
    selected_checks=pd.concat(selected_results,ignore_index=True)
    report=read_csv(ROOT/args.report_t3)
    global_bad=int(set(report.instance)!=set(inputs.meta.instance))+int(len(report)!=len(inputs.orders))
    global_bad+=int(set(zip(report.instance,report.order_id))!=set(zip(inputs.orders.instance,inputs.orders.order_id)))
    global_bad+=int(report.duplicated(['instance','order_id']).any())
    selected_checks=pd.concat([selected_checks,pd.DataFrame([dict(instance='ALL',policy='SELECTED',
                               group='T3+고정입력',check='a_global',status='PASS' if global_bad==0 else 'FAIL',
                               violations=global_bad,detail=f'제출 T3 전체 {len(report)}행; 인스턴스·주문 키 전체 대조')])],ignore_index=True)
    expected_pairs={(instance,policy) for instance in inputs.meta.instance for policy in ['P0','P1','P2']}
    coverage_bad=int(set(zip(kpis.instance,kpis.policy))!=expected_pairs)+int(kpis.duplicated(['instance','policy']).any())
    selected_checks=pd.concat([selected_checks,pd.DataFrame([dict(instance='ALL',policy='SELECTED',
                               group='인력표·근무기록',check='policy_coverage',status='PASS' if coverage_bad==0 else 'FAIL',
                               violations=coverage_bad,detail='BM1~3 × P0~2 비교 KPI 9조합 완전성')])],ignore_index=True)
    all_checks.to_csv(ROOT/'outputs/dispatch_verification.csv',index=False,encoding='utf-8-sig')
    selected_checks.to_csv(ROOT/'outputs/dispatch_selected_verification.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(computed).to_csv(ROOT/'outputs/bm_kpi_recalculated.csv',index=False,encoding='utf-8-sig')
    status_path=ROOT/args.root/'run_status.json'
    status=json.loads(status_path.read_text(encoding='utf-8'))
    ready=not (selected_checks.status=='FAIL').any() and status['source_unchanged'] and status['travel_module_unchanged']
    status.update(status='complete' if ready else 'needs_review',report_ready=bool(ready),
                  verification_checks=len(selected_checks),verification_failures=int((selected_checks.status=='FAIL').sum()),
                  reason='저장된 제출 T3와 근무기록 독립 재계산 완료' if ready else '독립 검증 실패 또는 실행 불가; 근접 후보 3개 검토 필요')
    status_path.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(checks=len(all_checks),selected_checks=len(selected_checks),
                         selected_failures=int((selected_checks.status=='FAIL').sum())),ensure_ascii=False))
    return int((selected_checks.status=='FAIL').any())


if __name__=='__main__':
    raise SystemExit(main())
