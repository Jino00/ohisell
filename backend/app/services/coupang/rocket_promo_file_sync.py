# rocket_promo_file_sync.py — 제안서 엑셀 → 프로모션 매칭 + 상품별 할인액 적재 (Harness, D-CPP-10)
#
# 조합: clients/coupang/rocket_promo_file.py(순수 파서 SA) + DB(프로모션·할인항목).
# 하는 일 둘:
#   ① 매칭 A — 파일명의 기간 → coupang_rocket_promotion(start_at/end_at의 **날짜부**)로 request_id 찾기
#   ② 적재    — 그 프로모션의 할인 항목을 파일 내용으로 **통째 교체**(snapshot)
#
# ★왜 서버가 매칭하나(페처가 아니라): 매칭 규칙은 틀리면 분담금이 엉뚱한 행사에 붙는 규칙이라
#   테스트가 있어야 한다. 페처(Mac)는 "폴더 읽어서 파일명+행 그대로 push"만 한다 — 규칙은 여기 하나뿐.
# ★멱등: 같은 파일을 다시 보내도 결과가 같다. 파일에서 빠진 SKU는 삭제된다(유령 방지).
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.clients.coupang import rocket_promo_file as parser
from app.models import CoupangPromoDiscountItem, CoupangRocketPromotion
from app.utils.kst import kst_now

log = logging.getLogger(__name__)


def _promo_period(p: CoupangRocketPromotion) -> tuple[date | None, date | None]:
    """프로모션 기간의 **날짜부**. 파일명은 초를 담지 못하므로 날짜로만 견준다.

    ★start_at은 초 단위로 보존돼 있다(예 07-24 00:01:00). 파일명 260724와 견주려면 date()로 내린다.
    """
    return (p.start_at.date() if p.start_at else None,
            p.end_at.date() if p.end_at else None)


def _norm(s: str | None) -> str:
    """라벨 비교용 정규화 — NFC + 공백/언더바 제거."""
    return parser.normalize_name(s or "").replace(" ", "").replace("_", "")


def match_promotion(
    db: Session, vendor_id: str, period_from: date, period_to: date, label: str
) -> tuple[str | None, str]:
    """기간(+동률 시 라벨)으로 프로모션을 찾는다. 반환 (request_id|None, 사유).

    ★기간이 같은 프로모션이 실제로 존재한다(2026-08-05 실측: 계약 2385997·2386000이 둘 다
      07-24~07-26). 그래서 기간만으로는 못 가르고 라벨이 필요하다. 라벨로도 못 가르면
      **추측하지 않고 None**을 돌려준다 — 틀린 행사에 붙은 분담금은 어디서도 대사되지 않는다.
    """
    cands = [
        p for p in db.query(CoupangRocketPromotion)
        .filter(CoupangRocketPromotion.vendor_id == vendor_id).all()
        if _promo_period(p) == (period_from, period_to)
    ]
    if not cands:
        return None, "기간에 맞는 프로모션 없음"
    if len(cands) == 1:
        return cands[0].request_id, "기간 일치"

    lab = _norm(label)
    hit = [
        p for p in cands
        if lab and (_norm(p.promotion_name)[:4] in lab or lab[:4] in _norm(p.promotion_name))
    ]
    if len(hit) == 1:
        return hit[0].request_id, "기간+라벨 일치"
    return None, f"기간이 같은 프로모션 {len(cands)}건 — 라벨로 못 가름"


def ingest_promo_discount_files(
    db: Session, vendor_id: str, files: list[dict]
) -> dict:
    """제안서 파일들 → 상품별 할인액 적재.

    files: [{"file_name": "instant_upload_template_260723_260815_플립폴드8시리즈",
             "file_mtime": "2026-07-23T11:26:00", "rows": [{product_number, discount_type, discount_value}, ...]}]
    반환: {matched:[...], unmatched:[...], items_ingested, items_removed}
      ★unmatched를 **응답과 로그에 반드시 남긴다**: 파일을 넣었는데 조용히 안 붙으면
        분담금이 0으로 남아 이익이 과대해진다(이 프로젝트에서 "낡음은 조용하다"가 반복됐다).
    """
    now = kst_now()
    matched: list[dict] = []
    unmatched: list[dict] = []
    total_in = 0
    total_removed = 0

    for f in files or []:
        if not isinstance(f, dict):
            continue
        raw_name = str(f.get("file_name") or "")
        meta = parser.parse_file_name(raw_name)
        if meta is None:
            unmatched.append({"file_name": parser.normalize_name(raw_name), "reason": "파일명 규칙 밖"})
            continue

        stats: dict = {}
        recs = parser.parse_discount_rows(f.get("rows") or [], stats=stats)
        if not recs:
            unmatched.append({"file_name": meta["name"], "reason": "유효한 할인 행 없음",
                              "skipped": stats.get("skipped", 0)})
            continue

        request_id, why = match_promotion(
            db, vendor_id, meta["period_from"], meta["period_to"], meta["label"]
        )
        if request_id is None:
            unmatched.append({
                "file_name": meta["name"], "reason": why,
                "period": f"{meta['period_from']}~{meta['period_to']}", "rows": len(recs),
            })
            log.warning(
                "제안서 미매칭: %s (기간 %s~%s, 행 %d) — %s",
                meta["name"], meta["period_from"], meta["period_to"], len(recs), why,
            )
            continue

        mtime = _parse_dt(f.get("file_mtime"))
        # 파일에서 빠진 SKU 제거(snapshot) — 제안서가 바뀌면 옛 항목이 유령으로 남으면 안 된다.
        #   recs가 비면 위에서 이미 continue 했으므로 keep은 항상 비어 있지 않다.
        keep = {r["product_number"] for r in recs}
        removed = (
            db.query(CoupangPromoDiscountItem)
            .filter(
                CoupangPromoDiscountItem.request_id == request_id,
                ~CoupangPromoDiscountItem.product_number.in_(keep),
            )
            .delete(synchronize_session=False)
        )
        total_removed += removed or 0

        for r in recs:
            row = (
                db.query(CoupangPromoDiscountItem)
                .filter(
                    CoupangPromoDiscountItem.request_id == request_id,
                    CoupangPromoDiscountItem.product_number == r["product_number"],
                )
                .first()
            )
            if row is None:
                row = CoupangPromoDiscountItem(
                    request_id=request_id, product_number=r["product_number"]
                )
                db.add(row)
            row.discount_type = r["discount_type"]
            row.discount_value = r["discount_value"]
            row.source_file = meta["name"][:300]
            row.file_mtime = mtime
            row.synced_at = now
        total_in += len(recs)
        matched.append({
            "file_name": meta["name"], "request_id": request_id, "why": why,
            "items": len(recs), "skipped": stats.get("skipped", 0),
        })

    db.commit()
    log.info(
        "제안서 할인액 ingest: vendor=%s 매칭 %d파일 %d항목 · 미매칭 %d파일 · 제거 %d",
        vendor_id, len(matched), total_in, len(unmatched), total_removed,
    )
    return {
        "vendor_id": vendor_id,
        "matched": matched,
        "unmatched": unmatched,
        "items_ingested": total_in,
        "items_removed": total_removed,
    }


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
