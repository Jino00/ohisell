# sync_meta_ad_costs.py — Meta 광고비 → ad_costs 테이블 동기화
# 용도: Meta 캠페인별 일별 spend를 수집하여 keyword 기준으로 ad_costs에 저장
# 실행: cd backend && python scripts/sync_meta_ad_costs.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run]
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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import engine
from app.services.meta_ad_fetcher import fetch_campaign_daily_spend, extract_keyword

# ── 상품 키워드 정의 ─────────────────────────────────────────────────────────
# 캠페인명에서 추출할 키워드 목록 (순서 중요: 길고 구체적인 것 먼저)
PRODUCT_KEYWORDS = [
    "지문방지필름",
    "골프필름",
    "버디필름",
    "강화유리",
    "셀카봉",
    "샐카봉",
    "문캅스",
    "일미리케이스",
]

# 셀카봉/샐카봉 표기 통일
KEYWORD_ALIAS = {
    "샐카봉": "셀카봉",
}


def normalize_keyword(kw: str) -> str:
    return KEYWORD_ALIAS.get(kw, kw)


def get_cafe24_channel_id(db_session: Session) -> int | None:
    row = db_session.execute(
        text("SELECT id FROM channels WHERE code = 'CAFE24' LIMIT 1")
    ).fetchone()
    return row[0] if row else None


def get_product_keyword_map(db_session: Session) -> dict[str, list[int]]:
    """키워드 → 매칭되는 product_master.id 목록"""
    rows = db_session.execute(
        text("SELECT id, product_name FROM product_master")
    ).fetchall()

    keyword_map: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        pid, pname = row[0], row[1] or ""
        for kw in PRODUCT_KEYWORDS:
            normalized = normalize_keyword(kw)
            if kw in pname or normalized in pname:
                keyword_map[normalized].append(pid)
                break
    return dict(keyword_map)


def run(since: date, until: date, dry_run: bool = False) -> None:
    print(f"Meta 광고비 동기화: {since} ~ {until} {'[DRY-RUN]' if dry_run else ''}")
    print()

    # 1. Meta API에서 캠페인 일별 spend 수집
    rows = fetch_campaign_daily_spend(since, until)
    if not rows:
        print("수집된 데이터 없음.")
        return

    print(f"수집: {len(rows)}건 (캠페인×일)")

    # 2. 키워드 매칭
    keyword_daily: dict[tuple[str, str], Decimal] = defaultdict(Decimal)  # (keyword, date) → spend
    unmatched_daily: dict[str, Decimal] = defaultdict(Decimal)  # date → spend (미매칭)
    match_log: list[str] = []

    for row in rows:
        kw_raw = extract_keyword(row["campaign_name"], PRODUCT_KEYWORDS)
        if kw_raw:
            kw = normalize_keyword(kw_raw)
            keyword_daily[(kw, row["date"])] += row["spend"]
        else:
            unmatched_daily[row["date"]] += row["spend"]

    # 3. 매핑 결과 출력
    print()
    print("=== 캠페인 → 키워드 매핑 결과 ===")

    seen_campaigns: set[str] = set()
    for row in sorted(rows, key=lambda r: r["campaign_name"]):
        cname = row["campaign_name"]
        if cname in seen_campaigns:
            continue
        seen_campaigns.add(cname)
        kw_raw = extract_keyword(cname, PRODUCT_KEYWORDS)
        kw = normalize_keyword(kw_raw) if kw_raw else None
        status = f"→ [{kw}]" if kw else "→ [미매칭 — 전체 비례배분]"
        print(f"  {cname[:50]:<50} {status}")

    print()
    print("=== 키워드별 광고비 합계 ===")
    for (kw, d), spend in sorted(keyword_daily.items()):
        print(f"  {d} | {kw:<12} | {spend:,.0f}원")
    if unmatched_daily:
        print()
        print("  [미매칭 — 전체 비례배분]")
        for d, spend in sorted(unmatched_daily.items()):
            print(f"  {d} | {'(전체배분)':<12} | {spend:,.0f}원")

    if dry_run:
        print()
        print("[DRY-RUN] DB 저장 건너뜀.")
        return

    # 4. DB 저장
    with Session(engine) as db:
        cafe24_id = get_cafe24_channel_id(db)
        if not cafe24_id:
            print("ERROR: CAFE24 채널 없음")
            return

        # 기존 meta 광고비 삭제 (해당 기간)
        db.execute(
            text("""
                DELETE FROM ad_costs
                WHERE channel_id = :cid
                  AND source LIKE 'meta:%'
                  AND ad_date >= :since
                  AND ad_date <= :until
            """),
            {"cid": cafe24_id, "since": since.isoformat(), "until": until.isoformat()},
        )

        inserted = 0
        for (kw, d), spend in keyword_daily.items():
            db.execute(
                text("""
                    INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                    VALUES (:cid, NULL, :ad_date, :spend, NULL, :source, datetime('now'))
                """),
                {
                    "cid": cafe24_id,
                    "ad_date": d,
                    "spend": str(spend),
                    "source": f"meta:{kw}",
                },
            )
            inserted += 1

        # 미매칭 광고비도 저장 (전체 비례배분용)
        for d, spend in unmatched_daily.items():
            db.execute(
                text("""
                    INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                    VALUES (:cid, NULL, :ad_date, :spend, NULL, 'meta:기타', datetime('now'))
                """),
                {"cid": cafe24_id, "ad_date": d, "spend": str(spend)},
            )
            inserted += 1

        db.commit()
        print()
        print(f"저장 완료: {inserted}건 → ad_costs")

    # 5. 검증 출력
    with Session(engine) as db:
        total = db.execute(
            text("""
                SELECT SUM(CAST(ad_spend AS REAL))
                FROM ad_costs
                WHERE channel_id = :cid AND source LIKE 'meta:%'
                  AND ad_date >= :since AND ad_date <= :until
            """),
            {"cid": cafe24_id, "since": since.isoformat(), "until": until.isoformat()},
        ).scalar()
        print(f"검증: {since} ~ {until} Meta 광고비 합계 = {total:,.0f}원")


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
