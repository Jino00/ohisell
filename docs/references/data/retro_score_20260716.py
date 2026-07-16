# retro_score.py — 소급 채점(읽기 전용): ①진단 보드를 과거 as-of로 리플레이해 방향 채점
#   ②trigger_pacing 정보성 경보를 그날 최종 소진으로 채점(+완결도 보정 시 억제율)
# 실행: cd /home/ubuntu/ohisell/backend; set -a; . .env; set +a; PYTHONPATH=. .venv/bin/python3 /tmp/retro_score.py
import json
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc

from app.database import SessionLocal
from app.models import NaverAdDaily, NaverHourlySnapshot, NaverProposal
from app.services.naver_ad import diagnosis
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

TRUE_BEP = Decimal("1.4758")  # 계정 매출가중 순수 손익분기(2026-07-16 실측)
DATA_MIN = date(2026, 7, 4)   # 상세행 시작일
DATA_MAX = date(2026, 7, 15)  # 마지막 완결일
ASOF_DAYS = [date(2026, 7, 8) + timedelta(days=i) for i in range(5)]  # 07-08~07-12

db = SessionLocal()

BOARDS = [
    # (board_key, grain, direction)
    ("shopping_group_bep", "adgroup", "down"),
    ("shopping_group_growth", "adgroup", "up"),
    ("bleeding_keywords", "keyword", "down"),
    ("starving_winners", "keyword", "up"),
    ("pause_candidates", "keyword", "pause"),
    ("shopping_pause_candidates", "adgroup", "pause"),
]


def post_agg(grain, ids, dfrom, dto):
    if not ids:
        return {}
    col = NaverAdDaily.adgroup_id if grain == "adgroup" else NaverAdDaily.keyword_id
    q = (
        db.query(
            col,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0)
            + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= dfrom, NaverAdDaily.ad_date <= dto,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            col.in_(list(ids)),
        )
        .group_by(col)
        .all()
    )
    return {r[0]: {"cost": int(r[1]), "conv": int(r[2]), "clk": int(r[3])} for r in q}


# ── Part A: 진단 보드 as-of 리플레이 + 방향 채점 ──
records = []
flag_history = defaultdict(list)  # (grain, tid) -> [(asof, board, direction)]

for D in ASOF_DAYS:
    dfrom = max(DATA_MIN, D - timedelta(days=14))
    diag_out = diagnosis.build_diagnosis(db, dfrom, D)
    boards = diag_out.get("boards") or {}
    cf = Decimal(str(diag_out["correction_factor"]["factor"]))
    bep = Decimal(str(diag_out["account_bep_roas"]))
    target = Decimal(str(diag_out["account_target_roas"]))
    post_from, post_to = D + timedelta(days=1), DATA_MAX
    post_days = (post_to - post_from).days + 1

    for board, grain, direction in BOARDS:
        cands = boards.get(board) or []
        key = "adgroup_id" if grain == "adgroup" else "keyword_id"
        ids = [c[key] for c in cands if c.get(key)]
        agg = post_agg(grain, ids, post_from, post_to)
        for c in cands:
            tid = c.get(key)
            if not tid:
                continue
            flag_history[(grain, tid)].append((D.isoformat(), board, direction))
            row = agg.get(tid, {"cost": 0, "conv": 0, "clk": 0})
            cost_p, conv_p = row["cost"], row["conv"]
            roas_c = float((Decimal(conv_p) / Decimal(cost_p)) * cf) if cost_p > 0 else None
            bleed = float(Decimal(cost_p) - Decimal(conv_p) * cf / TRUE_BEP)  # 진짜BEP 기준 공헌이익 적자
            if cost_p == 0:
                verdict = "no_spend"
            elif direction in ("down", "pause"):
                if direction == "pause":
                    verdict = "correct" if conv_p == 0 else ("gray" if roas_c < float(bep) else "wrong")
                else:
                    verdict = "correct" if roas_c < float(bep) else ("gray" if roas_c < float(target) else "wrong")
            else:  # up
                verdict = "correct" if roas_c >= float(target) else ("gray" if roas_c >= float(bep) else "wrong")
            records.append({
                "asof": D.isoformat(), "board": board, "direction": direction, "grain": grain,
                "target_id": tid, "campaign_id": c.get("campaign_id", ""),
                "cost_asof": c.get("cost"), "roas_c_asof": c.get("roas_corrected"),
                "post_days": post_days, "cost_post": cost_p, "conv_post": conv_p,
                "roas_c_post": roas_c, "bleed_post": round(bleed), "verdict": verdict,
            })

# 집계
summary = defaultdict(lambda: defaultdict(int))
bleed_sum = defaultdict(float)
for r in records:
    summary[r["board"]][r["verdict"]] += 1
    summary[r["board"]]["n"] += 1
    if r["direction"] in ("down", "pause") and r["verdict"] == "correct":
        bleed_sum[r["board"]] += max(0, r["bleed_post"])

# flip-flop / 지속성
flipflops = []
persist = defaultdict(int)
for (grain, tid), hist in flag_history.items():
    dirs = {d for _, _, d in hist}
    if "down" in dirs and "up" in dirs:
        flipflops.append({"grain": grain, "target_id": tid, "history": hist})
    days_flagged = len({a for a, _, _ in hist})
    persist[days_flagged] += 1

# ── Part B: trigger_pacing 경보 채점 ──
pat = re.compile(r"(저속|과속).*?(\d{4}-\d{2}-\d{2}) (\d+)시 기준 소진 (\d+)원/(\d+)원.*?배수=([0-9.]+)")
props = db.query(NaverProposal).filter(NaverProposal.proposal_type == "trigger_pacing").all()

# 최종 소진(sentinel)
finals = {
    (r[0], r[1]): int(r[2]) for r in db.query(
        NaverAdDaily.campaign_id, NaverAdDaily.ad_date, NaverAdDaily.cost
    ).filter(NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP,
             NaverAdDaily.ad_date >= date(2026, 7, 1)).all()
}

# 완결도 곡선(시각별 median: hourly_snapshot 누적cost ÷ sentinel 최종cost) — SA와 동일 규약 인라인
snaps = db.query(
    NaverHourlySnapshot.campaign_id, NaverHourlySnapshot.ad_date,
    NaverHourlySnapshot.snapshot_hour, NaverHourlySnapshot.cost,
).filter(NaverHourlySnapshot.ad_date < date(2026, 7, 16)).all()
ratios = defaultdict(list)
for cid, d, h, c in snaps:
    fc = finals.get((cid, d))
    if fc and fc >= 50_000:
        ratios[h].append(Decimal(c) / Decimal(fc))
curve = {h: statistics.median(v) for h, v in ratios.items() if len(v) >= 5}

pace = {"저속": defaultdict(int), "과속": defaultdict(int)}
suppressed = 0
suppress_eligible = 0
unparsed = 0
unscoreable = 0
for p in props:
    m = pat.search(p.rationale or "")
    if not m:
        unparsed += 1
        continue
    kind, dstr, hour, spent, budget, mult = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), float(m.group(6))
    d = date.fromisoformat(dstr)
    final = finals.get((p.campaign_id, d))
    if final is None or d > DATA_MAX:
        unscoreable += 1
        continue
    fr = final / budget if budget else 0
    if kind == "저속":
        bucket = "false_alarm_결국소진(≥90%)" if fr >= 0.9 else ("partial(50~90%)" if fr >= 0.5 else "correct_진짜저속(<50%)")
    else:
        bucket = "correct_과속(≥110%)" if fr >= 1.1 else ("partial(90~110%)" if fr >= 0.9 else "false_alarm(<90%)")
    pace[kind][bucket] += 1
    pace[kind]["n"] += 1
    # 완결도 보정 시 저속 경보 억제 여부(임계 0.5)
    if kind == "저속" and hour in curve:
        suppress_eligible += 1
        corrected_mult = mult / float(curve[hour])
        if corrected_mult > 0.5:
            suppressed += 1

out = {
    "part_a_board_replay": {
        "asof_days": [d.isoformat() for d in ASOF_DAYS],
        "summary": {b: dict(v) for b, v in summary.items()},
        "bleed_post_sum_correct(원)": {b: round(v) for b, v in bleed_sum.items()},
        "flipflop_targets": flipflops,
        "persistence_days_hist": dict(persist),
    },
    "part_b_trigger_pacing": {
        "total": len(props), "unparsed": unparsed, "unscoreable_최종치없음": unscoreable,
        "저속": dict(pace["저속"]), "과속": dict(pace["과속"]),
        "완결도보정_저속억제": {"eligible": suppress_eligible, "suppressed": suppressed},
        "curve_hours": {str(h): float(round(c, 3)) for h, c in sorted(curve.items())},
    },
}
with open("/tmp/retro_score_out.json", "w") as f:
    json.dump({"records": records, "summary": out}, f, ensure_ascii=False, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
