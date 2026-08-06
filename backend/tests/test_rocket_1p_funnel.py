# test_rocket_1p_funnel.py — 1P 유입·전환 퍼널 SA (S3)
#
# 이 파일이 지키는 것:
#   ① 전환율은 **Σ주문/Σ조회** — 일별 비율의 평균이 아니다(작은 날이 큰 날과 같은 표를 갖는다)
#   ② 표본이 얕은 옵션은 **판정하지 않는다**(low_sample) — 조회 3회의 33%가 상위로 올라가면 표가 거짓말한다
#   ③ 판정 임계값은 **응답에 실려 나온다** — 숨은 기준으로 나눈 분류는 사실이 아니라 의견이다
#   ④ 리뷰 0건의 평점은 **모름**이지 0점이 아니다(0.0으로 그리면 신상품이 최악의 상품이 된다)
#   ⑤ 부분 결손(며칠치 NULL)을 **세어서 드러낸다** — SUM은 NULL을 조용히 건너뛴다
#
# 값은 2026-08-04 라이브 실측: 방문자 1,443 · 조회 2,763 · 주문 363 · 판매 342 · 전환 13.14%.
# 옵션 1위 95752961189(Z폴드8) 방문자 765 · 조회 1,540 · 주문 162 · 전환 10.52%.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import CoupangRocketOptionSku, CoupangRocketSalesDaily
from app.services.coupang.rocket_1p_channel_pnl import ROCKET_1P_VENDOR_ID as VENDOR
from app.services.coupang.rocket_1p_funnel import compute_rocket_1p_funnel

D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _row(s, option_id, *, d=D, visitors=None, pv=None, orders=None, qty=0, revenue="0"):
    s.add(CoupangRocketSalesDaily(
        vendor_id=VENDOR, option_id=option_id, sku_id=f"S{option_id}", date=d,
        qty=qty, revenue=Decimal(revenue), visitors=visitors,
        page_views=pv, orders=orders,
        product_name=f"상품 {option_id}", source="sales_analysis"))


def _attrs(s, option_id, *, oos=None, rating_count=None, rating_score=None):
    s.add(CoupangRocketOptionSku(
        option_id=option_id, sku_id=f"S{option_id}", vendor_id=VENDOR,
        source="sales_analysis", is_oos=oos,
        rating_count=rating_count,
        rating_score=None if rating_score is None else Decimal(rating_score)))


# ═══ ① 전환율은 합계의 몫이지 일별 평균이 아니다 ═══
def test_cvr_is_sum_over_sum_not_average_of_daily_rates(db):
    """작은 날과 큰 날을 같은 표로 세면 안 된다 — 이 차이가 이 화면의 존재 이유다."""
    _row(db, "A", d=date(2026, 8, 3), pv=4, orders=1, visitors=3, qty=1)     # 25.00%
    _row(db, "A", d=date(2026, 8, 4), pv=400, orders=40, visitors=300, qty=40)  # 10.00%
    db.commit()
    r = compute_rocket_1p_funnel(db, date(2026, 8, 3), D, min_page_views=0)
    # Σ41/Σ404 = 0.1015…  (일별 평균이면 0.175가 나온다)
    assert Decimal(r["totals"]["cvr"]) == Decimal("0.1015")
    assert Decimal(r["options"][0]["cvr"]) == Decimal("0.1015")
    assert Decimal(r["totals"]["cvr"]) != Decimal("0.1750")


def test_funnel_totals_match_live_screen(db):
    """08-04 라이브 실측 재현 — 화면: 방문자 1,443 · 조회 2,763 · 주문 363 · 전환 13.14%."""
    _row(db, "A", visitors=765, pv=1540, orders=162, qty=152, revenue="3024800")
    _row(db, "B", visitors=678, pv=1223, orders=201, qty=190, revenue="3511200")
    db.commit()
    t = compute_rocket_1p_funnel(db, D, D)["totals"]
    assert t["visitors"] == 1443 and t["page_views"] == 2763
    assert t["orders"] == 363 and t["qty"] == 342
    assert Decimal(t["cvr"]) == Decimal("0.1314")          # 363/2763
    assert Decimal(t["views_per_visitor"]) == Decimal("1.9148")   # 2763/1443 = 1.91476…


def test_option_rates_match_coupang_definition(db):
    _row(db, "95752961189", visitors=765, pv=1540, orders=162, qty=152)
    db.commit()
    o = compute_rocket_1p_funnel(db, D, D)["options"][0]
    assert Decimal(o["cvr"]) == Decimal("0.1052")           # 화면 10.52%
    assert Decimal(o["units_per_order"]) == Decimal("0.9383")


# ═══ ② 표본이 얕으면 판정하지 않는다 ═══
def test_shallow_sample_is_not_judged(db):
    _row(db, "TINY", visitors=2, pv=3, orders=1, qty=1)      # 33% — 판정하면 표가 거짓말한다
    _row(db, "BIG", visitors=300, pv=600, orders=60, qty=60)
    db.commit()
    by = {o["option_id"]: o for o in compute_rocket_1p_funnel(db, D, D)["options"]}
    assert by["TINY"]["position"] == "low_sample"
    assert by["TINY"]["cvr"] == "0.3333"                     # 값은 그대로 보여준다
    assert by["BIG"]["position"] != "low_sample"


def test_position_describes_quadrant_not_advice(db):
    """위치는 중앙값 대비 상/하 서술이다. 조회↑전환↓ / 조회↓전환↑가 갈린다."""
    _row(db, "SEEN_NOT_BOUGHT", visitors=500, pv=1000, orders=20, qty=20)   # 조회↑ 전환 2%
    _row(db, "BOUGHT_NOT_SEEN", visitors=30, pv=50, orders=15, qty=15)      # 조회↓ 전환 30%
    _row(db, "MID", visitors=100, pv=200, orders=20, qty=20)                # 조회 200 전환 10%
    db.commit()
    by = {o["option_id"]: o for o in compute_rocket_1p_funnel(db, D, D)["options"]}
    assert by["SEEN_NOT_BOUGHT"]["position"] == "views_high_cvr_low"
    assert by["BOUGHT_NOT_SEEN"]["position"] == "views_low_cvr_high"


# ═══ ③ 임계값을 숨기지 않는다 ═══
def test_thresholds_are_disclosed(db):
    _row(db, "A", visitors=100, pv=200, orders=20, qty=20)
    _row(db, "B", visitors=50, pv=100, orders=5, qty=5)
    db.commit()
    th = compute_rocket_1p_funnel(db, D, D, min_page_views=40)["thresholds"]
    assert th["min_page_views"] == 40
    assert th["median_cvr"] is not None and th["median_page_views"] is not None
    assert th["eligible_options"] == 2
    assert "권고가 아니다" in th["note"]


def test_min_page_views_is_caller_controlled(db):
    _row(db, "A", visitors=5, pv=10, orders=2, qty=2)
    db.commit()
    assert compute_rocket_1p_funnel(db, D, D, min_page_views=30)["options"][0]["position"] == "low_sample"
    assert compute_rocket_1p_funnel(db, D, D, min_page_views=5)["options"][0]["position"] != "low_sample"


# ═══ ④ 리뷰 0건의 평점은 모름이다 ═══
def test_rating_without_reviews_is_unknown_not_zero(db):
    _row(db, "NEW", visitors=10, pv=20, orders=1, qty=1)
    _attrs(db, "NEW", rating_count=0, rating_score="0")
    _row(db, "OLD", visitors=10, pv=20, orders=1, qty=1)
    _attrs(db, "OLD", rating_count=10575, rating_score="4.6")
    db.commit()
    by = {o["option_id"]: o for o in compute_rocket_1p_funnel(db, D, D)["options"]}
    # 0.0으로 그리면 신상품이 '최악의 상품' 자리에 앉는다.
    assert by["NEW"]["rating_score"] is None
    assert Decimal(by["OLD"]["rating_score"]) == Decimal("4.60")


def test_oos_flag_passes_through(db):
    _row(db, "A", visitors=10, pv=20, orders=0, qty=0)
    _attrs(db, "A", oos=True)
    db.commit()
    assert compute_rocket_1p_funnel(db, D, D)["options"][0]["is_oos"] is True


# ═══ ⑤ 부분 결손을 드러낸다 ═══
def test_missing_metric_days_are_counted(db):
    """SUM은 NULL을 조용히 건너뛴다 — 며칠치 빠진 합계를 온전한 값처럼 보이면 안 된다.

    ★2026-08-06 적대 리뷰 P1: 예전엔 pv 결손 + orders 결손을 **더해서** 같은 하루를 2번 셌다.
      둘은 S1에서 같이 붙어 결손도 같이 나므로 한 컬럼으로 센다. 그리고 총계와 행이 **같은 정의**를 쓴다.
    """
    _row(db, "A", d=date(2026, 8, 3), visitors=10, pv=None, orders=None, qty=1)  # S1 이전 행
    _row(db, "A", d=date(2026, 8, 4), visitors=10, pv=100, orders=10, qty=10)
    db.commit()
    r = compute_rocket_1p_funnel(db, date(2026, 8, 3), D)
    assert r["options"][0]["days"] == 2
    assert r["options"][0]["days_missing"] == 1          # 결손난 날은 하루다(2가 아니다)
    # 총계와 행이 같은 것을 센다 — 예전엔 2배 차이로 갈렸다.
    assert r["coverage"]["option_days_missing_metrics"] == r["options"][0]["days_missing"]
    assert r["options"][0]["days_missing"] <= r["options"][0]["days"]


def test_ratios_align_numerator_and_denominator_supports(db):
    """★비율의 지지집합을 맞춘다(적대 리뷰 P1).

    qty는 NOT NULL, orders는 nullable → Σqty/Σorders는 '온전한 분자 ÷ 부분 분모'가 되어 과대.
    반대로 Σpv/Σvisitors는 과소. cvr만 pv·orders가 함께 결손이라 우연히 안전했다.
    """
    # 08-03: 구버전 행(pv·orders 없음) — 이 날의 qty·visitors는 짝이 없다
    _row(db, "A", d=date(2026, 8, 3), visitors=300, pv=None, orders=None, qty=30)
    # 08-04: 온전한 행 — 주문당 1.00개 / 방문자당 2.00회가 진실이다
    _row(db, "A", d=date(2026, 8, 4), visitors=100, pv=200, orders=20, qty=20)
    db.commit()
    o = compute_rocket_1p_funnel(db, date(2026, 8, 3), D)["options"][0]
    assert Decimal(o["units_per_order"]) == Decimal("1.0000")     # 예전엔 50/20 = 2.5
    assert Decimal(o["views_per_visitor"]) == Decimal("2.0000")   # 예전엔 200/400 = 0.5
    assert Decimal(o["cvr"]) == Decimal("0.1000")                 # 원래부터 안전했다
    t = compute_rocket_1p_funnel(db, date(2026, 8, 3), D)["totals"]
    assert Decimal(t["units_per_order"]) == Decimal("1.0000")     # 합계도 같은 규칙
    assert Decimal(t["views_per_visitor"]) == Decimal("2.0000")


def test_freshness_counts_absent_days_not_just_null_columns(db):
    """★행이 통째로 없는 결손은 NULL 카운터로 원리적으로 못 잡는다(적대 리뷰 P1).

    예전 화면은 그 상태를 "결손 없음"이라고 **단언**했다 — green-while-stale.
    """
    _row(db, "A", d=date(2026, 8, 4), visitors=10, pv=100, orders=10, qty=10)
    db.commit()
    r = compute_rocket_1p_funnel(db, date(2026, 8, 1), date(2026, 8, 7))
    f = r["freshness"]
    assert f["days_expected"] == 7 and f["days_with_data"] == 1
    assert f["days_no_data"] == 6           # NULL 카운터는 0이었을 자리다
    assert r["coverage"]["option_days_missing_metrics"] == 0   # 실제로 0 — 둘은 다른 고장이다
    assert f["data_as_of"] == "2026-08-04"
    assert f["lag_days"] == 3


def test_freshness_lag_distinguishes_normal_delay_from_stall(db):
    """원천은 D-1~D-2까지만 준다 — 늘 경고를 띄우면 경보가 죽는다."""
    _row(db, "A", d=date(2026, 8, 4), visitors=10, pv=100, orders=10, qty=10)
    db.commit()
    # 정상 지연(D-2): 경고 아님
    assert compute_rocket_1p_funnel(db, date(2026, 7, 31), date(2026, 8, 6))["freshness"]["stale"] is False
    # 진짜 정지(D-10): 경고
    assert compute_rocket_1p_funnel(db, date(2026, 8, 1), date(2026, 8, 14))["freshness"]["stale"] is True


def test_rating_kept_when_review_count_is_unknown(db):
    """리뷰수 미관측 + 평점 관측 → 평점을 버리지 않는다(적대 리뷰 P2). 0건일 때만 모름."""
    _row(db, "A", visitors=10, pv=20, orders=1, qty=1)
    _attrs(db, "A", rating_count=None, rating_score="4.6")
    db.commit()
    assert Decimal(compute_rocket_1p_funnel(db, D, D)["options"][0]["rating_score"]) == Decimal("4.60")


def test_null_metrics_stay_null_not_zero(db):
    _row(db, "A", visitors=None, pv=None, orders=None, qty=3)
    db.commit()
    o = compute_rocket_1p_funnel(db, D, D)["options"][0]
    assert o["page_views"] is None and o["orders"] is None
    assert o["cvr"] is None and o["views_per_visitor"] is None


# ═══ 기타 계약 ═══
def test_item_winner_is_omitted_with_reason(db):
    """1P 244옵션 전부 False라 열로 내지 않는다 — 정보량 0인 열은 표를 흐린다."""
    _row(db, "A", visitors=10, pv=20, orders=1, qty=1)
    db.commit()
    r = compute_rocket_1p_funnel(db, D, D)
    assert "is_item_winner" in r["omitted_fields"]
    assert "is_item_winner" not in r["options"][0]


def test_sorted_by_page_views_and_limit_tells_truth(db):
    for i in range(5):
        _row(db, f"O{i}", visitors=i + 1, pv=(i + 1) * 10, orders=1, qty=1)
    db.commit()
    r = compute_rocket_1p_funnel(db, D, D, limit=2)
    assert [o["option_id"] for o in r["options"]] == ["O4", "O3"]
    assert r["shown"] == 2 and r["option_count"] == 5


def test_module_is_not_referenced_by_accounting_paths():
    """퍼널 값이 순이익 경로로 새지 않는지 — 참조 자체를 막는다(D-CPP-2)."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    offenders = [
        str(p) for p in root.rglob("*.py")
        if p.name != "rocket_1p_funnel.py" and "rocket_1p_funnel" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"회계/서비스 코드가 퍼널 모듈을 참조한다: {offenders}"
