"""수요 결과 그림: 맑은 고딕, 한국어, 원본 GeoJSON 경계."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd

from src.common.io import ROOT, load_file
from src.demand.model import ramp


def plot_all(cfg, dong, training, rates, candidates, validation, yearly, model, output):
    p = cfg["pilot_diagnosis"]
    path = ROOT / p["figures_dir"]
    path.mkdir(parents=True, exist_ok=True)
    font_manager.fontManager.addfont(Path(p["font_path"]))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=p["font_path"]).get_name(),
                         "axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10})
    def save(fig, name):
        fig.savefig(path / f"demand_{name}.png", dpi=160, facecolor="white")
        plt.close(fig)

    chosen = candidates[candidates.selected.eq(1)].iloc[0]
    days = np.arange(0, 366)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
    for row in candidates.itertuples():
        axes[0].plot(days, ramp(days, row.a, row.tau), linewidth=2 if row.selected else 1,
                     alpha=1 if row.accepted else .25, linestyle="-" if row.accepted else "--",
                     label=f"τ={row.tau:g}일" + (" (중앙)" if row.selected else "") + ("" if row.accepted else " (제외)"))
    axes[0].axvspan(0, 90, alpha=.08, color="black", label="최대 관측기간")
    axes[0].axhline(1, color="gray", linestyle=":")
    axes[0].set(xlabel="개점 후 경과일", ylabel="성숙 수요 대비 수준", title="짧은 관측기간과 성숙 수준 외삽")
    axes[0].legend(fontsize=8, loc="lower right")
    axes[1].plot(candidates.tau, candidates.rmse_log, marker="o", color="#176b81")
    axes[1].axhline(chosen.rmse_log*cfg["demand"]["rmse_accept_ratio"], color="#b25131", linestyle="--", label="최저 로그 RMSE × 1.10")
    axes[1].set(xlabel="고정 τ (일)", ylabel="로그 주문 RMSE", title="범위에 포함할 후보의 적합 오차")
    axes[1].legend()
    fig.suptitle("램프업: 허용 후보만 불확실성 범위에 반영", fontsize=15)
    save(fig, "ramp")

    variables = model["variables"]
    count = max(len(variables), 1)
    fig, axes = plt.subplots(count, 1, figsize=(10, 2.8*count), layout="constrained", squeeze=False)
    labels = {"single_hh_ratio": "1인가구 비율", "age20_39_ratio": "20~39세 비율", "age65_ratio": "65세 이상 비율",
              "apt_hh_ratio": "아파트 세대 비율", "day_night_pop_ratio": "주야간 인구 비율", "log_household_density": "로그 세대 밀도"}
    for ax, var in zip(axes[:, 0], variables):
        ax.hist(dong[var], bins=25, color="#c7d9df", label="서울 전체 동")
        ax.axvspan(training[var].min(), training[var].max(), color="#176b81", alpha=.2, label="파일럿 관측 범위")
        ax.plot(training[var], np.zeros(len(training)), "|", markersize=14, color="#b25131")
        ax.set(xlabel=labels[var], ylabel="행정동 수")
        ax.legend()
    if not variables:
        axes[0, 0].text(.5, .5, "절편만 선택됨: 변수 기준 외삽 판정 없음", ha="center")
    fig.suptitle(f"선택 변수의 관측 범위 · 외삽 {int(rates.extrapolated.sum())}/{len(rates)}개 동", fontsize=15)
    save(fig, "extrapolation")

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5), layout="constrained")
    for hub, color in [("P1", "#176b81"), ("P2", "#b25131")]:
        sample = validation[validation.hub_id.eq(hub)]
        for ax, column in zip(axes, ["fitted_rate", "nested_loocv_rate"]):
            ax.scatter(sample.base_rate_observed*1000, sample[column]*1000, label=hub, color=color, s=35)
    top = validation[["base_rate_observed", "fitted_rate", "nested_loocv_rate"]].to_numpy().max()*1050
    for ax, title in zip(axes, ["전체 학습 동에 적합", "동 하나씩 제외: 변수 선택도 재실행"]):
        ax.plot([0, top], [0, top], linestyle="--", color="gray")
        ax.set(xlabel="관측 주문율 (1,000세대당 건/일)", ylabel="예측 주문율 (1,000세대당 건/일)", title=title,
               xlim=(0, top), ylim=(0, top))
        ax.legend()
    fig.suptitle("파일럿 동 관측 대 예측 · 세 계수와 달력·램프업 보정 후", fontsize=15)
    save(fig, "pilot_actual_predicted")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    for scenario, color, label in [("low", "#886c46", "시장 low"), ("base", "#176b81", "시장 base"), ("high", "#805a9a", "시장 high")]:
        group = yearly[yearly.scenario.eq(scenario)].sort_values("year")
        axes[0].plot(group.year, group.orders_mid/10000, color=color, label=label)
        axes[0].fill_between(group.year, group.orders_low/10000, group.orders_high/10000, alpha=.09, color=color)
        normalized = group.orders_mid/group.orders_mid.iloc[0]
        axes[1].plot(group.year, normalized, color=color, label=label+" 수요")
        axes[1].plot(group.year, group.market_index, color=color, linestyle="--", alpha=.7, label=label+" 지수")
    axes[0].set(xlabel="연도", ylabel="서울 잠재 주문 (만 건/일)", title="시장 시나리오와 주문율·램프업 범위")
    axes[1].set(xlabel="연도", ylabel="2026년 = 1", title="실선: 수요 / 점선: 시장지수")
    for ax in axes:
        ax.legend(fontsize=8, ncols=2)
        ax.grid(alpha=.15)
    fig.suptitle("20년 전망 · 시장 이외 변화는 세대수·1인가구 비율·경쟁", fontsize=15)
    save(fig, "year_trends")

    base = yearly[yearly.scenario.eq("base")].sort_values("year")
    fig, ax = plt.subplots(figsize=(10, 4.5), layout="constrained")
    ax.plot(base.year, base.orders_without_market/base.orders_without_market.iloc[0], marker="o", label="시장지수를 제거한 잠재 주문")
    ax.plot(base.year, base.households/base.households.iloc[0], label="세대수")
    ax.plot(base.year, base.competition_hh_weighted/base.competition_hh_weighted.iloc[0], label="세대 가중 경쟁 계수")
    ax.set(xlabel="연도", ylabel="2026년 = 1", title="시장지수와 수요 추세가 달라지는 원인")
    ax.legend()
    ax.grid(alpha=.2)
    save(fig, "trend_decomposition")

    geo = load_file("02_dong_boundary.geojson", config=cfg)
    demand = pd.read_csv(output.path / "demand_dong_year.csv", encoding="utf-8-sig", dtype={"adm_cd2": str})
    current = demand[demand.year.eq(cfg["demand"]["base_year"]) & demand.scenario.eq("base")].set_index("adm_cd2")
    fig, ax = plt.subplots(figsize=(10, 8), layout="constrained")
    patches, values = [], []
    for feature in geo["features"]:
        code = feature["properties"]["adm_cd2"]
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            patches.append(Polygon(polygon[0], closed=True))
            values.append(current.loc[code, "orders_mid"])
    collection = PatchCollection(patches, cmap="YlGnBu", norm=Normalize(0, max(values)), edgecolor="white", linewidth=.25)
    collection.set_array(np.array(values))
    ax.add_collection(collection)
    ax.autoscale_view()
    ax.set_aspect(1/np.cos(np.radians(dong.lat.mean())))
    ax.set_axis_off()
    fig.colorbar(collection, ax=ax, shrink=.7, label="잠재 주문 (건/일, 경쟁 반영)")
    ax.set_title("2026년 서울 잠재 수요 · 중앙 주문율\n맑음 기준 / 배송시간·구색 계수 적용 전", fontsize=16)
    fig.text(.02, .01, "경계: 제공 02 GeoJSON · SGIS/vuski admdongkor (CC BY 4.0)", fontsize=8)
    save(fig, "seoul_map")
