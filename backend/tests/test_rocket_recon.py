# test_rocket_recon.py — 로켓배송(1P) 통합 대사 화면의 집계·조인 규칙.
# 인메모리 SQLite fixture. 라이브 API 없음.
#
# 이 테스트가 지키는 계약(전부 "0으로 접지 않기"의 구체형):
#   ① 발주일 KST 윈도우 = rocket_intelligence와 같은 경계(+9h)
#   ② 드리프트는 상태로 갈린다 — 거래명세서확인(CI)만 진짜 신호, 입고 전(PA/RP)은 당연
#   ③ SKU별 입고는 **단일SKU PO에서만** 귀속 — 멀티SKU PO는 미귀속(안분 추정 금지)
#   ④ 납품가능수량 NULL은 0이 아니다 → confirmed_missing_lines로 샌다
#   ⑤ 계산서번호는 있는데 정산행이 없으면 found=false(발행 안 됨이 아니다)
#   ⑥ 1PO↔N계산서·1계산서↔N PO에서 계산서를 중복으로 세지 않는다
#   ⑦ 요약(PO 그레인)과 상품표(발주상세 그레인)의 차이 = detail_coverage
#
# 라이브 실측 고정(prod 2026-07-28, vendor A01029796) — 화면 숫자의 진위 기준:
#   전체 PO 873건 = CI 797 / PA 55 / RP 21,  CI 중 발주≠입고 **173건**
#   계산서 140건(전송표기 true 90 / 미표기 50), 확정일 없는 건 0
#   발주상세 1,207행 / 243 PO / 249 SKU, 납품가능수량 미수집 라인 756행
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CoupangRocketPurchaseOrder,
    CoupangRocketPurchaseOrderItem,
    CoupangRocketSettlement,
)
from app.services.coupang.rocket_recon import (
    SETTLED_STAGE_STATUSES,
    compute_rocket_recon,
    compute_rocket_recon_sku,
)

WIN = (date(2026, 6, 1), date(2026, 6, 30))
VID = "A01029796"  # 오하이테크(1P 단일 계정)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _po(db, seq, *, created_at=datetime(2026, 6, 10, 3, 0, 0), status="CI",
        desc="거래명세서확인", order_qty=10, recv_qty=10, order_amt=100000,
        recv_amt=100000, sku_count=1, seqs=None, vendor_id=VID):
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=vendor_id,
        sum_of_order_amount=order_amt, sum_of_receiving_amount=recv_amt,
        sum_of_vendor_confirmed_amount=0,
        order_qty=order_qty, receiving_qty=recv_qty, vendor_confirmed_qty=order_qty,
        purchase_order_status=status, purchase_order_status_description=desc,
        sku_count=sku_count, po_created_at=created_at, vendor_payment_seqs=seqs,
    ))


def _item(db, seq, product_number, *, qty=10, confirmed=None, amt=100000,
          name=None, line_no=1, vendor_id=VID):
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=vendor_id, line_no=line_no,
        product_number=product_number, product_name=name or f"상품{product_number}",
        order_qty=qty, vendor_confirmed_qty=confirmed,
        unit_purchase_price=Decimal(str(amt)) / qty if qty else Decimal(0),
        line_order_amount=Decimal(str(amt)),
        line_supply_amount=Decimal(str(amt)) / Decimal("1.1"),
        line_vat=Decimal(str(amt)) - Decimal(str(amt)) / Decimal("1.1"),
    ))


def _settle(db, invoice_seq, amount, *, issue=date(2026, 6, 20),
            confirmed=date(2026, 6, 21), transmitted=True, vendor_id=VID):
    db.add(CoupangRocketSettlement(
        invoice_seq=invoice_seq, vendor_id=vendor_id,
        supply_amount=Decimal(str(amount)) / Decimal("1.1"),
        vat=Decimal(0), payment_amount=Decimal(str(amount)),
        issue_date=issue, tax_invoice_confirmed_date=confirmed,
        tax_invoice_transmitted=transmitted, bill_issue_type="역발행",
    ))


# ── ① 윈도우 = 발주일 KST(+9h) ────────────────────────────────────
def test_kst_window_boundary(db):
    _po(db, 1, created_at=datetime(2026, 5, 31, 23, 0, 0))  # KST 6/1 08:00 → IN
    _po(db, 2, created_at=datetime(2026, 6, 30, 16, 0, 0))  # KST 7/1 01:00 → OUT
    _po(db, 3, created_at=datetime(2026, 5, 31, 14, 0, 0))  # KST 5/31 23:00 → OUT
    db.commit()
    s = compute_rocket_recon(db, *WIN)["summary"]
    assert s["po_count"] == 1
    assert s["order_qty"] == 10


def test_no_date_po_counted_but_excluded(db):
    _po(db, 1)
    _po(db, 2, created_at=None)
    db.commit()
    s = compute_rocket_recon(db, *WIN)["summary"]
    assert s["po_count"] == 1
    assert s["no_date_po_count"] == 1  # 어떤 윈도우에도 안 들어감 → 침묵하지 않고 표시


# ── ② 드리프트는 상태로 갈린다 ────────────────────────────────────
def test_drift_split_by_stage(db):
    # 라이브 구조 축소판: CI 2건(1건 드리프트) + PA 1건(미입고=당연) + RP 1건(미입고)
    _po(db, 1, status="CI", desc="거래명세서확인", order_qty=10, recv_qty=10)
    _po(db, 2, status="CI", desc="거래명세서확인", order_qty=10, recv_qty=7)
    _po(db, 3, status="PA", desc="발주확정", order_qty=10, recv_qty=0)
    _po(db, 4, status="RP", desc="거래처확인요청", order_qty=10, recv_qty=0)
    db.commit()
    s = compute_rocket_recon(db, *WIN)["summary"]
    assert s["drift_po_count"] == 3            # 전체(입고 전 단계 포함)
    assert s["drift_po_count_settled_stage"] == 1   # ★진짜 신호는 CI 1건뿐
    assert s["settled_stage_po_count"] == 2
    assert s["unreceived_qty"] == 40 - 17

    by = {r["status"]: r for r in s["by_status"]}
    assert by["CI"]["is_settled_stage"] is True
    assert by["PA"]["is_settled_stage"] is False
    assert by["PA"]["drift_po_count"] == 1


def test_settled_stage_constant_is_received_stages_only(db):
    """입고 완료 단계 = CI + RI. 나머지 새 코드는 추정하지 않는다.

    RI(거래명세서확인요청)는 이름이 CI보다 앞서 보이지만 라이브 전수 입고율이 더 높다
    (0.993 > 0.910) — 물건은 이미 들어온 뒤다. 실측 근거는 모듈 상단 주석 참조.
    """
    assert SETTLED_STAGE_STATUSES == frozenset({"CI", "RI"})


def test_ri_stage_drift_is_a_real_signal(db):
    """RI 단계의 발주≠입고는 '입고 전이라 당연'이 아니라 진짜 신호로 잡힌다(회귀 방지).

    라이브 PO 134001752 축소판: 212 발주 / 210 입고 — 납품가능수량까지 210으로 확정된 미입고 2개.
    """
    _po(db, 1, status="RI", desc="거래명세서확인요청", order_qty=212, recv_qty=210)
    _po(db, 2, status="RP", desc="거래처확인요청", order_qty=10, recv_qty=0)
    db.commit()
    s = compute_rocket_recon(db, *WIN)["summary"]
    assert s["drift_po_count"] == 2                  # 전체는 둘 다 불일치
    assert s["drift_po_count_settled_stage"] == 1    # ★진짜 신호는 RI 1건
    assert s["settled_stage_po_count"] == 1
    by = {r["status"]: r for r in s["by_status"]}
    assert by["RI"]["is_settled_stage"] is True
    assert by["RP"]["is_settled_stage"] is False


# ── ③ SKU별 입고 = 단일SKU PO에서만 귀속 ─────────────────────────
def test_sku_received_attributable_only_for_single_sku_po(db):
    # PO 1: 단일SKU(발주 10 / 입고 7) → SKU A에 귀속 가능
    _po(db, 1, order_qty=10, recv_qty=7, sku_count=1)
    _item(db, 1, "AAA", qty=10, amt=100000)
    # PO 2: 멀티SKU(발주 20 / 입고 20) → A/B 어느 쪽에도 못 쪼갠다
    _po(db, 2, order_qty=20, recv_qty=20, sku_count=2)
    _item(db, 2, "AAA", qty=12, amt=120000, line_no=1)
    _item(db, 2, "BBB", qty=8, amt=80000, line_no=2)
    db.commit()

    rows = {r["product_number"]: r for r in compute_rocket_recon(db, *WIN)["skus"]}
    a = rows["AAA"]
    assert a["order_qty"] == 22                      # SKU 정확(라인 합)
    assert a["received_qty"] == 7                    # 단일SKU PO 몫만
    assert a["attributable_order_qty"] == 10
    assert a["drift_qty"] == 3                       # 10 − 7 (귀속 가능분 기준)
    assert a["received_attributable_po_count"] == 1
    assert a["received_unattributable_po_count"] == 1

    b = rows["BBB"]
    assert b["received_qty"] is None                 # ★0이 아니라 "모른다"
    assert b["drift_qty"] is None
    assert b["received_unattributable_po_count"] == 1


def test_sku_count_mismatch_blocks_attribution(db):
    """PO는 3SKU라는데 라인이 1개만 수집됐으면 귀속하지 않는다(부분 수집 함정)."""
    _po(db, 1, order_qty=30, recv_qty=25, sku_count=3)
    _item(db, 1, "AAA", qty=10, amt=100000)
    db.commit()
    row = compute_rocket_recon(db, *WIN)["skus"][0]
    assert row["received_qty"] is None
    assert row["received_unattributable_po_count"] == 1


# ── ④ 납품가능수량 NULL ≠ 0 ──────────────────────────────────────
def test_confirmed_qty_null_is_not_zero(db):
    # 같은 SKU가 두 발주에 걸쳐 있고, 한쪽은 납품가능수량이 아직 수집되지 않았다.
    # (같은 PO에 같은 SKU 두 줄은 uq_rocket_po_item이 막는다 — 라인 분리는 PO 단위로만 발생)
    _po(db, 1, sku_count=1)
    _item(db, 1, "AAA", qty=10, confirmed=8, amt=100000)
    _po(db, 2, sku_count=1)
    _item(db, 2, "AAA", qty=5, confirmed=None, amt=50000)
    db.commit()
    row = compute_rocket_recon(db, *WIN)["skus"][0]
    assert row["confirmed_qty"] == 8            # 미수집 라인은 합에 안 들어감
    assert row["confirmed_missing_lines"] == 1  # 대신 몇 라인이 미상인지 센다
    assert row["order_qty"] == 15


# ── ⑤⑥ 계산서 조인 ──────────────────────────────────────────────
def test_invoice_missing_row_is_not_unissued(db):
    _po(db, 1, seqs=[500, 501])
    _settle(db, 500, 815800)
    # 501은 계산서번호만 있고 정산행 미수집
    db.commit()
    inv = compute_rocket_recon(db, *WIN)["summary"]["invoice"]
    assert inv["mapped_invoice_count"] == 2
    assert inv["invoice_missing_row_count"] == 1
    assert inv["settled_amount"] == Decimal("815800")   # 미수집분을 0으로 채워 넣지 않는다


def test_invoice_not_double_counted_across_pos(db):
    """1계산서↔N PO(묶음 정산) — 같은 계산서를 두 번 세지 않는다."""
    _po(db, 1, seqs=[600])
    _po(db, 2, seqs=[600])
    _settle(db, 600, 1000000)
    db.commit()
    inv = compute_rocket_recon(db, *WIN)["summary"]["invoice"]
    assert inv["mapped_invoice_count"] == 1
    assert inv["settled_amount"] == Decimal("1000000")


def test_po_without_invoice_and_unconfirmed_counts(db):
    _po(db, 1, seqs=None)                 # 아직 계산서에 안 묶임
    _po(db, 2, seqs=[])                   # 빈 리스트도 같은 의미
    _po(db, 3, seqs=[700])
    _settle(db, 700, 500000, confirmed=None, transmitted=None)
    db.commit()
    inv = compute_rocket_recon(db, *WIN)["summary"]["invoice"]
    assert inv["po_without_invoice_count"] == 2
    assert inv["invoice_unconfirmed_count"] == 1
    # 전송 '미표기'(None/False)는 실패가 아니라 미표기로 센다
    assert inv["invoice_not_marked_transmitted_count"] == 1


# ── ⑦ 요약(PO 그레인) vs 상품표(발주상세 그레인) ──────────────────
def test_detail_coverage_exposes_pos_without_detail(db):
    _po(db, 1, sku_count=1)
    _item(db, 1, "AAA", qty=10, amt=100000)
    _po(db, 2, sku_count=1)   # 발주상세 미수집(수집 시작 전 발주)
    db.commit()
    r = compute_rocket_recon(db, *WIN)
    assert r["summary"]["po_count"] == 2                    # 요약은 전체
    cov = r["summary"]["detail_coverage"]
    assert cov["pos_with_detail_count"] == 1
    assert cov["pos_without_detail_count"] == 1
    assert cov["po_coverage_pct"] == Decimal("0.5000")
    assert len(r["skus"]) == 1                              # 상품표는 수집분만


def test_coverage_pct_none_when_no_po(db):
    r = compute_rocket_recon(db, *WIN)
    assert r["summary"]["po_count"] == 0
    assert r["summary"]["detail_coverage"]["po_coverage_pct"] is None  # 0%가 아니라 미상


# ── 필터 ────────────────────────────────────────────────────────
def test_drift_only_filter(db):
    _po(db, 1, order_qty=10, recv_qty=10, sku_count=1)
    _item(db, 1, "AAA", qty=10, amt=100000)
    _po(db, 2, order_qty=10, recv_qty=6, sku_count=1)
    _item(db, 2, "BBB", qty=10, amt=100000)
    _po(db, 3, order_qty=10, recv_qty=6, sku_count=2)   # 미귀속 → drift 미상
    _item(db, 3, "CCC", qty=5, amt=50000, line_no=1)
    _item(db, 3, "DDD", qty=5, amt=50000, line_no=2)
    db.commit()
    r = compute_rocket_recon(db, *WIN, drift_only=True)
    assert [x["product_number"] for x in r["skus"]] == ["BBB"]
    assert r["filters"]["sku_count_total"] == 4
    assert r["filters"]["sku_count_shown"] == 1
    # ★제외된 CCC/DDD는 '드리프트 없음'이 아니라 '판정 불가' — 그 사실이 응답에 남아야 한다.
    assert r["filters"]["sku_count_unknown_drift"] == 2


def test_drift_only_excludes_pre_receiving_stage(db):
    """입고 전 단계(PA·RP)의 발주≠입고는 드리프트가 아니다 — 필터·빨강 표시에 태우지 않는다.

    이 화면이 존재하는 이유가 '당연한 불일치'와 '진짜 신호'를 가르는 것인데, 상품표가
    단계를 무시하면 매일 보는 표가 오탐으로 가득 찬다(라이브 PA 58 + RP 13건).
    """
    _po(db, 1, status="PA", desc="발주확정", order_qty=10, recv_qty=0, sku_count=1)
    _item(db, 1, "PRE", qty=10, amt=100000)
    _po(db, 2, status="CI", desc="거래명세서확인", order_qty=10, recv_qty=6, sku_count=1)
    _item(db, 2, "REAL", qty=10, amt=100000)
    db.commit()
    rows = {x["product_number"]: x for x in compute_rocket_recon(db, *WIN)["skus"]}
    # 입고 전 단계: 참고값 drift_qty는 10이지만 '진짜 신호'는 판정 불가(None)
    assert rows["PRE"]["drift_qty"] == 10
    assert rows["PRE"]["drift_qty_settled_stage"] is None
    assert rows["PRE"]["settled_stage_attributable_po_count"] == 0
    # 입고 완료 단계: 진짜 신호
    assert rows["REAL"]["drift_qty_settled_stage"] == 4
    r = compute_rocket_recon(db, *WIN, drift_only=True)
    assert [x["product_number"] for x in r["skus"]] == ["REAL"]
    assert r["filters"]["sku_count_unknown_drift"] == 1


def test_settled_drift_qty_cancellation_still_reports_po_count(db):
    """수량 순합이 상쇄돼 0이어도 불일치 발주 건수는 남는다 — 요약(건수)과 행(순합)의 어긋남 방지.

    초과입고 PO와 미입고 PO가 섞이면 Σ(발주−입고)=0이 되어 '불일치 없음'으로 보이지만,
    요약 타일은 건수를 세므로 2건으로 잡힌다. 행에서만 사라지면 대사가 어긋난다.
    """
    _po(db, 1, status="CI", desc="거래명세서확인", order_qty=10, recv_qty=7, sku_count=1)
    _item(db, 1, "YYY", qty=10, amt=100000)
    _po(db, 2, status="CI", desc="거래명세서확인", order_qty=10, recv_qty=13, sku_count=1)
    _item(db, 2, "YYY", qty=10, amt=100000)
    db.commit()
    r = compute_rocket_recon(db, *WIN)
    row = r["skus"][0]
    assert row["drift_qty_settled_stage"] == 0        # 순합은 상쇄
    assert row["drift_po_count_settled_stage"] == 2   # ★건수는 남는다
    assert r["summary"]["drift_po_count_settled_stage"] == 2  # 요약과 같은 단위
    # 상쇄됐다고 drift_only에서 사라지면 안 된다.
    assert [x["product_number"] for x in compute_rocket_recon(db, *WIN, drift_only=True)["skus"]] == ["YYY"]


def test_sku_invoice_counters_are_sku_scoped_not_window_distinct(db):
    """행 배지 합 ≠ 요약 타일 — 계산서 1건이 멀티SKU 발주에 걸리면 SKU마다 잡힌다.

    버그가 아니라 분모가 다른 것이다(요약=기간 전체 중복 제거). 화면 각주가 이 사실을
    고지하므로, 그 전제가 코드에서 바뀌면 각주가 거짓말이 된다 → 여기서 고정한다.
    """
    _po(db, 1, seqs=[900], sku_count=3)
    _item(db, 1, "AAA", qty=5, amt=50000, line_no=1)
    _item(db, 1, "BBB", qty=5, amt=50000, line_no=2)
    _item(db, 1, "CCC", qty=5, amt=50000, line_no=3)
    _settle(db, 900, 150000, confirmed=None)   # 미확정 계산서 1건
    db.commit()
    r = compute_rocket_recon(db, *WIN)
    assert r["summary"]["invoice"]["invoice_unconfirmed_count"] == 1   # 윈도우 distinct
    assert sum(x["invoice_unconfirmed_count"] for x in r["skus"]) == 3  # SKU 스코프 반복


def test_unconfirmed_only_filter(db):
    _po(db, 1, seqs=[800], sku_count=1)
    _settle(db, 800, 100000)                  # 정상 확정+전송
    _item(db, 1, "AAA", qty=10, amt=100000)
    _po(db, 2, seqs=None, sku_count=1)        # 계산서 미연결
    _item(db, 2, "BBB", qty=10, amt=100000)
    db.commit()
    r = compute_rocket_recon(db, *WIN, unconfirmed_only=True)
    assert [x["product_number"] for x in r["skus"]] == ["BBB"]


def test_summary_ignores_filters(db):
    """필터는 상품표에만 걸린다 — 요약 타일이 필터에 따라 흔들리면 대사 기준을 잃는다."""
    _po(db, 1, order_qty=10, recv_qty=10, sku_count=1)
    _item(db, 1, "AAA", qty=10, amt=100000)
    _po(db, 2, order_qty=10, recv_qty=6, sku_count=1)
    _item(db, 2, "BBB", qty=10, amt=100000)
    db.commit()
    base = compute_rocket_recon(db, *WIN)["summary"]
    filtered = compute_rocket_recon(db, *WIN, drift_only=True)["summary"]
    assert base == filtered


# ── 행 확장(SKU → PO 목록) ───────────────────────────────────────
def test_sku_detail_rows(db):
    _po(db, 1, created_at=datetime(2026, 6, 10, 3, 0, 0), order_qty=10, recv_qty=7,
        sku_count=1, seqs=[900, 901])
    _item(db, 1, "AAA", qty=10, confirmed=9, amt=100000, name="오하이 강화유리")
    _settle(db, 900, 60000)
    # 901 = 정산 미수집
    db.commit()

    r = compute_rocket_recon_sku(db, "AAA", *WIN)
    assert r["po_count"] == 1
    assert r["product_name"] == "오하이 강화유리"
    row = r["rows"][0]
    assert row["po_created_date"] == "2026-06-10"       # UTC 03:00 → KST 12:00 같은 날
    assert row["status_description"] == "거래명세서확인"
    assert row["is_settled_stage"] is True
    assert row["po_order_qty"] == 10 and row["po_received_qty"] == 7
    assert row["po_drift_qty"] == 3
    assert row["line_order_qty"] == 10 and row["line_confirmed_qty"] == 9

    inv = {i["invoice_seq"]: i for i in row["invoices"]}
    assert inv[900]["found"] is True
    assert inv[900]["payment_amount"] == Decimal("60000")
    assert inv[901]["found"] is False
    assert inv[901]["payment_amount"] is None          # ★0으로 접지 않는다


def test_sku_detail_respects_window(db):
    _po(db, 1, created_at=datetime(2026, 5, 1, 3, 0, 0), sku_count=1)  # 윈도우 밖
    _item(db, 1, "AAA", qty=10, amt=100000)
    db.commit()
    r = compute_rocket_recon_sku(db, "AAA", *WIN)
    assert r["po_count"] == 0
    assert r["rows"] == []


def test_vendor_filter(db):
    _po(db, 1, vendor_id=VID, sku_count=1)
    _item(db, 1, "AAA", qty=10, amt=100000, vendor_id=VID)
    _po(db, 2, vendor_id="OTHER", sku_count=1)
    _item(db, 2, "BBB", qty=10, amt=100000, vendor_id="OTHER")
    db.commit()
    r = compute_rocket_recon(db, *WIN, vendor_id=VID)
    assert r["summary"]["po_count"] == 1
    assert [x["product_number"] for x in r["skus"]] == ["AAA"]
    assert r["period"]["vendor_id"] == VID


# ── 라이브 형상 재현(prod 2026-07-28 축소판) ──────────────────────
def test_live_shaped_summary_reproduces_drift_signal(db):
    """prod 실측 형상: CI 797/PA 55/RP 21 중 CI 드리프트 173건 — 비율 구조를 축소 재현.

    핵심은 절대 숫자가 아니라 **PA/RP의 미입고가 CI 드리프트를 오염시키지 않는다**는 것.
    라이브에서 전체 드리프트 249건(173+55+21) 중 진짜 신호는 173건이다.
    """
    for i in range(5):                                   # CI 5건 중 2건 드리프트
        _po(db, 100 + i, status="CI", desc="거래명세서확인",
            order_qty=10, recv_qty=10 if i >= 2 else 8)
    for i in range(3):                                   # PA 3건 전부 미입고
        _po(db, 200 + i, status="PA", desc="발주확정", order_qty=10, recv_qty=0)
    _po(db, 300, status="RP", desc="거래처확인요청", order_qty=10, recv_qty=0)
    db.commit()

    s = compute_rocket_recon(db, *WIN)["summary"]
    assert s["po_count"] == 9
    assert s["drift_po_count"] == 6                      # 2 + 3 + 1
    assert s["drift_po_count_settled_stage"] == 2        # ★화면이 강조하는 값
    assert s["received_qty"] == 3 * 10 + 2 * 8
    assert s["order_qty"] == 90
