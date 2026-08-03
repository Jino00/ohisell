# test_ohitech_ad_option_ingest.py — 오하이테크(1P) 옵션 광고비 적재 (트랙 ohitech-ad D-13)
#
# 지키는 불변식 하나: **한 머니 행에 정의가 다른 두 writer를 붙이지 않는다.**
#   A01029796의 계정 단위 광고비(`coupang_ad_report` Retail)는 report/SALES 페처가
#   **전체(ALL_DELIVERED) 기준**으로 쓴다(D-10). Billboard XLSX는 **PA 기준**이라 같은 행을
#   덮으면 나중에 쓴 쪽이 조용히 이기고, 그 사고는 순이익에서만 드러난다.
#   → 옵션 경로는 `options_only=True`로 머니 테이블을 **구조적으로** 못 건드리고,
#     머니 경로로 로켓 벤더 XLSX가 들어오면 422로 거부한다.
#
# 라이브 실측(D-12, 2026-07-27~08-02): 7,002행·429옵션·전량 Retail·컬럼 44개.
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AdCost, CoupangAdOptionDaily, CoupangAdReport
from app.routers.ad_costs import ingest_coupang_ad_xlsx_content

ROCKET_VENDOR = "A01029796"

# D-12 실측 헤더(keyword 포맷) 중 파서가 쓰는 열만 같은 인덱스로 재현.
#   [0]날짜 [2]판매방식 [8]광고집행 옵션ID [10]광고전환매출발생 옵션ID
#   [13]노출수 [14]클릭수 [15]광고비 [17]총 주문수 [20]총 판매수량 [23]총 전환매출액
_HEADER = [
    "날짜", "과금 방식", "판매방식", "광고유형", "캠페인 ID", "캠페인명", "광고그룹",
    "광고집행 상품명", "광고집행 옵션ID", "광고전환매출발생 상품명", "광고전환매출발생 옵션ID",
    "광고 노출 지면", "키워드", "노출수", "클릭수", "광고비", "클릭률",
    "총 주문수(1일)", "직접 주문수(1일)", "간접 주문수(1일)",
    "총 판매수량(1일)", "직접 판매수량(1일)", "간접 판매수량(1일)",
    "총 전환매출액(1일)", "직접 전환매출액(1일)", "간접 전환매출액(1일)",
]


def _row(day: str, opt: str, spend: float, impr: int = 10, clicks: int = 1):
    r = [None] * len(_HEADER)
    r[0], r[2] = float(day), "Retail"
    r[8] = r[10] = float(opt)
    r[13], r[14], r[15] = impr, clicks, spend
    r[17], r[20], r[23] = 0, 0, 0
    return r


def _xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def rocket_env(monkeypatch):
    monkeypatch.setenv("COUPANG_ROCKET_VENDOR_ID", ROCKET_VENDOR)


_FILE = f"{ROCKET_VENDOR}_pa_daily_20260727_20260802.xlsx"


class TestOptionsOnlyDoesNotTouchMoney:
    def test_옵션만_적재하고_머니_테이블은_비어있다(self, db, rocket_env):
        content = _xlsx([_row("20260727", "70252569329", 8000),
                         _row("20260727", "70252569361", 2000),
                         _row("20260728", "70252569329", 5000)])
        result, rfrom, rto = ingest_coupang_ad_xlsx_content(
            content, _FILE, db, options_only=True)

        assert db.query(CoupangAdOptionDaily).count() == 3
        # ★핵심: 머니 테이블은 손도 대지 않는다
        assert db.query(CoupangAdReport).count() == 0, "계정 총액 행을 덮었다 — 순이익이 흔들린다"
        assert db.query(AdCost).count() == 0, "ad_costs에 이중 적재됐다"
        # 이익 재계산도 걸리면 안 된다(머니 값이 안 변했으므로)
        assert (rfrom, rto) == (None, None)
        assert result["options_only"] is True
        assert result["option_rows"] == 3
        assert result["option_spend"] == 15000, "옵션 합계를 돌려줘야 계정 총액과 대조할 수 있다"

    def test_옵션_금액이_행별로_보존된다(self, db, rocket_env):
        ingest_coupang_ad_xlsx_content(
            _xlsx([_row("20260727", "70252569329", 8000)]), _FILE, db, options_only=True)
        row = db.query(CoupangAdOptionDaily).one()
        assert row.vendor_id == ROCKET_VENDOR
        assert row.sell_type == "Retail"
        assert row.ad_option_id == "70252569329"
        assert row.ad_spend == Decimal("8000")
        assert row.report_date == date(2026, 7, 27)

    def test_재적재는_멱등이다(self, db, rocket_env):
        rows = [_row("20260727", "70252569329", 8000), _row("20260727", "70252569361", 2000)]
        for _ in range(2):
            ingest_coupang_ad_xlsx_content(_xlsx(rows), _FILE, db, options_only=True)
        assert db.query(CoupangAdOptionDaily).count() == 2

    def test_옵션ID가_없는_포맷은_422(self, db, rocket_env):
        """adGroup 포맷(옵션ID 컬럼 없음)을 이 경로로 밀면 조용히 0행이 아니라 거부."""
        no_opt = [h for h in _HEADER if "옵션ID" not in h]
        wb = openpyxl.Workbook(); ws = wb.active; ws.append(no_opt)
        r = [None] * len(no_opt)
        r[0], r[2] = 20260727.0, "Retail"
        ws.append(r)
        buf = io.BytesIO(); wb.save(buf)
        with pytest.raises(HTTPException) as e:
            ingest_coupang_ad_xlsx_content(buf.getvalue(), _FILE, db, options_only=True)
        assert e.value.status_code == 422


class TestMoneyPathGuard:
    def test_로켓_벤더_XLSX는_머니_경로로_못_들어온다(self, db, rocket_env):
        """★문서 규칙(S1c 체크리스트 ②)을 코드로 승격. 수동 업로드든 오픽스 엔드포인트든
        경로와 무관하게 같은 결론이 나야 한다."""
        content = _xlsx([_row("20260727", "70252569329", 8000)])
        with pytest.raises(HTTPException) as e:
            ingest_coupang_ad_xlsx_content(content, _FILE, db)   # options_only=False
        assert e.value.status_code == 422
        assert "option-ingest" in e.value.detail, "대안 경로를 안내해야 한다"
        assert db.query(CoupangAdOptionDaily).count() == 0, "거부인데 일부라도 적재되면 안 된다"

    def test_오픽스는_영향을_받지_않는다(self, db, rocket_env, monkeypatch):
        """공용 파서를 건드렸으므로 기존 머니 경로가 **전부** 그대로인지 확인한다.

        ad_costs 적재는 (vendor_id, suffix)→channel_id 조회가 성립해야 일어나므로 채널까지 세운다
        — 그걸 안 세우면 'ad_costs 안 씀'이 회귀인지 픽스처 부족인지 구분되지 않는다."""
        from app.models import Channel
        db.add(Channel(id=9, code="COUPANG_WING1", name="오픽스윙", platform="coupang",
                       channel_type="marketplace"))
        db.commit()
        monkeypatch.setenv("COUPANG_WING1_VENDOR_ID", "A01564720")

        ofix_file = "A01564720_pa_daily_20260727_20260802.xlsx"
        rows = [_row("20260727", "94277472815", 3000)]
        rows[0][2] = "3P"   # 오픽스는 3P/2P
        result, rfrom, rto = ingest_coupang_ad_xlsx_content(_xlsx(rows), ofix_file, db)

        assert db.query(CoupangAdReport).count() == 1, "오픽스 계정 총액 적재가 죽었다"
        assert db.query(AdCost).count() == 1, "오픽스 ad_costs 적재가 죽었다"
        assert db.query(CoupangAdOptionDaily).count() == 1, "오픽스 옵션 적재가 죽었다"
        assert result["options_only"] is False
        assert (rfrom, rto) == (date(2026, 7, 27), date(2026, 7, 27)), "이익 재계산이 안 걸린다"

    def test_env_미설정이면_가드는_조용히_비활성(self, db, monkeypatch):
        """운영 환경변수에 의존하는 가드다 — 미설정 시 기존 동작을 바꾸지 않는다(회귀 방지)."""
        monkeypatch.delenv("COUPANG_ROCKET_VENDOR_ID", raising=False)
        result, _f, _t = ingest_coupang_ad_xlsx_content(
            _xlsx([_row("20260727", "70252569329", 8000)]), _FILE, db)
        assert result["report_rows"] == 1
