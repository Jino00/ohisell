# test_naver_sa_writer.py — X1a T2 naver_sa_writer(제외키워드 쓰기 어댑터) 단위테스트
# 근거: docs/references/27_naver_sa_write_api_recon.md. HTTP는 전부 mock(네트워크 호출 0).
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.naver_ad import naver_sa_writer as writer
from app.services.naver_ad.naver_sa_writer import (
    WriteError,
    WriteResult,
    WriteValidationError,
    WriteVerificationError,
)


@pytest.fixture(autouse=True)
def _default_no_shopping_ads():
    """B-4 실효 레이어 가드(D-NAO-164)의 기본 상태 = «쇼핑 소재 없음» → 가드 무접촉.

    `update_adgroup_bid`는 이제 `fetcher.get_ads`로 실효 레이어를 판별한다. 이 파일의
    기존 테스트들은 `_get`을 side_effect 리스트로 모킹하므로, 패치하지 않으면 가드의
    조회가 그 리스트를 한 칸 앞당겨 소비해 **7건이 무관하게 빨개진다**(관계없는 실패는
    이후 모든 검증을 무효로 만든다 — 교훈 #200).

    기본값 `[]`는 「파워링크 등 쇼핑 소재가 없는 그룹」과 같은 상태라 가드가 통과한다.
    B-4 자체를 검사하는 테스트는 각자 안쪽에서 `get_ads`를 다시 패치해 이 값을 덮는다."""
    with patch.object(writer.fetcher, "get_ads", return_value=[]):
        yield


class FakeResp:
    """requests.Response 흉내 — status_code/json()/raise_for_status()/text만 필요."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


ADGROUP_ID = "grp-a001-01-000000031185769"
WEB_SITE_ADGROUP = FakeResp(200, {"nccAdgroupId": ADGROUP_ID, "adgroupType": "WEB_SITE"})
SHOPPING_ADGROUP = FakeResp(200, {"nccAdgroupId": ADGROUP_ID, "adgroupType": "SHOPPING"})


def _restricted_kwds_resp(rows):
    return FakeResp(200, rows)


# ★D-NAO-179: get_restricted_keywords는 **타입마다 한 번씩** GET 한다
# (KEYWORD_PLUS_RESTRICT → EXP_SEARCH). 그래서 제외목록 조회 1회당 응답이 2개 필요하다.
# 이 상수는 «EXP_SEARCH 쪽은 비어 있다»는 뜻이고, 두 타입이 섞이는 경우는 전용 테스트에서 다룬다.
NO_EXP = FakeResp(200, [])


# ── add_restricted_keywords ──────────────────────────────────────────────


def test_add_success_roundtrip():
    """before=[] → POST 200(nccAdgroupRestrictKwdId 발급) → after에 존재 → WriteResult 전수 검증."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp(
        [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어", "type": "KEYWORD_PLUS_RESTRICT"}]
    )
    post_resp = FakeResp(
        200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어", "type": "KEYWORD_PLUS_RESTRICT"}]
    )

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]) as mock_get, \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        result = writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert isinstance(result, WriteResult)
    assert result.action == "add_restricted_keywords"
    assert result.before == []
    assert result.response == post_resp.json()
    assert result.after == after_resp.json()
    assert result.created_ids == ["rkw-1"]
    # adgroup GET 1 + before(타입 2) + after(타입 2) = 5 (D-NAO-179)
    assert mock_get.call_count == 5
    assert mock_post.call_count == 1


def test_add_post_403_raises_write_error_and_no_after_refetch():
    before_resp = _restricted_kwds_resp([])
    post_resp = FakeResp(403, {"message": "signature invalid"})

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP]) as mock_get, \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        with pytest.raises(WriteError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert mock_post.call_count == 1
    assert mock_get.call_count == 3  # adgroup GET + before(타입 2)만, after 재조회 없음


def test_add_post_2xx_but_keyword_missing_in_after_raises_verification_error():
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([])  # 반영 안 됨
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        with pytest.raises(WriteVerificationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])


def test_add_non_web_site_adgroup_raises_validation_error_no_post():
    with patch.object(writer.fetcher, "_get", side_effect=[SHOPPING_ADGROUP]), \
         patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    mock_post.assert_not_called()


def test_add_empty_keywords_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, [])

    mock_get.assert_not_called()
    mock_post.assert_not_called()


def test_add_duplicate_keyword_already_in_before_raises_validation_error_no_post():
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-0", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP]), \
         patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    mock_post.assert_not_called()


def test_add_post_429_no_retry_single_call_raises_write_error():
    before_resp = _restricted_kwds_resp([])
    post_resp = FakeResp(429, {"message": "rate limited"})

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        with pytest.raises(WriteError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert mock_post.call_count == 1  # 재시도 없음(비멱등 쓰기)


def test_add_headers_signed_with_post_method():
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp), \
         patch.object(writer.fetcher, "_headers", return_value={}) as mock_headers:
        writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    post_call = [c for c in mock_headers.call_args_list if c.kwargs.get("method") == "POST" or (len(c.args) > 1 and c.args[1] == "POST")]
    assert len(post_call) == 1
    signed_path = post_call[0].args[0]
    assert signed_path == f"/ncc/adgroups/{ADGROUP_ID}/restricted-keywords"


def test_add_post_2xx_with_unparseable_body_still_succeeds_via_after_verification():
    """201 + 빈/비JSON body여도 쓰기는 이미 서버에 반영됐을 수 있다 — json 파싱 실패로
    실패 표면화하면 '서버엔 성공, 어댑터는 실패' 불일치가 생긴다. 성공 판정의 진실은
    after 재조회이므로 response=None으로 두고 진행해야 한다(created_ids는 after에서 보충)."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp(
        [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}]
    )

    class UnparseableResp(FakeResp):
        def json(self):
            raise ValueError("No JSON object could be decoded")

    post_resp = UnparseableResp(201, None)

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        result = writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert result.response is None
    assert result.created_ids == ["rkw-1"]  # after 재조회에서 보충 수집


def test_add_created_ids_derived_from_after_even_if_post_body_partial():
    """[codex P1] created_ids는 POST 응답이 아니라 검증 완료된 after 재조회에서 파생한다.
    POST 응답에 id가 1개만 있어도(부분 body) 요청 키워드 2개 전부의 id가 after에서 확보돼야 함."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([
        {"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "제외어A"},
        {"nccAdgroupRestrictKwdId": "rkw-2", "keyword": "제외어B"},
    ])
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "제외어A"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        result = writer.add_restricted_keywords(ADGROUP_ID, ["제외어A", "제외어B"])

    assert sorted(result.created_ids) == ["rkw-1", "rkw-2"]


def test_add_after_row_missing_id_field_raises_verification_error():
    """[codex P1 방어] after 행에 nccAdgroupRestrictKwdId가 없으면(요청 keyword당 id 1개
    미확보) 완전성 검증 실패 — WriteVerificationError."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([{"keyword": "테스트제외어"}])  # id 필드 누락
    post_resp = FakeResp(200, [{"keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        with pytest.raises(WriteVerificationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])


def test_add_after_has_multiple_rows_for_same_keyword_raises_verification_error():
    """[codex P1 2R] before/after 사이 다른 행위자(콘솔의 사람·MOP)가 같은 키워드를 등록하면
    after에 같은 keyword 행이 2개 이상 생길 수 있다 — 어느 행이 이번 쓰기 결과인지 판별 불가.
    임의 id 채택은 T3 원복이 남의 행을 삭제할 위험 → WriteVerificationError(fail-closed)."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([
        {"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"},
        {"nccAdgroupRestrictKwdId": "rkw-2", "keyword": "테스트제외어"},  # 동시 등록된 남의 행
    ])
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        with pytest.raises(WriteVerificationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])


def test_add_duplicate_keywords_within_request_raises_validation_error_no_http():
    """[codex P2] 요청 리스트 내부 중복 — 서버 동작 미문서라 진입 차단(HTTP 0회)."""
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어", "테스트제외어"])

    mock_get.assert_not_called()
    mock_post.assert_not_called()


# ── delete_restricted_keywords ───────────────────────────────────────────


def test_delete_success():
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    after_resp = _restricted_kwds_resp([])
    delete_resp = FakeResp(204, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "delete", return_value=delete_resp) as mock_delete:
        result = writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-1"])

    assert result.action == "delete_restricted_keywords"
    assert result.before == before_resp.json()
    assert result.response is None
    assert result.after == []
    assert result.created_ids == []
    assert mock_delete.call_count == 1


def test_delete_2xx_but_id_still_present_raises_verification_error():
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    after_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    delete_resp = FakeResp(204, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "delete", return_value=delete_resp):
        with pytest.raises(WriteVerificationError):
            writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-1"])


def test_delete_headers_signed_with_delete_method_and_no_query_in_path():
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    after_resp = _restricted_kwds_resp([])
    delete_resp = FakeResp(204, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, NO_EXP, after_resp, NO_EXP]), \
         patch.object(writer.requests, "delete", return_value=delete_resp), \
         patch.object(writer.fetcher, "_headers", return_value={}) as mock_headers:
        writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-1"])

    delete_call = [c for c in mock_headers.call_args_list if c.kwargs.get("method") == "DELETE" or (len(c.args) > 1 and c.args[1] == "DELETE")]
    assert len(delete_call) == 1
    signed_path = delete_call[0].args[0]
    assert signed_path == f"/ncc/adgroups/{ADGROUP_ID}/restricted-keywords"
    assert "?" not in signed_path and "ids=" not in signed_path


def test_delete_id_not_in_before_raises_validation_error_no_delete():
    """[codex P1] before에 없는 id 삭제 요청 = stale/오타 id — 204 no-op가 되면 'after에 없음'
    검증이 공허하게 통과하는 구멍. DELETE 호출 전에 차단한다."""
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, NO_EXP]), \
         patch.object(writer.requests, "delete") as mock_delete:
        with pytest.raises(WriteValidationError):
            writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-STALE"])

    mock_delete.assert_not_called()


def test_delete_429_no_retry_single_call_raises_write_error():
    """[codex P2] DELETE 429 → 재시도 없이 즉시 WriteError(POST 대칭 — 쓰기 무재시도 원칙)."""
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    delete_resp = FakeResp(429, {"message": "rate limited"})

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, NO_EXP]), \
         patch.object(writer.requests, "delete", return_value=delete_resp) as mock_delete:
        with pytest.raises(WriteError):
            writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-1"])

    assert mock_delete.call_count == 1


def test_delete_empty_ids_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "delete") as mock_delete:
        with pytest.raises(WriteValidationError):
            writer.delete_restricted_keywords(ADGROUP_ID, [])

    mock_get.assert_not_called()
    mock_delete.assert_not_called()


# ── get_restricted_keywords ──────────────────────────────────────────────


def test_get_restricted_keywords_returns_raw_json():
    rows = [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}]
    with patch.object(writer.fetcher, "_get", side_effect=[_restricted_kwds_resp(rows), NO_EXP]) as mock_get:
        out = writer.get_restricted_keywords(ADGROUP_ID)

    assert out == rows
    args, kwargs = mock_get.call_args
    assert args[0] == f"/ncc/adgroups/{ADGROUP_ID}/restricted-keywords"


# ── D-NAO-179: 제외키워드 타입은 둘이고 둘은 분리된 목록이다 ──────────────
# 라이브 실증(2026-08-16): WEB_SITE 그룹에 EXP_SEARCH 1건을 만들면 EXP_SEARCH 조회엔 1건,
# KEYWORD_PLUS_RESTRICT 조회엔 0건. 계정 전수로는 723건 중 711건이 EXP_SEARCH였다.


def test_get_restricted_keywords_unions_both_types():
    """한 타입만 물으면 나머지가 «없는 것»이 된다 — 그게 711건 전맹의 기제였다."""
    kp = [{"nccAdgroupRestrictKwdId": "rkw-kp", "keyword": "키워드확장제외", "type": "KEYWORD_PLUS_RESTRICT"}]
    exp = [{"nccAdgroupRestrictKwdId": "rkw-exp", "keyword": "확장검색제외", "type": "EXP_SEARCH"}]

    with patch.object(writer.fetcher, "_get",
                      side_effect=[_restricted_kwds_resp(kp), _restricted_kwds_resp(exp)]) as mock_get:
        out = writer.get_restricted_keywords(ADGROUP_ID)

    assert [r["keyword"] for r in out] == ["키워드확장제외", "확장검색제외"]
    asked = [c.args[1]["type"] for c in mock_get.call_args_list]
    assert asked == ["KEYWORD_PLUS_RESTRICT", "EXP_SEARCH"]


def test_get_restricted_keywords_stamps_missing_type():
    """행에 type이 없으면 조회한 타입으로 채운다 — 나중에 «어느 목록에서» 지울지가 그 값에 달렸다."""
    with patch.object(writer.fetcher, "_get",
                      side_effect=[_restricted_kwds_resp([{"keyword": "타입없는행"}]), NO_EXP]):
        out = writer.get_restricted_keywords(ADGROUP_ID)

    assert out[0]["type"] == "KEYWORD_PLUS_RESTRICT"


def test_get_restricted_keywords_second_type_failure_raises_not_partial():
    """★fail-closed: 두 번째 타입 조회가 죽으면 **부분 union을 성공으로 돌려주지 않는다.**
    부분 목록은 「없다」와 구분되지 않고, 생존감시는 그걸 「제외가 사라졌다」로 읽는다
    (D-NAO-174가 정확히 그 결함으로 P1을 맞았다)."""
    with patch.object(writer.fetcher, "_get",
                      side_effect=[_restricted_kwds_resp([{"keyword": "살아있는제외"}]), FakeResp(500, "boom")]):
        with pytest.raises(RuntimeError):
            writer.get_restricted_keywords(ADGROUP_ID)


def test_add_duplicate_guard_sees_keyword_registered_as_exp_search():
    """이미 EXP_SEARCH로 걸려 있는 키워드를 다시 등록하려 하면 중복 가드가 잡아야 한다.
    종전엔 KEYWORD_PLUS_RESTRICT만 봐서 «없다»로 통과했다."""
    with patch.object(writer.fetcher, "_get", side_effect=[
        WEB_SITE_ADGROUP,
        _restricted_kwds_resp([]),                                    # KEYWORD_PLUS: 없음
        _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-e",
                                "keyword": "테스트제외어", "type": "EXP_SEARCH"}]),
    ]), patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    mock_post.assert_not_called()


def test_fetcher_restricted_count_sums_both_types():
    """★두 번째 하드코딩 지점 — `naver_sa_ad_fetcher`는 writer를 import하지 않으려고 같은 상수를
    **따로** 들고 있었다(BM 단일 책임). 그래서 writer만 고치면 BM deep 차원은 조용히 옛 숫자를
    센다. 값이 갈라지는 곳은 한 번에 같이 본다."""
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, [{"keyword": "a"}]),
        FakeResp(200, [{"keyword": "b"}, {"keyword": "c"}]),
    ]) as mock_get:
        assert writer.fetcher.get_restricted_keyword_count(ADGROUP_ID) == 3

    assert [c.args[1]["type"] for c in mock_get.call_args_list] == [
        "KEYWORD_PLUS_RESTRICT", "EXP_SEARCH",
    ]


def test_fetcher_restricted_count_raises_on_partial_failure():
    """부분합을 숫자로 돌려주지 않는다 — 숫자는 «모름»을 표현할 수 없다."""
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, [{"keyword": "a"}]), FakeResp(500, "boom"),
    ]):
        with pytest.raises(RuntimeError):
            writer.fetcher.get_restricted_keyword_count(ADGROUP_ID)


def test_delete_accepts_id_registered_as_exp_search():
    """EXP_SEARCH로 등록된 제외도 id가 before union에 있으니 삭제가 진행된다
    (종전엔 before에서 못 찾아 stale id로 오인해 거부했다)."""
    delete_resp = FakeResp(204, None)
    with patch.object(writer.fetcher, "_get", side_effect=[
        _restricted_kwds_resp([]),                                    # before KEYWORD_PLUS
        _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-e",
                                "keyword": "확장검색제외", "type": "EXP_SEARCH"}]),
        _restricted_kwds_resp([]), NO_EXP,                            # after 둘 다 비었다
    ]), patch.object(writer.requests, "delete", return_value=delete_resp) as mock_delete:
        result = writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-e"])

    assert result.after == []
    assert mock_delete.call_count == 1


# ── X1b T1: update_keyword_bid / set_keyword_lock / set_adgroup_lock / set_campaign_lock ──
# 근거: ref 27 §3(bidAmt)·§4(userLock 3계층), swagger definitions(Adgroup/Campaign/AdKeyword)
# 재확인(2026-07-10) — PUT 응답 200(갱신 body)+201 둘 다 정의, 기존 add_restricted_keywords와
# 동일하게 2xx 전부 성공으로 취급한다.

KEYWORD_ID = "nkw-a001-01-000005009913563"
CAMPAIGN_ID = "cmp-a001-01-000000010206612"


def _keyword_resp(bid_amt=190, user_lock=False):
    return FakeResp(200, {
        "nccKeywordId": KEYWORD_ID, "nccAdgroupId": ADGROUP_ID, "keyword": "오하이",
        "bidAmt": bid_amt, "useGroupBidAmt": False, "userLock": user_lock, "status": "ELIGIBLE",
    })


def _adgroup_resp(user_lock=False):
    return FakeResp(200, {"nccAdgroupId": ADGROUP_ID, "adgroupType": "WEB_SITE", "userLock": user_lock})


def _campaign_resp(user_lock=False):
    return FakeResp(200, {"nccCampaignId": CAMPAIGN_ID, "userLock": user_lock})


# ── get_keyword / get_campaign ───────────────────────────────────────────


def test_get_keyword_returns_raw_json():
    with patch.object(writer.fetcher, "_get", return_value=_keyword_resp()) as mock_get:
        out = writer.get_keyword(KEYWORD_ID)

    assert out == _keyword_resp().json()
    args, kwargs = mock_get.call_args
    assert args[0] == f"/ncc/keywords/{KEYWORD_ID}"


def test_get_campaign_returns_raw_json():
    with patch.object(writer.fetcher, "_get", return_value=_campaign_resp()) as mock_get:
        out = writer.get_campaign(CAMPAIGN_ID)

    assert out == _campaign_resp().json()
    args, kwargs = mock_get.call_args
    assert args[0] == f"/ncc/campaigns/{CAMPAIGN_ID}"


# ── update_keyword_bid ───────────────────────────────────────────────────


def test_update_bid_success_roundtrip():
    before = _keyword_resp(bid_amt=190)
    after = _keyword_resp(bid_amt=500)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.update_keyword_bid(KEYWORD_ID, 500)

    assert isinstance(result, WriteResult)
    assert result.action == "update_keyword_bid"
    assert result.before == before.json()
    assert result.after == after.json()
    assert result.created_ids == []
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_update_bid_body_includes_use_group_bid_amt_false():
    before = _keyword_resp(bid_amt=190)
    after = _keyword_resp(bid_amt=500)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.update_keyword_bid(KEYWORD_ID, 500)

    _, kwargs = mock_put.call_args
    assert kwargs["json"] == {"nccKeywordId": KEYWORD_ID, "bidAmt": 500, "useGroupBidAmt": False}
    assert kwargs["params"] == {"fields": "bidAmt"}


def test_update_bid_below_min_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_keyword_bid(KEYWORD_ID, 60)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_bid_above_max_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_keyword_bid(KEYWORD_ID, 100_010)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_bid_not_multiple_of_10_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_keyword_bid(KEYWORD_ID, 505)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_bid_put_4xx_raises_write_error_no_after_refetch():
    before = _keyword_resp(bid_amt=190)
    put_resp = FakeResp(403, {"message": "forbidden"})

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        with pytest.raises(WriteError):
            writer.update_keyword_bid(KEYWORD_ID, 500)

    assert mock_get.call_count == 1  # before만, after 재조회 없음
    assert mock_put.call_count == 1


def test_update_bid_verification_mismatch_raises_verification_error():
    before = _keyword_resp(bid_amt=190)
    after = _keyword_resp(bid_amt=190)  # PUT은 성공했다는데 반영 안 됨
    put_resp = FakeResp(200, _keyword_resp(bid_amt=500).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_keyword_bid(KEYWORD_ID, 500)


def test_update_bid_use_group_bid_amt_still_true_raises_verification_error():
    """[codex P2] bidAmt는 요청대로 재조회돼도 useGroupBidAmt가 여전히 true면 실효 CPC는
    광고그룹 입찰가를 그대로 쓴다(우리 키워드별 입찰이 반영 안 됨) — bidAmt만 보고 성공
    판정하면 '응답은 성공, 실효 반영은 실패'를 놓친다. fail-closed."""
    before = FakeResp(200, {
        "nccKeywordId": KEYWORD_ID, "nccAdgroupId": ADGROUP_ID, "keyword": "오하이",
        "bidAmt": 190, "useGroupBidAmt": True, "userLock": False, "status": "ELIGIBLE",
    })
    after = FakeResp(200, {
        "nccKeywordId": KEYWORD_ID, "nccAdgroupId": ADGROUP_ID, "keyword": "오하이",
        "bidAmt": 500, "useGroupBidAmt": True, "userLock": False, "status": "ELIGIBLE",  # 반전 안 됨
    })
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_keyword_bid(KEYWORD_ID, 500)


def test_update_bid_headers_signed_with_put_method_and_no_query_in_path():
    before = _keyword_resp(bid_amt=190)
    after = _keyword_resp(bid_amt=500)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp), \
         patch.object(writer.fetcher, "_headers", return_value={}) as mock_headers:
        writer.update_keyword_bid(KEYWORD_ID, 500)

    put_call = [c for c in mock_headers.call_args_list if c.kwargs.get("method") == "PUT"]
    assert len(put_call) == 1
    signed_path = put_call[0].args[0]
    assert signed_path == f"/ncc/keywords/{KEYWORD_ID}"
    assert "?" not in signed_path and "fields=" not in signed_path


def test_update_bid_put_2xx_unparseable_body_still_verified_via_after():
    before = _keyword_resp(bid_amt=190)
    after = _keyword_resp(bid_amt=500)

    class UnparseableResp(FakeResp):
        def json(self):
            raise ValueError("no json")

    put_resp = UnparseableResp(200, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        result = writer.update_keyword_bid(KEYWORD_ID, 500)

    assert result.response is None
    assert result.after == after.json()


# ── set_keyword_lock ─────────────────────────────────────────────────────


def test_set_keyword_lock_pause_success_roundtrip():
    before = _keyword_resp(user_lock=False)
    after = _keyword_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.set_keyword_lock(KEYWORD_ID, True)

    assert result.action == "set_keyword_lock"
    assert result.before == before.json()
    assert result.after == after.json()
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_set_keyword_lock_resume_success_roundtrip():
    before = _keyword_resp(user_lock=True)
    after = _keyword_resp(user_lock=False)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        result = writer.set_keyword_lock(KEYWORD_ID, False)

    assert result.after["userLock"] is False


def test_set_keyword_lock_body_shape():
    before = _keyword_resp(user_lock=False)
    after = _keyword_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.set_keyword_lock(KEYWORD_ID, True)

    _, kwargs = mock_put.call_args
    assert kwargs["json"] == {"nccKeywordId": KEYWORD_ID, "userLock": True}
    assert kwargs["params"] == {"fields": "userLock"}


def test_set_keyword_lock_non_bool_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.set_keyword_lock(KEYWORD_ID, "true")  # 문자열 — bool 아님

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_set_keyword_lock_put_4xx_raises_write_error():
    before = _keyword_resp(user_lock=False)
    put_resp = FakeResp(500, {"message": "server error"})

    with patch.object(writer.fetcher, "_get", side_effect=[before]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteError):
            writer.set_keyword_lock(KEYWORD_ID, True)


def test_set_keyword_lock_verification_mismatch_raises_verification_error():
    before = _keyword_resp(user_lock=False)
    after = _keyword_resp(user_lock=False)  # 반영 안 됨
    put_resp = FakeResp(200, _keyword_resp(user_lock=True).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.set_keyword_lock(KEYWORD_ID, True)


# ── set_adgroup_lock ─────────────────────────────────────────────────────


def test_set_adgroup_lock_success_roundtrip():
    before = _adgroup_resp(user_lock=False)
    after = _adgroup_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.set_adgroup_lock(ADGROUP_ID, True)

    assert result.action == "set_adgroup_lock"
    assert result.after["userLock"] is True
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_set_adgroup_lock_body_shape_no_customer_id():
    before = _adgroup_resp(user_lock=False)
    after = _adgroup_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.set_adgroup_lock(ADGROUP_ID, True)

    _, kwargs = mock_put.call_args
    assert kwargs["json"] == {"nccAdgroupId": ADGROUP_ID, "userLock": True}
    assert kwargs["params"] == {"fields": "userLock"}


def test_set_adgroup_lock_verification_mismatch_raises_verification_error():
    before = _adgroup_resp(user_lock=False)
    after = _adgroup_resp(user_lock=False)
    put_resp = FakeResp(200, _adgroup_resp(user_lock=True).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.set_adgroup_lock(ADGROUP_ID, True)


# ── set_campaign_lock ────────────────────────────────────────────────────


def test_set_campaign_lock_success_roundtrip():
    before = _campaign_resp(user_lock=False)
    after = _campaign_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.set_campaign_lock(CAMPAIGN_ID, True)

    assert result.action == "set_campaign_lock"
    assert result.after["userLock"] is True
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_set_campaign_lock_body_includes_customer_id():
    before = _campaign_resp(user_lock=False)
    after = _campaign_resp(user_lock=True)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.set_campaign_lock(CAMPAIGN_ID, True)

    _, kwargs = mock_put.call_args
    assert kwargs["json"] == {
        "nccCampaignId": CAMPAIGN_ID, "customerId": int(writer.fetcher.CUSTOMER_ID), "userLock": True,
    }
    assert kwargs["params"] == {"fields": "userLock"}


def test_set_campaign_lock_verification_mismatch_raises_verification_error():
    before = _campaign_resp(user_lock=False)
    after = _campaign_resp(user_lock=False)
    put_resp = FakeResp(200, _campaign_resp(user_lock=True).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.set_campaign_lock(CAMPAIGN_ID, True)


def test_set_campaign_lock_put_4xx_raises_write_error_no_after_refetch():
    before = _campaign_resp(user_lock=False)
    put_resp = FakeResp(404, {"message": "not found"})

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteError):
            writer.set_campaign_lock(CAMPAIGN_ID, True)

    assert mock_get.call_count == 1


# ── update_adgroup_bid (쇼핑 광고그룹 단위 입찰가, D-NAO-16 3단계 SHOPPING 대칭 확장) ──
# 근거: ref 27 §85 + swagger(ncc-heroes-ncc.json) AdgroupRequest.bidAmt(70~100,000, 10원 단위,
# fields=bidAmt) 실측(2026-07-14). 키워드의 useGroupBidAmt 커플링은 adgroup엔 없음(adgroup이
# 입찰 최하위 단위). 대신 systemBiddingType(NONE|ML)·autobidStrategy.isAutobidActive로 ML
# 자동입찰 충돌을 사전 차단한다(수동 bidAmt PUT이 자동입찰과 충돌/무의미해지는 것을 방지).


def _adgroup_bid_resp(bid_amt=1200, system_bidding_type="NONE", is_autobid_active=False,
                       autobid_strategy=None):
    body = {
        "nccAdgroupId": ADGROUP_ID, "adgroupType": "SHOPPING", "bidAmt": bid_amt,
        "systemBiddingType": system_bidding_type,
    }
    if autobid_strategy is not None:
        body["autobidStrategy"] = autobid_strategy
    else:
        # 실제 수동입찰 그룹은 autobidStrategy.isAutobidActive=False를 명시적으로 반환한다
        # (S0-2 라이브 실측). codex[P1] 강화 가드는 이 값이 explicit False일 때만 수동으로
        # 인정하므로, 기본 mock도 실물처럼 명시값을 포함한다.
        body["autobidStrategy"] = {"isAutobidActive": is_autobid_active}
    return FakeResp(200, body)


def test_update_adgroup_bid_success_roundtrip():
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert isinstance(result, WriteResult)
    assert result.action == "update_adgroup_bid"
    assert result.before == before.json()
    assert result.after == after.json()
    assert result.created_ids == []
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_update_adgroup_bid_body_shape():
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.update_adgroup_bid(ADGROUP_ID, 1000)

    _, kwargs = mock_put.call_args
    assert kwargs["json"] == {"nccAdgroupId": ADGROUP_ID, "bidAmt": 1000}
    assert kwargs["params"] == {"fields": "bidAmt"}


def test_update_adgroup_bid_below_min_raises_validation_error_no_http():
    # VT4 P1-1: adgroup grain 하한 = 50원 → 40원은 여전히 차단(50 미만).
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 40)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_adgroup_bid_50_passes_min_bid():
    """VT4 P1-1: adgroup grain 하한 50원 — 50원 발사가 검증 통과(60원도 통과, prod 158그룹
    bid=50 라이브 실증). keyword grain(70)과 달리 adgroup은 쇼핑검색 최소 유효입찰 50원."""
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=50)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.update_adgroup_bid(ADGROUP_ID, 50)

    assert result.action == "update_adgroup_bid"
    assert mock_put.call_count == 1


def test_update_adgroup_bid_above_max_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 100_010)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_adgroup_bid_not_multiple_of_10_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1005)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_adgroup_bid_ml_autobid_type_raises_validation_error_no_put():
    """systemBiddingType='ML'이면 시스템 자동입찰 중 — 수동 bidAmt PUT은 충돌/무의미해
    fail-closed 차단한다(before 재조회만, PUT 시도 자체를 안 함)."""
    before = _adgroup_bid_resp(bid_amt=1200, system_bidding_type="ML")

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert mock_get.call_count == 1
    mock_put.assert_not_called()


def test_update_adgroup_bid_is_autobid_active_true_raises_validation_error_no_put():
    """systemBiddingType='NONE'이어도 autobidStrategy.isAutobidActive=true면 자동입찰 활성 —
    동일하게 fail-closed 차단."""
    before = _adgroup_bid_resp(bid_amt=1200, system_bidding_type="NONE", is_autobid_active=True)

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert mock_get.call_count == 1
    mock_put.assert_not_called()


def test_update_adgroup_bid_missing_system_bidding_type_field_raises_validation_error_no_put():
    """[fail-closed] systemBiddingType 필드 자체가 응답에 없으면(None) 'NONE'과 다르므로
    안전 쪽(차단)으로 판정한다 — 추정으로 '수동입찰이겠지'라고 통과시키지 않는다."""
    before = FakeResp(200, {"nccAdgroupId": ADGROUP_ID, "adgroupType": "SHOPPING", "bidAmt": 1200})

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert mock_get.call_count == 1
    mock_put.assert_not_called()


def test_update_adgroup_bid_none_systembidding_but_missing_autobid_flag_blocks():
    """[codex P1] systemBiddingType='NONE'이어도 autobidStrategy(또는 isAutobidActive)가
    응답에 없으면 isAutobidActive를 False로 강제하지 않고 차단한다 — 부분응답/스키마변경 시
    ML 가드를 우회하던 것 방지(explicit False일 때만 수동 인정, fail-closed on ambiguity)."""
    # systemBiddingType='NONE'이지만 autobidStrategy 필드 자체가 없음(isAutobidActive 불명)
    before = FakeResp(200, {
        "nccAdgroupId": ADGROUP_ID, "adgroupType": "SHOPPING", "bidAmt": 1200,
        "systemBiddingType": "NONE",
    })

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert mock_get.call_count == 1
    mock_put.assert_not_called()


def test_update_adgroup_bid_put_4xx_raises_write_error_no_after_refetch():
    before = _adgroup_bid_resp(bid_amt=1200)
    put_resp = FakeResp(403, {"message": "forbidden"})

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        with pytest.raises(WriteError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert mock_get.call_count == 1  # before만, after 재조회 없음
    assert mock_put.call_count == 1


def test_update_adgroup_bid_verification_mismatch_raises_verification_error():
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1200)  # PUT은 성공했다는데 반영 안 됨
    put_resp = FakeResp(200, _adgroup_bid_resp(bid_amt=1000).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_adgroup_bid(ADGROUP_ID, 1000)


def test_update_adgroup_bid_headers_signed_with_put_method_and_no_query_in_path():
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp), \
         patch.object(writer.fetcher, "_headers", return_value={}) as mock_headers:
        writer.update_adgroup_bid(ADGROUP_ID, 1000)

    put_call = [c for c in mock_headers.call_args_list if c.kwargs.get("method") == "PUT"]
    assert len(put_call) == 1
    signed_path = put_call[0].args[0]
    assert signed_path == f"/ncc/adgroups/{ADGROUP_ID}"
    assert "?" not in signed_path and "fields=" not in signed_path


def test_update_adgroup_bid_put_2xx_unparseable_body_still_verified_via_after():
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)

    class UnparseableResp(FakeResp):
        def json(self):
            raise ValueError("no json")

    put_resp = UnparseableResp(200, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        result = writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert result.response is None
    assert result.after == after.json()


# ── B-4 실효 레이어 가드 (D-NAO-164 · 교훈 #202, 2026-08-10) ────────────────────
# 쇼핑 소재가 전부 useGroupBidAmt=false면 그룹 입찰은 옥션에서 아무것도 지배하지 않는다.
# 그런 그룹에 PUT하면 API는 200을 주고 재조회도 새 값을 돌려주므로 «성공»으로 보이지만
# CPC는 안 바뀐다 — 라이브 실사고: 03. 아이폰_강화유리에서 PAO가 9일간 그룹 입찰 59건
# (전부 상향)을 썼는데 소재 36/36이 false라 전부 무접촉이었다.
#
# ★가드는 fail-**open**이다(이 파일의 다른 가드와 방향 반대, 의도적): 막는 대상이
# «돈이 새는 쓰기»가 아니라 «아무 일도 안 일어나는 쓰기»라 오탐의 대가가 더 크다.


def _ad(use_group_bid_amt):
    """fetcher.get_ads() 반환 원소 모양(필요 필드만)."""
    return {"ad_id": "nad-x", "adgroup_id": ADGROUP_ID, "use_group_bid_amt": use_group_bid_amt}


def test_b4_rejects_when_every_shopping_ad_overrides_group_bid():
    """★사고 입력 재현: 03 캠페인처럼 소재 전부 useGroupBidAmt=false → 거부.

    `prove-the-guard-catches-this-input` 패턴 준수 — 가드가 «막을 것 같은» 입력이 아니라
    **실제로 사고를 낸 입력**(36개 전부 false)으로 돌린다."""
    ads = [_ad(False) for _ in range(36)]

    with patch.object(writer.fetcher, "get_ads", return_value=ads), \
         patch.object(writer.fetcher, "_get", return_value=_adgroup_bid_resp()) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError) as exc:
            writer.update_adgroup_bid(ADGROUP_ID, 2590)

    assert "useGroupBidAmt" in str(exc.value)
    assert "update_ad_bid" in str(exc.value)  # 실효 레버를 안내해야 한다
    mock_put.assert_not_called()               # ★네트워크 쓰기가 일어나지 않았다
    assert mock_get.call_count == 1            # before 재조회까지만(after 재조회 없음)


def test_b4_allows_when_one_ad_follows_group_bid():
    """소재 하나라도 useGroupBidAmt=true면 그룹 입찰이 실효 → 통과(과차단 금지)."""
    ads = [_ad(False), _ad(False), _ad(True)]
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)

    with patch.object(writer.fetcher, "get_ads", return_value=ads), \
         patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, after.json())) as mock_put:
        result = writer.update_adgroup_bid(ADGROUP_ID, 1000)

    assert result.action == "update_adgroup_bid"
    mock_put.assert_called_once()


def test_b4_allows_when_flag_unknown_is_mixed_in():
    """use_group_bid_amt=None(파싱 실패·adAttr 부재)이 섞이면 통과 — 「전부 false」를
    **적극 입증**했을 때만 거부한다(추정 금지)."""
    ads = [_ad(False), _ad(None)]
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)

    with patch.object(writer.fetcher, "get_ads", return_value=ads), \
         patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, after.json())) as mock_put:
        writer.update_adgroup_bid(ADGROUP_ID, 1000)

    mock_put.assert_called_once()


def test_b4_allows_when_no_shopping_ads_powerlink_group():
    """소재 0건(파워링크 그룹 — 키워드가 입찰을 진다) → 통과. 쇼핑 판별자를 그쪽에 적용하지 않는다."""
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)

    with patch.object(writer.fetcher, "get_ads", return_value=[]), \
         patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, after.json())) as mock_put:
        writer.update_adgroup_bid(ADGROUP_ID, 1000)

    mock_put.assert_called_once()


def test_b4_fails_open_when_ads_lookup_raises():
    """소재 조회가 터지면 통과(fail-open) — 조회 장애가 광고 운영 정지가 되면 안 된다."""
    before = _adgroup_bid_resp(bid_amt=1200)
    after = _adgroup_bid_resp(bid_amt=1000)

    with patch.object(writer.fetcher, "get_ads", side_effect=RuntimeError("네이버 500")), \
         patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=FakeResp(200, after.json())) as mock_put:
        writer.update_adgroup_bid(ADGROUP_ID, 1000)

    mock_put.assert_called_once()


def test_b4_runs_after_validation_so_bad_bid_costs_no_api_call():
    """범위 밖 입찰가는 판별자 조회 **이전에** 걸러진다(불필요한 API 콜 0)."""
    with patch.object(writer.fetcher, "get_ads") as mock_ads, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_adgroup_bid(ADGROUP_ID, 40)

    mock_ads.assert_not_called()
    mock_put.assert_not_called()


# ── D-NAO-170: 거부는 «데이터 수리 신호»다 — 관측을 실어 보낸다 ──────────────────
# D-NAO-166은 거부하면서 손에 쥔 정답(라이브 소재 목록)을 **버렸다.** 그래서 라우터(DB 파생)와
# 가드(라이브)가 갈라진 그룹은 회차마다 같은 거부만 반복했다. 예외에 관측을 실으면 상위가
# 그걸 DB에 되돌려 써서, 다음 회차에 **기존 라우터가 스스로** 소재로 절체한다.


def test_b4_rejection_carries_live_ad_observation():
    """거부 예외가 adgroup_id와 라이브 소재 목록을 **실어 나른다**(추가 API 콜 0).

    이게 없으면 상위는 「무엇을 고쳐야 하는지」를 알기 위해 같은 조회를 다시 해야 하고,
    그러면 수리 경로가 조회 장애에 다시 노출된다.
    """
    ads = [
        {"ad_id": "nad-1", "adgroup_id": ADGROUP_ID, "mall_product_id": "111",
         "use_group_bid_amt": False, "ad_bid_amt": 800, "ad_user_lock": False},
        {"ad_id": "nad-2", "adgroup_id": ADGROUP_ID, "mall_product_id": "222",
         "use_group_bid_amt": False, "ad_bid_amt": 500, "ad_user_lock": False},
    ]
    with patch.object(writer.fetcher, "get_ads", return_value=ads) as mock_get_ads, \
         patch.object(writer.fetcher, "_get", return_value=_adgroup_bid_resp()), \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(writer.GroupBidDeadError) as exc:
            writer.update_adgroup_bid(ADGROUP_ID, 2590)

    assert exc.value.adgroup_id == ADGROUP_ID
    assert exc.value.ads == ads
    mock_get_ads.assert_called_once()  # ★관측 재조회 없음 — 판별에 쓴 응답 그대로 싣는다
    mock_put.assert_not_called()


def test_group_bid_dead_error_is_a_write_validation_error():
    """기존 호출부 호환 — `WriteValidationError`로 잡던 코드가 그대로 잡아야 한다.

    새 예외 타입을 만들면서 상위의 except를 «조용히 통과»하게 만들면, 막으려던 쓰기가
    실패로 기록되지 않고 다른 경로로 샌다.
    """
    assert issubclass(writer.GroupBidDeadError, WriteValidationError)


# ═══ D-NAO-180 — 쇼핑 제외 읽기 (`GET /ncc/targets`) ═══
#
# ★배경: 「쇼핑 제외는 API로 못 본다」는 전제가 2026-08-17에 무너졌다. 안 보였던 것은
#   `restricted-keywords` **리소스**였지 쇼핑 제외 자체가 아니다 — `/ncc/targets`의
#   `RESTRICT_KEYWORD_TARGET`이 3,880건/116그룹을 돌려주고 콘솔 캡처분과 차집합 0으로 일치했다.
#   아래는 그 읽기가 **하류가 이미 아는 모양으로** 정규화돼 나오는지를 겨눈다.

# 라이브 실물(2026-08-17 `grp-a001-02-000000047076738`) — 항목은 keyword/type/date뿐이고
# **키워드 단위 id가 없다**. id는 `nccTargetId` 하나로 **그룹 단위**다.
_TARGETS_LIVE = [
    {"nccTargetId": "tgt-1", "targetTp": "MEDIA_TARGET", "delFlag": False,
     "target": {"type": 2, "search": ["naver"]}},
    {"nccTargetId": "tgt-2", "targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": False,
     "target": [{"keyword": "아이패드블루", "type": 1, "date": 1745346227}]},
    {"nccTargetId": "tgt-3", "targetTp": "NON_SEARCH_KEYWORD_TARGET", "delFlag": False,
     "target": {"excluded": False}},
]


def test_shopping_exclusions_normalize_into_restricted_keyword_shape():
    """쇼핑 제외는 **하류가 이미 읽는 모양**으로 나와야 한다.

    새 모양을 돌려주면 소비자(생존감시 `_classify`·자동발견 편입)마다 분기가 생기고, 그 분기가
    갈라지는 것이 이 리포가 반복해 당한 형태다([[same-defect-three-times-fix-the-shape]]).
    """
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, _TARGETS_LIVE)) as g:
        rows = writer.get_shopping_exclusions(ADGROUP_ID)

    g.assert_called_once_with("/ncc/targets", {"ownerId": ADGROUP_ID})
    assert len(rows) == 1, "RESTRICT_KEYWORD_TARGET 외의 targetTp가 섞이면 안 된다"
    row = rows[0]
    assert row["keyword"] == "아이패드블루"
    assert row["type"] == 1
    assert row["delFlag"] is False, "하류가 delFlag를 반드시 보므로 «없음»이 아니라 명시 False다"
    assert row["nccTargetId"] == "tgt-2"


def test_shopping_exclusions_do_not_forge_a_keyword_id():
    """★★그룹 단위 id(`nccTargetId`)를 키워드 id인 척 넣으면 **나중에 엉뚱한 대상을 지운다.**

    `_classify`가 회수한 id는 `restrict_kwd_id`에 저장되고, 그 칸의 유일한 실쓰기 소비자가
    개방(`delete_restricted_keywords`)이다. 이 테스트가 그 경로를 막는 유일한 가드다.
    """
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, _TARGETS_LIVE)):
        row = writer.get_shopping_exclusions(ADGROUP_ID)[0]

    assert row["nccAdgroupRestrictKwdId"] is None


def test_shopping_exclusion_date_becomes_utc_regtm():
    """`date`(epoch) → `regTm`(UTC ISO). 하류 `_parse_reg_tm`이 그대로 쓴다.

    ★epoch를 UTC로 읽는 근거는 라이브 대조다(2026-08-17 12:34:18 KST에 직접 등록한 항목의
      `date`를 UTC로 환산하면 같은 응답의 `editTm`과 초 단위까지 일치). ref 58 §13-5의
      「+1시간, 원인 미상」은 API가 아니라 콘솔 표기 쪽 오차다 — 여기서 보정하면 오히려 틀린다.
    """
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, _TARGETS_LIVE)):
        row = writer.get_shopping_exclusions(ADGROUP_ID)[0]

    assert row["regTm"] == "2025-04-22T18:23:47.000Z"


@pytest.mark.parametrize("bad", [None, 0, "", "어제", [1], 10**18])
def test_shopping_exclusion_bad_date_is_unknown_not_a_dropped_row(bad):
    """시각 한 칸 때문에 «제외가 있다»는 1급 사실을 버리지 않는다(`_parse_reg_tm`과 같은 처분)."""
    payload = [{"nccTargetId": "tgt-2", "targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": False,
                "target": [{"keyword": "골프", "type": 1, "date": bad}]}]
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, payload)):
        rows = writer.get_shopping_exclusions(ADGROUP_ID)

    assert len(rows) == 1 and rows[0]["keyword"] == "골프"
    assert rows[0]["regTm"] is None


def test_shopping_exclusions_skip_soft_deleted_target():
    """타겟 행 자체가 소프트 삭제면 그 안의 키워드는 효력이 없다."""
    payload = [{"nccTargetId": "tgt-2", "targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": True,
                "target": [{"keyword": "골프", "type": 1, "date": 1745346227}]}]
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, payload)):
        assert writer.get_shopping_exclusions(ADGROUP_ID) == []


@pytest.mark.parametrize("empty_repr,label", [
    (None, "미설정 — 한 번도 편집 안 된 그룹(regTm==editTm)"),
    ([], "비움 — 설정했다가 목록을 비운 그룹"),
])
def test_shopping_exclusions_treat_both_empty_representations_as_zero(empty_repr, label):
    """★`None`과 `[]`는 **표현만 다른 0건**이다 — 어느 쪽도 에러가 아니다.

    2026-08-17 라이브 대조군으로 확정: `None`인 3그룹(Z플립8·Z폴드8 와이드/울트라)은
    `regTm == editTm`이라 **한 번도 편집된 적이 없고**, `[]`인 `S26`은 우리가 PUT으로 비워
    `editTm`이 갱신돼 있다.

    ★이 테스트가 막는 것: 초판은 `None`을 `raise`로 받아 그 3그룹을 **매 스윕 영구 에러**로
      만들었다(배포 후 라이브 detect 1회전에 errors 3건이 전부 이것). 지금은 그 그룹에 제외가
      없어 손실이 없지만, 거기 제외가 생기는 순간 **영영 못 본다.**
      0건을 에러로 세는 것도 모름을 0건으로 세는 것만큼 나쁘다 — 방향만 반대다.
    """
    payload = [{"nccTargetId": "tgt-2", "targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": False,
                "target": empty_repr}]
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, payload)):
        assert writer.get_shopping_exclusions(ADGROUP_ID) == [], label


def test_shopping_exclusions_raise_when_shape_changes():
    """★스키마가 바뀌면 «0건»이라 말하지 않고 **예외로 올린다**(교훈 #123: 모름≠0건).

    조용히 []를 돌려주면 3,880건이 하루아침에 「제외 없음」이 되고, 생존감시는 전부 missing으로,
    자동발견은 「찾을 게 없음」으로 뒤집힌다 — 둘 다 침묵으로 지나간다.
    """
    payload = [{"nccTargetId": "tgt-2", "targetTp": "RESTRICT_KEYWORD_TARGET", "delFlag": False,
                "target": {"keyword": "골프"}}]
    with patch.object(writer.fetcher, "_get", return_value=FakeResp(200, payload)):
        with pytest.raises(WriteError):
            writer.get_shopping_exclusions(ADGROUP_ID)


def test_get_live_exclusions_picks_source_by_adgroup_type():
    """유형이 소스를 고른다 — 이 분기가 한 곳에만 있어야 소비자마다 갈라지지 않는다."""
    with patch.object(writer, "get_restricted_keywords", return_value=[{"keyword": "웹"}]) as rk, \
         patch.object(writer, "get_shopping_exclusions", return_value=[{"keyword": "쇼핑"}]) as sh:
        assert writer.get_live_exclusions(ADGROUP_ID, "WEB_SITE") == [{"keyword": "웹"}]
        assert writer.get_live_exclusions(ADGROUP_ID, "SHOPPING") == [{"keyword": "쇼핑"}]
        rk.assert_called_once_with(ADGROUP_ID)
        sh.assert_called_once_with(ADGROUP_ID)


@pytest.mark.parametrize("unknown_type", [None, "BRAND_SEARCH", "PLACE", "", "web_site"])
def test_get_live_exclusions_returns_none_for_unreadable_types(unknown_type):
    """★«읽을 수 없다»는 None이다 — **빈 리스트가 아니다.**

    []로 뭉개면 「제외가 0건」과 구별되지 않고, 그 혼동이 D-NAO-174에서 P1을 맞은 결함이다.
    `"web_site"`(소문자)가 여기 있는 이유: 유형 문자열이 조금만 어긋나도 **조용히 0건**이 되는
    것이 아니라 «못 읽음»으로 떨어져야 한다.
    """
    with patch.object(writer.fetcher, "_get",
                      side_effect=AssertionError("읽을 소스를 모르면 호출조차 하지 않는다")):
        assert writer.get_live_exclusions(ADGROUP_ID, unknown_type) is None


# ═══ D-NAO-181 ③ — 쇼핑 제외 **쓰기** (`PUT /ncc/targets/{targetId}`) ═══
#
# ★이 경로의 고유 위험은 「추가 실패」가 아니라 **「기존 유실」**이다. 교체(replace) 의미론이라
#   요청 body가 곧 최종 상태이고, 대상 계정엔 대행사가 2024년부터 쌓은 3,880건이 있는데
#   **백업이 없다.** 아래 테스트는 전부 그 한 문장을 지킨다.

_EXISTING = [
    {"keyword": "기존A", "type": 1, "date": 1745346227},
    {"keyword": "기존B", "type": None, "date": 1700000000},
]


def _target_row(items=_EXISTING, edit_tm="2025-04-22T18:23:47.000Z"):
    return {"nccTargetId": "tgt-2", "ownerId": ADGROUP_ID,
            "targetTp": "RESTRICT_KEYWORD_TARGET", "target": items,
            "delFlag": False, "regTm": "2024-12-30T06:45:34.000Z", "editTm": edit_tm}


def _targets_resp(items=_EXISTING, edit_tm="2025-04-22T18:23:47.000Z"):
    return FakeResp(200, [
        {"nccTargetId": "tgt-1", "targetTp": "MEDIA_TARGET", "delFlag": False, "target": {}},
        _target_row(items, edit_tm),
    ])


class _FakePut:
    """PUT을 받아 기록하고 200을 돌려주는 스텁."""

    def __init__(self, status=200):
        self.status, self.calls = status, []

    def __call__(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.calls.append(json)
        return FakeResp(self.status, json)


def test_shopping_write_sends_the_whole_list_not_just_the_new_one():
    """★★부분 전송은 나머지를 지운다 — 이 경로에서 가장 비싼 실수다.

    PUT은 교체 의미론이므로 「신규 1건」만 보내면 기존 전건이 사라진다. 보낸 body를 직접 검사해
    **기존이 원문 그대로(=`date`까지) 실려 있는지**를 본다. `date`가 보존돼야 서버가 등록시각을
    유지한다(2026-08-17 라이브 실증).
    """
    put = _FakePut()
    after = _EXISTING + [{"keyword": "신규", "type": 1, "date": 1786937658}]
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),   # _get_adgroup
        _targets_resp(), _targets_resp(),             # before · 낙관적 잠금 재확인
        _targets_resp(after),                         # after 검증
    ]), patch.object(writer.requests, "put", put):
        result = writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])

    sent = put.calls[0]["target"]
    assert [k["keyword"] for k in sent] == ["기존A", "기존B", "신규"], "기존이 빠지면 그게 유실이다"
    assert sent[0] == _EXISTING[0], "기존 항목은 원문 그대로(date 포함) 보내야 한다"
    assert result.created_ids == [], "이 리소스엔 키워드 단위 id가 없다 — 위조하면 개방이 엉뚱해진다"


def test_shopping_write_refuses_when_someone_edited_between_read_and_write():
    """★낙관적 잠금 — before 조회 뒤 남이 1건을 추가하면 우리 「전문」은 옛것이고, 그대로 쓰면
    **그 조치를 지운다.** `editTm`이 바뀌면 쓰지 않고 거부한다."""
    put = _FakePut()
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),
        _targets_resp(),                                        # before
        _targets_resp(edit_tm="2026-08-17T03:34:18.000Z"),      # 그 사이 남이 씀
    ]), patch.object(writer.requests, "put", put):
        with pytest.raises(writer.WriteConcurrentEditError):
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])

    assert put.calls == [], "경합을 감지했으면 **쓰지 않아야** 한다"


def test_shopping_write_fails_closed_when_existing_keywords_vanish():
    """★★보존 검증 — 「추가됐나」만 보면 유실을 못 본다.

    after에 신규는 있는데 기존이 사라진 상황(서버가 교체를 다르게 처리했거나 경합)에서
    **성공으로 돌려주면 3,880건이 조용히 날아간 채 초록이 된다.** 사라진 키워드 전문을 메시지에
    실어야 사람이 복구할 수 있다.
    """
    put = _FakePut()
    after = [{"keyword": "신규", "type": 1, "date": 1786937658}]  # 기존 2건 증발
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),
        _targets_resp(), _targets_resp(), _targets_resp(after),
    ]), patch.object(writer.requests, "put", put):
        with pytest.raises(WriteVerificationError) as e:
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])

    msg = str(e.value)
    assert "기존A" in msg and "기존B" in msg, "무엇이 사라졌는지 모르면 복구할 수 없다"


def test_shopping_write_fails_closed_when_new_keyword_not_reflected():
    """PUT이 2xx여도 재조회에 없으면 실패다(쓰기 응답이 아니라 재조회가 진실이다)."""
    put = _FakePut()
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),
        _targets_resp(), _targets_resp(), _targets_resp(),  # after에 신규 없음
    ]), patch.object(writer.requests, "put", put):
        with pytest.raises(WriteVerificationError):
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])


def test_shopping_write_refuses_non_shopping_adgroup():
    """파워링크에 이 경로를 쓰면 안 된다 — 리소스가 다르다(이중 방벽의 writer 쪽 관문)."""
    with patch.object(writer.fetcher, "_get",
                      return_value=FakeResp(200, {"adgroupType": "WEB_SITE"})):
        with pytest.raises(WriteValidationError):
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])


def test_shopping_write_refuses_duplicate_and_empty():
    """이미 있는 키워드 재등록·빈 입력·요청 내 중복은 진입 차단(서버 동작이 문서에 없다)."""
    with pytest.raises(WriteValidationError):
        writer.add_shopping_exclusions(ADGROUP_ID, [])
    with pytest.raises(WriteValidationError):
        writer.add_shopping_exclusions(ADGROUP_ID, ["같은것", "같은것"])
    # 중복 검사는 낙관적 잠금 **뒤**에 온다(기준이 recheck이므로) → GET 3회
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}), _targets_resp(), _targets_resp(),
    ]):
        with pytest.raises(WriteValidationError):
            writer.add_shopping_exclusions(ADGROUP_ID, ["기존A"])


@pytest.mark.parametrize("n_rows", [0, 2])
def test_shopping_write_refuses_when_target_row_is_not_unique(n_rows):
    """쓸 대상을 특정 못 하면 추측하지 않는다 — 0개면 쓸 곳이 없고 2개면 어디에 쓸지 모른다."""
    row = _target_row()
    payload = [row] * n_rows
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}), FakeResp(200, payload),
    ]):
        with pytest.raises(WriteValidationError):
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])


def test_add_exclusions_picks_write_path_by_live_adgroup_type():
    """유형이 경로를 고른다 — 읽기(`get_live_exclusions`)와 같은 모양."""
    with patch.object(writer, "_get_adgroup", return_value={"adgroupType": "WEB_SITE"}), \
         patch.object(writer, "add_restricted_keywords", return_value="웹") as rk:
        assert writer.add_exclusions(ADGROUP_ID, ["k"]) == "웹"
        rk.assert_called_once_with(ADGROUP_ID, ["k"])
    with patch.object(writer, "_get_adgroup", return_value={"adgroupType": "SHOPPING"}), \
         patch.object(writer, "add_shopping_exclusions", return_value="쇼핑") as sh:
        assert writer.add_exclusions(ADGROUP_ID, ["k"]) == "쇼핑"
        sh.assert_called_once_with(ADGROUP_ID, ["k"])


@pytest.mark.parametrize("unknown", [None, "BRAND_SEARCH", "PLACE", ""])
def test_add_exclusions_refuses_unknown_type_instead_of_skipping(unknown):
    """★쓰기에서 «모름»은 조용히 넘기면 안 된다 — 읽기의 `None`과 대칭이 아니다.

    읽기에서 모름은 관측이 한 칸 비는 것으로 끝나지만, 쓰기에서 조용히 넘기면 **조치가 실행되지
    않았는데 실행된 것처럼** 흘러간다(제안은 executed로, 원장엔 행이 서는데 네이버엔 없다).
    """
    with patch.object(writer, "_get_adgroup", return_value={"adgroupType": unknown}), \
         patch.object(writer.requests, "put",
                      side_effect=AssertionError("모르면 쓰지 않는다")), \
         patch.object(writer, "add_restricted_keywords",
                      side_effect=AssertionError("모르면 쓰지 않는다")):
        with pytest.raises(WriteValidationError):
            writer.add_exclusions(ADGROUP_ID, ["k"])


def test_shopping_write_uses_the_recheck_snapshot_not_the_stale_one():
    """★★적대 리뷰 1R P1-1 회귀 — 낙관적 잠금이 «검사만 하고 결과는 안 쓰는» 반쪽이면 안 된다.

    초판은 `recheck`로 최신 목록을 **받아 놓고 버린 채** `before`로 본문을 만들었다. `editTm`은
    **초 단위 해상도**라 before~recheck 사이의 편집이 같은 초에 나면 문자열이 같아 잠금을
    통과하는데, 그때 옛 스냅샷으로 교체 PUT을 쏘면 **남이 방금 건 조치가 지워진다.**
    ★그리고 보존 검증도 못 잡는다 — `before`에 없던 키워드라 「유실」로 안 보인다.
    즉 예외 없이 성공으로 끝난다(fail-silent, 최악의 모양).

    여기서는 `editTm`을 **같게 유지**해 잠금을 일부러 통과시킨다. 그래야 「잠금이 못 잡는 창」
    자체를 겨눌 수 있다.
    """
    put = _FakePut()
    same_edit_tm = "2025-04-22T18:23:47.000Z"
    concurrent = _EXISTING + [{"keyword": "남이방금건것", "type": 2, "date": 1786900000}]
    after = concurrent + [{"keyword": "신규", "type": 1, "date": 1786937658}]
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),
        _targets_resp(_EXISTING, same_edit_tm),      # before — 아직 2건
        _targets_resp(concurrent, same_edit_tm),     # recheck — 3건인데 editTm은 같은 초
        _targets_resp(after),                        # after
    ]), patch.object(writer.requests, "put", put):
        writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])

    sent = [k["keyword"] for k in put.calls[0]["target"]]
    assert "남이방금건것" in sent, (
        "★잠금이 못 잡는 창에서도 남의 조치가 보존돼야 한다 — recheck로 본문을 만들어야 한다"
    )
    assert sent == ["기존A", "기존B", "남이방금건것", "신규"]


def test_shopping_write_preservation_check_is_measured_against_the_freshest_list():
    """보존 검증의 기준도 `recheck`다 — `before`로 재면 그 창의 항목 유실을 못 잡는다."""
    put = _FakePut()
    same = "2025-04-22T18:23:47.000Z"
    concurrent = _EXISTING + [{"keyword": "남이방금건것", "type": 2, "date": 1786900000}]
    # 서버가 그 항목을 떨어뜨린 채 돌려준 상황 — before 기준이면 눈치채지 못한다.
    after = _EXISTING + [{"keyword": "신규", "type": 1, "date": 1786937658}]
    with patch.object(writer.fetcher, "_get", side_effect=[
        FakeResp(200, {"adgroupType": "SHOPPING"}),
        _targets_resp(_EXISTING, same), _targets_resp(concurrent, same), _targets_resp(after),
    ]), patch.object(writer.requests, "put", put):
        with pytest.raises(WriteVerificationError) as e:
            writer.add_shopping_exclusions(ADGROUP_ID, ["신규"])
    assert "남이방금건것" in str(e.value)


def test_shopping_items_raise_when_shape_is_neither_list_nor_none():
    """적대 리뷰 P2-1(채택) — 이 방어 분기에 테스트가 0건이었다(변이 SURVIVED).

    이 함수의 존재 이유가 「모름을 0건으로 세지 않는다」인데, 그걸 지키는 테스트가 없으면
    다음 사람이 조용히 `return []`로 바꿔도 아무도 모른다.
    """
    for bad in ({"keyword": "골프"}, "골프", 123):
        with pytest.raises(WriteError):
            writer._shopping_items({"target": bad})
