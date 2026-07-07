# proposal_pipeline.py — proposal_pipeline Harness (P2-S3 T5)
# 역할: diagnosis(S2)·bid_simulator·proposal_writer·slack_notifier(T2~T4)를 조합해 08:00
#   체인 하나로 조립. SA간 직접 호출 금지(원칙18) — 이 Harness가 유일한 정보 유통 허브
#   (진단 보드 → bid_sims → 제안 → Slack 순으로 넘겨줌, 재조회 없이 1회 precompute한
#   aggregate를 각 SA에 전달). 각 단계 예외는 그 단계만 skip하고 단계상태를 기록한다
#   (partial-failure 격리 — 한 단계가 죽어도 나머지는 계속 진행, codex #11/#12).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity, NaverProposal
from app.services import naver_sa_ad_fetcher as fetcher
from app.services.naver_ad import (
    bid_simulator, campaign_target_resolver, diagnosis, growth_sweeper, proposal_writer, slack_notifier,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_TARGET_RANK_POSITION = 2  # 목표순위 기본값 — 진단 보드에 avg_rank가 아직 없어 고정값(P3+ 정교화 여지)
_PROPOSAL_EXPIRY_DAYS = 14  # 계획서 §0 "관찰 2~4주" 하한 채택 — pending 14일 초과 시 자동 만료


def _freshness_gate(db: Session, as_of: date) -> dict:
    """naver_ad_daily에 as_of(마지막 완결 KST일) 데이터가 있는지 확인.

    07:30 sync가 실패/부분이면 이 날짜 행이 아예 없음 — 있으면 fresh, 없으면 stale
    (생성 스킵이 정상, 원칙22 stale 제안 금지). 존재 여부만 확인(카운트는 참고용).
    """
    count = db.query(sqlfunc.count(NaverAdDaily.id)).filter(NaverAdDaily.ad_date == as_of).scalar() or 0
    return {"ok": count > 0, "as_of": as_of.isoformat(), "row_count": int(count)}


def _precompute_aggregates(
    db: Session, date_from: date, date_to: date, *, campaign_type: str | None = None,
) -> dict:
    """그룹(adgroup)/캠페인/계정 레벨 clk·conv_amt 합계를 회당 1회 집계(N+1 방지).

    bid_simulator.pooled_rpc가 소비하는 상위 aggregate. 반환:
    {"group": {adgroup_id: {"clk","conv_amt"}}, "campaign": {campaign_id: {...}}, "account": {...}}.

    campaign_type: None이면(compute_bid_sims 기존 동작) 전 캠페인유형을 계정 prior에 섞는다
    (예외 보드 후보는 항상 자기 자신의 실측 cost>0 행이라 계정 수준 수축은 미세조정일 뿐).
    growth_sweeper(WEB_SITE 전용, codex 지적)는 반드시 "WEB_SITE"로 좁혀서 호출해야 한다 —
    그렇지 않으면 클릭이 전혀 없는 WEB_SITE 신규 키워드가 SHOPPING/BRAND_SEARCH 매출로
    부풀려진 계정 prior를 그대로 물려받아 근거 없는 growth_bid_up이 나올 수 있다.
    """
    q = db.query(
        NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id,
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if campaign_type is not None:
        q = q.filter(NaverAdDaily.campaign_type == campaign_type)
    rows = q.group_by(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id).all()

    group_agg: dict[str, dict] = {}
    campaign_agg: dict[str, dict] = {}
    account_agg = {"clk": 0, "conv_amt": 0}
    for campaign_id, adgroup_id, clk, direct, indirect in rows:
        clk = int(clk)
        conv_amt = int(direct) + int(indirect)
        g = group_agg.setdefault(adgroup_id, {"clk": 0, "conv_amt": 0})
        g["clk"] += clk
        g["conv_amt"] += conv_amt
        c = campaign_agg.setdefault(campaign_id, {"clk": 0, "conv_amt": 0})
        c["clk"] += clk
        c["conv_amt"] += conv_amt
        account_agg["clk"] += clk
        account_agg["conv_amt"] += conv_amt
    return {"group": group_agg, "campaign": campaign_agg, "account": account_agg}


_RANK_ESTIMATE_BOARDS = {"bleeding_keywords", "shopping_group_bep"}  # 아래 라이브검증 주석 참조


def _collect_bid_sim_candidates(boards: dict) -> list[tuple[str, str, str, dict, str]]:
    """진단 보드에서 bid_simulator 대상 행을 (target_type, target_id, campaign_id, row, board) 튜플로 수집.

    exclusion_candidates(검색어)는 bid_simulator 대상이 아님(입찰가 없는 미등록 검색어 —
    proposal_writer가 진단 사실만으로 negative_keyword를 직접 만든다).
    """
    out: list[tuple[str, str, str, dict, str]] = []
    for row in boards.get("bleeding_keywords", []) or []:
        out.append(("keyword", row["keyword_id"], row["campaign_id"], row, "bleeding_keywords"))
    for row in boards.get("starving_winners", []) or []:
        out.append(("keyword", row["keyword_id"], row["campaign_id"], row, "starving_winners"))
    for row in boards.get("shopping_group_bep", []) or []:
        out.append(("adgroup", row["adgroup_id"], row["campaign_id"], row, "shopping_group_bep"))
    return out


def _fetch_rank_estimates(keyword_ids: list[str]) -> dict[str, int]:
    """키워드ID 목록에 대해 목표순위(_TARGET_RANK_POSITION) 필요입찰가를 조회.

    fetcher.estimate_average_position_bid가 200건 초과 시 내부적으로 자동 청크 분할
    (T1 실측) — 이 함수는 그대로 전량 넘기면 된다. 조회 실패(네트워크/자격증명 등)는
    상위(compute_bid_sims)가 잡아 rank_bid 없이 진행하도록 예외를 그대로 전파한다.
    """
    if not keyword_ids:
        return {}
    items = [{"key": kw_id, "position": _TARGET_RANK_POSITION} for kw_id in keyword_ids]
    estimates = fetcher.estimate_average_position_bid("MOBILE", items)
    return {e["nccKeywordId"]: e["bid"] for e in estimates if e.get("nccKeywordId")}


def _make_target_roas_resolver(db: Session, diag: dict):
    """campaign_id → Decimal target_roas 리졸버(override > 계정기본값, D-S3-b) — 회차 내 캐싱.

    compute_bid_sims/compute_growth_sims 공용(codex 지적 이력 있음, ref: 라이브검증
    2026-07-07 — 계정 단일 target_roas만 쓰면 override가 rationale에만 보이고 실제 계산에
    반영되지 않는 버그가 났었다. 이 리졸버를 양쪽이 공유해 동일 버그가 growth_sweeper에서
    재발하지 않도록 한다).
    """
    cache: dict[str, Decimal] = {}

    def _resolve(campaign_id: str) -> Decimal:
        if campaign_id not in cache:
            resolved = campaign_target_resolver.resolve_target_roas(db, campaign_id)
            cache[campaign_id] = (
                resolved["target_roas"] if resolved["target_roas"] is not None
                else Decimal(str(diag["account_target_roas"]))
            )
        return cache[campaign_id]

    return _resolve


def _fill_predicted_clicks(
    db: Session, sims: dict[tuple[str, str], dict], call_inputs: dict[tuple[str, str], dict],
    agg: dict, correction_factor: Decimal,
) -> None:
    """확정된 recommended_bid로 performance-bulk를 조회해 예측클릭을 채워 sims를 in-place 갱신.

    compute_bid_sims/compute_growth_sims 공통 2차 패스(codex 지적 이력 — 이 경로가 빠지면
    expected_effect가 항상 "미조회"로 남는다, 두 호출자가 각자 재구현하면 같은 회귀가
    growth_sweeper에서 반복될 수 있어 단일 구현으로 통합). performance-bulk는 키워드 텍스트
    전용(T1 실측)이라 nccKeywordId→텍스트 매핑이 필요하다.
    """
    perf_targets = [
        (ttype, tid) for (ttype, tid), sim in sims.items()
        if ttype == "keyword" and sim["recommended_bid"] > 0
    ]
    if not perf_targets:
        return
    names = dict(
        db.query(NaverEntity.entity_id, NaverEntity.name)
        .filter(NaverEntity.entity_id.in_([tid for _, tid in perf_targets])).all()
    )
    perf_items = [
        {"keyword": names[tid], "bid": sims[(ttype, tid)]["recommended_bid"],
         "keywordplus": False, "device": "MOBILE"}
        for ttype, tid in perf_targets if names.get(tid)
    ]
    if not perf_items:
        return
    try:
        perf_results = fetcher.estimate_performance(perf_items)
    except Exception as e:  # noqa: BLE001 — 성과 estimate 실패도 저하만, 파이프라인 중단 아님
        log.warning("proposal_pipeline: performance-bulk 조회 실패(예측클릭 없이 진행): %s", e)
        return
    # (keyword 텍스트, bid) 조합으로 매칭 — 요청 자체가 이 조합으로 유일하게 식별됨.
    clicks_by_key = {(r["keyword"], r["bid"]): r.get("clicks") for r in perf_results}

    for ttype, tid in perf_targets:
        name = names.get(tid)
        if name is None:
            continue
        recommended_bid = sims[(ttype, tid)]["recommended_bid"]
        predicted_clicks = clicks_by_key.get((name, recommended_bid))
        if predicted_clicks is None:
            continue
        inputs = call_inputs[(ttype, tid)]
        estimate = dict(inputs["estimate"] or {})
        estimate["predicted_clicks"] = predicted_clicks
        estimate.setdefault("device", "MOBILE")
        sims[(ttype, tid)] = bid_simulator.simulate_bid(
            inputs["keyword_row"], inputs["target_roas"],
            group_agg=inputs["group_agg"], campaign_agg=inputs["campaign_agg"],
            account_agg=agg["account"], correction_factor=correction_factor, estimate=estimate,
            is_new_or_growth=inputs.get("is_new_or_growth", False),
        )


def compute_bid_sims(db: Session, diag: dict, date_from: date, as_of: date, *, agg: dict | None = None) -> dict:
    """진단 보드 후보 전원에 대해 bid_simulator.simulate_bid를 실행해 묶음으로 반환.

    반환: {(target_type, target_id): simulate_bid() 결과, ...} — proposal_writer.build의
    bid_sims 인자에 그대로 전달. rank estimate 조회가 실패해도 economic_ceiling만으로
    계속 진행(부분 저하, 예외로 전체를 막지 않음).

    target_roas는 캠페인별로 해석한다(campaign_target_resolver.resolve_target_roas —
    override > 계정 기본값, D-S3-b). 라이브검증(2026-07-07, 원칙22)에서 발견: 계정 단일
    target_roas만 쓰면 `/campaign-settings`의 target_roas_override가 rationale 라벨에만
    보이고 실제 입찰 계산에는 반영되지 않아 override 기능이 사실상 죽어있었다(codex 지적).

    agg: run_daily이 growth_sweeper와 공유하려고 미리 계산해 넘길 수 있음(N+1 방지) — 없으면
    (단독 호출·기존 테스트 호환) 내부에서 계산.
    """
    boards = diag["boards"]
    candidates = _collect_bid_sim_candidates(boards)
    if not candidates:
        return {}

    correction_factor = Decimal(str(diag["correction_factor"]["factor"]))
    agg = agg if agg is not None else _precompute_aggregates(db, date_from, as_of)
    _target_roas_for = _make_target_roas_resolver(db, diag)

    target_ids = [tid for _, tid, _, _, _ in candidates]
    bid_amts = dict(
        db.query(NaverEntity.entity_id, NaverEntity.bid_amt)
        .filter(NaverEntity.entity_id.in_(target_ids)).all()
    )

    # ⚠️ 라이브검증(2026-07-07, 원칙22)에서 발견: starving_winners(성과 좋은데 저노출 —
    # 육성 의도, D-NAO-18)에 고정 목표순위(position=2) rank_bid를 적용하면, 이미 그
    # 순위보다 잘 나오는 고성과 키워드는 "지금보다 낮은 입찰로도 순위2는 유지된다"는
    # 계산이 나와 bid_down을 추천해버림(성장 의도와 정반대). starving_winners는 rank
    # estimate를 아예 조회하지 않고 economic_ceiling(수익 여력 기반 상한)만으로 판단—
    # 고ROAS 키워드는 ceiling이 현재 입찰보다 높아 자연히 bid_up으로 이어진다.
    keyword_ids = [
        tid for ttype, tid, _, _, board in candidates
        if ttype == "keyword" and board in _RANK_ESTIMATE_BOARDS
    ]
    try:
        rank_by_kw = _fetch_rank_estimates(keyword_ids)
    except Exception as e:  # noqa: BLE001 — estimate 실패는 저하만, 파이프라인 중단 아님
        log.warning("proposal_pipeline: average-position-bid 조회 실패(rank_bid 없이 진행): %s", e)
        rank_by_kw = {}

    # 1차 패스 — recommended_bid까지만 확정(성과 estimate는 아직 없음, rank_bid만 반영).
    call_inputs: dict[tuple[str, str], dict] = {}
    bid_sims: dict[tuple[str, str], dict] = {}
    for target_type, target_id, campaign_id, row, board in candidates:
        keyword_row = {
            "clk": row.get("clk", 0), "conv_amt": row.get("conv_amt", 0),
            "bid_amt": bid_amts.get(target_id),
        }
        campaign_agg = agg["campaign"].get(campaign_id, {"clk": 0, "conv_amt": 0})
        if target_type == "adgroup":
            # SHOPPING 그룹은 그룹 하위에 별도 키워드 grain이 없음 — group 레벨=campaign 레벨로
            # 근사(계층 한 단계를 생략하는 단순화, 완전한 3단 분리는 추후 정교화 여지).
            group_agg = campaign_agg
        else:
            group_agg = agg["group"].get(row.get("adgroup_id"), {"clk": 0, "conv_amt": 0})

        estimate = None
        if board in _RANK_ESTIMATE_BOARDS and target_id in rank_by_kw:
            estimate = {
                "rank_bid": rank_by_kw[target_id],
                "rank_position": _TARGET_RANK_POSITION,
                "device": "MOBILE",
            }

        target_roas = _target_roas_for(campaign_id)
        call_inputs[(target_type, target_id)] = {
            "keyword_row": keyword_row, "group_agg": group_agg, "campaign_agg": campaign_agg,
            "target_roas": target_roas, "estimate": estimate,
        }
        bid_sims[(target_type, target_id)] = bid_simulator.simulate_bid(
            keyword_row, target_roas,
            group_agg=group_agg, campaign_agg=campaign_agg, account_agg=agg["account"],
            correction_factor=correction_factor, estimate=estimate,
        )

    # 2차 패스 — 확정된 recommended_bid로 성과(예측클릭) estimate를 조회해 expected_effect를
    # 채운다(codex 지적: 이전엔 이 경로가 전혀 연결 안 돼 expected_effect가 항상 "미조회").
    _fill_predicted_clicks(db, bid_sims, call_inputs, agg, correction_factor)

    return bid_sims


def compute_growth_sims(
    db: Session, diag: dict, date_from: date, as_of: date, *, agg: dict | None = None,
) -> dict:
    """growth_sweeper 후보 상위 ESTIMATE_BUDGET개에 대해 bid_simulator.simulate_bid를 실행.

    전 활성 키워드(~89K)를 로컬 스윕(growth_sweeper.find_growth_candidates, API 無)한 뒤,
    갭 상위 ESTIMATE_BUDGET개만 estimate(외부 API)로 실측(계획서 §4-Phase2 — 전수 estimate
    금지). 반환: {"candidates": [상위 후보...], "sims": {("keyword", target_id): simulate_bid()
    결과}} — proposal_writer.build의 growth_candidates/growth_sims 인자에 그대로 전달.

    agg: 반드시 WEB_SITE로 스코프된 집계여야 한다(codex 지적 — compute_bid_sims용 전체 캠페인
    유형 집계를 그대로 재사용하면 SHOPPING/BRAND_SEARCH 매출이 계정 prior에 섞여, 클릭이 전혀
    없는 WEB_SITE 신규 키워드가 근거 없는 성장 제안을 받을 수 있다). run_daily이 별도로 계산해
    넘김(N+1 방지) — 없으면(단독 호출·테스트) 내부에서 WEB_SITE 스코프로 재계산.
    """
    correction_factor = Decimal(str(diag["correction_factor"]["factor"]))
    agg = agg if agg is not None else _precompute_aggregates(db, date_from, as_of, campaign_type=growth_sweeper.WEB_SITE)
    target_roas_for = _make_target_roas_resolver(db, diag)

    all_candidates = growth_sweeper.find_growth_candidates(
        db, date_from, as_of, correction_factor=correction_factor, agg=agg, target_roas_for=target_roas_for,
    )
    top = all_candidates[:growth_sweeper.ESTIMATE_BUDGET]
    if not top:
        return {"candidates": [], "sims": {}}

    keyword_ids = [c["keyword_id"] for c in top]
    try:
        rank_by_kw = _fetch_rank_estimates(keyword_ids)
    except Exception as e:  # noqa: BLE001 — estimate 실패는 저하만, 파이프라인 중단 아님
        log.warning("proposal_pipeline: growth_sweeper average-position-bid 조회 실패(rank_bid 없이 진행): %s", e)
        rank_by_kw = {}

    # 1차 패스 — recommended_bid까지만 확정(rank_bid만 반영, 성과 estimate는 아직 없음).
    call_inputs: dict[tuple[str, str], dict] = {}
    sims: dict[tuple[str, str], dict] = {}
    for c in top:
        keyword_row = {"clk": c["clk"], "conv_amt": c["conv_amt"], "bid_amt": c["current_bid"]}
        campaign_agg = agg["campaign"].get(c["campaign_id"], {"clk": 0, "conv_amt": 0})
        group_agg = agg["group"].get(c["adgroup_id"], {"clk": 0, "conv_amt": 0})
        target_roas = target_roas_for(c["campaign_id"])

        estimate = None
        if c["keyword_id"] in rank_by_kw:
            estimate = {
                "rank_bid": rank_by_kw[c["keyword_id"]], "rank_position": _TARGET_RANK_POSITION, "device": "MOBILE",
            }

        key = ("keyword", c["keyword_id"])
        call_inputs[key] = {
            "keyword_row": keyword_row, "group_agg": group_agg, "campaign_agg": campaign_agg,
            "target_roas": target_roas, "estimate": estimate, "is_new_or_growth": True,
        }
        sims[key] = bid_simulator.simulate_bid(
            keyword_row, target_roas, group_agg=group_agg, campaign_agg=campaign_agg,
            account_agg=agg["account"], correction_factor=correction_factor, estimate=estimate,
            is_new_or_growth=True,
        )

    # 2차 패스 — compute_bid_sims와 동일 공통 헬퍼(codex 회귀 이력 있는 경로, 단일 구현 재사용).
    _fill_predicted_clicks(db, sims, call_inputs, agg, correction_factor)

    return {"candidates": top, "sims": sims}


def _expire_stale_pending(db: Session) -> int:
    """pending 상태로 _PROPOSAL_EXPIRY_DAYS일 초과한 제안을 expired로 전환."""
    cutoff = datetime.combine(kst_today() - timedelta(days=_PROPOSAL_EXPIRY_DAYS), datetime.min.time())
    stale = db.query(NaverProposal).filter(
        NaverProposal.status == "pending", NaverProposal.created_at < cutoff,
    ).all()
    for p in stale:
        p.status = "expired"
    return len(stale)


def run_daily(db: Session, *, lookback_days: int = 15) -> dict:
    """08:00 엔트리 — freshness 게이트 → diagnosis → bid_simulator → proposal_writer →
    slack → 계정 브리프 싱글톤 → 만료 패스. 각 단계는 독립 try/except로 격리(부분 실패시
    나머지 단계는 계속 진행) — 반환 stage_status로 관측 가능(ok/degraded/failed/skipped).
    """
    as_of = kst_today() - timedelta(days=1)  # 마지막 완결 KST일(어제)
    date_from = as_of - timedelta(days=lookback_days - 1)

    result: dict = {"generated": 0, "skipped": 0, "expired": 0, "stage_status": {}, "errors": []}

    fresh = _freshness_gate(db, as_of)
    if not fresh["ok"]:
        result["stage_status"]["freshness"] = "stale"
        result["skipped"] = 1
        result["errors"].append(
            f"freshness 게이트: as_of={fresh['as_of']} naver_ad_daily 데이터 없음(07:30 sync 실패/부분 추정) — 생성 스킵"
        )
        return result
    result["stage_status"]["freshness"] = "ok"

    diag: dict | None = None
    try:
        diag = diagnosis.build_diagnosis(db, date_from, as_of)
        result["stage_status"]["diagnosis"] = "ok" if diag.get("boards") is not None else "degraded"
        if diag.get("boards") is None:
            result["errors"].append(f"diagnosis: {diag.get('error')}")
    except Exception as e:  # noqa: BLE001
        log.exception("proposal_pipeline diagnosis 단계 실패: %s", e)
        result["stage_status"]["diagnosis"] = "failed"
        result["errors"].append(f"diagnosis: {e}")
        diag = None

    agg: dict | None = None
    web_site_agg: dict | None = None
    if diag and diag.get("boards") is not None:
        agg = _precompute_aggregates(db, date_from, as_of)  # bid_simulator(전 캠페인유형, 기존 동작)
        # growth_sweeper는 WEB_SITE 전용(codex 지적) — 위 agg를 그대로 쓰면 SHOPPING/BRAND_SEARCH
        # 매출이 계정 prior에 섞여 클릭 0인 WEB_SITE 신규 키워드가 근거 없는 제안을 받을 수 있다.
        web_site_agg = _precompute_aggregates(db, date_from, as_of, campaign_type=growth_sweeper.WEB_SITE)

    bid_sims: dict = {}
    if diag and diag.get("boards") is not None:
        try:
            bid_sims = compute_bid_sims(db, diag, date_from, as_of, agg=agg)
            result["stage_status"]["bid_simulator"] = "ok"
        except Exception as e:  # noqa: BLE001
            log.exception("proposal_pipeline bid_simulator 단계 실패: %s", e)
            result["stage_status"]["bid_simulator"] = "failed"
            result["errors"].append(f"bid_simulator: {e}")
    else:
        result["stage_status"]["bid_simulator"] = "skipped"

    growth: dict = {"candidates": [], "sims": {}}
    if diag and diag.get("boards") is not None:
        try:
            growth = compute_growth_sims(db, diag, date_from, as_of, agg=web_site_agg)
            result["stage_status"]["growth_sweeper"] = "ok"
        except Exception as e:  # noqa: BLE001 — 성장 스윕 실패는 저하만, 예외 보드 제안은 계속 진행
            log.exception("proposal_pipeline growth_sweeper 단계 실패: %s", e)
            result["stage_status"]["growth_sweeper"] = "failed"
            result["errors"].append(f"growth_sweeper: {e}")
    else:
        result["stage_status"]["growth_sweeper"] = "skipped"

    proposal_summaries: list[dict] = []
    if diag and diag.get("boards") is not None:
        try:
            candidates = proposal_writer.build(
                db, diag, bid_sims=bid_sims,
                growth_candidates=growth["candidates"], growth_sims=growth["sims"], as_of=as_of,
            )
            saved = proposal_writer.persist(db, candidates)
            proposal_writer.account_brief_singleton(db, diag, as_of)
            db.commit()
            result["generated"] = len(saved)
            proposal_summaries = [{"proposal_type": p.proposal_type, "target_id": p.target_id} for p in saved]
            result["stage_status"]["proposal_writer"] = "ok"
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("proposal_pipeline proposal_writer 단계 실패: %s", e)
            result["stage_status"]["proposal_writer"] = "failed"
            result["errors"].append(f"proposal_writer: {e}")
    else:
        result["stage_status"]["proposal_writer"] = "skipped"

    try:
        notify_result = slack_notifier.notify(proposal_summaries)
        # no_webhook/no_proposals는 정상 no-op(D-NAO-21) — 그 외 reason(5xx/네트워크 등
        # 실제 발송 실패)은 stage_status에 반영해야 한다(codex 지적 — 이전엔 예외만 잡고
        # notify()의 반환값 자체를 무시해 실패가 "ok"로 보고됐다).
        if notify_result["sent"] or notify_result["reason"] in ("no_webhook", "no_proposals"):
            result["stage_status"]["slack"] = "ok"
        else:
            result["stage_status"]["slack"] = "failed"
            result["errors"].append(f"slack: {notify_result['reason']}")
    except Exception as e:  # noqa: BLE001
        log.exception("proposal_pipeline slack 단계 실패: %s", e)
        result["stage_status"]["slack"] = "failed"
        result["errors"].append(f"slack: {e}")

    try:
        result["expired"] = _expire_stale_pending(db)
        db.commit()
        result["stage_status"]["expiry"] = "ok"
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.exception("proposal_pipeline expiry 단계 실패: %s", e)
        result["stage_status"]["expiry"] = "failed"
        result["errors"].append(f"expiry: {e}")

    return result
