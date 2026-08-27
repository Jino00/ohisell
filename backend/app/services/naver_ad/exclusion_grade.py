"""제외 «임대» 등급 — 제외 원장 행의 등급·만료일 단일 결정 지점 (S2).

계약: `docs/contracts/CONTRACT_ignition_readiness.md` §4-A S2 · §4-B⑥⑦ · §4-C S2-a/S2-b

## 왜 이 모듈이 «단일» 지점이어야 하나

착수 실측(2026-08-27, 세션 pao-n58)에서 계약의 전제가 하나 반증됐다. 계약 §4-C S2-b는 신규
제외 경로를 「autofire·Confirm」 **둘**로 적었는데 실제로는 **넷**이었다:

  ① `search_term_execution.record_execution`      — 사람이 콘솔에서 실행한 것을 보고
  ② `search_term_execution.import_console_exclusions` — 콘솔에 이미 걸린 것을 일괄 편입
  ③ `search_term_ss_lane._upsert_exclusion`        — autofire 실쓰기 성공 후
  ④ `search_term_ss_lane._reconcile_orphan_exclusions` — 크래시 고아 자가 치유

그리고 재심사 백오프 `min(30×cycle, 90)`가 **세 곳에 따로** 구현돼 있었다(상수 1곳 +
리터럴 2곳, 그중 ④는 `cycle`을 무시한 `+30` 고정). 지금은 cycle이 전건 1이라 결과가 같아서
아무도 못 봤을 뿐, 재제외가 시작되면 같은 상태 전이가 경로마다 다른 날짜를 낳는다.

⇒ 「등급 칸을 더한다」로 고치면 **다섯 번째 경로가 생기는 날 같은 병이 재발한다**(교훈:
같은 결함이 두 번이면 모양을 고쳐라). 그래서 이 모듈이 **행을 만드는 유일한 입구**이고,
`test_exclusion_grade.py`의 인구조사 테스트가 이 파일 밖의 `NaverSearchTermExclusion(`
직접 호출을 0으로 못 박는다. 등급 없는 행은 «만들 수 없다»가 S2-b의 뜻이다.

## 등급과 만료일 (계약 §4-B⑥ 원문 그대로 — 여기서 새 정책을 만들지 않는다)

| 등급 | 뜻 | 기본 만료(`next_review_at`) |
|---|---|---|
| 무관 | 상품과 무관(경쟁사 브랜드 등 — Jino 판별분 포함) | **영구**(NULL) |
| 광의 | 관련은 있으나 우리 제품군 밖·과광폭 | +90일 (백오프 상한 재사용) |
| 성과미달 | 관련 있으나 창 내 확정 손해 | +min(30×cycle, 90)일 (기존 백오프 그대로) |
| 미검증 | 판정 근거 없음 | **보류**(NULL) — 재료 도래 시 재분류 |
| 오컷의심 | 제외 후에도 남은 증거가 BEP 초과 | **즉시 도래**(오늘) |

★무관과 미검증은 둘 다 `next_review_at=NULL`이다 — **그래서 `grade` 칸이 필요하다.**
NULL만 보면 「영영 안 볼 것」과 「나중에 볼 것」이 같은 모양이다.

★기존 행위 불변: 신규 제외는 전부 `성과미달`(근거 있는 컷)이라 만료일 계산이 종전과
글자 그대로 같다. 편입분은 `미검증`이라 종전처럼 NULL이다. **S2는 라벨을 더할 뿐 재개방
시점을 하루도 옮기지 않는다** — 옮기면 그건 계약 §1 「안 하는 것」 ⑥(재개방 실행)이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from ...models import NaverSearchTermDaily, NaverSearchTermExclusion

log = logging.getLogger(__name__)

# ── 등급 어휘 (계약 §4-B⑥) ──────────────────────────────────────────
GRADE_IRRELEVANT = "무관"
GRADE_BROAD = "광의"
GRADE_UNDERPERFORM = "성과미달"
GRADE_UNVERIFIED = "미검증"
GRADE_MISCUT = "오컷의심"

ALL_GRADES: tuple[str, ...] = (
    GRADE_IRRELEVANT, GRADE_BROAD, GRADE_UNDERPERFORM, GRADE_UNVERIFIED, GRADE_MISCUT,
)

# ── 재심사 백오프 — 값의 «정본»이 여기로 모인다 ────────────────────────
# ★값은 기존 그대로다(cycle당 30일·상한 90일, PX 상태기계 관례). 새 정책을 만들지 않았고
#   숫자를 바꾸지도 않았다(계약 §2-3) — **정의 자리만** 옮겼다. 옮긴 이유는 실측이다:
#   같은 식이 `search_term_execution`(상수) · `search_term_ss_lane`(리터럴 2곳, 그중
#   고아 치유는 `cycle`을 무시한 `+30` 고정)으로 **세 벌** 있었다. 지금은 cycle이 전건 1이라
#   결과가 우연히 같을 뿐이고, 재제외가 시작되는 순간 경로마다 다른 날짜가 나온다.
#   `search_term_execution`은 이 이름을 되읽어 기존 참조를 그대로 유지한다(호환).
REVIEW_BACKOFF_DAYS = 30
REVIEW_BACKOFF_MAX = 90

# 버킷 경계 — 계약 부록 [E]가 419건을 가른 그 경계를 **그대로** 쓴다(새 숫자 아님).
#   21 = 산업 표준 통계 컷([E]) · 10 = 쇼핑 제외 게이트 현행값
_BUCKET_B_MIN_CLK = 21
_BUCKET_C_MIN_CLK = 10


class ExclusionGradeError(ValueError):
    """등급 없이 / 모르는 등급으로 원장 행을 만들려 한 것. 조용히 넘기지 않는다.

    ★왜 예외인가: 이 원장에는 삭제 라우트가 없고 행은 배너·성적표·SS레인·학습 사슬에
      영구히 남는다(`ExclusionInputError`와 같은 이유). 등급 없는 행이 한 번 들어가면
      재개방 로직이 그 행을 영영 못 판정한다 — 입구에서 막는 것이 싸다.
    """


def default_next_review_at(grade: str, *, cycle: int, today: date) -> date | None:
    """등급이 정하는 기본 만료일 (계약 §4-B⑥ 표 그대로).

    ★`성과미달`의 식이 기존 백오프와 «같아야» 한다 — 다르면 S2가 재개방 시점을 옮긴 것이고,
      그건 계약 §1 「안 하는 것」이다.
    """
    if grade == GRADE_UNDERPERFORM:
        return today + timedelta(days=min(REVIEW_BACKOFF_DAYS * max(cycle, 1), REVIEW_BACKOFF_MAX))
    if grade == GRADE_BROAD:
        return today + timedelta(days=REVIEW_BACKOFF_MAX)
    if grade == GRADE_MISCUT:
        return today  # 즉시 도래 — 단 «대상 목록»에 오를 뿐, 실행은 소유권 분리 후(계약 §1-2)
    if grade in (GRADE_IRRELEVANT, GRADE_UNVERIFIED):
        return None  # 영구 / 보류 — 뜻은 다르고 구분은 `grade`가 진다
    raise ExclusionGradeError(f"모르는 등급: {grade!r} (허용: {ALL_GRADES})")


def _require_grade(grade: str) -> str:
    if grade not in ALL_GRADES:
        raise ExclusionGradeError(f"모르는 등급: {grade!r} (허용: {ALL_GRADES})")
    return grade


def set_grade(
    row: NaverSearchTermExclusion,
    grade: str,
    *,
    cycle: int,
    today: date,
    reason: str | None = None,
    keep_review_at: bool = False,
) -> None:
    """기존 행에 등급을 (재)부여하고 만료일을 등급 규칙으로 다시 계산한다.

    Args:
        keep_review_at: True면 `next_review_at`을 건드리지 않는다 — **백필 전용**이다.
            소급 라벨링은 라벨만 붙이는 것이고(계약 §2-5 "대행사 칸에는 라벨만 붙이고
            실행하지 않는다"), 만료일까지 새로 쓰면 3,987행의 재개방 예정일이 하루아침에
            생겨 그게 곧 재개방 «실행»의 예약이 된다.
    """
    _require_grade(grade)
    row.grade = grade
    row.grade_reason = reason
    if not keep_review_at:
        row.next_review_at = default_next_review_at(grade, cycle=cycle, today=today)


def new_exclusion(
    *,
    campaign_id: str,
    adgroup_id: str,
    search_term: str,
    grade: str,
    now: datetime,
    cycle: int = 1,
    status: str = "excluded",
    restrict_kwd_id: str | None = None,
    cost_at_exclusion: int = 0,
    source: str | None = None,
    console_excluded_at: datetime | None = None,
    grade_reason: str | None = None,
) -> NaverSearchTermExclusion:
    """제외 원장 행을 만드는 **유일한** 입구 (S2-b).

    ★`grade`는 키워드 «필수» 인자다 — 기본값을 주면 호출부가 생각 없이 통과하고, 그 순간
      이 모듈은 장식이 된다. 모르면 `GRADE_UNVERIFIED`를 **명시적으로** 넘겨라.
    """
    _require_grade(grade)
    return NaverSearchTermExclusion(
        campaign_id=campaign_id,
        adgroup_id=adgroup_id,
        search_term=search_term,
        restrict_kwd_id=restrict_kwd_id,
        status=status,
        cycle=cycle,
        excluded_at=now,
        last_transition_at=now,
        next_review_at=default_next_review_at(grade, cycle=cycle, today=now.date()),
        probation_until=None,
        cost_at_exclusion=cost_at_exclusion,
        source=source,
        console_excluded_at=console_excluded_at,
        grade=grade,
        grade_reason=grade_reason,
    )


# ══════════════════════════════════════════════════════════════════════════
# 소급 백필 (계약 §4-B⑦)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Evidence:
    """한 (adgroup, term)의 **전 기간** 성과 합계 — 백필 분류의 유일한 입력."""

    clk: int
    cost: int
    conv: int
    revenue: int
    has_history: bool


def classify(ev: Evidence, *, bep_roas: float | None) -> tuple[str, str]:
    """증거 → (등급, 근거 문자열). 계약 §4-B⑦ 부여 규칙 + 실측이 드러낸 사각 1건.

    계약 §4-B⑦ 원문:
      A급 16건 중 BEP 초과 13 → 오컷의심 / BEP 미달 2 + B 1 + C 3 → 성과미달 /
      D 264 + E 135 + 이력 없음 3,571 → 미검증 / 무관·광의는 백필에서 부여하지 않는다

    ★★계약이 못 본 사각 — A급 16건인데 「BEP 초과 13 + 미달 2」로 **15만 설명된다.**
      13+6+3,970 = **3,989**로 원장 3,990과 1건이 어긋나는 것이 그 자국이다(계약 §4-C S2-a가
      "불일치는 그 자체를 표면화한다"고 스스로 요구한 그 불일치). 실측으로 특정한 그 1건은
      `id=579`(`지문방지필름`) — **clk=0 · cost=0 · conv=1 · 매출 12,900원**이라 RoAS가
      0으로 나뉘어 «초과»에도 «미달»에도 안 들어간다. 전 원장에서 이 모양은 이 1건뿐이다.

      처분: **오컷의심**. 목적함수가 RoAS가 아니라 **총이익 절대액**(D-NAO-59)이므로
      «비용 0에 매출 발생»은 총이익 양수 = BEP 초과와 동치다. 그리고 이 행은 08-17에
      제외됐는데 증거(08-24·25)가 **전부 제외 «후»**라, 계약 §4-B⑥의 오컷의심 정의
      *"제외 후에도 남은 증거가 BEP 초과"*에 문자 그대로 걸린다.
      ★새 상수를 만들지 않았다 — 기존 목적함수를 적용했을 뿐이다(계약 §2-3).
      ★오컷의심은 재심사 «대상 목록»에 오를 뿐이고 실행은 소유권 분리 후라(계약 §1-2),
        이 판단이 틀려도 광고 계정에는 아무 일도 일어나지 않는다.
    """
    if not ev.has_history:
        return GRADE_UNVERIFIED, "backfill:이력없음 — 성과 행 0(판정 근거 없음)"

    if ev.conv >= 1:
        if ev.cost == 0:
            return (
                GRADE_MISCUT,
                f"backfill:A-비용0 — cost=0·conv={ev.conv}·매출={ev.revenue}원으로 RoAS 미정의. "
                "총이익 절대액(D-NAO-59) 기준 비용0·매출>0은 BEP 초과와 동치 ⇒ 오컷의심. "
                "★계약 §4-B⑦의 「13+2=15」가 A급 16건을 다 못 덮던 그 1건",
            )
        if bep_roas is None:
            return (
                GRADE_UNVERIFIED,
                f"backfill:A-BEP미상 — conv={ev.conv}인데 계정 기본 BEP를 못 구해 초과/미달 판정 불가",
            )
        roas = ev.revenue / ev.cost
        if roas > bep_roas:
            return (
                GRADE_MISCUT,
                f"backfill:A-BEP초과 — RoAS={roas:.3f} > BEP={bep_roas:.4f} (cost={ev.cost}·매출={ev.revenue})",
            )
        return (
            GRADE_UNDERPERFORM,
            f"backfill:A-BEP미달 — RoAS={roas:.3f} ≤ BEP={bep_roas:.4f} (컷이 옳았다)",
        )

    # conv == 0 — 클릭 수가 표본 충분성을 가른다(계약 부록 [E]의 B/C/D/E 버킷 그대로)
    if ev.clk >= _BUCKET_B_MIN_CLK:
        return GRADE_UNDERPERFORM, f"backfill:B — clk={ev.clk}(≥{_BUCKET_B_MIN_CLK} 산업표준 통계컷)·전환0"
    if ev.clk >= _BUCKET_C_MIN_CLK:
        return GRADE_UNDERPERFORM, f"backfill:C — clk={ev.clk}·전환0"
    if ev.clk >= 1:
        return GRADE_UNVERIFIED, f"backfill:D — clk={ev.clk}(표본 미달)·전환0"
    return GRADE_UNVERIFIED, "backfill:E — clk=0·전환0(노출만, 판정 근거 없음)"


def _evidence_map(db: Session) -> dict[tuple[str, str], Evidence]:
    """원장에 있는 (adgroup, term) 전건의 전 기간 성과 합계를 한 번에 뜬다.

    ★한 방에 뜨는 이유: 3,990행을 행마다 조회하면 prod에서 3,990 쿼리가 되고, 그건 읽기
      전용이어도 배포 중 부하다. 계약 §5(예산)의 「신규 상시 비용 0」과 같은 결.
    """
    rows = (
        db.query(
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_amt), 0),
        )
        .group_by(NaverSearchTermDaily.adgroup_id, NaverSearchTermDaily.search_term)
        .all()
    )
    return {
        (adgroup_id, term): Evidence(
            clk=int(clk), cost=int(cost), conv=int(conv), revenue=int(rev), has_history=True
        )
        for adgroup_id, term, clk, cost, conv, rev in rows
    }


_NO_HISTORY = Evidence(clk=0, cost=0, conv=0, revenue=0, has_history=False)


def backfill(db: Session, *, today: date, only_missing: bool = True, commit: bool = True) -> dict:
    """제외 원장 전건에 등급을 소급 부여한다 (계약 §4-A S2 「백필 3,990행」).

    ★`next_review_at`은 건드리지 않는다(`keep_review_at=True`) — 라벨링이지 실행이 아니다.
      만료일까지 새로 쓰면 3,987행에 재개방 예정일이 한꺼번에 생기고, 그건 계약 §1 「안 하는
      것」 ⑥(재개방 실행)의 예약에 해당한다. 광고 계정 무접촉의 실질이 여기 있다.

    ★`only_missing=True`(기본)라 **재실행해도 이미 붙은 등급을 덮지 않는다** — 사람이 나중에
      손으로 «무관»을 찍은 행을 백필이 «미검증»으로 되돌리면 그게 판단의 소실이다.

    반환: {"total", "graded", "skipped", "bep_roas", "distribution", "expected", "deviation"}
    """
    # 계정 기본 BEP — 기존 해석기를 쓴다(새 상수 발명 금지, 계약 §2-3).
    from . import campaign_target_resolver

    bep_dec = campaign_target_resolver.account_default_bep_roas(db)
    bep_roas = float(bep_dec) if bep_dec is not None else None
    if bep_roas is None:
        log.warning(
            "[제외등급] 계정 기본 BEP를 못 구했다 — A급(전환 있음) 행은 «미검증»으로 남긴다. "
            "추정해서 채우지 않는다(전역 §3 추정 금지)."
        )

    evidence = _evidence_map(db)
    q = db.query(NaverSearchTermExclusion)
    if only_missing:
        q = q.filter(NaverSearchTermExclusion.grade.is_(None))

    graded = 0
    distribution: dict[str, int] = {}
    for row in q.all():
        ev = evidence.get((row.adgroup_id, row.search_term), _NO_HISTORY)
        grade, reason = classify(ev, bep_roas=bep_roas)
        set_grade(row, grade, cycle=row.cycle, today=today, reason=reason, keep_review_at=True)
        distribution[grade] = distribution.get(grade, 0) + 1
        graded += 1

    if commit:
        db.commit()

    total = db.query(NaverSearchTermExclusion).count()
    skipped = total - graded if only_missing else 0

    # 계약 §4-C S2-a: 「[E]와 다르면 다른 이유가 함께 출력·기록」 — 기대치를 코드가 들고 있어야
    # 대조가 자동으로 된다. 사람이 눈으로 비교하게 두면 그 대조는 다음 세션에 사라진다.
    deviation = {
        g: distribution.get(g, 0) - n
        for g, n in CONTRACT_EXPECTED.items()
        if distribution.get(g, 0) != n
    }
    return {
        "total": total,
        "graded": graded,
        "skipped": skipped,
        "bep_roas": bep_roas,
        "distribution": distribution,
        "expected": dict(CONTRACT_EXPECTED),
        "deviation": deviation,
    }


# 계약 §4-C S2-a가 못 박은 기대 분포. ★합이 3,989로 원장 3,990과 1 어긋나는 것이 계약
#   자신의 사각이고(위 `classify` 주석), 실제 산출은 오컷의심 14로 나와 합이 3,990이 된다.
#   기대치를 «고쳐 적지» 않는다 — 계약 원문을 그대로 두고 차이를 표면화하는 것이 §4-C의 요구다.
CONTRACT_EXPECTED: dict[str, int] = {
    GRADE_MISCUT: 13,
    GRADE_UNDERPERFORM: 6,
    GRADE_UNVERIFIED: 3970,
}


def distribution_report(db: Session) -> dict:
    """등급 분포 + 계약 기대치 대조 + 이탈 사유 (계약 §4-C S2-a의 «Jino가 보는 것»).

    읽기 전용이다 — 관측이 상태를 바꾸면 그건 관측이 아니다.
    """
    rows = (
        db.query(NaverSearchTermExclusion.grade, sqlfunc.count(NaverSearchTermExclusion.id))
        .group_by(NaverSearchTermExclusion.grade)
        .all()
    )
    distribution = {(g or "(미분류)"): int(n) for g, n in rows}
    total = sum(distribution.values())
    deviation = {
        g: {"actual": distribution.get(g, 0), "expected": n, "diff": distribution.get(g, 0) - n}
        for g, n in CONTRACT_EXPECTED.items()
        if distribution.get(g, 0) != n
    }
    # 이탈을 낳은 «행»의 사유를 같이 싣는다 — 숫자만 다르면 다음 세션이 원인을 다시 판다.
    deviation_rows = [
        {"id": r.id, "search_term": r.search_term, "grade": r.grade, "reason": r.grade_reason}
        for r in db.query(NaverSearchTermExclusion)
        .filter(NaverSearchTermExclusion.grade_reason.like("backfill:A-비용0%"))
        .all()
    ]
    return {
        "total": total,
        "distribution": distribution,
        "expected": dict(CONTRACT_EXPECTED),
        "expected_sum": sum(CONTRACT_EXPECTED.values()),
        "deviation": deviation,
        "deviation_rows": deviation_rows,
    }
