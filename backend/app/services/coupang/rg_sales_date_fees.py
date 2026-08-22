# rg_sales_date_fees.py — RG 정산공제를 «판매일 축»으로 낸다 (계약 CONTRACT_rg_sales_date_axis).
#
# 왜 이 모듈이 생겼나 (2026-08-22 라이브 실측):
#   `profit_calculator.get_rg_total_by_account`는 정산 원장을 **정산 인식일 창 겹침**으로 읽는다.
#   정산 주기는 «한 주 통짜»라 안분이 없고, 그래서 그 주기를 덮는 **어느 하루를 물어도 같은 값**이
#   나온다. 라이브 재현: 08-17·18·19·20·21 다섯 날이 전부 `153,058원`이었다.
#   08-21은 그 값이 그날 매출(187,120)의 **81.8%**라 순이익 부호를 뒤집었고(−32,673원),
#   08-18은 그날 매출의 **625%**였다.
#   ⇒ 네 항(매출·원가·광고비·정산공제) 중 셋은 이미 판매일/집행일 축인데 **이 한 항만** 아니었다.
#   Jino 원문(2026-08-22): *"어제 어떤 제품이 몇개가 팔리고 그 판매분의 정산공제, 원가, 세금,
#   기타비용등을 빼고 남는 이익이 있잖아. 다른 판매와 같이 2P도 그걸 보자는거지"*
#
# ★이 모듈이 하는 일은 «재계산»이 아니라 «재귀속»이다. 쿠팡 금액표를 복제하지 않는다
#   (ref 17 §S8: 「쿠팡 금액표 완전 복제 = 잘못된 설계」). 쿠팡이 청구한 실측값에서 **단가와
#   요율만** 뽑아 그날 판 수량·매출에 곱한다. 그래서 총액 보존을 따로 검사할 수 있다(§4 ⓒ).
#
# 세 항의 축이 서로 다르고, 그 차이가 이 모듈의 전부다:
#   ① 물류비(delivery·warehousing) = **옵션별 정액 단가 × 그날 판매수량**
#      단가 출처 = 정산 엑셀 옵션 row의 `amount / billed_quantity`(추론 아님 — 쿠팡이 준 수량).
#   ② 판매수수료(sale_fee)          = **그날 매출 × 요율**
#      옵션 단위 `sale_fee` row가 **전 계정·전 기간 0건**이라(2026-08-22 실측) 계정 단위 역산뿐이다.
#   ③ 보관비·반품비                  = **판매일에 안 붙인다.** 계정 기간비용으로 일할 배분한다.
#      근거(계약 §8-5): WING2의 6개 주기에서 **매출이 정확히 0인데 storage는 1,988~7,259원 발생**했고
#      같은 주기 sale_fee·delivery·warehousing은 전부 0이었다 — 재고 보유 비용이라는 실증이다.
#      반품비도 같다(반품일에 붙지 판매일에 안 붙는다). 옵션·날짜 grain이 원장에 없다.
#
# ★못 덮는 부분은 0으로도 통짜로도 채우지 않는다(계약 §2 판단기준 2).
#   단가를 모르는 옵션의 그날 매출은 `unmapped_revenue`로 자백하고, 커버리지가 임계 미만이면
#   호출부가 순이익을 «내지 않는다». 0으로 채우면 순이익이 부풀고 통짜로 채우면 부호가 뒤집힌다 —
#   **둘 다 조용히 틀린다.**
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    CoupangRgSettlementFee,
    CoupangVendorItemSalesDaily,
    CoupangVendorSummaryDaily,
)
from app.services.coupang.rg_net_revenue import REGISTRATION_TYPE_RG

log = logging.getLogger(__name__)

ZERO = Decimal("0")

#: 옵션 row는 **VAT前(할인적용가 A−B)**, 계정 row는 VAT後다(models.py §8-1 · intelligence.py:606-612).
#: 단가는 옵션 row에서 오므로 계정 축과 같은 자리에 세우려면 gross-up 해야 한다.
#: `product_pnl._RG_VAT_GROSSUP`과 **같은 값·같은 이유**다 — 둘이 갈라지면 보존식이 터진다.
VAT_GROSSUP = Decimal("1.1")

#: 물류비 단가를 아는 매출의 비율 하한. 이 아래면 호출부가 순이익을 내지 않는다(계약 §4 ⓔ).
#: `rg_channel_pnl._COST_COVERAGE_MIN`(원가 게이트)과 **같은 계열·같은 기본값**으로 둔다 —
#: 두 게이트는 다른 것을 재지만(원가 / 물류비) 「모르면 안 낸다」는 같은 규율이다.
FEE_COVERAGE_MIN = Decimal(os.getenv("RG_FEE_COVERAGE_MIN") or "0.95")

#: 요율 실측에 쓰는 **완결** 정산주기 수. 하나만 쓰면 그 주기의 믹스에 통째로 끌려가고,
#: 너무 많이 쓰면 카테고리 믹스가 바뀐 옛 요율이 오늘에 섞인다(실측: 8.42%→10.23%로 이동 중).
RATE_CYCLES = int(os.getenv("RG_FEE_RATE_CYCLES") or "3")

#: 순이익에서 차감하지 «않는» fee_type. `profit_calculator._RG_FEE_TYPES_NOT_DEDUCTED`와 같다 —
#: 정산 `ad_sales`는 별개 비용이 아니라 광고센터 PA 광고비의 «공제»이고, PA는 이미 `ad_spend`로
#: 한 번 차감된다(D-CPP-43). 여기서 또 빼면 이중계상이다.
FEE_TYPES_NOT_DEDUCTED: frozenset[str] = frozenset({"ad_sales"})

#: 판매일에 귀속되는 fee_type — 그날 «판 것»에 붙는 비용.
SALES_DATE_FEE_TYPES: frozenset[str] = frozenset({"delivery", "warehousing"})

#: 계정 기간비용 — 판매일에 안 붙는다(위 ③).
PERIOD_FEE_TYPES: frozenset[str] = frozenset({"storage", "return_shipping", "return_handling"})

BASIS_SETTLED_RATE = "settled_rate"    # 최근 완결 주기에서 실측한 요율
BASIS_RATE_UNKNOWN = "rate_unknown"    # 실측할 완결 주기가 없다 — **추정하지 않는다**


def unit_logistics_prices(
    db: Session, account_key: str | None = None
) -> dict[tuple[str, str], dict]:
    """(account_key, vendor_item_id) → 건당 물류비 단가(VAT前) + 그 근거 주기.

    반환 {(ak, vid): {"delivery": D, "warehousing": D, "cycle_from": date, "cycle_to": date}}

    출처는 정산 엑셀 옵션 row의 `amount / billed_quantity`뿐이다 — 쿠팡이 **주문ID·판매수량을
    그대로 준다**(models.py `billed_quantity` 주석: 「같은 파일·같은 basis에서 읽으면 조인도
    추론도 없다」). 우리가 사이즈 등급에서 역산하거나 판매수량으로 나누지 않는다.

    ★`billed_quantity`는 **최근 3주기(08-03~ 이후)에만** 채워져 있다(2026-08-22 실측:
      delivery 옵션 row 357건 중 41건). 그 이전 주기는 NULL이라 애초에 후보가 아니다.
      그러니 이 사전은 «최근 단가표»이지 역사 전체의 단가표가 아니다 — 옛 창을 물으면
      커버리지가 떨어지고, 그건 자백해야 할 사실이지 채워 넣을 구멍이 아니다.
    ★옵션·fee_type별로 **가장 최근 주기 하나**를 쓴다. 쿠팡이 사이즈 재측정으로 단가를 바꾸면
      다음 정산부터 자동 반영된다(3P `option_fee_rates`의 「최신 정산 행을 쓴다」와 같은 규율).
    """
    q = (
        db.query(
            CoupangRgSettlementFee.account_key,
            CoupangRgSettlementFee.vendor_item_id,
            CoupangRgSettlementFee.fee_type,
            CoupangRgSettlementFee.amount,
            CoupangRgSettlementFee.billed_quantity,
            CoupangRgSettlementFee.recognition_date_from,
            CoupangRgSettlementFee.recognition_date_to,
        )
        .filter(
            CoupangRgSettlementFee.vendor_item_id != "",
            CoupangRgSettlementFee.fee_type.in_(tuple(SALES_DATE_FEE_TYPES)),
            CoupangRgSettlementFee.billed_quantity.isnot(None),
            CoupangRgSettlementFee.billed_quantity > 0,
        )
    )
    if account_key is not None:
        q = q.filter(CoupangRgSettlementFee.account_key == account_key)

    # 최신 우선으로 훑고 «처음 본 것만» 채운다 — window 함수 없이 결정적으로 고른다
    # (SQLite/Postgres 둘 다 같은 결과여야 한다).
    out: dict[tuple[str, str], dict] = {}
    for ak, vid, ft, amount, bq, rf, rt in sorted(
        q.all(), key=lambda r: (str(r[0]), str(r[1]), str(r[2]), r[5]), reverse=True
    ):
        key = (str(ak), str(vid))
        entry = out.setdefault(key, {})
        if ft in entry:
            continue          # 더 최근 주기를 이미 잡았다
        entry[ft] = Decimal(str(amount or 0)) / Decimal(str(bq))
        # 근거 주기는 **가장 최근에 본 것**을 남긴다(두 fee_type의 주기가 다를 수 있다).
        if "cycle_from" not in entry or rf > entry["cycle_from"]:
            entry["cycle_from"], entry["cycle_to"] = rf, rt
    return out


def _completed_cycles(db: Session, account_key: str, asof: date, limit: int) -> list[dict]:
    """요율 실측에 쓸 **완결** 정산주기 — 최근 것부터 `limit`개.

    ★완결 = `recognition_date_to < asof`. 진행 중 주기를 섞으면 안 되는 이유는 실측이 말한다:
      08-17~23(진행 중) 요율이 **13.65%**로 튄다 — 분모(그 주기의 매출)가 아직 안 찼기 때문이다.
      완결 주기 5개는 8.42~10.23% 안에 있다.
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.recognition_date_from,
            CoupangRgSettlementFee.recognition_date_to,
            CoupangRgSettlementFee.amount,
        )
        .filter(
            CoupangRgSettlementFee.account_key == account_key,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type == "sale_fee",
            CoupangRgSettlementFee.recognition_date_to < asof,
        )
        .order_by(CoupangRgSettlementFee.recognition_date_from.desc())
        .limit(limit)
        .all()
    )
    return [{"from": f, "to": t, "sale_fee": Decimal(str(a or 0))} for f, t, a in rows]


def sale_fee_rate(db: Session, account_key: str, asof: date) -> dict:
    """판매수수료 요율(VAT 포함) — 최근 완결 주기에서 **실측**하고 근거를 같이 낸다.

    반환 {"rate": D|None, "basis": str, "cycles": [(from,to)], "sale_fee": D, "gmv": D}

    ★왜 요율을 «추정»할 수밖에 없나 (계약 §8-4, 네 가설 중 둘 배제):
      - 3P가 쓰는 `coupang_revenue_fee`(옵션별 실측 요율)에 **RG 옵션은 0건**이다. 그 테이블은 3P 전용.
      - 옵션 단위 `sale_fee` 정산 row도 0건이다.
      ⇒ 계정 단위 역산 외에 길이 없다. 그러므로 `basis`를 행에 싣는 것은 선택이 아니라 **필수**다.
    ★**단일 상수로 박지 않는다.** 완결 5주기에서 8.42→10.23%로 움직였고(카테고리 믹스와 시점이
      겹친다 — 인과는 확인 안 됨), 상수를 박으면 그 이동을 영원히 못 따라간다.
    ★못 재면 **모른다고 한다**(`rate=None`, basis=`rate_unknown`). 3P처럼 기본 요율로 폴백하지
      않는 이유: 3P의 7.8%는 채널 시드값이라는 근거가 있지만 RG엔 그런 값이 없다. 없는 근거를
      만들어 내는 대신 호출부가 순이익을 안 내게 한다(계약 §3 금지선: 추정으로 채우고 단정 금지).

    분모는 **요약축 net GMV**다(옵션축이 아니라) — 정산 sale_fee는 계정 전체에 붙고, 요약축이
    창 커버리지가 길다. 두 축의 등가는 147 계정-일 전건 실측됐다(D-CPP-49).
    """
    cycles = _completed_cycles(db, account_key, asof, RATE_CYCLES)
    num = ZERO
    den = ZERO
    used: list[tuple[date, date]] = []
    for c in cycles:
        gmv = db.query(
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorSummaryDaily.gmv), 0)
        ).filter(
            CoupangVendorSummaryDaily.account_key == account_key,
            CoupangVendorSummaryDaily.registration_type == REGISTRATION_TYPE_RG,
            CoupangVendorSummaryDaily.summary_date >= c["from"],
            CoupangVendorSummaryDaily.summary_date <= c["to"],
        ).scalar()
        gmv = Decimal(str(gmv or 0))
        if gmv <= 0:
            # 매출 0인 주기는 요율의 근거가 될 수 없다(분모 0). 주기 자체는 정상이다 —
            # WING2엔 그런 주기가 흔하다(정산은 오는데 그 주에 판 게 없다).
            continue
        num += c["sale_fee"]
        den += gmv
        used.append((c["from"], c["to"]))
    if den <= 0:
        return {"rate": None, "basis": BASIS_RATE_UNKNOWN, "cycles": [],
                "sale_fee": ZERO, "gmv": ZERO}
    return {"rate": num / den, "basis": BASIS_SETTLED_RATE, "cycles": used,
            "sale_fee": num, "gmv": den}


def period_fees(db: Session, account_key: str, date_from: date, date_to: date) -> Decimal:
    """보관비·반품비 — 계정 기간비용을 창에 **일할 배분**한다(판매일 귀속 금지, 계약 §8-5).

    주기가 창에 걸치면 겹친 일수만큼만 가져온다: `amount × 겹친일수 / 주기일수`.
    ★이건 「그날 판 것에 붙는 비용」이 아니라 「그날 재고를 갖고 있던 값」이다. 판매일에 붙이면
      매출이 0인 날의 보관비가 사라지는데(WING2에서 실제로 그런 주기가 6개 있다) 그 비용은
      **실제로 청구된다.** 조용히 사라지면 §4 ⓒ 보존식이 터진다.
    """
    rows = (
        db.query(
            CoupangRgSettlementFee.recognition_date_from,
            CoupangRgSettlementFee.recognition_date_to,
            sqlfunc.sum(CoupangRgSettlementFee.amount),
        )
        .filter(
            CoupangRgSettlementFee.account_key == account_key,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type.in_(tuple(PERIOD_FEE_TYPES)),
            CoupangRgSettlementFee.recognition_date_from <= date_to,
            CoupangRgSettlementFee.recognition_date_to >= date_from,
        )
        .group_by(
            CoupangRgSettlementFee.recognition_date_from,
            CoupangRgSettlementFee.recognition_date_to,
        )
        .all()
    )
    total = ZERO
    for rf, rt, amount in rows:
        cycle_days = (rt - rf).days + 1
        if cycle_days <= 0:  # pragma: no cover — 원장이 깨진 경우의 방어
            continue
        overlap = (min(rt, date_to) - max(rf, date_from)).days + 1
        if overlap <= 0:  # pragma: no cover — overlap 필터가 이미 걸렀다
            continue
        total += Decimal(str(amount or 0)) * Decimal(overlap) / Decimal(cycle_days)
    return total


def ledger_total(db: Session, account_key: str, date_from: date, date_to: date) -> Decimal:
    """같은 창의 **정산 원장 실청구액**(광고 제외) — 보존식의 대조 기준(계약 §4 ⓒ).

    `profit_calculator.get_rg_total_by_account`와 같은 필터·같은 뜻이다. 여기서 다시 쓰는 것은
    이 모듈이 «자기 값을 원장과 대조»하는 데 그 값이 필요한데, 지연 임포트로 순환을 만들면
    이 모듈이 대시보드 사정을 알게 되기 때문이다.
    """
    total = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(CoupangRgSettlementFee.amount), 0))
        .filter(
            CoupangRgSettlementFee.account_key == account_key,
            CoupangRgSettlementFee.vendor_item_id == "",
            CoupangRgSettlementFee.fee_type.notin_(tuple(FEE_TYPES_NOT_DEDUCTED)),
            CoupangRgSettlementFee.recognition_date_from <= date_to,
            CoupangRgSettlementFee.recognition_date_to >= date_from,
        )
        .scalar()
    )
    return Decimal(str(total or 0))


def _latest_completed_cycle(db: Session, account_key: str, asof: date) -> tuple[date, date] | None:
    cycles = _completed_cycles(db, account_key, asof, 1)
    return (cycles[0]["from"], cycles[0]["to"]) if cycles else None


def sales_date_fees(
    db: Session,
    account_key: str,
    date_from: date,
    date_to: date,
    asof: date | None = None,
    reconcile: bool = True,
    revenue_reference: Decimal | None = None,
) -> dict:
    """[from, to]에 **판매된 것**에 붙는 정산공제 — 이 모듈의 본체.

    반환:
      total               판매일 축 정산공제 합계(VAT後) — 순이익에서 뺄 값
      logistics/sale_fee/period  세 항의 내역
      rate/rate_basis/rate_cycles 요율과 그 근거
      coverage            단가를 아는 매출의 비율(0~1) — None이면 잴 매출이 아예 없다
      unmapped_revenue    이 방식이 «비용을 못 붙인» 매출 — **0으로 안 채운 몫**

    ★`revenue_reference` = 호출부가 화면에 싣는 매출(= 요약축). 주면 **커버리지의 분모가 그것**이 된다.
      왜 필요한가(적대 리뷰 1R P1-1): 이 함수의 비용은 전부 **옵션축**에서 나오는데 행이 표시하는
      매출은 **요약축**이다. 두 축이 어긋난 창에서는 — 옵션축 페처가 `vi_days` 롤링이라 창 앞쪽이
      비는 것이 실제 조건이다 — 그 차액의 수수료·물류비가 **0으로 채워지는데**, 옵션축 «안에서의»
      비율만 보면 커버리지는 100%가 나온다. 결손을 원리적으로 못 보는 자다.
      ⇒ 분모를 «화면이 말하는 매출»로 세우면 그 결손이 곧바로 커버리지 하락으로 나타나고,
        §4 ⓔ 게이트가 발동한다. 안 주면 종전대로 옵션축 안에서만 잰다(하위호환).
      by_option           {vid: 판매일 축 귀속액(VAT後)} — 상품손익 귀속용
      reconciliation      최근 완결 주기에서 Σ(이 방식) vs 실청구 (계약 §4 ⓒ). 못 재면 None

    ★창 합계가 곧 일별 합계다 — 물류비(단가×수량)도 수수료(매출×요율)도 **선형**이라 날짜별로
      쪼갠 뒤 더한 것과 창 전체로 한 번 곱한 것이 같다. 그래서 날짜 루프를 돌지 않는다.
      기간비용만 일할이라 창 단위로 따로 센다.
    """
    asof = asof or date.today()
    units = unit_logistics_prices(db, account_key)
    rate_info = sale_fee_rate(db, account_key, asof)
    rate = rate_info["rate"]

    rows = (
        db.query(
            CoupangVendorItemSalesDaily.vendor_item_id,
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.units_sold), 0),
            sqlfunc.coalesce(sqlfunc.sum(CoupangVendorItemSalesDaily.gmv), 0),
        )
        .filter(
            CoupangVendorItemSalesDaily.account_key == account_key,
            CoupangVendorItemSalesDaily.registration_type == REGISTRATION_TYPE_RG,
            CoupangVendorItemSalesDaily.sale_date >= date_from,
            CoupangVendorItemSalesDaily.sale_date <= date_to,
        )
        .group_by(CoupangVendorItemSalesDaily.vendor_item_id)
        .all()
    )

    logistics = ZERO
    revenue_total = ZERO
    revenue_priced = ZERO
    unmapped_revenue = ZERO
    by_option: dict[str, Decimal] = {}

    for vid, qty, gmv in rows:
        vid = str(vid)
        qty = Decimal(str(qty or 0))
        gmv = Decimal(str(gmv or 0))
        revenue_total += gmv
        price = units.get((account_key, vid))
        if price is None:
            # 단가를 모른다 → 0으로 채우지 않는다. 그 매출을 자백한다.
            # ★매출이 0인 옵션(그 창에 안 팔린 옵션)은 자백 대상이 아니다 — 잴 것이 없다.
            if gmv != ZERO:
                unmapped_revenue += gmv
            continue
        unit_sum = price.get("delivery", ZERO) + price.get("warehousing", ZERO)
        opt_logi = unit_sum * qty * VAT_GROSSUP
        logistics += opt_logi
        revenue_priced += gmv
        by_option[vid] = opt_logi
        if rate is not None:
            by_option[vid] += gmv * rate

    fee = (revenue_total * rate) if rate is not None else ZERO
    period = period_fees(db, account_key, date_from, date_to)

    # ★커버리지의 분모 — 호출부가 화면에 싣는 매출이 있으면 **그것**으로 잰다(위 docstring ★).
    #   두 축이 어긋난 만큼이 그대로 「비용을 못 붙인 매출」이 되어 게이트에 걸린다.
    denom = revenue_reference if revenue_reference is not None else revenue_total
    coverage = (revenue_priced / denom) if denom > 0 else None
    if revenue_reference is not None:
        # 요약축이 옵션축보다 크면 그 차액도 «못 붙인 매출»이다 — 그 매출의 비용은 0으로 갔다.
        # 반대(옵션축이 더 큼)는 여기서 음수로 만들지 않는다: 자백 칸은 「못 붙인 몫」이지
        # 두 축의 부호 있는 차이가 아니고, 그 진단은 `revenue_reconcile`의 몫이다.
        unmapped_revenue = max(ZERO, denom - revenue_priced)

    out = {
        "total": logistics + fee + period,
        "logistics": logistics,
        "sale_fee": fee,
        "period": period,
        "rate": rate,
        "rate_basis": rate_info["basis"],
        "rate_cycles": rate_info["cycles"],
        "coverage": coverage,
        "unmapped_revenue": unmapped_revenue,
        "revenue_priced": revenue_priced,
        "revenue_total": revenue_total,
        "by_option": by_option,
        "reconciliation": None,
    }
    if reconcile:
        out["reconciliation"] = _reconcile(db, account_key, asof)
    return out


def _reconcile(db: Session, account_key: str, asof: date) -> dict | None:
    """최근 **완결** 주기에서 이 방식의 합 vs 원장 실청구액 (계약 §4 ⓒ).

    ★왜 「그 창」이 아니라 「완결 주기」에서 재나: 임의의 창은 정산 주기 경계와 안 맞아서
      원장 총액과 비교하는 것 자체가 뜻이 없다(주기의 일부만 걸치면 원장은 통짜로 준다).
      완결 주기는 분자·분모가 같은 기간을 가리키는 **유일한 자리**다.
    ★차이를 숨겨 0으로 만들지 않는다. 실측 −0.36%(08-10~16) / +4.83%(08-03~09)가
      이 방식이 실제로 얼마나 맞는지다. 화면이 이 숫자를 그대로 보여준다.
    """
    cycle = _latest_completed_cycle(db, account_key, asof)
    if cycle is None:
        return None
    cf, ct = cycle
    computed = sales_date_fees(db, account_key, cf, ct, asof=asof, reconcile=False)
    actual = ledger_total(db, account_key, cf, ct)
    diff = computed["total"] - actual
    return {
        "cycle_from": cf.isoformat() if hasattr(cf, "isoformat") else str(cf),
        "cycle_to": ct.isoformat() if hasattr(ct, "isoformat") else str(ct),
        "computed": computed["total"],
        "actual": actual,
        "diff": diff,
        "diff_pct": (diff / actual * Decimal("100")) if actual != ZERO else None,
    }


def daily_fees(
    db: Session, account_key: str, date_from: date, date_to: date, asof: date | None = None
) -> dict[date, Decimal]:
    """날짜별 판매일 축 정산공제 — 「같은 주의 날짜들이 서로 다른 값을 갖는가」의 증거(계약 §4 ⓑ).

    화면 경로는 창 단위(`sales_date_fees`)를 쓴다. 이 함수는 추이·검증용이다.
    """
    asof = asof or date.today()
    out: dict[date, Decimal] = {}
    d = date_from
    while d <= date_to:
        out[d] = sales_date_fees(db, account_key, d, d, asof=asof, reconcile=False)["total"]
        d += timedelta(days=1)
    return out
