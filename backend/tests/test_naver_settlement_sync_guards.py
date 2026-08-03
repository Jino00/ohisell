# test_naver_settlement_sync_guards.py — 정산 수집 3대 결함의 회귀 가드 (2026-08-03 라이브 사고).
#
# 사고 경위:
#   ① sync_naver_settlement_job이 `days=34`(35일 구간)를 요청했는데 네이버 daily API 상한은
#      32일이라 **매 호출 400**. 클라이언트가 이를 빈 결과로 삼켜 "0건 적재 완료"를 남겼고
#      스케줄러는 last_status='ok'로 기록 → daily 테이블이 07-27에서 멈춘 채 아무도 몰랐다.
#   ② 08-03 03:45 KST 백엔드 재시작으로 05:25/05:30 발화가 유실됐는데, 두 잡이
#      _CATCHUP_ORDER에 없어 다음날까지 영영 안 돌았다(SETTLED 129건 미수집).
#      misfire_grace_time은 in-memory jobstore라 프로세스 재시작을 못 잡는다.
#   ③ _CATCHUP_ORDER에 이름만 넣고 funcs 등록을 빠뜨리면 조용히 스킵된다.
from __future__ import annotations

from datetime import date

import pytest

from app.clients.naver import NaverClient
from app.config import NaverAccountConfig
from app.services import scheduler_service as sched


def _client() -> NaverClient:
    return NaverClient(NaverAccountConfig(client_id="x", client_secret="y"), access_token="t")


# ── ① 조회 구간 상한 ───────────────────────────────────────────────────────
def test_daily_span_limit_is_measured_value():
    # 라이브 실측: 32일 구간 OK / 33일 구간부터 400.
    assert NaverClient.SETTLE_DAILY_MAX_SPAN_DAYS == 32


def test_daily_over_span_raises_instead_of_silent_400():
    c = _client()
    with pytest.raises(ValueError, match="상한"):
        c.fetch_daily_settlement(date(2026, 6, 30), date(2026, 8, 3))  # 35일 구간(사고 재현)


def test_daily_at_span_limit_is_allowed():
    # 상한과 같은 32일 구간은 통과해야 한다 — 요청까지 가서 네트워크에서 죽는지로 확인.
    c = _client()
    c._request = lambda *a, **k: {"elements": []}
    assert c.fetch_daily_settlement(date(2026, 7, 3), date(2026, 8, 3)) == []


def test_scheduler_window_stays_within_api_limit(monkeypatch):
    """스케줄러가 **실제로 넘기는 창**이 API 상한 안이어야 한다.

    주석 문자열이 아니라 호출 인자를 관측한다 — 창을 되돌리면(35일) 여기서 잡힌다.
    """
    seen: dict[str, date] = {}

    def _capture(self, dfrom, dto):
        seen["from"], seen["to"] = dfrom, dto
        return []

    monkeypatch.setattr(sched, "_get_own_db_session", lambda: _FakeSession())
    monkeypatch.setattr(NaverClient, "fetch_daily_settlement", _capture)
    monkeypatch.setattr(
        "app.config.get_naver_config",
        lambda key: NaverAccountConfig(client_id="x", client_secret="y"),
    )
    monkeypatch.setattr("app.routers.naver_ops._upsert_settlement", lambda db, rows: 0)

    sched.sync_naver_settlement_job()

    span = (seen["to"] - seen["from"]).days + 1
    assert span <= NaverClient.SETTLE_DAILY_MAX_SPAN_DAYS, (
        f"창 {span}일 > API 상한 {NaverClient.SETTLE_DAILY_MAX_SPAN_DAYS}일 — "
        "매 호출 400이 된다(2026-08-03 사고)"
    )


class _FakeSession:
    def close(self):
        pass


# ── ① 조용한 유실 금지 ─────────────────────────────────────────────────────
def test_daily_request_failure_raises():
    c = _client()
    c._request = lambda *a, **k: None      # 400·네트워크 실패 재현
    with pytest.raises(RuntimeError, match="일별 정산 조회 실패"):
        c.fetch_daily_settlement(date(2026, 7, 20), date(2026, 8, 3))


def test_case_request_failure_raises():
    c = _client()
    c._request = lambda *a, **k: None
    with pytest.raises(RuntimeError, match="건별 정산 조회 실패"):
        c.fetch_case_settlement(date(2026, 8, 1), date(2026, 8, 3))


def test_daily_empty_result_is_not_an_error():
    # 진짜로 정산이 없는 기간은 빈 리스트가 정상 — 실패와 구분돼야 한다.
    c = _client()
    c._request = lambda *a, **k: {"elements": []}
    assert c.fetch_daily_settlement(date(2026, 8, 1), date(2026, 8, 3)) == []


# ── ②③ 캐치업 편입 ────────────────────────────────────────────────────────
def test_settlement_jobs_are_in_catchup_order():
    assert "sync_naver_settlement" in sched._CATCHUP_ORDER
    assert "sync_naver_case_settlement" in sched._CATCHUP_ORDER


def test_settlement_jobs_run_first_in_catchup():
    # cron이 가장 이르고(05:25/05:30) BEP·이익 회계가 정산을 입력으로 쓴다 → 하류보다 먼저.
    order = list(sched._CATCHUP_ORDER)
    assert order[0] == "sync_naver_settlement"
    assert order[1] == "sync_naver_case_settlement"


def test_every_catchup_job_has_a_registered_function():
    """★_CATCHUP_ORDER에 이름만 넣고 funcs 등록을 빠뜨리면 조용히 스킵된다 — 그 구멍을 막는다."""
    import inspect
    src = inspect.getsource(sched._catch_up_morning_batch)
    missing = [j for j in sched._CATCHUP_ORDER if f'"{j}"' not in src]
    assert not missing, f"catch-up 목록에 있으나 funcs 미등록: {missing}"


def test_settlement_jobs_are_registered_in_scheduler():
    # 크론 등록 자체가 빠지면 catch-up 이전에 정상 발화도 없다.
    import inspect
    src = inspect.getsource(sched)
    assert '("sync_naver_settlement", "25 5 * * *")' in src
    assert '("sync_naver_case_settlement", "30 5 * * *")' in src
