# creative_scorecard.py — 소재(광고)별 성과 조회 (D-NAO-140 S2). 읽기 전용.
"""역할: `naver_ad_creative_daily`를 구간 합산해 **소재별 ROAS를 BEP와 나란히** 준다.

══ 이 화면이 없으면 안 보이는 것 ══
캠페인 평균이 적자 소재를 가린다. 2026-08-03 실측: 캠페인 03의 ROAS는 2.07~3.26인데 그 안의
소재 `…373441859`는 **0.61**이었다(3일 10.4만원 써서 6.4만원). 그룹까지만 보던 동안은
이 소재가 존재조차 드러나지 않았다.

══ 판정은 BEP 대비로만 한다 ══
ROAS 숫자 자체는 상품마다 의미가 다르다(공헌이익률이 다르므로). 그래서 `roas - bep_roas`로
말한다. **BEP를 모르면 판정하지 않는다** — 모르는 걸 "미달"로 적으면 매핑 결손이 적자로 둔갑한다.

★읽기 전용이다. 이 모듈을 읽고 광고를 조작하는 경로는 없다(D-NAO-140 계약 금지선).
★단일 날짜로 판정하지 않는 게 기본이다: 소재당 전환이 하루 0~3건이라 하루치 ROAS는
  노이즈가 신호보다 크다. 그래서 구간 합산을 기본으로 준다.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    NaverAdCreativeDaily,
    NaverAdgroupProduct,
    NaverEntity,
    NaverProductBep,
)

NAVER_CHANNEL_ID = 6

# 정렬 축 — 화면이 임의 문자열을 넘기지 못하게 서버가 목록을 쥔다.
SORTS = {
    "cost": NaverAdCreativeDaily.cost,
    "imp": NaverAdCreativeDaily.imp,
    "clk": NaverAdCreativeDaily.clk,
}


def _product_map(db: Session, ad_ids: list[str]) -> dict[str, dict]:
    """소재 → {상품번호, 상품명, 현재 입찰, BEP}.

    ★같은 `ad_id`가 옛 그룹·새 그룹 두 행에 존재할 수 있다(unique 키가 (adgroup, mall_product)뿐).
    `bid_ceiling_calculator`와 **같은 규칙**으로 최신 관측을 채택한다 — 조회 화면과 실행 경로가
    서로 다른 상품을 가리키면 그 화면을 보고 내린 판단이 어긋난다.
    """
    if not ad_ids:
        return {}
    out: dict[str, dict] = {}
    rows = (
        db.query(NaverAdgroupProduct, NaverProductBep)
        .outerjoin(
            NaverProductBep,
            (NaverProductBep.channel_product_id == NaverAdgroupProduct.mall_product_id)
            & (NaverProductBep.channel_id == NAVER_CHANNEL_ID),
        )
        .filter(NaverAdgroupProduct.ad_id.in_(ad_ids))
        .order_by(NaverAdgroupProduct.synced_at.desc(), NaverAdgroupProduct.id.desc())
        .all()
    )
    for ap, bep in rows:
        if ap.ad_id in out:  # 최신 관측이 먼저 온다 — 뒤엣것은 옛 행
            continue
        out[ap.ad_id] = {
            "mall_product_id": ap.mall_product_id,
            "product_name": ap.product_name or "",
            "bid_amt": ap.ad_bid_amt,
            "use_group_bid_amt": ap.use_group_bid_amt,
            "bep_roas": float(bep.bep_roas) if bep is not None and bep.bep_roas is not None else None,
        }
    return out


def build(
    db: Session,
    *,
    since: date,
    until: date,
    campaign_id: str | None = None,
    sort: str = "cost",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """구간 합산 소재 성과. 반환: {window, total, totals, rows}.

    `totals`는 **필터·페이지와 무관한 구간 전체 합계**다 — 한 페이지만 보고 "이게 전부"라고
    읽지 않게 한다.
    """
    agg = (
        db.query(
            NaverAdCreativeDaily.ad_id,
            func.max(NaverAdCreativeDaily.campaign_id).label("campaign_id"),
            func.max(NaverAdCreativeDaily.adgroup_id).label("adgroup_id"),
            func.sum(NaverAdCreativeDaily.imp).label("imp"),
            func.sum(NaverAdCreativeDaily.clk).label("clk"),
            func.sum(NaverAdCreativeDaily.cost).label("cost"),
            func.sum(
                NaverAdCreativeDaily.conv_direct_cnt + NaverAdCreativeDaily.conv_indirect_cnt
            ).label("conv"),
            func.sum(
                NaverAdCreativeDaily.conv_direct_amt + NaverAdCreativeDaily.conv_indirect_amt
            ).label("rev"),
            func.count().label("days"),
        )
        .filter(NaverAdCreativeDaily.ad_date >= since, NaverAdCreativeDaily.ad_date <= until)
        .group_by(NaverAdCreativeDaily.ad_id)
    )
    if campaign_id:
        agg = agg.filter(NaverAdCreativeDaily.campaign_id == campaign_id)

    all_rows = agg.all()
    totals = {
        "ads": len(all_rows),
        "cost": sum(int(r.cost or 0) for r in all_rows),
        "rev": sum(int(r.rev or 0) for r in all_rows),
        "clk": sum(int(r.clk or 0) for r in all_rows),
    }
    key = {"cost": lambda r: int(r.cost or 0), "imp": lambda r: int(r.imp or 0),
           "clk": lambda r: int(r.clk or 0)}.get(sort, lambda r: int(r.cost or 0))
    all_rows.sort(key=key, reverse=True)
    page = all_rows[offset : offset + limit]

    products = _product_map(db, [r.ad_id for r in page])
    names = _entity_names(db, page)

    rows = []
    for r in page:
        cost, rev = int(r.cost or 0), int(r.rev or 0)
        p = products.get(r.ad_id, {})
        bep = p.get("bep_roas")
        roas = round(rev / cost, 4) if cost else None
        # ★BEP를 모르면 판정하지 않는다(None). "미달"로 적으면 매핑 결손이 적자로 둔갑한다.
        gap = round(roas - bep, 4) if (roas is not None and bep) else None
        rows.append({
            "ad_id": r.ad_id,
            "campaign_id": r.campaign_id,
            "campaign_name": names.get(("campaign", r.campaign_id)),
            "adgroup_id": r.adgroup_id,
            "adgroup_name": names.get(("adgroup", r.adgroup_id)),
            "mall_product_id": p.get("mall_product_id"),
            "product_name": p.get("product_name") or None,
            "bid_amt": p.get("bid_amt"),
            "use_group_bid_amt": p.get("use_group_bid_amt"),
            "days": int(r.days or 0),
            "imp": int(r.imp or 0),
            "clk": int(r.clk or 0),
            "cost": cost,
            "conv": int(r.conv or 0),
            "rev": rev,
            "roas": roas,
            "bep_roas": bep,
            "bep_gap": gap,
            # 판정 3상태: 넘음/미달/판정불가. bool 두 개로 쪼개면 "모름"이 사라진다.
            "verdict": None if gap is None else ("above" if gap >= 0 else "below"),
        })

    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "total": len(all_rows),
        "totals": totals,
        "rows": rows,
    }


def _entity_names(db: Session, page) -> dict[tuple[str, str], str]:
    """캠페인·광고그룹 이름 해석. 없으면 키를 안 넣는다 — 화면이 ID 폴백을 스스로 정한다
    (D-NAO-103① ID 폴백 금지: 이름 자리에 ID를 넣으면 '이름이 있다'로 읽힌다)."""
    ids = {("campaign", r.campaign_id) for r in page if r.campaign_id}
    ids |= {("adgroup", r.adgroup_id) for r in page if r.adgroup_id}
    if not ids:
        return {}
    rows = (
        db.query(NaverEntity.entity_type, NaverEntity.entity_id, NaverEntity.name)
        .filter(NaverEntity.entity_id.in_({i for _, i in ids}))
        .all()
    )
    return {(t, i): n for t, i, n in rows if n and n != i}
