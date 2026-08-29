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

from sqlalchemy import func as sqlfunc, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    NaverAdgroupProduct,
    NaverAgencyOp,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
    NaverSearchTermDaily,
)
from app.services.naver_ad import (
    account_diagnosis,
    budget_pacing,
    campaign_target_resolver,
    diary,
    guardrail_gate,
    guardrail_params,
    launch_rank_floor,
    naver_sa_writer,
    search_term_judge,
)
from app.services.naver_ad.bid_step_types import (
    APPROVAL_SOURCE_COLD,
    BID_DOWN_TYPES,
    BID_UP_TYPES,
    CHANGE_PCT_EXEMPT_TYPES,
    COLD_START_STEP_TYPES,
    EXPLORATION_STEP_TYPES,
    RANK_STEP_TYPES,
    decode_base_bid,
    decode_cold_ceiling,
    decode_exploration_ceiling,
)

# D-NAO-129: 소재(ad) 실쓰기 경계에서 **승인원 무관으로 개방**되는 UP 타입(= DOWN 대칭).
# ★불변식: 이 집합은 변경폭 상한을 면제받는 타입을 절대 포함하지 않는다. 쌍방향 (승인원⟺타입)
#   잠금의 존재 이유가 "면제 타입이 아무 승인원으로 발사되는 것"을 막는 것이기 때문이다
#   (적대 리뷰 재현: 300원→90,000원). 이 불변식은 테스트로 고정한다 —
#   test_ad_up_open_types_are_never_clamp_exempt.
_AD_UP_OPEN_TYPES: frozenset[str] = frozenset({"bid_up"})
from app.services.naver_ad.diagnosis import correction_factor as compute_correction_factor
from app.services import naver_sa_ad_fetcher as naver_sa_fetcher
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# D+14 검증 예정일(D-NAO-14 "D+7/14 실측" · proposal_pipeline._PROPOSAL_EXPIRY_DAYS와 동일
# 하한 채택) — proposal_scoreboard(Phase 6 루프1)가 이 날짜 이후 실측 대조를 수행한다.
VERIFY_DAYS = 14

# IU-R R1(P1-1): rank-step 서보 제안 신선도 상한(분). 서보는 시간당 레인에서 생성 직후 인라인
# 실행되므로 정상 경로는 초 단위다. 이 상한을 넘겨 실행되려는 서보 제안 = 콘솔 재실행·위임·
# 재시도로 나중에 살아난 stale 제안 — 그때는 경제성 상한/예산 pace가 재검증되지 않은 채 ±15%
# 면제만 적용되므로 fail-closed로 죽인다(면제의 폐루프 밖 누출 봉쇄). 위임 경로는 이미
# delegation_gate가 RANK_STEP_TYPES를 제외하므로, 이 게이트는 콘솔 재실행/재시도 잔존분 방어.
_RANK_STEP_MAX_AGE_MINUTES = 10

# CS(적대적 리뷰 P2-7): 콜드 첫 입찰 제안 신선도 상한(분). 정상 경로는 일 레인에서 생성 직후
# 인라인 실행이라 초 단위다. 이 상한을 넘겨 살아난 제안 = 킬스위치 OFF로 approved 잔존했다가
# 콘솔 재실행/재시도로 나중에 깨어난 stale 제안 — **시장가는 매일 변하므로** 어제 시세로 오늘
# 입찰하면 안 된다. rank-step보다 넉넉한 이유: 일 레인은 시장가 수집 → 레인 순으로 도는데 그
# 수집이 소재 수만큼 HTTP를 도느라 수 분이 걸린다(같은 회차 안에서 죽지 않게).
_COLD_STEP_MAX_AGE_MINUTES = 60

# guardrail_gate 컨텍스트의 실적 창(account_diagnosis.LOW_CLICK_LOOKBACK_DAYS 재사용 —
# 신규 상수 아님, pause_candidates/resume_candidates와 동일 창으로 정합성 유지).
_GUARDRAIL_LOOKBACK_DAYS = account_diagnosis.LOW_CLICK_LOOKBACK_DAYS

# SS3-A(PLAN §1 4) 검색어 제외 자동 실쓰기 1일 신규 제외 캡. 제외는 되돌림 비용이 있는 비대칭
# 액션(잘못 자르면 매출 소실)이라 BX(탐색)의 수량캡 제거(D-NAO-71)와 달리 캡을 유지한다 —
# 봉투 축소는 Jino 승인 후. account-wide 카운트(오늘 KST 성공한 exclude_search_term change_log).
_SS_DAILY_EXCLUDE_CAP = 10

# 검색어 제외 실쓰기 target_id 길이 상한 = NaverProposal.target_id 컬럼 길이(String(50)) 단일
# 출처(codex 2R[P1]). 생성 레인(search_term_ss_lane)이 초과 검색어를 skip하지만, 여기서도
# 방어 게이트를 둬 "잘린 텍스트로 실쓰기" 경로를 executor에서도 물리적으로 차단한다(SQLite는
# VARCHAR 길이를 강제하지 않아 초과 저장이 가능 → 이중 방벽 fail-closed).
_TARGET_ID_MAXLEN = NaverProposal.target_id.property.columns[0].type.length


class OptimizerGuardError(Exception):
    """optimizer!='ours' 캠페인에 대한 실행 시도 — D-NAO-13 하드체크 위반."""


class ScopeGuardError(Exception):
    """자동운영 스코프 «밖» 광고그룹(또는 스코프 캠페인의 캠페인 레벨 액션)에 대한 엔진 실행
    시도 — D-NAO-244. 킬스위치(auto_operate OFF)와 구별한다: 킬스위치는 「이 캠페인 전체가
    꺼졌다」이고 이건 「캠페인은 켜져 있으나 이 그룹은 우리 몫이 아니다」라, 원인이 다르면
    타입도 달라야 사후 채굴이 둘을 안 섞는다."""


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
    # SS3(검색어 ROAS 레이어) — 검색어 제외. negative_keyword와 같은 restricted-keywords API를
    # 쓰지만 별도 액션·별도 executor로 분리한다(SS 전용 봉투: SHOPPING 명시 거부·일일캡). 자동
    # 승인원 배선 없음 — Jino 콘솔 Confirm(approval_source='console')만이 승인 경로. explore/rank
    # 스텝처럼 delegation에서도 영구 제외(delegable_types()가 SEARCH_TERM_EXCLUDE_TYPE를 뺀다).
    search_term_judge.SEARCH_TERM_EXCLUDE_TYPE: "exclude_search_term",
    # bid 계열은 R0 레지스트리(bid_step_types)에서 파생 — 새 UP/DOWN 타입이 레지스트리에
    # 등록되면 실행 매핑도 자동 인식된다(가드는 UP으로 인식하는데 여기 매핑 누락으로
    # ActionNotExecutableError가 나는 어긋남 차단, codex R0 리뷰 P2).
    **{t: "update_bid" for t in sorted(BID_UP_TYPES | BID_DOWN_TYPES)},
    "pause": "set_user_lock",
    "resume": "set_user_lock",
    "budget_up": "update_budget",
    "budget_down": "update_budget",
}

# D-NAO-47(codex[P2] 2026-07-17): **우리가 실제로 광고 API에 쓴 것**의 action 집합.
# 위 매핑의 값 **+ 라벨 세분화분**에서 파생한다 — 하드코딩하면 새 제안 유형이 배선될 때
# 조용히 어긋난다.
#
# ★왜 필요한가: naver_change_log에는 세 부류가 섞여 있다.
#   ① 우리 실집행        — update_bid / add_negative_keyword / set_user_lock / update_budget
#                          (+ 같은 경로의 세분화 라벨 budget_up_pacing / budget_down_pacing)
#   ② 외부 변경 **감지**  — external_bid_change / external_status_change (entity_sync가 기록.
#                          MOP·사람이 바꾼 걸 우리가 관측한 것이지 우리가 한 게 아니다)
#   ③ 우리 시스템 내부 설정 — optimizer_change / update_expert_delegation / flight_pacing
#                          (광고 API 쓰기 아님)
# 커맨드 센터 1층의 "우리 조작 N회"가 ②③을 섞어 세면 **정반대의 거짓말**이 된다:
# prod 실측(2026-07-17) change_log의 dry_run=False 행 15건은 **전부 ②**이고 우리 실집행은
# 0건이라, 필터 없이 세면 "우리 조작 15회"라고 표시된다. 0을 0이라고 말하는 게 그 화면의
# 존재 이유다(D-47-h).
#
# ★PACING_ACTIONS를 합치는 이유(D-NAO-102 ⑥): BP(예산 페이싱) 레인의 쓰기는 **제안유형도
#   실행 경로도 budget_up/budget_down → _execute_update_budget 그대로**이고, 달라지는 건
#   change_log에 남기는 라벨뿐이다(`_budget_log_action`). 그래서 이 집합을 매핑 값에서만
#   파생시키면 BP의 실집행이 ①인데도 ①로 안 세어진다 — actor=ours 조회와 커맨드 센터 1층
#   "우리 조작 N회"가 **실제로 광고를 바꿔놓고 0이라고 말한다**. 0을 0이라 말하려고 만든
#   필터가 정반대 방향의 같은 거짓말을 하는 것이라 D-47-h 위반은 동일하다.
#   (성공행만이 아니다 — 가드 거부·쓰기 실패 행도 같은 라벨을 달므로 `_execution_state`의
#    blocked/unknown 배지까지 통째로 사라진다.)
# ★새 라벨을 또 세분화한다면 여기 합집합에 추가하는 것이 계약이다. 라우터·프론트에서
#   각자 합집합을 만들면(퍼진 사본) 다음 소비자가 같은 구멍에 빠진다.
EXECUTION_ACTIONS: frozenset[str] = frozenset(_ACTION_BY_PROPOSAL_TYPE.values()) | frozenset(
    budget_pacing.PACING_ACTIONS
)

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
# 제외 쓰기 경로를 아는 캠페인 유형 (D-NAO-181 ③). 여기 없는 유형은 harness가 조기 거부한다.
# ★이건 «조기 판별»이지 최종 판정이 아니다 — 실제 경로 선택은 naver_sa_writer.add_exclusions가
#   라이브 adgroupType으로 한다(캠페인 축 ≠ 광고그룹 축, D-NAO-179).
_WRITABLE_EXCLUSION_CAMPAIGN_TYPES = frozenset({"WEB_SITE", "SHOPPING"})

WRITE_FAILURE_MARKER = "[실행 실패]"
# ★D-NAO-170(B-4): 「그룹입찰이 죽은 그룹에 그룹입찰을 쓰려 했다」 전용 마커.
# 일반 실패와 **분리해서 셀 수 있어야** 한다 — 이 실패는 버그가 아니라 «라우터의 입력 데이터가
# 낡았다»는 신호이고, 회차마다 반복되면 아래 write-back(수리 루프) 자체가 고장 난 것이다.
# 상시 신선도 표면이 없으면 그 고장은 조용히 늙는다(파이프 정체 교훈과 같은 모양) →
# 주간 지혜 감사가 이 마커의 건수를 센다.
LEVER_MISMATCH_MARKER = "[LEVER_MISMATCH]"

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
    {"add_negative_keyword", "update_bid", "set_user_lock", "update_budget", "exclude_search_term"}
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
    conds = [
        NaverProposal.id == proposal.id,
        NaverProposal.status == "approved",
        NaverProposal.executed_change_log_id.is_(None),
    ]
    # ★D-NAO-125 codex 2R[P1] — 소재 자동 실행은 **대상 단위**로 클레임한다.
    #   기존 클레임은 proposal.id 단위라, 두 레인(크론 + 수동 트리거)이 같은 소재에 각자
    #   제안을 만들면 서로 다른 id라서 둘 다 클레임에 성공하고 같은 소재에 PUT을 두 번 쏜다
    #   (레인 쪽 'executing 있나' 조회는 조회-생성 사이가 열려 있어 경쟁을 못 막는다 — 검사와
    #   상태 전이가 한 문장이 아니면 원자적이지 않다).
    #   같은 대상에 이미 executing 제안이 있으면 이 UPDATE가 0행을 바꾸고 클레임이 실패한다.
    #   판정과 상태 전이가 **단일 UPDATE 문** 안에 있어 SQLite의 쓰기 직렬화가 그대로 경쟁을
    #   막는다(현 prod=SQLite. ★PostgreSQL로 옮기면 READ COMMITTED에서는 서로 다른 행을
    #   갱신하는 두 트랜잭션이 모두 통과할 수 있어 행 잠금·유니크 제약이 따로 필요하다).
    if proposal.target_type == "ad" and is_auto_exec(proposal):
        other = aliased(NaverProposal)
        conds.append(~select(other.id).where(
            other.target_type == proposal.target_type,
            other.target_id == proposal.target_id,
            other.status == "executing",
            other.id != proposal.id,
        ).exists())
    claimed = db.query(NaverProposal).filter(*conds).update(
        {"status": "executing"}, synchronize_session=False
    )
    db.commit()
    if claimed != 1:
        raise AlreadyExecutedError(
            f"proposal_id={proposal.id} 클레임 실패 — 다른 실행자가 선점(동시 실행 차단)"
        )
    db.refresh(proposal)

    if proposal.approval_source is not None:
        from app.services.naver_ad import auto_operator as _auto_operator  # 지연 import(순환 회피, 7R과 동일)
        from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE  # BX2 탐색 승인원

        # ★D-NAO-125 codex[P1] — 소재 자동 실행 킬스위치도 **여기서** 재확인한다.
        #   레인의 `_ad_auto_exec` 판정은 제안을 approved로 커밋한 **뒤에** 실행으로 넘어가므로,
        #   그 사이에 크래시하거나(잔존 approved 행) 킬스위치를 내려도 잔존 행이 다음 실행자·
        #   재시도 경로를 타고 그대로 통과한다 = "되돌리는 스위치가 되돌리지 않는" 상태.
        #   캠페인 킬스위치(_auto_operate_now)와 같은 자리에서 같은 방식으로 막는다.
        #   ★사람 승인(approval_source NULL·console)은 영향 없음 — 이 블록 자체가 자동 승인원
        #     전용이고, 스위치는 '자동 발사'를 끄는 것이지 사람 손을 묶는 것이 아니다.
        if (
            proposal.target_type == "ad"
            and proposal.proposal_type in _auto_operator._AD_AUTO_EXEC_PROPOSAL_TYPES
            and is_auto_exec(proposal)  # ★codex 2R[P1]: 사람 console 승인은 스위치와 무관
            and not _auto_operator.AD_BID_ROUTING_ENABLED
        ):
            proposal.status = "approved"  # 클레임 원복(미실행 정직 상태)
            db.commit()
            log.warning(
                "naver_execution_harness: 소재 자동 실행 킬스위치 OFF(writer 직전 최종 확인) — "
                "proposal_id=%s ad=%s 실행 거부(쓰기·change_log 없음, D-NAO-125)",
                proposal.id, proposal.target_id,
            )
            raise KillSwitchEngagedError(
                f"proposal_id={proposal.id} target={proposal.target_id} — "
                "AD_BID_ROUTING_ENABLED=False(소재 자동 실행 킬스위치 OFF)"
            )

        if proposal.approval_source in (
            _auto_operator.APPROVAL_SOURCE_DAILY, _auto_operator.APPROVAL_SOURCE_HOURLY,
            _auto_operator.APPROVAL_SOURCE_PROBE,  # D-NAO-58 CD2: 탐침도 동일 킬스위치 가드(우회 금지)
            _auto_operator.APPROVAL_SOURCE_REVERT,  # D-NAO-58 CD3: 되돌림도 킬스위치 통과(우회 금지)
            APPROVAL_SOURCE_EXPLORE,  # BX2 D-NAO-70: 탐색 자동 실쓰기도 동일 킬스위치 가드(우회 금지, probe_op 관례)
            APPROVAL_SOURCE_COLD,  # CS: 콜드 첫 입찰도 동일 킬스위치 가드(우회 금지 — 자동 실쓰기 레인)
            search_term_judge.APPROVAL_SOURCE_SS_EXCLUDE,  # SS3-A: 미래 자동 활성화 대비 사전 등록(현재 미배선)
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


def is_auto_exec(proposal: NaverProposal) -> bool:
    """이 제안이 **사람 없이** 실행되는가(D-NAO-125 codex 2R[P1] — 단일 판별 소스).

    사람은 둘뿐이다: 콘솔 Confirm(approval_source='console')과 아직 승인원이 없는 행(NULL).
    그 밖(auto_op/auto_op_hr/probe/revert/explore/cold/delegation…)은 전부 무인 경로다.
    ★킬스위치·자동 전용 상한이 **같은 판별**을 쓰게 고정한다 — 초판은 킬스위치 쪽에만
    `approval_source is not None`을 써서 콘솔 승인(=사람)까지 막았다(codex 재현: 사고 대응
    중 자동화를 끄면 사람도 직접 못 내린다).
    """
    return proposal.approval_source not in (None, "console")


def _ad_bid_from_payload(raw: str | None) -> int | None:
    """change_log의 before/after_value(JSON) → 소재 입찰가. ad grain은 get_ad 원본(adAttr 중첩)."""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    bid, _ = naver_sa_fetcher._parse_ad_attr(payload.get("adAttr"))
    if bid is not None:
        return bid
    v = payload.get("bidAmt")
    try:
        return int(v) if v is not None and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _our_last_known_ad_states(db: Session, ad_id: str) -> set[tuple[str, int | None]]:
    """우리가 아는 그 소재의 (editTm, bidAmt) 쌍 집합 — ①마지막 관측 ②우리 성공 쓰기들.

    live 상태가 여기 없으면 **그 사이 외부가 만진 것**이다(D-NAO-130).
    ★editTm만 비교하면 같은 초에 우리 쓰기와 외부 변경이 겹칠 때 외부를 우리 것으로 오인한다
    (codex 5R[P2]). 값까지 함께 봐야 그 충돌이 갈린다.
    """
    known: set[tuple[str, int | None]] = set()
    row = (
        db.query(NaverAdgroupProduct.ad_edit_tm, NaverAdgroupProduct.ad_bid_amt)
        .filter(NaverAdgroupProduct.ad_id == ad_id,
                NaverAdgroupProduct.ad_edit_tm.isnot(None))
        .first()
    )
    if row and row[0]:
        known.add((str(row[0]), row[1]))
    for (raw,) in (
        db.query(NaverChangeLog.after_value)
        .filter(
            NaverChangeLog.entity_type == "ad",
            NaverChangeLog.entity_id == ad_id,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .limit(_EDIT_TM_SCAN)
        .all()
    ):
        try:
            payload = json.loads(raw or "")
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("editTm"):
            bid, _ = naver_sa_fetcher._parse_ad_attr(payload.get("adAttr"))
            known.add((str(payload["editTm"]), bid))
    return known


# 외부변경 확인 판정 — 자동 상향은 CONFIRMED_OURS일 때만 나간다(fail-closed).
_EXT_OURS = "ours"          # 우리가 아는 상태 그대로 = 외부 손 없음
_EXT_TOUCHED = "external"   # 외부가 만졌다 → 기준점 재설정 후 진행
_EXT_UNKNOWN = "unknown"    # 판정 불가(관측 부재·조회 실패) → 자동 상향 보류


def _classify_external_touch(
    db: Session, ad_id: str, live_edit_tm: str | None, live_bid: int | None,
) -> str:
    """live 상태가 우리가 아는 것인가 / 외부가 만졌나 / 알 수 없나(D-NAO-130, codex 5R[P1]).

    ★"모르면 진행"이 아니라 **"모르면 보류"**다. 초판은 known이 비면 False("외부 아님")를
    반환해 자동 상향을 계속했는데, 그건 관측이 없는 신규 소재에서 정확히 반대로 작동한다 —
    대행사가 400으로 내려놔도 판정이 '외부 아님'이라 그대로 460으로 올린다. 확인이 성립하지
    않으면 올리지 않는 것이 이 장치의 존재 이유다.
    """
    if not live_edit_tm:
        return _EXT_UNKNOWN
    known = _our_last_known_ad_states(db, ad_id)
    if not known:
        return _EXT_UNKNOWN  # 기준선이 없다 — 이번 회차는 보류하고 관측만 세운다
    return _EXT_OURS if (str(live_edit_tm), live_bid) in known else _EXT_TOUCHED


def _seed_edit_tm_baseline(db: Session, ad_id: str, live_edit_tm: str, live_bid: int | None) -> None:
    """관측 기준선이 없는 소재에 현재 상태를 세워둔다 — 다음 회차부터 판정이 성립한다.

    (일 1회 sync가 채우기 전이라도 이 자리에서 세워 자동 상향이 하루 종일 묶이지 않게 한다.)
    """
    row = (
        db.query(NaverAdgroupProduct)
        .filter(NaverAdgroupProduct.ad_id == ad_id,
                NaverAdgroupProduct.ad_edit_tm.is_(None))
        .first()
    )
    if row is None:
        return
    row.ad_edit_tm = str(live_edit_tm)
    if live_bid is not None:
        row.ad_bid_amt = live_bid
    db.commit()


def _record_inline_external_touch(
    db: Session, proposal: NaverProposal, live_edit_tm: str, live_bid: int, now: datetime,
) -> None:
    """쓰기 직전에 발견한 외부 변경을 관측 테이블에 즉시 남긴다(소실 방지, D-NAO-130).

    D-NAO-127의 일 1회 탐지는 우리가 먼저 쓰면 editTm이 덮여 이 사건을 영영 못 본다.
    여기서 남겨야 다음 판정의 기준점이 산다. 멱등 키는 그쪽과 동일(entity_id, op_type, occurred_at).
    """
    from app.services.naver_ad import ad_external_change

    occurred_at = ad_external_change.parse_edit_tm(live_edit_tm)
    # ★codex 5R[P1-3]: occurred_at이 NULL(editTm 형식 변화)일 때 모든 NULL 사건을 하나로
    #   접으면 최신 외부 하향이 기록되지 않아 기준점이 옛 고가로 복귀한다.
    #   ad_external_change._dedup_key와 같은 규율로 값까지 키에 넣는다.
    dup_q = db.query(NaverAgencyOp.id).filter(
        NaverAgencyOp.entity_type == "ad",
        NaverAgencyOp.entity_id == proposal.target_id,
        NaverAgencyOp.op_type == "bid_change",
    )
    if occurred_at is not None:
        dup_q = dup_q.filter(NaverAgencyOp.occurred_at == occurred_at)
    else:
        dup_q = dup_q.filter(NaverAgencyOp.occurred_at.is_(None),
                             NaverAgencyOp.after_value == str(live_bid))
    dup = dup_q.first()
    if dup is not None:
        return
    db.add(NaverAgencyOp(
        op_date=now.date(), detected_at=now, entity_type="ad",
        entity_id=proposal.target_id, campaign_id=proposal.campaign_id or "",
        optimizer="ours", op_type="bid_change",
        before_value=None, after_value=str(live_bid), magnitude=None,
        is_exception=True, occurred_at=occurred_at,
    ))
    db.commit()
    log.warning(
        "소재 외부 변경을 쓰기 직전에 감지(D-NAO-130): ad=%s 현재 %s원(editTm=%s) — "
        "자동 상향 기준점을 이 값으로 재설정한다",
        proposal.target_id, live_bid, live_edit_tm,
    )


def last_change_was_auto_up(db: Session, entity_type: str, entity_id: str) -> bool:
    """이 대상의 **가장 최근 성공 쓰기**가 우리 자동 상향이었는가(D-NAO-130).

    되먹임 루프의 교정 단계(올렸는데 손실 → 되돌린다)가 쿨다운에 막히지 않게 하는 판정.
    사람이 올린 것은 포함하지 않는다 — 사람 판단을 자동이 2시간 안에 뒤집게 만들 이유가 없다.
    """
    row = (
        db.query(NaverProposal.proposal_type, NaverProposal.approval_source)
        .join(NaverChangeLog, NaverChangeLog.proposal_id == NaverProposal.id)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )
    if row is None:
        return False
    ptype, src = row
    return ptype in BID_UP_TYPES and src is not None and src != "console"


_EDIT_TM_SCAN = 20  # 우리 쓰기 editTm 스캔 폭


_HUMAN_ANCHOR_SCAN = 20  # 사람 기준점 후보 스캔 폭(최신 파싱 가능한 행을 찾는다)


def auto_up_base_bid(db: Session, entity_type: str, entity_id: str, now: datetime) -> int | None:
    """자동 상향 누적 상한의 **기준점**(anchor) — 사람이 마지막으로 정한 입찰가.

    ★왜 필요한가(D-NAO-129 codex 적대[P1]): 상향의 경제적 브레이크인 "BEP 미달 증액 금지"는
    소재가 아니라 **부모 광고그룹의 30일 집계**로 판정한다. 같은 그룹에 매출을 만드는 소재가
    하나라도 있으면 전환 0인 소재도 그 실적을 빌려 계속 통과하고, 그러면 남는 절대 상한은
    네이버 API 최대값(100,000원)뿐이다.

    ★기준점을 **시간창으로 잡으면 안 된다**(codex 적대 2R[P1] — 초판의 결함): "최근 7일" 같은
    이동창은 오래된 기준점을 자동으로 버려서, 1주차에 800→1,600으로 막힌 뒤 창이 만료되면
    1,600이 새 기준이 되어 3,200 → 6,400 → … 계단식으로 결국 100,000에 닿는다. 상한이 상승을
    **느리게 만들 뿐 멈추지 못한다**. "7일 동안 자동 상향이 없었다"는 "사람이 승인했다"와
    동치가 아니다.

    그래서 기준점은 **사람이 마지막으로 만든 값**이다:
      ① 사람 쓰기(approval_source NULL·console)의 after 입찰가 — 가장 최근 것.
      ② 그런 이력이 없으면, 사람 개입 이후 **최초 자동 상향의 before**(= 자동화가 출발한 값).
      ③ 둘 다 없으면 None → 상한 미적용(이번이 첫 스텝이고, 그 스텝의 before가 다음 기준점이 된다).
    사람이 올리든 내리든 그 값이 새 기준점이 되므로, 상한은 언제나 "사람이 정한 값의 N배"다.

    ★자동 상향 이력은 이번에 개방한 타입(_AD_UP_OPEN_TYPES)만 센다 — 탐색(explore)·콜드(cold)는
    각자의 경제성 상한을 가진 별도 레인이라 이 상한에 묶지 않는다(그 레인들의 의미를 바꾸지
    않는다는 스코프 원칙, codex 적대 2R[P2]).
    """
    human_rows = (
        db.query(NaverChangeLog)
        .outerjoin(NaverProposal, NaverProposal.id == NaverChangeLog.proposal_id)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.action == "update_bid",
            or_(
                NaverChangeLog.proposal_id.is_(None),
                NaverProposal.approval_source.is_(None),
                NaverProposal.approval_source == "console",
            ),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .limit(_HUMAN_ANCHOR_SCAN)
        .all()
    )
    # ★codex 적대 3R[P2]: "최신 1건"만 보면 그 행의 payload가 깨졌을 때 유효한 이전 사람
    #   기준점까지 버리고 상한이 통째로 풀린다 — **최신 파싱 가능한** 행을 찾는다.
    human = human_rows[0] if human_rows else None

    # ★codex 적대 3R[P1] — **외부(대행사) 변경도 기준점을 재설정한다.**
    #   초판 정의는 "우리 앱에서 사람이 정한 값"이었다. 대행사가 네이버 콘솔에서 2,000→400으로
    #   내려도 우리 change_log엔 안 남으므로 기준점은 2,000에 머물고, 자동화가 400→4,000까지
    #   **10배로 되돌린다**. 오늘 15:39에 대행사가 한 일이 정확히 그 하향이다(D-NAO-126).
    #   D-NAO-127이 그 사건을 naver_agency_op(ad grain, bid_change)에 남기므로 그것을 사람
    #   개입과 **같은 급의 기준점 재설정 사건**으로 쓴다.
    #   ★알려진 지연: 소재 외부변경 탐지는 하루 1회(07:45) 돈다. 그 사이의 외부 하향은 다음
    #     아침까지 기준점에 반영되지 않는다 — 그 구간은 ±15% 클램프·BEP·일일 3회가 막는다.
    external = (
        db.query(NaverAgencyOp)
        .filter(
            NaverAgencyOp.entity_type == entity_type,
            NaverAgencyOp.entity_id == entity_id,
            NaverAgencyOp.op_type == "bid_change",
            NaverAgencyOp.after_value.isnot(None),
        )
        # ★codex 5R[P1-3]: occurred_at NULLS LAST로 정렬하면 **최신** NULL 사건이 과거의
        #   non-NULL 사건에 밀려 기준점이 옛 고가로 복귀한다. 항상 존재하는 detected_at으로
        #   최신을 고르고, 시각 비교에는 occurred_at이 없으면 detected_at을 쓴다.
        .order_by(NaverAgencyOp.detected_at.desc(), NaverAgencyOp.id.desc())
        .first()
    )
    ext_at = None
    if external is not None:
        ext_at = external.occurred_at or external.detected_at
    if external is not None and (
        human is None or ext_at is None or human.changed_at is None or ext_at > human.changed_at
    ):
        try:
            return int(external.after_value)  # 대행사가 세운 값이 새 기준점이다
        except (TypeError, ValueError):
            pass

    for row in human_rows:
        anchor = _ad_bid_from_payload(row.after_value)
        if anchor is not None:
            return anchor

    q = (
        db.query(NaverChangeLog)
        .join(NaverProposal, NaverProposal.id == NaverChangeLog.proposal_id)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverProposal.proposal_type.in_(_AD_UP_OPEN_TYPES),
            NaverProposal.approval_source.isnot(None),
            NaverProposal.approval_source != "console",
        )
    )
    if human is not None:
        q = q.filter(NaverChangeLog.changed_at > human.changed_at)
    first_auto = q.order_by(NaverChangeLog.changed_at.asc()).first()
    return _ad_bid_from_payload(first_auto.before_value) if first_auto is not None else None


def count_auto_bid_down_today(
    db: Session, entity_type: str, entity_id: str, now: datetime,
) -> int:
    """오늘(KST) 이 대상에 **무인 경로로 성공한 하향** 건수 (D-NAO-125 codex 2R[P1]).

    ★compute_change_cadence의 changes_today_count를 그대로 쓰면 안 된다: 그것은 action·방향·
    승인원을 구분하지 않는 "그 대상의 오늘 모든 변경" 수다. 그 값에 자동 하향 상한을 걸면
    사람이 오늘 손으로 3번 만진 소재는 **정작 손실 하향이 하루 종일 막힌다** — 열려던 행동을
    정확히 막는 셈이다.
    그래서 change_log를 제안(NaverProposal)과 조인해 (하향 타입 ∧ 무인 승인원)만 센다.
    성공 판별은 이 코드베이스 규약대로 dry_run=False ∧ after_value 존재.
    """
    today_start = datetime.combine(now.date(), datetime.min.time())
    return (
        db.query(sqlfunc.count(NaverChangeLog.id))
        .join(NaverProposal, NaverProposal.id == NaverChangeLog.proposal_id)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= today_start,
            NaverProposal.proposal_type.in_(BID_DOWN_TYPES),
            NaverProposal.approval_source.isnot(None),
            NaverProposal.approval_source != "console",
        )
        .scalar()
    ) or 0


def compute_change_cadence(
    db: Session, entity_type: str, entity_id: str, now: datetime,
) -> tuple[datetime | None, int]:
    """(last_change_at, changes_today_count) — 쿨다운·일일상한의 단일 원료(IU-R R2 공용 helper).

    실제 쓰기가 확정된 행만 센다(dry_run=False ∧ after_value 존재) — dry-run·실패·가드거부 행은
    네이버 상태를 바꾸지 않았으므로 제외(codex[P2], _build_guardrail_context의 3개 중복 블록에서
    이 함수로 단일화). changes_today = KST 오늘 0시 이후 변경 수. R2 시간당 레인 rank-step
    prefilter(estimate 호출 절약)와 guardrail 컨텍스트가 **동일 쿼리·동일 판별**을 공유해
    "prefilter는 통과했는데 guardrail은 쿨다운으로 막는" 어긋남을 원천 차단한다(중복 금지)."""
    change_rows = (
        db.query(NaverChangeLog.changed_at)
        .filter(
            NaverChangeLog.entity_type == entity_type,
            NaverChangeLog.entity_id == entity_id,
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
        )
        .all()
    )
    if not change_rows:
        return None, 0
    last_change_at = max(r[0] for r in change_rows)
    today_start = datetime.combine(now.date(), datetime.min.time())
    changes_today = sum(1 for r in change_rows if r[0] >= today_start)
    return last_change_at, changes_today


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
        "last_change_at": None, "changes_today_count": 0, "campaign_type": None,
        # ★D-NAO-172 P1: 봉투 파라미터(DB KV → 코드 상수 폴백). guardrail_gate는 DB를 모르므로
        #   유일한 전달 통로가 이 컨텍스트다. 이 키가 없으면 게이트가 코드 상수로 떨어지므로
        #   컨텍스트를 안 채우는 기존 호출부·테스트는 종전 그대로 돈다(fail-to-current).
        "guardrail_params": guardrail_params.get_params(db),
        # D-NAO-125 codex[P1]: 사람 승인 없이 쏘는 제안인가(is_auto_exec 단일 판별).
        # guardrail_gate가 자동 하향에만 별도 일일 상한을 걸 때 쓴다 — DL3 면제(하향 무제한)는
        # 사람 눈을 전제로 설계된 규칙이라 무인 경로에 그대로 적용하면 바닥까지 파고든다.
        "auto_exec": is_auto_exec(proposal),
        # 자동 하향 상한의 **원료**(codex 2R[P1]). 소재 grain에서만 채운다 — 이 상한은
        # D-NAO-125가 새로 여는 경로에만 걸고, 이미 살아 있는 그룹·키워드 자동 하향
        # (RL3 손실 고삐 등)의 종전 동작은 건드리지 않는다. None = 상한 미적용.
        "auto_bid_down_today": None,
        # D-NAO-121: 출시창 순위 하한 — 소재(target_type='ad')와 그룹(target_type='adgroup',
        # 그룹 입찰을 쓰는 출시창 소재가 있을 때) 제안에서 채운다. 캠페인 단위는 항상 None.
        "launch_floor_bid": None, "launch_target_rank": None,
    }
    # VT4 codex 1R P1-1: guardrail_gate가 campaign_type 인지형 입찰 하한(SHOPPING 50·그 외 70)을
    # 적용하려면 context에 campaign_type이 실려야 한다 — proposal.campaign_id의 campaign 엔티티
    # 행에서 조회(부재/조회 실패 시 None → guardrail_gate가 보수 70 하한 유지, fail-closed). 세
    # 실쓰기 경로(adgroup/ad·keyword·campaign)가 모두 이 함수를 거쳐 아래에서 분기 return 하므로,
    # 분기 전 이 한 곳에서 채우면 세 경로 전부 커버된다(중복 조회·경로별 누락 방지).
    try:
        camp_row = (
            db.query(NaverEntity.campaign_type)
            .filter(
                NaverEntity.entity_type == "campaign",
                NaverEntity.entity_id == proposal.campaign_id,
            )
            .first()
        )
        if camp_row is not None:
            context["campaign_type"] = camp_row[0] or None
    except Exception as e:  # noqa: BLE001 — 조회 실패는 None 유지(보수 70 하한, fail-closed)
        log.warning(
            "naver_execution_harness: guardrail context campaign_type 조회 실패(보수 70 하한 유지) "
            "campaign_id=%s: %s", proposal.campaign_id, e,
        )
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
        if proposal.proposal_type in BID_UP_TYPES:
            as_of = now.date() - timedelta(days=1)  # naver_ad_daily는 D-1까지만 확정
            window_from = as_of - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS - 1)
            agg = account_diagnosis.adgroup_window_agg(db, proposal.target_id, window_from, as_of)
            if agg["cost"] > 0:
                correction = compute_correction_factor(db, as_of)
                roas_naver = agg["conv_amt"] / agg["cost"]
                # ★★D-NAO-234 ⓐ(계약 §5-Q4): 증액 가드는 «얼마나 올리나»(크기)가 아니라
                # «올려도 되나»(통과/차단) 판정이다 ⇒ **상한**. 하한을 쓰면 하한이 내려갈수록
                # 차단이 늘어 브레이크가 커진다 — D-NAO-231이 「선정=상한·크기=하한」으로 갈랐을 때
                # «게이트»라는 셋째 층에 배정이 없었고(ref 94 §6), 그 구멍으로 액셀 221→195건·
                # 대칭 3.005→3.405:1이 새고 있었다. 하한을 0.827로 내리는 배포에서 이 재배정을
                # 같이 하지 않으면 계약 §4 금지선 2(하한 단독 배포 금지) 위반이다.
                context["roas_corrected"] = roas_naver * float(correction["factor_high"])
                context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0
            context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
            context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
                db, proposal.campaign_id, now.date(),
            )

        # D-NAO-121 그룹 경로(codex 리뷰 P1, 2026-07-29): useGroupBidAmt=true 소재의 실효
        # 레버는 그룹 입찰이고 실행기도 adgroup bid_down을 낸다 — 소재 분기에만 하한이
        # 있으면 그 경로로 출시창 소재가 하한 밑으로 밀린다. 그룹 하한을 집계해 싣는다.
        try:
            gfloor = launch_rank_floor.group_floor_for(
                db, adgroup_id=proposal.target_id, today=now.date(),
            )
            context["launch_floor_bid"] = gfloor["floor_bid"]
            context["launch_target_rank"] = gfloor["target_rank"]
        except Exception as e:  # noqa: BLE001 — 조회 실패는 하한 없음 유지(종전 동작)
            log.warning(
                "naver_execution_harness: guardrail context group_floor_for 조회 실패 "
                "(하한 없음 유지) adgroup_id=%s: %s", proposal.target_id, e,
            )

        context["last_change_at"], context["changes_today_count"] = compute_change_cadence(
            db, proposal.target_type, proposal.target_id, now,
        )
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
        # D-NAO-130: 외부 변경 확인용 editTm은 **best-effort**로 따로 읽는다(실패해도 본 판정
        # 무해 — 그 경우 기준점 재설정이 이번 회차에만 생략된다). current_bid 계약은 불변.
        live_edit_tm = None
        try:
            live_edit_tm = naver_sa_writer.get_ad(proposal.target_id).get("editTm")
        except Exception as e:  # noqa: BLE001 — 관측 부가정보
            log.debug("naver_execution_harness: editTm 재조회 실패(무시) target_id=%s: %s",
                      proposal.target_id, e)

        # D-NAO-121: 출시창 순위 하한 — target_type='ad'(소재 단위) 제안에서만 계산한다.
        # adgroup_id가 없으면(구 제안·격상 경로) 판정 불가라 floor_for를 호출하지 않고 None
        # 유지(fail-closed는 아님 — 이 하한은 "있으면 추가 보호", 없어도 종전 동작).
        # guardrail_gate는 bid_down 제안에서만 launch_floor_bid를 소비하므로, up 제안에서
        # 채워져 있어도 무해하다(계산 자체는 방향 무관하게 항상 시도해 반환값을 그대로 싣는다).
        if proposal.adgroup_id:
            try:
                floor = launch_rank_floor.floor_for(
                    db, ad_id=proposal.target_id, adgroup_id=proposal.adgroup_id, today=now.date(),
                )
                context["launch_floor_bid"] = floor["floor_bid"]
                context["launch_target_rank"] = floor["target_rank"]
            except Exception as e:  # noqa: BLE001 — 조회 실패는 fail-closed(하한 없음 유지)
                log.warning(
                    "naver_execution_harness: guardrail context launch_rank_floor 조회 실패 "
                    "(하한 없음 유지) target_id=%s adgroup_id=%s: %s",
                    proposal.target_id, proposal.adgroup_id, e,
                )

        # 증액(bid_up/growth_bid_up)만 up-only 가드 원료를 채운다 — 소재 단위 실적은
        # naver_ad_daily에 없어(그레인=adgroup/keyword) 부모 광고그룹(proposal.adgroup_id) 창
        # agg로 BEP/스톱로스/일예산을 근사한다(소재-레벨 daily 부재 시 최선 근사 — down은 이
        # 검사가 면제라 여전히 None, keyword/adgroup up 브랜치와 동형 재사용). adgroup_id가
        # 없으면 roas_corrected가 None으로 남아 executor가 fail-closed로 막는다(아래 S3 가드).
        if proposal.proposal_type in BID_UP_TYPES and proposal.adgroup_id:
            as_of = now.date() - timedelta(days=1)  # naver_ad_daily는 D-1까지만 확정
            window_from = as_of - timedelta(days=_GUARDRAIL_LOOKBACK_DAYS - 1)
            agg = account_diagnosis.adgroup_window_agg(db, proposal.adgroup_id, window_from, as_of)
            if agg["cost"] > 0:
                correction = compute_correction_factor(db, as_of)
                roas_naver = agg["conv_amt"] / agg["cost"]
                # ★★D-NAO-234 ⓐ(계약 §5-Q4): 증액 가드는 «얼마나 올리나»(크기)가 아니라
                # «올려도 되나»(통과/차단) 판정이다 ⇒ **상한**. 하한을 쓰면 하한이 내려갈수록
                # 차단이 늘어 브레이크가 커진다 — D-NAO-231이 「선정=상한·크기=하한」으로 갈랐을 때
                # «게이트»라는 셋째 층에 배정이 없었고(ref 94 §6), 그 구멍으로 액셀 221→195건·
                # 대칭 3.005→3.405:1이 새고 있었다. 하한을 0.827로 내리는 배포에서 이 재배정을
                # 같이 하지 않으면 계약 §4 금지선 2(하한 단독 배포 금지) 위반이다.
                context["roas_corrected"] = roas_naver * float(correction["factor_high"])
                context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0
            context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
            context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
                db, proposal.campaign_id, now.date(),
            )

        context["last_change_at"], context["changes_today_count"] = compute_change_cadence(
            db, proposal.target_type, proposal.target_id, now,  # entity_type='ad', entity_id=nccAdId
        )
        # D-NAO-125 codex 2R[P1]: 자동 하향 상한은 **자동 하향만** 세야 한다(위 주석 참조).
        context["auto_bid_down_today"] = count_auto_bid_down_today(
            db, proposal.target_type, proposal.target_id, now,
        )
        # D-NAO-130: 직전 변경이 우리 자동 상향이면 하향 쿨다운 면제(되먹임 루프의 교정 단계).
        context["last_change_was_auto_up"] = last_change_was_auto_up(
            db, proposal.target_type, proposal.target_id,
        )
        # D-NAO-129: 자동 상향 누적 배수 상한의 기준점(auto_up_base_bid 참조).
        context["auto_up_base_bid"] = auto_up_base_bid(
            db, proposal.target_type, proposal.target_id, now,
        )
        # ★D-NAO-130(codex 적대 4R·5R[P1]) — **쓰기 직전 외부 변경 확인(fail-closed)**.
        #   D-NAO-127 소재 외부변경 탐지는 하루 1회(07:45)인데 이 레인은 매시간 쓴다. 우리가
        #   먼저 쓰면 editTm이 우리 것으로 바뀌어 **외부 사건이 영구 소실**된다 — 그러면 기준점이
        #   옛 값에 머문 채 자동화가 대행사의 하향을 되돌려 올린다(재현: 07:45 기준 2,000 →
        #   10:00 대행사 400 → 11:00 우리 460 → 다음날 탐지는 "우리 쓰기"로 분류 → 기준점 2,000).
        #   그래서 **지금 이 자리에서** 확인한다.
        #   ★핵심(codex 5R[P1]): 확인이 **성립하지 않으면 자동 상향을 하지 않는다**. 초판은
        #     best-effort라 조회 실패·관측 부재에서 조용히 통과했는데, 그러면 이 장치는 선행조건이
        #     아니라 장식이다. 실패·불명은 전부 보류(_EXT_UNKNOWN)로 모은다.
        context["external_check"] = _EXT_UNKNOWN
        try:
            verdict = _classify_external_touch(
                db, proposal.target_id, live_edit_tm, context["current_bid"],
            )
            if verdict == _EXT_TOUCHED:
                # 외부가 만졌다 — 기준점을 현재 실측값으로 재설정하고 사건을 남긴다(소실 방지).
                _record_inline_external_touch(
                    db, proposal, live_edit_tm, context["current_bid"], now,
                )
                context["auto_up_base_bid"] = context["current_bid"]
            elif verdict == _EXT_UNKNOWN and live_edit_tm and context["current_bid"] is not None:
                # 기준선이 없다 — 이번 회차는 보류하고 다음 판정을 위해 관측만 세운다.
                _seed_edit_tm_baseline(db, proposal.target_id, live_edit_tm, context["current_bid"])
            context["external_check"] = verdict
        except Exception as e:  # noqa: BLE001 — 실패는 곧 '확인 불가' = 자동 상향 보류
            log.warning(
                "naver_execution_harness: 쓰기 직전 외부변경 확인 실패 — 자동 상향 보류"
                "(target_id=%s): %s", proposal.target_id, e,
            )
            context["external_check"] = _EXT_UNKNOWN
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
        # ★★D-NAO-234 ⓐ — 위 두 분기와 같은 이유: 증액 가드는 «통과/차단»이므로 **상한**.
        context["roas_corrected"] = roas_naver * float(correction["factor_high"])
        context["unconverted_spend"] = agg["cost"] if agg["conv_amt"] == 0 else 0

    context["target_roas"] = _resolve_target_roas_float(db, proposal.campaign_id)
    context["cost_today"], context["daily_budget"] = _latest_hourly_snapshot_fields(
        db, proposal.campaign_id, now.date(),
    )

    # codex[P2]: dry-run·실패(outcome='failed', _guard_failure 포함) 행은 네이버 상태를
    # 바꾸지 않았다 — 그런데도 쿨다운·일일상한에 포함시키면 실제로는 아무 일도 안 일어났는데
    # 다음 실행이 차단된다. compute_change_cadence가 실제 쓰기 확정 행(dry_run=False ∧
    # after_value 존재)만 세는 단일 판별을 담당(IU-R R2 공용 helper — prefilter와 동일 쿼리).
    context["last_change_at"], context["changes_today_count"] = compute_change_cadence(
        db, proposal.target_type, proposal.target_id, now,
    )

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


def _count_search_term_excludes_today(db: Session, now: datetime) -> int:
    """오늘(KST) 성공한 exclude_search_term 실쓰기 수 — account-wide 일일 캡(§1 4) 카운트.
    실제 쓰기 확정 행만(dry_run=False ∧ after_value 존재) 센다(_guard_failure의 [실행 불가]·
    실패 행은 네이버 상태를 안 바꿨으므로 제외 — compute_change_cadence의 판별과 동일 규율).
    changed_at은 executor가 now(kst_now, KST naive)로 심으므로 KST 자정 경계로 비교한다."""
    today_start = datetime.combine(now.date(), datetime.min.time())
    return (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.action == "exclude_search_term",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= today_start,
        )
        .count()
    )


def _search_term_conversions_in_window(
    db: Session, adgroup_id: str, search_term: str, now: datetime,
) -> tuple[int, int]:
    """실행 시점 전환 재검증(GATE ⑥, PLAN §3 SS3-A) — rolling 창 내 (adgroup_id, 검색어)
    purchase 전환수 합. SS2 판정 창을 그대로 재사용해 판정↔실행 사이의 신선도 갭을 메운다.
    source를 가르지 않고 그룹+검색어로 합산한다(파워링크 source는 전환이 항상 0이라 합산해도
    무해, 보수적으로 그룹 내 전환을 전부 본다).

    ★D-NAO-265 — 창을 **모듈 상수가 아니라 `_ss_params(db)`에서** 받는다. 이전엔
    `search_term_judge._SS_WINDOW_DAYS`를 직접 읽었는데, 그 상수가 SPECS로 승격되면 판정은
    DB값(예: 7일) 창으로 돌고 이 재검증만 코드값(14일) 창으로 돌아 **재사용의 목적이 정확히
    깨진다** — D-NAO-262가 바로 이 분산 때문에 승격을 보류했던 자리다. 같은 출처를 보는 것이
    「같은 창」의 유일한 보존 방법이다.

    ★반환이 `(전환수, 창일수)` 튜플인 이유: 호출부의 거부 사유 문장이 창 길이를 말하는데,
    거기서 상수를 다시 읽으면 **잰 창과 말하는 창이 갈릴 수 있다.** 방금 실제로 잰 값을 그대로
    돌려줘 사유가 «방금 돈 게이트»를 설명하게 한다(교훈 #362 — 닿는 층)."""
    _ss_min_click, ss_window_days = search_term_judge._ss_params(db)
    window_from = now.date() - timedelta(days=ss_window_days - 1)
    total = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0))
        .filter(
            NaverSearchTermDaily.adgroup_id == adgroup_id,
            NaverSearchTermDaily.search_term == search_term,
            NaverSearchTermDaily.ad_date >= window_from,
            NaverSearchTermDaily.ad_date <= now.date(),
        )
        .scalar()
    )
    return int(total or 0), ss_window_days


def _execute_search_term_exclude(db: Session, proposal: NaverProposal, now: datetime) -> NaverChangeLog:
    """검색어 제외 실쓰기 1건 (SS3-A, PLAN §3). naver_sa_writer.add_restricted_keywords로
    파워링크(WEB_SITE) 광고그룹에 제외키워드를 등록한다. _execute_add_negative_keyword와 동일
    쓰기 손(restricted-keywords)이지만 **SS 전용 봉투**를 추가한다:
      · SHOPPING 명시 거부(§실측-0): ncc restricted-keywords API가 SHOPPING 그룹 쓰기 불가
        (HTTP 400 code 3728 실측). writer도 WEB_SITE만 허용(fail-closed 최종 관문)이지만, 여기서
        엔티티 campaign_type='SHOPPING'을 먼저 걸러 무의미한 API 왕복·오해석을 차단(이중 방벽).
      · 실행 시점 전환 재검증(GATE ⑥, codex[P1] stale): 판정(SS2)↔실행(Confirm) 사이에 새 전환이
        붙을 수 있어, 쓰기 직전 rolling 창(SS2와 동일 창)으로 (adgroup_id, 검색어) purchase 전환을
        재조회해 ≥1이면 §1-1 전환 보호 게이트로 거부한다(fail-closed, 살아있는 증거는 제외 불가).
      · 일일 캡(§1 4) 3중 방어(codex[P1/P2] TOCTOU): 커밋된 change_log만 세는 검사는 동시 Confirm
        2건이 둘 다 통과한 뒤 각자 쓰기를 가할 수 있는 원자성 공백이 있다. 그래서
        ①**사전 hold**(쓰기 전 커밋 count ≥ 캡이면 claim 없이 조기 종료) +
        ②**캡 원자 예약**(claim 커밋 후 "커밋된 성공 + 현재 executing 제외 제안 수(자기 포함)"가
        캡 초과면 쓰기 전 중단 — executing 예약을 함께 세어 동시 2건이 서로를 보고 양쪽 다
        중단하는 과보수 fail-closed로 캡 관통을 원천 차단, claim 해제·failed·사람 재승인 재시도) +
        ③**쓰기 후 재카운트 롤백**(최후 방벽 — 쓰기 성공 직후 재카운트가 캡 도달이면 방금 등록한
        제외키워드를 delete_restricted_keywords로 즉시 원복하고 failed 종결).
        제외는 되돌림 비용 있는 비대칭 액션이라 runaway 방지(§1 4).
    재조회 검증(등록 후 GET)·change_log 전건·킬스위치·클레임은 add_restricted_keywords writer와
    _claim_executing이 그대로 담당(_execute_add_negative_keyword와 동형).

    ★자동 발사 없음(PLAN §3): 이 스프린트에서 실쓰기 경로는 Jino 콘솔 Confirm
    (approval_source='console')만이 이 함수에 도달한다. 자동 승인원(ss_exclude)은 정의만 돼 있고
    (search_term_judge.APPROVAL_SOURCE_SS_EXCLUDE) 어디에서도 자동 승인을 배선하지 않는다."""
    if proposal.target_type != "search_term":
        reason = (
            f"target_type={proposal.target_type!r} — 검색어 제외는 search_term 대상만 가능"
            "(restricted-keywords는 검색어 텍스트 등록 API, fail-closed)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if not proposal.adgroup_id:
        reason = (
            "adgroup_id 없음 — restricted-keywords API는 adgroupId 필수(ref 27 §8-1, 재생성 필요)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # target_id 길이 방어(codex 2R[P1]) — 생성 레인이 초과 검색어를 skip하지만, SQLite는 VARCHAR
    # 길이를 강제하지 않아 초과 저장이 물리적으로 가능하다. 잘린/초과 검색어를 그대로 실제
    # 제외키워드로 등록하면 엉뚱한 검색어가 차단되는 비대칭 사고이므로 writer 전에 fail-closed.
    if len(proposal.target_id) > _TARGET_ID_MAXLEN:
        reason = (
            f"검색어 길이 {len(proposal.target_id)} > {_TARGET_ID_MAXLEN}자(target_id String"
            f"({_TARGET_ID_MAXLEN})) — 잘린 텍스트로 실쓰기 방지(fail-closed, 생성 레인 skip 이중 방벽)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # ★쓰기 경로 게이트 — 「SHOPPING 명시 거부」에서 **라우팅**으로 바뀌었다 (D-NAO-181 ③).
    #
    # 종전(§실측-0): campaign_type이 SHOPPING이면 무조건 거부했다. 근거는 「쇼핑은
    # `POST restricted-keywords`가 HTTP 400/3728」이었고 **그 관측 자체는 지금도 사실이다.**
    # 틀렸던 것은 그 거부를 «쇼핑 제외는 못 쓴다»로 일반화한 것이다(교훈 #299) — 쇼핑은
    # `PUT /ncc/targets/{targetId}`로 **쓸 수 있다**(2026-08-17 실증: 전문 PUT이면 기존 보존).
    # 그래서 가드를 «지우는» 게 아니라 **경로를 고르는 것**으로 바꾼다. 지우기만 하면 실패할
    # 요청(POST)을 쇼핑에 계속 쏘게 되고, 그건 종전 가드가 막던 바로 그 낭비다.
    #
    # ★여기서 **최종 판정을 내리지 않는다.** `campaign_type`(캠페인 축)과 `adgroupType`
    #   (광고그룹 축)은 **다른 축**이고, 그 둘을 섞은 게이트가 D-NAO-179에서 723건 중 711건을
    #   시야 밖에 뒀다. 그래서 여기서는 «쓸 줄 아는 유형인가»만 조기 판별해 무의미한 왕복을
    #   막고, 실제 경로 선택은 `naver_sa_writer.add_exclusions`가 **라이브 adgroupType**으로 한다
    #   (이중 방벽 유지 — writer 쪽 관문은 그대로 있다).
    entity = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "adgroup", NaverEntity.entity_id == proposal.adgroup_id)
        .first()
    )
    entity_campaign_type = (entity.campaign_type or "") if entity is not None else ""
    if entity_campaign_type and entity_campaign_type not in _WRITABLE_EXCLUSION_CAMPAIGN_TYPES:
        reason = (
            f"campaign_type={entity_campaign_type!r}에 제외를 쓰는 경로를 모른다 — 조용히 넘기지 "
            f"않고 거부한다(fail-closed). 쓸 줄 아는 유형: "
            f"{sorted(_WRITABLE_EXCLUSION_CAMPAIGN_TYPES)} "
            f"(WEB_SITE=POST restricted-keywords · SHOPPING=PUT /ncc/targets, D-NAO-181)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # 실행 시점 전환 재검증(GATE ⑥, PLAN §3 — codex[P1] stale 후보) — pending 제안이 묵은 사이
    # 새 전환이 붙었을 수 있어, 쓰기 직전 최신 데이터로 §1-1 전환 보호 게이트를 다시 확인한다.
    # SS2 판정 시점엔 전환 0이라 후보가 됐어도, Confirm 시점에 purchase 전환≥1이면 "살아있는
    # 증거"이므로 제외를 거부한다(fail-closed).
    conv_now, ss_window_days = _search_term_conversions_in_window(
        db, proposal.adgroup_id, proposal.target_id, now,
    )
    if conv_now >= 1:
        reason = (
            f"[검색어제외] 실행 시점 전환 발생 — 보호 게이트 재검증 거부(rolling "
            f"{ss_window_days}일 purchase 전환={conv_now}건≥1, §1-1). 판정↔실행 "
            "사이 전환이 붙은 stale 후보 — 전환은 살아있는 증거라 제외 불가(fail-closed)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # 일일 캡(§1 4) — 오늘 성공 제외 수가 캡 도달이면 hold(비대칭 액션 runaway 방지, 조기 종료).
    excludes_today = _count_search_term_excludes_today(db, now)
    if excludes_today >= _SS_DAILY_EXCLUDE_CAP:
        reason = (
            f"검색어 제외 일일 캡 도달({excludes_today}≥{_SS_DAILY_EXCLUDE_CAP}, §1 4) — "
            "제외는 되돌림 비용 있는 비대칭 액션이라 일일 신규 제외 상한 유지(hold, 익일 재시도)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    # 캡 원자적 예약(codex[P1] TOCTOU) — 자기 claim 커밋 후 = 이 제안이 status='executing'으로
    # DB에 반영된 시점. 캡 산정을 "오늘 커밋된 성공 제외 + 현재 executing인 search_term_exclude
    # 제안(자기 포함)"으로 잡고, 합이 캡을 넘으면 **쓰기 전에** 중단한다(claim 해제·failed). 커밋된
    # change_log만 세던 사전/사후 검사는 동시 2건이 둘 다 검사를 통과한 뒤 각자 쓰기를 가할 수
    # 있는 원자성 공백이 있었다(캡 관통). executing 예약을 함께 세면 동시 2건은 서로의 executing을
    # 보아 양쪽 다 초과를 보고 중단한다 — 과보수(둘 다 안 쓰는 fail-closed, 사람이 재승인으로
    # 재시도 가능)이지만 캡 관통은 원천 차단된다. 아래 "쓰기 후 재카운트+롤백"은 최후 방벽으로 유지.
    committed_today = _count_search_term_excludes_today(db, now)
    # ★오늘(KST) 클레임만 센다(codex 2R[P2]): status='executing'만으로 세면 과거 크래시로
    # executing에 잔존한 stale 클레임(사람 조사 대상, 자동 복구 안 함)이 일일 캡 예약을 영구
    # 잠식한다(매일 캡이 1씩 줄어듦). 이 예약은 "지금 동시에 진행 중인 Confirm"만 봉쇄하면
    # 되므로 오늘 생성분으로 한정한다 — NaverProposal.created_at은 server_default=func.now()로
    # UTC 저장([[sqlite-server-default-now-is-utc]])이라, KST 오늘 [00:00, +1d)를 UTC 경계
    # [today00:00-9h, +1d)로 변환해 비교한다(auto_operator._day_bounds_utc와 동일 패턴).
    # 어제 이전 생성분의 동시 Confirm은 이 예약이 놓쳐도 쓰기-후 재카운트 롤백(③)이 최후 방벽.
    day_start_utc = datetime.combine(now.date(), datetime.min.time()) - timedelta(hours=9)
    executing_excludes = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.proposal_type == search_term_judge.SEARCH_TERM_EXCLUDE_TYPE,
            NaverProposal.status == "executing",
            NaverProposal.created_at >= day_start_utc,
            NaverProposal.created_at < day_start_utc + timedelta(days=1),
        )
        .count()
    )
    if committed_today + executing_excludes > _SS_DAILY_EXCLUDE_CAP:
        reason = (
            f"검색어 제외 일일 캡 원자 예약 초과(커밋 {committed_today}+executing {executing_excludes}"
            f">{_SS_DAILY_EXCLUDE_CAP}, §1 4) — 동시 Confirm 경합 방지 위해 쓰기 전 중단"
            "(claim 해제·failed, 캡 여유 회복 후 재승인 재시도)"
        )
        _guard_failure(db, proposal, now, "exclude_search_term", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    try:
        # ★유형이 경로를 고른다(D-NAO-181 ③): WEB_SITE→POST restricted-keywords /
        #   SHOPPING→PUT /ncc/targets(전문 전송·낙관적 잠금·보존 검증). adgroup_type을 넘기지
        #   않는 것은 의도다 — writer가 **라이브 adgroupType**으로 판정한다(캠페인 축과 광고그룹
        #   축을 섞지 않는다, D-NAO-179).
        result = naver_sa_writer.add_exclusions(proposal.adgroup_id, [proposal.target_id])
    except Exception as exc:  # WriteValidationError/WriteConcurrentEditError/WriteError/WriteVerificationError
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="exclude_search_term",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        log.error(
            "naver_execution_harness: 검색어 제외 실쓰기 실패 proposal_id=%s adgroup=%s term=%r — %s: %s",
            proposal.id, proposal.adgroup_id, proposal.target_id, type(exc).__name__, exc,
        )
        raise

    # codex[P2] TOCTOU 2단 검증(§1 4, docstring 참조): 쓰기 성공 직후 재카운트 — 이 시점엔
    # 이번 건의 log_entry가 아직 db에 없으므로, 재카운트가 캡에 이미 도달했다면 위 사전 검사
    # 통과 후 동시 Confirm이 먼저 커밋된 것 → 이번 건을 더하면 캡 초과, 방금 등록한 키워드를
    # 원복한다.
    excludes_after_write = _count_search_term_excludes_today(db, now)
    if excludes_after_write >= _SS_DAILY_EXCLUDE_CAP:
        rollback_reason = (
            f"일일 캡 동시성 초과 롤백(쓰기 후 재카운트 {excludes_after_write}≥{_SS_DAILY_EXCLUDE_CAP}, "
            f"§1 4) — 사전 검사 통과 후 동시 Confirm 경합으로 캡 도달, 방금 등록한 제외키워드"
            f"(created_ids={result.created_ids}) 원복"
        )
        try:
            # ★유형이 되돌리는 열쇠를 고른다(파워링크=회수한 id / 쇼핑=키워드 본문).
            #   쇼핑은 `created_ids`가 **구조적으로 비어 있어**(키워드 단위 id 없음) 이
            #   롤백이 아예 돌지 않았다 — 은닉은 아니었으나(아래 except가 사실대로 적는다)
            #   자동 원복이 없는 채였다. 파워링크 경로는 **바뀌지 않는다**.
            naver_sa_writer.rollback_exclusions(
                proposal.adgroup_id, [proposal.target_id], result.created_ids
            )
            rolled_back = True
            rollback_reason += " | 롤백 성공(재조회로 삭제 확인, delete_restricted_keywords fail-closed)"
        except Exception as rollback_exc:  # noqa: BLE001 — 롤백 실패도 fail-open 금지, 사실대로 기록
            rolled_back = False
            rollback_reason += (
                f" | 롤백 실패({type(rollback_exc).__name__}: {str(rollback_exc)[:300]}) — "
                "네이버 측에 제외키워드가 남아있을 수 있음, 수동 확인 필요"
            )
            log.error(
                "naver_execution_harness: 캡 초과 롤백 실패 proposal_id=%s adgroup=%s created_ids=%s — %s: %s",
                proposal.id, proposal.adgroup_id, result.created_ids,
                type(rollback_exc).__name__, rollback_exc,
            )
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도(캡 리셋 후)
        # ★before_value/after_value를 채우지 않는다(의도적, _guard_failure·위 예외 실패 경로와
        # 동일 규율) — _count_search_term_excludes_today는 dry_run=False ∧ after_value 존재만
        # "네이버 상태가 바뀐 확정 행"으로 센다. 여기는 상태를 원복했으므로(성공이든 실패든) 그
        # 카운트에 다시 잡히면 안 된다 — 롤백 세부는 rationale 텍스트로만 감사 기록한다.
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="exclude_search_term",
            rationale=f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER} {rollback_reason}",
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        log.error(
            "naver_execution_harness: 검색어 제외 일일 캡 동시성 초과 proposal_id=%s adgroup=%s "
            "term=%r rolled_back=%s",
            proposal.id, proposal.adgroup_id, proposal.target_id, rolled_back,
        )
        raise naver_sa_writer.WriteError(rollback_reason)

    # outcome 미기록 — proposal_scoreboard가 D+14까지 IS NULL로 식별(_execute_add_negative_keyword 주석).
    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action="exclude_search_term",
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
        "naver_execution_harness: 검색어 제외 실쓰기 성공 proposal_id=%s adgroup=%s term=%r created_ids=%s",
        proposal.id, proposal.adgroup_id, proposal.target_id, result.created_ids,
    )
    return log_entry


def _writeback_live_ad_observation(
    db: Session, *, adgroup_id: str, ads: list[dict], campaign_id: str, now: datetime,
) -> int:
    """D-NAO-170(B-4 나머지 절반): 「그룹입찰 死」 거부에 실려 온 라이브 관측을
    `naver_adgroup_product`에 되돌려 쓴다. 반환 = 갱신·삽입된 행 수.

    ══ 이게 «재라우팅»인 이유 ══
    재라우팅 기계는 이미 있다 — `auto_operator`의 `source='ad'` 분기가 그것이고, 그 판정은
    이 테이블에서 파생된다(`effective_bid._derive`). 없던 것은 **라우터의 입력을 고치는 손**
    이다. 라이브(가드)와 DB(라우터)가 갈라지면 라우터는 계속 그룹으로 보내고 가드는 계속
    거부해서, 그 그룹의 입찰이 회차마다 같은 자리에서 얼어붙는다. 관측을 되돌려 쓰면
    **다음 회차에 기존 경로가 스스로 소재로 절체**한다 — 새 쓰기 경로를 만들지 않는다
    (harness는 초크포인트이지 판정자가 아니다: 여기서 스텝을 재산정하지 않는다).

    ══ fail-**open**(best-effort) ══
    실패해도 삼키고 0을 반환한다. 이건 안전장치가 아니라 **수리 가속기**이고, 실패의 대가는
    「다음 정규 sync까지 절체가 늦어짐」이지 돈이 아니다. 반대로 여기서 예외를 올리면
    **거부를 기록하는 경로 자체가 깨진다** — 그건 확실한 손해다.

    ══ 왜 update-only가 아니라 upsert인가(설계 검토와 갈린 지점) ══
    "행이 없으면 건너뛴다"로 두면 **가장 나쁜 경우가 안 고쳐진다**: 소재 행이 아예 없는 그룹은
    `_derive`가 group 폴백을 주므로(유효 소재데이터 0건) 라우터가 영원히 그룹으로 보내고,
    영원히 거부당한다. 2026-07월 사고(03. 아이폰_강화유리, 9일간 그룹입찰 79건 무접촉)가
    정확히 그 모양이었다. `get_ads`가 `mall_product_id`를 포함해 **유니크 키를 전부 주므로**
    삽입에 추정이 필요 없고, 정규 sync가 다음 회차에 같은 값을 덮어써도 무해하다(멱등).
    """
    try:
        rows = [a for a in ads if a.get("ad_id") and a.get("mall_product_id")]
        if not rows:
            log.warning(
                "naver_execution_harness: D-NAO-170 write-back 대상 없음 adgroup=%s "
                "(라이브 응답에 ad_id/mall_product_id 부재) — 정규 sync 대기",
                adgroup_id,
            )
            return 0
        existing = {
            r.mall_product_id: r
            for r in db.query(NaverAdgroupProduct)
                       .filter(NaverAdgroupProduct.adgroup_id == adgroup_id).all()
        }

        # ★★덮기 **전에** 외부변경 탐지를 돌린다 (적대 리뷰 P1-1, 2026-08-10).
        #   초판은 앵커(`ad_edit_tm`·`ad_apply_tm`)만 동결하고 **같은 탐지기의 나머지 절반**
        #   (비교 대상 값 3종: ad_bid_amt·use_group_bid_amt·ad_user_lock)을 덮었다. 그러면
        #   다음 07:45 sync가 `prev_by_ad`를 이 행에서 만들 때 이미 라이브와 같은 값이라
        #   `_diff_ops`가 빈 리스트를 주고 → `ad_edit` 폴백으로 강등된다. `ad_edit`은
        #   `auto_up_base_bid`의 `op_type == "bid_change"` 필터에 **안 걸린다** →
        #   자동 상향 2.0× 누적 상한의 **기준점이 재설정되지 않는다**(codex 3R[P1]이 닫았던
        #   「대행사가 2,000→400으로 내렸는데 기준점은 2,000에 머물러 자동화가 10배로 되돌림」
        #   구멍의 재개봉). 하필 write-back을 유발하는 divergence의 정체가 `bid_mode_flip`
        #   (True→False) 그 자체라, **가장 중요한 신호가 지워지는** 구조였다.
        #
        #   여기서 탐지가 가능한 이유가 앵커 동결이다 — `detect_ad_external_changes`의 게이트
        #   ②③이 «양쪽 editTm 존재 ∧ 상이»를 요구하는데, 우리가 앵커를 안 건드렸으므로
        #   prev(옛 editTm) ≠ obs(라이브 editTm)가 성립한다. 추가 API 콜 0(가드가 넘긴 원본).
        #   선례: D-NAO-130의 `_record_inline_external_touch`(우리가 먼저 쓰면 사건이 소실되므로
        #   그 자리에서 남긴다)와 같은 논증을 DB 쓰기에 적용한 것.
        #
        #   ★탐지 실패 = 덮지 않는다(fail-**closed**). 이 한 지점만 방향이 반대인 이유:
        #   수리를 못 하면 «다음 sync까지 절체 지연»(D-NAO-170 이전 상태로 무해 복귀)이지만,
        #   탐지를 건너뛰고 덮으면 **대행사 조작 이력이 영구 소실**되고 그건 돈 경로다.
        #   덜 고치는 쪽이 잘못 고치는 쪽보다 낫다.
        from app.services.naver_ad import ad_external_change  # 함수 레벨(순환 회피 관례)
        prev_by_ad = {
            r.ad_id: {"edit_tm": r.ad_edit_tm, "ad_bid_amt": r.ad_bid_amt,
                      "use_group_bid_amt": r.use_group_bid_amt, "ad_user_lock": r.ad_user_lock}
            for r in existing.values() if r.ad_id
        }
        observed = [{**a, "adgroup_id": adgroup_id, "campaign_id": campaign_id} for a in rows]
        ad_external_change.run(db, prev_by_ad=prev_by_ad, observed=observed, now=now)

        touched = 0
        for a in rows:
            mall_pid = str(a["mall_product_id"])
            row = existing.get(mall_pid)
            if row is None:
                row = NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=mall_pid)
                db.add(row)
                existing[mall_pid] = row
            row.campaign_id = campaign_id
            row.product_name = (a.get("product_name") or row.product_name or "")[:300]
            row.ad_id = a.get("ad_id")
            row.ad_bid_amt = a.get("ad_bid_amt")
            row.use_group_bid_amt = a.get("use_group_bid_amt")
            row.ad_user_lock = a.get("ad_user_lock")
            # ad_edit_tm/ad_apply_tm은 **건드리지 않는다** — 그 둘은 외부 변경 탐지(D-NAO-127)의
            # 앵커라, 탐지를 안 거친 경로에서 전진시키면 «우리가 안 본 외부 변경»을 본 것으로
            # 만들어 버린다. 여기서 고치려는 것은 «실효 레이어 판별»뿐이다.
            row.synced_at = now  # 신선도 — 이 관측이 언제 것인지가 stale 판별의 유일 근거
            touched += 1
        db.commit()
        log.info(
            "naver_execution_harness: D-NAO-170 write-back 완료 adgroup=%s 소재 %d건 — "
            "다음 회차 라우터가 소재 레버로 절체한다",
            adgroup_id, touched,
        )
        return touched
    except Exception as e:  # noqa: BLE001 — best-effort(§fail-open): 수리 실패가 거부 기록을 깨면 안 된다
        db.rollback()
        log.warning(
            "naver_execution_harness: D-NAO-170 write-back 실패 adgroup=%s (%s) — "
            "거부는 유지되고 절체는 정규 sync까지 지연된다",
            adgroup_id, e,
        )
        return 0


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

    # IU-R R1(P1-1) rank-step 서보 신선도 게이트 — 서보의 ±15% 면제가 폐루프(경제성 상한·pace)
    # 밖에서 재사용되는 것을 봉쇄한다. 서보는 인라인 즉시 실행이라 정상 age는 초 단위 —
    # _RANK_STEP_MAX_AGE_MINUTES(10분) 초과분은 콘솔 재실행/재시도로 나중에 살아난 stale
    # 제안이므로 fail-closed 종결한다. created_at은 UTC 저장([[sqlite-server-default-now-is-utc]]) —
    # now(kst_now, KST naive)와 비교하려면 +9h 해서 KST로 맞춘다(diary_outcome._kst_date 관례).
    # created_at=None도 stale 취급(fail-closed) — 컬럼이 server default일 뿐 NOT NULL 제약이
    # 없어 NULL 행이 가능하고, None을 통과시키면 신선도 게이트가 우회된다(codex R1 2R 단서).
    if proposal.proposal_type in RANK_STEP_TYPES:
        if proposal.created_at is None:
            age_minutes = None
        else:
            created_at_kst = proposal.created_at + timedelta(hours=9)
            age_minutes = (now - created_at_kst).total_seconds() / 60
        if age_minutes is None or age_minutes > _RANK_STEP_MAX_AGE_MINUTES:
            age_str = "미상(created_at 없음)" if age_minutes is None else f"{age_minutes:.0f}분 경과"
            reason = (
                f"서보 제안 신선도 초과(인라인 전용) — 생성 후 {age_str} "
                f"(> {_RANK_STEP_MAX_AGE_MINUTES}분). 콘솔 재실행/재시도로 살아난 stale 제안은 "
                "경제성 상한·예산 pace 재검증 없이 ±15% 면제만 적용되므로 차단(fail-closed)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # BX2 GATE P1 + codex P2(D-NAO-70·71) — **탐색 타입 ⟺ explore_op 승인원 쌍방향 잠금**(target_type
    # 무관, 전 target_type 대칭 경계). 두 방향 모두 fail-closed:
    #   (P1) 탐색 스텝인데 explore_op이 아니면 차단 — 없으면 bid_up_explore + adgroup/keyword + 콘솔/
    #        기타 승인원 제안이 (BEP 완전성 면제 + 킬스위치 화이트리스트 밖) 최약 UP 경로로 샌다(GATE 실증).
    #   (P2) explore_op인데 탐색 스텝이 아니면 차단 — explore_op은 탐색 UP 전용 승인원이므로 bid_down류
    #        (소재 DOWN은 콘솔 Confirm/기타 승인원 몫)를 explore_op으로 실으면 승인원 오용(codex P2).
    # 정상 경로(둘 다 참=탐색 UP / 둘 다 거짓=비탐색)만 통과. BX3 source='group' 그룹 탐색(adgroup)도
    # explore_op으로만 발사되므로 이 게이트가 정상 경로를 막지 않는다(ad 분기 검사와 동형 이중, D-NAO-13).
    from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE
    _is_explore_type = proposal.proposal_type in EXPLORATION_STEP_TYPES
    _is_explore_src = proposal.approval_source == APPROVAL_SOURCE_EXPLORE
    if _is_explore_type != _is_explore_src:
        if _is_explore_type:
            reason = (
                f"탐색 스텝({proposal.proposal_type})은 explore_op 승인원 전용"
                f"(승인원={proposal.approval_source!r}) — 콘솔·기타 승인원의 탐색 UP은 봉쇄"
                "(fail-closed, GATE P1 전 target_type 대칭)"
            )
        else:
            reason = (
                f"explore_op 승인원은 탐색 스텝 타입 전용(UP) — proposal_type="
                f"{proposal.proposal_type!r}는 탐색 스텝 아님(소재 DOWN은 콘솔 Confirm 몫). "
                "승인원 오용 봉쇄(fail-closed, codex P2 역방향 잠금)"
            )
        _guard_failure(db, proposal, now, "update_bid", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # BX2(D-NAO-70·71) — 소재(ad) 실쓰기 **최종 경계**(codex 소급[P2] 2026-07-20 이중화 계승,
    # D-NAO-13 optimizer 쓰기 직전 하드 체크 동형). B3 개정:
    #   ① 카나리 1호(맥세이프) 캠페인 제한 **해제** — 전 캠페인 개방(D-NAO-70②). AD_BID_CANARY_CAMPAIGNS
    #      게이트 제거(그 상수는 auto_operator 레인 라우팅·delegation 봉쇄에서 계속 사용 — 여기선 미사용).
    #   ② 방향: 하향(bid_down)은 기존 B3 Confirm 경로(승인원 무관·전 캠페인) 유지 / 상향은 탐색
    #      자동 경로 전용 — approval_source='explore_op' ∧ 탐색 스텝 타입(EXPLORATION_STEP_TYPES)일
    #      때만 개방(D-NAO-70③). 비탐색(성과) 소재 UP·다른 승인원(콘솔 NULL·auto_op·delegation)의
    #      소재 UP은 여전히 봉쇄(Confirm 스코프 밖). 자동발사 봉쇄 약화 없음 — 승인원 화이트리스트만.
    # stale 제안(경로 밖 생성·잔존 pending)이 콘솔 승인만으로 최종 경계를 통과하는 fail-open을 차단.
    # 함수 레벨 import — delegation_gate와 동일 관례(모듈 결합 최소화·순환 회피).
    if proposal.target_type == "ad":
        from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE
        ad_guard = []
        if not proposal.adgroup_id:
            ad_guard.append("adgroup_id 없음(소재 제안 필수 컨텍스트)")
        if proposal.proposal_type in BID_UP_TYPES:
            # 소재 UP 자동 경로 화이트리스트 — (승인원, 허용 타입 집합) 쌍방향 잠금.
            # ★CS(적대적 리뷰 P1-1): bid_up_cold를 BID_UP_TYPES에 넣은 순간 이 경계가 전건
            #   fail-closed로 막아 CS가 한 건도 집행되지 않았다. explore와 동일 규약으로 개방하되,
            #   반드시 **쌍방향**이어야 한다(P1-3): cold_op는 bid_up_cold만, bid_up_cold는 cold_op만.
            #   한쪽만 열면 다른 승인원(콘솔 NULL·delegation)이 bid_up_cold를 태워 ±15% 완전 면제를
            #   무제한 상향으로 쓰는 구멍이 된다(리뷰 재현: 300원→90,000원).
            _AD_UP_ROUTES = {
                APPROVAL_SOURCE_EXPLORE: EXPLORATION_STEP_TYPES,   # BX2 D-NAO-70③
                APPROVAL_SOURCE_COLD: COLD_START_STEP_TYPES,       # CS D-NAO-96
            }
            # ★D-NAO-129(2026-07-29): 성과 상향 `bid_up`은 **승인원 무관 개방**(DOWN 대칭).
            #   왜 쌍방향 잠금을 안 거는가 — 그 잠금의 존재 이유는 "±15% 변경폭이 **면제된**
            #   타입이 아무 승인원으로 발사되는 것"을 막는 데 있다(재현: 300원→90,000원).
            #   bid_up은 CHANGE_PCT_EXEMPT_TYPES에도 EXPLORATION_STEP_TYPES에도 없어 ±15%
            #   클램프가 그대로 걸리므로 그 구멍이 원리적으로 재현되지 않는다. 나머지 UP 타입
            #   (bid_up_servo·bid_up_rank·bid_up_cold·bid_up_explore)의 잠금은 **불변**이다.
            #   ★이 집합에 새 타입을 넣기 전에 반드시 확인할 것: 그 타입이 변경폭 상한을
            #     면제받는가? 면제 타입을 여기 넣는 순간 위 구멍이 그대로 열린다.
            if proposal.proposal_type in _AD_UP_OPEN_TYPES:
                pass  # 클램프 적용 타입 — BEP·스톱로스·일예산·쿨다운·일일상한은 전량 존치
            else:
                allowed = _AD_UP_ROUTES.get(proposal.approval_source)
                if allowed is None:
                    ad_guard.append(
                        f"소재 UP은 자동 경로(explore_op/cold_op)만 개방(승인원={proposal.approval_source!r}) "
                        "— 그 밖의 소재 UP은 Confirm 스코프 밖(D-NAO-70③·96)"
                    )
                elif proposal.proposal_type not in allowed:
                    ad_guard.append(
                        f"승인원 {proposal.approval_source!r} 소재 UP은 {sorted(allowed)} 타입만"
                        f"(요청={proposal.proposal_type!r}) — 타입⟺승인원 쌍방향 잠금"
                    )
        elif proposal.proposal_type not in BID_DOWN_TYPES:
            ad_guard.append(
                f"proposal_type={proposal.proposal_type!r}는 소재-레벨 미지원 방향"
                "(UP=bid_up·탐색explore_op·콜드cold_op / DOWN=bid_down)"
            )
        if ad_guard:
            reason = "소재(ad) 실쓰기 경계 차단(fail-closed) — " + " · ".join(ad_guard)
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # ★CS(적대적 리뷰 P1-3) — 콜드 스텝은 **target_type='ad' 강제**. 이 검사가 없으면
    # target_type을 'adgroup'/'keyword'로 바꾼 bid_up_cold 제안이 위 소재 가드를 통째로 우회하고,
    # ±15% 완전 면제라 변경 폭을 막는 가드가 하나도 남지 않는다(리뷰 재현: 그룹 300원→90,000원).
    if proposal.proposal_type in COLD_START_STEP_TYPES and proposal.target_type != "ad":
        reason = (
            f"콜드 첫 입찰은 소재(ad) 전용(fail-closed) — target_type={proposal.target_type!r}. "
            "그룹/키워드 grain으로는 소재 UP 경계·상한 게이트를 우회하게 된다"
        )
        _guard_failure(db, proposal, now, "update_bid", reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # ★CS(적대적 리뷰 P1-3) — 콜드 상한 **쓰기-경계 하드 게이트**(탐색 게이트와 동형).
    # bid_up_cold는 ±15% 완전 면제라 레인이 산정한 min(이익상한, 목표순위 시장가)이 유일한 가격
    # 브레이크다. 레인 계산을 불신하고 여기서 재검증한다 — 경로 밖 생성·변조·낡은 제안 재생이
    # 최종 경계를 통과하는 fail-open 차단. 마커 부재/중복(오염) = fail-closed.
    if proposal.proposal_type in COLD_START_STEP_TYPES:
        cold_ceiling = decode_cold_ceiling(proposal.expected_effect)
        if cold_ceiling is None:
            reason = (
                "콜드 상한 마커 부재/오염(fail-closed) — bid_up_cold는 ±15% 완전 면제라 상한이 "
                "유일 가격 브레이크. cold_start_bid_lane 밖 생성/변조 제안 차단(D-NAO-96)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")
        if proposal.target_bid > cold_ceiling:
            reason = (
                f"콜드 상한 초과(fail-closed) — target_bid={proposal.target_bid}원 > 상한 "
                f"{cold_ceiling}원. '클릭당 확정 손해' 가격 진입 금지(쓰기-경계 재검증, D-NAO-96)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")
        # 신선도(리뷰 P2-7): 킬스위치 OFF 등으로 approved 상태로 남은 낡은 제안이 며칠 뒤 콘솔
        # 클릭 한 번으로 낡은 target_bid를 집행하는 재생을 막는다(rank-step 신선도 게이트 동형).
        # 시장가는 매일 변한다 — 어제 시세로 오늘 입찰하지 않는다.
        # ★created_at은 **UTC 저장**([[sqlite-server-default-now-is-utc]]) — now(kst_now, KST naive)와
        #   비교하려면 +9h 해서 KST로 맞춘다. 이걸 빼먹으면 모든 제안이 9시간(540분) 늙은 것으로
        #   보여 게이트가 전건을 죽인다(실제로 이 구현의 첫 판에서 그렇게 났다).
        # ★created_at=None도 stale 취급(fail-closed) — 컬럼에 NOT NULL 제약이 없어 NULL 행이
        #   가능하고, None을 통과시키면 신선도 게이트가 우회된다(rank-step과 동일 판단).
        if proposal.created_at is None:
            age_min = None
        else:
            age_min = (now - (proposal.created_at + timedelta(hours=9))).total_seconds() / 60
        if age_min is None or age_min > _COLD_STEP_MAX_AGE_MINUTES:
            age_str = "미상(created_at 없음)" if age_min is None else f"{age_min:.0f}분 경과"
            reason = (
                f"콜드 제안 신선도 초과(fail-closed) — 생성 후 {age_str} "
                f"(> {_COLD_STEP_MAX_AGE_MINUTES}분). 시장가는 매일 변하므로 낡은 시세로 집행 금지"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # BX3(D-NAO-70·71, PLAN §3 BX2 GATE 이월 P2①) — 탐색 스텝 경제성 상한 **쓰기-경계 하드
    # 게이트**. D-NAO-71로 수량·빈도 캡이 사라져 경제성 상한이 **유일한 가격 브레이크**이므로,
    # 레인 계산(run_hourly_lane의 min(step, ceiling) 클램프)을 불신하고 여기서 재검증한다 —
    # 레인이 ceiling을 잘못 클램프하거나 경로 밖 생성/변조 제안이 최종 경계를 통과하는 fail-open
    # 차단. ceiling은 run_hourly_lane이 expected_effect에 심은 마커(base_bid 마커 관례 동형, 신규
    # 마이그레이션 금지). ★마커 부재/중복/오염 = fail-closed(경제 근거 없이 탐색 상향 금지) —
    # run_hourly_lane 정상 생성분은 항상 단일 suffix 마커 보유 → 정상 경로 무영향. explore_op·
    # 탐색 타입은 위 GATE P1 쌍방향 잠금을 이미 통과했으므로 여기 도달하는 탐색 스텝은 정상 경로다.
    if proposal.proposal_type in EXPLORATION_STEP_TYPES:
        ceiling = decode_exploration_ceiling(proposal.expected_effect)
        if ceiling is None:
            reason = (
                "탐색 경제성 상한 마커 부재/오염(fail-closed) — D-NAO-71로 상한이 유일 가격 "
                "브레이크. run_hourly_lane 밖 생성/변조 제안 차단(PLAN §3 BX2 GATE 이월 P2①)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")
        if proposal.target_bid > ceiling:
            reason = (
                f"탐색 경제성 상한 초과(fail-closed) — target_bid={proposal.target_bid}원 > 상한 "
                f"{ceiling}원. '클릭당 확정 손해' 가격 진입 금지(PLAN §1 가드2, 쓰기-경계 재검증)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    context = _build_guardrail_context(db, proposal, now)

    # IU-R R2(P1-5) rank-step TOCTOU 방어 — estimate/서보 스텝은 **제안 시점 current_bid**를
    # 기준으로 목표를 산정한다(estimate 필요입찰·서보 증분 모두). 그 사이(생성→실행) 라이브
    # bid가 MOP·사람·외부로 바뀌었으면 산정 전제가 깨졌으므로 **fail-closed 중단**한다 —
    # 하니스에서 재산정하지 않는다(초크포인트 순수성: 여기에 estimate 재호출/네트워크를 얹지
    # 않는다, PLAN §2 R2 point6·§codex P2-2). 재산정은 다음 시간당 레인의 fresh current 몫.
    # DB 상태 = failed(stale 사유)로 종결 — pending 잔류 시 자동 재시도 루프 위험(_guard_failure
    # 관례 동형). base_bid는 run_hourly_lane이 expected_effect에 심은 마커(신규 마이그레이션
    # 금지 — 기존 Text 컬럼 재사용). ★마커 부재/중복/오염 = fail-closed(codex R2 P1) —
    # rank-step은 ±15% 면제 타입이라 산정 base 검증 없이 큰 스텝이 나가면 안 된다.
    # run_hourly_lane 정상 생성분은 항상 단일 suffix 마커 보유 → 정상 경로 무영향.
    # 라이브 재조회 실패(current_bid None)는 아래 guardrail 완전성 게이트가 fail-closed 차단.
    if proposal.proposal_type in RANK_STEP_TYPES:
        base_bid = decode_base_bid(proposal.expected_effect)
        if base_bid is None:
            reason = (
                "rank-step base_bid 마커 부재/오염(fail-closed) — ±15% 면제 타입은 제안 시점 "
                "산정 base 검증 없이 실행 금지. run_hourly_lane 밖 생성/변조 제안 차단(codex R2 P1)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")
        live_bid = context.get("current_bid")
        if live_bid is not None and int(live_bid) != base_bid:
            reason = (
                f"rank-step TOCTOU 중단(fail-closed) — 제안 시점 base_bid={base_bid}원 ≠ 실행 시점 "
                f"라이브 bid={live_bid}원. 스텝 산정 전제(estimate/서보 증분)가 그 사이 외부 변경으로 "
                "깨짐 — 재산정 없이 중단(다음 시간당 레인이 fresh current로 재진입, PLAN §2 R2 P1-5)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # BX3 codex P1(기울기 연속성·TOCTOU): 탐색 스텝도 제안 시점 step_base(적응 스텝 산정 기준가)를
    # base_bid 마커로 실어 보낸다 — rank-step과 동형 TOCTOU. 제안 생성(라이브 재조회)→실행(라이브
    # 재조회) 사이 소재/그룹 입찰이 MOP·사람·외부로 바뀌었으면 산정 전제가 깨졌으므로 fail-closed
    # 중단(재산정 없이 다음 사이클 fresh current로 재진입). ★탐색은 RANK_STEP_TYPES가 아니라 위
    # 블록을 안 타므로 별도 게이트. 마커 부재/오염도 fail-closed(경로 밖 생성/변조 차단).
    if proposal.proposal_type in EXPLORATION_STEP_TYPES:
        exp_base = decode_base_bid(proposal.expected_effect)
        if exp_base is None:
            reason = (
                "탐색 base_bid 마커 부재/오염(fail-closed) — 적응 스텝 산정 기준가 검증 없이 실행 "
                "금지. run_hourly_lane 밖 생성/변조 제안 차단(codex P1 기울기 연속성·TOCTOU)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")
        exp_live = context.get("current_bid")
        if exp_live is not None and int(exp_live) != exp_base:
            reason = (
                f"탐색 TOCTOU 중단(fail-closed) — 제안 시점 base_bid={exp_base}원 ≠ 실행 시점 "
                f"라이브 bid={exp_live}원. 적응 스텝 전제(레버 입찰)가 그 사이 외부 변경으로 깨짐 — "
                "재산정 없이 중단(다음 탐색 사이클이 fresh current로 재진입, codex P1)"
            )
            _guard_failure(db, proposal, now, "update_bid", reason)
            raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    # S3(D-NAO-43 성장 확장): adgroup 증액(bid_up/growth_bid_up)은 guardrail_gate._check_bid의
    # BEP 검사(roas_corrected/target_roas)가 그 값이 None이면 fail-open(검사를 건너뜀)이라,
    # 컨텍스트가 완전히 채워지지 않은 채로 guardrail_gate에 넘기면 D-NAO-1 이익하한이 조용히
    # 우회된다. _build_guardrail_context의 adgroup up 브랜치가 이 두 값을 채우려 시도하지만
    # (adgroup_window_agg 창에 실적이 없거나 재조회가 실패하면 여전히 None일 수 있음) —
    # executor가 실행 직전 한 번 더 완전성을 확인해 fail-closed로 막는다(guardrail_gate
    # 자체는 변경하지 않음 — S2의 blanket 차단을 이 데이터 기반 차단으로 대체).
    # GATE P2-B(D-NAO-66 IU): "keyword"도 완전성 게이트에 포함 — 종전엔 adgroup/ad만이었고
    # 키워드 UP의 BEP 바닥은 핫셋 클릭 게이트·정착창 검사와의 암묵적 커플링에 기대고 있었다.
    # IU가 장중-단독 UP(정산 판정불가 유닛)을 열었으므로 키워드도 컨텍스트 완전성(BEP 원료·
    # 일예산)을 명시적으로 보장한다(가드 약화 없음 — keyword 브랜치는 이 원료를 원래 채움).
    # BX2(D-NAO-70·71) 탐색 스텝 예외: EXPLORATION_STEP_TYPES는 콜드 그룹(정착 표본 없음)에서
    # 도는 자동 실쓰기라 BEP를 못 잰다(표본 없는 그룹에 표본-기반 이익하한 강제 = 탐색 원천 봉쇄).
    # 대체 가격 브레이크 = product_bep 연동 경제성 상한(exploration.exploration_ceiling, BX3 레인이
    # target_bid를 상한까지 클램프) + 비용-기반 백스톱(무전환 스톱로스·일예산 — guardrail_gate의
    # fail-open 검사로 탐색에도 그대로 작동). PLAN §1 가드6 "표본 없는 그룹에 표본 판단 금지".
    # ★GATE P1: 면제 조건에 **explore_op 승인원까지 결합**(면제와 승인원을 한 지점에서 잠금) —
    # 탐색 타입인데 explore_op이 아니면 면제 없이 기존 이익하한 검사가 정상 적용된다. 위 GATE P1
    # 게이트가 이미 그런 제안을 fail-closed로 막지만(도달 불가), 이 이중 결합으로 "면제가 승인원과
    # 분리돼 다른 target_type에서 새는" 클래스의 결함을 원천 차단한다(리뷰어 권고 1안).
    from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE as _EXPLORE_SRC
    _exploration_exempt = (
        proposal.proposal_type in EXPLORATION_STEP_TYPES
        and proposal.approval_source == _EXPLORE_SRC
    )
    # ★CS: 콜드 첫 입찰도 같은 이유로 표본-기반 완전성 게이트 면제 — **콜드 소재는 정의상 정착
    #   표본이 없어 BEP(roas_corrected)를 잴 수 없다**. 표본이 없는 대상에 표본 판단을 요구하면
    #   레인이 전건 fail-closed로 죽는다(탐색이 같은 이유로 이미 면제받는다, PLAN §1 가드6).
    #   대체 가격 브레이크 = 위에서 **이미 통과한** 콜드 상한 하드 게이트(min(이익상한, 시장가)를
    #   expected_effect 마커로 재검증) — 면제가 무방비를 뜻하지 않는다.
    #   면제는 탐색과 동일하게 **타입 ∧ 승인원**을 함께 잠근다(면제가 승인원과 분리돼 다른
    #   target_type에서 새는 결함 클래스 원천 차단).
    #   ★면제되는 것은 "컨텍스트 **완전성 요구**" 하나뿐이다 — 검사 자체는 그대로 돈다.
    #     라이브 확인(리뷰 2R): 표본 없는 콜드는 통과하되, 무전환 지출 큰 그룹은 **스톱로스가**,
    #     적자 그룹(보정ROAS<target)은 **BEP 하한이** 그대로 차단한다. 단 target_roas가 미해석
    #     (None)이면 BEP 검사는 fail-open이다(guardrail_gate 성질 — 탐색도 동일).
    _cold_exempt = (
        proposal.proposal_type in COLD_START_STEP_TYPES
        and proposal.approval_source == APPROVAL_SOURCE_COLD
    )
    if (
        proposal.target_type in ("adgroup", "ad", "keyword")
        and proposal.proposal_type in BID_UP_TYPES
        and not _exploration_exempt
        and not _cold_exempt
    ):
        # guardrail_gate._check_bid의 up 전용 검사(BEP·스톱로스·일예산)는 그 원료가 None이면
        # 전부 fail-open(검사 건너뜀)이다 — 컨텍스트가 불완전한 채 넘기면 D-NAO-1 이익하한·
        # 일예산 상한이 조용히 우회된다. 각 원료의 소스가 달라(BEP/스톱로스=window_agg,
        # 일예산=당일 스냅샷+라이브 재조회) 하나가 채워져도 다른 게 빌 수 있으므로, executor가
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
                f"{proposal.target_type} 증액 가드 컨텍스트 불완전(fail-closed) — " + ", ".join(missing)
                + " (guardrail_gate의 해당 up 검사는 값이 None이면 fail-open이라 executor가 "
                "메꿈, codex[P1] D-NAO-43 S3 + GATE P2-B keyword 확장)"
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

    writer_kwargs: dict = {}
    if proposal.target_type == "keyword":
        writer_fn = naver_sa_writer.update_keyword_bid
    elif proposal.target_type == "adgroup":
        writer_fn = naver_sa_writer.update_adgroup_bid
    else:  # ad (B3, D-NAO-65) — 소재 bidAmt 직접 수정(useGroupBidAmt=false 실효 소재)
        writer_fn = naver_sa_writer.update_ad_bid
        # ★D-NAO-129 codex 적대[P1] TOCTOU: 가드레일이 **판정에 쓴 그 값**을 writer에 넘겨
        #   쓰기 직전 실측과 일치할 때만 PUT한다(CAS). 어긋나면 그 사이 외부가 바꾼 것이므로
        #   검증된 ±15%가 성립하지 않는다 — 쓰지 않고 다음 회차가 새 값으로 다시 판정한다.
        #   context["current_bid"]는 바로 위 guardrail_gate.check가 클램프·방향 검증에 쓴 값이다.
        writer_kwargs["expected_before_bid"] = context.get("current_bid")
    try:
        result = writer_fn(proposal.target_id, proposal.target_bid, **writer_kwargs)
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        # ★D-NAO-170: 「그룹입찰 死」 거부는 단순 실패가 아니라 **데이터 수리 신호**다.
        #   마커로 분리해 세고(위 LEVER_MISMATCH_MARKER), 아래에서 관측을 DB에 되돌려 쓴다.
        lever_mismatch = isinstance(exc, naver_sa_writer.GroupBidDeadError)
        mismatch_tag = f" {LEVER_MISMATCH_MARKER}" if lever_mismatch else ""
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action="update_bid",
            rationale=(
                f"{proposal.rationale or ''} {WRITE_FAILURE_MARKER}{mismatch_tag} "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            ),
            predicted_json=proposal.expected_effect, proposal_id=proposal.id,
            dry_run=False, outcome="failed", changed_at=now, executed_at=now,
        )
        db.add(fail_entry)
        db.commit()
        if lever_mismatch:
            _writeback_live_ad_observation(
                db, adgroup_id=exc.adgroup_id, ads=exc.ads,
                campaign_id=proposal.campaign_id, now=now,
            )
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


def _budget_log_action(proposal: NaverProposal) -> str:
    """change_log.action 라벨 — BP(예산 페이싱, D-NAO-102 ⑥) 쓰기만 세분화한다.

    'update_budget'(기본, 봉투·콘솔 Confirm 경로) vs 'budget_up_pacing'/'budget_down_pacing'
    (BP 레인). ★실행 경로·가드레일·디스패치 키(_WRITE_EXECUTORS/'update_budget')는 전부
    그대로다 — 이 함수가 바꾸는 건 사후 식별용 라벨뿐이라 초크포인트는 여전히 하나다.
    판별은 approval_source(구조화 컬럼)로 한다 — rationale 텍스트 파싱 금지 원칙 준수."""
    if proposal.approval_source != budget_pacing.APPROVAL_SOURCE_PACING:
        return "update_budget"
    return (
        budget_pacing.ACTION_BUDGET_DOWN_PACING
        if proposal.proposal_type == "budget_down"
        else budget_pacing.ACTION_BUDGET_UP_PACING
    )


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
    # BP(D-NAO-102 ⑥) 라벨 분기 — 실행 경로 불변, change_log.action만 세분화.
    log_action = _budget_log_action(proposal)

    if proposal.target_type != "campaign":
        reason = (
            f"target_type={proposal.target_type!r} — 캠페인 단위 예산만 구현됨(정직 경계)"
        )
        _guard_failure(db, proposal, now, log_action, reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    if proposal.target_budget is None:
        reason = "target_budget 없음 — 구조 결함(구 제안이거나 재생성 필요)"
        _guard_failure(db, proposal, now, log_action, reason)
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
        _guard_failure(db, proposal, now, log_action, reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    context = _build_guardrail_context(db, proposal, now)
    gate_proposal = {
        "proposal_type": proposal.proposal_type, "target_bid": None, "target_lock": None,
        "target_budget": proposal.target_budget,
    }
    block_reason = guardrail_gate.check(gate_proposal, context, now=now)
    if block_reason is not None:
        reason = f"가드레일 차단 — {block_reason}"
        _guard_failure(db, proposal, now, log_action, reason)
        raise MissingExecutionTargetError(f"proposal_id={proposal.id} {reason}")

    _claim_executing(db, proposal)

    try:
        result = naver_sa_writer.update_campaign_budget(proposal.campaign_id, proposal.target_budget)
    except Exception as exc:  # WriteValidationError/WriteError/WriteVerificationError + requests 계열
        proposal.status = "failed"  # 자동 재시도 차단(approved 게이트) — 재승인만 재시도 경로
        fail_entry = NaverChangeLog(
            entity_type=proposal.target_type, entity_id=proposal.target_id,
            campaign_id=proposal.campaign_id, action=log_action,
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
        campaign_id=proposal.campaign_id, action=log_action,
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
    "exclude_search_term": _execute_search_term_exclude,  # SS3-A(검색어 제외)
}


def open_executable_actions() -> list[str]:
    """지금 실제로 실쓰기 가능한 액션 목록(정렬). 이중 방벽의 **교집합**이다:
    `OPEN_ACTIONS`(D-NAO-16 개방 순서)에 있으면서 `_WRITE_EXECUTORS`(디스패치 구현)에도
    있는 것만. 콘솔 배너의 "현재 개방" 표시가 이 값을 진실로 삼는다(공개 헬퍼로 노출해
    라우터가 프라이빗 `_WRITE_EXECUTORS`를 직접 참조하지 않게 한다 — 하드코딩 라벨이
    개방 순서와 어긋나던 결함 재발 방지)."""
    return sorted(OPEN_ACTIONS & set(_WRITE_EXECUTORS))


def real_write_blocker(proposal: NaverProposal, db: Session | None = None) -> str | None:
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
    ★스코프(D-NAO-244) 판정은 `db`를 준 호출에서만 한다 — 조건은 execute()의 스코프 가드와
    **글자 그대로 같다**(`approval_source is not None` ∧ `blocked_by_scope`, 같은 리졸버).
    왜 필요한가: 이 함수가 스코프를 안 보던 동안, 엔진이 승인해 놓고 harness가 영원히 거부하는
    제안(prod 실측 119건)에 대해 콘솔이 `executable=True`(사유 없음)를 표시했다 —
    **값이 도는 층은 막고 있는데 사람이 읽는 층은 「실행 가능」이라 말하는** 상태였다.
    `db` 없이 부르면 종전과 완전히 동일하게 동작한다(구조 판정만).
    ★사람 손(수동 콘솔 승인, `approval_source=NULL`)에는 이 사유를 붙이지 않는다 —
    execute()가 그 경로를 스코프에서 면제하므로, 붙이면 콘솔이 «실행되는 것»을 못 한다고 거짓말한다.
    """
    if db is not None and proposal.approval_source is not None:
        # execute()의 스코프 가드와 같은 조건·같은 리졸버(위 ★ 참조). 순서: 구조 판정보다 먼저 —
        # 스코프 밖이면 구조가 완벽해도 이 제안은 영원히 실행되지 않는다(사람이 읽는 층의 진실).
        from app.services.naver_ad import adgroup_scope as _adgroup_scope

        if _adgroup_scope.blocked_by_scope(db, proposal.campaign_id, proposal.adgroup_id):
            if proposal.adgroup_id is None:
                return (
                    "자동운영 스코프 — 캠페인 레벨 액션은 그룹 귀속 불가로 실행 불가"
                    "(D-NAO-244). 엔진이 승인했어도 쓰기 직전에 거부된다."
                )
            return (
                f"자동운영 스코프 밖 광고그룹({proposal.adgroup_id}) — 엔진 승인분은 실행 불가"
                "(D-NAO-244). 쓰기 직전 스코프 가드가 거부한다."
            )
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
    elif action == "exclude_search_term":
        # SS3-A(검색어 제외) 구조 판정(콘솔 executable) — _execute_search_term_exclude의 구조
        # 가드와 동일 정적 조건(이중 방벽). SHOPPING 명시 거부·일일캡은 라이브/집계가 필요한
        # 실행시점 가드라 여기(정적 UI 판정)선 판정하지 않는다(add_negative_keyword의 ⚠️ 관례).
        if proposal.target_type != "search_term":
            return (
                f"target_type={proposal.target_type!r} — 검색어 제외는 search_term 대상만 가능"
                "(재생성 필요)"
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
        # BX2 GATE P1 + codex P2: 탐색 타입 ⟺ explore_op 승인원 **쌍방향 잠금**(target_type 무관,
        # _execute_update_bid 대칭). (P1) 탐색 타입인데 explore_op 아니면 executable=False —
        # bid_up_explore + adgroup/keyword + 콘솔이 콘솔 Confirm으로 최약 UP 경로로 열리는 것 차단
        # (적대 GATE 실증). (P2) explore_op인데 탐색 타입 아니면 executable=False — explore_op 승인원
        # 오용(소재 DOWN 등)을 콘솔 판정에서도 봉쇄.
        from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE
        _is_explore_type = proposal.proposal_type in EXPLORATION_STEP_TYPES
        _is_explore_src = proposal.approval_source == APPROVAL_SOURCE_EXPLORE
        if _is_explore_type != _is_explore_src:
            if _is_explore_type:
                return (
                    f"탐색 스텝({proposal.proposal_type})은 explore_op 승인원 전용 — 콘솔·기타 "
                    "승인원의 탐색 UP은 실행 버튼 비활성(GATE P1 전 target_type 대칭)"
                )
            return (
                f"explore_op 승인원은 탐색 스텝 타입 전용(UP) — proposal_type={proposal.proposal_type} "
                "는 탐색 스텝 아님(승인원 오용, codex P2 역방향 잠금)"
            )
        # BX3 GATE P2-risk3: 탐색 스텝 경제성 상한 재검증 — _execute_update_bid의 쓰기-경계 하드
        # 게이트와 **동일 정적 판정**(이중 방벽·콘솔 executable 대칭). 마커 decode+비교는 라이브
        # 재조회가 필요 없는 순수 정적 비교라 이 함수 설계(정적만)에 부합한다(explore_op 잠금과 동형).
        # 마커 부재/오염·target_bid>상한이면 콘솔 실행 버튼 비활성(경로 밖 생성/변조 제안 차단).
        if _is_explore_type:
            ceiling = decode_exploration_ceiling(proposal.expected_effect)
            if ceiling is None:
                return (
                    "탐색 경제성 상한 마커 부재/오염 — 경제 근거 없이 상향 금지(실행 버튼 비활성, "
                    "GATE P2-risk3 쓰기경계 대칭)"
                )
            if proposal.target_bid is not None and proposal.target_bid > ceiling:
                return (
                    f"탐색 경제성 상한 초과 — target_bid={proposal.target_bid}원 > 상한 {ceiling}원"
                    "('클릭당 확정 손해' 진입 금지, 실행 버튼 비활성)"
                )
            # codex P1(TOCTOU): base_bid 마커 존재 정적 확인(라이브 비교는 _execute_update_bid).
            # 마커 부재/오염 = 경로 밖 생성/변조 → 콘솔 실행 버튼 비활성(rank-step 마커 계약 동형).
            if decode_base_bid(proposal.expected_effect) is None:
                return (
                    "탐색 base_bid 마커 부재/오염 — 적응 스텝 산정 기준가 없이 실행 금지"
                    "(실행 버튼 비활성, codex P1 TOCTOU 정적 검증)"
                )
        # BX2(D-NAO-70·71): 소재(ad) 실쓰기 구조 게이트(콘솔 executable 판정) — _execute_update_bid의
        # 최종 경계 가드와 동일 판정(이중 방벽). 정적 비교만(라이브 재조회 없음, 이 함수 설계 유지).
        #   ① 카나리 캠페인 제한 해제(전 캠페인, D-NAO-70②).
        #   ② UP=탐색(explore_op)+탐색 스텝 타입만 / DOWN(bid_down)=전 캠페인 Confirm. 비탐색 UP·다른
        #      승인원 UP은 미개방(stale 제안·경로 밖 생성이 콘솔에서 executable로 보이는 것 차단).
        if proposal.target_type == "ad":
            from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE
            if not proposal.adgroup_id:
                return "adgroup_id 없음 — 소재 제안 필수 컨텍스트 부족(재생성 필요)"
            if proposal.proposal_type in BID_UP_TYPES:
                # ★D-NAO-129 codex 적대[P2]: executor와 **같은 판정**을 써야 한다. 종전엔 이
                #   함수만 explore/cold 화이트리스트에 머물러, 콘솔은 "실행 불가"인데 내부
                #   harness.execute()는 통과하는 **쓰기 경계 이중 계약**이 생겼다(사람이 승인한
                #   소재 UP이 콘솔에서 409로 막힘). 같은 상수를 공유해 계약을 하나로 되돌린다.
                if proposal.proposal_type not in _AD_UP_OPEN_TYPES:
                    # 타입⟺승인원 쌍방향 잠금(_execute_update_bid과 동일 판정 — 이중 방벽).
                    _routes = {
                        APPROVAL_SOURCE_EXPLORE: EXPLORATION_STEP_TYPES,  # BX2 D-NAO-70③
                        APPROVAL_SOURCE_COLD: COLD_START_STEP_TYPES,      # CS D-NAO-96
                    }
                    allowed = _routes.get(proposal.approval_source)
                    if allowed is None:
                        return (
                            "소재(ad) UP은 bid_up·자동 경로(explore_op/cold_op)만 개방 — 그 밖의 "
                            "소재 UP은 Confirm 스코프 밖(D-NAO-70③·96, 콘솔 실행 버튼 비활성)"
                        )
                    if proposal.proposal_type not in allowed:
                        return (
                            f"승인원 {proposal.approval_source} 소재 UP은 {sorted(allowed)} 타입만"
                            f"(요청={proposal.proposal_type}) — 타입⟺승인원 쌍방향 잠금"
                        )
            elif proposal.proposal_type not in BID_DOWN_TYPES:
                return (
                    f"소재(ad) {proposal.proposal_type}은 미지원 방향"
                    "(UP=bid_up·탐색explore_op·콜드cold_op / DOWN=bid_down)"
                )
        # CS(리뷰 P1-3): 콜드 스텝 구조 게이트 — target_type 강제 + 상한 마커 존재(정적 판정).
        if proposal.proposal_type in COLD_START_STEP_TYPES:
            if proposal.target_type != "ad":
                return (
                    f"콜드 첫 입찰은 소재(ad) 전용 — target_type={proposal.target_type} "
                    "(그룹/키워드 grain은 소재 UP 경계·상한 게이트 우회 경로)"
                )
            _cold_ceiling = decode_cold_ceiling(proposal.expected_effect)
            if _cold_ceiling is None:
                return (
                    "콜드 상한 마커 부재/오염 — ±15% 완전 면제 타입이라 상한 없이 실행 금지"
                    "(실행 버튼 비활성, D-NAO-96)"
                )
            # 리뷰 P3: 상한 초과 정적 검사도 여기 둔다(탐색엔 있는데 CS엔 없었다) — 없으면
            # 콘솔엔 "실행 가능"으로 보이다가 클릭하면 executor가 죽인다(UI 거짓말).
            if proposal.target_bid is not None and proposal.target_bid > _cold_ceiling:
                return (
                    f"콜드 상한 초과 — target_bid={proposal.target_bid}원 > 상한 {_cold_ceiling}원"
                    "(실행 버튼 비활성, D-NAO-96)"
                )
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
    # BP(D-NAO-102 ⑥): 기록 라벨만 세분화 — 디스패치/OPEN_ACTIONS 키는 계속 `action`
    # (초크포인트 불변). dry-run change_log·일기도 같은 라벨을 써야 BP 레인이 사후 식별된다.
    log_action = _budget_log_action(proposal) if action == "update_budget" else action

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
        from app.services.naver_ad.exploration import APPROVAL_SOURCE_EXPLORE  # BX2 탐색 승인원

        # D-NAO-125 codex[P1]: 소재 자동 실행 킬스위치 — 진입 지점(여기)과 writer 직전
        # (_claim_executing) 2중. 진입에서 먼저 막으면 라이브 재조회(get_ad_bid) API 콜조차
        # 나가지 않는다. 스위치를 내린 뒤 잔존 approved 행이 다음 실행자를 타고 나가는 것을
        # 막는 것이 목적 — 사람 승인(approval_source NULL/console)은 이 블록 밖이라 무영향.
        if (
            proposal.target_type == "ad"
            and proposal.proposal_type in _auto_operator._AD_AUTO_EXEC_PROPOSAL_TYPES
            and is_auto_exec(proposal)  # ★codex 2R[P1]: 사람 console 승인은 스위치와 무관
            and not _auto_operator.AD_BID_ROUTING_ENABLED
        ):
            log.warning(
                "naver_execution_harness: 소재 자동 실행 킬스위치 OFF(진입) — proposal_id=%s "
                "ad=%s 실행 거부(쓰기·change_log 없음, D-NAO-125)",
                proposal.id, proposal.target_id,
            )
            raise KillSwitchEngagedError(
                f"proposal_id={proposal.id} target={proposal.target_id} — "
                "AD_BID_ROUTING_ENABLED=False(소재 자동 실행 킬스위치 OFF)"
            )

        if proposal.approval_source in (
            _auto_operator.APPROVAL_SOURCE_DAILY, _auto_operator.APPROVAL_SOURCE_HOURLY,
            _auto_operator.APPROVAL_SOURCE_PROBE,  # D-NAO-58 CD2: 탐침도 동일 킬스위치 가드(우회 금지)
            _auto_operator.APPROVAL_SOURCE_REVERT,  # D-NAO-58 CD3: 되돌림도 진입 가드(probe_op와 동일 2중 harness 방어)
            budget_pacing.APPROVAL_SOURCE_PACING,  # BP D-NAO-102: 예산 페이싱 증액·원복도 킬스위치 가드(우회 금지)
            APPROVAL_SOURCE_EXPLORE,  # BX2 D-NAO-70: 탐색 자동 실쓰기도 진입 킬스위치 가드(우회 금지, probe_op 관례)
            APPROVAL_SOURCE_COLD,  # CS: 콜드 첫 입찰도 진입 킬스위치 가드(우회 금지 — 자동 실쓰기 레인)
            search_term_judge.APPROVAL_SOURCE_SS_EXCLUDE,  # SS3-A: 미래 자동 활성화 대비 사전 등록(현재 미배선)
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
                    adgroup_id=proposal.adgroup_id, action=log_action,
                    rationale="킬스위치 OFF(쓰기 직전 재확인)", now=now,
                )
            except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
                log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
            raise KillSwitchEngagedError(
                f"proposal_id={proposal.id} campaign_id={proposal.campaign_id} — "
                f"auto_operate=False(킬스위치 OFF, 쓰기 직전 재확인)"
            )

        # ★D-NAO-244 스코프 게이트 — «계약의 증거»가 서는 자리.
        # 스코프 판정을 여기(실쓰기 초크포인트)에 두는 이유: 생성측에만 두면 pending이 영원히
        # 0이 되어 「막혔다」와 「애초에 안 만들어졌다」를 구별할 수 없다(2026-08-21 실사고).
        # 생성측 필터(auto_operator)는 죽은 카드를 안 만드는 «소음 제거»이고, 잠김의 증거는
        # 이 지점이다 — 둘 다 adgroup_scope 단일 리졸버를 읽으므로 갈라질 수 없다.
        #
        # ★적용 대상이 `approval_source is not None`(엔진 승인분)인 것은 의도다 — 바로 위
        # 킬스위치 가드와 동일 경계다. 스코프는 «엔진의 자율 범위»를 좁히는 장치이지 사람의
        # 손을 묶는 게이트가 아니다(수동 콘솔 승인 approval_source=NULL은 이 블록 밖).
        # 전역 §1 "사고가 나도 게이트를 새로 세우지 않는다" — 사람 경로에 새 게이트를 두지 않는다.
        #
        # adgroup_id=None(캠페인 레벨 액션 — 예산 등)은 in_scope_now가 False로 떨어뜨린다:
        # 예산은 광고그룹으로 귀속이 원리적으로 불가능해, 스코프를 쓰는 동안 이 레버가 열려
        # 있으면 스코프 «밖» 그룹의 노출까지 같이 움직여 경계가 통째로 뚫린다.
        from app.services.naver_ad import adgroup_scope as _adgroup_scope

        if _adgroup_scope.blocked_by_scope(db, proposal.campaign_id, proposal.adgroup_id):
            log.warning(
                "naver_execution_harness: 스코프 밖 — proposal_id=%s(approval_source=%s, "
                "campaign_id=%s adgroup_id=%s) 실행 거부(쓰기·change_log 없음, D-NAO-244)",
                proposal.id, proposal.approval_source, proposal.campaign_id, proposal.adgroup_id,
            )
            # 킬스위치 가드와 동일한 fail-open 일기(인자 평가까지 try 안 — 독립 리뷰 P2-1 관례).
            try:
                diary.write_diary_entry(
                    db, "blocked", proposal.campaign_id,
                    actor=diary.actor_from_approval_source(proposal.approval_source),
                    target_type=proposal.target_type, target_id=proposal.target_id,
                    adgroup_id=proposal.adgroup_id, action=log_action,
                    rationale=(
                        "자동운영 스코프 밖(쓰기 직전 재확인)"
                        if proposal.adgroup_id is not None
                        else "스코프 캠페인의 캠페인 레벨 액션 — 그룹 귀속 불가로 hold"
                    ),
                    now=now,
                )
            except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
                log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
            raise ScopeGuardError(
                f"proposal_id={proposal.id} campaign_id={proposal.campaign_id} "
                f"adgroup_id={proposal.adgroup_id} — 자동운영 스코프 밖(D-NAO-244)"
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
                adgroup_id=proposal.adgroup_id, action=log_action,
                before_value=log_entry.before_value, after_value=log_entry.after_value,
                rationale=proposal.rationale, source_ref=log_entry.id, now=now,
            )
        except Exception as diary_err:  # noqa: BLE001 — fail-open(인자 평가 포함)
            log.warning("naver_execution_harness: diary 기록 실패(fail-open): %s", diary_err)
        return log_entry

    log_entry = NaverChangeLog(
        entity_type=proposal.target_type, entity_id=proposal.target_id,
        campaign_id=proposal.campaign_id, action=log_action,
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
