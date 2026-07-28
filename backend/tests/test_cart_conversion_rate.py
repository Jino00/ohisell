# test_cart_conversion_rate.py — D-NAO-58 CD1 상품별 장바구니→구매 전환율 SA 테스트
# 커버: by_product(1:1 adgroup 매핑만), 다상품 adgroup은 by_product 제외·by_campaign 포함,
#   구매>장바구니면 1.0 클램프, 장바구니 0 grain은 키 없음, 창 필터, 파워링크 campaign grain,
#   global 폴백, min_carts 임계, __backfill__ sentinel 이중가산 제외(회귀).
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdDaily, NaverAdgroupProduct
from app.services.naver_ad import cart_conversion_rate
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

AS_OF = date(2026, 7, 15)
IN_WINDOW = date(2026, 7, 10)      # 30일 창 안
OUT_WINDOW = date(2026, 6, 1)      # 창 밖(> 30일 전)


def _ad(ad_date, camp, grp, kw, ctype, cd, ci, cad, cai, ctd, cti, ctad, ctai):
    return NaverAdDaily(
        ad_date=ad_date, campaign_id=camp, campaign_type=ctype, adgroup_id=grp, keyword_id=kw,
        imp=100, clk=1, cost=100, rank_sum=200,
        conv_direct_cnt=cd, conv_indirect_cnt=ci, conv_direct_amt=cad, conv_indirect_amt=cai,
        cart_direct_cnt=ctd, cart_indirect_cnt=cti, cart_direct_amt=ctad, cart_indirect_amt=ctai,
    )


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


def _seed(db):
    # 쇼핑 캠페인 C_SHOP의 그룹들
    # g1 → 상품 "100" (1:1): 구매 3, 장바구니 10 → 0.3
    db.add(_ad(IN_WINDOW, "C_SHOP", "g1", "", "SHOPPING", 2, 1, 20000, 10000, 6, 4, 6000, 4000))
    # g2 → 상품 "200" (1:1): 구매 8, 장바구니 5 → 1.6 → clamp 1.0
    db.add(_ad(IN_WINDOW, "C_SHOP", "g2", "", "SHOPPING", 8, 0, 80000, 0, 5, 0, 5000, 0))
    # g3 → 상품 "300","301" (다상품): by_product 제외, campaign엔 포함. 구매 1, 장바구니 2
    db.add(_ad(IN_WINDOW, "C_SHOP", "g3", "", "SHOPPING", 1, 0, 9000, 0, 2, 0, 2000, 0))
    # g4 → 상품 "400" (1:1) 이지만 장바구니 0 → by_product 키 없음
    db.add(_ad(IN_WINDOW, "C_SHOP", "g4", "", "SHOPPING", 0, 0, 0, 0, 0, 0, 0, 0))
    # 창 밖 g1 행(창 필터 검증용, 반영되면 안 됨)
    db.add(_ad(OUT_WINDOW, "C_SHOP", "g1", "", "SHOPPING", 999, 0, 0, 0, 999, 0, 0, 0))
    # 파워링크 캠페인(상품 매핑 없음) → by_campaign만. 구매 1, 장바구니 4 → 0.25
    db.add(_ad(IN_WINDOW, "C_PL", "gp1", "nkw-1", "WEB_SITE", 1, 0, 5000, 0, 4, 0, 4000, 0))

    # adgroup↔product 매핑
    db.add(NaverAdgroupProduct(adgroup_id="g1", campaign_id="C_SHOP", mall_product_id="100"))
    db.add(NaverAdgroupProduct(adgroup_id="g2", campaign_id="C_SHOP", mall_product_id="200"))
    db.add(NaverAdgroupProduct(adgroup_id="g3", campaign_id="C_SHOP", mall_product_id="300"))
    db.add(NaverAdgroupProduct(adgroup_id="g3", campaign_id="C_SHOP", mall_product_id="301"))  # 다상품
    db.add(NaverAdgroupProduct(adgroup_id="g4", campaign_id="C_SHOP", mall_product_id="400"))
    db.commit()


def test_by_product_one_to_one_and_clamp(db):
    _seed(db)
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    bp = res["by_product"]
    assert bp["100"] == pytest.approx(0.3)     # 3/10
    assert bp["200"] == pytest.approx(1.0)     # 8/5 → clamp
    # 다상품 adgroup(g3→300/301)은 by_product에서 제외
    assert "300" not in bp and "301" not in bp
    # 장바구니 0 상품(400)은 키 없음
    assert "400" not in bp


def test_window_filtering_excludes_old_rows(db):
    _seed(db)
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    # 창 밖 g1 행(장바구니 999)이 반영됐다면 100 rate가 크게 흔들렸을 것 → 0.3 유지 확인
    assert res["by_product"]["100"] == pytest.approx(0.3)
    assert res["window"]["from"] == "2026-06-15" and res["window"]["to"] == "2026-07-15"


def test_by_campaign_includes_powerlink_and_multi_product(db):
    _seed(db)
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    bc = res["by_campaign"]
    # C_SHOP: 구매 3+8+1+0=12, 장바구니 10+5+2+0=17 → 0.7059 (g3 다상품도 캠페인엔 포함)
    assert bc["C_SHOP"] == pytest.approx(12 / 17, abs=1e-4)
    # 파워링크는 campaign grain으로 해소
    assert bc["C_PL"] == pytest.approx(0.25)


def test_global_fallback_and_shape(db):
    _seed(db)
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    # global: 구매 13, 장바구니 21 → 0.619
    assert res["global"] == pytest.approx(13 / 21, abs=1e-4)
    assert set(res.keys()) == {"by_product", "by_campaign", "global", "window"}


def test_min_carts_threshold(db):
    _seed(db)
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF, min_carts=6)
    bp = res["by_product"]
    assert "100" in bp          # 10 carts ≥ 6
    assert "200" not in bp      # 5 carts < 6
    bc = res["by_campaign"]
    assert "C_SHOP" in bc       # 17 carts ≥ 6
    assert "C_PL" not in bc     # 4 carts < 6


def test_empty_db_global_none(db):
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    assert res["by_product"] == {} and res["by_campaign"] == {}
    assert res["global"] is None


def test_backfill_sentinel_not_double_counted(db):
    # 회귀: __backfill__ sentinel(/stats 소스)의 conv_indirect_cnt는 구매+장바구니 전량 합계라
    # 상세 행과 더하면 분자만 이중가산된다(prod 실측 global 1.675 → 3.998).
    _seed(db)
    # 항등식(센티널 = 상세conv + 상세cart)대로 심기. cart_*=0 이므로 분모는 안 늘고 분자만 늘어남
    db.add(_ad(IN_WINDOW, "C_SHOP", BACKFILL_SENTINEL_ADGROUP, "", "SHOPPING",
               0, 12 + 17, 0, 300000, 0, 0, 0, 0))
    db.add(_ad(IN_WINDOW, "C_PL", BACKFILL_SENTINEL_ADGROUP, "", "WEB_SITE",
               0, 1 + 4, 0, 50000, 0, 0, 0, 0))
    db.commit()
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    # 미제외 시: C_SHOP (12+29)/17=2.41→clamp 1.0, C_PL (1+5)/4=1.5→clamp 1.0, global 2.24→1.0
    assert res["by_campaign"]["C_SHOP"] == pytest.approx(12 / 17, abs=1e-4)
    assert res["by_campaign"]["C_PL"] == pytest.approx(0.25)
    assert res["global"] == pytest.approx(13 / 21, abs=1e-4)
    assert res["by_product"]["100"] == pytest.approx(0.3)


def test_backfill_sentinel_excluded_from_by_product_even_if_mapped(db):
    # by_product는 sentinel이 adgroup 매핑에 없어 우연히 안전하지만, 매핑이 생겨도 막혀야 한다
    _seed(db)
    db.add(_ad(IN_WINDOW, "C_SHOP", BACKFILL_SENTINEL_ADGROUP, "", "SHOPPING",
               0, 29, 0, 300000, 0, 0, 0, 0))
    db.add(NaverAdgroupProduct(adgroup_id=BACKFILL_SENTINEL_ADGROUP, campaign_id="C_SHOP",
                               mall_product_id="100"))
    db.commit()
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    assert res["by_product"]["100"] == pytest.approx(0.3)   # 29 가산되면 (3+29)/10 → clamp 1.0


def test_zero_cart_grain_no_key(db):
    # 구매만 있고 장바구니 0인 상품/캠페인 → 어떤 grain에도 키 없음, global None
    db.add(_ad(IN_WINDOW, "C_ONLY", "gz", "", "SHOPPING", 5, 0, 50000, 0, 0, 0, 0, 0))
    db.add(NaverAdgroupProduct(adgroup_id="gz", campaign_id="C_ONLY", mall_product_id="900"))
    db.commit()
    res = cart_conversion_rate.cart_conversion_rates(db, window_days=30, as_of=AS_OF)
    assert "900" not in res["by_product"]
    assert "C_ONLY" not in res["by_campaign"]
    assert res["global"] is None  # 전체 장바구니 0
