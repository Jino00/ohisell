# cs.py — 쿠팡 CS(고객문의) 도메인 SA (6개). 읽기 3개 구현, 쓰기 3개 stub(쓰기 페이즈).
# 명세: docs/references/10_coupang_cs_api_specs.md (전수 디테일 2026-06-03, D-15).
# 용도: 미답변 문의 현황 조회(운영 보조). 조회 기간 최대 7일 — openapi 게이트웨이 전용.
# DB 적재: CoupangInquiry 경량 테이블 (미답변 현황 지표용).
# 호출은 서버 IP에서만(로컬 403, 트랙 D-8).
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from urllib.parse import quote

from app.clients.coupang._base import (
    CoupangBaseClient,
    CoupangReadError,
    CoupangWriteError,
    check_write_response,
)

log = logging.getLogger(__name__)

_OPENAPI_V5 = "/v2/providers/openapi/apis/api/v5/vendors"
_OPENAPI_V4 = "/v2/providers/openapi/apis/api/v4/vendors"
_OPENAPI_V5_CC = "/v2/providers/openapi/apis/api/v5/vendors"

_MAX_DAYS = 7  # CS API 최대 조회 기간


# ── 쓰기 경로 빌더 (v4 — 단일 정의, SA 실행 + Harness dry-run 미리보기 공유, 트랙 D-16/W2) ──
# ⚠️ 읽기는 v5, 쓰기는 v4. inquiry_id는 path segment quote(codex[P2] W1 패턴).
def online_reply_path(vendor_id: str, inquiry_id: str) -> str:
    """#2 상품별 고객문의 답변 경로."""
    return f"{_OPENAPI_V4}/{vendor_id}/onlineInquiries/{quote(str(inquiry_id), safe='')}/replies"


def cc_reply_path(vendor_id: str, inquiry_id: str) -> str:
    """#4 쿠팡 고객센터 문의답변 경로."""
    return f"{_OPENAPI_V4}/{vendor_id}/callCenterInquiries/{quote(str(inquiry_id), safe='')}/replies"


def cc_confirm_path(vendor_id: str, inquiry_id: str) -> str:
    """#5 쿠팡 고객센터 문의확인 경로."""
    return f"{_OPENAPI_V4}/{vendor_id}/callCenterInquiries/{quote(str(inquiry_id), safe='')}/confirms"


def _fmt_dt(dt: datetime) -> str:
    """yyyy-MM-dd'T'HH:mm:ss 형식으로 변환 (CS API 요구 형식)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def coerce_answer_id(value: int | str) -> int:
    """parentAnswerId를 Number로 정규화 (명세 10 §4, codex[P2] W2).

    공식 스키마가 Number이므로 int로 변환한다. 비숫자면 CoupangWriteError.
    Harness(dry-run preview)와 SA(live)가 동일 값을 쓰도록 공유한다.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise CoupangWriteError(f"parent_answer_id는 숫자(Number)여야 함: {value!r}")


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
    # 쓰기 구현 (트랙 D-16, W2) — ⚠️ 라이브 실행자(live executor).
    # ⚠️ 직접 호출 금지. 반드시 Harness(services/coupang/cs_ops.py)를 경유할 것.
    #    아래 메서드는 dry_run/confirm을 모른다(원칙18-1) — 호출 즉시 라이브(실제 고객에게 답변 전송).
    #    dry_run 게이트·confirm 토큰은 Harness(_write_guard)가 담당(트랙 §5).
    # 본문 스키마는 2026-06-04 /browse 재수집(명세 10 §2·4·5, 추정금지 D-1). 실패=CoupangWriteError(원칙22).
    # ════════════════════════════════════════════════

    @staticmethod
    def _require(value: str, field: str) -> str:
        """필수 문자열 필드 빈값 방어(명백한 실수 차단). 길이 등 상세는 쿠팡이 검증."""
        v = (value or "").strip()
        if not v:
            raise CoupangWriteError(f"CS 쓰기 필수 필드 누락: {field}")
        return v

    def reply_online_inquiry(self, *, inquiry_id: str, content: str, reply_by: str) -> dict:
        """#2 상품별 고객문의 답변 (POST v4). 명세 10 §2.

        Args:
            inquiry_id: 답변할 문의 ID(#1 조회로 확인).
            content: 답변 내용(줄넘김 \\n). 길이 제한은 쿠팡이 검증.
            reply_by: 응답자 셀러포탈(WING) 아이디 — 필수.
        Raises:
            CoupangWriteError: 필수 누락 또는 답변 실패.
        """
        self._require(content, "content")
        self._require(reply_by, "reply_by")
        body = {"content": content, "vendorId": self.vendor_id, "replyBy": reply_by}
        resp = self._request(
            "POST", online_reply_path(self.vendor_id, inquiry_id), body=body, retry_transient=False
        )
        return check_write_response(resp, f"상품 고객문의 답변(inquiry={inquiry_id})")

    def reply_call_center_inquiry(
        self, *, inquiry_id: str, content: str, reply_by: str, parent_answer_id: int | str
    ) -> dict:
        """#4 쿠팡 고객센터 문의 답변 (POST v4). 명세 10 §4.

        ⚠️ 미답변(progress/requestAnswer) 상태만 가능. 24시간 미답변 시 자동처리→API 답변 불가.
        Args:
            inquiry_id: 상담번호(#3 조회로 확인).
            content: 답변 내용(최소2~최대1000자, 줄넘김 \\n). 길이는 쿠팡이 검증.
            reply_by: 실사용자ID(WING ID) — 필수.
            parent_answer_id: 부모이관글 answerId(#3 조회의 answerId) — 필수.
        Raises:
            CoupangWriteError: 필수 누락 또는 답변 실패.
        """
        self._require(content, "content")
        self._require(reply_by, "reply_by")
        body = {
            "vendorId": self.vendor_id,
            "inquiryId": str(inquiry_id),
            "content": content,
            "replyBy": reply_by,
            "parentAnswerId": coerce_answer_id(parent_answer_id),
        }
        resp = self._request(
            "POST", cc_reply_path(self.vendor_id, inquiry_id), body=body, retry_transient=False
        )
        return check_write_response(resp, f"CS이관 문의 답변(inquiry={inquiry_id})")

    def confirm_call_center_inquiry(self, *, inquiry_id: str, confirm_by: str) -> dict:
        """#5 쿠팡 고객센터 문의 확인 (POST v4). 명세 10 §5.

        ⚠️ 미확인(TRANSFER) 건 확인 처리. 24시간 경과 시 불가.
        Args:
            inquiry_id: 상담번호(#3 조회의 미확인 건).
            confirm_by: 실사용자ID(WING ID) — 필수.
        Raises:
            CoupangWriteError: 필수 누락 또는 확인 실패.
        """
        self._require(confirm_by, "confirm_by")
        body = {"confirmBy": confirm_by}
        resp = self._request(
            "POST", cc_confirm_path(self.vendor_id, inquiry_id), body=body, retry_transient=False
        )
        return check_write_response(resp, f"CS이관 문의 확인(inquiry={inquiry_id})")
