#!/usr/bin/env python3
"""복귀(재개방) 실험 배선 — 생존 관측기 (S3).

계약: docs/contracts/CONTRACT_ignition_readiness.md §4-A S3 · §4-C S3-a·S3-b · §1 ⓔ

## 읽기 전용 — 쓰기 모드가 «없다»
S2의 형제 스크립트(`exclusion_grade_backfill.py`)에는 `--backfill`이 있지만 여기엔 없다.
S3는 라벨을 소급으로 붙이는 슬라이스가 아니라 **앞으로 생길 사건이 기록되게 하는 배선**이라,
소급으로 만들 수 있는 것이 없다. 없는 모드를 흉내 내지 않는다.

## 사용 (prod, 1줄)
  ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python3 scripts/exclusion_return_wiring.py"

★네이버 API를 호출하지 않는다. 우리 DB만 읽는다(계약 §3 금지선).

## ★이 화면이 반드시 구분해야 하는 두 가지
  「배선이 죽었다」 vs 「아직 안 켰다」 — 둘 다 카운트가 0이다.
점화 전(auto_operate 전건 0)에는 복귀 행이 **0인 것이 정상**이고(계약 §1 「안 하는 것」 ①),
그것을 화면이 스스로 관측해 말해야 한다. n=58 적대 리뷰 1R이 정확히 이 부류의 결함이었다 —
화면이 관측 없이 문장을 단언했고, 그 문장을 지키는 테스트가 0건이었다. 그래서 아래 출력 함수는
`tests/test_exclusion_lifecycle.py`가 직접 읽는다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.services.naver_ad import exclusion_lifecycle  # noqa: E402


def format_report(report: dict) -> list[str]:
    """★계약 §4-C S3-a가 지목한 «Jino가 보는 표면». 여기 찍히는 문장은 전부 관측에서 나와야 한다.
    리스트로 돌려주는 이유는 테스트가 print를 가로채지 않고 문장을 그대로 읽게 하기 위해서다."""
    out: list[str] = []
    ba = report["by_action"]
    out.append("── 복귀 실험 일기 (execute) " + "─" * 38)
    out.append(f"  {'제외 발사':<14} exclude_search_term      "
               f"{ba['exclude_search_term']['count']:>6,}  최종 {ba['exclude_search_term']['last_created_at'] or '—'}")
    out.append(f"  {'복귀 개방':<14} restore_search_term      "
               f"{ba['restore_search_term']['count']:>6,}  최종 {ba['restore_search_term']['last_created_at'] or '—'}")
    out.append(f"  {'복귀 확정':<14} settle_search_term_return "
               f"{ba['settle_search_term_return']['count']:>5,}  최종 {ba['settle_search_term_return']['last_created_at'] or '—'}")

    out.append("── 복귀 관찰창 성적 " + "─" * 46)
    if report["return_open_total"] == 0:
        out.append("  (복귀 개방 일기가 0건이라 채점할 대상이 없다)")
    else:
        for status, n in sorted(report["probation_distribution"].items(), key=lambda kv: -kv[1]):
            out.append(f"  {status:<14} {n:>6,}")

    out.append("── 0의 이유 (실행 게이트 실측) " + "─" * 35)
    for row in report["gate"]["rows"]:
        out.append(f"  auto_operate={int(row['auto_operate'])} · optimizer={row['optimizer']!r} "
                   f"· 캠페인 {row['campaigns']}개")
    # ★문장은 관측 «뒤에» 온다 — 고정 문단으로 두면 상태가 바뀐 날 화면이 거짓말을 한다.
    if report["gate"]["ignited"]:
        out.append("  ⇒ 점화됨(auto_operate=1인 캠페인이 있다). 위 카운트가 0이면 그건 «정상»이 "
                   "아니라 조사 대상이다.")
    else:
        out.append("  ⇒ 미점화(auto_operate 전건 0) — 복귀 카운트 0은 **배선 고장이 아니라 "
                   "«아직 안 켰다»**이다(계약 §1 「안 하는 것」 ①). 배선의 생존은 위 카운트가 "
                   "아니라 tests/test_exclusion_lifecycle.py가 증명한다.")
    return out


def main() -> int:
    db = SessionLocal()
    try:
        for line in format_report(exclusion_lifecycle.wiring_report(db)):
            print(line)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
