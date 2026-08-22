# test_import_cost_allocator.py — 통관비 배부·검산 순수 SA (D-CPP-48)
#
# 이 파일은 **실건 하나(SETR2608170216, 2026-08-18)를 정본으로** 쓴다. 합성 데이터로만 짜면
# 「테스트는 통과하는데 아무것도 안 지키는」 모양이 되기 쉽고(교훈 #181), 이 도메인은 실제
# 서류의 라인 분해가 어긋나는 것(CI 350 = PL 300+50)이 설계의 핵심이라 실물이 있어야 한다.
from __future__ import annotations

from decimal import Decimal as D

import pytest

from app.services.import_cost.allocator import (
    ALLOCATION_BASES,
    VAT_MULTIPLIER,
    AllocationError,
    CostLine,
    InvoiceLine,
    actual_vat_pool,
    allocate,
    costing_pool,
)
from app.services.import_cost.reconciler import reconcile

# ──────────────────────────────────────────────
# 실건 픽스처 — 통관경비서 SETR2608170216 (2026-08-18)
# ──────────────────────────────────────────────
COST_LINES = [
    CostLine("OCEAN FREIGHT(해상운임)", D("156550"), D("0")),
    CostLine("OVER WEIGHT CHARGES", D("18000"), D("0")),
    CostLine("C/O(원산지증명서비용)", D("35000"), D("0")),
    CostLine("신고비", D("44000"), D("0")),
    CostLine("PICKUP CHARGE IN CHINA", D("18400"), D("0")),
    CostLine("DOCUMENT FEE", D("25000"), D("0")),
    CostLine("국내운송료 ( 라보 * 1 )", D("90000"), D("9000")),
    CostLine("관세", D("249670"), D("0")),
    # ★부가세는 매입세액 공제 대상 → 배부하지 않는다. 값은 보존한다.
    CostLine("부가세", D("511230"), D("0"), is_costing=False),
    CostLine("통관수수료", D("25000"), D("2500")),
]

POOL = D("661620")  # 386,950(B/L 소계) + 249,670(관세) + 25,000(통관수수료)
FX = D("209.88")    # 경비서 신고환율 (계약 §2-6: 과세금액÷INV의 210.50이 아니라 이쪽)


def _line(seq, name, qty, price, w, cbm):
    return InvoiceLine(seq, name, D(qty), D(price), D(w), D(cbm))


# CI 15라인 + PL에서 온 중량·부피(박스 공유분은 수량비로 배분)
INVOICE_LINES = [
    _line(1, "Privacy Glass_iP16 Pro 2ea", "50", "19.2", "7.55", "0.026664"),
    _line(2, "Privacy Glass_iP15 Pro 2ea", "50", "19.2", "7.55", "0.026664"),
    _line(3, "Glass_Ip17Pro", "500", "12.2", "74.0", "0.26664"),
    _line(4, "Glass_Ip16 Pro", "350", "12.2", "52.75", "0.186312"),
    _line(5, "Glass_Ip16 Plus", "50", "12.2", "7.45", "0.026664"),
    _line(6, "Glass_iP15", "50", "12.2", "14.9", "0.053328"),
    _line(7, "Glass_iP15 pro", "200", "12.2", "30.0", "0.106656"),
    _line(8, "Glass_iP14promax", "50", "12.2", "7.6", "0.026664"),
    _line(9, "Glass_iP13/13pro", "50", "12.2", "7.6", "0.026664"),
    _line(10, "Glass_iP15 promax", "100", "12.2", "15.0", "0.053328"),
    _line(11, "Glass_iP15 plus", "50", "12.2", "7.55", "0.026664"),
    _line(12, "Privacy Glass_iP14pro 2ea", "50", "19.2", "7.55", "0.026664"),
    _line(13, "Glass_iP13promax", "50", "12.2", "7.65", "0.026664"),
    _line(14, "Glass_iP12promax", "50", "12.2", "7.65", "0.026664"),
    _line(15, "cleaning kits", "2400", "0.8", "33.0", "0.106656"),
]


def _by_name(result, name):
    return next(x for x in result.lines if x.item_name == name)


# ──────────────────────────────────────────────
# 배부 대상
# ──────────────────────────────────────────────
def test_costing_pool_excludes_vat_line():
    """부가세 라인이 배부 대상에서 빠진다 — 넣으면 원가가 통째로 부푼다."""
    assert costing_pool(COST_LINES) == POOL
    # 방어: 부가세를 원가성으로 뒤집으면 pool이 정확히 511,230 늘어난다.
    flipped = [
        CostLine(c.item_name, c.supply_amount, c.tax_amount, True) for c in COST_LINES
    ]
    assert costing_pool(flipped) - POOL == D("511230")


def test_costing_pool_ignores_tax_amount():
    """세액은 공급가액과 별도다 — pool에 더하지 않는다."""
    assert sum((c.tax_amount for c in COST_LINES), D("0")) == D("11500")
    assert costing_pool(COST_LINES) == POOL  # 11,500이 안 섞였다


def test_actual_vat_pool_is_reported_but_not_allocated():
    """실제 세액 합계는 «참고값»으로 따로 나온다(×1.1 규약과 대조하기 위해)."""
    assert actual_vat_pool(COST_LINES) == D("522730")  # 511,230 + 9,000 + 2,500


# ──────────────────────────────────────────────
# 합격기준 (계약 §4 · 앵커 ⓐⓑⓒ)
# ──────────────────────────────────────────────
def test_acceptance_ip17pro_unit_cost():
    """ⓑ Glass_Ip17Pro 500개 = 2,910원(VAT제외) / 3,201원(포함), ±1원."""
    r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    ip17 = _by_name(r, "Glass_Ip17Pro")
    assert abs(ip17.unit_cost_ex_vat - D("2910")) <= D("1"), ip17.unit_cost_ex_vat
    assert abs(ip17.unit_cost_inc_vat - D("3201")) <= D("1"), ip17.unit_cost_inc_vat


def test_acceptance_cleaning_kits_allocation():
    """ⓒ cleaning kits 2,400개 배부액 54,992원.

    ★계약 승인본엔 54,993으로 적혀 있었다 — 기획 단계 반올림 오기이고 구현 전에 정정했다
    (계약 §4-3 정정 주석). 정확한 값은 661,620 × 1920/23100 = 54,991.79 → 최대잔여법으로 54,992.
    """
    r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    kits = _by_name(r, "cleaning kits")
    assert abs(kits.allocated_cost_krw - D("54992")) <= D("1"), kits.allocated_cost_krw


@pytest.mark.parametrize("basis", ALLOCATION_BASES)
def test_acceptance_no_unallocated_remainder(basis):
    """ⓐ 미배분 잔액 0 — **네 기준 전부**에서. 최대잔여법이 산술로 보장한다."""
    r = allocate(INVOICE_LINES, COST_LINES, FX, basis)
    assert r.unallocated_krw == D("0")
    assert sum((ln.allocated_cost_krw for ln in r.lines), D("0")) == POOL


# ──────────────────────────────────────────────
# D-CPP-48 ①의 근거를 테스트가 지킨다
# ──────────────────────────────────────────────
def test_amount_weight_volume_converge_but_quantity_does_not():
    """금액·중량·부피는 수렴하고 수량만 이탈한다 — 배부기준 선택의 근거 그 자체.

    이 성질이 깨지면 D-CPP-48 ①의 논거가 사라진 것이므로 결정을 재심해야 한다.
    그래서 «구현이 맞나»가 아니라 «전제가 아직 참인가»를 재는 테스트다.
    """
    got = {}
    for basis in ALLOCATION_BASES:
        r = allocate(INVOICE_LINES, COST_LINES, FX, basis)
        got[basis] = _by_name(r, "Glass_Ip17Pro").unit_cost_ex_vat

    base = got["amount"]
    for basis in ("weight", "volume"):
        rel = abs(got[basis] - base) / base
        assert rel < D("0.005"), f"{basis}가 금액기준에서 {rel:.4%} 벗어났다 — 근거 재심 필요"
    # 수량기준은 확실히 다르다(부자재가 수량의 59%라서).
    assert abs(got["quantity"] - base) / base > D("0.05")


def test_quantity_basis_overloads_the_material_line():
    """수량 기준이면 개당 168원짜리 부자재의 «원가가 거의 2배»가 된다 — 기각의 실증.

    ★배부액(163원)이 물품대(167.9원)를 **넘지는 않는다** — 97% 수준이다. 초안 문구의
    「배보다 배꼽」은 원가가 배증한다는 뜻이지 배부가 물품대를 초과한다는 뜻이 아니었다.
    테스트는 실제 성질(배증)을 잰다.
    """
    amount_r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    qty_r = allocate(INVOICE_LINES, COST_LINES, FX, "quantity")
    per_unit_amount = _by_name(amount_r, "cleaning kits").allocated_cost_krw / D("2400")
    per_unit_qty = _by_name(qty_r, "cleaning kits").allocated_cost_krw / D("2400")
    assert per_unit_amount < D("25")
    assert per_unit_qty > D("160")

    goods_per_unit = D("0.8") * FX  # ≈ 167.9원
    # 금액기준: 물품대의 14% 미만이 얹힌다 / 수량기준: 90% 넘게 얹혀 원가가 거의 2배가 된다.
    assert per_unit_amount / goods_per_unit < D("0.15")
    assert per_unit_qty / goods_per_unit > D("0.9")
    unit_amount = _by_name(amount_r, "cleaning kits").unit_cost_ex_vat
    unit_qty = _by_name(qty_r, "cleaning kits").unit_cost_ex_vat
    assert unit_qty / unit_amount > D("1.7")


# ──────────────────────────────────────────────
# 부가세 두 값
# ──────────────────────────────────────────────
def test_inc_vat_is_exactly_ex_vat_times_multiplier():
    """포함값은 제외값 × 1.1 — 손익 엔진 규약(D-NAO-150)과 같은 모양이어야 한다."""
    r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    for ln in r.lines:
        expected = (ln.unit_cost_ex_vat * VAT_MULTIPLIER).quantize(D("0.01"))
        assert ln.unit_cost_inc_vat == expected


def test_inc_vat_multiplier_is_not_the_actual_tax():
    """×1.1이 «실제로 낸 부가세»가 아님을 테스트가 명시한다.

    실제 세액을 같은 기준으로 배부하면 ×1.1보다 작다(해상운임 등은 세액 0). 이 차이를
    모르고 ×1.1을 «회계적 정확성»으로 읽으면 안 되므로, 성질을 못 박아 둔다.
    """
    r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    ip17 = _by_name(r, "Glass_Ip17Pro")
    share = (D("500") * D("12.2")) / D("23100")           # 금액 기준 비중
    actual_vat_per_unit = actual_vat_pool(COST_LINES) * share / D("500")
    convention_vat_per_unit = ip17.unit_cost_inc_vat - ip17.unit_cost_ex_vat
    assert convention_vat_per_unit > actual_vat_per_unit
    assert convention_vat_per_unit - actual_vat_per_unit > D("10")


# ──────────────────────────────────────────────
# 실패 경로 — 조용히 0을 반환하지 않는다
# ──────────────────────────────────────────────
def test_missing_weight_raises_not_zero():
    lines = list(INVOICE_LINES)
    lines[2] = InvoiceLine(3, "Glass_Ip17Pro", D("500"), D("12.2"), None, D("0.26664"))
    with pytest.raises(AllocationError, match="weight"):
        allocate(lines, COST_LINES, FX, "weight")
    # 금액 기준으로는 여전히 돈다 — 결측이 다른 기준을 막지 않는다.
    assert allocate(lines, COST_LINES, FX, "amount").unallocated_krw == D("0")


def test_empty_invoice_raises():
    with pytest.raises(AllocationError):
        allocate([], COST_LINES, FX, "amount")


def test_zero_or_negative_fx_raises():
    for bad in (D("0"), D("-1")):
        with pytest.raises(AllocationError):
            allocate(INVOICE_LINES, COST_LINES, bad, "amount")


def test_zero_quantity_raises():
    lines = [InvoiceLine(1, "x", D("0"), D("10"))]
    with pytest.raises(AllocationError):
        allocate(lines, COST_LINES, FX, "amount")


def test_all_weights_zero_raises():
    lines = [InvoiceLine(1, "x", D("5"), D("0")), InvoiceLine(2, "y", D("5"), D("0"))]
    with pytest.raises(AllocationError, match="합계가 0"):
        allocate(lines, COST_LINES, FX, "amount")


def test_remainder_distribution_is_deterministic():
    """같은 입력이면 같은 배부 — 최대잔여법의 순서가 흔들리면 값이 매번 달라진다."""
    a = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    b = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    assert [x.allocated_cost_krw for x in a.lines] == [x.allocated_cost_krw for x in b.lines]


def test_pool_of_one_won_lands_on_a_single_line():
    """1원짜리 pool도 잔액 0으로 나뉜다 — 반올림이 돈을 만들거나 없애지 않는다."""
    cost = [CostLine("잡비", D("1"), D("0"))]
    r = allocate(INVOICE_LINES, cost, FX, "amount")
    assert r.unallocated_krw == D("0")
    assert sum((ln.allocated_cost_krw for ln in r.lines), D("0")) == D("1")


# ──────────────────────────────────────────────
# 검산 — CI와 PL의 라인 분해가 다르다는 실물 성질
# ──────────────────────────────────────────────
CI_ROWS = [(ln.item_name, ln.quantity) for ln in INVOICE_LINES]
# PL은 같은 품목을 박스별로 쪼갠다: Ip16 Pro 350 = 300(7-9번) + 50(10번 동승)
PL_ROWS = [
    ("Privacy Glass_iP16 Pro 2ea", D("50")),
    ("Privacy Glass_iP15 Pro 2ea", D("50")),
    ("Glass_Ip17Pro", D("500")),
    ("Glass_Ip16 Pro", D("300")),
    ("Glass_Ip16 Plus", D("50")),
    ("Glass_Ip16 Pro", D("50")),      # ← 10번 박스에 동승한 나머지
    ("Glass_iP15", D("50")),
    ("Glass_iP15 pro", D("200")),
    ("Glass_iP14promax", D("50")),
    ("Glass_iP13/13pro", D("50")),
    ("Glass_iP15 promax", D("100")),
    ("Glass_iP15 plus", D("50")),
    ("Privacy Glass_iP14pro 2ea", D("50")),
    ("Glass_iP13promax", D("50")),
    ("Glass_iP12promax", D("50")),
    ("cleaning kits", D("2400")),
]


def _reconcile(ci=None, pl=None, inv_total=D("23100"), declared=D("23100")):
    r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    return reconcile(
        invoice_rows=ci if ci is not None else CI_ROWS,
        packing_rows=pl if pl is not None else PL_ROWS,
        invoice_total_foreign=inv_total,
        declared_inv_value=declared,
        pool_krw=r.pool_krw,
        allocated_total_krw=r.allocated_total_krw,
    )


def test_reconcile_passes_on_real_shipment():
    """라인 수가 15 vs 16으로 달라도 **품목 합계**로는 맞는다 — 이게 검산의 설계다."""
    rep = _reconcile()
    assert rep.passed, [(c.key, c.status, c.detail, c.rows) for c in rep.failures]
    assert len(CI_ROWS) == 15 and len(PL_ROWS) == 16


def test_reconcile_catches_quantity_mismatch():
    bad = [(n, q - D("1") if n == "Glass_Ip17Pro" else q) for n, q in PL_ROWS]
    rep = _reconcile(pl=bad)
    assert not rep.passed
    qty = next(c for c in rep.checks if c.key == "quantity")
    assert qty.status == "mismatch"
    assert any(r["item"].startswith("glass_ip17pro") for r in qty.rows)


def test_reconcile_catches_invoice_total_mismatch():
    rep = _reconcile(declared=D("23000"))
    assert not rep.passed
    tot = next(c for c in rep.checks if c.key == "invoice_total")
    assert tot.status == "mismatch"


# ──────────────────────────────────────────────
# 적대 리뷰 P1-1 회귀 — 배부 검산이 `0 == 0`을 통과로 접던 자리
# (변이 #16「check_allocation을 항상 ok로」가 40/40 초록으로 살아남았던 구멍)
# ──────────────────────────────────────────────
def test_allocation_check_missing_when_no_costing_lines():
    """통관경비서가 없으면 «미배분 0원»이 아니라 `missing`이다.

    초판은 pool=0·allocated=0을 `ok`로 접었고, 그래서 **통관비 661,620원이 통째로 빠진
    단가가 「확정」으로 저장**됐다(적대 리뷰 P1-1 재현).
    """
    from app.services.import_cost.reconciler import check_allocation

    c = check_allocation(D("0"), D("0"), allocation_ran=True, has_costing_lines=False)
    assert c.status == "missing"
    assert not c.passed
    assert "통관경비서" in c.detail or "원가성" in c.detail


def test_allocation_check_missing_when_allocation_did_not_run():
    """배부가 예외로 못 돌았으면 `missing`이다 — 「잰 적 없음」을 「값 불일치」로 쓰지 않는다."""
    from app.services.import_cost.reconciler import check_allocation

    c = check_allocation(D("661620"), D("0"), allocation_ran=False)
    assert c.status == "missing"
    assert not c.passed
    assert c.actual is None  # 안 잰 값을 0으로 지어내지 않는다


def test_allocation_check_still_catches_real_mismatch():
    """원료가 있는데 합이 안 맞으면 종전대로 `mismatch`다 — 수정이 검산을 무디게 하지 않았다."""
    from app.services.import_cost.reconciler import check_allocation

    ok = check_allocation(D("661620"), D("661620"))
    assert ok.status == "ok"
    bad = check_allocation(D("661620"), D("661619"))
    assert bad.status == "mismatch"
    assert "1" in bad.detail


def test_reconcile_fails_when_cost_document_absent():
    """실건에서 비용 라인만 비우면 **전항 통과가 되지 않는다**(P1-1의 HTTP 아래 층)."""
    r = allocate(INVOICE_LINES, [], FX, "amount")
    rep = reconcile(
        invoice_rows=CI_ROWS,
        packing_rows=PL_ROWS,
        invoice_total_foreign=D("23100"),
        declared_inv_value=D("23100"),
        pool_krw=r.pool_krw,
        allocated_total_krw=r.allocated_total_krw,
        allocation_ran=True,
        has_costing_lines=False,
    )
    assert not rep.passed
    alloc = next(c for c in rep.checks if c.key == "allocation")
    assert alloc.status == "missing"


# ──────────────────────────────────────────────
# 살아남은 변이 #5 — `allocate`의 기본 basis
# ──────────────────────────────────────────────
def test_allocate_default_basis_is_amount():
    """기본 배부기준은 «금액»이다 — D-CPP-48 ①이 이 자리에서도 지켜져야 한다.

    변이 #5(기본값을 quantity로)가 살아남았던 이유는 호출부가 전부 basis를 명시해서다.
    「죽은 기본값」이라도 계약이 정한 값이어야 한다 — 나중에 명시를 빠뜨린 호출부가 생기면
    그때 조용히 다른 기준으로 배부된다.
    """
    default_r = allocate(INVOICE_LINES, COST_LINES, FX)
    amount_r = allocate(INVOICE_LINES, COST_LINES, FX, "amount")
    assert default_r.basis == "amount"
    assert [x.allocated_cost_krw for x in default_r.lines] == [
        x.allocated_cost_krw for x in amount_r.lines
    ]


def test_missing_is_not_ok():
    """원료가 없으면 `missing`이지 `ok`가 아니다 — 발견 0건과 실행 안 됨은 다르다(교훈 #123)."""
    rep = _reconcile(pl=[])
    qty = next(c for c in rep.checks if c.key == "quantity")
    assert qty.status == "missing"
    assert not qty.passed
    assert not rep.passed

    rep2 = _reconcile(declared=None)
    tot = next(c for c in rep2.checks if c.key == "invoice_total")
    assert tot.status == "missing"
    assert not rep2.passed
