# test_rocket_promo.py — 프로모션 손익 레이어 Phase 1 파서 + ingest (트랙 coupang-promo-pnl)
# 라이브 API 호출 없음. fixture는 **우리 레코드 계약**(PLAN §4) 기준 — 쿠팡 원시 스키마가 아니다
#   (2026-07-28 정찰 미완: supplier 세션 만료 → 원시 스키마 추측 금지).
# 값은 실측 사실 기반: 프로모션 Request 687878(2026-07-24 00:01:00~07-26 23:59:59, 분담 100%,
#   적용상품 2), 쿠폰 94177420 사용금액 156,000, SKU 62178970(=발주 product_number).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.coupang import rocket_promo as rp
from app.database import Base
from app.models import CoupangCoupon, CoupangRocketPromotion, CoupangRocketSalesDaily
from app.services.coupang import rocket_promo_sync as sync


# ─── fixture (레코드 계약) ─────────────────────────────
_SALES_ROWS = [
    {"option_id": "95536607339", "sku_id": "62178970", "date": "2026-07-24",
     "qty": 18, "revenue": "304200", "visitors": 512, "conversion_rate": "0.0352",
     "product_name": "오하이 풀커버 강화유리 아이폰17프로"},
    {"option_id": "95570603512", "sku_id": "69411570", "date": "2026-07-24",
     "qty": 7, "revenue": "111300"},
    # 필수키 누락(option_id 없음) → skip
    {"sku_id": "62178970", "date": "2026-07-24", "qty": 3},
]

_PROMO_ROWS = [
    {"request_id": "687878", "contract_id": "9962", "promotion_name": "17프로 강화유리 할인",
     "promotion_type": "즉시할인", "status": "APPROVED",
     "start_at": "2026-07-24 00:01:00", "end_at": "2026-07-26 23:59:59",
     "share_ratio": "100", "discount_method": "할인액", "discount_value": "2000",
     "budget_amount": "500000", "settlement_date": "2026-09-15",
     "applied_product_count": 2, "requested_at": "2026-07-23 18:20:11",
     "raw": {"requestId": 687878}},
    # 필수키 누락(request_id 없음) → skip
    {"promotion_name": "이름만 있는 행"},
]

_COUPON_ROWS = [
    {"coupon_id": "94177420", "used_amount": "156,000"},
    {"coupon_id": "93654161", "used_amount": 40000},
    {"coupon_id": "99999999", "used_amount": None},   # 값 없음 → skip(0으로 접지 않음)
]


# ═══ 파서 SA(순수) ═══
def test_parse_sales_skips_rows_without_required_keys():
    recs = rp.parse_sales_rows(_SALES_ROWS)
    assert len(recs) == 2
    assert {r["option_id"] for r in recs} == {"95536607339", "95570603512"}


def test_parse_sales_field_types():
    rec = next(r for r in rp.parse_sales_rows(_SALES_ROWS) if r["option_id"] == "95536607339")
    assert rec["date"] == date(2026, 7, 24)
    assert rec["sku_id"] == "62178970"          # = 발주 product_number 브리지 키
    assert rec["qty"] == 18
    assert rec["revenue"] == Decimal("304200")
    assert rec["visitors"] == 512
    assert rec["conversion_rate"] == Decimal("0.0352")
    assert rec["source"] == "sales_analysis"


def test_parse_sales_missing_optional_fields_stay_none_not_zero():
    """'없음'과 '0'을 구분한다 — visitors 미제공을 0으로 접으면 유입 0으로 오독된다."""
    rec = next(r for r in rp.parse_sales_rows(_SALES_ROWS) if r["option_id"] == "95570603512")
    assert rec["visitors"] is None
    assert rec["conversion_rate"] is None
    assert rec["qty"] == 7


def test_parse_sales_last_row_wins_on_duplicate_grain():
    recs = rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": 1},
        {"option_id": "A", "date": "2026-07-24", "qty": 9},
    ])
    assert len(recs) == 1 and recs[0]["qty"] == 9


def test_parse_sales_source_label_propagates():
    recs = rp.parse_sales_rows([{"option_id": "A", "date": "2026-07-24"}], source="excel")
    assert recs[0]["source"] == "excel"


def test_parse_sales_garbage_input():
    assert rp.parse_sales_rows([]) == []
    assert rp.parse_sales_rows(None) == []
    assert rp.parse_sales_rows(["nope", 3, None]) == []


def test_parse_promotion_preserves_seconds():
    """행사기간은 초 단위 — 날짜로 뭉개면 프로모션 창 조인이 하루씩 틀어진다."""
    rec = rp.parse_promotion_rows(_PROMO_ROWS)[0]
    assert rec["start_at"] == datetime(2026, 7, 24, 0, 1, 0)
    assert rec["end_at"] == datetime(2026, 7, 26, 23, 59, 59)


def test_parse_promotion_fields():
    rec = rp.parse_promotion_rows(_PROMO_ROWS)[0]
    assert rec["request_id"] == "687878"
    assert rec["share_ratio"] == Decimal("100")      # 100% = 전액 셀러 부담
    assert rec["discount_value"] == Decimal("2000")
    assert rec["budget_amount"] == Decimal("500000")
    assert rec["settlement_date"] == date(2026, 9, 15)
    assert rec["applied_product_count"] == 2
    assert rec["raw"] == {"requestId": 687878}


def test_parse_promotion_skips_rows_without_request_id():
    assert len(rp.parse_promotion_rows(_PROMO_ROWS)) == 1


def test_parse_promotion_wraps_non_dict_raw():
    rec = rp.parse_promotion_rows([{"request_id": "1", "raw": ["a", "b"]}])[0]
    assert rec["raw"] == {"_raw": ["a", "b"]}


def test_parse_coupon_usage_skips_missing_and_negative():
    recs = rp.parse_coupon_usage_rows(_COUPON_ROWS + [{"coupon_id": "X", "used_amount": -1}])
    assert {r["coupon_id"] for r in recs} == {"94177420", "93654161"}


def test_parse_coupon_usage_strips_comma():
    rec = rp.parse_coupon_usage_rows([{"coupon_id": "94177420", "used_amount": "156,000"}])[0]
    assert rec["used_amount"] == Decimal("156000")


def test_parse_coupon_usage_zero_is_kept():
    """0은 '안 쓴 쿠폰'이라는 사실이므로 skip 대상이 아니다(None만 skip)."""
    recs = rp.parse_coupon_usage_rows([{"coupon_id": "A", "used_amount": 0}])
    assert len(recs) == 1 and recs[0]["used_amount"] == Decimal(0)


# ═══ ingest Harness (인메모리 SQLite) ═══
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_ingest_sales_and_idempotent(db):
    r1 = sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    assert r1["ingested"] == 2 and r1["skipped"] == 1
    assert db.query(CoupangRocketSalesDaily).count() == 2
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95536607339").one()
    assert row.vendor_id == "A01029796"           # 계정축 주입
    assert row.revenue == Decimal("304200")
    # 재수신 멱등: 행 수 불변
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    assert db.query(CoupangRocketSalesDaily).count() == 2


def test_ingest_sales_updates_on_resync(db):
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    sync.ingest_rocket_sales(db, "A01029796", [
        {"option_id": "95536607339", "date": "2026-07-24", "qty": 25, "revenue": "422500"},
    ])
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95536607339").one()
    assert row.qty == 25 and row.revenue == Decimal("422500")   # 확정치 교체


def test_ingest_sales_isolated_per_vendor(db):
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    sync.ingest_rocket_sales(db, "A99999999", _SALES_ROWS)
    assert db.query(CoupangRocketSalesDaily).count() == 4   # 계정축이 grain에 포함


def test_ingest_promotion_and_idempotent(db):
    r = sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    assert r["ingested"] == 1 and r["skipped"] == 1
    row = db.query(CoupangRocketPromotion).filter_by(request_id="687878").one()
    assert row.vendor_id == "A01029796"
    assert row.start_at == datetime(2026, 7, 24, 0, 1, 0)
    assert row.share_ratio == Decimal("100.00")
    sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    assert db.query(CoupangRocketPromotion).count() == 1


def test_ingest_promotion_updates_status_on_resync(db):
    sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    sync.ingest_rocket_promotions(db, "A01029796", [
        {"request_id": "687878", "status": "FINISHED"},
    ])
    row = db.query(CoupangRocketPromotion).filter_by(request_id="687878").one()
    assert row.status == "FINISHED"
    assert row.raw == {"requestId": 687878}   # raw는 미제공 시 기존값 보존


def _seed_coupon(db, coupon_id: str, account_key: str = "COUPANG_WING1"):
    db.add(CoupangCoupon(
        account_key=account_key, vendor_id="A00123456",
        coupon_kind="INSTANT", coupon_id=coupon_id,
    ))
    db.commit()


def test_ingest_coupon_used_amount(db):
    _seed_coupon(db, "94177420")
    _seed_coupon(db, "93654161")
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", _COUPON_ROWS)
    assert r["updated"] == 2 and r["not_found"] == 0 and r["skipped"] == 1
    row = db.query(CoupangCoupon).filter_by(coupon_id="94177420").one()
    assert row.used_amount == Decimal("156000")     # D-CPP-3 권위값
    assert row.used_amount_source == "wing_ui"      # 출처 라벨 필수
    assert row.used_amount_synced_at is not None


def test_ingest_coupon_used_amount_does_not_create_rows(db):
    """없는 쿠폰에 행을 만들지 않는다 — 쿠폰 메타 없는 유령 행 방지(원칙22)."""
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", _COUPON_ROWS)
    assert r["updated"] == 0 and r["not_found"] == 2
    assert db.query(CoupangCoupon).count() == 0
    assert "94177420" in r["not_found_coupon_ids"]


def test_ingest_coupon_used_amount_account_scoped(db):
    """같은 coupon_id라도 다른 계정의 행은 건드리지 않는다."""
    _seed_coupon(db, "94177420", account_key="COUPANG_WING2")
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", [
        {"coupon_id": "94177420", "used_amount": 156000},
    ])
    assert r["updated"] == 0 and r["not_found"] == 1
    assert db.query(CoupangCoupon).filter_by(coupon_id="94177420").one().used_amount is None


def test_ingest_coupon_used_amount_source_label(db):
    _seed_coupon(db, "94177420")
    sync.ingest_coupon_used_amount(
        db, "COUPANG_WING1", [{"coupon_id": "94177420", "used_amount": 1}], source="manual"
    )
    assert db.query(CoupangCoupon).one().used_amount_source == "manual"
