# test_cs_client_date_format.py — CS 리스트 API 날짜 형식 회귀 가드.
#
# 배경(원칙22): CoupangCsClient가 inquiryStartAt/inquiryEndAt을 _fmt_dt로 풀 datetime
# ("2026-06-08T21:05:00")으로 보내 onlineInquiries=400·callCenterInquiries=500 →
# CS 동기화(2계정×3읽기=6건) 전수 실패. 공식 스펙은 둘 다 yyyy-MM-dd(날짜만)
# (예: inquiryStartAt=2019-06-25 / 2018-01-07). 날짜만 보내도록 _fmt_date로 수정.
#
# 이 테스트는 _request를 가로채 실제 전송 파라미터가 'yyyy-MM-dd'(T 없음)인지 검증한다.
# 풀 datetime이 되살아나면 'T' 포함으로 즉시 실패.
import datetime
import re

from app.clients.coupang import cs as cs_mod

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _CaptureClient(cs_mod.CoupangCsClient):
    """_request를 가로채 전송 파라미터만 캡처(HTTP 미발생). 빈 1페이지로 즉시 종료."""

    def __init__(self):
        self.vendor_id = "A0000000"
        self.calls = []

    def _request(self, method, path, params=None, **kwargs):
        self.calls.append({"path": path, "params": dict(params or {})})
        return {"code": "200", "data": []}


def test_fmt_date_is_date_only():
    assert cs_mod._fmt_date(datetime.date(2026, 6, 20)) == "2026-06-20"


def test_online_inquiries_sends_date_only():
    c = _CaptureClient()
    list(c.iter_online_inquiries(days=7))
    p = c.calls[0]["params"]
    for key in ("inquiryStartAt", "inquiryEndAt"):
        assert _DATE_ONLY.match(p[key]), f"{key}={p[key]!r} (yyyy-MM-dd 아님)"
        assert "T" not in p[key], f"{key}에 시각(T) 포함 — 400 회귀"


def test_call_center_inquiries_sends_date_only():
    c = _CaptureClient()
    list(c.iter_call_center_inquiries(status="NO_ANSWER", days=7))
    p = c.calls[0]["params"]
    for key in ("inquiryStartAt", "inquiryEndAt"):
        assert _DATE_ONLY.match(p[key]), f"{key}={p[key]!r} (yyyy-MM-dd 아님)"
        assert "T" not in p[key], f"{key}에 시각(T) 포함 — 500 회귀"


def test_cc_query_statuses_are_valid():
    """CS이관 동기화가 쿠팡 유효 partnerCounselingStatus만 조회한다.
    라이브 실측(2026-06-20): NO_ANSWER/ANSWER/TRANSFER/NONE만 200, COMPLETE 등은 code=500.
    과거 'COMPLETE'(무효)가 17일간 CS 동기화 부분 실패를 유발 → 재발 봉인."""
    from app.services.coupang import cs_sync

    assert set(cs_sync._CC_QUERY_STATUSES) <= cs_sync._VALID_CC_STATUSES
    assert "COMPLETE" not in cs_sync._CC_QUERY_STATUSES


def test_window_span_capped_at_6_days():
    """조회 범위 end-start ≤ 6일. 라이브 실측: 7일차이는 onlineInquiries 400
    ("interval ... less than or equal to 7 days")·callCenterInquiries 500 → 6일이 한계.
    호출자가 days=7을 줘도 클라이언트가 6으로 cap해야 한다."""
    c = _CaptureClient()
    list(c.iter_online_inquiries(days=7))  # 의도적으로 7 요청
    p = c.calls[0]["params"]
    lo = datetime.date.fromisoformat(p["inquiryStartAt"])
    hi = datetime.date.fromisoformat(p["inquiryEndAt"])
    assert 0 < (hi - lo).days <= 6, f"span={(hi - lo).days}일 (쿠팡 한계 6일 초과 → 400/500)"
