# test_intelligence_s5_nonpa_ad.py — S5a 비-PA 광고비(전체 ALL 전환) 머니코드 fixture (D-15)
# 공식: net_profit -= nonpa,  nonpa = max(0, ALL_DELIVERED − DELIVERED) (계정 단위, by_option 불변).
#   ALL_DELIVERED=all_day_cost(전체), DELIVERED=day_cost(집행/PA), report/SALES 권위값(ADV_SALES키).
# 게이트: 옵션 광고 활동(ad_spend_total>0=오픽스 포함)일 때만 적용 → WING2(광고0)/데이터0 no-op.
# 라이브 호출 없음. 인메모리 SQLite로 compute_command_center + get_ad_cost_totals 검증.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Channel, CoupangAdCostDaily, CoupangAdOptionDaily, CoupangProductItem
from app.services.coupang.ad_cost_sync import get_ad_cost_totals, ingest_ad_cost_days
from app.services.coupang.intelligence import compute_command_center

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))
_SALES_KEY = "ADV_SALES"
_OFIX = "A01564720"   # 광고주 vendor(오픽스) — 비-PA 적용 대상
_OHAI = "A01029796"   # 오하이테크 vendor — 비-PA 미적용


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_sales(db, day_cost, all_cost, *, cost_date=date(2026, 6, 10)):
    """report/SALES 확정일 행(ADV_SALES). day_cost=집행, all_day_cost=전체."""
    db.add(CoupangAdCostDaily(
        cost_date=cost_date, vendor_id=_SALES_KEY,
        day_cost=day_cost, all_day_cost=all_cost, conv_sales=0, month_cost=0,
    ))


def _seed_option_ad(db, vid, spend, *, vendor=_OFIX, report_date=date(2026, 6, 5)):
    """옵션 광고 행(per-product). vendor 기본=오픽스."""
    db.add(CoupangAdOptionDaily(
        report_date=report_date, vendor_id=vendor, sell_type="3P",
        ad_option_id=vid, conv_option_id=vid, ad_spend=Decimal(str(spend)),
    ))


def _seed_account(db, cid, code, vendor, vid):
    """계정 분리 게이트 테스트용 — Channel + 상품(vendor_id 도출원)."""
    db.add(Channel(id=cid, name=f"쿠팡_{code}", code=code, platform="coupang", company=code))
    db.add(CoupangProductItem(
        vendor_item_id=vid, account_key=code, vendor_id=vendor,
        seller_product_id=f"SP_{vid}", item_name=f"옵션{vid}", sale_price=Decimal("0")))


def _cc(db, account=None):
    return compute_command_center(db, WIN[0], WIN[1], account)


# ─── 1) 순수 헬퍼 get_ad_cost_totals ────────────────────────────

def test_totals_basic(db):
    _seed_sales(db, 1000, 1065)
    db.commit()
    t = get_ad_cost_totals(db, WIN[0], WIN[1])
    assert t == {"pa": 1000, "total": 1065, "nonpa": 65}


def test_totals_clamp_total_below_pa(db):
    # 이상 데이터(전체<집행) → 전체=집행으로 클램프, 비-PA=0.
    _seed_sales(db, 1000, 900)
    db.commit()
    t = get_ad_cost_totals(db, WIN[0], WIN[1])
    assert t == {"pa": 1000, "total": 1000, "nonpa": 0}


def test_totals_excludes_running_rows(db):
    # 오늘 running(per-vendor 행, vendor_id≠ADV_SALES)은 확정 아님 → 합계 제외.
    _seed_sales(db, 1000, 1065)
    db.add(CoupangAdCostDaily(cost_date=date(2026, 6, 14), vendor_id="104438581",
                              day_cost=8000, all_day_cost=8000, conv_sales=0, month_cost=0))
    db.commit()
    t = get_ad_cost_totals(db, WIN[0], WIN[1])
    assert t["pa"] == 1000 and t["total"] == 1065  # running 8000 미포함


# ─── 2) 통합: 비-PA 차감(게이트 활성) ─────────────────────────────

def test_integration_nonpa_deducted(db):
    # 옵션 광고 100(게이트 활성) + 확정 집행 1000/전체 1065 → 비-PA 65.
    _seed_option_ad(db, "OPT1", 100)
    _seed_sales(db, 1000, 1065)
    db.commit()
    s = _cc(db)["account"]["summary"]
    ad = _cc(db)["ad"]["summary"]
    # base net_profit = 0매출 − 옵션광고 100 = −100. 비-PA 65 추가차감 → −165.
    assert s["ad_nonpa_deducted"] == Decimal("65")
    assert s["net_profit"] == Decimal("-165")
    # ad_sum 분해 노출
    assert ad["ad_confirmed_pa"] == Decimal("1000")
    assert ad["ad_confirmed_total"] == Decimal("1065")
    assert ad["ad_confirmed_nonpa"] == Decimal("65")
    assert ad["ad_spend"] == Decimal("100")  # 옵션 rollup(per-product, 불변)


# ─── 3a) 게이트(codex P1 case1): 오픽스 비-PA만, 옵션 PA=0 → 적용 ──

def test_nonpa_applied_when_option_pa_zero(db):
    # 전체(account=None)뷰: 옵션 PA 활동이 0이어도 ADV_SALES 비-PA는 실제 돈 → 차감돼야 한다.
    # (구 활동 프록시 게이트의 누락 버그 — 계정 식별 게이트로 수정.)
    _seed_sales(db, 1000, 1065)
    db.commit()
    s = _cc(db)["account"]["summary"]
    ad = _cc(db)["ad"]["summary"]
    assert s["ad_nonpa_deducted"] == Decimal("65")
    assert s["net_profit"] == Decimal("-65")     # 옵션 0 − 비-PA 65
    assert ad["ad_confirmed_nonpa"] == Decimal("65")


# ─── 3b) 게이트(codex P1 case2): WING2 옵션 PA 있어도 오픽스 비-PA 미적용 ──

def test_gate_wing2_does_not_apply_ofix_nonpa(db):
    # WING2(오하이)가 자기 옵션 PA를 갖더라도, 오픽스 글로벌 ADV_SALES 비-PA를 절대 차감하면 안 된다
    # (계정 식별 게이트: vendor A01029796 ≠ 광고주 A01564720).
    _seed_account(db, 2, "COUPANG_WING2", _OHAI, "W2OPT")
    _seed_option_ad(db, "W2OPT", 100, vendor=_OHAI)   # WING2 옵션 PA
    _seed_sales(db, 1000, 1065)                        # 오픽스 비-PA(글로벌 ADV_SALES)
    db.commit()
    s = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING2")["account"]["summary"]
    ad = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING2")["ad"]["summary"]
    assert s["ad_nonpa_deducted"] == _Z          # ★오픽스 비-PA 오적용 방지
    assert s["net_profit"] == Decimal("-100")    # WING2 옵션 광고만(비-PA 0)
    assert ad["ad_confirmed_nonpa"] == _Z


# ─── 3c) 게이트: 오픽스(WING1) 계정뷰는 적용 ──────────────────────

def test_gate_wing1_applies(db):
    _seed_account(db, 1, "COUPANG_WING1", _OFIX, "W1OPT")
    _seed_option_ad(db, "W1OPT", 100, vendor=_OFIX)
    _seed_sales(db, 1000, 1065)
    db.commit()
    s = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING1")["account"]["summary"]
    assert s["ad_nonpa_deducted"] == Decimal("65")   # 오픽스 계정 → 적용


# ─── 4) 회귀 가드: 광고 데이터 전무 → 불변 ───────────────────────

def test_regression_no_ad_data_unchanged(db):
    s = _cc(db)["account"]["summary"]
    ad = _cc(db)["ad"]["summary"]
    assert s["ad_nonpa_deducted"] == _Z
    assert s["net_profit"] == _Z
    assert ad["ad_confirmed_pa"] == _Z
    assert ad["ad_confirmed_nonpa"] == _Z


# ─── 5) by_option 불변(운영 ROAS 지표) ──────────────────────────

def test_by_option_unchanged_by_nonpa(db):
    _seed_option_ad(db, "OPT1", 100)
    _seed_sales(db, 1000, 1065)
    db.commit()
    ad_rows = _cc(db)["ad"]["by_option"]
    opt = next(r for r in ad_rows if r["vendor_item_id"] == "OPT1")
    assert opt["ad_spend"] == Decimal("100")  # 옵션 비용은 비-PA 차감과 무관(계정 단위)


# ─── 6) 비-PA=0(전체=집행)이면 net_profit 불변 ──────────────────

def test_nonpa_zero_when_total_equals_pa(db):
    _seed_option_ad(db, "OPT1", 100)
    _seed_sales(db, 1000, 1000)  # 비-PA 없음
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["ad_nonpa_deducted"] == _Z
    assert s["net_profit"] == Decimal("-100")  # 옵션 광고만(비-PA 0)


# ─── 7) 감사 체인(codex P2-1): pre_nonpa → −비-PA → pre_rg ───────

def test_audit_chain_pre_nonpa(db):
    _seed_option_ad(db, "OPT1", 100)
    _seed_sales(db, 1000, 1065)
    db.commit()
    s = _cc(db)["account"]["summary"]
    # 옵션합(계정 조정 전) = −100. 비-PA 65 차감 후 = pre_rg = −165(RG 없음 → net_profit 동일).
    assert s["net_profit_pre_nonpa"] == Decimal("-100")
    assert s["net_profit_pre_nonpa"] - s["ad_nonpa_deducted"] == s["net_profit_pre_rg"]
    assert s["net_profit_pre_rg"] == Decimal("-165")
    assert s["net_profit"] == Decimal("-165")  # RG 데이터 없음 → 플립 no-op


# ─── 8) ingest 카운터(codex P2-2): missing/clamped 가시화 ────────

def test_ingest_counts_missing_all_cost(db):
    # all_cost 키 없음(구 페처/필드 누락) → ad_spend 폴백 + missing 카운트.
    r = ingest_ad_cost_days(db, [{"date": date(2026, 6, 10), "ad_spend": 1000, "conv_sales": 0}])
    assert r["all_cost_missing"] == 1 and r["all_cost_clamped"] == 0
    assert get_ad_cost_totals(db, WIN[0], WIN[1])["nonpa"] == 0  # 폴백 → 비-PA 0


def test_ingest_counts_clamped_all_cost(db):
    # all_cost < ad_spend(API 이상) → 클램프 + clamped 카운트.
    r = ingest_ad_cost_days(db, [{"date": date(2026, 6, 10), "ad_spend": 1000,
                                  "all_cost": 800, "conv_sales": 0}])
    assert r["all_cost_clamped"] == 1 and r["all_cost_missing"] == 0
    assert get_ad_cost_totals(db, WIN[0], WIN[1]) == {"pa": 1000, "total": 1000, "nonpa": 0}


def test_ingest_normal_all_cost_no_flags(db):
    r = ingest_ad_cost_days(db, [{"date": date(2026, 6, 10), "ad_spend": 1000,
                                  "all_cost": 1065, "conv_sales": 0}])
    assert r["all_cost_missing"] == 0 and r["all_cost_clamped"] == 0
    assert get_ad_cost_totals(db, WIN[0], WIN[1])["nonpa"] == 65
