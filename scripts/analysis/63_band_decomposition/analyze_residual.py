#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_residual.py — METHOD_residual_profit.md §0~§4·§7 구현.
읽기 전용. prod DB 쓰기 없음(BEP 조회만 별도 SQL 파일로 이미 뽑아둔 adgroup_bep.csv 사용).
재실행 가능(현재시각/랜덤 미사용). 파라미터는 전부 METHOD 문서·build_panel.py에서 확정된 값만 쓴다.
"""
import pandas as pd
import numpy as np
from pathlib import Path

SCRATCH = Path("/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/02e2416c-335b-4cfb-b417-e123946b0921/scratchpad")

BEP_ROAS = 1.711
BEP_ROAS_120 = round(BEP_ROAS * 1.2, 4)  # 2.0532
MATURE_CUTOFF = pd.Timestamp("2026-08-09")

NON_BASELINE_LABELS = ["F2", "F1b", "F1a", "연휴", "공휴일", "휴가창", "주말", "F3"]
BASE_PRIORITY = ["F2", "F1b", "F1a", "연휴", "공휴일", "휴가창", "주말", "F3"]
ALT_A_PRIORITY = ["휴가창", "F2", "F1b", "F1a", "연휴", "공휴일", "주말", "F3"]  # 휴가창을 F2보다 앞
ALT_B_PRIORITY = ["주말", "F2", "F1b", "F1a", "연휴", "공휴일", "휴가창", "F3"]  # 주말을 맨 앞

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)

report_lines = []


def log(s=""):
    print(s)
    report_lines.append(str(s))


# ---------------------------------------------------------------------------
# 0) 로드
# ---------------------------------------------------------------------------
panel = pd.read_csv(SCRATCH / "panel_labeled.csv", dtype={"campaign_id": str, "adgroup_id": str})
panel["ad_date"] = pd.to_datetime(panel["ad_date"])
for b in ["is_weekend", "is_seollal_chuseok", "is_holiday", "is_vacation", "is_vacation_peak",
          "mature", "data_gap", "model_known"]:
    if panel[b].dtype != bool:
        panel[b] = panel[b].astype(str).map({"True": True, "False": False}).astype(bool)

amm = pd.read_csv(SCRATCH / "adgroup_model_map.csv", dtype=str)
band_total_df = pd.read_csv(SCRATCH / "band_group_total.csv", dtype={"adgroup_id": str, "campaign_id": str})
adgroup_bep = pd.read_csv(SCRATCH / "adgroup_bep.csv", dtype={"adgroup_id": str})

log(f"panel_labeled.csv 로드: {len(panel)}행, 그룹수 {panel['adgroup_id'].nunique()}")

# ---------------------------------------------------------------------------
# 1) ad_profit + 분석대상 필터
# ---------------------------------------------------------------------------
panel["ad_profit"] = panel["conv_amt"] / BEP_ROAS - panel["cost"]

excl_mask = (~panel["mature"]) | (panel["data_gap"])
excluded = panel[excl_mask]
analysis = panel[~excl_mask].copy()

n_excluded = len(excluded)
cost_excluded = int(excluded["cost"].sum())
log(f"\n=== 제외 행 (mature=False 또는 data_gap=True) ===")
log(f"제외 행수: {n_excluded} / cost 합계: {cost_excluded:,}원")
log(f"분석대상 행수: {len(analysis)}")


# ---------------------------------------------------------------------------
# 2) 라벨 배정 (상호배타)
# ---------------------------------------------------------------------------
def label_conditions(df):
    return {
        "F2": df["launch_phase"] == "F2",
        "F1b": df["launch_phase"] == "F1b",
        "F1a": df["launch_phase"] == "F1a",
        "연휴": df["is_seollal_chuseok"],
        "공휴일": df["is_holiday"],
        "휴가창": df["is_vacation"],
        "주말": df["is_weekend"],
        "F3": df["launch_phase"] == "F3",
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


analysis["factor_label"] = assign_labels(analysis, BASE_PRIORITY)
analysis["quarter"] = analysis["ad_date"].dt.year.astype(str) + "Q" + (
    (analysis["ad_date"].dt.month - 1) // 3 + 1
).astype(str)

log("\n=== 라벨 분포(기본 우선순위) ===")
log(analysis["factor_label"].value_counts().to_string())

# ---------------------------------------------------------------------------
# 3) V1 baseline (전기간)
# ---------------------------------------------------------------------------
base_rows = analysis[analysis["factor_label"] == "평시"]
b_g_v1 = base_rows.groupby("adgroup_id")["ad_profit"].mean()
n_baseline_v1 = base_rows.groupby("adgroup_id").size()

n_total_g = analysis.groupby("adgroup_id").size()
total_ad_profit_g = analysis.groupby("adgroup_id")["ad_profit"].sum()

all_groups = sorted(analysis["adgroup_id"].unique())
baseline_unavailable = pd.Series(
    [g not in b_g_v1.index for g in all_groups], index=all_groups
)

log(f"\n=== baseline_unavailable (평시 표본 0일 그룹) ===")
n_unavail = int(baseline_unavailable.sum())
cost_unavail = int(analysis[analysis["adgroup_id"].isin(baseline_unavailable[baseline_unavailable].index)]["cost"].sum())
log(f"그룹수: {n_unavail} / 해당 그룹 cost 합계: {cost_unavail:,}원")

# row-level excess (V1)
analysis["b_g_v1"] = analysis["adgroup_id"].map(b_g_v1)
analysis["excess_v1_row"] = np.where(
    analysis["factor_label"] != "평시",
    analysis["ad_profit"] - analysis["b_g_v1"],
    np.nan,
)

excess_v1_by_group_label = (
    analysis[analysis["factor_label"] != "평시"]
    .groupby(["adgroup_id", "factor_label"])["excess_v1_row"]
    .sum()
    .unstack(fill_value=0.0)
)
for lab in NON_BASELINE_LABELS:
    if lab not in excess_v1_by_group_label.columns:
        excess_v1_by_group_label[lab] = 0.0
excess_v1_by_group_label = excess_v1_by_group_label.reindex(all_groups, fill_value=0.0)
# baseline_unavailable 그룹은 excess도 판정 불가 → NaN으로 되돌림
excess_v1_by_group_label.loc[baseline_unavailable[baseline_unavailable].index, :] = np.nan

residual_v1 = (b_g_v1.reindex(all_groups) * n_total_g.reindex(all_groups))
residual_v1.loc[baseline_unavailable[baseline_unavailable].index] = np.nan

# ---------------------------------------------------------------------------
# 4) 항등식 검산 (V1)
# ---------------------------------------------------------------------------
excess_v1_sum = excess_v1_by_group_label[NON_BASELINE_LABELS].sum(axis=1, min_count=1)
check_v1 = residual_v1 + excess_v1_sum
check_v1_diff = (check_v1 - total_ad_profit_g.reindex(all_groups)).dropna()
tol = 1.0
ok_v1 = (check_v1_diff.abs() <= tol)
log(f"\n=== 항등식 검산 (V1) ===")
log(f"검산 대상 그룹(=baseline 있음): {len(check_v1_diff)} / 일치(±1원): {int(ok_v1.sum())} / 불일치: {int((~ok_v1).sum())}")
if len(check_v1_diff):
    log(f"최대 절대오차: {check_v1_diff.abs().max():.4f}원")
if (~ok_v1).any():
    log("불일치 그룹 상위 5:")
    log(check_v1_diff[~ok_v1].abs().sort_values(ascending=False).head(5).to_string())

# ---------------------------------------------------------------------------
# 5) V2 baseline (분기국소, 표본<5일 V1 폴백)
# ---------------------------------------------------------------------------
q_base = base_rows.groupby(["adgroup_id", "quarter"])["ad_profit"].agg(["mean", "count"])
q_base_local_ok = q_base[q_base["count"] >= 5]["mean"]  # (adgroup_id,quarter)->local mean, 표본 충분

# beta_used per (group, quarter): local if available else V1 fallback (그룹 자체가 baseline_unavailable이면 NaN)
all_gq = analysis[["adgroup_id", "quarter"]].drop_duplicates()
all_gq = all_gq.set_index(["adgroup_id", "quarter"])
beta_used = pd.Series(index=all_gq.index, dtype=float)
fallback_flag = pd.Series(index=all_gq.index, dtype=bool)
for idx in all_gq.index:
    g, q = idx
    if idx in q_base_local_ok.index:
        beta_used[idx] = q_base_local_ok[idx]
        fallback_flag[idx] = False
    else:
        beta_used[idx] = b_g_v1.get(g, np.nan)
        fallback_flag[idx] = True

analysis["beta_v2"] = analysis.set_index(["adgroup_id", "quarter"]).index.map(beta_used)
analysis["fallback_v2"] = analysis.set_index(["adgroup_id", "quarter"]).index.map(fallback_flag)

analysis["excess_v2_row"] = np.where(
    analysis["factor_label"] != "평시",
    analysis["ad_profit"] - analysis["beta_v2"],
    np.nan,
)

excess_v2_by_group_label = (
    analysis[analysis["factor_label"] != "평시"]
    .groupby(["adgroup_id", "factor_label"])["excess_v2_row"]
    .sum()
    .unstack(fill_value=0.0)
)
for lab in NON_BASELINE_LABELS:
    if lab not in excess_v2_by_group_label.columns:
        excess_v2_by_group_label[lab] = 0.0
excess_v2_by_group_label = excess_v2_by_group_label.reindex(all_groups, fill_value=0.0)
excess_v2_by_group_label.loc[baseline_unavailable[baseline_unavailable].index, :] = np.nan

# residual_v2 = 총 ad_profit - Σ_L excess_v2 (잔차=나머지, §1 "제거하고도 남는 것"의 정의를 그대로 적용.
#   V1처럼 β×n_days 형태로 별도 계산해도 대수적으로 동일값이 되나(증명: baseline이 그 구간 평시행의
#   정확한 평균일 때), 분기 폴백이 걸린 구간은 β가 그 분기의 정확한 평균이 아니므로 β×n_days 방식은
#   항등식이 어긋난다 — "나머지"로 정의해 항등식을 구조적으로 보장한다.)
# ★min_count=1: baseline_unavailable 그룹은 8개 라벨열이 전부 NaN인데, skipna 기본 sum은 전부NaN→0.0
#   으로 되돌려 residual이 거짓으로 "total 그대로" 계산되는 함정이 있다(V1은 residual 쪽 NaN이 살아
#   남아 우연히 안전했지만 V2는 아니다) — 실측으로 걸려 여기서 명시적으로 막는다.
excess_v2_sum = excess_v2_by_group_label[NON_BASELINE_LABELS].sum(axis=1, min_count=1)
residual_v2 = total_ad_profit_g.reindex(all_groups) - excess_v2_sum
residual_v2.loc[baseline_unavailable[baseline_unavailable].index] = np.nan

check_v2 = residual_v2 + excess_v2_sum
check_v2_diff = (check_v2 - total_ad_profit_g.reindex(all_groups)).dropna()
ok_v2 = (check_v2_diff.abs() <= tol)
log(f"\n=== 항등식 검산 (V2, 정의상 항상 성립 — 코드 버그 탐지용) ===")
log(f"검산 대상 그룹: {len(check_v2_diff)} / 일치(±1원): {int(ok_v2.sum())} / 불일치: {int((~ok_v2).sum())}")
if len(check_v2_diff):
    log(f"최대 절대오차: {check_v2_diff.abs().max():.4f}원")

# 폴백 사용 그룹/분기 비율
fb_rate = analysis.groupby("adgroup_id")["fallback_v2"].mean()
baseline_v2_used_fallback = fb_rate.reindex(all_groups) > 0  # 그룹 내 분기 중 하나라도 폴백 있으면 True

log(f"\nV2 분기 중 폴백 사용된 (group,quarter) 비율: {fallback_flag.mean():.3f}")
log(f"V2 폴백을 최소 1개 분기에서 사용한 그룹 수: {int((baseline_v2_used_fallback == True).sum())} / 전체 {len(all_groups)}")

report_lines.append("__SPLIT1__")

# ---------------------------------------------------------------------------
# 6) 밴드 판정 로직 (build_panel.py의 band_judge/get_aov를 그대로 재사용 — 새 규칙 발명 금지)
# ---------------------------------------------------------------------------
def aov_lookup_table(df):
    acc = df["conv_amt"].sum() / df["conv_cnt"].sum() if df["conv_cnt"].sum() > 0 else np.nan
    by_type = df.groupby("campaign_type").apply(
        lambda s: s["conv_amt"].sum() / s["conv_cnt"].sum() if s["conv_cnt"].sum() > 0 else np.nan
    ).to_dict()
    by_camp = df.groupby("campaign_id").apply(
        lambda s: s["conv_amt"].sum() / s["conv_cnt"].sum() if s["conv_cnt"].sum() > 0 else np.nan
    ).to_dict()
    by_grp = df.groupby("adgroup_id").apply(
        lambda s: s["conv_amt"].sum() / s["conv_cnt"].sum() if s["conv_cnt"].sum() > 0 else np.nan
    ).to_dict()
    return acc, by_type, by_camp, by_grp


def get_aov(adgroup_id, campaign_id, campaign_type, acc, by_type, by_camp, by_grp):
    v = by_grp.get(adgroup_id)
    if v and not np.isnan(v):
        return v, "adgroup"
    v = by_camp.get(campaign_id)
    if v and not np.isnan(v):
        return v, "campaign"
    v = by_type.get(campaign_type)
    if v and not np.isnan(v):
        return v, "campaign_type"
    return acc, "account"


def band_judge(cost, conv_cnt, conv_amt, aov, bep_roas=BEP_ROAS, bep_roas_120=BEP_ROAS_120, k=3):
    if cost == 0:
        return "excluded_cost0", None
    if conv_cnt >= 1:
        roas = conv_amt / cost
        if roas >= bep_roas_120:
            return "band1", roas
        if roas >= bep_roas:
            return "band2", roas
        return "band3", roas
    else:
        threshold = k * aov / bep_roas if aov and not np.isnan(aov) else np.nan
        if not np.isnan(threshold) and cost > threshold:
            return "band3", 0.0
        return "band4_unjudgeable", None


def run_band_unit(df, group_cols):
    acc, by_type, by_camp, by_grp = aov_lookup_table(df)
    agg = df.groupby(group_cols, as_index=False).agg(
        cost=("cost", "sum"), conv_amt=("conv_amt", "sum"), conv_cnt=("conv_cnt", "sum"),
        clk=("clk", "sum"), n_days=("ad_date", "nunique"),
    )
    bands, roass = [], []
    for _, r in agg.iterrows():
        aov, layer = get_aov(r["adgroup_id"], r["campaign_id"], r["campaign_type"], acc, by_type, by_camp, by_grp)
        b, roas = band_judge(r["cost"], r["conv_cnt"], r["conv_amt"], aov)
        bands.append(b); roass.append(roas)
    agg["band"] = bands
    agg["roas"] = roass
    return agg


# 평시 행만으로 재판정 (band_group_total.csv와 "같은 규칙", 입력 모집단만 평시로 축소)
band_baseline_only_df = run_band_unit(base_rows, ["adgroup_id", "campaign_id", "campaign_type"])
band_baseline_only_df = band_baseline_only_df.set_index("adgroup_id")

log(f"\n=== band_baseline_only 판정 그룹수: {len(band_baseline_only_df)} / 전체 {len(all_groups)} "
    f"(평시 표본 0인 {len(all_groups) - len(band_baseline_only_df)}개 그룹은 판정 불가) ===")
log(band_baseline_only_df["band"].value_counts().to_string())

# ---------------------------------------------------------------------------
# 7) residual_by_group.csv
# ---------------------------------------------------------------------------
amm_idx = amm.set_index("adgroup_id")
band_total_idx = band_total_df.set_index("adgroup_id")

cost_total_g = analysis.groupby("adgroup_id")["cost"].sum()
conv_amt_total_g = analysis.groupby("adgroup_id")["conv_amt"].sum()

rows = []
for g in all_groups:
    info = amm_idx.loc[g] if g in amm_idx.index else None
    model = ""
    if info is not None:
        model = info.get("matched_json_model") or info.get("parsed_model") or ""
        if pd.isna(model):
            model = ""
    adgroup_name = info.get("adgroup_name") if info is not None else ""
    campaign_name = info.get("campaign_name") if info is not None else ""
    campaign_type = info.get("campaign_type") if info is not None else ""

    row = {
        "adgroup_id": g,
        "adgroup_name": adgroup_name,
        "campaign_name": campaign_name,
        "campaign_type": campaign_type,
        "model": model,
        "cost": int(cost_total_g.get(g, 0)),
        "conv_amt": int(conv_amt_total_g.get(g, 0)),
        "total_ad_profit": round(total_ad_profit_g.get(g, np.nan), 2),
        "residual_v1": round(residual_v1.get(g), 2) if pd.notna(residual_v1.get(g)) else "",
        "residual_v2": round(residual_v2.get(g), 2) if pd.notna(residual_v2.get(g)) else "",
        "baseline_v1_daily": round(b_g_v1.get(g), 4) if g in b_g_v1.index else "",
        "baseline_v2_used_fallback": bool(baseline_v2_used_fallback.get(g, False)),
        "n_days": int(n_total_g.get(g, 0)),
        "n_baseline_days": int(n_baseline_v1.get(g, 0)),
        "band_total": band_total_idx.loc[g, "band"] if g in band_total_idx.index else "",
        "band_baseline_only": band_baseline_only_df.loc[g, "band"] if g in band_baseline_only_df.index else "no_baseline_rows",
        "baseline_unavailable": bool(baseline_unavailable.get(g, False)),
    }
    for lab in NON_BASELINE_LABELS:
        v = excess_v1_by_group_label.loc[g, lab] if g in excess_v1_by_group_label.index else np.nan
        row[f"excess_{lab}"] = round(v, 2) if pd.notna(v) else ""
    rows.append(row)

residual_by_group = pd.DataFrame(rows)
residual_by_group = residual_by_group.sort_values("residual_v1", key=lambda s: pd.to_numeric(s, errors="coerce"))
residual_by_group.to_csv(SCRATCH / "residual_by_group.csv", index=False)
log(f"\nresidual_by_group.csv 저장: {len(residual_by_group)}행")

report_lines.append("__SPLIT2__")

# ---------------------------------------------------------------------------
# 8) factor_effect_summary.csv
# ---------------------------------------------------------------------------
CANON = {  # METHOD §2: 휴가·출시축=V1 정본, 요일·주말·공휴일축=V2 정본
    "F2": "V1", "F1b": "V1", "F1a": "V1", "F3": "V1", "휴가창": "V1",
    "주말": "V2", "공휴일": "V2", "연휴": "V2",
}

analysis_valid = analysis[~analysis["adgroup_id"].isin(baseline_unavailable[baseline_unavailable].index)]

fes_rows = []
for variant, col in [("V1", "excess_v1_row"), ("V2", "excess_v2_row")]:
    sub = analysis_valid[analysis_valid["factor_label"] != "평시"]
    for by_type in [True, False]:
        group_cols = ["factor_label", "campaign_type"] if by_type else ["factor_label"]
        g = sub.groupby(group_cols).agg(
            sum_excess=(col, "sum"),
            group_days=(col, "size"),
            n_groups=("adgroup_id", "nunique"),
            sum_cost=("cost", "sum"),
            sum_conv_amt=("conv_amt", "sum"),
        ).reset_index()
        if not by_type:
            g["campaign_type"] = "ALL"
        g["baseline_variant"] = variant
        fes_rows.append(g)

fes = pd.concat(fes_rows, ignore_index=True)
fes["observed_roas"] = fes["sum_conv_amt"] / fes["sum_cost"]
fes["daily_avg_excess_per_group_day"] = fes["sum_excess"] / fes["group_days"]
fes["is_canonical"] = fes.apply(lambda r: CANON.get(r["factor_label"]) == r["baseline_variant"], axis=1)
fes = fes[["factor_label", "campaign_type", "baseline_variant", "is_canonical",
           "sum_excess", "group_days", "n_groups", "sum_cost", "sum_conv_amt",
           "observed_roas", "daily_avg_excess_per_group_day"]]
fes = fes.sort_values(["factor_label", "campaign_type", "baseline_variant"])
fes.to_csv(SCRATCH / "factor_effect_summary.csv", index=False)
log(f"\nfactor_effect_summary.csv 저장: {len(fes)}행")

log("\n=== 요인별 Σexcess (정본만, 계정 전체 ALL 행) ===")
canon_all = fes[(fes["campaign_type"] == "ALL") & (fes["is_canonical"])]
log(canon_all[["factor_label", "baseline_variant", "sum_excess", "group_days", "sum_cost", "observed_roas"]]
    .to_string(index=False))

report_lines.append("__SPLIT3__")

# ---------------------------------------------------------------------------
# 9) label_priority_sensitivity.csv
# ---------------------------------------------------------------------------
def excess_v1_sums(labels_series, tag):
    tmp = analysis_valid.copy()
    tmp["factor_label"] = labels_series.loc[tmp.index]
    tmp["excess_row"] = np.where(tmp["factor_label"] != "평시", tmp["ad_profit"] - tmp["b_g_v1"], np.nan)
    sub = tmp[tmp["factor_label"] != "평시"]
    g = sub.groupby("factor_label")["excess_row"].sum().reindex(NON_BASELINE_LABELS)
    g.name = tag
    return g


base_labels_valid = assign_labels(analysis_valid, BASE_PRIORITY)
altA_labels = assign_labels(analysis_valid, ALT_A_PRIORITY)
altB_labels = assign_labels(analysis_valid, ALT_B_PRIORITY)

base_sum = excess_v1_sums(base_labels_valid, "base")
altA_sum = excess_v1_sums(altA_labels, "altA_휴가창_먼저")
altB_sum = excess_v1_sums(altB_labels, "altB_주말_먼저")

sens = pd.concat([base_sum, altA_sum, altB_sum], axis=1)
sens["pct_change_altA_vs_base"] = np.where(
    sens["base"].abs() > 0, (sens["altA_휴가창_먼저"] - sens["base"]) / sens["base"].abs() * 100, np.nan
)
sens["pct_change_altB_vs_base"] = np.where(
    sens["base"].abs() > 0, (sens["altB_주말_먼저"] - sens["base"]) / sens["base"].abs() * 100, np.nan
)
sens = sens.reset_index().rename(columns={"index": "factor_label"})
sens.to_csv(SCRATCH / "label_priority_sensitivity.csv", index=False)
log(f"\nlabel_priority_sensitivity.csv 저장: {len(sens)}행")
log(sens.to_string(index=False))

report_lines.append("__SPLIT4__")

# ---------------------------------------------------------------------------
# 10) band_flip_by_factor.csv
# ---------------------------------------------------------------------------
# band4_unjudgeable(무전환·판정불가)·excluded_cost0(비용0)은 "손익 순위"가 아니라 "판정 가능성"
# 범주라 band1~3과 같은 축에 놓고 우열을 매기지 않는다(rank=None → direction은 "판정가능성_변화"로
# 별도 표기, 억지로 더 낫다/나쁘다로 단정하지 않는다).
BAND_RANK = {"band1": 1, "band2": 2, "band3": 3, "band4_unjudgeable": None, "excluded_cost0": None}

flip_rows = []
for g in all_groups:
    bt = band_total_idx.loc[g, "band"] if g in band_total_idx.index else None
    bb = band_baseline_only_df.loc[g, "band"] if g in band_baseline_only_df.index else "no_baseline_rows"
    if bt is None or bb == "no_baseline_rows":
        continue
    if bt == bb:
        continue
    # 지배 요인 = |excess_v1| 최대인 라벨 (baseline_unavailable 그룹은 애초 여기 안 걸림 — bb가 no_baseline_rows)
    exc_row = {lab: excess_v1_by_group_label.loc[g, lab] if g in excess_v1_by_group_label.index else np.nan
               for lab in NON_BASELINE_LABELS}
    exc_series = pd.Series(exc_row).dropna()
    if len(exc_series):
        dominant_label = exc_series.abs().idxmax()
        dominant_excess = exc_series[dominant_label]
    else:
        dominant_label, dominant_excess = "", np.nan
    rank_t = BAND_RANK.get(bt)
    rank_b = BAND_RANK.get(bb)
    direction = ""
    if rank_t is not None and rank_b is not None:
        if rank_t < rank_b:
            direction = "band_total이_더_우수(요인이_없으면_더_나쁨)"
        elif rank_t > rank_b:
            direction = "band_total이_더_열등(요인이_없으면_더_좋음)"
    else:
        direction = "판정가능성_변화(band4_unjudgeable/excluded_cost0 관련 — 우열 비교 대상 아님)"
    info = amm_idx.loc[g] if g in amm_idx.index else None
    flip_rows.append({
        "adgroup_id": g,
        "adgroup_name": info.get("adgroup_name") if info is not None else "",
        "campaign_type": info.get("campaign_type") if info is not None else "",
        "band_total": bt,
        "band_baseline_only": bb,
        "direction": direction,
        "dominant_label": dominant_label,
        "dominant_excess": round(dominant_excess, 2) if pd.notna(dominant_excess) else "",
        "cost_total": int(cost_total_g.get(g, 0)),
        "conv_amt_total": int(conv_amt_total_g.get(g, 0)),
    })

band_flip = pd.DataFrame(flip_rows).sort_values("dominant_excess", key=lambda s: pd.to_numeric(s, errors="coerce").abs(), ascending=False)
band_flip.to_csv(SCRATCH / "band_flip_by_factor.csv", index=False)
log(f"\nband_flip_by_factor.csv 저장: {len(band_flip)}행 (band_total ≠ band_baseline_only)")
log(band_flip.head(10).to_string(index=False))

report_lines.append("__SPLIT5__")

# ---------------------------------------------------------------------------
# 11) weekday_effect.csv (평시 행만)
# ---------------------------------------------------------------------------
DOW_NAME = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
base_valid = analysis_valid[analysis_valid["factor_label"] == "평시"]

wk_rows = []
for by_type in [True, False]:
    group_cols = ["dow", "campaign_type"] if by_type else ["dow"]
    g = base_valid.groupby(group_cols).agg(
        n_group_days=("ad_profit", "size"),
        mean_ad_profit=("ad_profit", "mean"),
        sum_cost=("cost", "sum"),
        sum_conv_amt=("conv_amt", "sum"),
    ).reset_index()
    if not by_type:
        g["campaign_type"] = "ALL"
    wk_rows.append(g)

weekday_effect = pd.concat(wk_rows, ignore_index=True)
weekday_effect["dow_name"] = weekday_effect["dow"].map(DOW_NAME)
weekday_effect["roas"] = weekday_effect["sum_conv_amt"] / weekday_effect["sum_cost"]
weekday_effect = weekday_effect[["dow", "dow_name", "campaign_type", "n_group_days",
                                  "mean_ad_profit", "sum_cost", "sum_conv_amt", "roas"]]
weekday_effect = weekday_effect.sort_values(["campaign_type", "dow"])
weekday_effect.to_csv(SCRATCH / "weekday_effect.csv", index=False)
log(f"\nweekday_effect.csv 저장: {len(weekday_effect)}행")
log(weekday_effect[weekday_effect["campaign_type"] == "ALL"].to_string(index=False))

report_lines.append("__SPLIT6__")

# ---------------------------------------------------------------------------
# 12) product_bep_recheck.csv (METHOD §4)
# ---------------------------------------------------------------------------
bep_map = adgroup_bep.set_index("adgroup_id")["bep_g"].to_dict()
bep_pc_map = adgroup_bep.set_index("adgroup_id")["product_count"].to_dict()
campaign_id_map = analysis.groupby("adgroup_id")["campaign_id"].first().to_dict()

# 그룹 총계는 mature_grp(=band_group_total과 같은 모집단, mature & 전 라벨)로 계산 — band_account와
# 직접 비교하려면 같은 모집단이어야 하므로 band_total과 동일하게 analysis(=mature & !data_gap) 전체 사용.
_acc, _by_type, _by_camp, _by_grp = aov_lookup_table(analysis)
pbr_rows = []
for g in all_groups:
    bep_g = bep_map.get(g)
    pc = bep_pc_map.get(g)
    cost = int(cost_total_g.get(g, 0))
    conv_amt = int(conv_amt_total_g.get(g, 0))
    conv_cnt = int(analysis[analysis["adgroup_id"] == g]["conv_cnt"].sum())
    band_account = band_total_idx.loc[g, "band"] if g in band_total_idx.index else ""
    if bep_g is None or pd.isna(bep_g):
        pbr_rows.append({
            "adgroup_id": g, "campaign_type": amm_idx.loc[g, "campaign_type"] if g in amm_idx.index else "",
            "cost": cost, "conv_amt": conv_amt, "bep_g": "", "product_count": "",
            "ad_profit_account": round(conv_amt / BEP_ROAS - cost, 2),
            "ad_profit_prod": "", "band_account": band_account, "band_prod": "not_judged_no_bep",
            "diverges": False,
        })
        continue
    bep_g_120 = round(bep_g * 1.2, 4)
    # AOV: analysis(mature 전체) 기준 4계층 폴백 재사용(band_group_total과 동일 모집단으로 일치시킴)
    aov, layer = get_aov(g, campaign_id_map.get(g),
                          amm_idx.loc[g, "campaign_type"] if g in amm_idx.index else None,
                          _acc, _by_type, _by_camp, _by_grp)
    band_prod, roas_prod = band_judge(cost, conv_cnt, conv_amt, aov, bep_roas=bep_g, bep_roas_120=bep_g_120)
    ad_profit_prod = conv_amt / bep_g - cost
    diverges = (band_prod != band_account)
    pbr_rows.append({
        "adgroup_id": g, "campaign_type": amm_idx.loc[g, "campaign_type"] if g in amm_idx.index else "",
        "cost": cost, "conv_amt": conv_amt, "bep_g": bep_g, "product_count": int(pc) if pd.notna(pc) else "",
        "ad_profit_account": round(conv_amt / BEP_ROAS - cost, 2),
        "ad_profit_prod": round(ad_profit_prod, 2), "band_account": band_account, "band_prod": band_prod,
        "diverges": diverges,
    })

product_bep_recheck = pd.DataFrame(pbr_rows)
product_bep_recheck.to_csv(SCRATCH / "product_bep_recheck.csv", index=False)
n_bep_avail = int((product_bep_recheck["bep_g"] != "").sum())
n_diverge = int(product_bep_recheck["diverges"].sum())
log(f"\nproduct_bep_recheck.csv 저장: {len(product_bep_recheck)}행 "
    f"(상품 BEP 확보 {n_bep_avail}그룹, 계정 BEP와 갈리는 그룹 {n_diverge}개)")
log(product_bep_recheck[product_bep_recheck["diverges"]].head(10).to_string(index=False))

report_lines.append("__SPLIT7__")

# ---------------------------------------------------------------------------
# 13) holdout_gate.csv (METHOD §7-5) — 탐색274일/검증91일, mature 컷(2026-08-09) 종료 앵커
# ---------------------------------------------------------------------------
VALIDATE_END = MATURE_CUTOFF
VALIDATE_START = VALIDATE_END - pd.Timedelta(days=90)
EXPLORE_END = VALIDATE_START - pd.Timedelta(days=1)
EXPLORE_START = EXPLORE_END - pd.Timedelta(days=273)

log(f"\n=== 홀드아웃 창 ===")
log(f"탐색창: {EXPLORE_START.date()} ~ {EXPLORE_END.date()} ({(EXPLORE_END-EXPLORE_START).days+1}일)")
log(f"검증창: {VALIDATE_START.date()} ~ {VALIDATE_END.date()} ({(VALIDATE_END-VALIDATE_START).days+1}일)")
log(f"창 밖(더 오래된 구간, 게이트 미적용): {analysis['ad_date'].min().date()} ~ {(EXPLORE_START-pd.Timedelta(days=1)).date()}")

GATE_AAxis = ["주말", "공휴일", "연휴"]          # 스펙대로 게이트 적용 축
NO_GATE_AXIS = ["휴가창", "F1a", "F1b", "F2", "F3"]  # 표본 편재로 게이트 미적용

hg_rows = []
for lab in NON_BASELINE_LABELS:
    sub = analysis_valid[analysis_valid["factor_label"] == lab]
    exp = sub[(sub["ad_date"] >= EXPLORE_START) & (sub["ad_date"] <= EXPLORE_END)]
    val = sub[(sub["ad_date"] >= VALIDATE_START) & (sub["ad_date"] <= VALIDATE_END)]
    exp_excess = exp["excess_v1_row"].sum()
    val_excess = val["excess_v1_row"].sum()
    n_exp, n_val = len(exp), len(val)
    if lab in GATE_AAxis:
        if n_exp == 0 or n_val == 0:
            gate = "판정불능(표본0)"
        else:
            same_sign = (np.sign(exp_excess) == np.sign(val_excess)) and (exp_excess != 0)
            gate = "통과(방향재현)" if same_sign else "불통과(방향안맞음)"
    else:
        gate = "게이트 미적용(표본 편재)"
    hg_rows.append({
        "factor_label": lab, "gate_applicable": lab in GATE_AAxis, "gate_result": gate,
        "explore_n": n_exp, "explore_sum_excess_v1": round(exp_excess, 2) if n_exp else "",
        "validate_n": n_val, "validate_sum_excess_v1": round(val_excess, 2) if n_val else "",
        "validate_share_of_total": round(n_val / (n_exp + n_val), 3) if (n_exp + n_val) else "",
    })

holdout_gate = pd.DataFrame(hg_rows)
holdout_gate.to_csv(SCRATCH / "holdout_gate.csv", index=False)
log(f"\nholdout_gate.csv 저장: {len(holdout_gate)}행")
log(holdout_gate.to_string(index=False))

report_lines.append("__SPLIT8__")

with open(SCRATCH / "_report_dump.txt", "w") as f:
    f.write("\n".join(report_lines))
log("\n\n=== 완료 ===")
