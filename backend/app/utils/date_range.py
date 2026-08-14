"""운영 패널 기간 파라미터의 **단일 해석 규칙**.

## 왜 한 곳인가 (2026-08-14)

운영 패널들은 「오늘/어제/7·15·30일」 프리셋(`days`)만 받고 **임의 기간을 못 골랐다.** 그래서
특정 과거 하루를 다시 보려면 방법이 없었다 — 2026-08-13 완료 QA가 정확히 여기서 막혔다
(「API가 임의 과거 단일일 지정을 제공하지 않는다」).

날짜 파라미터를 화면마다 따로 붙이면 **곧 갈라진다.** 이 저장소는 그 실패를 이미 겪었다:
같은 «판매유형 축» 규칙이 `intelligence.py`에만 있고 `coupang_ops.py`에 없어서, 2026-08-03에
고친 버그가 2026-08-13에 다른 화면에서 그대로 재발했다(교훈 #103·#281). 그래서 규칙을 여기
한 곳에 두고 라우터들이 **참조**한다.

## 규칙
- 날짜(`date_from`·`date_to`)를 주면 **그게 이긴다.** 프리셋은 날짜의 단축키일 뿐이다.
- 한쪽만 주면 **거절한다.** 조용히 나머지를 채우면 화면 숫자가 사용자가 고른 기간이
  아니게 되는데, 화면은 그 사실을 말하지 않는다.
- 상한(기본 90일)은 `days` 파라미터의 `le=90`과 **같은 값**이어야 한다. 두 입구가 다른
  한계를 가지면 한쪽으로만 들어오는 기간이 생긴다.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException

from app.utils.kst import kst_today

#: 임의 기간 상한 — 라우터의 `days: Query(le=90)`과 같은 값.
MAX_SPAN_DAYS = 90


def preset_range(days: int) -> tuple[date, date]:
    """프리셋 → (시작일, 종료일). 0=오늘 하루, 1=어제 하루, N=오늘까지 최근 N일."""
    today = kst_today()
    if days == 0:
        return today, today
    if days == 1:
        d = today - timedelta(days=1)
        return d, d
    return today - timedelta(days=days - 1), today


def resolve_range(days: int, date_from: str | None, date_to: str | None,
                  *, max_span_days: int = MAX_SPAN_DAYS) -> tuple[date, date]:
    """기간 결정 — 날짜가 있으면 날짜, 없으면 프리셋.

    ⚠️라우터 시그니처에서 `Query(default=None)`을 쓰지 마라. 그러면 **직접 호출**
      (테스트·내부 재사용) 시 기본값이 `None`이 아니라 `Query` 객체로 들어와 `is None`
      판정이 통째로 빗나간다. 그냥 `date_from: str | None = None`으로 둔다.
    """
    if (date_from is None) != (date_to is None):
        raise HTTPException(400, "date_from·date_to는 함께 지정해야 합니다")
    if date_from is None or date_to is None:
        return preset_range(days)
    try:
        dfrom = date.fromisoformat(date_from)
        dto = date.fromisoformat(date_to)
    except (ValueError, TypeError):
        raise HTTPException(400, "날짜 형식은 YYYY-MM-DD입니다")
    if dfrom > dto:
        raise HTTPException(400, f"시작일({dfrom})이 종료일({dto})보다 늦습니다")
    span = (dto - dfrom).days + 1
    if span > max_span_days:
        raise HTTPException(400, f"기간은 최대 {max_span_days}일입니다(요청 {span}일)")
    return dfrom, dto
