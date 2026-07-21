# 이 파일은 naver_ad_daily 과거 그룹-그레인 백필 러너입니다 — 네이버 대용량 보고서(롤링 1년
# 한도, 2026-07-21 실측: 2025-07-22 접수·2025-07-15 거부)로 상세(캠페인×그룹×키워드) 행을
# 하루 단위 수집·적재한다. 적재는 기존 ingest_ad_daily(날짜 스냅샷 교체·멱등) 재사용 —
# 센티널(__backfill__) 행은 상세 적재 시 자동 대체된다(택일 정합, 2배 함정 방지).
#
# 사용(프로덕션 backend 디렉토리에서):
#   .venv/bin/python scripts/backfill_naver_ad_daily_history.py --from 2025-07-22 --to 2026-07-03
#   옵션: --force (상세가 이미 있는 날짜도 재적재), --sleep 2.0 (날짜 간 대기, 기본 1.0)
#
# 안전 규칙(이 스크립트가 지키는 것):
#   ① AD·AD_CONVERSION 두 보고서가 모두 BUILT로 확인된 날짜만 적재 — 한쪽 없이 적재하면
#      기존 센티널의 전환값을 0으로 격하시킬 수 있다(fail-closed skip).
#   ② 수집 결과 0행이면 적재 호출 안 함 — ingest의 날짜 삭제가 발동하지 않아 기존 행 보존.
#   ③ 상세 행이 이미 있는 날짜는 skip(--force 제외) — 매일 07-04 이후 정상 수집분 불변.
#   ④ 날짜별 독립 처리·독립 커밋 — 중간 실패해도 완료분은 남고, 재실행하면 이어서 진행(멱등).
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# fetcher는 import 시점에 자격증명 env를 읽으므로 load_dotenv 뒤에 import한다.
from app.database import SessionLocal  # noqa: E402
from app.models import NaverAdDaily  # noqa: E402
from app.services import naver_sa_ad_fetcher as fetcher  # noqa: E402
from app.services.naver_ad.ad_daily_ingest import ingest_ad_daily  # noqa: E402
from app.services.naver_ad.report_collector import collect_daily_rows  # noqa: E402

log = logging.getLogger("backfill_history")

_SENTINEL_ADGROUPS = ("", "__backfill__")


def _has_detail_rows(db, d: date) -> bool:
    """그 날짜에 상세(실제 adgroup_id) 행이 이미 있으면 True — 정상 수집분/기완료 백필 skip 판정."""
    return (
        db.query(NaverAdDaily.id)
        .filter(NaverAdDaily.ad_date == d, NaverAdDaily.adgroup_id.notin_(_SENTINEL_ADGROUPS))
        .first()
        is not None
    )


def backfill_one_date(db, d: date) -> str:
    """하루 백필: 보고서 2종 보장·확인 → 수집 → 적재. 반환=결과 상태 문자열(로그·집계용)."""
    # 과거 날짜 보고서는 신규 빌드라 기본 60s를 자주 초과(2026-03-15 실측) → 300s로 상향.
    fetcher.ensure_reports_built("AD", d, d, timeout=300.0)
    fetcher.ensure_reports_built("AD_CONVERSION", d, d, timeout=300.0)
    ad_reports = fetcher.list_ad_reports(d, d)
    conv_reports = fetcher._list_reports_by_type("AD_CONVERSION", d, d)
    if not conv_reports:
        # AD_CONVERSION 잡의 종결 상태가 NONE이면 "그날 전환 0건"의 정상 표현(2026-07-21 실측:
        # 03-15·03-23 NONE=우리 데이터 전환 0, 03-20·03-25 BUILT=전환 있음) → conv=0으로 적재
        # 진행이 정당하다. NONE 확인이 안 되는 경우(빌드 실패·타임아웃 등)만 fail-closed skip
        # (전환 0 격하 방지, 안전 규칙①).
        conv_status = fetcher._report_status_by_kst_date("AD_CONVERSION").get(d.isoformat())
        if not (conv_status and conv_status[0] == "NONE"):
            return f"skip_no_report(ad={bool(ad_reports)},conv_status={conv_status and conv_status[0]})"
    if not ad_reports:
        # AD 성과 보고서 없이는 그날 행 자체를 만들 수 없다(fail-closed, 안전 규칙①).
        return "skip_no_report(ad=False)"
    rows = collect_daily_rows(d, d)
    if not rows:
        # 안전 규칙②: 0행이면 ingest 호출 안 함 — 기존 행(센티널 포함) 보존.
        return "skip_empty_rows"
    result = ingest_ad_daily(db, d, d, rows=rows)
    return f"ingested rows={result['rows']} cost={result['total_cost']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="naver_ad_daily 과거 그룹-그레인 백필")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--force", action="store_true", help="상세가 이미 있는 날짜도 재적재")
    parser.add_argument("--sleep", type=float, default=1.0, help="날짜 간 대기 초(기본 1.0)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    d_from = date.fromisoformat(args.date_from)
    d_to = date.fromisoformat(args.date_to)
    if d_from > d_to:
        raise SystemExit("--from이 --to보다 늦습니다")

    stats = {"ingested": 0, "skip_detail": 0, "skip_no_report": 0, "skip_empty": 0, "error": 0}
    db = SessionLocal()
    try:
        # 과거→최근 순서: 롤링 1년 한도라 가장 과거 날짜가 먼저 소멸한다 — 한도 임박분부터 건진다.
        cur = d_from
        while cur <= d_to:
            try:
                if not args.force and _has_detail_rows(db, cur):
                    stats["skip_detail"] += 1
                    log.info("%s skip(상세 이미 있음)", cur)
                else:
                    outcome = backfill_one_date(db, cur)
                    if outcome.startswith("ingested"):
                        stats["ingested"] += 1
                    elif outcome.startswith("skip_no_report"):
                        stats["skip_no_report"] += 1
                    else:
                        stats["skip_empty"] += 1
                    log.info("%s %s", cur, outcome)
            except Exception as e:  # 날짜별 독립 — 한 날짜 실패가 전체를 멈추지 않는다(재실행 멱등).
                db.rollback()
                stats["error"] += 1
                log.error("%s 실패(계속): %s: %s", cur, type(e).__name__, e)
            cur += timedelta(days=1)
            time.sleep(args.sleep)
    finally:
        db.close()
    log.info("백필 완료: %s", stats)


if __name__ == "__main__":
    main()
