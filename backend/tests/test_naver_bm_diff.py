# test_naver_bm_diff.py — BM 벤치마크 레이어 Phase 2 단위 테스트 (SA-2 조작 감지, D-NAO-78)
# 커버: detect_agency_ops(스냅샷 D-1 vs D diff)의 op_type 감지·노이즈 필터 4종·멱등.
#   ★계획서 §3 실전 검증 사례(2026-07-22 실측: 그룹 신설·캠페인 status flip·bid_change)를
#   픽스처로 재현하고 is_exception 정합을 검증한다.
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAgencyOp, NaverChangeLog, NaverEntitySnapshot
from app.services.naver_ad.bm_diff import detect_agency_ops
from app.utils.kst import kst_now

D_PREV = date(2026, 7, 20)  # D-1
D_CURR = date(2026, 7, 21)  # D (조작 감지일)


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


def _snap(db, sdate, entity_type, entity_id, *, campaign_id="", campaign_type="", optimizer="none",
          status="on", bid_amt=None, keyword_count=None, daily_budget=None, extended_search=None,
          negative_kw_count=None, ad_count=None, name=""):
    db.add(NaverEntitySnapshot(
        snapshot_date=sdate, entity_type=entity_type, entity_id=entity_id,
        parent_id=campaign_id if entity_type == "adgroup" else "",
        campaign_id=campaign_id or entity_id, campaign_type=campaign_type, optimizer=optimizer,
        name=name, status=status, bid_amt=bid_amt, keyword_count=keyword_count,
        daily_budget=daily_budget, extended_search=extended_search,
        negative_kw_count=negative_kw_count, ad_count=ad_count, synced_at=kst_now(),
    ))


def _ops(db, op_date=D_CURR):
    return {
        (r.op_type, r.entity_id): r
        for r in db.query(NaverAgencyOp).filter(NaverAgencyOp.op_date == op_date).all()
    }


# ── 1. bootstrap 가드: D-1 스냅샷 없으면 0 이벤트 ──────────────────────────────
def test_bootstrap_skips_when_no_prev_snapshot(db):
    """최초 실행(전일 스냅샷 부재) → 전건 add 폭주 방지, 0 이벤트(§3-3)."""
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", bid_amt=300)
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert result["bootstrap"] is True
    assert result["events"] == 0
    assert db.query(NaverAgencyOp).count() == 0


# ── 2. ★실전 검증 사례 재현(2026-07-22 실측): 그룹 신설 3건 + status flip + bid_change ──
def test_realworld_20260722_group_adds_status_flip_bid_change(db):
    """대행사 07-21 원복 축소 재현: 갤럭시 파워링크에 신모델 그룹 3개 신설 + 캠페인 정지(userLock)
    + 기존 그룹 입찰 변경. SA-2가 정확히 감지하고 신설 3건만 is_exception=True인지 검증."""
    # D-1: 캠페인 on, 기존 그룹 1개(입찰 300)
    _snap(db, D_PREV, "campaign", "cmp-galaxy", campaign_type="WEB_SITE", status="on", name="갤럭시_파워링크")
    _snap(db, D_PREV, "adgroup", "grp-old", campaign_id="cmp-galaxy", campaign_type="WEB_SITE", bid_amt=300)
    # D: 캠페인 정지(on→off), 기존 그룹 입찰 300→330(+10%), 신모델 그룹 3개 신설
    _snap(db, D_CURR, "campaign", "cmp-galaxy", campaign_type="WEB_SITE", status="off", name="갤럭시_파워링크")
    _snap(db, D_CURR, "adgroup", "grp-old", campaign_id="cmp-galaxy", campaign_type="WEB_SITE", bid_amt=330)
    for gid, nm in [("grp-fold8", "폴드8"), ("grp-flip8", "플립8"), ("grp-new3", "신모델")]:
        _snap(db, D_CURR, "adgroup", gid, campaign_id="cmp-galaxy", campaign_type="WEB_SITE", bid_amt=250, name=nm)
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)

    # 그룹 신설 3건 + 캠페인 status flip 1건 + bid_change 1건 = 5 이벤트
    assert result["events"] == 5
    adds = [k for k in ops if k[0] == "adgroup_add"]
    assert len(adds) == 3
    assert all(ops[k].is_exception is True for k in adds)  # 구조 신설=항상 예외

    # 캠페인 정지(status_flip): 감지되되 대행사 소형 이벤트라 is_exception=False(§3 명시 임계 밖)
    flip = ops[("status_flip", "cmp-galaxy")]
    assert flip.before_value == "on" and flip.after_value == "off"
    assert flip.is_exception is False

    # bid_change +10%: 감지되되 <20%라 is_exception=False, magnitude=Δ%
    bid = ops[("bid_change", "grp-old")]
    assert bid.before_value == "300" and bid.after_value == "330"
    assert bid.magnitude == pytest.approx(10.0)
    assert bid.is_exception is False

    # 예외 = 신설 3건만
    assert result["exceptions"] == 3


# ── 3. 입찰 지터(<3%) 무시 · 대형 입찰(≥20%) 예외 ────────────────────────────
def test_bid_jitter_ignored_and_large_bid_is_exception(db):
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_PREV, "adgroup", "grp-jitter", campaign_id="cmp-a", bid_amt=1000)
    _snap(db, D_PREV, "adgroup", "grp-big", campaign_id="cmp-a", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_CURR, "adgroup", "grp-jitter", campaign_id="cmp-a", bid_amt=1020)  # +2% 지터
    _snap(db, D_CURR, "adgroup", "grp-big", campaign_id="cmp-a", bid_amt=1300)     # +30% 대형
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ("bid_change", "grp-jitter") not in ops  # 지터 무시
    assert ops[("bid_change", "grp-big")].is_exception is True  # ≥20% 예외


# ── 4. ours 자기변경 제외 · 미매칭 ours = 외부 개입 예외 ─────────────────────
def test_ours_self_change_excluded_but_external_intervention_flagged(db):
    """optimizer='ours' 변경: 최근 48h 우리 change_log 매칭 → 제외. 미매칭 → 외부 개입 예외."""
    # grp-mine: 우리가 입찰을 바꿈(change_log 존재) → agency_op 제외
    # grp-ext: ours인데 우리 기록 없음 → 외부가 우리 캠페인 건드림 → is_exception=True
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_PREV, "adgroup", "grp-ext", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)  # +30%
    _snap(db, D_CURR, "adgroup", "grp-ext", campaign_id="cmp-ours", optimizer="ours", bid_amt=530)   # +6% 소형
    # 우리 실집행 기록(grp-mine만) — 최근(48h 내)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-mine", campaign_id="cmp-ours",
        action="update_bid", dry_run=False, changed_at=kst_now() - timedelta(hours=2),
        after_value='{"bidAmt": 650}',
    ))
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ("bid_change", "grp-mine") not in ops  # 우리 손 → 제외
    ext = ops[("bid_change", "grp-ext")]           # 미매칭 ours → 외부 개입
    assert ext.is_exception is True                 # 소형(+6%)이어도 외부 개입은 예외 승격


def test_ours_change_outside_48h_window_not_matched(db):
    """우리 기록이 48h보다 오래됐으면 매칭 실패 → 외부 개입으로 기록(시간창 경계)."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)
    db.add(NaverChangeLog(
        entity_type="adgroup", entity_id="grp-mine", campaign_id="cmp-ours",
        action="update_bid", dry_run=False, changed_at=kst_now() - timedelta(hours=72),  # 창 밖
        after_value='{"bidAmt": 650}',
    ))
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ops[("bid_change", "grp-mine")].is_exception is True  # 오래된 기록 → 미매칭 → 외부 개입


# ── 5. deleted 가드: remove 1회만, 다음 날 재발화 금지 ───────────────────────
def test_deleted_guard_fires_remove_once(db):
    """on→deleted 전이 시 remove 1회 기록, 이후 deleted였던 엔티티는 재발화 금지(§3-2)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_PREV, "adgroup", "grp-del", campaign_id="cmp-a", status="on", bid_amt=300)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_CURR, "adgroup", "grp-del", campaign_id="cmp-a", status="deleted", bid_amt=300)
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ops[("adgroup_remove", "grp-del")].is_exception is True

    # 다음 날: grp-del 완전 소실(prev=D는 deleted) → 재발화 금지
    d_next = D_CURR + timedelta(days=1)
    _snap(db, d_next, "campaign", "cmp-a", campaign_type="WEB_SITE")
    db.commit()
    result_next = detect_agency_ops(db, op_date=d_next)
    assert not any(k[0] == "adgroup_remove" for k in _ops(db, d_next))
    assert result_next["events"] == 0


# ── 6. 키워드 수 증감 집계 이벤트 ────────────────────────────────────────────
def test_keyword_count_delta_event(db):
    """그룹 keyword_count 증감을 keyword_add/remove 집계 이벤트로(entity=그룹, before/after=count)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_PREV, "adgroup", "grp-kw", campaign_id="cmp-a", campaign_type="WEB_SITE", keyword_count=10)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_CURR, "adgroup", "grp-kw", campaign_id="cmp-a", campaign_type="WEB_SITE", keyword_count=13)
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    add = _ops(db)[("keyword_add", "grp-kw")]
    assert add.before_value == "10" and add.after_value == "13"
    assert add.magnitude == pytest.approx(3.0)


def test_p3_columns_both_null_no_event(db):
    """예산·확장검색·제외·소재는 P1/P2에서 양쪽 NULL(미수집) → 자연 비활성(이벤트 0)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", bid_amt=300)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", bid_amt=300)  # 아무 변화 없음
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert result["events"] == 0  # budget/extended/negative/creative 전부 NULL → 스킵


# ── 7. 멱등: 같은 날 재실행 = 중복 이벤트 없음 ───────────────────────────────
def test_idempotent_same_day_rerun(db):
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", status="on")
    _snap(db, D_PREV, "adgroup", "grp-old", campaign_id="cmp-a", bid_amt=300)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", status="off")  # flip
    _snap(db, D_CURR, "adgroup", "grp-old", campaign_id="cmp-a", bid_amt=400)       # +33% bid
    _snap(db, D_CURR, "adgroup", "grp-new", campaign_id="cmp-a", bid_amt=250)       # add
    db.commit()

    r1 = detect_agency_ops(db, op_date=D_CURR)
    n1 = db.query(NaverAgencyOp).count()
    r2 = detect_agency_ops(db, op_date=D_CURR)  # 재실행
    n2 = db.query(NaverAgencyOp).count()

    assert r1["events"] == r2["events"]
    assert n1 == n2  # 삭제-재생성 → 중복 없음
