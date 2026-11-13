"""시장별 기본 대비 증감과 NPV 순위 비교."""
import numpy as np


MARKET_LABELS = {"low": "저성장", "base": "기본", "high": "고성장"}


def add_comparisons(results, settings):
    keys = ["alternative", "scenario"]
    metrics = ["npv", "npv_eok", "max_funding", "max_funding_eok",
               "ebitda_positive_year", *[f"coverage_{year}" for year in settings["coverage_years"]],
               f"orders_{settings['orders_year']}", f"orders_{settings['orders_year']}_annual"]
    results["npv_rank"] = (results.npv.round(2).groupby([results.case, results.scenario])
                           .rank(method="min", ascending=False).astype(int))
    base = results[results.case.eq("BASE")].set_index(keys)[metrics + ["npv_rank"]]
    out = results.join(base.add_prefix("base_"), on=keys, validate="many_to_one")
    for metric in metrics:
        if metric != "ebitda_positive_year":
            out[f"delta_{metric}"] = out[metric] - out[f"base_{metric}"]
    out["ebitda_positive_year_changed"] = out.ebitda_positive_year.ne(out.base_ebitda_positive_year)
    out["npv_rank_changed"] = out.npv_rank.ne(out.base_npv_rank)
    return out


def rank_order(group):
    return " > ".join("=".join(sorted(members.alternative)) for _, members in group.groupby("npv_rank"))


def interval(series, digits):
    low, high = (round(v, digits) for v in (series.min(), series.max()))
    values = ["0" if value == 0 else f"{value:+,.{digits}f}" for value in (low, high)]
    if low == high:
        return values[0]
    return "~".join(values)


def make_summary(results, settings, moves, rates, affected, baseline_error, elapsed):
    version = results.plan_version.iat[0]
    start1, start2 = settings["w1"]["start_year"], settings["w2"]["start_year"]
    baseline = results[results.case.eq("BASE")]
    descriptions = {"W1": "인건비 충격", "W2": "신규 경쟁자", "NONRENEWAL": "10년 재계약 실패",
                    **{case: f"할인율 {rate:.0%}" for case, rate in settings["discount_rates"].items()}}
    lines = [f"# {version} What-if 비교", "",
             "시장별 중앙 수요로 평가했다. 증감 범위는 같은 시장·대안의 기본안과 비교한 15개 조합이며, 순위는 NPV 내림차순이다. `=`는 원 단위 소수 둘째 자리 기준 공동 순위다.", "",
             "기본 순위는 " + ", ".join(f"{MARKET_LABELS[market]} {rank_order(baseline[baseline.scenario.eq(market)])}"
                                        for market in settings["markets"]) + "다.", ""]
    for case, label in descriptions.items():
        group = results[results.case.eq(case)]
        coverage = group[[f"delta_coverage_{year}" for year in settings["coverage_years"]]].stack()
        changed = group[group.ebitda_positive_year_changed]
        ebitda = "흑자 연도 동일" if changed.empty else "흑자 연도 " + ", ".join(
            f"{r.alternative}/{MARKET_LABELS[r.scenario]} {r.base_ebitda_positive_year}→{r.ebitda_positive_year}"
            for r in changed.itertuples(index=False))
        order_year = settings["orders_year"]
        rankings = []
        for market in settings["markets"]:
            subset = group[group.scenario.eq(market)]
            ranking = rank_order(subset) if subset.npv_rank_changed.any() else "동일"
            rankings.append(f"{MARKET_LABELS[market]} {ranking}")
        lines.append(f"- **{label}** · 기본 대비 NPV {interval(group.delta_npv_eok, 2)}억 원, "
                     f"최대 자금 {interval(group.delta_max_funding_eok, 2)}억 원, 커버리지 {interval(coverage, 4)}, "
                     f"{order_year}년 주문 {interval(group[f'delta_orders_{order_year}'], 1)}건/일, {ebitda}. "
                     f"순위 {'변경' if group.npv_rank_changed.any() else '유지'}({', '.join(rankings)}).")

    income = rates[rates.parameter.eq("contract_min_hourly_earning_krw") & rates.year.eq(start1)].iloc[0]
    increase = settings["w1"]["increase"]
    lines += ["", "## 적용 가정 및 검산", "",
              f"- W1은 {start1}년부터 기존 연도별 단가에 {1 + increase:.2f}배를 적용했다. 피커·직고용·위탁의 모델 주문당 비용을 같은 비율로 올렸으며, 직고용 단가에 합산된 인센티브·차량비도 이 비례 가정에 포함된다. 수급 기준과 위탁 지급 단가를 같은 배수로 올려 고정 인력·배차의 수입 대비 기준 비율을 유지했다. {start1}년 15번 기준 수급 기준은 {income.base_krw:,.0f}→{income.w1_krw:,.0f}원/시간이다.",
              f"- W2는 {start2}년 초 B {settings['w2']['competitor_count']}곳을 A와 합산했다. 경쟁 계수가 하락한 동×연도는 {affected:,}개다. 계획·반경·동 배정·운영 단가를 고정하고 동별 수요와 처리량·재무를 다시 계산했다.",
              f"- 재계약 실패는 계약 만료 이후에도 영업 예정인 원거점을 모두 폐점하고, 입주 가능한 미사용 동일 유형 후보에 1:1로 배정했다. 맑은 날 {settings['forced_relocation']['depart_hour']}시 공용 이동시간 합을 기준으로 배정했으며, 원거점·이전지 {len(moves)}쌍을 공개했다. 이전지는 개점 램프업과 기존 반경별 운영비·처리 한도를 적용했다. 대안 간 비교용 스트레스 가정이다.",
              "- 커버리지는 해당 연도 배송 반경에 포함된 세대 비율이다. 2036년 주문은 처리 한도를 적용한 일평균과 연간 건수를 함께 기록했다.",
              f"- 기존 {version} 기본안 {len(baseline)}행의 NPV 최대 차이는 {baseline_error:.6f}원이다. 전체 {len(results)}행의 NPV·최대 자금·연간 주문 합계를 다시 계산했다. W1 비용 비례, W2 진입 전 재무·동 배정, 할인율 변경 시 현금흐름, 강제 이전 개폐점을 대조했다. 계산 시간 {elapsed:.2f}초.",
              "", f"재실행 `python -B -m src.whatif.run --plan-version {version}`", "",
              "v4 재실행은 v4 계획 5개와 `feedback_basis_v4.csv`, `alt_compare_v4.csv`가 저장된 뒤 `--plan-version v4`를 지정한다. 산출물의 `plan_version`으로 실행 버전을 확인할 수 있다.", ""]
    return "\n".join(lines)
