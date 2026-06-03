# settlement.py — 쿠팡 정산 도메인 SA (2개): 매출내역(revenue-history)·지급내역(settlement-histories).
# 명세: docs/references/04_coupang_fees_map.md §6 (라이브 실응답 확정 2026-06-03).
# 트랙 D-8: 호출은 서버 IP에서만(로컬 403). 트랙 P4 정산 도메인.
from __future__ import annotations

import logging
from collections.abc import Iterator

from app.clients.coupang._base import CoupangBaseClient, CoupangReadError

log = logging.getLogger(__name__)

_REVENUE_PATH = "/v2/providers/openapi/apis/api/v1/revenue-history"
_SETTLEMENT_PATH = "/v2/providers/marketplace_openapi/apis/api/v1/settlement-histories"


class CoupangSettlementClient(CoupangBaseClient):
    """쿠팡 정산 API 단일책임 래퍼. 각 메서드 = 1 엔드포인트(SA). raw data 반환, None=실패.

    매출내역(revenue-history): 거래(주문) 단위 + items[] 옵션 중첩. 옵션별 실제 판매수수료율
      (serviceFeeRatio)이 items[] 안에 있음 — D-10/D-11 비교 축.
    지급내역(settlement-histories): 주/월 정산 단위 통장 지급액. ★JSON 배열 직접 반환(code 래핑 없음).
    조합/저장/수수료 감사는 Harness(services/coupang/settlement_sync.py)가 담당한다(원칙18-6).
    """

    # ────────────────────────────────────────────────
    # 매출내역 (옵션별 실수수료 — 회계축 D-10/D-11)
    # ────────────────────────────────────────────────
    def get_revenue_history(
        self,
        *,
        recognition_date_from: str,
        recognition_date_to: str,
        next_token: str = "",
        max_per_page: int = 50,
    ) -> dict | None:
        """매출내역 조회 (명세 §6-1). 거래 단위 + items[] 옵션 중첩.

        ★token 파라미터 필수(누락 시 400). recognitionDate=인식일 기준(saleDate 아님 — 한참 뒤).
        반환: {code, message, hasNext, nextToken, data:[{orderId, saleType, deliveryFee, items:[...]}]}
        또는 None(인증/네트워크/IP 실패).
        """
        params = {
            "vendorId": self.vendor_id,
            "recognitionDateFrom": recognition_date_from,
            "recognitionDateTo": recognition_date_to,
            "token": next_token,  # ★필수 — 빈 문자열이라도 반드시 포함
            "maxPerPage": max_per_page,
        }
        return self._request("GET", _REVENUE_PATH, params)

    def iter_revenue_history(
        self,
        *,
        recognition_date_from: str,
        recognition_date_to: str,
        max_per_page: int = 50,
    ) -> Iterator[dict]:
        """매출내역을 token 페이징으로 전부 순회 (제너레이터). 각 거래(주문) dict yield(items[] 포함).

        하드 실패(None=인증/네트워크, 비200=API에러)는 CoupangReadError로 표면화한다
        (정상 빈 페이지와 구분 — stale 위장 방지, 원칙22·codex P1 패턴). 인식일 윈도우 분할은 Harness 책임.
        """
        next_token = ""
        seen_tokens: set[str] = set()
        while True:
            resp = self.get_revenue_history(
                recognition_date_from=recognition_date_from,
                recognition_date_to=recognition_date_to,
                next_token=next_token,
                max_per_page=max_per_page,
            )
            if resp is None:
                raise CoupangReadError(f"매출내역 읽기 실패(None): {self.vendor_id}")
            if str(resp.get("code")) not in ("200", "SUCCESS"):
                raise CoupangReadError(
                    f"매출내역 API 에러 code={resp.get('code')} msg={resp.get('message')}"
                )
            for txn in resp.get("data", []) or []:
                yield txn
            next_token = resp.get("nextToken") or ""
            # hasNext=False거나 토큰 소진/반복이면 종료(빈 페이지 자연 종료, stale 위장 아님).
            if not resp.get("hasNext") or not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(next_token)

    # ────────────────────────────────────────────────
    # 지급내역 (정산 단위 통장 지급액 — 윙 서비스이용료/차감 검증)
    # ────────────────────────────────────────────────
    def get_settlement_histories(
        self,
        *,
        revenue_recognition_year_month: str,
        max_per_page: int = 50,
    ) -> list | None:
        """지급내역 조회 (명세 §6-2). YYYY-MM 인식월 단위. ★응답은 JSON 배열을 직접 반환.

        revenue-history와 달리 code/data 래핑이 없어 리스트 그대로 온다. 빈 배열=해당 월 미정산.
        반환: [{settlementType, settlementDate, serviceFee, sellerServiceFee, finalAmount, ...}] 또는
        None(실패). 빈 배열([])과 None(실패)을 구분해 Harness가 처리한다.
        """
        params = {
            "vendorId": self.vendor_id,
            "revenueRecognitionYearMonth": revenue_recognition_year_month,
            "maxPerPage": max_per_page,
        }
        resp = self._request("GET", _SETTLEMENT_PATH, params)
        if resp is None:
            return None
        if isinstance(resp, list):
            return resp
        # 일부 응답이 dict로 래핑될 가능성 방어(라이브는 배열 확인됨 §6-2).
        # codex [P1]: 에러 dict({code,message})를 []로 만들면 '정상 미정산' 위장 →
        # 순이익 조용히 틀어짐(원칙22). 성공 code만 data 허용, 그 외는 None(실패 표면화).
        if isinstance(resp, dict):
            if str(resp.get("code")) in ("200", "SUCCESS"):
                return resp.get("data") or []
            log.error(
                "지급내역 dict 응답(에러 가능) code=%s msg=%s",
                resp.get("code"), resp.get("message"),
            )
            return None
        return None
