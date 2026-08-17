# test_scheduler_cron_weekday.py — 크론 요일 규약(교훈 #297) 집행 검증
#
# 배경(라이브 실측 2026-08-17): bm_deep이 「일요일 09:20 레인」으로 문서화된 채 **3주 내내
#   월요일에** 돌았다. naver_entity_snapshot.negative_kw_count 기입일 전수가
#   07-27·08-03·08-10·08-17로 **전부 월요일**이고 일요일은 0회였다. 놓친 실행은 0건 —
#   주 1회 리듬이 유지되고 잡은 매번 last_status='ok'라 로그·상태 어디에도 흔적이 없었다.
# 원인: 표준 crontab은 0=일요일인데 APScheduler day_of_week는 0=월…6=일이고,
#   CronTrigger.from_crontab()은 5번째 필드를 규약 변환 없이 그대로 넣는다.
# 커버: ①가드가 숫자 요일을 거부 ②시드 자체가 가드를 통과 ③**두 잡이 실제로 일요일에
#   발화**(문자열이 아니라 발화일로 고정 — 이게 이 파일의 핵심) ④reconcile이 기존 행을
#   갱신 ⑤reconcile이 명단 밖 잡을 **안 건드린다** ⑥is_enabled·last_run_at 보존 ⑦멱등.
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SchedulerState
from app.services import scheduler_service

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def db(tmp_path):
    """prod와 같은 autoflush=False (교훈 #292 — 픽스처가 prod보다 관대하면 그만큼 결함이 통과한다)."""
    engine = create_engine(f"sqlite:///{tmp_path}/cron.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


def _seed_of(db, job_name: str) -> str:
    """빈 DB에 시드를 심고 그 행을 읽는다 — 소스 파싱보다 정직하다(실제로 심기는 값을 본다)."""
    scheduler_service._ensure_default_states(db)
    row = db.query(SchedulerState).filter(SchedulerState.job_name == job_name).one()
    return row.cron_expression


# ═══════════════════ ① 가드가 숫자 요일을 거부한다 ═══════════════════


@pytest.mark.parametrize("dow", ["0", "6", "1-5", "0,6", "*/2"])
def test_guard_rejects_numeric_weekday(dow):
    """숫자·범위·목록 요일은 전부 거부 — 규약이 갈리는 표기를 아예 못 들어오게 한다."""
    with pytest.raises(ValueError, match="숫자 요일 크론 금지"):
        scheduler_service._assert_weekday_crons_are_named([("some_job", f"20 9 * * {dow}")])


@pytest.mark.parametrize("dow", ["sun", "mon", "SUN", "mon-fri", "sat,sun"])
def test_guard_accepts_named_weekday(dow):
    scheduler_service._assert_weekday_crons_are_named([("some_job", f"20 9 * * {dow}")])


def test_guard_ignores_daily_crons():
    """`*`는 대상이 아니다 — 매일 도는 크론엔 규약 차이가 드러나지 않는다."""
    scheduler_service._assert_weekday_crons_are_named([
        ("daily", "0 6 * * *"), ("hourly", "5 * * * *"), ("every2h", "0 */2 * * *"),
    ])


def test_seed_itself_has_no_numeric_weekday(db):
    """★시드 전체가 가드를 통과한다 — 누가 숫자 요일을 새로 넣으면 여기서 깨진다."""
    scheduler_service._ensure_default_states(db)  # 가드는 이 안에서 돈다


def test_guard_is_actually_wired_into_startup(db, monkeypatch):
    """★가드가 **기동 경로에 실제로 걸려 있는지**를 고정한다 (적대 리뷰 1R P2-1 채택).

    가드 함수를 직접 부르는 테스트만 있으면, `_ensure_default_states`에서 호출을 통째로
    지워도 전부 통과한다 — 리뷰어가 그 변이를 실제로 살려 보였다. 그건 「통과하는데
    아무것도 안 지키는 테스트」다(교훈 #181). 배선 자체를 관측해 못박는다.
    """
    seen: list[list] = []
    monkeypatch.setattr(
        scheduler_service, "_assert_weekday_crons_are_named", lambda d: seen.append(list(d))
    )

    scheduler_service._ensure_default_states(db)

    assert seen, "_ensure_default_states가 가드를 부르지 않았다 — 집행 지점이 끊겼다"
    assert ("run_naver_bm_deep", "20 9 * * sun") in seen[0], "가드가 시드 전체를 못 받았다"


def test_startup_refuses_when_guard_rejects(db, monkeypatch):
    """가드가 거부하면 기동이 **멈춘다** — 예외를 삼키면 집행 지점이 장식이 된다."""
    def _boom(_defaults):
        raise ValueError("숫자 요일 크론 금지: 테스트 주입")

    monkeypatch.setattr(scheduler_service, "_assert_weekday_crons_are_named", _boom)

    with pytest.raises(ValueError, match="숫자 요일 크론 금지"):
        scheduler_service._ensure_default_states(db)


# ═══════════════ ② 실제 발화일이 일요일이다 (핵심) ═══════════════


@pytest.mark.parametrize(
    "job_name,expected_hm", [("run_naver_bm_deep", (9, 20)), ("sync_naver_keyword_volume", (9, 0))]
)
def test_weekly_jobs_actually_fire_on_sunday(db, job_name, expected_hm):
    """문자열이 아니라 **발화일**로 고정한다.

    `* * 0`으로 되돌리면 이 테스트가 «월요일»을 관측해 실패한다 — 표기만 바꾸고 동작이
    안 바뀌는 회귀를 잡는 유일한 축이다(교훈 #297의 사고가 정확히 그 형태였다).
    """
    cron = _seed_of(db, job_name)
    trigger = CronTrigger.from_crontab(cron, timezone="Asia/Seoul")

    # 한 해를 통째로 돌려 «매 발화가 일요일»임을 본다(한 번만 보면 우연을 못 가른다).
    cursor = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    fired = 0
    for _ in range(52):
        nxt = trigger.get_next_fire_time(None, cursor)
        if nxt is None:
            break
        assert nxt.weekday() == 6, f"{job_name}: {nxt:%Y-%m-%d}는 일요일이 아니다(weekday={nxt.weekday()})"
        assert (nxt.hour, nxt.minute) == expected_hm
        cursor = nxt
        fired += 1
    assert fired >= 50, f"주 1회면 한 해 50회 이상이어야 하는데 {fired}회"


# ═══════════════ ③ reconcile — 기존 행까지 조정한다 ═══════════════


@pytest.mark.parametrize(
    "job_name,stale,expected",
    [
        ("run_naver_bm_deep", "20 9 * * 0", "20 9 * * sun"),
        ("sync_naver_keyword_volume", "0 9 * * 0", "0 9 * * sun"),
    ],
)
def test_reconcile_updates_existing_row_with_stale_numeric_cron(db, job_name, stale, expected):
    """★prod 실황 재현: 기존 행이 `* * 0`(=월요일)이면 시드 값으로 갱신된다.

    이게 없으면 시드를 고쳐도 prod는 영영 옛 크론으로 발화한다(_ensure_default_states가
    원래 «없는 행만 insert»이기 때문 — retired 주석이 같은 함정을 이미 적어 뒀다).
    ★두 잡 모두 돌린다(적대 리뷰 1R P2-3 채택) — prod에서 실제로 조정이 필요한 행이
    정확히 이 둘인데 한쪽만 실측하고 배포할 이유가 없다.
    """
    db.add(SchedulerState(
        job_name=job_name, cron_expression=stale, is_enabled=True,
    ))
    db.commit()

    scheduler_service._ensure_default_states(db)

    row = db.query(SchedulerState).filter(SchedulerState.job_name == job_name).one()
    assert row.cron_expression == expected
    # 표기가 아니라 발화일로 한 번 더 못박는다.
    nxt = CronTrigger.from_crontab(row.cron_expression, timezone="Asia/Seoul").get_next_fire_time(
        None, datetime(2026, 8, 17, 12, 0, tzinfo=KST)
    )
    assert nxt.weekday() == 6 and nxt.date().isoformat() == "2026-08-23"


def test_reconcile_survives_a_fresh_session(db, tmp_path):
    """갱신이 **commit되는가** — 같은 세션의 캐시가 아니라 DB에 앉았는지 새 커넥션으로 본다.

    이 저장소는 `autoflush=False` 세션에서 「썼다고 믿었는데 안 앉았다」로 다섯 번 당했다
    (교훈 #292). reconcile은 UPDATE라 같은 함정의 사정권이다.
    """
    db.add(SchedulerState(
        job_name="run_naver_bm_deep", cron_expression="20 9 * * 0", is_enabled=True,
    ))
    db.commit()
    scheduler_service._ensure_default_states(db)
    db.close()

    fresh = sessionmaker(
        bind=create_engine(f"sqlite:///{tmp_path}/cron.db"), autoflush=False, autocommit=False
    )()
    try:
        row = fresh.query(SchedulerState).filter(
            SchedulerState.job_name == "run_naver_bm_deep"
        ).one()
        assert row.cron_expression == "20 9 * * sun"
    finally:
        fresh.close()


def test_reconcile_preserves_enabled_and_last_run(db):
    """조정은 크론만 만진다 — 켜고 끄는 것은 운영 판단이고, 실행 이력은 워치독의 근거다."""
    stamp = datetime(2026, 8, 10, 9, 23, 58)
    db.add(SchedulerState(
        job_name="run_naver_bm_deep", cron_expression="20 9 * * 0",
        is_enabled=False, last_run_at=stamp, last_status="ok",
    ))
    db.commit()

    scheduler_service._ensure_default_states(db)

    row = db.query(SchedulerState).filter(
        SchedulerState.job_name == "run_naver_bm_deep"
    ).one()
    assert row.cron_expression == "20 9 * * sun"
    assert row.is_enabled is False
    assert row.last_run_at == stamp
    assert row.last_status == "ok"


def test_reconcile_does_not_touch_jobs_outside_the_ownership_list(db):
    """★명단 밖 잡은 시드와 달라도 그대로 둔다.

    라이브 실측(2026-08-17): `sync_coupang_rg_orders`가 시드 `0 */2 * * *` vs prod
    `55 5 * * *`로 73일째 갈라져 있다. 전량 덮기는 이 잡을 하루 1회에서 12회로 올린다
    (days=30 동기화라 부하가 유의하다) — 그 판단은 운영 몫이라 코드가 삼키면 안 된다.
    """
    db.add(SchedulerState(
        job_name="sync_coupang_rg_orders", cron_expression="55 5 * * *", is_enabled=True,
    ))
    db.commit()

    scheduler_service._ensure_default_states(db)

    row = db.query(SchedulerState).filter(
        SchedulerState.job_name == "sync_coupang_rg_orders"
    ).one()
    assert row.cron_expression == "55 5 * * *", "명단 밖 잡의 크론이 조용히 바뀌었다"
    assert "sync_coupang_rg_orders" not in scheduler_service._CRON_OWNED_BY_CODE


def test_ownership_list_is_exactly_the_two_weekday_jobs():
    """명단 편입 = «이 잡의 스케줄은 코드가 정한다»는 명시적 결정. 늘어나면 리뷰에 걸려야 한다."""
    assert scheduler_service._CRON_OWNED_BY_CODE == frozenset({
        "run_naver_bm_deep", "sync_naver_keyword_volume",
    })


def test_reconcile_is_idempotent(db, caplog):
    """2회차 기동에서 **또 UPDATE하지 않는다** — 결과가 아니라 조정 행위 자체를 본다.

    최종 상태만 보면 `!= cron` 동등성 체크를 지워도 통과한다(적대 리뷰 1R P2-2가 그 변이를
    살렸다). 조정 로그를 세어 「이 이름의 약속」을 실제로 지키게 만든다.
    """
    db.add(SchedulerState(
        job_name="run_naver_bm_deep", cron_expression="20 9 * * 0", is_enabled=True,
    ))
    db.commit()

    with caplog.at_level("WARNING", logger=scheduler_service.log.name):
        scheduler_service._ensure_default_states(db)
        first = [r for r in caplog.records if "cron 조정(코드 정본)" in r.getMessage()]
        caplog.clear()
        scheduler_service._ensure_default_states(db)
        second = [r for r in caplog.records if "cron 조정(코드 정본)" in r.getMessage()]

    assert len(first) == 1, "1회차에 조정이 한 번 일어나야 한다"
    assert second == [], "2회차에 또 조정했다 — 동등성 체크가 없다"

    rows = db.query(SchedulerState).filter(
        SchedulerState.job_name == "run_naver_bm_deep"
    ).all()
    assert len(rows) == 1 and rows[0].cron_expression == "20 9 * * sun"
