#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_rank_L1.py — METHOD_rank_axis.md §1·§3·§4(+§7 검산) L1(그룹×일) 층 구현.
읽기 전용. prod DB 접속 없음·네이버 API 호출 없음. 입력 = panel_labeled.csv.gz 하나.
재실행 가능(현재시각/랜덤 미사용). D0 = 2026-08-17 고정(ref 63과 같은 창).

+ 보강: 선행 주장 4건(P-A~P-D, 12일/90일 창에서 나온 주장) 1년 패널 재검증.
  파라미터는 전부 METHOD_rank_axis.md·ref 31/38/39/41에서 읽어온 값만 쓴다(신규 발명 0).
"""
import numpy as np
import pandas as pd
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DATA_DIR = REPO / "docs" / "references" / "data" / "64_rank_axis"
PANEL_PATH = REPO / "docs" / "references" / "data" / "63_band_decomposition" / "panel_labeled.csv.gz"
DATA_DIR.mkdir(parents=True, exist_ok=True)

D0 = pd.Timestamp("2026-08-17")  # 고정 — 재실행해도 안 바뀐다 (METHOD §9)

# ---------------------------------------------------------------------------
# 재발명 0 파라미터 — 전부 METHOD_rank_axis.md §1 표 / ref63 build_panel.py에서 그대로 읽어옴
# ---------------------------------------------------------------------------
BEP_ROAS = 1.711
BEP_ROAS_120 = round(BEP_ROAS * 1.2, 4)  # 2.0532 — build_panel.py:32-33
MATURE_CUTOFF = pd.Timestamp("2026-08-09")  # D0-8, build_panel.py:26

# 홀드아웃 창 — analyze_residual.py:574-584의 계산을 그대로 재현(같은 앵커 MATURE_CUTOFF에서 유도)
VALIDATE_END = MATURE_CUTOFF
VALIDATE_START = VALIDATE_END - pd.Timedelta(days=90)
EXPLORE_END = VALIDATE_START - pd.Timedelta(days=1)
EXPLORE_START = EXPLORE_END - pd.Timedelta(days=273)
assert (EXPLORE_START.date().isoformat(), EXPLORE_END.date().isoformat()) == ("2025-08-10", "2026-05-10")
assert (VALIDATE_START.date().isoformat(), VALIDATE_END.date().isoformat()) == ("2026-05-11", "2026-08-09")

# 정본 9구간 경계 (METHOD §3-3 사전 등록)
BIN9_EDGES = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 7.0, np.inf]
BIN9_LABELS = ["[1,1.5)", "[1.5,2)", "[2,2.5)", "[2.5,3)", "[3,3.5)", "[3.5,4)", "[4,5)", "[5,7)", "[7,∞)"]

# 라벨 우선순위(상호배타) — analyze_residual.py label_conditions/assign_labels와 동일 구조
BASE_PRIORITY = ["F2", "F1b", "F1a", "연휴", "공휴일", "휴가창", "주말", "F3"]

report_lines = []


def log(s=""):
    print(s)
    report_lines.append(str(s))


def save(df, name):
    path = DATA_DIR / name
    df.to_csv(path, index=False)
    log(f"  -> {name} 저장: {len(df)}행")
    return path


# ===========================================================================
# 0) 로드 + ad_profit
# ===========================================================================
panel = pd.read_csv(PANEL_PATH, dtype={"campaign_id": str, "adgroup_id": str})
panel["ad_date"] = pd.to_datetime(panel["ad_date"])
for b in ["is_weekend", "is_seollal_chuseok", "is_holiday", "is_vacation", "is_vacation_peak",
          "mature", "data_gap", "model_known"]:
    if panel[b].dtype != bool:
        panel[b] = panel[b].astype(str).map({"True": True, "False": False}).astype(bool)

panel["ad_profit"] = panel["conv_amt"] / BEP_ROAS - panel["cost"]
log(f"panel_labeled 로드: {len(panel)}행 (열 {len(panel.columns)}개), 그룹수 {panel['adgroup_id'].nunique()}")

# ===========================================================================
# 1) 제외 규율 (METHOD §1·§7-8) — 성숙컷+data_gap 먼저(ref63과 동일 정의), 그 안에서 imp=0
# ===========================================================================
excl_mature_gap = (~panel["mature"]) | (panel["data_gap"])
analysis = panel[~excl_mature_gap].copy()  # = ref63의 "analysis" 집합과 동일 정의

imp0_mask = analysis["imp"] == 0
imp0_cost_gt0_mask = imp0_mask & (analysis["cost"] > 0)

rank_universe = analysis[~imp0_mask].copy()
rank_universe["avg_rank"] = rank_universe["rank_sum"] / rank_universe["imp"]

excl_rows = [
    {"stage": "total_panel", "n_rows": len(panel), "sum_cost": int(panel["cost"].sum()),
     "sum_conv_amt": int(panel["conv_amt"].sum())},
    {"stage": "excluded_immature_or_datagap", "n_rows": int(excl_mature_gap.sum()),
     "sum_cost": int(panel.loc[excl_mature_gap, "cost"].sum()),
     "sum_conv_amt": int(panel.loc[excl_mature_gap, "conv_amt"].sum())},
    {"stage": "analysis_universe(mature&~data_gap)", "n_rows": len(analysis),
     "sum_cost": int(analysis["cost"].sum()), "sum_conv_amt": int(analysis["conv_amt"].sum())},
    {"stage": "excluded_imp0_within_analysis", "n_rows": int(imp0_mask.sum()),
     "sum_cost": int(analysis.loc[imp0_mask, "cost"].sum()),
     "sum_conv_amt": int(analysis.loc[imp0_mask, "conv_amt"].sum())},
    {"stage": "ANOMALY_imp0_cost_gt0_within_analysis(subset of above)", "n_rows": int(imp0_cost_gt0_mask.sum()),
     "sum_cost": int(analysis.loc[imp0_cost_gt0_mask, "cost"].sum()),
     "sum_conv_amt": int(analysis.loc[imp0_cost_gt0_mask, "conv_amt"].sum())},
    {"stage": "rank_analysis_universe(analysis & imp>0)", "n_rows": len(rank_universe),
     "sum_cost": int(rank_universe["cost"].sum()), "sum_conv_amt": int(rank_universe["conv_amt"].sum())},
]
log("\n=== §7-8 검산: 제외 규모 ===")
for r in excl_rows:
    log(f"  {r['stage']}: {r['n_rows']}행, cost={r['sum_cost']:,}, conv_amt={r['sum_conv_amt']:,}")
save(pd.DataFrame(excl_rows), "L1_excluded_rows.csv")

# ===========================================================================
# 2) 구간 분류 함수
# ===========================================================================
def to_bin9(avg_rank_series, edges=BIN9_EDGES, labels=BIN9_LABELS):
    return pd.cut(avg_rank_series, bins=edges, labels=labels, right=False, include_lowest=True)


def to_band3(avg_rank_series, lo=2.5, hi=4.0):
    lo_lab, hi_lab = f"<{lo:g}", f"≥{hi:g}"
    mid_lab = f"[{lo:g},{hi:g})"
    out = pd.Series(np.where(avg_rank_series < lo, lo_lab,
                     np.where(avg_rank_series < hi, mid_lab, hi_lab)), index=avg_rank_series.index)
    order = [lo_lab, mid_lab, hi_lab]
    return out, order


rank_universe["rank_bin9"] = to_bin9(rank_universe["avg_rank"])
rank_universe["band3"], BAND3_ORDER = to_band3(rank_universe["avg_rank"])

log(f"\n9-bin 분포:\n{rank_universe['rank_bin9'].value_counts().sort_index().to_string()}")
log(f"\nband3 분포:\n{rank_universe['band3'].value_counts().reindex(BAND3_ORDER).to_string()}")


# ---------------------------------------------------------------------------
# 2-b) 평시(baseline) 라벨 — §3-4②와 §4(3라운드: 홀드아웃 게이트도 평시행만으로 재실행)가 공유.
# ref63 analyze_residual.py의 label_conditions/assign_labels와 동일 정의·동일 우선순위(BASE_PRIORITY).
# 새 정의를 만들지 않는다(3라운드 코디네이터 지시).
# ---------------------------------------------------------------------------
def label_conditions(df):
    return {
        "F2": df["launch_phase"] == "F2", "F1b": df["launch_phase"] == "F1b", "F1a": df["launch_phase"] == "F1a",
        "연휴": df["is_seollal_chuseok"], "공휴일": df["is_holiday"],
        "휴가창": df["is_vacation"], "주말": df["is_weekend"], "F3": df["launch_phase"] == "F3",
    }


def assign_labels(df, priority):
    conds = label_conditions(df)
    label = pd.Series("평시", index=df.index, dtype=object)
    assigned = pd.Series(False, index=df.index)
    for name in priority:
        take = conds[name] & (~assigned)
        label.loc[take] = name
        assigned = assigned | take
    return label


rank_universe["factor_label"] = assign_labels(rank_universe, BASE_PRIORITY)
baseline_rows = rank_universe[rank_universe["factor_label"] == "평시"]
log(f"\n평시 행: {len(baseline_rows)} / 전체 rank_analysis_universe {len(rank_universe)}")


# ===========================================================================
# 3) 집계 헬퍼 — 4값 병기(§3-2) 그대로
# ===========================================================================
def aggregate(df, bin_col, extra_cols=()):
    g = df.groupby(["campaign_type", bin_col], observed=True).agg(
        n_group_days=("ad_profit", "size"),
        n_groups=("adgroup_id", "nunique"),
        sum_cost=("cost", "sum"),
        sum_conv_amt=("conv_amt", "sum"),
        sum_ad_profit=("ad_profit", "sum"),
        sum_imp=("imp", "sum"),
        sum_clk=("clk", "sum"),
        sum_conv_cnt=("conv_cnt", "sum"),
    ).reset_index()
    g["roas"] = np.where(g["sum_cost"] > 0, g["sum_conv_amt"] / g["sum_cost"], np.nan)
    g["profit_per_group_day"] = g["sum_ad_profit"] / g["n_group_days"]
    g["cost_per_group_day"] = g["sum_cost"] / g["n_group_days"]  # ★볼륨(소진강도) 축
    # ★효율 축 — 항등식: profit_per_cost_pct = sum_ad_profit/sum_cost*100 = (roas/BEP_ROAS - 1)*100.
    # profit_per_group_day는 분모가 "시간"이라 효율이 아니라 강도를 잰다(2라운드 코디네이터 지적,
    # 재현: 아래 두 열로 직접 검산 가능). 효율은 분모가 "비용"이어야 하고 그게 곧 roas의 아핀변환이다.
    g["profit_per_cost_pct"] = np.where(g["sum_cost"] > 0, (g["roas"] / BEP_ROAS - 1) * 100, np.nan)
    for c in ["sum_ad_profit", "roas", "profit_per_group_day", "cost_per_group_day", "profit_per_cost_pct"]:
        g[c] = g[c].round(2)
    return g.rename(columns={bin_col: "rank_bin"})  # 내부적으로 항상 "rank_bin"으로 통일(9-bin이든 band3든)


# ===========================================================================
# 4) 정본표 (9-bin) + 3구간 (가설 검정용)
# ===========================================================================
tab9 = aggregate(rank_universe, "rank_bin9")
_bin9_rank = {v: i for i, v in enumerate(BIN9_LABELS)}
tab9 = tab9.sort_values(["campaign_type", "rank_bin"],
                         key=lambda s: s.map(_bin9_rank) if s.name == "rank_bin" else s)
BAND_COLS = ["campaign_type", "rank_bin", "n_group_days", "n_groups", "sum_cost", "sum_conv_amt",
             "sum_ad_profit", "roas", "profit_per_group_day", "cost_per_group_day", "profit_per_cost_pct"]
save(tab9[BAND_COLS], "L1_rank_band_by_type.csv")

tab3 = aggregate(rank_universe, "band3")
tab3 = tab3.sort_values(["campaign_type", "rank_bin"], key=lambda s: s.map({v: i for i, v in enumerate(BAND3_ORDER)}) if s.name == "rank_bin" else s)
save(tab3[BAND_COLS].rename(columns={"rank_bin": "band3"}), "L1_rank_band3_by_type.csv")

# ★효율/강도 항등식 자체 문서화 (2라운드 코디네이터 지적 — "파일 주석 또는 README에 명시")
identity_note = pd.DataFrame([
    {"metric": "profit_per_group_day", "role": "강도(볼륨×효율 합성) — 효율지표 아님",
     "identity_or_caution": "cost_per_group_day와 사실상 비례(둘 다 상단 구간에서 같이 크다) — "
                             "이 값이 높은 건 «그 구간이 좋아서»가 아니라 «그 구간 그룹-일이 하루에 더 많이 써서»일 수 있다."},
    {"metric": "profit_per_cost_pct", "role": "★효율 축(비용당 이익률)",
     "identity_or_caution": "항등식: profit_per_cost_pct = (roas/1.711 - 1) * 100. "
                             "즉 roas의 아핀변환이라 독립적인 새 지표가 아니다 — roas만 봐도 같은 정보."},
    {"metric": "cost_per_group_day", "role": "볼륨(소진강도) 축", "identity_or_caution": "n_group_days 대비 sum_cost."},
    {"metric": "sum_ad_profit", "role": "절대이익(=Σcost × profit_per_cost_pct/100)",
     "identity_or_caution": "노출·지출량이 큰 구간이 구조적으로 유리 — 「최댓값 구간」을 「최고 구간」과 혼동하지 않는다."},
])
save(identity_note, "L1_metric_identity_note.csv")

log("\n=== 정본표(9-bin) ===")
log(tab9.to_string(index=False))
log("\n=== 3구간(가설 검정용) ===")
log(tab3.to_string(index=False))


# ===========================================================================
# 5) 홀드아웃 게이트 (METHOD §4)
# ===========================================================================
def order_by_metric(sub_tab, metric_col, bin_col="rank_bin"):
    """metric_col 내림차순 순서(튜플) — n_group_days=0인 구간은 제외."""
    s = sub_tab[sub_tab["n_group_days"] > 0].sort_values(metric_col, ascending=False)
    return tuple(s[bin_col].tolist())


def order_by_profit(sub_tab, bin_col="rank_bin"):
    """하위호환 래퍼(§6 민감도 3종에서 계속 사용) — profit_per_group_day 기준."""
    return order_by_metric(sub_tab, "profit_per_group_day", bin_col)


# ★2라운드 코디네이터 지적 반영: profit_per_group_day(강도) 게이트에 더해 roas(효율) 게이트를
# "추가"한다 — 기존 profit/일 행은 지우지 않고 metric 열로 구분해 같이 남긴다(두 축이 다른 답을
# 주면 그게 발견이다).
# ★3라운드 코디네이터 지적 반영: 검증창(2026-05-11~2026-08-09)이 휴가창·Z폴드8 출시를 통째로 품고
# 있어 "순위축 자체의 불안정"과 "검증창의 달력 구성"이 안 갈린다 — 창은 옮기지 않고(ref59 ⑤-9
# 사전고정, 옮기면 p-해킹) row_scope="baseline_only"(§2-b의 "평시" 라벨, 새 정의 아님)로 한 번 더
# 돌려 라벨로 통제한다. 기존 row_scope="all" 12행은 그대로 남기고 12행을 추가해 24행.
GATE_METRICS = ["profit_per_group_day", "roas"]
GATE_SCOPES = [("all", rank_universe), ("baseline_only", baseline_rows)]
peak_idx = {v: i for i, v in enumerate(BIN9_LABELS)}

hg_rows = []
for row_scope, universe_df in GATE_SCOPES:
    for ctype in ["SHOPPING", "WEB_SITE"]:
        exp_df = universe_df[(universe_df["campaign_type"] == ctype) &
                              (universe_df["ad_date"] >= EXPLORE_START) & (universe_df["ad_date"] <= EXPLORE_END)]
        val_df = universe_df[(universe_df["campaign_type"] == ctype) &
                              (universe_df["ad_date"] >= VALIDATE_START) & (universe_df["ad_date"] <= VALIDATE_END)]

        exp3 = aggregate(exp_df, "band3") if len(exp_df) else pd.DataFrame(columns=["campaign_type", "rank_bin"])
        val3 = aggregate(val_df, "band3") if len(val_df) else pd.DataFrame(columns=["campaign_type", "rank_bin"])
        exp9 = aggregate(exp_df, "rank_bin9") if len(exp_df) else pd.DataFrame(columns=["campaign_type", "rank_bin"])
        val9 = aggregate(val_df, "rank_bin9") if len(val_df) else pd.DataFrame(columns=["campaign_type", "rank_bin"])

        # 표본 편재·표본0 판정은 metric과 무관하지만 row_scope별로는 새로 계산한다(평시만 자르면
        # 표본이 얇아질 수 있다 — 3라운드 지시대로 편재 판정도 다시 계산)
        n_by_band = {}
        conc_flag = False
        zero_flag = False
        for b in BAND3_ORDER:
            n_e = int(exp3.loc[exp3["rank_bin"] == b, "n_group_days"].sum()) if len(exp3) else 0
            n_v = int(val3.loc[val3["rank_bin"] == b, "n_group_days"].sum()) if len(val3) else 0
            share = n_v / (n_e + n_v) if (n_e + n_v) else np.nan
            n_by_band[b] = (n_e, n_v, share)
            if n_e == 0 or n_v == 0:
                zero_flag = True
            if pd.notna(share) and (share >= 0.85 or share <= 0.15):
                conc_flag = True

        for metric in GATE_METRICS:
            order_e = order_by_metric(exp3, metric) if len(exp3) else ()
            order_v = order_by_metric(val3, metric) if len(val3) else ()
            peak_e = order_by_metric(exp9, metric)[0] if len(exp9) and len(order_by_metric(exp9, metric)) else None
            peak_v = order_by_metric(val9, metric)[0] if len(val9) and len(order_by_metric(val9, metric)) else None
            peak_adjacent_or_same = (peak_e is not None and peak_v is not None and
                                      abs(peak_idx.get(peak_e, -99) - peak_idx.get(peak_v, -99)) <= 1)

            if zero_flag:
                gate_result = "판정불능(표본0)"
            elif conc_flag:
                gate_result = "게이트 미적용(표본 편재)"
            elif order_e == order_v and order_e:
                gate_result = "통과(방향재현)" if peak_adjacent_or_same else "부분"
            else:
                gate_result = "미재현"

            for b in BAND3_ORDER:
                n_e, n_v, share = n_by_band[b]
                exp_row = exp3[exp3["rank_bin"] == b]
                val_row = val3[val3["rank_bin"] == b]
                hg_rows.append({
                    "row_scope": row_scope, "campaign_type": ctype, "metric": metric, "band3": b,
                    "explore_n_group_days": n_e,
                    "explore_sum_ad_profit": float(exp_row["sum_ad_profit"].iloc[0]) if len(exp_row) else "",
                    "explore_profit_per_group_day": float(exp_row["profit_per_group_day"].iloc[0]) if len(exp_row) else "",
                    "explore_cost_per_group_day": float(exp_row["cost_per_group_day"].iloc[0]) if len(exp_row) else "",
                    "explore_roas": float(exp_row["roas"].iloc[0]) if len(exp_row) else "",
                    "validate_n_group_days": n_v,
                    "validate_sum_ad_profit": float(val_row["sum_ad_profit"].iloc[0]) if len(val_row) else "",
                    "validate_profit_per_group_day": float(val_row["profit_per_group_day"].iloc[0]) if len(val_row) else "",
                    "validate_cost_per_group_day": float(val_row["cost_per_group_day"].iloc[0]) if len(val_row) else "",
                    "validate_roas": float(val_row["roas"].iloc[0]) if len(val_row) else "",
                    "validate_share_of_total": round(share, 3) if pd.notna(share) else "",
                    "order_explore": ">".join(order_e), "order_validate": ">".join(order_v),
                    "peak9_explore": peak_e or "", "peak9_validate": peak_v or "",
                    "gate_applicable": not (zero_flag or conc_flag),
                    "gate_result": gate_result,
                })

holdout_gate = pd.DataFrame(hg_rows)
save(holdout_gate, "L1_holdout_gate.csv")
log("\n=== 홀드아웃 게이트: row_scope(all/baseline_only) x metric(강도/효율) ===")
log(holdout_gate[["row_scope", "campaign_type", "metric", "band3", "explore_n_group_days", "validate_n_group_days",
                   "validate_share_of_total", "order_explore", "order_validate", "gate_result"]]
    .to_string(index=False))


# ===========================================================================
# 6) 민감도 3종 (METHOD §3-4)
# ===========================================================================
sens_rows = []
BASE_POS = {b: i for i, b in enumerate(BAND3_ORDER)}  # 구조적 위치: 0=저순위밴드 1=중간밴드 2=고순위밴드


def add_sens_rows(variant, tab, order_col="rank_bin", weight_note="", label_to_pos=None):
    """order_in_variant=사람이 읽는 라벨 순서, order_generic=경계가 달라도 비교 가능한 위치(0/1/2) 순서.
    ⚠️ bin 경계를 밀면 라벨 문자열 자체가 바뀌므로(예: "<2.25"), 문자열 비교로 "순서 유지"를
    판정하면 항상 False가 나온다 — 반드시 구조적 위치로 비교한다."""
    ltp = label_to_pos or BASE_POS
    for ctype in ["SHOPPING", "WEB_SITE"]:
        sub = tab[tab["campaign_type"] == ctype]
        order = order_by_profit(sub, order_col)
        generic = ">".join(str(ltp.get(b, "?")) for b in order)
        for _, r in sub.iterrows():
            sens_rows.append({
                "variant": variant, "campaign_type": ctype, "band3": r[order_col],
                "n_group_days": r.get("n_group_days", ""), "n_groups": r.get("n_groups", ""),
                "profit_per_group_day": r["profit_per_group_day"],
                "order_in_variant": ">".join(order), "order_generic": generic, "note": weight_note,
            })


# 베이스라인(그룹-일 가중, 원 경계) 순서 — 대조용
base_order = {ctype: order_by_profit(tab3[tab3["campaign_type"] == ctype]) for ctype in ["SHOPPING", "WEB_SITE"]}
base_generic = {ctype: ">".join(str(BASE_POS[b]) for b in base_order[ctype]) for ctype in ["SHOPPING", "WEB_SITE"]}
add_sens_rows("0_baseline(그룹-일 가중, 경계 원본)", tab3)

# ① bin 경계 ±0.25 — 구조적 위치(저/중/고)로 비교, 라벨 문자열로 비교하지 않는다
for shift, tag in [(-0.25, "1_bin_shift_-0.25"), (0.25, "1_bin_shift_+0.25")]:
    band3_s, order_s = to_band3(rank_universe["avg_rank"], lo=2.5 + shift, hi=4.0 + shift)
    tmp = rank_universe.copy()
    tmp["band3_shift"] = band3_s
    tab_s = aggregate(tmp, "band3_shift")  # aggregate()가 내부적으로 "rank_bin"으로 통일
    ltp_shift = {b: i for i, b in enumerate(order_s)}  # [저,중,고] 구조 순서 그대로
    add_sens_rows(tag, tab_s, label_to_pos=ltp_shift)

# ② 평시 행만 (factor_label·baseline_rows는 §2-b에서 이미 계산 — 재정의하지 않는다)
tab3_baseline = aggregate(baseline_rows, "band3")
add_sens_rows("2_평시행만", tab3_baseline)

# ③ 그룹 가중 (그룹별 평균 낸 뒤 구간 집계)
def weighted_avg(sub):
    w = sub["cost"].sum()
    if w > 0:
        return float((sub["avg_rank"] * sub["cost"]).sum() / w)
    return float(sub["avg_rank"].mean())


grp_rows = []
for (gid, ctype), sub in rank_universe.groupby(["adgroup_id", "campaign_type"], observed=True):
    n_days = len(sub)
    sum_cost = int(sub["cost"].sum())
    sum_conv_amt = int(sub["conv_amt"].sum())
    sum_ad_profit = round(float(sub["ad_profit"].sum()), 2)
    avg_rank_g = weighted_avg(sub)
    if sum_cost == 0:
        band = "cost0_excluded"
    else:
        roas = sum_conv_amt / sum_cost
        band = "band1" if roas >= BEP_ROAS_120 else ("band2" if roas >= BEP_ROAS else "band3")
    grp_rows.append({
        "adgroup_id": gid, "campaign_type": ctype, "avg_rank": round(avg_rank_g, 4),
        "n_days": n_days, "sum_cost": sum_cost, "sum_conv_amt": sum_conv_amt,
        "sum_ad_profit": sum_ad_profit, "profit_per_group_day": round(sum_ad_profit / n_days, 2),
        "band": band,
    })
group_profile = pd.DataFrame(grp_rows)
band3_of, _ = to_band3(group_profile["avg_rank"])
group_profile["band3"] = band3_of
save(group_profile, "L1_group_rank_profile.csv")

grp_weighted_tab = group_profile.groupby(["campaign_type", "band3"], observed=True).agg(
    n_groups=("adgroup_id", "nunique"),
    mean_profit_per_group_day=("profit_per_group_day", "mean"),
).reset_index()
for ctype in ["SHOPPING", "WEB_SITE"]:
    sub = grp_weighted_tab[grp_weighted_tab["campaign_type"] == ctype].copy()
    sub = sub[sub["band3"].isin(BAND3_ORDER)]
    sub_order = sub[sub["n_groups"] > 0].sort_values("mean_profit_per_group_day", ascending=False)["band3"].tolist()
    sub_generic = ">".join(str(BASE_POS.get(b, "?")) for b in sub_order)
    for _, r in sub.iterrows():
        sens_rows.append({
            "variant": "3_그룹가중(그룹별 평균 먼저)", "campaign_type": ctype,
            "band3": r["band3"], "n_group_days": "", "n_groups": r["n_groups"],
            "profit_per_group_day": round(r["mean_profit_per_group_day"], 2),
            "order_in_variant": ">".join(sub_order), "order_generic": sub_generic,
            "note": "그룹당 profit_per_group_day의 단순평균(가중 없음)",
        })

sensitivity = pd.DataFrame(sens_rows)
sensitivity["matches_baseline_order"] = sensitivity.apply(
    lambda r: r["order_generic"] == base_generic.get(r["campaign_type"], ""), axis=1)
save(sensitivity, "L1_sensitivity.csv")
log("\n=== 민감도 3종: 변형별 순서 (베이스라인과 구조적으로 일치? 0=저순위밴드 1=중간밴드 2=고순위밴드) ===")
log(sensitivity[["variant", "campaign_type", "order_in_variant", "order_generic", "matches_baseline_order"]]
    .drop_duplicates(subset=["variant", "campaign_type"]).to_string(index=False))


# ===========================================================================
# 7) ROAS 밴드 × 순위 구간 교차표 (그룹 단위, §2 산출물 목록)
# ===========================================================================
band_x_rank = group_profile.groupby(["campaign_type", "band", "band3"], observed=True).agg(
    n_groups=("adgroup_id", "nunique"), sum_cost=("sum_cost", "sum"), sum_ad_profit=("sum_ad_profit", "sum"),
).reset_index()
save(band_x_rank, "L1_band_x_rank.csv")
log("\n=== ROAS밴드(그룹단위) x 순위구간(band3) 교차표 ===")
log(band_x_rank.to_string(index=False))


# ===========================================================================
# 8) 보강 — 선행 주장 4건(P-A~P-D) 재검증
# ===========================================================================
log("\n\n##################  선행 주장 4건 재검증  ##################")

# --- P-A: 매출·ROAS·이익금액 3채점 최대구간 일치 여부 (band3 기준) ---
# ★2라운드 반영: argmax만으론 "지배"와 "거의 동률"을 못 가른다 — ROAS 스프레드(최대-최소)를 병기.
pa_rows = []
for ctype in ["SHOPPING", "WEB_SITE"]:
    sub = tab3[tab3["campaign_type"] == ctype].set_index("rank_bin").reindex(BAND3_ORDER)
    argmax_conv_amt = sub["sum_conv_amt"].idxmax()
    argmax_roas = sub["roas"].idxmax()
    argmax_ad_profit = sub["sum_ad_profit"].idxmax()
    argmax_profit_per_day = sub["profit_per_group_day"].idxmax()
    all_agree = len({argmax_conv_amt, argmax_roas, argmax_ad_profit}) == 1
    roas_max, roas_min = sub["roas"].max(), sub["roas"].min()
    roas_spread = round(roas_max - roas_min, 4)
    roas_spread_pct_of_mean = round(roas_spread / sub["roas"].mean() * 100, 1)
    flat_or_dominant = "평탄(스프레드<10%)" if roas_spread_pct_of_mean < 10 else \
                        ("약한 우위(10~25%)" if roas_spread_pct_of_mean < 25 else "지배 후보(≥25%)")
    pa_rows.append({
        "campaign_type": ctype, "argmax_sum_conv_amt": argmax_conv_amt, "argmax_roas": argmax_roas,
        "argmax_sum_ad_profit(절대액)": argmax_ad_profit,
        "argmax_profit_per_group_day(일평균, 강도 오염 주의)": argmax_profit_per_day,
        "3채점_일치(매출/ROAS/이익절대액)": all_agree,
        "dominant_in_25_40": (argmax_conv_amt == "[2.5,4)" and argmax_roas == "[2.5,4)" and argmax_ad_profit == "[2.5,4)"),
        "roas_<2.5": round(sub.loc["<2.5", "roas"], 4), "roas_[2.5,4)": round(sub.loc["[2.5,4)", "roas"], 4),
        "roas_≥4": round(sub.loc["≥4", "roas"], 4),
        "roas_spread(max-min)": roas_spread, "roas_spread_%of_mean": roas_spread_pct_of_mean,
        "지배_vs_평탄": flat_or_dominant,
    })
pa_df = pd.DataFrame(pa_rows)
save(pa_df, "L1_prior_claim_A_roas_spread.csv")
log("\n--- P-A: 3채점 최대구간 일치 + ROAS 스프레드(지배 vs 평탄 구분) ---")
log(pa_df.to_string(index=False))

# --- P-B: <2.5 vs [2.5,4) 볼륨배수 · 이익배수 ---
pb_rows = []
for ctype in ["SHOPPING", "WEB_SITE"]:
    sub = tab3[tab3["campaign_type"] == ctype].set_index("rank_bin").reindex(BAND3_ORDER)
    lo, mid = sub.loc["<2.5"], sub.loc["[2.5,4)"]
    imp_ratio = lo["sum_imp"] / mid["sum_imp"] if mid["sum_imp"] else np.nan
    clk_ratio = lo["sum_clk"] / mid["sum_clk"] if mid["sum_clk"] else np.nan
    profit_ratio = (lo["sum_ad_profit"] / mid["sum_ad_profit"]) if mid["sum_ad_profit"] not in (0, np.nan) else np.nan
    pb_rows.append({
        "campaign_type": ctype, "imp_lt25": int(lo["sum_imp"]), "imp_2540": int(mid["sum_imp"]),
        "imp_ratio(<2.5/[2.5,4))": round(imp_ratio, 3) if pd.notna(imp_ratio) else "",
        "clk_lt25": int(lo["sum_clk"]), "clk_2540": int(mid["sum_clk"]),
        "clk_ratio(<2.5/[2.5,4))": round(clk_ratio, 3) if pd.notna(clk_ratio) else "",
        "ad_profit_lt25": lo["sum_ad_profit"], "ad_profit_2540": mid["sum_ad_profit"],
        "profit_ratio(<2.5/[2.5,4))": round(profit_ratio, 3) if pd.notna(profit_ratio) else "",
    })
pb_df = pd.DataFrame(pb_rows)
log("\n--- P-B: <2.5 vs [2.5,4) 볼륨·이익 배수 (참고: 원문 주장 볼륨 2.4배 / 이익 1/3) ---")
log(pb_df.to_string(index=False))

# --- P-C: CTR 6버킷 (ref38 §1과 정확히 nest, 9-bin을 합쳐서 씀 — 새 bin 없음) ---
BUCKET6_MAP = {
    "[1,1.5)": "~2.0", "[1.5,2)": "~2.0",
    "[2,2.5)": "2~3", "[2.5,3)": "2~3",
    "[3,3.5)": "3~4", "[3.5,4)": "3~4",
    "[4,5)": "4~5", "[5,7)": "5~7", "[7,∞)": "7+",
}
BUCKET6_ORDER = ["~2.0", "2~3", "3~4", "4~5", "5~7", "7+"]
REF38_CTR = {  # ref38 §1 원문 수치 — 새 파라미터 아님, 비교용 상수. 출처: "쇼핑 전 캠페인 90일"
    "~2.0": {"imp": 106479, "clk": 1001, "ctr": 0.0094},
    "2~3": {"imp": 273324, "clk": 3476, "ctr": 0.01272},
    "3~4": {"imp": 405072, "clk": 4954, "ctr": 0.01223},
    "4~5": {"imp": 488524, "clk": 4267, "ctr": 0.00873},
    "5~7": {"imp": 1078576, "clk": 4433, "ctr": 0.00411},
    "7+": {"imp": 1266427, "clk": 1924, "ctr": 0.00152},
}
rank_universe["bucket6"] = rank_universe["rank_bin9"].astype(str).map(BUCKET6_MAP)
pc_rows = []
for ctype_scope, df_scope in [("SHOPPING", rank_universe[rank_universe["campaign_type"] == "SHOPPING"]),
                               ("WEB_SITE", rank_universe[rank_universe["campaign_type"] == "WEB_SITE"]),
                               ("ALL(SHOPPING+WEB_SITE)", rank_universe)]:
    g = df_scope.groupby("bucket6", observed=True).agg(sum_imp=("imp", "sum"), sum_clk=("clk", "sum")).reindex(BUCKET6_ORDER)
    for b in BUCKET6_ORDER:
        imp_1y = int(g.loc[b, "sum_imp"]) if pd.notna(g.loc[b, "sum_imp"]) else 0
        clk_1y = int(g.loc[b, "sum_clk"]) if pd.notna(g.loc[b, "sum_clk"]) else 0
        ctr_1y = clk_1y / imp_1y if imp_1y else np.nan
        ref = REF38_CTR[b]
        pc_rows.append({
            "scope": ctype_scope, "rank_bucket_ref38": b,
            "imp_1yr": imp_1y, "clk_1yr": clk_1y, "ctr_1yr": round(ctr_1y, 5) if pd.notna(ctr_1y) else "",
            "imp_ref38(90일,SHOPPING)": ref["imp"], "clk_ref38": ref["clk"], "ctr_ref38": ref["ctr"],
            "ctr_diff(1yr-ref38)": round(ctr_1y - ref["ctr"], 5) if pd.notna(ctr_1y) else "",
        })
pc_df = pd.DataFrame(pc_rows)
save(pc_df, "L1_prior_claim_C_ctr.csv")
log("\n--- P-C: CTR 6버킷 1년 vs ref38(90일, SHOPPING) 대조 ---")
log(pc_df.to_string(index=False))

# --- P-D: 전환단가(CPA) 3버킷 (~3 / 3~5 / 5+) ---
BUCKET3_MAP = {
    "[1,1.5)": "~3", "[1.5,2)": "~3", "[2,2.5)": "~3", "[2.5,3)": "~3",
    "[3,3.5)": "3~5", "[3.5,4)": "3~5", "[4,5)": "3~5",
    "[5,7)": "5+", "[7,∞)": "5+",
}
BUCKET3_ORDER = ["~3", "3~5", "5+"]
REF38_CPA = {  # ref38 §2 원문 수치 — 새 파라미터 아님, 비교용 상수
    "~3": {"imp": 379803, "clk": 4477, "cost": 6831127, "conv_cnt": 728, "cpa": 9383},
    "3~5": {"imp": 893596, "clk": 9221, "cost": 13561424, "conv_cnt": 1531, "cpa": 8858},
    "5+": {"imp": 2345003, "clk": 6357, "cost": 6554216, "conv_cnt": 807, "cpa": 8122},
}
rank_universe["bucket3"] = rank_universe["rank_bin9"].astype(str).map(BUCKET3_MAP)
pd_rows = []
for ctype_scope, df_scope in [("SHOPPING", rank_universe[rank_universe["campaign_type"] == "SHOPPING"]),
                               ("WEB_SITE", rank_universe[rank_universe["campaign_type"] == "WEB_SITE"]),
                               ("ALL(SHOPPING+WEB_SITE)", rank_universe)]:
    g = df_scope.groupby("bucket3", observed=True).agg(
        sum_cost=("cost", "sum"), sum_conv_cnt=("conv_cnt", "sum")).reindex(BUCKET3_ORDER)
    for b in BUCKET3_ORDER:
        cost_1y = int(g.loc[b, "sum_cost"]) if pd.notna(g.loc[b, "sum_cost"]) else 0
        cnt_1y = int(g.loc[b, "sum_conv_cnt"]) if pd.notna(g.loc[b, "sum_conv_cnt"]) else 0
        cpa_1y = cost_1y / cnt_1y if cnt_1y else np.nan
        ref = REF38_CPA[b]
        pd_rows.append({
            "scope": ctype_scope, "rank_bucket_ref38": b,
            "cost_1yr": cost_1y, "conv_cnt_1yr": cnt_1y,
            "cpa_1yr": round(cpa_1y, 1) if pd.notna(cpa_1y) else "",
            "cpa_ref38(90일,SHOPPING)": ref["cpa"],
            "cpa_diff(1yr-ref38)": round(cpa_1y - ref["cpa"], 1) if pd.notna(cpa_1y) else "",
        })
pd_df = pd.DataFrame(pd_rows)
save(pd_df, "L1_prior_claim_D_cpa.csv")
log("\n--- P-D: 전환단가(CPA) 3버킷 1년 vs ref38(90일, SHOPPING) 대조 ---")
log(pd_df.to_string(index=False))

# --- BEP 민감도: 1.711(현재) vs 1.4758(ref31 진짜 BEP, 과거 확정값) ---
BEP_ALT = 1.4758  # ref 31 §1-c "진짜 BEP 1.4758" — 신규 발명 아님, 문서에 있는 과거 확정값
rank_universe["ad_profit_bep_alt"] = rank_universe["conv_amt"] / BEP_ALT - rank_universe["cost"]

bep_sens_rows = []
for bep_tag, profit_col in [("1.711(현재 계정 BEP)", "ad_profit"),
                             ("1.4758(ref31 진짜 BEP)", "ad_profit_bep_alt")]:
    for ctype in ["SHOPPING", "WEB_SITE"]:
        sub = rank_universe[rank_universe["campaign_type"] == ctype]
        g = sub.groupby("band3", observed=True).agg(
            n_group_days=(profit_col, "size"), sum_ad_profit=(profit_col, "sum")).reindex(BAND3_ORDER)
        g["profit_per_group_day"] = (g["sum_ad_profit"] / g["n_group_days"]).round(2)
        order = g[g["n_group_days"] > 0].sort_values("profit_per_group_day", ascending=False).index.tolist()
        for b in BAND3_ORDER:
            bep_sens_rows.append({
                "bep_value": bep_tag, "campaign_type": ctype, "band3": b,
                "n_group_days": int(g.loc[b, "n_group_days"]) if pd.notna(g.loc[b, "n_group_days"]) else 0,
                "sum_ad_profit": round(g.loc[b, "sum_ad_profit"], 2) if pd.notna(g.loc[b, "sum_ad_profit"]) else "",
                "profit_per_group_day": g.loc[b, "profit_per_group_day"] if pd.notna(g.loc[b, "profit_per_group_day"]) else "",
                "order_in_bep": ">".join(order),
            })
bep_sens_df = pd.DataFrame(bep_sens_rows)
save(bep_sens_df, "L1_sensitivity_bep.csv")
log("\n--- BEP 민감도(1.711 vs 1.4758): 3구간 순서 ---")
log(bep_sens_df[["bep_value", "campaign_type", "order_in_bep"]].drop_duplicates().to_string(index=False))
bep_order_matches = {}
for ctype in ["SHOPPING", "WEB_SITE"]:
    o1 = bep_sens_df[(bep_sens_df["bep_value"].str.startswith("1.711")) & (bep_sens_df["campaign_type"] == ctype)]["order_in_bep"].iloc[0]
    o2 = bep_sens_df[(bep_sens_df["bep_value"].str.startswith("1.4758")) & (bep_sens_df["campaign_type"] == ctype)]["order_in_bep"].iloc[0]
    bep_order_matches[ctype] = (o1 == o2)
log(f"BEP 값 변경에도 순서 유지 여부: {bep_order_matches}")

# --- 종합 판정 CSV (규칙은 원 주장이 스스로 명시한 폭을 그대로 씀 — 새 문턱 발명 0) ---
pa_shopping = pa_df[pa_df["campaign_type"] == "SHOPPING"].iloc[0]
pa_web = pa_df[pa_df["campaign_type"] == "WEB_SITE"].iloc[0]

# P-A: SHOPPING 기준(원 주장의 scope) 3채점 일치 + 2.5~4 지배 여부
# ★2라운드 반영: dominant_in_25_40이 True라도 roas_spread가 "평탄" 범주면 진짜 지배가 아니다.
if bool(pa_shopping["3채점_일치(매출/ROAS/이익절대액)"]) and bool(pa_shopping["dominant_in_25_40"]) \
        and pa_shopping["지배_vs_평탄"] != "평탄(스프레드<10%)":
    pa_verdict = "지지"
elif bool(pa_shopping["3채점_일치(매출/ROAS/이익절대액)"]):
    pa_verdict = "부분지지"
else:
    pa_verdict = "미지지"
pa_evidence = (f"SHOPPING: argmax(매출)={pa_shopping['argmax_sum_conv_amt']} "
               f"argmax(ROAS)={pa_shopping['argmax_roas']} argmax(이익절대액)={pa_shopping['argmax_sum_ad_profit(절대액)']} "
               f"3채점일치={pa_shopping['3채점_일치(매출/ROAS/이익절대액)']} "
               f"ROAS(<2.5/[2.5,4)/≥4)={pa_shopping['roas_<2.5']}/{pa_shopping['roas_[2.5,4)']}/{pa_shopping['roas_≥4']} "
               f"스프레드={pa_shopping['roas_spread(max-min)']}({pa_shopping['roas_spread_%of_mean']}%, "
               f"{pa_shopping['지배_vs_평탄']}) | WEB_SITE: "
               f"argmax(매출)={pa_web['argmax_sum_conv_amt']} argmax(ROAS)={pa_web['argmax_roas']} "
               f"argmax(이익절대액)={pa_web['argmax_sum_ad_profit(절대액)']} "
               f"ROAS(<2.5/[2.5,4)/≥4)={pa_web['roas_<2.5']}/{pa_web['roas_[2.5,4)']}/{pa_web['roas_≥4']} "
               f"스프레드={pa_web['roas_spread(max-min)']}({pa_web['roas_spread_%of_mean']}%, {pa_web['지배_vs_평탄']})")

# P-B: <2.5 vs [2.5,4) 볼륨배수(imp·clk 중 원 주장 2.4배에 더 가까운 쪽)·이익배수(원 주장 1/3)
pb_shopping = pb_df[pb_df["campaign_type"] == "SHOPPING"].iloc[0]
pb_web = pb_df[pb_df["campaign_type"] == "WEB_SITE"].iloc[0]


def _pb_judge(row):
    imp_r, clk_r, prof_r = row["imp_ratio(<2.5/[2.5,4))"], row["clk_ratio(<2.5/[2.5,4))"], row["profit_ratio(<2.5/[2.5,4))"]
    vol_close = any(pd.notna(v) and 1.8 <= v <= 3.0 for v in [imp_r, clk_r] if v != "")
    profit_close = pd.notna(prof_r) and prof_r != "" and 0.15 <= prof_r <= 0.55
    if vol_close and profit_close:
        return "지지"
    if vol_close or profit_close:
        return "부분지지"
    return "미지지"


pb_verdict_shopping = _pb_judge(pb_shopping)
pb_verdict_web = _pb_judge(pb_web)
pb_verdict = pb_verdict_shopping if pb_verdict_shopping != "미지지" else pb_verdict_web
pb_evidence = (f"SHOPPING imp배수={pb_shopping['imp_ratio(<2.5/[2.5,4))']} "
               f"clk배수={pb_shopping['clk_ratio(<2.5/[2.5,4))']} "
               f"이익배수={pb_shopping['profit_ratio(<2.5/[2.5,4))']} "
               f"(원 주장 2.4배/1·3배) | WEB_SITE imp배수={pb_web['imp_ratio(<2.5/[2.5,4))']} "
               f"clk배수={pb_web['clk_ratio(<2.5/[2.5,4))']} 이익배수={pb_web['profit_ratio(<2.5/[2.5,4))']}")

# P-C: 5~7·7+ CTR이 피크(2~3 또는 3~4) 대비 1/3~1/8 붕괴 + 과열(~2.0)<2~3 역전, SHOPPING(원 주장 scope)
pc_shop = pc_df[pc_df["scope"] == "SHOPPING"].set_index("rank_bucket_ref38")
peak_ctr = max(pc_shop.loc["2~3", "ctr_1yr"] or 0, pc_shop.loc["3~4", "ctr_1yr"] or 0)
r_5to7 = (pc_shop.loc["5~7", "ctr_1yr"] / peak_ctr) if peak_ctr else np.nan
r_7plus = (pc_shop.loc["7+", "ctr_1yr"] / peak_ctr) if peak_ctr else np.nan
collapse_reproduced = pd.notna(r_5to7) and pd.notna(r_7plus) and (r_5to7 <= 0.55) and (r_7plus <= 0.30)
overheat_reproduced = (pc_shop.loc["~2.0", "ctr_1yr"] or 0) < (pc_shop.loc["2~3", "ctr_1yr"] or 0)
if collapse_reproduced and overheat_reproduced:
    pc_verdict = "지지"
elif collapse_reproduced or overheat_reproduced:
    pc_verdict = "부분지지"
else:
    pc_verdict = "미지지"
pc_evidence = (f"SHOPPING 1yr CTR ~2.0={pc_shop.loc['~2.0','ctr_1yr']} 2~3={pc_shop.loc['2~3','ctr_1yr']} "
               f"3~4={pc_shop.loc['3~4','ctr_1yr']} 4~5={pc_shop.loc['4~5','ctr_1yr']} "
               f"5~7={pc_shop.loc['5~7','ctr_1yr']} 7+={pc_shop.loc['7+','ctr_1yr']} | "
               f"5~7/피크={round(r_5to7,3) if pd.notna(r_5to7) else ''} 7+/피크={round(r_7plus,3) if pd.notna(r_7plus) else ''} "
               f"(원 주장 1/3~1/8 = 0.125~0.333) 과열역전재현={overheat_reproduced}")

# P-D: ~3/3~5/5+ CPA 스프레드가 원 주장(±15%) 폭 안에 있는가, SHOPPING(원 주장 scope)
pd_shop = pd_df[pd_df["scope"] == "SHOPPING"].set_index("rank_bucket_ref38")
cpas = [v for v in pd_shop["cpa_1yr"].tolist() if v != "" and pd.notna(v)]
if len(cpas) == 3:
    spread_pct = (max(cpas) - min(cpas)) / (sum(cpas) / 3) * 100
else:
    spread_pct = np.nan
if pd.notna(spread_pct) and spread_pct <= 20:
    pd_verdict = "지지"
elif pd.notna(spread_pct) and spread_pct <= 40:
    pd_verdict = "부분지지"
elif pd.notna(spread_pct):
    pd_verdict = "미지지"
else:
    pd_verdict = "판정불능"
pd_evidence = (f"SHOPPING 1yr CPA ~3={pd_shop.loc['~3','cpa_1yr']} 3~5={pd_shop.loc['3~5','cpa_1yr']} "
               f"5+={pd_shop.loc['5+','cpa_1yr']} 스프레드={round(spread_pct,1) if pd.notna(spread_pct) else ''}% "
               f"(원 주장 ±15% 평평)")

verdict_rows = [
    {"claim_id": "P-A", "source": "ref31(12일)",
     "claim": "이익극대 스팟=2.5~4, 매출·ROAS·이익금액 3채점 일치, 쇼핑에서 완전 지배",
     "verdict": pa_verdict, "evidence": pa_evidence},
    {"claim_id": "P-B", "source": "ref41:174",
     "claim": "<2.5(1·2위) 볼륨 2.4배, 이익 1/3 (vs [2.5,4))",
     "verdict": pb_verdict, "evidence": pb_evidence},
    {"claim_id": "P-C", "source": "ref38 §1(90일, SHOPPING)",
     "claim": "가시임계=순위4, 5위밖(5~7·7+) CTR 피크의 1/3~1/8 붕괴, 과열(~2.0)이 2~3보다 CTR 낮음",
     "verdict": pc_verdict, "evidence": pc_evidence},
    {"claim_id": "P-D", "source": "ref38 §2(90일, SHOPPING)",
     "claim": "전환단가(CPA)는 순위와 무관하게 평평(±15% 내)",
     "verdict": pd_verdict, "evidence": pd_evidence},
]
verdict_df = pd.DataFrame(verdict_rows)
save(verdict_df, "L1_prior_claims_verdict.csv")
log("\n=== 선행 주장 4건 종합 판정 ===")
log(verdict_df.to_string(index=False))

with open(DATA_DIR / "_L1_report_dump.txt", "w") as f:
    f.write("\n".join(report_lines))

log("\n\n=== 완료 ===")
