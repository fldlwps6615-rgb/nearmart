# 니어마트 서울 1시간 퀵커머스 거점 네트워크 — 의사결정 지원 시스템

2026 물류 AI 해커톤 제출물. 데이터 검증부터 수요예측, 네트워크 설계, 배차(VRP), 운영 시뮬레이션,
20년 재무(NPV), what-if, 제출 양식(T1~T4)까지 하나의 흐름으로 실행된다.

## 1. 환경

- Python 3.10 이상 (단계별 코드는 Windows Python 3.14에서 작성·실행, run_all.py는 Python 3.10에서 실행 확인)
- 패키지: `pip install -r requirements.txt` (pandas, numpy, scipy, matplotlib)
- 선택: Node.js — `src/finance/build_check.mjs`(재무 엑셀 독립 검산 파일 생성)에만 사용
- 모든 명령은 이 폴더(저장소 루트)에서 실행한다. 원본 데이터는 `data/`에 두며 코드가 수정하지 않는다.

## 2. 빠른 실행 (심사 시연용, 약 1분)

```bash
python -B -m src.run_all --quick
```

저장된 탐색·시뮬레이션 결과를 사용해 ⑥ 재무 검산 → ⑦ what-if → 제출 양식 T1~T4 → 최종안 KPI를 다시 만든다.
단계별 소요 시간과 최종안(B, v3)의 시장 저성장·기본·고성장 NPV, 최대 자금이 출력된다.

## 3. 조건을 바꿔 다시 실행 (즉석 조건 변경)

```bash
python -B -m src.run_all --skip-stages --scenario low                 # 시장 저성장만
python -B -m src.run_all --skip-stages --discount-rate 0.16           # 할인율 16%
python -B -m src.run_all --skip-stages --param contract_fee_growth=0.04   # 15번 파라미터 덮어쓰기
python -B -m src.run_all --quick --discount-rate 0.08                 # what-if·T1~T4까지 다시 만든 뒤 재평가
```

- `--param 이름=값` 의 이름은 `data/15_cost_parameters.csv` 의 파라미터 이름이다. 여러 번 줄 수 있다.
- 결과는 화면과 `outputs/run_all_condition.csv` 에 저장된다.
- 인건비 충격(W1)·신규 경쟁자(W2)·10년 재계약 실패·할인율 8/16%는 ⑦ what-if 단계가 함께 계산한다
  (`outputs/whatif_results.csv`, `outputs/whatif_summary.md`).

## 4. 전체 실행 (처음부터 재현)

```bash
python -B -m src.run_all
```

| 순서 | 단계 | 모듈 | 비고 |
| --- | --- | --- | --- |
| 1 | ① 데이터 정제 | `src.clean.run` | 결함 처리 로그 `outputs/data_issues_log.csv` |
| 2 | ① 파일럿 진단 | `src.diagnosis.run` | `outputs/pilot_kpi.csv`, `pilot_diagnosis.md` |
| 3 | ② 수요예측 | `src.demand.run` | 연도×행정동 수요, 시간대 패턴, 불확실성 구간 |
| 4 | ⑥ 재무 엔진 검산 | `src.finance.verify` | 부록 B 규칙 검산 |
| 5 | ③ 네트워크 1차 설계 | `src.network.run` | 대안 A·B·B2·C |
| 6 | ③ 단계 확장안 | `src.network.staged.run` | 대안 D |
| 7 | ④ 배차·벤치마크 | `src.dispatch.run` | T3, `outputs/bm_kpi.csv` |
| 8 | ⑤ 반경 실험 v1 | `src.sim.radius_bm` | |
| 9 | ⑤ 반경 강건 실험 v2 | `src.sim.radius_bm_v2` | 약 8분(8코어), 독립 20회 검증 |
| 10 | 환류 v2 | `src.network.feedback` | ⑤ → ③·⑥ 되먹임, C15·D15 |
| 11 | 환류 v3 | `src.network.feedback_v3` | 달력 가중, 재설계, 원인 분해, 손익분기 |
| 12 | ⑦ what-if | `src.whatif.run --plan-version v3` | 약 10초 |
| 13 | 제출 양식 | `src.submission.make_templates --alt B --version v3` | 약 2초 |

전체 실행은 탐색·시뮬레이션 단계 때문에 수십 분 이상 걸린다. 특정 단계만 실행하려면 `--only "what-if"` 처럼 단계 이름 일부를 준다.
각 단계의 실제 소요 시간과 검산 결과는 `docs/log.md` 에 기록돼 있다.

## 5. 출력 파일 지도

| 구분 | 파일 |
| --- | --- |
| 제출 양식 | `report/B_v3/T1_network_plan.csv`, `T2_dong_assignment.csv`, `T3_benchmark_dispatch.csv`, `T4_kpi_summary.csv` |
| 데이터 처리 로그 | `outputs/data_issues_log.csv` (v2·v3 환류 대체 규칙 포함본: `data_issues_log_v3.csv`) |
| 파일럿 진단 | `outputs/pilot_kpi.csv`, `outputs/pilot_diagnosis.md` |
| 수요 | `outputs/demand_*.csv`, `figures/demand_*.png` |
| 대안 비교 | `outputs/alt_compare.csv`(1차), `alt_compare_v2.csv`, `alt_compare_v3.csv`(최종) |
| 되먹임 전후 | `outputs/feedback_compare.csv`, `feedback_compare_v3.csv`, `feedback_decomposition.csv` |
| 손익분기·민감도 | `outputs/breakeven_cost.csv`, `outputs/capacity_sensitivity.csv` |
| 배차 | `outputs/bm_kpi.csv`, `report/T3_benchmark_dispatch.csv` |
| 시뮬레이션 | `outputs/radius_bm_check.csv`, `outputs/service_radius_basis.csv` |
| what-if | `outputs/whatif_results.csv`, `outputs/whatif_summary.md` |
| 가정·작업 기록 | `docs/assumptions.md`, `docs/log.md`, `docs/interfaces.md` |
| AI 활용 내역 | `docs/ai_usage.md` |
| 웹 시뮬레이터 | `app/main.py` (7절), 간단 버전 `app_simple.py` |

## 6. 최종안 요약

최종안은 B(v3): 서비스 반경 15분, 2027년 14곳. 이동시간 변동을 반영한 강건 운영비(주문당 5,642원)에서는
NPV가 음수이며, 기본 시장 NPV 손익분기 운영비는 주문당 4,817원, 시장 저성장 자금 500억 기준은 5,319원이다.
확장은 실측 운영비가 손익분기 아래로 확인될 때만 집행한다. 근거는 보고서 4~5장.

## 7. 웹 시뮬레이터 (app/main.py)

팀이 디자인한 재실행 콘솔에 시뮬레이션 환류(v3) 결과를 연결한 화면이다. 대안 A·B·B2·C15·D15를 같은 재무·네트워크 엔진으로 다시 계산한다.

- 조건: 대안, 시장 시나리오, 할인율, 인건비 충격(W1), 주문당 운영비 배수(1.00 = 시뮬레이션 기반 15분 5,642원/건), 후보지 제외, 빠른 조건 버튼
- 결과: NPV·최대 자금(500억 한도 막대)·EBITDA 흑자 전환·할인 회수, 기준 대비 변화와 "결론이 바뀌나?" 판정, 최근 실행 기록
- 탭: 지도(연도별 거점·권역), 재무(누적 현금흐름·EBITDA·연간표 CSV), 대안 비교(NPV·위험-수익·운영비 민감도와 대안별 손익분기), 운영·벤치마크(배차 P0 vs P2, 반경별 시뮬레이션 처리능력, 환류 원인 분해, 되먹임 전후), 가정(투자자 조건 점검, 판단 기준, 보정값, 데이터 처리 로그)
- 운영비 배수를 0.85(약 4,817원/건)로 내리면 B의 기본 시장 NPV가 0을 넘는다.

로컬 실행:

```bash
pip install -r requirements.txt
streamlit run app/main.py
```

간단 버전(한 파일): `streamlit run app_simple.py`

GitHub로 배포(Streamlit Community Cloud):

1. 이 폴더를 GitHub 저장소로 올린다(`.gitignore`가 _share, _submit, 캐시를 제외). 대회 데이터가 들어 있으므로 비공개 저장소를 권장한다.
2. share.streamlit.io 에 GitHub 계정으로 로그인 → New app → 저장소·브랜치 선택, Main file path = `app/main.py`.
3. Advanced settings에서 Python 3.11 이상을 고른다. 패키지는 requirements.txt로 자동 설치된다.
4. 배포 후 받은 URL을 공유한다. 저장소에 push하면 자동으로 다시 배포된다.

엔진 입력은 `config.json`, `data/`, `outputs/`(alt_compare_v3, feedback_basis_v3, network_plan_*_v3, bm_kpi, service_radius_basis, feedback_decomposition 등), `report/B_v3/`를 읽으므로 이 폴더들을 함께 올려야 한다.
