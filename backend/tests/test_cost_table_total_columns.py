# test_cost_table_total_columns.py — 원가표의 «어느 열이 원가인가» (2026-09-01)
#
# ## 왜 이 파일이 생겼나
#
# 파서가 언제나 D열을 총액으로 읽었다. 필름 섹션은 D가 「제품원가(+VAT)」라 맞았는데,
# 수입·케이스·악세서리 섹션은 D가 **외화 단가**였다. 그래서 prod에:
#
#   오타오_투명 강화유리    12.2  (실제 3,102.7)      ← CNY를 원가로 저장
#   맥세이프 일미리 케이스   0.99  (실제 2,676.4)      ← USD를 원가로 저장
#   오픽스 거치재            11   (실제 19,582.6)
#   맥세이프 셀카봉         4.5   (실제 8,011.1)
#
# 가 들어가 있었고, **D가 빈 국내 케이스 6행 + 그립톡 + 맥탭 카드케이스 8행은 «데이터 행이
# 아니다»로 판정돼 통째로 사라졌다**(엑셀 68품목 → DB 60품목). 그 8행이 케이스 165 SKU의
# 원가였고, 그동안 「원가표에 케이스가 없다」고 잘못 알고 있었다.
#
# ★이 파일은 «값이 맞나»가 아니라 **«어느 열에서 왔나»**를 지킨다 — 그게 틀렸던 것이다.
from __future__ import annotations

from decimal import Decimal as D

from app.services.cost_menu.recipe_parser import parse_cost_table

VAT = D("1.1")


# 실제 『2026년 원가표_v1.xlsx』 「제품 원가표」의 열 배치를 그대로 옮긴 것이다.
# (A는 비어 있고 B=품목. 그래서 인덱스 1이 품목이다.)
def _row(*cells):
    return [None, *cells]


# ★「품목」이 col1인 헤더는 섹션 이름을 **바로 앞 제목 행**에서 가져온다(실제 시트가 그렇다).
#   그 제목 행을 빼면 section이 None이라 데이터 행이 통째로 버려진다 — 초판 픽스처가 그랬다.
FILM_TITLE = _row("모바일 필름-아이폰,갤럭시")
FILM_HEADER = _row("품목", None, "제품원가\n(+VAT)", "매입", "필름", "필름*매입", "부착\n안내문")
FILM_ROW = _row("자가복원 고투명 EPU 3매", None, 3275.8, 3, 800, 2400, 30)

IMPORTED_TITLE = _row("오타오_강화유리필름", None, "환율", 200)
IMPORTED_HEADER = _row(
    "품목", None, "제품원가\n(CNY)", "제품원가\n(KRW)", "뮬류비", "관세", "부가세", "상품원가"
)
IMPORTED_ROW = _row("오타오_투명 강화유리 (New Ver.)", None, 12.2, 2440, 0.1, 0.056, 0.1, 3102.704)
IMPORTED_NO_LANDED = _row("오타오_갤럭시 투명 강화유리 (New Ver.)", None, 19.3)

CASE_HEADER = _row(
    "케이스", None, "제품원가\n(US$)", "제품원가\n(KRW)", "뮬류비(ea)", "관세", "부가세",
    "상품원가", "부자재",
)
CASE_IMPORTED = _row("맥세이프 일미리 케이스", None, 0.99, 1579.05, 725, 0.08, 0.1, 2676.38)
CASE_DOMESTIC = _row("맥세이프 소다 케이스 포유", None, None, 4202, None, None, None, 3700, 120)
CASE_MISMATCH = _row("일미리 케이스", None, None, 922, None, None, None, 790, 132)
CASE_LANDED_ONLY = _row("맥세이프 케이스(폴드)", None, None, None, None, None, None, 2300)

ACC_HEADER = _row("오타오_기타 액세서리", "제품원가 (KRW)")
ACC_ROW = _row("맥세이프 그립톡", 5240)


def _one(rows):
    r = parse_cost_table(rows)
    assert r.recipe_count == 1, f"품목 1건이어야 하는데 {r.recipe_count}건: {[d.item_name for d in r.recipes]}"
    return r.recipes[0]


# ═══════════════════════════════════════════════════════════════════
# 필름 섹션은 그대로여야 한다 (이 변경의 «안 건드린다» 쪽)
# ═══════════════════════════════════════════════════════════════════


def test_film_section_still_reads_the_plus_vat_column():
    d = _one([FILM_TITLE, FILM_HEADER, FILM_ROW])
    assert d.excel_total_inc_vat == D("3275.8")
    assert d.recipe_kind == "assembly"


# ═══════════════════════════════════════════════════════════════════
# 수입 섹션 — 외화가 아니라 「상품원가」다
# ═══════════════════════════════════════════════════════════════════


def test_imported_reads_landed_cost_not_the_foreign_price():
    """★12.2(CNY)가 아니라 3,102.704(상품원가)여야 한다 — prod에 12.2가 들어가 있었다."""
    d = _one([IMPORTED_TITLE, IMPORTED_HEADER, IMPORTED_ROW])
    assert d.excel_total_inc_vat == D("3102.704")
    assert d.excel_total_inc_vat != D("12.2")
    assert d.recipe_kind == "imported_goods"


def test_imported_without_landed_refuses_to_invent_a_cost():
    """★외화만 있는 행은 «원가 없음»이다 — 19.3원짜리 강화유리를 만들지 않는다."""
    d = _one([IMPORTED_TITLE, IMPORTED_HEADER, IMPORTED_NO_LANDED])
    assert d.excel_total_inc_vat is None
    assert any(a.startswith("foreign_only_no_landed:") for a in d.anomalies)


# ═══════════════════════════════════════════════════════════════════
# 케이스 — 네 가지 모양
# ═══════════════════════════════════════════════════════════════════


def test_imported_case_reads_landed_not_usd():
    d = _one([CASE_HEADER, CASE_IMPORTED])
    assert d.excel_total_inc_vat == D("2676.38")
    assert d.excel_total_inc_vat != D("0.99")


def test_domestic_case_total_is_body_plus_parts_times_vat():
    """국내 매입 케이스의 총액 = (상품원가 + 부자재) × 1.1 (Jino 확정 2026-09-01)."""
    d = _one([CASE_HEADER, CASE_DOMESTIC])
    assert d.excel_total_inc_vat == (D("3700") + D("120")) * VAT == D("4202.0")
    assert not [a for a in d.anomalies if a != "needs_manual_lines"]


def test_domestic_case_confesses_when_excel_total_disagrees():
    """★「일미리 케이스」는 엑셀 E가 922인데 (790+132)×1.1 = 1,014.2다.

    Jino 확정: **계산값이 맞다**(엑셀이 ×1.1을 빠뜨렸다). 값은 계산값을 쓰되
    어긋났다는 사실을 자백한다 — 조용히 고르면 다음 사람이 못 본다.
    """
    d = _one([CASE_HEADER, CASE_MISMATCH])
    assert d.excel_total_inc_vat == D("1014.20")
    assert any(a.startswith("excel_total_mismatch:") for a in d.anomalies)


def test_case_with_only_landed_says_vat_is_unknown():
    d = _one([CASE_HEADER, CASE_LANDED_ONLY])
    assert d.excel_total_inc_vat == D("2300")
    assert "landed_only_vat_unknown" in d.anomalies


# ═══════════════════════════════════════════════════════════════════
# ★헤더가 C열에 있는 섹션 — 통째로 사라지던 자리
# ═══════════════════════════════════════════════════════════════════


def test_section_whose_header_sits_in_column_c_is_not_lost():
    """★「오타오_기타 액세서리」는 제품원가가 C열이라 헤더 자체가 안 잡혔고, 그 아래
    「맥세이프 그립톡」 5,240원이 통째로 사라졌다."""
    d = _one([ACC_HEADER, ACC_ROW])
    assert d.item_name == "맥세이프 그립톡"
    assert d.excel_total_inc_vat == D("5240")


# ═══════════════════════════════════════════════════════════════════
# ★«데이터 행인가»와 «총액이 얼마인가»는 다른 질문이다
# ═══════════════════════════════════════════════════════════════════


def test_rows_without_the_foreign_column_are_still_data_rows():
    """★이게 8행 누락의 뿌리다 — D가 비었다고 «행이 아니다»로 버리면 안 된다."""
    r = parse_cost_table([CASE_HEADER, CASE_IMPORTED, CASE_DOMESTIC, CASE_MISMATCH, CASE_LANDED_ONLY])
    assert r.recipe_count == 4, [d.item_name for d in r.recipes]
    assert [d.item_name for d in r.recipes] == [
        "맥세이프 일미리 케이스",
        "맥세이프 소다 케이스 포유",
        "일미리 케이스",
        "맥세이프 케이스(폴드)",
    ]


def test_section_title_rows_are_still_treated_as_labels():
    """값이 하나도 없는 이름 행은 여전히 «섹션 제목»이지 데이터가 아니다."""
    r = parse_cost_table([FILM_TITLE, FILM_HEADER, FILM_ROW])
    assert r.recipe_count == 1
    assert r.recipes[0].section == "모바일 필름-아이폰,갤럭시"


# ═══════════════════════════════════════════════════════════════════
# ★recipe_kind 세 번째 값 — purchased
# ═══════════════════════════════════════════════════════════════════


def test_purchased_kind_is_distinct_from_imported_and_assembly():
    """★국내 매입품은 `assembly`도 `imported_goods`도 아니다 (Jino 확정 2026-09-01).

    `assembly`면 「구성 0줄인 조립품」으로 영영 「정본 없음」에 갇히고,
    `imported_goods`면 있지도 않은 통관 원장을 기다린다.
    """
    r = parse_cost_table(
        [CASE_HEADER, CASE_DOMESTIC,
         IMPORTED_TITLE, IMPORTED_HEADER, IMPORTED_ROW,
         FILM_TITLE, FILM_HEADER, FILM_ROW]
    )
    kinds = {d.item_name: d.recipe_kind for d in r.recipes}
    assert kinds["맥세이프 소다 케이스 포유"] == "purchased"
    assert kinds["오타오_투명 강화유리 (New Ver.)"] == "imported_goods"
    assert kinds["자가복원 고투명 EPU 3매"] == "assembly"


def test_accessory_and_etc_sections_are_purchased_too():
    r = parse_cost_table([ACC_HEADER, ACC_ROW])
    assert r.recipes[0].recipe_kind == "purchased"
    # 「기타」 섹션이 폼팩터 없음으로 인정돼 unknown_section 이 안 붙는다.
    assert not [a for a in r.anomalies if "unknown_section" in a]
