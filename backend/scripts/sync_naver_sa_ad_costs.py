# sync_naver_sa_ad_costs.py — 네이버 SA 광고비 → ad_costs 테이블 동기화
# 용도: /stat-reports API에서 AD 보고서를 수집하여 keyword 기준으로 ad_costs에 저장
# 실행: cd backend && python scripts/sync_naver_sa_ad_costs.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run]
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import engine
from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_spend, extract_keyword

# ── 상품 키워드 정의 ─────────────────────────────────────────────────────────
# 캠페인명에서 추출할 키워드 (순서 중요: 구체적인 것 먼저)
PRODUCT_KEYWORDS = [
    "지문방지",
    "강화유리",
    "종이질감",
    "사생활",
    "갤럭시탭",
    "아이패드",
    "아이폰",
    "갤럭시",
    "셀카봉",
    "뮤패드",
    "케이스",
]

KEYWORD_ALIAS: dict[str, str] = {}


def normalize_keyword(kw: str) -> str:
    return KEYWORD_ALIAS.get(kw, kw)


def get_naver_channel_id(db: Session) -> int | None:
    row = db.execute(text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")).fetchone()
    return row[0] if row else None


def run(since: date, until: date, dry_run: bool = False) -> None:
    print(f"Naver SA 광고비 동기화: {since} ~ {until} {'[DRY-RUN]' if dry_run else ''}")
    print()

    rows = fetch_campaign_daily_spend(since, until)
    if not rows:
        print("수집된 데이터 없음.")
        return

    print(f"수집: {len(rows)}건 (캠페인×일×라인)")

    # 키워드 매핑 및 날짜별 집계
    keyword_daily: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    unmatched_daily: dict[str, Decimal] = defaultdict(Decimal)
    seen_campaigns: set[str] = set()
    match_log: list[str] = []

    for row in rows:
        cname = row["campaign_name"]
        d = row["date"]
        spend = row["spend"]

        kw_raw = extract_keyword(cname, PRODUCT_KEYWORDS)
        if kw_raw:
            kw = normalize_keyword(kw_raw)
            keyword_daily[(kw, d)] += spend
            if cname not in seen_campaigns:
                match_log.append(f"  {cname[:45]:<45} → [{kw}]")
        else:
            unmatched_daily[d] += spend
            if cname not in seen_campaigns:
                match_log.append(f"  {cname[:45]:<45} → [미매칭]")
        seen_campaigns.add(cname)

    print()
    print("=== 캠페인 → 키워드 매핑 결과 ===")
    for log in sorted(set(match_log)):
        print(log)

    print()
    print("=== 키워드별 광고비 합계 ===")
    kw_totals: dict[str, Decimal] = defaultdict(Decimal)
    for (kw, d), spend in keyword_daily.items():
        kw_totals[kw] += spend
    for kw, total in sorted(kw_totals.items(), key=lambda x: -x[1]):
        print(f"  {kw:<15} {total:>12,.0f}원")
    if unmatched_daily:
        total_unmatched = sum(unmatched_daily.values())
        print(f"  {'(미매칭)':<15} {total_unmatched:>12,.0f}원")

    grand_total = sum(keyword_daily.values()) + sum(unmatched_daily.values())
    print(f"  {'합계':<15} {grand_total:>12,.0f}원")

    if dry_run:
        print()
        print("[DRY-RUN] DB 저장 건너뜀.")
        return

    # DB 저장
    with Session(engine) as db:
        naver_id = get_naver_channel_id(db)
        if not naver_id:
            print("ERROR: NAVER 채널 없음")
            return

        # 기존 naver_sa 광고비 삭제 (해당 기간)
        db.execute(
            text("""
                DELETE FROM ad_costs
                WHERE channel_id = :cid
                  AND source LIKE 'naver_sa:%'
                  AND ad_date >= :since
                  AND ad_date <= :until
            """),
            {"cid": naver_id, "since": since.isoformat(), "until": until.isoformat()},
        )

        inserted = 0
        for (kw, d), spend in keyword_daily.items():
            db.execute(
                text("""
                    INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                    VALUES (:cid, NULL, :ad_date, :spend, NULL, :source, datetime('now'))
                """),
                {"cid": naver_id, "ad_date": d, "spend": str(spend), "source": f"naver_sa:{kw}"},
            )
            inserted += 1

        for d, spend in unmatched_daily.items():
            db.execute(
                text("""
                    INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                    VALUES (:cid, NULL, :ad_date, :spend, NULL, 'naver_sa:기타', datetime('now'))
                """),
                {"cid": naver_id, "ad_date": d, "spend": str(spend)},
            )
            inserted += 1

        db.commit()
        print()
        print(f"저장 완료: {inserted}건 → ad_costs (NAVER 채널)")

    # 검증
    with Session(engine) as db:
        total = db.execute(
            text("""
                SELECT SUM(CAST(ad_spend AS REAL))
                FROM ad_costs
                WHERE channel_id = (SELECT id FROM channels WHERE code='NAVER')
                  AND source LIKE 'naver_sa:%'
                  AND ad_date >= :since AND ad_date <= :until
            """),
            {"since": since.isoformat(), "until": until.isoformat()},
        ).scalar() or 0
        print(f"검증: {since} ~ {until} Naver SA 광고비 합계 = {total:,.0f}원")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=(date.today() - timedelta(days=30)).isoformat())
    parser.add_argument("--until", default=(date.today() - timedelta(days=1)).isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(
        since=date.fromisoformat(args.since),
        until=date.fromisoformat(args.until),
        dry_run=args.dry_run,
    )
