# test_naver_ops_today_ad_spend.py — 「오늘」 광고비가 어제치를 물고 오지 않는다는 가드
#
# ★무엇을 고정하나(2026-08-06 라이브 발견): `ad_costs`에는 오늘 행이 없다(SA 리포트도
#   비즈머니 실차감도 D+1). 종전 코드는 그 자리에 **어제 전일치**를 넣어, 이익 카드가
#   「오늘 매출 − 어제 광고비」가 됐다. 실측: 매출 871,745원 − 어제 광고비 698,119원 →
#   −202,434원(−23.2%) 적자로 표시. 같은 시각 실제 당일 누적은 360,731원이라 실제로는
#   +134,953원 흑자였다 — **부호가 뒤집혔다.** 라벨("광고비 기준일: 어제")은 있었지만
#   이익·이익률 카드는 그 사실을 말하지 않았다.
#
# 판정 원천은 `naver_hourly_snapshot`(매시간 /stats datePreset=today 당일 누적).
# 축 동일성 교차검증(2026-08-05 라이브): 마지막 스냅샷 675,090원 vs ad_costs naver_sa
# 675,089원 — 1원 차이(반올림). 같은 돈이다.
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AdCost, Channel, NaverHourlySnapshot, Order
from app.routers.naver_ops import sales_summary
from app.utils.kst import kst_today

D = Decimal
NAVER_ID = 6


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    s.add(Channel(id=NAVER_ID, code="NAVER", name="네이버 스마트스토어",
                  platform="naver", channel_type="own"))
    s.commit()
    yield s
    s.close()


def _yesterday_ad(db, amount: str) -> None:
    """어제 확정 광고비 — 이게 오늘 화면에 새어나오면 안 된다."""
    db.add(AdCost(channel_id=NAVER_ID, ad_date=kst_today() - timedelta(days=1),
                  ad_spend=D(amount), source="naver_sa:지문방지"))
    db.commit()


def _snapshot(db, *, hour: int, cost: int, campaign: str = "cmp-1", imp: int | None = None) -> datetime:
    at = datetime.combine(kst_today(), datetime.min.time()) + timedelta(hours=hour, minutes=5)
    db.add(NaverHourlySnapshot(snapshot_at=at, ad_date=kst_today(), snapshot_hour=hour,
                               campaign_id=campaign, cost=cost, imp=imp))
    db.commit()
    return at


def test_today_uses_snapshot_not_yesterday(db):
    """★핵심 회귀 — 오늘 광고비는 당일 누적이고, 어제 확정치는 새어나오지 않는다."""
    _yesterday_ad(db, "698119")
    _snapshot(db, hour=15, cost=308910)
    at16 = _snapshot(db, hour=16, cost=360731)

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "360731.00"      # 최신 스냅샷 = 당일 누적
    assert out["ad_basis"]["kind"] == "today_snapshot"
    assert out["ad_basis"]["as_of"] == at16.isoformat(timespec="seconds")
    assert out["ad_basis"]["scope"] == "search_only"      # 디스플레이는 당일치가 없다


def test_today_takes_latest_snapshot_only_not_sum_of_hours(db):
    """스냅샷은 **누적**이라 시간별로 더하면 안 된다(15시+16시=669,641은 오답)."""
    _snapshot(db, hour=15, cost=308910)
    _snapshot(db, hour=16, cost=360731)

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "360731.00"


def test_today_sums_campaigns_within_the_same_snapshot(db):
    """같은 시각 스냅샷 안에서는 캠페인을 합산한다(라이브 46캠페인)."""
    _snapshot(db, hour=16, cost=200000, campaign="cmp-1")
    _snapshot(db, hour=16, cost=160731, campaign="cmp-2")

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "360731.00"


def test_no_snapshot_yet_reports_zero_not_yesterday(db):
    """자정~첫 스냅샷 사이 — 어제치로 되돌리지 않는다.

    모르는 것을 아는 척하는 것이 이 결함의 원인이었다. 0으로 두고 화면이 사유를 말한다.
    """
    _yesterday_ad(db, "698119")

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "0.00"
    assert out["ad_basis"]["kind"] == "today_no_snapshot"
    assert out["ad_basis"]["as_of"] is None


def test_cumulative_regression_keeps_observed_max(db):
    """★★NAVER 당일 누적은 **후퇴한다** — 화면은 관측 최대치를 쓰고 그 사실을 응답에 싣는다.

    라이브 실측(2026-08-06 20:04): 19:05~20:00 9회 연속 506,370원(clk 456·imp 66,760)이던
    값이 20:04에 398,102원(clk 366·imp 53,836)으로 떨어졌고 17시 슬롯과 원 단위까지 같았다
    (3시간 전 상태). 20:05 크론이 그걸 20시 슬롯에 적재해 화면 광고비가 10.8만원 줄고
    **이익이 그만큼 과대**로 표시됐다. 최근 14일에 같은 후퇴 14건.
    누적은 정의상 뒤로 갈 수 없으므로 최대치가 정답에 더 가깝다.
    """
    _snapshot(db, hour=19, cost=506370, imp=66760)
    _snapshot(db, hour=20, cost=398102, imp=53836)   # 후퇴

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "506370.00", "후퇴한 최신 배치를 그대로 쓰면 이익이 과대"
    assert out["ad_basis"]["basis"] == "day_max"
    assert out["ad_basis"]["regressed_by"] == "108268.00"   # 화면이 후퇴를 말할 수 있어야 한다
    assert out["ad_basis"]["latest_cost"] == "398102.00"
    assert out["ad_basis"]["pending"] is False


def test_no_regression_reports_zero(db):
    """정상 전진 구간에선 후퇴 금액이 0이다 — 상시 경고는 경고를 무의미하게 만든다."""
    _snapshot(db, hour=18, cost=447936, imp=60214)
    _snapshot(db, hour=19, cost=506370, imp=66760)

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "506370.00"
    assert out["ad_basis"]["regressed_by"] == "0.00"


def test_partial_batch_does_not_lower_today_spend(db):
    """부분 적재(46개 중 일부만 담긴 슬롯)도 광고비를 떨어뜨리지 못한다.

    캠페인별 최대 누적을 쓰므로 빠진 캠페인은 이전 최대치를 유지한다.
    """
    _snapshot(db, hour=14, cost=300000, campaign="cmp-1", imp=1000)
    _snapshot(db, hour=14, cost=200000, campaign="cmp-2", imp=800)
    _snapshot(db, hour=15, cost=320000, campaign="cmp-1", imp=1100)   # cmp-2 누락

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "520000.00"   # 320,000 + 200,000(유지)
    assert out["ad_basis"]["regressed_by"] == "200000.00"


def test_zero_batch_after_known_value_does_not_revert_to_unknown(db):
    """이미 값을 알고 있으면 최신 배치가 전부 0이어도 «모름»으로 되돌리지 않는다."""
    _snapshot(db, hour=13, cost=360731, imp=47720)
    _snapshot(db, hour=14, cost=0, imp=0)

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "360731.00"
    assert out["ad_basis"]["pending"] is False


def test_snapshot_of_all_zeros_is_pending_not_a_confirmed_zero(db):
    """★스냅샷이 있는데 전부 0이면 «모름»이다 — 확정 0원이 아니다.

    실측 5일 전수(2026-08-02~06): NAVER /stats 당일치는 자정 직후 아무 값도 주지 않는다
    (0시·1시 슬롯의 cost·clk·imp 모두 0 → 2시 슬롯에서 2.5~3만원이 한꺼번에 등장).
    그 0을 확정 실측처럼 내면 이익이 과대로 보인다 — 어제치를 물고 오던 결함과 같은 종류가
    새벽 구간에만 남아 있던 셈이다(교훈 #151).
    """
    _yesterday_ad(db, "698119")
    _snapshot(db, hour=1, cost=0, imp=0)

    out = sales_summary(days=0, db=db)

    assert out["summary"]["ad_spend"] == "0.00"          # 어제치로 되돌리지 않는다
    assert out["ad_basis"]["kind"] == "today_snapshot"
    assert out["ad_basis"]["pending"] is True


def test_nonzero_snapshot_is_not_pending(db):
    """값이 있으면 pending이 아니다 — 정상 구간에 경고를 띄우면 경고가 무의미해진다."""
    _snapshot(db, hour=19, cost=506370, imp=66760)

    out = sales_summary(days=0, db=db)

    assert out["ad_basis"]["pending"] is False


def test_zero_cost_with_impressions_is_not_pending(db):
    """노출은 있는데 지출이 0 = 집계는 됐고 실제로 안 쓴 것이다 — «모름»이 아니다."""
    _snapshot(db, hour=9, cost=0, imp=1200)

    out = sales_summary(days=0, db=db)

    assert out["ad_basis"]["pending"] is False


def test_no_snapshot_yet_is_pending(db):
    """첫 수집 전도 «모름»이다 — 같은 문구 경로를 타야 화면이 갈라지지 않는다."""
    out = sales_summary(days=0, db=db)

    assert out["ad_basis"]["kind"] == "today_no_snapshot"
    assert out["ad_basis"]["pending"] is True


def test_other_periods_still_use_ad_costs(db):
    """어제·7일 등은 확정치(ad_costs) 그대로 — 이 변경의 사거리 밖이다."""
    _yesterday_ad(db, "698119")
    _snapshot(db, hour=16, cost=360731)

    out = sales_summary(days=1, db=db)

    assert out["summary"]["ad_spend"] == "698119.00"
    assert out["ad_basis"] is None


# ── 기간 탭에 오늘 검색광고비가 빠지던 것 (2026-08-06) ────────────────
# ★배경: `ad_costs`는 D+1 확정이라 **오늘 행이 없다.** 그런데 매출은 오늘까지 들어간다
#   → 「오늘까지」 걸친 모든 기간에서 이익이 오늘 광고비만큼 과대였다.
#   라이브 실측(08-06 23:05): 30일 창에 오늘 매출 1,376,420원은 들어가는데 오늘 광고비
#   675,561원은 0원으로 들어갔다. 「오늘」 탭이 어제치를 물고 오던 결함(#151)의 **거울상** —
#   이쪽은 이미 손에 있는 값을 안 붙이고 있었다.
# ★디스플레이(ADVoost·GFA)는 못 메운다 — 비즈머니 실차감이 유일 경로인데 D−1까지만 준다.
#   그래서 합계는 «검색은 오늘까지 + 디스플레이는 어제까지»인 혼합 축이고, ad_basis가 말한다.

def test_period_adds_today_search_spend(db):
    """★핵심 회귀 — 오늘이 포함된 기간이면 오늘 검색광고 당일 누적을 더한다."""
    _yesterday_ad(db, "698119")
    _snapshot(db, hour=23, cost=675561)

    out = sales_summary(days=7, db=db)

    # 어제 확정 698,119 + 오늘 당일 누적 675,561
    assert out["summary"]["ad_spend"] == "1373680.00"
    assert out["ad_basis"]["kind"] == "period"
    assert out["ad_basis"]["today_search_added"] == "675561.00"
    assert out["ad_basis"]["today_search_source"] == "today_snapshot"
    assert out["ad_basis"]["today_display_missing"] is True


def test_period_uses_day_max_not_latest_batch(db):
    """기간 탭도 「오늘」 탭과 **같은 축**(캠페인별 관측 최대치)을 쓴다 — 후퇴에 안 속는다.

    두 탭이 다른 축을 쓰면 같은 날 광고비가 탭마다 달라진다.
    """
    _snapshot(db, hour=19, cost=506370)
    _snapshot(db, hour=20, cost=398102)   # 후퇴

    out = sales_summary(days=7, db=db)

    assert out["ad_basis"]["today_search_added"] == "506370.00"   # 최신(398,102)이 아니다
    assert out["summary"]["ad_spend"] == "506370.00"


def test_period_does_not_double_count_when_today_already_in_ad_costs(db):
    """★이중계상 가드 — 오늘치가 이미 `ad_costs`에 있으면 스냅샷을 더하지 않는다."""
    db.add(AdCost(channel_id=NAVER_ID, ad_date=kst_today(), ad_spend=D("675561"),
                  source="naver_sa:지문방지"))
    db.commit()
    _snapshot(db, hour=23, cost=675561)

    out = sales_summary(days=7, db=db)

    assert out["summary"]["ad_spend"] == "675561.00"        # 두 번 안 더해진다
    assert out["ad_basis"]["today_search_source"] == "ad_costs"
    assert out["ad_basis"]["today_search_added"] == "0.00"


def test_period_reports_pending_when_no_snapshot_yet(db):
    """자정~첫 수집 사이 — 오늘 광고비는 «모름»이다. 값을 지어내지 않고 사실만 싣는다."""
    _yesterday_ad(db, "698119")

    out = sales_summary(days=7, db=db)

    assert out["summary"]["ad_spend"] == "698119.00"
    assert out["ad_basis"]["today_search_source"] == "pending"


def test_period_not_including_today_has_no_ad_basis(db):
    """오늘이 창에 없으면 더할 것도 없다 — ad_basis도 안 만든다(거짓 배너 방지)."""
    _snapshot(db, hour=23, cost=675561)
    db.add(AdCost(channel_id=NAVER_ID, ad_date=kst_today() - timedelta(days=3),
                  ad_spend=D("100000"), source="naver_sa:지문방지"))
    db.commit()

    out = sales_summary(days=1, db=db)   # 어제만

    assert out["ad_basis"] is None
    assert out["summary"]["ad_spend"] == "0.00"


# ── 반품·교환 배송 손익이 손익에 잡힌다 (2026-08-06) ──────────────────
# ★배경: 반품 건은 REVENUE_EXCLUDED라 매출·원가·배송비가 **전부 0으로 빠진다.** 그런데
#   실제로는 출고비와 회수비가 나가고 반품비가 들어온다 — 종전 이 패널은 **반품이 늘어도
#   이익이 반응하지 않았다**(반품이 공짜인 것처럼 보였다). 라이브 30일: 반품 완료 22건.
#   메인 엔진(profit_calculator)엔 이미 있었고 이 패널만 없었다 → 그 함수를 그대로 호출한다
#   (같은 주문에 두 엔진이 다른 값을 쓰면 안 된다).
# ★귀속일 = 반품 **완료일**(주문일 아님). 결제일에 실으면 지난달 이익이 반품마다 흔들린다.

def _returned(db, *, no: str, price: str, demand: str, support: str = "0",
              completed_days_ago: int = 1) -> None:
    """반품 완료 주문 1건. 매출에서는 빠지고 배송 손익만 남는다."""
    db.add(Order(
        channel_id=NAVER_ID, order_number=no, platform_product_id="P-R",
        platform_product_name="반품 상품", quantity=1, selling_price=D(price),
        shipping_cost=D("0"), commission_amount=D("0"), status="returned",
        order_date=datetime.combine(kst_today() - timedelta(days=completed_days_ago + 1),
                                    datetime.min.time()) + timedelta(hours=12),
        return_completed_at=datetime.combine(kst_today() - timedelta(days=completed_days_ago),
                                             datetime.min.time()) + timedelta(hours=12),
        return_fee_demand_amount=D(demand),
        return_fee_support_amount=D(support),
    ))
    db.commit()


def test_return_shipping_pnl_now_reaches_the_panel(db):
    """★핵심 — 반품 배송 손익이 이 패널에 **들어온다**. 종전엔 통째로 0이었다.

    ★부호를 단정하지 않는다: 고객에게 5,000원을 청구한 반품은 비용(출고 1,900 + 회수 2,500
      = 4,400)을 넘어 **배송 축만 보면 소폭 흑자**다. 반품의 진짜 손실은 매출을 잃는 것이지
      배송 손익이 아니다 — 이 필드는 «반품이 공짜가 아님»을 보이는 축이지 손실 추정치가 아니다.
      (처음 이 테스트를 음수로 못 박았다가 실측이 +421.82를 내서 기대값을 고쳤다. 코드가 아니라
       내 가정이 틀렸다 — 미청구 건은 아래 테스트가 음수를 고정한다.)
    """
    _returned(db, no="R1", price="30000", demand="5000")

    out = sales_summary(days=7, db=db)
    s = out["summary"]

    assert s["claim_count"] == 1
    # 수입 5,000(VAT포함) → 공급가 4,545.45
    assert s["claim_income"] == "4545.45"
    # 비용 = 출고비 1,900 + 회수비 2,500 = 4,400 → 공급가 4,000.00
    assert s["claim_cost"] == "4000.00"
    # 순효과 = (수입 − 배송수수료 − 비용) ÷ 1.1. 0이 아니라는 것이 이 변경의 요점이다.
    assert s["claim_net"] == "421.82"
    # 반품 매출 30,000은 매출에 안 들어간다(REVENUE_EXCLUDED) — 클레임 수입만 들어간다.
    assert s["revenue"] == "4545.45"


def test_return_does_not_inflate_shipment_count(db):
    """클레임은 주문이 아니다 — 배송 건수를 올리면 객단가가 틀어진다."""
    _returned(db, no="R1", price="30000", demand="5000")

    out = sales_summary(days=7, db=db)

    assert out["summary"]["shipment_count"] == 0


def test_return_uncharged_case_is_pure_loss(db):
    """미청구 반품(demand=0)은 수입 없이 비용만 — 정액 5,000을 쓰면 이익 나는 일로 보인다.

    라이브 86건 중 22건이 미청구였다(메인 엔진 docstring). 그래서 건별 실측만 쓴다.
    """
    _returned(db, no="R1", price="30000", demand="0")

    out = sales_summary(days=7, db=db)
    s = out["summary"]

    assert s["claim_income"] == "0.00"
    assert Decimal(s["claim_cost"]) > 0
    assert Decimal(s["claim_net"]) < 0


def test_return_completed_outside_window_is_excluded(db):
    """귀속은 **완료일** — 창 밖에서 완료된 반품은 이 기간 손익에 안 들어간다."""
    _returned(db, no="R1", price="30000", demand="5000", completed_days_ago=30)

    out = sales_summary(days=7, db=db)

    assert out["summary"]["claim_count"] == 0
    assert out["summary"]["claim_net"] == "0.00"


def test_no_returns_reports_zero(db):
    """반품이 없으면 0 — 거짓 손실을 만들지 않는다(회귀 가드)."""
    out = sales_summary(days=7, db=db)

    assert out["summary"]["claim_count"] == 0
    assert out["summary"]["claim_income"] == "0.00"
    assert out["summary"]["claim_net"] == "0.00"
