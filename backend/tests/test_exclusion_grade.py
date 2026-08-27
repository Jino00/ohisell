# test_exclusion_grade.py — 제외 «임대» 등급이 «붙는지»가 아니라 «안 붙을 수 없는지»를 지킨다.
#   계약: docs/contracts/CONTRACT_ignition_readiness.md §4-C S2-a · S2-b
#
# ## 이 파일이 지키는 것 (S2-b 원문: "신규 제외 경로가 등급·만료일 없이 행을 만들 수 없음")
# 「없이 만들 수 없다」는 **한 경로를 고친 것**으로 증명되지 않는다. 착수 실측에서 계약이
# 「둘」이라 적은 생성 경로가 실제로는 **넷**이었다 — 그러니 이 파일의 본체는 개별 경로
# 테스트가 아니라 **인구조사 테스트**다: `NaverSearchTermExclusion(`를 직접 부르는 자리가
# 팩토리 밖에 0곳인가. 다섯 번째 경로가 생기는 날 그 테스트가 빨개진다.
#
# ★그리고 만료일 «불변»을 못 박는다. S2는 라벨을 더하는 슬라이스지 재개방 시점을 옮기는
#   슬라이스가 아니다 — 옮기면 그건 계약 §1 「안 하는 것」 ⑥(재개방 실행)이다.
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, NaverSearchTermDaily, NaverSearchTermExclusion
from app.services.naver_ad import exclusion_grade as eg

NOW = datetime(2026, 8, 27, 12, 0, 0)
TODAY = NOW.date()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Channel(id=6, name="네이버", code="naver", platform="naver"))
    session.commit()
    yield session
    session.close()


def _daily(db, adgroup, term, *, clk, cost, conv=0, rev=0, day=date(2026, 8, 1)):
    db.add(NaverSearchTermDaily(
        ad_date=day, campaign_id="cmp-1", adgroup_id=adgroup, search_term=term,
        source="shopping", imp=max(clk, 1), clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=conv, conv_purchase_amt=rev,
    ))


# ══════════════════════════════════════════════════════════════════════
# S2-b ①  인구조사 — 원장 행이 태어나는 자리는 팩토리 하나뿐이다
# ══════════════════════════════════════════════════════════════════════

def test_원장_행_생성자는_팩토리_밖에서_호출되지_않는다():
    """★이 파일의 본체. 개별 경로 테스트는 «내가 아는 경로»만 지키고, 계약이 아는 경로는
    실제보다 두 개 적었다. 그러니 「등급 없이 만들 수 없다」는 **자리의 수를 세서** 지킨다.

    새 경로를 만들고 싶으면 `exclusion_grade.new_exclusion()`을 쓰면 된다 — 이 테스트는
    새 경로를 막는 게 아니라 **등급 없는 새 경로**를 막는다.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    factory = app_dir / "services" / "naver_ad" / "exclusion_grade.py"
    pattern = re.compile(r"NaverSearchTermExclusion\s*\(")

    offenders: list[str] = []
    for py in app_dir.rglob("*.py"):
        if py == factory:
            continue
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("class "):
                continue  # 주석·클래스 «정의»는 생성자 호출이 아니다
            if pattern.search(line):
                offenders.append(f"{py.relative_to(app_dir)}:{lineno}: {stripped[:90]}")

    assert offenders == [], (
        "제외 원장 행을 팩토리 밖에서 직접 만드는 자리가 생겼다 — 그 경로는 등급 없는 행을 "
        "낳는다(계약 §4-C S2-b). `exclusion_grade.new_exclusion(...)`을 쓸 것:\n  "
        + "\n  ".join(offenders)
    )


def test_팩토리는_등급_없이는_행을_안_만든다():
    with pytest.raises(TypeError):  # grade가 키워드 «필수»
        eg.new_exclusion(campaign_id="c", adgroup_id="g", search_term="t", now=NOW)
    with pytest.raises(eg.ExclusionGradeError):
        eg.new_exclusion(
            campaign_id="c", adgroup_id="g", search_term="t", now=NOW, grade="아무거나",
        )


# ══════════════════════════════════════════════════════════════════════
# S2-b ②  네 경로 전부가 등급·만료일을 붙인다 (라이브 호출 — mock 없음)
# ══════════════════════════════════════════════════════════════════════

class _NoCloseSession:
    """diary는 자기 세션을 열고 반드시 닫는다 — 인메모리 테스트에선 그 close가 테스트 세션까지
    닫는다. 기존 `test_search_term_execution_chain.py`와 같은 관례.

    ★일기를 mock으로 «막지» 않는 이유(교훈 #362): mock은 만드는 층을 가려서, 등급이 붙기 전에
      일기가 먼저 나가는 순서 결함 같은 것을 통과시킨다. 진짜로 돌린다."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


def test_경로1_record_execution이_등급과_만료일을_붙인다(db, monkeypatch):
    from app.services.naver_ad import diary, search_term_execution as ste

    monkeypatch.setattr(diary, "_new_diary_session", lambda _db: _NoCloseSession(db))
    out = ste.record_execution(
        db, campaign_id="cmp-1", adgroup_id="grp-1", search_term="골프",
        rationale="30일 비용 5만원·전환 0", now=NOW,
    )
    row = db.query(NaverSearchTermExclusion).one()
    assert row.grade == eg.GRADE_UNDERPERFORM
    assert row.grade_reason  # 왜 그 등급인지가 비어 있으면 다음 세션이 숫자를 못 믿는다
    assert row.next_review_at == TODAY + timedelta(days=30)
    assert out["grade"] == eg.GRADE_UNDERPERFORM


def test_경로2_console_import는_미검증이고_만료는_보류다(db):
    from app.services.naver_ad import search_term_execution as ste

    ste.import_console_exclusions(db, rows=[{
        "campaign_id": "cmp-1", "adgroup_id": "grp-1", "search_term": "골프",
    }], now=NOW)
    row = db.query(NaverSearchTermExclusion).one()
    assert row.grade == eg.GRADE_UNVERIFIED
    # ★NULL «유지»가 핵심이다 — 편입분 3,987행에 만료일이 생기면 그게 재개방 실행의 예약이 된다
    assert row.next_review_at is None
    assert row.source == ste.CONSOLE_IMPORT_SOURCE


def test_경로3_autofire_upsert가_등급을_붙인다(db):
    from app.services.naver_ad import search_term_ss_lane as lane

    lane._upsert_exclusion(
        db, {"campaign_id": "cmp-1", "adgroup_id": "grp-1", "search_term": "골프", "cost": 51000},
        "restrict-1", NOW,
    )
    row = db.query(NaverSearchTermExclusion).one()
    assert row.grade == eg.GRADE_UNDERPERFORM
    assert row.next_review_at == TODAY + timedelta(days=30)


def test_경로3b_재제외는_cycle과_등급_만료일이_함께_전진한다(db):
    """★이 테스트가 없어서 변이 하나가 살아남았다(2026-08-27 자체 변이 M3).

    신규 insert 경로는 팩토리가 등급을 박아 주므로, `_apply_exclusion_fields`에서 등급
    부여를 통째로 지워도 **신규 행 테스트는 초록**이었다. 그런데 실제로 그 함수에 기대는
    것은 **재제외 경로**다 — restored/probation 행이 다시 잘릴 때 cycle이 +1 되고 백오프가
    30→60→90으로 늘어야 하는데, 등급 부여가 빠지면 그 행은 **옛 만료일을 그대로 안고**
    남는다. 즉 재제외를 반복해도 재심사 간격이 영영 안 늘어난다.

    ★교훈 #362의 재현: 「만드는 층」만 보는 테스트는 「닿는 층」의 절단을 못 본다.
    """
    from app.services.naver_ad import search_term_ss_lane as lane

    old_review = date(2026, 7, 1)
    row = eg.new_exclusion(
        campaign_id="cmp-1", adgroup_id="grp-1", search_term="골프",
        grade=eg.GRADE_UNDERPERFORM, now=NOW, cycle=1,
    )
    row.status = "restored"
    row.next_review_at = old_review
    db.add(row)
    db.commit()

    lane._upsert_exclusion(
        db, {"campaign_id": "cmp-1", "adgroup_id": "grp-1", "search_term": "골프", "cost": 9000},
        "restrict-2", NOW,
    )

    db.refresh(row)
    assert row.status == "excluded"
    assert row.cycle == 2                                   # 승계 +1
    assert row.grade == eg.GRADE_UNDERPERFORM
    assert row.next_review_at == TODAY + timedelta(days=60)  # 백오프가 «전진»한다
    assert row.next_review_at != old_review


def test_경로4_고아치유가_등급을_붙인다(db):
    """★계약이 «둘»이라 적어 놓친 네 번째 경로. 이 테스트가 없으면 크래시 치유로 태어난 행만
    조용히 등급 없이 남고, 그 행은 재개방 판정을 영영 못 받는다."""
    import json

    from app.models import NaverChangeLog, NaverProposal
    from app.services.naver_ad import search_term_ss_lane as lane

    prop = NaverProposal(
        campaign_id="cmp-1", adgroup_id="grp-1", proposal_type="exclude_search_term",
        target_type="keyword", target_id="골프", rationale="x", created_at=NOW,
    )
    db.add(prop)
    db.flush()
    db.add(NaverChangeLog(
        entity_type="search_term", entity_id="골프", campaign_id="cmp-1",
        action="exclude_search_term", proposal_id=prop.id, dry_run=False,
        after_value=json.dumps({"created_ids": ["restrict-9"]}),
        changed_at=NOW, executed_at=NOW, rationale="x",
    ))
    db.commit()

    healed = lane._reconcile_orphan_exclusions(db, NOW)
    assert healed == 1
    row = db.query(NaverSearchTermExclusion).one()
    assert row.grade == eg.GRADE_UNDERPERFORM
    # 종전 리터럴은 `+30일` 고정이었다 — 값이 같아야 «행위 불변»이 증명된다
    assert row.next_review_at == TODAY + timedelta(days=30)


# ══════════════════════════════════════════════════════════════════════
# 만료일 규칙 (계약 §4-B⑥) — 값이 종전과 같아야 한다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grade,cycle,expected", [
    (eg.GRADE_UNDERPERFORM, 1, TODAY + timedelta(days=30)),
    (eg.GRADE_UNDERPERFORM, 2, TODAY + timedelta(days=60)),
    (eg.GRADE_UNDERPERFORM, 5, TODAY + timedelta(days=90)),   # 상한 cap
    (eg.GRADE_BROAD, 1, TODAY + timedelta(days=90)),
    (eg.GRADE_MISCUT, 1, TODAY),                              # 즉시 도래
    (eg.GRADE_IRRELEVANT, 1, None),                           # 영구
    (eg.GRADE_UNVERIFIED, 1, None),                           # 보류
])
def test_등급별_기본_만료일(grade, cycle, expected):
    assert eg.default_next_review_at(grade, cycle=cycle, today=TODAY) == expected


def test_무관과_미검증은_같은_NULL이지만_등급으로_갈린다():
    """★`grade` 칸이 존재하는 이유 그 자체. 만료일만 보면 둘은 구분 불가다."""
    assert eg.default_next_review_at(eg.GRADE_IRRELEVANT, cycle=1, today=TODAY) is None
    assert eg.default_next_review_at(eg.GRADE_UNVERIFIED, cycle=1, today=TODAY) is None
    assert eg.GRADE_IRRELEVANT != eg.GRADE_UNVERIFIED


def test_성과미달_식이_기존_백오프와_같다():
    """S2가 재개방 시점을 옮기지 않았음을 상수 수준에서 못 박는다."""
    from app.services.naver_ad import search_term_execution as ste

    assert ste._REVIEW_BACKOFF_DAYS == eg.REVIEW_BACKOFF_DAYS == 30
    assert ste._REVIEW_BACKOFF_MAX == eg.REVIEW_BACKOFF_MAX == 90


# ══════════════════════════════════════════════════════════════════════
# 백필 분류 (계약 §4-B⑦ + 실측이 드러낸 사각)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("ev,bep,expected", [
    # A급 — 전환 있음
    (eg.Evidence(clk=20, cost=34835, conv=1, revenue=15900, has_history=True), 1.711,
     eg.GRADE_UNDERPERFORM),                                   # RoAS 0.456 → 컷이 옳았다
    (eg.Evidence(clk=5, cost=8817, conv=1, revenue=15900, has_history=True), 1.711,
     eg.GRADE_MISCUT),                                         # RoAS 1.803 → BEP 초과
    # ★★계약이 못 본 그 1건 — cost=0이라 RoAS 미정의
    (eg.Evidence(clk=0, cost=0, conv=1, revenue=12900, has_history=True), 1.711,
     eg.GRADE_MISCUT),
    # 전환 없음 — 클릭이 표본 충분성을 가른다
    (eg.Evidence(clk=21, cost=1, conv=0, revenue=0, has_history=True), 1.711,
     eg.GRADE_UNDERPERFORM),                                   # B: 산업표준 통계컷 충족
    (eg.Evidence(clk=10, cost=1, conv=0, revenue=0, has_history=True), 1.711,
     eg.GRADE_UNDERPERFORM),                                   # C
    (eg.Evidence(clk=9, cost=1, conv=0, revenue=0, has_history=True), 1.711,
     eg.GRADE_UNVERIFIED),                                     # D: 표본 미달
    (eg.Evidence(clk=0, cost=0, conv=0, revenue=0, has_history=True), 1.711,
     eg.GRADE_UNVERIFIED),                                     # E: 노출만
    (eg.Evidence(clk=0, cost=0, conv=0, revenue=0, has_history=False), 1.711,
     eg.GRADE_UNVERIFIED),                                     # 이력 없음
])
def test_백필_분류_규칙(ev, bep, expected):
    grade, reason = eg.classify(ev, bep_roas=bep)
    assert grade == expected
    assert reason  # 근거 없는 등급은 다음 세션이 못 쓴다


def test_BEP를_모르면_전환행을_추정하지_않는다():
    """전역 §3 추정 금지 — 계정 기본 BEP가 없으면 «초과»도 «미달»도 쓰지 않는다."""
    ev = eg.Evidence(clk=5, cost=8817, conv=1, revenue=15900, has_history=True)
    grade, reason = eg.classify(ev, bep_roas=None)
    assert grade == eg.GRADE_UNVERIFIED
    assert "BEP미상" in reason


def test_BEP_경계는_1_313과_1_803_사이에서_판정이_안_바뀐다():
    """★prod A급 16건의 RoAS 사다리에서 BEP 바로 아래는 1.313, 바로 위는 1.803이다.
    계약이 인용한 1.711과 라이브 계정값 1.6759가 **같은 13/2 분할**을 낳는다는 뜻이고,
    그래서 이 분류는 BEP 소수점에 흔들리는 knife-edge가 아니다."""
    below = eg.Evidence(clk=7, cost=10507, conv=1, revenue=13800, has_history=True)   # 1.313
    above = eg.Evidence(clk=5, cost=8817, conv=1, revenue=15900, has_history=True)    # 1.803
    for bep in (1.6759, 1.711):
        assert eg.classify(below, bep_roas=bep)[0] == eg.GRADE_UNDERPERFORM
        assert eg.classify(above, bep_roas=bep)[0] == eg.GRADE_MISCUT


# ══════════════════════════════════════════════════════════════════════
# 백필 실행 — 라벨만 붙이고 만료일은 안 건드린다 (계약 §2-5)
# ══════════════════════════════════════════════════════════════════════

def _seed_ledger(db):
    rows = [
        # (adgroup, term, next_review_at, 성과)
        ("grp-1", "적자전환", None, dict(clk=20, cost=34835, conv=1, rev=15900)),
        ("grp-1", "흑자전환", None, dict(clk=5, cost=8817, conv=1, rev=15900)),
        ("grp-1", "비용0전환", None, dict(clk=0, cost=0, conv=1, rev=12900)),
        ("grp-1", "표본충분", None, dict(clk=25, cost=9000, conv=0, rev=0)),
        ("grp-1", "표본미달", None, dict(clk=3, cost=900, conv=0, rev=0)),
    ]
    for adgroup, term, review, perf in rows:
        row = eg.new_exclusion(
            campaign_id="cmp-1", adgroup_id=adgroup, search_term=term,
            grade=eg.GRADE_UNVERIFIED, now=NOW, source="console_import",
        )
        row.next_review_at = review
        row.grade = None  # 백필 «전» 상태를 만든다
        row.grade_reason = None
        db.add(row)
        _daily(db, adgroup, term, **perf)
    # 이력 없는 행 하나
    orphan = eg.new_exclusion(
        campaign_id="cmp-1", adgroup_id="grp-1", search_term="이력없음",
        grade=eg.GRADE_UNVERIFIED, now=NOW, source="console_import",
    )
    orphan.next_review_at = None
    orphan.grade = None
    db.add(orphan)
    db.commit()


def test_백필이_전건에_등급을_붙이고_만료일은_안_건드린다(db, monkeypatch):
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: 1.711)
    _seed_ledger(db)
    before = {r.id: r.next_review_at for r in db.query(NaverSearchTermExclusion).all()}

    out = eg.backfill(db, today=TODAY)

    assert out["total"] == 6
    assert out["graded"] == 6
    assert db.query(NaverSearchTermExclusion).filter(
        NaverSearchTermExclusion.grade.is_(None)
    ).count() == 0
    # ★만료일 «불변» — 라벨링은 실행이 아니다
    after = {r.id: r.next_review_at for r in db.query(NaverSearchTermExclusion).all()}
    assert after == before

    dist = out["distribution"]
    assert dist[eg.GRADE_MISCUT] == 2        # 흑자전환 + 비용0전환
    assert dist[eg.GRADE_UNDERPERFORM] == 2  # 적자전환 + 표본충분
    assert dist[eg.GRADE_UNVERIFIED] == 2    # 표본미달 + 이력없음


def test_백필은_이미_붙은_등급을_덮지_않는다(db, monkeypatch):
    """사람이 손으로 찍은 «무관»을 재실행이 «미검증»으로 되돌리면 그게 판단의 소실이다."""
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: 1.711)
    _seed_ledger(db)
    row = db.query(NaverSearchTermExclusion).filter_by(search_term="표본미달").one()
    row.grade = eg.GRADE_IRRELEVANT
    row.grade_reason = "Jino 판별 — 경쟁사 브랜드"
    db.commit()

    out = eg.backfill(db, today=TODAY)

    assert out["graded"] == 5 and out["skipped"] == 1
    db.refresh(row)
    assert row.grade == eg.GRADE_IRRELEVANT
    assert row.grade_reason == "Jino 판별 — 경쟁사 브랜드"


def test_분포_보고가_계약_기대치와의_이탈을_이유까지_싣는다(db, monkeypatch):
    """계약 §4-C S2-a: "수치가 [E]와 다르면 **다른 이유가 함께 출력·기록**돼 있다"."""
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: 1.711)
    _seed_ledger(db)
    eg.backfill(db, today=TODAY)

    report = eg.distribution_report(db)
    assert report["total"] == 6
    assert report["deviation"], "기대치와 다른데 이탈이 비어 있으면 대조가 장식이다"
    # 비용0 행의 사유가 그대로 실려야 한다 — 숫자만 다르면 다음 세션이 원인을 다시 판다
    assert any("비용0" in (r["reason"] or "") for r in report["deviation_rows"])
    assert report["expected_sum"] == 3989  # 계약 원문의 합 — 고쳐 적지 않고 그대로 대조한다
