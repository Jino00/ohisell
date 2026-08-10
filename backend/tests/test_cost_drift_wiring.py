# test_cost_drift_wiring.py — 원가 정본 드리프트가 **실제로 헬스 API까지 흐르는지** 지킨다.
#
# ## 왜 이 파일이 따로 있나 (2026-08-10, ref 54 §7-6)
#
# `test_cost_buffer_audit.py`는 **판정 산술**을 지킨다(값이 이러면 buffered인가).
# 그런데 2026-08-10까지 그 산술은 CLI 안에만 있었고 **아무도 안 불렀다** — 산술이 아무리
# 촘촘해도 «부르는 사람이 없으면» 감시가 아니다. 실제로 177건이 여러 달 방치됐다.
#
# 그래서 이 파일이 지키는 것은 산술이 아니라 **배선**이다:
#   product_master → compute_scheduler_health → build_health → /api/scheduler/health → 배너
# 이 사슬 어디가 끊겨도 화면은 «이상 없음»으로 보인다(에러가 안 난다). 그 침묵을 막는다.
#
# ★교훈 #208의 형태: 도구의 값어치는 경계층에 있는데 순수 함수만 촘촘히 물리면
#   «통과하는데 아무것도 안 지키는 테스트»가 된다.
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ProductMaster
from app.services.scheduler_health import build_health, compute_scheduler_health

NOW = datetime(2026, 8, 10, 12, 0, 0)

# 라이브 실측 값(2026-08-10). 정본 2350.7 + 폰 버퍼 265.3 = 2616.
_TRUTH_VALUE = Decimal("2350.7")
_BUFFERED_VALUE = Decimal("2616")
_UNDETERMINED_VALUE = Decimal("3500")  # OHI-TGLASS-IP17PRO의 실제 값 — 원가표에 없다


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class _FakeScheduler:
    """APScheduler 유사 객체. 이 파일은 잡 감시가 아니라 원가 배선만 본다."""

    running = True

    def get_jobs(self):
        return []


def _seed(db, rows):
    for i, (sku, name, cost) in enumerate(rows, start=1):
        db.add(ProductMaster(internal_sku=sku, product_name=name, cost_price=cost))
    db.commit()


# ═══ 순수층 — build_health가 cost_drift를 healthy 판정에 넣는가 ═══


def test_build_health_exposes_cost_drift_key_even_when_clean():
    """★드리프트가 없어도 **키는 있어야** 한다.

    키가 없는 것과 «0건»은 프론트에서 똑같이 falsy다 — 그러면 «판정 자체를 안 함»이
    «이상 없음»으로 읽힌다. 이 프로젝트가 반복해 당한 형태다(교훈 #123).
    """
    h = build_health([], [], set(), True, NOW)
    assert "cost_drift" in h
    assert h["cost_drift"] is None
    assert h["healthy"] is True


def test_build_health_turns_unhealthy_on_cost_drift():
    """★드리프트가 있으면 healthy=False. 이게 배너를 띄우는 유일한 스위치다."""
    h = build_health([], [], set(), True, NOW, cost_drift={"count": 177, "by_buffer": {"폰": 177}})
    assert h["healthy"] is False
    assert h["cost_drift"]["count"] == 177


# ═══ 배선층 — 실제 DB → 헬스 dict ═══


def test_compute_health_detects_buffered_master_values(db):
    """★라이브 재현: 버퍼가 얹힌 값이 마스터에 있으면 헬스가 **스스로** 찾아낸다.

    이 테스트가 죽으면 = 배선이 끊긴 것 = 옛 매핑 엑셀 업로드가 다시 조용해진다.
    """
    _seed(db, [
        ("OHI-0001", "지문방지 필름 3매", _BUFFERED_VALUE),
        ("OHI-0002", "지문방지 필름 3매(정상)", _TRUTH_VALUE),
        ("OHI-0003", "강화유리", _UNDETERMINED_VALUE),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)

    d = h["cost_drift"]
    assert d is not None, "버퍼 값이 있는데 드리프트를 못 찾았다 — 배선이 끊겼다"
    assert d["count"] == 1
    assert d["by_buffer"] == {"폰": 1}
    assert d["sample"][0]["internal_sku"] == "OHI-0001"
    assert d["sample"][0]["cost_price"] == 2616.0
    assert d["sample"][0]["truth"] == 2350.7
    # ★여기서 `healthy is False`를 단언하지 않는다 — 빈 DB에선 missing_jobs 때문에 어차피
    #   False라 **아무것도 안 지키는 단언**이 된다(교훈 #181). healthy와 cost_drift의 연결은
    #   위 순수 테스트(test_build_health_turns_unhealthy_on_cost_drift)가 지킨다.

    # ★세 갈래를 **따로** 낸다 — 합치면 드리프트가 «판정 불가» 안에 묻힌다.
    assert d["ok"] == 1
    assert d["undetermined"] == 1

    # ★어느 원가표로 판정했는지 응답에 남는다. 없으면 «무엇과 비교한 결과인지» 알 수 없다.
    assert "MD_원가 계산_Jino_260807.xlsx" in d["source"]
    assert "7ed336b4c55ea71b" in d["source"]


def test_compute_health_is_silent_when_master_matches_truth(db):
    """★깨끗하면 **아무 말도 안 한다** — 상시 켜진 경고는 안 켜진 것과 같다.

    2026-08-10 이후 prod의 정상 상태가 이것이다(드리프트 0건).
    """
    _seed(db, [
        ("OHI-0002", "지문방지 필름 3매", _TRUTH_VALUE),
        ("OHI-0003", "강화유리", _UNDETERMINED_VALUE),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    # ★«판정 불가»가 있어도 경고가 아니다(케이스·거치대 계열은 원가표가 CNY로 따로 계산한다).
    assert h["cost_drift"] is None


def test_zero_cost_is_undetermined_not_drift(db):
    """★원가 «없음»은 이 스키마에서 NULL이 아니라 **0원**이다(cost_price nullable=False, default=0).

    그래서 원가 미입력 상품은 `undetermined`로 세어지고 **드리프트로는 안 세어진다** —
    맞는 동작이다. 0원은 «버퍼가 얹힌 값»이 아니라 «원가를 아직 안 넣은 것»이고,
    그건 다른 문제다(세션 시작 시 Jino가 지적한 «원가가 안 붙은 SKU»).
    ★여기서 0원을 드리프트에 합치면 배너가 상시 켜져 진짜 복귀를 가린다.
    """
    _seed(db, [("OHI-0004", "원가 미입력", Decimal("0")), ("OHI-0001", "버퍼", _BUFFERED_VALUE)])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["cost_drift"]["count"] == 1
    assert h["cost_drift"]["undetermined"] == 1
    assert h["cost_drift"]["sample"][0]["internal_sku"] == "OHI-0001"


def test_cost_drift_failure_does_not_kill_the_health_api(db, monkeypatch):
    """★대조가 깨져도 헬스 API 전체를 죽이면 안 된다 — 워치독 침묵이 더 나쁘다.

    (기존 data_stale·disk_low 쿼리와 같은 fail-soft 규약. 실패는 로그로만 남는다.)
    ⚠️그 대가로 «스냅샷이 없어서 못 봤다»와 «봤는데 0건»이 응답상 같아진다 —
      이건 알고 감수한 것이다(둘 다 None). 로그가 유일한 구분자다.
    """
    from app.services import cost_truth_audit

    def boom(*a, **kw):
        raise FileNotFoundError("스냅샷 없음")

    monkeypatch.setattr(cost_truth_audit, "load_truth", boom)
    _seed(db, [("OHI-0001", "버퍼", _BUFFERED_VALUE)])

    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["cost_drift"] is None
    assert "healthy" in h  # 응답 자체는 살아 있다
