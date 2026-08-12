# search_term_exclusion_list.py — 검색어 제외 «후보 리스트» 생성기 (D-NAO-173 P1-②,
#   docs/PLAN_search-term-exclusion-list.md)
#
# 역할(순수 SA·read-only): naver_search_term_daily 30일 누적을 **연속 ROAS**로 재고, 상품별 BEP를
#   기준선으로 붙여 «이번 주 자를 것 + 왜 + 어떻게 되돌리나»를 만든다. DB 쓰기 0·네이버 API 0.
#   실행은 사람이 콘솔에서 한다 — 이 모듈은 후보만 만든다(PLAN §3 금지선: 자동 제외 실행 금지).
#
# ★기존 search_term_judge.py와 무엇이 다른가(왜 새 모듈인가):
#   judge는 «전환 유무» 이진 게이트다 — ROAS 0.08과 1.65를 같은 칸에 넣고, 전환이 1건이라도
#   있으면 아무리 밑져도 보호한다. 그 판정은 **자동 발사(PX 파워링크)**의 봉투라서 그렇게
#   보수적인 게 맞다. 이쪽은 **사람이 보는 리스트**라 기준이 다르다: 연속 ROAS로 재고,
#   덜 잘라도 되는 대신 **왜 그렇게 봤는지가 전부 보여야** 한다. 두 판정을 한 함수로 합치면
#   자동 발사 봉투가 리스트 편의를 따라 헐거워진다 — 그래서 judge는 한 줄도 건드리지 않는다.
#
# ★이 모듈이 «침묵하지 않기» 위해 지키는 규칙:
#   후보에서 빠진 것은 전부 **버킷으로 세어 내보낸다**(표본 부족·BEP 미확인·파워링크 판정 불가·
#   성숙도 미달·상한 초과). 빠진 것이 안 보이면 fail-closed는 그냥 «안 보이는 손실»이 된다.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverProductBep, NaverSearchTermDaily, NaverSearchTermExclusion
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.exclusion_survival import REVERT_HOWTO
from app.services.naver_ad.search_term_judge import (
    _SHOPPING_SOURCE,
    _build_whitelist,
    _is_whitelisted,
)
from app.utils.kst import kst_now

# ── 창 ────────────────────────────────────────────────────────────────
WINDOW_DAYS = 30
# 전환 성숙도 컷오프 — 최근 3일은 판정에서 뺀다.
# 근거(2026-08-11 실측): 기록된 사실은 «D+1 이후 전환 불변, 재수집은 D+3까지»이고, 일자별 ROAS를
#   훑어도 오래된 날이 체계적으로 높지 않다(D+1 2.28 / D+3 1.93 / D+14 1.35). 다만
#   naver_search_term_daily는 upsert라 **수집 시점별 이력이 없어** 성숙 곡선 자체를 그릴 수 없다
#   — 곡선을 못 그리면 기록된 재수집 상한(D+3)에 맞추는 것이 유일하게 정직한 선택이다.
# ★이 3일이 «없는 셈»이 되지 않도록 그 구간의 비용을 따로 세어 화면에 보낸다(PLAN §5 4).
MATURITY_LAG_DAYS = 3

# ── 표본 게이트 ───────────────────────────────────────────────────────
# 클릭 하한 — SS 게이트(_SS_MIN_CLICK)와 같은 값. 근거가 같다: 클릭이 이보다 적으면 «전환이 안
# 났다»가 그 검색어의 성질인지 표본 부족인지 못 가른다.
MIN_CLICK = 10
# 라운드 상한 — 한 번에 자를 수 있는 최대 건수(PLAN §2 4·§3). 넘친 것은 버려지지 않고
# `capped_out`으로 세어 내보낸다(조용한 절단 금지).
DEFAULT_ROUND_CAP = 50

_UNDECIDABLE_NO_BEP = "bep_unknown"
_UNDECIDABLE_POWERLINK = "powerlink_no_conversion"


def _bep_for_adgroups(db: Session, adgroup_ids: set[str]) -> dict[str, dict]:
    """광고그룹 → {bep_roas, min_cost, product_count, products}. 매핑 없으면 키 자체가 없다.

    ★상품이 2개 이상 붙은 그룹(실측 93개·광고비 620만원 = 40%)의 처리가 이 함수의 핵심 결정이다:
      - **BEP는 그중 가장 낮은 값**을 쓴다 — 가장 넘기 쉬운 기준을 통과하면 자르지 않는다.
      - **비용 하한은 가장 큰 공헌이익**을 쓴다 — 문턱이 높을수록 후보가 적다.
      둘 다 «덜 자르는 쪽»으로 기울인 것이고, 그게 계약 판단기준 7(fail-closed)의 적용이다.
      잘못 자르면 매출이 사라지고, 안 자르면 지금까지의 손실이 유지될 뿐이다 — 손해가 비대칭이다.

    has_cost=True ∧ bep_roas IS NOT NULL 상품만 본다(원가 미확인 상품은 추정 없이 자연히 빠진다).
    """
    if not adgroup_ids:
        return {}
    rows = (
        db.query(
            NaverAdgroupProduct.adgroup_id,
            NaverProductBep.channel_product_id,
            NaverProductBep.product_name,
            NaverProductBep.bep_roas,
            NaverProductBep.contribution_margin,
        )
        .join(
            NaverProductBep,
            NaverProductBep.channel_product_id == NaverAdgroupProduct.mall_product_id,
        )
        .filter(
            NaverAdgroupProduct.adgroup_id.in_(adgroup_ids),
            NaverProductBep.has_cost.is_(True),
            NaverProductBep.bep_roas.isnot(None),
        )
        .all()
    )
    out: dict[str, dict] = {}
    for adgroup_id, cpid, pname, bep, margin in rows:
        slot = out.setdefault(
            adgroup_id, {"bep_roas": None, "min_cost": Decimal("0"), "products": []}
        )
        bep_d = Decimal(str(bep))
        margin_d = Decimal(str(margin or 0))
        slot["bep_roas"] = bep_d if slot["bep_roas"] is None else min(slot["bep_roas"], bep_d)
        slot["min_cost"] = max(slot["min_cost"], margin_d)
        slot["products"].append({"channel_product_id": cpid, "product_name": pname,
                                 "bep_roas": float(bep_d)})
    for slot in out.values():
        slot["product_count"] = len(slot["products"])
    return out


def _reason(roas: Decimal, bep: Decimal, cost: int, clk: int, conv: int, amt: int,
            product_count: int) -> str:
    multi = (
        f" (그룹에 상품 {product_count}개 — 그중 가장 낮은 BEP를 적용해 덜 자르는 쪽으로 판정)"
        if product_count > 1 else ""
    )
    return (
        f"30일 광고비 {cost:,}원 · 클릭 {clk}회 · 전환 {conv}건 · 전환매출 {amt:,}원 → "
        f"ROAS {roas:.2f} < 손익분기 {bep:.2f}{multi}. "
        f"이 검색어는 클릭이 늘수록 총이익이 줄어든다."
    )


def build_exclusion_list(
    db: Session,
    *,
    now: datetime | None = None,
    window_days: int = WINDOW_DAYS,
    campaign_id: str | None = None,
    round_cap: int = DEFAULT_ROUND_CAP,
    min_click: int = MIN_CLICK,
) -> dict:
    """제외 후보 리스트 1장을 만든다(순수·read-only).

    판정: 쇼핑 검색어의 30일 누적 ROAS(= conv_purchase_amt / cost)가 그 광고그룹의 적용 BEP보다
    낮으면 후보. 광고비 큰 순으로 정렬하고 라운드 상한까지만 «후보»로, 나머지는 세어서 내보낸다.

    후보에서 빠지는 모든 경로는 버킷으로 표면화된다:
      already_excluded        — 우리 장부에 이미 «제외됨»으로 있는 검색어(다른 판정보다 앞선다).
                                창이 30일이라 방금 자른 것도 당분간 계속 뜬다 — 조용히 빼지
                                않고 세어서, 「이미 한 일을 또 하라」는 목록이 되지 않게 한다.
      insufficient_sample     — 클릭 표본 부족(자를 근거가 아니라 표본이 없는 것)
      bep_unknown             — 상품 매핑/BEP 미확인(fail-closed — 기준선 없이 자르지 않는다)
      powerlink_undecidable   — 파워링크. **네이버가 검색어에 전환을 안 준다**(근본 한계, 실측
                                확정). 추정으로 메우지 않고 «판정 불가»로 세워 둔다(PLAN §3).
      profitable              — BEP 이상(자를 이유 없음)
      capped_out              — 상한 초과분(조용히 잘리지 않게 건수·비용을 함께 낸다)
      maturity_excluded       — 최근 MATURITY_LAG_DAYS일(전환이 아직 덜 잡힘)
    """
    now = now or kst_now()
    as_of: date = now.date()
    window_to = as_of - timedelta(days=MATURITY_LAG_DAYS)
    window_from = window_to - timedelta(days=window_days - 1)

    def _agg(date_from: date, date_to: date):
        q = (
            db.query(
                NaverSearchTermDaily.source,
                NaverSearchTermDaily.campaign_id,
                NaverSearchTermDaily.adgroup_id,
                NaverSearchTermDaily.search_term,
                sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.imp), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.clk), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_cnt), 0),
                sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.conv_purchase_amt), 0),
            )
            .filter(
                NaverSearchTermDaily.ad_date >= date_from,
                NaverSearchTermDaily.ad_date <= date_to,
                NaverSearchTermDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
                NaverSearchTermDaily.adgroup_id != "",
            )
        )
        if campaign_id:
            q = q.filter(NaverSearchTermDaily.campaign_id == campaign_id)
        return q.group_by(
            NaverSearchTermDaily.source,
            NaverSearchTermDaily.campaign_id,
            NaverSearchTermDaily.adgroup_id,
            NaverSearchTermDaily.search_term,
        ).all()

    rows = _agg(window_from, window_to)

    adgroup_ids = {r[2] for r in rows if r[0] == _SHOPPING_SOURCE}
    bep_map = _bep_for_adgroups(db, adgroup_ids)
    whitelist = _build_whitelist(db)

    candidates: list[dict] = []
    buckets: dict[str, dict] = {
        "already_excluded": {"terms": 0, "cost": 0},
        "insufficient_sample": {"terms": 0, "cost": 0},
        "bep_unknown": {"terms": 0, "cost": 0},
        "powerlink_undecidable": {"terms": 0, "cost": 0},
        "profitable": {"terms": 0, "cost": 0},
    }
    totals = {"terms": 0, "cost": 0, "conv_amt": 0}

    # ★이미 우리 장부에 «제외됨»으로 있는 검색어 — 후보로 다시 올리지 않는다(D-NAO-176).
    #   실측(2026-08-12 prod): 후보 63건 중 1건이 「골프」였다 — Jino가 8/11에 **직접 자른**
    #   바로 그 검색어가 30일 비용 31,411원을 달고 「자르세요」 목록에 다시 떠 있었다.
    #   창이 30일이라 방금 자른 것은 당분간 계속 뜬다. 43건을 편입하면 그만큼 늘어나고,
    #   그러면 이 목록은 「이미 한 일을 또 하라」고 말하는 목록이 된다.
    #   ⚠️`excluded`만 막는다 — `probation`은 재심사로 **일부러 다시 연** 상태라 후보가 맞고,
    #   `restored`/`void`는 제외 상태가 아니다.
    already_excluded = {
        (r.adgroup_id, r.search_term)
        for r in db.query(
            NaverSearchTermExclusion.adgroup_id, NaverSearchTermExclusion.search_term
        ).filter(NaverSearchTermExclusion.status == "excluded").all()
    }

    for source, cmp_id, adg_id, term, imp, clk, cost, conv, amt in rows:
        imp, clk, cost, conv, amt = int(imp), int(clk), int(cost), int(conv), int(amt)
        if cost <= 0:
            continue  # 비용이 없으면 «자를 것»의 대상이 아니다(노출만 된 검색어)
        totals["terms"] += 1
        totals["cost"] += cost
        totals["conv_amt"] += amt

        if (adg_id, term) in already_excluded:
            # 다른 어떤 판정보다 앞선다 — 이미 잘랐으면 «자를까»는 물을 필요가 없는 질문이다.
            buckets["already_excluded"]["terms"] += 1
            buckets["already_excluded"]["cost"] += cost
            continue

        if source != _SHOPPING_SOURCE:
            # 파워링크 — 네이버 API가 검색어에 전환을 붙이지 않는다. 조용히 빼지 않고 세어 둔다.
            buckets["powerlink_undecidable"]["terms"] += 1
            buckets["powerlink_undecidable"]["cost"] += cost
            continue

        bep_slot = bep_map.get(adg_id)
        if bep_slot is None or bep_slot["bep_roas"] is None:
            buckets["bep_unknown"]["terms"] += 1
            buckets["bep_unknown"]["cost"] += cost
            continue

        bep = bep_slot["bep_roas"]
        min_cost = bep_slot["min_cost"]
        roas = Decimal(amt) / Decimal(cost)
        if roas >= bep:
            buckets["profitable"]["terms"] += 1
            buckets["profitable"]["cost"] += cost
            continue
        if clk < min_click or (min_cost > 0 and Decimal(cost) < min_cost):
            # 표본 부족 — «BEP 미달»이 아니라 «아직 판단할 만큼 안 썼다». 자르지 않는다.
            buckets["insufficient_sample"]["terms"] += 1
            buckets["insufficient_sample"]["cost"] += cost
            continue

        candidates.append({
            "campaign_id": cmp_id,
            "adgroup_id": adg_id,
            "search_term": term,
            "source": source,
            "imp": imp,
            "clk": clk,
            "cost": cost,
            "conv_purchase_cnt": conv,
            "conv_purchase_amt": amt,
            "roas": round(float(roas), 4),
            "applied_bep": round(float(bep), 4),
            "bep_source": "product_bep_min" if bep_slot["product_count"] > 1 else "product_bep",
            "bep_product_count": bep_slot["product_count"],
            "bep_products": bep_slot["products"],
            "min_cost": int(min_cost),
            # 화이트리스트는 여기서 **거르지 않고 표시만** 한다. 자동 발사(judge)에서는 오컷
            # 방지로 거르는 게 맞지만, 사람이 보는 리스트에서 상품 핵심어를 조용히 빼면
            # 광고비가 가장 큰 구간이 통째로 안 보인다(01 지문방지 계열이 정확히 그 모양이다).
            "whitelisted": _is_whitelisted(term, whitelist),
            "reason": _reason(roas, bep, cost, clk, conv, amt, bep_slot["product_count"]),
            "revert_howto": REVERT_HOWTO,
        })

    candidates.sort(key=lambda c: (-c["cost"], c["search_term"]))
    capped_out = candidates[round_cap:]
    candidates = candidates[:round_cap]

    # 성숙도로 잘라낸 최근 구간의 비용 — 「빠진 것이 화면에 보인다」의 근거(PLAN §5 4).
    # ★비용 0인 행은 빼고 센다 — 본 루프가 `cost <= 0`을 대상에서 제외하므로, 여기만 전부 세면
    #   같은 패널에서 그레인이 갈라져 건수가 수십 배로 보인다(실측 37,189 vs 828, 적대 리뷰 P2-1).
    maturity_rows = [r for r in _agg(window_to + timedelta(days=1), as_of) if int(r[6]) > 0]
    maturity_cost = sum(int(r[6]) for r in maturity_rows)

    fresh_q = db.query(
        sqlfunc.max(NaverSearchTermDaily.ad_date),
        sqlfunc.max(NaverSearchTermDaily.synced_at),
    )
    if campaign_id:
        fresh_q = fresh_q.filter(NaverSearchTermDaily.campaign_id == campaign_id)
    latest_ad_date, latest_synced_at = fresh_q.first()

    return {
        "window": {
            "from": window_from.isoformat(),
            "to": window_to.isoformat(),
            "days": window_days,
        },
        "maturity": {
            "lag_days": MATURITY_LAG_DAYS,
            "excluded_from": (window_to + timedelta(days=1)).isoformat(),
            "excluded_to": as_of.isoformat(),
            "excluded_terms": len(maturity_rows),
            "excluded_cost": maturity_cost,
            "why": (
                "최근 3일은 전환이 아직 다 잡히지 않아 판정에서 뺐다"
                "(수집 이력이 upsert라 성숙 곡선 자체를 그릴 수 없어, 기록된 재수집 상한 D+3에 맞춘다)."
            ),
        },
        "freshness": {
            "latest_ad_date": latest_ad_date.isoformat() if latest_ad_date else None,
            "latest_synced_at": latest_synced_at.isoformat() if latest_synced_at else None,
            "as_of": as_of.isoformat(),
            "lag_days": (as_of - latest_ad_date).days if latest_ad_date else None,
        },
        "totals": totals,
        "gates": {"min_click": min_click, "round_cap": round_cap},
        "candidates": candidates,
        "candidate_cost": sum(c["cost"] for c in candidates),
        "buckets": {
            **buckets,
            "capped_out": {
                "terms": len(capped_out),
                "cost": sum(c["cost"] for c in capped_out),
                "why": f"라운드 상한 {round_cap}건을 넘은 후보 — 다음 회차 대상(버려진 것이 아니다)",
            },
            "maturity_excluded": {
                "terms": len(maturity_rows),
                "cost": maturity_cost,
            },
        },
        "revert_howto": REVERT_HOWTO,
        "generated_at": now.isoformat(),
    }
