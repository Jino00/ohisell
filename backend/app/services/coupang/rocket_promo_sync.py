# rocket_promo_sync.py — 쿠팡 프로모션 손익 레이어 ingest+store Harness (트랙 coupang-promo-pnl, Phase 1)
#
# 흐름(원칙18-2): 페처가 push한 **레코드 계약** → 파서 SA(clients/coupang/rocket_promo.py) 정규화
#   → snapshot upsert. 이 Harness는 수신·저장만 한다(계산·회계 결합 없음).
#
# ★회계축 불변: 여기서 적재하는 어떤 값도 net_profit·종합조망에 결합되지 않는다.
#   - 1P 판매 revenue = 소비자 실현가(D-CPP-2) → 회계 매출 아님. 1P 매출은 발주(납품)금액 축 유지.
#   - 프로모션 분담금 = 청구 방식 미확정(D-CPP-4) → 사실 기록일 뿐 비용 라인 아님.
#   손익 결합은 Phase 2(별도 승인)에서만.
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.coupang import rocket_promo as parser
from app.models import CoupangCoupon, CoupangRocketPromotion, CoupangRocketSalesDaily
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# ① 1P 옵션×일 판매 ingest — grain (vendor_id, option_id, date)
# ════════════════════════════════════════════════
def ingest_rocket_sales(
    db: Session, vendor_id: str, rows: list[dict], *, source: str = "sales_analysis"
) -> dict:
    """판매분석 레코드(계약) → coupang_rocket_sales_daily snapshot upsert.

    멱등: 같은 (vendor_id, option_id, date) 재수신 시 확정치로 교체.
    vendor_id는 계정축(판매분석 행에 없어 페처가 주입 — 정산 ingest와 동일 패턴).
    반환: {ingested, skipped, vendor_id}. skipped = 계약 필수키(option_id/date) 누락 행 수.
    """
    recs = parser.parse_sales_rows(rows, source=source)
    skipped = len(rows or []) - len(recs)
    now = kst_now()
    for rec in recs:
        row = (
            db.query(CoupangRocketSalesDaily)
            .filter(
                CoupangRocketSalesDaily.vendor_id == vendor_id,
                CoupangRocketSalesDaily.option_id == rec["option_id"],
                CoupangRocketSalesDaily.date == rec["date"],
            )
            .first()
        )
        if row is None:
            row = CoupangRocketSalesDaily(
                vendor_id=vendor_id, option_id=rec["option_id"], date=rec["date"]
            )
            db.add(row)
        row.sku_id = rec["sku_id"]
        row.qty = rec["qty"]
        row.revenue = rec["revenue"]
        row.visitors = rec["visitors"]
        row.conversion_rate = rec["conversion_rate"]
        row.product_name = rec["product_name"]
        row.source = rec["source"]
        row.synced_at = now
    db.commit()
    log.info("1P 판매 ingest: vendor=%s records=%d skipped=%d", vendor_id, len(recs), skipped)
    return {"ingested": len(recs), "skipped": skipped, "vendor_id": vendor_id}


# ════════════════════════════════════════════════
# ② 1P 프로모션 ingest — grain request_id
# ════════════════════════════════════════════════
def ingest_rocket_promotions(db: Session, vendor_id: str, rows: list[dict]) -> dict:
    """프로모션 신청 레코드(계약) → coupang_rocket_promotion snapshot upsert.

    멱등: 같은 request_id 재수신 시 확정치로 교체(상태 변화·기간 수정 반영).
    반환: {ingested, skipped, vendor_id}.
    """
    recs = parser.parse_promotion_rows(rows)
    skipped = len(rows or []) - len(recs)
    now = kst_now()
    for rec in recs:
        row = (
            db.query(CoupangRocketPromotion)
            .filter(CoupangRocketPromotion.request_id == rec["request_id"])
            .first()
        )
        if row is None:
            row = CoupangRocketPromotion(request_id=rec["request_id"])
            db.add(row)
        row.vendor_id = vendor_id
        row.contract_id = rec["contract_id"]
        row.promotion_name = rec["promotion_name"]
        row.promotion_type = rec["promotion_type"]
        row.status = rec["status"]
        row.start_at = rec["start_at"]
        row.end_at = rec["end_at"]
        row.share_ratio = rec["share_ratio"]
        row.discount_method = rec["discount_method"]
        row.discount_value = rec["discount_value"]
        row.budget_amount = rec["budget_amount"]
        row.settlement_date = rec["settlement_date"]
        row.applied_product_count = rec["applied_product_count"]
        row.requested_at = rec["requested_at"]
        if rec["raw"] is not None:
            row.raw = rec["raw"]
        row.synced_at = now
    db.commit()
    log.info("1P 프로모션 ingest: vendor=%s records=%d skipped=%d", vendor_id, len(recs), skipped)
    return {"ingested": len(recs), "skipped": skipped, "vendor_id": vendor_id}


# ════════════════════════════════════════════════
# ③ 2P RG 쿠폰 사용 금액 ingest (D-CPP-3 권위값)
# ════════════════════════════════════════════════
def ingest_coupon_used_amount(
    db: Session, account_key: str, rows: list[dict], *, source: str = "wing_ui"
) -> dict:
    """쿠폰별 실사용 할인액 → 기존 coupang_coupon 행 갱신(INSTANT 그레인).

    ★행을 새로 만들지 않는다: coupang_coupon의 자연키는 (account_key, coupon_kind, coupon_id)이고
      vendor_id·kind는 이 계약에 없다. 없는 쿠폰은 **not_found로 세어 돌려준다** — 조용히 만들면
      쿠폰 메타 없는 유령 행이 권위값 자리를 차지한다(원칙22).
      쿠폰 메타는 coupon_sync(cron 06:00)가 채우므로, 순서가 어긋나면 다음 회차에 붙는다.
    반환: {updated, not_found, skipped, account_key, source}.
    """
    recs = parser.parse_coupon_usage_rows(rows)
    skipped = len(rows or []) - len(recs)
    src = (source or "").strip()[:20] or "wing_ui"
    now = kst_now()
    updated = 0
    not_found: list[str] = []
    for rec in recs:
        row = (
            db.query(CoupangCoupon)
            .filter(
                CoupangCoupon.account_key == account_key,
                CoupangCoupon.coupon_id == rec["coupon_id"],
            )
            .order_by(CoupangCoupon.id)
            .first()
        )
        if row is None:
            not_found.append(rec["coupon_id"])
            continue
        row.used_amount = rec["used_amount"]
        row.used_amount_source = src
        row.used_amount_synced_at = now
        updated += 1
    db.commit()
    log.info(
        "쿠폰 사용금액 ingest: account=%s updated=%d not_found=%d skipped=%d source=%s",
        account_key, updated, len(not_found), skipped, src,
    )
    return {
        "updated": updated,
        "not_found": len(not_found),
        "not_found_coupon_ids": not_found[:50],  # 진단용 상한(응답 비대화 방지)
        "skipped": skipped,
        "account_key": account_key,
        "source": src,
    }
