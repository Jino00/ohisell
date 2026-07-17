# wisdom_judge.py — judge_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 숙성한(pending & TTL 14일 경과 or occurrences≥3) 후보를 독립 LLM 판사에게 보내
#   promote/reject 판정을 받는다(★자기평가 금지 — 후보를 만든 규칙과 별개 모델이 판단). 판사는
#   ①재사용 가능한 판단원칙인가 ②환경↔결과 연결이 데이터로 뒷받침되나 ③기존 가드레일/정책과
#   중복 아닌가 ④good/bad 승률·표본이 원칙을 뒷받침하나(분모 없이·모순 방향 동시 승격 금지, 리뷰
#   P2-2)를 기준으로 verdict를 낸다. 후보는 조건 시그니처라 good_count/bad_count/win_rate를 함께
#   프롬프트에 넘긴다. 회당 상한 5건(LLM 비용·시간). LLM 실패는 해당 후보 skip(fail-open —
#   pending 유지, 내일 재시도). 읽기/쓰기 모두 wisdom_candidates 테이블만.
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import OpsWisdomCandidate
from app.services.naver_ad.expert_llm import _invoke_claude
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# expert_desk/reflection과 동일 관례(독립 LLM, 배치 1콜).
_JUDGE_MODEL = "opus"
_JUDGE_TIMEOUT_S = 120

_TTL_DAYS = 14           # first_seen_at부터 이만큼 지나면 숙성(단발이 아니라 유지된 패턴)
_OCCURRENCE_GATE = 3     # 또는 유사 패턴 3회 재등장
_MAX_PER_RUN = 5         # 회당 판사 상한(나머지는 익일)

_SYSTEM = (
    "당신은 오하이 네이버 SA 광고 운영의 '지혜 승격 판사'입니다. 아래 후보는 우리가 특정 환경"
    "조건(요일계층·계절·아이폰 출시창)에서 같은 액션을 실행/차단했을 때의 결과 성적입니다. "
    "good_count/bad_count는 그 조건에서 결과가 target 이상(good)/미만(bad)이었던 관찰 수이고 "
    "win_rate=good/(good+bad)입니다. 다음 4가지를 기준으로 promote/reject를 판정하세요: "
    "①특정 날짜·단발 사건이 아니라 앞으로도 재사용 가능한 '판단원칙'인가, "
    "②환경조건↔결과의 연결이 이 후보의 데이터로 뒷받침되는가, "
    "③기존 가드레일/예산·입찰 정책과 단순 중복이 아닌가, "
    "④승률·표본이 원칙을 뒷받침하는가 — good/bad 관찰 수(분모)가 없으면 승격하지 말고, "
    "good과 bad가 모순되게 팽팽하면 어느 방향으로도 승격하지 마세요. "
    "반드시 아래 JSON만 응답하세요(다른 텍스트 없이): "
    '{"verdict": "promote" 또는 "reject", "principle": 승격 시 재사용 판단원칙 한 문장(reject면 빈 문자열), '
    '"rationale": 판정 근거(필수, 한국어)}. '
    "근거 없는 승격을 하지 말고, 표본이 얇거나 인과가 불명하면 reject하세요."
)

_SCHEMA = {"verdict": "promote|reject", "principle": "string", "rationale": "string"}


def _is_ripe(cand: OpsWisdomCandidate, now: datetime) -> bool:
    """숙성 게이트 — TTL 14일 경과 or occurrences≥3."""
    if (cand.occurrences or 0) >= _OCCURRENCE_GATE:
        return True
    return cand.first_seen_at is not None and cand.first_seen_at <= now - timedelta(days=_TTL_DAYS)


def _prompt(cand: OpsWisdomCandidate, now: datetime) -> str:
    env = json.loads(cand.env_bucket_json) if cand.env_bucket_json else {}
    days_since = (now - cand.first_seen_at).days if cand.first_seen_at else None
    good = cand.good_count or 0
    bad = cand.bad_count or 0
    total = good + bad
    win_rate = round(good / total, 3) if total else None
    view = {
        "campaign_id": cand.campaign_id, "action": cand.action, "env_bucket": env,
        "observation": cand.observation, "occurrences": cand.occurrences,
        "good_count": good, "bad_count": bad, "win_rate": win_rate,
        "days_since_first_seen": days_since,
    }
    return (
        "아래는 지혜 승격 후보 1건입니다(JSON). good_count/bad_count는 이 조건에서 결과가 "
        "target 이상(good)/미만(bad)이었던 관찰 수, win_rate=good/(good+bad)입니다. "
        "이 안의 정보만 근거로 판정하세요.\n\n"
        f"{json.dumps(view, ensure_ascii=False, default=str)}"
    )


def judge_ripe_candidates(db: Session, *, now: datetime | None = None, invoke=_invoke_claude) -> dict:
    """숙성 후보를 판사에게 보내 promote/reject 반영(회당 최대 5건).

    invoke는 LLM 주입경계(기본=expert_llm._invoke_claude, 테스트=가짜) — diary_reflection 전례.
    LLM 실패/파싱 실패는 해당 후보 skip(pending 유지 → 내일 재시도).
    """
    now = now or kst_now()
    pending = (
        db.query(OpsWisdomCandidate)
        .filter(OpsWisdomCandidate.status == "pending")
        .order_by(OpsWisdomCandidate.occurrences.desc(), OpsWisdomCandidate.first_seen_at.asc())
        .all()
    )
    ripe = [c for c in pending if _is_ripe(c, now)][:_MAX_PER_RUN]

    totals = {"ripe": len(ripe), "promoted": 0, "rejected": 0, "skipped_llm": 0, "errors": 0}
    for cand in ripe:
        try:
            res = invoke(_prompt(cand, now), system=_SYSTEM, schema=_SCHEMA,
                         model=_JUDGE_MODEL, timeout=_JUDGE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — fail-open: 내일 재시도(pending 유지)
            log.warning("wisdom_judge: LLM 호출 실패(skip, pending 유지): sig=%s: %s", cand.signature, e)
            totals["skipped_llm"] += 1
            continue

        parsed = (res or {}).get("json")
        verdict = (parsed or {}).get("verdict")
        rationale = (parsed or {}).get("rationale")
        # rationale 필수 + verdict 유효값만 반영. 그 외(파싱 실패·불충분)는 skip(pending 유지).
        if verdict not in ("promote", "reject") or not rationale:
            log.warning("wisdom_judge: 판사 응답 불충분(skip, pending 유지): sig=%s", cand.signature)
            totals["skipped_llm"] += 1
            continue

        try:
            cand.judge_verdict_json = json.dumps(parsed, ensure_ascii=False)
            cand.status = "promoted" if verdict == "promote" else "rejected"
            db.commit()
            totals["promoted" if verdict == "promote" else "rejected"] += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_judge: 판정 반영 실패(sig=%s): %s", cand.signature, e)
    return totals
