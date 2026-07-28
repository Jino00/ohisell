# test_naver_ctr_alert_briefing.py — 소재 CTR 경보 브리핑 harness + humanizer 단위테스트
#   (D-NAO-103). 커버: 신규 진입만 발화·만성 억제·월요일 주간 요약·캠페인 3개↑ 압축·
#   파워링크 문구 분기(탐색 래더 문구 금지)·이름 변환 폴백(entity 이름 없으면 ID 유지)·
#   W1/W3 dedupe와 사람 말 창 표기·이력 기록(발화한 것이 아니라 판정된 것 전부).
# 실 API 0 — DB 시드만(naver_entity/naver_ad_daily/naver_ctr_alert_log).
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverCtrAlertLog, NaverEntity
from app.services.naver_ad import alert_humanizer, ctr_alert_briefing

CAMPAIGN = "cmp-a001-01-000000010236310"
MONDAY = datetime(2026, 7, 27, 8, 50, 0)   # weekday()==0 → 주간 요약 발송일
TUESDAY = datetime(2026, 7, 28, 8, 50, 0)  # weekday()==1 → 만성 건 완전 침묵
D0_MON = date(2026, 7, 26)
D0_TUE = date(2026, 7, 27)


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


def _entity(db, entity_type, entity_id, name, *, campaign_id=CAMPAIGN, campaign_type="WEB_SITE"):
    db.add(NaverEntity(
        entity_type=entity_type, entity_id=entity_id, name=name,
        campaign_id=campaign_id, campaign_type=campaign_type, status="on",
    ))
    db.commit()


def _spend(db, *, as_of, cost=40000, clk=67, conv=2, campaign_id=CAMPAIGN):
    """최근 7일 캠페인 합계의 원료 1행(as_of 당일에 몰아 넣어도 창 안이라 합계는 동일)."""
    db.add(NaverAdDaily(
        ad_date=as_of, campaign_id=campaign_id, campaign_type="WEB_SITE",
        adgroup_id="grp-spend", keyword_id="", imp=10000, clk=clk, cost=cost,
        rank_sum=30000, conv_direct_cnt=conv,
    ))
    db.commit()


def _alert(gid, *, window="W3", imp=1240, clk=0, rank=3.2, expected=3.4,
           campaign_id=CAMPAIGN, campaign_type="WEB_SITE"):
    return {
        "campaign_id": campaign_id, "campaign_type": campaign_type, "adgroup_id": gid,
        "window": window, "imp": imp, "clk": clk, "avg_rank": rank,
        "expected_clk": expected, "reason": "…",
    }


# ══════════════════════════ 신규 진입 / 만성 억제 ══════════════════════════

def test_new_entry_fires_and_is_recorded(db):
    """전일 이력이 없는 그룹 = 신규 진입 → 개별 발화. 판정 이력이 as_of 기준으로 남는다."""
    _entity(db, "campaign", CAMPAIGN, "○ P. 아이패드 파워링크")
    _entity(db, "adgroup", "adg-1", "프로M4 11인치 6H")
    _spend(db, as_of=D0_TUE)
    res = ctr_alert_briefing.build_briefing(db, [_alert("adg-1")], now=TUESDAY)

    assert res["new"] == 1 and res["fired"] == 1 and res["suppressed"] == 0
    assert res["text"] is not None and "새로 생긴 문제 1건" in res["text"]
    rows = db.query(NaverCtrAlertLog).all()
    assert len(rows) == 1
    assert rows[0].as_of_date == D0_TUE and rows[0].streak_days == 1 and rows[0].notified is True


def test_chronic_is_suppressed_on_non_weekly_day(db):
    """전일에도 판정됐던 그룹(만성)은 월요일이 아니면 완전 침묵 — 그래도 이력은 남는다
    (남기지 않으면 다음날 '신규'로 되살아나 억제가 매일 무효화된다)."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    db.add(NaverCtrAlertLog(
        as_of_date=D0_TUE - timedelta(days=1), campaign_id=CAMPAIGN, adgroup_id="adg-1",
        window="W3", imp=1200, clk=0, streak_days=4, notified=False,
    ))
    db.commit()
    res = ctr_alert_briefing.build_briefing(db, [_alert("adg-1")], now=TUESDAY)

    assert res["text"] is None
    assert res["chronic"] == 1 and res["fired"] == 0 and res["suppressed"] == 1
    row = db.query(NaverCtrAlertLog).filter(NaverCtrAlertLog.as_of_date == D0_TUE).one()
    assert row.streak_days == 5 and row.notified is False


def test_chronic_summarized_on_monday(db):
    """같은 만성 건이 월요일에는 주간 요약으로 한 번 나간다(연속 일수 표기 포함)."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    _entity(db, "adgroup", "adg-1", "프로M4 11인치 6H")
    db.add(NaverCtrAlertLog(
        as_of_date=D0_MON - timedelta(days=1), campaign_id=CAMPAIGN, adgroup_id="adg-1",
        window="W3", imp=1200, clk=0, streak_days=4, notified=False,
    ))
    db.commit()
    res = ctr_alert_briefing.build_briefing(db, [_alert("adg-1")], now=MONDAY)

    assert res["chronic"] == 1 and res["fired"] == 1
    assert "주간 요약" in res["text"] and "5일째" in res["text"]


def test_new_and_chronic_both_sections_on_monday(db):
    """월요일엔 신규 섹션 + 주간 요약 섹션이 함께 나갈 수 있다(서로 다른 헤더)."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    db.add(NaverCtrAlertLog(
        as_of_date=D0_MON - timedelta(days=1), campaign_id=CAMPAIGN, adgroup_id="adg-old",
        window="W3", imp=1200, clk=0, streak_days=2, notified=False,
    ))
    db.commit()
    res = ctr_alert_briefing.build_briefing(
        db, [_alert("adg-old"), _alert("adg-new")], now=MONDAY,
    )
    assert "새로 생긴 문제 1건" in res["text"]
    assert "계속 이어지는 문제 1건" in res["text"]


# ══════════════════════════ 캠페인 단위 압축 ══════════════════════════

def test_campaign_compression_for_three_or_more_groups(db):
    """한 캠페인에서 8그룹 동시 발화 → 캠페인 요약 1줄 + 그룹 상세 3개 + '외 5개'."""
    _entity(db, "campaign", CAMPAIGN, "○ P. 아이패드 파워링크")
    for i in range(8):
        _entity(db, "adgroup", f"adg-{i}", f"그룹{i} 11인치 6H")
    _spend(db, as_of=D0_TUE)
    alerts = [_alert(f"adg-{i}") for i in range(8)]
    res = ctr_alert_briefing.build_briefing(db, alerts, now=TUESDAY)

    text = res["text"]
    assert "광고그룹 8개가" in text
    assert "외 5개" in text
    assert text.count("11인치 6H") == 3          # 상세는 3개만
    assert "아이패드 파워링크" in text            # 장식 접두(○ P.) 제거된 이름
    assert "최근 7일 이 캠페인: 4.0만 원 지출" in text
    assert "클릭 67" in text and "전환 2건" in text


def test_two_groups_are_listed_without_compression(db):
    """3개 미만이면 압축 없이 둘 다 상세로 나간다('외 N개' 없음)."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    _entity(db, "adgroup", "adg-0", "프로M4 11인치")
    _entity(db, "adgroup", "adg-1", "에어 11인치")
    res = ctr_alert_briefing.build_briefing(
        db, [_alert("adg-0"), _alert("adg-1")], now=TUESDAY,
    )
    assert "프로M4 11인치" in res["text"] and "에어 11인치" in res["text"]
    assert "외 " not in res["text"]


# ══════════════════════════ 파워링크 문구 분기 ══════════════════════════

def test_powerlink_message_has_no_exploration_ladder_wording(db):
    """파워링크(WEB_SITE)에는 탐색 래더 문구를 쓰지 않는다 — 탐색 래더는 SHOPPING 전용이라
    실제로 적용되지 않는다(구 브리핑이 매일 내보내던 거짓 문구)."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    res = ctr_alert_briefing.build_briefing(db, [_alert("adg-1")], now=TUESDAY)

    assert "래더" not in res["text"] and "탐색" not in res["text"]
    assert "광고 문구" in res["text"]


def test_shopping_message_keeps_exploration_ladder_wording(db):
    """쇼핑 캠페인에는 탐색 래더 중지 문구가 그대로 유지된다(실제 게이트가 작동하므로)."""
    _entity(db, "campaign", "cmp-04", "04 아이폰 지문방지필름", campaign_type="SHOPPING")
    res = ctr_alert_briefing.build_briefing(
        db, [_alert("adg-1", campaign_id="cmp-04", campaign_type="SHOPPING")], now=TUESDAY,
    )
    assert "자동 탐색 입찰 인상은 이 그룹들에서 중지됩니다" in res["text"]
    assert "썸네일" in res["text"]


# ══════════════════════════ 이름 변환 폴백 / dedupe ══════════════════════════

def test_name_fallback_keeps_ids_when_entity_missing(db):
    """naver_entity에 이름이 없으면(행 부재·빈 이름) ID를 그대로 표시한다 — 대상이 사라지는
    것보다 못 읽는 편이 낫다."""
    _entity(db, "adgroup", "adg-blank", "   ")  # 이름이 사실상 빈 값
    res = ctr_alert_briefing.build_briefing(
        db, [_alert("adg-blank"), _alert("adg-missing")], now=TUESDAY,
    )
    assert CAMPAIGN in res["text"]        # 캠페인 이름 행 자체가 없음 → ID 유지
    assert "adg-blank" in res["text"] and "adg-missing" in res["text"]


def test_dedupe_merges_w1_and_w3_into_one_group_row(db):
    """같은 그룹의 W1·W3는 한 건으로 합쳐지고 창은 사람 말로 병기된다. 대표 숫자는 노출이
    큰 쪽(더 넓은 창)을 남긴다."""
    _entity(db, "campaign", CAMPAIGN, "아이패드 파워링크")
    _entity(db, "adgroup", "adg-1", "프로M4 11인치")
    alerts = [
        _alert("adg-1", window="W1", imp=520, expected=2.1),
        _alert("adg-1", window="W3", imp=1240, expected=3.4),
    ]
    res = ctr_alert_briefing.build_briefing(db, alerts, now=TUESDAY)

    assert res["total"] == 1
    assert "최근 1일·3일" in res["text"]
    assert "노출 1,240" in res["text"] and "평소라면 3.4건" in res["text"]


def test_empty_alerts_is_total_silence(db):
    res = ctr_alert_briefing.build_briefing(db, [], now=TUESDAY)
    assert res["text"] is None and res["total"] == 0
    assert db.query(NaverCtrAlertLog).count() == 0


# ══════════════════════════ humanizer 단위 ══════════════════════════

def test_humanizer_clean_name_strips_operational_prefixes():
    assert alert_humanizer.clean_name("○ P. 아이패드 파워링크") == "아이패드 파워링크"
    assert alert_humanizer.clean_name("04. 아이폰 필름") == "아이폰 필름"
    assert alert_humanizer.clean_name("  ") == ""
    assert alert_humanizer.clean_name(None) == ""


def test_humanizer_window_and_rank_and_money():
    assert alert_humanizer.window_label("W1") == "최근 1일"
    assert alert_humanizer.window_label("W1+W3") == "최근 1일·3일"
    assert alert_humanizer.window_label("") == ""
    assert "정상" in alert_humanizer.rank_label(3.2)
    assert "밀림" in alert_humanizer.rank_label(6.0)
    assert alert_humanizer.rank_label(None) == "순위 확인 불가"
    assert alert_humanizer.money(40000) == "4.0만 원"
    assert alert_humanizer.money(3200) == "3,200원"
    assert alert_humanizer.money(150_000_000) == "1.5억 원"


def test_humanizer_streak_label():
    assert alert_humanizer.streak_label(1) == "(오늘 처음)"
    assert alert_humanizer.streak_label(None) == "(오늘 처음)"
    assert alert_humanizer.streak_label(5) == "(5일째)"
