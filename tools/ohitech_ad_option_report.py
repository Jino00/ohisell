#!/usr/bin/env python3
"""Billboard 옵션 보고서를 **임의 과거 구간**으로 받아온다 (1P/3P/RG 판별 + 광고비 소급용).

왜 있나 (2026-08-05):
  1P 광고비가 2026-03-17부터만 있었다. 매출(계산서)은 2025-07-23부터라 약 8개월 광고비가
  0으로 잡혀 손익이 과대했다(결손 350,366,257원). `report/SALES`로 소급할 수 있지만 그건
  **계정 총액**이라 판매방식이 섞인다 — 이 계정은 2025-07 전에 로켓그로스 광고를 태웠다.

  이 스크립트가 그걸 가른다. Billboard XLSX에는 **「판매방식」 컬럼**과 **광고집행 옵션ID**가
  있어 Retail(로켓배송=1P) / 3P / RG를 행 단위로 나눌 수 있다.

  ★2025-10 실측: Retail 53,465,961원(97.7%) · 3P 1,276,942원(2.3%) · **RG 0원**.
    Retail 옵션 321개 중 298개(92.8%)가 1P 채널 매핑(ch5)에 있고 RG·3P 매핑과는 교집합 0.
    → "2025년 7월부터 1P로 전환"(Jino)이 데이터로 확인됐다.

사용: ohitech_ad_option_report.py YYYYMMDD YYYYMMDD [출력.xlsx] [--report-type pa|nca|da]
  CDP 9224(오하이테크 광고 Chrome)에 붙어 same-origin으로 호출한다. 보고서 생성에 20~30초.
  ★`ReportType` enum은 `PA`·`NCA`·`DA`(소문자도 됨)다 — NCA도 받을 수 있다(ref 46 §5-1④ 정정).

함정 (전부 실제로 걸렸다 — 이제 `ohitech_ad_cdp`가 구조로 막는다):
  ① 탭이 유휴면 fetch가 `Failed to fetch`로 죽는데, 옛 판은 그걸 빈 응답으로 받아
     **"캠페인 0개"**로 출력하고 정상 종료했다(9개월이 조용히 건너뛰어졌다).
     → `connect()`가 리로드로 재무장하고, `gql()`이 실패를 전부 예외로 올린다.
     이제 "캠페인 0개"는 그 관문을 통과한 뒤에만 나오므로 **진짜 0건**이다.
  ② ★**행이 많으면 서버가 xlsx가 아니라 TSV를 준다**(2025-10 NCA = 188,094행 / 72MB).
     처음엔 "다운로드가 깨졌다"고 오진했다 — 조각으로 다시 받아 보니 길이가 정확히 같고
     내용도 멀쩡했다(TSV 합계가 주 단위 5개와 차이 0원). 진짜 결함은 **포맷이 바뀌는데
     파서가 xlsx만 가정한 것 + 받은 것을 확인하지 않은 것**이었다.
     → `sniff_format()`으로 판정해 확장자를 맞춰 저장하고, `read_report()`가 둘 다 읽는다.
     ⚠️단 prod `option-ingest`는 **xlsx만** 받는다 — TSV가 오면 구간을 쪼개 다시 받아야 한다.
  ③ openpyxl로 옵션ID를 읽으면 float이 되어 `.0`이 붙는다 — 정수 문자열로 정규화할 것.
     (이걸 안 하면 카탈로그 조인이 전부 0건이 되어 "겹치지 않는다"는 거짓 결론이 난다.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohitech_ad_cdp import (  # noqa: E402
    EXCEL_URL,
    GraphQLError,
    SessionDead,
    connect,
    download_binary,
    gql,
    poll_report,
    sniff_format,
    suggest_suffix,
)

Q_CAMPAIGNS = (
    "query GetCampaignListInBillboard($startDate: Int!, $endDate: Int!, $reportType: ReportType!) {\n"
    "  getCampaignList(\n    startDate: $startDate\n    endDate: $endDate\n    reportType: $reportType\n"
    "  ) {\n    id\n    name\n    __typename\n  }\n}\n"
)
M_REQUEST = (
    "mutation ($startDate: Int!, $endDate: Int!, $campaignIds: [ID], $reportType: ReportType!, "
    "$dateGroup: DateGroup!, $granularity: Granularity, $excludeIfNoClickCount: Boolean) {\n"
    "  requestReport(\n    data: {startDate: $startDate, endDate: $endDate, campaignIds: $campaignIds, "
    "reportType: $reportType, dateGroup: $dateGroup, granularity: $granularity, "
    "excludeIfNoClickCount: $excludeIfNoClickCount}\n  ) {\n    id\n    status\n    __typename\n  }\n}\n"
)
Q_LIST = (
    "query ($reportType: ReportType!, $page: Int!, $pageSize: Int!, $duration: Int!) {\n"
    "  reportList(\n    data: {reportType: $reportType, page: $page, pageSize: $pageSize, "
    "duration: $duration}\n  ) {\n    total\n    reports {\n      id\n      status\n      __typename\n"
    "    }\n    __typename\n  }\n}\n"
)

VALID_TYPES = ("pa", "nca", "da")

# 종료 코드 — 호출부(백필 드라이버)가 "세션 문제"와 "정말 없음"을 가를 수 있게 나눈다.
RC_OK = 0
RC_FAIL = 1           # 그 외 실패
RC_SESSION = 3        # 로그인/오리진 죽음 — 재시도해도 같으니 페처 갱신부터
RC_EMPTY = 4          # 세션은 살아 있는데 그 구간에 캠페인이 정말 없다


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rt = "pa"
    for a in sys.argv[1:]:
        if a.startswith("--report-type"):
            rt = (a.split("=", 1)[1] if "=" in a else "pa").strip().lower()
    if "--report-type" in sys.argv:
        i = sys.argv.index("--report-type")
        if i + 1 < len(sys.argv):
            rt = sys.argv[i + 1].strip().lower()
            args = [a for a in args if a != sys.argv[i + 1]]
    if rt not in VALID_TYPES:
        print(f"--report-type은 {VALID_TYPES} 중 하나여야 한다 (받은 값: {rt!r})", file=sys.stderr)
        return RC_FAIL
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return RC_FAIL
    sd, ed = int(args[0]), int(args[1])
    out = args[2] if len(args) > 2 else "opt_report.xlsx"

    try:
        call = connect()          # 리로드로 재무장 — 실패하면 SessionDead
    except SessionDead as ex:
        print(f"세션: {ex}", file=sys.stderr)
        return RC_SESSION

    try:
        d = gql(call, {"operationName": "GetCampaignListInBillboard",
                       "variables": {"startDate": sd, "endDate": ed, "reportType": rt},
                       "query": Q_CAMPAIGNS})
        camps = (d.get("data") or {}).get("getCampaignList") or []
        print(f"캠페인 {len(camps)}개 (reportType={rt})", file=sys.stderr)
        if not camps:
            # ★여기 오려면 재무장·비200·HTML·GraphQL errors를 전부 통과했어야 한다.
            #   그러므로 이건 세션 문제가 아니라 **정말 0건**이다.
            print(f"  → 세션은 살아 있다. {sd}~{ed} 구간에 {rt} 캠페인이 실제로 없다.", file=sys.stderr)
            return RC_EMPTY
        ids = [str(c["id"]) for c in camps if c.get("id")]

        d = gql(call, {"variables": {"reportType": rt, "startDate": sd, "endDate": ed,
                                     "dateGroup": "daily", "granularity": "keyword",
                                     "excludeIfNoClickCount": True, "campaignIds": ids},
                       "query": M_REQUEST})
        req = (d.get("data") or {}).get("requestReport")
        if not req or not req.get("id"):
            print(f"requestReport 실패: {d}", file=sys.stderr)
            return RC_FAIL
        rid = str(req["id"])
        print(f"보고서 id={rid} status={req.get('status')}", file=sys.stderr)

        poll_report(call, rid, rt, Q_LIST)

        def tick(done, total):
            print(f"  받는 중 {done:,}/{total:,} 바이트", file=sys.stderr)

        data = download_binary(call, EXCEL_URL + rid, progress=tick)
    except SessionDead as ex:
        print(f"세션: {ex}", file=sys.stderr)
        return RC_SESSION
    except (GraphQLError, RuntimeError, ValueError) as ex:
        print(f"실패: {ex}", file=sys.stderr)
        return RC_FAIL
    finally:
        try:
            call.close()
        except Exception:  # noqa: BLE001
            pass

    # ★받은 것이 무엇인지 **판정하고 나서** 저장한다(함정 ②).
    #   쿠팡은 행이 많으면 xlsx가 아니라 TSV를 준다 — 확장자만 믿으면 BadZipFile이 나고
    #   "다운로드가 깨졌다"고 오진한다(실제로 한 번 그렇게 오진했다).
    fmt = sniff_format(data)
    if fmt == "unknown":
        print(f"받은 {len(data):,} 바이트가 xlsx도 TSV도 아니다 — 저장하지 않는다. "
              f"머리 60바이트: {data[:60]!r}", file=sys.stderr)
        return RC_FAIL
    want = suggest_suffix(fmt)
    if not out.endswith(want):
        out = os.path.splitext(out)[0] + want
        print(f"  · 서버가 {fmt.upper()}를 줬다 → 확장자를 {want}로 맞춘다", file=sys.stderr)
    with open(out, "wb") as f:
        f.write(data)
    print(f"저장: {out} ({len(data):,} bytes, {fmt} 확인됨)", file=sys.stderr)
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
