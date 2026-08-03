# test_rg_fee_audit.py — Harness build_fee_audit 통합 테스트 (트랙 RG-Fee S8, D-17)
# in-memory SQLite에 옵션·정산비용·주문수량을 심어 SA 가로지르는 감사 흐름·집계 검증.
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    CoupangProductItem,
    CoupangRgOrderItem,
    CoupangRgSettlementFee,
)
from app.services.coupang.rg_fee_audit import build_fee_audit


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_item(db, vii, w, l, h, wt, name="옵션"):
    db.add(CoupangProductItem(
        vendor_item_id=vii, account_key="COUPANG_WING1", vendor_id="A01564720",
        seller_product_id="P1", seller_product_name="상품", item_name=name,
        sale_price=Decimal(10000), width_mm=w, length_mm=l, height_mm=h, weight_g=wt,
    ))


def _add_fee(db, vii, ftype, amount, dfrom=date(2026, 4, 1), dto=date(2026, 4, 5)):
    db.add(CoupangRgSettlementFee(
        account_key="COUPANG_WING1", vendor_item_id=vii, fee_type=ftype,
        amount=Decimal(amount), recognition_date_from=dfrom, recognition_date_to=dto,
    ))


def _add_order(db, vii, qty, oid="O1", paid=datetime(2026, 4, 2)):
    db.add(CoupangRgOrderItem(
        order_id=oid, vendor_item_id=vii, account_key="COUPANG_WING1",
        vendor_id="A01564720", sales_quantity=qty, paid_at=paid,
    ))


def test_normal_option_no_flag(db):
    # 극소형 폰케이스, 단가 배송 1900/입출고 1100, qty 1 → 정상
    _add_item(db, "100", 227, 126, 20, 137)
    _add_fee(db, "100", "delivery", 1900)
    _add_fee(db, "100", "warehousing", 1100)
    _add_order(db, "100", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["total_options"] == 1
    assert out["summary"]["flagged"] == 0
    it = out["items"][0]
    assert it["size_type"] == "극소형"
    assert it["per_unit_delivery"] == 1900


def test_size_mismatch_high_flagged(db):
    # 극소형 치수인데 배송비 19,750(qty 1) → size_mismatch_high
    _add_item(db, "200", 227, 126, 20, 137, name="아이폰17프로")
    _add_fee(db, "200", "delivery", 19750)
    _add_fee(db, "200", "warehousing", 1100)
    _add_order(db, "200", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["size_mismatch_high"] == 1
    # 플래그 항목이 최상단 정렬
    assert "size_mismatch_high" in out["items"][0]["flags"]


def test_unit_unknown_when_no_orders(db):
    # 정산비용은 있는데 주문수량 데이터 없음 → unit_unknown (과오청구 단정 금지)
    _add_item(db, "300", 227, 126, 20, 137)
    _add_fee(db, "300", "delivery", 1900)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert "unit_unknown" in it["flags"]
    assert it["per_unit_delivery"] is None


def test_missing_dims_flagged(db):
    # 치수 없는 옵션(정산비용만) → missing_dims
    _add_fee(db, "400", "delivery", 1900)
    _add_order(db, "400", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert "missing_dims" in it["flags"]
    assert it["size_type"] is None


def test_account_grain_rows_excluded(db):
    # 계정 단위 row(vendor_item_id='')는 감사 제외 — 옵션 단위만
    _add_item(db, "500", 227, 126, 20, 137)
    _add_fee(db, "500", "delivery", 1900)
    _add_fee(db, "", "delivery", 100000)  # 계정 합계 row — 제외돼야
    _add_order(db, "500", 1)
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["total_options"] == 1
    assert all(it["vendor_item_id"] != "" for it in out["items"])


def test_date_range_overlap_includes_boundary_period(db):
    # codex P2-2 회귀: 정산주기(04-01~04-05)가 조회범위(04-02~04-10)에 일부 걸침 → 포함돼야.
    _add_item(db, "700", 227, 126, 20, 137)
    _add_fee(db, "700", "delivery", 1900, dfrom=date(2026, 4, 1), dto=date(2026, 4, 5))
    _add_order(db, "700", 1, paid=datetime(2026, 4, 3))
    db.commit()
    out = build_fee_audit(db, "COUPANG_WING1", date_from=date(2026, 4, 2), date_to=date(2026, 4, 10))
    # 포함관계 필터였다면 from(04-01)<date_from(04-02)이라 드롭됐을 것 → overlap이라 포함.
    assert out["summary"]["total_options"] == 1
    assert out["items"][0]["charged_delivery"] == 1900


def test_net_profit_untouched_readonly(db):
    # D-17: 읽기 전용. 감사 후 정산비용 row 금액 불변.
    _add_item(db, "600", 227, 126, 20, 137)
    _add_fee(db, "600", "delivery", 1900)
    _add_order(db, "600", 1)
    db.commit()
    build_fee_audit(db, "COUPANG_WING1")
    row = db.query(CoupangRgSettlementFee).filter_by(vendor_item_id="600").first()
    assert row.amount == Decimal(1900)


# ── 주기별 판정 + 커버리지 강등 (2026-08-03, ⓒ+ⓑ) ─────────────────────────────
# 라이브 오탐 실사고를 DB 수준에서 재현해 잠근다. 옵션 91313543029(WING2 아이패드미니 필름):
# 정산주기 4개 × 배송 2,025 / 입출고 1,175인데 RG 주문 수집이 2026-06-03에 시작해 4월 주문이
# DB에 없었다 → 종전 코드는 8,100 ÷ 2주문 = 4,050(대형1 정합·극소형 최소의 3배)으로 판정해
# measured_vs_billed_mismatch를 최상단에 올렸다. 4건 전부 같은 원인이었다.

def _add_size(db, vii, size_type):
    from app.models import CoupangProductSize
    db.add(CoupangProductSize(vendor_item_id=vii, size_type=size_type))


def test_live_false_positive_regression_ipad_film(db):
    """★핵심 회귀: 주문 미수집 주기가 분자에 섞여 단가를 부풀리지 않는다."""
    _add_item(db, "91313543029", 355, 245, 5, 169, name="단일상품")
    _add_size(db, "91313543029", "극소형")
    for df, dt in [((2026, 4, 13), (2026, 4, 19)), ((2026, 4, 20), (2026, 4, 26)),
                   ((2026, 5, 11), (2026, 5, 17)), ((2026, 5, 18), (2026, 5, 24))]:
        _add_fee(db, "91313543029", "delivery", 2025, dfrom=date(*df), dto=date(*dt))
        _add_fee(db, "91313543029", "warehousing", 1175, dfrom=date(*df), dto=date(*dt))
    # 주문은 5월 두 주기에만 존재(4월분은 수집 전이라 DB에 없음 — 실제 prod 상태)
    _add_order(db, "91313543029", 1, oid="O-0514", paid=datetime(2026, 5, 14, 23, 37))
    _add_order(db, "91313543029", 1, oid="O-0518", paid=datetime(2026, 5, 18, 22, 46))
    db.commit()

    it = build_fee_audit(db, "COUPANG_WING1")["items"][0]
    assert it["size_type"] == "극소형" and it["size_source"] == "coupang_measured"
    # 청구 총액은 그대로 보이되(금액을 숨기지 않는다) 단가는 주기 단가여야 한다.
    assert it["charged_delivery"] == 2025 * 4
    assert it["judged_delivery"] == 2025 * 2
    assert it["per_unit_delivery"] == 2025
    assert it["per_unit_warehousing"] == 1175
    assert it["per_unit_delivery"] != 4050  # 종전 오탐 산술이 되살아나면 여기서 깨진다
    assert it["implied_size_delivery"] == "소형"  # 대형1 아님
    assert it["flags"] == []
    assert (it["periods_total"], it["periods_judged"], it["periods_unmatched"]) == (4, 2, 2)


def test_coverage_none_downgrades_when_paid_at_falls_outside_every_period(db):
    """ⓑ: 주문이 주기 밖(매출인식일↔결제일 basis 차이)이면 판정 보류 — 오탐도 오해도 안 만든다.

    라이브 94156664772: 주기 05-11~17인데 결제일 05-10 → 어느 주기에도 안 들어간다.
    """
    _add_item(db, "94156664772", 227, 126, 20, 137)
    _add_size(db, "94156664772", "극소형")
    _add_fee(db, "94156664772", "delivery", 1900, dfrom=date(2026, 4, 13), dto=date(2026, 4, 19))
    _add_fee(db, "94156664772", "delivery", 1900, dfrom=date(2026, 5, 11), dto=date(2026, 5, 17))
    _add_order(db, "94156664772", 1, oid="O-0510", paid=datetime(2026, 5, 10, 12, 0))
    db.commit()

    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert it["flags"] == ["unit_unknown"]
    assert it["per_unit_delivery"] is None
    assert out["summary"]["measured_vs_billed_mismatch"] == 0
    assert out["summary"]["coverage_none"] == 1


def test_coverage_partial_counted_but_not_flagged_as_anomaly(db):
    """커버리지 사실은 summary/필드로 표면화하고 flags(=이상 신호)는 오염시키지 않는다."""
    _add_item(db, "800", 227, 126, 20, 137)
    _add_fee(db, "800", "delivery", 1900, dfrom=date(2026, 4, 13), dto=date(2026, 4, 19))
    _add_fee(db, "800", "delivery", 1900, dfrom=date(2026, 5, 11), dto=date(2026, 5, 17))
    _add_order(db, "800", 1, oid="O-0512", paid=datetime(2026, 5, 12))
    db.commit()

    out = build_fee_audit(db, "COUPANG_WING1")
    assert out["summary"]["coverage_partial"] == 1
    assert out["summary"]["flagged"] == 0          # 커버리지는 이상치가 아니다
    assert out["items"][0]["flags"] == []
    assert out["items"][0]["periods_unmatched"] == 1


def test_real_anomaly_still_flagged_per_period(db):
    """강등이 진짜 신호를 죽이지 않는다 — 판정된 주기의 이상치는 그대로 올라온다."""
    _add_item(db, "900", 227, 126, 20, 137)
    _add_size(db, "900", "극소형")
    _add_fee(db, "900", "delivery", 1900, dfrom=date(2026, 5, 4), dto=date(2026, 5, 10))
    _add_fee(db, "900", "delivery", 19750, dfrom=date(2026, 5, 11), dto=date(2026, 5, 17))
    _add_order(db, "900", 1, oid="O-a", paid=datetime(2026, 5, 5))
    _add_order(db, "900", 1, oid="O-b", paid=datetime(2026, 5, 12))
    db.commit()

    out = build_fee_audit(db, "COUPANG_WING1")
    it = out["items"][0]
    assert "measured_vs_billed_mismatch" in it["flags"]
    assert out["summary"]["measured_vs_billed_mismatch"] == 1
    bad = [p for p in it["period_detail"] if p["flags"]]
    assert len(bad) == 1 and bad[0]["date_from"] == "2026-05-11"


def test_boundary_period_orders_not_re_cut_by_query_window(db):
    """조회범위는 주기를 고르고, 주기가 주문을 고른다 — 범위로 주문을 또 자르면 경계에서 재발.

    주기 04-01~04-05가 조회범위 04-02~04-10에 걸쳐 포함되면, 04-01 결제 주문도 분모에 들어야
    한다. 종전처럼 주문을 범위로 다시 자르면 분자는 주기 전체·분모는 1건이라 단가가 2배 된다.
    """
    _add_item(db, "700", 227, 126, 20, 137)
    _add_fee(db, "700", "delivery", 3800, dfrom=date(2026, 4, 1), dto=date(2026, 4, 5))
    _add_order(db, "700", 1, oid="O-in", paid=datetime(2026, 4, 3))
    _add_order(db, "700", 1, oid="O-edge", paid=datetime(2026, 4, 1))  # 범위 밖·주기 안
    db.commit()

    it = build_fee_audit(db, "COUPANG_WING1",
                         date_from=date(2026, 4, 2), date_to=date(2026, 4, 10))["items"][0]
    assert it["order_count"] == 2
    assert it["per_unit_delivery"] == 1900
    assert it["flags"] == []
