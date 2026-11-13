"""결과 파일에서 검산 보고·파일럿 데이터 진단 생성."""
from src.common.io import ROOT, load_config
import pandas as pd


def write_reports(cfg=None):
    cfg=cfg or load_config(); output=ROOT/cfg["paths"]["outputs"]
    def read(name):
        return pd.read_csv(output/name,encoding=cfg["io"]["encoding"],
                           dtype={c:str for c in cfg["io"]["string_columns"]})
    m=read("clean_verification_summary.csv").set_index("metric").value
    rows=read("clean_rowcount.csv").set_index("file")
    dist=read("clean_candidate_distribution.csv").set_index("hub_type").n_sites
    log=read("data_issues_log.csv")
    actual=log[~log.action.str.startswith("OBSERVE |")].set_index("rule_id")
    o=rows.loc["05_pilot_orders.csv"]; s=rows.loc["03_candidate_sites.csv"]
    n=lambda key:int(m[key])
    text=f'''# ① 데이터 검증·정제 검산

원본 CSV {n('raw_csv_files')}개 → 정제 CSV {n('clean_csv_files')}개(원본 대응 18개 + TEST·CANCELLED 분리본 2개). GeoJSON {n('geo_dong_count')}개 동 코드는 01과 일치하며 지도 파일은 그대로 복사했다.

## 직접 검산

- 주문: {int(o.before_rows):,} − 중복 {int(o.removed_rows):,} − TEST {int(actual.loc['05_TEST','n_rows']):,} − CANCELLED {int(actual.loc['05_CANCELLED','n_rows']):,} = **{int(o.after_rows):,}행**.
- 후보지: {int(s.before_rows)} − 서울 밖 1 − 중복 매물 2 = **{int(s.after_rows)}곳**. S {int(dist['S'])} + M {int(dist['M'])} + L {int(dist['L'])} = {int(dist.sum())}.
- 18개 파일 모두 정제 전−후−삭제−분리 = 0. 절대 차이 합 {n('rowcount_balance')}행.
- 8종 규칙 재검사: 미승인 잔여 {n('clean_residual_rows')}건, 저장 후 재읽기 검사 {n('persisted_residual_rows')}건. 분리본에도 같은 검사를 적용했다.
- data/ 전체 {n('raw_data_files_unchanged')}개 파일 SHA256 보존. 13 OD·15 비용 표를 포함한 변경 대상 외 테이블 값 보존. 벤치마크 원본 컬럼 전부 보존.
- 활성 OD {n('candidate_count')} × {n('geo_dong_count')} = {n('active_od_rows'):,}행. 정제된 13 파일은 제외 후보지 OD까지 보존하므로 active_od()를 사용한다.

## 승인된 잔존 값

- 전달 시각 결측 {n('delivered_missing')}건: 수요 포함, 배송시간 KPI 제외. 배송시간 KPI 대상 {n('orders_clean'):,} − {n('delivered_missing')} = {n('delivery_kpi_orders'):,}건.
- 중량 미해결 {n('weight_unresolved')}건: 결측과 weight_unresolved=1 보존; 외부 퀵 수요 포함, 라이더 트립·중량 분석 제외.
- 벤치마크 표기 차이 {n('benchmark_time_mismatch')}건: order_time 원문 보존, time_mismatch=1; 계산은 order_min. 초 60 반올림 시 분 올림 누락이라는 사용자 판단을 처리 로그에 기록했다. 실제 차이는 0.99~1.00분이다.
- 따라서 잔여 0건은 모든 원문 결측이 사라졌다는 뜻이 아니라 승인된 예외까지 적절히 표시·분리했다는 뜻이다.

## 원본 관찰과 실제 처리 건수 차이

중복 제거 전에 중량 음수 81행·중량 결측 239행·CANCELLED 1,563행·전달 시각 결측 778행이 검출됐다.
중복 제거 후 중량 음수 {int(actual.loc['05_WEIGHT_NEGATIVE','n_rows'])}행을 결측으로 바꾸었고, 중량 {int(actual.loc['05_WEIGHT_IMPUTED','n_rows'])}행을 추정하고 1행은 결측으로 유지했다.
실제 CANCELLED 분리본은 {int(actual.loc['05_CANCELLED','n_rows']):,}행, 전달 시각 결측은 {n('delivered_missing')}행이다.
시각 보정은 P2 2026-08-20의 {int(actual.loc['05_SHIFT_ROWS','n_rows'])}행 모든 시각 컬럼에 +9시간 적용했다.
RAW_ 로그는 원본 관찰이고 UPDATE/FLAG/REMOVE/SPLIT 로그는 실제 처리다. 서로 합산하지 않는다.

## 수요 장바구니 합계 보존

| 대상 | 원 | 억 원 |
|---|---:|---:|
| 수요 포함 {n('orders_clean'):,}건 장바구니 합계 | {m['demand_basket_sum_krw']:,.0f} | {m['demand_basket_sum_krw']/100000000:.6f} |

본표와 두 분리본을 합쳤을 때 중복 제거 원본의 주문 ID 집합과 주문별 장바구니·배달비·라이더 지급액·지연 쿠폰 값이 모두 일치한다.

## 재현 및 근거

- 실행: `python -m src.clean.run --config config.json`
- 독립 검산: `python -m unittest src.clean.test_checks`
- 이동시간: `python src/check_travel_time.py outputs/clean outputs`
- 결과 근거: clean_rowcount.csv, clean_candidate_distribution.csv, clean_validation_raw.csv, clean_validation_after.csv, clean_verification_summary.csv, clean_source_hashes.csv, data_issues_log.csv.
- 별도 검증 기록: clean_tests.json / clean_tests.txt, travel_check_result.csv.
- 파일별 적용 불가 검사 항목은 not_applicable로 명시했다. GeoJSON은 요청 범위인 코드 대응만 검사했다.
'''
    diagnosis=f'''## 파일럿 데이터 품질 진단

1. 주문 건수는 완전 중복 때문에 원본에서 {int(o.removed_rows)}행 과다 집계될 수 있다(근거: {int(o.before_rows):,}행 중 고유 주문 {n('orders_deduplicated'):,}행).
2. 수요 집계는 TEST·CANCELLED가 섞여 있기 때문에 상태 구분이 필요하다(근거: 중복 제거 후 TEST {int(actual.loc['05_TEST','n_rows'])}행·CANCELLED {int(actual.loc['05_CANCELLED','n_rows']):,}행 분리, 수요 본표 {n('orders_clean'):,}행).
3. 배송시간 KPI는 전달 시각 결측 때문에 수요와 분모가 다르다(근거: 수요 포함 {n('orders_clean'):,}건 중 {n('delivered_missing')}건 제외, KPI 대상 {n('delivery_kpi_orders'):,}건).
4. 시간대별 수요는 P2의 하루 전체 시각이 9시간 빠르기 때문에 보정이 필요하다(근거: 2026-08-20의 {int(actual.loc['05_SHIFT_ROWS','n_rows'])}행 시각에 +9시간 적용).
5. 중량 분석은 결측·음수 때문에 보정 및 별도 제외가 필요하다(근거: 중복 제거 후 {int(actual.loc['05_WEIGHT_IMPUTED','n_rows'])}행 추정, 외부 퀵 중량 미해결 {n('weight_unresolved')}건은 수요에만 유지).

근거 파일: data_issues_log.csv, clean_rowcount.csv, clean_verification_summary.csv.
'''
    (output/"clean_verification.md").write_text(text+"\n"+diagnosis,encoding="utf-8")


if __name__=="__main__":
    write_reports()
