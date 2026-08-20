# naver_product_meta_ingest.py — 네이버 커머스 상품 메타 적재 (C10 · D-NAO-212 · 북극성 M1 ④)
#
# 책임(SA): (1) `POST /v1/products/search`를 **필터 없이 전건 순회**해 (2) 채널상품 단위로
#   현재 단면을 upsert하고 (3) 값이 바뀐 필드만 변경 원장에 append하고 (4) **완주했는지를
#   숫자로 판정해 표면화**한다. 네이버에 쓰지 않는다(조회 전용 — 상품 도메인 쓰기 21종 무접촉).
#
# ★왜 이 축이 지금 열리는가: 상품 도메인 64 endpoint 전체에 변경-피드·변경 타임스탬프가 없다
#   (75건 전건 개봉 실측 2026-08-19). 즉 **폴링 개통일 = 관측 창의 시작일**이고 소급이 원리적으로
#   불가능하다. 늦게 열수록 창이 영원히 짧다.
#
# ★「완주」를 주장하는 필드를 새로 넣으면 그 함수의 조용한 실패 경로를 같은 턴에 다 훑어야 한다
#   (교훈 #320). 이 파일에서 «완주»는 두 등식이 **동시에** 성립할 때만 참이다:
#     ①읽은 페이지 수 == totalPages   ②본 원상품 수 == totalElements
#   ★★등식 ②의 grain에 주의: `totalElements`가 세는 것은 **원상품(contents 항목)**이지
#     채널상품이 아니다(2026-08-21 실측: size=8일 때 totalElements=1213·totalPages=152 →
#     152×8=1216 ≈ 1213). 계약 §4-4 ①은 「current 행수 == totalElements」라고 적었는데
#     저장 grain은 채널상품이라 **그대로는 성립하지 않는다** — 합격기준을 낮추는 게 아니라
#     grain을 바로잡아 ②로 검산하고, 채널상품 행수는 별도 숫자로 같이 보고한다.
#
# ★부분 적재를 success로 기록하지 않는다(교훈 #318·#319·#320 — 이 저장소는 절단이 `success`로
#   기록된 실사고를 갖고 있다). 미완주면 `complete=False`로 돌려주고 로그를 error로 남긴다.
#   ⚠️단 **이미 받은 페이지의 적재분은 지우지 않는다** — 관측된 값은 참이고, 지우면 다음
#   회차까지 그 값이 없다.
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.naver import NaverClient
from app.config import get_naver_config
from app.models import NaverProductMetaChange, NaverProductMetaCurrent
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 채널 api_config_key — 이 저장소의 다른 호출부와 같은 값(naver_ops.py:44 · scheduler_service.py:1769).
NAVER_CONFIG_KEY = "NAVER"

PAGE_SIZE = 200          # 문서 권고(100~200 시작) · 카탈로그 ~1,213 원상품 → 7페이지
MAX_PAGES = 60           # 폭주 방지 상한(예상 7페이지의 8배). 닿으면 미완주로 표면화한다.
SLEEP_S = 0.15

# 컬럼 ← 응답 키. **29종 전부** 받는다(2026-08-21 실응답 전수 실측) — 절삭 0.
# 응답 스키마가 항목마다 다르다(26키/29키 혼재)라 없는 키는 None이 된다. 키 부재와 null의
# 구분이 필요하면 `raw_json`이 정본이다(교훈 #315).
_SCALAR_FIELDS: dict[str, str] = {
    "origin_product_no": "originProductNo",
    "group_product_no": "groupProductNo",
    "name": "name",
    "status_type": "statusType",
    "display_status_type": "channelProductDisplayStatusType",
    "channel_service_type": "channelServiceType",
    "sale_price": "salePrice",
    "discounted_price": "discountedPrice",
    "mobile_discounted_price": "mobileDiscountedPrice",
    "stock_quantity": "stockQuantity",
    "category_id": "categoryId",
    "whole_category_id": "wholeCategoryId",
    "whole_category_name": "wholeCategoryName",
    "brand_name": "brandName",
    "manufacturer_name": "manufacturerName",
    "delivery_fee": "deliveryFee",
    "return_fee": "returnFee",
    "exchange_fee": "exchangeFee",
    "delivery_attribute_type": "deliveryAttributeType",
    "text_review_point": "textReviewPoint",
    "photo_video_review_point": "photoVideoReviewPoint",
    "regular_customer_point": "regularCustomerPoint",
    "manager_purchase_point": "managerPurchasePoint",
    "knowledge_shopping_registration": "knowledgeShoppingProductRegistration",
    "reg_date": "regDate",
    "modified_date": "modifiedDate",
}

# 문자열 컬럼의 길이 상한 — SQLite는 안 자르지만 이 저장소의 이행 목표는 PostgreSQL이고
# 거기선 초과가 **에러**다(같은 이유로 조인키를 문자열로 두는 판단과 한 짝).
_MAX_LEN: dict[str, int] = {
    "origin_product_no": 50, "group_product_no": 50, "status_type": 30,
    "display_status_type": 30, "channel_service_type": 30, "category_id": 30,
    "whole_category_id": 200, "whole_category_name": 300, "brand_name": 150,
    "manufacturer_name": 150, "delivery_attribute_type": 30,
    "reg_date": 40, "modified_date": 40,
}

# 변경 감지 대상 = 저장하는 모든 값 + 파생 2종. raw_json·타임스탬프는 제외한다
# (raw_json은 키 순서·공백만 달라져도 바뀌므로 넣으면 «매일 전건 변경»이 된다).
_DIFF_FIELDS: tuple[str, ...] = tuple(_SCALAR_FIELDS) + ("seller_tags_json", "image_url")


def _clip(field: str, value: Any) -> Any:
    limit = _MAX_LEN.get(field)
    if limit and isinstance(value, str) and len(value) > limit:
        log.warning("[c10] %s 값이 %d자를 넘어 잘랐다(원문은 raw_json에 남는다)", field, limit)
        return value[:limit]
    return value


def _values_from_cp(cp: dict) -> dict:
    """채널상품 응답 1건 → 컬럼 dict. **원문은 버리지 않는다**(raw_json)."""
    values: dict[str, Any] = {}
    for col, key in _SCALAR_FIELDS.items():
        v = cp.get(key)
        if col == "knowledge_shopping_registration":
            values[col] = None if v is None else bool(v)
        elif col in ("origin_product_no", "group_product_no"):
            # ★조인키 계열은 문자열로 통일한다(상대편이 String(50)이다).
            values[col] = None if v is None else _clip(col, str(v))
        else:
            values[col] = _clip(col, v)

    tags = cp.get("sellerTags")
    values["seller_tags_json"] = json.dumps(tags, ensure_ascii=False) if tags else None
    rep = cp.get("representativeImage")
    values["image_url"] = (rep or {}).get("url") if isinstance(rep, dict) else None
    values["raw_json"] = json.dumps(cp, ensure_ascii=False)
    return values


def _diff(row: NaverProductMetaCurrent, values: dict) -> dict[str, list]:
    """바뀐 필드만 {필드: [old, new]}. 값이 같으면 빈 dict."""
    changed: dict[str, list] = {}
    for field in _DIFF_FIELDS:
        old = getattr(row, field)
        new = values.get(field)
        if old != new:
            changed[field] = [old, new]
    return changed


def sync_product_meta(
    db: Session,
    *,
    page_size: int = PAGE_SIZE,
    sleep_s: float = SLEEP_S,
    max_pages: int = MAX_PAGES,
    client: Any | None = None,
) -> dict:
    """전건 폴링 1회.

    반환: {pages, total_pages, origins, total_elements, channel_rows, new, changed,
           unchanged, change_rows, dup_in_run, complete, incomplete_reason, errors, as_of}

    ★`complete`가 False면 그 회차는 **실패로 읽어야 한다** — 부분 적재를 성공으로 기록하지
      않는다. 다만 이미 upsert된 행은 남긴다(관측된 값은 참이다).
    """
    now = kst_now()
    if client is None:
        # ★`get_naver_config`는 채널의 api_config_key를 **인자로 받는다**(config.py:41).
        #   초판은 무인자로 불러 라이브 첫 트리거에서 TypeError로 죽었다 — 테스트·적대 리뷰가
        #   둘 다 가짜 클라이언트를 주입해서 **원리적으로 못 잡는 자리**였다(격리 성공은
        #   충분조건이 아니다). 값은 이 저장소의 다른 호출부와 같은 "NAVER"
        #   (`naver_ops.py:44 _NAVER_CONFIG_KEY` · `scheduler_service.py:1769`).
        cfg = get_naver_config(NAVER_CONFIG_KEY)
        if cfg is None:
            # 「설정이 없다」와 「호출했는데 0건」을 섞지 않는다 — 전자는 완주 실패다.
            raise RuntimeError(
                f"네이버 커머스 설정 없음({NAVER_CONFIG_KEY}_CLIENT_ID/SECRET 미설정) — 폴링 불가"
            )
        api = NaverClient(cfg)
    else:
        api = client
    stats: dict = {
        "pages": 0, "total_pages": None, "origins": 0, "total_elements": None,
        "channel_rows": 0, "new": 0, "changed": 0, "unchanged": 0, "change_rows": 0,
        "dup_in_run": 0,
        "complete": False, "incomplete_reason": None, "errors": [],
        "as_of": now.isoformat(),
    }

    # ★기존 행을 **한 번에** 들고 간다 — 행마다 query→add를 하면 autoflush=False 아래서
    #   같은 키를 두 번 INSERT하는 결함이 생긴다(이 저장소에서 5회 재발한 모양, 교훈 #292).
    existing: dict[str, NaverProductMetaCurrent] = {
        r.channel_product_no: r
        for r in db.execute(select(NaverProductMetaCurrent)).scalars().all()
    }
    # ★같은 회차에 같은 키가 두 번 오면 «변경»이 아니다(적대 리뷰 1R P2-5). 그대로 두면
    #   응답 안의 중복이 변경 원장에 유령 행을 만드는데, 이 원장은 소급 복구가 안 된다.
    seen_this_run: set[str] = set()

    page = 1
    while page <= max_pages:
        try:
            data = api.search_products_raw(page=page, size=page_size) or {}
        except Exception as e:  # noqa: BLE001 — 조회 실패는 «완주 실패»이지 «0건»이 아니다
            stats["errors"].append(f"page {page}: {type(e).__name__}: {e}")
            stats["incomplete_reason"] = f"page {page} 조회 실패"
            log.exception("[c10] %d페이지 조회 실패", page)
            break

        contents = data.get("contents") or []
        if stats["total_elements"] is None:
            stats["total_elements"] = data.get("totalElements")
            stats["total_pages"] = data.get("totalPages")
        stats["pages"] += 1
        stats["origins"] += len(contents)

        for item in contents:
            for cp in (item.get("channelProducts") or []):
                cpn = cp.get("channelProductNo")
                if cpn is None:
                    stats["errors"].append("channelProductNo 없는 항목")
                    continue
                cpn = str(cpn)
                stats["channel_rows"] += 1
                if cpn in seen_this_run:
                    # 회차 내 중복 — 첫 관측을 정본으로 두고 diff·append를 하지 않는다.
                    stats["dup_in_run"] += 1
                    continue
                seen_this_run.add(cpn)
                values = _values_from_cp(cp)
                row = existing.get(cpn)
                if row is None:
                    row = NaverProductMetaCurrent(
                        channel_product_no=cpn, first_seen_at=now, last_seen_at=now,
                        last_changed_at=now, **values,
                    )
                    db.add(row)
                    existing[cpn] = row          # ★즉시 등록 — 같은 회차 중복 키를 두 번 안 만든다
                    stats["new"] += 1
                else:
                    changed = _diff(row, values)
                    if changed:
                        db.add(NaverProductMetaChange(
                            channel_product_no=cpn, observed_at=now,
                            changed_fields=json.dumps(changed, ensure_ascii=False, default=str),
                        ))
                        stats["changed"] += 1
                        stats["change_rows"] += 1
                        row.last_changed_at = now
                    else:
                        stats["unchanged"] += 1
                    for k, v in values.items():
                        setattr(row, k, v)
                    row.last_seen_at = now

        # 페이지 단위 커밋 — 중간에 죽어도 여기까지는 남는다(백필 규율 승계).
        db.commit()

        total_pages = data.get("totalPages") or 1
        if data.get("last") is True or page >= total_pages:
            break
        page += 1
        if sleep_s:
            time.sleep(sleep_s)
    else:
        stats["incomplete_reason"] = f"페이지 상한 {max_pages} 도달"
        log.error("[c10] 페이지 상한 %d 도달 — 카탈로그가 예상보다 크다", max_pages)

    # ★완주 판정 — 두 등식이 **동시에** 성립해야 참이다.
    if stats["incomplete_reason"] is None:
        te, tp = stats["total_elements"], stats["total_pages"]
        if te is None:
            stats["incomplete_reason"] = "totalElements 없음(응답 형태가 예상과 다르다)"
        elif tp is not None and stats["pages"] != tp:
            stats["incomplete_reason"] = f"페이지 미완주 {stats['pages']}/{tp}"
        elif stats["origins"] != te:
            # ★grain 주의: totalElements는 «원상품» 수다(채널상품 아님).
            stats["incomplete_reason"] = f"원상품 수 불일치 {stats['origins']} != totalElements {te}"
        else:
            stats["complete"] = True

    if stats["complete"]:
        log.info("[c10] 상품 메타 폴링 완주 %s",
                 {k: v for k, v in stats.items() if k != "errors"})
    else:
        # 「조용한 절단」을 막는 자리 — 이 로그가 없으면 부분 적재가 성공처럼 보인다.
        log.error("[c10] 상품 메타 폴링 **미완주**: %s / %s",
                  stats["incomplete_reason"],
                  {k: v for k, v in stats.items() if k != "errors"})
    return stats
