# 부록 B 재무 규칙 ↔ 코드 대조표

근거: 문제지 18쪽 부록 B, 9~10쪽 5.6절, 사용자 ⑥ 지시 및 추가 2항. 번호는 사용자 지시서의 1~14다. 단가 숫자를 코드에 복사하지 않고 15, 임대는 03, 유형별 비용은 04에서 읽는다.

| 규칙 | 문제지 항목 | 구현 | 코드 위치 | 검증 |
|---|---|---|---|---|
| 1 | 기간·시점 / 할인율 | 2026년 말 t=0 포함 21행; 개점 투자·보증금은 직전 말, NPV는 t=0..20 | [engine.py:235](../src/finance/engine.py#L235) | a_npv, initial_prior_year |
| 2 | 매출 | 2027년 기준 장바구니·배달비 × 연 2% 지수; 배달비는 입력된 4만원 미만 비중만 | [engine.py:267](../src/finance/engine.py#L267) | 엑셀 매출 행 |
| 3 | 매출원가 / 변동비 | 15의 마진·수수료·단가 및 매출 기준 폐기·마케팅; 냉장/냉동 합집합 비중 | [engine.py:276](../src/finance/engine.py#L276) | 엑셀 비용 행 |
| 4 | 거점 고정비 | 03 임대·관리비, 04 거점 전체 고정비; 연 성장과 접안 할증 | [engine.py:292](../src/finance/engine.py#L292) | 엑셀 고정비 행 |
| 5 | 5.6절 임대 | 개점+5년 5%, +10년 시장 임대료, +15년 5%; 후발 개점 가격 지수 | [engine.py:298](../src/finance/engine.py#L298) | e_*, rent_5y/10y/15y |
| 6 | 5.6절 재계약 거절 | expected는 동일 유형 대체 비용 기댓값을 재계약 연도 말 CF에서 차감; forced는 지정 장소로 실제 이전 | [engine.py:162](../src/finance/engine.py#L162) | forced_*, pilot_renewal_2037 |
| 7 | 본사비 / 개점 마케팅 | 거점 수와 무관한 HQ, 신규 개점 연도 마케팅; 파일럿 매몰 제외 | [engine.py:312](../src/finance/engine.py#L312) | a_hq_pv, 엑셀 HQ |
| 8 | 설비투자 | 초기 투자 10년 상각, 개점 10년 차 말 40% 재투자·다음 해부터 상각(설정 가능) | [engine.py:312](../src/finance/engine.py#L312) | refresh_*, terminal_residual_positive |
| 9 | 운전자본(재고) | 원가 9일분; 그해 증감 CF, 폐점 80% 회수; 기본 신규 재고는 영업연도 말 | [engine.py:335](../src/finance/engine.py#L335) | d_inventory_*, 엑셀 재고 |
| 10 | 보증금 / 원상복구 | 후발 보증금 가격지수; 반환은 손익 제외, 복구비는 EBITDA 비용 | [engine.py:346](../src/finance/engine.py#L346) | d_deposit_return, d_restoration |
| 11 | 법인세 | 전사 기준 EBITDA-상각, 무기한 결손 이월; 미상각 장부가 공제는 기본 false | [engine.py:358](../src/finance/engine.py#L358) | a_tax_zero, tax_*, terminal_writeoff_* |
| 12 | 기간 종료 | 2046년 말 전 거점 정산, 보증금·재고·복구 반영; 설비 매각가 없음 | [engine.py:346](../src/finance/engine.py#L346) | d_terminal_cf_bridge, terminal_no_salvage |
| 13 | 파일럿 P1·P2 | 개점투자/마케팅 매몰, 보증금은 반환만; 미선택 파일럿은 t=0 철수; 재계약 2037 | [engine.py:245](../src/finance/engine.py#L245) | a_pilot_withdrawal, b 엑셀, pilot_renewal_2037 |
| 14 | 인건비·단가 상승 | 위탁 2.5%, 직고용·피커 3.0% 별도 적용; ops 금액은 2027 기준 | [engine.py:285](../src/finance/engine.py#L285) | split_contract/employed/picker_inflation |
| KPI | 6절 KPI / 자금 한도 | NPV 원 및 억 원, 최대 자금, 최초 회수 연도, 전사 EBITDA 최초 흑자 | [engine.py:382](../src/finance/engine.py#L382) | a_npv, c_npv_decreases, f_funding |

기댓값 비용 계산: [engine.py:309](../src/finance/engine.py#L309); HQ: [engine.py:353](../src/finance/engine.py#L353); CF: [engine.py:375](../src/finance/engine.py#L375).

상세 가정과 설정은 `docs/assumptions.md`의 ⑥ 항목, 입력 계약은 `docs/interfaces.md`, 실행법은 `src/finance/README.md`를 따른다.
