"""저장된 실험에서 미배송으로 중단된 회차만 이어서 처리한다."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import json
from time import perf_counter

import numpy as np
import pandas as pd

from src.common.io import ROOT, load_config
from .experiment import context_for, init_worker, run_case
from .experiment_inputs import Case
from .experiment_report import COST, KEY, capacity_table, compare_trends, expected_ranges, interval_summary, plot
from .experiment_verify import verify_saved


def resume(config):
    started = perf_counter()
    output = ROOT / config["paths"]["outputs"]
    settings = config["sim"]["experiment"]
    encoding = "utf-8-sig"

    def path(name):
        return output / f"sim_{name}.csv"

    def read(name):
        return pd.read_csv(path(name), encoding=encoding, dtype={"site_id": str})

    stage = output / "sim_experiment_recovery_checkpoint.jsonl"
    runs, plans, cost, runtime = (read(name) for name in (
        "experiment_repetitions", "experiment_plans", "cost_curve", "experiment_runtime"))
    failed = runs.loc[~runs.completed]
    if failed.empty:
        if "recovery_used" in runs and "recovered_repetitions" not in runtime:
            recovery = read("experiment_recovery")
            if not stage.exists() or len(recovery) != int(runs.recovery_used.sum()):
                raise ValueError("복구 결과의 확정에 필요한 체크포인트 또는 복구 기록이 없습니다.")
            with stage.open(encoding="utf-8") as checkpoint:
                header = json.loads(next(checkpoint))
                records = [row for line in checkpoint for row in json.loads(line)]
            pd.testing.assert_frame_equal(pd.DataFrame(records).sort_values(KEY + ["repetition"]).reset_index(drop=True),
                                          recovery.sort_values(KEY + ["repetition"]).reset_index(drop=True),
                                          check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12)
            elapsed = perf_counter() - started
            elapsed += (datetime.now().timestamp() - header["started_at"] if "started_at" in header
                        else stage.stat().st_mtime - stage.stat().st_ctime)
            finalize(config, runtime, runs, recovery, read("experiment_verification"), path, stage, elapsed)
            return
        print("이어서 처리할 미배송 회차 0개", flush=True)
        return
    if not failed.reason.str.fullmatch(r"계획 종료 후 미배송 [1-9][0-9]*건").all():
        raise ValueError("잔여 배송 연장으로 복구할 수 없는 실패 사유가 있습니다.")
    ids = KEY + ["repetition"]
    original = runs.loc[runs.completed].copy().set_index(ids).sort_index()
    by_plan = plans.set_index(KEY).plan_json
    signature = sha256(path("experiment_repetitions").read_bytes() + path("experiment_plans").read_bytes()
                       + json.dumps(config, sort_keys=True).encode()).hexdigest()
    if stage.exists():
        with stage.open(encoding="utf-8") as f:
            header = json.loads(next(f))
            if header["signature"] != signature:
                raise ValueError("복구 체크포인트와 저장된 실험 또는 설정이 다릅니다.")
            recovered = [record for line in f for record in json.loads(line)]
    else:
        stage.write_text(json.dumps({"signature": signature, "started_at": datetime.now().timestamp()}) + "\n", encoding="utf-8")
        recovered = []
    handled = {tuple(record[k] for k in ids) for record in recovered}
    todo = failed.loc[[tuple(row) not in handled for row in failed[ids].itertuples(index=False, name=None)]]
    tasks = []
    for key, group in todo.groupby(KEY):
        row = group.iloc[0]
        case = Case(**{k: row[k] for k in ("hub_type", "rider_class", "day_type", "radius_min", "orders_per_day", "order_level")})
        tasks.append((case, group[["repetition", "reason"]].to_dict("records"), json.loads(by_plan.loc[key])))
    if tasks:
        with ProcessPoolExecutor(max_workers=settings["workers"], initializer=init_worker,
                                 initargs=(config, context_for(config))) as executor:
            futures = [executor.submit(run_case, case, failed_rows=rows, saved_plan=plan)
                       for case, rows, plan in tasks]
            with stage.open("a", encoding="utf-8") as checkpoint:
                for future in as_completed(futures):
                    rows = future.result()
                    recovered.extend(rows)
                    checkpoint.write(json.dumps(rows, ensure_ascii=False, default=lambda x: x.item()) + "\n")
                    checkpoint.flush()
                    if len(recovered) % 50 < len(rows) or len(recovered) == len(failed):
                        print(f"잔여 배송 완료 {len(recovered)}/{len(failed)}회, {perf_counter()-started:.1f}초", flush=True)
    recovery = pd.DataFrame(recovered)
    if len(recovery) != len(failed) or recovery.duplicated(ids).any() or not recovery.delivered_orders.eq(recovery.orders_per_day).all():
        raise ValueError("복구 회차 또는 주문 수 검산 실패")
    if set(recovery[ids].itertuples(index=False, name=None)) != set(failed[ids].itertuples(index=False, name=None)):
        raise ValueError("기존 미완료 회차 키와 복구 회차 키 불일치")
    runs["recovery_used"] = False
    combined = pd.concat([runs.loc[runs.completed], recovery], ignore_index=True).sort_values(ids)
    if not combined.loc[~combined.recovery_used].set_index(ids).sort_index()[original.columns].equals(original):
        raise ValueError("기존 완료 회차 값이 바뀌었습니다.")
    if len(combined) != len(runs) or int(combined.delivered_orders.sum()) != int(runs.orders_per_day.sum()):
        raise ValueError("전체 주문 보존 검산 실패")
    # 원래 인력으로 20회 완료한 경우만 운영 용량과 T2 집계의 대상이다.
    initial_count = cost.complete_repetitions.copy()
    indexed_cost = cost.set_index(KEY)
    for key, values in combined.groupby(KEY):
        if not values.recovery_used.any():
            continue
        summary = interval_summary(values, COST)
        for column, value in summary.items():
            indexed_cost.loc[key, column] = value
            if f"{column}_eok" in indexed_cost:
                indexed_cost.loc[key, f"{column}_eok"] = value / 1e8
    cost = indexed_cost.reset_index().reindex(columns=cost.columns)
    cost["initial_complete_repetitions"] = initial_count
    cost["recovery_repetitions"] = config["sim"]["repetitions"] - initial_count
    cost["complete_repetitions"] = config["sim"]["repetitions"]
    cost["metrics_include_clearance"] = cost.recovery_repetitions.gt(0)
    capacity = capacity_table(cost, config["sim"]["ontime_target"])
    previous_capacity = read("capacity")
    pd.testing.assert_frame_equal(capacity, previous_capacity, check_exact=False)
    check_rows = [
        dict(check="기존 완료 회차 보존", passed=True, detail=f"{len(original):,}회 값 일치"),
        dict(check="미완료 회차만 복구", passed=True, detail=f"{len(recovery):,}회 키 일치"),
        dict(check="전체 반복 주문 보존", passed=True, detail=f"{int(combined.delivered_orders.sum()):,}건 모두 배송"),
        dict(check="보정 상한과 기존 인력 판정 보존", passed=True, detail=f"{len(capacity)}개 용량 그룹 일치"),
    ]
    if not (recovery.late_orders_corrected.ge(recovery.late_orders).all()
            and np.allclose(recovery.ontime_rate_corrected, 1-recovery.late_orders_corrected/recovery.orders_per_day)):
        raise ValueError("복구 회차 보정 정시율 검산 실패")
    figures = ROOT / config["pilot_diagnosis"]["figures_dir"]
    plot(config, combined, cost, capacity, figures)
    outputs = {
        "experiment_repetitions": combined, "cost_curve": cost,
        "experiment_recovery": recovery,
        "expectation_check": expected_ranges(config, cost, capacity),
        "trend_check": compare_trends(cost, capacity),
    }
    for name, frame in outputs.items():
        frame.to_csv(path(name), index=False, encoding=encoding)
    saved = read("experiment_repetitions")
    pd.testing.assert_frame_equal(saved.loc[~saved.recovery_used].set_index(ids).sort_index()[original.columns],
                                  original, check_exact=False, check_dtype=False, rtol=1e-12, atol=1e-12)
    finalize(config, runtime, combined, recovery, pd.DataFrame(check_rows), path, stage, perf_counter() - started)


def finalize(config, runtime, combined, recovery, preservation_checks, path, stage, elapsed):
    checks = pd.concat([verify_saved(config, runtime.grid.iat[0] == "reduced"),
                        preservation_checks.tail(4)], ignore_index=True)
    checks.to_csv(path("experiment_verification"), index=False, encoding="utf-8-sig")
    if not checks.passed.all():
        raise ValueError("복구 결과 검산 실패")
    runtime["initial_completed_repetitions"] = runtime.completed_repetitions
    runtime["completed_repetitions"] = len(combined)
    runtime["recovered_repetitions"] = len(recovery)
    runtime["delivered_orders"] = int(combined.delivered_orders.sum())
    runtime["recovery_seconds"] = elapsed
    runtime["execution_seconds"] += runtime.recovery_seconds
    runtime["checks_passed"] = checks.passed.sum()
    runtime["checks_total"] = len(checks)
    runtime.to_csv(path("experiment_runtime"), index=False, encoding="utf-8-sig")
    stage.unlink()
    print(runtime.to_string(index=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    resume(load_config(args.config))


if __name__ == "__main__":
    main()
