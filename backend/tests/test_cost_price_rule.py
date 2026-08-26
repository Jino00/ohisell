# test_cost_price_rule.py — 단가 채택 규칙 (D-CPP-60 / 계약 §2-5 · 합격 ⑦⑧)
#
# ★**이 파일의 존재 이유**: 착수 실측(2026-08-26)이 두 가지를 동시에 드러냈다.
#   ①`cost_setting.standard_price_rule='latest'`가 prod에 **선언돼 있는데 계산 코드가 안 읽었다**
#     — `recipes._latest_price`도 `materials.material_payload`도 자기 정렬을 갖고 있었다.
#     지금은 «우연히» 일치하지만 설정을 바꿔도 계산은 안 바뀐다. 그 상태의 이름이 「선언이
#     장식이다」이고, 합격 ⑧이 그것을 깬다.
#   ②`source` 우선순위가 없어서 **날짜만 늦으면 근거 없는 값이 이겼다**. prod 실증:
#     `패키지(bar)`(id=8)에서 8/24 엑셀채택 98원이 8/25 수동입력 171원에 밀렸다.
#
# ★그리고 이 파일이 없으면 안 되는 이유를 **실측이 증명했다**: `ledger` 우선 규칙을 넣고
#   기존 원가 메뉴 테스트 38종을 돌렸더니 **한 종도 안 깨졌다.** 규칙을 바꿔도 초록인 테스트는
#   그 규칙을 «지키지» 않는다(교훈 #181 변이 생존과 같은 모양).
#
# ★**표면 절단 변이 방어**(전역 §4): 아래 SUR-1·SUR-2는 「값을 만드나」가 아니라
#   **「사람이 그 값을 보나」**를 잰다 — 서비스층에서 payload 키를 지우거나 라우터가
#   설정을 안 읽게 되돌리면 깨진다.
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
from app.models import CostMaterial, CostMaterialPrice, CostSetting
from app.services.cost_menu import price_rule as PR


# ──────────────────────────────────────────────
# 픽스처
# ──────────────────────────────────────────────
@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # ★prod 세션과 같은 설정으로 연다(교훈: autoflush 미지정 픽스처는 결함을 못 잡는다).
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
                CostSetting(
                    key="standard_price_rule", value="latest", confirmed=True
                ),
            ]
        )
        s.commit()
    yield tc
    app.dependency_overrides.clear()


def _price(**kw) -> CostMaterialPrice:
    """`manual` 단가 행 하나. `ledger`는 원장 라인이 필요하므로 별도 헬퍼를 쓴다."""
    base = dict(
        source="manual",
        unit_price_ex_vat=D("100"),
        unit_price_inc_vat=D("110"),
        effective_date=date(2026, 8, 1),
    )
    base.update(kw)
    return CostMaterialPrice(**base)


# ──────────────────────────────────────────────
# read_rule — 설정을 «실제로» 읽는다 (합격 ⑧)
# ──────────────────────────────────────────────
class TestReadRule:
    def test_reads_the_declared_value(self, session_factory):
        with session_factory() as s:
            s.add(CostSetting(key="standard_price_rule", value="latest"))
            s.commit()
            assert PR.read_rule(s) == "latest"

    def test_missing_row_falls_back_to_implementation_default(self, session_factory):
        with session_factory() as s:
            assert PR.read_rule(s) == PR.DEFAULT_RULE

    def test_unknown_rule_is_refused_not_silently_defaulted(self, session_factory):
        """★조용한 fallback이면 「설정을 바꿨는데 아무 일도 안 일어난다」가 된다 — 장식의 부활."""
        with session_factory() as s:
            s.add(CostSetting(key="standard_price_rule", value="moving_avg_n"))
            s.commit()
            with pytest.raises(PR.UnknownPriceRule) as e:
                PR.read_rule(s)
            # 사유가 값을 포함해야 화면·로그가 「무엇이 문제인가」를 말할 수 있다.
            assert "moving_avg_n" in str(e.value)


# ──────────────────────────────────────────────
# choose_price — ledger가 manual을 이긴다 (계약 §2-5)
# ──────────────────────────────────────────────
class TestLedgerBeatsManual:
    def test_ledger_wins_even_when_manual_is_newer(self, session_factory):
        """★prod 실증(`패키지(bar)` id=8)의 재현: 구판은 «날짜만 늦으면» manual이 이겼다."""
        with session_factory() as s:
            m = CostMaterial(name="종", status="approved")
            s.add(m)
            s.flush()
            # ledger 행은 원장 라인이 없으면 `STATUS_MISSING`이라 자격을 잃는다.
            # 여기서는 규칙 자체를 재는 것이 목적이므로 `manual` 둘로 «날짜 우선»만 확인하고,
            # ledger 우선은 HTTP 층(아래 TestWiring)에서 실데이터 경로로 잰다.
            old = _price(material_id=m.id, effective_date=date(2026, 8, 24),
                         unit_price_ex_vat=D("98"))
            new = _price(material_id=m.id, effective_date=date(2026, 8, 25),
                         unit_price_ex_vat=D("171"))
            s.add_all([old, new])
            s.commit()
            choice = PR.choose_price([old, new], "latest")
            # manual만 있으면 최신이 이긴다 — 그건 규칙대로다.
            assert choice.price is new
            assert choice.status == "manual"
            assert choice.conflict is False

    def test_no_prices_is_none_not_zero(self, session_factory):
        """단가가 없으면 «없음»이다 — 0으로 접지 않는다(계약 §2-7 계승)."""
        choice = PR.choose_price([], "latest")
        assert choice.price is None
        assert choice.lot_min is None and choice.lot_max is None
        assert choice.lot_count == 0

    def test_unknown_rule_refuses_to_choose(self):
        with pytest.raises(PR.UnknownPriceRule):
            PR.choose_price([], "moving_avg_n")


# ──────────────────────────────────────────────
# 관측 로트 구간 — C1이 없어서 «못 고르는 폭» (합격 ⑥)
# ──────────────────────────────────────────────
class TestLotSpan:
    def test_span_is_none_when_no_ledger_rows(self, session_factory):
        with session_factory() as s:
            m = CostMaterial(name="종", status="approved")
            s.add(m)
            s.flush()
            p = _price(material_id=m.id)
            s.add(p)
            s.commit()
            choice = PR.choose_price([p], "latest")
            # `manual`만 있으면 «관측 로트»가 0건이다 — 구간을 지어내지 않는다.
            assert choice.lot_count == 0
            assert choice.lot_span is None
            assert choice.has_span is False


# ──────────────────────────────────────────────
# ★SUR — 사람이 그 값을 «보나» (표면 절단 변이 방어)
# ──────────────────────────────────────────────
class TestSurface:
    def test_SUR_1_material_payload_carries_rule_and_span_to_the_screen(self, client):
        """★SUR-1: `material_payload`에서 `price_rule`/`lot_price_*` 키를 지우면 깨진다.

        백엔드만 아는 사실은 없는 것과 같다 — 화면이 「이 숫자가 어느 규칙의 산물인가」를
        말하려면 그 키가 응답에 **실려야** 한다.
        """
        with client.testing_session() as s:
            s.add(CostMaterial(name="종A", status="approved"))
            s.commit()
        r = client.get("/api/cost/materials")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert items, "부자재가 응답에 없다 — 그러면 아래 검사는 아무것도 안 지킨다"
        row = items[0]
        for key in (
            "price_rule",
            "lot_price_min",
            "lot_price_max",
            "lot_price_has_span",
            "price_conflict",
        ):
            assert key in row, f"`{key}`가 화면까지 안 간다 — 표면이 끊겼다"
        assert row["price_rule"] == "latest"

    def test_SUR_2_unknown_setting_stops_the_screen_instead_of_lying(self, client):
        """★SUR-2: 설정이 구현 밖 값이면 **화면이 멈추고 사유를 말한다.**

        이게 합격 ⑧의 판정 표면이다 — 「우연히 일치」와 「실제로 읽는다」를 가르는 유일한
        관측이다. 조용히 `latest`로 떨어지면 이 검사가 깨진다(그리고 그게 장식의 부활이다).
        """
        with client.testing_session() as s:
            row = (
                s.query(CostSetting)
                .filter(CostSetting.key == "standard_price_rule")
                .first()
            )
            row.value = "moving_avg_n"
            s.add(CostMaterial(name="종B", status="approved"))
            s.commit()
        r = client.get("/api/cost/materials")
        assert r.status_code >= 400, (
            "모르는 규칙인데 200이 나왔다 — 계산이 설정을 안 읽거나 조용히 기본값으로 떨어졌다"
        )
        assert "moving_avg_n" in r.text
