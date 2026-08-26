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


# ══════════════════════════════════════════════════════════════════════
# 적대 리뷰 1R 회귀 — P1 3건 + SURVIVED 변이 3건
#
# ★아래 테스트들이 없던 동안 리뷰어의 변이가 **그대로 살아남았다**:
#   · `MAX_ATTEMPTS` 락(§3 금지선)을 통째로 지워도 623 passed
#   · 실패 시 `CostAutoRefreshEntry(FAILED)` 기록을 지워도 623 passed
#   이 계약의 «금지선»과 «침묵 금지»에 회귀 테스트가 0건이었다는 뜻이다.
# ══════════════════════════════════════════════════════════════════════
class TestReviewRound1Regressions:
    def test_P1_1_queue_survives_a_narrow_event_run(self, client):
        """★P1-1: 로트 하나만 스캔한 이벤트 회전이 **다른 로트의 할 일을 가리면 안 된다.**

        초판은 큐를 「최신 회전의 queued 항목」으로 만들어서, 로트가 확정될 때마다(=정상
        업무 흐름에서 매번) 좁은 이벤트 회전이 최신이 되며 이전 대기 항목이 **화면에서
        통째로 사라졌다.** 지금은 큐를 «현재 상태»에서 만든다.
        """
        with client.testing_session() as s:
            sh1 = _shipment(s, "LOT-A", date(2026, 8, 1))
            _line(s, sh1, "처음보는품목A", "100", "110", seq=1)
            _line(s, sh1, "처음보는품목B", "200", "220", seq=2)
            s.commit()
            AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert {q["item_name"] for q in AR.pending_queue(s)} == {
                "처음보는품목A", "처음보는품목B",
            }

            # 무관한 세 번째 로트가 확정 → 그 로트만 보는 이벤트 회전
            sh2 = _shipment(s, "LOT-C", date(2026, 8, 18))
            _line(s, sh2, "처음보는품목C", "300", "330", seq=1)
            s.commit()
            AR.run(s, trigger=AR.TRIGGER_EVENT, shipment_id=sh2.id)
            s.commit()

            names = {q["item_name"] for q in AR.pending_queue(s)}
            assert names == {"처음보는품목A", "처음보는품목B", "처음보는품목C"}, (
                f"좁은 이벤트 회전이 이전 대기 항목을 가렸다 — 지금 큐: {names}"
            )
            # 사유는 여전히 비면 안 된다(§2-6).
            assert all(q["message"] for q in AR.pending_queue(s))

    def test_P1_1b_queue_drops_the_line_once_a_human_links_it(self, client):
        """반대 방향도 지킨다 — 사람이 연결하면 그 라인은 큐에서 «즉시» 빠진다."""
        with client.testing_session() as s:
            m = CostMaterial(name="종X", status="approved")
            s.add(m)
            s.flush()
            sh = _shipment(s, "LOT-A", date(2026, 8, 1))
            ln = _line(s, sh, "품목X", "100", "110")
            s.commit()
            AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert len(AR.pending_queue(s)) == 1

            from app.services.cost_menu.materials import link_ledger_line

            link_ledger_line(s, m.id, ln.id, note="사람이 연결")
            s.commit()
            assert AR.pending_queue(s) == []

    def test_P1_2_a_failed_line_leaves_no_committed_price(self, client, monkeypatch):
        """★P1-2: 「failed」로 기록된 라인의 단가가 **실제로 커밋되면 안 된다.**

        초판은 `link_ledger_line`이 단가를 flush한 «뒤» `_propagate`가 터지면 그 flush가
        살아남았다. `run()`은 예외를 안 던지므로 호출자가 그대로 커밋했고, 결과는
        「실패라고 적힌 라인의 단가가 영구히 커밋되는 것」이었다. 게다가 그 라인은
        `_candidate_lines`가 «이미 링크됨»으로 빼서 **재시도도 큐도 안 걸렸다.**
        """
        with client.testing_session() as s:
            material_id, _ = _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()

            before = s.query(CostMaterialPrice).count()
            # `_propagate`(표준원가 재계산)만 터뜨린다 — 단가 flush «뒤»에 도는 자리다.
            import app.services.cost_menu.materials as M

            monkeypatch.setattr(
                M, "_propagate",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("재계산 폭발")),
            )
            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()   # ★라우터·스케줄러가 하는 것과 같은 커밋

            assert result.failed == 1 and result.updated == 0
            assert s.query(CostMaterialPrice).count() == before, (
                "실패로 기록됐는데 단가 행이 커밋됐다 — 로그와 실제 상태가 어긋난다"
            )
            # 그리고 그 라인은 «여전히 미연결»이라 다음 회전이 다시 본다.
            monkeypatch.undo()
            second = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert second.updated == 1, "실패한 라인이 재시도 대상에서 빠졌다"

    def test_P1_3_old_price_is_recorded_so_the_screen_can_say_old_to_new(self, client):
        """★P1-3: `old_price_ex_vat`를 **채우는 코드가 있어야** 화면이 `old → new`를 말한다.

        초판은 이 필드를 채우는 자리가 아예 없어서 항상 `None`이었고, 화면 분기가
        `old ? "old → new" : "new"`라 **「178.78 → 190.82」가 원리적으로 절대 안 떴다.**
        """
        with client.testing_session() as s:
            _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()
            AR.run(s, trigger=AR.TRIGGER_EVENT, shipment_id=new.id)
            s.commit()

            runs = AR.recent_runs(s)
            entry = next(
                e for e in runs[0]["entries"] if e["outcome"] == AR.OUTCOME_LINKED
            )
            assert entry["old_price_ex_vat"] == "178.78", (
                "연결 «전» 채택 단가가 안 남았다 — 화면이 old→new를 못 말한다"
            )
            assert entry["new_price_ex_vat"] == "190.82"

    def test_SURVIVED_1_max_attempts_lock_is_actually_enforced(self, client):
        """★리뷰어 변이 생존분: `MAX_ATTEMPTS` 락(§3 무한 재시도 금지)을 지워도 전건 초록이었다.

        이 계약의 **금지선**에 회귀 테스트가 0건이었다는 뜻이다.
        """
        with client.testing_session() as s:
            material_id, _ = _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            ln = _line(s, new, KIT, "190.82", "209.90")
            s.commit()
            # 실패 이력을 MAX_ATTEMPTS만큼 심는다(회전 행 없이 직접 — 판정 근거는 개수다).
            from app.models import CostAutoRefreshEntry as E, CostAutoRefreshRun as R

            r = R(trigger=AR.TRIGGER_CRON)
            s.add(r)
            s.flush()
            for _ in range(AR.MAX_ATTEMPTS):
                s.add(E(run_id=r.id, outcome=AR.OUTCOME_FAILED,
                        import_invoice_line_id=ln.id, item_name=KIT))
            s.commit()

            result = AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()
            assert result.updated == 0, "3회 실패한 라인을 또 시도했다 — §3 금지선 위반"
            assert result.queued == 1
            q = AR.pending_queue(s)
            assert any(str(AR.MAX_ATTEMPTS) in (x["message"] or "") for x in q), (
                "큐에 고정됐다는 사유가 사람에게 안 보인다"
            )

    def test_SURVIVED_2_a_failure_always_leaves_a_row_with_a_reason(
        self, client, monkeypatch
    ):
        """★리뷰어 변이 생존분: 실패 시 `FAILED` 행 기록을 지워도 전건 초록이었다(§2-6 침묵)."""
        with client.testing_session() as s:
            _seed_human_link(s)
            new = _shipment(s, "SETR2608170216", date(2026, 8, 18))
            _line(s, new, KIT, "190.82", "209.90")
            s.commit()

            import app.services.cost_menu.materials as M

            monkeypatch.setattr(
                M, "_propagate",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("재계산 폭발")),
            )
            AR.run(s, trigger=AR.TRIGGER_CRON)
            s.commit()

            runs = AR.recent_runs(s)
            failed = [
                e for e in runs[0]["entries"] if e["outcome"] == AR.OUTCOME_FAILED
            ]
            assert len(failed) == 1, "실패가 어느 행에도 안 남았다 — 침묵이다"
            assert "재계산 폭발" in (failed[0]["message"] or ""), (
                "실패 사유가 비었다 — 사유 없는 실패는 화면에서 침묵과 같다"
            )
            assert runs[0]["failed"] == 1

    def test_2R_P2_stale_queue_reason_is_dropped_when_the_pair_appears(self, client):
        """★적대 리뷰 2R P2: 짝이 «생긴» 뒤에도 옛 사유가 「연결된 적 없다」로 남으면 안 된다.

        사람이 같은 품목명을 다른 라인에서 연결하면 그 이름은 짝이 된다. 그런데 다음 회전
        전까지 큐는 옛 회전의 사유를 그대로 보여줬다 — 「내가 방금 연결했는데?」에서 멈춘다.
        ★반대로 `MAX_ATTEMPTS` 고정 사유는 **보존돼야 한다**(종이 같고 여전히 참이다).
        """
        with client.testing_session() as s:
            m = CostMaterial(name="종Y", status="approved")
            s.add(m)
            s.flush()
            sh = _shipment(s, "LOT-A", date(2026, 8, 1))
            l1 = _line(s, sh, "같은품목", "100", "110", seq=1)
            l2 = _line(s, sh, "같은품목", "100", "110", seq=2)
            s.commit()

            AR.run(s, trigger=AR.TRIGGER_CRON)   # 아직 짝이 없다 → 둘 다 큐
            s.commit()
            assert all("연결된 적이 없다" in q["message"] for q in AR.pending_queue(s))

            # 사람이 l1을 연결한다 → 「같은품목」이 짝이 된다. 회전은 아직 안 돌았다.
            from app.services.cost_menu.materials import link_ledger_line

            link_ledger_line(s, m.id, l1.id, note="사람이 연결")
            s.commit()

            q = AR.pending_queue(s)
            assert len(q) == 1 and q[0]["import_invoice_line_id"] == l2.id
            assert "연결된 적이 없다" not in q[0]["message"], (
                f"짝이 생겼는데 옛 사유가 남았다: {q[0]['message']}"
            )
            assert q[0]["material_id"] == m.id
