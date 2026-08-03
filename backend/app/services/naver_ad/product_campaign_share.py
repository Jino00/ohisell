# product_campaign_share.py — 상품 매출을 여러 캠페인에 나눠 계상할 때 쓰는 **분모 단일 출처**.
"""역할(순수 조회 유틸리티·읽기 전용): "이 판매 상품(mall_product_id)을 광고하는 캠페인이
몇 개인가"를 센다. 판정도 조립도 하지 않는다 — 다른 SA들이 같은 분모를 쓰게 하는 것이 전부다.

★왜 별도 모듈인가(P2-1 리뷰): 같은 규약("공유 상품 매출은 캠페인 수로 균등 분할")을 두 곳이
각자 세고 있었고, **셋이 서로 다른 모집단**이었다.
  · budget_pacing  — auto_operate 캠페인만 셌다 → 대행사·수동 캠페인이 같은 상품을 광고해도
    분모에서 빠져 **자기 몫을 과대 계상**했다(증액 게이트가 부당 통과할 수 있는 방향, 리뷰 P3-5).
  · 성과 뷰        — 호출자가 넘긴 캠페인 범위만 셌다 → 조회 범위에 따라 같은 날 같은 캠페인의
    매출이 달라졌다(화면 간 숫자 불일치).
분모는 조회 목적·자동운영 여부와 **무관한 사실**이어야 한다. 그래서 여기는 필터가 없다:
naver_adgroup_product에 그 상품을 매핑한 **모든** 캠페인을 센다.

★균등 분할이 정직한 이유: 실제 기여 비율은 알 수 없다(주문에 캠페인 귀속이 없다 — 그것이
애초에 프록시를 쓰는 이유다). 추정 금지 원칙상 균등이 가장 보수적이고 설명 가능한 배분이며,
어떤 배분을 쓰든 **합계가 실제 매출을 넘지 않는다**는 성질이 지켜져야 한다.

★적재 범위 주의: naver_adgroup_product는 shopping_ad_product_sync가 optimizer='ours' 쇼핑
캠페인만 스냅샷한다. 즉 지금은 "모든 캠페인"이라 해도 사실상 ours 캠페인끼리의 공유만 잡힌다 —
동기화 범위가 넓어지면 이 함수는 **코드 변경 없이** 더 정확해진다(그것이 필터를 안 두는 이유다).
"""
from __future__ import annotations

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct


def campaigns_per_product(db: Session, product_ids: list[str] | set[str]) -> dict[str, int]:
    """{상품ID: 그 상품을 매핑한 캠페인 수} — 1쿼리. 매핑이 없는 상품은 키가 없다(호출부가 1로 취급).

    ★**관점 필터**를 추가하지 말 것(auto_operate·optimizer·요청 범위 무엇도). 분모가 관점에 따라
    달라지면 같은 상품 매출이 화면마다 다른 값이 된다 — 이 모듈의 존재 이유가 그것이다.

    ★★**신선도 필터도 걸지 말 것**(2026-08-03 폴백 방향 조사에서 확정). 이 함수는 **분모**다:
    호출부가 `shares.get(pid, 1)`로 읽으므로 **행이 빠지면 분모가 작아지거나 아예 1이 되고,
    같은 매출이 여러 캠페인에 100%씩 계상돼 예산 증액 게이트가 부당 통과한다**(today_proxy_
    revenue:117 · budget_pacing:243). 이 모듈 헤더가 기록한 2026-07-28 사고가 정확히 그
    방향이었다 — 신선도 필터를 걸면 코드 변경 없이 그 버그를 재도입하게 된다.
    분자(campaign_target_resolver·today_proxy_revenue 쪽 상품 목록)에는 필터가 걸려 있는데
    분모에는 안 걸린 것은 **의도된 비대칭**이다: 분자 과소 + 분모 과대 = 프록시 ROAS 과소 =
    증액이 덜 열리는 방향(보수)."""
    uniq = [p for p in dict.fromkeys(product_ids) if p]
    if not uniq:
        return {}
    rows = (
        db.query(
            NaverAdgroupProduct.mall_product_id,
            sqlfunc.count(sqlfunc.distinct(NaverAdgroupProduct.campaign_id)),
        )
        .filter(NaverAdgroupProduct.mall_product_id.in_(uniq))
        .group_by(NaverAdgroupProduct.mall_product_id)
        .all()
    )
    return {pid: int(n) for pid, n in rows if pid}
