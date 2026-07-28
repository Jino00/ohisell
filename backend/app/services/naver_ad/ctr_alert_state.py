# ctr_alert_state.py — 소재 CTR 경보의 "어제도 그랬나" 상태 SA (D-NAO-103).
#   역할(단일 책임): naver_ctr_alert_log를 읽어 신규 진입 / 만성을 가르고, 오늘 판정을 기록한다.
#   판정 자체(무엇이 경보인가)는 ctr_alert가, 메시지 조립은 ctr_alert_briefing이 한다.
#
#   ★기록 대상은 발화한 것이 아니라 **판정된 것 전부**다. 억제된 만성 건을 안 남기면 다음날
#   "전일 집합에 없음 = 신규"로 되살아나 억제가 매일 무효화된다(설계상 가장 쉬운 함정).
#   ★날짜 축은 as_of_date(=detect_ctr_alerts의 D0=today−1)다. 브리핑이 하루 걸러 돌아도
#   "전일"의 의미가 흔들리지 않는다(created_at은 UTC라 계산에 절대 쓰지 않는다).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCtrAlertLog

log = logging.getLogger(__name__)

_STREAK_MAX = 3650  # 방어적 상한(연속 판정 누적이 무한히 커지지 않게)


def _key(alert: dict) -> tuple[str, str]:
    return (alert["campaign_id"], alert["adgroup_id"])


def previous_state(db: Session, as_of: date) -> dict[tuple[str, str], int]:
    """전일(as_of−1) 판정 집합 → {(campaign_id, adgroup_id): streak_days}.

    행이 없으면 빈 dict = 전부 신규 진입 취급(첫 배포일엔 그날 판정 전부가 한 번 발화하고,
    다음날부터 억제가 걸린다 — 상태가 없는데 "만성"이라고 단정하지 않는다)."""
    prev_day = as_of - timedelta(days=1)
    rows = (
        db.query(NaverCtrAlertLog.campaign_id, NaverCtrAlertLog.adgroup_id,
                 NaverCtrAlertLog.streak_days)
        .filter(NaverCtrAlertLog.as_of_date == prev_day)
        .all()
    )
    return {(str(c), str(g)): int(s or 1) for c, g, s in rows}


def classify(
    db: Session, alerts: list[dict], as_of: date,
) -> tuple[list[dict], list[dict], dict[tuple[str, str], int]]:
    """경보 목록을 (신규 진입, 만성, streak맵)으로 가른다.

    신규 = 전일 판정 집합에 없던 그룹(= 오늘 처음 들어옴, streak 1).
    만성 = 전일에도 있던 그룹(streak = 전일 streak + 1).
    입력 순서를 보존한다(브리핑의 결정적 출력)."""
    prev = previous_state(db, as_of)
    new_alerts: list[dict] = []
    chronic: list[dict] = []
    streaks: dict[tuple[str, str], int] = {}
    for a in alerts:
        k = _key(a)
        if k in prev:
            streaks[k] = min(prev[k] + 1, _STREAK_MAX)
            chronic.append(a)
        else:
            streaks[k] = 1
            new_alerts.append(a)
    return new_alerts, chronic, streaks


def record(
    db: Session, alerts: list[dict], *, as_of: date,
    streaks: dict[tuple[str, str], int], notified_keys: set[tuple[str, str]],
    now: datetime | None = None,
) -> int:
    """오늘 판정 전부를 naver_ctr_alert_log에 upsert(같은 as_of 재실행 시 덮어씀).

    fail-open: 기록 실패가 브리핑/일 레인을 막지 않는다(diary.write_diary_entry 관례 계승).
    실패 시 억제 상태가 하루 리셋될 뿐 — 알림이 한 번 더 오는 쪽이 안전한 방향이다.
    반환: 기록된 행 수(실패 시 0)."""
    if not alerts:
        return 0
    try:
        existing = {
            (str(r.campaign_id), str(r.adgroup_id)): r
            for r in db.query(NaverCtrAlertLog).filter(NaverCtrAlertLog.as_of_date == as_of).all()
        }
        written = 0
        for a in alerts:
            k = _key(a)
            row = existing.get(k)
            if row is None:
                row = NaverCtrAlertLog(
                    as_of_date=as_of, campaign_id=a["campaign_id"], adgroup_id=a["adgroup_id"],
                )
                db.add(row)
                existing[k] = row
            row.window = str(a.get("window", ""))[:8]
            row.imp = int(a.get("imp", 0))
            row.clk = int(a.get("clk", 0))
            row.avg_rank = a.get("avg_rank")
            row.expected_clk = a.get("expected_clk")
            row.streak_days = int(streaks.get(k, 1))
            row.notified = k in notified_keys
            if now is not None and row.created_at is None:
                row.created_at = now
            written += 1
        db.commit()
        return written
    except Exception as e:  # noqa: BLE001 — 상태 기록 실패는 브리핑/일 레인과 분리(fail-open)
        db.rollback()
        log.warning("ctr_alert_state: 경보 이력 기록 실패(fail-open): %s", e)
        return 0
