# probe_learning_loop.py — probe_learning_harness (D-NAO-58 CD4, SA간 정보 유통 허브)
"""환경 셀×순위 밴드 학습(aggregate)→세분화 판정(segment)→최적순위 승격(promote)→운영 일기
요약 기입(diary)을 하루 1회 체인으로 조립(wisdom_loop 스테이지 격리 패턴 — 한 단계 실패가
나머지를 막지 않는다). SA간 직접 호출 금지(원칙18) — 이 harness가 유일한 정보 유통 허브.

마이그레이션 없음 — 상태는 매 실행 재계산(probe_cell_aggregate/segmenter는 순수 조회). 쓰기는
observe 일기 1행뿐(diary.write_diary_entry, 하루 1회 idempotent — diary_reflection 전례).
learned_probe_rank는 D-NAO-60 RL5(CD5)가 소비하는 조회 SA — auto_operator._learned_optimal_skip이
탐침 발동 직전 이 함수로 학습된 최적 밴드를 조회해 과climb을 막는다(승격 판정 로직 자체는
불변, 소비만 배선됨)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import OpsDiaryEntry
from app.services.naver_ad.diary import ACTOR_SYSTEM, write_diary_entry
from app.services.naver_ad.diary_outcome import _kst_date
from app.services.naver_ad.probe_cell_aggregate import (
    _MIN_CELL_DAYS,
    _MIN_CELL_IMP,
    _WINDOW_DAYS,
    aggregate_cells,
)
from app.services.naver_ad.probe_cell_segmenter import judge_cell_segmentation
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

_DIARY_ACTION = "probe_learning"


def _is_promotable(view: dict) -> bool:
    """승격 조건 단일 소스: days≥_MIN_CELL_DAYS·imp≥_MIN_CELL_IMP·optimal_band 확정(not None).
    learned_probe_rank(단일셀 조회)·_promote_cells(집계 스냅샷 순회) 둘 다 이걸 쓴다."""
    return (
        view["days"] >= _MIN_CELL_DAYS
        and view["imp"] >= _MIN_CELL_IMP
        and view["optimal_band"] is not None
    )


def learned_probe_rank(
    db: Session, *, env_cell: str, as_of: date | None = None,
    window_days: int = _WINDOW_DAYS, campaign_id: str | None = None,
) -> str | None:
    """그 환경 셀이 승격 조건을 충족하면 optimal_band, 아니면 None. CD5가 탐침 트리거 판정에
    소비할 단일셀 조회 API(재집계 1회 — run_probe_learning의 일괄 승격과 달리 외부 단건용)."""
    agg = aggregate_cells(db, as_of=as_of, window_days=window_days, campaign_id=campaign_id)
    cell = agg["cells"].get(env_cell)
    if cell is None or not _is_promotable(cell):
        return None
    return cell["optimal_band"]


def _run_stage(result: dict, key: str, fn, default):
    """단계 실행 + 격리 — 실패해도 예외를 밖으로 던지지 않고 stage_status에만 기록
    (wisdom_loop._stage 전례). 실패 시 result[key]=default(호출부가 안전 폴백으로 쓸 수 있게)."""
    try:
        result[key] = fn()
        result["stage_status"][key] = "ok"
    except Exception as e:  # noqa: BLE001 — 한 단계 실패가 나머지를 막지 않음
        log.exception("probe_learning_loop: %s 단계 실패: %s", key, e)
        result["stage_status"][key] = "failed"
        result[key] = default


def _promote_cells(aggregate: dict) -> list[dict]:
    """이미 계산된 aggregate 스냅샷에서 승격 조건 충족 셀만 {cell, band}로(재집계 없음 —
    P3-1 리뷰: 셀당 aggregate_cells 재실행 N+1 제거 + 보고 cells와 동일 스냅샷 근거 보장).
    ★반환 shape은 하위호환 고정({"cell","band"} 2키만 — 소급 채점/기존 테스트가 이 dict를
    그대로 in 비교한다). basis는 별도 `_promoted_basis_map`으로 분리."""
    return [
        {"cell": cell, "band": view["optimal_band"]}
        for cell, view in aggregate.get("cells", {}).items()
        if _is_promotable(view)
    ]


def _promoted_basis_map(aggregate: dict) -> dict[str, str]:
    """D-NAO-60 RL5 Part B: 승격 셀별 optimal_basis("conv"|"ctr") — _promote_cells와 동일
    판정조건(_is_promotable) 재사용, `_summary_rationale`이 basis별 문구를 고르는 데 쓴다.
    `_promote_cells`의 {"cell","band"} shape을 건드리지 않으려 별도 함수/스테이지로 분리."""
    return {
        cell: view.get("optimal_basis", "ctr")
        for cell, view in aggregate.get("cells", {}).items()
        if _is_promotable(view)
    }


def _already_run_today(db: Session, today: date) -> bool:
    """같은 날 observe·probe_learning 행이 이미 있으면 재생성 안 함(catch-up 이중 방지,
    diary_reflection._already_reflected_today 패턴 복제)."""
    row = (
        db.query(OpsDiaryEntry)
        .filter(OpsDiaryEntry.event_type == "observe", OpsDiaryEntry.action == _DIARY_ACTION)
        .order_by(OpsDiaryEntry.created_at.desc())
        .first()
    )
    return row is not None and row.created_at is not None and _kst_date(row.created_at) == today


def _summary_rationale(result: dict) -> str:
    """observe 일기 rationale(볼트 열람층이 렌더하는 유일 필드 — vault_export._render_diary_day는
    observe를 e.rationale만 출력). D-58-12: 세분화 판정 근거·승격 결과를 여기 남긴다(P2-1 리뷰).

    D-NAO-60 RL5 Part B(P3-3 해소): 승격 표기가 더 이상 '클릭 최다·이익가중 미반영' 고정
    문구가 아니다 — `promoted_basis`(cell→"conv"|"ctr")를 보고 전환 신호가 있던 승격은
    '전환 최다(이익 방향)', 전환 신호 없어 CTR로 폴백한 승격은 '클릭 최다(전환 신호
    부족·CTR 폴백)'로 정직하게 구분 표기한다."""
    n_cells = result.get("cells", 0)
    promoted = result.get("promoted") or []
    basis_map = result.get("promoted_basis") or {}
    judged = (result.get("segment") or {}).get("judged") or []
    splits = [j for j in judged if j.get("verdict") == "split"]
    lines = [
        f"CD4 학습: {n_cells}셀 집계, {len(promoted)}셀 최적순위 승격, "
        f"{len(judged)}셀 세분판정({len(splits)}건 split).",
    ]
    if promoted:
        top = "; ".join(f"{p['cell']}→{p['band']}" for p in promoted[:5])
        bases = {basis_map.get(p["cell"], "ctr") for p in promoted}
        if bases == {"conv"}:
            label = "전환 최다(이익 방향)"
        elif "conv" in bases:
            label = "전환 최다(이익 방향) 일부·나머지 클릭 최다(전환 신호 부족·CTR 폴백)"
        else:
            label = "클릭 최다(전환 신호 부족·CTR 폴백)"
        lines.append(f"- 승격({label}·CD5): {top}")
    for j in splits[:5]:
        lines.append(
            f"- 세분 권고 [{j.get('cell')}] 축={j.get('axis')}: "
            f"{j.get('rationale') or '(근거 없음)'}"
        )
    return "\n".join(lines)


def _write_summary_diary(db: Session, now: datetime, result: dict) -> None:
    """observe 일기 1행(하루 1회 idempotent). 근거는 rationale(볼트 렌더 대상)에, 기계판독용
    상세(승격·세분 판정 전량)는 after_value에 JSON으로. write_diary_entry 자체도 fail-open —
    이 함수 호출부(run_probe_learning)가 추가로 stage 격리한다."""
    if _already_run_today(db, now.date()):
        return
    detail = json.dumps(
        {
            "promoted": result.get("promoted") or [],
            "promoted_basis": result.get("promoted_basis") or {},  # D-NAO-60 RL5 Part B
            "segment": (result.get("segment") or {}).get("judged") or [],
        },
        ensure_ascii=False, default=str,
    )
    write_diary_entry(
        db, "observe", "", actor=ACTOR_SYSTEM, action=_DIARY_ACTION,
        rationale=_summary_rationale(result), after_value=detail, now=now,
    )


def run_probe_learning(
    db: Session, *, now: datetime | None = None, campaign_id: str | None = None,
) -> dict:
    """09:05 엔트리 — 집계→세분판정→승격→일기 4단계 스테이지 격리(한 단계 실패가 나머지를
    막지 않음). 반환 {"stage_status":{...}, "cells":int, "promoted":[...], "promoted_basis":
    {cell:"conv"|"ctr",...}(RL5 Part B), "segment":{...}}."""
    now = now or kst_now()
    as_of = now.date()
    result: dict = {"stage_status": {}}

    _run_stage(result, "aggregate", lambda: aggregate_cells(db, as_of=as_of, campaign_id=campaign_id),
              {"cells": {}})
    aggregate = result.pop("aggregate")
    result["cells"] = len(aggregate.get("cells", {}))

    _run_stage(result, "segment",
              lambda: judge_cell_segmentation(db, as_of=as_of, campaign_id=campaign_id), {})

    _run_stage(result, "promoted", lambda: _promote_cells(aggregate), [])
    _run_stage(result, "promoted_basis", lambda: _promoted_basis_map(aggregate), {})

    try:
        _write_summary_diary(db, now, result)
        result["stage_status"]["diary"] = "ok"
    except Exception as e:  # noqa: BLE001 — fail-open(일기 실패가 학습 결과를 무효화 안 함)
        log.exception("probe_learning_loop: diary 단계 실패: %s", e)
        result["stage_status"]["diary"] = "failed"

    return result
