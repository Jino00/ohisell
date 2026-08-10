# test_naver_ad_lever_mismatch_repair_d170.py — D-NAO-170(B-4 나머지 절반): 거부 → 데이터 수리 → 절체
#
# ══ 이 파일이 지키는 것 ══
# D-NAO-166은 「그룹 입찰이 옥션에서 실효가 아닌 그룹」의 그룹입찰 PUT을 **거부**하게 했다.
# 그런데 거부만 하고 끝나서, 라우터(DB 파생)와 가드(라이브 API)가 갈라진 그룹은 회차마다 같은
# 거부를 반복하며 **입찰이 그 자리에서 얼어붙었다**. D-NAO-170은 거부에 실려 온 라이브 관측을
# `naver_adgroup_product`에 되돌려 써서, **다음 회차에 기존 라우터가 스스로 소재로 절체**하게
# 한다 — 새 재라우팅 경로를 만드는 게 아니라 라우터의 «입력»을 고치는 것이다.
#
# ★핵심 회귀는 마지막 테스트다: write-back **직후** effective_bid가 source='group' → 'ad'로
#   뒤집히는가. 그게 뒤집히지 않으면 이 기능은 로그만 예쁜 no-op다.
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverProposal,
)
from app.services.naver_ad import effective_bid
from app.services.naver_ad import naver_execution_harness as harness
from app.services.naver_ad import naver_sa_writer as writer

CAMP = "cmp-ours"
GRP = "grp-dead"
NOW = datetime(2026, 8, 10, 16, 20)  # KST


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


def _live_ads(n=2):
    """가드가 판별에 쓴 라이브 `get_ads` 응답 모양(그룹입찰 死 = 전부 useGroupBidAmt=False)."""
    return [
        {"ad_id": f"nad-{i}", "adgroup_id": GRP, "mall_product_id": f"{100 + i}",
         "product_name": f"상품{i}", "use_group_bid_amt": False,
         "ad_bid_amt": 800 - 100 * i, "ad_user_lock": False,
         "edit_tm": "2026-08-10T07:00:00.000Z", "apply_tm": 1754800000000}
        for i in range(n)
    ]


def _dead_error(ads=None):
    return writer.GroupBidDeadError("그룹 입찰 死", adgroup_id=GRP, ads=ads if ads is not None else _live_ads())


# ── write-back 단위 ────────────────────────────────────────────────────────────
def test_writeback_updates_existing_rows_flipping_the_router_input(db):
    """DB가 「모름(NULL)」이던 행이 라이브 관측(False)으로 고쳐진다 — 라우터가 보는 그 칸이다."""
    db.add(NaverAdgroupProduct(adgroup_id=GRP, campaign_id=CAMP, mall_product_id="100",
                               product_name="상품0", ad_id="nad-0",
                               ad_bid_amt=None, use_group_bid_amt=None))
    db.commit()
    touched = harness._writeback_live_ad_observation(
        db, adgroup_id=GRP, ads=_live_ads(1), campaign_id=CAMP, now=NOW)
    assert touched == 1
    row = db.query(NaverAdgroupProduct).one()
    assert (row.use_group_bid_amt, row.ad_bid_amt, row.ad_id) == (False, 800, "nad-0")
    assert row.synced_at == NOW  # 신선도 — 이 관측이 «언제 것인지»가 stale 판별의 유일 근거


def test_writeback_inserts_when_group_has_no_rows_at_all(db):
    """★가장 나쁜 경우가 여기다 — 소재 행이 **아예 없는** 그룹.

    `_derive`는 유효 소재데이터 0건이면 group 폴백을 주므로, 라우터는 영원히 그룹으로 보내고
    가드는 영원히 거부한다. update-only로 두면 이 그룹은 **영구 동결**된다(2026-07월 03 캠페인
    사고가 정확히 이 모양이었다). `get_ads`가 유니크 키(mall_product_id)를 주므로 추정 없이
    삽입할 수 있다.
    """
    assert db.query(NaverAdgroupProduct).count() == 0
    touched = harness._writeback_live_ad_observation(
        db, adgroup_id=GRP, ads=_live_ads(2), campaign_id=CAMP, now=NOW)
    assert touched == 2
    rows = {r.mall_product_id: r for r in db.query(NaverAdgroupProduct).all()}
    assert set(rows) == {"100", "101"}
    assert all(r.use_group_bid_amt is False and r.campaign_id == CAMP for r in rows.values())


def test_writeback_does_not_advance_external_change_anchors(db):
    """`ad_edit_tm`·`ad_apply_tm`은 **건드리지 않는다**.

    그 둘은 외부 변경 탐지(D-NAO-127)의 앵커다. 탐지를 안 거친 경로에서 전진시키면
    «우리가 본 적 없는 외부 변경»을 본 것으로 만들어 버린다 — 여기서 고치려는 것은
    실효 레이어 판별 하나뿐이다.
    """
    db.add(NaverAdgroupProduct(adgroup_id=GRP, campaign_id=CAMP, mall_product_id="100",
                               ad_id="nad-0", use_group_bid_amt=None,
                               ad_edit_tm="2026-08-01T00:00:00.000Z", ad_apply_tm=1700000000000))
    db.commit()
    harness._writeback_live_ad_observation(db, adgroup_id=GRP, ads=_live_ads(1),
                                           campaign_id=CAMP, now=NOW)
    row = db.query(NaverAdgroupProduct).one()
    assert row.ad_edit_tm == "2026-08-01T00:00:00.000Z"   # 라이브의 08-10 값으로 전진하지 않았다
    assert row.ad_apply_tm == 1700000000000


def test_writeback_is_fail_open_and_never_raises(db):
    """수리 실패는 삼킨다(0 반환) — 이건 안전장치가 아니라 **가속기**이고, 여기서 예외를
    올리면 «거부를 기록하는 경로» 자체가 깨진다. 그건 확실한 손해다."""
    with patch.object(db, "commit", side_effect=RuntimeError("DB 장애")):
        assert harness._writeback_live_ad_observation(
            db, adgroup_id=GRP, ads=_live_ads(1), campaign_id=CAMP, now=NOW) == 0


def test_writeback_skips_when_live_payload_lacks_keys(db):
    """ad_id·mall_product_id가 없으면 «추정해서» 쓰지 않는다 — 정규 sync에 맡긴다."""
    bad = [{"ad_id": None, "mall_product_id": None, "use_group_bid_amt": False}]
    assert harness._writeback_live_ad_observation(
        db, adgroup_id=GRP, ads=bad, campaign_id=CAMP, now=NOW) == 0
    assert db.query(NaverAdgroupProduct).count() == 0


# ── harness 배선(거부 → 마커 + 수리) ────────────────────────────────────────────
def _approved_adgroup_proposal(db):
    # optimizer='ours'가 아니면 harness가 실행 직전 OptimizerGuardError로 막는다(D-NAO-13).
    db.add(NaverCampaignSettings(campaign_id=CAMP, optimizer="ours", auto_operate=True))
    p = NaverProposal(proposal_type="bid_down", target_type="adgroup", target_id=GRP,
                      campaign_id=CAMP, adgroup_id=GRP, rationale="[시간당밴드] 테스트",
                      expected_effect="테스트", status="approved", target_bid=680)
    db.add(p)
    db.commit()
    return p


def _run_execute(db, p, exc):
    """가드레일은 이 파일의 관심사가 아니다(별도 테스트가 지킨다) — 통과시키고 **거부 이후**만 본다."""
    with patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", side_effect=exc):
        with pytest.raises(type(exc)):
            harness.execute(db, p.id, dry_run=False, now=NOW)


def test_execute_marks_lever_mismatch_and_repairs(db):
    """★합격기준 ①②: change_log에 `[LEVER_MISMATCH]` + failed, 그리고 DB가 고쳐진다.

    시각은 KST 실행 시각이어야 한다(changed_at == executed_at == now) — B-1 가드(D-NAO-169)의
    규약이자, stale 로그를 현재로 착각하지 않기 위한 조건이다.
    """
    p = _approved_adgroup_proposal(db)
    _run_execute(db, p, _dead_error())

    entry = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert entry.outcome == "failed"
    assert harness.LEVER_MISMATCH_MARKER in entry.rationale
    assert harness.WRITE_FAILURE_MARKER in entry.rationale
    assert entry.changed_at == NOW and entry.executed_at == NOW
    db.refresh(p)
    assert p.status == "failed"
    # 수리가 실제로 일어났다
    assert db.query(NaverAdgroupProduct).count() == 2


def test_ordinary_write_failure_gets_no_mismatch_marker(db):
    """정상 입력 전수 — 다른 실패는 마커를 달면 안 된다.

    마커가 아무 실패에나 붙으면 「수리 루프가 고장 났다」는 신호가 잡음에 묻힌다
    (발견 0건과 실행 안 됨을 같은 숫자로 만들지 않는다).
    """
    p = _approved_adgroup_proposal(db)
    _run_execute(db, p, writer.WriteError("500 서버 오류"))
    entry = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).one()
    assert harness.LEVER_MISMATCH_MARKER not in entry.rationale
    assert harness.WRITE_FAILURE_MARKER in entry.rationale
    assert db.query(NaverAdgroupProduct).count() == 0  # 수리도 안 일어난다


def test_repair_failure_still_records_the_rejection(db):
    """수리가 실패해도 **거부 기록은 남는다**(fail-open의 계약)."""
    p = _approved_adgroup_proposal(db)
    with patch.object(harness, "_writeback_live_ad_observation", side_effect=RuntimeError("x")), \
         patch.object(harness, "_build_guardrail_context", return_value={"current_bid": 800}), \
         patch.object(harness.guardrail_gate, "check", return_value=None), \
         patch.object(harness.naver_sa_writer, "update_adgroup_bid", side_effect=_dead_error()):
        with pytest.raises(RuntimeError):
            harness.execute(db, p.id, dry_run=False, now=NOW)
    assert db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id == p.id).count() == 1


# ── 폐루프(이 파일의 존재 이유) ─────────────────────────────────────────────────
def test_router_verdict_flips_from_group_to_ad_after_repair(db):
    """★★합격기준 ④의 단위판: 수리 **직후** 라우터 판정이 'group' → 'ad'로 뒤집힌다.

    이게 안 뒤집히면 write-back은 로그만 예쁜 no-op다 — 다음 회차에도 그룹으로 보내고
    또 거부당한다(동결 지속). 「라우터가 스스로 절체한다」는 주장의 유일한 증거가 이것이다.
    """
    # 수리 전: 소재 행이 없다 → 유효 소재데이터 0건 → group 폴백
    before = effective_bid.adgroup_effective_bid(db, GRP, 200)
    assert (before["source"], before["has_ad_data"]) == ("group", False)

    p = _approved_adgroup_proposal(db)
    _run_execute(db, p, _dead_error())

    after = effective_bid.adgroup_effective_bid(db, GRP, 200)
    assert after["source"] == "ad", "수리 후 라우터는 소재 레버를 가리켜야 한다"
    assert after["max_ad_id"] == "nad-0"      # 800원짜리가 실효
    assert after["effective_bid"] == 800
