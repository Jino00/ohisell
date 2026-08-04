# test_naver_ad_entity_bid_valve.py — D-NAO-47 Task1~2: entity_sync 입찰가 diff 밸브
# ★이 파일의 존재 이유: sync_entities는 매일 07:35 도는 크론이고 naver_entity는 91,005행이다.
#   "무변동 행 미로깅" 가드가 무너지면 매일 91,005행이 naver_change_log에 쌓여 DB가 죽는다.
#   타입 불일치(API가 "700"(str), DB가 700(int))가 그 가드를 무너뜨리는 유일한 경로라
#   _norm_bid 정규화를 반드시 거친다.
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverChangeLog, NaverEntity
from app.services.naver_ad import entity_sync
from app.utils.kst import kst_now


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ── Task 1: _norm_bid ──
def test_norm_bid_normalizes_str_and_int_to_same_value():
    """★API가 "700"(str), DB가 700(int)이어도 같은 값으로 봐야 한다.
    이게 깨지면 91,005행이 매일 '변경됨'으로 오판정된다."""
    assert entity_sync._norm_bid(700) == entity_sync._norm_bid("700") == 700


def test_norm_bid_handles_none_and_empty():
    assert entity_sync._norm_bid(None) is None
    assert entity_sync._norm_bid("") is None


def test_norm_bid_handles_float_and_decimal_string():
    """네이버가 700.0 같은 실수를 줘도 int로 접는다."""
    assert entity_sync._norm_bid(700.0) == 700
    assert entity_sync._norm_bid("700.0") == 700


def test_norm_bid_returns_none_on_garbage_rather_than_raising():
    """파싱 불가 값은 예외 대신 None — 크론이 죽으면 안 된다(fail-safe)."""
    assert entity_sync._norm_bid("N/A") is None
    assert entity_sync._norm_bid({}) is None


# ── Task 2: _log_external_bid_change 밸브 ──
def _seed_entity(db, *, bid_amt, status="on", synced_at=None):
    e = NaverEntity(
        entity_type="keyword", entity_id="nkw-1", parent_id="grp-1",
        campaign_id="cmp-1", campaign_type="WEB_SITE", name="필름",
        status=status, bid_amt=bid_amt, synced_at=synced_at or kst_now(),
    )
    db.add(e)
    db.commit()
    return e


def _rows(db, *, adgroup_bid=None, keyword_bid=700, status="ELIGIBLE"):
    """sync_entities에 넣을 rows — 키워드 1개짜리 최소 형태."""
    return [{
        "entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
        "status": "on", "bid_amt": keyword_bid,
    }]


def test_no_log_when_bid_unchanged(db):
    """★핵심 회귀 테스트: 무변동 = 로깅 0. 이게 깨지면 91,005행/일."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_when_bid_unchanged_across_types(db):
    """★타입만 다르고 값이 같으면 무변동이다 — DB 700(int) vs API "700"(str)."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid="700"))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_logs_when_bid_actually_changed(db):
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").all()
    assert len(logs) == 1
    assert json.loads(logs[0].before_value) == {"bidAmt": 700}
    assert json.loads(logs[0].after_value) == {"bidAmt": 900}
    assert logs[0].entity_id == "nkw-1"
    assert logs[0].campaign_id == "cmp-1"
    assert logs[0].dry_run is False


def test_bid_amt_is_actually_persisted_after_change(db):
    """밸브를 달아도 기존 동작(입찰가 갱신)은 그대로여야 한다."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert e.bid_amt == 900


def test_no_log_when_previous_bid_is_none(db):
    """신규 관측(옛값 없음)은 '변경'이 아니다."""
    _seed_entity(db, bid_amt=None)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_when_new_bid_is_none(db):
    """수집 누락(새값 없음)도 '변경'이 아니다 — 이걸 로깅하면 API 장애 시 91k행이 쏟아진다."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=None))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_log_for_deleted_entity(db):
    _seed_entity(db, bid_amt=700, status="deleted")
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_skips_log_when_change_is_our_own_recent_write(db):
    """우리가 방금 바꾼 건 '외부 변경'이 아니다 — _log_external_status_change와 동일 계약.
    after_value의 키는 camelCase 'bidAmt'(writer가 네이버 재조회 응답을 그대로 dumps)."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(minutes=30),  # 직전 관측 이후
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_logs_when_our_write_is_older_than_last_observation(db):
    """우리 쓰기가 직전 관측보다 *이전*이면, 방향이 같아도 외부 변경으로 기록한다.
    (status 쪽 원 버그의 대칭 — 우리 인상 → 외부 인하 → 외부 재인상 시퀀스 방어)"""
    prev_sync = kst_now() - timedelta(minutes=10)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(hours=5),  # 직전 관측보다 오래됨
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1


def test_dry_run_our_write_does_not_suppress_log(db):
    """dry_run 기록은 실제 쓰기가 아니다 — 억제하면 안 된다."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=True,  # ← dry
        changed_at=kst_now() - timedelta(minutes=30),
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1


def test_status_and_bid_change_together_produce_two_logs(db):
    """상태와 입찰가가 같이 바뀌면 각각 1행씩 — 두 밸브는 독립이다."""
    _seed_entity(db, bid_amt=700, status="on")
    rows = [{
        "entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
        "status": "off", "bid_amt": 900,
    }]
    entity_sync.sync_entities(db, rows=rows)

    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 1
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_status_change").count() == 1


def test_large_sync_with_no_bid_changes_writes_zero_logs(db):
    """★prod 규모 축소판 회귀: 5,000행 무변동 sync → change_log 0행.
    (prod는 91,005행. 이 테스트가 깨지면 크론이 매일 change_log를 폭파한다.)"""
    for i in range(5000):
        db.add(NaverEntity(
            entity_type="keyword", entity_id=f"nkw-{i}", parent_id="grp-1",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name=f"kw{i}",
            status="on", bid_amt=700, synced_at=kst_now(),
        ))
    db.commit()

    rows = [{
        "entity_type": "keyword", "entity_id": f"nkw-{i}", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": f"kw{i}",
        "status": "on", "bid_amt": "700",  # ★str — 타입만 다르고 값은 같다
    } for i in range(5000)]

    entity_sync.sync_entities(db, rows=rows)
    assert db.query(NaverChangeLog).count() == 0


# ── codex[P2] 2026-07-17: 되돌림 레이스 + 파싱 실패 관측성 ──
def test_logs_when_external_reverts_our_bid_change(db):
    """★codex[P2]: 우리가 700→900을 쓴 뒤 외부가 900→700으로 되돌리면 old==new(700==700)라
    무변동으로 보이지만 실제로는 외부가 우리 변경을 무효화한 것이다. 안 남기면 change_log가
    "우리가 900으로 바꿈"에서 멈춰 현재 값이 900인 줄로 읽힌다(실제 700).
    03(MOP) vs 04(우리) 대결에서 정확히 측정하려는 시나리오."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(minutes=30),  # 직전 관측 이후 = 우리가 방금 씀
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    # 외부가 900 → 700으로 되돌림. 관측값은 옛 관측값(700)과 같다.
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").all()
    assert len(logs) == 1
    # 실제 전이는 our_target(900) → 700 이다. 700→700이 아니다.
    assert json.loads(logs[0].before_value) == {"bidAmt": 900}
    assert json.loads(logs[0].after_value) == {"bidAmt": 700}
    assert "되돌림" in logs[0].rationale


def test_no_revert_log_when_our_write_predates_last_observation(db):
    """우리 쓰기가 직전 관측보다 오래됐으면 이미 관측에 반영된 것 — 되돌림이 아니다.
    (이게 없으면 옛 우리 쓰기 때문에 무변동 행이 매일 로깅된다 = 쓰기 폭증 재발)"""
    prev_sync = kst_now() - timedelta(minutes=10)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(hours=5),  # 직전 관측보다 오래됨
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=700))
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_warns_when_bid_cannot_be_normalized(db, caplog):
    """★codex[P2]: 값이 있는데 정규화 실패면 그 행은 영영 로깅 안 된다. 조용히 넘기지 말고
    시끄럽게 — 네이버 형식 변경을 사람이 알아챌 유일한 신호다."""
    _seed_entity(db, bid_amt=700)
    with caplog.at_level("WARNING"):
        entity_sync.sync_entities(db, rows=_rows(db, keyword_bid="1,450"))

    assert any("정규화 실패" in r.message for r in caplog.records)
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_no_warning_when_bid_is_legitimately_none(db, caplog):
    """수집 누락(None)은 '파싱 실패'가 아니라 정상 경로다. None까지 경고하면 네이버 장애 때
    91,005건 경고가 쏟아져 로그가 못 쓰게 된다 — 경고의 신호 가치를 지킨다."""
    _seed_entity(db, bid_amt=700)
    with caplog.at_level("WARNING"):
        entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=None))

    assert not any("정규화 실패" in r.message for r in caplog.records)
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").count() == 0


def test_our_bid_writes_loaded_once_not_per_row(db):
    """★codex[P2]의 순진한 수정(행마다 change_log 조회)은 91,005 쿼리/일 = 읽기 폭증이다.
    루프 전 1회 적재를 계약으로 고정한다 — 이 테스트가 깨지면 읽기 폭증이 돌아온 것."""
    for i in range(300):
        db.add(NaverEntity(
            entity_type="keyword", entity_id=f"nkw-{i}", parent_id="grp-1",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name=f"kw{i}",
            status="on", bid_amt=700, synced_at=kst_now(),
        ))
    db.commit()

    calls = {"n": 0}
    real = entity_sync._load_our_bid_writes

    def counting(dbs):
        calls["n"] += 1
        return real(dbs)

    entity_sync._load_our_bid_writes = counting
    try:
        rows = [{
            "entity_type": "keyword", "entity_id": f"nkw-{i}", "parent_id": "grp-1",
            "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": f"kw{i}",
            "status": "on", "bid_amt": 700,
        } for i in range(300)]
        entity_sync.sync_entities(db, rows=rows)
    finally:
        entity_sync._load_our_bid_writes = real

    assert calls["n"] == 1, f"300행 sync에 {calls['n']}회 적재 — 루프 안에서 부르고 있다"


def test_logs_our_target_as_before_when_external_overwrites_to_third_value(db):
    """★codex[P2] R2: 3값 케이스. 저장된 old=700, 우리가 900으로 씀, 외부가 800으로 덮음.
    old(700)!=new(800)이라 '되돌림'이 아니지만, change_log엔 이미 우리 700→900 행이 있으므로
    외부 행은 900→800이어야 이력이 이어진다. 700→800으로 적으면 900이라는 중간 상태가
    지워져 오귀속된다."""
    prev_sync = kst_now() - timedelta(hours=2)
    _seed_entity(db, bid_amt=700, synced_at=prev_sync)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-1", campaign_id="cmp-1",
        action="update_bid", dry_run=False,
        changed_at=kst_now() - timedelta(minutes=30),  # 직전 관측 이후 = 우리가 방금 씀
        after_value=json.dumps({"bidAmt": 900}),
    ))
    db.commit()

    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=800))  # 외부가 제3의 값으로

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").all()
    assert len(logs) == 1
    assert json.loads(logs[0].before_value) == {"bidAmt": 900}, "우리가 써넣은 900이 출발값이어야"
    assert json.loads(logs[0].after_value) == {"bidAmt": 800}
    assert "덮어씀" in logs[0].rationale


def test_plain_external_change_still_uses_old_bid_as_before(db):
    """우리 쓰기가 없으면(our_target None) 출발값은 그대로 old_bid — (c) 수정이 (a)를 깨지 않았는지."""
    _seed_entity(db, bid_amt=700)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))

    logs = db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_bid_change").all()
    assert len(logs) == 1
    assert json.loads(logs[0].before_value) == {"bidAmt": 700}
    assert "덮어씀" not in logs[0].rationale
    assert "되돌림" not in logs[0].rationale


# ── D-NAO-147: external_* 감지 행에 발생 시각(editTm) 붙이기 ─────────────
# 배경(라이브 실측 2026-08-04): 10:49:25에 꺼진 광고그룹이 change_log에 18:33:51(감지 시각)로
#   남았다 — 같은 순간 naver_entity.edit_tm은 10:49를 갖고 있었는데도.
# 창 = (직전 관측, 이번 관측]. bm_diff._occurred_at과 같은 규약.
from datetime import datetime  # noqa: E402

PREV_SYNC = datetime(2026, 8, 3, 7, 35, 0)   # 직전 관측
NOW_SYNC = datetime(2026, 8, 4, 7, 35, 0)    # 이번 관측
EDIT_IN = "2026-08-03T05:32:37.000Z"         # = 08-03 14:32:37 KST (창 안)
EDIT_IN_KST = datetime(2026, 8, 3, 14, 32, 37)
EDIT_OLD = "2026-03-30T06:42:23.000Z"        # 창보다 훨씬 과거(실측: 캠페인 31건이 이 상태)
EDIT_FUTURE = "2026-08-04T09:00:00.000Z"     # = 08-04 18:00 KST, 이번 관측 이후


def _kw_rows(bid, edit_tm):
    return [{
        "entity_type": "keyword", "entity_id": "nkw-1", "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": "필름",
        "status": "on", "bid_amt": bid, "edit_tm": edit_tm,
    }]


def _one_log(db, action):
    rows = db.query(NaverChangeLog).filter(NaverChangeLog.action == action).all()
    assert len(rows) == 1, f"{action}: {len(rows)}건"
    return rows[0]


def test_external_bid_change_carries_occurred_at_from_edit_tm(db):
    """editTm이 창 안이면 발생 시각이 change_log에 실린다."""
    _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    entity_sync.sync_entities(db, rows=_kw_rows(900, EDIT_IN))

    assert _one_log(db, "external_bid_change").occurred_at == EDIT_IN_KST


def test_external_bid_change_leaves_occurred_at_null_when_edit_tm_stale(db):
    """editTm이 직전 관측보다 과거면 이 변경의 시각이 아니다 → NULL(지어내지 않는다)."""
    _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    entity_sync.sync_entities(db, rows=_kw_rows(900, EDIT_OLD))

    assert _one_log(db, "external_bid_change").occurred_at is None


def test_occurred_at_null_when_edit_tm_after_this_observation(db):
    """이번 관측 이후의 editTm은 방금 본 값일 수 없다 → NULL(시계 어긋남 방어).

    ★sync_entities 경로가 아니라 순수 함수로 검증한다: sync_entities는 내부에서 `kst_now()`를
    잡으므로 "이번 관측 시각"을 테스트가 고정할 수 없고, 벽시계에 따라 결과가 갈린다."""
    e = _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    assert entity_sync.external_occurred_at(e, EDIT_FUTURE, NOW_SYNC) is None


def test_external_bid_change_without_edit_tm_still_logs(db):
    """editTm이 아예 없어도(레거시 rows) 탐지 자체는 종전대로 — 시각만 비어 있다."""
    _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    entity_sync.sync_entities(db, rows=_rows(db, keyword_bid=900))

    assert _one_log(db, "external_bid_change").occurred_at is None


def test_external_status_change_carries_occurred_at(db):
    """상태(On/Off) 축도 같다 — 08-04 10:49 그룹 정지 사건의 형태."""
    _seed_entity(db, bid_amt=700, status="on", synced_at=PREV_SYNC)
    rows = _kw_rows(700, EDIT_IN)
    rows[0]["status"] = "off"
    entity_sync.sync_entities(db, rows=rows)

    assert _one_log(db, "external_status_change").occurred_at == EDIT_IN_KST


def test_edit_tm_is_persisted_on_entity(db):
    """editTm 원문이 naver_entity에 남아야 다음 회차·스냅샷이 쓸 수 있다."""
    _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    entity_sync.sync_entities(db, rows=_kw_rows(900, EDIT_IN))

    assert db.query(NaverEntity).one().edit_tm == EDIT_IN


def test_external_occurred_at_window_is_half_open(db):
    """경계: 직전 관측과 **같은 시각**은 창 밖(그때 이미 봤다), 이번 관측과 같으면 창 안."""
    e = _seed_entity(db, bid_amt=700, synced_at=PREV_SYNC)
    assert entity_sync.external_occurred_at(e, "2026-08-02T22:35:00.000Z", NOW_SYNC) is None  # == PREV
    assert entity_sync.external_occurred_at(e, "2026-08-03T22:35:00.000Z", NOW_SYNC) == NOW_SYNC  # == NOW
