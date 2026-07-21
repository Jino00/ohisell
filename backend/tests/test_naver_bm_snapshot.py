# test_naver_bm_snapshot.py — BM 벤치마크 레이어 Phase 1 단위 테스트 (SA-1, D-NAO-78)
# 커버: snapshot_entities(upsert 멱등·중복없음, keyword_count/avg_bid 집계 정확성, optimizer
#   조인 none/ours/mop, WEB_SITE만 키워드 집계·타 유형 NULL), run_bm_layer fail-open.
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverEntity, NaverEntitySnapshot
from app.services.naver_ad import bm_harness
from app.services.naver_ad.bm_snapshot import snapshot_entities

SDATE = date(2026, 7, 22)


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


def _ent(db, entity_type, entity_id, *, parent_id="", campaign_id="", campaign_type="", name="", status="on", bid_amt=None):
    db.add(NaverEntity(
        entity_type=entity_type, entity_id=entity_id, parent_id=parent_id,
        campaign_id=campaign_id, campaign_type=campaign_type, name=name, status=status, bid_amt=bid_amt,
    ))


def _seed(db):
    """cmp-web(WEB_SITE, ours) / cmp-shop(SHOPPING, mop) / cmp-agency(WEB_SITE, settings 없음=none)."""
    db.add(NaverCampaignSettings(campaign_id="cmp-web", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp-shop", optimizer="mop"))
    # cmp-agency: settings 행 없음 → optimizer none 폴백(대행사 관찰 대상)

    _ent(db, "campaign", "cmp-web", campaign_id="cmp-web", campaign_type="WEB_SITE", name="파워링크")
    _ent(db, "campaign", "cmp-shop", campaign_id="cmp-shop", campaign_type="SHOPPING", name="쇼핑")
    _ent(db, "campaign", "cmp-agency", campaign_id="cmp-agency", campaign_type="WEB_SITE", name="대행사파워링크")

    _ent(db, "adgroup", "grp-web1", parent_id="cmp-web", campaign_id="cmp-web", campaign_type="WEB_SITE", name="그룹W1", bid_amt=500)
    _ent(db, "adgroup", "grp-shop1", parent_id="cmp-shop", campaign_id="cmp-shop", campaign_type="SHOPPING", name="그룹S1", bid_amt=70)
    _ent(db, "adgroup", "grp-agency1", parent_id="cmp-agency", campaign_id="cmp-agency", campaign_type="WEB_SITE", name="그룹A1", bid_amt=300)

    # grp-web1 키워드: on 3건(입찰 600/800/"1000" str) + off 1 + deleted 1 → count=3, avg=800
    _ent(db, "keyword", "kw-1", parent_id="grp-web1", campaign_id="cmp-web", campaign_type="WEB_SITE", name="필름", bid_amt=600)
    _ent(db, "keyword", "kw-2", parent_id="grp-web1", campaign_id="cmp-web", campaign_type="WEB_SITE", name="강화유리", bid_amt=800)
    _ent(db, "keyword", "kw-3", parent_id="grp-web1", campaign_id="cmp-web", campaign_type="WEB_SITE", name="지문방지", bid_amt="1000")
    _ent(db, "keyword", "kw-4", parent_id="grp-web1", campaign_id="cmp-web", campaign_type="WEB_SITE", name="off키워드", status="off", bid_amt=900)
    _ent(db, "keyword", "kw-5", parent_id="grp-web1", campaign_id="cmp-web", campaign_type="WEB_SITE", name="del키워드", status="deleted", bid_amt=900)
    # grp-agency1 키워드: on 2건(입찰 200/400) → count=2, avg=300
    _ent(db, "keyword", "kw-6", parent_id="grp-agency1", campaign_id="cmp-agency", campaign_type="WEB_SITE", name="폴드8", bid_amt=200)
    _ent(db, "keyword", "kw-7", parent_id="grp-agency1", campaign_id="cmp-agency", campaign_type="WEB_SITE", name="플립8", bid_amt=400)
    db.commit()


def _rows(db, sdate=SDATE):
    return {(r.entity_type, r.entity_id): r for r in db.query(NaverEntitySnapshot).filter(
        NaverEntitySnapshot.snapshot_date == sdate).all()}


def test_snapshot_grain_only_campaign_and_adgroup(db):
    """키워드 grain은 스냅샷에 저장 안 함 — 캠페인 3 + 그룹 3 = 6행만(§2)."""
    _seed(db)
    result = snapshot_entities(db, snapshot_date=SDATE)
    rows = _rows(db)
    assert result["campaigns"] == 3
    assert result["adgroups"] == 3
    assert len(rows) == 6
    assert not any(rt == "keyword" for (rt, _) in rows)


def test_keyword_count_and_avg_aggregation(db):
    """WEB_SITE 그룹만 키워드 집계(on만·off/deleted 제외, str 입찰 정규화). 타 유형은 NULL."""
    _seed(db)
    result = snapshot_entities(db, snapshot_date=SDATE)
    rows = _rows(db)

    web = rows[("adgroup", "grp-web1")]
    assert web.keyword_count == 3  # on 3건, off/deleted 제외
    assert web.keyword_avg_bid == 800  # (600+800+1000)/3, "1000" str도 정규화

    agency = rows[("adgroup", "grp-agency1")]
    assert agency.keyword_count == 2
    assert agency.keyword_avg_bid == 300

    # SHOPPING 그룹은 키워드 미동기화 → NULL(0으로 오도 금지)
    shop = rows[("adgroup", "grp-shop1")]
    assert shop.keyword_count is None
    assert shop.keyword_avg_bid is None

    # 완료기준: 그룹 keyword_count 합계 = naver_entity on 키워드 수(3+2=5)
    assert result["keyword_total"] == 5

    # 캠페인 행은 그룹 입찰·키워드 집계 없음
    cmp = rows[("campaign", "cmp-web")]
    assert cmp.keyword_count is None
    assert cmp.bid_amt is None


def test_optimizer_join(db):
    """optimizer는 naver_campaign_settings 조인. settings 없는 캠페인=none(대행사)."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE)
    rows = _rows(db)
    assert rows[("campaign", "cmp-web")].optimizer == "ours"
    assert rows[("campaign", "cmp-shop")].optimizer == "mop"
    assert rows[("campaign", "cmp-agency")].optimizer == "none"
    # 그룹 행도 부모 캠페인 optimizer 상속(campaign_id 조인)
    assert rows[("adgroup", "grp-agency1")].optimizer == "none"
    assert rows[("adgroup", "grp-web1")].optimizer == "ours"


def test_bid_amt_snapshotted_for_adgroups(db):
    """그룹 기본입찰(bid_amt)은 그룹 행에 스냅샷."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE)
    rows = _rows(db)
    assert rows[("adgroup", "grp-web1")].bid_amt == 500
    assert rows[("adgroup", "grp-shop1")].bid_amt == 70


def test_upsert_idempotent_same_day(db):
    """같은 snapshot_date 재실행 = 중복 없음(upsert), 갱신값 반영."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE)
    assert len(_rows(db)) == 6

    # 대행사가 그룹 입찰을 500→650으로 변경 후 재실행 → 같은 행 갱신, 행 수 불변
    grp = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-web1").one()
    grp.bid_amt = 650
    db.commit()
    snapshot_entities(db, snapshot_date=SDATE)

    rows = _rows(db)
    assert len(rows) == 6  # 중복 생성 안 됨
    assert rows[("adgroup", "grp-web1")].bid_amt == 650  # 갱신 반영


def test_next_day_creates_new_snapshot_rows(db):
    """다음 날짜 스냅샷은 별도 행(날짜별 history 보존)."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE)
    snapshot_entities(db, snapshot_date=date(2026, 7, 23))
    assert len(_rows(db, SDATE)) == 6
    assert len(_rows(db, date(2026, 7, 23))) == 6
    assert db.query(NaverEntitySnapshot).count() == 12


def test_run_bm_layer_fail_open(db, monkeypatch):
    """SA-1이 예외를 던져도 run_bm_layer는 삼키고 정상 반환(§0 금지선 5 — 관찰 잡 fail-open)."""
    def _boom(*a, **k):
        raise RuntimeError("네이버 DB 폭발")

    monkeypatch.setattr(bm_harness, "snapshot_entities", _boom)
    result = bm_harness.run_bm_layer(db)  # 예외 전파 안 됨
    assert result["snapshot"] is None


def test_run_bm_layer_happy_path(db):
    """정상 경로: run_bm_layer가 SA-1을 호출해 스냅샷 결과를 반환."""
    _seed(db)
    result = bm_harness.run_bm_layer(db)
    assert result["snapshot"]["campaigns"] == 3
    assert result["snapshot"]["adgroups"] == 3
    assert db.query(NaverEntitySnapshot).count() == 6
