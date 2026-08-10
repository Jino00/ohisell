# rocket_cost_map.py — 쿠팡 로켓배송(1P) 원가 브리지 매핑 Harness (트랙 rocket-1p S4.5b, D-13)
#
# 배경(ref 20b §3): 1P 발주상세 상품번호/바코드 ≠ product_master 카탈로그 키 → 자동 조인 불가.
#   일회성 수동 브리지(RocketProductCostMap: product_number → internal_sku)를 채워야 S4.5c 원가 결합 가능.
#
# 단일 책임 조합(원칙18):
#   ① 순수 SA `suggest_skus(name, candidates)` — difflib 이름유사도 랭커(DB·HTTP 無, 단위테스트 가능).
#   ② Harness `list_unmapped/list_mappings/upsert_mapping/delete_mapping` — DB 오케스트레이션.
# net_profit 불변(S4.5b는 매핑만, 원가 결합은 S4.5c). 사용자 확정 입력 — 읽기전용 예외(이 테이블 한정).
from __future__ import annotations

import logging
from difflib import SequenceMatcher

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    CoupangRocketPurchaseOrderItem,
    ProductMaster,
    RocketProductCostMap,
)
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ★status는 둘뿐이고 **뜻이 하나씩**이다(2026-08-10, Jino 결정. 마이그 e4c7a1b8d206).
#   'confirmed' = internal_sku에 연결됨 → product_master.cost_price가 원가.
#   'excluded'  = **연결 안 함 · 재제안 방지 · 손익에서는 「모름」.**
#
# ★★옛 이름 `ignored`는 세 모듈에서 서로 다르게 읽혔다 — 두 곳은 «원가 0원·해결됨»,
#   한 곳은 «원가 미상». 그래서 2026-06-17 일괄 매핑이 «후보를 못 찾은» 22건을 그 값으로
#   찍자 두 엔진이 **원가 0원 = 전액 이익**으로 셌다(90일 발주 실측 진짜 원가 3,311,826원).
#   이제 「원가 0원」 해석은 코드 어디에도 없다 — 모르면 모른다고 한다.
_VALID_STATUS = {"confirmed", "excluded"}

# 「제외」는 **사람만** 쓴다. 자동 매핑이 이 값을 쓰지 못하게 막는 것이 위 사고의 구조적 수리다.
#   ★prod 실측(2026-08-10): 사유 전수를 봐도 사람이 제외를 결정한 흔적은 **한 건도 없었다** —
#     그 상태를 만든 것은 오직 배치였고, 그것도 「결정」이 아니라 「매칭 실패」였다.
_AUTO_MATCH_METHODS = {"suggested", "auto"}


# ════════════════════════════════════════════════
# ① 순수 SA — 이름 유사도 제안 (DB 무관)
# ════════════════════════════════════════════════
def _norm(s: str | None) -> str:
    """비교용 정규화: 소문자·공백 제거(상품명 표기 흔들림 흡수)."""
    return "".join((s or "").lower().split())


def suggest_skus(name: str | None, candidates: list[dict], top_n: int = 3) -> list[dict]:
    """발주상세 product_name과 product_master 후보들의 이름 유사도 상위 top_n 반환(순수 함수).

    candidates: [{"internal_sku","product_name","cost_price"}, ...] (product_master 스냅샷).
    반환: [{"internal_sku","product_name","cost_price","score"(0~1, 4자리 반올림)}, ...] score 내림차순.
    A2(fuzzy)는 자동 확정이 아니라 **사람 확정 보조**(D-13: A1 수동 브리지). 취약성 가드 = 사용자 최종 결정.
    """
    nq = _norm(name)
    if not nq or not candidates:
        return []
    scored = []
    for c in candidates:
        score = SequenceMatcher(None, nq, _norm(c.get("product_name"))).ratio()
        scored.append({**c, "score": round(score, 4)})
    scored.sort(key=lambda x: (-x["score"], x.get("internal_sku") or ""))
    return scored[:top_n]


# ════════════════════════════════════════════════
# ② Harness — DB 조회/확정
# ════════════════════════════════════════════════
def _master_candidates(db: Session) -> list[dict]:
    """product_master 전체를 유사도 후보 스냅샷으로 1회 로드(N×스캔 회피, 원칙18-8)."""
    rows = db.query(
        ProductMaster.internal_sku, ProductMaster.product_name, ProductMaster.cost_price
    ).all()
    return [
        {"internal_sku": r.internal_sku, "product_name": r.product_name, "cost_price": r.cost_price}
        for r in rows
    ]


def list_unmapped(
    db: Session, vendor_id: str | None = None, limit: int = 200, suggest: bool = True
) -> dict:
    """발주상세에 있으나 매핑(confirmed/excluded) 없는 상품번호 목록 + 이름유사도 제안.

    각 항목: product_number·대표 product_name·barcode·총 발주수량(order_qty 합)·등장 PO 수·suggestions.
    매핑 테이블에 행이 있는 상품번호(confirmed/excluded 모두)는 제외 — 재제안 방지.
    limit: 제안 계산 비용 보호(미매핑 × 마스터 894 SequenceMatcher). suggest=False면 제안 생략(빠름).
    """
    mapped = {row[0] for row in db.query(RocketProductCostMap.product_number).all()}

    q = db.query(
        CoupangRocketPurchaseOrderItem.product_number,
        func.sum(CoupangRocketPurchaseOrderItem.order_qty).label("total_qty"),
        func.count(func.distinct(CoupangRocketPurchaseOrderItem.purchase_order_seq)).label("po_count"),
        func.max(CoupangRocketPurchaseOrderItem.product_name).label("product_name"),
        func.max(CoupangRocketPurchaseOrderItem.barcode).label("barcode"),
    )
    if vendor_id:
        q = q.filter(CoupangRocketPurchaseOrderItem.vendor_id == vendor_id)
    q = q.group_by(CoupangRocketPurchaseOrderItem.product_number)
    # 총 발주수량 큰 순(원가 영향 큰 SKU 우선 매핑) → 동률은 상품번호.
    q = q.order_by(func.sum(CoupangRocketPurchaseOrderItem.order_qty).desc(),
                   CoupangRocketPurchaseOrderItem.product_number)

    rows = [r for r in q.all() if r.product_number not in mapped]
    total_unmapped = len(rows)
    rows = rows[: max(0, limit)]

    candidates = _master_candidates(db) if suggest else []
    # ★후보의 내부 SKU에 **이미 몇 개 상품번호가 붙어 있는지**(2026-08-07, Jino).
    #   왜: 이 저장소의 내부 SKU 이름은 기종을 지목하는데 실제로는 **기종 공용 원가**인
    #   경우가 있다 — 라이브 실측 `OHI-TGLASS-IP17PRO`("아이폰17 Pro")에 붙은 12개가
    #   실제로는 아이폰12·13·14·16이다(납품단가는 전부 10,740원으로 같아 원가 공유 자체는
    #   일관되지만, **이름만 보면 다른 기종 원가를 붙이는 것으로 오해**한다).
    #   개수를 함께 보이면 이름이 뭐든 "이건 공용 원가구나"가 드러난다 — 이름을 고치는 것은
    #   실물 지식이 필요하지만, **오해를 막는 것은 지금 할 수 있다.**
    mapped_counts: dict[str, int] = {}
    if suggest:
        for isku, cnt in (
            db.query(RocketProductCostMap.internal_sku, func.count())
            .filter(RocketProductCostMap.status == "confirmed",
                    RocketProductCostMap.internal_sku.isnot(None))
            .group_by(RocketProductCostMap.internal_sku).all()
        ):
            mapped_counts[str(isku)] = int(cnt or 0)

    items = []
    for r in rows:
        sugg = suggest_skus(r.product_name, candidates) if suggest else []
        for c in sugg:
            # 0 = 아직 아무 상품번호도 안 붙은 내부 SKU(전용). 1+ = 이미 공용으로 쓰이는 중.
            c["already_mapped_count"] = mapped_counts.get(str(c.get("internal_sku")), 0)
        item = {
            "product_number": r.product_number,
            "product_name": r.product_name,
            "barcode": r.barcode,
            "total_order_qty": int(r.total_qty or 0),
            "po_count": int(r.po_count or 0),
            "suggestions": sugg,
        }
        items.append(item)
    return {
        "vendor_id": vendor_id,
        "total_unmapped": total_unmapped,  # 매핑 안 된 상품번호 총수(limit 전)
        "returned": len(items),
        "items": items,
    }


def list_mappings(db: Session) -> dict:
    """확정/제외 매핑 전체 목록(감사·프론트 표시). internal_sku의 현재 cost_price 조인해서 함께 반환."""
    cost_by_sku = {
        r.internal_sku: r.cost_price
        for r in db.query(ProductMaster.internal_sku, ProductMaster.cost_price).all()
    }
    out = []
    for m in db.query(RocketProductCostMap).order_by(RocketProductCostMap.product_number).all():
        out.append({
            "product_number": m.product_number,
            "internal_sku": m.internal_sku,
            "status": m.status,
            "match_method": m.match_method,
            "barcode": m.barcode,
            "product_name": m.product_name,
            "cost_price": cost_by_sku.get(m.internal_sku) if m.internal_sku else None,
            "note": m.note,
        })
    return {"count": len(out), "mappings": out}


def upsert_mapping(
    db: Session,
    product_number: str,
    internal_sku: str | None = None,
    status: str = "confirmed",
    match_method: str = "manual",
    note: str | None = None,
) -> dict:
    """상품번호 → internal_sku 매핑 확정(또는 'excluded' 연결 제외). 멱등 upsert.

    검증: status ∈ {confirmed, excluded}. confirmed면 internal_sku 필수 + product_master 존재 확인.
      excluded면 internal_sku 무시(None 저장). product_name/barcode는 발주상세에서 자동 캐시(라벨용).
    반환: 저장된 매핑 dict(+resolved cost_price). 검증 실패는 ValueError(라우터가 400/422로 변환).

    ★★`excluded`는 **사람이 사유를 적어야만** 쓸 수 있다(2026-08-10). 두 가드:
      ①`note` 필수 — 사유 없는 제외는 나중에 «결정이었나 실패였나»를 가릴 수 없다.
        2026-06-17 배치가 남긴 `no suggestion or low score` 22건이 정확히 그 상태였고,
        그것을 「결정」으로 읽은 화면이 정상 판매 상품을 손익 밖에 방치했다.
      ②`match_method`가 자동 계열이면 거부 — 자동 매핑에 이 값을 쓸 권한을 주지 않는다.
    ★사유 문자열을 **파싱해서** 자동 분류하지는 않는다. 그건 또 하나의 추측을 코드에 굳히는
      것이다 — 사유는 사람이 읽으라고 남긴다.
    """
    pn = (product_number or "").strip()
    if not pn:
        raise ValueError("product_number 필요")
    if status not in _VALID_STATUS:
        raise ValueError(f"status는 {sorted(_VALID_STATUS)} 중 하나")
    if status == "excluded":
        if not (note or "").strip():
            raise ValueError(
                "excluded(제외)는 사유(note)가 필요합니다 — 사유 없는 제외는 나중에 "
                "«사람의 결정»과 «자동 매핑 실패»를 가를 수 없습니다"
            )
        if (match_method or "").strip().lower() in _AUTO_MATCH_METHODS:
            raise ValueError(
                f"excluded(제외)는 자동 매핑이 쓸 수 없습니다(match_method={match_method}) — "
                "사람이 결정한 것만 제외로 남깁니다"
            )

    resolved_cost = None
    if status == "confirmed":
        sku = (internal_sku or "").strip()
        if not sku:
            raise ValueError("confirmed 매핑은 internal_sku 필요")
        master = db.query(ProductMaster).filter(ProductMaster.internal_sku == sku).first()
        if master is None:
            raise ValueError(f"product_master에 없는 internal_sku: {sku}")
        resolved_cost = master.cost_price
        internal_sku = sku
    else:  # excluded — 연결하지 않는다(원가는 «모름»으로 남는다)
        internal_sku = None

    # 발주상세에서 라벨 캐시(대표 product_name·barcode) 채움 — 매핑 목록 가독성.
    label = (
        db.query(
            func.max(CoupangRocketPurchaseOrderItem.product_name),
            func.max(CoupangRocketPurchaseOrderItem.barcode),
        )
        .filter(CoupangRocketPurchaseOrderItem.product_number == pn)
        .first()
    )
    cached_name = label[0] if label else None
    cached_barcode = label[1] if label else None

    row = (
        db.query(RocketProductCostMap)
        .filter(RocketProductCostMap.product_number == pn)
        .first()
    )
    if row is None:
        row = RocketProductCostMap(product_number=pn)
        db.add(row)
    row.internal_sku = internal_sku
    row.status = status
    row.match_method = match_method
    row.note = note
    if cached_name is not None:
        row.product_name = cached_name
    if cached_barcode is not None:
        row.barcode = cached_barcode
    row.updated_at = kst_now()
    db.commit()
    log.info("rocket cost-map upsert: pn=%s sku=%s status=%s", pn, internal_sku, status)
    return {
        "product_number": pn,
        "internal_sku": internal_sku,
        "status": status,
        "match_method": match_method,
        "cost_price": resolved_cost,
        "product_name": row.product_name,
        "barcode": row.barcode,
        "note": note,
    }


def delete_mapping(db: Session, product_number: str) -> dict:
    """매핑 삭제(상품번호를 다시 미매핑 상태로). 없으면 deleted=0."""
    pn = (product_number or "").strip()
    n = (
        db.query(RocketProductCostMap)
        .filter(RocketProductCostMap.product_number == pn)
        .delete()
    )
    db.commit()
    log.info("rocket cost-map delete: pn=%s deleted=%d", pn, n)
    return {"product_number": pn, "deleted": n}
