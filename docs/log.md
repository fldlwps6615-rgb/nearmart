# 작업 기록

- 2026-11-12: 이동시간 모듈 src/travel_time.py 구현, 17번 60건 통과(최대 오차 0.0000분)

## 2026-11-13 처음 세팅 (2~7단계)
1. 만든 파일: 신규 없음; docs/interfaces.md의 T1~T4 해당 행 수정, AGENTS.md 역할 섹션에 로그 규칙 추가, docs/log.md 기록.
2. 핵심 숫자: Python 3.14.10; 요청 패키지 6개 설치, 개별 import 4개 성공·2개 실패; data/ 파일 24개 SHA256 변경 없음; T1~T4 컬럼 수 6·6·10·2 및 인터페이스 4개 일치 검산 완료; 역할 규칙 1회 추가 및 날짜 포함 4줄 로그 확인.
3. 새로 세운 가정: 없음.
4. 남은 문제·확인 필요: Windows 애플리케이션 제어 정책이 pandas conversion 및 pulp _rustcore DLL을 차단함; python src/check_travel_time.py data outputs는 pandas import에서 실패하여 결과 두 줄 미생성. 정책 관련 확인 후 pandas·pulp import 및 이동시간 검증 재실행 필요. data/ 수정 없음.

## 2026-11-13 ⑦ 인터페이스 분리
1. 만든 파일: 신규 없음; docs/interfaces.md 수정, docs/log.md 기록.
2. 핵심 숫자: ⑦ 행 1개를 2개로 분리; 비교표 8개 컬럼, T4 2개 컬럼; 최종안 기준 KPI 26개 명시. 요청한 두 행과 일치 확인.
3. 새로 세운 가정: 없음.
4. 남은 문제·확인 필요: 이번 문서 수정 관련 없음; 기존 Python DLL 차단 문제는 미해결.

## 2026-11-13 패키지 및 이동시간 재검증
1. 만든 파일: outputs/travel_check_result.csv 생성·갱신, docs/log.md 기록; docs/interfaces.md의 ⑦ 두 행은 요청 내용과 이미 일치하여 유지.
2. 핵심 숫자: 패키지 6개 설치 상태 및 import 성공(pandas 3.1.1, pulp 4.2.0); 결과 CSV 60행, 통과 60/60건, 최대 오차 0.0000분, 한강 횡단 판정 및 교량 선택 각각 60/60 일치; ⑦ 두 행 일치 및 data/ 24개 파일 SHA256 변경 없음 확인.
3. 새로 세운 가정: 없음.
4. 남은 문제·확인 필요: 없음; 이전 pandas·pulp DLL 차단 오류는 이번 실행에서 재현되지 않음.

## 2026-11-13 ① 데이터 검증·정제 사전 점검 중단
1. 만든 파일: outputs/data_issues_log.csv(미결정 2개 규칙의 부분 로그), outputs/clean_blocker_weight.csv, outputs/clean_blocker_benchmark_time.csv, outputs/clean_blockers.md; docs/log.md 기록.
2. 핵심 숫자: 품목 수 181개 중량 결측 1행·동일 품목 수 정상 표본 0개; 벤치마크 시각 불일치 26행(BM1 9+BM2 6+BM3 11), 차이 0.99~1.00분; 원본 24개 파일 SHA256 변경 없음 검산.
3. 새로 세운 가정: 처리 가정 없음. 시각 두 표기의 반올림 차이를 허용하는 사전 검사 기준은 0.01분이며, 원본 수정에는 사용하지 않음.
4. 남은 문제·확인 필요: 사용자 지시의 목록 밖 결함 발견 시 중단 규칙 적용. 중량 대체 표본 부재 처리 및 벤치마크 기준 시각 결정 필요; 공용 로더/config·전체 8종 검사·정제·정제 후 검산 미완료. 선택지와 영향은 outputs/clean_blockers.md 참조.

## 2026-11-13 ① 데이터 검증·정제 + 공용 로더 완료
1. 만든 파일: config.json, src/common/{__init__,io}.py, src/clean/{__init__,rules,run,report,test_checks}.py, src/clean/README.md, outputs/clean/ CSV 20개·GeoJSON 1개, outputs/data_issues_log.csv, outputs/clean_rowcount.csv, outputs/clean_validation_{raw,after}.csv, outputs/clean_candidate_distribution.csv, outputs/clean_verification_summary.csv, outputs/clean_source_hashes.csv, outputs/clean_run_status.json, outputs/clean_verification.md, outputs/pilot_diagnosis.md, outputs/clean_tests.{json,txt}; src/travel_time.py의 구획 로더 dtype·파일 닫기 보완(이동시간 계산식 유지), outputs/travel_check_result.csv·clean_blockers.md 및 docs/interfaces.md·assumptions.md·log.md 갱신.
2. 핵심 숫자: 입력 CSV 18개·구획별 표 22개; 주문 79,539−중복 237−TEST 24−CANCELLED 1,561=77,717행; 후보지 71−서울 밖 1−중복 2=68곳(S 28·M 27·L 13); 전 18개 파일 행 수-로그 차이 0; 8종 재검사·저장 후 재읽기 미승인 잔여 0; 승인된 전달 시각 결측 777·중량 미해결 1·시각 불일치 26건 보존; 검산 10/10 및 이동시간 60/60 통과(최대 오차 0.0000분); 원본 24개 파일 SHA256 변경 없음.
3. 새로 세운 가정: 사용자 확정 2건(중량 미해결은 수요 포함·라이더 트립/중량 분석 제외, 벤치마크 계산은 order_min)을 사전 목록 밖 결함으로 기록; 표기 오류 원인은 사용자 판단이며 실측 차이 0.99~1.00분으로 병기. 세대원 수는 정상 동별 인구/세대 산술평균, 월세 표본은 중복·서울 밖 제외 후 같은 구, 중량 표본은 중복 제거 후 상태 분리 전 동일 품목 수; 추정값 반올림 없음. 면적 경계 330㎡는 M·660㎡는 L·1000㎡는 L(실제 경계값 없음). 서울 좌표 범위·자정 이후 라이더의 영업일 연결 해석을 assumptions.md에 명시.
4. 남은 문제·확인 필요: 이번 단계의 미승인 결함 없음. 승인된 원문 결측·시각 표기 불일치는 flag로 남겨 분석 시 필터 적용 필요. 이후 단계는 공용 로더의 outputs/clean/만 읽고 13번은 active_od() 또는 정제 후보지와 조인; 시나리오·T·배차 정책은 후속 단계 결정 전까지 config에서 null. 재실행: python -m src.clean.run --config config.json. 상세 검산은 outputs/clean_verification.md.

## 2026-11-13 ① 파일럿 운영 진단
1. 만든 파일: src/diagnosis/{run,figures}.py, outputs/pilot_kpi.csv, outputs/pilot_expectation_check.csv, figures/pilot_{daily_orders,delivery,hourly_batching,trip_distribution,rider_earnings,distance_demand,late_concentration}.png. outputs/pilot_diagnosis.md에 수치 출처를 붙인 진단 5문장을 작성했다. config.json·docs/interfaces.md·docs/assumptions.md·docs/log.md 갱신. src/clean/report.py의 품질 진단은 outputs/clean_verification.md로 옮겼다.
2. 핵심 숫자: KPI 4,635행, 기대값 비교 8개, 수요 77,717건과 배송 KPI 76,866건. 평균 19.10분, 90분위 27.10분, 지연 213건(0.277%, 전부 비·폭우), 정시율 99.723%. 트립당 P1 1.188·P2 1.011건, P1 피크 시간당 56.60건과 단건 출발 트립 84.2%. 일별 평균 시급 P1 12,089원·P2 5,324원, 17,000원 미달 P1 79/91일·P2 77/77일. 저장된 KPI의 주문 합·트립 수·정시율·거리별 합과 PNG 7개를 검산했다.
3. 새로 세운 가정: 개점일을 0일로 두고 피크는 18:00 이상 22:00 미만으로 집계했다. 트립은 거점·영업일·라이더·트립 ID로 식별하며 출발 시간으로 묶음을 집계했다. 서비스 반경 내 동 세대수와 1km 도로거리 구간을 사용하고 라이더 CV는 모집단 표준편차로 계산했다. 기대 시급 비교에는 거점·일 시급의 단순평균을 적용했다.
4. 남은 문제·확인 필요: 기대값과 큰 차이 없음. 05b 지급액 합계는 05 배분액보다 P1 1,135원·P2 24원 높으며 원인은 미확인이다. 주문당 지급액은 05, 접속시간당 지급액은 05b를 적용했다. 재실행은 python -m src.diagnosis.run --config config.json.

## 2026-11-13 ⑥ 20년 재무 엔진
1. 만든 파일: src/finance/{__init__,engine,verify,report}.py, src/finance/build_check.mjs, src/finance/README.md, outputs/finance_rule_map.md, outputs/finance_verification.md, outputs/finance_{zero,p1,feasible,late_open,all_2027_stress}_base.csv, outputs/finance_validation/ 검산 입력·결과·PNG, report/finance_check.xlsx; config.json 및 docs/interfaces.md·assumptions.md·log.md 갱신.
2. 핵심 숫자: 연도별 21행(2026~2046); 계산 검증 47개 통과·엑셀 96셀 최대 차이 0원; a NPV −496.548011121억(HQ −496.690511121억+철수 0.1425억), 세금 0; c 할인율 12→16% NPV 5245.288881→4002.524602억; d 종료 보증금 55,000,000원+재고 269,610,295.526313원−복구 63,000,000원 반영; e 1.1040808032배; f 68곳 최대 자금 460.73억; 300회 평균 0.036165초·최대 0.094441초; 원본 24개 SHA256 불변. 입력 순서 불변·07 세 시나리오 지수·재투자 시점 옵션 추가 확인.
3. 새로 세운 가정: 승인한 03/04/15 비용 출처와 2036년 말 재투자·다음 해 상각을 적용. 위탁·직고용 2027년 주문당 비용 분리. 관리비 2%·IT/쿠폰/퀵 고정·15년 갱신 5%, 종료 장부가 추가 공제 기본 없음(옵션 가능), 파일럿 재투자 기준 매몰 0(유형 금액 선택 가능), 신규 재고 기본 영업연도 말, 폐점연도 기본 첫 미영업 연도. 상세 판단과 설정은 assumptions.md.
4. 남은 문제·확인 필요: 임시 운영값은 ②③⑤ 결과로 교체 필요. f는 입주 연도 제약을 해제한 가상 검산(10곳 조기 입주)이며 실제 대안 아님. 종료 장부가 세무 공제 및 파일럿 재투자 기준은 부록 B 미확정 해석으로 config에 보존. t=0 양수인 손실 계획의 회수연도 2026 표시는 정의상 특례. 엑셀 자체 앱 재계산은 미실행(Artifact Tool 재계산·XLSX 수식/캐시·두 시트 렌더 검증 완료).

## 2026-11-13 ② 수요예측 완료 및 재검산
1. 만든 파일: src/demand/{__init__,model,run,report,figures}.py, outputs/{base_rate_dong,hour_profile,order_profile}.csv, outputs/demand_*의 전망·효과·진단·검산·상태 파일(권역/기간 비교 demand_pilot_comparison.csv, 램프업 분해 demand_ramp_sensitivity.csv, 종합 demand_validation.md 포함), figures/demand_{ramp,extrapolation,pilot_actual_predicted,seoul_map,year_trends,trend_decomposition}.png. config.json, outputs/data_issues_log.csv, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 주문 77,717건, 파일럿 34동, 서울 427동(외삽 74), 전망 26,901행, 시간대 60행, 주문 프로필 5행. 상태 complete, 검산 14/14, 원본 24개 SHA256 일치. 연도 합계 최대 상대오차 2.51e-16(허용 1e-9), 세 계수 역산 1.42e-14건/일. 90일차 성숙 배수 하/중/상 1.055/1.420/1.572. 서울 2026년 15,105.55건/일, 사전 배수 1.32 적용 민감도 14,037.39건/일, 램프업 영향 +1,068.15건/일, 상한 초과 105.55건/일.
3. 새로 세운 가정: 로그 주문 RMSE·공통 램프업, 표준 주간 맑음, 동별 Student-t 90% 예측구간과 성숙 배수 상대 범위를 사용했다. 민감도에서는 90일차 기준 수요를 고정하고 성숙 배수만 바꿨다. 모형과 전망은 기존 실행과 동일하다.
4. 남은 문제·확인 필요: 사전 주문율의 세부 집계 정의는 제공 문서에서 확인되지 않았다. 문제지 권역과 주문 관측 동이 일치하고, P1/P2는 영업기간 2.89/1.65건, 마지막 주 3.69/2.05건으로 확인한 범위에서는 관측 수요 밀도가 낮다(1,000세대·일당). 성숙 배수 범위가 사전 양쪽 경계를 벗어나므로 뒤 단계에 범위를 전달한다. 재실행 python -m src.demand.run --config config.json.

## 2026-11-13 ③ 네트워크 설계 1차 완료
1. 만든 파일: src/network/{model,search,run,verify,report}.py 및 README, outputs/network_plan_*.csv·assignment_*.csv·alt_compare.csv·network_*.csv/json/md·finance_<대안>_<시장>*.csv, figures/network_<대안>_<연도>.png. src/finance/engine.py, config.json, outputs/data_issues_log.csv, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 두 경로·파일럿 4조합으로 32탐색, 139,139회 평가, 비교표 24행, 배정 8,540행·지도 20장, 검산 56/56 통과. base·중앙 A/B/C/B2 NPV 225.49/377.43/444.91/282.48억 원, 최대 자금 157.23/153.39/142.47/75.61억 원. B 순차 추가 377.43억과 사전안 출발 369.13억, 2027년 19곳·80.30%·11,153건/일, 핵심 5/11곳. 유형·입주 위반 0, 최대 피크시간 12.00/14.99/19.96/14.99분. 원본 24개 SHA256 일치.
3. 새로 세운 가정: 승인한 한도 진입 전 NPV 개선·자금 감소 조건, 진입 후 500억 강제, B2 유지 파일럿 포함 8곳을 적용했다. 파일럿 말 재고는 P1 1.20억·P2 0.51억으로 추정해 철수 80% 회수와 계속운영 기초잔액에 같은 값을 넣었다. 정제 제외 C056을 사전 목록에서 뺐다. 완료 경로 8개 체크포인트 및 실행 시작 설정을 보존했다.
4. 남은 문제·확인 필요: 시장 low와 주문율 하한이 겹치면 네 선택안 모두 500억을 초과한다. 고정 B에서 2027년 동별 잠재 수요를 유지하면 NPV −9.09억 원으로, 장기 전망 적용 대비 386.52억 원 낮다. 기획 NPV 상세 산식은 미확인이다. ⑤에서 1차 운영 가정(배송시간, 지연, 라이더·피커 비용, 처리 상한, 시간대별 수급)을 검증·교체한다.

## 2026-11-13 ③ 대안 D 추가
1. 만든 파일: src/network/staged/, outputs/network_plan_D.csv·assignment_D.csv·network_coverage_D.csv·investor_condition_cost.csv·network_*_D 및 finance_D_* 결과, figures/network_D_*.png 7장. alt_compare.csv에 D 3행 추가. config.json과 docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 3,640회 평가, 검산 21/21, 기존 대안 파일 118개 해시와 비교표 기존 24행 보존. 2027/2032/2037년 19/23/26곳, 세대 커버리지 80.30/96.10/97.06%. base·중앙 NPV 399.55억 원, 최대 자금 153.39억 원. 중앙 주문율에서 C−D는 시장 low/base/high 순으로 28.92/45.36/49.67억 원. 배정 2,989행.
3. 새로 세운 가정: B의 첫해 개설 상태와 T=15분을 2031년까지 유지하고 2032년부터 T=20분을 적용했다. 2032/2037/2042년에 각각 4/3/1곳 추가를 채택했다. 투자자 조건 비용은 동일 시장·주문율 조합끼리 C−D로 계산했다.
4. 남은 문제·확인 필요: 시장 low와 주문율 하한 조합에서 D의 최대 자금은 1,341.39억 원으로 500억 원을 초과한다. 확정 계획은 base·중앙 수요 기준이며 ⑤에서 운영 가정을 검증한다.

## 2026-11-13 ④ 배차 엔진과 벤치마크 완료
1. 만든 파일: src/dispatch/{__init__,inputs,engine,search,run,verify_t3,test_checks,diagnose,report}.py·README.md, report/T3_benchmark_dispatch.csv, outputs/bm_{kpi,staffing,kpi_recalculated,expectation_check}.csv, outputs/dispatch_{verification,selected_verification}.csv·dispatch_summary.md, outputs/dispatch/ 인스턴스·정책별 T3/근무/피킹/체류/트립/계획·격자 탐색·상위 3후보·설정/상태/해시·시험/기대값 진단 파일; config.json·docs/interfaces.md·assumptions.md·log.md 갱신.
2. 핵심 숫자: T3 3,675행·10컬럼·UTF-8 BOM; KPI 9행, 인력표 351행; 선택안 BM1~3 모두 P2, 주문 1,107·1,261·1,307건 및 지연/규칙 위반 0, 수급 3/3 충족; 주문당 비용 4,538.94·5,288.52·5,073.94원, 위탁 시간당 수입 24,532.55·18,469.43·30,986.37원. 정책별 독립 검사 144개, 제출안 50/50 및 변조/경계/외부 퀵 시험 13/13 통과; 원본 24개 및 travel_time.py 불변. 최종 탐색 278.205·586.775·528.488초, 완료/미완료 후보 569·511·541개, 최종 각 실행 600초 이내.
3. 새로 세운 가정: 자정 후 21~24시 공급 상한 연장·실제 시각 속도 순환; 위탁/피커 시간 단위 계획 및 잔여 업무 후 다음 정시 종료(24시 이전 자동 조기종료 없음), 직고용 4/8시간 전부 유급. 무배송 라이더도 대기 공간·접속시간·모집단 CV에 포함; P0 3건은 씨앗 포함, 1.2km는 고객 쌍별 판정. 기준 정책 잔여 주문은 최대 48:00까지 청산, 실패 후보는 미배송을 숨기지 않고 기록. e/g는 T3 하한/절대 상한과 보조기록 완전 검증을 구분.
4. 남은 문제·확인 필요: ④ 제출안 검증 실패 없음, ⑤ 확률 이동/반복 실험은 후속 범위. BM1 직행 최대 사전값 26.4분은 접수시각 속도 진단 26.416874분으로 재현되지만 규칙상 실제 출발시각 값 25.285262분을 유지. BM3 P0은 수급을 충족해도 지연 569건·대기 공간 초과로 실행 불가(기대와 실패 원인 차이). 탐색 표는 최종 재실행 기준이며 초기 실패 실행을 포함한 개발 누적 시간은 미측정. 탐색 범위 내 선택 결과이며 전역 최소 보장은 없음. 재현은 src/dispatch/README.md, 상세는 outputs/dispatch_summary.md.

## 2026-11-13 ⑤ 파일럿 재현 검증
1. 만든 파일: src/sim/{orders,replay,report}.py, outputs/sim_replay_{check,observed_days,repetitions,audit}.csv, figures/sim_replay_boxplot.png. src/dispatch/{engine,inputs}.py, config.json, outputs/data_issues_log.csv, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: P1/P2 실측 48/45일, 05b 실측 접속표 각 20/20회·상한 대체 0회, 비교표 32행. 생성 주문 18,092건 모두 배송, 실행 5.95초. 같은 날짜 기준 평균 배송시간 P1 16.91→15.97분, P2 18.72→17.20분. 주문수·시드·비교표 재읽기, 원본 24개 SHA256과 ④ T3 3,675행 일치 검산.
3. 새로 세운 가정: 맑은 월~금 날짜를 복원 추출하고 해당 일 주문 수·05b 접속 구간을 적용. ② 시간대 패턴, 반경 내 위치와 파일럿 속성 재표본, 영업시간 M형 피커 상한 적용. 외부 퀵은 관측 잔여시간과 공용 확률 이동·건물 전달시간을 합산. 위탁 단가와 수급 기준은 2026년으로 환산.
4. 남은 문제·확인 필요: P1/P2 모두 대기 공간 20/20회 초과, 위탁 수급 0/20회 충족. 시간대별 접속 상한 초과 P1 12/20회·P2 0/20회. 내부 정시율 모두 1.000이고 퀵을 포함하면 지연 P1 6건·P2 1건. 사용자 확인까지 실험 미착수. 재실행 python -B -m src.sim.replay --config config.json.

## 2026-11-13 ⑤ 운영 실험 및 ④ 회귀 검증
1. 만든 파일: src/sim/{experiment,experiment_inputs,staffing,experiment_report,experiment_verify,resume}.py, outputs/sim_{capacity,cost_curve,late_rate,late_rate_repetitions,trend_check,expectation_check}.csv, outputs/sim_experiment_{plans,repetitions,recovery,verification,runtime}.csv, figures/sim_{experiment_boxplots,cost_and_employment}.png. config.json, src/dispatch/{engine,inputs}.py, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: BM1~3 P2 지연 0건, 주문당 비용 4,538.94·5,288.52·5,073.94원, T3 3,675행 및 verify_t3 50/50 일치. ④ 파일 해시와 원본 24개 해시 일치. 실험 204조합(M·A 60 포함)×20회, 기존 완료 3,450회 보존·미완료 630회만 추가 배송, 4,826,880건 전부 배송, 총 354.14초(실험 294.06초, 잔여 배송 60.08초), 결과 검산 19/19. 표는 용량 60행·비용 204행·지연 42행.
3. 새로 세운 가정: 중앙 대기면 대표 후보지, 맑음 18시 반경, 대표일별 ④ P2 설정과 2027년 공급·비용, 휴리스틱 인력 후보를 사용했다. 평균·주문별 SLA +1.2분, 90분위 보고치 +2.7분. 미완료 630회는 기존 근무 종료 후 직고용 8시간 교대로 잔여 배송을 처리했다. 해당 63조합의 비용·배송시간에는 추가 근무가 포함되며 용량·T2 지연 집계에는 원래 인력 기준을 적용한다.
4. 남은 문제·확인 필요: M·A 반경 8분의 보정 상한은 평일·주말 맑음 1,290건, 평일 비 1,032건으로 기대 범위 1,000~1,300건에 든다. M·A 12~20분 및 전체 54/60 용량 그룹에서 기준을 만족한 시험점이 없었고, 보정으로 상한이 변한 그룹은 0개다. M·A 평일 맑음 1,032건에서 8→20분 비용 3,822→10,961원, 직고용 비중 0.125→1.000(반올림). 20분의 보정 정시율 0.205여서 해당 비용은 정시 목표 충족 단가로 사용할 수 없다. 인력 제약 충족 비교에서 반경 확대 비용 증가 34/34, 주문량 증가 시 지연율 비감소 26/49, 비의 지연율 비감소 16/21. 인력 탐색 범위 밖의 가능성은 미확인.

## 2026-11-13 ⑤ 인력 탐색 수정 및 BM2 일관성 검사
1. 만든 파일: src/sim/{consistency,staffing_search}.py, outputs/sim_bm2_consistency_{check,repetitions,staffing_search}.csv. v1 CSV 11개·그림 2개를 outputs/sim_v1/으로 이동하고 SHA256 확인. src/sim/{experiment,experiment_inputs}.py, src/dispatch/engine.py, config.json, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 서로 다른 인력안 36개 평가, 독립 20회 25,220건 중 24,297건 배송·923건 미배송, 전량 완료 2/20회, 45.06초. 완료 2회 보정 전 정시율 0.5214(기준 0.99), 주문당 총 운영비 6,907.66원(허용 4,230.81~6,346.22원). ④ 최종 3,675행·지연 0·비용 일치, 배차 검산 13/13, 원본 24개 해시 일치.
3. 새로 세운 가정: P2 주말 맑음 1,261건, 공용 도로거리 3.8km 이내, ② 주말·후보지 유형 패턴과 파일럿 속성을 사용했다. ④ 인력표 환산과 위탁 상한에서 시작해 별도 3일로 인력안을 탐색했다. 두 KPI 수치는 완료한 두 날의 진단이며 미완료 날의 비용은 공란이다.
4. 남은 문제·확인 필요: 일관성 미통과. 완료일도 대기 공간 27면(상한 13) 및 위탁 수급 미달이다. 저장된 ④ BM2 P2는 직고용 48시간을 포함하며 126건은 3.8km 밖이다. 전체 실험 재실행·실패 행 계약 갱신·방향성 재검증은 착수 전 확인 단계에 남아 있다.

## 2026-11-13 ⑤ BM2 일관성 단일 요소 진단
1. 만든 파일: outputs/sim_bm2_factor_check.csv, outputs/sim_bm2_factor_repetitions.csv. 실행 코드는 임시로 전달했고 저장된 소스 및 ④ 파일의 해시를 대조했다.
2. 핵심 숫자: 5개 조건, 81회, 접수 102,141건·미배송 3,903건 검산. (a) ④ BM2 T3 1,261행·최종 인력표·정시율 1.000·5,288.52원 일치. (b)~(e) 정시율 평균 0.98846/0.49901/0.99865/0.96443, 미배송 합계 0/3,667/0/236건. 실행 20.17초.
3. 새로 세운 가정: (a)는 이동 확률 승수를 끈 ⑤ 운영 경로로 ④ 계획을 실행했다. (b)~(e)는 같은 20개 시드에서 매번 한 요소만 교체했다. 정시율 분모는 접수 주문이며, 미완료 날의 진단 비용 분모는 해당 날 전달 주문이다.
4. 남은 문제·확인 필요: (b)가 0.99를 처음 소폭 하회하고, (c)가 미배송을 크게 늘렸다. 생성 접수 시각 적용 시 16시·18시 평균 피킹 부하가 485·580분(기존 인력 420·540분)이고 트립당 주문은 4.38→1.87건이다. 접수 분포와 인력표를 먼저 맞추고 같은 비교를 재검증해야 한다. (e)도 0.99 미달이며 (d)는 기준을 충족했다. 소스 수정과 전체 실험 재실행은 하지 않았다.

## 2026-11-13 ⑤ 생성 부하 기반 BM2 통합 검사
1. 만든 파일: outputs/sim_bm2_consistency_workload.csv, outputs/sim_bm2_consistency_{check,repetitions,staffing_search}.csv 갱신. src/sim/{staffing_search,consistency}.py, config.json, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 인력안 36개, 독립 검사 20회·25,220건, 배송 18,422건·미배송 6,798건, 전량 완료 0/20회, 41.65초. 보정 전 정시율 평균 0.22839(5~95% 0.17847~0.29548), 최대 대기 17면, 위탁 최저 시급 11,060.82원. 15개 시간대 피커 산식·인원 상한·주문 보존·원본 24개 해시를 재검산했다.
3. 새로 세운 가정: ② 시간대 비중과 생성 품목의 기대 피킹 부하에 1.1배를 적용하고 유형 상한에서 제한했다. 기대 트립은 시간대별 내부 주문률과 P2 대기시간으로 추정했다. 3개 별도 생성일에서 계획을 탐색한 뒤 독립 20일을 검사했다. 미배송은 정시율 분모에 포함하며 전량 완료한 회차의 비용을 평가한다.
4. 남은 문제·확인 필요: 통합 검사 미통과. 18시 시작 피킹 부하 598.63분에 유형 상한 10명·600분이 적용되어 산식의 여유가 잘린다. 검사에서 대기 공간 12/20회 초과·수급 20/20회 미달, 전량 배송 사례가 없어 비용은 공란이다. 전체 실험 재실행·실패 조합 표시·방향성·기대 범위 비교는 통합 검사 통과 이후다.

## 2026-11-13 ⑤ 벤치마크 기반 반경 실험
1. 만든 파일: src/sim/radius_bm.py, outputs/radius_bm_{check,repetitions,plans,verification,runtime}.csv, outputs/service_radius_basis.csv. config.json과 docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 기준 3행·반경 8행, 전체 109.10초, 조합별 탐색 19.59~102.45초. 원위치 T3 3,675행·인력표·KPI 일치, 원본·④ 파일 117개 해시 일치. BM1·BM2 반경 6조합의 확률 120회에서 접수 142,080건=배송 136,222건+미배송 5,858건. 총비용·라이더비 인접 반경 비감소 8/8. BM3 원위치 계획을 반경 재표본에 적용한 추가 진단의 미배송은 15분 569건, 20분 727건이다.
3. 새로 세운 가정: 맑음 18시 편도시간 및 동일 구 속도로 환산한 도로거리 상한을 적용했다. 조합별 위치 표본을 고정하고 공용 확률 승수로 20회 반복했다. 비용은 결정론적 P2 선택안, 미배송은 20회 합계, 대기 공간은 최대, 수급은 모든 반복 충족 여부다.
4. 남은 문제·확인 필요: BM3 15분(4.00km)·20분(5.33km)은 ④ 탐색 범위에서 전량 배송한 P2 후보가 0개여서 확률 반복 및 반경별 비용 판정이 불가하다. service_radius_basis.csv의 두 행 모두 selected=False, 최대 반경 공란이다. 08에 넘길 검증 통과 반경은 없다. 기존 위치 BM3는 비교 기준 행으로만 포함했다.

## 2026-11-13 ⑤ 벤치마크 반경 v2 강건 탐색
1. 만든 파일: src/sim/radius_bm_v2.py, outputs/radius_bm_v2_{candidates,plans,repetitions,runtime}.csv. outputs/radius_bm_check.csv에 v2 36행 추가 및 v1 11행 보존, outputs/service_radius_basis.csv를 반경·날씨별 최대 검증 주문량과 비용 12행으로 갱신. src/sim/radius_bm.py, config.json, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 494.30초, 8개 병렬, BM1 포함 36/36조합, 인력 후보 2,355개, 탐색 통과 18조합, 독립 20회까지 통과 11조합. 독립 360회에서 접수 351,240건=전달 348,837건+미배송 2,403건. 통과 조합 미배송 0건, 원본·④ 파일 117개 SHA256 일치. 08 입력 통과 행 7/12개. BM3 원위치 1,307건, 12·15분 각각 784건, 15분 라이더 5,001.08원/건·피커 1,418.71원/건·최저 보정 정시율 0.964286.
3. 새로 세운 가정: 고정 시드로 원주문을 섞고 수준별 표본을 중첩했다. ④ 인력은 주문 비율로 올림하고 상한을 적용해 첫 후보로 평가했다. 5회와 독립 20회 각각 모든 반복에 동일 통과 기준을 적용했다. 08 비용은 독립 20회 평균이며, 최대 주문량은 시험 수준 중 최댓값이다.
4. 남은 문제·확인 필요: 세 대표일 모두 20분에서 통과한 시험점이 없었다. BM3 15분 80%의 독립 최저 보정 정시율 0.947419, 최대 대기 11면(한도 10)으로 60%만 통과했다. BM2 1,261건의 12→15분 라이더 비용은 4,049.34→4,038.81원으로 소폭 감소했다. 시험 수준 사이의 주문량과 탐색 후보 밖 인력안은 미검증이다.

## 2026-11-13 08 ⑤ 되먹임 재설계 완료

1. 만든 파일: outputs/feedback_compare.csv, outputs/alt_compare_v2.csv, outputs/network_plan_{A,B,B2,C15,D15}_v2.csv, outputs/investor_condition_cost_v2.csv, outputs/feedback_{basis,bm1_audit}_v2.csv, outputs/network_{search_trace,verification}_v2.csv, outputs/data_issues_log_v2.csv, src/network/{feedback,feedback_basis}.py. config.json, src/network/{model,verify}.py, docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: 전후 10행, 대안 비교 18행, 계획 5개, 투자자 조건 9행, 탐색 24,659회, 검산 81/81, 기존 파일 해시 358/358 일치. 12분·15분 M형 926.91·873.11건/일, 5,398.41·5,847.88원/건, 보정 정시율 0.994561·0.984414. 05 추정치 5,017.12원/건 대비 각각 +381.30·+830.76원. 기본 시장·중앙 수요 NPV C15 −374.22억 원, D15 −416.58억 원, 차이 42.36억 원.
3. 새로 세운 가정: 06의 비·폭우 26일을 비 평일로 대표시키고, 15분 맑은 평일은 비 평일 784건을 하한 대용치로 썼다. S·L은 M 대비 피커·대기면 비율의 최솟값을 적용했다. 단가·정시율은 반경 내 통과 조합의 독립 반복을 같은 비중으로 평균했다. 05 피커 비용은 관측 피킹시간의 15번 단가 환산치다. D15는 B의 첫해 거점을 2031년까지 고정하고, 한도 초과 출발 상태에서 ③의 자금 소요 감소·NPV 개선 규칙으로 재탐색했다.
4. 남은 문제·확인 필요: 기본 시장·중앙 수요에서 5개 안 모두 최대 자금 500억 원을 초과하고 2046년까지 EBITDA 흑자 전환이 없다. 기존 C·D의 20분은 ‘근거 없음’으로 보존했다. BM1 886건에서 원위치 2.2km 안은 20회 합계 440건 미배송·최저 위탁 시급 15,829원, 3.2km 표본은 전량 배송·최저 18,802원이었다. 고객 위치 표본과 선택 인력·묶음 계획이 함께 달라져 수급과 잔여 배송에 차이가 났을 가능성이 있다.

## 2026-11-13 08-2 환류 보정·재설계·분해 완료

1. 만든 파일: outputs/{alt_compare_v3,feedback_compare_v3,feedback_decomposition,breakeven_cost,capacity_sensitivity}.csv, outputs/network_plan_{A,B,B2,C15,D15}_v3.csv, outputs/feedback_{basis,calendar_cells}_v3.csv, outputs/network_{search_branches,search_trace,capacity_candidates,verification}_v3.csv, outputs/data_issues_log_v3.csv, config_feedback_v3.json, src/network/feedback_v3.py. src/network/{feedback_basis,search}.py와 docs/{interfaces,assumptions,log}.md 갱신.
2. 핵심 숫자: B의 08 NPV는 기준 377.43억 원에서 비용만 −208.88억, 처리능력만 112.81억, 지연율만 354.80억, 모두 적용 −432.78억 원이다. 달력 가중 운영비는 12분 5,508.40원, 15분 5,641.78원이다. B/D15의 NPV 0 단가는 4,817.14/4,798.53원, 저성장 자금 500억 단가는 5,319.47/5,296.58원이다. 탐색 평가 172,752회, 검산 94/94, 손익분기 목표값 재검산 최대 오차 265원, 기존 파일 해시 371/371 일치.
3. 새로 세운 가정: 15분 맑은 평일의 비용·정시율에는 같은 대표일의 12분 886건을 대입하고, 처리능력에는 08의 784건 가정을 유지했다. 손익분기·민감도는 B·D15 v3 계획을 고정했다. 손익분기는 위탁·직고용·피커 단가를 동일 비율로 바꿨다. 저성장·중앙 수요 한도를 초과한 후보끼리는 자금 초과액이 작은 후보를 우선했다.
4. 남은 문제·확인 필요: 탐색에서 저성장·중앙 수요 최대 자금 500억 원 이내 후보를 찾지 못했다. 비교용 대표안 중 최저는 B·C15 858.09억 원이다. M형 상한 1,261건에서도 B/D15 기본 NPV는 −82.64/−124.25억 원이고 저성장 최대 자금은 592.43/672.87억 원이다. 추가·교체 및 L형 후보 평가 근거는 탐색표에 남겼다.


## 2026-11-13 09 What-if

1. 만든 파일: src/whatif/{config.json,run.py,scenarios.py,summary.py}, outputs/{whatif_results.csv,whatif_summary.md,whatif_w1_rates.csv,whatif_nonrenewal_moves.csv}. docs/log.md 기록.
2. 핵심 숫자: 대안 5개 × 시장 3개 × 기본 및 충격 6개 = 90행, 기존 v3 기본 NPV 15/15행 일치, 보호 입력 SHA256 33/33 일치, v3 실행 3.59초. W1 NPV 181.39~267.44억 원 감소. W1·재계약 실패는 세 시장 모두에서 NPV 1위 대안을 바꿨고, W2·할인율 변경에서는 B·C15 공동 1위가 유지됐다.
3. 새로 세운 가정: W1은 고정 인력·배차에서 피커·직고용·위탁의 주문당 비용을 2028년부터 비례 인상하고 수급 기준에 같은 배수를 적용했다. 재계약 실패는 만료 후 영업 예정인 거점 전부를 입주 가능한 미사용 동일 유형 후보에 공용 이동시간 합 기준으로 1:1 이전했다(66쌍). 커버리지는 해당 연도의 배송 반경 내 세대 비율이다.
4. 남은 문제·확인 필요: v4 입력 게시 전이라 v3와 동일한 파일 형식으로 v4 선택 경로를 검산했다(2.94초). 실제 v4 계획 5개·운영 근거·비교표 게시 후 python -B -m src.whatif.run --plan-version v4로 실행한다.

