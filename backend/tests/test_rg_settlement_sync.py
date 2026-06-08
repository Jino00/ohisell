# test_rg_settlement_sync.py — RG 정산 수수료 파싱 fixture 테스트 (D-12)
# D-12: 머니코드 예외 — 파싱·부호·집계·dedup 라이브 self-verify 대신 committed fixture 테스트.
# 라이브 API 호출 없음. 응답 fixture는 S0 실측(2026-06-09) 기반.
import pytest
from decimal import Decimal
from datetime import date

from app.services.coupang.rg_settlement_sync import (
    _parse_status_response,
    _parse_date,
    _dec,
)
from app.clients.coupang.inbound import WingReadError
from app.clients.coupang.rg_settlement import CoupangWingRgSettlementClient


# ─── 헬퍼 테스트 ───────────────────────────────────────

def test_dec_normal():
    assert _dec(1234.56) == Decimal("1234.56")

def test_dec_none():
    assert _dec(None) == Decimal(0)

def test_dec_invalid():
    assert _dec("not_a_number") == Decimal(0)

def test_parse_date_utc_iso():
    d = _parse_date("2026-04-05T15:00:00Z")
    assert d == date(2026, 4, 6)  # UTC T15:00 = KST 다음날 00:00

def test_parse_date_none():
    assert _parse_date(None) is None

def test_parse_date_invalid():
    assert _parse_date("not-a-date") is None


# ─── KST→UTC 변환 테스트 ──────────────────────────────

def test_kst_date_to_utc_iso():
    result = CoupangWingRgSettlementClient._kst_date_to_utc_iso("2026-06-01")
    assert result == "2026-06-01T15:00:00.000Z"


# ─── 응답 파싱 테스트 ─────────────────────────────────

# S0 실측 fixture (2026-06-09 캡처)
_FIXTURE_SINGLE = {
    "settlementStatusReports": [
        {
            "settlementCycle": "WEEKLY",
            "settlementRatio": 70,
            "finalSettlementAmount": 0.00,
            "settlementGroupKey": "A01564720-2026-04-06-2026-04-12",
            "settlementDate": "2026-05-11T15:00:00Z",
            "settlementPeriodStartDate": "2026-04-05T15:00:00Z",  # → KST 2026-04-06
            "settlementPeriodEndDate": "2026-04-11T15:00:00Z",    # → KST 2026-04-12
            "settlementStatusReportDetail": {
                "totalTakeRateAmountWithVat": 2214,
                "totalFulfillmentFeeDeductionAmount": 2090,
                "totalStorageFeeDeductionAmount": 210,
                "totalWarehousingFeeDeductionAmount": 1210,
                "totalCreturnReverseShippingFeeDeductionAmount": 0,
                "totalVreturnHandlingFeeDeductionAmount": 0,
                "totalAdSalesDeductionAmount": 16510,
            }
        }
    ]
}


def test_parse_single_report():
    rows = _parse_status_response(_FIXTURE_SINGLE, "COUPANG_WING1")
    assert len(rows) == 7  # _FEE_FIELD_MAP 항목 수

    fee_map = {r.fee_type: r.amount for r in rows}
    assert fee_map["sale_fee"] == Decimal("2214")
    assert fee_map["fulfillment"] == Decimal("2090")
    assert fee_map["storage"] == Decimal("210")
    assert fee_map["warehousing"] == Decimal("1210")
    assert fee_map["ad_sales"] == Decimal("16510")

    # 날짜 확인 (UTC T15:00 → KST 다음날)
    assert rows[0].recognition_date_from == date(2026, 4, 6)
    assert rows[0].recognition_date_to == date(2026, 4, 12)
    assert rows[0].account_key == "COUPANG_WING1"


def test_parse_empty_reports():
    rows = _parse_status_response({"settlementStatusReports": []}, "COUPANG_WING1")
    assert rows == []


def test_parse_missing_key_raises():
    with pytest.raises(WingReadError, match="스키마 드리프트"):
        _parse_status_response({"unexpected_key": []}, "COUPANG_WING1")


def test_parse_non_list_raises():
    with pytest.raises(WingReadError):
        _parse_status_response({"settlementStatusReports": "not_a_list"}, "COUPANG_WING1")


# ─── 부호 테스트 (취소/환급 음수) ──────────────────────

_FIXTURE_NEGATIVE = {
    "settlementStatusReports": [
        {
            "settlementPeriodStartDate": "2026-05-03T15:00:00Z",
            "settlementPeriodEndDate": "2026-05-09T15:00:00Z",
            "settlementStatusReportDetail": {
                "totalTakeRateAmountWithVat": -500,  # 환급(음수)
                "totalFulfillmentFeeDeductionAmount": 1000,
                "totalStorageFeeDeductionAmount": 0,
                "totalWarehousingFeeDeductionAmount": 0,
                "totalCreturnReverseShippingFeeDeductionAmount": 0,
                "totalVreturnHandlingFeeDeductionAmount": 0,
                "totalAdSalesDeductionAmount": 0,
            }
        }
    ]
}


def test_negative_amounts_preserved():
    rows = _parse_status_response(_FIXTURE_NEGATIVE, "COUPANG_WING1")
    fee_map = {r.fee_type: r.amount for r in rows}
    assert fee_map["sale_fee"] == Decimal("-500")  # 음수 그대로 저장
    assert fee_map["fulfillment"] == Decimal("1000")


# ─── dedup 테스트 (70%+30% 분할정산 합산) ───────────────

_FIXTURE_SPLIT = {
    "settlementStatusReports": [
        {
            "settlementPeriodStartDate": "2026-04-05T15:00:00Z",
            "settlementPeriodEndDate": "2026-04-11T15:00:00Z",
            "settlementStatusReportDetail": {
                "totalTakeRateAmountWithVat": 1000,
                "totalFulfillmentFeeDeductionAmount": 500,
                "totalStorageFeeDeductionAmount": 0,
                "totalWarehousingFeeDeductionAmount": 0,
                "totalCreturnReverseShippingFeeDeductionAmount": 0,
                "totalVreturnHandlingFeeDeductionAmount": 0,
                "totalAdSalesDeductionAmount": 0,
            }
        },
        {
            "settlementPeriodStartDate": "2026-04-05T15:00:00Z",  # 같은 기간, 30% 분
            "settlementPeriodEndDate": "2026-04-11T15:00:00Z",
            "settlementStatusReportDetail": {
                "totalTakeRateAmountWithVat": 428,
                "totalFulfillmentFeeDeductionAmount": 214,
                "totalStorageFeeDeductionAmount": 0,
                "totalWarehousingFeeDeductionAmount": 0,
                "totalCreturnReverseShippingFeeDeductionAmount": 0,
                "totalVreturnHandlingFeeDeductionAmount": 0,
                "totalAdSalesDeductionAmount": 0,
            }
        }
    ]
}


def test_split_settlement_aggregated():
    rows = _parse_status_response(_FIXTURE_SPLIT, "COUPANG_WING1")
    fee_map = {r.fee_type: r.amount for r in rows}
    # 70% + 30% 합산
    assert fee_map["sale_fee"] == Decimal("1428")
    assert fee_map["fulfillment"] == Decimal("714")


# ─── 누락 필드 방어 테스트 (D-13) ──────────────────────

_FIXTURE_MISSING_FIELDS = {
    "settlementStatusReports": [
        {
            "settlementPeriodStartDate": "2026-05-03T15:00:00Z",
            "settlementPeriodEndDate": "2026-05-09T15:00:00Z",
            "settlementStatusReportDetail": {
                "totalTakeRateAmountWithVat": 1000,
                # 나머지 필드 누락 (스키마 변동 시뮬레이션)
            }
        }
    ]
}


def test_missing_fields_defaults_to_zero():
    rows = _parse_status_response(_FIXTURE_MISSING_FIELDS, "COUPANG_WING1")
    fee_map = {r.fee_type: r.amount for r in rows}
    assert fee_map["sale_fee"] == Decimal("1000")
    assert fee_map["fulfillment"] == Decimal("0")   # 누락 → 0
    assert fee_map["storage"] == Decimal("0")
