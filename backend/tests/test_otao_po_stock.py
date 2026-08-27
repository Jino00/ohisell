"""S4 — 파생 현재고 · 실사 대조 회귀 (계약 §4 **S4** · 체인 `발주예측` n=8).

## 무엇을 재는가

계약 원문의 요구는 한 문장 안에 둘인데 **둘의 성격이 다르다**:

1. **파생 현재고 = 초기 실사 + 픽업 입고 − 판매** — 「판매」 항이 **원리적으로 막혀 있다**
   (발주·픽업은 OTAO 품목코드 축, 판매는 우리 SKU 축, 교집합 0, 잇는 표 없음).
   ⇒ 이 파일이 잠그는 것은 「파생값이 맞는가」가 아니라 **「막힌 것이 «0»이 아니라 «막혔다»로
   남는가」**다. 0으로 접히는 순간 재고가 부풀고, 부푼 재고는 「발주하지 마라」로 읽힌다.
2. **실사 표본 대조 오차** — 이쪽은 **안 막힌다.** 판매 축을 안 타기 때문이다. 그래서 이 파일은
   대조 산술을 실제 숫자로 잠근다.

## 이 파일이 특별히 지키는 것 — 전부 앞 슬라이스가 실제로 밟은 지뢰다

- **`manual`(사람이 센 값)이 스냅샷 축에 섞이지 않는 것.** 섞이면 ECOUNT 값을 **자기 자신과**
  대조하게 되어 오차가 **항상 0**으로 나온다. 「전건 초록인데 아무것도 안 재는 테스트」의
  교과서적 모양이라 전용 회귀를 둔다(`test_manual_rows_never_enter_the_snapshot_axis`).
- **창고를 합치지 않는 것.** 초판 실측이 전 창고 합계를 내서 틀렸다(계약 §1). 「본사에 있는 것」과
  「이미 쿠팡 제트배송에 나가 있는 것」은 발주 판단에서 정반대다.
- **prod 모양 픽스처.** n=6 P1-1은 「픽스처가 키당 1행뿐이라 0건이 잡았다」, n=7 P1-2는
  「기본 목이 빈 원장이라 Jino가 보는 분기를 한 번도 안 렌더했다」였다. 그래서 여기 기본
  픽스처는 **2026-08-25 16:42:46 라이브 관측의 창고 구성과 행수 비율**을 따른다
  (본사 836 · 본사-포장 422 · 반품창고 57 · 쿠팡 제트배송 53 · 아마존 23 = 1,391행, ref 98 §8).
- **HTTP body 단언**(교훈 #321). 서비스층 dict만 보면 `response_model`이 자백 필드를 지운 것을
  못 잡는다.
"""

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
    ImportInvoiceLine,
    ImportShipment,
    OtaoItemNameMap,
    OtaoStockSnapshot,
)
from app.services.otao_po.stock import build_stock, warehouse_role
from app.services.otao_po.stock_ingest import (
    build_manual_count_payload,
    build_stock_payload,
    ingest_stock_payload,
)

T0 = datetime(2026, 8, 27, 10, 0, 0)
T1 = datetime(2026, 9, 3, 10, 0, 0)

# ── prod 실측 창고 구성 (2026-08-25 16:42:46 · ref 98 §8) ─────────────────
# (창고명, 그 창고의 행수) — 합 1,391. 행수를 그대로 재현하진 않지만 **구성과 역할**은 같게 둔다.
_PROD_WAREHOUSES = [
    ("본사", "00010"),
    ("본사-포장", "00011"),
    ("반품창고", "00012"),
    ("쿠팡 제트배송", "00020"),  # ★코드가 문서에 유일하게 남아 있는 창고
    ("아마존", "00030"),
]


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


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Testing = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = Testing()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Testing() as s:
        yield TestClient(app), s
    app.dependency_overrides.pop(get_db, None)


def _snap(session, at, wh_name, wh_code, code, qty, *, source="ecount_api"):
    session.add(
        OtaoStockSnapshot(
            snapshot_at=at,
            base_date=at.date(),
            warehouse_code=wh_code,
            warehouse_name=wh_name,
            product_code=code,
            quantity=Decimal(str(qty)),
            source=source,
        )
    )


def _seed_prod_shaped(session) -> None:
    """★기본 픽스처는 «prod가 지금 있을 모양»이다 — 빈 원장이 아니다(n=7 P1-2)."""
    # 본사: 차감항의 본체
    _snap(session, T0, "본사", "00010", "GAPIP15", 11)
    _snap(session, T0, "본사", "00010", "GAPIP16PR", 340)
    _snap(session, T0, "본사", "00010", "GAPIP12MI", 0)  # ★0개도 사실이다 — 없는 것과 다르다
    # 본사-포장: 부자재 — 차감항이 아니다
    _snap(session, T0, "본사-포장", "00011", "GAPIP16PR", 900)
    # 쿠팡 제트배송: 이미 채널에 나가 있다 — 우리 창고가 아니다
    _snap(session, T0, "쿠팡 제트배송", "00020", "GAPIP16PR", 120)
    # 반품창고·아마존: 미사용
    _snap(session, T0, "반품창고", "00012", "GAPIP15", 5)
    _snap(session, T0, "아마존", "00030", "GAPIP15", 3)
    session.commit()


def _seed_pickup(session, *, decl: date, item: str, qty: int) -> None:
    # `hbl_no`는 NOT NULL이다 — prod 스키마 그대로 쓴다(픽스처가 prod와 다르면 결함을 못 잡는다).
    sh = ImportShipment(
        hbl_no=f"SETR{decl:%y%m%d}{qty:04d}",
        declaration_date=decl,
        fx_rate=Decimal("190.0"),  # NOT NULL
        status="confirmed",
    )
    session.add(sh)
    session.flush()
    session.add(
        ImportInvoiceLine(
            shipment_id=sh.id,
            seq=1,  # NOT NULL
            item_name=item,
            quantity=qty,
            unit_price_foreign=Decimal("13.0"),  # NOT NULL
            line_type="product",
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 창고 역할 — 합치지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_warehouse_roles_follow_the_contract_table():
    """계약 §1 창고 5개 표가 정본이다. 데이터엔 안 적혀 있고 Jino만 안다."""
    assert warehouse_role("본사") == "own"
    assert warehouse_role("본사-포장") == "material"
    assert warehouse_role("쿠팡 제트배송") == "channel"
    assert warehouse_role("반품창고") == "excluded"
    assert warehouse_role("아마존") == "excluded"


def test_unknown_warehouse_is_unknown_not_own_and_not_excluded():
    """★모르는 창고를 `own`으로 접으면 없던 재고가 생기고, `excluded`로 접으면 재고가 사라진다.

    둘 다 계약 §2-8이 금지하는 「모름을 아는 값으로 바꾸기」다.
    """
    assert warehouse_role("제3창고") == "unknown"
    assert warehouse_role(None, None) == "unknown"


def test_baseline_counts_only_the_own_warehouse(session):
    _seed_prod_shaped(session)
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}

    # 본사 340만 기준이다. 포장 900·제트 120은 갈라져 실리되 기준에 안 들어간다.
    assert row["GAPIP16PR"].baseline_quantity == Decimal("340")
    assert row["GAPIP16PR"].baseline_by_role["material"] == Decimal("900")
    assert row["GAPIP16PR"].baseline_by_role["channel"] == Decimal("120")
    # 반품·아마존은 excluded로 갈려 실린다 — 버리지 않는다(있다는 사실은 남는다).
    assert row["GAPIP15"].baseline_quantity == Decimal("11")
    assert row["GAPIP15"].baseline_by_role["excluded"] == Decimal("8")  # 5 + 3


def test_zero_in_warehouse_is_not_absent(session):
    """★창고에 0개인 것과 스냅샷에 아예 없는 것은 다른 상태다."""
    _seed_prod_shaped(session)
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP12MI"].baseline_quantity == Decimal("0")  # 0이라고 «말한» 것
    assert "GAPIP99XX" not in row  # 아예 없는 것


def test_unknown_warehouse_is_surfaced_and_excluded_from_baseline(session):
    _seed_prod_shaped(session)
    _snap(session, T0, "제3창고", "00099", "GAPIP15", 77)
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP15"].baseline_quantity == Decimal("11")  # 77이 안 섞였다
    assert s.unknown_warehouses["제3창고"] == Decimal("77")
    assert any("역할을 모르는 창고" in n for n in s.notes)


# ══════════════════════════════════════════════════════════════════════════
# 판매 항 — 막힌 것을 0으로 접지 않는다
# ══════════════════════════════════════════════════════════════════════════


def test_sold_is_none_not_zero_and_derived_says_why(session):
    """★이 파일의 핵심. 판매를 0으로 두면 파생 현재고가 «재고+입고»로 부푼다."""
    _seed_prod_shaped(session)
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}

    assert row["GAPIP16PR"].sold_quantity is None
    assert row["GAPIP16PR"].derived_quantity is None
    assert row["GAPIP16PR"].derived_blocked_by == "sold"
    # 합계도 None이다 — 0으로 합치면 「합계는 0」으로 읽힌다.
    assert s.totals["sold"] is None
    assert s.totals["derived"] is None
    assert "다리" in (s.sold_unavailable_reason or "")


def test_missing_baseline_blocks_derivation_by_baseline_not_by_sold(session):
    """스냅샷에 없는 코드는 「재고 0」이 아니라 기준 자체가 없는 것이다."""
    _seed_prod_shaped(session)
    session.add(OtaoItemNameMap(raw_name="Glass_New", product_code="GAPIP17PR"))
    _seed_pickup(session, decl=date(2026, 8, 30), item="Glass_New", qty=500)
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP17PR"].baseline_quantity is None
    assert row["GAPIP17PR"].derived_blocked_by == "baseline"
    assert row["GAPIP17PR"].upper_bound_if_no_sales is None


def test_upper_bound_is_baseline_plus_inbound_and_is_not_called_stock(session):
    """「판매 미차감 상한」은 현재고가 아니다 — 판매가 ≥0이라 실제는 이보다 클 수 없다는 뜻뿐."""
    _seed_prod_shaped(session)
    session.add(OtaoItemNameMap(raw_name="Glass_iP16 Pro", product_code="GAPIP16PR"))
    _seed_pickup(session, decl=date(2026, 8, 30), item="Glass_iP16 Pro", qty=1000)
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP16PR"].inbound_quantity == Decimal("1000")
    assert row["GAPIP16PR"].upper_bound_if_no_sales == Decimal("1340")  # 340 + 1000
    assert row["GAPIP16PR"].derived_quantity is None  # ★상한은 파생값이 «아니다»


def test_inbound_counts_only_after_the_baseline_moment(session):
    """t0 «이전» 입고는 이미 기준 재고에 반영돼 있다 — 또 더하면 이중 계상이다."""
    _seed_prod_shaped(session)
    session.add(OtaoItemNameMap(raw_name="Glass_iP16 Pro", product_code="GAPIP16PR"))
    _seed_pickup(session, decl=date(2026, 8, 1), item="Glass_iP16 Pro", qty=700)  # t0 전
    _seed_pickup(session, decl=date(2026, 8, 27), item="Glass_iP16 Pro", qty=300)  # t0 당일
    _seed_pickup(session, decl=date(2026, 8, 28), item="Glass_iP16 Pro", qty=250)  # t0 후
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    # `> t0.date()`이므로 당일(8/27)도 제외된다 — 기준 재고가 그날 값이기 때문이다.
    assert row["GAPIP16PR"].inbound_quantity == Decimal("250")


# ══════════════════════════════════════════════════════════════════════════
# 실사 대조 — 여기는 «막히지 않는다»
# ══════════════════════════════════════════════════════════════════════════


def test_no_snapshot_says_never_taken_not_zero(session):
    s = build_stock(session)
    assert s.snapshot_count == 0
    assert s.rows == []
    assert any("찍은 적 없음" in n for n in s.notes)


def test_single_snapshot_says_measurement_starts_at_the_second(session):
    _seed_prod_shaped(session)
    s = build_stock(session)
    assert s.snapshot_count == 1
    assert s.baseline_at == s.latest_at == T0
    assert any("두 번째 스냅샷부터" in n for n in s.notes)


def test_latest_snapshot_is_the_comparison_target(session):
    _seed_prod_shaped(session)
    _snap(session, T1, "본사", "00010", "GAPIP16PR", 300)
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert s.snapshot_count == 2
    assert s.baseline_at == T0 and s.latest_at == T1
    assert row["GAPIP16PR"].baseline_quantity == Decimal("340")  # t0
    assert row["GAPIP16PR"].latest_snapshot_quantity == Decimal("300")  # 최신


def test_counted_argument_produces_the_variance_the_contract_asks_for(session):
    """계약 §2-7C ④가 요구하는 그 숫자 — ECOUNT가 말한 값 − 사람이 센 값."""
    _seed_prod_shaped(session)
    s = build_stock(session, counted={"GAPIP16PR": 320, "GAPIP15": 11})
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP16PR"].variance_vs_snapshot == Decimal("20")  # 340 − 320
    assert row["GAPIP16PR"].variance_pct == pytest.approx(6.25)
    assert row["GAPIP15"].variance_vs_snapshot == Decimal("0")  # 일치도 «측정된 결과»다
    assert s.totals["variance_sku_count"] == 2
    assert s.totals["variance_abs_sum"] == Decimal("20")


def test_variance_is_absent_when_no_count_and_that_is_not_zero(session):
    _seed_prod_shaped(session)
    s = build_stock(session)
    assert all(r.variance_vs_snapshot is None for r in s.rows)
    assert s.totals["variance_abs_sum"] is None  # ★0이 아니다
    assert any("미실시" in n for n in s.notes)


def test_counted_code_absent_from_snapshot_is_not_zero_variance(session):
    _seed_prod_shaped(session)
    s = build_stock(session, counted={"GAPIP99XX": 50})
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP99XX"].variance_vs_snapshot is None
    assert "GAPIP99XX" in s.totals["counted_without_snapshot"]
    assert any("대조가 성립하지 않은" in n for n in s.notes)


def test_manual_rows_never_enter_the_snapshot_axis(session):
    """★★섞이면 ECOUNT 값을 «자기 자신과» 대조하게 되어 오차가 항상 0으로 나온다.

    그 상태는 전건 초록이면서 아무것도 재지 않는다 — 이 트랙이 반복해 밟은 모양이라
    전용 회귀를 둔다.
    """
    _seed_prod_shaped(session)
    # 사람이 센 값이 «더 최근» 시각으로 들어온다. 순진하게 max(snapshot_at)를 쓰면 이게 최신
    # 스냅샷이 되어 대조 상대가 자기 자신이 된다.
    _snap(session, T1, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    session.commit()

    s = build_stock(session)
    # 스냅샷 축은 T0 하나뿐이어야 한다 — manual은 안 센다.
    assert s.snapshot_count == 1
    assert s.latest_at == T0
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP16PR"].latest_snapshot_quantity == Decimal("340")  # ECOUNT 값
    assert row["GAPIP16PR"].counted_quantity == Decimal("320")  # 사람 값
    # ★오차가 0이 아니라 20으로 나와야 한다. 0이면 자기 자신과 비교한 것이다.
    assert row["GAPIP16PR"].variance_vs_snapshot == Decimal("20")
    assert s.counted_at == T1


def test_explicit_counted_argument_wins_over_stored_manual(session):
    _seed_prod_shaped(session)
    _snap(session, T1, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    session.commit()
    s = build_stock(session, counted={"GAPIP16PR": 300})
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP16PR"].counted_quantity == Decimal("300")
    assert row["GAPIP16PR"].variance_vs_snapshot == Decimal("40")


# ══════════════════════════════════════════════════════════════════════════
# 적재 — 멱등 · 「못 읽음」을 0으로 만들지 않기 · 중복을 말하기
# ══════════════════════════════════════════════════════════════════════════


def _ecount_rows() -> list[dict]:
    return [
        {"WH_CD": c, "WH_DES": n, "PROD_CD": "GAPIP16PR", "PROD_DES": "강화유리", "BAL_QTY": "340.00"}
        for n, c in _PROD_WAREHOUSES
    ]


def test_ingest_is_idempotent(session):
    payload = build_stock_payload(_ecount_rows(), snapshot_at=T0, base_date_raw="20260827")
    r1 = ingest_stock_payload(session, payload)
    assert r1.inserted == 5 and r1.unchanged == 0
    r2 = ingest_stock_payload(session, payload)
    assert r2.inserted == 0 and r2.unchanged == 5


def test_unparseable_quantity_is_skipped_not_zeroed(session):
    """★0으로 대체하면 「그 창고에 0개 있다」가 되어 재고가 조용히 사라진다."""
    rows = _ecount_rows()
    rows[0]["BAL_QTY"] = "N/A"
    rep = ingest_stock_payload(
        session, build_stock_payload(rows, snapshot_at=T0)
    )
    assert rep.inserted == 4
    assert len(rep.skipped) == 1
    assert "수량 파싱 실패" in rep.skipped[0]["reason"]
    # 그 창고·품목 조합이 0으로 «들어가지» 않았다
    got = session.query(OtaoStockSnapshot).filter_by(warehouse_code="00010").all()
    assert got == []


def test_thousands_separator_parses(session):
    rows = [{"WH_CD": "00010", "WH_DES": "본사", "PROD_CD": "GAPIP15", "BAL_QTY": "1,391"}]
    ingest_stock_payload(session, build_stock_payload(rows, snapshot_at=T0))
    got = session.query(OtaoStockSnapshot).one()
    assert got.quantity == Decimal("1391")


def test_duplicate_key_is_summed_but_reported(session):
    """★조용히 더하면 안 된다 — n=6 P1-1이 정확히 그 모양이었다(1:N 펼침이 판매를 부풀렸다)."""
    rows = [
        {"WH_CD": "00010", "WH_DES": "본사", "PROD_CD": "GAPIP15", "BAL_QTY": "10"},
        {"WH_CD": "00010", "WH_DES": "본사", "PROD_CD": "GAPIP15", "BAL_QTY": "5"},
    ]
    rep = ingest_stock_payload(session, build_stock_payload(rows, snapshot_at=T0))
    assert rep.inserted == 1
    assert rep.duplicate_keys == ["00010/GAPIP15"]
    assert session.query(OtaoStockSnapshot).one().quantity == Decimal("15")


def test_row_without_product_code_is_skipped(session):
    rows = [{"WH_CD": "00010", "WH_DES": "본사", "PROD_CD": "", "BAL_QTY": "10"}]
    rep = ingest_stock_payload(session, build_stock_payload(rows, snapshot_at=T0))
    assert rep.inserted == 0 and len(rep.skipped) == 1


def test_payload_without_snapshot_at_is_refused(session):
    with pytest.raises(ValueError):
        ingest_stock_payload(session, {"rows": []})


def test_dry_run_does_not_commit(session):
    payload = build_stock_payload(_ecount_rows(), snapshot_at=T0)
    rep = ingest_stock_payload(session, payload, dry_run=True)
    assert rep.inserted == 5
    assert session.query(OtaoStockSnapshot).count() == 0


def test_manual_count_payload_lands_as_manual_source(session):
    ingest_stock_payload(
        session, build_manual_count_payload({"GAPIP16PR": 320}, snapshot_at=T1)
    )
    got = session.query(OtaoStockSnapshot).one()
    assert got.source == "manual"
    assert got.quantity == Decimal("320")


# ══════════════════════════════════════════════════════════════════════════
# HTTP — 자백 필드가 body에 «실제로» 실리는가 (교훈 #321)
# ══════════════════════════════════════════════════════════════════════════


def test_http_body_carries_every_confession_field(env):
    client, s = env
    _seed_prod_shaped(s)
    _snap(s, T1, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    s.commit()

    r = client.get("/api/otao-po/stock")
    assert r.status_code == 200
    body = r.json()

    for key in (
        "snapshot_empty",
        "snapshot_count",
        "baseline_at",
        "latest_at",
        "counted_at",
        "sold_unavailable_reason",
        "unknown_warehouses",
        "notes",
    ):
        assert key in body, f"자백 필드 {key}가 HTTP body에서 사라졌다"

    row = {r_["product_code"]: r_ for r_ in body["rows"]}["GAPIP16PR"]
    for key in (
        "baseline_by_role",
        "sold_quantity",
        "derived_quantity",
        "derived_blocked_by",
        "upper_bound_if_no_sales",
        "counted_quantity",
        "latest_snapshot_quantity",
        "variance_vs_snapshot",
    ):
        assert key in row, f"행 자백 필드 {key}가 HTTP body에서 사라졌다"

    assert row["sold_quantity"] is None
    assert row["derived_quantity"] is None
    assert row["derived_blocked_by"] == "sold"
    assert row["variance_vs_snapshot"] == 20.0
    assert body["totals"]["sold"] is None


def test_http_empty_snapshot_is_not_an_empty_success(env):
    client, _ = env
    body = client.get("/api/otao-po/stock").json()
    assert body["snapshot_empty"] is True
    assert body["snapshot_count"] == 0
    assert any("찍은 적 없음" in n for n in body["notes"])


# ══════════════════════════════════════════════════════════════════════════
# 적대 리뷰 1R 상환 — P1 3건 + 생존 변이 5종
# ══════════════════════════════════════════════════════════════════════════


def test_p1_3_split_counts_do_not_erase_the_earlier_round(session):
    """★P1-3: 10 SKU를 두 번에 나눠 세면 초판은 **앞 회차를 「실사 미실시」로 지웠다.**

    경고 한 줄 없이 «센 것»이 «안 셌다»가 됐다. 계약 §4 S4가 요구하는 건 10 SKU이고
    나눠 세는 쪽이 현실 경로다.
    """
    _seed_prod_shaped(session)
    early = datetime(2026, 9, 3, 10, 0, 0)
    late = datetime(2026, 9, 3, 14, 0, 0)
    _snap(session, early, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    _snap(session, early, "본사", "(실사)", "GAPIP15", 9, source="manual")
    _snap(session, late, "본사", "(실사)", "GAPIP12MI", 0, source="manual")
    session.commit()

    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    # 앞 회차 두 건이 살아 있다
    assert row["GAPIP16PR"].counted_quantity == Decimal("320")
    assert row["GAPIP15"].counted_quantity == Decimal("9")
    assert row["GAPIP12MI"].counted_quantity == Decimal("0")
    assert s.totals["counted_sku_count"] == 3
    assert s.counted_from == early and s.counted_at == late
    assert any("여러 회차에 나뉘어" in n for n in s.notes)


def test_p1_3b_same_code_counted_twice_uses_the_later_one(session):
    """같은 코드를 다시 세면 «그 코드의 최신»이 이긴다 — 회차 전체가 아니라 코드별로."""
    _seed_prod_shaped(session)
    _snap(session, datetime(2026, 9, 3, 10, 0), "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    _snap(session, datetime(2026, 9, 4, 10, 0), "본사", "(실사)", "GAPIP16PR", 330, source="manual")
    session.commit()
    row = {r.product_code: r for r in build_stock(session).rows}
    assert row["GAPIP16PR"].counted_quantity == Decimal("330")


def test_p1_2_counting_a_different_warehouse_is_not_called_a_variance(session):
    """★P1-2: 기준은 «본사»인데 본사-포장을 세면 그 차이는 **오차가 아니라 다른 축**이다.

    초판은 어느 창고를 셌는지 응답·화면 어디에도 안 실어서, `본사 340 vs 포장 900`의 차이가
    「대조 오차 −560」이라는 이름으로 화면에 설 수 있었다.
    """
    _seed_prod_shaped(session)
    _snap(session, T1, "본사-포장", "00011", "GAPIP16PR", 900, source="manual")
    session.commit()

    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    r = row["GAPIP16PR"]
    assert r.counted_warehouse == "본사-포장"
    assert r.counted_warehouse_role == "material"
    assert r.counted_axis_mismatch is True
    assert "GAPIP16PR" in s.counted_axis_mismatches
    assert any("기준 창고(본사)가 아닌" in n for n in s.notes)


def test_counting_the_own_warehouse_is_not_flagged(session):
    _seed_prod_shaped(session)
    _snap(session, T1, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    session.commit()
    s = build_stock(session)
    row = {r.product_code: r for r in s.rows}
    assert row["GAPIP16PR"].counted_axis_mismatch is False
    assert s.counted_axis_mismatches == []
    assert row["GAPIP16PR"].counted_warehouse == "본사"


def test_migration_parent_is_the_real_head():
    """★P1-1: 병합 즉시 head가 둘이 되면 `alembic upgrade head`가 죽는다.

    브랜치 단독으로는 head 1개라 PR 안에서 초록으로 보인다 — 그래서 **파일을 직접 읽어**
    부모가 실제 head인지 잠근다. 이 테스트는 「이 리비전이 어떤 부모를 «선언»했는가」만 본다
    (원칙 23-A: 세는 것만 한다 — 실제 head 계산은 배포 절차가 한다).
    """
    import pathlib
    import re

    text = pathlib.Path(
        "alembic/versions/otaostk1s4a_add_otao_stock_snapshot.py"
    ).read_text(encoding="utf-8")
    m = re.search(r'^down_revision:[^=]*=\s*"([^"]+)"', text, re.M)
    assert m, "down_revision 선언을 못 찾았다"
    # ★`cst60auto`는 origin/main의 `exgrade1s2`와 부모가 겹쳐 head를 둘로 만든다.
    assert m.group(1) != "cst60auto"


def test_ip_guard_refuses_unlisted_and_unknown():
    """★§3-3 집행에 테스트가 0건이었다 — 가드를 `if False:`로 지워도 전건 초록이었다.

    「모르면 안전」이 아니라 **「모르면 중단」**이다.
    """
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "_ecount_stock_export", pathlib.Path("scripts/ecount_stock_export.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 모듈 로드만 — 네트워크·ECOUNT 임포트는 main() 안에 있다

    assert mod.ip_is_allowed("183.99.236.174") is True  # Jino 등록 Mac
    assert mod.ip_is_allowed("168.107.19.222") is True  # 승인 VM(= sellc prod)
    assert mod.ip_is_allowed("211.234.188.208") is False  # 2026-08-27 실측: 바뀐 IP
    assert mod.ip_is_allowed(None) is False  # 못 알아냈으면 «중단»
    assert mod.ip_is_allowed("") is False
    # 계약 §3-3 집행 규칙 ②: 실패 최대 2회
    assert mod._MAX_ATTEMPTS == 2


def test_http_body_values_are_asserted_not_just_keys(env):
    """★생존 변이 상환 — 초판 HTTP 테스트가 `key in body`만 봐서 **값을 비워도 안 잡혔다.**

    교훈 #290(존재 게이트 ≠ 성숙 게이트)의 재현이다. 자백 필드는 «있는 것»이 아니라
    «말하는 것»이라야 한다.
    """
    client, s = env
    _seed_prod_shaped(s)
    _snap(s, T0, "제3창고", "00099", "GAPIP15", 77)
    _snap(s, T1, "본사", "(실사)", "GAPIP16PR", 320, source="manual")
    s.commit()

    body = client.get("/api/otao-po/stock").json()

    # ① 이유가 «비어 있지 않다»
    assert body["sold_unavailable_reason"]
    assert "교집합" in body["sold_unavailable_reason"]
    # ② 역할 미상 창고가 «값으로» 실린다
    assert body["unknown_warehouses"] == [{"warehouse": "제3창고", "quantity": 77.0}]
    # ③ notes가 비어 있지 않다
    assert len(body["notes"]) >= 2
    # ④ 실사 시각이 실린다
    assert body["counted_at"] is not None
    # ⑤ ★창고 역할이 **합쳐지지 않았다** — 계약 §1이 금지한 그 합계
    row = {r["product_code"]: r for r in body["rows"]}["GAPIP16PR"]
    assert row["baseline_by_role"] == {"own": 340.0, "material": 900.0, "channel": 120.0}
    assert row["counted_warehouse"] == "본사"
    assert row["counted_axis_mismatch"] is False
