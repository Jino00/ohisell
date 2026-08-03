# backfill_naver_display_ad_costs.py — ADVoost·GFA 광고비 소급 적재
# 용도: 수동 CSV 업로드가 멈춘 2026-06-05 이후 구간을 비즈머니 실차감으로 채운다.
# 실행: cd backend && python scripts/backfill_naver_display_ad_costs.py --since 2026-06-05 [--until YYYY-MM-DD] [--dry-run]
#
# ★수동 CSV(`gfa:쇼핑`)가 이미 있는 날짜는 서비스가 자동으로 건너뛴다(이중계상 방지) —
#   --since를 실수로 넉넉히 잡아도 과거가 2배가 되지 않는다.
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy.orm import Session

from app.database import engine
from app.services.naver_display_ad_costs import sync_display_ad_costs
from app.utils.kst import kst_today


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="시작일 YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="종료일 YYYY-MM-DD (기본: 어제)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    since = date.fromisoformat(args.since)
    # 기본 종료일이 '어제'인 이유: exhaust는 D-1까지만 준다(당일은 아직 안 잡힘).
    until = date.fromisoformat(args.until) if args.until else kst_today() - timedelta(days=1)
    if since > until:
        raise SystemExit(f"기간이 뒤집혔습니다: {since} > {until}")

    print(f"ADVoost·GFA 광고비 소급: {since} ~ {until} {'[DRY-RUN]' if args.dry_run else ''}")
    with Session(engine) as db:
        result = sync_display_ad_costs(db, since, until, dry_run=args.dry_run)
        if not args.dry_run:
            db.commit()

    print()
    print(f"  적재 행     : {result['written']}건")
    print(f"  CSV 중복 회피: {result['skipped_legacy']}일")
    for src, amt in sorted(result["by_source"].items()):
        print(f"  {src:<14}: {amt:>12,.0f}원")
    print(f"  합계        : {result['total']:>12,.0f}원")
    if args.dry_run:
        print("\n[DRY-RUN] DB 저장 건너뜀.")


if __name__ == "__main__":
    main()
