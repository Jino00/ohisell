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
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CostPurchasedPrice, ProductMaster
from app.services.scheduler_health import build_health, compute_scheduler_health

NOW = datetime(2026, 8, 10, 12, 0, 0)

# ★★2026-09-03 전환: 판정 근거가 «엑셀 스냅샷 한 벌»에서 **SKU별 정본 판별표**로 바뀌었다.
#   종전 값들(정본 2350.7 + 폰 버퍼 265.3 = 2616)은 08-07판 엑셀에 묶인 상수였고, 새 원가표가
#   나오자 **최신 값을 「옛 값 복귀」로 신고**했다(2026-09-03 오탐 7건). 이제 시드는
#   «그 SKU의 정본이 무엇인가»를 직접 만든다 — 승인된 매입가가 가장 짧은 길이다.
_TRUTH_VALUE = Decimal("2350.7")     # 승인된 매입가 = 이 SKU의 정본
_DRIFTED_VALUE = Decimal("2616")     # 마스터에 있는 값 — 정본과 다르다(격차 −265.3)
_NO_TRUTH_VALUE = Decimal("3500")    # 정본이 아예 없는 SKU(어긋날 수가 없다)


@pytest.fixture
def db():
    # ★StaticPool + check_same_thread=False가 **필수**다: TestClient는 라우트를 다른 스레드에서
    #   돌리는데, 기본 풀이면 그 스레드가 **새 연결 = 빈 인메모리 DB**를 열어 «no such table»이
    #   난다(실제로 당했다). 같은 연결을 공유해야 픽스처가 심은 행이 라우트에서 보인다.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    """rows = (sku, name, cost_price) 또는 (sku, name, cost_price, truth).

    ★`truth`를 주면 **승인된 매입가**를 함께 심는다 = 그 SKU에 정본이 생긴다.
      안 주면 정본이 없는 SKU다 — 어긋날 수가 없으므로 드리프트로 세어지지 않는다.
    """
    for row in rows:
        sku, name, cost = row[0], row[1], row[2]
        db.add(ProductMaster(internal_sku=sku, product_name=name, cost_price=cost))
        if len(row) > 3 and row[3] is not None:
            db.add(
                CostPurchasedPrice(
                    internal_sku=sku,
                    unit_price_inc_vat=row[3],
                    source="file",
                    source_file="(테스트)",
                    approved_at=NOW,
                )
            )
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
    h = build_health([], [], set(), True, NOW, cost_drift={"count": 177, "by_cause": {"purchased_approved": 177}})
    assert h["healthy"] is False
    assert h["cost_drift"]["count"] == 177


# ═══ 배선층 — 실제 DB → 헬스 dict ═══


def test_compute_health_detects_master_value_that_differs_from_its_truth(db):
    """★라이브 재현: 마스터 값이 **그 SKU의 정본**과 다르면 헬스가 스스로 찾아낸다.

    이 테스트가 죽으면 = 배선이 끊긴 것 = 원가가 정본과 어긋나도 화면이 조용해진다.
    """
    _seed(db, [
        ("OHI-0001", "정본과 어긋난 상품", _DRIFTED_VALUE, _TRUTH_VALUE),
        ("OHI-0002", "정본과 같은 상품", _TRUTH_VALUE, _TRUTH_VALUE),
        ("OHI-0003", "정본이 없는 상품", _NO_TRUTH_VALUE),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)

    d = h["cost_drift"]
    assert d is not None, "정본과 어긋난 값이 있는데 못 찾았다 — 배선이 끊겼다"
    assert d["count"] == 1
    assert d["by_cause"] == {"purchased_approved": 1}
    assert d["sample"][0]["internal_sku"] == "OHI-0001"
    assert Decimal(d["sample"][0]["cost_price"]) == _DRIFTED_VALUE
    assert Decimal(d["sample"][0]["truth"]) == _TRUTH_VALUE
    # ★여기서 `healthy is False`를 단언하지 않는다 — 빈 DB에선 missing_jobs 때문에 어차피
    #   False라 **아무것도 안 지키는 단언**이 된다(교훈 #181). healthy와 cost_drift의 연결은
    #   위 순수 테스트(test_build_health_turns_unhealthy_on_cost_drift)가 지킨다.

    # ★«정본 없음»을 «이상 없음»에 합치지 않는다 — 어긋날 수가 없어 자동으로 조용해지므로
    #   따로 센다. 합치면 「정본이 아예 없는 상태」가 「깨끗한 상태」로 읽힌다.
    assert d["with_truth"] == 2
    assert d["no_truth"] == 1

    # ★무엇에 대고 판정했는지 응답에 남는다. 없으면 «무엇과 비교한 결과인지» 알 수 없다.
    assert "정본 판별표" in d["source"]


def test_compute_health_is_silent_when_master_matches_truth(db):
    """★깨끗하면 **아무 말도 안 한다** — 상시 켜진 경고는 안 켜진 것과 같다.

    2026-08-10 이후 prod의 정상 상태가 이것이다(드리프트 0건).
    """
    _seed(db, [
        ("OHI-0002", "정본과 같은 상품", _TRUTH_VALUE, _TRUTH_VALUE),
        ("OHI-0003", "정본이 없는 상품", _NO_TRUTH_VALUE),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    # ★«정본 없음»이 있어도 경고가 아니다 — 그건 어긋남이 아니라 «아직 정본을 못 세운 것»이고,
    #   화면(`/cost` 「정본 판별」)이 그 수를 따로 보여준다.
    assert h["cost_drift"] is None


def test_drift_count_is_actually_counted_not_hardcoded(db):
    """★건수가 **세어진** 값인지 본다 (적대 리뷰 P2 / 변이 M19 생존).

    종전엔 드리프트 행을 **1건만** 시드하고 `count == 1`을 단언했다 — 그러면 `count`를
    상수 1로 바꿔도 통과한다. 계열이 섞인 여러 건을 넣어 «세는 일»이 실제로 일어나는지 본다.
    """
    _seed(db, [
        ("OHI-0001", "어긋남 A", _DRIFTED_VALUE, _TRUTH_VALUE),
        ("OHI-0002", "어긋남 B", _DRIFTED_VALUE, _TRUTH_VALUE),
        ("OHI-0003", "어긋남 C", Decimal("6186"), Decimal("6089.6")),
        ("OHI-0004", "정본과 같음", _TRUTH_VALUE, _TRUTH_VALUE),
    ])
    d = compute_scheduler_health(db, _FakeScheduler(), NOW)["cost_drift"]
    assert d["count"] == 3
    # ★사유별로도 나뉘어야 한다 — 뭉뚱그리면 «왜 어긋났나»를 못 본다.
    assert d["by_cause"] == {"purchased_approved": 3}
    assert d["with_truth"] == 4
    assert d["no_truth"] == 0


def test_cost_price_is_not_nullable_so_the_null_filter_is_a_dormant_guard():
    """★`.filter(cost_price.isnot(None))`는 지금 **도달 불가**다 — 그 사실을 못 박는다.

    적대 리뷰가 변이 M04(필터 제거)가 살아남았다고 지적했다. 원인을 실측했더니
    «테스트가 부족»한 게 아니라 **그 상태를 만들 수 없다**였다:
      · 모델: `cost_price` `nullable=False`
      · prod 실제 DDL(2026-08-10 확인): `cost_price NUMERIC(12, 2) NOT NULL`
      · prod NULL 행: **0건**
    NULL을 못 넣으니 «NULL이면 어떻게 되나»를 검증할 방법이 없다.

    그래서 필터를 **지우지 않고 남긴다** — 미래에 nullable로 바꾸면 `float(None)`이 터지고,
    fail-soft가 그 예외를 삼켜 **감시가 통째로 조용히 꺼진다**(그게 이 프로젝트가 반복해
    당한 형태다). 대신 이 테스트가 **전제가 바뀌는 순간** 울린다 — nullable로 바꾸는 사람이
    여기서 멈춰 위 문장을 읽게 된다. 도달 불가 코드를 «테스트했다»고 말하지 않기 위한 장치다.
    """
    assert ProductMaster.__table__.c.cost_price.nullable is False, (
        "cost_price가 nullable이 됐다 — 이제 NULL 행이 가능하므로 "
        "scheduler_health의 isnot(None) 필터가 실제 방어선이 된다. "
        "그 경로를 검증하는 테스트를 여기에 추가할 것."
    )


def test_sku_without_truth_is_counted_separately_not_as_drift(db):
    """★정본이 «없는» SKU는 드리프트가 아니다 — 어긋날 대상 자체가 없다.

    원가 미입력(이 스키마에선 NULL이 아니라 **0원**, `nullable=False` + default 0)도 마찬가지다.
    ★그런데 «없음»을 «이상 없음»에 합치면 안 된다 — 그래서 `no_truth`로 **따로 센다**.
      합치면 「정본을 아직 못 세운 40건」이 「깨끗한 상태」로 읽힌다(교훈 #123의 이 파일판).
    """
    _seed(db, [
        ("OHI-0004", "원가 미입력", Decimal("0")),
        ("OHI-0001", "정본과 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["cost_drift"]["count"] == 1
    assert h["cost_drift"]["no_truth"] == 1
    assert h["cost_drift"]["sample"][0]["internal_sku"] == "OHI-0001"


# ═══ HTTP 경계 — ★적대 리뷰 P1-1이 산 자리 ═══
#
# ★★2026-08-10 1R 실사고: 위 테스트들이 **전부 통과하는데 화면엔 아무것도 안 떴다.**
#   라우터가 `response_model=SchedulerHealthOut`인데 그 스키마에 `cost_drift`가 없어서
#   **FastAPI가 응답에서 조용히 지웠다.** 서비스층 dict엔 있고 HTTP body엔 없다.
#   즉 «검사기는 있는데 아무도 안 부른다»를 고치면서 «판정은 하는데 아무도 못 본다»를
#   새로 만들었다. 위 테스트가 하나도 안 죽었다 — dict까지만 보고 경계를 안 넘었기 때문이다.
#   교훈 #208(«도구의 값어치는 경계층에 있는데 순수 함수만 촘촘히 물린다»)을 이 파일이
#   스스로 재현했다. 그래서 여기부터는 **응답 body**를 본다.


def _client(db):
    """실제 라우터를 태운 TestClient. get_db를 이 테스트 세션으로 갈아끼운다."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_health_route_actually_returns_cost_drift(db):
    """★스키마가 지우지 않는가 — dict가 아니라 **HTTP body**에 있는지 본다.

    이 단언이 P1-1을 잡는다. `SchedulerHealthOut`에서 `cost_drift`를 빼면 여기서 죽는다.
    """
    _seed(db, [("OHI-0001", "정본과 어긋난 상품", _DRIFTED_VALUE, _TRUTH_VALUE)])
    try:
        r = _client(db).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "cost_drift" in body, "response_model이 cost_drift를 지웠다 — 화면까지 안 간다"
        assert body["cost_drift"] is not None
        assert body["cost_drift"]["count"] == 1
        assert body["cost_drift"]["by_cause"] == {"purchased_approved": 1}
        # ★건수만 넘기면 안 된다 — 배너가 «왜 어긋났나»와 «얼마나»를 쓴다.
        assert body["cost_drift"]["sample"][0]["internal_sku"] == "OHI-0001"
        assert body["cost_drift"]["gap_sum"] is not None
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_health_route_keeps_the_key_when_clean(db):
    """★깨끗해도 **키는 남는다** — 키 없음과 null을 프론트가 구분 못 하면 «판정 안 함»이
    «이상 없음»으로 읽힌다(교훈 #123). 응답 계약으로 못 박는다."""
    _seed(db, [("OHI-0002", "지문방지 필름 3매", _TRUTH_VALUE)])
    try:
        body = _client(db).get("/api/scheduler/health").json()
        assert "cost_drift" in body
        assert body["cost_drift"] is None
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_cost_drift_failure_does_not_kill_the_health_api(db, monkeypatch):
    """★대조가 깨져도 헬스 API 전체를 죽이면 안 된다 — 워치독 침묵이 더 나쁘다.

    (기존 data_stale·disk_low 쿼리와 같은 fail-soft 규약. 실패는 로그로만 남는다.)
    ⚠️그 대가로 «판별표가 터져서 못 봤다»와 «봤는데 0건»이 응답상 같아진다 —
      이건 알고 감수한 것이다(둘 다 None). 로그가 유일한 구분자다.
    """
    from app.services.cost_menu import truth_source

    def boom(*a, **kw):
        raise RuntimeError("정본 판별표 조회 실패")

    monkeypatch.setattr(truth_source, "truth_board", boom)
    _seed(db, [("OHI-0001", "정본과 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE)])

    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["cost_drift"] is None
    assert "healthy" in h  # 응답 자체는 살아 있다
