# 단계 간 파일 약속 (초안)

T1~T4 제출 양식(submission_templates/)을 찾으면 그 컬럼에 맞춰 이 문서를 먼저 고친다.
adm_cd2·site_id는 항상 문자열. 금액은 원, 시간은 분, 비율은 0~1.

| 단계 | 파일 | 컬럼 | 비고 |
|---|---|---|---|
| ① | outputs/clean/<원본 파일명>.csv | 원본 컬럼 + 아래 명시한 파생·flag 컬럼 | 원본은 data/에 그대로; 07·12·14 구획·설명행 보존 |
| ① | outputs/data_issues_log.csv | rule_id, file, check, n_rows, example, action | 제출 필수 |
| ① | outputs/clean_rowcount.csv | file,before_rows,after_rows,removed_rows,split_rows,balance | balance=전−후−삭제−분리, 전 파일 0이어야 함 |
| ① | outputs/clean/05_pilot_orders_test.csv / 05_pilot_orders_cancelled.csv | 정제 주문과 동일 컬럼 | TEST·CANCELLED 분리본; 수요·배송 KPI 제외 |
| ① | outputs/clean/02_dong_boundary.geojson | 원본 GeoJSON 그대로 | 01과 행정동 코드 대조 후 복사 |
| ① | outputs/clean_validation_raw.csv / clean_validation_after.csv | stage,file,section,category,rule_id,n_rows,accepted_rows,residual_rows,status,example,detail | 8종 검사; 승인된 잔존 값과 미승인 잔여 분리; 적용 불가 항목 명시 |
| ① | outputs/clean_candidate_distribution.csv | hub_type,n_sites | 정제 후보지 유형 분포 |
| ① | outputs/clean_verification_summary.csv | metric,value | 검산 요약 |
| ① | outputs/clean_source_hashes.csv | file,sha256_before,sha256_after,unchanged | data/ 전체 파일 보존 검산 |
| 공용 | src/common/io.py, config.json | load_file,load_all,active_od,demand_orders,delivery_kpi_orders,rider_trip_orders,weight_analysis_orders | 기본 입력 outputs/clean/; raw는 ①에서만 명시 허용 |
| ① | outputs/pilot_kpi.csv | hub_id,metric,segment,value | 파일럿 운영 KPI. ALL은 주문 합산 또는 거점·일 합산 지표 |
| ① | outputs/pilot_expectation_check.csv | hub_id,metric,segment,expected,actual,actual_minus_expected,pooled_actual,interpretation | 기대값 8개와 비교 |
| ① | figures/pilot_*.png | 일별 주문, 날씨별 배송, 시간대별 주문·묶음, 트립 분포, 라이더 시급, 거리별 수요, 지연 집중 | 맑은 고딕 |
| ① | outputs/pilot_diagnosis.md | "무엇이 __ 때문에 __하다(근거: 지표=값)" 문장 5개와 수치 출처 | 파일럿 운영 진단. 정제 단계의 품질 진단은 clean_verification.md에 포함 |
| 공용 | src/travel_time.py | TravelTime.minutes(...), road(...), speed(...) | 17번 60건 통과 |
| ② | outputs/demand_effects.csv | effect, level, multiplier | 램프업 곡선 모수 포함 |
| ② | outputs/base_rate_dong.csv | adm_cd2, rate_low, rate_mid, rate_high, extrapolated | 세대당 하루 성숙 기준 주문, 예측구간과 램프업 상대 불확실성 포함, 외삽 0/1 |
| ② | outputs/demand_dong_year.csv | scenario, year, adm_cd2, orders_low, orders_mid, orders_high | 하루 잠재 수요. 경쟁 반영, 배송시간·구색·개점 후 램프업 미적용. 2026 기준 및 2027~2046 전망 |
| ② | outputs/hour_profile.csv | day_type, area_type, hour, share | weekday/weekend × single_low/single_high, 각 그룹 09~23시 합계 1 |
| ② | outputs/order_profile.csv | day_type, area_type, n_orders, avg_basket_krw, avg_basket_eok, under_40000_share, chilled_share, frozen_share, cold_order_share, apt_share, external_quick_share, avg_items | ⑥. ALL/ALL은 전체 관측 주문, 그 외 4행은 시간대 패턴과 같은 그룹. 냉장 또는 냉동은 합집합 |
| ③ | outputs/network_plan_<대안>.csv | site_id,hub_type,open_year,close_year,relocated_from,note | T1 형식(튜토리얼 51쪽) |
| ③ | outputs/assignment_<대안>.csv | year,adm_cd2,site_id,expected_orders_per_day,avg_delivery_min,late_rate | T2 형식, 미서비스 site_id는 빈칸, 연도 2027·2031·2036·2041·2046 |
| ③ | outputs/alt_compare.csv | alternative,search,pilot_branch,selected,scenario,T,npv,max_funding,payback_year,discounted_payback_years,ebitda_positive_year,hubs_<연도>,coverage_<연도>,orders_<연도>,lost_orders_total,lost_revenue_krw,npv_low,npv_high,max_funding_low,max_funding_high 및 억 원·한도 플래그 | 두 탐색 결과 모두 기록. 개점 수 2027/2032/2037/2042, 커버리지 2027/2037, 주문 2027/2031/2036/2046 |
| ③ | outputs/network_hub_demand_<대안>.csv | year,site_id,hub_type,assigned_dongs,ramp_factor,requested_per_day,accepted_per_day,capacity_per_day,requested_capacity_ratio,accepted_capacity_ratio,lost_per_day,max_peak_min,avg_delivery_min,rider_cost_2027 | 제한 전/후 수요와 상한·거리 검산 |
| ③ | outputs/network_year_<대안>_<시나리오>.csv | year,active_hubs,households_served,households_total,coverage,requested_per_day,orders_per_day,lost_orders_per_day,lost_orders_annual,lost_product_revenue_krw,lost_delivery_revenue_krw,lost_revenue_krw | 해당 연도 명목 매출 손실. 상품과 배달 매출을 포함 |
| ③ D | outputs/network_coverage_D.csv | year,T,active_hubs,households_served,households_total,coverage,orders_per_day | 2027·2032·2037년 base·중앙 수요 |
| ③ D | outputs/investor_condition_cost.csv | scenario,demand_band,npv_C_krw,npv_D_krw,investor_condition_cost_krw,npv_C_eok,npv_D_eok,investor_condition_cost_eok | 시장 3개 × 주문율 3개에서 C−D |
| ④ | report/T3_benchmark_dispatch.csv | instance,order_id,rider_id,rider_type,trip_id,trip_seq,pick_start_min,pick_end_min,depart_min,delivered_min | 인스턴스별 선택안, T3 형식·UTF-8 BOM; 정책별 원본은 outputs/dispatch/<instance>/<policy>/T3.csv |
| ④ | outputs/bm_kpi.csv | instance,policy,on_time,avg_min,p90_min,orders_per_trip,cost_per_order,rider_hourly,workload_cv + orders,late_orders,supply_ok,rule_violations,feasible 및 비용 성분·근무시간 | 2027년 비용, 수급 17,860원; 비용 분모 전체 주문 |
| ④ | outputs/bm_staffing.csv | instance,policy,hour,pickers,contract_riders,employed_riders | 자정 후 hour는 24 이상, 접속시간은 시간별 인원 합 |
| ④ | outputs/dispatch/<instance>/<policy>/{work,picking,bays,trips}.csv | work: worker_id,role,start_min,end_min; picking: order_index,order_id,picker_id,pick_start_min,pick_end_min; bays: rider_id,start_min,end_min; trips: 트립 출발·복귀·거리·지급액 | 독립 검증 보조 기록, 시간구간은 [시작,끝) |
| ④ | outputs/dispatch_verification.csv | instance,policy,group,check,status,violations,detail | a·b·c·e·f·g·h는 T3 기반, d·i·수급은 보조 기록 기반. e·g의 완전 검증 한계 별도 표기 |
| ⑤ 재현 | outputs/sim_replay_check.csv | hub_id,metric,actual_days,simulation_runs,contract_source,actual_roster_runs,fallback_roster_runs,actual_mean,actual_p05,actual_p95,simulated_mean,simulated_p05,simulated_p95,simulated_minus_actual,actual_matched_mean,simulated_minus_actual_matched,reason 및 제약·시급 억 원 환산 | 맑은 월~금, 일 단위 통계 |
| ⑤ 재현 | outputs/sim_replay_{observed_days,repetitions,audit}.csv | hub_id + date 또는 repetition 및 일별 KPI, audit은 거점별 검산·제약·실행시간 | 실측과 재현의 집계 원본 |
| ⑤ | outputs/sim_capacity.csv | hub_type, rider_class, day_type, radius_min, max_orders_per_day, max_orders_per_day_raw 및 탐색 범위·기준 | 보정 후 정시율 기준 최대 실험 주문 수가 기본 |
| ⑤ | outputs/sim_cost_curve.csv | hub_type, rider_class, radius_min, orders_per_day, day_type, rider_cost_per_order, picker_cost_per_order, employed_share, contract_hourly_income, avg_delivery_min, p90_delivery_min, ontime_rate, avg_delivery_min_corrected, p90_delivery_min_corrected, ontime_rate_corrected 및 일별 분위·위탁/직고용 분해·제약 | 접미사 없는 배송시간·정시율은 보정 전, 비용은 2027년 원/건 |
| ⑤ | outputs/sim_late_rate.csv | radius_min, travel_min_bin, day_type, late_rate, avg_delivery_min, late_rate_corrected, avg_delivery_min_corrected 및 일별 분위·주문 수 | T2용 맑음 18시 기준 거점→고객 결정론적 편도시간 구간, 외부 퀵 포함 |
| ⑤ | outputs/sim_experiment_{repetitions,plans,recovery,verification,runtime}.csv, outputs/sim_late_rate_repetitions.csv, outputs/sim_{trend,expectation}_check.csv | 조합별 일 지표·계획 JSON, 잔여 배송 근무, 재읽기 검산, 전체 실행시간, 지연 구간 집계의 일별 근거, 방향성과 기대값 비교 | 반복 분포 그림은 figures/sim_experiment_boxplots.png |
| ⑤ 일관성 검사 | outputs/sim_bm2_consistency_{check,repetitions,staffing_search,workload}.csv | 5개 기준의 판정·평균·5/95분위·최소/최대, 반복별 배송·미배송·KPI, 평가 인력안, 시간대별 기대 피킹 부하·트립·시작 인원 | BM2 주말 맑음 조건, 총 운영비는 전량 배송한 회차 기준 |
| ⑤ 벤치마크 반경 | outputs/radius_bm_check.csv | instance, day_type, radius_min, radius_km, orders, ontime_det, ontime_stoch_mean, ontime_stoch_p05, ontime_stoch_corrected, undelivered, cost_per_order, rider_cost_per_order, picker_cost_per_order, employed_share, contract_hourly, orders_per_trip, bay_max, supply_ok, feasible | ④ 원위치 3행 및 위치 교체 8행, 08이 rider_cost_per_order를 사용 |
| ⑤ 벤치마크 반경 v2 | outputs/service_radius_basis.csv | instance, day_type, radius_type, radius_min, radius_km, version, tested_order_levels, passing_order_levels, ontime_target, feasible, max_orders_per_day, order_level, rider_cost_per_order, picker_cost_per_order, ontime_stoch_corrected_min, validation_repetitions | 08의 반경·대표일별 최대 검증 주문량과 원/건 비용 |
| ⑤ 벤치마크 반경 v2 | outputs/radius_bm_v2_{candidates,plans,repetitions,runtime}.csv | 후보의 5회 판정·평균비용, 선정 인력 계획, 선정안의 탐색·독립 검증 반복, 완료·시간 기록 | python -B -m src.sim.radius_bm_v2 --config config.json |
| ⑤ 벤치마크 반경 | outputs/radius_bm_{repetitions,plans,verification,runtime}.csv | 반복별 주문 보존·정시율·운영 제약, 결정론적 P2 계획과 탐색 종료 기록, 기준·비용 방향 검산, 실행시간 | 재실행 python -B -m src.sim.radius_bm --config config.json |
| ⑥ | outputs/finance_<대안>_<시나리오>.csv | year, revenue, ebitda, capex, cash_flow, cum_cash_flow, discounted_cf + 아래 상세 컬럼 | 원 단위; 2026년 말 초기시점 포함 21행, 핵심 금액의 _eok 병기 |
| 08 | outputs/feedback_compare.csv | alternative,phase,design,T,radius_evidence, 2027·2032·2037·2042 거점 수, 2027·2037 커버리지, 시장별 중앙 수요 NPV, 저성장 두 주문율의 최대 자금, 기본·중앙 EBITDA 흑자 연도와 2027 운영 KPI | 전후 10행. C·D의 기존 T=20은 `근거 없음` |
| 08 | outputs/alt_compare_v2.csv, outputs/network_plan_<대안>_v2.csv, outputs/investor_condition_cost_v2.csv | 기존 ③ 양식. 대안 A·B·B2·C15·D15 | 투자자 조건 비용 열의 C·D는 C15·D15 |
| 08 | outputs/feedback_basis_v2.csv, outputs/feedback_bm1_audit_v2.csv, outputs/network_search_trace_v2.csv, outputs/network_verification_v2.csv, outputs/data_issues_log_v2.csv | 운영 입력과 05 대조, BM1 886건 대조, 재탐색 채택 변경, 검산, 대표일 대체 기록 | 12·15분 입력, 근거·적용 내역 추적용 |
| ⑦ | outputs/scenario_compare.csv | alternative, scenario, npv, max_funding, payback_year, ebitda_positive_year, coverage, on_time | 대안×시나리오 비교표 |
| ⑦ | report/T4_kpi_summary.csv | kpi,value | T4 양식, 최종안 기준 KPI 26개 |

## ① 정제 컬럼과 이용 규약 (2026-11-13)

- 01: `population_scaled`, `apt_ratio_scaled`, `single_hh_estimated`, `households_estimated` (0/1).
- 03: `area_value`는 ㎡로 정규화하고 `area_unit=㎡`; `area_m2`는 그 값, `hub_type`은 S/M/L. `area_converted`, `coordinate_replaced`, `rent_estimated` (0/1). 구간은 S=[150,330), M=[330,660), L=[660,1000].
- 05 본표는 DELIVERED만 포함. `timestamp_shifted`, `coordinate_replaced`, `weight_negative`, `weight_estimated`, `weight_unresolved`, `delivered_time_missing` (0/1).
- 05 분석 대상은 `demand_included`, `delivery_kpi_eligible`, `rider_trip_eligible`, `weight_analysis_eligible` (0/1)로 선택한다. 모든 외부 퀵은 내부 라이더 트립에서 제외한다. 미해결 중량 주문은 수요에 포함하고 라이더 트립·중량 분석에서 제외한다. 전달 시각 결측은 수요 포함·배송시간 KPI 제외.
- 16: `time_mismatch` (0/1) 추가. **계산 기준은 `order_min`**, `order_time`은 원문 표시값으로 보존한다.
- 13: 원본 OD 전체와 값 유지. 제외 후보지 C025·C056·C069를 활성 후보로 되살리지 않도록 `active_od()` 또는 정제 03의 `site_id`와 내부 조인을 사용한다. C069 OD는 원래 없으며, C040의 OD도 재계산하지 않는다.
- `data_issues_log.csv`의 `OBSERVE`는 중복 제거 전 검출 건수, `UPDATE/FLAG`는 실제 수정·표시 건수, `REMOVE/SPLIT`만 행 수 감소 항목이다. 서로 겹치는 규칙의 건수를 전체 결함 행 수로 합산하지 않는다. 원본 전달 시각 결측은 정확히 778행이며 중복 제거 후 777행이다.
- `clean_validation_after.csv`의 `residual_rows=0`은 승인한 예외 처리까지 적용한 미해결 규칙 위반 0건을 뜻한다. 전달 시각 777건, 중량 1건의 원문 결측 및 벤치마크 표기 차이 26건을 삭제·추정했다는 뜻이 아니다.
- 재실행: `python -m src.clean.run --config config.json`. `outputs/clean_run_status.json`의 `complete` 확인 후 정제본을 사용한다. 중단 시 기존 정제본을 새 실행 결과로 간주하지 않는다.

## ① 파일럿 운영 진단 (2026-11-13)

- 재실행: `python -m src.diagnosis.run --config config.json`. 입력은 정제된 05·05b·06·01·13이다.
- `daily_orders`의 segment는 날짜, 개점 후 경과일, 요일, 날씨, 프로모션을 `|`로 연결한다. 개점일은 0일이다. `date=YYYY-MM-DD`에는 거점·일별 라이더 지표가 들어간다.
- `all`, `weather=...`, `order_hour=HH`, `weather=...|order_hour=HH`에서 배송 대상 건수, 60분 초과 건수, 정시율, 평균, 90분위, 네 구성요소 평균을 조회한다. 배송 대상은 외부 퀵을 제외한 전달시각 관측 주문이다.
- `demand_order_share`는 수요 본표 내 비중, `kpi_order_share`는 배송 KPI 분모 내 비중, `late_order_share`는 60분 초과 주문 내 비중이다. 정의되지 않은 비율과 평균은 빈 셀로 둔다.
- `trip_size=N`의 `trip_count`와 `trip_share`는 트립 수와 비중이다. `depart_hour=HH` 및 `peak/off_peak`의 묶음 지표는 출발 시각으로 분류한다. `orders_per_operating_day`는 접수 시각의 정시 구간별 건수를 영업일 수로 나눈 값이다. 23시 구간은 접수 마감까지 30분을 포함한다.
- 일별 `rider_earnings_krw_per_hour`는 05b 지급액 합 ÷ 접속시간 합이다. `all`에서 같은 지표는 일별 시급 단순평균이고 `rider_earnings_pooled_krw_per_hour`는 기간 합계 기준이다. `rider_workload_cv`는 일별로 라이더 배송 건수의 모집단 표준편차 ÷ 평균이며, `all`에서는 라이더별 기간 총 배송 건수로 계산한다.
- `road_km=A–B`는 A 이상 B 미만의 13번 도로거리 구간이다. 문제지 서비스 반경 안의 동 세대수 합을 분모로 쓴다. `orders_per_household`는 기간 전체이고 `daily_orders_per_household`는 영업일당이다.

## ② 수요예측 (2026-11-13)

- 재실행 `python -m src.demand.run --config config.json`. `outputs/demand_run_status.json`이 `complete`인 결과를 사용한다. 시나리오·τ 후보·`rain_day_share`·`w2`는 `config.json`의 `demand`에 둔다.
- `scenario`의 low/base/high는 07[B] 시장지수다. `orders_low/mid/high`는 각 시장 시나리오 안의 주문율·성숙 수준 불확실성이다. ③에서 개점 연차에 맞는 램프업 수준을 적용한다.
- `demand_effects.csv`의 `ramp`는 중앙 `a`, `tau_days`, `level_day90`를 담는다. `mature_div_last_week`, `mature_div_day90`의 low/mid/high는 다른 분모를 쓰는 성숙 환산 배수다. `weather`의 clear/rain/heavy_rain은 ⑤ 대표일에 사용한다. `weekday`의 level은 월요일 0~일요일 6이다.
- `demand_validation.md`에서 후보별 RMSE, 기대값 비교, 권역·기간 검산, 램프업 민감도, 세 계수 역산, 연도별 시장지수 효과를 확인한다. 연도 합계의 CSV 재읽기 검산은 시나리오·연도별 각 열 최대 상대오차 1e-9 이하를 적용한다.

## ⑥ 20년 재무 엔진 계약 (2026-11-13)

- 함수: `src.finance.evaluate(plan, ops, scenario)` 또는 재사용 가능한 `FinanceEngine(config).evaluate(plan, ops, scenario)` → `(annual: pandas.DataFrame, kpi: dict)`.
- `plan`은 DataFrame 또는 레코드 목록. 필수 컬럼 `site_id, hub_type, open_year, close_year, relocated_from`. T1의 `note` 등 추가 컬럼은 허용한다. `site_id` 및 비어 있지 않은 `relocated_from`은 문자열이고 후보지별 한 행이다. 동일 장소 재개점은 지원하지 않으며 중복을 거부한다.
- 기본 `close_year`는 **첫 미영업 연도(그해 초 철수)**. 예: 2027 개점·2037 폐점은 2027~2036 영업, 정산은 2036년 말. 빈 값은 2046년 말 종료(내부 2047). `finance.close_year_convention=last_operating_year`를 고르면 마지막 영업연도 기준으로 변환한다. P1·P2가 plan에 없으면 2027년 초 철수한다.
- `ops` 필수 컬럼: `year, site_id, orders_per_day, avg_basket_krw, under_40000_share, cold_order_share, contract_rider_cost_per_order_krw, employed_rider_cost_per_order_krw, picker_cost_per_order_krw, late_rate, external_quick_share`.
- 입력 금액은 **모든 연도에 대해 2027년 가격**. 장바구니는 엔진이 2%, 위탁은 2.5%, 직고용·피커는 3%를 연도에 맞게 한 번 적용한다. 비용 상승률은 모두 15를 읽는다. 직고용 인건비·인센티브·차량비를 포함한 입력 직고용 비용 전체에 3%를 적용하는 계약이다.
- 주문당 위탁·직고용·피커 비용의 분모는 각각 담당한 주문 수가 아닌 **전체 배송 주문 수**다. ②③⑤는 각 비용 총액/전체 주문으로 변환하여 전달한다. 외부 퀵 비용은 이 세 비용에서 제외하며 엔진이 외부 퀵 비중×15의 단가로 별도 가산한다. 동일 주문에 실제 지급한 비용은 각 비용 성분에 포함한다.
- 모든 비중은 전체 주문 대비 0~1. `cold_order_share`는 냉장 **또는** 냉동이 포함된 주문의 합집합 비중이며 두 비중의 합이 아니다. `under_40000_share`는 해당 연도 실제 명목 장바구니가 4만 원 미만인 비중으로 상위 단계가 제공한다. 고정 검산에서는 비중도 고정한다. 엔진은 평균 장바구니만으로 분포를 추정하지 않는다.
- `ops`는 실제 영업하는 거점×연도마다 정확히 한 행이어야 한다. 주문 0건도 0과 나머지 필수값을 명시한다. 결측·음수·중복·비율 범위 오류·미영업 행은 기록 후 거부한다. `scenario` 열은 선택이며 있으면 low/base/high 중 요청 시나리오만 선택한 뒤 완전성을 검사한다.
- 기본 `demand_basis=scenario_applied`: ②③⑤가 반영한 시나리오 주문을 그대로 쓴다. `index_2026`일 때만 입력 주문에 07[B]의 해당 연도/시나리오 지수를 곱한다. 기본 시나리오는 `finance.default_scenario`로 정하며 다른 단계의 `analysis.scenario`는 수정하지 않는다.
- `annual`의 `year`는 **2026~2046**. 2026은 운영연도가 아니라 초기 투자·파일럿 철수 행이다. 핵심 7개 컬럼 이름을 유지하고 원 단위 상세 내역 및 `revenue_eok, ebitda_eok, capex_eok, cash_flow_eok, cum_cash_flow_eok, discounted_cf_eok`를 추가한다. 비용·지급·회수 열은 각각 양수, `cash_flow`는 순유입 기준 부호다. `inventory_investment`는 감소하면 음수다.
- 상세 손익: `product_revenue, delivery_revenue, cogs, payment_fee, packaging, cold_packaging, shrink, ongoing_marketing, it_cost, late_coupon, external_quick, contract_rider_cost, employed_rider_cost, picker_cost, rent, maintenance, fixed_staff, utilities, replenish, launch_marketing, restoration, hq_cost, depreciation, ebit, pretax_for_tax, taxable_profit, tax, net_income`.
- 상세 현금흐름/잔액: `capex_initial, capex_refresh, deposit_paid, deposit_returned, inventory_investment, inventory_recovery, inventory_balance, asset_book_value, unamortized_book_value_at_close, tax_book_value_deduction, tax_loss_used, tax_loss_carryforward, expected_relocation_cost, operating_cf, cum_discounted_cf, orders, active_hubs`.
- `kpi`: `npv, max_funding, funding_limit`는 **원**, `_eok` 버전은 억 원. `funding_within_limit`는 불리언, `payback_year`는 연도 또는 `미회수`, `ebitda_positive_year`는 연도 또는 `미전환`. `scenario, discount_rate`도 반환한다. 기존 ⑦ 비교표의 `npv,max_funding`에는 원 단위 값을 연결한다.
- ③이 입력 오류를 잡을 때만 파일 쓰기가 발생한다(`outputs/data_issues_log.csv`). 정상 반복 평가는 메모리에서 수행하며 엔진 인스턴스를 재사용한다. 초기 데이터 로딩은 0.1초 성능 측정에서 제외한다.
- `outputs/finance_all_2027_stress_base.csv`는 검증 f의 입주 연도 제약 해제 가상 계획이다. 실제 계획은 `allow_early_open_for_stress_test=false`인 기본 설정으로 검증해야 한다.

## ④→⑤ 배차 엔진 계약 (2026-11-13)

- `DispatchEngine(DispatchInputs(config), orders, meta).simulate(plan)` → `SimulationResult(t3,trips,work,picking,bays,external,staffing,kpi,plan)`. 구현은 `src/dispatch/`이며 재실행 설명은 해당 README에 있다.
- `orders`: `instance,order_id,order_min,gu,cust_lat,cust_lon,building_type,weight_kg,pick_std_min` 필수. `order_min`이 기준이며 미래 주문은 배차 후보가 될 수 없다. 같은 시각 주문은 order_id로 정렬한다.
- `meta`: `instance,hub_lat,hub_lon,hub_gu,weather,rider_area_class,bike_bays`. 거점·고객 이동시간은 공용 TravelTime만 사용한다.
- `plan`: `policy,target,wait,wait_by_hour,margin,pickers,contract,employed_blocks`. 시간별 딕셔너리 키는 자정 기준 시간의 문자열이다. 블록은 `start_hour,hours,count`; hours는 4 또는 8이다. 탐색 결과 plan.json은 이 구조를 저장한다.
- 시간제 인원은 잔여 업무 완료 후 정시 종료 규칙이 적용된 실제 유급 시간으로 `staffing,work`에 기록된다. 직고용 블록은 조기 종료하지 않는다. `hour>=24`는 다음 날 새 주문을 받는다는 뜻이 아니라 같은 영업일의 잔여 처리 시간이다.
- `kpi`의 비용 분해: `contract_rider_cost_per_order_krw,employed_rider_cost_per_order_krw,picker_cost_per_order_krw`. 모두 2027년 가격이며 전체 전달 주문이 분모이고 외부 퀵은 이 세 성분에서 제외한다. ⑥ 입력 규약과 일치한다.
- 35kg 초과는 `external_delivery(order_record,pick_end_min)` 콜백을 반드시 제공한다. 완료 시각을 반환하면 별도 `external` 결과와 비용·쿠폰에 반영한다. 외부 퀵을 T3의 contract/employed로 기록하지 않는다. BM 입력은 35kg 초과 0건이며 BM 검증기는 이 고정 입력 기준이다.
- ④는 확률 승수를 사용하지 않는다. ⑤의 확률 이동시간 확장은 이 엔진의 사건·자원·배차 로직을 유지하면서 별도 검증해야 한다. 현재 코드가 확률 20회 실험까지 수행했다는 의미는 아니다.
- 정책별 T3에서 라이더/트립 키는 `(instance,rider_id)`, `(instance,trip_id)`다. 같은 식별자가 다른 인스턴스에 재사용될 수 있다. 출발은 상차 종료 후이며 모든 시각은 자정 기준 분(1440 초과 허용)이다.
- 검증 결과 `T3+고정입력`은 a·b·c·e·f·g·h, `인력표·근무기록`은 d·i·수급 및 e_full·g_staffing 보완을 포함한다. T3만으로 접속 중 대기 전체와 시간별 피커 수를 확정할 수 없다는 한계를 보존한다.

## ⑤ 파일럿 재현

- 재실행은 `python -B -m src.sim.replay --config config.json`이다. `sim`의 시드·반복 수·피킹 품목 상한을 적용한다. ⑤ 실험 표 3개의 컬럼 변경은 실험 착수 시 반영한다.
- 배송시간·정시율 기본 지표는 ① 진단과 같이 실제 외부 퀵과 전달 시각 결측을 제외한 내부 주문 기준이다. `all_avg_delivery_min`, `all_p90_delivery_min`, `all_ontime_rate`는 전달 시각이 있는 모든 주문 기준이다. `orders_per_day`는 수요 포함 주문 전체다.
- 평균과 5·95분위는 거점별 일 지표의 분포다. 반복별 위탁 인원은 14의 상한 전원이며 05b는 실측 수입·접속시간 비교에 사용한다. 상자그림 수염은 5·95분위, 마름모는 평균이다.
- `simulate(plan, travel_multiplier=callback)`은 결정론적 시간으로 묶음 후보를 구성한 뒤 출발 후보의 구간별 이동에 공용 확률 승수를 적용한다. 확률 이동을 반영한 복귀시각이 근무 종료 이내인 라이더를 배정한다. 기본 호출은 결정론적이다.

## ⑤ 운영 실험

- 벤치마크 반경 실험은 `config.json/sim/radius_bm`에서 조합·시간 제한·수급 기준을 읽는다. `radius_bm_check.csv`에서 `radius_min`이 공란인 행은 ④ 원위치 결정론적 기준이며 `radius_km`은 16b 표기 반경이다. 원위치 행의 확률 정시율은 공란이다.
- `radius_bm_check.csv`의 `version=v1`은 기존 결정론적 비용 행이다. `version=v2`는 `order_level`과 `radius_type`으로 구별하고, `radius_type=original`은 16번의 실제 위치이며 km는 16b 표기값이다. v2 비용은 20회 모두 전량 배송했을 때의 독립 반복 평균이다. `rider_cost_per_order`는 위탁과 직고용 비용의 합이다. 미배송은 합계, 대기 공간은 최대, 수급은 모든 반복의 충족 여부다.
- v2의 `service_radius_basis.csv`는 반경·대표일의 독립 20회에서 정시율·전량 배송·수급·대기 공간 및 운영 제약을 모두 충족한 최대 주문량과 그 조합의 비용을 저장한다. 08은 이 주문량으로 처리능력을 확인하고, 같은 반경의 모든 통과 조합의 독립 반복을 합쳐 비용과 정시율을 계산한다. 보정 정시율은 접수 주문을 분모로 하고 전달시간에 1.2분을 더한다. 통과 수준이 없는 행의 주문량·비용은 공란이다.

- v1의 결과 11개 CSV와 실험 그림 2개는 `outputs/sim_v1/`에 보존한다. BM2 통합 검사는 `python -B -m src.sim.consistency --config config.json`으로 실행한다. 독립 20회의 평균 보정 전 정시율 0.97 이상, 모든 회차 미배송 0건, 평균 총 운영비 4,231~6,347원, 반복 중 최대 대기 13면 이하, 최소 위탁 시급 17,860원 이상을 검사한다.

- 재실행은 `python -B -m src.sim.experiment --config config.json`이다. `--grid full`, `--grid reduced`를 지정할 수 있다. `config.json/sim`에서 시드·반복 수·정시율·대표일·반경·보정량·시간 한도를 설정한다.
- 주문량 축은 최종 접수 건수다. 비의 수요 배수는 계획 기록에 함께 제공하고 실험의 고정 주문 수에 다시 곱하지 않는다. 평일 맑음·주말 맑음·평일 비는 각각 `weekday_clear`, `weekend_clear`, `weekday_rain`이다.
- `*_corrected`는 보정 후 값이다. 평균은 주문별 +1.2분, 90분위 보고치는 +2.7분이다. 정시율과 지연율은 주문별 +1.2분으로 60분 기준을 다시 판정한다. 동일한 주문과 인력 계획을 사용하므로 보정 전후의 라이더·피커 비용과 직고용 비중은 공유한다.
- `staffing_feasible`은 20회 모두 배송 완료 및 공급·수급·대기 공간·피커·근무 블록 준수다. `max_orders_per_day`는 이 조건과 반복 평균 보정 정시율 기준을 만족한 실험 주문 수의 최댓값이다. 해당 점이 없으면 공란이며, 최고 주문 수준도 통과했는지는 `corrected_pass_at_grid_max`로 확인한다.
- 잔여 주문은 `python -B -m src.sim.resume --config config.json`으로 미완료 회차만 처리한다. 비용표의 `metrics_include_clearance`가 참이면 배송·비용의 20회 집계에 추가 근무를 포함한다. `initial_complete_repetitions`와 `recovery_repetitions`는 각각 초기 완료 및 추가 근무 횟수이며, 용량과 T2 지연 집계에는 기존 인력으로 20회 완료한 조합만 반영한다.
- `late_rate` 표는 인력 제약 충족 조합을 반경·대표일·피크 이동시간 구간별로 모아 반복 내 주문 수로 가중 집계한 뒤 일별 평균과 5·95분위를 제공한다. 구간 상한을 포함한다. `employed_share`와 위탁·직고용·피커 주문당 비용의 분모는 퀵을 포함한 전체 배송 주문이다. 외부 퀵 비용은 ⑥이 별도 가산한다.

## ③ 네트워크 설계 1차 (2026-11-13)

- 재실행은 `python -m src.network.run --config config.json`. 대안은 A/B/C/B2, 탐색 경로는 greedy/seed다. 시장 base·주문 mid로 각 계획을 정한 후 개폐점 계획을 고정해 시장 3개×주문율 3개 조합을 평가한다.
- `assignment.expected_orders_per_day`는 처리 상한에 따른 비례 접수 제한 이후 하루 주문이다. 미서비스 동은 site_id 빈칸, 주문 0, 배송시간·지연율 빈칸이다. 세대 커버리지는 T 이내 배정 동의 해당 연도 세대수 합/서울 전체 세대수다.
- `network_plan_<대안>_<탐색>.csv`에 두 경로의 결과를 모두 저장하고, 한도 내 NPV가 높은 결과를 `network_plan_<대안>.csv`로 선택한다. P1/P2의 2027년 초 철수는 open_year=close_year=2027로 표시한다. 계속 운영 파일럿의 재무상 open_year=2027과 실제 램프업 개점일은 구별한다.
- 교체는 과거 계획 이력을 보존한 폐점·개점이다. `relocated_from`은 이번 탐색에서 빈칸이며 임대차 강제 이전 기능을 호출하지 않는다. `close_year`부터 미영업이다.
- 접수 제한 손실은 배정 수요 중 상한 초과분만 집계한다. 미서비스 잠재 수요는 포함하지 않는다. 지도는 각 배정 출력 연도에 생성한다.
- `finance_<대안>_<시장>.csv`는 mid, `_rate_low/high.csv`는 주문율 범위별 연간 재무다. 재현에는 `network_effective_config.json`으로 초기화한 FinanceEngine과 `network_ops_<대안>_<시장>.csv`를 사용한다.
- 재무 엔진의 선택 설정 `finance.pilot_initial_inventory_krw`는 파일럿별 2026년 말 추정 재고 잔액이다. 지정 시 모든 파일럿 값을 포함해야 하며 즉시철수 회수와 계속운영 기초잔액에 같은 값을 사용한다. 생략한 기존 호출의 재고 계산은 종전 규칙을 따른다.
- D 전용 재실행은 `python -m src.network.staged.run --config config.json`이다. `alt_compare.csv`의 D 행만 추가 또는 교체하며 D의 `T`는 2032년 이후 값이다. `assignment_D.csv`와 지도는 기존 5개 연도에 2032·2037년을 추가한다. D 완료 상태는 `network_run_status_D.json`을 사용한다.

## 08 ⑤ 되먹임 재설계 (2026-11-13)

- 재실행은 `python -B -m src.network.feedback --config config.json`이다. `config.json`의 `feedback`에서 기존 네트워크 설정 경로, 반경 상한, 대표일 대체, 병렬 탐색 수와 실행시간을 지정한다.
- `alt_compare_v2.csv`의 `selected=1`은 대안별 비교 대상 계획이다. 자금 한도 충족 여부는 `funding_within_limit`로 읽는다. A·B·B2에는 기존 선택 경로를 기록하고 C15에는 두 재탐색 경로를 기록한다.
- 비교표의 운영 정시율과 건당 비용은 2027년 기본 시장·중앙 수요에서 접수된 주문으로 가중한다. 비용은 라이더와 피커의 2027년 단가 합이다. EBITDA 흑자 연도도 기본 시장·중앙 수요를 기준으로 한다. `max_funding_low_mid_krw`와 `max_funding_low_low_krw`는 저성장 시장의 주문율 중앙·하단이다.
- `feedback_basis_v2.csv`는 비 대표일로 대체한 맑은 평일 상한, 달력 일수, 유형별 환산 비율, 위탁·직고용·피커 단가와 보정 정시율, 05 대비 건당 차이를 기록한다. `cost_per_order_krw`에는 쿠폰과 외부 퀵을 별도 처리하는 ⑥의 라이더·피커 단가를 기록한다.

## 08-2 환류 보정·재설계·분해 (2026-11-13)

- `python -B -m src.network.feedback_v3 --config config_feedback_v3.json`으로 실행한다. 새 CSV 이름에 기존 파일이 있으면 보존을 위해 발행을 중단한다.
- `outputs/feedback_decomposition.csv`는 기존 B의 입지·개폐점과 기본 시장·중앙 수요를 고정한다. 기준, 08 변경별 단독 적용, 08 동시 적용 및 08-2 가중 동시 적용의 NPV와 기준 대비 차이를 기록한다.
- `outputs/alt_compare_v3.csv`와 `outputs/network_plan_<대안>_v3.csv`는 A·B·B2·C15·D15의 탐색 대표안과 재무 지표를 기록한다. `selected=1`은 비교용 대표안이다. 저성장·중앙 수요에서 자금 한도를 충족하는지는 `funding_within_limit_low_mid`를 확인한다. `outputs/feedback_compare_v3.csv`는 기존 ③, 08, 08-2의 15행을 보존한다.
- `outputs/breakeven_cost.csv`는 B·D15 v3 계획의 목표별 운영 단가와 재계산된 재무 지표를 기록한다. `outputs/capacity_sensitivity.csv`는 같은 계획의 M형 하루 상한 3수준과 기본·저성장 시장의 중앙 수요 재무 결과를 기록한다.
- `outputs/feedback_{basis,calendar_cells}_v3.csv`는 달력 가중 입력과 6개 대표일 칸을 기록한다. `outputs/network_{search_branches,search_trace,capacity_candidates,verification}_v3.csv`와 `outputs/data_issues_log_v3.csv`는 후보 탐색, 상한 초과 권역의 추가 후보, 검산, 대체값을 기록한다.

## 08-3 인력 탐색 및 v4 재평가 (2026-11-13)

- `python -B -m src.network.feedback_v4 --config config_feedback_v4.json`으로 실행한다. `outputs/staffing_frontier.csv`는 칸별 후보의 탐색 5회 요약과 독립 검증 시도별 20회 요약을 포함한다. `feasible`은 해당 단계 판정, `certified`는 탐색·독립 검증 통과 여부, `selected`는 최종 기록안이다. `outputs/staffing_validation_v4.csv`에서 시도별 독립 반복 상세를 확인한다.
- `outputs/staffing_contribution_v4.csv`는 2029년 공헌이익 계산에 사용한 ⑥ 엔진 출력이다. `outputs/feedback_calendar_cells_v4.csv`는 반경·대표일별 최대 검증 시험점, `outputs/feedback_basis_v4.csv`는 06 달력 가중 운영 입력이다.
- `outputs/{alt_compare_v4,feedback_compare_v4,breakeven_cost_v4}.csv`와 `outputs/network_plan_<대안>_v4.csv`는 08-2와 같은 기준으로 산출한다. 전후 비교에는 ③·08·08-2·08-3의 20행을 기록한다. 배송 주문이 없는 계획의 실적 단가·정시율과 손익분기 단가는 공란이다. `alt_compare_v4.csv`의 `ops_input_*`는 반경별 입력값이다. 탐색과 검산은 `outputs/network_{search_trace,verification}_v4.csv`를 사용한다.
