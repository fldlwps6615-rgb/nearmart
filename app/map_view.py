"""Assignment-based network map. Counts come from outputs; no redistribution."""
from copy import deepcopy
from html import escape
import math

import pandas as pd
import pydeck as pdk
from pydeck.types import String
import streamlit as st
from app import data

# Display-only constants: requested view, palette, opacity, and marker sizing.
PALETTE = (
    (32, 109, 191), (232, 104, 49), (27, 148, 116), (142, 87, 181),
    (219, 76, 121), (143, 117, 57), (40, 157, 176), (164, 72, 56),
    (104, 130, 42), (75, 92, 166), (194, 143, 35), (128, 104, 130),
)
RADII = {"S": 6, "M": 9, "L": 13}
VIEW = {"latitude": 37.56, "longitude": 126.98, "zoom": 10.3}


def _round_coordinates(value):
    if isinstance(value, list):
        return [_round_coordinates(item) for item in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


@st.cache_data(show_spinner=False)
def rounded_geo():
    geo = deepcopy(data.load_geo())
    for feature in geo["features"]:
        geometry = feature["geometry"]
        if "coordinates" in geometry:
            geometry["coordinates"] = _round_coordinates(geometry["coordinates"])
    return geo


def assignment_years(alt):
    return sorted(data.load_assignment(alt)["year"].astype(int).unique().tolist())


def _dong_tooltip(properties, site, orders, delivery):
    delivery_label = f"{delivery:,.2f}분" if pd.notna(delivery) and math.isfinite(float(delivery)) else "—"
    location = escape(f"{properties['gu']} {properties['dong']}")
    return (f"{location}\n담당 거점: {escape(site or '미서비스')}"
            f"\n하루 예상 주문: {orders:,.2f}건\n예상 평균 배송시간: {delivery_label}")


@st.cache_data(show_spinner=False)
def prepare_map(alt, year, excluded_sites=(), relocations=()):
    assignment = data.assignment_for(alt, relocations)
    if year not in assignment_years(alt):
        raise ValueError(f"배정표에 없는 연도입니다: {year}")
    current = assignment.loc[assignment["year"].eq(year)].copy()
    if current["adm_cd2"].duplicated().any():
        raise ValueError("선택 연도의 행정동 배정이 중복됩니다.")
    current["site_id"] = current["site_id"].fillna("")
    plan = data.plan_for(alt, relocations)
    excluded = set(excluded_sites) & set(plan["site_id"])
    closing = pd.to_numeric(plan["close_year"], errors="raise")
    active = plan.loc[plan["open_year"].le(year) & (closing.isna() | closing.gt(year))].copy()
    assigned_sites = set(current.loc[current["site_id"].ne(""), "site_id"])
    if assigned_sites - set(active["site_id"]):
        raise ValueError(f"운영하지 않는 거점에 동이 배정됐습니다: {sorted(assigned_sites - set(active['site_id']))}")
    retained = active.loc[~active["site_id"].isin(excluded)]
    serviced = current.loc[current["site_id"].isin(retained["site_id"])].copy()
    site_colors = {site: list(PALETTE[index % len(PALETTE)])
                   for index, site in enumerate(sorted(plan["site_id"]))}
    moved = {row.site_id for row in plan.itertuples() if "이전" in str(row.note)}
    geo = rounded_geo()
    geo_codes = [feature["properties"]["adm_cd2"] for feature in geo["features"]]
    if len(set(geo_codes)) != len(geo_codes) or set(current["adm_cd2"]) != set(geo_codes):
        raise ValueError("배정표와 경계 파일의 행정동 코드가 일치하지 않습니다.")
    lookup = current.set_index("adm_cd2").to_dict("index")
    retained_ids = set(retained["site_id"])
    for feature in geo["features"]:
        properties = feature["properties"]
        row = lookup[properties["adm_cd2"]]
        site = row["site_id"] if row["site_id"] in retained_ids else ""
        orders = float(row["expected_orders_per_day"]) if site else 0.0
        delivery = row["avg_delivery_min"] if site else None
        properties.update(site_id=site, expected_orders_per_day=orders, avg_delivery_min=delivery,
                          fill_color=site_colors[site] + [153] if site else [0, 0, 0, 0],
                          line_color=[255, 255, 255, 120] if site else [148, 163, 184, 200],
                          tooltip_text=_dong_tooltip(properties, site, orders, delivery))
    totals = current.loc[current["site_id"].ne("")].groupby("site_id").agg(
        dong_count=("adm_cd2", "size"), orders_per_day=("expected_orders_per_day", "sum"))
    sites = data.load_sites().set_index("site_id")
    markers, excluded_markers = [], []
    for row in active.itertuples(index=False):
        site = row.site_id
        if site not in sites.index:
            raise ValueError(f"후보지 좌표가 없습니다: {site}")
        detail = sites.loc[site]
        if not all(math.isfinite(float(detail[column])) for column in ("lon", "lat")):
            raise ValueError(f"후보지 좌표가 유한하지 않습니다: {site}")
        if row.hub_type not in RADII:
            raise ValueError(f"알 수 없는 거점 유형입니다: {row.hub_type}")
        dong_count = int(totals.loc[site, "dong_count"]) if site in totals.index else 0
        orders = float(totals.loc[site, "orders_per_day"]) if site in totals.index else 0.0
        status = "\n제외한 거점 · 수요 재배정 없음" if site in excluded else ""
        status += f"\n{row.note}" if site in moved else ""
        tip = (f"{escape(site)} · {escape(row.hub_type)}형\n{escape(str(detail['gu']))} {escape(str(detail['dong']))}"
               f"\n개점 연도: {int(row.open_year)}년\n{'제외 전 ' if site in excluded else ''}담당 동: {dong_count:,}개"
               f"\n하루 주문 합계: {orders:,.2f}건{status}")
        marker = {"site_id": site, "position": [float(detail["lon"]), float(detail["lat"])],
                  "hub_type": row.hub_type, "radius": RADII[row.hub_type], "color": site_colors[site],
                  "properties": {"tooltip_text": tip}, "text": "X"}
        (excluded_markers if site in excluded else markers).append(marker)
    summary = {"year": int(year), "hub_count": len(markers), "dong_count": len(serviced),
               "orders_per_day": float(serviced["expected_orders_per_day"].sum()),
               "excluded_active_count": len(excluded_markers)}
    return {"geo": geo, "markers": markers, "excluded_markers": excluded_markers,
            "summary": summary, "colors": site_colors}


def make_deck(prepared):
    layers = [
        pdk.Layer("GeoJsonLayer", prepared["geo"], id="dong-assignments", pickable=True,
                  filled=True, stroked=True, get_fill_color="properties.fill_color",
                  get_line_color="properties.line_color", line_width_min_pixels=1,
                  auto_highlight=True),
        pdk.Layer("ScatterplotLayer", prepared["markers"], id="active-hubs", pickable=True,
                  get_position="position", get_radius="radius", radius_units=String("pixels"),
                  get_fill_color="color", stroked=True, get_line_color=[255, 255, 255],
                  line_width_min_pixels=2),
        pdk.Layer("TextLayer", prepared["excluded_markers"], id="excluded-hubs", pickable=True,
                  get_position="position", get_text="text", get_size=30,
                  get_color=[220, 38, 38], font_family=String("Arial"), character_set=String("auto"), font_weight=String("bold"), get_text_anchor=String("middle"),
                  get_alignment_baseline=String("center")),
    ]
    return pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(**VIEW),
                    map_provider="carto", map_style=pdk.map_styles.CARTO_LIGHT,
                    tooltip={"text": "{tooltip_text}", "style": {
                        "backgroundColor": "rgba(255,255,255,0.97)", "color": "#183047",
                        "fontSize": "13px", "padding": "12px", "borderRadius": "8px", "whiteSpace": "pre-line"}})


def render(cond, *, height=620, key="network_assignment_map"):
    years = assignment_years(cond.alt)
    if not years:
        st.error("배정표에 표시할 연도가 없습니다.")
        st.stop()
    year = st.select_slider("배정 기준 연도", options=years, value=min(years),
                            key=f"map_year_{cond.alt}")
    try:
        prepared = prepare_map(cond.alt, year, tuple(sorted(cond.excluded_sites)),
                               tuple(tuple(pair) for pair in cond.relocations))
    except ValueError as exc:
        st.error(f"지도 검산을 중단했습니다: {exc}")
        st.stop()
    map_column, summary_column = st.columns([3, 1], gap="large")
    with map_column:
        event = st.pydeck_chart(make_deck(prepared), height=height, key=key,
                                on_select="rerun", selection_mode="single-object")
        st.caption("권역(색칠된 동)이나 거점(원)을 누르면 아래에 그 거점의 세부 내용이 나옵니다.")
    with summary_column:
        summary = prepared["summary"]
        st.markdown(f"**대안 {cond.alt} · {year}년**")
        st.table(pd.DataFrame({"항목": ["거점 수", "담당 동 수", "하루 주문 합계"],
                               "값": [f"{summary['hub_count']:,}곳", f"{summary['dong_count']:,}개",
                                      f"{summary['orders_per_day']:,.2f}건"]}).set_index("항목"))
        st.caption("원 크기: S < M < L · 빨간 X: 제외한 거점")
        if summary["excluded_active_count"]:
            st.caption(f"제외한 운영 거점 {summary['excluded_active_count']}곳의 동·주문을 집계에서 뺐습니다.")
        with st.expander("거점별 색상"):
            for marker in prepared["markers"]:
                rgb = ",".join(map(str, marker["color"]))
                st.html(f'<div style="font-size:12px;margin-bottom:5px"><span style="color:rgb({rgb})">●</span> '
                        f'{escape(marker["site_id"])} · {escape(marker["hub_type"])}형</div>')
    selected = _selected_site(event)
    if selected:
        render_hub_detail(cond, selected, year)
    st.caption("동 배정·주문은 시뮬레이션 환류(v3) 대안별 배정 결과입니다. 시장·인건비·운영비 조건은 재무에 반영하며, 제외 거점의 수요는 재배정하지 않습니다.")


def _selected_site(event):
    try:
        objects = event.selection.get("objects", {}) if hasattr(event, "selection") else event["selection"]["objects"]
    except (AttributeError, KeyError, TypeError):
        return None
    for layer, items in (objects or {}).items():
        for item in items or []:
            site = item.get("site_id") or (item.get("properties") or {}).get("site_id")
            if site:
                return site
    return None


@st.cache_data(show_spinner=False)
def _capacity(alt):
    model = data.load_models()[alt][0]
    basis = data.load_output("feedback_basis_v3.csv")
    row = basis[basis["T"].astype(int).eq(int(model.radius))].iloc[0]
    return int(model.radius), {kind: float(row[f"capacity_per_day_{kind}"]) for kind in ("S", "M", "L")}


def render_hub_detail(cond, site, year):
    relocations = tuple(tuple(pair) for pair in cond.relocations)
    plan = data.plan_for(cond.alt, relocations).set_index("site_id")
    if site not in plan.index:
        return
    detail = data.candidate_sites().set_index("site_id").loc[site]
    hub = plan.loc[site]
    radius, capacity = _capacity(cond.alt)
    cap = capacity[hub.hub_type]
    base_scen = data.load_effective_config()["finance"]["default_scenario"]
    ops = data.ops_for(cond.alt, base_scen, relocations)
    ops = ops[ops.site_id.eq(site)].sort_values("year")
    assign = data.assignment_for(cond.alt, relocations)
    mine = assign[assign.site_id.eq(site)]
    with st.container(key=f"glass_hub_{site}"):
        close = "운영 중(2046년까지)" if pd.isna(pd.to_numeric(hub.close_year, errors="coerce")) else f"{int(float(hub.close_year))}년 폐점"
        st.markdown(f"#### 거점 {site} · {hub.hub_type}형 · {detail['gu']} {detail['dong']}")
        st.caption(f"{int(hub.open_year)}년 개점 · {close} · 서비스 반경 {radius}분 · 하루 처리 상한 {cap:,.0f}건(시뮬레이션 기준) · {hub.note}")
        cur = mine[mine.year.eq(year)]
        y = ops[ops.year.eq(year)]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{year}년 담당 동", f"{len(cur):,}개")
        orders = float(y.orders_per_day.iloc[0]) if not y.empty else 0.0
        c2.metric(f"{year}년 하루 주문", f"{orders:,.0f}건")
        c3.metric("처리 상한 대비", f"{orders / cap:.0%}" if cap else "-")
        late = float(y.late_rate.iloc[0]) if not y.empty else 0.0
        c4.metric("정시율", f"{1 - late:.1%}")
        if not ops.empty:
            unit = ops[list(("contract_rider_cost_per_order_krw", "employed_rider_cost_per_order_krw",
                             "picker_cost_per_order_krw"))].sum(axis=1) * cond.cost_factor
            progress = pd.DataFrame({"연도": ops.year.astype(int).astype(str), "하루 주문(건)": ops.orders_per_day.round(0),
                                     "처리 상한(건)": round(cap), "가동률": (ops.orders_per_day / cap).map(lambda v: f"{v:.0%}"),
                                     "주문당 운영비(원, 2027년 단가)": unit.round(0), "정시율": (1 - ops.late_rate).map(lambda v: f"{v:.1%}")})
            st.markdown("**연도별 진행(기본 시장)**")
            st.line_chart(progress.set_index("연도")[["하루 주문(건)", "처리 상한(건)"]], height=220)
            st.dataframe(progress, hide_index=True, width="stretch")
        if not cur.empty:
            dongs = data.load_sites  # noqa: F841 (keep module import used)
            geo = {f["properties"]["adm_cd2"]: f["properties"] for f in data.load_geo()["features"]}
            listing = cur.assign(동=cur.adm_cd2.map(lambda c: f"{geo.get(c, {}).get('gu', '')} {geo.get(c, {}).get('dong', c)}"))
            listing = listing[["동", "expected_orders_per_day", "avg_delivery_min"]].rename(
                columns={"expected_orders_per_day": "하루 주문(건)", "avg_delivery_min": "평균 배송시간(분)"})
            st.markdown(f"**{year}년 담당 동 {len(listing)}곳**")
            st.dataframe(listing.sort_values("하루 주문(건)", ascending=False).round(1), hide_index=True, width="stretch")






