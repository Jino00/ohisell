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
) -> tuple[dict[str, list[dict]], set[str]]:
    """대상 광고그룹별 상품 매핑 + 조회 실패 그룹 집합을 반환.

    반환: ({adgroup_id: [{mall_product_id, product_name, campaign_id}, ...]}, failed_adgroup_ids)
    ads_by_adgroup는 테스트·재사용 주입용(원칙18-8). 미주입 시 get_ads로 그룹마다 1콜.
    같은 그룹에서 같은 mall_product_id가 여러 소재로 중복되면 dedup(첫 이름 채택).
    개별 그룹의 조회 실패는 그 그룹만 skip(fail-open)하되 **failed에 기록**한다 — 리컨실(P2-3)이
    실패 그룹의 기존 매핑을 stale로 오인해 지우지 않게(일시 장애에 매핑 소실 금지).
    """
    adgroups = _ours_shopping_adgroups(db)
    result: dict[str, list[dict]] = {}
    failed: set[str] = set()
    for ag in adgroups:
        aid = ag.entity_id
        try:
            ads = (ads_by_adgroup or {}).get(aid) if ads_by_adgroup is not None else get_ads(aid)
        except Exception as e:  # noqa: BLE001 — 그룹 단위 fail-open
            log.warning("shopping_ad_product_sync: 광고그룹 %s 소재 조회 실패(skip): %s", aid, e)
            failed.add(aid)
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
                # B1(D-NAO-65): 소재-레벨 입찰 필드(get_ads가 채움 — 미주입/부재 시 None).
                # dedup은 첫 소재를 채택하므로 입찰 필드도 첫 소재 값(product_name과 동형).
                "ad_id": ad.get("ad_id") or None,
                "ad_bid_amt": ad.get("ad_bid_amt"),
                "use_group_bid_amt": ad.get("use_group_bid_amt"),
                "ad_user_lock": ad.get("ad_user_lock"),
            })
        result[aid] = rows
    return result, failed


def sync_adgroup_products(
    db: Session, *, ads_by_adgroup: dict[str, list[dict]] | None = None
) -> dict:
    """naver_adgroup_product 그룹 단위 스냅샷 교체 + stale 리컨실(멱등, 리뷰 P2-3).

    반환: {adgroups, mappings, products, removed, failed_adgroups}.

    3층 정리로 stale 행 영구 잔존을 막는다:
      (0) 그룹 스냅샷 교체 — 동기화한 그룹의 행 삭제 후 재삽입(상품 이탈 반영, 기존 동작)
      (1) optimizer가 ours가 아니게 된 캠페인의 행 전체 삭제(관리 이탈 = 매핑 근거 소멸)
      (2) **전체 그룹을 성공적으로 열거한 캠페인에 한해** 수집 결과에 없는 그룹의 행 삭제
          (그룹 삭제/off/캠페인의 그룹 구성 변화). get_ads 실패로 skip된 그룹이 하나라도 있는
          캠페인은 (2)를 건너뛴다 — 일시 API 장애가 기존 매핑을 지우면 resolver ②가 조용히
          ③ 폴백으로 강등되므로(target 드리프트) 보존이 안전 방향.
    """
    ours_ids = [
        r[0] for r in db.execute(
            select(NaverCampaignSettings.campaign_id).where(
                NaverCampaignSettings.optimizer == "ours"
            )
        ).all()
    ]
    per_group, failed = collect_adgroup_products(db, ads_by_adgroup=ads_by_adgroup)
    now = kst_now()
    removed = 0

    # (1) ours 이탈 캠페인 정리 — ours가 하나도 없으면 전체가 근거 소멸.
    if ours_ids:
        res = db.execute(delete(NaverAdgroupProduct).where(
            NaverAdgroupProduct.campaign_id.not_in(ours_ids)
        ))
    else:
        res = db.execute(delete(NaverAdgroupProduct))
    removed += res.rowcount or 0

    # (0) 그룹 스냅샷 교체(동기화 성공 그룹만).
    n_map = 0
    distinct: set[str] = set()
    for aid, rows in per_group.items():
        db.execute(delete(NaverAdgroupProduct).where(NaverAdgroupProduct.adgroup_id == aid))
        for r in rows:
            db.add(NaverAdgroupProduct(
                adgroup_id=aid,
                campaign_id=r["campaign_id"],
                mall_product_id=r["mall_product_id"],
                product_name=r["product_name"],
                # B1(D-NAO-65): 입찰 필드 미주입(기존 소비자 형식) 시 .get→None(하위호환).
                ad_id=r.get("ad_id"),
                ad_bid_amt=r.get("ad_bid_amt"),
                use_group_bid_amt=r.get("use_group_bid_amt"),
                ad_user_lock=r.get("ad_user_lock"),
                synced_at=now,
            ))
            n_map += 1
            distinct.add(r["mall_product_id"])

    # (2) 완전 열거 캠페인의 stale 그룹 정리. 대상 그룹(활성 쇼핑)이 0인 ours 캠페인도
    #     "완전 열거"로 취급 — 남은 행 전부 stale(그룹이 사라졌거나 전부 off).
    adgroups = _ours_shopping_adgroups(db)
    attempted_by_campaign: dict[str, set[str]] = {}
    for ag in adgroups:
        attempted_by_campaign.setdefault(ag.campaign_id, set()).add(ag.entity_id)
    failed_campaigns = {ag.campaign_id for ag in adgroups if ag.entity_id in failed}
    for cid in ours_ids:
        if cid in failed_campaigns:
            continue  # 이 캠페인은 일시 장애 — stale 판정 유보(다음 성공 sync가 정리)
        gids = attempted_by_campaign.get(cid, set())
        cond = [NaverAdgroupProduct.campaign_id == cid]
        if gids:
            cond.append(NaverAdgroupProduct.adgroup_id.not_in(gids))
        res = db.execute(delete(NaverAdgroupProduct).where(*cond))
        removed += res.rowcount or 0

    db.commit()
    log.info("naver_adgroup_product sync: 그룹 %d개, 매핑 %d행, 상품 %d종, 정리 %d행, 실패그룹 %d",
             len(per_group), n_map, len(distinct), removed, len(failed))
    return {"adgroups": len(per_group), "mappings": n_map, "products": len(distinct),
            "removed": removed, "failed_adgroups": len(failed)}
