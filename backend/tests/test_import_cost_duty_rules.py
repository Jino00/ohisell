# test_import_cost_duty_rules.py — 품목별 관세율 규칙 (D-CPP-57, 2026-08-25 신설)
#
# ★이 규칙은 «추정»이 아니라 «재현»이라서 채택됐다. 그러니 테스트도 그 재현을 잠근다 —
#   아래 7건은 라이브 자금정산서에서 읽은 실제 값이고, 규칙이 그 관세를 반올림 오차 안에서
#   다시 만들어 내는지를 본다. 규칙이 흔들리면 여기서 먼저 깨진다.
from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.services.import_cost.duty_rules import (
    RATE_MATERIAL,
    RATE_PRODUCT,
    duty_rate_for,
    is_material,
    verify_against_document,
)

#: (HBL, 과세금액(원), 서류의 관세(원), [(품목명, 외화금액 CNY)])
#: CI·자금정산서 실측(2026-08-25). 품목은 CNY 금액 기준으로 유리류/부자재만 남겨 합산했다 —
#: 규칙이 보는 것이 «과세대상 비율»이므로 그 두 덩어리면 재현에 충분하다.
REAL_SHIPMENTS = [
    ("SETR2604010220", D("3754509"), D("210250"), [("Glass_Ip17", D("17180.00"))]),
    (
        "SETR2604240109",
        D("3632250"),
        D("174180"),
        [("Glass_Ip17", D("14305.00")), ("cleaning kits", D("2400.00"))],
    ),
    ("SETR2605100210", D("4175674"), D("233830"), [("Glass_Ip17", D("19350.00"))]),
    ("SETR2605170105", D("5417558"), D("303380"), [("Glass_Ip17", D("24750.00"))]),
    (
        "SETR2606140215",
        D("6592171"),
        D("338680"),
        [("Glass_Ip17", D("26670.00")), ("cleaning kits", D("2400.00"))],
    ),
    (
        "SETR2607120215",
        D("7607233"),
        D("413910"),
        [("Glass_Ip17", D("32870.00")), ("cleaning kits", D("960.00"))],
    ),
    (
        "SETR2607220324",
        D("2407850"),
        D("15200"),
        [("Glass_Ip16", D("1220.00")), ("cleaning kits", D("9600.00"))],
    ),
]


@pytest.mark.parametrize("hbl,base,duty,lines", REAL_SHIPMENTS, ids=[s[0] for s in REAL_SHIPMENTS])
def test_rule_reproduces_the_document(hbl, base, duty, lines):
    """규칙이 서류의 실제 관세를 다시 만들어 낸다 — 7건 전부, 반올림 오차 안에서."""
    v = verify_against_document(
        line_amounts_foreign=lines, customs_value_krw=base, document_duty_krw=duty
    )
    assert v.ok, f"{hbl}: {v.reason}"
    assert abs(v.diff) <= D("100")


def test_the_low_duty_shipment_is_explained_by_materials():
    """★규칙을 가장 잘 증명하는 건 — 관세가 유난히 작았던 화물.

    SETR2607220324는 관세가 15,200원뿐이라 「5.6% 고정」으로는 설명이 안 된다
    (2,407,850 × 5.6% = 134,839). 그 화물의 **88.7%가 cleaning kits(무관세)**였기 때문이고,
    규칙은 그 사실을 쓰기 때문에 15,204를 낸다. 「대충 5.6%」로는 안 나오는 숫자다.
    """
    _, base, duty, lines = REAL_SHIPMENTS[-1]
    flat = base * RATE_PRODUCT  # 전건에 5.6%를 먹였을 때
    v = verify_against_document(
        line_amounts_foreign=lines, customs_value_krw=base, document_duty_krw=duty
    )
    assert flat > duty * 8, "전제 확인: 일괄 5.6%는 실제 관세보다 훨씬 크다"
    assert v.ok and abs(v.diff) <= D("100")


def test_material_detection():
    """부자재 판정은 이름 매칭 하나다 — 그래서 검산이 필수다(모듈 docstring)."""
    assert is_material("cleaning kits") and is_material("Cleaning Kit")
    assert not is_material("Glass_Ip17")
    assert not is_material("Privacy Glass_iP16 2ea")
    assert not is_material("2.5D Clear Glass 2ea for Samsung S25")
    assert duty_rate_for("cleaning kits") == RATE_MATERIAL
    assert duty_rate_for("Glass_Ip17") == RATE_PRODUCT


def test_missing_inputs_are_not_a_pass():
    """★서류에 관세가 없으면 «대조 못 함»이지 «통과»가 아니다.

    `ok`가 True로 새면 호출부가 관세율을 채우고, 검증 없는 세율이 원가에 굳는다.
    """
    lines = [("Glass_Ip17", D("100"))]
    assert not verify_against_document(
        line_amounts_foreign=lines, customs_value_krw=D("1000"), document_duty_krw=None
    ).ok
    assert not verify_against_document(
        line_amounts_foreign=lines, customs_value_krw=None, document_duty_krw=D("56")
    ).ok
    assert not verify_against_document(
        line_amounts_foreign=[], customs_value_krw=D("1000"), document_duty_krw=D("56")
    ).ok


def test_new_material_breaks_verification_instead_of_passing_silently():
    """새 무관세 품목이 들어오면 **검산이 어긋나 사람에게 넘어간다** — 조용히 틀리지 않는다.

    이게 이 모듈이 「규칙 + 검산」 두 벌인 이유다. 규칙만 있으면 어휘가 바뀐 날부터
    조용히 과대 계상하고, 아무도 그날을 모른다.
    """
    # 서류상 관세는 무관세 품목이 절반인 화물의 것인데, 규칙은 그 품목을 몰라 전건 과세로 본다.
    v = verify_against_document(
        line_amounts_foreign=[("Glass_Ip17", D("500")), ("wet wipes", D("500"))],
        customs_value_krw=D("1000000"),
        document_duty_krw=D("28000"),  # 절반만 과세됐을 때의 관세
    )
    assert not v.ok
    assert "허용 오차" in v.reason
