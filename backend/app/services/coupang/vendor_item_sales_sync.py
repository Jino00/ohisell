# vendor_item_sales_sync.py — 쿠팡 Wing 판매분석 «일자×옵션» 정본 매출 축 (D-CPP-35).
#
# 소스: m-wing.coupang.com /tenants/rfm-ss/api/business-insight/vi-detail-search (2026-08-10 정찰).
# 런타임 경계(D-5와 동일): Mac 헤드풀 페처가 브라우저측 fetch → prod push → 이 SA가 적재.
#   백엔드에서 쿠팡을 직접 호출하지 않는다(cf_clearance 재생 불가).
#
# 단일 책임(원칙18-1): 옵션 행 적재(snapshot upsert) + **보존식 대조**(Σ옵션 == 요약축) 조회.
#   신선도 판정·배너 표시는 scheduler_health/watchdog이 한다(이 SA는 사실만 계산).
#
# ★보존식을 여기 두는 이유: ingest 시점에 거부하면 요약축 도착 타이밍과 결합된다(옵션축이
#   먼저 오면 «요약축 없음»으로 항상 거부). 일회성 스크립트는 방치된다 — 2026-08-10 오전에
#   만든 원가 드리프트 검사기가 완성됐는데도 아무도 안 불렀던 전례가 있다(교훈 #220).
#   그래서 «읽기 전용 조회 함수»로 두고, 헬스가 매 요청 시점에 부른다.
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import CoupangVendorItemSalesDaily, CoupangVendorSummaryDaily

log = logging.getLogger(__name__)

# 등록유형(ref 18): NORMAL=3P 마켓플레이스, RFM=로켓그로스(RG). 요약축과 같은 어휘를 쓴다.
_TYPES = ("NORMAL", "RFM")


def _upsert_row(
    db: Session,
    account_key: str,
    sale_date: date,
    vendor_item_id: str,
    registration_type: str,
    gmv: int,
    units_sold: int,
    total_orders: int,
    item_name: str | None,
    product_id: str | None,
    raw_metrics: dict | None,
    last_refresh: str | None,
) -> None:
    """(account_key, sale_date, vendor_item_id) 1행 upsert — 같은 키를 다시 받으면 확정치로 교체."""
    row = (
        db.query(CoupangVendorItemSalesDaily)
        .filter(
            CoupangVendorItemSalesDaily.account_key == account_key,
            CoupangVendorItemSalesDaily.sale_date == sale_date,
            CoupangVendorItemSalesDaily.vendor_item_id == vendor_item_id,
        )
        .first()
    )
    if row is None:
        row = CoupangVendorItemSalesDaily(
            account_key=account_key, sale_date=sale_date, vendor_item_id=vendor_item_id,
        )
        db.add(row)
    row.registration_type = registration_type
    row.gmv = gmv
    row.units_sold = units_sold
    row.total_orders = total_orders
    row.item_name = item_name
    row.product_id = product_id
    # 원본 보존 — 실패해도 적재 자체를 죽이지 않는다(직렬화 불가 값이 섞여도 매출은 들어가야 한다).
    if raw_metrics is not None:
        try:
            row.raw_metrics = json.dumps(raw_metrics, ensure_ascii=False)
        except (TypeError, ValueError):
            log.warning(
                "raw_metrics 직렬화 실패 — 매출만 적재 (%s %s %s)",
                account_key, sale_date, vendor_item_id,
            )
            row.raw_metrics = None
    if last_refresh is not None:
        row.last_refresh = last_refresh


def ingest_vendor_item_sales(db: Session, account_key: str, rows: list[dict]) -> dict:
    """Mac 페처가 보낸 «일자×옵션» 행을 upsert.

    rows: [{"date": date, "vendor_item_id": str, "registration_type": "NORMAL"|"RFM",
            "gmv": int, "units_sold": int, "total_orders": int,
            "item_name"?: str|None, "product_id"?: str|None,
            "raw_metrics"?: dict|None, "last_refresh"?: str|None}]

    ★멱등이다(upsert). 백필·재조회를 반복해도 중복 행이 GMV를 부풀리지 않는다 —
      돈 축이 오염되면 보존식이 조용히 통과하면서 손익만 틀린다.

    ★★«배치 안»의 중복도 먼저 접는다(적대 리뷰 P1-1, 2026-08-10). 앱 세션은 `autoflush=False`라
      (`database.py`) 같은 배치에서 `db.add()`한 행이 뒤 행의 `query().first()`에 **안 보인다** →
      두 번째 행이 새로 INSERT되고 commit에서 UNIQUE 위반 → **HTTP 500 + 그 회차 전량 롤백**.
      호출 간 멱등만 지키고 배치 내 멱등을 안 지키면, 페이지네이션 경계 중복(대상이
      `sortBy=GMV DESC`의 준실시간 회전 데이터다) 한 번에 그날 수집이 통째로 날아간다.
      리뷰가 재현했다: 서버가 pageNumber를 무시하는 경우 3페이지 → 유니크 3개에 행 9개.
      마지막 값이 이긴다 — 뒤에 온 것이 더 확정된 값이라는 upsert 정책과 같은 방향이다.
    """
    deduped: dict[tuple[str, str], dict] = {}
    for r in rows:
        rt = str(r.get("registration_type") or "")
        if rt not in _TYPES:
            continue  # 알 수 없는 등록유형은 무시(스키마 방어 — 요약축과 같은 정책)
        vid = str(r.get("vendor_item_id") or "").strip()
        if not vid:
            continue  # 옵션ID 없는 행은 조인축이 없어 쓸 수 없다
        day = r["date"]
        key = (day.isoformat() if hasattr(day, "isoformat") else str(day), vid)
        if key in deduped:
            log.info("배치 내 중복 접기: %s %s (마지막 값 채택)", key[0], vid)
        deduped[key] = {**r, "registration_type": rt, "vendor_item_id": vid}

    n = 0
    for r in deduped.values():
        rt = r["registration_type"]
        vid = r["vendor_item_id"]
        _upsert_row(
            db,
            account_key=account_key,
            sale_date=r["date"],
            vendor_item_id=vid,
            registration_type=rt,
            gmv=int(r.get("gmv") or 0),
            units_sold=int(r.get("units_sold") or 0),
            total_orders=int(r.get("total_orders") or 0),
            item_name=(str(r["item_name"])[:300] if r.get("item_name") else None),
            product_id=(str(r["product_id"])[:20] if r.get("product_id") else None),
            raw_metrics=r.get("raw_metrics") if isinstance(r.get("raw_metrics"), dict) else None,
            last_refresh=(str(r["last_refresh"]) if r.get("last_refresh") is not None else None),
        )
        n += 1
    db.commit()
    # `ingested`는 **실제 upsert한 유니크 행 수**다(시도한 행 수가 아니다) — 로그로 중복·누락을
    # 추정하려면 두 숫자가 달라야 한다(적대 리뷰 P2-8).
    log.info("vendor-item-sales ingest: account=%s received=%d upserted=%d",
             account_key, len(rows), n)
    return {"account_key": account_key, "received": len(rows), "ingested": n}


# ════════════════════════════════════════════════
# 보존식 — Σ옵션 GMV == 요약축 GMV
# ════════════════════════════════════════════════
# 허용오차 0인 근거: 2026-08-10 라이브 정찰 3회 전부 차 0이었다
#   (08-01~08-09 450,700=450,700 · 08-05 188,800=188,800 · 08-07 86,500=86,500).
#   둘 다 쿠팡이 같은 소스에서 낸 정수 원 단위 값이므로 반올림 여지가 없다. 여유를 두면
#   «조금 틀린 것»을 영구히 눈감아 준다 — 어긋나면 그건 우리가 알아야 할 사실이다.
CONSERVATION_TOLERANCE = 0


def conservation_check(
    db: Session, account_key: str, start: date, end: date
) -> dict:
    """[start, end] 계정·일자·등록유형별로 Σ옵션 GMV ↔ 요약축 GMV 대조(읽기 전용).

    반환: {
      "account_key", "start", "end",
      "compared": int,          # 양쪽에 데이터가 다 있어 실제로 비교한 (일자, 유형) 칸 수
      "mismatch": [{date, registration_type, option_gmv, summary_gmv, diff}, ...],
      "option_only": [...],     # 옵션축엔 있는데 요약축에 없는 칸
      "summary_only": [...],    # 요약축엔 있는데 옵션축에 없는 칸(= 옵션 수집이 아직 안 온 날)
    }

    ★«없음»과 «어긋남»을 가른다. 둘을 한 숫자로 뭉치면 «아직 안 왔다»가 «틀렸다»로 보이거나
      그 반대가 되고, 발견 0건과 실행 안 됨이 같은 모양이 된다(교훈 #123).
    """
    opt_rows = (
        db.query(
            CoupangVendorItemSalesDaily.sale_date,
            CoupangVendorItemSalesDaily.registration_type,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.gmv), 0),
        )
        .filter(
            CoupangVendorItemSalesDaily.account_key == account_key,
            CoupangVendorItemSalesDaily.sale_date >= start,
            CoupangVendorItemSalesDaily.sale_date <= end,
        )
        .group_by(
            CoupangVendorItemSalesDaily.sale_date,
            CoupangVendorItemSalesDaily.registration_type,
        )
        .all()
    )
    sum_rows = (
        db.query(
            CoupangVendorSummaryDaily.summary_date,
            CoupangVendorSummaryDaily.registration_type,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorSummaryDaily.gmv), 0),
        )
        .filter(
            CoupangVendorSummaryDaily.account_key == account_key,
            CoupangVendorSummaryDaily.summary_date >= start,
            CoupangVendorSummaryDaily.summary_date <= end,
        )
        .group_by(
            CoupangVendorSummaryDaily.summary_date,
            CoupangVendorSummaryDaily.registration_type,
        )
        .all()
    )

    def _key(d, rt) -> tuple[str, str]:
        return (d.isoformat() if hasattr(d, "isoformat") else str(d), str(rt))

    opt = {_key(d, rt): int(g or 0) for d, rt, g in opt_rows}
    summ = {_key(d, rt): int(g or 0) for d, rt, g in sum_rows}

    mismatch: list[dict] = []
    option_only: list[dict] = []
    summary_only: list[dict] = []
    compared = 0

    for k in sorted(set(opt) | set(summ)):
        d, rt = k
        if k in opt and k in summ:
            compared += 1
            diff = opt[k] - summ[k]
            if abs(diff) > CONSERVATION_TOLERANCE:
                mismatch.append({
                    "date": d, "registration_type": rt,
                    "option_gmv": opt[k], "summary_gmv": summ[k], "diff": diff,
                })
        elif k in opt:
            option_only.append({"date": d, "registration_type": rt, "option_gmv": opt[k]})
        else:
            summary_only.append({"date": d, "registration_type": rt, "summary_gmv": summ[k]})

    return {
        "account_key": account_key,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "compared": compared,
        "mismatch": mismatch,
        "option_only": option_only,
        "summary_only": summary_only,
    }


def latest_sale_date(db: Session, account_key: str) -> date | None:
    """이 계정 옵션축의 최신 sale_date. 없으면 None(= 한 번도 수집 안 됨 → 헬스가 no_data)."""
    return (
        db.query(sqlfunc.max(CoupangVendorItemSalesDaily.sale_date))
        .filter(CoupangVendorItemSalesDaily.account_key == account_key)
        .scalar()
    )
