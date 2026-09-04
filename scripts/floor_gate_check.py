"""floor_gate_check.py — D-NAO-286 표본 하한 게이트 라이브 확인 (읽기 전용)

계약 `docs/contracts/CONTRACT_sample_floor_gate.md` §5 `실행:` 명령의 스크립트.
prod에서 한 번 돌리면 §4 합격기준 ⓐⓑⓒⓓ의 관측값이 한 화면에 나온다.

    scp scripts/floor_gate_check.py sellc.ohitech.co.kr:/tmp/floor_gate_check.py
    ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python - < /tmp/floor_gate_check.py"

★쓰기 0 — SELECT와 순수 판정 호출만 한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv("/home/ubuntu/ohisell/backend/.env")

from app.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.services.naver_ad import auto_operator, guardrail_gate, guardrail_params  # noqa: E402

KST_NOW = datetime.utcnow() + timedelta(hours=9)
TODAY = KST_NOW.date()


def main() -> None:
    db = SessionLocal()
    print(f"== D-NAO-286 표본 하한 게이트 — 관측 {KST_NOW:%Y-%m-%d %H:%M:%S} KST ==")

    # ── ⓐ SPECS 2키가 화면 재료에 실리는가 ──────────────────────────────────
    print("\n[ⓐ] SPECS 노출")
    rows = {r["key"]: r for r in guardrail_params.describe(db)}
    print(f"     전체 키 {len(rows)}종")
    for key in ("min_weekly_conv_campaign", "min_weekly_conv_target"):
        r = rows.get(key)
        if r is None:
            print(f"     ⛔ {key} — describe()에 없음")
            continue
        print(f"     ✔ {key} = {r['value']} (code_default={r['code_default']} · "
              f"범위 {r['min']}~{r['max']} · source={r['source']} · direction={r['direction']})")
        print(f"        why: {r['why'][:96]}…")

    # ── ⓑ 소재 grain 정착창 집계가 0이 아닌가 ───────────────────────────────
    print("\n[ⓑ] 소재(ad) grain 정착창 집계 — 종전엔 원리적으로 전건 0이었다")
    window_from, window_to = auto_operator._settlement_window(TODAY)
    print(f"     창 {window_from} ~ {window_to}")
    ad_ids = [
        r[0] for r in db.execute(text(
            "select distinct entity_id from naver_change_log "
            "where action='update_bid' and dry_run=0 and after_value is not null "
            "and entity_type='ad' and changed_at >= datetime('now','-30 days')"
        ))
    ]
    if not ad_ids:
        print("     (최근 30일 소재 실집행 0건 — 대상 없음)")
    for ad_id in ad_ids:
        agg = auto_operator._settlement_agg(db, "ad", ad_id, window_from, window_to)
        camp_id = db.execute(text(
            "select campaign_id from naver_ad_creative_daily where ad_id=:a limit 1"
        ), {"a": ad_id}).scalar()
        camp, tgt = auto_operator.settlement_conv_counts(
            db, target_type="ad", target_id=ad_id, campaign_id=camp_id, today=TODAY)
        verdict = guardrail_gate._check_data_floor({
            "auto_exec": True, "floor_exempt": False,
            "guardrail_params": guardrail_params.get_params(db),
            "campaign_weekly_conv": camp, "target_weekly_conv": tgt,
        })
        mark = "★차단" if verdict else "통과"
        print(f"     {ad_id[-12:]}  clk={agg['clk']:>4} cost={agg['cost']:>8,} "
              f"conv={agg['conv_cnt']:>3}  캠페인conv={camp}  → {mark}")
        if verdict:
            print(f"        사유: {verdict}")

    # ── ⓒⓓ 배포 이후 실집행과 차단 일기 ────────────────────────────────────
    print("\n[ⓒⓓ] 오늘(KST) 소재 자동 실집행 / 하한 차단 일기")
    n_exec = db.execute(text(
        "select count(*) from naver_change_log where action='update_bid' and dry_run=0 "
        "and after_value is not null and entity_type='ad' and date(changed_at)=:d"
    ), {"d": TODAY.isoformat()}).scalar()
    print(f"     오늘 소재 update_bid 실집행: {n_exec}건")
    diary_rows = db.execute(text(
        "select substr(rationale,1,150) r, count(*) n, max(created_at) newest "
        "from ops_diary_entries where event_type='blocked' and rationale like '%표본 하한%' "
        "group by 1 order by 2 desc limit 8"
    )).fetchall()
    if not diary_rows:
        print("     하한 차단 일기: 0건 (아직 레인이 안 돌았거나 미배포)")
    for r, n, newest in diary_rows:
        print(f"     {n:>3}건  {newest}  {r}")

    db.close()


if __name__ == "__main__":
    main()
