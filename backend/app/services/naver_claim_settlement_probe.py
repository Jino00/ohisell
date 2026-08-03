# naver_claim_settlement_probe.py — 클레임(반품/교환) 정산 구조 관측 프로브 (관측 전용)
#
# ★존재 이유(2026-08-03): 반품 배송비 수입을 이익에 반영하려는데 **N배송(도착보장) 반품의
#   정산 구조를 모른다**. 라이브 실측:
#     - 비N배송(TODAY/NORMAL) 반품·교환 → productOrderType='DELIVERY' 행이 뜬다.
#       5,000원 지배적(45/50건)·2,500원 3건·7,500원 2건 = 고객이 낸 반품비 = 우리 수입.
#     - N배송 반품은 468건 중 **2건뿐**(2026072553216341 결제 07-25 / 2026072852172181 결제
#       07-28)이고 둘 다 settleDecisionType 3유형 전부에서 정산 행 0건.
#       정산 성숙이 D+12 → 08-06·08-09경 떠야 정상.
#     - 그 2건은 둘 다 비멤버십(deliveryDiscountAmount == 0) → 멤버십 N배송 반품 표본 0건.
#   가설("N배송 회수비는 비N배송과 다르고, 멤버십 반품은 네이버가 보상")은 표본이 없어 지금은
#   검증 불가다. 그래서 검증을 억지로 시도하는 대신 **표본이 익는 순간 자동으로 잡히게** 한다.
#   사람이 08-06·08-09을 기억하고 지키고 있을 수는 없기 때문이다.
#
# 책임(SA): (1) 클레임 주문을 뽑아 (2) 정산 API를 3-decision-type으로 훑고 (3) 관측을 적재하고
#   (4) **처음 보는 조합**만 Slack으로 올린다. 회계·이익 계산은 건드리지 않는다(관측 전용 —
#   가설이 검증되기 전에 숫자를 바꾸면 근거 없는 수치가 회계에 박힌다).
#
# ★조용한 실패 금지: 정산 조회 실패는 예외로 올라간다(client 계약). "아직 정산 안 뜸"과
#   "조회가 실패함"을 섞으면 표본이 익어도 0건으로 기록하고 알림 없이 지나간다.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.models import Channel, NaverClaimSettlementProbe, Order
from app.services.naver_ad.slack_notifier import notify_text
from app.services.order_delivery import NBAESONG_ATTR, product_order_of
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# 스캔 창 — 기존 건별정산 수집(sync_naver_case_settlement_job)과 같은 44일.
#   ①468건 전체를 매일 훑으면 API 호출이 과다하고(주문 × 3 decision type)
#   ②D+12 정산 성숙을 훨씬 넘긴 오래된 주문은 이미 패턴이 안정돼 재확인 가치가 낮다.
#   44일이면 성숙(D+12)에 32일 여유 — 성숙 직후 전이를 놓칠 창이 아니다.
SCAN_LOOKBACK_DAYS = 44

# periodType=SETTLE_CASEBYCASE_PAY_DATE에서만 의미를 갖는 3유형(공식 스펙). 전부 훑는 이유:
# N배송 2건이 지금 **세 유형 모두에서 0건**이라, 어디에 처음 나타나는지가 곧 정보다.
SETTLE_DECISION_TYPES: tuple[str, ...] = ("SETTLED", "UNSETTLED", "BEFORE_CANCEL")

# 클레임 = 반품/교환. 취소(cancelled)는 배송 회수가 없어 회수비 관측 대상이 아니다.
CLAIM_STATUSES: tuple[str, ...] = ("returned", "exchanged")

# 지금까지 관측 0건인 보상성 productOrderType — 뜨면 "네이버가 보상해줬다"의 직접 증거다.
COMPENSATION_TYPES = frozenset({"CONCESSION", "DEDUCTION_RESTORE", "PREFERENTIAL_COMMISSION"})

_DELIVERY_TYPE = "DELIVERY"


def is_membership_order(raw: Any) -> bool:
    """멤버십 회원 주문인가 — productOrder.deliveryDiscountAmount > 0.

    구조(라이브 실측 438건): deliveryFeeAmount는 항상 3,000이고 멤버십이면 네이버가 고객에게
    면제하며 그 금액을 deliveryDiscountAmount로 표시한다(비멤버십은 0).
    ★raw_data 파싱은 order_delivery SA(product_order_of)를 통해서만 한다 — 파싱 지점이 갈리면
      같은 주문에 대해 두 답이 나온다(order_delivery.py 상단 계약).
    판별 불가(파싱 실패·네이버 주문 아님·필드 부재)는 **False**(비멤버십)로 떨어진다:
    멤버십 표본이 아직 0건이라, 불확실한 건을 멤버십으로 올리면 첫 멤버십 알림이 오탐이 된다.
    """
    po = product_order_of(raw)
    if not isinstance(po, dict):
        return False
    amount = po.get("deliveryDiscountAmount")
    if amount is None:
        return False
    try:
        return Decimal(str(amount)) > 0
    except (ValueError, TypeError, ArithmeticError):
        return False


def collect_claim_orders(db, *, today: date, lookback_days: int = SCAN_LOOKBACK_DAYS) -> list[dict]:
    """스캔 창 안의 네이버 클레임 주문을 order_number 그레인으로 반환.

    orders는 주문라인 그레인(같은 order_number가 여러 행)이라 뭉친다 — 정산 API는 orderId
    단위 조회이므로 라인마다 부르면 같은 응답을 중복 호출한다.
    뭉칠 때의 규칙:
      - is_membership: 한 라인이라도 멤버십이면 멤버십(OR) — 멤버십 표본을 놓치지 않으려고.
      - delivery_attribute_type: 첫 non-NULL 값. 한 주문 안에서 배송방식이 갈리는 사례는
        관측된 적 없고, 갈린다면 그 자체가 신규 조합으로 올라온다.
      - order_status: returned를 exchanged보다 우선(반품이 회수비 관측의 본체).
    """
    since = today - timedelta(days=lookback_days)
    rows = (
        db.query(Order)
        .join(Channel, Channel.id == Order.channel_id)
        .filter(
            Channel.code == "NAVER",
            Order.status.in_(CLAIM_STATUSES),
            Order.order_date >= since,
        )
        .all()
    )

    merged: dict[str, dict] = {}
    for o in rows:
        key = o.order_number
        entry = merged.get(key)
        membership = is_membership_order(o.raw_data)
        if entry is None:
            merged[key] = {
                "order_number": key,
                "delivery_attribute_type": o.delivery_attribute_type,
                "is_membership": membership,
                "order_status": o.status,
            }
            continue
        entry["is_membership"] = entry["is_membership"] or membership
        if entry["delivery_attribute_type"] is None:
            entry["delivery_attribute_type"] = o.delivery_attribute_type
        if o.status == "returned":
            entry["order_status"] = "returned"
    return sorted(merged.values(), key=lambda e: e["order_number"])


def _combo(row: NaverClaimSettlementProbe | dict) -> tuple:
    """신규 판정 그레인 = (배송방식, 멤버십, productOrderType, settleDecisionType).

    금액·상품주문번호는 넣지 않는다 — 금액이 다를 때마다 알리면 매일 시끄러워 첫 출현이 묻힌다.
    """
    if isinstance(row, dict):
        return (
            row["delivery_attribute_type"],
            bool(row["is_membership"]),
            row["product_order_type"],
            row["settle_decision_type"],
        )
    return (
        row.delivery_attribute_type,
        bool(row.is_membership),
        row.product_order_type,
        row.settle_decision_type,
    )


def _known_combos(db) -> set[tuple]:
    """이번 실행 **이전**에 한 번이라도 기록된 조합 전체(과거 전체 이력).

    ★날짜로 자르지 않고 실행 시작 시점에 스냅샷을 뜨는 이유: 같은 날 두 번 돌려도 두 번
      알리면 안 된다(첫 실행이 이미 적재했으므로 두 번째엔 '이미 본 조합'이어야 한다).
      observed_date < today로 자르면 재실행마다 같은 알림이 반복된다.
    """
    return {
        (attr, bool(memb), pot, sdt)
        for attr, memb, pot, sdt in db.query(
            NaverClaimSettlementProbe.delivery_attribute_type,
            NaverClaimSettlementProbe.is_membership,
            NaverClaimSettlementProbe.product_order_type,
            NaverClaimSettlementProbe.settle_decision_type,
        ).distinct()
    }


def _highlight(combo: tuple) -> str | None:
    """강조 라벨 — 이 프로브를 만든 이유에 해당하는 사건만 별을 단다."""
    attr, membership, product_order_type, _decision = combo
    if attr == NBAESONG_ATTR and product_order_type == _DELIVERY_TYPE:
        return "★★★ N배송 반품/교환 배송비(DELIVERY) 최초 관측 — 회수비 실측 확보"
    if product_order_type in COMPENSATION_TYPES:
        return f"★★ 보상성 정산({product_order_type}) 최초 관측 — 네이버 보상 가설 증거"
    if attr == NBAESONG_ATTR and membership:
        return "★★ 멤버십 N배송 클레임 최초 관측"
    return None


def _format_slack(new_rows: list[dict], *, today: date) -> str:
    """신규 조합 알림 본문. 강조 건을 위로 올린다 — 중요한 것이 스크롤 아래 묻히면 안 된다."""
    ranked: list[tuple[int, tuple, dict]] = []
    for row in new_rows:
        combo = _combo(row)
        label = _highlight(combo)
        rank = 0 if (label or "").startswith("★★★") else (1 if label else 2)
        ranked.append((rank, combo, row))
    ranked.sort(key=lambda t: (t[0], str(t[1])))

    lines = [f"[N배송 반품 프로브 {today}] 처음 보는 정산 조합 {len(new_rows)}건"]
    for _rank, combo, row in ranked:
        attr, membership, product_order_type, decision = combo
        label = _highlight(combo)
        if label:
            lines.append(label)
        lines.append(
            f"- 배송={attr or 'UNKNOWN'} / 멤버십={'Y' if membership else 'N'} / "
            f"{product_order_type} / {decision} / settleType={row['settle_type'] or '-'} / "
            f"정산기준금액={row['pay_settle_amount']} "
            f"(주문 {row['order_number']}, 상품주문 {row['product_order_id'] or '-'})"
        )
    return "\n".join(lines)


def run_probe(db, client, *, today: date | None = None, webhook_url: str | None = None) -> dict:
    """클레임 주문 × 3 decision type 정산 조회 → 적재 → 신규 조합만 Slack.

    반환: {"orders": n, "observations": n, "inserted": n, "new_combos": [...], "notified": bool}
    """
    today = today or kst_today()
    orders = collect_claim_orders(db, today=today)
    known = _known_combos(db)  # ★적재 전 스냅샷(위 주석)

    observations: list[dict] = []
    for entry in orders:
        for decision in SETTLE_DECISION_TYPES:
            # 실패는 예외로 올라간다(client 계약) — 여기서 잡지 않는다.
            for e in client.fetch_case_settlement_by_order(entry["order_number"], decision):
                observations.append({
                    "order_number": entry["order_number"],
                    "product_order_id": e.get("product_order_id") or "",
                    "delivery_attribute_type": entry["delivery_attribute_type"],
                    "is_membership": entry["is_membership"],
                    "order_status": entry["order_status"],
                    "settle_decision_type": decision,
                    "product_order_type": e.get("product_order_type") or "",
                    "settle_type": e.get("settle_type") or "",
                    "pay_settle_amount": e.get("pay_settle_amount") or Decimal(0),
                    "settle_expect_amount": e.get("settle_expect_amount") or Decimal(0),
                    "pay_date": e.get("pay_date"),
                    "settle_expect_date": e.get("settle_expect_date"),
                })

    inserted = 0
    seen_keys: set[tuple] = set()
    new_by_combo: dict[tuple, dict] = {}
    for obs in observations:
        key = (
            obs["product_order_id"], obs["product_order_type"], obs["settle_type"],
            obs["settle_decision_type"], today,
        )
        combo = _combo(obs)
        if combo not in known and combo not in new_by_combo:
            new_by_combo[combo] = obs
        if key in seen_keys:
            continue   # 같은 실행 안의 중복(응답 중복) — UNIQUE 위반 전에 거른다
        seen_keys.add(key)
        exists = (
            db.query(NaverClaimSettlementProbe.id)
            .filter(
                NaverClaimSettlementProbe.product_order_id == obs["product_order_id"],
                NaverClaimSettlementProbe.product_order_type == obs["product_order_type"],
                NaverClaimSettlementProbe.settle_type == obs["settle_type"],
                NaverClaimSettlementProbe.settle_decision_type == obs["settle_decision_type"],
                NaverClaimSettlementProbe.observed_date == today,
            )
            .first()
        )
        if exists:
            continue   # 같은 날 재실행 — 멱등
        db.add(NaverClaimSettlementProbe(observed_date=today, **obs))
        inserted += 1
    db.commit()

    notified = False
    if new_by_combo:
        text = _format_slack(list(new_by_combo.values()), today=today)
        result = notify_text(text, webhook_url=webhook_url, log_label="N배송 반품 프로브")
        notified = bool(result.get("sent"))
        log.warning("[클레임정산프로브] 신규 조합 %d건: %s", len(new_by_combo), list(new_by_combo))
    else:
        # 신규 없으면 발송 생략(다른 잡들과 같은 관례) — 매일 오는 알림은 곧 무시되는 알림이다.
        log.info(
            "[클레임정산프로브] 주문 %d건·관측 %d행 적재 %d행 — 신규 조합 없음(발송 생략)",
            len(orders), len(observations), inserted,
        )

    return {
        "orders": len(orders),
        "observations": len(observations),
        "inserted": inserted,
        "new_combos": list(new_by_combo),
        "notified": notified,
    }
