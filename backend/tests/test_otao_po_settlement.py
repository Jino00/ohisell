"""S2 — 정산 창(전월 20~당월 19) 픽업 «금액» 회귀 (계약 §4 **S2** · 체인 `발주예측` n=7).

## 무엇을 재는가

계약 원문이 요구하는 둘을 그대로 잠근다:

1. **정산 창별 픽업 합계** — 창은 전월 20일부터 당월 19일까지이고, **20일부터는 다음 창**이다.
2. **실제 지급액과의 대조** — 그런데 **이 저장소엔 지급액 원장이 없다**(prod 123개 테이블 전수
   검색 0건, 2026-08-26). 그러니 이 파일이 잠그는 것은 「일치한다」가 아니라
   **「대조 «불가»와 「대조했는데 틀렸다」가 서로 다른 상태로 남는가」**다.

## 이 파일이 특별히 지키는 것 셋 — 전부 앞 슬라이스가 실제로 밟은 지뢰다

- **prod 모양 픽스처.** n=6 적대 리뷰 P1-1은 「픽스처가 키당 1행뿐이라 0건이 잡았다」였고
  P1-3은 「prod 상태를 렌더하는 테스트가 0건」이었다. 그래서 여기 픽스처는 **prod 12선적을
  창별 합계까지 정확히 재현한다**(2026-08-26 23:2x KST 실측). `test_prod_shaped_ledger_*`가
  7개 창의 숫자를 통째로 단언하므로 창 배정이 한 칸만 밀려도 즉시 빨개진다.
- **`product`와 `material`을 합치지 않는 것.** 부자재는 **지급액엔 들어가고 S1 픽업 누계엔
  안 들어간다.** 두 모듈을 나란히 돌려 그 관계를 등식으로 잠근다 — 한쪽만 고치면 깨진다.
- **결손을 0으로 덮지 않는 것.** 신고일 없는 라인·빈 창·지급액 미제공이 전부 «0»이 아니라
  각자의 이름을 갖는지 본다(계약 §2-8).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoPurchaseOrder,
    OtaoPurchaseOrderLine,
)
from app.services.otao_po.roster import build_roster
from app.services.otao_po.settlement import build_settlement, settlement_window_of


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


# ── prod 실측 (2026-08-26 23:2x KST, `import_shipment` 12건 / `import_invoice_line` 158줄) ──
# (신고일, status, 상품수량, 상품 CNY, 부자재수량)  ※부자재 단가는 전건 0.8 CNY
_PROD_SHIPMENTS = [
    (date(2026, 1, 27), "draft", 2700, 34355, 4000),
    (date(2026, 2, 5), "confirmed", 4360, 56702, 2500),
    (date(2026, 3, 19), "confirmed", 850, 10720, 7000),
    (date(2026, 4, 2), "confirmed", 1150, 17180, 0),
    (date(2026, 4, 27), "confirmed", 1000, 14305, 3000),
    (date(2026, 5, 11), "confirmed", 1500, 19350, 0),
    (date(2026, 5, 18), "confirmed", 2000, 24750, 0),
    (date(2026, 6, 15), "confirmed", 2100, 26670, 3000),
    (date(2026, 6, 30), "confirmed", 1800, 23360, 0),
    (date(2026, 7, 13), "confirmed", 2550, 32870, 1200),
    (date(2026, 7, 23), "confirmed", 100, 1220, 12000),
    (date(2026, 8, 18), "confirmed", 1650, 21180, 2400),
]

# prod 실측 창별 합계 — 이 표가 이 파일의 핵심 단언이다.
# 창키: (선적, 상품수량, 상품CNY, 부자재수량, 부자재CNY, 합계CNY)
_PROD_WINDOWS = {
    "2026-02": (2, 7060, 91057, 6500, 5200, 96257),
    "2026-03": (1, 850, 10720, 7000, 5600, 16320),
    "2026-04": (1, 1150, 17180, 0, 0, 17180),
    "2026-05": (3, 4500, 58405, 3000, 2400, 60805),
    "2026-06": (1, 2100, 26670, 3000, 2400, 29070),
    "2026-07": (2, 4350, 56230, 1200, 960, 57190),
    "2026-08": (2, 1750, 22400, 14400, 11520, 33920),
}


def _product_lines(quantity: int, amount: int) -> list[tuple[int, Decimal]]:
    """(수량, 금액)을 **정확히** 만드는 두 줄로 쪼갠다 — 단가 반올림으로 금액이 틀어지면
    이 파일이 재려는 것(창 합계)이 아니라 픽스처의 오차를 재게 된다.

    `p = floor(금액/수량)`으로 두면 `p`짜리 줄과 `p+1`짜리 줄의 조합이 항상 존재한다:
    수량 Q, 금액 C에 대해 `(p+1)`짜리 수량 = `C - p*Q`, 나머지가 `p`짜리 수량이다.
    """
    p = amount // quantity
    hi = amount - p * quantity  # 단가 p+1 을 받는 수량
    lo = quantity - hi
    out = []
    if lo:
        out.append((lo, Decimal(p)))
    if hi:
        out.append((hi, Decimal(p + 1)))
    return out


def _seed_prod_shaped(session) -> None:
    for i, (decl, status, p_qty, p_cny, m_qty) in enumerate(_PROD_SHIPMENTS, start=1):
        sh = ImportShipment(
            hbl_no=f"SETR{i:010d}",
            declaration_date=decl,
            eta=decl,
            status=status,
            currency="CNY",
            fx_rate=Decimal("209.88"),
        )
        session.add(sh)
        session.flush()
        seq = 0
        for qty, price in _product_lines(p_qty, p_cny):
            seq += 1
            session.add(
                ImportInvoiceLine(
                    shipment_id=sh.id, seq=seq, item_name=f"Glass_item{i}_{seq}",
                    quantity=Decimal(qty), unit_price_foreign=price, line_type="product",
                )
            )
        if m_qty:
            seq += 1
            session.add(
                ImportInvoiceLine(
                    shipment_id=sh.id, seq=seq, item_name="cleaning kits",
                    quantity=Decimal(m_qty), unit_price_foreign=Decimal("0.8"),
                    line_type="material",
                )
            )
    session.commit()


def _shipment(session, decl, *, status="confirmed", hbl="HBL-X"):
    sh = ImportShipment(
        hbl_no=hbl, declaration_date=decl, status=status,
        currency="CNY", fx_rate=Decimal("200"),
    )
    session.add(sh)
    session.flush()
    return sh


def _line(session, sh, *, qty, price, line_type="product", name="Glass_x", seq=1):
    session.add(
        ImportInvoiceLine(
            shipment_id=sh.id, seq=seq, item_name=name,
            quantity=Decimal(qty), unit_price_foreign=Decimal(str(price)),
            line_type=line_type,
        )
    )


# ── ① 창 경계 — 20일부터 다음 창이다 ────────────────────────────────────


@pytest.mark.parametrize(
    "day, expected",
    [
        (date(2026, 8, 19), "2026-08"),  # 창 끝 = 지급일 당일
        (date(2026, 8, 20), "2026-09"),  # ★여기서 창이 넘어간다
        (date(2026, 8, 1), "2026-08"),
        (date(2026, 1, 19), "2026-01"),
        (date(2026, 1, 20), "2026-02"),
    ],
)
def test_window_boundary_is_the_twentieth(day, expected):
    assert settlement_window_of(day) == expected


def test_december_twentieth_rolls_into_next_january():
    """연말 경계에서 연도가 같이 넘어가야 한다 — 안 넘으면 12월 하순 픽업이 사라진다."""
    assert settlement_window_of(date(2026, 12, 20)) == "2027-01"
    assert settlement_window_of(date(2026, 12, 19)) == "2026-12"


def test_window_bounds_are_the_twentieth_to_the_nineteenth(session):
    _line(session, _shipment(session, date(2026, 8, 18)), qty=10, price=1)
    session.commit()
    w = build_settlement(session).windows[0]
    assert (w.key, w.start, w.end) == ("2026-08", date(2026, 7, 20), date(2026, 8, 19))


# ── ② prod 모양 — 창별 합계를 통째로 잠근다 ─────────────────────────────


def test_prod_shaped_ledger_reproduces_every_window(session):
    """prod 12선적을 그대로 넣으면 7개 창의 숫자가 실측과 일치해야 한다.

    창 배정이 한 칸만 밀려도(예: 20일 경계를 19일로 잘못 잡으면) 이 단언이 즉시 깨진다.
    """
    _seed_prod_shaped(session)
    got = {
        w.key: (
            w.shipments,
            int(w.product_quantity),
            int(w.product_amount_cny),
            int(w.material_quantity),
            int(w.material_amount_cny),
            int(w.total_amount_cny),
        )
        for w in build_settlement(session).windows
    }
    assert got == _PROD_WINDOWS


def test_prod_shaped_totals_match_the_invoice_grand_total(session):
    """라인에서 다시 쌓은 합계가 prod 실측 총액과 같아야 한다(310,742 CNY)."""
    _seed_prod_shaped(session)
    t = build_settlement(session).totals
    assert int(t["product_amount_cny"]) == 282662
    assert int(t["material_amount_cny"]) == 28080
    assert int(t["total_amount_cny"]) == 310742
    assert int(t["product_quantity"]) == 21760
    assert int(t["material_quantity"]) == 35100
    assert t["shipments"] == 12


# ── ③ product와 material을 합치지 않는다 ────────────────────────────────


def test_material_is_split_out_and_never_folded_into_product(session):
    sh = _shipment(session, date(2026, 8, 18))
    _line(session, sh, qty=100, price=10, seq=1)
    _line(session, sh, qty=50, price=2, line_type="material", name="cleaning kits", seq=2)
    session.commit()
    w = build_settlement(session).windows[0]
    assert (int(w.product_quantity), int(w.product_amount_cny)) == (100, 1000)
    assert (int(w.material_quantity), int(w.material_amount_cny)) == (50, 100)
    # 합계는 준다 — 합계«만» 주지 않는 것이 요점이다.
    assert int(w.total_amount_cny) == 1100


def test_unknown_line_type_is_not_folded_into_product(session):
    """미분류를 판매 SKU로 접으면 「모름」이 「상품」으로 승격된다(모델 docstring의 금지선)."""
    sh = _shipment(session, date(2026, 8, 18))
    _line(session, sh, qty=10, price=3, line_type="unknown", seq=1)
    session.commit()
    w = build_settlement(session).windows[0]
    assert int(w.product_amount_cny) == 0
    assert (int(w.other_quantity), int(w.other_amount_cny)) == (10, 30)
    assert int(w.total_amount_cny) == 30


def test_payment_side_and_s1_pickup_side_differ_by_exactly_the_material(session):
    """★두 모듈을 같이 돌려 관계를 등식으로 잠근다.

    S1의 픽업 누계(`build_roster`)는 `line_type='product'`만 센다. S2의 지급액은 부자재까지
    포함한다. 그러니 **S2 상품수량 == S1 픽업 + S1 미매핑**이어야 하고, 부자재는 S1 어디에도
    없어야 한다. 한쪽만 고치면 이 등식이 깨진다 — 그게 이 테스트의 존재 이유다.
    """
    sh = _shipment(session, date(2026, 8, 18))
    _line(session, sh, qty=300, price=10, name="Glass_iP15 pro", seq=1)
    _line(session, sh, qty=80, price=10, name="For iPhone 15 Pro", seq=2)
    _line(session, sh, qty=2400, price="0.8", line_type="material", name="cleaning kits", seq=3)
    po = OtaoPurchaseOrder(
        serial="20260601-1", order_date=date(2026, 6, 1), source_kind="ecount",
        source_file="2026년/x.PDF", content_sha256="sha-1", is_authoritative=True,
    )
    session.add(po)
    session.flush()
    session.add(
        OtaoPurchaseOrderLine(
            order_id=po.id, seq=1, product_code="GAPIP15PR",
            name_en="Glass_iP15 pro", quantity=500,
        )
    )
    session.add_all([
        OtaoItemNameMap(raw_name="Glass_iP15 pro", product_code="GAPIP15PR", match_kind="exact_en"),
        OtaoItemNameMap(raw_name="For iPhone 15 Pro", product_code=None, match_kind="unmatched"),
    ])
    session.commit()

    roster = build_roster(session)
    w = build_settlement(session).windows[0]

    assert int(w.product_quantity) == roster.totals["picked"] + roster.totals["unmapped_qty"]
    # 부자재는 S1의 어느 칸에도 없다 — 있으면 예약 잔량이 조용히 깎인다.
    assert roster.totals["picked"] == 300
    assert "cleaning kits" not in roster.unmapped
    # 그런데 지급액에는 들어 있다.
    assert int(w.material_amount_cny) == 1920


# ── ④ 결손을 0으로 덮지 않는다 ──────────────────────────────────────────


def test_missing_declaration_date_is_reported_not_zeroed(session):
    sh_ok = _shipment(session, date(2026, 8, 18), hbl="HBL-OK")
    _line(session, sh_ok, qty=10, price=1)
    sh_bad = _shipment(session, None, hbl="HBL-NODATE")
    _line(session, sh_bad, qty=7, price=3)
    session.commit()
    s = build_settlement(session)
    assert s.unassigned_lines == 1
    assert int(s.unassigned_amount_cny) == 21
    # 창 합계엔 안 섞였다.
    assert int(s.totals["total_amount_cny"]) == 10
    assert any("창에도 못 넣은" in n for n in s.notes)


def test_empty_interior_window_is_listed_as_zero_not_dropped(session):
    """픽업이 없던 달이 목록에서 «사라지면» 화면이 그 달을 안 보여 준다 — 0으로 서 있어야 한다."""
    _line(session, _shipment(session, date(2026, 3, 2), hbl="A"), qty=10, price=1)
    _line(session, _shipment(session, date(2026, 6, 2), hbl="B"), qty=10, price=1)
    session.commit()
    keys = [w.key for w in build_settlement(session).windows]
    assert keys == ["2026-03", "2026-04", "2026-05", "2026-06"]
    empty = [w for w in build_settlement(session).windows if w.key == "2026-04"][0]
    assert (empty.shipments, int(empty.total_amount_cny)) == (0, 0)


def test_empty_ledger_says_so_instead_of_showing_zero_windows(session):
    s = build_settlement(session)
    assert s.windows == []
    assert s.ledger_start is None
    assert any("픽업 0이 아니다" in n for n in s.notes)


def test_ledger_window_is_declared(session):
    _seed_prod_shaped(session)
    s = build_settlement(session)
    assert (s.ledger_start, s.ledger_end) == (date(2026, 1, 27), date(2026, 8, 18))
    assert any("원장이 모르는 것" in n for n in s.notes)


# ── ⑤ draft·경계 선적을 자백한다 ────────────────────────────────────────


def test_draft_shipment_is_included_but_named(session):
    """빼면 픽업이 축소되고, 말없이 넣으면 확정된 창과 구별이 사라진다."""
    _seed_prod_shaped(session)
    s = build_settlement(session)
    feb = [w for w in s.windows if w.key == "2026-02"][0]
    assert len(feb.draft_shipment_ids) == 1
    assert feb.shipments == 2  # draft를 «빼지» 않았다
    assert int(feb.total_amount_cny) == 96257  # 합계에 들어 있다
    assert s.totals["draft_shipments"] == 1
    assert any("draft" in n for n in s.notes)


def test_boundary_shipments_are_named_because_pickup_date_is_missing(session):
    """창 경계 ±2일 선적은 실제 정산 창이 밀렸을 수 있다 — 지목해야 대조 불일치를 설명한다.

    prod 실측에서 해당하는 것은 3건: 2026-03-19(창 끝 당일) · 2026-05-18(끝 −1) ·
    2026-08-18(끝 −1). 나머지 9건은 경계에서 3일 이상 떨어져 있다.
    """
    _seed_prod_shaped(session)
    s = build_settlement(session)
    flagged = {w.key: len(w.boundary_shipment_ids) for w in s.windows if w.boundary_shipment_ids}
    assert flagged == {"2026-03": 1, "2026-05": 1, "2026-08": 1}
    assert s.totals["boundary_shipments"] == 3
    assert any("OTAO 픽업일" in n for n in s.notes)


# ── ⑥ 지급액 대조 — 「불가」와 「불일치」는 다른 상태다 ──────────────────


def test_without_payments_reconciliation_is_impossible_not_failed(session):
    """★이 파일에서 가장 중요한 단언이다.

    지급액 원장이 없는데 `reconciled=False`(불일치)로 적으면 **없는 사실을 만든 것**이고,
    `True`로 적으면 거짓 확신이다. 답은 `None`뿐이다.
    """
    _seed_prod_shaped(session)
    s = build_settlement(session)
    assert all(w.reconciled is None for w in s.windows)
    assert all(w.payment_actual_cny is None for w in s.windows)
    assert all(w.difference_cny is None for w in s.windows)
    assert s.reconciliation["source"] == "none"
    assert s.reconciliation["windows_compared"] == 0
    assert s.reconciliation["windows_matched"] == 0
    assert any("대조 불가" in n and "불일치가 아니다" in n for n in s.notes)


def test_one_supplied_payment_satisfies_the_contract(session):
    """계약 §4 S2가 요구하는 것은 «1개 창 이상» 대조 일치다."""
    _seed_prod_shaped(session)
    s = build_settlement(session, payments={"2026-08": 33920})
    aug = [w for w in s.windows if w.key == "2026-08"][0]
    assert aug.reconciled is True
    assert int(aug.difference_cny) == 0
    assert s.reconciliation["windows_matched"] == 1
    assert s.reconciliation["matched_keys"] == ["2026-08"]
    assert s.reconciliation["source"] == "supplied"
    # 값을 안 준 창은 여전히 «불가»다 — 하나 맞았다고 나머지가 맞은 게 아니다.
    assert [w.reconciled for w in s.windows if w.key != "2026-08"] == [None] * 6


def test_mismatch_reports_the_difference_instead_of_hiding_it(session):
    _seed_prod_shaped(session)
    s = build_settlement(session, payments={"2026-08": 30000})
    aug = [w for w in s.windows if w.key == "2026-08"][0]
    assert aug.reconciled is False
    assert int(aug.difference_cny) == 3920
    assert s.reconciliation["windows_matched"] == 0
    assert s.reconciliation["mismatched"][0]["key"] == "2026-08"


def test_payment_for_an_unknown_window_does_not_invent_a_window(session):
    """원장이 모르는 창에 지급액만 들어와도 창을 지어내지 않는다."""
    _seed_prod_shaped(session)
    s = build_settlement(session, payments={"2025-11": 100})
    assert [w.key for w in s.windows] == list(_PROD_WINDOWS)
    assert s.reconciliation["windows_compared"] == 0


# ── ⑦ 적대 리뷰 1R 상환분 (PR #486) ─────────────────────────────────────


def test_january_window_rolls_back_into_the_previous_december(session):
    """★리뷰 변이 M6 생존분 — 1월 창의 시작이 «전년» 12월 20일이어야 한다.

    연도를 안 되돌리면 창 시작이 창 «끝»보다 뒤로 가고(2026-12-20 ~ 2026-01-19), 그러면
    1월 창의 픽업이 통째로 사라지거나 엉뚱한 창에 붙는다. 전건 초록이던 자리라 잠근다.
    """
    _line(session, _shipment(session, date(2026, 1, 5), hbl="JAN"), qty=10, price=1)
    session.commit()
    w = build_settlement(session).windows[0]
    assert (w.key, w.start, w.end) == ("2026-01", date(2025, 12, 20), date(2026, 1, 19))
    assert w.start < w.end, "창 시작이 끝보다 뒤면 그 창은 아무것도 담지 못한다"


def test_unclassified_lines_are_confessed_in_notes(session):
    """★적대 리뷰 P1-1 — 미분류가 합계엔 들어가는데 상품·부자재 어느 칸에도 없다.

    `line_type` 기본값이 `unknown`이고 분류는 사람이 나중에 하므로 **갓 적재된 선적은 전부
    이 상태다.** 화면이 이 사실을 말하지 않으면 「상품 0 · 부자재 0」인데 픽업 합계만 서 있는
    표가 되고, 그 차이를 설명하는 글자가 한 자도 없다.
    """
    sh = _shipment(session, date(2026, 8, 18))
    _line(session, sh, qty=5000, price=2, line_type="unknown", seq=1)
    session.commit()
    s = build_settlement(session)
    assert int(s.totals["other_amount_cny"]) == 10000
    assert any("미분류" in n for n in s.notes), "미분류를 자백하는 문장이 없다"
    # 보이는 세 칸(상품·부자재·미분류)의 합이 픽업 합계와 같아야 화면이 앞뒤가 맞는다.
    w = s.windows[0]
    assert (
        w.product_amount_cny + w.material_amount_cny + w.other_amount_cny
        == w.total_amount_cny
    )


def test_a_freshly_ingested_shipment_is_entirely_unclassified(session):
    """적재 직후 상태 자체를 재현한다 — `line_type`을 «지정하지 않으면» 전부 미분류다."""
    sh = _shipment(session, date(2026, 8, 18))
    session.add(
        ImportInvoiceLine(
            shipment_id=sh.id, seq=1, item_name="갓 적재된 줄",
            quantity=Decimal(100), unit_price_foreign=Decimal(3),
        )
    )
    session.commit()
    w = build_settlement(session).windows[0]
    assert int(w.product_amount_cny) == 0
    assert int(w.other_amount_cny) == 300
    assert int(w.total_amount_cny) == 300
