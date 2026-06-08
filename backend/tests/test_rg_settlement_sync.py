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
    assert fee_map["delivery"] == Decimal("2090")   # 배송비(D-10: 풀필먼트 합계 아님)
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
    assert fee_map["delivery"] == Decimal("1000")


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
    assert fee_map["delivery"] == Decimal("714")


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
    assert fee_map["delivery"] == Decimal("0")   # 누락 → 0
    assert fee_map["storage"] == Decimal("0")


# ─── D-10 basis 잠금 테스트 (라이브 확정 2026-06-09) ──────

def test_search_date_type_default_is_SALES():
    """D-10: status/api 기본 searchDateType=SALES(매출인식일). 클라이언트 시그니처 잠금."""
    import inspect
    from app.clients.coupang.rg_settlement import _SEARCH_DATE_SALES
    sig = inspect.signature(CoupangWingRgSettlementClient.get_settlement_status)
    assert sig.parameters["search_date_type"].default == _SEARCH_DATE_SALES == "SALES"


# 라이브 실측 fixture (2026-06-09 WING1 04-06 리포트, 50필드 중 핵심 + 이월필드 포함)
_FIXTURE_LIVE_WITH_CARRYOVER = {
    "settlementStatusReports": [
        {
            "settlementPeriodStartDate": "2026-04-05T15:00:00Z",
            "settlementPeriodEndDate": "2026-04-11T15:00:00Z",
            "settlementStatusReportDetail": {
                # 발생비용(f) 컴포넌트 — 우리가 적재하는 7개
                "totalTakeRateAmountWithVat": 2214,
                "totalFulfillmentFeeDeductionAmount": 2090,   # 배송비
                "totalStorageFeeDeductionAmount": 210,
                "totalWarehousingFeeDeductionAmount": 1210,
                "totalCreturnReverseShippingFeeDeductionAmount": 0,
                "totalVreturnHandlingFeeDeductionAmount": 0,
                "totalAdSalesDeductionAmount": 16510,
                # 이월(g)/과거차감 — D-10: 발생 f에 섞이면 안 됨. 적재 제외 확인용.
                "totalCarryOverSettlementDeductionAmount": 114.0,
                "totalPastCfsDeductionAmount": -3396.0,
                "totalFinalSettlementAmount": 0.0,
            }
        }
    ]
}


def test_carryover_fields_excluded_from_accrual():
    """D-10: amount=발생비용(f). 이월(g)·과거차감·최종지급액 필드는 적재 안 함(7개 컴포넌트만)."""
    rows = _parse_status_response(_FIXTURE_LIVE_WITH_CARRYOVER, "COUPANG_WING1")
    assert len(rows) == 7  # 이월필드 3개는 무시
    fee_types = {r.fee_type for r in rows}
    # 이월/과거/최종 관련 fee_type 없음
    assert "carryover" not in fee_types
    assert all("final" not in ft and "past" not in ft for ft in fee_types)
    fee_map = {r.fee_type: r.amount for r in rows}
    assert fee_map["delivery"] == Decimal("2090")   # 발생 배송비(이월 미반영)


def test_fulfillment_components_distinct_no_double_count():
    """D-10 라이브 검증: 풀필먼트 J = 배송(delivery)+입출고(warehousing)+보관(storage), 세 값 독립.
    레퍼런스 17 §7(06-01~07): 배송 130,599 + 입출고 75,489 + 보관 168 = J 206,256."""
    detail = {
        "totalFulfillmentFeeDeductionAmount": 130599,  # 배송비
        "totalWarehousingFeeDeductionAmount": 75489,   # 입출고비
        "totalStorageFeeDeductionAmount": 168,         # 보관비
        "totalTakeRateAmountWithVat": 0,
        "totalCreturnReverseShippingFeeDeductionAmount": 0,
        "totalVreturnHandlingFeeDeductionAmount": 0,
        "totalAdSalesDeductionAmount": 0,
    }
    fixture = {"settlementStatusReports": [{
        "settlementPeriodStartDate": "2026-05-31T15:00:00Z",
        "settlementPeriodEndDate": "2026-06-06T15:00:00Z",
        "settlementStatusReportDetail": detail,
    }]}
    rows = _parse_status_response(fixture, "COUPANG_WING1")
    fee_map = {r.fee_type: r.amount for r in rows}
    j = fee_map["delivery"] + fee_map["warehousing"] + fee_map["storage"]
    assert j == Decimal("206256")  # 레퍼런스 §7 검산 일치


# ─── D-11 광고비 dedup 규칙 테스트 ────────────────────────

def test_rg_ad_dedup_pure_rule():
    """D-11: rg_ad_spend_to_exclude — 2P(RG)분만 합산, 3P/Retail 제외."""
    from app.services.coupang.intelligence import rg_ad_spend_to_exclude, RG_AD_SELL_TYPE
    assert RG_AD_SELL_TYPE == "2P"
    rows = [
        ("3P", Decimal("10000")),   # 윙 — 제외 안 함
        ("2P", Decimal("3000")),    # RG — 제외 대상
        ("2P", Decimal("500")),     # RG — 제외 대상
        ("Retail", Decimal("700")), # 로켓배송 — 제외 안 함
    ]
    assert rg_ad_spend_to_exclude(rows) == Decimal("3500")


def test_rg_ad_dedup_empty():
    """D-11: 2P 행이 없으면 0(현재 prod 상태 — 겹침 없음)."""
    from app.services.coupang.intelligence import rg_ad_spend_to_exclude
    assert rg_ad_spend_to_exclude([("3P", Decimal("585670"))]) == Decimal("0")


def test_rg_ad_dedup_normalizes_whitespace_and_none():
    """D-11 견고성(Codex 지적4a): 공백/None sell_type 방어."""
    from app.services.coupang.intelligence import rg_ad_spend_to_exclude
    rows = [(" 2P ", Decimal("100")), ("2P", Decimal("50")), (None, Decimal("999"))]
    assert rg_ad_spend_to_exclude(rows) == Decimal("150")


# ─── 대조 카드 reconcile guard 테스트 (Codex S5 지적1) ────

def test_rg_breakdown_reconciles_clean():
    """정상 entry: sale_fee + 풀필먼트 + return + ad_sales = total, other=0."""
    from app.services.coupang.intelligence import _rg_account_breakdown
    v = {
        "sale_fee": Decimal("2214"), "delivery": Decimal("2090"),
        "warehousing": Decimal("1210"), "storage": Decimal("210"),
        "return_shipping": Decimal("0"), "return_handling": Decimal("0"),
        "ad_sales": Decimal("16510"),
        "total": Decimal("22234"),  # 2214+2090+1210+210+16510
    }
    b = _rg_account_breakdown("COUPANG_WING1", v)
    assert b["fulfillment"] == Decimal("3510")  # 2090+1210+210
    assert b["other"] == Decimal("0")
    assert b["sale_fee"] + b["fulfillment"] + b["return_fee"] + b["ad_sales"] + b["other"] == b["total"]


def test_rg_breakdown_legacy_fulfillment_surfaces_in_other():
    """legacy 'fulfillment' fee_type(미매핑)이 total에 섞여도 'other'로 노출되어 reconcile 유지."""
    from app.services.coupang.intelligence import _rg_account_breakdown
    v = {
        "sale_fee": Decimal("1000"), "delivery": Decimal("500"),
        "fulfillment": Decimal("777"),  # ★legacy 키(미매핑) — total엔 포함
        "total": Decimal("2277"),       # 1000+500+777
    }
    b = _rg_account_breakdown("COUPANG_WING1", v)
    assert b["fulfillment"] == Decimal("500")  # delivery만(legacy 'fulfillment' 키는 미반영)
    assert b["other"] == Decimal("777")        # legacy 잔액 가시화(silent drop·중복 없음)
    # 라인합 + other == total
    assert b["sale_fee"] + b["fulfillment"] + b["return_fee"] + b["ad_sales"] + b["other"] == b["total"]
