"""Cached, read-only access to analysis outputs.

Call get_finance_engine(discount_rate) with a numeric fractional rate.
Cached engines are shared: callers must not mutate their config or attributes.
Clear the Streamlit data/resource caches after replacing a dataset.
"""
from copy import deepcopy
from datetime import datetime
import json
import math
from numbers import Real
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
STRING_COLUMNS = {name: str for name in ("site_id", "adm_cd2", "relocated_from")}


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=STRING_COLUMNS)


VERSION = "v3"  # 시뮬레이션 되먹임(환류) v3 = 최종 결과
ALT_ORDER = ["A", "B", "B2", "C15", "D15"]


@st.cache_data(show_spinner=False)
def load_alt_compare() -> pd.DataFrame:
    frame = _read_csv(OUTPUTS / f"alt_compare_{VERSION}.csv")
    return frame.loc[frame["selected"].eq(1)].drop_duplicates(["alternative", "scenario"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_alternatives() -> list[str]:
    present = set(load_alt_compare()["alternative"].dropna())
    return [alt for alt in ALT_ORDER if alt in present]


@st.cache_resource(show_spinner="엔진과 대안 계획을 불러오는 중…")
def load_models():
    """대안별 (FeedbackModel, plan frame, plan tuple). v3 처리능력·운영비·지연율이 반영된 공용 엔진."""
    from src.common.io import load_config
    from src.submission.evaluation import feedback_models, plan_input
    config = deepcopy(load_config(ROOT / "config.json"))
    rows, models = feedback_models(config, VERSION)
    result = {}
    for alt in load_alternatives():
        model = models[rows.loc[alt].loc["base", "T"]]
        frame, plan = plan_input(model, f"{alt}_{VERSION}")
        result[alt] = (model, frame, plan)
    return result


def model_for(alt: str):
    _validate_keys(alt)
    return load_models()[alt]


@st.cache_data(show_spinner=False)
def _operating(alt: str, scen: str, band: str = "mid"):
    model, frame, plan = model_for(alt)
    ops, state = model.operating(plan, model.radius, scen, band, details=True)
    yearly = model.summaries(plan, state).set_index("year")
    from src.submission.evaluation import assignment
    assign = assignment(model, state, yearly)
    return ops, yearly, assign


def _validate_keys(alt: str, scen: str | None = None) -> None:
    if alt not in load_alternatives():
        raise ValueError(f"Unknown alternative: {alt!r}")
    if scen is not None:
        reference = load_alt_compare()
        scenarios = reference.loc[reference["alternative"].eq(alt), "scenario"]
        if scen not in scenarios.values:
            raise ValueError(f"Unknown scenario for {alt!r}: {scen!r}")


@st.cache_data(show_spinner=False)
def load_plan(alt: str) -> pd.DataFrame:
    _validate_keys(alt)
    return _read_csv(OUTPUTS / f"network_plan_{alt}_{VERSION}.csv")


def load_ops(alt: str, scen: str) -> pd.DataFrame:
    """v3 운영표: 시뮬레이션으로 정한 처리능력·주문당 운영비·지연율이 반영된 거점×연도 표."""
    _validate_keys(alt, scen)
    ops = _operating(alt, scen)[0].copy(deep=True)
    ops["site_id"] = ops["site_id"].astype(str)
    return ops


def load_year(alt: str, scen: str) -> pd.DataFrame:
    _validate_keys(alt, scen)
    return _operating(alt, scen)[1].reset_index()


def load_assignment(alt: str) -> pd.DataFrame:
    _validate_keys(alt)
    base = load_effective_config()["finance"]["default_scenario"]
    frame = _operating(alt, base)[2].copy(deep=True)
    frame["adm_cd2"] = frame["adm_cd2"].astype(str)
    frame["site_id"] = frame["site_id"].where(frame["site_id"].notna(), None)
    return frame


@st.cache_data(show_spinner=False)
def load_output(name: str) -> pd.DataFrame:
    return _read_csv(OUTPUTS / name)


@st.cache_data(show_spinner=False)
def load_sites() -> pd.DataFrame:
    return _read_csv(OUTPUTS / "clean" / "03_candidate_sites.csv")


@st.cache_data(show_spinner=False)
def load_geo() -> dict:
    with (OUTPUTS / "clean" / "02_dong_boundary.geojson").open(encoding="utf-8-sig") as source:
        return json.load(source)


@st.cache_data(show_spinner=False)
def load_bm() -> pd.DataFrame:
    return _read_csv(OUTPUTS / "bm_kpi.csv")


@st.cache_data(show_spinner=False)
def load_investor() -> pd.DataFrame:
    return _read_csv(OUTPUTS / f"investor_condition_cost_{VERSION}.csv")


@st.cache_data(show_spinner=False)
def _version_label(modified_at: float) -> str:
    updated = datetime.fromtimestamp(modified_at, tz=ZoneInfo("Asia/Seoul"))
    return f"데이터 기준 {updated:%H:%M}"


def data_version() -> str:
    # Check the actual mtime on each call; cache formatting by timestamp so
    # replacing alt_compare.csv cannot leave the badge stuck at an old time.
    return f"시뮬레이션 환류 {VERSION} · " + _version_label((OUTPUTS / f"alt_compare_{VERSION}.csv").stat().st_mtime)


@st.cache_resource(show_spinner=False)
def get_finance_engine(discount_rate: float):
    if isinstance(discount_rate, bool) or not isinstance(discount_rate, Real):
        raise TypeError("discount_rate must be a numeric fractional rate.")
    if not math.isfinite(discount_rate):
        raise ValueError("discount_rate must be finite.")
    # 보고서·T4와 같은 v3 엔진 설정에 할인율만 바꾼다.
    from src.submission.evaluation import discount_engine
    model = next(iter(load_models().values()))[0]
    return discount_engine(model, float(discount_rate))


@st.cache_data(show_spinner=False)
def load_effective_config() -> dict:
    """Return an isolated copy of the output configuration through the data layer."""
    with (OUTPUTS / "network_effective_config.json").open(encoding="utf-8-sig") as source:
        return json.load(source)



# ── 거점 이전 시뮬레이션 ─────────────────────────────────────────
def candidate_sites() -> pd.DataFrame:
    """후보지 68곳(정제 후)과 유형·입주 가능 연도·임대료. 유형은 면적으로 정해진다(문제지 5.4)."""
    model = next(iter(load_models().values()))[0]
    frame = model.engine.sites.reset_index()
    return frame[["site_id", "status", "gu", "dong", "lat", "lon", "hub_type", "area_m2", "monthly_rent_krw",
                  "available_from_year", "bike_bays"]].copy()


def relocated_plan(alt: str, relocations: tuple = ()) -> pd.DataFrame:
    """relocations = ((기존 거점, 이전할 후보지), ...). 이전 후보지는 원래 개점 연도와 입주 가능 연도 중 늦은 해에 연다."""
    frame = load_plan(alt).copy(deep=True)
    if not relocations:
        return frame
    sites = candidate_sites().set_index("site_id")
    for source, target in relocations:
        rows = frame.index[frame["site_id"].eq(source)]
        if len(rows) != 1:
            raise ValueError(f"계획에 없는 거점입니다: {source}")
        if target in set(frame["site_id"]):
            raise ValueError(f"이미 쓰고 있는 후보지입니다: {target}")
        if target not in sites.index:
            raise ValueError(f"후보지 목록에 없습니다: {target}")
        info = sites.loc[target]
        row = rows[0]
        opening = max(int(frame.at[row, "open_year"]), int(info["available_from_year"]))
        frame.at[row, "site_id"] = target
        frame.at[row, "hub_type"] = info["hub_type"]
        frame.at[row, "open_year"] = opening
        frame.at[row, "note"] = f"{source}에서 이전(시뮬레이션)"
    return frame


@st.cache_data(show_spinner="이전한 계획으로 동 배정·운영을 다시 계산하는 중…")
def _operating_relocated(alt: str, scen: str, relocations: tuple, band: str = "mid"):
    from src.submission.evaluation import assignment
    model = load_models()[alt][0]
    frame = relocated_plan(alt, relocations)
    rows = model.engine._plan(frame)
    plan = tuple(sorted((model.index[r["site_id"]], r["open_year"], r["close_year"])
                        for r in rows if r["open_year"] < r["close_year"]))
    ops, state = model.operating(plan, model.radius, scen, band, details=True)
    yearly = model.summaries(plan, state).set_index("year")
    assign = assignment(model, state, yearly)
    ops = ops.copy(deep=True)
    ops["site_id"] = ops["site_id"].astype(str)
    assign = assign.copy(deep=True)
    assign["adm_cd2"] = assign["adm_cd2"].astype(str)
    assign["site_id"] = assign["site_id"].where(assign["site_id"].notna(), None)
    return frame, ops, yearly, assign


def plan_for(alt: str, relocations: tuple = ()) -> pd.DataFrame:
    return relocated_plan(alt, relocations) if relocations else load_plan(alt)


def ops_for(alt: str, scen: str, relocations: tuple = ()) -> pd.DataFrame:
    return _operating_relocated(alt, scen, relocations)[1].copy(deep=True) if relocations else load_ops(alt, scen)


def year_for(alt: str, scen: str, relocations: tuple = ()) -> pd.DataFrame:
    return _operating_relocated(alt, scen, relocations)[2].reset_index() if relocations else load_year(alt, scen)


def assignment_for(alt: str, relocations: tuple = ()) -> pd.DataFrame:
    if not relocations:
        return load_assignment(alt)
    base = load_effective_config()["finance"]["default_scenario"]
    return _operating_relocated(alt, base, relocations)[3].copy(deep=True)
