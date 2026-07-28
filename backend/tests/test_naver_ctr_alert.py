# test_naver_ctr_alert.py — VT3 ctr_alert SA 단위테스트 (D-NAO-82② §4.1 + D-NAO-103 개편).
# 커버: W1(최근1일)·W3(rolling 3일) 창별 독립 발화·기대클릭 −80%↓ 분기·트레일링 CTR 폴백
#   사다리(그룹→캠페인→None)·기대클릭<5 −80%분기 비활성·기대클릭<2 클릭0분기 비활성(D-NAO-103)·
#   imp<200/rank>4.0 미발화·as_of=D-1 경계(오늘 데이터 미사용)·auto_operate 스코프.
# ★D-NAO-103 이후 판정 전제가 바뀌었다: 트레일링 CTR 표본이 없으면 "이례적인지"를 말할 수
#   없으므로 **아무 경보도 발화하지 않는다**(구 동작은 클릭0만으로 발화). 그래서 대부분의
#   케이스는 트레일링 창(~D0-3]에 기준 CTR 시드를 깔아야 한다(_trailing 헬퍼).
# 실 API 0 — 순수 read-only SA라 mock 불필요(naver_ad_daily/campaign_settings 시드만).
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverCampaignSettings
from app.services.naver_ad import ctr_alert

CAMPAIGN = "cmp-04"
GROUP = "grp-04"
NOW = datetime(2026, 7, 22, 8, 50, 0)  # as_of D0 = 2026-07-21(today-1)
D0 = date(2026, 7, 21)


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


def _settings(db, *, campaign_id=CAMPAIGN, auto_operate=True):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, auto_operate=auto_operate, optimizer="ours"))
    db.commit()


def _daily(db, *, ad_date, imp, clk, rank, campaign_id=CAMPAIGN, adgroup_id=GROUP,
           campaign_type="SHOPPING"):
    """naver_ad_daily 1행 — rank_sum=round(rank*imp)(avg_rank=rank_sum/imp 역산)."""
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type=campaign_type,
        adgroup_id=adgroup_id, keyword_id="", imp=imp, clk=clk, cost=1000,
        rank_sum=round(rank * imp),
    ))
    db.commit()


def _trailing(db, *, imp=1000, clk=20, adgroup_id=GROUP, campaign_id=CAMPAIGN, days_ago=10):
    """트레일링 창(~D0-3]에 기준 CTR 시드 1행 — D-NAO-103 기대클릭 게이트의 분모.
    기본 imp 1000(그룹 표본 하한 충족)·clk 20 = CTR 2%."""
    _daily(db, ad_date=D0 - timedelta(days=days_ago), imp=imp, clk=clk, rank=3.0,
           adgroup_id=adgroup_id, campaign_id=campaign_id)


def _one(alerts: list[dict], window: str) -> dict | None:
    matches = [a for a in alerts if a["window"] == window]
    return matches[0] if matches else None


# ══════════════════════════ W1/W3 창별 독립 발화 ══════════════════════════

def test_w1_fires_w3_not_fired_by_existing_clicks(db):
    """04 실표본 미러: W1(단일일 850노출·클릭0·rank 3.8) 발화 ∧ 같은 그룹 W3에 클릭 2 있으면
    W3 비발화. 트레일링 CTR 0.5%(imp 2000·clk 10) — W1 기대클릭 4.25(≥2 발화) / W3 기대클릭
    5.25인데 실클릭 2 > 5.25×0.2=1.05라 −80%↓ 분기도 미충족."""
    _settings(db)
    _trailing(db, imp=1000, clk=5, days_ago=10)
    _trailing(db, imp=1000, clk=5, days_ago=20)
    _daily(db, ad_date=D0 - timedelta(days=2), imp=100, clk=1, rank=3.5)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=100, clk=1, rank=3.5)
    _daily(db, ad_date=D0, imp=850, clk=0, rank=3.8)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)

    w1 = _one(res["alerts"], "W1")
    assert w1 is not None
    assert w1["imp"] == 850 and w1["clk"] == 0 and w1["avg_rank"] == 3.8
    assert w1["expected_clk"] == 4.2  # 0.5% × 850(소수 1자리 반올림)
    assert "입찰로 못 푸는" in w1["reason"]
    assert _one(res["alerts"], "W3") is None  # 클릭 2건 존재 → W3 미발화


def test_w1_not_fired_when_w3_clean(db):
    """정상 그룹(클릭 있음·밴드 밖도 아님) → W1도 발화 안 함(음성 대조)."""
    _settings(db)
    for d in (D0 - timedelta(days=2), D0 - timedelta(days=1), D0):
        _daily(db, ad_date=d, imp=100, clk=5, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


# ══════════════════════════ 기대클릭 −80% 분기 ══════════════════════════

def test_w3_fires_on_expected_click_ratio(db):
    """트레일링 CTR 5%(그룹 표본≥1,000) → W3 창(imp 1000) 기대클릭 50 ∧ 실클릭 8(≤10=20%)
    → −80%↓ 분기 발화. avg_rank 3.0(밴드 내)."""
    _settings(db)
    # 트레일링(D0-32..D0-3): imp 2000·clk 100 → ctr 5%(그룹 표본≥1,000 → 그룹 CTR 사용).
    _daily(db, ad_date=D0 - timedelta(days=10), imp=1000, clk=50, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=20), imp=1000, clk=50, rank=3.0)
    # W3(D0-2..D0): imp 1000·clk 8 → 기대클릭 50×0.2=10 ≥ clk 8.
    _daily(db, ad_date=D0 - timedelta(days=2), imp=400, clk=3, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=400, clk=3, rank=3.0)
    _daily(db, ad_date=D0, imp=200, clk=2, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)

    w3 = _one(res["alerts"], "W3")
    assert w3 is not None
    assert w3["imp"] == 1000 and w3["clk"] == 8
    assert w3["expected_clk"] == 50.0
    assert "−80%" in w3["reason"] or "-80%" in w3["reason"]


def test_w3_not_fired_when_click_above_ratio(db):
    """같은 트레일링 CTR·기대클릭 50이지만 실클릭 15(>10=20%) → −80%↓ 분기 미충족·클릭≠0 → 미발화."""
    _settings(db)
    _daily(db, ad_date=D0 - timedelta(days=10), imp=1000, clk=50, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=20), imp=1000, clk=50, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=2), imp=400, clk=6, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=400, clk=6, rank=3.0)
    _daily(db, ad_date=D0, imp=200, clk=3, rank=3.0)  # 누적 clk=15
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert _one(res["alerts"], "W3") is None


# ══════════════════════════ 트레일링 CTR 폴백 사다리 ══════════════════════════

def test_trailing_ctr_falls_back_to_campaign_when_group_sample_thin(db):
    """그룹 트레일링 표본(imp<1,000) → 캠페인 CTR 폴백. 캠페인 트레일링(다른 그룹 포함)
    imp 2000·clk 100 → ctr 5% → 기대클릭 50(W3 imp 1000) → 동일 −80%↓ 판정."""
    _settings(db)
    OTHER = "grp-other"
    # 대상 그룹 트레일링 표본 얇음(imp 200<1,000) — 그룹 CTR 신뢰 불가.
    _daily(db, ad_date=D0 - timedelta(days=10), imp=100, clk=1, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=20), imp=100, clk=1, rank=3.0)
    # 캠페인 트레일링 총합(다른 그룹) — imp 1800·clk 98(+대상 그룹 200·2 = 총 2000·100=5%).
    _daily(db, ad_date=D0 - timedelta(days=10), imp=900, clk=49, rank=3.0, adgroup_id=OTHER)
    _daily(db, ad_date=D0 - timedelta(days=20), imp=900, clk=49, rank=3.0, adgroup_id=OTHER)
    _daily(db, ad_date=D0 - timedelta(days=2), imp=400, clk=3, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=400, clk=3, rank=3.0)
    _daily(db, ad_date=D0, imp=200, clk=2, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)

    w3 = _one(res["alerts"], "W3")
    assert w3 is not None
    assert w3["expected_clk"] == 50.0  # 캠페인 CTR(5%) 폴백 적용


def test_no_trailing_data_no_alert_at_all(db):
    """D-NAO-103 전제 변경: 트레일링 데이터 전무(그룹·캠페인 둘 다)면 기대클릭을 구할 수
    없으므로 클릭이 0이어도 **아무것도 발화하지 않는다**(구 동작은 clk=0 분기가 발화했다).
    "이례적인지"를 말할 근거가 없는 상태에서 경보를 내는 것이 반복 발화의 뿌리였다."""
    _settings(db)
    _daily(db, ad_date=D0 - timedelta(days=2), imp=100, clk=0, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=100, clk=0, rank=3.0)
    _daily(db, ad_date=D0, imp=100, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


def test_expected_click_below_min_disables_ratio_branch(db):
    """기대클릭<5(소표본) → −80%↓ 분기 발화 금지(clk=0 분기만 유효). 트레일링 imp 매우 작지만
    ≥1,000 문턱을 넘기게 구성해 그룹 CTR을 그대로 쓰되, W3 창 imp를 작게 눌러 기대클릭<5로."""
    _settings(db)
    # 그룹 트레일링 imp=1000·clk=10 → ctr=1%.
    _daily(db, ad_date=D0 - timedelta(days=10), imp=500, clk=5, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=20), imp=500, clk=5, rank=3.0)
    # W3 창 imp=200(하한 걸침)·clk=1 → 기대클릭=1%×200=2(<5) → −80% 분기 비활성, clk≠0 → 미발화.
    _daily(db, ad_date=D0 - timedelta(days=2), imp=100, clk=0, rank=3.0)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=50, clk=0, rank=3.0)
    _daily(db, ad_date=D0, imp=50, clk=1, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert _one(res["alerts"], "W3") is None


# ══════════════ D-NAO-103: 기대클릭≥2 게이트(클릭0 분기) 경계 ══════════════

def test_zero_click_suppressed_when_expected_below_two(db):
    """만성 저CTR 재현 — 트레일링 CTR 0.5%(imp 2000·clk 10)·당일 노출 300 → 기대클릭 1.5(<2).
    "클릭 0"이 통계적으로 평범한 날이므로 W1·W3 둘 다 비발화(반복 발화의 실체를 차단)."""
    _settings(db)
    _trailing(db, imp=1000, clk=5, days_ago=10)
    _trailing(db, imp=1000, clk=5, days_ago=20)
    _daily(db, ad_date=D0, imp=300, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


def test_zero_click_fires_when_expected_at_least_two(db):
    """같은 조건에서 당일 노출만 400으로 올리면 기대클릭 2.0(=경계) → W1 발화.
    경계값 2.0은 발화 쪽(≥2)이다."""
    _settings(db)
    _trailing(db, imp=1000, clk=5, days_ago=10)
    _trailing(db, imp=1000, clk=5, days_ago=20)
    _daily(db, ad_date=D0, imp=400, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)

    w1 = _one(res["alerts"], "W1")
    assert w1 is not None and w1["expected_clk"] == 2.0
    assert w1["campaign_type"] == "SHOPPING"  # 브리핑 문구 분기 재료가 실려 나온다


def test_trailing_ctr_uses_full_history_not_30_days(db):
    """트레일링 창은 가용 전 기간 — 100일 전 표본도 분모에 든다(구 30일 창이었으면 그룹 표본
    미달로 캠페인 CTR 폴백돼 기대클릭이 달라졌을 자리)."""
    _settings(db)
    _trailing(db, imp=1000, clk=5, days_ago=100)   # 100일 전(구 30일 창 밖)
    _trailing(db, imp=1000, clk=5, days_ago=120)
    _daily(db, ad_date=D0, imp=400, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)

    w1 = _one(res["alerts"], "W1")
    assert w1 is not None and w1["expected_clk"] == 2.0  # 0.5% × 400 = 그룹 CTR 그대로 적용


# ══════════════════════════ 게이트: imp<200 / rank>4.0 ══════════════════════════

def test_imp_below_200_no_alert(db):
    _settings(db)
    _daily(db, ad_date=D0, imp=199, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


def test_rank_above_band_no_alert(db):
    """밴드 밖(>4.0)은 래더 영역 — CTR 경보 아님(입찰로 풀 여지가 아직 있음)."""
    _settings(db)
    _daily(db, ad_date=D0 - timedelta(days=2), imp=300, clk=0, rank=4.5)
    _daily(db, ad_date=D0 - timedelta(days=1), imp=300, clk=0, rank=4.5)
    _daily(db, ad_date=D0, imp=300, clk=0, rank=4.5)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


# ══════════════════════════ as_of=D-1 경계 ══════════════════════════

def test_today_data_not_used(db):
    """오늘(now.date())의 행은 as_of=D0(=today-1)에 포함되지 않는다 — 오늘 값을 경보 밴드
    밖(클릭 있음)으로 심어도 무관, D0(어제)만으로 판정."""
    _settings(db)
    _trailing(db)  # 트레일링 CTR 2% → 기대클릭 6(≥2)
    _daily(db, ad_date=D0, imp=300, clk=0, rank=3.0)  # D0(어제) — 경보 조건 충족
    _daily(db, ad_date=NOW.date(), imp=999, clk=999, rank=1.0)  # 오늘 — 무시돼야 함
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    w1 = _one(res["alerts"], "W1")
    assert w1 is not None and w1["imp"] == 300  # 오늘(999) 섞이지 않음


# ══════════════════════════ auto_operate 스코프 ══════════════════════════

def test_non_auto_operate_campaign_scope_empty(db):
    _settings(db, auto_operate=False)
    _daily(db, ad_date=D0, imp=300, clk=0, rank=3.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


def test_unknown_campaign_scope_empty(db):
    """NaverCampaignSettings 행 자체 없음 — auto_operate 확인 불가 → 경보 0."""
    res = ctr_alert.detect_ctr_alerts(db, "cmp-unregistered", now=NOW)
    assert res["alerts"] == []


# ══════════════════════════ P2-3(codex 1R): rank_sum≤0 오탐 차단 ══════════════════════════

def test_rank_sum_zero_no_alert(db):
    """imp>0인데 rank_sum≤0(순위 소스 누락 = 순위 미상)이면 W1·W3 둘 다 비발화 — rank 0.0을
    과열밴드(≤2.5) 최상위 순위로 오독해 발화하던 것 차단(순위 미상=경보 발화 금지)."""
    _settings(db)
    # rank=0.0 → rank_sum=round(0×imp)=0. 나머지는 경보 조건 충족(imp 300·clk 0).
    for d in (D0 - timedelta(days=2), D0 - timedelta(days=1), D0):
        _daily(db, ad_date=d, imp=300, clk=0, rank=0.0)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []


# ══════════════════════════ P2-1(codex 1R): D0 부분적재 가드 ══════════════════════════

def test_partial_load_suppresses_alerts(db):
    """D0 행수가 직전 3일 일평균의 절반 미만이면 부분적재 의심 → 캠페인 경보 전체 억제.
    직전 창 4행/일 baseline, D0 1행(25%<50%)."""
    _settings(db)
    for d in (D0 - timedelta(days=3), D0 - timedelta(days=2), D0 - timedelta(days=1)):
        for g in range(4):
            _daily(db, ad_date=d, imp=300, clk=0, rank=3.0, adgroup_id=f"grp-{g}")
    _daily(db, ad_date=D0, imp=300, clk=0, rank=3.0, adgroup_id="grp-0")  # D0=1행(부분적재)
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert res["alerts"] == []  # 부분적재 억제(경보 조건 충족해도 발화 안 함)


def test_normal_load_still_fires(db):
    """P2-1 대조: D0 행수가 baseline과 비등(ratio≥0.5)하면 가드 미적용 — 경보 정상 발화.
    baseline 1행/일, D0 1행 → ratio 1.0."""
    _settings(db)
    _trailing(db)  # 트레일링 CTR 기준선(D-NAO-103 기대클릭 게이트 통과용)
    for d in (D0 - timedelta(days=3), D0 - timedelta(days=2), D0 - timedelta(days=1)):
        _daily(db, ad_date=d, imp=300, clk=0, rank=3.0, adgroup_id="grp-0")
    _daily(db, ad_date=D0, imp=300, clk=0, rank=3.0, adgroup_id="grp-0")
    res = ctr_alert.detect_ctr_alerts(db, CAMPAIGN, now=NOW)
    assert _one(res["alerts"], "W1") is not None  # 가드 미적용 → W1 정상 발화
