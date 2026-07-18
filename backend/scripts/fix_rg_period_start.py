#!/usr/bin/env python3
# fix_rg_period_start.py — RG 정산 옵션 row의 오염된 recognition_date_from 1회성 보정.
#
# 무엇: coupang_rg_settlement_fee의 옵션 row(vendor_item_id != '')가 status/api 계정 row 부재 시
#       구 -6d 추측 폴백으로 적재돼 recognition_date_from이 틀린 그룹을, 검증된 달력 규칙
#       (월~일 주별 + 달력 월 경계 분할)으로 교정한다.
# 왜:   2026-07-17 사고 — WING1/WING2 쿠키 만료로 status/api 계정 row가 26/37일 공백인 동안
#       옵션 엑셀만 적재됐고, _resolve_period_start의 -6d 폴백이 월 경계 주간(06-30, 07-05)에서
#       from을 틀리게 찍어 6월 RG 비용이 +55% 과대계상됐다(2개 주기 이중계상).
#       달력 규칙은 prod 계정 row 18개 전수 검증 18/18 일치(라이브가 권위, 레퍼런스 17엔 월 분할 미기재).
#       참조: docs/PLAN_pipeline-freshness-3layer.md §3.
#
# stdlib만 사용(sqlite3/argparse/datetime) — prod에서 venv 없이 실행 가능.
# 사용:  python3 scripts/fix_rg_period_start.py --db /path/to/app.db          # dry-run
#        python3 scripts/fix_rg_period_start.py --db /path/to/app.db --apply  # 실제 적용
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta

TABLE = "coupang_rg_settlement_fee"


def expected_period_start(period_end: date) -> date:
    """정산주기 종료일 → 시작일 (달력 규칙: 월~일 주별 + 달력 월 경계 분할).

    start = max(그 주의 월요일, 그 달 1일). rg_settlement_sync._expected_period_start와 동일 규칙
    (이 스크립트는 stdlib만 써야 해 app import를 피하고 규칙을 복제 — 두 곳의 테스트가 함께 못박음).
    """
    monday = period_end - timedelta(days=period_end.weekday())
    month_first = period_end.replace(day=1)
    return max(monday, month_first)


def _to_date(value: str) -> date:
    """DB의 날짜 문자열('YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM:SS') → date."""
    s = str(value).strip().replace("T", " ").split(" ")[0]
    return date.fromisoformat(s)


def find_violations(conn: sqlite3.Connection) -> list[dict]:
    """보정 대상 그룹 탐색.

    조건: 옵션 row(vendor_item_id != '')의 distinct (account_key, from, to) 그룹 중,
      ① 같은 (account_key, to)에 계정 row(vendor_item_id='')가 **없고**
      ② stored from != 달력 규칙(expected_period_start(to)) 인 그룹.
    각 그룹에 대해 유니크 충돌 여부(같은 account,새from,to,fee_type,vendor_item_id 기존행 존재)도 판정.
    """
    cur = conn.cursor()
    cur.execute(
        f"SELECT DISTINCT account_key, recognition_date_from, recognition_date_to "
        f"FROM {TABLE} WHERE vendor_item_id != ''"
    )
    groups = cur.fetchall()

    violations: list[dict] = []
    for account_key, stored_from_raw, period_end_raw in groups:
        period_end = _to_date(period_end_raw)
        stored_from = _to_date(stored_from_raw)
        expected = expected_period_start(period_end)
        if stored_from == expected:
            continue  # 이미 정합

        # ① 계정 row 존재 시 skip (계정 row가 권위 — 이 스크립트 범위 밖)
        cur.execute(
            f"SELECT 1 FROM {TABLE} "
            f"WHERE account_key=? AND recognition_date_to=? AND vendor_item_id='' LIMIT 1",
            (account_key, period_end_raw),
        )
        if cur.fetchone() is not None:
            continue

        # 대상 그룹의 (fee_type, vendor_item_id) 목록 + 행수
        cur.execute(
            f"SELECT fee_type, vendor_item_id FROM {TABLE} "
            f"WHERE account_key=? AND recognition_date_from=? AND recognition_date_to=? "
            f"  AND vendor_item_id != ''",
            (account_key, stored_from_raw, period_end_raw),
        )
        pairs = cur.fetchall()

        # ③ 유니크 충돌 방어: 같은 (account, 새from, to, fee_type, vendor_item_id) 기존행 존재?
        new_from = expected.isoformat()
        conflict_pairs = []
        for fee_type, vendor_item_id in pairs:
            cur.execute(
                f"SELECT 1 FROM {TABLE} "
                f"WHERE account_key=? AND recognition_date_from=? AND recognition_date_to=? "
                f"  AND fee_type=? AND vendor_item_id=? LIMIT 1",
                (account_key, new_from, period_end_raw, fee_type, vendor_item_id),
            )
            if cur.fetchone() is not None:
                conflict_pairs.append((fee_type, vendor_item_id))

        violations.append({
            "account_key": account_key,
            "stored_from_raw": stored_from_raw,
            "period_end_raw": period_end_raw,
            "expected_from": new_from,
            "row_count": len(pairs),
            "conflict": bool(conflict_pairs),
            "conflict_pairs": conflict_pairs,
        })
    return violations


def apply_fixes(conn: sqlite3.Connection, violations: list[dict]) -> int:
    """충돌 없는 그룹의 from을 달력 규칙 값으로 UPDATE. 영향 행수 반환. 충돌 그룹은 skip+경고."""
    cur = conn.cursor()
    updated = 0
    for v in violations:
        if v["conflict"]:
            print(
                f"  [SKIP] 유니크 충돌 — {v['account_key']} {v['stored_from_raw']}→{v['expected_from']} "
                f"(to={v['period_end_raw']}) 충돌쌍={v['conflict_pairs']}",
                file=sys.stderr,
            )
            continue
        # 충돌 재검사(적대적 리뷰 P2): find_violations의 사전검사는 스캔 시점 기준이라, 같은 apply
        # 실행에서 앞 그룹의 UPDATE가 방금 만든 (from,to) 조합과 뒤 그룹이 충돌하면 IntegrityError로
        # 크래시했다(롤백이라 무오염이지만 크래시). UPDATE 직전 현재 DB 상태로 재검사해 skip.
        live_conflict = cur.execute(
            f"SELECT 1 FROM {TABLE} WHERE account_key=? AND recognition_date_from=? "
            f"  AND recognition_date_to=? AND vendor_item_id != '' LIMIT 1",
            (v["expected_from"], v["account_key"], v["period_end_raw"]),
        ).fetchone()
        if live_conflict is not None:
            print(
                f"  [SKIP] 유니크 충돌(적용 중 발생) — {v['account_key']} "
                f"{v['stored_from_raw']}→{v['expected_from']} (to={v['period_end_raw']})",
                file=sys.stderr,
            )
            continue
        cur.execute(
            f"UPDATE {TABLE} SET recognition_date_from=? "
            f"WHERE account_key=? AND recognition_date_from=? AND recognition_date_to=? "
            f"  AND vendor_item_id != ''",
            (v["expected_from"], v["account_key"], v["stored_from_raw"], v["period_end_raw"]),
        )
        updated += cur.rowcount
    conn.commit()
    return updated


def _print_plan(violations: list[dict]) -> None:
    if not violations:
        print("보정 대상 없음 — 모든 옵션 그룹이 달력 규칙과 정합.")
        return
    print(f"보정 대상 그룹 {len(violations)}개:")
    for v in violations:
        flag = " ⚠️충돌" if v["conflict"] else ""
        print(
            f"  {v['account_key']}: from {v['stored_from_raw']} → {v['expected_from']} "
            f"(to={v['period_end_raw']}, {v['row_count']}행){flag}"
        )
    total = sum(v["row_count"] for v in violations if not v["conflict"])
    print(f"적용 시 예상 UPDATE 행수(충돌 제외): {total}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RG 정산 옵션 row from 달력 규칙 보정(1회성)")
    parser.add_argument("--db", required=True, help="SQLite DB 파일 경로")
    parser.add_argument("--apply", action="store_true", help="실제 UPDATE(없으면 dry-run)")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        violations = find_violations(conn)
        _print_plan(violations)

        if not args.apply:
            print("\n[dry-run] --apply 없이는 변경하지 않습니다.")
            return 0

        print("\n[apply] UPDATE 실행 중...")
        updated = apply_fixes(conn, violations)
        print(f"[apply] UPDATE 완료: {updated}행")

        # 재스캔 — 잔여 위반 0 확인(충돌 skip 그룹은 남을 수 있음)
        remaining = find_violations(conn)
        non_conflict = [v for v in remaining if not v["conflict"]]
        if non_conflict:
            print(f"⚠️ 잔여 위반 {len(non_conflict)}개(충돌 아님) — 조사 필요:", file=sys.stderr)
            _print_plan(non_conflict)
            return 1
        conflict_left = [v for v in remaining if v["conflict"]]
        if conflict_left:
            print(f"잔여 {len(conflict_left)}개는 유니크 충돌로 의도적 skip(수동 조사).")
        else:
            print("재스캔 통과: 잔여 위반 0.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
