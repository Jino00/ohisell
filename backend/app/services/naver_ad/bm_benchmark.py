# bm_benchmark.py — SA-3 성과 대조기 (BM 벤치마크 레이어, D-NAO-78 · Phase 4)
# 역할: naver_entity_snapshot(오늘 구조) + naver_ad_daily(계정 전체 14일 성과) + naver_entity
#   (대행사 등록 키워드)를 대조해, 고성과 구조를 벤치마크化한 프라이어(naver_bm_benchmark)를
#   매일 재산출한다(교체 upsert). 산출 bench_kind: keyword_verified(대행사 등록 키워드셋)·
#   bid_band(고성과 그룹 입찰밴드 [min,p50,max])·group_structure(고성과 그룹 구조 요약).
#   B-X(초기입찰)·SS4(승격 교차)가 optional 프라이어로 소비 — 전부 fail-open(부재=기존 동일).
#   ★관찰 전용(§0 금지선 1): DB만 읽고 산출. 네이버 API·실행 손(naver_execution_harness/
#   naver_sa_writer)은 import조차 하지 않는다(원칙18-1 단일 책임).
#   ★bid_rank_slope(IU-R 서보 프라이어)는 이번 스코프 제외: 산출 원료인 대행사 입찰 변경 이벤트
#   (naver_agency_op)가 오늘 개시라 아직 0건. 향후 slope는 naver_learning_state(scope=entity/
#   metric=bid_rank_slope)에 써서 rank_servo가 기존 bid_rank_curve.load_response_priors로 무개조
#   소비한다(§2-b 혼용 — 배선 재사용, 이 테이블 아님). 그래서 IU-R 주입 지점은 이미 존재한다.
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverBmBenchmark,
    NaverCampaignSettings,
    NaverEntity,
    NaverEntitySnapshot,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

WINDOW_DAYS = 14           # 성과 대조 lookback(일). [sdate-14, sdate-1] 완결일만(오늘 부분치 제외).
_MIN_HIGH_PERF_GROUPS = 3  # campaign_type별 밴드/구조 산출 최소 고성과 그룹 수(미만이면 그 유형 스킵).
_CONFIDENCE_FULL_N = 10    # 신뢰도 1.0 도달 표본 수(confidence = min(1, sample_n/이 값)).
_HIGH_PERF_KINDS = ("bid_band", "group_structure")  # 재산출 시 교체 대상(keyword_verified와 별도)


def _agency_optimizer_map(db: Session) -> dict[str, str]:
    """campaign_id → optimizer(none/ours/mop). settings 없는 캠페인=none(대행사 관찰 대상).

    bm_snapshot._optimizer_map과 동일 규약(private 재사용 대신 값만 복제 — 모듈 결합 최소)."""
    return {
        s.campaign_id: (s.optimizer or "none")
        for s in db.query(NaverCampaignSettings).all()
    }


def _group_performance(db: Session, window_from: date, window_to: date) -> dict[str, dict]:
    """adgroup_id → {cost, conv_amt} 14일 합(계정 전체). naver_ad_daily 리프 행(키워드/그룹
    grain)을 adgroup 단위로 합산 — UniqueConstraint가 (ad_date,campaign,adgroup,keyword_id)라
    keyword_id가 다른 행은 서로 다른 리프이므로 합산=그룹 총합(이중계상 아님, exploration.
    _grain_settlement_agg 관례). ★__backfill__ sentinel 행 제외(2배 함정 회피)."""
    rows = (
        db.query(
            NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= window_from,
            NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.adgroup_id != "",
        )
        .group_by(NaverAdDaily.adgroup_id)
        .all()
    )
    return {
        aid: {"cost": int(cost), "conv_amt": int(direct) + int(indirect)}
        for aid, cost, direct, indirect in rows
    }


def _high_performers_by_type(
    groups: list[NaverEntitySnapshot], perf: dict[str, dict],
) -> dict[str, list[NaverEntitySnapshot]]:
    """campaign_type → 고성과 그룹 스냅샷 리스트. 고성과 = cost>0으로 ROAS 산출 가능한 그룹 중
    그 유형 ROAS 중앙값 이상(상위 절반). 유효 그룹 < _MIN_HIGH_PERF_GROUPS면 그 유형 제외
    (표본 부족 — 벤치마크 미산출). 관찰 프라이어라 정확 임계보다 fail-open 폴백이 중요."""
    by_type: dict[str, list[tuple[float, NaverEntitySnapshot]]] = defaultdict(list)
    for g in groups:
        p = perf.get(g.entity_id)
        if not p or p["cost"] <= 0:
            continue
        roas = p["conv_amt"] / p["cost"]
        by_type[g.campaign_type or ""].append((roas, g))

    out: dict[str, list[NaverEntitySnapshot]] = {}
    for ctype, scored in by_type.items():
        if len(scored) < _MIN_HIGH_PERF_GROUPS:
            continue
        med = median([r for r, _ in scored])
        winners = [g for r, g in scored if r >= med]
        if len(winners) >= _MIN_HIGH_PERF_GROUPS:
            out[ctype] = winners
    return out


def _confidence(sample_n: int) -> float:
    """표본 수 → 신뢰도(0~1). 소비측이 게이팅에 쓸 수 있게 단조 증가·상한 1.0."""
    return min(1.0, sample_n / _CONFIDENCE_FULL_N)


def _bid_band(winners: list[NaverEntitySnapshot]) -> dict | None:
    """고성과 그룹의 기본입찰 분포 [min,p50,max](원). bid_amt NULL 그룹은 제외. 유효 입찰
    < _MIN_HIGH_PERF_GROUPS면 None(밴드 미산출)."""
    bids = sorted(g.bid_amt for g in winners if g.bid_amt is not None)
    if len(bids) < _MIN_HIGH_PERF_GROUPS:
        return None
    return {"min": bids[0], "p50": int(median(bids)), "max": bids[-1], "n": len(bids)}


def _group_structure(winners: list[NaverEntitySnapshot]) -> dict:
    """고성과 그룹 구조 요약: 키워드 수 p50(WEB_SITE만 유효)·확장검색 비율(non-null 중 True)."""
    kw_counts = sorted(g.keyword_count for g in winners if g.keyword_count is not None)
    ext_vals = [g.extended_search for g in winners if g.extended_search is not None]
    return {
        "keyword_count_p50": int(median(kw_counts)) if kw_counts else None,
        "keyword_count_n": len(kw_counts),
        "extended_search_ratio": round(sum(1 for e in ext_vals if e) / len(ext_vals), 3) if ext_vals else None,
        "extended_search_n": len(ext_vals),
        "group_n": len(winners),
    }


def _keyword_verified_by_type(db: Session, opt_map: dict[str, str]) -> dict[str, list[str]]:
    """campaign_type → 대행사(optimizer='none') 활성 키워드 텍스트 셋. naver_entity keyword 행
    (WEB_SITE만 동기화, status='on')에서 name(키워드 텍스트) 수집·중복 제거. ours/mop 캠페인
    키워드는 제외(대행사가 사람 손으로 검증·등록한 정답지만 SS4 교차용)."""
    by_type: dict[str, set[str]] = defaultdict(set)
    rows = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "keyword", NaverEntity.status == "on")
        .all()
    )
    for e in rows:
        if opt_map.get(e.campaign_id, "none") != "none":
            continue  # ours/mop 제외 — 대행사(none) 등록 키워드만
        name = (e.name or "").strip()
        if name:
            by_type[e.campaign_type or ""].add(name)
    return {ctype: sorted(names) for ctype, names in by_type.items()}


def _upsert(db: Session, bench_kind: str, bench_key: str, value: dict | list, sample_n: int,
            confidence: float | None, now) -> None:
    """(bench_kind, bench_key) 1건 교체 upsert — value_json 재직렬화. 멱등(같은 날 재실행=동일)."""
    row = (
        db.query(NaverBmBenchmark)
        .filter(NaverBmBenchmark.bench_kind == bench_kind, NaverBmBenchmark.bench_key == bench_key)
        .first()
    )
    payload = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(NaverBmBenchmark(
            bench_kind=bench_kind, bench_key=bench_key, value_json=payload,
            sample_n=sample_n, confidence=confidence, computed_at=now,
        ))
    else:
        row.value_json = payload
        row.sample_n = sample_n
        row.confidence = confidence
        row.computed_at = now


def compute_benchmarks(db: Session, *, snapshot_date: date | None = None) -> dict:
    """SA-3 1회 산출 — 오늘 스냅샷 + 14일 성과로 벤치마크 프라이어를 재산출(교체 upsert).

    keyword_verified(대행사 등록 키워드셋)는 항상 산출(성과 무관·구조 신호). bid_band·
    group_structure는 고성과 그룹(그 유형 ROAS 중앙값 이상)에서만 산출 — 표본 부족 유형은
    스킵(fail-open, 소비측이 부재를 폴백으로 처리). 멱등: 같은 (bench_kind,bench_key)를 교체.
    """
    sdate = snapshot_date or kst_today()
    now = kst_now()
    window_to = sdate - timedelta(days=1)  # 완결일만(오늘 부분치 제외)
    window_from = sdate - timedelta(days=WINDOW_DAYS)

    opt_map = _agency_optimizer_map(db)
    groups = (
        db.query(NaverEntitySnapshot)
        .filter(
            NaverEntitySnapshot.snapshot_date == sdate,
            NaverEntitySnapshot.entity_type == "adgroup",
            NaverEntitySnapshot.status != "deleted",
        )
        .all()
    )
    perf = _group_performance(db, window_from, window_to)
    high = _high_performers_by_type(groups, perf)

    n_band = n_struct = n_kwset = 0

    # ── keyword_verified(campaign_type별 1행) ──
    kw_by_type = _keyword_verified_by_type(db, opt_map)
    for ctype, names in kw_by_type.items():
        _upsert(db, "keyword_verified", ctype or "UNKNOWN", names, len(names), None, now)
        n_kwset += 1

    # ── bid_band / group_structure(고성과 그룹 유형별 1행) ──
    for ctype, winners in high.items():
        band = _bid_band(winners)
        if band is not None:
            _upsert(db, "bid_band", ctype or "UNKNOWN", band, band["n"], _confidence(band["n"]), now)
            n_band += 1
        struct = _group_structure(winners)
        _upsert(db, "group_structure", ctype or "UNKNOWN", struct,
                struct["group_n"], _confidence(struct["group_n"]), now)
        n_struct += 1

    db.commit()
    result = {
        "snapshot_date": str(sdate), "window": f"{window_from}~{window_to}",
        "keyword_verified_types": n_kwset, "bid_band_types": n_band,
        "group_structure_types": n_struct,
    }
    log.info("[BM] SA-3 벤치마크 산출: %s", result)
    return result


# ══════════════════════════════════════════════════════════════════
# 소비측 프라이어 로더 (전부 fail-open — 부재/파싱실패 시 None/빈셋 → 소비 SA 기존 동일)
# ══════════════════════════════════════════════════════════════════
def verified_keyword_set(db: Session) -> set[str]:
    """SS4 교차용 — 대행사 등록 키워드 텍스트 전체 셋(campaign_type 무관 union).

    naver_bm_benchmark(bench_kind='keyword_verified') 행들의 value_json 리스트를 합쳐 반환.
    부재/파싱 실패는 빈 셋(fail-open — SS4는 빈 셋이면 교차 플래그 없이 기존과 동일 출력)."""
    out: set[str] = set()
    try:
        rows = (
            db.query(NaverBmBenchmark)
            .filter(NaverBmBenchmark.bench_kind == "keyword_verified")
            .all()
        )
    except Exception as e:  # noqa: BLE001 — 조회 실패는 빈 셋(fail-open)
        log.warning("[BM] verified_keyword_set 조회 실패(fail-open, 빈 셋): %s", e)
        return out
    for r in rows:
        try:
            names = json.loads(r.value_json) if r.value_json else []
        except (ValueError, TypeError):
            continue
        out.update(n for n in names if isinstance(n, str))
    return out


def bid_band_anchor(db: Session, campaign_type: str) -> int | None:
    """B-X 초기입찰 프라이어 — campaign_type 고성과 그룹 입찰밴드의 p50(원)|None.

    naver_bm_benchmark(bench_kind='bid_band', bench_key=campaign_type)의 value_json.p50.
    부재/파싱 실패는 None(fail-open — exploration.adaptive_step은 None이면 기존 콜드스타트 폴백)."""
    try:
        row = (
            db.query(NaverBmBenchmark)
            .filter(
                NaverBmBenchmark.bench_kind == "bid_band",
                NaverBmBenchmark.bench_key == campaign_type,
            )
            .first()
        )
    except Exception as e:  # noqa: BLE001 — 조회 실패는 None(fail-open)
        log.warning("[BM] bid_band_anchor 조회 실패(fail-open, None): %s", e)
        return None
    if row is None or not row.value_json:
        return None
    try:
        p50 = json.loads(row.value_json).get("p50")
    except (ValueError, TypeError, AttributeError):
        return None
    return int(p50) if isinstance(p50, (int, float)) else None
