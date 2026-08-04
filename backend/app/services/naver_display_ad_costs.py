# naver_display_ad_costs.py — ADVoost 쇼핑 · 성과형 디스플레이(GFA) 광고비 적재 SA
#
# 왜 별도 경로인가: 검색광고 리포트(/stat-reports)는 NCC(검색광고)만 덮는다. 같은 비즈머니에서
# 함께 빠지는 ADVoost 쇼핑(PMAX)·GFA는 리포트 축에 존재하지 않아 **실차감 내역이 유일한 수집
# 경로**다. 종전에는 사람이 GFA 콘솔에서 CSV를 받아 올렸고(source `gfa:쇼핑`), 그 업로드가
# 2026-06-04에 멈춘 뒤 **59일간 4,881,848원이 손익에서 빠져 있었다**(2026-08-03 발견).
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.naver_sa_ad_fetcher import (
    BIZMONEY_PROD_ADVOOST,
    BIZMONEY_PROD_DISPLAY,
    fetch_bizmoney_exhaust_daily,
)

log = logging.getLogger(__name__)

# 신규 source. 기존 수동 CSV의 `gfa:쇼핑`과 **접두사 `gfa:`를 공유**한다 —
# profit_calculator._get_gfa_ad_spend_daily가 `gfa:%`로 읽으므로 과거 행과 한 계열로 이어진다.
SOURCE_ADVOOST = "gfa:advoost"
SOURCE_DISPLAY = "gfa:da"
LEGACY_CSV_SOURCE = "gfa:쇼핑"

# prodInfoCd → source. NCC는 여기 없다(이미 `naver_sa:*`로 적재 중 — 넣으면 이중계상).
_PROD_TO_SOURCE = {
    BIZMONEY_PROD_ADVOOST: SOURCE_ADVOOST,
    BIZMONEY_PROD_DISPLAY: SOURCE_DISPLAY,
}


def _naver_channel_id(db: Session) -> int | None:
    row = db.execute(text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")).fetchone()
    return row[0] if row else None


def _legacy_csv_dates(db: Session, channel_id: int, date_from: date, date_to: date) -> set[str]:
    """수동 CSV(`gfa:쇼핑`)가 이미 채운 날짜들.

    ★이 날짜엔 쓰지 않는다: 2026-04-02~05-28 실측에서 CSV의 'ADVoost 쇼핑' 컬럼 = PMAX와
    **원 단위로 동일**했고, 05-29부터는 = PMAX + GFA 였다. 즉 CSV 행이 이미 같은 돈을 담고
    있어 그 위에 쓰면 그날 광고비가 2배가 된다.
    """
    rows = db.execute(
        text("""
            SELECT DISTINCT ad_date FROM ad_costs
            WHERE channel_id = :cid AND source = :src
              AND ad_date >= :since AND ad_date <= :until
        """),
        {"cid": channel_id, "src": LEGACY_CSV_SOURCE,
         "since": date_from.isoformat(), "until": date_to.isoformat()},
    ).fetchall()
    return {str(r[0]) for r in rows}


def sync_display_ad_costs(
    db: Session, date_from: date, date_to: date, *, dry_run: bool = False
) -> dict:
    """[date_from, date_to] 구간의 ADVoost·GFA 광고비를 `ad_costs`에 적재(멱등).

    Returns: {"written": 건수, "skipped_legacy": 날짜수, "total": Decimal, "by_source": {...}}
    """
    from app.routers.ad_costs import _upsert_ad_cost  # 순환 임포트 회피(라우터가 서비스를 씀)

    channel_id = _naver_channel_id(db)
    if not channel_id:
        raise RuntimeError("NAVER 채널이 없습니다 — channels 테이블 확인 필요")

    daily = fetch_bizmoney_exhaust_daily(date_from, date_to)
    skip_dates = _legacy_csv_dates(db, channel_id, date_from, date_to)

    written = 0
    total = Decimal("0")
    by_source: dict[str, Decimal] = {}
    skipped: set[str] = set()

    for day in sorted(daily):
        if day in skip_dates:
            skipped.add(day)
            continue
        for prod, amount in sorted(daily[day].items()):
            source = _PROD_TO_SOURCE.get(prod)
            if source is None:
                continue  # NCC 및 미지의 신규 상품은 여기서 다루지 않는다
            if amount <= 0:
                # 환불·조정으로 순차감이 0 이하인 날은 행을 만들지 않는다(음수 광고비 방지).
                log.info("비즈머니 %s %s 순차감 %s — 적재 생략", day, prod, amount)
                continue
            if not dry_run:
                _upsert_ad_cost(db, channel_id, date.fromisoformat(day), amount, source)
            written += 1
            total += amount
            by_source[source] = by_source.get(source, Decimal("0")) + amount

    if skipped:
        log.info("수동 CSV(%s)가 이미 채운 %d일은 건너뜀: %s ~ %s",
                 LEGACY_CSV_SOURCE, len(skipped), min(skipped), max(skipped))

    return {
        "written": written,
        "skipped_legacy": len(skipped),
        "total": total,
        "by_source": by_source,
    }
