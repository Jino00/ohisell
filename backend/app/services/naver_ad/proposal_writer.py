# proposal_writer.py — proposal_writer SA (P2-S3 T3, D-3/D-S3-b)
# 역할(SA): 진단 보드 + bid_simulator 결과 → 제안(naver_proposals) 생성·저장. 읽기전용 제안만
#   (광고 API 쓰기 없음, D-3 관찰 모드). optimizer='ours' 캠페인만 대상(D-NAO-13).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 diagnosis·bid_sims를 넘긴다.
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import campaign_target_resolver, growth_sweeper
# 라이브[P1] DOA 수정: 생성 단계 스텝 클램프의 스텝 상수는 실행 가드레일과 단일 진실 소스
# (하드코딩 0.15 중복 금지 — 드리프트 방지). guardrail_gate는 이 모듈을 import하지 않아 순환 없음.
from app.services.naver_ad.guardrail_gate import _MAX_CHANGE_PCT
from app.services.naver_ad.trigger_watch import PROPOSAL_TYPE_CPC, PROPOSAL_TYPE_PACING
from app.utils.kst import kst_today

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
_BUDGET_DOWN = "budget_down"  # D-NAO-42-f, budget_up과 동형(감액은 자유 — guardrail_gate 참조)

ALL_PROPOSAL_TYPES: frozenset[str] = frozenset({
    _BID_UP, _BID_DOWN, _GROWTH_BID_UP, _NEGATIVE, _PAUSE, _RESUME,
    _BUDGET_UP, _BUDGET_DOWN, _BUDGET_PRE_EXHAUSTION,
    _ANOMALY, _ANOMALY_FRESHNESS, _ACCOUNT_BRIEF, PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC,
    _WISDOM_PROMOTED,  # D-NAO-54 P3(정보성) — INFORMATIONAL_PROPOSAL_TYPES <= ALL 불변 유지
    PARAM_CHANGE,  # D-NAO-54 P4(결정 전용) — 정보성도 실행형도 아님(라우터 DECISION_ONLY 분기)
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


class _TargetLabelCache:
    """캠페인당 target_roas 근거(override/account_default)를 회차 내 1회만 조회(N+1 방지)."""

    def __init__(self, db: Session):
        self._db = db
        self._cache: dict[str, dict] = {}

    def get(self, campaign_id: str) -> dict:
        if campaign_id not in self._cache:
            self._cache[campaign_id] = campaign_target_resolver.resolve_target_roas(self._db, campaign_id)
        return self._cache[campaign_id]


def _forecast_evidence_suffix(forecast: dict | None) -> str:
    """예측치(F2b ⓐ, D-NAO-26)가 있으면 rationale에 병기할 문구 — 없으면 빈 문자열(정직 경계,
    fallback/미가동 타겟에 억지로 예측을 만들지 않음). 입찰 산식(D-NAO-19)에는 관여하지 않는다."""
    if forecast is None:
        return ""
    return (
        f" 예측(오늘): clk={forecast['pred_clk']}, cost={forecast['pred_cost']}원, "
        f"conv_amt={forecast['pred_conv_amt']}원."
    )


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
            stepped = Decimal(current_bid) * (Decimal(1) - _MAX_CHANGE_PCT)
            # 10원 올림(ceil) — Decimal의 //는 버림(truncation)이라 음수 트릭 불가, ROUND_CEILING 명시
            step_floor = int((stepped / 10).to_integral_value(rounding=ROUND_CEILING)) * 10
            step_floor = max(step_floor, 70)  # 절대 하한(_MIN_BID 규약, guardrail_gate와 동일)
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
        + _forecast_evidence_suffix(forecast)
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
        + _forecast_evidence_suffix(forecast)
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


def _pause_proposal(row: dict, *, target_type: str = "keyword") -> dict:
    """pause_candidates/shopping_pause_candidates 보드 1행(account_diagnosis) → pause 제안
    (X1b T3, D-NAO-38 / 쇼핑 adgroup 확장은 X1b-S S1, D-NAO-43).

    target_type='keyword'(기본값, 하위호환)는 WEB_SITE 키워드(pause_candidates 보드,
    target_id=row["keyword_id"]) — target_type='adgroup'은 SHOPPING 광고그룹
    (shopping_pause_candidates 보드, target_id=row["adgroup_id"]). 두 보드는 row 스키마가
    동형(campaign_id/cost/stop_loss_amount 등 공통, 대상id 키만 다름)이라 한 빌더가 함께
    처리한다. adgroup_id 컬럼(NaverProposal, 실쓰기 대상 광고그룹 정보용)은 keyword
    대상에서만 채운다 — adgroup 대상은 target_id 자체가 이미 그 값이라 중복 저장하지 않는다
    (shopping_group_bep의 _bid_proposal이 adgroup 대상에 adgroup_id를 안 채우는 것과 동일 관례).

    D-NAO-20 스톱로스 절대액(현재입찰×LOW_CLICK_THRESHOLD) 도달 — 무전환 지출 중단이
    목적이라 target_roas 근거 병기가 불필요(스톱로스는 손익 목표와 무관한 절대액 안전핀)."""
    target_id = row["keyword_id"] if target_type == "keyword" else row["adgroup_id"]
    board_name = "pause_candidates" if target_type == "keyword" else "shopping_pause_candidates"
    return {
        "proposal_type": _PAUSE,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"] if target_type == "keyword" else None,
        "target_lock": True,
        "rationale": (
            f"[{board_name}] 무전환 누적비용 {row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원"
            f"(현재입찰={row['current_bid']}원, D-NAO-20) clk={row.get('clk')}."
        ),
        "expected_effect": "무전환 지출 중단 — 추가 비용 발생 차단(D-NAO-16 정지).",
        "status": "pending",
    }


def _resume_proposal(row: dict, target_label: dict, *, target_type: str = "keyword") -> dict:
    """resume_candidates/shopping_resume_candidates 보드 1행 → resume 제안(X1b T3, D-NAO-38
    / 쇼핑 adgroup 확장은 X1b-S S1, D-NAO-43). target_type 규약은 _pause_proposal과 동일
    (기본값 'keyword'=하위호환, 'adgroup'=쇼핑 광고그룹).

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
        + _forecast_evidence_suffix(forecast)
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
    labels = _TargetLabelCache(db)

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
        if p:
            proposals.append(p)

    for row in boards.get("shopping_group_bep", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["adgroup_id"]
        sim = bid_sims.get(("adgroup", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="adgroup",
                           target_label=labels.get(cid), board_name="shopping_group_bep",
                           forecast=forecast_data.get(("adgroup", target_id)))
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
        sim = bid_sims.get(("adgroup", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="adgroup",
                           target_label=labels.get(cid), board_name="shopping_group_growth",
                           forecast=forecast_data.get(("adgroup", target_id)))
        if p:
            proposals.append(p)

    for row in boards.get("exclusion_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_negative_keyword_from_exclusion(row, cid))

    # pause_candidates/resume_candidates(X1b T3, D-NAO-38): D-NAO-13 ours 필터 동일 적용
    # (실행 대상 액션이라 진단 보드와 달리 optimizer 무관 전 캠페인 대상이 아님).
    for row in boards.get("pause_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_pause_proposal(row))

    for row in boards.get("resume_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_resume_proposal(row, labels.get(cid)))

    # shopping_pause_candidates/shopping_resume_candidates(X1b-S S1, D-NAO-43): 위
    # pause_candidates/resume_candidates의 SHOPPING adgroup 대칭 확장 — D-NAO-13 ours 필터
    # 동일 적용(실행형 제안).
    for row in boards.get("shopping_pause_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_pause_proposal(row, target_type="adgroup"))

    for row in boards.get("shopping_resume_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_resume_proposal(row, labels.get(cid), target_type="adgroup"))

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
        obj = NaverProposal(**p)
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
