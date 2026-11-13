# 대안 D: B로 시작한 뒤 서비스 범위 확대

2031년까지 B의 2027년 개설 상태와 T=15분을 유지한다. 2032년부터 T=20분으로 넓히고 구간 시작 때 거점을 추가·폐점·교체한다. base·중앙 수요에서 개선된 변경은 모두 500억 원 이하다. 확정 계획을 시장 3개 × 주문율 3개에 적용한다. 비교표의 D.T는 확장 이후 값이다.

## 커버리지

| year | T | active_hubs | households_served | households_total | coverage | orders_per_day |
| --- | --- | --- | --- | --- | --- | --- |
| 2027 | 15 | 19 | 3.61089e+06 | 4.49674e+06 | 0.803001 | 11152.6 |
| 2032 | 20 | 23 | 4.35187e+06 | 4.52856e+06 | 0.960983 | 20633.9 |
| 2037 | 20 | 26 | 4.42737e+06 | 4.56141e+06 | 0.970613 | 24118 |

## C와의 NPV 차이

C−D가 양수이면 D의 NPV가 그만큼 작다. 음수이면 해당 조합에서 D의 NPV가 더 크다.

| scenario | demand_band | npv_C_eok | npv_D_eok | investor_condition_cost_eok |
| --- | --- | --- | --- | --- |
| low | low | -479.776 | -508.691 | 28.9149 |
| low | mid | 148.693 | 119.774 | 28.9185 |
| low | high | 564.048 | 530.343 | 33.7052 |
| base | low | -215.658 | -243.229 | 27.5716 |
| base | mid | 444.908 | 399.551 | 45.3571 |
| base | high | 697.991 | 664.762 | 33.2291 |
| high | low | 28.0629 | 2.46228 | 25.6006 |
| high | mid | 581.116 | 531.442 | 49.6737 |
| high | high | 747.022 | 719.075 | 27.9466 |

## 검산

| alternative | metric | actual | expected | passed | detail |
| --- | --- | --- | --- | --- | --- |
| D | B_fixed_active_2027 | 19 | 19 | 1 |  |
| D | B_fixed_active_2028 | 19 | 19 | 1 |  |
| D | B_fixed_active_2029 | 19 | 19 | 1 |  |
| D | B_fixed_active_2030 | 19 | 19 | 1 |  |
| D | B_fixed_active_2031 | 19 | 19 | 1 |  |
| D | B_saved_assignment_2027 | 0 | 0 | 1 |  |
| D | B_saved_assignment_2031 | 0 | 0 | 1 |  |
| D | nearest_assignment_errors | 0 | 0 | 1 |  |
| D | max_peak_before_expansion | 14.99 | 15 | 1 |  |
| D | max_peak_after_expansion | 19.96 | 20 | 1 |  |
| D | availability_violations | 0 | 0 | 1 |  |
| D | accepted_capacity_ratio_max | 1 | 1 | 1 |  |
| D | funding_krw | 1.53386e+10 | 5e+10 | 1 |  |
| D | all_moves_under_funding_limit | 1.53386e+10 | 5e+10 | 1 |  |
| D | all_moves_after_fixed_period | 2032 | 2032 | 1 |  |
| D | npv_cash_flow_sum | 3.99551e+10 | 3.99551e+10 | 1 |  |
| D | coverage_2027 | 0.803001 | 0.803001 | 1 |  |
| D | coverage_2032 | 0.960983 | 0.960983 | 1 |  |
| D | coverage_2037 | 0.970613 | 0.970613 | 1 |  |
| D | legacy_files_unchanged | 118 | 118 | 1 |  |
| D | legacy_comparison_rows_preserved | 24 | 24 | 1 |  |
