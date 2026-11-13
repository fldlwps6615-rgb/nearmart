"""기본 입력은 outputs/clean/. 원본 접근은 정제 단계에서만 명시적으로 허용한다."""
import json
from pathlib import Path

import pandas as pd

from src.travel_time import read_sectioned_csv

ROOT = Path(__file__).resolve().parents[2]


def load_config(path=None):
    with Path(path or ROOT / "config.json").open(encoding="utf-8-sig") as source:
        return json.load(source)


def load_file(name, *, source="clean", allow_raw=False, config=None):
    cfg = config or load_config()
    if source not in {"raw", "clean"}:
        raise ValueError("source는 raw 또는 clean이어야 합니다.")
    if source == "raw" and not allow_raw:
        raise ValueError("원본 읽기는 정제 단계만 allow_raw=True로 허용합니다.")
    base = (ROOT / cfg["paths"][source]).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError("입력 폴더 밖 경로는 허용하지 않습니다.")
    if path.suffix in {".json", ".geojson"}:
        with path.open(encoding=cfg["io"]["encoding"]) as f:
            return json.load(f)
    dtypes = {column: str for column in cfg["io"]["string_columns"]}
    if path.name in cfg["io"]["sectioned_files"]:
        return read_sectioned_csv(path, dtype=dtypes)
    return pd.read_csv(path, encoding=cfg["io"]["encoding"], dtype=dtypes)


def load_all(*, source="clean", allow_raw=False, config=None):
    cfg = config or load_config()
    return {name: load_file(name, source=source, allow_raw=allow_raw, config=cfg)
            for name in cfg["files"]}


def tables(value):
    return value if isinstance(value, dict) else {"main": value}


def active_od(config=None):
    """13번 값은 보존하며 정제 후보지에 해당하는 OD만 반환한다."""
    cfg = config or load_config()
    sites = load_file("03_candidate_sites.csv", config=cfg)
    od = load_file("13_od_site_dong.csv", config=cfg)
    return od[od.site_id.isin(sites.site_id)].copy()


def demand_orders(config=None):
    return load_file("05_pilot_orders.csv", config=config).query("demand_included == 1").copy()


def delivery_kpi_orders(config=None):
    return load_file("05_pilot_orders.csv", config=config).query("delivery_kpi_eligible == 1").copy()


def rider_trip_orders(config=None):
    return load_file("05_pilot_orders.csv", config=config).query("rider_trip_eligible == 1").copy()


def weight_analysis_orders(config=None):
    return load_file("05_pilot_orders.csv", config=config).query("weight_analysis_eligible == 1").copy()
