"""S3 — 채널 통합 판매 시계열 회귀 (계약 §4 **S3** · 체인 `발주예측` n=6).

## 무엇을 재는가

계약 원문이 요구하는 셋을 그대로 잠근다:

1. **채널 통합 SKU별 판매수량 시계열** — 5축(네이버·cafe24·Wing 3P·RG 2P·로켓 1P)이 하나의
   SKU 축(`product_master.internal_sku`)으로 합쳐지되 **채널별 내역이 갈라져** 남는다.
2. **채널별 매핑률** — 수량 기준. 분모가 0이면 «0%»가 아니라 **`None`(잴 수 없음)**이다.
3. **결손일 구분** — 근거가 «있는» 채널만 `collected_zero`/`no_data`를 가른다. 근거가 없는
   채널에서 그걸 채우면 **없는 근거를 지어낸 것**이므로 그 자리가 비어 있어야 한다.

## 그리고 이 파일이 특별히 지키는 것 둘

- **다리를 틀리면 예외가 아니라 «0»이 나온다.** 쿠팡 3P의 다리는 `vendor_item_id`인데
  `product_id`(쿠팡 플랫폼 상품 ID)로 조인하면 매핑률이 조용히 0%가 된다 — 이 세션의 첫 조사가
  실제로 그 함정에 빠졌다. `test_wing_bridge_is_vendor_item_not_product_id`가 그 자리를 잠근다.
- **취소·반품을 조용히 빼지 않는다.** 뺀 몫이 `quantity_excluded`로 남아야 한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    CoupangRgOrderItem,
    CoupangRocketSalesDaily,
    CoupangVendorItemSalesDaily,
    Order,
    ProductChannelMapping,
    ProductMaster,
    RocketProductCostMap,
    SyncLog,
)
from app.services.otao_po.sales import build_sales_timeseries

TODAY = date(2026, 8, 26)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정(autoflush=False) — 다르면 「방금 만든 행이 안 보이는」 결함을 못 잡는다.
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Testing() as s:
        yield s


def _master(session, sku="OHI-0001", name="지문방지 필름, 아이폰16"):
    pm = ProductMaster(internal_sku=sku, product_name=name, cost_price=Decimal("1000"))
    session.add(pm)
    session.flush()
    return pm


def _order(session, *, channel_id, product_id, d, qty=1, status="delivered"):
    session.add(
        Order(
            channel_id=channel_id,
            product_id=product_id,
            order_number=f"O-{channel_id}-{d}-{qty}-{status}",
            quantity=qty,
            selling_price=Decimal("10000"),
            order_date=datetime(d.year, d.month, d.day, 10, 0, 0),
            status=status,
        )
    )


def _sync_run(session, *, channel_id, f, t, status="success"):
    session.add(
        SyncLog(
            channel_id=channel_id,
            sync_type="orders",
            status=status,
            date_from=datetime(f.year, f.month, f.day),
            date_to=datetime(t.year, t.month, t.day),
            started_at=datetime(t.year, t.month, t.day, 23, 0, 0),
        )
    )


def _ch(ts, key):
    return next(c for c in ts.channels if c.key == key)


# ── ① 시계열이 채널을 합치되 갈라 놓는가 ────────────────────────────────────


def test_channels_are_summed_into_one_sku_axis_but_stay_separable(session):
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=3)
    _order(session, channel_id=7, product_id=pm.id, d=TODAY, qty=2)
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    (row,) = ts.rows
    assert row["internal_sku"] == "OHI-0001"
    assert row["product_name"] == "지문방지 필름, 아이폰16"
    # 합쳐진다
    assert row["total"] == 5
    # 그런데 갈라져 있다 — 합산 단일 숫자가 채널 내역을 덮지 않는다
    assert row["by_channel"] == {"naver": 3, "cafe24": 2}
    today_cell = next(d for d in ts.daily if d["date"] == TODAY.isoformat())
    assert today_cell["by_channel"] == {"naver": 3, "cafe24": 2}
    assert today_cell["total"] == 5


def test_all_five_axes_reach_the_same_sku(session):
    """5축이 전부 같은 SKU 축에 닿는다 — 하나라도 빠지면 그 채널 수요가 통째로 사라진다."""
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=1)
    _order(session, channel_id=7, product_id=pm.id, d=TODAY, qty=1)
    session.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="V-1"))
    session.add(
        CoupangVendorItemSalesDaily(
            sale_date=TODAY, account_key="COUPANG_WING1", vendor_item_id="V-1",
            registration_type="ROCKET_GROWTH", product_id="8314657485", units_sold=1,
        )
    )
    session.add(
        CoupangRgOrderItem(
            order_id="R-1", vendor_item_id="V-1", account_key="COUPANG_WING1",
            vendor_id="A0", sales_quantity=1,
            paid_at=datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0),
        )
    )
    session.add(RocketProductCostMap(product_number="SKU-1", internal_sku="OHI-0001", status="ok", match_method="manual", note=""))
    session.add(
        CoupangRocketSalesDaily(
            vendor_id="A0", option_id="OPT-1", sku_id="SKU-1", date=TODAY, qty=1
        )
    )
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    (row,) = ts.rows
    assert row["total"] == 5
    assert set(row["by_channel"]) == {
        "naver", "cafe24", "wing3p_ofix", "rg2p_ofix", "rocket1p",
    }


# ── ② 매핑률 ────────────────────────────────────────────────────────────────


def test_mapping_rate_is_quantity_weighted_and_unmapped_is_kept(session):
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=9)
    # product_id 없음 = 못 붙은 판매. 조용히 빠지면 수요가 그만큼 사라진다(§2-9).
    _order(session, channel_id=6, product_id=None, d=TODAY, qty=1)
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    naver = _ch(ts, "naver")
    assert (naver.quantity, naver.quantity_mapped) == (10, 9)
    assert naver.mapping_rate == 90.0
    assert ts.unmapped == {"naver": 1}
    assert any("못 붙은 판매 1개" in n for n in ts.notes)


def test_mapping_rate_is_none_not_zero_when_there_is_nothing_to_measure(session):
    """분모 0에서 «0%»라고 쓰면 「전부 실패」로 읽힌다 — 그건 잴 수 없다는 뜻이다."""
    _master(session)
    session.flush()
    ts = build_sales_timeseries(session, days=7, today=TODAY)
    assert _ch(ts, "naver").mapping_rate is None


def test_cancelled_and_returned_are_excluded_but_not_silently(session):
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=5)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=2, status="cancelled")
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=1, status="returned")
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    naver = _ch(ts, "naver")
    assert naver.quantity == 5  # 수요는 5
    assert naver.quantity_excluded == 3  # 뺀 몫이 남아 있다
    assert ts.rows[0]["total"] == 5


# ── ③ 결손일 구분 — 근거가 있는 채널만 ──────────────────────────────────────


def test_missing_days_are_split_only_where_evidence_exists(session):
    """수집 성공 run이 덮은 날의 빈칸은 「판매 0」, 안 덮은 날은 「데이터 없음」."""
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=1)
    # 최근 3일 중 어제까지만 수집 run이 덮었다 → 그저께는 「수집됨·판매 0」
    _sync_run(session, channel_id=6, f=TODAY - timedelta(days=1), t=TODAY)
    session.flush()

    ts = build_sales_timeseries(session, days=3, today=TODAY)
    naver = _ch(ts, "naver")
    assert naver.missing_day_evidence is True
    assert naver.days_collected_zero == [(TODAY - timedelta(days=1)).isoformat()]
    assert naver.days_no_data == [(TODAY - timedelta(days=2)).isoformat()]


def test_channels_without_collection_log_do_not_invent_the_distinction(session):
    """★근거가 없는 채널에서 결손을 가르면 «없는 근거»를 지어낸 것이다 — 비어 있어야 한다."""
    ts = build_sales_timeseries(session, days=5, today=TODAY)
    for key in ("wing3p_ofix", "wing3p_ohitech", "rg2p_ofix", "rg2p_ohitech", "rocket1p"):
        c = _ch(ts, key)
        assert c.missing_day_evidence is False
        assert c.days_collected_zero == []
        assert c.days_no_data == []
    # 그리고 화면이 그 사실을 말한다
    assert any("구분할 근거가 **없는** 채널" in n for n in ts.notes)


def test_rolling_window_runs_are_read_as_coverage_not_as_run_dates(session):
    """`sync_log`는 run 1건이 창을 덮는다 — 「그날 run이 돌았나」로 세면 롤링 재조회가 안 보인다."""
    _master(session)
    # run은 오늘 하루만 돌았지만 5일 창을 덮었다
    _sync_run(session, channel_id=7, f=TODAY - timedelta(days=4), t=TODAY)
    session.flush()

    ts = build_sales_timeseries(session, days=5, today=TODAY)
    cafe24 = _ch(ts, "cafe24")
    assert cafe24.days_no_data == []  # 전부 덮였다
    assert len(cafe24.days_collected_zero) == 5


def test_failed_runs_do_not_count_as_coverage(session):
    _master(session)
    _sync_run(session, channel_id=7, f=TODAY - timedelta(days=2), t=TODAY, status="error")
    session.flush()
    ts = build_sales_timeseries(session, days=3, today=TODAY)
    assert _ch(ts, "cafe24").days_collected_zero == []
    assert len(_ch(ts, "cafe24").days_no_data) == 3


# ── ④ 다리를 틀리면 예외가 아니라 «0»이 나온다 ──────────────────────────────


def test_wing_bridge_is_vendor_item_not_product_id(session):
    """★`coupang_vendor_item_sales_daily.product_id`는 **쿠팡 플랫폼 상품 ID**지
    `product_master.id`가 아니다. 그걸로 조인하면 조용히 0%가 된다."""
    pm = _master(session)
    session.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="V-9"))
    session.add(
        CoupangVendorItemSalesDaily(
            sale_date=TODAY, account_key="COUPANG_WING1", vendor_item_id="V-9",
            registration_type="ROCKET_GROWTH",
            # ★`product_id`는 일부러 «다른 체계»의 값이다. 이걸 다리로 쓰면 안 붙는다.
            product_id="8314657485", units_sold=7,
        )
    )
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    wing = _ch(ts, "wing3p_ofix")
    assert wing.quantity == 7
    assert wing.quantity_mapped == 7, "vendor_item_id로 붙어야 한다 — 0이면 다리가 틀린 것"
    assert wing.mapping_rate == 100.0


def test_accounts_are_kept_apart(session):
    """오픽스와 오하이테크는 **다른 법인**이다 — 한 칸으로 합치면 손익 축이 깨진다."""
    pm = _master(session)
    for acct, key, qty in (("COUPANG_WING1", "wing3p_ofix", 4), ("COUPANG_WING2", "wing3p_ohitech", 6)):
        session.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id=f"V-{acct}"))
        session.add(
            CoupangVendorItemSalesDaily(
                sale_date=TODAY, account_key=acct, vendor_item_id=f"V-{acct}",
                registration_type="ROCKET_GROWTH", product_id="1", units_sold=qty,
            )
        )
    session.flush()
    ts = build_sales_timeseries(session, days=7, today=TODAY)
    assert _ch(ts, "wing3p_ofix").quantity == 4
    assert _ch(ts, "wing3p_ohitech").quantity == 6
    assert ts.rows[0]["by_channel"] == {"wing3p_ofix": 4, "wing3p_ohitech": 6}


# ── ⑤ 발주 축과의 다리 상태를 화면이 자백하는가 ─────────────────────────────


def test_order_axis_bridge_is_confessed_when_absent(session):
    """★이 자백이 없으면 판매 숫자가 예약 잔량 옆에 놓여 «거짓 대비»가 만들어진다."""
    _master(session)
    session.flush()
    ts = build_sales_timeseries(session, days=7, today=TODAY)
    assert ts.order_axis["overlap"] == 0
    assert ts.order_axis["sales_axis_skus"] == 1
    assert any("다리가 아직 없다" in n for n in ts.notes)


# ── ⑥ 적대 리뷰 P1-1 — 중복 매핑이 판매를 «곱하면» 안 된다 ──────────────────
#
# `product_channel_mapping.channel_product_id`엔 unique 제약이 없고 prod에 중복 55키·121행이
# 실재한다(한 키가 서로 다른 상품 5개를 가리키는 경우까지). 초판은 outerjoin으로 풀어서
# 같은 수량을 N번 더했다 — Wing 3P 오픽스가 원장 1,980 → 화면 2,099(+6.0%)로 부풀었고
# 그 몫이 **팔린 적 없는 SKU에도 배분**됐다. 기존 픽스처는 키당 1행이라 0건이 잡았다.


def test_duplicate_mapping_rows_to_the_same_product_do_not_multiply(session):
    """같은 상품을 가리키는 **중복 행**은 안전하게 접힌다 — 수량이 두 배가 되면 안 된다."""
    pm = _master(session)
    for _ in range(3):  # 같은 (키 → 같은 상품) 매핑이 3행
        session.add(ProductChannelMapping(product_id=pm.id, channel_id=1, channel_product_id="V-1"))
    session.add(
        CoupangVendorItemSalesDaily(
            sale_date=TODAY, account_key="COUPANG_WING1", vendor_item_id="V-1",
            registration_type="ROCKET_GROWTH", product_id="1", units_sold=10,
        )
    )
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    wing = _ch(ts, "wing3p_ofix")
    assert wing.quantity == 10, "원장 수량 그대로여야 한다"
    assert wing.quantity_mapped == 10
    assert ts.rows[0]["total"] == 10, "중복 매핑 행 수만큼 곱해지면 안 된다"
    assert len(ts.rows) == 1


def test_one_channel_id_pointing_at_several_products_is_left_ambiguous(session):
    """★서로 다른 상품을 가리키면 **고르지 않는다.** 다수결도 안 된다 — 발주 오염이다."""
    a = _master(session, sku="OHI-0001")
    b = _master(session, sku="OHI-0002", name="다른 상품")
    session.add(ProductChannelMapping(product_id=a.id, channel_id=1, channel_product_id="V-9"))
    session.add(ProductChannelMapping(product_id=b.id, channel_id=1, channel_product_id="V-9"))
    session.add(
        CoupangVendorItemSalesDaily(
            sale_date=TODAY, account_key="COUPANG_WING1", vendor_item_id="V-9",
            registration_type="ROCKET_GROWTH", product_id="1", units_sold=10,
        )
    )
    session.flush()

    ts = build_sales_timeseries(session, days=7, today=TODAY)
    wing = _ch(ts, "wing3p_ofix")
    assert wing.quantity == 10
    assert wing.quantity_mapped == 0, "모호한 것을 붙이면 안 된다"
    assert wing.quantity_ambiguous == 10
    # 팔린 적 없는 SKU에 배분되지 않는다
    assert ts.rows == []
    assert ts.unmapped == {"wing3p_ofix": 10}
    assert any("서로 다른 상품 여러 개" in n for n in ts.notes)


def test_rg_uses_the_same_ambiguity_rule(session):
    a = _master(session, sku="OHI-0001")
    b = _master(session, sku="OHI-0002", name="다른 상품")
    session.add(ProductChannelMapping(product_id=a.id, channel_id=3, channel_product_id="R-9"))
    session.add(ProductChannelMapping(product_id=b.id, channel_id=3, channel_product_id="R-9"))
    session.add(
        CoupangRgOrderItem(
            order_id="R-1", vendor_item_id="R-9", account_key="COUPANG_WING1",
            vendor_id="A0", sales_quantity=4,
            paid_at=datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0),
        )
    )
    session.flush()
    ts = build_sales_timeseries(session, days=7, today=TODAY)
    assert _ch(ts, "rg2p_ofix").quantity_ambiguous == 4
    assert ts.rows == []


# ── ⑦ 적대 리뷰 P1-2 — 「시계열」이 실제로 시계열인가 ────────────────────────


def test_rows_carry_a_per_day_series_aligned_to_the_date_axis(session):
    """★S3 원문의 첫 요구가 «시계열»이다. SKU×창합계만으로는 그것이 아니다."""
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=3)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY - timedelta(days=2), qty=5)
    session.flush()

    ts = build_sales_timeseries(session, days=3, today=TODAY)
    assert ts.dates == [
        (TODAY - timedelta(days=2)).isoformat(),
        (TODAY - timedelta(days=1)).isoformat(),
        TODAY.isoformat(),
    ]
    (row,) = ts.rows
    # 배열이 날짜 축과 «자리로» 대응한다 — 합계 8이 아니라 [5, 0, 3]이어야 한다
    assert row["series"] == [5, 0, 3]
    assert sum(row["series"]) == row["total"] == 8


def test_series_sums_channels_per_day(session):
    pm = _master(session)
    _order(session, channel_id=6, product_id=pm.id, d=TODAY, qty=2)
    _order(session, channel_id=7, product_id=pm.id, d=TODAY, qty=3)
    session.flush()
    ts = build_sales_timeseries(session, days=2, today=TODAY)
    (row,) = ts.rows
    assert row["series"] == [0, 5]
