# test_naver_reflection_health.py — D-NAO-228 「반성 침묵 표면화」
# 계약: docs/PLAN_naver-m5-reflection-visibility.md §5 ⓐⓑⓒ
#
# ★이 테스트가 지키는 것은 «값이 계산되는가»가 아니라 «사람이 그걸 보는가»다(전역 §4 ★조항).
#   실제 사고(계약 §3): 2026-07-18~08-22 결번 19일 동안 스케줄러 로그는 성공·재료없음 skip·
#   LLM 실패를 전부 'ok'로 적었고, 20일 침묵이 아무에게도 안 보였다. 그래서 여기엔
#   ①로그 3분기 ②날짜별 판정 ③**성적표 응답까지 실려 나가는지**가 전부 들어 있다.
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import OpsDiaryEntry
from app.services.naver_ad import reflection_health, reflection_loop, wisdom_scorecard

NOW = datetime(2026, 8, 22, 8, 35)  # KST
TODAY = NOW.date()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)  # prod와 동일(§database.py:16)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _utc_for(d: date) -> datetime:
    """_kst_date(created_at)==d 가 되도록 UTC created_at(=KST 12:00)을 만든다."""
    return datetime.combine(d, time(3, 0))


def _row(db, d: date, *, event_type: str, action: str, rationale: str | None = None):
    e = OpsDiaryEntry(
        created_at=_utc_for(d), event_type=event_type, campaign_id="",
        action=action, actor="system", rationale=rationale,
    )
    db.add(e)
    db.flush()
    return e


def _material(db, d: date, event_type: str = "execute"):
    """실집행 일기 1건 — 반성의 «재료»."""
    return _row(db, d, event_type=event_type, action="bid_up")


def _reflection(db, d: date):
    return _row(db, d, event_type="observe", action=reflection_health.REFLECTION_ACTION,
                rationale="관찰 서술문")


# ── ⓒ 로그 3분기 정직화 ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "refl, expected_status, expect_row, expect_row_status",
    [
        ({"written": True, "entries": 3}, "ok", False, None),
        ({"skipped": "no_entries"}, "skipped:no_entries", True, "skipped_no_material"),
        ({"error": "claude CLI 오류(코드 1): You've hit your limit", "entries": 42}, "failed", True, "failed"),
        # catch-up 이중 방지는 결번이 아니다 — 그 날엔 산출물 행이 이미 있다.
        ({"skipped": "already_exists"}, "skipped:already_exists", False, None),
    ],
)
def test_stage_status_distinguishes_three_outcomes(db, monkeypatch, refl, expected_status,
                                                   expect_row, expect_row_status):
    """★초판은 넷 다 'ok'였다 — 그래서 결번 19일이 안 보였다(계약 §3)."""
    monkeypatch.setattr(reflection_loop, "backfill_outcomes", lambda db, now=None: {"scored": 0})
    monkeypatch.setattr(reflection_loop, "build_reflection", lambda db, now=None: refl)

    result = reflection_loop.run_daily_reflection(db, now=NOW)

    assert result["stage_status"]["daily_reflection"] == expected_status
    rows = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == reflection_health.RUN_STATUS_ACTION).all()
    assert bool(rows) is expect_row
    if expect_row:
        assert json.loads(rows[0].rationale)["status"] == expect_row_status


def test_exception_path_also_records_failed(db, monkeypatch):
    """예외로 죽는 경로도 상태 행을 남긴다(초판은 로그만 남기고 끝났다)."""
    monkeypatch.setattr(reflection_loop, "backfill_outcomes", lambda db, now=None: {"scored": 0})

    def _boom(db, now=None):
        raise RuntimeError("터짐")

    monkeypatch.setattr(reflection_loop, "build_reflection", _boom)
    result = reflection_loop.run_daily_reflection(db, now=NOW)

    assert result["stage_status"]["daily_reflection"] == "failed"
    rows = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.action == reflection_health.RUN_STATUS_ACTION).all()
    assert len(rows) == 1 and json.loads(rows[0].rationale)["status"] == "failed"


def test_status_row_failure_does_not_kill_the_job(db, monkeypatch):
    """상태 행 기록이 실패해도 잡은 계속 돈다(반성은 관찰 전용 — fail-open 계약)."""
    monkeypatch.setattr(reflection_loop, "backfill_outcomes", lambda db, now=None: {"scored": 0})
    monkeypatch.setattr(reflection_loop, "build_reflection", lambda db, now=None: {"skipped": "no_entries"})

    def _boom(*a, **kw):
        raise RuntimeError("일기 테이블 잠김")

    monkeypatch.setattr(reflection_loop, "record_run_status", _boom)
    result = reflection_loop.run_daily_reflection(db, now=NOW)
    assert result["stage_status"]["daily_reflection"] == "skipped:no_entries"


# ── ⓑ 날짜별 판정 ──────────────────────────────────────────────────────────────
def test_classifies_no_material_as_skipped_not_failure(db):
    """★재료가 없어서 안 도는 것은 정상이다(북극성 §5-2) — 고장으로 세면 M4 대기 중 내내 빨간불."""
    _reflection(db, TODAY - timedelta(days=3))  # 창 시작
    health = reflection_health.build_reflection_health(db, now=NOW)

    assert health["counts"]["ok"] == 1
    assert health["counts"]["skipped_no_material"] == 3  # 재료 0건인 나머지 3일
    assert health["counts"]["failed"] == 0
    assert health["counts"]["unresolved"] == 0
    assert health["last_success_kst"] == (TODAY - timedelta(days=3)).isoformat()
    assert health["gap_days_since_success"] == 3


def test_material_present_but_no_row_is_unresolved_not_ok(db):
    """재료가 있었는데 반성도 상태 행도 없으면 «미상»이다 — 침묵을 성공으로 적지 않는다."""
    _reflection(db, TODAY - timedelta(days=2))
    _material(db, TODAY - timedelta(days=2))  # ⇒ D-1(어제)·D-2(오늘) 재료가 된다

    health = reflection_health.build_reflection_health(db, now=NOW)
    by_date = {d["date"]: d for d in health["days"]}

    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert by_date[yesterday]["state"] == "unresolved"
    assert by_date[yesterday]["has_material"] is True
    assert by_date[TODAY.isoformat()]["state"] == "unresolved"
    assert health["counts"]["unresolved"] == 2


def test_recorded_status_row_beats_inference(db):
    """상태 행이 있으면 추론보다 그것을 믿는다 — 배선 이후 날짜는 DB만으로 구분된다."""
    _reflection(db, TODAY - timedelta(days=2))
    _material(db, TODAY - timedelta(days=2))
    _row(db, TODAY - timedelta(days=1), event_type="observe",
         action=reflection_health.RUN_STATUS_ACTION,
         rationale=json.dumps({"status": "failed", "detail": "hit your limit", "entries": 12}))

    health = reflection_health.build_reflection_health(db, now=NOW)
    by_date = {d["date"]: d for d in health["days"]}
    yesterday = (TODAY - timedelta(days=1)).isoformat()

    assert by_date[yesterday]["state"] == "failed"
    assert by_date[yesterday]["source"] == "status_row"
    assert by_date[yesterday]["detail"] == "hit your limit"
    assert health["counts"]["failed"] == 1


def test_broken_status_row_is_unresolved_not_ok(db):
    """판독 불가한 상태 행을 성공으로 세지 않는다(전역 §3 추정 금지)."""
    _reflection(db, TODAY - timedelta(days=1))
    _material(db, TODAY - timedelta(days=1))
    _row(db, TODAY, event_type="observe", action=reflection_health.RUN_STATUS_ACTION,
         rationale="JSON 아님")

    health = reflection_health.build_reflection_health(db, now=NOW)
    by_date = {d["date"]: d for d in health["days"]}
    assert by_date[TODAY.isoformat()]["state"] == "unresolved"


def test_today_before_cron_time_is_pending_not_missing(db):
    """★적대 리뷰 1R P1-2: 08:35 «전»에 조회하면 오늘이 결번으로 세어지고 경고가 켜졌다.

    아직 돌 차례가 안 온 것을 판정 결과로 적으면, M4로 L3이 재개된 뒤 매일 아침
    오탐 경고가 켜진다 — 「정상 침묵과 고장을 구분한다」는 목표의 거울상 실패다.
    """
    _reflection(db, TODAY - timedelta(days=1))
    _material(db, TODAY - timedelta(days=1))  # 오늘의 D-1 재료 = 있음

    before = reflection_health.build_reflection_health(db, now=datetime.combine(TODAY, time(7, 0)))
    by_date = {d["date"]: d for d in before["days"]}

    assert by_date[TODAY.isoformat()]["state"] == "pending"
    assert by_date[TODAY.isoformat()]["source"] == "not_due"
    assert before["counts"]["unresolved"] == 0
    assert before["missing_days"] == 0, "미도래를 결번으로 세면 매일 아침 +1 부푼다"
    assert "08:35 미도래" in before["headline"]


def test_after_cron_time_the_same_day_is_judged(db):
    """반대쪽 — 발화 시각이 지났는데 아무 흔적이 없으면 그때는 «미상»이 맞다."""
    _reflection(db, TODAY - timedelta(days=1))
    _material(db, TODAY - timedelta(days=1))

    after = reflection_health.build_reflection_health(db, now=datetime.combine(TODAY, time(9, 0)))
    by_date = {d["date"]: d for d in after["days"]}

    assert by_date[TODAY.isoformat()]["state"] == "unresolved"
    assert after["missing_days"] == 1


def test_d8_only_material_is_not_counted_as_no_material(db):
    """★생존 변이 L9 방어: has_material에서 D-8을 빼도 아무 테스트가 안 죽었다.

    D-8 재료로만 살아나는 날이 실재한다 — prod에서 08-11의 execute 1건이
    08-12(D-1)·08-13(D-2)·08-19(D-8) 세 날의 반성을 살렸다(계약 §3의 지문).
    상수만 검사하는 테스트는 «사용처»를 안 지킨다.
    """
    _reflection(db, TODAY - timedelta(days=9))
    _material(db, TODAY - timedelta(days=9))  # ⇒ D-8 기준일은 (TODAY-1)

    health = reflection_health.build_reflection_health(db, now=NOW)
    by_date = {d["date"]: d for d in health["days"]}
    d8_day = (TODAY - timedelta(days=1)).isoformat()

    assert by_date[d8_day]["has_material"] is True, "D-8 재료를 안 보면 이 날이 «재료없음»으로 샌다"
    assert by_date[d8_day]["state"] == "unresolved"


def test_material_window_matches_gather_offsets():
    """★판정 창이 diary_reflection._gather와 어긋나면 이 판정이 통째로 거짓말이 된다."""
    assert reflection_health.MATERIAL_OFFSETS == (1, 2, 8)


def test_empty_db_does_not_crash(db):
    health = reflection_health.build_reflection_health(db, now=NOW)
    assert health["last_success_kst"] is None
    assert health["window"]["days"] == 1


# ── ⓐ 표면: 성적표 응답까지 실려 나가는가 ─────────────────────────────────────────
def test_scorecard_payload_carries_reflection_health(db):
    """★표면 변이 방어: 성적표 응답에서 이 블록이 빠지면 Jino 화면에서 사라진다.

    지혜 0건이어도 반드시 실려야 한다 — 성적표가 비었을 때 「지혜가 없어서」인지
    「반성이 죽어서」인지 구분하는 게 이 블록의 존재 이유이기 때문이다.
    """
    _reflection(db, TODAY - timedelta(days=3))

    payload = wisdom_scorecard.build(db)

    assert "reflection_health" in payload, "성적표 응답에 reflection_health가 없다 — 표면이 끊겼다"
    health = payload["reflection_health"]
    assert health["headline"], "화면에 그대로 뿌릴 한 줄(headline)이 비어 있다"
    assert "반성 최근 성공" in health["headline"]
    assert "결번" in health["headline"]
    for key in ("ok", "skipped_no_material", "failed", "unresolved"):
        assert key in health["counts"]
    assert health["evidence_gap"], "배선 이전 구간의 한계를 산출물이 스스로 밝혀야 한다"
