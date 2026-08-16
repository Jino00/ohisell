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
from app.models import (
    CoupangCoupon,
    CoupangRocketOptionSku,
    CoupangRocketPromotion,
    CoupangRocketSalesDaily,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 쿠폰 사용금액이 붙는 쿠폰 종류. coupang_coupon 그레인은 (account_key, coupon_kind, coupon_id)라
#   같은 coupon_id가 INSTANT/DOWNLOAD 두 행으로 공존할 수 있다(발급 시스템이 다르다: fms vs
#   marketplace). D-CPP-3의 "사용 금액"은 **셀러 부담 즉시할인**이므로 INSTANT에만 붙인다 —
#   kind를 안 걸면 DOWNLOAD 행(이미 usage_amount를 가진 다른 축)에 권위값이 잘못 앉는다.
_COUPON_KIND = "INSTANT"


def _observe_option_sku(
    db: Session, vendor_id: str, rec: dict, now,
    seen: dict[str, CoupangRocketOptionSku],
) -> None:
    """옵션ID↔상품번호 대응을 **누적 보존**한다(트랙 ohitech-ad D-16).

    판매분석은 BETA + 무료체험(D-CPP-5, 2026-08-20 종료 예정)이라 언젠가 끊긴다. 대응 자체는
    시간 불변인데(실측: sku_id가 바뀐 옵션 0건) 지금까지 이 일별 테이블의 부산물로만 존재했다
    — 수집이 멈추면 브리지가 통째로 사라져 옵션·상품별 손익이 조용히 멈춘다. 여기 남기면
    끊긴 뒤에도 이미 아는 상품은 계속 산출된다.

    ★sku_id가 비면 **아무것도 쓰지 않는다**(빈 행이 "매핑 있음"으로 오인되면 손익이 0원가로
      계산돼 순이익이 과대해진다). ★기존 행의 sku_id는 값이 실제로 바뀔 때만 갱신하고 그때
      로그를 남긴다 — 불변이라던 전제가 깨진 것이므로 조용히 넘기면 안 된다.

    seen: 이 요청에서 이미 잡은 브리지 행 캐시(option_id → 행). **필수 인자다** — 생략 가능하게
      두면 안 넘긴 호출부가 조용히 옛 결함으로 돌아간다(교훈 #283 「한 파일 수리 ≠ 규율 채택」).
      ★왜 필요한가(2026-08-15 prod 500 사고): 앱 세션은 `autoflush=False`(database.py)라
      query가 **아직 flush 안 된 add()를 못 본다**. 판매분석 한 요청은 옵션×날짜 격자라 같은
      option_id가 여러 날짜 행으로 되풀이 등장하는데, 브리지에 없는 신규 옵션이 **이틀 이상**
      팔렸으면 같은 option_id로 INSERT가 두 번 쌓여 commit에서
      `UNIQUE constraint failed: coupang_rocket_option_sku.option_id` → 그 청크가 통째 롤백된다.
      신규 옵션이 하루만 팔리던 동안은 1회 등장이라 무사했고(2026-08-05~13 신규 13건 전부 1일),
      2026-08-15 수집분에서 처음 이틀짜리 신규 옵션이 나와 3번째 청크가 죽었다.
      returns_sync(`seen`)·rg_order_sync(`pending`)이 같은 이유로 쓰는 것과 같은 모양이다.
    """
    sku = (rec.get("sku_id") or "").strip()
    option_id = rec.get("option_id")
    if not sku or not option_id:
        return
    row = seen.get(option_id)
    if row is None:
        row = (
            db.query(CoupangRocketOptionSku)
            .filter(CoupangRocketOptionSku.option_id == option_id)
            .first()
        )
    if row is None:
        row = CoupangRocketOptionSku(
            option_id=option_id, sku_id=sku, vendor_id=vendor_id,
            product_name=rec.get("product_name"), source="sales_analysis",
            first_observed_at=now, last_observed_at=now,
        )
        db.add(row)
        seen[option_id] = row
        _apply_option_attrs(row, rec, now)
        return
    seen[option_id] = row
    if row.sku_id != sku:
        log.warning(
            "1P 브리지 변경 관측: option_id=%s sku %s → %s (불변 전제가 깨졌다 — 손익 귀속이 바뀐다)",
            option_id, row.sku_id, sku,
        )
        row.sku_id = sku
    row.vendor_id = vendor_id
    if rec.get("product_name"):
        row.product_name = rec["product_name"]
    _apply_option_attrs(row, rec, now)
    row.last_observed_at = now


# 판매분석이 주는 옵션 속성 — **조회 시점의 현재값**이지 그 날짜의 상태가 아니다(D-CPP-11 실측:
#   08-04와 06-05를 공통 옵션 22개로 대조했더니 전부 동일). 그래서 일별 행이 아니라 여기 최신값
#   1개로 남기고, 언제 본 값인지를 attrs_observed_at에 적는다.
_OPTION_ATTRS = (
    "brand_name", "category_path", "is_item_winner", "is_oos", "rating_count", "rating_score",
)


def _apply_option_attrs(row, rec: dict, now) -> None:
    """옵션 속성 갱신. **하나라도 관측됐을 때만** attrs_observed_at을 전진시킨다.

    ★None은 덮지 않는다: 속성을 안 보내는 경로(excel 폴백·구버전 페처)가 같은 옵션을 재적재할 때
      이미 아는 값을 지워버린다. 그리고 아무것도 안 왔는데 관측 시각만 갱신하면 **반년 전 값이
      오늘 본 값처럼 보인다** — 그게 이 필드를 둔 이유를 무너뜨린다.
    """
    seen = False
    for attr in _OPTION_ATTRS:
        val = rec.get(attr)
        if val is None:
            continue
        setattr(row, attr, val)
        seen = True
    if seen:
        row.attrs_observed_at = now


# ════════════════════════════════════════════════
# ① 1P 옵션×일 판매 ingest — grain (vendor_id, option_id, date)
# ════════════════════════════════════════════════
def ingest_rocket_sales(
    db: Session, vendor_id: str, rows: list[dict], *, source: str = "sales_analysis"
) -> dict:
    """판매분석 레코드(계약) → coupang_rocket_sales_daily snapshot upsert.

    멱등: 같은 (vendor_id, option_id, date) 재수신 시 확정치로 교체.
    vendor_id는 계정축(판매분석 행에 없어 페처가 주입 — 정산 ingest와 동일 패턴).
    반환: {ingested, skipped, deduped, accepted, blank_observations, blank_qty, blank_revenue,
           vendor_id}.
      skipped = 계약 위반 행 수(option_id/date/관측값 누락) — **수집 건강 신호**
      deduped = 같은 그레인에 흡수된 행 수 — 정상(재조회). 둘을 한 숫자로 뭉치면 계약 위반이
                중복 뒤에 숨어 보이지 않는다.
      accepted = 계약을 통과한 행 수(= ingested + deduped). 아래 빈 관측 카운터의 **분모**다 —
                ingested와 비교하면 중복이 있는 배치에서 판정이 어긋난다.
    """
    stats: dict = {}
    recs = parser.parse_sales_rows(rows, source=source, stats=stats)
    skipped = stats.get("skipped", 0)
    now = kst_now()
    # 이 요청에서 잡은 브리지 행 캐시 — autoflush=False라 query가 pending add()를 못 본다.
    #   상세 근거는 _observe_option_sku docstring(2026-08-15 prod 500 사고).
    seen_options: dict[str, CoupangRocketOptionSku] = {}
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
        # ★None이면 **덮지 않는다**(D-CPP-11): 이 컬럼들은 나중에 붙었으므로, 이 값을 안 보내는
        #   구버전 페처나 excel 폴백 경로가 같은 그레인을 재적재할 때 이미 채운 값을 지운다.
        #   qty/revenue와 달리 "관측 없음"이 정상적으로 존재하는 필드라 upsert 규칙이 다르다.
        if rec.get("page_views") is not None:
            row.page_views = rec["page_views"]
        if rec.get("orders") is not None:
            row.orders = rec["orders"]
        row.product_name = rec["product_name"]
        row.source = rec["source"]
        row.synced_at = now
        _observe_option_sku(db, vendor_id, rec, now, seen_options)
    db.commit()
    log.info(
        "1P 판매 ingest: vendor=%s records=%d skipped=%d deduped=%d",
        vendor_id, len(recs), skipped, stats.get("deduped", 0),
    )
    return {
        "ingested": len(recs),
        "skipped": skipped,
        "deduped": stats.get("deduped", 0),
        # 빈 관측 카운터. **accepted와 같아지면** 0판매일이 아니라 페처 매핑 사고를 의심해야
        #   한다(분모는 ingested가 아니라 accepted — 중복이 섞인 배치에서 어긋난다).
        #   blank_qty/blank_revenue를 따로 두는 이유: 컬럼명이 한쪽만 바뀌면 다른 쪽이 행을
        #   살려서 blank_observations를 빠져나간다.
        "accepted": stats.get("accepted", 0),
        "blank_observations": stats.get("blank_observations", 0),
        "blank_qty": stats.get("blank_qty", 0),
        "blank_revenue": stats.get("blank_revenue", 0),
        "vendor_id": vendor_id,
    }


# ════════════════════════════════════════════════
# ② 1P 프로모션 ingest — grain request_id
# ════════════════════════════════════════════════
def ingest_rocket_promotions(db: Session, vendor_id: str, rows: list[dict]) -> dict:
    """프로모션 신청 레코드(계약) → coupang_rocket_promotion snapshot upsert.

    멱등: 같은 request_id 재수신 시 확정치로 교체(상태 변화·기간 수정 반영).
    반환: {ingested, skipped, deduped, vendor_id} — skipped(계약 위반)와 deduped(중복 흡수) 분리.
    (판매 ingest와 달리 빈 관측 카운터는 없다 — 프로모션은 필수키가 request_id 하나뿐이라
     '전 행이 빈 값'이라는 상태가 성립하지 않는다.)
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
    ★not_found와 wrong_kind를 가른다: 전자는 **일시적**(쿠폰 메타가 아직 안 들어옴 → 다음
      회차에 붙는다), 후자는 **영구적**(그 id는 DOWNLOAD 쿠폰이라 영원히 안 붙는다).
      한 숫자로 뭉치면 운영자가 "기다리면 되는 것"과 "고쳐야 하는 것"을 구분할 수 없다.
    반환: {updated, not_found, wrong_kind, skipped, deduped, account_key, source}.
    """
    stats: dict = {}
    recs = parser.parse_coupon_usage_rows(rows, stats=stats)
    skipped = stats.get("skipped", 0)
    src = (source or "").strip()[:20] or "wing_ui"
    now = kst_now()
    updated = 0
    not_found: list[str] = []
    wrong_kind: list[str] = []
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
            # kind만 빼고 다시 찾아본다 — 있으면 '아직 안 들어온 것'이 아니라 '다른 축'이다.
            other = (
                db.query(CoupangCoupon)
                .filter(
                    CoupangCoupon.account_key == account_key,
                    CoupangCoupon.coupon_id == rec["coupon_id"],
                )
                .first()
            )
            (wrong_kind if other is not None else not_found).append(rec["coupon_id"])
            continue
        row.used_amount = rec["used_amount"]
        row.used_amount_source = src
        row.used_amount_synced_at = now
        # 수집 시각 동결: 값을 그대로 SET 절에 실어 onupdate=func.now()를 이긴다.
        flag_modified(row, "synced_at")
        updated += 1
    db.commit()
    log.info(
        "쿠폰 사용금액 ingest: account=%s kind=%s updated=%d not_found=%d wrong_kind=%d "
        "skipped=%d source=%s",
        account_key, _COUPON_KIND, updated, len(not_found), len(wrong_kind), skipped, src,
    )
    if wrong_kind:
        log.warning(
            "쿠폰 사용금액: %s가 아닌 쿠폰 %d건 — 재시도해도 안 붙는다(계약 확인 필요): %s",
            _COUPON_KIND, len(wrong_kind), wrong_kind[:10],
        )
    return {
        "updated": updated,
        "not_found": len(not_found),
        "not_found_coupon_ids": not_found[:50],  # 진단용 상한(응답 비대화 방지)
        "wrong_kind": len(wrong_kind),
        "wrong_kind_coupon_ids": wrong_kind[:50],
        "skipped": skipped,
        "deduped": stats.get("deduped", 0),
        "account_key": account_key,
        "source": src,
    }
