# auto_operator.py — auto_operator Harness (D-NAO-49, docs/PLAN_naver-ad-auto-operator.md)
# 역할: D-NAO-48 04 자동운영 4조건 정책을 서버로 이관(일 레인, run_daily_lane — 08:50 크론)
#   + 시간당 밴드 관제 실입찰(시간당 레인, run_hourly_lane — 매시 :20 크론). 둘 다
#   auto_operate=True 캠페인(현재 04 하나)만 대상. 로컬 08:55 루틴은 보고·감사 전용으로
#   강등(§0). 예산 변경 불가침(D-NAO-42 Jino 게이트), 03(MOP) 등 타 캠페인 개입 금지, 시간당
#   레인은 순위·CPC·페이싱만 판단(ROAS/BEP 신규 판단 금지 — 가드레일의 기존 BEP 차단만).
#   쓰기는 반드시 naver_execution_harness.execute() 경유(초크포인트 유지, 원칙18-6 —
#   guardrail_gate·naver_sa_writer 직접 쓰기 호출 금지, 이 harness는 SA를 조합만 한다).
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func as sqlfunc
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
from app.services.naver_ad import campaign_target_resolver, diagnosis, hourly_pattern, naver_execution_harness, naver_sa_writer
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
_MIN_CLICK_FOR_APPROVAL = 10  # D-NAO-48 조건②(rationale 창 클릭) / §4-1 핫셋 클릭 게이트 공유
_MIN_HOURLY_SAMPLE_IMP = 30  # §4-2 "imp 합 < 30이면 그 시간대 묶음은 판단 보류"
_HOURLY_RANK_DOWN_THRESHOLD = Decimal("2.5")  # §4-3 DOWN: 가중 avg_rank < 2.5
_HOURLY_RANK_UP_THRESHOLD = Decimal("4.0")  # §4-3 UP: 가중 avg_rank > 4.0
_HOURLY_RECENT_HOURS = 3  # §4-2 "최근 3개 완료 시간대"
_HOURLY_SPEND_BREAKER_MULTIPLE = 3  # §4-6 "직전 7일 일평균 ×3"
_HOURLY_BASELINE_DAYS = 7  # 소진 서킷브레이커 직전 7일

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


def _settlement_roas_ok(
    db: Session, target_type: str, target_id: str, campaign_id: str, today: date,
) -> tuple[bool, str]:
    """D-NAO-48 조건③(그룹 보정ROAS(정착창 D-8~D-2) ≥ target_roas) — 근거 없음은 전부
    fail-closed(추정 금지 원칙, guardrail_gate와 동일 태도)."""
    window_from, window_to = _settlement_window(today)
    agg = _settlement_agg(db, target_type, target_id, window_from, window_to)
    if agg["cost"] <= 0:
        return False, f"정착창({window_from.isoformat()}~{window_to.isoformat()}) 실적 없음 — 보정ROAS 검증 불가(fail-closed)"
    factor = diagnosis.correction_factor(db, today - timedelta(days=1))["factor"]
    roas_corrected = (agg["conv_amt"] / agg["cost"]) * float(factor)
    target_roas = _resolve_target_roas(db, campaign_id)
    if target_roas is None:
        return False, "target_roas 해석 불가(계정 기본값 없음) — 검증 불가(fail-closed)"
    if roas_corrected < target_roas:
        return False, f"정착창 보정ROAS {roas_corrected:.4f} < 목표 {target_roas}"
    return True, f"정착창 보정ROAS {roas_corrected:.4f} >= 목표 {target_roas}"


def _is_bleeding_now(db: Session, target_type: str, target_id: str) -> bool:
    """D-NAO-48 조건④(최신 소급채점에서 bleeding 아님) — retro_snapshotter._BOARDS가 매일
    board별로 스냅샷하는 NaverRetroSignal 최신 asof_date 행에 이 target_id가 해당 bleeding
    보드(board)로 존재하면 "지금 bleeding"으로 판정한다. 소급채점 시스템 자체가 아직 한
    번도 안 돌았으면(latest_asof None) 검증 근거가 없어 fail-closed(True=bleeding 취급,
    hold) — 반면 최신 스냅샷은 있는데 이 target_id가 그 보드에 없으면 "그날 안 걸림" =
    실제 not-bleeding 신호이므로 False."""
    board = _BLEEDING_BOARD_BY_TARGET_TYPE.get(target_type)
    if board is None:
        return True  # 알 수 없는 grain — fail-closed
    latest_asof = db.query(sqlfunc.max(NaverRetroSignal.asof_date)).scalar()
    if latest_asof is None:
        return True  # 소급채점 데이터 자체가 없음 — 검증 불가(fail-closed)
    exists = db.query(NaverRetroSignal.id).filter(
        NaverRetroSignal.asof_date == latest_asof,
        NaverRetroSignal.board == board,
        NaverRetroSignal.target_id == target_id,
    ).first()
    return exists is not None


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

    # ④최신 소급채점에서 bleeding 아님
    if _is_bleeding_now(db, p.target_type, p.target_id):
        return "④최신 소급채점에서 bleeding으로 판정됨"

    return None


def run_daily_lane(db: Session, *, now: datetime | None = None) -> dict:
    """D-NAO-48 정책의 서버 코드화 — auto_operate 캠페인의 당일 생성 pending 실행형
    (bid_up/bid_down/pause)을 심사·승인·집행(PLAN §3). 08:50 크론(catch-up 포함).

    bid_up은 4조건 전부 충족해야 승인(하나라도 미충족 시 hold, pending 그대로 유지 —
    harness로 넘기지 않아 'failed' 영구 종결을 피한다). bid_down은 무조건 승인(안전
    방향, ref31 정밀도 61~88%). pause는 D-NAO-40 외부 정지 이력만 확인. 승인 후 실행은
    반드시 naver_execution_harness.execute(dry_run=False) 경유 — 가드레일 이중 검증
    의도적(§3 "이중 게이트 의도적").

    반환: {"reviewed", "approved", "executed", "held": [{"id","reason"}], "failed"}.
    """
    now = now or kst_now()
    today = now.date()
    day_start, day_end = _day_bounds_utc(today)

    result: dict = {"reviewed": 0, "approved": 0, "executed": 0, "held": [], "failed": 0}

    auto_ids = _auto_operate_campaign_ids(db)
    if not auto_ids:
        return result

    candidates = (
        db.query(NaverProposal)
        .filter(
            NaverProposal.status == "pending",
            NaverProposal.proposal_type.in_(_DAILY_LANE_PROPOSAL_TYPES),
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
                continue
        elif p.proposal_type == "pause":
            if _has_recent_external_stop(db, p.target_type, p.target_id):
                result["held"].append(
                    {"id": p.id, "reason": "D-NAO-40: 최근 외부/수동 정지 이력 발견 — hold"}
                )
                continue
        # bid_down: 조건 없음(무조건 승인, 안전 방향)

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

    return result


# ══════════════════════════ 시간당 레인(A2+A3) ══════════════════════════


def _auto_operate_campaigns(db: Session) -> list[str]:
    rows = db.query(NaverCampaignSettings.campaign_id).filter(
        NaverCampaignSettings.auto_operate.is_(True)
    ).order_by(NaverCampaignSettings.campaign_id.asc()).all()
    return [r[0] for r in rows]


def _check_spend_circuit_breaker(db: Session, campaign_id: str, today: date) -> str | None:
    """§4-6 정지 조건(레인 자체 fail-closed): 당일 캠페인 소진 > 직전 7일 일평균 ×3 →
    그 캠페인의 시간당 레인 전체 hold.

    codex 2R[P1-2]: 당일 스냅샷 자체가 없으면(수집 미가동/stale — 어제 행만 있는 경우도
    ad_date 필터로 동일하게 부재) 소진을 **평가할 수 없다** — 평가 불가 상태에서 실입찰을
    진행하면 브레이커가 무의미하므로 fail-closed로 캠페인 전체 hold. 반면 직전 7일
    베이스라인이 없는 경우(신규 캠페인 등)는 "당일 소진은 보이는데 비교 기준이 없음" —
    폭주 관측 자체는 가능한 상태라 미발동(fail-open, 개별 안전장치는 guardrail_gate가
    여전히 담당)으로 남긴다(두 부재의 의미가 다름)."""
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
    """최근 _HOURLY_RECENT_HOURS(3)개 **완료** 시간대의 imp-가중 avg_rank + imp 합계.

    codex 1R[P2]: :20 실행 시 hh24 응답에 현재 시간대(20분치 부분 데이터)가 섞여 온다 —
    부분 버킷을 그대로 판정에 쓰면 rank/표본이 왜곡된다. hour < now_hour인 완료 시간대만
    취한 뒤 마지막 3개를 쓴다(00시대 실행처럼 완료 버킷이 없으면 imp_sum=0 → 표본 부족
    hold로 자연 수렴)."""
    completed = [h for h in curve if h["hour"] < now_hour]
    recent = sorted(completed, key=lambda h: h["hour"])[-_HOURLY_RECENT_HOURS:]
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


def _is_pacing_slow(
    db: Session, target_type: str, target_id: str, curve: list[dict], now: datetime,
) -> tuple[bool, str]:
    """§4-3 UP 조건의 "당일 소진 페이싱 저속" — hourly_pattern.expected_cost_fraction
    (요일×시간 168칸 실측 곡선) 사용 가능하면 그것, 없으면 선형 기대(경과분/24h)로 폴백
    (§4-2 명시 규칙). 실제 페이스 = 오늘 누적 그룹 비용 ÷ 정착창 일평균 그룹 비용(둘 다
    "정상적인 하루 전체 대비 비율" 단위로 맞춘 것 — expected_cost_fraction과 동일 척도).
    실제<기대면 저속(스펙에 배수 미명시 — 단순 비교, 추정 금지 원칙상 임의 배수 도입 안 함)."""
    window_from, window_to = _settlement_window(now.date())
    agg = _settlement_agg(db, target_type, target_id, window_from, window_to)
    avg_daily_cost = agg["cost"] / _HOURLY_BASELINE_DAYS if agg["cost"] > 0 else 0.0
    if avg_daily_cost <= 0:
        return False, "정착창 소진 기준 없음(페이싱 판단 불가 — 저속 아님으로 처리)"

    today_cost = sum(h["cost"] for h in curve)
    actual_pace = today_cost / avg_daily_cost

    expected = hourly_pattern.expected_cost_fraction(db, weekday=now.weekday(), hour=now.hour)
    if expected is None:
        expected = Decimal(now.hour * 60 + now.minute) / Decimal(24 * 60)
        basis = "선형기대"
    else:
        basis = "hourly_pattern"
    expected_f = float(expected)

    if actual_pace < expected_f:
        return True, f"페이싱저속(실제소진비{actual_pace:.2f}<기대{expected_f:.2f}, {basis})"
    return False, f"페이싱정상(실제{actual_pace:.2f}>=기대{expected_f:.2f}, {basis})"


def _judge_hourly(
    db: Session, *, target_type: str, target_id: str, campaign_id: str, curve: list[dict], now: datetime,
) -> dict:
    """§4-3 시간당 판정(우선순위 순, 하나만) — {"direction": "up"/"down"/"hold", "reason": str}.

    ROAS/BEP는 여기서 신규 판단하지 않는다(정착창 재사용은 조건 재확인일 뿐, 시간당
    ROAS 산출은 없음 — §4 "시간당 판단은 순위·CPC·페이싱만" 원칙 준수)."""
    summary = _weighted_recent(curve, now.hour)
    if summary["imp_sum"] < _MIN_HOURLY_SAMPLE_IMP:
        return {
            "direction": "hold",
            "reason": f"최근{_HOURLY_RECENT_HOURS}시간대 imp={summary['imp_sum']}<{_MIN_HOURLY_SAMPLE_IMP}(표본 부족)",
        }

    weighted_rank = summary["weighted_rank"]

    # DOWN 우선 ①: 과열밴드(순위 과도하게 높음 — 낮은 숫자일수록 상위노출)
    if weighted_rank is not None and weighted_rank < _HOURLY_RANK_DOWN_THRESHOLD:
        return {
            "direction": "down",
            "reason": f"가중avg_rank={float(weighted_rank):.2f}<{_HOURLY_RANK_DOWN_THRESHOLD}(과열밴드)",
        }

    # DOWN 우선 ②: CPC 급등(trigger_watch.CPC_SPIKE_RATIO 재사용 — 단일소스, PLAN §4 원문의
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
            }

    # UP: 3조건 동시 충족 시만(밴드하단이탈 AND 정착ROAS충족 AND 페이싱저속)
    if weighted_rank is not None and weighted_rank > _HOURLY_RANK_UP_THRESHOLD:
        roas_ok, roas_reason = _settlement_roas_ok(db, target_type, target_id, campaign_id, now.date())
        if roas_ok:
            pacing_slow, pacing_reason = _is_pacing_slow(db, target_type, target_id, curve, now)
            if pacing_slow:
                return {
                    "direction": "up",
                    "reason": (
                        f"가중avg_rank={float(weighted_rank):.2f}>{_HOURLY_RANK_UP_THRESHOLD}"
                        f"(밴드하단이탈), {roas_reason}, {pacing_reason}"
                    ),
                }

    return {"direction": "hold", "reason": "판정 조건 미충족(기본 hold)"}


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


def run_hourly_lane(db: Session, *, now: datetime | None = None, fetch_intraday=None) -> dict:
    """시간당 밴드 관제 실입찰(PLAN §4). 매시 :20 크론(catch-up 제외 — 시간성 소멸).

    ①캠페인별 소진 서킷브레이커(§4-6) → 걸리면 그 캠페인 전체 hold ②핫셋 선정(§4-1)
    ③유닛별 intraday hh24 곡선 조회(§4-2, 실패 시 skip) ④판정(§4-3, 순위·CPC·페이싱만 —
    ROAS 신규 판단 없음) ⑤스텝 제안 생성+즉시 승인(approval_source=APPROVAL_SOURCE_HOURLY)
    +naver_execution_harness.execute() 경유 실행(가드레일 전량 통과 필요 — 쿨다운·일일상한·
    BEP·스톱로스가 최종 방어선).

    fetch_intraday 미주입 시 fetch_entity_hh24(테스트 주입, 원칙18-8 — keyword_hourly_sweep과
    동일 관례). stat_date=오늘(now.date())로 호출하면 timeRange since=until=오늘이 되어
    이미 당일 조회가 가능하다 — datePreset="today" 하위호환 확장은 불필요로 판단(최종보고 명시).

    반환: {"reviewed", "approved", "executed", "held": [...], "skipped", "failed"}.
    """
    now = now or kst_now()
    fetch_intraday = fetch_intraday or fetch_entity_hh24
    today = now.date()
    window_from, window_to = _settlement_window(today)

    result: dict = {
        "reviewed": 0, "approved": 0, "executed": 0, "held": [], "skipped": 0, "failed": 0,
    }

    for campaign_id in _auto_operate_campaigns(db):
        breaker_reason = _check_spend_circuit_breaker(db, campaign_id, today)
        if breaker_reason:
            result["held"].append({"campaign_id": campaign_id, "reason": breaker_reason})
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
                result["held"].append({"target_id": target_id, "reason": verdict["reason"]})
                continue

            current_bid = _live_current_bid(target_type, target_id)
            if current_bid is None:
                result["held"].append({"target_id": target_id, "reason": "라이브 현재가 재조회 실패"})
                continue
            step_bid = _clamp_step(current_bid, verdict["direction"])
            if step_bid is None:
                result["held"].append({"target_id": target_id, "reason": "스텝 클램프 계산 불가(방향 무의미)"})
                continue

            proposal_type = "bid_up" if verdict["direction"] == "up" else "bid_down"
            proposal = NaverProposal(
                proposal_type=proposal_type, target_type=target_type, target_id=target_id,
                campaign_id=campaign_id,
                rationale=f"[시간당밴드] {verdict['reason']}",
                expected_effect="시간당 밴드 관제 — 순위·CPC·페이싱 기반 스텝 조정(ROAS 신규 판단 없음).",
                status="pending", target_bid=step_bid,
            )
            db.add(proposal)
            db.flush()

            proposal.status = "approved"
            proposal.approval_source = APPROVAL_SOURCE_HOURLY
            db.commit()
            result["approved"] += 1

            try:
                naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
                result["executed"] += 1
            except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
                result["failed"] += 1
                log.warning("auto_operator: 시간당 레인 실행 실패 proposal_id=%s: %s", proposal.id, e)

    return result
