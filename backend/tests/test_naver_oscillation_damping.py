# test_naver_oscillation_damping.py — D-NAO-287 「진동차단 목표」 회귀 고정
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
