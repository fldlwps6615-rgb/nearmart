"""Financial charts from the live engine result and unchanged baseline."""
import math
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from app import data
from app.style import ACCENT, MUTED, GOOD, BAD, PLOTLY_TEMPLATE, PLOTLY_CONFIG


def financial_frame(annual):
    frame = annual.copy(deep=True)
    required = {"year", "cum_cash_flow", "ebitda_eok"}
    if required - set(frame):
        raise ValueError(f"재무 차트 필수 열 누락: {sorted(required - set(frame))}")
    unit = data.load_effective_config()["finance"]["krw_per_eok"]
    calculated = frame["cum_cash_flow"] / unit
    if "cum_cash_flow_eok" in frame and not frame["cum_cash_flow_eok"].equals(calculated):
        raise ValueError("누적 현금흐름의 원·억 원 환산값이 일치하지 않습니다.")
    frame["cum_cash_flow_eok"] = calculated
    if not frame[["year", "cum_cash_flow_eok", "ebitda_eok"]].map(lambda value: math.isfinite(float(value))).all().all():
        raise ValueError("재무 차트에 유한하지 않은 값이 있습니다.")
    return frame.sort_values("year")


def cash_flow_figure(annual, baseline, kpi):
    current = financial_frame(annual)
    reference = financial_frame(baseline)
    unit = data.load_effective_config()["finance"]["krw_per_eok"]
    limit = float(kpi["funding_limit"]) / unit
    minimum = current.loc[current["cum_cash_flow_eok"].idxmin()]
    low = float(minimum["cum_cash_flow_eok"])
    headroom = limit + low
    status = f"한도까지 {headroom:,.1f}억 여유" if headroom >= 0 else f"한도 {abs(headroom):,.1f}억 초과"
    title = f"누적 현금흐름 최저점 {low:,.1f}억, {status}"
    fig = go.Figure(layout=dict(template=PLOTLY_TEMPLATE))
    fig.add_trace(go.Scatter(x=current["year"].tolist(), y=current["cum_cash_flow_eok"].tolist(),
                             name="현재 조건", mode="lines", line=dict(color=ACCENT, width=3),
                             hovertemplate="현재 조건 · %{x:.0f}년<br>%{y:,.1f}억 원<extra></extra>"))
    fig.add_trace(go.Scatter(x=reference["year"].tolist(), y=reference["cum_cash_flow_eok"].tolist(),
                             name="기준", mode="lines", line=dict(color=MUTED, width=2, dash="dot"),
                             hovertemplate="기준 · %{x:.0f}년<br>%{y:,.1f}억 원<extra></extra>"))
    fig.add_hline(y=-limit, line_dash="dot", line_color=BAD, line_width=1.5,
                  annotation_text=f"자금 한도 −{limit:,.0f}억", annotation_position="bottom left",
                  annotation_font_color=BAD)
    fig.add_trace(go.Scatter(x=[int(minimum["year"])], y=[low], mode="markers+text",
                             marker=dict(color=ACCENT, size=7), text=[f"최저 {low:,.1f}억"],
                             textposition="top center", textfont=dict(color=ACCENT, size=12),
                             showlegend=False, cliponaxis=False,
                             hovertemplate="최저점 · %{x:.0f}년<br>%{y:,.1f}억 원<extra></extra>"))
    lower = min(-limit, low, float(reference["cum_cash_flow_eok"].min()))
    upper = max(0.0, float(current["cum_cash_flow_eok"].max()), float(reference["cum_cash_flow_eok"].max()))
    padding = (upper - lower) * 0.10
    fig.update_layout(title_text=title, height=420, xaxis_title="연도", yaxis_title="누적 현금흐름 (억 원)")
    fig.update_xaxes(tickformat="d")
    fig.update_yaxes(range=[lower - padding, upper + padding])
    return fig


def ebitda_figure(annual, kpi):
    current = financial_frame(annual)
    start = data.load_effective_config()["demand"]["forecast_start_year"]
    current = current.loc[current["year"].ge(start)]
    if current.empty:
        raise ValueError("영업기간 EBITDA 자료가 없습니다.")
    last = current.iloc[-1]
    positive_year = kpi["ebitda_positive_year"]
    transition = f"{positive_year}년 EBITDA 흑자 전환" if isinstance(positive_year, int) else "평가기간 내 EBITDA 흑자 미전환"
    title = f"{transition}, {int(last['year'])}년 {last['ebitda_eok']:,.1f}억"
    colors = [BAD if value < 0 else GOOD if value > 0 else MUTED for value in current["ebitda_eok"]]
    fig = go.Figure(go.Bar(x=current["year"].tolist(), y=current["ebitda_eok"].tolist(),
                           name="연간 EBITDA", marker=dict(color=colors),
                           hovertemplate="%{x:.0f}년 EBITDA<br>%{y:,.1f}억 원<extra></extra>"),
                    layout=dict(template=PLOTLY_TEMPLATE))
    fig.update_layout(title_text=title, height=370, xaxis_title="연도", yaxis_title="EBITDA (억 원)")
    fig.update_xaxes(tickformat="d")
    return fig


def annual_csv_bytes(annual):
    return financial_frame(annual).to_csv(index=False).encode("utf-8-sig")


def render(cond, annual, kpi, baseline):
    try:
        cash = cash_flow_figure(annual, baseline, kpi)
        ebitda = ebitda_figure(annual, kpi)
        export = financial_frame(annual)
    except ValueError as exc:
        st.error(f"재무 차트 검산을 중단했습니다: {exc}")
        st.stop()
    st.caption("누적 현금흐름은 할인 전 금액입니다. 할인율은 NPV·할인 회수 연도에 반영되며, 점선은 같은 대안의 기본 시장·기본 조건입니다.")
    with st.container(key="glass_cash_flow"):
        st.plotly_chart(cash, theme=None, config=PLOTLY_CONFIG, width="stretch", key="cash_flow_chart")
    with st.container(key="glass_ebitda"):
        st.plotly_chart(ebitda, theme=None, config=PLOTLY_CONFIG, width="stretch", key="ebitda_chart")
    with st.expander("연간 계산표 · CSV 내려받기"):
        columns = {"year": "연도", "orders": "주문 수 (건)", "revenue_eok": "매출 (억 원)",
                   "ebitda_eok": "EBITDA (억 원)", "cash_flow_eok": "순현금흐름 (억 원)",
                   "cum_cash_flow_eok": "누적 현금흐름 (억 원)"}
        st.dataframe(export[list(columns)].rename(columns=columns), hide_index=True, width="stretch")
        st.download_button("현재 조건의 연간 CSV 내려받기", data=annual_csv_bytes(annual),
                           file_name=f"NearMart_{cond.alt}_{cond.scen}_annual.csv", mime="text/csv",
                           key="annual_csv_download", on_click="ignore")
        st.caption("CSV에는 현재 조건의 전체 연간 계산 열이 포함됩니다. 금액의 _eok 열은 억 원, 나머지 금액 열은 원 단위입니다.")


