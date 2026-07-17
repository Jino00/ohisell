# backfill_diary_from_changelog.py — naver_change_log 실집행/차단 행을 ops_diary_entries로 소급
#   생성하는 1회성 백필(D-NAO-54 P3 "백필 일기" 재료). diary 기록층(P1)이 배선되기 전에 쌓인
#   과거 실집행·[실행 불가] 차단에 대해, 그 순간의 달력 환경(요일·휴일·계절·아이폰 오프셋)을
#   정확히 재구성해 일기 행을 만든다. 멱등(source_ref 존재 시 skip) + dry-run 기본(--apply로 실쓰기).
#
# ★시간계 주의(라이브 실측): naver_change_log.executed_at/changed_at은 execute()가 now=kst_now()로
#   찍어 **KST-naive**이지만, OpsDiaryEntry.created_at은 server_default(func.now())라 **UTC** 관례다
#   ([[sqlite-server-default-now-is-utc]]). 라이브 diary 훅도 같은 집행에서 created_at=UTC·env=KST로
#   남긴다. 그래서 여기선 created_at = executed_at(KST) − 9h 로 변환해 라이브가 남겼을 UTC 값을
#   그대로 재구성하고, 달력 env는 KST 날짜(executed_at.date())로 뽑는다. 두 시간계를 섞으면 P2/P3의
#   _kst_date(created_at)=created_at+9h 가 하루 어긋난다.
#
# 실행: cd backend && python scripts/backfill_diary_from_changelog.py [--apply]
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import NaverChangeLog, NaverProposal, OpsDiaryEntry  # noqa: E402
from app.services.naver_ad import diary  # noqa: E402
from app.services.naver_ad.naver_execution_harness import (  # noqa: E402
    EXTERNAL_DETECTION_ACTIONS,
    GUARD_BLOCK_MARKER,
    WRITE_FAILURE_MARKER,
)

_KST_TO_UTC = timedelta(hours=9)


def _classify(rationale: str | None) -> str | None:
    """change_log 행 → diary event_type. [실행 불가]=blocked, 그 외=execute. [실행 실패](실쓰기
    도중 오류)는 라이브 execute 훅이 성공 때만 남기므로 대응 diary 이벤트가 없다 → None(skip)."""
    r = rationale or ""
    if GUARD_BLOCK_MARKER in r:
        return "blocked"
    if WRITE_FAILURE_MARKER in r:
        return None
    return "execute"


def _env_for(kst_date) -> dict:
    """KST 달력일로 환경 재구성(diary 모듈 헬퍼 재사용 — 단일 진실). spend_pacing_pct/avg_rank는
    당시 실측 불가라 None(추정 금지 — P1 원칙)."""
    return {
        "weekday": kst_date.weekday(),
        "is_kr_holiday": kst_date in diary._KR_HOLIDAYS,
        "season": diary._season_of(kst_date.month),
        "iphone_launch_offset_days": diary._iphone_offset_days(kst_date),
    }


def backfill(db: Session, *, apply: bool) -> dict:
    """멱등 백필. dry-run(기본)은 카운트만, --apply만 실제 insert."""
    proposals = {p.id: p for p in db.query(NaverProposal).all()}
    existing_refs = {
        row[0] for row in db.query(OpsDiaryEntry.source_ref).filter(OpsDiaryEntry.source_ref.isnot(None)).all()
    }
    rows = (
        db.query(NaverChangeLog)
        .filter(NaverChangeLog.dry_run.is_(False))
        .order_by(NaverChangeLog.id)
        .all()
    )
    totals = {"scanned": len(rows), "execute": 0, "blocked": 0,
              "skipped_existing": 0, "skipped_external": 0, "skipped_failure": 0, "skipped_no_time": 0}
    for cl in rows:
        if cl.id in existing_refs:  # 라이브 훅 또는 이전 백필이 이미 남김(멱등)
            totals["skipped_existing"] += 1
            continue
        if cl.action in EXTERNAL_DETECTION_ACTIONS:  # 외부 감지 = 우리가 한 일 아님
            totals["skipped_external"] += 1
            continue
        event_type = _classify(cl.rationale)
        if event_type is None:
            totals["skipped_failure"] += 1
            continue
        kst_dt = cl.executed_at or cl.changed_at
        if kst_dt is None:
            totals["skipped_no_time"] += 1
            continue

        env = _env_for(kst_dt.date())
        proposal = proposals.get(cl.proposal_id) if cl.proposal_id else None
        actor = diary.actor_from_approval_source(proposal.approval_source if proposal else None)
        if apply:
            db.add(OpsDiaryEntry(
                created_at=kst_dt - _KST_TO_UTC,  # KST → UTC(라이브 diary 관례 일치)
                event_type=event_type, campaign_id=cl.campaign_id or "", actor=actor,
                target_type=cl.entity_type, target_id=cl.entity_id, adgroup_id=None,
                action=cl.action, before_value=cl.before_value, after_value=cl.after_value,
                rationale=cl.rationale, source_ref=cl.id,
                weekday=env["weekday"], is_kr_holiday=env["is_kr_holiday"],
                season=env["season"], iphone_launch_offset_days=env["iphone_launch_offset_days"],
                spend_pacing_pct=None, avg_rank=None,
            ))
        totals[event_type] += 1
    if apply:
        db.commit()
    return totals


def main() -> None:
    ap = argparse.ArgumentParser(description="naver_change_log → ops_diary_entries 소급 백필(D-NAO-54 P3)")
    ap.add_argument("--apply", action="store_true", help="실제 insert(생략 시 dry-run 카운트만)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        totals = backfill(db, apply=args.apply)
    finally:
        db.close()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[backfill_diary_from_changelog] {mode} — {totals}")


if __name__ == "__main__":
    main()
