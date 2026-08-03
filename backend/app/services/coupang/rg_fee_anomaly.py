# rg_fee_anomaly.py — SA3: RG 청구액 이상치 탐지 (트랙 RG-Fee S8, D-17)
# 단일 책임: (치수·무게, 주별 청구 배송/입출고비, 판매수량) → 이상치 플래그 + 근거 수치.
# ★스크리닝 도구지 확정 계산기 아님(D-5). 합포장으로 주별집계÷수량이 깔끔한 단가가 아닐 수 있어
#   플래그는 "사람 검토 신호"이지 과오청구 단정이 아니다. 근거 수치를 함께 반환해 Jino가 판단.
# 순수함수 — DB·외부호출 없음. SA1(분류)·SA2(floor)만 호출. fixture 테스트로 잠금(D-12).
#
# ★단가 판정의 전제(2026-08-03, 오탐 4건 규명 후 도입 — LESSONS #106):
#   단가 = 청구액 ÷ 주문수다. **분자와 분모가 같은 창에서 오지 않으면 단가는 거짓이 된다.**
#   실사고: 아이패드미니 필름(91313543029)은 정산주기 4개에 각 2,025원이 청구됐는데 RG 주문
#   수집이 2026-06-03에 시작해 4월 주문이 DB에 없었다 → 감사가 합계 8,100원을 주문 2건으로
#   나눠 4,050원(=대형1 정합, 극소형 최소금액의 3.0배)을 만들고 "실측과 청구가 어긋났다"는
#   가장 강한 플래그로 올렸다. 같은 원인으로 WING1 3건까지 총 4건이 전부 오탐이었다.
#   → 그래서 이 모듈의 판정 단위는 **정산주기 1개**이고(주기 밖 청구액을 절대 섞지 않는다),
#     주문이 대응되지 않은 주기는 **판정에서 제외**하되 그 사실(periods_unmatched)을 표면화한다.
#     대응 주기가 하나도 없으면 판정하지 않고 unit_unknown으로 강등한다 — 조용한 오탐은
#     조용한 침묵과 같은 값으로 신뢰를 깎기 때문이다.
#   ★남는 한계(해결 못 함, 단정 금지): 주기 안에서 **일부** 주문만 수집된 경우는 여전히 단가가
#     부풀 수 있다. 청구된 주문수를 알 방법이 없고, 금액÷알려진단가로 역산하는 것은 쿠팡 계산기
#     복제(D-17 금지)다. 그래서 주기별 판정은 이 오차를 **국소화**할 뿐 제거하지 못한다.
from __future__ import annotations

from app.services.coupang.rg_fee_reference import (
    expected_fee_floor,
    implied_size_from_delivery,
)
from app.services.coupang.rg_size_classifier import SIZE_TYPES, classify_size_type

# 배송 단가가 우리 사이즈 최소금액의 이 배수 이상이면 "검토" 플래그.
# floor는 최소치라 1~1.x배는 카테고리·판매가 변동으로 정상일 수 있음(오탐). 2배+는 gross outlier.
_DELIVERY_OVERCHARGE_MULTIPLE = 2.0

# 이상치 플래그(=검토 신호)만 모은 집합. 커버리지 사실 표기는 여기 들어가지 않는다 —
# 커버리지는 periods_total/judged/unmatched 필드로 표면화하고, flags는 "이상하다"만 뜻하게 둔다.
ANOMALY_FLAGS = frozenset({
    "missing_dims", "oversize", "unit_unknown", "below_floor",
    "size_mismatch_high", "measured_vs_billed_mismatch", "billed_size_vs_amount_mismatch",
})

# 사이즈 출처 → 불일치 플래그 이름. 신호의 **세기**가 출처마다 다르기 때문에 이름을 나눈다.
#   registered_dims   우리 등록 치수 기준 — 등록이 틀렸을 가능성이 남아 가장 약하다.
#   coupang_measured  물류센터 실측 기준 — 등록 오류 설명이 제거돼 더 강하다.
#   settlement_billed 정산서가 **청구에 썼다고 적은** 등급 기준 — 가장 강하다. 이건 추론이
#     아니라 정산서 내부의 모순이다("극소형으로 청구했다"면서 큰 등급 금액을 받았다).
_MISMATCH_FLAG_BY_SOURCE = {
    "registered_dims": "size_mismatch_high",
    "coupang_measured": "measured_vs_billed_mismatch",
    "settlement_billed": "billed_size_vs_amount_mismatch",
}


def detect_fee_anomalies(
    width_mm: int | None,
    length_mm: int | None,
    height_mm: int | None,
    weight_g: int | None,
    *,
    delivery_amount: float | None,
    warehousing_amount: float | None,
    quantity: int | None,
    order_count: int | None = None,
    coupang_size_type: str | None = None,
    size_source: str | None = None,
) -> dict:
    """이상치 플래그 + 근거 수치 반환.

    정규화(codex P2): 배송비는 합포장 시 **주문당 1회** → order_count로 나눔(주문당 단가).
      입출고비는 **수량당** → quantity로 나눔. order_count 미지정 시 quantity로 폴백.
    배송 floor·입출고 floor 모두 각 부과단위(주문당·수량당)와 정합.

    플래그:
      missing_dims        치수 미측정 → 감사 불가
      oversize            '초과'(입고 불가 사이즈) 분류 — 데이터 점검
      unit_unknown        수량 없음/0 → 단가 정규화 불가(과오청구 단정 금지)
      below_floor         단가 < 우리 사이즈 최소금액 → 데이터/사이즈 과대등록 의심
      size_mismatch_high  배송 주문당 단가가 우리 사이즈 최소금액의 2배+ AND 큰 등급에 정합
                          → 과대측정/과오청구 의심(정확 금액표로 확인 필요). floor는 최소치라
                          1~1.x배는 정상 가능 → 배수 임계로 오탐 억제.
                          **실측 미확보(등록 치수 기준)일 때의 이름이다.**
      measured_vs_billed_mismatch
                          같은 조건이되 기준이 **쿠팡 실측 사이즈**일 때. 과금 기준인 물류센터
                          실측값 자체와 청구가 어긋났고 '등록 치수가 틀렸다'는 설명이 이미
                          제거됐으므로 size_mismatch_high보다 **강한 신호**다(2026-08-03 신설).
    """
    # 쿠팡 실측 사이즈가 있으면 우선 사용(과금 기준). 없으면 등록 치수로 분류(폴백).
    size_type = coupang_size_type or classify_size_type(width_mm, length_mm, height_mm, weight_g)
    flags: list[str] = []
    result: dict = {
        "size_type": size_type,
        # ★size_source는 호출자가 알려준다(S9). 종전엔 coupang_size_type 유무로만 갈랐는데
        #   이제 출처가 셋(정산서 기재·물류센터 실측·등록 치수)이라 유무로는 구분되지 않는다.
        "size_source": size_source or (
            "coupang_measured" if coupang_size_type else "registered_dims"),
        "per_unit_delivery": None,
        "per_unit_warehousing": None,
        "floor": expected_fee_floor(size_type),
        "implied_size_delivery": None,
        "flags": flags,
    }

    if size_type is None:
        flags.append("missing_dims")
        return result
    if size_type == "초과":
        flags.append("oversize")
        return result

    if not quantity or quantity <= 0:
        flags.append("unit_unknown")
        return result

    floor = expected_fee_floor(size_type) or {}
    our_idx = SIZE_TYPES.index(size_type)
    # 배송비는 주문당(합포장) → 주문수로 정규화. 미지정 시 수량 폴백(보수적).
    delivery_divisor = order_count if order_count and order_count > 0 else quantity

    if delivery_amount is not None:
        per_order_delivery = delivery_amount / delivery_divisor
        result["per_unit_delivery"] = round(per_order_delivery, 2)
        implied = implied_size_from_delivery(per_order_delivery)
        result["implied_size_delivery"] = implied
        floor_delivery = floor.get("delivery")
        if floor_delivery is not None and per_order_delivery < floor_delivery:
            flags.append("below_floor")
        elif (
            implied is not None
            and SIZE_TYPES.index(implied) > our_idx
            and floor_delivery is not None
            and per_order_delivery >= floor_delivery * _DELIVERY_OVERCHARGE_MULTIPLE
        ):
            # 배송비가 우리 등급보다 큰 사이즈에 정합 + 2배+ → 검토 신호.
            # ★어느 사이즈를 기준으로 어긋났느냐에 따라 신호의 **세기**가 다르다(2026-08-03):
            #   · 실측 미확보(registered_dims 기준) → size_mismatch_high.
            #     우리 등록 치수가 틀렸을 가능성이 남아 있어 상대적으로 약한 신호다.
            #   · 실측 확보(coupang_measured 기준) → measured_vs_billed_mismatch.
            #     **더 강한 신호다.** 과금 기준인 물류센터 실측값 자체와 청구가 어긋났고,
            #     등록 치수 오탐이라는 설명이 이미 제거됐기 때문이다.
            # ★종전엔 `coupang_size_type is None` 조건으로 후자를 **통째로 스킵**했다
            #   ("실측값이 과금 기준이므로 추정 불필요"). 그 논리는 "실측 사이즈 = 청구 사이즈"를
            #   전제하는데, 라이브가 그 전제를 반증했다 — 옵션 91313543029(아이패드미니 필름,
            #   WING2): 실측 '극소형'인데 주문당 배송비 4,050원(극소형 최소 1,350원의 3.0배)이고
            #   청구 함의 사이즈는 '대형1'. 실측이 들어온 것이 불일치를 해소한 게 아니라
            #   **확인해 줬는데** 코드는 그걸 "볼 필요 없음"으로 읽고 신호를 껐다.
            #   그 결과 2026-06-15에 올라온 플래그가 숫자 하나 안 바뀐 채 조용히 사라졌다.
            #   · 정산서 기재(settlement_billed 기준) → billed_size_vs_amount_mismatch.
            #     **가장 강한 신호다(2026-08-03 S9).** 사이즈도 추론이 아니고 분모도 추론이
            #     아니므로 "우리 데이터가 틀렸다"는 설명이 전부 제거된다.
            flags.append(_MISMATCH_FLAG_BY_SOURCE.get(
                result["size_source"],
                "measured_vs_billed_mismatch" if coupang_size_type is not None
                else "size_mismatch_high",
            ))

    if warehousing_amount is not None:
        per_unit_wh = warehousing_amount / quantity
        result["per_unit_warehousing"] = round(per_unit_wh, 2)
        floor_wh = floor.get("warehousing")
        if floor_wh is not None and per_unit_wh < floor_wh and "below_floor" not in flags:
            flags.append("below_floor")

    return result


def detect_fee_anomalies_by_period(
    width_mm: int | None,
    length_mm: int | None,
    height_mm: int | None,
    weight_g: int | None,
    *,
    periods: list[dict],
    coupang_size_type: str | None = None,
    size_source: str | None = None,
) -> dict:
    """옵션 1개를 정산주기 단위로 **집계해** 판정하고, 주기별 근거를 함께 낸다(2026-08-03).

    periods 각 항목: {"date_from", "date_to", "delivery", "warehousing",
                      "order_count", "quantity"}  ← 그 주기 안에서만 집계된 값

    ★두 층을 분리한다 — **판정은 판정주기 전체 합산, 표시는 주기별.**
      · 주문이 대응되지 않은 주기(order_count/quantity 0)는 판정에서 **제외**한다. 주기 밖
        주문으로 나누면 단가가 정수배로 부풀기 때문이다(모듈 상단 실사고 4건).
      · 그러나 판정 자체를 주기 단위로 쪼개지는 **않는다.** 라이브가 그 설계를 반증했다:
        주기 **안에서도** 주문이 부분만 수집된다(같은 옵션 95570603482가 `1,975원/1주문`과
        `3,950원/1주문`을 함께 갖고, 95598292078은 `12,600원/7주문`과 `12,600원/3주문`을 함께
        갖는다 — 금액은 안정된 단가의 정수배인데 분모가 불안정하다). 주기별 판정은 표본을
        주기 1개로 줄여 **그 불안정을 증폭**했다: WING1 measured_vs_billed_mismatch 3→20건.
        표본을 키울수록 결손·과다가 서로 상쇄되므로 **집계 판정이 통계적으로 옳다.**
      · 주기별 신호는 버리지 않고 period_detail·periods_flagged로 **표면화**한다. 집계가
        희석할 수 있는 국소 이상치를 사람이 볼 수 있어야 하기 때문이다.

    반환(기존 키 유지 + 커버리지 필드 추가):
      per_unit_delivery/warehousing  판정 주기들만의 단가(Σ판정청구 ÷ Σ판정주문). 청구총액을
                                     주문수로 나눈 값이 **아니다** — 그 나눗셈이 오탐의 원인이었다.
      judged_delivery/warehousing    위 단가의 분자(판정 주기 청구액 합). charged_*와 다를 수 있다.
      flags                          집계 판정 결과. 판정 주기 0개면 ['unit_unknown'].
      periods_total/judged/unmatched 커버리지 표면화. unmatched>0이면 단가는 부분 표본이다.
      periods_flagged                주기 단위로 보면 이상한 주기 수(집계 판정과 별개의 참고값).
      period_detail                  주기별 근거 행(judged·주기 단가·주기 플래그) — 대조 원자료.
    """
    size_type = coupang_size_type or classify_size_type(width_mm, length_mm, height_mm, weight_g)
    base: dict = {
        "size_type": size_type,
        "size_source": size_source or (
            "coupang_measured" if coupang_size_type else "registered_dims"),
        "per_unit_delivery": None,
        "per_unit_warehousing": None,
        "judged_delivery": None,
        "judged_warehousing": None,
        "floor": expected_fee_floor(size_type),
        "implied_size_delivery": None,
        "flags": [],
        "periods_total": len(periods),
        "periods_judged": 0,
        "periods_unmatched": len(periods),
        "periods_flagged": 0,
        "period_detail": [],
    }

    # 치수 미측정·입고불가는 주기와 무관한 판정 → 조기 종료(단일 주기 판정기와 동일 의미).
    if size_type is None:
        base["flags"] = ["missing_dims"]
        return base
    if size_type == "초과":
        base["flags"] = ["oversize"]
        return base

    judged = 0
    periods_flagged = 0
    sum_dlv = sum_wh = 0.0
    sum_orders = sum_qty = 0
    has_dlv = has_wh = False
    detail: list[dict] = []

    for p in periods:
        oc = p.get("order_count") or 0
        qy = p.get("quantity") or 0
        row = {
            "date_from": p.get("date_from"),
            "date_to": p.get("date_to"),
            "delivery": p.get("delivery"),
            "warehousing": p.get("warehousing"),
            "order_count": oc,
            "quantity": qy,
            "judged": False,
            "per_unit_delivery": None,
            "per_unit_warehousing": None,
            "implied_size_delivery": None,
            "flags": [],
        }
        if oc <= 0 or qy <= 0:
            # 주문 미대응 주기 — 판정 제외(ⓑ). 청구액은 charged_*에 남아 금액은 안 사라진다.
            detail.append(row)
            continue

        # 주기 단위 판정은 **참고 표시용**이다(판정은 아래 집계로 한다). 주기 안에서도 주문이
        # 부분 수집되기 때문에 이 값 하나로 이상 여부를 단정하면 오탐이 3→20건으로 늘었다.
        one = detect_fee_anomalies(
            width_mm, length_mm, height_mm, weight_g,
            delivery_amount=p.get("delivery"), warehousing_amount=p.get("warehousing"),
            quantity=qy, order_count=oc, coupang_size_type=coupang_size_type,
            size_source=size_source,
        )
        judged += 1
        row.update({
            "judged": True,
            "per_unit_delivery": one["per_unit_delivery"],
            "per_unit_warehousing": one["per_unit_warehousing"],
            "implied_size_delivery": one["implied_size_delivery"],
            "flags": list(one["flags"]),
        })
        detail.append(row)
        if one["flags"]:
            periods_flagged += 1

        if p.get("delivery") is not None:
            sum_dlv += float(p["delivery"])
            has_dlv = True
        if p.get("warehousing") is not None:
            sum_wh += float(p["warehousing"])
            has_wh = True
        sum_orders += oc
        sum_qty += qy

    base["period_detail"] = detail
    base["periods_judged"] = judged
    base["periods_unmatched"] = len(periods) - judged
    base["periods_flagged"] = periods_flagged

    if judged == 0:
        # 대응 주기가 하나도 없다 → 단가를 만들 근거가 없다. 판정 보류(강등).
        base["flags"] = ["unit_unknown"]
        return base

    # ★판정 = 판정주기 전체 합산 1회. 단일 주기가 아니라 모아서 봐야 분모 결손·과다가 상쇄된다.
    agg = detect_fee_anomalies(
        width_mm, length_mm, height_mm, weight_g,
        delivery_amount=round(sum_dlv, 2) if has_dlv else None,
        warehousing_amount=round(sum_wh, 2) if has_wh else None,
        quantity=sum_qty, order_count=sum_orders,
        coupang_size_type=coupang_size_type, size_source=size_source,
    )
    if has_dlv:
        base["judged_delivery"] = round(sum_dlv, 2)
    if has_wh:
        base["judged_warehousing"] = round(sum_wh, 2)
    base["per_unit_delivery"] = agg["per_unit_delivery"]
    base["per_unit_warehousing"] = agg["per_unit_warehousing"]
    base["implied_size_delivery"] = agg["implied_size_delivery"]
    base["flags"] = list(agg["flags"])
    return base
