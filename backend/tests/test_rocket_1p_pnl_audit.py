# test_rocket_1p_pnl_audit.py — 손익 «근거 화면» SA (2026-08-07 설계 승인)
#
# 이 파일이 **지금** 지키는 것:
#   ① 원자(day_option_atoms)는 파생의 단일 출처다 — 원자의 합이 화면 타일과 맞고,
#      맞지 않는 유일한 항(판매 없는 옵션의 광고비)은 **크기까지 못 박혀 있다**
#   ①' `burden_known=False`(분담금 모름)면 원자 net이 전부 None이다 — 0으로 접지 않는다
#
# 아직 지키지 않는 것 (후속 태스크에서 추가 — 지금 적으면 거짓 초록이다):
#   ② 검사는 «같은 함수의 다른 그레인»을 비교한다 — 재계산이 아니다
#   ③ B1은 절대 pass가 되지 않는다 — 판정할 수 없는 검사를 초록으로 칠하면 거짓 초록
#   ④ A5·A6·A7은 조용한 결손(INNER JOIN 탈락·분담금 모름·광고 미귀속)을 드러낸다
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text as _t
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (Channel, CoupangAdOptionDaily, CoupangAdReport,
                        CoupangRocketPurchaseOrderItem, CoupangRocketSalesDaily)
from app.services.coupang import rocket_1p_channel_pnl as pnl
from app.services.coupang.rocket_1p_revenue import (
    _money, compute_rocket_1p_revenue, day_option_atoms)

VENDOR = pnl.ROCKET_1P_VENDOR_ID
ZERO_D = Decimal("0")
D = date(2026, 8, 4)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Channel(id=5, code="COUPANG_ROCKET", name="쿠팡 로켓배송", platform="coupang",
                  channel_type="consignment", company="주식회사 오하이테크"))
    s.commit()
    yield s
    s.close()


def _sale(s, option_id, sku, qty, consumer, *, d=D):
    s.add(CoupangRocketSalesDaily(
        vendor_id=VENDOR, option_id=option_id, sku_id=sku, date=d,
        qty=qty, revenue=Decimal(consumer),
        product_name=f"상품 {option_id}", source="sales_analysis"))


def _price(s, sku, unit_price, seq):
    s.add(CoupangRocketPurchaseOrderItem(
        purchase_order_seq=seq, vendor_id=VENDOR, product_number=sku,
        unit_purchase_price=Decimal(unit_price), order_qty=1))


def _cost(s, sku, cost_price, internal_sku=None, match_method=None):
    isku = internal_sku or f"OHI-{sku}"
    s.execute(_t("INSERT INTO product_master (internal_sku, product_name, cost_price) "
                 "VALUES (:i, :n, :c)"), {"i": isku, "n": isku, "c": cost_price})
    s.execute(_t("INSERT INTO rocket_product_cost_map "
                 "(product_number, internal_sku, status, match_method) "
                 "VALUES (:p, :i, 'confirmed', :m)"),
              {"p": str(sku), "i": isku, "m": match_method})


def _ad_option(s, option_id, spend, d=D):
    s.add(CoupangAdOptionDaily(
        report_date=d, vendor_id=VENDOR, sell_type="Retail",
        ad_option_id=option_id, conv_option_id=option_id,
        impressions=0, clicks=0, ad_spend=Decimal(spend),
        orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


def _ad_account(s, spend, d=D):
    s.add(CoupangAdReport(report_date=d, sell_type="Retail", vendor_id=VENDOR,
                          impressions=0, clicks=0, ad_spend=Decimal(spend),
                          orders=0, sales_qty=0, conversion_revenue=Decimal("0")))


# ═══ ① 원자의 합 = 화면 타일 (원자는 파생의 단일 출처) ═══


def test_atoms_sum_to_screen_tile(db):
    """Σ원자 순이익 = compute_rocket_1p_revenue의 pnl 타일. 원자를 따로 계산하지 않았다는 증거.

    ★등식은 **무조건 참이 아니다**: 「그 창에 판매행이 없는 옵션의 광고비」가 **basis='full'
      일 때만** 타일에서 세후로 추가 차감되어 잔차가 남는다. 성립을 정하는 술어는 그 돈의
      크기가 아니라 `ad_no_sales_included`다 — False면 그 돈이 0이 아니어도 잔차가 없다.
      여기선 그 항이 0인 조건을 만들어 두고(단언까지 한다) 순수한 접기만 검사한다 —
      잔차가 있는 경우는 아래 델타 테스트가 included=True를 단언한 뒤 크기까지 못 박는다.
    ★2옵션 × 2일로 둔다 — 합이 **한 항**이면 접기가 발산해도 관측할 수 없다.
    """
    for i, sku in enumerate(("S1", "S2"), 1):
        _price(db, sku, str(60000 + i * 1111), i)
        _cost(db, sku, 20000 + i * 337)
        for dnum, day in enumerate((date(2026, 8, 3), D)):
            _sale(db, f"O{i}", sku, 7 + i + dnum, str(900000 + i * 1000 + dnum * 700), d=day)
            _ad_option(db, f"O{i}", str(9000 + i * 101 + dnum * 17), d=day)
    _ad_account(db, "40000")
    db.commit()
    ctx = day_option_atoms(db, date(2026, 8, 3), D)
    r = compute_rocket_1p_revenue(db, date(2026, 8, 3), D)
    assert len(ctx["atoms"]) == 4                  # 2옵션 × 2일 — 합이 한 항이 아니다
    assert r["pnl"]["ad_no_sales"] == "0"          # 잔차 항이 없는 조건에서만 등식이다
    atom_sum = sum((a["net_profit"] for a in ctx["atoms"] if a["net_profit"] is not None), ZERO_D)
    assert str(atom_sum) == r["pnl"]["net_profit"]
    assert ctx["burden_known"] is True


def test_atom_sum_minus_tile_is_exactly_the_unattributable_ad(db):
    """★★「Σ원자 = 타일」은 라이브에서 **거짓**이다 — 그 차이의 정체와 크기를 못 박는다.

    basis='full'이면 그 창에 판매행이 없는 옵션의 광고비(`ad_no_sales`)가 타일에서 **세후로
    추가 차감**된다. 귀속할 원자가 아예 없는 돈이라 어떤 원자에도 실리지 않는다.
    계획 단계 라이브 실측(8/1~7) 253,091원 — 픽스처에 그런 옵션이 없으면 이 잔차가 보이지
    않아 "Σ = 타일"이 참인 것처럼 통과한다. 그게 이 테스트가 있는 이유다.
    후속 태스크의 A7 검사가 **바로 이 잔차의 존재 위에** 세워지므로 등식으로 고정해 둔다.
    """
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)              # 커버리지 100% → basis='full'
    _ad_option(db, "A", "10000")
    _ad_option(db, "GHOST", "50000")    # 광고만 돌고 그 창엔 판매행이 없다
    db.commit()
    ctx = day_option_atoms(db, D, D)
    p = compute_rocket_1p_revenue(db, D, D)["pnl"]
    atom_sum = sum((a["net_profit"] for a in ctx["atoms"] if a["net_profit"] is not None), ZERO_D)
    assert p["basis"] == "full" and p["ad_no_sales_included"] is True
    delta = atom_sum - Decimal(p["net_profit"])
    # 잔차 = 판매 없는 옵션 광고비의 세후분. **0이 아니다** — 등식이 거짓이라는 증거다.
    assert delta == _money(Decimal(p["ad_no_sales"]) * Decimal("100") / Decimal("110"))
    assert delta > ZERO_D
    # ★그 돈은 원자 어디에도 없다 — 광고 맵은 원자의 **상위집합**이다.
    assert sum((a["ad_spend"] or ZERO_D) for a in ctx["atoms"]) == Decimal("10000")
    assert sum(ctx["ad_by_option"].values()) == Decimal("60000")


# ═══ ①' 분담금 «모름» — 0으로 접지 않는다 (그리고 그것은 창에 의존한다) ═══


def test_unknown_promo_burden_makes_every_atom_net_unknown(db):
    """★`burden_known`의 존재 이유는 «모름»이다 — False면 원자 net이 전부 None이다.

    제안서(할인액 원천)가 없는 프로모션이 창에 걸치면 분담금을 모른다. 0으로 접으면 그
    할인액만큼 이익이 부풀어 보이므로 손익 자체를 내지 않는다.

    ★★그리고 이 값은 **창-종속**이다 — 같은 원자라도 그 프로모션이 창 밖이면 net이 나온다.
      그래서 호출자는 화면과 **같은 창**으로 불러야 한다: 하루치만 보려고 창을 좁히면
      화면이 «—»(모름)로 그린 행에 숫자가 찍힌다. 넓은 창으로 부른 뒤 **거르는** 게 옳다.
      (day_option_atoms docstring의 창 계약 — 후속 태스크의 원자 상세 API가 지켜야 한다.)
    """
    _sale(db, "A", "S1", 10, "1000000")
    _price(db, "S1", "60000", 1)
    _cost(db, "S1", 20000)
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-02 23:59:59')"),
               {"v": VENDOR})   # 할인액(coupang_promo_discount_item) 없음 = 모름
    db.commit()
    wide = day_option_atoms(db, date(2026, 8, 1), D)      # 프로모션이 창에 걸친다
    assert wide["burden_known"] is False
    # 그 프로모션과 무관한 날(8/4)의 원자까지 net이 없다 — 0이 아니라 «모름»이다.
    assert [a["net_profit"] for a in wide["atoms"]] == [None]
    assert [a["promo_burden"] for a in wide["atoms"]] == [None]

    narrow = day_option_atoms(db, D, D)                   # 프로모션이 창 밖이다
    assert narrow["burden_known"] is True
    assert narrow["atoms"][0]["net_profit"] is not None
    # ★창-불변 축은 그대로다 — 창에 따라 갈리는 것은 분담금 축뿐이라는 증거.
    assert wide["atoms"][0]["our_revenue"] == narrow["atoms"][0]["our_revenue"]
    assert wide["atoms"][0]["cost"] == narrow["atoms"][0]["cost"]


# ═══ ② A6의 원료 — 창에 걸친 프로모션 수와 할인액 없는 수 ═══


def test_promo_window_counts(db):
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('P1', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"), {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('P2', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"), {"v": VENDOR})
    db.execute(_t("INSERT INTO coupang_promo_discount_item "
                  "(request_id, product_number, discount_type, discount_value) "
                  "VALUES ('P1', 'S1', 'FIXED', 1500)"))
    db.commit()
    c = pnl.promo_window_counts(db, D, D)
    assert c == {"promos": 2, "unpriced": 1}


def test_promo_window_counts_excludes_promos_outside_window(db):
    """조회 창과 안 겹치는 프로모션은 세지 않는다 — A6은 좌·우변이 같은 창(모집단)이라야
    성립하는 검사라, `promos`가 창 밖까지 세면 그 전제부터 깨진다."""
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('OUT', :v, '2026-09-01 00:00:00', '2026-09-15 23:59:59')"), {"v": VENDOR})
    db.commit()
    c = pnl.promo_window_counts(db, D, D)
    assert c == {"promos": 0, "unpriced": 0}


def test_promo_window_counts_zero_promos_is_not_unknown(db):
    """프로모션이 창에 하나도 없으면 `{"promos": 0, "unpriced": 0}` — 이건 «모름»(테이블
    없음=None)이 아니라 «프로모션이 없었다»는 사실이다. A6이 이 경우를 pass로 판정할
    근거가 바로 이 구분이다."""
    c = pnl.promo_window_counts(db, D, D)
    assert c is not None
    assert c == {"promos": 0, "unpriced": 0}
