#!/usr/bin/env python3
"""P2 — 카나리 대상 선정 전수 판독 (계약 CONTRACT_pao_ignition_canary.md Q5).

★읽기 전용이다. SELECT만 실행한다. 쓰기·DDL 0건.
출력은 JSON 1덩어리 — 호출측에서 표로 렌더한다.
"""
import json
import sqlite3
import sys

DB = "/home/ubuntu/ohisell/backend/ohisell.db"
WINDOW = 28  # 일 — 주당 환산 분모 4

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
q = lambda sql, *a: [dict(r) for r in con.execute(sql, a).fetchall()]

out = {"window_days": WINDOW}
out["as_of"] = q("SELECT datetime('now','+9 hours') AS kst")[0]["kst"]

# ── 0. 모집단 ─────────────────────────────────────────────────
# 전수 = naver_entity의 campaign 행 ∪ naver_ad_daily에 비용이 있는 campaign_id
out["pop_entity"] = q(
    "SELECT status, COUNT(*) n FROM naver_entity WHERE entity_type='campaign' GROUP BY 1")
out["pop_spending"] = q(
    "SELECT COUNT(DISTINCT campaign_id) n FROM naver_ad_daily "
    "WHERE adgroup_id <> '__backfill__' AND cost > 0 "
    "AND ad_date BETWEEN date('now','-%d day') AND date('now','-1 day')" % WINDOW)[0]["n"]
out["settings_rows"] = q("SELECT COUNT(*) n FROM naver_campaign_settings")[0]["n"]

# ── 1. 캠페인별 전수 판독 ─────────────────────────────────────
rows = q("""
WITH perf AS (
  SELECT campaign_id,
         SUM(cost) cost, SUM(imp) imp, SUM(clk) clk,
         SUM(conv_direct_cnt + conv_indirect_cnt) conv_cnt,
         SUM(conv_direct_amt + conv_indirect_amt) conv_amt,
         COUNT(DISTINCT ad_date) active_days
  FROM naver_ad_daily
  WHERE adgroup_id <> '__backfill__'
    AND ad_date BETWEEN date('now','-%d day') AND date('now','-1 day')
  GROUP BY 1
),
ent AS (
  SELECT entity_id, name, campaign_type, status, status_reason
  FROM naver_entity WHERE entity_type='campaign'
),
ag AS (
  SELECT campaign_id, COUNT(*) n, MAX(detected_at) last_at
  FROM naver_agency_op
  WHERE detected_at >= datetime('now','-%d day')
  GROUP BY 1
),
ours AS (
  SELECT campaign_id, COUNT(*) n, MAX(changed_at) last_at
  FROM naver_change_log
  WHERE changed_at >= datetime('now','-%d day')
    AND action NOT LIKE 'external_%%'
  GROUP BY 1
)
SELECT COALESCE(ent.entity_id, perf.campaign_id) AS campaign_id,
       ent.name, ent.campaign_type, ent.status, ent.status_reason,
       s.optimizer, s.auto_operate, s.mode, s.memo, s.experiment_batch, s.updated_at,
       COALESCE(perf.cost,0) cost, COALESCE(perf.imp,0) imp, COALESCE(perf.clk,0) clk,
       COALESCE(perf.conv_cnt,0) conv_cnt, COALESCE(perf.conv_amt,0) conv_amt,
       COALESCE(perf.active_days,0) active_days,
       COALESCE(ag.n,0) agency_ops, ag.last_at AS agency_last,
       COALESCE(ours.n,0) our_changes, ours.last_at AS our_last
FROM ent
LEFT JOIN perf ON perf.campaign_id = ent.entity_id
LEFT JOIN naver_campaign_settings s ON s.campaign_id = ent.entity_id
LEFT JOIN ag ON ag.campaign_id = ent.entity_id
LEFT JOIN ours ON ours.campaign_id = ent.entity_id
UNION ALL
-- 원장에 비용은 있는데 entity에 없는 캠페인(있으면 그 자체가 발견이다)
SELECT perf.campaign_id, NULL, NULL, NULL, NULL,
       s.optimizer, s.auto_operate, s.mode, s.memo, s.experiment_batch, s.updated_at,
       perf.cost, perf.imp, perf.clk, perf.conv_cnt, perf.conv_amt, perf.active_days,
       COALESCE(ag.n,0), ag.last_at, COALESCE(ours.n,0), ours.last_at
FROM perf
LEFT JOIN naver_campaign_settings s ON s.campaign_id = perf.campaign_id
LEFT JOIN ag ON ag.campaign_id = perf.campaign_id
LEFT JOIN ours ON ours.campaign_id = perf.campaign_id
WHERE perf.campaign_id NOT IN (SELECT entity_id FROM ent)
""" % (WINDOW, WINDOW, WINDOW))

for r in rows:
    r["conv_per_week"] = round(r["conv_cnt"] / (WINDOW / 7.0), 2)
    r["roas"] = round(r["conv_amt"] / r["cost"], 4) if r["cost"] else None
out["campaigns"] = rows

# ── 2. 실험 배치 표기 판독 대상 (memo/experiment_batch 비어있지 않은 행) ──
out["memo_nonempty"] = q(
    "SELECT campaign_id, optimizer, auto_operate, experiment_batch, memo "
    "FROM naver_campaign_settings WHERE COALESCE(memo,'') <> '' OR COALESCE(experiment_batch,'') <> ''")

# ── 3. rationale 축 (change_log) — 실험 배치 표기가 여기에도 있는가 ──
out["rationale_experiment_hits"] = q(
    "SELECT campaign_id, COUNT(*) n, MIN(changed_at) first_at, MAX(changed_at) last_at "
    "FROM naver_change_log "
    "WHERE rationale LIKE '%A/B%' OR rationale LIKE '%대조군%' OR rationale LIKE '%홀드아웃%' "
    "   OR rationale LIKE '%MOP%' OR rationale LIKE '%holdout%' "
    "GROUP BY 1 ORDER BY n DESC")

# ── 4. 금지선 대조: 현재 스위치 상태 전수 ──────────────────────
out["switch_state"] = q(
    "SELECT campaign_id, optimizer, auto_operate, updated_at "
    "FROM naver_campaign_settings ORDER BY updated_at DESC")

json.dump(out, sys.stdout, ensure_ascii=False, default=str)
