"""니어마트 의사결정 지원 시스템 일괄 실행기.

전체 실행:   python -B -m src.run_all
빠른 실행:   python -B -m src.run_all --quick
조건 변경:   python -B -m src.run_all --quick --scenario low --discount-rate 0.16
             python -B -m src.run_all --quick --param contract_fee_growth=0.04

--quick 은 무거운 탐색·시뮬레이션 단계(③ 탐색, ④ 배차 탐색, ⑤ 반경 실험, 환류 재탐색)를
저장된 결과로 대체하고, 최종안 재무 재계산·what-if·제출 양식(T1~T4)만 다시 만든다.
"""

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from time import perf_counter

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
FINAL_ALT, FINAL_VERSION = "B", "v3"

# (단계 이름, 모듈, 추가 인자, 무거운 단계 여부)
STAGES = [
    ("① 데이터 정제", "src.clean.run", [], False),
    ("① 파일럿 진단", "src.diagnosis.run", [], False),
    ("② 수요예측", "src.demand.run", [], False),
    ("⑥ 재무 엔진 검산", "src.finance.verify", [], False),
    ("③ 네트워크 1차 설계", "src.network.run", [], True),
    ("③ 단계 확장안(D)", "src.network.staged.run", [], True),
    ("④ 배차·벤치마크", "src.dispatch.run", [], True),
    ("⑤ 반경 실험 v1", "src.sim.radius_bm", [], True),
    ("⑤ 반경 강건 실험 v2", "src.sim.radius_bm_v2", [], True),
    ("환류 v2(⑤→③·⑥)", "src.network.feedback", [], True),
    ("환류 v3(가중·재설계·손익분기)", "src.network.feedback_v3", [], True),
    ("⑦ what-if", "src.whatif.run", ["--plan-version", FINAL_VERSION], False),
    ("제출 양식 T1~T4", "src.submission.make_templates", ["--alt", FINAL_ALT, "--version", FINAL_VERSION], False),
]
QUICK = {"⑥ 재무 엔진 검산", "⑦ what-if", "제출 양식 T1~T4"}


def run_stage(name, module, extra):
    started = perf_counter()
    print(f"\n=== {name} ({module}) ===", flush=True)
    result = subprocess.run([sys.executable, "-B", "-m", module, *extra], cwd=ROOT)
    elapsed = perf_counter() - started
    status = "성공" if result.returncode == 0 else f"실패(코드 {result.returncode})"
    print(f"--- {name}: {status}, {elapsed:.1f}초", flush=True)
    return name, result.returncode, elapsed


def parse_param(text):
    key, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("--param 은 이름=값 형식입니다 (예: contract_fee_growth=0.04)")
    return key.strip(), float(value)


def parse_shock(text):
    year, sep, rate = text.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError("--wage-shock 은 연도:비율 형식입니다 (예: 2028:0.12)")
    return int(year), float(rate)


def condition_run(args):
    """(인건비 충격 W1은 ⑦ what-if 단계가 계산한다) 최종안을 바뀐 조건으로 다시 평가해 KPI를 출력하고 outputs/run_all_condition.csv에 저장한다."""
    import pandas as pd
    from src.common.io import load_config
    from src.submission.evaluation import MARKETS, discount_engine, evaluate, feedback_models, plan_input

    config = deepcopy(load_config(ROOT / "config.json"))
    rows, models = feedback_models(config, FINAL_VERSION)
    saved = rows.loc[FINAL_ALT]
    model = models[saved.loc["base", "T"]]
    frame, plan = plan_input(model, f"{FINAL_ALT}_{FINAL_VERSION}")
    engine = discount_engine(model, args.discount_rate) if args.discount_rate is not None else model.engine
    for key, value in args.param:
        if key not in engine.p:
            raise SystemExit(f"15_cost_parameters.csv 에 없는 파라미터: {key}")
        engine.p[key] = value
    markets = [args.scenario] if args.scenario else list(MARKETS)
    out = []
    for market in markets:
        _, kpi, _, _, yearly = evaluate(model, frame, plan, model.radius, market, engine=engine)
        out.append(dict(alternative=FINAL_ALT, version=FINAL_VERSION, scenario=market,
                        discount_rate=kpi["discount_rate"], params=";".join(f"{k}={v}" for k, v in args.param),
                        npv_eok=round(kpi["npv_eok"], 2), max_funding_eok=round(kpi["max_funding_eok"], 2),
                        funding_within_limit=kpi["funding_within_limit"],
                        ebitda_positive_year=kpi["ebitda_positive_year"], payback_year=kpi["payback_year"],
                        orders_per_day_2031=round(float(yearly.loc[2031, "orders_per_day"]), 1)))
    table = pd.DataFrame(out)
    table.to_csv(ROOT / "outputs" / "run_all_condition.csv", index=False, encoding="utf-8-sig")
    print("\n=== 조건 변경 재평가 (최종안 %s %s) ===" % (FINAL_ALT, FINAL_VERSION))
    print(table.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="무거운 단계는 저장 결과 사용")
    parser.add_argument("--only", help="이 이름이 포함된 단계만 실행 (예: what-if)")
    parser.add_argument("--scenario", choices=("low", "base", "high"), help="시장 수요지수 시나리오")
    parser.add_argument("--discount-rate", type=float, help="할인율 (예: 0.08)")
    parser.add_argument("--param", type=parse_param, action="append", default=[], help="15번 파라미터 덮어쓰기 이름=값")
    parser.add_argument("--skip-stages", action="store_true", help="단계 실행 없이 조건 재평가만")
    args = parser.parse_args()

    started = perf_counter()
    results = []
    if not args.skip_stages:
        for name, module, extra, heavy in STAGES:
            if args.only and args.only not in name:
                continue
            if args.quick and name not in QUICK:
                print(f"=== {name}: 건너뜀(--quick, 저장 결과 사용)")
                continue
            results.append(run_stage(name, module, extra))
            if results[-1][1] != 0:
                break
    condition_run(args)
    print("\n=== 요약 ===")
    for name, code, elapsed in results:
        print(f"{name:<28} {'성공' if code == 0 else '실패'} {elapsed:8.1f}초")
    print(f"총 소요 시간 {perf_counter() - started:.1f}초")
    return 1 if any(code for _, code, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
