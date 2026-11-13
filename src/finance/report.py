"""저장된 검산 결과와 현재 코드 위치로 대조표·검증 보고서를 만든다."""
import json

import pandas as pd

from src.common.io import ROOT


def run():
    base = ROOT / "outputs/finance_validation"
    s = json.loads((base / "summary.json").read_text(encoding="utf-8"))
    w = json.loads((base / "workbook_check.json").read_text(encoding="utf-8"))
    if not w["all_match_to_won"]:
        raise ValueError("엑셀 검산 미통과")
    lines = (ROOT / "src/finance/engine.py").read_text(encoding="utf-8").splitlines()

    def link(token):
        line = next(i for i, text in enumerate(lines, 1) if token in text)
        return f"[engine.py:{line}](../src/finance/engine.py#L{line})"

    mapping = [
        ("1", "기간·시점 / 할인율", "2026년 말 t=0 포함 21행; 개점 투자·보증금은 직전 말, NPV는 t=0..20", "R01:", "a_npv, initial_prior_year"),
        ("2", "매출", "2027년 기준 장바구니·배달비 × 연 2% 지수; 배달비는 입력된 4만원 미만 비중만", "R02:", "엑셀 매출 행"),
        ("3", "매출원가 / 변동비", "15의 마진·수수료·단가 및 매출 기준 폐기·마케팅; 냉장/냉동 합집합 비중", "R03:", "엑셀 비용 행"),
        ("4", "거점 고정비", "03 임대·관리비, 04 거점 전체 고정비; 연 성장과 접안 할증", "R04:", "엑셀 고정비 행"),
        ("5", "5.6절 임대", "개점+5년 5%, +10년 시장 임대료, +15년 5%; 후발 개점 가격 지수", "R05:", "e_*, rent_5y/10y/15y"),
        ("6", "5.6절 재계약 거절", "expected는 동일 유형 대체 비용 기댓값을 재계약 연도 말 CF에서 차감; forced는 지정 장소로 실제 이전", "R06:", "forced_*, pilot_renewal_2037"),
        ("7", "본사비 / 개점 마케팅", "거점 수와 무관한 HQ, 신규 개점 연도 마케팅; 파일럿 매몰 제외", "R07/R08:", "a_hq_pv, 엑셀 HQ"),
        ("8", "설비투자", "초기 투자 10년 상각, 개점 10년 차 말 40% 재투자·다음 해부터 상각(설정 가능)", "R07/R08:", "refresh_*, terminal_residual_positive"),
        ("9", "운전자본(재고)", "원가 9일분; 그해 증감 CF, 폐점 80% 회수; 기본 신규 재고는 영업연도 말", "R09:", "d_inventory_*, 엑셀 재고"),
        ("10", "보증금 / 원상복구", "후발 보증금 가격지수; 반환은 손익 제외, 복구비는 EBITDA 비용", "R10/R12:", "d_deposit_return, d_restoration"),
        ("11", "법인세", "전사 기준 EBITDA-상각, 무기한 결손 이월; 미상각 장부가 공제는 기본 false", "R11:", "a_tax_zero, tax_*, terminal_writeoff_*"),
        ("12", "기간 종료", "2046년 말 전 거점 정산, 보증금·재고·복구 반영; 설비 매각가 없음", "R10/R12:", "d_terminal_cf_bridge, terminal_no_salvage"),
        ("13", "파일럿 P1·P2", "개점투자/마케팅 매몰, 보증금은 반환만; 미선택 파일럿은 t=0 철수; 재계약 2037", "R13:", "a_pilot_withdrawal, b 엑셀, pilot_renewal_2037"),
        ("14", "인건비·단가 상승", "위탁 2.5%, 직고용·피커 3.0% 별도 적용; ops 금액은 2027 기준", "R14:", "split_contract/employed/picker_inflation"),
        ("KPI", "6절 KPI / 자금 한도", "NPV 원 및 억 원, 최대 자금, 최초 회수 연도, 전사 EBITDA 최초 흑자", "# KPI:", "a_npv, c_npv_decreases, f_funding"),
    ]
    body = "# 부록 B 재무 규칙 ↔ 코드 대조표\n\n근거: 문제지 18쪽 부록 B, 9~10쪽 5.6절, 사용자 ⑥ 지시 및 추가 2항. 번호는 사용자 지시서의 1~14다. 단가 숫자를 코드에 복사하지 않고 15, 임대는 03, 유형별 비용은 04에서 읽는다.\n\n"
    body += "| 규칙 | 문제지 항목 | 구현 | 코드 위치 | 검증 |\n|---|---|---|---|---|\n"
    body += "\n".join(f"| {n} | {title} | {impl} | {link(token)} | {test} |" for n, title, impl, token, test in mapping)
    body += f"\n\n기댓값 비용 계산: {link('replacement =')}; HQ: {link('out[\"hq_cost\"] =')}; CF: {link('out[\"cash_flow\"] =')}.\n"
    body += "\n상세 가정과 설정은 `docs/assumptions.md`의 ⑥ 항목, 입력 계약은 `docs/interfaces.md`, 실행법은 `src/finance/README.md`를 따른다.\n"
    (ROOT / "outputs/finance_rule_map.md").write_text(body, encoding="utf-8")
    p1 = pd.read_csv(ROOT / "outputs/finance_p1_base.csv", encoding="utf-8-sig")
    p27, p28 = [p1.loc[p1.year.eq(y)].iloc[0] for y in (2027, 2028)]
    t = s["timing"]
    report = f"""# ⑥ 재무 검증 결과

검증일: 2026-11-13. 수치는 `outputs/finance_validation/summary.json`, `checks.csv`, `workbook_check.json` 및 연도별 결과 CSV에서 읽었다.

| 검증 | 실행 결과 |
|---|---|
| a 거점 0개 | HQ 현재가치 {s['a']['hq_npv_eok']:.9f}억 원 + 파일럿 철수 {s['a']['pilot_withdrawal_eok']:.4f}억 원 = NPV **{s['a']['npv_eok']:.9f}억 원**, 법인세 0원 |
| b P1 하루 900건 | 2027·2028년 {w['compared_cells']}개 셀 비교, 최대 차이 **{w['max_absolute_difference_krw']:.9f}원**. 주문 수 입력 변경 후 매출 재계산 확인 |
| c 할인율 상승 | 입주 가능한 {s['c']['sites']}곳 계획의 모든 영업연도 CF 양수. NPV 12% **{s['c']['npv_12_eok']:.6f}억 원 → 16% {s['c']['npv_16_eok']:.6f}억 원** |
| d 2046년 종료 | P1 보증금 {s['d']['deposit_returned']:,.0f}원 + 재고 회수 {s['d']['inventory_recovery']:,.6f}원 − 복구비 {s['d']['restoration']:,.0f}원이 CF에 포함됨; 재고·설비 기말 잔액 0 |
| e 2032년 개점 | {s['e']['site_id']}의 최초 월세·보증금·설비투자가 모두 **{s['e']['factor']:.10f}배 = 1.02^5** |
| f 모든 후보지 2027년 개점 | {s['f']['sites']}곳(파일럿 포함), 임시 900건/일·거점, 최대 자금 **{s['f']['max_funding_eok']:.2f}억 원**, 500억 이내. 입주 연도를 어긴 {s['f']['early_open_sites']}곳을 포함한 가상 검산이며 실행 가능 계획 아님 |

## P1 검산 주요 값

| 항목 | 2027년 원 | 2028년 원 | 2027년 억 원 | 2028년 억 원 |
|---|---:|---:|---:|---:|
| 매출 | {p27.revenue:,.6f} | {p28.revenue:,.6f} | {p27.revenue_eok:.6f} | {p28.revenue_eok:.6f} |
| EBITDA | {p27.ebitda:,.6f} | {p28.ebitda:,.6f} | {p27.ebitda_eok:.6f} | {p28.ebitda_eok:.6f} |
| 순현금흐름 | {p27.cash_flow:,.6f} | {p28.cash_flow:,.6f} | {p27.cash_flow_eok:.6f} | {p28.cash_flow_eok:.6f} |

본사비를 포함한 전사 기준. P2는 2027년 초 철수하므로 2026년 말 정산은 별도 행에 들어 있다. 검산 단가는 2027년 가격이고 위탁·직고용 비용을 분리했다. 해당 워크북은 초기 2개 영업연도 검산용이며 20년 전체 모델은 Python 엔진이다.

## 추가 검증과 성능

- 계산·경계 검증 **{s['checks_passed']}개 통과**: 위탁/직고용/피커 별도 상승, 5·10·15년 임대, 재투자 지급/상각 시점, 강제 이전 중복 비용 방지, 결손금 이월 및 입력 오류 거부 등.
- 2032년 개점 후보지의 종료 미상각 장부가 **{s['terminal_writeoff']['residual_book_value']:,.6f}원**. 기본은 추가 세무 공제 없음. 옵션을 켠 충분한 흑자 테스트에서는 법인세가 **{s['terminal_writeoff']['tax_saving_if_elected']:,.6f}원** 줄고 CF도 같은 금액만 증가했다. 장부가를 현금 유입/지출로 넣지 않았다.
- 초기 로딩 이후 {t['sites']}곳·ops {t['ops_rows']}행으로 {t['calls']}회 전체 평가(입력 검증·반환 표 포함): 평균 **{t['mean_seconds']:.6f}초**, 95백분위 **{t['p95_seconds']:.6f}초**, 최대 **{t['max_seconds']:.6f}초**. 목표 {t['target_seconds']:.1f}초, 전 호출 충족 여부 **{t['all_calls_within_target']}**. 해당 PC 실측이며 초기 디스크 로딩은 제외한다.
- `data/` 원본 **{s['source_files_unchanged']}개 파일 SHA256 변경 없음**.
- 워크북 수식은 Artifact Tool에서 재계산했고 수식 오류 검색 0건, 입력 변경 반응 및 저장 XLSX의 수식/캐시를 확인했다. 두 시트 모두 렌더링하여 확인했다. 데스크톱 Excel 자체 재계산은 실행하지 않았다.

## 해석상의 주의

- 운영값은 테스트용 가정이다. 실제 수요·비용·서비스 판정은 ②③⑤와 연결해야 한다.
- 회수기간은 최초 0 이상이라는 정의에 t=0을 포함한다. a·b는 파일럿 철수에 따른 t=0 유입 때문에 2026으로 표시되며, 이후 손실을 회수했다는 의미가 아니다.
- 부록 B에서 확정되지 않은 세무 처리·파일럿 재투자 기준 등의 기본값과 대안은 `docs/assumptions.md` 및 `config.json`에 명시했다.
"""
    (ROOT / "outputs/finance_verification.md").write_text(report, encoding="utf-8")
    print(f"규칙 대조 {len(mapping)}행, 검증 {s['checks_passed']}개, 엑셀 {w['compared_cells']}셀 차이 0원")


if __name__ == "__main__":
    run()
