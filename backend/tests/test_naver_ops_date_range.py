# test_naver_ops_date_range.py — 네이버 운영 패널의 임의 기간 선택
#
# 왜 생겼나(2026-08-14): 이 화면은 오늘/어제/7·15·30일 **버튼뿐**이라 특정 과거 하루나
#   임의 구간을 볼 방법이 없었다. 전날 완료 QA도 정확히 이 한계에 걸렸다 —
#   「합격기준의 숫자를 재관측하려 했으나 API가 임의 과거 단일일 지정을 제공하지 않는다」.
#
# 이 파일이 지키는 것 셋:
#   ①버튼과 날짜 입력이 **같은 축**을 쓴다(둘이 다른 경로면 같은 기간에 다른 값이 나온다 —
#     2026-08-13 쿠팡 사고가 정확히 그 모양이었다).
#   ②「오늘」의 **특수 광고 축**이 날짜로 골랐을 때도 보존된다. days=0은 `ad_costs`가 아니라
#     당일 시간별 스냅샷의 «관측 최대치»를 쓴다(원천 누적이 후퇴하기 때문, 교훈: 2026-08-06
#     부호가 뒤집힌 사고). 이게 빠지면 같은 하루가 «어떻게 골랐느냐»에 따라 다른 이익을 낸다.
#   ③잘못된 입력을 **조용히 보정하지 않는다**. 보정하면 화면 숫자가 사용자가 고른 기간이
#     아니게 되는데 화면은 그걸 말하지 않는다.
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AdCost, Channel, NaverHourlySnapshot, Order
from app.routers.naver_ops import sales_summary
from app.utils.kst import kst_today

D = Decimal
NAVER_ID = 6
TODAY = kst_today()
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    s.add(Channel(id=NAVER_ID, code="NAVER", name="네이버 스마트스토어",
                  platform="naver", channel_type="own"))
    # 어제 확정 광고비 + 어제 주문
    s.add(AdCost(channel_id=NAVER_ID, ad_date=YESTERDAY,
                 ad_spend=D("698119"), source="naver_sa:지문방지"))
    s.add(Order(channel_id=NAVER_ID, order_number="N-1", platform_product_id="P1",
                platform_product_name="어제상품", quantity=1, selling_price=D("110000"),
                order_date=datetime.combine(YESTERDAY, datetime.min.time().replace(hour=12)),
                status="delivered"))
    # 오늘 주문 + 당일 시간별 스냅샷(오늘의 광고 축)
    s.add(Order(channel_id=NAVER_ID, order_number="N-2", platform_product_id="P2",
                platform_product_name="오늘상품", quantity=1, selling_price=D("220000"),
                order_date=datetime.combine(TODAY, datetime.min.time().replace(hour=10)),
                status="delivered"))
    for hour, cost in ((17, 506370), (20, 398102), (21, 360731)):
        # ★20시가 17시보다 낮다 = 원천 누적의 «후퇴». 축은 최대치를 써야 한다.
        s.add(NaverHourlySnapshot(
            snapshot_at=datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=hour, minutes=5),
            ad_date=TODAY, snapshot_hour=hour, campaign_id="cmp-1", cost=cost, imp=100))
    s.commit()
    yield s
    s.close()


def _iso(d) -> str:
    return d.isoformat()


# ── ①②  버튼과 날짜가 같은 축 ────────────────────────────────────────

def test_오늘을_날짜로_골라도_오늘_버튼과_같다(db):
    """★핵심 — days=0의 «당일 스냅샷 최대치» 광고 축이 날짜 경로에서도 살아야 한다.

    안 그러면 같은 하루가 어떻게 골랐느냐에 따라 다른 광고비·다른 이익을 낸다."""
    by_button = sales_summary(days=0, db=db)
    by_date = sales_summary(days=7, date_from=_iso(TODAY), date_to=_iso(TODAY), db=db)
    assert by_date["period"] == by_button["period"]
    assert by_date["ad_ref_date"] == by_button["ad_ref_date"]
    assert by_date["ad_basis"] == by_button["ad_basis"]
    assert by_date["summary"]["ad_spend"] == by_button["summary"]["ad_spend"]
    assert by_date["summary"]["profit"] == by_button["summary"]["profit"]


def test_오늘_광고비는_후퇴한_누적이_아니라_최대치다(db):
    """위 동치가 «둘 다 틀린 값»으로 성립하면 의미가 없다 — 축 자체를 못박는다."""
    s = sales_summary(days=7, date_from=_iso(TODAY), date_to=_iso(TODAY), db=db)
    assert Decimal(s["summary"]["ad_spend"]) == Decimal("506370"), \
        "당일 광고비가 관측 최대치가 아니다(후퇴값이나 어제치를 물었다)"


def test_어제를_날짜로_골라도_어제_버튼과_같다(db):
    by_button = sales_summary(days=1, db=db)
    by_date = sales_summary(days=7, date_from=_iso(YESTERDAY), date_to=_iso(YESTERDAY), db=db)
    assert by_date["summary"] == by_button["summary"]
    assert by_date["period"] == by_button["period"]


def test_임의_구간이_그_구간을_돌려준다(db):
    s = sales_summary(days=7, date_from=_iso(YESTERDAY), date_to=_iso(TODAY), db=db)
    assert s["period"] == {"from": _iso(YESTERDAY), "to": _iso(TODAY)}


def test_days_프리셋은_안_바뀐다(db):
    """회귀 방어 — 프리셋이 **어느 창**을 가리키는지 못박는다.

    ★`from <= to`만 보면 창이 통째로 밀려도 통과한다(적대 리뷰 1R P2-2).
      화면은 이제 날짜를 보내지만, 이 경로는 API 계약으로 남아 있다."""
    expected = {
        0:  (TODAY, TODAY),
        1:  (YESTERDAY, YESTERDAY),
        7:  (TODAY - timedelta(days=6), TODAY),
        15: (TODAY - timedelta(days=14), TODAY),
        30: (TODAY - timedelta(days=29), TODAY),
    }
    for d, (f, t_) in expected.items():
        out = sales_summary(days=d, db=db)
        assert out["period"] == {"from": _iso(f), "to": _iso(t_)}, f"days={d} 창이 밀렸다"


def test_다일_구간은_확정치에_당일치를_더한다(db):
    """오늘을 «포함»하는 구간과 «오늘 하루»는 다른 경로다 — 종전 days>=2 동작을 유지한다.

    ★`kind != "today"`는 공허했다(적대 리뷰 1R P2-1): 실제 값은
      `today_snapshot`/`today_no_snapshot`/`period`라 어떤 구현에서도 참이다. 금액으로 못박는다.
    ★기대값의 근거(실측으로 정정): 다일 구간은 어제까지의 확정치(`ad_costs` 698,119)에
      **당일 스냅샷 최대치(506,370)를 더한다**(`ad_basis.today_search_added`). 오늘 하루만
      고른 경우(506,370 단독)와 금액이 달라야 두 경로가 안 섞였다는 뜻이다."""
    s = sales_summary(days=7, date_from=_iso(YESTERDAY), date_to=_iso(TODAY), db=db)
    assert s["ad_ref_date"] is None, "다일 구간인데 오늘 탭 기준일이 붙었다"
    assert Decimal(s["summary"]["ad_spend"]) == Decimal("1204489"), \
        "다일 구간의 광고비 축이 바뀌었다(확정치 698,119 + 당일 506,370)"


# ── ③  잘못된 입력은 거절한다 ────────────────────────────────────────

def test_한쪽만_주면_거절한다(db):
    """조용히 나머지를 채우면 화면 숫자가 사용자가 고른 기간이 아니게 된다."""
    for kwargs in ({"date_from": _iso(TODAY)}, {"date_to": _iso(TODAY)}):
        with pytest.raises(HTTPException) as e:
            sales_summary(days=7, db=db, **kwargs)
        assert e.value.status_code == 400


def test_시작일이_종료일보다_늦으면_거절한다(db):
    with pytest.raises(HTTPException) as e:
        sales_summary(days=7, date_from=_iso(TODAY), date_to=_iso(YESTERDAY), db=db)
    assert e.value.status_code == 400
    assert "늦" in e.value.detail


def test_90일을_넘으면_거절한다(db):
    """`days` 파라미터의 le=90과 같은 한계 — 두 입구가 다른 한계를 가지면 안 된다."""
    with pytest.raises(HTTPException) as e:
        sales_summary(days=7, date_from=_iso(TODAY - timedelta(days=90)),
                      date_to=_iso(TODAY), db=db)
    assert e.value.status_code == 400
    assert "90" in e.value.detail


def test_정확히_90일은_통과한다(db):
    """경계 — 상한 자체는 허용한다(off-by-one 방어)."""
    out = sales_summary(days=7, date_from=_iso(TODAY - timedelta(days=89)),
                        date_to=_iso(TODAY), db=db)
    assert out["period"]["to"] == _iso(TODAY)


def test_형식이_틀리면_거절한다(db):
    with pytest.raises(HTTPException) as e:
        sales_summary(days=7, date_from="2026/08/14", date_to=_iso(TODAY), db=db)
    assert e.value.status_code == 400
