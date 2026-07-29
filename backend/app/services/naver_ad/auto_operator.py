# auto_operator.py — auto_operator Harness (D-NAO-49, docs/PLAN_naver-ad-auto-operator.md)
# 역할: D-NAO-48 04 자동운영 4조건 정책을 서버로 이관(일 레인, run_daily_lane — 08:50 크론)
#   + 시간당 밴드 관제 실입찰(시간당 레인, run_hourly_lane — 매시 :20 크론). 둘 다
#   auto_operate=True 캠페인(현재 04 하나)만 대상. 로컬 08:55 루틴은 보고·감사 전용으로
#   강등(§0). 예산 변경 불가침(D-NAO-42 Jino 게이트), 03(MOP) 등 타 캠페인 개입 금지. 시간당
#   레인 판정: ★IU(D-NAO-66)로 순위 제한 폐지 — 순위는 목표가 아니라 결과이고, 상향/하향의
#   지배 게이트는 target ROAS(BEP×공격성) 유지 여부다. DOWN=CPC 급등 + RL3 장중 loss 고삐
#   (추정ROAS<BEP), UP=(장중 tally OR 정착창 실측 ROAS≥target) ∧ 예산 여력. 실집행 방어선은
#   여전히 가드레일(BEP 하한·쿨다운 2h·일일상한·스톱로스)이며 이 harness는 방향만 정한다.
#   쓰기는 반드시 naver_execution_harness.execute() 경유(초크포인트 유지, 원칙18-6 —
#   guardrail_gate·naver_sa_writer 직접 쓰기 호출 금지, 이 harness는 SA를 조합만 한다).
#   + D-NAO-58 CD2 클릭 탐침(_probe_trigger): 시간당 레인의 밴드 판정이 hold인 사각지대
#   (imp≥30인데 클릭0·rank 밴드 안/하단)에서 능동적으로 한 등 상향(up)을 제안해 "클릭 살아나는
#   순위"를 실험. 탐침도 기존 가드레일·킬스위치·execute 전량 통과(우회 없음), approval_source=
#   probe_op로 태그(diary probe actor). 되돌림(CD3)·학습(CD4)은 이 파일 범위 밖.
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import and_, exists, func as sqlfunc, select, update
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverKeywordHourly,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import bid_rank_curve, bid_simulator, budget_envelope, budget_pacing, campaign_target_resolver, ctr_alert, ctr_alert_briefing, diagnosis, diary, effective_bid, exploration, expansion_allocator, expansion_pressure, gave_score, guardrail_gate, intraday_roas, naver_execution_harness, naver_sa_writer, rank_servo, slack_notifier, visibility, vitality_signal
from app.services.naver_ad.bid_step_types import BID_UP_TYPES, EXPLORATION_STEP_TYPES, encode_base_bid, encode_exploration_ceiling
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.guardrail_gate import _MAX_CHANGE_PCT
from app.services.naver_ad.trigger_watch import CPC_SPIKE_RATIO
from app.services.naver_sa_ad_fetcher import estimate_average_position_bid, fetch_entity_hh24
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# §3 "정착창 D-8~D-2" — naver_ad_daily 확정치는 D-1까지만(다른 SA와 동일 관례, as_of=D-1)
# 기준 7일 창 [as_of-7, as_of-1] = [오늘-8, 오늘-2]. account_diagnosis.LOW_CLICK_LOOKBACK_DAYS
# (30일, 저클릭 판정 창)와는 별도 상수 — 이 창은 D-NAO-48 정책이 명시한 "정착"(전환귀속
# ~1일 정착, naver-ad-data-cadence 메모리) 전용 짧은 창이며 코드베이스에 기존 구현이 없어
# 이 모듈에서 처음 정의한다(PLAN §3 실코드 대조 — 재사용 대상 없음, 신규 로컬 상수).
_SETTLEMENT_WINDOW_START_DAYS = 8
_SETTLEMENT_WINDOW_END_DAYS = 2

_DAILY_LANE_PROPOSAL_TYPES = ("bid_up", "bid_down", "pause")  # PLAN §3 명시 목록(growth_bid_up 등 제외)

# codex 2R[P1-1]: naver_proposals.approval_source는 String(12) — 'auto_operator'(13자)/
# 'auto_operator_hourly'(20자)는 스키마 계약 위반(SQLite는 무시하지만 PG 이전 시 커밋 실패).
# 스키마 변경 없이 값을 단축(마이그레이션 리스크 0) — 소급채점 레인별 분리 식별자 역할은 유지.
APPROVAL_SOURCE_DAILY = "auto_op"  # 7자
APPROVAL_SOURCE_HOURLY = "auto_op_hr"  # 10자
APPROVAL_SOURCE_PROBE = "probe_op"  # 8자 — D-NAO-58 CD2 클릭 탐침(String(12) 적합, diary probe actor)
APPROVAL_SOURCE_REVERT = "revert_op"  # 9자 — D-NAO-58 CD3 탐침 되돌림(String(12) 적합, diary ACTOR_PROBE 재사용)
# BP(D-NAO-102) 예산 페이싱 승인원 — 값의 집은 budget_pacing SA(harness가 auto_operator를
# module-level import하면 순환이라 SA 쪽이 소유). 여기선 다른 레인 상수와 같은 자리에 재노출만.
APPROVAL_SOURCE_PACING = budget_pacing.APPROVAL_SOURCE_PACING  # 'pace_op'(7자, diary ACTOR_PACING)

# ── VT2(D-NAO-81 B축 스파이럴 복원) ──
# 스파이럴 복원 발사는 새 approval_source·새 쓰기 경로를 만들지 않는다(§0 3 "새 권한 없음"):
# 기존 시간당 밴드 UP 경로(APPROVAL_SOURCE_HOURLY·proposal_type='bid_up'·_clamp_step±15%·
# naver_execution_harness.execute)를 그대로 태우고 rationale 접두 [스파이럴복원]로만 구분한다.
# BEP 가드레일·킬스위치·쿨다운은 전부 harness가 최종 차단(우회 신규 경로 없음).
VITALITY_RATIONALE_PREFIX = "[스파이럴복원]"
_VITALITY_DAILY_CAP = 5            # §2 봉투: 캠페인당 복원 발사 그룹 ≤5/일
_VITALITY_COOLDOWN_HOURS = 48      # §2 봉투: 같은 그룹 48h 재발사 쿨다운
# C5(codex 1R): 당일 순위 재확인 밴드 상단 — vitality_signal._RANK_BAND_TOP(4.0)과 동일.
# D-1 스파이럴 신호로 발사 큐에 올랐어도, 당일 intraday 순위가 밴드(≤4.0)로 복귀했으면 skip.
_VITALITY_INTRADAY_BAND_TOP = 4.0
ACTION_VITALITY_BRIEFING = "vitality_spiral_briefing"  # diary observe action(브리핑 렌더용)

# ── VT3(D-NAO-82② 소재 CTR 경보) ──
# 새 권한 없음(§0 "브리핑+래더 중지뿐") — 실행 레버는 그대로, 브리핑 1개 + 탐색 래더 skip 1개.
ACTION_CTR_ALERT_BRIEFING = "ctr_alert_briefing"  # diary observe action(브리핑 렌더용)
# D-NAO-103: 브리핑 문안·압축·발화 억제는 ctr_alert_briefing(harness)으로 이관했다
# (구 _dedupe_ctr_alert_rows/_fmt_ctr_alert_rows/_CTR_ALERT_BRIEFING_TOP_N 폐기).
_CTR_ALERT_LADDER_SKIP_REASON = "CTR경보 — 소재 처방 대상, 추가 UP 무의미"

# ── VF(D-NAO-83 가시성 우선) 유령∧창 비활성 관측(VT3b 최소형) ──
# 새 권한 없음(§0.5 "관측 라인만·실쓰기 0") — 일 레인 diary observe 1개(경보 채널 규칙: diary만).
ACTION_GHOST_VISIBILITY_BRIEFING = "ghost_visibility_briefing"  # diary observe action(관측 렌더용)
_GHOST_VISIBILITY_BRIEFING_TOP_N = 20

# B3(D-NAO-65) 소재-레벨 입찰 제어 카나리 게이트. auto_operate 캠페인 중 이 집합에 든
# 캠페인만 레버 미연결(source='ad') 그룹에서 ad-레벨 실쓰기(update_ad_bid)로 라우팅한다 —
# 나머지는 기존 [레버 미연결] hold(B2 억제) 유지. ★기본 빈 집합 = 전면 hold(현행 동작 보존,
# 배포 즉시 행위 변화 0). 개방은 운영 결정으로 이 상수에 캠페인 id를 채운다(마이그레이션 0 —
# 코드 배포로만 변경, D-NAO-16 개방 순서 관례·OPEN_ACTIONS와 동형). §0 "개별 캠페인 하드코딩
# 금지"의 유일 예외 자리(카나리 상수) — 산출 규칙 자체는 전역(source='ad' 판별)이고, 이 상수는
# "어느 캠페인에 먼저 개방하느냐"의 카나리 스코프만 좁힌다.
AD_BID_CANARY_CAMPAIGNS: frozenset[str] = frozenset({
    # 카나리 1호 = 맥세이프카드케이스_쇼검 (Jino 확정 2026-07-20 "카나리는 맥세이프로 열자")
    "cmp-a001-02-000000010769985",
})

# B3 GATE P2-2: 카나리 1단계 개방 = **bid_down만**(안전방향 — 지출·노출 하향). ad UP은
# 카나리 2단계(1단계 DOWN 실적 확인 후 상수 확장으로 개방). 탐침 UP은 방향과 별개로
# CD3 되돌림 기계가 'ad' grain을 처리 못 해(_standing_probes의 before_value 최상위 bidAmt
# 파싱 — ad는 adAttr JSON 문자열 중첩·_conv_direct_today grain 필터 부재) 별도 페이즈로 이월.
# ★이름 정정(IU-R R0, codex R3 P2-1): 값은 direction이 아니라 proposal_type("bid_down")이다 —
# 이름을 _AD_BID_CANARY_PROPOSAL_TYPES로 정정(값·판별 로직 불변 = 행위 불변, rename만).
#
# ★D-NAO-129(Jino 지시 2026-07-29 저녁 *"올리는것도 해줘"*): **성과 상향(bid_up) 개방** —
#   카나리 2단계. D-NAO-125가 하향을 연 그날, 시스템이 "브레이크만 자동이고 액셀은 반쪽"인
#   상태가 됐다. 손실은 자동으로 깎이는데 이익 구간에서 볼륨을 늘리는 손이 없으면 D-NAO-59
#   (총이익 최대화)가 아니라 ROAS 방어로 표류한다 — EX 스프린트가 잡았던 "RoAS +7%인데
#   매출 −52%"와 같은 방향의 실패다.
#   ★열리는 타입이 `bid_up`인 것이 안전의 핵심이다: 소재 UP 라우팅은 rank-step 분기
#   (bid_up_servo/bid_up_rank)에 걸리지 않아 레거시 ±15% _clamp_step 폴백을 탄다. 즉 이번에
#   열리는 것은 **±15% 클램프가 걸리는 타입 하나**이고, 클램프 면제 타입들(bid_up_servo·
#   bid_up_rank·bid_up_cold·bid_up_explore)의 (승인원⟺타입) 쌍방향 잠금은 그대로다.
#   그 잠금은 적대 리뷰가 300원→90,000원 구멍을 잡은 자리라 건드리지 않는다.
_AD_BID_CANARY_PROPOSAL_TYPES: frozenset[str] = frozenset({"bid_down", "bid_up"})

# D-NAO-125: 소재-레벨 제안 중 **레인이 인라인 자동 실행**하는 타입. 여기 없는 타입은
# pending으로 남아 콘솔 Confirm을 기다린다. 비우면 종전 "ad 전면 Confirm-only"로 즉시 복귀.
# ★생성 게이트(_AD_BID_CANARY_PROPOSAL_TYPES)와 별개인 이유: 생성은 "카드를 만드는가",
#   이것은 "사람 없이 쏘는가"다. 한쪽만 열면 실쓰기 경계에서 죽는 카드가 Confirm 큐에 쌓이므로
#   두 상수는 항상 같이 움직여야 한다(죽은 카드를 만들지 않는다).
# ★D-NAO-130(Jino 확정 2026-07-29 저녁 *"자동진행으로 만들어"* + *"평가하고 판단하면서
#   수정하면 되지 않을까?"*): **성과 상향도 자동 발사한다.**
#
#   Jino의 근거가 이 시스템의 실제 구조다 — 올리고, 실측하고, 손실이면 **자동 하향이 깎는다**.
#   되먹임 루프가 이미 시간당으로 돌고 있으므로 상향만 사람 손에 묶어둘 이유가 없다.
#   ★그래서 루프의 **교정 단계가 막히면 안 된다**: UP과 DOWN이 같은 쿨다운 시계를 쓰면 되돌리는
#     손이 최대 2시간 묶인다(codex 적대 2R 지적). "직전 변경이 우리 자동 상향이면 하향은 쿨다운
#     면제"를 같이 열었다(guardrail_gate._check_cooldown_and_cap) — 교정을 막는 브레이크는
#     브레이크가 아니라 고장이다.
#   ★codex 적대 4R의 근본 P1(외부 변경이 우리 쓰기에 가려져 영구 소실 → 기준점이 옛 값에 머물러
#     대행사 하향을 되돌려 올림)은 **쓰기 직전 외부변경 확인**으로 닫았다: 하루 1회 탐지에
#     의존하지 않고, 매 판정마다 live editTm을 우리가 아는 editTm 집합과 대조해 다르면 기준점을
#     현재 실측값으로 즉시 재설정하고 그 사건을 관측 테이블에 남긴다
#     (naver_execution_harness._external_touch_since_last_known).
#
#   상향에 걸리는 가드(하향보다 많다): ±15% 클램프 · BEP 미달 증액 금지(D-NAO-1) · 스톱로스 ·
#   일예산 상한 · 쿨다운 2h · 일일 3회(DL3 면제 아님) · 기준가 2배 누적 상한 · 레인당 5건 ·
#   킬스위치 2중 · 쓰기 CAS(판정 기준가와 어긋나면 PUT 안 함).
#   ★남은 한계(정직): BEP는 여전히 **부모 그룹 30일 집계**라 소재 단위 경제 신호가 아니다.
#     소재별 전환·매출 귀속이 생기면 가격 규칙(기준가 2배)이라는 대리 지표를 그것으로 교체한다.
#   되돌리려면 이 집합에서 "bid_up"을 빼면 Confirm 큐로 즉시 복귀한다.
_AD_AUTO_EXEC_PROPOSAL_TYPES: frozenset[str] = frozenset({"bid_down", "bid_up"})

# D-NAO-125 codex[P1]: 레인 1회차당 소재 자동 실행 상한(계정 전체 합). 초과분은 Confirm 대기로
# 강등된다(드롭 아님). 5인 이유: 2h 쿨다운·시간당 레인이므로 활동시간 16h면 하루 최대 ~40건이
# 흘러가는데, 그건 256소재 규모에서 "며칠에 걸쳐 수렴"이라 규칙이 틀렸을 때 되돌릴 시간이 남는다.
_MAX_AD_AUTO_EXEC_PER_LANE = 5


def _ad_auto_exec(proposal_type: str) -> bool:
    """이 소재 제안을 레인이 인라인 자동 실행하는가.

    ★킬스위치를 여기서도 본다(테스트 정리 중 발견): 두 게이트가 독립 축이면
    AD_BID_ROUTING_ENABLED=False로 내려도 카나리 상수에 남은 캠페인의 소재 하향은 계속
    자동 실행돼 **"되돌리는 스위치가 완전히 되돌리지 않는" 상태**가 된다(현재는 맥세이프에
    ad-레버 유닛이 없어 도달 불가능하지만, 롤백 보장은 도달 가능성과 무관하게 성립해야
    한다 — 사고 났을 때 한 줄로 원복된다는 믿음이 이 스위치의 존재 이유다).
    """
    return AD_BID_ROUTING_ENABLED and proposal_type in _AD_AUTO_EXEC_PROPOSAL_TYPES


# D-NAO-125(Jino 확정 2026-07-29) — 소재-레벨 **제안 생성** 스코프를 카나리 상수에서
# 떼어내 auto_operate에 위임한다. Jino 원문: *"적용되는 건 우리가 MOP에서 관리하는
# 캠페인만 적용하면 되잖아?"*
#
# ★왜 상수를 채우지 않고 분리했는가(이게 이 변경의 핵심): AD_BID_CANARY_CAMPAIGNS는 한
#   이름으로 **정반대 두 의미**를 겸하고 있었다 —
#     · auto_operator/proposal_writer: 집합에 들면 소재 제안 생성 **허용**(개방)
#     · delegation_gate·expert_briefing_builder: 집합에 들면 위임·브리핑에서 **제외**(제한)
#   그래서 D-NAO-70②("쓰기 카나리를 모든 캠페인으로 확대해", 2026-07-21)를 상수 채우기로
#   이행하면 **모든 캠페인이 위임 자동승인에서 빠져 계정 전체 자동 실행이 죽는다.** 07-21
#   적대 리뷰가 P2로 "생성 게이트를 D-NAO-70②에 맞춰 정리하라"고 한 것이 바로 이 뜻인데,
#   8일간 미이행돼 4캠페인·92그룹·256소재의 상·하향이 전부 [레버 미연결] hold로 죽어 있었다
#   (2026-07-29 실측: 7일 홀드 78건 = UP 59 + DOWN 19).
#
# ★스코프가 이중으로 이미 걸린다: 이 판정에 도달하는 캠페인은 run_hourly_lane의
#   _auto_operate_campaign_ids(레인 시작 스냅샷) + 실행 직전 _auto_operate_now(독립 커넥션
#   재확인) 둘을 통과한 것뿐이다. 캠페인 목록을 코드에 또 적는 것은 중복이자, 새 캠페인을
#   인수할 때마다 상수를 고쳐야 하는 구멍이다(오늘 사고의 직접 원인 — 07:37 인수 시
#   상수 갱신 누락).
# ★AD_BID_CANARY_CAMPAIGNS는 **건드리지 않는다** — 그 상수의 Confirm-only 의미
#   (delegation_gate·expert_briefing_builder)는 그대로 유지된다.
AD_BID_ROUTING_ENABLED: bool = True  # 킬스위치 — False면 종전 카나리 전면 hold로 즉시 복귀


def _ad_bid_canary(campaign_id: str) -> bool:
    """이 캠페인에서 소재-레벨 제안 생성을 개방하는지.

    스코프 판정(우리가 관리하는 캠페인인가)은 상위 auto_operate 게이트가 이미 두 번 했으므로
    여기서는 킬스위치만 본다. 킬스위치를 내리면 종전 카나리 집합으로 되돌아간다.
    """
    if AD_BID_ROUTING_ENABLED:
        return True
    return campaign_id in AD_BID_CANARY_CAMPAIGNS
_MIN_CLICK_FOR_APPROVAL = 10  # D-NAO-48 조건②(rationale 창 클릭) / §4-1 핫셋 클릭 게이트 공유
_MIN_HOURLY_SAMPLE_IMP = 30  # §4-2 "imp 합 < 30이면 그 시간대 묶음은 판단 보류"
# _HOURLY_RANK_DOWN_THRESHOLD: 과열밴드 DOWN(<2.5)은 IU2(D-NAO-66)로 폐지 — 순위는 목표가
# 아니라 결과이므로 순위만으로 강제 하향하지 않는다(상단 순위의 이상 지출은 CPC 급등 DOWN +
# RL3 loss 고삐가 담당). 이 상수는 탐침(_probe_trigger·_learned_optimal_skip의 밴드 프라이어)
# 에서 계속 소비하므로 유지(삭제 금지) — 탐침의 "밴드 안/하단이라 올릴 여지 있음" 경계값.
_HOURLY_RANK_DOWN_THRESHOLD = Decimal("2.5")  # 탐침(CD2/CD5) 밴드 프라이어 경계 — ROAS-UP 캡 아님(D-NAO-66)
# _HOURLY_RANK_UP_THRESHOLD: 과거 UP 전제(weighted_rank>4.0 = 밴드하단이탈일 때만 UP)였으나
# IU1(D-NAO-66)로 폐지 — UP은 순위 무관, target ROAS 유지 여부만이 지배 게이트. 상수 자체는
# 관찰 프라이어(스팟 밴드 하단 = 밴드 재시작 여지)로 보존하되 UP 판정에는 더 이상 쓰지 않는다.
_HOURLY_RANK_UP_THRESHOLD = Decimal("4.0")  # (D-NAO-66 UP 게이트에서 은퇴 — 관찰 프라이어로만 보존)
# IU1(D-NAO-66) 장중 tally UP 게이트 — 오늘 hh24 누적 신호로 상향(정착창 실측 UP과 OR).
_INTRADAY_UP_MIN_CONV = 2  # 장중 직접전환 tally 최소치(추정치 과신 방지 — 표본 하한)
_INTRADAY_UP_MARGIN = Decimal("1.2")  # 추정 ROAS는 전환지연으로 과소추정되지만, 여유계수로 과상향 폭주 방지
# GATE P2-A-2: 정산 판정불가(unknown) 유닛의 장중-단독 근거 UP은 일 1스텝(+15%/일)로 제한 —
# ccnt 과대귀속 가능성 × 정산 지연(최대 D-2 창)이 겹치면 검증 안 된 추정만으로 하루 여러
# 스텝(최대 8) 오르는 폭주 경로가 열린다. 다음날 정산이 따라오면 veto(below) 또는 승인(ok)으로
# 해소된다. settle_ok 근거 UP은 이 캡 미적용(기존 가드레일 일일상한 3 유지).
_INTRADAY_ONLY_UP_DAILY_CAP = 1
_HOURLY_RECENT_HOURS = 3  # §4-2 "최근 3개 완료 시간대"
_HOURLY_SPEND_BREAKER_MULTIPLE = 3  # §4-6 "직전 7일 일평균 ×3"
_HOURLY_BASELINE_DAYS = 7  # 소진 서킷브레이커 직전 7일

# IU-R R1(D-NAO-67) 서보 예산 pace 사전체크(codex R3 P2-3): 큰 스텝은 guardrail의 사후
# 소진 가드(cost_today≥daily_budget)만으론 부족 → "잔여시간 예상 지출 ≤ 잔여예산×안전계수"를
# 서보 스텝에 사전 체크한다. ★안전계수는 **보수 방향 < 1**로 못박음(여유계수 1.2류 오독 방지) —
# 잔여예산의 80%까지만 예상 지출을 허용(외삽 오차 마진). daily_budget=0(uncapped)이면 통과.
_BUDGET_HEADROOM_SAFETY_RATIO = Decimal("0.8")

# ── IU-R R2(D-NAO-67 원리③) 파워링크 estimate 직행 상수 ──
# §난제4 estimate 호출 캡(보수 초기값 — canary 실측 후 확정, §실측5). 실제 스텝할 유닛
# (ROAS 게이트 통과·쿨다운/일일캡 prefilter 통과·데드밴드 밖)에만 호출하고 런 내
# (kw_id,position) 캐시로 중복 호출을 없앤다 — 그럼에도 호출량 상한을 둔다.
# ★의미 = **per-run 캡**(codex R2 P2 — 정직한 명명): counter가 run_hourly_lane 실행마다
# 리셋되므로 같은 시간대 재시도/수동 실행이 겹치면 시간당 총량은 이 값을 넘을 수 있다.
# 시간당 총량 계약이 필요해지면 DB 시간 버킷 카운터로 승격(canary 실측 후 판단).
_RUN_ESTIMATE_BUDGET = 50
# estimate/average-position-bid의 유효 position 범위(fetcher 실측: 1~4만 유효, 그 밖은 400).
_ESTIMATE_POSITION_MIN = 1
_ESTIMATE_POSITION_MAX = 4
# 네이버 SA 유효 입찰가 규격(bid_simulator._MIN_BID/_MAX_BID/_BID_INCREMENT·rank_servo와 동일 —
# 라이브검증 2026-07-07: 70~100,000원·10원 단위만 유효). estimate rank_bid 이상값 판별에 사용.
_VALID_BID_MIN = 70
_VALID_BID_MAX = 100_000
_BID_INCREMENT = 10

# ── D-NAO-58 CD2 클릭 탐침 상수(D-58-7 확정, 기존 검증 상수 재사용 원칙 — 새 매직넘버 최소화) ──
_PROBE_ZERO_CLICK_HOURS = 2  # 완료 시간대 클릭0 지속 창(Jino 3h→2h 단축, "민첩한 시장 대응")
# 실시간 안전판 손실배수 — 정착창 시간당평균 ×N ∧ 즉시구매 0 → 즉시 원위치(CD3가 소비).
# CD2는 이 상수만 단일 소스로 정의하고 되돌림 로직은 구현하지 않는다(스코프 밖).
_PROBE_BLEED_COST_MULTIPLE = _HOURLY_SPEND_BREAKER_MULTIPLE  # =3, 소진 서킷브레이커 배수 재사용

# rationale에 이미 병기된 "clk=N" 추출 정규식 — proposal_writer._bid_proposal/_growth_proposal이
# 공유하는 포맷("... clk={n} ..." 또는 "... clk={n} (저클릭 표본) ...", 둘 다 뒤에 숫자 아닌
# 문자가 와서 \d+가 정확히 멈춘다).
_RATIONALE_CLK_RE = re.compile(r"clk=(\d+)")

# retro_snapshotter._BOARDS의 down 방향 보드(bleeding_keywords=keyword, shopping_group_bep=adgroup)
# — D-NAO-48 조건④ "최신 소급채점에서 해당 그룹 bleeding 아님" 판정에 쓸 보드명.
_BLEEDING_BOARD_BY_TARGET_TYPE = {"keyword": "bleeding_keywords", "adgroup": "shopping_group_bep"}


def _settlement_window(today: date) -> tuple[date, date]:
    """정착창 [오늘-8, 오늘-2] — 일 레인 조건③(+시간당 레인 §4가 이 함수를 그대로 재사용)."""
    return (
        today - timedelta(days=_SETTLEMENT_WINDOW_START_DAYS),
        today - timedelta(days=_SETTLEMENT_WINDOW_END_DAYS),
    )


def _day_bounds_utc(today: date) -> tuple[datetime, datetime]:
    """KST 달력일 today의 UTC 경계 [start, end) — NaverProposal.created_at은
    server_default=func.now()로 UTC 저장([[sqlite-server-default-now-is-utc]] 교훈) —
    proposal_writer.account_brief_singleton과 동일 변환 패턴 재사용."""
    start = datetime.combine(today, datetime.min.time()) - timedelta(hours=9)
    return start, start + timedelta(days=1)


def _auto_operate_campaign_ids(db: Session) -> set[str]:
    rows = db.query(NaverCampaignSettings.campaign_id).filter(
        NaverCampaignSettings.auto_operate.is_(True)
    ).all()
    return {r[0] for r in rows}


def _record_blocked(
    db: Session, *, campaign_id: str, actor: str, reason: str, now: datetime,
    target_type: str | None = None, target_id: str | None = None,
    adgroup_id: str | None = None, action: str | None = None,
    event_type: str = "blocked",
) -> None:
    """레인 고유 hold 1건을 운영 일기(blocked)로 기록(D-NAO-54 P1). diary.write_diary_entry가
    fail-open이라 별도 try 불필요 — 일기 실패가 레인 집행/hold를 막지 않는다. 실집행/가드레일
    차단/킬스위치는 harness가 기록하므로(이중 기록 금지), 이 함수는 레인이 harness로 넘기지
    않고 자체 hold한 이벤트만 남긴다.

    ★기록 대상 선별(소음 차단): "의도된 액션이 차단된 것"만 기록한다 — 시간당 레인의 일상
    관찰(판정 hold=밴드 정상, 당일 imp 없음, intraday skip)은 액션 의도 자체가 없었으므로
    기록하지 않는다(핫셋×매시 hold를 전부 적으면 일 수백 행 소음 → P3 후보 채굴 오염).

    킬스위치 사유 hold는 event_type="kill_switch"로 기록(독립 리뷰 P3-1: harness의 writer측
    거부와 같은 원인이 두 타입에 분산되면 P3 채굴이 kill_switch 빈도를 과소집계한다)."""
    diary.write_diary_entry(
        db, event_type, campaign_id, actor=actor, target_type=target_type,
        target_id=target_id, adgroup_id=adgroup_id, action=action, rationale=reason, now=now,
    )


def _auto_operate_now(db: Session, campaign_id: str) -> bool:
    """킬스위치 실행 직전 재확인(codex 5R[P1-2]) — 레인 시작 시 1회 스냅샷만 믿으면 실행
    도중 Jino가 OFF("04 자동운영 중지") 해도 남은 실입찰이 진행된다(문서화된 "즉시 정지"
    계약 위반). 행 부재도 False(fail-closed).

    codex 6R[P1]: 세션 경유 컬럼 조회(초판)는 결국 같은 Session 트랜잭션 안에서 실행된다 —
    SQLite(WAL)에서 리더는 트랜잭션 시작 시점 스냅샷을 보므로, 레인이 조기 쿼리로 읽기
    트랜잭션을 연 뒤 타 프로세스가 auto_operate=0을 커밋해도 이 세션엔 안 보였다(첫 승인
    커밋 전 구간에서 stale True → 실입찰 1건 진행 가능). **엔진 레벨 독립 커넥션**으로
    조회한다 — 세션 트랜잭션과 무관한 새 트랜잭션이라 타 프로세스 커밋이 항상 보이고,
    세션 상태를 오염시키지 않는다(commit/rollback 사이드이펙트 없음)."""
    with db.get_bind().connect() as conn:
        row = conn.execute(
            select(NaverCampaignSettings.auto_operate).where(
                NaverCampaignSettings.campaign_id == campaign_id
            )
        ).first()
    return bool(row and row[0])


def _extract_rationale_clk(rationale: str | None) -> int | None:
    """D-NAO-48 조건②("rationale 창 클릭") 추출 — 로컬 루틴(사람/Claude가 rationale
    텍스트를 읽고 판단)과 동일 신호를 그대로 재현한다. target_bid처럼 구조화 컬럼이
    없는 값이라(clk은 NaverProposal 컬럼이 아님) 이 필드만 예외적으로 rationale에서 읽는다
    — proposal_writer의 target_bid 텍스트파싱금지 원칙(실행 대상 결정 필드)과는 다른
    경계다(이건 사후 재검증용 보조 신호일 뿐 실행 방향/금액을 결정하지 않는다)."""
    if not rationale:
        return None
    m = _RATIONALE_CLK_RE.search(rationale)
    return int(m.group(1)) if m else None


def _live_current_bid(target_type: str, target_id: str) -> int | None:
    """라이브 현재 입찰가 재조회(naver_execution_harness._build_guardrail_context의 동일
    패턴 — get_keyword/_get_adgroup 재사용). 실패는 fail-closed(None)."""
    try:
        if target_type == "keyword":
            live = naver_sa_writer.get_keyword(target_id)
        elif target_type == "adgroup":
            live = naver_sa_writer._get_adgroup(target_id)
        else:
            return None
        return live.get("bidAmt")
    except Exception as e:  # noqa: BLE001 — 재조회 실패는 fail-closed(None 유지)
        log.warning("auto_operator: 라이브 현재가 재조회 실패 target_type=%s target=%s: %s",
                    target_type, target_id, e)
        return None


def _resolve_target_roas(db: Session, campaign_id: str) -> float | None:
    """override>계정기본값 목표ROAS(naver_execution_harness._resolve_target_roas_float와
    동일 로직 — private 헬퍼 재사용 대신 이 모듈 내부에서 독립 구현해 harness 내부구현
    변경에 결합되지 않게 한다)."""
    resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
    target_roas = resolved["target_roas"]
    if target_roas is None:
        target_roas = campaign_target_resolver.account_default_target_roas(db)
    return float(target_roas) if target_roas is not None else None


def _settlement_agg(db: Session, target_type: str, target_id: str, date_from: date, date_to: date) -> dict:
    """정착창 내 (clk, cost, conv_amt) 집계 — account_diagnosis.keyword_window_agg/
    adgroup_window_agg는 clk을 반환하지 않아(cost/conv_amt만, D-NAO-48 조건③·시간당 CPC/
    페이싱 판정엔 clk도 필요) 이 모듈 전용 로컬 집계를 둔다. 기존 SA(account_diagnosis)
    수정 금지 원칙에 따라 새 함수를 거기 추가하지 않고 이 파일 안에 격리한다."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if target_type == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id, NaverAdDaily.campaign_type == "WEB_SITE")
    else:  # adgroup
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    clk, cost, direct, indirect = q.one()
    return {"clk": int(clk), "cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def _settlement_roas_status(
    db: Session, target_type: str, target_id: str, campaign_id: str, today: date,
) -> tuple[str, str]:
    """D-NAO-48 조건③(그룹 보정ROAS(정착창 D-8~D-2) ≥ target_roas)의 3상 판정
    (GATE P2-A-1, D-NAO-66) — (status, reason) 반환, status ∈:
      "ok"      = 데이터 충분 ∧ 보정ROAS ≥ target (검증된 합격)
      "below"   = 데이터 충분 ∧ 보정ROAS < target (**명시적 미달** — 정산이 나쁘다고 실측으로
                  말하는 상태. 장중 추정 UP의 거부권(veto) 근거로 쓰인다)
      "unknown" = 데이터 불충분(실적 없음/보정계수 unavailable/target 해석 불가 — 판정 불가)
    "below"와 "unknown"의 구분이 P2-A 핵심: 종전 bool은 둘을 뭉개 "정산이 명시적으로 나쁨"을
    "모름"과 동일 취급했다 — 정산 미달인데 장중 추정(ccnt 과대귀속 가능)만으로 올리는 구멍.

    codex 5R[P1-1]: correction_factor()는 실주문 매출 부재 시 factor=1·source='unavailable'
    을 반환한다(no-op 보정 폴백) — 그걸 검증된 보정처럼 쓰면 데이터 정전 시 무보정
    convAmt/cost로 bid_up(일 레인)·시간당 UP이 승인된다. source가 'actual_revenue_ratio'
    (실측 비율)가 아니면 ROAS 검증 불가 = "unknown". DOWN 경로는 이 함수를 타지 않아
    영향 없음(안전 방향, 보정 불요)."""
    window_from, window_to = _settlement_window(today)
    agg = _settlement_agg(db, target_type, target_id, window_from, window_to)
    if agg["cost"] <= 0:
        return "unknown", f"정착창({window_from.isoformat()}~{window_to.isoformat()}) 실적 없음 — 보정ROAS 검증 불가(fail-closed)"
    factor_info = diagnosis.correction_factor(db, today - timedelta(days=1))
    if factor_info.get("source") != "actual_revenue_ratio":
        return "unknown", (
            f"보정계수 unavailable(source={factor_info.get('source')!r}) — 실주문 매출 부재, "
            "보정ROAS 검증 불가(fail-closed, codex 5R[P1-1])"
        )
    factor = factor_info["factor"]
    roas_corrected = (agg["conv_amt"] / agg["cost"]) * float(factor)
    target_roas = _resolve_target_roas(db, campaign_id)
    if target_roas is None:
        return "unknown", "target_roas 해석 불가(계정 기본값 없음) — 검증 불가(fail-closed)"
    if roas_corrected < target_roas:
        return "below", f"정착창 보정ROAS {roas_corrected:.4f} < 목표 {target_roas}"
    return "ok", f"정착창 보정ROAS {roas_corrected:.4f} >= 목표 {target_roas}"


def _settlement_roas_ok(
    db: Session, target_type: str, target_id: str, campaign_id: str, today: date,
) -> tuple[bool, str]:
    """기존 bool 인터페이스 보존 래퍼(일 레인 _check_bid_up_conditions 등 기존 호출처 회귀 0)
    — ok만 True, below/unknown은 둘 다 False(종전과 동일 fail-closed 의미). 3상 구분이 필요한
    시간당 UP 경로는 _settlement_roas_status를 직접 쓴다(GATE P2-A-1)."""
    status, reason = _settlement_roas_status(db, target_type, target_id, campaign_id, today)
    return status == "ok", reason


def _entity_status_hold_reason(db: Session, target_type: str, target_id: str) -> str | None:
    """일 레인 공통 사전 가드(2026-07-21 실사고) — 타깃 엔티티가 naver_entity에서 status!='on'
    (off/deleted)이면 hold 사유 반환, 아니면 None.

    사고 경위: shopping_group_bep 보드는 NaverAdDaily 집계만으로 후보를 뽑아, 창 안에 지출이
    남은 deleted 그룹(맥세이프 69087677/69089452)에도 bid_down 제안이 생성됨 → 일 레인
    무조건 승인 → harness에서 네이버 API 404(current_bid 미확보 fail-closed) 매일 반복
    (change_log 181·183). 실행 불가 타깃은 심사 단계에서 사유와 함께 hold → 레인 말미
    sweep이 rejected 처리(codex 11R 일일 재생성 사이클과 동일 수명).

    entity 행이 없으면 통과(None) — 기존 동작 보존. naver_entity는 keyword를 WEB_SITE만
    동기화하는 등 커버리지 경계가 있어, '행 부재'를 fail-closed로 확대하면 정상 타깃까지
    막을 수 있다(deleted는 물리 삭제 없이 행이 남으므로 이 사고 계열은 행 존재가 보장됨)."""
    row = db.query(NaverEntity.status).filter(
        NaverEntity.entity_type == target_type,
        NaverEntity.entity_id == target_id,
    ).first()
    if row is not None and row[0] != "on":
        return (
            f"타깃 엔티티 status={row[0]!r}(≠on) — 실행 불가 대상 사전 제외"
            "(deleted면 네이버 API 404행, 2026-07-21 실사고)"
        )
    return None


def _bleeding_hold_reason(db: Session, target_type: str, target_id: str, today: date) -> str | None:
    """D-NAO-48 조건④(최신 소급채점에서 bleeding 아님) 판정 — 미충족 시 hold 사유, 통과 시
    None. retro_snapshotter._BOARDS가 매일 08:30 board별로 스냅샷하는 NaverRetroSignal의
    최신 asof_date 행에 이 target_id가 해당 bleeding 보드(board)로 존재하면 bleeding.

    fail-closed 계열(전부 hold):
    - 알 수 없는 grain(board 매핑 없음) — 판정 불가.
    - 소급채점 데이터 전무(latest_asof None) — 검증 근거 없음.
    - codex 4R[P1] asof 신선도: 일 레인(08:50) 기준 기대 as-of = 오늘-1(08:30 retro가
      매일 어제 as-of를 생성). latest_asof < 기대면 당일 retro 크론이 실패한 것 — 과거
      성적표에서의 "부재"를 "bleeding 아님"으로 해석하면 stale 데이터로 bid_up이 자동
      실행된다(fail-open). stale이면 존재 여부와 무관하게 hold.

    최신(신선한) 스냅샷은 있는데 이 target_id가 그 보드에 없으면 "그날 안 걸림" = 실제
    not-bleeding 신호 — 통과(None)."""
    board = _BLEEDING_BOARD_BY_TARGET_TYPE.get(target_type)
    if board is None:
        return f"④grain={target_type!r} 판정 불가(bleeding 보드 매핑 없음, fail-closed)"
    latest_asof = db.query(sqlfunc.max(NaverRetroSignal.asof_date)).scalar()
    if latest_asof is None:
        return "④소급채점 데이터 없음 — bleeding 검증 불가(fail-closed)"
    expected_asof = today - timedelta(days=1)
    if latest_asof < expected_asof:
        return (
            f"④소급채점 stale — latest_asof={latest_asof.isoformat()} < 기대 "
            f"{expected_asof.isoformat()}(당일 retro 미완주, fail-closed, codex 4R[P1])"
        )
    exists = db.query(NaverRetroSignal.id).filter(
        NaverRetroSignal.asof_date == latest_asof,
        NaverRetroSignal.board == board,
        NaverRetroSignal.target_id == target_id,
    ).first()
    if exists is not None:
        return "④최신 소급채점에서 bleeding으로 판정됨"
    return None


def _has_recent_external_stop(db: Session, target_type: str, target_id: str) -> bool:
    """D-NAO-40: pause 승인 전 "최근 외부/수동 정지 이력 없음" 확인 —
    account_diagnosis.resume_candidates가 쓰는 "최신 lock 변경이 우리 시스템 것인지" 판별과
    동일 원리(그 함수는 이미 정지된 대상 중 우리가 정지시킨 것만 골라내고, 이건 반대로
    "정지하려는 대상을 최근 외부가 이미 건드렸는지"를 본다). 최신 잠금 변경 행의 action이
    external_status_change면 외부 개입 흔적 — True(hold 대상)."""
    last = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.entity_type == target_type,
            NaverChangeLog.entity_id == target_id,
            NaverChangeLog.action.in_(["set_user_lock", "external_status_change"]),
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )
    if last is None:
        return False
    return last.action == "external_status_change"


def _new_ex_review_ctx() -> dict:
    """일 레인 EX 폴백 심사 컨텍스트(D-NAO-85 §4-4) — 캠페인당 압력·배분 1회 캐시 + 프라이어
    lazy 로드 상태. run_daily_lane이 심사 루프 시작 시 1개 생성해 _check_bid_up_conditions에
    넘긴다(반복 호출·재로드 금지)."""
    return {"pressure": {}, "alloc": {}, "priors": None, "priors_loaded": False}


def _ex_campaign_pressure(db: Session, campaign_id: str, today: date, ex_ctx: dict | None) -> dict:
    """EX 캠페인 압력 판정(P3 프라이어 폴백용) — 캠페인당 1회 캐시. expansion_pressure는
    auto_operator를 import하지 않으므로 순환 없음(모듈 상단 import)."""
    cache = ex_ctx["pressure"] if ex_ctx is not None else None
    if cache is not None and campaign_id in cache:
        return cache[campaign_id]
    pressure = expansion_pressure.judge_campaign_pressure(db, campaign_id, today=today)
    if cache is not None:
        cache[campaign_id] = pressure
    return pressure


def _ex_allocation_adgroups(
    db: Session, campaign_id: str, today: date, pressure: dict, ex_ctx: dict | None,
) -> set[str]:
    """★P1 방어 심층(codex 적대 리뷰): [EX확장] 태그를 텍스트로만 믿지 않고, 08:50 심사 시각의
    최신 데이터로 expansion_allocator.allocate_expansion을 캠페인당 1회 재실행해 배분 목록
    (adgroup_id 집합)을 얻는다 — 폴백은 제안의 target_id가 이 목록에 있을 때만 허용한다(위조
    태그 차단 + CTR/tier/cap을 최신 데이터로 재검증하는 이중 효과). response_priors는 폴백
    후보가 실제 존재할 때(이 함수 최초 호출 시) 1회만 lazy 로드한다(N+1·불필요 로드 회피)."""
    cache = ex_ctx["alloc"] if ex_ctx is not None else {}
    if campaign_id in cache:
        return cache[campaign_id]
    if ex_ctx is not None and not ex_ctx.get("priors_loaded"):
        ex_ctx["priors"] = bid_rank_curve.load_response_priors(db)
        ex_ctx["priors_loaded"] = True
    priors = ex_ctx.get("priors") if ex_ctx is not None else None
    allocations = expansion_allocator.allocate_expansion(
        db, campaign_id, today=today, pressure=pressure, response_priors=priors,
    )
    adgroups = {a["adgroup_id"] for a in allocations}
    cache[campaign_id] = adgroups
    return adgroups


def _check_bid_up_conditions(
    db: Session, p: NaverProposal, today: date, *, ex_ctx: dict | None = None,
) -> str | None:
    """D-NAO-48 bid_up 4조건(PLAN §3) — 하나라도 미충족이면 hold 사유 문자열, 전부
    충족이면 None(승인 가능).

    ★EX 멤버십 재검증 필수 게이트(codex D-NAO-89 P1): [EX확장] 접두 bid_up **전건**(clk≥10 표준
    clk_ok 경로 포함·폴백 필요 여부 무관)은 승인 전 08:50 최신 데이터로 allocator를 재실행한 배분
    목록(_ex_allocation_adgroups)에 target_id가 있어야 한다 — 없으면 hold. 이전엔 clk≥10 deep/own
    제안이 표준 clk_ok 경로를 타 이 재검증을 우회했다(과열밴드 deep 제안이 08:10 학습으로 slope
    프라이어 무효화·deep 4조건 붕괴돼도 집행 가능한 구멍). 멤버십 재실행이 deep 4조건·CTR·tier·cap을
    최신 데이터로 자연 재적용하므로 별도 deep 게이트를 중복 구현하지 않는다.

    ★P3 EX 프라이어 폴백(D-NAO-85 §4-4): rationale이 [EX확장] 접두인 bid_up에 한해, 조건②
    (clk≥10) 미달 ∧ 조건③이 'unknown'(표본 부족)일 때 캠페인 압력 판정(expansion_mode)으로 ②③를
    대체 통과시킨다(멤버십 재검증은 위 필수 게이트가 clk_ok 경로와 공통 수행 — 중복 제거). 조건③이
    'below'(명시적 미달)면 폴백 불가(거부권 유지 — DOWN 비대칭 보수성). 조건①(스텝 클램프)·
    ④(bleeding)는 폴백 없음. 비EX bid_up은 4조건 현행 그대로(회귀 0)."""
    if p.target_bid is None:
        return "target_bid 없음 — 구조 결함(재생성 필요)"

    # ①스텝 클램프 정상 — target_bid가 라이브 현재가 대비 ±_MAX_CHANGE_PCT 이내인지 재확인.
    # (harness/guardrail_gate가 실행 직전 다시 검증하지만, 여기서 미리 걸러 실패를 예정된
    # 재시도가 가능한 'pending 유지'로 남긴다 — harness에 넘겨 fail-closed 'failed'로 영구
    # 종결시키지 않기 위함.) ★폴백 없음(EX여도 스텝 클램프는 현행 유지).
    current_bid = _live_current_bid(p.target_type, p.target_id)
    if current_bid is None:
        return "①라이브 현재가 재조회 실패 — 스텝 클램프 검증 불가(fail-closed)"
    if current_bid <= 0:
        return "①라이브 현재가 0 이하 — 검증 불가(fail-closed)"
    change_pct = abs(Decimal(p.target_bid) - Decimal(current_bid)) / Decimal(current_bid)
    if change_pct > _MAX_CHANGE_PCT:
        return (
            f"①스텝 클램프 이탈 — 현재={current_bid}원 목표={p.target_bid}원 "
            f"변경폭={float(change_pct):.1%}(상한 {float(_MAX_CHANGE_PCT):.0%})"
        )

    # ②rationale 창 클릭 ≥10 + ③그룹 보정ROAS(정착창 D-8~D-2) ≥ target_roas.
    clk = _extract_rationale_clk(p.rationale)
    clk_ok = clk is not None and clk >= _MIN_CLICK_FOR_APPROVAL
    is_ex = (p.rationale or "").startswith(expansion_pressure.EX_RATIONALE_PREFIX)

    if clk_ok:
        # 표본 충분 — 표준 ③(ok만 통과). 비EX·EX 공통·회귀 0.
        roas_ok, roas_reason = _settlement_roas_ok(db, p.target_type, p.target_id, p.campaign_id, today)
        if not roas_ok:
            return f"③{roas_reason}"
    else:
        # ② 미달. 비EX는 즉시 hold(회귀 0). EX는 ③이 'unknown'(표본 부족)일 때만 캠페인 프라이어 폴백.
        if not is_ex:
            return f"②rationale 창 클릭 부족(clk={clk})"
        status, roas_reason = _settlement_roas_status(db, p.target_type, p.target_id, p.campaign_id, today)
        if status == "below":
            return f"③{roas_reason}"  # 명시적 미달 — 거부권(폴백 불가, DOWN 비대칭 보수성)
        if status == "ok":
            # ③은 통과했으나 폴백 조건(③ unknown)이 아님 — ② 표준 hold(폴백은 ②미달∧③unknown 교집합만).
            return f"②rationale 창 클릭 부족(clk={clk})"
        # status == "unknown" — 캠페인 프라이어 폴백(캠페인당 1회 캐시). 여기선 폴백 자격(확장 모드)만
        # 확인하고, 멤버십 재검증은 아래 EX 필수 게이트가 clk_ok 경로와 공통으로 수행한다(중복 제거).
        pressure = _ex_campaign_pressure(db, p.campaign_id, today, ex_ctx)
        if not pressure.get("expansion_mode"):
            return (
                f"②③ EX 프라이어 폴백 불가 — 캠페인 확장 모드 아님(clk={clk}, {pressure.get('reason')})"
            )
        # 폴백 자격 통과 → ②③ skip, 아래 EX 멤버십 게이트 → ④로 진행.

    # ★EX 멤버십 재검증 필수 게이트(codex D-NAO-89 P1) — [EX확장] 태그 제안 전건(clk≥10 표준 clk_ok
    # 경로·clk<10 프라이어 폴백 경로 무관)은 승인 전 08:50 최신 데이터로 allocator를 재실행한 배분
    # 목록에 target_id가 있어야 한다. 이전엔 clk≥10 deep/own 제안이 표준 clk_ok 경로를 타 이 재검증을
    # 우회했다(과열밴드 deep 제안이 08:10 학습으로 slope 프라이어 무효화·deep 4조건 붕괴돼도 집행
    # 가능한 구멍). 멤버십 재실행(_ex_allocation_adgroups=allocate_expansion 재실행)이 deep 4조건·CTR·
    # tier·cap을 최신 데이터로 자연 재적용하므로 별도 deep 게이트를 중복 구현하지 않는다. 캠페인당 1회
    # 캐시(ex_ctx)라 폴백 경로가 위에서 이미 pressure를 산출했으면 재실행 비용 0. 비EX 제안은 무접촉.
    if is_ex:
        pressure = _ex_campaign_pressure(db, p.campaign_id, today, ex_ctx)
        alloc_adgroups = _ex_allocation_adgroups(db, p.campaign_id, today, pressure, ex_ctx)
        if p.target_id not in alloc_adgroups:
            return f"EX 멤버십 재검증 실패 — 배분 목록에 없음(target={p.target_id}, clk={clk})"

    # ④최신 소급채점에서 bleeding 아님(asof 신선도 포함 — codex 4R[P1]). ★폴백 없음.
    bleeding_reason = _bleeding_hold_reason(db, p.target_type, p.target_id, today)
    if bleeding_reason:
        return bleeding_reason

    return None


def _run_ctr_alert_briefing(db: Session, now: datetime, result: dict) -> None:
    """VT3(D-NAO-82② → D-NAO-103 개편) 소재 CTR 경보 브리핑 — 일 레인(08:50)에서 전
    auto_operate 캠페인의 ctr_alert(SA)를 캠페인당 1회 호출해 수집하고, 조립·발화 억제는
    ctr_alert_briefing(harness)에 위임한다. 이 함수는 수집·발송·집계만 한다(원칙18 — 레인은
    SA/harness를 엮을 뿐 문안을 만들지 않는다).

    D-NAO-103: 매일 같은 만성 건을 반복 발화하던 것을 "신규 진입만 즉시, 만성은 월요일 요약"
    으로 바꿨다. 발화할 게 없는 날은 여전히 완전 침묵(diary·Slack 둘 다 없음).
    fail-open: 이 스텝의 실패가 일 레인 본작업(승인/실행/stale 정리)을 막지 않는다(bleed
    밸브·vitality 스텝과 동형 — 호출부는 독립 try로 감싸지 않고 이 함수가 직접 감싼다,
    run_daily_lane 말미에서 반환값 없이 호출)."""
    try:
        alerts: list[dict] = []
        for campaign_id in _auto_operate_campaign_ids(db):
            signals = ctr_alert.detect_ctr_alerts(db, campaign_id, now=now)
            alerts.extend(signals.get("alerts", []))
        brief = ctr_alert_briefing.build_briefing(db, alerts, now=now)
        result["ctr_alerts"] = brief["total"]              # 판정된 그룹 수(창 중복 합산 아님)
        result["ctr_alerts_fired"] = brief["fired"]        # 실제 메시지에 실린 그룹 수
        result["ctr_alerts_suppressed"] = brief["suppressed"]  # 만성 반복이라 억제된 수
        text = brief["text"]
        if not text:
            return  # 신규 진입 0(+주간 요약일 아님) = 완전 침묵
        diary.write_diary_entry(
            db, "observe", "", actor=diary.ACTOR_DAILY, action=ACTION_CTR_ALERT_BRIEFING,
            rationale=text, now=now,
        )
        slack_notifier.notify_text(text, log_label="소재 CTR 경보 브리핑")
    except Exception as e:  # noqa: BLE001 — VT3 브리핑 실패는 일 레인 본작업과 분리(fail-open)
        log.warning("auto_operator: CTR 경보 브리핑 실패(fail-open): %s", e)


def _run_ghost_visibility_briefing(db: Session, now: datetime, result: dict) -> None:
    """VF(D-NAO-83 가시성 우선) 유령∧증거창 비활성 관측 — 일 레인(08:50)에서 D-1(어제)
    naver_ad_daily의 SHOPPING 그룹 중 유령 지면(avg_rank>_GHOST_RANK=5)이면서 증거 구매 창이
    비활성(경제성/예산/표본 사유)인 그룹을 diary observe로 관측 기록한다(VT3b 최소형·실쓰기 0).
    Slack 없음(§0.5 "관측 라인만"·경보 채널 규칙 = diary만). 없는 날 완전 침묵.
    fail-open: 이 스텝 실패가 일 레인 본작업(승인/실행/stale 정리)을 막지 않는다(CTR 브리핑 동형).
    _run_ctr_alert_briefing과 완전 독립 — 자기 런에서 D-1 실측으로 파생(시간당 result 미참조)."""
    try:
        auto_ids = _auto_operate_campaign_ids(db)
        if not auto_ids:
            result["ghost_visibility_observed"] = 0
            return
        today = now.date()
        yesterday = today - timedelta(days=1)
        # D-1 SHOPPING 그룹별 노출·순위합(sentinel·그룹 sentinel '' 제외) — 유령 판별 원료.
        rows = (
            db.query(
                NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id,
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.rank_sum), 0),
            )
            .filter(
                NaverAdDaily.ad_date == yesterday,
                NaverAdDaily.campaign_type == "SHOPPING",
                NaverAdDaily.campaign_id.in_(auto_ids),
                NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
                NaverAdDaily.adgroup_id != "",
            )
            .group_by(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id)
            .all()
        )
        observed: list[str] = []
        for camp_id, ag_id, imp_sum, rank_sum in rows:
            imp_sum = int(imp_sum)
            if imp_sum <= 0:
                continue
            avg_rank = Decimal(int(rank_sum)) / Decimal(imp_sum)
            if avg_rank <= visibility._GHOST_RANK:
                continue  # 유령 아님(가시권)
            win = visibility.evidence_window(db, ag_id, camp_id, today)
            if win["active"]:
                continue  # 창 활성 = 증거 구매 진행 중(스텝 허용) — 관측 대상 아님
            observed.append(f"- {camp_id}/{ag_id}(순위 {float(avg_rank):.2f}): {win['reason']}")
        result["ghost_visibility_observed"] = len(observed)
        if not observed:
            return  # 관측 대상 없음 = 침묵
        top = observed[:_GHOST_VISIBILITY_BRIEFING_TOP_N]
        remainder = len(observed) - len(top)
        if remainder > 0:
            top.append(f"- 외 {remainder}건")
        header = (
            f"{today.isoformat()} 유령∧증거창 비활성 관측 {len(observed)}건(D-NAO-83) — "
            "유령 지면(순위>5)에 머무나 증거 구매 창이 열리지 않은 그룹(스텝 금지 상태) — 관측 전용"
        )
        text = "\n".join([header] + top)
        diary.write_diary_entry(
            db, "observe", "", actor=diary.ACTOR_DAILY, action=ACTION_GHOST_VISIBILITY_BRIEFING,
            rationale=text, now=now,
        )
    except Exception as e:  # noqa: BLE001 — VF 관측 실패는 일 레인 본작업과 분리(fail-open)
        log.warning("auto_operator: 유령 가시성 관측 실패(fail-open): %s", e)


def _sweep_precommit_seam(db: Session) -> None:
    """잔존 pending 정리(sweep)의 TOCTOU 창을 결정론적으로 검증하기 위한 테스트 seam
    (codex 12R[P2]). 프로덕션에선 no-op — 프레시 게이트(rollback) 직후·원자 UPDATE 직전에
    호출되므로, 테스트가 이 지점을 patch해 "심사 종료 후~reject 커밋 전" 창 안에서 타
    프로세스가 auto_operate=OFF를 커밋하는 상황을 실주입한다(run_hourly_lane의 fetch_intraday
    주입 seam과 동형 — 실행 흐름에 봉합점을 두어 경합을 재현). no-op이라 프로덕션 경로·성능에
    영향 없음."""
    return None


def _run_budget_envelope_lane(
    db: Session, auto_ids: set[str], day_start: datetime, day_end: datetime,
    now: datetime, result: dict,
) -> None:
    """D-NAO-87 예산 봉투 자동 심사 — [예산봉투] 접두 budget_up만 자동 승인·집행한다. **비태그
    budget_up은 현행 Confirm 전용 불변**: _DAILY_LANE_PROPOSAL_TYPES에 budget_up을 넣지 않고
    이 별도 스코프 쿼리(rationale 접두 필터)로만 처리한다(기존 의미 불변). 게이트: auto_operate ∧
    당일 생성 ∧ 라운드 봉투 자율분(budget_auto_eligible=True — 회당 총 증가 ≤10만 캡 존속, 초과분은
    Confirm 대기 pending 유지) ∧ 킬스위치 재확인 통과 → 승인(APPROVAL_SOURCE_DAILY)·harness.execute
    경유(guardrail _check_budget이 +100%캡·스톱로스·BEP 재검증 — 신규 실쓰기 경로 0). fail-open은
    호출부가 감싼다."""
    if not auto_ids:
        return
    candidates = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.status == "pending",
            NaverProposal.proposal_type == "budget_up",
            NaverProposal.campaign_id.in_(auto_ids),
            # [예산봉투] 접두만(SQLite/PG 공히 '['는 LIKE 리터럴 — %·_만 특수문자).
            NaverProposal.rationale.like(f"{budget_envelope.BUDGET_ENVELOPE_PREFIX}%"),
            # 라운드 봉투 캡 존속: 회당 총 증가 ≤10만 자율분(True)만 자동 집행(초과분 False는
            # Confirm 대기 pending 유지 — _classify_budget_round_envelope 그리디 분류 결과 존중).
            NaverProposal.budget_auto_eligible.is_(True),
            NaverProposal.created_at >= day_start,
            NaverProposal.created_at < day_end,
        )
        .order_by(NaverProposal.id.asc())
        .all()
    )
    # ★P2 KST 당일 1회 게이트(복리 차단, codex 적대 리뷰) — 승인 전 재확인(생성 단계 skip과
    # 이중 게이트). 이번 런에서 성공 집행한 캠페인도 즉시 추가해 같은 런 내 중복도 막는다.
    raised_today = budget_envelope.campaigns_raised_today(db, today=now.date())
    for p in candidates:
        result["budget_reviewed"] += 1
        if p.campaign_id in raised_today:
            hold_reason = (
                "예산 봉투 KST 당일 1회 게이트 — 오늘 이미 봉투 증액 성공 집행됨(복리 차단)"
            )
            result["held"].append({"id": p.id, "reason": hold_reason})
            _record_blocked(
                db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=hold_reason,
                now=now, target_type=p.target_type, target_id=p.target_id,
                adgroup_id=p.adgroup_id, action=p.proposal_type,
            )
            continue
        # 킬스위치 실행 직전 재확인(일 레인 실행형과 동일 계약).
        if not _auto_operate_now(db, p.campaign_id):
            hold_reason = "킬스위치 OFF — auto_operate=False(예산 봉투 실행 직전 재확인)"
            result["held"].append({"id": p.id, "reason": hold_reason})
            _record_blocked(
                db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=hold_reason,
                now=now, target_type=p.target_type, target_id=p.target_id,
                adgroup_id=p.adgroup_id, action=p.proposal_type, event_type="kill_switch",
            )
            continue
        p.status = "approved"
        p.approval_source = APPROVAL_SOURCE_DAILY
        db.commit()
        result["budget_approved"] += 1
        try:
            naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
            result["budget_executed"] += 1
            raised_today.add(p.campaign_id)  # 같은 런 내 복리 차단(성공 집행분 즉시 반영)
        except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
            result["budget_failed"] += 1
            log.warning("auto_operator: 예산 봉투 실행 실패 proposal_id=%s: %s", p.id, e)

    # ★P2 잔존 pending 정리(codex 2R — persist dedup 좌초 방지): [예산봉투] budget_up은
    # _DAILY_LANE_PROPOSAL_TYPES에 없어 위 leftovers sweep(codex 11R)이 안 건드린다. 오늘
    # hold분(당일 게이트·킬스위치)+이전 날 stale분을 rejected 처리해 익일 08:00 생성기가 갱신
    # 데이터로 재생성(persist pending dedup이 14일 만료까지 신규 봉투 제안을 막는 좌초 방지).
    # 킬스위치 OFF 캠페인 제외(정지 ≠ 폐기). ★codex 3R: `auto_ids & _auto_operate_campaign_ids(db)`
    # 는 **같은 세션 재조회**라 레인 도중 OFF 된 캠페인의 stale True를 볼 수 있다(SQLite WAL 리더는
    # 트랜잭션 시작 스냅샷 — _auto_operate_now가 독립 커넥션을 쓰는 이유와 동일). auto_ids(레인 시작
    # 스냅샷)로 1차 prefilter만 하고, **최종 폐기 게이트는 캠페인별 _auto_operate_now(독립 커넥션
    # fresh 확인)**로 판정한다. ★비태그 budget_up(콘솔 Confirm 대기)은 접두 필터로 절대 안 건드린다.
    if not auto_ids:
        return
    candidates_stale = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.status == "pending",
            NaverProposal.proposal_type == "budget_up",
            NaverProposal.rationale.like(f"{budget_envelope.BUDGET_ENVELOPE_PREFIX}%"),
            NaverProposal.campaign_id.in_(auto_ids),
            NaverProposal.created_at < day_end,
        )
        .all()
    )
    # 최종 게이트: 캠페인별 fresh 확인(독립 커넥션) — 도중 OFF 캠페인의 pending은 폐기하지 않는다.
    # 캠페인당 1회만 조회(dedup으로 캠페인당 leftover ≤1이나 방어적 캐시).
    fresh_on: dict[str, bool] = {}

    def _still_auto(cid: str) -> bool:
        if cid not in fresh_on:
            fresh_on[cid] = _auto_operate_now(db, cid)
        return fresh_on[cid]

    leftovers = [lp for lp in candidates_stale if _still_auto(lp.campaign_id)]
    for lp in leftovers:
        lp.status = "rejected"
        lp.rationale = (
            f"{lp.rationale or ''} [예산봉투 보류/stale — 익일 08:00 생성기가 갱신 데이터로 "
            "재생성(D-NAO-87 일일 사이클, codex 2R)]"
        )
        result["budget_rejected_stale"] += 1
    if leftovers:
        # 커밋 前 원시값 캡처(독립 리뷰 P2-1 패턴 — 커밋이 ORM 인스턴스 만료 → 커밋 후 lp.*
        # 접근이 refresh SELECT를 유발, write_diary_entry try 밖이라 fail-open 계약을 뚫는다).
        rejected_info = [
            (lp.campaign_id, lp.target_type, lp.target_id, lp.adgroup_id, lp.proposal_type)
            for lp in leftovers
        ]
        db.commit()
        for c_id, t_type, t_id, ag_id, p_type in rejected_info:
            diary.write_diary_entry(
                db, "reject", c_id, actor=diary.ACTOR_DAILY,
                target_type=t_type, target_id=t_id, adgroup_id=ag_id, action=p_type,
                rationale="예산봉투 보류/stale — 익일 08:00 재생성(D-NAO-87 일일 사이클, codex 2R)",
                now=now,
            )


def run_daily_lane(db: Session, *, now: datetime | None = None) -> dict:
    """D-NAO-48 정책의 서버 코드화 — auto_operate 캠페인의 당일 생성 pending 실행형
    (bid_up/bid_down/pause)을 심사·승인·집행(PLAN §3). 08:50 크론(catch-up 포함).

    bid_up은 4조건 전부 충족해야 승인(하나라도 미충족 시 hold — harness로 넘기지 않아
    'failed' 영구 종결을 피한다). bid_down은 무조건 승인(안전 방향, ref31 정밀도 61~88%).
    pause는 D-NAO-40 외부 정지 이력만 확인. 승인 후 실행은 반드시
    naver_execution_harness.execute(dry_run=False) 경유 — 가드레일 이중 검증 의도적
    (§3 "이중 게이트 의도적").

    codex 11R[P2] 일일 재생성 사이클: hold된 제안을 pending으로 남기면 proposal_writer.
    persist의 dedup(같은 타깃 pending 존재 시 신규 억제)에 걸려 다음 날 갱신 제안이 영구히
    안 생긴다(당일 생성분만 심사하므로 어제 pending은 재심사도 안 됨 — 959~961 좌초).
    레인 말미에 auto_operate 캠페인의 잔존 pending(오늘 hold분 + 이전 날 stale분)을
    rejected 처리 → 익일 08:00 생성기가 fresh rationale로 재생성 → 08:50 재심사.
    킬스위치 OFF 캠페인의 pending은 건드리지 않는다(정지 ≠ 폐기 — 말미에 auto_operate를
    재조회해 여전히 ON인 캠페인만 정리).

    반환: {"reviewed", "approved", "executed", "held": [{"id","reason"}], "failed",
           "rejected_stale"}.
    """
    now = now or kst_now()
    today = now.date()
    day_start, day_end = _day_bounds_utc(today)

    result: dict = {
        "reviewed": 0, "approved": 0, "executed": 0, "held": [], "failed": 0,
        "rejected_stale": 0,
        # D-NAO-87 예산 봉투 자동 심사(별도 스코프 — 비태그 budget_up 불변).
        "budget_reviewed": 0, "budget_approved": 0, "budget_executed": 0, "budget_failed": 0,
        "budget_rejected_stale": 0,
    }

    auto_ids = _auto_operate_campaign_ids(db)
    if not auto_ids:
        return result

    # P3 EX 프라이어 폴백(D-NAO-85 §4-4) — 캠페인당 압력·배분 1회 캐시 + 프라이어 lazy 로드.
    ex_ctx = _new_ex_review_ctx()

    candidates = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.status == "pending",
            NaverProposal.proposal_type.in_(_DAILY_LANE_PROPOSAL_TYPES),
            # B3 GATE P2-2 Confirm-only: target_type='ad'(소재-레벨)는 자동승인 제외 —
            # 실행은 Jino 콘솔 Confirm 경로만(D-NAO-5 카나리 "자동발사 0").
            NaverProposal.target_type != "ad",
            NaverProposal.campaign_id.in_(auto_ids),
            NaverProposal.created_at >= day_start,
            NaverProposal.created_at < day_end,
        )
        .order_by(NaverProposal.id.asc())
        .all()
    )

    for p in candidates:
        result["reviewed"] += 1

        # 전 타입 공통 사전 가드: 타깃 엔티티 status!='on'(off/deleted)이면 실행 불가 —
        # deleted에 bid_down을 태우면 harness에서 404 fail-closed가 매일 반복(2026-07-21 실사고).
        status_hold = _entity_status_hold_reason(db, p.target_type, p.target_id)
        if status_hold:
            result["held"].append({"id": p.id, "reason": status_hold})
            _record_blocked(
                db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=status_hold,
                now=now, target_type=p.target_type, target_id=p.target_id,
                adgroup_id=p.adgroup_id, action=p.proposal_type,
            )
            continue

        if p.proposal_type == "bid_up":
            hold_reason = _check_bid_up_conditions(db, p, today, ex_ctx=ex_ctx)
            if hold_reason:
                result["held"].append({"id": p.id, "reason": hold_reason})
                _record_blocked(
                    db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=hold_reason,
                    now=now, target_type=p.target_type, target_id=p.target_id,
                    adgroup_id=p.adgroup_id, action=p.proposal_type,
                )
                continue
        elif p.proposal_type == "pause":
            if _has_recent_external_stop(db, p.target_type, p.target_id):
                hold_reason = "D-NAO-40: 최근 외부/수동 정지 이력 발견 — hold"
                result["held"].append({"id": p.id, "reason": hold_reason})
                _record_blocked(
                    db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=hold_reason,
                    now=now, target_type=p.target_type, target_id=p.target_id,
                    adgroup_id=p.adgroup_id, action=p.proposal_type,
                )
                continue
        # bid_down: 조건 없음(무조건 승인, 안전 방향)

        # 킬스위치 실행 직전 재확인(codex 5R[P1-2]) — 앞선 실행 도중 OFF 됐으면 이후 전부 skip.
        if not _auto_operate_now(db, p.campaign_id):
            hold_reason = "킬스위치 OFF — auto_operate=False(실행 직전 재확인, codex 5R[P1-2])"
            result["held"].append({"id": p.id, "reason": hold_reason})
            _record_blocked(
                db, campaign_id=p.campaign_id, actor=diary.ACTOR_DAILY, reason=hold_reason,
                now=now, target_type=p.target_type, target_id=p.target_id,
                adgroup_id=p.adgroup_id, action=p.proposal_type, event_type="kill_switch",
            )
            continue

        p.status = "approved"
        p.approval_source = APPROVAL_SOURCE_DAILY
        db.commit()
        result["approved"] += 1

        try:
            naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
            result["executed"] += 1
        except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
            result["failed"] += 1
            log.warning("auto_operator: 일 레인 실행 실패 proposal_id=%s: %s", p.id, e)

    # codex 11R[P2]: 잔존 pending 정리(일일 재생성 사이클) — 오늘 hold분+이전 날 stale분을
    # rejected 처리해 persist dedup 좌초를 막는다. 킬스위치 OFF 캠페인은 제외(정지 ≠ 폐기 —
    # 그 캠페인의 pending은 그대로 두고, 스위치 재가동 시 정상 사이클로 복귀).
    #
    # codex 12R[P1 r2] TOCTOU 엄격 봉쇄("정지 ≠ 폐기" 계약) — 방언별 분기. 라운드 이력이
    # 두 엔진에서 정반대로 안전했다:
    #   · 라운드1(킬스위치를 EXISTS로 UPDATE WHERE에 바인딩, pre-SELECT 없음): UPDATE가 rollback
    #     직후 fresh txn의 첫 문이라 SQLite에선 쓰기 직렬화+최신 스냅샷으로 창이 닫힌다(SAFE).
    #     그러나 Postgres READ COMMITTED에선 단일 UPDATE 스냅샷이 문 시작에 고정 → 문 시작 후
    #     ~COMMIT 전 외부 OFF는 EXISTS에 안 보여 잘못 reject(UNSAFE).
    #   · 라운드2(라이브 집합을 with_for_update()로 먼저 SELECT-락 → campaign_id.in_(locked_live)):
    #     Postgres는 FOR UPDATE로 OFF를 reject까지 직렬화(SAFE). 그러나 SQLite는 FOR UPDATE를
    #     조용히 생략 → locked_live가 pre-SELECT로 굳은 파이썬 집합이 되고, 그 SELECT 後~UPDATE
    #     前 외부 OFF는 이미 굳은 집합에 남아 잘못 reject(UNSAFE, 파일 WAL 실증).
    # 결론: 각 엔진에 그 엔진에서 증명된 구조만 태운다.
    #   (1) 프레시 게이트(공통): 정리 직전 세션의 열린 읽기 스냅샷을 종료(_auto_operate_now
    #       docstring codex 6R[P1] 동형). 이 시점 커밋 안 된 세션 쓰기 없음(승인은 ~709 즉시 커밋,
    #       hold 일기는 독립 세션 자체 커밋) → rollback이 버릴 상태 없음. rollback 선택 이유: 순수
    #       스냅샷 폐기 의도가 명확하고, harness.execute가 예외로 남긴 세션 상태까지 리셋.
    #   (2a) Postgres: 라이브 auto 집합(auto_operate IS TRUE ∩ auto_ids)을 with_for_update()로
    #        행-락 → 외부 OFF는 락 획득 전이면 EvalPlanQual로 최신 False 재조회돼 제외, 락 획득
    #        후면 우리 COMMIT까지 블록 → 문-스냅샷 창 소멸. EXISTS 분기는 여기서 안 탐(무관).
    #   (2b) SQLite/기타: pre-SELECT 없이 킬스위치를 EXISTS로 UPDATE에 바인딩(라운드1) → UPDATE가
    #        fresh txn의 첫 쓰기문이라 최신 커밋(창 안 OFF 포함)을 평가·직렬화 → 창 소멸.
    #   auto_ids 교집합은 두 분기 모두 유지(Postgres=락 SELECT WHERE, SQLite=in_(auto_ids)) →
    #   "도중에 ON 된 캠페인 제외" 의미 불변. RETURNING으로 실제 rejected 행을 돌려받아
    #   rejected_stale 카운트+D-NAO-54 P1 일기 원시값을 단일 원자 문에서 얻는다(일기≡reject 일치).
    db.rollback()  # (1) 프레시 게이트 — 열린 읽기 스냅샷 종료(버릴 세션 쓰기 없음, 위 주석 참조)
    _sweep_precommit_seam(db)  # codex 12R 테스트 seam(prod no-op) — 단일 쓰기문 直前 창 OFF 재현점
    if db.get_bind().dialect.name == "postgresql":
        # (2a) FOR UPDATE 행-락 — 파이썬 집합으로 굳혀도 락이 reject까지 OFF를 직렬화(Postgres 전용).
        locked_live = set(
            db.execute(
                select(NaverCampaignSettings.campaign_id)
                .where(
                    NaverCampaignSettings.auto_operate.is_(True),
                    NaverCampaignSettings.campaign_id.in_(auto_ids),  # lane-start 교집합(도중 ON 제외)
                )
                .with_for_update()
            ).scalars()
        )
        kill_switch_pred = NaverProposal.campaign_id.in_(locked_live)
    else:
        # (2b) SQLite/기타: pre-SELECT 없이 킬스위치를 UPDATE에 바인딩 — UPDATE가 fresh txn 첫
        # 쓰기문이라 최신 커밋 상태로 원자 평가(쓰기 직렬화가 창을 닫는다).
        kill_switch_pred = and_(
            NaverProposal.campaign_id.in_(auto_ids),  # lane-start 교집합(도중 ON 제외)
            exists().where(
                NaverCampaignSettings.campaign_id == NaverProposal.campaign_id,
                NaverCampaignSettings.auto_operate.is_(True),
            ),
        )
    reject_pred = (
        NaverProposal.status == "pending",
        NaverProposal.proposal_type.in_(_DAILY_LANE_PROPOSAL_TYPES),
        # B3 GATE P2-2: ad-레벨 제안은 stale 정리에서도 제외 — pending은 "Confirm 대기" 정상
        # 상태(rejected 처리하면 콘솔 승인 창 자체가 소멸). 만료는 proposal_pipeline expiry(14일).
        NaverProposal.target_type != "ad",
        NaverProposal.created_at < day_end,
        kill_switch_pred,  # 방언별 킬스위치 술어(정지 ≠ 폐기) — 위 분기 참조.
    )
    reject_stmt = (
        update(NaverProposal)
        .where(*reject_pred)
        .values(
            status="rejected",
            # 원시 문자열 concat(NULL 안전 coalesce) — 초판 f"{lp.rationale or ''} …"와 동일 텍스트.
            rationale=sqlfunc.coalesce(NaverProposal.rationale, "")
            + " [auto_op 보류 — 익일 08:00 생성기가 갱신 데이터로 재생성(D-NAO-49 일일 사이클, codex 11R)]",
        )
        .returning(
            NaverProposal.campaign_id, NaverProposal.target_type,
            NaverProposal.target_id, NaverProposal.adgroup_id, NaverProposal.proposal_type,
        )
        .execution_options(synchronize_session=False)  # 로드된 인스턴스 재동기화 불필요(아래서 원시값만 사용)
    )
    rejected_rows = db.execute(reject_stmt).all()
    result["rejected_stale"] += len(rejected_rows)  # 실제 UPDATE된 행 수(RETURNING = reject 집합)
    db.commit()
    # 커밋 확정 후에만 기록(기록은 확정된 사실만) — 인자는 RETURNING 원시값(원자 문 = 일기≡reject 일치).
    for c_id, t_type, t_id, ag_id, p_type in rejected_rows:
        diary.write_diary_entry(
            db, "reject", c_id, actor=diary.ACTOR_DAILY,
            target_type=t_type, target_id=t_id, adgroup_id=ag_id, action=p_type,
            rationale="auto_op 보류/stale — 익일 08:00 재생성(D-NAO-49 일일 사이클, codex 11R)",
            now=now,
        )

    # VT3(D-NAO-82②): 소재 CTR 경보 브리핑 — 핫셋/탐색과 독립, 실행형 심사 결과와 무관하게
    # 항상 시도(auto_ids 재사용 — 위에서 이미 스코프 확정, 이 함수 내부에서 재조회는 안 하지만
    # ctr_alert 자신도 자체 auto_operate 검증을 한다, 이중 방어). fail-open은 함수 내부에서 처리.
    # D-NAO-87 예산 봉투 자동 심사([예산봉투] 접두 budget_up만) — 실행형 심사와 독립. fail-open:
    # 봉투 레인 실패가 일 레인 본작업/브리핑을 막지 않는다(CTR·유령 브리핑과 동형).
    try:
        _run_budget_envelope_lane(db, auto_ids, day_start, day_end, now, result)
    except Exception as e:  # noqa: BLE001 — 예산 봉투 레인 실패는 일 레인 본작업과 분리(fail-open)
        log.warning("auto_operator: 예산 봉투 레인 실패(fail-open): %s", e)

    result["ctr_alerts"] = 0
    _run_ctr_alert_briefing(db, now, result)

    # VF(D-NAO-83): 유령∧증거창 비활성 관측(VT3b 최소형·diary만·실쓰기 0) — CTR 브리핑과 독립.
    result["ghost_visibility_observed"] = 0
    _run_ghost_visibility_briefing(db, now, result)

    return result


# ══════════════════════════ 시간당 레인(A2+A3) ══════════════════════════


def _auto_operate_campaigns(db: Session) -> list[str]:
    rows = db.query(NaverCampaignSettings.campaign_id).filter(
        NaverCampaignSettings.auto_operate.is_(True)
    ).order_by(NaverCampaignSettings.campaign_id.asc()).all()
    return [r[0] for r in rows]


def _check_spend_circuit_breaker(db: Session, campaign_id: str, now: datetime) -> str | None:
    """§4-6 정지 조건(레인 자체 fail-closed): 당일 캠페인 소진 > 직전 7일 일평균 ×3 →
    그 캠페인의 시간당 레인 전체 hold.

    codex 2R[P1-2]: 당일 스냅샷 자체가 없으면(수집 미가동/stale — 어제 행만 있는 경우도
    ad_date 필터로 동일하게 부재) 소진을 **평가할 수 없다** — 평가 불가 상태에서 실입찰을
    진행하면 브레이커가 무의미하므로 fail-closed로 캠페인 전체 hold.

    codex 3R[P1-1]: 당일 행 존재만으로는 부족 — 스냅샷 잡이 이른 시각에 쓰고 죽으면 몇
    시간 전 today_cost로 폭주 여부를 평가하게 된다(폭주를 놓침). 최신 snapshot_hour >=
    now.hour-1을 요구한다(스냅샷 크론 :05·이 레인 :20이라 정상 시 same-hour, 직전 시각
    까지만 유예). 미달이면 stale hold(fail-closed).

    반면 직전 7일 베이스라인이 없는 경우(신규 캠페인 등)는 "당일 소진은 보이는데 비교
    기준이 없음" — 폭주 관측 자체는 가능한 상태라 미발동(fail-open, 개별 안전장치는
    guardrail_gate가 여전히 담당)으로 남긴다(두 부재의 의미가 다름)."""
    today = now.date()
    latest = (
        db.query(NaverHourlySnapshot)
        .filter(NaverHourlySnapshot.campaign_id == campaign_id, NaverHourlySnapshot.ad_date == today)
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if latest is None:
        return (
            f"당일({today.isoformat()}) 소진 스냅샷 부재 — 서킷브레이커 평가 불가"
            "(fail-closed, codex 2R[P1-2])"
        )
    if latest.snapshot_hour < now.hour - 1:
        return (
            f"소진 스냅샷 stale — 최신 snapshot_hour={latest.snapshot_hour} < now.hour-1"
            f"={now.hour - 1}(몇 시간 전 소진값으로 폭주 평가 불가, fail-closed, codex 3R[P1-1])"
        )
    today_cost = latest.cost

    window_from = today - timedelta(days=_HOURLY_BASELINE_DAYS)
    window_to = today - timedelta(days=1)
    (prior_total,) = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0)
    ).filter(
        NaverAdDaily.campaign_id == campaign_id,
        NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).one()
    prior_avg = int(prior_total) / _HOURLY_BASELINE_DAYS
    if prior_avg <= 0:
        return None
    if today_cost > prior_avg * _HOURLY_SPEND_BREAKER_MULTIPLE:
        return (
            f"소진 서킷브레이커 — 당일 {today_cost}원 > 직전{_HOURLY_BASELINE_DAYS}일평균"
            f"×{_HOURLY_SPEND_BREAKER_MULTIPLE}({prior_avg:.0f}원×{_HOURLY_SPEND_BREAKER_MULTIPLE})"
        )
    return None


# codex 1R[P1-2]: 입찰 grain 규약 — 캠페인유형별 유효 entity_type. WEB_SITE(파워링크)는
# 키워드 단위 입찰만, SHOPPING/BRAND_SEARCH는 광고그룹 단위 입찰만(naver_ad_daily grain
# 규약·update_keyword_bid/update_adgroup_bid 분기와 동일 원칙). 이 매핑에 없는 조합
# (예: WEB_SITE 캠페인의 adgroup 엔티티)은 핫셋에서 제외 — 잘못된 grain에 스텝을 쏘는
# 것을 원천 차단한다.
_HOT_SET_ENTITY_TYPE_BY_CAMPAIGN_TYPE = {
    "WEB_SITE": "keyword",
    "SHOPPING": "adgroup",
    "BRAND_SEARCH": "adgroup",
}


def _hot_set_candidates(
    db: Session, campaign_id: str, window_from: date, window_to: date,
) -> list[tuple[str, str]]:
    """§4-1 핫셋 선정: auto_operate 캠페인의 keyword/adgroup 엔티티(status='on') 중
    캠페인유형-grain 규약(P1-2, 위 매핑)에 맞고 **부모 체인 전체 활성**(codex 2R[P2])이며
    정착창 클릭 ≥10인 것.

    부모 체인(codex 2R[P2]): entity_sync는 부모-자식 status를 캐스케이드하지 않는다
    (account_diagnosis._on_adgroup_ids와 동일 근거 — 네이버 API가 각 계층 상태를 독립
    보고). 캠페인/부모 adgroup이 off인데 자식만 on이면 비활성 체인 아래에 실입찰이
    나간다 — campaign 엔티티 행 status='on' + (키워드 grain이면 부모 adgroup 행도 on)을
    요구한다. 캠페인/부모 엔티티 행 자체가 없으면 체인 확인 불가 — fail-closed 제외.
    campaign_type이 비어 있으면(동기화 미채움) grain 판정 불가 — 동일하게 fail-closed 제외
    (억지 판정 금지, 다음 entity_sync 후 자연 편입). 반환: [(target_type, target_id), ...]
    target_id 오름차순(결정적)."""
    campaign_on = (
        db.query(NaverEntity.id)
        .filter(
            NaverEntity.entity_type == "campaign",
            NaverEntity.entity_id == campaign_id,
            NaverEntity.status == "on",
        )
        .first()
        is not None
    )
    if not campaign_on:
        return []  # 캠페인 엔티티가 off이거나 행 부재 — 체인 최상위 비활성(fail-closed)

    # 이 캠페인 소속 on adgroup id 집합 — 키워드 grain의 부모 체인 확인용(캠페인 on은 위에서 확정)
    on_adgroup_ids = {
        r[0] for r in db.query(NaverEntity.entity_id).filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.campaign_id == campaign_id,
            NaverEntity.status == "on",
        ).all()
    }

    entities = (
        db.query(NaverEntity)
        .filter(
            NaverEntity.campaign_id == campaign_id,
            NaverEntity.entity_type.in_(["keyword", "adgroup"]),
            NaverEntity.status == "on",
        )
        .order_by(NaverEntity.entity_id.asc())
        .all()
    )
    out: list[tuple[str, str]] = []
    for e in entities:
        allowed_type = _HOT_SET_ENTITY_TYPE_BY_CAMPAIGN_TYPE.get(e.campaign_type or "")
        if allowed_type is None or e.entity_type != allowed_type:
            continue  # grain 규약 위반 또는 campaign_type 미확보 — fail-closed 제외
        if e.entity_type == "keyword" and e.parent_id not in on_adgroup_ids:
            continue  # 부모 adgroup off/행 부재 — 비활성 체인(fail-closed 제외, codex 2R[P2])
        agg = _settlement_agg(db, e.entity_type, e.entity_id, window_from, window_to)
        if agg["clk"] >= _MIN_CLICK_FOR_APPROVAL:
            out.append((e.entity_type, e.entity_id))
    return out


def _weighted_recent(curve: list[dict], now_hour: int) -> dict:
    """최근 _HOURLY_RECENT_HOURS(3)개 **시계 시간** 창의 imp-가중 avg_rank + imp 합계.

    codex 1R[P2]: :20 실행 시 hh24 응답에 현재 시간대(20분치 부분 데이터)가 섞여 온다 —
    부분 버킷을 그대로 판정에 쓰면 rank/표본이 왜곡된다 → hour < now_hour만.
    codex 3R[P1-2]: hh24는 **활동 있는 버킷만** 반환하므로 "완료 버킷 마지막 3개"가 몇
    시간 전 데이터일 수 있다(이른 아침 활동 후 정오까지 조용한 곡선을 14시에 판정하는 오류)
    → 창을 시계 시간 [now_hour-3, now_hour)으로 고정한다. 창에 없는 시간대는 활동 0
    (imp 0 기여)과 동일 — 창 내 imp 합 < 30이면 기존 표본 게이트가 자연 hold(00시대
    실행처럼 창이 자정 이전으로 넘어가는 부분은 그날 버킷이 없어 동일하게 표본 부족)."""
    recent = sorted(
        (h for h in curve if now_hour - _HOURLY_RECENT_HOURS <= h["hour"] < now_hour),
        key=lambda h: h["hour"],
    )
    imp_sum = sum(h["imp"] for h in recent)
    rank_imp_sum = sum(h["imp"] for h in recent if h.get("avg_rank") is not None)
    weighted_rank = None
    if rank_imp_sum > 0:
        weighted_rank = sum(
            Decimal(str(h["avg_rank"])) * h["imp"] for h in recent if h.get("avg_rank") is not None
        ) / Decimal(rank_imp_sum)
    return {"imp_sum": imp_sum, "weighted_rank": weighted_rank}


def _today_group_cpc(curve: list[dict]) -> Decimal | None:
    clk = sum(h["clk"] for h in curve)
    cost = sum(h["cost"] for h in curve)
    if clk <= 0:
        return None
    return Decimal(cost) / Decimal(clk)


def _intraday_up_ok(
    db: Session, *, target_type: str, target_id: str, campaign_id: str,
    curve: list[dict], now: datetime,
) -> tuple[bool, str]:
    """IU1(D-NAO-66) 장중 tally UP 게이트(순수) — 오늘 hh24 곡선 누적만으로 상향 근거를
    판정한다. 대행사 사이클("올려보고 → 누적 ROAS ≥ target이면 또 올리고")의 자동판이며,
    정착창(D-8~D-2) 실측 UP(_settlement_roas_ok)과 OR로 결합돼 "순위 무관·ROAS 지배" UP을
    구성한다(D-NAO-66 §0). (발동여부, 사유) 반환.

    발동 조건(전부 충족):
    ① adgroup_id 해석 가능 + intraday_roas.adgroup_unit_price가 price 산출 — 원가 아는
       상품에만 발동(미확인 상품은 판정 근거 없음, fail-closed. RL3 _intraday_loss_leash와
       동일 관례 — 원가 미확인이면 상향/하향 어느 쪽도 근거가 없다).
    ② 표본 게이트: 당일 누적 imp ≥ _MIN_HOURLY_SAMPLE_IMP(기존 상수 재사용) — 얇은 표본에서
       조급한 상향 금지. estimated_intraday_roas가 전체 곡선 누적을 쓰므로 표본도 전체 누적.
    ③ 직접전환 tally ≥ _INTRADAY_UP_MIN_CONV — hh24 conv_cnt 누적(RL1 실측 신호, 장중 동일일
       전환건수). 전환 없는(또는 1건) 유닛은 추정 ROAS가 불안정하므로 상향 근거 불충분.
    ④ estimated_intraday_roas ≥ target_roas × _INTRADAY_UP_MARGIN — 여유계수로 과소추정
       (전환지연, intraday_roas 정직 경계②)에도 확실히 이익 구간일 때만 상향(과상향 폭주 방지).

    이 게이트는 순위를 전혀 보지 않는다(D-NAO-66: 순위는 결과). 실집행은 여느 UP과 동일하게
    execute()→guardrail_gate(BEP 하한·쿨다운 2h·일일 상한·스텝 클램프) 전량을 통과해야 한다."""
    adgroup_id = _resolve_adgroup_id(db, target_type, target_id)
    if adgroup_id is None:
        return False, "adgroup 해석 불가 — 장중 UP 판정 불가"
    price = intraday_roas.adgroup_unit_price(db, adgroup_id)["price"]
    if price is None:
        return False, "상품 단가 미확인(원가 미확인 상품) — 장중 UP 판정 불가(fail-closed)"

    total_imp = sum(h["imp"] for h in curve)
    if total_imp < _MIN_HOURLY_SAMPLE_IMP:
        return False, f"당일 누적 imp={total_imp}<{_MIN_HOURLY_SAMPLE_IMP}(표본 부족) — 장중 UP 보류"

    total_conv = sum(int(h.get("conv_cnt", 0) or 0) for h in curve)
    if total_conv < _INTRADAY_UP_MIN_CONV:
        return False, f"장중 직접전환 tally {total_conv}<{_INTRADAY_UP_MIN_CONV} — 상향 근거 부족"

    est_roas = intraday_roas.estimated_intraday_roas(curve, price)
    if est_roas is None:
        return False, "당일 소진 없음 — 추정 ROAS 산출 불가"
    target_roas = _resolve_target_roas(db, campaign_id)
    if target_roas is None:
        return False, "target_roas 해석 불가(계정 기본값 없음) — 검증 불가(fail-closed)"
    threshold = Decimal(str(target_roas)) * _INTRADAY_UP_MARGIN
    if est_roas < threshold:
        return False, (
            f"장중 추정ROAS {est_roas} < target {target_roas}×{_INTRADAY_UP_MARGIN}"
            f"={threshold:.4f}(여유 미달)"
        )
    return True, (
        f"장중 tally 충족 — 전환 {total_conv}≥{_INTRADAY_UP_MIN_CONV}·추정ROAS {est_roas} ≥ "
        f"target {target_roas}×{_INTRADAY_UP_MARGIN}"
    )


def _budget_headroom_ok(db: Session, campaign_id: str, now: datetime) -> tuple[bool, str]:
    """IU1(D-NAO-66) UP 예산 여력 게이트 — 종전 "페이싱 저속일 때만 UP"을 "일예산 잔여가
    있으면 UP"으로 재정의(§2). 저속 여부가 아니라 예산이 남아 있는지가 상향 허용 기준.
    (발동가능여부, 사유) 반환.

    데이터 소스 = 당일 최신 시간별 스냅샷(cost, daily_budget) — guardrail_gate 일예산
    불가침(_check_bid)·_check_spend_circuit_breaker와 **동일 소스** 재사용(단일 진실, 하드코딩
    금지). 스텝 단위의 정밀 상한 검증은 execute()의 guardrail_gate가 다시 수행하므로, 이 게이트는
    "이미 일예산 상한에 도달했는가"(도달했으면 여력 0)만 본다.

    - daily_budget == 0 = uncapped(useDailyBudget=false, 무제한) → 항상 여력 있음(허용 —
      guardrail_gate._check_bid의 0 취급과 동일).
    - 소스 미확보(당일 스냅샷 부재)·daily_budget None·(capped인데 cost_today None) → fail-closed
      hold(예산 검증 불가 상태에서 증액 금지 — 돈 경로 보수). 라이브 경로에서는 캠페인 진입 시
      _check_spend_circuit_breaker가 스냅샷 부재·stale을 이미 캠페인 전체 hold로 걸러낸다.
    - daily_budget > 0 ∧ cost_today ≥ daily_budget → 소진 완료(여력 없음, hold)."""
    latest = (
        db.query(NaverHourlySnapshot)
        .filter(NaverHourlySnapshot.campaign_id == campaign_id, NaverHourlySnapshot.ad_date == now.date())
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if latest is None:
        return False, "당일 소진 스냅샷 부재 — 예산 여력 검증 불가(fail-closed)"
    daily_budget = latest.daily_budget
    cost_today = latest.cost
    if daily_budget is None:
        return False, "일예산 미확보(daily_budget=None) — 예산 여력 검증 불가(fail-closed)"
    if daily_budget == 0:
        return True, "일예산 uncapped(무제한) — 예산 여력 있음"
    if cost_today is None:
        return False, "당일 소진 미확보(cost_today=None) — 예산 여력 검증 불가(fail-closed)"
    if cost_today >= daily_budget:
        return False, f"일예산 소진 — 오늘 {cost_today}원 ≥ 일예산 {daily_budget}원(여력 없음)"
    return True, f"예산 여력 — 오늘 {cost_today}원 < 일예산 {daily_budget}원"


def _executed_bid_ups_today(db: Session, target_type: str, target_id: str, now: datetime) -> int:
    """오늘(KST) 이 유닛에 **성공한** 상향 실쓰기(bid_up/growth_bid_up) 횟수 — GATE P2-A-2
    장중-단독 UP 일 1스텝 캡의 카운터. 단일 쿼리(change_log×proposal 조인, N+1 없음).

    판별 기준 = naver_execution_harness._build_guardrail_context의 쿨다운/일일상한 카운트와
    동일 관례 재사용: dry_run=False ∧ after_value 존재(실쓰기 확정 — 실패 행은 after_value가
    없어 자연 제외, outcome은 D+14 채점 전 NULL이라 판별에 못 씀) ∧ changed_at ≥ KST 오늘
    0시(changed_at은 kst_now 기반 naive KST — 같은 관례). 방향(up)은 change_log에 직접 없어
    proposal_type 조인으로 식별 — growth_bid_up도 상향이므로 포함(보수 방향)."""
    today_start = datetime.combine(now.date(), datetime.min.time())
    return int(
        db.query(sqlfunc.count(NaverChangeLog.id))
        .join(NaverProposal, NaverProposal.id == NaverChangeLog.proposal_id)
        .filter(
            NaverChangeLog.entity_type == target_type,
            NaverChangeLog.entity_id == target_id,
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= today_start,
            NaverProposal.proposal_type.in_(sorted(BID_UP_TYPES)),
        )
        .scalar() or 0
    )


def _resolve_adgroup_id(db: Session, target_type: str, target_id: str) -> str | None:
    """고삐 판정용 adgroup_id 해석 — target_type='adgroup'이면 그대로, 'keyword'면
    NaverEntity.parent_id(부모 광고그룹)를 조회. 행 부재/parent_id 미기록이면 None
    (fail-closed — 원가/BEP를 어느 광고그룹에 물어야 할지 모르면 고삐를 발동하지 않는다)."""
    if target_type == "adgroup":
        return target_id
    if target_type == "keyword":
        entity = (
            db.query(NaverEntity)
            .filter(NaverEntity.entity_type == "keyword", NaverEntity.entity_id == target_id)
            .first()
        )
        if entity is None or not entity.parent_id:
            return None
        return entity.parent_id
    return None


def _intraday_loss_leash(
    db: Session, *, target_type: str, target_id: str, campaign_id: str,
    curve: list[dict], now: datetime, baseline_agg: dict,
) -> tuple[bool, str]:
    """D-NAO-60 RL3 — 장중(오늘) loss면 순위를 한 등 하향(고삐). 정지가 아니라 하향(D-NAO-59
    총이익 절대액 극대화 — 볼륨 0=이익 0이므로 kill보다 leash가 우월). (발동여부, 사유) 반환.

    발동 조건(전부 충족):
    ① adgroup_id 해석 가능(원가를 물을 대상이 정해져야 함).
    ② intraday_roas.adgroup_unit_price가 price·bep_roas 둘 다 산출(원가 아는 상품에만
       발동 — 미확인 상품은 판정 근거가 없어 fail-closed).
    ③ 당일 곡선에 소진이 있어 추정 ROAS 산출 가능(estimated_intraday_roas가 None이 아님).
    ④ **당일 누적 소진 ≥ 정착창 하루평균 소진**(baseline_agg["cost"]/_HOURLY_BASELINE_DAYS
       재사용 — 새 매직넘버 도입 금지). 전환지연으로 장중 추정 ROAS는 구조적으로 과소추정
       되므로(intraday_roas.py 정직 경계②), 하루치 예산을 이미 다 쓴 뒤에만 발동해 이른
       시각의 조급한 하향을 막는다(보수적 floor, PLAN §RL3).
    ⑤ 추정 ROAS < bep_roas(명백히 BEP 하회).

    비대칭 기억: 이 함수는 **오늘 curve만** 본다(자정 리셋 — 어제 loss가 오늘 판정에
    영향 없음). 반대로 UP 경로(_settlement_roas_ok)는 정착창 누적을 쓰며 여기서 건드리지
    않는다(관성 — 좋은 성과의 이득은 다음날 자동으로 사라지지 않음, PLAN §0 비대칭 기억).

    하향 자체는 이 함수가 결정하지 않는다 — 방향만 반환하고 실제 집행은 여느 bid_down과
    동일하게 naver_execution_harness.execute()의 guardrail_gate(BEP 하한·쿨다운 2h·일일
    상한·스텝 클램프)를 전량 통과해야 한다(§금지선: 우회 경로 금지)."""
    adgroup_id = _resolve_adgroup_id(db, target_type, target_id)
    if adgroup_id is None:
        return False, "adgroup 해석 불가"

    price_info = intraday_roas.adgroup_unit_price(db, adgroup_id)
    price = price_info["price"]
    bep_roas = price_info["bep_roas"]
    if price is None or bep_roas is None:
        return False, "상품 단가/BEP 미확인 — 고삐 판정 불가"

    est_roas = intraday_roas.estimated_intraday_roas(curve, price)
    if est_roas is None:
        return False, "당일 소진 없음"

    today_cost = sum(h["cost"] for h in curve)
    avg_daily_cost = baseline_agg["cost"] / _HOURLY_BASELINE_DAYS
    if avg_daily_cost <= 0:
        return False, "정착창 소진 기준 없음"
    if today_cost < avg_daily_cost:
        return False, "당일 소진<하루평균 — 판정 유보(과소추정 방어)"

    if est_roas < bep_roas:
        return True, (
            f"순위고삐(장중loss) — 추정ROAS {est_roas} < BEP {bep_roas}, "
            f"당일소진 {today_cost}≥하루평균 {avg_daily_cost:.0f}"
        )
    return False, f"장중 추정ROAS {est_roas} ≥ BEP {bep_roas} — 고삐 불발"


# ══════════════════════ IU-R R1 쇼검 순위 서보 원료(PLAN §2 R1) ══════════════════════


def _entity_campaign_type(db: Session, target_type: str, target_id: str) -> str | None:
    """유닛의 campaign_type 조회(NaverEntity) — 서보 그레인 라우팅용(SHOPPING adgroup만 서보,
    BRAND_SEARCH adgroup은 ±15% fallback, codex P1-3). 행 부재/미채움이면 None(fail-closed —
    서보 미적용 → 기존 _clamp_step 경로 유지, 회귀 0)."""
    row = (
        db.query(NaverEntity.campaign_type)
        .filter(NaverEntity.entity_type == target_type, NaverEntity.entity_id == target_id)
        .first()
    )
    return row[0] if row and row[0] else None


def _servo_economic_ceiling(
    db: Session, *, adgroup_id: str, campaign_id: str, servo_agg: dict,
    correction_factor: Decimal, window_from: date, window_to: date,
) -> int:
    """쇼검 광고그룹 경제성 상한(원) — compute_bid_sims의 SHOPPING 처리 동형(PLAN §2 R1).
    pooled_rpc(정착창 adgroup 실적, group_agg=campaign_agg 근사, 상위 prior={campaign,account})
    → affordable_ceiling(rpc×보정계수, target_roas). rpc≤0(심층 콜드)·target_roas 미해석 →
    0(입찰 근거 없음 → 서보 fail-closed hold)."""
    target_roas = _resolve_target_roas(db, campaign_id)
    if target_roas is None or target_roas <= 0:
        return 0
    settle = _settlement_agg(db, "adgroup", adgroup_id, window_from, window_to)
    keyword_row = {"clk": settle["clk"], "conv_amt": settle["conv_amt"]}
    campaign_agg = servo_agg["campaign"].get(campaign_id, {"clk": 0, "conv_amt": 0})
    group_agg = campaign_agg  # SHOPPING: 그룹 하위 키워드 grain 부재 → group=campaign 근사
    rpc_raw = bid_simulator.pooled_rpc(keyword_row, group_agg, campaign_agg, servo_agg["account"])
    rpc_corrected = (rpc_raw * correction_factor).quantize(Decimal("0.0001"))
    return bid_simulator.affordable_ceiling(rpc_corrected, Decimal(str(target_roas)))


def _servo_budget_pace_ok(
    db: Session, *, campaign_id: str, curve: list[dict], now: datetime, target_bid: int,
) -> tuple[bool, str]:
    """서보 스텝 예산 pace 사전체크(PLAN §2 R1, codex P1-2) — "잔여시간 예상 지출 ≤ 잔여예산
    ×안전계수"(guardrail의 사후 소진 가드를 보완하는 forward-looking 체크). (통과여부, 사유).

    원료 = 이미 조회한 hh24 곡선의 최근 3시간 실측 clk pace(신규 API 없음). 잔여 활동시간은
    now.hour 기준 그날 남은 시각(24−now.hour)으로 보수 외삽(§실측11). daily_budget=0(uncapped)
    → 통과. 소스 미확보/소진 완료 → fail-closed hold(§0 표본 게이트가 이미 대부분 hold하지만
    이중 방어). 안전계수 _BUDGET_HEADROOM_SAFETY_RATIO는 보수 방향<1.

    ★P1-2 관측 슬롯 분모: pace = clk합 ÷ **관측된 완료 슬롯 수**(imp>0 버킷만) — 고정 3이
    아니다. 자정 직후(완료 슬롯 1개)·곡선 누락 버킷을 0클릭으로 세면 pace가 과소추정돼 과대
    허용된다(누락 버킷=0 아님, 관측 슬롯만). observed==0(근거 없음·now.hour==0 포함) →
    fail-closed hold. ★P2-2: snapshot_hour<=now.hour만(같은 ad_date 내 미래 스냅샷 배제 —
    백필/테스트 now 주입 방어)."""
    latest = (
        db.query(NaverHourlySnapshot)
        .filter(
            NaverHourlySnapshot.campaign_id == campaign_id,
            NaverHourlySnapshot.ad_date == now.date(),
            NaverHourlySnapshot.snapshot_hour <= now.hour,  # P2-2: 미래 스냅샷 배제
        )
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if latest is None or latest.daily_budget is None:
        return False, "예산 소스 미확보(스냅샷/일예산) — 서보 pace 검증 불가(fail-closed)"
    if latest.daily_budget == 0:
        return True, "일예산 uncapped(무제한) — pace 제약 없음"
    if latest.cost is None:
        return False, "당일 소진 미확보 — 서보 pace 검증 불가(fail-closed)"
    remaining_budget = latest.daily_budget - latest.cost
    if remaining_budget <= 0:
        return False, f"잔여예산 없음 — 오늘 {latest.cost}원 ≥ 일예산 {latest.daily_budget}원"
    recent = [h for h in curve if now.hour - _HOURLY_RECENT_HOURS <= h["hour"] < now.hour]
    observed = sum(1 for h in recent if h["imp"] > 0)  # P1-2: 관측된 완료 슬롯만(누락=0 아님)
    if observed == 0:
        return False, "최근 완료 슬롯 관측 0(자정 직후/곡선 누락) — pace 근거 없음(fail-closed)"
    clk_pace = Decimal(sum(h["clk"] for h in recent)) / Decimal(observed)  # 관측 슬롯당 클릭
    remaining_hours = Decimal(max(0, 24 - now.hour))
    expected_spend = clk_pace * remaining_hours * Decimal(target_bid)
    allowed = Decimal(remaining_budget) * _BUDGET_HEADROOM_SAFETY_RATIO
    if expected_spend <= allowed:
        return True, (
            f"pace 여유 — 예상지출 {float(expected_spend):.0f}원 ≤ 잔여예산 {remaining_budget}원×"
            f"{float(_BUDGET_HEADROOM_SAFETY_RATIO)}"
        )
    return False, (
        f"pace 초과 — 예상지출 {float(expected_spend):.0f}원(관측 {observed}슬롯 clk pace "
        f"{float(clk_pace):.1f}/h×{int(remaining_hours)}h×{target_bid}원) > 잔여예산 "
        f"{remaining_budget}원×{float(_BUDGET_HEADROOM_SAFETY_RATIO)}"
    )


# ══════════════════════ IU-R R2 파워링크 estimate 직행 원료(PLAN §2 R2) ══════════════════════


def _rank_step_prefilter(
    db: Session, *, entity_type: str, entity_id: str, now: datetime, proposal_type: str,
) -> str | None:
    """rank-step(서보·estimate) 진입 전 쿨다운/일일캡 사전점검(PLAN §2 R2 point2 · R1 GATE P2-2
    백로그 이행). (차단사유, or None=통과). estimate 호출·서보 산정 비용을 아끼려고, 실행 시점
    guardrail이 어차피 막을 쿨다운/일일캡을 **동일 판별**로 미리 거른다 — guardrail 로직 중복
    금지: naver_execution_harness.compute_change_cadence(원료 단일 쿼리) + guardrail_gate.
    precheck_cooldown_and_cap(임계·면제 단일 소스)을 그대로 재사용한다(prefilter↔guardrail 어긋남
    0). proposal_type=bid_up_servo/bid_up_rank(둘 다 UP·일일캡 비면제)."""
    last_change_at, changes_today = naver_execution_harness.compute_change_cadence(
        db, entity_type, entity_id, now,
    )
    return guardrail_gate.precheck_cooldown_and_cap(last_change_at, changes_today, now, proposal_type)


def _estimate_target_position(weighted_rank) -> int | None:
    """estimate 목표 순위 = clamp(ceil(weighted_rank)−1, 1, 4)(PLAN §2 R2 point1, estimate
    position 1~4 제약). None 반환 = hold:
      · weighted_rank None(순위 전부 null) — 근거 없음(fail-closed).
      · weighted_rank ≤ 1+deadband — 최상단 converged(rank_servo 최상단 가드 재사용,
        estimate position 1 요청 자체 차단, codex P1-4). deadband는 rank_servo 단일 소스."""
    if weighted_rank is None:
        return None
    wr = Decimal(str(weighted_rank))
    if wr <= Decimal(1) + rank_servo._SERVO_DEADBAND:
        return None
    ceil_rank = int(wr.to_integral_value(rounding=ROUND_CEILING))
    target = ceil_rank - 1
    return max(_ESTIMATE_POSITION_MIN, min(_ESTIMATE_POSITION_MAX, target))


def _fetch_estimate_rank_bid(
    keyword_id: str, position: int, *, cache: dict, counter: dict,
) -> tuple[int | None, str]:
    """(rank_bid, note) — 목표 position 필요입찰가 estimate 조회(런 캐시·회당 캡). fetcher
    실패는 fail-closed(None). 캐시 히트/캡 도달은 API 호출을 소비하지 않는다(§난제4 호출 절약)."""
    key = (keyword_id, position)
    if key in cache:
        return cache[key], "런 캐시 재사용"
    if counter["n"] >= _RUN_ESTIMATE_BUDGET:
        return None, f"estimate 회당(run) 캡 {_RUN_ESTIMATE_BUDGET} 도달 — 호출 보류(다음 레인)"
    counter["n"] += 1
    try:
        rows = estimate_average_position_bid("MOBILE", [{"key": keyword_id, "position": position}])
    except Exception as e:  # noqa: BLE001 — API/네트워크 실패는 fail-closed hold(순위 근거 없음)
        log.warning("auto_operator: estimate 조회 실패 keyword=%s position=%s: %s", keyword_id, position, e)
        cache[key] = None
        return None, f"estimate 호출 실패({type(e).__name__})"
    # bid도 .get() — 네이버가 산정 불가 키워드에 bid 키 자체를 누락한 행을 줄 수 있고,
    # 첨자 접근이면 KeyError가 시간당 레인 전체(R1 서보 포함)를 중단시킨다(GATE R2 P1).
    # 키 부재→None은 아래 이상값 검증이 그대로 fail-closed hold로 받는다.
    # position도 일치해야(codex R2 P2) — 같은 키워드의 다른 position 행이 섞이면 목표순위와
    # 다른 필요입찰을 쓰게 된다(호출은 1건이지만 응답 방어).
    rank_bid = next(
        (r.get("bid") for r in rows
         if r.get("nccKeywordId") == keyword_id and r.get("position") == position),
        None,
    )
    cache[key] = rank_bid
    return rank_bid, f"estimate 조회(position {position})"


def _estimate_direct_step(
    db: Session, *, keyword_id: str, campaign_id: str, current_bid: int, weighted_rank,
    servo_agg: dict, correction_factor: Decimal, window_from: date, window_to: date,
    cache: dict, counter: dict,
) -> dict:
    """파워링크 estimate 직행 스텝(PLAN §2 R2) — 목표순위(현재−1)로 estimate 필요입찰을 받아
    bid_simulator.simulate_bid의 min(경제성 상한, rank_bid)로 최종 목표가를 낸다.

    반환: {"target_bid": int|None, "target_position": int|None, "step_reason": str}.
      target_bid=None = hold. estimate 실패·이상값(누락/0/비10원/범위밖/현재이하) = fail-closed
      hold(일 레인은 경제성상한만으로 계속 진행하지만, 시간당 서보는 순위 근거 없으면 스텝
      금지 — PLAN §2 R2 point5 명시 차이). 원료 부재/clk=0 심층 콜드 → 경제성상한 0 →
      recommended≤현재 → hold(fail-closed)."""
    position = _estimate_target_position(weighted_rank)
    if position is None:
        wr_txt = "None(순위 null)" if weighted_rank is None else f"{float(Decimal(str(weighted_rank))):.2f}(최상단)"
        return {"target_bid": None, "target_position": None,
                "step_reason": f"estimate 미요청(weighted_rank={wr_txt}) — fail-closed hold"}

    rank_bid, note = _fetch_estimate_rank_bid(keyword_id, position, cache=cache, counter=counter)
    if (rank_bid is None or rank_bid <= 0 or rank_bid % _BID_INCREMENT != 0
            or not (_VALID_BID_MIN <= rank_bid <= _VALID_BID_MAX)):
        return {"target_bid": None, "target_position": position,
                "step_reason": f"estimate 이상값(rank_bid={rank_bid}, {note}) — fail-closed hold"}

    target_roas = _resolve_target_roas(db, campaign_id)
    if target_roas is None or target_roas <= 0:
        return {"target_bid": None, "target_position": position,
                "step_reason": "target_roas 미해석 — fail-closed hold"}

    adgroup_id = _resolve_adgroup_id(db, "keyword", keyword_id)  # 부모 광고그룹(group_agg 귀속)
    settle = _settlement_agg(db, "keyword", keyword_id, window_from, window_to)
    keyword_row = {"clk": settle["clk"], "conv_amt": settle["conv_amt"], "bid_amt": current_bid}
    group_agg = servo_agg["group"].get(adgroup_id, {"clk": 0, "conv_amt": 0}) if adgroup_id else {"clk": 0, "conv_amt": 0}
    campaign_agg = servo_agg["campaign"].get(campaign_id, {"clk": 0, "conv_amt": 0})
    sim = bid_simulator.simulate_bid(
        keyword_row, Decimal(str(target_roas)),
        group_agg=group_agg, campaign_agg=campaign_agg, account_agg=servo_agg["account"],
        correction_factor=correction_factor, estimate={"rank_bid": rank_bid},
    )
    target_bid = sim["recommended_bid"]  # min(경제성 상한, estimate rank_bid)
    if target_bid is None or target_bid <= current_bid or target_bid < _VALID_BID_MIN:
        return {"target_bid": None, "target_position": position,
                "step_reason": (
                    f"유효 스텝 없음 — estimate {rank_bid}원·경제성상한 "
                    f"{sim['economic_ceiling']}원 → min {target_bid}원 ≤ 현재 {current_bid}원 or <70원"
                )}
    return {
        "target_bid": int(target_bid), "target_position": position,
        "step_reason": (
            f"목표순위 {position}(관측 {float(Decimal(str(weighted_rank))):.2f}) — estimate {rank_bid}원·"
            f"경제성상한 {sim['economic_ceiling']}원 → {int(target_bid)}원(현재 {current_bid}원, "
            f"basis={sim['basis']})"
        ),
    }


def _judge_hourly(
    db: Session, *, target_type: str, target_id: str, campaign_id: str, curve: list[dict], now: datetime,
) -> dict:
    """§4-3 시간당 판정(우선순위 순, 하나만) — {"direction": "up"/"down"/"hold", "reason": str}.

    ★IU(D-NAO-66) 순위 제한 폐지 + 장중 상향 개방. 순위는 목표가 아니라 결과 — 상향/하향의
    유일한 지배 게이트 = target ROAS(BEP×공격성) 유지 여부. 과열밴드 DOWN(<2.5)·UP의 순위
    하단(>4) 전제는 폐지됐다. UP은 (장중 tally OR 정착창 실측 ROAS) ∧ 예산 여력이면 순위와
    무관하게 발동한다.

    우선순위(하나만, DOWN이 UP보다 먼저 = bleeding day UP 금지, §0 불변 가드):
      0. 표본 게이트(최근 3시간 imp<30 → hold).
      1. CPC 급등 DOWN(trigger_watch.CPC_SPIKE_RATIO).
      2. 장중 loss 고삐 DOWN(D-NAO-60 RL3 — 추정 ROAS<BEP, 안전방향 한정 완화). 상단 순위의
         무전환 고지출도 여기서 잡힌다(전환 0 → est ROAS 0 < BEP → 고삐, IU2 안전망).
      3. UP(IU1): (장중 tally 게이트 OR 정착창 실측 ROAS≥target) ∧ 예산 여력. 순위 무관.
      4. 기본 hold.

    비대칭 기억(§0): 고삐 DOWN은 오늘 곡선만 보고 자정에 리셋(용서). UP의 정착창 근거는
    D-8~D-2 누적(관성 — 좋은 성과는 다음날 자동 하강하지 않음)이고, 장중 tally는 당일 누적
    (뒤로 갈수록 신뢰 상승, 자정 리셋)."""
    summary = _weighted_recent(curve, now.hour)
    # IU-R R1(PLAN §2): 구조화 verdict 필드 — 서보/estimate 라우팅이 문자열 파싱 없이 소비한다
    # (codex 지적1). weighted_rank·imp_sum은 항상 채우고(서보 입력), UP 특정 필드
    # (settle_status·intraday_ok·target_roas·est_roas·budget_ok)는 UP 판정 구간에서 채운다.
    # 기존 direction/reason 소비자 행위 불변 — 키를 추가만 한다.
    base = {"weighted_rank": summary["weighted_rank"], "imp_sum": summary["imp_sum"]}
    if summary["imp_sum"] < _MIN_HOURLY_SAMPLE_IMP:
        return {
            "direction": "hold",
            "reason": f"최근{_HOURLY_RECENT_HOURS}시간대 imp={summary['imp_sum']}<{_MIN_HOURLY_SAMPLE_IMP}(표본 부족)",
            **base,
        }

    # ── IU2(D-NAO-66): 과열밴드 DOWN(<2.5) 삭제 ──
    # 종전엔 여기서 weighted_rank<2.5면 무조건 DOWN이었다. 순위는 목표가 아니라 결과이므로
    # (§0) 순위만으로 강제 하향하지 않는다. 상단 순위(1~2등)여도 ROAS≥target이면 유지·상향,
    # 상단인데 이상 지출(무전환 고지출)이면 아래 CPC 급등 DOWN + RL3 loss 고삐가 담당한다.

    # DOWN 우선 ①: CPC 급등(trigger_watch.CPC_SPIKE_RATIO 재사용 — 단일소스, PLAN §4 원문의
    # "×1.5" 표기와 실제 상수(×2)가 불일치해 실코드 상수를 채택함, 최종보고에 명시)
    window_from, window_to = _settlement_window(now.date())
    baseline_agg = _settlement_agg(db, target_type, target_id, window_from, window_to)
    baseline_cpc = (
        Decimal(baseline_agg["cost"]) / Decimal(baseline_agg["clk"]) if baseline_agg["clk"] > 0 else None
    )
    today_cpc = _today_group_cpc(curve)
    if baseline_cpc is not None and baseline_cpc > 0 and today_cpc is not None:
        if today_cpc > baseline_cpc * CPC_SPIKE_RATIO:
            return {
                "direction": "down",
                "reason": (
                    f"CPC급등 — 당일={float(today_cpc):.1f}원 > 정착창기준={float(baseline_cpc):.1f}원"
                    f"×{CPC_SPIKE_RATIO}"
                ),
                **base,
            }

    # DOWN 우선 ②: 장중 loss 고삐(D-NAO-60 RL3) — UP보다 먼저 검사해 "bleeding day엔 UP
    # 금지"(§0 우선순위)를 자연히 구현한다. baseline_agg는 위 CPC급등 검사에서 이미 계산된
    # 값을 그대로 재사용(중복 쿼리 금지). ★IU2 안전망: 과열밴드 DOWN 삭제 후 상단 순위의
    # 무전환 고지출 유닛은 이 고삐가 잡는다(전환 0 → est ROAS 0 < BEP, 하루치 소진 도달 시).
    leash_fired, leash_reason = _intraday_loss_leash(
        db, target_type=target_type, target_id=target_id, campaign_id=campaign_id,
        curve=curve, now=now, baseline_agg=baseline_agg,
    )
    if leash_fired:
        return {"direction": "down", "reason": leash_reason, "leash": True, **base}

    # UP(IU1, D-NAO-66): 순위 전제 폐지 — target ROAS 유지가 유일 지배 게이트.
    # UP 검토 = (장중 tally 게이트) OR (정착창 실측 ROAS≥target). 둘 다 실패면 hold("재시작
    # 대기"의 진짜 사유 = ROAS 미달 — 순위 전제가 사라졌으니 사유문도 ROAS 기준으로 정합).
    # ★DL4(D-NAO-65) 익일 밴드 재시작 = 이 UP 경로가 자연 수행(어제 고삐로 스로틀된 건강 유닛이
    # 정착창 ROAS≥target이면 재상향). 별도 재시작 미들웨어·BEP 우회 게이트 신설 없음(§0 금지선).
    intraday_ok, intraday_reason = _intraday_up_ok(
        db, target_type=target_type, target_id=target_id, campaign_id=campaign_id,
        curve=curve, now=now,
    )
    settle_status, settle_reason = _settlement_roas_status(
        db, target_type, target_id, campaign_id, now.date()
    )
    # IU-R R1 구조화 필드(UP 특정) — 서보/estimate 라우팅·다운스트림 브레드크럼. est_roas는
    # intraday_roas로 산출(원가 아는 상품에 한함, 아니면 None). 서보 라우팅은 이 필드를 소비하지
    # 않고(economic_ceiling을 harness가 별도 precompute) weighted_rank/imp_sum만 쓴다.
    _servo_adgroup = _resolve_adgroup_id(db, target_type, target_id)
    _servo_price = intraday_roas.adgroup_unit_price(db, _servo_adgroup)["price"] if _servo_adgroup else None
    up_fields = {
        "settle_status": settle_status,
        "intraday_ok": intraday_ok,
        "target_roas": _resolve_target_roas(db, campaign_id),
        "est_roas": intraday_roas.estimated_intraday_roas(curve, _servo_price) if _servo_price is not None else None,
    }
    # GATE P2-A-1 정산 거부권(veto): 정산(간접전환 포함 실측 — 장중 ccnt보다 신뢰)이 **명시적
    # 미달**(below)이라 말하면, 장중 추정(과대귀속 가능)이 아무리 좋아도 UP 금지. "모름
    # (unknown)"과 "나쁨(below)"은 다르다 — 나쁨이 확인된 유닛을 추정만으로 올리지 않는다.
    if settle_status == "below" and intraday_ok:
        return {
            "direction": "hold",
            "reason": (
                f"UP 보류(정산 거부권, GATE P2-A) — 정착창 명시적 미달({settle_reason})인데 "
                f"장중 추정({intraday_reason})만으로는 상향 금지"
            ),
            **base, **up_fields,
        }
    if not (intraday_ok or settle_status == "ok"):
        # DL4 관측 사유("재시작 대기")의 의미 유지 — 만성 sub-BEP 유닛이 UP 게이트를 못 넘고
        # 바닥에 눌러앉는 신호를 기존 hold reason 체계로 표면화(순위 언급 제거, ROAS 근거로).
        return {
            "direction": "hold",
            "reason": f"재시작 대기(ROAS 미달) — 장중 tally:{intraday_reason} / 정착:{settle_reason}",
            **base, **up_fields,
        }
    # GATE P2-A-2 장중-단독 UP 일 1스텝 캡: 정산 판정불가(unknown) 유닛의 UP 근거가 장중
    # 추정뿐이면, 오늘 이미 성공한 상향이 있을 때 추가 UP 금지(+15%/일 제한). 다음날 정산이
    # 따라와 veto(below)하거나 승인(ok)한다. settle_ok 근거 UP은 기존 일일상한(3, guardrail)만.
    intraday_only = intraday_ok and settle_status != "ok"
    if intraday_only:
        ups_today = _executed_bid_ups_today(db, target_type, target_id, now)
        if ups_today >= _INTRADAY_ONLY_UP_DAILY_CAP:
            return {
                "direction": "hold",
                "reason": (
                    f"UP 보류(장중 단독 일 {_INTRADAY_ONLY_UP_DAILY_CAP}스텝 캡, GATE P2-A) — "
                    f"오늘 성공 상향 {ups_today}회, 정산 판정불가({settle_reason}) 유닛은 "
                    "추정 단독으로 하루 1스텝만"
                ),
                **base, **up_fields,
            }
    # 예산 여력(IU1 재정의): 저속일 때만이 아니라 일예산 잔여가 있으면 UP(§2). 스텝 단위 정밀
    # 상한은 execute()의 guardrail_gate가 재검증 — 여기선 "이미 소진 완료 아님"만 확인.
    budget_ok, budget_reason = _budget_headroom_ok(db, campaign_id, now)
    if not budget_ok:
        return {
            "direction": "hold", "reason": f"UP 보류(예산 여력 없음/미확보) — {budget_reason}",
            **base, **up_fields, "budget_ok": budget_ok,
        }
    up_basis = (
        f"정착창 실측({settle_reason})" if settle_status == "ok" else f"장중 tally({intraday_reason})"
    )
    return {
        "direction": "up",
        "reason": f"ROAS-UP(순위 무관, D-NAO-66) — {up_basis}, {budget_reason}",
        **base, **up_fields, "budget_ok": budget_ok,
    }


def _clamp_step(current_bid: int, direction: str) -> int | None:
    """§4-4 스텝 = 현재가×(1±0.15) 클램프 + 10원 반올림 — proposal_writer._bid_proposal의
    스텝 클램프 반올림 규약과 동일(up=10원 내림, down=10원 올림, 절대하한 70원, 상한
    100,000원) — _MAX_CHANGE_PCT 단일소스(guardrail_gate) import."""
    if direction == "up":
        raw = Decimal(current_bid) * (Decimal(1) + _MAX_CHANGE_PCT)
        stepped = int(raw // 10) * 10
        stepped = min(stepped, 100_000)
        return stepped if stepped > current_bid else None
    if direction == "down":
        raw = Decimal(current_bid) * (Decimal(1) - _MAX_CHANGE_PCT)
        stepped = int((raw / 10).to_integral_value(rounding=ROUND_CEILING)) * 10
        stepped = max(stepped, 70)
        return stepped if stepped < current_bid else None
    return None


def _probe_window_stats(curve: list[dict], now: datetime) -> tuple[int, int, Decimal | None, str]:
    """D-NAO-58 CD2·D-NAO-60 RL5(CD5) 공유 헬퍼 — 완료 창 [now.hour-_PROBE_ZERO_CLICK_HOURS,
    now.hour)의 imp/clk 합 + imp-가중 avg_rank(rank None 버킷 제외). `_probe_trigger`와
    `_learned_optimal_skip`이 동일 창·동일 가중 규약을 쓰도록 계산부만 공유(중복 제거).
    반환 (imp_sum, clk_sum, weighted_rank_or_None, win_label)."""
    window_start = now.hour - _PROBE_ZERO_CLICK_HOURS
    window = [h for h in curve if window_start <= h["hour"] < now.hour]  # 완료 시간대만(현재 진행중 제외)
    imp_sum = sum(h["imp"] for h in window)
    clk_sum = sum(h["clk"] for h in window)
    rank_imp_sum = sum(h["imp"] for h in window if h.get("avg_rank") is not None)
    weighted_rank = None
    if rank_imp_sum > 0:
        weighted_rank = sum(
            Decimal(str(h["avg_rank"])) * h["imp"] for h in window if h.get("avg_rank") is not None
        ) / Decimal(rank_imp_sum)
    win_label = f"[{window_start},{now.hour})"
    return imp_sum, clk_sum, weighted_rank, win_label


def _probe_trigger(
    curve: list[dict], now: datetime, rank_floor: Decimal | None = None,
) -> tuple[bool, str]:
    """D-NAO-58 CD2 클릭 탐침 트리거(순수 SA·단일 창 자기완결) — 밴드의 사각지대(밴드 안/하단
    인데 클릭0)를 감지한다(D-58-7 확정, 기존 검증 상수 재사용). (발동여부, 사유) 반환.

    ★리뷰 R1 P3-1 수정: 클릭/노출과 rank를 **같은 완료 2시간 창 [now.hour-2, now.hour)에서**
    산출한다. 초판은 rank를 _weighted_recent의 3시간 창에서 받아 넘겨, 클릭창 밖(3h 전) 저순위·
    고노출 버킷이 가중 rank를 끌어올려 최근 2h가 이미 좋은 순위(2.0)인 유닛에 탐침이 나가는
    오탐이 재현됐다(D-58-7 조건③ 훼손). 이제 rank도 이 창 안에서만 imp-가중 계산한다.

    조건(전부 충족 시 발동, 창 = [now.hour - _PROBE_ZERO_CLICK_HOURS, now.hour)):
    - clk 합 == 0.
    - imp 합 ≥ 30(_MIN_HOURLY_SAMPLE_IMP 재사용) — "노출 부족 무클릭"↔"낮은 순위 무클릭"
      분리: imp≥30인데 clk=0이면 순위 병리 = 탐침 대상.
    - 창 내 imp-가중 avg_rank ≥ 하한 — rank가 하한보다 좋으면 밴드 상단/과열 = 위치가 아니라
      수요 문제라 올려도 소용없음. 밴드 안/하단이어야 올라갈 여지.
      하한 = `rank_floor`(호출부가 넘긴 **학습된** 밴드 하한) 또는 없으면
      `_HOURLY_RANK_DOWN_THRESHOLD`(2.5, 하드코딩 프라이어). 학습값 우선 규약은
      `_probe_rank_floor` docstring 참조 — 상수가 학습값을 이기던 충돌을 그쪽에서 해소한다.
      SA는 db를 모른다(원칙18-6) — 학습밴드 조회는 harness(run_hourly_lane)가 해서 넘긴다.
      가중 로직은 _weighted_recent와 동일(rank None 버킷 제외·imp 가중·Decimal). 창 안 rank가
      전부 None이면(rank_imp_sum==0) weighted_rank=None → fail-closed 보류(근거 없음).

    now.hour < 2(이른 새벽, 완료 2시간 창이 그날 버킷을 못 채우는 경계)면 표본 없음으로
    False — _weighted_recent의 자정 경계 처리와 동일 철학. BEP 여유는 여기서
    수치로 판단하지 않는다 — 제안이 execute()로 흐르면 guardrail_gate가 BEP 하한을 강제한다
    (§금지선: 탐침 우회 경로 금지, downstream 위임)."""
    if now.hour < _PROBE_ZERO_CLICK_HOURS:
        return False, f"이른 새벽(now.hour={now.hour}<{_PROBE_ZERO_CLICK_HOURS}) — 완료 창 표본 없음(탐침 보류)"

    imp_sum, clk_sum, weighted_rank, win_label = _probe_window_stats(curve, now)
    if clk_sum != 0:
        return False, f"클릭 존재(창{win_label} clk={clk_sum}) — 탐침 대상 아님"
    if imp_sum < _MIN_HOURLY_SAMPLE_IMP:
        return False, f"노출 부족(창{win_label} imp={imp_sum}<{_MIN_HOURLY_SAMPLE_IMP}) — 순위 병리 아님(탐침 보류)"
    if weighted_rank is None:
        return False, f"가중 avg_rank 근거 없음(창{win_label} rank 전부 None) — 탐침 보류(fail-closed)"
    floor = rank_floor if rank_floor is not None else _HOURLY_RANK_DOWN_THRESHOLD
    floor_label = (
        f"{floor}(학습밴드)" if rank_floor is not None else f"{_HOURLY_RANK_DOWN_THRESHOLD}"
    )
    if weighted_rank < floor:
        return False, (
            f"창{win_label} 가중 avg_rank={float(weighted_rank):.2f}<{floor_label}"
            "(밴드 상단/과열=수요 문제) — 탐침 대상 아님"
        )
    return True, (
        f"창{win_label} imp={imp_sum}≥{_MIN_HOURLY_SAMPLE_IMP}·clk=0·가중avg_rank="
        f"{float(weighted_rank):.2f}≥{floor_label}(밴드 사각지대 — 한 등 상향 탐침)"
    )


def _probe_rank_floor(learned_band: str | None) -> Decimal | None:
    """학습된 최적 밴드 → 탐침 트리거 하한(없으면 None = 하드코딩 프라이어 유지).

    ★해소하는 충돌(2026-07-28): `_probe_trigger`의 하한은 `_HOURLY_RANK_DOWN_THRESHOLD`
    (2.5) 상수였다. 그 근거는 "rank<2.5면 위치가 아니라 수요 문제"라는 **프라이어**인데,
    학습이 그 캠페인의 최적 밴드를 `1.0-2.0`으로 승격시키면 프라이어와 학습값이 정면으로
    충돌한다. 그때 상수가 이기면 탐침은 rank 2.5에서 멈추고 **자기가 학습한 최적점(2.0)에
    구조적으로 도달할 수 없다.** 동시에 그 도달을 막으려고 만든 CD5 게이트
    (`_learned_optimal_skip`, 하한 band_high 미만이면 생략)도 2.5 > 2.0이라 영원히 발동하지
    않는다 — 학습 소비자 하나가 통째로 사문화된다.

    이 코드베이스의 일관된 규약대로 **학습값이 있으면 학습값이, 없으면 하드코딩 프라이어가**
    이긴다. 단 방향은 `min`으로만 — 학습밴드가 2.5보다 느슨해도(2.5-3.0/3.0-4.0/4.0+)
    하한 **값**을 키우지 않는다(그 경우 종전과 완전히 동일하게 동작하고, 과climb 억제는
    기존 CD5가 그대로 담당한다). 즉 이 함수가 실제로 값을 바꾸는 경우는 `1.0-2.0` 하나다.

    ★★위험 방향을 정확히 적는다(리뷰 L-1 정정). 하한 **값**은 안 커지지만 하한이 내려가면
    **탐침 발동 집합은 엄격히 커진다** — 학습밴드가 `1.0-2.0`인 캠페인에서 rank 2.0~2.5
    구간이 새로 열리고, 그건 입찰 **상향** 제안이 늘어난다는 뜻이다(라이브 광고비 방향).
    "느슨해지지 않는다"는 값 이야기지 노출 이야기가 아니다. 새로 열리는 2.0~2.5는 실측
    이익극대 스팟밴드(2.5~4)보다 **위쪽**이라 이익 관점에선 검증되지 않은 구간이다 —
    그래서 학습이 그 캠페인에 대해 직접 승격시킨 밴드가 있을 때만 열린다.
    제안이 실제 집행되려면 ±15% 클램프·킬스위치 재확인·쿨다운 2h·일일상한·BEP 하한·
    스톱로스를 전부 통과해야 한다(우회 없음).

    ★CD5와의 관계도 정확히 적는다(리뷰 H-2 정정). floor를 band_high로 맞췄으므로 탐침
    발동(rank ≥ band_high)과 CD5 생략(rank < band_high)은 **정확한 여집합**이 된다 —
    즉 `1.0-2.0`에서 CD5는 여전히 한 번도 발동하지 않는다. 달성된 것은 "탐침이 학습
    최적점에서 멈춘다"이지 "CD5가 되살아난다"가 아니다. CD5의 역할은 이제 트리거 자신이
    수행하고, CD5는 중복 방어로 남는다. (floor를 band_high보다 낮추면 탐침이 최적점을
    **넘어서** 계속 오르므로 그 방향은 취하지 않는다.)

    상한 개방 밴드("4.0+", hi=None)는 종전 경로 유지 — 탐침이 발동한 뒤 CD5가 생략시키며,
    그 편이 "학습 최적밴드 도달" 사유가 일기에 남아 관측성이 낫다.
    """
    if not learned_band:
        return None
    from app.services.naver_ad import probe_cell_aggregate

    try:
        band_high = probe_cell_aggregate.rank_band_upper(learned_band)
    except ValueError:  # 알 수 없는 라벨 — 프라이어 유지(조용한 오판정 방지)
        return None
    if band_high is None or band_high >= _HOURLY_RANK_DOWN_THRESHOLD:
        return None
    return band_high


def _account_band_fallback_ok(
    db: Session, target_type: str, target_id: str,
) -> tuple[bool, str]:
    """이 유닛에 **계정 밴드 폴백**을 적용해도 되는가 = 사후 고삐가 실제로 발동 가능한가.

    ★조건은 하나: 상품 단가·BEP가 확인될 것(Jino 확정 2026-07-29).
    근거 — 브레이크는 두 겹인데 그중 하나가 이 조건에 걸려 있다.
      · 사전 상한(`bid_simulator.affordable_ceiling` = 보정RPC ÷ target ROAS)은 자기 이력이
        없어도 `pooled_rpc`의 계층 수축으로 항상 계산된다. 즉 BEP를 넘는 입찰가로는 애초에
        못 올라간다. **다만 그 RPC는 빌린 값**이라 자기 CVR이 계정 평균보다 나쁘면 상한이
        자기 기준으론 관대하다(트랙 실측: 신규 그룹에서 계정 풀링 상한이 약 22% 관대).
      · 그 관대함을 회수하는 것이 사후 고삐(`_intraday_loss_leash`, 장중 ROAS<BEP → 한 등
        하향)인데, 그 함수는 `price`·`bep_roas`가 **둘 다** 있어야 판정하고 없으면
        fail-closed로 **침묵**한다. 즉 BEP 미확인 유닛은 사전 상한만 남고 회수 장치가 없다.
    그래서 BEP가 확인되는 유닛에만 폴백을 연다 — 브레이크가 두 겹 다 살아 있는 곳에서만
    빌린 학습값으로 올라간다.

    자기 캠페인이 직접 승격시킨 밴드가 있으면 이 검사를 타지 않는다(그건 빌린 값이 아니다).
    """
    adgroup_id = _resolve_adgroup_id(db, target_type, target_id)
    if adgroup_id is None:
        return False, "adgroup 해석 불가 — 계정밴드 폴백 보류"
    try:
        info = intraday_roas.adgroup_unit_price(db, adgroup_id)
    except Exception:  # noqa: BLE001 — 판정 불가는 fail-closed(폴백 안 함)
        log.exception("auto_operator: 폴백 BEP 확인 실패 adgroup=%s", adgroup_id)
        return False, "BEP 조회 실패 — 계정밴드 폴백 보류"
    if info.get("price") is None or info.get("bep_roas") is None:
        return False, "상품 단가/BEP 미확인 — 사후 고삐 불가라 계정밴드 폴백 보류"
    return True, "BEP 확인 — 계정밴드 폴백 허용"


def _learned_bands_of(
    db: Session, now: datetime, campaign_id: str,
) -> tuple[str | None, str | None]:
    """(그 캠페인 자신의 승격 밴드, 계정 전체 승격 밴드). 각각 없으면 None.

    ★스코프 불일치 해소(Jino 확정 2026-07-29): 09:03 학습 잡은 `campaign_id` 없이 돌아
    **계정 전체** 밴드를 승격·기록하는데 게이트는 **캠페인별** 값만 읽었다. 그래서 계정에
    학습값이 있어도 캠페인이 자기 승격에 못 미치면 그 캠페인은 하드코딩 프라이어(2.5)로
    동작했다 — 학습해놓고 아무도 안 읽는 상태(2026-07-29 실측: ours 6개 중 계정 승격
    밴드 `1.0-2.0`을 쓰는 캠페인 0개).
    이제 둘 다 돌려주고, **자기 값이 없을 때만** 호출부가 조건부로 계정 값을 쓴다
    (조건 = `_account_band_fallback_ok`).
    """
    return (
        _learned_band_of(db, now, campaign_id),
        _learned_band_of(db, now, None),
    )


def _learned_band_of(db: Session, now: datetime, campaign_id: str | None) -> str | None:
    """그 캠페인(또는 campaign_id=None이면 계정 전체)·오늘 환경셀의 승격된 최적 밴드 라벨.

    `_learned_optimal_skip`이 하던 조회를 밖으로 뽑은 것 — 탐침 하한(`_probe_rank_floor`)과
    CD5 게이트가 **같은 값**을 봐야 서로 어긋나지 않는다. 조회 실패는 삼키고 None(=학습값
    없음 → 하드코딩 프라이어 유지)으로 폴백한다: 학습 조회가 시간당 레인 전체를 막으면 안 된다.
    """
    from app.services.naver_ad import probe_cell_aggregate, probe_learning_loop

    try:
        env_cell = probe_cell_aggregate.env_cell_of_date(now.date())
        return probe_learning_loop.learned_probe_rank(
            db, env_cell=env_cell, as_of=now.date(), campaign_id=campaign_id,
        )
    except Exception:  # noqa: BLE001 — 학습값 부재와 동일 취급(프라이어 폴백)
        log.exception("auto_operator: 학습밴드 조회 실패 campaign=%s", campaign_id)
        return None


def _learned_optimal_skip(
    db: Session, curve: list[dict], now: datetime, campaign_id: str,
    learned_band: str | None = None, band_resolved: bool = False,
) -> tuple[bool, str]:
    """D-NAO-59/60 RL5(CD5) — 탐침(`_probe_trigger`)이 발동한 직후 게이트. **탐침 전용**.
    ★밴드=탐침 프라이어, ROAS-UP 캡 아님(D-NAO-66). IU2 이후 ROAS-driven 일반 UP은 이 게이트를
    참조하지 않는다(순위는 목표가 아니라 결과 — 학습밴드로 상향을 막지 않는다). 이 게이트는
    "클릭 살아나는 순위"를 찾는 탐침이 과열 상단으로 무근거로 계속 오르는 것만 억제한다.
    과climb 방지: 이익 스팟밴드(2.5~4)를 넘어 학습된 최적 순위밴드까지 이미 도달했으면 더 올릴
    이유가 없다(탐침 목적이 이미 달성됨). `probe_learning_loop.
    learned_probe_rank`(CD4 산출물)로 그 캠페인 env_cell의 승격된 최적 밴드를 조회해,
    `_probe_trigger`와 **동일한 완료 2시간 창**([now.hour-_PROBE_ZERO_CLICK_HOURS, now.hour))
    에서 산출한 가중 avg_rank와 그 밴드 상한을 비교한다.

    lazy import(순환 회피 — run_hourly_lane 말미 probe_revert import 전례). (skip, 사유)
    반환 — skip=True면 run_hourly_lane이 up 승격을 취소하고 hold(관찰)로 남긴다. 학습된
    밴드가 없으면(데이터 부족·백필 미도달) 게이트 없이 통과(CD2 폴백 — 이 게이트는 CD2
    위에 얹는 추가 제약일 뿐, CD2 자체의 발동 조건을 대체하지 않는다). guardrail 우회는
    없다 — skip=False로 통과한 제안도 기존 execute()→guardrail_gate 전량을 그대로 탄다."""
    from app.services.naver_ad import probe_cell_aggregate, probe_learning_loop

    _imp_sum, _clk_sum, weighted_rank, win_label = _probe_window_stats(curve, now)
    if weighted_rank is None:
        return False, "순위 근거 없음 — 게이트 미적용"

    env_cell = probe_cell_aggregate.env_cell_of_date(now.date())
    # ★호출부가 이미 해결한 값이 있으면 그것을 쓴다(리뷰 M-1). 재조회하면 ①유닛마다
    # 30일 aggregate를 도는 N+1이 그대로 남고 ②탐침 하한과 이 게이트가 **서로 다른 값**을
    # 볼 수 있다(하한은 run 시작값 고정, 여기는 매번 재계산) — 두 게이트가 같은 밴드를
    # 봐야 어긋나지 않는다는 게 이 배선의 전제다.
    learned = learned_band if band_resolved else probe_learning_loop.learned_probe_rank(
        db, env_cell=env_cell, as_of=now.date(), campaign_id=campaign_id,
    )
    if learned is None:
        return False, "학습된 최적 밴드 없음 — 무조건 탐침(CD2 폴백)"

    band_high = probe_cell_aggregate.rank_band_upper(learned)
    if band_high is None or weighted_rank < band_high:
        return True, (
            f"학습 최적밴드({env_cell}→{learned}) 이미 도달(현재{win_label} 가중avg_rank="
            f"{float(weighted_rank):.2f}) — 탐침 생략(CD5)"
        )
    return False, (
        f"학습 최적밴드({learned})보다 하위(현재{win_label} 가중avg_rank="
        f"{float(weighted_rank):.2f}) — 탐침 상향(CD5 목표)"
    )


# ══════════════════ B-X BX3 저볼륨 그룹 탐색-UP 레인(PLAN §2·§3, D-NAO-70·71) ══════════════════

# BX3 GATE P2-risk6(PLAN §1 가드5 완성): 탐색 UP 전 **활성 daily 손실 상태** 제외 대상 보드 —
# account_diagnosis 어드그룹 손실 보드를 retro_snapshotter가 매일 스냅한 것이 원료다(추정 아님):
#   · shopping_group_bep         = 정착 ROAS<BEP 바닥손실(일 레인 bid_up 게이트 _bleeding_hold_reason
#                                  이 읽는 D-NAO-48 조건④ 보드 — down 방향).
#   · shopping_pause_candidates  = 무전환 스톱로스/floored 후보(proposal_writer._stop_loss_proposal이
#                                  읽는 DL 스톱로스 보드 — pause 방향).
# 어제 daily 고삐(bid_down 스톱로스·floored pause)로 조인 그룹이 오늘 탐색 UP으로 역전되는 것을
# 막는다(_bleeding_hold_reason의 asof 신선도 계약 재사용 — fail-closed).
_ADGROUP_DAILY_LOSS_BOARDS = ("shopping_group_bep", "shopping_pause_candidates")


def _exploration_daily_loss_reason(db: Session, adgroup_id: str, today: date) -> str | None:
    """탐색 후보 그룹이 **활성 daily 손실 상태**면 제외 사유(문자열), 아니면 None(PLAN §1 가드5,
    GATE P2-risk6). DL이 읽는 것과 **동일 보드/신선도**를 재사용한다:
    - shopping_group_bep + asof 신선도 = 일 레인 bid_up 게이트(_bleeding_hold_reason, D-NAO-48 조건④)
      를 그대로 호출(같은 판정 원료). 신선도 미달·bleeding이면 그 사유 반환(fail-closed).
    - shopping_pause_candidates(스톱로스/floored 보드, _stop_loss_proposal 원료)에 이 그룹이 있으면
      제외(같은 최신 asof — group_bep 통과 시 신선함이 보장됨).
    ★탐색은 표본-기반 UP인데 daily 손실 조치는 비용-기반 백스톱이라, 후자가 조인 그룹을 전자가
      역전하면 어제 조치가 무의미해진다(가드5: 손실 조치 그룹 UP 금지)."""
    bep_reason = _bleeding_hold_reason(db, "adgroup", adgroup_id, today)
    if bep_reason is not None:
        return bep_reason  # shopping_group_bep bleeding OR asof stale/missing(fail-closed)
    # group_bep 통과 = 신선 asof 확정 → 같은 최신 asof에서 스톱로스 보드도 확인.
    latest_asof = db.query(sqlfunc.max(NaverRetroSignal.asof_date)).scalar()
    on_stoploss = db.query(NaverRetroSignal.id).filter(
        NaverRetroSignal.asof_date == latest_asof,
        NaverRetroSignal.board == "shopping_pause_candidates",
        NaverRetroSignal.target_id == adgroup_id,
    ).first()
    if on_stoploss is not None:
        return "daily 스톱로스/floored 후보(shopping_pause_candidates) — 탐색 UP 제외(가드5)"
    return None


def _exploration_bid_from_change_value(raw: str | None) -> int | None:
    """change_log before/after_value(JSON)에서 bidAmt 추출 — 광고그룹({"bidAmt":N})·소재
    ({"nccAdId":..,"adAttr":"{..bidAmt..}"} 중첩) 둘 다 지원(직전 탐색 스텝 입찰 파싱)."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    if isinstance(d.get("bidAmt"), int):
        return d["bidAmt"]
    attr = d.get("adAttr")  # 소재(ad) — adAttr JSON 문자열 중첩
    if attr:
        try:
            a = json.loads(attr) if isinstance(attr, str) else attr
        except (ValueError, TypeError):
            return None
        if isinstance(a, dict) and a.get("bidAmt") is not None:
            try:
                return int(a["bidAmt"])
            except (ValueError, TypeError):
                return None
    return None


def _exploration_last_step(db: Session, adgroup_id: str) -> dict | None:
    """이 그룹의 마지막 탐색 UP **성공 실쓰기**(change_log) — 직전 스텝 스냅샷(적응 스텝 기울기·
    쿨다운 tz 계약 원료). NaverProposal(adgroup_id·proposal_type∈EXPLORATION_STEP_TYPES) ⋈
    change_log(after_value 존재 = 성공 실쓰기, 가드실패는 after_value None). ★tz 계약(PLAN §3
    이월 P2②): changed_at은 harness가 now(=kst_now, KST naive)로 심으므로 KST — 호출측 now(kst_now)
    와 동일 tz. proposal.created_at(UTC)는 쓰지 않는다. 반환 {"before_bid","after_bid",
    "changed_at","target_type"}|None(첫 탐색)."""
    row = (
        db.query(NaverChangeLog)
        .join(NaverProposal, NaverChangeLog.proposal_id == NaverProposal.id)
        .filter(
            NaverProposal.adgroup_id == adgroup_id,
            NaverProposal.proposal_type.in_(tuple(EXPLORATION_STEP_TYPES)),
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.after_value.isnot(None),  # 성공 실쓰기만(가드실패=outcome failed·after None)
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "before_bid": _exploration_bid_from_change_value(row.before_value),
        "after_bid": _exploration_bid_from_change_value(row.after_value),
        "changed_at": row.changed_at,
        "target_type": row.entity_type,
        "target_id": row.entity_id,  # codex P1: 기울기 연속성 판정용(직전 스텝의 실쓰기 대상)
    }


def _exploration_yesterday_flow(db: Session, adgroup_id: str, yesterday: date, from_hour: int) -> int | None:
    """어제 [from_hour, 23] 시간대 이 그룹 clk 합(롤링 24h 창의 어제 부분, codex P1 자정 리셋 +
    신규 P1 유닛-레벨 교차확인). 반환 int=확정 clk / None=확정 불가(fail-toward-hold 신호).

    출처: NaverKeywordHourly(keyword_hourly_sweep이 SHOPPING 그룹을 keyword_id='' sentinel →
    entity_type='adgroup'·entity_id=adgroup_id로 유닛별 hh24 통째 축적). ★keyword_hourly_sweep은
    **유닛-부분적**(실패/캡 스킵 유닛만 빠지고 나머지 커밋) — "어제 아무 행이나 있으면 스윕 성공"의
    날짜-레벨 판정은 이 그룹만 실패한 날 "flow 0" 오독을 낳는다(codex 신규 P1). 그래서:

    ① 이 그룹의 어제 시간별 행 존재 = 이 유닛이 스윕됨(전 hh24 저장) → 창 합이 신뢰 가능(반환).
    ② 이 그룹 시간별 부재 → 어제 naver_ad_daily(이 adgroup, sentinel 제외)로 유닛-레벨 교차확인:
       - 이 그룹 daily clk>0 → 클릭 실재했는데 시간별만 누락(이 유닛 스윕 실패) → None(fail-toward-hold).
       - 이 그룹 daily 행 있고 clk=0 → 일별 grain 확정 무클릭 → 0(정상 사용).
       - 이 그룹 daily 행 없음 → 어제 daily가 **수집됐는지**(날짜-레벨 — daily는 원자적 일 수집)로 분기:
         · 어제 daily 수집됨(다른 그룹 행 존재) → 이 그룹 무활동 확정 → 0.
         · 어제 daily 미수집(새벽 07:30 전 등 전무) → 확인 불가 → None(fail-toward-hold, 안전 방향)."""
    group_hourly = (
        db.query(NaverKeywordHourly.clk, NaverKeywordHourly.hour)
        .filter(
            NaverKeywordHourly.ad_date == yesterday,
            NaverKeywordHourly.entity_type == "adgroup",
            NaverKeywordHourly.entity_id == adgroup_id,
        )
        .all()
    )
    if group_hourly:  # ① 이 유닛 스윕됨 → 시간별 창 합 신뢰
        return sum(int(clk) for clk, hour in group_hourly if hour >= from_hour)

    # ② 이 그룹 시간별 부재 → 어제 daily 증거로 교차확인.
    group_daily = (
        db.query(NaverAdDaily.clk)
        .filter(
            NaverAdDaily.ad_date == yesterday,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .all()
    )
    if group_daily:
        total = sum(int(c) for (c,) in group_daily)
        return None if total > 0 else 0  # clk>0=시간별만 누락(hold) / clk=0=확정 무클릭
    # 이 그룹 어제 daily 행 없음 → 어제 daily가 수집됐는지(날짜-레벨) 확인.
    any_daily = (
        db.query(NaverAdDaily.id)
        .filter(
            NaverAdDaily.ad_date == yesterday,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .first()
    )
    if any_daily is not None:
        return 0  # 어제 daily 수집됨·이 그룹 부재 = 무활동 확정
    return None  # 어제 daily 미수집(새벽 등) → 확인 불가 → fail-toward-hold


def _exploration_weighted_rank(buckets: list[dict]) -> Decimal | None:
    """imp-가중 avg_rank(rank None 버킷 제외) — _probe_window_stats와 동일 가중 규약.
    rank 관측 버킷의 imp 합이 0이면 None(관측 근거 없음)."""
    rank_imp = sum(h["imp"] for h in buckets if h.get("avg_rank") is not None)
    if rank_imp <= 0:
        return None
    weighted = sum(
        Decimal(str(h["avg_rank"])) * h["imp"] for h in buckets if h.get("avg_rank") is not None
    )
    return weighted / Decimal(rank_imp)


def _exploration_observe(db: Session, adgroup_id: str, curve: list[dict], now: datetime, last_step: dict | None) -> dict:
    """시간별 관측 조립(PLAN §2) — 오늘 hh24 curve(+어제 시간별)에서 래더/적응 스텝 원료를 뽑는다.
    - since(직전 스텝 이후 사이클): (step_hour, now.hour) **완료 버킷만**(step_hour 버킷 제외 —
      codex P2: 스텝 시각 버킷은 스텝 前 클릭이 섞여 false stop·기울기 오염). 직전 스텝이 오늘이
      아니면 step_hour=-1(오늘 전체 [0, now.hour) 포함).
    - rank_before(적응 스텝 Δ순위/Δ입찰 기울기용): [0, step_hour) 완료 버킷 가중 avg_rank
      (직전 스텝이 오늘·step_hour>0일 때만 — 스텝 전 순위).
    - flow_clk(**롤링 24h** 정체 신호, codex P1 자정 리셋 수정): 오늘 [0, now.hour) + 어제
      [now.hour, 23] 완료 버킷 clk 합. 어제 부분은 NaverKeywordHourly(어제 스윕). 어제 확정 불가
      (스윕 미가동)면 flow_available=False → fail-toward-hold. 반환 {"since","rank_before",
      "flow_clk","flow_available"}."""
    if (last_step is not None and last_step.get("changed_at") is not None
            and last_step["changed_at"].date() == now.date()):
        step_hour = last_step["changed_at"].hour
    else:
        step_hour = -1  # 오늘 스텝 없음 → 오늘 전체 완료분 포함(0시 버킷도)
    since = [h for h in curve if step_hour < h["hour"] < now.hour]  # step_hour 버킷 제외(codex P2)
    since_stats = {
        "imp": sum(h["imp"] for h in since),
        "clk": sum(h["clk"] for h in since),
        "cost": sum(h["cost"] for h in since),
        "avg_rank": _exploration_weighted_rank(since),
    }
    if last_step is not None and step_hour > 0:
        rank_before = _exploration_weighted_rank([h for h in curve if h["hour"] < step_hour])
    else:
        rank_before = None

    # 롤링 24h 흐름: 오늘 [0, now.hour) 완료분 + 어제 [now.hour, 23](24-now.hour 시간 = 24h 채움).
    today_flow = sum(h["clk"] for h in curve if 0 <= h["hour"] < now.hour)
    yesterday_flow = _exploration_yesterday_flow(db, adgroup_id, now.date() - timedelta(days=1), now.hour)
    if yesterday_flow is None:
        flow_clk, flow_available = today_flow, False  # 어제 확정 불가 — fail-toward-hold
    else:
        flow_clk, flow_available = today_flow + yesterday_flow, True
    return {"since": since_stats, "rank_before": rank_before, "flow_clk": flow_clk, "flow_available": flow_available}


# P4 밴드 동적화(D-NAO-85 §4-3) deep 확장 게이트 — EX 압력 갭 상수 재사용(§4-3 "재사용 가능하면
# 재사용"). 값의 단일 진실은 expansion_pressure.EX_PRESSURE_RATIO(=1.25).
_EX_DEEP_RATIO = expansion_pressure.EX_PRESSURE_RATIO


def _deep_expansion_ok(
    db: Session, campaign_id: str, adgroup_id: str, today: date,
    settled_clk: int, response_priors: dict | None,
) -> bool:
    """P4 밴드 동적화(D-NAO-85 §4-3) deep_ok 산출 — 셋 다 충족 시 True, 하나라도 미충족·판정
    불가·보정계수 unavailable이면 False(정적 밴드 유지, fail-closed):
      ① 그룹 자기 정착창 clk ≥ _MIN_CLICK_FOR_APPROVAL(10) — 자기 표본 충분.
      ② 정착창 보정ROAS ≥ bep_roas × _EX_DEEP_RATIO(1.25) — 한계 여유 실증.
      ③ bid_rank_slope 프라이어 존재(load_response_priors) — 한계 반응 학습 중.
    ①(cost·correction 무관한 값 비교)을 먼저 봐 대부분의 콜드 후보를 조기 반환한다(불필요 DB 접근
    회피 — 탐색 후보는 정착 clk<10이라 통상 여기서 False)."""
    if settled_clk < _MIN_CLICK_FOR_APPROVAL:
        return False
    if not response_priors or response_priors.get(f"adgroup:{adgroup_id}") is None:
        return False
    bep_roas = exploration.resolve_exploration_bep_roas(db, campaign_id, adgroup_id)
    if bep_roas is None or Decimal(str(bep_roas)) <= 0:
        return False
    window_from, window_to = _settlement_window(today)
    agg = _settlement_agg(db, "adgroup", adgroup_id, window_from, window_to)
    if agg["cost"] <= 0:
        return False
    factor_info = diagnosis.correction_factor(db, today - timedelta(days=1))
    if factor_info.get("source") != "actual_revenue_ratio":
        return False  # 보정계수 unavailable → 무보정 추정으로 과열 진입 금지(fail-closed)
    factor = Decimal(str(factor_info["factor"]))
    scored = gave_score.compute_gave_score(
        revenue=Decimal(agg["conv_amt"]) * factor, cost=agg["cost"], bep_roas=Decimal(str(bep_roas)),
    )
    ratio = scored["roas_ratio"]
    return ratio is not None and ratio >= _EX_DEEP_RATIO


def _run_exploration_for_campaign(
    db: Session, campaign_id: str, window_from: date, window_to: date,
    now: datetime, fetch_intraday, result: dict, response_priors: dict | None = None,
) -> None:
    """탐색-UP 레인(핫셋 여집합 SHOPPING 그룹) — PLAN §2 구조. 캠페인 1개의 후보를 순회하며
    트리거→관측→래더→발사(레버 맞춤: source='ad'→소재입찰 explore_op / source='group'→그룹입찰
    explore_op). 실쓰기는 explore_op 자동 경로만(B3 Confirm 경계 유지). 손실고삐 발동 캠페인은
    호출측이 제외(봉투#5) — 여기선 후보별 트리거·경제성 상한·킬스위치가 최종 방어선.

    VT3(D-NAO-82②): 후보 순회 전 ctr_alert(SA)를 캠페인당 1회 호출해 CTR 경보 활성 그룹
    집합을 구한다 — 그 그룹은 순회에서 즉시 skip(밴드 도달+CTR 경보=추가 UP 무의미, 사람
    처방 대상). 차단 범위는 이 탐색 래더뿐(시간당 밴드 레인·핫셋 레인·vitality 소생 불변)."""
    today = now.date()
    candidates = exploration.exploration_candidates(db, campaign_id, window_from, window_to)
    # VT4(D-NAO-82①): 수요 우선 재정렬 — 플로어(≤100원)·판정 표본 미달 그룹을 최근 7일 노출
    # 내림차순으로 앞에 세운다(03 아이폰17프로 노출 34% 방치 발견). 봉투·스텝·상한·쿨다운·발동
    # 조건 전부 불변("순서만"). 원소·개수 동일 — 다운스트림 순회 로직 무영향.
    candidates = exploration.prioritize_candidates(db, campaign_id, candidates, window_from, window_to)
    # BM P4(D-NAO-78): 대행사 고성과 SHOPPING 그룹 입찰밴드 p50을 콜드 탐색 초기입찰 프라이어로
    # 1회 조회(후보 순회 밖·N+1 방지). 후보는 전부 SHOPPING(exploration_candidates 게이트).
    # bid_band_anchor 자체 fail-open → 부재/실패 시 None → adaptive_step 기존 콜드스타트 폴백(회귀 0).
    try:
        from app.services.naver_ad import bm_benchmark
        bm_bid_anchor = bm_benchmark.bid_band_anchor(db, exploration._EXPLORATION_CAMPAIGN_TYPE)
    except Exception as e:  # noqa: BLE001 — 프라이어 조회 실패는 None(탐색 레인 무영향)
        log.warning("auto_operator: 탐색 BM 입찰밴드 프라이어 조회 실패(None 폴백): %s", e)
        bm_bid_anchor = None
    # VT3(D-NAO-82②): 소재 CTR 경보 — 캠페인당 1회 재산출(순회 밖, N+1 방지, PLAN §4.1).
    # 경보 활성 그룹은 아래 루프에서 skip(밴드 도달+CTR 경보=더 올려도 헛돎 — 사람 처방 대상).
    # codex 1R P1-2: 정지 신호(CTR 경보) 산출 실패 시 fail-open(게이트 없이 진행)이 아니라 캠페인
    # 레인 보류(fail-soft 전파)로 바꾼다 — 예외를 잡지 않고 그대로 올려 호출측(run_hourly_lane의
    # 캠페인 try/except)이 캠페인 단위로 잡아 그 캠페인 탐색 레인 전체가 이번 런을 쉬게 한다
    # (실쓰기 0 = 안전 방향, 기존 daily 손실상태 체크와 동일 관례). 경보 산출이 죽었는데 게이트만
    # 건너뛰고 탐색 UP을 계속 쏘면 "사람 처방 대상" 그룹에 헛발사가 나가므로 fail-open은 위험.
    ctr_signals = ctr_alert.detect_ctr_alerts(db, campaign_id, now=now)
    ctr_alerted_groups = {a["adgroup_id"] for a in ctr_signals.get("alerts", [])}
    for _etype, adgroup_id in candidates:
        settled_clk = exploration._settlement_clk(db, adgroup_id, window_from, window_to)
        last_step = _exploration_last_step(db, adgroup_id)
        last_step_at = last_step["changed_at"] if last_step else None

        # 트리거: 클릭 표본 부족 ∧ 쿨다운 2h(tz 계약 — last_step_at·now 둘 다 KST). 미발동은
        # 조용히 skip(사이클 대기·표본 충분 = 관찰 소음, 일기 미기록).
        fire, _reason = exploration.exploration_trigger({"clk": settled_clk}, last_step_at, now)
        if not fire:
            continue

        # VT3(D-NAO-82②) CTR 경보 skip — 트리거 통과 **후**에 판정·기록한다(GATE 재배치):
        # 탐색 레인은 시간당이라 트리거 앞에 두면 경보 그룹마다 매시 blocked 행이 쌓여
        # _record_blocked의 소음 차단 원칙("의도된 액션이 차단된 것만")을 위반한다 — 손실상태
        # 제외 블록과 동일 위치 관례(트리거 발동=액션 의도 성립 시에만 기록).
        if adgroup_id in ctr_alerted_groups:
            result["held"].append({
                "target_id": adgroup_id, "reason": f"[탐색] {_CTR_ALERT_LADDER_SKIP_REASON}",
            })
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE,
                            reason=_CTR_ALERT_LADDER_SKIP_REASON, now=now,
                            target_type="adgroup", target_id=adgroup_id, adgroup_id=adgroup_id,
                            action="bid_up")
            continue

        # GATE P2-risk6(가드5 완성): 활성 daily 손실 상태(스톱로스/floored/바닥손실) 그룹 제외 —
        # 어제 daily 고삐가 조인 그룹을 오늘 탐색 UP으로 역전하지 않는다(DL과 동일 보드·신선도).
        loss_reason = _exploration_daily_loss_reason(db, adgroup_id, today)
        if loss_reason is not None:
            result["held"].append({"target_id": adgroup_id, "reason": f"[탐색] daily 손실상태 제외 — {loss_reason}"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE,
                            reason=f"daily 손실상태 제외 — {loss_reason}", now=now,
                            target_type="adgroup", target_id=adgroup_id, adgroup_id=adgroup_id,
                            action="bid_up")
            continue

        try:
            curve = fetch_intraday(adgroup_id, today)
        except Exception as e:  # noqa: BLE001 — intraday 실패 skip(핫셋 레인과 동형)
            result["skipped"] += 1
            log.warning("auto_operator: 탐색 레인 intraday 조회 실패 target=%s: %s", adgroup_id, e)
            continue

        current_group_bid = _live_current_bid("adgroup", adgroup_id)
        if current_group_bid is None:
            result["held"].append({"target_id": adgroup_id, "reason": "탐색: 라이브 현재가 재조회 실패"})
            continue

        # 레버 맞춤 라우팅(effective_bid) — source='ad'면 소재입찰, 'group'이면 그룹입찰을 스텝한다.
        try:
            eff = effective_bid.adgroup_effective_bid(db, adgroup_id, current_group_bid)
        except Exception as e:  # noqa: BLE001 — 파생 실패는 그룹입찰 폴백(fail-safe)
            log.warning("auto_operator: 탐색 실효입찰 파생 실패 adgroup=%s: %s", adgroup_id, e)
            eff = None
        exec_target_type, exec_target_id, step_base = "adgroup", adgroup_id, current_group_bid
        if eff is not None and eff["source"] == "ad" and eff.get("max_ad_id"):
            try:
                ad_live_bid = naver_sa_writer.get_ad_bid(eff["max_ad_id"])
            except Exception as e:  # noqa: BLE001 — 소재 재조회 실패는 hold(fail-closed)
                log.warning("auto_operator: 탐색 소재 입찰 재조회 실패 ad=%s: %s", eff["max_ad_id"], e)
                ad_live_bid = None
            if ad_live_bid is None:
                result["held"].append({"target_id": adgroup_id, "reason": "탐색: 소재 입찰 라이브 재조회 실패"})
                continue
            exec_target_type, exec_target_id, step_base = "ad", eff["max_ad_id"], ad_live_bid

        # VF(D-NAO-83): 증거 구매 창 판정(무테이블 파생) — 정착창 clk는 위에서 산출한 값 재사용
        # (원칙18-8·재계산 회피). 창 활성 그룹만 콜드 상한을 캠페인 90일 실측 RPC로 해방한다.
        win = visibility.evidence_window(db, adgroup_id, campaign_id, today, settlement_clk=settled_clk)
        ev_ceiling = None
        if win["active"]:
            bep_roas = exploration.resolve_exploration_bep_roas(db, campaign_id, adgroup_id)
            ev_ceiling = visibility.evidence_ceiling(db, campaign_id, bep_roas, step_base, today)

        # 경제성 상한(유일 가격 브레이크) — 스텝 기준가 기반. 증거창 활성∧대체 상한 산출 시
        # 그 상한을 주입(콜드 순환 해방), 아니면 레거시 경제성 상한(회귀 0).
        ceiling = exploration.exploration_ceiling(
            db, campaign_id, adgroup_id, step_base, window_from, window_to,
            evidence_ceiling_value=(ev_ceiling if win["active"] and ev_ceiling is not None else None),
        )

        obs = _exploration_observe(db, adgroup_id, curve, now, last_step)
        current_rank = obs["since"]["avg_rank"]
        # ladder 존재 프로브: 직전 탐색 스텝이 있으면 not None(상태 기계 'start' 여부만 결정).
        ladder_probe = None
        if last_step is not None:
            ladder_probe = {"bid": last_step["before_bid"], "rank": obs["rank_before"]}
        # codex P1 기울기 연속성: 직전 성공 스텝의 대상(target_type/id)이 이번 발사 대상과 같고
        # 현재 라이브 입찰 == 직전 after_bid일 때만 Δ순위/Δ입찰 기울기를 쓴다 — 레버 전환(ad↔group)·
        # 외부 수동 변경으로 오염된 기울기로 30% 풀스텝이 나가는 것 차단(불일치 → slope 폐기·보수 10%).
        continuity_ok = (
            last_step is not None
            and last_step.get("target_type") == exec_target_type
            and last_step.get("target_id") == exec_target_id
            and last_step.get("after_bid") is not None
            and step_base == last_step["after_bid"]
        )
        slope_probe = {"bid": last_step["before_bid"], "rank": obs["rank_before"]} if continuity_ok else None
        # P4 밴드 동적화(D-NAO-85 §4-3): 자기 표본·보정ROAS·slope 증거가 충분한 그룹은 정적 2.5
        # 밴드 천장을 해제하고 상향 지속(한계 ROAS→BEP 동적 경계). 기본 False → 기존 판정 불변.
        deep_ok = _deep_expansion_ok(db, campaign_id, adgroup_id, today, settled_clk, response_priors)
        verdict, vreason = exploration.ladder_judgment(
            ladder_probe, obs["since"], ceiling, step_base,
            recent_flow_clk=obs["flow_clk"], settled_clk=settled_clk, flow_available=obs["flow_available"],
            evidence_active=win["active"], deep_ok=deep_ok,
        )

        # VF(D-NAO-83): 유령 지면(rank>5)·증거창 비활성 → 스텝 금지(기록만·실쓰기 0). 시간당
        # 관측 라인으로도 수집(ghost_hold_groups) — 일 레인 브리핑과 별개(각자 자기 런에서 파생).
        if verdict == "ghost_hold":
            result["explored_ghost_hold"] += 1
            result["ghost_hold_groups"].append({
                "adgroup_id": adgroup_id, "rank": current_rank,
                "current_bid": step_base, "reason": win["reason"],
            })
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {vreason}"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE, reason=vreason,
                            now=now, target_type=exec_target_type, target_id=exec_target_id,
                            adgroup_id=adgroup_id, action="bid_up")
            continue
        if verdict == "capped":
            result["explored_capped"] += 1
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {vreason}"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE, reason=vreason,
                            now=now, target_type=exec_target_type, target_id=exec_target_id,
                            adgroup_id=adgroup_id, action="bid_up")
            continue
        if verdict == "not_rank":
            result["explored_not_rank"] += 1
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {vreason}"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE, reason=vreason,
                            now=now, target_type=exec_target_type, target_id=exec_target_id,
                            adgroup_id=adgroup_id, action="bid_up")
            continue
        if verdict == "stop_observe":
            result["explored_held"] += 1
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {vreason}"})
            # D-NAO-85 관측 갭②: 그동안 침묵이던 분기(매시 반복) — 상태 변화 시에만 저소음 observe 기록.
            # (17프로 그룹 10회 연속 무기록 → 라이브 원인 규명에 표적 시뮬 필요했던 침묵 분기 해소.)
            diary.write_observe_if_changed(
                db, campaign_id, actor=diary.ACTOR_EXPLORE, adgroup_id=adgroup_id,
                state=diary.OBSERVE_STOP, rationale=vreason,
                target_type=exec_target_type, target_id=exec_target_id, now=now,
            )
            continue

        # start / step_up / reactivate → 적응 스텝 발사. 기울기는 연속성 검증된 slope_probe만 사용
        # (불연속=None → 보수 10% 스텝, codex P1).
        target = exploration.adaptive_step(
            step_base, current_rank, exploration._EXPLORATION_TARGET_BAND, slope_probe,
            exploration._EXPLORATION_STEP_PCT, bm_prior=bm_bid_anchor, deep_ok=deep_ok,
        )
        if target is None:
            # 밴드 내(도달)·스텝 소실 — 상향 없음(관찰).
            result["explored_held"] += 1
            _band_reason = "적응 스텝 소실(밴드 내/미세) — 관찰"
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {_band_reason}"})
            # D-NAO-85 관측 갭②: stop_observe와 동형 침묵 분기(밴드 도달 정상 상태) — 저소음 관측 기록.
            diary.write_observe_if_changed(
                db, campaign_id, actor=diary.ACTOR_EXPLORE, adgroup_id=adgroup_id,
                state=diary.OBSERVE_BAND, rationale=_band_reason,
                target_type=exec_target_type, target_id=exec_target_id, now=now,
            )
            continue
        target = min(target, ceiling)  # 레인 1차 클램프(harness 쓰기-경계 하드 게이트가 재검증)
        if target <= step_base:
            result["explored_capped"] += 1
            _ceiling_reason = "상한 클램프로 스텝 소실 — 종료"
            result["held"].append({"target_id": exec_target_id, "reason": f"[탐색] {_ceiling_reason}"})
            # D-NAO-85 관측 갭②: 침묵이던 상한 클램프 분기 — 상태 변화 시 저소음 관측 기록.
            diary.write_observe_if_changed(
                db, campaign_id, actor=diary.ACTOR_EXPLORE, adgroup_id=adgroup_id,
                state=diary.OBSERVE_CEILING, rationale=_ceiling_reason,
                target_type=exec_target_type, target_id=exec_target_id, now=now,
            )
            continue

        # 킬스위치 실행 직전 재확인(핫셋 레인과 동형·즉시 정지 계약).
        if not _auto_operate_now(db, campaign_id):
            result["held"].append({"target_id": exec_target_id, "reason": "탐색: 킬스위치 OFF(실행 직전 재확인)"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_EXPLORE,
                            reason="킬스위치 OFF — auto_operate=False(탐색 실행 직전 재확인)", now=now,
                            target_type=exec_target_type, target_id=exec_target_id,
                            adgroup_id=adgroup_id, action="bid_up", event_type="kill_switch")
            continue

        rank_tag = "탐색UP·재가동" if verdict == "reactivate" else "탐색UP"
        effect_body = (
            "저볼륨 그룹 탐색 UP — 핫셋 미달(정착클릭<10) 그룹을 클릭 관측 순위대(이익 스팟 밴드 "
            "2.5~4)에 최소 입찰로 진입시키는 적응 스텝(D-NAO-70·71). 되돌림=스톱로스/BEP/손실고삐 "
            "백스톱·2h 사이클 재평가."
        )
        # expected_effect 마커: ceiling(쓰기경계 상한 재검증) 먼저, base_bid(TOCTOU, step_base) 끝에
        # (base_bid의 strict-suffix 계약 보존 — codex P1 기울기 연속성·TOCTOU). harness가 실행 직전
        # 라이브 입찰 == base_bid 재검증(외부 변경 시 fail-closed).
        expected_effect = encode_base_bid(
            encode_exploration_ceiling(effect_body, ceiling), step_base,
        )
        proposal = NaverProposal(
            proposal_type="bid_up_explore", target_type=exec_target_type, target_id=exec_target_id,
            campaign_id=campaign_id, adgroup_id=adgroup_id,
            rationale=f"[{rank_tag}] {vreason}",
            expected_effect=expected_effect,
            status="approved", target_bid=target, approval_source=exploration.APPROVAL_SOURCE_EXPLORE,
        )
        db.add(proposal)
        db.commit()

        try:
            naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
            result["explored"] += 1
        except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
            result["failed"] += 1
            log.warning("auto_operator: 탐색 레인 실행 실패 proposal_id=%s: %s", proposal.id, e)


def _vitality_daily_count(db: Session, campaign_id: str, now: datetime) -> int:
    """오늘(KST) 이 캠페인의 스파이럴 복원 발사 수 — change_log에 확정된 [스파이럴복원] update_bid
    행(dry_run=False·after_value 존재)만 센다. 이전 시간당 런의 발사 + 이번 런에서 execute가
    이미 커밋한 발사를 모두 포함(캡을 하루 전체에 걸쳐 강제, §2 봉투 ≤5/일). changed_at은
    executor가 KST naive로 심으므로 KST 자정 경계로 비교."""
    day_start = datetime.combine(now.date(), datetime.min.time())
    return (
        db.query(sqlfunc.count(NaverChangeLog.id))
        .filter(
            NaverChangeLog.campaign_id == campaign_id,
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.rationale.like(f"{VITALITY_RATIONALE_PREFIX}%"),
            NaverChangeLog.changed_at >= day_start,
        )
        .scalar()
    ) or 0


def _vitality_group_on_cooldown(db: Session, adgroup_id: str, now: datetime) -> bool:
    """같은 그룹 48h 재발사 쿨다운(§2 봉투) — [스파이럴복원] update_bid **시도** 행이 48h 내
    있으면 True. change_log 접두로 카운트(별도 상태 테이블 없음, 마이그레이션 0).

    ★GATE P2-2(재시도 폭풍 감쇠): 쿨다운은 성공(after_value 존재)뿐 아니라 실패·가드거부
    (BEP 차단 등, after_value=None) 시도 행까지 전부 센다 — after_value 필터 없음. 근거: BEP
    가드레일 차단은 시간 단위로 안 바뀌는 조건이라, 매시간 재발사 시도(라이브 GET 다발·failed
    행 누적)를 봉투 보수 우선으로 시도 자체를 48h 감쇠한다. (일일 캡 ≤5는 반대로 성공만 센다 —
    _vitality_daily_count 참조. 감쇠는 시도 기준, 캡은 실집행 기준으로 서로 다른 창을 지킨다.)"""
    since = now - timedelta(hours=_VITALITY_COOLDOWN_HOURS)
    row = (
        db.query(NaverChangeLog.id)
        .filter(
            NaverChangeLog.entity_type == "adgroup",
            NaverChangeLog.entity_id == adgroup_id,
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.rationale.like(f"{VITALITY_RATIONALE_PREFIX}%"),
            NaverChangeLog.changed_at >= since,
        )
        .first()
    )
    return row is not None


def _vitality_intraday_recovered(db: Session, campaign_id: str, now: datetime) -> bool:
    """C5(codex 1R): 당일 intraday 순위(NaverHourlySnapshot 오늘 최신 avg_rank·캠페인 grain)가
    밴드(≤4.0)로 복귀했으면 True → 그 캠페인 소생 발사 skip("이미 회복"). D-1 스파이럴 신호로
    큐에 올랐어도 당일 순위가 이미 회복됐으면 소생이 불필요·과잉이라 억제한다. 데이터 없으면
    False(진행 — D-1 신호 기준, 당일 순위 미수집·avgRnk<=0→NULL은 판정 근거 아님)."""
    row = (
        db.query(NaverHourlySnapshot.avg_rank)
        .filter(
            NaverHourlySnapshot.campaign_id == campaign_id,
            NaverHourlySnapshot.ad_date == now.date(),
            NaverHourlySnapshot.avg_rank.isnot(None),
        )
        .order_by(NaverHourlySnapshot.snapshot_hour.desc())
        .first()
    )
    if row is None or row[0] is None:
        return False
    return float(row[0]) <= _VITALITY_INTRADAY_BAND_TOP


def _fire_vitality_revive(db: Session, target: dict, now: datetime, result: dict) -> None:
    """소생 대상 그룹 1건을 기존 시간당 밴드 UP 경로로 발사(§2). 새 권한·우회 경로 없음 —
    proposal_type='bid_up'·adgroup·target_bid=_clamp_step(라이브가, up)·approval_source=
    APPROVAL_SOURCE_HOURLY·rationale 접두 [스파이럴복원], naver_execution_harness.execute()가
    BEP 가드레일·킬스위치·쿨다운·일일상한을 전량 최종 검증(가드레일 우회 절대 없음)."""
    campaign_id, adgroup_id = target["campaign_id"], target["adgroup_id"]
    # GATE P3(회복력): execute 이전 준비 구간(_live_current_bid·_record_blocked·commit)도
    # per-target try로 감싼다 — 한 타깃의 예외(라이브 GET 오류·DB 커밋 실패 등)가 남은 타깃과
    # 브리핑까지 죽이지 않게(fail-soft, 로그·held). execute 자체는 아래 별도 try가 담당.
    try:
        # 킬스위치 실행 직전 재확인(시간당 레인 동형·즉시 정지 계약).
        if not _auto_operate_now(db, campaign_id):
            result["vitality_held"].append({"target_id": adgroup_id, "reason": "킬스위치 OFF(발사 직전 재확인)"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_HOURLY,
                            reason="[스파이럴복원] 킬스위치 OFF — auto_operate=False", now=now,
                            target_type="adgroup", target_id=adgroup_id, action="bid_up",
                            event_type="kill_switch")
            return
        # C4(codex 1R, 부분 수용): 발사 직전 캡 재카운트 백스톱 — _run_vitality_step의 루프 상단
        # 캡 검사와 실제 커밋 사이 창(check-then-act)을 좁힌다. 완전 원자 예약은 하지 않는다
        # (비례성: 시간당 크론 단일·catch-up 없음이라 잔여 리스크는 수동 중복 실행뿐 — 재카운트가
        # 기존 harness 관례의 최소 백스톱). _vitality_daily_count는 커밋된 change_log만 세므로
        # 같은 런 앞선 발사도 반영된다.
        if _vitality_daily_count(db, campaign_id, now) >= _VITALITY_DAILY_CAP:
            result["vitality_held"].append(
                {"target_id": adgroup_id, "reason": "캠페인 일일 캡(발사 직전 재카운트) 도달"}
            )
            return
        current_bid = _live_current_bid("adgroup", adgroup_id)
        if current_bid is None:
            result["vitality_held"].append({"target_id": adgroup_id, "reason": "라이브 현재가 재조회 실패"})
            _record_blocked(db, campaign_id=campaign_id, actor=diary.ACTOR_HOURLY,
                            reason="[스파이럴복원] 라이브 현재가 재조회 실패", now=now,
                            target_type="adgroup", target_id=adgroup_id, action="bid_up")
            return
        step_bid = _clamp_step(current_bid, "up")
        if step_bid is None:
            result["vitality_held"].append({"target_id": adgroup_id, "reason": "UP 스텝 소실(상한 클램프)"})
            return
        proposal = NaverProposal(
            proposal_type="bid_up", target_type="adgroup", target_id=adgroup_id,
            campaign_id=campaign_id, adgroup_id=adgroup_id,
            rationale=f"{VITALITY_RATIONALE_PREFIX} {target['reason']}",
            expected_effect=(
                "스파이럴 조기 복원 — 흐름 붕괴(노출·순위 궤적 하락) 검증 그룹의 밴드 복원 방향 "
                "UP(D-NAO-81 B축). BEP 가드레일·킬스위치·쿨다운·48h 재발사 쿨다운이 백스톱."
            ),
            status="approved", target_bid=step_bid, approval_source=APPROVAL_SOURCE_HOURLY,
        )
        db.add(proposal)
        db.commit()
    except Exception as e:  # noqa: BLE001 — GATE P3 fail-soft: 준비 구간 예외는 이 타깃만 건너뛴다
        db.rollback()
        result["vitality_held"].append(
            {"target_id": adgroup_id, "reason": f"발사 준비 예외(fail-soft): {type(e).__name__}"}
        )
        log.warning("auto_operator: 스파이럴 복원 발사 준비 실패 adgroup=%s(fail-soft): %s", adgroup_id, e)
        return
    try:
        naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
        result["vitality_fired"] += 1
    except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
        result["failed"] += 1
        log.warning("auto_operator: 스파이럴 복원 발사 실패 proposal_id=%s: %s", proposal.id, e)


def _emit_vitality_briefing(
    db: Session, alerts: list[dict], now: datetime, *, revive_hold_reason: str | None = None,
) -> None:
    """경보/발사가 있던 날만 diary(observe)+Slack — PX 브리핑 관례 미러(fail-open·독립 try·
    없는 날 침묵). 호출부는 alerts 비어있지 않을 때만 부른다(무경보 무브리핑, §2).

    revive_hold_reason(C3): 부분적재 전면 보류 사유가 있으면 헤더 아래 한 줄로 표기(발사 0 이유
    명시). 캠페인별 revive_note(C2/C6 게이트 탈락 사유)는 해당 줄에 덧붙인다."""
    try:
        header = f"{now.date().isoformat()} 스파이럴 경보 {len(alerts)}캠페인 — B축 흐름 복원(D-NAO-81)"
        parts = [header]
        if revive_hold_reason:
            parts.append(f"⚠️ {revive_hold_reason}")
        for a in alerts:
            s3 = "" if a.get("s3_low_qi_ratio") is None else f"·저품질비중 {a['s3_low_qi_ratio']}"
            note = f"·{a['revive_note']}" if a.get("revive_note") else ""
            parts.append(
                f"- {a['campaign_id']}: 노출궤적 {a['imp_traj']}(누적 −{a['cum_drop_pct']}%)·"
                f"순위 {a['avg_rank']}(궤적 {a['rank_traj']})·소생후보 {a['revive_group_count']}그룹{s3}{note}"
            )
        text = "\n".join(parts)
        diary.write_diary_entry(
            db, "observe", "", actor=diary.ACTOR_HOURLY, action=ACTION_VITALITY_BRIEFING,
            rationale=text, now=now,
        )
        slack_notifier.notify_text(text, log_label="스파이럴 복원 브리핑")
    except Exception as e:  # noqa: BLE001 — 브리핑 실패는 발사(실쓰기)와 분리(fail-open)
        log.warning("auto_operator: 스파이럴 복원 브리핑 실패(fail-open): %s", e)


def _run_vitality_step(db: Session, now: datetime, result: dict) -> None:
    """VT2 vitality 스텝(§2) — vitality_signal(SA)이 낸 경보/소생 대상을 harness가 소비해
    발사한다(원칙18-6 허브). 경보 없으면 완전 무동작·무브리핑. 발사는 봉투(캠페인당 ≤5/일·
    같은 그룹 48h 쿨다운) 안에서만, 기존 시간당 밴드 UP 경로로. fail-soft: 이 스텝의 예외가
    핫셋 레인 집행 결과를 오염시키지 않는다(bleed 밸브 관례 동형)."""
    signals = vitality_signal.detect_spirals(db, now=now)
    alerts = signals["alerts"]
    result["vitality_alerts"] = len(alerts)
    if not alerts:
        return  # 무경보 = 무동작·무브리핑(§2)
    revive_hold_reason = signals.get("revive_hold_reason")  # C3: 부분적재 전면 보류(전역)
    breaker_cache: dict[str, str | None] = {}  # C1: 캠페인당 서킷브레이커 1회 평가 캐시
    for target in signals["revive_targets"]:
        campaign_id, adgroup_id = target["campaign_id"], target["adgroup_id"]
        # C1(codex 1R): 발사 전 캠페인 소진 서킷브레이커 재사용 — 오늘 스냅샷 부재/스테일이면
        # 그 캠페인 소생 발사 전면 hold(fail-closed). 경보·브리핑은 유지, 발사만 차단·사유 로그.
        if campaign_id not in breaker_cache:
            breaker_cache[campaign_id] = _check_spend_circuit_breaker(db, campaign_id, now)
        breaker_reason = breaker_cache[campaign_id]
        if breaker_reason is not None:
            result["vitality_held"].append(
                {"target_id": adgroup_id, "reason": f"소진 서킷브레이커 hold — {breaker_reason}"}
            )
            continue
        # C5(codex 1R): 당일 intraday 순위가 밴드(≤4.0)로 복귀했으면 skip(이미 회복).
        if _vitality_intraday_recovered(db, campaign_id, now):
            result["vitality_held"].append(
                {"target_id": adgroup_id, "reason": "당일 순위 회복(intraday ≤4.0) — 발사 skip"}
            )
            continue
        if _vitality_daily_count(db, campaign_id, now) >= _VITALITY_DAILY_CAP:
            result["vitality_held"].append({"target_id": adgroup_id, "reason": "캠페인 일일 캡(≤5) 초과"})
            continue
        if _vitality_group_on_cooldown(db, adgroup_id, now):
            result["vitality_held"].append({"target_id": adgroup_id, "reason": "48h 재발사 쿨다운"})
            continue
        _fire_vitality_revive(db, target, now, result)
    _emit_vitality_briefing(db, alerts, now, revive_hold_reason=revive_hold_reason)


def _bp_fire(
    db: Session, *, campaign_id: str, proposal_type: str, target_budget: int,
    rationale: str, expected_effect: str, now: datetime, result: dict,
    ok_key: str, fail_key: str,
) -> bool:
    """BP(D-NAO-102) 예산 쓰기 1건 — 제안 생성→승인→naver_execution_harness.execute 경유.

    새 쓰기 경로를 만들지 않는다(원칙18-6 초크포인트): guardrail_gate._check_budget이
    +100%캡·스톱로스·BEP 이익하한·쿨다운2h·일일상한을 실행 직전에 다시 검증하고, harness가
    킬스위치 최종 가드(approval_source=pace_op)를 건다. 여기서 하는 추가 방어는 승인 직전
    킬스위치 재확인 하나(다른 레인과 동일 계약)뿐이다.

    NAVER_BP_DRY_RUN=1이면 execute(dry_run=True) — 실쓰기 없이 change_log에 제안만 남긴다.
    """
    if not _auto_operate_now(db, campaign_id):
        hold_reason = "킬스위치 OFF — auto_operate=False(BP 실행 직전 재확인)"
        result["budget_pacing_held"].append({"campaign_id": campaign_id, "reason": hold_reason})
        _record_blocked(
            db, campaign_id=campaign_id, actor=diary.ACTOR_PACING, reason=hold_reason,
            now=now, target_type="campaign", target_id=campaign_id,
            action=proposal_type, event_type="kill_switch",
        )
        return False

    proposal = NaverProposal(
        proposal_type=proposal_type, target_type="campaign", target_id=campaign_id,
        campaign_id=campaign_id, rationale=rationale, expected_effect=expected_effect,
        status="pending", target_budget=target_budget,
        # 라운드 봉투 분류는 BP 레인이 이미 apply_round_cap으로 소비했다(초과분은 제안 자체를
        # 만들지 않는다) — 여기까지 온 건 전부 자율분.
        budget_auto_eligible=True,
    )
    db.add(proposal)
    db.flush()
    proposal.status = "approved"
    proposal.approval_source = APPROVAL_SOURCE_PACING
    db.commit()

    dry_run = budget_pacing.dry_run_enabled()
    try:
        naver_execution_harness.execute(db, proposal.id, dry_run=dry_run, now=now)
        if dry_run:
            result["budget_pacing_dry_run"] += 1
        else:
            result[ok_key] += 1
        return True
    except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
        result[fail_key] += 1
        log.warning(
            "auto_operator: BP %s 실행 실패 campaign=%s proposal_id=%s: %s",
            proposal_type, campaign_id, proposal.id, e,
        )
        return False


def _run_budget_pacing_restore(db: Session, now: datetime, result: dict) -> None:
    """BP 익일 원복 — 어제 이전 BP 증액분을 base_daily_budget으로 되돌린다(D-NAO-102 ⑤).

    00:05 전용 잡이 아니라 **멱등 판정**이라 시간당 레인도 같은 함수를 호출한다(00:05 잡이
    죽어도 다음 정시가 따라잡는 자가치유). 감액이라 guardrail은 방향·클램프만 본다."""
    for cand in budget_pacing.restore_candidates(db, now=now):
        result["budget_pacing_restore_reviewed"] += 1
        rationale = f"{budget_pacing.BUDGET_PACING_RESTORE_PREFIX} {cand['reason']}"
        _bp_fire(
            db, campaign_id=cand["campaign_id"], proposal_type="budget_down",
            target_budget=cand["base_budget"], rationale=rationale,
            expected_effect=(
                "BP 익일 원복 — 장중 페이싱 증액분을 그날 기준(base) 예산으로 복귀"
                "(감액은 가드레일 면제 대상이라 방향·클램프만 검증)."
            ),
            now=now, result=result,
            ok_key="budget_pacing_restored", fail_key="budget_pacing_restore_failed",
        )


def _run_budget_pacing_lane(db: Session, now: datetime, result: dict) -> None:
    """BP 장중 증액 레인(D-NAO-102) — auto_operate 전 캠페인 전역 레인(캠페인 하드코딩 없음).

    ①익일 원복 자가치유 ②budget_pacing.evaluate 판정(소진율≥90% ∧ 프록시ROAS≥target)
    ③회당 라운드 봉투(≤10만) 그리디 배정 ④소진 서킷브레이커 통과분만 ⑤제안·승인·execute.

    ★소진 서킷브레이커(§4-6, 당일 소진 > 직전7일 일평균×3)를 증액에도 적용한다 — 이미
    폭주 중인 캠페인의 천장을 더 여는 것은 브레이커의 취지와 정면 충돌한다.
    ★base 시드는 판정 결과(base_seeded)를 하니스가 반영한다(SA는 DB 쓰기 금지).
    """
    _run_budget_pacing_restore(db, now, result)

    decisions = budget_pacing.evaluate(db, now=now)
    if not decisions:
        return

    # base 시드/재시드 반영(오늘 BP 증액이 없던 캠페인은 현재 예산이 그날 기준값).
    seeded = [d for d in decisions if d.get("base_seeded") and d.get("base_budget")]
    if seeded:
        for d in seeded:
            db.query(NaverCampaignSettings).filter(
                NaverCampaignSettings.campaign_id == d["campaign_id"]
            ).update({"base_daily_budget": int(d["base_budget"])}, synchronize_session=False)
        db.commit()

    # 트리거 미달·uncapped 같은 일상 관찰은 일기에 남기지 않는다(_record_blocked 선별 규약 —
    # 매시×캠페인 hold를 전부 적으면 일기가 소음으로 매몰된다). 결과 dict에만 집계.
    result["budget_pacing_reviewed"] += len(decisions)
    candidates = [d for d in decisions if d["needs_raise"]]
    if not candidates:
        return

    # 이번 런의 총 증액 한도 = min(회당 라운드 봉투 10만, 계정 일일 잔여 10만 − 오늘 집행분).
    # 회당 캡만으로는 매시 발동이 누적돼 하루 총액이 무한정 커진다(리뷰 P2-4).
    raised_amount = budget_pacing.raised_amount_today(db, today=now.date())
    daily_left = budget_pacing.DAILY_ACCOUNT_RAISE_CAP - raised_amount
    run_cap = min(budget_pacing.ROUND_BUDGET_CAP, daily_left)
    result["budget_pacing_daily_left"] = daily_left
    if run_cap <= 0:
        hold_reason = (
            f"계정 BP 일일 총량 캡 소진 — 오늘 이미 {raised_amount}원 증액"
            f"(한도 {budget_pacing.DAILY_ACCOUNT_RAISE_CAP}원)"
        )
        for d in candidates:
            result["budget_pacing_held"].append(
                {"campaign_id": d["campaign_id"], "reason": hold_reason}
            )
            _record_blocked(
                db, campaign_id=d["campaign_id"], actor=diary.ACTOR_PACING, reason=hold_reason,
                now=now, target_type="campaign", target_id=d["campaign_id"], action="budget_up",
            )
        return

    budget_pacing.apply_round_cap(candidates, cap=run_cap)

    for d in candidates:
        campaign_id = d["campaign_id"]
        if not d.get("round_eligible", True):
            hold_reason = (
                f"이번 회차 증액 한도 초과(한도 {run_cap}원 = min(회당 봉투 "
                f"{budget_pacing.ROUND_BUDGET_CAP}, 계정 일일 잔여 {daily_left})) — 배정 불가"
                f"(다음 정시 재평가). 목표 {d['target_budget']}원/현재 {d['current_budget']}원"
            )
            result["budget_pacing_held"].append({"campaign_id": campaign_id, "reason": hold_reason})
            _record_blocked(
                db, campaign_id=campaign_id, actor=diary.ACTOR_PACING, reason=hold_reason,
                now=now, target_type="campaign", target_id=campaign_id, action="budget_up",
            )
            continue

        breaker_reason = _check_spend_circuit_breaker(db, campaign_id, now)
        if breaker_reason:
            hold_reason = f"소진 서킷브레이커 — 증액 금지: {breaker_reason}"
            result["budget_pacing_held"].append({"campaign_id": campaign_id, "reason": hold_reason})
            _record_blocked(
                db, campaign_id=campaign_id, actor=diary.ACTOR_PACING, reason=hold_reason,
                now=now, target_type="campaign", target_id=campaign_id, action="budget_up",
            )
            continue

        _bp_fire(
            db, campaign_id=campaign_id, proposal_type="budget_up",
            target_budget=int(d["target_budget"]),
            rationale=f"{budget_pacing.BUDGET_PACING_PREFIX} {d['reason']}",
            expected_effect=(
                "예산 페이싱 자동 증액(D-NAO-102) — 당일 소진 속도로 자정까지 필요분을 추정해 "
                "예산 소진에 의한 조기 정지를 막는다. 성과 판정은 스마트스토어 실주문 기반 "
                "상한 프록시(광고 귀속 아님)이며, 실집행 방어선은 guardrail_gate(BEP·스톱로스·"
                "+100%캡·쿨다운)."
            ),
            now=now, result=result,
            ok_key="budget_pacing_raised", fail_key="budget_pacing_failed",
        )


def run_budget_pacing_reset_lane(db: Session, *, now: datetime | None = None) -> dict:
    """BP 익일 원복 전용 공개 엔트리 — 00:05 크론(run_naver_budget_pacing_reset_job)이 호출.

    시간당 레인도 같은 판정 함수를 태우므로(자가치유) 이 잡이 죽어도 다음 정시가 따라잡는다
    — catch-up 목록(아침배치 전용 체인)에 넣지 않는 이유."""
    now = now or kst_now()
    result = {
        "budget_pacing_restore_reviewed": 0, "budget_pacing_restored": 0,
        "budget_pacing_restore_failed": 0, "budget_pacing_dry_run": 0,
        "budget_pacing_held": [],
    }
    _run_budget_pacing_restore(db, now, result)
    return result


def run_hourly_lane(db: Session, *, now: datetime | None = None, fetch_intraday=None) -> dict:
    """시간당 밴드 관제 실입찰(PLAN §4). 매시 :20 크론(catch-up 제외 — 시간성 소멸).

    ①캠페인별 소진 서킷브레이커(§4-6) → 걸리면 그 캠페인 전체 hold ②핫셋 선정(§4-1)
    ③유닛별 intraday hh24 곡선 조회(§4-2, 실패 시 skip) ④판정(_judge_hourly, ★IU D-NAO-66:
    순위 제한 폐지 — CPC 급등 DOWN + RL3 장중 loss 고삐 DOWN[추정ROAS<BEP] + UP(장중 tally
    OR 정착창 실측 ROAS) ∧ 예산 여력, 순위 무관) ⑤스텝 제안 생성+즉시 승인(approval_source=
    APPROVAL_SOURCE_HOURLY) +naver_execution_harness.execute() 경유 실행(가드레일 전량 통과
    필요 — 쿨다운 2h·일일상한·BEP 하한·스톱로스가 최종 방어선).

    fetch_intraday 미주입 시 fetch_entity_hh24(테스트 주입, 원칙18-8 — keyword_hourly_sweep과
    동일 관례). stat_date=오늘(now.date())로 호출하면 timeRange since=until=오늘이 되어
    이미 당일 조회가 가능하다 — datePreset="today" 하위호환 확장은 불필요로 판단(최종보고 명시).

    D-NAO-58 CD2: 밴드 판정이 hold인 사각지대에서 _probe_trigger가 참이면 up 탐침으로 치환
    (기존 up 경로 그대로 통과 — 라이브 현재가 재조회·_clamp_step·킬스위치 재확인·execute).
    탐침 제안만 approval_source=probe_op·rationale [클릭탐침]로 태그(diary probe actor).

    D-NAO-60 RL5(CD5): 탐침이 참이어도 `_learned_optimal_skip`이 그 캠페인 env_cell의 학습된
    최적 순위밴드(probe_learning_loop.learned_probe_rank)에 이미 도달했다고 판정하면 up
    승격을 취소하고 hold로 남긴다(과climb 방지) — guardrail 우회는 없음(통과분만 기존
    execute() 경로).

    D-NAO-58 CD3 Stage 1: 레인 말미에 probe_revert.run_bleed_valve로 당일 standing probe의
    실시간 출혈(비용×3 급등∧즉시구매0)을 회수한다(lazy import·fail-soft — 밸브 실패가 레인
    집행 결과를 오염시키지 않는다).

    반환: {"reviewed", "approved", "executed", "held": [...], "skipped", "failed", "probed",
           "bleed"}.
    """
    now = now or kst_now()
    fetch_intraday = fetch_intraday or fetch_entity_hh24
    today = now.date()
    window_from, window_to = _settlement_window(today)
    # 캠페인별 **학습밴드 라벨** 캐시(run 1회 한정). 조회가 30일 aggregate라 유닛마다
    # 부르면 N+1 — 캠페인당 1회로 묶는다. None도 캐시한다("학습값 없음"도 확정된 답).
    # 하한(`_probe_rank_floor`)과 CD5(`_learned_optimal_skip`)가 **이 하나의 값을 공유**한다.
    _probe_band_cache: dict[str, str | None] = {}

    result: dict = {
        "reviewed": 0, "approved": 0, "executed": 0, "held": [], "skipped": 0, "failed": 0,
        "probed": 0,  # D-NAO-58 CD2: 탐침으로 승격된 up 제안 수(라이브 관측용)
        "ad_confirm_pending": 0,  # B3 GATE P2-2: Confirm 대기로 생성된 ad-레벨 제안 수
        "ad_confirm_pending_dup_skipped": 0,  # B3 GATE 2R P2-B: 동일 pending 존재로 skip된 수
        # D-NAO-125 레인 캡 원료. ★"예약"이다 — execute() **전에** 증가한다(codex 2R[P2]).
        # 실제 성공 수는 result["executed"]. 승인 직후 실패해도 그 자리는 이미 쓴 것으로
        # 세는데, 그 방향이 안전(첫 회차에 몰아 쏘지 않음)이라 그대로 두고 이름을 바꿨다.
        "ad_auto_exec_reserved": 0,
        "ad_auto_exec_capped": 0,  # D-NAO-125 codex[P1]: 레인 캡 초과로 Confirm 대기로 강등된 수
        "ad_auto_exec_inflight_skipped": 0,  # D-NAO-125 codex[P1]: 같은 소재가 실행 중이라 skip
        "servo": 0,  # IU-R R1: 서보 스텝으로 승인된 쇼검 UP 제안 수(라이브 관측용)
        "rank_direct": 0,  # IU-R R2: estimate 직행 스텝으로 승인된 파워링크 UP 제안 수(라이브 관측용)
        # B-X BX3(D-NAO-70·71) 탐색-UP 레인 카운터(라이브 관측용):
        "explored": 0,          # 탐색 UP 실쓰기(start/step_up/reactivate) 수
        "explored_capped": 0,   # 경제성 상한 도달로 종료
        "explored_not_rank": 0,  # rank≤2.5·클릭0 = 순위 병리 아님 진단 종료
        "explored_held": 0,     # 밴드 도달/클릭 hold(상향 정지·관측)
        # VF(D-NAO-83 가시성 우선) 카운터·관측(라이브 관측용):
        "explored_ghost_hold": 0,  # 유령 지면(rank>5)·증거창 비활성 → 스텝 금지
        "ghost_hold_groups": [],   # 유령∧창 비활성 그룹 관측 라인(adgroup/rank/bid/사유)
        # VT2(D-NAO-81 B축 스파이럴 복원) 카운터(라이브 관측용):
        "vitality_alerts": 0,   # S1∧S2 스파이럴 경보 캠페인 수
        "vitality_fired": 0,    # 복원 UP 실쓰기(execute 성공) 수
        "vitality_held": [],    # 캡·쿨다운·킬스위치·재조회실패로 미발사된 그룹
        # BP(D-NAO-102 예산 페이싱) 카운터(라이브 관측용):
        "budget_pacing_reviewed": 0,  # 판정한 auto_operate 캠페인 수
        "budget_pacing_raised": 0,    # 증액 실쓰기 성공 수
        "budget_pacing_failed": 0,    # 증액 실행 실패(가드레일 차단 포함)
        "budget_pacing_dry_run": 0,   # NAVER_BP_DRY_RUN=1로 제안만 기록한 수
        "budget_pacing_held": [],     # 회차/일일 캡 초과·서킷브레이커·킬스위치로 미발사
        "budget_pacing_daily_left": None,  # 계정 BP 일일 총량 캡 잔여(원, 관측용)
        "budget_pacing_restore_reviewed": 0,  # 익일 원복 후보 수
        "budget_pacing_restored": 0,          # 원복 실쓰기 성공 수
        "budget_pacing_restore_failed": 0,    # 원복 실행 실패
    }
    # IU-R R2: estimate 회당 캡·런 캐시(§난제4) — 실제 스텝 유닛에만 호출하고 (kw_id,position)
    # 중복은 캐시로 흡수. counter는 mutable dict로 helper와 공유(호출 수 봉인 테스트가 이 값 관측).
    estimate_cache: dict = {}
    estimate_counter: dict = {"n": 0}

    # IU-R R1(PLAN §2, 원칙18-6 허브): 서보 경제성 상한 원료를 캠페인 순회 **밖에서 1회**
    # precompute한다(N+1 방지) — 정착창 계층 agg(campaign/account) + 보정계수. 함수 레벨
    # import(proposal_pipeline는 무거운 파이프라인이라 module-level 결합 회피, 순환 리스크
    # 최소화 — probe_revert lazy import 관례와 동형). auto 캠페인이 없으면 계산 자체를 건너뛴다.
    campaigns = _auto_operate_campaigns(db)
    servo_agg: dict | None = None
    servo_correction_factor: Decimal | None = None
    # IU-R R3: 서보 response_prior(입찰→순위 반응 곡선 기울기, 원/rank개선) — 캠페인 순회 밖에서
    # 1회 벌크 적재(N+1 방지). {scope_key: slope}. 서보 유닛의 "adgroup:<id>" 키만 조회해 콜드
    # 스타트 대신 "한 단 위 필요 증분"을 근접 산정한다(없으면 None → rank_servo 콜드스타트 폴백).
    response_priors: dict = {}
    if campaigns:
        from app.services.naver_ad import proposal_pipeline
        servo_agg = proposal_pipeline._precompute_aggregates(db, window_from, window_to)
        _cf = diagnosis.correction_factor(db, today - timedelta(days=1))
        servo_correction_factor = Decimal(str(_cf["factor"]))
        response_priors = bid_rank_curve.load_response_priors(db)

    for campaign_id in campaigns:
        breaker_reason = _check_spend_circuit_breaker(db, campaign_id, now)
        if breaker_reason:
            result["held"].append({"campaign_id": campaign_id, "reason": breaker_reason})
            _record_blocked(
                db, campaign_id=campaign_id, actor=diary.ACTOR_HOURLY,
                reason=breaker_reason, now=now,
            )
            continue

        # BX3(D-NAO-70·71, PLAN §1 가드5): 이 캠페인에 장중 손실고삐(is_leash DOWN)가 이번 런에서
        # 발동하면 탐색 UP을 제외한다(UP·DOWN 충돌 금지). 핫셋 루프가 이 플래그를 세우고, 탐색
        # 레인은 루프 뒤에서 이를 읽는다(같은 캠페인 순회 안에서 순서 보장 — 핫셋 먼저·탐색 나중).
        campaign_leashed = False

        for target_type, target_id in _hot_set_candidates(db, campaign_id, window_from, window_to):
            result["reviewed"] += 1

            try:
                curve = fetch_intraday(target_id, today)
            except Exception as e:  # noqa: BLE001 — §4-6 "intraday 조회 실패 → 해당 그룹 skip"
                result["skipped"] += 1
                log.warning("auto_operator: 시간당 레인 intraday 조회 실패 target=%s: %s", target_id, e)
                continue

            if not curve or sum(h["imp"] for h in curve) == 0:
                result["held"].append({"target_id": target_id, "reason": "당일 imp 없음"})
                continue

            verdict = _judge_hourly(
                db, target_type=target_type, target_id=target_id, campaign_id=campaign_id,
                curve=curve, now=now,
            )
            if verdict["direction"] == "hold":
                # D-NAO-58 CD2 클릭 탐침: 밴드 판정이 hold(액션 없음)일 때만 사각지대 평가
                # (up/down이면 이미 액션 — 이중 발동 금지). 트리거 참이면 up-의도 탐침으로 치환.
                # _probe_trigger는 clk/imp/rank를 모두 자기 2시간 창에서 산출(R1 P3-1 자기완결).
                # ★학습밴드가 있으면 탐침 하한을 학습값으로(상수 2.5가 학습 최적점 도달을
                # 막던 충돌 해소 — `_probe_rank_floor` docstring). 조회는 harness 몫이고
                # (SA는 db를 모른다, 원칙18-6) 캠페인당 1회만 — `learned_probe_rank`는
                # 내부에서 30일 aggregate를 도는 무거운 호출이라 유닛마다 부르면 N+1이 된다.
                if campaign_id not in _probe_band_cache:
                    _probe_band_cache[campaign_id] = _learned_bands_of(db, now, campaign_id)
                own_band, account_band = _probe_band_cache[campaign_id]
                # 자기 캠페인이 승격시킨 밴드가 우선. 없을 때만 계정 밴드를 빌리되,
                # **사후 고삐가 발동 가능한 유닛(BEP 확인)에만** 연다(Jino 확정 2026-07-29,
                # `_account_band_fallback_ok` docstring에 근거).
                band, band_note = own_band, ""
                if band is None and account_band is not None:
                    ok, why = _account_band_fallback_ok(db, target_type, target_id)
                    band_note = f" · {why}"
                    if ok:
                        band = account_band
                probe_fired, probe_reason = _probe_trigger(
                    curve, now, rank_floor=_probe_rank_floor(band),
                )
                if band_note:
                    probe_reason = f"{probe_reason}{band_note}"
                if probe_fired:
                    # D-NAO-60 RL5(CD5): 학습된 최적 밴드에 이미 도달했으면 상향 생략(과climb
                    # 방지). guardrail 우회 없음 — 통과한 제안도 execute() 전량을 그대로 탄다.
                    skip, skip_reason = _learned_optimal_skip(
                        db, curve, now, campaign_id,
                        learned_band=band, band_resolved=True,
                    )
                    if skip:
                        result["held"].append({"target_id": target_id, "reason": skip_reason})
                        continue
                    verdict = {
                        "direction": "up", "reason": f"{probe_reason} · {skip_reason}", "probe": True,
                    }
                else:
                    result["held"].append({"target_id": target_id, "reason": verdict["reason"]})
                    continue

            # ★IU2(D-NAO-66) 재시작 천장(learned band) 폐지: 종전엔 일반 UP(ROAS-driven
            # band-return)에도 _learned_optimal_skip 천장을 적용해 학습밴드 도달 시 상향을
            # 취소했다. 그러나 D-NAO-66은 learned band를 하드 캡이 아니라 **탐침(CD2/CD5)
            # 프라이어**로 강등한다 — ROAS-UP 경로는 밴드를 참조하지 않는다(순위는 목표가 아니라
            # 결과). 밴드=탐침 프라이어, ROAS-UP 캡 아님(D-NAO-66). _learned_optimal_skip은 위
            # 탐침(probe) 분기 전용으로 남는다(과열 상단으로의 무근거 탐침 상향만 억제). 오버슛
            # 비용은 스텝 1개 분량 + 쿨다운 2h로 자연 캡되고, BEP 하한·loss 고삐가 최종 방어선.

            # 여기부터는 verdict가 up/down = 액션 의도 확정 — 이후의 hold는 "의도된 액션이
            # 차단된 것"이므로 일기(blocked) 기록 대상(위의 판정 hold·imp 없음은 관찰 소음이라 제외).
            is_probe = verdict.get("probe", False)
            is_leash = verdict.get("leash", False)  # D-NAO-60 RL3 — 순위 고삐(장중 loss DOWN)
            if is_leash:
                campaign_leashed = True  # BX3 봉투#5: 손실고삐 발동 캠페인 → 탐색 UP 제외
            lane_actor = diary.ACTOR_PROBE if is_probe else diary.ACTOR_HOURLY  # 차단 일기 주체
            intended_action = "bid_up" if verdict["direction"] == "up" else "bid_down"
            current_bid = _live_current_bid(target_type, target_id)
            if current_bid is None:
                hold_reason = "라이브 현재가 재조회 실패"
                result["held"].append({"target_id": target_id, "reason": hold_reason})
                _record_blocked(
                    db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                    now=now, target_type=target_type, target_id=target_id, action=intended_action,
                )
                continue
            # B2 GATE P2-2①(D-NAO-65) → B3 라우팅: 레버 미연결 그룹(실효입찰이 소재
            # bidAmt=useGroupBidAmt=false, source='ad')은 그룹입찰 스텝(고삐 DOWN·밴드 DOWN/UP)
            # 이 지출·노출에 무효이면서 ①DL1 행동 창(naver_change_log update_bid)만 리셋해 진단의
            # 미연결 스톱로스 그물(P2-1 만성 7일 창)을 늦추고 ②changes_today 일일 상한 슬롯을
            # 낭비한다. 카나리 캠페인이면 실효 레버(max 소재)의 bidAmt를 대상으로 ad-레벨 제안으로
            # 라우팅(B3 소재-레벨 제어), 비카나리는 기존 hold(B2 억제) 유지. adgroup grain만
            # (키워드는 소재입찰 개념 없음). 파생 실패/소재 데이터 없음(sync 전)은 그룹입찰 유효
            # 가정 폴백(fail-safe 하위호환). exec_* = 실제 제안 대상(라우팅 후 소재로 절체될 수 있음).
            exec_target_type, exec_target_id, exec_adgroup_id = target_type, target_id, None
            step_base = current_bid
            if target_type == "adgroup":
                try:
                    eff = effective_bid.adgroup_effective_bid(db, target_id, current_bid)
                except Exception as e:  # noqa: BLE001 — 파생 실패는 기존 동작 폴백(차단 아님)
                    log.warning("auto_operator: 실효입찰 파생 실패 adgroup=%s: %s", target_id, e)
                    eff = None
                if eff is not None and eff["source"] == "ad":
                    if not (_ad_bid_canary(campaign_id) and eff.get("max_ad_id")):
                        hold_reason = (
                            f"[레버 미연결] 그룹입찰 무효(실효=소재입찰 {eff['effective_bid']}원) "
                            f"— B3 대기"
                        )
                    elif is_probe:
                        # GATE P2-1: 탐침은 ad 라우팅 금지 — CD3 되돌림(_standing_probes)이
                        # 'ad' before_value(adAttr JSON 중첩)를 파싱 못 해 영원히 미회수 +
                        # _conv_direct_today grain 필터 부재. 탐침 ad 확장은 별도 페이즈.
                        hold_reason = "[레버 미연결] 탐침은 ad 미지원 — CD3 'ad' 확장 후"
                    elif intended_action not in _AD_BID_CANARY_PROPOSAL_TYPES:
                        # D-NAO-129 이후 이 분기에 남는 것은 bid_down/bid_up 외의 타입뿐이다
                        # (예: 향후 신설되는 소재 타입). 열려면 위 상수에 등록한다.
                        hold_reason = f"[레버 미연결] ad {intended_action}는 미개방 타입"
                    else:
                        hold_reason = None
                    if hold_reason is not None:
                        result["held"].append({"target_id": target_id, "reason": hold_reason})
                        _record_blocked(
                            db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                            now=now, target_type=target_type, target_id=target_id,
                            action=intended_action,
                        )
                        continue
                    # B3 카나리: max 소재의 라이브 bidAmt를 스텝 기준으로 ad-레벨 제안 라우팅.
                    # 라이브 재조회는 ±15% 클램프(가드레일)를 소재 자기 입찰 기준으로 맞추기 위함.
                    try:
                        ad_live_bid = naver_sa_writer.get_ad_bid(eff["max_ad_id"])
                    except Exception as e:  # noqa: BLE001 — 재조회 실패는 hold(fail-closed)
                        log.warning("auto_operator: 소재 입찰 재조회 실패 ad=%s: %s",
                                    eff["max_ad_id"], e)
                        ad_live_bid = None
                    if ad_live_bid is None:
                        hold_reason = "소재 입찰 라이브 재조회 실패(B3 카나리)"
                        result["held"].append({"target_id": target_id, "reason": hold_reason})
                        _record_blocked(
                            db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                            now=now, target_type=target_type, target_id=target_id,
                            action=intended_action,
                        )
                        continue
                    exec_target_type = "ad"
                    exec_target_id = eff["max_ad_id"]
                    exec_adgroup_id = target_id
                    step_base = ad_live_bid

            # ── IU-R 그레인·방향 라우팅(PLAN §2 R1·R2) ──
            # UP ∧ 비-probe ∧ 비-ad에 대해:
            #   · SHOPPING adgroup → 쇼검 폐루프 순위 서보(bid_up_servo, R1)
            #   · WEB_SITE keyword → 파워링크 estimate 직행(bid_up_rank, R2 — 목표순위 현재−1의
            #     estimate 필요입찰을 min(경제성 상한, rank_bid)로 절체)
            # 그 외(BRAND_SEARCH adgroup·DOWN·probe UP·ad DOWN·campaign_type None)는 기존 ±15%
            # _clamp_step 폴백(codex P1-3·P2-1 — rank-step 미적용이지 UP 회귀 아님, 더 보수적 레거시
            # 경로. BEP·스톱로스·일예산·쿨다운 가드 전량 존치·스텝도 더 작음). probe는 별도 실험
            # 기계라 rank-step 미적용.
            # ★D-NAO-129 이후 정정: ad-라우팅 UP이 더 이상 hold되지 않는다. 그래도 rank-step은
            #   **여전히 안 걸린다** — 아래 분기가 exec_target_type=="adgroup"(서보)·"keyword"
            #   (estimate)만 매칭하기 때문이고, 소재는 둘 다 아니라 레거시 ±15% _clamp_step
            #   폴백을 탄다. 이건 우연이 아니라 이번 개방의 **안전 근거**다: 소재 UP이 클램프
            #   면제 타입(bid_up_servo/bid_up_rank)을 절대 달지 못하고 ±15% 상한이 걸린 bid_up
            #   으로만 나간다. ★소재 grain에 rank-step을 확장하려면 그때는 면제 타입이 소재로
            #   흘러가므로 쓰기 경계의 (승인원⟺타입) 잠금부터 다시 설계해야 한다.
            # rank_step_meta['step_reason']엔 태그를 넣지 않고(중복 방지),
            # hold_reason·rationale 구성 시 [순위서보]/[순위직행] 접두를 이 레인에서 붙인다.
            rank_step_used = False
            rank_step_meta: dict = {}
            rank_step_type = None  # "bid_up_servo" | "bid_up_rank"
            rank_kind = None       # "servo" | "estimate"
            rank_tag = ""
            if verdict["direction"] == "up" and not is_probe:
                if (exec_target_type == "adgroup"
                        and _entity_campaign_type(db, "adgroup", exec_target_id) == "SHOPPING"):
                    rank_kind, rank_step_type, rank_tag = "servo", "bid_up_servo", "순위서보"
                elif (exec_target_type == "keyword"
                        and _entity_campaign_type(db, "keyword", exec_target_id) == "WEB_SITE"):
                    rank_kind, rank_step_type, rank_tag = "estimate", "bid_up_rank", "순위직행"

            step_bid = None
            if rank_kind is not None and servo_agg is not None and servo_correction_factor is not None:
                # prefilter(쿨다운/일일캡) — estimate 호출·서보 산정 전에 실행 시점 guardrail이
                # 어차피 막을 유닛을 동일 판별로 미리 거른다(R1 GATE P2-2 백로그·중복 금지).
                prefilter_reason = _rank_step_prefilter(
                    db, entity_type=exec_target_type, entity_id=exec_target_id, now=now,
                    proposal_type=rank_step_type,
                )
                if prefilter_reason is not None:
                    hold_reason = f"[{rank_tag}] prefilter 차단(쿨다운/일일캡) — {prefilter_reason}"
                    result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                    _record_blocked(
                        db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                        now=now, target_type=exec_target_type, target_id=exec_target_id,
                        action=intended_action,
                    )
                    continue
                if rank_kind == "servo":
                    economic_ceiling = _servo_economic_ceiling(
                        db, adgroup_id=exec_target_id, campaign_id=campaign_id,
                        servo_agg=servo_agg, correction_factor=servo_correction_factor,
                        window_from=window_from, window_to=window_to,
                    )
                    # IU-R R3: 반응곡선 기울기를 response_prior로 주입(harness read, 원칙18-6).
                    # 유닛별 "adgroup:<id>" 곡선이 있으면 그 기울기로 "한 단 위 필요 증분" 근접,
                    # 없으면 None → rank_servo 콜드스타트 보수 기본 스텝 폴백.
                    rank_step_meta = rank_servo.decide_servo_step(
                        verdict.get("weighted_rank"), step_base,
                        imp_sum=verdict.get("imp_sum", 0),
                        economic_ceiling=economic_ceiling,
                        response_prior=response_priors.get(f"adgroup:{exec_target_id}"),
                    )
                    hold_prefix = f"[{rank_tag}] 스텝 없음 — "
                else:  # estimate 직행(R2)
                    rank_step_meta = _estimate_direct_step(
                        db, keyword_id=exec_target_id, campaign_id=campaign_id, current_bid=step_base,
                        weighted_rank=verdict.get("weighted_rank"), servo_agg=servo_agg,
                        correction_factor=servo_correction_factor,
                        window_from=window_from, window_to=window_to,
                        cache=estimate_cache, counter=estimate_counter,
                    )
                    hold_prefix = f"[{rank_tag}] "
                step_bid = rank_step_meta["target_bid"]
                if step_bid is None:
                    hold_reason = f"{hold_prefix}{rank_step_meta['step_reason']}"
                    result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                    _record_blocked(
                        db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                        now=now, target_type=exec_target_type, target_id=exec_target_id,
                        action=intended_action,
                    )
                    continue
                # 예산 pace 사전체크(공용) — 큰 스텝 → 잔여예산 초과 지출 사전 차단. guardrail의
                # 사후 소진 가드와 별개(forward-looking). 실패면 hold(관찰).
                pace_ok, pace_reason = _servo_budget_pace_ok(
                    db, campaign_id=campaign_id, curve=curve, now=now, target_bid=step_bid,
                )
                if not pace_ok:
                    hold_reason = f"[{rank_tag}] 예산 pace 차단 — {pace_reason}"
                    result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                    _record_blocked(
                        db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                        now=now, target_type=exec_target_type, target_id=exec_target_id,
                        action=intended_action,
                    )
                    continue
                rank_step_used = True
            else:
                step_bid = _clamp_step(step_base, verdict["direction"])
            if step_bid is None:
                hold_reason = "스텝 클램프 계산 불가(방향 무의미)"
                result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                _record_blocked(
                    db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                    now=now, target_type=exec_target_type, target_id=exec_target_id,
                    action=intended_action,
                )
                continue

            # 킬스위치 실행 직전 재확인(codex 5R[P1-2]) — 앞선 실행 도중 OFF 됐으면 이후
            # 유닛은 제안 생성 자체를 하지 않는다(즉시 정지 계약). 탐침도 동일 경로 통과.
            if not _auto_operate_now(db, campaign_id):
                hold_reason = "킬스위치 OFF — auto_operate=False(실행 직전 재확인, codex 5R[P1-2])"
                result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                _record_blocked(
                    db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                    now=now, target_type=exec_target_type, target_id=exec_target_id,
                    action=intended_action, event_type="kill_switch",
                )
                continue

            proposal_type = intended_action
            # 탐침(is_probe): rationale은 이미 [클릭탐침] 접두 없이 사유만 오므로 접두를 붙이고,
            # approval_source=probe_op·전용 expected_effect로 태그(diary probe actor).
            # 고삐(is_leash, D-NAO-60 RL3): 새 approval_source를 만들지 않고 시간당 밴드 레인
            # 소속을 유지(APPROVAL_SOURCE_HOURLY·ACTOR_HOURLY 그대로) — rationale 접두만
            # [순위고삐]로 구분해 소급채점/일기에서 시간당밴드 down과 leash down을 분간한다.
            # 그 외(일반 밴드 down/up)는 [시간당밴드].
            if is_probe:
                rationale = f"[클릭탐침] {verdict['reason']}"
                expected_effect = (
                    "클릭 탐침 — 밴드 사각지대(imp 있음·클릭0)에서 한 등 상향해 클릭 살아나는 "
                    "순위 실험(D-NAO-58 CD2, 되돌림·이익 판정은 CD3)."
                )
                proposal_approval_source = APPROVAL_SOURCE_PROBE
            elif rank_step_used:
                # IU-R R1/R2: rank-step(bid_up_servo 쇼검 서보 / bid_up_rank 파워링크 estimate
                # 직행) — 둘 다 ±15% 면제·rank-step(스톱로스 current 기준·신선도·TOCTOU). 시간당
                # 밴드 레인 소속 유지(APPROVAL_SOURCE_HOURLY) — rationale 접두([순위서보]/[순위직행])로
                # 소급채점/일기에서 일반 UP·rank-step UP을 분간한다. 근거(목표순위·산정)를
                # rationale에 보존(원칙25 근거 보존). ★expected_effect 끝에 제안 시점 base_bid
                # 마커를 심어 harness가 실행 직전 TOCTOU 대조(제안 시점≠실행 시점 라이브 bid면 중단).
                proposal_type = rank_step_type
                rationale = f"[{rank_tag}] {verdict['reason']} · {rank_step_meta['step_reason']}"
                if rank_kind == "servo":
                    effect_body = (
                        "쇼검 폐루프 순위 서보 — 관측 순위 한 단 위로 래칫(D-NAO-67 원리③). ±15% 면제, "
                        "경제성 상한·서보 절대 캡·예산 pace로 상한 대체. 다음 시간 순위 피드백으로 재평가."
                    )
                else:  # estimate 직행
                    effect_body = (
                        "파워링크 estimate 직행 — 목표순위(현재−1)의 필요입찰을 min(경제성 상한, "
                        "estimate)로 직행(D-NAO-67 원리③·D-NAO-19). ±15% 면제, 경제성 상한으로 상한 대체."
                    )
                expected_effect = encode_base_bid(effect_body, step_base)
                proposal_approval_source = APPROVAL_SOURCE_HOURLY
            elif is_leash:
                rationale = f"[순위고삐] {verdict['reason']}"
                expected_effect = (
                    "순위 고삐 — 장중 추정 총이익 loss(추정ROAS<BEP·하루치 소진)에서 한 등 "
                    "하향, 자정 리셋(D-NAO-60 RL3)."
                )
                proposal_approval_source = APPROVAL_SOURCE_HOURLY
            else:
                rationale = f"[시간당밴드] {verdict['reason']}"
                expected_effect = (
                    "시간당 밴드 관제 — CPC 급등 DOWN 또는 ROAS-UP(장중 tally/정착 실측, 순위 무관, "
                    "예산 여력) 기반 스텝 조정(D-NAO-66)."
                )
                proposal_approval_source = APPROVAL_SOURCE_HOURLY
            # B3 GATE 2R P2-B: ad Confirm-only 제안은 즉시 실행되지 않고 pending으로 남으므로,
            # 동일 (proposal_type, target_type='ad', target_id) pending이 이미 있으면 생성
            # skip(proposal_writer.persist의 pending dedup 규약 재사용) — 없으면 매시간 동일
            # pending이 누적(~16건/일 × 만료 14일)돼 Confirm 큐가 매몰된다.
            # ★D-NAO-125: 인라인 자동 실행 타입(하향)은 이 dedup에서 제외한다. 그 타입은
            #   pending으로 남지 않으므로 누적 매몰이 애초에 없고, 반대로 **배포 전에 쌓여
            #   있던 stale pending 하향 카드 한 장이 이후 모든 하향 판정을 영구히 skip시키는**
            #   역방향 사고가 난다(생성 자체가 막히니 일기에도 안 남아 조용히 죽는다).
            if exec_target_type == "ad":
                if _ad_auto_exec(proposal_type):
                    # ★D-NAO-125 codex[P1] 동시 실행 중복 쓰기 방지. 쿨다운은 change_log가
                    #   커밋된 **뒤에야** 보이므로, 레인이 겹쳐 돌면(수동 트리거 + 크론,
                    #   또는 다중 프로세스) 두 인스턴스가 각자 제안을 만들어 둘 다 쿨다운을
                    #   통과한 뒤 연달아 쓴다. 같은 소재에 'executing' 제안이 있으면 skip한다
                    #   — executing = 다른 실행자가 클레임했거나(동시), 크래시로 잔존해
                    #   **쓰기 결과가 불확실**한 상태다(harness 규약). 둘 다 지금 또 쏘면 안 되는
                    #   상황이라 fail-closed가 맞다.
                    #   ★pending은 보지 않는다: 자동 실행 타입은 pending으로 남지 않으므로,
                    #   배포 전에 쌓여 있던 stale pending 한 장이 이후 모든 하향을 영구히
                    #   막는 역방향 사고만 만든다(그게 이 dedup을 걷어낸 원래 이유다).
                    inflight = db.query(NaverProposal.id).filter(
                        NaverProposal.target_type == "ad",
                        NaverProposal.target_id == exec_target_id,
                        NaverProposal.status == "executing",
                    ).first()
                    if inflight is not None:
                        result["ad_auto_exec_inflight_skipped"] += 1
                        continue
                    # ★codex 2R[P2] 강등분 누적 방지: 레인 캡으로 Confirm 대기에 남은 이전
                    #   회차 카드가 있으면 **만료 처리하고 새 카드로 교체**한다(skip이 아니다).
                    #   skip하면 stale 한 장이 이후 하향을 계속 막고(그게 dedup을 걷어낸 이유),
                    #   그냥 두면 매시간 같은 소재의 pending이 쌓여 Confirm 큐가 매몰된다.
                    #   교체하면 큐에는 항상 **지금 값 기준 1장**만 남는다.
                    db.query(NaverProposal).filter(
                        NaverProposal.proposal_type == proposal_type,
                        NaverProposal.target_type == "ad",
                        NaverProposal.target_id == exec_target_id,
                        NaverProposal.status == "pending",
                    ).update({"status": "expired"}, synchronize_session=False)
                else:
                    dup_exists = db.query(NaverProposal.id).filter(
                        NaverProposal.proposal_type == proposal_type,
                        NaverProposal.target_type == "ad",
                        NaverProposal.target_id == exec_target_id,
                        NaverProposal.status == "pending",
                    ).first()
                    if dup_exists is not None:
                        result["ad_confirm_pending_dup_skipped"] += 1
                        continue

            proposal = NaverProposal(
                proposal_type=proposal_type, target_type=exec_target_type, target_id=exec_target_id,
                campaign_id=campaign_id, adgroup_id=exec_adgroup_id,
                rationale=rationale, expected_effect=expected_effect,
                status="pending", target_bid=step_bid,
            )
            db.add(proposal)
            db.flush()

            # B3 GATE P2-2 Confirm-only(계획서 카나리 스펙 "Jino Confirm 승인분만·자동발사 0",
            # D-NAO-5): target_type='ad' 제안은 어떤 레인에서도 자동 승인·인라인 실행 금지 —
            # pending으로 생성만 하고 실행은 기존 콘솔 Confirm 경로(라우터 승인→harness)만.
            #
            # ★D-NAO-125(Jino 확정 2026-07-29): **하향(bid_down)만 이 금지에서 해제**한다.
            #   근거 — 오늘 폴드8와이드(추정ROAS 0.945 < BEP 1.9611)가 RL3 손실 판정을
            #   13:20·14:20·15:20 세 번 받고도 세 번 다 집행되지 못했다. 판정은 정확한데 손이
            #   없어 손실이 시간당 3만원씩 쌓였다. Jino: *"손해를 보면 순위를 당장 낮춰야지."*
            #   방향을 하향으로 한정하는 이유:
            #     · 지출·노출이 **줄어드는** 방향이라 최악의 실수가 "너무 많이 내림"(기회 손실)
            #       이지 현금 유출이 아니다.
            #     · 실쓰기 경계(naver_execution_harness)가 이미 소재 bid_down을 승인원 무관·전
            #       캠페인으로 통과시킨다(D-NAO-70② ①). 즉 여기만 열면 경로가 완성된다.
            #     · 상향은 그 경계에서 (승인원 ⟺ 타입) 쌍방향 잠금에 막혀 있고, 그 잠금은 적대
            #       리뷰가 ±15% 클램프 면제 구멍(재현: 300원→90,000원)을 잡은 자리다. 성과 UP
            #       자동화는 새 승인원 경로 설계 + codex 적대 리뷰가 필요한 **별도 스프린트**다.
            #   가드레일은 전부 그대로 걸린다 — 2시간 쿨다운·한 등씩·±15% 클램프·스톱로스·
            #   출시창 하한(D-NAO-121). 되돌리려면 _AD_AUTO_EXEC_PROPOSAL_TYPES를 비우면 된다.
            # ★D-NAO-125 codex[P1] 첫 실행 blast radius: 이 게이트가 열리는 순간 그동안
            #   [레버 미연결]로 눌려 있던 92그룹·256소재의 하향 판정이 **한 회차에 통째로**
            #   쏟아질 수 있다. 판정이 옳다면 결국 다 내려가야 하지만, 그걸 첫 회차에 한꺼번에
            #   하면 규칙이 틀렸을 때 되돌릴 시간이 없다. 레인당 상한을 두어 회차에 걸쳐
            #   흘려보낸다(2h 쿨다운이라 유닛당 손해는 최대 1회차 = 1시간 지연).
            #   초과분은 버리지 않고 Confirm 대기로 남긴다 — 사람이 급하면 바로 승인할 수 있고,
            #   무엇이 밀렸는지가 큐에 보인다(조용한 드롭 금지).
            lane_capped = (
                _ad_auto_exec(proposal_type)
                and result["ad_auto_exec_reserved"] >= _MAX_AD_AUTO_EXEC_PER_LANE
            )
            if lane_capped:
                result["ad_auto_exec_capped"] += 1
                log.warning(
                    "auto_operator: 소재 자동 실행 레인 캡 도달(%d건) — proposal_id=%s ad=%s는 "
                    "Confirm 대기로 강등(D-NAO-125)",
                    _MAX_AD_AUTO_EXEC_PER_LANE, proposal.id, exec_target_id,
                )
            if exec_target_type == "ad" and (not _ad_auto_exec(proposal_type) or lane_capped):
                db.commit()
                result["ad_confirm_pending"] += 1
                log.info(
                    "auto_operator: B3 카나리 ad-레벨 제안 Confirm 대기 생성 proposal_id=%s "
                    "ad=%s adgroup=%s target_bid=%s(자동발사 0, D-NAO-5)",
                    proposal.id, exec_target_id, exec_adgroup_id, step_bid,
                )
                continue

            proposal.status = "approved"
            proposal.approval_source = proposal_approval_source
            db.commit()
            result["approved"] += 1
            if exec_target_type == "ad":
                result["ad_auto_exec_reserved"] += 1  # D-NAO-125 레인 캡(승인=자리 예약 시점)
            if is_probe:
                result["probed"] += 1
            if rank_step_used and rank_kind == "servo":
                result["servo"] += 1
            elif rank_step_used and rank_kind == "estimate":
                result["rank_direct"] += 1

            try:
                naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
                result["executed"] += 1
            except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
                result["failed"] += 1
                log.warning("auto_operator: 시간당 레인 실행 실패 proposal_id=%s: %s", proposal.id, e)

        # BX3(D-NAO-70·71): 핫셋 처리 후 탐색-UP 레인(핫셋 여집합 SHOPPING 그룹). exploration_candidates
        # 가 캠페인 엔티티 campaign_type='SHOPPING'만 통과시켜 WEB_SITE·BRAND_SEARCH는 자연 제외.
        # 손실고삐 발동 캠페인(campaign_leashed)은 제외(봉투#5 — UP·DOWN 충돌 금지). fail-soft:
        # 탐색 레인 예외가 핫셋 집행 결과를 오염시키지 않는다(bleed valve 관례 동형).
        if not campaign_leashed:
            try:
                _run_exploration_for_campaign(
                    db, campaign_id, window_from, window_to, now, fetch_intraday, result,
                    response_priors=response_priors,
                )
            except Exception as e:  # noqa: BLE001 — 탐색 레인 실패는 fail-soft(핫셋 결과 불변)
                log.warning("auto_operator: 탐색 레인 실패(fail-soft) campaign=%s: %s", campaign_id, e)

    # D-NAO-58 CD3 Stage 1: 탐침 실시간 출혈 밸브 — 당일 standing probe 회수(비용×3 급등∧즉시구매0).
    # lazy import(순환 회피 — probe_revert가 auto_operator를 import). 실패가 레인 결과를 오염시키지 않음.
    from app.services.naver_ad import probe_revert
    try:
        result["bleed"] = probe_revert.run_bleed_valve(db, now=now, fetch_intraday=fetch_intraday)
    except Exception as e:  # noqa: BLE001 — 밸브 실패는 fail-soft(레인 집행 결과 불변)
        log.warning("auto_operator: 탐침 출혈 밸브 실패(fail-soft): %s", e)
        result["bleed"] = {"error": str(e)}

    # VT2(D-NAO-81 B축): 스파이럴 조기 복원 스텝 — 핫셋/탐침 레인과 독립. fail-soft(경보 감지·
    # 발사 예외가 위 레인 집행 결과를 오염시키지 않음, bleed 밸브 관례 동형).
    try:
        _run_vitality_step(db, now, result)
    except Exception as e:  # noqa: BLE001 — vitality 스텝 실패는 fail-soft(레인 결과 불변)
        log.warning("auto_operator: 스파이럴 복원 스텝 실패(fail-soft): %s", e)

    # BP(D-NAO-102): 예산 페이싱 증액 + 익일 원복 자가치유. 핫셋/탐색 레인과 독립·맨 뒤
    # (그 시각까지의 소진을 다 보고 판정) · fail-soft(BP 실패가 입찰 집행 결과를 오염시키지 않음).
    try:
        _run_budget_pacing_lane(db, now, result)
    except Exception as e:  # noqa: BLE001 — BP 레인 실패는 fail-soft(레인 결과 불변)
        log.warning("auto_operator: 예산 페이싱 레인 실패(fail-soft): %s", e)

    return result
