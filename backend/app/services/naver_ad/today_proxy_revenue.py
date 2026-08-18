# today_proxy_revenue.py — 캠페인별 **당일** 매출 프록시 SA (D-NAO-104 Phase 1, 계획서 §4-ⓐ).
"""역할(SA·단일 책임·읽기 전용): "오늘 이 캠페인은 얼마를 벌었나"에 답할 **유일한 근사치**를
캠페인 단위로 산출한다. 판정도 문장 조립도 하지 않는다 — 숫자와 "왜 못 구했는지"만 준다.

★왜 별도 SA인가: `actual_revenue.naver_order_revenue()`는 **계정 총계 전용**이다(주문에는
캠페인 귀속 정보가 없다). 캠페인 배분은 쇼핑 광고그룹↔판매상품 매핑(naver_adgroup_product,
mall_product_id == orders.platform_product_id)으로만 성립하고, 그 매핑이 없는 지면
(파워링크·브랜드검색)은 **원리적으로 배분이 불가능**하다. 그때 0을 채우면 거짓이므로
`revenue=None`(= 알 수 없음)과 사유를 함께 돌려준다(원칙22, 계획서 §0-5).

★정직 경계(소비 전 숙지 — 화면에 반드시 병기):
  이 값은 "광고로 인한 매출"이 아니라 **그 상품의 그날 전체 판매액**이다(광고 외 유입 포함).
  구조적으로 과대추정 방향의 **상한 프록시**다([[naver-ad-today-conversion-via-smartstore]]).
  budget_pacing이 증액 판정에서 이 신호를 "필요조건"으로만 쓰는 것과 같은 이유로, 성과 뷰도
  이걸 확정 성과로 표시하면 안 된다.

★회계 규약은 actual_revenue와 동일하게 고정한다(채널6 · 매출제외 상태 제외 ·
  selling_price=라인총액이라 ×수량 2중계상 없음). 여기서만 다르게 굴면 같은 날 두 화면이
  다른 매출을 말한다.

★한 상품이 여러 캠페인에 매핑돼 있으면 매출을 캠페인 수로 **균등 분할**한다. 실제 기여
  비율은 알 수 없고(추정 금지), 양쪽에 100%씩 계상하면 계정 합계가 실제 매출을 넘어버린다.
  분모는 `product_campaign_share.campaigns_per_product` **한 곳에서만** 온다(P2-1) — 여기서
  요청 범위 안의 캠페인만 세면 조회 범위에 따라 같은 캠페인의 매출이 달라진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import extract
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, Order
from app.services.cafe24_status_mapper import REVENUE_EXCLUDED
from app.services.naver_ad import load_window, product_campaign_share
from app.utils.kst import kst_now

NAVER_CHANNEL_ID = 6

# 주문 수집 크론의 «분» — scheduler_service의 ("sync_naver_orders_hourly", "45 * * * *").
# ★시간 버킷의 완결 여부를 정하는 것은 **시계가 아니라 이 크론**이다. 벽시계로 "지난 시간이니
#   끝났다"고 보면, 아직 수집이 안 돈 구간을 «매출 0»으로 읽는다 — D-NAO-193이 수리한
#   «적재 창 밖을 0으로 읽는» 결함과 같은 모양이다.
_ORDERS_SYNC_MINUTE = 45

# 매핑이 없어 배분 자체가 불가능한 경우의 사유(화면이 그대로 쓸 수 있는 한국어).
NO_MAPPING_REASON = "이 광고에 연결된 판매 상품 정보가 없어 오늘 매출을 계산할 수 없습니다."


def product_ids_by_campaign(db: Session, campaign_ids: list[str]) -> dict[str, list[str]]:
    """{campaign_id: [상품ID…]} — 1쿼리(N+1 금지). 매핑이 없는 캠페인은 키 자체가 없다.

    소스는 shopping_ad_product_sync 스냅샷이라 **쇼핑 캠페인만** 행이 있다(파워링크·브랜드검색은
    소재에 mallProductId가 없다). 갓 'ours'로 전환돼 다음 sync 전인 캠페인도 여기서 비어 있다 —
    둘 다 "아직 모른다"이지 "매출 0"이 아니다."""
    if not campaign_ids:
        return {}
    rows = (
        db.query(NaverAdgroupProduct.campaign_id, NaverAdgroupProduct.mall_product_id)
        .filter(NaverAdgroupProduct.campaign_id.in_(campaign_ids))
        .all()
    )
    by_campaign: dict[str, list[str]] = {}
    for cid, pid in rows:
        if not cid or not pid:
            continue
        bucket = by_campaign.setdefault(cid, [])
        if pid not in bucket:
            bucket.append(pid)
    return {cid: sorted(pids) for cid, pids in by_campaign.items()}


def revenue_by_product(db: Session, product_ids: list[str], day: date) -> dict[str, Decimal]:
    """당일(KST) 상품별 실주문 매출 합 — 1쿼리. 주문이 없는 상품은 키가 없다(=0원).

    ★공개 함수다(D-NAO-193): probe_revert의 출혈밸브가 «상한 프록시» 보강 신호로 같은 값을
      쓴다. 회계 규약(채널6·매출제외 상태·selling_price=라인총액)이 **이 한 곳에만** 있어야
      한다 — budget_pacing.py:213이 동명의 로컬 함수로 Order를 직접 쿼리해 규약을 병렬
      재구현한 전례가 있다(ref 72 §0 행 D). 새 소비처는 이 함수를 부른다.
    ⚠️캠페인 배분(균등 분할)은 여기 없다 — build()가 한다. 상품 단위 원값이다."""
    if not product_ids:
        return {}
    rows = (
        db.query(
            Order.platform_product_id,
            sqlfunc.coalesce(sqlfunc.sum(Order.selling_price), 0),
        )
        .filter(
            Order.channel_id == NAVER_CHANNEL_ID,
            Order.status.notin_(tuple(REVENUE_EXCLUDED)),
            Order.platform_product_id.in_(product_ids),
            Order.order_date >= datetime.combine(day, time.min),
            Order.order_date <= datetime.combine(day, time.max),
        )
        .group_by(Order.platform_product_id)
        .all()
    )
    return {pid: Decimal(str(amount or 0)) for pid, amount in rows if pid}


@dataclass(frozen=True)
class HourlyRevenue:
    """시(hour) 단위 상품 매출 프록시 + **어느 버킷이 아직 미완인가**.

    ★`hours`의 0원과 `incomplete_hours`의 0원은 **다른 값**이다 — 전자는 «측정된 0»,
      후자는 «아직 안 들어왔다». 소비처가 이 둘을 합치면 D-NAO-193이 수리한 결함이 재발한다.
    ★`order_counts`를 같이 주는 이유: 시간대 금액은 **단건 극단치에 쉽게 지배된다**(2026-08-18
      실측 — 90일 창에서 hour=15 총액의 73%가 주문 3건에서 나왔다). 건수 없이 금액만 보면
      «이 시간대가 강하다»로 오독한다.
    """

    day: date
    hours: dict[int, Decimal]           # 0~23 전부 채움(주문 없으면 0)
    order_counts: dict[int, int]        # 0~23 전부 채움
    complete_through_hour: int | None   # 이 시각까지가 완결. None = 그날 완결 버킷 없음
    incomplete_hours: tuple[int, ...] = field(default=())

    def total(self) -> Decimal:
        """전 시간 합 — 일 단위 프록시(`revenue_by_product` 합)와 일치해야 한다(검산용)."""
        return sum(self.hours.values(), Decimal(0))

    def complete_total(self) -> Decimal:
        """완결 버킷만의 합 — «지금까지 확실히 팔린 액수»의 하한."""
        return sum(
            (amt for hour, amt in self.hours.items() if hour not in self.incomplete_hours),
            Decimal(0),
        )


def last_complete_hour(now: datetime, day: date) -> int | None:
    """`day`의 시 버킷 중 «수집이 지나간» 마지막 시. 과거 날짜면 23, 미완이면 그 이전, None=없음.

    근거: 수집 크론이 매시 :45에 7일 창을 다시 훑으므로, S:45 회차가 돌고 나면 그 시각 이전
    주문은 원장에 있다. 시 버킷 h는 h:59:59에 끝나므로 **(h+1):45 회차가 돈 뒤에야** 완결이다.
      · now의 분 ≥ 45 → 마지막 회차 = now.hour:45 → 완결은 now.hour − 1 까지
      · now의 분 < 45  → 마지막 회차 = (now.hour−1):45 → 완결은 now.hour − 2 까지
    ⚠️**이것은 필요조건이지 충분조건이 아니다**: 우리 수집이 그 시각을 지났는지만 안다.
      스마트스토어가 주문을 API에 노출하기까지의 플랫폼측 지연은 `[미상]`이라, 완결로 표시된
      버킷도 뒤늦게 늘어날 수 있다. 그래서 이 값은 «끄는 판정»(상한 0 ⇒ 참값 0) 쪽으로만
      안전하고, «켜는 판정»의 근거로 쓰면 안 된다(ref 72 §1의 비대칭).
    """
    if day < now.date():
        return 23
    if day > now.date():
        return None
    offset = 1 if now.minute >= _ORDERS_SYNC_MINUTE else 2
    last = now.hour - offset
    return last if last >= 0 else None


def revenue_by_hour(
    db: Session, product_ids: list[str], day: date, *, now: datetime | None = None
) -> HourlyRevenue:
    """상품들의 `day`(KST) **시간대별** 매출 프록시 — 1쿼리. 회계 규약은 `revenue_by_product`와 동일.

    ★왜 여기 두는가(ref 72 §2-②): `Order.order_date`에는 결제 시:분:초가 **이미** 저장돼 있는데
      코드 전체에서 hour를 뽑는 곳이 **0건**이었다 — API 공백이 아니라 **읽기 계층 공백**이다.
      새 수집·새 컬럼이 필요 없다. 그리고 회계 규약(채널6·매출제외 상태·selling_price=라인총액)이
      이 파일에 단일화돼 있으므로, 시간 해상도도 여기 얹어야 `budget_pacing.py:213`류의
      병렬 재구현이 또 생기지 않는다.
    ⚠️`order_date`는 **결제 시각**이지 클릭 시각이 아니다. 클릭→결제 리드타임은 저장소 어디에도
      없어 `[미상]`이므로, 같은 시각의 광고비와 1:1로 대응시키면 원리적으로 오염된다
      (ref 72 §1 — 같은 시각 비용↔매출 대응은 «불성립»으로 확정).
    """
    now = now or kst_now()
    # 적재 창 밖을 조용히 «0»으로 읽지 않는다(D-NAO-193 재발 방지 층).
    load_window.require_loaded(
        "orders", day, now.date(), reader="today_proxy_revenue.revenue_by_hour"
    )

    hours: dict[int, Decimal] = {h: Decimal(0) for h in range(24)}
    counts: dict[int, int] = {h: 0 for h in range(24)}
    if product_ids:
        # extract('hour')는 SQLite에서 CAST(STRFTIME('%H',…) AS INTEGER)로 컴파일된다 —
        # strftime을 직접 쓰면 PostgreSQL 이행 때 통째로 깨진다(스택 기본값이 SQLite→PG).
        rows = (
            db.query(
                extract("hour", Order.order_date).label("h"),
                sqlfunc.coalesce(sqlfunc.sum(Order.selling_price), 0),
                sqlfunc.count(Order.id),
            )
            .filter(
                Order.channel_id == NAVER_CHANNEL_ID,
                Order.status.notin_(tuple(REVENUE_EXCLUDED)),
                Order.platform_product_id.in_(product_ids),
                Order.order_date >= datetime.combine(day, time.min),
                Order.order_date <= datetime.combine(day, time.max),
            )
            .group_by("h")
            .all()
        )
        for hour, amount, cnt in rows:
            if hour is None:
                continue
            h = int(hour)
            if 0 <= h <= 23:
                hours[h] = Decimal(str(amount or 0))
                counts[h] = int(cnt or 0)

    through = last_complete_hour(now, day)
    incomplete = tuple(h for h in range(24) if through is None or h > through)
    return HourlyRevenue(
        day=day,
        hours=hours,
        order_counts=counts,
        complete_through_hour=through,
        incomplete_hours=incomplete,
    )


def build(db: Session, campaign_ids: list[str], day: date) -> dict[str, dict]:
    """{campaign_id: {revenue, product_count, shared_product_count, reason}} — 요청한 캠페인 전부.

    revenue: int(원) 또는 **None**(=알 수 없음, 상품 매핑 부재). 매핑이 있는데 그날 주문이
      없으면 revenue=0이다 — 이건 측정된 0이지 '모름'이 아니라서 구분해 돌려준다.
    reason: revenue가 None일 때만 채워지는 한국어 사유(화면 그대로 노출용).
    shared_product_count: 여러 캠페인이 공유해 매출을 나눠 계상한 상품 수(정직 표기용).
    """
    result: dict[str, dict] = {
        cid: {"revenue": None, "product_count": 0, "shared_product_count": 0,
              "reason": NO_MAPPING_REASON}
        for cid in campaign_ids
    }
    pids_by_campaign = product_ids_by_campaign(db, campaign_ids)
    if not pids_by_campaign:
        return result

    # 분모 = "그 상품을 매핑한 **모든** 캠페인 수"(요청 범위·자동운영 여부 무관, P2-1).
    # ★요청 범위 안에서만 세면 안 된다: 같은 캠페인을 단독 조회할 때와 전체 조회할 때 매출이
    #   달라진다(범위가 좁으면 분모가 작아져 과대 계상).
    all_pids = sorted({pid for pids in pids_by_campaign.values() for pid in pids})
    shares = product_campaign_share.campaigns_per_product(db, all_pids)
    revenue_by_pid = revenue_by_product(db, all_pids, day)

    for cid, pids in pids_by_campaign.items():
        total = Decimal(0)
        shared = 0
        for pid in pids:
            share = shares.get(pid, 1) or 1
            if share > 1:
                shared += 1
            total += revenue_by_pid.get(pid, Decimal(0)) / Decimal(share)
        result[cid] = {
            "revenue": int(total),
            "product_count": len(pids),
            "shared_product_count": shared,
            "reason": None,
        }
    return result
