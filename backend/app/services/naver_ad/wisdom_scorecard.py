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

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import (
    NaverAdgroupProduct,
    NaverAdgroupTargetCurrent,
    NaverChangeLog,
    NaverProductBep,
    NaverProposal,
    OpsDiaryEntry,
    OpsWisdomCandidate,
    OpsWisdomEntry,
)
from app.services.naver_ad import guardrail_params
from app.services.naver_ad import reflection_health
from app.services.naver_ad import wisdom_apply
from app.services.naver_ad.wisdom_candidates import (
    _HARVEST_LOOKBACK_DAYS,
    _reopen_ready,
    HARVEST_EVENT_TYPES,
    RETRO_HARVEST_LABEL,
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

# ★D-NAO-248 §4-A(A7② 브리핑 주입 여부) — wisdom_apply.active_wisdom_prefix()가 실제로 쓰는
#   질의(status=active·promoted_at desc·limit)를 여기서 «재현»한다(그 함수를 호출하면 문자열만
#   돌아와 지혜 id 단위로 역파싱해야 하는데, truncate(500자)·동일 텍스트 중복이 있으면
#   역파싱이 깨진다 — 그래서 같은 질의를 다시 짜는 쪽이 더 정직하다. 이 저장소의 기존 관례:
#   proposal_pipeline.py가 retro_scorer._gamma_for를 같은 이유로 재현한다).
#   ★값은 wisdom_apply._PREFIX_LIMIT(=10)과 반드시 같아야 한다 — 갈라지면 이 표시가 거짓말이
#   된다. 바뀌면 두 곳을 함께 고친다.
_BRIEFING_INJECT_LIMIT = 10
_BRIEFING_ATTRIBUTION_NOTE = (
    "주입 여부만 관측한다 — active_wisdom_prefix()는 지혜를 자유 텍스트로 브리핑에 얹을 뿐이라 "
    "«주입됐다»가 «그 덕에 좋은 결과가 났다»를 뜻하지 않는다(위 attribution.limitation과 같은 한계)."
)


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _iso(v: Optional[datetime]) -> Optional[str]:
    return v.isoformat(sep=" ", timespec="seconds") if v else None


def _profit_amounts(change: NaverChangeLog) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """조치 1건의 총이익 «금액»(전/후/델타). 원료는 `naver_change_log.actual_json`이다.

    ★적대 리뷰 1R P1-1이 잡은 것: 초판은 총이익 «라벨»(improved/declined)만 냈고 금액을 안 냈다.
      그러면 화면의 유일한 «크기» 숫자가 GAVE 델타가 되는데, **GAVE에는 비용을 빼는 항이 없어서**
      「총이익 악화 1건 · GAVE 델타 +250,000」처럼 판정과 크기가 서로 반대를 가리킬 수 있다.
      그건 D-NAO-225가 채점기에서 걷어낸 바로 그 병이 표시층으로 되돌아온 것이다.
    ★식은 `proposal_scoreboard._gross_profit`과 «같은 것»이다 —
      총이익 = (conv_amt x cf / bep) - cost (D-NAO-59가 최대화하라고 한 그 양).
      그 함수는 module-private이라 크로스임포트하지 않고 여기서 재현한다(이 저장소 관례 —
      `proposal_pipeline.py`가 `retro_scorer._gamma_for`를 같은 이유로 재현한다).
    ★렌즈(bep·cf)가 `actual_json`에 없으면 **금액을 지어내지 않는다** — (None,None,None)을
      돌려주고 호출부가 «산출불가»로 센다. 2026-08-22 실측 기준 prod의 기존 249개
      `actual_json` 중 `lens`를 가진 것은 0개다(렌즈는 M3-b부터 적히므로 소급되지 않는다).
    """
    raw = change.actual_json
    if not raw:
        return (None, None, None)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return (None, None, None)
    if not isinstance(payload, dict):
        return (None, None, None)
    lens = payload.get("lens") or {}
    bep, cf = lens.get("bep"), lens.get("cf")
    # ★정본(`proposal_scoreboard._profit_verdict`)은 bep <= 0을 거부한다 — 가드를 맞춘다
    #   (적대 리뷰 2R P2. 현재 원장엔 음수 bep이 안 생기지만 두 자가 다르면 언젠가 갈린다).
    if bep is None or cf is None:
        return (None, None, None)
    try:
        if float(bep) <= 0:
            return (None, None, None)
    except (TypeError, ValueError):
        return (None, None, None)
    out: list[Optional[float]] = []
    for side in ("before", "after"):
        w = payload.get(side) or {}
        conv_amt, cost = w.get("conv_amt"), w.get("cost")
        if conv_amt is None or cost is None:
            return (None, None, None)
        try:
            out.append(round(float(conv_amt) * float(cf) / float(bep) - float(cost), 2))
        except (TypeError, ValueError, ZeroDivisionError):
            return (None, None, None)
    return (out[0], out[1], round(out[1] - out[0], 2))


def _maturity_state() -> dict:
    """전환 정착 지연의 «실제» 처리 상태. 곡선은 매일 산출되지만 보정 적용은 꺼져 있다
    (`bid_ceiling_calculator.MATURITY_CORRECTION_ENABLED = False` — 곡선이 days 8~18에서
    산술적으로 퇴화해 신뢰 불가). ★「지연을 다룬다」고 쓰면 거짓이므로 상태를 그대로 싣는다."""
    try:
        from app.services.naver_ad import bid_ceiling_calculator

        # ★적대 리뷰 BM5: getattr 폴백 False를 쓰면 상수가 개명·삭제돼도 성적표는
        #   「보정 미적용」을 **확신에 차서** 계속 말한다. 없으면 모른다고 해야 한다.
        if not hasattr(bid_ceiling_calculator, "MATURITY_CORRECTION_ENABLED"):
            raise AttributeError("MATURITY_CORRECTION_ENABLED 상수가 없다")
        enabled = bool(bid_ceiling_calculator.MATURITY_CORRECTION_ENABLED)
    except Exception as e:  # noqa: BLE001 — 상태 조회 실패는 성적표를 죽일 이유가 아니다
        log.warning("wisdom_scorecard: maturity 플래그 조회 실패: %s", e)
        return {"window": CONVERSION_DELAY_WINDOW, "correction_applied": None,
                "note": "정착 보정 상태를 읽지 못했다(판정불능) — 보정 여부를 단정하지 않는다"}
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
        # ★적대 리뷰 P2-1: 분자(naver_adgroup_product = 삭제하지 않는 누적 원장)가 분모
        #   (naver_adgroup_target_current = 현재 스윕)보다 클 수 있다 — 그러면 화면에
        #   「5/2 그룹(250.0%)」 같은 숫자가 뜬다. 분자를 분모 유니버스로 가둔다.
        resolvable = (
            db.query(NaverAdgroupProduct.adgroup_id)
            .join(
                NaverProductBep,
                NaverProductBep.channel_product_id == NaverAdgroupProduct.mall_product_id,
            )
            .join(
                NaverAdgroupTargetCurrent,
                NaverAdgroupTargetCurrent.adgroup_id == NaverAdgroupProduct.adgroup_id,
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
    profit_before_sum = 0.0
    profit_after_sum = 0.0
    profit_pairs = 0
    profit_unavailable = 0
    profit_unjudged = 0
    executed = 0
    details = []
    for r in rows:
        if r.outcome_profit:
            verdicts[r.outcome_profit] = verdicts.get(r.outcome_profit, 0) + 1
        src = r.bep_source or "unmeasured"
        bep_sources[src] = bep_sources.get(src, 0) + 1
        gb, ga = _num(r.gave_before), _num(r.gave_after)
        # ★0.0은 GAVE의 정상값이다(min((ROAS/BEP)^gamma,1) x revenue). `if gb and ga`로 쓰면
        #   0을 짝에서 버려 행과 합계가 서로 다른 말을 한다(적대 리뷰 BM3 생존 변이).
        if gb is not None and ga is not None:
            gave_before_sum += gb
            gave_after_sum += ga
            gave_pairs += 1
        pb, pa, pd = _profit_amounts(r)
        # ★적대 리뷰 2R P1: 금액 합의 «집합»을 판정 집합에 맞춘다.
        #   `run_daily`는 모수 미달(양쪽 창 clk<10) 행에 대해 **판정은 보류하면서 actual_json은
        #   무조건 쓴다**(`proposal_scoreboard.py:290-294` + `:339`). 그래서 렌즈는 있고 판정은
        #   없는 행이 원장에 쌓이는데, 그걸 합에 섞으면 「채점 1/4건 · 총이익 개선 1건 ·
        #   총이익 델타 −2,000,000원(4건)」처럼 **판정과 크기가 서로 반대를 가리킨다** —
        #   P1-1이 고치려던 바로 그 증상이 «지표 불일치»가 아니라 «행 집합 불일치»로 재현된다.
        #   ⇒ 합은 판정된 행만. 보류 행은 버리지 않고 profit_unjudged로 «따로» 센다.
        # ★버킷 3종은 서로 겹치지 않고 «모든 행»을 덮는다:
        #   pairs(판정O·금액O) + unavailable(금액X) + unjudged(판정X·금액O) == changes_total.
        #   3R P2-1이 잡은 것: 초판은 「판정X·금액X」 행이 어느 버킷에도 안 들어가 조용히
        #   사라졌다. 분모가 어디로 갔는지 화면이 설명하지 못하면 그게 침묵이다.
        if pb is None or pa is None:
            profit_unavailable += 1
        elif r.outcome_profit is not None:
            profit_before_sum += pb
            profit_after_sum += pa
            profit_pairs += 1
        else:
            profit_unjudged += 1
        if not r.dry_run:
            executed += 1
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
            "profit_before": pb,          # 총이익 «금액»(원) — 계약 §4-A① "ad_profit 합"
            "profit_after": pa,
            "profit_delta": pd,
            "bep_source": r.bep_source,           # 계약 §4-B ⑥ 값 정확도 라벨
        })
    scored = sum(verdicts.values())
    return {
        "changes_total": len(rows),
        "changes_executed": executed,  # dry_run=False만 — 채점 대상이 될 수 있는 행
        "changes_scored_profit": scored,
        "verdicts": verdicts,
        "bep_sources": bep_sources,
        "gave_before_sum": round(gave_before_sum, 4) if gave_pairs else None,
        "gave_after_sum": round(gave_after_sum, 4) if gave_pairs else None,
        "gave_delta_sum": round(gave_after_sum - gave_before_sum, 4) if gave_pairs else None,
        "gave_pairs": gave_pairs,
        # ★계약 §4-A①이 요구한 «ad_profit 합». 판정 개수만으로는 「얼마나」에 답할 수 없고,
        #   GAVE는 비용 항이 없어 크기 축으로 쓰면 부호가 어긋난다(적대 리뷰 P1-1).
        "profit_before_sum": round(profit_before_sum, 2) if profit_pairs else None,
        "profit_after_sum": round(profit_after_sum, 2) if profit_pairs else None,
        "profit_delta_sum": round(profit_after_sum - profit_before_sum, 2) if profit_pairs else None,
        "profit_pairs": profit_pairs,
        "profit_unavailable": profit_unavailable,  # 렌즈 부재 등으로 «금액 산출불가»인 행수(판정 여부 무관)
        # ★채점기가 표본 미달로 «판정을 거부한» 행 중 금액은 계산되는 것들. 합에는 안 들어가고
        #   화면에 별도 표시된다 — 조용히 빠지면 분모가 어디로 갔는지 아무도 모른다.
        "profit_unjudged": profit_unjudged,
        "details": details,
    }


def _proposal_decision(p: NaverProposal, change_row: Optional[NaverChangeLog]) -> dict:
    """A7① 결정 메타 — D-NAO-248 §4-A가 심은 decided_at/decided_by/decision_note 3컬럼은
    이번 스프린트 신설(전부 nullable, 기존 행 무영향)이라 그 이전 결정분은 컬럼 자체가 없던
    시절에 결정됐다. decided_at IS NULL을 «아직 결정 안 됨»으로 읽으면 거짓이다 — 그 제안은
    실제로 승인/반려됐을 수 있다(예: 2314는 07-26 rejected). 그래서 일반 규칙(entry id를
    코드에 박지 않는다)으로 NULL을 「기록 없음(컬럼 신설 전)」이라고 명시한다.

    ★B4(D-NAO-248 §4-B) — 「지혜id → 제안id → 결정 메타 → change_log before/after」를 한 줄로
    잇는다. change_row는 호출부(_score_entry)가 p.executed_change_log_id로 이미 찾아 둔 것을
    그대로 받는다(같은 것을 다시 쿼리하지 않는다 — rows는 어차피 롤업을 위해 이미 불러와 있다).
    None이면 아직 반영되지 않았거나(pending/rejected) 승인 시 값이 무변화(같은 값 재승인)라
    change_log가 안 생긴 경우다 — 둘 다 「반영 증거 없음」으로 정직하게 나타난다.
    """
    if p.decided_at is None:
        decision: dict = {
            "decided_at": None,
            "decided_by": None,
            "decision_note": "기록 없음(컬럼 신설 전)",
        }
    else:
        decision = {
            "decided_at": _iso(p.decided_at),
            "decided_by": p.decided_by,
            "decision_note": p.decision_note,
        }
    decision["applied_change"] = (
        None if change_row is None else {
            "change_log_id": change_row.id,
            "before_value": change_row.before_value,
            "after_value": change_row.after_value,
            "action": change_row.action,
            "changed_at": _iso(change_row.changed_at),
        }
    )
    return decision


def _briefing_injected_ids(db: Session, *, limit: int = _BRIEFING_INJECT_LIMIT) -> set[int]:
    """A7② — wisdom_apply.active_wisdom_prefix()가 브리핑에 실제로 얹는 지혜 id 집합.
    같은 질의(status=active, promoted_at desc, id desc, limit)를 재현한다(위 상수 주석 참조)."""
    rows = (
        db.query(OpsWisdomEntry.id)
        .filter(OpsWisdomEntry.status == "active")
        .order_by(OpsWisdomEntry.promoted_at.desc(), OpsWisdomEntry.id.desc())
        .limit(limit)
        .all()
    )
    return {r[0] for r in rows}


def _score_entry(db: Session, entry: OpsWisdomEntry, injected_ids: set[int]) -> dict:
    proposal_ids = [entry.param_proposal_id] if entry.param_proposal_id else []
    proposals = (
        db.query(NaverProposal).filter(NaverProposal.id.in_(proposal_ids)).all()
        if proposal_ids else []
    )
    rows = _change_rows_for(db, proposal_ids)
    rows_by_id = {r.id: r for r in rows}  # B4 — executed_change_log_id로 재쿼리 없이 찾는다
    rollup = _rollup_changes(rows)

    # ★「왜 잴 것이 없나」를 행 스스로 말하게 한다 — 빈 성적표가 «좋은 성적»으로 읽히면
    #   그게 정확히 qi_grade=4 죽은 신호의 재발이다.
    gap = None
    if rollup["changes_scored_profit"] == 0:
        if not proposal_ids:
            gap = "이 지혜는 아직 제안을 낳지 않았다(param_proposal_id 없음)."
        elif not proposals:
            # ★적대 리뷰 P2-5: 링크는 있는데 그 제안 «행»이 없는 경우를 「실집행 0건」으로
            #   뭉뚱그리면 데이터 정합 문제가 운영 상태로 위장된다.
            gap = f"연결된 제안 행을 찾지 못했다(param_proposal_id={proposal_ids[0]})."
        elif not rows:
            gap = (
                "제안은 났으나 실집행 조치가 0건이다 "
                f"(제안 상태: {', '.join(sorted({p.status for p in proposals}))})."
            )
        elif rollup["changes_executed"] == 0:
            # ★적대 리뷰 P2-4: dry_run 행은 run_daily가 «영원히» 채점하지 않는다 —
            #   그걸 「채점 대기」라고 쓰면 오지 않을 것을 기다린다고 말하는 셈이다.
            gap = f"조치 {rollup['changes_total']}건이 전부 모의(dry_run)라 채점 대상이 아니다."
        else:
            gap = "조치는 있으나 새 식으로 채점된 행이 0건이다(채점 대기 또는 모수 미달)."
            # ★3R P2-2: 판정 행이 0이면 배지 줄이 통째로 안 뜬다 — 사유는 위 문구가 나르지만
            #   «몇 건»인지는 사라진다. 건수를 문구에 실어 보낸다.
            if rollup["profit_unjudged"]:
                gap += f" 그중 {rollup['profit_unjudged']}건은 표본 미달로 판정 보류다."

    return {
        "wisdom_id": entry.id,
        "wisdom_text": entry.wisdom_text,
        "status": entry.status,
        "promoted_at": _iso(entry.promoted_at),
        "source_candidate_id": entry.source_candidate_id,
        "linked_proposals": [
            {
                "proposal_id": p.id, "proposal_type": p.proposal_type, "status": p.status,
                "campaign_id": p.campaign_id, "executed_change_log_id": p.executed_change_log_id,
                # A7① decided_at/decided_by/decision_note(또는 「기록 없음」) + B4 applied_change
                **_proposal_decision(p, rows_by_id.get(p.executed_change_log_id)),
            }
            for p in proposals
        ],
        "linked_proposal_count": len(proposals),
        # A7② — 이 지혜가 «지금» 전문가 브리핑 자유 텍스트에 실리는가(위 attribution.limitation과
        # 같은 이유로 «주입=성과»가 아니다 — 성적은 여전히 rollup으로만 잰다).
        "briefing_injected": entry.id in injected_ids,
        "briefing_injection_note": _BRIEFING_ATTRIBUTION_NOTE,
        "has_evidence": rollup["changes_scored_profit"] > 0,
        "evidence_gap": gap,
        **rollup,
    }


def _candidate_bucket(c: OpsWisdomCandidate) -> str:
    """A2 경계 분리 판별 — D-NAO-248이 시그니처 접두사로 구조적으로 분리한 세 버킷 +
    옛(레거시) 캠페인 grain을 후보 «행» 하나로부터 판독한다(새 테이블 없이, wisdom_candidates
    의 접두사 규약을 그대로 문자열로 대조 — harvest_candidates._build_signature 참조).
    ⚠️grain='global'은 전역 풀·실험분리·미상분리 «셋 다»에 붙는다(harvest_candidates.py가
    known 여부와 무관하게 grain="global"을 쓴다) — 그래서 grain만으로는 못 가르고 signature
    접두사(+experiment_batch)까지 봐야 한다."""
    if c.grain is None:
        return "legacy"
    sig = c.signature or ""
    if sig.startswith("g?|"):
        return "separated_unknown"
    if c.experiment_batch:
        return "separated_experiment"
    return "global_pool"


_BUCKET_LABELS = {
    "legacy": "레거시(캠페인 grain, D-NAO-248 이전)",
    "global_pool": "전역 풀(캠페인 통합)",
    "separated_experiment": "실험배치 분리(전역 풀과 안 섞임)",
    "separated_unknown": "라벨미상 fail-closed 분리(캠페인 단위 고립)",
}


# d1_st.status의 알려진 4값(diary_outcome.py:170,183,194-201 정본). 이 밖은 fail-closed로
# "unknown"에 떨어뜨린다(harvest_candidates._D1_ST_SKIP_COUNTER·_UNKNOWN_STATUS_COUNTER와
# 같은 원칙 — 조용히 good/bad로 잘못 세는 것보다 스킵되는 쪽이 안전하다).
_D1_ST_KNOWN_STATUSES = ("stopped", "leaking", "ambiguous", "no_data")


def _search_term_material(db: Session) -> dict:
    """A2 재료 표면 — 계약 §C2 "재료 전건 왕복": harvest_candidates()가 «실행 시점에» 세는
    이벤트 카운터(search_term_good 등, wisdom_loop 로그에만 남는다)와 달리, 이건 target_type
    =="search_term" 일기 «전건»을 harvest_candidates와 **같은 lookback 창**으로 read-time에
    다시 집계한다(_candidate_status와 같은 스냅샷 관례 — 창 상수는 wisdom_candidates에서
    그대로 가져와 두 계산이 어긋나지 않게 한다).

    ★분모는 harvest_candidates()의 SQL 필터를 그대로 따르지 않는다 — 여기 목적은 «수확된
    것»이 아니라 «수확 대상이 될 수 있었던 재료 전건»이다(target_type만 거른다).

    ★`not_harvestable` 버킷(prod 실측으로 드러난 결함의 수정, 2026-08-25) — harvest_candidates()
    의 SQL 필터는 `event_type IN (execute, blocked)` ∧ `outcome_json IS NOT NULL` **둘 다**다.
    그 필터 밖에 있는 행(예: event_type="voided" — outcome 자체가 안 남는 이벤트)은
    d1_st가 나중에 채워지든 말든 harvest가 **원리적으로 절대 보지 않는다**. 예전 코드는 이런
    행도 "absent"(= d1_st만 없는 행, 즉 「채워지면 처리될 행」)로 셌는데, 그건 부정직하다 —
    "absent"라는 이름이 「아직 안 왔을 뿐 언젠가 올 것」을 암시하기 때문이다. 그래서 여기서는
    ①event_type이 HARVEST_EVENT_TYPES 밖이거나 ②outcome_json이 비어 있는 행을 먼저
    `not_harvestable`로 갈라내고, `absent`는 **harvest가 보긴 하는데(=①②를 통과) d1_st 키
    자체만 없는 행**으로 의미를 좁힌다.
    (prod 실측: search_term 일기 3건 = execute 2건[outcome 보유, d1_st.status=stopped] +
    voided 1건[outcome 없음] → voided 1건은 not_harvestable, absent는 0이어야 정직하다.)

    ★이 총수·분포가 몇 건이든 «검색어 지혜가 났다»는 뜻이 아니다 — label이 그 취지를 담는다
    (D-NAO-247 점화 전 계약, «검색어 지혜가 났다» 주장 금지 — 점화 후 몫)."""
    now = kst_now()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_HARVEST_LOOKBACK_DAYS)
    rows = (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.target_type == "search_term",
            OpsDiaryEntry.created_at.isnot(None),
            OpsDiaryEntry.created_at >= lower_utc,
        )
        .all()
    )
    by_status: dict[str, int] = {
        k: 0
        for k in ("stopped", "leaking", "ambiguous", "no_data", "absent", "unknown", "not_harvestable")
    }
    for entry in rows:
        # ① harvest_candidates()의 자체 필터에 애초에 안 걸리는 행 — d1_st 값과 무관하게
        #   harvest는 이 행을 절대 소비하지 않는다.
        if entry.event_type not in HARVEST_EVENT_TYPES or not entry.outcome_json:
            by_status["not_harvestable"] += 1
            continue
        # ② 여기부터는 harvest가 «보는» 행이다 — d1_st 키 자체가 없으면 absent(좁힌 의미).
        status_key = "absent"
        try:
            outcome = json.loads(entry.outcome_json)
        except (TypeError, ValueError):
            outcome = None
        d1_st = outcome.get("d1_st") if isinstance(outcome, dict) else None
        if d1_st:
            status = d1_st.get("status")
            status_key = status if status in _D1_ST_KNOWN_STATUSES else "unknown"
        by_status[status_key] += 1
    total = len(rows)
    harvestable = total - by_status["not_harvestable"]
    skip_count = harvestable - by_status["stopped"] - by_status["leaking"]
    label = (
        f"재료 {total}건 — 수확 대상 {harvestable}건(good {by_status['stopped']}·"
        f"bad {by_status['leaking']}·skip {skip_count}) / 수확 대상 밖 {by_status['not_harvestable']}건"
        " — 검색어 «지혜가 났다»는 뜻이 아니다(점화 후 몫, D-NAO-247)."
    )
    return {"total": total, "by_status": by_status, "label": label}


def _judge_backlog(db: Session) -> dict:
    """★D-NAO-251 §4-③ — 판사 대기열 적체 지표(읽기 전용 스냅샷).

    `pending_ripe`는 «지금 판사에게 갈 자격이 있는» 후보 수다(TTL 14일 경과 or occurrences≥3,
    `wisdom_judge._is_ripe`와 같은 판정을 재사용한다 — 문턱을 두 곳에 복사하지 않는다).
    `days_to_drain`은 크론이 1일 1회라 «회차 수 = 일수»인 데서 나온 산술이며, 신규 후보 유입이
    0이라는 가정 위의 값이다(그 가정을 라벨에 적어 둔다 — 창을 안 밝힌 커버리지 주장은 이
    저장소의 반복 실패다).
    """
    from app.services.naver_ad import wisdom_judge as _wj

    now = kst_now()
    pending = db.query(OpsWisdomCandidate).filter(OpsWisdomCandidate.status == "pending").all()
    ripe = [c for c in pending if _wj._is_ripe(c, now) and c.action]
    cap = _wj._MAX_PER_RUN_BACKLOG if len(ripe) > _wj._MAX_PER_RUN else _wj._MAX_PER_RUN
    return {
        "pending_total": len(pending),
        "pending_ripe": len(ripe),
        "cap_next_run": cap,
        "days_to_drain": (len(ripe) + cap - 1) // cap if ripe else 0,
        "cron": "08:45 KST 1일 1회 (캐치업 크론 없음 — 적체는 회차 상한으로 흡수)",
        "assumption": "days_to_drain은 신규 후보 유입 0 가정. 실제로는 매일 새 후보가 생긴다.",
    }


def _candidate_status(db: Session) -> dict:
    """A2 관측 표면 — 지혜 승격 «후보»(OpsWisdomCandidate) 현황. wisdom_scorecard의 나머지는
    이미 승격된 지혜(OpsWisdomEntry)만 보므로, 승격 전 파이프라인(harvest_candidates가 매일
    쌓는 27+N행)은 이 성적표 어디에도 없었다 — 그 공백을 메운다(계약 §4 A2).

    ★읽기 전용 스냅샷이다: harvest_candidates()가 실행 «시점»에 세는 totals(이벤트 단위 카운터,
    회차마다 리셋)와 다르게, 이건 **현재 저장된 후보 행**을 매번 다시 세므로 재현 가능하고
    회차 사이에도 최신값을 낸다(계약이 새 테이블을 금지했으므로 D-NAO-248이 심은 4컬럼만
    읽어 파생한다 — 부록 Q2 "합치되 이질성은 판사에게 보인다"를 화면에서도 지킨다)."""
    cands = db.query(OpsWisdomCandidate).order_by(OpsWisdomCandidate.id.desc()).all()
    bucket_counts: dict[str, int] = {k: 0 for k in _BUCKET_LABELS}
    rows = []
    for c in cands:
        bucket = _candidate_bucket(c)
        bucket_counts[bucket] += 1
        by_campaign = json.loads(c.by_campaign_json) if c.by_campaign_json else {}
        # ★레거시 행은 by_campaign_json이 없다(컬럼 신설 전 — 항상 NULL). 시그니처 자체가
        #   캠페인 1개 단위였으므로 campaign_id가 있으면 1로 파생한다(그 이상은 지어내지 않는다).
        campaign_count = len(by_campaign) if by_campaign else (1 if c.campaign_id else 0)
        rows.append({
            "candidate_id": c.id,
            "signature": c.signature,
            "status": c.status,
            "grain": c.grain,  # None=레거시(D-NAO-248 이전) / 'global'=신형
            "bucket": bucket,
            "bucket_label": _BUCKET_LABELS[bucket],
            "campaign_type": c.campaign_type,
            "experiment_batch": c.experiment_batch,
            "action": c.action,
            "occurrences": c.occurrences,
            "good_count": c.good_count,
            "bad_count": c.bad_count,
            "campaign_count": campaign_count,
            "by_campaign": by_campaign,  # {} = 레거시(캠페인 1개, 분해 없음) / {campaign_id: {good,bad}}
            "observation": c.observation,
            "first_seen_at": _iso(c.first_seen_at),
            "last_seen_at": _iso(c.last_seen_at),
            # ★D-NAO-251 §5 ①-b/①-c 관측 표면 — 기각분의 증거가 판정 이후 얼마나 더 쌓였는지,
            #   재심 여력이 남았는지를 화면에서 바로 본다. judged_occurrences가 None이면
            #   「아직 판정된 적 없음」이고 occurrences_since_judgment도 None이다(0이 아니다 —
            #   0으로 내면 「판정 후 하나도 안 쌓임」과 구별이 안 된다).
            "judged_at": _iso(c.judged_at),
            "judged_occurrences": c.judged_occurrences,
            "occurrences_since_judgment": (
                None if c.judged_occurrences is None
                else (c.occurrences or 0) - c.judged_occurrences
            ),
            "rejudge_count": c.rejudge_count or 0,
            "reopen_ready": _reopen_ready(c) if c.status == "rejected" else False,
            "prior_judgment_count": len(json.loads(c.prior_judgments_json or "[]")),
        })
    return {
        "candidates_total": len(cands),
        "bucket_counts": bucket_counts,
        "bucket_labels": _BUCKET_LABELS,
        # ★D-NAO-251 §4-③ — 판사 적체가 침묵하지 않게 한다. 「pending 17건인데 회당 5건」이
        # 어디에도 안 보이던 것이 이 계약이 고치는 결함 셋 중 하나다(교훈 #318).
        "judge_backlog": _judge_backlog(db),
        # A2 「기존 재료 재집계」 라벨 — harvest_candidates.RETRO_HARVEST_LABEL과 문자열 동일
        # (한 곳에서만 정의 — wisdom_candidates.py 참조).
        "retro_harvest_label": RETRO_HARVEST_LABEL,
        "candidates": rows,
        # ★B7-6(D-NAO-248 §4-B) — param_suggestion이 코드 클램프에서 어떻게 갈렸는지(제안
        # 생성/조건부 폴백/미매핑/제안 없음). 0이어도 침묵하지 않는다(교훈 #318).
        "param_gate": wisdom_apply.gate_summary(db),
        # ★계약 §C2 「재료 전건 왕복」 — target_type=="search_term" 일기가 소급 재수확에서
        # tally 반영/skip 사유로 어떻게 갈렸는지 A2 표면에서 관측되게 한다(0이어도 키를 낸다).
        "search_term_material": _search_term_material(db),
    }


# ★B5 대칭·탐색 관측(D-NAO-247 점화 계약) — 탐색 몫·탐색 차단률의 관측 창(일). 커버리지
#   주장은 창을 밝혀야 한다(이 저장소 반복 교훈) — 응답에 window_days로 실어 나른다.
#   ★액셀/브레이크 분류(①)는 이 창을 걸지 않는다 — 질문이 「지혜가 지금까지 조인 이력 전체가
#   대칭인가」이지 「최근 N일만」이 아니다. 최근 N일로 자르면 옛 조치가 조용히 사라진다.
_SYMMETRY_WINDOW_DAYS = 28


def _param_direction_events(before: dict, after: dict) -> dict[str, str]:
    """before/after dict(둘 다 SPECS 키 → 문자열 값, guardrail_params.apply_params가 남기는
    change_log 모양)에서 «값이 실제로 바뀐» 키만 {key: 'brake'|'accel'}로 낸다. 파싱 실패·
    무변화 키는 결과에 안 실린다 — 호출부가 그 차이를 unchanged_or_unknown으로 센다.

    방향 판정은 guardrail_params.SPECS[key].direction이 정한다:
    'tighten_up'(커질수록 조임) → 증가=brake·감소=accel / 'tighten_down'(작아질수록 조임) →
    감소=brake·증가=accel."""
    out: dict[str, str] = {}
    for key, spec in guardrail_params.SPECS.items():
        if key not in before or key not in after:
            continue
        try:
            b, a = Decimal(str(before[key])), Decimal(str(after[key]))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if a == b:
            continue
        if spec.direction == "tighten_up":
            out[key] = "brake" if a > b else "accel"
        elif spec.direction == "tighten_down":
            out[key] = "brake" if a < b else "accel"
        # 그 외 direction 값은 미지원 — 현재 SPECS엔 없다. 판정하지 않고 넘어간다
        # (unchanged_or_unknown으로 셈 — 조용히 brake/accel로 잘못 세는 것보다 안전).
    return out


def _accel_brake_classification(db: Session) -> dict:
    """B5① 액셀/브레이크 분류. 재료는 `NaverChangeLog.action == "update_guardrail_params"`
    전건(창 없음 — 위 주석 참조).

    ★이 숫자가 존재하는 이유: 지혜가 브레이크만 N회 조이고 액셀을 0회 건드리면 그 숫자가
    그대로 표류 경보다(D-NAO-85 실측: 브레이크만 강하고 확장 압력이 0이라 ROAS +7%·매출
    −52%). 이 함수는 그 경보가 «성과 판정»이 아니라 «건수 그대로»로 뜨게 한다 — 좋다/
    나쁘다를 이 코드가 말하지 않는다.

    ★brake/accel은 **키 인스턴스** 단위다(한 change_log 행이 SPECS 키 3개를 동시에 실어
    나르는데, 그중 실제로 바뀐 키만 방향이 매겨진다) — 그래서 total_changes(행 수)와
    brake+accel+unchanged_or_unknown(키 인스턴스 수, ≈ 행수×len(SPECS))는 다른 분모다.
    두 분모가 섞이지 않게 이름을 갈랐다."""
    rows = (
        db.query(NaverChangeLog)
        .filter(NaverChangeLog.action == "update_guardrail_params")
        .order_by(NaverChangeLog.changed_at)
        .all()
    )
    by_key: dict[str, dict[str, int]] = {k: {"brake": 0, "accel": 0} for k in guardrail_params.SPECS}
    brake = accel = unchanged_or_unknown = 0
    for r in rows:
        try:
            before = json.loads(r.before_value) if r.before_value else {}
        except (TypeError, ValueError):
            before = {}
        try:
            after = json.loads(r.after_value) if r.after_value else {}
        except (TypeError, ValueError):
            after = {}
        if not isinstance(before, dict):
            before = {}
        if not isinstance(after, dict):
            after = {}
        events = _param_direction_events(before, after)
        for key in guardrail_params.SPECS:
            if key in events:
                by_key[key][events[key]] += 1
                if events[key] == "brake":
                    brake += 1
                else:
                    accel += 1
            else:
                unchanged_or_unknown += 1
    return {
        "brake": brake,
        "accel": accel,
        "unchanged_or_unknown": unchanged_or_unknown,
        "by_key": by_key,
        # 조치(change_log) «행 수» — 키 인스턴스 합(brake+accel+unchanged_or_unknown)과는
        # 다른 분모다(위 주석 참조). 0이어도 낸다.
        "total_changes": len(rows),
    }


def _actor_snapshot(rows: list[OpsDiaryEntry]) -> dict:
    """일기 행 집합의 actor 구성비 + 탐색(explore) 차단률. 0/None을 정직하게 낸다 —
    표본이 없으면 비율은 지어내지 않고 None이다."""
    total = len(rows)
    by_actor: dict[str, int] = {}
    explore_blocked = 0
    for r in rows:
        by_actor[r.actor] = by_actor.get(r.actor, 0) + 1
        if r.actor == "explore" and r.event_type == "blocked":
            explore_blocked += 1
    explore_total = by_actor.get("explore", 0)
    return {
        "total": total,
        "by_actor": by_actor,
        "explore_share": round(explore_total / total, 4) if total else None,
        "explore_total": explore_total,
        "explore_blocked": explore_blocked,
        "explore_blocked_rate": round(explore_blocked / explore_total, 4) if explore_total else None,
    }


def _exploration_symmetry(db: Session, *, now: Optional[datetime] = None) -> dict:
    """B5② 탐색 몫 + 탐색 차단률, «가장 최근 파라미터 변경» 시각을 경계로 전/후 병기.

    ★경계는 창(28일)과 무관하게 **전체 이력**에서 가장 최근 update_guardrail_params
    change_log를 찾는다(그 경계 자체가 창 밖일 수도 있다 — 그러면 창 안 «변경 전» 구간이
    자연히 0건이 된다. 그것도 정직한 값이다, 지어내지 않는다).
    ★파라미터 변경이 «한 번도» 없으면 전/후를 가를 경계가 없다 — before/after를 둘 다
    None으로 두고 그 사실을 note에 명시한다(whole_window에 창 전체 통계를 대신 낸다,
    창이 낭비되지 않도록)."""
    now = now or kst_now()
    window_lower = (now - timedelta(hours=9)) - timedelta(days=_SYMMETRY_WINDOW_DAYS)
    latest_change = (
        db.query(NaverChangeLog)
        .filter(
            NaverChangeLog.action == "update_guardrail_params",
            NaverChangeLog.changed_at.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.desc())
        .first()
    )

    def _window_rows(*, lower: datetime, upper: Optional[datetime] = None) -> list[OpsDiaryEntry]:
        q = db.query(OpsDiaryEntry).filter(
            OpsDiaryEntry.created_at.isnot(None),
            OpsDiaryEntry.created_at >= lower,
        )
        if upper is not None:
            q = q.filter(OpsDiaryEntry.created_at < upper)
        return q.all()

    if latest_change is None:
        return {
            "window_days": _SYMMETRY_WINDOW_DAYS,
            "boundary_changed_at": None,
            "before": None,
            "after": None,
            "note": "파라미터 변경 이력이 없다 — «전/후»를 가를 경계가 없다(전/후를 지어내지 않는다).",
            "whole_window": _actor_snapshot(_window_rows(lower=window_lower)),
        }
    boundary = latest_change.changed_at
    return {
        "window_days": _SYMMETRY_WINDOW_DAYS,
        "boundary_changed_at": _iso(boundary),
        "before": _actor_snapshot(_window_rows(lower=window_lower, upper=boundary)),
        "after": _actor_snapshot(_window_rows(lower=boundary)),
        "note": f"가장 최근 파라미터 변경(change_log_id={latest_change.id}) 시각을 경계로 나눴다.",
        "whole_window": None,
    }


def _symmetry_report(db: Session, *, now: Optional[datetime] = None) -> dict:
    """B5. 대칭·탐색 관측(D-NAO-247 점화 계약 원문) — ①적용 이력의 액셀/브레이크 분류
    ②탐색 몫과 탐색 차단률(파라미터 변경 전/후 병기).

    ★[판정불능 예약] — 이 보고는 **성과 판정을 하지 않는다**. 실집행이 0이라 파라미터
    변경이 행동·총이익에 낳는 효과를 관측할 사건 자체가 없다(계약 원문). 이 보고는
    «배선·관측의 증거»이지 «효과의 증거»가 아니다 — 「좋아졌다/나빠졌다」를 이 코드가
    말하면 그 순간 이 라벨이 거짓말이 된다."""
    return {
        "verdict_pending": (
            "[판정불능 예약] 실집행 0건이라 파라미터 변경의 행동·총이익 효과를 관측할 사건이 "
            "없다. 이 보고는 배선·관측의 증거이지 효과의 증거가 아니다."
        ),
        "guardrail_direction": _accel_brake_classification(db),
        "exploration": _exploration_symmetry(db, now=now),
    }


def build(db: Session, *, wisdom_id: int | None = None) -> dict:
    """지혜 성적표 산출(읽기 전용). wisdom_id를 주면 그 1건만."""
    q = db.query(OpsWisdomEntry).order_by(OpsWisdomEntry.id)
    if wisdom_id is not None:
        q = q.filter(OpsWisdomEntry.id == wisdom_id)
    entries = q.all()
    injected_ids = _briefing_injected_ids(db)
    rows = [_score_entry(db, e, injected_ids) for e in entries]

    return {
        "generated_at_kst": kst_now().isoformat(sep=" ", timespec="seconds"),
        "wisdom_total": len(rows),
        # ★적대 리뷰 P2-3: 화면 문구가 「승격 지혜 N건」인데 retired까지 세면 과대표시가 된다.
        "wisdom_active": sum(1 for r in rows if r["status"] == "active"),
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
        # ★D-NAO-228: 성적표의 재료는 «반성»이 만든다. 반성이 도는지 안 도는지가 화면에 없으면
        #   성적표가 비어 있을 때 「지혜가 없어서」인지 「반성이 죽어서」인지 구분이 안 된다.
        #   실제로 2026-07-18~08-22 결번 19일이 로그에서도 전부 'ok'로 보였다(계약 §3).
        "reflection_health": reflection_health.build_reflection_health(db),
        "wisdom": rows,
        # ★계약 §4 A2 — 승격 «전» 후보 파이프라인 관측 표면. wisdom_id 필터와 무관하게 항상
        #   전체를 낸다(후보는 특정 지혜 1건에 속하지 않는다 — 승격 전 상태라 1:1 링크가 없다).
        "candidate_status": _candidate_status(db),
        # ★B5(D-NAO-247 점화 계약) — 대칭·탐색 관측. wisdom_id 필터와 무관하게 항상 전체를
        #   낸다(개별 지혜 1건에 속하는 값이 아니다 — 봉투 파라미터·탐색 레인은 계정 전역이다).
        "symmetry_report": _symmetry_report(db),
    }
