# ③ 네트워크 설계 1차

저장소 루트에서 실행한다.

```powershell
python -m src.network.run --config config.json
```

`config.json`의 `network`에 T·유형별 상한·시간 및 비용 구간·개점 구간·사전 후보 목록을 모았다. ⑤ 결과가 나오면 이 입력을 교체한다.

`model.py`는 ② 잠재 수요를 동별로 배정하고 접수 제한을 적용해 재무 엔진의 ops 입력을 만든다. `search.py`는 파일럿 조합별 순차 추가와 사전안 출발 탐색을 수행한다. `verify.py`는 직접 검산, `report.py`는 지도와 한국어 설명을 생성한다.

선택안은 `network_plan_A/B/C/B2.csv`이며 `alt_compare.csv`는 대안×탐색 경로×시장 시나리오 행이다. `selected=1`이 채택 경로다. 주문율 하·상한의 NPV와 최대 자금 소요는 같은 행의 `_low/_high` 열에 들어간다.

기본 계획은 시장 base·orders_mid의 최대 자금 소요 500억 원 이내에서 선택한다. 다른 조합에서 한도를 초과할 수 있으며 해당 플래그를 공개한다. 한도 내 결과를 찾지 못한 대안은 비교표에 남기고 선택안으로 표시하지 않는다.

`network_run_status.json`에서 실행 완료 상태를 확인한다. 탐색 경로·파일럿 조합·개별 채택 변경은 `network_search_*.csv`, 제약 검산은 `network_verification.csv`, 설명은 `network_validation.md`에 있다.

대안·탐색 경로가 끝날 때마다 `network_checkpoint_<대안>_<탐색>.json`에 파일럿 4조합의 결과를 저장한다. 중단 후 같은 설정·입력·코드로 다시 실행하면 저장된 경로를 읽어 결과를 조립하고 남은 경로만 계산한다.

이번 실행의 시작 설정은 `outputs/network_resume_config.json`에 보존했다. 다른 단계에서 공용 설정이 바뀌어도 이 실행의 체크포인트를 재사용하려면 `--config outputs/network_resume_config.json`을 지정한다.

파일럿의 추정 기초재고는 `network_pilot_inventory.csv`에 저장한다. 실제 평가한 설정 `network_effective_config.json`에는 동일 잔액이 `finance.pilot_initial_inventory_krw`로 포함되어 있다. 이 설정으로 FinanceEngine을 초기화하면 저장된 ops와 계획의 NPV를 재현할 수 있다.
