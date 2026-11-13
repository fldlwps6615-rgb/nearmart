# NearMart_data — 2026 물류 AI 해커톤 데이터 패키지

문제: 서울 1시간 퀵커머스 거점 네트워크 설계 (문제지 8장 참조)

| 파일 | 내용 |
|---|---|
| 01_dong_master.csv | 서울 행정동 427개 특성 |
| 02_dong_boundary.geojson | 서울 행정동 경계(단순화) |
| 03_candidate_sites.csv | 거점 후보지(파일럿 P1·P2 포함) |
| 04_hub_types.csv | 거점 유형 S/M/L |
| 05_pilot_orders.csv | 파일럿 주문·배송 로그 |
| 05b_pilot_rider_log.csv | 파일럿 라이더 접속·지급 로그 |
| 06_pilot_calendar_weather.csv | 파일럿 기간 달력·날씨 |
| 07_market_scenarios.csv | 시장 규모(참고)·공통 평가 시나리오 |
| 08_population_projection.csv | 자치구 장래추계 |
| 09_competitor_hubs.csv | 경쟁사 거점 |
| 10_market_rules.csv | 수요 계수·배달비 정책 |
| 11_bridges.csv | 한강 교량 |
| 12_speed_profile.csv | 이륜차 속도 |
| 13_od_site_dong.csv | 후보지 × 행정동 거리·시간 |
| 14_rider_supply.csv | 위탁 라이더 공급 상한·수급 조건 |
| 15_cost_parameters.csv | 비용·재무 파라미터 |
| 16_vrp_benchmark_orders.csv / 16b_vrp_benchmark_meta.csv | VRP 벤치마크 BM1~BM3 |
| 17_travel_time_check.csv | 이동시간 계산 검증 표본 60건(문제지 부록 A 공식의 정답값) |
| submission_templates/ | 제출 양식 T1~T4 |

- 인코딩: UTF-8(BOM). 좌표: WGS84. 금액: 원(부가세 제외).
- 일부 CSV(07, 12, 14)는 `#`으로 시작하는 구획 제목이 있어 여러 표가 한 파일에 들어 있습니다.
- 데이터는 공공통계 수준에 맞춘 근사·가상 데이터이며, 현업처럼 결함이 섞여 있습니다.
- 행정동 경계 출처: 통계청 SGIS 행정동 경계(공공누리 제1유형)를 가공한 vuski/admdongkor ver20261001 (CC BY 4.0), https://github.com/vuski/admdongkor
- 소스코드는 제공하지 않습니다. 이동시간은 문제지 부록 A 공식으로 직접 구현하고 17번 표본으로 확인하세요.
