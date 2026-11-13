"""저장된 결과만 이용해 사람이 검토할 요약을 만든다."""
import json
from src.common.io import ROOT
from .verify_t3 import read_csv


def main():
    root=ROOT/'outputs/dispatch'
    status=json.loads((root/'run_status.json').read_text(encoding='utf-8'))
    selected=json.loads((root/'selection.json').read_text(encoding='utf-8'))
    kpi=read_csv(ROOT/'outputs/bm_kpi.csv')
    checks=read_csv(ROOT/'outputs/dispatch_selected_verification.csv')
    expected=read_csv(ROOT/'outputs/bm_expectation_check.csv')
    tests=read_csv(root/'validation_tests.csv')
    timing=read_csv(root/'solo_timing_diagnostic.csv')
    staff=read_csv(ROOT/'outputs/bm_staffing.csv')
    lines=['# ④ 배차 벤치마크 결과','',
           f'실행 상태: **{status["status"]}**, 제출 가능: **{status["report_ready"]}**.',
           '비용은 2027년 원 단위, 수급 기준은 17,860원/접속시간이다. 모든 수치는 저장된 CSV를 다시 읽어 작성했다.','',
           '| 인스턴스 | 정책 | 주문 | 지연 | 정시율 | 평균/90분위(분) | 건/트립 | 원/주문 | 총비용(억 원) | 위탁 원/시간 | CV | 수급 | 규칙 위반 |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|']
    for r in kpi.itertuples():
        marker=' **선택**' if selected[r.instance]==r.policy else ''
        lines.append(f'| {r.instance} | {r.policy}{marker} | {r.orders} | {r.late_orders} | {r.on_time:.6f} | {r.avg_min:.3f}/{r.p90_min:.3f} | {r.orders_per_trip:.3f} | {r.cost_per_order:,.2f} | {r.total_cost_eok:.8f} | {r.rider_hourly:,.2f} | {r.workload_cv:.4f} | {r.supply_ok} | {r.rule_violations} |')
    lines+=['','## 선택안 인력과 탐색','',
            '| 인스턴스 | 탐색(초) | 후보 | 시간 제한 종료 | 위탁 인시 | 직고용 인시 | 피커 인시 | 거점 동시 체류/상한 |',
            '|---|---:|---:|---|---:|---:|---:|---|']
    capacities={'BM1':10,'BM2':13,'BM3':10}
    for run in status['instances']:
        instance=run['instance']; policy=selected[instance]
        r=kpi[(kpi.instance==instance)&(kpi.policy==policy)].iloc[0]
        lines.append(f'| {instance} | {run["elapsed_sec"]:.3f} | {run["evaluated"]} | {run["timed_out"]} | {r.contract_hours:.0f} | {r.employed_hours:.0f} | {r.picker_hours:.0f} | {r.bay_max:.0f}/{capacities[instance]} |')
    for instance,policy in selected.items():
        plan=json.loads((root/instance/policy/'plan.json').read_text(encoding='utf-8'))
        rows=staff[(staff.instance==instance)&(staff.policy==policy)]
        blocks='; '.join(f'{b["start_hour"]}:00 시작 {b["hours"]}시간 × {b["count"]}명' for b in plan['employed_blocks']) or '없음'
        lines+=['',f'- {instance}: 목표 {plan["target"]}건, 기본 W {plan["wait"]}분, 여유 {plan["margin"]}분; 직고용 {blocks}. 피커 최대 {rows.pickers.max()}명, 위탁 최대 {rows.contract_riders.max()}명. 시간별 예외 W는 plan.json 참조.']
    lines+=['','## 독립 검산','',
            f'- 제출 T3 검증 {len(checks)}개 중 실패 {(checks.status=="FAIL").sum()}개. 기준 정책의 실행 불가 결과와 제출안의 검증 결과를 분리했다.',
            f'- 변조 탐지·경계/외부 퀵 시험 {len(tests)}개 중 통과 {tests.detected.sum()}개.',
            f'- 원본 파일 불변: {status["source_unchanged"]}; travel_time.py 불변: {status["travel_module_unchanged"]}.',
            '- a·b·c·e·f·g·h: T3+고정 원입력. e의 상차 동시성은 확인 가능한 하한이고, g는 피커 10명 상한이다.',
            '- d·i·수급, e_full(전체 대기), g_staffing(시간별/개인 피커): 인력표·근무기록까지 읽어 확인했다.',
            '- 검증기는 엔진의 집계 함수를 호출하지 않고 이동시간·지급액·근무비용·KPI를 다시 계산한다.','',
            '## 사전 기대값 비교','',
            '| 인스턴스 | 지표 | 실제(분) | 기대(분) | 차이(분) |',
            '|---|---|---:|---:|---:|']
    for r in expected.itertuples():
        lines.append(f'| {r.instance} | {r.metric} | {r.actual:.6f} | {r.expected:.1f} | {r.difference:+.6f} |')
    lines+=['','위 단건 직행 비교는 접수 즉시 피킹·상차 후 직행하는 별도 계산이며 선택안의 묶음/대기시간 KPI가 아니다.',
            'BM1의 3건·BM2의 4~5건은 기대 경향이며 탐색에 강제 조건으로 넣지 않았다. 실제 선택 묶음과 직고용 블록을 위에 보고했다.','',
            f'BM1의 접수시각 속도를 쓰는 진단 계산은 최대 {timing[(timing.instance=="BM1")&(timing.clock=="order_time")].delivery_min.max():.6f}분으로 사전 26.4분을 재현한다. 실제 출발시각 기준은 {timing[(timing.instance=="BM1")&(timing.clock=="actual_departure")].delivery_min.max():.6f}분이다. BM1-0748은 접수 1195.95분(19:55:57), 출발 1210.20분(20:10:12)으로 서로 다른 시간대 속도가 적용된다. 출제 규칙대로 실제 구간 출발시각을 유지했다. 근거: outputs/dispatch/solo_timing_diagnostic.csv.',
            '',
            'P0 기대의 실패 양상도 구분한다. 모든 P0은 시간별 접속 상한을 강제로 지키므로 인력이 모자라면 실제 상한 위반 대신 지연이 발생한다. BM3 P0은 수급 기준을 충족하지만 지연과 거점 공간 초과로 실행 불가다. 기대한 실패 원인과 실제 실패 원인이 같다고 주장하지 않는다. 근거: outputs/dispatch/expectation_policy.csv.',
            '',
            '## 재현 및 한계','',
            '```','python -m src.dispatch.run --config config.json',
            'python -m src.dispatch.verify_t3 --config config.json',
            'python -m src.dispatch.test_checks','python -m src.dispatch.diagnose','python -m src.dispatch.report','```','',
            '- P0·P1은 비교용 기준 설정이고, 인력·정책 세밀 탐색은 P2에 수행했다. 실행 불가 정책도 모든 주문을 배송한 기록과 비용을 보존한다.',
            '- 계획은 벤치마크의 시간별 작업량으로 만들지만 실제 배차는 그 시각까지 완료된 주문만 사용한다.',
            '- 거친 격자 일부와 상위 후보 근방을 조사한 결과이며 전역 최적해 또는 전역 최소인원이라는 주장은 하지 않는다.',
            '- 최종 계획은 outputs/dispatch/<instance>/<policy>/plan.json, 탐색 내역은 search.csv에 있다. 재실행의 시간 제한 도달 여부는 장비 부하에 따라 달라질 수 있다.',
            '- 탐색 시간 표는 최종 실행 기준이다. 초기 점검에서 BM2 완료 후보가 없어 격자·잔여 처리 종료 범위를 보완한 뒤 재실행했으며, 개발 중 실패 실행을 포함한 누적 시간은 별도 측정하지 않았다. 600초 제한은 실행당 인스턴스별 탐색 호출에 적용된다.',
            '- 원자료, 입력 인터페이스, 비용/이동시간 규칙은 docs/interfaces.md·assumptions.md와 함께 확인한다.']
    (ROOT/'outputs/dispatch_summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('outputs/dispatch_summary.md')


if __name__=='__main__':
    main()
