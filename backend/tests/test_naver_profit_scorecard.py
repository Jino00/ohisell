# test_naver_profit_scorecard.py — P7 일일 이익 스코어카드 단위 테스트 (D-NAO-85/ref39 P7,
#   docs/PLAN_naver-ad-ex-expansion.md §4-1). 커버: ①보정계수 적용 이익 계산 정확성
#   ②sentinel(__backfill__) 행 제외 ③BEP 해석 불가 캠페인 처리(숫자 조작 없음) ④6월 baseline
#   대비 증감% ⑤Slack/diary 호출 여부(mock) ⑥무보정(source≠actual_revenue_ratio) 정직 표기.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.services.naver_ad.diagnosis import _as_interval

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverEntity,
    NaverProductBep,
    OpsDiaryEntry,
)
from app.services.naver_ad import profit_scorecard, slack_notifier
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

YDAY = date(2026, 7, 23)  # "어제" — run_profit_scorecard(now=NOW)의 D-1
NOW = datetime(2026, 7, 24, 8, 40, 0)


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


def _fixed_factor(factor: str, source: str = "actual_revenue_ratio"):
    """diagnosis.correction_factor를 결정적 값으로 고정(diary_reflection 테스트 관례 미러).

    ★D-NAO-230: 구간 키(factor_low/high/point)를 **prod와 같은 `_as_interval`로** 만든다 —
    손으로 dict를 짜면 픽스처가 prod 계약과 갈라져 「테스트는 통과하는데 라이브는 깨지는」
    자리가 생긴다(교훈: 테스트 픽스처는 prod 세션과 같아야 한다).
    """
    return lambda db, d: _as_interval(Decimal(factor), source=source)


def _ad_row(db, ad_date, campaign_id, adgroup_id, *, cost=0, direct=0, indirect=0,
            keyword_id="", campaign_type="SHOPPING"):
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id=keyword_id,
        imp=0, clk=0, cost=cost, rank_sum=0,
        conv_direct_cnt=1 if direct else 0, conv_indirect_cnt=1 if indirect else 0,
        conv_direct_amt=direct, conv_indirect_amt=indirect,
    ))


def _settings(db, campaign_id, *, auto_operate=False, optimizer="none"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))


def _campaign_name(db, campaign_id, name):
    db.add(NaverEntity(entity_type="campaign", entity_id=campaign_id, campaign_id=campaign_id,
                        campaign_type="SHOPPING", name=name, status="on"))


def _product_bep(db, cpid, bep_roas):
    db.add(NaverProductBep(
        channel_id=6, channel_product_id=cpid, product_name="테스트상품",
        selling_price=Decimal("10000"), cost_price=Decimal("5000"),
        commission_rate=Decimal("0.05"), logistics_cost=Decimal("1000"),
        contribution_margin=Decimal("3000"), bep_roas=Decimal(str(bep_roas)),
        aggressiveness="standard", target_roas=Decimal(str(bep_roas)) * Decimal("1.15"), has_cost=True,
    ))


def _adgroup_product(db, adgroup_id, campaign_id, cpid):
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, campaign_id=campaign_id, mall_product_id=cpid))


def _observes(db):
    return db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "observe").all()


def _fake_slack(monkeypatch):
    captured = {}

    def fake_notify_text(text, *, webhook_url=None, log_label="메시지"):
        captured["text"] = text
        captured["log_label"] = log_label
        return {"sent": True, "reason": None, "slack_ts": None}

    monkeypatch.setattr(slack_notifier, "notify_text", fake_notify_text)
    return captured


# ── ① 이익 계산 정확성(보정 적용) ──────────────────────────────────

def test_profit_calculation_applies_correction_factor(db, monkeypatch):
    """총이익 = (conv_amt × factor) ÷ bep_roas − cost. bep=2.0, factor=1.5,
    cost=10000, conv_amt=20000(직15000+간5000) → (20000*1.5)/2.0 - 10000 = 5000원."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=10000, direct=15000, indirect=5000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.5"))
    captured = _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)

    assert result["campaigns"] == 1
    assert result["bep_unknown"] == 0
    assert result["correction_factor_source"] == "actual_revenue_ratio"
    entry = _observes(db)[0]
    assert entry.action == profit_scorecard.ACTION_PROFIT_SCORECARD
    assert entry.actor == "system"
    assert "+5,000원" in entry.rationale  # 어제 이익
    assert "실측" in entry.rationale
    assert captured["text"] == entry.rationale


def test_profit_calculation_negative_profit_formatted_with_sign(db, monkeypatch):
    """이익이 음수(손실)여도 부호를 지어내지 않고 그대로: cost 20000, conv_amt 10000, bep=2.0
    factor=1.0 → 10000/2.0 - 20000 = -15000원."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "03.아이폰_유리")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=20000, direct=10000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    entry = _observes(db)[0]
    assert "-15,000원" in entry.rationale
    assert "-15,000원" in entry.rationale.split("합계")[1]  # 헤더 합계에도 반영
    assert result["campaigns"] == 1


# ── ② sentinel(__backfill__) 행 제외 ──────────────────────────────

def test_sentinel_rows_excluded_from_profit(db, monkeypatch):
    """campaign_backfill sentinel(adgroup_id=__backfill__) 행은 2배 집계 방지를 위해 제외돼야
    한다 — 거대한 값을 sentinel에 심어도 결과(정상 행만 반영)가 변하지 않아야 한다."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=10000, direct=20000)  # 정상 행: (20000)/2.0 - 10000 = 0
    _ad_row(db, YDAY, "cmp1", BACKFILL_SENTINEL_ADGROUP, cost=999_999, direct=999_999)  # sentinel(제외 대상)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    profit_scorecard.run_profit_scorecard(db, now=NOW)
    entry = _observes(db)[0]
    assert "+0원" in entry.rationale
    assert "999,999" not in entry.rationale


# ── ③ BEP 미확인 캠페인 처리 ──────────────────────────────────────

def test_bep_unknown_campaign_shows_no_fabricated_numbers(db, monkeypatch):
    """상품 매핑도 계정 기본 BEP(has_cost=True 상품)도 없으면 숫자를 지어내지 않고
    'BEP 미확인' 행으로 표기한다."""
    _settings(db, "cmp2", auto_operate=True)
    _campaign_name(db, "cmp2", "P_Test.아이패드")
    _ad_row(db, YDAY, "cmp2", "grp2", cost=5000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["campaigns"] == 1
    assert result["bep_unknown"] == 1
    entry = _observes(db)[0]
    assert "BEP 미확인" in entry.rationale
    assert "이익 산출 불가" in entry.rationale


# ── ④ 6월 baseline 비교 ───────────────────────────────────────────

def test_june_baseline_pct_change(db, monkeypatch):
    """6월(06-01~06-30) 매일 profit=1000원 → 30일 평균 1000원. 최근 7일(D-7~D-1)은 매일
    profit=1500원 → 7일 평균 1500원. 증감% = (1500-1000)/1000*100 = +50.0%."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")

    # 6월 30일 매일: cost=1000, direct=4000 → 4000/2.0-1000=1000원/일
    d = date(2026, 6, 1)
    while d <= date(2026, 6, 30):
        _ad_row(db, d, "cmp1", "grp1", cost=1000, direct=4000)
        d = date.fromordinal(d.toordinal() + 1)

    # 최근 7일(D-7~D-1 = 07-17~07-23) 매일: cost=1000, direct=5000 → 5000/2.0-1000=1500원/일
    d = date(2026, 7, 17)
    while d <= YDAY:
        _ad_row(db, d, "cmp1", "grp1", cost=1000, direct=5000)
        d = date.fromordinal(d.toordinal() + 1)

    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    profit_scorecard.run_profit_scorecard(db, now=NOW)
    entry = _observes(db)[0]
    assert "7일평균 +1,500원" in entry.rationale
    assert "6월평균 +1,000원" in entry.rationale
    assert "+50.0%" in entry.rationale


# ── ⑤ Slack/diary 호출 여부(mock) ─────────────────────────────────

def test_calls_diary_and_slack_with_matching_text(db, monkeypatch):
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    captured = _fake_slack(monkeypatch)

    profit_scorecard.run_profit_scorecard(db, now=NOW)

    assert captured["log_label"] == "일일 이익 스코어카드"
    entries = _observes(db)
    assert len(entries) == 1
    assert entries[0].campaign_id == ""  # 계정 전체 브리핑(bm_briefing 관례와 동형)
    assert captured["text"] == entries[0].rationale


def test_no_target_campaigns_still_writes_diary_and_slack(db, monkeypatch):
    """대상 캠페인(auto_operate ∪ optimizer=ours)이 0개여도 브리핑 자체는 발생(관찰 침묵 방지)."""
    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    captured = _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)

    assert result["campaigns"] == 0
    entry = _observes(db)[0]
    assert "대상 캠페인 없음" in entry.rationale
    assert captured["text"] == entry.rationale


# ── ⑥ 무보정(source≠actual_revenue_ratio) 정직 표기 ────────────────

def test_uncorrected_factor_labeled_honestly(db, monkeypatch):
    """실주문 매출 부재(source='unavailable') → factor=1.0 그대로 쓰되 '무보정'으로 표기한다
    (스코어카드는 관찰이라 fail-open이지만 계산의 신뢰도는 숨기지 않는다)."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0", source="unavailable"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["correction_factor_source"] == "unavailable"
    entry = _observes(db)[0]
    assert "무보정" in entry.rationale
    assert "실측" not in entry.rationale


# ── ours-only(optimizer='ours', auto_operate=False) 캠페인도 대상 ──

def test_ours_optimizer_campaign_included_even_without_auto_operate(db, monkeypatch):
    """대상 = auto_operate=True ∪ optimizer='ours' — 04 자동운영은 꺼져 있어도(optimizer만
    'ours') 스코어카드 대상에는 포함돼야 한다(§4-1 표면 대상 정의)."""
    _settings(db, "cmp3", auto_operate=False, optimizer="ours")
    _campaign_name(db, "cmp3", "17프로.케이스")
    _product_bep(db, "cp-3", "2.0")
    _adgroup_product(db, "grp3", "cmp3", "cp-3")
    _ad_row(db, YDAY, "cmp3", "grp3", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["campaigns"] == 1
    entry = _observes(db)[0]
    assert "17프로.케이스" in entry.rationale


def test_spending_agency_campaign_is_now_included(db, monkeypatch):
    """★계약 변경(2026-07-30 사고 수정): 구 계약은 `auto_operate ∪ optimizer='ours'`가
    아니면 제외였다. 이제 **돈을 쓰는 캠페인은 스위치와 무관하게 관측한다** — 이 스코어카드는
    목적함수(D-NAO-59 총이익 절대액)를 표면화하는 관찰 전용 장치이고, 대행사가 태우는 광고비도
    우리 총이익에 그대로 들어가기 때문이다(D-NAO-13: 진단·리포트는 상태 무관 전 캠페인)."""
    _settings(db, "cmp-agency", auto_operate=False, optimizer="mop")
    _campaign_name(db, "cmp-agency", "대행사 캠페인")
    _ad_row(db, YDAY, "cmp-agency", "grpX", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["campaigns"] == 1
    assert "대행사 캠페인" in _observes(db)[0].rationale


def test_all_optimizer_none_still_produces_scorecard(db, monkeypatch):
    """★2026-07-30 사고 직접 회귀: 전 캠페인 optimizer='none' · auto_operate=0(긴급정지)
    상태에서도 스코어카드가 침묵하지 않는다. 구 코드로는 campaigns==0이었다."""
    _settings(db, "cmp-stopped", auto_operate=False, optimizer="none")
    _campaign_name(db, "cmp-stopped", "정지된 캠페인")
    _ad_row(db, YDAY, "cmp-stopped", "grpS", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["campaigns"] == 1
    assert "정지된 캠페인" in _observes(db)[0].rationale


def test_stale_campaign_outside_scope_excluded(db, monkeypatch):
    """스코프는 계정 전체가 아니다 — settings 행도 없고 최근 7일 광고비도 없으면 제외."""
    _campaign_name(db, "cmp-stale", "옛날 캠페인")
    _ad_row(db, YDAY - timedelta(days=60), "cmp-stale", "grpZ", cost=1000, direct=3000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.0"))
    _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert result["campaigns"] == 0


# ── ★D-NAO-230 §5-7 — 총이익 구간 양끝 병기 ────────────────────────
#
# 계정 30일 총이익은 이 계수 하나에 «부호»가 달려 있다(계약 §1-3 실측: 보정 +5,963,568원 ↔
# 무보정 −234,545원). 한쪽 끝만 적으면 읽는 사람이 그 사실을 알 길이 없고, M4 카나리·M5
# 지혜 기여 판정이 부푼 자 위에서 «연달아 거짓 합격»한다(북극성 §6 M4·M5 각주).

def test_profit_is_reported_at_both_ends_of_the_interval(db, monkeypatch):
    """상한기준·하한기준 총이익이 **둘 다** 사람이 읽는 문구에 나온다.

    bep=2.0 · cost=10000 · conv_amt=20000 →
      상한(1.5): (20000×1.5)/2.0 − 10000 = +5,000원
      하한(1.0): (20000×1.0)/2.0 − 10000 =      0원
    """
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=10000, direct=15000, indirect=5000)
    db.commit()

    monkeypatch.setattr(profit_scorecard, "correction_factor", _fixed_factor("1.5"))
    captured = _fake_slack(monkeypatch)

    result = profit_scorecard.run_profit_scorecard(db, now=NOW)

    # ① 응답이 양끝을 들고 나간다 — Slack 문구만 고치면 프로그램 경로는 여전히 점추정만 본다.
    assert result["correction_factor_low"] == 1.0
    assert result["correction_factor_high"] == 1.5
    assert result["total_profit_high"] == 5000
    assert result["total_profit_low"] == 0
    # ★적대 리뷰 2R P2-c: 소비처가 아직 없는 키도 «존재»를 단언한다 — 안 그러면
    #   조용히 사라져도 아무도 모른다(「표방↔실배선 괴리」의 씨앗).
    assert result["total_profit_avg7_high"] == 5000
    assert result["total_profit_avg7_low"] == 0
    assert "30일" in result["profit_window_note"], "없는 창을 채우지 않았다는 사실이 응답에 남아야 한다"

    # ② ★표면 — Slack·일기 본문에 두 값이 **글자로** 나온다(계약 §3-6).
    text = captured["text"]
    assert "상한기준 +5,000원" in text
    assert "하한기준 +0원" in text
    assert "[1.0000~1.5000]" in text, "계수도 구간으로 표기해야 한다(카드와 같은 자릿수)"
    entry = _observes(db)[0]
    assert "하한기준" in entry.rationale, "일기에도 같은 문구가 남아야 사후 감사가 된다"


def test_interval_note_carries_its_window(db, monkeypatch):
    """계수는 창과 함께 나간다 — 롤링 창이라 어제 값이 오늘 값이 아니다(계약 §3-6·§3-7)."""
    _settings(db, "cmp1", auto_operate=True)
    _campaign_name(db, "cmp1", "04.아이폰_필름")
    _product_bep(db, "cp-1", "2.0")
    _adgroup_product(db, "grp1", "cmp1", "cp-1")
    _ad_row(db, YDAY, "cmp1", "grp1", cost=10000, direct=15000, indirect=5000)
    db.commit()

    def _with_window(dbx, d):
        return _as_interval(
            Decimal("1.5"), source="actual_revenue_ratio",
            window_from="2026-07-25", window_to="2026-08-23",
        )

    monkeypatch.setattr(profit_scorecard, "correction_factor", _with_window)
    captured = _fake_slack(monkeypatch)
    profit_scorecard.run_profit_scorecard(db, now=NOW)
    assert "창 2026-07-25~2026-08-23" in captured["text"]
