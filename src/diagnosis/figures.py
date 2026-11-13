"""파일럿 결과를 보고서용 PNG로 그린다."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import PercentFormatter
import numpy as np

from src.common.io import ROOT


HUB_COLORS = {"P1": "#176b81", "P2": "#b25131"}
WEATHER_COLORS = {"clear": "#c19639", "rain": "#3981a9", "heavy_rain": "#4e3f88"}
COMPONENTS = {"pick_wait": "피킹 대기", "pick": "피킹", "depart_wait": "출발 대기", "depart_to_delivery": "출발~전달"}


def plot_all(cfg, result, daily, riders, hourly, distance):
    p = cfg["pilot_diagnosis"]
    out = ROOT / p["figures_dir"]
    out.mkdir(parents=True, exist_ok=True)
    font_path = Path(p["font_path"])
    font_manager.fontManager.addfont(font_path)
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=font_path).get_name(),
                         "axes.unicode_minus": False, "axes.spines.top": False,
                         "axes.spines.right": False, "font.size": 10})
    lookup = result.set_index(["hub_id", "metric", "segment"]).value
    hubs = list(daily)
    weather = p["weather_labels"]
    sla = p["sla_minutes"]
    peak_start, peak_end = p["peak_hours"]

    def values(hub, metric, segments):
        return np.array([lookup.loc[(hub, metric, s)] for s in segments])

    def save(fig, name, title):
        fig.suptitle(title, x=.03, ha="left", fontsize=17, fontweight="bold")
        fig.savefig(out / f"pilot_{name}.png", dpi=160, facecolor="white")
        plt.close(fig)

    def grid(ax):
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)

    fig, axes = plt.subplots(2, 1, figsize=(10.6, 6.5), layout="constrained", sharex=True)
    for ax, hub in zip(axes, hubs):
        d = daily[hub]
        promoted = d[d.promotion.notna()].elapsed
        for i, day in enumerate(promoted):
            ax.axvspan(day-.5, day+.5, color="#ebebd8", label="프로모션" if i == 0 else None)
        ax.plot(d.elapsed, d.orders, color="#a9b3ba", linewidth=1, zorder=1)
        for key, label in weather.items():
            for weekend, marker in [(False, "o"), (True, "s")]:
                mask = d.weather.eq(key) & d.weekday.isin(["토", "일"]).eq(weekend)
                ax.scatter(d.elapsed[mask], d.orders[mask], s=19, marker=marker, color=WEATHER_COLORS[key],
                           label=label if not weekend else None, zorder=2)
        ax.set(title=hub, ylabel="주문 수 (건)")
        ax.legend(loc="upper left", frameon=False, ncols=4)
        grid(ax)
    axes[-1].set_xlabel("개점 후 경과일 (개점일 = 0일, 원 = 평일, 사각형 = 주말)")
    save(fig, "daily_orders", "개점 후 일별 주문")

    fig, axes = plt.subplots(2, 2, figsize=(11, 6.8), layout="constrained")
    segments = ["all", *[f"weather={w}" for w in weather]]
    labels = ["전체", *weather.values()]
    x = np.arange(len(labels))
    for row, hub in enumerate(hubs):
        ax, rate_ax = axes[row]
        bottom = np.zeros(len(x))
        for (metric, label), color in zip(COMPONENTS.items(), ["#b6cbd8", "#4e90a4", "#dca65d", "#475c86"]):
            duration = values(hub, f"avg_{metric}_min", segments)
            ax.bar(x, duration, bottom=bottom, label=label, color=color, width=.55)
            bottom += duration
        ax.scatter(x, values(hub, "p90_delivery_min", segments), label="전체 배송시간 90분위", color="#842e3c", marker="D", s=27)
        ax.set(title=f"{hub} · 배송시간", ylabel="분", xticks=x, xticklabels=labels)
        ax.legend(ncols=2, frameon=False, fontsize=8)
        ax.set_ylim(0, max(42, np.nanmax(values(hub, "p90_delivery_min", segments))*1.34))
        late = values(hub, "late_rate", segments)
        rate_ax.bar(x, late, width=.55, color=HUB_COLORS[hub], alpha=.85)
        for i, v in enumerate(late):
            if np.isfinite(v):
                rate_ax.annotate(f"{v:.2%}", (i, v), xytext=(0, 4), textcoords="offset points", ha="center", fontsize=9)
        rate_ax.set(title=f"{hub} · {sla}분 초과율", xticks=x, xticklabels=labels, ylim=(0, max(.004, np.nanmax(late)*1.28)))
        rate_ax.yaxis.set_major_formatter(PercentFormatter(1))
        grid(ax)
        grid(rate_ax)
    save(fig, "delivery", "날씨별 배송시간과 지연율")

    fig, axes = plt.subplots(2, 2, figsize=(11, 6.4), layout="constrained")
    for i, hub in enumerate(hubs):
        ax, trip_ax = axes[i]
        table = hourly[hub]
        operational = table.loc[table.index >= cfg["cleaning"]["service_start_min"] // 60]
        ax.bar(operational.index, operational.orders_per_day, color=HUB_COLORS[hub], width=.68)
        ax.axvspan(peak_start-.5, peak_end-.5, color="#ded3bf", alpha=.5)
        ax.set(title=f"{hub} · 접수 기준", ylabel="영업일당 주문 (건)", xlabel="접수 시각 (시)",
               xticks=operational.index)
        trip_ax.axvspan(peak_start-.5, peak_end-.5, color="#ded3bf", alpha=.5)
        departure = table.orders_per_trip.reindex(range(24))
        trip_ax.plot(departure.index, departure, "o-", color=HUB_COLORS[hub], markersize=4)
        trip_ax.axhline(1, color="#818c94", linewidth=1, linestyle="--")
        trip_ax.set(title=f"{hub} · 출발 기준", ylabel="트립당 주문 (건)", xlabel="출발 시각 (시)",
                    xticks=range(0, 24, 2), ylim=(.95, None))
        grid(ax)
        grid(trip_ax)
    save(fig, "hourly_batching", "시간대별 주문과 묶음배송")

    fig, ax = plt.subplots(figsize=(8, 4.2), layout="constrained")
    size_segments = list(result.loc[result.metric.eq("trip_share"), "segment"].unique())
    x = np.arange(len(size_segments))
    for i, hub in enumerate(hubs):
        shares = values(hub, "trip_share", size_segments)
        pos = x + (i-.5)*.31
        ax.bar(pos, shares, width=.28, label=hub, color=HUB_COLORS[hub])
        for a, b in zip(pos, shares):
            ax.annotate(f"{b:.1%}", (a, b), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=9)
    ax.set(xticks=x, xticklabels=[s.split("=")[1] for s in size_segments], xlabel="트립에 포함된 주문 수 (건)",
           ylabel="트립 비중", ylim=(0, 1.13))
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(frameon=False)
    grid(ax)
    save(fig, "trip_distribution", "트립당 주문 수 분포")

    fig, axes = plt.subplots(2, 1, figsize=(10.6, 6), layout="constrained", sharex=True, sharey=True)
    threshold = p["contract_min_earnings_krw_per_hour"]
    for ax, hub in zip(axes, hubs):
        d = riders[hub]
        ax.plot(d.index, d.earnings, color=HUB_COLORS[hub], linewidth=1.4, label="지급액 합 ÷ 접속시간 합")
        ax.axhline(threshold, color="#8d453f", linestyle="--", label=f"기준 {threshold:,}원/시간")
        ax.set(title=f"{hub} · 기준 미달 {d.below.sum()}/{len(d)}일", ylabel="원/시간", ylim=(0, None))
        ax.legend(frameon=False, loc="upper left", ncols=2)
        grid(ax)
    axes[-1].set_xlabel("영업일")
    save(fig, "rider_earnings", "거점·일별 위탁 라이더 시간당 수입")

    fig, ax = plt.subplots(figsize=(8.6, 4.3), layout="constrained")
    for hub in hubs:
        d = distance[hub]
        ax.plot(list(d.index), d.rate_daily, "o-", color=HUB_COLORS[hub], label=f"{hub} · 반경 {p['service_radius_km'][hub]:g}km")
    ax.set(xlabel="도로거리 구간 (km, 거점별 서비스 반경 안)", ylabel="세대당 하루 주문 (건)", ylim=(0, None))
    ax.legend(frameon=False)
    grid(ax)
    save(fig, "distance_demand", "도로거리별 세대당 주문")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.7), layout="constrained", gridspec_kw={"width_ratios": [1, 2]})
    order_hours = [int(s.split("=")[1]) for h, m, s in lookup.index
                   if h == "ALL" and m == "late_orders" and s.startswith("order_hour=")]
    for ax, segment, labels in [
        (axes[0], segments[1:], list(weather.values())),
        (axes[1], [f"order_hour={hour:02d}" for hour in order_hours], order_hours),
    ]:
        x = np.arange(len(labels))
        ax.bar(x-.17, values("ALL", "demand_order_share", segment), width=.34, label="수요 주문 비중", color="#85aab6")
        ax.bar(x+.17, values("ALL", "late_order_share", segment), width=.34, label=f"{sla}분 초과 주문 비중", color="#ad4d36")
        ax.set(xticks=x, xticklabels=labels)
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.legend(frameon=False, fontsize=9)
        grid(ax)
    axes[0].set(xlabel="날씨", ylabel="각 집단 내 비중")
    axes[1].set(xlabel="주문 접수 시각 (시)")
    save(fig, "late_concentration", f"{sla}분 초과 주문이 몰린 날씨와 시간대")
