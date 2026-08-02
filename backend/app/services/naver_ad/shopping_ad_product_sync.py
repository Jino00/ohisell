# shopping_ad_product_sync.py — shopping_ad_product_sync (D-NAO-57 A, 관찰성 sync)
# 역할: **관측 스코프**(campaign_roster.observation_campaign_ids) 쇼핑 캠페인의 활성 광고그룹을
#   순회 → /ncc/ads의 referenceData.mallProductId를 수집 → naver_adgroup_product에 그룹 단위
#   스냅샷 교체 적재. campaign_target_resolver 우선순위 ②(상품 파생 target_roas)가 소비한다.
# 순수 수집(collect_) + 쓰기 harness(sync_) 분리 — 관찰만이라 실행 게이트 없음(fail-open은
#   호출 크론이 담당). 매핑은 느리게 변하는 관측치라 일 1회(07:45, 레인·제안 이전) 충분.
#
# ★2026-07-30 사고(D-NAO-132 긴급정지)로 두 가지를 고쳤다 — 자세한 근거는
#   `campaign_roster.observation_campaign_ids` docstring:
#   ①스코프를 optimizer='ours'에서 관측 스코프로 교체(스위치를 내리면 눈까지 감던 구조).
#   ②**관측 대상 0일 때의 전량 삭제 제거.** 구 코드는 ours가 하나도 없으면
#     `delete(NaverAdgroupProduct)`로 테이블을 통째로 비웠고, 실제로 2026-07-31 07:45 KST에
#     276행이 0행이 됐다(prod 로그 "그룹 0개 ... 정리 276행"). **"관측 대상 0"은 정상 상태가
#     아니라 이상 신호이며, 데이터를 지우는 근거가 될 수 없다.**
from __future__ import annotations

import logging

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverEntity
from app.services.naver_ad import ad_external_change, campaign_roster
from app.services.naver_ad.diary import ACTOR_SYSTEM, write_diary_entry
from app.services.naver_sa_ad_fetcher import get_ads
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 관측 대상 자체가 0일 때 남기는 일기 action(diary observe grain).
DIARY_ACTION_SCOPE_BLIND = "adgroup_product_scope_blind"


def _observed_shopping_adgroups(db: Session) -> list[NaverEntity]:
    """**관측 스코프** 캠페인에 속한 활성(status='on') 쇼핑 광고그룹 엔티티.

    스코프 = `campaign_roster.observation_campaign_ids`(최근 7일 광고비>0 ∪ settings 행 존재,
    optimizer 무관). ★여기를 optimizer='ours'로 되돌리면 2026-07-30 사고가 재현된다 —
    이유는 그 함수 docstring에 D-NAO-13 원문·bm_diff.py:10-13과 함께 적혀 있다.

    모듈 고유 필터는 그대로다: 매핑 소스는 SHOPPING 캠페인의 활성 그룹뿐이다
    (파워링크/브랜드검색은 상품 소재가 아님).
    """
    scope_ids = campaign_roster.observation_campaign_ids(db)
    if not scope_ids:
        return []
    return (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "adgroup",
            NaverEntity.campaign_type == "SHOPPING",
            NaverEntity.status == "on",
            NaverEntity.campaign_id.in_(scope_ids),
        )
        .all()
    )


def _log_observation_blind(
    db: Session, *, scope_ids: set[str], adgroups: list[NaverEntity], now,
) -> None:
    """관측 대상이 0일 때의 유성 실패 기록 — WARNING + 운영 일기(observe).

    `flight_loop._log_flight_silence`의 관례를 따른다: **병리 상태일 때만** 1행 남긴다
    (정상일 때 0행이라 소음이 되지 않는다). 다만 이 모듈은 캠페인 단위가 아니므로
    change_log가 아니라 diary observe에 남긴다(캠페인 조인에 섞이지 않게 campaign_id="").

    ★문장이 곧 진단이어야 한다: 구 코드의 INFO `그룹 0개`는 "관측했더니 0건"으로도,
      "볼 대상 자체가 없다"로도 읽혀서, 사흘간의 맹목이 안전 확인처럼 보였다.
    """
    existing = db.query(func.count(NaverAdgroupProduct.id)).scalar() or 0
    if not scope_ids:
        cause = "관측 스코프 캠페인이 0개(최근 7일 광고비>0 ∪ settings 행 — 둘 다 비었다)"
    else:
        cause = (
            f"관측 스코프 캠페인은 {len(scope_ids)}개인데 그 안의 활성 SHOPPING 광고그룹이 0개"
            "(entity_sync 침묵·그룹 전부 off 의심)"
        )
    msg = (
        f"관측 대상 자체가 0 — 스코프 결함 의심. {cause}. "
        f"기존 매핑 {existing}행은 **삭제하지 않고 보존**한다"
        "(2026-07-30 사고 재발 방지: 대상 0은 이상 신호이지 삭제 근거가 아니다)."
    )
    log.warning("shopping_ad_product_sync: %s", msg)
    write_diary_entry(
        db, "observe", "", actor=ACTOR_SYSTEM,
        action=DIARY_ACTION_SCOPE_BLIND, rationale=msg, now=now,
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
    adgroups = _observed_shopping_adgroups(db)
    result: dict[str, list[dict]] = {}
    failed: set[str] = set()
    if not adgroups:
        # ★"대상 0" ≠ "관측했는데 0건". 구 코드는 둘 다 조용히 넘겨 맹목 상태가 안전 확인처럼
        #   보였다(2026-07-31~08-02 사흘간 INFO "그룹 0개"만 찍히며 침묵).
        log.warning(
            "shopping_ad_product_sync: 관측 대상 광고그룹이 0 — 관측 스코프 결함 의심"
            "(스코프 정의는 campaign_roster.observation_campaign_ids)",
        )
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
                # D-NAO-127: 소재 외부 변경 탐지 앵커(editTm). 미주입 경로는 None → 판정 유보.
                "edit_tm": ad.get("edit_tm"),
                "adgroup_id": aid,
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
      (1) **관측 스코프를 벗어난** 캠페인의 행 전체 삭제(더 이상 관측 대상이 아님)
      (2) **전체 그룹을 성공적으로 열거한 캠페인에 한해** 수집 결과에 없는 그룹의 행 삭제
          (그룹 삭제/off/캠페인의 그룹 구성 변화). get_ads 실패로 skip된 그룹이 하나라도 있는
          캠페인은 (2)를 건너뛴다 — 일시 API 장애가 기존 매핑을 지우면 resolver ②가 조용히
          ③ 폴백으로 강등되므로(target 드리프트) 보존이 안전 방향.

    ★★관측 대상이 0이면 (1)·(2) 정리를 **통째로 건너뛴다**(2026-07-30 사고의 직접 수정).
      구 코드는 대상 0을 "전부 근거 소멸"로 읽어 `delete(NaverAdgroupProduct)` 전량 삭제를
      돌렸고, 긴급정지로 모든 캠페인이 optimizer='none'이 된 다음 날 276행이 0행이 됐다.
      대상 0은 우리 스코프·설정·수집 경로 중 하나가 고장났다는 **이상 신호**이지, 매핑이
      실제로 사라졌다는 관측이 아니다 — 위 (2)의 "실패 그룹은 정리 유보"와 같은 규율을
      스코프 전체 레벨로 끌어올린 것이다.
    """
    scope_ids = campaign_roster.observation_campaign_ids(db)
    # ★D-NAO-127: 직전 관측 상태를 **삭제 전에** 메모리로 뜬다(이 함수는 스냅샷 교체라
    #   아래 delete가 지나가면 어제 값이 사라진다 = 비교 대상 소멸).
    prev_by_ad = {
        r.ad_id: {
            "edit_tm": r.ad_edit_tm, "ad_bid_amt": r.ad_bid_amt,
            "use_group_bid_amt": r.use_group_bid_amt, "ad_user_lock": r.ad_user_lock,
        }
        for r in db.query(NaverAdgroupProduct).filter(NaverAdgroupProduct.ad_id.isnot(None)).all()
    }
    per_group, failed = collect_adgroup_products(db, ads_by_adgroup=ads_by_adgroup)
    adgroups = _observed_shopping_adgroups(db)
    now = kst_now()
    removed = 0

    # ★관측 맹목 판정 — 여기가 이 함수의 파괴적 분기 가드다.
    #   `scope_ids`가 비면 `adgroups`도 반드시 비지만(스코프로 필터하므로) 둘을 따로 본다:
    #   "우리가 아무 캠페인도 안 본다"와 "볼 캠페인은 있는데 활성 쇼핑 그룹이 하나도 없다"는
    #   원인이 다르고, 로그에서 구분돼야 사람이 어디를 고칠지 안다.
    observation_blind = not scope_ids or not adgroups
    if observation_blind:
        _log_observation_blind(db, scope_ids=scope_ids, adgroups=adgroups, now=now)

    # (1) 스코프 이탈 캠페인 정리 — 맹목 상태면 실행하지 않는다(전량 삭제 금지).
    if not observation_blind:
        res = db.execute(delete(NaverAdgroupProduct).where(
            NaverAdgroupProduct.campaign_id.not_in(scope_ids)
        ))
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
                ad_edit_tm=r.get("edit_tm"),  # D-NAO-127 앵커
                synced_at=now,
            ))
            n_map += 1
            distinct.add(r["mall_product_id"])

    # (2) 완전 열거 캠페인의 stale 그룹 정리. 대상 그룹(활성 쇼핑)이 0인 **스코프 내** 캠페인도
    #     "완전 열거"로 취급 — 남은 행 전부 stale(그룹이 사라졌거나 전부 off).
    #     ★단 스코프 전체가 맹목이면 이 층도 돌지 않는다(위 observation_blind 참조).
    if not observation_blind:
        attempted_by_campaign: dict[str, set[str]] = {}
        for ag in adgroups:
            attempted_by_campaign.setdefault(ag.campaign_id, set()).add(ag.entity_id)
        failed_campaigns = {ag.campaign_id for ag in adgroups if ag.entity_id in failed}
        for cid in sorted(scope_ids):
            if cid in failed_campaigns:
                continue  # 이 캠페인은 일시 장애 — stale 판정 유보(다음 성공 sync가 정리)
            gids = attempted_by_campaign.get(cid, set())
            cond = [NaverAdgroupProduct.campaign_id == cid]
            if gids:
                cond.append(NaverAdgroupProduct.adgroup_id.not_in(gids))
            res = db.execute(delete(NaverAdgroupProduct).where(*cond))
            removed += res.rowcount or 0

    db.commit()

    # ★D-NAO-127 소재 외부 변경 탐지 — 매핑 커밋 **이후**에 독립 실행한다.
    #   ①prev_by_ad는 이미 메모리에 떠 있어 DB가 새 값으로 덮여도 비교가 가능하고,
    #   ②탐지 실패가 매핑 sync를 되돌리지 않는다(관측 부가기능이 본 기능을 죽이면 안 됨 —
    #     이 파일의 그룹 단위 fail-open과 같은 규율).
    detected = {"observed": 0, "ops": 0, "recorded": 0}
    try:
        observed = [r for rows in per_group.values() for r in rows if r.get("ad_id")]
        detected = ad_external_change.run(db, prev_by_ad=prev_by_ad, observed=observed, now=now)
    except Exception as e:  # noqa: BLE001 — 관측 전용 부가기능, fail-open
        log.exception("shopping_ad_product_sync: 소재 외부 변경 탐지 실패(무시하고 진행): %s", e)
        db.rollback()

    # ★로그 레벨로 "대상 0"과 "관측했는데 0건"을 가른다(맹목은 WARNING, 정상은 INFO).
    #   맹목 경로의 상세 진단은 이미 _log_observation_blind가 남겼으므로 여기선 요약만.
    logline = ("naver_adgroup_product sync: 그룹 %d개, 매핑 %d행, 상품 %d종, 정리 %d행, "
               "실패그룹 %d, 외부변경 %d건%s")
    args = (len(per_group), n_map, len(distinct), removed, len(failed), detected["recorded"],
            " [관측 대상 0 — 정리 전면 보류]" if observation_blind else "")
    (log.warning if observation_blind else log.info)(logline, *args)
    return {"adgroups": len(per_group), "mappings": n_map, "products": len(distinct),
            "removed": removed, "failed_adgroups": len(failed),
            "external_ad_changes": detected["recorded"],
            # additive: 호출부(스케줄러 로그)·테스트가 맹목 상태를 값으로 확인할 수 있게.
            "observation_blind": observation_blind}
