# test_rocket_1p_pnl_audit.py — 손익 «근거 화면» SA (2026-08-07 설계 승인)
#
# 이 파일이 **지금** 지키는 것:
#   ① 원자(day_option_atoms)는 파생의 단일 출처다 — 원자의 합이 화면 타일과 맞고,
#      맞지 않는 유일한 항(판매 없는 옵션의 광고비)은 **크기까지 못 박혀 있다**
#   ①' `burden_known=False`(분담금 모름)면 원자 net이 전부 None이다 — 0으로 접지 않는다
#   ② 검사는 «같은 함수의 다른 그레인»을 비교한다 — 재계산이 아니다
#      (사다리는 `compute_rocket_1p_revenue` 응답을 **그대로** 싣는다: ladder == 화면 pnl)
#   ③ B1은 절대 pass가 되지 않는다 — 판정할 수 없는 검사를 초록으로 칠하면 거짓 초록
#   ④ A5·A6·A7은 조용한 결손(INNER JOIN 탈락·분담금 모름·광고 미귀속)을 드러낸다
#
# 아직 지키지 않는 것 (후속 태스크에서 추가 — 지금 적으면 거짓 초록이다):
#   ⑤ 원천 행 드릴다운(원자 목록·원자 상세 API)과 그 창 계약
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
# `_full_fixture`의 첫째 날. 원자를 **2옵션 × 2일**로 두려고 있다 — 합이 한 항이면
# 접기가 발산해도 관측할 수 없다. 검사 창은 (D_PREV, D).
D_PREV = date(2026, 8, 3)


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


# ═══ ③ 검사 — 같은 함수의 다른 그레인 비교, 재계산 아님 ═══


def _audit(db, dfrom, dto, *, limit=None):
    """라우터가 할 일을 테스트가 대신한다 — 화면 응답을 만들어 **주입**한다.

    ★검사 SA는 화면 함수를 부르지 않는다(부를 수 없다) — 아래
      `test_audit_service_does_not_reference_the_revenue_module`가 그 계약을 지킨다.
      그래서 「근거 창은 계산을 새로 하지 않는다」가 규칙이 아니라 구조다.
    ★`limit`은 `ATOM_LIMIT`이 기본이다 — 라우터도 이 값을 써야 한다(자기 숫자를 쓰면
      「옵션 표가 잘리면 undetermined」 계약이 두 곳에 흩어진다).
    """
    from app.services.coupang.rocket_1p_pnl_audit import (ATOM_LIMIT,
                                                          compute_pnl_audit_checks)
    screen = compute_rocket_1p_revenue(db, dfrom, dto, None, limit or ATOM_LIMIT)
    return compute_pnl_audit_checks(db, dfrom, dto, screen)


def test_audit_service_does_not_reference_the_revenue_module():
    """★검사 SA는 매출 화면 모듈을 **문자열로도** 담지 않는다 — D-CPP-2 가드가 이 파일에도
    살아 있다는 것을 여기서 못 박는다.

    왜 이 테스트가 따로 있나: 상위 가드(`test_rocket_1p_revenue.py`의
    `test_module_is_not_referenced_by_accounting_paths`)는 `app/services/` 전체를 훑으므로
    이 파일도 이미 덮는다. 그런데 그 가드가 깨지면 **어느 파일이 원인인지**가 아니라 목록만
    나오고, 무엇보다 «왜 이 모듈만 주입 방식인가»의 근거가 코드 어디에도 안 남는다.
    나중에 누가 "부르는 게 편한데"라며 import를 되살리면 여기서 이유와 함께 걸린다.

    ★가드는 import가 아니라 **원시 문자열 포함**을 본다 — 주석·docstring에 모듈명을 적어도
      걸린다. 그래서 그 파일은 화면 모듈을 이름 대신 «1P 매출·손익 화면 SA»로 부른다.
    """
    import pathlib
    from app.services.coupang import rocket_1p_pnl_audit as audit
    src = pathlib.Path(audit.__file__).read_text(encoding="utf-8")
    banned = "rocket_1p" + "_revenue"      # 이 테스트 파일 자신이 걸리지 않게 쪼개 둔다
    assert banned not in src, "검사 SA가 매출 화면 모듈을 참조한다 — 주입 계약이 깨졌다"


def test_audit_rejects_a_screen_from_a_different_window(db):
    """★주입 방식의 유일한 새 위험: 라우터가 **다른 창**의 화면을 넘기는 것.

    숫자는 그럴듯해서 아무도 눈치채지 못한다 — 특히 분담금 가드가 창-종속이라 창이 좁으면
    «모름»이 숫자로 바뀐다(`day_option_atoms` 창 계약). 그래서 조용히 대조하지 않고 죽는다.
    """
    from app.services.coupang.rocket_1p_pnl_audit import compute_pnl_audit_checks
    _full_fixture(db)
    screen = compute_rocket_1p_revenue(db, D, D)          # 하루 창
    with pytest.raises(ValueError, match="창이 다릅니다"):
        compute_pnl_audit_checks(db, D_PREV, D, screen)   # 이틀 창으로 대조 시도


def _full_fixture(db):
    """A1~A7 전부 판정 가능한 최소 데이터 — **2옵션 × 2일**(원자 4개).

    ★1옵션×1일이면 A1·A2의 «합»이 각각 한 항이라, 접기가 발산해도 등식이 그대로 성립한다.
      옵션 축과 날짜 축이 **서로 다른 묶음**이 되도록 넷으로 둔다.
    ★A1~A7이 전부 pass가 되는 조건: 전 SKU에 납품단가(A5)와 원가(A4)가 있고,
      광고를 쓴 (날짜,옵션)마다 판매행이 있으며(A7), 창에 프로모션이 없다(A6).
    """
    for i, (oid, sku) in enumerate((("O1", "S1"), ("O2", "S2")), 1):
        _price(db, sku, str(60000 + i * 1111), i)
        _cost(db, sku, 20000 + i * 337)
        for dnum, day in enumerate((D_PREV, D)):
            _sale(db, oid, sku, 7 + i + dnum, str(900000 + i * 1000 + dnum * 700), d=day)
            _ad_option(db, oid, str(9000 + i * 101 + dnum * 17), d=day)
    _ad_account(db, "40000")
    db.commit()


def test_checks_pass_and_ladder_matches_screen(db):
    _full_fixture(db)
    r = _audit(db, D_PREV, D)
    by = {c["id"]: c for c in r["checks"]}
    for cid in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "B2"):
        assert by[cid]["verdict"] == "pass", (cid, by[cid])
        # ★통과해도 좌·우변 숫자를 싣는다 — 발견 0건과 실행 안 됨은 같은 숫자로 보인다
        assert by[cid]["left"] is not None and by[cid]["right"] is not None
    scr = compute_rocket_1p_revenue(db, D_PREV, D)
    assert r["ladder"]["net_profit"] == scr["pnl"]["net_profit"]
    # ★사다리는 **화면 응답 그대로**다 — 근거 창이 자기 계산을 하면 두 계산이 된다.
    for k, v in r["ladder"].items():
        assert v == scr["pnl"][k], k


def test_a1_a2_fold_over_more_than_one_term(db):
    """★합이 한 항이면 등식이 아무것도 검사하지 않는다 — 두 축이 실제로 여럿인지 못 박는다."""
    _full_fixture(db)
    scr = compute_rocket_1p_revenue(db, D_PREV, D)
    assert len(scr["daily"]) == 2 and len(scr["options"]) == 2
    r = _audit(db, D_PREV, D)
    by = {c["id"]: c for c in r["checks"]}
    # 두 축은 서로 다른 묶음인데 같은 우변으로 수렴한다 — 그게 검사의 내용이다.
    assert by["A1"]["left"] == by["A2"]["left"] == by["A1"]["right"]
    # ★diff는 Decimal의 **자릿수를 보존한 문자열**이라 0이어도 "0.00"이다 — 화면이 문자열
    #   비교로 «차이 없음»을 판정하면 안 된다. 판정은 verdict가 하고 diff는 크기만 말한다.
    assert Decimal(by["A1"]["diff"]) == ZERO_D and by["A1"]["diff"] == "0.00"


def test_a3_discloses_that_vat_is_a_residual(db):
    """★A3은 **동어반복**이다 — 숨기면 그게 거짓 초록이다.

    `compute_rocket_1p_revenue`의 `pnl_vat`는 매출−원가−분담금−광고−순이익의 **잔차**로
    계산된다(rocket_1p_revenue.py `pnl_vat = pnl_revenue - ... - pnl_net_total`). 그래서
    A3의 좌변은 대수적으로 항상 순이익과 같고, **오늘의 구현에선 fail이 날 수 없다.**
    검사를 지우지 않는 이유는 부가세가 독립 계산으로 바뀌면 그때부터 진짜 검사가 되기
    때문이고, 남겨 두는 조건은 그 사실을 note에 적는 것이다.
    """
    _full_fixture(db)
    r = _audit(db, D_PREV, D)
    a3 = next(c for c in r["checks"] if c["id"] == "A3")
    assert a3["verdict"] == "pass"
    assert "잔차" in (a3["note"] or "")


def test_b1_never_passes_even_when_equal(db):
    """★판정할 수 없는 검사를 초록으로 칠하지 않는다 — 값이 우연히 같아도."""
    _full_fixture(db)
    r = _audit(db, D_PREV, D)
    b1 = next(c for c in r["checks"] if c["id"] == "B1")
    assert b1["verdict"] == "undetermined"
    # 값이 같아도(둘 다 계산서 0 / 판매 축 값) pass가 아니다 — 그 사실을 여기서 못 박는다.
    assert b1["diff"] is not None or b1["left"] is None


def test_a2_and_a7_are_undetermined_when_option_table_is_truncated(db):
    """★옵션 표가 잘리면 «합»을 낼 수 없다 — 그때 pass를 내면 거짓 초록이다.

    A1(날짜 축)은 잘리지 않으므로 그대로 pass다. 두 축의 판정이 갈리는 것이 정상이고,
    잘림이 A2·A7에만 영향을 준다는 사실 자체가 이 테스트의 내용이다.

    ★잘림은 **주입된 화면 응답의 성질**이다(`shown < option_count`) — 검사 SA가 스스로
      만들 수 없으므로 라우터가 작은 limit으로 부른 상황을 그대로 재현한다.
    """
    _full_fixture(db)
    r = _audit(db, D_PREV, D, limit=1)
    by = {c["id"]: c for c in r["checks"]}
    assert by["A2"]["verdict"] == "undetermined" and by["A2"]["left"] is None
    assert by["A7"]["verdict"] == "undetermined" and by["A7"]["left"] is None
    assert by["A1"]["verdict"] == "pass"


def test_a5_surfaces_silent_inner_join_loss(db):
    """발주 이력 없는 SKU는 손익 매출에서 조용히 빠진다 — A5가 그 수량을 드러낸다."""
    _full_fixture(db)
    _sale(db, "B", "S9", 5, "500000")   # 발주 이력 없음 → INNER JOIN 탈락
    db.commit()
    r = _audit(db, D_PREV, D)
    a5 = next(c for c in r["checks"] if c["id"] == "A5")
    assert a5["verdict"] == "fail"
    assert a5["left"] == "36" and a5["right"] == "41"


def test_a6_unpriced_promo_fails_and_a1_undetermined(db):
    """분담금 모름 → A6 fail, 손익 자체가 없으므로 A1~A3은 undetermined."""
    _full_fixture(db)
    db.execute(_t("INSERT INTO coupang_rocket_promotion (request_id, vendor_id, start_at, end_at) "
                  "VALUES ('686180', :v, '2026-08-01 00:00:00', '2026-08-15 23:59:59')"),
               {"v": VENDOR})
    db.commit()
    r = _audit(db, D_PREV, D)
    by = {c["id"]: c for c in r["checks"]}
    assert by["A6"]["verdict"] == "fail"
    assert by["A1"]["verdict"] == "undetermined"
    assert by["A3"]["verdict"] == "undetermined"
    # ★막힌 이유를 note가 말한다 — "검사 안 됨"과 "검사해서 통과"를 화면이 갈라야 한다.
    assert "promo_burden_unknown" in (by["A1"]["note"] or "")


def test_a7_catches_ad_on_no_sales_day_of_sold_option(db):
    """★창 내 판매행이 있는 옵션이 «판매 없는 날»에 쓴 광고비 — 원자에도 ad_no_sales에도
    귀속되지 않는다(prod 실측 7일 창 435,916원). A7이 이 결손을 드러낸다."""
    _full_fixture(db)
    _ad_option(db, "O1", "5000", d=date(2026, 8, 5))   # 8/5 광고, 그날 판매행 없음
    db.commit()
    r = _audit(db, D_PREV, date(2026, 8, 5))
    a7 = next(c for c in r["checks"] if c["id"] == "A7")
    assert a7["verdict"] == "fail"
    assert Decimal(a7["right"]) - Decimal(a7["left"]) == Decimal("5000")
