# test_rg_layer2_gaps.py — RG 층2(옵션 엑셀) 결손 조회(자가치유 D1).
#   ★존재 이유: 층2는 매 회차 '최신 1주기'만 받아(rg_max_periods=1) 버튼 캐던스가 정산주기 생성
#   속도보다 느려지면 그 사이 주기가 영구 공백이 됐다(실측 WING1 04-20~05-03, WING2 04-27~05-03).
#   페처가 "무엇이 비었나"를 물어볼 수 있어야 스스로 메운다. 여기서 고정하는 것:
#     ① 층1 있고 층2 없으면 결손 ② 층2 있으면 아님 ③ 층1 금액 0이면 아님(빈 주기 루프 방지)
#     ④ 리포트가 커버하는 fee_type 전부 0건일 때만 결손 ⑤ 창(days) 밖은 제외 ⑥ 미매핑 리포트 격리
#     ⑦ 최신 우선 정렬 ⑧ 엔드포인트 토큰 인증·account_key 검증·읽기 전용
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import CoupangRgSettlementFee
from app.services.coupang import rg_settlement_sync
from app.services.coupang.rg_settlement_sync import layer2_gaps
from app.utils.kst import kst_now

_TOKEN = "test-token-gap"
_ACC = "COUPANG_WING1"
_URL = "/api/coupang/ops/wing/rg-settlement/layer2-gaps"

# WAREHOUSING_SHIPPING 엑셀 = 입출고비+배송비 2시트(= 커버 fee_type 2개).
_WS = "WAREHOUSING_SHIPPING"
_WS_FEES = ("warehousing", "delivery")


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


def _today() -> date:
    return kst_now().date()


def _period(days_ago: int) -> tuple[date, date]:
    """days_ago일 전에 끝난 (월~일) 주기."""
    end = _today() - timedelta(days=days_ago)
    return end - timedelta(days=6), end


def _acct(db, period, fee_type, amount=Decimal("1000"), account_key=_ACC):
    """층1(계정 단위) 행 — vendor_item_id='' sentinel."""
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=period[0], recognition_date_to=period[1],
        fee_type=fee_type, vendor_item_id="", raw_type="status/api", amount=amount))
    db.commit()


def _option(db, period, fee_type, option_id="9551", amount=Decimal("500"), account_key=_ACC):
    """층2(옵션 단위) 행 — vendor_item_id=옵션ID."""
    db.add(CoupangRgSettlementFee(
        account_key=account_key, recognition_date_from=period[0], recognition_date_to=period[1],
        fee_type=fee_type, vendor_item_id=option_id, raw_type="xlsx", amount=amount))
    db.commit()


def _ends(result) -> list[str]:
    return [g["recognition_date_to"] for g in result["gaps"]]


# ── ① 층1만 있고 층2가 없으면 결손 ────────────────────────────────────
def test_period_without_option_rows_is_a_gap(db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
    res = layer2_gaps(db, _ACC, days=35, report_types=[_WS])

    assert res["periods_checked"] == 1
    assert _ends(res) == [p[1].isoformat()]
    assert res["gaps"][0]["recognition_date_from"] == p[0].isoformat()
    assert res["gaps"][0]["missing_report_types"] == [_WS]
    assert res["covered_fee_types"] == ["delivery", "warehousing"]
    assert res["unmapped_report_types"] == []


# ── ② 층2가 이미 있으면 결손 아님 ─────────────────────────────────────
def test_period_with_option_rows_is_not_a_gap(db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
        _option(db, p, ft)
    assert layer2_gaps(db, _ACC, days=35, report_types=[_WS])["gaps"] == []


def test_partial_coverage_is_not_a_gap(db):
    """일부 fee_type만 0건은 결손이 아니다 — 그 주 배송비가 실제 0이면 영구 재다운로드 루프가 된다."""
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
    _option(db, p, "warehousing")          # 입출고비만 옵션 행 존재
    assert layer2_gaps(db, _ACC, days=35, report_types=[_WS])["gaps"] == []


# ── ③ 빈 주기 가드: 층1 금액이 전부 0이면 항목화할 게 없다 ──────────────
def test_zero_amount_period_is_not_a_gap(db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft, amount=Decimal("0"))
    assert layer2_gaps(db, _ACC, days=35, report_types=[_WS])["gaps"] == []


def test_any_nonzero_component_still_makes_a_gap(db):
    """한 컴포넌트라도 금액이 있으면 그 엑셀엔 받을 상세가 있다."""
    p = _period(10)
    _acct(db, p, "warehousing", amount=Decimal("0"))
    _acct(db, p, "delivery", amount=Decimal("130599"))
    assert _ends(layer2_gaps(db, _ACC, days=35, report_types=[_WS])) == [p[1].isoformat()]


def test_negative_amount_counts_as_something_to_itemize(db):
    """환급(음수)도 항목이 있다 — 0만 '없음'이다."""
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft, amount=Decimal("-2000"))
    assert _ends(layer2_gaps(db, _ACC, days=35, report_types=[_WS])) == [p[1].isoformat()]


# ── ④ 리포트별 판정 ───────────────────────────────────────────────────
def test_missing_report_types_are_per_report(db):
    """입출고/배송은 받았고 판매수수료만 없으면 CATEGORY_TR 하나만 결손으로 나온다."""
    p = _period(10)
    for ft in (*_WS_FEES, "sale_fee"):
        _acct(db, p, ft)
    for ft in _WS_FEES:
        _option(db, p, ft)
    res = layer2_gaps(db, _ACC, days=35, report_types=[_WS, "CATEGORY_TR"])
    assert [g["missing_report_types"] for g in res["gaps"]] == [["CATEGORY_TR"]]


def test_duplicate_report_types_are_deduped(db):
    """중복이 missing에 반복되면 페처가 같은 리포트를 두 번 요청 → dup → 긴 백오프(리뷰 [P2-8])."""
    p = _period(3)
    _acct(db, p, "warehousing")
    out = layer2_gaps(db, _ACC, days=35, report_types=[_WS, _WS, _WS])
    assert out["report_types"] == [_WS]
    assert out["gaps"][0]["missing_report_types"] == [_WS]


def test_unmapped_report_types_are_reported_not_guessed(db):
    """파서 없는 리포트는 결손 판정 불가 — 결손으로 세지 않고 목록으로만 밝힌다."""
    p = _period(10)
    _acct(db, p, "warehousing")
    res = layer2_gaps(db, _ACC, days=35, report_types=["PRODUCT_SIZE_COMPARISON"])
    assert res["gaps"] == [] and res["covered_fee_types"] == []
    assert res["unmapped_report_types"] == ["PRODUCT_SIZE_COMPARISON"]


def test_default_report_types_cover_all_mapped(db):
    res = layer2_gaps(db, _ACC, days=35)
    assert set(res["report_types"]) == set(rg_settlement_sync._REPORT_TYPE_FEE_TYPES)
    assert res["unmapped_report_types"] == []


# ── ⑤ 창(days) ────────────────────────────────────────────────────────
def test_period_outside_window_is_ignored(db):
    inside, outside = _period(10), _period(60)
    for p in (inside, outside):
        for ft in _WS_FEES:
            _acct(db, p, ft)
    res = layer2_gaps(db, _ACC, days=35, report_types=[_WS])
    assert res["periods_checked"] == 1
    assert _ends(res) == [inside[1].isoformat()]

    # 백필 오버라이드: 창을 넓히면 옛 주기도 열거·치유 대상이 된다(스코프: 경로만 유지).
    wide = layer2_gaps(db, _ACC, days=90, report_types=[_WS])
    assert _ends(wide) == [inside[1].isoformat(), outside[1].isoformat()]


def test_days_is_clamped_and_garbage_falls_back(db):
    assert layer2_gaps(db, _ACC, days=0)["days"] == 1
    assert layer2_gaps(db, _ACC, days=9999)["days"] == 400
    assert layer2_gaps(db, _ACC, days="garbage")["days"] == rg_settlement_sync.LAYER2_GAP_DEFAULT_DAYS


# ── ⑥ 계정 격리 ───────────────────────────────────────────────────────
def test_other_account_rows_do_not_leak(db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft, account_key="COUPANG_WING2")
    assert layer2_gaps(db, _ACC, days=35, report_types=[_WS])["gaps"] == []
    assert _ends(layer2_gaps(db, "COUPANG_WING2", days=35, report_types=[_WS])) == [p[1].isoformat()]


def test_other_account_option_rows_do_not_fill_our_gap(db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
        _option(db, p, ft, account_key="COUPANG_WING2")
    assert _ends(layer2_gaps(db, _ACC, days=35, report_types=[_WS])) == [p[1].isoformat()]


def test_unknown_account_key_raises(db):
    with pytest.raises(ValueError):
        layer2_gaps(db, "COUPANG_WING9")


# ── ⑦ 최신 우선(페처가 앞에서부터 상한만큼 자른다) ──────────────────────
def test_gaps_are_newest_first(db):
    ends = []
    for ago in (5, 12, 19):
        p = _period(ago)
        ends.append(p[1].isoformat())
        for ft in _WS_FEES:
            _acct(db, p, ft)
    assert _ends(layer2_gaps(db, _ACC, days=35, report_types=[_WS])) == ends  # 5일전 → 19일전


def test_month_boundary_split_periods_are_separate_gaps(db):
    """월경계 분할 주기(04-27~04-30 | 05-01~05-03)는 서로 다른 결손이다."""
    ago = (_today() - date(2026, 5, 3)).days
    if ago < 0 or ago > 300:      # 고정 날짜가 창 밖이면 상대 날짜로 대체
        pytest.skip("고정 날짜가 조회 창 밖")
    for pfrom, pto in ((date(2026, 4, 27), date(2026, 4, 30)), (date(2026, 5, 1), date(2026, 5, 3))):
        for ft in _WS_FEES:
            _acct(db, (pfrom, pto), ft)
    res = layer2_gaps(db, _ACC, days=ago + 30, report_types=[_WS])
    assert _ends(res) == ["2026-05-03", "2026-04-30"]


# ── ⑧ 엔드포인트 ──────────────────────────────────────────────────────
@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("AD_INGEST_TOKEN", _TOKEN)

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_returns_gaps(client, db):
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
    r = client.get(_URL, params={"account_key": _ACC, "days": 35, "report_types": [_WS]},
                   headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["account_key"] == _ACC and body["days"] == 35
    assert body["gaps"] == [{
        "recognition_date_from": p[0].isoformat(),
        "recognition_date_to": p[1].isoformat(),
        "missing_report_types": [_WS],
    }]


def test_endpoint_requires_token(client, db):
    assert client.get(_URL).status_code == 401
    assert client.get(_URL, headers={"X-Ingest-Token": "wrong"}).status_code == 401


def test_endpoint_rejects_unknown_account(client):
    r = client.get(_URL, params={"account_key": "COUPANG_WING9"},
                   headers={"X-Ingest-Token": _TOKEN})
    assert r.status_code == 400


def test_endpoint_rejects_out_of_range_days(client):
    for days in (0, 401):
        r = client.get(_URL, params={"days": days}, headers={"X-Ingest-Token": _TOKEN})
        assert r.status_code == 422


def test_endpoint_is_read_only(client, db):
    """조회가 데이터를 건드리면 안 된다(회계 테이블)."""
    p = _period(10)
    for ft in _WS_FEES:
        _acct(db, p, ft)
    before = db.query(CoupangRgSettlementFee).count()
    client.get(_URL, params={"report_types": [_WS]}, headers={"X-Ingest-Token": _TOKEN})
    assert db.query(CoupangRgSettlementFee).count() == before
