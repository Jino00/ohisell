# test_naver_bm_snapshot.py — BM 벤치마크 레이어 Phase 1 단위 테스트 (SA-1, D-NAO-78)
# 커버: snapshot_entities(upsert 멱등·중복없음, keyword_count/avg_bid 집계 정확성, optimizer
#   조인 none/ours/mop, WEB_SITE만 키워드 집계·타 유형 NULL), run_bm_layer fail-open.
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverCampaignSettings, NaverEntity, NaverEntitySnapshot
from app.services.naver_ad import bm_harness, bm_snapshot
from app.services.naver_ad.bm_snapshot import snapshot_entities, update_deep_dimensions
from app.utils.kst import kst_now

SDATE = date(2026, 7, 22)
ENT_OBS = datetime(2026, 7, 21, 7, 35)  # 앞선 entity_sync 관측 시각(D-NAO-93 실측: 스냅샷보다 이름)
# Phase 3(예산·확장검색)은 미주입 시 실제 네이버 GET을 호출한다 — 유닛 테스트는 항상 빈 값을
# 주입해 라이브 네트워크 호출을 피한다(entity_sync.collect_entities와 동일 관례, 원칙18-8).
_NO_GET = {"campaigns_full": [], "adgroups_by_campaign": {}}


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
    result = snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
    rows = _rows(db)
    assert result["campaigns"] == 3
    assert result["adgroups"] == 3
    assert len(rows) == 6
    assert not any(rt == "keyword" for (rt, _) in rows)


def test_keyword_count_and_avg_aggregation(db):
    """WEB_SITE 그룹만 키워드 집계(on만·off/deleted 제외, str 입찰 정규화). 타 유형은 NULL."""
    _seed(db)
    result = snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
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
    snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
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
    snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
    rows = _rows(db)
    assert rows[("adgroup", "grp-web1")].bid_amt == 500
    assert rows[("adgroup", "grp-shop1")].bid_amt == 70


def test_upsert_idempotent_same_day(db):
    """같은 snapshot_date 재실행 = 중복 없음(upsert), 갱신값 반영."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
    assert len(_rows(db)) == 6

    # 대행사가 그룹 입찰을 500→650으로 변경 후 재실행 → 같은 행 갱신, 행 수 불변
    grp = db.query(NaverEntity).filter_by(entity_type="adgroup", entity_id="grp-web1").one()
    grp.bid_amt = 650
    db.commit()
    snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)

    rows = _rows(db)
    assert len(rows) == 6  # 중복 생성 안 됨
    assert rows[("adgroup", "grp-web1")].bid_amt == 650  # 갱신 반영


def test_next_day_creates_new_snapshot_rows(db):
    """다음 날짜 스냅샷은 별도 행(날짜별 history 보존)."""
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE, **_NO_GET)
    snapshot_entities(db, snapshot_date=date(2026, 7, 23), **_NO_GET)
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


def test_run_bm_layer_happy_path(db, monkeypatch):
    """정상 경로: run_bm_layer가 SA-1을 호출해 스냅샷 결과를 반환.

    run_bm_layer는 파라미터 없이 snapshot_entities(db)를 호출한다(prod 07:37 실경로 — Phase 3가
    실제 GET을 시도). 유닛 테스트는 라이브 네트워크를 피하기 위해 fetcher 함수를 모듈 스코프에서
    monkeypatch(entity_sync 테스트 관례와 동일 목적, 원칙18-8)."""
    monkeypatch.setattr(bm_snapshot, "get_campaigns_full", lambda: [])
    monkeypatch.setattr(bm_snapshot, "get_adgroups", lambda cid: [])
    _seed(db)
    result = bm_harness.run_bm_layer(db)
    assert result["snapshot"]["campaigns"] == 3
    assert result["snapshot"]["adgroups"] == 3
    assert db.query(NaverEntitySnapshot).count() == 6


# ══════════════════════════════════════════════════════════════════════════
# Phase 3 — 차원 보강(예산·확장검색, 일별) 테스트 (D-NAO-78)
# ══════════════════════════════════════════════════════════════════════════

def test_phase3_campaign_budget_and_adgroup_dims_filled(db):
    """캠페인 daily_budget(get_campaigns_full 주입)·그룹 daily_budget+extended_search
    (get_adgroups 주입)가 스냅샷 행에 채워진다(§2 nullable 규약, 스키마 공용 컬럼)."""
    _seed(db)
    campaigns_full = [
        {"campaign_id": "cmp-web", "daily_budget": 50000},
        {"campaign_id": "cmp-shop", "daily_budget": None},  # 예산 무제한 캠페인 → NULL 그대로
    ]
    adgroups_by_campaign = {
        "cmp-web": [{"adgroup_id": "grp-web1", "daily_budget": 20000, "extended_search": True}],
        "cmp-agency": [{"adgroup_id": "grp-agency1", "daily_budget": None, "extended_search": False}],
    }
    result = snapshot_entities(
        db, snapshot_date=SDATE, campaigns_full=campaigns_full, adgroups_by_campaign=adgroups_by_campaign,
    )
    rows = _rows(db)

    assert rows[("campaign", "cmp-web")].daily_budget == 50000
    assert rows[("campaign", "cmp-shop")].daily_budget is None
    assert rows[("campaign", "cmp-agency")].daily_budget is None  # 주입 목록에 없음 → NULL

    web1 = rows[("adgroup", "grp-web1")]
    assert web1.daily_budget == 20000
    assert web1.extended_search is True

    agency1 = rows[("adgroup", "grp-agency1")]
    assert agency1.daily_budget is None
    assert agency1.extended_search is False

    # 그룹 주입 목록에 없는 그룹(grp-shop1)은 Phase 3 차원 NULL 유지
    shop1 = rows[("adgroup", "grp-shop1")]
    assert shop1.daily_budget is None
    assert shop1.extended_search is None

    assert result["get_calls"] == 0  # 전량 주입 — 실제 GET 없음


def test_phase3_campaign_budget_get_failure_is_fail_open(db, monkeypatch):
    """캠페인 예산 GET이 실패해도 SA-1 전체는 죽지 않는다 — daily_budget만 NULL 유지(§0 금지선 5)."""
    def _boom():
        raise RuntimeError("네이버 API 타임아웃")

    monkeypatch.setattr(bm_snapshot, "get_campaigns_full", _boom)
    _seed(db)
    result = snapshot_entities(db, snapshot_date=SDATE, adgroups_by_campaign={})  # campaigns_full 미주입 → 위 _boom 호출
    rows = _rows(db)

    assert result["campaigns"] == 3  # 실패해도 스냅샷 자체는 완료
    assert rows[("campaign", "cmp-web")].daily_budget is None
    assert result["get_calls"] == 0  # 실패 시 GET 성공 카운트 0(§실측)
    # ★D-NAO-93: 관측 못 한 필드는 관측 시각도 NULL — bm_diff의 synced_at 폴백이 성립해야 한다
    assert rows[("campaign", "cmp-web")].p3_observed_at is None


def test_phase3_adgroup_dims_get_failure_for_one_campaign_is_isolated(db, monkeypatch):
    """한 캠페인의 그룹 GET이 실패해도 나머지 캠페인은 계속 채워진다(캠페인 단위 fail-open)."""
    def _get_adgroups(cid):
        if cid == "cmp-shop":
            raise RuntimeError("네이버 API 502")
        if cid == "cmp-web":
            return [{"adgroup_id": "grp-web1", "daily_budget": 30000, "extended_search": True}]
        return []

    monkeypatch.setattr(bm_snapshot, "get_adgroups", _get_adgroups)
    _seed(db)
    result = snapshot_entities(db, snapshot_date=SDATE, campaigns_full=[])
    rows = _rows(db)

    assert rows[("adgroup", "grp-web1")].daily_budget == 30000
    assert rows[("adgroup", "grp-web1")].extended_search is True
    assert rows[("adgroup", "grp-shop1")].daily_budget is None  # 실패한 캠페인 소속 → NULL 유지
    assert result["campaigns"] == 3  # 실패해도 스냅샷 자체는 완료
    assert result["get_calls"] == 2  # cmp-web·cmp-agency 성공(cmp-shop 실패는 미포함)


# ══════════════════════════════════════════════════════════════════════════
# D-NAO-93 — 필드 출처별 관측 시각(bm_diff 대조창 앵커)
# ══════════════════════════════════════════════════════════════════════════

def test_observed_at_recorded_per_field_source(db):
    """entity_observed_at은 전 행에 NaverEntity.synced_at 복사(=entity_sync 관측 시각),
    p3_observed_at은 **P3 데이터를 실제로 받은 행**만 채운다(못 받은 행은 NULL → 폴백)."""
    _seed(db)
    for e in db.query(NaverEntity).all():  # entity_sync가 D-1 07:35에 관측했다고 가정
        e.synced_at = ENT_OBS
    db.commit()

    campaigns_full = [
        {"campaign_id": "cmp-web", "daily_budget": 50000},
        {"campaign_id": "cmp-shop", "daily_budget": None},  # 무제한이어도 '관측됨'
    ]
    adgroups_by_campaign = {
        "cmp-web": [{"adgroup_id": "grp-web1", "daily_budget": 20000, "extended_search": True}],
    }
    snapshot_entities(db, snapshot_date=SDATE, campaigns_full=campaigns_full,
                      adgroups_by_campaign=adgroups_by_campaign)
    rows = _rows(db)

    assert all(r.entity_observed_at == ENT_OBS for r in rows.values())
    # 스냅샷 복사 시각(synced_at)보다 P3 관측이 늦다 — 이 순서가 bm_diff 오탐 밴드의 근거
    web = rows[("campaign", "cmp-web")]
    assert web.p3_observed_at is not None and web.p3_observed_at >= web.synced_at
    assert rows[("campaign", "cmp-shop")].p3_observed_at is not None  # 값 None ≠ 미관측
    assert rows[("adgroup", "grp-web1")].p3_observed_at is not None
    # P3 응답에 없던 행 → NULL(관측 안 됨)
    assert rows[("campaign", "cmp-agency")].p3_observed_at is None
    assert rows[("adgroup", "grp-shop1")].p3_observed_at is None
    assert rows[("adgroup", "grp-agency1")].p3_observed_at is None


def test_p3_observed_at_is_per_campaign_not_global(db, monkeypatch):
    """★codex[P1]: 그룹 dims는 캠페인별 순차 GET이라 관측 시각이 **캠페인마다 다르다**.
    전역 1개로 뭉치면 먼저 관측된 캠페인 소속 그룹에 늦은 시각이 찍혀 bm_diff 밴드가 되살아난다.
    캠페인 예산 GET(첫 GET) 시각은 그룹 GET 시각보다 이르다."""
    stamps = iter([
        datetime(2026, 7, 22, 7, 36),  # snapshot_entities 시작(synced_at)
        datetime(2026, 7, 22, 7, 37),  # get_campaigns_full 직후
        datetime(2026, 7, 22, 7, 38),  # cmp-web 그룹 GET 직후
        datetime(2026, 7, 22, 7, 39),  # cmp-shop 그룹 GET 직후
        datetime(2026, 7, 22, 7, 50),  # cmp-agency 그룹 GET 직후
    ])
    monkeypatch.setattr(bm_snapshot, "kst_now", lambda: next(stamps))
    monkeypatch.setattr(bm_snapshot, "get_adgroups", lambda cid: [
        {"adgroup_id": f"grp-{cid.split('-')[1]}1", "daily_budget": 10000, "extended_search": True},
    ])
    _seed(db)
    snapshot_entities(db, snapshot_date=SDATE, campaigns_full=[
        {"campaign_id": "cmp-web", "daily_budget": 50000},
    ])
    rows = _rows(db)

    # 캠페인 행 = 예산 GET 시각(첫 GET) / 그룹 행 = **그 캠페인의** dims GET 시각
    assert rows[("campaign", "cmp-web")].p3_observed_at == datetime(2026, 7, 22, 7, 37)
    assert rows[("adgroup", "grp-web1")].p3_observed_at == datetime(2026, 7, 22, 7, 38)
    assert rows[("adgroup", "grp-shop1")].p3_observed_at == datetime(2026, 7, 22, 7, 39)
    assert rows[("adgroup", "grp-agency1")].p3_observed_at == datetime(2026, 7, 22, 7, 50)


# ══════════════════════════════════════════════════════════════════════════
# Phase 3 — 주간 deep 차원(제외키워드·소재수) 테스트 (D-NAO-78, bm_deep 레인)
# ══════════════════════════════════════════════════════════════════════════

def _snap_group(db, sdate, entity_id, *, campaign_type, status="on"):
    db.add(NaverEntitySnapshot(
        snapshot_date=sdate, entity_type="adgroup", entity_id=entity_id,
        campaign_id="cmp-x", campaign_type=campaign_type, status=status, synced_at=kst_now(),
    ))


def test_update_deep_dimensions_fills_negative_and_ad_count(db, monkeypatch):
    """WEB_SITE 그룹만 제외키워드 GET 호출, 전 유형 소재수 GET 호출. 값이 당일 행에 채워진다."""
    _snap_group(db, SDATE, "grp-web1", campaign_type="WEB_SITE")
    _snap_group(db, SDATE, "grp-shop1", campaign_type="SHOPPING")
    db.commit()

    calls: list[str] = []

    def _neg(aid):
        calls.append(f"neg:{aid}")
        return 7

    def _ad(aid):
        calls.append(f"ad:{aid}")
        return 3

    monkeypatch.setattr(bm_snapshot, "get_restricted_keyword_count", _neg)
    monkeypatch.setattr(bm_snapshot, "get_ad_count", _ad)
    monkeypatch.setattr(bm_snapshot.time, "sleep", lambda s: None)  # 테스트 가속

    result = update_deep_dimensions(db, snapshot_date=SDATE)
    rows = _rows(db)

    assert rows[("adgroup", "grp-web1")].negative_kw_count == 7
    assert rows[("adgroup", "grp-web1")].ad_count == 3
    assert rows[("adgroup", "grp-shop1")].negative_kw_count is None  # SHOPPING은 제외키워드 API 대상 아님
    assert rows[("adgroup", "grp-shop1")].ad_count == 3

    assert "neg:grp-shop1" not in calls  # SHOPPING은 제외키워드 GET 자체를 호출 안 함(GET 절약)
    assert result["groups"] == 2
    assert result["get_calls"] == 3  # web1: neg+ad(2) + shop1: ad(1)
    assert result["failures"] == 0


def test_update_deep_dimensions_per_group_fail_open(db, monkeypatch):
    """한 그룹의 GET 실패는 그 그룹만 skip(값 유지) — 다른 그룹·크론 전체는 계속."""
    _snap_group(db, SDATE, "grp-ok", campaign_type="WEB_SITE")
    _snap_group(db, SDATE, "grp-bad", campaign_type="WEB_SITE")
    db.commit()

    def _neg(aid):
        if aid == "grp-bad":
            raise RuntimeError("네이버 API 429")
        return 5

    monkeypatch.setattr(bm_snapshot, "get_restricted_keyword_count", _neg)
    monkeypatch.setattr(bm_snapshot, "get_ad_count", lambda aid: 1)
    monkeypatch.setattr(bm_snapshot.time, "sleep", lambda s: None)

    result = update_deep_dimensions(db, snapshot_date=SDATE)
    rows = _rows(db)

    assert rows[("adgroup", "grp-ok")].negative_kw_count == 5
    assert rows[("adgroup", "grp-bad")].negative_kw_count is None  # 실패 → NULL 유지, 예외 전파 없음
    assert rows[("adgroup", "grp-bad")].ad_count == 1  # 다른 GET(소재수)은 별개로 성공
    assert result["failures"] == 1


def test_run_bm_deep_fail_open(db, monkeypatch):
    """update_deep_dimensions이 예외를 던져도 run_bm_deep은 삼키고 정상 반환(§0 금지선 5).

    ★Phase 5(D-NAO-79) 이후 run_bm_deep은 deep 갱신과 무관하게 주간 벤치마크 요약도 시도한다
    (독립 try) — 벤치마크 0건인 빈 DB에서는 정상 산출되어 weekly_summary가 채워진다."""
    def _boom(*a, **k):
        raise RuntimeError("DB 폭발")

    monkeypatch.setattr(bm_harness, "update_deep_dimensions", _boom)
    result = bm_harness.run_bm_deep(db)  # 예외 전파 안 됨
    assert result == {"deep": None, "weekly_summary": {"benchmarks": 0}}
