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
from app.services.naver_ad import probe_cell_aggregate
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


def gate_bands(db: Session, *, as_of: date, campaign_ids: list[str]) -> dict[str, str | None]:
    """★게이트가 **실제로 읽는** 값 — 캠페인별 승격 밴드(없으면 None).

    이 잡(`run_probe_learning`)은 스케줄러가 `campaign_id` 없이 부르므로 **계정 전체** 집계를
    승격시켜 일기에 남긴다. 그런데 실제 소비자(`auto_operator._learned_band_of` →
    `_probe_rank_floor`·`_learned_optimal_skip`)는 **캠페인별**로 재계산한 값을 쓴다.
    두 값은 다를 수 있고, 실제로 달랐다 — 2026-07-29 실측에서 계정 전체는 `1.0-2.0`이
    승격돼 있었으나 당시 `optimizer='ours'` 6개 캠페인 중 그 밴드를 소비하는 곳은 **0개**였다
    (campaign_ids 스코프는 2026-07-30 사고 이후 `_observed_campaign_ids`로 교체됐다).
    일기에는 학습이 된 것처럼 보이는데 아무도 그 값을 안 읽는 상태 — 이 프로젝트가 추적
    중인 표방↔실구현 괴리 그대로다.

    그래서 일기에 **둘을 나란히** 남긴다. 계정 승격은 '참고', 이 함수 결과가 '실제 적용값'이다.
    """
    if not campaign_ids:
        # ★"대상 0" ≠ "대상은 있는데 학습밴드가 없다"(후자는 아래 루프가 None으로 표기).
        log.warning(
            "probe_learning_loop: gate_band 산출 대상 캠페인이 0 — 관측 스코프 결함 의심"
            "(스코프 정의는 campaign_roster.observation_campaign_ids)",
        )
    out: dict[str, str | None] = {}
    for cid in campaign_ids:
        try:
            env_cell = probe_cell_aggregate.env_cell_of_date(as_of)
            out[cid] = learned_probe_rank(db, env_cell=env_cell, as_of=as_of, campaign_id=cid)
        except Exception:  # noqa: BLE001 — 한 캠페인 실패가 나머지를 막지 않음(관측 목적)
            log.exception("probe_learning_loop: gate_band 산출 실패 campaign=%s", cid)
            out[cid] = None
    return out


def _observed_campaign_ids(db: Session, *, as_of: date | None = None) -> list[str]:
    """`gate_bands`를 산출할 캠페인 목록 = **관측 스코프**.

    ★스코프 교체(2026-07-30 사고): 구 코드는 `optimizer='ours'`만 봤고, D-NAO-132
    긴급정지로 그 집합이 비자 "게이트가 실제로 읽는 값"의 캠페인별 재계산이 통째로 멈췄다
    (일기에는 계정 승격값만 남아, 이 함수가 애초에 드러내려던 표방↔실구현 괴리가 다시
    가려졌다). 이 산출은 DB 읽기뿐이라 네이버 API 콜이 0이고 실행에도 전혀 관여하지 않는다
    — 근거·선례는 campaign_roster.observation_campaign_ids docstring(D-NAO-13 원문).
    """
    from app.services.naver_ad import campaign_roster

    return sorted(campaign_roster.observation_campaign_ids(db, today=as_of))


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
        scope_label = "계정 전체" if result.get("scope") in (None, "account") else f"캠페인 {result['scope']}"
        lines.append(f"- 승격({label}·CD5, **{scope_label} 기준 — 참고값**): {top}")
    gate = result.get("gate_bands") or {}
    if gate:
        applied = {c: b for c, b in gate.items() if b}
        if applied:
            lines.append(
                "- ★실제 적용값(캠페인별, 게이트가 읽는 값): "
                + "; ".join(f"{c[-12:]}→{b}" for c, b in list(applied.items())[:6])
            )
        if len(applied) < len(gate):
            lines.append(
                f"- ★학습밴드 없는 캠페인 {len(gate) - len(applied)}/{len(gate)}개 — "
                "그 캠페인은 하드코딩 프라이어(2.5)로 동작(계정 승격값을 쓰지 않는다)"
            )
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
            "scope": result.get("scope"),                # 위 promoted가 어느 스코프의 값인지
            "gate_bands": result.get("gate_bands") or {},  # 게이트가 실제로 읽는 캠페인별 값
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

    # ★위 promoted는 이 호출의 스코프(스케줄러는 campaign_id 없이 부르므로 = 계정 전체)다.
    # 게이트가 실제로 읽는 캠페인별 값을 따로 산출해 일기·로그에 나란히 남긴다(gate_bands docstring).
    result["scope"] = campaign_id or "account"
    _run_stage(result, "gate_bands",
               lambda: gate_bands(db, as_of=as_of,
                                  campaign_ids=_observed_campaign_ids(db, as_of=as_of)), {})

    try:
        _write_summary_diary(db, now, result)
        result["stage_status"]["diary"] = "ok"
    except Exception as e:  # noqa: BLE001 — fail-open(일기 실패가 학습 결과를 무효화 안 함)
        log.exception("probe_learning_loop: diary 단계 실패: %s", e)
        result["stage_status"]["diary"] = "failed"

    return result
