# routers/overview.py — 쿠팡 종합 조망(Command Center) API (트랙 P7, D-2/D-3)
# GET /api/overview/command-center?from&to → 3축(회계·광고·상품) 단일 응답.
# 결합 엔진은 services/coupang/intelligence.py. 이 라우터는 기간 파싱·직렬화만(Agent 계층).
# D-3: 사실/지표만 — 추천 없음. Decimal은 문자열로 직렬화(금액 정밀도 보존, settlements 패턴).
from __future__ import annotations

from app.utils.kst import kst_now, kst_today
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coupang.intelligence import compute_command_center
from app.services.coupang.revenue_canonical import compute_canonical_revenue
from app.services.coupang.revenue_reconcile import reconcile_revenue
from app.services.coupang.rocket_1p_funnel import compute_rocket_1p_funnel
from app.services.coupang.rocket_1p_revenue import compute_rocket_1p_revenue
from app.services.coupang.rocket_intelligence import compute_rocket_overview
from app.services.coupang.rocket_pipeline import (
    PRE_INVOICE_STAGES,
    compute_rocket_pipeline,
    compute_rocket_pipeline_rows,
    compute_rocket_ri_queue,
)
from app.services.coupang.rocket_promo_pnl import compute_promo_pnl_overview
from app.services.coupang.rocket_recon import compute_rocket_recon, compute_rocket_recon_sku

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/overview", tags=["overview"])



def _parse_date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"잘못된 날짜 형식: {s} (YYYY-MM-DD)")


def _jsonify(v):
    """Decimal → str(정밀도 보존), 중첩 dict/list 재귀. None/숫자/bool은 그대로."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonify(x) for x in v]
    return v


# S1(트랙 reconciliation D-4): 허용 계정 — 쿠팡 대시보드(계정별)와 1:1 비교용.
_VALID_ACCOUNTS = {"COUPANG_WING1", "COUPANG_WING2"}  # 오픽스 / 오하이테크


@router.get("/command-center")
def command_center(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    account: str | None = Query(
        None, description="계정 필터: COUPANG_WING1(오픽스)·COUPANG_WING2(오하이테크). 생략=전체 합산."
    ),
    db: Session = Depends(get_db),
):
    """옵션ID 결합 엔진으로 3축 조망 반환. 기본 기간=최근 7일(KST).

    회계: 옵션별 매출−반품차감−실측수수료−광고비−원가=순이익(원가 있으면).
    광고: 비용·노출·클릭·전환매출·ROAS·CTR (사실, D-3).
    상품: 주문수·반품률·재고·판매상태.
    S1: account 주면 계정별 분리 뷰. 생략 시 전체 합산(기존 동작 불변).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    if account is not None and account not in _VALID_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail=f"잘못된 account: {account} (허용: {', '.join(sorted(_VALID_ACCOUNTS))} 또는 생략)",
        )
    result = compute_command_center(db, dfrom, dto, account)
    # S2(트랙 revenue-wing-truth D-1/D-9 A안): 닫힌 과거일 정본 매출(Wing GMV) 오버레이.
    # 읽기전용 가산 블록 — net_profit·account.summary.revenue 등 기존 값 불변(회귀 0).
    result["revenue_canonical"] = compute_canonical_revenue(db, dfrom, dto, account)
    return _jsonify(result)


@router.get("/revenue-reconcile")
def revenue_reconcile(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    account: str | None = Query(
        None, description="계정 필터: COUPANG_WING1(오픽스)·COUPANG_WING2(오하이테크). 생략=전체 합산."
    ),
    db: Session = Depends(get_db),
):
    """우리 매출(revenue_3p/rg) vs 쿠팡 공식 GMV(vendor-summary) 닫힌일 드리프트% 대조.

    Wing 세션 자동화 트랙 S2. 읽기전용(net_profit 등 종합조망 값 불변). 닫힌 과거일만 비교(D-3).
    드리프트% = (우리−쿠팡)/쿠팡. 사실·지표만(D-2). 기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    if account is not None and account not in _VALID_ACCOUNTS:
        raise HTTPException(
            status_code=422,
            detail=f"잘못된 account: {account} (허용: {', '.join(sorted(_VALID_ACCOUNTS))} 또는 생략)",
        )
    result = reconcile_revenue(db, dfrom, dto, account)
    return _jsonify(result)


# 로켓배송(1P) 단일 계정 = 오하이테크(D-6). env override 가능, 미설정이면 None(전체 Retail/PO).
_ROCKET_VENDOR_ID = os.getenv("COUPANG_ROCKET_VENDOR_ID") or None


@router.get("/rocket-overview")
def rocket_overview(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 돈 축 종합조망 블록 — 매출(발주)·광고·순이익·발주↔정산 드리프트.

    트랙 rocket-1p S4(D-11/D-12). 1P는 PO그레인이라 옵션그레인 command-center와 별도 블록.
    읽기전용(3P/RG 종합조망 값 불변). 매출=Σ발주 gross(발주일 KST). net_profit=매출−광고로
    cost 미반영(has_cost=false, D-12: PO 61% multi-SKU 원가분해 불가, 발주상세 수집 후속). 기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    result = compute_rocket_overview(db, dfrom, dto, _ROCKET_VENDOR_ID)
    return _jsonify(result)


@router.get("/rocket-1p-revenue")
def rocket_1p_revenue(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500, description="옵션 표에 실을 최대 행 수(소비자 매출 내림차순)"),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 매출 두 축 대조 — **소비자 판매가(쿠팡가) ∥ 우리 매출(납품가)**.

    왜 있나(Jino 2026-08-06): 쿠팡 판매분석 화면엔 우리 매출이 없고 우리 종합조망엔 소비자
    판매가가 없다. 08-04를 두 화면에서 보면 6,536,000원과 3,885,820원이 나오는데 왜 다른지
    어디서도 확인할 수 없었다. 이 엔드포인트가 두 축을 나란히 놓는다.

    ★★조회 전용이다 — 여기 값은 net_profit·종합조망에 결합되지 않는다(D-CPP-2 불변).
      소비자 판매가는 **쿠팡의 매출**이지 우리 것이 아니다(1P는 쿠팡이 사입해 자기 가격으로 판다).
    기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    return _jsonify(compute_rocket_1p_revenue(db, dfrom, dto, _ROCKET_VENDOR_ID, limit))


@router.get("/rocket-1p-funnel")
def rocket_1p_funnel(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000, description="옵션 표 최대 행 수(조회수 내림차순)"),
    min_page_views: int = Query(
        30, ge=0, le=100000,
        description="위치 판정에 필요한 최소 조회수. 이하 옵션은 low_sample로 남긴다(응답에 임계 노출)",
    ),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 옵션별 유입·전환 퍼널 — 방문자 → 조회 → 주문 → 판매수량.

    "왜 안 팔리나"에 답하는 축이다(매출 화면 S2는 "얼마 벌었나"에 답한다).
    ★전환율은 **Σ주문 ÷ Σ조회** — 일별 비율의 평균이 아니다(작은 날이 큰 날과 같은 표를 갖는다).
    ★위치(position)는 기간 중앙값 대비 서술일 뿐 **권고가 아니다**. 쓰인 임계값은 전부 응답에 실린다.
    ★조회 전용 — net_profit·종합조망에 결합되지 않는다(D-CPP-2 불변). 기본 기간=최근 7일(KST).
    """
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=6))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    return _jsonify(
        compute_rocket_1p_funnel(db, dfrom, dto, _ROCKET_VENDOR_ID, limit, min_page_views)
    )


@router.get("/rocket-promo-pnl")
def rocket_promo_pnl(
    limit: int = Query(20, ge=1, le=100, description="최근 프로모션 N건"),
    request_id: str | None = Query(None, description="한 건만 보기(프로모션 Request ID)"),
    db: Session = Depends(get_db),
):
    """쿠팡 프로모션 손익 레이어 (트랙 coupang-promo-pnl Phase 2) — 프로모션별 진짜 손익·BEP ROAS.

    ★읽기 전용 신규 API다. 기존 net_profit·종합조망 회계는 **한 톨도 바뀌지 않는다**
      (1P 회계 매출은 여전히 발주 납품금액 축, D-CPP-2 / 분담금은 청구방식 미확정, D-CPP-4).

    기간 파라미터가 없는 이유: 창은 사용자가 고르는 게 아니라 **프로모션 행사기간이 정한다.**
      임의 기간을 받으면 프로모션 밖 판매가 손익에 섞인다.

    응답: promotions[](카드) · freshness(판매분석 결손·구독 체험 경고) · rg_coupons(나열).
    미상은 전부 null + 사유(blockers/unresolved_reasons)로 온다 — 0으로 접지 않는다(원칙22).
    """
    result = compute_promo_pnl_overview(
        db, _ROCKET_VENDOR_ID, limit=limit, request_id=(request_id or None)
    )
    return _jsonify(result)


def _recon_window(from_: str | None, to: str | None) -> tuple[date, date]:
    """대사 화면 기간 파싱 — 기본 최근 90일(발주 기준). 다른 조망(7일)보다 긴 이유:
    발주→입고→거래명세서→계산서 확정까지 수 주가 걸려, 7일 창은 대사 자체가 성립하지 않는다."""
    today = kst_today()
    dto = _parse_date(to, today)
    dfrom = _parse_date(from_, dto - timedelta(days=89))
    if dfrom > dto:
        raise HTTPException(status_code=422, detail="from이 to보다 늦습니다")
    return dfrom, dto


@router.get("/rocket-recon")
def rocket_recon(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    drift_only: bool = Query(False, description="발주≠입고인 상품만(귀속 가능분 기준)"),
    unconfirmed_only: bool = Query(False, description="계산서 미연결·미확정·전송 미표기 상품만"),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 통합 대사 — 발주·납품·거래명세서 단계·계산서를 상품(SKU) 한 표로. 조회 전용.

    ★읽기 전용 신규 API다. 기존 회계·종합조망·수집 경로는 한 톨도 바뀌지 않는다.

    응답: summary(PO 그레인 요약 타일 + 상태별 + 계산서 + 발주상세 커버리지) · skus[](상품 행).
      - 요약 타일은 윈도우 발주 **전체**(PO 그레인), 상품표는 **발주상세 수집분만**(SKU 그레인).
        둘의 합계가 다른 것은 정상이며 그 차이가 summary.detail_coverage다.
      - drift_po_count_settled_stage = **입고 완료 단계**(CI 거래명세서확인 · RI 거래명세서확인요청)
        인데 발주≠입고(진짜 신호). drift_po_count(전체)는 입고 전 단계(PA·RP)의 당연한 불일치를 포함한다.
      - SKU별 입고수량은 원천에 없어 **단일SKU PO에서만** 귀속하고 나머지는 미귀속으로 표기한다.
        미수집(납품가능수량 NULL·정산행 없음)은 0이 아니라 별도 카운트로 온다(원칙22).

    기본 기간 = 최근 90일(발주일 KST).
    """
    dfrom, dto = _recon_window(from_, to)
    result = compute_rocket_recon(
        db, dfrom, dto, _ROCKET_VENDOR_ID,
        drift_only=drift_only, unconfirmed_only=unconfirmed_only,
    )
    return _jsonify(result)


@router.get("/rocket-recon/sku/{product_number}")
def rocket_recon_sku(
    product_number: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """한 상품(SKU)이 속한 발주 목록 — 행 확장용. 조회 전용, 윈도우 계약은 /rocket-recon과 동일.

    각 행: PO번호·발주일·상태(거래명세서 단계 포함)·PO 발주/입고 수량·이 SKU 라인 수량·
      연결 계산서(번호·작성일·확정일·전송 표기·지급예정). 계산서 found=false = 번호는 있으나
      정산행 미수집(발행 안 됨이 아니다).
    """
    dfrom, dto = _recon_window(from_, to)
    result = compute_rocket_recon_sku(db, product_number, dfrom, dto, _ROCKET_VENDOR_ID)
    return _jsonify(result)


def _ship_window(from_: str | None, to: str | None) -> tuple[date | None, date | None]:
    """발송일 창 파싱 — **기본값이 없다(=창 없음, 열려 있는 것 전부)**.

    ★`_recon_window`(발주일 기본 90일)와 일부러 다르다. 파이프라인의 질문은 「지금 어느 칸에
      얼마가 걸려 있나」라서 기본이 «전부»여야 한다 — 기본 창을 두면 창 밖에 굳어 있는 돈이
      화면에서 사라지고, 그게 정확히 이 화면이 잡으려는 병이다.
    """
    dfrom = _parse_date(from_, None) if from_ else None
    dto = _parse_date(to, None) if to else None
    if dfrom and dto and dfrom > dto:
        raise HTTPException(status_code=422, detail="ship_from이 ship_to보다 늦습니다")
    return dfrom, dto


@router.get("/rocket-pipeline")
def rocket_pipeline(
    ship_from: str | None = Query(None, description="③입고대기 칸에만 적용되는 발송일 창 시작"),
    ship_to: str | None = Query(None, description="③입고대기 칸에만 적용되는 발송일 창 끝"),
    db: Session = Depends(get_db),
):
    """로켓배송(1P) 열린 파이프라인 — 발주가 돈이 되기까지 어느 칸에 얼마가 걸려 있나. 조회 전용.

    ★읽기 전용 신규 API다. 기존 회계·종합조망·수집 경로는 한 톨도 바뀌지 않는다.

    칸 넷(`stages`): ①await_confirm(발주 왔고 우리가 미확정, RP) ②await_ship(확정했고 미발송, PA)
      ③await_receive(보냈는데 쿠팡 미입고 = **계산서 미발행**, PA) ④await_payment(계산서 나감,
      지급일 미도래 — **계산서 그레인**).
      `pre_invoice_subtotal` = ①②③ 합. ④는 축이 달라(계산서 그레인) 소계에 안 들어간다.

    소계 밖 두 덩어리:
      - `closed_unshipped` 확정했는데 발송 없이 닫힘(영영 못 보내는 분)
      - `unexplained` 발송 신고 > 쿠팡 인정 입고. **확정 숫자가 아니다** — 덜 보냄·반송·진짜
        미수금이 구별 불가로 섞여 있다(`confirmed: false`). 소계 합산 금지.

    `clamp`(음수 절단 표면화) · `unpriced_shipped_qty`(단가 못 붙인 발송수량) ·
      `freshness`(수집 신선도)로 «이 숫자가 언제 것이고 무엇을 못 보는지»를 함께 낸다(원칙22).

    ship_from/ship_to는 **③에만** 적용된다(「8/20 이후 발송분 중 미발행」). 창을 줘도 금액은
      PO 전체 기준이고, 창 밖 발송이 섞인 PO 수를 `ship_window`가 함께 낸다.
    """
    dfrom, dto = _ship_window(ship_from, ship_to)
    return _jsonify(
        compute_rocket_pipeline(db, _ROCKET_VENDOR_ID, kst_today(), dfrom, dto)
    )


@router.get("/rocket-pipeline/stage/{stage}")
def rocket_pipeline_stage(
    stage: str,
    ship_from: str | None = Query(None),
    ship_to: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """한 칸에 걸린 PO 목록 — 요약 타일 확장용. 조회 전용.

    `stage` = await_confirm | await_ship | await_receive | closed_unshipped | unexplained.
      (await_payment은 계산서 그레인이라 PO 목록이 없다 — 400.)
    각 행의 `stage_amount`가 **그 칸에 계상된 금액**이다(PO 총액이 아니다). `is_stale`은
      마지막 수집일과 그 PO의 수집일이 다르다는 뜻 — 「지금 참인 상태」가 아닐 수 있다.
    """
    allowed = set(PRE_INVOICE_STAGES) | {"closed_unshipped", "unexplained"}
    if stage not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"알 수 없는 칸: {stage} (가능: {', '.join(sorted(allowed))})",
        )
    dfrom, dto = _ship_window(ship_from, ship_to)
    return _jsonify(
        compute_rocket_pipeline_rows(db, _ROCKET_VENDOR_ID, stage, dfrom, dto, limit)
    )


@router.get("/rocket-po-changes")
def rocket_po_changes(db: Session = Depends(get_db)):
    """★「이번 수집에서 달라진 것」 — 처음 본 발주 vs 상태가 바뀐 발주. 조회 전용.
    계약 `docs/contracts/CONTRACT_1p_po_status_history.md` (Jino 승인 2026-08-28 13:33).

    ★이 화면이 있는 이유(2026-08-28 실사고): 원장이 snapshot upsert라 «현재 단면»만 갖는 탓에
      「①확인 대기가 왜 줄고 ②발송 대기가 왜 늘었나」에 아무도 답하지 못했고, 그 자리에서
      **「Jino가 확정했기 때문」이라는 근거 없는 인과 주장**이 나왔다. 실측이 그걸 반증했다 —
      그날 발주 9건 중 8건이 12:34 수집에서 **처음 관측**됐다(10:14엔 없었다).

    ★★응답은 «우리가 본 것»만 말한다: 변화는 `observed_from ~ observed_to` **구간**에 귀속되고,
      `first_seen`은 전이가 아니라 **출현**이다(「PA로 처음 관측됨」 ≠ 「RP에서 PA로 바뀜을 봄」).
    """
    from app.services.coupang import rocket_po_changes as svc

    return _jsonify(svc.latest_round_changes(db, _ROCKET_VENDOR_ID))


@router.get("/rocket-po-changes/{purchase_order_seq}")
def rocket_po_history(purchase_order_seq: int, db: Session = Depends(get_db)):
    """발주 1건의 관측 이력 전체(시간순). 조회 전용.

    이력이 0건이면 `empty_reason`이 **왜 비었는지**를 말한다 — 소급이 원리적으로 불가하므로
    (배선일부터만 쌓인다) 빈 이력을 「변화 없음」으로 읽으면 안 된다(원칙22).
    """
    from app.services.coupang import rocket_po_changes as svc

    return _jsonify(svc.po_history(db, purchase_order_seq))


@router.get("/rocket-ri-queue")
def rocket_ri_queue(db: Session = Depends(get_db)):
    """거래명세서확인요청(RI) — 우리가 눌러야 할 일 목록. 조회 전용.

    ★파이프라인 칸이 **아니다**.

    ★★2026-09-06(Jino 지시): 라이브를 계산서 유무로 가른다. 「RI = 계산서가 이미 나갔다」는
      2026-08-27 하루의 우연이었고 라이브가 반증했다(12건 중 계산서 보유는 4건뿐).
      `live_no_invoice_*`가 「지금 확인이 필요한 건」의 건수·금액이고, `live_invoiced_*`는
      ④지급대기와 중복이라 그 금액에서 뺀다 — 다만 `rows`에는 남는다(누르는 일은 남아 있다).

    ★★`is_stale`이 이 응답의 핵심이다. 수집 창(발주일 기준)이 좁아 오래된 미종결 PO는 상태가
      마지막 수집일에 굳는다 — 굳은 행은 이미 닫혔을 수 있다(2026-08-27 실측: RI 12건 중 8건이
      2026-08-05에 굳었고 그 8건의 계산서는 지급일까지 지났다). 판정 근거(연결 계산서의
      confirmed/transmitted/payment_date + synced_date)를 행마다 실어 보낸다.

    ★2026-08-28(계약 CONTRACT_1p_invoice_confirm_write): 행마다 `confirm`을 실어 보낸다 —
      「지금 확인 버튼을 띄울 수 있는가(can_request)」와 **못 띄우면 그 이유**(진행 중 /
      결과 미상 잠금). 버튼만 조용히 사라지면 사람은 왜 못 누르는지 모른다.
      ★합성을 서비스가 아니라 여기서 하는 이유: `rocket_invoice_confirm`이
      `rocket_pipeline`을 import하므로, 파이프라인이 거꾸로 부르면 순환 import가 된다.
    """
    from app.services.coupang import rocket_invoice_confirm

    out = compute_rocket_ri_queue(db, _ROCKET_VENDOR_ID)
    seqs = [int(r["purchase_order_seq"]) for r in out.get("rows", [])]
    states = rocket_invoice_confirm.confirm_states(db, seqs, _ROCKET_VENDOR_ID)
    for r in out.get("rows", []):
        # ★굳음 판정은 `confirm_states`가 **직접** 한다(적대 리뷰 1R P2-1). 전엔 여기서
        #   응답을 덧칠했는데, 그러면 같은 규칙이 서비스·라우터 두 곳에 갈라져 살고
        #   **어느 쪽도 테스트가 없었다**(변이 D11이 살아남았다). 라우터는 이제 합성만 한다.
        r["confirm"] = states.get(int(r["purchase_order_seq"]), {})
    return _jsonify(out)
