#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_panel.py — 외부 요인 라벨 패널 + 커버리지 + ROAS 밴드 판정 생성.
읽기 전용 산출물. prod DB 쓰기/네이버 API 호출/저장소 파일 수정 없음.
재실행 가능(D0=2026-08-17 상수 고정, 랜덤/현재시각 미사용).
"""
import json
import re
import csv
from pathlib import Path
from datetime import date, timedelta
import calendar

import pandas as pd
import numpy as np
import holidays

# ---------------------------------------------------------------------------
# 경로 / 상수
# ---------------------------------------------------------------------------
SCRATCH = Path("/private/tmp/claude-501/-Users-jino-Library-Mobile-Documents-com-apple-CloudDocs-1Personal-AI-Program-Ohiselling/02e2416c-335b-4cfb-b417-e123946b0921/scratchpad")
REPO = Path("/Users/jino/Library/Mobile Documents/com~apple~CloudDocs/1Personal/AI Program/Ohiselling")

D0 = pd.Timestamp("2026-08-17")
MATURE_CUTOFF = pd.Timestamp("2026-08-09")  # D0 - 8
VACATION_START_MD = (7, 20)
VACATION_END_MD = (8, 15)
VACATION_PEAK_START_MD = (7, 25)
VACATION_PEAK_END_MD = (8, 10)

BEP_ROAS = 1.711
BEP_ROAS_120 = round(BEP_ROAS * 1.2, 4)  # 2.0532

DATA_GAP_START = pd.Timestamp("2026-03-02")
DATA_GAP_END = pd.Timestamp("2026-03-29")

print("=== 0) 로드 ===")

# ---------------------------------------------------------------------------
# 1) 원천 CSV 로드
# ---------------------------------------------------------------------------
panel = pd.read_csv(SCRATCH / "panel_daily.csv", dtype={"campaign_id": str, "adgroup_id": str})
panel["ad_date"] = pd.to_datetime(panel["ad_date"])
print("panel_daily rows:", len(panel))

entities_ag = pd.read_csv(SCRATCH / "entities_adgroup.csv", dtype=str)
entities_cmp = pd.read_csv(SCRATCH / "entities_campaign.csv", dtype=str)
exclusion = pd.read_csv(SCRATCH / "exclusion.csv", dtype=str)
agency_op = pd.read_csv(SCRATCH / "agency_op.csv", dtype=str)
snapshot = pd.read_csv(SCRATCH / "snapshot.csv", dtype=str)

print("entities_adgroup:", len(entities_ag), "entities_campaign:", len(entities_cmp))
print("exclusion:", len(exclusion), "agency_op:", len(agency_op), "snapshot:", len(snapshot))

# ---------------------------------------------------------------------------
# 2) 검산
# ---------------------------------------------------------------------------
print("\n=== 검산 ① sentinel vs real_unit ===")
sentinel_check = pd.read_csv(SCRATCH / "sentinel_check.csv")
tol = 20  # ±수원
bad = sentinel_check[sentinel_check["diff"].abs() > tol]
print(f"sentinel 존재 날짜 수: {len(sentinel_check)}, 허용오차(±{tol}원) 초과: {len(bad)}")
if len(bad):
    print(bad.to_string(index=False))
sentinel_check.to_csv(SCRATCH / "verification_sentinel_check.csv", index=False)

print("\n=== 검산 ② panel_daily.csv 총합 vs 원천 non-sentinel 총합 ===")
total_check = pd.read_csv(SCRATCH / "total_cost_check.csv")
panel_cost_sum = int(panel["cost"].sum())
source_total = int(total_check["non_sentinel_nonempty_total"].iloc[0])
print("panel_daily.csv cost sum:", panel_cost_sum)
print("원천 SQL non-sentinel(+campaign_type<>'') 총합:", source_total)
print("일치:", panel_cost_sum == source_total)
print("제외 행 보고: adgroup_id='' ->", int(total_check["empty_adgroup_rows"].iloc[0]),
      "행 / campaign_type='' (adgroup_id 유효) ->", int(total_check["empty_campaign_type_rows"].iloc[0]), "행")

# ---------------------------------------------------------------------------
# 3) 일자 라벨
# ---------------------------------------------------------------------------
print("\n=== 일자 라벨 ===")
all_dates = pd.date_range(panel["ad_date"].min(), panel["ad_date"].max(), freq="D")
kr_holidays = holidays.SouthKorea(years=range(all_dates.min().year, all_dates.max().year + 1))

# 명절(설날/추석) 라벨 날짜 수집
sc_label_dates = sorted([pd.Timestamp(d) for d, name in kr_holidays.items()
                          if ("설날" in name) or ("추석" in name)])

# 연속일 클러스터링
clusters = []
cur = []
for d in sc_label_dates:
    if not cur:
        cur = [d]
    elif d == cur[-1] + pd.Timedelta(days=1):
        cur.append(d)
    else:
        clusters.append(cur)
        cur = [d]
if cur:
    clusters.append(cur)

# 각 클러스터를 인접 주말로 확장
windows = []
for c in clusters:
    start, end = c[0], c[-1]
    while (start - pd.Timedelta(days=1)).weekday() in (5, 6):
        start -= pd.Timedelta(days=1)
    while (end + pd.Timedelta(days=1)).weekday() in (5, 6):
        end += pd.Timedelta(days=1)
    labels = [f"{d.date()}:{kr_holidays.get(d)}" for d in c]
    windows.append({"window_start": start.date(), "window_end": end.date(),
                     "core_labels": " | ".join(labels), "n_days": (end - start).days + 1})

holiday_windows_df = pd.DataFrame(windows)
holiday_windows_df.to_csv(SCRATCH / "holiday_windows.csv", index=False)
print("명절 연휴 창 개수:", len(holiday_windows_df))
print(holiday_windows_df.to_string(index=False))


def in_any_window(d):
    for w in windows:
        if pd.Timestamp(w["window_start"]) <= d <= pd.Timestamp(w["window_end"]):
            return True
    return False


def month_phase(d):
    last_day = calendar.monthrange(d.year, d.month)[1]
    if d.day <= 5:
        return "early"
    if d.day > last_day - 5:
        return "late"
    return "mid"


def is_vacation(d):
    md = (d.month, d.day)
    return VACATION_START_MD <= md <= VACATION_END_MD


def is_vacation_peak(d):
    md = (d.month, d.day)
    return VACATION_PEAK_START_MD <= md <= VACATION_PEAK_END_MD


date_labels = pd.DataFrame({"ad_date": all_dates})
date_labels["dow"] = date_labels["ad_date"].dt.weekday
date_labels["is_weekend"] = date_labels["dow"].isin([5, 6])
date_labels["is_seollal_chuseok"] = date_labels["ad_date"].apply(in_any_window)
date_labels["is_holiday"] = date_labels["ad_date"].apply(
    lambda d: (d in kr_holidays) and not in_any_window(d)
)
date_labels["holiday_name"] = date_labels["ad_date"].apply(lambda d: kr_holidays.get(d) if d in kr_holidays else None)
date_labels["is_vacation"] = date_labels["ad_date"].apply(is_vacation)
date_labels["is_vacation_peak"] = date_labels["ad_date"].apply(is_vacation_peak)
date_labels["month_phase"] = date_labels["ad_date"].apply(month_phase)
date_labels["mature"] = date_labels["ad_date"] <= MATURE_CUTOFF
date_labels["data_gap"] = (date_labels["ad_date"] >= DATA_GAP_START) & (date_labels["ad_date"] <= DATA_GAP_END)

date_labels.to_csv(SCRATCH / "date_labels.csv", index=False)
print("date_labels 행수:", len(date_labels))

# ---------------------------------------------------------------------------
# 4) 출시 라벨 — 그룹→모델 조인
# ---------------------------------------------------------------------------
print("\n=== 출시 라벨 매핑 ===")

with open(REPO / "backend/app/data/device_launch_dates_kr.json", encoding="utf-8") as f:
    kr_json = json.load(f)
with open(REPO / "backend/app/data/iphone_launch_dates.json", encoding="utf-8") as f:
    iphone_json = json.load(f)

# DEVICE_DB: (series, gen) -> record (원문 JSON 그대로, 날짜는 여기서만 읽는다)
DEVICE_DB = {}
for series in ["galaxy_s", "galaxy_z_fold", "galaxy_z_flip", "galaxy_a", "galaxy_tab", "ipad"]:
    for rec in kr_json[series]:
        DEVICE_DB[(series, str(rec["generation"]))] = rec

# iPhone: JSON엔 launch_kr 3건만(연도 오름차순, 모델 라벨 없음).
# ★추정 표기: 2023-09-22→iPhone15 / 2024-09-20→iPhone16 / 2025-09-19→iPhone17로
#   연대순 대응시켰다. JSON 자체엔 이 대응이 명시돼 있지 않아 "fuzzy"로 신뢰도를 낮춘다.
iphone_dates_sorted = sorted(iphone_json["launch_dates"])
IPHONE_GEN_MAP = {"15": iphone_dates_sorted[0], "16": iphone_dates_sorted[1], "17": iphone_dates_sorted[2]}
for gen, launch in IPHONE_GEN_MAP.items():
    DEVICE_DB[("iphone", gen)] = {"model": f"아이폰{gen}(추정 매핑)", "launch_kr": launch,
                                   "preorder_kr": None, "announce_kr": None, "source_tier": "inferred_from_order"}


def get_rec(series, gen):
    return DEVICE_DB.get((series, gen))


def strip_ts_suffix(name):
    # 끝의 타임스탬프 접미사 제거: -1744937657247 류 (9자리 이상)
    return re.sub(r"[-_]\d{9,}$", "", name).strip()


TABLET_SUFFIX_RE = re.compile(r"(종이질감|6H\s*BLC|6H_PET_BLC|블루라이트|강화유리코팅)")
PHONE_SUFFIX_RE = re.compile(r"(지문방지|강화유리(?!코팅)|사생활|맥세이프|카메라|자가복원)")


def match_models(raw_adgroup_name, raw_campaign_name, campaign_type):
    """단일 (adgroup, campaign) 이름쌍에서 매칭되는 (series,gen,confidence) 목록 반환."""
    name = strip_ts_suffix(raw_adgroup_name or "")
    cname = raw_campaign_name or ""
    combined = f"{cname} {name}"
    matches = []  # (series, gen, confidence, matched_text)

    def add(series, gen, conf, text):
        if get_rec(series, gen) is not None or True:
            matches.append((series, gen, conf, text))

    # --- 골프 액세서리(폰/탭 아님) ---
    if "골프" in name:
        return []  # no_model 취급(아래에서 골프 별도 플래그)

    # --- 뮤패드(비Apple/Samsung 태블릿, DB에 없음) ---
    if "뮤패드" in combined:
        add("mupad", "generic", "unresolved", "뮤패드")
        return matches

    is_tab_ctx = ("갤럭시탭" in combined) or ("탭" in cname and "갤럭시" in cname)
    is_ipad_ctx = ("아이패드" in combined)

    # --- iPad ---
    if is_ipad_ctx or re.search(r"(미니\s*\d|에어\s*[\d,/~\-]+|프로\s*[\dM,/~\-]+|^\s*\d{1,2}\s*세대|\d{1,2}[-~]\d{1,2}[-~]?\d?\s*세대)", name):
        gens_found = []
        m = re.search(r"미니\s*[_]?\s*([\d/,~\-]+)", name)
        if m or "미니" in name:
            digits = re.findall(r"\d", m.group(1)) if m else re.findall(r"미니\D{0,3}(\d)", name)
            for dg in digits:
                if ("ipad", f"mini{dg}") in DEVICE_DB:
                    gens_found.append(("ipad", f"mini{dg}"))
        m = re.search(r"에어\s*[_]?\s*\(?([\dM,/~\-]+)", name)
        if m:
            token = m.group(1)
            digs = re.findall(r"\d", token)
            # "45"/"67" 처럼 구분자 없이 붙은 경우 자릿수 분해
            if len(digs) == 1 and len(token.replace(",", "").replace("/", "").replace("~", "").replace("-", "")) == 2:
                digs = list(token.replace(",", "").replace("/", "").replace("~", "").replace("-", ""))
            for dg in set(digs):
                if ("ipad", f"air{dg}") in DEVICE_DB:
                    gens_found.append(("ipad", f"air{dg}"))
        m = re.search(r"프로\s*[_]?\s*(M?\d[\dM,/~\-]*)", name)
        if m:
            token = m.group(1)
            if "M4" in token or "M47" in name.replace(" ", ""):
                gens_found.append(("ipad", "pro7"))
            else:
                digs = re.findall(r"\d", token)
                if len(digs) == 1 and len(re.sub(r"[^0-9]", "", token)) >= 2:
                    digs = list(re.sub(r"[^0-9]", "", token))
                for dg in set(digs):
                    if dg in ("3", "4", "5"):
                        gens_found.append(("ipad", "pro3-5"))
                    elif dg == "6":
                        gens_found.append(("ipad", "pro6"))
                    elif dg == "7":
                        gens_found.append(("ipad", "pro7"))
        if not gens_found:
            m = re.search(r"(?:^|[^0-9])(\d{1,2})\s*세대", name)
            if m:
                g = m.group(1)
                if ("ipad", g) in DEVICE_DB:
                    gens_found.append(("ipad", g))
                else:
                    gens_found.append(("ipad", "UNKNOWN_" + g))
            m2 = re.search(r"(\d)[-~](\d)[-~]?(\d)?\s*세대", name)  # 7-8-9세대
            if m2 and not gens_found:
                gens_found.append(("ipad", "UNKNOWN_" + "".join([g for g in m2.groups() if g])))

        if gens_found:
            for series, gen in set(gens_found):
                if gen.startswith("UNKNOWN_"):
                    add(series, gen, "unresolved", name)
                else:
                    rec = get_rec(series, gen)
                    conf = "exact" if len(gens_found) == 1 else "fuzzy"
                    if rec and rec.get("launch_kr") is None:
                        conf = "unresolved"
                    add(series, gen, conf, name)
            return matches
        else:
            add("ipad", "generic_gen_unknown", "unresolved", name)
            return matches

    # --- Galaxy Tab (S8-S11, A9+, 구형) ---
    if is_tab_ctx:
        m = re.search(r"A\s*9\s*\+?\s*플러스|A9\+|A9플러스", name)
        if m:
            add("galaxy_tab", "A9+", "exact", m.group(0))
            return matches
        m = re.search(r"A\s*(7|7라이트|7\s*라이트|8|9)(?!\d)", name)
        if m:
            add("galaxy_tab", "구형", "unresolved", m.group(0))
            return matches
        m = re.search(r"S\s*(8|9|10|11)(?!\d)", name)
        if m:
            gen = "S" + m.group(1)  # JSON galaxy_tab generation 키는 'S8'..'S11' (접두 포함)
            add("galaxy_tab", gen, "exact", m.group(0))
            return matches
        # JSON '구형' 버킷이 명시하는 S5E/S6/S7(FE/+) 계열 — 날짜 데이터 없음
        m = re.search(r"S\s*(5[Ee]|6|7)(?!\d)", name)
        if m:
            add("galaxy_tab", "구형", "unresolved", m.group(0))
            return matches
        add("galaxy_tab", "generic_gen_unknown", "unresolved", name)
        return matches

    tablet_suffix = bool(TABLET_SUFFIX_RE.search(name))
    phone_suffix = bool(PHONE_SUFFIX_RE.search(name))

    # --- iPhone ---
    m = re.search(r"아이폰\s*_?\s*(SE2|SE3|SE|16e|17e|17E|X[RS]?|\d{1,2})\s*(프로맥스|프로|미니|플러스|에어)?", combined)
    if m or re.match(r"^\s*(\d{1,2})\s*(프로맥스|프로|미니|플러스|에어)?\s*[_\(]?\s*(맥|일|맥세이프)?", name):
        num = None
        variant = None
        if m:
            num, variant = m.group(1), m.group(2)
        else:
            m2 = re.match(r"^\s*(\d{1,2})\s*(프로맥스|프로|미니|플러스|에어)?", name)
            if m2 and m2.group(1) in {"7", "8", "11", "12", "13", "14", "15", "16", "17"}:
                num, variant = m2.group(1), m2.group(2)
        if num:
            if num in ("16e",):
                add("iphone", "16", "fuzzy", combined)
            elif num in ("17e", "17E"):
                add("iphone", "17", "fuzzy", combined)
            elif variant == "에어" and num == "17":
                add("iphone", "17", "fuzzy", combined)
            elif num in ("15", "16", "17"):
                add("iphone", num, "fuzzy", combined)  # iPhone json 자체가 추정매핑 -> fuzzy 상한
            elif num in ("7", "8", "11", "12", "13", "14", "X", "XR", "XS", "SE", "SE2", "SE3"):
                add("iphone", f"UNKNOWN_{num}", "unresolved", combined)
            if num or variant:
                return matches

    # --- Z 폴드/플립 8 특수(와이드/울트라) ---
    if re.search(r"Z?\s*폴드\s*8\s*(와이드|울트라)?", name) and "폴드" in name:
        add("galaxy_z_fold", "8", "exact", name)
        return matches
    if re.search(r"Z?\s*플립\s*8", name):
        add("galaxy_z_flip", "8", "exact", name)
        return matches

    m = re.search(r"Z?\s*폴드\s*/?\s*플립\s*(\d)", name)
    if m:
        add("galaxy_z_fold", m.group(1), "fuzzy", name)
        add("galaxy_z_flip", m.group(1), "fuzzy", name)
        return matches

    m = re.search(r"(?:갤럭시\s*)?Z?\s*폴드\s*(SE|\d)(?!\d)", name)
    if m:
        g = m.group(1)
        if g == "SE":
            add("galaxy_z_fold", "SE_unresolved", "unresolved", name)
        elif g in ("1", "2"):
            add("galaxy_z_fold", "1-2", "unresolved", name)
        else:
            add("galaxy_z_fold", g, "exact", name)
        return matches

    m = re.search(r"(?:갤럭시\s*)?Z?\s*플립\s*(\d)(?!\d)", name)
    if m:
        g = m.group(1)
        if g in ("1", "2"):
            add("galaxy_z_flip", "1-2", "unresolved", name)
        else:
            add("galaxy_z_flip", g, "exact", name)
        return matches

    # --- 노트 시리즈 (DB에 없음) ---
    m = re.search(r"노트\s*(8|9|10|20)\s*(플러스|울트라)?", name)
    if m:
        add("note", f"노트{m.group(1)}{m.group(2) or ''}", "unresolved", name)
        return matches

    # --- 퀀텀(A54/55/56 리브랜딩) ---
    m = re.search(r"퀀텀\s*([456])", name)
    if m:
        gen_map = {"4": "A54", "5": "A55", "6": "A56"}
        add("galaxy_a", gen_map[m.group(1)], "fuzzy", name)
        return matches

    # --- Galaxy A (A15~A73, 지문방지_TPU 접미 다수) ---
    m = re.search(r"A\s*(15|16|17|23|24|25|33|34|35|36|53|54|55|56|73)(?!\d)", name)
    if m:
        num = m.group(1)
        gen = "A" + num  # JSON galaxy_a generation 키는 'A15'..'A73' (접두 포함)
        rec = get_rec("galaxy_a", gen)
        conf = "exact"
        if num in ("16", "17"):
            conf = "fuzzy"  # LTE/5G(버디) 두 후보 중 LTE를 기본값으로 사용 — 모호성 있음
        if rec and rec.get("launch_kr") is None:
            conf = "unresolved"
        add("galaxy_a", gen, conf, name)
        return matches

    # --- 갤럭시 S / 갤럭시(브랜드만) ---
    m = re.search(r"S\s*(22|23|24|25|26)\s*(FE|엣지|Edge)?(?!\d)", name)
    if m:
        gen, suf = m.group(1), m.group(2)
        if suf and suf.upper() == "FE":
            if gen == "24":
                add("galaxy_s", "S24 FE", "exact", name)
            else:
                add("galaxy_s", f"S{gen}FE_unresolved", "unresolved", name)
        elif suf and ("엣지" in suf or suf == "Edge"):
            if gen == "25":
                add("galaxy_s", "S25 Edge", "exact", name)
            else:
                add("galaxy_s", f"S{gen}Edge_unresolved", "unresolved", name)
        else:
            add("galaxy_s", f"S{gen}", "exact", name)
        return matches

    m = re.search(r"S\s*(8|9|10|11|20|21)\s*(E|5G|FE|플러스|울트라)?(?!\d)", name)
    if m:
        add("galaxy_s", "S9-S21", "unresolved", name)
        return matches

    # 버디필름(모델 불명, 리브랜딩 여부 미확인 — spec 지시대로 unresolved 고정)
    if "버디필름" in name or "버디" in name:
        add("galaxy_a", "budi_unresolved", "unresolved", name)
        return matches

    return matches  # no match -> no_model


# --- 그룹 단위 매핑 실행 ---
cmp_name_map = dict(zip(entities_cmp["entity_id"], entities_cmp["name"]))
ag_rows = entities_ag.copy()
ag_rows["campaign_name"] = ag_rows["campaign_id"].map(cmp_name_map)

# panel의 adgroup 목록(비용 가중치 계산용)
ag_cost = panel.groupby(["adgroup_id", "campaign_id", "campaign_type"], as_index=False)["cost"].sum()
ag_cost = ag_cost.rename(columns={"cost": "cost_365d"})

map_rows = []
n_no_entity = 0
for _, r in ag_cost.iterrows():
    ent = ag_rows[ag_rows["entity_id"] == r["adgroup_id"]]
    if len(ent) == 0:
        n_no_entity += 1
        adgroup_name = None
        campaign_name_e = cmp_name_map.get(r["campaign_id"])
        status = None
    else:
        adgroup_name = ent.iloc[0]["name"]
        campaign_name_e = ent.iloc[0]["campaign_name"]
        status = ent.iloc[0]["status"]

    if adgroup_name is None:
        # entity 없음 -> 이름 기반 매칭 불가
        map_rows.append({
            "adgroup_id": r["adgroup_id"], "adgroup_name": None,
            "campaign_name": campaign_name_e, "campaign_type": r["campaign_type"],
            "parsed_model": None, "matched_json_model": None,
            "launch_kr": None, "preorder_kr": None, "announce_kr": None,
            "source_tier": None, "match_confidence": "no_model",
            "model_known": False, "cost_365d": r["cost_365d"],
            "golf_flag": False,
        })
        continue

    is_golf = "골프" in (adgroup_name or "")
    matches = match_models(adgroup_name, campaign_name_e, r["campaign_type"])

    if not matches:
        map_rows.append({
            "adgroup_id": r["adgroup_id"], "adgroup_name": adgroup_name,
            "campaign_name": campaign_name_e, "campaign_type": r["campaign_type"],
            "parsed_model": None, "matched_json_model": None,
            "launch_kr": None, "preorder_kr": None, "announce_kr": None,
            "source_tier": None, "match_confidence": "no_model",
            "model_known": False, "cost_365d": r["cost_365d"],
            "golf_flag": is_golf,
        })
        continue

    parsed_names = []
    matched_names = []
    launch_dates = []
    confs = []
    best_rec = None
    best_launch = None
    for series, gen, conf, text in matches:
        parsed_names.append(f"{series}:{gen}")
        confs.append(conf)
        rec = get_rec(series, gen) if not gen.startswith(("UNKNOWN_", "generic")) and "unresolved" not in gen else None
        if rec:
            matched_names.append(rec["model"])
            if rec.get("launch_kr"):
                launch_dates.append((rec["launch_kr"], rec, series, gen))

    if launch_dates:
        launch_dates.sort(key=lambda x: x[0])
        chosen_launch, chosen_rec, chosen_series, chosen_gen = launch_dates[0]
        overall_conf = "fuzzy" if len(launch_dates) > 1 or any(c == "fuzzy" for c in confs) else "exact"
        if any(c == "unresolved" for c in confs) and len(launch_dates) == 0:
            overall_conf = "unresolved"
        map_rows.append({
            "adgroup_id": r["adgroup_id"], "adgroup_name": adgroup_name,
            "campaign_name": campaign_name_e, "campaign_type": r["campaign_type"],
            "parsed_model": "|".join(parsed_names), "matched_json_model": "|".join(set(matched_names)),
            "launch_kr": chosen_rec.get("launch_kr"), "preorder_kr": chosen_rec.get("preorder_kr"),
            "announce_kr": chosen_rec.get("announce_kr"), "source_tier": chosen_rec.get("source_tier"),
            "match_confidence": overall_conf, "model_known": True, "cost_365d": r["cost_365d"],
            "golf_flag": is_golf,
        })
    else:
        map_rows.append({
            "adgroup_id": r["adgroup_id"], "adgroup_name": adgroup_name,
            "campaign_name": campaign_name_e, "campaign_type": r["campaign_type"],
            "parsed_model": "|".join(parsed_names), "matched_json_model": None,
            "launch_kr": None, "preorder_kr": None, "announce_kr": None,
            "source_tier": None, "match_confidence": "unresolved", "model_known": True,
            "cost_365d": r["cost_365d"], "golf_flag": is_golf,
        })

adgroup_model_map = pd.DataFrame(map_rows)
adgroup_model_map.to_csv(SCRATCH / "adgroup_model_map.csv", index=False)

n_total = len(adgroup_model_map)
n_matched = (adgroup_model_map["launch_kr"].notna()).sum()
cost_total = adgroup_model_map["cost_365d"].sum()
cost_matched = adgroup_model_map.loc[adgroup_model_map["launch_kr"].notna(), "cost_365d"].sum()
print(f"그룹 수: {n_total}, 날짜 매칭 성공: {n_matched} ({n_matched/n_total:.1%})")
print(f"비용 가중 커버리지: {cost_matched}/{cost_total} = {cost_matched/cost_total:.4%}")
print(f"entity 없는 그룹 수(이름 null): {n_no_entity}")

unresolved_df = adgroup_model_map[adgroup_model_map["match_confidence"].isin(["unresolved"])]
unresolved_df.to_csv(SCRATCH / "adgroup_model_map_unresolved.csv", index=False)
print("unresolved 그룹 수:", len(unresolved_df), " / cost 합:", unresolved_df["cost_365d"].sum())

budi_rows = adgroup_model_map[adgroup_model_map["adgroup_name"].fillna("").str.contains("버디")]
print("\n버디필름 계열 그룹(unresolved 확인):")
print(budi_rows[["adgroup_id", "adgroup_name", "match_confidence", "cost_365d"]].to_string(index=False))

# ---------------------------------------------------------------------------
# 5) launch_phase (일자 x 그룹)
# ---------------------------------------------------------------------------
print("\n=== launch_phase 계산 ===")

lp_map = adgroup_model_map.set_index("adgroup_id")[
    ["launch_kr", "preorder_kr", "announce_kr", "match_confidence", "model_known"]
].to_dict("index")


def compute_phase(ad_date, adgroup_id):
    info = lp_map.get(adgroup_id)
    if info is None or pd.isna(info["launch_kr"]) or info["launch_kr"] is None:
        return "none"
    launch = pd.Timestamp(info["launch_kr"])
    preorder = pd.Timestamp(info["preorder_kr"]) if pd.notna(info["preorder_kr"]) and info["preorder_kr"] else None
    announce = pd.Timestamp(info["announce_kr"]) if pd.notna(info["announce_kr"]) and info["announce_kr"] else None
    d = ad_date
    if announce is not None and preorder is not None and announce <= d < preorder:
        return "F1a"
    if preorder is not None and preorder <= d < launch:
        return "F1b"
    if launch <= d <= launch + pd.Timedelta(days=27):
        return "F2"
    if launch + pd.Timedelta(days=28) <= d <= launch + pd.Timedelta(days=180):
        return "F3"
    return "none"


# ---------------------------------------------------------------------------
# 6) 패널 조립 (그룹 grain, 일자별)
# ---------------------------------------------------------------------------
print("\n=== 패널 조립(그룹 grain) ===")
grp = panel.groupby(["ad_date", "campaign_id", "campaign_type", "adgroup_id"], as_index=False).agg(
    imp=("imp", "sum"), clk=("clk", "sum"), cost=("cost", "sum"),
    rank_sum=("rank_sum", "sum"), conv_cnt=("conv_cnt", "sum"), conv_amt=("conv_amt", "sum"),
)
print("그룹×일자 행수:", len(grp))

grp = grp.merge(date_labels, on="ad_date", how="left")
grp["model_known"] = grp["adgroup_id"].map(lambda a: lp_map.get(a, {}).get("model_known", False))
grp["match_confidence"] = grp["adgroup_id"].map(lambda a: lp_map.get(a, {}).get("match_confidence"))
grp["launch_phase"] = [compute_phase(d, a) for d, a in zip(grp["ad_date"], grp["adgroup_id"])]

grp.to_csv(SCRATCH / "panel_labeled.csv", index=False)
print("panel_labeled.csv 저장, 행수:", len(grp))

# ---------------------------------------------------------------------------
# 7) 커버리지 표
# ---------------------------------------------------------------------------
print("\n=== 커버리지 표 ===")


def month_key(d):
    return f"{d.year}-{d.month:02d}"


grp["ym"] = grp["ad_date"].apply(month_key)
cov_rows = []
for (ctype, ym), sub in grp.groupby(["campaign_type", "ym"]):
    dates_present = sorted(sub["ad_date"].dt.date.unique())
    y, m = map(int, ym.split("-"))
    ndays_expected = calendar.monthrange(y, m)[1]
    # 실제 관측 가능한 범위로 클립(전체 테이블 범위 밖 날짜는 기대하지 않음)
    month_start = pd.Timestamp(y, m, 1)
    month_end = pd.Timestamp(y, m, ndays_expected)
    clip_start = max(month_start, panel["ad_date"].min())
    clip_end = min(month_end, panel["ad_date"].max())
    expected_dates = pd.date_range(clip_start, clip_end, freq="D").date
    missing = sorted(set(expected_dates) - set(dates_present))
    data_gap_flag = any(DATA_GAP_START.date() <= d <= DATA_GAP_END.date() for d in expected_dates)
    cov_rows.append({
        "campaign_type": ctype, "year_month": ym,
        "distinct_ad_date": len(dates_present),
        "expected_days_in_window": len(expected_dates),
        "missing_days": len(missing),
        "missing_dates": ";".join(str(d) for d in missing),
        "rows": len(sub), "n_adgroups": sub["adgroup_id"].nunique(),
        "cost": int(sub["cost"].sum()), "conv_amt": int(sub["conv_amt"].sum()),
        "data_gap": data_gap_flag,
    })
coverage_df = pd.DataFrame(cov_rows).sort_values(["campaign_type", "year_month"])
coverage_df.to_csv(SCRATCH / "coverage.csv", index=False)

# 유형별 전체 창(고유일수) 요약
cov_summary = grp.groupby("campaign_type").agg(
    distinct_dates=("ad_date", lambda s: s.dt.date.nunique()),
    min_date=("ad_date", "min"), max_date=("ad_date", "max"),
    rows=("ad_date", "size"), cost=("cost", "sum"), conv_amt=("conv_amt", "sum"),
).reset_index()
overall_distinct = grp["ad_date"].dt.date.nunique()
print("유형별 커버리지 요약:")
print(cov_summary.to_string(index=False))
print("테이블 전체 distinct ad_date:", overall_distinct)
cov_summary.to_csv(SCRATCH / "coverage_summary_by_type.csv", index=False)

# ---------------------------------------------------------------------------
# 8) 밴드 판정
# ---------------------------------------------------------------------------
print("\n=== 밴드 판정 ===")

mature_grp = grp[grp["mature"]].copy()


def aov_lookup_table(df):
    """AOV 계층 폴백 테이블 준비: 그룹/캠페인/유형/계정 4계층."""
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


def band_judge(cost, conv_cnt, conv_amt, aov, k=3):
    if cost == 0:
        return "excluded_cost0", None
    if conv_cnt >= 1:
        roas = conv_amt / cost
        if roas >= BEP_ROAS_120:
            return "band1", roas
        if roas >= BEP_ROAS:
            return "band2", roas
        return "band3", roas
    else:
        threshold = k * aov / BEP_ROAS if aov and not np.isnan(aov) else np.nan
        if not np.isnan(threshold) and cost > threshold:
            return "band3", 0.0
        return "band4_unjudgeable", None


def run_band_unit(df, group_cols, label=""):
    acc, by_type, by_camp, by_grp = aov_lookup_table(df)
    agg = df.groupby(group_cols, as_index=False).agg(
        cost=("cost", "sum"), conv_amt=("conv_amt", "sum"), conv_cnt=("conv_cnt", "sum"),
        clk=("clk", "sum"), n_days=("ad_date", "nunique"),
    )
    if "campaign_id" not in agg.columns:
        agg["campaign_id"] = df.groupby(group_cols)["campaign_id"].first().values
    if "campaign_type" not in agg.columns:
        agg["campaign_type"] = df.groupby(group_cols)["campaign_type"].first().values
    if "adgroup_id" not in agg.columns:
        agg["adgroup_id"] = df.groupby(group_cols)["adgroup_id"].first().values

    bands, roass, aov_used, aov_layer = [], [], [], []
    for _, r in agg.iterrows():
        aov, layer = get_aov(r["adgroup_id"], r["campaign_id"], r["campaign_type"], acc, by_type, by_camp, by_grp)
        b, roas = band_judge(r["cost"], r["conv_cnt"], r["conv_amt"], aov, k=3)
        bands.append(b); roass.append(roas); aov_used.append(aov); aov_layer.append(layer)
    agg["band"] = bands
    agg["roas"] = roass
    agg["aov_used"] = aov_used
    agg["aov_layer"] = aov_layer
    return agg


# ⓐ 그룹×전체기간(mature)
band_group_total = run_band_unit(mature_grp, ["adgroup_id", "campaign_id", "campaign_type"], "group_total")
band_group_total.to_csv(SCRATCH / "band_group_total.csv", index=False)
print("band_group_total 행수:", len(band_group_total))
print(band_group_total["band"].value_counts())

# ⓑ 그룹×라벨층
label_dims = {
    "weekend_flag": mature_grp["is_weekend"].map({True: "weekend", False: "weekday"}),
    "vacation_flag": mature_grp["is_vacation"].map({True: "vacation_in", False: "vacation_out"}),
    "holiday_flag": mature_grp["is_holiday"].map({True: "holiday", False: "non_holiday"}),
    "seollal_chuseok_flag": mature_grp["is_seollal_chuseok"].map({True: "seollal_chuseok", False: "non_sc"}),
    "launch_phase": mature_grp["launch_phase"],
}
mature_grp2 = mature_grp.copy()
for k, v in label_dims.items():
    mature_grp2[k] = v

band_by_label_frames = []
for dim in ["weekend_flag", "vacation_flag", "holiday_flag", "seollal_chuseok_flag", "launch_phase"]:
    sub_agg = run_band_unit(mature_grp2, ["adgroup_id", "campaign_id", "campaign_type", dim], dim)
    sub_agg["label_dim"] = dim
    sub_agg = sub_agg.rename(columns={dim: "label_value"})
    band_by_label_frames.append(sub_agg)
band_group_by_label = pd.concat(band_by_label_frames, ignore_index=True)
band_group_by_label.to_csv(SCRATCH / "band_group_by_label.csv", index=False)
print("band_group_by_label 행수:", len(band_group_by_label))

# ---------------------------------------------------------------------------
# 9) 민감도 (무전환 경계 계수 1/3/5)
# ---------------------------------------------------------------------------
print("\n=== 민감도 분석 ===")
acc, by_type, by_camp, by_grp = aov_lookup_table(mature_grp)
agg_base = mature_grp.groupby(["adgroup_id", "campaign_id", "campaign_type"], as_index=False).agg(
    cost=("cost", "sum"), conv_amt=("conv_amt", "sum"), conv_cnt=("conv_cnt", "sum"),
)
sens_rows = []
for k in (1, 3, 5):
    counts = {"band3": 0, "band4_unjudgeable": 0}
    for _, r in agg_base.iterrows():
        aov, layer = get_aov(r["adgroup_id"], r["campaign_id"], r["campaign_type"], acc, by_type, by_camp, by_grp)
        b, roas = band_judge(r["cost"], r["conv_cnt"], r["conv_amt"], aov, k=k)
        if b in counts:
            counts[b] += 1
    sens_rows.append({"k": k, "band3_count": counts["band3"], "band4_unjudgeable_count": counts["band4_unjudgeable"]})
band_sensitivity = pd.DataFrame(sens_rows)
band_sensitivity.to_csv(SCRATCH / "band_sensitivity.csv", index=False)
print(band_sensitivity.to_string(index=False))

print("\n=== 완료 ===")
