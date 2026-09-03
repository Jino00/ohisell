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

import json
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


def test_by_buffer_alias_exists_until_the_new_frontend_is_deployed(db):
    """★prod 프론트가 아직 옛 코드면 `d.by_buffer`를 읽는다 — 키가 없으면 배너가 **터진다**.

    `Object.entries(undefined)`는 TypeError고, 그 배너는 레이아웃 전역에 있다. 지금은
    어긋남이 0건이라 그 분기가 안 돌지만 **「지금은 안 터진다」에 기대지 않는다**
    (2026-09-03: 백엔드는 배포됐는데 프론트는 다른 세션의 미푸시 커밋 탓에 CAS로 막혔다).

    ★이 테스트는 «지울 때 멈추게» 하는 장치다 — 새 프론트가 prod에 올라간 뒤
      별칭을 지우면 여기서 죽고, 지우는 사람이 위 문장을 읽게 된다.
    """
    _seed(db, [("OHI-0001", "정본과 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE)])
    d = compute_scheduler_health(db, _FakeScheduler(), NOW)["cost_drift"]
    assert d["by_buffer"] == d["by_cause"], (
        "by_buffer 별칭이 사라졌다 — prod 프론트가 새 코드인지 먼저 확인할 것. "
        "옛 프론트가 남아 있으면 어긋남이 생기는 순간 배너가 TypeError로 터진다."
    )


# ═══ 적대 리뷰 1R이 살아남은 변이로 지목한 자리들 (2026-09-03) ═══


def test_held_is_not_counted_as_having_truth(db):
    """★P1-2: 「보류」는 `truth_value=None`이다 — **정본이 있는 것으로 세면 안 된다.**

    이 PR이 없애려는 병이 바로 그것(«판정 불가»를 «정상»으로 세기)인데, 같은 dict 안에서
    되풀이하고 있었다. 리뷰어가 `two_grounds`(계산값+매입가 동시)로 재현했다.
    """
    from app.models import CostRecipe, CostRecipeLink, CostStandard

    _seed(db, [
        ("OHI-DRIFT", "정본과 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE),
        ("OHI-HELD", "근거가 둘", Decimal("1000"), Decimal("1000")),
    ])
    r = CostRecipe(product_name="근거가 둘", form_factor="bar", status="approved",
                   source="excel", recipe_kind="assembly")
    db.add(r); db.flush()
    db.add(CostRecipeLink(recipe_id=r.id, internal_sku="OHI-HELD", status="approved", source="excel"))
    db.add(CostStandard(recipe_id=r.id, std_cost_ex_vat=Decimal("909.09"),
                        std_cost_inc_vat=Decimal("1000"), breakdown=json.dumps([{"label": "x"}])))
    db.commit()

    d = compute_scheduler_health(db, _FakeScheduler(), NOW)["cost_drift"]
    assert d["held"] == 1, "보류 행이 안 만들어졌다 — 이 테스트의 전제가 깨졌다"
    assert d["with_truth"] == 1, (
        f"with_truth={d['with_truth']} — 보류(정본값 None)를 「정본 있음」에 셌다. "
        "sku_count - none_count - held_count 여야 한다."
    )


def test_gap_sum_is_absolute_so_opposite_signs_do_not_cancel(db):
    """★P2-2: 부호 있는 합이면 +5,000과 −5,000이 만나 **「격차 합 0원」**이 된다.

    배너가 새로 얻은 유일한 정량 정보가 「걸린 돈이 없다」로 읽힌다 — 리뷰어가 재현했다.
    """
    _seed(db, [
        ("OHI-UP", "정본이 더 크다", Decimal("1000"), Decimal("6000")),
        ("OHI-DOWN", "정본이 더 작다", Decimal("6000"), Decimal("1000")),
    ])
    d = compute_scheduler_health(db, _FakeScheduler(), NOW)["cost_drift"]
    assert d["count"] == 2
    assert Decimal(d["gap_sum"]) == Decimal("10000"), (
        f"gap_sum={d['gap_sum']} — 부호가 상쇄됐다. 배너가 「0원」이라 말한다."
    )
    # 부호 있는 합도 «따로» 남긴다 — 컷오버 화면과 같은 수를 대조할 수 있어야 한다.
    assert Decimal(d["gap_sum_signed"]) == Decimal("0")


def test_drift_count_equals_cutover_ready_count(db):
    """★P2-4: 「드리프트 = 컷오버 대상」이라 주장했으면 **그 일치를 지켜야** 한다.

    임계를 `>= MATCH_EPSILON`에서 `> 0`으로 바꾸면 반올림 잔차 행이 배너에만 세어져
    두 화면이 다른 수를 말한다. 리뷰어의 변이 M02가 그렇게 살아남았다.
    """
    from app.services.cost_menu import truth_source as ts

    _seed(db, [
        ("OHI-A", "경계 밖", Decimal("1000"), Decimal("1000.5")),   # gap 0.5 = 임계
        ("OHI-B", "경계 안", Decimal("1000"), Decimal("1000.4")),   # gap 0.4 < 임계
        ("OHI-C", "정본 없음", Decimal("1000")),
    ])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    census = ts.truth_board(db)["census"]
    assert h["cost_drift"]["count"] == census["cutover_ready_count"], (
        "배너와 컷오버 화면이 다른 수를 말한다 — 임계가 갈라졌다."
    )


def test_by_cause_is_sorted_by_count_desc(db):
    """★P2-5: 배너는 앞에서부터 읽는다 — 「많은 순」이 뒤집히면 우선순위가 조용히 바뀐다.

    옛 테스트에 있던 이 보증이 전환하면서 **대체 없이 사라졌다**(변이 M08 생존).
    """
    from app.models import CostRecipe, CostRecipeLink, CostStandard

    rows = [(f"OHI-P{i}", "매입가 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE) for i in range(3)]
    rows.append(("OHI-R1", "계산값 어긋남", Decimal("5000")))
    _seed(db, rows)
    r = CostRecipe(product_name="계산값 어긋남", form_factor="bar", status="approved",
                   source="excel", recipe_kind="assembly")
    db.add(r); db.flush()
    db.add(CostRecipeLink(recipe_id=r.id, internal_sku="OHI-R1", status="approved", source="excel"))
    # ★구성이 **1줄**이면 판별표가 `g3_1_*`(보류)로 보내 드리프트 행이 안 생긴다 —
    #   초판은 그래서 사유가 하나뿐이었고 **정렬이 뒤집혀도 통과하는 테스트**였다
    #   (자체 변이 주입으로 발견, 2026-09-03). 3줄로 둬야 `computed`가 되어 사유가 갈린다.
    db.add(CostStandard(recipe_id=r.id, std_cost_ex_vat=Decimal("7272.73"),
                        std_cost_inc_vat=Decimal("8000"),
                        breakdown=json.dumps([{"label": "a"}, {"label": "b"}, {"label": "c"}])))
    db.commit()

    d = compute_scheduler_health(db, _FakeScheduler(), NOW)["cost_drift"]
    assert len(d["by_cause"]) >= 2, (
        f"사유가 하나뿐이면 정렬을 검사할 수 없다 — 시드가 깨졌다: {d['by_cause']}"
    )
    counts = list(d["by_cause"].values())
    assert counts == sorted(counts, reverse=True), f"많은 순이 아니다: {d['by_cause']}"
    assert d["by_cause"]["purchased_approved"] == 3
    # ★사유 라벨이 함께 실린다 — 배너가 영문 스네이크를 한국어 문장에 박지 않게(P2-7)
    assert set(d["cause_labels"]) == set(d["by_cause"])
    # ★★**라벨이 코드와 달라야 의미가 있다**(적대 리뷰 2R 관측): 초판은 `CAUSE_REF118`을
    #   썼는데 그 표엔 지배적 사유 `purchased_approved`가 **없어서** 코드 그대로 폴백했다.
    #   즉 「라벨을 붙였다」고 하면서 화면엔 영문 스네이크가 그대로 떴다.
    assert d["cause_labels"]["purchased_approved"] == "매입가 정본"


def test_board_guard_says_the_judge_is_off_not_just_silent(db, monkeypatch):
    """★★P1-1: 판정기가 **꺼졌다**를 「어긋남 0건」과 구분해 말한다.

    판정 근거를 엑셀 스냅샷에서 SKU별 정본 판별표로 옮기면서, 「검사기가 꺼졌다」를
    알리던 `cost_guard`와의 인과가 끊겼다 — `truth_board`가 터져도 `cost_guard.active`는
    True로 남고 `cost_drift`만 None이 되어 **깨끗한 상태와 응답이 똑같아졌다**.
    리뷰어가 그 구분 불가를 재현했다(1R P1-1).
    """
    from app.services.cost_menu import truth_source

    clean = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert clean["cost_drift"] is None
    assert clean["cost_board_guard"]["active"] is True

    def boom(*a, **kw):
        raise RuntimeError("정본 판별표 조회 실패")

    monkeypatch.setattr(truth_source, "truth_board", boom)
    broken = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert broken["cost_drift"] is None
    assert broken["cost_board_guard"]["active"] is False, (
        "판별표가 터졌는데 응답이 깨끗한 상태와 똑같다 — 감시가 조용히 꺼진다."
    )
    assert broken["cost_board_guard"]["reason"]
    # ★healthy에도 물려야 배너가 뜬다 — 판정에만 있고 표시가 없으면 통째로 숨는다.
    h = build_health([], [], set(), True, NOW, cost_board_guard={"active": False, "reason": "x"})
    assert h["healthy"] is False


def test_health_route_returns_cost_board_guard(db):
    """★스키마가 지우지 않는가 — 이 리포가 `cost_drift`로 이미 한 번 당한 자리다."""
    _seed(db, [("OHI-0001", "정본과 어긋남", _DRIFTED_VALUE, _TRUTH_VALUE)])
    try:
        body = _client(db).get("/api/scheduler/health").json()
        assert "cost_board_guard" in body, "response_model이 지웠다 — 화면까지 안 간다"
        assert body["cost_board_guard"]["active"] is True
        assert body["cost_drift"]["cause_labels"] is not None
    finally:
        from app.main import app
        app.dependency_overrides.clear()


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
