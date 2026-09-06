# test_rocket_ri_queue_router.py — 「확인요청함」의 HTTP 경계.
#   GET /api/overview/rocket-ri-queue
#
# ★왜 이 파일이 생겼나 (적대 리뷰 P2-3, 2026-09-06): 이 엔드포인트의 **응답을 검증하는
#   테스트가 저장소에 0건**이었다. 리뷰어가 라우터에서 새 키 4개를 통째로 `pop`하는 변이를
#   넣었는데 백엔드 7,981건이 **전건 통과**했고, 프론트는 `fetchRocketRiQueue`를 mock하므로
#   거기서도 안 걸렸다 — 즉 **화면 제목이 prod에서 「—건 · —원」으로 죽어도 CI는 초록**이었다.
#   서비스가 옳게 계산해도 라우터 아래로는 아무도 안 보고 있었던 자리다.
#
# 경계에서 지키는 것:
#   ① 갈림 키 4종(live_no_invoice_* · live_invoiced_*)이 **응답에 실제로 실린다**.
#   ② `_jsonify`가 Decimal을 문자열로 보존한다(화면이 그대로 포맷한다).
#   ③ 검산이 응답 안에서 성립한다 — 두 덩어리의 합 == 라이브 전체.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
)

_URL = "/api/overview/rocket-ri-queue"
# 라우터는 모듈 로드 시점의 COUPANG_ROCKET_VENDOR_ID를 쓴다 — 테스트에선 보통 None(전 벤더)이다.
_VID = "A01029796"
_SYNC = datetime(2026, 9, 6, 12, 34)


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSession()

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), db
    app.dependency_overrides.clear()
    db.close()


def _po(db, seq, *, recv, pay_seqs=None):
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=_VID, purchase_order_status="RI",
        purchase_order_status_description="거래명세서확인요청",
        po_created_at=datetime(2026, 9, 4, 1, 0), synced_at=_SYNC,
        order_qty=1, vendor_confirmed_qty=1, receiving_qty=1,
        sum_of_order_amount=recv, sum_of_vendor_confirmed_amount=recv,
        sum_of_receiving_amount=recv, vendor_payment_seqs=pay_seqs or [], sku_count=1,
    ))
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=_VID, line_no=1, product_number=f"P{seq}",
        order_qty=1, unit_purchase_price=Decimal(recv), line_order_amount=Decimal(recv),
    ))


def test_갈림_키가_HTTP_응답에_실제로_실린다(client):
    c, db = client
    _po(db, 141005728, recv=51300)                             # 계산서 없음
    _po(db, 141475041, recv=861600, pay_seqs=[31012484])       # 계산서 있음
    db.add(CoupangRocketSettlement(
        invoice_seq=31012484, vendor_id=_VID, supply_amount=Decimal("2499301"),
        vat=Decimal("249930"), payment_amount=Decimal("2749231"),
        issue_date=date(2026, 9, 5), payment_date=date(2026, 11, 4),
        tax_invoice_confirmed_date=date(2026, 9, 6), tax_invoice_transmitted=True,
    ))
    db.commit()

    r = c.get(_URL)
    assert r.status_code == 200
    body = r.json()

    # ★키의 «존재»부터 센다 — 라우터가 조용히 떨어뜨리면 화면 제목이 「—건 · —원」이 된다.
    for k in ("live_no_invoice_count", "live_no_invoice_amount",
              "live_invoiced_count", "live_invoiced_amount"):
        assert k in body, f"응답에 {k}가 없다 — 화면 제목이 죽는다"

    assert body["live_no_invoice_count"] == 1
    assert body["live_invoiced_count"] == 1
    # Decimal은 문자열로 보존된다(화면이 그대로 포맷한다) — 부동소수로 새면 원 단위가 흔들린다.
    assert body["live_no_invoice_amount"] == "51300"
    assert body["live_invoiced_amount"] == "861600"

    # ★검산이 응답 안에서 성립해야 한다 — 한쪽이 새면 여기서 걸린다.
    assert body["live_count"] == body["live_no_invoice_count"] + body["live_invoiced_count"]
    assert Decimal(body["live_amount"]) == (
        Decimal(body["live_no_invoice_amount"]) + Decimal(body["live_invoiced_amount"])
    )

    # 갈랐다고 목록에서 지우지 않는다 — 두 건 모두 rows에 남는다.
    assert {r_["purchase_order_seq"] for r_ in body["rows"]} == {141005728, 141475041}


def test_정산행_미수집도_발행된_쪽으로_실린다(client):
    """계산서 번호는 있는데 정산행이 없는 건 — 「미발행」이 아니라 「모름」이고, 발행된 쪽이다."""
    c, db = client
    _po(db, 141477039, recv=688620, pay_seqs=[999999])   # 정산행 없음
    db.commit()

    body = c.get(_URL).json()
    assert body["live_invoiced_count"] == 1
    assert body["live_no_invoice_count"] == 0
    assert body["live_no_invoice_amount"] == "0"
    assert body["rows"][0]["invoice_rows_missing"] == [999999]
