# test_naver_bm_diff.py — BM 벤치마크 레이어 Phase 2 단위 테스트 (SA-2 조작 감지, D-NAO-78)
# 커버: detect_agency_ops(스냅샷 D-1 vs D diff)의 op_type 감지·노이즈 필터 5종·멱등.
#   ★계획서 §3 실전 검증 사례(2026-07-22 실측: 그룹 신설·캠페인 status flip·bid_change)를
#   픽스처로 재현하고 is_exception 정합을 검증한다.
#   ★2026-07-27 추가: "우리 변경의 지연 반영(echo)" 오탐 수정 회귀 테스트(§8) — 스냅샷이
#   실집행보다 1~2일 늦게 값을 반영해 우리 손이 대행사 조작으로 기록된 03 캠페인 실측 7건.
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAgencyOp, NaverChangeLog, NaverEntitySnapshot
from app.services.naver_ad.bm_diff import ECHO_LOOKBACK_D, _window, detect_agency_ops
from app.utils.kst import kst_now

D_PREV = date(2026, 7, 20)  # D-1
D_CURR = date(2026, 7, 21)  # D (조작 감지일)
SNAP_H, SNAP_M = 7, 37  # 스냅샷 실행 시각(07:37 KST) — change_log 조회창의 상한 앵커
# 조회창: until=D 스냅샷 synced_at / ours=until-48h / echo=until-3d
IN_OURS = datetime(2026, 7, 20, 9, 0)        # 48h·3일 창 모두 안
IN_ECHO_ONLY = datetime(2026, 7, 19, 0, 0)   # 3일 창 안·48h 창 밖(=지연 반영 구간)
AFTER_SNAP = datetime(2026, 7, 21, 20, 0)    # D 스냅샷 이후 — 이 diff에 반영될 수 없음
OUT_ALL = datetime(2026, 7, 17, 12, 0)       # 두 창 모두 밖
# ★D-NAO-93 필드 출처별 관측 시각(실측: entity_sync 커밋이 bm 스냅샷보다 늦어 D 스냅샷의
# 입찰·상태는 D-1 07:35 관측값 / 예산·확장검색은 스냅샷 시작 몇 분 뒤 P3 GET 관측값)
ENTITY_OBS = datetime(2026, 7, 20, 7, 35)    # D 스냅샷의 entity 관측 시각
ENTITY_OBS_LATE = datetime(2026, 7, 21, 7, 38)  # 스냅샷 복사(07:37)보다 늦은 관측 = load_until 경계
P3_OBS = datetime(2026, 7, 21, 7, 42)        # D 스냅샷의 P3 GET 관측 시각
IN_ENTITY_LAG = IN_OURS                      # entity 관측 후 ~ 스냅샷 복사 전 = 구 코드의 미탐 밴드
IN_P3_LAG = datetime(2026, 7, 21, 7, 40)     # 스냅샷 복사 후 ~ P3 GET 전 = 구 코드의 오탐 밴드
# P3는 캠페인별 순차 GET이라 행마다 관측 시각이 다르다(전역 max 금지 — codex[P1])
P3_OBS_EARLY = datetime(2026, 7, 21, 7, 38)  # 먼저 관측된 캠페인
P3_OBS_LATE = datetime(2026, 7, 21, 7, 50)   # 나중 관측된 캠페인
IN_P3_ROW_GAP = datetime(2026, 7, 21, 7, 45)  # early 관측 후 · late 관측 전


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
          negative_kw_count=None, ad_count=None, name="", synced_at=None,
          entity_observed_at=None, p3_observed_at=None):
    # synced_at 기본값 = 그 날 07:37(실제 스냅샷 크론 시각). change_log 조회창의 상한이므로
    # kst_now()가 아니라 스냅샷 날짜에 묶어야 리플레이가 결정적이다.
    # entity_observed_at·p3_observed_at 기본값 = NULL(구 행) → bm_diff가 synced_at으로 폴백.
    synced_at = synced_at or datetime.combine(sdate, time(SNAP_H, SNAP_M))
    db.add(NaverEntitySnapshot(
        snapshot_date=sdate, entity_type=entity_type, entity_id=entity_id,
        parent_id=campaign_id if entity_type == "adgroup" else "",
        campaign_id=campaign_id or entity_id, campaign_type=campaign_type, optimizer=optimizer,
        name=name, status=status, bid_amt=bid_amt, keyword_count=keyword_count,
        daily_budget=daily_budget, extended_search=extended_search,
        negative_kw_count=negative_kw_count, ad_count=ad_count, synced_at=synced_at,
        entity_observed_at=entity_observed_at, p3_observed_at=p3_observed_at,
    ))


def _ops(db, op_date=D_CURR):
    return {
        (r.op_type, r.entity_id): r
        for r in db.query(NaverAgencyOp).filter(NaverAgencyOp.op_date == op_date).all()
    }


def _log(db, entity_id, action, after, *, at, before=None, entity_type="adgroup", campaign_id="cmp-a"):
    """우리 실집행 change_log 1행(dry_run=False). before/after는 writer가 재조회 응답을 그대로
    json.dumps 한 dict 형태({"bidAmt":N} / {"userLock":bool} / {"dailyBudget":N})."""
    db.add(NaverChangeLog(
        entity_type=entity_type, entity_id=entity_id, campaign_id=campaign_id,
        action=action, dry_run=False, changed_at=at,
        before_value=None if before is None else json.dumps(before),
        after_value=json.dumps(after),
    ))


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

    # 캠페인 정지(status_flip): 캠페인 grain은 항상 is_exception=True로 승격(P2 리뷰 반영 —
    # 캠페인 통째 정지/재개는 구조적 대사건, 대형변화 임계와 무관하게 예외)
    flip = ops[("status_flip", "cmp-galaxy")]
    assert flip.before_value == "on" and flip.after_value == "off"
    assert flip.is_exception is True

    # bid_change +10%: 감지되되 <20%라 is_exception=False, magnitude=Δ%
    bid = ops[("bid_change", "grp-old")]
    assert bid.before_value == "300" and bid.after_value == "330"
    assert bid.magnitude == pytest.approx(10.0)
    assert bid.is_exception is False

    # 예외 = 신설 3건 + 캠페인 status flip 1건 = 4건
    assert result["exceptions"] == 4


# ── 2b. 캠페인 vs 그룹 status_flip 예외 승격 차등(P2 리뷰 지적, 2026-07-22) ──────────
def test_campaign_status_flip_promoted_but_adgroup_flip_not(db):
    """캠페인 grain status_flip = 항상 is_exception=True. 그룹 grain flip은 기존대로 False
    (대형변화·외부개입 판정에서만 예외 승격) — 같은 op_type이라도 grain별 차등."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", status="on")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", campaign_type="WEB_SITE", status="on", bid_amt=300)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", status="off")   # 캠페인 flip
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", campaign_type="WEB_SITE", status="off", bid_amt=300)  # 그룹 flip
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ops[("status_flip", "cmp-a")].is_exception is True    # 캠페인 = 항상 예외
    assert ops[("status_flip", "grp-a")].is_exception is False   # 그룹 = 기존대로 비예외


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
    """optimizer='ours' 변경: op_date 기준 48h 창의 우리 change_log 매칭 → 제외. 미매칭 → 외부 개입 예외.

    ★시간창 앵커는 kst_now가 아니라 op_date다(리플레이 멱등) — 그래서 픽스처의 changed_at도
    실행 시각이 아니라 D_CURR 기준으로 놓는다."""
    # grp-mine: 우리가 입찰을 바꿈(change_log 존재) → agency_op 제외
    # grp-ext: ours인데 우리 기록 없음 → 외부가 우리 캠페인 건드림 → is_exception=True
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_PREV, "adgroup", "grp-ext", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)  # +30%
    _snap(db, D_CURR, "adgroup", "grp-ext", campaign_id="cmp-ours", optimizer="ours", bid_amt=530)   # +6% 소형
    # 우리 실집행 기록(grp-mine만) — op_date 기준 48h 창 안
    _log(db, "grp-mine", "update_bid", {"bidAmt": 650}, at=IN_OURS, campaign_id="cmp-ours")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ("bid_change", "grp-mine") not in ops  # 우리 손 → 제외
    ext = ops[("bid_change", "grp-ext")]           # 미매칭 ours → 외부 개입
    assert ext.is_exception is True                 # 소형(+6%)이어도 외부 개입은 예외 승격


def test_ours_change_outside_all_windows_not_matched(db):
    """우리 기록이 조회창(48h·echo 3일) 모두 밖이면 매칭 실패 → 외부 개입으로 기록(시간창 경계)."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)
    _log(db, "grp-mine", "update_bid", {"bidAmt": 650}, at=OUT_ALL, campaign_id="cmp-ours")  # 창 밖
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ops[("bid_change", "grp-mine")].is_exception is True  # 오래된 기록 → 미매칭 → 외부 개입


def test_ours_lagged_write_beyond_48h_still_echo_matched(db):
    """48h 창 밖이어도 echo 창(3일) 안이고 after 값이 일치하면 '지연 반영'으로 제외한다.
    (구 코드는 여기서 우리 캠페인에 대한 외부 개입 예외로 잘못 승격했다)"""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)
    _log(db, "grp-mine", "update_bid", {"bidAmt": 650}, at=IN_ECHO_ONLY, campaign_id="cmp-ours")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-mine") not in _ops(db)


def test_ours_lagged_write_with_different_value_is_external(db):
    """echo 창 안 기록이라도 after 값이 다르면 우리 손의 증거가 아니다 → 외부 개입 예외 유지."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)
    _log(db, "grp-mine", "update_bid", {"bidAmt": 600}, at=IN_ECHO_ONLY, campaign_id="cmp-ours")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert _ops(db)[("bid_change", "grp-mine")].is_exception is True


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


# ── 8. ★우리 변경의 지연 반영(echo) 오탐 수정 회귀 (2026-07-27 실측 7건) ──────────
# 스냅샷은 매일 07:37 KST에 naver_entity를 복사하므로 우리 실집행이 1~2일 늦게 diff에 나타난다.
# 구 코드는 optimizer=='ours' 행만 change_log와 대조했고 값 일치도 안 봐서, D-NAO-92로
# optimizer=none이 된 03 캠페인의 우리 변경이 전부 "대행사 bid_change"로 기록됐다.
def test_echo_of_our_write_not_recorded_when_optimizer_none(db):
    """실측 재현: 07-23 21:51 EX확장 실집행(1,970→2,260)이 optimizer=none 그룹의 익일 스냅샷
    diff로 나타난 건 — 대행사 조작이 아니라 우리 변경의 지연 반영이므로 미기록."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=2260)
    _log(db, "grp-15pro", "update_bid", {"bidAmt": 2260}, before={"bidAmt": 1970},
         at=IN_ECHO_ONLY)  # 1~2일 전 실집행
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-15pro") not in _ops(db)
    assert result["events"] == 0


def test_echo_matches_intermediate_ladder_step(db):
    """탐색UP 래더(90→180→270): 스냅샷 diff가 중간 스텝(90→180)에 걸려도, 창 안 실집행 로그
    **전체 중 하나라도** after가 일치하면 echo로 인정한다(최신 1건만 보면 놓친다)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-13mini", campaign_id="cmp-a", optimizer="none", bid_amt=90)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-13mini", campaign_id="cmp-a", optimizer="none", bid_amt=180)
    _log(db, "grp-13mini", "update_bid", {"bidAmt": 180}, before={"bidAmt": 90},
         at=datetime(2026, 7, 19, 18, 20))
    _log(db, "grp-13mini", "update_bid", {"bidAmt": 270}, before={"bidAmt": 180},
         at=datetime(2026, 7, 19, 20, 20))  # 최신
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-13mini") not in _ops(db)


def test_real_agency_change_still_recorded_when_after_value_differs(db):
    """진짜 대행사 조작(우리 로그의 after와 값이 다름)은 기존대로 기록·예외 판정 유지."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=2500)
    _log(db, "grp-15pro", "update_bid", {"bidAmt": 2260}, at=IN_ECHO_ONLY)  # 우리는 2,260까지만
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    op = _ops(db)[("bid_change", "grp-15pro")]
    assert op.after_value == "2500"
    assert op.is_exception is True  # +26.9% ≥ 20% 대형 변화


def test_echo_string_bid_value_normalized(db):
    """SQLite 동적 타입으로 after_value가 문자열("2260")이어도 수치 정규화 후 비교(entity_sync
    _norm_bid와 같은 함정) — 타입 차이로 echo를 놓치지 않는다."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-15pro", campaign_id="cmp-a", optimizer="none", bid_amt=2260)
    _log(db, "grp-15pro", "update_bid", {"bidAmt": "2260"}, at=IN_ECHO_ONLY)
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-15pro") not in _ops(db)


def test_echo_status_flip_and_budget(db):
    """set_user_lock(userLock=True → status 'off')·update_budget도 값 일치로 echo 인정."""
    _snap(db, D_PREV, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          status="on", daily_budget=30000)
    _snap(db, D_CURR, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          status="off", daily_budget=50000)
    _log(db, "cmp-b", "set_user_lock", {"userLock": True}, before={"userLock": False},
         at=IN_ECHO_ONLY, entity_type="campaign", campaign_id="cmp-b")
    _log(db, "cmp-b", "update_budget", {"dailyBudget": 50000}, before={"dailyBudget": 30000},
         at=IN_ECHO_ONLY, entity_type="campaign", campaign_id="cmp-b")
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert result["events"] == 0


def test_external_status_change_log_is_not_echo_evidence(db):
    """entity_sync가 남기는 external_status_change는 '외부가 바꿨다'는 관측 기록이지 우리 손의
    증거가 아니다 — 이걸 echo로 인정하면 대행사 정지가 통째로 묻힌다(기록 유지)."""
    _snap(db, D_PREV, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none", status="on")
    _snap(db, D_CURR, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none", status="off")
    _log(db, "cmp-b", "external_status_change", {"userLock": True}, at=IN_ECHO_ONLY,
         entity_type="campaign", campaign_id="cmp-b")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert _ops(db)[("status_flip", "cmp-b")].is_exception is True


def test_write_after_snapshot_does_not_suppress(db):
    """★codex[P1] R1: 조회창 상한은 D 스냅샷 관측 시각(07:37)이다. 스냅샷 이후(같은 날 20시)의
    우리 쓰기는 이 diff에 반영될 수 없으므로, 값이 우연히 겹쳐도 대행사 조작을 지우면 안 된다."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1500)
    _log(db, "grp-a", "update_bid", {"bidAmt": 1500}, at=AFTER_SNAP)  # 스냅샷 이후 우연 일치
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") in _ops(db)


def test_revert_after_our_ladder_is_recorded(db):
    """★codex[P1] R1: 우리가 90→180→270까지 올린 뒤 대행사가 270→180으로 되돌리면, 옛 로그의
    after=180이 우연히 맞더라도 echo가 아니다(스냅샷 before=270=우리 마지막 값 → 이탈 전이)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-lad", campaign_id="cmp-a", optimizer="none", bid_amt=270)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-lad", campaign_id="cmp-a", optimizer="none", bid_amt=180)
    _log(db, "grp-lad", "update_bid", {"bidAmt": 180}, at=datetime(2026, 7, 19, 18, 20))
    _log(db, "grp-lad", "update_bid", {"bidAmt": 270}, at=datetime(2026, 7, 19, 20, 20))
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-lad") in _ops(db)  # 되돌림 = 대행사 조작으로 기록


def test_external_bid_change_log_vetoes_echo(db):
    """★codex[P1] R1: entity_sync가 같은 값을 external_bid_change로 남겼으면 그쪽이 이긴다
    (동기화 시점에 '우리 쓰기로 설명 안 됨'을 이미 판정한 더 강한 근거)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1500)
    _log(db, "grp-a", "update_bid", {"bidAmt": 1500}, at=IN_ECHO_ONLY)          # 우리 옛 쓰기
    _log(db, "grp-a", "external_bid_change", {"bidAmt": 1500}, at=IN_OURS)      # 외부 귀속 관측
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") in _ops(db)


def test_non_integer_and_string_bool_values_do_not_match(db):
    """★codex[P2]: 값 정규화는 무손실이어야 한다 — 2260.9는 2260과 다르고, userLock="false"는
    bool이 아니라 판정 불가(문자열 "false"가 bool()로 True가 되어 정지로 오인되던 문제)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none", status="on")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none", status="off")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=2260)
    _log(db, "grp-a", "update_bid", {"bidAmt": 2260.9}, at=IN_ECHO_ONLY)  # 절삭하면 잘못 일치
    _log(db, "cmp-a", "set_user_lock", {"userLock": "false"}, at=IN_ECHO_ONLY,
         entity_type="campaign", campaign_id="cmp-a")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ("bid_change", "grp-a") in ops
    assert ("status_flip", "cmp-a") in ops


def test_ours_external_status_change_only_is_not_hidden(db):
    """★codex[P1] R2: optimizer='ours'라도 값 근거 없이 action만으로 제외하면 안 된다.
    외부 기록(external_status_change)만 있는 우리 캠페인 정지는 외부 개입으로 남아야 한다."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours", status="on")
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours", status="off")
    _log(db, "cmp-ours", "external_status_change", {"userLock": True}, at=IN_OURS,
         entity_type="campaign", campaign_id="cmp-ours")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert _ops(db)[("status_flip", "cmp-ours")].is_exception is True


def test_ours_failed_write_without_after_value_is_not_hidden(db):
    """★codex[P1] R2: 실패·가드거부 행(dry_run=False·after_value=None)은 우리 손의 증거가
    아니다 — ours 캠페인이어도 제외되면 안 된다."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=500)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-mine", campaign_id="cmp-ours", optimizer="ours", bid_amt=650)
    db.add(NaverChangeLog(  # 실패 시도 — after_value 없음
        entity_type="adgroup", entity_id="grp-mine", campaign_id="cmp-ours",
        action="update_bid", dry_run=False, changed_at=IN_OURS, after_value=None,
    ))
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert _ops(db)[("bid_change", "grp-mine")].is_exception is True


def test_ours_negative_add_keeps_action_only_match(db):
    """값 비교가 불가능한 집계 op(제외키워드 수)는 기존 action-only 매칭을 유지한다 —
    ours 캠페인의 제외키워드 추가는 그대로 제외(§3-1 규약 보존)."""
    _snap(db, D_PREV, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_PREV, "adgroup", "grp-neg", campaign_id="cmp-ours", optimizer="ours",
          bid_amt=500, negative_kw_count=3)
    _snap(db, D_CURR, "campaign", "cmp-ours", campaign_type="WEB_SITE", optimizer="ours")
    _snap(db, D_CURR, "adgroup", "grp-neg", campaign_id="cmp-ours", optimizer="ours",
          bid_amt=500, negative_kw_count=5)
    _log(db, "grp-neg", "add_negative_keyword", {"after": ["a", "b"]}, at=IN_OURS,
         campaign_id="cmp-ours")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("negative_add", "grp-neg") not in _ops(db)


def test_external_log_older_than_our_write_does_not_veto(db):
    """★codex[P2] R2: 외부 기록이 우리 쓰기보다 **먼저**였다면 veto하지 않는다 — 그 뒤 우리가
    같은 값을 쓴 것은 진짜 지연 반영이다(시간순서를 버리면 여기서 오탐)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1500)
    _log(db, "grp-a", "external_bid_change", {"bidAmt": 1500}, at=IN_ECHO_ONLY)  # 먼저
    _log(db, "grp-a", "update_bid", {"bidAmt": 1500}, at=IN_OURS)                # 나중 = 우리 손
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") not in _ops(db)


def test_third_value_before_breaks_causality_and_is_recorded(db):
    """★codex[P1] R3: 예산은 외부 귀속 기록(external_*)이 없다. 우리가 30,000→50,000을 쓴 뒤
    외부가 20,000으로 바꿨고(D-1 스냅샷) 오늘 다시 50,000으로 되돌린 경우 — 스냅샷 before
    (20,000)는 우리가 지나온 적 없는 제3의 값이라 우리 손일 수 없다(인과 정합 규칙)."""
    _snap(db, D_PREV, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=20000)
    _snap(db, D_CURR, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=50000)
    _log(db, "cmp-b", "update_budget", {"dailyBudget": 50000}, before={"dailyBudget": 30000},
         at=IN_ECHO_ONLY, entity_type="campaign", campaign_id="cmp-b")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("budget_change", "cmp-b") in _ops(db)


def test_partial_before_trail_does_not_reject_valid_echo(db):
    """★codex[P2] R4: 궤적의 시작(창 안 첫 쓰기의 before)을 모르면 인과 규칙을 건너뛴다.
    첫 쓰기 100→200의 before가 없고 둘째 200→300의 before만 있는 경우, 스냅샷 100→300은
    정당한 지연 반영이므로 대행사 조작으로 기록하면 안 된다."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=100)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=300)
    _log(db, "grp-a", "update_bid", {"bidAmt": 200}, at=datetime(2026, 7, 19, 10, 0))  # before 없음
    _log(db, "grp-a", "update_bid", {"bidAmt": 300}, before={"bidAmt": 200},
         at=datetime(2026, 7, 19, 12, 0))
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") not in _ops(db)


def test_echo_judgement_is_replay_deterministic(db):
    """리플레이 멱등(§9-1): 판정 앵커가 op_date라 과거 날짜를 다시 돌려도 결과가 같고,
    실행 시각(kst_now) 근처의 로그는 과거 op_date 판정에 영향을 주지 않는다."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-echo", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_PREV, "adgroup", "grp-now", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-echo", campaign_id="cmp-a", optimizer="none", bid_amt=2260)
    _snap(db, D_CURR, "adgroup", "grp-now", campaign_id="cmp-a", optimizer="none", bid_amt=1500)
    _log(db, "grp-echo", "update_bid", {"bidAmt": 2260}, at=IN_ECHO_ONLY)      # op_date 창 안
    _log(db, "grp-now", "update_bid", {"bidAmt": 1500}, at=kst_now())          # 실행 시각(창 밖)
    db.commit()

    r1 = detect_agency_ops(db, op_date=D_CURR)
    ops1 = set(_ops(db))
    r2 = detect_agency_ops(db, op_date=D_CURR)  # 리플레이
    ops2 = set(_ops(db))

    assert ops1 == ops2 and r1 == r2
    assert ("bid_change", "grp-echo") not in ops1   # 창 안 실집행 → echo 제외
    assert ("bid_change", "grp-now") in ops1        # 창 밖(오늘) 로그 → 판정에 영향 없음


# ── 9. ★D-NAO-93 필드 출처별 관측 시각 앵커 ──────────────────────────────────
# 구 코드는 창 상한이 synced_at(스냅샷 **복사** 시각) 하나였다. 입찰·상태는 그보다 앞선
# entity_sync 관측값이라 창이 ~24h 과대(미탐), 예산·확장검색은 스냅샷 시작 몇 분 뒤 P3 GET
# 관측값이라 창이 과소(오탐)였다. 이제 op_type별로 맞는 관측 시각을 상한으로 쓴다.
def test_null_observed_at_falls_back_to_synced_at(db):
    """하위호환: 관측 시각 컬럼이 NULL인 구 행은 상한이 synced_at(07:37) — 종전 동작 그대로.
    entity 관측(D-1 07:35) 이후의 우리 쓰기도 창 안이라 echo로 억제된다(구 판정 보존)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=2260)
    _log(db, "grp-a", "update_bid", {"bidAmt": 2260}, before={"bidAmt": 1970}, at=IN_ENTITY_LAG)
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") not in _ops(db)
    assert result["events"] == 0


def test_entity_observed_at_closes_missed_detection_band(db):
    """미탐 해소: 입찰은 entity_sync(D-1 07:35)가 관측한 값이다. 그 이후 ~ 스냅샷 복사(D 07:37)
    사이의 우리 쓰기는 이 스냅샷에 반영될 수 없으므로, 값이 우연히 겹쳐도 대행사 조작을
    지우면 안 된다(같은 픽스처가 NULL일 때는 위 테스트처럼 억제됐다)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1970)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none",
          entity_observed_at=ENTITY_OBS)
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=2260,
          entity_observed_at=ENTITY_OBS)
    _log(db, "grp-a", "update_bid", {"bidAmt": 2260}, before={"bidAmt": 1970}, at=IN_ENTITY_LAG)
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    op = _ops(db)[("bid_change", "grp-a")]
    assert op.before_value == "1970" and op.after_value == "2260"


def test_p3_lagged_write_without_observed_at_is_false_positive(db):
    """오탐 재현(구 동작): p3_observed_at이 NULL이면 상한이 07:37이라, 07:37~P3 GET(07:42)
    사이의 우리 update_budget이 창 밖으로 밀려 대행사 조작으로 오기록된다."""
    _snap(db, D_PREV, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=30000)
    _snap(db, D_CURR, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=50000)
    _log(db, "cmp-b", "update_budget", {"dailyBudget": 50000}, before={"dailyBudget": 30000},
         at=IN_P3_LAG, entity_type="campaign", campaign_id="cmp-b")
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("budget_change", "cmp-b") in _ops(db)


def test_p3_observed_at_closes_false_positive_band(db):
    """오탐 해소: 예산은 스냅샷 시작 몇 분 뒤 그 행의 P3 GET(07:42)이 관측한다. **그 행 관측
    이전**(07:40)의 우리 쓰기는 이미 스냅샷에 반영된 값이므로 echo로 억제돼야 한다."""
    _snap(db, D_PREV, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=30000)
    _snap(db, D_CURR, "campaign", "cmp-b", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=50000, p3_observed_at=P3_OBS)
    _log(db, "cmp-b", "update_budget", {"dailyBudget": 50000}, before={"dailyBudget": 30000},
         at=IN_P3_LAG, entity_type="campaign", campaign_id="cmp-b")
    db.commit()

    result = detect_agency_ops(db, op_date=D_CURR)
    assert ("budget_change", "cmp-b") not in _ops(db)
    assert result["events"] == 0


def test_p3_band_uses_row_own_observation_not_global_max(db):
    """★codex[P1]: P3 GET은 캠페인별 순차라 행마다 관측 시각이 다르다. 전역 max(07:50)를 상한으로
    쓰면 먼저 관측된 행(07:38)에서 **아직 반영될 수 없는** 07:45 쓰기까지 창 안에 들어와 억제된다
    — 상한은 그 행 자신의 p3_observed_at이어야 한다. 같은 시각의 동일 쓰기가 행에 따라 갈린다."""
    for cid in ("cmp-early", "cmp-late"):
        _snap(db, D_PREV, "campaign", cid, campaign_type="WEB_SITE", optimizer="none",
              daily_budget=30000)
        _log(db, cid, "update_budget", {"dailyBudget": 50000}, before={"dailyBudget": 30000},
             at=IN_P3_ROW_GAP, entity_type="campaign", campaign_id=cid)
    _snap(db, D_CURR, "campaign", "cmp-early", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=50000, p3_observed_at=P3_OBS_EARLY)
    _snap(db, D_CURR, "campaign", "cmp-late", campaign_type="WEB_SITE", optimizer="none",
          daily_budget=50000, p3_observed_at=P3_OBS_LATE)
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    ops = _ops(db)
    assert ("budget_change", "cmp-early") in ops      # 그 행 관측(07:38) 이후 쓰기 → 반영 불가 → 기록
    assert ("budget_change", "cmp-late") not in ops   # 그 행 관측(07:50) 이전 쓰기 → 반영됨 → echo


def test_external_log_at_observation_boundary_still_vetoes(db):
    """★codex[P1]: entity_sync는 external_bid_change의 changed_at과 NaverEntity.synced_at에
    **같은 now**를 쓴다. 상한이 반개구간이면 '그 관측이 남긴 외부 귀속 증거'가 경계에서 탈락해
    veto가 사라지고, 과거 우리 쓰기의 값이 우연히 같을 때 대행사 변경이 echo로 오억제된다.
    외부 증거는 상한을 닫아 판정한다(우리 쓰기는 반개구간 유지)."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none",
          entity_observed_at=ENTITY_OBS)
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1500,
          entity_observed_at=ENTITY_OBS)
    _log(db, "grp-a", "update_bid", {"bidAmt": 1500}, at=IN_ECHO_ONLY)         # 우리 옛 쓰기
    _log(db, "grp-a", "external_bid_change", {"bidAmt": 1500}, at=ENTITY_OBS)  # 관측과 동시각
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") in _ops(db)


def test_external_log_at_load_until_boundary_is_loaded(db):
    """★codex[P2]: 적재창 상한(load_until)도 닫혀야 한다. 밴드 상한이 곧 load_until인 경계
    (entity 관측이 스냅샷 복사보다 늦고 P3 스탬프가 없는 날 — bm_snapshot은 now를 NaverEntity
    조회 **전에** 잡으므로 그 틈에 entity_sync가 커밋하면 발생)에서 그 시각의 외부 귀속 로그가
    SQL 적재부터 탈락하면, _in_closed까지 갈 기회조차 없이 veto가 사라진다."""
    _snap(db, D_PREV, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none")
    _snap(db, D_PREV, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1000)
    _snap(db, D_CURR, "campaign", "cmp-a", campaign_type="WEB_SITE", optimizer="none",
          entity_observed_at=ENTITY_OBS_LATE)
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none", bid_amt=1500,
          entity_observed_at=ENTITY_OBS_LATE)  # > synced_at(07:37) → load_until = 이 값
    _log(db, "grp-a", "update_bid", {"bidAmt": 1500}, at=IN_ECHO_ONLY)
    _log(db, "grp-a", "external_bid_change", {"bidAmt": 1500}, at=ENTITY_OBS_LATE)  # == load_until
    db.commit()

    detect_agency_ops(db, op_date=D_CURR)
    assert ("bid_change", "grp-a") in _ops(db)


def test_deleted_zombie_stamp_does_not_widen_load_window(db):
    """★codex[P2]: entity_sync는 deleted 전이 때 synced_at을 갱신하지 않는데 bm_snapshot은
    deleted 행도 스냅샷한다 → entity_observed_at이 수개월 전인 좀비가 curr에 남는다(실측 82일).
    그 행은 remove op(=fallback 밴드)만 만들어 판정 기여가 0인데, 적재창 하한이 그걸 집으면
    change_log를 수개월 스캔한다(entity_id 인덱스 없음)."""
    _snap(db, D_CURR, "adgroup", "grp-live", campaign_id="cmp-a", optimizer="none",
          bid_amt=1000, entity_observed_at=ENTITY_OBS)
    _snap(db, D_CURR, "adgroup", "grp-zombie", campaign_id="cmp-a", optimizer="none",
          status="deleted", entity_observed_at=datetime(2026, 4, 30, 7, 35))  # 82일 전 좀비
    db.commit()

    curr = {
        (r.entity_type, r.entity_id): r
        for r in db.query(NaverEntitySnapshot).filter(
            NaverEntitySnapshot.snapshot_date == D_CURR).all()
    }
    win = _window(D_CURR, curr)
    assert win.load_from == ENTITY_OBS - timedelta(days=ECHO_LOOKBACK_D)  # 좀비 스탬프 무시


def test_band_until_is_clamped_into_load_window(db):
    """★codex[P2]: op은 prev(D-1) 행 스탬프를 실을 수 있는데(_detect_removed) _window는 curr만
    스캔한다. 지금은 remove가 _OP_STAMP에 없어 도달하지 않지만, 미래에 누가 넣으면 밴드 상한이
    적재창 밖으로 나가 '적재되지 않은 구간을 판정'하게 되어 조용히 틀린다 — 구조로 클램프한다."""
    _snap(db, D_CURR, "adgroup", "grp-a", campaign_id="cmp-a", optimizer="none",
          bid_amt=1000, entity_observed_at=ENTITY_OBS)
    db.commit()
    curr = {
        (r.entity_type, r.entity_id): r
        for r in db.query(NaverEntitySnapshot).filter(
            NaverEntitySnapshot.snapshot_date == D_CURR).all()
    }
    win = _window(D_CURR, curr)

    too_late = {"op_type": "bid_change", "entity_observed_at": win.load_until + timedelta(days=9)}
    too_early = {"op_type": "bid_change", "entity_observed_at": win.load_from - timedelta(days=9)}
    assert win.band(too_late).until == win.load_until
    assert win.band(too_early).until == win.load_from
