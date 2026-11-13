"""① 실행: python -m src.clean.run [--config config.json]. 미승인 결함 발견 시 발행 중단."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_all, load_config, load_file, tables
from src.clean.rules import (BENCH, MASTER, ORDERS, SITES, benchmark_mismatch,
                             classify_area, coordinate_bad, order_days, shift_mask, validate)


class NeedsDecision(RuntimeError):
    pass


class IssueLog:
    def __init__(self):
        self.rows=[]

    def add(self, rule, file, check, df, mask, action, operation="UPDATE", example=None):
        mask=pd.Series(mask,index=df.index).fillna(False).astype(bool)
        count=int(mask.sum())
        if not count:
            return
        cols=[col for col in ["order_id","site_id","adm_cd2","weight_kg","items","order_time","order_min"] if col in df]
        sample=df.loc[mask,cols].head(3).to_json(orient="records",force_ascii=False)
        self.rows.append(dict(rule_id=rule,file=file,check=check,n_rows=count,
                              example=example or sample,action=operation+" | "+action))

    def frame(self):
        return pd.DataFrame(self.rows,columns=["rule_id","file","check","n_rows","example","action"])


def hashes(directory):
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def clean_data(raw,cfg,log):
    c=cfg["cleaning"]
    data=copy.deepcopy(raw)
    d=data[MASTER]
    d["population_scaled"]=d.population_2026.div(d.households_2026).gt(c["population_household_limit"]).astype(int)
    mask=d.population_scaled.eq(1)
    log.add("01_POP_SCALE",MASTER,"인구/세대 상한 초과",d,mask,"인구÷10; population_scaled=1")
    d.loc[mask,"population_2026"]=d.loc[mask,"population_2026"]/c["population_divisor"]
    d["apt_ratio_scaled"]=d.apt_hh_ratio.gt(1).astype(int); mask=d.apt_ratio_scaled.eq(1)
    log.add("01_APT_PERCENT",MASTER,"아파트 비율 백분율 혼입",d,mask,"아파트 비율÷100; apt_ratio_scaled=1")
    d.loc[mask,"apt_hh_ratio"]=d.loc[mask,"apt_hh_ratio"]/c["percent_divisor"]
    d["single_hh_estimated"]=d.single_hh_ratio.isna().astype(int); mask=d.single_hh_estimated.eq(1)
    log.add("01_SINGLE_NULL",MASTER,"1인가구 비율 결측",d,mask,"동일 구 정상값 중앙값; single_hh_estimated=1")
    d.loc[mask,"single_hh_ratio"]=d.gu.map(d.groupby("gu").single_hh_ratio.median())[mask]
    d["households_estimated"]=d.households_2026.isna().astype(int); mask=d.households_estimated.eq(1)
    avg_members=d.population_2026.div(d.households_2026).groupby(d.gu).mean()
    log.add("01_HOUSEHOLDS_NULL",MASTER,"세대수 결측",d,mask,"보정 인구÷동일 구 유효 동별(인구/세대) 산술평균; households_estimated=1; 반올림 안 함")
    d.loc[mask,"households_2026"]=d.population_2026.div(d.gu.map(avg_members))[mask]
    points=d.set_index("adm_cd2")[["lat","lon"]]

    s=data[SITES]
    mask=s.area_unit.eq("평"); s["area_converted"]=mask.astype(int)
    log.add("03_PYEONG",SITES,"면적 단위 평",s,mask,"area_value×3.3058, area_unit=㎡; area_converted=1")
    s.loc[mask,"area_value"]=s.loc[mask,"area_value"]*c["pyeong_to_m2"]
    s.loc[mask,"area_unit"]="㎡"
    s["area_m2"]=s.area_value
    mask=~s.gu.isin(d.gu)
    if set(s.loc[mask,"site_id"]) != set(c["excluded_site_ids"]):
        raise NeedsDecision("서울 밖 후보지가 승인 목록과 다릅니다.")
    log.add("03_OUTSIDE",SITES,"서울 밖",s,mask,"C069 제외",operation="REMOVE")
    s=s.loc[~mask].copy()
    mask=coordinate_bad(s,"lat","lon",cfg)
    if set(s.loc[mask,"site_id"]) != set(c["coordinate_replacement_site_ids"]):
        raise NeedsDecision("후보지 좌표 보정 대상이 승인 목록과 다릅니다.")
    s["coordinate_replaced"]=mask.astype(int)
    log.add("03_COORD",SITES,"위도·경도 이상",s,mask,"소재 행정동 대표점으로 대체; 13번 OD 값 보존")
    for col in ["lat","lon"]:
        s.loc[mask,col]=s.adm_cd2.map(points[col])[mask]
    # 임대료가 같거나 결측인 중복 매물은 임의로 선택하지 않는다.
    signature=["lat","lon","area_m2"]
    duplicate=s.duplicated(signature,keep=False)
    for _,group in s.loc[duplicate].groupby(signature):
        if group.monthly_rent_krw.isna().any() or group.monthly_rent_krw.duplicated().any():
            raise NeedsDecision("중복 매물의 월세가 결측 또는 동률입니다: "+str(group.site_id.tolist()))
    sorted_sites=s.sort_values("monthly_rent_krw",ascending=False)
    removed=sorted_sites.index[sorted_sites.duplicated(signature,keep="first")]
    mask=s.index.isin(removed)
    log.add("03_DUPLICATE",SITES,"좌표·면적 동일 중복 매물",s,mask,"월세가 높은 행 유지; 낮은 행 제외",operation="REMOVE")
    s=s.loc[~mask].copy()
    s["rent_estimated"]=s.monthly_rent_krw.isna().astype(int); mask=s.rent_estimated.eq(1)
    rent_per_m2=s.monthly_rent_krw.div(s.area_m2).groupby(s.gu).median()
    log.add("03_RENT_NULL",SITES,"월세 결측",s,mask,"중복·서울 밖 제외 후 동일 구 유효 ㎡당 월세 중앙값×면적; rent_estimated=1; 원 단위 계산, 반올림 안 함")
    s.loc[mask,"monthly_rent_krw"]=s.gu.map(rent_per_m2).mul(s.area_m2)[mask]
    if s.monthly_rent_krw.isna().any():
        raise NeedsDecision("월세를 추정할 동일 구 정상 표본이 없습니다.")
    s["hub_type"]=classify_area(s.area_m2,cfg)
    data[SITES]=s.reset_index(drop=True)

    o=data[ORDERS]
    mask=o.duplicated()
    log.add("05_DUPLICATE",ORDERS,"완전 중복",o,mask,"동일 원본 행의 첫 번째만 유지",operation="REMOVE")
    o=o.loc[~mask].copy()
    mask=shift_mask(o,cfg); o["timestamp_shifted"]=mask.astype(int)
    log.add("05_SHIFT_ROWS",ORDERS,"거점·일 전체 시각 -9h",o,mask,"모든 *_ts 컬럼에 +9시간; timestamp_shifted=1; 영업일 P2 2026-08-20")
    for col in c["timestamp_columns"]:
        t=pd.to_datetime(o[col],errors="raise")
        t.loc[mask]+=pd.Timedelta(hours=c["timezone_correction_hours"])
        o[col]=t.dt.strftime("%Y-%m-%d %H:%M:%S")
    mask=coordinate_bad(o,"cust_lat","cust_lon",cfg); o["coordinate_replaced"]=mask.astype(int)
    log.add("05_COORD",ORDERS,"고객 좌표 이상",o,mask,"고객 행정동 대표점으로 대체; coordinate_replaced=1")
    o.loc[mask,"cust_lat"]=o.adm_cd2.map(points.lat)[mask]
    o.loc[mask,"cust_lon"]=o.adm_cd2.map(points.lon)[mask]
    negative=o.weight_kg.lt(0); o["weight_negative"]=negative.astype(int)
    log.add("05_WEIGHT_NEGATIVE",ORDERS,"중량 음수",o,negative,"결측으로 변경한 뒤 동일 품목 수 중앙값 추정 대상에 포함")
    o.loc[negative,"weight_kg"]=np.nan
    missing=o.weight_kg.isna(); medians=o.weight_kg.groupby(o['items']).median()
    replacements=o['items'].map(medians)
    unresolved=missing & replacements.isna()
    if set(o.loc[unresolved,"order_id"]) != set(c["weight_unresolved_order_ids"]):
        raise NeedsDecision("품목 수별 중량 중앙값 미산출 대상이 승인 목록과 다릅니다.")
    estimated=missing & ~unresolved
    log.add("05_WEIGHT_IMPUTED",ORDERS,"중량 결측(음수 전환 포함)",o,estimated,"완전 중복 제거 후 유효 비음수 중량의 동일 품목 수 중앙값; weight_estimated=1")
    o.loc[estimated,"weight_kg"]=replacements[estimated]
    o["weight_estimated"]=estimated.astype(int); o["weight_unresolved"]=unresolved.astype(int)
    log.add("05_WEIGHT_MEDIAN_UNAVAILABLE",ORDERS,"사전 목록 밖에서 발견한 결함: 동일 품목 수 중량 표본 없음",o,unresolved,
            "사용자 승인 A안: 결측 유지, weight_unresolved=1; 외부 퀵이므로 수요 포함, 라이더 트립·중량 분석 제외",operation="FLAG",
            example="P1-260917-0285; items=181; 동일 품목 수 정상 표본 0; EXT-QUICK")
    delivery=o.status.eq("DELIVERED"); external=o.rider_id.eq(c["external_rider_id"])
    missing_delivery=delivery & o.delivered_ts.isna()
    log.add("05_DELIVERED_NULL",ORDERS,"DELIVERED 전달 시각 결측",o,missing_delivery,"수요 집계 포함; 전달 시각 원문 결측 유지; 배송시간 KPI 제외",operation="FLAG")
    o["delivered_time_missing"]=missing_delivery.astype(int)
    o["demand_included"]=delivery.astype(int)
    o["delivery_kpi_eligible"]=(delivery & ~missing_delivery).astype(int)
    o["rider_trip_eligible"]=(delivery & ~external & ~unresolved).astype(int)
    o["weight_analysis_eligible"]=(delivery & ~unresolved).astype(int)
    split={}
    for status in c["split_statuses"]:
        mask=o.status.eq(status)
        filename=f"05_pilot_orders_{status.lower()}.csv"
        log.add("05_"+status,ORDERS,"분석 본표에서 분리",o,mask,filename+"로 분리; 수요·배송시간 KPI 제외",operation="SPLIT")
        split[filename]=o.loc[mask].reset_index(drop=True)
    data[ORDERS]=o.loc[delivery].reset_index(drop=True)
    b=data[BENCH]; mismatch=benchmark_mismatch(b,cfg)
    b["time_mismatch"]=mismatch.astype(int)
    log.add("16_TIME_MISMATCH",BENCH,"사전 목록 밖에서 발견한 결함: 시각 표기 불일치",b,mismatch,
            "사용자 승인 A안: order_min을 계산 기준으로 사용; order_time 원문 보존; time_mismatch=1. 초가 60으로 반올림될 때 분 올림을 누락한 표기 오류로 판단",operation="FLAG",
            example="26행 모두 order_time의 초=00; order_min보다 약 1분(실측 0.99~1.00분) 이름. 예 BM1-0409: 16:01:00=961분, order_min=962.00. 사용자는 초 60 반올림 시 분 올림 누락으로 판단")
    return data,split


def save_csv(df,path,cfg):
    df.to_csv(path,index=False,encoding=cfg["io"]["encoding"])


def contract_failures(audit,cfg):
    found=audit.groupby("rule_id").n_rows.sum()
    return [{"rule_id":rule,"expected":count,"actual":int(found.get(rule,-1))}
            for rule,count in cfg["cleaning"]["expected_raw_counts"].items() if found.get(rule,-1)!=count]


def save_pending(unknown, output, cfg):
    save_csv(unknown,output/"clean_pending_issues.csv",cfg)
    rows=[]
    for r in unknown.itertuples():
        rows.append(dict(rule_id=r.rule_id,file=r.file,check="사전 목록 밖에서 발견한 결함: "+r.detail,
                         n_rows=r.residual_rows,example=r.example,
                         action="PENDING | 미처리; 선택지: 원값 유지+분석 제외 flag 또는 출처를 확인한 보정. 보정은 값·집계에 영향을 주므로 사용자 결정 필요"))
    if rows:
        save_csv(pd.DataFrame(rows),output/"data_issues_log.csv",cfg)


def run(cfg):
    output=ROOT/cfg["paths"]["outputs"]; output.mkdir(parents=True,exist_ok=True)
    raw_dir=ROOT/cfg["paths"]["raw"]; before_hash=hashes(raw_dir)
    raw=load_all(source="raw",allow_raw=True,config=cfg)
    geo=load_file("02_dong_boundary.geojson",source="raw",allow_raw=True,config=cfg)
    audit_raw=validate(raw,cfg,stage="raw",geo=geo)
    save_csv(audit_raw,output/"clean_validation_raw.csv",cfg)
    contracts=contract_failures(audit_raw,cfg)
    unknown=audit_raw[audit_raw.residual_rows.gt(0)]
    if len(unknown) or contracts:
        save_pending(unknown,output,cfg)
        (output/"clean_contract_failures.json").write_text(json.dumps(contracts,ensure_ascii=False,indent=2),encoding="utf-8")
        raise NeedsDecision("미승인 검사 결과 또는 검출 건수 불일치: "+json.dumps(contracts,ensure_ascii=False)+"; 상세 outputs/clean_pending_issues.csv")
    log=IssueLog()
    for row in audit_raw.itertuples():
        if row.n_rows and row.rule_id in cfg["cleaning"]["expected_raw_counts"]:
            log.rows.append(dict(rule_id="RAW_"+row.rule_id,file=row.file,check="원본 검출: "+row.detail,
                                 n_rows=row.n_rows,example=row.example,action="OBSERVE | 중복 제거 전 원본 검출 건수; 실제 처리 건수와 합산 금지"))
    data,split=clean_data(raw,cfg,log)
    audit_clean=validate(data,cfg,stage="clean",geo=geo,raw_site_ids=raw[SITES].site_id)
    # 분리본도 같은 검사에 통과해야 한다. 상태 분리 규칙만 목적에 맞게 승인한다.
    split_audits=[]
    for filename,frame in split.items():
        subset=dict(data); subset[ORDERS]=frame
        a=validate(subset,cfg,stage="clean",raw_site_ids=raw[SITES].site_id)
        a=a[a.file.eq(ORDERS)].copy(); a["file"]=filename
        expected="05_"+filename.removesuffix(".csv").rsplit("_",1)[1].upper()
        allowed=a.rule_id.eq(expected)
        a.loc[allowed,"accepted_rows"]=a.loc[allowed,"n_rows"]
        a.loc[allowed,"residual_rows"]=0
        split_audits.append(a)
    combined=pd.concat([audit_clean,*split_audits],ignore_index=True)
    save_csv(combined,output/"clean_validation_after.csv",cfg)
    if combined.residual_rows.sum():
        save_pending(combined[combined.residual_rows.gt(0)],output,cfg)
        raise NeedsDecision("정제 후 미승인 결함이 남았습니다. outputs/clean_pending_issues.csv 확인")
    issues=log.frame()
    rowcounts=[]
    for file,original in raw.items():
        before=sum(len(df) for df in tables(original).values())
        after=sum(len(df) for df in tables(data[file]).values())
        entries=issues[issues.file.eq(file)]
        removed=int(entries.loc[entries.action.str.startswith("REMOVE |"),"n_rows"].sum())
        separated=int(entries.loc[entries.action.str.startswith("SPLIT |"),"n_rows"].sum())
        balance=before-after-removed-separated
        rowcounts.append(dict(file=file,before_rows=before,after_rows=after,removed_rows=removed,split_rows=separated,balance=balance))
    counts=pd.DataFrame(rowcounts)
    assert counts.balance.eq(0).all(), "행 수와 로그 불일치"
    assert len(data[SITES])==cfg["cleaning"]["expected_candidate_count"], "후보지 수 불일치"
    assert data[SITES].hub_type.notna().all()
    # 수정 금지 테이블은 컬럼별 값 전체를 직접 대조한다.
    for file in raw:
        if file not in {MASTER,SITES,ORDERS,BENCH}:
            for section,df in tables(raw[file]).items():
                pd.testing.assert_frame_equal(df,tables(data[file])[section])
    pd.testing.assert_frame_equal(raw[BENCH],data[BENCH][raw[BENCH].columns])
    all_clean_orders=pd.concat([data[ORDERS],*split.values()],ignore_index=True).set_index("order_id")
    unique_raw=raw[ORDERS].drop_duplicates().set_index("order_id")
    assert set(all_clean_orders.index)==set(unique_raw.index)
    assert all_clean_orders.index.is_unique
    for name in ["basket_krw","delivery_fee_krw","rider_pay_alloc_krw","late_coupon_krw"]:
        pd.testing.assert_series_equal(all_clean_orders[name].sort_index(),unique_raw[name].sort_index(),check_names=False)
    pending_unresolved=all_clean_orders[all_clean_orders.weight_unresolved.eq(1)]
    assert set(pending_unresolved.index)==set(cfg["cleaning"]["weight_unresolved_order_ids"])
    assert pending_unresolved.weight_kg.isna().all()
    assert pending_unresolved.demand_included.eq(1).all()
    assert pending_unresolved[["rider_trip_eligible","weight_analysis_eligible"]].eq(0).all().all()
    # 저장·재읽기 후에도 같은 검사를 통과해야 정제 폴더로 발행한다.
    with tempfile.TemporaryDirectory(prefix="clean_stage_",dir=output) as folder:
        stage_dir=Path(folder)
        for file,value in data.items():
            if isinstance(value,dict):
                # 14번 [C]의 표 없는 설명행까지 바이트 단위로 보존한다.
                shutil.copyfile(raw_dir/file,stage_dir/file)
            else:
                save_csv(value,stage_dir/file,cfg)
        for file,df in split.items():
            save_csv(df,stage_dir/file,cfg)
        shutil.copyfile(raw_dir/"02_dong_boundary.geojson",stage_dir/"02_dong_boundary.geojson")
        read_cfg=copy.deepcopy(cfg); read_cfg["paths"]["clean"]=str(stage_dir)
        reread=load_all(config=read_cfg)
        persisted=validate(reread,cfg,stage="clean",geo=geo,raw_site_ids=raw[SITES].site_id)
        assert persisted.residual_rows.sum()==0
        for file in data:
            for section,frame in tables(data[file]).items():
                pd.testing.assert_frame_equal(frame.reset_index(drop=True),tables(reread[file])[section].reset_index(drop=True),check_dtype=False,check_categorical=False)
        for file,frame in split.items():
            pd.testing.assert_frame_equal(frame,load_file(file,config=read_cfg),check_dtype=False)
        clean_dir=ROOT/cfg["paths"]["clean"]; clean_dir.mkdir(parents=True,exist_ok=True)
        for staged in stage_dir.iterdir():
            staged.replace(clean_dir/staged.name)
    after_hash=hashes(raw_dir)
    assert after_hash==before_hash, "원본 SHA256 변경"
    save_csv(issues,output/"data_issues_log.csv",cfg)
    save_csv(counts,output/"clean_rowcount.csv",cfg)
    distribution=data[SITES].groupby("hub_type").size().reindex(cfg["cleaning"]["hub_area_labels"],fill_value=0).rename("n_sites").reset_index()
    save_csv(distribution,output/"clean_candidate_distribution.csv",cfg)
    metrics={"raw_csv_files":len(raw),"raw_data_files_unchanged":len(before_hash),
             "clean_csv_files":len(data)+len(split),"raw_residual_rows":int(audit_raw.residual_rows.sum()),
             "clean_residual_rows":int(combined.residual_rows.sum()),"persisted_residual_rows":int(persisted.residual_rows.sum()),
             "rowcount_balance":int(counts.balance.abs().sum()),"candidate_count":len(data[SITES]),
             "orders_raw":len(raw[ORDERS]),"orders_deduplicated":len(unique_raw),"orders_clean":len(data[ORDERS]),
             "weight_unresolved":int(data[ORDERS].weight_unresolved.sum()),
             "delivered_missing":int(data[ORDERS].delivered_time_missing.sum()),
             "delivery_kpi_orders":int(data[ORDERS].delivery_kpi_eligible.sum()),
             "benchmark_time_mismatch":int(data[BENCH].time_mismatch.sum()),
             "geo_dong_count":len(geo['features']),"active_od_rows":int(data["13_od_site_dong.csv"].site_id.isin(data[SITES].site_id).sum()),
             "demand_basket_sum_krw":float(data[ORDERS].basket_krw.sum())}
    metric_frame=pd.DataFrame(metrics.items(),columns=["metric","value"])
    save_csv(metric_frame,output/"clean_verification_summary.csv",cfg)
    save_csv(pd.DataFrame([{"file":f,"sha256_before":h,"sha256_after":after_hash[f],"unchanged":True} for f,h in before_hash.items()]),output/"clean_source_hashes.csv",cfg)
    from src.clean.report import write_reports
    write_reports(cfg)
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print(distribution.to_string(index=False))
    return metrics


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--config",default=str(ROOT/"config.json")); args=parser.parse_args()
    cfg=load_config(args.config)
    status_path=ROOT/cfg["paths"]["outputs"]/"clean_run_status.json"
    status_path.parent.mkdir(parents=True,exist_ok=True)
    def status(value,detail=""):
        status_path.write_text(json.dumps({"status":value,"detail":detail,
            "config_sha256":hashlib.sha256(Path(args.config).read_bytes()).hexdigest()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    status("running")
    try:
        metrics=run(cfg)
    except NeedsDecision as error:
        status("blocked",str(error))
        raise SystemExit("사용자 결정 필요: "+str(error)) from error
    except Exception as error:
        status("error",str(error))
        raise
    status("complete","검산 통과; 승인된 잔존 값은 flag로 관리; 미승인 잔여 "+str(metrics["clean_residual_rows"])+"건")


if __name__=="__main__":
    main()
