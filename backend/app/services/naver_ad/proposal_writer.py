# proposal_writer.py — proposal_writer SA (P2-S3 T3, D-3/D-S3-b)
# 역할(SA): 진단 보드 + bid_simulator 결과 → 제안(naver_proposals) 생성·저장. 읽기전용 제안만
#   (광고 API 쓰기 없음, D-3 관찰 모드). optimizer='ours' 캠페인만 대상(D-NAO-13).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 diagnosis·bid_sims를 넘긴다.
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import campaign_target_resolver, growth_sweeper
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

# X1a T6(D-NAO-37): 정보성 제안 유형 5종 — 실행 대상 자체가 없는 제안(naver_execution_harness의
# _ACTION_BY_PROPOSAL_TYPE에 매핑이 없는 유형과 의미상 같지만, 그 매핑에는 budget_up처럼 아직
# OPEN_ACTIONS에 없을 뿐인 "실행형인데 미개방" 유형도 섞여 있어 "매핑에 없음"을 파생 조건으로
# 쓰면 안전하지 않다(향후 예산 개방 시점에 조용히 브리핑에서 빠져버릴 위험) — 반드시 이 명시
# 목록을 단일 진실로 유지한다. proposal_pipeline(차등 TTL)·expert_briefing_builder(브리핑
# 접기)가 이 상수를 공유 import한다(순환 import 없음 — 두 모듈 다 이 모듈을 참조할 뿐, 이
# 모듈은 어느 쪽도 import하지 않는다).
INFORMATIONAL_PROPOSAL_TYPES: frozenset[str] = frozenset({
    _ANOMALY, _ANOMALY_FRESHNESS, _ACCOUNT_BRIEF, PROPOSAL_TYPE_PACING, PROPOSAL_TYPE_CPC,
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
    "growth_sweeper": {"up"},
}


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

    rationale = (
        f"[{board_name}] 보정ROAS={row.get('roas_corrected')} cost={row.get('cost')}원 "
        f"clk={row.get('clk')} — 시뮬 근거={sim['basis']}, 추천입찰={sim['recommended_bid']}원, "
        f"target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + "."
        + _forecast_evidence_suffix(forecast)
    )
    return {
        "proposal_type": proposal_type,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "expected_effect": sim["expected_effect_text"],
        "status": "pending",
        # X1b T3(D-NAO-38 갭①): 추천입찰가를 구조화 컬럼에 저장 — 실행자는 이 필드만 읽는다
        # (rationale 텍스트 파싱 금지). negative_keyword 격상 시엔 입찰 목표가 없어 None.
        "target_bid": sim["recommended_bid"] if proposal_type != _NEGATIVE else None,
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


def _pause_proposal(row: dict) -> dict:
    """pause_candidates 보드 1행(account_diagnosis) → pause 제안(X1b T3, D-NAO-38).

    D-NAO-20 스톱로스 절대액(현재입찰×LOW_CLICK_THRESHOLD) 도달 — 무전환 지출 중단이
    목적이라 target_roas 근거 병기가 불필요(스톱로스는 손익 목표와 무관한 절대액 안전핀)."""
    return {
        "proposal_type": _PAUSE,
        "target_type": "keyword",
        "target_id": row["keyword_id"],
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"],
        "target_lock": True,
        "rationale": (
            f"[pause_candidates] 무전환 누적비용 {row['cost']}원 ≥ 스톱로스 {row['stop_loss_amount']}원"
            f"(현재입찰={row['current_bid']}원, D-NAO-20) clk={row.get('clk')}."
        ),
        "expected_effect": "무전환 지출 중단 — 추가 비용 발생 차단(D-NAO-16 정지).",
        "status": "pending",
    }


def _resume_proposal(row: dict, target_label: dict) -> dict:
    """resume_candidates 보드 1행 → resume 제안(X1b T3, D-NAO-38).

    정지 직전 창의 보정ROAS가 현재 목표(target_roas) 이상으로 회복 — D-NAO-16 "정지 사유
    해소"(BEP 개선) 신호. 우리 시스템이 정지시킨 키워드만 대상(account_diagnosis.
    resume_candidates가 proposal_id 없는 수동 정지를 이미 제외)."""
    return {
        "proposal_type": _RESUME,
        "target_type": "keyword",
        "target_id": row["keyword_id"],
        "campaign_id": row["campaign_id"],
        "adgroup_id": row["adgroup_id"],
        "target_lock": False,
        "rationale": (
            f"[resume_candidates] 정지({row['paused_at']}) 직전 보정ROAS {row['roas_at_pause']}"
            f"(현재 목표={target_label.get('target_roas')}, 근거={target_label.get('source')}) "
            f"— BEP 개선 신호(D-NAO-16)."
        ),
        "expected_effect": "정지 해제 — 재노출 재개.",
        "status": "pending",
    }


def _budget_proposal(signal: dict, target_label: dict) -> dict:
    """budget_allocator 신호(D-NAO-22-③) → budget_up 제안. 실행은 영구 Confirm(D-NAO-5 —
    예산 상한 인상은 신규 캠페인·재구축과 동급 게이트). marginal ROAS 인과추정은 하지
    않는다(D-S3-c 연기 사유 유지, 추정 금지) — "예산 캡이 이미 소진됐고 그 안에 이익보장
    잔존 볼륨이 실측으로 확인됐다"는 사실만 제공."""
    rationale = (
        f"[budget_allocator] 일예산 {signal['daily_budget']}원 소진(누적 {signal['cost']}원, "
        f"{signal['hour']}시 기준) — 동일 캠페인 내 이익보장 성장후보 {signal['growth_candidate_count']}건 "
        f"존재(합산 입찰여력 gap={signal['total_gap']}원). target_roas 근거={target_label['source']}"
        + (f"({target_label['target_roas']})" if target_label.get("target_roas") is not None else "")
        + "."
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
      예측 없음(fallback/미가동)이라 병기하지 않는다(정직 경계).
    anomalies: anomaly_feed 신호(경량, Phase3) — {"freshness": {...}|None, "spend": [...]}.
      진단 성격(D-3, 사실 정리)이라 optimizer 무관 전 캠페인 대상(diagnosis 보드와 동일 취급).
    diagnosis["boards"]의 pause_candidates/resume_candidates(X1b T3, D-NAO-38)는 실행형
      제안이라 ours 필터 적용(D-NAO-13) — 다른 진단 보드처럼 optimizer 무관이 아니다.
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

    # budget_allocator(D-NAO-22-③, Phase3): 예산 상한 인상은 D-NAO-5 영구 Confirm 게이트 —
    # 소진 캠페인 수 자체가 자연히 적어(전체 캠페인의 극히 일부만 캡에 도달) 별도 캡 불필요.
    for s in budget_signals:
        cid = s["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_budget_proposal(s, labels.get(cid)))

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
