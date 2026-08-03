# launch_rank_floor.py — 출시창 순위 하한 SA (D-NAO-121, 단일 책임: "이 소재를 목표 순위에
#   묶어두려면 최소 얼마여야 하는가"를 근거 라벨과 함께 낸다). 광고 API 호출 없음, DB 읽기만.
#
# ★왜 필요한가(2026-07-29 실사고): 갤럭시Z 신제품 3종을 인수한 다음 날, 머리 키워드 실측
#   순위가 1.7~2.2위에서 **7위로 추락**했다. 원인은 입찰 하락(CPC 1,541→902)인데, 우리
#   파이프라인엔 **상한(BEP ceiling)만 있고 하한이 없었다** — 상한이 낮게 잡히면 순위가
#   어디까지 밀려나든 막을 것이 없다. 출시 초기에는 그 자리가 매출 자체를 결정한다.
#
# ★설계의 핵심: 기준은 **순위**이고 입찰가는 종속 변수다(Jino 2026-07-29 11:02:
#   "1600원이 기준이 아니고 4위 순위 안에 들어오는게 기준인거잖아").
#   경쟁 상황이 바뀌면 같은 순위를 지키는 데 필요한 금액이 매일 달라지므로, 고정 금액을
#   저장하지 않고 **매번 사다리(naver_bid_estimate_daily)에서 다시 계산한다.**
#
# ★상한과 충돌하면: 출시창(21일) 안에서는 **하한이 이긴다**(가시성 우선, 계산된 손해 감수).
#   창 밖이면 하한 자체가 사라지고 종전대로 상한이 지배한다. 이 우선순위가 이 모듈이
#   존재하는 이유 전부다 — 소비자(guardrail_gate·auto_operator)는 None이면 종전 동작.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily,
    NaverAdgroupProduct,
    NaverBidEstimateDaily,
    NaverLearningState,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

log = logging.getLogger(__name__)

# 출시창 — conversion_maturity.MATURITY_DAYS(21)와 같은 길이·같은 근거: 그 기간이 지나면
# 자기 전환 데이터가 판단할 만큼 쌓인다고 이미 정한 값이라 기준을 하나로 유지한다.
LAUNCH_WINDOW_DAYS = 21

# 목표 순위는 NaverLearningState에 얹는다(전용 테이블·마이그레이션 없이 즉시 운용 가능).
#   scope="entity" / scope_key=<ad_id> / metric=METRIC_TARGET_RANK / current_value=목표순위(1~N)
METRIC_TARGET_RANK = "launch_target_rank"

# 쇼핑 검색광고 트래픽 주력이 모바일이라 모바일 사다리를 기준으로 잡는다.
# (PC/MOBILE 추정가가 크게 다르고, PC 값은 표본이 얇아 50원 같은 바닥값이 섞여 나온다 —
#  2026-07-29 실측: 같은 소재 4위가 PC 990 / MOBILE 1,390.)
DEFAULT_DEVICE = "MOBILE"


def get_target_rank(db: Session, ad_id: str) -> int | None:
    """이 소재에 지정된 출시창 목표 순위. 미지정이면 None(= 하한 없음, 종전 동작)."""
    row = (
        db.query(NaverLearningState.current_value)
        .filter(
            NaverLearningState.scope == "entity",
            NaverLearningState.scope_key == ad_id,
            NaverLearningState.metric == METRIC_TARGET_RANK,
            NaverLearningState.current_value.isnot(None),
        )
        .first()
    )
    if row is None or row[0] is None:
        return None
    rank = int(Decimal(row[0]))
    return rank if rank >= 1 else None


def set_target_rank(db: Session, ad_id: str, rank: int, *, note: str = "") -> None:
    """목표 순위 지정/변경. 이익 서보가 ±1칸 옮길 때도 이 진입점을 쓴다."""
    row = (
        db.query(NaverLearningState)
        .filter(
            NaverLearningState.scope == "entity",
            NaverLearningState.scope_key == ad_id,
            NaverLearningState.metric == METRIC_TARGET_RANK,
        )
        .first()
    )
    if row is None:
        row = NaverLearningState(scope="entity", scope_key=ad_id, metric=METRIC_TARGET_RANK)
        db.add(row)
    row.current_value = Decimal(rank)
    row.sample_n = 0
    row.confidence = Decimal(1)
    if note:
        log.info("launch_rank_floor: %s 목표순위 %d위 (%s)", ad_id, rank, note)


def days_since_launch(db: Session, adgroup_id: str, today: date) -> int | None:
    """첫 노출일로부터 경과일수. 노출 이력이 아예 없으면 None(= 아직 안 뜬 소재).

    ★첫 노출을 기준으로 잡는 이유: 생성일은 우리 DB에 없고, "광고가 실제로 돌기 시작한 날"이
    출시창의 의미에 맞다. sentinel 행은 제외(2배 계상 함정).
    """
    first = (
        db.query(sqlfunc.min(NaverAdDaily.ad_date))
        .filter(
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.imp > 0,
        )
        .scalar()
    )
    if first is None:
        return None
    d = first.date() if isinstance(first, datetime) else first
    return (today - d).days


def _ladder_bid(db: Session, ad_id: str, rank: int, today: date, device: str) -> tuple[int | None, date | None]:
    """사다리에서 '목표 순위 유지에 필요한 입찰'. position은 **1-베이스**(1위=1)라 rank로 조회.

    ★2026-07-29 정정(codex 리뷰 P1): 종전 구현은 `position == rank - 1`이었고 docstring도
      "0-베이스"라고 적혀 있었으나 **규약이 정반대다** — NaverBidEstimateDaily는
      `position 1~4 = 순위별 필요 입찰가`, `position 0 = 최소노출입찰가`(같은 테이블에
      담기 위한 가상 position, 실제 API position 아님)라고 모델 docstring에 명시돼 있다.
      그래서 4위 목표가 3위 가격을 강제하고(초과 지불), 1위 목표는 최소노출입찰가를 읽어
      순위를 통째로 잃는 상태였다. 라이브 3개 상품(목표 4위)이 전부 이 값에 걸려 있었다.

    ★is_floor 행 제외: 시세가 무의미하다는 표식(50원 바닥·순위별 무차등)이라 그걸 "이 순위
      유지에 필요한 금액"이라고 부르면 거짓이다. bep_breakdown.py:91이 같은 이유로 이미
      제외하고 있고(그 주석 참조), 신제품 사다리가 바로 이 상태가 되기 쉬워 하한이 50원으로
      무너질 수 있었다(codex 리뷰 P1).

    당일 행이 없으면 가장 최근 행으로 폴백한다(사다리는 전날 23:50 수집이라 보통 당일 것이
    있지만, 수집 실패한 날 하한이 통째로 사라지면 그날 순위가 무너진다 — 마지막으로 알려진
    값을 쓰는 편이 안전하다. 대신 얼마나 오래된 값인지 호출부에 같이 돌려준다).
    """
    row = (
        db.query(NaverBidEstimateDaily.bid, NaverBidEstimateDaily.date)
        .filter(
            NaverBidEstimateDaily.ad_id == ad_id,
            NaverBidEstimateDaily.device == device,
            NaverBidEstimateDaily.position == rank,
            NaverBidEstimateDaily.is_floor.is_(False),
            NaverBidEstimateDaily.date <= today,
        )
        .order_by(NaverBidEstimateDaily.date.desc())
        .first()
    )
    if row is None or not row[0]:
        return None, None
    return int(row[0]), row[1]


def floor_for(
    db: Session, *, ad_id: str, adgroup_id: str, today: date, device: str = DEFAULT_DEVICE,
) -> dict:
    """이 소재의 출시창 순위 하한.

    반환: {"floor_bid": int|None, "target_rank": int|None, "days_since_launch": int|None,
           "ladder_date": date|None, "reason": str}
      floor_bid=None 이면 **하한 없음 = 종전 동작**(상한이 지배). 소비자는 None을 그대로 통과시킨다.
    """
    out: dict = {
        "floor_bid": None, "target_rank": None, "days_since_launch": None,
        "ladder_date": None, "reason": "",
    }
    rank = get_target_rank(db, ad_id)
    if rank is None:
        out["reason"] = "출시창 목표순위 미지정 — 하한 없음"
        return out
    out["target_rank"] = rank

    days = days_since_launch(db, adgroup_id, today)
    out["days_since_launch"] = days
    if days is None:
        out["reason"] = "노출 이력 없음 — 출시창 판정 불가, 하한 없음"
        return out
    if days > LAUNCH_WINDOW_DAYS:
        out["reason"] = f"출시창 종료(첫 노출 후 {days}일 > {LAUNCH_WINDOW_DAYS}일) — 하한 해제, 이익 기준 복귀"
        return out

    bid, ladder_date = _ladder_bid(db, ad_id, rank, today, device)
    if bid is None:
        out["reason"] = f"사다리에 {rank}위 추정가 없음({device}) — 하한 산출 불가"
        return out

    out["floor_bid"] = bid
    out["ladder_date"] = ladder_date
    stale = "" if ladder_date == today else f", 사다리 {ladder_date} 기준"
    out["reason"] = (
        f"출시창 순위 하한 — 목표 {rank}위 유지에 {bid}원 필요"
        f"(첫 노출 후 {days}/{LAUNCH_WINDOW_DAYS}일{stale})"
    )
    return out


def group_floor_for(
    db: Session, *, adgroup_id: str, today: date, device: str = DEFAULT_DEVICE,
) -> dict:
    """이 **그룹**의 입찰을 내릴 때 지켜야 할 하한 — 그룹 입찰을 쓰는 출시창 소재들의 최대 하한.

    ★왜 필요한가(codex 리뷰 P1, 2026-07-29): 소재가 `useGroupBidAmt=true`면 실효 레버는
      **그룹 입찰**이고 실행기도 adgroup 단위 bid_down을 낸다. 그런데 하한 계산이
      target_type='ad' 분기에만 있어 그 경로에서는 launch_floor_bid=None → 출시창 소재의
      실효 입찰이 하한 밑으로 내려갈 수 있었다. 소재 하한을 그룹으로 올려 집계한다.

    ★use_group_bid_amt가 NULL(미수집)인 소재는 **포함한다.** 오탐의 대가는 "그룹 입찰을
      한 번 못 내렸다"이고, 누락의 대가는 "출시 상품이 1페이지에서 떨어졌다"이다.
      출시창에서는 가시성이 우선이라는 D-NAO-120에 따라 안전한 쪽으로 판단한다.

    반환: {"floor_bid": int|None, "target_rank": int|None, "binding_ad_id": str|None,
           "reason": str}. floor_bid=None이면 하한 없음(종전 동작).
    """
    out: dict = {"floor_bid": None, "target_rank": None, "binding_ad_id": None, "reason": ""}
    rows = (
        db.query(NaverAdgroupProduct.ad_id, NaverAdgroupProduct.use_group_bid_amt)
        .filter(
            NaverAdgroupProduct.adgroup_id == adgroup_id,
            NaverAdgroupProduct.ad_id.isnot(None),
        )
        .all()
    )
    best: tuple[int, int, str] | None = None  # (floor_bid, target_rank, ad_id)
    for ad_id, use_group in rows:
        if use_group is False:  # 소재 개별 입찰이 실효 — 그룹 입찰은 이 소재를 안 움직인다
            continue
        f = floor_for(db, ad_id=ad_id, adgroup_id=adgroup_id, today=today, device=device)
        bid = f["floor_bid"]
        if bid is None:
            continue
        if best is None or bid > best[0]:
            best = (int(bid), int(f["target_rank"]), ad_id)
    if best is None:
        out["reason"] = "그룹 입찰을 쓰는 출시창 소재 없음 — 하한 없음"
        return out
    out["floor_bid"], out["target_rank"], out["binding_ad_id"] = best
    out["reason"] = (
        f"그룹 출시창 하한 — 소재 {best[2]}의 목표 {best[1]}위 유지에 {best[0]}원 필요"
    )
    return out


def window_expires_on(db: Session, adgroup_id: str, today: date) -> date | None:
    """출시창 만료일(첫 노출일 + 21일). 노출 이력 없으면 None."""
    days = days_since_launch(db, adgroup_id, today)
    if days is None:
        return None
    return today + timedelta(days=LAUNCH_WINDOW_DAYS - days)
