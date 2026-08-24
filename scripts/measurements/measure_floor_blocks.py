#!/usr/bin/env python3
"""④ 합격기준 6의 빈 칸 — `interval_floor_blocks_up`으로 «사라지는 액셀 제안» 건수 실측.

읽기 전용. prod에 배포된 «현행» 코드로 돈다 — 새 코드(correction_interval.py)를 안 쓴다.
가능한 이유: `interval_floor_blocks_up` 분기와 `diag["correction_factor"]["factor_low"]`
소비 경로는 D-NAO-231로 «이미 배포돼» 있고, D-NAO-234가 바꾸는 것은 그 자리에 들어가는
«값»(1.0 → 0.827)뿐이다. 그러니 diag 딕셔너리의 factor_low만 갈아끼워 두 번 돌리면
「하한을 내렸을 때 액셀 제안이 몇 건 사라지는가」가 그대로 나온다.

DB는 mode=ro URI로 연다(쓰기 원천 차단). prod 앱·크론 무접촉.
"""
import copy
import os
import sys
from collections import Counter
from datetime import timedelta
from decimal import Decimal

BACKEND = "/home/ubuntu/ohisell/backend"
DB = f"{BACKEND}/ohisell.db"
os.environ["DATABASE_URL"] = f"sqlite:///file:{DB}?mode=ro&uri=true"
sys.path.insert(0, BACKEND)

from app.database import SessionLocal  # noqa: E402
from app.services.naver_ad import diagnosis, proposal_pipeline  # noqa: E402
from app.utils.kst import kst_today  # noqa: E402


def count_bases(sims: dict) -> Counter:
    c = Counter()
    for v in sims.values():
        if isinstance(v, dict):
            c[v.get("basis") or "(none)"] += 1
            c["__direction__" + str(v.get("direction"))] += 1
    return c


def main() -> int:
    lookback_days = 15
    as_of = kst_today() - timedelta(days=1)
    date_from = as_of - timedelta(days=lookback_days - 1)
    print(f"창: {date_from} ~ {as_of} (run_daily과 동일: lookback_days={lookback_days})")

    db = SessionLocal()
    try:
        diag = diagnosis.build_diagnosis(db, date_from, as_of)
        if diag.get("boards") is None:
            print(f"!! diagnosis 실패: {diag.get('error')}")
            return 2

        cf = diag["correction_factor"]
        print(f"라이브 correction_factor: {cf}")

        agg = proposal_pipeline._precompute_aggregates(db, date_from, as_of)

        results = {}
        for label, floor in (("A. 하한 1.0 (현행 prod)", "1.0"),
                             ("B. 하한 0.827 (D-NAO-234)", "0.827")):
            d = copy.deepcopy(diag)
            d["correction_factor"]["factor_low"] = float(Decimal(floor))
            sims = proposal_pipeline.compute_bid_sims(db, d, date_from, as_of, agg=agg)
            results[label] = (sims, count_bases(sims))
            print(f"\n=== {label} — 후보 {len(sims)}건 ===")
            for k, n in sorted(results[label][1].items()):
                if not k.startswith("__"):
                    print(f"  basis {k:34s} {n:5d}")
            for k, n in sorted(results[label][1].items()):
                if k.startswith("__direction__"):
                    print(f"  direction {k[13:]:30s} {n:5d}")

        (sa, ca), (sb, cb) = results["A. 하한 1.0 (현행 prod)"], results["B. 하한 0.827 (D-NAO-234)"]
        blocked_a = ca.get("interval_floor_blocks_up", 0)
        blocked_b = cb.get("interval_floor_blocks_up", 0)
        up_a = ca.get("__direction__up", 0)
        up_b = cb.get("__direction__up", 0)

        print("\n" + "=" * 66)
        print("★ 합격기준 6의 빈 칸 — 하한을 내려서 «사라지는» 액셀 제안")
        print("=" * 66)
        print(f"  interval_floor_blocks_up : {blocked_a} → {blocked_b}  (Δ {blocked_b - blocked_a:+d})")
        print(f"  direction='up' (액셀 생존): {up_a} → {up_b}  (Δ {up_b - up_a:+d})")
        print(f"  후보 총계                : {len(sa)} / {len(sb)}")

        flipped = [k for k in sa
                   if sa[k].get("direction") == "up" and sb.get(k, {}).get("direction") != "up"]
        print(f"\n  ★up → 非up 으로 뒤집힌 개별 대상 {len(flipped)}건:")
        for k in flipped[:40]:
            print(f"    {k}  {sa[k].get('direction')}/{sa[k].get('basis')}"
                  f"  →  {sb.get(k, {}).get('direction')}/{sb.get(k, {}).get('basis')}")
        if len(flipped) > 40:
            print(f"    … 외 {len(flipped) - 40}건")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
