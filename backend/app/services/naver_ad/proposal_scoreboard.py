# proposal_scoreboard.py — proposal_scoreboard SA (듀얼모드 스프린트 Phase 6, D-NAO-14 학습루프1)
# 역할(SA): naver_change_log에서 verify_date가 지난(D+14) 실행 건을 찾아 실측 성과(전/후 RPC
#   트렌드)로 outcome(improved/declined/neutral)을 판정하고, 제안유형별 정확도를
#   NaverLearningState(metric=proposal_accuracy)에 롤업한다. 콘솔 "성적표"(D-NAO-14 "정확도
#   상시 공개")의 데이터 소스.
#
#   ⚠️ dry_run=True(Phase 5 골격, 이번 스프린트는 항상 이 값) 건은 검증 대상에서 제외한다 —
#   실제 입찰이 안 바뀌었으니 "전/후 성과"를 비교해도 우리 판단과 무관한 자연 변동만 보게
#   되고, 그걸 "제안 정확도"로 오귀속하면 거짓 신호가 된다(추정 금지). 실제 집행(dry_run=
#   False)이 열리기 전까지 이 SA는 자연히 대상이 0건 — 계획서 §4-Phase6 "관찰모드에선 데이터
#   없음(정상)"과 일치. predicted_json은 구조화 수치가 아니라 서술 텍스트(expected_effect,
#   P2-S3 plan-eng-review 결정)라 "예측값 대 실측값" 수치 비교는 하지 않는다 — 대신 전/후
#   RPC 트렌드 자체로 outcome을 판정한다(코드가 알 수 있는 유일한 객관적 신호).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverChangeLog, NaverLearningState
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
METRIC = "proposal_accuracy"
IMPROVED_RATIO = Decimal("1.1")  # 전/후 RPC 비율 ≥10% 개선 — improved (상식적 배수, anomaly_feed 전례 수준)
DECLINED_RATIO = Decimal("0.9")  # ≤10% 악화 — declined. 사이는 neutral.


def _aggregate_entity_metrics(
    db: Session, entity_type: str, entity_id: str, campaign_id: str, date_from: date, date_to: date,
) -> dict:
    """entity_type/entity_id 기준 naver_ad_daily 클릭·전환매출 합계(campaign 레벨은 entity_id
    무시하고 campaign_id로 집계)."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if entity_type == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == entity_id)
    elif entity_type == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == entity_id)
    else:  # campaign
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    clk, direct, indirect = q.first()
    clk = int(clk)
    conv_amt = int(direct) + int(indirect)
    rpc = (Decimal(conv_amt) / Decimal(clk)) if clk > 0 else None
    return {"clk": clk, "conv_amt": conv_amt, "rpc": rpc}


def evaluate_change(db: Session, change: NaverChangeLog, *, today: date | None = None) -> dict:
    """change_log 1건의 전/후 RPC 트렌드로 outcome 판정. 반환: {"outcome", "actual_json"}.

    executed_at 이전 동일 길이 창(before)과 executed_at부터 동일 길이 창(after)을 비교
    (codex 지적 — verify_date를 그대로 after_to로 쓰면 before는 window_days일인데 after는
    window_days+1일이 되어(양끝 포함) 창 길이가 달라 RPC 비교가 편향된다. after도 정확히
    window_days일로 맞춘다 — verify_date 자체를 반드시 마지막 날로 쓸 필요는 없다, 검증
    "기한"일 뿐 관측 창의 끝이어야 하는 건 아니다).
    모수게이트(양쪽 창 모두 clk>=LOW_CLICK_THRESHOLD) 미달이면 outcome=None(판정 보류 —
    억지로 improved/declined를 매기지 않는다).
    """
    today = today or kst_today()
    executed_date = change.executed_at.date() if isinstance(change.executed_at, datetime) else change.executed_at
    verify_date = change.verify_date or today
    window_days = max((verify_date - executed_date).days, 1)

    after_from = executed_date
    after_to = executed_date + timedelta(days=window_days - 1)
    before_from = executed_date - timedelta(days=window_days)
    before_to = executed_date - timedelta(days=1)

    after = _aggregate_entity_metrics(db, change.entity_type, change.entity_id, change.campaign_id, after_from, after_to)
    before = _aggregate_entity_metrics(db, change.entity_type, change.entity_id, change.campaign_id, before_from, before_to)

    actual_json = json.dumps({
        "before": {"clk": before["clk"], "conv_amt": before["conv_amt"]},
        "after": {"clk": after["clk"], "conv_amt": after["conv_amt"]},
    }, ensure_ascii=False)

    if before["clk"] < LOW_CLICK_THRESHOLD or after["clk"] < LOW_CLICK_THRESHOLD:
        return {"outcome": None, "actual_json": actual_json}
    if before["rpc"] is None or after["rpc"] is None or before["rpc"] <= 0:
        return {"outcome": None, "actual_json": actual_json}

    ratio = (after["rpc"] / before["rpc"]).quantize(_Q4)
    if ratio >= IMPROVED_RATIO:
        outcome = "improved"
    elif ratio <= DECLINED_RATIO:
        outcome = "declined"
    else:
        outcome = "neutral"
    return {"outcome": outcome, "actual_json": actual_json}


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """매일 실행 — verify_date 지난 미검증 실집행(dry_run=False) 건을 판정 + 제안유형별
    정확도(improved 비율) 롤업. dry_run=True 건은 대상 아님(모듈 docstring 참조).
    """
    today = today or kst_today()
    pending = db.query(NaverChangeLog).filter(
        NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.verify_date.isnot(None), NaverChangeLog.verify_date <= today,
        NaverChangeLog.outcome.is_(None),
    ).all()

    verified = 0
    for change in pending:
        result = evaluate_change(db, change, today=today)
        change.actual_json = result["actual_json"]
        if result["outcome"] is not None:
            change.outcome = result["outcome"]
            verified += 1
        # outcome=None(모수 미달)은 actual_json만 채우고 다음 회차 재시도 대상으로 남김
    db.commit()

    accuracy_by_action = _rollup_accuracy(db)
    for action, stats in accuracy_by_action.items():
        confidence = min(Decimal(1), Decimal(stats["n"]) / Decimal(10))
        row = db.query(NaverLearningState).filter(
            NaverLearningState.scope == "action_type", NaverLearningState.scope_key == action,
            NaverLearningState.metric == METRIC,
        ).first()
        if row is None:
            row = NaverLearningState(scope="action_type", scope_key=action, metric=METRIC)
            db.add(row)
        row.current_value = stats["improved_ratio"]
        row.sample_n = stats["n"]
        row.confidence = confidence.quantize(_Q4)
    db.commit()

    log.info("proposal_scoreboard: pending=%d verified=%d", len(pending), verified)
    return {"pending": len(pending), "verified": verified, "accuracy_by_action": accuracy_by_action}


def _rollup_accuracy(db: Session) -> dict:
    """action별 outcome 분포에서 improved 비율 산출(전체 대상=improved+declined+neutral,
    outcome=None인 미판정 건은 분모에서 제외 — 정직 경계). dry_run=False만 대상(codex 지적 —
    run_daily의 pending 조회는 이미 dry_run=False로 걸렀지만, 이 롤업은 change_log 테이블
    전체를 다시 훑으므로 dry_run=True 건에 수동/과거 경로로 outcome이 채워져 있어도 정확도
    통계에 섞이지 않도록 여기서도 동일 필터를 명시한다)."""
    rows = db.query(NaverChangeLog.action, NaverChangeLog.outcome).filter(
        NaverChangeLog.outcome.isnot(None), NaverChangeLog.dry_run.is_(False),
    ).all()
    by_action: dict[str, dict[str, int]] = {}
    for action, outcome in rows:
        stats = by_action.setdefault(action, {"improved": 0, "n": 0})
        stats["n"] += 1
        if outcome == "improved":
            stats["improved"] += 1
    return {
        action: {"n": s["n"], "improved_ratio": (Decimal(s["improved"]) / Decimal(s["n"])).quantize(_Q4)}
        for action, s in by_action.items()
    }
