# naver_execution_harness.py — naver_execution_harness Harness (듀얼모드 스프린트 Phase 5 골격
#   → X1a T3 실쓰기 개방, D-NAO-12/13/16). 역할: 네이버 광고 계정에 실제 쓰기를 가하는 유일한
#   초크포인트. 제안(NaverProposal) → 실행 시도 → naver_change_log 전건 기록(D-NAO-12).
#
#   X1a T3(실행 루프 X 스프린트): D-NAO-16 개방 순서의 1단계 — 제외키워드(add_negative_keyword)
#   실쓰기를 개방한다. 쓰기 스펙은 ref 27(docs/references/27) 정찰 완료·naver_sa_writer(T2)로
#   구현됨(추정 금지 충족). 실쓰기 = dry_run=False 명시 호출 + OPEN_ACTIONS 포함 +
#   _WRITE_EXECUTORS 구현 존재의 3중 조건 — 하나라도 빠지면 dry-run 강등 또는
#   WriteNotOpenedError(fail-closed). 나머지 액션(정지·재개→입찰→예산 순서)은 X1b 이후
#   (D-NAO-34 금지선: 개방 순서 임의 변경 금지, 예산은 스코프 밖).
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverChangeLog, NaverProposal
from app.services.naver_ad import naver_sa_writer
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
    """실제 쓰기(dry_run=False)를 시도했지만 해당 액션의 실행 함수가 _WRITE_EXECUTORS에 없음
    (OPEN_ACTIONS 확장만으로는 실제 집행이 열리지 않는다 — 쓰기 실행 함수 구현이 별도
    전제조건). OPEN_ACTIONS에 액션이 실수로 추가되더라도 구현 없이는 여기서 막힌다
    (fail-closed) — 이 예외가 그 안전장치다."""


class MissingExecutionTargetError(Exception):
    """실쓰기 대상 정보 부족/부적합 — 사전 검증 실패라 실행 시도가 아니며(writer 미호출,
    change_log 미기록), 제안 데이터 결함을 표면화한다. 걸리는 케이스:
    ① target_type != 'search_term': restricted-keywords는 검색어 텍스트를 등록하는 API인데
       _bid_proposal 격상 경로(economic_ceiling<=0)의 negative_keyword는 target_type='keyword',
       target_id='nkw-…'(키워드 ID)라서 그대로 등록하면 무의미한 문자열이 제외키워드로 등록됨
       — adgroup_id가 채워져 있어도 차단(fail-closed).
    ② adgroup_id 없음: X1a T3 이전에 생성된 구 제안(adgroup_id 컬럼 없던 시절) 등."""


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
# (런타임/UI 토글 없음). X1a T3: 1단계 제외키워드만 개방 — ref 27 정찰 + naver_sa_writer(T2)
# 구현 완료가 전제조건이었다. 나머지는 X1b 이후(순서 임의 변경 금지, D-NAO-34).
OPEN_ACTIONS: frozenset[str] = frozenset({"add_negative_keyword"})


def _execute_add_negative_keyword(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """제외키워드 실쓰기 1건 (X1a T3). naver_sa_writer 호출 → 성공/실패 모두 change_log 전건
    기록(D-NAO-12) — 성공은 before/after 실측값+created_ids(원복 원료, T3 이후 원복 기능의
    유일한 재료라 반드시 저장), 실패는 outcome='failed'+예외 요약을 커밋한 후 원 예외 재전파.

    실패 시 proposal.status='failed' — approved 게이트가 자동 재시도를 자연 차단한다
    (재시도는 사람이 콘솔에서 재승인하는 것이 유일 경로, D-NAO-5와 일관).
    """
    if proposal.target_type != "search_term":
        # 사전 검증 실패는 실행 시도가 아님 — writer 미호출, change_log 미기록.
        log.error(
            "naver_execution_harness: proposal_id=%s target_type=%r — 실쓰기 불가(fail-closed)",
            proposal.id, proposal.target_type,
        )
        raise MissingExecutionTargetError(
            f"proposal_id={proposal.id} target_type={proposal.target_type!r} — "
            f"restricted-keywords는 검색어 텍스트를 등록하는 API. target_type='keyword'"
            f"(_bid_proposal 격상 경로) 제안의 target_id는 nkw-… ID라서 그대로 등록하면 "
            f"무의미한 문자열이 제외키워드로 등록됨(fail-closed) — search_term 제안만 실행 가능"
        )

    if not proposal.adgroup_id:
        # 사전 검증 실패는 실행 시도가 아님 — writer 미호출, change_log 미기록.
        log.error(
            "naver_execution_harness: proposal_id=%s adgroup_id 없음 — 실쓰기 불가(fail-closed)",
            proposal.id,
        )
        raise MissingExecutionTargetError(
            f"proposal_id={proposal.id} adgroup_id 없음 — restricted-keywords API는 adgroupId "
            f"필수(ref 27 §8-1). 구 제안이거나 격상 경로 제안 — 재생성 필요"
        )

    try:
        result = naver_sa_writer.add_restricted_keywords(proposal.adgroup_id, [proposal.target_id])
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="add_negative_keyword",
            rationale=(
                f"{proposal.rationale or ''} [실행 실패] {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", executed_at=now,
            # before_value: writer 예외는 before 스냅샷을 실어주지 않아 확보 불가 — 정직하게 None.
            # executed_change_log_id는 연결하지 않음(성공 전용). verify_date 없음(검증 대상 부재).
        )
        db.add(fail_entry)
        db.commit()  # 실패 기록을 확정한 후 재전파 — 호출자가 실패를 알아야 함
        log.error(
            "naver_execution_harness: 실쓰기 실패 proposal_id=%s adgroup=%s keyword=%r — %s: %s",
            proposal.id, proposal.adgroup_id, proposal.target_id, type(exc).__name__, exc,
        )
        raise

    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="add_negative_keyword",
        rationale=proposal.rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=False, outcome="executed",
        before_value=json.dumps(result.before, ensure_ascii=False),
        after_value=json.dumps(
            {"after": result.after, "created_ids": result.created_ids}, ensure_ascii=False
        ),
        executed_at=now, verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    db.commit()

    log.info(
        "naver_execution_harness: 실쓰기 성공 proposal_id=%s adgroup=%s keyword=%r created_ids=%s",
        proposal.id, proposal.adgroup_id, proposal.target_id, result.created_ids,
    )
    return log_entry


# 실쓰기 디스패치 테이블 — OPEN_ACTIONS와 별도(이중 방벽): OPEN_ACTIONS에 있어도 여기 구현이
# 없으면 WriteNotOpenedError(fail-closed). 액션 확장 시 두 곳을 모두 의도적으로 갱신해야 한다.
_WRITE_EXECUTORS = {"add_negative_keyword": _execute_add_negative_keyword}


def _resolve_optimizer(db: Session, campaign_id: str) -> str:
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    return settings.optimizer if settings else "none"


def execute(db: Session, proposal_id: int, *, dry_run: bool = True, now: datetime | None = None) -> NaverChangeLog:
    """제안 1건을 실행 시도 — 실행 여부와 무관하게 naver_change_log에 전건 기록한다.

    순서: ①제안 조회 ②실행 가능 유형인지(액션 매핑 존재) ③status=='approved' 하드체크
    (D-NAO-5 사람 승인 게이트 — pending/rejected/expired/failed는 실행 불가) ④재실행 방지
    (executed_change_log_id가 이미 있으면 차단) ⑤optimizer=='ours' 하드체크(D-NAO-13 —
    제안 생성 단계에서 이미 걸러졌어도, 그 사이 설정이 바뀌었을 수 있어 실행 직전 재검증)
    ⑥OPEN_ACTIONS 미포함 액션은 dry_run 강제 True(D-NAO-5) ⑦change_log 기록
    ⑧proposal.executed_change_log_id 연결.

    X1a T3: dry_run=False + action∈OPEN_ACTIONS + _WRITE_EXECUTORS 구현 존재의 3중 조건이
    전부 충족될 때만 실쓰기 실행 함수로 디스패치한다(현재 add_negative_keyword만). 그 외에는
    기존 dry-run 경로 그대로 — dry-run 기록의 before_value/after_value는 계속 None(실행
    전/후 실측값이 존재하지 않음 — 추정 금지, 정직하게 남김).
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
        executor = _WRITE_EXECUTORS.get(action)
        if executor is None:
            # OPEN_ACTIONS에 실수로 추가돼도 실행 함수 구현이 없으면 여기서 막힌다(fail-closed).
            raise WriteNotOpenedError(f"action={action!r}는 아직 개방되지 않음(D-NAO-16/D-NAO-5)")
        return executor(db, proposal, now)

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
