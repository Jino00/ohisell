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
    # D-CPP-33: 납부세액 반영. 매출 0 → 매출VAT 0, 매입세액 (광고100+비-PA65)×10/110=15.00 환급
    assert s["payable_vat"] == Decimal("-15.00")
    assert s["net_profit"] == Decimal("-150.00")
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
    # 매입세액 65×10/110=5.91 환급 (D-CPP-33)
    assert s["net_profit"] == Decimal("-59.09")  # 옵션 0 − 비-PA 65 + 환급 5.91
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
    assert s["net_profit"] == Decimal("-90.91")   # WING2 옵션 광고 100 − 매입세액 환급 9.09
    # ★★«미적용»은 0이 아니라 None이다 (2026-08-11 시각 QA로 발견한 실토 결함).
    #   종전엔 여기 0을 실어 보냈고, 프론트의 `?? s.ad_spend` 폴백은 null만 잡으므로 화면이
    #   「전체 광고비(ALL) 0원 · net_profit 차감 기준」이라 **단정**했다 — 같은 화면 요약은
    #   11,247원인데. 돈은 안 틀렸지만 «광고를 안 썼다»로 읽히는 거짓 실토였다(교훈 #235 형태).
    assert ad["ad_confirmed_applies"] is False
    assert ad["ad_confirmed_nonpa"] is None
    assert ad["ad_confirmed_total"] is None
    assert ad["ad_confirmed_pa"] is None
    # 실제 차감액은 옵션축 값이고, 화면은 이 값으로 폴백한다.
    assert ad["ad_spend"] == Decimal("100")


# ─── 3c) 게이트: 오픽스(WING1) 계정뷰는 적용 ──────────────────────

def test_gate_wing1_applies(db):
    _seed_account(db, 1, "COUPANG_WING1", _OFIX, "W1OPT")
    _seed_option_ad(db, "W1OPT", 100, vendor=_OFIX)
    _seed_sales(db, 1000, 1065)
    db.commit()
    out = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING1")
    s = out["account"]["summary"]
    ad = out["ad"]["summary"]
    assert s["ad_nonpa_deducted"] == Decimal("65")   # 오픽스 계정 → 적용
    # ★적용되는 계정에선 실제 값이 실린다 — None이 «적용 안 됨»의 신호로만 쓰이는지 못 박는다.
    assert ad["ad_confirmed_applies"] is True
    assert ad["ad_confirmed_total"] == Decimal("1065")
    assert ad["ad_confirmed_pa"] == Decimal("1000")
    assert ad["ad_confirmed_nonpa"] == Decimal("65")


def test_applied_account_with_zero_ad_reports_zero_not_none(db):
    """★★«측정된 0원»과 «미적용»을 구분한다.

    광고주 계정인데 그 기간 광고비가 0원이면 그건 **사실**이고 0으로 실려야 한다.
    None으로 뭉개면 화면이 옵션축으로 폴백해 «광고센터 기준 0원»이라는 진짜 사실을 잃는다.
    (이 테스트가 없으면 «전부 None으로 주기»라는 게으른 수정이 통과한다.)
    """
    _seed_account(db, 1, "COUPANG_WING1", _OFIX, "W1OPT")
    _seed_option_ad(db, "W1OPT", 100, vendor=_OFIX)
    _seed_sales(db, 0, 0)          # 광고센터 보고서에 0원으로 «측정»됨
    db.commit()
    ad = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING1")["ad"]["summary"]
    assert ad["ad_confirmed_applies"] is True
    assert ad["ad_confirmed_total"] == _Z    # None이 아니다 — 0원이 사실이다
    assert ad["ad_confirmed_nonpa"] == _Z


def test_all_accounts_view_still_applies(db):
    """★account=None(전체 합산)은 종전대로 적용된다 — 오픽스가 유일 광고주이므로.

    ⚠️**이 테스트는 «적용 여부»만 고정한다 — 전체 합산 뷰의 «표시 정합성»을 보증하지 않는다.**
      적대 리뷰(2026-08-11 P2-1)가 지적: 전체 합산 뷰의 `ad_confirmed_total`은
      「net_profit 차감 기준」이라 인쇄되지만 실제 차감액은 **그것 + 비광고주 계정의 옵션축
      광고비**다(아래 테스트가 그 간극을 실제로 못 박는다). 이 PR 범위 밖의 기존 결함이고,
      여기서 «정상»으로 봉인되지 않게 하려고 아래 테스트를 같이 둔다.
    """
    _seed_account(db, 1, "COUPANG_WING1", _OFIX, "W1OPT")
    _seed_option_ad(db, "W1OPT", 100, vendor=_OFIX)
    _seed_sales(db, 1000, 1065)
    db.commit()
    out = compute_command_center(db, WIN[0], WIN[1], None)
    assert out["ad"]["summary"]["ad_confirmed_applies"] is True
    assert out["account"]["summary"]["ad_nonpa_deducted"] == Decimal("65")


def test_all_accounts_view_confirmed_total_is_not_the_deducted_amount(db):
    """★★전체 합산 뷰에는 «같은 거짓말»이 남아 있다 — 봉인하지 말고 명시한다 (적대 리뷰 P2-1).

    `ad_confirmed_total`(광고센터 ALL)은 **광고주 계정 것만** 센다. 그런데 net_profit에서
    실제로 빠지는 광고비는 «전 계정 옵션축 PA + 광고주 비-PA»다. 비광고주 계정(오하이테크)이
    광고를 쓰면 그 차액만큼 화면 단정(「net_profit 차감 기준」)이 사실과 어긋난다.

    이 테스트는 **현재 동작을 기술(characterize)**한다 — 통과한다고 «옳다»는 뜻이 아니다.
    고칠 때 여기가 빨개지면서 «이제 정합한다»로 단언을 바꾸면 된다.
    """
    _seed_account(db, 1, "COUPANG_WING1", _OFIX, "W1OPT")
    _seed_option_ad(db, "W1OPT", 1000, vendor=_OFIX)     # 광고주 옵션 PA
    _seed_account(db, 2, "COUPANG_WING2", _OHAI, "W2OPT")
    _seed_option_ad(db, "W2OPT", 100, vendor=_OHAI)      # 비광고주 옵션 PA — ALL에 안 잡힌다
    _seed_sales(db, 1000, 1065)                          # 광고센터 ALL=1065(집행1000+비PA65)
    db.commit()
    out = compute_command_center(db, WIN[0], WIN[1], None)
    shown = out["ad"]["summary"]["ad_confirmed_total"]           # 화면이 「차감 기준」이라 인쇄
    deducted = out["account"]["summary"]["ad_spend"] + out["account"]["summary"]["ad_nonpa_deducted"]
    assert shown == Decimal("1065")
    assert deducted == Decimal("1165")     # 옵션축 1000+100, 비-PA 65
    # ★현재는 어긋난다. 이 부등식이 등식이 되는 날 = 전체 합산 뷰 표시가 고쳐진 날.
    assert shown != deducted, "전체 합산 뷰가 정합해졌다면 이 테스트를 등식으로 바꾸고 부채를 닫아라"


def test_money_path_unchanged_when_not_applied(db):
    """★★돈은 안 바뀐다 — 이 수정은 «표시»만 고친다.

    미적용 계정의 net_profit·ad_nonpa_deducted는 수정 전과 같아야 한다(회귀 가드).
    표시를 고치다 돈을 건드리면 그게 훨씬 나쁜 사고다.
    """
    _seed_account(db, 2, "COUPANG_WING2", _OHAI, "W2OPT")
    _seed_option_ad(db, "W2OPT", 100, vendor=_OHAI)
    _seed_sales(db, 1000, 1065)
    db.commit()
    s = compute_command_center(db, WIN[0], WIN[1], "COUPANG_WING2")["account"]["summary"]
    assert s["ad_nonpa_deducted"] == _Z
    assert s["net_profit"] == Decimal("-90.91")
    assert s["ad_spend"] == Decimal("100")      # 옵션축 값이 실제 차감액


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
    assert s["net_profit"] == Decimal("-90.91")  # 옵션 광고 100 − 매입세액 환급 9.09


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
    assert s["net_profit"] == Decimal("-150.00")  # RG 플립 no-op, 납부세액 −15.00만 반영


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
