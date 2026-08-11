# test_vendor_item_axis.py — 쿠팡 판매분석 «일자×옵션» 정본 매출 축 (D-CPP-36).
#
# ## 이 파일이 지키는 것
#
# ①**적재**가 멱등인가(백필 반복이 GMV를 부풀리지 않는가)
# ②**보존식**(Σ옵션 == 요약)이 «어긋남»과 «아직 안 옴»을 가르는가
# ③그 판정이 **HTTP body까지** 흐르는가 — 서비스층에 있고 응답엔 없던 사고를 되풀이하지 않는다
#   (2026-08-10 적대 리뷰 P1-1: `response_model`에 필드가 없어 FastAPI가 조용히 지웠고,
#    dict까지만 보는 배선 테스트 6건이 전부 살아 있었다. 교훈 #221)
# ④**신선도 규칙**이 판매분석을 실제로 본다 — 규칙이 RG 정산 한 곳에 하드코딩돼 있던 탓에
#   요약축이 13일 멈췄는데 `healthy: true`였다. 그 구멍이 다시 생기지 않게 한다.
#
# 라이브 근거(2026-08-10 정찰): WING2 08-01~08-09 옵션축 GMV 450,700 == 요약축 450,700(차 0),
#   단일 일자도 일치(08-05 188,800 · 08-07 86,500). 그래서 허용오차가 0이다.
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangVendorItemSalesDaily, CoupangVendorSummaryDaily
from app.services.coupang import vendor_item_sales_sync as vis
from app.services.scheduler_health import (
    DATA_FRESHNESS_RULES,
    build_health,
    compute_scheduler_health,
)

NOW = datetime(2026, 8, 10, 12, 0, 0)
ACC = "COUPANG_WING2"


@pytest.fixture
def db():
    # StaticPool + check_same_thread=False: TestClient가 라우트를 다른 스레드에서 돌리므로
    # 같은 연결을 공유해야 픽스처가 심은 행이 라우트에서 보인다(빈 인메모리 DB «no such table» 방지).
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    # ★★`autoflush=False`가 **필수**다(적대 리뷰 P1-1): prod의 `SessionLocal`이 그 설정이고
    #   (`database.py`), 기본값 True로 테스트하면 «같은 배치에서 add한 행이 뒤 행의 query에
    #   안 보인다»는 prod 고유의 의미론이 테스트에서 사라진다. 그 차이 때문에 배치 내 중복이
    #   prod에서만 UNIQUE 위반(HTTP 500 + 회차 전량 롤백)으로 터졌고 테스트는 전부 초록이었다.
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


class _FakeScheduler:
    running = True

    def get_jobs(self):
        return []


def _row(day: str, vid: str, gmv: int, units: int = 1, rt: str = "NORMAL", **kw) -> dict:
    return {
        "date": date.fromisoformat(day),
        "vendor_item_id": vid,
        "registration_type": rt,
        "gmv": gmv,
        "units_sold": units,
        **kw,
    }


def _seed_summary(db, day: str, gmv: int, rt: str = "NORMAL", account: str = ACC) -> None:
    db.add(CoupangVendorSummaryDaily(
        summary_date=date.fromisoformat(day), account_key=account,
        registration_type=rt, gmv=gmv, units_sold=0,
    ))
    db.commit()


# ═══ ① 적재 — 멱등성과 방어 ═══


def test_ingest_is_idempotent_so_backfill_does_not_inflate_gmv(db):
    """★같은 (계정, 일자, 옵션)을 다시 받으면 **교체**다.

    이게 깨지면 백필을 두 번 돌린 날의 GMV가 두 배가 되고, 보존식은 요약축과 안 맞으니
    울리기는 한다 — 하지만 원인이 «수집 실패»로 오진된다. 여기서 못 박는다.
    """
    rows = [_row("2026-08-05", "87286712272", 132300, 7)]
    assert vis.ingest_vendor_item_sales(db, ACC, rows)["ingested"] == 1
    vis.ingest_vendor_item_sales(db, ACC, rows)  # 같은 회차 재전송
    vis.ingest_vendor_item_sales(db, ACC, rows)

    got = db.query(CoupangVendorItemSalesDaily).all()
    assert len(got) == 1, "upsert가 아니라 append다 — 백필이 GMV를 부풀린다"
    assert got[0].gmv == 132300


def test_duplicate_keys_inside_one_batch_do_not_blow_up(db):
    """★★같은 배치에 같은 키가 두 번 와도 죽지 않는다 (적대 리뷰 P1-1 회귀).

    앱 세션은 `autoflush=False`라 먼저 add한 행이 뒤 행의 `query().first()`에 안 보인다 →
    종전엔 commit에서 UNIQUE 위반 → **HTTP 500 + 그 회차 옵션축 전량 롤백**.
    실제 도달 경로: 페이지네이션 경계 중복(대상이 `sortBy=GMV DESC`의 준실시간 회전 데이터)
    또는 서버가 pageNumber를 무시하는 경우. 마지막 값이 이긴다.
    """
    res = vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 10000, 1),
        _row("2026-08-05", "222", 20000, 1),
        _row("2026-08-05", "111", 18900, 2),   # 경계 중복
    ])
    assert res["ingested"] == 2, "유니크 키 수를 세야 한다"
    assert res["received"] == 3, "받은 행 수도 따로 보고해야 중복을 로그로 안다"
    rows = {r.vendor_item_id: r for r in db.query(CoupangVendorItemSalesDaily).all()}
    assert set(rows) == {"111", "222"}
    assert (rows["111"].gmv, rows["111"].units_sold) == (18900, 2), "마지막 값이 이겨야 한다"


def test_duplicate_across_days_is_not_a_duplicate(db):
    """★날짜가 다르면 다른 행이다 — dedupe가 날짜를 무시하면 하루치가 통째로 사라진다."""
    res = vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 10000, 1),
        _row("2026-08-06", "111", 20000, 2),
    ])
    assert res["ingested"] == 2
    assert db.query(CoupangVendorItemSalesDaily).count() == 2


def test_ingest_replaces_with_the_newer_confirmed_value(db):
    """★준실시간 값이 확정치로 회전한다 — 나중 값이 이긴다(요약축과 같은 snapshot 정책)."""
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 10000, 1)])
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 18900, 2)])
    got = db.query(CoupangVendorItemSalesDaily).one()
    assert (got.gmv, got.units_sold) == (18900, 2)


def test_ingest_keeps_accounts_separate(db):
    """★계정이 다르면 다른 행이다 — 섞이면 한 계정의 매출이 다른 계정 손익에 들어간다."""
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 18900)])
    vis.ingest_vendor_item_sales(db, "COUPANG_WING1", [_row("2026-08-05", "111", 12900)])
    assert db.query(CoupangVendorItemSalesDaily).count() == 2


def test_ingest_skips_rows_without_a_join_key(db):
    """★옵션ID가 없는 행은 버린다 — 조인축이 없으면 수수료·주문과 못 이어져 쓸 수 없다."""
    res = vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "", 18900),
        _row("2026-08-05", "   ", 18900),
        _row("2026-08-05", "111", 18900),
    ])
    assert res["ingested"] == 1
    assert db.query(CoupangVendorItemSalesDaily).one().vendor_item_id == "111"


def test_ingest_skips_unknown_registration_type(db):
    """★모르는 등록유형은 무시(요약축과 같은 스키마 방어). 쿠팡이 새 유형을 내도 적재가 안 깨진다."""
    res = vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 18900, rt="WHOLESALE"),
        _row("2026-08-05", "222", 15000, rt="RFM"),
    ])
    assert res["ingested"] == 1


def test_negative_gmv_is_accepted_because_refunds_can_exceed_sales(db):
    """★음수 GMV는 정상이다 — 쿠팡 GMV = 판매액 − 환불액.

    요약축에서 «비용은 0 이상» 가정을 복제해 45일 백필이 통째로 400에 막힌 전례가 있다.
    같은 실수를 새 축에서 반복하지 않는다.
    """
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", -12900, -1)])
    assert db.query(CoupangVendorItemSalesDaily).one().gmv == -12900


def test_raw_metrics_failure_does_not_lose_the_money(db):
    """★원본 직렬화가 깨져도 **매출은 들어간다** — 부가 정보가 정본을 죽이면 안 된다."""

    class Unserializable:
        pass

    vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 18900, raw_metrics={"bad": Unserializable()}),
    ])
    got = db.query(CoupangVendorItemSalesDaily).one()
    assert got.gmv == 18900
    assert got.raw_metrics is None


# ═══ ② 보존식 — «어긋남»과 «아직 안 옴»을 가른다 ═══


def test_conservation_passes_on_the_live_measured_numbers(db):
    """★2026-08-10 라이브 실측 재현: 08-05 옵션 합 188,800 == 요약 188,800 → 어긋남 0."""
    _seed_summary(db, "2026-08-05", 188800)
    vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "87286712272", 132300, 7),
        _row("2026-08-05", "95881375791", 37800, 2),
        _row("2026-08-05", "91052517446", 18700, 1),
    ])
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert res["mismatch"] == []
    assert res["compared"] == 1


def test_conservation_catches_a_one_won_gap(db):
    """★허용오차 0 — 1원도 잡는다.

    실측 3회가 전부 차 0이었으므로 여유를 둘 근거가 없다. 여유를 두면 «조금 틀린 것»을
    영구히 눈감아 준다.
    """
    _seed_summary(db, "2026-08-05", 188800)
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 188799, 10)])
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert len(res["mismatch"]) == 1
    m = res["mismatch"][0]
    assert (m["option_gmv"], m["summary_gmv"], m["diff"]) == (188799, 188800, -1)
    assert m["date"] == "2026-08-05"


def test_missing_option_data_is_summary_only_not_a_mismatch(db):
    """★★«아직 안 왔다»는 «틀렸다»가 아니다.

    요약축만 있는 날을 mismatch로 세면, 옵션 수집이 하루 늦은 정상 상태가 매일 빨강이 된다 —
    그게 알림 피로의 발생 방식이고, 진짜 불일치가 그 안에 묻힌다(교훈 #123).
    """
    _seed_summary(db, "2026-08-05", 188800)
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert res["mismatch"] == []
    assert res["compared"] == 0
    assert len(res["summary_only"]) == 1
    assert res["summary_only"][0]["summary_gmv"] == 188800


def test_option_data_without_summary_is_option_only(db):
    """★반대 방향도 가른다 — 요약축이 없는데 옵션축만 있으면 대조 «불가»지 «불일치»가 아니다."""
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 18900)])
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert res["mismatch"] == []
    assert len(res["option_only"]) == 1


def test_conservation_compares_per_registration_type(db):
    """★NORMAL(3P)과 RFM(RG)을 **따로** 맞춘다.

    합계만 맞추면 3P를 RG로 잘못 분류한 옵션이 상쇄돼 통과한다 — 그러면 3P 손익만 조용히 틀린다.
    """
    _seed_summary(db, "2026-08-05", 100000, rt="NORMAL")
    _seed_summary(db, "2026-08-05", 50000, rt="RFM")
    vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 150000, 1, rt="NORMAL"),   # 3P에 RG 몫이 들어갔다
        _row("2026-08-05", "222", 0, 0, rt="RFM"),
    ])
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    types = sorted(m["registration_type"] for m in res["mismatch"])
    assert types == ["NORMAL", "RFM"], "유형별로 안 맞추면 상쇄돼 통과한다"


def test_conservation_ignores_other_accounts(db):
    """★계정 격리 — 남의 계정 요약축이 내 보존식을 깨거나 메우면 안 된다."""
    _seed_summary(db, "2026-08-05", 188800, account="COUPANG_WING1")
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 18900)])
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert res["mismatch"] == []
    assert res["compared"] == 0


def test_conservation_window_excludes_outside_dates(db):
    """★창 밖 날짜는 안 본다 — 안 그러면 옛 구멍이 영구히 울린다."""
    _seed_summary(db, "2026-07-01", 999999)
    res = vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))
    assert res["summary_only"] == []


def test_conservation_sums_many_options_not_just_one(db):
    """★합이 **실제로 더해지는지** 본다 (변이 방어).

    옵션 1개만 시드하면 «Σ» 대신 «첫 행»을 써도 통과한다. 여러 건으로 그 변이를 죽인다.
    """
    _seed_summary(db, "2026-08-05", 60000)
    vis.ingest_vendor_item_sales(db, ACC, [
        _row("2026-08-05", "111", 10000, 1),
        _row("2026-08-05", "222", 20000, 1),
        _row("2026-08-05", "333", 30000, 1),
    ])
    assert vis.conservation_check(db, ACC, date(2026, 8, 1), date(2026, 8, 9))["mismatch"] == []


# ═══ ③ 배선 — HTTP body까지 흐르는가 ═══


def _client(db):
    """실제 라우터를 태운 TestClient. get_db를 이 테스트 세션으로 갈아끼운다."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_build_health_exposes_conservation_key_even_when_clean():
    """★대조 결과가 없어도 키는 있다 — 키 없음과 0건이 같아 보이면 «판정 안 함»이
    «이상 없음»으로 읽힌다."""
    h = build_health([], [], set(), True, NOW)
    assert "vendor_item_conservation" in h
    assert h["vendor_item_conservation"] is None


def test_build_health_turns_unhealthy_on_conservation_mismatch():
    """★어긋남이 healthy=False를 만든다 — 이게 배너를 띄우는 유일한 스위치다."""
    h = build_health([], [], set(), True, NOW, vendor_item_conservation={
        "mismatch": [{"date": "2026-08-05", "registration_type": "NORMAL", "diff": -1}],
    })
    assert h["healthy"] is False


def test_build_health_stays_healthy_when_only_summary_only():
    """★«아직 안 옴»만으로는 healthy를 깨지 않는다 — 그건 신선도 규칙의 몫이다.

    여기서 깨면 옵션 수집이 하루 늦은 정상 상태가 매일 unhealthy가 된다.
    """
    h = build_health([], [], set(), True, NOW, vendor_item_conservation={
        "mismatch": [],
        "summary_only": [{"date": "2026-08-09", "registration_type": "NORMAL"}],
    })
    assert h["healthy"] is True


def test_compute_health_finds_the_mismatch_by_itself(db):
    """★라이브 재현: DB에 어긋남이 있으면 헬스가 **스스로** 찾는다.

    이 테스트가 죽으면 = 배선이 끊긴 것 = 옵션↔요약 불일치가 다시 조용해진다.
    """
    _seed_summary(db, "2026-08-05", 188800)
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 100000, 5)])
    c = compute_scheduler_health(db, _FakeScheduler(), NOW)["vendor_item_conservation"]
    assert c is not None, "보존식 대조가 헬스에 연결되지 않았다"
    assert len(c["mismatch"]) == 1
    assert c["mismatch"][0]["diff"] == -88800
    # ★어느 계정인지가 응답에 남아야 한다 — 계정이 둘 이상이면 없이는 추적 불가.
    assert c["mismatch"][0]["account_key"] == ACC


def test_health_route_actually_returns_conservation(db):
    """★★스키마가 지우지 않는가 — dict가 아니라 **HTTP body**에 있는지 본다.

    2026-08-10 P1-1이 정확히 이 구멍이었다(`response_model`에 필드 누락 → 서비스층엔 있고
    응답엔 없음 → 배선 테스트 6건 전부 생존). `SchedulerHealthOut`에서
    `vendor_item_conservation`를 빼면 여기서 죽는다.
    """
    _seed_summary(db, "2026-08-05", 188800)
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-05", "111", 100000, 5)])
    try:
        r = _client(db).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "vendor_item_conservation" in body, \
            "response_model이 필드를 지웠다 — 화면까지 안 간다"
        assert body["vendor_item_conservation"]["mismatch"][0]["diff"] == -88800
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_ingest_route_round_trips_through_http(db):
    """★ingest도 HTTP 경계를 태운다 — 페처가 실제로 부르는 경로다."""
    import os

    token = os.environ.get("AD_INGEST_TOKEN") or "test-token"
    os.environ["AD_INGEST_TOKEN"] = token
    try:
        r = _client(db).post(
            "/api/coupang/ops/wing/vendor-item-sales/ingest",
            headers={"X-Ingest-Token": token},
            json={"account_key": ACC, "rows": [{
                "date": "2026-08-05", "vendor_item_id": "87286712272",
                "registration_type": "NORMAL", "gmv": 132300, "units_sold": 7,
                "total_orders": 7, "item_name": "오하이 풀커버 강화유리",
                "product_id": "6089846168",
                "raw_metrics": {"totalGmv": 132300.0, "totalPageViews": 13.0},
            }]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ingested"] == 1
        got = db.query(CoupangVendorItemSalesDaily).one()
        assert got.gmv == 132300
        assert got.raw_metrics is not None and "totalPageViews" in got.raw_metrics
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_ingest_route_rejects_unknown_account(db):
    """★오타 account_key가 «전체» 합계를 조용히 오염시키지 않게 한다(요약축과 같은 방어)."""
    import os

    token = os.environ.get("AD_INGEST_TOKEN") or "test-token"
    os.environ["AD_INGEST_TOKEN"] = token
    try:
        r = _client(db).post(
            "/api/coupang/ops/wing/vendor-item-sales/ingest",
            headers={"X-Ingest-Token": token},
            json={"account_key": "COUPANG_WING9", "rows": [{
                "date": "2026-08-05", "vendor_item_id": "1",
                "registration_type": "NORMAL", "gmv": 1, "units_sold": 1,
            }]},
        )
        assert r.status_code == 400
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_ingest_route_rejects_absurd_amounts(db):
    """★방어 상한이 실제로 막는지 (변이 M7 생존 → 추가).

    json은 `Infinity`를 허용하고 응답 형태가 바뀌면 거대값이 올 수 있다. 그게 그대로 적재되면
    보존식이 터지고 원인은 «수집 실패»로 오진된다. gmv·units·orders **셋 다** 막아야 한다
    (둘만 막으면 한 필드로 통과한다).
    """
    import os

    token = os.environ.get("AD_INGEST_TOKEN") or "test-token"
    os.environ["AD_INGEST_TOKEN"] = token
    base = {"date": "2026-08-05", "vendor_item_id": "1",
            "registration_type": "NORMAL", "gmv": 1, "units_sold": 1, "total_orders": 1}
    try:
        client = _client(db)
        for field, bad in (("gmv", 10 ** 12), ("units_sold", 10 ** 7), ("total_orders", 10 ** 7)):
            r = client.post(
                "/api/coupang/ops/wing/vendor-item-sales/ingest",
                headers={"X-Ingest-Token": token},
                json={"account_key": ACC, "rows": [{**base, field: bad}]},
            )
            assert r.status_code == 400, f"{field}={bad}이 통과했다"
        assert db.query(CoupangVendorItemSalesDaily).count() == 0
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_ingest_route_rejects_an_oversized_batch(db):
    """★배치 상한 (변이 M10 생존 → 추가). 실측 규모는 «하루 최대 146옵션 × 수십 일»이다."""
    import os

    from app.routers.coupang_ops import _VI_MAX_ROWS

    token = os.environ.get("AD_INGEST_TOKEN") or "test-token"
    os.environ["AD_INGEST_TOKEN"] = token
    row = {"date": "2026-08-05", "registration_type": "NORMAL",
           "gmv": 1, "units_sold": 1, "total_orders": 1}
    try:
        r = _client(db).post(
            "/api/coupang/ops/wing/vendor-item-sales/ingest",
            headers={"X-Ingest-Token": token},
            json={"account_key": ACC,
                  "rows": [{**row, "vendor_item_id": str(i)} for i in range(_VI_MAX_ROWS + 1)]},
        )
        assert r.status_code == 400
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_conservation_accounts_excludes_wing1(db):
    """★보존식 대상에서 WING1 제외는 **의도** (변이 M9 생존 → 추가).

    WING1은 옵션축을 수집하지 않으므로 넣으면 `summary_only`가 매일 쌓여 노이즈가 된다.
    (신선도 규칙에서 WING1을 뺀 것과 같은 이유 — 영구 빨강이 이 감시선을 죽인다.)
    """
    from app.services.scheduler_health import CONSERVATION_ACCOUNTS

    assert CONSERVATION_ACCOUNTS == ("COUPANG_WING2",)

    # 라이브 재현: WING1 요약축만 있어도 헬스가 그걸 근거로 아무 말도 하지 않는다.
    _seed_summary(db, "2026-08-09", 59400, account="COUPANG_WING1")
    c = compute_scheduler_health(db, _FakeScheduler(), NOW)["vendor_item_conservation"]
    assert c["summary_only"] == [], "WING1 요약축이 보존식 노이즈로 들어왔다"


def test_conservation_failure_does_not_kill_the_health_api(db, monkeypatch):
    """★대조가 깨져도 헬스 API 전체를 죽이면 안 된다 — 워치독 침묵이 더 나쁘다.

    (data_stale·disk_low·cost_drift와 같은 fail-soft 규약.)
    """
    def boom(*a, **kw):
        raise RuntimeError("보존식 쿼리 깨짐")

    monkeypatch.setattr(vis, "conservation_check", boom)
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["vendor_item_conservation"] is None
    assert "healthy" in h  # 응답 자체는 살아 있다


# ═══ ④ 신선도 규칙 — 판매분석을 실제로 보는가 ═══


def test_freshness_rules_actually_cover_the_sales_analysis_pipelines():
    """★★13일 침묵의 재발 방지.

    규칙이 RG 정산 한 곳에만 있던 탓에 판매분석이 멈춰도 `healthy: true`였다.
    «감시선이 셋 있다»는 «이 파이프라인을 본다»는 뜻이 아니다 — 목록에 없으면 조용하다.
    """
    names = {r["name"] for r in DATA_FRESHNESS_RULES}
    assert "wing_vendor_summary" in names, "요약축이 신선도 판정에 없다 — 13일 침묵이 재발한다"
    assert "wing_vendor_item_sales" in names, "옵션축이 신선도 판정에 없다"
    sources = {r.get("source") for r in DATA_FRESHNESS_RULES}
    assert {"rg_settlement", "vendor_summary", "vendor_item_sales"} <= sources


def test_freshness_rule_reads_the_right_table_for_each_source(db):
    """★source별로 **다른 테이블**을 읽는지 본다.

    분기가 없으면(하드코딩 시절처럼) 새 규칙이 RG 테이블을 읽어 항상 no_data로 뜬다 —
    즉 «감시하는 척»만 하게 된다. 두 축에 서로 **다른 날짜**를 심어 그 혼동을 잡는다.
    """
    from app.services.scheduler_health import _latest_for_rule

    _seed_summary(db, "2026-08-08", 1000)
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-06", "111", 1000)])

    summary_rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "wing_vendor_summary")
    item_rule = next(r for r in DATA_FRESHNESS_RULES if r["name"] == "wing_vendor_item_sales")
    assert _latest_for_rule(db, summary_rule) == date(2026, 8, 8)
    assert _latest_for_rule(db, item_rule) == date(2026, 8, 6)


def test_unknown_freshness_source_raises_instead_of_faking_no_data(db):
    """★모르는 source는 None이 아니라 예외다.

    None은 `no_data`(즉시 비정상)로 해석되므로, 오타 하나가 «한 번도 수집 안 됨»이라는
    거짓 경보로 나타난다. 호출부 try/except가 데이터 감시만 강등하고 로그를 남긴다.
    """
    from app.services.scheduler_health import _latest_for_rule

    with pytest.raises(ValueError):
        _latest_for_rule(db, {"source": "오타", "account_key": ACC})


def test_stale_sales_analysis_actually_shows_up_in_data_stale(db):
    """★★라이브 재현: 판매분석이 낡으면 `data_stale`에 뜬다.

    2026-08-10 prod 상태가 정확히 이랬다 — 요약축 최신이 07-26(15일 전)인데 헬스는 조용했다.
    """
    _seed_summary(db, "2026-07-26", 34300)           # 실제로 멈춰 있던 날짜
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-07-26", "111", 34300)])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "wing_vendor_summary" in names
    assert "wing_vendor_item_sales" in names
    # ★impact 문구가 그대로 배너에 나간다 — 비어 있으면 화면에 «무엇이 문제인지»가 안 뜬다.
    for d in h["data_stale"]:
        assert d["impact"], f"{d['name']}에 impact 문구가 없다"


def test_fresh_sales_analysis_is_silent(db):
    """★신선하면 아무 말도 안 한다 — 상시 켜진 경고는 안 켜진 것과 같다.

    수집 직후에도 최신 날짜는 «어제»다(닫힌 과거일만 받는다). 그 정상 상태가 조용해야 한다.
    """
    _seed_summary(db, "2026-08-09", 34300)           # NOW=08-10 → 나이 약 1.5일 < 3.0
    vis.ingest_vendor_item_sales(db, ACC, [_row("2026-08-09", "111", 34300)])
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    names = {d["name"] for d in h["data_stale"]}
    assert "wing_vendor_summary" not in names
    assert "wing_vendor_item_sales" not in names


def test_never_collected_option_axis_is_no_data_not_silence(db):
    """★한 번도 수집 안 된 것도 시끄럽게 잡는다 — «없음»이 «정상»으로 보이면 안 된다."""
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    verdict = next(d for d in h["data_stale"] if d["name"] == "wing_vendor_item_sales")
    assert verdict["state"] == "no_data"


def test_one_broken_rule_does_not_silence_the_other_rules(db, monkeypatch):
    """★★규칙 하나가 깨져도 나머지 감시는 산다 (적대 리뷰 P1-2 회귀).

    종전엔 try/except가 **for 루프 전체**를 감싸 `data_snapshots = []`로 리셋했다 →
    새 테이블이 없는 상태(마이그 전 코드 배포)에서 **RG 정산 감시까지 함께 침묵**했고,
    `data_stale: []`는 «이상 없음»과 구별되지 않았다.
    13일 침묵을 막으려는 변경이 같은 실패 모드를 새 트리거로 되살린 셈이었다.
    """
    import app.services.scheduler_health as sh

    real = sh._latest_for_rule

    def selective_boom(session, rule):
        if rule.get("source") == "vendor_item_sales":
            raise RuntimeError("no such table: coupang_vendor_item_sales_daily")
        return real(session, rule)

    monkeypatch.setattr(sh, "_latest_for_rule", selective_boom)
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    by_name = {d["name"]: d for d in h["data_stale"]}

    # 깨진 규칙은 «확인 못 했다»로 남는다 — 침묵도 거짓 정상도 아니다.
    assert by_name["wing_vendor_item_sales"]["state"] == "check_failed"
    assert "no such table" in by_name["wing_vendor_item_sales"]["reason"]
    # ★나머지 규칙은 그대로 판정된다(이게 P1-2의 핵심).
    assert "rg_settlement_account_rows" in by_name, "RG 정산 감시가 함께 사라졌다"
    assert by_name["wing_vendor_summary"]["state"] == "no_data"


def test_check_failed_also_breaks_healthy_and_carries_an_impact_label(db, monkeypatch):
    """★«확인 못 했다»도 healthy를 깬다 — 그래야 배너에 뜬다.

    `data_stale`에 실어 보내는 이유가 이것이다: healthy 판정과 배너 렌더가 이미 그 리스트를
    쓰므로 새 필드를 만들 필요가 없다(새 필드를 만들고 표시 분기를 빼먹는 것이 이 리포의
    반복 실패다 — 교훈 #223).
    """
    from app.services.scheduler_watchdog import evaluate_data_freshness

    verdicts = evaluate_data_freshness([{
        "name": "wing_vendor_item_sales", "account_key": ACC,
        "latest": None, "max_age_days": 3.0, "impact": "옵션별 3P 매출이 낡은 값으로 구른다",
        "error": "OperationalError: no such table",
    }], NOW)
    assert len(verdicts) == 1
    assert verdicts[0]["state"] == "check_failed"
    assert verdicts[0]["impact"], "impact가 비면 배너에 «무엇이 문제인지»가 안 뜬다"
    # healthy 스위치: data_stale이 비어 있지 않으면 unhealthy(기존 규약 재확인)
    assert build_health([], [], set(), True, NOW, data_snapshots=[{
        "name": "x", "account_key": ACC, "latest": None, "max_age_days": 3.0,
        "impact": "i", "error": "boom",
    }])["healthy"] is False


def test_daily_trigger_job_requests_only_its_own_account(db, monkeypatch):
    """★★새 잡이 **자기 계정만** 요청한다 (적대 리뷰 M6 — 큐 도난 함정).

    갱신 요청 큐는 계정 차원이다. WING1을 요청하면 오픽스 데몬이 그걸 집어 가고 WING2는
    영원히 안 받는다 — 즉 13일 정체가 그대로 재발한다. 리뷰에서 이 계정을 WING1로 바꾼
    변이가 테스트 0건이라 생존했다.
    """
    import app.services.scheduler_service as ss
    from app.services.coupang import vendor_summary_sync

    asked: list[str] = []
    monkeypatch.setattr(vendor_summary_sync, "request_refresh",
                        lambda _db, account_key: asked.append(account_key) or {"ok": True})
    monkeypatch.setattr(ss, "_get_own_db_session", lambda: db)

    ss.request_wing_vendor_summary_daily_job()
    assert asked == ["COUPANG_WING2"], f"요청 계정이 틀렸다: {asked}"


def test_daily_trigger_job_is_watched_so_its_death_is_not_silent():
    """★트리거 잡이 죽으면 신선도가 울리기까지 3일이 걸린다 — 잡 자체도 감시 목록에 있어야 한다."""
    from app.services.scheduler_health import WATCHDOG_JOBS

    assert "request_wing_vendor_summary_daily" in WATCHDOG_JOBS


def test_wing1_is_deliberately_excluded_from_sales_analysis_freshness():
    """★WING1 제외는 **의도**다 — 오픽스 3P는 RG로 이관돼 넣으면 영구 빨강이 된다.

    (알림 피로가 이 감시선 계열이 실제로 죽는 방식이다. WATCHDOG_COOKIES가 WING1/2를 뺀
     것과 같은 이유.) 나중에 켤 때 이 테스트가 «왜 없었는지»를 알려 준다.
    """
    wing1_sales = [
        r for r in DATA_FRESHNESS_RULES
        if r["account_key"] == "COUPANG_WING1"
        and r.get("source") in ("vendor_summary", "vendor_item_sales")
    ]
    assert wing1_sales == []
