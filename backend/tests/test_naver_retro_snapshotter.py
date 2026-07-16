# test_naver_retro_snapshotter.py — D-NAO-45 retro_snapshotter SA 단위 테스트
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverEntity, NaverProductBep, NaverRetroSignal
from app.services.naver_ad import diagnosis, retro_snapshotter

ASOF = date(2026, 7, 15)


@pytest.fixture
def db(monkeypatch):
    # 보정계수(D-NAO-21)는 실주문 매출 의존이라 픽스처가 무거워진다 — 이 SA의 책임은
    # "diagnosis 산출값을 고정 렌즈로 저장하는가"이지 보정계수 산식 자체가 아니므로,
    # correction_factor를 결정적 값으로 고정해 픽스처를 단순화한다(existing 관례: 다른
    # naver_ad 테스트들도 fetcher/campaign_target_resolver 등 이웃 SA를 monkeypatch — 예:
    # test_naver_proposal_pipeline.py).
    def _fixed_cf(db, date_to):
        return {"factor": Decimal("1"), "source": "test-fixed", "window_revenue": 0, "window_conv_amt": 0}

    monkeypatch.setattr(diagnosis, "correction_factor", _fixed_cf)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _bep(db, *, bep_roas="2.0", target_roas="4.0"):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="cp-1", product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal(bep_roas),
        aggressiveness="standard", target_roas=Decimal(target_roas), has_cost=True,
    ))


def _row(db, ad_date, campaign_id, campaign_type, adgroup_id, keyword_id, imp, clk, cost, direct=0, indirect=0):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=imp, clk=clk, cost=cost, rank_sum=imp * 3,
        conv_direct_cnt=1 if direct else 0, conv_indirect_cnt=1 if indirect else 0,
        conv_direct_amt=direct, conv_indirect_amt=indirect,
    ))


def _entity(db, entity_type, entity_id, *, status="on", bid_amt=None, campaign_id="cmp1",
            parent_id="grp1", campaign_type="WEB_SITE"):
    db.add(NaverEntity(
        entity_type=entity_type, entity_id=entity_id, parent_id=parent_id,
        campaign_id=campaign_id, campaign_type=campaign_type, name=entity_id,
        status=status, bid_amt=bid_amt,
    ))


def _active_campaign(db, campaign_id, campaign_type):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, parent_id="",
                        campaign_id=campaign_id, campaign_type=campaign_type, name=campaign_id, status="on"))


def _active_adgroup(db, campaign_id, adgroup_id, campaign_type="WEB_SITE"):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                        campaign_id=campaign_id, campaign_type=campaign_type, name=adgroup_id, status="on"))


def _active_chain(db, *, campaign_id, adgroup_id, campaign_type="WEB_SITE"):
    """부모 체인(campaign→adgroup) status='on' 단건 — 캠페인·그룹이 1:1인 경우 전용."""
    _active_campaign(db, campaign_id, campaign_type)
    _active_adgroup(db, campaign_id, adgroup_id, campaign_type)


def test_snapshot_creates_row_with_locked_lens(db):
    """행 생성 + 렌즈 고정값 저장(PLAN §6-C1) — diagnosis 산출값과 저장된 행이 일치해야 함."""
    _bep(db)
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 50, 10000, direct=1000)  # roas=0.1 < bep 2.0
    db.commit()

    expected = diagnosis.build_diagnosis(db, ASOF - timedelta(days=14), ASOF)
    result = retro_snapshotter.snapshot_signals(db, ASOF)

    assert result["snapshotted"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.asof_date == ASOF
    assert row.board == "bleeding_keywords"
    assert row.direction == "down"
    assert row.grain == "keyword"
    assert row.target_id == "nkw-1"
    assert row.campaign_id == "cmp1"
    assert row.cf_asof == expected["correction_factor"]["factor"]
    assert row.bep_asof == expected["account_bep_roas"]
    assert row.target_asof == expected["account_target_roas"]
    assert row.cost_asof == 10000
    assert row.roas_c_asof == expected["boards"]["bleeding_keywords"][0]["roas_corrected"]
    assert row.created_at is not None
    assert row.verdict_d3 is None
    assert row.verdict_d7 is None


def test_snapshot_covers_all_six_boards_with_correct_direction(db):
    """대상 보드 6종·방향 매핑 (ref 31 §1-a) — down/up/pause × keyword/adgroup 전부."""
    _bep(db)
    _active_chain(db, campaign_id="cmp1", adgroup_id="grp1", campaign_type="WEB_SITE")
    _active_campaign(db, "cmp2", "SHOPPING")

    # bleeding_keywords(down): roas 0.1 < bep 2.0
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-down", 1000, 50, 10000, direct=1000)
    # starving_winners(up): roas 9.0 >= target 4.0, clk=0(저클릭)
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-up", 100, 0, 1000, direct=9000)
    # pause_candidates(pause): 무전환 + 스톱로스(bid_amt 200 * LOW_CLICK_THRESHOLD 10 = 2000) 이상
    _entity(db, "keyword", "nkw-pause", status="on", bid_amt=200, campaign_id="cmp1", parent_id="grp1")
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-pause", 500, 25, 2500, direct=0)

    # shopping_group_bep(down)
    _row(db, ASOF, "cmp2", "SHOPPING", "grp-bep", "", 500, 20, 5000, direct=500)
    # shopping_group_growth(up)
    _entity(db, "adgroup", "grp-growth", status="on", bid_amt=300, campaign_id="cmp2",
            parent_id="cmp2", campaign_type="SHOPPING")
    _row(db, ASOF, "cmp2", "SHOPPING", "grp-growth", "", 200, 20, 1000, direct=9000)
    # shopping_pause_candidates(pause): bid_amt 100 * 10 = 1000 스톱로스, 무전환
    _entity(db, "adgroup", "grp-pause", status="on", bid_amt=100, campaign_id="cmp2",
            parent_id="cmp2", campaign_type="SHOPPING")
    _row(db, ASOF, "cmp2", "SHOPPING", "grp-pause", "", 300, 15, 2000, direct=0)
    db.commit()

    result = retro_snapshotter.snapshot_signals(db, ASOF)
    # nkw-pause/grp-pause는 무전환(roas=0<bep)이라 bleeding_keywords/shopping_group_bep에도
    # 동시에 걸린다(실제 진단 로직의 정당한 중복 — 같은 타깃이 여러 보드에 동시 플래그될 수
    # 있음, 보드마다 독립적으로 판단). 그래서 6개 타깃이지만 스냅샷 행은 8개.
    assert result["snapshotted"] == 8

    def _get(board, target_id):
        return db.query(NaverRetroSignal).filter_by(board=board, target_id=target_id).one()

    checks = [
        ("bleeding_keywords", "nkw-down", "down", "keyword"),
        ("starving_winners", "nkw-up", "up", "keyword"),
        ("pause_candidates", "nkw-pause", "pause", "keyword"),
        ("shopping_group_bep", "grp-bep", "down", "adgroup"),
        ("shopping_group_growth", "grp-growth", "up", "adgroup"),
        ("shopping_pause_candidates", "grp-pause", "pause", "adgroup"),
    ]
    for board, target_id, direction, grain in checks:
        row = _get(board, target_id)
        assert row.direction == direction
        assert row.grain == grain

    seen_boards = {r.board for r in db.query(NaverRetroSignal).all()}
    assert seen_boards == {b for b, _, _, _ in checks}


def test_snapshot_idempotent_on_rerun(db):
    """같은 asof 재실행 시 중복 행이 생기지 않음(크론 재시도·backfill 재실행 안전)."""
    _bep(db)
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 50, 10000, direct=1000)
    db.commit()

    first = retro_snapshotter.snapshot_signals(db, ASOF)
    second = retro_snapshotter.snapshot_signals(db, ASOF)

    assert first["snapshotted"] == 1
    assert second["snapshotted"] == 0
    assert db.query(NaverRetroSignal).count() == 1


def test_snapshot_boards_none_returns_skip_dict(db):
    """계정 BEP/목표ROAS 산출 불가(상품 BEP 데이터 없음) — graceful skip, 예외 없음."""
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 50, 10000, direct=1000)
    db.commit()

    result = retro_snapshotter.snapshot_signals(db, ASOF)
    assert result["snapshotted"] == 0
    assert result["skipped"] == "diagnosis 실패"
    assert result["error"] is not None
    assert db.query(NaverRetroSignal).count() == 0


def test_snapshot_asof_leakage_regression(db):
    """as-of 누출 회귀(D-NAO-44 codex R1 클래스, PLAN §6-C1) — asof 이후 날짜 데이터가
    스냅샷 결과(cost_asof)에 절대 섞이면 안 된다."""
    _bep(db)
    # asof 당일 지출 10,000원
    _row(db, ASOF, "cmp1", "WEB_SITE", "grp1", "nkw-1", 1000, 50, 10000, direct=1000)
    # asof 다음날(미래 관점) 지출 900,000원 — 리플레이 시점에선 존재하면 안 되는 데이터
    _row(db, ASOF + timedelta(days=1), "cmp1", "WEB_SITE", "grp1", "nkw-1", 90000, 4500, 900000, direct=90000)
    db.commit()

    result = retro_snapshotter.snapshot_signals(db, ASOF)
    assert result["snapshotted"] == 1
    row = db.query(NaverRetroSignal).one()
    assert row.cost_asof == 10000, "asof 다음날 지출이 섞이면 910,000원이 되어야 하는데 그러면 안 됨"


def test_snapshot_growth_freezes_campaign_override_target(db):
    """codex[P2] 회귀: shopping_group_growth는 캠페인별 리졸브 목표(override>계정기본)로
    선별되므로 채점 렌즈(target_asof)도 같은 값을 얼려야 한다. 보정ROAS 3.5 그룹은
    override 3.0으론 선별되지만 계정 목표 4.0 기준으론 아니다 — 행이 존재한다는 것 자체가
    선별에 override가 쓰였다는 증거이고, 그렇다면 target_asof도 3.0이어야 정합."""
    from app.models import NaverCampaignSettings

    _bep(db)  # 계정 bep 2.0 / target 4.0
    db.add(NaverCampaignSettings(campaign_id="cmp-s", optimizer="ours",
                                 target_roas_override=Decimal("3.0")))
    _active_campaign(db, "cmp-s", "SHOPPING")
    _entity(db, "adgroup", "grp-s", status="on", bid_amt=500, campaign_id="cmp-s",
            parent_id="cmp-s", campaign_type="SHOPPING")
    _row(db, ASOF, "cmp-s", "SHOPPING", "grp-s", "", 1000, 50, 10000, direct=35000)  # roas_c=3.5
    db.commit()

    result = retro_snapshotter.snapshot_signals(db, ASOF)
    assert result["snapshotted"] >= 1
    row = db.query(NaverRetroSignal).filter(NaverRetroSignal.board == "shopping_group_growth").one()
    assert row.target_asof == 3.0, "override 캠페인의 growth 행은 계정 목표(4.0)가 아니라 override(3.0)를 얼려야 함"
    # 다른 보드는 계정 목표 유지(선별도 계정값이므로) — 회귀 방지 대조군
    others = db.query(NaverRetroSignal).filter(NaverRetroSignal.board != "shopping_group_growth").all()
    for o in others:
        assert o.target_asof == 4.0
