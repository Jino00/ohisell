# test_scheduler_bm_layer_chain.py — BM 레이어 크론(07:37) 폐지·entity_sync 체이닝 전환 검증
# 배경(라이브 실측 2026-07-23~27 5/5일): entity_sync는 45캠페인+1,004그룹+~91k 키워드를 순차
#   fetch한 뒤 맨 끝에서 한 번 commit해서 실제 commit이 07:37을 넘긴다 → 구 07:37 크론의
#   naver_entity_snapshot(D)이 항상 entity_sync(D-1) 값을 담았다(구조적 경합).
# 커버: ①sync 성공 후 BM 체이닝(순서) + BM의 **별도 세션이 sync의 commit을 본다**(핵심 보장,
#   파일 DB·실제 close로 검증 — codex R1 [P2]) ②sync 실패해도 BM 실행 + sync 예외 재전파
#   ③BM 경계가 sync 판정을 못 바꾼다(세션 생성/close 실패 포함 — codex R1 [P1]) ④retired 행
#   삭제 ⑤catch-up 등록(맨 뒤) ⑥job_func_for에서 제거(크론 재등록 방지).
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import NaverEntity, SchedulerState
from app.services import scheduler_service
from app.services.naver_ad import bm_harness, entity_sync


@pytest.fixture
def session_factory(tmp_path):
    """파일 기반 SQLite — 세션마다 **진짜 별도 커넥션**이라 commit 가시성이 실제로 검증된다.

    in-memory + StaticPool은 커넥션을 공유해 '커밋 전에도 보인다' → 체이닝의 핵심 보장
    (BM의 새 세션이 sync의 commit을 본다)이 깨져도 통과한다(codex R1 [P2]).
    """
    engine = create_engine(f"sqlite:///{tmp_path}/chain.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def calls(monkeypatch, session_factory):
    """호출 순서 기록 + 잡마다 새 세션 발급(prod와 동일: _get_own_db_session=SessionLocal()).

    close()는 모킹하지 않는다 — 실제로 닫힌 세션을 BM이 재사용하면 터지므로, 두 잡이 세션을
    각자 관리한다는 사실까지 함께 고정된다.
    """
    seq: list[str] = []

    def _new_session():
        seq.append("session")
        return session_factory()

    monkeypatch.setattr(scheduler_service, "_get_own_db_session", _new_session)
    monkeypatch.setattr(entity_sync, "sync_entities", lambda _db: seq.append("sync") or {"ok": 1})
    monkeypatch.setattr(bm_harness, "run_bm_layer", lambda _db: seq.append("bm") or {"ok": 1})
    return seq


# ══════════════════════════ ① 체이닝(성공 경로) ══════════════════════════


def test_entity_sync_chains_bm_layer_after_sync(calls):
    scheduler_service.sync_naver_entity_job()

    assert "sync" in calls and "bm" in calls
    assert calls.index("sync") < calls.index("bm")  # 단일 commit이 끝난 뒤라야 당일 값 스냅샷


def test_bm_layer_gets_its_own_session(calls):
    """BM은 sync 세션을 물려받지 않고 새로 연다(닫힌 세션 재사용 금지 + 커밋 경계 분리)."""
    scheduler_service.sync_naver_entity_job()

    assert calls.count("session") == 2
    assert calls == ["session", "sync", "session", "bm"]


def test_bm_session_sees_rows_committed_by_entity_sync(monkeypatch, session_factory):
    """★핵심 보장: BM의 별도 세션이 sync가 commit한 당일 값을 본다(구 07:37 크론이 놓친 것).

    sync 스텁은 실제로 행을 넣고 commit하고, BM 스텁은 **자기 세션으로** 개수를 센다.
    파일 DB라 커넥션이 분리돼 있어 commit이 실제로 일어나야만 보인다.
    """
    seen: dict[str, int] = {}
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", session_factory)

    def _sync(db):
        db.add(NaverEntity(
            entity_type="campaign", entity_id="cmp-1", parent_id="",
            campaign_id="cmp-1", campaign_type="WEB_SITE", name="파워링크", status="on",
        ))
        db.commit()  # entity_sync.py의 맨 끝 단일 commit에 해당
        return {"campaigns": 1}

    def _bm(db):
        seen["rows"] = db.query(NaverEntity).count()
        return {"snapshot": seen["rows"]}

    monkeypatch.setattr(entity_sync, "sync_entities", _sync)
    monkeypatch.setattr(bm_harness, "run_bm_layer", _bm)

    scheduler_service.sync_naver_entity_job()

    assert seen["rows"] == 1  # D-1이 아니라 당일 값


# ══════════════════════════ ② 실패 경로 ══════════════════════════


def test_bm_chain_runs_even_when_sync_fails_and_error_propagates(calls, monkeypatch):
    """sync 실패해도 BM은 실행(구 07:37 크론이 sync 성공 여부와 무관히 발화하던 것과 동일 —
    스냅샷 연속성 유지). 단 sync 예외는 그대로 재전파돼 EVENT_JOB_ERROR로 표면화된다."""
    def _boom(_db):
        calls.append("sync")
        raise RuntimeError("네이버 API 500")

    monkeypatch.setattr(entity_sync, "sync_entities", _boom)

    with pytest.raises(RuntimeError, match="네이버 API 500"):
        scheduler_service.sync_naver_entity_job()

    assert "bm" in calls
    assert calls.index("sync") < calls.index("bm")


def test_bm_chain_failure_is_fail_open(calls, monkeypatch):
    """BM 레이어가 던져도 sync 잡은 성공으로 끝난다(관찰 잡이 집행 레인을 못 막게)."""
    def _boom(_db):
        calls.append("bm")
        raise RuntimeError("BM 폭발")

    monkeypatch.setattr(bm_harness, "run_bm_layer", _boom)

    scheduler_service.sync_naver_entity_job()  # 예외 전파 없음

    assert calls.count("bm") == 1


# ══════════════ ③ 체이닝 경계는 sync 판정을 못 바꾼다(codex R1 [P1]) ══════════════


def test_bm_session_creation_failure_does_not_fail_a_successful_sync(calls, monkeypatch):
    """BM 잡은 세션 생성이 try 밖이라 여기서 던지면 finally를 타고 나가 성공한 sync를 실패로
    뒤집는다(EVENT_JOB_ERROR 오탐 → last_run_at 미전진). 경계가 이를 삼켜야 한다."""
    original = scheduler_service._get_own_db_session
    state = {"n": 0}

    def _flaky():
        state["n"] += 1
        if state["n"] == 2:  # BM 잡이 여는 두 번째 세션
            raise RuntimeError("DB 커넥션 고갈")
        return original()

    monkeypatch.setattr(scheduler_service, "_get_own_db_session", _flaky)

    scheduler_service.sync_naver_entity_job()  # 예외 없이 성공 종료

    assert "sync" in calls and "bm" not in calls


def test_bm_failure_does_not_mask_the_original_sync_exception(calls, monkeypatch):
    """sync·BM이 동시에 실패하면 상류로 나가는 것은 **sync 예외**여야 한다(원인 오보 방지).

    ★BM 실패는 run_bm_layer가 아니라 **세션 생성**에서 낸다(codex R2 [P2]): run_bm_layer의
    예외는 run_naver_bm_layer_job 내부가 이미 삼켜서 helper 없이도 통과해버린다. 세션 생성은
    그 try 밖이라 여기서 던져야 새 non-throwing 경계를 실제로 검증한다.
    """
    original = scheduler_service._get_own_db_session
    state = {"n": 0}

    def _flaky():
        state["n"] += 1
        if state["n"] == 2:  # BM 잡이 여는 두 번째 세션 — run_naver_bm_layer_job의 try 밖
            raise RuntimeError("곁가지: DB 커넥션 고갈")
        return original()

    def _sync_boom(_db):
        calls.append("sync")
        raise RuntimeError("진짜 원인: entity_sync 실패")

    monkeypatch.setattr(scheduler_service, "_get_own_db_session", _flaky)
    monkeypatch.setattr(entity_sync, "sync_entities", _sync_boom)

    with pytest.raises(RuntimeError, match="진짜 원인"):
        scheduler_service.sync_naver_entity_job()

    assert state["n"] == 2  # BM 체이닝은 시도됐고, 그 예외만 경계에서 삼켜졌다
    assert "bm" not in calls


def test_bm_session_close_failure_does_not_mask_the_sync_exception(calls, monkeypatch):
    """BM 잡 finally의 db.close()도 안 삼킨다 — 경계가 이것까지 막아야 sync 예외가 보존된다."""
    original = scheduler_service._get_own_db_session
    state = {"n": 0}

    def _flaky():
        state["n"] += 1
        session = original()
        if state["n"] == 2:
            monkeypatch.setattr(session, "close", _close_boom)
        return session

    def _close_boom():
        raise RuntimeError("곁가지: close 실패")

    def _sync_boom(_db):
        calls.append("sync")
        raise RuntimeError("진짜 원인: entity_sync 실패")

    monkeypatch.setattr(scheduler_service, "_get_own_db_session", _flaky)
    monkeypatch.setattr(entity_sync, "sync_entities", _sync_boom)

    with pytest.raises(RuntimeError, match="진짜 원인"):
        scheduler_service.sync_naver_entity_job()

    assert "bm" in calls  # BM 본체는 돌았고, close 예외만 경계가 삼켰다


# ══════════════════════════ ④ 크론 폐지(retired) ══════════════════════════


def test_default_states_drops_retired_bm_layer_row(db):
    """defaults에서 빼는 것만으론 prod 행이 계속 07:37에 발화한다 — 행 자체를 지워야 한다."""
    db.add(SchedulerState(job_name="run_naver_bm_layer", cron_expression="37 7 * * *", is_enabled=True))
    db.commit()

    scheduler_service._ensure_default_states(db)

    assert db.query(SchedulerState).filter(
        SchedulerState.job_name == "run_naver_bm_layer"
    ).first() is None
    # 재생성도 없어야 한다(defaults에서 제거됨 — retired 목록에만 남는다)
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_bm_layer", "37 7 * * *")' not in src
    assert 'retired = ("run_naver_bm_layer",)' in src
    # 상류 잡은 그대로 유지
    assert db.query(SchedulerState).filter(
        SchedulerState.job_name == "sync_naver_entity"
    ).first() is not None


def test_default_states_is_idempotent_without_the_retired_row(db):
    """행이 이미 없어도(2회차 기동) 조용히 통과 — 삭제는 멱등."""
    scheduler_service._ensure_default_states(db)
    scheduler_service._ensure_default_states(db)

    assert db.query(SchedulerState).filter(
        SchedulerState.job_name == "run_naver_bm_layer"
    ).count() == 0


def test_job_func_for_no_longer_maps_bm_layer():
    """toggle 라이브 등록·start_scheduler 공용 매핑에서 빠져야 07:37 독립 발화가 안 되살아난다."""
    assert scheduler_service.job_func_for("run_naver_bm_layer") is None
    assert scheduler_service.job_func_for("sync_naver_entity") is scheduler_service.sync_naver_entity_job
    assert scheduler_service.job_func_for("run_naver_bm_deep") is scheduler_service.run_naver_bm_deep_job


# ══════════════════════════ ⑤ catch-up ══════════════════════════


def test_entity_sync_is_last_in_catchup_order():
    """07:35 크론이지만 catch-up은 맨 뒤 — 전량 fetch(수 분)가 돈 잡 복구를 지연시키면 안 되고,
    이 잡은 re-raise라 앞에 두면 네이버 API 한 번 삐끗에 집행 체인 전체가 끊긴다."""
    order = scheduler_service._CATCHUP_ORDER
    assert order[-1] == "sync_naver_entity"
    assert order.index("sync_naver_entity") > order.index("run_naver_auto_operator_daily")


def test_catchup_funcs_cover_entity_sync():
    src = inspect.getsource(scheduler_service._catch_up_morning_batch)
    assert '"sync_naver_entity": sync_naver_entity_job' in src
    # BM은 독립 항목으로 넣지 않는다(체이닝으로 함께 따라잡힘)
    assert "run_naver_bm_layer" not in scheduler_service._CATCHUP_ORDER
