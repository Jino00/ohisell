"""OTAO 발주 원장 — 파서·품목명 사전·S1 로스터 회귀 그물 (계약 §4 S1 · 체인 `발주예측` n=5).

## 왜 이 파일이 있나 (적대 리뷰 1R의 진짜 발견)

n=4 리뷰가 변이 4종을 주입했는데 **전건이 생존**했다 — 4개를 «동시에» 적용한 채 전체 스위트가
6,631 passed / 0 failed였다. 원인은 로직이 아니라 **테스트 0건**이다. 이 파일은 그 4종을 각각
죽이는 것을 최소 크기로 삼는다(그게 곧 회귀 그물의 하한이다).

    A  `parser._int()`의 콤마 스트립 제거      → `test_parser_reads_thousands_separator`
    B  로스터 예약잔량에서 `- picked` 제거      → `test_reserved_is_ordered_minus_picked`
    C  `name_map.normalize()`의 `2ea` 규칙 제거 → `test_normalize_folds_2ea_packaging`
    D  ★표면 배선 절단 `row(code).picked += n`  → `test_picked_is_wired_from_customs_ledger`

★D가 이 파일에서 가장 중요하다. 「함수가 값을 만드나」가 아니라 **「원장의 수량이 실제로 픽업
칸까지 흐르나」**를 묻는 유일한 단언이고, 배선이 끊기면 다른 단언은 전부 초록인 채 화면의
픽업 칸만 영원히 0이 된다(전역 §4 「표면까지 가는 경로를 끊는 변이」의 테스트판).

## P1 2건의 회귀 잠금 (1R FAIL → 이 세션 수정분)

- **P1-1** `order_count`의 그레인 — `test_order_count_counts_orders_not_dates`
  초판은 `set[date]`를 세어 **같은 날 복수 발주가 1로 뭉개졌다.** `serial` 명명 규칙
  (`20260107-1`/`-2`)이 같은 날 복수 건을 전제한다.
- **P1-2** 모델↔마이그 nullable 파리티 — `test_migration_nullable_matches_model`
  모델은 NOT NULL, 마이그는 NULLABLE이라 **`create_all`로 만든 테스트 DB와 prod 스키마가
  갈라져 있었다.** 정적 대조로 잠근다(alembic 미설치 환경에서도 돈다 —
  `test_alembic_revision_integrity.py`와 같은 방식).

## 파서 픽스처는 실측 함정 5종을 그대로 담는다

`parse_po_text`가 순수 함수라 PDF·pypdf 없이 검증된다. 픽스처 문자열은 `parser.py` docstring이
적어 둔 함정(페이지별 `Q;ty` 서브토탈 / 빈 단가 행의 코드 밀림 / 줄바꿈 가짜 앵커 / 코드 자체의
줄바꿈 / 2023년 `2ea30ea` 표기)을 한 문서 안에 모아 둔 것이다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

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
from app.services.otao_po import name_map as NM
from app.services.otao_po import parser as P
from app.services.otao_po.roster import build_roster

# ─────────────────────────────────────────────────────────────────────────────
# 파서 — 실측 함정 5종
# ─────────────────────────────────────────────────────────────────────────────

# 2페이지 문서. `Q;ty`는 **페이지 서브토탈**이라 합산해야 라인합과 맞고(함정 1),
# `총합계`는 페이지마다 반복돼도 문서 전체 금액이라 마지막 하나만 쓴다.
TWO_PAGE_PO = """Purchasing order
Serial No. 20260812-1
Product Code Product Quantity Currency Unit price Total amount
Q;ty 500
GAPIP15PR 강화유리 아이폰15프로 Glass_iP15 pro 200 CNY 1.5 300
GAPIP16PR 강화유리 아이폰16프로 Glass_iP16 pro 300 CNY 2 600
총합계 900
Q;ty 1,427
GSAS24U 강화유리 갤럭시S24울트라 Glass_S24 Ultra 1,427 CNY 1 1,427
총합계 2,327
"""

# 함정 2·3·4·5를 한 문서에.
TRAP_PO = """Purchasing order
Serial No. 20231226-1
Q;ty 150
GAPIP17PR 강화유리 아이폰17프로 Privacy Glass_Ip17 CNY 19.2
GAPIP17PM 강화유리 아이폰17프로맥스 Privacy Glass_
IP17 Pro Max 100 CNY 3 300
GCAPIP15PR_15
PM 강화유리 아이폰15프로맥스 카메라 Glass_cam 20 CNY 1 20
GAPIP13 강화유리 아이폰13 Glass_iP13 2ea30ea CNY 1 30
총합계 350
"""


def test_parser_reads_thousands_separator():
    """★변이 A 킬 — `_int()`가 콤마를 벗기지 않으면 `1,427`에서 `ValueError`로 죽는다.

    콤마는 실제 발주서 표기다(`Q;ty 1,927`). 파서가 숫자 하나 때문에 통째로 죽으면
    그 발주서의 라인이 전부 원장에서 사라진다 — 조용한 발주 누락이다.
    """
    assert P._int("1,427") == 1427
    doc = P.parse_po_text(TWO_PAGE_PO)
    assert doc is not None
    assert doc["header_qty"] == 1927


def test_header_qty_sums_pages_but_amount_takes_last():
    """함정 1 — 두 헤더를 같은 방식으로 처리하면(둘 다 합산/둘 다 마지막) 검산이 깨진다."""
    doc = P.parse_po_text(TWO_PAGE_PO)
    assert doc["serial"] == "20260812-1"
    assert doc["header_qty"] == doc["line_qty_sum"] == 1927  # 500 + 1,427
    assert doc["header_amount"] == doc["line_amount_sum"] == 2327  # 마지막 총합계
    assert doc["dropped"] == []


def test_blank_price_row_does_not_shift_next_code():
    """함정 2 — 단가·수량이 통째로 빈 행이 있어도 **다음 행의 코드가 밀리지 않는다**.

    `blank_qty` 행의 수량은 **0이 아니라 None**이다. 0으로 채우면 「안 시켰다」로 읽혀
    발주 누계가 조용히 줄어든다(계약 §2-8 「데이터 없음 ≠ 0」의 파서판).
    """
    doc = P.parse_po_text(TRAP_PO)
    codes = [line["code"] for line in doc["lines"]]
    assert codes == ["GAPIP17PR", "GAPIP17PM", "GCAPIP15PR_15PM", "GAPIP13"]

    blank = doc["lines"][0]
    assert blank["blank_qty"] is True
    assert blank["qty"] is None
    assert blank["currency"] == "CNY"


def test_wrapped_english_name_is_not_a_fake_code_anchor():
    """함정 3 — 영문상품명이 줄바꿈돼 `IP17`이 줄 맨 앞에 와도 새 행으로 세지 않는다."""
    doc = P.parse_po_text(TRAP_PO)
    assert "IP17" not in [line["code"] for line in doc["lines"]]
    pm = next(line for line in doc["lines"] if line["code"] == "GAPIP17PM")
    assert pm["qty"] == 100
    assert "IP17 Pro Max" in (pm["name_en"] or "")


def test_code_split_across_lines_is_rejoined():
    """함정 4 — `GCAPIP15PR_15\\nPM`은 한 코드다. 쪼개지면 원장에 없는 SKU가 태어난다."""
    doc = P.parse_po_text(TRAP_PO)
    line = next(line for line in doc["lines"] if line["code"] == "GCAPIP15PR_15PM")
    assert line["qty"] == 20


def test_2023_ea_quantity_ignores_packaging_prefix():
    """함정 5 — `2ea30ea`에서 수량은 **30**이다(앞 `2ea`는 포장 표기)."""
    doc = P.parse_po_text(TRAP_PO)
    line = next(line for line in doc["lines"] if line["code"] == "GAPIP13")
    assert line["qty"] == 30


def test_non_purchase_order_returns_none():
    """Packing List·Invoice는 발주서가 아니다 — 121파일 중 26개가 그것이다."""
    assert P.parse_po_text("PACKING LIST\nCarton No. 1\n") is None


# ─────────────────────────────────────────────────────────────────────────────
# 품목명 사전 — Jino 확정 규칙 3 (D-INV-2)
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_folds_2ea_packaging():
    """★변이 C 킬 — 규칙 3(`2ea` = 2매입 포장)이 죽으면 같은 상품이 둘로 갈린다.

    갈리면 원장의 `Privacy Glass_iP16 Pro 2ea` 수량이 픽업 칸에 안 붙고 「매핑 필요」로
    떨어진다 — 계약 §2-9가 말하는 «조용한 발주 누락»이 정확히 그것이다.
    """
    assert NM.normalize("Privacy Glass_iP16 Pro 2ea") == NM.normalize("Privacy Glass_iP16 Pro")


def test_normalize_folds_screen_protector_suffix():
    """규칙 1 — `screen protector` 접미는 상품 구분이 아니다."""
    assert NM.normalize("Glass_Ip16 Pro screen protector") == NM.normalize("Glass_Ip16 Pro")


def test_normalize_keeps_2_5d_prefix():
    """`2.5D Clear Glass`의 `2`는 포장 수량이 아니다 — lookbehind가 이걸 지킨다.

    먹어 버리면 삼성 품목이 엉뚱한 코드에 붙는다(발주 오염). 규칙 3의 반대편 가드다.
    """
    assert NM.normalize("2.5D Clear Glass") != NM.normalize(".5D Clear Glass")
    assert "2.5d" in NM.normalize("2.5D Clear Glass")
    # ★적대 리뷰 P2-4 — 위 두 줄은 `\b`만으로도 통과한다(`2` 뒤에 `ea`가 없다). 즉 lookbehind
    #   `(?<![\d.])`가 «지키는 입력»이 하나도 없었다. 소수점 뒤 `2ea`가 그 입력이다:
    #   lookbehind를 지우면 `glass3.2ea` → `glass3.`으로 꼬리가 통째로 잘린다.
    assert NM.normalize("Glass 3.2ea") == "glass3.2ea"


def test_resolve_marks_ambiguous_instead_of_guessing():
    """한 이름 → 코드 둘이면 **고르지 않는다.** 「아마 이것」은 발주 수량의 근거가 못 된다."""
    d = NM.build_dictionary([
        {"code": "GSAS24U", "name_en": "Glass_S24 Ultra", "serial": "20260101-1"},
        {"code": "GSAS24UX2", "name_en": "Glass_S24 Ultra", "serial": "20260102-1"},
    ])
    (entry,) = NM.resolve(["Glass_S24 Ultra"], d)
    assert entry.match_kind == "ambiguous"
    assert entry.product_code is None


def test_resolve_reports_unmatched_rather_than_dropping():
    """규칙 2(공용 ≡ 단일)는 상품 지식이라 코드가 못 푼다 — 숨기지 말고 `unmatched`로 낸다."""
    d = NM.build_dictionary([
        {"code": "GAPIP15PR", "name_en": "Glass_iP15 pro", "serial": "20260101-1"},
    ])
    (entry,) = NM.resolve(["For iPhone 15/16/14Pro Privacy Tempered Glass"], d)
    assert entry.match_kind == "unmatched"
    assert entry.product_code is None


def test_resolve_separates_exact_from_normalized():
    """규칙에 얼마나 의존하는지 재려면 두 종류가 갈려 있어야 한다."""
    d = NM.build_dictionary([
        {"code": "GAPIP16PR", "name_en": "Privacy Glass_iP16 Pro", "serial": "20260101-1"},
    ])
    exact, normalized = NM.resolve(
        ["Privacy Glass_iP16 Pro", "Privacy Glass_iP16 Pro 2ea"], d
    )
    assert (exact.match_kind, exact.product_code) == ("exact_en", "GAPIP16PR")
    assert (normalized.match_kind, normalized.product_code) == ("normalized", "GAPIP16PR")


# ─────────────────────────────────────────────────────────────────────────────
# S1 로스터 — 3칸 (발주 누계 · 픽업 누계 · 예약 잔량)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정이어야 한다 — `autoflush=True`로 두면 「방금 만든 행이 안 보이는」
    #   결함을 이 파일이 원리적으로 못 잡는다(이 저장소의 알려진 함정).
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Testing() as s:
        yield s


def _order(
    session,
    serial: str,
    order_date: date | None,
    lines: list[tuple[str, int]],
    *,
    authoritative: bool = True,
) -> OtaoPurchaseOrder:
    # ★같은 `serial`이 여러 파일로 존재하는 것이 정상이다(개정본·중복 사본). 멱등 키는
    #   serial이 아니라 **파일 내용 해시**라, 픽스처도 파일마다 다른 해시를 줘야 한다.
    n = session.query(OtaoPurchaseOrder).count()
    po = OtaoPurchaseOrder(
        serial=serial,
        order_date=order_date,
        source_kind="local",
        source_file=f"{serial}_{n}.pdf",
        content_sha256=f"sha-{serial}-{n}",
        is_authoritative=authoritative,
    )
    session.add(po)
    session.flush()
    for seq, (code, qty) in enumerate(lines, start=1):
        session.add(
            OtaoPurchaseOrderLine(
                order_id=po.id, seq=seq, product_code=code, quantity=qty
            )
        )
    session.flush()
    return po


def _customs(session, declaration_date: date, lines: list[tuple[str, int]]) -> None:
    """통관 원장(계약 A′/B 소관 — 로스터는 **읽기만** 한다)."""
    sh = ImportShipment(
        hbl_no=f"HBL{declaration_date.isoformat()}",
        declaration_date=declaration_date,
        fx_rate=Decimal("200.0000"),
    )
    session.add(sh)
    session.flush()
    for seq, (item_name, qty) in enumerate(lines, start=1):
        session.add(
            ImportInvoiceLine(
                shipment_id=sh.id,
                seq=seq,
                item_name=item_name,
                quantity=Decimal(qty),
                unit_price_foreign=Decimal("1.0000"),
                line_type="product",
            )
        )
    session.flush()


def _map(session, raw_name: str, code: str | None) -> None:
    session.add(
        OtaoItemNameMap(
            raw_name=raw_name,
            product_code=code,
            match_kind="exact_en" if code else "unmatched",
        )
    )
    session.flush()


def test_picked_is_wired_from_customs_ledger(session):
    """★★변이 D 킬 — **원장 수량이 픽업 칸까지 실제로 흐르는가.**

    이 저장소에서 가장 비싸게 배운 병이 「값은 만들어지는데 사람에게 안 닿는다」이고, 그
    마지막 마디를 끊는 변이(`row(code).picked += n` 제거)는 n=4에서 **다른 단언 전부가 초록인
    채** 생존했다. 그래서 이 단언은 «픽업 칸이 0이 아니다»를 직접 요구한다.
    """
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])
    _customs(session, date(2026, 5, 1), [("Glass_iP15 pro", 120)])
    _map(session, "Glass_iP15 pro", "GAPIP15PR")

    roster = build_roster(session)
    (row,) = [r for r in roster.rows if r.product_code == "GAPIP15PR"]
    assert row.picked == 120, "통관 원장의 수량이 픽업 칸에 안 닿았다 — 배선이 끊겼다"
    assert roster.totals["picked"] == 120


def test_reserved_is_ordered_minus_picked(session):
    """★변이 B 킬 — 잔량에서 픽업을 빼지 않으면 **이미 받아 온 물량을 또 받을 수 있다고** 말한다."""
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])
    _customs(session, date(2026, 5, 1), [("Glass_iP15 pro", 120)])
    _map(session, "Glass_iP15 pro", "GAPIP15PR")

    (row,) = build_roster(session).rows
    assert (row.ordered, row.picked, row.reserved) == (500, 120, 380)


def test_reserved_may_go_negative_and_is_not_clamped(session):
    """음수는 「창이 어긋났다」는 신호다 — 0으로 깎으면 그 신호가 사라진다."""
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 100)])
    _customs(session, date(2026, 5, 1), [("Glass_iP15 pro", 300)])
    _map(session, "Glass_iP15 pro", "GAPIP15PR")

    (row,) = build_roster(session).rows
    assert row.reserved == -200
    assert any("음수" in n for n in build_roster(session).notes)


def test_order_count_counts_orders_not_dates(session):
    """★P1-1 회귀 — 같은 날 복수 발주(`-1`/`-2`)가 **2건으로** 세어져야 한다.

    초판은 `set[date]`를 세어 1로 뭉갰다. 그 입력이 실재한다는 근거는 `serial` 명명 규칙과
    실측 개정본 4건이다 — **자기 문서가 자기 버그를 반증하고 있었다.**
    """
    _order(session, "20260107-1", date(2026, 1, 7), [("GAPIP15PR", 300)])
    _order(session, "20260107-2", date(2026, 1, 7), [("GAPIP15PR", 200)])

    (row,) = build_roster(session).rows
    assert row.order_count == 2, "같은 날 복수 발주가 1건으로 뭉개졌다"
    assert row.ordered == 500


def test_order_without_date_is_still_counted(session):
    """`order_date`가 NULL이어도 발주는 발주다 — 건수에서 조용히 사라지면 안 된다."""
    _order(session, "unknown-1", None, [("GAPIP15PR", 50)])
    (row,) = build_roster(session).rows
    assert row.order_count == 1
    assert row.ordered == 50


def test_non_authoritative_orders_are_excluded(session):
    """D-INV-3 — 개정 전 판본을 같이 세면 발주가 부풀려진다(파일 95 ↔ 고유 번호 66)."""
    _order(session, "20260107-2", date(2026, 1, 7), [("GAPIP15PR", 5700)], authoritative=False)
    _order(session, "20260107-2", date(2026, 1, 7), [("GAPIP15PR", 1300)])

    (row,) = build_roster(session).rows
    assert row.ordered == 1300
    assert row.order_count == 1


def test_out_of_window_orders_are_separated_not_dropped(session):
    """창 밖 발주는 잔량에서 빼되 **따로 실어 보낸다** — 사라지면 화면이 자백할 수 없다."""
    _customs(session, date(2026, 1, 27), [("Glass_iP15 pro", 100)])
    _map(session, "Glass_iP15 pro", "GAPIP15PR")
    _order(session, "20230426-1", date(2023, 4, 26), [("GAPIP15PR", 4000)])
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])

    roster = build_roster(session)
    (row,) = roster.rows
    assert roster.window_start == date(2026, 1, 27)
    assert row.ordered == 500
    assert row.out_of_window_ordered == 4000
    assert row.reserved == 400
    assert roster.totals["out_of_window_ordered"] == 4000


def test_window_start_is_the_earliest_shipment_not_the_latest(session):
    """★적대 리뷰 P2-3 — 창 시작일은 원장의 **가장 이른** 통관일이다.

    이 단언이 없으면 `min`↔`max`를 바꿔도 전건 초록이었다. 픽스처가 전부 선적 **1건**이라
    min ≡ max였기 때문이다 — 로직이 아니라 표본이 만든 사각지대다.

    ★prod 실측(2026-08-26 07:56 KST): 선적 **12건 · 2026-01-27 ~ 2026-08-18**. `max`였다면
    창이 08-18로 밀려 **거의 모든 발주가 「창 밖」으로 떨어진다** — 잔량 칸이 통째로 무의미해진다.
    """
    _customs(session, date(2026, 1, 27), [("Glass_iP15 pro", 100)])
    _customs(session, date(2026, 8, 18), [("Glass_iP15 pro", 50)])
    _map(session, "Glass_iP15 pro", "GAPIP15PR")
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])

    roster = build_roster(session)
    assert roster.window_start == date(2026, 1, 27)
    (row,) = roster.rows
    # 06-01 발주는 창 «안»이다. `max`(08-18)였다면 out_of_window로 떨어진다.
    assert (row.ordered, row.out_of_window_ordered) == (500, 0)
    assert row.picked == 150


def test_unmapped_ledger_names_are_surfaced_with_quantity(session):
    """계약 §2-9 — 못 붙인 품목명을 조용히 빼면 그만큼이 발주 누락이 된다."""
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])
    _customs(
        session,
        date(2026, 5, 1),
        [("Glass_iP15 pro", 120), ("For iPhone 15/16/14Pro Privacy Tempered Glass", 80)],
    )
    _map(session, "Glass_iP15 pro", "GAPIP15PR")
    _map(session, "For iPhone 15/16/14Pro Privacy Tempered Glass", None)

    roster = build_roster(session)
    assert roster.unmapped == {"For iPhone 15/16/14Pro Privacy Tempered Glass": 80}
    assert roster.totals["unmapped_qty"] == 80
    assert roster.totals["unmapped_name_count"] == 1
    assert any("매핑" in n or "안 붙었다" in n for n in roster.notes)


def test_material_lines_are_not_counted_as_pickup(session):
    """부자재(`line_type='material'`)는 판매 SKU가 아니다 — 픽업 누계에 섞이면 안 된다."""
    _order(session, "20260601-1", date(2026, 6, 1), [("GAPIP15PR", 500)])
    _customs(session, date(2026, 5, 1), [("Glass_iP15 pro", 120)])
    session.add(
        ImportInvoiceLine(
            shipment_id=session.query(ImportShipment).one().id,
            seq=99,
            item_name="Glass_iP15 pro",
            quantity=Decimal(9999),
            unit_price_foreign=Decimal("1.0000"),
            line_type="material",
        )
    )
    session.flush()
    _map(session, "Glass_iP15 pro", "GAPIP15PR")

    (row,) = build_roster(session).rows
    assert row.picked == 120


# ─────────────────────────────────────────────────────────────────────────────
# P1-2 — 모델 ↔ 마이그레이션 파리티
# ─────────────────────────────────────────────────────────────────────────────

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "otao1po4n4a_add_otao_purchase_order_ledger.py"
)

_OTAO_TABLES = (OtaoPurchaseOrder, OtaoPurchaseOrderLine, OtaoItemNameMap)


def _migration_nullable() -> dict[tuple[str, str], bool]:
    """마이그레이션 파일에서 `(table, column) -> nullable`을 정적으로 읽는다.

    alembic 로더를 쓰지 않는다 — `test_alembic_revision_integrity.py`와 같은 이유로
    alembic 미설치 환경에서도 돌아야 한다.
    """
    text = _MIGRATION.read_text(encoding="utf-8")
    out: dict[tuple[str, str], bool] = {}
    for block in re.finditer(
        r"op\.create_table\(\s*\"([a-z_]+)\",(.*?)\n    \)", text, re.S
    ):
        table, body = block.group(1), block.group(2)
        for col in re.finditer(r"sa\.Column\(\s*\"([a-z_0-9]+)\"(.*?)\),\n", body, re.S):
            name, rest = col.group(1), col.group(2)
            # `nullable=` 미기재는 alembic 기본값(True)이다 — 명시 안 한 것도 드리프트다.
            m = re.search(r"nullable\s*=\s*(True|False)", rest)
            out[(table, name)] = (m.group(1) == "True") if m else True
    return out


def test_migration_covers_every_model_column():
    """마이그레이션에 없는 컬럼이 모델에 있으면 prod에서만 죽는다."""
    mig = _migration_nullable()
    missing = [
        (m.__tablename__, c.name)
        for m in _OTAO_TABLES
        for c in m.__table__.columns
        if (m.__tablename__, c.name) not in mig
    ]
    assert not missing, f"마이그레이션에 없는 컬럼: {missing}"


def test_migration_nullable_matches_model():
    """★P1-2 회귀 — 갈라지면 `create_all`(테스트)과 prod 스키마가 다른 물건이 된다.

    n=4에서 `parsed_at`·`created_at`·`updated_at`이 모델 NOT NULL / 마이그 NULLABLE이었다.
    이후 `alembic revision --autogenerate`가 그 드리프트를 잡아 불필요한 ALTER를 낸다.
    """
    mig = _migration_nullable()
    drift = [
        (m.__tablename__, c.name, c.nullable, mig[(m.__tablename__, c.name)])
        for m in _OTAO_TABLES
        for c in m.__table__.columns
        if (m.__tablename__, c.name) in mig
        and bool(c.nullable) != mig[(m.__tablename__, c.name)]
    ]
    assert not drift, f"(table, column, model_nullable, migration_nullable) 드리프트: {drift}"
