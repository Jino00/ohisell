#!/usr/bin/env python3
"""제외 «임대» 등급 — 소급 백필 실행기 겸 분포 관측기 (S2).

계약: docs/contracts/CONTRACT_ignition_readiness.md §4-A S2 · §4-B⑥⑦ · §4-C S2-a

## 두 모드 — 기본이 «읽기»인 이유
  --report (기본)  읽기 전용. 등급 분포표 + 계약 기대치 대조 + 이탈 사유를 찍는다.
  --backfill       등급이 NULL인 행에만 등급을 부여한다(만료일 무접촉·재실행 안전).

기본을 report로 둔 것은 실수 방지가 아니라 **계약 §4-C S2-a가 요구하는 관측 명령이 이
스크립트**이기 때문이다. Jino가 아무 인자 없이 실행하면 아무것도 안 바뀌고 표만 나온다.

## 사용 (prod, 1줄)
  ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && python3 scripts/exclusion_grade_backfill.py"
  ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && python3 scripts/exclusion_grade_backfill.py --backfill"

★이 스크립트는 광고 계정에 아무것도 쓰지 않는다 — 네이버 API를 호출조차 하지 않는다.
  건드리는 것은 우리 DB의 라벨 두 칸(`grade`·`grade_reason`)뿐이다(계약 §2-5·§3).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.services.naver_ad import exclusion_grade  # noqa: E402
from app.utils.kst import kst_today  # noqa: E402


def _print_report(report: dict) -> None:
    print("── 제외 «임대» 등급 분포 " + "─" * 44)
    for grade, n in sorted(report["distribution"].items(), key=lambda kv: -kv[1]):
        print(f"  {grade:<10} {n:>6,}")
    print(f"  {'합계':<10} {report['total']:>6,}")
    print()
    print("── 계약 §4-C S2-a 기대치 대조 " + "─" * 37)
    for grade, n in report["expected"].items():
        actual = report["distribution"].get(grade, 0)
        mark = "일치" if actual == n else f"차이 {actual - n:+d}"
        print(f"  {grade:<10} 기대 {n:>6,}  실제 {actual:>6,}   {mark}")
    print(f"  기대 합계 {report['expected_sum']:,} · 실제 합계 {report['total']:,}")
    if not report["deviation"]:
        print("  ⇒ 이탈 없음")
        return
    # 계약 §4-C S2-a: "수치가 [E]와 다르면 **다른 이유가 함께 출력·기록**돼 있다"
    print()
    print("── 이탈의 이유 " + "─" * 52)
    print("  계약 §4-B⑦의 합은 13+6+3,970 = 3,989로, 원장 총계와 1건 어긋난다.")
    print("  부록 [E]가 A급을 16건이라 하면서 「BEP 초과 13 + 미달 2」로 15만 설명한 자국이다.")
    for row in report["deviation_rows"]:
        print(f"  · id={row['id']} {row['search_term']!r} → {row['grade']}")
        print(f"    {row['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="제외 등급 백필/관측 (S2)")
    ap.add_argument("--backfill", action="store_true",
                    help="등급이 NULL인 행에 등급 부여(기본은 읽기 전용 report)")
    ap.add_argument("--all", action="store_true",
                    help="--backfill과 함께: 이미 붙은 등급도 다시 계산(사람이 찍은 판단을 덮는다 — 주의)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.backfill:
            out = exclusion_grade.backfill(db, today=kst_today(), only_missing=not args.all)
            bep = out["bep_roas"]
            print(f"백필 완료 — 총 {out['total']:,}행 중 {out['graded']:,}행 부여 "
                  f"(건너뜀 {out['skipped']:,}) · 계정 기본 BEP="
                  + (f"{bep:.4f}" if bep is not None else "[미상 — A급은 미검증으로 남김]"))
            print()
        _print_report(exclusion_grade.distribution_report(db))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
