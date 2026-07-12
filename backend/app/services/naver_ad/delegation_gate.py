# delegation_gate.py — E2 위임 자동승인·자동실행 게이트 (X1a T5, D-NAO-25 부분 게이트).
# 단일 책임: naver_account_settings.expert_delegated_types(Jino가 콘솔에서 명시 위임한
# proposal_type 집합)에 속하고, 같은 run의 전문가(Ava) 평결이 정확히 'agree'인 제안만
# 조건부 UPDATE로 원자 자동승인(approval_source='delegation') 후 naver_execution_harness.
# execute()로 곧바로 실행한다. 그 외 전부 무접촉(fail-closed — 사람 콘솔 triage 대상).
#
# 크래시 케이스: 승인 커밋(claim) 후 execute() 호출 전에 프로세스가 죽으면 그 제안은
# approved 상태로 미실행 잔존한다 — 콘솔 approved 탭에서 사람이 실행 버튼으로 마무리하면
# 되는 우아한 저하(degradation)다. 별도 복구 로직은 두지 않는다(harness의 executing 클레임
# 잔존 정책과 동일한 태도 — naver_execution_harness.py 상단 주석 참조).
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import (
    NaverAccountSettings,
    NaverCampaignSettings,
    NaverExpertReview,
    NaverExpertReviewRun,
    NaverProposal,
)
from app.services.naver_ad import naver_execution_harness
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_DELEGATION_KEY = "expert_delegated_types"


def get_delegated_types(db: Session) -> set[str]:
    """저장된 위임 유형 집합. KV 행이 없거나 value_json이 비어있거나 파싱에 실패하면
    빈 set(fail-closed) — 위임은 명시적으로 켠 경우에만 작동해야 한다."""
    row = db.query(NaverAccountSettings).filter(NaverAccountSettings.key == _DELEGATION_KEY).first()
    if row is None or not row.value_json:
        return set()
    try:
        parsed = json.loads(row.value_json)
        if not isinstance(parsed, list):
            raise ValueError(f"expert_delegated_types는 리스트여야 하는데 {type(parsed).__name__}")
        return set(parsed)
    except (TypeError, ValueError) as exc:
        log.error("delegation_gate: expert_delegated_types 파싱 실패(fail-closed) — %s: %s",
                  type(exc).__name__, exc)
        return set()


def delegable_types() -> set[str]:
    """실제 위임 가능한 proposal_type 집합 — action이 OPEN_ACTIONS(D-NAO-16 개방 순서)와
    _WRITE_EXECUTORS(이중 방벽) 둘 다에 있는 것만(harness.real_write_blocker와 동일 기준).
    저장된 delegated_types에 미개방 유형이 섞여 있어도 run_gate에서 이 집합과 교집합을
    취해 무시한다(이중 방어 — X1b 이후 OPEN_ACTIONS가 넓어지면 이 함수도 자동으로 넓어짐)."""
    openable = naver_execution_harness.OPEN_ACTIONS & set(naver_execution_harness._WRITE_EXECUTORS)
    return {
        ptype for ptype, action in naver_execution_harness._ACTION_BY_PROPOSAL_TYPE.items()
        if action in openable
    }


def _resolve_optimizer(db: Session, campaign_id: str) -> str:
    """harness._resolve_optimizer와 동일 시맨틱(설정 행 없으면 'none' 취급)."""
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    return settings.optimizer if settings else "none"


def _eligible(db: Session, proposal: NaverProposal, delegated: set[str], skipped: dict) -> bool:
    """자격 사전 필터 — 하나라도 탈락하면 그 제안은 무접촉(상태 변경·기록 없음, 사유별
    카운트만). 순서: 위임유형 → pending → real_write_blocker → optimizer → 라운드봉투(budget_up만).

    라운드봉투(budget_envelope, D-NAO-42-f②, codex P1): budget_up 제안은
    budget_auto_eligible이 정확히 True일 때만 자동승인 대상이다 — 라운드 합계가 회당
    자율한도(10만원)를 넘긴 초과분(False)은 위임이 켜져 있어도 반드시 사람 승인
    (proposal_writer._classify_budget_round_envelope가 생성 단계에서 분류, 여기선 그
    분류를 소비만 한다). budget_down은 이 검사에서 면제(감액은 자유, budget_auto_eligible이
    애초에 None으로 남아 있음 — _classify_budget_round_envelope는 budget_up만 분류)."""
    if proposal.proposal_type not in delegated:
        skipped["not_delegated"] += 1
        return False
    if proposal.status != "pending":
        # ★failed→approved 재승인은 영구 사람 전용 — 자동 재시도 금지 불변(D-NAO-5와 일관).
        skipped["not_pending"] += 1
        return False
    if naver_execution_harness.real_write_blocker(proposal) is not None:
        skipped["blocked"] += 1
        return False
    if _resolve_optimizer(db, proposal.campaign_id) != "ours":
        skipped["optimizer"] += 1
        return False
    if proposal.proposal_type == "budget_up" and proposal.budget_auto_eligible is not True:
        skipped["budget_envelope"] += 1
        return False
    return True


def run_gate(db: Session, run_id: int, *, now=None) -> dict:
    """run_id에 속한 agree 평결 중 위임된 유형만 자동승인+실행. 반환값은 감사·로그용 요약
    (호출측 expert_desk.result['delegation']에 그대로 담김).

    run.status=='ok' 검사는 호출측(expert_desk stage5)에도 있지만 여기서도 자체 수행한다
    (codex R2 — 이 함수가 자동승인의 경계이므로 미래의 직접 호출자가 degraded run을 넘겨도
    fail-closed. OPEN_ACTIONS/_WRITE_EXECUTORS 이중 방벽과 같은 태도)."""
    now = now or kst_now()
    run = db.get(NaverExpertReviewRun, run_id)
    if run is None or run.status != "ok":
        return {
            "status": "skipped",
            "reason": f"run_id={run_id} 없음 또는 status!='ok' — degraded/부재 run 자동실행 금지(fail-closed)",
            "delegated_types": [], "agree_count": 0, "auto_approved": 0, "executed": 0, "failed": 0,
            "skipped": {"not_delegated": 0, "not_pending": 0, "blocked": 0, "optimizer": 0, "budget_envelope": 0},
        }
    delegated = get_delegated_types(db) & delegable_types()  # 저장값에 미개방 유형이 섞여도 이중 방어
    if not delegated:
        return {
            "status": "skipped", "reason": "delegated_types 비어있음",
            "delegated_types": [], "agree_count": 0, "auto_approved": 0, "executed": 0, "failed": 0,
            "skipped": {"not_delegated": 0, "not_pending": 0, "blocked": 0, "optimizer": 0, "budget_envelope": 0},
        }

    agree_reviews = (
        db.query(NaverExpertReview)
        .filter(
            NaverExpertReview.run_id == run_id,
            NaverExpertReview.verdict == "agree",
            NaverExpertReview.proposal_id.isnot(None),
        )
        .all()
    )

    skipped = {"not_delegated": 0, "not_pending": 0, "blocked": 0, "optimizer": 0, "budget_envelope": 0}
    auto_approved = 0
    executed = 0
    failed = 0

    for review in agree_reviews:
        proposal = db.get(NaverProposal, review.proposal_id)
        if proposal is None:
            continue  # 참조 무결성 이상(평결이 가리키는 제안이 없음) — 카운트 대상 아님

        if not _eligible(db, proposal, delegated, skipped):
            continue

        # 조건부 UPDATE 원자 승인(naver_execution_harness._execute_add_negative_keyword의
        # 클레임 패턴과 동일 — codex R2 P1 계열) — 동시 콘솔 조작 등으로 그 사이 상태가
        # 바뀌었으면 rowcount!=1, 그 제안은 skip(사람 조작 우선).
        claimed = db.query(NaverProposal).filter(
            NaverProposal.id == proposal.id,
            NaverProposal.status == "pending",
        ).update({"status": "approved", "approval_source": "delegation"}, synchronize_session=False)
        db.commit()
        if claimed != 1:
            skipped["not_pending"] += 1
            continue
        auto_approved += 1

        try:
            naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
            executed += 1
        except Exception as exc:  # noqa: BLE001 — harness가 실패 종결(failed)+change_log 감사
            # 기록을 이미 책임진다(코어 실쓰기 예외는 전부 그 안에서 커밋됨). 게이트는 재시도나
            # 수습을 하지 않고 로그+카운트만 남기고 다음 제안으로 진행한다.
            failed += 1
            log.error(
                "delegation_gate: proposal_id=%s 자동실행 실패(harness가 처리 완료) — %s: %s",
                proposal.id, type(exc).__name__, exc,
            )

    return {
        "status": "ok",
        "delegated_types": sorted(delegated),
        "agree_count": len(agree_reviews),
        "auto_approved": auto_approved,
        "executed": executed,
        "failed": failed,
        "skipped": skipped,
    }
