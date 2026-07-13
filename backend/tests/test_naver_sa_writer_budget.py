# test_naver_sa_writer_budget.py — P1 update_campaign_budget(캠페인 일예산 dailyBudget PUT)
# 단위테스트. 근거: docs/PLAN_naver-ad-budget-control.md §5-B(swagger+라이브 04 확정,
# 2026-07-13). HTTP는 전부 mock(네트워크 호출 0) — test_naver_sa_writer.py와 동일 규율.
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
    """requests.Response 흉내 — status_code/json()/raise_for_status()/text만 필요
    (test_naver_sa_writer.py FakeResp과 동일 계약, 이 파일 단독 실행 가능하도록 복제)."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


CAMPAIGN_ID = "cmp-a001-02-000000008514959"  # 04 카나리 캠페인(HANDOFF 근거)


def _campaign_resp(daily_budget=30_000, use_daily_budget=True, shared_budget_id=None):
    return FakeResp(200, {
        "nccCampaignId": CAMPAIGN_ID,
        "customerId": 1313769,
        "userLock": False,
        "dailyBudget": daily_budget,
        "useDailyBudget": use_daily_budget,
        "sharedBudgetId": shared_budget_id,
    })


# ── update_campaign_budget: 성공 경로 ────────────────────────────────────


def test_update_budget_success_roundtrip():
    before = _campaign_resp(daily_budget=30_000)
    after = _campaign_resp(daily_budget=60_000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        result = writer.update_campaign_budget(CAMPAIGN_ID, 60_000)

    assert isinstance(result, WriteResult)
    assert result.action == "update_campaign_budget"
    assert result.before == before.json()
    assert result.after == after.json()
    assert result.response == put_resp.json()
    assert result.created_ids == []
    assert mock_get.call_count == 2
    assert mock_put.call_count == 1


def test_update_budget_body_shape_and_params():
    before = _campaign_resp(daily_budget=30_000)
    after = _campaign_resp(daily_budget=60_000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        writer.update_campaign_budget(CAMPAIGN_ID, 60_000)

    args, kwargs = mock_put.call_args
    assert args[0] == writer.fetcher.BASE_URL + f"/ncc/campaigns/{CAMPAIGN_ID}"
    assert kwargs["json"] == {
        "nccCampaignId": CAMPAIGN_ID,
        "customerId": int(writer.fetcher.CUSTOMER_ID),
        "useDailyBudget": True,
        "dailyBudget": 60_000,
    }
    assert kwargs["params"] == {"fields": "budget"}
    assert kwargs["timeout"] == 30


def test_update_budget_headers_signed_with_put_method_and_no_query_in_path():
    before = _campaign_resp(daily_budget=30_000)
    after = _campaign_resp(daily_budget=60_000)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.fetcher, "_headers", wraps=writer.fetcher._headers) as mock_headers, \
         patch.object(writer.requests, "put", return_value=put_resp):
        writer.update_campaign_budget(CAMPAIGN_ID, 60_000)

    args, kwargs = mock_headers.call_args
    assert args[0] == f"/ncc/campaigns/{CAMPAIGN_ID}"  # 쿼리스트링 제외, path만 서명
    assert kwargs.get("method") == "PUT" or (len(args) > 1 and args[1] == "PUT")


# ── 사전검증: daily_budget ────────────────────────────────────────────────


def test_update_budget_zero_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 0)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_budget_negative_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_campaign_budget(CAMPAIGN_ID, -10_000)

    mock_get.assert_not_called()
    mock_put.assert_not_called()


def test_update_budget_non_int_raises_validation_error_no_http():
    with patch.object(writer.fetcher, "_get") as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000.5)  # type: ignore[arg-type]

    mock_get.assert_not_called()
    mock_put.assert_not_called()


# ── 공유예산 fail-closed ──────────────────────────────────────────────────


def test_update_budget_shared_budget_id_present_raises_validation_error_no_put():
    before = _campaign_resp(daily_budget=30_000, shared_budget_id="sbg-000001")

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put") as mock_put:
        with pytest.raises(WriteValidationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000)

    assert mock_get.call_count == 1  # before만(공유예산 판정에 필요), PUT 호출 자체가 없음
    mock_put.assert_not_called()


# ── PUT 실패 ──────────────────────────────────────────────────────────────


def test_update_budget_put_4xx_raises_write_error_no_after_refetch():
    before = _campaign_resp(daily_budget=30_000)
    put_resp = FakeResp(403, {"message": "forbidden"})

    with patch.object(writer.fetcher, "_get", side_effect=[before]) as mock_get, \
         patch.object(writer.requests, "put", return_value=put_resp) as mock_put:
        with pytest.raises(WriteError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000)

    assert mock_get.call_count == 1  # before만, after 재조회 없음
    assert mock_put.call_count == 1


# ── 재조회 검증(fail-closed) ──────────────────────────────────────────────


def test_update_budget_daily_budget_mismatch_raises_verification_error():
    before = _campaign_resp(daily_budget=30_000)
    after = _campaign_resp(daily_budget=30_000)  # PUT은 성공했다는데 반영 안 됨
    put_resp = FakeResp(200, _campaign_resp(daily_budget=60_000).json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000)


def test_update_budget_use_daily_budget_not_true_raises_verification_error():
    """dailyBudget은 요청대로 재조회돼도 useDailyBudget이 true가 아니면 네이버가 값을
    무시한다(swagger 명시) — dailyBudget만 보고 성공 판정하면 '응답은 성공, 실효 반영은
    실패'를 놓친다. useGroupBidAmt 이중 확인(update_keyword_bid)과 동형. fail-closed."""
    before = _campaign_resp(daily_budget=30_000)
    after = _campaign_resp(daily_budget=60_000, use_daily_budget=False)
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000)


def test_update_budget_after_shared_budget_id_appears_raises_verification_error():
    """Fix 5(codex P2): before 재조회 시점엔 공유예산이 아니었지만(그래서 PUT까지 진행),
    PUT과 after 재조회 사이에 다른 행위자(콘솔의 사람·MOP)가 공유예산으로 전환했다면 —
    우리가 방금 쓴 per-campaign dailyBudget은 무효(swagger sharedDailyBudget 별도 경로).
    dailyBudget/useDailyBudget만 보고 성공 판정하면 이 상태 변화를 놓친다(fail-closed)."""
    before = _campaign_resp(daily_budget=30_000, shared_budget_id=None)
    after = _campaign_resp(daily_budget=60_000, use_daily_budget=True, shared_budget_id="sbg-000002")
    put_resp = FakeResp(200, after.json())

    with patch.object(writer.fetcher, "_get", side_effect=[before, after]), \
         patch.object(writer.requests, "put", return_value=put_resp):
        with pytest.raises(WriteVerificationError):
            writer.update_campaign_budget(CAMPAIGN_ID, 60_000)
