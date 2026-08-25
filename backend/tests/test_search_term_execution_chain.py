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
    """★보고에 의존하지 않는 경로 — 사람이 콘솔에서 자르고 화면 보고를 잊어도 편입된다.

    이 리포는 이미 «보고에 없던 변경»에 당했다(대행사 되돌림 2건 중 1건은 change_log에 행조차
    없었다). 상태 대조가 사건 보고보다 튼튼하다는 같은 원리를 여기에도 쓴다.

    ★D-NAO-179로 **문이 바뀌었다**: 편입은 `source='console_import'`(일기 없음)로 간다.
      여기 걸리는 행은 정의상 «우리가 한 조치가 아니다».
    """
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False,
         "type": "EXP_SEARCH", "regTm": "2026-07-14T03:59:54.000Z"},
        {"nccAdgroupRestrictKwdId": "rst-2", "keyword": "이미지워진것", "delFlag": True},
    ])

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    assert len(out["recorded"]) == 1, "delFlag 행은 편입하지 않는다"
    assert out["imported"] == 1
    row = db.query(NaverSearchTermExclusion).one()
    assert row.search_term == TERM and row.restrict_kwd_id == "rst-1"
    assert row.source == ste.CONSOLE_IMPORT_SOURCE


def test_detect_never_writes_a_diary_entry(db, monkeypatch, powerlink_groups):
    """★남이 과거에 자른 것을 「오늘 우리가 한 조치」로 학습 사슬에 태우지 않는다 (D-NAO-179).

    종전 경로(`record_execution(discovered=True)`)는 원장과 함께 diary `execute` 행을 만들었다.
    읽기가 두 타입 union으로 넓어지면(EXP_SEARCH 711건) 그 문으로 수백 건이 쏟아지고,
    D-NAO-176이 이미 적어 둔 그대로 **진짜 표본 1건이 쓰레기 수백 건에 익사한다.**
    """
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": f"rst-{i}", "keyword": f"남이자른것{i}", "delFlag": False,
         "type": "EXP_SEARCH", "regTm": "2025-04-30T05:43:10.000Z"}
        for i in range(20)
    ])

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    assert out["imported"] == 20
    assert db.query(OpsDiaryEntry).count() == 0, "편입은 일기를 만들지 않는다"


def test_detect_preserves_console_registration_time_from_reg_tm(db, monkeypatch, powerlink_groups):
    """★`regTm`이 곧 콘솔 등록시각이다 — 파워링크는 사람이 캡처하지 않아도 시각이 채워진다.

    D-NAO-176은 「시점 미상」을 전제로 이 칸을 만들었는데, API가 UTC로 그 값을 준다.
    KST naive로 정규화돼 들어가야 한다(2026-07-14T03:59:54Z = 2026-07-14 12:59:54 KST).
    """
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False,
         "type": "EXP_SEARCH", "regTm": "2026-07-14T03:59:54.000Z"},
    ])

    ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.console_excluded_at == datetime(2026, 7, 14, 12, 59, 54)
    # 편입 시각으로 메우지 않는다 — 두 칸은 뜻이 다르다(D-NAO-177)
    assert row.excluded_at == NOW


@pytest.mark.parametrize("reg_tm", [
    "언제인지모름",                 # 파싱 불가
    "2005-01-01T00:00:00.000Z",   # 연도 오타로 볼 만큼 과거(<2010)
    "2026-08-12T00:00:00.000Z",   # NOW(2026-08-11 12:00) 기준 미래
    "",                            # 값 없음
])
def test_detect_imports_even_when_reg_tm_is_unusable(db, monkeypatch, powerlink_groups, reg_tm):
    """★시각은 모를 수 있어도 «제외가 있다»는 사실은 반드시 들어온다 (적대 리뷰 1R P1).

    `console_excluded_at`은 보조 메타데이터인데, 그 한 칸의 이상값이 행 전체를 rejected로
    만들면 그 키워드는 **매 스윕마다 같은 이유로 거부되어 영구히 원장 밖**에 남는다.
    `regTm`은 API가 주는 고정값이라 사람이 고쳐 넣을 수도 없다.
    """
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    db.commit()
    from app.services.naver_ad import naver_sa_writer

    monkeypatch.setattr(naver_sa_writer, "get_restricted_keywords", lambda a: [
        {"nccAdgroupRestrictKwdId": "rst-1", "keyword": TERM, "delFlag": False,
         "type": "EXP_SEARCH", "regTm": reg_tm},
    ])

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    assert out["imported"] == 1 and out["rejected"] == 0
    row = db.query(NaverSearchTermExclusion).one()
    assert row.search_term == TERM
    # 모르는 것을 «지금»으로 메우지 않는다 — 아는 값과 구분되지 않으면 아무도 되짚을 수 없다
    assert row.console_excluded_at is None


def test_console_import_still_rejects_bad_time_from_a_human(db):
    """사람 입력 경로는 여전히 거부한다 — 오타를 조용히 삼키면 사람이 고칠 기회가 사라진다.

    같은 경계 규칙인데 처분이 다른 이유가 이것이다(입력원의 성격 차이). 두 테스트는 짝이다.
    """
    out = ste.import_console_exclusions(db, rows=[{
        "campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM,
        "console_excluded_at": "2005.01.01 00:00",
    }], now=NOW)

    assert out["rejected"] == 1 and out["imported"] == 0
    assert "너무 과거" in out["results"][0]["reason"]


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


def test_the_entry_is_actually_picked_up_by_outcome_grains(db):
    """★★이 테스트가 이 파일의 존재 이유다 — 우리 일기 행을 **하류가 실제로 줍는가**.

    「우리가 diary를 불렀다」만 확인하면 인자 형태가 틀려 하류가 그 행을 못 줍는 상태를
    통과시킨다. 그래서 diary_outcome을 진짜로 돌려 결과를 단언한다.

    ★D-NAO-178로 사슬의 종착지가 바뀌었다: 이 행의 `d1`은 `_grain_and_target`의 campaign
    폴백이라 **그 캠페인 전체의 하루 성과**이지 이 검색어의 성적이 아니다(라이브 diary 4371:
    d1 43,084원 vs 「골프」 30일 누적 31,411원). 그래서 ①검색어 grain 결과는 `d1_st`가 담고
    ②wisdom 수확은 **막는다** — 거짓 승률이 쌓이던 경로였다. 아래 짝 테스트가 ②를 지킨다.
    """
    from app.services.naver_ad import diary_outcome

    _product(db)
    # 캠페인 grain 실적(diary_outcome이 d1을 여기서 집계한다 — 이 값은 배경 성과다)
    for i in range(20):
        d = date(2026, 8, 1) + timedelta(days=i)
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, keyword_id="",
            imp=1000, clk=50, cost=20_000, conv_direct_cnt=2, conv_direct_amt=60_000,
            conv_indirect_cnt=0, conv_indirect_amt=0,
        ))
    _spend(db, day=date(2026, 8, 5), cost=9_000)      # 제외 전 기왕력 → 필요 source=shopping
    _spend(db, term="다른검색어", day=date(2026, 8, 8), cost=12_000)  # d1일 보고서 실재
    db.commit()

    # ★실행 시각을 4일 전으로 둔다 — d1/d1_st 기입 문턱이 `age >= 4`다(D-NAO-178: 원료가 3일
    #   창으로 delete+insert 재수집되므로 창이 닫힌 뒤에만 채점한다). 어제·그제 실행분은
    #   «아직» 안 채워지는 게 정상이고, 그걸 모르고 앞당기면 «사슬이 끊겼다»는 거짓 경보가 난다.
    executed_at = NOW - timedelta(days=4)
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="ROAS 0.00 < BEP 1.49", now=executed_at)
    entry = db.query(OpsDiaryEntry).one()
    # created_at은 server_default(UTC)라 테스트에서 명시 조정한다(KST 변환 규칙과 짝).
    entry.created_at = executed_at - timedelta(hours=9)
    db.commit()

    filled = diary_outcome.backfill_outcomes(db, now=NOW)
    assert filled["d1_filled"] >= 1, "우리 일기 행에 D+1 결과가 안 붙었다 — 사슬 1단계가 끊겼다"
    assert filled["d1_st_filled"] >= 1, "검색어 grain 결과(d1_st)가 안 붙었다 — 조치의 성적이 없다"

    outcome = json.loads(db.query(OpsDiaryEntry).one().outcome_json or "{}")
    st = outcome["d1_st"]
    assert st["status"] == "stopped", f"제외 다음날 그 검색어 비용이 0인데 stopped가 아니다: {st}"
    assert st["cost_total"] == 0
    assert st["required_sources"] == ["shopping"]
    assert st["by_source"]["shopping"]["present"] is True, "보고서 실재 게이트가 안 걸렸다"
    # 두 값이 **다른 숫자**라는 사실 자체가 오귀속 제거의 증거다.
    assert outcome["d1"]["cost"] != st["cost_total"]


def test_the_entry_is_harvested_via_d1_st_not_the_campaign_fallback(db):
    """★위 테스트의 짝 — S8(D-NAO-178 해제, 2026-08-25) 집행 후: 검색어 제외 행은 이제
    wisdom이 **줍는다**, 단 d1(캠페인 grain, 배경 성과)이 아니라 d1_st.status로만 판정한다.

    이 픽스처의 캠페인 grain d1은 cost 20,000원/일(good으로 읽힐 값)이지만, 검색어 자체는
    제외 다음날 비용 0(d1_st.status=='stopped')이다. 수확된 candidate가 good이라는 사실은
    ①d1_st가 정말 소비됐고 ②그 판정이 d1과 별개 경로임을 함께 보인다 — d1을 먹었다면
    good/bad 자체는 우연히 같아도 값(43,084원 vs 0원)의 출처가 달라 signature·observation의
    campaign_type/action 축은 같더라도 이 테스트가 지키는 것은 「수확이 됐다」+「good이다」다.
    """
    from app.services.naver_ad import diary_outcome, wisdom_candidates

    _product(db)
    for i in range(20):
        d = date(2026, 8, 1) + timedelta(days=i)
        db.add(NaverAdDaily(
            ad_date=d, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, keyword_id="",
            imp=1000, clk=50, cost=20_000, conv_direct_cnt=2, conv_direct_amt=60_000,
            conv_indirect_cnt=0, conv_indirect_amt=0,
        ))
    _spend(db, day=date(2026, 8, 5), cost=9_000)
    _spend(db, term="다른검색어", day=date(2026, 8, 8), cost=12_000)
    db.commit()

    executed_at = NOW - timedelta(days=4)
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="ROAS 0.00 < BEP 1.49", now=executed_at)
    entry = db.query(OpsDiaryEntry).one()
    entry.created_at = executed_at - timedelta(hours=9)
    db.commit()
    filled = diary_outcome.backfill_outcomes(db, now=NOW)
    outcome = json.loads(db.query(OpsDiaryEntry).one().outcome_json or "{}")
    assert outcome["d1_st"]["status"] == "stopped"  # 이 테스트의 전제(위 테스트와 동일 픽스처)

    harvested = wisdom_candidates.harvest_candidates(db, now=NOW)

    assert harvested["search_term_good"] == 1, f"good tally가 안 올랐다: {harvested}"
    assert harvested["new"] == 1
    cand = db.query(OpsWisdomCandidate).one()
    assert cand.good_count == 1 and cand.bad_count == 0


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
    assert out["unverifiable_groups"] == 0, "읽을 소스를 모르는 유형과 조회 실패도 서로 다르다"


def test_detect_finds_shopping_exclusions_without_a_console_capture(db, monkeypatch):
    """★★D-NAO-180: 쇼핑 제외가 **자동 발견에 들어온다** — 사람의 콘솔 캡처 없이.

    종전엔 `adgroup_type != "WEB_SITE"`에서 통째로 건너뛰었고, 그 근거는 「쇼핑은
    `restricted-keywords`가 200/0건」이었다. 관측은 맞았지만 결론이 과잉 일반화였다 — 거짓말한
    것은 그 **리소스**였지 쇼핑 제외가 아니다. `/ncc/targets`로는 3,880건이 읽힌다.
    그 결과 S5(그룹마다 화면 3장 캡처)의 존재 이유가 사라진다.

    ★그래도 **일기는 0건이어야 한다.** 여기 걸리는 행은 정의상 「남이 이미 해 둔 것」이지
      「오늘 우리가 한 조치」가 아니다 — 그걸 학습 사슬에 태우면 남의 조치가 우리 승률이 된다.
    """
    from app.services.naver_ad import naver_sa_writer

    _spend(db, day=NOW.date() - timedelta(days=1), cost=1000)  # camp_of 매핑을 만든다
    db.commit()

    monkeypatch.setattr(naver_sa_writer, "get_adgroup_type", lambda a: "SHOPPING")
    monkeypatch.setattr(
        naver_sa_writer, "get_restricted_keywords",
        lambda a: (_ for _ in ()).throw(AssertionError("쇼핑에 이 리소스를 쓰면 안 된다")),
    )
    monkeypatch.setattr(naver_sa_writer, "get_shopping_exclusions", lambda a: [
        # `date`(epoch)가 UTC ISO로 정규화돼 온다 — 2026-08-10T05:14:56Z → KST 14:14:56
        {"keyword": TERM, "type": 1, "delFlag": False, "nccAdgroupRestrictKwdId": None,
         "nccTargetId": "tgt-1", "regTm": "2026-08-10T05:14:56.000Z"},
    ])

    out = ste.detect_new_exclusions(db, adgroup_ids=[ADGROUP], now=NOW)

    assert out["unverifiable_groups"] == 0, "★회귀 감시: 쇼핑이 다시 건너뛰기로 접히면 안 된다"
    assert out["imported"] == 1

    row = db.query(NaverSearchTermExclusion).one()
    assert row.search_term == TERM and row.status == "excluded"
    assert row.source == "console_import", "남이 해 둔 것은 우리 조치와 다른 문으로 들어온다"
    assert row.restrict_kwd_id is None, "그룹 단위 id를 키워드 id로 저장하면 개방이 엉뚱해진다"
    # ★캡처 없이 시각까지 채워진다 — D-NAO-176이 「시점 미상」을 전제로 만들었던 칸이다.
    assert row.console_excluded_at == datetime(2026, 8, 10, 14, 14, 56)

    assert db.query(OpsDiaryEntry).count() == 0, "★편입은 학습 사슬에 태우지 않는다"


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


# ═══ 콘솔 일괄 편입 — 장부에는 넣되 학습에는 안 넣는다 (D-NAO-176) ═══


def test_console_import_writes_the_ledger_but_never_the_diary(db):
    """★이 계약의 1번 금지선. 편입은 **시점을 모르는 과거 조치**라 학습 입력이 되면 안 된다.

    `record_execution`을 재사용하면 「오늘 실행된 조치 N건」이라는 거짓 일기가 생기고, 각각
    D+1 소급 대상이 되어 **13일 만의 진짜 표본 1건(「골프」)이 그 안에 익사한다.**
    그래서 검증(`_require`)만 공유하고 일기 경로는 공유하지 않는다.
    """
    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "골프장"},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "골프공"},
    ], now=NOW)

    assert out["imported"] == 2 and out["rejected"] == 0
    assert out["diary_written"] == 0
    assert db.query(OpsDiaryEntry).count() == 0, (
        "편입이 일기를 만들면 진짜 표본이 편입분에 익사한다 — 이 단언이 이 파일에서 제일 중요하다"
    )
    rows = db.query(NaverSearchTermExclusion).all()
    assert len(rows) == 2
    assert all(r.source == ste.CONSOLE_IMPORT_SOURCE for r in rows)
    assert all(r.status == "excluded" and r.next_review_at is None for r in rows), (
        "실행 시점을 모르니 재심사 예정일도 정할 수 없다 — 추정하지 않는다"
    )


def test_console_import_rejects_per_row_not_the_whole_batch(db):
    """★43건 중 1건이 오타라고 42건이 죽으면 사람이 전체를 다시 붙여넣는다."""
    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "정상1"},
        {"campaign_id": "", "adgroup_id": ADGROUP, "search_term": "캠페인없음"},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "정상2"},
    ], now=NOW)

    assert out["imported"] == 2 and out["rejected"] == 1
    bad = [r for r in out["results"] if r["result"] == "rejected"][0]
    assert bad["row"] == 1 and "campaign_id" in bad["reason"], "어느 행이 왜 거부됐는지 나와야 한다"
    assert db.query(NaverSearchTermExclusion).count() == 2


def test_console_import_does_not_relabel_a_row_we_executed_ourselves(db):
    """★우리가 실행한 행을 편입분으로 덮으면 그 행이 성적표에서 **조용히 사라진다.**"""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="우리가 잘랐다", now=NOW)

    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)

    assert out["skipped"] == 1 and out["imported"] == 0
    row = db.query(NaverSearchTermExclusion).one()
    assert row.source is None, "우리가 실행한 행의 출처가 편입으로 바뀌면 안 된다"
    assert db.query(OpsDiaryEntry).count() == 1, "기존 일기도 그대로여야 한다"


def test_scorecard_counts_imported_rows_instead_of_judging_or_hiding_them(db):
    """★편입분을 판정하면 거짓 판정이 나오고, 조용히 버리면 43건이 화면에서 증발한다."""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="우리가 잘랐다", now=NOW)
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "편입된것"},
    ], now=NOW)

    out = scorecard.build_scorecard(db, now=NOW)
    assert out["total"] == 1, "편입분은 판정 대상이 아니다"
    assert all("편입된것" != i["search_term"] for i in out["items"])
    assert out["imported_unjudgeable_count"] == 1, "판정 안 한 몫이 숫자로 나와야 한다"
    # ★설명문은 «왜 판정 안 하나»를 말한다. 종전 문구 「실행 시점을 모르므로」는 콘솔에
    #   등록시각이 있다는 사실이 확인되면서 거짓이 됐다(D-NAO-177 적대 리뷰 P1-1) —
    #   같은 응답이 dated>0을 내면서 「시점을 모른다」고 말하는 상태를 여기서 막는다.
    note = out["imported_unjudgeable_note"]
    assert "사전 검색어 데이터가 없어" in note
    assert "실행 시점을 모르므로" not in note, (
        "콘솔이 등록시각을 준다는 사실이 확인된 뒤로 이 문장은 거짓이다"
    )


def test_candidate_list_stops_recommending_what_we_already_cut(db):
    """★prod 실측(2026-08-12): 후보 63건 중 1건이 「골프」였다 — Jino가 8/11에 **직접 자른**
    바로 그 검색어가 30일 비용 31,411원을 달고 「자르세요」 목록에 다시 떠 있었다.

    창이 30일이라 방금 자른 것은 당분간 계속 뜬다. 43건을 편입하면 그만큼 늘어나고, 그러면
    이 목록은 「이미 한 일을 또 하라」고 말하는 목록이 된다. 조용히 빼지 않고 버킷으로 센다.
    """
    from app.services.naver_ad import search_term_exclusion_list as cl

    _product(db)
    for i in range(20):  # 손해 검색어 — 원래대로면 후보로 뜬다
        _spend(db, day=date(2026, 7, 20) + timedelta(days=i), cost=3_000, clk=10, conv=0, amt=0)
    db.commit()

    before = cl.build_exclusion_list(db, now=NOW)
    assert any(c["search_term"] == TERM for c in before["candidates"]), "표본 전제 — 원래 후보다"
    assert before["buckets"]["already_excluded"]["terms"] == 0

    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)

    after = cl.build_exclusion_list(db, now=NOW)
    assert all(c["search_term"] != TERM for c in after["candidates"]), "이미 자른 걸 또 추천하지 않는다"
    assert after["buckets"]["already_excluded"]["terms"] == 1, "조용히 빼지 않고 세어야 한다"
    assert after["buckets"]["already_excluded"]["cost"] > 0


def test_probation_terms_come_back_as_candidates(db):
    """★`probation`은 재심사로 **일부러 다시 연** 상태다 — 그건 후보가 맞다.
    `excluded`만 막지 않으면 상태기계의 in-out 생태계가 죽는다."""
    from app.services.naver_ad import search_term_exclusion_list as cl

    _product(db)
    for i in range(20):
        _spend(db, day=date(2026, 7, 20) + timedelta(days=i), cost=3_000, clk=10, conv=0, amt=0)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="probation", cycle=1,
        excluded_at=datetime(2026, 6, 1), last_transition_at=datetime(2026, 7, 1),
    ))
    db.commit()

    out = cl.build_exclusion_list(db, now=NOW)
    assert any(c["search_term"] == TERM for c in out["candidates"])
    assert out["buckets"]["already_excluded"]["terms"] == 0


def test_import_route_is_wired_and_caps_the_batch(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        r = client.post("/api/naver/ad/search-term/executions/import", json={"rows": [
            {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "라우터편입"},
        ]})
        assert r.status_code == 200
        assert r.json()["imported"] == 1 and r.json()["diary_written"] == 0
        assert db.query(OpsDiaryEntry).count() == 0, "라우터 경유로도 일기가 생기면 안 된다"

        empty = client.post("/api/naver/ad/search-term/executions/import", json={"rows": []})
        assert empty.status_code == 422

        too_many = client.post("/api/naver/ad/search-term/executions/import", json={"rows": [
            {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": f"t{i}"}
            for i in range(201)
        ]})
        assert too_many.status_code == 422, "한 번의 붙여넣기 실수가 원장을 덮으면 안 된다"
    finally:
        app.dependency_overrides.clear()


def test_console_import_of_an_existing_row_still_marks_it_as_imported(db):
    """★적대 리뷰 P2-1 상환 — 재편입 분기(restored/probation/void)에 테스트가 한 줄도 없어
    변이 3개가 살아남았다.

    그중 제일 나쁜 것: 이 분기에서 `source`가 안 붙으면 편입 행이 **가짜 excluded_at으로
    성적표에 판정당한다** — 계약이 금지한 바로 그 결과인데 아무 테스트도 안 겨눴다.
    """
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM, status="restored", cycle=2,
        excluded_at=datetime(2026, 6, 1), last_transition_at=datetime(2026, 7, 1),
        next_review_at=date(2026, 9, 1), cost_at_exclusion=99_999,
        live_state="alive", live_checked_at=datetime(2026, 7, 1),
    ))
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()

    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)

    assert out["imported"] == 1
    row = db.query(NaverSearchTermExclusion).one()
    assert row.status == "excluded"
    assert row.source == ste.CONSOLE_IMPORT_SOURCE, (
        "출처가 안 붙으면 이 행이 가짜 excluded_at으로 성적표에 판정당한다"
    )
    assert row.next_review_at is None, (
        "실행 시점을 모르니 재심사 예정일도 못 정한다 — 옛 값을 그대로 두면 그게 추정이 된다"
    )
    assert row.live_state is None and row.live_checked_at is None, "옛 대조 결과가 새 상태를 덮지 않는다"
    assert db.query(OpsDiaryEntry).count() == 0, "재편입도 일기를 만들지 않는다"
    assert scorecard.build_scorecard(db, now=NOW)["total"] == 0, "편입분은 판정 대상이 아니다"


def test_console_import_does_not_guess_the_cost_it_never_saw(db):
    """★`cost_at_exclusion`은 «제외 시점의 30일 비용» 스냅샷이다. 편입분은 그 시점을 모르므로
    지금 시점의 비용을 넣으면 그건 추정이고, 나중에 성적표가 그 값을 근거로 되짚는다."""
    for i in range(20):
        _spend(db, day=date(2026, 7, 25) + timedelta(days=i), cost=4_000)
    db.commit()

    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.cost_at_exclusion == 0, "안 본 값을 지금 값으로 메우지 않는다(추정 금지)"


def test_voiding_an_imported_row_can_prove_no_learning_happened(db):
    """★적대 리뷰 P2-8 — 편입분은 **일기가 없다는 것을 증명할 수 있다.** 그러니
    「확인 못 함(None)」이 아니라 「안 셌음(False)」이 맞다. 43건 전부에 「확인하지 못했다」가
    붙으면 3상 설계의 의미가 희석된다."""
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)
    row = db.query(NaverSearchTermExclusion).one()

    out = ste.void_execution(db, exclusion_id=row.id, reason="잘못 편입했다", now=NOW)

    assert out["diary_voided"] == 0
    assert out["wisdom_may_have_counted"] is False, (
        "편입 경로는 일기를 만들지 않으므로 «안 셌다»를 단언할 수 있다 — 여기서만 False가 정직하다"
    )
    assert "편입" in out["diary_note"]


# ═══ 콘솔이 알려준 «실제 제외 시각» — 아는 것과 모르는 것을 갈라 둔다 (D-NAO-177) ═══


def test_console_time_lands_in_its_own_column_and_never_moves_excluded_at(db):
    """★이 슬라이스의 핵심 단언 — 콘솔 등록시각은 `console_excluded_at`에만 들어간다.

    `excluded_at`은 「장부가 이 행을 제외로 세운 시각」이고 두 소비자가 그 뜻에 기대어 있다:
    `void_execution`의 일기 매칭 시간 하한과 `exclusion_survival`의 방치 판정. 2024년 날짜를
    그 칸에 넣으면 매칭 창이 1년 반으로 벌어지고 편입 직후 배너가 거짓 빨강이 된다.
    """
    # prod 실제 순서 그대로: 8/11 22:26에 콘솔에서 잘렸고 다음 날 장부에 편입한다.
    imported_at = datetime(2026, 8, 12, 21, 0, 0)
    out = ste.import_console_exclusions(db, rows=[
        # 콘솔 화면 실물 표기 그대로(2026-08-12 Jino 확인: 「골프」 2026.08.11 22:26)
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "골프",
         "console_excluded_at": "2026.08.11 22:26"},
    ], now=imported_at)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.console_excluded_at == datetime(2026, 8, 11, 22, 26)
    assert row.excluded_at == imported_at, "장부가 세운 시각은 편입 시각 그대로여야 한다"
    assert out["dated"] == 1 and out["undated"] == 0
    assert out["results"][0]["console_excluded_at"] == "2026-08-11T22:26:00"


def test_unknown_console_time_stays_null_instead_of_being_filled_with_now(db):
    """★모르는 것은 NULL이다. 편입 시각으로 메우면 «아는 값»과 구분이 사라진다 —
    이 리포가 세 번 연속 당한 「모르는 것을 아는 것으로 센다」의 네 번째가 된다."""
    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "시각모름"},
    ], now=NOW)

    row = db.query(NaverSearchTermExclusion).one()
    assert row.console_excluded_at is None
    assert out["dated"] == 0 and out["undated"] == 1


@pytest.mark.parametrize("raw,expected", [
    ("2026.08.11 22:26", datetime(2026, 8, 11, 22, 26)),      # 콘솔 실물
    ("2026-08-11T22:26:00", datetime(2026, 8, 11, 22, 26)),   # ISO
    ("2026-08-11 22:26", datetime(2026, 8, 11, 22, 26)),
    ("2024-12-26", datetime(2024, 12, 26, 0, 0)),             # 날짜만(가장 오래된 실물)
    ("2024.12.26", datetime(2024, 12, 26, 0, 0)),
])
def test_console_time_accepts_the_shapes_a_human_actually_pastes(db, raw, expected):
    """사람이 콘솔에서 옮겨 적는 경로다 — 형식 하나만 받으면 실제 입력의 대부분이 거부된다."""
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": f"검색어{raw}",
         "console_excluded_at": raw},
    ], now=datetime(2026, 8, 12, 21, 0, 0))
    assert db.query(NaverSearchTermExclusion).one().console_excluded_at == expected


def test_unreadable_or_future_time_rejects_that_row_only(db):
    """★못 읽은 시각을 조용히 버리면 「모른다」와 「안 적었다」가 구분되지 않는다. 그 행만 거부한다.

    미래 시각도 거부한다 — **이미 걸려 있는** 제외의 등록시각이 미래일 수 없으므로 오타다.
    """
    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "정상",
         "console_excluded_at": "2026.08.01 09:00"},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "형식깨짐",
         "console_excluded_at": "어제쯤"},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "미래",
         "console_excluded_at": "2027-01-01T00:00:00"},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "연도오타",
         "console_excluded_at": "0226-08-11 22:26"},
    ], now=NOW)

    assert out["imported"] == 1 and out["rejected"] == 3
    reasons = {r["search_term"]: r.get("reason", "") for r in out["results"]}
    # ★거부 메시지는 **요청에 실제로 있는 필드명**을 안내해야 한다(2R P2) — 개명 후에도
    #   옛 이름을 말하면 사람이 없는 칸을 찾는다.
    assert "console_excluded_at" in reasons["형식깨짐"]
    assert "미래" in reasons["미래"]
    assert "과거" in reasons["연도오타"]
    terms = {r.search_term for r in db.query(NaverSearchTermExclusion).all()}
    assert terms == {"정상"}, "거부된 행은 장부에 안 들어간다"


def test_reimport_without_a_time_keeps_the_time_we_already_knew(db):
    """★재편입이 정보를 «잃는» 방향으로 가면 안 된다 — 아는 값을 모르는 값으로 덮지 않는다."""
    imported_at = datetime(2026, 8, 12, 21, 0, 0)
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM,
         "console_excluded_at": "2026.08.11 22:26"},
    ], now=imported_at)
    row = db.query(NaverSearchTermExclusion).one()
    ste.void_execution(db, exclusion_id=row.id, reason="테스트", now=imported_at)

    out2 = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=imported_at + timedelta(days=1))

    db.refresh(row)
    assert row.console_excluded_at == datetime(2026, 8, 11, 22, 26)
    # dated는 «편입 후 이 행이 시각을 아는가»로 센다 — 이번 붓기에 시각을 안 줬어도 안다.
    assert out2["dated"] == 1 and out2["undated"] == 0


def test_not_console_import_predicate_is_null_safe(db):
    """★`source != 'console_import'` 한 줄이면 될 것 같지만 우리 행의 source는 **NULL**이라
    SQL에서 그 비교가 NULL(=거짓)이 된다 — 편입분이 아니라 **정상 행이 통째로 사라진다.**
    파이썬 필터와 같은 모양인데 결과가 정반대라 술어를 함수로 못 박고 여기서 지킨다."""
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="우리가 잘랐다", now=NOW)
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "편입분"},
    ], now=NOW)

    kept = db.query(NaverSearchTermExclusion).filter(ste.not_console_import()).all()

    assert [r.search_term for r in kept] == [TERM], (
        "우리가 실행한 행(source IS NULL)이 남고 편입분만 빠져야 한다"
    )


def test_console_time_can_be_attached_later_to_a_row_we_already_know(db):
    """★적대 리뷰 P1-2 — 이미 아는 행에 시각을 나중에 붙일 수 있어야 한다.

    종전엔 `already_known` 분기가 준 값을 **아무 신호 없이 버렸고**, 이 칸을 채울 다른 입구가
    없어 한 번 미상이면 영영 미상이었다. prod의 「골프」가 정확히 이 케이스다 — 우리가 8/11에
    보고한 행인데 콘솔 43건 목록에도 들어 있어 첫 붓기에서 22:26이 버려질 참이었다.
    상태·출처는 그대로 두고 **시각만** 채운다.
    """
    _spend(db, day=date(2026, 8, 1), cost=5_000)
    db.commit()
    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="우리가 잘랐다", now=NOW)

    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM,
         "console_excluded_at": "2026.08.11 10:00"},
    ], now=datetime(2026, 8, 12, 21, 0, 0))

    row = db.query(NaverSearchTermExclusion).one()
    assert row.console_excluded_at == datetime(2026, 8, 11, 10, 0), "준 시각이 버려지면 안 된다"
    assert row.source is None and row.excluded_at == NOW, (
        "시각만 채운다 — 출처·장부 시각이 바뀌면 그 행이 성적표에서 사라진다"
    )
    res = out["results"][0]
    assert res["result"] == "already_known" and res["console_time_filled"] is True, (
        "무엇이 어떻게 처리됐는지 행마다 말해야 한다(조용한 폐기 금지)"
    )


def test_reexecuting_a_voided_import_makes_the_row_ours_again(db):
    """★적대 리뷰 P2 — 편입행을 void한 뒤 우리가 진짜 실행하면 그 행은 이제 **우리 조치**다.

    `source='console_import'`가 남으면 **일기는 써서 wisdom이 먹는데** 성적표·오늘 집계·
    Slack 브리핑에선 통째로 빠진다 — 측정에서만 사라지는 조치가 생긴다.
    """
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": TERM},
    ], now=NOW)
    row = db.query(NaverSearchTermExclusion).one()
    ste.void_execution(db, exclusion_id=row.id, reason="편입이 틀렸다", now=NOW)

    ste.record_execution(db, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
                         rationale="이번엔 우리가 잘랐다", now=NOW)

    db.refresh(row)
    assert row.source is None, "우리가 실행한 행이 편입분으로 남으면 측정에서만 사라진다"
    assert db.query(NaverSearchTermExclusion).filter(ste.not_console_import()).count() == 1
    out = scorecard.build_scorecard(db, now=NOW)
    assert out["total"] == 1 and out["imported_unjudgeable_count"] == 0


def test_predicate_keeps_rows_from_a_third_source_not_just_null(db):
    """★변이 생존이 드러낸 구멍(적대 리뷰): 술어의 `!=` 절이 아무 테스트에도 안 걸려 있었다.

    지금은 `source`가 NULL/`console_import` 둘뿐이라 `is_(None)`만으로도 통과한다 — 세 번째
    출처가 생기는 순간 그 행들이 오늘 집계·Slack에서 통째로 사라지는데 테스트는 초록이다.
    """
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "편입분"},
    ], now=NOW)
    db.add(NaverSearchTermExclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term="다른출처", status="excluded",
        cycle=1, excluded_at=NOW, last_transition_at=NOW, cost_at_exclusion=0, source="detected",
    ))
    db.commit()

    kept = {r.search_term for r in
            db.query(NaverSearchTermExclusion).filter(ste.not_console_import()).all()}

    assert kept == {"다른출처"}, "편입분«만» 빠져야 한다 — 술어가 NULL만 보면 이 행이 사라진다"


@pytest.mark.parametrize("raw,ok", [
    ("2026-08-12 20:50", True),    # 편입 5분 전 — 통과
    ("2026-08-12 21:05", True),    # 여유 10분 안 — 시계 차이로 본다
    ("2026-08-12 21:30", False),   # 여유 밖 — 오타로 본다
    ("2010-01-01", True),          # 연도 하한 경계
    ("2009-12-31", False),
])
def test_time_bounds_are_pinned_not_just_roughly_right(db, raw, ok):
    """★경계값이 아무 테스트에도 안 걸려 있어 「미래 여유 30일」·「하한 1900년」 변이가 생존했다.

    성긴 표본(2027년·226년)만 쓰면 경계가 사실상 무근이 된다.
    """
    out = ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": f"경계{raw}",
         "console_excluded_at": raw},
    ], now=datetime(2026, 8, 12, 21, 0, 0))

    assert (out["imported"] == 1) is ok, f"{raw} 판정이 뒤집혔다"


def test_console_time_survives_whitespace_and_timezone_shapes(db):
    """★사람이 콘솔에서 복사하면 탭·후행 공백이 붙는다 — `.strip()`이 깨지면 **전건 거부**다.

    tz-aware datetime 분기도 여기서만 지켜진다(라우터는 str만 넘기므로 SA 직접 호출 경로).
    """
    from zoneinfo import ZoneInfo

    imported_at = datetime(2026, 8, 12, 21, 0, 0)
    ste.import_console_exclusions(db, rows=[
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "공백붙음",
         "console_excluded_at": "\t2026.08.11 22:26  "},
        {"campaign_id": CAMPAIGN, "adgroup_id": ADGROUP, "search_term": "UTC로들어옴",
         "console_excluded_at": datetime(2026, 8, 11, 13, 26, tzinfo=ZoneInfo("UTC"))},
    ], now=imported_at)

    got = {r.search_term: r.console_excluded_at for r in db.query(NaverSearchTermExclusion).all()}
    assert got["공백붙음"] == datetime(2026, 8, 11, 22, 26)
    assert got["UTC로들어옴"] == datetime(2026, 8, 11, 22, 26), (
        "tz-aware는 KST로 옮겨 naive화한다 — 안 그러면 벽시계가 9시간 틀어진다"
    )
