"""Fast scenario toggles, baseline changes, and session-only execution history."""
from copy import deepcopy
from dataclasses import asdict, replace
from html import escape
import math
from numbers import Real

import streamlit as st
from app import data, scenario
from app.style import ACCENT, GOOD, BAD, MUTED

PERCENT = 100
HISTORY_LIMIT = 5  # Requested UI history capacity.


def reference_condition(alt):
    return scenario.Conditions(alt, data.load_effective_config()["finance"]["default_scenario"])


def quick_discount_rate():
    return float(data.load_effective_config()["finance"]["validation"]["sensitivity_discount_rate"])


@st.cache_data(show_spinner=False)
def leading_site(alt, scen):
    ops = data.load_ops(alt, scen)
    if ops.empty or ops.duplicated(["site_id", "year"]).any():
        raise ValueError("주문 1위 거점 계산에 유효한 거점·연도별 운영표가 필요합니다.")
    means = ops.groupby("site_id", sort=True)["orders_per_day"].mean()
    if means.isna().any() or not means.map(lambda value: math.isfinite(float(value))).all():
        raise ValueError("연평균 주문에 유한하지 않은 값이 있습니다.")
    site = means.idxmax()  # Sorted IDs make tied maxima deterministic.
    locations = data.load_sites().set_index("site_id")
    detail = locations.loc[site]
    return {"site_id": site, "name": f"{site} · {detail['gu']} {detail['dong']}",
            "mean_orders_per_day": float(means.loc[site])}


def from_widgets():
    return scenario.Conditions(
        alt=st.session_state["condition_alt"], scen=st.session_state["condition_scen"],
        discount_rate=st.session_state["discount_percent"] / PERCENT,
        w1_on=st.session_state["w1_on"], w1_rate=st.session_state["w1_percent"] / PERCENT,
        excluded_sites=list(st.session_state["excluded_sites"]),
        cost_factor=float(st.session_state.get("cost_factor", 1.0)),
    )


def restore_conditions(snapshot):
    cond = scenario.Conditions(**deepcopy(snapshot))
    values = {"condition_alt": cond.alt, "condition_scen": cond.scen,
              "discount_percent": cond.discount_rate * PERCENT,
              "w1_on": cond.w1_on, "w1_percent": cond.w1_rate * PERCENT,
              "excluded_sites": list(cond.excluded_sites), "cost_factor": cond.cost_factor}
    st.session_state.update(values)


def quick_states(cond):
    default = reference_condition(cond.alt)
    return {
        "discount": cond.discount_rate == quick_discount_rate(),
        "labor": cond.w1_on and cond.w1_rate == default.w1_rate,
        "market": cond.scen == "low",
        "exclude": leading_site(cond.alt, cond.scen)["site_id"] in cond.excluded_sites,
    }


def toggled_condition(cond, kind):
    cond = deepcopy(cond)
    default = reference_condition(cond.alt)
    active = quick_states(cond)[kind]
    if kind == "discount":
        return replace(cond, discount_rate=default.discount_rate if active else quick_discount_rate())
    if kind == "labor":
        return replace(cond, w1_on=not active, w1_rate=default.w1_rate)
    if kind == "market":
        return replace(cond, scen=default.scen if active else "low")
    if kind == "exclude":
        site = leading_site(cond.alt, cond.scen)["site_id"]
        excluded = set(cond.excluded_sites)
        excluded.discard(site) if active else excluded.add(site)
        return replace(cond, excluded_sites=sorted(excluded))
    raise ValueError(f"알 수 없는 빠른 조건입니다: {kind}")


def toggle_quick(kind):
    restore_conditions(asdict(toggled_condition(from_widgets(), kind)))


def render_quick():
    cond = from_widgets()
    active = quick_states(cond)
    default = reference_condition(cond.alt)
    top = leading_site(cond.alt, cond.scen)
    st.markdown("### 빠른 조건")
    definitions = [("discount", f"할인율 {quick_discount_rate():.0%}"),
                   ("labor", f"인건비 +{default.w1_rate:.0%}(W1)"),
                   ("market", "시장 low"), ("exclude", "주문 1위 거점 제외")]
    for offset in range(0, len(definitions), 2):
        columns = st.columns(2, gap="small")
        for column, (kind, label) in zip(columns, definitions[offset:offset + 2]):
            key = f"quick_{kind}"
            with column:
                if active[kind]:
                    st.html(f'<style>.st-key-{key} button {{background:{ACCENT}!important; '
                            f'border-color:{ACCENT}!important;color:white!important;}}</style>')
                st.button(label, key=key, type="primary" if active[kind] else "secondary",
                          on_click=toggle_quick, args=(kind,), width="stretch")
    st.caption(f"주문 1위: {top['name']}")
    st.divider()


def is_reference(cond):
    default = reference_condition(cond.alt)
    return (cond.scen == default.scen and cond.discount_rate == default.discount_rate
            and not cond.w1_on and not cond.excluded_sites and cond.cost_factor == 1.0)


def _numeric(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def change_rows(base, current):
    definitions = [("NPV", "npv_eok", False, False),
                   ("최대 자금 소요", "max_funding_eok", True, False),
                   ("EBITDA 흑자 전환", "ebitda_positive_year", True, True),
                   ("할인 회수", "payback_year", True, True)]
    rows = []
    for label, key, smaller_is_better, year in definitions:
        old, new = base[key], current[key]
        before = f"{old:.0f}년" if year and _numeric(old) else str(old) if year else f"{old:,.1f}억 원"
        after = f"{new:.0f}년" if year and _numeric(new) else str(new) if year else f"{new:,.1f}억 원"
        if old == new:
            difference = ("0년" if _numeric(new) else "변화 없음") if year else "0.0억 원"
            color = MUTED
        elif _numeric(old) and _numeric(new):
            delta = new - old
            improved = delta < 0 if smaller_is_better else delta > 0
            difference = f"{delta:+.0f}년" if year else f"{delta:+,.1f}억 원"
            color = GOOD if improved else BAD
        elif _numeric(new):
            difference, color = "달성", GOOD
        else:
            difference, color = "미달성", BAD
        rows.append({"label": label, "before": before, "after": after,
                     "difference": difference, "color": color})
    return rows


def review_deadline():
    cfg = data.load_effective_config()
    return min(year for year in cfg["network"]["assignment_years"] if year > cfg["demand"]["forecast_start_year"])


def broken_rules(base, current):
    unit = data.load_effective_config()["finance"]["krw_per_eok"]
    reasons = []
    if current["max_funding"] > current["funding_limit"]:
        reasons.append(f"최대 자금 {current['max_funding_eok']:,.1f}억 원 > 한도 {current['funding_limit'] / unit:,.0f}억 원")
    sign = lambda value: (value > 0) - (value < 0)
    if sign(current["npv_eok"]) != sign(base["npv_eok"]):
        reasons.append(f"NPV 부호 변화: {base['npv_eok']:,.1f}억 원 → {current['npv_eok']:,.1f}억 원")
    deadline = review_deadline()
    year = current["ebitda_positive_year"]
    if not _numeric(year) or year > deadline:
        result = f"{year:.0f}년" if _numeric(year) else str(year)
        reasons.append(f"EBITDA 흑자 전환 {result}: {deadline}년까지 달성 기준 미달")
    return reasons


def render_changes(cond, base, current):
    with st.container(key="glass_changes"):
        st.markdown("#### 바뀐 점")
        if is_reference(cond):
            st.write("기준 조건입니다")
            return
        reference = reference_condition(cond.alt)
        st.caption(f"대안 {cond.alt} · {reference.scen} 시장 · 기본 조건 대비")
        body = "".join(f'<tr><th>{escape(row["label"])}</th><td>{escape(row["before"])}</td>'
                       f'<td>{escape(row["after"])}</td><td style="color:{row["color"]};font-weight:700">'
                       f'{escape(row["difference"])}</td></tr>' for row in change_rows(base, current))
        st.html('<table class="nm-change-table"><thead><tr><th>항목</th><th>기준</th><th>현재</th><th>차이</th></tr></thead>'
                f'<tbody>{body}</tbody></table>')
        reasons = broken_rules(base, current)
        color = BAD if reasons else GOOD
        verdict = "최종안 재검토" if reasons else "최종안 유지"
        st.html(f'<div class="nm-verdict">결론이 바뀌나? <span style="color:{color};background:{color}18;'
                f'border:1px solid {color}40" class="nm-verdict-pill">{verdict}</span></div>')
        if reasons:
            st.html(f'<div class="nm-rule-reason">{escape(" · ".join(reasons))}</div>')


def snapshot(cond):
    values = asdict(cond)
    values["excluded_sites"] = sorted(set(values["excluded_sites"]))
    return values


def condition_summary(cond):
    default = reference_condition(cond.alt)
    parts = [f"{cond.alt} · {cond.scen}"]
    if cond.discount_rate != default.discount_rate:
        parts.append(f"할인 {cond.discount_rate:.1%}".replace(".0%", "%"))
    if cond.w1_on:
        parts.append(f"인건비 +{cond.w1_rate:.1%}".replace(".0%", "%"))
    if cond.excluded_sites:
        excluded = sorted(cond.excluded_sites)
        label = f"제외 {next(iter(excluded))}"
        parts.append(label + (f" 외 {len(excluded) - 1}곳" if len(excluded) > 1 else ""))
    if cond.cost_factor != 1.0:
        parts.append(f"운영비 ×{cond.cost_factor:.2f}")
    if is_reference(cond):
        parts.append("기본")
    return " · ".join(parts)


def add_execution(history, cond, kpi):
    recent = deepcopy(history)
    entry = {"conditions": snapshot(cond), "summary": condition_summary(cond),
             "npv_eok": float(kpi["npv_eok"]), "max_funding_eok": float(kpi["max_funding_eok"])}
    if recent and recent[0]["conditions"] == entry["conditions"]:
        recent[0] = entry
    else:
        recent.insert(0, entry)
    return recent[:HISTORY_LIMIT]


def record_execution(cond, kpi):
    st.session_state["recent_runs"] = add_execution(st.session_state.get("recent_runs", []), cond, kpi)


def render_history():
    with st.container(key="glass_history"):
        st.markdown("#### 최근 실행")
        st.caption(f"현재 실행 포함 최근 {HISTORY_LIMIT}개 · 클릭하면 복원")
        for index, entry in enumerate(st.session_state.get("recent_runs", [])):
            label = (f"{entry['summary']}  \nNPV {entry['npv_eok']:,.1f}억 · 최대 자금 {entry['max_funding_eok']:,.1f}억")
            st.button(label, key=f"history_restore_{index}", on_click=restore_conditions,
                      args=(deepcopy(entry["conditions"]),), width="stretch")


