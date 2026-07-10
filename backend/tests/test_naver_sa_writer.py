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
