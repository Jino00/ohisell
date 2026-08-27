# exclusion_lifecycle.py — 제외 원장 «상태 전이»가 학습 사슬에 타는 단일 지점
#   (D-NAO-259 후속 · 계약 docs/contracts/CONTRACT_ignition_readiness.md §4-A S3, 결손 #11·#12)
#
# ── 왜 이 모듈이 있나 ──────────────────────────────────────────────────────────
# 검색어 제외의 상태기계는 excluded → (개방) → probation → (재판정) → excluded | restored 다.
# 이 중 **네이버에 실제로 쓰는 두 전이**의 기록 경로가 서로 달랐다:
#
#   재제외(_autofire_exclude)  → naver_execution_harness.execute() → execute 일기 ✅
#   재개방(_open_exclusion)    → naver_sa_writer.delete_* 직접 호출 → **일기 0건** ❌
#
# 재개방이 harness를 안 타는 것은 사고가 아니라 설계다(제안 없이 상태기계가 스스로 여는
# 전이라 proposal_id가 없다). 그래서 harness에 태우는 대신 **일기를 남기는 자리를 여기 하나로
# 모은다** — 계약 §4-C S3-a가 요구한 "harness 우회 경로 포함"의 집행 지점이다.
#
# ★모양을 이렇게 잡은 이유(D-NAO-259에서 배운 것의 재적용): 「호출부마다 일기 한 줄을 더한다」로
#   고치면 **다음에 생기는 전이가 또 조용히 빠진다.** 행이 태어나는 자리를 하나로 묶고, 인구조사
#   테스트가 그 밖의 직접 호출을 0으로 못 박는 편이 같은 병의 재발을 구조로 막는다
#   (`backend/tests/test_exclusion_lifecycle.py::test_no_diaryless_transition_outside_this_module`).
#
# ── 쓰기 범위 ────────────────────────────────────────────────────────────────
# 이 모듈은 **일기(ops_diary_entries)만 쓴다.** 제외 원장(status/cycle/next_review_at)·네이버
# 광고계정·제안 어디에도 쓰지 않는다. 기록층이 조치를 바꾸면 기록이 아니다
# (exclusion_survival.py 모듈 주석과 같은 규율).
#
# ── 계약 §3 금지선과의 관계 ───────────────────────────────────────────────────
# "재개방·제외 발사·probation 전이의 «실행» 0건 — S3는 배선까지이고, 실행 게이트(auto_operate)는
# 닫혀 있다." 이 모듈은 실행 게이트를 만지지 않는다. 라이브 행 생성은 점화 «후»에만 일어나므로
# 완료 QA는 이 배선을 「존재 확인 + 라이브 판정불능」으로 남긴다(계약 §4-C S3-a 원문).
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverSearchTermExclusion
from app.services.naver_ad import diary, search_term_judge

log = logging.getLogger(__name__)

# ── change_log / 일기 action 토큰 ────────────────────────────────────────────
# ★RETURN_OPEN_ACTION의 «정의 자리»를 여기로 모은다 — search_term_ss_lane._RESTORE_ACTION은
#   이 이름을 되읽는다(값 불변, 호환). D-NAO-259가 재심사 백오프를 세 벌에서 한 벌로 모은 것과
#   같은 이유다: 같은 규칙이 여러 곳에 복제되면 그중 하나만 고쳐지는 날이 온다.
RETURN_OPEN_ACTION = "restore_search_term"

# 복귀 실험의 «종료» 전이. 개방(RETURN_OPEN_ACTION)이 실험의 시작이고 이것이 끝이다.
# 재제외로 끝나는 경우는 harness가 exclude_search_term execute 일기를 이미 남기므로 여기서
# 다시 남기지 않는다(같은 사건 두 줄 = 소급 채점 이중 계상).
RETURN_SETTLED_ACTION = "settle_search_term_return"

# 복귀 판정 어휘 — 실험이 어떻게 끝났는가.
VERDICT_RESTORED = "restored"      # 더는 §1 후보가 아님 → 복귀 확정(제외 해제 유지)
VERDICT_REEXCLUDED = "reexcluded"  # 여전히 후보 → 재제외(이 경우 일기는 harness 몫)

# 복귀 «방향»의 action 집합 — 제외 성적표(diary_outcome.d1_st)가 **손대면 안 되는** 대상.
# ★`_st_window`의 status(stopped/leaking)는 **비용 정지가 성공**인 자다. 복귀는 목적함수가
#   정반대(돈이 다시 나가고 그 위에서 총이익이 나는지를 본다)라 같은 자로 재면
#   「복귀했는데 아무도 안 찾음(cost=0)」이 **stopped=성공**으로 뒤집혀 기록된다. 북극성 §7이
#   이 트랙의 상습 실패 모드로 지목한 「브레이크 어휘로 액셀을 채점」이 정확히 이 모양이다.
#
# ★★게이트를 «허용목록(제외 action만 통과)»이 아니라 «배제목록»으로 쓰는 이유:
#   d1_st는 execute뿐 아니라 blocked·reject·kill_switch 행에도 붙는다(diary_outcome.EVENT_TYPES).
#   그 행들의 action 값은 경로마다 다르고 None일 수도 있어, 허용목록으로 좁히면 **기존 행이 조용히
#   d1_st를 잃는다.** 배제목록은 그 위험이 0이다 — 여기 든 두 값은 이 모듈이 «지금 처음» 만드는
#   것이라 기존 데이터에 단 한 건도 없다. 새 배선이 옛 채점을 건드리지 않는다.
RETURN_ACTIONS = (RETURN_OPEN_ACTION, RETURN_SETTLED_ACTION)

# 일기 actor — 재제외가 쓰는 것과 «같은 값»을 쓴다. 실험의 두 반쪽(개방·종료)이 서로 다른
# 주체로 기록되면 사슬에서 한 사건으로 안 묶인다. harness는 proposal.approval_source를
# actor_from_approval_source로 옮기는데, ss_exclude는 매핑 표에 없어 값이 그대로 통과한다
# (diary.actor_from_approval_source 참조) — 그 결과값을 여기서도 그대로 쓴다.
ACTOR = search_term_judge.APPROVAL_SOURCE_SS_EXCLUDE


def _target_fields(row: NaverSearchTermExclusion) -> dict:
    """일기의 대상 좌표. target_type='search_term'이어야 소급 채점이 검색어 grain 원료를 본다
    (diary_outcome._backfill_row의 d1_st·probation 게이트가 이 값을 본다)."""
    return {
        "target_type": "search_term",
        "target_id": row.search_term,
        "adgroup_id": row.adgroup_id,
    }


def record_return_opened(
    db: Session,
    row: NaverSearchTermExclusion,
    change_log: NaverChangeLog,
    now: datetime,
) -> None:
    """재심사 개방(제외키워드 삭제) 성공 1건 → execute 일기 1행.

    호출 위치: `search_term_ss_lane._open_exclusion`이 delete 성공 + 복귀 change_log를 커밋한
    **직후**. harness가 executor 커밋 직후에 일기를 남기는 것과 같은 자리다(실쓰기가 확정된
    뒤에만 기록 — 실패분은 남기지 않는다).

    ★fail-open: 일기 실패가 개방을 되돌리면 안 된다. diary.write_diary_entry가 이미 내부에서
    전부 삼키지만, 인자 평가(change_log.after_value 등)가 commit 후 refresh SELECT를 유발해
    **여기서** 터질 수 있다 — harness가 같은 이유로 호출부 try를 두고 있고(독립 리뷰 P2-1),
    그 전례를 그대로 따른다.

    ★★적대 리뷰 P2: 로그 인자를 **try 앞에서** 지역변수로 뽑는다. 초판은 except 절 안에서
    `row.adgroup_id`를 다시 읽었는데, 실패 원인이 바로 그 row의 refresh 실패(경합 삭제·쓰기락 —
    이 repo의 D-NAO-46② 상습 모드)면 **except 안에서 예외가 재발해 밖으로 샌다.** 그러면
    fail-open이 아니라 fail-loud가 되고, 호출부(`_open_exclusion`)엔 try가 없어 레인이 죽는다."""
    ag, term = row.adgroup_id, row.search_term  # ← 여기서 미리 뽑는다(except가 row를 다시 안 읽게)
    try:
        diary.write_diary_entry(
            db, "execute", row.campaign_id, actor=ACTOR,
            **_target_fields(row),
            action=RETURN_OPEN_ACTION,
            before_value=change_log.before_value,
            after_value=change_log.after_value,
            rationale=change_log.rationale,
            source_ref=change_log.id,
            now=now,
        )
    except Exception as e:  # noqa: BLE001 — fail-open(인자 평가 포함): 기록 실패가 집행을 못 막는다
        log.warning("exclusion_lifecycle: 개방 일기 기록 실패(fail-open, 개방은 확정): "
                    "adgroup=%s term=%r: %s", ag, term, e)


def record_return_settled(
    db: Session,
    row: NaverSearchTermExclusion,
    *,
    verdict: str,
    now: datetime,
) -> None:
    """복귀 실험의 종료 전이 → execute 일기 1행.

    ★`VERDICT_REEXCLUDED`는 기록하지 않는다 — 그 경로는 `_autofire_exclude`가 harness를 타서
    `exclude_search_term` execute 일기를 이미 남긴다. 여기서 또 남기면 같은 사건이 두 줄이 되어
    소급 채점이 이중 계상한다. 그래도 인자로 받는 이유는 **호출부가 두 갈래를 모두 이 함수에
    통과시켜야** 인구조사 테스트가 「전이마다 이 모듈을 지난다」를 셀 수 있기 때문이다.

    광고계정 무접촉 — restored는 순수 DB 전이다(네이버엔 이미 개방 시 삭제가 끝나 있다)."""
    if verdict == VERDICT_REEXCLUDED:
        return  # harness가 기록함(중복 방지) — 위 docstring 참조
    # 적대 리뷰 P2: except가 row를 다시 읽지 않도록 미리 뽑는다(위 record_return_opened 참조).
    ag, term, cycle = row.adgroup_id, row.search_term, row.cycle
    try:
        diary.write_diary_entry(
            db, "execute", row.campaign_id, actor=ACTOR,
            **_target_fields(row),
            action=RETURN_SETTLED_ACTION,
            before_value=json.dumps({"status": "probation", "cycle": cycle}, ensure_ascii=False),
            after_value=json.dumps({"status": verdict, "cycle": cycle}, ensure_ascii=False),
            rationale=(
                f"[검색어제외 복귀] 관찰창 만료 재판정 → {verdict}(cycle={cycle}) — "
                "더는 §1 손실 후보가 아니어서 제외 해제를 확정한다. 행은 보존(기억) — "
                "재충족 시 일반 경로로 다시 제외된다."
            ),
            source_ref=None,  # 이 전이엔 change_log가 없다(순수 DB 전이 — 네이버 무접촉)
            now=now,
        )
    except Exception as e:  # noqa: BLE001 — fail-open: 기록 실패가 상태 전이를 되돌리면 안 된다
        log.warning("exclusion_lifecycle: 복귀 확정 일기 기록 실패(fail-open, 전이는 확정): "
                    "adgroup=%s term=%r verdict=%s: %s", ag, term, verdict, e)


# ══════════════════════════════════════════════════════════════════════════════
# 관측 표면 (계약 §1 ⓔ · §2-6: "Jino가 명령 한 줄로 각 부품의 생존을 확인한다")
# ══════════════════════════════════════════════════════════════════════════════

def wiring_report(db: Session) -> dict:
    """복귀 실험 배선의 «생존»을 DB에서만 읽어 돌려준다(읽기 전용 — 네이버 무접촉).

    ★왜 «게이트 상태»를 같이 싣나: 이 배선의 라이브 행 수는 점화 전에는 **0이 정상**이다
    (계약 §1 「안 하는 것」 ①). 0을 그냥 찍으면 화면이 「배선이 죽었다」와 「아직 안 켰다」를
    구분하지 못한다 — 그래서 `auto_operate` 실측을 같은 표에 실어, 0의 «이유»를 화면이 스스로
    말하게 한다. (n=58 적대 리뷰 1R이 정확히 이 부류의 결함이었다: 화면이 관측 없이 단언했다.)
    """
    from sqlalchemy import func

    from app.models import NaverCampaignSettings, OpsDiaryEntry

    rows = (
        db.query(OpsDiaryEntry.action, func.count(OpsDiaryEntry.id),
                 func.max(OpsDiaryEntry.created_at))
        .filter(OpsDiaryEntry.event_type == "execute")
        .filter(OpsDiaryEntry.action.in_((*RETURN_ACTIONS, "exclude_search_term")))
        .group_by(OpsDiaryEntry.action)
        .all()
    )
    by_action = {a: {"count": int(n), "last_created_at": str(ts) if ts else None}
                 for a, n, ts in rows}
    for action in (*RETURN_ACTIONS, "exclude_search_term"):
        by_action.setdefault(action, {"count": 0, "last_created_at": None})

    gate = (
        db.query(NaverCampaignSettings.auto_operate, NaverCampaignSettings.optimizer,
                 func.count(NaverCampaignSettings.campaign_id))
        .group_by(NaverCampaignSettings.auto_operate, NaverCampaignSettings.optimizer)
        .all()
    )
    gate_rows = [{"auto_operate": bool(a), "optimizer": o, "campaigns": int(n)} for a, o, n in gate]
    ignited = any(r["auto_operate"] for r in gate_rows)

    # 복귀 관찰창 성적 분포 — outcome_json은 문자열이라 파이썬에서 센다(행 수가 작다: 복귀는
    # 일일 캡 10건). SQLite JSON 함수에 의존하지 않아 PostgreSQL 이관에도 그대로 산다.
    probation: dict[str, int] = {}
    unverified_reasons: dict[str, int] = {}
    open_entries = (
        db.query(OpsDiaryEntry.outcome_json)
        .filter(OpsDiaryEntry.event_type == "execute",
                OpsDiaryEntry.action == RETURN_OPEN_ACTION)
        .all()
    )
    for (raw,) in open_entries:
        try:
            prob = ((json.loads(raw) if raw else {}) or {}).get("probation") or {}
            status = prob.get("status")
        except (ValueError, TypeError):
            prob, status = {}, None
        probation[status or "미채점"] = probation.get(status or "미채점", 0) + 1
        # ★적대 리뷰 P1-3 후속: «왜 판정을 못 했나»가 화면에 없으면, 보류가 늘어나는 것과
        #   보류의 «이유»가 바뀌는 것을 구분할 수 없다. 이유별로 센다.
        if status == "unverified":
            r = str(prob.get("unverified_reason") or "사유 미기재")
            unverified_reasons[r] = unverified_reasons.get(r, 0) + 1

    return {
        "by_action": by_action,
        "gate": {"rows": gate_rows, "ignited": ignited},
        "probation_distribution": probation,
        "unverified_reasons": unverified_reasons,
        "return_open_total": len(open_entries),
    }
