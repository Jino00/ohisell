# wisdom_scorecard.py — 지혜 성적표 SA (M3-a, 계약 docs/PLAN_naver-m3-wisdom-scorecard.md §6)
#
# 역할: **지혜 id → 그 지혜가 낳은 제안들 → 그 제안이 낳은 조치 → 총이익·GAVE 롤업**을
# 한 곳에서 잇는다. 북극성 ref 82 §5-3 ①의 진단 *"지혜 id로 묶는 조인 하나가 없다"*가
# 2026-08-22 실측에서도 **참**으로 확인됐고(아래 «귀속의 한계» 참조), 이 모듈이 그 조인이다.
#
# ★계약 §8-Q3 확정 = **확장**이다 — 새 원장을 만들지 않는다. 롤업 원료는 M3-b가 심은
#   `naver_change_log`의 4컬럼(`outcome_profit`·`gave_before`·`gave_after`·`bep_source`)이고,
#   이 모듈은 **읽기 시점 집계**만 한다(마이그레이션 0건).
#
# ★이 성적표는 «값이 없다»를 숨기지 않는다. 2026-08-22 실측 기준 롤업 원료는 전건 비어 있다
#   (신규 실집행이 07-30 이후 0건 · 기존 150건은 `run_daily` 필터 `outcome IS NULL`에 걸려
#   영구 제외). 그러므로 표본 0을 «0점»이나 «문제없음»으로 렌더하면 안 된다 — 각 행에
#   `has_evidence`와 `evidence_gap`을 실어 «아직 잴 것이 없다»와 «재 봤더니 나빴다»를 가른다
#   (교훈 #318: 카운터가 있어야 침묵을 본다 / 품질지수 qi_grade=4 죽은 신호의 재발 방지).

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupProduct,
    NaverAdgroupTargetCurrent,
    NaverChangeLog,
    NaverProductBep,
    NaverProposal,
    OpsWisdomEntry,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ★값 정의 상수 — 계약 §4-B ⑥이 「성적표 산출물에 명시」를 요구하는 세 축.
#   문자열을 여기 한 곳에만 두는 이유: 화면·API·문서가 각자 다른 말을 하면
#   「무엇을 잰 숫자인가」가 다시 흐려진다.
PROFIT_FORMULA = "(conv_amt x cf / bep_roas) - cost"  # D-NAO-225 확정식(총이익 델타의 부호 비교)
PROFIT_GRAIN = "조치 1건 (naver_change_log 행)"
CONVERSION_DELAY_WINDOW = "D+1~D+7 (전환 정착 창)"

_ATTRIBUTION_PATH = "OpsWisdomEntry.param_proposal_id -> NaverProposal -> NaverChangeLog"
_ATTRIBUTION_LIMIT = (
    "추적 가능한 경로는 param_proposal_id 1:1 링크뿐이다. "
    "wisdom_apply.active_wisdom_prefix()는 지혜를 «자유 텍스트»로 전문가 브리핑에 주입하므로, "
    "그 지혜를 참고해 나온 이후 제안·조치에는 지혜 id가 원천적으로 남지 않는다. "
    "따라서 이 성적표의 롤업은 지혜 기여의 «하한»이다 — 0이라고 해서 기여가 없었다는 뜻이 아니다."
)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat(sep=" ", timespec="seconds") if v else None


def _maturity_state() -> dict:
    """전환 정착 지연의 «실제» 처리 상태. 곡선은 매일 산출되지만 보정 적용은 꺼져 있다
    (`bid_ceiling_calculator.MATURITY_CORRECTION_ENABLED = False` — 곡선이 days 8~18에서
    산술적으로 퇴화해 신뢰 불가). ★「지연을 다룬다」고 쓰면 거짓이므로 상태를 그대로 싣는다."""
    try:
        from app.services.naver_ad import bid_ceiling_calculator

        enabled = bool(getattr(bid_ceiling_calculator, "MATURITY_CORRECTION_ENABLED", False))
    except Exception as e:  # noqa: BLE001 — 상태 조회 실패는 성적표를 죽일 이유가 아니다
        log.warning("wisdom_scorecard: maturity 플래그 조회 실패: %s", e)
        return {"window": CONVERSION_DELAY_WINDOW, "correction_applied": None,
                "note": "보정 플래그를 읽지 못했다(판정불능)"}
    return {
        "window": CONVERSION_DELAY_WINDOW,
        "correction_applied": enabled,
        "note": (
            "정착 곡선은 매일 산출되나 보정 배수는 **적용되지 않는다**(곡선 퇴화로 비활성). "
            "즉 이 성적표의 총이익은 «정착 보정 전» 값이다."
            if not enabled else "정착 보정이 적용된 값이다."
        ),
    }


def _bep_coverage(db: Session) -> dict:
    """계약 §4-B ⑥ 커버리지 — 상품BEP를 실제로 해석할 수 있는 그룹의 비율.

    ★분모를 «현재 살아 있는 전수 스윕 유니버스»(naver_adgroup_target_current)로 둔다.
      과거 문서의 「230/854」는 391일 밴드 코호트의 정적 스냅샷이라 재실행이 불가능하다 —
      그 숫자를 그대로 인용하면 stale을 확정값처럼 쓰는 것이 된다."""
    try:
        total = db.query(NaverAdgroupTargetCurrent.adgroup_id).distinct().count()
        resolvable = (
            db.query(NaverAdgroupProduct.adgroup_id)
            .join(
                NaverProductBep,
                NaverProductBep.channel_product_id == NaverAdgroupProduct.mall_product_id,
            )
            .filter(
                NaverProductBep.channel_id == 6,
                NaverProductBep.has_cost.is_(True),
                NaverProductBep.bep_roas.isnot(None),
            )
            .distinct()
            .count()
        )
    except Exception as e:  # noqa: BLE001
        log.warning("wisdom_scorecard: BEP 커버리지 산출 실패: %s", e)
        return {"groups_total": None, "groups_with_product_bep": None, "ratio": None,
                "note": "커버리지 산출에 실패했다(판정불능)"}
    ratio = round(resolvable / total, 4) if total else None
    return {
        "groups_total": total,
        "groups_with_product_bep": resolvable,
        "ratio": ratio,
        "note": (
            "미확보 그룹은 계정 블렌디드 BEP로 «근사»된다 — 그 근사로 잰 행은 "
            "bep_source='account_default'로 표시된다."
        ),
    }


def _change_rows_for(db: Session, proposal_ids: list[int]) -> list[NaverChangeLog]:
    """제안 id 집합이 낳은 조치 행. 두 방향을 모두 본다 —
    ①`NaverProposal.executed_change_log_id`(제안→조치) ②`NaverChangeLog.proposal_id`(조치→제안).
    한 방향만 보면 한쪽만 채워진 행을 놓친다(이 저장소에 둘 다 실재한다)."""
    if not proposal_ids:
        return []
    ids: set[int] = set()
    for (clid,) in (
        db.query(NaverProposal.executed_change_log_id)
        .filter(NaverProposal.id.in_(proposal_ids), NaverProposal.executed_change_log_id.isnot(None))
        .all()
    ):
        ids.add(clid)
    rows = db.query(NaverChangeLog).filter(NaverChangeLog.proposal_id.in_(proposal_ids)).all()
    seen = {r.id for r in rows}
    if ids - seen:
        rows += db.query(NaverChangeLog).filter(NaverChangeLog.id.in_(list(ids - seen))).all()
    return sorted(rows, key=lambda r: (r.changed_at or datetime.min))


def _rollup_changes(rows: list[NaverChangeLog]) -> dict:
    """조치 행들의 총이익·GAVE·BEP 렌즈 롤업.

    ★계약 §8-Q5 확정 각주의 명시 요구: 판정이 «부호» 비교라 아주 작은 델타도 판정이 된다 —
      그래서 롤업은 판정 «개수»만 세지 않고 GAVE 델타의 «크기»를 함께 낸다."""
    verdicts: dict[str, int] = {}
    bep_sources: dict[str, int] = {}
    gave_before_sum = 0.0
    gave_after_sum = 0.0
    gave_pairs = 0
    details = []
    for r in rows:
        if r.outcome_profit:
            verdicts[r.outcome_profit] = verdicts.get(r.outcome_profit, 0) + 1
        src = r.bep_source or "unmeasured"
        bep_sources[src] = bep_sources.get(src, 0) + 1
        gb, ga = _num(r.gave_before), _num(r.gave_after)
        if gb is not None and ga is not None:
            gave_before_sum += gb
            gave_after_sum += ga
            gave_pairs += 1
        details.append({
            "change_log_id": r.id,
            "changed_at": _iso(r.changed_at),
            "action": r.action,
            "campaign_id": r.campaign_id,
            "dry_run": bool(r.dry_run),
            "outcome_legacy": r.outcome,          # 옛 자(효율 배율) — 불변 증거(§8-Q1)
            "outcome_profit": r.outcome_profit,   # 새 자(총이익 델타 부호)
            "gave_before": gb,
            "gave_after": ga,
            "gave_delta": (ga - gb) if (gb is not None and ga is not None) else None,
            "bep_source": r.bep_source,           # 계약 §4-B ⑥ 값 정확도 라벨
        })
    scored = sum(verdicts.values())
    return {
        "changes_total": len(rows),
        "changes_scored_profit": scored,
        "verdicts": verdicts,
        "bep_sources": bep_sources,
        "gave_before_sum": round(gave_before_sum, 4) if gave_pairs else None,
        "gave_after_sum": round(gave_after_sum, 4) if gave_pairs else None,
        "gave_delta_sum": round(gave_after_sum - gave_before_sum, 4) if gave_pairs else None,
        "gave_pairs": gave_pairs,
        "details": details,
    }


def _score_entry(db: Session, entry: OpsWisdomEntry) -> dict:
    proposal_ids = [entry.param_proposal_id] if entry.param_proposal_id else []
    proposals = (
        db.query(NaverProposal).filter(NaverProposal.id.in_(proposal_ids)).all()
        if proposal_ids else []
    )
    rows = _change_rows_for(db, proposal_ids)
    rollup = _rollup_changes(rows)

    # ★「왜 잴 것이 없나」를 행 스스로 말하게 한다 — 빈 성적표가 «좋은 성적»으로 읽히면
    #   그게 정확히 qi_grade=4 죽은 신호의 재발이다.
    gap = None
    if rollup["changes_scored_profit"] == 0:
        if not proposal_ids:
            gap = "이 지혜는 아직 제안을 낳지 않았다(param_proposal_id 없음)."
        elif not rows:
            gap = (
                "제안은 났으나 실집행 조치가 0건이다 "
                f"(제안 상태: {', '.join(sorted({p.status for p in proposals})) or '알 수 없음'})."
            )
        else:
            gap = "조치는 있으나 새 식으로 채점된 행이 0건이다(채점 대기 또는 모수 미달)."

    return {
        "wisdom_id": entry.id,
        "wisdom_text": entry.wisdom_text,
        "status": entry.status,
        "promoted_at": _iso(entry.promoted_at),
        "source_candidate_id": entry.source_candidate_id,
        "linked_proposals": [
            {"proposal_id": p.id, "proposal_type": p.proposal_type, "status": p.status,
             "campaign_id": p.campaign_id, "executed_change_log_id": p.executed_change_log_id}
            for p in proposals
        ],
        "linked_proposal_count": len(proposals),
        "has_evidence": rollup["changes_scored_profit"] > 0,
        "evidence_gap": gap,
        **rollup,
    }


def build(db: Session, *, wisdom_id: int | None = None) -> dict:
    """지혜 성적표 산출(읽기 전용). wisdom_id를 주면 그 1건만."""
    q = db.query(OpsWisdomEntry).order_by(OpsWisdomEntry.id)
    if wisdom_id is not None:
        q = q.filter(OpsWisdomEntry.id == wisdom_id)
    entries = q.all()
    rows = [_score_entry(db, e) for e in entries]

    return {
        "generated_at_kst": kst_now().isoformat(sep=" ", timespec="seconds"),
        "wisdom_total": len(rows),
        "wisdom_with_evidence": sum(1 for r in rows if r["has_evidence"]),
        # ★계약 §4-B ⑥ — 「총이익」 값의 정의를 산출물이 스스로 명시한다.
        "value_definition": {
            "metric": "총이익(gross profit) 절대액",
            "formula": PROFIT_FORMULA,
            "grain": PROFIT_GRAIN,
            "verdict_rule": "조치 전/후 총이익의 «부호» 비교(D-NAO-225). 크기는 gave_delta로 별도 표시",
            "conversion_delay": _maturity_state(),
            "bep_coverage": _bep_coverage(db),
            "legacy_note": (
                "outcome(옛 자)은 효율 배율 기반이라 이 성적표의 outcome_profit과 다른 것을 잰다. "
                "옛 값은 증거로 불변 보존한다(§8-Q1)."
            ),
        },
        "attribution": {"path": _ATTRIBUTION_PATH, "limitation": _ATTRIBUTION_LIMIT},
        "wisdom": rows,
    }
