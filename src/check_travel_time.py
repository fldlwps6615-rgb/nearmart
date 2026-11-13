"""17_travel_time_check.csv 60건으로 이동시간 모듈 검증 (허용 오차 ±0.01분)
사용법: python check_travel_time.py <data 폴더> <outputs 폴더>
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from travel_time import TravelTime  # noqa: E402

data_dir, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)
tt = TravelTime(data_dir)
chk = pd.read_csv(os.path.join(data_dir, "17_travel_time_check.csv"), encoding="utf-8-sig")

rows = []
for r in chk.itertuples():
    straight, cross, bridge, road = tt.road(r.from_lat, r.from_lon, r.from_gu, r.to_lat, r.to_lon, r.to_gu)
    spd = tt.speed(r.depart_hour, r.from_gu, r.to_gu, r.weather)
    mins = road / spd * 60
    exp_bridge = r.bridge_used if isinstance(r.bridge_used, str) else None
    rows.append({
        "case_id": r.case_id,
        "straight_km": round(straight, 4), "exp_straight_km": r.straight_km,
        "cross_river": "Y" if cross else "N", "exp_cross_river": r.cross_river,
        "bridge_used": bridge, "exp_bridge_used": exp_bridge,
        "road_km": round(road, 4), "exp_road_km": r.road_km,
        "speed_kmh": round(spd, 4), "exp_speed_kmh": r.speed_kmh,
        "travel_min": round(mins, 4), "exp_travel_min": r.travel_min,
        "diff_min": round(mins - r.travel_min, 4),
    })
res = pd.DataFrame(rows)
res["pass"] = res["diff_min"].abs() <= 0.01
res["bridge_match"] = res["bridge_used"].fillna("") == res["exp_bridge_used"].fillna("")
res["cross_match"] = res["cross_river"] == res["exp_cross_river"]
res.to_csv(os.path.join(out_dir, "travel_check_result.csv"), index=False, encoding="utf-8-sig")

print(f"통과 {res['pass'].sum()}/{len(res)}건 | 최대 오차 {res['diff_min'].abs().max():.4f}분")
print(f"한강 횡단 판정 일치 {res['cross_match'].sum()}/{len(res)} | 교량 선택 일치 {res['bridge_match'].sum()}/{len(res)}")
bad = res[~res["pass"] | ~res["bridge_match"] | ~res["cross_match"]]
if len(bad):
    print(bad.to_string())
