# reflection_health.py — reflection_health_sa (D-NAO-228, 계약 docs/PLAN_naver-m5-reflection-visibility.md)
# 역할: 08:35 반성 루프가 «돌았는가»를 날짜별로 판정해 사람이 읽는 한 줄로 낸다.
#
# ★왜 있나 (계약 §3): 2026-07-18~08-22 36일 중 반성 행은 17일뿐이었는데, 스케줄러 로그는
#   성공·재료없음 skip·LLM 실패를 **전부 `'ok'` 한 줄**로 적고 있었다. 그래서 20일 침묵이
#   아무에게도 안 보였다(교훈 #319·#321과 같은 모양의 네 번째 재현).
# ★이 모듈이 «안» 하는 것: 반성을 돌리지 않는다. 재료가 없는 날 안 도는 것은 정상이다
#   (북극성 §5-2 — 학습 주기는 관측 신호의 주기를 넘을 수 없다). 이 모듈은 «재료가 없어서
#   안 돈 날»과 «고장 나서 안 돈 날»을 사람이 구분할 수 있게만 한다.
# 읽기 + diary 상태행 쓰기(diary.write_diary_entry 재사용)만. 마이그레이션 불필요.
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import OpsDiaryEntry
from app.services.naver_ad.diary import write_diary_entry
from app.services.naver_ad.diary_outcome import EVENT_TYPES, _kst_date
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 반성 산출물 행(성공 시) — diary_reflection.build_reflection이 쓰는 것과 같은 action.
REFLECTION_ACTION = "daily_reflection"
# ★반성이 «안 써진» 실행의 흔적(D-NAO-228 신설). 성공한 날엔 안 쓴다 — 산출물 행이 곧 증거다.
RUN_STATUS_ACTION = "daily_reflection_run"

# _gather가 보는 창(diary_reflection._gather와 같아야 한다 — 어긋나면 이 판정이 거짓말을 한다).
MATERIAL_OFFSETS = (1, 2, 8)

# 반성 크론 발화 시각(scheduler_service의 "35 8 * * *"와 같아야 한다).
# ★적대 리뷰 1R P1-2: 이 시각 «전»에 조회하면 오늘이 결번으로 세어지고, 재료가 있으면
#   「원인은 DB 밖에 있다」는 확정 문구와 함께 경고색이 켜졌다. 아직 돌 차례가 안 온 것을
#   판정 결과로 적는 것은 이 모듈의 판단기준(§2-4 판정불능을 판정불능으로)의 정반대다.
REFLECTION_CRON_TIME = time(8, 35)

_STATE_OK = "ok"
_STATE_SKIPPED = "skipped_no_material"
_STATE_FAILED = "failed"
_STATE_UNRESOLVED = "unresolved"
_STATE_PENDING = "pending"  # 아직 발화 시각 전 — 결번이 아니다

_EVIDENCE_GAP = (
    "이 배선(D-NAO-228) 이전 날짜의 «행 없음 + 재료 있음»은 DB만으로 실패·미상을 못 가른다 — "
    "당시 실행은 상태 행을 남기지 않았고 pm2 로그는 rotation으로 일부 소실됐다. "
    "계약 docs/PLAN_naver-m5-reflection-visibility.md §3이 로그 실측으로 그 구간을 분해해 보존한다. "
    "★배선 이후 날짜는 매 실행이 상태 행을 남기므로 ok/skipped/failed가 DB만으로 구분된다."
)


def record_run_status(
    db: Session, status: str, *, detail: str | None = None,
    entries: int | None = None, now: datetime | None = None,
) -> None:
    """반성이 «안 써진» 실행의 사유를 일기 1행으로 남긴다(fail-open — write_diary_entry 계약).

    status: 'skipped_no_material' | 'failed' | 그 외 자유 문자열(그대로 보존).
    성공한 실행에는 부르지 않는다 — daily_reflection 산출물 행 자체가 증거이기 때문이다.
    """
    payload = {"status": status, "detail": detail, "entries": entries}
    write_diary_entry(
        db, "observe", "", actor="system", action=RUN_STATUS_ACTION,
        rationale=json.dumps(payload, ensure_ascii=False), now=now,
    )


def _kst_dates(rows: list[OpsDiaryEntry]) -> dict[date, OpsDiaryEntry]:
    """KST 날짜 → 그 날의 마지막 행(같은 날 여러 행이면 최신이 이긴다)."""
    out: dict[date, OpsDiaryEntry] = {}
    for e in rows:
        if e.created_at is None:
            continue
        out[_kst_date(e.created_at)] = e
    return out


def _parse_status(entry: OpsDiaryEntry) -> tuple[str, str | None]:
    """상태 행 rationale(JSON) 판독. 깨졌으면 unresolved — 지어내지 않는다."""
    try:
        payload = json.loads(entry.rationale or "")
    except Exception:  # noqa: BLE001 — 형식이 깨진 옛 행
        return _STATE_UNRESOLVED, "상태 행 판독 실패"
    status = payload.get("status")
    detail = payload.get("detail")
    if status == _STATE_SKIPPED:
        return _STATE_SKIPPED, detail
    if status == _STATE_FAILED:
        return _STATE_FAILED, detail
    return _STATE_UNRESOLVED, f"알 수 없는 status={status!r}"


def build_reflection_health(
    db: Session, *, now: datetime | None = None, start: date | None = None,
) -> dict:
    """날짜별 반성 상태 판정(읽기 전용).

    start 미지정 시 창의 시작 = 반성이 처음 발화한 날(그 이전은 기제가 없어 결번이 아니다).
    """
    now = now or kst_now()
    today = now.date()

    observe_rows = (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.event_type == "observe",
            OpsDiaryEntry.action.in_([REFLECTION_ACTION, RUN_STATUS_ACTION]),
            OpsDiaryEntry.created_at.isnot(None),
        )
        .order_by(OpsDiaryEntry.created_at)
        .all()
    )
    reflected = _kst_dates([e for e in observe_rows if e.action == REFLECTION_ACTION])
    statuses = _kst_dates([e for e in observe_rows if e.action == RUN_STATUS_ACTION])

    if start is None:
        start = min(reflected) if reflected else today
    if start > today:
        start = today

    # 재료(EVENT_TYPES) 날짜 집합 — 창 시작 D-8까지 거슬러 봐야 첫날 판정이 성립한다.
    material_lower_utc = (
        datetime.combine(start - timedelta(days=max(MATERIAL_OFFSETS) + 1), datetime.min.time())
        - timedelta(hours=9)
    )
    material_rows = (
        db.query(OpsDiaryEntry.created_at)
        .filter(
            OpsDiaryEntry.event_type.in_(EVENT_TYPES),
            OpsDiaryEntry.created_at.isnot(None),
            OpsDiaryEntry.created_at >= material_lower_utc,
        )
        .all()
    )
    material_days = {_kst_date(r[0]) for r in material_rows}

    days: list[dict] = []
    counts = {_STATE_OK: 0, _STATE_SKIPPED: 0, _STATE_FAILED: 0,
              _STATE_UNRESOLVED: 0, _STATE_PENDING: 0}
    cursor = start
    while cursor <= today:
        has_material = any((cursor - timedelta(days=off)) in material_days for off in MATERIAL_OFFSETS)
        if cursor in reflected:
            state, source, detail = _STATE_OK, "reflection_row", None
        elif cursor in statuses:
            state, detail = _parse_status(statuses[cursor])
            source = "status_row"
        elif cursor == today and now.time() < REFLECTION_CRON_TIME:
            # 오늘 08:35이 아직 안 왔다 — 결번도 실패도 아니다(적대 리뷰 1R P1-2).
            state, source = _STATE_PENDING, "not_due"
            detail = f"{REFLECTION_CRON_TIME.strftime('%H:%M')} 크론 미도래 — 아직 판정 대상이 아니다"
        elif not has_material:
            state, source = _STATE_SKIPPED, "inferred"
            detail = f"D-{'·D-'.join(str(o) for o in MATERIAL_OFFSETS)} 재료 0건"
        else:
            state, source = _STATE_UNRESOLVED, "inferred"
            detail = "재료는 있었으나 반성 행도 상태 행도 없다 — 원인은 DB 밖(로그)에 있다"
        counts[state] += 1
        days.append({
            "date": cursor.isoformat(), "state": state, "source": source,
            "has_material": has_material, "detail": detail,
        })
        cursor += timedelta(days=1)

    ok_days = [d["date"] for d in days if d["state"] == _STATE_OK]
    last_success = ok_days[-1] if ok_days else None
    gap = (today - date.fromisoformat(last_success)).days if last_success else None
    # 결번에서 pending은 뺀다 — 아직 돌 차례가 안 온 날을 «빠진 날»로 세면 매일 아침 +1 부푼다.
    missing = len(days) - counts[_STATE_OK] - counts[_STATE_PENDING]

    headline = (
        f"반성 최근 성공 {last_success or '없음'}"
        + (f"({gap}일 전)" if gap else "(오늘)" if last_success else "")
        + f" · 창 {start.isoformat()}~{today.isoformat()} {len(days)}일 중 결번 {missing}일"
        + f" = 재료없음 {counts[_STATE_SKIPPED]} / 실패 {counts[_STATE_FAILED]}"
        + f" / 미상 {counts[_STATE_UNRESOLVED]}"
        + (f" (오늘은 {REFLECTION_CRON_TIME.strftime('%H:%M')} 미도래)" if counts[_STATE_PENDING] else "")
    )

    return {
        "window": {"start": start.isoformat(), "end": today.isoformat(), "days": len(days)},
        "last_success_kst": last_success,
        "gap_days_since_success": gap,
        "missing_days": missing,
        "counts": counts,
        "headline": headline,
        "days": days,
        "evidence_gap": _EVIDENCE_GAP,
        "material_note": (
            "재료 = 실집행 일기(execute·blocked·reject·kill_switch)의 D-1·D-2·D-8. "
            "L3 정지 중에는 재료가 없어 반성이 안 도는 것이 정상이다(북극성 §5-2)."
        ),
    }
