# test_naver_sa_ad_fetcher_device_bid_weight.py — D-NAO-218(M2-b2)
# get_adgroups가 /ncc/adgroups 응답의 pcNetworkBidWeight·mobileNetworkBidWeight를 뽑아내는지
# (계약 스펙 ①「이번은 이 2필드만」) — 추가 API 콜 0(기존 endpoint 재사용)의 파싱 단.
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import naver_sa_ad_fetcher as fetcher


def _resp(payload: list[dict]) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def test_get_adgroups_extracts_device_bid_weight():
    """정상 케이스: pcNetworkBidWeight=70·mobileNetworkBidWeight=80 그대로 뽑힌다."""
    payload = [{
        "nccAdgroupId": "grp-1", "nccCampaignId": "cmp-1", "name": "그룹1",
        "status": "ELIGIBLE", "userLock": False, "bidAmt": 500,
        "pcNetworkBidWeight": 70, "mobileNetworkBidWeight": 80,
    }]
    with patch.object(fetcher, "_get", return_value=_resp(payload)) as mock_get:
        out = fetcher.get_adgroups("cmp-1")
    mock_get.assert_called_once_with("/ncc/adgroups", {"nccCampaignId": "cmp-1"})  # 추가 콜 0 — 같은 1콜
    assert out[0]["pc_bid_weight"] == 70
    assert out[0]["mobile_bid_weight"] == 80


def test_get_adgroups_device_bid_weight_above_100_not_truncated():
    """★스펙 ④: 100 초과값(range 10~500, 공식 확정)도 잘리지 않고 그대로 온다 — 실재
    사례(AG5054=130, D-NAO-216 §8-Q2 각주)."""
    payload = [{
        "nccAdgroupId": "grp-2", "nccCampaignId": "cmp-1", "name": "그룹2",
        "status": "ELIGIBLE", "userLock": False, "bidAmt": 500,
        "pcNetworkBidWeight": 130, "mobileNetworkBidWeight": 100,
    }]
    with patch.object(fetcher, "_get", return_value=_resp(payload)):
        out = fetcher.get_adgroups("cmp-1")
    assert out[0]["pc_bid_weight"] == 130
    assert out[0]["mobile_bid_weight"] == 100


def test_get_adgroups_device_bid_weight_missing_key_is_none():
    """②NULL/100 구분의 전제: 키 자체가 없으면(구버전 응답·부분 실패) None — 100을 지어내지
    않는다(호출부가 NULL/100을 구분해 로깅할 근거)."""
    payload = [{
        "nccAdgroupId": "grp-3", "nccCampaignId": "cmp-1", "name": "그룹3",
        "status": "ELIGIBLE", "userLock": False, "bidAmt": 500,
    }]
    with patch.object(fetcher, "_get", return_value=_resp(payload)):
        out = fetcher.get_adgroups("cmp-1")
    assert out[0]["pc_bid_weight"] is None
    assert out[0]["mobile_bid_weight"] is None


def test_get_adgroups_existing_fields_unchanged():
    """④회귀 0: 기존 11키는 이번 변경으로 손대지 않는다."""
    payload = [{
        "nccAdgroupId": "grp-1", "nccCampaignId": "cmp-1", "name": "그룹1",
        "status": "ELIGIBLE", "statusReason": "", "userLock": False, "bidAmt": 500,
        "dailyBudget": 10000, "useExpSearch": True, "editTm": "2026-08-01T00:00:00.000",
        "regTm": "2026-01-01T00:00:00.000", "pcNetworkBidWeight": 100, "mobileNetworkBidWeight": 100,
    }]
    with patch.object(fetcher, "_get", return_value=_resp(payload)):
        out = fetcher.get_adgroups("cmp-1")
    row = out[0]
    assert row["adgroup_id"] == "grp-1"
    assert row["campaign_id"] == "cmp-1"
    assert row["bid_amt"] == 500
    assert row["daily_budget"] == 10000
    assert row["extended_search"] is True
    assert row["edit_tm"] == "2026-08-01T00:00:00.000"
    assert row["reg_tm"] == "2026-01-01T00:00:00.000"
