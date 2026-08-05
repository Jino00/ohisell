# 제안서 엑셀 → 프로모션 매칭·할인액 적재 (D-CPP-10)
#
# 이 테스트가 지키는 것 넷:
#   ① macOS NFD 파일명이 NFC 프로모션명과 매칭된다 (라이브에서 실제로 깨졌던 함정)
#   ② 기간이 같은 프로모션 둘을 라벨로 가른다 / 못 가르면 **붙이지 않는다**
#   ③ 정률 표기가 애매하면(1 초과) 추측하지 않고 skip
#   ④ 파일에서 빠진 SKU는 사라진다(snapshot) — 유령 항목 금지
import unicodedata
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.clients.coupang import rocket_promo_file as parser
from app.models import Base, CoupangPromoDiscountItem, CoupangRocketPromotion
from app.services.coupang import rocket_promo_file_sync as sync

VENDOR = "A01029796"


@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _promo(db, request_id, name, start, end):
    db.add(CoupangRocketPromotion(
        request_id=request_id, vendor_id=VENDOR, promotion_name=name,
        start_at=start, end_at=end,
    ))
    db.commit()


# ── ① 파일명 파싱 ────────────────────────────────────────────
def test_parse_file_name_basic():
    m = parser.parse_file_name("instant_upload_template_260723_260815_플립폴드8시리즈")
    assert m["period_from"] == date(2026, 7, 23)
    assert m["period_to"] == date(2026, 8, 15)
    assert m["label"] == "플립폴드8시리즈"


def test_parse_file_name_rejects_out_of_rule():
    assert parser.parse_file_name("260213_S26출시 할인율") is None
    assert parser.parse_file_name("instant_upload_template_플립폴드8") is None


def test_parse_file_name_rejects_reversed_period():
    assert parser.parse_file_name("instant_upload_template_260815_260723_거꾸로") is None


# ── ② NFD/NFC 함정 ───────────────────────────────────────────
def test_nfd_filename_matches_nfc_promotion(db):
    """macOS가 주는 NFD 파일명이 NFC 프로모션명과 매칭돼야 한다.

    기간이 같은 프로모션 둘(2026-08-05 실측: 계약 2385997·2386000이 둘 다 07-24~07-26)을
    라벨로 가르는 경로라, 정규화가 빠지면 조용히 미매칭이 된다.
    """
    _promo(db, "688054", "폴드7", datetime(2026, 7, 24), datetime(2026, 7, 26, 23, 59, 59))
    _promo(db, "687878", "아이폰17프로_강화유리, S26울트라_지문",
           datetime(2026, 7, 24, 0, 1), datetime(2026, 7, 26, 23, 59, 59))

    nfd_label = unicodedata.normalize("NFD", "폴드7")
    assert nfd_label != "폴드7"          # 전제 확인: 두 표현은 문자열로 다르다
    rid, why = sync.match_promotion(db, VENDOR, date(2026, 7, 24), date(2026, 7, 26), nfd_label)
    assert rid == "688054", why


def test_same_period_without_label_hit_is_not_guessed(db):
    """라벨로 못 가르면 붙이지 않는다 — 틀린 행사에 붙은 분담금은 대사되지 않는다."""
    _promo(db, "688054", "폴드7", datetime(2026, 7, 24), datetime(2026, 7, 26))
    _promo(db, "687878", "아이폰17프로", datetime(2026, 7, 24), datetime(2026, 7, 26))
    rid, why = sync.match_promotion(db, VENDOR, date(2026, 7, 24), date(2026, 7, 26), "관계없는라벨")
    assert rid is None
    assert "못 가름" in why


def test_period_matches_on_date_part_only(db):
    """start_at은 초 단위(07-24 00:01:00)지만 파일명은 날짜뿐 — 날짜부로 견준다."""
    _promo(db, "685840", "즉시할인", datetime(2026, 7, 22, 0, 0), datetime(2026, 7, 23, 0, 0))
    rid, _ = sync.match_promotion(db, VENDOR, date(2026, 7, 22), date(2026, 7, 23), "아무거나")
    assert rid == "685840"


# ── ③ 행 검증 ────────────────────────────────────────────────
def test_rate_over_one_is_skipped_not_guessed():
    """정률 20(=20%인지 2000%인지 불명)은 100으로 나누지 않고 skip한다."""
    st = {}
    out = parser.parse_discount_rows(
        [{"product_number": "1", "discount_type": "정률", "discount_value": 20}], stats=st
    )
    assert out == []
    assert st["skipped"] == 1


def test_rate_ratio_is_accepted():
    out = parser.parse_discount_rows(
        [{"product_number": "69592743", "discount_type": "정률", "discount_value": 0.2}]
    )
    assert out[0]["discount_type"] == parser.RATE
    assert out[0]["discount_value"] == Decimal("0.2")


def test_header_and_guide_rows_are_dropped():
    rows = [
        {"product_number": "SKUID", "discount_type": "필수값_할인타입", "discount_value": None},
        {"product_number": "필수", "discount_type": "필수", "discount_value": "필수"},
        {"product_number": "76350897", "discount_type": "정액수량", "discount_value": 3000},
    ]
    out = parser.parse_discount_rows(rows)
    assert [r["product_number"] for r in out] == ["76350897"]
    assert out[0]["discount_value"] == Decimal("3000")


def test_zero_or_negative_discount_skipped():
    st = {}
    out = parser.parse_discount_rows([
        {"product_number": "1", "discount_type": "정액수량", "discount_value": 0},
        {"product_number": "2", "discount_type": "정액수량", "discount_value": -100},
    ], stats=st)
    assert out == []
    assert st["skipped"] == 2


# ── ④ 적재(snapshot) ────────────────────────────────────────
def test_ingest_matches_and_replaces_snapshot(db):
    _promo(db, "686180", "플립폴드8 할인",
           datetime(2026, 7, 23, 22, 0), datetime(2026, 8, 15, 23, 59, 59))

    files = [{
        "file_name": "instant_upload_template_260723_260815_플립폴드8시리즈",
        "file_mtime": "2026-07-23T11:26:00",
        "rows": [
            {"product_number": "76350896", "discount_type": "정액수량", "discount_value": 3000},
            {"product_number": "76350897", "discount_type": "정액수량", "discount_value": 3000},
            {"product_number": "76350898", "discount_type": "정액수량", "discount_value": 3000},
        ],
    }]
    out = sync.ingest_promo_discount_files(db, VENDOR, files)
    assert out["items_ingested"] == 3
    assert out["matched"][0]["request_id"] == "686180"
    assert out["unmatched"] == []

    # 제안서가 2개로 줄면 빠진 SKU는 사라져야 한다(유령 금지)
    files[0]["rows"] = files[0]["rows"][:2]
    out2 = sync.ingest_promo_discount_files(db, VENDOR, files)
    assert out2["items_removed"] == 1
    left = {r.product_number for r in db.query(CoupangPromoDiscountItem).all()}
    assert left == {"76350896", "76350897"}


def test_ingest_reports_unmatched_loudly(db):
    """매칭 실패는 조용히 지나가면 안 된다 — 분담금 0 = 순이익 과대."""
    out = sync.ingest_promo_discount_files(db, VENDOR, [{
        "file_name": "instant_upload_template_260101_260102_없는행사",
        "rows": [{"product_number": "1", "discount_type": "정액수량", "discount_value": 100}],
    }])
    assert out["items_ingested"] == 0
    assert len(out["unmatched"]) == 1
    assert out["unmatched"][0]["reason"] == "기간에 맞는 프로모션 없음"


def test_ingest_is_idempotent(db):
    _promo(db, "686180", "플립폴드8 할인",
           datetime(2026, 7, 23, 22, 0), datetime(2026, 8, 15, 23, 59, 59))
    files = [{
        "file_name": "instant_upload_template_260723_260815_플립폴드8시리즈",
        "rows": [{"product_number": "76350897", "discount_type": "정액수량", "discount_value": 3000}],
    }]
    sync.ingest_promo_discount_files(db, VENDOR, files)
    sync.ingest_promo_discount_files(db, VENDOR, files)
    assert db.query(CoupangPromoDiscountItem).count() == 1
