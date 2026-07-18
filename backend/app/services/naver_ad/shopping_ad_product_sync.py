# shopping_ad_product_sync.py — shopping_ad_product_sync (D-NAO-57 A, 관찰성 sync)
# 역할: optimizer='ours' 쇼핑 캠페인의 활성 광고그룹을 순회 → /ncc/ads의
#   referenceData.mallProductId를 수집 → naver_adgroup_product에 그룹 단위 스냅샷 교체 적재.
#   campaign_target_resolver 우선순위 ②(상품 파생 target_roas)가 이 매핑을 소비한다.
# 순수 수집(collect_) + 쓰기 harness(sync_) 분리 — 관찰만이라 실행 게이트 없음(fail-open은
#   호출 크론이 담당). 매핑은 느리게 변하는 관측치라 일 1회(08:20, 레인·제안 이전) 충분.
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverCampaignSettings, NaverEntity
from app.services.naver_sa_ad_fetcher import get_ads
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _ours_shopping_adgroups(db: Session) -> list[NaverEntity]:
    """optimizer='ours' 캠페인에 속한 활성(status='on') 쇼핑 광고그룹 엔티티.

    NaverEntity(entity_type='adgroup', campaign_type='SHOPPING')를 optimizer='ours'
    campaign_id 집합으로 필터. 매핑 소스는 SHOPPING 캠페인의 그룹뿐이다(파워링크/브랜드검색은
    상품 소재가 아님).
    """
    ours_ids = [
        r[0] for r in db.execute(
            select(NaverCampaignSettings.campaign_id).where(
                NaverCampaignSettings.optimizer == "ours"
            )
        ).all()
    ]
    if not ours_ids:
        return []
    return (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.campaign_type == "SHOPPING",
            NaverEntity.status == "on",
            NaverEntity.campaign_id.in_(ours_ids),
        )
        .all()
    )


def collect_adgroup_products(
    db: Session, *, ads_by_adgroup: dict[str, list[dict]] | None = None
) -> dict[str, list[dict]]:
    """대상 광고그룹별 상품 매핑 dict를 반환: {adgroup_id: [{mall_product_id, product_name, campaign_id}, ...]}.

    ads_by_adgroup는 테스트·재사용 주입용(원칙18-8). 미주입 시 get_ads로 그룹마다 1콜.
    같은 그룹에서 같은 mall_product_id가 여러 소재로 중복되면 dedup(첫 이름 채택).
    개별 그룹의 조회 실패는 그 그룹만 skip(fail-open) — 한 그룹 장애가 전체 sync를 죽이지 않게.
    """
    adgroups = _ours_shopping_adgroups(db)
    result: dict[str, list[dict]] = {}
    for ag in adgroups:
        aid = ag.entity_id
        try:
            ads = (ads_by_adgroup or {}).get(aid) if ads_by_adgroup is not None else get_ads(aid)
        except Exception as e:  # noqa: BLE001 — 그룹 단위 fail-open
            log.warning("shopping_ad_product_sync: 광고그룹 %s 소재 조회 실패(skip): %s", aid, e)
            continue
        seen: set[str] = set()
        rows: list[dict] = []
        for ad in ads or []:
            mall_pid = str(ad.get("mall_product_id") or "")
            if not mall_pid or mall_pid in seen:
                continue
            seen.add(mall_pid)
            rows.append({
                "mall_product_id": mall_pid,
                "product_name": (ad.get("product_name") or "")[:300],
                "campaign_id": ag.campaign_id,
            })
        result[aid] = rows
    return result


def sync_adgroup_products(
    db: Session, *, ads_by_adgroup: dict[str, list[dict]] | None = None
) -> dict:
    """naver_adgroup_product 그룹 단위 스냅샷 교체(멱등). 반환: {adgroups, mappings, products}.

    동기화한 광고그룹의 기존 행만 삭제 후 재삽입(그룹 단위 교체) — 다른 그룹/과거 매핑은 보존한다.
    한 그룹 안에서 상품이 사라지면 그 행이 다음 sync에서 빠지므로 매핑이 최신으로 유지된다.
    """
    per_group = collect_adgroup_products(db, ads_by_adgroup=ads_by_adgroup)
    now = kst_now()
    n_map = 0
    distinct: set[str] = set()
    for aid, rows in per_group.items():
        # 이 그룹의 기존 매핑 삭제 후 재삽입(그룹 단위 스냅샷 — 상품 이탈도 반영).
        db.execute(delete(NaverAdgroupProduct).where(NaverAdgroupProduct.adgroup_id == aid))
        for r in rows:
            db.add(NaverAdgroupProduct(
                adgroup_id=aid,
                campaign_id=r["campaign_id"],
                mall_product_id=r["mall_product_id"],
                product_name=r["product_name"],
                synced_at=now,
            ))
            n_map += 1
            distinct.add(r["mall_product_id"])
    db.commit()
    log.info("naver_adgroup_product sync: 그룹 %d개, 매핑 %d행, 상품 %d종",
             len(per_group), n_map, len(distinct))
    return {"adgroups": len(per_group), "mappings": n_map, "products": len(distinct)}
