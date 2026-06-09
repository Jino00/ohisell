# test_intelligence_rg_flip.py — S7 net_profit 플립 머니코드 fixture 테스트 (D-12 / D-14 / D-16)
# D-16: net_profit_new = net_profit_pre_rg − rg_total  [RG 정산 총액 전액 차감, 광고 포함]
#   (D-15 non-ad 차감은 폐기 — RG 광고가 광고센터 PA 보고서에 없고 정산 전용임을 /browse로 실증.)
# 라이브 API 호출 없음. 인메모리 SQLite로 compute_command_center 통합 검증 + 순수함수 단위 검증.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CoupangRgSettlementFee, CoupangAdOptionDaily
from app.services.coupang.intelligence import (
    apply_rg_net_profit_flip,
    compute_command_center,
)

_Z = Decimal(0)
WIN = (date(2026, 6, 1), date(2026, 6, 30))  # 기본 조회 윈도우


# ─── 인메모리 DB + 시드 헬퍼 ─────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_rg_fee(db, account_key, fee_type, amount, *,
                 dfrom=date(2026, 6, 1), dto=date(2026, 6, 7), vid=""):
    """RG 정산 수수료 행 1개. 계정 row=vid '' (대조뷰·플립 권위 소스)."""
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=dfrom, recognition_date_to=dto,
        fee_type=fee_type, vendor_item_id=vid, amount=Decimal(str(amount)),
    ))


def _seed_ad_2p(db, vid, spend, *, report_date=date(2026, 6, 5), sell_type="2P"):
    """광고 XLSX 행(net_profit의 ad_spend에 들어감). sell_type 2P=RG."""
    db.add(CoupangAdOptionDaily(
        report_date=report_date, vendor_id="A01564720", sell_type=sell_type,
        ad_option_id=vid, conv_option_id=vid, ad_spend=Decimal(str(spend)),
    ))


def _cc(db):
    return compute_command_center(db, WIN[0], WIN[1])


# ─── 1) 순수함수 (부호 3종) ─────────────────────────────────

def test_apply_flip_positive():
    # RG 총액 800 전액 차감 → 1000 − 800 = 200
    assert apply_rg_net_profit_flip(Decimal("1000"), Decimal("800")) == Decimal("200")

def test_apply_flip_zero():
    # RG 데이터 없음(차감 0) → 불변 (회귀 가드 순수버전)
    assert apply_rg_net_profit_flip(Decimal("1000"), _Z) == Decimal("1000")

def test_apply_flip_negative_refund():
    # 환급주기(rg_total < 0) → 부호 그대로 가산: 1000 − (−200) = 1200
    assert apply_rg_net_profit_flip(Decimal("1000"), Decimal("-200")) == Decimal("1200")


# ─── 2) 통합: RG 계정 row만 → 총액 전액(광고 포함) 차감 ──────────

def test_integration_rg_account_only(db):
    # base net_profit_pre_rg = 0. RG 판매수수료 500 + 풀필먼트 200 + 광고 100 = 총 800.
    _seed_rg_fee(db, "A01564720", "sale_fee", 500)
    _seed_rg_fee(db, "A01564720", "warehousing", 200)
    _seed_rg_fee(db, "A01564720", "ad_sales", 100)
    db.commit()
    s = _cc(db)["account"]["summary"]
    # rg_total=800 전액 차감 → net_profit = 0 − 800 = −800. (D-16: 광고 100도 차감)
    assert s["net_profit_pre_rg"] == _Z
    assert s["rg_settlement_total"] == Decimal("800")
    assert s["rg_ad_settlement"] == Decimal("100")           # 표시: 전액 중 광고분
    assert s["rg_non_ad_deducted"] == Decimal("700")         # 표시: 광고 제외 브레이크다운
    assert s["net_profit"] == Decimal("-800")                # ★전액 차감
    assert s["rg_flip_status"] == "applied_full"


# ─── 3) [t1] 부분 윈도우 견고 (정산주기 통째 차감, D-2 겹침) ──────

def test_t1_partial_window_full_cycle_deducted(db):
    # 주간 정산주기(6/1~6/7) 전액이, 1일 윈도우(6/3)에 겹치면 전액 차감(D-2).
    _seed_rg_fee(db, "A01564720", "sale_fee", 400, dfrom=date(2026, 6, 1), dto=date(2026, 6, 7))
    db.commit()
    s = compute_command_center(db, date(2026, 6, 3), date(2026, 6, 3))["account"]["summary"]
    assert s["rg_settlement_total"] == Decimal("400")
    assert s["net_profit"] == Decimal("-400")  # 비례배분 안 함 — 주기 전액


# ─── 4) [t2] 광고가 큰 케이스도 광고 포함 전액 차감 ───────────────

def test_t2_ad_dominant_full_deducted(db):
    # 광고 900 + 판매수수료 100 = 1000. D-16: 광고 포함 전액 차감(RG 광고는 정산 전용).
    _seed_rg_fee(db, "A01564720", "ad_sales", 900)
    _seed_rg_fee(db, "A01564720", "sale_fee", 100)
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["rg_settlement_total"] == Decimal("1000")
    assert s["rg_ad_settlement"] == Decimal("900")
    assert s["net_profit"] == Decimal("-1000")               # 광고 포함 전액


# ─── 5) [t3] 음수 환급주기 → 순이익 가산 ─────────────────────────

def test_t3_negative_refund_cycle(db):
    # 환급(취소)으로 sale_fee가 음수 → rg_total<0 → 순이익에 가산.
    _seed_rg_fee(db, "A01564720", "sale_fee", -300)
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["rg_settlement_total"] == Decimal("-300")
    assert s["net_profit"] == Decimal("300")  # 0 − (−300) = +300 환급 반영


# ─── 6) [t4] 광고 XLSX(2P=0 실측) + RG non-ad → 누락 없이 차감 ────

def test_t4_xlsx_3p_ad_plus_rg(db):
    # 광고 XLSX 3P(윙) 250이 net_profit의 ad_spend에 들어가 base = −250(RG 무관).
    # RG 풀필먼트 200 → 전액 차감. RG 광고는 정산 전용이라 XLSX와 겹치지 않음(prod 2P=0).
    _seed_ad_2p(db, "OPT1", 250, sell_type="3P")
    _seed_rg_fee(db, "A01564720", "warehousing", 200)
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["net_profit_pre_rg"] == Decimal("-250")    # 윙 광고비(XLSX) 반영
    assert s["rg_settlement_total"] == Decimal("200")
    assert s["net_profit"] == Decimal("-450")           # −250 − 200


# ─── 7) [t5] RG 광고 전액 차감 — 광고센터 미포함이라 이중계상 없음 ──

def test_t5_rg_ad_fully_deducted_no_xlsx_overlap(db):
    # RG 정산 광고 500 + 판매수수료 300 = 800. 광고 XLSX엔 RG(2P) 0행(D-16 실측) →
    # 광고센터 광고비(3P 250)와 RG 정산 광고(500)는 별개 소스 → 전액 차감해도 이중계상 없음.
    _seed_ad_2p(db, "OPT1", 250, sell_type="3P")   # 광고센터 윙 광고(net_profit에 이미 반영)
    _seed_rg_fee(db, "A01564720", "ad_sales", 500)  # RG 정산 광고(별개)
    _seed_rg_fee(db, "A01564720", "sale_fee", 300)
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["net_profit_pre_rg"] == Decimal("-250")    # 윙 광고만(XLSX)
    assert s["rg_settlement_total"] == Decimal("800")   # RG 광고 500 + 수수료 300
    assert s["rg_ad_settlement"] == Decimal("500")
    assert s["net_profit"] == Decimal("-1050")          # −250 − 800 (RG 광고도 차감)


# ─── 8) [D3] 브리지 필드 + 등식 ──────────────────────────────

def test_d3_bridge_fields_and_equation(db):
    _seed_rg_fee(db, "A01564720", "sale_fee", 600)
    _seed_rg_fee(db, "A01564720", "ad_sales", 150)
    db.commit()
    s = _cc(db)["account"]["summary"]
    for k in ("net_profit_pre_rg", "rg_settlement_total", "rg_ad_settlement",
              "rg_non_ad_deducted", "rg_flip_status"):
        assert k in s, f"브리지 필드 누락: {k}"
    # ★등식(D-16): pre_rg − rg_settlement_total == net_profit (전액 차감)
    assert s["net_profit_pre_rg"] - s["rg_settlement_total"] == s["net_profit"]
    # 브레이크다운: non_ad == total − ad
    assert s["rg_non_ad_deducted"] == s["rg_settlement_total"] - s["rg_ad_settlement"]
    # rg_settlement 섹션 summary에도 flip_status·deducted 노출
    rg = _cc(db)["rg_settlement"]["summary"]
    assert rg["flip_status"] == "applied_full"
    assert rg["deducted"] == Decimal("750")   # 전액(600+150)


# ─── 9) [회귀 가드 CRITICAL] RG 데이터 0 → net_profit 불변 ─────────

def test_regression_no_rg_data_unchanged(db):
    # 광고 XLSX만 있고 RG 정산 데이터 없음 → 플립 no-op, net_profit == pre_rg.
    _seed_ad_2p(db, "OPT1", 320, sell_type="3P")
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["rg_settlement_total"] == _Z
    assert s["net_profit_pre_rg"] == Decimal("-320")
    assert s["net_profit"] == Decimal("-320")           # IRON RULE: 기존 동작 보존
    assert s["rg_flip_status"] == "not_applied_no_data"


# ─── 비중복 가정: 옵션 row(vid≠'')는 계정 플립에 미포함 ──────────

def test_option_rows_excluded_from_account_flip(db):
    # 같은 (account, 기간, fee_type)에 계정 row('')와 옵션 row(실제ID) 공존 시,
    # 플립은 계정 row만 사용(옵션 row 이중계상 차단, S6 D-6 가드).
    _seed_rg_fee(db, "A01564720", "warehousing", 500, vid="")       # 계정(VAT後)
    _seed_rg_fee(db, "A01564720", "warehousing", 450, vid="OPT9")   # 옵션(VAT前) — 무시돼야
    db.commit()
    s = _cc(db)["account"]["summary"]
    assert s["rg_settlement_total"] == Decimal("500")   # 옵션 450 미합산
    assert s["net_profit"] == Decimal("-500")
