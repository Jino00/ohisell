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

def _ledger_constructor_offenders() -> list[str]:
    """원장 행 생성자를 팩토리 밖에서 부르는 자리를 센다.

    ★적대 리뷰 P2-2가 초판의 두 구멍을 변이로 실증했다:
      ①**별칭 import** — `from app.models import NaverSearchTermExclusion as _L` 뒤 `_L(...)`은
        정규식 `NaverSearchTermExclusion\\s*\\(`에 안 걸린다
      ②**스캔 루트가 `app/`뿐** — `backend/scripts/` 아래에 같은 생성자를 두면 안 잡힌다
      둘 다 148 전건 초록으로 통과했다. 「자리를 센다」는 세는 «범위»가 곧 보장 범위다.
    """
    backend = Path(__file__).resolve().parents[1]
    factory = backend / "app" / "services" / "naver_ad" / "exclusion_grade.py"
    direct = re.compile(r"NaverSearchTermExclusion\s*\(")
    alias_decl = re.compile(r"import\s+NaverSearchTermExclusion\s+as\s+(\w+)")

    offenders: list[str] = []
    for root in ("app", "scripts"):
        for py in (backend / root).rglob("*.py"):
            if py == factory:
                continue
            text = py.read_text(encoding="utf-8")
            patterns = [direct]
            for alias in alias_decl.findall(text):
                patterns.append(re.compile(rf"\b{re.escape(alias)}\s*\("))
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith("class "):
                    continue  # 주석·클래스 «정의»는 생성자 호출이 아니다
                if any(p.search(line) for p in patterns):
                    offenders.append(f"{py.relative_to(backend)}:{lineno}: {stripped[:90]}")
    return offenders


def test_원장_행_생성자는_팩토리_밖에서_호출되지_않는다():
    """★이 파일의 본체. 개별 경로 테스트는 «내가 아는 경로»만 지키고, 계약이 아는 경로는
    실제보다 두 개 적었다. 그러니 「등급 없이 만들 수 없다」는 **자리의 수를 세서** 지킨다.

    새 경로를 만들고 싶으면 `exclusion_grade.new_exclusion()`을 쓰면 된다 — 이 테스트는
    새 경로를 막는 게 아니라 **등급 없는 새 경로**를 막는다.
    """
    offenders = _ledger_constructor_offenders()
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
    # ★★계약이 못 본 그 자리 — cost=0이라 RoAS 미정의 ⇒ 초과/미달 어느 쪽도 «실측»이 아니다.
    #   초판은 `오컷의심`이었고 적대 리뷰 P2-3이 그 논증을 깼다(제외 후에도 노출이 계속 나서
    #   컷이 그 매출을 막지 못했다 ⇒ 「컷이 틀렸다」의 실측이 아니다) ⇒ 보수적으로 `미검증`.
    (eg.Evidence(clk=0, cost=0, conv=1, revenue=12900, has_history=True), 1.711,
     eg.GRADE_UNVERIFIED),
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
    assert dist[eg.GRADE_MISCUT] == 1        # 흑자전환
    assert dist[eg.GRADE_UNDERPERFORM] == 2  # 적자전환 + 표본충분
    assert dist[eg.GRADE_UNVERIFIED] == 3    # 표본미달 + 이력없음 + 비용0전환(판정 보류)


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


# ══════════════════════════════════════════════════════════════════════
# ★Jino가 «실제로 읽는 화면» (계약 §4-C S2-a) — 여기가 테스트 0건이었다
# ══════════════════════════════════════════════════════════════════════
#
# 적대 리뷰가 변이 둘로 이 공백을 실증했다:
#   M6 「이탈의 이유」 렌더 제거      → 148 전건 초록으로 **생존**
#   M7 `_print_report` 호출 자체 삭제 → 148 전건 초록으로 **생존**
# 즉 Jino가 아무것도 못 보게 만들어도 테스트는 전부 통과했다. 서비스 층에만 표면 변이를
# 걸어 둔 탓이다 — 「만드는 층」은 지켜졌는데 «닿는 층»이 비어 있었다(교훈 #362의 세 번째).

def _run_script(monkeypatch, db, capsys, argv: list[str]) -> str:
    """실제 스크립트의 `main()`을 돌리고 표준출력을 그대로 돌려준다(mock 없음)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "exclusion_grade_backfill",
        Path(__file__).resolve().parents[1] / "scripts" / "exclusion_grade_backfill.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr(mod, "kst_today", lambda: TODAY)
    monkeypatch.setattr("sys.argv", ["exclusion_grade_backfill.py", *argv])
    mod.main()
    return capsys.readouterr().out


def test_화면이_백필_전에_계약_이탈을_사실로_단언하지_않는다(db, monkeypatch, capsys):
    """★P1-1 그 자체. 백필 «전»엔 이탈이 −3,985인데 초판은 「1건 어긋난다」를 찍었다."""
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: 1.711)
    _seed_ledger(db)  # 전건 미분류 상태

    out = _run_script(monkeypatch, db, capsys, [])  # 무인자 = 읽기 전용, docstring이 권하는 그 실행

    assert "1건 어긋난다" not in out, "백필 전인데 계약 셈 이야기를 사실로 단언하면 안 된다"
    # ★문자열을 좁게 잡는다 — 「미분류」는 분포표의 라벨로도 찍히므로 그것만 보면
    #   경고 문단을 통째로 지워도 통과한다(자체 변이 M15가 그렇게 살아남았다).
    assert "아직 등급이 없는 행" in out, "백필이 안 돌았다는 «경고»가 화면 위쪽에 없다"
    assert "백필 미실행" in out, "이탈의 진짜 원인이 이유 절에 안 적혔다"
    assert "--backfill" in out, "다음에 뭘 하라는 안내가 없다"


def test_화면이_백필_후에_이탈과_그_이유를_함께_찍는다(db, monkeypatch, capsys):
    """계약 §4-C S2-a: "수치가 [E]와 다르면 **다른 이유가 함께 출력·기록**돼 있다"."""
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: 1.711)
    _seed_ledger(db)

    out = _run_script(monkeypatch, db, capsys, ["--backfill"])

    assert "제외 «임대» 등급 분포" in out          # 분포표 자체가 사라지면 안 된다(M7)
    assert "이탈의 이유" in out                    # 이유 절이 사라지면 안 된다(M6)
    assert "비용0" in out, "이탈을 낳은 행의 «사유»가 화면에 없다"
    assert "아직 등급이 없는 행" not in out         # 백필 후엔 미분류 경고가 없다
    # ★2R P1: 이유 문단의 숫자도 «관측»에서 나와야 한다. 초판 수정은 여기에 「원장 총계
    #   3,990과 1건」을 리터럴로 박았고, 원장 6행짜리 이 테스트에서도 초록이었다 — 즉
    #   화면이 바로 위 표(「실제 합계 6」)와 모순돼도 아무도 안 잡았다.
    ledger_total = db.query(NaverSearchTermExclusion).count()
    assert f"원장 총계 {ledger_total:,}" in out, "이유 문단이 실제 원장 크기를 안 쓴다"
    assert "원장 총계 3,990" not in out, "관측하지 않은 숫자를 사실로 단언하면 안 된다"
    # ★라벨이 아니라 «값»을 단언한다 — 라벨만 보면 BEP를 통째로 숨겨도 통과한다
    #   (자체 변이 M16이 그렇게 살아남았다). 판정에 실제로 쓴 자가 화면에 있어야 한다.
    assert "계정 기본 BEP(현재) = 1.7110" in out


def test_화면이_BEP_미상을_숨기지_않는다(db, monkeypatch, capsys):
    """BEP를 못 구하면 A급이 전부 미검증으로 떨어진다 — 그 사실이 화면에 보여야 한다."""
    from app.services.naver_ad import campaign_target_resolver

    monkeypatch.setattr(campaign_target_resolver, "account_default_bep_roas", lambda _db: None)
    _seed_ledger(db)

    out = _run_script(monkeypatch, db, capsys, ["--backfill"])

    assert "[미상]" in out, "BEP를 못 구했는데 화면이 그 사실을 안 알린다"
    assert "BEP미상" in out, "BEP 미상으로 판정을 포기한 행의 사유가 화면에 없다"


# ══════════════════════════════════════════════════════════════════════════
# 등급 ↔ 만료일 정합성 (D-NAO-285 · 기능 #1의 (b))
# ══════════════════════════════════════════════════════════════════════════
#
# ★이 절이 지키는 것은 «감사가 도는가»가 아니라 **«무엇을 안 고치는가»**다.
#   prod 실측(2026-09-03)에서 어긋난 20건 중 13건이 `오컷의심`이었고, 그 13건에 만료일을
#   주면 다음 08:50 레인이 **네이버 제외키워드를 실제로 삭제**한다. 모듈 머리말이
#   「실행은 소유권 분리 후」라 못 박았고 그 결정은 Jino 대기다 — 그래서 자동 수리가
#   그 등급을 **건드리면 테스트가 빨개진다**. 「고칠 수 있는 것」과 「사람이 정할 것」이
#   같은 통에 들어가는 순간 그 통은 아무도 안 본다.


def _row(db, *, grade, review_at, cycle=1, term="t", adgroup="ag-1"):
    row = eg.new_exclusion(
        campaign_id="cmp-1", adgroup_id=adgroup, search_term=term,
        grade=grade, now=NOW, cycle=cycle,
    )
    row.next_review_at = review_at  # 규칙값을 일부러 어긋내 감사 대상을 만든다
    db.add(row)
    db.commit()
    return row


def test_audit_counts_intentional_null_as_ok(db):
    """`무관`·`미검증`의 NULL은 결함이 아니다 — 규칙이 NULL을 지정한다.

    이걸 «어긋남»으로 세면 prod 3,970건이 매일 결함으로 떠서 감사가 소음이 된다.
    """
    _row(db, grade=eg.GRADE_IRRELEVANT, review_at=None, term="a")
    _row(db, grade=eg.GRADE_UNVERIFIED, review_at=None, term="b")
    out = eg.grade_review_audit(db, today=TODAY)
    assert out["ok"] == 2
    assert out["repairable"] == []
    assert out["pending_decision"] == []


def test_audit_flags_underperform_with_null_review(db):
    """`성과미달`인데 만료일이 비었으면 어긋남 — prod 실측 4건이 이 모양이었다."""
    _row(db, grade=eg.GRADE_UNDERPERFORM, review_at=None, term="c")
    out = eg.grade_review_audit(db, today=TODAY)
    assert len(out["repairable"]) == 1
    item = out["repairable"][0]
    assert item["actual"] is None
    # 규칙이 정한 값 — 테스트가 날짜를 «다시 계산»하지 않고 모듈에 물어본다.
    assert item["should"] == eg.default_next_review_at(
        eg.GRADE_UNDERPERFORM, cycle=1, today=TODAY
    )


def test_audit_flags_unverified_with_date(db):
    """반대 방향 어긋남 — 규칙이 NULL인데 날짜가 박혀 있다(prod 실측 1건)."""
    _row(db, grade=eg.GRADE_UNVERIFIED, review_at=TODAY + timedelta(days=15), term="d")
    out = eg.grade_review_audit(db, today=TODAY)
    assert len(out["repairable"]) == 1
    assert out["repairable"][0]["should"] is None


def test_miscut_goes_to_pending_decision_not_repairable(db):
    """★핵심 — `오컷의심`은 **자동 수리 대상이 아니다**.

    규칙은 「오늘(즉시 도래)」이지만 집행이 소유권 분리(Jino 결정)에 묶여 있다.
    `repairable`에 들어가면 자동 수리가 날짜를 채우고, 그러면 다음 레인이 네이버에 쓴다.
    """
    _row(db, grade=eg.GRADE_MISCUT, review_at=None, term="e")
    out = eg.grade_review_audit(db, today=TODAY)
    assert out["repairable"] == []
    assert len(out["pending_decision"]) == 1
    assert out["pending_decision"][0]["scheduled"] is False


def test_repair_never_touches_miscut(db):
    """★절단 변이 대비 — 수리를 실제로 돌려도 `오컷의심` 행의 만료일은 NULL로 남는다."""
    _row(db, grade=eg.GRADE_MISCUT, review_at=None, term="f")
    _row(db, grade=eg.GRADE_UNDERPERFORM, review_at=None, term="g")
    eg.repair_grade_review(db, today=TODAY, dry_run=False)
    miscut = db.query(NaverSearchTermExclusion).filter_by(search_term="f").one()
    under = db.query(NaverSearchTermExclusion).filter_by(search_term="g").one()
    assert miscut.next_review_at is None, "오컷의심에 날짜가 들어갔다 — 네이버 실쓰기가 예약된다"
    assert under.next_review_at == eg.default_next_review_at(
        eg.GRADE_UNDERPERFORM, cycle=1, today=TODAY
    )


def test_repair_is_dry_run_by_default(db):
    """쓰기는 호출부가 «명시적으로» 켜야 한다 — 이 원장엔 삭제 라우트가 없다."""
    _row(db, grade=eg.GRADE_UNDERPERFORM, review_at=None, term="h")
    out = eg.repair_grade_review(db, today=TODAY)
    assert out["dry_run"] is True and out["would_repair"] == 1
    row = db.query(NaverSearchTermExclusion).filter_by(search_term="h").one()
    assert row.next_review_at is None, "dry_run인데 DB가 바뀌었다"


def test_audit_counts_ungraded_separately(db):
    """등급 자체가 없는 행은 «어긋남»이 아니라 별 문제다 — 두 통을 섞지 않는다."""
    row = eg.new_exclusion(
        campaign_id="cmp-1", adgroup_id="ag-1", search_term="i",
        grade=eg.GRADE_UNVERIFIED, now=NOW,
    )
    row.grade = None
    db.add(row)
    db.commit()
    out = eg.grade_review_audit(db, today=TODAY)
    assert out["ungraded"] == 1 and out["checked"] == 0


def test_repair_actually_commits(db):
    """수리가 **커밋까지** 하는지 — 같은 세션에서 읽으면 트랜잭션 안의 값이 보여서
    `db.commit()`을 지워도 초록이었다(변이 M8 생존, 2026-09-03). `rollback()` 뒤에
    읽으면 커밋된 것만 남는다: 커밋했으면 no-op, 안 했으면 값이 사라져 빨개진다.
    """
    _row(db, grade=eg.GRADE_UNDERPERFORM, review_at=None, term="j")
    eg.repair_grade_review(db, today=TODAY, dry_run=False)
    db.rollback()
    row = db.query(NaverSearchTermExclusion).filter_by(search_term="j").one()
    assert row.next_review_at == eg.default_next_review_at(
        eg.GRADE_UNDERPERFORM, cycle=1, today=TODAY
    ), "커밋 안 됨 — 프로세스가 죽으면 수리가 통째로 사라진다"
