# account_diagnosis.py — account_diagnosis_sa (쿠팡 판정 이식, P2-S2)
# 역할(SA): naver_ad_daily/naver_entity/naver_search_term_daily만 읽어 6개 진단 보드를 계산.
#   출혈/승자·굶는승자/확장버킷/쇼핑그룹BEP/제외후보/3단분류. 제안 없음(D-3 사실만 정리) —
#   판정만 하고 액션은 P2-S3(bid_simulator·proposal_writer) 소관.
# 판정 기준 이식원(쿠팡 스킬 ohi-ad-learning-loop): BEP 미달=즉시개입 성격의 '출혈',
#   모수 게이트(D-NAO-9 30일 클릭<10='저클릭')는 keyword_volume_sync과 동일 상수 재사용.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverChangeLog, NaverEntity, NaverSearchTermDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

_WEB_SITE = "WEB_SITE"
_SHOPPING = "SHOPPING"

# D-NAO-9/18: keyword_volume_sync과 동일 상수 — 30일 창, 클릭 10미만=판정 불가(저클릭/굶는 상태)
LOW_CLICK_LOOKBACK_DAYS = 30
LOW_CLICK_THRESHOLD = 10

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


def pause_candidates(db: Session, date_from: date, date_to: date) -> list[dict]:
    """정지 후보 (X1b T3, D-NAO-38) — WEB_SITE 등록 키워드(status='on', 부모 체인도 전부 on)
    중 창 내 전환 0건 + 누적비용이 스톱로스 절대액(D-NAO-20) 이상. 스톱로스 절대액 = 현재
    입찰가 × LOW_CLICK_THRESHOLD(growth_sweeper.STOP_LOSS_CLICK_MULTIPLE과 동일 상수,
    D-NAO-9/20 근거 재사용) — "무전환 지출 상한 도달 시 자동 인하/중단"(D-NAO-16)의 "중단"
    쪽 구현. NaverEntity에 bid_amt가 없는(미확보) 키워드는 스톱로스 계산 불가라 fail-closed
    제외. **부모(광고그룹·캠페인)가 off인데 키워드만 on인 경우도 제외**(codex[P2]) — 그
    상태에서 키워드에 별도 정지를 걸면, 나중에 부모만 재개해도 이 키워드는 계속 잠긴 채
    남는다(의도치 않은 영구 정지).
    """
    on_adgroups = _on_adgroup_ids(db)
    entity_map = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword", NaverEntity.status == "on").all()
    }
    if not entity_map:
        return []

    rows = [r for r in _keyword_rows(db, date_from, date_to, _WEB_SITE)
            if r["keyword_id"] and r["cost"] > 0]
    out = []
    for r in rows:
        entity = entity_map.get(r["keyword_id"])
        if entity is None or not entity.bid_amt:
            continue
        if entity.parent_id not in on_adgroups:
            continue
        stop_loss_amount = entity.bid_amt * LOW_CLICK_THRESHOLD
        if r["conv_amt"] == 0 and r["cost"] >= stop_loss_amount:
            out.append({**r, "current_bid": entity.bid_amt, "stop_loss_amount": stop_loss_amount})
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def resume_candidates(
    db: Session, date_to: date, target_roas: Decimal, correction_factor: Decimal,
) -> list[dict]:
    """재개 후보 (X1b T3, D-NAO-38) — 우리 시스템이 정지시킨(change_log action='pause',
    proposal_id 있음 — Jino가 콘솔에서 수동 정지한 경우는 proposal_id가 없어 제외, 우리가
    모르는 이유로 정지된 것을 임의로 재개하지 않는다) 키워드 중 정지 직전 창의 보정ROAS가
    현재 목표(target_roas) 이상 — D-NAO-16 "정지 사유 해소" 중 "BEP 개선"(우리 목표 자체가
    낮아졌거나, 정지 당시 이미 양호했던 키워드) 신호.

    정직 경계: "계절성 회복·CPC 하락"(D-NAO-16 예시의 나머지 2가지)은 미구현 — 정지 중엔
    해당 키워드의 새 실적이 쌓이지 않아 직접 관측 불가하고, 대체 신호(캠페인 CPC 추세 등)는
    이번 스코프에 넣지 않았다(§8 승계 큐 후보로 별도 기록 필요).

    **최신 잠금변경이 우리 정지일 때만 후보**(codex[P2]) — 정지→우리 재개→(수동이든 새
    시스템 정지든)재정지 이력이 있으면 지금 off 상태의 진짜 원인은 그 마지막 이벤트다.
    action='pause'만 보고 max(changed_at)를 구하면 옛 우리 정지를 잘못 채택할 수 있어,
    pause·resume 둘 다 조회해 키워드별 진짜 최신 이벤트를 가린 뒤 그 이벤트가 action='pause'
    +proposal_id 있음일 때만 진행한다.
    """
    off_entities = {
        e.entity_id: e for e in
        db.query(NaverEntity).filter(NaverEntity.entity_type == "keyword", NaverEntity.status == "off").all()
    }
    if not off_entities:
        return []
    off_ids = set(off_entities)

    lock_rows = (
        db.query(
            NaverChangeLog.entity_id, NaverChangeLog.campaign_id, NaverChangeLog.action,
            NaverChangeLog.proposal_id, NaverChangeLog.changed_at,
        )
        .filter(
            NaverChangeLog.entity_type == "keyword", NaverChangeLog.action.in_(("pause", "resume")),
            NaverChangeLog.entity_id.in_(off_ids), NaverChangeLog.outcome == "executed",
        )
        .order_by(NaverChangeLog.changed_at.asc())
        .all()
    )
    latest_lock_change: dict[str, tuple] = {}
    for entity_id, campaign_id, action, proposal_id, changed_at in lock_rows:
        latest_lock_change[entity_id] = (campaign_id, action, proposal_id, changed_at)  # asc 정렬 — 뒤에 올수록 최신

    out = []
    for keyword_id, (campaign_id, action, proposal_id, paused_at) in latest_lock_change.items():
        if action != "pause" or proposal_id is None:
            continue  # 최신 잠금변경이 우리 정지가 아님(수동 재정지·우리 재개로 끝남 등)
        pause_date = paused_at.date()
        window_to = pause_date - timedelta(days=1)
        window_from = window_to - timedelta(days=LOW_CLICK_LOOKBACK_DAYS - 1)
        # codex[P2]: _keyword_rows()는 창 내 WEB_SITE 전 키워드를 GROUP BY로 집계한 뒤 파이썬
        # 필터로 이 keyword_id 하나만 골라낸다 — 후보마다(창이 제각각이라 배치 불가) 이걸
        # 반복하면 O(재개후보 수 × 전체 키워드 행)로 스케일된다. keyword_id를 SQL WHERE에
        # 직접 걸어 이 키워드 행만 집계한다.
        agg_cost, agg_direct, agg_indirect = db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        ).filter(
            NaverAdDaily.keyword_id == keyword_id,
            NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.campaign_type == _WEB_SITE,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        ).one()
        agg_cost, agg_conv = int(agg_cost), int(agg_direct) + int(agg_indirect)
        if agg_cost == 0:
            continue  # 정지 직전 창에 실적 자체가 없음 — 판정 불가
        roas_naver = float(Decimal(agg_conv) / Decimal(agg_cost))
        roas_c = _corrected_roas(roas_naver, correction_factor)
        if roas_c is not None and roas_c >= float(target_roas):
            out.append({
                "campaign_id": campaign_id, "adgroup_id": off_entities[keyword_id].parent_id,
                "keyword_id": keyword_id, "roas_at_pause": roas_c, "paused_at": paused_at.isoformat(),
            })
    out.sort(key=lambda x: x["roas_at_pause"], reverse=True)
    return out
