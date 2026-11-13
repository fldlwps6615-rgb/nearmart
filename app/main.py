from pathlib import Path
import sys
import os

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.dont_write_bytecode = True

from html import escape
import math
from numbers import Real

import streamlit as st
from app import data, scenario, map_view, finance_view, comparison_view, demo_controls, presentation_view, evidence_view
from app.style import GOOD, BAD, MUTED, GLASS_CSS, QUICK_CSS, FONT_CSS, mode_css

NEUTRAL = MUTED
PERCENT = 100
# Widget ranges are the UI specifications supplied for S3, not model inputs.
DISCOUNT_MIN, DISCOUNT_MAX, DISCOUNT_STEP = 8.0, 16.0, 0.5
LABOR_MIN, LABOR_MAX, LABOR_STEP = 0.0, 20.0, 0.5
LIMITATION = "시뮬레이션 환류 v3 결과(처리능력·운영비·지연율 반영) 기준 · 거점 배치 고정 후 재무 재계산 · 인건비 충격·운영비 배수는 라이더·피커 단가에 반영 · 제외 거점 수요는 재배정 안 함"
TAB_NAMES = ["지도", "재무", "대안 비교", "운영·벤치마크", "가정"]

STYLE = """
<style>
:root { --nm-good:#0F9D8A; --nm-ink:#183047; }
.stApp {
 background-color:#F4F1FF;
 background-image:
  linear-gradient(115deg, transparent 40%, rgba(255,255,255,0.45) 50%, transparent 60%),
  radial-gradient(ellipse at 55% 15%, rgba(255,255,255,0.70) 0%, rgba(255,255,255,0) 35%),
  radial-gradient(ellipse at 85% 90%, rgba(185,180,242,0.60) 0%, rgba(185,180,242,0) 45%),
  linear-gradient(135deg, #FFFFFF 0%, #F4F1FF 30%, #E3DEFB 60%, #C9CDF6 85%, #BFD3F5 100%);
 background-attachment:fixed;
 background-repeat:no-repeat;
 background-size:cover;
 min-height:100vh;
}
[data-testid="stAppViewContainer"], [data-testid="stMain"] { background:transparent; }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stMainBlockContainer"] { padding-top:2.3rem; max-width:1480px; }
[data-testid="stSidebar"] { background:rgba(255,255,255,0.55); isolation:isolate; border-right:1px solid rgba(255,255,255,0.65); }
/* Isolate the glass layer so wide screenshots keep sidebar text in place. */
[data-testid="stSidebar"]::before { content:""; position:absolute; inset:0; z-index:-1; pointer-events:none;
 backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px); }
[data-testid="stSidebar"] h2 { font-size:1.15rem; letter-spacing:-.025em; }
h1 { color:#183047; letter-spacing:-.055em; font-weight:750; }
.nm-eyebrow { color:#61768B; font-size:11px; letter-spacing:.2em; font-weight:700; margin-bottom:6px; }
.nm-badge { display:inline-flex; align-items:center; padding:7px 12px; border-radius:7px;
 font-size:12px; font-weight:600; background:#E8EEF4; color:#486177; margin:0 7px 9px 0; }
.nm-good { color:#087768; background:#DFF5EF; }
.nm-bad { color:#B91C1C; background:#FEEBEC; }
.nm-summary { background:rgba(255,255,255,0.86); border:1px solid #E1E8EF; border-left:4px solid #0F9D8A;
 border-radius:8px; padding:17px 20px; margin:14px 0 23px; color:#29445B; line-height:1.7; }
[data-testid="stMetricLabel"] { color:#61758A; font-size:13px; }
[data-testid="stMetricValue"] { color:#183047; font-weight:700; font-variant-numeric:tabular-nums; }
[class*="st-key-kpi_"] { background:rgba(255,255,255,0.88); border-radius:11px; min-height:200px; }
[data-testid="stPlotlyChart"], [data-testid="stDeckGlJsonChart"] { background:rgba(255,255,255,0.88); border-radius:11px; }
[data-testid="stTabs"] { margin-top:24px; }
[data-testid="stTabs"] button { font-weight:600; }
.nm-empty { color:#8191A2; text-align:center; padding:65px 20px; border:1px dashed #CBD5E1;
 border-radius:10px; margin-top:18px; background:rgba(255,255,255,0.82); font-size:14px; }
.nm-footer { border-top:1px solid #E1E8EF; padding-top:16px; margin-top:25px;
 color:#728397; font-size:11px; line-height:1.8; }
[data-testid="stSidebar"] [data-baseweb="slider"] [role="slider"] { background:#0F9D8A; }
</style>
"""


def numeric(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def delta_details(value, reference, *, smaller_is_better=False, year=False):
    if numeric(value) and numeric(reference):
        difference = value - reference
        if difference == 0:
            return "기준과 동일", NEUTRAL
        improved = difference < 0 if smaller_is_better else difference > 0
        text = f"{difference:+.0f}년 · 기준 대비" if year else f"{difference:+,.2f}억 원 · 기준 대비"
        return text, GOOD if improved else BAD
    if value == reference:
        return "기준과 동일", NEUTRAL
    if numeric(value):
        return "기준 대비 달성", GOOD
    if numeric(reference):
        return "기준 대비 미달성", BAD
    return "기준 대비 변동", NEUTRAL


def badge(text, passed=None):
    status = "" if passed is None else (" nm-good" if passed else " nm-bad")
    return f'<span class="nm-badge{status}">{escape(text)}</span>'


def reset_conditions(defaults):
    for key, value in defaults.items():
        st.session_state[key] = list(value) if isinstance(value, list) else value


def clear_excluded_sites():
    st.session_state["excluded_sites"] = []
    st.session_state["relocations"] = []


def add_relocation():
    source, target = st.session_state.get("reloc_from"), st.session_state.get("reloc_to")
    if source and target:
        pairs = [list(pair) for pair in st.session_state.get("relocations", []) if pair[0] != source]
        pairs.append([source, target])
        st.session_state["relocations"] = pairs


def clear_relocations():
    st.session_state["relocations"] = []


def metric_card(column, key, label, value, reference, *, year=False, smaller_is_better=False):
    delta, color = delta_details(value, reference, smaller_is_better=smaller_is_better, year=year)
    displayed = f"{value:.0f}년" if year and numeric(value) else (str(value) if year else f"{value:,.2f}억 원")
    with column:
        card = st.container(border=True, key=key)
        with card:
            st.html(f'<style>.st-key-{key} [data-testid="stMetricDelta"], '
                    f'.st-key-{key} [data-testid="stMetricDelta"] * '
                    f'{{color:{color}!important;fill:{color}!important;}}</style>')
            st.metric(label, displayed, delta=delta, delta_color="off")
    return card


def main():
    presenting = bool(st.session_state.get("presentation_mode", False))
    st.set_page_config(layout="wide", page_title="NearMart 재실행 콘솔",
                       initial_sidebar_state="collapsed" if presenting else "expanded")
    st.html(STYLE)
    st.html(GLASS_CSS)
    st.html(QUICK_CSS)
    st.html(FONT_CSS)
    st.html(mode_css(presenting))
    cfg = data.load_effective_config()
    alternatives = data.load_alternatives()
    default_alt = "B"  # 최종안
    default_scen = cfg["finance"]["default_scenario"]
    if default_alt not in alternatives:
        st.error("최종안 B가 데이터에 없습니다.")
        st.stop()
    default = scenario.Conditions(default_alt, default_scen)
    defaults = {"condition_alt": default.alt, "condition_scen": default.scen,
                "discount_percent": default.discount_rate * PERCENT,
                "w1_on": default.w1_on, "w1_percent": default.w1_rate * PERCENT,
                "excluded_sites": default.excluded_sites, "cost_factor": default.cost_factor,
                "annual_cut_percent": default.annual_cut * PERCENT, "relocations": []}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = list(value) if isinstance(value, list) else value

    with st.sidebar:
        demo_controls.render_quick()
        st.html('<div class="nm-eyebrow">NEARMART / SIMULATION</div>')
        st.header("조건 변경")
        st.caption("조건을 바꾸면 결과가 바로 반영됩니다.")
        alt = st.segmented_control("대안", alternatives, key="condition_alt", required=True,
                                   on_change=clear_excluded_sites, width="stretch")
        reference = data.load_alt_compare()
        scenarios = reference.loc[reference["alternative"].eq(alt), "scenario"].dropna().drop_duplicates().tolist()
        scen = st.segmented_control("시장 시나리오", scenarios, key="condition_scen", required=True, width="stretch")
        st.divider()
        discount = st.slider("할인율 (%)", min_value=DISCOUNT_MIN, max_value=DISCOUNT_MAX,
                             step=DISCOUNT_STEP, key="discount_percent", format="%.1f%%")
        w1_on = st.toggle("인건비 충격", key="w1_on")
        w1 = st.slider("인건비 상승률 (%)", min_value=LABOR_MIN, max_value=LABOR_MAX,
                       step=LABOR_STEP, key="w1_percent", format="%.1f%%", disabled=not w1_on)
        st.caption(f"{scenario._shock_start_year()}년부터 라이더·피커 비용에 적용")
        st.divider()
        cost_factor = st.slider("주문당 운영비 배수(라이더+피커)", min_value=0.70, max_value=1.20, step=0.01,
                                key="cost_factor", format="%.2f",
                                help="1.00 = 시뮬레이션 기반 강건 운영비(15분 반경 5,642원/건, 2027년 단가)")
        st.caption(f"적용 ≈ {5642 * cost_factor:,.0f}원/건 · 손익분기(B): 자금 500억 5,319원(×0.94), NPV 0 4,817원(×0.85)")
        annual_cut = st.slider("연간 운영비 개선율 (%/년, 가설)", min_value=0.0, max_value=5.0, step=0.5,
                               key="annual_cut_percent", format="%.1f%%",
                               help="2028년부터 매년 라이더·피커 주문당 비용이 이 비율만큼 누적 감소(배차·인력 운영 학습 효과 가정)")
        st.caption("B 기준: 매년 1% → EBITDA 2030년 흑자·자금 한도 충족, 매년 3% → NPV 흑자")
        st.divider()
        plan = data.load_plan(alt)
        sites = data.load_sites().set_index("site_id")
        labels = {site: f"{site} · {sites.loc[site, 'gu']} {sites.loc[site, 'dong']}" for site in plan["site_id"]}
        excluded = st.multiselect("후보지 제외", options=plan["site_id"].tolist(), key="excluded_sites",
                                  format_func=labels.__getitem__, placeholder="제외할 거점을 선택하세요")
        with st.expander("거점 이전 시뮬레이션", expanded=bool(st.session_state.get("relocations"))):
            relocs = tuple(tuple(pair) for pair in st.session_state.get("relocations", []))
            current_plan = data.plan_for(alt, relocs)
            cands = data.candidate_sites().set_index("site_id")
            used = set(current_plan["site_id"])
            free = [site for site in cands.index if site not in used]
            st.selectbox("옮길 거점", current_plan["site_id"].tolist(), key="reloc_from",
                         format_func=lambda s: f"{s} · {cands.loc[s, 'gu']} {cands.loc[s, 'dong']} · {cands.loc[s, 'hub_type']}형")
            st.selectbox("이전할 후보지", free, key="reloc_to",
                         format_func=lambda s: (f"{s} · {cands.loc[s, 'gu']} {cands.loc[s, 'dong']} · {cands.loc[s, 'hub_type']}형 · "
                                                f"월세 {cands.loc[s, 'monthly_rent_krw'] / 1e4:,.0f}만 · {int(cands.loc[s, 'available_from_year'])}년~"))
            st.button("이전 적용", key="apply_relocation", on_click=add_relocation, width="stretch")
            if relocs:
                st.caption("적용 중: " + " · ".join(f"{a} → {b}" for a, b in relocs))
                st.button("이전 초기화", key="clear_relocation", on_click=clear_relocations, width="stretch")
            st.caption("이전하면 반경 15분·처리 상한 기준으로 동 배정과 운영을 엔진이 다시 계산한다. 이전지는 원래 개점 연도와 입주 가능 연도 중 늦은 해에 연다.")
        st.button("기준으로 되돌리기", key="reset_conditions", on_click=reset_conditions,
                  args=(defaults,), width="stretch")
        st.html(f'<div class="nm-footer">{escape(LIMITATION)}</div>')

    st.html('<div class="nm-eyebrow">SEOUL QUICK COMMERCE / DECISION CONSOLE</div>')
    with st.container(key="page_header"):
        title_col, badge_col, mode_col = st.columns([5, 1.5, 1.5], vertical_alignment="center")
        with title_col:
            st.title("NearMart 재실행 콘솔")
            st.caption("최종안 B: 반경 15분 · 2027년 14곳 · 주문당 운영비가 관문(5,300원·4,800원)을 넘을 때 확장")
        with badge_col:
            st.html(badge(data.data_version()))
        with mode_col:
            st.toggle("발표 모드", key="presentation_mode")
    cond = scenario.Conditions(alt=alt, scen=scen, discount_rate=discount / PERCENT,
                               w1_on=w1_on, w1_rate=w1 / PERCENT, excluded_sites=list(excluded),
                               cost_factor=float(cost_factor), annual_cut=float(annual_cut) / PERCENT,
                               relocations=tuple(tuple(pair) for pair in st.session_state.get("relocations", [])))
    annual, kpi = scenario.run(cond)
    if annual is None or "error" in kpi:
        st.error(f"재무 계산을 중단했습니다: {kpi.get('error', '계산 결과가 없습니다.')}")
        st.stop()
    baseline_annual, base = scenario.baseline(demo_controls.reference_condition(cond.alt))
    if baseline_annual is None or "error" in base:
        st.error(f"기준 검산을 중단했습니다: {base.get('error', '기준 결과가 없습니다.')}")
        st.stop()

    delta = kpi["npv_eok"] - base["npv_eok"]
    suffix = f" · 인건비 +{w1:g}%" if w1_on else ""
    suffix += f" · 거점 {len(excluded)}곳 제외" if excluded else ""
    suffix += f" · 운영비 ×{cost_factor:.2f}" if cost_factor != 1 else ""
    suffix += f" · 운영비 매년 −{annual_cut:g}%" if annual_cut else ""
    suffix += " · 이전 " + ", ".join(f"{a}→{b}" for a, b in cond.relocations) if cond.relocations else ""
    summary = (f"대안 {alt} · {scen} 시장 · 할인율 {discount:g}%{suffix} → "
               f"NPV {kpi['npv_eok']:,.2f}억 원, 기준 대비 {delta:+,.2f}억 원")
    st.html(f'<div class="nm-summary">{escape(summary)}</div>')
    demo_controls.record_execution(cond, kpi)
    if presenting:
        presentation_view.render(cond, annual, kpi, baseline_annual)
        return
    with st.container(key="metric_grid"):
        cards = st.columns(4, gap="medium")
        metric_card(cards[0], "kpi_npv", "NPV", kpi["npv_eok"], base["npv_eok"])
        funding_card = metric_card(cards[1], "kpi_funding", "최대 자금 소요", kpi["max_funding_eok"], base["max_funding_eok"], smaller_is_better=True)
        unit = cfg["finance"]["krw_per_eok"]
        limit_eok = kpi["funding_limit"] / unit
        usage = kpi["max_funding"] / kpi["funding_limit"]
        # Place the funding bar inside its own KPI card.
        with funding_card:
            st.progress(min(1.0, max(0.0, usage)), text=f"한도 {limit_eok:,.0f}억 원 대비 {usage:.1%}")
        metric_card(cards[2], "kpi_ebitda", "EBITDA 흑자 전환 연도", kpi["ebitda_positive_year"], base["ebitda_positive_year"], year=True, smaller_is_better=True)
        metric_card(cards[3], "kpi_payback", "할인 회수 연도", kpi["payback_year"], base["payback_year"], year=True, smaller_is_better=True)
    deadline = min(year for year in cfg["network"]["assignment_years"] if year > cfg["demand"]["forecast_start_year"])
    funding_ok = bool(kpi["funding_within_limit"])
    ebitda_ok = numeric(kpi["ebitda_positive_year"]) and kpi["ebitda_positive_year"] <= deadline
    funding_text = f"자금 한도 {limit_eok:,.0f}억 {'충족' if funding_ok else '초과'}"
    ebitda_text = f"{deadline}년까지 EBITDA 흑자 {'달성' if ebitda_ok else '미달'}"
    st.html(badge(funding_text, funding_ok) + badge(ebitda_text, ebitda_ok))
    with st.container(key="glass_condition_table"):
        evidence_view.render_condition_table(cond)
    changes_column, history_column = st.columns([3, 1.4], gap="large")
    with changes_column:
        demo_controls.render_changes(cond, base, kpi)
    with history_column:
        demo_controls.render_history()
    tabs = st.tabs(TAB_NAMES)
    for tab, name in zip(tabs, TAB_NAMES):
        with tab:
            if name == "지도":
                map_view.render(cond)
            elif name == "재무":
                finance_view.render(cond, annual, kpi, baseline_annual)
            elif name == "대안 비교":
                comparison_view.render(cond)
                evidence_view.render_cost_curve(cond)
            elif name == "운영·벤치마크":
                evidence_view.render_operations()
            else:
                evidence_view.render_assumptions(cond, kpi)


if __name__ == "__main__":
    main()










