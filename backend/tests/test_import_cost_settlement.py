# test_import_cost_settlement.py — 자금정산서 양식 (2026-08-25 신설)
#
# ★왜 파일을 따로 두나: 이건 통관경비서와 **다른 서류**다. 같은 파일에 섞으면 「경비서 테스트」가
#   두 양식을 뭉뚱그려, 한쪽이 깨져도 다른 쪽 초록에 가려진다.
#
# ★픽스처 규율(교훈 #292, `test_import_cost_parse.py` 머리말과 같다): 아래 텍스트는 내가 «정리해
#   준» 것이 아니라 **`extract_pdf_text(..., layout=True)`가 실파일에서 실제로 뽑은 그대로**다.
#   실파일: `2. IV & PL/20260721 입금건/20260722_주식회사 오하이테크 SETR2607220324.pdf`.
#   정리된 텍스트로 검증하면 실파일에서 0줄을 뽑는다 — 초판 파서가 그랬다.
from __future__ import annotations

from decimal import Decimal as D

import pytest

import app.services.import_cost.parser as P
from app.services.import_cost.parser import (
    CustomsDocParseError,
    detect_expense_form,
    parse_customs_expense,
    parse_expense_document,
    parse_settlement_statement,
)

REAL_SETTLEMENT_TEXT = """\
                              자금정산서 (수입)


    상    호  (주)세아트랜스                          상    호  관세법인 천지인 인천지사
 받  등록번호    634-88-00553  담당자               공 등록번호    121-85-43259        2026-07-24작성일자
 는  전    화  031-653-9461     031-653-9463팩스 급 통관담당    이건희    T             F
    실 화 주   주식회사 오하이테크                        정산담당    이건희  032-887-5144  032-887-5147TF
 자  주    소  인천광역시 연수구 새말로96번길               자 주    소  인천광역시 중구 서해대로 366, 8층
            30,302호(연수동,이강빌딩                          812호(신흥동3가, 정석빌딩 신관)

 입항일자        2026-07-23  신고번호      44598-26-650964M          과세금액(₩)     2,407,850
 신고일자        2026-07-23  B/L No    SETR2607220324            신고금액($)     1,607
 수리일자        2026-07-23  품    명    SCREEN PROTECTOR          적용환율        221.1600
 수    량             11 CT장 치 장     백마보세창고                    결제금액          TT    CNY
 총 중 량          179.80KG 해외거래처     SHENZHEN OTAO TECHNOLOGY CO LTD       10,820.00
 File No1                          File No2                  총세액         257,500
 적요사항 :


    비 용 명          공급가         부가세         합   계                    비    고
관세              0       0     15,200
부가세             0       0    242,300
통관수수료        25,000     2,500     27,500
비용합계(a)                                       285,000
미 수 금(b)                                            0
이월잔액(c)                                             0
입금금액(d)                                       285,000 [ 입금일자 : 2026-07-23 ]
잔액송금(e)                                             0
차액=(c+d)-(a+b+e)                                   0
"""


def test_detect_form_picks_settlement():
    """양식 판별은 «내용»이 한다 — 파일명이 아니다.

    실측: 같은 자금정산서가 `…수입신고필증 외 _SETR….pdf` · `…_정산서.pdf` ·
    `20260722_주식회사 오하이테크 SETR….pdf` 세 이름으로 저장돼 있었고, 「정산서」로 이름을
    거르면 마지막 하나가 통째로 샌다.
    """
    assert detect_expense_form(REAL_SETTLEMENT_TEXT) == "settlement"


def test_detect_form_says_none_for_other_documents():
    """송금증·PI는 «경비 서류가 아니다»라고 말해야 한다 — 「비용 라인을 못 찾음」이 아니라.

    폴더 일괄 경로는 송금증 4건·PI 3건을 같이 집는다. 사유가 틀리면 사람이 엉뚱한 걸 고친다.
    """
    assert detect_expense_form("해외송금 확인서\nREF-NO 060654OR2601102\nCNY 53,725.00") is None


def test_settlement_header_reads_real_values():
    """헤더가 실제 값으로 채워진다. 옛 경로에서는 이 다섯 칸이 **전부 None**이었다."""
    ex = parse_settlement_statement(REAL_SETTLEMENT_TEXT)
    assert ex.hbl_no == "SETR2607220324"
    assert ex.declaration_no == "44598-26-650964M"
    assert ex.fx_rate == D("221.1600")
    assert ex.customs_value_krw == D("2407850")
    assert ex.declaration_date.isoformat() == "2026-07-23"
    assert ex.eta.isoformat() == "2026-07-23"
    assert ex.carton_count == 11
    assert ex.gross_weight_kg == D("179.80")
    assert ex.currency == "CNY"
    assert ex.declared_inv_value == D("10820.00")
    assert ex.shipper_name.startswith("SHENZHEN OTAO")


def test_settlement_cost_rows_and_flags():
    """세 행이 나오고, 금액 열과 배부 플래그가 규약대로다.

    열 머리글이 `공급가 / 부가세 / 합계`이고 세 번째가 실제 금액이다.
    관세·부가세는 공급가·부가세 칸이 0이라 **합계를 공급가 칸에** 넣는다(배부기 규약).
    """
    ex = parse_settlement_statement(REAL_SETTLEMENT_TEXT)
    by = {c.item_name: c for c in ex.cost_lines}
    assert set(by) == {"관세", "부가세", "통관수수료"}

    assert by["관세"].supply_amount == D("15200")
    assert by["관세"].is_duty is True and by["관세"].is_costing is True

    assert by["부가세"].supply_amount == D("242300")
    assert by["부가세"].is_costing is False  # 배부 제외 — 매입세액 공제 대상이라 원가가 아니다

    assert by["통관수수료"].supply_amount == D("25000")
    assert by["통관수수료"].tax_amount == D("2500")
    assert by["통관수수료"].is_duty is False  # 「통관…」으로 시작하므로 관세가 아니다


def test_settlement_totals_match_the_document():
    """서류가 스스로 검산된다 — 라인 합 == 비용합계(a) == 285,000."""
    ex = parse_settlement_statement(REAL_SETTLEMENT_TEXT)
    assert sum((c.total for c in ex.cost_lines), D(0)) == D("285000")
    # 부가세 = (과세금액 + 관세) × 10% (반올림 차 5원 이내)
    expected_vat = (ex.customs_value_krw + D("15200")) / 10
    got_vat = next(c for c in ex.cost_lines if c.item_name == "부가세").supply_amount
    assert abs(expected_vat - got_vat) <= 10


def test_settlement_refuses_when_totals_disagree():
    """검산이 어긋나면 **실패한다** — 조용히 틀린 값을 채우지 않는다."""
    broken = REAL_SETTLEMENT_TEXT.replace("285,000", "999,999")
    with pytest.raises(CustomsDocParseError, match="자기검산 불일치"):
        parse_settlement_statement(broken)


def test_settlement_refuses_when_verification_anchor_is_missing():
    """★검산 «기준»을 못 찾으면 그것도 실패다.

    옛 파서가 이 서류에서 조용히 틀린 값을 낸 뿌리가 정확히 이것이다 — 이 양식엔 「소계」가
    없어 `subtotal`이 None이 됐고, 검산이 **통째로 건너뛰어졌다**. 검산 없는 성공은 성공이 아니다.
    """
    no_anchor = REAL_SETTLEMENT_TEXT.replace("비용합계(a)", "비 용 총 액")
    with pytest.raises(CustomsDocParseError, match="검산할 수 없다"):
        parse_settlement_statement(no_anchor)


def test_old_parser_on_this_document_is_wrong_which_is_why_dispatch_exists():
    """★이 서류를 옛 파서에 태우면 **쓸 수 있는 값이 안 나온다** — 그게 배선이 필요한 이유다.

    이 테스트는 「옛 파서를 고쳐라」가 아니라 「자금정산서를 저기로 보내지 마라」를 못 박는다.
    `detect_expense_form`이 `settlement`을 돌려주는 한 이 경로는 안 밟힌다.

    ★실측 두 갈래를 다 적어 둔다(둘 다 «틀린 값을 조용히 내는 것»보다는 낫지만 둘 다 못 쓴다):
      - layout 텍스트를 넣으면 비용 라인을 하나도 못 찾아 **예외를 던진다**(아래).
      - 기본 추출 텍스트를 넣으면 **예외 없이 «성공»하면서** 비용명 자리에 주소 문자열이,
        헤더 다섯 칸에 None이 들어왔다(2026-08-25 실측). 그쪽이 더 위험한 실패다.
    """
    with pytest.raises(CustomsDocParseError):
        parse_customs_expense(REAL_SETTLEMENT_TEXT)


# ──────────────────────────────────────────────
# 디스패치 — «사람에게 닿는 마지막 배선» (적대 리뷰 1R 변이 1·2가 SURVIVED한 자리)
# ──────────────────────────────────────────────
# ★1R에서 `parse_expense_document`의 양식 분기를 통째로 지워도 **94개 테스트가 전부 초록**이었다.
#   `parse_settlement_statement`도 `detect_expense_form`도 각각 잠겨 있었는데, **둘을 잇는 배선을
#   아무도 안 밟았다.** 순수 함수는 잠갔는데 그 출력이 사람에게 닿는 경로는 안 잠근 것 —
#   이 저장소가 반복해 밟은 모양이다. 그래서 라우터가 실제로 부르는 입구를 여기서 잠근다.


class _FakePdf:
    """`extract_pdf_text`를 대신한다 — layout 여부에 따라 다른 텍스트를 준다.

    진짜 PDF 바이트를 만들지 않는 이유: 잠그려는 것은 **PDF 해독**이 아니라 **어느 파서로
    보내는가**다. 해독은 위 단위 테스트가 이미 실파일 출력으로 잠갔다.
    """

    def __init__(self, layout_text: str, plain_text: str = ""):
        self.layout_text, self.plain_text = layout_text, plain_text
        self.calls: list[bool] = []

    def __call__(self, data, *, layout=False):
        self.calls.append(layout)
        text = self.layout_text if layout else self.plain_text
        if not text.strip():
            raise CustomsDocParseError("PDF에서 글자를 찾지 못했다 — 스캔 이미지 PDF로 보인다.")
        return text


def test_dispatch_sends_settlement_to_the_settlement_parser(monkeypatch):
    """★자금정산서를 넣으면 **자금정산서 파서의 결과**가 나온다 — 옛 파서로 새면 값이 틀린다."""
    monkeypatch.setattr(P, "extract_pdf_text", _FakePdf(REAL_SETTLEMENT_TEXT))
    ex = parse_expense_document(b"%PDF-fake")
    # 옛 파서로 샜다면 이 셋은 전부 None이고 비용명은 주소 문자열이 된다(1R 실측).
    assert ex.hbl_no == "SETR2607220324"
    assert ex.fx_rate == D("221.1600")
    assert {c.item_name for c in ex.cost_lines} == {"관세", "부가세", "통관수수료"}


def test_dispatch_sends_customs_expense_to_the_old_parser(monkeypatch):
    """통관예상경비는 **기본 추출 결과**로 옛 파서에 간다 — 그 경로의 입력을 흔들지 않는다.

    잠그는 것은 «어떤 텍스트를 누구에게 주는가»다: layout으로 한 번 훑어 양식을 가른 뒤,
    옛 파서에는 **기본 추출**을 준다(그 경로가 검증된 입력이 그것이기 때문이다).
    파싱 성공 여부는 여기서 묻지 않는다 — 그건 `test_import_cost_parse.py`가 실픽스처로 잠갔다.
    """
    fake = _FakePdf(layout_text="HBL NO  SETR9999999999", plain_text="HBL NO / SETR9999999999")
    monkeypatch.setattr(P, "extract_pdf_text", fake)
    with pytest.raises(CustomsDocParseError):
        parse_expense_document(b"%PDF-fake")  # 이 가짜 텍스트엔 비용 표가 없다
    # layout=True로 양식을 가른 뒤 **기본 추출**(False)로 옛 파서를 부른다.
    assert fake.calls == [True, False], fake.calls


def test_dispatch_refuses_documents_that_are_neither(monkeypatch):
    """송금증·PI는 「경비 서류가 아니다」로 거부된다 — 「비용 라인을 못 찾음」이 아니라."""
    monkeypatch.setattr(P, "extract_pdf_text", _FakePdf("해외송금 확인서\nREF-NO 060654OR2601102"))
    with pytest.raises(CustomsDocParseError, match="경비 서류가 아니다"):
        parse_expense_document(b"%PDF-fake")


def test_dispatch_says_scan_when_there_is_no_text(monkeypatch):
    """스캔본은 「글자를 찾지 못했다」로 나간다 — OCR로 지어내지 않는다(금지선)."""
    monkeypatch.setattr(P, "extract_pdf_text", _FakePdf(""))
    with pytest.raises(CustomsDocParseError, match="글자를 찾지 못했다"):
        parse_expense_document(b"%PDF-fake")


def test_router_pdf_upload_path_is_actually_exercised(monkeypatch):
    """★라우터의 **PDF 업로드 경로**를 밟는다.

    1R 실측: `grep -rl "expense_pdf" backend/tests/` → **0건**. `/parse`의 PDF 경로는 이 PR의
    신규 테스트를 포함해 **어떤 테스트에서도 실행되지 않았고**, 그래서 라우터에서 그 분기를
    통째로 지워도 94개가 초록이었다. 폼에 채워지는 값이 «사람이 보는 표면»이므로 여기서 잠근다.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    # ★라우터는 함수 «안»에서 `from app.services.import_cost import parser as P`를 한다 —
    #   그래서 라우터 모듈이 아니라 **파서 모듈 자체**를 갈아야 그 호출부에 닿는다.
    monkeypatch.setattr(P, "extract_pdf_text", _FakePdf(REAL_SETTLEMENT_TEXT))
    client = TestClient(app)
    res = client.post(
        "/api/import-cost/parse",
        files={"expense_file": ("정산서.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["errors"] == [], body["errors"]
    assert body["header"].get("hbl_no") == "SETR2607220324"
    assert body["header"].get("fx_rate") == "221.1600"
    names = [c["item_name"] for c in body["cost_lines"]]
    assert names == ["관세", "부가세", "통관수수료"], names
