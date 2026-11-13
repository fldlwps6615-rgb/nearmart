"""니어마트 웹 시뮬레이터 — 대안별(A·B·B2·C15·D15, 되먹임 v3) 계획을 실제 재무·네트워크 엔진으로 다시 계산한다.

실행: streamlit run app.py
"""

from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.common.io import load_config  # noqa: E402
from src.network.feedback import COST  # noqa: E402
from src.submission.evaluation import assignment, discount_engine, feedback_models, plan_input  # noqa: E402

VERSION, FINAL_ALT = "v3", "B"
ALTS = ["A", "B", "B2", "C15", "D15"]
ALT_DESC = {"A": "반경 12분", "B": "반경 15분 · 최종안", "B2": "반경 15분 · 소규모 시작",
            "C15": "전역형 C를 15분으로 재설계(결과가 B와 같은 계획)", "D15": "단계 확장형 D를 15분으로 재설계"}
MARKETS = {"저성장(low)": "low", "기본(base)": "base", "고성장(high)": "high"}
CURRENT_COST = 5642  # v3 달력 가중 주문당 운영비(라이더+피커, 2027년 단가, 15분)

st.set_page_config(page_title="니어마트 거점 네트워크 시뮬레이터", layout="wide")


@st.cache_resource(show_spinner="엔진과 대안 계획을 불러오는 중…")
def load_models():
    config = deepcopy(load_config(ROOT / "config.json"))
    rows, models = feedback_models(config, VERSION)
    plans = {}
    for alt in ALTS:
        model = models[rows.loc[alt].loc["base", "T"]]
        frame, plan = plan_input(model, f"{alt}_{VERSION}")
        plans[alt] = (model, frame, plan)
    return plans


@st.cache_data
def load_tables():
    out = ROOT / "outputs"
    sites = pd.read_csv(ROOT / "data" / "03_candidate_sites.csv", encoding="utf-8-sig")[["site_id", "gu", "dong", "lat", "lon"]]
    dongs = pd.read_csv(ROOT / "data" / "01_dong_master.csv", encoding="utf-8-sig")[["adm_cd2", "lat", "lon"]]
    dongs["adm_cd2"] = dongs.adm_cd2.astype(str)
    whatif = pd.read_csv(out / "whatif_results.csv", encoding="utf-8-sig")
    return sites, dongs, whatif


@st.cache_data(show_spinner="엔진 재계산 중…")
def simulate(alt, market, discount_rate, cost_factor, params, band="mid"):
    model, frame, plan = load_models()[alt]
    engine = discount_engine(model, discount_rate)
    for key, value in params:
        engine.p[key] = value
    ops, state = model.operating(plan, model.radius, market, band, details=True)
    ops = ops.copy()
    ops[COST] = ops[COST] * cost_factor
    annual, kpi = engine.evaluate(frame, ops, market)
    yearly = model.summaries(plan, state).set_index("year")
    t2 = assignment(model, state, yearly)
    return annual, kpi, yearly, t2


plans = load_models()
sites, dongs, whatif = load_tables()

st.title("니어마트 서울 1시간 퀵커머스 — 거점 네트워크 시뮬레이터")
st.caption("대안별 계획(되먹임 v3)을 보고서와 같은 재무·네트워크 엔진으로 다시 계산한다. 왼쪽 조건을 바꾸면 모든 대안이 같은 조건으로 재계산된다.")

with st.sidebar:
    st.header("대안")
    alt = st.selectbox("상세·지도로 볼 대안", ALTS, index=ALTS.index(FINAL_ALT),
                       format_func=lambda a: f"{a} — {ALT_DESC[a]}")
    compare = st.multiselect("비교할 대안", ALTS, default=ALTS)
    st.header("조건")
    market_label = st.radio("시장 수요 시나리오", list(MARKETS), index=1)
    band_label = st.radio("수요 예측 구간", ["하한", "중간", "상한"], index=1, horizontal=True)
    discount = st.slider("할인율", 0.06, 0.18, 0.12, 0.01)
    cost_factor = st.slider("주문당 운영비(라이더+피커) 배수", 0.70, 1.20, 1.00, 0.01,
                            help=f"1.00 = 시뮬레이션 기반 강건 운영비(15분 반경 {CURRENT_COST:,}원/건, 2027년 단가)")
    st.caption(f"15분 반경 기준 적용 운영비 ≈ {CURRENT_COST * cost_factor:,.0f}원/건 · "
               "B 손익분기: NPV 0 4,817원, 자금 500억 5,319원")
    with st.expander("15번 비용 파라미터 덮어쓰기"):
        base_engine = plans[FINAL_ALT][0].engine
        fee_growth = st.number_input("위탁 단가 연 상승률(contract_fee_growth)",
                                     value=float(base_engine.par("contract_fee_growth")), step=0.005, format="%.3f")
        wage_growth = st.number_input("인건비 연 상승률(wage_growth)",
                                      value=float(base_engine.par("wage_growth")), step=0.005, format="%.3f")
    year = st.select_slider("지도 연도", options=[2027, 2031, 2036, 2041, 2046], value=2031)

market = MARKETS[market_label]
band = {"하한": "low", "중간": "mid", "상한": "high"}[band_label]
params = (("contract_fee_growth", fee_growth), ("wage_growth", wage_growth))

# ── 대안 비교 ──────────────────────────────────────────────
results = {a: simulate(a, market, discount, cost_factor, params, band) for a in sorted(set(compare) | {alt}, key=ALTS.index)}
rows = []
for a in [x for x in ALTS if x in compare]:
    annual, kpi, yearly, _ = results[a]
    rows.append({"대안": a, "설명": ALT_DESC[a], "NPV(억)": round(kpi["npv_eok"]), "최대 자금(억)": round(kpi["max_funding_eok"]),
                 "500억 한도": "충족" if kpi["funding_within_limit"] else "초과",
                 "EBITDA 흑자": str(kpi["ebitda_positive_year"]), "할인 회수": str(kpi["payback_year"]),
                 "거점 2027": int(yearly.loc[2027, "active_hubs"]), "거점 2042": int(yearly.loc[2042, "active_hubs"]),
                 "커버리지 2027": f"{yearly.loc[2027, 'coverage']:.0%}",
                 "하루 주문 2031": round(yearly.loc[2031, "orders_per_day"]),
                 "놓친 주문 2036": round(yearly.loc[2036, "lost_orders_per_day"])})
st.subheader(f"대안 비교 — 시장 {market_label}, 수요 {band_label}, 할인율 {discount:.0%}, 운영비 ×{cost_factor:.2f}")
if rows:
    table = pd.DataFrame(rows)
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("판단 순서: ① 시장 저성장에서도 최대 자금 500억 이하 → ② 저성장 NPV → ③ 기본 NPV. 투자자 조건(전역을 한 번에 덮지 말 것, 2031년까지 흑자)을 함께 본다.")
    cum = pd.DataFrame({a: results[a][0].set_index("year").loc[2027:, "cum_cash_flow_eok"] for a in table["대안"]})
    cum.index = cum.index.astype(str)
    st.markdown("**대안별 누적 현금흐름(억 원)** — 최저점이 최대 자금 소요, −500억 아래는 투자자 한도 초과")
    st.line_chart(cum)

# ── 선택 대안 상세 ─────────────────────────────────────────
annual, kpi, yearly, t2 = results[alt]
st.divider()
st.subheader(f"대안 {alt} 상세 — {ALT_DESC[alt]}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("20년 NPV", f"{kpi['npv_eok']:,.0f}억")
c2.metric("최대 자금 소요", f"{kpi['max_funding_eok']:,.0f}억",
          "한도 500억 이내" if kpi["funding_within_limit"] else "한도 500억 초과",
          delta_color="normal" if kpi["funding_within_limit"] else "inverse")
c3.metric("EBITDA 흑자 전환", str(kpi["ebitda_positive_year"]))
c4.metric("할인 회수 연도", str(kpi["payback_year"]))

left, right = st.columns([3, 2])
with left:
    st.markdown("**연도별 EBITDA와 누적 현금흐름(억 원)**")
    chart = annual.loc[annual.year.ge(2027), ["year", "ebitda_eok", "cum_cash_flow_eok"]].rename(
        columns={"ebitda_eok": "EBITDA", "cum_cash_flow_eok": "누적 현금흐름"})
    chart["연도"] = chart.year.astype(int).astype(str)
    st.line_chart(chart.set_index("연도")[["EBITDA", "누적 현금흐름"]])
with right:
    _, frame, _ = plans[alt]
    hubs = frame.merge(sites, on="site_id", how="left")
    close = pd.to_numeric(hubs.close_year, errors="coerce").fillna(9999)
    active = hubs[(hubs.open_year <= year) & (close > year)]
    served = t2[t2.year.eq(year) & t2.site_id.notna()].assign(adm_cd2=lambda d: d.adm_cd2.astype(str)).merge(dongs, on="adm_cd2", how="left")
    points = pd.concat([
        served.assign(size=served.expected_orders_per_day.clip(lower=1) * 3, color="#0F7F7055")[["lat", "lon", "size", "color"]],
        active.assign(size=900, color="#C2410C")[["lat", "lon", "size", "color"]],
    ])
    st.markdown(f"**{year}년 거점과 서비스 권역**")
    st.map(points, latitude="lat", longitude="lon", size="size", color="color")
    st.caption(f"주황 = 운영 거점 {len(active)}곳 · 초록 = 서비스 동 {served.adm_cd2.nunique()}곳 (하루 {served.expected_orders_per_day.sum():,.0f}건)")

st.markdown("**거점별 담당 권역**")
hub_table = (served.groupby("site_id").agg(동_수=("adm_cd2", "size"), 하루_주문=("expected_orders_per_day", "sum"),
                                           평균_배송시간_분=("avg_delivery_min", "mean")).round(1).reset_index()
             .merge(hubs[["site_id", "hub_type", "gu", "dong", "open_year", "note"]], on="site_id", how="left"))
st.dataframe(hub_table.sort_values("하루_주문", ascending=False), hide_index=True, width="stretch")

st.markdown(f"**what-if — 대안 {alt} (저장 결과, 수요 중간·기본 운영비 기준)**")
case_names = {"BASE": "기본", "W1": "W1 인건비 +12%", "W2": "W2 경쟁사 12곳 진입",
              "NONRENEWAL": "10년 재계약 실패", "DISCOUNT_08": "할인율 8%", "DISCOUNT_16": "할인율 16%"}
w = whatif[whatif.alternative.eq(alt)].pivot_table(index="case", columns="scenario", values="npv_eok")
w = w.reindex(list(case_names))[["low", "base", "high"]].round(0)
w.index = [case_names[c] for c in w.index]
w.columns = ["NPV 저성장(억)", "NPV 기본(억)", "NPV 고성장(억)"]
st.dataframe(w, width="stretch")

st.caption("근거: outputs/alt_compare_v3.csv, outputs/network_plan_{대안}_v3.csv, outputs/whatif_results.csv. "
           "운영비 배수는 반경별 시뮬레이션 운영비(12분 5,508원, 15분 5,642원)에 같은 비율로 곱한다. 전체 재계산: python -B -m src.run_all")
