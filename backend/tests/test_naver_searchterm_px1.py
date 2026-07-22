# test_naver_searchterm_px1.py — PX1 파워링크 전용 게이트(search_term_judge._judge_powerlink) 테스트
# 커버(PLAN_naver-ad-powerlink-autoexclude.md §1, GATE): ⓪스코프(auto_operate) 누출 0 ①창 30일
#   ②clk≥5 ③cost≥margin·margin 폴백 ④그룹 순손실 프록시 ⑤디바이스 토큰 제외 화이트리스트
#   ⑥전환 보호 ⑦sentinel 제외 ⑧대행사 브리핑(agency_powerlink) — 쇼핑/승격 회귀는 ss2/ss4가 고정.
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverCampaignSettings,
    NaverEntity,
    NaverProductBep,
    NaverSearchTermDaily,
)
from app.services.naver_ad import search_term_judge as judge
from app.services.naver_ad.campaign_target_resolver import NAVER_CHANNEL_ID

_NOW = datetime(2026, 7, 22, 9, 0, 0)  # 파워링크 창 30일 → from=2026-06-23


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


def _settings(db, campaign_id="cmp1", auto_operate=True, optimizer="ours"):
    db.add(NaverCampaignSettings(campaign_id=campaign_id, optimizer=optimizer, auto_operate=auto_operate))
    db.commit()


def _product(db, *, adgroup_id="grp-web", cpid="P1", margin="500", name="필름 정품",
             target_roas="2.0", has_cost=True, campaign_id="cmp1"):
    db.add(NaverProductBep(
        channel_id=NAVER_CHANNEL_ID, channel_product_id=cpid, has_cost=has_cost,
        product_name=name, selling_price=Decimal("1000"), contribution_margin=Decimal(margin),
        cost_price=0, commission_rate=0, logistics_cost=0,
        target_roas=(Decimal(target_roas) if target_roas is not None else None),
        bep_roas=Decimal("1.5"),
    ))
    db.add(NaverAdgroupProduct(adgroup_id=adgroup_id, mall_product_id=cpid, campaign_id=campaign_id))
    db.commit()


def _pl_term(db, *, term, adgroup_id="grp-web", clk=10, cost=6000, pconv=0,
             ad_date=date(2026, 7, 10), campaign_id="cmp1"):
    db.add(NaverSearchTermDaily(
        ad_date=ad_date, campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=term,
        source="expkeyword", imp=100, clk=clk, cost=cost, rank_sum=0,
        conv_purchase_cnt=pconv, conv_direct_cnt=0, conv_purchase_amt=0, cart_cnt=0, cart_amt=0,
    ))
    db.commit()


def _group_perf(db, *, adgroup_id="grp-web", cost=10000, conv_amt=1000,
                ad_date=date(2026, 7, 10), campaign_id="cmp1", keyword_id="nkw-1"):
    """gate ④ 그룹 순손실 프록시용 naver_ad_daily 행 — (직+간 전환매출)/cost 판정."""
    db.add(NaverAdDaily(
        ad_date=ad_date, campaign_id=campaign_id, campaign_type="WEB_SITE", adgroup_id=adgroup_id,
        keyword_id=keyword_id, imp=500, clk=50, cost=cost, rank_sum=0,
        conv_direct_cnt=0, conv_indirect_cnt=0, conv_direct_amt=conv_amt, conv_indirect_amt=0,
    ))
    db.commit()


def _pl(out):
    return {c["search_term"] for c in out["exclude_candidates"]["powerlink"]}


def _agency(out):
    return {c["search_term"] for c in out["agency_powerlink"]}


# ── ⓪ 스코프 누출 0: auto_operate=1 안이면 후보, 밖(대행사)이면 자동 후보 절대 아님 ──
def test_in_scope_powerlink_becomes_candidate(db):
    _settings(db, auto_operate=True)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)  # roas 0.1 < 2.0 = 순손실
    _pl_term(db, term="싼검색어", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "싼검색어" in _pl(out)
    assert out["agency_powerlink"] == []


def test_out_of_scope_high_cost_goes_to_agency_never_powerlink(db):
    _settings(db, auto_operate=False)  # 대행사(비 auto_operate)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="대행사고비용", adgroup_id="grp-web", clk=12, cost=40000)  # ≥30,000·≥10
    out = judge.judge_search_terms(db, now=_NOW)
    assert _pl(out) == set()  # 자동 발사 후보 0(스코프 누출 없음)
    assert "대행사고비용" in _agency(out)


def test_out_of_scope_low_cost_not_even_agency(db):
    _settings(db, auto_operate=False)
    _product(db, adgroup_id="grp-web", margin="500")
    _pl_term(db, term="대행사저비용", adgroup_id="grp-web", clk=12, cost=6000)  # <30,000
    out = judge.judge_search_terms(db, now=_NOW)
    assert _pl(out) == set()
    assert _agency(out) == set()


# ── ① 창 30일 경계 ──
def test_window_30d_includes_20_days_ago(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000, ad_date=date(2026, 7, 2))
    _pl_term(db, term="20일전", adgroup_id="grp-web", clk=10, cost=6000, ad_date=date(2026, 7, 2))
    out = judge.judge_search_terms(db, now=_NOW)
    assert "20일전" in _pl(out)


def test_window_30d_excludes_out_of_window(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000, ad_date=date(2026, 6, 1))
    _pl_term(db, term="창밖", adgroup_id="grp-web", clk=10, cost=6000, ad_date=date(2026, 6, 1))
    out = judge.judge_search_terms(db, now=_NOW)
    assert "창밖" not in _pl(out)


# ── ② clk≥5 경계(쇼핑 10과 분리) ──
def test_min_click_boundary(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="클릭4", adgroup_id="grp-web", clk=4, cost=6000)   # <5 배제
    _pl_term(db, term="클릭5", adgroup_id="grp-web", clk=5, cost=6000)   # =5 통과
    out = judge.judge_search_terms(db, now=_NOW)
    assert "클릭4" not in _pl(out)
    assert "클릭5" in _pl(out)


# ── ③ cost≥margin / margin 폴백 ──
def test_cost_below_margin_excluded(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="8000")  # min_cost=8,000
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="저비용", adgroup_id="grp-web", clk=10, cost=6000)  # <8,000 배제
    out = judge.judge_search_terms(db, now=_NOW)
    assert "저비용" not in _pl(out)


def test_margin_fallback_when_margin_unavailable(db):
    # margin 미확인(contribution_margin=0) → 폴백 10,000. cost 12,000≥폴백=통과, 9,000<폴백=배제.
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="0", target_roas="2.0")  # margin→0→폴백
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="폴백통과", adgroup_id="grp-web", clk=10, cost=12000)
    _pl_term(db, term="폴백미달", adgroup_id="grp-web", clk=10, cost=9000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "폴백통과" in _pl(out)
    assert "폴백미달" not in _pl(out)


# ── ④ 그룹 순손실 프록시 ──
def test_group_profitable_not_candidate(db):
    # 그룹 ROAS 3.0 ≥ target 2.0 = 순이익 → 이 그룹 확장검색어는 안 자른다(오컷 방지 1차).
    _settings(db)
    _product(db, adgroup_id="grp-win", margin="500", target_roas="2.0")
    _group_perf(db, adgroup_id="grp-win", cost=10000, conv_amt=30000)  # roas 3.0
    _pl_term(db, term="이익그룹검색어", adgroup_id="grp-win", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "이익그룹검색어" not in _pl(out)


def test_group_no_daily_data_fail_closed(db):
    # 그룹 성과행 없음(cost 0) → 판정 불가 → fail-closed(안 자름).
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _pl_term(db, term="데이터없음", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "데이터없음" not in _pl(out)


# ── ⑤ 화이트리스트: 디바이스 토큰 제외 / 제품형 토큰 보호 ──
def test_device_token_not_protected(db):
    # '아이패드'는 디바이스 토큰(스톱리스트) → 보호 안 됨 → 자를 수 있음(실측 2 해소).
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500", name="아이패드 강화유리")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="아이패드케이스싼거", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "아이패드케이스싼거" in _pl(out)  # 아이패드=디바이스 토큰이라 보호 안 됨


def test_product_form_token_protected(db):
    # '강화유리'는 제품형 토큰(디바이스 아님) → 보호 → 안 잘림(그룹 핵심 의도어 오컷 방지 2차).
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500", name="아이패드 강화유리")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="아이패드강화유리추천", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "아이패드강화유리추천" not in _pl(out)  # 강화유리=제품형 토큰 보호


def test_global_form_token_protects_when_no_group_mapping(db):
    # 그룹 매핑 상품 없음 → 전역 제품형 토큰(지문방지 등, 디바이스 제외)으로 폴백 보호.
    # 단 gate ③(margin 폴백 10,000)·④(순손실)는 별도 통과해야 하므로 cost·성과 세팅.
    _settings(db)
    # 그룹엔 매핑 없음 — 단, 계정 기본 target_roas 확보용 다른 그룹에 has_cost 상품 1개.
    _product(db, adgroup_id="grp-other", cpid="PX", margin="500", target_roas="2.0")
    _group_perf(db, adgroup_id="grp-nomap", cost=10000, conv_amt=1000)
    _pl_term(db, term="지문방지필름싼거", adgroup_id="grp-nomap", clk=10, cost=12000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "지문방지필름싼거" not in _pl(out)  # 전역 '지문방지' 보호(폴백)


# ── P2-2 GATE: 그룹명 제품형 토큰 보호(오컷 방지) — 상품명엔 없고 그룹명(_종이질감)에만 있는
#   제품형 의도가 검색어를 보호한다(§0.5 경고 케이스: "아이패드종이질감필름"이 종이질감 그룹에서
#   미보호로 잘리던 문제). NaverEntity.name(adgroup) 토큰을 상품명 토큰과 같은 소스로 합산. ──
def _group_name(db, adgroup_id, name, campaign_id="cmp1"):
    db.add(NaverEntity(entity_type="adgroup", entity_id=adgroup_id, parent_id=campaign_id,
                       campaign_id=campaign_id, campaign_type="WEB_SITE", name=name, status="on"))
    db.commit()


def test_group_name_form_token_protects(db):
    _settings(db)
    # 상품명엔 종이질감 없음(아이패드=디바이스, 정품=제품형이나 검색어에 없음) → 상품명만으론 미보호.
    _product(db, adgroup_id="grp-web", margin="500", name="아이패드 정품")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _group_name(db, "grp-web", "10세대_종이질감")  # 그룹명에만 종이질감 의도
    _pl_term(db, term="아이패드종이질감필름", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "아이패드종이질감필름" not in _pl(out)  # 그룹명 '종이질감' 토큰이 보호


def test_group_name_absent_form_token_not_protected(db):
    # 대조군: 그룹명에 제품형 토큰이 없으면(디바이스/숫자만) 같은 검색어는 후보로 남는다 —
    # 그룹명 토큰이 실제 load-bearing임을 증명(상품명·전역 토큰만으론 미보호).
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500", name="아이패드 정품")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _group_name(db, "grp-web", "10세대")  # 제품형 토큰 없음(숫자+세대만)
    _pl_term(db, term="아이패드종이질감필름", adgroup_id="grp-web", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "아이패드종이질감필름" in _pl(out)  # 미보호 → 후보


# ── ⑥ 전환 보호(§1-1, 파워링크 구조적 0이나 게이트 유지) ──
def test_conversion_protection_retained(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="전환있는검색어", adgroup_id="grp-web", clk=10, cost=6000, pconv=1)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "전환있는검색어" not in _pl(out)


# ── ⑦ sentinel 제외 ──
def test_sentinel_adgroup_excluded(db):
    _settings(db)
    _pl_term(db, term="센티넬검색어", adgroup_id="__backfill__", clk=10, cost=6000)
    _pl_term(db, term="빈그룹검색어", adgroup_id="", clk=10, cost=6000)
    out = judge.judge_search_terms(db, now=_NOW)
    assert "센티넬검색어" not in _pl(out)
    assert "빈그룹검색어" not in _pl(out)
    assert _agency(out) == set()


# ── 결정적 정렬(cost 내림차순) ──
def test_powerlink_sorted_by_cost_desc(db):
    _settings(db)
    _product(db, adgroup_id="grp-web", margin="500")
    _group_perf(db, adgroup_id="grp-web", cost=10000, conv_amt=1000)
    _pl_term(db, term="싼거", adgroup_id="grp-web", clk=10, cost=6000)
    _pl_term(db, term="비싼거", adgroup_id="grp-web", clk=10, cost=9000)
    out = judge.judge_search_terms(db, now=_NOW)
    terms = [c["search_term"] for c in out["exclude_candidates"]["powerlink"]]
    assert terms == ["비싼거", "싼거"]
