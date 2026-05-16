# cafe24_status_mapper.py — SA: cafe24 order_status 코드를 정규 상태로 변환
# 단일 책임: cafe24 공식 order_status enum → active|cancelled|returned|exchanged|pending
# 다른 SA를 모름. 순수 함수.
from __future__ import annotations

import re

# 매출/순이익에서 제외해야 하는 정규 상태 (Harness가 참조)
REVENUE_EXCLUDED = frozenset({"cancelled", "returned", "pending"})

# cafe24 raw order_status 형식: 알파벳 1 + 숫자 2 (예: N40, C40, R20, E00)
_CODE_RE = re.compile(r"^[A-Z]\d{2}$")

# 공식 enum (developers.cafe24.com Orders 리소스 order_status)
# N00 입금전 / N02~N50 정상 / C* 취소 / R* 반품 / E* 교환
_EXPLICIT = {
    "N00": "pending",  # 입금전 (매출 미확정)
}


def normalize_status(code: str | None) -> str:
    """cafe24 order_status 코드 → 정규 상태.

    prefix 기반 폴백으로 cafe24가 신규 코드를 추가해도 안전:
      N* → active (N00만 pending)
      C* → cancelled
      R* → returned
      E* → exchanged
    인식 불가 시 보수적으로 'active' (매출 유지, 누락보다 과대가 덜 위험한 케이스는 아니나
    알 수 없는 코드를 임의 제외하면 매출 손실 → active 유지 후 로깅은 호출측 책임).
    """
    if not code:
        return "active"
    c = code.strip().upper()
    if c in _EXPLICIT:
        return _EXPLICIT[c]
    # raw 코드 형식이 아니면(예: 구버전 정규화 문자열) 임의 제외 방지 → active
    if not _CODE_RE.match(c):
        return "active"
    head = c[0]
    if head == "N":
        return "active"
    if head == "C":
        return "cancelled"
    if head == "R":
        return "returned"
    if head == "E":
        return "exchanged"
    return "active"
