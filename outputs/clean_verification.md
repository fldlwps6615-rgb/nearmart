# ① 데이터 검증·정제 검산

원본 CSV 18개 → 정제 CSV 20개(원본 대응 18개 + TEST·CANCELLED 분리본 2개). GeoJSON 427개 동 코드는 01과 일치하며 지도 파일은 그대로 복사했다.

## 직접 검산

- 주문: 79,539 − 중복 237 − TEST 24 − CANCELLED 1,561 = **77,717행**.
- 후보지: 71 − 서울 밖 1 − 중복 매물 2 = **68곳**. S 28 + M 27 + L 13 = 68.
- 18개 파일 모두 정제 전−후−삭제−분리 = 0. 절대 차이 합 0행.
- 8종 규칙 재검사: 미승인 잔여 0건, 저장 후 재읽기 검사 0건. 분리본에도 같은 검사를 적용했다.
- data/ 전체 24개 파일 SHA256 보존. 13 OD·15 비용 표를 포함한 변경 대상 외 테이블 값 보존. 벤치마크 원본 컬럼 전부 보존.
- 활성 OD 68 × 427 = 29,036행. 정제된 13 파일은 제외 후보지 OD까지 보존하므로 active_od()를 사용한다.

## 승인된 잔존 값

- 전달 시각 결측 777건: 수요 포함, 배송시간 KPI 제외. 배송시간 KPI 대상 77,717 − 777 = 76,940건.
- 중량 미해결 1건: 결측과 weight_unresolved=1 보존; 외부 퀵 수요 포함, 라이더 트립·중량 분석 제외.
- 벤치마크 표기 차이 26건: order_time 원문 보존, time_mismatch=1; 계산은 order_min. 초 60 반올림 시 분 올림 누락이라는 사용자 판단을 처리 로그에 기록했다. 실제 차이는 0.99~1.00분이다.
- 따라서 잔여 0건은 모든 원문 결측이 사라졌다는 뜻이 아니라 승인된 예외까지 적절히 표시·분리했다는 뜻이다.

## 원본 관찰과 실제 처리 건수 차이

중복 제거 전에 중량 음수 81행·중량 결측 239행·CANCELLED 1,563행·전달 시각 결측 778행이 검출됐다.
중복 제거 후 중량 음수 80행을 결측으로 바꾸었고, 중량 316행을 추정하고 1행은 결측으로 유지했다.
실제 CANCELLED 분리본은 1,561행, 전달 시각 결측은 777행이다.
시각 보정은 P2 2026-08-20의 271행 모든 시각 컬럼에 +9시간 적용했다.
RAW_ 로그는 원본 관찰이고 UPDATE/FLAG/REMOVE/SPLIT 로그는 실제 처리다. 서로 합산하지 않는다.

## 수요 장바구니 합계 보존

| 대상 | 원 | 억 원 |
|---|---:|---:|
| 수요 포함 77,717건 장바구니 합계 | 1,912,787,400 | 19.127874 |

본표와 두 분리본을 합쳤을 때 중복 제거 원본의 주문 ID 집합과 주문별 장바구니·배달비·라이더 지급액·지연 쿠폰 값이 모두 일치한다.

## 재현 및 근거

- 실행: `python -m src.clean.run --config config.json`
- 독립 검산: `python -m unittest src.clean.test_checks`
- 이동시간: `python src/check_travel_time.py outputs/clean outputs`
- 결과 근거: clean_rowcount.csv, clean_candidate_distribution.csv, clean_validation_raw.csv, clean_validation_after.csv, clean_verification_summary.csv, clean_source_hashes.csv, data_issues_log.csv.
- 별도 검증 기록: clean_tests.json / clean_tests.txt, travel_check_result.csv.
- 파일별 적용 불가 검사 항목은 not_applicable로 명시했다. GeoJSON은 요청 범위인 코드 대응만 검사했다.

## 파일럿 데이터 품질 진단

1. 주문 건수는 완전 중복 때문에 원본에서 237행 과다 집계될 수 있다(근거: 79,539행 중 고유 주문 79,302행).
2. 수요 집계는 TEST·CANCELLED가 섞여 있기 때문에 상태 구분이 필요하다(근거: 중복 제거 후 TEST 24행·CANCELLED 1,561행 분리, 수요 본표 77,717행).
3. 배송시간 KPI는 전달 시각 결측 때문에 수요와 분모가 다르다(근거: 수요 포함 77,717건 중 777건 제외, KPI 대상 76,940건).
4. 시간대별 수요는 P2의 하루 전체 시각이 9시간 빠르기 때문에 보정이 필요하다(근거: 2026-08-20의 271행 시각에 +9시간 적용).
5. 중량 분석은 결측·음수 때문에 보정 및 별도 제외가 필요하다(근거: 중복 제거 후 316행 추정, 외부 퀵 중량 미해결 1건은 수요에만 유지).

근거 파일: data_issues_log.csv, clean_rowcount.csv, clean_verification_summary.csv.
