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

import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func as sqlfunc, select
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverEntity,
    NaverHourlySnapshot,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import bid_simulator, campaign_target_resolver, diagnosis, diary, effective_bid, intraday_roas, naver_execution_harness, naver_sa_writer, rank_servo
from app.services.naver_ad.bid_step_types import BID_UP_TYPES
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.guardrail_gate import _MAX_CHANGE_PCT
from app.services.naver_ad.trigger_watch import CPC_SPIKE_RATIO
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24
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
_AD_BID_CANARY_PROPOSAL_TYPES: frozenset[str] = frozenset({"bid_down"})


def _ad_bid_canary(campaign_id: str) -> bool:
    """캠페인이 B3 소재-레벨 제어 카나리 개방 대상인지(기본 빈 집합=전면 미개방)."""
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


def _check_bid_up_conditions(db: Session, p: NaverProposal, today: date) -> str | None:
    """D-NAO-48 bid_up 4조건(PLAN §3) — 하나라도 미충족이면 hold 사유 문자열, 전부
    충족이면 None(승인 가능)."""
    if p.target_bid is None:
        return "target_bid 없음 — 구조 결함(재생성 필요)"

    # ①스텝 클램프 정상 — target_bid가 라이브 현재가 대비 ±_MAX_CHANGE_PCT 이내인지 재확인.
    # (harness/guardrail_gate가 실행 직전 다시 검증하지만, 여기서 미리 걸러 실패를 예정된
    # 재시도가 가능한 'pending 유지'로 남긴다 — harness에 넘겨 fail-closed 'failed'로 영구
    # 종결시키지 않기 위함.)
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

    # ②rationale 창 클릭 ≥10
    clk = _extract_rationale_clk(p.rationale)
    if clk is None or clk < _MIN_CLICK_FOR_APPROVAL:
        return f"②rationale 창 클릭 부족(clk={clk})"

    # ③그룹 보정ROAS(정착창 D-8~D-2) ≥ target_roas
    roas_ok, roas_reason = _settlement_roas_ok(db, p.target_type, p.target_id, p.campaign_id, today)
    if not roas_ok:
        return f"③{roas_reason}"

    # ④최신 소급채점에서 bleeding 아님(asof 신선도 포함 — codex 4R[P1])
    bleeding_reason = _bleeding_hold_reason(db, p.target_type, p.target_id, today)
    if bleeding_reason:
        return bleeding_reason

    return None


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
    }

    auto_ids = _auto_operate_campaign_ids(db)
    if not auto_ids:
        return result

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

        if p.proposal_type == "bid_up":
            hold_reason = _check_bid_up_conditions(db, p, today)
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
    # 그 캠페인의 pending은 그대로 두고, 스위치 재가동 시 정상 사이클로 복귀): 레인 시작
    # 시점 auto 집합(auto_ids)과 지금 재조회한 집합의 교집합만 정리 — 도중에 OFF 된
    # 캠페인(킬스위치 skip분 포함)과 도중에 ON 된 캠페인(이번 레인이 심사 안 함) 둘 다 제외.
    sweep_ids = auto_ids & _auto_operate_campaign_ids(db)
    if sweep_ids:
        leftovers = (
            db.query(NaverProposal)
            .filter(
                NaverProposal.status == "pending",
                NaverProposal.proposal_type.in_(_DAILY_LANE_PROPOSAL_TYPES),
                # B3 GATE P2-2: ad-레벨 제안은 stale 정리에서도 제외 — pending은 "Confirm
                # 대기" 정상 상태(rejected 처리하면 콘솔 승인 창 자체가 소멸). 만료는
                # proposal_pipeline의 expiry가 담당(14일).
                NaverProposal.target_type != "ad",
                NaverProposal.campaign_id.in_(sweep_ids),
                NaverProposal.created_at < day_end,
            )
            .all()
        )
        for lp in leftovers:
            lp.status = "rejected"
            lp.rationale = (
                f"{lp.rationale or ''} [auto_op 보류 — 익일 08:00 생성기가 갱신 데이터로 "
                "재생성(D-NAO-49 일일 사이클, codex 11R)]"
            )
            result["rejected_stale"] += 1
        if leftovers:
            # D-NAO-54 P1 일기(reject)용 원시값을 커밋 前 캡처(독립 리뷰 P2-1: 커밋이 ORM
            # 인스턴스를 만료시켜, 커밋 후 lp.* 접근은 refresh SELECT(I/O)를 유발 — 그 예외는
            # write_diary_entry의 try 밖(호출 프레임)이라 fail-open 계약을 뚫는다).
            rejected_info = [
                (lp.campaign_id, lp.target_type, lp.target_id, lp.adgroup_id, lp.proposal_type)
                for lp in leftovers
            ]
            db.commit()
            # 커밋 확정 후에만 기록(기록은 확정된 사실만) — 인자는 위에서 캡처한 원시값.
            for c_id, t_type, t_id, ag_id, p_type in rejected_info:
                diary.write_diary_entry(
                    db, "reject", c_id, actor=diary.ACTOR_DAILY,
                    target_type=t_type, target_id=t_id, adgroup_id=ag_id, action=p_type,
                    rationale="auto_op 보류/stale — 익일 08:00 재생성(D-NAO-49 일일 사이클, codex 11R)",
                    now=now,
                )

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


def _probe_trigger(curve: list[dict], now: datetime) -> tuple[bool, str]:
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
    - 창 내 imp-가중 avg_rank ≥ 2.5(_HOURLY_RANK_DOWN_THRESHOLD 재사용) — rank<2.5는 밴드
      상단/과열 = 위치가 아니라 수요 문제라 올려도 소용없음. 밴드 안/하단이어야 올라갈 여지.
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
    if weighted_rank < _HOURLY_RANK_DOWN_THRESHOLD:
        return False, (
            f"창{win_label} 가중 avg_rank={float(weighted_rank):.2f}<{_HOURLY_RANK_DOWN_THRESHOLD}"
            "(밴드 상단/과열=수요 문제) — 탐침 대상 아님"
        )
    return True, (
        f"창{win_label} imp={imp_sum}≥{_MIN_HOURLY_SAMPLE_IMP}·clk=0·가중avg_rank="
        f"{float(weighted_rank):.2f}≥{_HOURLY_RANK_DOWN_THRESHOLD}(밴드 사각지대 — 한 등 상향 탐침)"
    )


def _learned_optimal_skip(
    db: Session, curve: list[dict], now: datetime, campaign_id: str,
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
    learned = probe_learning_loop.learned_probe_rank(
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

    result: dict = {
        "reviewed": 0, "approved": 0, "executed": 0, "held": [], "skipped": 0, "failed": 0,
        "probed": 0,  # D-NAO-58 CD2: 탐침으로 승격된 up 제안 수(라이브 관측용)
        "ad_confirm_pending": 0,  # B3 GATE P2-2: Confirm 대기로 생성된 ad-레벨 제안 수
        "ad_confirm_pending_dup_skipped": 0,  # B3 GATE 2R P2-B: 동일 pending 존재로 skip된 수
        "servo": 0,  # IU-R R1: 서보 스텝으로 승인된 쇼검 UP 제안 수(라이브 관측용)
    }

    # IU-R R1(PLAN §2, 원칙18-6 허브): 서보 경제성 상한 원료를 캠페인 순회 **밖에서 1회**
    # precompute한다(N+1 방지) — 정착창 계층 agg(campaign/account) + 보정계수. 함수 레벨
    # import(proposal_pipeline는 무거운 파이프라인이라 module-level 결합 회피, 순환 리스크
    # 최소화 — probe_revert lazy import 관례와 동형). auto 캠페인이 없으면 계산 자체를 건너뛴다.
    campaigns = _auto_operate_campaigns(db)
    servo_agg: dict | None = None
    servo_correction_factor: Decimal | None = None
    if campaigns:
        from app.services.naver_ad import proposal_pipeline
        servo_agg = proposal_pipeline._precompute_aggregates(db, window_from, window_to)
        _cf = diagnosis.correction_factor(db, today - timedelta(days=1))
        servo_correction_factor = Decimal(str(_cf["factor"]))

    for campaign_id in campaigns:
        breaker_reason = _check_spend_circuit_breaker(db, campaign_id, now)
        if breaker_reason:
            result["held"].append({"campaign_id": campaign_id, "reason": breaker_reason})
            _record_blocked(
                db, campaign_id=campaign_id, actor=diary.ACTOR_HOURLY,
                reason=breaker_reason, now=now,
            )
            continue

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
                probe_fired, probe_reason = _probe_trigger(curve, now)
                if probe_fired:
                    # D-NAO-60 RL5(CD5): 학습된 최적 밴드에 이미 도달했으면 상향 생략(과climb
                    # 방지). guardrail 우회 없음 — 통과한 제안도 execute() 전량을 그대로 탄다.
                    skip, skip_reason = _learned_optimal_skip(db, curve, now, campaign_id)
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
                        # GATE P2-2: 카나리 1단계는 bid_down만 — ad UP은 카나리 2단계.
                        hold_reason = "[레버 미연결] ad UP은 카나리 2단계"
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

            # ── IU-R R1(PLAN §2 R1) 그레인·방향 라우팅 ──
            # UP ∧ SHOPPING adgroup(비-probe·비-ad) → 순위 서보(decide_servo_step). 그 외
            # (BRAND_SEARCH adgroup·keyword UP[R2 전]·DOWN·probe UP·ad DOWN)는 기존 _clamp_step
            # ±15% 유지(codex P1-3 — 서보 미적용이지 UP 회귀 아님). probe는 별도 실험 기계라
            # 서보 미적용(is_probe 제외) — 서보는 ROAS 지배 게이트 통과 UP에만 얹힌다.
            # ★P2-1(codex 기각 근거): campaign_type None(NULL·미채움)이면 서보 미적용 =
            #   기존 ±15% _clamp_step 경로로 폴백한다. 이는 fail-open이 아니라 **더 보수적인
            #   레거시 경로**다 — BEP·스톱로스·일예산·쿨다운 가드가 전량 걸리고 스텝도 더 작다.
            #   hold로 바꾸면 campaign_type NULL 유닛의 UP 능력이 R1 이전 대비 회귀(행위 보존
            #   위반)라 폴백을 유지한다. 핫셋 campaign_type 관통 전달은 GATE P2-3와 함께 R2 백로그.
            # ★ad-라우팅 UP은 위에서 이미 hold(카나리 2단계)라 여기 exec_target_type=="ad"는
            #   DOWN뿐 → 서보 조건(direction=="up")에 자연히 안 걸림(ad 카나리 UP 누출 0).
            servo_used = False
            servo_meta: dict = {}
            is_servo_grain = (
                verdict["direction"] == "up" and not is_probe
                and exec_target_type == "adgroup"
                and _entity_campaign_type(db, "adgroup", exec_target_id) == "SHOPPING"
            )
            if is_servo_grain and servo_agg is not None and servo_correction_factor is not None:
                economic_ceiling = _servo_economic_ceiling(
                    db, adgroup_id=exec_target_id, campaign_id=campaign_id,
                    servo_agg=servo_agg, correction_factor=servo_correction_factor,
                    window_from=window_from, window_to=window_to,
                )
                servo = rank_servo.decide_servo_step(
                    verdict.get("weighted_rank"), step_base,
                    imp_sum=verdict.get("imp_sum", 0),
                    economic_ceiling=economic_ceiling, response_prior=None,  # R3 전 콜드스타트
                )
                if servo["target_bid"] is None:
                    hold_reason = f"[순위서보] 스텝 없음 — {servo['step_reason']}"
                    result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                    _record_blocked(
                        db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                        now=now, target_type=exec_target_type, target_id=exec_target_id,
                        action=intended_action,
                    )
                    continue
                # 예산 pace 사전체크(큰 스텝 → 잔여예산 초과 지출 사전 차단). guardrail의 사후
                # 소진 가드와 별개(forward-looking) — 실패면 hold(관찰).
                pace_ok, pace_reason = _servo_budget_pace_ok(
                    db, campaign_id=campaign_id, curve=curve, now=now, target_bid=servo["target_bid"],
                )
                if not pace_ok:
                    hold_reason = f"[순위서보] 예산 pace 차단 — {pace_reason}"
                    result["held"].append({"target_id": exec_target_id, "reason": hold_reason})
                    _record_blocked(
                        db, campaign_id=campaign_id, actor=lane_actor, reason=hold_reason,
                        now=now, target_type=exec_target_type, target_id=exec_target_id,
                        action=intended_action,
                    )
                    continue
                step_bid = servo["target_bid"]
                servo_used = True
                servo_meta = servo
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
            elif servo_used:
                # IU-R R1: 쇼검 폐루프 순위 서보 — proposal_type=bid_up_servo(±15% 면제·rank-step).
                # 시간당 밴드 레인 소속 유지(APPROVAL_SOURCE_HOURLY) — rationale 접두 [순위서보]로
                # 소급채점/일기에서 일반 UP·서보 UP을 분간한다. 근거(목표순위·서보 산정)를
                # rationale에 보존(원칙25 근거 보존).
                proposal_type = "bid_up_servo"
                rationale = f"[순위서보] {verdict['reason']} · {servo_meta['step_reason']}"
                expected_effect = (
                    "쇼검 폐루프 순위 서보 — 관측 순위 한 단 위로 래칫(D-NAO-67 원리③). ±15% 면제, "
                    "경제성 상한·서보 절대 캡·예산 pace로 상한 대체. 다음 시간 순위 피드백으로 재평가."
                )
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
            if exec_target_type == "ad":
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
            if exec_target_type == "ad":
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
            if is_probe:
                result["probed"] += 1
            if servo_used:
                result["servo"] += 1

            try:
                naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
                result["executed"] += 1
            except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
                result["failed"] += 1
                log.warning("auto_operator: 시간당 레인 실행 실패 proposal_id=%s: %s", proposal.id, e)

    # D-NAO-58 CD3 Stage 1: 탐침 실시간 출혈 밸브 — 당일 standing probe 회수(비용×3 급등∧즉시구매0).
    # lazy import(순환 회피 — probe_revert가 auto_operator를 import). 실패가 레인 결과를 오염시키지 않음.
    from app.services.naver_ad import probe_revert
    try:
        result["bleed"] = probe_revert.run_bleed_valve(db, now=now, fetch_intraday=fetch_intraday)
    except Exception as e:  # noqa: BLE001 — 밸브 실패는 fail-soft(레인 집행 결과 불변)
        log.warning("auto_operator: 탐침 출혈 밸브 실패(fail-soft): %s", e)
        result["bleed"] = {"error": str(e)}

    return result
