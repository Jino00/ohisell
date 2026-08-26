# verify_bep_delivery_parity.py — 배송 구분이 BEP로 들어가는 **현재 경로**를 라이브 데이터로 검증
#
# 이력(중요): 원본(2026-07-28 N0)은 `_avg_qty_and_logistics`가 주문 건별로 부르던
# `order_delivery.order_shipping_cost`를 몽키패치해 신·구 산출을 대조했다. N1(D-NAO-99)이 지불
# 배송비를 **건별 합산 → 혼합비 공식**(NORMAL + (NBAESONG−NORMAL) × N배송비중)으로 바꾸면서 그 호출이
# 끊겼고, 스크립트는 무엇을 주입해도 통과하는 껍데기가 됐다(적대적 리뷰 P2-2 재현: 9,999원을
# 주입해도 출력 불변). **초록불인데 아무것도 안 보는 상태가 아무 검증도 없는 것보다 나쁘다**
# (원칙 22). 그래서 현재 경로에 맞게 다시 썼다.
#
# 무엇을 보는가 (셋 다 라이브 DB 실데이터):
#   [1] 판별 일치 — 주문 전 건에 대해 `영속 컬럼(delivery_attribute_type)` 판정과
#       `raw_data 파싱` 판정이 같은가. N0 백필이 원천과 어긋나면 여기서 잡힌다.
#       ★자기 점검(sentinel): 일부러 어긋난 행을 넣어 검사기가 **감지에 실패하면 즉시 FAIL**한다
#         — 경로가 또 끊겼을 때 조용히 통과하지 않게 하는 장치.
#   [2] 물류비 산출 — `_avg_qty_and_logistics`의 상품별 결과를 SQL 원자료로 독립 재계산해 대조
#       (혼합비 → 지불 → 순배송원가 → 단가당 물류비).
#   [3] (--recalc) 재실행 결정성 — calculate_bep을 두 번 돌려 전 상품 값이 동일한가.
#       ★"저장된 값과 같은가"가 아니다. N1은 값을 의도적으로 바꾸므로 그 대조는 이제 무의미하고,
#         멱등성(같은 입력 → 같은 출력)이 크론 재실행이 보장해야 할 성질이다.
#
# 실행:
#   cd backend && python scripts/verify_bep_delivery_parity.py            # [1][2] (읽기 전용)
#   cd backend && python scripts/verify_bep_delivery_parity.py --recalc   # [1][2][3] (테이블 재생성)
from __future__ import annotations

import argparse
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import engine  # noqa: E402
from app.models import NaverProductBep, Order  # noqa: E402
from app.services import order_delivery  # noqa: E402
from app.services.naver_ad import bep_calculator  # noqa: E402

NAVER_CHANNEL_ID = 6


def _raw_verdict(raw) -> bool:
    """raw_data 원천만 보고 내리는 N배송 판정(대조군 — 컬럼을 보지 않는다)."""
    po = order_delivery.product_order_of(raw)
    attr = po.get("deliveryAttributeType") if po else None
    return order_delivery.shipping_method_of(attr) == order_delivery.METHOD_NBAESONG


def verdict_parity(db: Session) -> int:
    """[1] 영속 컬럼 판정 vs raw_data 판정 일치 + 검사기 자기 점검."""
    rows = db.query(Order.id, Order.delivery_attribute_type, Order.raw_data).filter(
        Order.channel_id == NAVER_CHANNEL_ID).all()
    diffs = 0
    n_compared = n_no_raw = n_raw_only = 0
    for oid, attr, raw in rows:
        has_raw = order_delivery.product_order_of(raw) is not None
        if attr is None:
            n_raw_only += 1
            continue          # 컬럼이 없으면 대조 대상이 아니다(폴백 경로는 self-check가 본다)
        if not has_raw:
            n_no_raw += 1     # ★원천이 없거나 잘려 대조 자체가 불가 — "일치"로 세지 않는다
            continue
        n_compared += 1
        col_v = bep_calculator._is_nbaesong({"delivery_attribute_type": attr, "raw_data": raw})
        if col_v != _raw_verdict(raw):
            print("  ✗ order %s: 컬럼=%s raw=%s (attr=%r)" % (oid, col_v, _raw_verdict(raw), attr))
            diffs += 1
    # 자기 점검 — 어긋난 행을 주입했을 때 검사기가 반드시 다르게 판정해야 한다.
    fake_raw = '{"productOrder": {"deliveryAttributeType": "TODAY"}}'
    if bep_calculator._is_nbaesong(
        {"delivery_attribute_type": order_delivery.NBAESONG_ATTR, "raw_data": fake_raw}
    ) == _raw_verdict(fake_raw):
        print("  ✗ [self-check] 판별 경로가 끊겼다 — 컬럼을 바꿔도 판정이 안 변한다")
        diffs += 1
    if not bep_calculator._is_nbaesong({"delivery_attribute_type": None,
                                        "raw_data": '{"productOrder": {"deliveryAttributeType": '
                                                    '"ARRIVAL_GUARANTEE"}}'}):
        print("  ✗ [self-check] raw_data 폴백 경로가 죽었다 — 컬럼 NULL 행이 항상 일반배송이 된다")
        diffs += 1
    print("[1] 판별 일치 — 주문 %d건 중 실대조 %d건, 불일치 %d건 "
          "(원천 부재·잘림으로 대조불가 %d · 컬럼 미충전 %d)"
          % (len(rows), n_compared, diffs, n_no_raw, n_raw_only))
    if n_compared == 0:
        # ★무증명 실행은 초록불로 끝내지 않는다 — "아무것도 못 봤다"는 PASS가 아니라 FAIL이다.
        print("  ✗ 실제로 대조한 행이 0건이다 — 이 실행의 [1]은 아무것도 증명하지 않는다")
        diffs += 1
    return diffs


def logistics_recompute(db: Session) -> int:
    """[2] 상품별 물류비를 원자료에서 독립 재계산해 대조."""
    produced = bep_calculator._avg_qty_and_logistics(db)
    raw_rows = bep_calculator._naver_order_rows(db)
    n = bep_calculator._RECENT_SAMPLE_SIZE
    wide_cut = bep_calculator.kst_today() - bep_calculator.timedelta(
        days=bep_calculator._PRICE_WINDOW_DAYS)
    diffs = 0
    for pid, rows in raw_rows.items():
        wide = [r for r in rows if r["order_date"] and r["order_date"] >= wide_cut] or rows
        recent, _fresh = bep_calculator._recent_sample(rows, n)
        share = (Decimal(sum(1 for r in recent if r["nbaesong"])) / Decimal(len(recent))
                 if recent else Decimal("0"))
        # ★단가를 숫자로 적지 않는다 — 정본은 order_delivery.py(2026-08-26, 교훈 #365).
        #   종전엔 `1900 + 1120 * share`가 하드코딩돼 있었다. N배송 단가가 3,020→3,377로
        #   갱신되면서 차액이 1,120→1,477이 됐는데 여기만 안 따라가, 이 «검증» 스크립트가
        #   nb_share>0인 전 상품에서 **거짓 불일치**를 뱉는 상태였다(엔진이 맞고 검증기가 틀림).
        paid = (bep_calculator.SHIPPING_COST_NORMAL
                + (bep_calculator.SHIPPING_COST_NBAESONG - bep_calculator.SHIPPING_COST_NORMAL)
                * share)
        qty = sum(r["quantity"] for r in wide)
        avg_qty = Decimal(qty) / Decimal(len(wide)) if wide and qty > 0 else Decimal("1")
        # ★수취는 **지불과 같은 표본(최근 N건)**에서 뽑는다 — D-NAO-168(2026-08-10).
        #   종전엔 여기서 wide(120일)를 썼다. 엔진은 그때 recent로 옮겼는데 이 스크립트만
        #   남아, 위 하드코딩과 겹쳐 두 번째 거짓 불일치 원인이 됐다.
        collected = (sum((r["collected"] for r in recent), Decimal("0")) / Decimal(len(recent))
                     if recent else Decimal("0"))
        expect = (max(Decimal("0"), paid - collected) / avg_qty).quantize(
            Decimal("0.01"), ROUND_HALF_UP)
        got = produced.get(pid, {}).get("logistics")
        if got != expect:
            print("  ✗ %s: logistics 산출=%s 재계산=%s (혼합비 %s)" % (pid, got, expect, share))
            diffs += 1
    print("[2] 물류비 독립 재계산 — 상품 %d개 대조, 차이 %d건" % (len(raw_rows), diffs))
    return diffs


_BEP_COLS = ("selling_price", "cost_price", "logistics_cost", "contribution_margin",
             "bep_roas", "target_roas", "commission_rate", "commission_basis", "has_cost")


def _snapshot(db: Session) -> dict:
    return {r.channel_product_id: {c: getattr(r, c) for c in _BEP_COLS}
            for r in db.query(NaverProductBep).all()}


def recalc_determinism(db: Session) -> int:
    """[3] calculate_bep 2회 실행 → 전 상품 동일해야 한다(크론 재실행 안전성)."""
    bep_calculator.calculate_bep(db)
    db.expire_all()
    first = _snapshot(db)
    meta = bep_calculator.calculate_bep(db)
    db.expire_all()
    second = _snapshot(db)
    diffs = 0
    for cpid in sorted(set(first) | set(second)):
        a, b = first.get(cpid), second.get(cpid)
        if a is None or b is None:
            print("  ✗ %s: 한쪽에만 존재" % cpid)
            diffs += 1
            continue
        for c in _BEP_COLS:
            if a[c] != b[c]:
                print("  ✗ %s.%s: 1회차=%s 2회차=%s" % (cpid, c, a[c], b[c]))
                diffs += 1
    print("[3] 재실행 결정성 — 상품 %d개, 차이 %d건 (meta=%s)" % (len(second), diffs, meta))
    return diffs


def main() -> None:
    ap = argparse.ArgumentParser(description="BEP 배송 구분 경로 검증")
    ap.add_argument("--recalc", action="store_true", help="[3] 재실행 결정성까지(테이블 재생성)")
    a = ap.parse_args()
    with Session(engine) as db:
        total = verdict_parity(db) + logistics_recompute(db)
        if a.recalc:
            total += recalc_determinism(db)
    print("── 결과: %s ──" % ("PASS (차이 0)" if total == 0 else "FAIL (차이 %d건)" % total))
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
