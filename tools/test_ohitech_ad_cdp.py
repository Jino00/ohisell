"""ohitech_ad_cdp 회귀 테스트 — 2026-08-06 두 실사고를 코드로 못 박는다.

실행: python3 tools/test_ohitech_ad_cdp.py   (pytest 로도 돌아간다)

왜 가짜 CDP로 하는가: 진짜 세션을 죽여야만 재현되는 결함이라 라이브로는 반복 검증이 안 된다.
`call`은 CDP 응답 모양만 흉내 내면 되므로 얇게 만들 수 있다.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ohitech_ad_cdp as cdp  # noqa: E402


def fake_call(value):
    """Runtime.evaluate가 `value`를 돌려주는 가짜 CDP."""
    def call(method, params=None):
        if method in ("Runtime.enable", "Page.enable", "Page.reload"):
            return {"result": {}}
        return {"result": {"result": {"type": "string", "value": value}}}
    return call


def expect_raises(exc, fn, label):
    try:
        fn()
    except exc as ex:
        print(f"  ✓ {label} → {type(ex).__name__}: {str(ex)[:70]}")
        return
    except Exception as ex:  # noqa: BLE001
        raise AssertionError(f"{label}: {exc.__name__}를 기대했는데 {type(ex).__name__}: {ex}")
    raise AssertionError(f"{label}: 예외가 안 났다 — 조용한 실패가 되살아났다")


# ── 결함 ①: 세션이 죽은 것을 "데이터 0건"으로 읽지 않는다 ──────────────────
def test_fetch_throw_raises():
    """fetch가 throw하면 예외다. 옛 판은 이걸 빈 응답으로 받아 '캠페인 0개'로 읽었다."""
    call = fake_call(json.dumps({"err": "TypeError: Failed to fetch"}))
    expect_raises(cdp.SessionDead, lambda: cdp.gql(call, {}), "fetch throw")


def test_login_html_raises():
    """200인데 본문이 로그인 HTML인 경우 — 상태코드만 보면 성공처럼 보인다."""
    call = fake_call(json.dumps({"s": 200, "b": "<!DOCTYPE html><html>login</html>"}))
    expect_raises(cdp.SessionDead, lambda: cdp.gql(call, {}), "200 + 로그인 HTML")


def test_non_200_raises():
    call = fake_call(json.dumps({"s": 404, "b": "Cannot POST"}))
    expect_raises(cdp.SessionDead, lambda: cdp.gql(call, {}), "HTTP 404")


def test_graphql_errors_raise():
    call = fake_call(json.dumps({"s": 200, "b": json.dumps(
        {"errors": [{"message": 'Value "cac" does not exist in "ReportType" enum'}]})}))
    expect_raises(cdp.GraphQLError, lambda: cdp.gql(call, {}), "GraphQL errors")


def test_missing_value_raises():
    """CDP가 값을 아예 안 줄 때(오리진 죽음) — 옛 js()는 여기서 {}를 흘렸다."""
    def call(method, params=None):
        return {"result": {"result": {"type": "object"}}}   # value 없음
    expect_raises(cdp.SessionDead, lambda: cdp.evaluate(call, "1"), "CDP value 부재")


def test_empty_campaign_list_is_not_an_error():
    """관문을 다 통과한 빈 목록은 **진짜 0건**이므로 예외가 아니다."""
    call = fake_call(json.dumps({"s": 200, "b": json.dumps({"data": {"getCampaignList": []}})}))
    out = cdp.gql(call, {})
    assert out["data"]["getCampaignList"] == [], out
    print("  ✓ 진짜 0건은 예외 없이 통과한다")


# ── 결함 ②: 큰 파일이 잘리면 성공으로 쓰지 않는다 ──────────────────────────
def _dl_call(payload: bytes, *, lie_length: int | None = None):
    """다운로드 2단계(시작→조각)를 흉내 낸다. lie_length면 서버가 더 크다고 말한 셈."""
    state = {"started": False}

    def call(method, params=None):
        expr = (params or {}).get("expression", "")
        if not state["started"] and "fetch" in expr:
            state["started"] = True
            n = lie_length if lie_length is not None else len(payload)
            return {"result": {"result": {"value": json.dumps({"s": 200, "n": n})}}}
        if "subarray" in expr:
            nums = [int(x) for x in expr.split("subarray(")[1].split(")")[0].split(",")]
            off, end = nums[0], nums[1]
            return {"result": {"result": {
                "value": base64.b64encode(payload[off:end]).decode()}}}
        return {"result": {"result": {"value": 1}}}
    return call


def test_download_reassembles_across_chunks():
    payload = bytes(range(256)) * 5000            # 1.28MB
    got = cdp.download_binary(_dl_call(payload), "u", chunk=100_000)
    assert got == payload, f"조각 재조립 실패: {len(got)} vs {len(payload)}"
    print(f"  ✓ {len(payload):,} 바이트를 13조각으로 받아 원본과 동일")


def test_truncated_download_raises():
    """서버가 말한 길이보다 적게 오면 예외 — 옛 판은 잘린 파일을 그냥 저장했다."""
    payload = b"x" * 1000
    expect_raises(RuntimeError,
                  lambda: cdp.download_binary(_dl_call(payload, lie_length=5000), "u", chunk=400),
                  "잘린 다운로드")


def test_empty_download_raises():
    expect_raises(RuntimeError, lambda: cdp.download_binary(_dl_call(b""), "u"), "0바이트 본문")


# ── 결함 ②의 진짜 원인: 포맷이 바뀐다 ────────────────────────────────────
TSV_HEAD = ('"날짜"\t"과금 방식"\t"판매방식"\t"집행 광고비"\n'
            '"20251001"\t"참여당 과금"\t"Retail"\t"1000"\n'
            '"20251001"\t"참여당 과금"\t"3P"\t"250"\n')


def test_sniff_tsv_not_broken():
    """72MB 비-zip은 깨진 파일이 아니라 **TSV**였다. unknown으로 떨어뜨리면 또 오진한다."""
    assert cdp.sniff_format(TSV_HEAD.encode()) == "tsv"
    assert cdp.suggest_suffix("tsv") == ".tsv"
    print("  ✓ TSV를 TSV로 판정한다(깨진 파일 취급 안 함)")


def test_sniff_xlsx_and_unknown():
    assert cdp.sniff_format(b"PK\x03\x04rest") == "xlsx"
    assert cdp.sniff_format(b"\x00\x01\x02\x03garbage") == "unknown"
    print("  ✓ xlsx / unknown 판정")


def test_read_report_tsv():
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".tsv", delete=False) as f:
        f.write(TSV_HEAD.encode())
        path = f.name
    try:
        hdr, rows = cdp.read_report(path)
        assert hdr[2] == "판매방식", hdr
        got = list(rows)
        assert len(got) == 2, got
        assert got[0][3] == "1000", got[0]
        print("  ✓ read_report가 TSV를 헤더+행으로 읽는다")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"ohitech_ad_cdp 회귀 {len(fns)}건")
    for fn in fns:
        fn()
    print("전부 통과")
