# proposal_writer.py — proposal_writer SA (P2-S3 T3, D-3/D-S3-b)
# 역할(SA): 진단 보드 + bid_simulator 결과 → 제안(naver_proposals) 생성·저장. 읽기전용 제안만
#   (광고 API 쓰기 없음, D-3 관찰 모드). optimizer='ours' 캠페인만 대상(D-NAO-13).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 diagnosis·bid_sims를 넘긴다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import (
    campaign_target_resolver, effective_bid, forecast_source, growth_sweeper, naver_sa_writer,
)
# 라이브[P1] DOA 수정: 생성 단계 스텝 클램프의 스텝 상수는 실행 가드레일과 단일 진실 소스
# (하드코딩 0.15 중복 금지 — 드리프트 방지). guardrail_gate는 이 모듈을 import하지 않아 순환 없음.
from app.services.naver_ad.guardrail_gate import _MAX_CHANGE_PCT
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_CPC, PROPOSAL_TYPE_PACING
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

_NEGATIVE = "negative_keyword"
_GROWTH_BID_UP = "growth_bid_up"
_PAUSE = "pause"  # X1b T3, D-NAO-38 — 정지(target_lock=True)
_RESUME = "resume"  # X1b T3, D-NAO-38 — 재개(target_lock=False)
_BUDGET_UP = "budget_up"
_BUDGET_PRE_EXHAUSTION = "budget_pre_exhaustion"
_ANOMALY = "anomaly"
_ANOMALY_FRESHNESS = "anomaly_freshness"
_ACCOUNT_BRIEF = "account_brief"
_WISDOM_PROMOTED = "wisdom_promoted"  # D-NAO-54 P3 — 지혜 승격 보고(정보성, 실행 대상 아님)
# D-NAO-54 P4(소비층) — 지혜가 함의한 생성기 파라미터 변경 "제안". 정보성도 실행형도 아닌
# **결정 전용**: 실행 payload(target_bid 등)를 애초에 담지 않고, 승인해도 harness.execute를
# 부르지 않는다(적용은 Jino가 콘솔/설정에서 수동 — D-NAO-54 금지선 "지혜→실행 직접 쓰기 금지").
# 그래서 INFORMATIONAL_PROPOSAL_TYPES(no-op·자동만료 카드)에도, _ACTION_BY_PROPOSAL_TYPE/
# OPEN_ACTIONS/_WRITE_EXECUTORS(실행 매핑)에도 절대 등록하지 않는다 — 라우터의
# DECISION_ONLY_PROPOSAL_TYPES가 이 유형을 결정 전용으로 분기한다.
PARAM_CHANGE = "param_change"

# SS4(PLAN_naver-ad-searchterm-ss.md §3 SS4) — 검색어 승격 후보(정식 키워드 등록 제안). 정식
# 등록 쓰기 손 자체가 L3 스코프라 실행 매핑이 없다(wisdom_promoted와 동일 모양 — 보고·열람
# 전용, Jino가 콘솔 밖에서 수동 등록). 값의 단일 진실은 search_term_judge.SEARCH_TERM_
# PROMOTE_TYPE(리터럴 중복이나 SS3의 _SEARCH_TERM_EXCLUDE와 동일하게 import 결합 회피 —
# 드리프트는 test_all_proposal_types_constant_covers_every_emitted_type이 강제).
_SEARCH_TERM_PROMOTE = "search_term_promote"

# X1a T6(D-NAO-37): 정보성 제안 유형 5종 — 실행 대상 자체가 없는 제안(naver_execution_harness의
# _ACTION_BY_PROPOSAL_TYPE에 매핑이 없는 유형과 의미상 같지만, 그 매핑에는 budget_up처럼 아직
# OPEN_ACTIONS에 없을 뿐인 "실행형인데 미개방" 유형도 섞여 있어 "매핑에 없음"을 파생 조건으로
# 쓰면 안전하지 않다(향후 예산 개방 시점에 조용히 브리핑에서 빠져버릴 위험) — 반드시 이 명시
# 목록을 단일 진실로 유지한다. proposal_pipeline(차등 TTL)·expert_briefing_builder(브리핑
# 접기)가 이 상수를 공유 import한다(순환 import 없음 — 두 모듈 다 이 모듈을 참조할 뿐, 이
# 모듈은 어느 쪽도 import하지 않는다).
INFORMATIONAL_PROPOSAL_TYPES: frozenset[str] = frozenset({
    _ANOMALY, _ANOMALY_FRESHNESS, _ACCOUNT_BRIEF, PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC,
    _WISDOM_PROMOTED,  # D-NAO-54 P3 — 정보성(실행 개방 액션에 매핑 없음, Jino 콘솔 열람 전용)
    _SEARCH_TERM_PROMOTE,  # SS4 — 정보성(등록 실행 매핑 없음, L3 스코프. 정의는 위 참조)
})

# D-NAO-47: 제안 유형 14종 단일 진실 — 프론트 라벨 맵이 이걸 진실로 삼는다.
# ★배경: 프론트 PROPOSAL_TYPE_LABEL이 6종만 정의해 9종이 영문 원문으로 렌더됐고, 반대로
# 백엔드가 생성하지 않는 'budget'·'new_setup' 유령 라벨을 갖고 있었다(스펙 §1-3).
# 새 유형을 추가하면 여기에도 반드시 넣는다 — test_all_proposal_types_constant_covers_
# every_emitted_type이 강제한다.
# ⚠️계획서(2026-07-17-mop-command-center-backend.md T4) 표는 13종이라 적었으나 budget_down이
# 빠져 있었다(실측: naver_execution_harness._ACTION_BY_PROPOSAL_TYPE에 budget_up과 대칭으로
# 이미 존재 — D-NAO-42-f, 커밋 68d7ef5). 드리프트 가드는 계획서 표가 아니라 실제 코드 상태를
# 진실로 삼아야 하므로 여기서 14종으로 정정한다.
_BID_UP = "bid_up"
_BID_DOWN = "bid_down"
_BID_UP_SERVO = "bid_up_servo"  # IU-R R1, 쇼검 폐루프 순위 서보(auto_operator.run_hourly_lane inline 생성)
_BID_UP_RANK = "bid_up_rank"  # IU-R R2, 파워링크 estimate 직행(auto_operator.run_hourly_lane inline 생성)
_BID_UP_EXPLORE = "bid_up_explore"  # B-X BX2(D-NAO-70), 저볼륨 그룹 탐색 UP(exploration 레인 inline, BX3 배선)
_BID_UP_COLD = "bid_up_cold"  # CS, 콜드 소재 첫 입찰(cold_start_bid_lane inline — 시장가 직행)
_BUDGET_DOWN = "budget_down"  # D-NAO-42-f, budget_up과 동형(감액은 자유 — guardrail_gate 참조)
# SS3(검색어 ROAS 레이어) — 검색어 제외(Confirm 전용). 값의 단일 진실은
# search_term_judge.SEARCH_TERM_EXCLUDE_TYPE(리터럴 중복이나 import 결합 회피 — 드리프트는
# test_all_proposal_types_constant_covers_every_emitted_type이 강제).
_SEARCH_TERM_EXCLUDE = "search_term_exclude"

ALL_PROPOSAL_TYPES: frozenset[str] = frozenset({
    _BID_UP, _BID_DOWN, _BID_UP_SERVO, _BID_UP_RANK, _BID_UP_EXPLORE, _BID_UP_COLD, _GROWTH_BID_UP, _NEGATIVE, _PAUSE, _RESUME,
    _BUDGET_UP, _BUDGET_DOWN, _BUDGET_PRE_EXHAUSTION, _SEARCH_TERM_EXCLUDE,
    _ANOMALY, _ANOMALY_FRESHNESS, _ACCOUNT_BRIEF, PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC,
    _WISDOM_PROMOTED,  # D-NAO-54 P3(정보성) — INFORMATIONAL_PROPOSAL_TYPES <= ALL 불변 유지
    PARAM_CHANGE,  # D-NAO-54 P4(결정 전용) — 정보성도 실행형도 아님(라우터 DECISION_ONLY 분기)
    _SEARCH_TERM_PROMOTE,  # SS4(정보성) — INFORMATIONAL_PROPOSAL_TYPES <= ALL 불변 유지
})

# 보드 의미상 허용되는 방향(codex 지적, 라이브검증 후속): starving_winners(육성 의도, D-NAO-18)는
# bid_up만, bleeding_keywords/shopping_group_bep(손실 축소 의도)는 bid_down만 허용한다.
# rank estimate를 걸러도(proposal_pipeline._RANK_ESTIMATE_BOARDS) economic_ceiling 자체가
# 표본이 얇을 때 계층 수축으로 board 의도와 반대 방향을 낼 수 있어(라이브검증 실측) 여기서
# 한 번 더 막는다 — negative_keyword 격상(경제성 상한<=0)은 보드 무관하게 항상 허용.
# growth_sweeper(D-NAO-22-①, Phase2)도 같은 원칙 — 전수 스윕 후보라 성장 의도 반대(down/hold)는
# 만들지 않는다(_ALLOWED_DIRECTIONS에 신규 유형 등록 필수, 계획서 §6 가드레일).
_ALLOWED_DIRECTIONS = {
    "bleeding_keywords": {"down"},
    "starving_winners": {"up"},
    "shopping_group_bep": {"down"},
    "shopping_group_growth": {"up"},  # X1b-S S3(D-NAO-43 확장) — shopping_group_bep의 반대(성장)
    "growth_sweeper": {"up"},
}

# P3(D-NAO-42-f): 예산 사이징(PLAN §5-G)·라운드 봉투(PLAN §5-E) 상수.
_BUDGET_ROUND_INCREMENT = 100  # 100원 단위 반올림 — 네이버 침묵 반올림 방어(PLAN §5-B/G)
_ROUND_BUDGET_AUTO_CAP = 100_000  # 계획서 §2② — 회당 총 증가액(라운드 합계) 자율 한도


def _ours_campaign_ids(db: Session) -> set[str]:
    """optimizer='ours' 캠페인 ID 집합(D-NAO-13 — 제안 대상은 이 캠페인만)."""
    rows = db.query(NaverCampaignSettings.campaign_id).filter(
        NaverCampaignSettings.optimizer == "ours"
    ).all()
    return {r[0] for r in rows}


def _loss_policy_map(db: Session) -> dict[str, str]:
    """캠페인별 loss 대응 정책(D-NAO-65 UI1) — {campaign_id: 'stoploss_pause', ...} 매핑.

    NULL/'leash'는 기본값(고삐-일일리셋)이라 매핑에서 제외한다 — 소비부는 dict.get(cid)가
    None이면 leash로 해석하므로 stoploss_pause만 실을 필요가 있다(회귀 0: 기존 캠페인은
    전부 미등록 → None → 고삐 경로 불변). build()가 한 번에 로드해 `_stop_loss_proposal`에
    주입한다 — SA(순수 함수)는 DB를 직접 읽지 않는다(_ours_campaign_ids와 동일 관례)."""
    rows = db.query(
        NaverCampaignSettings.campaign_id, NaverCampaignSettings.loss_policy
    ).filter(NaverCampaignSettings.loss_policy == "stoploss_pause").all()
    return {cid: lp for cid, lp in rows}


class _TargetLabelCache:
    """캠페인당 target_roas 근거(override/account_default)를 회차 내 1회만 조회(N+1 방지)."""

    def __init__(self, db: Session):
        self._db = db
        self._cache: dict[str, dict] = {}

    def get(self, campaign_id: str) -> dict:
        if campaign_id not in self._cache:
            self._cache[campaign_id] = campaign_target_resolver.resolve_target_roas(self._db, campaign_id)
        return self._cache[campaign_id]


def _forecast_evidence_suffix(forecast: dict | None, grain: str) -> str:
    """예측치(F2b ⓐ, D-NAO-26)가 있으면 rationale에 병기할 문구 — 없으면 빈 문자열(정직 경계,
    fallback/미가동 타겟에 억지로 예측을 만들지 않음). 입찰 산식(D-NAO-19)에는 관여하지 않는다.

    ★conv_amt 기준 병기(라벨 정정, 2026-07-28): pred_conv_amt의 회계 의미가 grain마다 다르다 —
    campaign은 sentinel(/stats) 소스라 **구매+장바구니 합**이고(03 캠페인 07-27 +64% 과대),
    adgroup/keyword는 상세 행이라 구매만이다. 기준을 안 적으면 캠페인 제안 근거에서 예상매출이
    부풀어 보인다. 라벨의 단일 진실 소스는 forecast_source.CONV_AMT_BASIS_LABEL(문구를 여기서
    지어내면 드리프트). 값 자체는 건드리지 않는다 — forecast_scorer의 예측·실측이 같은 소스라
    내부 일관성이 이미 성립하고, 예측은 산식에 물려 있지 않다.
    """
    if forecast is None:
        return ""
    basis = forecast_source.CONV_AMT_BASIS_LABEL.get(grain)
    basis_note = f"({basis})" if basis else ""
    return (
        f" 예측(오늘): clk={forecast['pred_clk']}, cost={forecast['pred_cost']}원, "
        f"conv_amt{basis_note}={forecast['pred_conv_amt']}원."
    )


def _step_down_bid(current_bid: int) -> int:
    """스텝 하향 입찰가(±15% 가드레일과 동일 산식, guardrail_gate._MAX_CHANGE_PCT 재사용) —
    현재입찰×(1-스텝)의 10원 올림(ROUND_CEILING — 내림하면 15%를 넘어버림) 과 절대 하한
    70원(_MIN_BID 규약, guardrail_gate와 동일) 중 큰 값.

    RL4(D-NAO-60)에서 _bid_proposal의 bid_down 클램프와 _stop_loss_proposal의 키워드 고삐가
    동일 산식을 공유하도록 추출한 헬퍼 — 중복 로직 방지(단일 진실 소스)."""
    stepped = Decimal(current_bid) * (Decimal(1) - _MAX_CHANGE_PCT)
    step_floor = int((stepped / 10).to_integral_value(rounding=ROUND_CEILING)) * 10
    return max(step_floor, 70)


def _ad_step_bid(current_ad_bid: int, direction: str) -> int | None:
    """소재 bidAmt 스텝(±15% 가드레일 동일 산식, B3 D-NAO-65) — down=_step_down_bid(10원 올림·
    70원 하한), up=10원 내림·10만원 상한. 스텝이 소실(방향 무의미)이면 None(억지 제안 금지,
    _bid_proposal/_clamp_step과 동일 규약 — 가드레일 방향검증에 어차피 막힐 제안을 안 만든다)."""
    if direction == "down":
        step = _step_down_bid(current_ad_bid)
        return step if step < current_ad_bid else None
    if direction == "up":
        step = int((Decimal(current_ad_bid) * (Decimal(1) + _MAX_CHANGE_PCT)) // 10) * 10
        step = min(step, 100_000)
        return step if step > current_ad_bid else None
    return None


def _ad_bid_proposal(
    *, ad_id: str, adgroup_id: str, campaign_id: str, current_ad_bid: int,
    direction: str, board_name: str, note: str = "",
) -> dict | None:
    """레버 미연결(useGroupBidAmt=false 소재) 그룹의 ad-레벨 bid_down/bid_up 제안(B3 카나리,
    D-NAO-65 설계질문 3·4). 그룹입찰 대신 실효 레버(소재 bidAmt)를 직접 스텝 조정한다 —
    target_type='ad'·target_id=nccAdId·adgroup_id 병기(harness가 부모 그룹으로 up BEP 컨텍스트를
    채운다). useGroupBidAmt는 건드리지 않는다(§0 강제 전환 금지 — writer가 false 유지만 검증).
    스텝 소실(방향 무의미·소재 at-floor)이면 None(호출부가 기존 대기/pause 경로로 폴백).
    GATE P2-2: 카나리 1단계 게이트(_AD_BID_CANARY_PROPOSAL_TYPES={"bid_down"}) — up은 None
    (2단계 개방 시 상수 확장만으로 열림). ★소급채점/diary는 target_type='ad'를 아직 전용
    grain으로 채점하지 않는다 — 부모 adgroup grain 채점이 정확(후속, adgroup_id 병기가 그 원료)."""
    target_bid = _ad_step_bid(current_ad_bid, direction)
    if target_bid is None:
        return None
    proposal_type = _BID_UP if direction == "up" else _BID_DOWN
    # GATE P2-2 DOWN 한정 — 함수 레벨 import(순환 회피, build()의 _ad_bid_canary와 동일 관례).
    from app.services.naver_ad.auto_operator import _AD_BID_CANARY_PROPOSAL_TYPES
    if proposal_type not in _AD_BID_CANARY_PROPOSAL_TYPES:
        return None
    rationale = (
        f"[{board_name}·소재입찰] 레버 실연결(useGroupBidAmt=false 소재 {ad_id}) 소재입찰 "
        f"{current_ad_bid}→{target_bid}원 (B3 카나리 소재-레벨 제어, 그룹입찰 우회 소재의 실효 "
        f"레버 직접 조정){note}"
    )
    return {
        "proposal_type": proposal_type,
        "target_type": "ad",
        "target_id": ad_id,
        "adgroup_id": adgroup_id,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "expected_effect": (
            "소재-레벨 실효입찰 직접 조정 — 그룹입찰이 헛도는 useGroupBidAmt=false 소재를 실효 "
            "레버로 하향/상향(D-NAO-65 예외② leash화). ±15%·쿨다운·BEP 소재 단위 정합."
        ),
        "status": "pending",
        "target_bid": target_bid,
    }


def _band_ad_route(
    row: dict, band_effs: dict, campaign_id: str, board_name: str, bid_sims: dict,
) -> dict | None:
    """미연결 밴드 그룹(shopping_group_bep/growth)의 ad-레벨 라우팅(B3 카나리) — 방향은 sim,
    스텝은 소재 bidAmt(band_effs의 실효입찰). sim 부재/방향 불일치/소재 at-floor면 None
    (억지 제안 금지)."""
    e = band_effs.get(row["adgroup_id"])
    if not e or not e.get("max_ad_id"):
        return None
    sim = bid_sims.get(("adgroup", row["adgroup_id"]))
    if sim is None or sim["direction"] not in _ALLOWED_DIRECTIONS.get(board_name, {"up", "down"}):
        return None
    return _ad_bid_proposal(
        ad_id=e["max_ad_id"], adgroup_id=row["adgroup_id"], campaign_id=campaign_id,
        current_ad_bid=e["effective_bid"], direction=sim["direction"], board_name=board_name,
    )


def _tag_gave(p: dict | None, board_name: str, row: dict, correction_factor: Decimal) -> dict | None:
    """D-NAO-72-2: GAVE 사전 정렬용 임시 태그 — harness(proposal_pipeline._apply_gave_priority)가
    읽어 기대 GAVE(gave_score.score_batch)를 계산·정렬한 뒤 persist 직전 제거한다
    (NaverProposal 컬럼 아님 — build()의 budget_up "total_gap" 임시키와 동일 관례).
    revenue는 D-NAO-21 보정계수를 적용(정착창 매출 근사) — retro_scorer.gave_score_d7
    산출(revenue=conv_post×cf_asof)과 동일 정의라 사전 기대치·사후 실적을 같은 잣대로
    비교할 수 있다. board_name은 retro_snapshotter._BOARDS와 동일 6종만 호출부에서 넘긴다
    (그 외 보드는 사후 GAVE 롤업이 없어 태깅하지 않음 — 기존 순서 폴백)."""
    if p is None:
        return None
    p["_gave_board"] = board_name
    p["_gave_revenue"] = Decimal(row.get("conv_amt", 0)) * correction_factor
    p["_gave_cost"] = row.get("cost", 0)
    return p


def _bid_proposal(
    row: dict, sim: dict | None, campaign_id: str, target_id: str, *,
    target_type: str, target_label: dict, board_name: str, forecast: dict | None = None,
) -> dict | None:
    """진단 보드 1행 + bid_simulator 결과 → bid_up/bid_down/negative_keyword 제안 1건.

    sim이 없으면(harness가 이번 회차 estimate/시뮬을 못 만든 경우) 제안을 만들지 않고
    건너뛴다(다음 08:00에 재시도 — 억지로 부정확한 제안을 만들지 않음).
    sim.direction='hold'는 변경 불필요라 제안하지 않는다.
    economic_ceiling<=0(수익성 있는 입찰가 자체가 없음)이고 target_type='keyword'면
    bid_down 대신 negative_keyword로 격상(계속 낮은 입찰을 제안해봐야 무의미) — 이 격상은
    보드 무관하게 항상 허용. 격상되지 않은 bid_up/bid_down은 보드가 허용하는 방향
    (_ALLOWED_DIRECTIONS)과 일치해야 한다 — 표본이 얇은 키워드는 계층 수축으로 보드 의도와
    반대 방향이 나올 수 있어(라이브검증 실측), 그런 경우 억지로 제안하지 않고 건너뛴다.
    """
    if sim is None:
        return None
    if sim["direction"] == "hold":
        return None

    if sim["economic_ceiling"] <= 0 and target_type == "keyword":
        proposal_type = _NEGATIVE
    else:
        if sim["direction"] not in _ALLOWED_DIRECTIONS.get(board_name, {"up", "down"}):
            return None
        proposal_type = f"bid_{sim['direction']}"  # bid_up / bid_down

    # X1b T3(D-NAO-38 갭①): 추천입찰가를 구조화 컬럼에 저장 — 실행자는 이 필드만 읽는다
    # (rationale 텍스트 파싱 금지). negative_keyword 격상 시엔 입찰 목표가 없어 None.
    target_bid = sim["recommended_bid"] if proposal_type != _NEGATIVE else None
    clamp_note = ""
    if proposal_type == "bid_up":
        # 라이브[P1] DOA 수정(04 카나리 실측): 경제 상한을 target_bid로 직행 저장하면 실행
        # 가드레일 _MAX_CHANGE_PCT(±15%)와 영구 충돌 — bid_up 제안이 구조적으로 전부 발사
        # 불능(스텝 +39%~+4300% 전건 차단). 생성 단계에서 min(시뮬 추천, 현재입찰×(1+스텝))
        # 으로 클램프(10원 내림 — affordable_ceiling과 동일 규약). 상한까지는 여러 라운드에
        # 걸쳐 스텝 단위로 접근한다(growth_bid_up은 가드레일 면제 유형이라 대상 아님).
        current_bid = sim.get("current_bid")
        if current_bid:
            step_cap = int(
                (Decimal(current_bid) * (Decimal(1) + _MAX_CHANGE_PCT)) // 10
            ) * 10
            if step_cap < target_bid:
                target_bid = step_cap
                clamp_note = f"(경제상한 {sim['economic_ceiling']}원, 스텝 클램프)"
            if target_bid <= current_bid:
                # 클램프/불일치 sim으로 스텝이 소실되면 skip(기존 '억지 제안 금지' 관례) —
                # 가드레일 방향 불일치로 어차피 차단될 제안을 만들지 않는다.
                return None
    elif proposal_type == "bid_down":
        # bid_down 대칭(ref31 실측: down 정밀도 61~88% — 검증된 핵심 루프가 DOA면 사망):
        # 경제 하한 직행도 같은 ±15% 가드레일과 충돌. 스텝 하한 = 현재입찰×(1-스텝)의
        # **10원 올림**(내림하면 15%를 넘어버림 — up의 내림과 방향 대칭) 후 절대 하한 70원
        # (기존 클램프 규약) 유지. target = max(시뮬 추천, 스텝 하한).
        current_bid = sim.get("current_bid")
        if current_bid:
            step_floor = _step_down_bid(current_bid)
            if step_floor > target_bid:
                target_bid = step_floor
                clamp_note = f"(경제하한 {sim['economic_ceiling']}원, 스텝 클램프)"
            if target_bid >= current_bid:
                # 스텝 소실/불일치 sim — bid_up과 동일 skip 관례.
                return None

    rationale = (
        f"[{board_name}] 보정ROAS={row.get('roas_corrected')} cost={row.get('cost')}원 "
        f"clk={row.get('clk')} — 시뮬 근거={sim['basis']}, 추천입찰={target_bid if target_bid is not None else sim['recommended_bid']}원{clamp_note}, "
        f"target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + "."
        + _forecast_evidence_suffix(forecast, target_type)
    )

    # codex[P2] 클램프 후 예측치 정합: sim의 예측 텍스트(예상 클릭·매출)는 원 추천 입찰가
    # 기준이라 클램프된 target_bid와 불일치 — 그대로 두면 콘솔 오도 + predicted_json
    # (naver_execution_harness가 expected_effect를 복사 저장)이 잘못된 기준으로 남는다.
    # 클램프 입찰가 기준 재산출은 불가(예측클릭 = /estimate/performance API 2차 패스 산출,
    # 이 SA는 API 접근 없음·로컬 응답곡선 없음) → 정직 정리로 교체(추정 금지 원칙).
    expected_effect = sim["expected_effect_text"]
    if clamp_note:
        expected_effect = (
            f"스텝 클램프 {target_bid}원 실행 — 원 추천 {sim['recommended_bid']}원 기준 "
            f"예측치는 무효(클램프 입찰가 기준 재산출 불가, 정직 경계)."
        )

    return {
        "proposal_type": proposal_type,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "expected_effect": expected_effect,
        "status": "pending",
        "target_bid": target_bid,
    }


def _growth_proposal(
    candidate: dict, sim: dict | None, target_label: dict, *, forecast: dict | None = None,
) -> dict | None:
    """growth_sweeper 후보 1건(D-NAO-22-①) + bid_simulator 결과 → growth_bid_up 제안.

    sim 없음(harness가 이번 회차 estimate/시뮬을 못 만든 경우)이거나 direction이
    "up"이 아니면(_ALLOWED_DIRECTIONS["growth_sweeper"]) 건너뛴다 — 표본이 얇은 후보는
    계층 수축으로 성장 의도와 반대 방향이 나올 수 있어(_bid_proposal과 동일 원칙), 억지로
    제안하지 않는다. D-NAO-20 스톱로스 절대액을 rationale에 부착(정직 경계: 이번 회차엔
    실행/집행 게이트가 아니라 정보 노출만 — Phase 5 execution_harness가 실제 집행 시 강제).
    """
    if sim is None or sim["direction"] not in _ALLOWED_DIRECTIONS["growth_sweeper"]:
        return None

    stop_loss_amount = sim["recommended_bid"] * growth_sweeper.STOP_LOSS_CLICK_MULTIPLE
    rationale = (
        f"[growth_sweeper] 갭={candidate['gap']}원(경제성상한={candidate['economic_ceiling']}원 "
        f"vs 현재입찰={candidate['current_bid']}원) clk={candidate['clk']}"
        + (" (저클릭 표본)" if candidate.get("sample_thin") else "")
        + f" — 시뮬 근거={sim['basis']}, 추천입찰={sim['recommended_bid']}원, "
        f"target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + f". D-NAO-20 스톱로스={stop_loss_amount}원"
        f"(무전환 {growth_sweeper.STOP_LOSS_CLICK_MULTIPLE}클릭 상당 지출 도달 시 재검토 신호)."
        + _forecast_evidence_suffix(forecast, "keyword")
    )
    return {
        "proposal_type": _GROWTH_BID_UP,
        "target_type": "keyword",
        "target_id": candidate["keyword_id"],
        "campaign_id": candidate["campaign_id"],
        "rationale": rationale,
        "expected_effect": sim["expected_effect_text"],
        "status": "pending",
        "target_bid": sim["recommended_bid"],  # X1b T3(D-NAO-38 갭①) — 구조화 저장
    }


def _negative_keyword_from_exclusion(row: dict, campaign_id: str) -> dict:
    """확장버킷 비용상위 검색어 → 제외후보 제안(전환귀속 없음 — '비용/볼륨 후보' 정직 라벨,
    exclusion_candidates 보드 자체가 전환 데이터 없이 비용순만 제공하므로 그 경계를 그대로 전달).

    adgroup_id(X1a T3): restricted-keywords 쓰기 API가 adgroupId 필수(ref 27 §8-1) —
    exclusion_candidates 보드가 이미 SELECT하는 값을 제안 생성 시점에 확정 저장(실행 시점
    재해석보다 단순·결정적)."""
    return {
        "proposal_type": _NEGATIVE,
        "target_type": "search_term",
        "target_id": row["search_term"],
        "campaign_id": campaign_id,
        "adgroup_id": row["adgroup_id"],
        "rationale": (
            f"[exclusion_candidates] 비용/볼륨 후보(전환귀속 데이터 없음, source={row.get('source')}) "
            f"cost={row.get('cost')}원, clk={row.get('clk')}, imp={row.get('imp')}."
        ),
        "expected_effect": "전환 데이터 없음 — 제외 시 비용 절감 추정만 가능(정밀 예측 불가).",
        "status": "pending",
    }


def _terminal_pause(
    row: dict, *, target_type: str, lever_broken: bool = False, floor_bleed: bool = False,
    policy_pause: bool = False,
) -> dict:
    """스톱로스 대응의 최종 단계(터미널) — pause 제안 1건. DL2(D-NAO-65)로 pause는 "정책"이
    아니라 "레버 불능 예외"로 격하됐다 — 이 함수는 (a) 예외① ML 자동입찰(입찰 API가 거부)·
    (b) 예외② 레버끊김(그룹입찰 하향으로 지출 제어 불가한 소재-레벨 입찰 정황)·
    (c) 지속 밸브 floor_bleed(GATE P2-2 — 바닥 대기 3일+ 무전환 출혈 지속 = 레버로 할 수 있는
    걸 다 했는데도 출혈인 실증, 무한 대기 방치 차단)일 때만 쓰인다.
    `_stop_loss_proposal`이 호출한다(SA 직접 호출 금지, 이 함수는 비공개).
    lever_broken=True면 예외②로 pause되는 경우라 사유문에 실측 CPC≫그룹입찰을 창 라벨과 함께
    사실 명시(D-NAO-64 정직 경계 계승 + GATE P3-5 — CPC는 만성7일∩변경후 교집합, 누적비용은
    변경 후 행동 창이라 서로 다른 창임을 라벨로 구분). floor_bleed=True면 밸브 사유문
    (바닥 대기 N일 출혈 지속). 키워드 경로는 floor_bleed 밸브로만 이 함수에 도달한다.

    target_type='keyword'는 WEB_SITE 키워드(pause_candidates 보드, target_id=row["keyword_id"]),
    'adgroup'은 SHOPPING 광고그룹(shopping_pause_candidates 보드, target_id=row["adgroup_id"]).
    두 보드는 row 스키마가 동형(campaign_id/cost/stop_loss_amount 등 공통, 대상id 키만 다름)
    이라 한 빌더가 함께 처리한다. adgroup_id 컬럼(NaverProposal, 실쓰기 대상 광고그룹 정보용)은
    keyword 대상에서만 채운다 — adgroup 대상은 target_id 자체가 이미 그 값이라 중복 저장하지
    않는다(shopping_group_bep의 _bid_proposal이 adgroup 대상에 adgroup_id를 안 채우는 것과
    동일 관례).

    D-NAO-20 스톱로스 절대액(현재입찰×LOW_CLICK_THRESHOLD) 도달 — 무전환 지출 중단이
    목적이라 target_roas 근거 병기가 불필요(스톱로스는 손익 목표와 무관한 절대액 안전핀)."""
    target_id = row["keyword_id"] if target_type == "keyword" else row["adgroup_id"]
    board_name = "pause_candidates" if target_type == "keyword" else "shopping_pause_candidates"
    # D-NAO-64(A): 바닥그룹 저ROAS 정지는 전환이 "있는데도" 실질ROAS≪BEP·입찰 하한이라
    # 정지하는 경우(맥세이프_MO) — 사유문에 '무전환'이라 쓰면 거짓(정직 경계). 전환 유무로 분기.
    conv_amt = int(row.get("conv_amt") or 0)
    if policy_pause:
        # UI1(D-NAO-65): 이 캠페인은 loss_policy='stoploss_pause' — Jino 콘솔이 이 캠페인만
        # 종전 하드 정지로 회귀시키기로 선언한 예외다(전역 기본값은 여전히 고삐-일일리셋, §0).
        # 고삐로 내릴 여지가 있어도 정책상 정지이므로, "레버 불능 예외"(①ML ②레버끊김 ③밸브)와
        # 구분되도록 사유문을 '캠페인 정책'으로 정직 표기한다(전환 유무는 부기로만).
        rationale = (
            f"[스톱로스정지 — 캠페인 정책] 스톱로스 발동(누적비용 {row['cost']}원 ≥ 스톱로스 "
            f"{row['stop_loss_amount']}원, 현재입찰 {row['current_bid']}원) → 고삐 대신 정지"
            f"(loss_policy=stoploss_pause, D-NAO-65 UI1) clk={row.get('clk')}."
            + (f" 전환 {conv_amt}원." if conv_amt > 0 else "")
        )
    elif lever_broken:
        # DL2 예외②: 그룹입찰 하향으로 지출이 안 줄어드는 레버끊김(실측 CPC≫그룹입찰) — pause만
        # 유일 실효 레버. 전환 유무와 무관하게 '무전환'이라 쓰지 않는다(정직 경계, 전환이 있을
        # 수 있음). GATE P3-5: CPC(만성 7일∩변경 후 교집합)와 누적비용(변경 후 행동 창)은 서로
        # 다른 창의 수치라 창 라벨을 명시한다(라벨 없으면 오독).
        rationale = (
            f"[{board_name}] 레버 끊김 — 실측 CPC {row.get('chronic_cpc')}원(만성 7일∩변경 후) "
            f"≫ 그룹입찰 {row['current_bid']}원(그룹입찰 하향으로 지출 제어 불가, 소재-레벨 입찰 "
            f"정황) → 정지(D-NAO-65 예외②) 누적비용 {row['cost']}원(변경 후 창) ≥ 스톱로스 "
            f"{row['stop_loss_amount']}원"
            + (f", 전환 {conv_amt}원(실질ROAS≪BEP)" if conv_amt > 0 else "")
            + f" clk={row.get('clk')}."
        )
    elif floor_bleed:
        # DL2 GATE P2-2 지속 밸브: 바닥 대기 N일+ 무전환 출혈 지속 — "레버로 할 수 있는 걸 다
        # 했는데도 출혈" = 레버 불능 예외의 실증 케이스(무한 대기 방치 차단).
        # B2 GATE P3-1(정직 경계): 레버 미연결(effective_source='ad') 경유면 "바닥"이 아니다 —
        # 입찰이 하한이라 대기한 게 아니라 그룹입찰이 무효(소재입찰이 우회)라 대기한 것.
        # 사유문을 사실대로 분기(레버 미연결 대기 + 실효 소재입찰 명기).
        if row.get("effective_source") == "ad":
            rationale = (
                f"[{board_name}] 레버 미연결 대기 {row.get('window_days')}일(실효 소재입찰 "
                f"{row.get('effective_bid')}원, 그룹입찰 {row['current_bid']}원 무효 — B3 전) "
                f"무전환 출혈 지속(비용 {row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원) "
                f"→ 정지(D-NAO-65 지속 밸브) clk={row.get('clk')}."
            )
        else:
            rationale = (
                f"[{board_name}] 바닥 창 {row.get('window_days')}일 무전환 출혈 지속"
                f"(비용 {row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원, 입찰 하한 "
                f"{row['current_bid']}원) → 정지(D-NAO-65 지속 밸브) clk={row.get('clk')}."
            )
    elif target_type == "keyword":
        rationale = (
            f"[스톱로스-터미널] 입찰 하한({row['current_bid']}원) 도달 무전환 → "
            f"정지(D-NAO-60 RL4 터미널) cost={row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원 "
            f"clk={row.get('clk')}."
        )
    elif conv_amt > 0:
        rationale = (
            f"[{board_name}] 저ROAS 바닥그룹(전환 {conv_amt}원 있으나 실질ROAS≪BEP) 누적비용 "
            f"{row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원(현재입찰={row['current_bid']}원 "
            f"입찰 하한이라 하향 불가 → 정지, D-NAO-64) clk={row.get('clk')}."
        )
    else:
        rationale = (
            f"[{board_name}] 무전환 누적비용 {row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원"
            f"(현재입찰={row['current_bid']}원, D-NAO-20) clk={row.get('clk')}."
        )
    return {
        "proposal_type": _PAUSE,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"] if target_type == "keyword" else None,
        "target_lock": True,
        "rationale": rationale,
        "expected_effect": (
            "손실 지출 중단 — 추가 비용 발생 차단(D-NAO-16 정지)." if conv_amt > 0
            else "무전환 지출 중단 — 추가 비용 발생 차단(D-NAO-16 정지)."
        ),
        "status": "pending",
    }


def _adgroup_is_manual_bid(adgroup_id: str) -> bool | None:
    """쇼핑 광고그룹이 수동입찰인지 판정(RL4b, D-NAO-60) — naver_sa_writer.update_adgroup_bid의
    ML 자동입찰 사전가드와 동일 판정을 생성 단계에서 재사용한다(추정 금지·단일 진실 소스 —
    같은 판정을 두 곳에 따로 구현하면 드리프트 위험). systemBiddingType이 'NONE'이고
    autobidStrategy.isAutobidActive가 명시적으로 False일 때만 True(수동) — 그 외(ML·필드
    누락·비-dict·불명)는 전부 False(update_adgroup_bid와 동일하게 모호하면 차단 쪽으로,
    fail-closed on ambiguity).

    재조회(GET) 자체가 실패(API 예외·404/403 등)하면 판정 자체가 불가능하므로 None을
    반환한다 — 호출부(_stop_loss_proposal)는 manual_bid가 True가 아니면 전부 터미널
    pause로 처리하므로 None도 안전 쪽(정지)으로 떨어진다(fail-closed)."""
    try:
        adgroup = naver_sa_writer._get_adgroup(adgroup_id)
    except Exception as e:  # noqa: BLE001 — 재조회 실패는 판정불가(None), 호출부가 안전측(pause) 처리
        log.warning("proposal_writer: adgroup 입찰유형 재조회 실패 adgroup=%s: %s", adgroup_id, e)
        return None
    system_bidding_type = adgroup.get("systemBiddingType")
    autobid_strategy = adgroup.get("autobidStrategy")
    autobid_active = autobid_strategy.get("isAutobidActive") if isinstance(autobid_strategy, dict) else None
    return system_bidding_type == "NONE" and autobid_active is False


def _stop_loss_proposal(
    row: dict, *, target_type: str = "keyword", manual_bid: bool | None = None,
    ad_bid_canary: bool = False, loss_policy: str | None = None,
) -> dict | None:
    """pause_candidates/shopping_pause_candidates 보드 1행(account_diagnosis) → 스톱로스 대응
    제안(X1b T3 D-NAO-38 → RL4 D-NAO-60 고삐화 → DL2 D-NAO-65 pause 예외화·바닥 대기).

    ★D-NAO-60 RL4(총이익 극대화, D-NAO-59): 스톱로스는 정지 대신 순위 하향(고삐, bid_down)
    으로 대응한다 — 무전환 지출이 지속되면 다음 날 재발동해 또 한 등 하향(자연 graduation),
    도중 전환이 살아나면 자동 사면. 쇼핑(RL4b)도 수동입찰이면 동일 고삐.

    ★DL2(D-NAO-65): at-floor(더 못 내림)를 "정지"가 아니라 **바닥 대기(무액션, None 반환)**
    로 바꾼다 — 바닥(70원)에선 노출~0=출혈~0이고 익일 밴드 재시작(DL4)이 다시 기회를 준다.
    pause는 "정책"이 아니라 "레버 불능 예외" ①②만:
    - **예외① ML 자동입찰**(쇼핑 manual_bid가 True 아님 = False/None): 입찰 변경 API가 거부
      하므로 pause만 실효 → 터미널 pause(불변).
    - **예외② 레버 끊김**(쇼핑 manual_bid=True·at-floor·row['lever_broken']=True): 그룹입찰
      하향으로 지출이 안 줄어드는 소재-레벨 입찰 정황 → pause가 유일 실효 레버.
    manual_bid=True이고 하향 여지가 있으면(스텝하한 < 현재입찰) 기존 bid_down 고삐(불변).
    키워드(pause_candidates) at-floor는 레버끊김 개념 없음(파워링크 키워드 입찰은 직접 유효)
    → 바닥 대기(None). manual_bid는 호출부(build())가 `_adgroup_is_manual_bid`로 미리
    해석해 넘긴다(이 함수는 API를 직접 부르지 않는 순수 함수 — 테스트 주입성).

    ★GATE P2-2 지속 밸브(키워드·쇼핑 공통): 바닥 대기는 영구가 아니라 최대 3일의 실증 기간 —
    at-floor에서 행동 창 ≥3일 무전환 출혈이 지속되면(진단이 row['floor_bleed'] 태깅) "레버로
    할 수 있는 걸 다 했는데도 출혈" = 레버 불능 예외의 실증으로 터미널 pause 격상.
    row['lever_broken']/['chronic_cpc']/['floor_bleed']/['window_days']는 account_diagnosis가
    태깅한 값(단일 진실 소스 — k 임계·최소 실증 기간 상수는 그쪽에 canonical).

    ★UI1(D-NAO-65) loss_policy: 캠페인별 예외 스위치. build()가 _loss_policy_map으로 미리
    로드해 주입한다(순수 함수 유지 — DB 직접 조회 안 함). None/'leash'(기본)=위 DL/B 동작
    그대로(회귀 0). 'stoploss_pause'=스톱로스 발동 즉시 고삐(bid_down)·바닥 대기·B3 소재
    라우팅을 전부 스킵하고 종전 하드 pause로 회귀(키워드·쇼핑 수동입찰 공통). 단 쇼핑 ML
    (manual_bid가 True 아님)은 정책과 무관하게 기존 예외① pause 그대로 — 정책 pause가 아니라
    레버 불능 pause라 사유문을 relabel하지 않는다(정직 경계, §0 pause 예외 불변)."""
    policy_pause = loss_policy == "stoploss_pause"
    if target_type == "adgroup":
        current_bid = row["current_bid"]  # 그룹입찰(target_bid 스텝 기준 — B2는 소재입찰 안 씀)
        # B2(D-NAO-65): at-floor·레버연결 판정은 실효입찰(effective_bid) 기준(진단과 정합).
        # 그룹입찰이 곧 실효 레버일 때만(effective_source='group' = 실효==그룹) 그룹 bid_down이
        # 유효 — 실효≫그룹(useGroupBidAmt=false 소재가 그룹입찰 우회)이면 그룹 bid_down은 무의미
        # 하므로 제안하지 않고 lever/pause 판정에 위임한다(소재-레벨 제어는 B3). effective_bid/
        # effective_source 미제공(하위호환·키워드)이면 그룹입찰 기준으로 폴백(종전 동작 보존).
        step_floor = _step_down_bid(current_bid)  # 그룹 스텝(target_bid — 소재입찰 아님)
        effective_source = row.get("effective_source")
        effective_bid_val = row.get("effective_bid", current_bid)
        if effective_source is not None:
            lever_connected = effective_source == "group"
        else:
            lever_connected = effective_bid_val == current_bid
        if manual_bid is True:
            if policy_pause:
                # UI1(D-NAO-65): loss_policy='stoploss_pause' — 고삐·바닥 대기·B3 소재 라우팅·
                # 레버/밸브 판정을 전부 스킵하고 종전 하드 정지로 회귀(§0 캠페인 예외 스위치).
                # leash/NULL이면 policy_pause=False라 아래 기존 고삐 경로가 바이트 단위로 불변.
                return _terminal_pause(row, target_type=target_type, policy_pause=True)
            if step_floor < current_bid and lever_connected:
                # 그룹입찰이 실효 레버이고 하향 여지 있음 → 기존 bid_down 고삐(RL4b 불변).
                rationale = (
                    f"[스톱로스고삐] 무전환 누적비용 {row['cost']}원≥스톱로스 {row['stop_loss_amount']}원"
                    f"(현재입찰 {current_bid}→{step_floor}원, D-NAO-60 RL4b 쇼핑 수동입찰 한 등 하향) "
                    f"clk={row.get('clk')}."
                )
                return {
                    "proposal_type": _BID_DOWN,
                    "target_type": "adgroup",
                    "target_id": row["adgroup_id"],
                    "campaign_id": row["campaign_id"],
                    "rationale": rationale,
                    "expected_effect": (
                        "무전환 지출 순위 고삐(쇼핑 수동입찰) — 정지 대신 한 등 하향, ML/하한이면 "
                        "pause. D-NAO-59 총이익."
                    ),
                    "status": "pending",
                    "target_bid": step_floor,
                }
            # B3 카나리(D-NAO-65): 레버 미연결(effective_source='ad') 소재를 실효 레버로 직접
            # 하향(그룹 bid_down은 무의미). 예외② leash화 — 이전엔 바닥 대기(None)였던 미연결
            # 유닛이 소재-레벨 bid_down 고삐를 받는다. 소재 at-floor(스텝 소실)면 _ad_bid_proposal
            # 이 None → 아래 기존 예외 경로로 폴백. 미카나리/소재 데이터 부재(effective_ad_id
            # 없음)도 기존 경로(대기/pause 예외).
            # ★B4(D-NAO-65) item 1 — lever_broken 조건부 은퇴: B3에선 `not lever_broken`으로
            #   lever_broken 유닛을 이 라우팅에서 제외해 pause로 뒀지만(안전망), B3 제어가 라이브
            #   검증됐으므로 카나리에 한해 그 제외를 해제한다. lever_broken이어도 소재 실효입찰에
            #   하향 여지가 있으면(스텝 성립) 소재-레벨 bid_down으로 되돌린다(kill→leash). 소재
            #   실효입찰까지 하한(70)인 진짜 레버불능(CPC 폭등)만 _ad_bid_proposal이 None을 내
            #   아래 lever_broken 터미널 pause로 떨어진다(§0 금지선: pause는 정책 아닌 예외).
            #   비카나리는 ad_bid_canary=False라 이 블록 자체를 건너뛰어 기존 pause 유지(회귀 0).
            if (
                ad_bid_canary and row.get("effective_source") == "ad"
                and row.get("effective_ad_id")
            ):
                if row.get("lever_broken"):
                    note = (
                        f" 레버끊김(실측 CPC {row.get('chronic_cpc')}원 ≫ 실효입찰) — 소재입찰 "
                        f"하향으로 지출 제어(B4 예외② leash화). 누적비용 {row['cost']}원 ≥ 스톱로스 "
                        f"{row['stop_loss_amount']}원, clk={row.get('clk')}."
                    )
                else:
                    note = (
                        f" 무전환 누적비용 {row['cost']}원 ≥ 스톱로스 "
                        f"{row['stop_loss_amount']}원, clk={row.get('clk')}."
                    )
                ad_p = _ad_bid_proposal(
                    ad_id=row["effective_ad_id"], adgroup_id=row["adgroup_id"],
                    campaign_id=row["campaign_id"], current_ad_bid=effective_bid_val,
                    direction="down", board_name="shopping_pause_candidates", note=note,
                )
                if ad_p is not None:
                    return ad_p
            # 실효입찰 at-floor OR 레버 미연결(실효≫그룹 — 그룹 bid_down 무의미) → lever/pause 판정.
            # DL2 예외② 레버끊김 → pause / 지속 밸브 floor_bleed(GATE P2-2, 바닥 3일+ 무전환 출혈
            # 지속) → 밸브 pause / 그 외 → 바닥 대기(None). 예외②가 밸브보다 우선(정직 경계).
            # B4: 카나리·하향 여지 있는 lever_broken은 위에서 이미 소재 bid_down으로 반환됐고,
            # 여기 도달하는 lever_broken은 소재까지 하한인 진짜 레버불능(터미널 pause 잔존).
            if row.get("lever_broken"):
                return _terminal_pause(row, target_type=target_type, lever_broken=True)
            if row.get("floor_bleed"):
                return _terminal_pause(row, target_type=target_type, floor_bleed=True)
            return None
        # manual_bid is not True(ML 자동입찰·판정불가 None) → 예외① 터미널 pause(불변).
        return _terminal_pause(row, target_type=target_type)

    current_bid = row["current_bid"]
    step_floor = _step_down_bid(current_bid)
    if policy_pause:
        # UI1(D-NAO-65): 키워드(파워링크)도 loss_policy='stoploss_pause'면 고삐·바닥 대기 스킵,
        # 즉시 종전 하드 정지(§0 캠페인 예외 스위치). leash/NULL이면 아래 기존 경로 불변(회귀 0).
        return _terminal_pause(row, target_type=target_type, policy_pause=True)
    if step_floor >= current_bid:
        # DL2: 이미 입찰 하한(70원) — 정지가 아니라 바닥 대기(무액션). 키워드는 레버끊김 개념
        # 없음(직접 입찰 유효). 익일 밴드 재시작(DL4)이 기회를 준다.
        # GATE P2-2 지속 밸브: 바닥 3일+ 무전환 출혈 지속(진단이 floor_bleed 태깅)이면 대기가
        # 아니라 터미널 pause — 대기는 최대 3일의 실증 기간이지 영구 방치가 아니다.
        if row.get("floor_bleed"):
            return _terminal_pause(row, target_type=target_type, floor_bleed=True)
        return None

    rationale = (
        f"[스톱로스고삐] 무전환 누적비용 {row['cost']}원≥스톱로스 {row['stop_loss_amount']}원"
        f"(현재입찰 {current_bid}→{step_floor}원, D-NAO-60 RL4 정지 대신 한 등 하향) "
        f"clk={row.get('clk')}."
    )
    return {
        "proposal_type": _BID_DOWN,
        "target_type": "keyword",
        "target_id": row["keyword_id"],
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"],
        "rationale": rationale,
        "expected_effect": (
            "무전환 지출 순위 고삐 — 한 등 하향(정지 대신, 자연 재발동시 하한까지·전환 살면 "
            "사면). D-NAO-59 총이익."
        ),
        "status": "pending",
        "target_bid": step_floor,
    }


def _resume_proposal(row: dict, target_label: dict, *, target_type: str = "keyword") -> dict:
    """resume_candidates/shopping_resume_candidates 보드 1행 → resume 제안(X1b T3, D-NAO-38
    / 쇼핑 adgroup 확장은 X1b-S S1, D-NAO-43). target_type 규약은 _stop_loss_proposal/
    _terminal_pause와 동일(기본값 'keyword'=하위호환, 'adgroup'=쇼핑 광고그룹).

    정지 직전 창의 보정ROAS가 현재 목표(target_roas) 이상으로 회복 — D-NAO-16 "정지 사유
    해소"(BEP 개선) 신호. 우리 시스템이 정지시킨 대상만(account_diagnosis.resume_candidates/
    shopping_resume_candidates가 proposal_id 없는 수동 정지를 이미 제외)."""
    target_id = row["keyword_id"] if target_type == "keyword" else row["adgroup_id"]
    board_name = "resume_candidates" if target_type == "keyword" else "shopping_resume_candidates"
    return {
        "proposal_type": _RESUME,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"] if target_type == "keyword" else None,
        "target_lock": False,
        "rationale": (
            f"[{board_name}] 정지({row['paused_at']}) 직전 보정ROAS {row['roas_at_pause']}"
            f"(현재 목표={target_label.get('target_roas')}, 근거={target_label.get('source')}) "
            f"— BEP 개선 신호(D-NAO-16)."
        ),
        "expected_effect": "정지 해제 — 재노출 재개.",
        "status": "pending",
    }


def _lever_resume_proposal(row: dict) -> dict:
    """레버끊김 그룹 재개 제안 (B4, D-NAO-65 item 2·3 — GATE P2-1 "바닥에서 재개") —
    shopping_lever_resume_candidates의 action='resume' 행(소재 실효입찰이 바닥 70원까지
    내려옴) → resume 제안.

    shopping_resume_candidates(정지 직전 ROAS 회복 신호)의 _resume_proposal과 별개인 이유:
    레버끊김 그룹은 정지 당시 ROAS가 나빠 그 회복 게이트를 못 통과한다(정지 사유가 ROAS가
    아니라 '그룹입찰 하향으로 지출 제어 불가'였다). 재개 근거는 대신 '소재 실효입찰을 바닥까지
    잡았다 = 실효레버 확보 + 재출혈 상한 ≈ 바닥 노출 수준'이다. 재개는 최소 노출로 증거 축적
    재개일 뿐이고, 올리는 건 DL4 밴드 재시작의 BEP 게이트가 정착창 ROAS 실증과 함께 수행한다
    (D-NAO-59 — 증거 없이 올리지 않는다).

    ★GATE P2-1 c 정직 경계: 카나리 체제에선 재개 후의 교정 액션(소재 bid_down 등)도 전부
    Confirm-only pending이라 "자동 감시"라 쓰면 거짓 — rationale에 사실대로 명시한다.
    resume 자체도 자동 레인(_DAILY_LANE_PROPOSAL_TYPES) 대상이 아닌 콘솔 Confirm 전용.
    카나리 스코프·ML 사전 제외·순서강제는 build()가 적용."""
    return {
        "proposal_type": _RESUME,
        "target_type": "adgroup",
        "target_id": row["adgroup_id"],
        "campaign_id": row["campaign_id"],
        "adgroup_id": None,  # adgroup 대상은 target_id 자체가 그 값(_resume_proposal 관례)
        "target_lock": False,
        "rationale": (
            f"[shopping_lever_resume] 소재 실효입찰 {row['effective_bid']}원 = 입찰 바닥 — "
            f"최소 노출로 증거 축적 재개(정지 {row['paused_at']}, D-NAO-65 예외② leash화). "
            f"바닥 재개라 재출혈 상한 ≈ 바닥 노출 수준. 상향은 밴드 재시작(DL4)의 BEP 게이트가 "
            f"정착창 ROAS 실증과 함께 수행(증거 없이 올리지 않음, D-NAO-59). 재개 후 교정 "
            f"제안은 콘솔 Confirm 대기 — 자동 실행 아님(카나리 Confirm-only)."
        ),
        "expected_effect": (
            "정지 해제 — 바닥(최소 노출) 재개로 증거 축적 재개. 재출혈 상한 ≈ 바닥 노출 수준, "
            "교정 제안은 콘솔 Confirm 대기(D-NAO-65 예외② leash화)."
        ),
        "status": "pending",
    }


def _size_budget_up(current: int, pred_cost: int | None) -> int:
    """목표 일예산 사이징(PLAN §5-G, D-NAO-42-f) — marginal ROAS 인과추정 없이(추정 금지)
    목표 예산을 정한다.

    pred_cost(캠페인 grain 예측, forecast_engine 추세 지수감쇠·비인과, D-NAO-26/28) 있으면
    "추세가 말하는 미제한 지출"까지 완화(min(현재×2, max(현재+1증분, pred_cost))) — 현재×2는
    캠페인당 +100% 캡(계획서 §2③, guardrail_gate._BUDGET_UP_MAX_CHANGE_PCT와 동일 상한을
    사이징 단계에서 선반영)이다. pred_cost 없으면(fallback, 예측 미가동/미적재) 보수적 고정
    스텝 +20%(Jino 확정 2026-07-13, current×1.2).

    100원 단위로 반올림(네이버 침묵 반올림 방어, §5-B) — 반올림 결과가 현재값 이하로
    떨어지면 다음 100원 단위로 올려 방향 불일치(guardrail_gate가 차단)를 사전에 막는다.
    이 함수는 사이징(생성 단계) 휴리스틱일 뿐 최종 방어는 여전히 guardrail_gate._check_budget
    (실행 직전 재검증, 이중 방벽)."""
    if pred_cost is not None:
        raw = min(current * 2, max(current + _BUDGET_ROUND_INCREMENT, pred_cost))
    else:
        raw = current * 1.2  # Jino 확정 fallback +20%(2026-07-13)

    target = int(round(raw / _BUDGET_ROUND_INCREMENT) * _BUDGET_ROUND_INCREMENT)
    if target <= current:
        target = ((current // _BUDGET_ROUND_INCREMENT) + 1) * _BUDGET_ROUND_INCREMENT
    return target


def _classify_budget_round_envelope(deltas_and_candidates: list[tuple[int, dict]]) -> None:
    """budget_up 후보들의 라운드 봉투 분류(PLAN §5-E, 계획서 §2②) — "회당 총 증가액≤10만원
    (라운드 합계)"을 그리디로 재현한다.

    deltas_and_candidates: [(target_budget-current_budget, candidate_dict), ...]. candidate_dict
    가 "total_gap" 키를 가지고 있으면(build()가 threading — 아래 build() 참조) 이 함수가
    스스로 그 값 내림차순으로 정렬한 후 그리디 누적한다(Fix 6, codex P2 — 호출자가
    budget_allocator.find_budget_expansion_signals의 정렬을 이미 신뢰할 수 있어도, 이 함수가
    "재정렬하지 않는다"는 이전 계약은 호출자 순서에 암묵 의존이라 취약했다: 호출자가 실수로
    비정렬 리스트를 넘기면 조용히 잘못된 분류가 나온다). "total_gap"이 없는 dict(레거시
    직접호출 테스트 등)는 0으로 취급 — Python sort는 안정정렬이라 전부 같은 키(0)면 원래
    순서가 그대로 보존돼 기존 동작과 100% 호환된다.

    정렬은 candidate dict 참조 자체를 바꾸지 않는다(같은 객체에 in-place로
    "budget_auto_eligible"을 세팅) — 그래서 caller가 들고 있는 원본 리스트(어떤 순서든)의
    각 dict에도 올바른 값이 반영된다.

    누적 ΣΔ가 10만원 이내인 동안은 True(자율 승급 시 자동 발사 대상, 오늘은 반자동이라
    분류 메타일 뿐 게이트 아님 — PLAN §5-E 참조), 넘기는 순간부터는 False(초과분, 위임이
    켜져도 반드시 Jino 승인). 앞선 큰 증액이 캡을 넘겨 거부돼도, 뒤이은 더 작은 증액은
    남은 여유에 들어갈 수 있다 — 이건 "제일 큰 것부터 강제 배정"이 아니라 순서대로 소비하는
    그리디 누적이다(PLAN §5-E 명시 알고리즘)."""
    ordered = sorted(deltas_and_candidates, key=lambda dc: dc[1].get("total_gap", 0), reverse=True)
    cumulative = 0
    for delta, candidate in ordered:
        if cumulative + delta <= _ROUND_BUDGET_AUTO_CAP:
            candidate["budget_auto_eligible"] = True
            cumulative += delta
        else:
            candidate["budget_auto_eligible"] = False


def _budget_proposal(signal: dict, target_label: dict, *, forecast: dict | None = None) -> dict:
    """budget_allocator 신호(D-NAO-22-③) → budget_up 제안. 실행은 P3(D-NAO-42-f)부터 개방
    (D-NAO-16 4단계 — 반자동 게이트는 그대로, 콘솔 승인 후 실행). marginal ROAS 인과추정은
    하지 않는다(D-S3-c 연기 사유 유지, 추정 금지) — "예산 캡이 이미 소진됐고 그 안에 이익보장
    잔존 볼륨이 실측으로 확인됐다"는 사실만 제공.

    target_budget(구조화 컬럼, 실행자는 이 컬럼만 읽는다 — rationale 텍스트 파싱 금지)은
    _size_budget_up(§5-G)이 정한다. forecast(캠페인 grain pred_cost, F2b/D-NAO-26 계열
    비인과 예측)가 있으면 사이징에 반영 + rationale에 병기(_forecast_evidence_suffix,
    입찰 산식과 동일하게 정보 병기만 — 산식 자체를 바꾸지 않음). budget_auto_eligible은
    여기서 세팅하지 않는다 — 라운드 봉투 분류(_classify_budget_round_envelope)는 build()가
    이번 회차 전체 우선순위를 보고 나서 하는 별도 단계다(PLAN §5-E)."""
    current = signal["daily_budget"]
    pred_cost = forecast.get("pred_cost") if forecast else None
    target_budget = _size_budget_up(current, pred_cost)

    rationale = (
        f"[budget_allocator] 일예산 {signal['daily_budget']}원 소진(누적 {signal['cost']}원, "
        f"{signal['hour']}시 기준) — 동일 캠페인 내 이익보장 성장후보 {signal['growth_candidate_count']}건 "
        f"존재(합산 입찰여력 gap={signal['total_gap']}원). target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + f". 목표 일예산={target_budget}원(D-NAO-42-f 사이징, §5-G)."
        + _forecast_evidence_suffix(forecast, "campaign")
    )
    return {
        "proposal_type": _BUDGET_UP,
        "target_type": "campaign",
        "target_id": signal["campaign_id"],
        "campaign_id": signal["campaign_id"],
        "rationale": rationale,
        "expected_effect": (
            "예산 증액 후보 — marginal ROAS 인과추정 없음(추정 금지 원칙, D-S3-c 연기사유 유지). "
            "잠재 신호(성장후보 gap 합계)만 제공, 실제 효과는 승인 후 D+7/14 실측 필요."
        ),
        "status": "pending",
        "target_budget": target_budget,
    }


def _budget_pre_exhaustion_proposal(signal: dict, target_label: dict) -> dict:
    """budget_allocator 사전경보 신호(F2b, D-NAO-26) → 정보성 제안. 아직 예산 소진 전이지만
    오늘자 예측(forecast_model_builder pred_cost)이 daily_budget을 넘어설 것으로 보이는 경우.
    marginal ROAS 인과추정 아님(예측치를 daily_budget과 비교만 함) — anomaly_feed와 동일하게
    실행 대상 아닌 정보성 신호(D-3)."""
    rationale = (
        f"[budget_allocator 사전경보] 일예산 {signal['daily_budget']}원, 현재 누적 {signal['cost']}원"
        f"({signal['hour']}시 기준) — 오늘 예측 지출 {signal['pred_cost']}원으로 예산 초과 예상"
        f"(예측초과분={signal['pred_gap']}원). target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + "."
    )
    return {
        "proposal_type": _BUDGET_PRE_EXHAUSTION,
        "target_type": "campaign",
        "target_id": signal["campaign_id"],
        "campaign_id": signal["campaign_id"],
        "rationale": rationale,
        "expected_effect": "정보성 신호 — 실행 대상 아님(예측 기반 사전경보, marginal ROAS 인과추정 없음).",
        "status": "pending",
    }


def _anomaly_spend_proposal(item: dict) -> dict:
    """anomaly_feed 소진 급변 신호 → 정보성 제안(D-3, 실행 대상 아님). 전 캠페인 대상
    (진단 성격이라 D-NAO-13 optimizer='ours' 제한 예외 — account_diagnosis 보드와 동일 취급).

    "오늘/어제" 대신 실제 ISO 날짜(item["as_of"]/["prior_date"])를 쓴다 — anomaly_feed는
    run_daily의 as_of(확정치 어제)로 호출되므로 "오늘"이라 쓰면 실제로는 어제 데이터인데
    오늘로 오인될 수 있다(codex 지적, 정직 경계).
    """
    kind_label = "급증" if item["kind"] == "spike" else "급감"
    return {
        "proposal_type": _ANOMALY,
        "target_type": "campaign",
        "target_id": item["campaign_id"],
        "campaign_id": item["campaign_id"],
        "rationale": (
            f"[anomaly_feed] 소진 {kind_label} — {item['as_of']} {item['cost_today']}원 vs "
            f"{item['prior_date']} {item['cost_prior']}원(비율 {item['ratio']})."
        ),
        "expected_effect": "정보성 신호 — 실행 대상 아님.",
        "status": "pending",
    }


def _anomaly_freshness_proposal(freshness: dict) -> dict:
    """anomaly_feed 부분적재 신호(S3a codex 연기분 해소) → 계정 레벨 정보성 제안."""
    return {
        "proposal_type": _ANOMALY_FRESHNESS,
        "target_type": "account",
        "target_id": "",
        "campaign_id": "",
        "rationale": (
            f"[anomaly_feed] 부분적재 의심 — as_of={freshness['as_of']} 행수={freshness['as_of_count']} "
            f"(최근 평균 {freshness['baseline_avg']}행 대비 {freshness['ratio']}). {freshness['reason']}."
        ),
        "expected_effect": "정보성 신호 — 실행 대상 아님.",
        "status": "pending",
    }


def build(
    db: Session, diagnosis: dict, *, bid_sims: dict | None = None,
    growth_candidates: list[dict] | None = None, growth_sims: dict | None = None,
    budget_signals: list[dict] | None = None, pre_exhaustion_signals: list[dict] | None = None,
    forecast_data: dict | None = None, anomalies: dict | None = None, as_of: date,
) -> list[dict]:
    """진단 보드 + bid_simulator 결과 → 제안 후보 목록(아직 미저장, dict 리스트).

    bid_sims: {(target_type, target_id): simulate_bid() 반환값, ...} — harness가 미리
      계산해 전달(재조회 금지). 없는 (target_type,target_id)는 그 회차엔 제안 건너뜀.
    growth_candidates/growth_sims: growth_sweeper 갭 상위 후보(D-NAO-22-①, Phase2)와 그
      bid_simulator 결과 — proposal_pipeline.compute_growth_sims()가 그대로 전달. 진단
      보드(diagnosis["boards"])와 별도 소스라 형태가 달라(gap/economic_ceiling 등) 전용
      빌더(_growth_proposal)로 처리한다.
    budget_signals: budget_allocator 신호(D-NAO-22-③, Phase3) —
      proposal_pipeline.compute_budget_signals() 반환값. optimizer='ours'만 제안 생성.
    pre_exhaustion_signals: budget_allocator 사전경보 신호(F2b, D-NAO-26) —
      proposal_pipeline.compute_pre_exhaustion_signals() 반환값. budget_signals와 형태가
      달라(pred_cost/pred_gap, 이미 소진된 게 아니라 오늘 예측이 초과) 전용 빌더로 처리.
      정보성(anomaly와 동일 취급, 실행 대상 아님)이나 대상은 optimizer='ours'로 제한한다
      (budget_signals와 동일 성격 — 이 시스템이 관리하는 캠페인에 대한 예산 신호이므로).
    forecast_data: {(target_type, target_id): {"pred_clk","pred_cost","pred_conv_amt"}, ...}(F2b ⓐ,
      D-NAO-26) — proposal_pipeline.compute_forecast_evidence() 반환값. bid_up/bid_down/
      growth_bid_up rationale에 예측치를 병기만 한다(입찰 산식 D-NAO-19 불변). 없는 타겟은
      예측 없음(fallback/미가동)이라 병기하지 않는다(정직 경계). budget_up은 ("campaign",
      campaign_id) grain(P3, D-NAO-42-f) — rationale 병기뿐 아니라 _size_budget_up(§5-G)
      사이징에도 쓰인다(harness가 forecast_targets에 캠페인 target을 포함시켜야 채워짐).
    anomalies: anomaly_feed 신호(경량, Phase3) — {"freshness": {...}|None, "spend": [...]}.
      진단 성격(D-3, 사실 정리)이라 optimizer 무관 전 캠페인 대상(diagnosis 보드와 동일 취급).
    diagnosis["boards"]의 pause_candidates/resume_candidates(X1b T3, D-NAO-38)와 그 SHOPPING
      adgroup 대칭 확장 shopping_pause_candidates/shopping_resume_candidates(X1b-S S1,
      D-NAO-43)는 실행형 제안이라 ours 필터 적용(D-NAO-13) — 다른 진단 보드처럼 optimizer
      무관이 아니다.
    optimizer='ours' 캠페인만 대상(D-NAO-13) — 그 외(none/mop)는 진단엔 나와도 제안 없음.
    """
    bid_sims = bid_sims or {}
    growth_candidates = growth_candidates or []
    growth_sims = growth_sims or {}
    budget_signals = budget_signals or []
    pre_exhaustion_signals = pre_exhaustion_signals or []
    forecast_data = forecast_data or {}
    anomalies = anomalies or {}
    boards = diagnosis.get("boards") or {}
    ours = _ours_campaign_ids(db)
    # D-NAO-72-2: GAVE 사전 태깅용 보정계수(_tag_gave) — diagnosis(harness가 미리 계산해
    # 넘김) 재사용, 재조회 없음. compute_bid_sims의 correction_factor 추출과 동일 패턴.
    gave_correction_factor = Decimal(str(diagnosis["correction_factor"]["factor"]))
    # UI1(D-NAO-65): 캠페인별 loss 정책을 회차당 1쿼리로 로드해 스톱로스 SA에 주입한다(허브
    # 역할 — SA는 DB를 직접 읽지 않는다). stoploss_pause만 실림(NULL/leash=기본, .get→None).
    loss_policies = _loss_policy_map(db)
    labels = _TargetLabelCache(db)
    # B3(D-NAO-65) 카나리 게이트 — 함수 레벨 import(auto_operator ↔ proposal_writer 순환 회피,
    # account_diagnosis._step_down_bid 함수 레벨 import와 동일 관례). 기본 빈 집합=전면 미개방.
    from app.services.naver_ad.auto_operator import _ad_bid_canary

    # B2 GATE P2-2②: 레버 미연결 그룹(실효입찰 source='ad' — useGroupBidAmt=false 소재가
    # 그룹입찰을 우회) 판별. 미연결 그룹에 그룹입찰 bid_down/bid_up을 내면 지출엔 무효이면서
    # DL1 행동 창만 리셋(P2-1 침묵 그물 지연) + changes_today 슬롯 낭비 — adgroup grain 밴드
    # 보드(shopping_group_bep/shopping_group_growth) 제안을 생성 단계에서 건너뛴다(소재-레벨
    # 제어는 B3 몫, 키워드 보드 무관). 그룹입찰은 sim의 current_bid(NaverEntity.bid_amt 유래)
    # 로 얻고, 없으면 판정 불가 → 기존 동작 폴백(그룹입찰 유효 가정). 배치 1쿼리(GATE P3-2).
    band_group_bids: dict[str, int] = {}
    for band_board in ("shopping_group_bep", "shopping_group_growth"):
        for row in boards.get(band_board, []) or []:
            if row["campaign_id"] not in ours:
                continue
            band_sim = bid_sims.get(("adgroup", row["adgroup_id"]))
            band_current_bid = (band_sim or {}).get("current_bid")
            if band_current_bid:
                band_group_bids[row["adgroup_id"]] = band_current_bid
    band_effs = effective_bid.adgroup_effective_bids(db, band_group_bids)
    disconnected_adgroups = {aid for aid, e in band_effs.items() if e["source"] == "ad"}

    proposals: list[dict] = []

    for row in boards.get("bleeding_keywords", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["keyword_id"]
        sim = bid_sims.get(("keyword", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="keyword",
                           target_label=labels.get(cid), board_name="bleeding_keywords",
                           forecast=forecast_data.get(("keyword", target_id)))
        p = _tag_gave(p, "bleeding_keywords", row, gave_correction_factor)
        if p:
            proposals.append(p)

    for row in boards.get("starving_winners", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["keyword_id"]
        sim = bid_sims.get(("keyword", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="keyword",
                           target_label=labels.get(cid), board_name="starving_winners",
                           forecast=forecast_data.get(("keyword", target_id)))
        p = _tag_gave(p, "starving_winners", row, gave_correction_factor)
        if p:
            proposals.append(p)

    for row in boards.get("shopping_group_bep", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["adgroup_id"]
        if target_id in disconnected_adgroups:
            # B2 GATE P2-2② → B3 라우팅: 미연결 그룹의 그룹입찰 bid_down은 무효 —
            # 카나리면 실효 레버(max 소재) ad-레벨 제안, 비카나리는 hold(B2 억제).
            if _ad_bid_canary(cid):
                ad_p = _band_ad_route(row, band_effs, cid, "shopping_group_bep", bid_sims)
                ad_p = _tag_gave(ad_p, "shopping_group_bep", row, gave_correction_factor)
                if ad_p:
                    proposals.append(ad_p)
            continue
        sim = bid_sims.get(("adgroup", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="adgroup",
                           target_label=labels.get(cid), board_name="shopping_group_bep",
                           forecast=forecast_data.get(("adgroup", target_id)))
        p = _tag_gave(p, "shopping_group_bep", row, gave_correction_factor)
        if p:
            proposals.append(p)

    # shopping_group_growth(X1b-S S3, D-NAO-43 확장): shopping_group_bep(적자, down)의 반대 —
    # 수익 그룹 성장(bid_up). 배선은 shopping_group_bep과 완전 대칭(target_type="adgroup",
    # board_name만 다름) — _ALLOWED_DIRECTIONS가 "up"만 통과시킨다.
    for row in boards.get("shopping_group_growth", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["adgroup_id"]
        if target_id in disconnected_adgroups:
            # B2 GATE P2-2② → B3 라우팅(대칭): 미연결 그룹의 그룹입찰 bid_up 무효 —
            # 카나리면 실효 레버(max 소재) ad-레벨 up 제안, 비카나리는 hold.
            if _ad_bid_canary(cid):
                ad_p = _band_ad_route(row, band_effs, cid, "shopping_group_growth", bid_sims)
                ad_p = _tag_gave(ad_p, "shopping_group_growth", row, gave_correction_factor)
                if ad_p:
                    proposals.append(ad_p)
            continue
        sim = bid_sims.get(("adgroup", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="adgroup",
                           target_label=labels.get(cid), board_name="shopping_group_growth",
                           forecast=forecast_data.get(("adgroup", target_id)))
        p = _tag_gave(p, "shopping_group_growth", row, gave_correction_factor)
        if p:
            proposals.append(p)

    for row in boards.get("exclusion_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_negative_keyword_from_exclusion(row, cid))

    # pause_candidates/resume_candidates(X1b T3, D-NAO-38): D-NAO-13 ours 필터 동일 적용
    # (실행 대상 액션이라 진단 보드와 달리 optimizer 무관 전 캠페인 대상이 아님).
    # RL4(D-NAO-60): pause_candidates 보드는 이제 pause 대신 bid_down(고삐)을 만든다(키워드
    # 경로, 입찰 하한 도달 시에만 터미널 pause) — _stop_loss_proposal 참조.
    for row in boards.get("pause_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        # DL2(D-NAO-65): at-floor는 바닥 대기(None) — 안전 필터링(제안 생성 안 함).
        # UI1: 캠페인 정책 주입(stoploss_pause면 고삐 대신 즉시 하드 정지).
        p = _stop_loss_proposal(row, loss_policy=loss_policies.get(cid))
        p = _tag_gave(p, "pause_candidates", row, gave_correction_factor)
        if p is not None:
            proposals.append(p)

    for row in boards.get("resume_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_resume_proposal(row, labels.get(cid)))

    # shopping_pause_candidates/shopping_resume_candidates(X1b-S S1, D-NAO-43): 위
    # pause_candidates/resume_candidates의 SHOPPING adgroup 대칭 확장 — D-NAO-13 ours 필터
    # 동일 적용(실행형 제안).
    # RL4b(D-NAO-60): 쇼핑 경로도 이제 고삐(bid_down)로 확장 — 대상이 수동입찰인지는
    # `_adgroup_is_manual_bid`(라이브 재조회)로 여기서 미리 해석해 `_stop_loss_proposal`에
    # 넘긴다(그 함수는 API를 직접 부르지 않는 순수 함수로 유지 — 테스트 주입성).
    for row in boards.get("shopping_pause_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        manual_bid = _adgroup_is_manual_bid(row["adgroup_id"])
        # DL2(D-NAO-65): at-floor·레버 정상은 바닥 대기(None) — 안전 필터링.
        # B3 카나리: 레버 미연결 유닛은 소재-레벨 bid_down 고삐로 라우팅(예외② leash화).
        # UI1(D-NAO-65): 캠페인 정책 주입 — stoploss_pause면 수동입찰이어도 고삐 대신 즉시
        # 하드 정지(ML은 정책 무관 기존 예외① pause 그대로, _stop_loss_proposal 내부 분기).
        p = _stop_loss_proposal(row, target_type="adgroup", manual_bid=manual_bid,
                                ad_bid_canary=_ad_bid_canary(cid),
                                loss_policy=loss_policies.get(cid))
        p = _tag_gave(p, "shopping_pause_candidates", row, gave_correction_factor)
        if p is not None:
            proposals.append(p)

    for row in boards.get("shopping_resume_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_resume_proposal(row, labels.get(cid), target_type="adgroup"))

    # shopping_lever_resume_candidates(B4, D-NAO-65 item 2·4·5 — GATE P2-1/P2-3): 이미 pause된
    # 레버끊김(MO형) 그룹의 소재-레벨 재개 배선 — 예외②를 pause에서 leash로 되돌리는 마지막
    # 조각. 카나리 한정(B3 스코프 계승, _ad_bid_canary). **ML 사전 제외(GATE P2-3②)**: 부모
    # 그룹이 수동입찰(True)이 아니면(ML/판정불가 None) resume·bid_down_first 모두 미생성 —
    # 예외①(ML) 부류는 소재 bidAmt도 무시돼 소재 leash가 불가하므로 재개하면 통제 수단이 없다
    # (fail-closed). 재조회 API 콜은 카나리 소수 그룹뿐(비카나리는 위에서 이미 skip). action 분기:
    #  - bid_down_first: 소재 실효입찰 > 바닥(70) → 소재-레벨 bid_down으로 바닥까지 단계 하향
    #    (재개 준비 — 800원 그대로 재개 방지, item 5). Confirm 전용(target_type='ad'는 일/시간당
    #    레인이 이미 제외). 소재 at-floor(스텝 소실)면 _ad_bid_proposal None → skip.
    #  - resume: 소재 실효입찰 ≤ 바닥(준비 완료 — 바닥 재개, GATE P2-1) → resume 제안.
    #    단 **순서강제**(item 4): 같은 그룹에 ad bid_down pending이 살아있으면 하향이 아직
    #    확정 안 된 것이므로 resume 금지(하향 먼저). resume은 자동 레인 대상 아님 = 콘솔
    #    Confirm 전용.
    for row in boards.get("shopping_lever_resume_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        if loss_policies.get(cid) == "stoploss_pause":
            # UI1(D-NAO-65): 이 캠페인의 정책이 pause인데 재개(resume·bid_down_first) 제안은
            # 자기모순 — 정책이 유지하려는 정지를 스스로 되돌리게 된다. 제외(재개 미생성).
            continue
        if not _ad_bid_canary(cid):
            continue  # B4 = 카나리 한정(B3 스코프 계승)
        if _adgroup_is_manual_bid(row["adgroup_id"]) is not True:
            continue  # GATE P2-3②: ML/판정불가 — 소재 leash 불가, 재개 미제안(fail-closed)
        if row["action"] == "bid_down_first":
            ad_p = _ad_bid_proposal(
                ad_id=row["effective_ad_id"], adgroup_id=row["adgroup_id"], campaign_id=cid,
                current_ad_bid=row["effective_bid"], direction="down",
                board_name="shopping_lever_resume",
                note=(f" 재개 준비 — 소재 실효입찰 {row['effective_bid']}원 > 바닥(70원), "
                      f"바닥까지 단계 하향 후 재개(정지 {row['paused_at']})."),
            )
            if ad_p is not None:
                proposals.append(ad_p)
        elif row["action"] == "resume":
            # 순서강제: 같은 그룹에 ad bid_down pending 존재 시 resume 금지(하향 확정 전).
            pending_ad_down = db.query(NaverProposal.id).filter(
                NaverProposal.status == "pending",
                NaverProposal.target_type == "ad",
                NaverProposal.proposal_type == _BID_DOWN,
                NaverProposal.adgroup_id == row["adgroup_id"],
            ).first()
            if pending_ad_down is None:
                proposals.append(_lever_resume_proposal(row))

    # growth_sweeper(D-NAO-22-①): 후보는 이미 gap 내림차순(find_growth_candidates) — 상위부터
    # 채택해 GROWTH_PROPOSAL_CAP에서 멈춘다(탐색 예산 총액 캡의 count 기반 대체, growth_sweeper
    # 모듈 docstring 참조). skip된(sim 없음/direction 불일치) 후보는 캡을 소비하지 않는다.
    growth_created = 0
    for c in growth_candidates:
        if growth_created >= growth_sweeper.GROWTH_PROPOSAL_CAP:
            break
        cid = c["campaign_id"]
        if cid not in ours:
            continue
        sim = growth_sims.get(("keyword", c["keyword_id"]))
        p = _growth_proposal(c, sim, labels.get(cid), forecast=forecast_data.get(("keyword", c["keyword_id"])))
        if p:
            proposals.append(p)
            growth_created += 1

    # budget_allocator(D-NAO-22-③, Phase3): P3(D-NAO-42-f)부터 실행 개방 — 소진 캠페인 수
    # 자체가 자연히 적어(전체 캠페인의 극히 일부만 캡에 도달) 개수 캡은 불필요하나, 대신
    # 라운드 봉투(§5-E "회당 총 증가액≤10만원, 라운드 합계")를 여기서 분류한다.
    # budget_signals는 통상 total_gap 내림차순(find_budget_expansion_signals)이지만, 이
    # 순서에 의존하지 않는다(Fix 6, codex P2) — "total_gap"을 candidate dict에 threading해
    # _classify_budget_round_envelope가 스스로 재정렬하게 한다. ours 필터링만 하고 원본
    # signal 순서는 바꾸지 않는다(재정렬은 분류 함수 내부에서만 일어남).
    budget_up_deltas: list[tuple[int, dict]] = []
    for s in budget_signals:
        cid = s["campaign_id"]
        if cid not in ours:
            continue
        p = _budget_proposal(s, labels.get(cid), forecast=forecast_data.get(("campaign", cid)))
        p["total_gap"] = s["total_gap"]  # 분류용 임시 키(persist 직전에 제거 — NaverProposal 컬럼 아님)
        budget_up_deltas.append((p["target_budget"] - s["daily_budget"], p))
    _classify_budget_round_envelope(budget_up_deltas)
    for _, p in budget_up_deltas:
        del p["total_gap"]
    proposals.extend(p for _, p in budget_up_deltas)

    # budget_allocator 사전경보(F2b, D-NAO-26): 아직 소진 전이지만 오늘 예측이 예산을
    # 넘어설 것으로 보이는 캠페인 — 정보성(실행 대상 아님), budget_signals와 동일하게 ours로 제한.
    for s in pre_exhaustion_signals:
        cid = s["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_budget_pre_exhaustion_proposal(s, labels.get(cid)))

    # anomaly_feed(경량, Phase3): 진단 성격이라 diagnosis 보드와 동일하게 전 캠페인 대상
    # (optimizer 무관) — D-3 사실 정리, 실행 없음.
    for item in anomalies.get("spend") or []:
        proposals.append(_anomaly_spend_proposal(item))

    freshness = anomalies.get("freshness")
    if freshness and freshness.get("partial"):
        proposals.append(_anomaly_freshness_proposal(freshness))

    return proposals


def persist(db: Session, proposals: list[dict]) -> list[NaverProposal]:
    """제안 후보를 naver_proposals에 저장. dedup: 같은 (proposal_type, target_type,
    campaign_id, adgroup_id, target_id)로 status='pending'이 이미 있으면 skip(트랜잭션 내
    check-then-insert). campaign_id를 빼면 서로 다른 캠페인의 동일 검색어
    negative_keyword 제안이 충돌한다(codex 지적, 라이브검증 후속 — search_term은
    캠페인마다 같은 문자열이 반복될 수 있음). adgroup_id도 동일 원리(codex[P2] X1a T3):
    restricted-keywords는 광고그룹 단위 리소스라 같은 검색어·같은 캠페인이라도 adgroup이
    다르면 별개 실행 대상 — adgroup_id 없는 유형은 None(IS NULL) 비교라 기존 동작 불변.

    단일 08:00 크론 가정 — 동시성/재시도 하드닝(DB 유니크 인덱스)은 P3(계획서 명시 연기).

    D-NAO-72-2: `_tag_gave`가 붙인 `_gave_*` 임시키(GAVE 사전 정렬용, NaverProposal 컬럼
    아님)는 harness(proposal_pipeline._apply_gave_priority)가 소비 후 제거하는 게 정상
    경로지만, build()를 harness 없이 직접 호출하는 경로(단위테스트 등)도 있어 여기서 한 번
    더 방어적으로 제거한다(build()가 낸 dict를 persist가 그대로 못 믿는 건 아니지만, 없는
    컬럼으로 ORM 생성이 TypeError로 죽는 사고를 이 함수 하나가 막아준다).
    """
    saved: list[NaverProposal] = []
    for p in proposals:
        exists = db.query(NaverProposal).filter(
            NaverProposal.proposal_type == p["proposal_type"],
            NaverProposal.target_type == p["target_type"],
            NaverProposal.campaign_id == p["campaign_id"],
            NaverProposal.adgroup_id == p.get("adgroup_id"),  # None → IS NULL(기존 유형 불변)
            NaverProposal.target_id == p["target_id"],
            NaverProposal.status == "pending",
        ).first()
        if exists:
            continue
        clean = {k: v for k, v in p.items() if not k.startswith("_gave_")}
        obj = NaverProposal(**clean)
        db.add(obj)
        saved.append(obj)
    db.flush()
    return saved


def account_brief_singleton(db: Session, diagnosis: dict, as_of: date) -> NaverProposal:
    """계정레벨 일일 브리프 — 결정적 싱글톤(dedup·optimizer 필터 무관, 매일 1건 보장, codex #17).

    오늘(달력일) 이미 생성됐으면 새로 만들지 않고 기존 것을 반환(하루 1회 실행 가정 —
    harness가 여러 번 재실행해도 중복 생성 없음, as_of 자체가 아니라 '오늘 실행 여부'로
    판단해야 08:00 재시도/수동 재실행 시에도 안전).
    """
    # dedup 경계는 KST 달력일이어야 한다. created_at은 server_default(UTC)라, date.today()
    # (서버 로컬=UTC)로 today_start를 잡으면 08:00 KST 실행분(=전날 23:00 UTC)과 09:00 KST
    # 이후 재실행분(=당일 UTC)이 서로 다른 UTC일로 갈려 같은 KST일에 중복 브리프가 생긴다
    # (2026-07-13 실측: id 502·544 중복, catch-up/수동 재실행 시 재현). KST 자정을 UTC로
    # 환산해 비교한다([[sqlite-server-default-now-is-utc]], learning_loops와 동일 kst_today 채택).
    today_start = datetime.combine(kst_today(), datetime.min.time()) - timedelta(hours=9)
    existing = db.query(NaverProposal).filter(
        NaverProposal.proposal_type == _ACCOUNT_BRIEF,
        NaverProposal.created_at >= today_start,
    ).first()
    if existing:
        return existing

    boards = diagnosis.get("boards") or {}
    exp = boards.get("expansion_bucket") or {}
    triage = boards.get("keyword_triage") or {}
    n_bleeding = len(boards.get("bleeding_keywords") or [])
    n_starving = len(boards.get("starving_winners") or [])

    rationale = (
        f"[일일 계정 브리프] as_of={as_of.isoformat()}. "
        f"출혈 키워드 {n_bleeding}건, 굶는승자 {n_starving}건, "
        f"확장버킷 비용비중={exp.get('cost_share')}(ROAS_corrected={exp.get('roas_corrected')}), "
        f"키워드 위생: 판정가능={triage.get('judgeable')}/육성후보={triage.get('growth_candidate')}/"
        f"정리대상={triage.get('dead')}."
    )
    obj = NaverProposal(
        proposal_type=_ACCOUNT_BRIEF, target_type="account", target_id="",
        campaign_id="", rationale=rationale,
        expected_effect="정보성 요약 — 실행 대상 아님.", status="pending",
    )
    db.add(obj)
    db.flush()
    return obj
