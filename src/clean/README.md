# ① 데이터 검증·정제

저장소 루트에서 `python -m src.clean.run --config config.json`을 실행한다.
`python -m unittest src.clean.test_checks`는 원본 보존·승인 예외·동 코드와 구획 로더·오류 검출을 검산한다.
이후 단계는 `src.common.io.load_file()` 또는 `load_all()`의 기본 입력인 `outputs/clean/`을 사용한다.

## 실행 순서

1. data/ 전체 SHA256 기록 → 공용 로더로 18개 CSV와 지도 읽기.
2. config의 파일별 스키마를 바탕으로 범위·단위·결측·중복·좌표·시간대·상태값·관계 검사. 표에 해당 컬럼이 없으면 `not_applicable`로 기록한다. 모든 구획에 8종의 적용 여부가 남는다.
3. 지시된 결함의 원본 검출 건수를 대조한다. 새로운 위반이나 건수 차이는 중단하고 `clean_run_status.json`을 blocked로 기록한다.
4. 승인한 보정만 적용. 중복 제거·보정·상태 분리의 실제 건수는 `data_issues_log.csv`에 기록한다.
5. 본표와 분리본에 같은 검사 함수를 재실행. 승인된 잔존 값은 flag를 검증하고 미승인 위반과 구별한다.
6. 임시 폴더에 저장·다시 읽기·같은 검사와 값 일치 대조 후 정제본 발행. 원본 SHA256, 변경 금지 테이블, 벤치마크 원문, 주문별 금액 보존 및 행 수 균형을 검산한다.

## 공용 로더

```python
from src.common.io import (
    load_file, active_od, demand_orders, delivery_kpi_orders,
    rider_trip_orders, weight_analysis_orders,
)

dong = load_file("01_dong_master.csv")  # UTF-8 BOM, 코드 문자열
speed_sections = load_file("12_speed_profile.csv")  # {구획 제목: DataFrame}
benchmark = load_file("16_vrp_benchmark_orders.csv")
calculation_minutes = benchmark["order_min"]  # order_time은 표시용 원문
od = active_od()  # 원본 OD 값을 그대로 두고 정제 후보지에 대응하는 행만 선택
```

07·12·14는 기존 `src/travel_time.py::read_sectioned_csv()`를 호출한다.
해당 함수의 변경은 문자열 dtype·인코딩 지정과 파일 닫기뿐이며, 이동시간 계산식은 변경하지 않았다.
구획 파일을 복사할 때 표 없는 14번 [C] 설명행까지 보존한다.
기존 이동시간 검산은 `python src/check_travel_time.py outputs/clean outputs`로 실행한다.

비용·재무 값은 정제된 15번 파일을 참조한다. 시나리오·서비스 기준·배차 정책은 config에 모으되 이번 단계에서 결정하지 않는다.
검출용 서울 좌표 범위는 지도 폴리곤 판정을 의미하지 않는다. 모든 수치 검산은 `outputs/` 결과 파일에 남는다.

## 예외 및 건수 해석

`RAW_` 로그는 원본 관찰, 접두어 없는 로그는 실제 처리다. `REMOVE`와 `SPLIT`만 행 수 감소에 사용한다.
여러 규칙에 같은 주문이 걸릴 수 있으므로 단순 합계를 결함 주문 수로 해석하면 안 된다.
중량 미해결 주문은 수요에서 유지하고 라이더 트립·중량 분석에서 제외한다.
전달 시각 결측 주문은 수요에서 유지하고 배송시간 KPI에서 제외한다.
고정된 외부 퀵 요금이나 35kg 기준은 이상치 제거 기준으로 사용하지 않는다.
리포트의 잔여 0건은 승인된 예외의 flag와 사용 규칙까지 반영한 결과다.
