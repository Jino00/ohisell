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
import json
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
    """없는 필드를 지어내지 않는다(D-CPP-7): 단위 할인액·정산일은 API에 없다.

    ★이전 판은 **동어반복**이었다(적대적 리뷰 2R): `_promotion_record`가 두 키를 하드코딩
      `None`으로 넣으므로 fixture가 무엇이든 통과했고, 쿠팡이 그 필드를 실제로 추가해도
      계속 통과했다 — 즉 docstring이 말하는 위험을 원리적으로 못 잡는다.
      그래서 **실측 응답에 그 필드가 정말 없다**는 전제부터 검증한다. 쿠팡이 단위 할인액을
      주기 시작하면 이 테스트가 깨지고, 그때가 D-CPP-7(수기 1칸)을 재검토할 시점이다.
    """
    for absent in ("discountValue", "unitDiscountAmount", "settlementDate", "discountPrice"):
        assert absent not in _PROMO_ITEM, (
            f"실측 응답에 {absent}가 생겼다 — D-CPP-7(수기 입력) 전제가 깨졌으니 재검토할 것"
        )
    rec = fetcher._promotion_record(_PROMO_ITEM)
    assert rec["discount_value"] is None
    assert rec["settlement_date"] is None


def test_promotion_detail_null_does_not_erase_list_value(fetcher):
    """상세의 null이 목록의 정상 값을 지우면 안 된다(적대적 리뷰 2R).

    dict 언패킹은 **키 존재**로 덮으므로 `{**item, **detail}`은 detail의 null까지 이긴다.
    그러면 ingest가 그 None을 컬럼에 그대로 써서(skip도 경보도 없이) 좋은 값이 사라진다.
    """
    detail = {"requestId": 687878, "discountBudget": None, "supplierFundRate": None,
              "status": "CLOSED"}
    merged = {**_PROMO_ITEM, **{k: v for k, v in detail.items() if v is not None}}
    rec = fetcher._promotion_record(merged)
    assert rec["budget_amount"] == 1000000     # 목록 값 보존
    assert rec["share_ratio"] == 100           # 목록 값 보존
    assert rec["status"] == "CLOSED"           # 값이 있는 필드는 상세가 이긴다


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


# ═══════════════════════════════════════════════════════════════════════
# ⑦ 제어 흐름(수집 루프) — 적대적 리뷰 2R/3R로 추가
# ───────────────────────────────────────────────────────────────────────
# ★왜 이 절이 생겼나: 이전 판의 371줄은 **순수 매핑 함수만** 덮고 있었고
#   `_collect_sales_day/_collect_sales_rows/_collect_promotion_rows/_fetch_promotion_detail/
#   _sales_access_ok` 등 실제 판정을 내리는 9개 함수는 참조조차 없었다. 발견된 결함 대부분이
#   정확히 그 층에 있었다(부분 실패 시 전량 유실·실패를 '범위밖'으로 위장·조용한 절단).
#   그래서 스크립트된 가짜 page로 그 층을 직접 돌린다 — 브라우저는 쓰지 않는다.
# ═══════════════════════════════════════════════════════════════════════
_SUB_OK = json.dumps({"data": {"permittedLevel": "BASIC",
                               "detailInfo": {"subscribedLevel": "FREE",
                                              "freeTrialEndDate": "2026.08.20"}}})


class FakePage:
    """`page.evaluate(js, arg)`만 흉내낸다. 응답은 호출자가 함수로 지정한다."""

    def __init__(self, responder, sub_body: str = _SUB_OK):
        self._responder = responder
        self._sub_body = sub_body
        self.calls: list = []

    def wait_for_timeout(self, ms):    # 폴라이트 간격 — 테스트에선 즉시
        pass

    def evaluate(self, js, arg):
        path = arg[0]
        if path == "/rpd/v2/supplier/subscription/detail":
            return {"status": 200, "body": self._sub_body}
        self.calls.append(arg)
        return self._responder(arg)


def _sales_ok_body(day: str, *, items=1, total_pages=1, page_number=0, total_results=None):
    vis = [{"vendorItemDetails": {"vendorItemId": f"V{day}-{i}", "externalSkuIds": ["S1"],
                                  "itemName": "opt"},
            "businessInsightsMetricsResponse": {"totalUnitsSold": 2, "totalGmv": 1000,
                                                "totalUniqueVisitor": 5, "pvToOrder": 0.1}}
           for i in range(items)]
    return json.dumps({"vendorItems": vis,
                       "paginationDetails": {"pageNumber": page_number,
                                             "totalPages": total_pages,
                                             "totalResults": items if total_results is None
                                             else total_results}})


_CFG = {"sales_days": 7, "sales_backfill_days": 0, "sales_page_size": 20,
        "sales_max_pages": 40, "sales_budget_min": 10}


@pytest.fixture
def frozen_today(fetcher, monkeypatch):
    """KST 오늘을 2026-07-28로 고정(창 계산이 실행일에 흔들리지 않게)."""
    import datetime as _dt

    class _DT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 7, 28, 15, 0, tzinfo=tz)

    monkeypatch.setattr(fetcher, "datetime", _DT)
    return date(2026, 7, 28)


def test_one_bad_day_does_not_discard_the_other_days(fetcher, frozen_today):
    """★한 날의 500이 이미 수집한 앞날들을 버리면 안 된다(적대적 리뷰 2R).

    이전 판은 예외가 `_collect_sales_rows`를 뚫고 나가 **push 자체가 일어나지 않았다** —
    롤링 7일이면 다음 회차가 메우지만 백필 45일이면 44일째 실패가 43일치를 버렸다.
    """
    bad = "2026-07-24"

    def responder(arg):
        day = arg[1]["startDate"]
        if day == bad:
            return {"status": 500, "body": "boom"}
        return {"status": 200, "body": _sales_ok_body(day)}

    rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_requested"] == 7
    assert stats["days_collected"] == 6          # 나머지 6일은 살아남는다
    assert stats["days_failed"] == 1
    assert stats["failed_dates"] == [bad]
    assert len(rows) == 6                        # ★그리고 push할 행이 남아 있다


def test_failed_day_is_not_laundered_as_out_of_range(fetcher, frozen_today):
    """유효 구간 '안'인데 재시도까지 400 = 실패다. '범위밖'으로 세면 조용한 성공이 된다."""
    bad = "2026-07-24"
    period = "[2026-06-01 ~ 2026-07-28]"          # 7일 전부 유효 구간 안

    def responder(arg):
        day = arg[1]["startDate"]
        if day == bad:
            return {"status": 400, "body": json.dumps(
                {"code": "INVALID_DATE",
                 "message": f"[vendorId:A01029796] Date {day} outside the viewable period {period}"})}
        return {"status": 200, "body": _sales_ok_body(day)}

    rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_failed"] == 1              # 실패로 센다
    assert stats["days_out_of_range"] == 0        # ★'범위밖'이 아니다
    assert stats["failed_dates"] == [bad]
    assert stats["days_collected"] == 6 and len(rows) == 6


def test_genuinely_out_of_range_days_are_clamped_not_failed(fetcher, frozen_today):
    """진짜 구간 밖은 실패가 아니다 — 롤링 창의 정상 동작(오탐이면 rc가 흔들린다)."""
    period = "[2026-07-26 ~ 2026-07-28]"          # 7일 중 앞 4일이 구간 밖

    def responder(arg):
        day = arg[1]["startDate"]
        if day < "2026-07-26":
            return {"status": 400, "body": json.dumps(
                {"code": "INVALID_DATE",
                 "message": f"Date {day} is outside the viewable period {period}"})}
        return {"status": 200, "body": _sales_ok_body(day)}

    rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_out_of_range"] == 4
    assert stats["days_failed"] == 0
    assert stats["days_collected"] == 3
    # ★세 카운터의 합 = 요청 일수. 안 맞으면 어딘가로 하루가 조용히 새고 있다는 뜻이다.
    assert (stats["days_collected"] + stats["days_out_of_range"]
            + stats["days_failed"] + stats["days_abandoned"]) == stats["days_requested"]


def test_all_days_failing_is_systemic_and_raises(fetcher, frozen_today):
    """전 날짜 실패 = 계통 고장 → rc≠0으로 올라가야 한다(부분 실패와 다르다, 3R 규칙)."""
    page = FakePage(lambda arg: {"status": 500, "body": "down"})
    with pytest.raises(RuntimeError, match="전부 실패"):
        fetcher._collect_sales_rows(page, dict(_CFG))


def test_empty_vendoritems_is_access_denied_not_a_quiet_zero(fetcher, frozen_today):
    """★D-CPP-5: 전 날짜가 200인데 vendorItems가 전부 비면 '안 팔린 날'이 아니라 접근 차단."""
    def responder(arg):
        return {"status": 200, "body": json.dumps(
            {"vendorItems": [], "paginationDetails": {"pageNumber": 0, "totalPages": 1,
                                                      "totalResults": 0}})}

    with pytest.raises(fetcher._SalesAccessDenied):
        fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))


def test_items_present_but_zero_records_is_mapping_error_not_access(fetcher, frozen_today):
    """★쿠팡이 vendorItemId를 개명하면 '구독 만료'가 아니라 '매핑 파손'이어야 한다(3R).

    둘을 뭉치면 운영자가 결제를 갱신하며 코드 버그를 쫓는다. 게다가 레코드가 0이라 push가
    없으므로 백엔드의 blank_qty 경보(accepted 분모)도 침묵한다 — 여기가 유일한 탐지 자리다.
    """
    def responder(arg):
        return {"status": 200, "body": json.dumps({
            "vendorItems": [{"vendorItemDetails": {"vendorItemIdRENAMED": 1},
                             "businessInsightsMetricsResponse": {"totalUnitsSold": 5}}],
            "paginationDetails": {"pageNumber": 0, "totalPages": 1, "totalResults": 1}})}

    with pytest.raises(fetcher._SalesMappingError):
        fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))


def test_page_number_echo_mismatch_stops_instead_of_double_counting(fetcher, frozen_today):
    """서버가 페이징을 무시하고 page 0을 계속 주면 같은 행을 N번 센다 — 즉시 끊는다."""
    def responder(arg):
        day = arg[1]["startDate"]
        return {"status": 200, "body": _sales_ok_body(day, total_pages=5, page_number=0)}

    # 매 날짜가 에코 불일치로 끊기고, 전 날짜 실패이므로 계통 고장으로 올라간다.
    with pytest.raises(RuntimeError, match="전부 실패"):
        fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))


def test_total_results_mismatch_is_reported_not_silently_truncated(fetcher, frozen_today):
    """totalResults와 실제 수신량이 다르면 조용한 절단이다 — 그 날을 실패로 올린다."""
    truncated_day = "2026-07-25"

    def responder(arg):
        day = arg[1]["startDate"]
        if day == truncated_day:
            # totalPages=1이라 1페이지에서 끝나는데 서버는 51건이 있다고 말한다
            return {"status": 200, "body": _sales_ok_body(day, items=1, total_pages=1,
                                                          total_results=51)}
        return {"status": 200, "body": _sales_ok_body(day)}

    _rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_failed"] == 1                    # 절단된 하루만 실패
    assert stats["failed_dates"] == [truncated_day]
    assert stats["days_collected"] == 6                 # 나머지는 정상 수집


def test_expired_free_trial_is_blocked_before_collection(fetcher, frozen_today):
    """★게이트가 값을 '읽고 판정'하는지 — 이전 판은 로그만 찍고 무조건 True였다(2R)."""
    expired = json.dumps({"data": {"permittedLevel": "BASIC",
                                   "detailInfo": {"subscribedLevel": "FREE",
                                                  "freeTrialEndDate": "2026.07.01"}}})
    page = FakePage(lambda arg: {"status": 200, "body": _sales_ok_body("x")}, sub_body=expired)
    ok, why = fetcher._sales_access_ok(page, today=date(2026, 7, 28))
    assert ok is False and "무료체험 종료" in why
    with pytest.raises(fetcher._SalesAccessDenied):
        fetcher._collect_sales_rows(page, dict(_CFG))


def test_unknown_permitted_level_is_blocked(fetcher):
    """모르는 등급은 통과시키지 않는다(열어두면 만료가 조용히 지나간다)."""
    body = json.dumps({"data": {"permittedLevel": "NONE", "detailInfo": {}}})
    ok, why = fetcher._sales_access_ok(FakePage(lambda a: None, sub_body=body),
                                       today=date(2026, 7, 28))
    assert ok is False and "NONE" in why


def test_promotion_detail_exception_falls_back_instead_of_losing_everything(fetcher):
    """★상세 1건의 예외가 7건 전부를 날리면 안 된다(적대적 리뷰 2R).

    이전 판은 비200/비JSON만 접고 `_eval_retry`의 재발생 예외는 통과시켜, 4번째에서 흔들리면
    `_collect_promotion_rows`를 뚫고 나가 아무것도 push되지 않았다.
    """
    calls = {"n": 0}

    def responder(arg):
        path = arg[0]
        if path.startswith("/promotion/promotion-request?"):
            return {"status": 200, "body": json.dumps(_PROMO_PAGE)}
        calls["n"] += 1
        raise RuntimeError("Execution context was destroyed")

    rows, stats = fetcher._collect_promotion_rows(
        FakePage(responder), {"promo_page_size": 25, "promo_max_pages": 20, "promo_detail_max": 100})
    assert stats["listed"] == 1
    assert stats["detail_failed"] == 1
    assert len(rows) == 1                       # ★목록 값으로 살아남는다
    assert rows[0]["request_id"] == "687878"


def test_ingest_source_never_assigns_unit_discount_amount():
    """★수기값 보존을 '동작'이 아니라 '소스'로도 못박는다(적대적 리뷰 2R).

    `test_resync_does_not_wipe_manual_unit_discount`는 진짜 동작 테스트지만, 미래의 누군가가
    upsert에 `row.unit_discount_amount = rec[...]` 한 줄을 넣으면 그 테스트도 같이 고쳐질 수
    있다. 페처 경로가 이 칸을 **쓰지 않는다**는 것이 D-CPP-7의 계약이므로 소스에 걸어 둔다.
    """
    src = Path(sync.__file__).read_text(encoding="utf-8")
    assert "unit_discount_amount" not in src, (
        "rocket_promo_sync가 unit_discount_amount를 건드린다 — 수기 입력(D-CPP-7)이 "
        "재수집에 지워진다. 페처 경로는 이 칸을 절대 쓰지 않아야 한다."
    )


def test_empty_today_after_transient_failures_is_not_access_denied(fetcher, frozen_today):
    """★내가 만든 회귀(적대적 리뷰 4R): 못 본 창은 증거가 아니다.

    6일이 일시적 500 + 오늘은 정상 200인데 아직 판매 0(이른 아침) — 완전히 정당한 상태다.
    그런데 창 전체 판정이 `days_failed`를 무시하면 "vendorItems 전부 0 = 접근 차단"으로
    단정하고, 그 결과 rc가 RC_ACCESS_DENIED가 되어 **재시도 0회로 요청이 영구 소멸**한다.
    수정 전 코드보다 나쁜 상태 — 빈 창은 '관측된 빈 창'일 때만 증거다.
    """
    def responder(arg):
        day = arg[1]["startDate"]
        if day != "2026-07-28":
            return {"status": 500, "body": "transient"}
        return {"status": 200, "body": json.dumps(
            {"vendorItems": [], "paginationDetails": {"pageNumber": 0, "totalPages": 1,
                                                      "totalResults": 0}})}

    rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert rows == []
    assert stats["days_failed"] == 6 and stats["days_collected"] == 1
    # ★핵심: 예외가 나지 않는다(= 요청이 소멸되지 않는다)


def test_access_denied_still_fires_when_window_fully_observed(fetcher, frozen_today):
    """반대 방향도 지킨다 — 빠짐없이 관측한 빈 창은 여전히 접근 차단으로 올린다."""
    def responder(arg):
        return {"status": 200, "body": json.dumps(
            {"vendorItems": [], "paginationDetails": {"pageNumber": 0, "totalPages": 1,
                                                      "totalResults": 0}})}

    with pytest.raises(fetcher._SalesAccessDenied):
        fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))


def test_today_total_results_race_keeps_rows_instead_of_failing(fetcher, frozen_today):
    """★당일은 페이지 사이 신규 판매로 totalResults가 정상적으로 어긋난다(4R).

    당일까지 hard-raise하면 장사가 잘 되는 날마다 그날을 버린다 — 경보가 정상을 실패로 만든다.
    과거일은 확정치라 그대로 실패로 올린다.
    """
    def responder(arg):
        day = arg[1]["startDate"]
        # 모든 날: 1건 받았는데 서버는 2건이라고 말한다(당일은 레이스, 과거일은 절단)
        return {"status": 200, "body": _sales_ok_body(day, items=1, total_pages=1,
                                                      total_results=2)}

    _rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_collected"] == 1                  # 오늘(07-28)만 살아남는다
    assert stats["days_failed"] == 6                     # 과거 6일은 절단으로 실패
    assert "2026-07-28" not in stats["failed_dates"]


def test_budget_exhaustion_counts_abandoned_days(fetcher, frozen_today, monkeypatch):
    """예산 초과로 포기한 날도 센다 — 안 세면 '창을 다 봤다'고 착각한다(4R)."""
    import itertools
    # 첫 호출 이후 시계를 예산 밖으로 밀어버린다
    ticks = itertools.chain([0.0, 1.0], itertools.repeat(10_000.0))
    monkeypatch.setattr(fetcher.time, "monotonic", lambda: next(ticks))

    def responder(arg):
        return {"status": 200, "body": _sales_ok_body(arg[1]["startDate"])}

    _rows, stats = fetcher._collect_sales_rows(FakePage(responder), dict(_CFG))
    assert stats["days_abandoned"] > 0
    assert (stats["days_collected"] + stats["days_out_of_range"]
            + stats["days_failed"] + stats["days_abandoned"]) == stats["days_requested"]


def test_empty_today_only_window_is_not_access_denied(fetcher, frozen_today):
    """★분모는 '닫힌 날'이다(5R): 창이 당일 하나뿐이면 빈 결과는 증거가 못 된다.

    sales_days=1이면 창은 당일 하나다. 새벽에 아직 판매가 없으면 vendorItems=0인데,
    이걸 접근 차단으로 단정하면 **아침마다 갱신 요청이 소멸한다**.
    """
    cfg = dict(_CFG, sales_days=1)

    def responder(arg):
        return {"status": 200, "body": json.dumps(
            {"vendorItems": [], "paginationDetails": {"pageNumber": 0, "totalPages": 1,
                                                      "totalResults": 0}})}

    rows, stats = fetcher._collect_sales_rows(FakePage(responder), cfg)
    assert rows == []
    assert stats["days_collected"] == 1 and stats["days_collected_closed"] == 0
    # 예외 없음 = 요청이 소멸되지 않는다


def test_last_run_error_does_not_leak_into_a_later_run(fetcher, monkeypatch):
    """★진단 텍스트는 어떤 조기 return보다 먼저 비운다(5R).

    _do_run에는 조기 return이 여럿이라, 리셋이 아래쪽에 있으면 **직전 실행의 사유**가
    남아 새 실패에 오귀속된다(어제의 '판매분석 접근 차단'이 오늘의 설정 누락 보고에 붙는다).
    """
    fetcher._LAST_RUN_ERROR = "직전 실행의 판매분석 접근 차단"
    fetcher._LAST_RUN_KIND = "access_denied"
    # 설정 누락 경로(가장 이른 return)로 빠지게 한다
    assert fetcher._do_run({}) == 2
    assert fetcher._LAST_RUN_ERROR == ""
    assert fetcher._LAST_RUN_KIND is None
