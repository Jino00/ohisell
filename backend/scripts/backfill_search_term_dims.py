#!/usr/bin/env python3
"""naver_search_term_dim_daily / _cell_daily 180일 백필 (D-NAO-198 ①).

★왜 시한이 있나: SHOPPINGKEYWORD_DETAIL 재생성 한도가 **정확히 180일**이다
  (day-180 BUILT ↔ day-181 400/10004, `scripts/probe_report_retro_limit.py` 실측).
  매일 창이 굴러가므로 오늘 안 받은 D-180은 내일 영구 소실된다.

동작: 하루씩 뒤에서 앞으로(최신→과거) 돈다. 각 날짜마다 ensure_reports_built가 리포트가
  없으면 생성 요청 후 폴링한다(오래된 날짜는 대개 생성이 필요하다). 날짜 단위 커밋이라
  중단해도 거기까지는 남고, 다시 돌리면 **이미 적재된 날짜는 --skip-existing으로 건너뛴다**.

사용:
  python3 scripts/backfill_search_term_dims.py --days 180 --skip-existing
  python3 scripts/backfill_search_term_dims.py --from 2026-03-01 --to 2026-03-31
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.models import NaverSearchTermDimCellDaily, NaverSearchTermDimDaily  # noqa: E402
from app.services.naver_ad.search_term_dim_ingest import ingest_search_term_dimensions  # noqa: E402
from app.utils.kst import kst_today  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("backfill_dims")

RETRO_LIMIT_DAYS = 180  # 실측 확정 — 넘기면 API가 400/10004


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=RETRO_LIMIT_DAYS,
                    help=f"오늘−1 부터 거슬러 며칠(기본 {RETRO_LIMIT_DAYS} = 재생성 한도)")
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD (지정 시 --days 무시)")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--skip-existing", action="store_true",
                    help="이미 마진 행이 있는 날짜는 건너뛴다(재개용)")
    ap.add_argument("--sleep", type=float, default=0.0, help="날짜 사이 대기(초)")
    args = ap.parse_args()

    end = date.fromisoformat(args.date_to) if args.date_to else kst_today() - timedelta(days=1)
    start = (date.fromisoformat(args.date_from) if args.date_from
             else end - timedelta(days=args.days - 1))

    hard_floor = kst_today() - timedelta(days=RETRO_LIMIT_DAYS)
    if start < hard_floor:
        log.warning("시작일 %s 가 재생성 한도(%s) 밖 — %s 로 당긴다(그 앞은 API가 안 준다)",
                    start, hard_floor, hard_floor)
        start = hard_floor

    db = SessionLocal()
    total_m = total_c = 0
    done = skipped = failed = 0
    try:
        cur = end
        while cur >= start:
            if args.skip_existing:
                exists = db.query(NaverSearchTermDimDaily.id).filter(
                    NaverSearchTermDimDaily.ad_date == cur).first()
                if exists:
                    skipped += 1
                    cur -= timedelta(days=1)
                    continue
            try:
                r = ingest_search_term_dimensions(db, cur, cur)
                if not r["dates"]:
                    failed += 1
                    log.warning("[%s] 리포트 없음/생성 실패 — 건너뜀", cur)
                else:
                    done += 1
                    total_m += r["marginal_rows"]
                    total_c += r["cell_rows"]
                    log.info("[%s] 마진 %d행 / 유료결합 %d칸 (누적 %d일, %d행)",
                             cur, r["marginal_rows"], r["cell_rows"], done, total_m)
            except Exception as e:  # 한 날짜 실패가 나머지를 막지 않게
                db.rollback()
                failed += 1
                log.exception("[%s] 적재 실패(계속 진행): %s", cur, e)
            if args.sleep:
                time.sleep(args.sleep)
            cur -= timedelta(days=1)
    finally:
        db.close()

    log.info("백필 종료: 범위 %s~%s · 적재 %d일 · 건너뜀 %d일 · 실패 %d일 · 마진 %d행 · 유료결합 %d칸",
             start, end, done, skipped, failed, total_m, total_c)
    print(f"BACKFILL_RESULT days_ingested={done} skipped={skipped} failed={failed} "
          f"marginal_rows={total_m} cell_rows={total_c} range={start}..{end}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
