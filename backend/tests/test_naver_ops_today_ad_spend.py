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
from app.models import AdCost, Channel, NaverHourlySnapshot
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


def _snapshot(db, *, hour: int, cost: int, campaign: str = "cmp-1") -> datetime:
    at = datetime.combine(kst_today(), datetime.min.time()) + timedelta(hours=hour, minutes=5)
    db.add(NaverHourlySnapshot(snapshot_at=at, ad_date=kst_today(), snapshot_hour=hour,
                               campaign_id=campaign, cost=cost))
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


def test_other_periods_still_use_ad_costs(db):
    """어제·7일 등은 확정치(ad_costs) 그대로 — 이 변경의 사거리 밖이다."""
    _yesterday_ad(db, "698119")
    _snapshot(db, hour=16, cost=360731)

    out = sales_summary(days=1, db=db)

    assert out["summary"]["ad_spend"] == "698119.00"
    assert out["ad_basis"] is None
