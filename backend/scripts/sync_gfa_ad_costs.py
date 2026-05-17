# sync_gfa_ad_costs.py — GFA(ADVoost) 광고비 CSV → ad_costs 테이블 동기화
# 용도: cafe24 GFA 콘솔에서 다운로드한 광고비 보고서 CSV를 ad_costs에 저장
# 실행: cd backend && python scripts/sync_gfa_ad_costs.py <CSV파일경로> [--dry-run]
#
# CSV 파일명 형식: theohi11_광고비 보고서_YYYYMMDD_YYYYMMDD.csv
# CSV 컬럼: 광고 계정 이름, 광고 계정 ID, 기간, 통화, 총비용, ..., ADVoost 쇼핑
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import engine


def parse_date_from_row(date_str: str) -> date:
    """'2026.05.15.' 형식 파싱."""
    cleaned = date_str.strip().rstrip(".")
    return date.fromisoformat(cleaned.replace(".", "-"))


def get_naver_channel_id(db: Session) -> int | None:
    row = db.execute(text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")).fetchone()
    return row[0] if row else None


def load_csv(path: Path) -> list[dict]:
    """CSV 파일 읽어서 행 목록 반환."""
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if row.get("기간", "").strip()]


def run(csv_path: Path, dry_run: bool = False) -> None:
    rows = load_csv(csv_path)
    if not rows:
        print("데이터 없음.")
        return

    print(f"GFA 광고비 동기화: {csv_path.name} {'[DRY-RUN]' if dry_run else ''}")
    print(f"총 {len(rows)}행")
    print()

    records: list[tuple[date, Decimal]] = []
    for row in rows:
        try:
            d = parse_date_from_row(row["기간"])
        except (ValueError, KeyError):
            print(f"  날짜 파싱 실패: {row.get('기간')} — 건너뜀")
            continue

        # ADVoost 쇼핑 우선, 없으면 총비용
        spend_str = row.get("ADVoost 쇼핑", "").strip() or row.get("총비용", "0").strip()
        try:
            spend = Decimal(spend_str.replace(",", ""))
        except Exception:
            print(f"  비용 파싱 실패: {spend_str} — 건너뜀")
            continue

        if spend <= 0:
            print(f"  {d}: 광고비 0 — 건너뜀")
            continue

        records.append((d, spend))
        print(f"  {d}: ADVoost 쇼핑 {spend:,.0f}원")

    if not records:
        print("저장할 데이터 없음.")
        return

    total = sum(s for _, s in records)
    print()
    print(f"합계: {total:,.0f}원 ({len(records)}일)")

    if dry_run:
        print("[DRY-RUN] DB 저장 건너뜀.")
        return

    with Session(engine) as db:
        naver_id = get_naver_channel_id(db)
        if not naver_id:
            print("ERROR: NAVER 채널 없음")
            return

        inserted = 0
        for d, spend in records:
            # 같은 날짜 기존 GFA 데이터 삭제 후 재삽입 (idempotent)
            db.execute(
                text("""
                    DELETE FROM ad_costs
                    WHERE channel_id = :cid
                      AND source = 'gfa:쇼핑'
                      AND ad_date = :ad_date
                """),
                {"cid": naver_id, "ad_date": d.isoformat()},
            )
            db.execute(
                text("""
                    INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                    VALUES (:cid, NULL, :ad_date, :spend, NULL, 'gfa:쇼핑', datetime('now'))
                """),
                {"cid": naver_id, "ad_date": d.isoformat(), "spend": str(spend)},
            )
            inserted += 1

        db.commit()
        print(f"저장 완료: {inserted}건 → ad_costs (NAVER 채널, source=gfa:쇼핑)")

    # 검증
    with Session(engine) as db:
        since = min(d for d, _ in records).isoformat()
        until = max(d for d, _ in records).isoformat()
        total_db = db.execute(
            text("""
                SELECT SUM(CAST(ad_spend AS REAL))
                FROM ad_costs
                WHERE channel_id = (SELECT id FROM channels WHERE code='NAVER')
                  AND source = 'gfa:쇼핑'
                  AND ad_date >= :since AND ad_date <= :until
            """),
            {"since": since, "until": until},
        ).scalar() or 0
        print(f"검증: {since} ~ {until} GFA 광고비 합계 = {total_db:,.0f}원")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", help="GFA 광고비 CSV 파일 경로")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(Path(args.csv_file), dry_run=args.dry_run)
