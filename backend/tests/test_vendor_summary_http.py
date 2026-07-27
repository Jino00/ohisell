# test_vendor_summary_http.py — Wing 세션 자동화 S2 라우터 HTTP 라운드트립 (TestClient, 격리 인메모리 DB)
# 토큰 인증·검증·ingest 저장·reconcile read-back·refresh 트리거를 HTTP 레이어로 검증.
# 스케줄러 lifespan은 TestClient를 context manager로 쓰지 않아 시작 안 됨(부작용 차단).
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.utils.kst import kst_today

_TOKEN = "rt-test-token-123"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _yesterday() -> str:
    return (kst_today() - timedelta(days=1)).isoformat()


def test_ingest_requires_token(client):
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest", json={
        "account_key": "COUPANG_WING1",
        "days": [{"date": y, "registration_type": "NORMAL", "gmv": 1693230, "units_sold": 10}],
    })
    assert r.status_code == 401


def test_ingest_bad_account_key_rejected(client):
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "TYPO",
                        "days": [{"date": y, "registration_type": "NORMAL", "gmv": 1, "units_sold": 1}],
                    })
    assert r.status_code == 400


def test_ingest_allows_negative_gmv(client):
    """★2026-07-27 라이브 실측 결함 재현: 환불이 판매를 초과하는 날은 gmv가 정당하게 음수다
    (WING1 07-02 -16,620·07-07 -12,900). 배치 전체가 400으로 거부되면 안 된다."""
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "COUPANG_WING1",
                        "days": [
                            {"date": y, "registration_type": "NORMAL", "gmv": -16620, "units_sold": 0},
                        ],
                    })
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 1

    rr = client.get(f"/api/overview/revenue-reconcile?from={y}&to={y}&account=COUPANG_WING1")
    assert rr.status_code == 200, rr.text
    assert rr.json()["official"]["gmv_3p"] == -16620


def test_ingest_then_reconcile_roundtrip(client):
    """토큰 ingest → reconcile read-back: official GMV가 그대로 조회되고 드리프트 계산됨(우리 매출 0)."""
    y = _yesterday()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "COUPANG_WING1",
                        "days": [
                            {"date": y, "registration_type": "NORMAL", "gmv": 1693230, "units_sold": 10},
                            {"date": y, "registration_type": "RFM", "gmv": 1786500, "units_sold": 5},
                        ],
                    })
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 2

    rr = client.get(f"/api/overview/revenue-reconcile?from={y}&to={y}&account=COUPANG_WING1")
    assert rr.status_code == 200, rr.text
    body = rr.json()
    assert body["has_official"] is True
    assert body["coverage"]["complete"] is True          # 단일 닫힌일·full 적재
    assert body["official"]["gmv_3p"] == 1693230
    assert body["official"]["gmv_rg"] == 1786500
    # Decimal은 문자열로 직렬화(_jsonify). 우리 매출 0 → 드리프트 -100%.
    assert body["ours"]["revenue_3p"] == "0"
    assert body["drift"]["pct_3p"] == "-100"


def test_ingest_accepts_negative_gmv_refund_day(client):
    """환불>판매 순액 음수일 허용(2026-07-27 라이브 실증: WING1 07-02 3P GMV=-16,620).

    ★페처는 45일 창을 한 번의 push로 보낸다 — 음수 하루를 거부하면 배치 전체가 all-or-nothing으로
    소멸해 PR#108 자가치유가 발동 자체를 못 한다. 음수는 정상 데이터로 저장돼야 한다.
    """
    y = _yesterday()
    d2 = (kst_today() - timedelta(days=2)).isoformat()
    r = client.post("/api/coupang/ops/wing/vendor-summary/ingest",
                    headers={"X-Ingest-Token": _TOKEN}, json={
                        "account_key": "COUPANG_WING1",
                        "days": [
                            {"date": d2, "registration_type": "NORMAL", "gmv": -16620, "units_sold": -1},
                            {"date": d2, "registration_type": "RFM", "gmv": 1326580, "units_sold": 30},
                            {"date": y, "registration_type": "NORMAL", "gmv": 12900, "units_sold": 1},
                        ],
                    })
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 3

    rr = client.get(f"/api/overview/revenue-reconcile?from={d2}&to={y}&account=COUPANG_WING1")
    assert rr.status_code == 200, rr.text
    body = rr.json()
    assert body["official"]["gmv_3p"] == -16620 + 12900  # 음수일이 합계에 그대로 반영
    assert body["official"]["gmv_rg"] == 1326580


def test_ingest_rejects_absurd_magnitude(client):
    """음수 허용으로 열린 문은 절대값 상한이 지킨다 — 파싱 깨짐 수준의 값은 여전히 400."""
    y = _yesterday()
    hdr = {"X-Ingest-Token": _TOKEN}
    for bad in ({"gmv": -10_000_000_001, "units_sold": 1},
                {"gmv": 1, "units_sold": 2_000_000}):
        r = client.post("/api/coupang/ops/wing/vendor-summary/ingest", headers=hdr, json={
            "account_key": "COUPANG_WING1",
            "days": [{"date": y, "registration_type": "NORMAL", **bad}],
        })
        assert r.status_code == 400, r.text
        assert "비정상" in r.json()["detail"]


def test_reconcile_today_only_no_closed_days(client):
    today = kst_today().isoformat()
    rr = client.get(f"/api/overview/revenue-reconcile?from={today}&to={today}&account=COUPANG_WING1")
    assert rr.status_code == 200
    assert rr.json()["has_closed_days"] is False


def test_refresh_trigger_claim_flow(client):
    """request-refresh(무토큰 UI) → status requested=True → claim(토큰) → 요청은 보존(lease).

    ★2026-07-27 계약 변경: claim이 요청을 소비하던 것을 임대로 바꿨다(실패 시 재시도 보장).
    요청이 사라지는 것은 성공(ingest) 또는 3회 실패/로그인필요일 때뿐이다.
    """
    base = "/api/coupang/ops/wing/vendor-summary"
    assert client.get(f"{base}/refresh-status").json()["requested"] is False
    assert client.post(f"{base}/request-refresh").status_code == 200
    assert client.get(f"{base}/refresh-status").json()["requested"] is True
    # claim은 토큰 필요
    assert client.post(f"{base}/refresh-claim").status_code == 401
    claimed = client.post(f"{base}/refresh-claim", headers={"X-Ingest-Token": _TOKEN})
    assert claimed.status_code == 200 and claimed.json()["claimed"] is True

    st = client.get(f"{base}/refresh-status").json()
    assert st["requested"] is True and st["in_flight"] is True   # ★임대 중(진행 중)
    # 같은 요청을 두 번 claim하지는 못한다(창 2회 방지 성질 유지)
    again = client.post(f"{base}/refresh-claim", headers={"X-Ingest-Token": _TOKEN})
    assert again.json()["claimed"] is False


# ── RG 정산 자동 다운로드 (S4-P2) ──
def test_rg_settlement_upload_requires_token(client):
    """회계(net_profit 소스) 변경 엔드포인트 → 토큰 없으면 401(파일 파싱 전 차단)."""
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        files={"file": ("A01564720-WAREHOUSING_SHIPPING-ko-x.xlsx", b"dummy",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 401


def test_rg_settlement_upload_roundtrip_survives_cross_thread_session(client):
    """업로드 성공 경로 HTTP 왕복 — 파싱·적재 결과가 응답에 실려 오는지.

    ★이 테스트가 필요한 이유(2026-07-17): run_in_threadpool 오프로드는 **DB 세션을 워커
    스레드에서 쓰게 만든다** → SQLite `check_same_thread` 설정이 틀리면 여기서만 터진다
    (파서 단위 테스트로는 절대 안 잡힘). prod 엔진도 connect_args={"check_same_thread": False}
    라 동일 조건(app/database.py).

    ⚠️이 테스트는 "오프로드가 됐는지"는 검증하지 **않는다**(오프로드를 지워도 통과) —
    그건 아래 test_rg_settlement_upload_runs_ingest_off_the_event_loop 담당. (codex P2 반영)
    """
    from tests.test_rg_settlement_sync import _build_xlsx, _WH_ROWS

    content = _build_xlsx([("입출고비", "입출고비", _WH_ROWS)])
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},   # 파일명 vendor_id 없음 → 명시 account_key 사용
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx", content,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["upserted"] == 2               # 고유 옵션 2개
    assert body["account_key"] == "COUPANG_WING1"


def test_rg_settlement_upload_runs_ingest_off_the_event_loop(client, monkeypatch):
    """★사고 재발 방지의 핵심 가드: 동기 ingest가 이벤트 루프 위에서 돌면 실패한다.

    2026-07-17 사고의 절반은 파싱 O(n²)였고, 나머지 절반이 이것이다 — 업로드 엔드포인트가
    `async def`인데 동기 ingest를 그냥 호출해서 **이벤트 루프를 점유**했다. prod는
    uvicorn --workers 1이라 그동안 모든 API가 멈춘다(같은 시각 fetch-error POST 15초 타임아웃).

    판정 방법(타이밍·스레드 이름 대신 결정론적 신호): 이벤트 루프 스레드에서 돌면
    asyncio.get_running_loop()이 루프를 반환하고, 스레드풀 워커에서 돌면 RuntimeError가 난다.
    → 워커에서 돌았다 == 루프가 안 잡힌다.

    (codex P2: 기존 왕복 테스트는 run_in_threadpool을 지워도 통과해서 이 퇴행을 못 잡았다.)
    """
    import asyncio

    from app.services.coupang import rg_settlement_sync as _rs
    from tests.test_rg_settlement_sync import _build_xlsx, _WH_ROWS

    seen: dict[str, bool] = {}
    _real = _rs.ingest_settlement_xlsx

    def _spy(db, account_key, content):
        try:
            asyncio.get_running_loop()
            seen["on_event_loop"] = True      # 루프 스레드 = 오프로드 안 됨 = 퇴행
        except RuntimeError:
            seen["on_event_loop"] = False     # 워커 스레드 = 정상
        return _real(db, account_key, content)

    monkeypatch.setattr(_rs, "ingest_settlement_xlsx", _spy)

    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx",
                        _build_xlsx([("입출고비", "입출고비", _WH_ROWS)]),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 200, r.text
    assert seen.get("on_event_loop") is False, (
        "동기 ingest가 이벤트 루프에서 실행됨 — run_in_threadpool 오프로드가 빠졌다."
        " 단일 워커(prod)에서 업로드 내내 모든 API가 멈춘다."
    )


def test_rg_settlement_upload_corrupt_file_returns_422(client):
    """손상 파일 → 500 아닌 422(WingReadError 매핑). 스레드풀 오프로드 후에도 예외가 전파되는지 확인."""
    r = client.post(
        "/api/coupang/ops/rg/settlement/upload-xlsx",
        params={"account_key": "COUPANG_WING1"},
        files={"file": ("WAREHOUSING_SHIPPING-ko-x.xlsx", b"not-a-real-xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers={"X-Ingest-Token": _TOKEN},
    )
    assert r.status_code == 422, r.text


def test_rg_settlement_refresh_trigger_claim_flow(client):
    """request-refresh(무토큰 UI) → status requested=True → claim(토큰) → 요청 보존(lease).

    (vendor-summary 미러 — 2026-07-27 계약 변경 동일 적용)
    """
    base = "/api/coupang/ops/wing/rg-settlement"
    assert client.get(f"{base}/refresh-status").json()["requested"] is False
    assert client.post(f"{base}/request-refresh").status_code == 200
    assert client.get(f"{base}/refresh-status").json()["requested"] is True
    assert client.post(f"{base}/refresh-claim").status_code == 401          # 토큰 필요
    claimed = client.post(f"{base}/refresh-claim", headers={"X-Ingest-Token": _TOKEN})
    assert claimed.status_code == 200 and claimed.json()["claimed"] is True

    st = client.get(f"{base}/refresh-status").json()
    assert st["requested"] is True and st["in_flight"] is True


# ── 버튼 큐 계정 분리 (2026-07-27 — WING2 데몬 편입) ──
# 큐가 계정 무구분이면 WING2 데몬이 WING1 버튼 요청을 claim해 간다. HTTP 레이어에서 고정.
_RG = "/api/coupang/ops/wing/rg-settlement"
_VS = "/api/coupang/ops/wing/vendor-summary"


@pytest.mark.parametrize("base", [_RG, _VS])
def test_refresh_queue_account_isolation(client, base):
    """WING1 요청을 WING2가 claim 못 하고, 그 반대도 못 한다(RG·판매분석 둘 다)."""
    hdr = {"X-Ingest-Token": _TOKEN}
    assert client.post(f"{base}/request-refresh?account_key=COUPANG_WING1").status_code == 200
    # 남의 계정으로는 못 가져감 — 요청은 그대로 살아있어야 한다.
    assert client.post(f"{base}/refresh-claim?account_key=COUPANG_WING2",
                       headers=hdr).json()["claimed"] is False
    assert client.get(f"{base}/refresh-status?account_key=COUPANG_WING2").json()["requested"] is False
    assert client.get(f"{base}/refresh-status?account_key=COUPANG_WING1").json()["requested"] is True
    # 주인만 소비 가능.
    assert client.post(f"{base}/refresh-claim?account_key=COUPANG_WING1",
                       headers=hdr).json()["claimed"] is True


@pytest.mark.parametrize("base", [_RG, _VS])
def test_refresh_queue_default_account_is_wing1(client, base):
    """account_key 생략 = WING1 — 파라미터 없는 구버전 페처 하위호환(배포 순서 무관)."""
    assert client.post(f"{base}/request-refresh").status_code == 200
    assert client.get(f"{base}/refresh-status?account_key=COUPANG_WING1").json()["requested"] is True
    claimed = client.post(f"{base}/refresh-claim", headers={"X-Ingest-Token": _TOKEN})
    assert claimed.json()["claimed"] is True


@pytest.mark.parametrize("base", [_RG, _VS])
@pytest.mark.parametrize("path,method", [
    ("request-refresh", "post"), ("refresh-status", "get"),
    ("refresh-claim", "post"), ("fetch-error", "post"),
])
def test_refresh_queue_bad_account_key_400(client, base, path, method):
    """RG_ACCOUNTS 밖 account_key → 400(오타로 유령 상태행이 생기지 않게)."""
    url = f"{base}/{path}?account_key=TYPO"
    kwargs = {"headers": {"X-Ingest-Token": _TOKEN}}
    if path == "fetch-error":
        kwargs["json"] = {"error": "boom"}
    r = client.get(url, **kwargs) if method == "get" else client.post(url, **kwargs)
    assert r.status_code == 400, r.text


def test_vs_ingest_heartbeat_is_per_account(client):
    """판매분석 ingest 성공 heartbeat도 계정별(codex R1 [P2]).

    성공만 계정 무구분이면 WING2 push가 WING1 행에 last_success_at을 찍고 WING1의 실패 흔적까지
    지운다 — 오픽스는 실제로 안 왔는데 배너·폴링은 '갱신됨'이라 말한다(신선도 위조).
    """
    hdr = {"X-Ingest-Token": _TOKEN}
    y = _yesterday()
    # WING1에 먼저 실패를 남긴다 — WING2 성공이 이걸 지우면 안 된다.
    client.post(f"{_VS}/fetch-error?account_key=COUPANG_WING1", headers=hdr, json={"error": "boom"})
    r = client.post(f"{_VS}/ingest", headers=hdr, json={
        "account_key": "COUPANG_WING2",
        "days": [{"date": y, "registration_type": "NORMAL", "gmv": 1000, "units_sold": 1}],
    })
    assert r.status_code == 200, r.text
    assert client.get(f"{_VS}/refresh-status?account_key=COUPANG_WING2").json()["last_success_at"] is not None
    st1 = client.get(f"{_VS}/refresh-status?account_key=COUPANG_WING1").json()
    assert st1["last_success_at"] is None      # 남의 성공이 내 신선도가 되지 않는다
    assert st1["last_error"] == "boom"         # 남의 성공이 내 실패 흔적을 지우지 않는다


@pytest.mark.parametrize("base", [_RG, _VS])
def test_fetch_error_is_per_account(client, base):
    """실패 보고도 계정별 — WING2 실패가 WING1 화면에 뜨면 원인 오진단."""
    r = client.post(f"{base}/fetch-error?account_key=COUPANG_WING2",
                    headers={"X-Ingest-Token": _TOKEN}, json={"error": "chrome crash"})
    assert r.status_code == 200
    assert client.get(f"{base}/refresh-status?account_key=COUPANG_WING2").json()["last_error"] == "chrome crash"
    assert client.get(f"{base}/refresh-status?account_key=COUPANG_WING1").json()["last_error"] is None


# ── 층1: RG status/api 계정 단위 인제스트 (Mac 상주 브라우저, 쿠키 이관) ──
_STATUS = "/api/coupang/ops/wing/rg-settlement/ingest-status"


def test_rg_ingest_status_requires_token(client):
    """회계(net_profit 소스) 변경 → 토큰 없으면 401(파싱 전 차단)."""
    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1", json={"settlementStatusReports": []})
    assert r.status_code == 401


def test_rg_ingest_status_bad_account_key_rejected(client):
    """RG_ACCOUNTS 아닌 account_key → 400."""
    r = client.post(f"{_STATUS}?account_key=TYPO",
                    headers={"X-Ingest-Token": _TOKEN}, json={"settlementStatusReports": []})
    assert r.status_code == 400


def test_rg_ingest_status_parse_error_returns_422(client):
    """스키마 드리프트 raw → WingReadError → 500 아닌 422."""
    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1",
                    headers={"X-Ingest-Token": _TOKEN}, json={"unexpected_key": []})
    assert r.status_code == 422, r.text


def test_rg_ingest_status_roundtrip(client):
    """토큰 + 유효 raw → 200, 계정 단위 7행 적재 결과가 응답에 실려 온다."""
    from tests.test_rg_settlement_sync import _FIXTURE_SINGLE

    r = client.post(f"{_STATUS}?account_key=COUPANG_WING1",
                    headers={"X-Ingest-Token": _TOKEN}, json=_FIXTURE_SINGLE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"synced": 7, "account_key": "COUPANG_WING1", "status": "ok"}
