"""이동시간 공용 모듈 (문제지 부록 A 규칙 그대로 구현)

순서: 직선거리(haversine) -> 한강 남북 판정(자치구) -> 도로거리(같은 쪽 x1.32 / 횡단은 이륜차 가능 교량 최단 + 0.4km)
      -> 속도(시간대 기본속도 x 출발·도착 자치구 계수 평균 x 기상계수) -> 분
③ 네트워크 설계, ④ 배차, ⑤ 시뮬레이션이 모두 이 모듈 하나를 씁니다.
"""
import io
import math
import os

import numpy as np
import pandas as pd

EARTH_R_KM = 6371.0088
DETOUR = 1.32
BRIDGE_EXTRA_KM = 0.4
SOUTH_GU = {"강서구", "양천구", "구로구", "금천구", "영등포구", "동작구",
            "관악구", "서초구", "강남구", "송파구", "강동구"}  # 여의도는 영등포구 소속이라 자동으로 남쪽
CV = 0.15  # 시뮬레이션용 이동시간 변동계수
SIGMA = math.sqrt(math.log(1 + CV ** 2))


def read_sectioned_csv(path, dtype=None):
    """'#'로 시작하는 줄로 나뉜 CSV(07, 12, 14)를 {구획 제목: DataFrame}으로 읽는다."""
    with open(path, encoding="utf-8-sig") as source:
        text = source.read()
    if dtype is None:
        dtype = {"adm_cd2": str, "adm_cd": str, "site_id": str}
    sections, title, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("#"):
            if buf:  # 앞 표를 닫는다
                sections[title] = pd.read_csv(io.StringIO("\n".join(buf)), encoding="utf-8-sig", dtype=dtype)
                buf, title = [], None
            if title is None:  # 연속된 '#' 줄은 첫 줄을 제목으로
                title = line.lstrip("#").strip()
            continue
        if line.strip():
            buf.append(line)
    if buf:
        sections[title] = pd.read_csv(io.StringIO("\n".join(buf)), encoding="utf-8-sig", dtype=dtype)
    return sections


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def river_side(gu):
    return "S" if gu in SOUTH_GU else "N"


class TravelTime:
    def __init__(self, data_dir):
        b = pd.read_csv(os.path.join(data_dir, "11_bridges.csv"), encoding="utf-8-sig")
        allowed = b["motorcycle_allowed"].astype(str).str.strip().str.lower() == "true"
        self.bridges = b[allowed].reset_index(drop=True)

        sp = read_sectioned_csv(os.path.join(data_dir, "12_speed_profile.csv"))
        hour_tbl = next(v for v in sp.values() if "base_speed_kmh" in v.columns)
        gu_tbl = next(v for v in sp.values() if "gu_speed_factor" in v.columns)
        wx_tbl = next(v for v in sp.values() if "weather_speed_factor" in v.columns)
        self.base_speed = dict(zip(hour_tbl["hour"].astype(int), hour_tbl["base_speed_kmh"].astype(float)))
        self.gu_factor = dict(zip(gu_tbl["gu"].str.strip(), gu_tbl["gu_speed_factor"].astype(float)))
        self.wx_factor = dict(zip(wx_tbl["weather"].str.strip(), wx_tbl["weather_speed_factor"].astype(float)))

    def road(self, from_lat, from_lon, from_gu, to_lat, to_lon, to_gu):
        """(직선 km, 한강 횡단 여부, 사용 교량, 도로 km)"""
        straight = float(haversine_km(from_lat, from_lon, to_lat, to_lon))
        if river_side(from_gu) == river_side(to_gu):
            return straight, False, None, DETOUR * straight
        d1 = haversine_km(from_lat, from_lon, self.bridges["lat"].values, self.bridges["lon"].values)
        d2 = haversine_km(self.bridges["lat"].values, self.bridges["lon"].values, to_lat, to_lon)
        via = DETOUR * (d1 + d2)
        i = int(np.argmin(via))
        return straight, True, self.bridges.loc[i, "bridge"], float(via[i]) + BRIDGE_EXTRA_KM

    def speed(self, depart_hour, from_gu, to_gu, weather="clear"):
        hour = int(math.floor(depart_hour)) % 24  # 출발 시각의 정수 시간으로 고정
        gu_avg = (self.gu_factor[from_gu] + self.gu_factor[to_gu]) / 2
        return self.base_speed[hour] * gu_avg * self.wx_factor[weather]

    def minutes(self, from_lat, from_lon, from_gu, to_lat, to_lon, to_gu, depart_hour, weather="clear"):
        """결정론적 이동시간(분). 설계·벤치마크에 사용."""
        _, _, _, road_km = self.road(from_lat, from_lon, from_gu, to_lat, to_lon, to_gu)
        return road_km / self.speed(depart_hour, from_gu, to_gu, weather) * 60

    @staticmethod
    def stochastic_multiplier(rng, size=None):
        """시뮬레이션용 확률 승수 exp(Z), Z~N(-σ²/2, σ). 평균 1, 변동계수 0.15."""
        return np.exp(rng.normal(-SIGMA ** 2 / 2, SIGMA, size))
