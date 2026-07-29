# test_naver_ad_external_change.py — 소재(ad) grain 외부 변경 탐지 단위 테스트 (D-NAO-127)
# 커버: editTm 앵커 판정 5단계 · before 귀속 규칙(우리 값을 외부가 덮음/되돌림) · op_type 3종 +
#   ad_edit 폴백 · 멱등 · KST 변환 · bm_diff 리플레이가 소재 행을 지우지 않는 것.
# ★재현 시나리오는 2026-07-29 실사고 그대로다: 우리가 10:58에 1,000→1,600으로 올린 폴드8와이드
#   소재를 15:39:05에 외부가 1,000으로 되돌렸고, 값만 보면 "어제와 같은 1,000"이라 무변동이다.
from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAgencyOp, NaverCampaignSettings, NaverChangeLog
from app.services.naver_ad import ad_external_change
from app.services.naver_ad.ad_external_change import parse_edit_tm, run

AD = "nad-a001-02-000000554755092"
CAMP = "cmp-a001-02-000000005925050"
GRP = "grp-a001-02-000000029854835"
NOW = datetime(2026, 7, 30, 8, 20, 0)

EDIT_PREV = "2026-07-28T01:10:00.000Z"   # 직전 관측 시점의 editTm
EDIT_OURS = "2026-07-29T01:58:00.000Z"   # 우리 쓰기(10:58 KST)
EDIT_EXT = "2026-07-29T06:39:05.000Z"    # 외부 되돌림(15:39:05 KST) — 라이브 실측값


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _prev(edit_tm=EDIT_PREV, bid=1000, ugba=False, lock=False):
    return {AD: {"edit_tm": edit_tm, "ad_bid_amt": bid,
                 "use_group_bid_amt": ugba, "ad_user_lock": lock}}


def _obs(edit_tm=EDIT_EXT, bid=1000, ugba=False, lock=False):
    return [{"ad_id": AD, "adgroup_id": GRP, "campaign_id": CAMP, "edit_tm": edit_tm,
             "ad_bid_amt": bid, "use_group_bid_amt": ugba, "ad_user_lock": lock}]


def _our_write(db, *, edit_tm, bid, ugba=False, lock=False, dry_run=False, after=True):
    """harness가 남기는 모양 그대로 — after_value는 get_ad 응답 원본의 json.dumps."""
    payload = {"nccAdId": AD, "editTm": edit_tm, "userLock": lock,
               "adAttr": {"bidAmt": bid, "useGroupBidAmt": ugba}}
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CAMP, action="update_bid",
        dry_run=dry_run, changed_at=datetime(2026, 7, 29, 10, 58),
        executed_at=datetime(2026, 7, 29, 10, 58),
        before_value=json.dumps({"bidAmt": 1000}),
        after_value=json.dumps(payload) if after else None,
    ))
    db.commit()


def _ops(db):
    return db.query(NaverAgencyOp).order_by(NaverAgencyOp.id.asc()).all()


# ── 판정 5단계 ────────────────────────────────────────────────────────────────

def test_editTm_unchanged_no_op(db):
    """③ 아무도 안 만짐 — 절대다수가 여기서 끊긴다."""
    res = run(db, prev_by_ad=_prev(), observed=_obs(edit_tm=EDIT_PREV, bid=1000), now=NOW)
    assert res["ops"] == 0 and _ops(db) == []


def test_new_ad_not_a_change(db):
    """① 직전 관측이 없는 신규 소재는 '변경'이 아니다."""
    res = run(db, prev_by_ad={}, observed=_obs(bid=1600), now=NOW)
    assert res["ops"] == 0


def test_missing_anchor_holds_judgement(db):
    """② editTm이 한쪽이라도 없으면 값이 달라도 판정 유보(반쪽 신호를 사실로 남기지 않는다)."""
    res = run(db, prev_by_ad=_prev(edit_tm=None), observed=_obs(bid=1600), now=NOW)
    assert res["ops"] == 0
    res = run(db, prev_by_ad=_prev(), observed=_obs(edit_tm=None, bid=1600), now=NOW)
    assert res["ops"] == 0


def test_our_own_write_not_flagged(db):
    """④ 마지막 편집이 우리 쓰기면(editTm 동일) 외부로 기록하지 않는다."""
    _our_write(db, edit_tm=EDIT_OURS, bid=1600)
    res = run(db, prev_by_ad=_prev(), observed=_obs(edit_tm=EDIT_OURS, bid=1600), now=NOW)
    assert res["ops"] == 0


def test_external_bid_change_recorded(db):
    """⑤ 우리 쓰기가 없는데 editTm이 전진 → 외부 입찰 변경."""
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    (op,) = _ops(db)
    assert (op.entity_type, op.op_type, op.before_value, op.after_value) == ("ad", "bid_change", "1000", "1600")
    assert op.is_exception is True and op.optimizer == "ours"
    assert op.occurred_at == datetime(2026, 7, 29, 15, 39, 5)  # editTm UTC → KST
    assert op.op_date == NOW.date()  # 감지일(브리핑이 오늘로 조회하므로)


# ── before 귀속(우리 값을 외부가 덮음/되돌림) ────────────────────────────────

def test_external_revert_of_our_write_is_detected(db):
    """★핵심 회귀: 우리 1,600 → 외부 1,000(=직전 관측값). 값만 보면 무변동인데 잡아야 한다.

    before는 직전 관측(1000)이 아니라 **우리가 써넣은 값(1600)**이어야 이력이 끊기지 않는다.
    """
    _our_write(db, edit_tm=EDIT_OURS, bid=1600)
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(edit_tm=EDIT_EXT, bid=1000), now=NOW)
    (op,) = _ops(db)
    assert (op.op_type, op.before_value, op.after_value) == ("bid_change", "1600", "1000")
    assert op.magnitude == pytest.approx(-37.5)


def test_external_overwrite_with_third_value(db):
    """우리 1,600 → 외부 900(제3의 값): before는 1,600(중간 상태를 지우지 않는다)."""
    _our_write(db, edit_tm=EDIT_OURS, bid=1600)
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=900), now=NOW)
    (op,) = _ops(db)
    assert (op.before_value, op.after_value) == ("1600", "900")


def test_stale_our_write_is_not_treated_as_the_starting_point(db):
    """★codex[P1] 회귀: 우리 쓰기가 **직전 관측보다 이전**이면 before 귀속에 쓰지 않는다.

    D-2 우리 1,600 → D-1 외부 1,200(직전 관측) → D 외부가 문구만 편집(입찰 1,200 유지).
    초판은 `ours_edit != prev_edit`만 봐서 옛 우리 값이 되살아나 `bid_change 1600→1200`을
    지어냈다(codex가 실행으로 재현). 실제로는 입찰이 안 바뀌었으므로 ad_edit이어야 한다.
    """
    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CAMP, action="update_bid", dry_run=False,
        changed_at=datetime(2026, 7, 27, 10), executed_at=datetime(2026, 7, 27, 10),
        after_value=json.dumps({"editTm": "2026-07-27T01:00:00.000Z", "userLock": False,
                                "adAttr": {"bidAmt": 1600, "useGroupBidAmt": False}}),
    ))
    db.commit()
    run(db,
        prev_by_ad={AD: {"edit_tm": "2026-07-28T01:00:00.000Z", "ad_bid_amt": 1200,
                         "use_group_bid_amt": False, "ad_user_lock": False}},
        observed=[{"ad_id": AD, "adgroup_id": GRP, "campaign_id": CAMP,
                   "edit_tm": "2026-07-29T01:00:00.000Z", "ad_bid_amt": 1200,
                   "use_group_bid_amt": False, "ad_user_lock": False}],
        now=NOW)
    (op,) = _ops(db)
    assert op.op_type == "ad_edit"  # 입찰 무변동 — 유령 bid_change 금지


def test_uncertain_write_is_recorded_but_not_escalated(db):
    """codex[P2]: 결과 불명(`[실행 실패]`) 쓰기가 낀 구간이면 기록은 남기되 예외 승격은 보류."""
    from app.services.naver_ad.naver_execution_harness import WRITE_FAILURE_MARKER

    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CAMP, action="update_bid", dry_run=False,
        outcome="failed", rationale=f"{WRITE_FAILURE_MARKER} WriteVerificationError: timeout",
        changed_at=datetime(2026, 7, 29, 15, 0), executed_at=datetime(2026, 7, 29, 15, 0),
    ))
    db.commit()
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    (op,) = _ops(db)
    assert op.op_type == "bid_change" and op.is_exception is False


def test_guard_blocked_write_does_not_suppress_escalation(db):
    """`[실행 불가]`(가드 거부)는 PUT을 보내지도 않았다 → 불확실이 아니다(예외 승격 유지)."""
    from app.services.naver_ad.naver_execution_harness import GUARD_BLOCK_MARKER

    db.add(NaverChangeLog(
        entity_type="ad", entity_id=AD, campaign_id=CAMP, action="update_bid", dry_run=False,
        outcome="failed", rationale=f"{GUARD_BLOCK_MARKER} 쿨다운",
        changed_at=datetime(2026, 7, 29, 15, 0), executed_at=datetime(2026, 7, 29, 15, 0),
    ))
    db.commit()
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    (op,) = _ops(db)
    assert op.is_exception is True


def test_unparseable_edit_tm_still_records_distinct_events(db):
    """codex[P2]: occurred_at이 NULL이어도 값이 다르면 다른 사건으로 남는다(영구 미탐 금지)."""
    run(db, prev_by_ad=_prev(edit_tm="A", bid=1000), observed=_obs(edit_tm="B", bid=1600), now=NOW)
    run(db, prev_by_ad=_prev(edit_tm="B", bid=1600), observed=_obs(edit_tm="C", bid=1200), now=NOW)
    ops = _ops(db)
    assert [o.after_value for o in ops] == ["1600", "1200"]
    assert all(o.occurred_at is None for o in ops)
    # 같은 사건 재관측은 여전히 1건(after_value 폴백 키)
    run(db, prev_by_ad=_prev(edit_tm="B", bid=1600), observed=_obs(edit_tm="C", bid=1200), now=NOW)
    assert len(_ops(db)) == 2


def test_dry_run_and_failed_writes_are_not_our_evidence(db):
    """dry-run·실패(after 없음) 행은 '우리 쓰기'로 인정하지 않는다(기존 규약과 동일)."""
    _our_write(db, edit_tm=EDIT_EXT, bid=1600, dry_run=True)
    _our_write(db, edit_tm=EDIT_EXT, bid=1600, after=False)
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    (op,) = _ops(db)
    assert op.before_value == "1000"  # 우리 쓰기로 인정 안 됨 → 출발값은 직전 관측


# ── op_type 3종 + 폴백 ────────────────────────────────────────────────────────

def test_bid_mode_flip_recorded(db):
    """useGroupBidAmt=true 전환 = 우리 소재 입찰 레버가 죽는 사건 → 예외 승격."""
    run(db, prev_by_ad=_prev(ugba=False), observed=_obs(ugba=True), now=NOW)
    (op,) = _ops(db)
    assert (op.op_type, op.before_value, op.after_value) == ("bid_mode_flip", "False", "True")
    assert op.is_exception is True


def test_status_flip_recorded(db):
    run(db, prev_by_ad=_prev(lock=False), observed=_obs(lock=True), now=NOW)
    (op,) = _ops(db)
    assert (op.op_type, op.before_value, op.after_value) == ("status_flip", "on", "off")


def test_multiple_fields_one_edit_two_ops(db):
    run(db, prev_by_ad=_prev(bid=1000, lock=False), observed=_obs(bid=1600, lock=True), now=NOW)
    assert {o.op_type for o in _ops(db)} == {"bid_change", "status_flip"}


def test_untracked_field_edit_falls_back_to_ad_edit(db):
    """추적 3필드는 그대로인데 editTm만 전진 = 다른 필드 변경. 지어내지 않고 '편집됨'만 남긴다."""
    run(db, prev_by_ad=_prev(), observed=_obs(), now=NOW)
    (op,) = _ops(db)
    assert op.op_type == "ad_edit" and op.is_exception is False
    assert op.after_value == "07-29 15:39:05"


def test_none_fields_are_not_changes(db):
    """미수집(None)은 변경이 아니다 — 유령 생성 금지."""
    run(db, prev_by_ad=_prev(bid=None), observed=_obs(bid=1600), now=NOW)
    (op,) = _ops(db)
    assert op.op_type == "ad_edit"  # 입찰은 비교 불가 → 폴백만


# ── 멱등 · 유틸 ───────────────────────────────────────────────────────────────

def test_idempotent_on_reobservation(db):
    """같은 편집을 두 번 관측해도 두 번 기록되지 않는다((entity_id, op_type, occurred_at))."""
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    assert len(_ops(db)) == 1


def test_parse_edit_tm_utc_to_kst():
    assert parse_edit_tm("2026-07-29T06:39:05.000Z") == datetime(2026, 7, 29, 15, 39, 5)
    assert parse_edit_tm("2026-07-29T06:39:05+00:00") == datetime(2026, 7, 29, 15, 39, 5)
    assert parse_edit_tm("2026-07-29T06:39:05") is None  # 오프셋 없음 = 해석 불가(추정 금지)
    assert parse_edit_tm(None) is None and parse_edit_tm("쓰레기") is None


def test_optimizer_defaults_to_none_when_unmanaged(db):
    obs = _obs(bid=1600)
    obs[0]["campaign_id"] = "cmp-unknown"
    run(db, prev_by_ad=_prev(bid=1000), observed=obs, now=NOW)
    (op,) = _ops(db)
    assert op.optimizer == "none"


# ── 다른 프로듀서(bm_diff)와의 공존 ───────────────────────────────────────────

def test_bm_diff_replay_does_not_delete_ad_rows(db):
    """bm_diff의 멱등 재생성이 같은 날 소재 관측을 쓸어가면 안 된다(프로듀서 2개 공존)."""
    from app.services.naver_ad.bm_diff import detect_agency_ops

    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    assert len(_ops(db)) == 1
    # D-1 스냅샷이 없어 bootstrap 스킵되는 경로여도 delete는 돌지 않아야 하고,
    # 스냅샷이 있는 경로에서도 ad 행은 남아야 한다 → 실제 delete 문을 타는 경로로 확인한다.
    _seed_snapshots(db)
    detect_agency_ops(db, op_date=NOW.date())
    assert [o.entity_type for o in _ops(db) if o.entity_type == "ad"] == ["ad"]


def _seed_snapshots(db):
    """bm_diff가 delete 문까지 도달하도록 D-1·D 스냅샷을 최소로 심는다."""
    from app.models import NaverEntitySnapshot

    for d, bid in ((date(2026, 7, 29), 500), (NOW.date(), 500)):
        db.add(NaverEntitySnapshot(
            snapshot_date=d, entity_type="adgroup", entity_id=GRP, parent_id=CAMP,
            campaign_id=CAMP, campaign_type="SHOPPING", optimizer="ours",
            name="g", status="on", bid_amt=bid, synced_at=datetime.combine(d, datetime.min.time()),
        ))
    db.commit()


def test_module_writes_nothing_to_change_log(db):
    """★관측 전용 계약: change_log에 쓰면 소재 쿨다운(compute_change_cadence)이 오염된다."""
    before = db.query(NaverChangeLog).count()
    run(db, prev_by_ad=_prev(bid=1000), observed=_obs(bid=1600), now=NOW)
    assert db.query(NaverChangeLog).count() == before
    assert "NaverChangeLog(" not in _source()


def _source() -> str:
    import inspect
    return inspect.getsource(ad_external_change)
