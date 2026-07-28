# diary.py — 운영 일기 기록층 SA (D-NAO-54 P1, docs/PLAN_naver-ad-diary-wisdom.md §P1)
# 역할: ①env_snapshot_sa — 집행/차단 순간의 환경(요일·휴일·계절·아이폰 출시 오프셋·소진
#   페이싱·avg_rank)을 순수 조회로 스냅샷. ②write_diary_entry(diary_writer_sa) — 독립 세션에
#   ops_diary_entries 1행을 짧은 트랜잭션으로 기록(fail-open: 일기 실패가 집행을 절대 막지
#   않는다). naver_execution_harness/auto_operator의 훅이 이 두 SA를 호출한다.
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import holidays
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NaverHourlySnapshot, NaverKeywordHourly, OpsDiaryEntry
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# actor(일기) 값 — ops_diary_entries.actor(String(12), CHECK 없음 — probe 추가는 마이그레이션
# 불필요). daily/hourly/console/delegation/system + probe(D-NAO-58 CD2 클릭 탐침).
ACTOR_DAILY = "daily"
ACTOR_HOURLY = "hourly"
ACTOR_CONSOLE = "console"
ACTOR_DELEGATION = "delegation"
ACTOR_SYSTEM = "system"
ACTOR_PROBE = "probe"  # D-NAO-58 CD2: 클릭 탐침(밴드 사각지대 능동 상향) 집행 주체
ACTOR_EXPLORE = "explore"  # B-X BX3(D-NAO-70·71): 저볼륨 그룹 탐색 UP(핫셋 미달 능동 상향) 집행 주체
ACTOR_PACING = "pacing"  # BP(D-NAO-102): 예산 페이싱 자동 증액·익일 원복 집행 주체

# observe(관측) 상태 토큰(D-NAO-85 관측 갭②) — write_observe_if_changed가 event_type="observe" 행의
# action 컬럼에 저장하는 안정 키. 시간당 탐색 레인이 스텝 없이 관측만 하는 정상 상태를 저소음
# (상태 변화 시 1행)으로 남긴다. 매시 같은 그룹에서 반복 발동하는 분기라 무조건 기록하면 소음 폭주.
OBSERVE_STOP = "observe_stop"        # ladder_judgment stop_observe(클릭 도착/밴드 도달/흐름 생존 → 상향 정지)
OBSERVE_BAND = "observe_band"        # 적응 스텝 소실(밴드 내/미세) — 상향 없음(관찰)
OBSERVE_CEILING = "observe_ceiling"  # 상한 클램프로 스텝 소실 — 종료

# approval_source(naver_proposals) → actor 매핑. auto_operator.APPROVAL_SOURCE_DAILY/HOURLY/
# PROBE의 실제 값('auto_op'/'auto_op_hr'/'probe_op', codex 2R[P1-1] 단축)을 여기 문자열
# 리터럴로 둔다 — auto_operator를 import하면 순환(auto_operator가 diary를 import)이라 값만 복제한다.
_APPROVAL_SOURCE_TO_ACTOR = {
    "auto_op": ACTOR_DAILY,      # APPROVAL_SOURCE_DAILY
    "auto_op_hr": ACTOR_HOURLY,  # APPROVAL_SOURCE_HOURLY
    "probe_op": ACTOR_PROBE,     # APPROVAL_SOURCE_PROBE (D-NAO-58 CD2)
    "revert_op": ACTOR_PROBE,    # APPROVAL_SOURCE_REVERT (D-NAO-58 CD3 되돌림 — 같은 주체)
    "explore_op": ACTOR_EXPLORE,  # APPROVAL_SOURCE_EXPLORE (B-X BX3 탐색 UP — exploration.py 소유 상수 값 복제)
    "pace_op": ACTOR_PACING,     # APPROVAL_SOURCE_PACING (BP D-NAO-102 예산 페이싱)
}

# 공휴일 판정 — sales_velocity_estimator._KR_HOLIDAYS와 동일 라이브러리(holidays.SouthKorea).
# 그 모듈의 _classify_day는 private이라 재사용 불가 → 라이브러리를 직접 쓴다(참조만 남김).
_KR_HOLIDAYS = holidays.SouthKorea()

# 아이폰 출시일 정적 config(app/data/iphone_launch_dates.json) — 모듈 레벨 1회 로드 캐시.
_IPHONE_LAUNCH_DATES: list[date] | None = None


def _iphone_launch_dates() -> list[date]:
    """config JSON의 출시일 목록(캐시). 파일 부재/파싱 실패는 빈 목록(fail-soft — 오프셋 None)."""
    global _IPHONE_LAUNCH_DATES
    if _IPHONE_LAUNCH_DATES is None:
        path = Path(__file__).resolve().parents[2] / "data" / "iphone_launch_dates.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _IPHONE_LAUNCH_DATES = [date.fromisoformat(s) for s in raw.get("launch_dates", [])]
        except Exception as e:  # noqa: BLE001 — config 문제로 일기 전체가 죽으면 안 됨
            log.warning("diary: 아이폰 출시일 config 로드 실패(오프셋 None으로 진행): %s", e)
            _IPHONE_LAUNCH_DATES = []
    return _IPHONE_LAUNCH_DATES


def _season_of(month: int) -> str:
    """월 → 계절(3-5 spring / 6-8 summer / 9-11 autumn / 12,1,2 winter)."""
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def _iphone_offset_days(today: date) -> int | None:
    """가장 가까운 출시일과의 부호 있는 일수(양수=출시 후 경과, 음수=출시 전). config 비면 None."""
    launches = _iphone_launch_dates()
    if not launches:
        return None
    return min(((today - d).days for d in launches), key=abs)


def _spend_pacing_pct(db: Session, campaign_id: str, today: date) -> float | None:
    """캠페인 당일 소진율(%) = 당일 최신 스냅샷 cost / daily_budget × 100. 스냅샷 부재·예산
    미확보(None/0)면 None(추정으로 채우지 않음 — auto_operator._latest_hourly_snapshot_fields와
    동일 소스). 예외도 None."""
    try:
        latest = (
            db.query(NaverHourlySnapshot)
            .filter(NaverHourlySnapshot.campaign_id == campaign_id, NaverHourlySnapshot.ad_date == today)
            .order_by(NaverHourlySnapshot.snapshot_hour.desc())
            .first()
        )
        if latest is None or not latest.daily_budget or latest.daily_budget <= 0:
            return None
        return round(float(latest.cost) / float(latest.daily_budget) * 100.0, 2)
    except Exception as e:  # noqa: BLE001
        log.warning("diary: spend_pacing_pct 조회 실패(None): campaign=%s: %s", campaign_id, e)
        return None


def _latest_keyword_avg_rank(db: Session, target_id: str) -> float | None:
    """keyword/adgroup 대상의 최신 avg_rank(naver_keyword_hourly — entity_id는 nkw-…와
    grp-…(쇼핑그룹, D-NAO-46②) 둘 다 축적됨). 없으면 None. 예외도 None.

    ★adgroup 포함 근거(2026-07-18): 첫 해석문이 "env.avg_rank null인데 시간당밴드는 자체
    가중 avg_rank를 씀" 불일치를 지적 — 원래 keyword만 조회하던 협소 설계였고, 쇼핑그룹
    (17E 등) 일기 행에 순위 맥락이 비면 P3 지혜 채굴에서 순위×환경 패턴을 잃는다."""
    try:
        row = (
            db.query(NaverKeywordHourly.avg_rank)
            .filter(NaverKeywordHourly.entity_id == target_id, NaverKeywordHourly.avg_rank.isnot(None))
            .order_by(NaverKeywordHourly.ad_date.desc(), NaverKeywordHourly.hour.desc())
            .first()
        )
        return float(row[0]) if row and row[0] is not None else None
    except Exception as e:  # noqa: BLE001
        log.warning("diary: avg_rank 조회 실패(None): target=%s: %s", target_id, e)
        return None


def env_snapshot_sa(
    db: Session, now: datetime, campaign_id: str,
    target_type: str | None = None, target_id: str | None = None,
) -> dict:
    """집행/차단 순간의 환경 스냅샷(순수 조회 SA) — 어떤 필드든 조회 실패는 None으로 남긴다
    (전체가 예외로 죽지 않게 각 필드 독립 방어). now는 호출자의 KST now(weekday/season 기준)."""
    today = now.date()
    return {
        "weekday": today.weekday(),  # 0=월 (KST now 기준)
        "is_kr_holiday": today in _KR_HOLIDAYS,
        "season": _season_of(today.month),
        "iphone_launch_offset_days": _iphone_offset_days(today),
        "spend_pacing_pct": _spend_pacing_pct(db, campaign_id, today),
        "avg_rank": (
            _latest_keyword_avg_rank(db, target_id)
            if target_type in ("keyword", "adgroup") and target_id else None
        ),
    }


def actor_from_approval_source(approval_source: str | None) -> str:
    """naver_proposals.approval_source → 일기 actor. daily/hourly 레인 상수는 각 라벨로,
    NULL(수동 콘솔 승인)은 console, 그 외(delegation 등)는 값 그대로."""
    if approval_source is None:
        return ACTOR_CONSOLE
    return _APPROVAL_SOURCE_TO_ACTOR.get(approval_source, approval_source)


def _new_diary_session(db: Session) -> Session:
    """호출자 db와 같은 엔진에 새 세션(독립 트랜잭션) — auto_operator._auto_operate_now의
    db.get_bind() 독립 커넥션 패턴과 동형. 레인/harness 트랜잭션을 오염시키지 않고, 짧은
    insert+commit으로 SQLite 쓰기락을 오래 잡지 않는다(D-NAO-46② 교훈). 프로덕션은 ohisell.db,
    테스트는 caller의 엔진을 그대로 따라간다(SessionLocal 직접 참조 회피 — prod 파일 사이드이펙트 X)."""
    return Session(bind=db.get_bind(), autoflush=False, autocommit=False)


def _insert_diary_entry(
    session: Session, event_type: str, campaign_id: str, *, actor: str,
    target_type: str | None, target_id: str | None, adgroup_id: str | None,
    action: str | None, before_value: str | None, after_value: str | None,
    rationale: str | None, source_ref: int | None, now: datetime,
) -> None:
    """주어진 세션에 일기 1행을 env 스냅샷과 함께 insert+commit(순수 실행 — fail-open은 호출자 담당).
    write_diary_entry / write_observe_if_changed가 공유(env 조회·행 구성 로직 단일화)."""
    try:
        env = env_snapshot_sa(session, now, campaign_id, target_type, target_id)
    except Exception as e:  # noqa: BLE001 — env 실패해도 일기 행은 남긴다(컬럼 None)
        log.warning("diary: env_snapshot 실패(env None으로 기록): campaign=%s: %s", campaign_id, e)
        env = {}
    entry = OpsDiaryEntry(
        event_type=event_type, campaign_id=campaign_id or "", actor=actor,
        target_type=target_type, target_id=target_id, adgroup_id=adgroup_id,
        action=action, before_value=before_value, after_value=after_value,
        rationale=rationale, source_ref=source_ref,
        weekday=env.get("weekday"), is_kr_holiday=env.get("is_kr_holiday"),
        season=env.get("season"), iphone_launch_offset_days=env.get("iphone_launch_offset_days"),
        spend_pacing_pct=env.get("spend_pacing_pct"), avg_rank=env.get("avg_rank"),
    )
    session.add(entry)
    session.commit()


def write_diary_entry(
    db: Session, event_type: str, campaign_id: str, *, actor: str,
    target_type: str | None = None, target_id: str | None = None, adgroup_id: str | None = None,
    action: str | None = None, before_value: str | None = None, after_value: str | None = None,
    rationale: str | None = None, source_ref: int | None = None, now: datetime | None = None,
) -> None:
    """운영 일기 1행 기록(diary_writer_sa) — 독립 세션·짧은 트랜잭션·fail-open.

    ★일기 기록 실패는 집행을 절대 막지 않는다: 전체를 try/except로 감싸 예외를 밖으로 던지지
    않고 log.warning만 남긴다(env 스냅샷 실패도 env 컬럼 None으로라도 기록). 훅 호출부는 이
    fail-open 계약에 의존해 별도 try 없이 호출한다."""
    try:
        now = now or kst_now()
        session = _new_diary_session(db)
        try:
            _insert_diary_entry(
                session, event_type, campaign_id, actor=actor, target_type=target_type,
                target_id=target_id, adgroup_id=adgroup_id, action=action,
                before_value=before_value, after_value=after_value, rationale=rationale,
                source_ref=source_ref, now=now,
            )
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 — fail-open: 일기 실패가 집행을 막으면 안 됨
        log.warning("diary: write_diary_entry 실패(fail-open, 집행 계속): event=%s campaign=%s: %s",
                    event_type, campaign_id, e)


def write_observe_if_changed(
    db: Session, campaign_id: str, *, actor: str, adgroup_id: str, state: str,
    rationale: str | None = None, target_type: str | None = None,
    target_id: str | None = None, now: datetime | None = None,
) -> bool:
    """관측(observe) 1행을 '상태 변화가 있을 때만' 기록한다(저소음, D-NAO-85 관측 갭②).

    시간당 탐색 레인의 stop_observe·밴드 도달·상한 클램프 분기는 매시 같은 그룹에서 반복
    발동한다 — 무조건 기록하면 하루 수십~수백 행 소음(_record_blocked의 소음 차단 원칙 위반)이라
    그동안 완전 침묵이었다(17프로 그룹 10회 연속 무기록 → 라이브 원인 규명에 표적 시뮬 필요).

    dedup 규칙: 같은 (campaign_id, adgroup_id, actor)의 **가장 최근 일기 행**이 이미 같은 state의
    observe면 skip(그 관측 상태가 계속 지속 중 = 소음). 직전 행이 실쓰기/차단/다른 관측 상태거나
    아예 없으면(=상태 변화) 1행 기록한다. state는 action 컬럼에 저장하는 안정 토큰(OBSERVE_*).
    → 정확히 '상태 전이' 시점에만 남는다(연속 stop_observe 10시간 = 1행).

    반환: 실제로 기록하면 True, 소음 dedup으로 skip하면 False. fail-open(조회/기록 실패 시 예외를
    던지지 않고 False 반환 — 레인 집행에 무영향)."""
    try:
        now = now or kst_now()
        session = _new_diary_session(db)
        try:
            last = session.execute(
                select(OpsDiaryEntry.event_type, OpsDiaryEntry.action)
                .where(
                    OpsDiaryEntry.campaign_id == (campaign_id or ""),
                    OpsDiaryEntry.adgroup_id == adgroup_id,
                    OpsDiaryEntry.actor == actor,
                )
                .order_by(OpsDiaryEntry.created_at.desc(), OpsDiaryEntry.id.desc())
                .limit(1)
            ).first()
            if last is not None and last.event_type == "observe" and last.action == state:
                return False  # 같은 관측 상태 지속 → 소음 dedup(기록 안 함)
            _insert_diary_entry(
                session, "observe", campaign_id, actor=actor, target_type=target_type,
                target_id=target_id, adgroup_id=adgroup_id, action=state,
                before_value=None, after_value=None, rationale=rationale, source_ref=None, now=now,
            )
            return True
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 — fail-open: 관측 기록 실패가 집행을 막으면 안 됨
        log.warning("diary: write_observe_if_changed 실패(fail-open): state=%s campaign=%s adgroup=%s: %s",
                    state, campaign_id, adgroup_id, e)
        return False
