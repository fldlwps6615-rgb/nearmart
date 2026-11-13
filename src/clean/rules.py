"""범위·단위·결측·중복·좌표·시간대·상태값·관계 검사. 원본/정제본에 같은 함수 적용."""
import json

import numpy as np
import pandas as pd

from src.common.io import tables

CATEGORIES = ["범위", "단위", "결측", "중복", "좌표", "시간대", "상태값", "관계"]
ORDERS = "05_pilot_orders.csv"
SITES = "03_candidate_sites.csv"
MASTER = "01_dong_master.csv"
BENCH = "16_vrp_benchmark_orders.csv"


def flag(df, name):
    return df[name].eq(1) if name in df else pd.Series(False, index=df.index)


def coordinate_bad(df, lat, lon, cfg):
    bounds = cfg["cleaning"]["coordinate_bounds"]
    a, b = pd.to_numeric(df[lat], errors="coerce"), pd.to_numeric(df[lon], errors="coerce")
    return ~a.between(*bounds["lat"]) | ~b.between(*bounds["lon"]) | a.eq(b)


def order_days(df):
    return pd.to_datetime(df.order_id.str.split("-").str[1], format="%y%m%d", errors="coerce")


def shifted_groups(df, cfg):
    """주문 ID의 영업일과 주문 접수 시간대 전체가 +9h로 복구되는 거점·일 탐지."""
    c = cfg["cleaning"]
    t = pd.to_datetime(df.order_ts, errors="coerce")
    minute = t.dt.hour * 60 + t.dt.minute + t.dt.second / 60
    shifted = minute + c["timezone_correction_hours"] * 60
    valid_after = shifted.between(c["service_start_min"], c["service_end_min"])
    key = pd.DataFrame({"hub": df.hub_id, "day": order_days(df).dt.strftime("%Y-%m-%d"),
                        "before": minute < c["service_start_min"], "valid_after": valid_after})
    groups = key.groupby(["hub", "day"]).agg(before=("before", "any"), after=("valid_after", "all"))
    return [tuple(x) for x in groups.index[groups.before & groups.after]]


def shift_mask(df, cfg):
    groups = set(shifted_groups(df, cfg))
    return pd.Series([(h, d) in groups for h, d in zip(df.hub_id, order_days(df).dt.strftime("%Y-%m-%d"))], index=df.index)


def benchmark_mismatch(df, cfg):
    minutes = pd.to_timedelta(df.order_time, errors="coerce").dt.total_seconds() / 60
    return (minutes - df.order_min).abs() > cfg["cleaning"]["benchmark_time_tolerance_min"]


class Audit:
    def __init__(self, stage):
        self.stage = stage
        self.rows = []

    def add(self, file, section, category, rule, df, bad, detail, *, known=False, accepted=None):
        bad = pd.Series(bad, index=df.index).fillna(False).astype(bool)
        approved = pd.Series(False, index=df.index) if accepted is None else pd.Series(accepted, index=df.index).fillna(False).astype(bool)
        if self.stage == "raw" and known:
            approved = bad.copy()
        n, a = int(bad.sum()), int((bad & approved).sum())
        example_cols = [c for c in ["order_id", "site_id", "adm_cd2", "case_id", "date", "parameter", "year"] if c in df]
        if not example_cols:
            example_cols = list(df.columns[:3])
        example = df.loc[bad, example_cols].head(3).to_json(orient="records", force_ascii=False)
        self.rows.append(dict(stage=self.stage, file=file, section=section, category=category,
                              rule_id=rule, n_rows=n, accepted_rows=a, residual_rows=n-a,
                              status="checked", example=example, detail=detail))

    def count(self, file, section, category, rule, count, detail, *, known=False):
        frame = pd.DataFrame({"item": range(count)})
        self.add(file, section, category, rule, frame, [True]*count, detail, known=known)

    def ensure_categories(self, file, section):
        used = {r["category"] for r in self.rows if r["file"] == file and r["section"] == section}
        for category in CATEGORIES:
            if category not in used:
                self.rows.append(dict(stage=self.stage, file=file, section=section, category=category,
                                      rule_id="NOT_APPLICABLE", n_rows=0, accepted_rows=0, residual_rows=0,
                                      status="not_applicable", example="[]", detail="해당 구획에 이 검사 대상 컬럼이 없음"))

    def frame(self):
        return pd.DataFrame(self.rows)


def validate(data, cfg, *, stage, geo=None, raw_site_ids=None):
    audit = Audit(stage)
    c = cfg["cleaning"]
    master = data[MASTER].set_index("adm_cd2")
    valid_gu = set(master.gu)
    for file, value in data.items():
        if file not in cfg["files"]:
            continue
        expected_tables = cfg["files"][file]["tables"]
        actual_tables = tables(value)
        audit.count(file, "file", "관계", "SECTION_SCHEMA", len(set(expected_tables) ^ set(actual_tables)), "설정의 구획 목록과 실제 구획 일치")
        for section, df in actual_tables.items():
            if section not in expected_tables:
                continue
            schema = expected_tables[section]
            absent = set(schema["columns"]) - set(df)
            audit.count(file, section, "관계", "COLUMN_SCHEMA", len(absent), "필수 컬럼 누락: " + ",".join(sorted(absent)))
            if absent:
                continue
            def check(category, rule, bad, detail, **kw):
                audit.add(file, section, category, rule, df, bad, detail, **kw)

            for col in schema["columns"]:
                if col not in schema["optional"]:
                    check("결측", "REQUIRED_"+col, df[col].isna(), col+" 필수값")
            for col in schema["numeric"]:
                number = pd.to_numeric(df[col], errors="coerce")
                check("단위", "NUMERIC_"+col, df[col].notna() & (number.isna() | ~np.isfinite(number)), col+" 유한 숫자 형식")
            for col in cfg["io"]["string_columns"]:
                if col in df:
                    bad = df[col].notna() & ~df[col].map(lambda x: isinstance(x, str))
                    check("단위", "STRING_"+col, bad, col+" 문자열 보존")
                    if col in {"adm_cd2", "adm_cd"}:
                        digits = 10 if col == "adm_cd2" else 8
                        check("단위", "CODE_FORMAT_"+col, df[col].notna() & ~df[col].str.fullmatch(r"\d{"+str(digits)+"}", na=False), "행정동 코드 자릿수")
            for col, (low, high) in schema["ranges"].items():
                if (file, col) in {(MASTER, "apt_hh_ratio"), (ORDERS, "weight_kg")}:
                    continue  # 아래에서 승인된 교정 규칙으로 검사
                n = pd.to_numeric(df[col], errors="coerce")
                bad = n.notna() & ((n < low) | ((n > high) if high is not None else False))
                check("범위", "RANGE_"+col, bad, f"{col}: [{low}, {high}]")
            for col, allowed in schema["enums"].items():
                check("상태값", "ENUM_"+col, df[col].notna() & ~df[col].isin(allowed), col+" 허용값 "+str(allowed))
            check("중복", "05_DUPLICATE" if file == ORDERS else "EXACT_DUPLICATE",
                  df[schema["columns"]].duplicated(), "완전 중복의 두 번째 이후 행", known=file == ORDERS)
            # 완전 중복을 제외한 뒤에도 식별 키가 중복이면 별도 결함이다.
            dedup = df.drop_duplicates(subset=schema["columns"])
            audit.add(file, section, "중복", "KEY_DUPLICATE", dedup, dedup.duplicated(schema["keys"]), "복합 식별 키 중복")

            for lat, lon in [("lat", "lon"), ("cust_lat", "cust_lon"), ("hub_lat", "hub_lon"), ("from_lat", "from_lon"), ("to_lat", "to_lon")]:
                if lat not in df:
                    continue
                rule = "03_COORD" if file == SITES else "05_COORD" if file == ORDERS else "COORD_"+lat
                check("좌표", rule, coordinate_bad(df, lat, lon, cfg), "서울 점검용 경위도 범위 및 위도=경도 검사", known=file in {SITES, ORDERS})
            if "adm_cd2" in df:
                outside = ~df.adm_cd2.isin(master.index)
                approved = df.site_id.isin(c["excluded_site_ids"]) if file == SITES else None
                check("관계", "DONG_FOREIGN_KEY", outside, "01 행정동 코드 참조", accepted=approved if stage == "raw" else None)
                for col in ["gu", "dong"]:
                    if col in df:
                        check("관계", "DONG_MATCH_"+col, df.adm_cd2.isin(master.index) & df[col].ne(df.adm_cd2.map(master[col])), "행정동 코드와 구·동 이름 일치")
            if "gu" in df and "adm_cd2" not in df:
                check("관계", "GU_FOREIGN_KEY", ~df.gu.isin(valid_gu), "01 자치구 참조")
            for col in ["from_gu", "to_gu", "hub_gu"]:
                if col in df:
                    check("관계", "GU_FOREIGN_KEY_"+col, ~df[col].isin(valid_gu), "01 자치구 참조")

            if file == MASTER:
                check("결측", "01_SINGLE_NULL", df.single_hh_ratio.isna(), "1인가구 비율 결측", known=True)
                check("결측", "01_HOUSEHOLDS_NULL", df.households_2026.isna(), "세대수 결측", known=True)
                check("범위", "01_APT_PERCENT", df.apt_hh_ratio.gt(1), "아파트 비율 백분율 혼입", known=True)
                check("범위", "APT_NEGATIVE", df.apt_hh_ratio.lt(0), "아파트 비율 음수")
                check("범위", "01_POP_SCALE", df.population_2026.div(df.households_2026).gt(c["population_household_limit"]), "인구/세대 > 설정 상한", known=True)
                check("중복", "ADM_CD_UNIQUE", df.adm_cd.duplicated(), "8자리 행정동 코드 유일성")
            elif file == SITES:
                check("단위", "03_PYEONG", df.area_unit.eq("평"), "평→㎡ 환산 대상", known=True)
                area = df.area_value.where(df.area_unit.eq("㎡"), df.area_value*c["pyeong_to_m2"])
                check("범위", "AREA_CLASSIFIABLE", ~area.between(c["hub_area_bins"][0], c["hub_area_bins"][-1]), "환산 면적 150~1000㎡")
                check("관계", "03_OUTSIDE", ~df.gu.isin(valid_gu), "서울 25개 구 밖 후보지", known=True)
                signature = df.assign(_area=area)
                check("중복", "03_DUPLICATE", signature.duplicated(["lat", "lon", "_area"]), "좌표·환산 면적 동일 매물의 추가 행 수", known=True)
                check("결측", "03_RENT_NULL", df.monthly_rent_krw.isna(), "월세 결측", known=True)
                check("결측", "PILOT_LEASE_REQUIRED", df.status.eq("PILOT") & df.lease_start.isna(), "파일럿 임대차 시작월 필요")
                parsed = pd.to_datetime(df.lease_start, format="%Y-%m", errors="coerce")
                check("시간대", "LEASE_MONTH_FORMAT", df.lease_start.notna() & parsed.isna(), "임대차 시작월 YYYY-MM")
                if stage == "clean":
                    expected_type = classify_area(area, cfg)
                    check("단위", "AREA_M2_MATCH", ~np.isclose(df.area_m2, area), "area_m2와 환산 면적 일치")
                    check("상태값", "HUB_TYPE_MATCH", df.hub_type.ne(expected_type), "면적에 따른 S/M/L 일치")
            elif file == ORDERS:
                delivery = df.status.eq("DELIVERED")
                external = df.rider_id.eq(c["external_rider_id"])
                for status in c["split_statuses"]:
                    check("상태값", "05_"+status, df.status.eq(status), "별도 파일 분리 대상", known=True)
                check("범위", "05_WEIGHT_NEGATIVE", df.weight_kg.lt(0), "중량 음수→결측→품목 수별 중앙값", known=True)
                accepted_weight = (df.order_id.isin(c["weight_unresolved_order_ids"]) & external & flag(df,"weight_unresolved")
                                   & ~flag(df,"rider_trip_eligible") & ~flag(df,"weight_analysis_eligible") & flag(df,"demand_included"))
                check("결측", "05_WEIGHT_NULL", df.weight_kg.isna(), "중량 결측; 승인된 미해결 주문은 수요만 포함", known=True, accepted=accepted_weight)
                for col in ["pick_start_ts", "pick_end_ts", "depart_ts", "rider_id", "trip_seq", "rider_pay_alloc_krw"]:
                    check("결측", "DELIVERED_REQUIRED_"+col, delivery & df[col].isna(), "배송 주문 필수 실행 기록")
                check("결측", "INTERNAL_TRIP_REQUIRED", delivery & ~external & df.trip_id.isna(), "내부 라이더 트립 ID 필수; 외부 퀵 제외")
                check("결측", "05_DELIVERED_NULL", delivery & df.delivered_ts.isna(), "전달 시각 결측은 수요 포함·배송시간 KPI 제외", known=True,
                      accepted=flag(df,"delivered_time_missing") & flag(df,"demand_included") & ~flag(df,"delivery_kpi_eligible"))
                groups = shifted_groups(df, cfg)
                audit.count(file, section, "시간대", "05_SHIFT", len(groups), "전체 접수 시간대를 +9시간 복구할 수 있는 거점·일: "+str(groups), known=True)
                unexpected_groups = set(groups)-{tuple(g) for g in c["known_shift_groups"]}
                audit.count(file, section, "시간대", "UNKNOWN_SHIFT_GROUP", len(unexpected_groups), str(unexpected_groups))
                shifted = shift_mask(df,cfg) if stage == "raw" else pd.Series(False,index=df.index)
                parsed = {col:pd.to_datetime(df[col],errors="coerce") for col in c["timestamp_columns"]}
                for col,t in parsed.items():
                    check("시간대", "TIMESTAMP_FORMAT_"+col, df[col].notna() & t.isna(), "YYYY-MM-DD HH:MM:SS")
                for a,b in zip(c["timestamp_columns"],c["timestamp_columns"][1:]):
                    check("시간대", "TIME_SEQUENCE_"+a, parsed[a].gt(parsed[b]), a+" <= "+b)
                minute=parsed["order_ts"].dt.hour*60+parsed["order_ts"].dt.minute+parsed["order_ts"].dt.second/60
                check("시간대", "ORDER_SERVICE_HOURS", ~minute.between(c["service_start_min"],c["service_end_min"]), "09:00~23:30 접수; 승인한 +9h 그룹만 원본 예외", accepted=shifted)
                check("관계", "ORDER_ID_DATE", order_days(df).isna() | parsed["order_ts"].dt.normalize().ne(order_days(df)), "주문 ID 날짜와 접수일 일치")
                check("관계", "ORDER_HUB", ~df.hub_id.isin(data[SITES].site_id), "거점 ID 참조")
                check("관계", "ORDER_CALENDAR", ~order_days(df).dt.strftime("%Y-%m-%d").isin(data["06_pilot_calendar_weather.csv"].date), "달력의 영업일 존재")
                riders=data["05b_pilot_rider_log.csv"]
                keys=set(zip(riders.date,riders.hub_id,riders.rider_id))
                present=pd.Series([(d,h,r) in keys for d,h,r in zip(order_days(df).dt.strftime("%Y-%m-%d"),df.hub_id,df.rider_id)],index=df.index)
                check("관계", "RIDER_DAY_REFERENCE", delivery & ~external & ~present, "내부 라이더 거점·영업일 접속 기록 존재")
                if stage == "clean":
                    expected={"demand_included":delivery,"delivery_kpi_eligible":delivery & df.delivered_ts.notna(),
                              "rider_trip_eligible":delivery & ~external & ~flag(df,"weight_unresolved"),
                              "weight_analysis_eligible":delivery & ~flag(df,"weight_unresolved"),
                              "delivered_time_missing":delivery & df.delivered_ts.isna(),
                              "weight_unresolved":df.order_id.isin(c["weight_unresolved_order_ids"]) & df.weight_kg.isna()}
                    for name,mask in expected.items():
                        check("상태값", "FLAG_"+name, df[name].ne(mask.astype(int)), "승인된 분석 대상 flag 검산")
            elif file == "05b_pilot_rider_log.csv":
                start=pd.to_datetime(df.login_ts,errors="coerce"); end=pd.to_datetime(df.logoff_ts,errors="coerce")
                day=pd.to_datetime(df.date,errors="coerce")
                check("시간대", "RIDER_TIME", start.isna() | end.isna() | day.isna() | end.le(start), "접속·종료 시각 형식 및 순서")
                # date는 주문의 영업일이다. 자정 뒤 접속한 라이더도 전날 주문을 처리한다.
                orders=data[ORDERS].drop_duplicates().copy()
                orders["_day"]=order_days(orders).dt.strftime("%Y-%m-%d")
                orders["_depart"]=pd.to_datetime(orders.depart_ts,errors="coerce")
                orders["_delivered"]=pd.to_datetime(orders.delivered_ts,errors="coerce")
                shifted=shift_mask(orders,cfg) if stage=="raw" else pd.Series(False,index=orders.index)
                for col in ["_depart","_delivered"]:
                    orders.loc[shifted,col]+=pd.Timedelta(hours=c["timezone_correction_hours"])
                grouped=orders.groupby(["_day","hub_id","rider_id"]).agg(first_depart=("_depart","min"),last_delivered=("_delivered","max"))
                linked=df[["date","hub_id","rider_id"]].join(grouped,on=["date","hub_id","rider_id"])
                check("시간대", "RIDER_ORDER_WINDOW", start.lt(day) | start.gt(linked.first_depart) | end.lt(linked.last_delivered), "영업일 이후 접속 및 해당 주문의 출발·전달 시각을 접속 구간에 포함; 자정 이후 접속 허용")
                check("관계", "RIDER_HUB", ~df.hub_id.isin(data[SITES].site_id), "거점 ID 참조")
                check("관계", "RIDER_CALENDAR", ~df.date.isin(data["06_pilot_calendar_weather.csv"].date), "접속 영업일의 달력 존재")
            elif file == "06_pilot_calendar_weather.csv":
                day=pd.to_datetime(df.date,errors="coerce")
                check("시간대", "CALENDAR_DATE", day.isna(), "달력 날짜 파싱")
                weekday=day.dt.dayofweek.map(dict(enumerate(["월","화","수","목","금","토","일"])))
                check("시간대", "WEEKDAY_MATCH", df.weekday.ne(weekday), "날짜-요일 일치")
                check("관계", "CALENDAR_CONTIGUOUS", day.diff().dt.days.fillna(1).ne(1), "달력 연속 일자")
            elif file == "09_competitor_hubs.csv":
                check("시간대", "COMPETITOR_YEARS", df.close_year.notna() & df.close_year.lt(df.open_year), "폐점연도>=개점연도; 폐점 2건은 정상")
            elif file == "10_market_rules.csv":
                optional_value=df.rule.isin(["assortment_multiplier","service_hours"])
                check("결측", "RULE_VALUE", df.value.isna() & ~optional_value, "다른 표 참조 또는 영업시간 설명 외 수치 필수")
                check("결측", "RULE_UNIT", df.unit.isna() & ~df.rule.eq("service_hours"), "영업시간 설명 외 단위 필수")
                check("단위", "RULE_UNIT_ENUM", df.unit.notna() & ~df.unit.isin(["배수","계수","원/건","원"]), "시장규칙 단위")
            elif file == "12_speed_profile.csv" and "hour" in df:
                check("시간대", "HOUR_INTEGER", df.hour.mod(1).ne(0) | df.hour.ge(24), "정수 0~23시")
                audit.count(file,section,"시간대","HOURS_COMPLETE",len(set(range(24))^set(df.hour)),"24개 시간대 완비")
            elif file == "14_rider_supply.csv" and "hour_from" in df:
                check("시간대", "SUPPLY_INTERVAL", df.hour_from.ge(df.hour_to), "공급 시간 구간 시작<끝")
                for area,g in df.groupby("area_class"):
                    ordered=g.sort_values("hour_from")
                    gaps=ordered.hour_from.iloc[1:].to_numpy()!=ordered.hour_to.iloc[:-1].to_numpy()
                    audit.count(file,section,"시간대","SUPPLY_GAP_"+area,int(gaps.sum()),"권역 내 인접 공급 구간의 공백/겹침")
            elif file == "13_od_site_dong.csv":
                valid_sites=set(raw_site_ids if raw_site_ids is not None else data[SITES].site_id)
                check("관계", "OD_SITE_REFERENCE", ~df.site_id.isin(valid_sites), "원본 후보지 ID 참조; 제외 후보지 OD는 보존")
                check("관계", "OD_ROAD_DISTANCE", df.road_km.lt(df.straight_km), "도로거리>=직선거리")
                counts=df.groupby("site_id").adm_cd2.nunique()
                audit.count(file,section,"관계","OD_DONG_COVERAGE",int(counts.ne(len(master)).sum()),"후보지별 모든 행정동 OD 존재")
                active=set(data[SITES].loc[~data[SITES].site_id.isin(c["excluded_site_ids"]),"site_id"])
                audit.count(file,section,"관계","ACTIVE_SITE_OD",len(active-set(df.site_id)),"서울 후보지 OD 존재")
            elif file == "15_cost_parameters.csv":
                numeric=pd.to_numeric(df.value,errors="coerce")
                text=df.parameter.isin(["horizon","cash_timing"])
                check("단위", "COST_NUMERIC", ~text & (numeric.isna() | ~np.isfinite(numeric)), "기간·현금시점 외 파라미터는 숫자")
                check("단위", "COST_UNIT_REQUIRED", ~text & df.unit.isna(), "수치 파라미터 단위 필수")
                check("범위", "COST_NONNEGATIVE", ~text & numeric.lt(0), "비용 파라미터 음수 금지")
                check("범위", "COST_RATIO", df.unit.isin(["비율","연율"]) & ~numeric.between(0,1), "비율/연율은 0~1")
            elif file == BENCH:
                mismatch=benchmark_mismatch(df,cfg)
                check("시간대", "16_TIME_MISMATCH", mismatch, "사전 목록 밖에서 발견한 결함: 원문 보존, 계산은 order_min", known=True, accepted=flag(df,"time_mismatch"))
                seconds=pd.to_timedelta(df.order_time,errors="coerce").dt.total_seconds()
                check("시간대", "BENCH_TIME_FORMAT", seconds.isna(), "HH:MM:SS 표기")
                check("시간대", "BENCH_SERVICE_HOURS", ~df.order_min.between(c["service_start_min"],c["service_end_min"]), "계산용 order_min의 접수 시간대")
                check("관계", "BENCH_INSTANCE", ~df.instance.isin(data["16b_vrp_benchmark_meta.csv"].instance), "벤치마크 메타 참조")
                if stage=="clean":
                    check("상태값", "BENCH_MISMATCH_FLAG", df.time_mismatch.ne(mismatch.astype(int)), "time_mismatch 실제 불일치와 일치")
            elif file == "16b_vrp_benchmark_meta.csv":
                counts=data[BENCH].groupby("instance").size()
                check("관계", "BENCH_COUNT", df.n_orders.ne(df.instance.map(counts)), "인스턴스별 주문 수 일치")
                check("관계", "BENCH_HUB", ~df.hub_site_id.isin(data[SITES].site_id), "벤치마크 거점 존재")
            elif file == "17_travel_time_check.csv":
                check("결측", "CROSS_BRIDGE_REQUIRED", df.cross_river.eq("Y") & df.bridge_used.isna(), "한강 횡단 교량 필수")
                allowed=data["11_bridges.csv"].query("motorcycle_allowed == True").bridge
                check("관계", "CROSS_BRIDGE_ALLOWED", df.bridge_used.notna() & ~df.bridge_used.isin(allowed), "이륜차 허용 교량 참조")
                check("시간대", "DEPART_HOUR", ~df.depart_hour.between(0,24,inclusive="left"), "출발 시간 0<=h<24")
            # 연도 컬럼은 시간 축 검사도 수행한다.
            for col in ["year","open_year","close_year","available_from_year"]:
                if col in df:
                    check("시간대", "YEAR_INTEGER_"+col, df[col].notna() & df[col].mod(1).ne(0), "연도 정수")
            audit.ensure_categories(file,section)
    if geo is not None:
        props=pd.DataFrame([f["properties"] for f in geo["features"]])
        audit.add("02_dong_boundary.geojson","features","중복","GEO_DUPLICATE",props,props.adm_cd2.duplicated(),"GeoJSON 동 코드 유일성")
        audit.count("02_dong_boundary.geojson","features","관계","GEO_CODE_SET",len(set(props.adm_cd2)^set(master.index)),"GeoJSON/01 adm_cd2 집합 일치; 지도 형상 검사는 요청 범위 밖")
        for col in ["adm_cd","gu","dong"]:
            audit.add("02_dong_boundary.geojson","features","관계","GEO_MATCH_"+col,props,props[col].ne(props.adm_cd2.map(master[col])),"GeoJSON/01 코드·이름 매핑 일치")
    return audit.frame()


def classify_area(area, cfg):
    c=cfg["cleaning"]
    # 330은 M, 660은 L; 최종 상한 1000 포함. 경계 정책은 config와 문서에 명시.
    result=pd.cut(area,bins=c["hub_area_bins"],labels=c["hub_area_labels"],right=False).astype("string")
    result.loc[area.eq(c["hub_area_bins"][-1])]=c["hub_area_labels"][-1]
    return result
