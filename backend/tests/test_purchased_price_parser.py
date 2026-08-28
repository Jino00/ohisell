"""매입 단가 시트 파서 — 계약 D-CPP-63 S1.

★이 테스트가 지키는 것은 「값을 잘 읽는가」보다 **「못 읽을 때 조용히 0을 돌려주지 않는가」**다.
계약 저술 중 실사고가 정확히 그 자리였다: 열을 «위치»로 읽어 08-22판을 「원가 0행 빈 파일」로
오독했고, 그 거짓이 계약 문단이 됐다(§0-B). 발견 0건과 「읽을 수 없었다」는 다른 사실이다.
"""

from decimal import Decimal

import pytest

from app.services.cost_menu.purchased_price_parser import (
    PLACEHOLDER_MAX,
    PriceSheetError,
    detect_columns,
    parse_price_sheet,
)

# 08-07판 모양: 0 상품명(제품+옵션 통합) · 1 원가 · 이후 채널명/코드
HEADER_0807 = ["상품명", "원가", "채널명", "카페24 품목코드1", "채널명", "스스 품목코드1"]
# 08-22판 모양: 0 상품명(제품만) · 1 옵션명 — **원가 열이 없다**
HEADER_0822 = ["상품명", "옵션명", "채널명", "카페24 품목코드1", "채널명", "스스 품목코드1"]


def test_08_22판은_거부된다_원가_열이_없다():
    """★열 위치로 읽었으면 옵션명을 원가로 적재했을 자리다(실사고 재현 방지)."""

    rows = [HEADER_0822, ["일미리 케이스", "아이폰16", "자사몰", "P001", "스스", "111"]]
    with pytest.raises(PriceSheetError) as e:
        parse_price_sheet(rows)
    assert "원가" in str(e.value)


def test_상품명_열이_없으면_거부된다():
    with pytest.raises(PriceSheetError):
        parse_price_sheet([["옵션명", "원가"], ["x", 100]])


def test_빈_시트는_거부된다():
    with pytest.raises(PriceSheetError):
        parse_price_sheet([])


def test_열은_이름으로_찾는다_위치가_바뀌어도():
    """헤더 순서를 뒤집어도 라벨로 찾는다 — 위치 상수를 안 두는 것이 이 모듈의 요지다."""

    header = ["채널명", "카페24 품목코드1", "원가", "상품명"]
    name_col, price_col, codes = detect_columns(header)
    assert (name_col, price_col) == (3, 2)
    assert 1 in codes and 0 not in codes  # 「채널명」은 코드가 아니다


def test_단가와_자리표시자를_가른다():
    rows = [
        HEADER_0807,
        ["일미리 케이스, 아이폰16", 922, "자사몰", "P001", "스스", "111"],
        ["시스루 케이스 블랙, 아이폰14", 1, "자사몰", "P002", "스스", "222"],
        ["하이톡, 화이트", 0, "자사몰", "P003", "스스", "333"],
    ]
    r = parse_price_sheet(rows)
    assert len(r.rows) == 3
    assert [x.price for x in r.rows] == [Decimal("922"), None, None]
    assert [x.is_placeholder for x in r.rows] == [False, True, True]
    # 원문은 남는다 — 화면이 「왜 공백인가」를 말할 수 있어야 한다
    assert r.rows[1].raw_price == Decimal("1")
    assert len(r.priced) == 1 and len(r.placeholders) == 2


def test_자리표시자_경계는_1원_이하다():
    rows = [HEADER_0807, ["a", PLACEHOLDER_MAX, "c", "P", "d", "S"],
            ["b", PLACEHOLDER_MAX + 1, "c", "P", "d", "S"]]
    r = parse_price_sheet(rows)
    assert r.rows[0].is_placeholder is True
    assert r.rows[1].is_placeholder is False


def test_숫자가_아닌_원가는_공백이고_이상으로_기록된다():
    """조용히 0으로 만들지 않는다 — 「없음」과 「0」은 다른 사실이다."""

    rows = [HEADER_0807, ["a", "미정", "c", "P001", "d", "S1"]]
    r = parse_price_sheet(rows)
    assert r.rows[0].price is None and r.rows[0].is_placeholder is True
    assert len(r.anomalies) == 1 and "a" in r.anomalies[0]


def test_쉼표와_원화기호가_붙은_값도_읽는다():
    rows = [HEADER_0807, ["a", "2,400", "c", "P001", "d", "S1"]]
    assert parse_price_sheet(rows).rows[0].price == Decimal("2400")


def test_채널코드를_모으고_상품명_원문을_그대로_싣는다():
    """상품명은 자르지 않는다 — 자르는 규칙은 판정이고 이 모듈은 판정하지 않는다."""

    name = "오하이 빛반사, 지문방지 매트 필름 3매, 갤럭시노트10플러스"
    rows = [HEADER_0807, [name, 1956, "자사몰", "P001", "스스", "111"]]
    row = parse_price_sheet(rows).rows[0]
    assert row.product_name == name  # 쉼표가 이름 안에 있다 — 잘랐으면 깨진다
    assert row.channel_codes == ("P001", "111")


def test_상품명이_빈_행은_건너뛴다():
    rows = [HEADER_0807, ["", 100, "c", "P", "d", "S"], ["a", 100, "c", "P", "d", "S"]]
    assert len(parse_price_sheet(rows).rows) == 1


def test_읽은_열을_결과가_말한다():
    """화면이 「어느 파일·어느 열을 읽었나」를 표시할 수 있어야 한다(계약 §3 금지선의 표면)."""

    r = parse_price_sheet([HEADER_0807, ["a", 100, "c", "P", "d", "S"]])
    assert (r.name_label, r.price_label) == ("상품명", "원가")
    assert (r.name_col, r.price_col) == (0, 1)
