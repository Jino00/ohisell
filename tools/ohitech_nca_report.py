#!/usr/bin/env python3
"""NCA(신규 구매 고객 확보) 보고서 = 옵션 보고서의 `--report-type nca`.

★사본이 아니라 **얇은 별칭**이다. 처음엔 따로 만들었는데 같은 코드를 두 벌 두면 결함도 두 벌
  난다(2026-08-06에 실제로 그랬다 — 세션 오판과 다운로드 깨짐이 파일마다 따로 존재했다).
  실제 구현은 `ohitech_ad_option_report.py`와 `ohitech_ad_cdp.py` 한 곳에만 있다.

왜 NCA를 따로 받는가: `report/SALES`의 `ALL_DELIVERED − DELIVERED`(=비-PA)의 정체가 NCA다.
  NCA는 PA 보고서에 **한 건도 안 잡힌다**(2025-09·10 캠페인 ID 교집합 0). 그런데 NCA 보고서엔
  「판매방식」 컬럼이 있어 Retail/3P를 **비례배분 없이 실측으로 가를 수 있다** — 실제로 달마다
  뒤집힌다(2025-07 3P 100% · 2025-09 Retail 100% · 2025-11 Retail 53.7%). ref 46 §5-1④ 참조.

사용: ohitech_nca_report.py YYYYMMDD YYYYMMDD out.xlsx
⚠️큰 달(2025-10)은 **주 단위로 쪼갤 것** — 조각 전송이 생겨 더는 깨지지 않지만 생성이 느리다.
"""
import os
import runpy
import sys

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    sys.argv = [os.path.join(here, "ohitech_ad_option_report.py"),
                *sys.argv[1:], "--report-type", "nca"]
    runpy.run_path(sys.argv[0], run_name="__main__")
