# cs.py — 쿠팡 CS(고객문의) 도메인 SA (6개). 읽기 3개 구현, 쓰기 3개 stub(쓰기 페이즈).
# 명세: docs/references/10_coupang_cs_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 미답변 문의 현황 조회(운영 보조). 조회 기간 최대 7일 — openapi 게이트웨이 전용.
# DB 적재: CoupangInquiry 경량 테이블 (미답변 현황 지표용).
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_OPENAPI_V5 = "/v2/providers/openapi/apis/api/v5/vendors"
_OPENAPI_V4 = "/v2/providers/openapi/apis/api/v4/vendors"
_OPENAPI_V5_CC = "/v2/providers/openapi/apis/api/v5/vendors"

_MAX_DAYS = 7  # CS API 최대 조회 기간


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd'T'HH:mm:ss 형식으로 변환 (CS API 요구 형식)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class CoupangCsClient(CoupangBaseClient):
    """쿠팡 CS(고객문의) API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    조합/저장은 Harness(services/coupang/cs_sync.py)가 담당.
    SA는 다른 SA를 모른다(원칙 18-6). 하드 실패는 CoupangReadError로 표면화(원칙22·codex P1).
    """

    # ════════════════════════════════════════════════
    # 읽기 구현
    # ════════════════════════════════════════════════

    def iter_online_inquiries(
        self,
        *,
        answered_type: str = "ALL",
        days: int = 7,
        page_size: int = 50,
    ) -> Iterator[dict]:
        """#1 상품별 고객문의 페이징 iterator. openapi v5.

        Args:
            answered_type: ALL / ANSWERED / NOANSWER.
            days: 조회 일수(최대 7일).
            page_size: 페이지당 건수.
        Yields:
            문의 dict.
        """
        days = min(days, _MAX_DAYS)
        now = datetime.now(timezone.utc)
        end = now
        start = now - timedelta(days=days)

        path = f"{_OPENAPI_V5}/{self.vendor_id}/onlineInquiries"
        page_num = 1
        while True:
            resp = self._request(
                "GET",
                path,
                params={
                    "vendorId": self.vendor_id,
                    "answeredType": answered_type,
                    "inquiryStartAt": _fmt_dt(start),
                    "inquiryEndAt": _fmt_dt(end),
                    "pageSize": page_size,
                    "pageNum": page_num,
                },
            )
            if resp is None:
                raise CoupangReadError(f"고객문의 조회 실패(페이지 {page_num})")
            # codex[P2]: code 필드로 API 레벨 오류 감지 — 빈 data를 정상 빈페이지로 착각 방지.
            resp_code = str(resp.get("code", ""))
            if resp_code and resp_code not in ("200", "SUCCESS"):
                raise CoupangReadError(
                    f"고객문의 API 오류(code={resp_code}): {resp.get('message')}"
                )
            data = resp.get("data") or []
            if isinstance(data, dict):
                items = data.get("content") or data.get("data") or []
            else:
                items = data
            if not items:
                break
            yield from items
            # 페이징 종료 조건
            if len(items) < page_size:
                break
            page_num += 1

    def iter_call_center_inquiries(
        self,
        *,
        status: str = "NO_ANSWER",
        days: int = 7,
        page_size: int = 50,
    ) -> Iterator[dict]:
        """#3 쿠팡 고객센터 문의 페이징 iterator (업체이관 건). openapi v5.

        Args:
            status: 문의 상태 (NO_ANSWER 등).
            days: 조회 일수(최대 7일).
        Yields:
            문의 dict.
        """
        days = min(days, _MAX_DAYS)
        now = datetime.now(timezone.utc)
        end = now
        start = now - timedelta(days=days)

        path = f"{_OPENAPI_V5_CC}/{self.vendor_id}/callCenterInquiries"
        page_num = 1
        while True:
            resp = self._request(
                "GET",
                path,
                params={
                    "vendorId": self.vendor_id,
                    "partnerCounselingStatus": status,
                    "inquiryStartAt": _fmt_dt(start),
                    "inquiryEndAt": _fmt_dt(end),
                    "pageSize": page_size,
                    "pageNum": page_num,
                },
            )
            if resp is None:
                raise CoupangReadError(f"CS 문의 조회 실패(페이지 {page_num})")
            resp_code = str(resp.get("code", ""))
            if resp_code and resp_code not in ("200", "SUCCESS"):
                raise CoupangReadError(
                    f"CS이관문의 API 오류(code={resp_code}): {resp.get('message')}"
                )
            data = resp.get("data") or []
            if isinstance(data, dict):
                items = data.get("content") or data.get("data") or []
            else:
                items = data
            if not items:
                break
            yield from items
            if len(items) < page_size:
                break
            page_num += 1

    def get_call_center_inquiry(self, *, inquiry_id: str) -> dict | None:
        """#6 쿠팡 고객센터 문의 단건 조회. ⚠️ 과도 조회 시 자동 차단.

        Returns:
            문의 dict 또는 None(실패).
        """
        path = f"/v2/providers/openapi/apis/api/v5/vendors/callCenterInquiries/{inquiry_id}"
        resp = self._request("GET", path)
        if resp is None:
            raise CoupangReadError(f"CS 문의 단건 조회 실패({inquiry_id})")
        return resp.get("data")

    # ════════════════════════════════════════════════
    # 쓰기 stub — 쓰기 페이즈에서 dry_run + 본문스키마 재확인(D-1)
    # ════════════════════════════════════════════════

    def reply_online_inquiry(self, *, inquiry_id: str, content: str) -> dict:
        """#2 상품별 고객문의 답변 — stub(쓰기 페이즈)."""
        raise NotImplementedError("쓰기 페이즈 — 고객문의 답변 미구현(D-1)")

    def reply_call_center_inquiry(self, *, inquiry_id: str, content: str) -> dict:
        """#4 쿠팡 고객센터 문의 답변 — stub(쓰기 페이즈).
        ⚠️ 24시간 미답변 시 쿠팡 자동처리→답변 불가.
        """
        raise NotImplementedError("쓰기 페이즈 — CS 문의 답변 미구현(D-1)")

    def confirm_call_center_inquiry(self, *, inquiry_id: str, confirm_by: str) -> dict:
        """#5 쿠팡 고객센터 문의 확인 — stub(쓰기 페이즈).
        ⚠️ 24시간 경과 시 불가. confirm_by=WING ID.
        """
        raise NotImplementedError("쓰기 페이즈 — CS 문의 확인 미구현(D-1)")
