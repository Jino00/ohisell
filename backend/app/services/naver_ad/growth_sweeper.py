# growth_sweeper.py — growth_sweeper SA (듀얼모드 스프린트 Phase 2, D-NAO-22-①)
# 역할(SA): 예외 보드(bleeding_keywords/starving_winners 등 소수 후보)만 보는 account_diagnosis와
#   달리, 전 활성 WEB_SITE 키워드(~89,274개, D-NAO-22 실측)를 로컬로 스윕해 "현재 입찰 <<
#   경제성 상한" 갭이 큰 순으로 "이익 보장 볼륨 확장" 후보를 산출한다. 이 함수 자체는 광고
#   API를 호출하지 않는다(naver_ad_daily/naver_entity만 읽음, 순수 함수) — estimate(외부 API)
#   조회는 harness(proposal_pipeline)가 이 함수의 반환값 중 상위 N개에만 별도로 수행한다
#   (전수 estimate 금지, 계획서 §4-Phase2).
# SA간 직접 호출 금지(원칙18) — bid_simulator.pooled_rpc/affordable_ceiling을 그대로 재사용.
from __future__ import annotations

from decimal import Decimal
from typing import Callable

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity
from app.services.naver_ad import bid_simulator, hierarchical_pooling
from app.services.naver_ad.account_diagnosis import LOW_CLICK_THRESHOLD
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

WEB_SITE = "WEB_SITE"  # 공개 상수 — proposal_pipeline이 _precompute_aggregates(campaign_type=...)에 재사용
_Q4 = Decimal("0.0001")

# estimate(외부 API) 예산 — 89K 전수 조회 금지, 갭 상위 후보만 실측(D-NAO-22-①). 네이버 SA
# estimate 엔드포인트 1콜 상한(200건, naver_sa_ad_fetcher._ESTIMATE_*_MAX_ITEMS 실측, ref 23)과
# 동일 값을 예산으로 채택 — 08:00 1회 실행에서 estimate 콜 소수 회로 스윕이 끝나도록.
ESTIMATE_BUDGET = 200

# D-NAO-20 스톱로스 절대액 산식 = recommended_bid × STOP_LOSS_CLICK_MULTIPLE. 근거: "10클릭
# 무전환 = 통계적으로 판단 가능한 실패 신호"는 D-NAO-9가 이미 채택한 임계값(LOW_CLICK_THRESHOLD,
# bid_simulator._SHRINK_K도 동일 상수 재사용)이지 이번에 새로 발명한 상수가 아니다(추정 금지
# 원칙 — 근거 없는 임의 원화값 대신 기존 승인 임계값을 재사용).
STOP_LOSS_CLICK_MULTIPLE = LOW_CLICK_THRESHOLD

# 탐색 예산 총액 캡(D-NAO-20 "동시 육성 최대 손실 상계") — 원화 총액 캡은 캠페인별 daily_budget
# 집계 소스가 아직 없어(vicious_cycle_flags 보드와 동일한 기존 한계, docs 확인 안 됨) 산출
# 불가. 대신 1회 실행당 생성 건수를 캡해 총 노출 손실을 사실상 제한하고, 합계를 harness가
# 계정 브리프에 노출한다(count 캡으로 대체 — Phase 5 execution_harness가 실제 집행을 게이트).
GROWTH_PROPOSAL_CAP = 50


def _active_web_site_keywords(db: Session) -> list[NaverEntity]:
    """전 활성(status=on) WEB_SITE 키워드 엔티티 — 스윕 모집단(D-NAO-22-①)."""
    return (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "keyword", NaverEntity.status == "on",
            NaverEntity.campaign_type == WEB_SITE,
        )
        .all()
    )


def _keyword_perf_map(db: Session, date_from, date_to) -> dict[str, dict]:
    """키워드ID → {clk, conv_amt} — naver_ad_daily 집계(로컬, API 無, N+1 방지 1콜)."""
    rows = (
        db.query(
            NaverAdDaily.keyword_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == WEB_SITE, NaverAdDaily.keyword_id != "",
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.keyword_id)
        .all()
    )
    return {
        kw_id: {"clk": int(clk), "conv_amt": int(direct) + int(indirect)}
        for kw_id, clk, direct, indirect in rows
    }


def find_growth_candidates(
    db: Session, date_from, date_to, *,
    correction_factor: Decimal, agg: dict, target_roas_for: Callable[[str], Decimal],
) -> list[dict]:
    """전 활성 WEB_SITE 키워드를 로컬 스윕해 (현재입찰 << 경제성상한) 갭 상위 후보를 산출.

    agg: proposal_pipeline._precompute_aggregates(campaign_type=WEB_SITE)의 반환값이어야 한다
      (group/campaign/account, 회당 1회 harness가 계산해 넘김 — 재조회 금지, N+1 방지). 반드시
      WEB_SITE로 스코프된 집계여야 함(codex 지적, 라이브검증 이력 없음·리뷰 발견) — 전 캠페인
      유형(SHOPPING/BRAND_SEARCH 포함) 집계를 넘기면, 클릭이 전혀 없는 WEB_SITE 신규 키워드가
      다른 캠페인유형 매출로 부풀려진 계정 prior를 그대로 물려받아 근거 없는 성장 제안이 나올
      수 있다. target_roas_for: campaign_id → Decimal(harness가 campaign_target_resolver 결과를
      캐싱해 전달, 계획서 D-S3-b 우선순위 그대로).

    반환: [{keyword_id, campaign_id, adgroup_id, current_bid, economic_ceiling, gap,
            clk, conv_amt}, ...] gap 내림차순.
    제외 조건: ①current_bid(bid_amt)가 없는 키워드(그룹 기본가 상속, 갭 계산 기준 없음 —
      추정 금지) ②economic_ceiling<=0(수익 근거 없음, negative_keyword 소관) ③gap<=0(이미
      상한 이상 입찰 중, 확장 대상 아님) ④계정 전체에 클릭이 전혀 없는 부트스트랩 초기 상태
      (D-NAO-9 모수 게이트 — pooled_rpc가 상위 prior로도 근거를 만들 수 없는 유일한 경우).
    """
    if agg["account"].get("clk", 0) < LOW_CLICK_THRESHOLD:
        return []  # 계정 전체 부트스트랩 초기 — 풀링 신뢰도 자체가 없음(D-NAO-9)

    entities = _active_web_site_keywords(db)
    perf = _keyword_perf_map(db, date_from, date_to)

    candidates: list[dict] = []
    for e in entities:
        if e.bid_amt is None:
            continue
        row = perf.get(e.entity_id, {"clk": 0, "conv_amt": 0})
        campaign_agg = agg["campaign"].get(e.campaign_id, {"clk": 0, "conv_amt": 0})
        group_agg = agg["group"].get(e.parent_id, {"clk": 0, "conv_amt": 0})
        target_roas = target_roas_for(e.campaign_id)

        rpc_raw = hierarchical_pooling.pool_metric(
            row, group_agg, campaign_agg, agg["account"], "rpc",
        )  # M2-a(D-NAO-214·ref 65 S1-ⓑ): bid_simulator.pooled_rpc → hierarchical_pooling.
        # 값은 동일하다 — 양쪽 다 K=10(bid_simulator._SHRINK_K = LOW_CLICK_THRESHOLD = 10 =
        # hierarchical_pooling.SHRINK_K)로 같은 공식 (n·raw+K·prior)/(n+K)를 쓴다. 중복 구현을
        # 하나로 접는 것이지 계산을 바꾸는 게 아니다(회귀 0 — 테스트가 두 경로 동치를 잡는다).
        rpc_corrected = (rpc_raw * correction_factor).quantize(_Q4)
        ceiling = bid_simulator.affordable_ceiling(rpc_corrected, target_roas)
        if ceiling <= 0:
            continue

        gap = ceiling - e.bid_amt
        if gap <= 0:
            continue

        candidates.append({
            "keyword_id": e.entity_id, "campaign_id": e.campaign_id, "adgroup_id": e.parent_id,
            "current_bid": e.bid_amt, "economic_ceiling": ceiling, "gap": gap,
            "clk": row["clk"], "conv_amt": row["conv_amt"],
            "sample_thin": row["clk"] < LOW_CLICK_THRESHOLD,
        })

    candidates.sort(key=lambda c: c["gap"], reverse=True)
    return candidates
