# test_rocket_promo_fetcher.py — 페처의 쿠팡 원시 → 레코드 계약 매핑 + 창 보정 + 수기 단위 할인액
#   (트랙 coupang-promo-pnl, Phase 1 페처 확장)
#
# fixture는 **2026-07-28 라이브 정찰 실측 응답**이다(추측 스키마 아님):
#   판매분석 POST /retail-insight/api/business-insight/vi-detail-search
#   프로모션 GET  /promotion/promotion-request(목록) · /{requestId}(상세)
#   유효구간 400: {"code":"INVALID_DATE","message":"... viewable period [2026-06-01 ~ 2026-07-27]"}
#
# ★이 파일의 핵심 가치 = **페처 매핑과 백엔드 파서 계약의 왕복 검증**. 둘 사이가 어긋나면
#   (필드명 오타·단위 오해) 아무 예외 없이 0원 테이블이 쌓인다 — 라운드트립 테스트만 잡는다.
from __future__ import annotations

import importlib.util
import os
import sys
import types
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.coupang import rocket_promo as rp
from app.database import Base, get_db
from app.models import CoupangRocketPromotion, CoupangRocketSalesDaily
from app.services.coupang import rocket_promo_sync as sync

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _ensure_playwright_stub() -> None:
    """페처는 playwright를 import한다 — 테스트 환경에 없을 수 있으므로 스텁(브라우저 사용 금지)."""
    if "playwright.sync_api" in sys.modules:
        return
    pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    def _stub_sync_playwright(*_a, **_k):
        raise RuntimeError("playwright stub — 테스트에서 브라우저 사용 금지")

    sync_api.sync_playwright = _stub_sync_playwright
    pkg.sync_api = sync_api
    sys.modules.setdefault("playwright", pkg)
    sys.modules["playwright.sync_api"] = sync_api


@pytest.fixture(scope="module")
def fetcher(tmp_path_factory):
    """tools/rocket_supplier_fetcher.py를 독립 로드(import 시 로그파일을 만들므로 HOME 격리)."""
    _ensure_playwright_stub()
    home = tmp_path_factory.mktemp("home")
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            "_tool_rocket_supplier_fetcher_promo", TOOLS / "rocket_supplier_fetcher.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


# ─── 실측 fixture ───────────────────────────────────────────────
_INVALID_DATE_BODY = (
    '{"code":"INVALID_DATE","message":"[vendorId:A01029796] Date 2025-01-01 is outside '
    'the viewable period [2026-06-01 ~ 2026-07-27]"}'
)

_SALES_PAYLOAD = {
    "vendorItems": [
        {   # 실측 행(2026-07-26, 아이폰17프로 강화유리)
            "vendorItemDetails": {
                "vendorId": "A00010028",          # ★상품의 리테일 vendor — 우리 계정축 아님
                "vendorItemId": 93373791456,
                "itemName": "오하이 풀커버 강화유리 ... 세트, 아이폰 17 Pro, 1세트",
                "productName": "오하이 풀커버 강화유리 휴대폰 액정보호필름 2p + EZ 툴 세트",
                "externalSkuIds": [62178970],     # = 발주 product_number(원가 브리지 키)
                "skuCount": 1,
            },
            "businessInsightsMetricsResponse": {
                "totalOrders": 21.0, "totalUnitsSold": 20.0, "totalGmv": 348000.0,
                "totalUniqueVisitor": 93.0, "totalPageViews": 193.0,
                "pvToOrder": 0.10880829015544041,
            },
        },
        {   # SKU가 여러 개 → 브리지 키를 하나로 못 고른다 → sku_id는 비운다
            "vendorItemDetails": {"vendorItemId": 93247026354, "itemName": "묶음옵션",
                                  "externalSkuIds": [50342949, 50342950]},
            "businessInsightsMetricsResponse": {"totalUnitsSold": 3.0, "totalGmv": 51000.0},
        },
        {   # 그레인 키 없음 → 행 자체를 버린다
            "vendorItemDetails": {"itemName": "옵션ID 없는 행", "externalSkuIds": [1]},
            "businessInsightsMetricsResponse": {"totalUnitsSold": 9.0},
        },
    ],
    "soldVICount": None,
    "paginationDetails": {"pageSize": 20, "pageNumber": 0, "totalResults": 51, "totalPages": 3},
}

_PROMO_ITEM = {   # 실측 Request 687878 (목록·상세 응답 동일)
    "requestId": 687878,
    "title": "아이폰17프로_강화유리, S26울트라_지문",
    "promotionType": "INSTANT_DISCOUNT",
    "discountType": "FIXED_AMOUNT_WITH_QUANTITY",
    "discountBudget": 1000000,
    "supplierFundRate": 100,
    "effectiveDate": "2026-07-24T00:01:00.000+09:00",
    "expiryDate": "2026-07-26T23:59:59.000+09:00",
    "status": "COMPLETE",
    "detailCount": 2,
    "contractId": "2385997",
    "vendorId": "A01029796",
    "createdAt": "2026-07-23T11:30:30.557+09:00",
    "detailStatus": "CONTRACT_SIGNED",
}

_PROMO_PAGE = {"content": [_PROMO_ITEM], "totalPages": 1, "totalElements": 7,
               "last": True, "first": True, "size": 25, "number": 0, "numberOfElements": 7}


# ═══ ① 유효 구간(롤링 창) 자동 보정 ═══
def test_parse_viewable_period_from_real_400_body(fetcher):
    """일수를 하드코딩하지 않는다 — 서버가 알려준 구간을 그대로 읽는다."""
    assert fetcher._parse_viewable_period(_INVALID_DATE_BODY) == (
        date(2026, 6, 1), date(2026, 7, 27))


def test_parse_viewable_period_ignores_other_brackets(fetcher):
    """본문엔 `[vendorId:...]`도 대괄호로 들어 있다 — 날짜~날짜 형태만 잡아야 한다."""
    assert fetcher._parse_viewable_period('{"message":"[vendorId:A01029796] no period here"}') is None
    assert fetcher._parse_viewable_period("") is None
    assert fetcher._parse_viewable_period('{"message":"[2026-13-99 ~ 2026-99-01]"}') is None


def test_sales_window_days_is_rolling_and_includes_today(fetcher):
    """오늘을 포함한다: 마감 시각 가정을 코드에 박지 않고 400 클램프에 맡긴다."""
    cfg = fetcher.load_config()
    cfg["sales_days"] = 3
    cfg["sales_backfill_days"] = 0
    days = fetcher._sales_window_days(cfg, date(2026, 7, 28))
    assert days == [date(2026, 7, 26), date(2026, 7, 27), date(2026, 7, 28)]


def test_sales_backfill_days_widens_window(fetcher):
    cfg = fetcher.load_config()
    cfg["sales_days"] = 3
    cfg["sales_backfill_days"] = 45
    days = fetcher._sales_window_days(cfg, date(2026, 7, 28))
    assert len(days) == 45 and days[-1] == date(2026, 7, 28)


def test_clamp_days_drops_out_of_range(fetcher):
    days = [date(2026, 5, 31), date(2026, 7, 27), date(2026, 7, 28)]
    period = (date(2026, 6, 1), date(2026, 7, 27))
    assert fetcher._clamp_days(days, period) == [date(2026, 7, 27)]
    assert fetcher._clamp_days(days, None) == days       # 아직 모르면 자르지 않는다


def test_sales_page_meta_reads_pagination_details(fetcher):
    meta = fetcher._sales_page_meta(_SALES_PAYLOAD)
    assert meta == {"page_number": 0, "total_pages": 3, "total_results": 51}
    assert fetcher._sales_page_meta({}) == {"page_number": 0, "total_pages": 0, "total_results": 0}


# ═══ ② 판매분석 원시 → 레코드 계약 ═══
def test_sales_records_maps_live_fields(fetcher):
    recs = fetcher._sales_records(_SALES_PAYLOAD, date(2026, 7, 26))
    assert len(recs) == 2                       # 옵션ID 없는 행은 버린다
    r = recs[0]
    assert r["option_id"] == "93373791456"      # vendorItemId
    assert r["date"] == "2026-07-26"
    assert r["sku_id"] == "62178970"            # externalSkuIds[0] = 발주 product_number
    assert r["qty"] == 20.0 and r["revenue"] == 348000.0
    assert r["visitors"] == 93.0
    assert r["conversion_rate"] == pytest.approx(0.10880829015544041)   # 이미 0~1 소수
    assert "아이폰 17 Pro" in r["product_name"]


def test_sales_records_never_drops_qty_revenue_keys(fetcher):
    """키 존재가 '관측 있음'의 신호다 — 값이 없어도 키는 남겨야 배치 경보가 작동한다."""
    payload = {"vendorItems": [{"vendorItemDetails": {"vendorItemId": 1},
                                "businessInsightsMetricsResponse": {}}]}
    rec = fetcher._sales_records(payload, date(2026, 7, 26))[0]
    assert "qty" in rec and "revenue" in rec
    assert rec["qty"] is None and rec["revenue"] is None


def test_sales_records_blanks_sku_when_ambiguous(fetcher):
    """SKU가 여럿이면 비운다 — 아무거나 고르면 영원히 잘못된 원가에 붙는다."""
    rec = next(r for r in fetcher._sales_records(_SALES_PAYLOAD, date(2026, 7, 26))
               if r["option_id"] == "93247026354")
    assert rec["sku_id"] is None


def test_sales_records_survives_garbage_shapes(fetcher):
    assert fetcher._sales_records({}, date(2026, 7, 26)) == []
    assert fetcher._sales_records({"vendorItems": ["nope", None, 3]}, date(2026, 7, 26)) == []


# ═══ ③ 프로모션 원시 → 레코드 계약 ═══
def test_promotion_record_maps_live_fields(fetcher):
    rec = fetcher._promotion_record(_PROMO_ITEM)
    assert rec["request_id"] == "687878"
    assert rec["contract_id"] == "2385997"
    assert rec["promotion_name"] == "아이폰17프로_강화유리, S26울트라_지문"
    assert rec["promotion_type"] == "INSTANT_DISCOUNT"
    assert rec["discount_method"] == "FIXED_AMOUNT_WITH_QUANTITY"
    assert rec["share_ratio"] == 100            # 100 = 전액 셀러 부담
    assert rec["budget_amount"] == 1000000      # ★총예산이지 단위 할인액이 아니다
    assert rec["applied_product_count"] == 2
    assert rec["raw"]["detailStatus"] == "CONTRACT_SIGNED"   # 미매핑 필드도 보존


def test_promotion_record_leaves_absent_fields_none(fetcher):
    """없는 필드를 지어내지 않는다(D-CPP-7): 단위 할인액·정산일은 API에 없다."""
    rec = fetcher._promotion_record(_PROMO_ITEM)
    assert rec["discount_value"] is None
    assert rec["settlement_date"] is None


def test_promotion_record_requires_request_id(fetcher):
    assert fetcher._promotion_record({"title": "그레인 키 없음"}) is None
    assert fetcher._promotion_record("nope") is None


def test_promo_page_meta(fetcher):
    assert fetcher._promo_page_meta(_PROMO_PAGE) == {"total_pages": 1, "number": 0, "last": True}


# ═══ ④ 라운드트립: 페처 매핑 → 백엔드 파서 계약 (어긋나면 조용히 0원 테이블) ═══
def test_roundtrip_sales_records_pass_backend_contract(fetcher):
    stats: dict = {}
    recs = rp.parse_sales_rows(fetcher._sales_records(_SALES_PAYLOAD, date(2026, 7, 26)),
                               stats=stats)
    assert stats["skipped"] == 0 and stats["accepted"] == 2
    assert stats["blank_qty"] == 0 and stats["blank_revenue"] == 0   # 매핑 사고 경보 없음
    r = next(x for x in recs if x["option_id"] == "93373791456")
    assert r["date"] == date(2026, 7, 26)
    assert r["qty"] == 20 and r["revenue"] == Decimal("348000.0")
    assert r["sku_id"] == "62178970"


def test_roundtrip_promotion_keeps_kst_seconds(fetcher):
    """tz(+09:00) ISO가 KST naive로, **초까지** 보존돼야 한다(프로모션 창 조인이 초 단위)."""
    recs = rp.parse_promotion_rows([fetcher._promotion_record(_PROMO_ITEM)])
    r = recs[0]
    assert r["start_at"] == datetime(2026, 7, 24, 0, 1, 0)
    assert r["end_at"] == datetime(2026, 7, 26, 23, 59, 59)
    assert r["requested_at"] == datetime(2026, 7, 23, 11, 30, 30, 557000)
    assert r["share_ratio"] == Decimal("100")
    assert r["budget_amount"] == Decimal("1000000")


# ═══ ⑤ ingest 멱등 (페처가 만든 레코드로) ═══
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_fetcher_rows_ingest_is_idempotent(db, fetcher):
    rows = fetcher._sales_records(_SALES_PAYLOAD, date(2026, 7, 26))
    r1 = sync.ingest_rocket_sales(db, "A01029796", rows)
    r2 = sync.ingest_rocket_sales(db, "A01029796", rows)   # 롤링 재수집 = 매 실행 같은 날 재push
    assert r1["ingested"] == r2["ingested"] == 2
    assert db.query(CoupangRocketSalesDaily).count() == 2
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="93373791456").one()
    assert row.vendor_id == "A01029796" and row.revenue == Decimal("348000.00")


def test_resync_does_not_wipe_manual_unit_discount(db, fetcher):
    """★수기 입력(D-CPP-7)은 재수집에 지워지면 안 된다 — 페처는 그 칸을 쓰지 않는다."""
    rows = [fetcher._promotion_record(_PROMO_ITEM)]
    sync.ingest_rocket_promotions(db, "A01029796", rows)
    row = db.query(CoupangRocketPromotion).one()
    row.unit_discount_amount = Decimal("3000")
    db.commit()
    sync.ingest_rocket_promotions(db, "A01029796", rows)   # 재수집(상태 변화 반영)
    row = db.query(CoupangRocketPromotion).one()
    assert row.unit_discount_amount == Decimal("3000")
    assert row.status == "COMPLETE"


# ═══ ⑥ 수기 단위 할인액 PATCH 라우트 (D-CPP-7) ═══
_TOKEN = "test-token-123"
_PATCH = "/api/coupang/ops/rocket/promotion/687878/unit-discount"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    from app.main import app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    seed.add(CoupangRocketPromotion(request_id="687878", vendor_id="A01029796",
                                    promotion_name="아이폰17프로_강화유리", applied_product_count=2))
    seed.commit()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


def test_patch_unit_discount_requires_token(client):
    c, _ = client
    assert c.patch(_PATCH, json={"unit_discount_amount": 3000}).status_code == 401
    assert c.patch(_PATCH, json={"unit_discount_amount": 3000},
                   headers={"X-Ingest-Token": "wrong"}).status_code == 401


def test_patch_unit_discount_sets_value(client):
    c, s = client
    r = c.patch(_PATCH, headers={"X-Ingest-Token": _TOKEN},
                json={"unit_discount_amount": "3,000"})
    assert r.status_code == 200
    assert r.json()["applied_product_count"] == 2     # 이 값이 여러 상품에 공통 적용됨(D-CPP-7)
    assert s.query(CoupangRocketPromotion).one().unit_discount_amount == Decimal("3000")


def test_patch_unit_discount_null_clears_to_unknown(client):
    """0원 할인과 '모름'은 다르다 — null은 모름으로 되돌린다."""
    c, s = client
    c.patch(_PATCH, headers={"X-Ingest-Token": _TOKEN}, json={"unit_discount_amount": 3000})
    r = c.patch(_PATCH, headers={"X-Ingest-Token": _TOKEN}, json={"unit_discount_amount": None})
    assert r.status_code == 200 and r.json()["unit_discount_amount"] is None
    assert s.query(CoupangRocketPromotion).one().unit_discount_amount is None


def test_patch_unit_discount_rejects_bad_input(client):
    c, _ = client
    h = {"X-Ingest-Token": _TOKEN}
    assert c.patch(_PATCH, headers=h, json={}).status_code == 400                       # 키 없음
    assert c.patch(_PATCH, headers=h, json={"unit_discount_amount": -1}).status_code == 400
    assert c.patch(_PATCH, headers=h, json={"unit_discount_amount": "삼천원"}).status_code == 400
    assert c.patch(_PATCH, headers=h,
                   json={"unit_discount_amount": "1E+999"}).status_code == 400           # 컬럼 상한
    assert c.patch(_PATCH, headers=h, json={"unit_discount_amount": "NaN"}).status_code == 400


def test_patch_unit_discount_unknown_promotion_is_404_not_created(client):
    """행을 지어내지 않는다 — 수집되지 않은 프로모션에 수기값이 앉으면 대사할 원본이 없다."""
    c, s = client
    r = c.patch("/api/coupang/ops/rocket/promotion/999999/unit-discount",
                headers={"X-Ingest-Token": _TOKEN}, json={"unit_discount_amount": 1000})
    assert r.status_code == 404
    assert s.query(CoupangRocketPromotion).count() == 1
