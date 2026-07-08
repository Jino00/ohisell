# naver_execution_harness.py — naver_execution_harness Harness (듀얼모드 스프린트 Phase 5,
#   D-NAO-12/13/16 골격). 역할: 네이버 광고 계정에 실제 쓰기를 가하는 유일한 초크포인트.
#   제안(NaverProposal) → 실행 시도 → naver_change_log 전건 기록(D-NAO-12) 흐름을 조립한다.
#
#   이번 Phase의 스코프는 "골격"이다 — 실제 네이버 API 쓰기 함수는 아직 구현하지 않는다
#   (POST/PUT /ncc/keywords·/ncc/adgroups·/ncc/campaigns의 정확한 요청 스펙을 이 코드베이스가
#   아직 실측/문서 확인하지 않았다 — 추정 금지 원칙, 쓰기 스펙은 실제 개방 결정 시 별도 실측
#   필요). OPEN_ACTIONS(D-NAO-16 개방 순서: 제외키워드→정지·재개→입찰→예산)는 이번
#   스프린트에서 항상 빈 집합 — 즉 dry_run 플래그를 False로 호출해도 강제로 dry-run 처리되고,
#   실제 쓰기 코드 경로는 이 스프린트 안에서는 도달 불가능하다(D-NAO-5 영구 사람 게이트,
#   "실제 쓰기 개방은 이 스프린트 스코프 밖" — 계획서 §4-Phase5).
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverChangeLog, NaverProposal
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# D+14 검증 예정일(D-NAO-14 "D+7/14 실측" · proposal_pipeline._PROPOSAL_EXPIRY_DAYS와 동일
# 하한 채택) — proposal_scoreboard(Phase 6 루프1)가 이 날짜 이후 실측 대조를 수행한다.
VERIFY_DAYS = 14


class OptimizerGuardError(Exception):
    """optimizer!='ours' 캠페인에 대한 실행 시도 — D-NAO-13 하드체크 위반."""


class ActionNotExecutableError(Exception):
    """정보성 제안(anomaly/anomaly_freshness/account_brief/trigger_*)에 대한 실행 시도 —
    이런 제안은 target 액션 자체가 없어 실행 대상이 아니다(D-3, proposal_writer 문서 참조)."""


class ProposalNotApprovedError(Exception):
    """status!='approved' 제안에 대한 실행 시도 — D-NAO-5 "반자동 = Confirm 승인 후 실행"
    게이트. 콘솔의 승인 액션(현재 disabled, Phase 1 참조)이 status를 'approved'로 바꾸는
    것이 유일한 정당 경로 — pending을 곧바로 실행하면 사람 승인 단계 자체가 없어진다
    (codex 지적, 원칙19 — 최초 구현에서 이 체크가 빠져 있었다)."""


class AlreadyExecutedError(Exception):
    """이미 executed_change_log_id가 있는 제안을 재실행 시도 — 같은 제안이 change_log에
    중복 기록되는 것을 막는다(codex 지적과 동일 계열 — 실행은 1회성이어야 한다)."""


class WriteNotOpenedError(Exception):
    """실제 쓰기(dry_run=False)를 시도했지만 실제 네이버 API 쓰기 함수가 아직 구현되지 않음
    (OPEN_ACTIONS 확장만으로는 실제 집행이 열리지 않는다 — 쓰기 함수 구현이 별도 전제조건).
    이번 스프린트는 OPEN_ACTIONS가 항상 비어 있어 effective_dry_run이 항상 True로 강등되므로
    도달하지 않지만, 향후 OPEN_ACTIONS에 액션을 추가하더라도 쓰기 함수 없이는 여전히 여기서
    막혀야 한다(fail-closed) — 이 예외가 그 안전장치다."""


# 제안유형 → 실행 액션 매핑. anomaly/anomaly_freshness/account_brief/trigger_pacing/
# trigger_cpc_spike는 의도적으로 매핑에 없음(정보성, 실행 불가 — ActionNotExecutableError).
_ACTION_BY_PROPOSAL_TYPE = {
    "negative_keyword": "add_negative_keyword",
    "bid_up": "update_bid",
    "bid_down": "update_bid",
    "growth_bid_up": "update_bid",
    "budget_up": "update_budget",
}

# D-NAO-16 개방 순서(제외키워드→정지·재개→입찰→예산)의 실제 스위치. 코드 배포로만 변경
# (런타임/UI 토글 없음 — 콘솔의 "실행" 버튼은 Phase 1부터 계속 disabled, D-NAO-5).
# 정지·재개는 그 신호를 만드는 진단 보드/제안유형 자체가 아직 없어(P2 보드 목록 참조)
# 매핑 대상이 없다 — 이번 Phase 골격에는 포함하지 않는다(추정으로 액션을 지어내지 않음).
OPEN_ACTIONS: frozenset[str] = frozenset()


def _resolve_optimizer(db: Session, campaign_id: str) -> str:
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    return settings.optimizer if settings else "none"


def execute(db: Session, proposal_id: int, *, dry_run: bool = True, now: datetime | None = None) -> NaverChangeLog:
    """제안 1건을 실행 시도 — 실행 여부와 무관하게 naver_change_log에 전건 기록한다.

    순서: ①제안 조회 ②실행 가능 유형인지(액션 매핑 존재) ③status=='approved' 하드체크
    (D-NAO-5 사람 승인 게이트 — pending/rejected/expired는 실행 불가) ④재실행 방지
    (executed_change_log_id가 이미 있으면 차단) ⑤optimizer=='ours' 하드체크(D-NAO-13 —
    제안 생성 단계에서 이미 걸러졌어도, 그 사이 설정이 바뀌었을 수 있어 실행 직전 재검증)
    ⑥OPEN_ACTIONS 미포함 액션은 dry_run 강제 True(D-NAO-5) ⑦change_log 기록
    ⑧proposal.executed_change_log_id 연결.

    이번 Phase는 dry_run=False로 호출해도 OPEN_ACTIONS가 비어 있어 항상 dry-run으로
    강등된다 — 실제 네이버 API는 절대 호출되지 않는다(골격, 계획서 §4-Phase5).
    before_value/after_value는 아직 채우지 않는다(실제 쓰기 함수가 없어 실행 전/후 실측값
    자체가 존재하지 않음 — 추정 금지, None으로 정직하게 남김).
    """
    now = now or kst_now()
    proposal = db.get(NaverProposal, proposal_id)
    if proposal is None:
        raise ValueError(f"NaverProposal id={proposal_id} 없음")

    action = _ACTION_BY_PROPOSAL_TYPE.get(proposal.proposal_type)
    if action is None:
        raise ActionNotExecutableError(
            f"proposal_type={proposal.proposal_type!r}는 정보성 제안이라 실행 대상이 아님"
        )

    if proposal.status != "approved":
        raise ProposalNotApprovedError(
            f"proposal_id={proposal_id} status={proposal.status!r} — "
            f"'approved'만 실행 가능(D-NAO-5 사람 승인 게이트)"
        )
    if proposal.executed_change_log_id is not None:
        raise AlreadyExecutedError(
            f"proposal_id={proposal_id}는 이미 실행됨(change_log_id={proposal.executed_change_log_id})"
        )

    optimizer = _resolve_optimizer(db, proposal.campaign_id)
    if optimizer != "ours":
        raise OptimizerGuardError(
            f"campaign_id={proposal.campaign_id} optimizer={optimizer!r} — "
            f"'ours'만 실행 가능(D-NAO-13, 실행 직전 재검증)"
        )

    effective_dry_run = dry_run or action not in OPEN_ACTIONS
    if not effective_dry_run:
        # OPEN_ACTIONS가 비어 있는 한 도달 불가능한 방벽(위 조건이 항상 dry_run=True로 강등).
        raise WriteNotOpenedError(f"action={action!r}는 아직 개방되지 않음(D-NAO-16/D-NAO-5)")

    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action=action,
        rationale=proposal.rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=effective_dry_run, executed_at=now,
        verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    db.commit()

    log.info(
        "naver_execution_harness: proposal_id=%s action=%s dry_run=%s campaign_id=%s",
        proposal_id, action, effective_dry_run, proposal.campaign_id,
    )
    return log_entry
