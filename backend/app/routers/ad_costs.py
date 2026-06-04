# routers/ad_costs.py — 광고비 조회 + GFA CSV 업로드 + SA/Meta 동기화 + 쿠팡 XLSX 업로드 API
from __future__ import annotations

import csv
import io
import logging
import os
import re
from app.utils.kst import kst_now, kst_today
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_ad_db, get_db
from app.schemas import AdSpendByOption, AdSpendDaily
from app.services.ad_cost_reader import get_ad_spend_by_option, get_daily_ad_spend

log = logging.getLogger(__name__)

# profit_calculator와 동일한 키워드 목록 (source 생성 기준)
_NAVER_SA_KEYWORDS = [
    "지문방지", "강화유리", "종이질감", "사생활", "갤럭시탭", "아이패드", "아이폰",
    "갤럭시", "셀카봉", "뮤패드", "케이스",
]
_META_KEYWORDS = [
    "지문방지필름", "골프필름", "버디필름", "강화유리", "셀카봉", "문캅스", "일미리케이스",
]
_KEYWORD_ALIAS = {"샐카봉": "셀카봉"}


def _extract_naver_sa_keyword(campaign_name: str) -> str:
    name = campaign_name or ""
    for kw in _NAVER_SA_KEYWORDS:
        if kw in name:
            return kw
    return "기타"


def _extract_meta_keyword(campaign_name: str) -> str:
    name = campaign_name or ""
    for kw in _META_KEYWORDS:
        if kw in name:
            return _KEYWORD_ALIAS.get(kw, kw)
    return "기타"


def _upsert_ad_cost(db: Session, channel_id: int, ad_date: date, spend: Decimal, source: str) -> None:
    """ad_costs 테이블에 특정 날짜/source 레코드를 delete+insert (멱등)."""
    db.execute(
        text("DELETE FROM ad_costs WHERE channel_id = :cid AND source = :src AND ad_date = :dt"),
        {"cid": channel_id, "src": source, "dt": ad_date.isoformat()},
    )
    db.execute(
        text("""
            INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
            VALUES (:cid, NULL, :dt, :spend, NULL, :src, datetime('now'))
        """),
        {"cid": channel_id, "dt": ad_date.isoformat(), "spend": str(spend), "src": source},
    )

def _upsert_ad_revenue(db: Session, channel_id: int, ad_date: date, revenue: Decimal, source: str) -> None:
    """전환매출 전용 행 upsert. ad_spend=0(광고비 중복합산 방지), ad_revenue=전환매출."""
    db.execute(
        text("DELETE FROM ad_costs WHERE channel_id = :cid AND source = :src AND ad_date = :dt"),
        {"cid": channel_id, "src": source, "dt": ad_date.isoformat()},
    )
    db.execute(
        text("""
            INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
            VALUES (:cid, NULL, :dt, 0, :rev, :src, datetime('now'))
        """),
        {"cid": channel_id, "dt": ad_date.isoformat(), "rev": str(revenue), "src": source},
    )


# 검색광고 전환매출 전용 source (ad_spend=0, ad_revenue=구매 전환매출)
NAVER_SA_CONV_SOURCE = "naver_sa:conv"

router = APIRouter(prefix="/api/ad-costs", tags=["ad-costs"])


@router.get("/daily", response_model=list[AdSpendDaily])
def daily_ad_spend(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    ad_db=Depends(get_ad_db),
):
    """일별 총 광고비 조회"""
    if ad_db is None:
        return []
    return get_daily_ad_spend(
        ad_db,
        date.fromisoformat(date_from),
        date.fromisoformat(date_to),
    )


@router.get("/by-option", response_model=list[AdSpendByOption])
def ad_spend_by_option(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    option_id: str | None = Query(None),
    ad_db=Depends(get_ad_db),
):
    """상품(option_id)별 광고비 집계"""
    if ad_db is None:
        return []
    return get_ad_spend_by_option(
        ad_db,
        date.fromisoformat(date_from),
        date.fromisoformat(date_to),
        option_id,
    )


@router.get("/gfa/status")
def get_gfa_status(db: Session = Depends(get_db)):
    """현재 DB에 적재된 GFA 광고비 현황 반환."""
    row = db.execute(
        text("""
            SELECT MIN(ad_date), MAX(ad_date), COUNT(*), COALESCE(SUM(ad_spend), 0)
            FROM ad_costs
            WHERE source = 'gfa:쇼핑'
        """)
    ).fetchone()
    if not row or row[0] is None:
        return {"has_data": False, "date_from": None, "date_to": None, "days": 0, "total_spend": 0}
    return {
        "has_data": True,
        "date_from": row[0],
        "date_to": row[1],
        "days": int(row[2]),
        "total_spend": int(row[3]),
    }


def _recalc_profit_background(date_from: date, date_to: date) -> None:
    """업로드된 날짜 범위의 이익을 백그라운드에서 재계산."""
    from app.database import SessionLocal
    from app.services.profit_calculator import calculate_daily_trend

    db = SessionLocal()
    try:
        calculate_daily_trend(db, None, None, date_from, date_to)
        log.info("GFA 업로드 후 이익 재계산 완료: %s ~ %s", date_from, date_to)
    except Exception as e:
        log.exception("GFA 업로드 후 이익 재계산 실패: %s", e)
    finally:
        db.close()


@router.post("/gfa/upload")
async def upload_gfa_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """GFA(디스플레이) 광고비 CSV 업로드 → ad_costs 저장 (source=gfa:쇼핑, 채널=NAVER).
    파일 형식: theohi11_광고비 보고서_YYYYMMDD_YYYYMMDD.csv
    필수 컬럼: 기간(YYYY.MM.DD.), 총비용(디스플레이 전체=ADVoost 쇼핑+동영상 조회 등).
    총비용 없으면 ADVoost 쇼핑으로 폴백.
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSV 파일만 업로드 가능합니다.")

    content = await file.read()
    text_content = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_content))

    naver_row = db.execute(
        text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")
    ).fetchone()
    if not naver_row:
        raise HTTPException(status_code=500, detail="NAVER 채널이 DB에 없습니다.")
    naver_id = naver_row[0]

    inserted = 0
    skipped = 0
    records: list[dict] = []

    for row in reader:
        date_str = row.get("기간", "").strip().rstrip(".")
        if not date_str:
            skipped += 1
            continue
        try:
            ad_date = date.fromisoformat(date_str.replace(".", "-"))
        except ValueError:
            skipped += 1
            continue

        # 총비용 = 디스플레이 전체(ADVoost 쇼핑 + 동영상 조회 등). 동영상 광고비 누락 방지로 총비용 우선.
        spend_str = (row.get("총비용") or row.get("ADVoost 쇼핑") or "0").strip().replace(",", "")
        try:
            spend = Decimal(spend_str)
        except Exception:
            skipped += 1
            continue

        if spend <= 0:
            skipped += 1
            continue

        records.append({"date": ad_date, "spend": spend})

    if not records:
        raise HTTPException(status_code=422, detail="저장할 데이터가 없습니다. 컬럼(기간, ADVoost 쇼핑)을 확인하세요.")

    for rec in records:
        db.execute(
            text("""
                DELETE FROM ad_costs
                WHERE channel_id = :cid AND source = 'gfa:쇼핑' AND ad_date = :ad_date
            """),
            {"cid": naver_id, "ad_date": rec["date"].isoformat()},
        )
        db.execute(
            text("""
                INSERT INTO ad_costs (channel_id, product_id, ad_date, ad_spend, ad_revenue, source, created_at)
                VALUES (:cid, NULL, :ad_date, :spend, NULL, 'gfa:쇼핑', datetime('now'))
            """),
            {"cid": naver_id, "ad_date": rec["date"].isoformat(), "spend": str(rec["spend"])},
        )
        inserted += 1

    db.commit()

    total_spend = sum(r["spend"] for r in records)
    date_from = min(r["date"] for r in records)
    date_to = max(r["date"] for r in records)

    # 업로드된 날짜 범위에 대해 이익 재계산 (백그라운드)
    background_tasks.add_task(_recalc_profit_background, date_from, date_to)

    return {
        "inserted": inserted,
        "skipped": skipped,
        "total_spend": int(total_spend),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "dates": [r["date"].isoformat() for r in records],
        "recalculation_triggered": True,
    }


@router.post("/naver-sa/sync")
def sync_naver_sa_ad_costs(
    date_from: str = Query(default=None, description="YYYY-MM-DD (기본: 어제)"),
    date_to: str = Query(default=None, description="YYYY-MM-DD (기본: 어제)"),
    db: Session = Depends(get_db),
):
    """네이버 검색광고(SA) 캠페인별 일별 광고비 + 구매 전환매출 → ad_costs 저장.
    source 형식: naver_sa:{키워드}(광고비) / naver_sa:conv(전환매출, ad_spend=0)
    """
    from app.services.naver_sa_ad_fetcher import fetch_campaign_daily_spend, fetch_daily_conversion_revenue

    yesterday = kst_today() - timedelta(days=1)
    d_from = date.fromisoformat(date_from) if date_from else yesterday
    d_to = date.fromisoformat(date_to) if date_to else yesterday

    naver_row = db.execute(
        text("SELECT id FROM channels WHERE code = 'NAVER' LIMIT 1")
    ).fetchone()
    if not naver_row:
        raise HTTPException(status_code=500, detail="NAVER 채널이 DB에 없습니다.")
    naver_id = naver_row[0]

    try:
        campaigns = fetch_campaign_daily_spend(d_from, d_to)
    except Exception as e:
        log.error("Naver SA API 오류: %s", e)
        raise HTTPException(status_code=502, detail=f"Naver SA API 오류: {e}")

    if not campaigns:
        return {"inserted": 0, "skipped": 0, "total_spend": 0, "dates": [], "message": "수집된 캠페인 없음"}

    # 날짜×source 별 집계
    agg: dict[tuple[str, str], Decimal] = {}
    for c in campaigns:
        kw = _extract_naver_sa_keyword(c["campaign_name"])
        source = f"naver_sa:{kw}"
        key = (c["date"], source)
        agg[key] = agg.get(key, Decimal("0")) + c["spend"]

    for (dt_str, source), spend in agg.items():
        _upsert_ad_cost(db, naver_id, date.fromisoformat(dt_str), spend, source)

    # 구매 전환매출(직접+간접) 일별 적재 — RoAS 산출용
    conv_revenue = 0
    try:
        conv_daily = fetch_daily_conversion_revenue(d_from, d_to)
        for dt_str, rev in conv_daily.items():
            _upsert_ad_revenue(db, naver_id, date.fromisoformat(dt_str), rev, NAVER_SA_CONV_SOURCE)
        conv_revenue = int(sum(conv_daily.values()))
    except Exception as e:
        log.warning("Naver SA 전환매출 적재 실패(광고비는 저장됨): %s", e)

    db.commit()

    dates = sorted({k[0] for k in agg})
    total = int(sum(agg.values()))
    log.info("Naver SA 광고비 %d건 + 전환매출 %d원 적재 (%s~%s) 총 %d원", len(agg), conv_revenue, d_from, d_to, total)
    return {
        "inserted": len(agg),
        "skipped": 0,
        "total_spend": total,
        "conv_revenue": conv_revenue,
        "dates": dates,
    }


@router.post("/meta/sync")
def sync_meta_ad_costs(
    date_from: str = Query(default=None, description="YYYY-MM-DD (기본: 어제)"),
    date_to: str = Query(default=None, description="YYYY-MM-DD (기본: 어제)"),
    db: Session = Depends(get_db),
):
    """Meta 캠페인별 일별 광고비 → ad_costs 저장.
    source 형식: meta:{키워드} 또는 meta:기타
    """
    from app.services.meta_ad_fetcher import fetch_campaign_daily_spend

    yesterday = kst_today() - timedelta(days=1)
    d_from = date.fromisoformat(date_from) if date_from else yesterday
    d_to = date.fromisoformat(date_to) if date_to else yesterday

    cafe24_row = db.execute(
        text("SELECT id FROM channels WHERE code = 'CAFE24' LIMIT 1")
    ).fetchone()
    if not cafe24_row:
        raise HTTPException(status_code=500, detail="CAFE24 채널이 DB에 없습니다.")
    cafe24_id = cafe24_row[0]

    try:
        campaigns = fetch_campaign_daily_spend(d_from, d_to)
    except Exception as e:
        log.error("Meta API 오류: %s", e)
        raise HTTPException(status_code=502, detail=f"Meta API 오류: {e}")

    if not campaigns:
        return {"inserted": 0, "skipped": 0, "total_spend": 0, "dates": [], "message": "수집된 캠페인 없음"}

    agg: dict[tuple[str, str], Decimal] = {}
    for c in campaigns:
        kw = _extract_meta_keyword(c["campaign_name"])
        source = f"meta:{kw}"
        key = (c["date"], source)
        agg[key] = agg.get(key, Decimal("0")) + c["spend"]

    for (dt_str, source), spend in agg.items():
        _upsert_ad_cost(db, cafe24_id, date.fromisoformat(dt_str), spend, source)

    db.commit()

    dates = sorted({k[0] for k in agg})
    total = int(sum(agg.values()))
    log.info("Meta 광고비 %d건 적재 (%s~%s) 총 %d원", len(agg), d_from, d_to, total)
    return {
        "inserted": len(agg),
        "skipped": 0,
        "total_spend": total,
        "dates": dates,
    }


# ── 쿠팡 광고비 XLSX 업로드 ──────────────────────────────────────────────────

_SELL_TYPE_TO_CHANNEL_SUFFIX = {
    "3P": "WING",
    "2P": "RG",
    "Retail": "ROCKET",
}


def _build_vendor_channel_map(db: Session) -> dict[tuple[str, str], int]:
    """(vendor_id, channel_suffix) → channel_id 조회 테이블 생성."""
    mapping: dict[tuple[str, str], int] = {}
    for suffix in ("WING", "RG"):
        for num in ("1", "2"):
            code = f"COUPANG_{suffix}{num}"
            vid = os.getenv(f"COUPANG_{suffix}{num}_VENDOR_ID", "").strip()
            if not vid:
                continue
            row = db.execute(text("SELECT id FROM channels WHERE code = :c LIMIT 1"), {"c": code}).fetchone()
            if row:
                mapping[(vid, suffix)] = row[0]
    rocket_row = db.execute(text("SELECT id FROM channels WHERE code = 'COUPANG_ROCKET' LIMIT 1")).fetchone()
    if rocket_row:
        mapping[("*", "ROCKET")] = rocket_row[0]
    return mapping


@router.get("/coupang/status")
def get_coupang_ad_status(db: Session = Depends(get_db)):
    """현재 DB에 적재된 쿠팡 광고비 현황 반환."""
    rows = db.execute(text("""
        SELECT ac.source, c.name, MIN(ac.ad_date), MAX(ac.ad_date), COUNT(*), COALESCE(SUM(ac.ad_spend), 0)
        FROM ad_costs ac
        JOIN channels c ON ac.channel_id = c.id
        WHERE ac.source LIKE 'coupang_%'
        GROUP BY ac.source, c.name
        ORDER BY ac.source
    """)).fetchall()
    if not rows:
        return {"has_data": False, "items": []}
    return {
        "has_data": True,
        "items": [
            {"source": r[0], "channel_name": r[1], "date_from": r[2], "date_to": r[3], "days": r[4], "total_spend": int(r[5])}
            for r in rows
        ],
    }


def _detect_xlsx_format(headers: list) -> dict:
    """헤더 행으로 쿠팡 광고 XLSX 포맷을 감지해 컬럼 인덱스 반환.

    지원 포맷:
      - pa_daily_keyword : 광고비=P(15), 노출수=N(13), 클릭수=O(14)
      - pa_daily_adGroup : 광고비=L(11), 노출수/클릭수는 헤더 탐색
    """
    def _find(keyword: str) -> int:
        for i, h in enumerate(headers):
            if h and keyword in str(h):
                return i
        return -1

    spend_idx = _find("광고비")
    if spend_idx == -1:
        spend_idx = 11  # fallback

    return {
        "spend":   spend_idx,
        "impr":    _find("노출수"),
        "clicks":  _find("클릭수"),
        "orders":  _find("총 주문수(1일)") if _find("총 주문수(1일)") != -1 else _find("전환수"),
        "qty":     _find("총 판매수량(1일)") if _find("총 판매수량(1일)") != -1 else _find("판매수량"),
        "rev":     _find("총 전환매출액(1일)") if _find("총 전환매출액(1일)") != -1 else _find("전환매출액"),
        # 옵션ID 컬럼(keyword 포맷에만 존재 — adGroup 포맷은 -1) → 트랙 D-9 3자 조인 광고축
        "ad_opt":   _find("광고집행 옵션"),       # 비용·노출·클릭 귀속 옵션ID
        "conv_opt": _find("전환매출발생 옵션"),    # 매출·주문 귀속 옵션ID
    }


@router.post("/coupang/upload")
async def upload_coupang_ad_xlsx(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """쿠팡 광고 XLSX 업로드 → ad_costs + coupang_ad_report 동시 저장.
    파일명 형식: {vendor_id}_pa_daily_*.xlsx
    C열(판매방식): Retail=로켓배송, 3P=윙, 2P=로켓그로스
    포맷(adGroup/keyword)은 헤더 행에서 자동 감지.
    """
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="xlsx 파일만 업로드 가능합니다.")

    m = re.match(r"(A\d+)_", file.filename)
    if not m:
        raise HTTPException(status_code=400, detail="파일명에서 vendor_id를 찾을 수 없습니다. 형식: {vendor_id}_pa_daily_...")
    vendor_id = m.group(1)

    vendor_channel_map = _build_vendor_channel_map(db)

    try:
        import openpyxl
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl 패키지가 설치되지 않았습니다.")

    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"XLSX 파일을 열 수 없습니다: {e}")

    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    col = _detect_xlsx_format(headers)
    log.info("쿠팡 XLSX 포맷 감지: spend=%s impr=%s clicks=%s orders=%s qty=%s rev=%s",
             col["spend"], col["impr"], col["clicks"], col["orders"], col["qty"], col["rev"])

    # ad_costs 집계: (date, channel_id, source) → spend
    agg: dict[tuple[date, int, str], Decimal] = {}
    # coupang_ad_report 집계: (date, sell_type) → metrics
    report_agg: dict[tuple[date, str], dict] = {}
    # coupang_ad_option_daily 집계: (date, vendor_id, sell_type, ad_opt, conv_opt) → metrics (트랙 D-9)
    opt_agg: dict[tuple, dict] = {}
    has_opt = col["ad_opt"] != -1 and col["conv_opt"] != -1
    skipped = 0

    def _cell_int(r: tuple, idx: int) -> int:
        try:
            return int(r[idx] or 0) if idx != -1 and idx < len(r) else 0
        except (TypeError, ValueError):
            return 0

    def _cell_dec(r: tuple, idx: int) -> Decimal:
        try:
            return Decimal(str(r[idx] or 0)) if idx != -1 and idx < len(r) else Decimal("0")
        except Exception:
            return Decimal("0")

    def _norm_opt(v):
        """옵션ID 정규화: 94277472815.0 → '94277472815', 빈값('-'/None)은 None."""
        if v is None:
            return None
        s = str(v).strip()
        if s in ("", "-"):
            return None
        try:
            return str(int(float(s)))
        except (TypeError, ValueError):
            return s

    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or len(row) <= col["spend"]:
            skipped += 1
            continue

        date_raw = row[0]
        sell_type = str(row[2] or "").strip() if row[2] else None
        spend_raw = row[col["spend"]]

        if not date_raw or not sell_type:
            skipped += 1
            continue

        try:
            date_str = str(int(date_raw))
            ad_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except Exception:
            skipped += 1
            continue

        spend = Decimal(str(spend_raw or 0))

        suffix = _SELL_TYPE_TO_CHANNEL_SUFFIX.get(sell_type)
        if suffix is None:
            skipped += 1
            continue

        # ── ad_costs 집계 (spend > 0 인 행만) ──
        if spend > 0:
            if suffix == "ROCKET":
                channel_id = vendor_channel_map.get(("*", "ROCKET"))
            else:
                channel_id = vendor_channel_map.get((vendor_id, suffix))

            if channel_id is not None:
                source = f"coupang_{suffix.lower()}"
                key = (ad_date, channel_id, source)
                agg[key] = agg.get(key, Decimal("0")) + spend

        # ── coupang_ad_report 집계 (노출 지표가 있는 포맷만) ──
        if col["impr"] != -1:
            rkey = (ad_date, sell_type)
            if rkey not in report_agg:
                report_agg[rkey] = {"impr": 0, "clicks": 0, "spend": Decimal("0"),
                                     "orders": 0, "qty": 0, "rev": Decimal("0")}
            g = report_agg[rkey]
            g["impr"]   += _cell_int(row, col["impr"])
            g["clicks"] += _cell_int(row, col["clicks"])
            g["spend"]  += spend
            g["orders"] += _cell_int(row, col["orders"])
            g["qty"]    += _cell_int(row, col["qty"])
            g["rev"]    += _cell_dec(row, col["rev"])

        # ── coupang_ad_option_daily 집계 (옵션ID 컬럼이 있는 keyword 포맷만, 트랙 D-9) ──
        if has_opt:
            ad_opt = _norm_opt(row[col["ad_opt"]] if col["ad_opt"] < len(row) else None)
            conv_opt = _norm_opt(row[col["conv_opt"]] if col["conv_opt"] < len(row) else None)
            if ad_opt or conv_opt:
                # 한쪽만 있으면 동일 옵션으로 폴백(귀속축 결측 방지). 보통 둘은 같음.
                ad_opt = ad_opt or conv_opt
                conv_opt = conv_opt or ad_opt
                okey = (ad_date, vendor_id, sell_type, ad_opt, conv_opt)
                if okey not in opt_agg:
                    opt_agg[okey] = {"impr": 0, "clicks": 0, "spend": Decimal("0"),
                                     "orders": 0, "qty": 0, "rev": Decimal("0")}
                o = opt_agg[okey]
                o["impr"]   += _cell_int(row, col["impr"])
                o["clicks"] += _cell_int(row, col["clicks"])
                o["spend"]  += spend
                o["orders"] += _cell_int(row, col["orders"])
                o["qty"]    += _cell_int(row, col["qty"])
                o["rev"]    += _cell_dec(row, col["rev"])

    if not agg and not report_agg:
        raise HTTPException(status_code=422, detail="저장할 광고 데이터가 없습니다. 파일 형식을 확인하세요.")

    # ad_costs 저장
    for (ad_date, channel_id, source), spend in agg.items():
        _upsert_ad_cost(db, channel_id, ad_date, spend, source)

    # coupang_ad_report 저장
    if report_agg:
        from app.models import CoupangAdReport
        for (ad_date, sell_type), g in report_agg.items():
            existing = db.query(CoupangAdReport).filter(
                CoupangAdReport.report_date == ad_date,
                CoupangAdReport.sell_type == sell_type,
                CoupangAdReport.vendor_id == vendor_id,
            ).first()
            if existing:
                existing.impressions = g["impr"]
                existing.clicks = g["clicks"]
                existing.ad_spend = g["spend"]
                existing.orders = g["orders"]
                existing.sales_qty = g["qty"]
                existing.conversion_revenue = g["rev"]
            else:
                db.add(CoupangAdReport(
                    report_date=ad_date, sell_type=sell_type, vendor_id=vendor_id,
                    impressions=g["impr"], clicks=g["clicks"], ad_spend=g["spend"],
                    orders=g["orders"], sales_qty=g["qty"], conversion_revenue=g["rev"],
                ))

    # coupang_ad_option_daily 저장 (옵션 그레인 — 트랙 D-9 3자 조인 광고축)
    # delete-then-insert: 이 업로드가 커버하는 (vendor_id, 등장 날짜) 범위의 기존 옵션 행을
    # 먼저 제거 후 전량 삽입. 14일 전환 윈도우로 conv_option_id가 나중에 바뀌어 5-튜플 키가
    # 달라질 때 옛 행이 stale로 남아 이중집계되는 것을 차단(codex P2). 같은 파일 재업로드 시엔
    # 같은 날짜를 지우고 동일 재삽입하므로 멱등성 유지.
    if opt_agg:
        from app.models import CoupangAdOptionDaily
        opt_dates = {od for (od, _vid, _st, _ad, _cv) in opt_agg}
        db.query(CoupangAdOptionDaily).filter(
            CoupangAdOptionDaily.vendor_id == vendor_id,
            CoupangAdOptionDaily.report_date.in_(opt_dates),
        ).delete(synchronize_session=False)
        for (od, vid, st, ad_opt, conv_opt), o in opt_agg.items():
            db.add(CoupangAdOptionDaily(
                report_date=od, vendor_id=vid, sell_type=st,
                ad_option_id=ad_opt, conv_option_id=conv_opt,
                impressions=o["impr"], clicks=o["clicks"], ad_spend=o["spend"],
                orders=o["orders"], sales_qty=o["qty"], conversion_revenue=o["rev"],
            ))

    db.commit()

    date_from = min((k[0] for k in agg), default=min(k[0] for k in report_agg)) if agg else min(k[0] for k in report_agg)
    date_to   = max((k[0] for k in agg), default=max(k[0] for k in report_agg)) if agg else max(k[0] for k in report_agg)
    dates = sorted({k[0].isoformat() for k in agg})
    total = int(sum(agg.values()))
    channel_summary: dict[str, int] = {}
    for (_, _, source), spend in agg.items():
        channel_summary[source] = channel_summary.get(source, 0) + int(spend)

    if agg:
        background_tasks.add_task(_recalc_profit_background, date_from, date_to)
    log.info("쿠팡 광고 적재 %s (%s~%s) 광고비=%d원 리포트=%d건 옵션=%d건",
             vendor_id, date_from, date_to, total, len(report_agg), len(opt_agg))
    return {
        "vendor_id": vendor_id,
        "inserted": len(agg),
        "skipped": skipped,
        "total_spend": total,
        "report_rows": len(report_agg),
        "option_rows": len(opt_agg),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "dates": dates,
        "channel_summary": channel_summary,
        "recalculation_triggered": True,
    }
