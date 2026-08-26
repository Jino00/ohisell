# test_cost_auto_refresh.py — 단가 자동 갱신 (D-CPP-60 갈래② / 합격 ②③④⑤)
#
# ★**이 파일이 지키는 것은 «자동이 무엇을 안 하는가»다.** 계약 §7-4의 불변식:
#   **사람 연결 1회 없이는 짝이 생기지 않는다.** 자동이 가져가는 것은 「연결」 버튼의 반복
#   클릭뿐이고, 판단(이 라인이 이 부자재인가 · 새 종을 승인할까)은 전부 사람에 남는다.
#   그래서 «연결됐다»를 재는 테스트보다 **«연결 안 됐고 큐로 갔다»를 재는 테스트가 더 중요하다**
#   — 자동이 게이트를 뒷문으로 여는 것이 이 슬라이스의 유일한 큰 위험이다.
#
# ★그리고 **침묵 금지**(§2-6): `updated=0`인 회전도 행을 남겨야 한다. 아무것도 안 남기면
#   «돌았는데 바뀔 게 없었다»와 «죽었다»가 화면에서 똑같이 보인다 — 이 저장소가 반복 실측한
#   fail-open이다.
#
# ★표면 절단 변이 방어: SUR-3~5는 「함수가 값을 만드나」가 아니라 **「사람이 그걸 보나」**를
#   잰다 — 라우터를 지우거나 payload 키를 빼면 깨진다.
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import (
    CostMaterial,
    CostMaterialPrice,
    CostSetting,
    ImportInvoiceLine,
    ImportShipment,
)
from app.services.cost_menu import auto_refresh as AR


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def client(session_factory):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    tc.testing_session = session_factory
    with session_factory() as s:
        s.add_all(
            [
                CostSetting(key="valuation_method", value="fifo", confirmed=False),
                CostSetting(key="standard_price_rule", value="latest", confirmed=True),
            ]
        )
        s.commit()
    yield tc
    app.dependency_overrides.clear()


# ──────────────────────────────────────────────
# prod 실건의 축소 재현 — cleaning kit 두 로트
# ──────────────────────────────────────────────
KIT = "cleaning kit"


def _shipment(s, hbl: str, decl: date, status: str = "confirmed") -> ImportShipment:
    ship = ImportShipment(
        hbl_no=hbl,
        status=status,
        fx_rate=D("209.88"),
        currency="CNY",
        declaration_date=decl,
        shipper_name="SHENZHEN OTAO TECHNOLOGY LIMITED",
        allocation_basis="amount",
    )
    s.add(ship)
    s.flush()
    return ship


def _line(s, ship, name: str, ex: str, inc: str, seq: int = 1) -> ImportInvoiceLine:
    ln = ImportInvoiceLine(
        shipment_id=ship.id,
        seq=seq,
        item_name=name,
        quantity=D("2400"),
        unit_price_foreign=D("1"),
        line_type="material",
        unit_cost_ex_vat=D(ex),
        unit_cost_inc_vat=D(inc),
    )
    s.add(ln)
    s.flush()
    return ln


def _seed_human_link(s) -> tuple[int, int]:
    """★사람이 «한 번» 연결한 상태를 만든다 — 자동이 반복할 수 있는 유일한 근거다."""
    m = CostMaterial(name=KIT, unit="ea", category="부자재", status="approved")
    s.add(m)
    s.flush()
    old_ship = _shipment(s, "SETR2607220324", date(2026, 7, 23))
    old_line = _line(s, old_ship, KIT, "178.78", "196.66")
    s.add(
        CostMaterialPrice(
            material_id=m.id,
            source="ledger",
            import_invoice_line_id=old_line.id,
            linked_item_name=old_line.item_name,   # ★자동의 매칭 키는 이것뿐이다
            linked_shipment_id=old_ship.id,
            unit_price_ex_vat=D("178.78"),
            unit_price_inc_vat=D("196.66"),
            effective_date=old_ship.declaration_date,
        )
    )
    s.commit()
    return m.id, old_line.id


# ──────────────────────────────────────────────
# ★불변식 — 첫 연결은 영원히 사람 (계약 §7-4)
# ──────────────────────────────────────────────
class TestFirstLinkStaysHuman:
    def test_unknown_item_is_queued_not_linked(self, client):
        """사람이 연결한 적 없는 품목은 **자동이 안 만든다.** 큐로 올린다."""
        with client.testing_session() as s:
            ship = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, ship, "처음 보는 부자재", "190.82", "209.90")
            s.commit()
            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert result.updated == 0, "사람 연결 없이 자동이 단가를 만들었다 — §7-4 위반"
            assert result.queued == 1
            assert s.query(CostMaterialPrice).count() == 0

    def test_ambiguous_name_is_not_guessed(self, client):
        """같은 품목명이 두 종에 갈려 있으면 **자동이 고르지 않는다** — 고르는 것이 판단이다."""
        with client.testing_session() as s:
            a = CostMaterial(name="종A", status="approved")
            b = CostMaterial(name="종B", status="approved")
            s.add_all([a, b])
            s.flush()
            sh = _shipment(s, "OLD-1", date(2026, 7, 1))
            l1 = _line(s, sh, "모호품목", "100", "110", seq=1)
            l2 = _line(s, sh, "모호품목", "100", "110", seq=2)
            for mid, ln in ((a.id, l1), (b.id, l2)):
                s.add(
                    CostMaterialPrice(
                        material_id=mid, source="ledger",
                        import_invoice_line_id=ln.id,
                        linked_item_name="모호품목", linked_shipment_id=sh.id,
                        unit_price_ex_vat=D("100"), unit_price_inc_vat=D("110"),
                        effective_date=sh.declaration_date,
                    )
                )
            new = _shipment(s, "NEW-1", date(2026, 8, 18))
            _line(s, new, "모호품목", "190.82", "209.90")
            s.commit()

            before = s.query(CostMaterialPrice).count()
            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert result.updated == 0, "모호한 이름을 자동이 골랐다 — 판단을 자동화했다"
            assert result.queued == 1
            assert s.query(CostMaterialPrice).count() == before

    def test_draft_shipment_is_not_touched(self, client):
        """확정 전 로트는 «계산된 적 없는 값»이다 — 자동이 안 가져간다."""
        with client.testing_session() as s:
            _seed_human_link(s)
            draft = _shipment(s, "DRAFT-1", date(2026, 8, 18), status="draft")
            _line(s, draft, KIT, "190.82", "209.90")
            s.commit()
            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert result.checked == 0
            assert result.updated == 0


# ──────────────────────────────────────────────
# 자동이 «하는» 일 — 사람이 만든 짝의 반복
# ──────────────────────────────────────────────
class TestRepeatsKnownPair:
    def test_new_lot_of_a_known_pair_is_linked(self, client):
        with client.testing_session() as s:
            material_id, _ = _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()

            result = AR.run(s, trigger=AR.TRIGGER_EVENT, shipment_id=new.id)
            s.commit()
            assert result.updated == 1, "사람이 연결해 둔 짝인데 자동이 안 이었다"
            prices = (
                s.query(CostMaterialPrice)
                .filter(CostMaterialPrice.material_id == material_id)
                .all()
            )
            assert len(prices) == 2, "이력이 쌓이지 않았다 — append가 아니라 덮어썼다"
            assert {str(p.unit_price_ex_vat) for p in prices} == {"178.78", "190.82"}

    def test_second_run_is_idempotent(self, client):
        """이미 연결된 라인을 다시 만들지 않는다 — 같은 로트가 두 번 세지면 이력이 거짓말이 된다."""
        with client.testing_session() as s:
            _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()
            AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            count_after_first = s.query(CostMaterialPrice).count()
            second = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert second.updated == 0
            assert s.query(CostMaterialPrice).count() == count_after_first


# ──────────────────────────────────────────────
# ★침묵 금지 (계약 §2-6 · 합격 ④)
# ──────────────────────────────────────────────
class TestNoSilence:
    def test_a_run_with_nothing_to_do_still_leaves_a_row(self, client):
        """★`updated=0`인 회전도 남는다 — 그 행이 「자동이 살아 있다」의 유일한 증거다."""
        with client.testing_session() as s:
            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert result.updated == 0 and result.checked == 0
            runs = AR.recent_runs(s)
            assert len(runs) == 1, "할 일이 없다고 회전 기록조차 안 남겼다 — 침묵이다"
            assert runs[0]["checked"] == 0
            assert runs[0]["started_at"] is not None
            assert runs[0]["finished_at"] is not None

    def test_empty_history_means_never_ran_not_nothing_changed(self, client):
        """★한 번도 안 돈 것과 「바뀔 게 없었다」는 다르다 — 목록이 비어야 전자다."""
        with client.testing_session() as s:
            assert AR.recent_runs(s) == []


# ──────────────────────────────────────────────
# ★SUR — 사람이 그걸 «보나» (표면 절단 변이 방어)
# ──────────────────────────────────────────────
class TestSurface:
    def test_SUR_3_run_log_reaches_the_screen(self, client):
        """★SUR-3: `/auto-refresh/runs` 라우터를 지우거나 payload 키를 빼면 깨진다."""
        with client.testing_session() as s:
            _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()
        r = client.post("/api/cost/auto-refresh/run")
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == 1

        r2 = client.get("/api/cost/auto-refresh/runs")
        assert r2.status_code == 200, r2.text
        items = r2.json()["items"]
        assert items, "회전이 화면 목록에 안 실린다 — 표면이 끊겼다"
        top = items[0]
        for key in ("trigger", "started_at", "checked", "updated", "failed", "queued"):
            assert key in top, f"`{key}`가 화면까지 안 간다"
        # 개별 사건의 좌표까지 가야 「어느 로트에서 왔나」를 사람이 안다.
        entry = top["entries"][0]
        assert entry["hbl_no"] == "SETR2608170216"
        assert entry["outcome"] == AR.OUTCOME_LINKED
        assert entry["new_price_ex_vat"] == "190.82"

    def test_SUR_4_queue_reaches_the_screen_with_a_reason(self, client):
        """★SUR-4: 큐가 화면에 안 가면 「사람이 해야 할 일」이 통째로 사라진다(합격 ⑤)."""
        with client.testing_session() as s:
            ship = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, ship, "처음 보는 부자재", "190.82", "209.90")
            s.commit()
        client.post("/api/cost/auto-refresh/run")
        r = client.get("/api/cost/auto-refresh/queue")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1, "자동이 안 건드린 라인이 큐에 안 뜬다"
        assert items[0]["item_name"] == "처음 보는 부자재"
        # ★사유가 비어 있으면 화면에서 침묵과 같다.
        assert items[0]["message"], "대기 사유가 비었다"

    def test_SUR_5_setting_change_leaves_history_even_when_value_is_unchanged(
        self, client
    ):
        """★SUR-5: 「선입선출 재확인」은 값 변경이 아니라 **사람이 확인했다는 사건**이다(합격 ②).

        값 비교로 걸러 버리면 그 확인 행위가 통째로 사라진다 — §74의 「신고한 방법」 확인
        기록이 바로 그것이다.
        """
        r = client.post(
            "/api/cost/settings/valuation_method",
            json={"value": "fifo", "confirmed": False, "actor": "jino",
                  "note": "홈택스 확인 전 재확인"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["value_changed"] is False, "값은 안 바뀌는 것이 맞다"

        h = client.get("/api/cost/settings/history")
        assert h.status_code == 200, h.text
        items = h.json()["items"]
        assert len(items) == 1, "값이 안 바뀌었다고 확인 행위가 사라졌다"
        assert items[0]["actor"] == "jino"
        assert items[0]["old_value"] == "fifo" and items[0]["new_value"] == "fifo"

    def test_SUR_5b_declaring_an_unimplemented_rule_is_refused(self, client):
        """구현 없이 «선언»만 바꾸면 계산이 멈춘다 — 그 상태를 만들지 않는다."""
        r = client.post(
            "/api/cost/settings/standard_price_rule", json={"value": "moving_avg_n"}
        )
        assert r.status_code == 400, r.text
        assert "구현된 규칙이 아니다" in r.text
