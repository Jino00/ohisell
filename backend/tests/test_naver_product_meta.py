# test_naver_product_meta.py — C10 상품 메타 적재 (D-NAO-212 · 북극성 M1 ④)
#
# 무엇을 지키는가:
#   ①**필드 절삭 0** — 실측 29키가 전부 컬럼에 실리고 raw_json이 원문을 보존한다(교훈 #315)
#   ②첫 회차 change 0행(신규 insert는 «변경»이 아니다) / 값이 바뀐 회차만 change 행
#   ③**「완주」는 두 등식이 동시에 성립할 때만 참**이다 — 절단이 success로 기록된 실사고의 재발
#     방지(교훈 #318·#319·#320). 미완주는 complete=False로 «표면화»되어야 한다
#   ④같은 회차에 같은 channelProductNo가 두 번 와도 **INSERT가 두 번 일어나지 않는다**
#     (query-then-add 이중 INSERT — 이 저장소에서 5회 재발한 모양, 교훈 #292)
#   ⑤응답에서 사라진 상품의 행을 **지우지 않는다**(「사라졌다」와 「이번엔 안 보였다」를 못 가른다)
#   ⑥raw_json은 diff 대상이 아니다 — 넣으면 키 순서만 달라져도 «매일 전건 변경»이 된다
#   ⑦조인키는 **문자열**이다(상대편 mall_product_id·channel_product_id가 String(50))
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverProductMetaChange, NaverProductMetaCurrent
from app.services import naver_product_meta_ingest as ingest


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ★prod와 같은 autoflush=False (교훈 #292 — 관대한 픽스처는 query-then-add 결함을 못 잡는다)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# 2026-08-21 prod 실응답에서 관측된 29키를 그대로 옮긴 표본(값만 축약).
CP_29 = {
    "channelProductNo": 12345678901, "originProductNo": 987654321, "groupProductNo": 555,
    "name": "아이폰16 프로 강화유리", "statusType": "SALE",
    "channelProductDisplayStatusType": "ON", "channelServiceType": "STOREFARM",
    "salePrice": 19900, "discountedPrice": 12900, "mobileDiscountedPrice": 12900,
    "stockQuantity": 300, "categoryId": "50000205",
    "wholeCategoryId": "50000000>50000204>50000205",
    "wholeCategoryName": "디지털/가전>휴대폰액세서리>액정보호필름",
    "brandName": "오하이", "manufacturerName": "오하이테크",
    "deliveryFee": 0, "returnFee": 3000, "exchangeFee": 6000,
    "deliveryAttributeType": "NORMAL", "knowledgeShoppingProductRegistration": True,
    "sellerTags": [{"code": 1, "text": "강화유리"}], "representativeImage": {"url": "https://x/y.jpg"},
    "regDate": "2026-03-01T10:00:00.000+09:00", "modifiedDate": "2026-08-20T09:00:00.000+09:00",
    "textReviewPoint": 300, "photoVideoReviewPoint": 500, "regularCustomerPoint": 100,
    "manerPurchasePointPlaceholder": None,  # 아래에서 제거 — 오타 방지용 자리표시
}
CP_29.pop("manerPurchasePointPlaceholder")
CP_29["managerPurchasePoint"] = 0


class FakeClient:
    """페이지 목록을 그대로 돌려주는 가짜 클라이언트. 실패를 주입할 수도 있다."""

    def __init__(self, pages: list[dict], raise_on_page: int | None = None):
        self._pages = pages
        self._raise_on = raise_on_page
        self.calls: list[dict] = []

    def search_products_raw(self, *, page=1, size=200, **kw):
        self.calls.append({"page": page, "size": size, **kw})
        if self._raise_on is not None and page == self._raise_on:
            raise RuntimeError("네이버 500")
        return self._pages[page - 1]


def _page(contents: list[dict], *, total_elements: int, total_pages: int, page: int = 1,
          last: bool | None = None) -> dict:
    return {"contents": contents, "totalElements": total_elements,
            "totalPages": total_pages, "page": page,
            "last": (page >= total_pages) if last is None else last}


def _origin(cps: list[dict], origin_no=987654321) -> dict:
    return {"originProductNo": origin_no, "groupProductNo": 555, "channelProducts": cps}


def test_first_run_stores_all_29_fields_and_raw(db):
    """①절삭 0 — 29키가 컬럼으로 살아 있고 raw_json이 원문을 보존한다."""
    c = FakeClient([_page([_origin([CP_29])], total_elements=1, total_pages=1)])
    st = ingest.sync_product_meta(db, client=c)

    assert st["complete"] is True
    row = db.execute(select(NaverProductMetaCurrent)).scalar_one()
    # ⑦조인키는 문자열이다
    assert row.channel_product_no == "12345678901"
    assert isinstance(row.channel_product_no, str)
    assert row.origin_product_no == "987654321"
    assert row.name == "아이폰16 프로 강화유리"
    assert row.status_type == "SALE"
    assert row.display_status_type == "ON"
    assert row.channel_service_type == "STOREFARM"
    assert (row.sale_price, row.discounted_price, row.mobile_discounted_price) == (19900, 12900, 12900)
    assert row.stock_quantity == 300
    assert row.category_id == "50000205"
    assert row.whole_category_name.endswith("액정보호필름")
    assert (row.brand_name, row.manufacturer_name) == ("오하이", "오하이테크")
    assert (row.delivery_fee, row.return_fee, row.exchange_fee) == (0, 3000, 6000)
    assert row.delivery_attribute_type == "NORMAL"
    assert row.knowledge_shopping_registration is True
    # ★리뷰 «수»가 아니라 적립 포인트다 — 이름과 값이 그 사실을 말해야 한다
    assert (row.text_review_point, row.photo_video_review_point) == (300, 500)
    assert (row.regular_customer_point, row.manager_purchase_point) == (100, 0)
    assert json.loads(row.seller_tags_json)[0]["text"] == "강화유리"
    assert row.image_url == "https://x/y.jpg"
    assert row.modified_date == "2026-08-20T09:00:00.000+09:00"
    # 원문 보존 — 키 부재/null 구분의 정본
    assert json.loads(row.raw_json)["channelProductNo"] == 12345678901

    # ②첫 회차는 전건 신규라 change 0행이 정상
    assert db.execute(select(func.count()).select_from(NaverProductMetaChange)).scalar() == 0
    assert (st["new"], st["changed"], st["channel_rows"]) == (1, 0, 1)


def test_second_run_records_only_changed_fields(db):
    """②값이 바뀐 필드만 change 행에 남고, 안 바뀌면 행이 안 생긴다."""
    ingest.sync_product_meta(db, client=FakeClient(
        [_page([_origin([CP_29])], total_elements=1, total_pages=1)]))

    # 값 무변경 회차
    st = ingest.sync_product_meta(db, client=FakeClient(
        [_page([_origin([dict(CP_29)])], total_elements=1, total_pages=1)]))
    assert (st["new"], st["changed"], st["unchanged"]) == (0, 0, 1)
    assert db.execute(select(func.count()).select_from(NaverProductMetaChange)).scalar() == 0

    # 가격·재고가 바뀐 회차
    moved = dict(CP_29, discountedPrice=9900, stockQuantity=250)
    st2 = ingest.sync_product_meta(db, client=FakeClient(
        [_page([_origin([moved])], total_elements=1, total_pages=1)]))
    assert (st2["new"], st2["changed"]) == (0, 1)
    chg = db.execute(select(NaverProductMetaChange)).scalar_one()
    fields = json.loads(chg.changed_fields)
    assert fields["discounted_price"] == [12900, 9900]
    assert fields["stock_quantity"] == [300, 250]
    assert "raw_json" not in fields          # ⑥raw_json은 diff 대상이 아니다
    row = db.execute(select(NaverProductMetaCurrent)).scalar_one()
    assert row.discounted_price == 9900 and row.last_changed_at is not None


def test_incomplete_when_origin_count_mismatches_total_elements(db):
    """③원상품 수 != totalElements면 «완주»가 아니다 — 절단을 success로 기록하지 않는다."""
    c = FakeClient([_page([_origin([CP_29])], total_elements=99, total_pages=1)])
    st = ingest.sync_product_meta(db, client=c)
    assert st["complete"] is False
    assert "1 != totalElements 99" in st["incomplete_reason"]
    # ⚠️단 받은 페이지의 적재분은 지우지 않는다(관측된 값은 참이다)
    assert db.execute(select(func.count()).select_from(NaverProductMetaCurrent)).scalar() == 1


def test_incomplete_when_a_page_fails_midway(db):
    """③조회 실패는 «0건»이 아니라 «완주 실패»다 — 앞 페이지 적재분은 남는다."""
    p1 = _page([_origin([CP_29])], total_elements=2, total_pages=2, page=1, last=False)
    c = FakeClient([p1, {}], raise_on_page=2)
    st = ingest.sync_product_meta(db, client=c)
    assert st["complete"] is False
    assert "page 2" in st["incomplete_reason"]
    assert st["errors"] and "RuntimeError" in st["errors"][0]
    assert db.execute(select(func.count()).select_from(NaverProductMetaCurrent)).scalar() == 1


def test_duplicate_channel_product_in_one_run_inserts_once(db):
    """④같은 회차에 같은 키가 두 번 와도 INSERT는 한 번이다(query-then-add 이중 INSERT 방어)."""
    dup = _page([_origin([CP_29]), _origin([dict(CP_29, stockQuantity=1)], origin_no=111)],
                total_elements=2, total_pages=1)
    st = ingest.sync_product_meta(db, client=FakeClient([dup]))
    assert db.execute(select(func.count()).select_from(NaverProductMetaCurrent)).scalar() == 1
    assert st["channel_rows"] == 2      # 응답에서 본 건수는 2
    assert st["new"] == 1               # 만든 행은 1


def test_missing_product_row_is_not_deleted(db):
    """⑤이번 응답에 없는 상품의 행을 지우지 않는다 — last_seen_at 정체로만 관측한다."""
    ingest.sync_product_meta(db, client=FakeClient(
        [_page([_origin([CP_29])], total_elements=1, total_pages=1)]))
    before = db.execute(select(NaverProductMetaCurrent)).scalar_one().last_seen_at

    other = dict(CP_29, channelProductNo=222, name="다른 상품")
    ingest.sync_product_meta(db, client=FakeClient(
        [_page([_origin([other], origin_no=222)], total_elements=1, total_pages=1)]))

    rows = {r.channel_product_no: r for r in
            db.execute(select(NaverProductMetaCurrent)).scalars().all()}
    assert set(rows) == {"12345678901", "222"}          # 지우지 않았다
    assert rows["12345678901"].last_seen_at == before     # 정체했다
