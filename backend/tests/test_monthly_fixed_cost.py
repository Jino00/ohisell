# 월 고정비 — 일할 배분과 손익 반영의 계약 고정
#
# 이 파일이 지키는 것: ①배분 합계 = 월 총액(원 단위) ②창을 잘라도 이중계상 없음
# ③주문 없는 날에도 비용이 사라지지 않음 ④payable_vat의 매입세액에 포함
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Channel, MonthlyFixedCost, Order
from app.services.profit_calculator import (
    calculate_channel_summary,
    calculate_daily_trend,
)
from app.services.monthly_fixed_cost_service import (
    FIXED_COST_ITEMS,
    allocate_month_daily,
    get_daily_fixed_cost,
    upsert_monthly_fixed_cost,
)

# 2026-07 두핸즈 정산서 실측
JULY = {"입고비": "73700", "보관료": "43549", "항공도선료": "19800", "합포장비": "2585"}
JULY_TOTAL = Decimal("139634")


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _naver_channel(db) -> Channel:
    ch = Channel(name="네이버 스마트스토어", code="NAVER", platform="naver", channel_type="own")
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


class TestAllocation:
    def test_31일치_합계가_월총액과_원단위로_일치한다(self):
        alloc = allocate_month_daily(Decimal("380034"), 2026, 7)
        assert len(alloc) == 31
        assert sum(alloc.values()) == Decimal("380034")

    def test_무한소수여도_합이_닫힌다(self):
        # 380034/31 = 12259.16129... — 매일 같은 값을 쓰면 합이 총액과 어긋난다
        alloc = allocate_month_daily(Decimal("380034"), 2026, 7)
        naive = (Decimal("380034") / 31).quantize(Decimal("0.01")) * 31
        assert naive != Decimal("380034")   # 단순 방식은 틀린다
        assert sum(alloc.values()) == Decimal("380034")  # 누적 차분은 맞는다

    def test_2월_윤년과_평년의_일수를_따른다(self):
        assert len(allocate_month_daily(Decimal("100"), 2026, 2)) == 28
        assert len(allocate_month_daily(Decimal("100"), 2028, 2)) == 29

    def test_하루도_음수가_되지_않는다(self):
        for d, v in allocate_month_daily(Decimal("1"), 2026, 7).items():
            assert v >= 0, d


class TestDailyLookup:
    def test_월_전체_조회하면_합계가_총액이다(self, db_session):
        ch = _naver_channel(db_session)
        for item, amt in JULY.items():
            upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", item, Decimal(amt))

        daily = get_daily_fixed_cost(db_session, date(2026, 7, 1), date(2026, 7, 31))
        assert sum(daily.values()) == JULY_TOTAL

    def test_창을_잘라도_이중계상이_없다(self, db_session):
        ch = _naver_channel(db_session)
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("100000"))

        first = get_daily_fixed_cost(db_session, date(2026, 7, 1), date(2026, 7, 15))
        second = get_daily_fixed_cost(db_session, date(2026, 7, 16), date(2026, 7, 31))
        whole = get_daily_fixed_cost(db_session, date(2026, 7, 1), date(2026, 7, 31))
        assert sum(first.values()) + sum(second.values()) == sum(whole.values())
        assert set(first) & set(second) == set()

    def test_여러_달에_걸친_창은_달마다_따로_배분한다(self, db_session):
        ch = _naver_channel(db_session)
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-06", "보관료", Decimal("30000"))
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("31000"))

        daily = get_daily_fixed_cost(db_session, date(2026, 6, 1), date(2026, 7, 31))
        assert sum(daily.values()) == Decimal("61000")
        assert len(daily) == 30 + 31

    def test_채널_필터가_먹는다(self, db_session):
        naver = _naver_channel(db_session)
        other = Channel(name="쿠팡", code="COUPANG", platform="coupang", channel_type="open_market")
        db_session.add(other)
        db_session.commit()
        upsert_monthly_fixed_cost(db_session, naver.id, "2026-07", "보관료", Decimal("31000"))

        mine = get_daily_fixed_cost(db_session, date(2026, 7, 1), date(2026, 7, 31), naver.id)
        theirs = get_daily_fixed_cost(db_session, date(2026, 7, 1), date(2026, 7, 31), other.id)
        assert sum(mine.values()) == Decimal("31000")
        assert theirs == {}

    def test_값이_없는_달은_0이지_추정하지_않는다(self, db_session):
        _naver_channel(db_session)
        assert get_daily_fixed_cost(db_session, date(2026, 5, 1), date(2026, 5, 31)) == {}


class TestProfitEngine:
    """손익 엔진에 실제로 반영되는가 — 여기가 이 기능의 존재 이유다."""

    def test_주문이_없는_날에도_비용이_사라지지_않는다(self, db_session):
        ch = _naver_channel(db_session)
        for item, amt in JULY.items():
            upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", item, Decimal(amt))

        trend = calculate_daily_trend(
            db_session, None, ch.id, date(2026, 7, 1), date(2026, 7, 31)
        )
        # 주문이 하나도 없어도 31일이 다 나오고, 합계가 월 총액과 일치한다
        assert len(trend) == 31
        assert sum(Decimal(r["fixed_cost"]) for r in trend) == JULY_TOTAL

    def test_고정비가_매입세액에_포함된다(self, db_session):
        ch = _naver_channel(db_session)
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("31000"))
        db_session.add(Order(
            channel_id=ch.id, order_number="N1", platform_product_id="NV1",
            selling_price=Decimal("10000"), quantity=1,
            order_date=datetime(2026, 7, 5, 10, 0),
            status="delivered", commission_amount=Decimal("550"),
        ))
        db_session.commit()

        row = next(
            r for r in calculate_daily_trend(
                db_session, None, ch.id, date(2026, 7, 1), date(2026, 7, 31)
            )
            if r["date"] == "2026-07-05"
        )
        assert Decimal(row["fixed_cost"]) == Decimal("1000")  # 31000 / 31일

        # 모든 축이 VAT 포함이므로 순이익 = (매출 − 비용전부) / 1.1 이어야 한다.
        # 고정비를 매입세액에서 빠뜨리면 이 항등식이 깨진다(순이익이 90.9원 낮게 나온다).
        gross = (
            Decimal(row["revenue"]) - Decimal(row["cost"]) - Decimal(row["commission"])
            - Decimal(row["shipping"]) - Decimal(row["ad_spend"]) - Decimal(row["fixed_cost"])
        )
        assert abs(Decimal(row["net_profit"]) - gross / Decimal("1.1")) < Decimal("0.01")

    def test_채널_요약에도_같은_금액이_반영된다(self, db_session):
        ch = _naver_channel(db_session)
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("31000"))
        db_session.add(Order(
            channel_id=ch.id, order_number="N1", platform_product_id="NV1",
            selling_price=Decimal("10000"), quantity=1,
            order_date=datetime(2026, 7, 5, 10, 0),
            status="delivered", commission_amount=Decimal("550"),
        ))
        db_session.commit()

        summary = calculate_channel_summary(
            db_session, None, date(2026, 7, 1), date(2026, 7, 31)
        )
        row = next(r for r in summary if r["channel_id"] == ch.id)
        assert Decimal(row["fixed_cost"]) == Decimal("31000")


class TestUpsertGuards:
    def test_같은_채널_월_항목은_덮어쓴다(self, db_session):
        ch = _naver_channel(db_session)
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("1"))
        upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("43549"))
        rows = db_session.query(MonthlyFixedCost).all()
        assert len(rows) == 1
        assert Decimal(str(rows[0].amount)) == Decimal("43549")

    def test_정해진_항목명만_받는다(self, db_session):
        ch = _naver_channel(db_session)
        with pytest.raises(ValueError):
            upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료 ", Decimal("1"))
        assert "보관료" in FIXED_COST_ITEMS

    def test_월_형식이_틀리면_거부한다(self, db_session):
        ch = _naver_channel(db_session)
        for bad in ("2026-7", "2026-13", "202607", ""):
            with pytest.raises(ValueError):
                upsert_monthly_fixed_cost(db_session, ch.id, bad, "보관료", Decimal("1"))

    def test_음수는_거부한다(self, db_session):
        ch = _naver_channel(db_session)
        with pytest.raises(ValueError):
            upsert_monthly_fixed_cost(db_session, ch.id, "2026-07", "보관료", Decimal("-1"))
