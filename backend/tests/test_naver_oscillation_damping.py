# test_naver_oscillation_damping.py — D-NAO-288 「진동차단 목표」 회귀 고정
#
# 무엇을 고정하나: 액셀(정착창=과거)과 브레이크(당일 누적)가 서로 다른 창을 보다가 생긴
# 되먹임 진동. prod 4일 실측(2026-09-02~05, 소재 nad-…554755092)에서 이 유닛은 54회 발화·
# 16회 실쓰기로 1,320원 → 1,330원(순변위 +0.8%)을 돌았다:
#   09-03  1320→1980(UP 3) → CPC급등 DOWN 14시간 연속 → 1230
#   09-04  1230→1620(UP 2) → 순위고삐 DOWN 12시간 연속 → 1010
# 이 파일의 ⓐⓑ는 그 두 날의 **판정 입력을 그대로 재현**하고, 종전 코드와 새 코드의 판정을
# **둘 다** 단언한다 — 「고쳤다」가 아니라 「무엇이 달라졌나」를 고정하기 위해서다.
#
# ⓒⓓ는 반대 방향의 고정이다: 자기유발분이 없으면 브레이크는 종전 그대로 살아 있어야 한다.
# ⓔ는 창 선언표가 «선언»으로만 남지 않게 실제 코드 소비와 대조한다(ref 131 §5 신3).
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProductBep,
    NaverProposal,
    OpsDiaryEntry,
)
from app.services.naver_ad import auto_operator

CAMPAIGN = "cmp-04"
TODAY = date(2026, 7, 20)
NOW = datetime(2026, 7, 20, 12, 20, 0)  # 시간당 크론 :20 — 실측 09-03 10:20·09-04 12:20과 같은 슬롯

# 보정계수 정전 시 `_settlement_roas_status`가 unknown으로 떨어지므로(codex 5R[P1-1]),
# 정착창 판정을 「측정됨」으로 만들려면 실측 비율 소스를 주입해야 한다.
_FACTOR_OK = {
    "factor": Decimal("1"), "factor_low": Decimal("1"), "factor_high": Decimal("1"),
    "factor_point": Decimal("1"), "source": "actual_revenue_ratio",
}


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


def _hour(h, *, imp, clk, cost, avg_rank=3.0, conv_cnt=0):
    return {"hour": h, "imp": imp, "clk": clk, "cost": cost, "avg_rank": avg_rank, "conv_cnt": conv_cnt}


def _seed_unit(db, *, target_id, target_roas=Decimal("2.0"), bep_roas=None, price=Decimal("1000")):
    """auto_operate 캠페인 + 활성 부모체인 + 당일 예산 스냅샷 + (선택) 상품 원가 매핑."""
    db.add(NaverCampaignSettings(
        campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours",
        target_roas_override=target_roas,
    ))
    db.add(NaverEntity(entity_type="campaign", entity_id=CAMPAIGN, campaign_id=CAMPAIGN,
                       campaign_type="WEB_SITE", status="on"))
    db.add(NaverEntity(entity_type="adgroup", entity_id="grp-1", parent_id=CAMPAIGN,
                       campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    db.add(NaverEntity(entity_type="keyword", entity_id=target_id, parent_id="grp-1",
                       campaign_id=CAMPAIGN, campaign_type="WEB_SITE", status="on"))
    db.add(NaverHourlySnapshot(
        snapshot_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=23),
        campaign_id=CAMPAIGN, ad_date=TODAY, snapshot_hour=23, cost=0, daily_budget=100000,
    ))
    if bep_roas is not None:
        db.add(NaverAdgroupProduct(adgroup_id="grp-1", campaign_id=CAMPAIGN, mall_product_id="p-osc"))
        db.add(NaverProductBep(
            channel_id=6, channel_product_id="p-osc", has_cost=True,
            selling_price=price, contribution_margin=price / 2, bep_roas=bep_roas,
        ))
    db.commit()


def _seed_settlement(db, *, keyword_id, clk, cost, conv_amt=0):
    """정착창(D-8~D-2) 한 날에 실적을 몰아 넣는다 — 창 «내부» 분포는 어느 게이트도 안 본다."""
    window_from, _ = auto_operator._settlement_window(TODAY)
    db.add(NaverAdDaily(
        ad_date=window_from, campaign_id=CAMPAIGN, campaign_type="WEB_SITE",
        adgroup_id="grp-1", keyword_id=keyword_id, imp=1000, clk=clk, cost=cost,
        conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


def _seed_own_bid_writes(db, *, target_id, steps, day=TODAY):
    """오늘 «우리» 입찰 실쓰기 이력 — B-veto가 자기유발 배수를 읽는 원장.

    steps = [(before_bid, after_bid), ...] 시간순. 실측 09-03의 1320→1510→1730→1980이 그 모양이다.
    """
    for i, (before, after) in enumerate(steps):
        db.add(NaverChangeLog(
            entity_type="keyword", entity_id=target_id, campaign_id=CAMPAIGN,
            action="update_bid", dry_run=False,
            before_value=json.dumps({"bidAmt": before, "userLock": False}),
            after_value=json.dumps({"bidAmt": after, "userLock": False}),
            changed_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=8 + i),
        ))
    db.commit()


def _judge(db, target_id, curve, now=NOW):
    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK):
        return auto_operator._judge_hourly(
            db, target_type="keyword", target_id=target_id, campaign_id=CAMPAIGN,
            curve=curve, now=now,
        )


# ══════════════ ⓐ A-veto — 「오늘 나쁘다」를 손에 쥐고도 액셀만 눈이 없던 자리 ══════════════

def test_a_veto_blocks_up_when_today_is_sub_bep_before_leash_spend_floor(db):
    """★2026-09-04 11:20 재현. 정착창은 합격(ROAS 3.0 ≥ 2.0)인데 **오늘 추정ROAS가 이미 BEP
    아래**(1.11 < 2.0)다. 고삐는 ④당일소진 ≥ 하루평균(900 < 1000)을 못 채워 침묵하고, 종전
    코드는 그 침묵 구간에서 정착창만 보고 **올렸다**. 한 시간 뒤 소진이 문턱을 넘으면 고삐가
    켜져 12시간 연속 되돌린다 — 그 왕복이 진동이다.

    새 코드는 같은 입력에서 **hold**다. 하향을 만들지 않는다(방향은 up→hold, down 아님).

    📄 픽스처의 전환 2건은 실측이 아니라 **하한 충족 조건**이다 — 09-04 11:20 그 시각의 당일
    전환 건수는 hh24 곡선이 남아 있지 않아(네이버 7일 보존·우리 미저장) **판정불능**이다.
    즉 이 테스트가 고정하는 것은 「그날이 이렇게 막혔다」가 아니라 「이 입력이면 막힌다」다."""
    _seed_unit(db, target_id="nkw-a", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-a", clk=20, cost=21000, conv_amt=63000)  # ROAS 3.0 ≥ 2.0 = ok
    curve = [                                    # 하루평균 = 21000/7 = 3000원 · baseline CPC 1050원
        _hour(9, imp=20, clk=1, cost=800),
        _hour(10, imp=20, clk=1, cost=800, conv_cnt=1),
        _hour(11, imp=20, clk=1, cost=800, conv_cnt=1),
    ]  # 당일소진 2400 < 3000(고삐 유보) · 전환 2 ≥ 하한 2 · 추정ROAS = 2×1000/2400 = 0.83 < BEP 2.0
       # 당일 CPC 800 ≤ 1050×2 (CPC급등 아님)

    # ── 종전 코드: 고삐가 「오늘 BEP 하회」를 알려주지 않던 시절(3번째 값 None) ──
    real_leash = auto_operator._intraday_loss_leash
    with patch.object(
        auto_operator, "_intraday_loss_leash",
        side_effect=lambda *a, **k: (lambda r: (r[0], r[1], None))(real_leash(*a, **k)),
    ):
        before = _judge(db, "nkw-a", curve)
    assert before["direction"] == "up", "종전 코드가 여기서 올리지 않았다면 재현 픽스처가 틀린 것이다"
    assert "정착창 실측" in before["reason"]

    # ── 새 코드: 같은 입력, A-veto가 상향만 멈춘다 ──
    after = _judge(db, "nkw-a", curve)
    assert after["direction"] == "hold"
    assert after.get("veto") == "accel"
    assert "오늘 증거 거부권" in after["reason"]


def test_a_veto_does_not_fire_when_today_verdict_is_unknown(db):
    """★「모름 ≠ 나쁨」 — 원가 미확인 상품은 오늘 BEP 하회를 **판정할 수 없다**(None).
    거기서 상향을 끊으면 그건 감쇠가 아니라 **브레이크 신설**이다. GATE P2-A-1이 below와
    unknown을 가른 것과 같은 규율(교훈 #123의 반대 방향 적용)."""
    _seed_unit(db, target_id="nkw-unk", bep_roas=None)  # 원가 매핑 없음 → sub_bep None
    _seed_settlement(db, keyword_id="nkw-unk", clk=20, cost=7000, conv_amt=21000)
    curve = [_hour(h, imp=20, clk=1, cost=300) for h in (9, 10, 11)]

    verdict = _judge(db, "nkw-unk", curve)
    assert verdict["direction"] == "up"          # 종전과 동일 — 모름은 막지 않는다
    assert verdict.get("veto") is None


def test_a_veto_does_not_choke_the_accelerator_on_zero_conversion_mornings(db):
    """★★이 테스트가 이 계약의 §7 방어선이다 — **A-veto가 D-NAO-85가 되지 않는지** 본다.

    `estimated_intraday_roas`는 전환지연 때문에 구조적으로 과소추정이고 자체 전환 하한이
    없다(intraday_roas.py 정직 경계②). 그래서 「오늘 BEP 하회」는 **아침에 전환 0인 유닛
    거의 전부**에서 참이다(추정ROAS 0 < BEP). 하한 없이 걸었다면 매일 아침 액셀이 통째로
    눌렸을 것이다 — ROAS +7%·매출 −52%를 만든 그 모양(북극성 §7).

    전환 0·소진 있음 → sub_bep는 True지만 **하한 미달이라 거부권이 안 걸리고 UP은 산다.**
    이 단언이 깨지면 브레이크만 남은 것이다."""
    _seed_unit(db, target_id="nkw-morning", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-morning", clk=20, cost=21000, conv_amt=63000)
    curve = [_hour(h, imp=20, clk=1, cost=800) for h in (9, 10, 11)]  # 전환 0 → 추정ROAS 0 < BEP 2.0

    # 전제 확인: 고삐는 「오늘 BEP 하회」를 True로 본다(하한이 없으면 여기서 UP이 죽는다)
    _fired, _reason, sub_bep = auto_operator._intraday_loss_leash(
        db, target_type="keyword", target_id="nkw-morning", campaign_id=CAMPAIGN,
        curve=curve, now=NOW,
        baseline_agg={"clk": 20, "cost": 21000, "conv_amt": 63000, "conv_cnt": 0},
    )
    assert sub_bep is True

    verdict = _judge(db, "nkw-morning", curve)
    assert verdict["direction"] == "up", "전환 하한이 사라지면 아침 액셀이 통째로 눌린다(§7 위반)"
    assert verdict.get("veto") is None


# ══════════ ⓑⓒⓓ B-veto — 엔진이 만든 CPC 상승이 엔진의 정지 근거가 되던 자리 ══════════

def _spike_fixture(db, target_id):
    """★2026-09-03 재현: 정착창 CPC 794.7원 · 당일 CPC 1683.3원.
    종전 문턱 794.7×2 = 1,589.4원을 넘겨 14시간 연속 DOWN이 나갔던 그 숫자다."""
    _seed_unit(db, target_id=target_id, bep_roas=None)  # 고삐·장중UP은 판정 불가(원가 없음)
    _seed_settlement(db, keyword_id=target_id, clk=10, cost=7947)  # baseline_cpc = 794.7
    return [
        _hour(9, imp=20, clk=2, cost=3367),
        _hour(10, imp=20, clk=2, cost=3367),
        _hour(11, imp=20, clk=2, cost=3366),
    ]  # 당일 CPC = 10100/6 = 1683.3원


def test_b_veto_suppresses_cpc_spike_that_our_own_bid_raise_explains(db):
    """★진동의 핵심 고리. 우리가 오늘 1,320 → 1,980원(×1.5)으로 올려 놓고, 그래서 오른 CPC를
    근거로 우리 브레이크가 걸렸다. 새 문턱은 794.7×2×**1.5** = 2,384.1원이라 1,683.3원은
    급등이 아니다 — 자기가 만든 결과를 자기 정지 근거로 쓰지 않는다."""
    curve = _spike_fixture(db, "nkw-b")
    _seed_own_bid_writes(db, target_id="nkw-b", steps=[(1320, 1510), (1730, 1980)])  # 배수 1.5

    # ── 종전 코드: 자기유발 배수를 안 보던 시절(항상 1.0) ──
    with patch.object(auto_operator, "_own_bid_multiple_today", return_value=(Decimal(1), "종전")):
        before = _judge(db, "nkw-b", curve)
    assert before["direction"] == "down"
    assert "CPC급등" in before["reason"]

    # ── 새 코드: 같은 입력, 자기유발분을 벗기니 문턱 아래 ──
    after = _judge(db, "nkw-b", curve)
    assert after["direction"] != "down"
    assert "CPC급등 보류(자기유발분)" in after["cpc_spike_self_caused"]
    assert "1320→1980원" in after["cpc_spike_self_caused"]


def test_b_veto_inert_when_we_did_not_raise_today(db):
    """★회귀 0 증명 — 오늘 우리 쓰기가 0건이면 배수 1.0이라 **종전과 완전 동일하게** DOWN.
    이 저장소의 상습 실패 모드가 「완화 한 줄이 전체를 조용히 초록으로 만드는 것」이라
    반대 방향을 따로 못박는다."""
    curve = _spike_fixture(db, "nkw-c")
    verdict = _judge(db, "nkw-c", curve)          # change_log 0행
    assert verdict["direction"] == "down"
    assert "CPC급등" in verdict["reason"]
    assert verdict.get("cpc_spike_self_caused") is None


def test_b_veto_does_not_shield_exogenous_spike_beyond_our_own_raise(db):
    """★브레이크 생존 — 우리 상향(×1.5)으로 설명되는 범위를 **넘는** 급등은 여전히 DOWN.
    당일 CPC 2,700원 > 794.7×2×1.5 = 2,384.1원. B-veto는 브레이크를 없애지 않는다."""
    _seed_unit(db, target_id="nkw-d", bep_roas=None)
    _seed_settlement(db, keyword_id="nkw-d", clk=10, cost=7947)
    _seed_own_bid_writes(db, target_id="nkw-d", steps=[(1320, 1510), (1730, 1980)])
    curve = [_hour(h, imp=20, clk=2, cost=5400) for h in (9, 10, 11)]  # CPC = 16200/6 = 2700

    verdict = _judge(db, "nkw-d", curve)
    assert verdict["direction"] == "down"
    assert "자기상향 1.50배" in verdict["reason"]  # 벗기고도 넘었다는 사실을 사유문이 말한다


def test_own_bid_multiple_never_tightens_the_brake(db):
    """★대칭 방어 — 오늘 우리가 **내렸으면** 배수가 1 미만이 되어 문턱이 낮아지고 브레이크가
    더 쉽게 걸린다. 그건 감쇠가 아니라 브레이크 단독 강화(§7 위반)라 1.0에서 바닥을 친다."""
    _seed_unit(db, target_id="nkw-e", bep_roas=None)
    _seed_own_bid_writes(db, target_id="nkw-e", steps=[(2000, 1800), (1800, 1200)])  # 순하향
    multiple, reason = auto_operator._own_bid_multiple_today(db, "keyword", "nkw-e", NOW)
    assert multiple == Decimal(1)
    assert "순상향 없음" in reason


def test_own_bid_multiple_falls_back_to_one_when_snapshot_unreadable(db):
    """★fail-closed — 입찰가를 못 읽으면 1.0이다. 「모름」을 「우리가 올렸다」로 읽으면
    브레이크가 근거 없이 꺼진다."""
    _seed_unit(db, target_id="nkw-f", bep_roas=None)
    db.add(NaverChangeLog(
        entity_type="keyword", entity_id="nkw-f", campaign_id=CAMPAIGN, action="update_bid",
        dry_run=False, before_value="not json at all", after_value=json.dumps({"bidAmt": 1980}),
        changed_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8),
    ))
    db.commit()
    multiple, reason = auto_operator._own_bid_multiple_today(db, "keyword", "nkw-f", NOW)
    assert multiple == Decimal(1)
    assert "판독 불가" in reason


def test_own_bid_multiple_reads_ad_grain_bid_path(db):
    """소재(ad) grain은 입찰가가 `adAttr.bidAmt`에 산다(prod 실측 155행) — 최상위 `bidAmt`만
    보면 실집행의 **전부**를 놓친다(최근 30일 실집행 15건이 전부 ad grain, D-NAO-286)."""
    _seed_unit(db, target_id="nkw-g", bep_roas=None)
    for i, (before, after) in enumerate([(1320, 1510), (1730, 1980)]):
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-osc", campaign_id=CAMPAIGN, action="update_bid",
            dry_run=False,
            before_value=json.dumps({"nccAdId": "nad-osc", "adAttr": {"bidAmt": before}}),
            after_value=json.dumps({"nccAdId": "nad-osc", "adAttr": {"bidAmt": after}}),
            changed_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8 + i),
        ))
    db.commit()
    multiple, reason = auto_operator._own_bid_multiple_today(db, "ad", "nad-osc", NOW)
    assert multiple == Decimal("1.5")
    assert "1320→1980원" in reason


# ══════════════════ ⓔ 창 선언표가 «선언»으로 끝나지 않게 — 코드와 대조 ══════════════════

def test_window_declaration_covers_every_hourly_gate_and_both_sides():
    """선언표는 시간당 레인의 네 게이트를 **전부** 담고, 액셀·브레이크 양쪽을 다 갖는다.
    한쪽이 비면 그 자체가 §7 비대칭 신호다."""
    table = auto_operator._HOURLY_DECISION_WINDOWS
    assert set(table) == {"roas_up", "intraday_up", "cpc_spike", "loss_leash"}
    sides = [g["side"] for g in table.values()]
    assert sides.count("accel") == 2 and sides.count("brake") == 2
    # 진동의 좌표: 브레이크 두 갈래 **전부** 자기 행동이 섞인 창을 본다. 이 사실이 표에서 읽혀야 한다.
    assert all(g["self_mixed"] for g in table.values() if g["side"] == "brake")
    # 액셀 쪽은 갈라진다 — 정착창은 과거만(오늘을 못 본다), 장중 tally는 오늘을 본다.
    assert table["roas_up"]["self_mixed"] is False
    assert table["intraday_up"]["self_mixed"] is True


def test_declared_settlement_window_is_the_window_the_judge_actually_reads(db):
    """★선언 ↔ 집행 대조. 「정착창을 본다」고 적어 둔 게이트가 실제로 `_settlement_window(오늘)`
    경계로 `_settlement_agg`를 부르는지 본다 — 표만 고치고 코드를 지나가는 일(ref 131 §5 신3의
    그 병)을 막는다."""
    _seed_unit(db, target_id="nkw-w", bep_roas=None)
    _seed_settlement(db, keyword_id="nkw-w", clk=10, cost=1000)
    curve = [_hour(h, imp=20, clk=2, cost=100) for h in (9, 10, 11)]
    expected = auto_operator._settlement_window(TODAY)

    real_agg = auto_operator._settlement_agg
    seen: list[tuple] = []

    def _spy(db_, target_type, target_id, date_from, date_to):
        seen.append((date_from, date_to))
        return real_agg(db_, target_type, target_id, date_from, date_to)

    with patch.object(auto_operator, "_settlement_agg", side_effect=_spy):
        _judge(db, "nkw-w", curve)

    assert seen, "정착창을 본다고 선언한 게이트가 정착창을 한 번도 안 읽었다"
    assert set(seen) == {expected}, f"선언 {expected} ≠ 실제 {set(seen)}"
    assert (expected[1] - expected[0]).days + 1 == 7  # 창 «길이»는 이번 계약의 안 함 — 7일 고정


def test_today_window_gates_read_only_the_passed_curve(db):
    """「당일 hh24를 본다」고 선언한 게이트(CPC급등)가 **넘겨받은 곡선 그 객체**를 쓰는지 본다.
    당일 실적을 DB에서 따로 조회하기 시작하면 판정창과 실행창이 갈라진다(D-NAO-265 재발)."""
    _seed_unit(db, target_id="nkw-t", bep_roas=None)
    _seed_settlement(db, keyword_id="nkw-t", clk=10, cost=1000)
    curve = [_hour(h, imp=20, clk=2, cost=100) for h in (9, 10, 11)]

    real_cpc = auto_operator._today_group_cpc
    seen_curves: list[int] = []

    def _spy(c):
        seen_curves.append(id(c))
        return real_cpc(c)

    with patch.object(auto_operator, "_today_group_cpc", side_effect=_spy):
        _judge(db, "nkw-t", curve)

    assert seen_curves == [id(curve)]


# ══════════ 적대 리뷰 1R P1 회귀 고정 — 리뷰어가 재현한 6건이 다시 안 나오게 ══════════

def test_b_veto_sees_ad_grain_writes_belonging_to_the_judged_adgroup(db):
    """★★P1-1. **판정 grain ≠ 실쓰기 grain**이 이 계약의 가장 큰 구멍이었다.

    시간당 레인은 `adgroup`으로 판정하는데 카나리 그룹은 B3 라우팅으로 **소재(ad)에 쓴다**.
    판정 grain으로만 원장을 조회하면 그 유닛에서 배수가 영원히 1.0 = **B-veto가 아무것도 안
    하는 코드**가 되고, 그러면 살아 있는 거부권이 A-veto 하나뿐이라 계약 §3 「액셀만 조이는
    수정 금지」·북극성 §7을 이 커밋 스스로 위반한다. 하필 최근 30일 실집행이 전부 ad grain이다.

    적대 리뷰가 재현한 그대로: adgroup으로 물으면 1.0, ad로 물으면 1.5였다."""
    _seed_unit(db, target_id="nkw-x", bep_roas=None)
    # 이 그룹(grp-1)에 귀속된 «소재» 쓰기 — proposal.adgroup_id가 연결 고리(prod 54/54행 채워짐)
    for i, (before, after) in enumerate([(1320, 1510), (1730, 1980)]):
        p = NaverProposal(
            proposal_type="bid_up", target_type="ad", target_id="nad-osc",
            campaign_id=CAMPAIGN, adgroup_id="grp-1", rationale="-", expected_effect="-",
            status="approved", target_bid=after,
        )
        db.add(p)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-osc", campaign_id=CAMPAIGN, action="update_bid",
            dry_run=False, proposal_id=p.id,
            before_value=json.dumps({"adAttr": {"bidAmt": before}}),
            after_value=json.dumps({"adAttr": {"bidAmt": after}}),
            changed_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8 + i),
        ))
    db.commit()

    # adgroup_id를 안 주면 종전 결함 그대로 1.0 — 그래서 호출부가 반드시 넘겨야 한다
    bare, _ = auto_operator._own_bid_multiple_today(db, "adgroup", "grp-1", NOW)
    assert bare == Decimal(1)
    # 그룹에 귀속된 소재 쓰기를 보면 1.5
    with_group, reason = auto_operator._own_bid_multiple_today(
        db, "adgroup", "grp-1", NOW, adgroup_id="grp-1")
    assert with_group == Decimal("1.5")
    assert "1320→1980원" in reason


def test_b_veto_end_to_end_on_adgroup_judged_unit_with_ad_grain_writes(db):
    """★P1-1의 «호출부» 고정 — `_judge_hourly`가 adgroup을 판정할 때 소재 쓰기를 실제로 본다.
    함수를 직접 부르는 테스트만 있으면 호출부가 grain을 안 넘기는 결함이 원리적으로 안 잡힌다
    (적대 리뷰 P2-6이 지적한 그 자리)."""
    _seed_unit(db, target_id="nkw-y", bep_roas=None)
    _seed_settlement(db, keyword_id="nkw-y", clk=10, cost=7947)  # baseline_cpc 794.7
    # 판정 대상은 adgroup grp-1인데 쓰기는 소재로 남는다
    db.add(NaverAdDaily(
        ad_date=auto_operator._settlement_window(TODAY)[0], campaign_id=CAMPAIGN,
        campaign_type="SHOPPING", adgroup_id="grp-1", keyword_id=None,
        imp=1000, clk=10, cost=7947, conv_direct_amt=0, conv_indirect_amt=0,
    ))
    for i, (before, after) in enumerate([(1320, 1510), (1730, 1980)]):
        p = NaverProposal(
            proposal_type="bid_up", target_type="ad", target_id="nad-y",
            campaign_id=CAMPAIGN, adgroup_id="grp-1", rationale="-", expected_effect="-",
            status="approved", target_bid=after,
        )
        db.add(p)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id="nad-y", campaign_id=CAMPAIGN, action="update_bid",
            dry_run=False, proposal_id=p.id,
            before_value=json.dumps({"adAttr": {"bidAmt": before}}),
            after_value=json.dumps({"adAttr": {"bidAmt": after}}),
            changed_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8 + i),
        ))
    db.commit()
    curve = [_hour(h, imp=20, clk=2, cost=3367) for h in (9, 10, 11)]  # CPC 1683.5

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK):
        verdict = auto_operator._judge_hourly(
            db, target_type="adgroup", target_id="grp-1", campaign_id=CAMPAIGN,
            curve=curve, now=NOW,
        )
    assert verdict["direction"] != "down"
    assert "자기유발분" in (verdict.get("cpc_spike_self_caused") or "")


def test_self_caused_note_absent_when_leash_actually_lowers_the_bid(db):
    """★P1-3. 「내리려던 걸 멈췄다」와 「실제로 내렸다」가 **같은 verdict에 같이 실리면** 안 된다.
    초판은 `base`에 실어서, 고삐 DOWN이 나가는 판정에도 「CPC급등 보류」가 따라붙었고
    `run_hourly_lane`이 그걸 보고 **하향을 집행하면서 동시에 「하향 보류」 일기**를 남겼다."""
    _seed_unit(db, target_id="nkw-z", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-z", clk=10, cost=7947)   # 하루평균 1135 · CPC 794.7
    _seed_own_bid_writes(db, target_id="nkw-z", steps=[(1320, 1510), (1730, 1980)])  # ×1.5
    curve = [_hour(h, imp=20, clk=2, cost=3600) for h in (9, 10, 11)]
    # 당일 CPC 1800 → 옛 문턱 1589.4 초과(B-veto가 누름) · 새 문턱 2384.1 미만
    # 전환 0 → 추정ROAS 0 < BEP 2.0 · 당일소진 10800 ≥ 하루평균 1135 → 고삐 DOWN

    verdict = _judge(db, "nkw-z", curve)
    assert verdict["direction"] == "down"
    assert verdict.get("leash") is True
    assert verdict.get("cpc_spike_self_caused") is None, "하향이 나가는 판정에 「하향 보류」가 실렸다"


def test_a_veto_scoped_to_settlement_ok_only(db):
    """★P1-4. 계약 §4-A는 A-veto를 *"정착창이 ok여도"*로 정의했다. 초판 코드엔 그 조건이 없어
    **정착창이 미달·판정불가라 애초에 UP 의도가 없던 유닛까지** 「UP 보류」 일기를 만들었고,
    사유문도 문자 그대로 거짓이었다(*"정착창(미달)인데 … 정착창은 오늘을 못 본다"*)."""
    _seed_unit(db, target_id="nkw-below", bep_roas=Decimal("2.0"))
    # 정착창 ROAS 0.5 < 목표 2.0 = below
    _seed_settlement(db, keyword_id="nkw-below", clk=20, cost=21000, conv_amt=10500)
    curve = [
        _hour(9, imp=20, clk=1, cost=800),
        _hour(10, imp=20, clk=1, cost=800, conv_cnt=1),
        _hour(11, imp=20, clk=1, cost=800, conv_cnt=1),
    ]  # 오늘도 BEP 하회지만 정착창이 below라 A-veto 범위 밖

    verdict = _judge(db, "nkw-below", curve)
    assert verdict.get("veto") is None
    assert "재시작 대기" in verdict["reason"]   # 종전 사유 체계 그대로


def test_declaration_table_is_load_bearing_not_decorative(db):
    """★P1-6. 초판은 표를 선언만 하고 코드는 `_settlement_window`를 직접 불렀다 — 표의 값을
    **거짓으로 바꿔도 전건 초록**이었다. 계약 §2-3이 「선언만 있는 표는 조용히 거짓이 된다」며
    세운 방어가 그 자신에게 없었다. 이제 CPC급등·고삐의 창 경계는 표를 거친다."""
    # ① 표가 실제 경계를 만든다
    assert auto_operator._gate_baseline_window("cpc_spike", TODAY) == \
        auto_operator._settlement_window(TODAY)
    assert auto_operator._gate_baseline_window("intraday_up", TODAY) is None
    # ② 표를 거짓으로 고치면 «조용히» 통과하지 않고 죽는다
    with patch.dict(auto_operator._HOURLY_DECISION_WINDOWS["cpc_spike"],
                    {"baseline_window": "settlement_d30_d1"}):
        with pytest.raises(ValueError):
            auto_operator._gate_baseline_window("cpc_spike", TODAY)


def test_declared_accel_window_matches_what_the_settlement_gate_reads(db):
    """★P1-6(액셀 쪽). `roas_up`은 창 경계를 인자로 받지 않으므로(함수가 스스로 계산한다)
    **관측된 소비와 선언을 대조**해 표의 거짓을 잡는다 — 표에 「당일창을 본다」고 적어 두면
    실제 소비(정착창)와 어긋나 이 테스트가 죽는다."""
    _seed_unit(db, target_id="nkw-decl", bep_roas=None)
    _seed_settlement(db, keyword_id="nkw-decl", clk=10, cost=1000)
    curve = [_hour(h, imp=20, clk=2, cost=100) for h in (9, 10, 11)]

    real_agg = auto_operator._settlement_agg
    seen: list[tuple] = []

    def _spy(db_, tt, tid, date_from, date_to):
        seen.append((date_from, date_to))
        return real_agg(db_, tt, tid, date_from, date_to)

    with patch.object(auto_operator, "_settlement_agg", side_effect=_spy):
        _judge(db, "nkw-decl", curve)

    declared = auto_operator._HOURLY_DECISION_WINDOWS["roas_up"]["baseline_window"]
    assert declared == auto_operator._WINDOW_SETTLEMENT, "선언과 실제 소비가 갈라졌다"
    assert set(seen) == {auto_operator._settlement_window(TODAY)}


def test_counter_up_types_match_the_apps_single_source():
    """★P1-5. 계수기가 `bid_up_servo`/`bid_up_rank`를 DOWN으로 세면 §7 판정이 뒤집힌다
    (`"bid_up_servo".endswith("bid_up")`은 False다). 두 집합이 갈라지면 여기서 죽는다."""
    import importlib.util
    from app.services.naver_ad.bid_step_types import BID_UP_TYPES as APP_TYPES

    path = Path(__file__).resolve().parents[2] / "scripts" / "measurements" / "oscillation_symmetry_count.py"
    spec = importlib.util.spec_from_file_location("osc_counter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.BID_UP_TYPES) == set(APP_TYPES)
    assert mod.CPC_SPIKE_RATIO == auto_operator.CPC_SPIKE_RATIO


# ══════ ★P1-2 표면 절단 방어 — 두 거부권이 «운영 일기»에 실제로 남는지 (레인 통과) ══════
# 적대 리뷰의 생존 변이 M13: `run_hourly_lane`의 `_record_blocked` 두 호출을 통째로 지워도
# 235개 전건 초록이었다. 계약 §4-D가 지목한 이번 슬라이스의 **유일한 사람 표면**이 무보호였다.
# 아래 둘은 레인을 실제로 통과시켜 일기 행을 확인한다 — 배선을 지우면 죽는다.

def _blocked_rows(db, action):
    return (
        db.query(OpsDiaryEntry)
        .filter(OpsDiaryEntry.event_type == "blocked", OpsDiaryEntry.action == action)
        .all()
    )


def test_lane_writes_diary_row_when_a_veto_holds_an_up(db):
    """★A-veto가 상향을 멈추면 운영 일기(blocked, action='bid_up')에 사유가 원문으로 남는다.
    이 행이 §4-C ⓙ가 「A-veto의 유일한 정확한 계수」라고 선언한 그 행이다 — 배선이 없으면
    배포 후 판정 자체가 불가능하다."""
    _seed_unit(db, target_id="nkw-lane-a", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-lane-a", clk=20, cost=21000, conv_amt=63000)
    curve = [
        _hour(9, imp=20, clk=1, cost=800),
        _hour(10, imp=20, clk=1, cost=800, conv_cnt=1),
        _hour(11, imp=20, clk=1, cost=800, conv_cnt=1),
    ]
    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    mock_exec.assert_not_called()          # 상향이 나가지 않았다
    rows = _blocked_rows(db, "bid_up")
    assert len(rows) == 1, "A-veto 일기 배선이 없다(표면 절단)"
    assert "오늘 증거 거부권" in rows[0].rationale
    assert rows[0].target_id == "nkw-lane-a"


def test_lane_writes_diary_row_when_b_veto_suppresses_a_down(db):
    """★B-veto가 CPC급등 하향을 멈추면 운영 일기(blocked, action='bid_down')에 남는다.
    ★그리고 그 행은 «하향이 실제로 안 나간» 판정에서만 나와야 한다(P1-3) — 여기선 고삐도
    안 걸리므로 하향 0건이다."""
    _seed_unit(db, target_id="nkw-lane-b", bep_roas=None)   # 원가 없음 → 고삐 판정 불가
    _seed_settlement(db, keyword_id="nkw-lane-b", clk=10, cost=7947)
    _seed_own_bid_writes(db, target_id="nkw-lane-b", steps=[(1320, 1510), (1730, 1980)])
    curve = [_hour(h, imp=20, clk=2, cost=3367) for h in (9, 10, 11)]  # CPC 1683.5

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    mock_exec.assert_not_called()          # 하향이 나가지 않았다
    rows = _blocked_rows(db, "bid_down")
    assert len(rows) == 1, "B-veto 일기 배선이 없다(표면 절단)"
    assert "자기유발분" in rows[0].rationale


def test_lane_does_not_write_a_false_down_hold_when_the_leash_actually_lowers(db):
    """★P1-3의 레인 판본 — 하향이 **집행되는** 회차엔 「하향 보류」 일기가 없어야 한다.
    초판은 같은 분에 「내리려던 걸 멈췄다」 일기 1행 + 진짜 bid_down 제안을 같이 냈다."""
    _seed_unit(db, target_id="nkw-lane-c", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-lane-c", clk=10, cost=7947)   # 하루평균 1135
    _seed_own_bid_writes(db, target_id="nkw-lane-c", steps=[(1320, 1510), (1730, 1980)])
    curve = [_hour(h, imp=20, clk=2, cost=3600) for h in (9, 10, 11)]  # CPC 1800 · 전환 0

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    mock_exec.assert_called_once()                       # 고삐 하향이 실제로 나갔다
    saved = db.get(NaverProposal, mock_exec.call_args[0][1])
    assert saved.proposal_type == "bid_down"
    assert _blocked_rows(db, "bid_down") == [], "하향을 집행하면서 「하향 보류」 일기를 남겼다"


def test_multiple_across_several_ads_takes_the_conservative_minimum(db):
    """★자기 변이 M20이 살아남아 추가한 고정 — `min`을 `max`로 바꿔도 아무도 안 죽었다.

    그룹 CPC는 소재들의 혼합이라 한 소재만 1.5배 올렸다고 그룹 CPC가 1.5배 오르지 않는다.
    최댓값을 쓰면 «자기 몫»을 과대 계상해 브레이크를 필요 이상으로 끈다 — 이 거부권이 지켜야
    할 경계(「완화만 하고 강화하지 않는다」)의 반대편이다. 그래서 최솟값이 정답이고, 그 선택은
    취향이 아니라 계약 §2-6이라 테스트로 못박는다."""
    _seed_unit(db, target_id="nkw-multi", bep_roas=None)
    for idx, (ad_id, before, after) in enumerate(
        [("nad-hot", 1320, 1980), ("nad-flat", 1000, 1000)]
    ):
        p = NaverProposal(
            proposal_type="bid_up", target_type="ad", target_id=ad_id, campaign_id=CAMPAIGN,
            adgroup_id="grp-1", rationale="-", expected_effect="-", status="approved",
            target_bid=after,
        )
        db.add(p)
        db.flush()
        db.add(NaverChangeLog(
            entity_type="ad", entity_id=ad_id, campaign_id=CAMPAIGN, action="update_bid",
            dry_run=False, proposal_id=p.id,
            before_value=json.dumps({"adAttr": {"bidAmt": before}}),
            after_value=json.dumps({"adAttr": {"bidAmt": after}}),
            changed_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8 + idx),
        ))
    db.commit()

    multiple, reason = auto_operator._own_bid_multiple_today(
        db, "adgroup", "grp-1", NOW, adgroup_id="grp-1")
    assert multiple == Decimal(1), "최댓값을 쓰면 자기 몫을 과대 계상해 브레이크를 과하게 끈다"
    assert "순상향 없음" in reason


def test_a_veto_yields_to_the_budget_gate_so_its_count_means_prevented_ups(db):
    """★적대 리뷰 P2-5 채택분 고정 — A-veto는 **예산 여력 게이트 뒤**에 선다.

    앞에 두면 「예산이 없어 어차피 안 올라갔을 유닛」까지 가로채, §4-C ⓙ가 「A-veto의 유일한
    정확한 계수」라고 선언한 일기 수가 «막은 상향»이 아니라 «지나간 유닛»을 세게 된다.
    (그리고 종전 사유 `예산 여력 없음`의 시계열이 끊긴다.)"""
    _seed_unit(db, target_id="nkw-budget", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-budget", clk=20, cost=21000, conv_amt=63000)
    # 일예산을 이미 소진 — UP은 예산 게이트에서 멈춰야 한다
    snap = db.query(NaverHourlySnapshot).filter(
        NaverHourlySnapshot.campaign_id == CAMPAIGN).one()
    snap.cost = 100000
    db.commit()
    curve = [
        _hour(9, imp=20, clk=1, cost=800),
        _hour(10, imp=20, clk=1, cost=800, conv_cnt=1),
        _hour(11, imp=20, clk=1, cost=800, conv_cnt=1),
    ]  # A-veto 조건도 동시에 충족(오늘 BEP 하회 · 전환 2)

    verdict = _judge(db, "nkw-budget", curve)
    assert verdict["direction"] == "hold"
    assert verdict.get("veto") is None, "예산 게이트가 먼저 잡아야 ⓙ의 계수가 「막은 상향」이 된다"
    assert "예산 여력 없음" in verdict["reason"]


# ══════════════════ D-NAO-290 — 거부권이 CD2 클릭탐침에 되돌려지지 않는다 ══════════════════
#
# 왜 생겼나 (2026-09-05 17:4x, 레인 실행으로 재현 — 교훈 #395):
# `run_hourly_lane`은 **모든** hold를 CD2 클릭탐침 후보로 다시 집어 올려 up으로 치환한다.
# `_probe_trigger`는 순수 SA라 거부권을 모른다(clk·imp·rank만 본다). 그래서 D-NAO-288 배포분에서
#   ① A-veto가 「UP 보류」 일기를 쓴 **같은 회차에** 탐침이 bid_up 1000→1150을 집행했고,
#   ② B-veto가 CPC급등 DOWN을 억제한 자리에 탐침이 **새 UP**을 만들었다(배포 전이면 DOWN이었다).
# 아래 셋은 그 셋을 한 벌로 고정한다 — 두 거부권은 탐침을 이기고, **평범한 hold의 탐침은 그대로 산다.**

_PROBE_BLIND_CURVE_TAIL = [           # 직전 완료 2시간 [10,12): clk=0 · imp≥30 · rank 3.0 ≥ 2.5
    _hour(10, imp=40, clk=0, cost=0, avg_rank=3.0),
    _hour(11, imp=40, clk=0, cost=0, avg_rank=3.0),
]


def _lane_proposals(db):
    return db.query(NaverProposal).all()


def test_a_veto_hold_is_not_reopened_by_the_click_probe(db):
    """★A-veto가 멈춘 상향을 탐침이 되살리지 않는다.

    수리 전 실측: 일기 「UP 보류(오늘 증거 거부권)」 1행 + `bid_up 1000→1150 approved` 동시 발생.
    일기가 «막았다»고 말하는데 상향이 나갔다 — 계약 §4-C ⓖ가 ⓙ를 「유일한 정확한 계수」로
    지정한 그 행이 거짓이 되는 자리다."""
    _seed_unit(db, target_id="nkw-veto-probe", bep_roas=Decimal("2.0"))
    _seed_settlement(db, keyword_id="nkw-veto-probe", clk=20, cost=21000, conv_amt=63000)
    curve = [_hour(8, imp=40, clk=4, cost=1600, conv_cnt=2)] + _PROBE_BLIND_CURVE_TAIL

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert _lane_proposals(db) == [], "거부권이 만든 hold에서 탐침 제안이 나왔다"
    mock_exec.assert_not_called()
    rows = _blocked_rows(db, "bid_up")
    assert len(rows) == 1 and "오늘 증거 거부권" in rows[0].rationale
    # 매시 로그 한 줄에서 보여야 한다 — `other`에 섞이면 사람은 발동을 못 본다.
    assert result["held_by_reason"].get("veto_accel") == 1, result["held_by_reason"]


def test_b_veto_suppression_does_not_create_a_new_up_via_probe(db):
    """★B-veto가 억제한 DOWN 자리에 **새 UP**이 생기지 않는다.

    계약 §4-A S2 원문: *"둘 다 hold만 만들고 **새 액션을 만들지 않는다**"*.
    수리 전에는 그 자리에 `bid_up 1000→1150`이 났다 — 브레이크 자리에 액셀이 들어서는 것이라
    북극성 §7 대칭이 액셀 쪽으로 기운다."""
    _seed_unit(db, target_id="nkw-bveto-probe", bep_roas=None)   # 원가 없음 → 고삐 판정 불가
    _seed_settlement(db, keyword_id="nkw-bveto-probe", clk=10, cost=7947)
    _seed_own_bid_writes(db, target_id="nkw-bveto-probe", steps=[(1320, 1510), (1730, 1980)])
    curve = [_hour(8, imp=40, clk=2, cost=3367)] + _PROBE_BLIND_CURVE_TAIL   # 당일 CPC 1683.5

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute") as mock_exec:
        result = auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    assert [p.proposal_type for p in _lane_proposals(db)] == [], "억제한 하향 자리에 새 상향이 생겼다"
    mock_exec.assert_not_called()
    assert any("자기유발분" in r.rationale for r in _blocked_rows(db, "bid_down"))
    # ★브레이크 거부권은 자기 사유가 verdict["reason"]에 안 실린다 — 레인이 앞에 붙여야
    #   로그에서 보인다. 그 배선을 여기서 고정한다(안 그러면 `other`로 흡수된다).
    assert result["held_by_reason"].get("veto_brake") == 1, result["held_by_reason"]


def test_ordinary_hold_still_gets_the_click_probe(db):
    """★회귀 0 증명 — 거부권이 **아닌** hold에서는 CD2 탐침이 종전 그대로 상향을 낸다.

    이게 없으면 위 두 고정은 「탐침을 통째로 껐다」와 구별되지 않는다(D-NAO-58 기능 삭제)."""
    _seed_unit(db, target_id="nkw-plain-hold", bep_roas=None)    # 원가 없음 → 고삐·A-veto 불가
    # 정착창 실적은 있되 ROAS 미달 → UP 게이트가 hold(거부권 아님).
    # ★실적 자체는 있어야 한다 — 없으면 유닛이 핫셋 후보에 안 들어가 탐침까지 가지도 못하고,
    #   그러면 이 테스트는 「탐침이 살아 있다」가 아니라 「후보가 없다」를 고정하게 된다.
    _seed_settlement(db, keyword_id="nkw-plain-hold", clk=20, cost=21000, conv_amt=0)
    curve = [_hour(8, imp=40, clk=1, cost=400)] + _PROBE_BLIND_CURVE_TAIL

    with patch.object(auto_operator.diagnosis, "correction_factor", return_value=_FACTOR_OK), \
         patch.object(auto_operator.naver_sa_writer, "get_keyword", return_value={"bidAmt": 1000}), \
         patch.object(auto_operator.naver_execution_harness, "execute"):
        auto_operator.run_hourly_lane(db, now=NOW, fetch_intraday=lambda tid, d: curve)

    kinds = [p.proposal_type for p in _lane_proposals(db)]
    assert kinds == ["bid_up"], f"평범한 hold의 탐침이 죽었다(D-NAO-58 회귀): {kinds}"
    assert "클릭탐침" in _lane_proposals(db)[0].rationale


def test_hold_reason_classification_names_the_two_vetoes(db):
    """★거부권 hold가 로그 집계에서 `other`에 섞이지 않는다(D-NAO-290 규칙 신설).

    ⓙ는 운영 일기를 정확한 계수로 쓰지만, **매시 도는 사람 표면은 이 로그 한 줄**이다.
    규칙이 없으면 거부권 1건은 `other`(최근 3회차 8·14·13건) 안에서 안 보인다."""
    a_veto = ("UP 보류(오늘 증거 거부권, D-NAO-288 A-veto) — 정착창(정착창 보정ROAS 3.0000 "
              ">= 목표 2.0)인데 오늘 실측이 BEP 하회(전환 2건, …)")
    b_veto = ("CPC급등 보류(자기유발분) — 당일 1683.5원 ≤ 정착창 794.7원×2×자기상향 1.50배 "
              "· 이후 UP 보류(예산 여력 없음/미확보) — …")
    assert auto_operator.classify_hold_reason(a_veto) == "veto_accel"
    assert auto_operator.classify_hold_reason(b_veto) == "veto_brake"
    # 좁은 규칙이 넓은 규칙보다 위에 있어야 한다 — 아래로 내려가면 `roas_below`로 흡수된다.
    counts = auto_operator.summarize_held_by_reason([{"reason": a_veto}, {"reason": b_veto}])
    assert counts == {"veto_accel": 1, "veto_brake": 1}, counts
