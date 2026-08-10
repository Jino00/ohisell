#!/usr/bin/env python3
"""audit_cost_buffer.py — `product_master.cost_price`가 **원가표 정본**과 어긋나는지 본다 (읽기 전용).

## 왜 있나 (2026-08-10, D-CPP-30)

원가의 정본은 엑셀 `MD_원가 계산_Jino_260807.xlsx`의 「제품 원가표」다. 그런데 DB의
`product_master.cost_price`에 **버퍼가 얹힌 값**이 오래 남아 있었다 — 정본 + 265.3(폰) /
+232.6(도어락·플립) / +96.4(폴드) 같은 **일정한 차이**다.

그 결과 원가가 실제보다 높게 잡혀 **이익이 과소 계상**됐다. 2026-08-10 실측:
전 채널 90일 **+1,012,405원**(스마트스토어 941,659 · 자사몰 61,372 · 쿠팡 9,374) + 로켓1P 186,279원.
★취소·반품·입금전(`REVENUE_EXCLUDED`)은 빼고 센 값이다 — 손익 엔진이 그 주문을 건너뛰므로
  그 원가 과대는 이익에 반영된 적이 없다.
177건을 정본으로 내려 해소했다.

★★**그런데 되돌아올 수 있다.** 2026-08-07 인계본이 이미 경고했다 —
  *"그 엑셀을 지금 그대로 업로드하면 오늘 고친 값이 되돌아간다."*
  옛 매핑 엑셀(`ohisell_mapping_template*.xlsx`)을 올리는 순간 버퍼가 통째로 복귀한다.
  **그리고 그건 조용하다** — 아무 에러도 안 나고 이익만 줄어든다.

## ★이 스크립트는 상시 감시자가 **아니다** (2026-08-10 배선 후)

처음엔 이 CLI가 유일한 감지 수단이었는데, **아무도 안 불렀다.**
이제 상시 경로는 앱 안에 있다:

    GET /api/scheduler/health → `cost_drift` → 전역 파이프라인 헬스 배너

판정 산술은 `backend/app/services/cost_truth_audit.py` **한 벌**이고 이 CLI는 그걸 임포트한다
(사본을 두 벌 두면 한쪽만 고쳐져 감시자가 감시 대상보다 낡는다).
이 CLI가 남아 있는 이유는 셋뿐이다: ①앱 없이 임의 DB 파일(백업본 등)을 점검 ②종료 코드가
필요한 자동화 ③사람이 드리프트 **상세 목록**을 보고 싶을 때.

## 무엇을 하나

정본 스냅샷(`backend/app/data/cost_truth_20260807.json`)과 DB를 대조해 세 갈래로 가른다:

  · `ok`           — 정본 값과 정확히 일치
  · **`buffered`** — 정본 + **알려진 버퍼**와 일치 → **드리프트. 이게 잡으려는 것.**
  · `undetermined` — 둘 다 아님. 케이스·거치대·강화유리(오타오) 등 원가표가 CNY·환율로
                     따로 계산하는 계열이라 단순 값 대조가 안 된다(08-07 결정: 마스터 값 유지).

★**«판정 불가»를 «정상»으로 세지 않는다.** 셋을 따로 낸다 — 합치면 드리프트가 묻힌다
  (이 프로젝트가 반복해 당한 형태: 발견 0건과 실행 안 됨이 같은 숫자로 보인다).

★**이름 매칭을 하지 않는다.** 값 산술만 본다 — 상세는 `cost_truth_audit.py` 헤더.

## 읽기 전용

`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`로만 연다. INSERT/UPDATE/DELETE 없음.

## 사용

    python3 scripts/audit_cost_buffer.py --db <DB 경로>
    python3 scripts/audit_cost_buffer.py --db <DB> --format json --out drift.json

종료 코드: 드리프트 0건이면 0, 있으면 1.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections import defaultdict

_REPO = pathlib.Path(__file__).resolve().parents[1]
# ★판정 산술은 앱 서비스 모듈 한 벌만 쓴다. 그 모듈은 DB·SQLAlchemy를 임포트하지 않으므로
#   venv 없이 시스템 python3으로도 임포트된다(백업 DB를 서버 밖에서 점검할 때가 그 경우다).
sys.path.insert(0, str(_REPO / "backend"))
from app.services.cost_truth_audit import (  # noqa: E402
    DEFAULT_TRUTH_PATH,
    EPS as _EPS,  # noqa: F401 — 허용오차의 단일 출처. 여기서 다시 정의하지 않는다.
    classify,
    classify_rows,
    count_verdicts,
    load_truth,
)


def scan(db_path: str, truth: dict) -> list[dict]:
    """DB를 **읽기 전용**으로 열어 원가가 있는 행만 판정한다."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT internal_sku, product_name, cost_price FROM product_master "
            "WHERE cost_price IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return classify_rows(rows, truth)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True, help="SQLite DB 경로 (읽기 전용으로 연다)")
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH_PATH), help="정본 스냅샷 JSON")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--out", help="출력 파일 (없으면 stdout)")
    a = ap.parse_args()

    truth = load_truth(pathlib.Path(a.truth))
    rows = scan(a.db, truth)
    drift = [r for r in rows if r["verdict"] == "buffered"]

    by_buf: dict[str, list] = defaultdict(list)
    for r in drift:
        by_buf[r["buffer_label"]].append(r)
    counts = count_verdicts(rows)

    if a.format == "json":
        payload = {"source": truth["source_file"], "source_sha256_16": truth["source_sha256_16"],
                   "counts": counts, "drift": drift}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    else:
        L = [f"원가 정본 대조 — {truth['source_file']} (sha {truth['source_sha256_16']}) · 정본 {len(truth['_values'])}개 값",
             f"  ✅ 정본과 일치        {counts['ok']:5}",
             f"  ⚠️  버퍼가 얹혀 있음   {counts['buffered']:5}   ← 드리프트",
             f"  ·  판정 불가          {counts['undetermined']:5}   (케이스·거치대 등 — 정상일 수 있다)"]
        if drift:
            L.append("")
            L.append("=== 드리프트 상세 (버퍼별) ===")
            for label, items in sorted(by_buf.items(), key=lambda x: -len(x[1])):
                L.append(f"  [{label} +{truth['known_buffers'][label]}] {len(items)}건")
                for r in items[:5]:
                    L.append(f"     {r['internal_sku']:34} {r['cost_price']:>9} → {r['truth']:<9} "
                             f"{(r['product_name'] or '')[:34]}")
                if len(items) > 5:
                    L.append(f"     … 외 {len(items)-5}건")
            L.append("")
            L.append("★옛 매핑 엑셀(ohisell_mapping_template*.xlsx)이 업로드됐을 수 있다 —")
            L.append("  그걸 올리면 2026-08-10에 정본으로 내린 177건이 버퍼로 되돌아간다(08-07 인계본 경고).")
        else:
            L.append("")
            L.append("드리프트 없음 — 마스터가 원가표 정본과 맞다.")
        text = "\n".join(L)

    if a.out:
        pathlib.Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {a.out} ({len(drift)}건 드리프트)")
    else:
        print(text)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
