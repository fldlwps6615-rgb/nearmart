"""Alternative comparison from selected baseline rows, independent of UI shocks."""
import math
import plotly.graph_objects as go
import streamlit as st
from app import data
from app.style import ALT_COLORS, PLOTLY_TEMPLATE, PLOTLY_CONFIG

PATTERNS = ("/", "", ".")


def comparison_data():
    reference = data.load_alt_compare()
    selected = reference.loc[reference["selected"].eq(True)].copy()
    if selected.empty or selected.duplicated(["alternative", "scenario"]).any():
        raise ValueError("대안 비교 기준표가 없거나 대안·시나리오가 중복됩니다.")
    alternatives = data.load_alternatives()
    if set(alternatives) - set(ALT_COLORS):
        raise ValueError(f"ALT_COLORS에 대안 색상이 없습니다: {sorted(set(alternatives) - set(ALT_COLORS))}")
    scenarios = data.load_effective_config()["finance"]["scenarios"]
    expected = {(alt, scen) for alt in alternatives for scen in scenarios}
    actual = set(selected[["alternative", "scenario"]].itertuples(index=False, name=None))
    if expected != actual:
        raise ValueError("대안 비교표의 대안·시나리오 조합이 완전하지 않습니다.")
    if not selected[["npv_eok", "max_funding_eok"]].map(lambda value: math.isfinite(float(value))).all().all():
        raise ValueError("대안 비교표에 유한하지 않은 값이 있습니다.")
    return selected, alternatives, scenarios


def ranking_title(selected, chosen):
    base_scenario = data.load_effective_config()["finance"]["default_scenario"]
    rows = selected.loc[selected["scenario"].eq(base_scenario)].set_index("alternative")
    rank = rows["npv_eok"].rank(ascending=False, method="min")
    winner = rows["npv_eok"].idxmax()
    title = f"{base_scenario} NPV 1위 {winner}안 {rows.loc[winner, 'npv_eok']:,.1f}억"
    if chosen != winner:
        title += f", {chosen}안은 {int(rank.loc[chosen])}위"
    return title


def grouped_npv_figure(chosen):
    selected, alternatives, scenarios = comparison_data()
    if chosen not in alternatives:
        raise ValueError(f"비교표에 없는 대안입니다: {chosen}")
    fig = go.Figure(layout=dict(template=PLOTLY_TEMPLATE))
    for index, scen in enumerate(scenarios):
        rows = selected.loc[selected["scenario"].eq(scen)].set_index("alternative").loc[alternatives]
        fig.add_trace(go.Bar(x=alternatives, y=rows["npv_eok"].tolist(), name=scen,
                             marker=dict(color=[ALT_COLORS[alt] for alt in alternatives],
                                         opacity=[1.0 if alt == chosen else 0.30 for alt in alternatives],
                                         pattern=dict(shape=PATTERNS[index % len(PATTERNS)], solidity=0.15, fillmode="overlay")),
                             hovertemplate=f"%{{x}}안 · {scen}<br>NPV %{{y:,.1f}}억 원<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.28, title_text=ranking_title(selected, chosen),
                      height=430, xaxis_title="대안", yaxis_title="NPV (억 원)")
    return fig


def risk_return_figure(chosen):
    selected, alternatives, _ = comparison_data()
    if chosen not in alternatives:
        raise ValueError(f"비교표에 없는 대안입니다: {chosen}")
    base_scenario = data.load_effective_config()["finance"]["default_scenario"]
    base = selected.loc[selected["scenario"].eq(base_scenario)].set_index("alternative")
    low = selected.loc[selected["scenario"].eq("low")].set_index("alternative")
    if (base["max_funding_eok"] < 0).any():
        raise ValueError("최대 자금 소요는 음수일 수 없습니다.")
    max_funding = float(base["max_funding_eok"].max())
    size_reference = 2 * max_funding / (44 ** 2) if max_funding else 1.0
    fig = go.Figure(layout=dict(template=PLOTLY_TEMPLATE))
    for alt in alternatives:
        fig.add_trace(go.Scatter(x=[float(low.loc[alt, "npv_eok"])], y=[float(base.loc[alt, "npv_eok"])],
                                 name=f"{alt}안", text=[alt], mode="markers+text", textposition="top center",
                                 textfont=dict(color=ALT_COLORS[alt], size=15 if alt == chosen else 12),
                                 opacity=1.0 if alt == chosen else 0.30,
                                 marker=dict(color=ALT_COLORS[alt], size=[float(base.loc[alt, "max_funding_eok"])],
                                             sizemode="area", sizeref=size_reference, sizemin=7,
                                             line=dict(color=ALT_COLORS[alt], width=3 if alt == chosen else 1)),
                                 customdata=[[float(base.loc[alt, "max_funding_eok"])]],
                                 hovertemplate=f"{alt}안<br>low NPV %{{x:,.1f}}억 원<br>{base_scenario} NPV %{{y:,.1f}}억 원<br>최대 자금 소요 %{{customdata[0]:,.1f}}억 원<extra></extra>"))
    title = (f"{chosen}안 low NPV {low.loc[chosen, 'npv_eok']:,.1f}억 · {base_scenario} NPV {base.loc[chosen, 'npv_eok']:,.1f}억, "
             f"최대 자금 {base.loc[chosen, 'max_funding_eok']:,.1f}억")
    fig.update_layout(title_text=title, height=430, xaxis_title="low NPV (억 원)",
                      yaxis_title=f"{base_scenario} NPV (억 원)")
    fig.update_xaxes(rangemode="tozero")
    fig.update_yaxes(rangemode="tozero")
    return fig


def render(cond):
    try:
        bars = grouped_npv_figure(cond.alt)
        scatter = risk_return_figure(cond.alt)
    except ValueError as exc:
        st.error(f"대안 비교 검산을 중단했습니다: {exc}")
        st.stop()
    st.caption("저장된 기본 조건의 대안 비교입니다. 선택한 대안을 진하게 표시하며, 할인율·인건비·거점 제외 조정은 재무 탭에 반영됩니다.")
    with st.container(key="glass_alt_npv"):
        st.plotly_chart(bars, theme=None, config=PLOTLY_CONFIG, width="stretch", key="alt_npv_chart")
    with st.container(key="glass_alt_scatter"):
        st.plotly_chart(scatter, theme=None, config=PLOTLY_CONFIG, width="stretch", key="alt_scatter_chart")
        st.caption("점의 면적은 기본 시장 시나리오의 최대 자금 소요에 비례합니다.")

