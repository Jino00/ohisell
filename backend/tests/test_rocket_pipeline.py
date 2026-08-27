# test_rocket_pipeline.py — 로켓배송(1P) 열린 파이프라인의 칸 분류·자백 규칙.
# 인메모리 SQLite fixture. 라이브 API 없음.
#
# 이 테스트가 지키는 계약(계약 CONTRACT_1p_invoice_gap):
#   ① 칸끼리 금액이 겹치지 않는다 — 소계는 ①②③뿐, ④지급대기(계산서 그레인)·RI는 밖
#   ② 미종결(RP·PA)과 종결단계(RI·CI)를 가른다 — 종결단계의 차이는 «대기»가 아니다
#   ③ **입고는 발송의 하한이다** — ASN 라인 0건인데 입고된 PO를 「안 보냄」으로 세지 않는다
#   ④ **시간대 규약이 섞여 있다** — shipped_at·synced_at은 KST 저장, po_created_at은 UTC 저장
#   ⑤ clamp(음수 절단)·ASN 미수집 보정·단가 결손을 숨기지 않고 센다
#   ⑥ RI 목록은 살아있음/굳음을 가른다 — 굳은 것을 산 것처럼 보여주면 죽은 줄을 누른다
#   ⑦ 미해명(종결단계 발송>입고)은 confirmed=False이고 소계에 절대 안 들어간다
#
# 라이브 실측 고정(prod 2026-08-27, vendor A01029796) — 화면 숫자의 진위 기준:
#   ①확인대기 30 PO 2,907,817 / ②발송대기 50 PO 12,895,305 / ③입고대기 27 PO 9,373,788
#   소계 25,176,910 · ④지급대기 계산서 66건 116,587,510
#   발송일 창 8/20~ 적용 시 ③ = 21 PO **9,319,638**
#   미해명 137 PO 8,939,475(확정 아님) · 발송없이닫힘 2 PO 55,200
#   RI 12건 = 살아있음 4(481,085) / 굳음 8(4,870,435)
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
    CoupangRocketShipment,
    CoupangRocketShipmentItem,
)
from app.services.coupang.rocket_pipeline import (
    PRE_INVOICE_STAGES,
    STAGE_AWAIT_CONFIRM,
    STAGE_AWAIT_RECEIVE,
    STAGE_AWAIT_SHIP,
    compute_rocket_pipeline,
    compute_rocket_pipeline_rows,
    compute_rocket_ri_queue,
)

VID = "A01029796"  # 오하이테크(1P 단일 계정)
TODAY = date(2026, 8, 27)
# 마지막 수집일 — 신선도 판정의 기준선. 이 날짜의 synced_at을 가진 PO만 «최신»이다.
LAST_SYNC = datetime(2026, 8, 27, 16, 19)      # ★KST naive로 저장된다(kst_now())
OLD_SYNC = datetime(2026, 8, 5, 16, 49)        # 굳은 PO


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _po(db, seq, status, *, conf, recv=0, order=None, synced=LAST_SYNC,
        po_created=datetime(2026, 8, 18, 3, 0), pay_seqs=None, conf_qty=1, recv_qty=0):
    """PO 한 건. po_created_at은 **UTC naive**(JSON 유래), synced_at은 **KST naive**(kst_now())."""
    db.add(CoupangRocketPurchaseOrder(
        purchase_order_seq=seq, vendor_id=VID,
        sum_of_order_amount=order if order is not None else conf,
        sum_of_vendor_confirmed_amount=conf, sum_of_receiving_amount=recv,
        order_qty=conf_qty, vendor_confirmed_qty=conf_qty, receiving_qty=recv_qty,
        purchase_order_status=status,
        purchase_order_status_description={"RP": "거래처확인요청", "PA": "발주확정",
                                           "RI": "거래명세서확인요청", "CI": "거래명세서확인"}[status],
        po_created_at=po_created, synced_at=synced, vendor_payment_seqs=pay_seqs or [],
        sku_count=1,
    ))


def _line(db, seq, product, qty, unit_price):
    db.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VID, line_no=1, product_number=product,
        order_qty=qty, unit_purchase_price=Decimal(str(unit_price)),
        line_order_amount=Decimal(str(unit_price)) * qty,
    ))


def _ship(db, shipment_seq, po_seq, product, qty, *, shipped_at):
    """발송 1건. shipped_at은 **KST naive**로 저장된다(발주상세 DOM 셀 「발송일」 유래)."""
    db.add(CoupangRocketShipment(
        shipment_seq=shipment_seq, vendor_id=VID, shipped_at=shipped_at, status_code="CONFIRMED",
    ))
    db.add(CoupangRocketShipmentItem(
        shipment_seq=shipment_seq, vendor_id=VID, line_no=1, purchase_order_seq=po_seq,
        product_number=product, shipped_qty=qty, received_qty=0,
    ))


def _stage(result, key):
    return next(s for s in result["stages"] if s["key"] == key)


# ══════════════════════════════════════════════════════════════
# ① 칸 분류
# ══════════════════════════════════════════════════════════════
def test_rp는_확인대기이고_발송대기에_들어가지_않는다(db):
    """RP = 아직 우리가 확정 안 한 단계. 확정액 전액이 ①이고 ②엔 0이어야 한다.

    ②에도 넣으면 같은 돈이 두 칸에 잡혀 소계가 부푼다.
    """
    _po(db, 1, "RP", conf=100000, conf_qty=10)
    _line(db, 1, "P1", 10, 10000)
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert _stage(r, STAGE_AWAIT_CONFIRM)["amount"] == Decimal("100000")
    assert _stage(r, STAGE_AWAIT_SHIP)["amount"] == Decimal("0")
    assert r["pre_invoice_subtotal"]["amount"] == Decimal("100000")


def test_pa는_발송분과_미발송분이_두_칸으로_갈리고_겹치지_않는다(db):
    """확정 10개 중 6개 발송, 그중 4개만 입고 → ②=미발송 4개분, ③=발송했으나 미입고 2개분.

    ②+③이 확정액을 넘지 않는 것이 「칸이 안 겹친다」의 구체형이다.
    """
    _po(db, 1, "PA", conf=100000, recv=40000, conf_qty=10, recv_qty=4)
    _line(db, 1, "P1", 10, 10000)
    _ship(db, 900, 1, "P1", 6, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert _stage(r, STAGE_AWAIT_SHIP)["amount"] == Decimal("40000")     # 100,000 − 60,000
    assert _stage(r, STAGE_AWAIT_RECEIVE)["amount"] == Decimal("20000")  # 60,000 − 40,000
    assert r["pre_invoice_subtotal"]["amount"] == Decimal("60000")
    assert r["pre_invoice_subtotal"]["amount"] <= Decimal("100000")


def test_종결단계의_차이는_대기가_아니라_별도_덩어리다(db):
    """CI(거래명세서확인)에서 발송>입고는 «입고 대기»가 아니라 «미해명»이다.

    입고가 끝난 단계라 앞으로 잡힐 것이 아니고, 덜 보냄·반송·진짜 미수금이 섞여 있다.
    이걸 ③에 넣으면 「곧 계산서 나올 돈」처럼 읽힌다.
    """
    _po(db, 1, "CI", conf=100000, recv=70000, conf_qty=10, recv_qty=7)
    _line(db, 1, "P1", 10, 10000)
    _ship(db, 900, 1, "P1", 10, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert _stage(r, STAGE_AWAIT_RECEIVE)["amount"] == Decimal("0")
    assert r["unexplained"]["amount"] == Decimal("30000")
    assert r["unexplained"]["confirmed"] is False
    assert r["pre_invoice_subtotal"]["amount"] == Decimal("0")


def test_미해명은_소계에_절대_들어가지_않는다(db):
    """계약 §3 금지선. 소계 = ①②③ 합이고, 미해명·발송없이닫힘은 그 밖이다."""
    _po(db, 1, "PA", conf=50000, conf_qty=5)          # ② 50,000
    _line(db, 1, "P1", 5, 10000)
    _po(db, 2, "CI", conf=100000, recv=70000, conf_qty=10, recv_qty=7)
    _line(db, 2, "P2", 10, 10000)
    _ship(db, 901, 2, "P2", 10, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    subtotal = sum(_stage(r, k)["amount"] for k in PRE_INVOICE_STAGES)
    assert r["pre_invoice_subtotal"]["amount"] == subtotal == Decimal("50000")
    assert r["unexplained"]["amount"] == Decimal("30000")


# ══════════════════════════════════════════════════════════════
# ② 입고는 발송의 하한 (2026-08-27 라이브가 잡은 결함)
# ══════════════════════════════════════════════════════════════
def test_ASN기록이_없어도_입고됐으면_안보낸_것이_아니다(db):
    """★prod 실측 2건(123977085·127009073)이 이 모양이었다 — 전량 입고인데 ASN 라인 0건.

    `shipped=0`을 그대로 「안 보냄」으로 읽으면 **전량 입고된 발주가 「영영 못 보낸 분」**으로
    계상된다(실제로 그렇게 225,840원이 잡혀 있었고, 자를 고치니 55,200원으로 내려갔다).
    """
    _po(db, 1, "CI", conf=161100, recv=161100, conf_qty=15, recv_qty=15)
    _line(db, 1, "P1", 15, 10740)
    # 발송(ASN) 라인을 일부러 안 넣는다 = 수집 결손
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert r["closed_unshipped"]["amount"] == Decimal("0"), "입고된 발주가 «못 보낸 분»으로 잡혔다"
    assert r["clamp"]["asn_missing"]["po_count"] == 1
    assert r["clamp"]["asn_missing"]["received_amount"] == Decimal("161100")


def test_ASN_결손_보정은_숨기지_않고_자백한다(db):
    """보정 자체보다 «보정했다는 사실»이 화면에 뜨는 것이 계약이다(조용한 절단 금지)."""
    _po(db, 1, "PA", conf=100000, recv=100000, conf_qty=10, recv_qty=10)
    _line(db, 1, "P1", 10, 10000)
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert r["clamp"]["asn_missing"]["po_count"] == 1
    assert _stage(r, STAGE_AWAIT_SHIP)["amount"] == Decimal("0")  # 하한을 썼으니 미발송 0


def test_단가를_못_붙인_발송은_0원이_아니라_수량으로_샌다(db):
    """발주상세가 아직 안 들어온 PO의 발송 라인 — 금액에 0을 더하면 미발행이 과소해진다."""
    _po(db, 1, "PA", conf=100000, conf_qty=10)
    # 발주상세(단가) 라인을 일부러 안 넣는다
    _ship(db, 900, 1, "P1", 4, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert r["unpriced_shipped_qty"] == 4


# ══════════════════════════════════════════════════════════════
# ③ 시간대 규약 — 섞여 있다
# ══════════════════════════════════════════════════════════════
def test_마지막_수집일은_synced_at을_환산하지_않는다(db):
    """`synced_at`은 `kst_now()`가 넣은 **KST naive**다. +9h를 걸면 «내일»이 나온다.

    그 하루가 밀리면 전건이 «굳음»으로 판정돼 이 화면의 핵심 기능이 통째로 뒤집힌다.
    """
    _po(db, 1, "PA", conf=100000, conf_qty=10, synced=LAST_SYNC)
    _line(db, 1, "P1", 10, 10000)
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert r["last_collection_date_kst"] == "2026-08-27"
    assert r["freshness"]["po_synced_at_kst"] == "2026-08-27"


def test_발송일_창은_shipped_at을_환산하지_않는다(db):
    """`shipped_at`은 발주상세 DOM 셀 유래라 **KST naive**다(실측: 992건 중 965건이 16시대).

    UTC로 알고 −9h를 걸면 16:00 발송이 창 밖으로 밀려 «그날 발송이 없는 것»이 된다.
    """
    _po(db, 1, "PA", conf=100000, conf_qty=10)
    _line(db, 1, "P1", 10, 10000)
    _ship(db, 900, 1, "P1", 10, shipped_at=datetime(2026, 8, 20, 16, 14))  # 8/20 16:14 KST
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY, date(2026, 8, 20), date(2026, 8, 20))
    assert _stage(r, STAGE_AWAIT_RECEIVE)["amount"] == Decimal("100000"), \
        "16시대 발송이 당일 창에서 빠졌다 — shipped_at을 UTC로 읽고 있다"


def test_발송일_창_밖의_발송은_창에서_빠진다(db):
    """창이 실제로 «거르는지» — 위 테스트만 있으면 창을 통째로 무시해도 통과한다."""
    _po(db, 1, "PA", conf=100000, conf_qty=10)
    _line(db, 1, "P1", 10, 10000)
    _ship(db, 900, 1, "P1", 10, shipped_at=datetime(2026, 8, 19, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY, date(2026, 8, 20), date(2026, 8, 27))
    assert _stage(r, STAGE_AWAIT_RECEIVE)["amount"] == Decimal("0")


def test_창은_삼번_칸에만_걸리고_일이번은_그대로다(db):
    """창을 ①②에도 걸면 「지금 확정·발송을 기다리는 돈」이 발송일로 잘려 사라진다."""
    _po(db, 1, "RP", conf=70000, conf_qty=7)
    _line(db, 1, "P1", 7, 10000)
    _po(db, 2, "PA", conf=50000, conf_qty=5)
    _line(db, 2, "P2", 5, 10000)
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY, date(2026, 8, 20), date(2026, 8, 27))
    assert _stage(r, STAGE_AWAIT_CONFIRM)["amount"] == Decimal("70000")
    assert _stage(r, STAGE_AWAIT_SHIP)["amount"] == Decimal("50000")


# ══════════════════════════════════════════════════════════════
# ④ 신선도 — 굳은 것과 산 것
# ══════════════════════════════════════════════════════════════
def test_굳은_PO는_금액이_갈려_나온다(db):
    """수집 창 밖 PO는 상태가 멈춰 있다. 합계에는 넣되 «굳음»으로 갈라야 오독을 막는다."""
    _po(db, 1, "PA", conf=100000, conf_qty=10, synced=LAST_SYNC)
    _line(db, 1, "P1", 10, 10000)
    _po(db, 2, "PA", conf=30000, conf_qty=3, synced=OLD_SYNC)
    _line(db, 2, "P2", 3, 10000)
    db.commit()
    st = _stage(compute_rocket_pipeline(db, VID, TODAY), STAGE_AWAIT_SHIP)
    assert st["amount"] == Decimal("130000")
    assert st["fresh_amount"] == Decimal("100000")
    assert st["stale_amount"] == Decimal("30000")
    assert st["stale_po_count"] == 1
    assert st["oldest_stale_synced_date"] == "2026-08-05"


def test_수집이_하루_걸러_돌아도_전건이_굳음이_되지_않는다(db):
    """판정 기준선은 «오늘»이 아니라 «마지막 수집일»이다 — 버튼-only 수집(D-17)이라 매일 안 돈다."""
    old = datetime(2026, 8, 25, 10, 0)
    _po(db, 1, "PA", conf=100000, conf_qty=10, synced=old)
    _line(db, 1, "P1", 10, 10000)
    db.commit()
    st = _stage(compute_rocket_pipeline(db, VID, TODAY), STAGE_AWAIT_SHIP)
    assert st["stale_amount"] == Decimal("0"), "마지막 수집분인데 굳음으로 판정됐다"
    assert st["fresh_amount"] == Decimal("100000")


# ══════════════════════════════════════════════════════════════
# ⑤ 칸 목록
# ══════════════════════════════════════════════════════════════
def test_칸_목록의_금액은_PO총액이_아니라_그_칸의_몫이다(db):
    _po(db, 1, "PA", conf=100000, recv=40000, conf_qty=10, recv_qty=4)
    _line(db, 1, "P1", 10, 10000)
    _ship(db, 900, 1, "P1", 6, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    rows = compute_rocket_pipeline_rows(db, VID, STAGE_AWAIT_RECEIVE)["rows"]
    assert len(rows) == 1
    assert rows[0]["stage_amount"] == Decimal("20000")
    assert rows[0]["confirmed_amount"] == Decimal("100000")


def test_목록이_잘리면_잘렸다고_말한다(db):
    """조용한 절단 금지 — 잘린 목록이 「전부」로 읽히면 미발행이 과소해 보인다."""
    for i in range(1, 6):
        _po(db, i, "PA", conf=10000, conf_qty=1)
        _line(db, i, f"P{i}", 1, 10000)
    db.commit()
    out = compute_rocket_pipeline_rows(db, VID, STAGE_AWAIT_SHIP, limit=2)
    assert out["total_count"] == 5
    assert len(out["rows"]) == 2
    assert out["truncated"] is True


def test_알_수_없는_칸은_거부한다(db):
    with pytest.raises(ValueError):
        compute_rocket_pipeline_rows(db, VID, "await_payment")


# ══════════════════════════════════════════════════════════════
# ⑥ 확인요청함 (RI)
# ══════════════════════════════════════════════════════════════
def test_RI는_살아있음과_굳음으로_갈린다(db):
    """★실측(2026-08-27) RI 12건 중 8건이 지급까지 끝난 유령이었다.

    안 가르면 Jino가 죽은 줄을 누른다 — 이 갈림이 S2의 실질적 합격 조건이다.
    """
    _po(db, 1, "RI", conf=230235, recv=230235, conf_qty=25, recv_qty=25,
        synced=LAST_SYNC, pay_seqs=[30871641])
    _line(db, 1, "P1", 25, 9209.4)
    _po(db, 2, "RI", conf=75430, recv=75430, conf_qty=10, recv_qty=10,
        synced=OLD_SYNC, pay_seqs=[27442270])
    _line(db, 2, "P2", 10, 7543)
    db.add(CoupangRocketSettlement(
        invoice_seq=30871641, vendor_id=VID, supply_amount=Decimal("367950"),
        vat=Decimal("36795"), payment_amount=Decimal("404745"),
        issue_date=date(2026, 8, 26), payment_date=date(2026, 10, 23),
        tax_invoice_confirmed_date=date(2026, 8, 27), tax_invoice_transmitted=True,
    ))
    db.add(CoupangRocketSettlement(
        invoice_seq=27442270, vendor_id=VID, supply_amount=Decimal("100"),
        vat=Decimal("10"), payment_amount=Decimal("110"),
        issue_date=date(2025, 10, 22), payment_date=date(2025, 12, 19),
        tax_invoice_confirmed_date=date(2025, 10, 23), tax_invoice_transmitted=True,
    ))
    db.commit()
    q = compute_rocket_ri_queue(db, VID)
    assert q["live_count"] == 1 and q["live_amount"] == Decimal("230235")
    assert q["stale_count"] == 1 and q["stale_amount"] == Decimal("75430")
    live = next(r for r in q["rows"] if not r["is_stale"])
    # ★판정 근거를 행에 실어 보낸다 — 화면이 «왜 죽었다고 보는지»를 말할 수 있어야 한다
    assert live["invoices"][0]["payment_date"] == "2026-10-23"
    stale = next(r for r in q["rows"] if r["is_stale"])
    assert stale["synced_date"] == "2026-08-05"
    assert stale["invoices"][0]["payment_date"] == "2025-12-19"


def test_RI는_파이프라인_소계에_들어가지_않는다(db):
    """RI 입고금액은 이미 계산서가 나가 ④지급대기에 있다 — 칸으로 더하면 같은 돈을 두 번 센다."""
    _po(db, 1, "RI", conf=230235, recv=230235, conf_qty=25, recv_qty=25, pay_seqs=[30871641])
    _line(db, 1, "P1", 25, 9209.4)
    _ship(db, 900, 1, "P1", 25, shipped_at=datetime(2026, 8, 25, 16, 0))
    db.commit()
    r = compute_rocket_pipeline(db, VID, TODAY)
    assert r["pre_invoice_subtotal"]["amount"] == Decimal("0")


def test_정산행_미수집은_미발행이_아니라_모름이다(db):
    """계산서번호는 있는데 정산행이 없으면 «발행 안 됨»이 아니라 «아직 안 받아옴»이다."""
    _po(db, 1, "RI", conf=1000, recv=1000, conf_qty=1, recv_qty=1, pay_seqs=[999999])
    _line(db, 1, "P1", 1, 1000)
    db.commit()
    row = compute_rocket_ri_queue(db, VID)["rows"][0]
    assert row["invoices"] == []
    assert row["invoice_rows_missing"] == [999999]


# ══════════════════════════════════════════════════════════════
# ⑦ 지급 대기 (계산서 그레인)
# ══════════════════════════════════════════════════════════════
def test_지급대기는_지급일이_미래인_계산서만_센다(db):
    db.add(CoupangRocketSettlement(
        invoice_seq=1, vendor_id=VID, supply_amount=Decimal("100"), vat=Decimal("10"),
        payment_amount=Decimal("110"), payment_date=date(2026, 10, 23),
    ))
    db.add(CoupangRocketSettlement(  # 이미 지급일 경과 — 채권이 아니다
        invoice_seq=2, vendor_id=VID, supply_amount=Decimal("200"), vat=Decimal("20"),
        payment_amount=Decimal("220"), payment_date=date(2026, 8, 1),
    ))
    db.commit()
    st = _stage(compute_rocket_pipeline(db, VID, TODAY), "await_payment")
    assert st["invoice_count"] == 1
    assert st["amount"] == Decimal("110")
    assert st["next_payment_date"] == "2026-10-23"


# ══════════════════════════════════════════════════════════════
# ⑧ 굳은 미종결 발주 재훑기 대상 (S2 — 페처가 어느 날짜를 다시 볼지)
# ══════════════════════════════════════════════════════════════
def test_굳은_미종결_발주의_발주일만_낸다(db):
    """CI(종결)는 다시 볼 필요가 없고, 마지막 수집분은 이미 최신이다."""
    from app.services.coupang.rocket_pipeline import stale_open_po_dates

    # 굳음 + 미종결 → 대상
    _po(db, 1, "RI", conf=1000, conf_qty=1, synced=OLD_SYNC,
        po_created=datetime(2025, 10, 15, 3, 0))
    # 굳음이지만 CI(종결) → 대상 아님
    _po(db, 2, "CI", conf=1000, conf_qty=1, synced=OLD_SYNC,
        po_created=datetime(2025, 11, 1, 3, 0))
    # 미종결이지만 최신 수집분 → 대상 아님
    _po(db, 3, "PA", conf=1000, conf_qty=1, synced=LAST_SYNC,
        po_created=datetime(2026, 8, 25, 3, 0))
    db.commit()
    out = stale_open_po_dates(db, VID, limit_days=400)
    assert out["dates"] == ["2025-10-15"]
    assert out["po_count"] == 1


def test_발주일은_UTC를_KST로_환산해_낸다(db):
    """`po_created_at`은 **UTC naive** 저장이다 — 환산 안 하면 자정 근처가 하루 어긋나고,
    페처가 엉뚱한 날짜를 훑어 굳은 발주가 영원히 안 갱신된다."""
    from app.services.coupang.rocket_pipeline import stale_open_po_dates

    # 2026-08-19 23:00 KST = 2026-08-19 14:00 UTC
    _po(db, 1, "PA", conf=1000, conf_qty=1, synced=OLD_SYNC,
        po_created=datetime(2026, 8, 19, 14, 0))
    # 기준선 — 이 행이 있어야 «마지막 수집일»이 정해지고 위 행이 «굳음»이 된다
    _po(db, 9, "PA", conf=1000, conf_qty=1, synced=LAST_SYNC)
    db.commit()
    assert stale_open_po_dates(db, VID, limit_days=400)["dates"] == ["2026-08-19"]


def test_limit_days_밖은_잘렸다고_말한다(db):
    """조용한 절단 금지 — 안 훑은 날짜가 있으면 페처 로그와 화면이 그걸 말해야 한다."""
    from app.services.coupang.rocket_pipeline import stale_open_po_dates

    _po(db, 1, "RI", conf=1000, conf_qty=1, synced=OLD_SYNC,
        po_created=datetime(2025, 10, 15, 3, 0))
    _po(db, 2, "PA", conf=1000, conf_qty=1, synced=OLD_SYNC,
        po_created=datetime(2026, 8, 1, 3, 0))
    _po(db, 9, "PA", conf=1000, conf_qty=1, synced=LAST_SYNC)  # 기준선
    db.commit()
    out = stale_open_po_dates(db, VID, limit_days=30)
    assert out["dates"] == ["2026-08-01"]
    assert out["truncated_before"] == "2026-07-28"
    assert out["truncated_date_count"] == 1 and out["truncated_po_count"] == 1
