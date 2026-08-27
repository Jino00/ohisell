#!/usr/bin/env python3
"""파워링크 문안 적재 현황 — 관측기 (S5).

계약: docs/contracts/CONTRACT_ignition_readiness.md §4-A S5 · §4-C S5-a · §1 ⓔ

## 읽기 전용 — 쓰기 모드가 «없다»
이 스크립트는 우리 DB만 읽는다. 네이버 API 0콜(계약 §3 금지선 — GET조차 여기선 안 한다.
수집은 크론 `sync_naver_ad_creative_text`(11:32)의 일이고, 이 화면은 그 결과를 볼 뿐이다).

## 사용 (prod, 1줄)
  ssh sellc.ohitech.co.kr "cd /home/ubuntu/ohisell/backend && .venv/bin/python3 scripts/ad_creative_text_report.py"

## ★이 화면이 반드시 구분해야 하는 두 가지
  「수집이 아직 안 돌았다」 vs 「돌았는데 0건이다」 — 둘 다 행수가 0이다.
배포 당일 11:32 이전에는 **0행이 정상**이고, 그 뒤로도 0이면 결함이다. 아래 문장은 전부
`creative_text_report()`의 관측값에서 나오며 **단언하지 않는다** — n=58 1R·n=59 1R이 연달아
잡은 결함이 「화면이 관측 없이 문장을 단언」이었고, 그때 살아남은 변이는 전부 «스크립트 출력을
테스트가 안 읽어서» 생겼다. 그래서 `format_report`는 리스트를 돌려주고
`tests/test_naver_ad_creative_text.py`가 **문장이 아니라 숫자**를 읽는다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal  # noqa: E402
from app.services.naver_ad import naver_ad_creative_text_ingest as ingest  # noqa: E402


def format_report(report: dict) -> list[str]:
    """★계약 §4-C S5-a가 지목한 «Jino가 보는 표면». 리스트로 돌려주는 이유는 테스트가
    print를 가로채지 않고 숫자를 그대로 읽게 하기 위해서다."""
    out: list[str] = []
    out.append("── 파워링크 문안 적재 (naver_ad_creative_text) " + "─" * 20)
    out.append(f"  소재 행            {report['ads']:>7,}")
    out.append(f"  광고그룹 커버      {report['groups_covered']:>7,} / {report['groups_target']:,}")
    out.append(f"  캠페인 커버        {report['campaigns_covered']:>7,} / {report['campaigns_target']:,}")
    out.append(f"  변경 원장 행       {report['change_rows']:>7,}")
    out.append(f"  최종 관측          {report['last_seen_at'] or '—'}")
    out.append(f"  최종 변경          {report['last_changed_at'] or '—'}")

    if report["by_type"]:
        types = " · ".join(f"{k} {v:,}" for k, v in sorted(report["by_type"].items()))
        out.append(f"  소재 타입          {types}")
    if report["by_status"]:
        stats = " · ".join(f"{k} {v:,}" for k, v in sorted(report["by_status"].items()))
        out.append(f"  소재 상태          {stats}")

    if not report["collected"]:
        # ★단언이 아니라 관측 보고다 — 「아직 안 돌았다」와 「돌았는데 0건」을 사람이 가를 수
        #   있게 두 가능성을 **둘 다** 적는다. 크론 이름을 실어야 다음 사람이 확인할 수 있다.
        out.append("")
        out.append("  ⚠️ 소재 행 0 — 두 경우가 같은 숫자다:")
        out.append("     ① 수집이 아직 안 돌았다(크론 sync_naver_ad_creative_text, 매일 11:32 KST)")
        out.append("     ② 돌았는데 대상이 0건이다(그러면 크론 로그가 미완주로 error를 남긴다)")
        out.append("     가르는 법: `grep 's5' <앱 로그>` — 수집 라인이 있으면 ②다")
        return out

    out.append("")
    out.append(f"── 최근 변경순 표본 {len(report['samples'])}건 " + "─" * 30)
    for s in report["samples"]:
        out.append(f"  [{s['ad_type']}] {s['ad_id']}  ({s['status']})  editTm={s['edit_tm'] or '—'}")
        out.append(f"     제목 : {s['headline'] or '—'}")
        out.append(f"     설명 : {s['description'] or '—'}")
    return out


def main() -> int:
    db = SessionLocal()
    try:
        report = ingest.creative_text_report(db)
    finally:
        db.close()
    for line in format_report(report):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
