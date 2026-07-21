# test_naver_searchterm_ss2.py — SS2 판단 SA(search_term_judge) 테스트
# 커버(경계 차등, PLAN §검증 2·§난제): ①전환 보호(purchase≥1이면 절대 후보 불가) ②표본 게이트
#   (clk<10·cost<margin 배제) ③화이트리스트 관통 0 ④margin 부재 fail-closed ⑤rolling 창 경계
#   ⑥파워링크 confirm_required 마킹 ⑦승격 후보(direct 전환).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NaverAdgroupProduct, NaverProductBep, NaverSearchTermDaily
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)


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


def _map_product(db, adgroup_id="grp-1", cpid="P1", margin="500", name="무관템", has_cost=True):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=cpid, has_cost=has_cost,
        product_name=name, selling_price=Decimal("1000"),
        contribution_margin=Decimal(margin), cost_price=0, commission_rate=0, logistics_cost=0,
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=cpid, campaign_id="cmp1"))
    db.commit()


def _term(db, *, term="에어팟케이스", source="shopping", ad_date=date(2026, 7, 20),
          adgroup_id="grp-1", clk=20, cost=6000, pconv=0, dconv=0, pamt=0, imp=100):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id="cmp1", adgroup_id=adgroup_id, search_term=term, source=source,
        imp=imp, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=pconv, conv_direct_cnt=dconv, conv_purchase_amt=pamt, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _all_terms(out):
    return {c["search_term"] for c in out["exclude_candidates"]["shopping"] + out["exclude_candidates"]["powerlink"]}


# ── ① 전환 보호(§1 1) — purchase 전환 1건이면 절대 후보 불가(fail-closed) ──
def test_purchase_conversion_never_becomes_exclude_candidate(db):
    _map_product(db)
    # clk·cost는 표본 게이트를 넘지만 purchase 전환이 1건 → 보호(살아있는 증거)
    _term(db, term="전환검색어", clk=50, cost=9000, pconv=1, dconv=1, pamt=30000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "전환검색어" not in _all_terms(out)
    # 승격 후보엔 들어간다(direct 전환)
    assert any(c["search_term"] == "전환검색어" for c in out["promote_candidates"])


# ── ② 표본 게이트(§1 2) ──
def test_sample_gate_low_click_excluded(db):
    _map_product(db)
    _term(db, term="적은클릭", clk=5, cost=9000)  # clk<10
    out = judge.judge_search_terms(db, now=_NOW)
    assert "적은클릭" not in _all_terms(out)


def test_sample_gate_low_cost_excluded(db):
    _map_product(db, margin="500")
    _term(db, term="적은비용", clk=20, cost=400)  # cost<margin(500)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "적은비용" not in _all_terms(out)


def test_sample_gate_passes_becomes_candidate(db):
    _map_product(db, margin="500")
    _term(db, term="손실검색어", clk=20, cost=6000, pconv=0)
    out = judge.judge_search_terms(db, now=_NOW)
    cands = out["exclude_candidates"]["shopping"]
    assert len(cands) == 1
    assert cands[0]["search_term"] == "손실검색어"
    assert cands[0]["min_cost"] == 500
    assert "[검색어제외]" in cands[0]["reason"]


# ── ③ 화이트리스트(§1 3) 관통 0 ──
def test_whitelist_hardcoded_token_protects(db):
    _map_product(db)
    _term(db, term="아이폰15케이스", clk=50, cost=9000)  # '아이폰' 포함 → 보호
    out = judge.judge_search_terms(db, now=_NOW)
    assert "아이폰15케이스" not in _all_terms(out)


def test_whitelist_from_product_name_protects(db):
    _map_product(db, name="갤럭시 초강력 방탄필름")  # 상품명 토큰 '방탄필름' 등 파생
    _term(db, term="방탄필름추천", clk=50, cost=9000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "방탄필름추천" not in _all_terms(out)


def test_whitelist_english_token_protects_case_insensitive(db):
    # 상품명 토큰 'iPhone'(대문자 포함)이 검색어의 소문자 표기에서도 보호되는지(casefold 정규화).
    # 정규화 없으면 'iPhone' != 'iphone' substring 매칭 실패 → 오컷(§1 3 갭).
    _map_product(db, name="iPhone 자석 카드지갑")  # 상품명 토큰 'iPhone' 파생
    _term(db, term="iphone 케이스 추천", clk=50, cost=9000)  # 소문자 표기
    out = judge.judge_search_terms(db, now=_NOW)
    assert "iphone 케이스 추천" not in _all_terms(out)


# ── ④ margin 부재 fail-closed(§1 2) ──
def test_margin_absent_fail_closed_no_candidate(db):
    # 상품 매핑 없음 → adgroup_unit_price margin=None → 후보 제외(자르지 않음)
    _term(db, term="원가미확인", clk=50, cost=9000, adgroup_id="grp-nomap")
    out = judge.judge_search_terms(db, now=_NOW)
    assert "원가미확인" not in _all_terms(out)


# ── ⑤ rolling 창 경계(§난제 2) ──
def test_rolling_window_excludes_out_of_window(db):
    _map_product(db)
    # 창 밖(as_of=2026-07-22, window=14일 → from=2026-07-09). 7월 1일은 창 밖.
    _term(db, term="오래된검색어", clk=50, cost=9000, ad_date=date(2026, 7, 1))
    out = judge.judge_search_terms(db, now=_NOW)
    assert "오래된검색어" not in _all_terms(out)
    assert out["window"]["from"] == "2026-07-09"
    assert out["window"]["to"] == "2026-07-22"


def test_rolling_window_accumulates_within_window(db):
    _map_product(db)
    # 두 날짜에 clk 6+6=12 → 누적으로 표본 게이트(≥10) 통과
    _term(db, term="누적검색어", clk=6, cost=4000, ad_date=date(2026, 7, 20))
    _term(db, term="누적검색어", clk=6, cost=4000, ad_date=date(2026, 7, 21))
    out = judge.judge_search_terms(db, now=_NOW)
    cands = out["exclude_candidates"]["shopping"]
    assert len(cands) == 1
    assert cands[0]["clk"] == 12 and cands[0]["cost"] == 8000


# ── ⑥ 파워링크 confirm_required 마킹(§난제 5) ──
def test_powerlink_candidate_marked_confirm_required(db):
    _map_product(db, adgroup_id="grp-pl")
    _term(db, term="파워링크검색어", source="expkeyword", adgroup_id="grp-pl", clk=20, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert out["exclude_candidates"]["shopping"] == []
    pl = out["exclude_candidates"]["powerlink"]
    assert len(pl) == 1
    assert pl[0]["confirm_required"] is True


# ── ⑦ 승격 후보(direct 전환) ──
def test_promote_candidate_from_direct_conversion(db):
    _map_product(db)
    _term(db, term="좋은검색어", clk=30, cost=5000, pconv=2, dconv=2, pamt=60000)
    out = judge.judge_search_terms(db, now=_NOW)
    prom = out["promote_candidates"]
    assert len(prom) == 1
    assert prom[0]["search_term"] == "좋은검색어"
    assert prom[0]["conv_direct_cnt"] == 2
