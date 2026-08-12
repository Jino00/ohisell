# test_search_term_execution_chain.py — 「사람이 실행한 제외」가 **학습 사슬까지 흐르는지** 지킨다
#   (D-NAO-173 P2, docs/PLAN_search-term-exclusion-list.md §4-a).
#
# ## 이 파일이 지키는 것
# 판정 산술이 아니라 **사슬**이다:
#   record_execution → ops_diary_entries(execute) → diary_outcome(D+1 기입)
#                    → wisdom_candidates(시그니처 수확) → 지혜 승격 후보
# 이 사슬 어디가 끊겨도 «자르는 것»은 계속 되고 아무 에러도 안 난다 — 다만 시스템이 아무것도
# 배우지 않을 뿐이다. 그 침묵을 막는 게 이 파일의 전부다.
#
# ★그래서 diary·wisdom 모듈을 **실제로 호출해** 단언한다(모킹 아님). 「우리가 diary를 부른다」만
#   확인하면, 인자 형태가 틀려 하류가 그 행을 못 줍는 상태를 통과시킨다.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel,
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverProductBep,
    NaverSearchTermDaily,
    NaverSearchTermExclusion,
    OpsDiaryEntry,
    OpsWisdomCandidate,
)
from app.services.naver_ad import search_term_execution as ste
from app.services.naver_ad import search_term_scorecard as scorecard

NOW = datetime(2026, 8, 11, 12, 0, 0)
CAMPAIGN = "cmp-09"
ADGROUP = "grp-buddy"
TERM = "골프"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Channel(id=6, name="네이버", code="naver", platform="naver"))
    session.commit()
    yield session
    session.close()


class _NoCloseSession:
    """close()만 무시하는 얇은 프록시.

    diary는 자기 세션을 열고 **반드시 닫는다**(_new_diary_session → finally: close). 인메모리
    테스트에서 같은 세션을 넘겨주면 그 close가 테스트 세션까지 닫아 이후 ORM 접근이 죽는다 —
    prod에서는 별개 세션이라 안 나는 현상이므로, 테스트 쪽에서 그 계약 차이를 흡수한다.
    """

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):  # diary의 finally가 부른다 — 테스트 세션은 살려 둔다
        pass


@pytest.fixture(autouse=True)
def _diary_uses_test_session(monkeypatch, db):
    """diary는 **독립 세션**(_new_diary_session)을 열어 쓴다 — 인메모리 테스트에선 그 세션이
    다른 DB를 보게 되므로 같은 세션을 쓰게 갈아끼운다. 이 fixture가 없으면 일기 행이
    «어딘가에» 쓰이고 테스트는 조용히 0건을 본다."""
    from app.services.naver_ad import diary

    monkeypatch.setattr(diary, "_new_diary_session", lambda _db: _NoCloseSession(db))


@pytest.fixture
def powerlink_groups(monkeypatch):
    """대조 대상 그룹을 **파워링크로 명시**한다.

    ★이 fixture가 왜 생겼나(D-NAO-174 후속): 테스트 환경엔 API 인증이 없어
      `get_adgroup_type`이 언제나 None(=모름)을 돌려준다. 종전 호출부 조건
      `not in (None, "WEB_SITE")`은 그 모름을 **대조 가능 쪽**에 넣었고, 그래서 아래 detect
      테스트들이 «유형을 한 번도 확인하지 않은 채» 초록이었다. 모름을 fail-closed로 바꾸자
      그 사실이 드러났다 — 테스트가 실제로 무엇에 의존하는지는 규율을 조일 때만 보인다.
    """
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "WEB_SITE")


def _spend(db, term=TERM, *, day: date, cost: int, clk: int = 5, conv: int = 0, amt: int = 0,
           adgroup_id=ADGROUP):
    db.add(NaverSearchTermDaily(
        ad_date=day, campaign_id=CAMPAIGN, adgroup_id=adgroup_id, search_term=term,
        source="shopping", imp=clk * 20, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=conv, conv_direct_cnt=conv, conv_purchase_amt=amt,
    ))


def _product(db, bep="1.49"):
    # target_roas도 채운다 — wisdom_candidates의 good/bad 판정이 campaign_target_resolver를
    # 거치고, 그 해석은 target_roas 컬럼에서만 나온다(bep_roas만 있으면 방향이 None → 후보 skip).
    db.add(NaverProductBep(
        channel_id=6, channel_product_id="p1", product_name="버디필름",
        selling_price=10000, cost_price=5000, commission_rate=0.05, logistics_cost=0,
        contribution_margin=3000, bep_roas=bep, target_roas=str(round(float(bep) * 1.15, 2)),
        has_cost=True,
    ))
    db.add(NaverAdgroupProduct(campaign_id=CAMPAIGN, adgroup_id=ADGROUP, mall_product_id="p1"))


# ═══ 등록층 — 원장 + 일기 ═══


def test_record_execution_creates_ledger_row_and_diary_entry(db):
    """★한 번의 등록이 «감시 대상»과 «학습 입력»을 동시에 만든다. 둘 중 하나만 생기면
    각각 다른 방식으로 조용히 실패한다(감시 없음 / 학습 없음)."""
    # 창은 today 포함 30일(2026-07-13~08-11) — 하루라도 밖에 두면 스냅샷이 그만큼 작아진다.
    for i in range(30):
        _spend(db, day=date(2026, 7, 13) + timedelta(days=i), cost=1_100)
    db.commit()

    out = ste.record_execution(
        db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
        rationale="30일 광고비 33,792원 · 전환 0건 → ROAS 0.00 < BEP 1.49", now=NOW,
    )

    assert out["result"] == "created" and out["diary"] is True
    row = db.query(NaverSearchTermExclusion).one()
    assert row.status == "excluded" and row.cycle == 1
    assert row.cost_at_exclusion == 33_000, "제외 시점 30일 비용이 감사 스냅샷으로 박혀야 한다"
    assert row.live_state is None, "아직 대조 전이다(alive로 미리 칠하지 않는다)"

    entry = db.query(OpsDiaryEntry).one()
    assert entry.event_type == "execute", "execute가 아니면 diary_outcome·wisdom이 줍지 않는다"
    assert entry.action == ste.DIARY_ACTION, "action은 학습 시그니처의 축이다"
    assert entry.actor == "console"
    assert entry.target_type == "search_term" and entry.target_id == TERM
    assert entry.campaign_id == CAMPAIGN and entry.adgroup_id == ADGROUP
    assert "ROAS 0.00" in entry.rationale, "왜 잘랐는지가 일기에 남아야 나중에 해석된다"


def test_recording_twice_does_not_double_count_the_lesson(db):
    """★멱등: 화면에서 두 번 눌렀다고 학습 표본이 두 배가 되면 승률이 조작된다."""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="근거", now=NOW)
    out2 = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                                rationale="근거", now=NOW)

    assert out2["result"] == "already_recorded" and out2["diary"] is False
    assert db.query(OpsDiaryEntry).count() == 1
    assert db.query(NaverSearchTermExclusion).count() == 1


def test_re_exclusion_bumps_cycle_and_resets_live_state(db):
    """재제외는 새 조치다 — cycle이 오르고 새 일기가 남고, 옛 생존 판정이 새 조치를 덮지 않는다."""
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="restored", cycle=1,
        excluded_at=datetime(2026, 6, 1), last_transition_at=datetime(2026, 7, 1),
        live_state="alive", live_checked_at=datetime(2026, 7, 1),
    ))
    _spend(db, day=date(2026, 8, 1), cost=7_000)
    db.commit()

    out = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                               rationale="다시 손해로 돌아섰다", now=NOW)
    row = db.query(NaverSearchTermExclusion).one()
    assert out["result"] == "re_excluded" and row.cycle == 2
    assert row.live_state is None and row.live_checked_at is None
    assert db.query(OpsDiaryEntry).count() == 1


def test_detect_finds_console_cuts_without_anyone_reporting_them(db, monkeypatch, powerlink_groups):
    """★보고에 의존하지 않는 경로 — 사람이 콘솔에서 자르고 화면 보고를 잊어도 등록된다.

    이 리포는 이미 «보고에 없던 변경»에 당했다(대행사 되돌림 2건 중 1건은 change_log에 행조차
    없었다). 상태 대조가 사건 보고보다 튼튼하다는 같은 원리를 여기에도 쓴다.
    """
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False},
        {"nccAdgroupRestrictKwdId": "rst-2", "keyword": "이미지워진것", "delFlag": True},
    ])

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    assert len(out["recorded"]) == 1, "delFlag 행은 등록하지 않는다"
    row = db.query(NaverSearchTermExclusion).one()
    assert row.search_term == TERM and row.restrict_kwd_id == "rst-1"
    assert db.query(OpsDiaryEntry).count() == 1
    assert "라이브 대조가 발견" in db.query(OpsDiaryEntry).one().rationale


def test_detect_reports_groups_that_returned_nothing(db, monkeypatch, powerlink_groups):
    """★«0건»과 «못 읽음»이 같아 보이면 안 된다 — 쇼핑 제외의 되읽기 가능 여부가 아직
    미해결이라(2026-08-11 실측), 빈 응답 그룹 수를 세어 내보낸다."""
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [])
    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP, "grp-2"], now=NOW)
    assert out["groups_with_zero"] == 2 and out["recorded"] == []


def test_detect_survives_one_group_failing(db, monkeypatch, powerlink_groups):
    from app.services.naver_ad import naver_sa_writer

    def fake(adgroup_id):
        if adgroup_id == "grp-dead":
            raise RuntimeError("boom")
        return [{"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False}]

    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", fake)
    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP, "grp-dead"], now=NOW)
    assert len(out["recorded"]) == 1 and len(out["errors"]) == 1


# ═══ 사슬층 — diary_outcome → wisdom_candidates ═══


def test_the_entry_is_actually_picked_up_by_outcome_and_wisdom(db):
    """★★이 테스트가 이 파일의 존재 이유다 — 우리 일기 행을 **하류가 실제로 줍는가**.

    「우리가 diary를 불렀다」만 확인하면 인자 형태가 틀려 하류가 그 행을 못 줍는 상태를
    통과시킨다. 그래서 diary_outcome과 wisdom_candidates를 진짜로 돌려 결과를 단언한다.
    """
    from app.services.naver_ad import diary_outcome, wisdom_candidates

    _product(db)
    # 캠페인 grain 실적(diary_outcome이 D+1 결과를 여기서 집계한다)
    for i in range(20):
        d = date(2026, 8, 1) + timedelta(days=i)
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, keyword_id="",
            imp=1000, clk=50, cost=20_000, conv_direct_cnt=2, conv_direct_amt=60_000,
            conv_indirect_cnt=0, conv_indirect_amt=0,
        ))
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()

    # ★실행 시각을 3일 전으로 둔다 — diary_outcome의 D+1 기입 조건이 `age >= 2`다(실행 다음날
    #   실적이 확정된 뒤에 본다). 어제 실행분은 «아직» 안 채워지는 게 정상이고, 그걸 모르고
    #   어제로 두면 이 테스트가 «사슬이 끊겼다»고 거짓 경보를 낸다.
    executed_at = NOW - timedelta(days=3)
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="ROAS 0.00 < BEP 1.49", now=executed_at)
    entry = db.query(OpsDiaryEntry).one()
    # created_at은 server_default(UTC)라 테스트에서 명시 조정한다(KST 변환 규칙과 짝).
    entry.created_at = executed_at - timedelta(hours=9)
    db.commit()

    filled = diary_outcome.backfill_outcomes(db, now=NOW)
    assert filled["d1_filled"] >= 1, "우리 일기 행에 D+1 결과가 안 붙었다 — 사슬 1단계가 끊겼다"
    outcome = json.loads(db.query(OpsDiaryEntry).one().outcome_json or "{}")
    assert outcome.get("d1"), "d1 결과가 없으면 wisdom이 이 행을 수확하지 않는다"

    harvested = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert harvested["new"] + harvested["updated"] >= 1, f"지혜 후보로 수확되지 않았다: {harvested}"
    cand = db.query(OpsWisdomCandidate).first()
    assert cand is not None
    assert ste.DIARY_ACTION in cand.signature, (
        "시그니처에 액션이 없으면 이 판단 종류의 승률이 따로 쌓이지 않는다"
    )


# ═══ 성적표층 ═══


def test_scorecard_says_stopped_when_the_term_actually_died(db):
    _product(db)
    ex_date = date(2026, 8, 1)
    for i in range(14):  # 실행 전 14일 — 매일 3,000원
        _spend(db, day=ex_date - timedelta(days=14 - i), cost=3_000)
    # 실행 후: 비용 0(행 자체가 없다)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=42_000,
    ))
    db.commit()

    out = scorecard.build_scorecard(db, now=NOW)
    item = out["items"][0]
    assert item["verdict"] == "stopped"
    assert item["before"]["cost_per_day"] == 3_000
    assert item["after"]["cost_per_day"] == 0
    assert item["profit_recovered"] == 3_000 * item["after_days"]
    assert out["profit_recovered_judged"] == item["profit_recovered"]


def test_scorecard_flags_a_cut_that_did_not_take(db):
    """★조치가 안 걸린 것을 조용히 «효과 미미»로 적지 않는다 — 그건 1급 결과다."""
    _product(db)
    ex_date = date(2026, 8, 1)
    for i in range(14):
        _spend(db, day=ex_date - timedelta(days=14 - i), cost=3_000)
    for i in range(1, 8):  # 실행 후에도 계속 쓴다
        _spend(db, day=ex_date + timedelta(days=i), cost=2_800)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=42_000,
    ))
    db.commit()

    item = scorecard.build_scorecard(db, now=NOW)["items"][0]
    assert item["verdict"] == "still_spending"
    assert "아직 쓰고 있다" in item["why"]


def test_scorecard_does_not_judge_before_maturity(db):
    """★「아직」과 「효과 없음」을 같은 칸에 넣으면 하루 뒤 스크린샷이 실패로 보인다."""
    _product(db)
    _spend(db, day=date(2026, 8, 9), cost=3_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 10, 10, 0, 0), last_transition_at=datetime(2026, 8, 10, 10, 0, 0),
        cost_at_exclusion=3_000,
    ))
    db.commit()

    out = scorecard.build_scorecard(db, now=NOW)
    item = out["items"][0]
    assert item["verdict"] == "pending"
    assert out["pending_count"] == 1 and out["profit_recovered_judged"] == 0


def test_recovered_profit_subtracts_the_margin_that_died_with_it(db):
    """★비용만 줄어든 것을 이익으로 읽지 않는다 — 같이 사라진 공헌이익을 뺀다.

    이게 없으면 「매출이 딸려 나간 컷」이 성과로 보이고, 볼륨 절멸이 성적표에서 초록이 된다.
    """
    _product(db, bep="2.0")
    ex_date = date(2026, 8, 1)
    for i in range(14):  # 전: 하루 3,000원 쓰고 4,000원 팔던 검색어
        _spend(db, day=ex_date - timedelta(days=14 - i), cost=3_000, conv=1, amt=4_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=42_000,
    ))
    db.commit()

    item = scorecard.build_scorecard(db, now=NOW)["items"][0]
    days = item["after_days"]
    # 회수 = 비용 3,000 − 공헌이익 손실(4,000/2.0=2,000) = 1,000원/일
    assert item["profit_recovered"] == 1_000 * days


def test_no_bep_means_no_recovered_profit_not_full_cost_saving(db):
    """★적대 리뷰 P1-2 회귀: BEP가 없으면 **회수액을 내지 않는다**(fail-closed).

    종전에는 `margin_lost = 0.0`으로 두고 **비용 절감 전액**을 회수액이라 신고했다. 그러면
    이 파일 위 테스트가 막겠다고 선언한 바로 그것 — 「매출이 딸려 나간 컷」이 초록으로 —
    이 BEP 없는 그룹에서만 조용히 일어난다. 같은 데이터인데 BEP 유무로 부호가 뒤집힌다.

    판정층은 이미 fail-closed다: `_campaign_window`는 `profit = None if not bep`이고,
    리스트 생성기는 BEP 없는 그룹을 `bep_unknown`으로 후보에서 뺀다. 채점층만 fail-open이면
    그 총계(`profit_recovered_judged`)가 다음 라운드 확대 판단의 근거로 부풀려진다.
    """
    ex_date = date(2026, 8, 1)
    for i in range(14):  # 전: 하루 3,000원 쓰고 40,000원 팔던 검색어(= 매출이 큰 컷)
        _spend(db, day=ex_date - timedelta(days=14 - i), cost=3_000, conv=1, amt=40_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=42_000,
    ))
    db.commit()  # ★_product()를 부르지 않는다 — 이 그룹엔 BEP가 없다

    out = scorecard.build_scorecard(db, now=NOW)
    item = out["items"][0]
    assert item["applied_bep"] is None
    assert item["verdict"] == "stopped", "판정 자체는 나온다 — 못 내는 건 회수액뿐이다"
    assert item["profit_recovered"] is None, (
        "BEP 없이 비용 절감 전액을 이익이라 신고하면 매출이 죽은 컷이 성과가 된다"
    )
    assert "BEP" in item["why"], "왜 회수액이 없는지 그 행이 스스로 밝혀야 한다"
    assert out["profit_recovered_judged"] == 0
    assert out["profit_unknown_count"] == 1, (
        "합계가 0원인 이유가 «회수액이 적다»인지 «못 잰다»인지 화면이 구분할 수 있어야 한다"
    )


def test_scorecard_carries_the_campaign_side_for_volume_collapse(db):
    """부작용 축 — 캠페인 전환매출이 같이 죽었는지 같은 표에서 보여야 한다."""
    _product(db)
    ex_date = date(2026, 8, 1)
    for i in range(20):
        d = ex_date - timedelta(days=14) + timedelta(days=i)
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, keyword_id="",
            imp=1000, clk=50, cost=20_000, conv_direct_cnt=2, conv_direct_amt=60_000,
            conv_indirect_cnt=0, conv_indirect_amt=0,
        ))
    _spend(db, day=ex_date - timedelta(days=1), cost=3_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=3_000,
    ))
    db.commit()

    item = scorecard.build_scorecard(db, now=NOW)["items"][0]
    assert item["campaign"]["before"]["conv_amt_per_day"] > 0
    assert item["campaign"]["after"]["conv_amt_per_day"] > 0
    assert item["campaign"]["before"]["profit_contrib"] is not None


def test_routes_are_wired(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        r = client.post("/api/naver/ad/search-term/executions", json={
            "campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM,
            "rationale": "ROAS 0.00 < BEP 1.49",
        })
        assert r.status_code == 200 and r.json()["result"] == "created"
        assert r.json()["diary"] is True, "라우터 경유로도 일기가 남아야 학습에 잡힌다"

        s = client.get("/api/naver/ad/search-term/exclusion-scorecard")
        assert s.status_code == 200
        assert s.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


# ═══ 장부 입구 — 검증 · 무효화 (D-NAO-174 이월 P2 상환) ═══


def test_recovered_profit_never_credits_revenue_we_did_not_create(db):
    """★회수액이 «비용 절감분»을 넘어설 수 없다 — 사후 매출 증가는 우리 성과가 아니다.

    종전 식은 `margin_lost = (before_amt - after_amt)/bep * days`를 그대로 뺐다. 사후 매출이
    사전보다 크면 이 값이 음수가 되고 `saved = cost_saved - margin_lost`가 **비용 절감분보다
    커진다.** 아래 표본은 광고비를 **한 푼도 못 줄인**(still_spending) 컷인데도 종전 식이면
    회수액이 플러스로 나온다 — 즉 「제외가 안 걸렸다」가 성과로 집계된다.

    ⚠️이건 가설이 아니다: prod의 「골프」(exclusion_id=2)는 사전 매출이 0원이라 이 식이
      **구조적으로 음수만 낼 수 있는** 행이고, 2026-08-17이 그 첫 판정일이다.
    """
    _product(db, bep="2.0")
    ex_date = date(2026, 8, 1)
    for i in range(14):  # 전: 하루 3,000원 쓰고 매출 0원
        _spend(db, day=ex_date - timedelta(days=14 - i), cost=3_000, conv=0, amt=0)
    for i in range(1, 8):  # 후: 여전히 3,000원 쓰는데 매출이 5,000원 붙었다
        _spend(db, day=ex_date + timedelta(days=i), cost=3_000, conv=1, amt=5_000)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=datetime(2026, 8, 1, 10, 0, 0), last_transition_at=datetime(2026, 8, 1, 10, 0, 0),
        cost_at_exclusion=42_000,
    ))
    db.commit()

    item = scorecard.build_scorecard(db, now=NOW)["items"][0]
    assert item["verdict"] == "still_spending", "표본 전제 — 광고비가 안 줄었다"
    assert item["profit_recovered"] == 0, (
        "비용을 한 푼도 못 줄였는데 회수액이 0이 아니면, 우리가 안 만든 매출을 성과로 센 것이다"
    )
    assert "0으로 끊었다" in item["why"], "조용히 보정하지 않는다 — 왜 깎았는지 그 행이 밝힌다"


def test_blank_campaign_id_is_refused_at_the_ledger_door(db):
    """★빈 값은 장부에 못 들어온다 — 들어오면 지울 방법이 없던 자리다.

    빈 campaign_id로 저장되면 그 행의 학습 시그니처가 `|search_term_exclude|…`로 시작해
    캠페인 축을 잃고, 원장·일기 어디서도 어느 캠페인의 조치였는지 복원할 수 없다.
    """
    for field, kwargs in [
        ("campaign_id", {"campaign_id": "", "adgroup_id": ADGROUP, "search_term": TERM}),
        ("adgroup_id", {"campaign_id": CAMPAIGN, "adgroup_id": "  ", "search_term": TERM}),
        ("search_term", {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": ""}),
    ]:
        with pytest.raises(ste.ExclusionInputError, match=field):
            ste.record_execution(db, rationale="근거", now=NOW, **kwargs)

    with pytest.raises(ste.ExclusionInputError, match="rationale"):
        ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                             rationale="   ", now=NOW)

    assert db.query(NaverSearchTermExclusion).count() == 0, "거부는 «저장 후 에러»가 아니어야 한다"
    assert db.query(OpsDiaryEntry).count() == 0, "거부된 입력이 학습 사슬에 남으면 안 된다"


def test_detect_refuses_to_write_a_row_it_cannot_attribute(db, monkeypatch, powerlink_groups):
    """★캠페인을 못 붙이면 등록하지 않되 **못 적었다는 사실은 남긴다**.

    종전엔 `camp_of.get(id, "")`로 빈 campaign_id를 넣었다. 그렇다고 조회 전에 그룹째 건너뛰면
    「거기 제외가 있는데 우리가 모른다」가 통째로 안 보인다 — 침묵으로 바꾸는 건 수리가 아니다.
    """
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False},
    ])
    # 검색어 성과 테이블에 없는 그룹 → camp_of에 매핑이 없다.
    out = ste.detect_new_exclusions(db, adgroup_ids=["grp-보지못한것"], now=NOW)

    assert out["unattributable_count"] == 1
    assert out["unattributable"][0]["search_term"] == TERM, "무엇을 못 적었는지까지 나와야 한다"
    assert out["recorded"] == []
    assert db.query(NaverSearchTermExclusion).count() == 0, "빈 campaign_id 행이 생기면 안 된다"
    assert db.query(OpsDiaryEntry).count() == 0


def test_detect_counts_unknown_adgroup_type_apart_from_zero(db, monkeypatch):
    """★«유형을 모른다»를 «제외 0건»으로 세지 않는다(fail-closed).

    `get_adgroup_type`은 조회 실패도 None으로 돌려준다 — 그 docstring이 *«모름»이지 «WEB_SITE가
    아님»이 아니다*라고 직접 적어 뒀다. 종전 조건 `not in (None, "WEB_SITE")`은 그 모름을
    **대조 가능 쪽**에 넣었고, 그러면 쇼핑 그룹이 유형 조회 500 한 번으로 restricted-keywords를
    타고 그 API는 쇼핑에서 200/0건이라 «제외 0건»으로 조용히 세어진다.
    `exclusion_survival.py`가 정확히 같은 결함으로 P1을 맞았다(D-NAO-174).
    """
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: None)

    def _must_not_be_called(adgroup_id):
        raise AssertionError("유형을 모르는 그룹에 restricted-keywords를 물으면 안 된다")

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", _must_not_be_called)

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP, "grp-2"], now=NOW)

    assert out["type_unknown_groups"] == 2
    assert out["groups_with_zero"] == 0, "«못 봤다»가 «0건»에 섞이면 안 된다"
    assert out["unverifiable_groups"] == 0, "쇼핑(=대조 불가)과 조회 실패(=모름)도 서로 다르다"


def test_void_takes_the_row_out_of_every_consumer_and_neutralizes_the_diary(db):
    """★무효화는 «보이는 곳»만이 아니라 **학습 사슬에서도** 뺀다.

    장부 행만 지우고 일기를 두면 성적표·배너에선 사라졌는데 diary_outcome → wisdom은 계속
    그 조치를 먹는다 — 「보이는 곳에서만 지운다」가 이 리포의 반복 실패 유형이다.
    """
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    out = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                               rationale="오타로 잘못 등록했다", now=NOW)
    exclusion_id = out["exclusion_id"]
    assert scorecard.build_scorecard(db, now=NOW)["total"] == 1, "무효화 전엔 성적표에 있다"

    voided = ste.void_execution(db, exclusion_id=exclusion_id, reason="검색어를 잘못 적었다", now=NOW)

    assert voided["result"] == "voided" and voided["diary_voided"] == 1
    row = db.get(NaverSearchTermExclusion, exclusion_id)
    assert row is not None, "행 자체는 감사용으로 남는다(하드 삭제 아님)"
    assert row.status == ste.VOID_STATUS and row.live_state is None
    assert "잘못 적었다" in row.live_note, "왜 지웠는지가 행에 남아야 감사 가능하다"

    assert scorecard.build_scorecard(db, now=NOW)["total"] == 0, "성적표에서 빠져야 한다"
    entry = db.query(OpsDiaryEntry).one()
    assert entry.event_type == ste.VOIDED_EVENT_TYPE
    from app.services.naver_ad.diary_outcome import EVENT_TYPES

    assert entry.event_type not in EVENT_TYPES, (
        "일기가 소급 대상으로 남으면 무효화한 조치가 계속 학습된다"
    )
    assert "[무효화:" in entry.rationale


def test_void_is_idempotent_and_admits_what_it_cannot_undo(db):
    """★이미 학습에 반영된 몫은 못 되돌린다 — 숨기지 않고 표면화한다(조용한 부분 실패 금지)."""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    out = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                               rationale="근거", now=NOW)
    entry = db.query(OpsDiaryEntry).one()
    entry.outcome_json = json.dumps({"d1": {"cost": 100, "clk": 1, "conv": 0, "roas_c": 0.0}})
    db.commit()

    first = ste.void_execution(db, exclusion_id=out["exclusion_id"], reason="사유", now=NOW)
    assert first["wisdom_may_have_counted"] is True, (
        "outcome이 이미 채워진 행은 승률에 셈이 들어갔을 수 있다 — 그 사실을 말해야 한다"
    )

    second = ste.void_execution(db, exclusion_id=out["exclusion_id"], reason="사유", now=NOW)
    assert second["result"] == "already_void" and second["diary_voided"] == 0, "두 번 눌러도 같다"


def test_reviving_a_voided_row_does_not_inherit_its_cycle(db):
    """무효화는 «일어나지 않은 조치»다 — 그 횟수가 재심사 백오프(30일×cycle)를 늘리면 안 된다."""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    out = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                               rationale="근거", now=NOW)
    ste.void_execution(db, exclusion_id=out["exclusion_id"], reason="오등록", now=NOW)

    again = ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                                 rationale="이번엔 진짜로 잘랐다", now=NOW)
    row = db.get(NaverSearchTermExclusion, again["exclusion_id"])
    assert row.status == "excluded" and row.cycle == 1, "재제외가 아니라 첫 등록으로 센다"


def test_ledger_door_routes_are_wired(db):
    """라우터 경유 — 빈 값은 422, 무효화는 200. SA만 고치고 라우터를 안 잇는 실패를 막는다."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        bad = client.post("/api/naver/ad/search-term/executions", json={
            "campaign_id": "", "adgroup_id": ADGROUP, "search_term": TERM, "rationale": "근거",
        })
        assert bad.status_code == 422, "빈 campaign_id가 200으로 저장되면 안 된다"

        ok = client.post("/api/naver/ad/search-term/executions", json={
            "campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM,
            "rationale": "ROAS 0.00 < BEP 1.49",
        })
        assert ok.status_code == 200
        eid = ok.json()["exclusion_id"]

        no_reason = client.request(
            "DELETE", f"/api/naver/ad/search-term/executions/{eid}", json={"reason": ""},
        )
        assert no_reason.status_code == 422, "사유 없는 삭제는 감사 불가다"

        gone = client.request(
            "DELETE", f"/api/naver/ad/search-term/executions/{eid}", json={"reason": "오등록"},
        )
        assert gone.status_code == 200 and gone.json()["result"] == "voided"

        missing = client.request(
            "DELETE", "/api/naver/ad/search-term/executions/99999", json={"reason": "없는 행"},
        )
        assert missing.status_code == 422

        listed = client.get("/api/naver/ad/search-term/exclusions?status=void")
        assert listed.status_code == 200 and listed.json()["total"] == 1, (
            "무효화한 행이 어디로 갔는지 조회할 수 있어야 한다(사후 가시성)"
        )
    finally:
        app.dependency_overrides.clear()


def _harness_shaped_diary(db, *, term=TERM, adgroup_id=ADGROUP, created_at=None):
    """SS레인/하네스가 쓰는 모양의 일기 1행.

    `naver_execution_harness._ACTION_BY_PROPOSAL_TYPE[SEARCH_TERM_EXCLUDE_TYPE]`이
    `exclude_search_term`이고 target_id는 proposal.target_id(전문)다 — record_execution이
    쓰는 모양(`search_term_exclude` + [:50])과 **다르다**.
    """
    e = OpsDiaryEntry(
        event_type="execute", campaign_id=CAMPAIGN, actor="console",
        target_type="search_term", target_id=term, adgroup_id=adgroup_id,
        action="exclude_search_term", rationale="SS레인 자동 집행",
    )
    db.add(e)
    db.flush()
    if created_at is not None:
        e.created_at = created_at
    db.commit()
    return e


def test_void_also_finds_the_diary_the_harness_wrote(db):
    """★이 원장에는 쓰기 주체가 둘이고 둘이 일기를 **다른 모양**으로 쓴다.

    record_execution 모양만 보면 하네스 산 행을 무효화할 때 일기가 `execute`로 살아남아
    diary_outcome → wisdom이 계속 그 조치를 먹는다. prod exclusion_id=1(아이패드종이필름)이
    실제로 하네스 산 행이라 이건 가설이 아니다.
    """
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=1_000,
    ))
    db.commit()
    row = db.query(NaverSearchTermExclusion).one()
    _harness_shaped_diary(db)

    out = ste.void_execution(db, exclusion_id=row.id, reason="오등록", now=NOW)

    assert out["diary_voided"] == 1, "하네스 모양 일기를 못 찾으면 학습이 계속 먹는다"
    entry = db.query(OpsDiaryEntry).one()
    assert entry.event_type == ste.VOIDED_EVENT_TYPE


def test_void_says_it_could_not_check_instead_of_asserting_a_negative(db):
    """★«일기가 없다»와 «일기를 못 찾았다»는 같은 0이다 — False로 단언하지 않는다(교훈 #123)."""
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=1,
        excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=1_000,
    ))
    db.commit()
    row = db.query(NaverSearchTermExclusion).one()

    out = ste.void_execution(db, exclusion_id=row.id, reason="오등록", now=NOW)

    assert out["diary_voided"] == 0
    assert out["wisdom_may_have_counted"] is None, (
        "확인하지 못한 것을 False로 내보내면 «확인 안 함»이 «안 셌음»으로 둔갑한다"
    )
    assert "확인하지 못했다" in out["diary_note"]


def test_void_does_not_kill_the_lesson_from_an_earlier_cycle(db):
    """★1주기의 «정당했던» 학습 표본이 2주기 오등록 때문에 같이 죽으면 안 된다."""
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="excluded", cycle=2,
        excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=1_000,
    ))
    db.commit()
    row = db.query(NaverSearchTermExclusion).one()
    old = _harness_shaped_diary(db, created_at=datetime(2026, 6, 1, 0, 0, 0))  # 1주기(두 달 전)
    _harness_shaped_diary(db, created_at=NOW - timedelta(hours=9))            # 이번 주기

    out = ste.void_execution(db, exclusion_id=row.id, reason="2주기가 오등록", now=NOW)

    assert out["diary_voided"] == 1, "이번 주기 일기만 중화한다"
    assert db.get(OpsDiaryEntry, old.id).event_type == "execute", "옛 주기의 표본은 살아 있어야 한다"


def test_void_refuses_when_two_terms_share_the_truncated_key(db):
    """★target_id는 [:50]으로 잘려 저장된다 — 앞 50자가 같으면 남의 일기를 죽이느니 멈춘다.

    죽여 버리면 다른 행은 excluded로 멀쩡히 남아 **화면 어디에도 이상이 안 보인다.**
    """
    long_a = "가" * 50 + "에이"
    long_b = "가" * 50 + "비"
    for term in (long_a, long_b):
        db.add(NaverSearchTermExclusion(
            campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=term, status="excluded", cycle=1,
            excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=1_000,
        ))
    db.commit()
    _harness_shaped_diary(db, term=long_b, created_at=NOW - timedelta(hours=9))
    row_a = db.query(NaverSearchTermExclusion).filter_by(search_term=long_a).one()

    out = ste.void_execution(db, exclusion_id=row_a.id, reason="A만 오등록", now=NOW)

    assert out["diary_voided"] == 0 and out["wisdom_may_have_counted"] is None
    assert "구분할 수 없다" in out["diary_note"]
    assert db.query(OpsDiaryEntry).one().event_type == "execute", "B의 일기는 살아 있어야 한다"


def test_post_route_translates_whitespace_only_input_to_422(db):
    """리뷰어 M9 SURVIVED 상환 — Pydantic이 못 거르는 «공백만»이 SA까지 가서 500이 되면 안 된다."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).post("/api/naver/ad/search-term/executions", json={
            "campaign_id": "   ", "adgroup_id": ADGROUP, "search_term": TERM, "rationale": "근거",
        })
        assert r.status_code == 422, "SA의 거부가 500이 아니라 422로 나와야 한다"
        assert "campaign_id" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_over_long_values_are_refused_before_the_database_truncates_them(db):
    """리뷰어 M13 SURVIVED 상환 — 길이 상한이 없으면 DB가 조용히 자르고 원장·일기가 갈린다."""
    with pytest.raises(ste.ExclusionInputError, match="너무 길다"):
        ste.record_execution(db, campaign_id="c" * 51, adgroup_id=ADGROUP, search_term=TERM,
                             rationale="근거", now=NOW)
    with pytest.raises(ste.ExclusionInputError, match="너무 길다"):
        ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term="t" * 301,
                             rationale="근거", now=NOW)
    assert db.query(NaverSearchTermExclusion).count() == 0


def test_void_finds_the_untruncated_target_id_the_harness_stored(db):
    """★두 쓰기 주체는 target_id 길이도 다르다 — 하네스는 **자르지 않고** 넣는다.

    `record_execution`은 `search_term[:50]`으로 잘라 쓰지만 하네스는 `proposal.target_id`를
    그대로 넘긴다. 컬럼이 String(50)이어도 SQLite는 길이를 강제하지 않아 전문이 그대로 남는다.
    절단본만 대조하면 50자를 넘는 검색어에서 하네스 산 일기를 통째로 못 찾는다 —
    「골프」처럼 짧은 표본만 쓰면 두 값이 같아 이 구멍이 안 보인다.
    """
    long_term = ("무선충전기 고속 15W 맥세이프 호환 거치대 겸용 아이폰 갤럭시 공용 정품 "
                 "차량용 무선 급속 충전 거치대 자석형")
    assert len(long_term) > 50, "표본 전제 — 절단이 실제로 일어나는 길이여야 한다"
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=long_term, status="excluded",
        cycle=1, excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=1_000,
    ))
    db.commit()
    row = db.query(NaverSearchTermExclusion).one()
    _harness_shaped_diary(db, term=long_term, created_at=NOW - timedelta(hours=9))

    out = ste.void_execution(db, exclusion_id=row.id, reason="오등록", now=NOW)

    assert out["diary_voided"] == 1, "전문 target_id를 안 보면 긴 검색어의 일기를 영영 못 찾는다"
    assert db.query(OpsDiaryEntry).one().event_type == ste.VOIDED_EVENT_TYPE
