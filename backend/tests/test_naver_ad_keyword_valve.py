# test_naver_ad_keyword_valve.py — D-NAO-50: entity_sync 키워드 인벤토리 diff 밸브
# ★이 파일의 존재 이유: sync_entities는 매일 07:35 도는 크론이고 naver_entity는 91,005행이다.
#   지금까지 키워드가 나타나거나 사라져도 이력이 **아무 데도** 남지 않았다(입찰가만 로깅).
#   Jino "키워드들의 history는 어디서 볼 수 있어?"의 답이 '없음'이었던 걸 메운다.
#   ★가드가 무너지면(부트스트랩/대량) 매일 91,005행이 change_log에 쏟아진다 — 입찰가 밸브와 동일 위험.
from __future__ import annotations

import json

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


# ── helpers ──
def _seed_keyword(db, *, entity_id, name="필름", bid_amt=700, status="on", synced_at=None):
    e = NaverEntity(
        entity_type="keyword", entity_id=entity_id, parent_id="grp-1",
        campaign_id="cmp-1", campaign_type="WEB_SITE", name=name,
        status=status, bid_amt=bid_amt, synced_at=synced_at or kst_now(),
    )
    db.add(e)
    db.commit()
    return e


def _kw_row(*, entity_id, name="필름", bid_amt=700, status="on", reg_tm=None):
    row = {
        "entity_type": "keyword", "entity_id": entity_id, "parent_id": "grp-1",
        "campaign_id": "cmp-1", "campaign_type": "WEB_SITE", "name": name,
        "status": status, "bid_amt": bid_amt,
    }
    if reg_tm is not None:
        row["reg_tm"] = reg_tm
    return row


def _added(db):
    return db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_keyword_added").all()


def _removed(db):
    return db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_keyword_removed").all()


# ── 1. 신규 키워드 등장 (비-부트스트랩) ──
def test_new_keyword_appears_logs_added(db):
    """기존 키워드가 1개라도 있으면(비-부트스트랩) 새 키워드는 added 1건으로 기록된다."""
    _seed_keyword(db, entity_id="nkw-old")
    rows = [_kw_row(entity_id="nkw-old"), _kw_row(entity_id="nkw-new", name="신규키워드", bid_amt=850)]
    entity_sync.sync_entities(db, rows=rows)

    logs = _added(db)
    assert len(logs) == 1
    assert logs[0].entity_id == "nkw-new"
    assert logs[0].campaign_id == "cmp-1"
    assert logs[0].before_value is None
    assert json.loads(logs[0].after_value) == {"keyword": "신규키워드", "bidAmt": 850}
    assert logs[0].dry_run is False


# ── 2. rows에서 사라진 키워드 → stale + removed ──
def test_keyword_absent_from_rows_logs_removed_and_marks_stale(db):
    _seed_keyword(db, entity_id="nkw-1", name="사라질키워드", bid_amt=700)
    entity_sync.sync_entities(db, rows=[])  # 수집에서 완전히 빠짐

    logs = _removed(db)
    assert len(logs) == 1
    assert logs[0].entity_id == "nkw-1"
    assert json.loads(logs[0].before_value) == {"keyword": "사라질키워드", "bidAmt": 700}
    assert logs[0].after_value is None

    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert e.status == "deleted"


# ── 3. API가 status=deleted 반환 → removed(그리고 status 로그 없음) ──
def test_keyword_api_returns_deleted_logs_removed_not_status(db):
    _seed_keyword(db, entity_id="nkw-1", status="on")
    entity_sync.sync_entities(db, rows=[_kw_row(entity_id="nkw-1", status="deleted")])

    assert len(_removed(db)) == 1
    # removed 경로가 external_status_change와 이중 로깅되면 안 된다(on→deleted는 lock 변화 아님).
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_status_change").count() == 0


# ── 4. 재등장 (기존 deleted → 수집 on) → added ──
def test_keyword_reappearance_logs_added(db):
    _seed_keyword(db, entity_id="nkw-1", name="부활키워드", bid_amt=650, status="deleted")
    entity_sync.sync_entities(db, rows=[_kw_row(entity_id="nkw-1", name="부활키워드", bid_amt=650, status="on")])

    logs = _added(db)
    assert len(logs) == 1
    assert logs[0].entity_id == "nkw-1"
    assert json.loads(logs[0].after_value) == {"keyword": "부활키워드", "bidAmt": 650}
    # e.status가 deleted였으므로 status 밸브는 발화하지 않아야 한다.
    assert db.query(NaverChangeLog).filter(NaverChangeLog.action == "external_status_change").count() == 0


# ── 5. 부트스트랩(빈 naver_entity) → added 0 ──
def test_bootstrap_empty_db_logs_zero_added(db):
    """새 DB 첫 sync는 91,005개 키워드를 'added'로 쏟아내면 안 된다. 부트스트랩이면 added 스킵."""
    rows = [_kw_row(entity_id=f"nkw-{i}") for i in range(3)]
    result = entity_sync.sync_entities(db, rows=rows)

    assert len(_added(db)) == 0
    assert result["keyword_added"] == 0


# ── 6. 무변동 5,000행 sync → 키워드 로그 0 (증폭 회귀) ──
def test_large_unchanged_sync_writes_zero_keyword_logs(db):
    """★prod 규모 축소판: 5,000행 무변동 → change_log 0행. 깨지면 크론이 매일 폭파한다."""
    for i in range(5000):
        db.add(NaverEntity(
            entity_type="keyword", entity_id=f"nkw-{i}", parent_id="grp-1",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name=f"kw{i}",
            status="on", bid_amt=700, synced_at=kst_now(),
        ))
    db.commit()

    rows = [_kw_row(entity_id=f"nkw-{i}", name=f"kw{i}") for i in range(5000)]
    entity_sync.sync_entities(db, rows=rows)
    assert db.query(NaverChangeLog).count() == 0


# ── 7. 대량 소실(250건) → __bulk__ 요약 1행 ──
def test_mass_removal_writes_single_bulk_row(db):
    """API 부분 장애(어떤 그룹이 빈 응답)면 수천 키워드가 stale된다. 개별 로깅하면 change_log 폭파 →
    방향당 200 초과 시 요약 1행."""
    for i in range(250):
        db.add(NaverEntity(
            entity_type="keyword", entity_id=f"nkw-{i}", parent_id="grp-1",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name=f"kw{i}",
            status="on", bid_amt=700, synced_at=kst_now(),
        ))
    db.commit()

    result = entity_sync.sync_entities(db, rows=[])  # 250개 전부 소실

    logs = _removed(db)
    assert len(logs) == 1
    assert logs[0].entity_id == "__bulk__"
    assert logs[0].campaign_id == ""
    assert logs[0].after_value is None
    payload = json.loads(logs[0].before_value)
    assert payload["bulk"] is True
    assert payload["count"] == 250
    assert len(payload["sample"]) == 20
    assert result["keyword_removed"] == 250  # 요약해도 참 카운트는 250


# ── 8. 대량 등록(250건, 비-부트스트랩) → __bulk__ 요약 1행 ──
def test_mass_addition_writes_single_bulk_row(db):
    _seed_keyword(db, entity_id="nkw-anchor")  # 비-부트스트랩 고정
    rows = [_kw_row(entity_id="nkw-anchor")] + [
        _kw_row(entity_id=f"nkw-new-{i}", name=f"new{i}", bid_amt=800) for i in range(250)
    ]
    result = entity_sync.sync_entities(db, rows=rows)

    logs = _added(db)
    assert len(logs) == 1
    assert logs[0].entity_id == "__bulk__"
    assert logs[0].before_value is None
    payload = json.loads(logs[0].after_value)
    assert payload["bulk"] is True
    assert payload["count"] == 250
    assert len(payload["sample"]) == 20
    assert result["keyword_added"] == 250


# ── 9. 캠페인/그룹 추가·삭제 → 키워드 로그 0 ──
def test_campaign_adgroup_inventory_changes_not_logged(db):
    """스코프는 keyword 전용. 캠페인/그룹 등장·소실은 로깅 대상이 아니다(YAGNI)."""
    db.add(NaverEntity(entity_type="campaign", entity_id="cmp-old", parent_id="",
                       campaign_id="cmp-old", campaign_type="WEB_SITE", name="구캠페인",
                       status="on", synced_at=kst_now()))
    db.add(NaverEntity(entity_type="adgroup", entity_id="ag-old", parent_id="cmp-old",
                       campaign_id="cmp-old", campaign_type="WEB_SITE", name="구그룹",
                       status="on", synced_at=kst_now()))
    db.commit()

    rows = [
        {"entity_type": "campaign", "entity_id": "cmp-new", "parent_id": "",
         "campaign_id": "cmp-new", "campaign_type": "WEB_SITE", "name": "신캠페인", "status": "on", "bid_amt": None},
        {"entity_type": "adgroup", "entity_id": "ag-new", "parent_id": "cmp-new",
         "campaign_id": "cmp-new", "campaign_type": "WEB_SITE", "name": "신그룹", "status": "on", "bid_amt": 500},
    ]
    entity_sync.sync_entities(db, rows=rows)

    assert len(_added(db)) == 0
    assert len(_removed(db)) == 0


# ── 10. 반환 dict가 참 카운트를 담는다 (개별 케이스) ──
def test_return_dict_carries_true_counts(db):
    _seed_keyword(db, entity_id="nkw-stay")
    _seed_keyword(db, entity_id="nkw-gone", name="소실될것", bid_amt=600)
    rows = [_kw_row(entity_id="nkw-stay"), _kw_row(entity_id="nkw-a", name="추가A"), _kw_row(entity_id="nkw-b", name="추가B")]
    result = entity_sync.sync_entities(db, rows=rows)

    assert result["keyword_added"] == 2
    assert result["keyword_removed"] == 1


# ── 11. 두 action이 EXTERNAL_DETECTION_ACTIONS에 편입 ──
def test_actions_registered_in_external_detection_set():
    from app.services.naver_ad.naver_execution_harness import EXTERNAL_DETECTION_ACTIONS

    assert "external_keyword_added" in EXTERNAL_DETECTION_ACTIONS
    assert "external_keyword_removed" in EXTERNAL_DETECTION_ACTIONS


# ── ★D-NAO-148: 키워드 등록 시각(regTm) ──────────────────────────────────────
# 배경: `external_keyword_added`는 prod 30일 95건 전부 occurred_at NULL이었다 —
#   화면이 "07-23 07:37에 추가됨"이라고 말했지만 그건 **우리가 알아챈** 시각이고,
#   실제 등록은 전날 오후였다(라이브 대조: nkw…756866 regTm 07-22 13:41:30).
# 창 = (직전 sync, 이번 sync]. 하한을 엔티티 행에서 못 얻는다 — 신규 키워드엔
#   직전 관측이 없으므로 **직전 sync 실행 시각**(기존 행들의 max synced_at)을 쓴다.
from datetime import datetime, timedelta  # noqa: E402


def _reg(dt_utc: str) -> str:
    return dt_utc


def test_added_keyword_carries_registration_time(db):
    """★신규 등록: regTm이 (직전 sync, 이번 sync] 안 → occurred_at에 그 시각(KST)이 찍힌다."""
    prev_sync = kst_now() - timedelta(hours=24)
    _seed_keyword(db, entity_id="nkw-old", synced_at=prev_sync)
    # 직전 sync(24시간 전) 이후, 지금 이전에 등록된 키워드
    reg_kst = prev_sync + timedelta(hours=6)
    reg_utc = (reg_kst - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-old"),
        _kw_row(entity_id="nkw-new", name="신규키워드", bid_amt=850, reg_tm=reg_utc),
    ])

    rows = _added(db)
    assert len(rows) == 1
    assert rows[0].entity_id == "nkw-new"
    # 초 단위 일치(마이크로초는 strftime에서 버려짐)
    assert rows[0].occurred_at == reg_kst.replace(microsecond=0)


def test_added_keyword_without_reg_tm_leaves_time_null(db):
    """regTm 미수집(구 경로·API 누락) → NULL. 화면은 '감지 시각'이라고 정직하게 말한다."""
    _seed_keyword(db, entity_id="nkw-old", synced_at=kst_now() - timedelta(hours=24))
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-old"), _kw_row(entity_id="nkw-new", name="신규"),
    ])

    rows = _added(db)
    assert len(rows) == 1 and rows[0].occurred_at is None


def test_reappeared_keyword_gets_null_not_old_creation_time(db):
    """★재등장(deleted→on)은 등록이 아니다 — 원래 생성 시각(창 밖)이 실려 와도 NULL.
    이걸 채우면 '2024년에 추가됨'이 어제 사건으로 화면에 뜬다."""
    _seed_keyword(db, entity_id="nkw-1", status="deleted",
                  synced_at=kst_now() - timedelta(hours=24))
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-1", name="부활키워드", status="on",
                reg_tm="2024-08-19T01:19:26.000Z"),
    ])

    rows = _added(db)
    assert len(rows) == 1 and rows[0].occurred_at is None


def test_added_keyword_reg_tm_after_now_is_rejected(db):
    """이번 sync 이후 시각은 창 밖 → NULL. 시계가 어긋난 값을 사실로 적지 않는다."""
    _seed_keyword(db, entity_id="nkw-old", synced_at=kst_now() - timedelta(hours=24))
    future_utc = (kst_now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-old"),
        _kw_row(entity_id="nkw-new", name="신규", reg_tm=future_utc),
    ])

    assert _added(db)[0].occurred_at is None


def test_removed_keyword_never_carries_time(db):
    """★소멸은 영구 NULL — regTm은 '언제 지웠나'에 답하지 못한다(삭제 시각은 응답에 없다)."""
    _seed_keyword(db, entity_id="nkw-1", synced_at=kst_now() - timedelta(hours=24))
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-1", status="deleted", reg_tm="2026-07-22T04:41:30.000Z"),
    ])

    rows = _removed(db)
    assert len(rows) == 1 and rows[0].occurred_at is None


def test_reg_tm_persisted_on_entity(db):
    """naver_entity.reg_tm에 원문이 보관된다 — 백필·재판정의 원천이다(불변값)."""
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-1", reg_tm="2026-07-22T04:41:30.000Z"),
    ])
    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert e.reg_tm == "2026-07-22T04:41:30.000Z"


def test_reg_tm_not_erased_by_legacy_rows(db):
    """레거시 주입 rows(reg_tm 키 없음)가 기존 생성 시각을 지우지 않는다(qi와 같은 규약)."""
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id="nkw-1", reg_tm="2026-07-22T04:41:30.000Z"),
    ])
    entity_sync.sync_entities(db, rows=[_kw_row(entity_id="nkw-1", bid_amt=900)])

    e = db.query(NaverEntity).filter(NaverEntity.entity_id == "nkw-1").one()
    assert e.reg_tm == "2026-07-22T04:41:30.000Z"
    assert e.bid_amt == 900


def test_bulk_added_summary_row_has_null_time(db):
    """대량 등록 요약행(__bulk__)은 개별 키워드가 아니므로 시각을 갖지 않는다."""
    _seed_keyword(db, entity_id="nkw-anchor", synced_at=kst_now() - timedelta(hours=24))
    reg_utc = (kst_now() - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rows = [_kw_row(entity_id="nkw-anchor")] + [
        _kw_row(entity_id=f"nkw-new-{i}", name=f"new{i}", reg_tm=reg_utc) for i in range(250)
    ]
    entity_sync.sync_entities(db, rows=rows)

    added = _added(db)
    assert len(added) == 1 and added[0].entity_id == "__bulk__"
    assert added[0].occurred_at is None


def test_bootstrap_still_logs_nothing_with_reg_tm(db):
    """★회귀: 부트스트랩 가드는 regTm이 있어도 그대로 산다(91,005행 폭주 방지)."""
    reg_utc = (kst_now() - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    entity_sync.sync_entities(db, rows=[
        _kw_row(entity_id=f"nkw-{i}", reg_tm=reg_utc) for i in range(3)
    ])
    assert _added(db) == []
