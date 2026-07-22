# visibility.py — 가시성 우선 판단 순수 SA (스프린트 VF, D-NAO-83 "노출이 기본")
# 역할(SA·순수함수·DB 읽기만): "보이는 자리를 사거나, 아예 안 사거나"를 판정하는 세 조각 —
#   ① classify_visibility: 순위→가시성 등급(밴드/가시/유령/미상).
#   ② evidence_window: 고수요·표본미달 그룹에 한해 밴드 진입까지 허용하는 "증거 구매 창"의
#      활성 여부를 naver_ad_daily 7일 집계로 무테이블 파생(예산 5만·클릭 15 상한).
#   ③ evidence_ceiling: 창 활성 그룹의 콜드 상한을 캠페인 90일 실측 RPC 기반 대체 산식으로
#      해방(순환논리 해소 — ref38 §2 "전환단가 순위 무관 평평" 근거).
#   ★import 정책: models + bid_simulator(최말단 순수 SA·models만 의존)만 import한다.
#     exploration·auto_operator는 절대 import하지 않는다 — 둘 다 이 모듈을 import하므로
#     역방향 import는 순환을 만든다(exploration→visibility, auto_operator→visibility). 상수는
#     visibility가 소유하고 exploration/auto_operator가 참조한다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily
from app.services.naver_ad import bid_simulator
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# ── 파라미터(PLAN §2 — 전부 ref38 실측 앵커) ──────────────────────────────────
# 유령 지면 경계(스텝 금지선): 5위 밖 CTR이 밴드의 1/3~1/8로 붕괴(ref38 §1) = 노출 집계는
# 되지만 사람 눈에 사실상 안 보임. 순위>이 값 ∧ 창 비활성 → 래더 스텝 금지(ghost_hold).
_GHOST_RANK = Decimal("5.0")  # D-NAO-83: 유령 임계(ref38 §1 CTR 붕괴점)
# 가시 임계 = 기존 이익 스팟 밴드 상단 재사용(순위≤이 값 = 클릭이 관측되는 밴드 안).
_VISIBILITY_BAND_TOP = Decimal("4.0")  # D-NAO-83: 가시 임계(exploration._EXPLORATION_BAND_HIGH와 동일 값)
# 증거 구매 창 예산 상한(원/그룹, 7일): 15프로 실측 시세 주 3.4만 + 여유(ref38 §3, 3~5만 상단).
_EVIDENCE_BUDGET_7D = 50_000  # D-NAO-83: 창당 7일 지출 상한(넘으면 창 종료)
# 증거 구매 창 클릭 목표: 클릭 10~15 확보 시 종료 → 통상 ROAS 판정 복귀(ref38 §5-2, 상단).
_EVIDENCE_CLICK_TARGET = 15  # D-NAO-83: 7일 클릭이 이 값 이상이면 증거 확보 = 창 종료
# 고수요 판별(7일 노출 하한): 유령 순위에서도 노출은 존재(17프로 7일 217 통과) — 무수요 그룹
# (노출조차 없어 증거 구매가 무의미한 그룹)을 창에서 배제(ref38 §2·§실측 4).
_EVIDENCE_MIN_DEMAND_IMP_7D = 100  # D-NAO-83: 고수요 판별 7일 노출 하한
# 콜드 상한 대체 산식의 캠페인 RPC 창(일): ref38 §1·2 분석 창과 동일(90일 실측 전환단가 평평).
_CAMPAIGN_RPC_WINDOW_D = 90  # D-NAO-83: 캠페인 실측 RPC 관측 창(일)

# ── 파생 상수 ──────────────────────────────────────────────────────────────
# 7일 수요/지출/클릭 관측 창 = [today-7, today-1](완결일만 — 오늘 당일은 미완결이라 제외).
_DEMAND_WINDOW_DAYS = 7  # D-NAO-83: 수요·지출·클릭 관측 창(일)
# 정착창 [today-8, today-2] 표본미달 게이트 하한 — auto_operator._settlement_window(D-8~D-2)·
# exploration._MIN_CLICK_FOR_EXPLORATION(10) 출처를 로컬 복제(visibility는 auto_operator를
# import 못 함 = 순환). 폴백 경로(settlement_clk 미주입)에서만 자체 쿼리로 쓴다 — 통상 경로는
# 호출측(레인)이 exploration._settlement_clk 값을 settlement_clk로 주입한다(원칙18-8·재계산 회피).
_SETTLEMENT_WINDOW_START_DAYS = 8  # 출처: auto_operator._SETTLEMENT_WINDOW_START_DAYS
_SETTLEMENT_WINDOW_END_DAYS = 2    # 출처: auto_operator._SETTLEMENT_WINDOW_END_DAYS
_SAMPLE_DEFICIENT_CLK = 10         # 출처: exploration._MIN_CLICK_FOR_EXPLORATION(표본미달 게이트)
# evidence_ceiling 최소 표본: 캠페인 90일 클릭이 이 값 미만이면 RPC 신뢰 불가 → None(레거시 폴백).
_MIN_CAMPAIGN_CLK_FOR_RPC = 30     # D-NAO-83: 캠페인 90일 RPC 최소 표본(clk)
_BID_INCREMENT = 10                # 유효 입찰가 10원 단위(bid_simulator 규격)


def classify_visibility(rank: Decimal | float | None) -> str:
    """순위 → 가시성 등급(순수, PLAN §2). 순위 숫자가 작을수록 상단(더 좋은 위치).

    반환:
      "unknown"  = rank None(imp=0 = 순위 관측 불가).
      "in_band"  = rank ≤ 4.0(이익 스팟 밴드 안 — 클릭 관측대).
      "visible"  = 4.0 < rank ≤ 5.0(밴드 밖이지만 아직 가시권).
      "ghost"    = rank > 5.0(유령 지면 — CTR 붕괴, 사람 눈에 사실상 안 보임).
    """
    if rank is None:
        return "unknown"
    r = Decimal(str(rank))
    if r <= _VISIBILITY_BAND_TOP:
        return "in_band"
    if r <= _GHOST_RANK:
        return "visible"
    return "ghost"


def _settlement_clk(db: Session, adgroup_id: str, date_from: date, date_to: date) -> int:
    """정착창 [date_from, date_to] 광고그룹 clk 합(폴백 전용 — 통상은 호출측이 값 주입).
    exploration._settlement_clk의 adgroup grain 집계를 복제한다(sentinel 행 제외)."""
    (clk,) = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0))
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    return int(clk)


def evidence_window(
    db: Session, adgroup_id: str, campaign_id: str, today: date,
    *, settlement_clk: int | None = None,
) -> dict:
    """증거 구매 창 활성 판정(순수 파생 — 무테이블, PLAN §1·§2). naver_ad_daily 7일 집계만으로
    "고수요·표본미달 그룹을 밴드 진입까지 허용하는 창"이 지금 열려 있는지 판정한다.

    활성 조건(전부 충족):
      ① 고수요:   7일 노출 ≥ _EVIDENCE_MIN_DEMAND_IMP_7D(100) — 유령 순위에서도 노출 존재.
      ② 표본미달: 정착창(D-8~D-2) clk < _SAMPLE_DEFICIENT_CLK(10) — ROAS 판정 불가 = 증거 필요.
      ③ 예산 여유: 7일 지출 < _EVIDENCE_BUDGET_7D(50,000원) — 창 예산 소진 전.
      ④ 클릭 미달: 7일 클릭 < _EVIDENCE_CLICK_TARGET(15) — 증거(클릭) 아직 미확보.

    settlement_clk: 정착창 clk를 호출측(레인)이 exploration._settlement_clk로 이미 산출했으면
      주입(원칙18-8·재계산 회피). None이면 자체 폴백 쿼리(_settlement_clk).

    반환 dict: {active, demand_imp_7d, spend_7d, clicks_7d, settlement_clk, sample_deficient,
      reason} — reason은 브리핑/로그용 한국어 사유(활성/비활성 근거)."""
    win_from = today - timedelta(days=_DEMAND_WINDOW_DAYS)
    win_to = today - timedelta(days=1)
    imp_sum, cost_sum, clk_sum = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= win_from,
            NaverAdDaily.ad_date <= win_to,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    demand_imp_7d, spend_7d, clicks_7d = int(imp_sum), int(cost_sum), int(clk_sum)

    if settlement_clk is None:
        s_from = today - timedelta(days=_SETTLEMENT_WINDOW_START_DAYS)
        s_to = today - timedelta(days=_SETTLEMENT_WINDOW_END_DAYS)
        settle_clk = _settlement_clk(db, adgroup_id, s_from, s_to)
    else:
        settle_clk = int(settlement_clk)

    high_demand = demand_imp_7d >= _EVIDENCE_MIN_DEMAND_IMP_7D
    sample_deficient = settle_clk < _SAMPLE_DEFICIENT_CLK
    budget_available = spend_7d < _EVIDENCE_BUDGET_7D
    clicks_below = clicks_7d < _EVIDENCE_CLICK_TARGET
    active = high_demand and sample_deficient and budget_available and clicks_below

    if active:
        reason = (
            f"증거창 활성 — 고수요(7일노출 {demand_imp_7d}≥{_EVIDENCE_MIN_DEMAND_IMP_7D})·"
            f"표본미달(정착clk {settle_clk}<{_SAMPLE_DEFICIENT_CLK})·예산여유(7일지출 {spend_7d}<{_EVIDENCE_BUDGET_7D})·"
            f"클릭미달(7일클릭 {clicks_7d}<{_EVIDENCE_CLICK_TARGET})"
        )
    else:
        causes = []
        if not high_demand:
            causes.append(f"저수요(7일노출 {demand_imp_7d}<{_EVIDENCE_MIN_DEMAND_IMP_7D})")
        if not sample_deficient:
            causes.append(f"표본충분(정착clk {settle_clk}≥{_SAMPLE_DEFICIENT_CLK} — 통상 ROAS 판정)")
        if not budget_available:
            causes.append(f"예산소진(7일지출 {spend_7d}≥{_EVIDENCE_BUDGET_7D})")
        if not clicks_below:
            causes.append(f"클릭확보(7일클릭 {clicks_7d}≥{_EVIDENCE_CLICK_TARGET} — 증거 달성)")
        reason = "증거창 비활성 — " + "·".join(causes)

    return {
        "active": active,
        "demand_imp_7d": demand_imp_7d,
        "spend_7d": spend_7d,
        "clicks_7d": clicks_7d,
        "settlement_clk": settle_clk,
        "sample_deficient": sample_deficient,
        "reason": reason,
    }


def _heuristic_ceiling(current_bid: int) -> int:
    """휴리스틱 병행 캡 = 현재입찰 × 2, 10원 단위 내림(exploration 병행 캡과 동형 값·2배)."""
    return int((Decimal(current_bid) * Decimal(2) // _BID_INCREMENT) * _BID_INCREMENT)


def evidence_ceiling(
    db: Session, campaign_id: str, bep_roas: Decimal | None, current_bid: int, today: date,
) -> int | None:
    """증거창 활성 그룹의 콜드 상한 대체 산식(순수, PLAN §1·§2 — 순환논리 해소).

    자기 RPC 없는 콜드 그룹의 경제성 상한을 "증거없음→상한낮음→증거못삼" 순환에서 해방한다:
    캠페인 90일 실측 RPC(전환단가 순위 무관 평평, ref38 §2)를 분자로 써서 밴드 진입에 필요한
    입찰까지 상한을 연다. 근거 = ref38 §2 전환단가 8.1~9.4천원 평평성(보이는 자리를 사도 전환
    경제성 안 나빠짐).

    산식:
      campaign_rpc_90d = Σconv_amt(90일) / Σclk(90일)  — clk ≥ _MIN_CAMPAIGN_CLK_FOR_RPC(30) 필요.
      economic  = affordable_ceiling(campaign_rpc_90d, bep_roas)  (bid_simulator 재사용).
      heuristic = current_bid × 2(10원 내림).
      반환 = min(economic, heuristic)  (current_bid>0) / economic  (current_bid≤0).

    None 반환(→ 호출측이 레거시 exploration_ceiling 폴백):
      - bep_roas None/≤0(경제 근거 없음).
      - 캠페인 90일 clk < 30(RPC 표본 부족 — 캠페인조차 콜드).
      - economic ≤ 0(affordable_ceiling 내부 70원 하한 미달 = 그 입찰로 이미 수익성 없음).
    """
    if bep_roas is None or bep_roas <= 0:
        return None  # 경제 근거 없음 — 레거시 폴백

    win_from = today - timedelta(days=_CAMPAIGN_RPC_WINDOW_D)
    win_to = today - timedelta(days=1)
    clk_sum, direct, indirect = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= win_from,
            NaverAdDaily.ad_date <= win_to,
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    clk_sum = int(clk_sum)
    conv_amt_sum = int(direct) + int(indirect)
    if clk_sum < _MIN_CAMPAIGN_CLK_FOR_RPC:
        return None  # 캠페인 90일 표본 부족 — 레거시 폴백

    rpc = Decimal(conv_amt_sum) / Decimal(clk_sum)
    economic = bid_simulator.affordable_ceiling(rpc, Decimal(str(bep_roas)))
    if economic <= 0:
        return None  # RPC/BEP로 유효 상한 못 세움(70원 하한 미달) — 레거시 폴백

    if current_bid <= 0:
        return economic  # 현재입찰 부재 — 경제 상한만
    return min(economic, _heuristic_ceiling(current_bid))
