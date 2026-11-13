"""네트워크 지도와 계획 비교·가정 설명."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon
import numpy as np
import pandas as pd

from src.common.io import ROOT, load_file
from src.demand.report import md_table
from src.network.model import read_output


def maps(model, alternative, plan, state):
    setting = model.cfg["pilot_diagnosis"]
    font_manager.fontManager.addfont(Path(setting["font_path"]))
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=setting["font_path"]).get_name(),
                         "axes.unicode_minus": False, "font.size": 9})
    out = ROOT / setting["figures_dir"]
    out.mkdir(parents=True, exist_ok=True)
    geo = load_file("02_dong_boundary.geojson", config=model.cfg)
    lookup = {code:i for i,code in enumerate(model.dong.adm_cd2)}
    colors = plt.get_cmap("turbo")(np.linspace(.03,.97,len(model.ids)))
    summary = model.summaries(plan, state).set_index("year")
    for year in model.p["assignment_years"]:
        y = int(year-model.years[0])
        selected = state["assigned"][y]
        fig, ax = plt.subplots(figsize=(11,9), layout="constrained")
        patches, faces = [], []
        for feature in geo["features"]:
            d = lookup[feature["properties"]["adm_cd2"]]
            assigned = selected[d]
            geometry = feature["geometry"]
            polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
            for polygon in polygons:
                patches.append(Polygon(polygon[0],closed=True))
                faces.append(colors[assigned] if assigned >= 0 else (.90,.90,.90,1))
        collection = PatchCollection(patches, facecolors=faces, edgecolor="white", linewidth=.3, alpha=.65)
        ax.add_collection(collection)
        active = [i for i,o,c in plan if o <= year < c]
        for i in active:
            s = model.sites.iloc[i]
            ax.scatter(s.lon, s.lat, s=45, c=[colors[i]], edgecolor="black", linewidth=.7, marker="^", zorder=3)
            ax.annotate(s.site_id, (s.lon,s.lat), xytext=(3,5), textcoords="offset points", fontsize=8,
                        bbox=dict(facecolor="white",alpha=.8,edgecolor="none",pad=.8), zorder=4)
        ax.autoscale_view()
        ax.set_aspect(1/np.cos(np.radians(model.dong.lat.mean())))
        ax.axis("off")
        row = summary.loc[year]
        ax.set_title(f"{alternative}안 · {year}년 · 피크 이동시간 T={model.p['alternatives'][alternative]['T']}분\n"
                     f"운영 {int(row.active_hubs)}곳 / 세대 커버리지 {row.coverage:.1%} / 접수 {row.orders_per_day:,.0f}건/일", fontsize=16)
        fig.text(.02,.015,"색상 = 배정 거점 / 회색 = 미서비스 / 삼각형 = 운영 거점\n경계: 제공 02 GeoJSON · SGIS/vuski admdongkor (CC BY 4.0)",fontsize=9)
        fig.savefig(out / f"network_{alternative}_{year}.png",dpi=150,facecolor="white")
        plt.close(fig)


def write_report(model, comparison, selected, checks):
    out = model.out
    expected = model.p["expected_B"]
    comparisons = []
    key_sites = []
    for alternative, result in selected.items():
        plan = result["plan"]
        lookup = {model.ids[i]:(o,c) for i,o,c in plan}
        for sid in model.p["key_sites"]:
            dates = lookup.get(sid)
            key_sites.append(dict(alternative=alternative,site_id=sid,included_ever=int(dates is not None),
                                  open_year=dates[0] if dates else np.nan, close_year=dates[1] if dates and dates[1] < model.end else np.nan,
                                  included_2027=int(dates is not None and dates[0] <= 2027 < dates[1])))
    pd.DataFrame(key_sites).to_csv(out / "network_key_sites.csv",index=False,encoding="utf-8-sig")
    if "B" in selected:
        b = comparison[(comparison.alternative == "B") & comparison.selected.eq(1) & comparison.scenario.eq("base")].iloc[0]
        tol = model.p["expectation_relative_tolerance"]
        for metric, actual, target in [("hubs_2027",b.hubs_2027,expected["hubs_2027"]),
                                       ("coverage_2027",b.coverage_2027,expected["coverage_2027"]),
                                       ("orders_2027",b.orders_2027,expected["orders_2027"])]:
            comparisons.append(dict(metric=metric,actual=actual,expected_low=target*(1-tol),expected_high=target*(1+tol),
                                    difference=actual-target,in_range=int(abs(actual-target)<=target*tol)))
        comparisons.append(dict(metric="npv_eok",actual=b.npv_eok,expected_low=expected["npv_eok_low"],expected_high=expected["npv_eok_high"],
                                difference=b.npv_eok,in_range=int(expected["npv_eok_low"]<=b.npv_eok<=expected["npv_eok_high"])))
    expectation = pd.DataFrame(comparisons)
    expectation.to_csv(out / "network_expectation_check.csv",index=False,encoding="utf-8-sig")

    # 같은 운영 가정 아래 출발점과 탐색 결과를 재평가하여 계획 변경 효과를 분리한다.
    decomposition = []
    if "B" in selected:
        result = selected["B"]
        for label, plan in [("selected_search_start",result["initial"]),("selected_final",result["plan"])]:
            k, annual, _, state = model.evaluate(plan,model.p["alternatives"]["B"]["T"],details=True)
            year = model.summaries(plan,state).set_index("year")
            discount = (1+model.engine.rate("discount_rate"))**np.arange(len(annual))
            decomposition.append(dict(plan=label,npv_eok=k["npv_eok"],funding_eok=k["max_funding_eok"],
                                      hubs_2027=year.loc[2027,"active_hubs"],coverage_2027=year.loc[2027,"coverage"],
                                      orders_2027=year.loc[2027,"orders_per_day"],orders_2046=year.loc[2046,"orders_per_day"],
                                      requested_2046=year.loc[2046,"requested_per_day"],
                                      revenue_pv_eok=(annual.revenue/discount).sum()/model.engine.unit,
                                      rider_pv_eok=((annual.contract_rider_cost+annual.external_quick)/discount).sum()/model.engine.unit,
                                      picker_pv_eok=(annual.picker_cost/discount).sum()/model.engine.unit,
                                      hq_pv_eok=(annual.hq_cost/discount).sum()/model.engine.unit,
                                      capex_pv_eok=(annual.capex/discount).sum()/model.engine.unit))
    pd.DataFrame(decomposition).to_csv(out / "network_B_decomposition.csv",index=False,encoding="utf-8-sig")

    central = comparison[comparison.scenario.eq("base")][["alternative","search","selected","pilot_branch","npv_eok","max_funding_eok","hubs_2027","coverage_2027","orders_2027"]]
    uncertainty = comparison[comparison.selected.eq(1)][["alternative","scenario","npv_low_eok","npv_eok","npv_high_eok","max_funding_low_eok","max_funding_eok","max_funding_high_eok"]]
    inventory = read_output(out / "network_pilot_inventory.csv")
    seed_audit = read_output(out / "network_seed_audit.csv")
    text = f"""# ③ 네트워크 설계 1차

## 결과와 두 탐색 비교

`python -m src.network.run --config config.json`으로 실행한다. 시장 base·orders_mid로 탐색하며, A/B/C/B2는 같은 코드의 설정값으로 생성한다.
선택은 500억 원 이내 결과 중 NPV가 높은 경로를 기준으로 한다. 시나리오 및 주문율 범위는 확정 계획에 적용한다.
표의 금액은 억 원, 주문은 접수 제한 이후 건/일, 세대 커버리지는 해당 연도 추계 기준 비율이다.
출처는 `alt_compare.csv`다.

{md_table(central)}

`greedy`는 파일럿 유지/철수 조합에서 구간별 최대 NPV 증가 후보를 추가한 뒤 제거·교체를 한 바퀴 평가한다.
`seed`는 사전 후보 목록에서 시작해 구간별 제거·교체를 한 바퀴 돌고 NPV가 개선되는 동안 추가한다.
각 경로는 P1/P2의 유지·철수 4조합을 평가한다. `pilot_branch`는 P1/P2 순서의 유지 여부다.
기록은 `network_search_branches.csv`, 채택한 개별 변경은 `network_search_trace.csv`, 구간별 집계는 `network_search_phases.csv`에 있다.
모든 변경은 재배정 후 전체 NPV로 비교한다. 폐점 시점 이전의 운영 이력은 보존한다.
자금 한도에 진입하기 전에는 NPV 증가와 최대 자금 감소를 함께 만족해야 한다. 진입 후에는 한도 내 변경만 허용한다.
순차 탐색의 결과이며 전역 최적성을 보장하지 않는다.

## 사전 후보 목록과 제약

{md_table(seed_audit)}

C056은 정제 단계에서 제외됐으므로 출발점에서 제외했다. 이를 임의의 다른 후보지로 대체하지 않았다.
B2의 상위 8곳은 유지 파일럿을 포함한 2027년 운영 거점 수다. seed 경로의 2027년 후보는 순차 한계 NPV로 순위를 정하고 나머지는 2032년에 배치해 탐색을 시작한다.
이미 운영한 장소의 재개점은 재무 엔진 계약에 따라 지원하지 않는다. 교체는 구간 시작 시 기존 거점 폐점과 신규 후보지 개점으로 평가한다.

## 수요·운영·비용의 1차 가정

- 13의 피크 이동시간이 최소인 운영 거점에 동을 배정하고 T 이내인 동을 서비스한다. 동률은 site_id순이다.
- 동별 예상 평균 배송시간은 23 + 0.6×평시 이동시간 + 0.4×피크 이동시간이다. 구간 상한과 같은 값은 아래 구간에 포함한다.
- 동 잠재 수요에 배송시간 계수, 구색, 연평균 램프업, 지연 수요 계수를 곱한다. 시장과 경쟁은 ②에서 이미 반영됐다.
- 램프업은 ②의 a={model.a:.8f}, τ={model.tau:g}일을 사용해 달력의 매일 수준을 평균한다. 파일럿 실제 개점일을 이어 계산한다. 재무의 연간 주문 환산은 기존 엔진의 365일이다.
- 거점별 접수는 유형 상한까지이며 초과분은 각 배정 동 수요에 비례해 제한한다. `assignment`의 주문은 접수 이후 값이다. 접수 제한을 받더라도 거리상 서비스 권역에 속하면 세대 커버리지에 포함한다.
- 위탁 단가는 지정한 피크 구간별 2027년 원/건을 주문 가중평균한다. 외부 퀵 비중에는 외부 퀵 비용, 내부 주문 비중에는 위탁 비용을 적용한다. 피커 단가는 전체 주문당 {model.p['picker_cost_2027']:,}원이다.
- 평균 장바구니는 관측 {model.profile.avg_basket_krw:,.2f}원(2026년 가격)을 {model.basket_2027:,.2f}원(2027년 가격)으로 환산한다. 비중은 ALL/ALL 관측값으로 고정한다. 배달비 기준 변화에 따른 비중 변화는 1차에서 미추정이다.
- 위탁 라이더 구간 단가·유형별 하루 상한·23분 고정 처리시간·지연율 0은 사용자가 지정한 ⑤ 이전 가정이다. 14번 공급표는 로드·기록하며 시간대별 인력 수급은 ⑤에서 검증한다.

## 파일럿 기초 재고

2026년 마지막 관측 주의 실제 일평균 주문 × 전체 평균 장바구니 × 매출원가율 × 재고일수로 추정한다.
같은 값을 2027년 초 철수 시 80% 회수와 계속 운영 시 기초 재고에 사용한다.
실제 관측 마지막 주는 아래 표에 명시했다. 10월 관측을 2026년 말 잔액의 대용치로 사용하는 가정이다.
`network_effective_config.json`의 finance.pilot_initial_inventory_krw에 평가에 사용한 잔액을 보존한다.

{md_table(inventory)}

## 불확실성

개폐점 계획을 고정하고 시장 low/base/high마다 orders_low/mid/high를 적용한다. 주문 범위에 따라 접수 제한과 운영비·재고·법인세가 다시 계산된다.
표의 세 NPV와 세 자금 소요는 주문 하한·중앙·상한에 각각 대응한다. 자금 소요는 낮은 수요에서 더 커질 수 있다.
최종안의 500억 제약은 최적화 기준 base·mid에서 검사한다. 다른 조합의 초과 여부는 alt_compare의 funding_within_limit 열에 표시한다.

{md_table(uncertainty)}

## B 기획 추정과 비교

점 추정의 진단 범위는 ±{model.p['expectation_relative_tolerance']:.0%}다. 결과를 기대값에 맞추는 조정은 수행하지 않았다.

{md_table(expectation)}

같은 운영 가정으로 출발점과 선택 계획을 비교한 결과는 다음과 같다. 매출·라이더·피커·본사비·투자의 할인 현재가치를 함께 공개한다.
후보지 구성·개폐점 시점·접수 제한의 변화는 이 표와 연도별 표에서 확인할 수 있다.
기획 추정의 상세 손익·가정별 산식은 제공되지 않아 차이 전체를 특정 가정 하나의 효과로 단정할 수 없다.
현재 결과는 23분 처리시간, 지정한 라이더 단가, 유형별 일일 상한, 지연율 0에 조건부이며 이 값들은 ⑤에서 교체한다.

{md_table(pd.DataFrame(decomposition))}

핵심 후보지 포함 여부는 `network_key_sites.csv`에 개점·폐점 연도와 2027년 포함 여부로 기록했다.

{md_table(pd.DataFrame(key_sites))}

## 출력과 검산

선택 계획 network_plan_<대안>.csv와 5개 연도의 assignment_<대안>.csv를 후속 단계에 넘긴다.
network_hub_demand_<대안>.csv에는 거점별 제한 전/후 수요, 상한 대비 비율, 최대 피크시간, 램프업 수준을 저장한다.
network_year_<대안>_<시장>.csv에는 제한 주문·상품 매출 손실·배달 매출 손실을 연도별로 저장한다.
누적 손실 매출은 각 연도 명목금액의 합이며, 미서비스 동의 잠재 수요는 접수 제한 손실에 합산하지 않는다.
금액은 원 단위로 계산하고 비교표에서는 억 원을 병기한다.

{md_table(pd.DataFrame(checks))}
"""
    (out / "network_validation.md").write_text(text,encoding="utf-8")
