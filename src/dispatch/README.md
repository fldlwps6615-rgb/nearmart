# ④ 배차 엔진과 벤치마크

Python 3.14, pandas, numpy를 사용한다. 모든 CSV는 UTF-8 BOM이다.

```
python -m src.dispatch.run --config config.json
python -m src.dispatch.verify_t3 --config config.json
python -m src.dispatch.test_checks
python -m src.dispatch.diagnose
python -m src.dispatch.report
```

`config.json/dispatch`만 바꾸어 정책·시간별 인원·탐색 범위를 변경한다.
거친 격자는 고정 시드로 순서를 섞어 평가하고 상위 후보의 W·여유분·묶음 크기·시간별
위탁/피커 인원·시간별 W·직고용 시작/길이/인원 이웃을 세밀 탐색한다.
인스턴스별 제한은 600초이고, 진행 중 후보를 중단하여 마지막 **완료된** 후보에서 선택한다.
실행시간 제한 때문에 가능한 모든 시간별 인원 조합을 전수 탐색한 결과는 아니다.
실행 가능 후보에서는 주문당 비용이 먼저이며, 같은 비용이면 더 적은 위탁 접속시간을 택한다.
최소 인원은 탐색한 조합 범위에서만 해석한다.

출력은 `report/T3_benchmark_dispatch.csv`, `outputs/bm_kpi.csv`, `outputs/bm_staffing.csv`다.
T3에는 인스턴스별 선택 정책 하나만 넣고, 정책별 T3와 근무·피킹·체류·트립 기록은
`outputs/dispatch/<instance>/<policy>/`에 보존한다. `search.csv`는 모든 완료/미완료 후보,
`closest_three.csv`는 우선순위상 가장 가까운 세 후보다. 실행 불가 정책도 비교 KPI에 표시한다.
`run_status.json`과 `dispatch_selected_verification.csv`를 함께 확인해야 한다.

검증기는 엔진을 다시 실행하지 않고 저장된 파일을 다시 읽는다. 원주문과 고정 파라미터는
모든 검사에 필요한 참조 입력이다. a·b·c·e·f·g·h는 `T3+고정입력` 그룹이며,
e는 T3로 드러나는 최소 체류, g는 10명 상한 검사다. 최초 출발 전·마지막 복귀 후·
무배송 라이더의 대기(e_full), 시간별 피커 계획과 개인 배정(g_staffing)은 근무기록이 필요하다.
d·i·수급 및 이 보완 검사는 `인력표·근무기록` 그룹이다. T3만으로 알 수 없는 항목을 통과로 추정하지 않는다.

⑤ 재사용 계약:

```python
from src.dispatch import DispatchInputs, DispatchEngine
inputs = DispatchInputs(config)
engine = DispatchEngine(inputs, orders, meta)
result = engine.simulate(plan)
```

`orders`는 정제 16의 주문·위치·피킹·중량·건물 컬럼을, `meta`는 instance, hub_lat,
hub_lon, hub_gu, weather, rider_area_class, bike_bays를 포함한다. `plan`은 저장된
plan.json과 같은 policy, target, wait, wait_by_hour, margin, pickers(시간→명),
contract(시간→명), employed_blocks(start_hour,hours,count)를 받는다.
계획 작성은 일별 주문 정보를 활용하지만 **배차는 해당 시각까지 피킹 완료된 주문만** 본다.
피킹은 FIFO이며 시간대별 고정 인력으로 미리 계산한 독립 사건열을 배차 사건열에 합친다.
배차에는 피킹 완료·복귀·교대·대기 타이머 사건이 사용된다.

35kg 초과 주문이 있을 때는 `external_delivery(order_record, pick_end_min)` 콜백이
외부 퀵 완료 시각을 제공해야 한다. 미제공이면 실패하며 가상의 완료시각을 만들지 않는다.
외부 퀵은 result.external로 별도 반환하고 25,000원을 비용에 포함한다.
현재 BM 입력에는 외부 퀵 주문이 없으며, T3의 rider_type은 contract/employed만 허용한다.
⑤에서 외부 퀵이 생기면 별도 외부 퀵 결과와 내부 T3를 합쳐 전체 주문 완전성을 검증해야 한다.
확률 이동시간 어댑터는 ⑤에서 별도 주입하며 ④ 벤치마크는 항상 결정론적이다.

금액은 15의 2027년 값만 사용한다. 수급 17,860원은 지시서가 지정한 기준값이다.
직고용 비용에는 시급×1.35, 배송 인센티브, 차량 월비용/174시간을 포함한다.
근무한 모든 라이더(배송 0건 포함)의 일별 배송 건수에 모집단 표준편차로 CV를 계산한다.
23:30 이후 잔여 업무도 계속 처리한다. 피커는 마지막 피킹 완료, 시간제 위탁은 마지막
라이더 복귀 후 다음 정시에 종료하며 24:00 이전으로 조기 종료하지 않는다. 직고용은
4/8시간 블록을 끝까지 비용·공간에 반영한다. 계획의 시간별 값은 가용 상한이고,
bm_staffing/work에는 이 종료 규칙 적용 후 실제 유급 시간이 기록된다.
지연이 누적된 기준 정책도 모든 주문을 배송하기 위해 최대 48:00까지 잔여 업무를 처리한다.
48:00에도 미배송이 있으면 해당 후보는 incomplete로 기록한다. 24:00 이후 공급 상한은
21~24시 값을 연장하고 이동속도는 실제 자정 기준 시각을 24시간으로 순환한다.
불완전 후보는 누락 주문을 버리지 않고 실패로 기록한다. 원자료는 변경하지 않는다.
