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


def test_detect_finds_console_cuts_without_anyone_reporting_them(db, monkeypatch):
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


def test_detect_reports_groups_that_returned_nothing(db, monkeypatch):
    """★«0건»과 «못 읽음»이 같아 보이면 안 된다 — 쇼핑 제외의 되읽기 가능 여부가 아직
    미해결이라(2026-08-11 실측), 빈 응답 그룹 수를 세어 내보낸다."""
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [])
    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP, "grp-2"], now=NOW)
    assert out["groups_with_zero"] == 2 and out["recorded"] == []


def test_detect_survives_one_group_failing(db, monkeypatch):
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
