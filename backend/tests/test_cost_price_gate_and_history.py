# test_cost_price_gate_and_history.py — 계약 D-CPP-64 §4 S1 (이력과 문)
#
# ## 이 파일이 지키는 것 셋 (합격기준과 1:1)
#
#   S1-① 이력이 남는다   — 값이 바뀌면 «시각·경로·old→new·근거» 1행 + **HTTP로 나온다**
#   S1-② 안 잠긴 문이 닫힌다 — `POST/PUT /api/products`의 `cost_price`가 거부되고 **사유가 문장**
#   S1-③ fail-open이 자백한다 — 정본 스냅샷이 없으면 헬스가 「가드 미작동」을 말한다
#
# ★**HTTP body를 단언한다.** 서비스층 dict만 보면 `response_model`이 키를 지우는 사고를 못
#   잡는다(교훈 #321 — 서비스층 9건 초록인데 화면엔 배너가 통째로 안 떴다).
# ★**화면까지 가는 경로를 끊는 변이**를 상정하고 쓴다: 라우터가 이력을 안 부르면·응답에서
#   키가 사라지면·배너 조건이 뒤집히면 여기서 죽어야 한다(§4 표면 절단 변이).
from __future__ import annotations

import pathlib
from datetime import datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CostPriceHistory, ProductMaster
from app.services import cost_price_history as CPH
from app.services.scheduler_health import build_health

NOW = datetime(2026, 8, 31, 21, 0, 0)


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정(autoflush=False) — 픽스처가 prod와 다르면 결함을 못 잡는다.
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    tc.testing_session = TestingSession

    with TestingSession() as s:
        s.add(ProductMaster(
            internal_sku="OHI-0001", product_name="지문방지 필름 3매", cost_price=D("2350.7")
        ))
        s.commit()
    yield tc
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════
# S1-② 안 잠긴 문이 닫힌다
# ═══════════════════════════════════════════════════════════════════


def test_put_product_with_cost_price_is_rejected_with_the_sentence(client):
    """★핵심: 상품 수정 화면으로 원가를 밀어 넣던 문이 닫혔다.

    2026-08-31까지 이 요청은 200을 주고 `setattr` 루프로 값을 그대로 넣었다(무검사·무이력).
    """
    pid = client.get("/api/products").json()[0]["id"]
    r = client.put(f"/api/products/{pid}", json={"cost_price": "9999"})
    assert r.status_code == 400, "안 잠긴 문이 그대로 열려 있다"
    # ★거부만으로는 부족하다 — **사유가 문장으로** 나와야 사람이 어디로 갈지 안다(§4 S1-②).
    assert CPH.REJECTION_SENTENCE in r.json()["detail"]
    assert "원가 메뉴" in r.json()["detail"]

    with client.testing_session() as s:
        p = s.query(ProductMaster).filter_by(internal_sku="OHI-0001").one()
        assert p.cost_price == D("2350.70"), "거부했다면서 값이 바뀌었다"


def test_post_product_with_cost_price_is_rejected(client):
    """★신규 등록에도 구멍을 안 남긴다 — 「등록 때 한 번은 아무 값이나」가 곧 정본 행세다."""
    r = client.post(
        "/api/products",
        json={"internal_sku": "OHI-NEW", "product_name": "새 상품", "cost_price": "1234"},
    )
    assert r.status_code == 400
    assert CPH.REJECTION_SENTENCE in r.json()["detail"]


def test_put_without_cost_price_still_works(client):
    """★닫는 것은 원가 칸 하나지 이 API 전체가 아니다 — 상품명·메모 수정은 정상이어야 한다.

    이 단언이 없으면 「전부 거부」로 고쳐도 위 두 테스트가 초록이다(아무것도 안 지키는 짝).
    """
    pid = client.get("/api/products").json()[0]["id"]
    r = client.put(f"/api/products/{pid}", json={"product_name": "이름만 바꾼다"})
    assert r.status_code == 200, r.text
    assert r.json()["product_name"] == "이름만 바꾼다"
    assert D(str(r.json()["cost_price"])) == D("2350.70")


def test_rejection_even_when_value_is_unchanged(client):
    """★같은 값이어도 거부한다.

    「같으면 통과」면 문의 상태가 값에 따라 달라져 **한 줄로 말할 수 없는 문**이 된다.
    """
    pid = client.get("/api/products").json()[0]["id"]
    r = client.put(f"/api/products/{pid}", json={"cost_price": "2350.7"})
    assert r.status_code == 400


def test_frontend_uses_the_same_sentence_as_the_backend():
    """★API가 A라 하고 화면이 B라 하는 상태를 막는다 — 문구는 한 벌이다.

    프론트 사본은 `frontend/src/lib/costPriceGate.ts`. 둘 중 하나만 고쳐지면 여기서 죽는다.
    """
    ts = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "lib" / "costPriceGate.ts"
    )
    assert ts.exists(), f"프론트 사본이 없다: {ts}"
    assert CPH.REJECTION_SENTENCE in ts.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# S1-① 이력이 남는다
# ═══════════════════════════════════════════════════════════════════


def test_values_differ_treats_decimal_scale_as_same_value():
    """★`300` vs `300.00`은 **같은 값**이다.

    문자열로 비교하면 `Numeric(12,2)` 왕복만으로 «변경» 행이 매일 쌓인다 — 시끄러운 이력은
    아무도 안 읽고, 안 읽는 이력은 없는 것과 같다.
    """
    assert CPH.values_differ(D("300"), D("300.00")) is False
    assert CPH.values_differ(D("300"), D("300.01")) is True
    # 「없음 → 값」은 사건이다(신규 등록).
    assert CPH.values_differ(None, D("0")) is True
    assert CPH.values_differ(None, None) is False


def test_unknown_path_raises_instead_of_silently_recording():
    """★오타 난 경로 이름은 「어느 문이 열려 있나」를 세는 순간 틀린 답을 만든다."""
    with pytest.raises(ValueError):
        CPH.record_cost_price_change(
            None, internal_sku="X", old_value=None, new_value=D("1"), path="typo_path"
        )


def test_excel_upload_records_history_row(client):
    """★배선: 업로드로 원가가 바뀌면 이력 1행이 **같은 커밋에** 남는다.

    라우터가 `record_cost_price_change` 호출을 잃으면(표면 절단 변이) 여기서 죽는다.
    """
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "상품 원가표"
    ws.append(["사내SKU", "상품명", "원가", "카테고리", "메모"])
    ws.append(["OHI-0001", "지문방지 필름 3매", 2500, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post(
        "/api/products/upload",
        files={"file": ("t.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text

    with client.testing_session() as s:
        rows = s.query(CostPriceHistory).all()
        assert len(rows) == 1, "값은 바뀌었는데 이력이 없다"
        h = rows[0]
        assert h.internal_sku == "OHI-0001"
        assert h.old_value == D("2350.70")
        assert h.new_value == D("2500.00")
        assert h.path == CPH.PATH_EXCEL_UPLOAD
        # 근거는 **좌표**여야 한다 — 「업로드됨」 같은 말은 되짚을 수 없다.
        assert "t.xlsx" in h.reason and "행 2" in h.reason


def test_history_reaches_the_user_over_http(client):
    """★★사람이 보는 표면까지 간다 — 서비스층 dict가 아니라 **HTTP body**를 단언한다.

    라우터가 없거나 응답에서 키가 지워지면(교훈 #321의 모양) 여기서 죽는다.
    """
    with client.testing_session() as s:
        s.add(CostPriceHistory(
            internal_sku="OHI-0001", old_value=D("2350.7"), new_value=D("2500"),
            path=CPH.PATH_EXCEL_UPLOAD, actor="excel", reason="엑셀 「t.xlsx」 행 2",
        ))
        s.commit()

    body = client.get("/api/cost/price-history").json()
    assert body["total"] == 1
    assert body["empty_reason"] is None
    assert body["started_at"] is not None, "이력 시작 시각이 없으면 «소급 불가»를 못 말한다"
    item = body["items"][0]
    assert item["internal_sku"] == "OHI-0001"
    assert item["old_value"] == "2350.70"
    assert item["new_value"] == "2500.00"
    assert item["path"] == CPH.PATH_EXCEL_UPLOAD
    assert item["reason"] == "엑셀 「t.xlsx」 행 2"


def test_empty_history_says_why_instead_of_going_quiet(client):
    """★0건은 「원가가 안 바뀌었다」가 아니다 — 대개 「아직 시작 안 됐다」다(교훈 #123).

    `empty_reason`이 사라지면 화면이 빈 표를 «이상 없음»으로 읽는다.
    """
    body = client.get("/api/cost/price-history").json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["empty_reason"] and "소급 불가" in body["empty_reason"]


def test_new_product_records_none_not_zero_as_old_value(client):
    """★「없음 ≠ 0」 — 신규 등록의 옛 값은 **NULL**이다.

    0으로 적으면 「0원이었다가 올랐다」는 **없던 사실**이 이력에 생긴다.
    """
    with client.testing_session() as s:
        CPH.record_cost_price_change(
            s, internal_sku="OHI-NEW", old_value=None, new_value=D("1200"),
            path=CPH.PATH_MAPPING_INGEST, actor="excel", reason="신규",
        )
        s.commit()
    item = client.get("/api/cost/price-history").json()["items"][0]
    assert item["old_value"] is None


# ═══════════════════════════════════════════════════════════════════
# S1-③ fail-open이 자백한다
# ═══════════════════════════════════════════════════════════════════


def test_build_health_exposes_cost_guard_key_even_when_active():
    """★키는 항상 있다 — 없는 키와 «정상»이 프론트에서 똑같이 falsy면 침묵이 정상으로 읽힌다."""
    h = build_health([], [], set(), True, NOW)
    assert "cost_guard" in h


def test_build_health_turns_unhealthy_when_guard_is_off():
    """★★가드가 꺼지면 healthy=False.

    배너는 healthy=false일 때만 뜬다 — 이 줄이 없으면 화면은 **영영 침묵한다**
    (2026-08-10 `disk_low`가 판정에만 있고 표시가 없어 통째로 숨었던 것의 거울상).
    """
    h = build_health(
        [], [], set(), True, NOW,
        cost_guard={"active": False, "reason": "스냅샷 없음", "snapshot_path": "/x.json"},
    )
    assert h["healthy"] is False
    assert h["cost_guard"]["active"] is False


def test_active_guard_does_not_make_it_unhealthy():
    """★가드가 정상이면 아무 말도 안 한다 — 상시 켜진 경고는 안 켜진 것과 같다."""
    h = build_health(
        [], [], set(), True, NOW,
        cost_guard={"active": True, "reason": None, "snapshot_path": "/x.json"},
    )
    assert h["cost_guard"]["active"] is True
    # healthy 자체는 빈 DB의 다른 사유로도 갈리므로 여기서 단언하지 않는다(교훈 #181).


def test_missing_snapshot_makes_the_guard_report_itself_off(monkeypatch, client):
    """★라이브 모양 재현: 스냅샷 파일이 없으면 «검사 안 함»을 **스스로 말한다**.

    지금까지는 그 상태에서 `cost_drift`가 None이 되어 「어긋남 0건」과 구별이 안 됐다.
    """
    from app.services import cost_truth_audit as cta

    monkeypatch.setattr(cta, "try_load_truth", lambda *a, **k: None)

    from app.services.scheduler_health import compute_scheduler_health

    class _FakeScheduler:
        running = True

        def get_jobs(self):
            return []

    with client.testing_session() as s:
        h = compute_scheduler_health(s, _FakeScheduler(), NOW)

    assert h["cost_guard"]["active"] is False
    assert h["cost_guard"]["reason"], "꺼졌다면서 이유를 안 말한다"
