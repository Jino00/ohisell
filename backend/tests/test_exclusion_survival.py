# test_exclusion_survival.py — 조치 생존 감시가 **화면까지 흐르는지** 지킨다 (D-NAO-173 P1-①).
#
# ## 왜 이 파일이 있나
# 우리가 네이버에 건 제외가 사라져도 지금까지 아무도 몰랐다. 대행사가 우리 조치를 되돌린 것이
# 2회이고 그중 2026-08-10 그룹 userLock 해제는 **우리 change_log에 행 자체가 없었다** — 즉
# «되돌림을 사건으로 잡는» 경로는 이미 조용히 실패했다. 그래서 감시는 상태 대조로 하고,
# 이 파일은 그 대조가 (판정 → DB → 헬스 dict → HTTP body) 어디서도 끊기지 않는지 본다.
#
# ★배선 테스트를 판정 테스트와 같이 두는 이유: 2026-08-10 적대 리뷰 P1-1이 잡은 형태가
#   «서비스층엔 있는데 response_model이 지운다»였다. 판정만 촘촘히 물리면 그 침묵을 못 본다.
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverSearchTermExclusion
from app.services.naver_ad import exclusion_survival as es
from app.services.scheduler_health import build_health, compute_scheduler_health

NOW = datetime(2026, 8, 11, 12, 0, 0)

# 라이브 검체(2026-08-11 실측) — prod의 유일한 제외 1건.
LIVE_ADGROUP = "grp-a001-01-000000060841583"
LIVE_TERM = "아이패드종이필름"
LIVE_RESTRICT_ID = "rst-a001-00-000001865606760"


@pytest.fixture
def db():
    # StaticPool + check_same_thread=False: TestClient가 다른 스레드에서 라우트를 돌린다.
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
    running = True

    def get_jobs(self):
        return []


def _row(**kw) -> NaverSearchTermExclusion:
    base = dict(
        campaign_id="cmp-1",
        adgroup_id=LIVE_ADGROUP,
        search_term=LIVE_TERM,
        restrict_kwd_id=LIVE_RESTRICT_ID,
        status="excluded",
        cycle=1,
        excluded_at=datetime(2026, 7, 22, 9, 0, 0),
        last_transition_at=datetime(2026, 7, 22, 9, 0, 0),
        next_review_at=date(2026, 8, 21),
        cost_at_exclusion=22854,
    )
    base.update(kw)
    return NaverSearchTermExclusion(**base)


def _live(**kw) -> dict:
    """라이브 응답 실물 형태(2026-08-11 확인)."""
    base = {
        "nccAdgroupRestrictKwdId": LIVE_RESTRICT_ID,
        "nccAdgroupId": LIVE_ADGROUP,
        "keyword": LIVE_TERM,
        "type": "KEYWORD_PLUS_RESTRICT",
        "delFlag": False,
        "regTm": "2026-07-22T00:00:00Z",
    }
    base.update(kw)
    return base


# ═══ 판정층 — _classify ═══


def test_alive_when_our_id_is_present():
    state, note, found = es._classify(_row(), [_live()])
    assert state == es.STATE_ALIVE
    assert found is None


def test_deleted_when_delflag_true():
    """★존재 여부만 보면 소프트 삭제를 «걸려 있음»으로 오독한다 — delFlag가 최종 판정이다."""
    state, note, found = es._classify(_row(), [_live(delFlag=True)])
    assert state == es.STATE_DELETED
    assert "delFlag" in note


def test_missing_when_gone():
    state, note, found = es._classify(_row(), [])
    assert state == es.STATE_MISSING


def test_missing_when_only_other_keywords_remain():
    """★목록이 비어 있지 않아도 «우리 것»이 없으면 missing이다.

    빈 목록만 시험하면 «len(live)==0이면 missing»이라는 가짜 구현도 통과한다.
    """
    other = _live(nccAdgroupRestrictKwdId="rst-other", keyword="전혀다른검색어")
    state, note, found = es._classify(_row(), [other])
    assert state == es.STATE_MISSING


def test_alive_by_keyword_when_id_is_unknown():
    """★콘솔 수동 제외에는 우리가 회수한 id가 없다 — 본문 대조가 없으면 전부 missing으로 보인다.

    이번 스프린트의 제외는 **사람이 콘솔에서** 실행하므로 이 경로가 기본값이다.
    """
    state, note, found = es._classify(
        _row(restrict_kwd_id=None), [_live(nccAdgroupRestrictKwdId="rst-console-1")]
    )
    assert state == es.STATE_ALIVE
    assert found == "rst-console-1", "회수한 id가 없으면 나중에 개방(delete)을 못 한다"


def test_alive_by_keyword_when_id_changed_and_note_says_so():
    """id가 바뀐 채 살아 있으면 alive이되 **경위를 note에 남긴다**(누군가 지웠다 다시 걸었다)."""
    state, note, found = es._classify(_row(), [_live(nccAdgroupRestrictKwdId="rst-new")])
    assert state == es.STATE_ALIVE
    assert found == "rst-new"
    assert "다른 id" in note


def test_near_match_is_unknown_not_alive():
    """★표기만 다른 행을 alive로 세면 **우리 조치가 사라진 바로 그 경우에 초록**이 된다.

    네이버 제외키워드에서 「아이패드 종이필름」과 「아이패드종이필름」은 서로 다른 키워드다.
    그리고 그 id를 회수하면 더 나쁘다 — restrict_kwd_id의 유일한 실쓰기 소비자가 개방
    (delete_restricted_keywords)이라, 우리가 걸지 않은 제외를 나중에 우리가 지운다.
    (적대 리뷰 P1-2. 초판은 이 경우를 alive로 판정했다.)
    """
    state, note, found = es._classify(
        _row(restrict_kwd_id=None),
        [_live(nccAdgroupRestrictKwdId="rst-other", keyword="아이패드 종이필름")],
    )
    assert state == es.STATE_UNKNOWN
    assert found is None, "판별 못 한 행의 id를 회수하면 감시가 나중에 남의 제외를 지운다"


def test_exact_match_wins_over_near_match_regardless_of_response_order():
    """★정확 일치가 응답 뒤쪽에 있어도 그걸 고른다 — 단일 OR 필터면 순서가 판정을 정한다."""
    state, note, found = es._classify(
        _row(restrict_kwd_id=None),
        [
            _live(nccAdgroupRestrictKwdId="rst-other", keyword="아이패드 종이필름"),
            _live(nccAdgroupRestrictKwdId="rst-ours"),
        ],
    )
    assert state == es.STATE_ALIVE
    assert found == "rst-ours"


def test_duplicate_exact_matches_are_unknown_not_a_guess():
    """★동명이 둘이면 어느 행이 우리 것인지 모른다 — writer가 같은 모호성에서 fail-closed로
    거부하는 것과 같은 규율을 쓴다([0]으로 고르면 리포 안에서 규율이 갈라진다)."""
    state, note, found = es._classify(
        _row(restrict_kwd_id=None),
        [_live(nccAdgroupRestrictKwdId="rst-a"), _live(nccAdgroupRestrictKwdId="rst-b")],
    )
    assert state == es.STATE_UNKNOWN
    assert found is None


def test_soft_deleted_same_keyword_is_deleted_not_alive():
    """★delFlag 행을 본문 대조에서 alive로 세면 «지워진 것»이 살아 있는 것으로 뒤집힌다."""
    state, note, found = es._classify(
        _row(restrict_kwd_id=None), [_live(nccAdgroupRestrictKwdId="rst-z", delFlag=True)]
    )
    assert state == es.STATE_DELETED


# ═══ 대조층 — check_survival(라이브 조회는 가짜로 대체) ═══


def _patch_live(monkeypatch, mapping: dict[str, list[dict]], fail: set[str] = frozenset()):
    from app.services.naver_ad import naver_sa_writer

    # 기본은 파워링크(WEB_SITE) — 라이브 대조가 진실을 말하는 유일한 유형이다.
    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "WEB_SITE")

    def fake(adgroup_id: str):
        if adgroup_id in fail:
            raise RuntimeError("boom")
        return mapping.get(adgroup_id, [])

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", fake)


def test_check_survival_writes_state_and_recovers_id(db, monkeypatch):
    db.add(_row(restrict_kwd_id=None))
    db.commit()
    _patch_live(monkeypatch, {LIVE_ADGROUP: [_live(nccAdgroupRestrictKwdId="rst-console-1")]})

    result = es.check_survival(db, now=NOW)

    assert result["checked"] == 1 and result["alive"] == 1
    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_ALIVE
    assert row.live_checked_at == NOW
    assert row.restrict_kwd_id == "rst-console-1"


def test_check_survival_only_looks_at_excluded_rows(db, monkeypatch):
    """★probation/restored는 **우리가 의도적으로 개방한** 상태다 — 라이브에 없는 게 정상이다.

    셋을 한 잣대로 재면 정상 개방이 매일 «어긋남»으로 떠서 배너가 상시 빨강이 된다
    (알림 피로 = 이 계열 감시가 실제로 죽는 방식).
    """
    db.add(_row(status="probation", search_term="개방된검색어"))
    db.add(_row(status="restored", search_term="복귀된검색어", adgroup_id="grp-other"))
    db.commit()
    _patch_live(monkeypatch, {})

    result = es.check_survival(db, now=NOW)
    assert result["checked"] == 0
    assert es.survival_summary(db, now=NOW)["monitored"] == 0


def test_api_failure_becomes_unknown_not_silence(db, monkeypatch):
    """★조회가 죽었을 때 어제의 alive를 남기면 화면이 계속 초록이다 — unknown으로 덮는다."""
    db.add(_row(live_state=es.STATE_ALIVE, live_checked_at=NOW - timedelta(days=1)))
    db.commit()
    _patch_live(monkeypatch, {}, fail={LIVE_ADGROUP})

    result = es.check_survival(db, now=NOW)
    assert result["unknown"] == 1 and result["errors"]
    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_UNKNOWN


def test_one_group_failure_does_not_erase_other_groups(db, monkeypatch):
    """★한 그룹 실패가 감시 전체를 지우면 그 침묵이 «이상 없음»과 구별되지 않는다(교훈 #123)."""
    db.add(_row())
    db.add(_row(adgroup_id="grp-dead", search_term="다른검색어", restrict_kwd_id="rst-dead"))
    db.commit()
    _patch_live(monkeypatch, {LIVE_ADGROUP: [_live()]}, fail={"grp-dead"})

    result = es.check_survival(db, now=NOW)
    assert result["alive"] == 1 and result["unknown"] == 1
    assert result["groups"] == 2


# ═══ 요약층 — survival_summary ═══


def test_summary_is_unhealthy_when_an_old_row_was_never_checked(db):
    """★«한 번도 대조 안 함»은 alive가 아니다 — NULL을 초록으로 세면 감시가 켜지기 전부터 초록이다.

    단 «오래됐는데 여태 안 본» 행이어야 한다(아래 짝 테스트 참조).
    """
    db.add(_row(excluded_at=NOW - timedelta(days=5)))
    db.commit()
    s = es.survival_summary(db, now=NOW)
    assert s["never_checked_due"] == 1 and s["healthy"] is False


def test_a_just_created_exclusion_does_not_turn_the_banner_red(db):
    """★방금 실행한 제외는 다음 대조(08:25) 전까지 NULL인 게 정상이다.

    이걸 곧장 이상으로 세면 **제외를 한 건 실행할 때마다 다음 날까지 전역 배너가 빨강**이고,
    계약 P2가 「Jino가 콘솔에서 20~50건 제외」이므로 그 상태가 상시화된다 — 상시 빨강은 이
    감시가 실제로 죽는 방식이다(적대 리뷰 P1-3. 초판은 여기서 healthy=False였다).
    """
    db.add(_row(excluded_at=NOW - timedelta(hours=2)))
    db.commit()
    s = es.survival_summary(db, now=NOW)
    assert s["never_checked"] == 1, "아직 안 본 사실 자체는 응답에 남는다"
    assert s["never_checked_due"] == 0
    assert s["stale"] is False
    assert s["healthy"] is True


def test_summary_is_unhealthy_when_check_went_stale(db):
    """★대조가 멈춘 것과 어긋남이 없는 것을 같은 초록으로 두면 감시가 죽은 날부터 조용해진다."""
    db.add(_row(live_state=es.STATE_ALIVE, live_checked_at=NOW - timedelta(hours=40)))
    db.commit()
    s = es.survival_summary(db, now=NOW)
    assert s["stale"] is True and s["healthy"] is False


def test_summary_is_healthy_when_alive_and_fresh(db):
    """★정상이면 아무 말도 안 한다 — 상시 켜진 경고는 안 켜진 것과 같다."""
    db.add(_row(live_state=es.STATE_ALIVE, live_checked_at=NOW - timedelta(hours=3)))
    db.commit()
    s = es.survival_summary(db, now=NOW)
    assert s["healthy"] is True and s["breached"] == [] and s["alive"] == 1


def test_summary_breach_carries_enough_to_act_on(db):
    """배너는 «무엇이 어디서 사라졌나»를 말해야 한다 — 건수만으로는 아무것도 못 한다."""
    db.add(_row(live_state=es.STATE_MISSING, live_checked_at=NOW, live_note="사라졌다"))
    db.commit()
    b = es.survival_summary(db, now=NOW)["breached"][0]
    assert b["search_term"] == LIVE_TERM
    assert b["adgroup_id"] == LIVE_ADGROUP
    assert b["live_state"] == es.STATE_MISSING


def test_a_neglected_row_is_caught_even_when_the_check_itself_is_fresh(db):
    """★대조는 어제도 오늘도 돌았는데 **어떤 행만** 계속 안 보고 있는 경우.

    이걸 놓치면 그 행은 영원히 사각지대다 — `stale`은 «마지막 대조 시각»만 보므로 다른 행이
    잘 대조되는 한 초록이다. `never_checked_due`가 healthy 판정에 **따로** 들어가야 하는
    이유가 이것이고, 이 테스트가 없으면 그 항을 지워도 아무것도 안 죽는다(변이 M21 생존).
    """
    db.add(_row(live_state=es.STATE_ALIVE, live_checked_at=NOW - timedelta(hours=2)))
    db.add(_row(search_term="사각지대검색어", restrict_kwd_id="rst-blind",
                excluded_at=NOW - timedelta(days=5)))  # live_checked_at = NULL
    db.commit()

    s = es.survival_summary(db, now=NOW)
    assert s["stale"] is False, "마지막 대조는 신선하다 — stale로는 안 잡힌다"
    assert s["never_checked_due"] == 1
    assert s["healthy"] is False


def test_breached_list_is_capped_but_says_the_total(db):
    """★헬스 응답은 프론트가 폴링마다 받는다 — 어긋남이 수백 건이면 payload가 통째로 부푼다.
    잘라내되 **잘랐다는 사실이 숨지 않게** 총계를 함께 낸다."""
    for i in range(es.BREACH_SAMPLE_CAP + 5):
        db.add(_row(search_term=f"사라진검색어{i}", restrict_kwd_id=f"rst-{i}",
                    live_state=es.STATE_MISSING, live_checked_at=NOW))
    db.commit()
    s = es.survival_summary(db, now=NOW)
    assert len(s["breached"]) == es.BREACH_SAMPLE_CAP
    assert s["breached_total"] == es.BREACH_SAMPLE_CAP + 5


def test_row_without_adgroup_is_marked_undecidable_not_skipped(db, monkeypatch):
    """★대조 불가 행을 조용히 건너뛰면 그 행은 영원히 «한 번도 대조 안 함»으로 남아
    배너가 영구히 빨강이 된다(적대 리뷰 P2-6)."""
    db.add(_row(adgroup_id="", excluded_at=NOW - timedelta(days=5)))
    db.commit()
    _patch_live(monkeypatch, {})

    result = es.check_survival(db, now=NOW)
    assert result["unknown"] == 1
    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_UNKNOWN and row.live_checked_at == NOW


def test_summary_stays_quiet_with_no_rows(db):
    """감시 대상이 0건이면 healthy다 — 없는 것을 이상이라 하면 배너가 처음부터 빨강이다."""
    s = es.survival_summary(db, now=NOW)
    assert s["monitored"] == 0 and s["healthy"] is True


# ═══ 배선층 — build_health → compute → HTTP body ═══


def test_build_health_exposes_key_even_when_clean():
    h = build_health([], [], set(), True, NOW)
    assert "exclusion_survival" in h and h["exclusion_survival"] is None
    assert h["healthy"] is True


def test_build_health_turns_unhealthy_on_breach():
    """★이게 배너를 띄우는 유일한 스위치다."""
    h = build_health([], [], set(), True, NOW, exclusion_survival={"healthy": False, "breached": [1]})
    assert h["healthy"] is False


def test_build_health_stays_healthy_when_survival_is_clean():
    h = build_health([], [], set(), True, NOW, exclusion_survival={"healthy": True, "breached": []})
    assert h["healthy"] is True


def test_compute_health_carries_survival_from_db(db):
    db.add(_row(live_state=es.STATE_MISSING, live_checked_at=NOW))
    db.commit()
    h = compute_scheduler_health(db, _FakeScheduler(), NOW)
    assert h["exclusion_survival"] is not None
    assert len(h["exclusion_survival"]["breached"]) == 1


def _client(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_health_route_actually_returns_exclusion_survival(db):
    """★스키마가 지우지 않는가 — dict가 아니라 **HTTP body**에 있는지 본다.

    `SchedulerHealthOut`에서 `exclusion_survival`을 빼면 여기서 죽는다(2026-08-10 P1-1의 형태).
    """
    db.add(_row(live_state=es.STATE_MISSING, live_checked_at=NOW, live_note="사라졌다"))
    db.commit()
    try:
        r = _client(db).get("/api/scheduler/health")
        assert r.status_code == 200
        body = r.json()
        assert "exclusion_survival" in body, "response_model이 지웠다 — 화면까지 안 간다"
        assert body["exclusion_survival"]["breached"][0]["search_term"] == LIVE_TERM
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_survival_route_returns_summary(db):
    db.add(_row(live_state=es.STATE_ALIVE, live_checked_at=NOW))
    db.commit()
    try:
        r = _client(db).get("/api/naver/ad/search-term/exclusion-survival")
        assert r.status_code == 200
        assert r.json()["alive"] == 1
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_job_is_registered_so_the_check_actually_runs():
    """★«검사기는 있는데 아무도 안 부른다»가 이 리포가 반복해 당한 형태다(교훈 #208).

    크론 등록과 함수 매핑이 **둘 다** 있어야 실제로 돈다 — 한쪽만 있으면 조용히 안 돈다.
    """
    from app.services import scheduler_service as ss

    assert ss.job_func_for("verify_search_term_exclusions") is not None
    src = open(ss.__file__.replace(".pyc", ".py")).read()
    assert '("verify_search_term_exclusions", "25 8 * * *")' in src


def test_shopping_exclusion_is_unverifiable_not_missing(db, monkeypatch):
    """★★2026-08-11 실측: 쇼핑 광고그룹은 콘솔에 제외가 **43건** 등록돼 있어도
    `GET restricted-keywords`가 200/**0건**을 돌려준다 — 이 API는 쇼핑에서 「없다」고 거짓말한다.

    그 거짓말을 missing으로 받으면 **우리가 콘솔에서 자른 검색어가 매일 「사라졌다」로 뜨고**
    배너가 상시 빨강이 된다(= 이 감시가 실제로 죽는 방식). 「사라졌다」와 「볼 수 없다」는 다르다.
    """
    from app.services.naver_ad import naver_sa_writer

    db.add(_row(adgroup_id="grp-shopping", search_term="골프", restrict_kwd_id=None,
                excluded_at=NOW - timedelta(days=5)))
    db.commit()
    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "SHOPPING")
    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords",
                        lambda a: (_ for _ in ()).throw(AssertionError("쇼핑이면 조회조차 하지 않는다")))

    result = es.check_survival(db, now=NOW)
    assert result["unverifiable"] == 1 and result["missing"] == 0

    row = db.query(NaverSearchTermExclusion).one()
    assert row.live_state == es.STATE_UNVERIFIABLE
    assert "SHOPPING" in row.live_note

    s = es.survival_summary(db, now=NOW)
    assert s["breached"] == [], "볼 수 없는 것을 어긋남으로 세면 안 된다"
    assert s["unverifiable"] == 1, "대신 «못 보는 몫»이 숫자로 드러나야 한다"
    assert s["healthy"] is True, "쇼핑 제외 하나 때문에 배너가 상시 빨강이 되면 안 된다"


def test_powerlink_group_is_still_verified_normally(db, monkeypatch):
    """쇼핑 예외가 파워링크 대조까지 꺼 버리면 감시가 통째로 무력해진다."""
    from app.services.naver_ad import naver_sa_writer

    db.add(_row())
    db.commit()
    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "WEB_SITE")
    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [_live()])

    result = es.check_survival(db, now=NOW)
    assert result["alive"] == 1 and result["unverifiable"] == 0
