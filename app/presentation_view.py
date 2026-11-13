"""Presentation mode reuses the existing map and cumulative cash-flow renderer."""
import streamlit as st
from app import map_view, finance_view
from app.style import PLOTLY_CONFIG, presentation_figure


def render(cond, annual, kpi, baseline):
    st.subheader("거점 네트워크")
    map_view.render(cond, height=700, key="presentation_map")
    with st.container(key="glass_presentation_cash"):
        figure = presentation_figure(finance_view.cash_flow_figure(annual, baseline, kpi))
        st.plotly_chart(figure, theme=None, config=PLOTLY_CONFIG, width="stretch", key="presentation_cash_flow")
