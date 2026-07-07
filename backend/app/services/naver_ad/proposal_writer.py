# proposal_writer.py — proposal_writer SA (P2-S3 T3, D-3/D-S3-b)
# 역할(SA): 진단 보드 + bid_simulator 결과 → 제안(naver_proposals) 생성·저장. 읽기전용 제안만
#   (광고 API 쓰기 없음, D-3 관찰 모드). optimizer='ours' 캠페인만 대상(D-NAO-13).
#   SA간 직접 호출 금지(원칙18) — harness(proposal_pipeline, T5)가 diagnosis·bid_sims를 넘긴다.
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProposal
from app.services.naver_ad import campaign_target_resolver, growth_sweeper

_NEGATIVE = "negative_keyword"
_GROWTH_BID_UP = "growth_bid_up"

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


def _bid_proposal(
    row: dict, sim: dict | None, campaign_id: str, target_id: str, *,
    target_type: str, target_label: dict, board_name: str,
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
    )
    return {
        "proposal_type": proposal_type,
        "target_type": target_type,
        "target_id": target_id,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "expected_effect": sim["expected_effect_text"],
        "status": "pending",
    }


def _growth_proposal(candidate: dict, sim: dict | None, target_label: dict) -> dict | None:
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
    )
    return {
        "proposal_type": _GROWTH_BID_UP,
        "target_type": "keyword",
        "target_id": candidate["keyword_id"],
        "campaign_id": candidate["campaign_id"],
        "rationale": rationale,
        "expected_effect": sim["expected_effect_text"],
        "status": "pending",
    }


def _negative_keyword_from_exclusion(row: dict, campaign_id: str) -> dict:
    """확장버킷 비용상위 검색어 → 제외후보 제안(전환귀속 없음 — '비용/볼륨 후보' 정직 라벨,
    exclusion_candidates 보드 자체가 전환 데이터 없이 비용순만 제공하므로 그 경계를 그대로 전달)."""
    return {
        "proposal_type": _NEGATIVE,
        "target_type": "search_term",
        "target_id": row["search_term"],
        "campaign_id": campaign_id,
        "rationale": (
            f"[exclusion_candidates] 비용/볼륨 후보(전환귀속 데이터 없음, source={row.get('source')}) "
            f"cost={row.get('cost')}원, clk={row.get('clk')}, imp={row.get('imp')}."
        ),
        "expected_effect": "전환 데이터 없음 — 제외 시 비용 절감 추정만 가능(정밀 예측 불가).",
        "status": "pending",
    }


def build(
    db: Session, diagnosis: dict, *, bid_sims: dict | None = None,
    growth_candidates: list[dict] | None = None, growth_sims: dict | None = None, as_of: date,
) -> list[dict]:
    """진단 보드 + bid_simulator 결과 → 제안 후보 목록(아직 미저장, dict 리스트).

    bid_sims: {(target_type, target_id): simulate_bid() 반환값, ...} — harness가 미리
      계산해 전달(재조회 금지). 없는 (target_type,target_id)는 그 회차엔 제안 건너뜀.
    growth_candidates/growth_sims: growth_sweeper 갭 상위 후보(D-NAO-22-①, Phase2)와 그
      bid_simulator 결과 — proposal_pipeline.compute_growth_sims()가 그대로 전달. 진단
      보드(diagnosis["boards"])와 별도 소스라 형태가 달라(gap/economic_ceiling 등) 전용
      빌더(_growth_proposal)로 처리한다.
    optimizer='ours' 캠페인만 대상(D-NAO-13) — 그 외(none/mop)는 진단엔 나와도 제안 없음.
    """
    bid_sims = bid_sims or {}
    growth_candidates = growth_candidates or []
    growth_sims = growth_sims or {}
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
                           target_label=labels.get(cid), board_name="bleeding_keywords")
        if p:
            proposals.append(p)

    for row in boards.get("starving_winners", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["keyword_id"]
        sim = bid_sims.get(("keyword", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="keyword",
                           target_label=labels.get(cid), board_name="starving_winners")
        if p:
            proposals.append(p)

    for row in boards.get("shopping_group_bep", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        target_id = row["adgroup_id"]
        sim = bid_sims.get(("adgroup", target_id))
        p = _bid_proposal(row, sim, cid, target_id, target_type="adgroup",
                           target_label=labels.get(cid), board_name="shopping_group_bep")
        if p:
            proposals.append(p)

    for row in boards.get("exclusion_candidates", []) or []:
        cid = row["campaign_id"]
        if cid not in ours:
            continue
        proposals.append(_negative_keyword_from_exclusion(row, cid))

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
        p = _growth_proposal(c, sim, labels.get(cid))
        if p:
            proposals.append(p)
            growth_created += 1

    return proposals


def persist(db: Session, proposals: list[dict]) -> list[NaverProposal]:
    """제안 후보를 naver_proposals에 저장. dedup: 같은 (proposal_type, target_type,
    campaign_id, target_id)로 status='pending'이 이미 있으면 skip(트랜잭션 내
    check-then-insert). campaign_id를 빼면 서로 다른 캠페인의 동일 검색어
    negative_keyword 제안이 충돌한다(codex 지적, 라이브검증 후속 — search_term은
    캠페인마다 같은 문자열이 반복될 수 있음).

    단일 08:00 크론 가정 — 동시성/재시도 하드닝(DB 유니크 인덱스)은 P3(계획서 명시 연기).
    """
    saved: list[NaverProposal] = []
    for p in proposals:
        exists = db.query(NaverProposal).filter(
            NaverProposal.proposal_type == p["proposal_type"],
            NaverProposal.target_type == p["target_type"],
            NaverProposal.campaign_id == p["campaign_id"],
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
    today_start = datetime.combine(date.today(), datetime.min.time())
    existing = db.query(NaverProposal).filter(
        NaverProposal.proposal_type == "account_brief",
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
        proposal_type="account_brief", target_type="account", target_id="",
        campaign_id="", rationale=rationale,
        expected_effect="정보성 요약 — 실행 대상 아님.", status="pending",
    )
    db.add(obj)
    db.flush()
    return obj
