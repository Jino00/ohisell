#!/usr/bin/env python3
"""NCA 보고서들을 **일별 × 판매방식**으로 접어 Retail분만 뽑는다.

★비례배분 없음 — 「판매방식」 컬럼의 실측 그대로다. 달마다 비중이 완전히 달라서
  (2025-07 0% · 2025-08 94.5% · 2025-11 53.7% · 2026-04~06 100%) 배분은 쓸 수 없다.

★포맷 무관 — 쿠팡은 행이 많으면 xlsx 대신 **TSV**를 준다(2025-10 = 188,094행 / 72MB).
  `read_report()`가 둘 다 읽는다. xlsx라고 가정하면 `BadZipFile`이 나고 "다운로드가 깨졌다"고
  오진한다(2026-08-06에 실제로 그렇게 오진했다).

사용: ohitech_nca_daily.py [보고서 디렉터리]   (기본 ~/.ohisell_nca)
출력: /tmp/claude-501/nca_retail_daily.json = {"YYYY-MM-DD": {"spend":…, "conv":…}}
"""
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohitech_ad_cdp import read_report, sniff_format  # noqa: E402

OUT = "/tmp/claude-501/nca_retail_daily.json"
COL_SPEND = "집행 광고비"
COL_SELL = "판매방식"
COL_DATE = "날짜"
COL_CONV = "첫구매를 통한 광고 전환 매출"


def _num(v):
    try:
        return float(str(v).replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.ohisell_nca")
    spend = collections.defaultdict(float)
    conv = collections.defaultdict(float)
    allsell = collections.defaultdict(lambda: collections.Counter())
    # 같은 날이 두 파일에 있으면(월 단위 + 주 단위 재수집) 중복 합산이 된다.
    # 먼저 읽은 파일이 그 날을 소유한다 — 나중 파일의 같은 날은 건너뛴다.
    owner: dict[str, str] = {}
    report = []
    for p in sorted(glob.glob(os.path.join(src, "*"))):
        if os.path.isdir(p):
            continue
        with open(p, "rb") as f:
            fmt = sniff_format(f.read(4096))
        if fmt == "unknown":
            report.append((os.path.basename(p), fmt, 0))
            continue
        hdr, rows = read_report(p)
        idx = {h: n for n, h in enumerate(hdr)}
        missing = [c for c in (COL_SPEND, COL_SELL, COL_DATE) if c not in idx]
        if missing:
            print(f"  ! {os.path.basename(p)}: 컬럼 없음 {missing} — 건너뜀", file=sys.stderr)
            report.append((os.path.basename(p), fmt, 0))
            continue
        ci, si, di = idx[COL_SPEND], idx[COL_SELL], idx[COL_DATE]
        vi = idx.get(COL_CONV)
        n = 0
        for r in rows:
            raw = str(r[di]).split(".")[0]
            if len(raw) < 8:
                continue
            day = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            own = owner.get(day)
            if own is None:
                owner[day] = p
            elif own != p:
                continue
            n += 1
            allsell[day][str(r[si])] += _num(r[ci])
            if str(r[si]) != "Retail":
                continue
            spend[day] += _num(r[ci])
            if vi is not None:
                conv[day] += _num(r[vi])
        report.append((os.path.basename(p), fmt, n))

    out = {d: {"spend": round(spend[d]), "conv": round(conv.get(d, 0))} for d in sorted(spend)}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), ensure_ascii=False)
    for name, fmt, n in report:
        print(f"  {name:26} {fmt:5} 행 {n:,}")
    total_all = sum(sum(v.values()) for v in allsell.values())
    print(f"\n일수 {len(out)}  NCA 전체 {total_all:,.0f}  그중 Retail {sum(spend.values()):,.0f}  → {OUT}")
    ms = collections.defaultdict(lambda: [0.0, 0.0])
    for d, v in out.items():
        ms[d[:7]][0] += v["spend"]
        ms[d[:7]][1] += v["conv"]
    for m in sorted(ms):
        print(f"  {m}  Retail 광고비 {ms[m][0]:>12,.0f}  전환 {ms[m][1]:>13,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
