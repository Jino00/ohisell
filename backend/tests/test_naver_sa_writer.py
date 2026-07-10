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

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]) as mock_get, \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        result = writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert isinstance(result, WriteResult)
    assert result.action == "add_restricted_keywords"
    assert result.before == []
    assert result.response == post_resp.json()
    assert result.after == after_resp.json()
    assert result.created_ids == ["rkw-1"]
    assert mock_get.call_count == 3
    assert mock_post.call_count == 1


def test_add_post_403_raises_write_error_and_no_after_refetch():
    before_resp = _restricted_kwds_resp([])
    post_resp = FakeResp(403, {"message": "signature invalid"})

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp]) as mock_get, \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        with pytest.raises(WriteError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert mock_post.call_count == 1
    assert mock_get.call_count == 2  # adgroup GET + before GET만, after 재조회 없음


def test_add_post_2xx_but_keyword_missing_in_after_raises_verification_error():
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([])  # 반영 안 됨
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp]), \
         patch.object(writer.requests, "post") as mock_post:
        with pytest.raises(WriteValidationError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    mock_post.assert_not_called()


def test_add_post_429_no_retry_single_call_raises_write_error():
    before_resp = _restricted_kwds_resp([])
    post_resp = FakeResp(429, {"message": "rate limited"})

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp]), \
         patch.object(writer.requests, "post", return_value=post_resp) as mock_post:
        with pytest.raises(WriteError):
            writer.add_restricted_keywords(ADGROUP_ID, ["테스트제외어"])

    assert mock_post.call_count == 1  # 재시도 없음(비멱등 쓰기)


def test_add_headers_signed_with_post_method():
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    post_resp = FakeResp(200, [{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
         patch.object(writer.requests, "post", return_value=post_resp):
        result = writer.add_restricted_keywords(ADGROUP_ID, ["제외어A", "제외어B"])

    assert sorted(result.created_ids) == ["rkw-1", "rkw-2"]


def test_add_after_row_missing_id_field_raises_verification_error():
    """[codex P1 방어] after 행에 nccAdgroupRestrictKwdId가 없으면(요청 keyword당 id 1개
    미확보) 완전성 검증 실패 — WriteVerificationError."""
    before_resp = _restricted_kwds_resp([])
    after_resp = _restricted_kwds_resp([{"keyword": "테스트제외어"}])  # id 필드 누락
    post_resp = FakeResp(200, [{"keyword": "테스트제외어"}])

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[WEB_SITE_ADGROUP, before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, after_resp]), \
         patch.object(writer.requests, "delete", return_value=delete_resp):
        with pytest.raises(WriteVerificationError):
            writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-1"])


def test_delete_headers_signed_with_delete_method_and_no_query_in_path():
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    after_resp = _restricted_kwds_resp([])
    delete_resp = FakeResp(204, None)

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp, after_resp]), \
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

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp]), \
         patch.object(writer.requests, "delete") as mock_delete:
        with pytest.raises(WriteValidationError):
            writer.delete_restricted_keywords(ADGROUP_ID, ["rkw-STALE"])

    mock_delete.assert_not_called()


def test_delete_429_no_retry_single_call_raises_write_error():
    """[codex P2] DELETE 429 → 재시도 없이 즉시 WriteError(POST 대칭 — 쓰기 무재시도 원칙)."""
    before_resp = _restricted_kwds_resp([{"nccAdgroupRestrictKwdId": "rkw-1", "keyword": "테스트제외어"}])
    delete_resp = FakeResp(429, {"message": "rate limited"})

    with patch.object(writer.fetcher, "_get", side_effect=[before_resp]), \
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
    with patch.object(writer.fetcher, "_get", return_value=_restricted_kwds_resp(rows)) as mock_get:
        out = writer.get_restricted_keywords(ADGROUP_ID)

    assert out == rows
    args, kwargs = mock_get.call_args
    assert args[0] == f"/ncc/adgroups/{ADGROUP_ID}/restricted-keywords"


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
