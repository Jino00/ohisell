# test_rocket_promo.py — 프로모션 손익 레이어 Phase 1 파서 + ingest (트랙 coupang-promo-pnl)
# 라이브 API 호출 없음. fixture는 **우리 레코드 계약**(PLAN §4) 기준 — 쿠팡 원시 스키마가 아니다
#   (2026-07-28 정찰 미완: supplier 세션 만료 → 원시 스키마 추측 금지).
# 값은 실측 사실 기반: 프로모션 Request 687878(2026-07-24 00:01:00~07-26 23:59:59, 분담 100%,
#   적용상품 2), 쿠폰 94177420 사용금액 156,000, SKU 62178970(=발주 product_number).
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.coupang import rocket_promo as rp
from app.database import Base, get_db
from app.models import CoupangCoupon, CoupangRocketPromotion, CoupangRocketSalesDaily
from app.services.coupang import rocket_promo_sync as sync


# ─── fixture (레코드 계약) ─────────────────────────────
_SALES_ROWS = [
    {"option_id": "95536607339", "sku_id": "62178970", "date": "2026-07-24",
     "qty": 18, "revenue": "304200", "visitors": 512, "conversion_rate": "0.0352",
     "product_name": "오하이 풀커버 강화유리 아이폰17프로"},
    {"option_id": "95570603512", "sku_id": "69411570", "date": "2026-07-24",
     "qty": 7, "revenue": "111300"},
    # 필수키 누락(option_id 없음) → skip
    {"sku_id": "62178970", "date": "2026-07-24", "qty": 3},
]

_PROMO_ROWS = [
    {"request_id": "687878", "contract_id": "9962", "promotion_name": "17프로 강화유리 할인",
     "promotion_type": "즉시할인", "status": "APPROVED",
     "start_at": "2026-07-24 00:01:00", "end_at": "2026-07-26 23:59:59",
     "share_ratio": "100", "discount_method": "할인액", "discount_value": "2000",
     "budget_amount": "500000", "settlement_date": "2026-09-15",
     "applied_product_count": 2, "requested_at": "2026-07-23 18:20:11",
     "raw": {"requestId": 687878}},
    # 필수키 누락(request_id 없음) → skip
    {"promotion_name": "이름만 있는 행"},
]

_COUPON_ROWS = [
    {"coupon_id": "94177420", "used_amount": "156,000"},
    {"coupon_id": "93654161", "used_amount": 40000},
    {"coupon_id": "99999999", "used_amount": None},   # 값 없음 → skip(0으로 접지 않음)
]


# ═══ 파서 SA(순수) ═══
def test_parse_sales_skips_rows_without_required_keys():
    recs = rp.parse_sales_rows(_SALES_ROWS)
    assert len(recs) == 2
    assert {r["option_id"] for r in recs} == {"95536607339", "95570603512"}


def test_parse_sales_field_types():
    rec = next(r for r in rp.parse_sales_rows(_SALES_ROWS) if r["option_id"] == "95536607339")
    assert rec["date"] == date(2026, 7, 24)
    assert rec["sku_id"] == "62178970"          # = 발주 product_number 브리지 키
    assert rec["qty"] == 18
    assert rec["revenue"] == Decimal("304200")
    assert rec["visitors"] == 512
    assert rec["conversion_rate"] == Decimal("0.0352")
    assert rec["source"] == "sales_analysis"


def test_parse_sales_missing_optional_fields_stay_none_not_zero():
    """'없음'과 '0'을 구분한다 — visitors 미제공을 0으로 접으면 유입 0으로 오독된다."""
    rec = next(r for r in rp.parse_sales_rows(_SALES_ROWS) if r["option_id"] == "95570603512")
    assert rec["visitors"] is None
    assert rec["conversion_rate"] is None
    assert rec["qty"] == 7


def test_parse_sales_last_row_wins_on_duplicate_grain():
    stats: dict = {}
    recs = rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": 1},
        {"option_id": "A", "date": "2026-07-24", "qty": 9},
    ], stats=stats)
    assert len(recs) == 1 and recs[0]["qty"] == 9
    # 중복 흡수는 '계약 위반'이 아니다 — 따로 센다(수집 건강 신호를 중복 뒤에 숨기지 않는다)
    assert stats == {"skipped": 0, "deduped": 1, "accepted": 2,
                     "blank_observations": 0, "blank_qty": 0, "blank_revenue": 0}


def test_parse_sales_source_label_propagates():
    recs = rp.parse_sales_rows([{"option_id": "A", "date": "2026-07-24", "qty": 0}], source="excel")
    assert recs[0]["source"] == "excel"


def test_parse_sales_skips_rows_with_no_observation():
    """관측 유무는 **키 존재**로 본다 — 매핑이 필드명을 놓친 것(키 없음)만 skip.

    값으로 판별하면 빈 셀('-'·NaN)로 오는 정상적인 0판매일이 매번 skipped에 잡혀
    수집 건강 경보가 상시 거짓말을 하고, 동반 신호(visitors)까지 함께 버려진다.
    """
    stats: dict = {}
    assert rp.parse_sales_rows(
        [{"option_id": "A", "date": "2026-07-24", "visitors": 10}], stats=stats
    ) == []
    assert stats["skipped"] == 1
    # 명시적 0은 관측이므로 통과한다
    assert len(rp.parse_sales_rows([{"option_id": "A", "date": "2026-07-24", "qty": 0}])) == 1


def test_parse_sales_blank_cell_is_zero_not_dropped():
    """키가 있고 값이 빈 셀('-'·NaN)이면 '0으로 팔린 날'이다 — visitors까지 버리지 않는다."""
    rec = rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": "-", "revenue": float("nan"),
         "visitors": 120},
    ])[0]
    assert rec["qty"] == 0 and rec["revenue"] == Decimal(0)
    assert rec["visitors"] == 120        # 유입은 있는데 전환이 0 — 이 테이블의 핵심 신호


def test_parse_sales_botched_value_is_skipped_not_zeroed():
    """값이 **적혀 있는데 못 읽은** 경우는 빈 셀과 다르다 — 0으로 접으면 사고가 사실로 둔갑."""
    stats: dict = {}
    assert rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": "십팔개", "revenue": "1000"},
    ], stats=stats) == []
    assert stats["skipped"] == 1


def test_parse_sales_oversized_number_does_not_reach_db():
    """Decimal('1E+999')는 is_finite()를 통과한다 — 유한한 것과 담기는 것은 다르다.

    NUMERIC(14,2) 상한을 넘는 값은 SQLite에선 inf로 적재돼 sum()을 오염시키고,
    PostgreSQL에선 commit 자체를 죽인다. 파싱 시점에 접는다.
    """
    stats: dict = {}
    assert rp.parse_sales_rows(
        [{"option_id": "A", "date": "2026-07-24", "revenue": "1e999"}], stats=stats
    ) == []
    assert stats["skipped"] == 1
    assert rp.parse_coupon_usage_rows([{"coupon_id": "A", "used_amount": "1e999"}]) == []


def test_parse_sales_infinity_is_an_accident_not_a_blank_cell():
    """Inf는 '안 적힌 값'이 아니라 계산 사고 — 0으로 접으면 18개 팔린 날이 매출 0원이 된다.

    빈 셀은 엑셀에서 NaN으로 오지 Inf로 오지 않는다. 상한 초과(1E+999)를 사고로 보면서
    한 자리 더 간 Inf를 0으로 접으면 앞뒤가 안 맞는다.
    """
    stats: dict = {}
    assert rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": 18, "revenue": float("inf")},
    ], stats=stats) == []
    assert stats["skipped"] == 1


def test_parse_sales_all_blank_batch_raises_alarm(caplog):
    """행 단위로는 전부 합법인데 배치 전체가 빈 관측 = 페처 매핑 사고(dict 리터럴 + 컬럼명 변경).

    키 존재 gate는 {"qty": row.get("판매수량")} 꼴을 구조적으로 못 잡는다 — 배치로 봐야 보인다.
    """
    stats: dict = {}
    recs = rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": None, "revenue": None},
        {"option_id": "B", "date": "2026-07-24", "qty": None, "revenue": None},
    ], stats=stats)
    assert len(recs) == 2                      # 행은 살린다(0판매일일 수도 있으므로)
    assert stats["blank_observations"] == 2    # 그러나 전부 빈 관측이면
    assert "매핑 사고" in caplog.text          # 경보를 울린다


def test_parse_sales_half_broken_mapping_is_caught(caplog):
    """컬럼명이 **한쪽만** 바뀌어도 잡는다 — 다른 쪽이 행을 살려 blank_observations를 빠져나간다.

    revenue만 전멸하면 '18개 팔렸는데 매출 0원'이 되고, qty만 전멸하면 Phase 2 단위경제가
    뒤집힌다. 그래서 필드별로 따로 세고 **어느 한쪽이라도** 전멸이면 경보를 울린다.
    """
    stats: dict = {}
    recs = rp.parse_sales_rows([
        {"option_id": "OP5", "date": "2026-07-24", "qty": 18, "revenue": None},
        {"option_id": "OP6", "date": "2026-07-24", "qty": 7, "revenue": None},
    ], stats=stats)
    assert len(recs) == 2 and stats["blank_observations"] == 0   # 둘 다 빈 행은 없지만
    assert stats["blank_revenue"] == 2                            # revenue는 전멸
    assert "매핑 사고" in caplog.text and "revenue 전부" in caplog.text


def test_parse_sales_partial_blank_batch_is_quiet():
    """일부만 빈 관측이면 정상(그날 안 팔린 옵션) — 거짓 경보를 울리지 않는다."""
    stats: dict = {}
    rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": None, "revenue": None},
        {"option_id": "B", "date": "2026-07-24", "qty": 7, "revenue": "111300"},
    ], stats=stats)
    assert stats["blank_observations"] == 1


def test_parse_sales_date_tz_is_converted_before_grain():
    """date는 그레인 키 — tz를 환산하지 않으면 **다른 날 행**에 적재된다."""
    rec = rp.parse_sales_rows(
        [{"option_id": "A", "date": "2026-07-24T23:00:00+00:00", "qty": 1}]
    )[0]
    assert rec["date"] == date(2026, 7, 25)   # KST 07-25 08:00


def test_parse_sales_nan_does_not_kill_batch():
    """엑셀 폴백의 빈 숫자셀은 float('nan') — int(nan)/Decimal('NaN')이 배치를 죽이면 안 된다.

    NaN은 빈 셀이므로 0으로 적재하고 행을 살린다. 반면 Inf는 계산 사고라 행을 skip한다
    (둘을 같이 취급하면 '18개 팔렸는데 매출 0원'이 조용히 쌓인다).
    """
    stats: dict = {}
    recs = rp.parse_sales_rows([
        {"option_id": "A", "date": "2026-07-24", "qty": float("nan"), "revenue": 1000},
        {"option_id": "B", "date": "2026-07-24", "qty": float("inf"), "revenue": 2000},
        {"option_id": "C", "date": "2026-07-24", "qty": 5, "revenue": "nan"},
    ], stats=stats)
    by = {r["option_id"]: r for r in recs}
    assert set(by) == {"A", "C"} and stats["skipped"] == 1   # B(Inf)만 사고로 skip
    assert by["A"]["qty"] == 0 and by["A"]["revenue"] == Decimal("1000")
    assert by["C"]["revenue"] == Decimal(0)   # NaN이 NUMERIC 컬럼에 적재되지 않는다


def test_parse_sales_oversized_identifier_is_not_truncated():
    """식별자는 자르지 않는다 — 잘린 ID는 '다른 ID'가 되어 조용히 잘못된 행에 붙는다."""
    long_id = "9" * 31
    assert rp.parse_sales_rows([{"option_id": long_id, "date": "2026-07-24", "qty": 1}]) == []
    rec = rp.parse_sales_rows(
        [{"option_id": "A", "sku_id": long_id, "date": "2026-07-24", "qty": 1}]
    )[0]
    assert rec["sku_id"] is None   # 선택 키는 NULL로 눈에 보이게 남는다


def test_parse_sales_garbage_input():
    assert rp.parse_sales_rows([]) == []
    assert rp.parse_sales_rows(None) == []
    assert rp.parse_sales_rows(["nope", 3, None]) == []


def test_parse_promotion_preserves_seconds():
    """행사기간은 초 단위 — 날짜로 뭉개면 프로모션 창 조인이 하루씩 틀어진다."""
    rec = rp.parse_promotion_rows(_PROMO_ROWS)[0]
    assert rec["start_at"] == datetime(2026, 7, 24, 0, 1, 0)
    assert rec["end_at"] == datetime(2026, 7, 26, 23, 59, 59)


def test_parse_promotion_fields():
    rec = rp.parse_promotion_rows(_PROMO_ROWS)[0]
    assert rec["request_id"] == "687878"
    assert rec["share_ratio"] == Decimal("100")      # 100% = 전액 셀러 부담
    assert rec["discount_value"] == Decimal("2000")
    assert rec["budget_amount"] == Decimal("500000")
    assert rec["settlement_date"] == date(2026, 9, 15)
    assert rec["applied_product_count"] == 2
    assert rec["raw"] == {"requestId": 687878}


def test_parse_promotion_skips_rows_without_request_id():
    assert len(rp.parse_promotion_rows(_PROMO_ROWS)) == 1


def test_parse_promotion_wraps_non_dict_raw():
    rec = rp.parse_promotion_rows([{"request_id": "1", "raw": ["a", "b"]}])[0]
    assert rec["raw"] == {"_raw": ["a", "b"]}


def test_parse_coupon_usage_skips_missing_and_negative():
    recs = rp.parse_coupon_usage_rows(_COUPON_ROWS + [{"coupon_id": "X", "used_amount": -1}])
    assert {r["coupon_id"] for r in recs} == {"94177420", "93654161"}


def test_parse_coupon_usage_strips_comma():
    rec = rp.parse_coupon_usage_rows([{"coupon_id": "94177420", "used_amount": "156,000"}])[0]
    assert rec["used_amount"] == Decimal("156000")


def test_parse_promotion_tz_is_converted_to_kst_not_stripped():
    """'...T00:01:00Z'는 KST 09:01이다. tz를 그냥 떼면 9시간 틀어진다(초 단위 보존과 모순)."""
    rec = rp.parse_promotion_rows([
        {"request_id": "1", "start_at": "2026-07-24T00:01:00+00:00"},
    ])[0]
    assert rec["start_at"] == datetime(2026, 7, 24, 9, 1, 0)


def test_parse_coupon_usage_nan_does_not_kill_batch():
    """Decimal('NaN')은 예외 없이 생기고 `< 0` 비교에서 InvalidOperation으로 배치를 죽인다."""
    recs = rp.parse_coupon_usage_rows([
        {"coupon_id": "A", "used_amount": "nan"},
        {"coupon_id": "B", "used_amount": float("inf")},
        {"coupon_id": "94177420", "used_amount": 156000},
    ])
    assert [r["coupon_id"] for r in recs] == ["94177420"]   # 못 읽은 값은 skip(0으로 접지 않음)


def test_parse_coupon_usage_zero_is_kept():
    """0은 '안 쓴 쿠폰'이라는 사실이므로 skip 대상이 아니다(None만 skip)."""
    recs = rp.parse_coupon_usage_rows([{"coupon_id": "A", "used_amount": 0}])
    assert len(recs) == 1 and recs[0]["used_amount"] == Decimal(0)


# ═══ ingest Harness (인메모리 SQLite) ═══
# ★autoflush=False는 **prod 세션과 맞추려고** 있다(database.py:16), 편의가 아니다. 기본값(True)
#   으로 두면 query가 미커밋 add()를 먼저 flush해 보여주므로 「query로 확인 → 없으면 add」 결함이
#   테스트에서만 무사히 통과한다 — 2026-08-15 prod 500 사고(브리지 UNIQUE 위반)가 정확히 이 틈으로
#   새어나갔다. 이 파일의 대상 코드는 그 세션에서 도는 것이므로 픽스처도 그 세션이어야 한다.
@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False)()
    yield s
    s.close()


def test_ingest_sales_and_idempotent(db):
    r1 = sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    assert r1["ingested"] == 2 and r1["skipped"] == 1
    assert db.query(CoupangRocketSalesDaily).count() == 2
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95536607339").one()
    assert row.vendor_id == "A01029796"           # 계정축 주입
    assert row.revenue == Decimal("304200")
    # 재수신 멱등: 행 수 불변
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    assert db.query(CoupangRocketSalesDaily).count() == 2


class TestOptionSkuBridgeIsAccumulated:
    """브리지(옵션↔상품번호)는 판매분석의 부산물이 아니라 **누적 자산**이다(D-16).

    판매분석은 BETA + 무료체험(2026-08-20 종료 예정)이라 언젠가 끊긴다. 대응 자체는 시간
    불변인데(실측: 바뀐 옵션 0건) 일별 테이블에만 있으면 수집이 멈추는 순간 손익이 조용히
    멈춘다 — 그래서 관측될 때마다 따로 남긴다.
    """

    def test_적재하면_브리지가_함께_남는다(self, db):
        from app.models import CoupangRocketOptionSku
        sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
        rows = {r.option_id: r for r in db.query(CoupangRocketOptionSku).all()}
        assert rows["95536607339"].sku_id == "62178970"
        assert rows["95536607339"].vendor_id == "A01029796"
        assert rows["95570603512"].sku_id == "69411570"

    def test_sku가_빈_재수신이_기존_브리지를_지우지_않는다(self, db):
        """★핵심: 판매분석이 sku를 안 주는 날(또는 끊긴 뒤)에도 이미 아는 대응은 살아남는다."""
        from app.models import CoupangRocketOptionSku
        sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
        sync.ingest_rocket_sales(db, "A01029796", [
            {"option_id": "95536607339", "date": "2026-07-25", "qty": 3, "revenue": "50000"},
        ])
        assert db.query(CoupangRocketOptionSku).filter_by(
            option_id="95536607339").one().sku_id == "62178970"

    def test_재적재해도_행이_늘지_않는다(self, db):
        from app.models import CoupangRocketOptionSku
        for _ in range(3):
            sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
        assert db.query(CoupangRocketOptionSku).count() == 2

    def test_신규옵션이_한_요청에_여러_날짜로_와도_브리지가_하나만_생긴다(self, db):
        """2026-08-15 prod 500 재현 — 브리지에 없는 옵션이 **이틀 이상** 팔린 배치.

        판매분석 한 요청은 옵션×날짜 격자라 같은 option_id가 날짜 수만큼 되풀이 등장한다.
        세션이 autoflush=False라 query가 미커밋 add()를 못 보므로, per-run 캐시가 없으면
        같은 option_id로 INSERT가 두 번 쌓여 commit에서
        `UNIQUE constraint failed: coupang_rocket_option_sku.option_id` → 그 청크가 통째로
        롤백된다(= 페처가 받는 HTTP 500). 신규 옵션이 하루만 팔리던 동안은 1회 등장이라
        무사했고, 이틀짜리 신규 옵션이 처음 나온 2026-08-15 수집분에서 3번째 청크가 죽었다.
        """
        from app.models import CoupangRocketOptionSku
        신규옵션_이틀치 = [
            {"option_id": "95999000111", "sku_id": "70011122", "date": "2026-08-13",
             "qty": 4, "revenue": "60000", "product_name": "오하이 신규 옵션"},
            {"option_id": "95999000111", "sku_id": "70011122", "date": "2026-08-14",
             "qty": 9, "revenue": "135000", "product_name": "오하이 신규 옵션"},
        ]
        out = sync.ingest_rocket_sales(db, "A01029796", 신규옵션_이틀치)
        assert out["ingested"] == 2                      # 날짜 행은 둘 다 남고
        assert db.query(CoupangRocketOptionSku).filter_by(
            option_id="95999000111").count() == 1        # 브리지는 하나뿐이다

    def test_신규옵션_여러개가_각각_여러_날짜로_와도_안전하다(self, db):
        """한 배치에 이틀짜리 신규 옵션이 여럿이어도 각 옵션당 브리지 1행."""
        from app.models import CoupangRocketOptionSku
        rows = [
            {"option_id": oid, "sku_id": f"7000{i}", "date": d, "qty": 1, "revenue": "1000"}
            for i, oid in enumerate(("95999000111", "95999000222", "95999000333"))
            for d in ("2026-08-13", "2026-08-14", "2026-08-15")
        ]
        sync.ingest_rocket_sales(db, "A01029796", rows)
        assert db.query(CoupangRocketOptionSku).count() == 3
        assert db.query(CoupangRocketSalesDaily).count() == 9

    def test_같은_배치_안에서_sku가_바뀌면_그것도_잡는다(self, db):
        """캐시가 «갱신 경로»를 건너뛰지 않는다 — 배치 안 변경도 경고+반영돼야 한다."""
        from app.models import CoupangRocketOptionSku
        sync.ingest_rocket_sales(db, "A01029796", [
            {"option_id": "95999000444", "sku_id": "70044400", "date": "2026-08-13",
             "qty": 1, "revenue": "1000"},
            {"option_id": "95999000444", "sku_id": "70044499", "date": "2026-08-14",
             "qty": 1, "revenue": "1000"},
        ])
        assert db.query(CoupangRocketOptionSku).filter_by(
            option_id="95999000444").one().sku_id == "70044499"

    def test_sku가_실제로_바뀌면_갱신하고_경고한다(self, db, caplog):
        """불변이라던 전제가 깨진 것이므로 조용히 넘기면 안 된다(손익 귀속이 바뀐다)."""
        from app.models import CoupangRocketOptionSku
        sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
        with caplog.at_level("WARNING"):
            sync.ingest_rocket_sales(db, "A01029796", [
                {"option_id": "95536607339", "sku_id": "99999999", "date": "2026-07-26",
                 "qty": 1, "revenue": "1000"},
            ])
        assert db.query(CoupangRocketOptionSku).filter_by(
            option_id="95536607339").one().sku_id == "99999999"
        assert "브리지 변경" in caplog.text


def test_ingest_sales_updates_on_resync(db):
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    sync.ingest_rocket_sales(db, "A01029796", [
        {"option_id": "95536607339", "date": "2026-07-24", "qty": 25, "revenue": "422500"},
    ])
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95536607339").one()
    assert row.qty == 25 and row.revenue == Decimal("422500")   # 확정치 교체


def test_ingest_sales_isolated_per_vendor(db):
    sync.ingest_rocket_sales(db, "A01029796", _SALES_ROWS)
    sync.ingest_rocket_sales(db, "A99999999", _SALES_ROWS)
    assert db.query(CoupangRocketSalesDaily).count() == 4   # 계정축이 grain에 포함


def test_ingest_promotion_and_idempotent(db):
    r = sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    assert r["ingested"] == 1 and r["skipped"] == 1
    row = db.query(CoupangRocketPromotion).filter_by(request_id="687878").one()
    assert row.vendor_id == "A01029796"
    assert row.start_at == datetime(2026, 7, 24, 0, 1, 0)
    assert row.share_ratio == Decimal("100.00")
    sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    assert db.query(CoupangRocketPromotion).count() == 1


def test_ingest_promotion_updates_status_on_resync(db):
    sync.ingest_rocket_promotions(db, "A01029796", _PROMO_ROWS)
    sync.ingest_rocket_promotions(db, "A01029796", [
        {"request_id": "687878", "status": "FINISHED"},
    ])
    row = db.query(CoupangRocketPromotion).filter_by(request_id="687878").one()
    assert row.status == "FINISHED"
    assert row.raw == {"requestId": 687878}   # raw는 미제공 시 기존값 보존


_OLD_STAMP = datetime(2020, 1, 1, 0, 0, 0)   # '오래 전에 수집됨' — 초 단위 해상도 함정 회피


def _seed_coupon(db, coupon_id: str, account_key: str = "COUPANG_WING1", kind: str = "INSTANT"):
    db.add(CoupangCoupon(
        account_key=account_key, vendor_id="A00123456",
        coupon_kind=kind, coupon_id=coupon_id, synced_at=_OLD_STAMP,
    ))
    db.commit()


def test_ingest_coupon_used_amount(db):
    _seed_coupon(db, "94177420")
    _seed_coupon(db, "93654161")
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", _COUPON_ROWS)
    assert r["updated"] == 2 and r["not_found"] == 0 and r["skipped"] == 1
    row = db.query(CoupangCoupon).filter_by(coupon_id="94177420").one()
    assert row.used_amount == Decimal("156000")     # D-CPP-3 권위값
    assert row.used_amount_source == "wing_ui"      # 출처 라벨 필수
    assert row.used_amount_synced_at is not None


def test_ingest_coupon_used_amount_does_not_create_rows(db):
    """없는 쿠폰에 행을 만들지 않는다 — 쿠폰 메타 없는 유령 행 방지(원칙22)."""
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", _COUPON_ROWS)
    assert r["updated"] == 0 and r["not_found"] == 2
    assert db.query(CoupangCoupon).count() == 0
    assert "94177420" in r["not_found_coupon_ids"]


def test_ingest_coupon_used_amount_account_scoped(db):
    """같은 coupon_id라도 다른 계정의 행은 건드리지 않는다."""
    _seed_coupon(db, "94177420", account_key="COUPANG_WING2")
    r = sync.ingest_coupon_used_amount(db, "COUPANG_WING1", [
        {"coupon_id": "94177420", "used_amount": 156000},
    ])
    assert r["updated"] == 0 and r["not_found"] == 1
    assert db.query(CoupangCoupon).filter_by(coupon_id="94177420").one().used_amount is None


def test_ingest_coupon_used_amount_source_label(db):
    _seed_coupon(db, "94177420")
    sync.ingest_coupon_used_amount(
        db, "COUPANG_WING1", [{"coupon_id": "94177420", "used_amount": 1}], source="manual"
    )
    assert db.query(CoupangCoupon).one().used_amount_source == "manual"


def test_ingest_coupon_used_amount_does_not_touch_download_kind(db):
    """같은 coupon_id의 DOWNLOAD 행에는 붙지 않는다 — 그레인은 (account, kind, coupon_id)다.

    DOWNLOAD는 이미 usage_amount(다운로드쿠폰 사용량)라는 다른 축을 들고 있어, 셀러부담
    권위값(D-CPP-3)이 거기 앉으면 어느 쪽이 우리 실부담인지 영영 구분되지 않는다.
    """
    _seed_coupon(db, "94177420", kind="DOWNLOAD")
    r = sync.ingest_coupon_used_amount(
        db, "COUPANG_WING1", [{"coupon_id": "94177420", "used_amount": 156000}]
    )
    assert r["updated"] == 0
    # ★영구적 실패(wrong_kind)와 일시적 실패(not_found)를 가른다 — 전자는 재시도해도 안 붙는다
    assert r["wrong_kind"] == 1 and r["not_found"] == 0
    assert db.query(CoupangCoupon).one().used_amount is None


def test_ingest_coupon_used_amount_freezes_collection_stamp(db):
    """수집 시각(synced_at)은 이 push로 갱신되지 않는다 — 죽은 수집기가 신선해 보이면 안 된다.

    ★_OLD_STAMP로 심는 이유: server_default(CURRENT_TIMESTAMP)는 초 해상도라, 같은 초 안에
      끝나는 테스트에서는 onupdate가 발동해도 값이 같아 **가드가 통과해 버린다**(빈 테스트).
    """
    _seed_coupon(db, "94177420")
    sync.ingest_coupon_used_amount(
        db, "COUPANG_WING1", [{"coupon_id": "94177420", "used_amount": 156000}]
    )
    db.expire_all()
    row = db.query(CoupangCoupon).one()
    assert row.synced_at == _OLD_STAMP             # 수집 시각 동결(onupdate=now를 이긴다)
    assert row.used_amount_synced_at is not None   # push 시각은 따로 남는다


# ═══ 라우트 3종 (X-Ingest-Token) ═══
# 인증·입력검증은 형제 ingest 라우트(/rocket/po, /rocket/settlement)와 같은 규칙이어야 한다.
#   토큰 미설정·불일치 모두 401(서버 상태 비노출), 계약 위반 body는 500이 아니라 400.
_TOKEN = "test-token-123"
_BASE = "/api/coupang/ops"
_ROUTES = ["/rocket/sales/ingest", "/rocket/promotion/ingest", "/coupon/used-amount/ingest"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)
    from app.main import app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    seed = TestingSession()
    yield TestClient(app), seed
    seed.close()
    app.dependency_overrides.clear()


@pytest.mark.parametrize("url", _ROUTES)
def test_route_requires_token(client, url):
    c, _ = client
    body = {"vendor_id": "A01029796", "account_key": "COUPANG_WING1", "rows": [{"x": 1}]}
    assert c.post(_BASE + url, json=body).status_code == 401                       # 헤더 없음
    assert c.post(_BASE + url, json=body,
                  headers={"X-Ingest-Token": "wrong"}).status_code == 401          # 불일치


@pytest.mark.parametrize("url", _ROUTES)
def test_route_rejects_oversized_grain_key(client, url):
    """계정축도 그레인 키다 — SQLite는 초과를 통과시켜 평행 축을 만들고 PG는 배치를 죽인다."""
    c, _ = client
    key = "account_key" if "coupon" in url else "vendor_id"
    r = c.post(_BASE + url, headers={"X-Ingest-Token": _TOKEN},
               json={key: "X" * 21, "rows": [{"coupon_id": "1", "used_amount": 1,
                                              "option_id": "1", "date": "2026-07-24", "qty": 1,
                                              "request_id": "1"}]})
    assert r.status_code == 400


@pytest.mark.parametrize("url", _ROUTES)
def test_route_rejects_bad_body_with_400_not_500(client, url):
    c, _ = client
    h = {"X-Ingest-Token": _TOKEN}
    assert c.post(_BASE + url, json={}, headers=h).status_code == 400              # 계정축 없음
    key = "account_key" if "coupon" in url else "vendor_id"
    val = "COUPANG_WING1" if "coupon" in url else "A01029796"
    assert c.post(_BASE + url, json={key: val}, headers=h).status_code == 400       # rows 없음
    assert c.post(_BASE + url, json={key: val, "rows": "nope"},
                  headers=h).status_code == 400                                     # rows 비-리스트


def test_route_sales_ingest_happy_path_and_source_label(client):
    c, s = client
    r = c.post(_BASE + "/rocket/sales/ingest", headers={"X-Ingest-Token": _TOKEN}, json={
        "vendor_id": "A01029796", "source": "excel",
        "rows": [{"option_id": "95536607339", "date": "2026-07-24", "qty": 18,
                  "revenue": "304200", "sku_id": "62178970"}],
    })
    assert r.status_code == 200 and r.json()["ingested"] == 1
    row = s.query(CoupangRocketSalesDaily).one()
    assert row.vendor_id == "A01029796" and row.source == "excel"   # 폴백 경로 라벨 보존


def test_route_sales_ingest_surfaces_blank_observation_counters(client):
    """운영자가 보는 건 응답 필드다 — 파서에만 있고 응답에 없으면 아무도 못 본다."""
    c, _ = client
    r = c.post(_BASE + "/rocket/sales/ingest", headers={"X-Ingest-Token": _TOKEN}, json={
        "vendor_id": "A01029796",
        "rows": [{"option_id": "O1", "date": "2026-07-24", "qty": None, "revenue": None},
                 {"option_id": "O2", "date": "2026-07-24", "qty": None, "revenue": None}],
    })
    body = r.json()
    assert body["ingested"] == 2 and body["accepted"] == 2
    # 판정 기준은 ingested가 아니라 accepted(중복 섞인 배치에서 어긋난다)
    assert body["blank_observations"] == body["accepted"]
    assert body["blank_qty"] == 2 and body["blank_revenue"] == 2


def test_route_promotion_ingest_happy_path(client):
    c, s = client
    r = c.post(_BASE + "/rocket/promotion/ingest", headers={"X-Ingest-Token": _TOKEN}, json={
        "vendor_id": "A01029796",
        "rows": [{"request_id": "687878", "start_at": "2026-07-24 00:01:00",
                  "end_at": "2026-07-26 23:59:59", "share_ratio": "100"}],
    })
    assert r.status_code == 200 and r.json()["ingested"] == 1
    assert s.query(CoupangRocketPromotion).one().end_at == datetime(2026, 7, 26, 23, 59, 59)


def test_route_coupon_used_amount_reports_not_found(client):
    """없는 쿠폰은 200 + not_found로 표면화 — 행을 지어내지 않는다."""
    c, s = client
    r = c.post(_BASE + "/coupon/used-amount/ingest", headers={"X-Ingest-Token": _TOKEN}, json={
        "account_key": "COUPANG_WING1",
        "rows": [{"coupon_id": "94177420", "used_amount": 156000}],
    })
    assert r.status_code == 200
    assert r.json()["updated"] == 0 and r.json()["not_found"] == 1
    assert s.query(CoupangCoupon).count() == 0


# ══════════════════════════════════════════════════════════════════
# 판매분석 퍼널 지표 + 옵션 속성 (D-CPP-11, 2026-08-06)
#   값은 라이브 실측: 2026-08-04 옵션 95752961189(갤럭시 Z 폴드8) —
#   조회 1,540 · 주문 162 · 방문자 765 · 판매 152 · 매출 3,024,800 · 평점 4.6 · 리뷰 10,575.
#   전환율 162/1540 = 0.10519… 가 쿠팡의 pvToOrder와 자릿수까지 일치한다.
# ══════════════════════════════════════════════════════════════════
_FUNNEL_ROW = {
    "option_id": "95752961189", "sku_id": "76350897", "date": "2026-08-04",
    "qty": 152, "revenue": "3024800", "visitors": 765,
    "conversion_rate": "0.1051948052", "page_views": 1540, "orders": 162,
    "brand_name": "오하이",
    "category_path": "가전/디지털 > 휴대폰/태블릿PC/액세서리 > 보호필름",
    "is_item_winner": False, "is_oos": True,
    "rating_count": 10575, "rating_score": "4.6",
    "product_name": "오하이 폴드 플립 지문방지 무광택 액정보호 필름, 갤럭시 Z 폴드8",
}


def test_parse_sales_maps_funnel_metrics():
    """조회·주문이 계약대로 실린다 — 그리고 전환율 == 주문/조회로 검산된다."""
    rec = rp.parse_sales_rows([_FUNNEL_ROW])[0]
    assert rec["page_views"] == 1540
    assert rec["orders"] == 162
    # 쿠팡이 준 전환율과 우리가 담은 두 값의 몫이 같아야 매핑이 맞다.
    assert abs(Decimal(rec["orders"]) / Decimal(rec["page_views"]) - rec["conversion_rate"]) < Decimal("0.000001")


def test_parse_sales_funnel_absent_stays_none_not_zero():
    """조회·주문이 없는 행은 None으로 남는다 — 0이면 '조회 0회인 날'로 둔갑한다."""
    rec = rp.parse_sales_rows([{"option_id": "1", "date": "2026-08-04", "qty": 3}])[0]
    assert rec["page_views"] is None and rec["orders"] is None


def test_parse_sales_bool_does_not_coerce_unknown_to_false():
    """모르는 값을 False로 접지 않는다 — is_oos=False는 '품절 아님'이라는 관측이다."""
    rec = rp.parse_sales_rows([{**_FUNNEL_ROW, "is_oos": None, "is_item_winner": "쓰레기"}])[0]
    assert rec["is_oos"] is None
    assert rec["is_item_winner"] is None
    # 명시적 False는 살아남는다(관측이므로).
    assert rp.parse_sales_rows([{**_FUNNEL_ROW, "is_oos": False}])[0]["is_oos"] is False


def test_ingest_funnel_metrics_land_on_daily_row(db):
    sync.ingest_rocket_sales(db, "A01029796", [_FUNNEL_ROW])
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95752961189").one()
    assert row.page_views == 1540 and row.orders == 162
    assert row.qty == 152 and row.revenue == Decimal("3024800.00")   # 기존 축 불변


def test_ingest_option_attrs_go_to_option_table_not_daily(db):
    """속성은 **현재값**이라 일별이 아니라 option_sku로 간다(가짜 시계열 방지)."""
    from app.models import CoupangRocketOptionSku

    sync.ingest_rocket_sales(db, "A01029796", [_FUNNEL_ROW])
    opt = db.query(CoupangRocketOptionSku).filter_by(option_id="95752961189").one()
    assert opt.brand_name == "오하이"
    assert opt.is_oos is True and opt.is_item_winner is False
    assert opt.rating_count == 10575 and opt.rating_score == Decimal("4.60")
    assert opt.attrs_observed_at is not None
    # 일별 테이블엔 그런 컬럼이 아예 없다(모델 계약).
    assert not hasattr(db.query(CoupangRocketSalesDaily).first(), "is_oos")


def test_ingest_none_does_not_erase_known_funnel_or_attrs(db):
    """속성을 안 보내는 경로(excel 폴백·구버전 페처)가 이미 아는 값을 지우면 안 된다."""
    from app.models import CoupangRocketOptionSku

    sync.ingest_rocket_sales(db, "A01029796", [_FUNNEL_ROW])
    first_seen = db.query(CoupangRocketOptionSku).filter_by(option_id="95752961189").one().attrs_observed_at
    # 같은 그레인을 속성/퍼널 없이 재적재(구 계약 모양).
    sync.ingest_rocket_sales(db, "A01029796", [{
        "option_id": "95752961189", "sku_id": "76350897", "date": "2026-08-04",
        "qty": 152, "revenue": "3024800",
    }], source="excel")
    row = db.query(CoupangRocketSalesDaily).filter_by(option_id="95752961189").one()
    assert row.page_views == 1540 and row.orders == 162     # 안 지워졌다
    opt = db.query(CoupangRocketOptionSku).filter_by(option_id="95752961189").one()
    assert opt.is_oos is True and opt.rating_count == 10575
    # ★아무 속성도 안 왔으면 관측 시각을 전진시키지 않는다 — 반년 전 값이 오늘 값처럼 보이면 안 된다.
    assert opt.attrs_observed_at == first_seen
