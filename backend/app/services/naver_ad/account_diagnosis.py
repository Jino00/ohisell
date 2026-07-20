# account_diagnosis.py — account_diagnosis_sa (쿠팡 판정 이식, P2-S2)
# 역할(SA): naver_ad_daily/naver_entity/naver_search_term_daily만 읽어 6개 진단 보드를 계산.
#   출혈/승자·굶는승자/확장버킷/쇼핑그룹BEP/제외후보/3단분류. 제안 없음(D-3 사실만 정리) —
#   판정만 하고 액션은 P2-S3(bid_simulator·proposal_writer) 소관.
# 판정 기준 이식원(쿠팡 스킬 ohi-ad-learning-loop): BEP 미달=즉시개입 성격의 '출혈',
#   모수 게이트(D-NAO-9 30일 클릭<10='저클릭')는 keyword_volume_sync과 동일 상수 재사용.
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverAdgroupProduct, NaverChangeLog, NaverEntity, NaverSearchTermDaily
from app.services.naver_ad import effective_bid as effbid
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

_WEB_SITE = "WEB_SITE"
_SHOPPING = "SHOPPING"

# D-NAO-9/18: keyword_volume_sync과 동일 상수 — 30일 창, 클릭 10미만=판정 불가(저클릭/굶는 상태)
LOW_CLICK_LOOKBACK_DAYS = 30
LOW_CLICK_THRESHOLD = 10

# DL1(D-NAO-65) 스톱로스 창 절체 상수.
# 입찰 변경 canonical action — naver_execution_harness._ACTION_BY_PROPOSAL_TYPE이 bid_up/
# bid_down/growth_bid_up을 전부 'update_bid'로 기록한다(probe_revert도 동일 하드코딩 관례.
# entity_sync의 외부 감지 기록은 'external_bid_change'라 별개 — 우리 실집행만 창 기준).
# 순환 import 회피 위해 상수로 둔다(그쪽 매핑 변경 시 함께 갱신).
_BID_CHANGE_ACTIONS = ("update_bid",)
# 행동 창(스톱로스 발동 비용): 변경 이력 없는 유닛의 고정 폴백 = 3일(ref 33 소규모 하한 보수값).
_STOP_LOSS_FALLBACK_DAYS = 3
# 만성 판정 창(shopping 레버끊김 보정ROAS·실측 CPC): 7일 롤링 soft(ref 33 [1], 단발 나쁜 날로 pause 방지).
_CHRONIC_WINDOW_DAYS = 7
# DL2(D-NAO-65) 예외② 레버끊김 임계 배수 — 만성 창 실측 CPC(cost/clk)가 그룹입찰의 k배를
# 초과하면 "그룹입찰을 낮춰도 지출이 안 줄어드는" 소재-레벨 입찰 정황으로 판정한다(pause만 실효).
# k=5 근거: D-NAO-64 MO 실사례(그룹입찰 50 vs 실측 CPC 800대 = 16배)에서 유도한 **보수값**.
# 실측 CPC는 원래 그룹입찰을 초과할 수 없는 구조라(입찰=상한) CPC≫그룹입찰 자체가 소재-레벨
# 입찰이 그룹입찰을 우회한다는 정의적 신호 — 배수를 크게 잡아 정상 그룹 오판을 막는다.
_LEVER_BROKEN_CPC_MULTIPLE = 5
# DL2 GATE P2-2: 바닥 대기 지속 밸브(floor_bleed) 최소 실증 기간 — at-floor·무전환·행동 창이
# 이 일수 이상·창 내 비용≥스톱로스면 "레버로 할 수 있는 걸 다 했는데도 출혈 지속"의 실증 →
# 터미널 pause 격상(무한 대기 방치 차단). 3일 = _STOP_LOSS_FALLBACK_DAYS와 동일 보수값
# (전환 귀속 ~1일 정착 완충 포함 — 방금 바닥 도달한 유닛은 창<3일이라 대기 유지).
_FLOOR_BLEED_MIN_DAYS = 3
# B4 GATE P2-3①(D-NAO-65): 레버끊김 재개 flip-flop 쿨다운 — pause→resume→재pause 이력 그룹은
# 재pause 후 이 일수 동안 재개 후보 제외(D-NAO-19 flip-flop 방지 계승, 3일 = 위 밸브들과 동일
# 보수값 — 재pause가 곧 "재개 실패 증거"이므로 그 증거의 정착 기간).
_LEVER_RESUME_COOLDOWN_DAYS = 3

_Q4 = Decimal("0.0001")


def earliest_real_data_date(db: Session, date_to: date, lookback_days: int) -> date | None:
    """[date_to-lookback_days+1, date_to] 창 내 실단위(비-backfill) 최초 적재일.

    파이프라인 가동 초기(예: P0 개시 직후)엔 요청한 lookback_days 전체가 아직 쌓이지
    않았을 수 있음 — 보정계수(D-NAO-21)처럼 다른 소스와 '같은 창'으로 비교해야 하는
    계산이 짧은 실데이터 구간과 긴 요청 구간을 섞어 왜곡되지 않도록 harness가 이 값으로
    실제 비교 가능한 시작일을 구한다.
    """
    window_start = date_to - timedelta(days=lookback_days - 1)
    earliest = db.query(sqlfunc.min(NaverAdDaily.ad_date)).filter(
        NaverAdDaily.ad_date >= window_start, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).scalar()
    return earliest


def _corrected_roas(roas_naver: float | None, correction_factor: Decimal) -> float | None:
    """D-NAO-21: 네이버 convAmt 과대(~2.6배) 보정 — roas_naver × 보정계수."""
    if roas_naver is None:
        return None
    return float((Decimal(str(roas_naver)) * correction_factor).quantize(_Q4))


def _keyword_rows(db: Session, date_from: date, date_to: date, campaign_type: str) -> list[dict]:
    """캠페인유형별 키워드 grain 집계 (WEB_SITE=nkw-.../확장''  SHOPPING은 그룹만이라 미사용)."""
    q = (
        db.query(
            NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id, NaverAdDaily.keyword_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == campaign_type,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id, NaverAdDaily.keyword_id)
        .all()
    )
    rows = []
    for campaign_id, adgroup_id, keyword_id, imp, clk, cost, conv_direct, conv_indirect in q:
        conv_amt = int(conv_direct) + int(conv_indirect)
        roas_naver = float((Decimal(conv_amt) / Decimal(cost)).quantize(_Q4)) if cost else None
        rows.append({
            "campaign_id": campaign_id, "adgroup_id": adgroup_id, "keyword_id": keyword_id,
            "imp": int(imp), "clk": int(clk), "cost": int(cost), "conv_amt": conv_amt,
            "roas_naver": roas_naver,
        })
    return rows


def bleeding_keywords(
    db: Session, date_from: date, date_to: date, bep_roas: Decimal, correction_factor: Decimal,
) -> list[dict]:
    """출혈 키워드 — WEB_SITE 등록 키워드(확장버킷 제외) 중 보정ROAS < 계정 BEP, 비용순.

    확장('') 버킷은 개별 키워드가 아니므로 제외(expansion_bucket 보드가 별도 담당).
    """
    rows = [r for r in _keyword_rows(db, date_from, date_to, _WEB_SITE)
            if r["keyword_id"] and r["cost"] > 0]
    out = []
    for r in rows:
        roas_c = _corrected_roas(r["roas_naver"], correction_factor)
        if roas_c is not None and roas_c < float(bep_roas):
            out.append({**r, "roas_corrected": roas_c})
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def starving_winners(
    db: Session, date_from: date, date_to: date, target_roas: Decimal, correction_factor: Decimal,
) -> list[dict]:
    """굶는 승자 — 보정ROAS ≥ 목표 달성(승자 검증됨)인데 일평균 클릭 < 1(D-NAO-9 저클릭 상한).

    쿠팡 스킬 '굶는 승자'(성과 좋은데 노출 부족) 이식 — 판단 불가 상태에 갇힌 채 방치되지 않게
    D-NAO-18 육성 파이프라인의 입력이 되는 보드.
    """
    days = (date_to - date_from).days + 1
    rows = [r for r in _keyword_rows(db, date_from, date_to, _WEB_SITE)
            if r["keyword_id"] and r["cost"] > 0]
    out = []
    for r in rows:
        roas_c = _corrected_roas(r["roas_naver"], correction_factor)
        avg_daily_clk = r["clk"] / days if days else 0
        if roas_c is not None and roas_c >= float(target_roas) and avg_daily_clk < 1.0:
            out.append({**r, "roas_corrected": roas_c, "avg_daily_clk": round(avg_daily_clk, 3)})
    out.sort(key=lambda x: x["roas_corrected"], reverse=True)
    return out


def expansion_bucket(db: Session, date_from: date, date_to: date, correction_factor: Decimal) -> dict:
    """확장버킷 — WEB_SITE & keyword_id='' (등록 키워드 밖 자동매칭 노출), 파워링크 대비 비용 비중.

    D-NAO-18③ 확장버킷 검색어 승격의 대상 전체 규모 — search_term_daily(source=expkeyword)가
    검색어 단위 세부, 이 보드는 버킷 총계(비용비중·ROAS)만.
    """
    base = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.campaign_type == _WEB_SITE,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    web_site_total_cost = int(base.one()[0] or 0)

    exp = base.filter(NaverAdDaily.keyword_id == "").one()
    exp_cost, exp_clk, exp_imp, exp_direct, exp_indirect = (int(v or 0) for v in exp)
    exp_conv_amt = exp_direct + exp_indirect
    roas_naver = float((Decimal(exp_conv_amt) / Decimal(exp_cost)).quantize(_Q4)) if exp_cost else None

    return {
        "cost": exp_cost,
        "clk": exp_clk,
        "imp": exp_imp,
        "conv_amt": exp_conv_amt,
        "roas_naver": roas_naver,
        "roas_corrected": _corrected_roas(roas_naver, correction_factor),
        "web_site_total_cost": web_site_total_cost,
        "cost_share": (round(exp_cost / web_site_total_cost, 4) if web_site_total_cost else None),
    }


def shopping_group_bep(
    db: Session, date_from: date, date_to: date, bep_roas: Decimal, correction_factor: Decimal,
) -> list[dict]:
    """쇼핑검색 그룹(adgroup)별 BEP 미달 — SHOPPING은 개별 키워드가 없어 그룹 단위 진단."""
    q = (
        db.query(
            NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == _SHOPPING,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id)
        .all()
    )
    out = []
    for campaign_id, adgroup_id, cost, conv_direct, conv_indirect in q:
        cost = int(cost)
        if cost <= 0:
            continue
        conv_amt = int(conv_direct) + int(conv_indirect)
        roas_naver = float((Decimal(conv_amt) / Decimal(cost)).quantize(_Q4))
        roas_c = _corrected_roas(roas_naver, correction_factor)
        if roas_c is not None and roas_c < float(bep_roas):
            out.append({
                "campaign_id": campaign_id, "adgroup_id": adgroup_id, "cost": cost,
                "conv_amt": conv_amt, "roas_naver": roas_naver, "roas_corrected": roas_c,
            })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def exclusion_candidates(db: Session, date_from: date, date_to: date, *, limit: int = 20) -> list[dict]:
    """제외후보 — 확장버킷 검색어 중 비용 상위(전환은 검색어 단위로 추적되지 않음, 정직 경계).

    naver_search_term_daily에는 전환 컬럼이 없음(모델 확정, docs/references/22) — '전환0'을
    직접 판정할 수 없어 비용순 후보만 제시. 최종 제외/승격 판단(D-NAO-18③)은 이 후보를
    등록 키워드로 승격 시 실측 ROAS로 검증하는 P2-S3/P3의 몫.
    """
    q = (
        db.query(
            NaverSearchTermDaily.campaign_id, NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term, NaverSearchTermDaily.source,
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.imp), 0),
        )
        .filter(NaverSearchTermDaily.ad_date >= date_from, NaverSearchTermDaily.ad_date <= date_to)
        .group_by(
            NaverSearchTermDaily.campaign_id, NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term, NaverSearchTermDaily.source,
        )
        .order_by(sqlfunc.sum(NaverSearchTermDaily.cost).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "campaign_id": c, "adgroup_id": a, "search_term": term, "source": src,
            "cost": int(cost), "clk": int(clk), "imp": int(imp),
        }
        for c, a, term, src, cost, clk, imp in q
    ]


def keyword_triage(db: Session, *, as_of: date) -> dict:
    """죽은키워드 위생 3단분류(D-NAO-18) — 판정가능/육성후보/진짜정리.

    판정가능=최근 30일 클릭≥10(keyword_volume_sync과 동일 게이트, 통계적 판단 가능).
    육성후보=저클릭이지만 keywordstool 월검색량>0 확인됨(수요는 있는데 안 보임).
    진짜정리=저클릭 + 월검색량 0(또는 미조회) — 육성 불가, 위생 정리 대상.
    """
    cutoff = as_of - timedelta(days=LOW_CLICK_LOOKBACK_DAYS)
    clicks = dict(
        db.query(NaverAdDaily.keyword_id, sqlfunc.sum(NaverAdDaily.clk))
        .filter(NaverAdDaily.ad_date >= cutoff, NaverAdDaily.ad_date <= as_of, NaverAdDaily.keyword_id != "")
        .group_by(NaverAdDaily.keyword_id).all()
    )
    entities = db.query(NaverEntity).filter(
        NaverEntity.entity_type == "keyword", NaverEntity.status == "on",
    ).all()

    judgeable = growth_candidate = dead = 0
    for e in entities:
        clk = int(clicks.get(e.entity_id, 0) or 0)
        if clk >= LOW_CLICK_THRESHOLD:
            judgeable += 1
        elif e.monthly_volume and e.monthly_volume > 0:
            growth_candidate += 1
        else:
            dead += 1

    return {
        "total": len(entities),
        "judgeable": judgeable,
        "growth_candidate": growth_candidate,
        "dead": dead,
        "volume_unchecked": sum(1 for e in entities if e.monthly_volume is None),
    }


def vicious_cycle_flags(
    db: Session, date_to: date, target_roas: Decimal, correction_factor: Decimal,
) -> list[dict]:
    """악순환 감지 — 최근 7일 보정ROAS가 이전 23일(30일 창) 대비 하락 + 목표 미달 지속.

    쿠팡 스킬 패턴A(모수부족 악순환) 이식. ⚠️정직 경계: 일예산 소진율(<70%) 판정에 필요한
    캠페인 daily_budget 필드가 이 스키마엔 없음(D-NAO-13 naver_campaign_settings는 optimizer/
    mode만 저장) — 소진율 대신 '클릭 추세 하락'을 모수 위축의 대리 신호로 사용, 실제 소진율
    연결은 캠페인 예산 동기화(별도 스프린트) 확보 후 재검토.
    """
    recent_from, recent_to = date_to - timedelta(days=6), date_to
    prior_from, prior_to = date_to - timedelta(days=29), date_to - timedelta(days=7)

    def _daily_rows(date_from: date, date_to_: date, *, backfill: bool) -> dict:
        q = (
            db.query(
                NaverAdDaily.ad_date, NaverAdDaily.campaign_id,
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
            )
            .filter(
                NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to_,
                (NaverAdDaily.adgroup_id == BACKFILL_SENTINEL_ADGROUP) if backfill
                else (NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP),
            )
            .group_by(NaverAdDaily.ad_date, NaverAdDaily.campaign_id)
            .all()
        )
        return {(ad_date, cid): (int(cost), int(clk), int(direct), int(indirect))
                for ad_date, cid, cost, clk, direct, indirect in q}

    def _by_campaign(date_from: date, date_to_: date) -> dict[str, dict]:
        """실단위(P0) 우선, 실단위 없는 날짜만 backfill로 보충 — 겹치는 날짜 이중계상 방지."""
        real = _daily_rows(date_from, date_to_, backfill=False)
        backfill = _daily_rows(date_from, date_to_, backfill=True)
        merged: dict[str, list[int]] = {}
        for key, vals in {**backfill, **real}.items():  # real이 backfill을 덮어씀(같은 날짜 우선순위)
            _, cid = key
            acc = merged.setdefault(cid, [0, 0, 0, 0])
            for i, v in enumerate(vals):
                acc[i] += v

        out = {}
        for cid, (cost, clk, direct, indirect) in merged.items():
            conv_amt = direct + indirect
            roas = float((Decimal(conv_amt) / Decimal(cost)).quantize(_Q4)) if cost else None
            out[cid] = {"cost": cost, "clk": clk, "roas_naver": roas}
        return out

    recent = _by_campaign(recent_from, recent_to)
    prior = _by_campaign(prior_from, prior_to)

    flags = []
    for cid, r in recent.items():
        p = prior.get(cid)
        if not p or not p["clk"]:
            continue
        recent_roas_c = _corrected_roas(r["roas_naver"], correction_factor)
        prior_roas_c = _corrected_roas(p["roas_naver"], correction_factor)
        if recent_roas_c is None or prior_roas_c is None:
            continue
        recent_daily_clk = r["clk"] / 7
        prior_daily_clk = p["clk"] / 23
        declining = recent_roas_c < prior_roas_c * 0.9
        thinning = prior_daily_clk > 0 and recent_daily_clk < prior_daily_clk * 0.7
        below_target = recent_roas_c < float(target_roas)
        if declining and thinning and below_target:
            flags.append({
                "campaign_id": cid,
                "recent_roas_corrected": recent_roas_c, "prior_roas_corrected": prior_roas_c,
                "recent_daily_clk": round(recent_daily_clk, 2), "prior_daily_clk": round(prior_daily_clk, 2),
            })
    flags.sort(key=lambda x: x["recent_roas_corrected"])
    return flags


def _on_adgroup_ids(db: Session) -> set[str]:
    """부모 체인(campaign→adgroup)이 전부 status='on'인 adgroup_id 집합.

    entity_sync는 부모-자식 status를 캐스케이드하지 않는다(네이버 API가 캠페인/그룹/키워드
    상태를 각자 독립적으로 보고 — F2a codex 지적, D-NAO-27과 동일 근거) — 캠페인이 꺼져도
    자식 adgroup이 개별 status='on'으로 남을 수 있다. pause_candidates(codex[P2], X1b T3
    2라운드)가 이 세트로 자식 키워드의 실질 활성 여부를 교차 확인한다.
    """
    rows = db.query(
        NaverEntity.entity_type, NaverEntity.entity_id, NaverEntity.parent_id, NaverEntity.status,
    ).filter(NaverEntity.entity_type.in_(("campaign", "adgroup"))).all()
    on_campaigns = {r[1] for r in rows if r[0] == "campaign" and r[3] == "on"}
    return {r[1] for r in rows if r[0] == "adgroup" and r[3] == "on" and r[2] in on_campaigns}


def keyword_window_agg(db: Session, keyword_id: str, date_from: date, date_to: date) -> dict:
    """단일 키워드의 창 내 (cost, conv_amt) 집계 — SQL WHERE에 keyword_id를 직접 걸어
    `_keyword_rows()`처럼 전 키워드를 GROUP BY하지 않는다(codex[P2], X1b T3 — 후보/실행
    대상마다 창이 제각각이라 배치 불가할 때 O(대상 수 × 전체 키워드 행) 스케일을 피한다).
    resume_candidates와 naver_execution_harness(T4 guardrail context)가 공유한다.
    """
    cost, direct, indirect = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.keyword_id == keyword_id,
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.campaign_type == _WEB_SITE,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).one()
    return {"cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def campaign_window_agg(db: Session, campaign_id: str, date_from: date, date_to: date) -> dict:
    """단일 캠페인의 창 내 (cost, conv_amt) 집계 — keyword_window_agg의 캠페인 grain 병렬판
    (P2, D-NAO-42-f PLAN §5-D). naver_execution_harness._build_guardrail_context의 campaign
    브랜치가 이 값으로 guardrail_gate._check_budget용 roas_corrected/unconverted_spend를
    산출한다.

    ⚠️ keyword_window_agg와 달리 campaign_type == WEB_SITE 필터를 **의도적으로 걸지 않는다**
    — 예산 통제(budget_up/budget_down)는 캠페인 그레인 개념이라 SHOPPING(쇼핑검색) 캠페인도
    대상이다(예: 라이브 04 카나리는 SHOPPING). WEB_SITE 전용인 키워드 집계와는 스코프가
    다르다(PLAN §5-D 명시, 추정 아님).
    adgroup_id != BACKFILL_SENTINEL_ADGROUP은 그대로 유지 — 캠페인 단위 백필 센티널 행과의
    이중집계를 막는다(keyword_window_agg와 동일 규율).
    """
    cost, direct, indirect = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.campaign_id == campaign_id,
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).one()
    return {"cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def _last_bid_change_kst_date(db: Session, entity_ids: set[str]) -> dict[str, date]:
    """유닛(키워드 entity_id 또는 쇼핑 adgroup_id)별 마지막 **성공한** 입찰변경 KST 날짜
    (DL1, D-NAO-65).

    스톱로스 임계는 '현재입찰 × LOW_CLICK_THRESHOLD'라 비용도 그 입찰이 유효한 구간만
    합산해야 시점이 정합한다(D-NAO-63 시점 불일치 소멸) — naver_change_log에서 bid 변경
    (update_bid, _BID_CHANGE_ACTIONS·dry_run=False) 최신 1건 changed_at의 KST 날짜를 돌려준다.
    ★성공 판별 = after_value 존재(resume_candidates와 동일 규약) — _guard_failure·실행 예외
    경로가 남기는 실패 시도 행(after_value=None, outcome='failed')까지 잡으면, 매일 bid_down이
    가드에 막히는 출혈 유닛의 창이 매일 리셋돼 스톱로스가 영구 침묵한다(GATE P1-A).
    ★changed_at은 **KST-naive**(harness execute가 now=kst_now()로 기입, probe_revert 주석
    명시 — server_default UTC와 달리 이 경로는 KST) — 변환 없이 .date()가 곧 KST 달력일
    (GATE P1-B: +9h를 더하면 KST 15시 이후 변경 유닛의 창이 하루 밀림).
    ★34개 유닛에 N+1 쿼리 금지: entity_id 집합 IN절 + GROUP BY 1쿼리 집계.
    (스케일 메모: SQLite IN 파라미터 상한 — 유닛 수천 규모 계정이 되면 청크 분할 필요.)"""
    if not entity_ids:
        return {}
    rows = (
        db.query(NaverChangeLog.entity_id, sqlfunc.max(NaverChangeLog.changed_at))
        .filter(
            NaverChangeLog.action.in_(_BID_CHANGE_ACTIONS),
            NaverChangeLog.entity_id.in_(entity_ids),
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),  # 실제 성공한 쓰기만(실패/가드차단 시도 제외)
            NaverChangeLog.changed_at.isnot(None),
        )
        .group_by(NaverChangeLog.entity_id)
        .all()
    )
    return {
        entity_id: changed_at.date()
        for entity_id, changed_at in rows if changed_at is not None
    }


def _last_ad_bid_change_by_group(db: Session, adgroup_ids: set[str]) -> dict[str, date]:
    """레버 미연결 그룹의 마지막 **소재-레벨** 입찰변경 KST 날짜(B3 이월, D-NAO-65).

    B3부터 우리가 소재 bidAmt를 직접 바꾸므로(entity_type='ad'·action='update_bid' change_log),
    미연결 유닛(source='ad')의 증거 창도 '마지막 소재입찰 변경 이후'로 절체해야 시점이 정합한다
    (변경 전 고CPC·고비용 혼입 방지 — source='group' 유닛의 DL1 창 절체와 동일 근거).
    NaverAdgroupProduct(adgroup↔ad_id 매핑)로 그룹의 소재들을 모으고, 그 ad_id들의 update_bid
    change_log 최신일(_last_bid_change_kst_date 재사용 — entity_type 무관·entity_id만 필터)을
    그룹별 max로 집계. ★B3 실집행 전에는 ad change_log가 없어 빈 dict(종전 만성 7일 창 동작
    보존 — 하위호환). ★1쿼리 매핑 + 1쿼리 change_log(N+1 금지, _last_bid_change_kst_date 계승)."""
    if not adgroup_ids:
        return {}
    ad_rows = (
        db.query(NaverAdgroupProduct.adgroup_id, NaverAdgroupProduct.ad_id)
        .filter(
            NaverAdgroupProduct.adgroup_id.in_(adgroup_ids),
            NaverAdgroupProduct.ad_id.isnot(None),
        )
        .all()
    )
    ad_to_group: dict[str, str] = {ad_id: gid for gid, ad_id in ad_rows if ad_id}
    if not ad_to_group:
        return {}
    last_by_ad = _last_bid_change_kst_date(db, set(ad_to_group))
    out: dict[str, date] = {}
    for ad_id, changed_date in last_by_ad.items():
        gid = ad_to_group.get(ad_id)
        if gid is not None and (gid not in out or changed_date > out[gid]):
            out[gid] = changed_date
    return out


def _stop_loss_window_start(date_from: date, date_to: date, last_change: date | None) -> date:
    """스톱로스 행동 창 시작일 (DL1, D-NAO-65) — 마지막 입찰변경 있으면 max(date_from, 변경일),
    없으면 3일 고정 폴백 max(date_from, date_to-2). 신규/장기휴면 유닛의 조기 사살 방지."""
    if last_change is None:
        return max(date_from, date_to - timedelta(days=_STOP_LOSS_FALLBACK_DAYS - 1))
    return max(date_from, last_change)


def _daily_cost_conv_by_unit(
    db: Session, unit_col, unit_ids: set[str], campaign_type: str, date_from: date, date_to: date,
) -> dict[str, list[tuple]]:
    """(유닛, ad_date) 그레인 일별 (imp, clk, cost, conv_amt) 집계 — 유닛별 스톱로스 창
    컷오프를 파이썬에서 적용하기 위한 단일 쿼리(DL1, N+1 회피 — keyword_window_agg가 대상마다
    쿼리하는 것과 달리 유닛마다 창 시작일이 달라 GROUP BY 배치가 불가하므로 date 그레인으로
    한 번에 긁고 파이썬에서 합산). unit_col = NaverAdDaily.keyword_id 또는 .adgroup_id."""
    if not unit_ids:
        return {}
    q = (
        db.query(
            unit_col, NaverAdDaily.ad_date,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            unit_col.in_(unit_ids),
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == campaign_type,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(unit_col, NaverAdDaily.ad_date)
        .all()
    )
    out: dict[str, list[tuple]] = {}
    for unit_id, ad_date, imp, clk, cost, direct, indirect in q:
        out.setdefault(unit_id, []).append(
            (ad_date, int(imp), int(clk), int(cost), int(direct) + int(indirect))
        )
    return out


def _sum_cost_conv_since(rows: list[tuple], window_start: date) -> tuple[int, int, int, int]:
    """일별 행 목록에서 ad_date ≥ window_start 구간의 (imp, clk, cost, conv_amt) 합산 (DL1)."""
    imp = clk = cost = conv = 0
    for ad_date, i, c, co, cv in rows:
        if ad_date >= window_start:
            imp += i
            clk += c
            cost += co
            conv += cv
    return imp, clk, cost, conv


def pause_candidates(db: Session, date_from: date, date_to: date) -> list[dict]:
    """정지 후보 (X1b T3, D-NAO-38) — WEB_SITE 등록 키워드(status='on', 부모 체인도 전부 on)
    중 창 내 전환 0건 + 누적비용이 스톱로스 절대액(D-NAO-20) 이상. 스톱로스 절대액 = 현재
    입찰가 × LOW_CLICK_THRESHOLD(growth_sweeper.STOP_LOSS_CLICK_MULTIPLE과 동일 상수,
    D-NAO-9/20 근거 재사용) — "무전환 지출 상한 도달 시 자동 인하/중단"(D-NAO-16)의 "중단"
    쪽 구현. NaverEntity에 bid_amt가 없는(미확보) 키워드는 스톱로스 계산 불가라 fail-closed
    제외. **부모(광고그룹·캠페인)가 off인데 키워드만 on인 경우도 제외**(codex[P2]) — 그
    상태에서 키워드에 별도 정지를 걸면, 나중에 부모만 재개해도 이 키워드는 계속 잠긴 채
    남는다(의도치 않은 영구 정지).

    ★DL1(D-NAO-65): 스톱로스 비용/무전환 판정을 15일 롤링 전체 창이 아니라 유닛별 **행동 창
    (마지막 입찰변경 이후, 변경이력 없으면 3일 폴백)** 으로 좁힌다 — 스톱로스 임계가
    '현재입찰×10'이라 비용도 그 입찰 유효 구간만 합산해야 시점이 정합(D-NAO-63 시점 불일치
    소멸: 과거 고입찰 시절 비용만 남은 휴면 유닛은 최근 창 비용이 스톱로스 미만이라 미진입)."""
    on_adgroups = _on_adgroup_ids(db)
    entity_map = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword", NaverEntity.status == "on").all()
    }
    if not entity_map:
        return []

    # 부모 체인 on + bid_amt 확보된 키워드만 후보(그 집합으로 창 조회 — 무관 키워드 스캔 회피).
    candidate_ids = {
        kid for kid, e in entity_map.items() if e.bid_amt and e.parent_id in on_adgroups
    }
    if not candidate_ids:
        return []
    last_change = _last_bid_change_kst_date(db, candidate_ids)
    daily = _daily_cost_conv_by_unit(db, NaverAdDaily.keyword_id, candidate_ids, _WEB_SITE, date_from, date_to)

    out = []
    for keyword_id in candidate_ids:
        rows = daily.get(keyword_id)
        if not rows:
            continue
        entity = entity_map[keyword_id]
        last_change_date = last_change.get(keyword_id)
        window_start = _stop_loss_window_start(date_from, date_to, last_change_date)
        # GATE P2: 행동 창 2일 미만(마지막 실변경=date_to 당일)이면 zero_conv 판정 보류 —
        # 간접전환 ~1일 정착이라 변경 당일 하루 창에서는 전환이 있어도 0으로 읽혀 고지출
        # 유닛이 오발동 사살될 수 있다. 다음날 창이 2일이 되면 정상 판정된다.
        # (GATE-2R P3: 술어는 변경일 기준 — window_start 기준이면 date_from==date_to인
        #  ad-hoc 짧은 창에서 오래된 변경 이력만으로 유닛이 통째 억제된다.)
        if last_change_date is not None and last_change_date >= date_to:
            continue
        imp, clk, cost, conv_amt = _sum_cost_conv_since(rows, window_start)
        if cost <= 0:
            continue
        stop_loss_amount = entity.bid_amt * LOW_CLICK_THRESHOLD
        if conv_amt == 0 and cost >= stop_loss_amount:
            roas_naver = float((Decimal(conv_amt) / Decimal(cost)).quantize(_Q4)) if cost else None
            # DL2 GATE P2-2 지속 밸브: 행동 창 길이(일) = date_to - window_start + 1(DL1 산출물
            # 재사용, 새 상태 저장 없음). 창 ≥3일 무전환 출혈이면 floor_bleed 태깅 —
            # _stop_loss_proposal이 at-floor시 대기 대신 밸브 pause로 격상(무한 대기 차단).
            # at-floor 여부는 writer가 _step_down_bid로 판정(단일 진실 소스) — 여기선 기간만.
            window_days = (date_to - window_start).days + 1
            out.append({
                "campaign_id": entity.campaign_id, "adgroup_id": entity.parent_id,
                "keyword_id": keyword_id, "imp": imp, "clk": clk, "cost": cost,
                "conv_amt": conv_amt, "roas_naver": roas_naver,
                "current_bid": entity.bid_amt, "stop_loss_amount": stop_loss_amount,
                "floor_bleed": window_days >= _FLOOR_BLEED_MIN_DAYS, "window_days": window_days,
            })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def resume_candidates(
    db: Session, date_to: date, target_roas_resolver, correction_factor: Decimal,
) -> list[dict]:
    """재개 후보 (X1b T3, D-NAO-38) — 우리 시스템이 정지시킨(change_log proposal_id 있음 —
    Jino가 콘솔에서 수동 정지한 경우는 proposal_id가 없어 제외, 우리가 모르는 이유로 정지된
    것을 임의로 재개하지 않는다) 키워드 중 정지 직전 창의 보정ROAS가 현재 목표 이상 —
    D-NAO-16 "정지 사유 해소" 중 "BEP 개선"(우리 목표 자체가 낮아졌거나, 정지 당시 이미
    양호했던 키워드) 신호.

    target_roas_resolver: campaign_id(str) → Decimal, 캠페인별 목표(override > 계정 기본값,
    campaign_target_resolver.resolve_target_roas) 해석 함수(harness가 주입). 계정 단일
    target_roas만 쓰면 캠페인 override가 무시되는 재발 버그 패턴(codex[P2] — compute_bid_sims의
    _make_target_roas_resolver가 동일 근거로 이미 한 번 고친 버그, 2026-07-07 라이브검증
    이력)이라 여기도 같은 방식으로 캠페인별 해석을 강제한다.

    정직 경계: "계절성 회복·CPC 하락"(D-NAO-16 예시의 나머지 2가지)은 미구현 — 정지 중엔
    해당 키워드의 새 실적이 쌓이지 않아 직접 관측 불가하고, 대체 신호(캠페인 CPC 추세 등)는
    이번 스코프에 넣지 않았다(§8 승계 큐 후보로 별도 기록 필요).

    **최신 잠금변경이 우리 정지일 때만 후보**(codex[P2]) — 정지→우리 재개→(수동이든 새
    시스템 정지든)재정지 이력이 있으면 지금 off 상태의 진짜 원인은 그 마지막 이벤트다.
    action 문자열은 방향 판별에 쓰지 않는다 — naver_execution_harness가 pause·resume 둘 다
    action='set_user_lock'으로 기록하므로(_ACTION_BY_PROPOSAL_TYPE이 두 proposal_type을
    하나의 실행 액션으로 묶음, update_bid가 bid_up/bid_down/growth_bid_up을 묶는 것과 동일
    관례, codex[P2] X1b T4 2라운드) 정지 여부는 after_value의 실제 userLock 값으로 판별한다.

    **의도적 비대칭(Claude 적대 리뷰 지적, codex 한도 소진 대체, 2026-07-10) — pause_candidates와
    달리 부모(광고그룹·캠페인) 체인 상태를 확인하지 않는다**: pause_candidates의 부모체인
    검사는 "새 판단이 부모-off로 왜곡된 실적 데이터에 근거할 위험"을 막는 것(부모가 꺼져
    트래픽이 없는데 그걸 키워드 자체 문제로 오판)이지만, resume_candidates는 **과거 정지 시점
    이전(부모 상태와 무관한 실데이터) 실적을 근거로 우리 자신의 과거 판단을 되돌리는 것**이라
    같은 왜곡 경로가 없다. 부모가 꺼진 채로 재개해도(userLock만 false로 복원) 키워드는 실제
    노출되지 않으며(네이버는 부모 체인 전부 on이어야 서빙), 이는 낭비가 아니라 우리
    키워드레벨 판단과 남의 부모레벨 운영 판단을 분리 보존하는 것 — 부모가 나중에 켜지면
    자동으로 정상 서빙 재개(재차 개입 불요). 판단 재검토 필요 시 Jino 논의 대상.
    """
    off_entities = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword", NaverEntity.status == "off").all()
    }
    if not off_entities:
        return []
    off_ids = set(off_entities)

    # 실제 성공한 쓰기 판별은 outcome이 아니라 after_value 존재 여부로 한다(X1b T5 배선확인,
    # Claude 적대적 리뷰 발견 — outcome은 이제 D+14 채점 전엔 NULL이고 채점 후엔
    # improved/declined/neutral로 바뀌므로 "executed" 영구 상태가 아니다. naver_execution_harness
    # 주석 참조).
    lock_rows = (
        db.query(
            NaverChangeLog.entity_id, NaverChangeLog.campaign_id, NaverChangeLog.after_value,
            NaverChangeLog.proposal_id, NaverChangeLog.changed_at,
        )
        .filter(
            NaverChangeLog.entity_type == "keyword",
            NaverChangeLog.action.in_(["set_user_lock", "external_status_change"]),
            NaverChangeLog.entity_id.in_(off_ids),
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    latest_lock_change: dict[str, tuple] = {}
    for entity_id, campaign_id, after_value, proposal_id, changed_at in lock_rows:
        latest_lock_change[entity_id] = (campaign_id, after_value, proposal_id, changed_at)  # asc 정렬 — 뒤에 올수록 최신

    out = []
    for keyword_id, (campaign_id, after_value, proposal_id, paused_at) in latest_lock_change.items():
        if proposal_id is None:
            continue  # 최신 잠금변경이 우리 실행이 아님(수동 정지)
        try:
            after = json.loads(after_value) if after_value else None
        except (ValueError, TypeError):
            after = None
        if not isinstance(after, dict) or after.get("userLock") is not True:
            continue  # 방향 판별 불가(fail-closed) 또는 최신 잠금변경이 정지가 아님(재개로 끝남)
        pause_date = paused_at.date()
        window_to = pause_date - timedelta(days=1)
        window_from = window_to - timedelta(days=LOW_CLICK_LOOKBACK_DAYS - 1)
        agg = keyword_window_agg(db, keyword_id, window_from, window_to)
        if agg["cost"] == 0:
            continue  # 정지 직전 창에 실적 자체가 없음 — 판정 불가
        roas_naver = float(Decimal(agg["conv_amt"]) / Decimal(agg["cost"]))
        roas_c = _corrected_roas(roas_naver, correction_factor)
        target_roas = target_roas_resolver(campaign_id)
        if roas_c is not None and roas_c >= float(target_roas):
            out.append({
                "campaign_id": campaign_id, "adgroup_id": off_entities[keyword_id].parent_id,
                "keyword_id": keyword_id, "roas_at_pause": roas_c, "paused_at": paused_at.isoformat(),
            })
    out.sort(key=lambda x: x["roas_at_pause"], reverse=True)
    return out


def shopping_pause_candidates(
    db: Session, date_from: date, date_to: date,
    bep_roas: Decimal | None = None, correction_factor: Decimal | None = None,
) -> list[dict]:
    """쇼핑 정지 후보 (X1b-S S1 T2, D-NAO-43) — pause_candidates(WEB_SITE 키워드 전용)의
    SHOPPING adgroup 대칭 확장. 대상=NaverEntity entity_type='adgroup', status='on',
    campaign_type='SHOPPING'이고 부모 캠페인도 on(_on_adgroup_ids 재사용 — 대상 자신이 이미
    adgroup이라 키워드판의 3단 체인과 달리 캠페인→adgroup 2단만 확인).

    스톱로스 절대액 = 현재입찰가(entity.bid_amt) × LOW_CLICK_THRESHOLD — pause_candidates와
    동일 상수·산식(D-NAO-20). NaverEntity에 bid_amt가 없는(미확보) 그룹은 계산 불가라
    fail-closed 제외.

    ★D-NAO-64(A) 바닥그룹 저ROAS 지혈: 무전환(conv_amt==0)만 잡던 게이트의 사각지대 —
    전환은 조금 있으나 실질ROAS(=보정ROAS)가 BEP를 크게 밑돌고 입찰이 이미 하한(70원)이라
    bid_down(shopping_group_bep 고삐)도 불가한 그룹(맥세이프_MO 실사례: 268K 지출·실질ROAS
    0.07·입찰 50)은 어느 레인도 안 잡았다. bep_roas·correction_factor가 주어지면 그런 그룹을
    후보로 넣되, DL2(아래)부터는 **레버끊김(실측 CPC≫그룹입찰)일 때만** 넣는다(레버 정상이면
    바닥 대기). **입찰 여유가 있으면(하한 초과) 배제** — bid_down이 shopping_group_bep의 몫이라
    여기서도 잡으면 이중 제안. bep 미제공(하위호환 호출)이면 이 경로 휴면(무전환 게이트만 작동).

    ★DL1(D-NAO-65) 창 이원화:
    - **행동 창(스톱로스 비용·무전환 게이트) = 마지막 입찰변경 이후**(변경이력 없으면 3일 폴백)
      — pause_candidates와 동일(D-NAO-63 시점 불일치 소멸).
    - **만성 판정 창(보정ROAS·실측 CPC) = 7일 롤링 soft**(ref 33 [1]) — 단발 나쁜 날의
      일별 분산으로 pause를 결정하지 않도록 명시 분리(과거 전체 창 → 7일 롤링).

    ★DL2(D-NAO-65) 레버끊김 판정기(구 D-NAO-64 floored_loss 진화): 전환 있는 바닥손실은
    이제 '레버끊김'(만성 창 실측 CPC = cost/clk > _LEVER_BROKEN_CPC_MULTIPLE × 그룹입찰)일
    때만 pause 후보로 넣는다 — 레버 정상(실측 CPC ≤ k×입찰)이면 바닥에서 노출~0이라 자연
    지혈되므로 후보 미진입(바닥 대기). 무전환 행에도 lever_broken 플래그를 태깅해
    _stop_loss_proposal(쇼핑 경로)이 at-floor시 pause(예외②) vs 대기를 분기하게 한다.
    reason 값: zero_conv(무전환·행동 창) / lever_broken(전환 있는 레버끊김·만성 창).

    ★B2(D-NAO-65) 실효입찰 재정의: 스톱로스 임계(=입찰×10)·lever_broken(CPC>k×입찰)·at-floor
    판정의 기준 입찰을 **그룹입찰(entity.bid_amt) → 실효입찰(effective_bid SA)**로 치환한다.
    85/88 소재가 useGroupBidAmt=false라 실효입찰≠그룹입찰이 기본 — 그룹입찰 50인데 소재 bidAmt
    1990이면 실효≈CPC라 lever_broken 대부분 자연 소멸(예외② 오판 축소, 완료기준 a). 진짜
    소재-레벨 끊김(소재 bidAmt도 낮은데 CPC 폭등)만 lever_broken 유지(완료기준 b). 소재-레벨
    데이터 없는 그룹(sync 전)은 effective_bid SA가 그룹입찰 폴백 → 종전 동작 보존(하위호환).
    board에 effective_bid 필드 제공(_stop_loss_proposal이 at-floor/사유문에 소비) — current_bid는
    그룹입찰 유지(target_bid 스텝 기준·B2는 소재입찰 안 씀·제어는 B3). 예외② pause 경로 자체는
    B2에서 제거하지 않는다(B3 제어 전 통제불능 갭 방지, §0 금지선)."""
    on_adgroups = _on_adgroup_ids(db)
    entity_map = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(
            NaverEntity.entity_type == "adgroup", NaverEntity.status == "on",
            NaverEntity.campaign_type == _SHOPPING,
        ).all()
    }
    if not entity_map:
        return []

    # 부모 캠페인 on + bid_amt 확보된 그룹만 후보(그 집합으로 창 조회).
    candidate_ids = {
        aid for aid, e in entity_map.items() if e.bid_amt and aid in on_adgroups
    }
    if not candidate_ids:
        return []
    last_change = _last_bid_change_kst_date(db, candidate_ids)
    daily = _daily_cost_conv_by_unit(db, NaverAdDaily.adgroup_id, candidate_ids, _SHOPPING, date_from, date_to)
    chronic_from = date_to - timedelta(days=_CHRONIC_WINDOW_DAYS - 1)  # 7일 롤링 만성 창
    # B2 GATE P3-2: 실효입찰 배치 파생(후보별 1쿼리 N+1 → 전 후보 1쿼리).
    effs = effbid.adgroup_effective_bids(
        db, {aid: entity_map[aid].bid_amt for aid in candidate_ids},
    )
    # B3 이월(D-NAO-65): 미연결 유닛의 증거 창을 '마지막 소재입찰 변경 이후'로 절체하기 위해
    # 그룹별 마지막 ad-레벨 입찰변경일을 미리 집계(B3 실집행 전엔 빈 dict = 종전 만성 7일 창).
    ad_change = _last_ad_bid_change_by_group(db, candidate_ids)

    # D-NAO-64(A): at-floor(못 내림) 판정은 스텝하향의 단일 진실원천 재사용(하드코딩 70 금지,
    # _MAX_CHANGE_PCT 변경 시 자동 정합). 함수 레벨 import로 순환 의존 회피(호출 시점 해석).
    from app.services.naver_ad.proposal_writer import _step_down_bid

    out = []
    for adgroup_id in candidate_ids:
        rows = daily.get(adgroup_id)
        if not rows:
            continue
        entity = entity_map[adgroup_id]
        # B2(D-NAO-65): 실효입찰 파생 — 그룹입찰 대신 소재-레벨 실효입찰을 임계·lever_broken·
        # at-floor 기준으로 쓴다(소재 데이터 없으면 SA가 그룹입찰 폴백 = 종전 동작 보존).
        eff = effs[adgroup_id]
        eff_bid = eff["effective_bid"]
        last_change_date = last_change.get(adgroup_id)
        # B2 GATE P2-1: 레버 미연결 유닛(source='ad' — 소재입찰이 그룹입찰 우회)의 스톱로스
        # 비용 창은 DL1 행동 창(3일 폴백)이 아니라 **만성 7일 창**을 쓴다(침묵 대역 해소).
        # 근거: ①이 유닛들은 우리가 입찰을 안 바꾸므로(그룹 bid_down이 P2-2로 억제됨) DL1의
        # "변경 후 창 절체" 우려가 구조적으로 없음 = 시점 정합 유지 ②3일 폴백이면 일 출혈이
        # 임계/3 미만인 유닛이 영구 침묵(예: 실효 1990 임계 19,900 → 일 6,633원 미만 무감지),
        # 7일이면 일 임계/7 이상 출혈은 도달하고 그 미만은 진짜 "실효입찰 10클릭 증거 부족"
        # (성급 사살 금지가 정당). floor_bleed 밸브의 window_days도 이 창 기준(아래 산출 공유).
        # 레버 연결 유닛(source='group')은 기존 DL1 행동 창 유지(우리가 입찰을 바꾸는 유닛).
        if eff["source"] == "ad":
            # B3 이월: 소재입찰을 우리가 바꿨으면 증거 창을 그 변경일 이후로 절체(만성 7일과
            # 결합 = 더 좁은 창). 변경 없으면(B3 전) 종전 만성 7일 창(max(date_from, chronic_from)).
            ad_change_date = ad_change.get(adgroup_id)
            base_start = max(date_from, chronic_from)
            window_start = max(base_start, ad_change_date) if ad_change_date is not None else base_start
            cpc_change = ad_change_date  # CPC 창도 소재입찰 변경 이후로 절체(변경 전 고CPC 혼입 방지)
        else:
            window_start = _stop_loss_window_start(date_from, date_to, last_change_date)
            cpc_change = last_change_date  # source='group'은 기존 adgroup-레벨 변경일
        _, _, cost, conv_amt = _sum_cost_conv_since(rows, window_start)  # 행동/증거 창
        if cost <= 0:
            continue
        stop_loss_amount = eff_bid * LOW_CLICK_THRESHOLD  # B2: 실효입찰×10(그룹입찰 아님)
        if cost < stop_loss_amount:
            continue
        # 만성 7일 창 집계 — 보정ROAS 판정용(단발 나쁜 날 조기사살 방지, DL1).
        _, _, c_cost, c_conv = _sum_cost_conv_since(rows, chronic_from)
        # DL2(D-NAO-65) 예외② 레버끊김: 실측 CPC(cost/clk)가 그룹입찰의 k배를 초과하면 입찰
        # 레버가 헛도는 소재-레벨 입찰 정황. clk=0이면 CPC 계산 불가 → 판정 불가(False).
        # ★GATE P2-1 시점 정합: CPC는 **만성 창 ∩ 마지막 실입찰변경 이후** 교집합으로 계산 —
        # 만성 창 전체를 쓰면 입찰 하향 전 고CPC 클릭이 혼입돼, 방금 정상 하향된 그룹이
        # "레버 끊김"으로 오판·pause된다(스톱로스 비용 창을 변경 이후로 절체한 DL1과 동일 근거).
        # 변경 이력 없으면 만성 창 그대로(혼입 원인인 '과거 다른 입찰 구간' 자체가 없음 —
        # 폴백 3일로 좁히면 진짜 레버끊김의 표본만 줄어 검출력 손실). 교집합에 클릭 0이면
        # CPC None = 판정 불가 → lever_broken False(fail-safe). 보정ROAS는 만성 창 유지(목적이
        # 다름 — 단발 나쁜 날 방지). 모든 행에 lever_broken을 태깅해 _stop_loss_proposal이
        # at-floor시 pause(예외②) vs 바닥 대기를 분기하게 한다(무전환 행 포함).
        # B3 이월: source='ad'는 소재입찰 변경(cpc_change), source='group'은 adgroup 변경 이후로
        # CPC 창을 절체(위 window 분기에서 cpc_change 확정 — 변경 전 고CPC 클릭 혼입 방지).
        cpc_from = chronic_from if cpc_change is None else max(chronic_from, cpc_change)
        _, cpc_clk, cpc_cost, _ = _sum_cost_conv_since(rows, cpc_from)
        chronic_cpc = int(cpc_cost // cpc_clk) if cpc_clk > 0 else None
        # B2: 레버끊김 판정 기준 = 실효입찰(그룹입찰 아님). 실효≈CPC면 CPC≤k×실효라 미발동
        # (예외② 오판 소멸) — 진짜 소재-레벨 끊김(소재 bidAmt도 낮은데 CPC 폭등)만 유지.
        lever_broken = (
            chronic_cpc is not None and chronic_cpc > _LEVER_BROKEN_CPC_MULTIPLE * eff_bid
        )
        # GATE P2: 행동 창 2일 미만(마지막 실변경=date_to 당일)이면 zero_conv 판정 보류 —
        # 간접전환 ~1일 정착·쇼핑 ML그룹은 터미널 pause라 오발동=실손실. lever_broken(만성
        # 7일 창) 경로는 창이 길어 정착 완충이 있으므로 이 보류와 무관.
        # (GATE-2R P3: 술어는 변경일 기준 — pause_candidates와 동일 근거)
        hold_zero_conv = last_change_date is not None and last_change_date >= date_to
        reason: str | None = None
        if conv_amt == 0:
            if hold_zero_conv:
                continue  # 변경 당일 하루 창 — 귀속 지연 완충 후 다음날 정상 판정
            reason = "zero_conv"  # 기존 무전환 스톱로스(행동 창 기준)
        elif bep_roas is not None and correction_factor is not None:
            # 전환 있음 + 실질ROAS ≪ BEP + 입찰 바닥(못 내림) + **레버끊김** = 지혈 불능 출혈 →
            # 터미널 pause(예외②). DL2: 레버 정상(실측 CPC≤k×입찰)이면 후보 미진입 = 바닥 대기
            # (바닥 노출~0이라 자연 지혈). ★보정ROAS는 7일 롤링 만성 창으로 판정.
            roas_naver = float((Decimal(c_conv) / Decimal(c_cost)).quantize(_Q4)) if c_cost else None
            roas_c = _corrected_roas(roas_naver, correction_factor)
            at_floor = _step_down_bid(eff_bid) >= eff_bid  # B2: at-floor도 실효입찰 기준
            if roas_c is not None and roas_c < float(bep_roas) and at_floor and lever_broken:
                reason = "lever_broken"
        if reason is None:
            continue
        # DL2 GATE P2-2 지속 밸브: 무전환(zero_conv) 유닛의 행동 창이 ≥3일이면 floor_bleed
        # 태깅(비용≥스톱로스는 이미 후보 게이트) — writer가 at-floor시 대기 대신 밸브 pause.
        # at-floor 판정은 writer의 _step_down_bid(단일 진실 소스) 몫이라 여기선 기간만 본다.
        window_days = (date_to - window_start).days + 1
        out.append({
            "campaign_id": entity.campaign_id, "adgroup_id": adgroup_id, "cost": cost,
            "conv_amt": conv_amt, "current_bid": entity.bid_amt,
            # B2: 실효입찰(그룹입찰과 다를 수 있음) — writer가 at-floor/레버연결·사유문에 소비.
            # B3: effective_ad_id = 실효값을 만든 소재 nccAdId(source='ad'일 때, 소재-레벨 제어 대상).
            "effective_bid": eff_bid, "effective_source": eff["source"],
            "effective_ad_id": eff["max_ad_id"],
            "stop_loss_amount": stop_loss_amount, "reason": reason,
            "lever_broken": lever_broken, "chronic_cpc": chronic_cpc,
            "floor_bleed": reason == "zero_conv" and window_days >= _FLOOR_BLEED_MIN_DAYS,
            "window_days": window_days,
        })
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def shopping_group_growth(
    db: Session, date_from: date, date_to: date, target_roas_resolver, correction_factor: Decimal,
) -> list[dict]:
    """쇼핑검색 그룹(adgroup)별 수익 성장 후보 (X1b-S S3, D-NAO-43 확장) —
    shopping_group_bep(적자, down)의 반대. SHOPPING adgroup(status='on' + 부모 캠페인도 on,
    _on_adgroup_ids 재사용 — shopping_pause/resume_candidates와 동일 부모체인 규율) 중
    보정ROAS ≥ 캠페인별 목표(target_roas_resolver(campaign_id), override>계정기본값)인
    수익 그룹, cost>0.

    starving_winners(account_diagnosis.py 상단, WEB_SITE 키워드판 육성 의도)의 adgroup grain
    대칭이되, 저클릭 필터는 두지 않는다 — "충분히 노출됐는데 저클릭"이라는 판단 자체가
    SHOPPING 그룹 단위엔 성립하지 않고(그룹 안에 여러 상품이 섞여있어 클릭 볼륨 해석이
    다름), 방향(성장 여력 유무)은 bid_simulator의 economic_ceiling 계산에 맡긴다 — 이 보드는
    "수익성 확인된 그룹"까지만 걸러준다.

    entity 조회로 bid_amt(현재 입찰가)를 확보하지 못하면 fail-closed 제외
    (shopping_pause_candidates와 동일 근거 — downstream bid_simulator/실쓰기가 현재
    입찰가를 필요로 함, 값 없이 성장 후보로 올려봐야 실행 불가능한 유령 후보가 된다).
    """
    on_adgroups = _on_adgroup_ids(db)
    entity_map = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(
            NaverEntity.entity_type == "adgroup", NaverEntity.status == "on",
            NaverEntity.campaign_type == _SHOPPING,
        ).all()
    }
    if not entity_map:
        return []

    q = (
        db.query(
            NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.campaign_type == _SHOPPING,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id)
        .all()
    )
    out = []
    for campaign_id, adgroup_id, cost, conv_direct, conv_indirect, clk in q:
        cost = int(cost)
        if cost <= 0:
            continue
        entity = entity_map.get(adgroup_id)
        if entity is None or not entity.bid_amt:
            continue
        if adgroup_id not in on_adgroups:
            continue
        conv_amt = int(conv_direct) + int(conv_indirect)
        roas_naver = float((Decimal(conv_amt) / Decimal(cost)).quantize(_Q4))
        roas_c = _corrected_roas(roas_naver, correction_factor)
        target_roas = target_roas_resolver(campaign_id)
        if roas_c is not None and target_roas is not None and roas_c >= float(target_roas):
            # clk 포함(codex[P2]): compute_bid_sims가 row["clk"]로 이 그룹 자신의 RPC
            # (conv_amt/clk)를 계산 — 없으면 clk=0 폴백으로 캠페인 평균 RPC가 쓰여 이 그룹
            # 경제성과 안 맞는 target_bid가 추천될 수 있다(성장=up이라 특히 중요).
            out.append({
                "campaign_id": campaign_id, "adgroup_id": adgroup_id, "cost": cost,
                "conv_amt": conv_amt, "clk": int(clk),
                "roas_naver": roas_naver, "roas_corrected": roas_c,
            })
    out.sort(key=lambda x: x["roas_corrected"], reverse=True)
    return out


def adgroup_window_agg(db: Session, adgroup_id: str, date_from: date, date_to: date) -> dict:
    """단일 광고그룹의 창 내 (cost, conv_amt, conv_cnt) 집계 — keyword_window_agg의 adgroup
    grain 공개 대칭판(P2/S3, D-NAO-42-f PLAN §3 S2/S3 스코프 예고 실현). naver_execution_harness
    (S3 adgroup up 가드레일 컨텍스트)가 소비한다.

    campaign_type 필터를 걸지 않는다 — adgroup_id 자체가 이미 특정 campaign_type에 속한
    그룹 하나를 유일하게 가리키므로(현재 유일한 소비처는 SHOPPING adgroup up이지만, 필터를
    걸지 않는 편이 campaign_window_agg와 동일한 "그레인이 이미 스코프를 결정한다" 원칙에
    맞다). S1b의 private `_shopping_adgroup_window_agg`(SHOPPING 필터 포함,
    shopping_resume_candidates 전용)는 그대로 유지 — 이 함수가 대체하지 않는다(기존
    호출부 회귀 방지, 별도 유지보수 부담이 크지 않음).
    """
    cost, direct, indirect, direct_cnt, indirect_cnt = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_cnt), 0),
    ).filter(
        NaverAdDaily.adgroup_id == adgroup_id,
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).one()
    return {
        "cost": int(cost), "conv_amt": int(direct) + int(indirect),
        "conv_cnt": int(direct_cnt) + int(indirect_cnt),
    }


def _shopping_adgroup_window_agg(db: Session, adgroup_id: str, date_from: date, date_to: date) -> dict:
    """단일 쇼핑 광고그룹의 창 내 (cost, conv_amt) 집계 — keyword_window_agg의 adgroup·
    SHOPPING grain 판(shopping_resume_candidates 전용, X1b-S S1). 캠페인 무관 재사용
    가능한 공개 adgroup_window_agg(PLAN §3 S2/S3 스코프)는 별도 서브스프린트에서 도입."""
    cost, direct, indirect = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.adgroup_id == adgroup_id,
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.campaign_type == _SHOPPING,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).one()
    return {"cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def shopping_resume_candidates(
    db: Session, date_to: date, target_roas_resolver, correction_factor: Decimal,
) -> list[dict]:
    """쇼핑 재개 후보 (X1b-S S1 T2, D-NAO-43) — resume_candidates(WEB_SITE 키워드 전용)의
    SHOPPING adgroup 대칭 확장. 우리 시스템이 정지시킨(change_log proposal_id 있음) 광고그룹
    중 정지 직전 30일 창의 보정ROAS가 현재 목표(캠페인별 override > 계정기본값) 이상 —
    D-NAO-16 "정지 사유 해소" 신호.

    D-NAO-40 안전판별을 entity_type='adgroup'으로 그대로 계승: 최신 잠금변경(action이
    set_user_lock 또는 entity_sync의 external_status_change)이 우리 정지(proposal_id 존재 +
    after_value.userLock=True)일 때만 후보 — 수동/외부 재정지가 최신이면 제외(resume_candidates
    문서 참조, 의미상 동일 판별 로직)."""
    off_entities = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(
            NaverEntity.entity_type == "adgroup", NaverEntity.status == "off",
            NaverEntity.campaign_type == _SHOPPING,
        ).all()
    }
    if not off_entities:
        return []
    off_ids = set(off_entities)

    lock_rows = (
        db.query(
            NaverChangeLog.entity_id, NaverChangeLog.campaign_id, NaverChangeLog.after_value,
            NaverChangeLog.proposal_id, NaverChangeLog.changed_at,
        )
        .filter(
            NaverChangeLog.entity_type == "adgroup",
            NaverChangeLog.action.in_(["set_user_lock", "external_status_change"]),
            NaverChangeLog.entity_id.in_(off_ids),
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    latest_lock_change: dict[str, tuple] = {}
    for entity_id, campaign_id, after_value, proposal_id, changed_at in lock_rows:
        latest_lock_change[entity_id] = (campaign_id, after_value, proposal_id, changed_at)  # asc 정렬 — 뒤에 올수록 최신

    out = []
    for adgroup_id, (campaign_id, after_value, proposal_id, paused_at) in latest_lock_change.items():
        if proposal_id is None:
            continue  # 최신 잠금변경이 우리 실행이 아님(수동 정지)
        try:
            after = json.loads(after_value) if after_value else None
        except (ValueError, TypeError):
            after = None
        if not isinstance(after, dict) or after.get("userLock") is not True:
            continue  # 방향 판별 불가(fail-closed) 또는 최신 잠금변경이 정지가 아님
        pause_date = paused_at.date()
        window_to = pause_date - timedelta(days=1)
        window_from = window_to - timedelta(days=LOW_CLICK_LOOKBACK_DAYS - 1)
        agg = _shopping_adgroup_window_agg(db, adgroup_id, window_from, window_to)
        if agg["cost"] == 0:
            continue  # 정지 직전 창에 실적 자체가 없음 — 판정 불가
        roas_naver = float(Decimal(agg["conv_amt"]) / Decimal(agg["cost"]))
        roas_c = _corrected_roas(roas_naver, correction_factor)
        target_roas = target_roas_resolver(campaign_id)
        if roas_c is not None and roas_c >= float(target_roas):
            out.append({
                "campaign_id": campaign_id, "adgroup_id": adgroup_id,
                "roas_at_pause": roas_c, "paused_at": paused_at.isoformat(),
            })
    out.sort(key=lambda x: x["roas_at_pause"], reverse=True)
    return out


def _our_paused_shopping_groups(db: Session) -> dict[str, tuple[str, datetime, bool]]:
    """우리가 정지시킨 SHOPPING 광고그룹(off) → {adgroup_id: (campaign_id, paused_at,
    had_prior_resume)}.

    shopping_resume_candidates와 동일한 D-NAO-40 안전판별을 계승한다 — 최신 잠금변경
    (set_user_lock/external_status_change 중 changed_at 최신)이 우리 실행(proposal_id 존재)
    이고 after_value.userLock=True(정지)일 때만 포함. 수동/외부 재정지가 최신이거나 방향
    판별 불가(비-dict/파싱 실패)면 제외(fail-closed). had_prior_resume = 최신 pause 이전에
    재개(userLock=False) 이력이 있었는지(B4 GATE P2-3① flip-flop 쿨다운의 원료 — "재pause"
    판별). shopping_resume_candidates와 이 헬퍼를 공유하지 않는 이유: 그 함수는 정지 직전
    ROAS 회복 게이트가 목적이라 창 조회까지 하나로 묶여 있고, B4 레버끊김 재개는 게이트
    (바닥 재개)가 근본적으로 다르다(그 함수를 확장하면 두 목적이 한 루프에 얽혀 회귀 위험)."""
    off_ids = {
        e.entity_id for e in
        db.query(NaverEntity.entity_id).filter(
            NaverEntity.entity_type == "adgroup", NaverEntity.status == "off",
            NaverEntity.campaign_type == _SHOPPING,
        ).all()
    }
    if not off_ids:
        return {}
    lock_rows = (
        db.query(
            NaverChangeLog.entity_id, NaverChangeLog.campaign_id, NaverChangeLog.after_value,
            NaverChangeLog.proposal_id, NaverChangeLog.changed_at,
        )
        .filter(
            NaverChangeLog.entity_type == "adgroup",
            NaverChangeLog.action.in_(["set_user_lock", "external_status_change"]),
            NaverChangeLog.entity_id.in_(off_ids),
            NaverChangeLog.dry_run.is_(False), NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at.isnot(None),  # paused_at.isoformat() 크래시 방지(정렬 안정)
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    def _locked_of(after_value) -> bool | None:
        try:
            after = json.loads(after_value) if after_value else None
        except (ValueError, TypeError):
            return None
        if not isinstance(after, dict):
            return None
        lock = after.get("userLock")
        return lock if isinstance(lock, bool) else None

    latest: dict[str, tuple] = {}
    resume_seen: dict[str, bool] = {}  # 재개(userLock=False) 이력 존재 여부 — 최종 행이
    # 정지(True)일 때만 소비되므로(아래 게이트) 재개 행은 항상 그 최종 정지보다 앞선다
    # (asc 순회) = "선행 재개" 판별로 충분.
    for entity_id, campaign_id, after_value, proposal_id, changed_at in lock_rows:
        if _locked_of(after_value) is False:
            resume_seen[entity_id] = True
        latest[entity_id] = (campaign_id, after_value, proposal_id, changed_at)  # asc — 뒤=최신
    out: dict[str, tuple[str, datetime, bool]] = {}
    for adgroup_id, (campaign_id, after_value, proposal_id, changed_at) in latest.items():
        if proposal_id is None:
            continue  # 최신 잠금변경이 우리 실행 아님(수동 정지)
        if _locked_of(after_value) is not True:
            continue  # 방향 판별 불가 또는 최신이 재개(정지 아님)
        out[adgroup_id] = (campaign_id, changed_at, resume_seen.get(adgroup_id, False))
    return out


def shopping_lever_resume_candidates(db: Session, *, date_to: date) -> list[dict]:
    """레버끊김 재개 후보 (B4, D-NAO-65 설계질문 5 — GATE P2-1 "바닥에서 재개") — 우리가
    정지시킨 쇼핑 그룹(off) 중 소재-레벨 실효 레버가 확보된(effective_source='ad') 그룹을
    소재 실효입찰과 **입찰 바닥(_MIN_BID 70, guardrail 규약 재사용)**의 비교로 분기한다.
    DL2 예외②를 pause에서 leash로 되돌리는 재개 배선.

    분기(action):
    - 'bid_down_first': 소재 실효입찰 > 바닥 → 재개 전 소재입찰을 바닥까지 단계 하향
      (800원 그대로 재개하면 재출혈 — item 4·5). effective_ad_id로 소재-레벨 bid_down.
    - 'resume': 소재 실효입찰 ≤ 바닥 → 재개 준비 완료.

    ★재개 = 최소 노출로 증거 축적 재개일 뿐이다(GATE P2-1 원리): 바닥(70원)에선 노출~0이라
    재출혈 상한 ≈ 바닥 노출 수준. 올리는 건 이 보드가 아니라 DL4 밴드 재시작의 BEP 게이트가
    **정착창 ROAS 실증과 함께** 수행한다(D-NAO-59 정합 — 증거 없이 올리지 않는다). 이전
    설계의 "안전 CPC = 판매가/target_roas"는 CVR=1 상한이라 실질 무필터였고(MO 실사례: 소재
    800 ≤ 8,711 즉시 통과 — 실측 CVR 0.9% 기준 현실 BEP CPC ~78원의 10배) 무필터를 필터인
    척 두지 않기 위해 제거했다(정직 경계).

    ★GATE P2-3① flip-flop 쿨다운: pause→resume→재pause 이력(had_prior_resume)이 있고 그
    재pause가 최근 _LEVER_RESUME_COOLDOWN_DAYS(3일, D-NAO-19 계승) 내면 제외 — 재개하자마자
    다시 pause된 그룹을 즉시 또 재개 제안하는 진동 차단. 최초 pause(선행 재개 없음)는
    쿨다운 미적용(첫 재개 기회를 늦출 이유 없음).

    '레버끊김'의 조작적 정의 = effective_source='ad'(소재 bidAmt가 그룹입찰을 우회 = DL2가
    lever_broken으로 pause하던 바로 그 부류의 실효레버 확보 상태) — 전역 규칙이라 per-campaign
    하드코딩 없음(§0). 카나리 스코프·순서강제(같은 그룹에 ad bid_down pending 공존 시 resume
    금지)·ML 사전 제외·ours 필터는 proposal_writer.build()가 적용한다 — 이 SA는 canary/
    pending/optimizer/API를 모른다(단일책임, 원칙18)."""
    # 함수 레벨 import — guardrail_gate와의 모듈 결합 최소화(proposal_writer._step_down_bid
    # 함수 레벨 import와 동일 관례). _MIN_BID=70(절대 하한)의 단일 진실 소스 재사용.
    from app.services.naver_ad.guardrail_gate import _MIN_BID

    paused = _our_paused_shopping_groups(db)
    if not paused:
        return []
    entity_bids = dict(
        db.query(NaverEntity.entity_id, NaverEntity.bid_amt).filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.entity_id.in_(set(paused)),
        ).all()
    )
    group_bids = {aid: bid for aid, bid in entity_bids.items() if bid}
    effs = effbid.adgroup_effective_bids(db, group_bids)

    out = []
    for adgroup_id, (campaign_id, paused_at, had_prior_resume) in paused.items():
        eff = effs.get(adgroup_id)
        # 레버 미확보(그룹입찰 폴백·소재 데이터 없음)면 소재-레벨 재개 대상 아님(B4 스코프 밖).
        if eff is None or eff["source"] != "ad" or not eff.get("max_ad_id"):
            continue
        # GATE P2-3① flip-flop 쿨다운: 재pause(선행 재개 이력 존재) 후 3일 내면 제외.
        if had_prior_resume and (date_to - paused_at.date()).days < _LEVER_RESUME_COOLDOWN_DAYS:
            continue
        eff_bid = eff["effective_bid"]
        action = "resume" if eff_bid <= _MIN_BID else "bid_down_first"
        out.append({
            "campaign_id": campaign_id, "adgroup_id": adgroup_id,
            "effective_bid": eff_bid, "effective_ad_id": eff["max_ad_id"],
            "action": action, "paused_at": paused_at.isoformat(),
        })
    out.sort(key=lambda x: x["effective_bid"], reverse=True)
    return out
