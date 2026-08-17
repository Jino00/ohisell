#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_rank_L2L3.py — S1 순위(avg_rank) 축 1년 재검증, L2(키워드×일)·L3(검색어×일) 층 + 마스킹 정량화.

읽기 전용 산출물. prod DB 쓰기 0건, 네이버 API 호출 0건, backend/·alembic/ 수정 0건.
재실행 가능(D0=2026-08-17 상수 고정, 랜덤/현재시각 미사용).

입력(raw-dir, 사전에 scripts/analysis/64_rank_axis/sql/10~12_*.sql로 prod DB에서 읽기 전용 pull):
  L2_raw.csv, L3_raw.csv, change_log_raw.csv, adgroup_product_raw.csv,
  counts_summary.csv, change_log_by_action.csv, lever_grain_raw.txt

L1 소스(다른 에이전트 소유 — 읽기만 함): REPO/docs/references/data/63_band_decomposition/panel_labeled.csv.gz

사용:
  python3 analyze_rank_L2L3.py --raw-dir <raw pull 디렉터리> [--out-dir <출력 디렉터리>]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 상수 — METHOD_rank_axis.md §1·§3, ref 63 build_panel.py와 동일(재발명 0)
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[3]
D0 = pd.Timestamp("2026-08-17")
MATURE_CUTOFF = pd.Timestamp("2026-08-09")  # D0 - 8
DATA_GAP_START = pd.Timestamp("2026-03-02")
DATA_GAP_END = pd.Timestamp("2026-03-29")
BEP_ROAS = 1.711
BEP_ROAS_120 = round(BEP_ROAS * 1.2, 4)  # 2.0532 (미사용이지만 §1 표 그대로 보관)

BIN9_EDGES = [1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 7, np.inf]
BIN9_LABELS = ["[1,1.5)", "[1.5,2)", "[2,2.5)", "[2.5,3)", "[3,3.5)", "[3.5,4)", "[4,5)", "[5,7)", "[7,inf)"]

BIN3_EDGES = [1, 2.5, 4, np.inf]
BIN3_LABELS = ["<2.5", "[2.5,4)", ">=4"]

L1_PANEL_PATH = REPO / "docs/references/data/63_band_decomposition/panel_labeled.csv.gz"


def bin9(avg_rank: pd.Series) -> pd.Series:
    return pd.cut(avg_rank, bins=BIN9_EDGES, labels=BIN9_LABELS, right=False, include_lowest=True)


def bin3(avg_rank: pd.Series, shift: float = 0.0) -> pd.Series:
    edges = [1, 2.5 + shift, 4 + shift, np.inf]
    return pd.cut(avg_rank, bins=edges, labels=BIN3_LABELS, right=False, include_lowest=True)


def four_value_table(df: pd.DataFrame, group_col, cost_col="cost", conv_amt_col="conv_amt",
                      ad_profit_col="ad_profit", n_col="n_rows") -> pd.DataFrame:
    """§3-2 4값 병기: Σad_profit·Σcost·ROAS·건수 + 일평균 ad_profit."""
    g = df.groupby(group_col, observed=True).agg(
        sum_ad_profit=(ad_profit_col, "sum"),
        sum_cost=(cost_col, "sum"),
        sum_conv_amt=(conv_amt_col, "sum"),
        n=(cost_col, "size"),
    ).reset_index()
    g["roas"] = np.where(g["sum_cost"] > 0, g["sum_conv_amt"] / g["sum_cost"], np.nan)
    g["ad_profit_per_row"] = np.where(g["n"] > 0, g["sum_ad_profit"] / g["n"], np.nan)
    return g


def weighted_percentile(values: np.ndarray, weights: np.ndarray, pct: float) -> float:
    """비용가중 percentile. weights 합이 0이면 단순평균 percentile로 폴백."""
    if len(values) == 0:
        return np.nan
    if weights.sum() <= 0:
        return float(np.percentile(values, pct))
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cw = np.cumsum(w)
    cutoff = pct / 100.0 * cw[-1]
    idx = np.searchsorted(cw, cutoff)
    idx = min(idx, len(v) - 1)
    return float(v[idx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", required=True, type=Path,
                     help="prod DB에서 읽기 전용 pull한 raw CSV들이 있는 디렉터리 (sql/10~12_*.sql 실행 결과)")
    ap.add_argument("--out-dir", default=REPO / "docs/references/data/64_rank_axis", type=Path)
    args = ap.parse_args()
    raw_dir: Path = args.raw_dir
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    findings = []  # 최종 보고에 쓸 미판정/발견 누적

    print("=== 0) 로드 ===")
    l2 = pd.read_csv(raw_dir / "L2_raw.csv", dtype={"campaign_id": str, "adgroup_id": str, "keyword_id": str})
    l3 = pd.read_csv(raw_dir / "L3_raw.csv", dtype={"campaign_id": str, "adgroup_id": str, "search_term": str})
    l1 = pd.read_csv(L1_PANEL_PATH, dtype={"campaign_id": str, "adgroup_id": str})
    l1["ad_date"] = pd.to_datetime(l1["ad_date"])
    l2["ad_date"] = pd.to_datetime(l2["ad_date"])
    l3["ad_date"] = pd.to_datetime(l3["ad_date"])
    print("L2 rows:", len(l2), "L3 rows:", len(l3), "L1 rows:", len(l1))

    counts_summary = pd.read_csv(raw_dir / "counts_summary.csv")
    l3_expkeyword_n = int(counts_summary.loc[counts_summary["label"] == "L3_expkeyword", "n"].iloc[0])
    l3_expkeyword_min = counts_summary.loc[counts_summary["label"] == "L3_expkeyword", "min_d"].iloc[0]
    l3_expkeyword_max = counts_summary.loc[counts_summary["label"] == "L3_expkeyword", "max_d"].iloc[0]

    # L2/L3 conv_cnt/conv_amt = 직접+간접 합산 (L1 panel_labeled의 conv_cnt/conv_amt와 동일 정의,
    # models.py 도크스트링: AD_CONVERSION 직접1/간접2 전환수·매출 / SS1 conv_purchase_*=직+간 합산)
    l2["conv_cnt"] = l2["conv_direct_cnt"] + l2["conv_indirect_cnt"]
    l2["conv_amt"] = l2["conv_direct_amt"] + l2["conv_indirect_amt"]
    l3["conv_cnt"] = l3["conv_purchase_cnt"]
    l3["conv_amt"] = l3["conv_purchase_amt"]

    # ==========================================================================
    # 단계 1 — 커버리지 (가정 금지, 먼저 잰다)
    # ==========================================================================
    print("\n=== 1) 커버리지 ===")

    def coverage_row(df, label, unit_col, group_cols):
        return {
            "layer": label,
            "rows": len(df),
            "min_date": df["ad_date"].min().date().isoformat(),
            "max_date": df["ad_date"].max().date().isoformat(),
            "distinct_dates": df["ad_date"].nunique(),
            "distinct_units": df[unit_col].nunique(),
            "distinct_groups": df[group_cols].drop_duplicates().shape[0],
            "sum_cost": int(df["cost"].sum()),
            "sum_conv_cnt": int(df["conv_cnt"].sum()),
            "rank_sum_gt0_rows": int((df["rank_sum"] > 0).sum()),
            "rank_sum_gt0_ratio": round((df["rank_sum"] > 0).mean(), 4),
            "imp_gt0_rows": int((df["imp"] > 0).sum()),
            "imp_gt0_ratio": round((df["imp"] > 0).mean(), 4),
        }

    cov_l2 = coverage_row(l2, "L2_WEB_SITE_keyword", "keyword_id", ["campaign_id", "adgroup_id"])
    cov_l3 = coverage_row(l3, "L3_SHOPPING_searchterm", "search_term", ["campaign_id", "adgroup_id"])
    l1_web = l1[l1["campaign_type"] == "WEB_SITE"]
    l1_shop = l1[l1["campaign_type"] == "SHOPPING"]
    cov_l1_web = {
        "layer": "L1_WEB_SITE_group(ref)", "rows": len(l1_web),
        "min_date": l1_web["ad_date"].min().date().isoformat(), "max_date": l1_web["ad_date"].max().date().isoformat(),
        "distinct_dates": l1_web["ad_date"].nunique(), "distinct_units": np.nan,
        "distinct_groups": l1_web[["campaign_id", "adgroup_id"]].drop_duplicates().shape[0],
        "sum_cost": int(l1_web["cost"].sum()), "sum_conv_cnt": int(l1_web["conv_cnt"].sum()),
        "rank_sum_gt0_rows": np.nan, "rank_sum_gt0_ratio": np.nan,
        "imp_gt0_rows": np.nan, "imp_gt0_ratio": np.nan,
    }
    cov_l1_shop = {
        "layer": "L1_SHOPPING_group(ref)", "rows": len(l1_shop),
        "min_date": l1_shop["ad_date"].min().date().isoformat(), "max_date": l1_shop["ad_date"].max().date().isoformat(),
        "distinct_dates": l1_shop["ad_date"].nunique(), "distinct_units": np.nan,
        "distinct_groups": l1_shop[["campaign_id", "adgroup_id"]].drop_duplicates().shape[0],
        "sum_cost": int(l1_shop["cost"].sum()), "sum_conv_cnt": int(l1_shop["conv_cnt"].sum()),
        "rank_sum_gt0_rows": np.nan, "rank_sum_gt0_ratio": np.nan,
        "imp_gt0_rows": np.nan, "imp_gt0_ratio": np.nan,
    }
    # ★코디네이터 지적(3라운드): 「원 커버리지(raw, 성숙컷 전)」와 「분석 대상(성숙컷+제외 후 실제
    # 분석에 쓰인 창)」을 같은 문자열에 섞지 않는다 — 두 열로 분리한다. raw_* 열은 여기서 그대로 두고,
    # analysis_* 열은 단계2에서 l2c/l3c/l1c가 만들어진 뒤 채워서 CSV는 그때 쓴다(아래로 이동).
    coverage_df = pd.DataFrame([cov_l2, cov_l1_web, cov_l3, cov_l1_shop])
    coverage_df = coverage_df.rename(columns={
        "rows": "raw_rows", "min_date": "raw_min_date", "max_date": "raw_max_date", "distinct_dates": "raw_distinct_dates",
    })
    coverage_df["raw_window_days_span"] = [
        (pd.Timestamp(r["raw_max_date"]) - pd.Timestamp(r["raw_min_date"])).days + 1 for _, r in coverage_df.iterrows()
    ]
    coverage_df["raw_covers_1y"] = coverage_df["raw_distinct_dates"] >= 360
    print(coverage_df[["layer", "raw_rows", "raw_min_date", "raw_max_date", "raw_distinct_dates", "raw_covers_1y"]].to_string(index=False))

    l3_window_short = cov_l3["distinct_dates"] < 360
    if l3_window_short:
        findings.append(
            f"★L3(SHOPPING 검색어)는 1년을 안 덮는다 — 원 커버리지 {cov_l3['distinct_dates']}일만 "
            f"({cov_l3['min_date']}~{cov_l3['max_date']}). SS1 전환 병합 도입 이후로 보인다. "
            f"L3 결론은 부분창 기술로만 쓰고 1년 판정에 올리지 않는다. "
            "(성숙컷 적용 후 실제 분석 대상 일수는 이보다 더 짧다 — 아래 analysis_distinct_dates 참조.)"
        )
    else:
        findings.append(f"L3(SHOPPING 검색어) 원 커버리지는 {cov_l3['distinct_dates']}일로 1년에 근접/충족한다.")
    l2_window_short = cov_l2["distinct_dates"] < 360
    findings.append(
        f"L2(WEB_SITE 키워드) 원 커버리지 = {cov_l2['distinct_dates']}일({cov_l2['min_date']}~{cov_l2['max_date']}), "
        f"L1 WEB_SITE 원 커버리지({cov_l1_web['distinct_dates']}일)와 {'동일' if cov_l2['distinct_dates']==cov_l1_web['distinct_dates'] else '불일치'}."
    )

    # ==========================================================================
    # 단계 2 — 필수 제외
    # ==========================================================================
    print("\n=== 2) 필수 제외 ===")
    excl_rows = []

    l2_imp0 = l2[l2["imp"] == 0]
    l2_imp0_costpos = l2_imp0[l2_imp0["cost"] > 0]
    excl_rows.append({"layer": "L2", "filter": "imp=0 (전체)", "n_rows": len(l2_imp0), "sum_cost": int(l2_imp0["cost"].sum())})
    excl_rows.append({"layer": "L2", "filter": "imp=0 ∧ cost>0 (이상치)", "n_rows": len(l2_imp0_costpos), "sum_cost": int(l2_imp0_costpos["cost"].sum())})

    l3_imp0 = l3[l3["imp"] == 0]
    l3_imp0_costpos = l3_imp0[l3_imp0["cost"] > 0]
    excl_rows.append({"layer": "L3", "filter": "imp=0 (전체)", "n_rows": len(l3_imp0), "sum_cost": int(l3_imp0["cost"].sum())})
    excl_rows.append({"layer": "L3", "filter": "imp=0 ∧ cost>0 (이상치)", "n_rows": len(l3_imp0_costpos), "sum_cost": int(l3_imp0_costpos["cost"].sum())})

    excl_rows.append({
        "layer": "L3", "filter": "source='expkeyword' (측정체계 부재 — 모집단에서 제외, 규모만 명시)",
        "n_rows": l3_expkeyword_n, "sum_cost": np.nan,
        "note": f"창 {l3_expkeyword_min}~{l3_expkeyword_max}",
    })

    l2_gap = l2[(l2["ad_date"] >= DATA_GAP_START) & (l2["ad_date"] <= DATA_GAP_END)]
    l3_gap = l3[(l3["ad_date"] >= DATA_GAP_START) & (l3["ad_date"] <= DATA_GAP_END)]
    excl_rows.append({"layer": "L2", "filter": "data_gap(2026-03-02~03-29)", "n_rows": len(l2_gap), "sum_cost": int(l2_gap["cost"].sum())})
    excl_rows.append({"layer": "L3", "filter": "data_gap(2026-03-02~03-29)", "n_rows": len(l3_gap), "sum_cost": int(l3_gap["cost"].sum())})

    l2_immature = l2[l2["ad_date"] > MATURE_CUTOFF]
    l3_immature = l3[l3["ad_date"] > MATURE_CUTOFF]
    excl_rows.append({"layer": "L2", "filter": f"ad_date>{MATURE_CUTOFF.date()}(미성숙)", "n_rows": len(l2_immature), "sum_cost": int(l2_immature["cost"].sum())})
    excl_rows.append({"layer": "L3", "filter": f"ad_date>{MATURE_CUTOFF.date()}(미성숙)", "n_rows": len(l3_immature), "sum_cost": int(l3_immature["cost"].sum())})

    # WEB_SITE 안에도 keyword_id=''(그룹 롤업 버킷) 행이 존재 — L2 grain 정의(keyword_id<>'')상
    # 원리적으로 포함 불가한 모집단. __backfill__과는 다른 원인(그건 sentinel, 이건 확장 롤업으로 보임).
    wskw = pd.read_csv(raw_dir / "websites_emptykw.csv").set_index("metric")["n"]
    excl_rows.append({
        "layer": "L2", "filter": "WEB_SITE keyword_id=''(그룹롤업 버킷, __backfill__ 아님 — grain 정의상 L2 포함 불가)",
        "n_rows": int(wskw["WEB_SITE_keyword_empty_nonbackfill_n"]),
        "sum_cost": int(wskw["WEB_SITE_keyword_empty_nonbackfill_sum_cost"]),
        "note": f"{int(wskw['WEB_SITE_keyword_empty_nonbackfill_distinct_adgroups'])}개 adgroup에 분포. "
                "L1(그룹 총합)엔 포함되지만 L2(키워드 grain)엔 키워드 정체성이 없어 못 넣는다 — 단계3 검산 괴리의 주 원인.",
    })
    excl_rows.append({
        "layer": "L2", "filter": "WEB_SITE __backfill__ sentinel(제외 대상)",
        "n_rows": int(wskw["WEB_SITE_backfill_n"]), "sum_cost": int(wskw["WEB_SITE_backfill_sum_cost"]),
        "note": "이미 keyword_id<>'' 필터와 무관하게 adgroup_id<>'__backfill__'로 제외됨(위 필수제외)",
    })

    pd.DataFrame(excl_rows).to_csv(out_dir / "L2_L3_excluded_rows.csv", index=False)
    print(pd.DataFrame(excl_rows).to_string(index=False))
    findings.append(
        f"★L2 scope 구조적 제약: WEB_SITE의 naver_ad_daily에는 keyword_id=''인 그룹롤업 버킷이 "
        f"{int(wskw['WEB_SITE_keyword_empty_nonbackfill_n']):,}행(Σcost {int(wskw['WEB_SITE_keyword_empty_nonbackfill_sum_cost']):,}원, "
        f"{int(wskw['WEB_SITE_keyword_empty_nonbackfill_distinct_adgroups'])}개 adgroup) 있다 — __backfill__ sentinel과 무관한 "
        "별도 현상(아마 확장검색/미매칭 롤업). L1(그룹 총합)엔 잡히지만 L2(키워드 grain)는 정의상 못 담는다. "
        "이게 단계3 검산에서 WEB_SITE cost/conv가 L1보다 낮게(약 -25~29%) 나오는 주 원인으로 보인다(추정 아님 — 금액 규모가 정합)."
    )

    if len(l2_imp0_costpos) or len(l3_imp0_costpos):
        findings.append(
            f"★데이터 이상: imp=0인데 cost>0인 행 — L2 {len(l2_imp0_costpos)}건(Σ{int(l2_imp0_costpos['cost'].sum())}원), "
            f"L3 {len(l3_imp0_costpos)}건(Σ{int(l3_imp0_costpos['cost'].sum())}원). 추정 없이 별도 이상치로 표시만 하고 판단엔 안 씀."
        )

    # 분석 모집단 = 필수 제외 전부 적용
    def apply_exclusions(df):
        return df[(df["imp"] > 0) & (df["ad_date"] <= MATURE_CUTOFF) &
                   ~((df["ad_date"] >= DATA_GAP_START) & (df["ad_date"] <= DATA_GAP_END))].copy()

    l2c = apply_exclusions(l2)
    l3c = apply_exclusions(l3)
    l2c["avg_rank"] = l2c["rank_sum"] / l2c["imp"]
    l3c["avg_rank"] = l3c["rank_sum"] / l3c["imp"]
    l2c["ad_profit"] = l2c["conv_amt"] / BEP_ROAS - l2c["cost"]
    l3c["ad_profit"] = l3c["conv_amt"] / BEP_ROAS - l3c["cost"]

    l1c = l1[(l1["imp"] > 0) & (l1["ad_date"] <= MATURE_CUTOFF) & (~l1["data_gap"])].copy()
    l1c["avg_rank"] = l1c["rank_sum"] / l1c["imp"]
    l1c["ad_profit"] = l1c["conv_amt"] / BEP_ROAS - l1c["cost"]

    print(f"분석모집단(필수제외 후): L2 {len(l2c)}행, L3 {len(l3c)}행, L1 {len(l1c)}행")

    # ★코디네이터 지적(3라운드) 반영: L2_L3_coverage.csv의 analysis_* 열 — 실제 분석에 쓰인(성숙컷+제외
    # 적용 후) distinct_dates/min/max. raw_*(위 단계1)와 절대 같은 문자열에 섞지 않는다.
    l1_web_c_full = l1c[l1c["campaign_type"] == "WEB_SITE"]
    l1_shop_c_full = l1c[l1c["campaign_type"] == "SHOPPING"]

    def analysis_cov(df):
        return {
            "analysis_rows": len(df), "analysis_distinct_dates": df["ad_date"].nunique(),
            "analysis_min_date": df["ad_date"].min().date().isoformat(),
            "analysis_max_date": df["ad_date"].max().date().isoformat(),
        }

    analysis_map = {
        "L2_WEB_SITE_keyword": analysis_cov(l2c),
        "L1_WEB_SITE_group(ref)": analysis_cov(l1_web_c_full),
        "L3_SHOPPING_searchterm": analysis_cov(l3c),
        "L1_SHOPPING_group(ref)": analysis_cov(l1_shop_c_full),
    }
    for col in ["analysis_rows", "analysis_distinct_dates", "analysis_min_date", "analysis_max_date"]:
        coverage_df[col] = coverage_df["layer"].map(lambda lyr: analysis_map[lyr][col])
    coverage_df["analysis_window_days_span"] = [
        (pd.Timestamp(r["analysis_max_date"]) - pd.Timestamp(r["analysis_min_date"])).days + 1
        for _, r in coverage_df.iterrows()
    ]
    coverage_df.to_csv(out_dir / "L2_L3_coverage.csv", index=False)
    print(coverage_df[["layer", "raw_distinct_dates", "analysis_distinct_dates", "analysis_min_date", "analysis_max_date"]].to_string(index=False))
    findings.append(
        "★[3라운드 반영] L2_L3_coverage.csv에 raw_*(성숙컷 전 원 커버리지)와 analysis_*(성숙컷+제외 후 "
        f"실제 분석 대상) 열을 분리했다 — L2 raw {cov_l2['distinct_dates']}일 vs analysis "
        f"{analysis_map['L2_WEB_SITE_keyword']['analysis_distinct_dates']}일, "
        f"L3 raw {cov_l3['distinct_dates']}일 vs analysis "
        f"{analysis_map['L3_SHOPPING_searchterm']['analysis_distinct_dates']}일. "
        "1라운드/2라운드에서 window 라벨 문자열에 raw 일수와 analysis 날짜를 섞어 쓴 것이 원인 — "
        "실제 밴드·마스킹 계산 자체(l2c/l3c/l1c, MATURE_CUTOFF 필터 적용됨)는 처음부터 맞았고 "
        "라벨 문자열만 틀렸다(아래 검증 참조)."
    )

    # ==========================================================================
    # 단계 3 — 검산 (같은 창·같은 유형에서 L1 대조)
    # ==========================================================================
    print("\n=== 3) 검산 vs L1 ===")
    recon_rows = []

    def recon(layer_df, l1_sub, label, window_note):
        r = {
            "layer": label, "window": window_note,
            "imp_layer": int(layer_df["imp"].sum()), "imp_L1": int(l1_sub["imp"].sum()),
            "clk_layer": int(layer_df["clk"].sum()), "clk_L1": int(l1_sub["clk"].sum()),
            "cost_layer": int(layer_df["cost"].sum()), "cost_L1": int(l1_sub["cost"].sum()),
            "conv_cnt_layer": int(layer_df["conv_cnt"].sum()), "conv_cnt_L1": int(l1_sub["conv_cnt"].sum()),
            "conv_amt_layer": int(layer_df["conv_amt"].sum()), "conv_amt_L1": int(l1_sub["conv_amt"].sum()),
        }
        for k in ["imp", "clk", "cost", "conv_cnt", "conv_amt"]:
            a, b = r[f"{k}_layer"], r[f"{k}_L1"]
            r[f"{k}_diff"] = a - b
            r[f"{k}_diff_pct"] = round((a - b) / b * 100, 4) if b else np.nan
        return r

    # L2 vs L1 WEB_SITE — 필수제외 후 전체 창(공통, 실측 355일 — 아래 window 문자열은 실제 min/max/nunique로 생성)
    l1_web_c = l1_web_c_full
    l2_n_dates = l2c["ad_date"].nunique()
    recon_rows.append(recon(l2c, l1_web_c, "L2_vs_L1_WEB_SITE",
                             f"필수제외 후 전체 창(공통) {l2_n_dates}d({l2c['ad_date'].min().date()}~{l2c['ad_date'].max().date()})"))

    # L3 vs L1 SHOPPING — L3의 실제 창(실측 distinct_dates)으로 L1도 제한
    l3_min, l3_max = l3c["ad_date"].min(), l3c["ad_date"].max()
    l3_n_dates = l3c["ad_date"].nunique()
    l1_shop_c = l1c[(l1c["campaign_type"] == "SHOPPING") & (l1c["ad_date"] >= l3_min) & (l1c["ad_date"] <= l3_max)]
    recon_rows.append(recon(l3c, l1_shop_c, "L3_vs_L1_SHOPPING",
                             f"필수제외 후 L3 창으로 제한 {l3_n_dates}d({l3_min.date()}~{l3_max.date()})"))

    recon_df = pd.DataFrame(recon_rows)
    recon_df.to_csv(out_dir / "L2_L3_reconcile_vs_L1.csv", index=False)
    print(recon_df[["layer", "window", "cost_diff_pct", "conv_cnt_diff_pct", "conv_amt_diff_pct"]].to_string(index=False))

    for _, r in recon_df.iterrows():
        big = [k for k in ["imp", "clk", "cost", "conv_cnt", "conv_amt"] if abs(r.get(f"{k}_diff_pct") or 0) > 5]
        if big:
            findings.append(f"★검산 괴리 — {r['layer']}: {big} 항목이 L1 대비 ±5% 초과 (구체 값은 L2_L3_reconcile_vs_L1.csv).")
        else:
            findings.append(f"검산 — {r['layer']}: 전 항목 L1 대비 ±5% 이내로 정합.")

    # L3 괴리의 세부 원인 — L3 창 안에서도 일부 그룹-일이 통째로 빠져 있는지 확인(추정 금지, 직접 대조)
    l3_groupdays = l3c.groupby(["ad_date", "campaign_id", "adgroup_id"], observed=True).size().reset_index()[["ad_date", "campaign_id", "adgroup_id"]]
    merged_gd = l1_shop_c.merge(l3_groupdays.assign(has_l3=1), on=["ad_date", "campaign_id", "adgroup_id"], how="left")
    missing_gd = merged_gd[merged_gd["has_l3"].isna()]
    gap_detail = pd.DataFrame([{
        "l1_shopping_groupdays_in_l3_window": len(l1_shop_c),
        "l3_covered_groupdays": len(l3_groupdays),
        "l1_groupdays_missing_from_l3": len(missing_gd),
        "missing_groupdays_ratio": round(len(missing_gd) / len(l1_shop_c), 4) if len(l1_shop_c) else np.nan,
        "missing_sum_imp": int(missing_gd["imp"].sum()),
        "missing_imp_share_of_l1_total": round(missing_gd["imp"].sum() / l1_shop_c["imp"].sum(), 4) if l1_shop_c["imp"].sum() else np.nan,
        "missing_sum_cost": int(missing_gd["cost"].sum()),
        "missing_cost_share_of_l1_total": round(missing_gd["cost"].sum() / l1_shop_c["cost"].sum(), 4) if l1_shop_c["cost"].sum() else np.nan,
        "missing_distinct_adgroups": missing_gd["adgroup_id"].nunique(),
        "missing_distinct_campaigns": missing_gd["campaign_id"].nunique(),
    }])
    gap_detail.to_csv(out_dir / "L3_coverage_gap_detail.csv", index=False)
    print(gap_detail.T)

    if len(missing_gd):
        top_ag = missing_gd.groupby("adgroup_id")["imp"].sum().sort_values(ascending=False)
        findings.append(
            f"★L3 창 내부 결측(부분창과 별개의 원인): L3의 {l3_n_dates}일 창(={l3_min.date()}~{l3_max.date()}, "
            f"성숙컷 적용) 안에서도 L1 SHOPPING 그룹-일 "
            f"{len(l1_shop_c)}건 중 {len(missing_gd)}건({len(missing_gd)/len(l1_shop_c)*100:.1f}%)에 L3 상세행이 "
            f"아예 없다. 이 결측 그룹-일이 imp의 {missing_gd['imp'].sum()/l1_shop_c['imp'].sum()*100:.1f}%를 차지하지만 "
            f"cost는 {missing_gd['cost'].sum()/l1_shop_c['cost'].sum()*100:.1f}%뿐이다(저비용·고노출 트래픽). "
            f"{missing_gd['campaign_id'].nunique()}개 캠페인·{missing_gd['adgroup_id'].nunique()}개 adgroup에 몰려있다"
            f"(최다: {top_ag.index[0]} imp {int(top_ag.iloc[0]):,}) — 무작위 누락이 아니라 특정 adgroup 구조적 결측으로 보인다."
        )

    # ==========================================================================
    # 단계 4 — 하위 grain 순위×이익 재집계 (9-bin + 3-bin, 4값 병기)
    # ==========================================================================
    print("\n=== 4) 순위 밴드 재집계 ===")

    def band_tables(df, label):
        d = df.copy()
        d["bin9"] = bin9(d["avg_rank"])
        d["bin3"] = bin3(d["avg_rank"])
        t9 = four_value_table(d, "bin9")
        t9.insert(0, "layer", label)
        t9.insert(1, "variant", "base")
        t3 = four_value_table(d, "bin3")
        t3.insert(0, "layer", label)
        t3.insert(1, "variant", "base")

        # 민감도 ① bin 경계 ±0.25
        for shift, tag in [(-0.25, "shift_-0.25"), (0.25, "shift_+0.25")]:
            d[f"bin3_{tag}"] = bin3(d["avg_rank"], shift=shift)
            tt = four_value_table(d, f"bin3_{tag}")
            tt = tt.rename(columns={f"bin3_{tag}": "bin3"})
            tt.insert(0, "layer", label)
            tt.insert(1, "variant", tag)
            t3 = pd.concat([t3, tt], ignore_index=True)

        # 민감도 ③ 그룹-일 가중 vs 그룹 가중(그룹별 먼저 평균 후 구간 집계)
        # "그룹"= (campaign_id, adgroup_id). 그룹별 avg_rank·ad_profit 평균을 먼저 낸 뒤 3구간.
        grp = d.groupby(["campaign_id", "adgroup_id"], observed=True).agg(
            avg_rank=("avg_rank", "mean"), ad_profit=("ad_profit", "mean"),
            cost=("cost", "mean"), conv_amt=("conv_amt", "mean"),
        ).reset_index()
        grp["bin3"] = bin3(grp["avg_rank"])
        tg = four_value_table(grp, "bin3")
        tg.insert(0, "layer", label)
        tg.insert(1, "variant", "group_weighted(그룹별 평균 후 집계)")
        t3 = pd.concat([t3, tg], ignore_index=True)

        return t9, t3

    l2_t9, l2_t3 = band_tables(l2c, "L2_WEB_SITE_keyword")
    l3_t9, l3_t3 = band_tables(l3c, "L3_SHOPPING_searchterm")

    # L1 참조(내부 비교용 — L1_* 파일로는 안 쓴다, 다른 에이전트 소유)
    # ★2라운드 코디네이터 지적(P2) 반영: L1 SHOPPING을 "전체창"과 "L3와 정확히 같은 44일 공통창"
    # 둘 다 계산한다 — 전자만 쓰면 grain 차이와 window 차이가 뒤섞인다.
    l1c_web = l1c[l1c["campaign_type"] == "WEB_SITE"].copy()
    l1c_shop_full = l1c[l1c["campaign_type"] == "SHOPPING"].copy()
    l1c_web["avg_rank"] = l1c_web["rank_sum"] / l1c_web["imp"]
    l1c_shop_full["avg_rank"] = l1c_shop_full["rank_sum"] / l1c_shop_full["imp"]
    l1c_web["bin3"] = bin3(l1c_web["avg_rank"])
    l1c_shop_full["bin3"] = bin3(l1c_shop_full["avg_rank"])
    l1_web_t3 = four_value_table(l1c_web, "bin3")
    l1_shop_full_t3 = four_value_table(l1c_shop_full, "bin3")

    l1c_shop_44 = l1_shop_c.copy()  # 단계3에서 이미 L3 창(l3_min~l3_max)으로 제한해둔 L1 SHOPPING
    l1c_shop_44["avg_rank"] = l1c_shop_44["rank_sum"] / l1c_shop_44["imp"]
    l1c_shop_44["bin3"] = bin3(l1c_shop_44["avg_rank"])
    l1_shop_44_t3 = four_value_table(l1c_shop_44, "bin3")

    l2_t9.to_csv(out_dir / "L2_rank_band_by_bin.csv", index=False)
    l3_t9.to_csv(out_dir / "L3_rank_band_by_bin.csv", index=False)

    def order_by(t3, col, variant="base"):
        sub = t3[t3["variant"] == variant].set_index("bin3").reindex(BIN3_LABELS)
        return list(sub.sort_values(col, ascending=False).index)

    def roas_spread(t3, variant="base"):
        sub = t3[t3["variant"] == variant].set_index("bin3").reindex(BIN3_LABELS)
        return round(sub["roas"].max() - sub["roas"].min(), 4)

    # ★[3라운드 반영] window 라벨은 하드코딩 숫자(368/44/391) 대신 해당 df의 실측
    # distinct_dates·min·max로만 만든다 — «일수»와 «날짜»가 같은 df에서 나오므로 모순이 원리적으로 불가.
    def window_label(df, note=""):
        n = df["ad_date"].nunique()
        lo, hi = df["ad_date"].min().date(), df["ad_date"].max().date()
        return f"{n}d(={lo}~{hi}, 성숙컷 적용{', ' + note if note else ''})"

    l2_win = window_label(l2c, "L1과 동일 실측 — 확인됨")
    l3_win = window_label(l3c, "L3 자체 커버리지")
    l1_shop_full_win = window_label(l1c_shop_full, "전체창 — L3와 창이 다름, 참고용")
    l1_shop_44_win = window_label(l1c_shop_44, "L3와 정확히 같은 창(공통창 대조용)")

    l2_t3["window"] = l2_win
    l3_t3["window"] = l3_win
    l1w_ref = l1_web_t3.copy(); l1w_ref.insert(0, "layer", "L1_WEB_SITE_group(ref, 다른SA소유 아님-내부계산)")
    l1w_ref.insert(1, "variant", "base"); l1w_ref["window"] = window_label(l1c_web, "L2와 동일 실측 — 확인됨")
    l1s_full_ref = l1_shop_full_t3.copy(); l1s_full_ref.insert(0, "layer", "L1_SHOPPING_group(ref, 내부계산)")
    l1s_full_ref.insert(1, "variant", "base"); l1s_full_ref["window"] = l1_shop_full_win
    l1s_44_ref = l1_shop_44_t3.copy(); l1s_44_ref.insert(0, "layer", "L1_SHOPPING_group(ref, 내부계산, 44d_common)")
    l1s_44_ref.insert(1, "variant", "base"); l1s_44_ref["window"] = l1_shop_44_win

    # 검산: L2·L1_WEB_SITE 창이 실제로 같은지, L3·L1_SHOPPING(44d_common) 창이 실제로 같은지 단언이 아니라 확인
    assert l2c["ad_date"].nunique() == l1c_web["ad_date"].nunique(), "L2/L1_WEB_SITE 창 불일치 — 라벨의 '확인됨'이 거짓이 됨"
    assert l3c["ad_date"].nunique() == l1c_shop_44["ad_date"].nunique(), "L3/L1_SHOPPING(44d_common) 창 불일치"
    print(f"[검증] L2 window={l2_win} | L1_WEB_SITE window={l1w_ref['window'].iloc[0]}")
    print(f"[검증] L3 window={l3_win} | L1_SHOPPING(common) window={l1_shop_44_win}")
    print(f"[검증] L1_SHOPPING(전체) window={l1_shop_full_win}")

    # ★출력 파일명은 L2_L3_band3.csv이므로 L1_* 소유권 규칙에 안 걸린다. layer 컬럼값으로만 L1 참조를 구분.
    band3_out = pd.concat([l2_t3, l3_t3, l1w_ref, l1s_full_ref, l1s_44_ref], ignore_index=True)
    band3_out.to_csv(out_dir / "L2_L3_band3.csv", index=False)

    # ★2라운드 코디네이터 지적(P1) 반영: 정본 순서 축은 roas(=효율), ad_profit_per_row는 참고(행당
    # 소진강도에 비례 — 항등식 ad_profit/cost = roas/1.711 - 1로 roas가 이미 효율축임이 확인됨).
    order_summary_rows = []
    for name, t3, extra_window in [
        ("L2_WEB_SITE_keyword", l2_t3, None), ("L3_SHOPPING_searchterm", l3_t3, None),
        ("L1_WEB_SITE_group(ref)", l1w_ref, None),
        ("L1_SHOPPING_group(ref,full_analysis_window)", l1s_full_ref, None),
        ("L1_SHOPPING_group(ref,common_window=L3)", l1s_44_ref, None),
    ]:
        order_summary_rows.append({
            "layer": name, "window": t3["window"].iloc[0],
            "order_by_roas(효율축·정본)": " > ".join(order_by(t3, "roas")),
            "roas_spread(max-min)": roas_spread(t3),
            "order_by_ad_profit_per_row(참고·행당소진강도에 비례)": " > ".join(order_by(t3, "ad_profit_per_row")),
        })
    order_summary = pd.DataFrame(order_summary_rows)
    order_summary.to_csv(out_dir / "L2_L3_band3_order_summary.csv", index=False)
    print(order_summary.to_string(index=False))

    spread_l2 = roas_spread(l2_t3); spread_l1w = roas_spread(l1w_ref)
    spread_l3 = roas_spread(l3_t3); spread_l1s_full = roas_spread(l1s_full_ref); spread_l1s_44 = roas_spread(l1s_44_ref)

    findings.append(
        f"★[2라운드 P1 반영] 정본 축을 roas(효율)로 바꾸면 — L1 WEB_SITE({l1w_ref['window'].iloc[0]}) "
        f"roas_spread={spread_l1w} → L2(키워드, {l2_win}) roas_spread={spread_l2}: 둘 다 순위가 뚜렷한 "
        f"효율축이고 순서(roas 내림차순)도 "
        f"{'동일' if order_by(l1w_ref,'roas')==order_by(l2_t3,'roas') else '다름'} — WEB_SITE는 키워드 grain에서도 "
        f"'순위가 좋을수록 효율이 높다'는 방향이 재현된다. "
        f"L1 SHOPPING({l1_shop_full_win}) roas_spread={spread_l1s_full} vs L3(검색어, {l3_win}) roas_spread={spread_l3} — "
        f"둘 다 spread가 WEB_SITE의 1/10 수준(노이즈에 가까움). "
        f"ad_profit_per_row 기준 순서(구판)는 참고용으로만 L2_L3_band3_order_summary.csv에 남긴다."
    )

    # ★[2라운드 P2 반영] 창 통제 판정: L1을 L3와 정확히 같은 창(실측 distinct_dates)으로 자르면 무슨 일이 일어나는가.
    order_l1s_full = order_by(l1s_full_ref, "roas")
    order_l1s_44 = order_by(l1s_44_ref, "roas")
    order_l3_roas = order_by(l3_t3, "roas")
    window_effect_confirmed = (spread_l1s_44 > spread_l1s_full * 3)  # 그룹 단위에서만 봐도 창을 좁히면 스프레드가 급변하는지
    findings.append(
        f"★[2라운드 P2 판정] L1 SHOPPING을 L3와 동일한 창({l1_shop_44_win})으로 제한하면"
        f"(그룹 grain 그대로, grain 안 바꿈): "
        f"roas_spread {spread_l1s_full}({l1_shop_full_win}) → {spread_l1s_44}({l1_shop_44_win}, "
        f"{spread_l1s_44/spread_l1s_full:.1f}배), "
        f"순서 {' > '.join(order_l1s_full)}({l1_shop_full_win}) → {' > '.join(order_l1s_44)}({l1_shop_44_win}). "
        f"즉 **grain을 전혀 안 바꿔도(그룹 단위 그대로) 창만 좁히면 순서와 스프레드가 이미 크게 바뀐다** — "
        f"휴가창(7/20~8/15)·Z폴드8 출시가 이 창에 통째로 들어있다(ref 63 기지식). **창 효과는 확인됨.** "
        f"단 L3(검색어, 같은 창 {l3_win}) 순서는 {' > '.join(order_l3_roas)}로 L1-공통창({' > '.join(order_l1s_44)})과도 "
        f"또 다르다 — L1-공통창은 spread {spread_l1s_44}(뚜렷)인데 L3는 spread {spread_l3}(노이즈 수준)이라, "
        f"'grain을 내려가면 결론이 또 바뀐다'는 주장은 신호 대 잡음비가 낮아 **판정불능**으로 남긴다"
        f"(grain 효과가 있는지 없는지, 이 데이터로는 창 효과와 분리해 말할 수 없다). "
        f"conv_amt −19% 저계상(단계3) 보정 방향: L3 conv_amt를 올리면 ad_profit·roas가 전 구간 상향 이동하므로 "
        f"'전 구간 적자'라는 진술은 최소 일부 완화될 방향이다 — 어느 bin에 얼마나 몰려있는지는 미상이라 크기는 보정 못함."
    )

    # ==========================================================================
    # 단계 5 — 마스킹 정량화
    # ==========================================================================
    print("\n=== 5) 마스킹 정량화 ===")

    def group_day_mask_stats(unit_df, unit_col):
        """unit_df: ad_date, campaign_id, adgroup_id, <unit_col>, imp, cost, rank_sum, avg_rank, conv_amt, ad_profit."""
        keys = ["ad_date", "campaign_id", "adgroup_id"]
        g = unit_df.groupby(keys, observed=True)

        group_agg = g.agg(
            group_imp=("imp", "sum"), group_rank_sum=("rank_sum", "sum"),
            group_cost=("cost", "sum"), group_conv_amt=("conv_amt", "sum"),
            unit_count=(unit_col, "nunique"),
        ).reset_index()
        group_agg["group_avg_rank"] = group_agg["group_rank_sum"] / group_agg["group_imp"]
        group_agg["group_ad_profit"] = group_agg["group_conv_amt"] / BEP_ROAS - group_agg["group_cost"]

        # head unit = cost 1위 (동률이면 rank_sum 큰 쪽 우선 — 결정론적)
        sorted_df = unit_df.sort_values(["cost", "rank_sum"], ascending=[False, False])
        head = sorted_df.groupby(keys, observed=True).first().reset_index()
        head = head[keys + ["avg_rank"]].rename(columns={"avg_rank": "head_unit_rank"})

        merged = group_agg.merge(head, on=keys, how="left")
        merged["head_gap"] = merged["head_unit_rank"] - merged["group_avg_rank"]

        # 비용가중 p90-p10 (unit_count>=2인 그룹-일만 의미 있음, 1개면 spread=0 자동)
        def spread(sub):
            return weighted_percentile(sub["avg_rank"].values, sub["cost"].values, 90) - \
                   weighted_percentile(sub["avg_rank"].values, sub["cost"].values, 10)

        spreads = unit_df.groupby(keys, observed=True).apply(spread, include_groups=False).reset_index(name="rank_spread")
        merged = merged.merge(spreads, on=keys, how="left")
        return merged

    # ★2라운드 P3 반영: WEB_SITE(L2, 368일)와 SHOPPING(L3, 44일)은 서로 다른 창이다 — window 컬럼을
    # 명시하지 않고 두 유형을 나란히 놓으면 "SHOPPING이 순손실"이 창 효과와 뒤섞여 오독된다.
    # ★[3라운드 반영] 하드코딩 숫자(368/44) 대신 l2c/l3c 실측 distinct_dates로 라벨 생성(§4의 window_label과 동일 원칙)
    l2_window_label = f"{l2c['ad_date'].nunique()}d({l2c['ad_date'].min().date()}~{l2c['ad_date'].max().date()}, 성숙컷 적용)"
    l3_window_label = f"{l3c['ad_date'].nunique()}d({l3c['ad_date'].min().date()}~{l3c['ad_date'].max().date()}, 성숙컷 적용, L3 자체 커버리지 — 휴가창·Z폴드8 출시 포함)"

    l2_mask = group_day_mask_stats(l2c, "keyword_id")
    l2_mask["campaign_type"] = "WEB_SITE"
    l2_mask["window"] = l2_window_label
    l3_mask = group_day_mask_stats(l3c, "search_term")
    l3_mask["campaign_type"] = "SHOPPING"
    l3_mask["window"] = l3_window_label

    mask_all = pd.concat([l2_mask, l3_mask], ignore_index=True)
    mask_all["unit_label"] = np.where(mask_all["unit_count"] <= 1, "단일단위(가림 구조적 불가)", "복수단위(가림 가능)")

    # 분포 표 — window 컬럼 포함(WEB_SITE·SHOPPING을 나란히 놓되 창이 다름을 항상 같이 보이게)
    dist_rows = []
    for (ctype, ulabel), sub in mask_all.groupby(["campaign_type", "unit_label"], observed=True):
        dist_rows.append({
            "campaign_type": ctype, "unit_label": ulabel, "window": sub["window"].iloc[0], "n_group_days": len(sub),
            "head_gap_mean": round(sub["head_gap"].mean(), 4), "head_gap_median": round(sub["head_gap"].median(), 4),
            "head_gap_p10": round(sub["head_gap"].quantile(0.10), 4), "head_gap_p90": round(sub["head_gap"].quantile(0.90), 4),
            "head_gap_positive_ratio": round((sub["head_gap"] > 0).mean(), 4),
            "rank_spread_mean": round(sub["rank_spread"].mean(), 4), "rank_spread_median": round(sub["rank_spread"].median(), 4),
            "sum_cost": int(sub["group_cost"].sum()), "sum_ad_profit": int(sub["group_ad_profit"].sum()),
        })
    dist_df = pd.DataFrame(dist_rows)
    dist_df.to_csv(out_dir / "mask_head_gap_distribution.csv", index=False)
    print(dist_df.to_string(index=False))

    # 핵심 표: 그룹 avg_rank in [2.5,4) 인데 head_unit_rank>=4 — 유형별 + 단일/복수단위 분리 + 레버 여부 + window
    # ★창이 다른 WEB_SITE(368d)·SHOPPING(44d) 숫자는 나란히 "비교"하지 않는다 — 각자 자기 창 안에서만 읽는다.
    lever_map = {"WEB_SITE": "있음(키워드 개별입찰)", "SHOPPING": "없음(검색어 단위 레버 부재 — 소재/그룹 단위만)"}
    zone_rows = []
    for ctype, sub in mask_all.groupby("campaign_type", observed=True):
        in_zone = sub[(sub["group_avg_rank"] >= 2.5) & (sub["group_avg_rank"] < 4)]
        for ulabel, zsub in in_zone.groupby("unit_label", observed=True):
            hit = zsub[zsub["head_unit_rank"] >= 4]
            zone_rows.append({
                "campaign_type": ctype, "unit_label": ulabel, "window": zsub["window"].iloc[0],
                "lever_at_unit_grain": lever_map[ctype],
                "zone_group_days": len(zsub), "hit_group_days": len(hit),
                "hit_ratio": round(len(hit) / len(zsub), 4) if len(zsub) else np.nan,
                "hit_sum_cost": int(hit["group_cost"].sum()), "hit_sum_ad_profit": int(hit["group_ad_profit"].sum()),
                "zone_sum_cost": int(zsub["group_cost"].sum()), "zone_sum_ad_profit": int(zsub["group_ad_profit"].sum()),
            })
    zone_df = pd.DataFrame(zone_rows)
    zone_df.to_csv(out_dir / "mask_hypothesis_zone_breakdown.csv", index=False)
    print(zone_df.to_string(index=False))
    findings.append(
        f"★[2라운드 P3 반영] mask_* CSV에 window 컬럼 추가 — WEB_SITE는 {l2_window_label}, "
        f"SHOPPING은 {l3_window_label}. 두 숫자(WEB_SITE +342,341원 vs SHOPPING −468,798원)는 "
        "창이 달라 나란히 대조하지 않는다 — SHOPPING 쪽은 §4에서 확인된 '창 효과'(휴가창·Z폴드8)가 "
        "섞여 있을 수 있어 그 부호(순손실)를 grain 탓으로만 돌리지 않는다."
    )

    for _, r in zone_df.iterrows():
        if r["unit_label"].startswith("복수"):
            findings.append(
                f"마스킹 — {r['campaign_type']}({r['unit_label']}): 그룹 [2.5,4)인데 머리 단위≥4인 "
                f"그룹-일 {r['hit_group_days']}/{r['zone_group_days']}건({r['hit_ratio']*100:.2f}%), "
                f"Σcost {r['hit_sum_cost']:,}원 / Σad_profit {r['hit_sum_ad_profit']:,}원. 레버: {r['lever_at_unit_grain']}"
            )

    # ==========================================================================
    # 단계 5-보강 — 레버 grain 실측 (코디네이터 추가 지시)
    # ==========================================================================
    print("\n=== 5-보강) 레버 grain 실측 ===")
    # sql/13_lever_summary.sql 결과(metric,n 반복 헤더 — 첫 줄만 남기고 정리한 raw_dir/lever_summary.csv)
    lv = pd.read_csv(raw_dir / "lever_summary.csv").set_index("metric")["n"]

    lever_summary = pd.DataFrame([
        {
            "campaign_type": "WEB_SITE", "unit_grain": "keyword(키워드)",
            "unit_grain_rows_nonempty_in_ad_daily": int(lv["ad_daily_WEB_SITE_keyword_nonempty"]),
            "keyword_entities_in_naver_entity": int(lv["entity_keyword_WEB_SITE"]),
            "distinct_campaigns_with_keyword_entity": int(lv["entity_keyword_WEB_SITE_distinct_campaigns"]),
            "lever_exists_at_unit_grain": "있음 — 키워드별 개별 bid_amt(naver_entity.bid_amt)",
            "source_note": "sql/13_lever_summary.sql 실측(2026-08-17)",
        },
        {
            "campaign_type": "SHOPPING", "unit_grain": "search_term(검색어)",
            "unit_grain_rows_nonempty_in_ad_daily": int(lv["ad_daily_SHOPPING_keyword_nonempty"]),
            "keyword_entities_in_naver_entity": int(lv["entity_keyword_SHOPPING"]),
            "distinct_campaigns_with_keyword_entity": 0,
            "lever_exists_at_unit_grain": (
                f"없음 — naver_ad_daily SHOPPING {int(lv['ad_daily_SHOPPING_keyword_empty']):,}행 전부 "
                "keyword_id=''(sentinel), naver_entity keyword행도 SHOPPING 0건(설계상 동기화 제외, "
                "models.py NaverEntity 도크스트링: 'SHOPPING은 keyword 행 동기화 제외')"
            ),
            "source_note": "레버는 (adgroup,mall_product_id)=소재 grain에만 존재 — 아래 별도 행",
        },
        {
            "campaign_type": "SHOPPING", "unit_grain": "mall_product_id(소재/상품 — search_term보다 상위)",
            "unit_grain_rows_nonempty_in_ad_daily": np.nan,
            "keyword_entities_in_naver_entity": np.nan,
            "distinct_campaigns_with_keyword_entity": np.nan,
            "lever_exists_at_unit_grain": (
                f"부분 — naver_adgroup_product {int(lv['adgroup_product_total']):,}행/"
                f"{int(lv['adgroup_product_distinct_adgroups'])}그룹/{int(lv['adgroup_product_distinct_campaigns'])}캠페인 중 "
                f"use_group_bid_amt=false(소재 개별입찰) {int(lv['use_group_bid_amt_false_individual']):,}행/"
                f"{int(lv['use_group_bid_amt_false_individual_distinct_adgroups'])}그룹, "
                f"ad_user_lock=true {int(lv['ad_user_lock_true']):,}행. 단 이 레버는 그 소재가 노출된 "
                "'모든 검색어'에 동일 적용 — 검색어별 조준 불가"
            ),
            "source_note": "sql/13_lever_summary.sql 실측(2026-08-17) — 문서(shopping-avg-rank-masks-head-keyword) 588/317과 일치",
        },
    ])
    lever_summary.to_csv(out_dir / "discriminator_lever_grain.csv", index=False)
    print(lever_summary[["campaign_type", "unit_grain", "lever_exists_at_unit_grain"]].to_string(index=False))

    findings.append(
        f"★레버 grain 실측(문서 주장과 대조, 추정 아님): WEB_SITE는 naver_ad_daily {int(lv['ad_daily_WEB_SITE_keyword_nonempty']):,}행 "
        f"100%가 keyword_id<>''(개별 키워드 입찰 레버 실재, naver_entity에 {int(lv['entity_keyword_WEB_SITE']):,}개 키워드 엔티티). "
        f"SHOPPING은 naver_ad_daily {int(lv['ad_daily_SHOPPING_keyword_empty']):,}행 전부 keyword_id=''(sentinel) — "
        "검색어 단위 레버가 데이터상 아예 없다. naver_adgroup_product의 소재별 개별입찰"
        f"({int(lv['use_group_bid_amt_false_individual'])}/{int(lv['adgroup_product_total'])})·"
        f"userLock({int(lv['ad_user_lock_true'])})은 '상품' grain 레버이지 '검색어' grain 레버가 아니다 — "
        "그 상품이 노출되는 모든 검색어(L3 단위)에 동일하게 적용된다. 즉 L3에서 순위를 알아도 검색어별로는 조준 불가."
    )

    # ==========================================================================
    # 단계 6 — 판별자 커버리지 (상한만, 실분해 안 함)
    # ==========================================================================
    print("\n=== 6) 판별자 커버리지 ===")
    cl = pd.read_csv(raw_dir / "change_log_raw.csv")
    cl["changed_at"] = pd.to_datetime(cl["changed_at"], format="mixed")
    our_actions = cl[(cl["dry_run"] == 0) & (~cl["action"].str.startswith("external_"))]
    external_bid = cl[cl["action"] == "external_bid_change"]
    external_all = cl[cl["action"].str.startswith("external_")]

    def disc_row(sub, label):
        return {
            "label": label, "n_rows": len(sub),
            "min_ts": sub["changed_at"].min() if len(sub) else np.nan,
            "max_ts": sub["changed_at"].max() if len(sub) else np.nan,
            "distinct_campaigns": sub["campaign_id"].nunique(),
            "distinct_entities": sub["entity_id"].nunique(),
            "dry_run_true_n": int((sub["dry_run"] == 1).sum()) if "dry_run" in sub else np.nan,
            "occurred_at_filled_ratio": round(sub["occurred_at"].notna().mean(), 4) if len(sub) else np.nan,
        }

    disc_df = pd.DataFrame([
        disc_row(our_actions, "우리_실집행(dry_run=0, action not external_*)"),
        disc_row(external_bid, "외부_관측_bid(action=external_bid_change)"),
        disc_row(external_all, "외부_관측_전체(action LIKE external_*)"),
        disc_row(cl, "change_log_전체"),
    ])
    disc_df.to_csv(out_dir / "discriminator_coverage.csv", index=False)
    print(disc_df.to_string(index=False))

    # 상한 추정: 판별자가 덮는 그룹(캠페인) 수 / 순위축 분석 모집단의 그룹 수
    l1_web_groups = l1_web_c[["campaign_id", "adgroup_id"]].drop_duplicates().shape[0]
    l1_shop_groups = l1_shop_c[["campaign_id", "adgroup_id"]].drop_duplicates().shape[0] if len(l1_shop_c) else 0
    findings.append(
        f"판별자 커버리지(상한, 실분해 아님) — naver_change_log 전체 {len(cl)}행"
        f"(우리 실집행 {len(our_actions)}행·외부관측 bid {len(external_bid)}행·외부관측 전체 {len(external_all)}행), "
        f"창 {cl['changed_at'].min().date()}~{cl['changed_at'].max().date()}(약 {(cl['changed_at'].max()-cl['changed_at'].min()).days}일). "
        f"이 창은 순위축 분석 모집단(최대 {MATURE_CUTOFF.date()}까지, 실측 최장 {l2_n_dates}일 — {l2_win})의 "
        "극히 일부만 덮는다 — "
        f"판별자가 있는 사건의 비율은 여기서 계산하지 않는다(사건 정의는 B-5 이월, METHOD §6)."
    )

    # ==========================================================================
    # 미판정 목록 + 리포트
    # ==========================================================================
    print("\n=== 미판정 목록 ===")
    unresolved = [
        f"L3(SHOPPING 검색어) 3구간 순서는 {l3_n_dates}일({l3_win}) 부분창이라 1년 판정에 못 올린다 — "
        "참고 수치로만 보고.",
        "홀드아웃 게이트(§4, 탐색/검증창 분리 재현)는 L2/L3에서 별도로 안 돌렸다 — L1(다른 SA)의 몫이거나 "
        "B-5 이월 후보. L3는 창 자체가 검증창(2026-05-11~08-09)보다 짧아 분리하면 표본이 더 얇아진다.",
        "민감도 ②(평시 행만 재계산)는 L1의 캘린더 라벨을 L2/L3 원본 행에 날짜로 병합해야 하는데, "
        "이번 라운드에서는 ①(bin 경계)·③(그룹 가중)만 실행했다 — ②는 미실행(시간 예산).",
        "마스킹 표의 head unit 동률 처리(비용 동률 시 rank_sum 큰 쪽)는 임의 결정론적 규칙이다 — "
        "동률 비율이 크면 이 규칙 민감도를 추가로 봐야 하나 이번엔 안 쟀다.",
        "판별자 커버리지 '사건의 몇 %가 덮이는가' 자체는 사건 정의가 없어 계산하지 않았다(판정불능, "
        "METHOD §6이 명시적으로 이번 라운드 범위 밖이라 규정).",
    ]
    for u in unresolved:
        print(" -", u)

    (out_dir / "_findings_and_unresolved.txt").write_text(
        "발견/판정:\n" + "\n".join(f"- {f}" for f in findings) +
        "\n\n미판정:\n" + "\n".join(f"- {u}" for u in unresolved) + "\n",
        encoding="utf-8"
    )

    print("\n=== 완료 — 산출물:", out_dir, "===")
    for f in sorted(out_dir.glob("*.csv")):
        print(" -", f.name)


if __name__ == "__main__":
    main()
