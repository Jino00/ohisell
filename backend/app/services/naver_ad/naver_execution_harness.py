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
#
#   X1a T4(콘솔 승인 버튼+실행 라우터): `real_write_blocker()`가 실행 가능 여부 판정을
#   naver_ad.py 라우터에 공개한다(D-NAO-5 반자동 게이트 UI 노출).
#
#   X1b T4(D-NAO-16 2·3단계): 정지·재개(set_user_lock)·입찰(update_bid) 개방. 실쓰기 직전
#   guardrail_gate.check()(±15%·쿨다운·일일상한·스톱로스·BEP증액금지·클램프·일예산)를
#   반드시 통과해야 하는 새 단계가 실행 순서(§4)에 추가된다. MOP 충돌 감지(D-NAO-13)도
#   여기서 배선 — 우리 마지막 기록과 방금 재조회한 라이브 값이 다르면 경고(차단 아님).
#
#   P3(D-NAO-42-f, D-NAO-16 4단계): 예산(update_budget) 개방 — "우리 MOP = MOP Pro+
#   무제한"의 마지막 조각. update_bid와 동형(구조검증 → guardrail_gate._check_budget →
#   claim → naver_sa_writer.update_campaign_budget → change_log). 라운드 봉투(§5-E, 회당
#   총 증가액≤10만원)는 실행 단계가 아니라 proposal_writer.build(생성 단계)에서 이미
#   budget_auto_eligible로 분류돼 있다 — 여기서는 그 분류를 소비하지 않는다(오늘은 반자동,
#   Jino 콘솔 승인이 모든 budget_up 실행의 유일한 게이트).
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverChangeLog, NaverEntity, NaverHourlySnapshot, NaverProposal
from app.services.naver_ad import account_diagnosis, campaign_target_resolver, diary, guardrail_gate, naver_sa_writer
from app.services.naver_ad.diagnosis import correction_factor as compute_correction_factor
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# D+14 검증 예정일(D-NAO-14 "D+7/14 실측" · proposal_pipeline._PROPOSAL_EXPIRY_DAYS와 동일
# 하한 채택) — proposal_scoreboard(Phase 6 루프1)가 이 날짜 이후 실측 대조를 수행한다.
VERIFY_DAYS = 14

# guardrail_gate 컨텍스트의 실적 창(account_diagnosis.LOW_CLICK_LOOKBACK_DAYS 재사용 —
# 신규 상수 아님, pause_candidates/resume_candidates와 동일 창으로 정합성 유지).
_GUARDRAIL_LOOKBACK_DAYS = account_diagnosis.LOW_CLICK_LOOKBACK_DAYS


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


class KillSwitchEngagedError(Exception):
    """auto_operator 승인 제안(approval_source가 auto_op/auto_op_hr)이 쓰기 직전 킬스위치
    (naver_campaign_settings.auto_operate) OFF로 거부됨 (codex 7R[P1], D-NAO-49).

    레인의 승인 커밋~harness 쓰기 사이에 Jino가 킬스위치를 끄는 TOCTOU 구간을 여기서
    봉쇄한다(레인 자체의 승인 직전 pre-check와 이중 방어 — 이 가드가 최종). 쓰기·change_log
    없음, proposal은 approved인 채 미실행(정직 상태 — 스위치 재가동 후 재실행 가능).
    수동 콘솔(approval_source NULL)·delegation 승인 제안에는 절대 적용되지 않는다."""


class MissingExecutionTargetError(Exception):
    """실쓰기 대상 정보 부족/부적합 — writer는 호출하지 않지만 운영자 관점에선 시도이므로
    change_log 전건 기록(D-NAO-12, rationale '[실행 불가]') + status='failed' 종결(영구 결함
    — 재승인해도 데이터가 안 바뀌므로 재승인 루프 방지) 후 이 예외를 던진다. 걸리는 케이스:
    ① target_type != 'search_term': restricted-keywords는 검색어 텍스트를 등록하는 API인데
       _bid_proposal 격상 경로(economic_ceiling<=0)의 negative_keyword는 target_type='keyword',
       target_id='nkw-…'(키워드 ID)라서 그대로 등록하면 무의미한 문자열이 제외키워드로 등록됨
       — adgroup_id가 채워져 있어도 차단(fail-closed).
    ② adgroup_id 없음: X1a T3 이전에 생성된 구 제안(adgroup_id 컬럼 없던 시절) 등."""


# 제안유형 → 실행 액션 매핑. anomaly/anomaly_freshness/account_brief/trigger_pacing/
# trigger_cpc_spike는 의도적으로 매핑에 없음(정보성, 실행 불가 — ActionNotExecutableError).
# pause/resume(X1b T3, D-NAO-38)은 둘 다 naver_sa_writer.set_keyword_lock 하나로 실행되므로
# 액션명을 공유(target_lock 값으로 방향 구분 — guardrail_gate가 이미 이 값으로 방향검증).
# budget_up/budget_down(P3, D-NAO-42-f)도 동일 원리 — 둘 다 naver_sa_writer.
# update_campaign_budget 하나로 실행되고, 방향은 target_budget vs current_budget 비교로
# guardrail_gate._check_budget이 구분한다.
_ACTION_BY_PROPOSAL_TYPE = {
    "negative_keyword": "add_negative_keyword",
    "bid_up": "update_bid",
    "bid_down": "update_bid",
    "growth_bid_up": "update_bid",
    "pause": "set_user_lock",
    "resume": "set_user_lock",
    "budget_up": "update_budget",
    "budget_down": "update_budget",
}

# D-NAO-47(codex[P2] 2026-07-17): **우리가 실제로 광고 API에 쓴 것**의 action 집합.
# 위 매핑의 값에서 파생한다 — 하드코딩하면 새 제안 유형이 배선될 때 조용히 어긋난다.
#
# ★왜 필요한가: naver_change_log에는 세 부류가 섞여 있다.
#   ① 우리 실집행        — update_bid / add_negative_keyword / set_user_lock / update_budget
#   ② 외부 변경 **감지**  — external_bid_change / external_status_change (entity_sync가 기록.
#                          MOP·사람이 바꾼 걸 우리가 관측한 것이지 우리가 한 게 아니다)
#   ③ 우리 시스템 내부 설정 — optimizer_change / update_expert_delegation / flight_pacing
#                          (광고 API 쓰기 아님)
# 커맨드 센터 1층의 "우리 조작 N회"가 ②③을 섞어 세면 **정반대의 거짓말**이 된다:
# prod 실측(2026-07-17) change_log의 dry_run=False 행 15건은 **전부 ②**이고 우리 실집행은
# 0건이라, 필터 없이 세면 "우리 조작 15회"라고 표시된다. 0을 0이라고 말하는 게 그 화면의
# 존재 이유다(D-47-h).
EXECUTION_ACTIONS: frozenset[str] = frozenset(_ACTION_BY_PROPOSAL_TYPE.values())

# ══════════════════════════════════════════════════════════════════
# 실패 rationale 접두사(D-NAO-54) — **두 사건은 다르다. 섞으면 화면이 거짓말한다.**
#
#   [실행 불가] = 사전 가드 거부(_guard_failure). writer를 부르지도 않았다.
#                 → 광고는 **확실히 안 바뀌었다**.
#   [실행 실패] = writer 예외. PUT을 이미 보낸 뒤일 수 있다.
#                 → 광고가 바뀌었는지 **모른다**. 예: naver_sa_writer의
#                   WriteVerificationError는 "bidAmt는 반영됐으나 useGroupBidAmt가
#                   전환 안 됨"에서도 뜬다(writer:341) — 이때 네이버엔 우리 입찰가가
#                   들어가 있다. 네트워크 타임아웃(PUT 성공·응답 유실)도 같은 행을 만든다.
#
# 두 행의 DB 모양은 동일하다(dry_run=False · outcome='failed' · after_value=None).
# 구분 신호는 이 접두사뿐이라, 문자열을 흩뿌리지 않고 상수로 고정해 라우터가 파생하게 한다
# (EXECUTION_ACTIONS를 _ACTION_BY_PROPOSAL_TYPE에서 파생시킨 것과 같은 원칙 — 하드코딩하면
# 접두사가 바뀔 때 화면이 **조용히** 어긋난다).
# ★"모름"을 "차단됨"으로 표시하는 것은 원칙22 위반이다. 이 코드의 다른 곳(:441 주석)도
#   쓰기 실패에 대해 "사람이 네이버 콘솔로 실제 반영 여부를 확인"하라고 못 박고 있다.
GUARD_BLOCK_MARKER = "[실행 불가]"
WRITE_FAILURE_MARKER = "[실행 실패]"

# entity_sync가 기록하는 "외부가 바꿨다" 감지 행(D-NAO-40 상태 / D-NAO-47 입찰 /
# D-NAO-50 키워드 인벤토리 add·remove 밸브). 커맨드 센터의 actor=external 필터가 이 집합을 쓴다.
EXTERNAL_DETECTION_ACTIONS: frozenset[str] = frozenset({
    "external_status_change", "external_bid_change",
    "external_keyword_added", "external_keyword_removed",
})

# D-NAO-16 개방 순서(제외키워드→정지·재개→입찰→예산)의 실제 스위치. 코드 배포로만 변경
# (런타임/UI 토글 없음). X1a T3: 1단계 제외키워드 개방. X1b T4: 2·3단계(정지·재개→입찰)
# 개방 — ref 27 정찰 + naver_sa_writer(T1)·guardrail_gate(T2) 구현 완료가 전제조건이었다.
# P3(D-NAO-42-f): 4단계 예산(update_budget) 개방 — D-NAO-34 금지선 개정(예산 통제 개방,
# 계획서 PLAN_naver-ad-budget-control.md §0). 안전가드레일(BEP 이익하한·스톱로스·클램프·
# +100%캡)은 guardrail_gate._check_budget이 그대로 유지한다 — "무제한 ≠ 무분별".
OPEN_ACTIONS: frozenset[str] = frozenset(
    {"add_negative_keyword", "update_bid", "set_user_lock", "update_budget"}
)


def _guard_failure(db: Session, proposal: NaverProposal, now: datetime, action: str, reason: str) -> None:
    """사전 가드 실패 처리(codex P1) — 운영자 관점에선 시도다: 전건 기록(D-NAO-12) + 영구
    결함(제안 데이터가 재승인으로 바뀌지 않음)이라 failed로 종결해 재승인 루프를 막는다.
    writer는 호출하지 않는다(실제 API 시도 없음 — change_log에는 [실행 불가]로 구분 기록)."""
    proposal.status = "failed"
    entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action=action,
        rationale=f"{proposal.rationale or ''} {GUARD_BLOCK_MARKER} {reason}",
        predicted_json=proposal.expected_effect, proposal_id=proposal.id,
        dry_run=False, outcome="failed", changed_at=now, executed_at=now,
    )
    db.add(entry)
    db.commit()
    log.error("naver_execution_harness: proposal_id=%s 실쓰기 불가(fail-closed) — %s",
              proposal.id, reason)
    # D-NAO-54 P1 일기(blocked) — 가드레일/구조 차단 1건. source_ref=방금 커밋한 change_log id.
    # ★호출부 try 필요(독립 리뷰 P2-1): 직전 commit이 entry/proposal을 만료시켜 인자 평가
    # (entry.id, proposal.*)가 refresh SELECT(I/O)를 유발한다 — 그 예외는 write_diary_entry의
    # try 밖이라, 여기서 감싸지 않으면 일기 계약("집행/차단 보고를 오염시키지 않음")이 깨진다.
    try:
        diary.write_diary_entry(
            db, "blocked", proposal.campaign_id,
            actor=diary.actor_from_approval_source(proposal.approval_source),
            target_type=proposal.target_type, target_id=proposal.target_id,
            adgroup_id=proposal.adgroup_id, action=action,
            # ★리터럴 대신 공유 상수(3-way 병합, D-NAO-54): 마커 드리프트 방지 테스트가
            #   같은 값의 리터럴을 금지한다 — 값은 동일("[실행 불가]")이라 동작 불변.
            rationale=f"{GUARD_BLOCK_MARKER} {reason}", source_ref=entry.id, now=now,
        )
    except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
        log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)


def _claim_executing(db: Session, proposal: NaverProposal) -> None:
    """내구 클레임(codex P1) — 조건부 UPDATE로 원자화(codex R2 P1): ORM 인스턴스 대입만으로는
    두 세션이 동시에 approved를 읽었을 때 둘 다 클레임 가능(check-then-set TOCTOU).
    UPDATE ... WHERE status='approved' AND executed_change_log_id IS NULL은 DB가 직렬화해
    정확히 한쪽만 1행 성공 — 콘솔 라우터(사람 클릭)·X2 flight_loop 크론의 다중 진입 대비.
    이 시점 이후 크래시해도 'executing'이 남아 approved 게이트가 재진입을 차단한다.
    실행자 3개(add_negative_keyword/update_bid/set_user_lock)가 동일 로직을 공유한다.

    codex 8R[P1](D-NAO-49): 클레임 성공 직후 = 모든 실행자의 writer_fn 호출 직전 단일
    공통 지점 — execute() 진입 체크(7R)와 PUT 사이의 라이브 재조회·가드레일 평가(수백 ms)
    동안 킬스위치가 꺼지는 잔여 레이스를 여기서 봉쇄한다(최종 권위, 3중 방어 완성:
    레인 pre-check → execute() 진입 → writer 직전). approval_source가 auto_op/auto_op_hr인
    제안에 한해 재확인, OFF면 클레임을 approved로 원복(미실행 정직 상태 — executing 잔존
    방지)하고 KillSwitchEngagedError — 쓰기·change_log 없음. 수동 콘솔(NULL)·delegation은
    비영향."""
    claimed = db.query(NaverProposal).filter(
        NaverProposal.id == proposal.id,
        NaverProposal.status == "approved",
        NaverProposal.executed_change_log_id.is_(None),
    ).update({"status": "executing"}, synchronize_session=False)
    db.commit()
    if claimed != 1:
        raise AlreadyExecutedError(
            f"proposal_id={proposal.id} 클레임 실패 — 다른 실행자가 선점(동시 실행 차단)"
        )
    db.refresh(proposal)

    if proposal.approval_source is not None:
        from app.services.naver_ad import auto_operator as _auto_operator  # 지연 import(순환 회피, 7R과 동일)

        if proposal.approval_source in (
            _auto_operator.APPROVAL_SOURCE_DAILY, _auto_operator.APPROVAL_SOURCE_HOURLY,
            _auto_operator.APPROVAL_SOURCE_PROBE,  # D-NAO-58 CD2: 탐침도 동일 킬스위치 가드(우회 금지)
            _auto_operator.APPROVAL_SOURCE_REVERT,  # D-NAO-58 CD3: 되돌림도 킬스위치 통과(우회 금지)
        ) and not _auto_operator._auto_operate_now(db, proposal.campaign_id):
            proposal.status = "approved"  # 클레임 원복 — executing 잔존 방지(미실행 정직 상태)
            db.commit()
            log.warning(
                "naver_execution_harness: 킬스위치 OFF(writer 직전 최종 확인) — proposal_id=%s"
                "(approval_source=%s, campaign_id=%s) 실행 거부(쓰기·change_log 없음, "
                "approved 원복, codex 8R)",
                proposal.id, proposal.approval_source, proposal.campaign_id,
            )
            # D-NAO-54 P1 일기(kill_switch) — writer 직전 최종 확인 거부(쓰기·change_log 없음).
            # 호출부 try(독립 리뷰 P2-1): 직전 rollback이 proposal을 만료시켜 인자 평가가 I/O 유발 가능.
            try:
                diary.write_diary_entry(
                    db, "kill_switch", proposal.campaign_id,
                    actor=diary.actor_from_approval_source(proposal.approval_source),
                    target_type=proposal.target_type, target_id=proposal.target_id,
                    adgroup_id=proposal.adgroup_id,
                    action=_ACTION_BY_PROPOSAL_TYPE.get(proposal.proposal_type),
                    rationale="킬스위치 OFF(writer 직전 최종 확인)",
                )
            except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
                log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
            raise KillSwitchEngagedError(
                f"proposal_id={proposal.id} campaign_id={proposal.campaign_id} — "
                f"auto_operate=False(킬스위치 OFF, writer 직전 최종 확인)"
            )


def _detect_external_change(db: Session, proposal: NaverProposal, live_before: dict, field: str) -> str | None:
    """MOP 충돌 감지(D-NAO-13, X1b T4 배선) — 우리의 마지막 성공 기록(change_log.after_value)과
    방금 재조회한 라이브 값이 다르면 그 사이 외부(MOP 등)가 바꿨다는 신호. 차단하지 않고
    경고만 반환(원문: "외부 변경 감지 시 경고") — 호출자가 rationale에 부착해 change_log에
    남긴다. field: 비교할 키('bidAmt' 또는 'userLock').

    "우리 마지막 성공" 판별은 outcome이 아니라 after_value 존재 여부로 한다 — outcome은
    이제 D+14 채점 전엔 NULL이고(proposal_scoreboard 배선, 위 executor 주석 참조) 채점
    후엔 improved/declined/neutral로 바뀌므로 "executed"라는 영구 상태가 아니다.
    dry_run=False + after_value가 있는 행만 실제 성공한 쓰기다(실패·가드거부 행은
    before/after_value를 안 채움, dry-run도 마찬가지)."""
    last = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.entity_type == proposal.target_type,
            NaverChangeLog.entity_id == proposal.target_id,
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.executed_at.desc())
        .first()
    )
    if last is None or not last.after_value:
        return None
    try:
        last_after = json.loads(last.after_value)
    except (ValueError, TypeError):
        return None
    if not isinstance(last_after, dict):
        return None
    prior_value = last_after.get(field)
    live_value = live_before.get(field)
    if prior_value is not None and live_value is not None and prior_value != live_value:
        return (
            f"⚠️외부 변경 감지(D-NAO-13) — 우리 마지막 기록 {field}={prior_value}, "
            f"현재 라이브 {field}={live_value}(MOP가 아직 켜져있을 수 있음)"
        )
    return None


def _resolve_target_roas_float(db: Session, campaign_id: str) -> float | None:
    """override>계정기본값 목표ROAS를 float로 해석(keyword/campaign/adgroup up 브랜치 공유,
    S3 D-NAO-43 확장 — 중복 로직 통합)."""
    resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
    target_roas = resolved["target_roas"]
    if target_roas is None:
        target_roas = campaign_target_resolver.account_default_target_roas(db)
    return float(target_roas) if target_roas is not None else None


def _latest_hourly_snapshot_fields(db: Session, campaign_id: str, on_date) -> tuple[int | None, int | None]:
    """캠페인의 당일 최신 시간별 스냅샷에서 (cost, daily_budget) 추출(keyword/campaign/adgroup
    up 브랜치 공유, S3 D-NAO-43 확장 — 중복 로직 통합)."""
    latest_snapshot = (
        db.query(NaverHourlySnapshot)
        .filter(NaverHourlySnapshot.campaign_id == campaign_id, NaverHourlySnapshot.ad_date == on_date)
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if latest_snapshot is None:
        return None, None
    return latest_snapshot.cost, latest_snapshot.daily_budget


def _build_guardrail_context(db: Session, proposal: NaverProposal, now: datetime) -> dict:
    """guardrail_gate.check()에 넘길 라이브 상태 precompute (X1b T4, P2 D-NAO-42-f 확장,
    X1b-S S1 D-NAO-43 adgroup lock 최소 컨텍스트 확장).
    keyword·campaign 대상은 전체 필드 지원, adgroup 대상은 쿨다운 필드만(아래 참조) — 그
    외 target_type이면 전부 None(guardrail_gate가 fail-closed로 차단).

    재조회 실패·데이터 부재는 전부 None으로 남긴다(추정으로 채우지 않음 — guardrail_gate가
    None 필드를 만나면 검증불가로 차단하는 것이 기존 계약, T2 참조).
    current_bid(keyword)/current_budget(campaign, P2): 라이브 재조회(naver_sa_writer의
      get_keyword/get_campaign) — writer 자체의 before 재조회와 별개(가드 판정 시점과
      실쓰기 시점 값이 다를 수 있어 각자 재조회, writer의 after 검증이 최종 안전망).
    roas_corrected/unconverted_spend: keyword는 account_diagnosis.keyword_window_agg,
      campaign(P2)은 account_diagnosis.campaign_window_agg 재사용(둘 다 30일 창,
      as_of=어제 — naver_ad_daily는 D-1까지만 확정) + diagnosis.correction_factor
      (D-NAO-21, 진단과 동일 산식·양쪽 공유).
    cost_today/daily_budget: NaverHourlySnapshot 당일 최신 스냅샷(캠페인 단위,
      budget_allocator와 동일 소스 — keyword/campaign 공통, proposal.campaign_id 기준이라
      분기 불필요).
    last_change_at/changes_today_count: naver_change_log 이력(entity_type/entity_id 기준,
      액션 유형 무관 — 정지 다음 곧바로 입찰·예산 변경하는 것도 동일 쿨다운·상한 대상으로
      본다. campaign 대상이면 entity_type='campaign'이 자연히 걸린다).

    target_type='adgroup'(X1b-S S1, D-NAO-43 쇼핑 스톱로스 정지·재개 + X1b-S bid 확장,
      D-NAO-16 3단계 SHOPPING 대칭 + S3 D-NAO-43 성장 확장): current_bid는
      naver_sa_writer._get_adgroup 라이브 재조회로 채운다(get_keyword의 adgroup 대칭) —
      bid_down/pause/resume은 guardrail_gate에서 스톱로스·BEP·일예산이 up 전용 검사라
      면제되므로 current_bid만으로 충분. **proposal_type이 bid_up/growth_bid_up(증액)일
      때만** roas_corrected/target_roas/unconverted_spend/cost_today/daily_budget도 채운다
      (S3, account_diagnosis.adgroup_window_agg + campaign_target_resolver + 당일 스냅샷 —
      keyword/campaign 브랜치와 동형 재사용, 아래 공용 헬퍼로 통합). 이건 안전 핵심이다:
      guardrail_gate._check_bid의 BEP 검사(roas_corrected/target_roas)는 그 값이 None이면
      fail-open(검사를 건너뜀)이므로, 컨텍스트가 채워지지 않은 상태로 guardrail_gate에
      넘기면 D-NAO-1 이익하한이 조용히 우회된다 — _execute_update_bid가 이 컨텍스트의
      완전성(둘 다 not None)을 실행 직전에 다시 확인해 fail-closed로 메꾼다(guardrail_gate
      자체는 변경하지 않음, 완료기준 참조). current_budget은 여전히 adgroup에 해당 없는
      필드라 None 유지. 쿨다운·일일상한(_check_cooldown_and_cap)까지 통째로 None/0
      고정이면 그 안전장치 자체가 adgroup에서 항상 fail-open(무력화)되므로,
      last_change_at/changes_today_count는 proposal_type·방향 무관하게 항상 아래 공용
      change_rows 조회로 채운다.
    """
    context: dict = {
        "current_bid": None, "current_budget": None, "roas_corrected": None, "target_roas": None,
        "cost_today": None, "daily_budget": None, "unconverted_spend": None,
        "last_change_at": None, "changes_today_count": 0,
    }
    if proposal.target_type == "adgroup":
        # X1b-S bid 확장(D-NAO-16 3단계 SHOPPING 대칭, shopping_group_bep 보드): current_bid는
        # naver_sa_writer._get_adgroup 라이브 재조회로 채운다(get_keyword의 adgroup 대칭 —
        # writer 자체의 before 재조회와 별개, 가드 판정 시점과 실쓰기 시점 값이 다를 수 있어
        # 각자 재조회). current_budget은 adgroup에 해당 없는 필드라 항상 None.
        try:
            live = naver_sa_writer._get_adgroup(proposal.target_id)
            context["current_bid"] = live.get("bidAmt")
        except Exception as e:  # noqa: BLE001 — 재조회 실패는 fail-closed(current_bid=None 유지)
            log.warning(
                "naver_execution_harness: guardrail context _get_adgroup 실패(fail-closed) "
                "target_id=%s: %s", proposal.target_id, e,
            )

        # S3(D-NAO-43 성장 확장): 증액(bid_up/growth_bid_up)만 up-only 가드 원료를 채운다 —
        # bid_down/pause/resume은 그 검사 자체가 면제라 여전히 None(회귀 없음, keyword/
        # campaign 브랜치와 동일 as_of=D-1 창 + diagnosis.correction_factor 공유).
        if proposal.proposal_type in ("bid_up", "growth_bid_up"):
            as_of = now.date() - timedelta(days=1)  # naver_ad_daily는 D-1까지만 확정
            window_from = as_of - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS - 1)
            agg = account_diagnosis.adgroup_window_agg(db, proposal.target_id, window_from, as_of)
            if agg["cost"] > 0:
                correction = compute_correction_factor(db, as_of)
                roas_naver = agg["conv_amt"] / agg["cost"]
                context["roas_corrected"] = roas_naver * float(correction["factor"])
                context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0
            context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
            context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
                db, proposal.campaign_id, now.date(),
            )

        change_rows = (
            db.query(NaverChangeLog.changed_at)
            .filter(
                NaverChangeLog.entity_type == proposal.target_type,
                NaverChangeLog.entity_id == proposal.target_id,
                NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
            )
            .all()
        )
        if change_rows:
            context["last_change_at"] = max(r[0] for r in change_rows)
            today_start = datetime.combine(now.date(), datetime.min.time())
            context["changes_today_count"] = sum(1 for r in change_rows if r[0] >= today_start)
        return context

    if proposal.target_type == "ad":
        # B3(D-NAO-65 설계질문 3·4): 소재-레벨 입찰 제어. current_bid = 라이브 소재 bidAmt
        # (adAttr.bidAmt) 재조회 — ±15% 클램프·방향검증이 소재 자기 입찰 기준으로 돈다
        # (그룹/키워드 대칭, writer의 before 재조회와 별개라 각자 재조회). 쿨다운 시계는 아래
        # change_rows가 entity_type='ad'·entity_id=nccAdId로 조회 = 소재 단위 독립 쿨다운.
        try:
            context["current_bid"] = naver_sa_writer.get_ad_bid(proposal.target_id)
        except Exception as e:  # noqa: BLE001 — 재조회 실패는 fail-closed(current_bid=None 유지)
            log.warning(
                "naver_execution_harness: guardrail context get_ad_bid 실패(fail-closed) "
                "target_id=%s: %s", proposal.target_id, e,
            )

        # 증액(bid_up/growth_bid_up)만 up-only 가드 원료를 채운다 — 소재 단위 실적은
        # naver_ad_daily에 없어(그레인=adgroup/keyword) 부모 광고그룹(proposal.adgroup_id) 창
        # agg로 BEP/스톱로스/일예산을 근사한다(소재-레벨 daily 부재 시 최선 근사 — down은 이
        # 검사가 면제라 여전히 None, keyword/adgroup up 브랜치와 동형 재사용). adgroup_id가
        # 없으면 roas_corrected가 None으로 남아 executor가 fail-closed로 막는다(아래 S3 가드).
        if proposal.proposal_type in ("bid_up", "growth_bid_up") and proposal.adgroup_id:
            as_of = now.date() - timedelta(days=1)  # naver_ad_daily는 D-1까지만 확정
            window_from = as_of - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS - 1)
            agg = account_diagnosis.adgroup_window_agg(db, proposal.adgroup_id, window_from, as_of)
            if agg["cost"] > 0:
                correction = compute_correction_factor(db, as_of)
                roas_naver = agg["conv_amt"] / agg["cost"]
                context["roas_corrected"] = roas_naver * float(correction["factor"])
                context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0
            context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
            context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
                db, proposal.campaign_id, now.date(),
            )

        change_rows = (
            db.query(NaverChangeLog.changed_at)
            .filter(
                NaverChangeLog.entity_type == proposal.target_type,  # 'ad'
                NaverChangeLog.entity_id == proposal.target_id,
                NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
            )
            .all()
        )
        if change_rows:
            context["last_change_at"] = max(r[0] for r in change_rows)
            today_start = datetime.combine(now.date(), datetime.min.time())
            context["changes_today_count"] = sum(1 for r in change_rows if r[0] >= today_start)
        return context

    if proposal.target_type not in ("keyword", "campaign"):
        return context

    as_of = now.date() - timedelta(days=1)  # naver_ad_daily는 D-1까지만 확정
    window_from = as_of - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS - 1)

    if proposal.target_type == "keyword":
        try:
            live = naver_sa_writer.get_keyword(proposal.target_id)
            context["current_bid"] = live.get("bidAmt")
        except Exception as e:  # noqa: BLE001 — 재조회 실패는 fail-closed(current_bid=None 유지)
            log.warning(
                "naver_execution_harness: guardrail context get_keyword 실패(fail-closed) "
                "target_id=%s: %s", proposal.target_id, e,
            )
        agg = account_diagnosis.keyword_window_agg(db, proposal.target_id, window_from, as_of)
    else:  # campaign (P2, D-NAO-42-f 예산 통제 컨텍스트)
        try:
            # codex[P1, Fix 2]: campaign_id로 재조회한다(target_id 아님) — 집계(agg, 아래)는
            # 이미 campaign_id를 쓰고 있었으므로 current_budget도 같은 캠페인을 가리켜야
            # "가드 판정 대상"과 "실쓰기 대상"이 항상 동일 캠페인이 된다(target_id는 실행 함수
            # 쪽 별도 가드로 campaign_id와 항상 같음이 이미 강제됨, 여기선 명시적으로 campaign_id).
            live = naver_sa_writer.get_campaign(proposal.campaign_id)
            context["current_budget"] = live.get("dailyBudget")
        except Exception as e:  # noqa: BLE001 — 재조회 실패는 fail-closed(current_budget=None 유지)
            log.warning(
                "naver_execution_harness: guardrail context get_campaign 실패(fail-closed) "
                "campaign_id=%s: %s", proposal.campaign_id, e,
            )
        agg = account_diagnosis.campaign_window_agg(db, proposal.campaign_id, window_from, as_of)

    if agg["cost"] > 0:
        correction = compute_correction_factor(db, as_of)
        roas_naver = agg["conv_amt"] / agg["cost"]
        context["roas_corrected"] = roas_naver * float(correction["factor"])
        context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0

    context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
    context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
        db, proposal.campaign_id, now.date(),
    )

    # codex[P2]: dry-run·실패(outcome='failed', _guard_failure 포함) 행은 네이버 상태를
    # 바꾸지 않았다 — 그런데도 쿨다운·일일상한에 포함시키면 실제로는 아무 일도 안 일어났는데
    # 다음 실행이 차단된다. 실제 쓰기가 확정된 행만 센다 — outcome이 아니라 after_value
    # 존재 여부로 판별(outcome은 D+14 채점 전 NULL, "executed" 영구 상태 아님 — 위
    # _execute_add_negative_keyword 주석 참조).
    change_rows = (
        db.query(NaverChangeLog.changed_at)
        .filter(
            NaverChangeLog.entity_type == proposal.target_type,
            NaverChangeLog.entity_id == proposal.target_id,
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
        )
        .all()
    )
    if change_rows:
        context["last_change_at"] = max(r[0] for r in change_rows)
        today_start = datetime.combine(now.date(), datetime.min.time())
        context["changes_today_count"] = sum(1 for r in change_rows if r[0] >= today_start)

    return context


def _execute_add_negative_keyword(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """제외키워드 실쓰기 1건 (X1a T3). naver_sa_writer 호출 → 성공/실패 모두 change_log 전건
    기록(D-NAO-12) — 성공은 before/after 실측값+created_ids(원복 원료, T3 이후 원복 기능의
    유일한 재료라 반드시 저장), 실패는 outcome='failed'+예외 요약을 커밋한 후 원 예외 재전파.

    클레임 우선(codex P1): 가드 통과 후 writer 호출 **전에** status='executing'을 커밋한다
    (내구 클레임) — 크래시/동시호출 시 approved 게이트가 재진입을 자연 차단한다. 성공하면
    approved로 복원(같은 커밋에 executed_change_log_id 연결 — "실행됨" 마커는
    executed_change_log_id. 'executed' 신규 status는 다운스트림 필터 영향 미조사라 도입 안 함).
    ⚠️ 'executing'에 멈춘 채 잔존하는 제안 = 크래시로 쓰기 결과 불확실 — 자동 복구하지
    않는다. 사람이 네이버 콘솔/재조회(GET restricted-keywords)로 실제 반영 여부를 확인한 후
    수동 처리해야 한다(사람 조사 대상).

    실패 시 proposal.status='failed' — approved 게이트가 자동 재시도를 자연 차단한다
    (재시도는 사람이 콘솔에서 재승인하는 것이 유일 경로, D-NAO-5와 일관).
    """
    if proposal.target_type != "search_term":
        reason = (
            f"target_type={proposal.target_type!r} — restricted-keywords는 검색어 텍스트를 "
            f"등록하는 API. target_type='keyword'(_bid_proposal 격상 경로) 제안의 target_id는 "
            f"nkw-… ID라서 그대로 등록하면 무의미한 문자열이 제외키워드로 등록됨(fail-closed) "
            f"— search_term 제안만 실행 가능"
        )
        _guard_failure(db, proposal, now, "add_negative_keyword", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if not proposal.adgroup_id:
        reason = (
            "adgroup_id 없음 — restricted-keywords API는 adgroupId 필수(ref 27 §8-1). "
            "구 제안이거나 격상 경로 제안 — 재생성 필요"
        )
        _guard_failure(db, proposal, now, "add_negative_keyword", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    try:
        result = naver_sa_writer.add_restricted_keywords(proposal.adgroup_id, [proposal.target_id])
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="add_negative_keyword",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
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

    # codex[P2, T5 배선확인, Claude 적대적 리뷰 — codex 한도 소진 대체]: outcome은 여기서
    # "executed"로 채우지 않는다(X1a T3 원안 결함, 지금 발견·수정) — proposal_scoreboard.
    # run_daily()가 "실행됐지만 아직 미검증"을 outcome IS NULL로 식별한다(Phase 6 설계
    # 원안·test_naver_proposal_scoreboard.py의 _change() 기본값이 이미 그렇게 되어 있었음).
    # 여기서 outcome="executed"를 즉시 박으면 그 필터에 영원히 안 걸려 D+14 채점이 전혀
    # 안 도는 상태였다(D-NAO-14 학습루프 핵심 기능 무력화). 실제 성공 여부는 after_value가
    # 채워졌는지로 판별한다(_detect_external_change·guardrail 쿨다운·resume_candidates 참조).
    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="add_negative_keyword",
        rationale=proposal.rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=False,
        before_value=json.dumps(result.before, ensure_ascii=False),
        after_value=json.dumps(
            {"after": result.after, "created_ids": result.created_ids}, ensure_ascii=False
        ),
        changed_at=now, executed_at=now, verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    proposal.status = "approved"  # 클레임 해제(복원) — 같은 커밋에 executed_change_log_id 연결
    db.commit()

    log.info(
        "naver_execution_harness: 실쓰기 성공 proposal_id=%s adgroup=%s keyword=%r created_ids=%s",
        proposal.id, proposal.adgroup_id, proposal.target_id, result.created_ids,
    )
    return log_entry


def _execute_update_bid(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """입찰가 실쓰기 1건 (X1b T4, D-NAO-16 3단계 + X1b-S bid 확장, SHOPPING 대칭 + S3
    D-NAO-43 성장 확장). bid_up/bid_down/growth_bid_up 공용 — target_bid 컬럼(X1b T3,
    proposal_writer가 구조화 저장)을 그대로 쓴다(rationale 텍스트 파싱 금지).
    guardrail_gate.check()가 실행 직전 최종 관문(§4 실행 순서의 "가드레일" 단계).

    구조 결함(target_type이 'keyword'·'adgroup' 어느 쪽도 아님·target_bid 없음)·가드레일
    위반은 전부 _guard_failure로 failed 종결(재승인 루프 방지, _execute_add_negative_keyword와
    동일 원칙). target_type='keyword'는 naver_sa_writer.update_keyword_bid, 'adgroup'
    (shopping_group_bep/shopping_group_growth 보드, ref 27 §85)은
    naver_sa_writer.update_adgroup_bid로 분기만 다르고 가드·클레임·기록 로직은 완전 대칭
    (_execute_set_user_lock의 keyword/adgroup 분기와 동형). 캠페인 단위 입찰은 여전히
    미구현(정직 경계).

    ★안전 핵심(S3): adgroup 증액(bid_up/growth_bid_up)은 컨텍스트의 roas_corrected와
    target_roas가 둘 다 not None일 때만 guardrail_gate로 넘어간다 — guardrail_gate._check_bid
    의 BEP 검사는 이 값들이 None이면 fail-open(검사를 건너뜀)이므로, 여기서 fail-closed로
    메꾸지 않으면 D-NAO-1 이익하한이 컨텍스트 미비 상태로 조용히 우회된다(S2의 blanket
    차단을 이 데이터 기반 차단으로 대체).
    """
    if proposal.target_type not in ("keyword", "adgroup", "ad"):
        reason = (
            f"target_type={proposal.target_type!r} — 키워드·광고그룹·소재(ad) 단위 입찰만 구현됨"
            "(캠페인 단위는 미구현, 정직 경계)"
        )
        _guard_failure(db, proposal, now, "update_bid", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if proposal.target_bid is None:
        reason = "target_bid 없음 — 구조 결함(구 제안이거나 격상 경로, 재생성 필요)"
        _guard_failure(db, proposal, now, "update_bid", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # codex 소급[P2] 2026-07-20 — B3 카나리의 **최종 쓰기 경계** 이중화(D-NAO-13 optimizer
    # 쓰기 직전 하드 체크와 동형 관례): 카나리·방향 제한은 생성(proposal_writer)·위임
    # (delegation_gate)에서 강제되지만, 실행자는 승인된 'ad' 제안을 재검증 없이 썼다 —
    # stale 제안(카나리 상수 축소 후 잔존 pending)이나 경로 밖에서 생성된 제안이 콘솔
    # 승인만으로 최종 경계를 통과하는 fail-open. 쓰기 직전 fail-closed로 재검증한다.
    # 함수 레벨 import — delegation_gate와 동일 관례(auto_operator 모듈 결합 최소화).
    if proposal.target_type == "ad":
        from app.services.naver_ad.auto_operator import (
            AD_BID_CANARY_CAMPAIGNS, _AD_BID_CANARY_DIRECTIONS,
        )
        ad_guard = []
        if proposal.campaign_id not in AD_BID_CANARY_CAMPAIGNS:
            ad_guard.append("캠페인이 소재입찰 카나리 개방 대상 아님")
        if proposal.proposal_type not in _AD_BID_CANARY_DIRECTIONS:
            ad_guard.append(
                f"proposal_type={proposal.proposal_type!r}는 소재-레벨 미개방 방향"
                f"(개방={sorted(_AD_BID_CANARY_DIRECTIONS)})"
            )
        if not proposal.adgroup_id:
            ad_guard.append("adgroup_id 없음(소재 제안 필수 컨텍스트)")
        if ad_guard:
            reason = "소재(ad) 실쓰기 경계 차단(fail-closed) — " + " · ".join(ad_guard)
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    context = _build_guardrail_context(db, proposal, now)

    # S3(D-NAO-43 성장 확장): adgroup 증액(bid_up/growth_bid_up)은 guardrail_gate._check_bid의
    # BEP 검사(roas_corrected/target_roas)가 그 값이 None이면 fail-open(검사를 건너뜀)이라,
    # 컨텍스트가 완전히 채워지지 않은 채로 guardrail_gate에 넘기면 D-NAO-1 이익하한이 조용히
    # 우회된다. _build_guardrail_context의 adgroup up 브랜치가 이 두 값을 채우려 시도하지만
    # (adgroup_window_agg 창에 실적이 없거나 재조회가 실패하면 여전히 None일 수 있음) —
    # executor가 실행 직전 한 번 더 완전성을 확인해 fail-closed로 막는다(guardrail_gate
    # 자체는 변경하지 않음 — S2의 blanket 차단을 이 데이터 기반 차단으로 대체).
    if proposal.target_type in ("adgroup", "ad") and proposal.proposal_type in ("bid_up", "growth_bid_up"):
        # guardrail_gate._check_bid의 up 전용 검사(BEP·스톱로스·일예산)는 그 원료가 None이면
        # 전부 fail-open(검사 건너뜀)이다 — 컨텍스트가 불완전한 채 넘기면 D-NAO-1 이익하한·
        # 일예산 상한이 조용히 우회된다. 각 원료의 소스가 달라(BEP/스톱로스=adgroup_window_agg,
        # 일예산=당일 스냅샷+_get_adgroup) 하나가 채워져도 다른 게 빌 수 있으므로, executor가
        # 실행 직전 완전성을 종합 확인해 fail-closed로 막는다(guardrail_gate 자체는 불변).
        missing = []
        if context.get("roas_corrected") is None or context.get("target_roas") is None:
            # 스톱로스 원료(unconverted_spend)는 roas_corrected와 동일 agg 소스라 roas가
            # not None이면 함께 채워짐 — 별도 검사 불요.
            missing.append(
                f"BEP(roas_corrected={context.get('roas_corrected')!r}, "
                f"target_roas={context.get('target_roas')!r})"
            )
        daily_budget = context.get("daily_budget")
        cost_today = context.get("cost_today")
        # daily_budget is None = 예산 미확보(스냅샷/재조회 실패)라 상한 검증 불가 → 차단.
        # daily_budget == 0 = uncapped(useDailyBudget=false, 무제한)라 상한 검사 자체가 없음 →
        #   cost_today None이어도 허용(codex[P2] 이력: 0은 uncapped, guardrail도 동일 처리).
        # daily_budget > 0 인데 cost_today None = 오늘 소진 미확보 → 상한 검증 불가 → 차단.
        if daily_budget is None or (daily_budget > 0 and cost_today is None):
            missing.append(f"일예산(daily_budget={daily_budget!r}, cost_today={cost_today!r})")
        if missing:
            reason = (
                "adgroup 증액 가드 컨텍스트 불완전(fail-closed) — " + ", ".join(missing)
                + " (guardrail_gate의 해당 up 검사는 값이 None이면 fail-open이라 executor가 "
                "메꿈, codex[P1] D-NAO-43 S3)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    gate_proposal = {
        "proposal_type": proposal.proposal_type, "target_bid": proposal.target_bid, "target_lock": None,
    }
    block_reason = guardrail_gate.check(gate_proposal, context, now=now)
    if block_reason is not None:
        reason = f"가드레일 차단 — {block_reason}"
        _guard_failure(db, proposal, now, "update_bid", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    if proposal.target_type == "keyword":
        writer_fn = naver_sa_writer.update_keyword_bid
    elif proposal.target_type == "adgroup":
        writer_fn = naver_sa_writer.update_adgroup_bid
    else:  # ad (B3, D-NAO-65) — 소재 bidAmt 직접 수정(useGroupBidAmt=false 실효 소재)
        writer_fn = naver_sa_writer.update_ad_bid
    try:
        result = writer_fn(proposal.target_id, proposal.target_bid)
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="update_bid",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        log.error(
            "naver_execution_harness: 실쓰기 실패 proposal_id=%s target_type=%s target=%s "
            "target_bid=%s — %s: %s",
            proposal.id, proposal.target_type, proposal.target_id, proposal.target_bid,
            type(exc).__name__, exc,
        )
        raise

    conflict_warning = _detect_external_change(db, proposal, result.before, "bidAmt")
    rationale = f"{proposal.rationale or ''} {conflict_warning}" if conflict_warning else proposal.rationale

    # outcome 미기록 — proposal_scoreboard.run_daily()가 D+14까지 IS NULL로 식별(위
    # _execute_add_negative_keyword 주석 참조).
    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="update_bid",
        rationale=rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=False,
        before_value=json.dumps(result.before, ensure_ascii=False),
        after_value=json.dumps(result.after, ensure_ascii=False),
        changed_at=now, executed_at=now, verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    proposal.status = "approved"
    db.commit()

    log.info(
        "naver_execution_harness: 실쓰기 성공 proposal_id=%s target_type=%s target=%s target_bid=%s",
        proposal.id, proposal.target_type, proposal.target_id, proposal.target_bid,
    )
    return log_entry


def _execute_set_user_lock(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """정지·재개 실쓰기 1건 (X1b T4, D-NAO-16 2단계 + X1b-S S1 쇼핑 adgroup 확장, D-NAO-43).
    pause/resume 공용 — target_lock 컬럼(X1b T3)을 그대로 쓴다. target_type='keyword'는
    WEB_SITE 키워드(naver_sa_writer.set_keyword_lock), target_type='adgroup'은 SHOPPING
    광고그룹(naver_sa_writer.set_adgroup_lock) — writer 호출만 분기하고 가드·클레임·기록
    로직은 완전 대칭(양쪽 다 change_log.action="set_user_lock"으로 기록, 기존 pause/resume
    관례 유지). guardrail_gate가 방향 일치(pause→true/resume→false, T2 codex 반영분)까지
    재검증한다."""
    if proposal.target_type not in ("keyword", "adgroup"):
        reason = (
            f"target_type={proposal.target_type!r} — 키워드·광고그룹 단위 정지·재개만 구현됨"
            "(캠페인 단위 userLock은 미구현, 정직 경계)"
        )
        _guard_failure(db, proposal, now, "set_user_lock", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if proposal.target_lock is None:
        reason = "target_lock 없음 — 구조 결함(구 제안, 재생성 필요)"
        _guard_failure(db, proposal, now, "set_user_lock", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    context = _build_guardrail_context(db, proposal, now)
    gate_proposal = {
        "proposal_type": proposal.proposal_type, "target_bid": None, "target_lock": proposal.target_lock,
    }
    block_reason = guardrail_gate.check(gate_proposal, context, now=now)
    if block_reason is not None:
        reason = f"가드레일 차단 — {block_reason}"
        _guard_failure(db, proposal, now, "set_user_lock", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    writer_fn = (
        naver_sa_writer.set_keyword_lock if proposal.target_type == "keyword"
        else naver_sa_writer.set_adgroup_lock
    )
    try:
        result = writer_fn(proposal.target_id, proposal.target_lock)
    except Exception as exc:
        proposal.status = "failed"
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="set_user_lock",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        log.error(
            "naver_execution_harness: 실쓰기 실패 proposal_id=%s keyword=%s target_lock=%s — %s: %s",
            proposal.id, proposal.target_id, proposal.target_lock, type(exc).__name__, exc,
        )
        raise

    conflict_warning = _detect_external_change(db, proposal, result.before, "userLock")
    rationale = f"{proposal.rationale or ''} {conflict_warning}" if conflict_warning else proposal.rationale

    # outcome 미기록 — proposal_scoreboard.run_daily()가 D+14까지 IS NULL로 식별(위
    # _execute_add_negative_keyword 주석 참조).
    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="set_user_lock",
        rationale=rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=False,
        before_value=json.dumps(result.before, ensure_ascii=False),
        after_value=json.dumps(result.after, ensure_ascii=False),
        changed_at=now, executed_at=now, verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    proposal.status = "approved"
    db.commit()

    log.info(
        "naver_execution_harness: 실쓰기 성공 proposal_id=%s keyword=%s target_lock=%s",
        proposal.id, proposal.target_id, proposal.target_lock,
    )
    return log_entry


def _execute_update_budget(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """캠페인 일예산 실쓰기 1건 (P3, D-NAO-16 4단계, D-NAO-42-f). budget_up/budget_down 공용 —
    target_budget 컬럼(P1, proposal_writer가 구조화 저장)을 그대로 쓴다(rationale 텍스트
    파싱 금지). guardrail_gate.check()가 실행 직전 최종 관문(_check_budget, PLAN §5-C) —
    클램프·방향·+100%캡(증액만)·스톱로스(증액만)·BEP(증액만)·쿨다운/일일상한.

    구조 결함(target_type≠'campaign'·target_budget 없음)·가드레일 위반은 전부 _guard_failure로
    failed 종결(재승인 루프 방지, _execute_update_bid와 동일 원칙). 캠페인 단위 쓰기라
    target_id==campaign_id(proposal_writer._budget_proposal이 그렇게 저장) — writer 호출은
    proposal.campaign_id를 쓴다(target_id와 동일값이지만 의미상 캠페인ID를 명시).

    라운드 봉투(budget_auto_eligible, §5-E)는 여기서 소비하지 않는다 — 오늘은 반자동이라
    Jino 콘솔 승인(status='approved')이 실행 전 유일한 게이트이고, 라운드 봉투는 자율(위임)
    승급 후 자동발사 경로에서만 실효(PLAN §5-E 주석)."""
    if proposal.target_type != "campaign":
        reason = (
            f"target_type={proposal.target_type!r} — 캠페인 단위 예산만 구현됨(정직 경계)"
        )
        _guard_failure(db, proposal, now, "update_budget", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if proposal.target_budget is None:
        reason = "target_budget 없음 — 구조 결함(구 제안이거나 재생성 필요)"
        _guard_failure(db, proposal, now, "update_budget", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # codex[P1, Fix 2]: 캠페인 단위 예산 쓰기는 target_id==campaign_id가 불변이어야 한다
    # (proposal_writer._budget_proposal이 그렇게 저장). 어긋나면 stale/malformed 제안 —
    # writer 호출(_build_guardrail_context의 재조회 포함)에 어느 캠페인을 쓸지 모호해지므로
    # fail-closed(이중 방벽: real_write_blocker도 동일 조건으로 UI 표시를 미리 막는다).
    if proposal.target_id != proposal.campaign_id or not proposal.target_id or not proposal.campaign_id:
        reason = (
            f"target_id={proposal.target_id!r} != campaign_id={proposal.campaign_id!r}(또는 "
            "둘 중 하나가 비어있음) — 캠페인 예산 쓰기는 target_id==campaign_id가 불변, "
            "stale/malformed 제안(fail-closed)"
        )
        _guard_failure(db, proposal, now, "update_budget", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    context = _build_guardrail_context(db, proposal, now)
    gate_proposal = {
        "proposal_type": proposal.proposal_type, "target_bid": None, "target_lock": None,
        "target_budget": proposal.target_budget,
    }
    block_reason = guardrail_gate.check(gate_proposal, context, now=now)
    if block_reason is not None:
        reason = f"가드레일 차단 — {block_reason}"
        _guard_failure(db, proposal, now, "update_budget", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    try:
        result = naver_sa_writer.update_campaign_budget(proposal.campaign_id, proposal.target_budget)
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="update_budget",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        log.error(
            "naver_execution_harness: 실쓰기 실패 proposal_id=%s campaign=%s target_budget=%s — %s: %s",
            proposal.id, proposal.campaign_id, proposal.target_budget, type(exc).__name__, exc,
        )
        raise

    conflict_warning = _detect_external_change(db, proposal, result.before, "dailyBudget")
    rationale = f"{proposal.rationale or ''} {conflict_warning}" if conflict_warning else proposal.rationale

    # outcome 미기록 — proposal_scoreboard.run_daily()가 D+14까지 IS NULL로 식별(위
    # _execute_add_negative_keyword 주석 참조).
    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="update_budget",
        rationale=rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=False,
        before_value=json.dumps(result.before, ensure_ascii=False),
        after_value=json.dumps(result.after, ensure_ascii=False),
        changed_at=now, executed_at=now, verify_date=(now + timedelta(days=VERIFY_DAYS)).date(),
    )
    db.add(log_entry)
    db.flush()
    proposal.executed_change_log_id = log_entry.id
    proposal.status = "approved"
    db.commit()

    log.info(
        "naver_execution_harness: 실쓰기 성공 proposal_id=%s campaign=%s target_budget=%s",
        proposal.id, proposal.campaign_id, proposal.target_budget,
    )
    return log_entry


# 실쓰기 디스패치 테이블 — OPEN_ACTIONS와 별도(이중 방벽): OPEN_ACTIONS에 있어도 여기 구현이
# 없으면 WriteNotOpenedError(fail-closed). 액션 확장 시 두 곳을 모두 의도적으로 갱신해야 한다.
_WRITE_EXECUTORS = {
    "add_negative_keyword": _execute_add_negative_keyword,
    "update_bid": _execute_update_bid,
    "set_user_lock": _execute_set_user_lock,
    "update_budget": _execute_update_budget,
}


def open_executable_actions() -> list[str]:
    """지금 실제로 실쓰기 가능한 액션 목록(정렬). 이중 방벽의 **교집합**이다:
    `OPEN_ACTIONS`(D-NAO-16 개방 순서)에 있으면서 `_WRITE_EXECUTORS`(디스패치 구현)에도
    있는 것만. 콘솔 배너의 "현재 개방" 표시가 이 값을 진실로 삼는다(공개 헬퍼로 노출해
    라우터가 프라이빗 `_WRITE_EXECUTORS`를 직접 참조하지 않게 한다 — 하드코딩 라벨이
    개방 순서와 어긋나던 결함 재발 방지)."""
    return sorted(OPEN_ACTIONS & set(_WRITE_EXECUTORS))


def real_write_blocker(proposal: NaverProposal) -> str | None:
    """이 제안이 지금 실쓰기 불가능한 이유(사람이 읽을 한국어 문자열)를 반환, 가능하면 None
    (X1a T4). 콘솔의 실행 버튼 활성화 여부(`executable`, naver_ad.py `_serialize_proposal`)와
    사람이 읽을 사유(`not_executable_reason`) 판정에 쓰인다. **판정만 하고 DB를 절대
    건드리지 않는다** — 부수효과 없는 순수 함수.

    판정 순서:
    ①`_ACTION_BY_PROPOSAL_TYPE`에 매핑이 없음 → 정보성 제안(실행 대상 자체가 없음).
    ②action이 `OPEN_ACTIONS`(D-NAO-16 개방 순서) 또는 `_WRITE_EXECUTORS`(이중 방벽)에
      없음 → 아직 미개방.
    ③action=='add_negative_keyword'인데 target_type이 'search_term'이 아니거나
      adgroup_id가 없음 → restricted-keywords API 대상으로 부적합
      (`_execute_add_negative_keyword`의 MissingExecutionTargetError 가드와 동일 조건).
    ④(X1b T4 + X1b-S bid 확장) action=='update_bid'인데 target_type이 'keyword'·'adgroup'
      어느 쪽도 아니거나(캠페인 단위 bidAmt는 미구현, 정직 경계) target_bid가 없음 →
      실행 대상 부적합. adgroup 증액(bid_up/growth_bid_up)의 BEP 컨텍스트 완전성(S3,
      D-NAO-43)은 여기서 판정하지 않는다 — 라이브 재조회·window agg가 필요한 데이터
      검증이라 목록 조회 비용 문제(아래 ⚠️ 참조)로 실행 시도 시점(_execute_update_bid)에만
      확인한다.
    ⑤(X1b T4 + X1b-S S1 D-NAO-43) action=='set_user_lock'인데 target_type이 'keyword'·
      'adgroup' 어느 쪽도 아니거나(캠페인 단위 userLock은 미구현, 정직 경계) target_lock이
      없음 → 실행 대상 부적합.
    ⑥(P3, D-NAO-42-f) action=='update_budget'인데 target_type이 'campaign'이 아니거나
      target_budget이 없거나 target_id!=campaign_id(Fix 2, codex P1 — 캠페인 예산 쓰기는
      두 값이 항상 같아야 함) → 실행 대상 부적합.

    ⚠️ ③~⑥의 판정은 harness의 실행 함수 내부 가드와 의도적으로 중복이다(이중 방벽 — 그
    가드는 제거하지 않는다). 이 함수는 UI 표시(`executable`)용 구조 판정만 하고,
    guardrail_gate.check()(±15%·쿨다운·일일상한·스톱로스·BEP증액금지·일예산 — 라이브
    재조회가 필요)는 **실행 시도 시점에만** 돈다 — 목록 조회마다 매번 네이버 API를 호출하면
    콘솔 로딩이 승인된 제안 수만큼 API 콜을 유발하므로(비용·레이트리밋), 여기서는 하지
    않는다(설계 결정, T4). `POST /proposals/{id}/execute`(naver_ad.py)의 사전 차단은
    ①·②만 가로챈다 — ③~⑥(구조 결함)·가드레일 위반은 harness에 그대로 넘겨
    MissingExecutionTargetError→422+failed 감사 기록 경로를 타게 한다(라우터가 여기서
    선점하면 그 감사 기록이 안 남는다 — T4 설계, 라우터 docstring 참조).
    """
    action = _ACTION_BY_PROPOSAL_TYPE.get(proposal.proposal_type)
    if action is None:
        # P4 리뷰 P3-3: 결정 전용(param_change)을 "정보성"으로 오라벨하면 informational=False/
        # decision_only=True 파생값과 모순되는 문자열이 API에 남는다 — 유형별 정직 표기.
        if proposal.proposal_type == "param_change":
            return "결정 전용 제안 — 승인=기록만, 자동 적용 없음(D-NAO-54 금지선)"
        return "정보성 제안 — 실행 대상 아님"
    if action not in OPEN_ACTIONS or action not in _WRITE_EXECUTORS:
        return "액션 미개방(D-NAO-16 개방 순서, 아직 코드 배포 전)"
    if action == "add_negative_keyword":
        if proposal.target_type != "search_term":
            return (
                f"target_type={proposal.target_type!r} — negative_keyword 실행은 "
                "search_term 대상만 가능(격상 경로 제안은 재생성 필요)"
            )
        if not proposal.adgroup_id:
            return "adgroup_id 없음 — 실행 대상 정보 부족(구 제안이거나 재생성 필요)"
    elif action == "update_bid":
        if proposal.target_type not in ("keyword", "adgroup", "ad"):
            return (
                f"target_type={proposal.target_type!r} — 키워드·광고그룹·소재(ad) 단위 입찰만 "
                "구현됨(캠페인 단위는 미구현, 정직 경계)"
            )
        if proposal.target_bid is None:
            return "target_bid 없음 — 실행 대상 정보 부족(구 제안이거나 재생성 필요)"
        # codex 소급[P2] 2026-07-20: 소재(ad) 실쓰기는 카나리 캠페인·개방 방향 안에서만 —
        # _execute_update_bid의 최종 경계 가드와 동일 판정(이중 방벽). 정적 상수 비교라
        # API 콜 없음(이 함수의 "라이브 재조회 금지" 설계 유지). stale 제안(카나리 축소 후
        # 잔존 pending)이 콘솔에서 executable로 보이는 것을 막는다.
        if proposal.target_type == "ad":
            from app.services.naver_ad.auto_operator import (
                AD_BID_CANARY_CAMPAIGNS, _AD_BID_CANARY_DIRECTIONS,
            )
            if proposal.campaign_id not in AD_BID_CANARY_CAMPAIGNS:
                return "소재(ad) 입찰은 카나리 캠페인만 실쓰기 가능(B3 개방 스코프 밖 — stale 제안 가능성)"
            if proposal.proposal_type not in _AD_BID_CANARY_DIRECTIONS:
                return (
                    f"소재(ad) {proposal.proposal_type}은 미개방 방향"
                    f"(현재 개방={sorted(_AD_BID_CANARY_DIRECTIONS)}, 카나리 2단계에서 확장)"
                )
            if not proposal.adgroup_id:
                return "adgroup_id 없음 — 소재 제안 필수 컨텍스트 부족(재생성 필요)"
    elif action == "set_user_lock":
        if proposal.target_type not in ("keyword", "adgroup"):
            return (
                f"target_type={proposal.target_type!r} — 키워드·광고그룹 단위 정지·재개만 "
                "구현됨(캠페인 단위 userLock은 미구현, 정직 경계)"
            )
        if proposal.target_lock is None:
            return "target_lock 없음 — 실행 대상 정보 부족(구 제안, 재생성 필요)"
    elif action == "update_budget":
        if proposal.target_type != "campaign":
            return (
                f"target_type={proposal.target_type!r} — 캠페인 단위 예산만 구현됨(정직 경계)"
            )
        if proposal.target_budget is None:
            return "target_budget 없음 — 실행 대상 정보 부족(구 제안이거나 재생성 필요)"
        # codex[P1, Fix 2]: _execute_update_budget과 동일한 이중 방벽 — target_id가
        # campaign_id와 다르면(또는 비어있으면) stale/malformed(정직 경계).
        if proposal.target_id != proposal.campaign_id or not proposal.target_id or not proposal.campaign_id:
            return (
                f"target_id={proposal.target_id!r} != campaign_id={proposal.campaign_id!r} — "
                "캠페인 예산 쓰기는 target_id==campaign_id가 불변(stale/malformed 제안)"
            )
    return None


def _resolve_optimizer(db: Session, campaign_id: str) -> str:
    settings = db.query(NaverCampaignSettings).filter(
        NaverCampaignSettings.campaign_id == campaign_id
    ).first()
    return settings.optimizer if settings else "none"


def execute(db: Session, proposal_id: int, *, dry_run: bool = True, now: datetime | None = None) -> NaverChangeLog:
    """제안 1건을 실행 시도 — 실행 여부와 무관하게 naver_change_log에 전건 기록한다.

    순서: ①제안 조회 ②실행 가능 유형인지(액션 매핑 존재) ③status=='approved' 하드체크
    (D-NAO-5 사람 승인 게이트 — pending/rejected/expired/failed/executing은 실행 불가.
    executing은 클레임 잔존 = 크래시로 쓰기 결과 불확실 — 사람 조사 대상) ④재실행 방지
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

    # codex 7R[P1](D-NAO-49): auto_operator 승인 제안 한정 킬스위치 최종 가드 — 레인의 승인
    # 커밋과 이 지점 사이에 Jino가 auto_operate를 끄는 TOCTOU 구간을 쓰기 직전 단일 지점에서
    # 봉쇄한다(부가적 가드 — 기존 게이트·실행 로직 불변, 다른 approval_source(수동 콘솔
    # NULL·delegation)는 조건 분기로 절대 영향 없음). 지연 import: auto_operator가 이 모듈을
    # module-level import하므로 역방향은 함수 안에서만(순환 회피). _auto_operate_now는 엔진
    # 레벨 독립 커넥션 조회(codex 6R)라 타 프로세스의 OFF 커밋이 항상 보인다.
    if proposal.approval_source is not None:
        from app.services.naver_ad import auto_operator as _auto_operator

        if proposal.approval_source in (
            _auto_operator.APPROVAL_SOURCE_DAILY, _auto_operator.APPROVAL_SOURCE_HOURLY,
            _auto_operator.APPROVAL_SOURCE_PROBE,  # D-NAO-58 CD2: 탐침도 동일 킬스위치 가드(우회 금지)
            _auto_operator.APPROVAL_SOURCE_REVERT,  # D-NAO-58 CD3: 되돌림도 진입 가드(probe_op와 동일 2중 harness 방어)
        ) and not _auto_operator._auto_operate_now(db, proposal.campaign_id):
            log.warning(
                "naver_execution_harness: 킬스위치 OFF — proposal_id=%s(approval_source=%s, "
                "campaign_id=%s) 실행 거부(쓰기·change_log 없음, approved 유지, codex 7R)",
                proposal.id, proposal.approval_source, proposal.campaign_id,
            )
            # D-NAO-54 P1 일기(kill_switch) — 쓰기 직전 재확인 거부(쓰기·change_log 없음).
            # 호출부 try(독립 리뷰 P2-1): 인자 평가의 만료속성 refresh I/O까지 fail-open으로 감싼다.
            try:
                diary.write_diary_entry(
                    db, "kill_switch", proposal.campaign_id,
                    actor=diary.actor_from_approval_source(proposal.approval_source),
                    target_type=proposal.target_type, target_id=proposal.target_id,
                    adgroup_id=proposal.adgroup_id, action=action,
                    rationale="킬스위치 OFF(쓰기 직전 재확인)", now=now,
                )
            except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
                log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
            raise KillSwitchEngagedError(
                f"proposal_id={proposal.id} campaign_id={proposal.campaign_id} — "
                f"auto_operate=False(킬스위치 OFF, 쓰기 직전 재확인)"
            )

    effective_dry_run = dry_run or action not in OPEN_ACTIONS
    if not effective_dry_run:
        executor = _WRITE_EXECUTORS.get(action)
        if executor is None:
            # OPEN_ACTIONS에 실수로 추가돼도 실행 함수 구현이 없으면 여기서 막힌다(fail-closed).
            raise WriteNotOpenedError(f"action={action!r}는 아직 개방되지 않음(D-NAO-16/D-NAO-5)")
        log_entry = executor(db, proposal, now)
        # D-NAO-54 P1 일기(execute) — 실쓰기 성공 1건(executor가 change_log 커밋한 직후).
        # 구조/가드/writer 실패는 executor가 예외를 던져 여기 도달하지 않는다(성공만 기록).
        # source_ref=그 change_log id, before/after_value는 실측값 그대로.
        # ★호출부 try 필수(독립 리뷰 P2-1): executor의 commit이 log_entry/proposal을 만료시켜
        # 인자 평가(log_entry.before_value 등)가 refresh SELECT(I/O)를 유발 — 여기서 예외가
        # 새면 "돈 나간 집행 완료"가 레인에서 failed로 오집계된다(집행은 이미 확정됐는데).
        try:
            diary.write_diary_entry(
                db, "execute", proposal.campaign_id,
                actor=diary.actor_from_approval_source(proposal.approval_source),
                target_type=proposal.target_type, target_id=proposal.target_id,
                adgroup_id=proposal.adgroup_id, action=action,
                before_value=log_entry.before_value, after_value=log_entry.after_value,
                rationale=proposal.rationale, source_ref=log_entry.id, now=now,
            )
        except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
            log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
        return log_entry

    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action=action,
        rationale=proposal.rationale, predicted_json=proposal.expected_effect,
        proposal_id=proposal.id, dry_run=effective_dry_run, changed_at=now, executed_at=now,
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
