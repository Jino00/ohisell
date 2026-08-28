# test_exclusion_lifecycle.py — 복귀(재개방) 실험이 «학습 사슬에 타는지»를 지킨다.
#   계약: docs/contracts/CONTRACT_ignition_readiness.md §4-C **S3-a · S3-b**
#
# ## 이 파일이 지키는 것
# S3-a 원문: *"재개방·재제외 경로가 execute 일기를 남김이 테스트로 고정(harness 우회 경로 포함
#   — diary 호출이 브리핑 3곳뿐이던 상태의 종결)"*
#
# 착수 실측(2026-08-27)이 확인한 출발점:
#   재제외(`_autofire_exclude`) → harness.execute() → execute 일기 ✅
#   재개방(`_open_exclusion`)   → naver_sa_writer 직접 호출 → **일기 0건** ❌
#   복귀 확정(restored)         → 순수 DB 전이, change_log조차 없음 → **어디에도 없음** ❌
# 즉 «실험의 시작도 끝도» 사슬 밖이었다.
#
# ★그래서 본체는 개별 경로 테스트가 아니라 **인구조사**다(S2-b에서 배운 모양의 재적용):
#   제외 원장의 status를 바꾸는 자리를 전수로 세고, 각 자리가 「일기를 누가 남기는가」로
#   분류돼 있는지를 못 박는다. 새 전이가 생기면 이 테스트가 빨개진다.
#
# ★★인구조사의 좌표를 **줄번호가 아니라 함수 이름**으로 잡는다. 이 세션의 착수 실측이 바로
#   그 이유를 실증했다 — 계약 §1이 박아 둔 줄번호 `970·984·1004`는 S1·S2가 코드를 넣는 사이
#   `983·997·1017`로 밀려 이미 stale이었다. 줄번호로 고정한 인구조사는 리팩터 한 번에 거짓이 된다.
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverSearchTermDaily,
    NaverSearchTermExclusion,
    OpsDiaryEntry,
)
from app.services.naver_ad import (
    diary_outcome,
    exclusion_grade,
    exclusion_lifecycle,
    exclusion_return_score,
    search_term_ss_lane as lane,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)
TODAY = NOW.date()
CAMPAIGN = "cmp-1"
ADGROUP = "grp-1"
TERM = "지문방지필름"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Channel(id=6, name="네이버", code="naver", platform="naver"))
    # 킬스위치 ON + 그룹 소속 — 이 둘은 «다른 가드»라 진짜 행으로 세운다(테스트가 지키려는
    # 층은 일기 배선이지 가드가 아니지만, 가드를 mock으로 치우면 경로가 실제로 안 밟힌다).
    session.add(NaverCampaignSettings(campaign_id=CAMPAIGN, auto_operate=True, optimizer="ours"))
    session.add(NaverEntity(entity_type="adgroup", entity_id=ADGROUP, parent_id=CAMPAIGN,
                            name="그룹1", status="ELIGIBLE"))
    session.commit()
    yield session
    session.close()


def _excluded_row(db, *, status="excluded", cycle=1, kwd="rk-1"):
    row = exclusion_grade.new_exclusion(
        campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=TERM,
        grade=exclusion_grade.GRADE_UNVERIFIED, now=NOW, cycle=cycle,
        status=status, restrict_kwd_id=kwd,
    )
    db.add(row)
    db.commit()
    return row


def _diaries(db, *, action=None):
    q = db.query(OpsDiaryEntry).filter(OpsDiaryEntry.event_type == "execute")
    if action is not None:
        q = q.filter(OpsDiaryEntry.action == action)
    return q.all()


# ══════════════════════════════════════════════════════════════════════
# S3-a ①  인구조사 — 상태 전이마다 「일기를 누가 남기는가」가 정해져 있다
# ══════════════════════════════════════════════════════════════════════

# 제외 원장 행이 «태어나거나 상태가 바뀌는» 자리의 정본 목록.
# 키 = (파일, 함수, 쓰는 값 또는 `new_exclusion()`), 값 = 「일기를 누가 남기는가」 분류.
#   lifecycle : `exclusion_lifecycle`이 남긴다(harness 우회 경로 — 이 PR이 만든 자리)
#   harness   : `naver_execution_harness.execute`가 남긴다(제안 경로)
#   self      : 그 함수가 직접 `diary.write_diary_entry`를 부른다(콘솔 실행 보고 경로)
#   voided    : 그 함수가 짝 일기(`event_type="voided"`)를 남긴다 — 무효화 전용 축
#   import    : 남이 콘솔에서 한 제외를 «장부에 편입»만 한다 — 우리 조치가 아니라 일기 대상이 아니다
#   claim     : 같은 사건의 원자 전이 보조 — 일기는 사건이 «확정»될 때 1건만 남는다
#   heal      : 크래시 고아 치유 — 아래 이유 참조(일기 없음이 옳다)
_STATUS_WRITE_INVENTORY: dict[tuple[str, str, str], str] = {
    ("search_term_execution.py", "record_execution", "new_exclusion()"): "self",
    ("search_term_execution.py", "record_execution", "excluded"): "self",
    ("search_term_execution.py", "import_console_exclusions", "new_exclusion()"): "import",
    ("search_term_execution.py", "import_console_exclusions", "excluded"): "import",
    ("search_term_execution.py", "void_execution", "VOID_STATUS"): "voided",
    ("search_term_ss_lane.py", "_apply_exclusion_fields", "excluded"): "harness",
    ("search_term_ss_lane.py", "_upsert_exclusion", "new_exclusion()"): "harness",
    ("search_term_ss_lane.py", "_open_exclusion", "probation"): "claim",
    ("search_term_ss_lane.py", "_open_exclusion", "excluded"): "claim",
    ("search_term_ss_lane.py", "_reconcile_orphan_exclusions", "new_exclusion()"): "heal",
    ("search_term_ss_lane.py", "_reconcile_probation_orphans", "excluded"): "heal",
    ("search_term_ss_lane.py", "_run_reexamination", "probation"): "lifecycle",
    ("search_term_ss_lane.py", "_run_reexamination", "restored"): "lifecycle",
}

# heal 분류의 근거(코드가 아니라 «판단»이라 여기 적어 둔다 — 다음 사람이 재발명하지 않도록):
#   ·`_reconcile_probation_orphans` 창①은 「개방 클레임은 커밋됐는데 delete는 안 나갔다」를
#     excluded로 되돌리는 것이다. 그 경우 개방 일기는 **애초에 안 쓰였다**(일기는 delete 성공 +
#     change_log 커밋 «후»에만 쓴다). 되돌림에 일기를 남기면 «일어나지 않은 실험»이 사슬에 뜬다.
#     창②는 probation_until 소급 세팅뿐이라 status를 안 쓴다(개방 일기는 이미 있다).
#   ·`_reconcile_orphan_exclusions`는 harness가 실쓰기·일기를 이미 끝낸 제외의 **상태 행만**
#     재생성한다. 일기를 또 남기면 한 제외가 두 번 집행된 것처럼 보인다.

# ★적대 리뷰 P2가 구멍 셋을 실증했다 — 초판 정규식은 **문자열 리터럴 status만** 봤다:
#   ① `row.status = VOID_STATUS` (상수 참조) — prod에 `status='void'` 4행 실재인데 안 잡혔다
#   ② `exclusion_grade.new_exclusion(status=...)`로 **태어나는** 행 — `_reconcile_orphan_exclusions`가
#      이미 그렇게 행을 만드는데 목록에 아예 없었다. 새 전이를 팩토리로 구현하면 인구조사는
#      초록인 채 일기가 조용히 빠진다(이 PR이 고치러 온 병 그 자체)
#   ③ assert 메시지가 「전수」를 주장하는데 ①②가 빠져 그 주장이 거짓이었다
# ⇒ 값을 리터럴로 한정하지 않고(상수 참조 포함), 팩토리 «출생»도 같은 인구조사에 넣는다.
_ASSIGN = re.compile(r"\.status\s*=\s*(\"[^\"]+\"|'[^']+'|[A-Z_][A-Z0-9_]*)")
_UPDATE = re.compile(r"[\"']status[\"']\s*:\s*(\"[^\"]+\"|'[^']+'|[A-Z_][A-Z0-9_]*)")
_BIRTH = re.compile(r"\bnew_exclusion\s*\(")
# 원장을 다루는 파일만 본다 — 같은 문자열은 다른 도메인(쿠팡 원가 등)에도 흔하다.
_LEDGER_FILES = ("search_term_ss_lane.py", "search_term_execution.py")


def _enclosing_def(lines: list[str], idx: int) -> str:
    """idx(0-based) 위쪽에서 가장 가까운 `def `를 찾아 함수 이름을 돌려준다.
    ★줄번호 대신 함수 이름으로 좌표를 잡는 이유는 파일 헤더 주석 참조(계약 §1의 줄번호가
    이미 stale이 된 실증)."""
    for i in range(idx, -1, -1):
        m = re.match(r"\s*def\s+(\w+)", lines[i])
        if m:
            return m.group(1)
    return "<module>"


def _status_write_sites() -> dict[tuple[str, str, str], list[int]]:
    naver_ad = Path(__file__).resolve().parents[1] / "app" / "services" / "naver_ad"
    sites: dict[tuple[str, str, str], list[int]] = {}
    for name in _LEDGER_FILES:
        path = naver_ad / name
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # 주석 속 예시는 쓰기가 아니다
            m = _ASSIGN.search(line) or _UPDATE.search(line)
            token = m.group(1).strip("\"'") if m else ("new_exclusion()" if _BIRTH.search(line) else None)
            if token is None:
                continue
            sites.setdefault((name, _enclosing_def(lines, idx), token), []).append(idx + 1)
    return sites


def test_상태_전이마다_일기_주체가_정해져_있다():
    """★이 파일의 본체. 「재개방이 일기를 남긴다」를 한 경로 테스트로만 지키면, **다음에 생기는
    전이가 또 조용히 빠진다** — 그게 이번 슬라이스가 고치러 온 병 그 자체다(재개방은 2026-07
    부터 있었는데 일기가 0건이었고 아무 테스트도 빨개지지 않았다).

    새 전이를 만드는 것을 막지 않는다. 막는 것은 **분류되지 않은 새 전이**다: 새 자리를 만들면
    이 목록에 등록하면서 「일기를 누가 남기는가」를 답해야 한다.
    """
    found = _status_write_sites()
    unknown = {k: v for k, v in found.items() if k not in _STATUS_WRITE_INVENTORY}
    missing = {k for k in _STATUS_WRITE_INVENTORY if k not in found}
    assert not unknown, (
        "제외 원장 status를 바꾸는 새 자리가 생겼는데 「일기를 누가 남기는가」가 미분류다 "
        "(계약 §4-C S3-a). _STATUS_WRITE_INVENTORY에 등록할 것:\n  "
        + "\n  ".join(f"{f}:{fn} → {val} (줄 {ln})" for (f, fn, val), ln in unknown.items())
    )
    assert not missing, (
        "정본 목록의 전이가 코드에서 사라졌다 — 이름이 바뀌었으면 목록도 같이 고쳐야 인구조사가 "
        f"계속 유효하다: {sorted(missing)}"
    )


def test_lifecycle_분류_전이는_실제로_lifecycle을_지난다():
    """분류표가 «주장»에 그치지 않게, lifecycle로 분류한 전이가 실제로 그 모듈을 호출하는지
    소스에서 확인한다(분류표만 고치고 배선은 안 하는 경로 차단)."""
    src = (Path(__file__).resolve().parents[1] / "app" / "services" / "naver_ad"
           / "search_term_ss_lane.py").read_text(encoding="utf-8")
    assert "exclusion_lifecycle.record_return_opened(" in src
    assert "exclusion_lifecycle.record_return_settled(" in src


# ══════════════════════════════════════════════════════════════════════
# S3-a ②  경로별 — 재개방·복귀확정이 실제로 execute 일기를 남긴다
# ══════════════════════════════════════════════════════════════════════

def test_재개방이_execute_일기를_남긴다(db, monkeypatch):
    """harness 우회 경로의 종결. 외부 경계(네이버 writer)만 가짜로 두고 나머지 가드는 실제
    행으로 통과시킨다 — 킬스위치·스코프·소속 가드를 mock으로 치우면 「경로를 밟았다」가 거짓이 된다."""
    row = _excluded_row(db)
    calls: list[tuple] = []

    def _fake_delete(adgroup_id, ids):
        calls.append((adgroup_id, tuple(ids)))
        return type("R", (), {"before": [{"k": TERM}], "after": []})()

    monkeypatch.setattr(lane.naver_sa_writer, "get_adgroup_type", lambda adgroup_id: "WEB_SITE")
    monkeypatch.setattr(lane.naver_sa_writer, "delete_restricted_keywords", _fake_delete)

    assert lane._open_exclusion(db, row, NOW) is True
    assert calls == [(ADGROUP, ("rk-1",))]  # 외부 쓰기는 정확히 1회

    entries = _diaries(db, action=exclusion_lifecycle.RETURN_OPEN_ACTION)
    assert len(entries) == 1, "재개방이 execute 일기를 남기지 않았다 — 학습 사슬 밖으로 다시 샜다"
    e = entries[0]
    assert e.target_type == "search_term" and e.target_id == TERM
    assert e.adgroup_id == ADGROUP and e.campaign_id == CAMPAIGN
    assert e.actor == exclusion_lifecycle.ACTOR
    # source_ref가 복귀 change_log를 가리켜야 「무엇을 근거로 한 일기인가」가 추적된다.
    log_row = db.query(NaverChangeLog).filter(
        NaverChangeLog.action == exclusion_lifecycle.RETURN_OPEN_ACTION,
        NaverChangeLog.after_value.isnot(None),
    ).one()
    assert e.source_ref == log_row.id


def test_개방_실패는_일기를_남기지_않는다(db, monkeypatch):
    """실쓰기가 안 났는데 일기가 남으면 사슬이 «일어나지 않은 실험»을 채점한다."""
    row = _excluded_row(db)

    def _boom(adgroup_id, ids):
        raise RuntimeError("네이버 400")

    monkeypatch.setattr(lane.naver_sa_writer, "delete_restricted_keywords", _boom)
    assert lane._open_exclusion(db, row, NOW) is False
    assert _diaries(db, action=exclusion_lifecycle.RETURN_OPEN_ACTION) == []
    db.refresh(row)
    assert row.status == "excluded"  # 클레임 롤백도 함께 확인


def test_복귀_확정이_execute_일기를_남긴다(db):
    """restored는 네이버 무접촉이라 change_log조차 없다 — 이 일기가 유일한 흔적이다."""
    row = _excluded_row(db, status="probation", cycle=2)
    exclusion_lifecycle.record_return_settled(
        db, row, verdict=exclusion_lifecycle.VERDICT_RESTORED, now=NOW,
    )
    entries = _diaries(db, action=exclusion_lifecycle.RETURN_SETTLED_ACTION)
    assert len(entries) == 1
    after = json.loads(entries[0].after_value)
    assert after == {"status": "restored", "cycle": 2}
    assert entries[0].source_ref is None  # change_log 없는 전이 — 없는 것을 가리키지 않는다


def test_레인의_복귀확정_갈래가_일기를_남긴다(db):
    """★변이가 잡은 실결함의 회귀 잠금. 초판은 recorder를 **직접 불러** 검사했고, 그래서
    `_run_reexamination`에서 recorder 호출을 통째로 지워도 23건이 전부 초록이었다
    (변이 M3 SURVIVED). 만드는 층은 지켰는데 **닿는 층**을 안 지킨 것 — 교훈 #362 그대로다.
    그래서 레인 함수를 실제로 돌려 그 갈래를 밟는다(mock 없음)."""
    row = _excluded_row(db, status="probation")
    row.probation_until = TODAY - timedelta(days=1)  # 관찰창 만료
    db.commit()

    # powerlink=[] ⇒ 더는 §1 후보가 아니다 ⇒ restored 갈래
    res = lane._run_reexamination(db, [], NOW)

    assert res["restored"] == 1
    db.refresh(row)
    assert row.status == "restored"
    entries = _diaries(db, action=exclusion_lifecycle.RETURN_SETTLED_ACTION)
    assert len(entries) == 1, (
        "복귀 확정이 레인에서 일기를 안 남겼다 — 실험의 «끝»이 사슬 밖으로 샌다"
    )
    assert json.loads(entries[0].after_value)["status"] == "restored"


def test_재제외는_일기를_두_번_남기지_않는다(db):
    """재제외 갈래는 harness가 exclude_search_term 일기를 이미 남긴다. recorder가 또 남기면
    같은 사건이 두 줄이 되어 소급 채점이 이중 계상한다."""
    row = _excluded_row(db, status="probation")
    exclusion_lifecycle.record_return_settled(
        db, row, verdict=exclusion_lifecycle.VERDICT_REEXCLUDED, now=NOW,
    )
    assert _diaries(db) == []


def _code_only(path: Path) -> str:
    """주석·독스트링을 걷어낸 «실행되는 코드»만. 이 구분이 없으면 금지선 검사가 **주석에 적힌
    설명**을 위반으로 읽는다(초판이 그랬다 — 모듈 헤더가 「naver_sa_writer 직접 호출 → 일기 0건」
    이라는 배경 설명을 담고 있어서 빨개졌다). 검사는 코드에 걸어야 한다."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # 독스트링 노드 제거 — 본문 첫 문장이 Expr(Constant(str))인 경우
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


def test_lifecycle은_광고계정에_쓰지_않는다():
    """계약 §4-C 공통: 슬라이스별 광고계정 쓰기 0(diff 증명)의 테스트판."""
    naver_ad = Path(__file__).resolve().parents[1] / "app" / "services" / "naver_ad"
    for name in ("exclusion_lifecycle.py", "exclusion_return_score.py"):
        code = _code_only(naver_ad / name)
        assert "naver_sa_writer" not in code, f"{name}이 광고계정 writer에 닿는다(계약 §3 금지선)"


def test_채점기는_원장을_쓰지_않는다():
    """북극성 §6-b M3: *"성적표는 «재는 자»이지 «돌리는 손»이 아니다."*
    채점기가 상태를 바꾸는 경로를 얻는 순간 그건 성적표가 아니라 승인 없는 자동 반영이 된다."""
    code = _code_only(Path(__file__).resolve().parents[1] / "app" / "services" / "naver_ad"
                      / "exclusion_return_score.py")
    for forbidden in (".status =", "NaverSearchTermExclusion(", "db.commit()", "db.add("):
        assert forbidden not in code, (
            f"채점기가 상태를 바꾸는 경로를 얻었다({forbidden!r}) — 북극성 §6-b M3 금지선"
        )


# ══════════════════════════════════════════════════════════════════════
# S3-b  복귀에는 복귀의 자 — 제외 성적표의 어휘가 복귀 행을 채점하면 안 된다
# ══════════════════════════════════════════════════════════════════════

def _return_entry(db, *, open_date: date, term=TERM, adgroup=ADGROUP):
    e = OpsDiaryEntry(
        event_type="execute", campaign_id=CAMPAIGN, adgroup_id=adgroup, actor="ss_exclude",
        target_type="search_term", target_id=term,
        action=exclusion_lifecycle.RETURN_OPEN_ACTION,
        # created_at은 UTC 저장 — diary_outcome._kst_date가 +9h 해서 KST 날짜를 얻는다.
        created_at=datetime.combine(open_date, datetime.min.time()) + timedelta(hours=3),
    )
    db.add(e)
    db.commit()
    return e


def _st_daily(db, day: date, *, source="shopping", imp=0, clk=0, cost=0, rev=0, term=TERM):
    db.add(NaverSearchTermDaily(
        ad_date=day, campaign_id=CAMPAIGN, adgroup_id=ADGROUP, search_term=term,
        source=source, imp=imp, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=1 if rev else 0, conv_purchase_amt=rev,
    ))
    db.commit()


def test_제외_성적표는_복귀_행을_채점하지_않는다(db):
    """★S3의 핵심 안전장치. `_st_window`의 status는 **비용 정지가 성공**인 자다
    (cost_total==0 → "stopped"). 복귀는 목적함수가 정반대라, 같은 자로 재면
    「복귀했는데 아무도 안 찾음」이 **성공**으로 뒤집혀 사슬에 들어간다 — 북극성 §7이
    이 트랙의 상습 실패 모드로 지목한 「브레이크 어휘로 액셀을 채점」이 정확히 이 모양이다.
    """
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    # 보고서는 있고 이 검색어만 없다 = d1_st라면 cost 0 → "stopped"(성공)로 찍힐 상황.
    _st_daily(db, open_date + timedelta(days=1), imp=10, clk=1, cost=500, term="다른검색어")

    counts = diary_outcome._backfill_row(db, entry, TODAY, 1.0)
    db.commit()
    outcome = json.loads(entry.outcome_json or "{}")
    assert "d1_st" not in outcome, (
        "복귀 일기가 제외 성적표(d1_st)로 채점됐다 — 부호가 반대라 학습 사슬이 "
        "「복귀하면 좋다」를 비용이 안 나서 배운다"
    )
    assert counts["d1_st"] == 0


def test_제외_행은_여전히_d1_st로_채점된다(db):
    """위 게이트가 **배제목록**이라 기존 제외 행의 채점은 한 글자도 안 바뀐다는 회귀 잠금.
    (허용목록으로 좁혔다면 action이 다른 blocked·reject 행이 조용히 d1_st를 잃었을 자리다.)"""
    action_date = TODAY - timedelta(days=10)
    entry = OpsDiaryEntry(
        event_type="execute", campaign_id=CAMPAIGN, adgroup_id=ADGROUP, actor="ss_exclude",
        target_type="search_term", target_id=TERM, action="exclude_search_term",
        created_at=datetime.combine(action_date, datetime.min.time()) + timedelta(hours=3),
    )
    db.add(entry)
    db.commit()
    _st_daily(db, action_date, imp=10, clk=2, cost=900)          # 기왕력(필요 source 판별)
    _st_daily(db, action_date + timedelta(days=1), imp=5, clk=0, cost=0, term="다른검색어")
    _st_daily(db, action_date + timedelta(days=1), source="expkeyword", imp=1, cost=0,
              term="다른검색어")

    diary_outcome._backfill_row(db, entry, TODAY, 1.0)
    db.commit()
    outcome = json.loads(entry.outcome_json or "{}")
    assert outcome["d1_st"]["status"] == "stopped"


@pytest.fixture
def bep(monkeypatch):
    monkeypatch.setattr(
        exclusion_return_score.campaign_target_resolver, "account_default_bep_roas",
        lambda db: 1.6760,
    )


def _score(db, entry, open_date):
    return exclusion_return_score.score_probation_window(db, entry, TODAY, open_date)


def test_창이_안_닫혔으면_키를_쓰지_않는다(db, bep):
    open_date = TODAY - timedelta(days=3)
    entry = _return_entry(db, open_date=open_date)
    assert _score(db, entry, open_date) is None  # 다음 스윕 재시도


def test_열었는데_아무도_안_찾으면_silent이지_성공이_아니다(db, bep):
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=3, clk=0, cost=0, term="다른검색어")

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_SILENT
    assert got["cost_total"] == 0
    # ★어휘가 제외 성적표와 겹치면 안 된다 — 겹치는 순간 소비처가 둘을 섞어 읽는다.
    assert got["status"] not in ("stopped", "leaking")


def test_보고서가_없으면_no_data이지_0이_아니다(db, bep):
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    assert _score(db, entry, open_date)["status"] == exclusion_return_score.STATUS_NO_DATA


def test_BEP_초과면_profitable_컷이_틀렸다는_실측(db, bep):
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_PROFITABLE
    assert got["roas"] == 3.0 and got["bep_roas"] == 1.676


def test_BEP_미달이면_unprofitable_컷이_옳았다(db, bep):
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=5_000)
    assert _score(db, entry, open_date)["status"] == exclusion_return_score.STATUS_UNPROFITABLE


def test_전환귀속_불가_source_단독_지출은_unverified다(db, bep):
    """expkeyword(WEB_SITE 계열)는 전환 귀속이 **원리적으로 부재**다(ref 64). 그 비용을 분모에
    넣으면 RoAS가 구조적으로 과소평가돼 「복귀는 늘 손해」가 데이터가 아니라 산식에서 나온다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), source="expkeyword", imp=50, clk=5, cost=7_000)

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_UNVERIFIED
    assert got["conv_scope"]["excluded_cost_no_attribution"] == 7_000


def test_BEP를_모르면_판정하지_않는다(db, monkeypatch):
    monkeypatch.setattr(
        exclusion_return_score.campaign_target_resolver, "account_default_bep_roas",
        lambda db: None,
    )
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)
    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_UNVERIFIED
    assert got["unverified_reason"] == "BEP 부재"


def test_소급_스윕이_복귀_행에_probation_키를_채운다(db, bep):
    """배선의 «닿는 층» — 채점 함수가 옳아도 스윕이 안 부르면 outcome_json은 영원히 빈다
    (교훈 #362: 만드는 층과 닿는 층은 다른 층이고, 합격기준이 지목한 건 늘 닿는 층)."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)

    totals = diary_outcome.backfill_outcomes(db, now=NOW)
    db.commit()
    outcome = json.loads(db.query(OpsDiaryEntry).filter(OpsDiaryEntry.id == entry.id)
                         .one().outcome_json or "{}")
    assert outcome["probation"]["status"] == exclusion_return_score.STATUS_PROFITABLE
    assert totals["probation_filled"] == 1
    assert totals["probation_silent"] == 0


# ══════════════════════════════════════════════════════════════════════
# 관측 표면 — 「배선이 죽었다」와 「아직 안 켰다」를 화면이 구분하는가
# ══════════════════════════════════════════════════════════════════════
# ★이 절이 있는 이유: n=58 적대 리뷰 1R·2R이 **둘 다 «Jino가 읽는 화면»의 결함**이었고,
#   내가 표면 변이를 서비스 층에만 걸어 **스크립트 출력은 테스트 0건**이었다(변이 M6·M7이
#   148 전건 초록으로 생존). 그래서 여기선 출력 «문장»을 직접 읽는다.

def _wiring_lines(db):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "exclusion_return_wiring",
        Path(__file__).resolve().parents[1] / "scripts" / "exclusion_return_wiring.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.format_report(exclusion_lifecycle.wiring_report(db))


def test_미점화면_0을_배선_고장이라_말하지_않는다(db):
    db.query(NaverCampaignSettings).update({"auto_operate": False}, synchronize_session=False)
    db.commit()
    text = "\n".join(_wiring_lines(db))
    assert "미점화" in text and "아직 안 켰다" in text
    assert "조사 대상" not in text


def test_점화되면_같은_0이_조사_대상으로_뒤집힌다(db):
    """★「관측 뒤에 문장」 규율의 증명. 고정 문단이었다면 이 테스트가 통과할 수 없다 —
    상태가 바뀌었는데 화면이 같은 말을 하면 그게 곧 n=58 1R의 결함이다."""
    text = "\n".join(_wiring_lines(db))  # fixture가 auto_operate=True로 세워 둔다
    assert "점화됨" in text and "조사 대상" in text
    assert "아직 안 켰다" not in text


def test_복귀_개방이_0건이면_성적표가_없다고_말한다(db):
    text = "\n".join(_wiring_lines(db))
    assert "채점할 대상이 없다" in text


def test_복귀_일기가_생기면_화면_카운트가_따라_움직인다(db, monkeypatch):
    """표면 변이 대비: 레인의 recorder 호출을 지우면 이 테스트가 죽는다(카운트가 안 움직인다)."""
    row = _excluded_row(db)
    monkeypatch.setattr(lane.naver_sa_writer, "get_adgroup_type", lambda adgroup_id: "WEB_SITE")
    monkeypatch.setattr(
        lane.naver_sa_writer, "delete_restricted_keywords",
        lambda a, i: type("R", (), {"before": [], "after": []})(),
    )
    assert lane._open_exclusion(db, row, NOW) is True

    report = exclusion_lifecycle.wiring_report(db)
    assert report["by_action"]["restore_search_term"]["count"] == 1
    assert report["return_open_total"] == 1
    text = "\n".join(_wiring_lines(db))
    assert "채점할 대상이 없다" not in text
    assert "미채점" in text  # 아직 소급 채점 전 — 「모른다」가 화면에 그대로 선다


# ══════════════════════════════════════════════════════════════════════
# 적대 리뷰 1R 상환 — P1 3건 + 생존 변이 6종의 회귀 잠금
# ══════════════════════════════════════════════════════════════════════

def test_화면이_관측한_숫자를_실제로_찍는다(db, bep):
    """★P1-1 상환. 리뷰어가 표면 변이 **3종을 전부 생존**시켰다 — probation 건수를 상수 0으로,
    복귀 개방 카운트를 상수 0으로, 「복귀 실험 일기」 렌더 블록을 통째로 제거해도 527건이
    전부 초록이었다. 테스트가 스크립트를 «읽기는» 했지만 **문단만 읽고 숫자는 안 읽었다**.
    n=58이 1R·2R 연속 FAIL을 맞은 그 층과 문자 그대로 같은 자리다."""
    open_date = TODAY - timedelta(days=30)
    _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)
    diary_outcome.backfill_outcomes(db, now=NOW)
    db.commit()

    text = "\n".join(_wiring_lines(db))
    # ① 렌더 블록이 존재한다(블록 제거 변이 사망)
    assert "복귀 실험 일기" in text and "exclude_search_term" in text
    # ② 복귀 개방 카운트가 관측값 1을 찍는다(상수 0 변이 사망)
    assert re.search(r"restore_search_term\s+1\b", text), text
    # ③ probation 분포가 관측값 1을 찍는다(상수 0 변이 사망)
    assert re.search(r"profitable\s+1\b", text), text


def test_귀속불가_지출이_판정분모보다_크면_보류한다(db, bep):
    """★P1-3 상환. 초판은 `shop_cost<=0`이면 보류하면서(브레이크 쪽 보수) `shop_cost>0`이면
    expkeyword 지출이 아무리 커도 shopping 안 RoAS만으로 `profitable`(=「컷이 틀렸다는 실측」)을
    단언했다 — **액셀 쪽으로만 관대**했다. 리뷰어 재현: shopping 10,000/매출 20,000 +
    expkeyword 75,000 ⇒ profitable(roas 2.0)인데 **총이익 −65,000원**. 이 모듈은 「브레이크
    어휘로 액셀을 채점하면 부호가 뒤집힌다」를 고치러 왔는데 새 자가 액셀 쪽에서 같은 부호
    오류를 냈다(북극성 §7 대칭)."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    day = open_date + timedelta(days=2)
    _st_daily(db, day, imp=100, clk=10, cost=10_000, rev=20_000)                    # shopping
    _st_daily(db, day, source="expkeyword", imp=500, clk=50, cost=75_000)           # 귀속 불가

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_UNVERIFIED, (
        f"총이익 −65,000원인 창을 {got['status']!r}로 판정했다 — 액셀 쪽 부호 오류"
    )
    assert "88%" in got["unverified_reason"]
    # 화면이 그 사유를 스스로 말한다(P1-1과 같은 규율 — 숫자가 화면에 닿는지까지 본다)
    diary_outcome.backfill_outcomes(db, now=NOW)
    db.commit()
    assert "unverified 사유별" in "\n".join(_wiring_lines(db))


def test_귀속불가_지출이_분모보다_작으면_판정한다(db, bep):
    """대칭의 반대쪽 — 보류가 «모든 것을 보류»가 되면 그것도 자가 죽은 것이다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    day = open_date + timedelta(days=2)
    _st_daily(db, day, imp=100, clk=10, cost=10_000, rev=30_000)
    _st_daily(db, day, source="expkeyword", imp=20, clk=2, cost=3_000)
    assert _score(db, entry, open_date)["status"] == exclusion_return_score.STATUS_PROFITABLE


def test_복귀행은_지혜_성적표의_absent에_들어가지_않는다(db, bep):
    """★P1-2 상환. 복귀 행은 `d1_st`가 **원리적으로 영원히** 안 채워지는데,
    `wisdom_scorecard._search_term_material`이 그런 행을 `absent`로 셌다 — 그 버킷의 뜻은
    「아직 안 왔을 뿐 언젠가 올 것」이고, 그게 거짓이라 2026-08-25에 `not_harvestable`을
    따로 가른 것이다. 규모: 일일 복귀 캡 10 × harvest 창 90일 ⇒ 최대 ~860행이 상시 눌러앉는다.
    이 값은 콘솔(NaverAdOptimizationConsole)에 그대로 그려진다."""
    from app.services.naver_ad import wisdom_scorecard

    open_date = TODAY - timedelta(days=30)
    _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)
    diary_outcome.backfill_outcomes(db, now=NOW)
    db.commit()

    mat = wisdom_scorecard._search_term_material(db)
    assert mat["by_status"]["absent"] == 0, "복귀 행이 absent에 계상됐다 — 영영 안 오는 것을 곧 올 것처럼 센다"
    assert mat["by_status"]["return_experiment"] == 1
    assert "수확 대상 0건" in mat["label"] and "복귀 실험 1건" in mat["label"]


def test_수확_카운터도_복귀행을_no_d1_st로_세지_않는다(db, bep):
    """P1-2의 짝 — `wisdom_candidates`의 `skipped_search_term_no_d1_st`는 주석이
    「아직 스윕 전」이라 같은 거짓을 센다."""
    from app.services.naver_ad import wisdom_candidates

    open_date = TODAY - timedelta(days=30)
    _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)
    diary_outcome.backfill_outcomes(db, now=NOW)
    db.commit()

    totals = wisdom_candidates.harvest_candidates(db, now=NOW)
    assert totals["skipped_search_term_no_d1_st"] == 0
    assert totals["skipped_search_term_return_experiment"] == 1


def test_관찰창_경계가_고정돼_있다(db, bep):
    """생존 변이 M-L1(`open+1`→`open`, 제외구간 혼입)·M-L2(`open+14`→`open+21`) 상환.
    창 경계를 검증하는 assert가 하나도 없었다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    got = _score(db, entry, open_date)
    assert got["window"] == {
        "from": (open_date + timedelta(days=1)).isoformat(),   # 개방 당일은 제외구간이 섞인다
        "to": (open_date + timedelta(days=14)).isoformat(),    # _PROBATION_DAYS
        "days": 14,
    }


def test_BEP_경계에서_profitable이다(db, bep):
    """생존 변이 M-L5(`>=`→`>`) 상환 — 분기점 자체가 테스트에 없었다.
    RoAS == BEP는 손익분기 «도달»이므로 `profitable` 쪽이다(BEP의 정의)."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=16_760)
    got = _score(db, entry, open_date)
    assert got["roas"] == 1.676 and got["bep_roas"] == 1.676
    assert got["status"] == exclusion_return_score.STATUS_PROFITABLE


def test_재제외_갈래도_recorder를_지난다(db, monkeypatch):
    """생존 변이 M-D2 상환. `record_return_settled` 주석이 *"인구조사 테스트가 「종료 전이는
    예외 없이 이 모듈을 지난다」를 셀 수 있어야 하기 때문"*이라 no-op 호출을 정당화하는데,
    **그걸 세는 테스트가 없었다** — 지우면 전건 초록이었다. 주석이 약속한 것을 여기서 지킨다.

    `_autofire_exclude`(harness 경로)는 이 테스트의 대상 층이 아니라 대역을 쓴다 — 여기서 보는
    것은 「레인이 종료 전이를 recorder에 통과시키는가」뿐이다."""
    row = _excluded_row(db, status="probation")
    row.probation_until = TODAY - timedelta(days=1)
    db.commit()
    monkeypatch.setattr(lane, "_autofire_exclude", lambda db_, cand, now: object())
    seen: list[str] = []
    real = exclusion_lifecycle.record_return_settled
    monkeypatch.setattr(
        lane.exclusion_lifecycle, "record_return_settled",
        lambda db_, r, *, verdict, now: (seen.append(verdict), real(db_, r, verdict=verdict, now=now))[1],
    )

    cand = {"adgroup_id": ADGROUP, "search_term": TERM, "campaign_id": CAMPAIGN,
            "reason": "여전히 손실", "cost": 1000}
    res = lane._run_reexamination(db, [cand], NOW)

    assert res["reexcluded"] == 1
    assert seen == [exclusion_lifecycle.VERDICT_REEXCLUDED]
    # 그리고 recorder는 no-op이어야 한다(harness가 이미 남긴다 — 이중 계상 금지)
    assert _diaries(db, action=exclusion_lifecycle.RETURN_SETTLED_ACTION) == []


# ══════════════════════════════════════════════════════════════════════
# 적대 리뷰 2R P2 상환 (2R 판정은 PASS — 아래는 라운드를 늘리지 않는 트리아지 채택분)
# ══════════════════════════════════════════════════════════════════════

def test_부호가_갈리는_구간은_판정하지_않는다(db, bep):
    """★P2-A 상환. 1R 수정(`outside > shop_cost`)은 오차를 **유계로** 만들었을 뿐 없애지
    못했다 — 리뷰어 재현: shopping 10,000/매출 20,000 + expkeyword 10,000이면 `outside ==
    shop_cost`라 보류 규칙에 안 걸리는데, 판정은 `profitable`(roas 2.0)이고 **창 전체 비용
    기준 총 RoAS는 1.0 < BEP**다. 이 슬라이스의 목적이 «부호 뒤집힘 제거»라 남은 구간도 닫는다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    day = open_date + timedelta(days=2)
    _st_daily(db, day, imp=100, clk=10, cost=10_000, rev=20_000)
    _st_daily(db, day, source="expkeyword", imp=50, clk=5, cost=10_000)

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_UNVERIFIED, (
        f"총 RoAS 1.0 < BEP인 창을 {got['status']!r}로 판정했다 — 부호가 뒤집힌 채 사슬에 들어간다"
    )
    assert "하한" in got["unverified_reason"]


def test_부호가_안_갈리면_경계에서도_판정한다(db, bep):
    """★P2-B 상환 — 생존 변이 MN-3(`outside > shop_cost` → `>=`)를 잡는다. 그리고 보류가
    「모든 것을 보류」로 번지지 않는지도 같이 지킨다(자가 죽으면 그것도 실패다).
    여기선 `outside == shop_cost`인데 총 RoAS 2.0 ≥ BEP라 부호가 안 갈린다 ⇒ 판정해야 한다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    day = open_date + timedelta(days=2)
    _st_daily(db, day, imp=100, clk=10, cost=10_000, rev=40_000)
    _st_daily(db, day, source="expkeyword", imp=50, clk=5, cost=10_000)

    got = _score(db, entry, open_date)
    assert got["status"] == exclusion_return_score.STATUS_PROFITABLE
    assert got["roas"] == 4.0


def test_일기_실패해도_레인이_죽지_않는다(db):
    """★P2-C 상환 — 생존 변이 MN-9. 초판은 except 절에서 `row.adgroup_id`를 **다시 읽었다**.
    실패 원인이 바로 그 row의 refresh 실패(경합 삭제·쓰기락)면 except 안에서 예외가 재발해
    밖으로 새고, 호출부엔 try가 없어 레인이 죽는다 — fail-open이 fail-loud가 된다."""
    real_row = _excluded_row(db, status="probation")
    state = {"boom": False}

    def _raise(*a, **k):
        state["boom"] = True
        raise RuntimeError("diary 세션 실패")

    class _Proxy:
        """일기 호출이 터진 «뒤»에 row를 다시 읽으면 폭발한다(ObjectDeletedError 모사)."""

        def __getattr__(self, name):
            if state["boom"] and name in ("adgroup_id", "search_term", "cycle"):
                raise RuntimeError("except가 row를 다시 읽었다 — fail-open이 깨졌다")
            return getattr(real_row, name)

    import app.services.naver_ad.diary as diary_mod

    orig = diary_mod.write_diary_entry
    try:
        diary_mod.write_diary_entry = _raise
        exclusion_lifecycle.record_return_settled(  # 예외가 새면 이 줄에서 테스트가 죽는다
            db, _Proxy(), verdict=exclusion_lifecycle.VERDICT_RESTORED, now=NOW,
        )
    finally:
        diary_mod.write_diary_entry = orig
    assert state["boom"] is True  # 실제로 실패 경로를 밟았는지 확인(가짜 통과 방지)


def test_반성_프롬프트가_복귀_어휘를_설명한다():
    """★P2-D 상환 — 생존 변이 MN-12. 사슬 「일기→소급채점→**반성**→지혜」의 세 번째 고리가
    새 키를 모르면, 반성이 복귀 행을 제외 어휘로 서술한다. 이 repo엔 선례가 있다 —
    `test_naver_wisdom.py`가 `wisdom_judge._SYSTEM` 문구를 같은 방식으로 못 박는다."""
    from app.services.naver_ad import diary_reflection

    s = diary_reflection._SYSTEM
    assert "probation" in s
    assert "silent" in s and "정보를 못 낸 것" in s  # 성공으로 서술하지 말 것
    assert "profitable" in s and "unprofitable" in s


def test_관측일수가_함께_실린다(db, bep):
    """★P2-E 상환 — 생존 변이 MN-13. `present`는 창 14일 중 **하루만** 보고서가 있어도 True다.
    관측일수를 안 실으면 1/14일짜리 부분 합계를 완결값처럼 읽는다."""
    open_date = TODAY - timedelta(days=30)
    entry = _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=100, clk=10, cost=10_000, rev=30_000)

    shop = _score(db, entry, open_date)["by_source"]["shopping"]
    assert shop["observed_days"] == 1 and shop["window_days"] == 14


def test_silent_건수가_따로_세어진다(db, bep):
    """「열었는데 아무 일도 안 일어났다」가 몇 건인지가 곧 «복귀 실험이 정보를 내고 있는가»의
    지표다 — 0건 성적표를 초록으로 착각하지 않기 위한 카운터(S1의 「결손 0도 기록」과 같은 규율)."""
    open_date = TODAY - timedelta(days=30)
    _return_entry(db, open_date=open_date)
    _st_daily(db, open_date + timedelta(days=2), imp=3, clk=0, cost=0, term="다른검색어")

    totals = diary_outcome.backfill_outcomes(db, now=NOW)
    assert totals["probation_filled"] == 1 and totals["probation_silent"] == 1
