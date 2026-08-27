# otao_stock_import.py — ECOUNT 수집 원문(JSON) → `otao_stock_snapshot`. **prod에서 돈다.**
#
# 짝은 `ecount_stock_export.py`(Mac). 이쪽은 ECOUNT도 네트워크도 필요 없다 — DB만 있으면 된다.
# 멱등이다: 같은 페이로드를 여러 번 먹여도 행이 두 배가 되지 않는다
# (키 = `snapshot_at` + 창고 + 품목코드).
#
#   python3 scripts/otao_stock_import.py --payload /tmp/otao_stock_payload.json
#   python3 scripts/otao_stock_import.py --payload /tmp/otao_stock_payload.json --dry-run
#   python3 scripts/otao_stock_import.py --manual-count /tmp/count.json   # 사람이 센 값(실사)
#   python3 scripts/otao_stock_import.py --manual-count /tmp/count.json --warehouse 본사
#
# ★`--manual-count`의 JSON은 `{"GAPIP16PR": 120, "GAPIP15": 7}` 꼴이다. 이 행들은
#   `source='manual'`로 들어가고 **ECOUNT 스냅샷 축에서 빠진다** — 대조의 상대편이지
#   「시스템이 말한 재고」가 아니기 때문이다. 섞으면 ECOUNT 값을 자기 자신과 대조하게 되어
#   오차가 항상 0으로 나온다.
#
# ★선행조건: 마이그레이션 `otaostk1s4a`가 적용돼 있어야 한다. 이 앱은 부팅 시 인프로세스
#   마이그레이션을 하지 않으므로 순서는 `scripts/safe_deploy.sh … --migrate`가 강제한다.
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

sys.path.insert(0, ".")

from app.database import SessionLocal  # noqa: E402
from app.services.otao_po.stock_ingest import (  # noqa: E402
    build_manual_count_payload,
    build_stock_payload,
    ingest_stock_payload,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="ECOUNT 수집 원문 → 재고 스냅샷 원장")
    ap.add_argument("--payload", help="ecount_stock_export.py가 만든 JSON(원문 캡처)")
    ap.add_argument("--manual-count", help='실사값 JSON: {"GAPIP16PR": 120, …}')
    ap.add_argument(
        "--warehouse", default="본사", help="--manual-count가 센 창고 이름 (기본: 본사)"
    )
    ap.add_argument("--dry-run", action="store_true", help="적재해 보고 커밋하지 않는다")
    args = ap.parse_args()

    if not args.payload and not args.manual_count:
        ap.error("--payload 또는 --manual-count 중 하나가 필요하다")
    if args.payload and args.manual_count:
        # 둘을 한 번에 받으면 어느 `snapshot_at`이 어느 것인지 화면에서 갈라지지 않는다.
        ap.error("--payload와 --manual-count는 따로 실행한다 (시각 축이 갈려야 한다)")

    if args.payload:
        with open(args.payload, encoding="utf-8") as fh:
            capture = json.load(fh)
        rows = capture.get("result")
        if rows is None:
            print("payload에 result가 없다 — ecount_stock_export.py 산출물이 맞는가?", file=sys.stderr)
            return 2
        payload = build_stock_payload(
            rows,
            snapshot_at=datetime.fromisoformat(str(capture["snapshot_at"])),
            base_date_raw=capture.get("base_date"),
        )
        # ★우리가 센 행수와 ECOUNT가 말한 개수가 다르면 «그 자체가 신호»다. 조용히 넘기지 않는다.
        total_cnt = capture.get("response_total_cnt")
        if total_cnt is not None and int(total_cnt) != len(rows):
            print(f"⚠️ 응답 TotalCnt={total_cnt} 인데 result 행수는 {len(rows)}다 — 잘렸을 수 있다.")
    else:
        with open(args.manual_count, encoding="utf-8") as fh:
            counts = json.load(fh)
        if not isinstance(counts, dict) or not counts:
            print('실사값은 {"코드": 수량} 꼴의 비어 있지 않은 객체여야 한다', file=sys.stderr)
            return 2
        payload = build_manual_count_payload(
            counts, snapshot_at=datetime.now(), warehouse_name=args.warehouse
        )
        print(f"실사 {len(counts)}개 코드 — 창고 「{args.warehouse}」")

    with SessionLocal() as session:
        rep = ingest_stock_payload(session, payload, dry_run=args.dry_run)

    out = rep.as_dict()
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("(--dry-run — 커밋하지 않았다)")
    if rep.skipped:
        print(f"⚠️ 버린 행 {len(rep.skipped)}건 — 0으로 대체하지 않았다. 사유는 위 목록에.")
    if rep.duplicate_keys:
        print(f"⚠️ 같은 (창고,품목) 키가 두 번 이상 온 것 {len(rep.duplicate_keys)}건 — 합쳤다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
