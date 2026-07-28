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
from sqlalchemy.orm.attributes import flag_modified

from app.clients.coupang import rocket_promo as parser
from app.models import CoupangCoupon, CoupangRocketPromotion, CoupangRocketSalesDaily
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 쿠폰 사용금액이 붙는 쿠폰 종류. coupang_coupon 그레인은 (account_key, coupon_kind, coupon_id)라
#   같은 coupon_id가 INSTANT/DOWNLOAD 두 행으로 공존할 수 있다(발급 시스템이 다르다: fms vs
#   marketplace). D-CPP-3의 "사용 금액"은 **셀러 부담 즉시할인**이므로 INSTANT에만 붙인다 —
#   kind를 안 걸면 DOWNLOAD 행(이미 usage_amount를 가진 다른 축)에 권위값이 잘못 앉는다.
_COUPON_KIND = "INSTANT"


# ════════════════════════════════════════════════
# ① 1P 옵션×일 판매 ingest — grain (vendor_id, option_id, date)
# ════════════════════════════════════════════════
def ingest_rocket_sales(
    db: Session, vendor_id: str, rows: list[dict], *, source: str = "sales_analysis"
) -> dict:
    """판매분석 레코드(계약) → coupang_rocket_sales_daily snapshot upsert.

    멱등: 같은 (vendor_id, option_id, date) 재수신 시 확정치로 교체.
    vendor_id는 계정축(판매분석 행에 없어 페처가 주입 — 정산 ingest와 동일 패턴).
    반환: {ingested, skipped, deduped, vendor_id}.
      skipped = 계약 위반 행 수(option_id/date/관측값 누락) — **수집 건강 신호**
      deduped = 같은 그레인에 흡수된 행 수 — 정상(재조회). 둘을 한 숫자로 뭉치면 계약 위반이
                중복 뒤에 숨어 보이지 않는다.
    """
    stats: dict = {}
    recs = parser.parse_sales_rows(rows, source=source, stats=stats)
    skipped = stats.get("skipped", 0)
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
    log.info(
        "1P 판매 ingest: vendor=%s records=%d skipped=%d deduped=%d",
        vendor_id, len(recs), skipped, stats.get("deduped", 0),
    )
    return {
        "ingested": len(recs),
        "skipped": skipped,
        "deduped": stats.get("deduped", 0),
        "vendor_id": vendor_id,
    }


# ════════════════════════════════════════════════
# ② 1P 프로모션 ingest — grain request_id
# ════════════════════════════════════════════════
def ingest_rocket_promotions(db: Session, vendor_id: str, rows: list[dict]) -> dict:
    """프로모션 신청 레코드(계약) → coupang_rocket_promotion snapshot upsert.

    멱등: 같은 request_id 재수신 시 확정치로 교체(상태 변화·기간 수정 반영).
    반환: {ingested, skipped, deduped, vendor_id} — skipped(계약 위반)와 deduped(중복 흡수) 분리.
    """
    stats: dict = {}
    recs = parser.parse_promotion_rows(rows, stats=stats)
    skipped = stats.get("skipped", 0)
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
    log.info(
        "1P 프로모션 ingest: vendor=%s records=%d skipped=%d deduped=%d",
        vendor_id, len(recs), skipped, stats.get("deduped", 0),
    )
    return {
        "ingested": len(recs),
        "skipped": skipped,
        "deduped": stats.get("deduped", 0),
        "vendor_id": vendor_id,
    }


# ════════════════════════════════════════════════
# ③ 2P RG 쿠폰 사용 금액 ingest (D-CPP-3 권위값)
# ════════════════════════════════════════════════
def ingest_coupon_used_amount(
    db: Session, account_key: str, rows: list[dict], *, source: str = "wing_ui"
) -> dict:
    """쿠폰별 실사용 할인액 → 기존 coupang_coupon 행 갱신(**INSTANT 그레인에 한정**).

    ★행을 새로 만들지 않는다: coupang_coupon의 자연키는 (account_key, coupon_kind, coupon_id)이고
      vendor_id는 이 계약에 없다. 없는 쿠폰은 **not_found로 세어 돌려준다** — 조용히 만들면
      쿠폰 메타 없는 유령 행이 권위값 자리를 차지한다(원칙22).
      쿠폰 메타는 coupon_sync(cron 06:00)가 채우므로, 순서가 어긋나면 다음 회차에 붙는다.
    ★kind를 그레인에 건다: 같은 coupon_id의 DOWNLOAD 행(다른 축인 usage_amount 보유)에 셀러부담
      권위값이 잘못 앉는 것을 막는다. DOWNLOAD 쿠폰이 섞여 들어오면 not_found로 표면화된다.
    ★coupang_coupon.synced_at(=쿠폰 **수집** 시각, onupdate=now)은 건드리지 않는다: 이 경로는
      수집이 아니라 별도 push다. 그대로 두면 죽은 수집기가 이 push 덕에 신선해 보인다
      (원칙22·RG 26일 침묵 교훈). 사용금액의 시각은 used_amount_synced_at이 따로 들고 있다.
    반환: {updated, not_found, skipped, deduped, account_key, source}.
    """
    stats: dict = {}
    recs = parser.parse_coupon_usage_rows(rows, stats=stats)
    skipped = stats.get("skipped", 0)
    src = (source or "").strip()[:20] or "wing_ui"
    now = kst_now()
    updated = 0
    not_found: list[str] = []
    for rec in recs:
        row = (
            db.query(CoupangCoupon)
            .filter(
                CoupangCoupon.account_key == account_key,
                CoupangCoupon.coupon_kind == _COUPON_KIND,
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
        # 수집 시각 동결: 값을 그대로 SET 절에 실어 onupdate=func.now()를 이긴다.
        flag_modified(row, "synced_at")
        updated += 1
    db.commit()
    log.info(
        "쿠폰 사용금액 ingest: account=%s kind=%s updated=%d not_found=%d skipped=%d source=%s",
        account_key, _COUPON_KIND, updated, len(not_found), skipped, src,
    )
    return {
        "updated": updated,
        "not_found": len(not_found),
        "not_found_coupon_ids": not_found[:50],  # 진단용 상한(응답 비대화 방지)
        "skipped": skipped,
        "deduped": stats.get("deduped", 0),
        "account_key": account_key,
        "source": src,
    }
