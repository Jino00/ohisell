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
