"""시뮬레이션 근거·보정값·필수 보고 지표 화면(운영·벤치마크, 가정, 운영비 민감도)."""
from dataclasses import replace

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import data, scenario
from app.style import ALT_COLORS, BAD, GOOD, MUTED, PLOTLY_CONFIG, PLOTLY_TEMPLATE

FACTORS = [round(x, 2) for x in np.arange(0.78, 1.101, 0.02)]
BASE_COST = {12: 5508, 15: 5642}  # 반경별 달력 가중 강건 운영비(원/건, 2027년 단가, feedback_basis_v3)
LIMIT_EOK = 500


@st.cache_data(show_spinner="운영비 민감도 계산 중…")
def cost_curve(scen, discount_rate):
    rows = []
    for alt in data.load_alternatives():
        for factor in FACTORS:
            for market in sorted({scen, "low"}):
                cond = scenario.Conditions(alt=alt, scen=market, discount_rate=discount_rate, cost_factor=factor)
                _, kpi = scenario.run(cond)
                if "error" in kpi:
                    continue
                rows.append({"alt": alt, "factor": factor, "market": market,
                             "npv_eok": kpi["npv_eok"], "max_funding_eok": kpi["max_funding_eok"]})
    return pd.DataFrame(rows)


def _crossing(frame, column, target):
    """배수를 낮추며 처음 기준을 만족하는 지점(선형 보간)."""
    frame = frame.sort_values("factor", ascending=False)
    ok = frame[column] >= target if column == "npv_eok" else frame[column] <= target
    if ok.iloc[0]:
        return float(frame["factor"].iloc[0])
    for (_, hi), (_, lo) in zip(frame.iloc[:-1].iterrows(), frame.iloc[1:].iterrows()):
        a, b = hi[column] - target, lo[column] - target
        if (a < 0 <= b) if column == "npv_eok" else (a > 0 >= b):
            return float(hi["factor"] + (lo["factor"] - hi["factor"]) * (a / (a - b)))
    return None


def render_cost_curve(cond):
    curve = cost_curve(cond.scen, cond.discount_rate)
    st.markdown("#### 주문당 운영비가 내려가면 — 대안별 NPV")
    st.caption("거점 배치를 고정하고 라이더·피커 단가만 같은 비율로 바꿨다. 결과를 좌우하는 변수가 시장이 아니라 운영비임을 보여 준다.")
    fig = go.Figure(layout=dict(template=PLOTLY_TEMPLATE))
    view = curve[curve.market.eq(cond.scen)]
    for alt in data.load_alternatives():
        rows = view[view.alt.eq(alt)].sort_values("factor")
        fig.add_trace(go.Scatter(x=rows.factor, y=rows.npv_eok, name=f"{alt}안", mode="lines+markers",
                                 line=dict(color=ALT_COLORS[alt], width=4 if alt == cond.alt else 2),
                                 opacity=1 if alt == cond.alt else 0.45,
                                 hovertemplate=f"{alt}안 · 운영비 ×%{{x:.2f}}<br>NPV %{{y:,.0f}}억<extra></extra>"))
    fig.add_hline(y=0, line_color=BAD, line_dash="dot", annotation_text="NPV 0", annotation_position="top left")
    fig.add_vline(x=1.0, line_color=MUTED, line_dash="dot", annotation_text="현재(시뮬레이션 운영비)")
    fig.update_layout(height=420, xaxis_title="주문당 운영비 배수 (1.00 = 15분 5,642원/건)", yaxis_title=f"NPV ({cond.scen}, 억 원)")
    fig.update_xaxes(autorange="reversed")
    with st.container(key="glass_cost_curve"):
        st.plotly_chart(fig, theme=None, config=PLOTLY_CONFIG, width="stretch", key="cost_curve_chart")
    rows = []
    models = data.load_models()
    for alt in data.load_alternatives():
        radius = int(models[alt][0].radius)
        base_cost = BASE_COST.get(radius)
        npv0 = _crossing(curve[(curve.alt == alt) & (curve.market == cond.scen)], "npv_eok", 0)
        fund = _crossing(curve[(curve.alt == alt) & (curve.market == "low")], "max_funding_eok", LIMIT_EOK)
        fmt = lambda f: "범위 밖" if f is None else (f"×{f:.2f} (약 {base_cost * f:,.0f}원)" if base_cost else f"×{f:.2f}")
        rows.append({"대안": alt, "반경": f"{radius}분", "현재 운영비": f"{base_cost:,}원" if base_cost else "-",
                     "저성장 자금 500억 이내가 되는 배수": fmt(fund), f"NPV({cond.scen}) 0이 되는 배수": fmt(npv0)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("배수가 1에 가까울수록 적은 비용 개선으로 기준을 넘는다. B는 가장 작은 개선으로 자금 한도를 통과한다(보고서 5.1절).")


def render_operations():
    st.markdown("#### ④ 배차 벤치마크 — 파일럿 방식(P0) vs 우리 정책(P2)")
    bm = data.load_output("bm_kpi.csv")
    bm = bm[bm.policy.isin(["P0", "P2"])].copy()
    table = pd.DataFrame({
        "벤치마크": bm.instance, "방식": bm.policy.map({"P0": "파일럿 방식", "P2": "우리 정책"}),
        "정시율": (bm.on_time * 100).round(1).astype(str) + "%",
        "평균/90분위 배송(분)": bm.avg_min.round(1).astype(str) + " / " + bm.p90_min.round(1).astype(str),
        "트립당 주문": bm.orders_per_trip.round(2), "주문당 비용(원)": bm.cost_per_order.round(0).astype(int),
        "위탁 시급(원)": bm.rider_hourly.round(0).astype(int), "작업량 편차(CV)": bm.workload_cv.round(2),
        "규칙 위반": bm.rule_violations})
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("이동시간 고정(결정론) 조건, 2027년 단가. 2029년 수급 기준 시급 17,860원. 출처 outputs/bm_kpi.csv, T3 독립 검증 50/50")

    st.markdown("#### ⑤ 시뮬레이션 — 반경별 처리 가능 주문(M형, 하루)")
    basis = data.load_output("service_radius_basis.csv")
    basis = basis[basis.version.eq("v2")]
    label = {"original": "원래 반경", "12": "12분", "15": "15분", "20": "20분"}
    day = {"weekday_clear": "맑은 평일(BM1)", "weekend_clear": "맑은 주말(BM2)", "weekday_rain": "비 오는 평일(BM3)"}
    grid = basis.assign(반경=basis.radius_type.astype(str).map(label), 대표일=basis.day_type.map(day),
                        값=np.where(basis.feasible.astype(str).str.lower().eq("true"),
                                   basis.max_orders_per_day.fillna(0).astype(int).astype(str) + "건",
                                   "통과 없음"))
    pivot = grid.pivot(index="대표일", columns="반경", values="값")[["원래 반경", "12분", "15분", "20분"]]
    st.dataframe(pivot, width="stretch")
    st.caption("길 막힘 변동(이동시간 변동계수 0.15)을 넣고 시드 5개로 인력 배치를 탐색, 통과 배치를 독립 20회로 재검증. "
               "기준: 모든 반복에서 보정 정시율 95%(팀 SLA), 미배송 0, 라이더 수급·대기 공간 충족. 20분은 세 대표일 모두 통과 없음 → C·D를 15분으로 재설계.")

    st.markdown("#### 환류 — 무엇이 결과를 바꿨나 (B, 기본 시장)")
    dec = data.load_output("feedback_decomposition.csv")
    names = {"baseline": "1차(되먹임 전)", "cost_only": "운영비만 반영", "capacity_only": "처리능력만 반영",
             "late_rate_only": "지연율만 반영", "all_08": "모두 반영(v2)", "all_08_2_weighted": "모두 반영(v3, 달력 가중)"}
    fig = go.Figure(go.Bar(x=[names.get(c, c) for c in dec.case], y=dec.npv_base_eok,
                           marker_color=[GOOD if v >= 0 else BAD for v in dec.npv_base_eok],
                           text=[f"{v:,.0f}억" for v in dec.npv_base_eok], textposition="outside"),
                    layout=dict(template=PLOTLY_TEMPLATE))
    fig.update_layout(height=380, yaxis_title="NPV (억 원)", title_text="운영비가 주범: 운영비만 반영해도 377억 → −209억")
    with st.container(key="glass_decomposition"):
        st.plotly_chart(fig, theme=None, config=PLOTLY_CONFIG, width="stretch", key="decomposition_chart")

    st.markdown("#### 되먹임 전후 대안 비교")
    comp = data.load_output("feedback_compare_v3.csv")
    view = comp[["alternative", "phase", "design", "T", "hubs_2027", "coverage_2027", "npv_low_eok", "npv_base_eok",
                 "npv_high_eok", "max_funding_low_mid_eok", "on_time", "cost_per_order_krw", "ebitda_positive_year"]].copy()
    view.columns = ["대안", "단계", "설계", "반경(분)", "거점 2027", "커버리지 2027", "NPV 저성장", "NPV 기본", "NPV 고성장",
                    "최대 자금(저성장)", "정시율", "운영비(원/건)", "EBITDA 흑자"]
    st.dataframe(view.round(2), hide_index=True, width="stretch")


def render_assumptions(cond, kpi):
    st.markdown("#### 투자자 조건 점검 (현재 조건)")
    yearly = data.year_for(cond.alt, cond.scen, tuple(tuple(p) for p in cond.relocations)).set_index("year")
    cov = float(yearly.loc[2027, "coverage"]) if 2027 in yearly.index else float("nan")
    ebitda = kpi["ebitda_positive_year"]
    rows = [
        ("서울 전역을 한 번에 덮지 말 것", f"2027년 커버리지 {cov:.1%}", cov < 0.9),
        ("2031년까지 EBITDA 흑자", f"{ebitda}", isinstance(ebitda, (int, np.integer)) and ebitda <= 2031),
        ("누적 자금 소요 500억 이내", f"{kpi['max_funding_eok']:,.0f}억", bool(kpi["funding_within_limit"])),
    ]
    st.dataframe(pd.DataFrame([{"조건": a, "현재 결과": b, "충족": "충족" if c else "미충족"} for a, b, c in rows]),
                 hide_index=True, width="stretch")
    st.markdown("#### 판단 기준 (팀 결정)")
    st.markdown("1. 시장 저성장에서도 최대 자금 500억 이하(탈락 조건)\n"
                "2. 시장 저성장 NPV가 높은 안\n3. 기본 시장 NPV\n"
                "4. 커버리지 하한은 두지 않음(시장이 20년간 약 1.9배 성장 → 작게 시작해도 확장으로 회복)")
    st.markdown("#### 주요 가정과 보정값")
    st.markdown(
        "- 서비스 반경: 15분 상한(시뮬레이션 근거). 12분 대안 A는 12분 기준\n"
        "- 거점 처리능력(M형, 하루): 12분 927건, 15분 873건(대표일 달력 가중). 맑은 평일 15분은 비 오는 평일 784건을 하한으로 대용\n"
        "- S·L형 처리능력: M형 대비 피커 상한과 대기 공간 비율 중 작은 값(S 0.5배, L 1.6배)\n"
        "- 주문당 운영비(라이더+피커, 2027년 단가): 12분 5,508원, 15분 5,642원(달력 가중: 맑은 평일 48·주말 17·비 26일)\n"
        "- 지연율: 12분 0.4%, 15분 0.9%(통과 조합 평균) → 쿠폰 2,000원, 수요 × (1 − 0.6 × 지연율)\n"
        "- 수요: 램프업 성숙 배수 1.42(범위 1.06~1.57), 시장 수요지수 2046년 1.90(기본)\n"
        "- 10년 재계약 거절: 기대값으로 반영, 실제 거절은 what-if로 확인\n"
        "- 재무: 할인율 12%, 2027~2046년, 본사비 55억(연 3%), 법인세 22%·이월공제 (문제지 부록 B)")
    st.caption("근거 파일: outputs/feedback_basis_v3.csv, service_radius_basis.csv, breakeven_cost.csv, docs/assumptions.md, docs/log.md")
    with st.expander("데이터 결함 처리 로그 요약 (outputs/data_issues_log.csv)"):
        log = data.load_output("data_issues_log.csv")
        st.dataframe(log, hide_index=True, width="stretch")


BEFORE_NAME = {"A": "A", "B": "B", "B2": "B2", "C15": "C", "D15": "D"}


def _unit_and_ontime(cond):
    """현재 조건의 2027년 주문당 운영비(원/건, 2027년 단가)와 2031년 정시율(주문 가중)."""
    relocations = tuple(tuple(pair) for pair in cond.relocations)
    plan = data.plan_for(cond.alt, relocations)
    ops = data.ops_for(cond.alt, data.load_effective_config()["finance"]["default_scenario"], relocations)
    excluded = [site for site in cond.excluded_sites if site in set(plan["site_id"])]
    plan, ops = scenario.exclude_sites(plan, ops, excluded)
    cost = ops[list(scenario.LABOR_COLUMNS)].sum(axis=1) * cond.cost_factor
    y27 = ops["year"].eq(2027)
    unit = float((cost[y27] * ops.loc[y27, "orders_per_day"]).sum() / ops.loc[y27, "orders_per_day"].sum())
    y31 = ops["year"].eq(2031)
    late = float((ops.loc[y31, "late_rate"] * ops.loc[y31, "orders_per_day"]).sum() / ops.loc[y31, "orders_per_day"].sum())
    hubs = int(plan["open_year"].le(2027).sum())
    return hubs, unit, 1 - late


def _row_values(cond):
    _, base = scenario.run(replace(cond, scen="base"))
    yearly = data.year_for(cond.alt, "base", tuple(tuple(p) for p in cond.relocations)).set_index("year")
    _, low = scenario.run(replace(cond, scen="low"))
    if "error" in base or "error" in low:
        return None
    hubs, unit, ontime = _unit_and_ontime(cond)
    return {"거점 수 2027년": f"{hubs}곳", "주문당 운영비(원/건, 2027년)": f"{unit:,.0f}원",
            "정시율(2031년)": f"{ontime:.1%}", "NPV(기본 시장)": f"{base['npv_eok']:,.0f}억",
            "최대 자금(저성장)": f"{low['max_funding_eok']:,.0f}억" + (" ✓" if low["max_funding_eok"] <= LIMIT_EOK else " (한도 초과)"),
            "EBITDA 흑자 전환": str(base["ebitda_positive_year"]),
            "커버리지 2027년": f"{float(yearly.loc[2027, 'coverage']):.1%}",
            "하루 주문 2031년": f"{float(yearly.loc[2031, 'orders_per_day']):,.0f}건",
            "놓친 주문 2036년(하루)": f"{float(yearly.loc[2036, 'lost_orders_per_day']):,.0f}건"}


def render_condition_table(cond):
    """조건을 바꾸면 다시 계산되는 핵심 지표표: 1차 · 시뮬레이션 기준(v3) · 현재 조건."""
    st.markdown("#### 조건 반영 결과표")
    reference = scenario.Conditions(alt=cond.alt, scen="base")
    current = _row_values(cond)
    baseline = _row_values(reference)
    if current is None or baseline is None:
        st.error("결과표 계산을 중단했습니다.")
        return
    before = data.load_output("feedback_compare_v3.csv")
    before = before[(before.alternative.eq(BEFORE_NAME[cond.alt])) & (before.phase.eq("before"))]
    first = {}
    if not before.empty:
        b = before.iloc[0]
        first = {"거점 수 2027년": f"{int(b.hubs_2027)}곳", "주문당 운영비(원/건, 2027년)": f"{b.cost_per_order_krw:,.0f}원",
                 "정시율(2031년)": f"{b.on_time:.1%}", "NPV(기본 시장)": f"{b.npv_base_eok:,.0f}억",
                 "최대 자금(저성장)": f"{b.max_funding_low_mid_eok:,.0f}억", "EBITDA 흑자 전환": str(b.ebitda_positive_year),
                 "커버리지 2027년": f"{b.coverage_2027:.1%}"}
    table = pd.DataFrame({"항목": list(current),
                          f"1차(이동시간 고정, {BEFORE_NAME[cond.alt]})": [first.get(k, "-") for k in current],
                          "시뮬레이션 기준(v3)": [baseline[k] for k in current],
                          "현재 조건": [current[k] for k in current]})
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("NPV는 기본 시장, 최대 자금은 시장 저성장 기준(투자자 한도 500억). 현재 조건 = 왼쪽의 할인율·인건비 충격·운영비 배수·거점 제외를 적용한 값. "
               "1차는 시뮬레이션 반영 전(이동시간 고정) 결과이며, 운영비·정시율은 버전별 집계 기준.")

    st.markdown("#### 대안별 결과 — 현재 조건을 다섯 대안에 똑같이 적용")
    rows = {}
    for alt in data.load_alternatives():
        values = _row_values(replace(cond, alt=alt, excluded_sites=[] if alt != cond.alt else cond.excluded_sites,
                                     relocations=() if alt != cond.alt else cond.relocations))
        if values:
            rows[f"{alt}안" + (" (선택)" if alt == cond.alt else "")] = values
    st.dataframe(pd.DataFrame(rows).rename_axis("항목").reset_index(), hide_index=True, width="stretch")
