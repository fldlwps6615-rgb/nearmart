# ⑥ 20년 재무 엔진

`FinanceEngine`는 03·04·15·07 정제본과 `config.json`을 한 번 읽는다. `evaluate()`는 영업 시뮬레이션이나 입지 선택을 하지 않고, 주어진 개설 계획과 운영값의 재무만 계산한다.

```python
import pandas as pd
from src.finance import FinanceEngine

engine = FinanceEngine("config.json")
plan = pd.read_csv("outputs/finance_validation/plan_p1.csv", encoding="utf-8-sig", dtype={"site_id": str, "relocated_from": str})
ops = pd.read_csv("outputs/finance_validation/ops_p1.csv", encoding="utf-8-sig", dtype={"site_id": str})
annual, kpi = engine.evaluate(plan, ops, "base")
annual.to_csv("outputs/finance_example_base.csv", index=False, encoding="utf-8-sig")
print(kpi)
```

`from src.finance import evaluate`도 동일한 함수 계약을 제공한다. 이 편의 함수는 기본 config의 수정 시각이 바뀌면 캐시를 갱신한다. 데이터나 설정을 바꿨다면 새 `FinanceEngine`을 생성하는 것이 명확하다. 반복 평가 중 입력 계획과 ops는 매번 검증하며 수정하지 않는다.

입력 컬럼·분모·단위·시점은 `docs/interfaces.md`의 ⑥ 계약을 따른다. 부족한 연도나 중복, NaN, 음수, 범위 밖 비율, 유형/입주 연도 불일치는 보간하거나 삭제하지 않고 `outputs/data_issues_log.csv`에 기록한 뒤 `ValueError`로 중단한다. 실제 데이터 이슈와 의도적인 오류 검증 로그는 분리한다.

설정은 `config.json`의 `finance` 한 곳에 있다. `discount_rate`가 `"discount_rate"`이면 15의 값을 참조하며, 민감도 분석은 이 설정에 숫자를 입력한다. 장바구니·라이더·피커 입력 단가는 모두 **2027년 가격**이다. 라이더 단가는 전체 주문 수로 나눈 **위탁 지급액/전체 주문, 직고용 비용/전체 주문**이며 외부 퀵 비용은 제외한다. 상위 단계가 인건비를 이미 인상한 값을 넘기면 이중 인상되므로 금지한다.

`scenario_applied`는 시나리오가 반영된 연도별 수요를 그대로 사용한다. 2026년 수요 기준으로 단순 시장지수 전파를 시험하려는 경우에만 `demand_basis="index_2026"`으로 바꾼다. 07의 low/base/high 지수는 이 옵션에서만 곱한다. 운영 가능성·라이더 수급·서비스 비율의 현실성 검증은 ②③⑤의 책임이며 재무 엔진은 그 결과를 입력받는다.

강제 이전 예시:

```json
"nonrenewal_mode": "forced",
"forced_relocations": [{"from_site_id": "P1", "to_site_id": "C001"}]
```

위 설정은 P1을 2037년 초 철수시키고 C001을 2037년 초 연다. 목적지를 자동 선정하지 않는다. `ops`는 변환된 실제 영업기간에 맞게 **P1 2027~2036년, C001 2037~2046년**을 명시해야 한다. 목적지가 plan에 있으면 개점연도와 `relocated_from`이 일치해야 한다. 그렇지 않으면 거부한다. 초기 투자·보증금 지급과 원거점 정산은 2036년 말, 새 개점 마케팅은 2037년 비용이다. 기댓값 비용을 중복 가산하지 않는다.

## 재실행

1. `python -m src.finance.verify --config config.json`
2. 제공된 Node.js 런타임으로 `src/finance/build_check.mjs` 실행 (`@oai/artifact-tool` 필요).
3. `python -m src.finance.report`

엔진은 pandas/numpy만 필요하다. 엑셀 작성 시 사용한 Node 패키지는 Codex 번들 라이브러리이며 `src/finance/node_modules`는 읽기 전용 번들로 연결된 로컬 junction이다. 다른 환경에서는 해당 환경의 Artifact Tool 런타임을 연결한다. 엔진 실행에 Node는 필요하지 않다.

검증 출력: `outputs/finance_validation/summary.json`, `checks.csv`, `workbook_check.json`, `outputs/finance_verification.md`. 워크북은 `report/finance_check.xlsx`이며 P1의 2027·2028년 손익·현금흐름을 실제 셀 수식으로 계산한다. 입력 편집 후 재계산도 확인한다. 비교용 엔진 숫자는 생성 시점의 스냅샷이므로 운영 입력 변경 시 위 순서로 다시 생성한다. 데스크톱 Excel 자체의 재계산은 별도로 실행하지 않았다.

검증 f는 입주 가능 연도만 해제한 **가상 스트레스 검산**이다. 실제 개설안으로 사용할 수 없으며, 그 옵션의 기본값은 false다. 임시 900건/일, 장바구니·비중·라이더 구성 등은 `finance.validation`에서 확인한다. 검산 결과는 실제 수요 분석이나 수익성 전망이 아니다.

회수기간은 지시서 문구대로 초기시점까지 포함하여 누적 할인 현금흐름이 처음 0 이상인 연도를 반환한다. 따라서 파일럿 철수로 t=0에 소액 유입이 생기는 손실 계획도 2026으로 표시될 수 있다. 이후 다시 음수가 되는지와 NPV를 함께 봐야 한다.
