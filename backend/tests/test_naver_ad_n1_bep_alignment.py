# test_naver_ad_n1_bep_alignment.py — N1 BEP 배송방식 인지형 정합 (D-NAO-99 ref 42 · D-NAO-100 ref 43)
# 커버: ① 레짐 표본 = **최근 10건 중앙값**(달력 상한 120일, E5c) — 10건 경계·상한 걸림·10건 미만·
#   상한 내 0건일 때 전기간 폴백·주문 0건, 그리고 표본이 한 건씩 밀려 나가는 **왕복 없는 단조 이동**
#   (폐기된 적응형 사다리의 계단 진동이 재발하지 않는지) ② 판매가와 N배송 혼합비가 같은 표본을 쓰고,
#   평균수량·수취배송비는 넓은 창(120일)을 유지 ③ product_commission SA(주문관리·기저 매출연동 분해,
#   N배송 프리미엄 1.5%p 제거·재부과, 정확 키 매칭·분할 라인 중복 방지, 표본<5 계정 폴백,
#   표본 없음 → 계정 단일 요율 폴백) ④ calculate_bep 통합 손계산.
from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Channel, NaverProductBep, NaverSettlementCase, Order, ProductChannelMapping, ProductMaster,
)
from app.services.naver_ad import bep_calculator, product_commission
from app.utils.kst import kst_today

AG = "ARRIVAL_GUARANTEE"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _order(db, pid, *, days_ago, price="10000", qty=1, nbaesong=False, collected=None, n=1):
    for i in range(n):
        db.add(Order(
            channel_id=6, platform_product_id=pid, order_number=f"o-{pid}-{days_ago}-{i}-{price}",
            quantity=qty, selling_price=Decimal(price),
            shipping_cost=Decimal(collected) if collected is not None else None,
            order_date=kst_today() - timedelta(days=days_ago),
            delivery_attribute_type=AG if nbaesong else "TODAY",
        ))


# ══════════════════════════════════════════════════════════════════
# ① 레짐 표본 = 최근 N건(달력 상한 120일) — D-NAO-100 / ref 43 E5c
# ══════════════════════════════════════════════════════════════════
def _rows(*specs):
    """(days_ago, nbaesong) 목록 → _recent_sample 입력 형태(날짜 내림차순)."""
    return sorted(
        [{"order_date": kst_today() - timedelta(days=d), "nbaesong": nb, "quantity": 1,
          "unit_price": Decimal("10000"), "collected": Decimal("0")} for d, nb in specs],
        key=lambda r: r["order_date"], reverse=True)


def test_recent_sample_takes_exactly_n_most_recent():
    rows = _rows(*[(1, False)] * 5, *[(20, False)] * 10)
    sub, fresh = bep_calculator._recent_sample(rows)
    assert fresh and len(sub) == 10                       # 15건 중 최근 10건만
    assert all(r["order_date"] >= kst_today() - timedelta(days=20) for r in sub)


def test_recent_sample_boundary_at_ten():
    """경계: 10건이면 그대로, 11건이면 가장 오래된 1건이 밀려난다."""
    exactly = _rows(*[(3, False)] * 10)
    assert len(bep_calculator._recent_sample(exactly)[0]) == 10
    eleven = _rows(*[(3, False)] * 10, (60, False))
    sub = bep_calculator._recent_sample(eleven)[0]
    assert len(sub) == 10
    assert all(r["order_date"] == kst_today() - timedelta(days=3) for r in sub)


def test_recent_sample_applies_120d_calendar_cap():
    """달력 상한: 120일 밖 주문은 후보에서 빠진다(최근 10건을 못 채워도)."""
    rows = _rows(*[(10, False)] * 3, *[(200, False)] * 20)
    sub, fresh = bep_calculator._recent_sample(rows)
    assert fresh and len(sub) == 3                        # 상한 내 3건뿐 → 있는 만큼


def test_recent_sample_falls_back_to_alltime_when_cap_empty():
    """상한 안에 0건이면 전기간 최근 N건 — 표본을 비우면 BEP 행이 통째로 사라진다."""
    rows = _rows(*[(200, False)] * 15)
    sub, fresh = bep_calculator._recent_sample(rows)
    assert not fresh and len(sub) == 10


def test_order_rows_sorted_by_datetime_then_id(db):
    """정렬키 = (주문 시각, id) — 같은 날 주문이 여러 건이어도 DB 물리 행 순서에 의존하지 않는다.

    같은 날짜의 시각 다른 주문 3건을 **역순으로 삽입**해도 최신순이 시각 기준으로 나와야 한다.
    """
    for hour, num in ((9, "b"), (18, "c"), (3, "a")):
        db.add(Order(channel_id=6, platform_product_id="p1", order_number=f"o-{num}",
                     platform_order_line_id=f"l-{num}", quantity=1, selling_price=Decimal("10000"),
                     order_date=datetime.combine(kst_today() - timedelta(days=1),
                                                 dt_time(hour, 0))))
    db.commit()
    rows = bep_calculator._naver_order_rows(db)["p1"]
    assert [r["order_at"].hour for r in rows] == [18, 9, 3]
    # 시각이 완전히 같으면 id 내림차순(결정적) — 물리 행 순서가 아니라 키로 결정된다
    same = [{"order_at": datetime(2026, 7, 1, 12), "id": i} for i in (1, 3, 2)]
    assert [r["id"] for r in sorted(same, key=bep_calculator._order_sort_key, reverse=True)] == [3, 2, 1]


def test_recent_sample_size_default_is_ten():
    """ref 43 §5: N=10이 내부 최적점(N=5는 편향↑, N≥20은 정확도 붕괴)."""
    assert bep_calculator._RECENT_SAMPLE_SIZE == 10
    assert bep_calculator._RECENT_SAMPLE_MAX_AGE_DAYS == 120
    assert not hasattr(bep_calculator, "_adaptive_rows")  # 사다리 폐기 확인


# ══════════════════════════════════════════════════════════════════
# ② 판매가·물류비 — 레짐 변수만 최근 표본
# ══════════════════════════════════════════════════════════════════
def test_unit_price_uses_recent_ten_not_120d_median(db):
    # 인하 전 18,900 × 40건(90일 전) vs 인하 후 15,900 × 12건(10일 전).
    # 120일 median이면 18,900이 이기지만, 최근 10건은 전부 15,900이다.
    _order(db, "p1", days_ago=90, price="18900", n=40)
    _order(db, "p1", days_ago=10, price="15900", n=12)
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("15900")


def test_unit_price_uses_available_rows_when_fewer_than_ten(db):
    """상한 내 주문이 10건 미만이면 있는 만큼으로 중앙값(옛 표본을 끌어오지 않는다)."""
    _order(db, "p1", days_ago=200, price="18900", n=40)   # 상한 밖
    _order(db, "p1", days_ago=2, price="15900", n=3)      # 상한 내 3건뿐
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("15900")


def test_unit_price_moves_monotonically_without_round_trip(db):
    """★사다리 폐기의 핵심 성질(ref 43 §0-2·§3): 표본이 한 건씩 밀려 나가는 **연속 이동**.

    옛 18,900 10건 위로 새 15,900 주문이 하나씩 쌓이는 리플레이. 최근 10건 median은
    18,900 → (5건째 17,400) → 15,900으로 **단조 하락**하고 되돌아오지 않는다.
    사다리는 여기서 창이 계단 점프하며 15,900 → 18,900으로 왕복했다.
    """
    _order(db, "p1", days_ago=15, price="18900", n=10)
    db.commit()
    seen = [bep_calculator._unit_prices(db)["p1"]]
    for k in range(1, 13):
        _order(db, "p1", days_ago=1, price="15900", n=1)
        db.commit()
        # 같은 날짜 주문이 여러 건이라 order_number가 겹치지 않도록 n=1씩, k로 구분
        db.query(Order).filter(Order.order_number == "o-p1-1-0-15900").update(
            {"order_number": f"o-p1-1-{k}-15900"})
        db.commit()
        seen.append(bep_calculator._unit_prices(db)["p1"])
    assert seen[0] == Decimal("18900")
    assert seen[-1] == Decimal("15900")
    assert all(a >= b for a, b in zip(seen, seen[1:])), f"왕복 발생: {seen}"


def test_unit_price_ignores_orders_older_than_120d_when_fresh_exist(db):
    _order(db, "p1", days_ago=200, price="9900", n=30)
    _order(db, "p1", days_ago=5, price="15900", n=2)
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("15900")


def test_unit_price_falls_back_to_alltime_when_no_recent_orders(db):
    """120일 내 주문이 0건인 상품(라이브 45종)도 판매가를 잃지 않는다."""
    _order(db, "p1", days_ago=200, price="9900", n=30)
    db.commit()
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("9900")


def test_unit_price_absent_without_any_order(db):
    """주문 0건이면 여기서 값을 내지 않는다 → 호출부의 매핑 판매가 폴백(D-NAO-95)이 받는다."""
    assert "p-none" not in bep_calculator._unit_prices(db)


def test_logistics_uses_recent_nbaesong_share(db):
    # 오래된 일반배송 40건 + 최근 N배송 12건 → 최근 10건은 전부 N배송 → 혼합비 100%
    _order(db, "p1", days_ago=90, n=40)
    _order(db, "p1", days_ago=3, nbaesong=True, n=12)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_share"] == Decimal("1")
    assert out["shipping"] == Decimal("3245")     # 1,900 + 1,345 × 1
    assert out["nb_sample"] == 10
    # ★평균 수량·수취 배송비는 넓은 창(52건 전부) — 혼합비만 최근 표본이다
    assert out["orders"] == 52


def test_logistics_nbaesong_share_uses_same_sample_as_price(db):
    """혼합비와 판매가는 **같은 최근 10건**을 본다(레짐 표본 통일, D-NAO-100)."""
    _order(db, "p1", days_ago=60, price="18900", n=20)               # 옛 일반배송
    _order(db, "p1", days_ago=2, price="15900", nbaesong=True, n=6)  # 최근 N배송
    _order(db, "p1", days_ago=3, price="15900", nbaesong=False, n=4)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_sample"] == 10
    assert out["nb_share"] == Decimal("0.6")      # 최근 10건 중 6건
    assert bep_calculator._unit_prices(db)["p1"] == Decimal("15900")


def test_logistics_paid_is_linear_in_nbaesong_share(db):
    # 최근 창 10건 중 5건 N배송 → 지불 = 1,900 + 1,120 × 0.5 = 2,460
    _order(db, "p1", days_ago=3, nbaesong=True, n=5)
    _order(db, "p1", days_ago=4, nbaesong=False, n=5)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_share"] == Decimal("0.5")
    assert out["shipping"] == Decimal("2572.5")
    assert out["logistics"] == Decimal("2572.50")


def test_logistics_wide_window_still_drives_qty_and_collected(db):
    # 넓은 창 안의 옛 주문(수량 3·수취 900)이 평균에 그대로 반영된다(창 단축 잡음 차단).
    _order(db, "p1", days_ago=100, qty=3, collected="900", n=1)
    _order(db, "p1", days_ago=1, qty=1, collected="0", n=1)
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["avg_qty"] == Decimal("2")          # (3+1)/2
    assert out["collected"] == Decimal("450")      # (900+0)/2
    assert out["net_ship"] == Decimal("1450")      # 1,900 − 450
    assert out["logistics"] == Decimal("725.00")   # 1,450 / 2


# ══════════════════════════════════════════════════════════════════
# ③ product_commission SA
# ══════════════════════════════════════════════════════════════════
def _settled(db, pid, *, i, gross, mgmt_rate, interlock_rate, nbaesong=False, days_ago=10,
             order_number=None, line_id=None, product_order_id=None):
    """주문 1행 + 그에 대응하는 건별 정산 1행.

    기본은 라이브 규약대로 platform_order_line_id == product_order_id(원자 주문라인 그레인).
    """
    g = Decimal(str(gross))
    onum = order_number or f"ord-{pid}-{i}"
    line = line_id if line_id is not None else f"po-{pid}-{i}"
    db.add(Order(channel_id=6, platform_product_id=pid, order_number=onum,
                 platform_order_line_id=line, quantity=1,
                 selling_price=g, order_date=kst_today() - timedelta(days=days_ago),
                 delivery_attribute_type=AG if nbaesong else "TODAY"))
    db.add(NaverSettlementCase(
        product_order_id=product_order_id or f"po-{pid}-{i}", order_id=onum, product_id=pid,
        product_order_type="PROD_ORDER", pay_settle_amount=g,
        total_pay_commission=-(g * Decimal(str(mgmt_rate))),
        selling_interlock_commission=-(g * Decimal(str(interlock_rate))),
        free_installment_commission=Decimal("0"),
    ))


def test_measure_decomposes_mgmt_and_base_interlock(db):
    # 비 N배송 10건: 주문관리 2.724% · 매출연동 3.0%
    for i in range(10):
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    assert t.available
    comp = t.products["p1"]
    assert comp.rows == 10
    assert comp.mgmt_rate == Decimal("0.02724")
    assert comp.base_interlock_rate == Decimal("0.03")
    rate, basis = t.rate_for("p1")
    assert basis == "delivery_case"
    assert rate == Decimal("0.05724")


def test_measure_strips_nbaesong_premium_from_base_interlock(db):
    # N배송 10건: 매출연동 4.5% = 기저 3.0% + 프리미엄 1.5%p → 기저만 3.0%로 환산돼야 한다
    for i in range(10):
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.045",
                 nbaesong=True)
    db.commit()
    t = product_commission.measure(db)
    assert t.products["p1"].base_interlock_rate == Decimal("0.03")
    # 혼합비 0(일반배송 기준) → 프리미엄 미부과
    assert t.rate_for("p1")[0] == Decimal("0.05724")
    # 혼합비 100% → 프리미엄 전액 재부과 = 실측 그대로
    assert t.rate_for("p1", Decimal("1"))[0] == Decimal("0.07224")
    # 혼합비 절반 → 선형
    assert t.rate_for("p1", Decimal("0.5"))[0] == Decimal("0.06474")


def test_measure_falls_back_to_account_when_product_sample_thin(db):
    for i in range(10):  # 계정 표본을 만드는 다른 상품
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.01")
    for i in range(3):   # 표본 3건(<5) → 계정 폴백
        _settled(db, "p2", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    rate, basis = t.rate_for("p2")
    assert basis == "delivery_acct"
    acct_rate, _ = t.rate_for(None)
    assert rate == acct_rate
    # 미지의 상품도 같은 계정 폴백
    assert t.rate_for("p-unknown")[0] == acct_rate


def test_measure_unavailable_without_settlement(db):
    t = product_commission.measure(db)
    assert not t.available


def test_measure_ignores_unmatched_settlement_rows(db):
    # orders와 조인되지 않는 정산 행은 상품 귀속이 불가 → 실측 표본에 들어가지 않는다
    db.add(NaverSettlementCase(product_order_id="po-x", order_id="no-such-order", product_id="p9",
                               product_order_type="PROD_ORDER", pay_settle_amount=Decimal("10000"),
                               total_pay_commission=Decimal("-272"),
                               selling_interlock_commission=Decimal("-300"),
                               free_installment_commission=Decimal("0")))
    db.commit()
    assert not product_commission.measure(db).available


def test_measure_rejects_implausible_rate(db):
    for i in range(10):  # 수수료 40% — 데이터 이상
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.20", interlock_rate="0.20")
    db.commit()
    assert not product_commission.measure(db).available


@pytest.mark.parametrize("n_rows,expected_basis", [(4, "delivery_acct"), (5, "delivery_case")])
def test_measure_min_rows_boundary(db, n_rows, expected_basis):
    """표본 경계: 4건이면 계정 폴백, 5건이면 상품 실측(min_rows=5의 등호 방향 고정)."""
    for i in range(10):  # 계정 표본
        _settled(db, "base", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.01")
    for i in range(n_rows):
        _settled(db, "p2", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    assert t.products["p2"].rows == n_rows
    assert t.rate_for("p2")[1] == expected_basis


def test_measure_split_order_lines_are_not_double_counted(db):
    """★P2-1 회귀 가드: 같은 주문+상품이 2개 productOrderId로 쪼개진 케이스.

    (order_number, product_id) 조인은 정산 2행 × 주문 2행 = 4행으로 부풀고, 배송구분이 섞여
    있으면 N배송 프리미엄 제거가 과도해져 기저 매출연동이 낙관(=상한 과대) 쪽으로 오염된다.
    정확 키(product_order_id ↔ platform_order_line_id)는 2행 그대로여야 한다.
    """
    onum = "ord-split-1"
    # 라인 A = 일반배송(매출연동 3.0%), 라인 B = N배송(4.5% = 기저 3.0% + 1.5%p)
    _settled(db, "p1", i=0, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03",
             order_number=onum, line_id="poA", product_order_id="poA")
    _settled(db, "p1", i=1, gross=10000, mgmt_rate="0.02724", interlock_rate="0.045",
             nbaesong=True, order_number=onum, line_id="poB", product_order_id="poB")
    db.commit()
    t = product_commission.measure(db)
    comp = t.products["p1"]
    assert comp.rows == 2                      # 4가 아니다(데카르트 중복 없음)
    assert comp.gross == Decimal("20000")
    assert comp.mgmt_rate == Decimal("0.02724")
    assert comp.base_interlock_rate == Decimal("0.03")   # 두 라인 모두 기저 3.0%
    assert t.matched_by_line == 2 and t.matched_by_fallback == 0 and t.unmatched == 0


def test_measure_fallback_matches_when_single_candidate(db):
    """정확 키가 안 맞는 정산 행(라이브 0.19%)은 후보가 유일할 때만 폴백 매칭."""
    _settled(db, "p1", i=0, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03",
             order_number="ord-1", line_id="line-1", product_order_id="mismatched-1")
    db.commit()
    t = product_commission.measure(db)
    assert t.matched_by_line == 0 and t.matched_by_fallback == 1 and t.unmatched == 0
    assert t.products["p1"].rows == 1


def test_measure_drops_ambiguous_fallback(db):
    """후보가 2건 이상(분할 주문)이면 귀속이 애매 → 억지로 붙이지 않고 제외한다."""
    onum = "ord-amb"
    _settled(db, "p1", i=0, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03",
             order_number=onum, line_id="line-a", product_order_id="line-a")
    _settled(db, "p1", i=1, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03",
             order_number=onum, line_id="line-b", product_order_id="nope-b")
    db.commit()
    t = product_commission.measure(db)
    assert t.matched_by_line == 1 and t.unmatched == 1
    assert t.products["p1"].rows == 1


def test_measure_window_default_is_120_days(db):
    """ref 42 §3: 기저 요율 창 = 120일. 창 밖 표본만 있는 상품은 전기간 폴백으로 살린다."""
    assert product_commission._COMMISSION_WINDOW_DAYS == 120
    for i in range(6):  # 창 안 — 매출연동 3.0%
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03",
                 days_ago=10)
    for i in range(6):  # 창 밖(200일 전) — 매출연동 1.0%, 기본 창에선 제외돼야 한다
        _settled(db, "p1", i=100 + i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.01",
                 days_ago=200)
    for i in range(6):  # 창 밖 표본만 있는 상품 → 전기간 폴백
        _settled(db, "p2", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.01",
                 days_ago=200)
    db.commit()
    t = product_commission.measure(db)
    assert t.products["p1"].rows == 6
    assert t.products["p1"].base_interlock_rate == Decimal("0.03")
    assert t.products["p2"].rows == 6      # 창 밖뿐이지만 폴백으로 살아남는다
    assert t.products["p2"].base_interlock_rate == Decimal("0.01")


def test_rate_for_accepts_non_decimal_share(db):
    """P3-4: float/int/str 혼합비도 Decimal로 강제 변환(TypeError로 BEP 전체가 죽지 않게)."""
    for i in range(10):
        _settled(db, "p1", i=i, gross=10000, mgmt_rate="0.02724", interlock_rate="0.03")
    db.commit()
    t = product_commission.measure(db)
    assert t.rate_for("p1", 0.5)[0] == t.rate_for("p1", Decimal("0.5"))[0]
    assert t.rate_for("p1", 1)[0] == t.rate_for("p1", Decimal("1"))[0]
    assert t.rate_for("p1", "0.25")[0] == t.rate_for("p1", Decimal("0.25"))[0]
    # 범위 밖·이상값은 보수적으로 클램프
    assert t.rate_for("p1", 5)[0] == t.rate_for("p1", Decimal("1"))[0]
    assert t.rate_for("p1", -1)[0] == t.rate_for("p1", Decimal("0"))[0]
    assert t.rate_for("p1", "nonsense")[0] == t.rate_for("p1", Decimal("0"))[0]


# ══════════════════════════════════════════════════════════════════
# _is_nbaesong — 영속 컬럼 우선 · raw_data 폴백
# ══════════════════════════════════════════════════════════════════
def test_is_nbaesong_prefers_persisted_column():
    assert bep_calculator._is_nbaesong({"delivery_attribute_type": AG, "raw_data": None})
    assert not bep_calculator._is_nbaesong({"delivery_attribute_type": "TODAY", "raw_data":
                                            '{"productOrder": {"deliveryAttributeType": "%s"}}' % AG})


def test_is_nbaesong_falls_back_to_raw_data_when_column_null():
    # 백필 이전·판별 불가 행: 컬럼 NULL → raw_data 파싱
    assert bep_calculator._is_nbaesong(
        {"delivery_attribute_type": None,
         "raw_data": '{"productOrder": {"deliveryAttributeType": "%s"}}' % AG})
    assert not bep_calculator._is_nbaesong(
        {"delivery_attribute_type": None,
         "raw_data": '{"productOrder": {"deliveryAttributeType": "TODAY"}}'})
    # 파싱 실패·부재는 일반배송 fail-safe
    assert not bep_calculator._is_nbaesong({"delivery_attribute_type": None, "raw_data": "not json{"})
    assert not bep_calculator._is_nbaesong({"delivery_attribute_type": None, "raw_data": None})


def test_logistics_uses_raw_data_fallback_for_unbackfilled_rows(db):
    """컬럼 미충전 주문도 혼합비에 반영된다(raw_data 폴백이 살아 있는가)."""
    for i in range(10):
        db.add(Order(channel_id=6, platform_product_id="p1", order_number=f"o{i}",
                     platform_order_line_id=f"l{i}", quantity=1, selling_price=Decimal("10000"),
                     order_date=kst_today() - timedelta(days=2),
                     delivery_attribute_type=None,
                     raw_data='{"productOrder": {"deliveryAttributeType": "%s"}}' % AG))
    db.commit()
    out = bep_calculator._avg_qty_and_logistics(db)["p1"]
    assert out["nb_share"] == Decimal("1")
    assert out["shipping"] == Decimal("3245")


# ══════════════════════════════════════════════════════════════════
# ④ calculate_bep 통합
# ══════════════════════════════════════════════════════════════════
def test_calculate_bep_delivery_aware_hand_computed(db):
    """손계산 하드코딩 대조(거울 방지).

    입력: 최근 10건 전부 N배송·판매가 20,000·수량 1·수취 0 / 원가 5,000
          정산 실측 10건: 주문관리 2.724% · 매출연동 4.5%(N배송) → 기저 3.0%
    손계산: 혼합비 1.0 → 수수료율 = 0.02724 + 0.03 + 0.015 = 0.07224
            물류비 = 1,900 + 1,345×1 = 3,245 (품고 요율표 2, 2026-08-03 정정)
            20,000×0.07224 = 1,444.8 → 20,000−1,444.8−5,000−3,245 = 10,310.2
            공헌이익 = 10,310.2 / 1.1 = 9,372.9090… → 9,372.91(2자리)
            bep = 20,000 / 9,372.91 = 2.13383… → 2.1338(4자리)
    """
    pm = ProductMaster(internal_sku="SKU-N1", product_name="N1", cost_price=Decimal("5000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="p1",
                                 channel_product_name="N1", selling_price=Decimal("0"),
                                 is_active=True))
    for i in range(10):
        _settled(db, "p1", i=i, gross=20000, mgmt_rate="0.02724", interlock_rate="0.045",
                 nbaesong=True, days_ago=3)
    db.commit()

    res = bep_calculator.calculate_bep(db)
    assert res["commission_basis"] == "delivery_acct"   # 대표값은 계정 실측(배송 중립)
    assert res["product_rate_rows"] == 1
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "p1").one()
    assert row.commission_basis == "delivery_case"
    assert row.commission_rate == Decimal("0.0722")     # 0.07224 → 4자리 반올림
    assert row.logistics_cost == Decimal("3245.00")
    assert row.selling_price == Decimal("20000")
    expected_cm = ((Decimal("20000") - Decimal("20000") * Decimal("0.07224")
                    - Decimal("5000") - Decimal("3245")) / Decimal("1.1")).quantize(Decimal("0.01"))
    assert row.contribution_margin == expected_cm
    assert row.bep_roas == Decimal("2.1338")


def test_calculate_bep_falls_back_to_account_rate_without_settlement(db):
    """정산 표본이 없으면 종전 계정 단일 요율 경로 그대로(회귀 0)."""
    db.add(Channel(id=6, name="네이버", code="NAVER", platform="naver",
                   commission_rate=Decimal("5.0")))
    pm = ProductMaster(internal_sku="SKU-F", product_name="F", cost_price=Decimal("5000"))
    db.add(pm)
    db.flush()
    db.add(ProductChannelMapping(product_id=pm.id, channel_id=6, channel_product_id="p1",
                                 channel_product_name="F", selling_price=Decimal("0"),
                                 is_active=True))
    _order(db, "p1", days_ago=1, price="10000")
    db.commit()
    res = bep_calculator.calculate_bep(db)
    assert res["commission_basis"] == "blended"
    assert res["product_rate_rows"] == 0
    row = db.query(NaverProductBep).filter(NaverProductBep.channel_product_id == "p1").one()
    assert row.commission_rate == Decimal("0.0500")
    assert row.logistics_cost == Decimal("1900.00")
