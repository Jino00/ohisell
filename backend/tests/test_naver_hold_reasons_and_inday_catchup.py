# test_naver_hold_reasons_and_inday_catchup.py — D-NAO-258 (계약 「점화준비 목표」 S1)
#
# 무엇을 지키는 테스트인가 (둘 다 «사람이 보는 표면»까지 간다):
#   S1-a 탐색 차단 사유가 **로그 한 줄에서 사유별로 갈라져** 보인다.
#   S1-b 결손 회차가 «그날 안에» 보충되고, 결손 0도 로그에 찍힌다.
#
# ★설계 의도(전역 §4): 단위 테스트는 「함수가 값을 만드나」를 묻지 「사람이 그걸 보나」를 못
#   묻는다. 그래서 이 파일은 카운터 계산뿐 아니라 **스케줄러 로그 라인의 실제 출력 문자열**을
#   assert한다 — 집계는 되는데 로그에 안 실리는 상태(=2026-08-26에 실제로 있던 상태)가 초록으로
#   통과하면 안 되기 때문이다. 호출부/렌더 제거 변이가 이 파일에서 반드시 빨간불이 된다.
from __future__ import annotations

import importlib.util
import logging
import sys
import types
from datetime import datetime, timedelta

import pytest

if importlib.util.find_spec("apscheduler") is None:  # pragma: no cover - 설치 환경에선 미실행
    _apscheduler = types.ModuleType("apscheduler")
    _events = types.ModuleType("apscheduler.events")
    _events.EVENT_JOB_EXECUTED = 1
    _events.EVENT_JOB_ERROR = 2
    _events.EVENT_JOB_MISSED = 4
    _schedulers = types.ModuleType("apscheduler.schedulers")
    _background = types.ModuleType("apscheduler.schedulers.background")

    class _StubBackgroundScheduler:
        def __init__(self, *a, **kw):
            pass

        def add_job(self, *a, **kw):
            pass

        def add_listener(self, *a, **kw):
            pass

        def start(self):
            pass

        def shutdown(self, *a, **kw):
            pass

    _background.BackgroundScheduler = _StubBackgroundScheduler
    _triggers = types.ModuleType("apscheduler.triggers")
    _cron = types.ModuleType("apscheduler.triggers.cron")

    class _StubCronTrigger:
        @classmethod
        def from_crontab(cls, *a, **kw):
            return cls()

    _cron.CronTrigger = _StubCronTrigger

    sys.modules["apscheduler"] = _apscheduler
    sys.modules["apscheduler.events"] = _events
    sys.modules["apscheduler.schedulers"] = _schedulers
    sys.modules["apscheduler.schedulers.background"] = _background
    sys.modules["apscheduler.triggers"] = _triggers
    sys.modules["apscheduler.triggers.cron"] = _cron

from app.services import scheduler_service  # noqa: E402
from app.services.naver_ad import auto_operator  # noqa: E402


# ══════════════════════ S1-a ① 분류기 — 실제 코드가 만드는 문자열로 ══════════════════════

def test_retro_stale_reason_produced_by_code_classifies_as_retro_stale():
    """★이 테스트의 핵심: 분류기를 «내가 지어낸 문자열»이 아니라 `_bleeding_hold_reason`이
    실제로 만드는 문자열로 검증한다. 사유 문구가 바뀌면 분류가 조용히 other로 떨어지는데,
    그러면 로그는 계속 찍히지만 숫자가 엉뚱한 칸으로 간다(제일 잡기 어려운 실패 모드)."""
    latest = "2026-08-20"
    expected = "2026-08-25"
    reason = (
        f"④소급채점 stale — latest_asof={latest} < 기대 "
        f"{expected}(당일 retro 미완주, fail-closed, codex 4R[P1], "
        f"허용지연={auto_operator._RETRO_ASOF_MAX_LAG_DAYS}일)"
    )
    assert auto_operator.classify_hold_reason(reason) == "retro_stale"


def test_retro_stale_wins_over_generic_retro_rules():
    """순서 계약: 좁은 규칙이 넓은 규칙을 이긴다. 'stale'이 'bleeding'/'데이터 없음'과
    같은 ④ 계열이라 규칙 순서가 뒤집히면 1,890건이 통째로 다른 칸에 쌓인다."""
    assert auto_operator.classify_hold_reason("④소급채점 stale — latest_asof=...") == "retro_stale"
    assert auto_operator.classify_hold_reason("④소급채점 데이터 없음 — bleeding 검증 불가") == "retro_missing"
    assert auto_operator.classify_hold_reason("④최신 소급채점에서 bleeding으로 판정됨") == "bleeding"


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("②rationale 창 클릭 부족(clk=3)", "clk_short"),
        ("EX 멤버십 재검증 실패 — 배분 목록에 없음(target=g1, clk=12)", "ex_membership"),
        ("②③ EX 프라이어 폴백 불가 — 캠페인 확장 모드 아님(clk=2, ...)", "ex_fallback_denied"),
        (auto_operator._CTR_ALERT_LADDER_SKIP_REASON, "ctr_alert"),
        ("daily 손실상태 제외 — 스톱로스", "daily_loss"),
        ("[탐색] daily 손실상태 제외 — floored", "daily_loss"),
        ("탐색: 킬스위치 OFF(실행 직전 재확인)", "kill_switch"),
        ("D-NAO-40: 최근 외부/수동 정지 이력 발견 — hold", "external_stop"),
        ("타깃 엔티티 status='deleted'(≠on) — 실행 불가 대상 사전 제외", "entity_not_on"),
    ],
)
def test_known_reason_strings_classify(reason, expected):
    assert auto_operator.classify_hold_reason(reason) == expected


# ★사유 인구조사(2026-08-27 실측) — auto_operator가 result["held"]에 실을 수 있는 사유 문구를
#   코드에서 훑어 모은 것. 이 목록이 「other로 떨어지면 실패」의 기준선이다.
#   왜 목록으로 고정하나: 2026-08-27에 「target_bid 없음 — 구조 결함」이 분류표에 없어 other로
#   떨어지는 것을 라이브 레인 테스트가 잡았다. 하나씩 때우면 다음 문구에서 또 샌다 —
#   인구조사를 테스트로 굳혀야 «새 사유를 추가하면서 분류를 안 고친 것»이 빨간불이 된다.
_REASON_CENSUS = (
    "④소급채점 stale — latest_asof=2026-08-20 < 기대 2026-08-25(당일 retro 미완주)",
    "④소급채점 데이터 없음 — bleeding 검증 불가(fail-closed)",
    "④최신 소급채점에서 bleeding으로 판정됨",
    # D-NAO-289 적대리뷰 P1-1 수리(2026-09-05): 「표본 부족」 예외 판정 후에도 손실이 확정인
    # 경우의 사유문 — 안정 어구(needle "소급채점에서 bleeding")를 앞에, 비교값을 뒤에 둬야
    # classify_hold_reason이 "daily_loss"가 아니라 "bleeding"으로 잡는다(리뷰어가 실행으로
    # 확정한 회귀: 값이 needle 앞에 오면 버킷이 조용히 옮겨간다).
    "④최신 소급채점에서 bleeding으로 판정됨 — 14일 비용 17617원 ≥ 스톱로스 4600원(D-NAO-289)",
    "④grain='keyword' 판정 불가(bleeding 보드 매핑 없음, fail-closed)",
    "②rationale 창 클릭 부족(clk=3)",
    "EX 멤버십 재검증 실패 — 배분 목록에 없음(target=g1, clk=12)",
    "②③ EX 프라이어 폴백 불가 — 캠페인 확장 모드 아님(clk=2)",
    "daily 손실상태 제외 — 스톱로스",
    "[탐색] daily 손실상태 제외 — floored",
    "탐색: 킬스위치 OFF(실행 직전 재확인)",
    "D-NAO-40: 최근 외부/수동 정지 이력 발견 — hold",
    "타깃 엔티티 status='deleted'(≠on) — 실행 불가 대상 사전 제외",
    "target_bid 없음 — 구조 결함(재생성 필요)",
    "자동운영 스코프 밖 광고그룹(g1) — D-NAO-244",
    "당일(2026-08-26) 소진 스냅샷 부재 — 서킷브레이커 평가 불가",
    "소진 스냅샷 stale — 최신 snapshot_hour=3 < now.hour-1=7",
    "소진 서킷브레이커 — 당일 90000원 > 직전7일평균×3(20000원×3)",
    "당일 imp 없음",
    "순위 근거 없음 — 게이트 미적용",
    "탐색: 라이브 현재가 재조회 실패",
    "탐색: 소재 입찰 라이브 재조회 실패",
    "유령 지면(순위 7.20>5)·증거창 비활성 — 첫 스텝 차단",
    "예산봉투 보류/stale — 익일 08:00 재생성",
)


@pytest.mark.parametrize("reason", _REASON_CENSUS)
def test_every_known_reason_is_classified(reason):
    """★인구조사 전건이 분류돼야 한다. other가 나오면 «사유는 늘렸는데 분류표를 안 고쳤다»는 뜻."""
    got = auto_operator.classify_hold_reason(reason)
    assert got not in {"other", "unknown"}, f"미분류: {reason!r} → {got}"


def test_d_nao_289_confirmed_loss_reason_stays_in_bleeding_bucket():
    """★적대리뷰 P1-1 재발 방지: 인구조사에 «있다»만으로는 부족하다 — «칸이 옮겨진 것»은
    other/unknown 검사로는 안 잡히고 여전히 daily_loss로 조용히 통과한다(2026-09-05 리뷰가
    실행으로 확정한 회귀). 그러니 이 사유는 반드시 "bleeding"으로 분류돼야 한다고 못 박는다 —
    비교값(14일 비용·스톱로스 금액)이 달라도 버킷은 흔들리지 않는다."""
    reason_a = (
        "④최신 소급채점에서 bleeding으로 판정됨 — 14일 비용 17617원 "
        "≥ 스톱로스 4600원(D-NAO-289)"
    )
    reason_b = (
        "④최신 소급채점에서 bleeding으로 판정됨 — 14일 비용 9370원 "
        "≥ 스톱로스 1500원(D-NAO-289)"
    )
    assert auto_operator.classify_hold_reason(reason_a) == "bleeding"
    assert auto_operator.classify_hold_reason(reason_b) == "bleeding"


def test_spend_stale_is_not_confused_with_retro_stale():
    """★둘 다 「stale」이지만 다른 병이다 — 섞이면 「탐색을 막는 건 소급채점」이라는 결론이
    소진 스냅샷 문제로 오염된다(진단이 통째로 틀어진다)."""
    assert auto_operator.classify_hold_reason(
        "소진 스냅샷 stale — 최신 snapshot_hour=3 < now.hour-1=7"
    ) == "spend_snapshot_stale"
    assert auto_operator.classify_hold_reason(
        "④소급채점 stale — latest_asof=2026-08-20 < 기대"
    ) == "retro_stale"


def test_unknown_and_empty_are_not_silently_merged():
    """0으로 감추지 않는다 — 사유를 안 적은 자리(unknown)와 분류가 낡은 자리(other)는 다른 병이다."""
    assert auto_operator.classify_hold_reason(None) == "unknown"
    assert auto_operator.classify_hold_reason("") == "unknown"
    assert auto_operator.classify_hold_reason("듣도 보도 못한 사유") == "other"


# ══════════════════════ S1-a ② 집계·표기 ══════════════════════

def test_summarize_counts_and_orders_desc_then_by_key():
    held = [
        {"id": 1, "reason": "④소급채점 stale — a"},
        {"id": 2, "reason": "④소급채점 stale — b"},
        {"id": 3, "reason": "②rationale 창 클릭 부족(clk=1)"},
        {"id": 4, "reason": auto_operator._CTR_ALERT_LADDER_SKIP_REASON},
    ]
    got = auto_operator.summarize_held_by_reason(held)
    assert got == {"retro_stale": 2, "clk_short": 1, "ctr_alert": 1}
    # 회차 간 눈 비교가 목적이라 순서가 흔들리면 안 된다(건수 desc → 키 asc).
    assert list(got.keys()) == ["retro_stale", "clk_short", "ctr_alert"]


def test_summarize_accepts_plain_strings_and_missing_reason():
    """레인마다 held 원소 형태가 다르다(dict/문자열). 한 형태만 받으면 그 레인 집계가 0이 된다."""
    got = auto_operator.summarize_held_by_reason(
        ["④소급채점 stale — x", {"target_id": "g1"}, {"reason": "④소급채점 stale — y"}]
    )
    assert got["retro_stale"] == 2
    assert got["unknown"] == 1


def test_summarize_empty_is_empty_dict():
    assert auto_operator.summarize_held_by_reason([]) == {}
    assert auto_operator.summarize_held_by_reason(None) == {}


def test_format_distinguishes_zero_from_uncounted():
    """"-"는 «집계 결과 0건»이고, 숫자 나열은 실제 분해다. 빈 문자열이면 로그에서 항목이
    통째로 사라져 「집계가 도는가」를 못 본다(교훈 #123)."""
    assert auto_operator.format_held_by_reason({}) == "-"
    assert auto_operator.format_held_by_reason({"retro_stale": 12, "bleeding": 3}) == (
        "retro_stale=12 bleeding=3"
    )


# ══════════════════════ S1-a ③ ★표면 — 스케줄러 로그가 실제로 낸다 ══════════════════════

class _FakeDB:
    def close(self):
        pass


@pytest.fixture
def _fake_db(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: _FakeDB())


def test_daily_lane_log_line_actually_prints_reason_breakdown(monkeypatch, caplog, _fake_db):
    """★표면 테스트(일 레인). 카운터가 result에 있어도 로그 라인에 안 실리면 Jino는 못 본다 —
    2026-08-26에 실제로 그 상태였다. 로그 «출력 문자열»을 본다."""
    fake_result = {
        "reviewed": 9, "approved": 1, "executed": 1, "failed": 0,
        "held": [{"id": i, "reason": "④소급채점 stale — x"} for i in range(7)],
        "held_by_reason": {"retro_stale": 7},
    }
    monkeypatch.setattr(
        "app.services.naver_ad.auto_operator.run_daily_lane", lambda db: fake_result
    )
    with caplog.at_level(logging.INFO):
        scheduler_service.run_naver_auto_operator_daily_job()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "auto_operator daily" in line
    assert "held_by_reason" in line
    assert "retro_stale=7" in line  # ← 사유별 «숫자»가 실제로 찍혀야 한다


def test_hourly_lane_log_line_actually_prints_reason_breakdown(monkeypatch, caplog, _fake_db):
    """★표면 테스트(시간당 레인). 탐색의 본진이라 여기가 끊기면 S1-a는 의미가 없다."""
    # ★키를 전부 채운다 — «부족하면 skip»하는 탈출구를 두지 않는다. 표면 테스트가 skip으로
    #   빠지면 초록인데 아무것도 안 지킨 상태가 되고, 그건 이 파일이 막으려는 바로 그 병이다.
    fake_result = {
        "reviewed": 3, "approved": 0, "executed": 0, "skipped": 0, "failed": 0, "probed": 0,
        "held": [], "held_by_reason": {"retro_stale": 31, "ctr_alert": 2},
        "explored": 0, "explored_held": 0, "explored_capped": 0, "explored_not_rank": 0,
        "explored_ghost_hold": 0, "ghost_hold_groups": [], "explored_not_serving": 0,
        "ad_auto_exec_reserved": 0, "ad_auto_exec_capped": 0,
        "ad_auto_exec_inflight_skipped": 0,
        "ad_confirm_pending": 0, "ad_confirm_pending_dup_skipped": 0,
        "budget_pacing_reviewed": 0, "budget_pacing_raised": 0, "budget_pacing_failed": 0,
        "budget_pacing_dry_run": 0, "budget_pacing_held": [],
        "budget_pacing_restore_reviewed": 0, "budget_pacing_restored": 0,
        "budget_pacing_restore_failed": 0,
    }
    monkeypatch.setattr(
        "app.services.naver_ad.auto_operator.run_hourly_lane", lambda db: fake_result
    )
    with caplog.at_level(logging.INFO):
        scheduler_service.run_naver_auto_operator_hourly_job()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "held_by_reason" in line
    assert "retro_stale=31" in line


# ══════════════════ S1-a ④ ★레인이 «실제로» 파생하는가 (mock 없음) ══════════════════
#
# 왜 이 절이 따로 있나: 위 ③의 표면 테스트는 레인 결과를 mock한다. 그래서 레인 말미의
# `result["held_by_reason"] = summarize_held_by_reason(...)` 한 줄을 지워도 ③은 전부 초록이다
# (실제로 변이 주입에서 그 변이가 살아남았다). 만드는 층과 닿는 층은 다른 층이라, 닿는 층만
# 지키면 만드는 층이 조용히 죽는다(교훈 #362). 이 절이 «만드는 층»을 지킨다.

@pytest.fixture
def lane_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # autoflush=False — prod 세션과 같은 설정으로 맞춘다(다르면 잡히는 결함이 달라진다).
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


_LANE_CAMPAIGN = "cmp-s1a"
_LANE_TODAY = datetime(2026, 8, 26, 8, 50, 0)


def test_daily_lane_really_derives_held_by_reason(lane_db):
    """★레인을 mock 없이 돌려서, hold가 난 만큼 사유별 집계가 «실제로» 채워지는지 본다.

    합계 불변식(sum(held_by_reason) == len(held))을 쓰는 이유: 어느 게이트가 먼저 걸리는지는
    픽스처 상세에 달렸는데, 이 테스트가 지키려는 것은 «어느 칸에 들어가느냐»가 아니라
    «집계가 held와 갈라지지 않는다»이기 때문이다. 파생 호출을 지우면 좌변이 0이 되어 죽는다.
    """
    from app.models import NaverCampaignSettings, NaverProposal

    lane_db.add(
        NaverCampaignSettings(campaign_id=_LANE_CAMPAIGN, auto_operate=True, optimizer="ours")
    )
    lane_db.commit()

    p = NaverProposal(
        proposal_type="bid_up", target_type="adgroup", target_id="grp-1",
        campaign_id=_LANE_CAMPAIGN, adgroup_id="grp-1",
        rationale="테스트 상향", expected_effect="x", status="pending",
    )
    lane_db.add(p)
    lane_db.commit()
    created = datetime(2026, 8, 26, 0, 0) - timedelta(hours=9) + timedelta(hours=1)
    lane_db.query(NaverProposal).filter(NaverProposal.id == p.id).update({"created_at": created})
    lane_db.commit()

    result = auto_operator.run_daily_lane(lane_db, now=_LANE_TODAY)

    assert result["held"], "픽스처가 hold를 하나도 안 만들었다 — 이 테스트가 무의미해진다"
    counts = result["held_by_reason"]
    assert counts, "레인이 held_by_reason을 파생하지 않았다(집계 호출 누락)"
    assert sum(counts.values()) == len(result["held"]), (
        f"집계가 held와 갈라졌다: {counts} vs held={len(result['held'])}"
    )
    # 사유를 못 읽어 전부 unknown/other로 떨어지면 로그가 있어도 쓸모가 없다.
    assert set(counts) - {"unknown", "other"}, f"전건이 미분류로 떨어졌다: {counts}"


def test_hourly_lane_really_derives_held_by_reason(lane_db):
    """★적대 리뷰 P2-1: daily에만 mock-free 테스트가 있고 hourly엔 없어서, 시간당 레인의
    파생 호출을 지우는 변이가 **살아남았다**. 시간당 레인이 탐색의 본진이므로 여기가 조용히
    죽으면 S1-a는 통째로 무의미해진다 — 같은 병을 두 번 겪지 않도록 대칭으로 채운다."""
    from app.models import NaverCampaignSettings

    lane_db.add(
        NaverCampaignSettings(campaign_id=_LANE_CAMPAIGN, auto_operate=True, optimizer="ours")
    )
    lane_db.commit()

    result = auto_operator.run_hourly_lane(
        lane_db, now=datetime(2026, 8, 26, 10, 20), fetch_intraday=lambda *a, **k: []
    )

    assert result["held"], "픽스처가 hold를 안 만들었다 — 이 테스트가 무의미해진다"
    counts = result["held_by_reason"]
    assert counts, "시간당 레인이 held_by_reason을 파생하지 않았다(집계 호출 누락)"
    assert sum(counts.values()) == len(result["held"])
    assert set(counts) - {"unknown", "other"}, f"전건이 미분류로 떨어졌다: {counts}"


def test_daily_lane_early_return_still_exposes_the_key(lane_db):
    """auto_operate 캠페인이 0이면 조기 반환한다 — 그 경로에서도 키가 있어야 로그가 KeyError를
    안 낸다(로그 라인이 result[...]를 직접 읽기 때문이다)."""
    result = auto_operator.run_daily_lane(lane_db, now=_LANE_TODAY)
    assert result["held_by_reason"] == {}
    assert auto_operator.format_held_by_reason(result["held_by_reason"]) == "-"


# ══════════════════════ S1-b 당일 결손 보충 ══════════════════════

class _FakeState:
    def __init__(self, job_name, cron, last_run_at, enabled=True):
        self.job_name = job_name
        self.cron_expression = cron
        self.last_run_at = last_run_at
        self.is_enabled = enabled


class _StateQueryDB:
    """_missed_morning_jobs가 쓰는 최소 쿼리 표면만 흉내낸다."""

    def __init__(self, states):
        self._by_name = {s.job_name: s for s in states}
        self._want = None

    def query(self, *_a):
        return self

    def filter(self, criterion):
        # SchedulerState.job_name == "x" 의 우변만 꺼낸다
        self._want = criterion.right.value
        return self

    def first(self):
        return self._by_name.get(self._want)

    def close(self):
        pass


_WISDOM_CRON = "45 8 * * *"


def _states(last_run_at):
    return [_FakeState("run_naver_wisdom", _WISDOM_CRON, last_run_at)]


def test_missed_detects_wisdom_when_round_never_ran():
    """2026-08-26 실사고 재현: 08:45 회차가 통째로 안 돌았다(last_run_at=어제)."""
    now = datetime(2026, 8, 26, 10, 47)
    db = _StateQueryDB(_states(datetime(2026, 8, 25, 8, 45)))
    missed = scheduler_service._missed_morning_jobs(
        db, now, only={"run_naver_wisdom"}, min_lag=timedelta(minutes=30)
    )
    assert missed == ["run_naver_wisdom"]


def test_min_lag_prevents_double_firing_a_still_running_job():
    """★이게 없으면 스윕이 판사를 이중 호출한다. last_run_at은 «성공 시»에만 갱신되므로
    08:47(발화 2분 뒤)엔 실행 중인 잡과 결손이 구분되지 않는다."""
    now = datetime(2026, 8, 26, 8, 47)  # 예정 08:45 + 2분
    db = _StateQueryDB(_states(datetime(2026, 8, 25, 8, 45)))
    missed = scheduler_service._missed_morning_jobs(
        db, now, only={"run_naver_wisdom"}, min_lag=timedelta(minutes=30)
    )
    assert missed == []


def test_successful_round_is_not_re_run():
    """멱등: 오늘 성공했으면 이후 스윕에서 제외된다."""
    now = datetime(2026, 8, 26, 12, 47)
    db = _StateQueryDB(_states(datetime(2026, 8, 26, 8, 45, 30)))
    missed = scheduler_service._missed_morning_jobs(
        db, now, only={"run_naver_wisdom"}, min_lag=timedelta(minutes=30)
    )
    assert missed == []


def test_only_filter_keeps_money_jobs_out_of_the_sweep():
    """계약 §3: 스윕 대상은 관측·보고 잡뿐. 집행 잡이 섞이면 금지선 위반이다."""
    assert "run_naver_auto_operator_daily" not in scheduler_service._INDAY_CATCHUP_JOBS
    assert "generate_naver_proposals" not in scheduler_service._INDAY_CATCHUP_JOBS
    assert "run_naver_wisdom" in scheduler_service._INDAY_CATCHUP_JOBS


def test_sweep_is_not_in_catchup_order_no_self_recursion():
    assert "run_naver_inday_catchup" not in scheduler_service._CATCHUP_ORDER


def test_sweep_job_is_registered_with_a_cron_and_a_func():
    """목록에만 넣고 함수 등록을 빠뜨리면 조용히 안 돈다(_run_morning_catchup이 겪은 병)."""
    crons = dict(scheduler_service._default_jobs()) if hasattr(scheduler_service, "_default_jobs") else None
    if crons is None:
        src = open(scheduler_service.__file__, encoding="utf-8").read()
        assert '("run_naver_inday_catchup", "47 * * * *")' in src
    else:  # pragma: no cover - 등록 API가 생기면 이쪽
        assert crons["run_naver_inday_catchup"] == "47 * * * *"
    assert scheduler_service.job_func_for("run_naver_inday_catchup") is not None


def test_sweep_logs_zero_missed_so_silence_is_distinguishable(monkeypatch, caplog):
    """★결손 0도 찍혀야 한다 — 안 그러면 「스윕이 도는가」와 「결손이 없는가」가 같은 침묵이다."""
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: _FakeDB())
    monkeypatch.setattr(scheduler_service, "_missed_morning_jobs", lambda *a, **k: [])
    with caplog.at_level(logging.INFO):
        scheduler_service.run_naver_inday_catchup_job()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "당일 catch-up 스윕" in line
    assert "missed=0" in line


def test_sweep_actually_calls_the_missed_job(monkeypatch, caplog):
    """★보충이 «실제로 실행»되는지 — 감지만 하고 안 돌리면 S1-b는 미달이다."""
    calls: list[str] = []
    monkeypatch.setattr(scheduler_service, "_get_own_db_session", lambda: _FakeDB())
    monkeypatch.setattr(
        scheduler_service, "_missed_morning_jobs", lambda *a, **k: ["run_naver_wisdom"]
    )
    monkeypatch.setattr(
        scheduler_service, "run_naver_wisdom_job", lambda: calls.append("wisdom")
    )
    monkeypatch.setattr(scheduler_service, "_record_catchup_status", lambda *a, **k: None)

    started: list = []
    real_thread = scheduler_service.threading.Thread

    def _sync_thread(target=None, name=None, daemon=None, **kw):
        started.append(name)

        class _T:
            def start(self_inner):
                target()

        return _T()

    monkeypatch.setattr(scheduler_service.threading, "Thread", _sync_thread)
    try:
        with caplog.at_level(logging.INFO):
            scheduler_service.run_naver_inday_catchup_job()
    finally:
        monkeypatch.setattr(scheduler_service.threading, "Thread", real_thread)

    assert calls == ["wisdom"], "결손 감지만 하고 보충 실행을 안 했다"
    assert "naver-inday-catchup" in started


# ═══════════ 적대 리뷰 P1-1 수정: 단일 실행 락(같은 잡 이중 실행 금지) ═══════════
#
# 리뷰어가 실증한 것: 08:45 판사가 아직 도는 중에 :47 스윕이 func()를 직접 호출하면 같은
# 후보에 LLM이 두 번 돌고, 나중 커밋이 먼저 커밋을 stale 스냅샷으로 덮어써 **이중 실행
# 사실 자체가 흔적 없이 사라진다**. 초판의 30분 마진은 보장이 아니었다 —
# 판사 최악 실행시간은 _MAX_PER_RUN_BACKLOG(15) × 재시도 총합 9분 = **135분**이다.

def test_observation_jobs_are_actually_decorated():
    """★데코레이터 제거 변이를 죽인다. functools.wraps가 남기는 __wrapped__로 확인한다."""
    for fn in (
        scheduler_service.run_naver_wisdom_job,
        scheduler_service.run_naver_diary_reflection_job,
        scheduler_service.run_naver_profit_scorecard_job,
        scheduler_service.run_naver_vault_export_job,
    ):
        assert hasattr(fn, "__wrapped__"), f"{fn.__name__}에 singleflight 데코레이터가 없다"


def test_job_is_skipped_while_the_same_job_is_running():
    """★핵심 계약: 락이 잡혀 있으면 잡 본체를 «호출하지 않고» 표식을 돌려준다.
    본체가 안 돌았다는 것을 DB 접근 0으로 확인한다(_get_own_db_session이 불리면 실패)."""
    lock = scheduler_service._singleflight_lock("run_naver_wisdom")
    assert lock.acquire(blocking=False)
    try:
        result = scheduler_service.run_naver_wisdom_job()
    finally:
        lock.release()
    assert result is scheduler_service.SKIPPED_ALREADY_RUNNING


def test_lock_is_released_even_when_the_job_raises():
    """락을 놓지 않으면 그 잡이 **영원히** 생략된다 — 조용한 영구 정지다."""
    calls = []

    @scheduler_service.singleflight("test-raises")
    def _boom():
        calls.append(1)
        raise RuntimeError("의도된 실패")

    with pytest.raises(RuntimeError):
        _boom()
    with pytest.raises(RuntimeError):
        _boom()  # 락이 안 풀렸으면 여기서 SKIPPED가 돌아와 raise가 안 난다
    assert calls == [1, 1]


def test_concurrent_calls_execute_the_body_only_once():
    """★리뷰어 재현의 축약판 — 동시 호출 시 본체는 한 번만 돈다."""
    import threading as _t

    entered, released = _t.Event(), _t.Event()
    bodies = []

    @scheduler_service.singleflight("test-concurrent")
    def _slow():
        bodies.append(1)
        entered.set()
        released.wait(timeout=5)
        return "ran"

    outcomes = {}
    t = _t.Thread(target=lambda: outcomes.setdefault("first", _slow()))
    t.start()
    assert entered.wait(timeout=5), "첫 호출이 본체에 못 들어갔다"
    outcomes["second"] = _slow()   # 첫 호출이 도는 중에 두 번째
    released.set()
    t.join(timeout=5)

    assert bodies == [1], "본체가 두 번 돌았다 — 락이 장식이다"
    assert outcomes["second"] is scheduler_service.SKIPPED_ALREADY_RUNNING
    assert outcomes["first"] == "ran"


def test_sweep_does_not_record_success_when_the_job_was_skipped():
    """★가장 아픈 자리: 생략을 «성공»으로 기록하면 last_run_at이 전진해 **진짜 결손이
    영영 안 돈다**. 생략은 성공도 실패도 아니므로 상태를 건드리면 안 된다."""
    recorded = []
    _patch = pytest.MonkeyPatch()
    _patch.setattr(scheduler_service, "_get_own_db_session", lambda: _FakeDB())
    _patch.setattr(scheduler_service, "_missed_morning_jobs", lambda *a, **k: ["run_naver_wisdom"])
    _patch.setattr(
        scheduler_service, "run_naver_wisdom_job",
        lambda: scheduler_service.SKIPPED_ALREADY_RUNNING,
    )
    _patch.setattr(
        scheduler_service, "_record_catchup_status",
        lambda job_name, **kw: recorded.append((job_name, kw)),
    )
    _patch.setattr(scheduler_service.threading, "Thread", _sync_thread_factory())
    try:
        scheduler_service.run_naver_inday_catchup_job()
    finally:
        _patch.undo()
    assert recorded == [], f"생략인데 상태를 기록했다: {recorded}"


def _sync_thread_factory():
    def _sync_thread(target=None, name=None, daemon=None, **kw):
        class _T:
            def start(self):
                target()
        return _T()
    return _sync_thread


# ═══════════ 적대 리뷰 P2-2 / P2-3: 살아남은 변이 두 개를 닫는다 ═══════════

def test_only_filter_actually_excludes_money_jobs_at_runtime():
    """★P2-2: 기존 테스트는 frozenset «내용»만 봤다 — 필터를 지우는 변이가 살아남았다.
    돈 잡을 실제로 seed해서 «런타임에 배제되는지»를 본다. _CATCHUP_ORDER엔 돈 잡이
    실재하므로(auto_operator_daily·proposals) 이 필터가 §3 금지선의 1차 방어선이다."""
    now = datetime(2026, 8, 26, 12, 47)
    states = [
        _FakeState("run_naver_wisdom", "45 8 * * *", datetime(2026, 8, 25, 8, 45)),
        _FakeState("run_naver_auto_operator_daily", "50 8 * * *", datetime(2026, 8, 25, 8, 50)),
        _FakeState("generate_naver_proposals", "0 8 * * *", datetime(2026, 8, 25, 8, 0)),
    ]
    db = _StateQueryDB(states)
    missed = scheduler_service._missed_morning_jobs(
        db, now, only=scheduler_service._INDAY_CATCHUP_JOBS,
        min_lag=scheduler_service._INDAY_CATCHUP_MIN_LAG,
    )
    assert missed == ["run_naver_wisdom"], f"돈 잡이 스윕 대상에 섞였다: {missed}"

    # 필터 없이 부르면 셋 다 결손으로 잡힌다 — 위 결과가 «필터 덕»임을 증명한다
    unfiltered = scheduler_service._missed_morning_jobs(
        _StateQueryDB(states), now, min_lag=scheduler_service._INDAY_CATCHUP_MIN_LAG
    )
    assert "run_naver_auto_operator_daily" in unfiltered
    assert "generate_naver_proposals" in unfiltered


def test_sweep_records_success_exactly_once_so_it_is_idempotent():
    """★P2-3: 성공 기록을 지우는 변이가 살아남았다. 이 줄이 없으면 catch-up 경로로는
    last_run_at이 절대 안 갱신돼 **매시간 무한 재실행**된다(docstring이 주장하는 멱등성 붕괴)."""
    recorded = []
    _patch = pytest.MonkeyPatch()
    _patch.setattr(scheduler_service, "_get_own_db_session", lambda: _FakeDB())
    _patch.setattr(scheduler_service, "_missed_morning_jobs", lambda *a, **k: ["run_naver_wisdom"])
    _patch.setattr(scheduler_service, "run_naver_wisdom_job", lambda: None)
    _patch.setattr(
        scheduler_service, "_record_catchup_status",
        lambda job_name, **kw: recorded.append((job_name, kw.get("ok"))),
    )
    _patch.setattr(scheduler_service.threading, "Thread", _sync_thread_factory())
    try:
        scheduler_service.run_naver_inday_catchup_job()
    finally:
        _patch.undo()
    assert recorded == [("run_naver_wisdom", True)], f"성공 기록이 정확히 1회가 아니다: {recorded}"
