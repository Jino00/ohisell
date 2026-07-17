# test_naver_vault_export.py — D-NAO-54 P5 열람층(vault_export_sa + 크론 5지점 등록) 단위 테스트.
# 일기 md(이벤트+해석문+outcome·KST 날짜 배정)·지혜 md(활성/은퇴 frontmatter·승률 tally 조인)·
# INDEX·멱등 재생성·원자적 쓰기(tmp→replace)·env 경로 오버라이드·빈 DB 무해를 검증한다.
# Mac pull 스크립트는 순수 rsync 래퍼라 단위 테스트 불요 — python 문법(ast.parse)만 확인.
from __future__ import annotations

import ast
import inspect
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import OpsDiaryEntry, OpsWisdomCandidate, OpsWisdomEntry
from app.services import scheduler_service
from app.services.naver_ad import vault_export

NOW = datetime(2026, 7, 20, 9, 5)  # KST
TODAY = NOW.date()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """OHISELL_VAULT_DIR 오버라이드 — 테스트 볼트를 tmp_path로."""
    monkeypatch.setenv("OHISELL_VAULT_DIR", str(tmp_path))
    return tmp_path


def _utc_for(action_date: date) -> datetime:
    """_kst_date(created_at)==action_date가 되도록 UTC created_at(=KST 12시)을 만든다."""
    return datetime.combine(action_date, time(3, 0))  # +9h → 12:00 KST 같은 날


def _diary(db, *, action_date, event_type="execute", campaign_id="cmp1", actor="daily",
           target_type="keyword", target_id="nkw-1", action="bid_up", before_value=None,
           after_value=None, rationale=None, outcome_json=None, weekday=None, season=None,
           is_kr_holiday=None, spend_pacing_pct=None, avg_rank=None,
           iphone_launch_offset_days=None):
    e = OpsDiaryEntry(
        created_at=_utc_for(action_date), event_type=event_type, campaign_id=campaign_id,
        actor=actor, target_type=target_type, target_id=target_id, action=action,
        before_value=before_value, after_value=after_value, rationale=rationale,
        outcome_json=outcome_json, weekday=weekday, season=season, is_kr_holiday=is_kr_holiday,
        spend_pacing_pct=spend_pacing_pct, avg_rank=avg_rank,
        iphone_launch_offset_days=iphone_launch_offset_days,
    )
    db.add(e)
    db.flush()
    return e


def _wisdom(db, *, wisdom_text="순위 3밴드에서 bid_up은 유효", source_candidate_id, status="active",
            judge_rationale="근거문", promoted_at=None):
    e = OpsWisdomEntry(
        wisdom_text=wisdom_text, source_candidate_id=source_candidate_id, status=status,
        judge_rationale=judge_rationale, promoted_at=promoted_at or NOW,
    )
    db.add(e)
    db.flush()
    return e


def _candidate(db, *, signature="sig-1", good_count=3, bad_count=1, env_bucket=None):
    c = OpsWisdomCandidate(
        signature=signature, campaign_id="cmp1", action="bid_up",
        good_count=good_count, bad_count=bad_count, occurrences=good_count + bad_count,
        status="promoted", env_bucket_json=json.dumps(env_bucket) if env_bucket else None,
    )
    db.add(c)
    db.flush()
    return c


# ══════════════════════════ 일기 export ══════════════════════════


def test_diary_file_written_with_event_env_and_outcome(db, vault):
    _diary(
        db, action_date=TODAY - timedelta(days=2), before_value="1500", after_value="1720",
        rationale="3밴드 진입", weekday=3, season="summer", spend_pacing_pct=45.0, avg_rank=3.2,
        outcome_json=json.dumps({"d1": {"cost": 1000, "clk": 5, "conv": 500, "roas_c": 1.4}}),
    )
    db.commit()

    res = vault_export.export_vault(db, now=NOW)

    assert res["diary_files"] == 1 and res["error"] is None
    md = (vault / "diary" / f"{(TODAY - timedelta(days=2)).isoformat()}.md").read_text(encoding="utf-8")
    assert "집행·차단 이벤트" in md
    assert "1500→1720" in md         # 변경
    assert "목" in md and "summer" in md and "소진45%" in md and "순위3.2" in md  # 환경 요약
    assert "d1: roas 1.4" in md      # outcome 표기
    assert "3밴드 진입" in md         # 사유


def test_diary_reflection_section_holds_observe_text(db, vault):
    _diary(db, action_date=TODAY - timedelta(days=1), event_type="observe", action="daily_reflection",
           target_type=None, target_id=None, rationale="목요일·저소진과 함께 움직임 관찰.")
    db.commit()

    vault_export.export_vault(db, now=NOW)

    md = (vault / "diary" / f"{(TODAY - timedelta(days=1)).isoformat()}.md").read_text(encoding="utf-8")
    assert "## 해석문" in md
    assert "목요일·저소진과 함께 움직임 관찰." in md


def test_diary_kst_date_assignment(db, vault):
    # created_at UTC 03:00 = KST 12:00 같은 날 → 그 KST 날짜 파일에 배정.
    d = TODAY - timedelta(days=3)
    _diary(db, action_date=d)
    db.commit()

    vault_export.export_vault(db, now=NOW)

    assert (vault / "diary" / f"{d.isoformat()}.md").exists()


def test_diary_only_last_8_days(db, vault):
    _diary(db, action_date=TODAY - timedelta(days=2))   # 창 안
    _diary(db, action_date=TODAY - timedelta(days=30))  # 창 밖
    db.commit()

    res = vault_export.export_vault(db, now=NOW)

    assert res["diary_files"] == 1
    assert not (vault / "diary" / f"{(TODAY - timedelta(days=30)).isoformat()}.md").exists()


# ══════════════════════════ 지혜 export ══════════════════════════


def test_wisdom_active_frontmatter_and_win_rate(db, vault):
    c = _candidate(db, good_count=3, bad_count=1, env_bucket={"season": "summer"})
    _wisdom(db, source_candidate_id=c.id, status="active")
    db.commit()

    res = vault_export.export_vault(db, now=NOW)

    assert res["wisdom_files"] == 1
    files = list((vault / "wisdom").glob("*.md"))
    assert len(files) == 1
    md = files[0].read_text(encoding="utf-8")
    assert "status: active" in md
    assert "win_rate: 75.0%" in md            # frontmatter
    assert "good 3 / bad 1 → 75.0% (표본 4)" in md  # tally 조인
    assert "summer" in md                     # 조건(env_bucket)


def test_wisdom_retired_keeps_file_with_status_frontmatter(db, vault):
    c = _candidate(db)
    _wisdom(db, source_candidate_id=c.id, status="retired")
    db.commit()

    res = vault_export.export_vault(db, now=NOW)

    assert res["wisdom_files"] == 1  # 파일 삭제 아님
    md = list((vault / "wisdom").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "status: retired" in md
    assert "은퇴" in md


def test_wisdom_no_candidate_tally_is_na(db, vault):
    # 후보가 없어도(조인 실패) 지혜 파일은 나오고 승률은 n/a.
    _wisdom(db, source_candidate_id=999)
    db.commit()

    vault_export.export_vault(db, now=NOW)
    md = list((vault / "wisdom").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "win_rate: n/a" in md


# ══════════════════════════ INDEX ══════════════════════════


def test_index_lists_recent_diary_and_active_wisdom(db, vault):
    _diary(db, action_date=TODAY - timedelta(days=1))
    c = _candidate(db)
    _wisdom(db, source_candidate_id=c.id, status="active")
    c2 = _candidate(db, signature="sig-2")
    _wisdom(db, source_candidate_id=c2.id, status="retired")
    db.commit()

    res = vault_export.export_vault(db, now=NOW)

    assert res["index"] is True
    idx = (vault / "INDEX.md").read_text(encoding="utf-8")
    assert f"[[diary/{(TODAY - timedelta(days=1)).isoformat()}" in idx
    assert "## 활성 지혜" in idx
    assert "[[wisdom/001" in idx        # active 지혜 링크
    assert "[[wisdom/002" not in idx    # 은퇴 지혜는 활성 목록에서 제외


# ══════════════════════════ 멱등·원자적·무해 ══════════════════════════


def test_idempotent_regeneration_reflects_new_outcome(db, vault):
    e = _diary(db, action_date=TODAY - timedelta(days=2))
    db.commit()
    vault_export.export_vault(db, now=NOW)

    # 소급 outcome 기입(P2) 시뮬 → 재생성이 반영해야 한다.
    e.outcome_json = json.dumps({"d7": {"cost": 700, "clk": 7, "conv": 350, "roas_c": 2.0}})
    db.commit()
    res = vault_export.export_vault(db, now=NOW)

    assert res["diary_files"] == 1  # 덮어쓰기(중복 아님)
    md = (vault / "diary" / f"{(TODAY - timedelta(days=2)).isoformat()}.md").read_text(encoding="utf-8")
    assert "d7: roas 2.0" in md


def test_no_leftover_tmp_files(db, vault):
    _diary(db, action_date=TODAY - timedelta(days=1))
    c = _candidate(db)
    _wisdom(db, source_candidate_id=c.id)
    db.commit()

    vault_export.export_vault(db, now=NOW)

    # 원자적 쓰기: .*.tmp 잔재가 없어야 한다.
    assert list(vault.rglob(".*.tmp")) == []
    assert (vault / "INDEX.md").exists()


def test_atomic_write_uses_tmp_then_replace(tmp_path, monkeypatch):
    # os.replace가 실패하면 대상 파일은 안 만들어진다 = 쓰기가 tmp에 먼저 감(원자성 증명).
    target = tmp_path / "sub" / "x.md"

    def _boom(src, dst):
        raise RuntimeError("replace 실패")

    monkeypatch.setattr(vault_export.os, "replace", _boom)
    with pytest.raises(RuntimeError):
        vault_export._atomic_write(target, "hello")
    assert not target.exists()                        # 대상은 미생성
    assert (tmp_path / "sub" / ".x.md.tmp").exists()  # tmp만 남음


def test_empty_db_is_harmless(db, vault):
    res = vault_export.export_vault(db, now=NOW)
    assert res == {"diary_files": 0, "wisdom_files": 0, "index": True, "error": None}
    assert (vault / "INDEX.md").exists()  # 빈 인덱스라도 생성


def test_default_vault_root_under_backend_data(monkeypatch):
    monkeypatch.delenv("OHISELL_VAULT_DIR", raising=False)
    root = vault_export._vault_root()
    assert root.parts[-3:] == ("data", "vault", "Ohisell")
    assert root.parent.parent.parent.name == "backend"


# ══════════════════════════ 크론 5지점 등록 ══════════════════════════


def test_job_function_exists_with_self_contained_session():
    assert hasattr(scheduler_service, "run_naver_vault_export_job")
    src = inspect.getsource(scheduler_service.run_naver_vault_export_job)
    assert "_get_own_db_session" in src and "db.close()" in src


def test_default_cron_is_0905_kst():
    src = inspect.getsource(scheduler_service._ensure_default_states)
    assert '("run_naver_vault_export", "5 9 * * *")' in src


def test_registered_in_catchup_order_at_tail():
    """catch-up은 맨 뒤(P5 스펙) — 열람 전용이라 wisdom(승격) 뒤에 재생성해야 최신."""
    order = scheduler_service._CATCHUP_ORDER
    assert "run_naver_vault_export" in order
    assert order.index("run_naver_vault_export") > order.index("run_naver_wisdom")


def test_wired_in_job_func_for_and_catchup_funcs():
    assert scheduler_service.job_func_for("run_naver_vault_export") \
        is scheduler_service.run_naver_vault_export_job
    src = inspect.getsource(scheduler_service._catch_up_morning_batch)
    assert '"run_naver_vault_export": run_naver_vault_export_job' in src


def test_vault_export_cron_job_is_fail_open(monkeypatch):
    """열람 전용 잡 fail-open(raise 없음) — catch-up 체인에서 이 잡이 raise하면 안 된다."""
    def _boom(db, **kwargs):
        raise RuntimeError("vault_export 폭발(테스트)")

    monkeypatch.setattr("app.services.naver_ad.vault_export.export_vault", _boom)
    scheduler_service.run_naver_vault_export_job()  # 예외 전파 시 테스트 실패


# ══════════════════════════ Mac pull 스크립트 문법 ══════════════════════════


def test_mac_pull_script_parses():
    p = Path(__file__).resolve().parents[2] / "scripts" / "ohisell_vault_pull.py"
    ast.parse(p.read_text(encoding="utf-8"))
