# rg_net_revenue.py — 로켓그로스(RG) 매출·원가를 **콘솔 net 축**으로 읽는다 (D-CPP-47).
#
# 왜 이 SA가 필요한가 (ref 89, 2026-08-21 조사):
#   우리 gross 주문 원장(`coupang_rg_order_item`)은 **틀리지 않았다** — 쿠팡 RG 주문 API와
#   원 단위로 일치한다. 어긋나는 것은 쿠팡의 «두 표면»이다:
#     주문 API = gross(취소·반품 미반영) · 콘솔 판매분석 = net.
#   그리고 **RG 주문 API에는 취소·상태 축이 없다**(목록·단건 둘 다). 즉 gross에서 net을
#   빼는 길이 원리적으로 없다. 오픽스 30일 실측 +694,070원(+11.8%)·34개가 그 간극이고,
#   갭의 성격은 단가가 아니라 **건수**다(30일 중 9일은 원 단위 완전 일치).
#   ⇒ net을 알고 싶으면 **net을 주는 표면을 읽어야 한다.** 그게 이 모듈이다.
#
# 두 개의 net 축을 쓴다 — 축이 갈리는 것은 의도다:
#   ① 요약축 `CoupangVendorSummaryDaily`(grain: date×account×registration_type)
#      → **소계용**. 커버리지가 길다(WING1 06-07~).
#   ② 옵션축 `CoupangVendorItemSalesDaily`(grain: account×date×vendor_item_id)
#      → **원가 계산·옵션 귀속용**. 옵션ID가 있어야 원가에 닿는데 요약축엔 옵션ID가 없다.
#      커버리지는 페처 `vi_days`(기본 7일) 롤링이라 **창 앞쪽이 빌 수 있다**.
#      ★2026-08-22 일회성 백필로 WING1 06-07~ · WING2 06-12~ 까지 채워졌다(구 주석의
#        「WING1 08-05~ · WING2 07-27~」는 백필 전 상태였다). 롤링은 7일로 원복돼 있으므로
#        그 이전 구간은 다시 안 들어온다 — 「지금 비어 있지 않다」를 「영원히 안 빈다」로 읽지 말 것.
#   두 축의 등가는 실측됐다: 초판 16일 → **147 계정-일 전건**(WING1 76일·WING2 71일,
#     2026-08-22 15:47 KST prod 읽기 전용): 금액 불일치 0 · 수량 불일치 0 · 한쪽에만 있는 날 0.
#   그래서 소계를 요약축으로 읽고 원가를 옵션축으로 계산해도 두 숫자가 안 갈라진다.
#
# ★이 모듈이 하지 않는 것:
#   - 수수료를 계산하지 않는다. RG 수수료는 **쿠팡이 실제 청구한 금액**이 정산 원장에
#     있고(`CoupangRgSettlementFee`), 그걸 읽는 것은 `profit_calculator.get_rg_total_by_account`다.
#     금액표로 되계산하면 프로모션·저가할인·합포장 재산정 때문에 fragile 머니코드가 된다
#     (ref 17 §S8 설계 함의 — 「쿠팡 금액표 완전 복제 = 잘못된 설계」).
#   - 광고비를 만들지 않는다. 정산서의 `ad_sales`는 별개 비용이 아니라 **광고센터 PA 광고비의
#     «공제»**이고(D-CPP-43, 윙 1차 출처: "매입세금계산서 1건이 발행"), RG 광고비는
#     다른 채널과 **같은 PA 원장**에서 온다.
#   - 없는 구간을 추정하지 않는다. 옵션축이 없는 날짜의 원가는 0이 아니라 **미상**이다
#     (금지선: 추정 배분 금지). 커버리지를 행에 실어 화면이 자백하게 한다.
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    CoupangVendorItemSalesDaily,
    CoupangVendorSummaryDaily,
)

log = logging.getLogger(__name__)

ZERO = Decimal("0")

#: 콘솔 등록유형(ref 18). RFM=로켓그로스(RG) / NORMAL=3P 마켓플레이스.
#: 두 축이 같은 어휘를 쓴다 — `vendor_item_sales_sync._TYPES`와 같은 값이다.
REGISTRATION_TYPE_RG = "RFM"

#: RG 채널의 `Channel.sell_type`. seed.py의 COUPANG_RG1·COUPANG_RG2가 이 값을 갖는다.
RG_SELL_TYPE = "RG"


def rg_channel_for_account(db: Session, account_key: str) -> Channel | None:
    """Wing 로그인 계정(COUPANG_WING1/2) → 그 법인의 RG 채널(COUPANG_RG1/2).

    ★다리는 `company`다. 소스마다 계정 식별 키가 다르기 때문이다(`intelligence._resolve_account`
      의 실측과 같은 사실): net 원장·정산은 `account_key`(=Wing 채널 code)로 오는데, RG 채널은
      code가 `COUPANG_RG1`이라 **문자열이 안 맞는다.** 실제로 이 불일치가 라이브 결함을 만들었다 —
      `dashboard._channel_rows`가 `ch.code in rg_by_account`로 매칭해서, RG 정산 수수료가
      RG 행이 아니라 **3P 행**에서 빠지고 있었다(D-CPP-47이 고친다).
    같은 법인에 RG 채널이 여럿이면 id 최소를 고른다(결정적 — `_cost_master`와 같은 규율).
    """
    company = (
        db.query(Channel.company)
        .filter(Channel.code == account_key, Channel.platform == "coupang")
        .scalar()
    )
    if not company:
        return None
    rows = (
        db.query(Channel)
        .filter(
            Channel.platform == "coupang",
            Channel.company == company,
            Channel.sell_type == RG_SELL_TYPE,
        )
        .order_by(Channel.id)
        .all()
    )
    if not rows:
        return None
    if len(rows) > 1:  # 사실 경고(D-3) — 임의판단 금지, 고른 것을 밝힌다
        log.warning(
            "법인 %s에 RG 채널이 %d개 — id 최소(%s) 선택",
            company, len(rows), rows[0].id,
        )
    return rows[0]


# ════════════════════════════════════════════════
# ① 요약축 — 소계용 net 매출
# ════════════════════════════════════════════════
def net_revenue_by_account(
    db: Session, date_from: date, date_to: date
) -> dict[str, dict]:
    """[from, to] 콘솔 net RG 매출 — {account_key: {"revenue", "units"}}.

    ★소계를 **요약축 단독으로** 읽는 이유(계약 판단기준): 소계 grain에서 두 축의 등가가
      실측됐고(16일 불일치 0), 날짜별로 옵션축을 폴백시키면 «측정된 개선 0에 복잡성만» 산다.
      옵션축은 원가 계산에서만 쓴다.

    gmv/units 음수 허용: 콘솔 GMV = 판매액 − 환불액이라 환불 초과일은 정당하게 음수다
      (요약축·옵션축 모델이 이미 같은 정책 — 「비용은 0 이상」 가정을 복제하면 백필이 통째로
      막혔던 전례가 있다).
    """
    rows = (
        db.query(
            CoupangVendorSummaryDaily.account_key,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorSummaryDaily.gmv), 0),
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorSummaryDaily.units_sold), 0),
        )
        .filter(
            CoupangVendorSummaryDaily.summary_date >= date_from,
            CoupangVendorSummaryDaily.summary_date <= date_to,
            CoupangVendorSummaryDaily.registration_type == REGISTRATION_TYPE_RG,
        )
        .group_by(CoupangVendorSummaryDaily.account_key)
        .all()
    )
    return {
        str(ak): {"revenue": Decimal(str(gmv or 0)), "units": int(units or 0)}
        for ak, gmv, units in rows
    }


# ════════════════════════════════════════════════
# ② 옵션축 — 원가 계산·옵션 귀속용
# ════════════════════════════════════════════════
def net_revenue_by_option(
    db: Session, date_from: date, date_to: date, account_key: str | None = None
) -> dict[str, dict]:
    """[from, to] 콘솔 net RG 매출을 **옵션ID별** 집계 — `intelligence._agg_rg_orders`와 같은 모양.

    반환 {vid: {"revenue", "qty", "order_count", "name"}} — 종합조망이 gross 원장 대신
    이걸 쓰면 대시보드 소계와 같은 net 축 위에 선다(계약 ⓑ).

    ★`order_count`는 `total_orders`(콘솔이 준 주문 수)다. gross 원장의 «라인 수»와 뜻이 다르다 —
      콘솔은 취소분을 뺀 net 주문 수라 더 작다. 이 차이가 곧 우리가 메우려던 갭(+34개)이다.
    ★커버리지 밖 날짜는 **행이 없다**(0이 아니다). 그래서 이 함수만 보고 「그날 RG가 0이었다」고
      말하면 안 된다 — `option_axis_coverage`로 «받았는데 0» 과 «안 받았다»를 갈라야 한다.
    """
    q = (
        db.query(
            CoupangVendorItemSalesDaily.vendor_item_id,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.gmv), 0),
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.units_sold), 0),
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.total_orders), 0),
            sqlfunc.max(CoupangVendorItemSalesDaily.item_name),
        )
        .filter(
            CoupangVendorItemSalesDaily.sale_date >= date_from,
            CoupangVendorItemSalesDaily.sale_date <= date_to,
            CoupangVendorItemSalesDaily.registration_type == REGISTRATION_TYPE_RG,
        )
    )
    if account_key is not None:
        q = q.filter(CoupangVendorItemSalesDaily.account_key == account_key)
    rows = q.group_by(CoupangVendorItemSalesDaily.vendor_item_id).all()
    return {
        str(vid): {
            "revenue": Decimal(str(gmv or 0)),
            "qty": int(units or 0),
            "order_count": int(orders or 0),
            "name": name,
        }
        for vid, gmv, units, orders, name in rows
    }


def accounts_in_window(db: Session, date_from: date, date_to: date) -> list[str]:
    """창 안에 **콘솔 축이 존재하는** 계정 키 목록 — 커버리지 판정의 «분모»다.

    ★`registration_type`을 안 건다. 묻는 것은 「이 계정의 콘솔 수집이 이 창에 돌았나」이지
      「RG를 팔았나」가 아니다. RFM만 세면 RG 매출이 0인 계정이 목록에서 빠지고, 그 계정의
      «수집 안 됨»이 «판매 0»으로 읽힌다 — 이 모듈이 통째로 막으려는 그 오독이다.
    ★두 축의 **합집합**이다. 요약축만 보면 「옵션축이 통째로 없는 계정」이 안 잡히고,
      옵션축만 보면 그 계정이 스스로를 완전하다고 말한다. 둘 다 봐야 한다.
    """
    rows_o = db.query(CoupangVendorItemSalesDaily.account_key).filter(
        CoupangVendorItemSalesDaily.sale_date >= date_from,
        CoupangVendorItemSalesDaily.sale_date <= date_to,
    ).distinct().all()
    rows_s = db.query(CoupangVendorSummaryDaily.account_key).filter(
        CoupangVendorSummaryDaily.summary_date >= date_from,
        CoupangVendorSummaryDaily.summary_date <= date_to,
    ).distinct().all()
    return sorted({str(a) for (a,) in rows_o} | {str(a) for (a,) in rows_s})


def option_axis_coverage(
    db: Session, date_from: date, date_to: date, account_key: str
) -> dict:
    """창 안에서 옵션축이 **실제로 있는** 날짜 수 — 「없음」과 「0원」을 가른다.

    반환 {"days_total", "days_covered", "first_date", "last_date", "complete"}.

    ★이 함수가 존재하는 이유: 옵션축은 페처 `vi_days`(기본 7일) 롤링이라 창 앞쪽이 비어 있다.
      비어 있는 날의 원가를 0으로 치면 그만큼 순이익이 **위로** 부풀고, 그건 조용히 틀린다.
      교훈 #123과 같은 결 — 「발견 0건」과 「실행 안 됨」이 같은 숫자로 보이면 안 된다.
    ★`registration_type` 필터를 여기선 **걸지 않는다**: 그날 수집이 돌았는지를 묻는 것이지
      그날 RG 판매가 있었는지를 묻는 게 아니다. RG 매출이 0인 날에도 수집은 됐을 수 있고,
      그런 날의 원가는 «미상»이 아니라 «0»이다.
    """
    days_total = (date_to - date_from).days + 1
    row = (
        db.query(
            sqlfunc.count(sqlfunc.distinct(CoupangVendorItemSalesDaily.sale_date)),
            sqlfunc.min(CoupangVendorItemSalesDaily.sale_date),
            sqlfunc.max(CoupangVendorItemSalesDaily.sale_date),
        )
        .filter(
            CoupangVendorItemSalesDaily.account_key == account_key,
            CoupangVendorItemSalesDaily.sale_date >= date_from,
            CoupangVendorItemSalesDaily.sale_date <= date_to,
        )
        .one()
    )
    covered = int(row[0] or 0)
    return {
        "days_total": days_total,
        "days_covered": covered,
        "first_date": row[1],
        "last_date": row[2],
        "complete": covered >= days_total,
    }


def net_cost(
    db: Session,
    date_from: date,
    date_to: date,
    account_key: str,
    cost_master: dict[str, dict],
) -> dict:
    """옵션축 net 수량 × 옵션 원가 — **추정 없는** RG 원가.

    반환 {"cost", "revenue_costed", "revenue_total", "coverage", "options_total",
          "options_costed", "unmapped_revenue"}.

    `cost_master`: `intelligence._cost_master(db)`의 반환({vid: {"cost_price", "name"}}).
      ★직접 만들지 않고 **주입받는다** — 원가 원천이 두 벌이 되면 화면마다 원가가 달라진다.
      그 함수는 중복 매핑을 결정적으로 고르는 규율(원가>0 우선 → product_id 최소)을 이미 갖고 있다.

    ★`coverage`는 **매출 기준**이다(옵션 개수 기준이 아니라). 원가를 못 붙인 옵션이 몇 «개»인지는
      돈의 크기를 말해주지 않는다 — 꼬리 옵션 20개를 놓친 것과 주력 옵션 1개를 놓친 것은
      같은 사고가 아니다. ref 89의 실측(99.44% = 3,135,040/3,152,860)도 매출 기준이다.
    ★음수 수량(환불 초과일)도 그대로 곱한다. 원가도 같이 되돌아오는 것이 맞다.
    """
    by_option = net_revenue_by_option(db, date_from, date_to, account_key)

    cost = ZERO
    revenue_total = ZERO
    revenue_costed = ZERO
    unmapped_revenue = ZERO
    options_costed = 0
    net_orders = 0

    for vid, o in by_option.items():
        rev = o["revenue"]
        revenue_total += rev
        # ★실제 net «주문 수». 요약축엔 이 값이 없다(수량만 있다) — 그래서 옵션축에서 가져온다.
        #   행의 `order_count`가 「주문 건수」라는 뜻을 지키려면 이 숫자여야 한다(아래 반환 참조).
        net_orders += o["order_count"]
        pm = cost_master.get(vid)
        unit_cost = pm.get("cost_price") if pm else None
        if unit_cost is None or unit_cost <= 0:
            # 원가를 못 붙인 매출 — 이익률을 위로 부풀리므로 행마다 자백한다(D-22와 같은 규율).
            unmapped_revenue += rev
            continue
        cost += Decimal(str(unit_cost)) * Decimal(o["qty"])
        revenue_costed += rev
        options_costed += 1

    coverage = (revenue_costed / revenue_total) if revenue_total > 0 else None
    return {
        "cost": cost,
        "revenue_costed": revenue_costed,
        "revenue_total": revenue_total,
        "coverage": coverage,
        "options_total": len(by_option),
        "options_costed": options_costed,
        "unmapped_revenue": unmapped_revenue,
        # ★옵션축이 준 **실제 net 주문 수**. 「판매수량」이 아니다.
        #   같은 조회를 두 번 돌지 않으려고 여기서 같이 낸다(이 함수가 이미 by_option을 훑는다).
        "net_orders": net_orders,
    }


# ════════════════════════════════════════════════
# ③ 광고비 — 판매경로별 «측정된» 귀속 (추정 배분 없음)
# ════════════════════════════════════════════════
def option_sell_route(db: Session, account_key: str) -> dict[str, str]:
    """옵션ID(vendor_item_id) → 판매방식 `"RG"` / `"3P"` — **옵션ID 자체가 판매방식을 담는다.**

    ★근거(Jino 2026-08-22, 윙 화면 캡처): 쿠팡은 **같은 상품·같은 옵션명이라도 판매방식마다
      옵션ID를 따로 발급한다.** 실물 예:
          「오픽스 맥세이프 이지 카드지갑 / 투명 1개」 등록상품ID 16224706669
            · 판매자 배송  → 옵션ID 95501699185
            · 로켓그로스   → 옵션ID 95501699184     (노출상품ID는 9568229053으로 동일)
      ⇒ **한 옵션ID가 3P와 RG 양쪽일 수 없다.** 이것이 이 함수가 성립하는 이유이고, 동시에
        「같은 옵션이 두 경로로 팔려서 광고비를 못 가른다」는 문제가 **원리적으로 존재하지
        않는다**는 뜻이다(그 문제를 풀려고 매출비율 안분 같은 추정을 넣을 뻔했다).

    ★판매 «이력»이 아니라 «정체»로 가른다. 이력으로 가르면 그 창에 안 팔린 옵션이 미배분으로
      떨어지는데, 광고는 돌았는데 판매가 0인 옵션은 실제로 존재하고 금액이 작지 않다
      (네이버 실측: 상품 282개·118,890원 = 일 광고비의 19.6%, 교훈 #327). 정체로 가르면
      «안 팔렸다»와 «어느 쪽인지 모른다»가 섞이지 않는다.

    ★**모든 상품은 카탈로그에 있다**(Jino 2026-08-22: *"모든 상품은 상품관리>상품 조회/수정에
      들어가면 다 있어. 너가 모를 수가 없어"* — 윙 `/vendor-inventory/list`가 판매방식을 옵션마다
      표시한다). 그래서 이 함수의 「모름」은 **설계상의 버킷이 아니라 동기화가 밀렸다는 신호**다.
      우리 카탈로그 사본(`coupang_product_item`)에 없는 옵션에 광고가 돌고 있으면 그건 자백할
      사실이지 정상 상태가 아니다 — 호출부가 `opt_unknown`을 보고 경고할 수 있게 분리해 둔다.

    소스(전부 실측 원장 — 추정 0):
      **우주(전체 옵션)** = `coupang_product_item`(계정별 카탈로그 스냅샷, product_sync 산물)
      RG 표시 = `coupang_rg_inventory`(로켓창고 실재고 — **안 팔린 옵션도 담는다**, 1순위)
              ∪ `coupang_rg_order_item`(RG 주문 이력, 창 밖까지 길다)
              ∪ 옵션축 `registration_type='RFM'`(전 기간)
      3P 표시 = `orders`(우리 3P 주문 이력) ∪ 옵션축 `NORMAL`(전 기간)
      카탈로그에 있는데 RG 표시가 없으면 **3P로 본다** — 로켓그로스가 아닌 것이 판매자 배송이다.
    창을 안 자르는 이유: 옵션ID의 정체는 기간에 안 변한다. 좁게 보면 근거만 줄어든다.

    충돌(한 옵션ID가 RG·3P 양쪽 표시)은 **일어나면 안 되는 일**이라 경고하고 RG로 둔다 —
    RG 쪽 소스가 더 특정적(로켓창고 재고·RG 전용 주문 원장)이기 때문이다.
    """
    from app.models import (  # noqa: PLC0415
        CoupangProductItem,
        CoupangRgInventory,
        CoupangRgOrderItem,
        Order,
    )

    rg_vids: set[str] = set()
    p3_vids: set[str] = set()

    # 카탈로그 = 우주. 여기 있는 옵션은 「모름」으로 떨어지지 않는다.
    catalog: set[str] = {
        str(vid)
        for (vid,) in db.query(CoupangProductItem.vendor_item_id)
        .filter(CoupangProductItem.account_key == account_key)
        .distinct()
        if vid
    }

    for (vid,) in db.query(CoupangRgInventory.vendor_item_id).filter(
        CoupangRgInventory.account_key == account_key
    ).distinct():
        if vid:
            rg_vids.add(str(vid))
    for (vid,) in db.query(CoupangRgOrderItem.vendor_item_id).filter(
        CoupangRgOrderItem.account_key == account_key
    ).distinct():
        if vid:
            rg_vids.add(str(vid))

    axis_rows = (
        db.query(
            CoupangVendorItemSalesDaily.vendor_item_id,
            CoupangVendorItemSalesDaily.registration_type,
        )
        .filter(CoupangVendorItemSalesDaily.account_key == account_key)
        .distinct()
        .all()
    )
    for vid, rt in axis_rows:
        if not vid:
            continue
        (rg_vids if rt == REGISTRATION_TYPE_RG else p3_vids).add(str(vid))

    # 3P 주문 이력 — 계정의 3P 채널을 company 다리로 찾는다(account_key == 3P 채널 code).
    for (vid,) in db.query(Order.platform_product_id).join(
        Channel, Order.channel_id == Channel.id
    ).filter(
        Channel.code == account_key,
        Order.platform_product_id.isnot(None),
    ).distinct():
        if vid:
            p3_vids.add(str(vid))

    both = rg_vids & p3_vids
    if both:  # 옵션ID는 판매방식마다 따로 발급되므로 이 교집합은 비어야 한다
        log.warning(
            "옵션ID %d개가 RG·3P 양쪽 소스에 있다(계정 %s) — 옵션ID는 판매방식마다 따로 "
            "발급되므로 일어나면 안 되는 일이다. RG로 둔다. 예: %s",
            len(both), account_key, sorted(both)[:5],
        )

    # 카탈로그에 있는데 RG 표시가 없는 옵션 = 3P(로켓그로스가 아닌 것이 판매자 배송이다).
    # 이 한 줄이 「판매 이력이 없어서 모르겠다」를 없앤다 — 카탈로그가 정본이기 때문이다.
    p3_vids |= catalog - rg_vids

    route = {vid: "3P" for vid in p3_vids}
    route.update({vid: "RG" for vid in rg_vids})  # 충돌 시 RG 우선(소스가 더 특정적)
    return route


def split_wing_ad_spend(
    db: Session,
    date_from: date,
    date_to: date,
    account_key: str,
    vendor_id: str,
) -> dict:
    """Wing PA 광고비를 3P / RG / 미배분으로 가른다 — **라벨이 아니라 옵션ID의 정체로**.

    반환 {"rg", "p3", "unallocated", "total", "opt_rg", "opt_p3", "opt_unknown",
          "attributed_ratio"}.

    ★왜 `sell_type` 라벨을 쓰지 않는가 (D-CPP-43): 광고 원장의 `sell_type`은 **판매경로를 뜻하지
      않는다.** 오픽스 PA 광고비의 97.28%가 RG로 팔리는 옵션에 쓰이는데 `sell_type='2P'` 행은
      전기간 **0건**이고, 광고비 상위 5개 옵션의 3P 주문은 0건이다(ref 56 §3). D-16이 그 라벨을
      믿고 「겹침 없음」이라 판정했다가 틀렸고, 걸어 둔 감시 장치는 원리적으로 못 잡았다
      (교훈 #261·#268). 라벨은 여기서도 못 믿는다 — 대신 옵션ID를 쓴다(`option_sell_route`).

    ★미배분 = 「그 옵션ID가 우리 원장 어디에도 없다」뿐이다. 재고에도 없고 판매 이력도 없는
      옵션에 광고가 돌았다는 뜻이라, 그 자체가 **알아야 할 사실**이다(옵션 삭제 후 광고 잔존 등).
      추정으로 채우지 않는다 — 금지선이 추정 배분을 금지하고, 그 몫이 어느 판매경로 것인지
      모른다는 사실이 화면이 말해야 할 정보다.
    """
    # 지연 임포트 — 순환 참조 방지(`intelligence`는 이 모듈을 모른다) 및 순수 코어 테스트용.
    from app.services.coupang.intelligence import _agg_ads  # noqa: PLC0415

    # 광고비: ad_option_id 귀속(D-9 — 비용은 «집행» 옵션에 붙는다, 전환 옵션이 아니라).
    # sell_types 기본 = Wing 축(3P,2P). Retail(1P)은 같은 vendor의 «다른 축»이라 빠져 있다.
    ads = _agg_ads(db, date_from, date_to, vendor_id)
    route = option_sell_route(db, account_key)

    rg = p3 = unallocated = total = ZERO
    opt_rg = opt_p3 = opt_unknown = 0
    for vid, a in ads.items():
        spend = a.get("spend", ZERO) or ZERO
        total += spend
        r = route.get(vid)
        if r == "RG":
            rg += spend
            opt_rg += 1
        elif r == "3P":
            p3 += spend
            opt_p3 += 1
        else:
            unallocated += spend
            opt_unknown += 1

    # 검산 등식 — 셋의 합이 총액이어야 한다. 어긋나면 어딘가에서 돈이 사라진 것이다.
    if rg + p3 + unallocated != total:  # pragma: no cover — 방어(도달하면 로직 결함)
        log.error(
            "광고비 분해 검산 실패: rg=%s p3=%s unalloc=%s != total=%s (account=%s)",
            rg, p3, unallocated, total, account_key,
        )

    return {
        "rg": rg,
        "p3": p3,
        "unallocated": unallocated,
        "total": total,
        "opt_rg": opt_rg,
        "opt_p3": opt_p3,
        "opt_unknown": opt_unknown,
        "attributed_ratio": ((rg + p3) / total) if total > 0 else None,
    }
