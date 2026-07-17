# 오하이테크(1P 로켓배송) 광고센터 일별 광고비 ingest Harness.
# 트랙: docs/tracks/active/track_coupang-ohitech-ad.md (D-8/D-9/D-10).
#
# 역할(단일 책임): Mac CDP 페처가 report/SALES에서 뽑은 일별 광고비를
#   coupang_ad_report(sell_type='Retail', vendor_id=오하이테크 A코드)로 snapshot upsert.
#   → rocket_intelligence._agg_rocket_ad가 sell_type='Retail'로 자동 합산 → 1P 순이익 차감(D-4).
#
# 머니룰(D-10): ad_spend = ALL_DELIVERED_AD_COST(전체, 비-PA 포함). 3P/RG net_profit과 동일하게
#   실제 지불 총액을 차감(intelligence.py:763-783 비-PA 추가차감과 일관). report/SALES는 impressions/
#   clicks/orders/sales_qty를 주지 않으므로 0. conversion_revenue = AD_ATTRIBUTED_SALES.
#
# ★클로버 방지(리뷰 P1①②): coupang_ad_report는 PA-XLSX 업로더(coupang_report.py·ad_costs.py)도
#   같은 (report_date,'Retail',vendor_id) 키에 쓸 수 있다(last-writer-wins). 안전장치 2겹:
#   ① 차감은 vendor 스코프 — prod env COUPANG_ROCKET_VENDOR_ID='A01029796'로 _agg_rocket_ad가
#      이 vendor만 합산(타 벤더 stray Retail 행 무시). ② **A01029796 계정은 PA-XLSX 수동 업로드 금지**
#      (이 fetcher의 ALL_DELIVERED 값을 PA-only로 덮어써 순이익을 과대화). 1P 광고는 이 경로가 단일 소스.
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import CoupangAdReport, CoupangWingCookie
from app.utils.kst import kst_now

log = logging.getLogger("ohitech_ad_sync")

_SELL_TYPE = "Retail"  # 1P 로켓배송 (rocket_intelligence.ROCKET_AD_SELL_TYPE와 일치)
# 갱신 트리거/heartbeat 상태행 식별자(coupang_wing_cookie 재사용 — rocket/adcost 패턴).
# 오픽스 광고(COUPANG_ADS)·로켓 발주(COUPANG_ROCKET_FETCHER)와 분리 → 독립 버튼·독립 만료측정.
_OHITECH_AD_ACCOUNT = "COUPANG_OHITECH_AD"


def _to_date(v) -> date | None:
    """'YYYY-MM-DD' 또는 date → date. 실패 시 None(해당 행 스킵)."""
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_dec(v) -> Decimal:
    """방어적 Decimal 변환(None/빈값/잘못된 값 → 0)."""
    try:
        return Decimal(str(v if v is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def ingest_ohitech_ad_cost(db: Session, vendor_id: str, days: list[dict]) -> dict:
    """오하이테크 일별 광고비 → coupang_ad_report(Retail) per-day upsert(멱등).

    days[i] = {"date": "YYYY-MM-DD", "ad_spend": <전체 ALL_DELIVERED, D-10>,
               "conv_sales": <AD_ATTRIBUTED_SALES, optional>}.
    (report_date, sell_type='Retail', vendor_id) 키로 교체 — report/SALES는 확정 과거일만
    반환하므로 윈도우 밖 날짜는 건드리지 않는다(전체 삭제 금지, 다른 윈도우 백필 보존).
    """
    upserted = 0
    skipped = 0
    seen_dates: list[date] = []
    for d in days:
        if not isinstance(d, dict):
            skipped += 1
            continue
        ad_date = _to_date(d.get("date"))
        if ad_date is None:
            skipped += 1
            continue
        ad_spend = _to_dec(d.get("ad_spend"))       # 전체(D-10) — _agg_rocket_ad 차감값
        conv = _to_dec(d.get("conv_sales"))
        existing = (
            db.query(CoupangAdReport)
            .filter(
                CoupangAdReport.report_date == ad_date,
                CoupangAdReport.sell_type == _SELL_TYPE,
                CoupangAdReport.vendor_id == vendor_id,
            )
            .first()
        )
        if existing:
            existing.ad_spend = ad_spend
            existing.conversion_revenue = conv
        else:
            db.add(
                CoupangAdReport(
                    report_date=ad_date,
                    sell_type=_SELL_TYPE,
                    vendor_id=vendor_id,
                    impressions=0,
                    clicks=0,
                    ad_spend=ad_spend,
                    orders=0,
                    sales_qty=0,
                    conversion_revenue=conv,
                )
            )
        upserted += 1
        seen_dates.append(ad_date)

    db.commit()
    result = {
        "vendor_id": vendor_id,
        "sell_type": _SELL_TYPE,
        "upserted": upserted,
        "skipped": skipped,
        "date_from": min(seen_dates).isoformat() if seen_dates else None,
        "date_to": max(seen_dates).isoformat() if seen_dates else None,
    }
    log.info("오하이테크 광고비 적재 %s Retail %d건 (%s~%s, skip=%d)",
             vendor_id, upserted, result["date_from"], result["date_to"], skipped)
    return result


# ════════════════════════════════════════════════
# 오하이테크 광고 페처 갱신 트리거 / heartbeat (S3 버튼-poll, rocket/adcost 패턴 복제)
#   - UI '광고비 갱신' 버튼이 refresh_requested_at set → Mac poll 데몬이 claim(소비) → fetch·push.
#   - run 성공 시 mark_fetch_success → last_success_at 갱신 → UI 폴링이 완료 감지(baseline 변화).
#   - 광고비는 prod 직접 fetch 불가(Akamai, D-4) → 이 버튼-poll이 유일한 갱신 경로.
# ════════════════════════════════════════════════
def _state_row(db: Session):
    return (
        db.query(CoupangWingCookie)
        .filter(CoupangWingCookie.account_key == _OHITECH_AD_ACCOUNT)
        .first()
    )


def _ensure_state_row(db: Session):
    row = _state_row(db)
    if row is None:
        row = CoupangWingCookie(account_key=_OHITECH_AD_ACCOUNT)
        db.add(row)
    return row


def request_refresh(db: Session) -> dict:
    """UI '광고비 갱신' 버튼 → 갱신 요청 플래그 set. Mac poll 데몬이 다음 폴에서 소비."""
    row = _ensure_state_row(db)
    row.refresh_requested_at = kst_now()
    db.commit()
    return {"requested": True, "requested_at": row.refresh_requested_at.isoformat()}


def refresh_status(db: Session) -> dict:
    """갱신 요청/완료 상태. UI 폴링·페처 소비 공용(민감값 없음).

    last_error_at=마지막 실패 시각(버튼 후 이 값이 올라가면 갱신 실패) — UI가 성공/실패 둘 중
    무엇이 왔는지 이 두 시각의 변화로 가른다. 없으면 실패를 못 보고 폴링 창을 헛기다린다.
    """
    row = _state_row(db)
    if row is None:
        return {"requested": False, "requested_at": None, "last_success_at": None,
                "status": "none", "last_error": None, "last_error_at": None}
    return {
        "requested": row.refresh_requested_at is not None,
        "requested_at": row.refresh_requested_at.isoformat() if row.refresh_requested_at else None,
        "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
        "status": row.status,
        "last_error": row.last_error,
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
    }


def claim_refresh(db: Session) -> dict:
    """페처가 갱신 요청을 '소비'(플래그 clear). 원자적 조건부 UPDATE(중복 claim 방지)."""
    from sqlalchemy import update

    res = db.execute(
        update(CoupangWingCookie)
        .where(CoupangWingCookie.account_key == _OHITECH_AD_ACCOUNT)
        .where(CoupangWingCookie.refresh_requested_at.isnot(None))
        .values(refresh_requested_at=None)
    )
    db.commit()
    return {"claimed": (res.rowcount or 0) > 0}


def mark_fetch_success(db: Session) -> None:
    """페처 run 성공 시 last_success_at 갱신(UI 폴링 완료 감지 + poll 23h 자동실행 기준)."""
    row = _ensure_state_row(db)
    row.last_success_at = kst_now()
    row.status = "green"
    row.last_error = None
    row.last_error_at = None  # 성공 = 실패 흔적 클리어(안 지우면 오래된 실패가 화면에 남는다)
    db.commit()


def mark_fetch_error(db: Session, error: str) -> None:
    """페처 run 실패 보고 → last_error/last_error_at 기록(UI가 실패를 감지하는 유일 경로).

    ★존재 이유(PR #30이 광고비에서 먼저 고친 것과 같은 구멍): 페처가 갱신 요청을 claim한
    뒤(=플래그 이미 clear) 브라우저 에러로 죽으면 prod에 아무 흔적도 안 남았다. 성공에만
    시각(last_success_at)이 있고 실패엔 짝이 없어서 UI는 "실패"와 "아직 진행 중"을 구분할
    수단이 없었다 — 215초를 헛기다린 뒤 "Mac 응답 없음"이라는 뭉뚱그린 문구만 냈다.
    형제 mark_fetch_success의 짝.

    ★status는 일부러 건드리지 않는다(PR #30 codex 1R[P2]): status=red는 Layout 배너에서
    곧바로 "쿠키 만료(재설정 필요)" + 쿠키 재설정 CTA로 렌더된다(Layout.tsx:201/206).
    브라우저 크래시는 쿠키 문제가 아니라 재설정해도 헛수고다. 지속 실패는 워치독이
    last_success_at 경과로 잡는다(status 미의존).
    """
    row = _ensure_state_row(db)
    row.last_error = error[:300]  # 컬럼 한계 — 긴 스택트레이스로 보고 자체가 날아가면 안 된다
    row.last_error_at = kst_now()
    db.commit()
