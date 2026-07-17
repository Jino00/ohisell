# auto_operator.py — auto_operator Harness (D-NAO-49, docs/PLAN_naver-ad-auto-operator.md)
# 역할: D-NAO-48 04 자동운영 4조건 정책을 서버로 이관 — 일 레인(run_daily_lane, 08:50 크론).
#   시간당 밴드 관제 레인(run_hourly_lane)은 다음 커밋(A2+A3)에서 이 파일에 추가된다.
#   auto_operate=True 캠페인(현재 04 하나)만 대상. 로컬 08:55 루틴은 보고·감사 전용으로
#   강등(§0). 예산 변경 불가침(D-NAO-42 Jino 게이트), 03(MOP) 등 타 캠페인 개입 금지.
#   쓰기는 반드시 naver_execution_harness.execute() 경유(초크포인트 유지, 원칙18-6 —
#   guardrail_gate·naver_sa_writer 직접 쓰기 호출 금지, 이 harness는 SA를 조합만 한다).
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverCampaignSettings,
    NaverChangeLog,
    NaverProposal,
    NaverRetroSignal,
)
from app.services.naver_ad import campaign_target_resolver, diagnosis, naver_execution_harness, naver_sa_writer
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.guardrail_gate import _MAX_CHANGE_PCT
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
_MIN_CLICK_FOR_APPROVAL = 10  # D-NAO-48 조건②(rationale 창 클릭) / §4-1 핫셋 클릭 게이트 공유

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
        p.approval_source = "auto_operator"
        db.commit()
        result["approved"] += 1

        try:
            naver_execution_harness.execute(db, p.id, dry_run=False, now=now)
            result["executed"] += 1
        except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정(failed 등)
            result["failed"] += 1
            log.warning("auto_operator: 일 레인 실행 실패 proposal_id=%s: %s", p.id, e)

    return result
