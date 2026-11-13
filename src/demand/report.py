"""저장된 수요 결과의 한국어 설명과 검산 보고서."""
import pandas as pd


def md_table(frame):
    def cell(value):
        if pd.isna(value):
            return "—"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value).replace("|", "/")
    return "\n".join(["| " + " | ".join(map(str, frame.columns)) + " |",
                      "| " + " | ".join(["---"]*len(frame.columns)) + " |",
                      *["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False, name=None)]])


def write_report(cfg, output, training, rates, model, threshold):
    # 수치 출처는 같은 실행에서 저장한 CSV이며 보고서에도 파일명을 명시한다.
    candidates = pd.read_csv(output.path / "demand_ramp_candidates.csv", encoding="utf-8-sig")
    expectations = pd.read_csv(output.path / "demand_expectation_check.csv", encoding="utf-8-sig")
    checks = pd.read_csv(output.path / "demand_verification.csv", encoding="utf-8-sig")
    pilot = pd.read_csv(output.path / "demand_pilot_comparison.csv", encoding="utf-8-sig")
    sensitivity = pd.read_csv(output.path / "demand_ramp_sensitivity.csv", encoding="utf-8-sig")
    impact = sensitivity.set_index("metric").value
    chosen = candidates[candidates.selected.eq(1)].iloc[0]
    accepted = candidates[candidates.accepted.eq(1)]
    yearly = pd.read_csv(output.path / "demand_year_summary.csv", encoding="utf-8-sig")
    base = yearly[yearly.scenario.eq("base")]
    first, last = base.iloc[0], base.iloc[-1]
    report = f"""# ② 수요예측 결과

## 재실행 및 해석

`python -m src.demand.run --config config.json`
`demand_run_status.json`이 complete인 결과만 다음 단계에서 사용한다.
전망은 2027~2046년 20개 연도이며, 2026년은 검증 기준으로 함께 저장했다.
`scenario`는 시장 low/base/high이고, 각 행의 low/mid/high는 별도의 주문율·램프업 불확실성이다.
`orders_*`는 연간 주문 합이 아니라 그 연도의 **하루 잠재 주문**이다.

## 램프업과 효과 분리

거점별 절편, 요일 6개, 날씨, 공휴일 유무, 프로모션 유무를 로그 주문에 적합했다.
두 거점의 램프업 a·τ는 공유한다. 개점일은 0일이며, τ마다 a를 [0, 0.999999]에서 적합했다.
RMSE 비교 척도는 로그 주문이며 주문 건수 척도 RMSE도 공개한다.
허용 후보는 최저 RMSE의 {cfg['demand']['rmse_accept_ratio']:.2f}배 이내다.
중앙 τ={chosen.tau:g}일, a={chosen.a:.6f}; 허용 τ={', '.join(str(int(v)) for v in accepted.tau)}일.
성숙÷90일차와 성숙÷마지막 주는 다른 값이다. 마지막 주는 거점별 관측 마지막 7일의 램프업 평균이며,
통합 배수는 거점 성숙 주문을 가중치로 계산한다. 날씨·요일·프로모션이 섞인 실제 마지막 주 주문을 분모로 사용하지 않는다.

출처: `demand_ramp_candidates.csv`

{md_table(candidates)}

## 기대 범위 비교

출처: `demand_expectation_check.csv`. 점 추정 기대값에는 ±10% 진단 범위를 적용했다.
사전 성숙 배수는 하한 1.14 / 중앙 1.32 / 상한 1.55이며 추정치를 그 값에 맞추지 않았다.
P1·P2의 관측 주문율은 문제지 1.2절 개점일과 8.3절 서비스 권역을 기준으로 다시 계산했다.
사전 범위에 사용한 세부 권역과 기간은 제공 문서에서 확인되지 않아, 아래에서 정의별 영향을 비교한다.

{md_table(expectations)}

### P1·P2 권역 및 기간 검산

분자는 같은 기간·권역에 접수된 DELIVERED 주문(외부 퀵 포함), 분모는 01의 동일 권역 세대수와 실제 영업일이다.
`full_service_radius`는 13의 도로거리 반경, `full_observed_dongs`는 관측 주문이 있는 동이다.
`common_dates_service_radius`는 두 거점이 모두 영업한 기간이며, `last_week_service_radius`는 거점별 마지막 7영업일이다.
출처: `demand_pilot_comparison.csv`.

{md_table(pilot.drop(columns=['road_radius_km', 'orders_outside_dongs', 'same_dong_sets']))}

두 권역 정의의 동 목록이 같고 공통 기간·마지막 주로 좁혀도 사전 하한을 밑돌아, 확인한 정의에서는 관측 주문 밀도 자체가 낮다. 사전값의 정확한 집계 정의는 미확인이다.

### 2026년 서울 잠재 주문의 램프업 민감도

같은 90일차 기준 수요 {impact['same_day90_anchor']:,.2f}건/일에 사전 성숙 배수 {impact['prior_mature_div_day90']:.2f}를 적용하면 {impact['with_prior_ramp']:,.2f}건/일이다.
적합 배수 {impact['fitted_mature_div_day90']:.6f}를 적용하면 {impact['with_fitted_ramp']:,.2f}건/일로 {impact['fitted_minus_prior']:+,.2f}건/일 증가한다.
기대 상한과의 차이는 {impact['prior_minus_upper']:+,.2f} + {impact['fitted_minus_prior']:,.2f} = {impact['fitted_minus_upper']:+,.2f}건/일이다.
적합한 서울 주문 수에서 90일차 수준을 역산하고 성숙 환산 배수만 바꾼 민감도다. 거점·동 모형과 다른 계수, 배포 전망은 그대로 둔다.
출처: `demand_ramp_sensitivity.csv`.

{md_table(sensitivity)}

## 주문율 및 세 계수 검산

`demand_pilot_dong.csv`에 원시 주문 수, 유효 노출일, 성숙 하루 주문, 세대수와 세 계수를 모두 저장했다.
성숙 하루 주문 = 기간 주문 ÷ Σ(요일·날씨·공휴일·프로모션 계수 × 램프업 수준).
요일 계수의 7일 산술평균은 1이므로 표준 주간 하루 평균이며, 맑음·비공휴일·비프로모션 기준이다.
기준 주문율 = 성숙 하루 주문 ÷ 세대수 ÷ 경쟁 계수 ÷ 배송시간 계수 ÷ 구색 계수.
배송시간은 기존 KPI 규약에 따라 외부 퀵과 전달시각 결측을 제외한 관측 주문~전달 시간의 동별 평균이다.
그 주문들은 수요 및 주문 프로필에는 포함되어 있다.
배송시간 계수 분포: {training.delivery_factor.value_counts().sort_index().to_dict()}.
구색 계수: {sorted(float(v) for v in training.assortment_factor.unique())}.

{md_table(checks[checks.metric.eq('three_factor_division')])}

## 지역 모형과 불확실성

선택 변수: {', '.join(model['variables']) or '절편만'}.
6개 후보 특성의 0~3변수 조합을 로그 주문율의 LOOCV RMSE로 선택했다.
서울 전체 동을 같은 변환으로 평가한다. 세대 밀도는 log(2026년 세대수/면적)이다.
모형 변수에 1인가구 비율이 포함되면 장래 %p/100을 더해 예측값과 구간을 다시 계산한다.
다른 특성은 2026년 값에 고정한다.
예측구간은 잔차 분산과 새 동의 레버리지를 포함한 Student-t 기반 90% 구간이며, 로그 재변환에는 잔차 smearing을 적용한다.
최종 low/high 주문율은 이 구간에 허용 τ의 성숙÷마지막주 배수를 **중앙 배수로 나눈 상대값**을 곱한다.
이미 중앙 모형에서 성숙화했으므로 절대 성숙 배수를 다시 곱하지 않는다.
이 복합 범위는 모형 선택·시간 효과 추정·외삽의 모든 불확실성을 보장하는 90% 보장구간은 아니다.
외삽 표시는 선택 변수 중 하나라도 파일럿 최솟값~최댓값 밖이면 1이다. 다변량 볼록껍질 검사는 아니다.
외삽 {int(rates.extrapolated.sum())}/{len(rates)}개 동이며 동을 삭제하거나 임의로 범위를 절단하지 않았다.
파일럿은 2개 거점의 인접 동이므로 공간적 상관과 지역 선택 편향이 남는다.
`demand_pilot_predictions.csv`는 선택 변수 고정 LOOCV와 변수 선택도 fold 내부에서 다시 수행한 nested LOOCV를 모두 제공한다.
기준 주문율은 중앙 램프업으로 먼저 계산되므로 이 검증은 지역 모형 검증이며 전체 추정 과정의 독립 검증은 아니다.

## 20년 합계와 시장지수

시장지수 외의 변화는 구별 세대지수, 선택 시 1인가구 비율 변화, 경쟁사 개폐점이다.
base의 2026→2046 주문 배율 {last.orders_mid/first.orders_mid:.4f} = 시장지수 배율 {last.market_index/first.market_index:.4f}
× 시장 외 배율 {last.orders_without_market/first.orders_without_market:.4f}.
그래프 `demand_year_trends.png`는 수요와 시장지수를 비교하고 `demand_trend_decomposition.png`는 시장 외 변화를 분해한다.
`demand_year_summary.csv`에서 시장지수를 제거한 수요를 다시 곱해 연도별 합계를 검산했다.
rain_day_share={cfg['demand']['rain_day_share']}는 설정값만 기록하고 연간 수요에 곱하지 않는다.
날씨 계수는 `demand_effects.csv`에서 ⑤의 우천 대표일로 전달한다.

## 시간대 및 주문 프로필

1인가구 비율 기준은 서울 427개 동의 비가중 중앙값 {threshold:.6f}이다.
중앙값 이상은 single_high, 미만은 single_low다. 평일/주말은 월~금/토·일이며 공휴일도 실제 요일로 분류한다.
09~23시 15개 구간의 관측 주문 비중이고 23시 구간은 23:30 접수 마감까지다.
`order_profile.csv`의 ALL/ALL은 전체 주문 가중 통계이며 나머지 4개 행은 시간대 패턴과 같은 그룹이다.
cold_order_share는 냉장 또는 냉동의 합집합이며 두 비율을 단순 합산하지 않는다.
금액은 원이며 평균 장바구니의 억 원 표기도 함께 제공한다. 외부 퀵 비율은 실제 EXT-QUICK 기록 기준이다.
관측 프로필은 계절·날씨·프로모션 구성을 제거하지 않은 파일럿 분포다.

## 직접 실행한 검산

출처: `demand_verification.csv`

{md_table(checks)}
"""
    (output.path / "demand_validation.md").write_text(report, encoding="utf-8")
