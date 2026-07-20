# test_naver_campaign_roster.py — D-NAO-48: 캠페인 명부 SA(관리주체 스위치의 데이터 원천)
# ★존재 이유: Jino가 "광고 옆에 토글"을 원하는데, 지금 API 어디에도 **캠페인 이름이 없다**
#   (콘솔은 report+settings+diagnosis 3-call 병합인데 report가 이름을 안 줌) → 화면에
#   cmp-a001-02-000000008492582가 그대로 노출된다. MOP 리뷰에서 "베끼면 안 되는 것"으로
#   꼽은 내부 ID 노출(§2-7)을 우리가 하고 있는 것. 이름은 naver_entity에 있다.
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverCampaignSettings, NaverEntity
from app.services.naver_ad import campaign_roster
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now, kst_today


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


def _campaign(db, *, cid, name, ctype="SHOPPING", status="on"):
    db.add(NaverEntity(
        entity_type="campaign", entity_id=cid, parent_id="",
        campaign_id=cid, campaign_type=ctype, name=name,
        status=status, bid_amt=None, synced_at=kst_now(),
    ))


def _daily(db, *, cid, days_ago, cost, ctype="SHOPPING", adgroup_id="grp-1",
           conv_direct=0, conv_indirect=0, clk=0):
    db.add(NaverAdDaily(
        ad_date=kst_today() - timedelta(days=days_ago), campaign_id=cid, campaign_type=ctype,
        adgroup_id=adgroup_id, keyword_id="-", imp=0, clk=clk, cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0,
        conv_direct_amt=conv_direct, conv_indirect_amt=conv_indirect,
        synced_at=kst_now(),
    ))


def test_roster_returns_name_and_type(db):
    """★이름이 이 SA의 존재 이유다 — 내부 ID만 보여주던 걸 끝낸다."""
    _campaign(db, cid="cmp-1", name="● [P_삭제금지]03. 아이폰_강화유리", ctype="SHOPPING")
    _daily(db, cid="cmp-1", days_ago=1, cost=1000)
    db.commit()

    rows = campaign_roster.build(db)
    assert len(rows) == 1
    assert rows[0]["campaign_id"] == "cmp-1"
    assert rows[0]["name"] == "● [P_삭제금지]03. 아이폰_강화유리"
    assert rows[0]["campaign_type"] == "SHOPPING"


def test_roster_excludes_backfill_sentinel_rows(db):
    """★2배 함정: campaign_backfill이 심는 sentinel 행은 그룹 롤업이라 실단위 행과 합치면
    광고비가 이중집계된다. dashboard_overview._optimizer_coverage·proposal_pipeline·
    account_diagnosis와 동일하게 제외한다(프로젝트 단일 진실 관례)."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=1000, adgroup_id="grp-real")
    _daily(db, cid="cmp-1", days_ago=1, cost=1000, adgroup_id=BACKFILL_SENTINEL_ADGROUP)
    db.commit()

    rows = campaign_roster.build(db)
    assert rows[0]["cost"] == 1000, "sentinel 행이 합산되면 2000이 된다(이중집계)"


def test_roster_excludes_today_uses_d1_window(db):
    """오늘(D-0)은 naver_ad_daily가 확정 적재 전일 수 있어 창에서 제외 —
    ad_report·diagnosis·optimizer_coverage의 D-1 확정치 관례와 동일."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=0, cost=9999)   # 오늘 — 제외돼야
    _daily(db, cid="cmp-1", days_ago=1, cost=1000)   # D-1 — 포함
    db.commit()

    assert campaign_roster.build(db)[0]["cost"] == 1000


def test_roster_respects_window_days(db):
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=100)
    _daily(db, cid="cmp-1", days_ago=40, cost=500)  # 창 밖
    db.commit()

    assert campaign_roster.build(db, days=30)[0]["cost"] == 100


def test_roster_optimizer_defaults_to_none_when_no_settings_row(db):
    """설정 행이 없으면 'none' — naver_execution_harness._resolve_optimizer와 동일 시맨틱
    (단일 진실 유지). 여기서 다르게 굴면 화면과 실행 게이트가 어긋난다."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=100)
    db.commit()

    assert campaign_roster.build(db)[0]["optimizer"] == "none"


def test_roster_reflects_optimizer_setting(db):
    _campaign(db, cid="cmp-ours", name="04")
    _campaign(db, cid="cmp-mop", name="03")
    _daily(db, cid="cmp-ours", days_ago=1, cost=100)
    _daily(db, cid="cmp-mop", days_ago=1, cost=200)
    db.add(NaverCampaignSettings(campaign_id="cmp-ours", optimizer="ours"))
    db.add(NaverCampaignSettings(campaign_id="cmp-mop", optimizer="mop"))
    db.commit()

    by = {r["campaign_id"]: r for r in campaign_roster.build(db)}
    assert by["cmp-ours"]["optimizer"] == "ours"
    assert by["cmp-mop"]["optimizer"] == "mop"


def test_roster_loss_policy_none_when_no_settings_row(db):
    """UI2(D-NAO-65): 설정 행이 없으면 loss_policy=None — 프론트가 '기본(고삐)'로 해석.
    조회 SA는 NULL을 임의로 'leash'로 정규화하지 않는다(그 책임은 쓰기 경로에만)."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=100)
    db.commit()

    assert campaign_roster.build(db)[0]["loss_policy"] is None


def test_roster_reflects_loss_policy_setting(db):
    """UI2(D-NAO-65): 설정된 stoploss_pause를 그대로 실어 보낸다(스위치 렌더 원천)."""
    _campaign(db, cid="cmp-leash", name="고삐")
    _campaign(db, cid="cmp-stop", name="스톱로스")
    _daily(db, cid="cmp-leash", days_ago=1, cost=100)
    _daily(db, cid="cmp-stop", days_ago=1, cost=200)
    db.add(NaverCampaignSettings(campaign_id="cmp-leash", optimizer="ours", loss_policy="leash"))
    db.add(NaverCampaignSettings(campaign_id="cmp-stop", optimizer="ours", loss_policy="stoploss_pause"))
    db.commit()

    by = {r["campaign_id"]: r for r in campaign_roster.build(db)}
    assert by["cmp-leash"]["loss_policy"] == "leash"
    assert by["cmp-stop"]["loss_policy"] == "stoploss_pause"


def test_roster_includes_campaigns_with_zero_spend(db):
    """★광고비 0이어도 명부에 있어야 스위치를 걸 수 있다. 0은 '없는 것'이 아니다 —
    정지된 캠페인·신규 인계 대상도 관리주체를 지정할 수 있어야 한다(D-47-h와 같은 정신)."""
    _campaign(db, cid="cmp-quiet", name="집행 없는 캠페인")
    db.commit()

    rows = campaign_roster.build(db)
    assert len(rows) == 1
    assert rows[0]["cost"] == 0
    assert rows[0]["roas_naver"] is None  # 0/0 → None(0.0 아님)


def test_roster_excludes_deleted_campaigns(db):
    _campaign(db, cid="cmp-live", name="살아있음", status="on")
    _campaign(db, cid="cmp-dead", name="삭제됨", status="deleted")
    db.commit()

    ids = {r["campaign_id"] for r in campaign_roster.build(db)}
    assert ids == {"cmp-live"}


def test_roster_roas_uses_naver_convention_direct_plus_indirect(db):
    """네이버 기준 ROAS = (직접+간접 전환매출)/광고비 — D-NAO-7의 1열과 동일 정의."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=1000, conv_direct=1500, conv_indirect=1000)
    db.commit()

    assert campaign_roster.build(db)[0]["roas_naver"] == pytest.approx(2.5)


def test_roster_roas_is_none_when_no_cost(db):
    """★0으로 나누지 않는다. 광고비 0은 'ROAS 0배'가 아니라 '알 수 없음'이다."""
    _campaign(db, cid="cmp-1", name="캠페인")
    _daily(db, cid="cmp-1", days_ago=1, cost=0, conv_direct=500)
    db.commit()

    assert campaign_roster.build(db)[0]["roas_naver"] is None


def test_roster_sorted_by_cost_desc(db):
    for i, cost in enumerate([100, 5000, 700]):
        _campaign(db, cid=f"cmp-{i}", name=f"c{i}")
        _daily(db, cid=f"cmp-{i}", days_ago=1, cost=cost)
    db.commit()

    assert [r["cost"] for r in campaign_roster.build(db)] == [5000, 700, 100]


def test_roster_empty_db_returns_empty_list(db):
    assert campaign_roster.build(db) == []
