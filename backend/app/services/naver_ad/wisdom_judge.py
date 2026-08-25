# wisdom_judge.py — judge_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 숙성한(pending & TTL 14일 경과 or occurrences≥3) 후보를 독립 LLM 판사에게 보내
#   promote/reject 판정을 받는다(★자기평가 금지 — 후보를 만든 규칙과 별개 모델이 판단). 판사는
#   ①재사용 가능한 판단원칙인가 ②환경↔결과 연결이 데이터로 뒷받침되나 ③기존 가드레일/정책과
#   중복 아닌가 ④good/bad 승률·표본이 원칙을 뒷받침하나(분모 없이·모순 방향 동시 승격 금지, 리뷰
#   P2-2)를 기준으로 verdict를 낸다. 후보는 조건 시그니처라 good_count/bad_count/win_rate를 함께
#   프롬프트에 넘긴다. 회당 상한 5건(LLM 비용·시간). LLM 실패는 해당 후보 skip(fail-open —
#   pending 유지, 내일 재시도). 읽기/쓰기 모두 wisdom_candidates 테이블만.
#
# ★D-NAO-248(2026-08-25, §1) — 판사 재료에 두 가지를 추가했다(재료만, 판정 강제 아님):
#   ①sibling_buckets: 같은 action의 다른 후보(다른 환경/유형/캠페인_type)들의 n·good/bad·
#     win_rate. 「이 패턴이 다른 조건에서도 재현되나, 아니면 이 조건에서만 특이하게 좋나」를
#     판사가 직접 대조하게 한다. ②by_campaign: grain='global' 후보면 캠페인별 분해(전역 시그니처가
#     합친 표본 안의 이질성을 판사에게 병기 — 부록 Q2 "항상 표본은 합치되 이질성은 판사에게 보인다").
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import OpsWisdomCandidate
from app.services.naver_ad import guardrail_params
from app.services.naver_ad.expert_llm import _invoke_claude
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# expert_desk/reflection과 동일 관례(독립 LLM, 배치 1콜).
_JUDGE_MODEL = "opus"
_JUDGE_TIMEOUT_S = 120

_TTL_DAYS = 14           # first_seen_at부터 이만큼 지나면 숙성(단발이 아니라 유지된 패턴)
_OCCURRENCE_GATE = 3     # 또는 유사 패턴 3회 재등장
_MAX_PER_RUN = 5         # 회당 판사 상한(나머지는 익일)
_MAX_SIBLINGS = 8        # 같은 액션의 형제 버킷 재료 상한(프롬프트 비대화 방지, occurrences desc)

# ★D-NAO-248 §4-B(B7-2) — param을 자유 텍스트가 아니라 SPECS 화이트리스트 enum으로 좁힌다.
#   판사가 무엇을 고를 수 있는지(키·이름·근거·범위)를 프롬프트에 그대로 실어 보낸다 — 코드
#   쪽 클램프(wisdom_apply._classify_param_suggestion)가 최종 판정이지만, 애초에 판사가
#   화이트리스트 밖을 고를 이유를 없앤다.
_PARAM_KEYS_DESC = "\n".join(
    f"  - {key}: {spec.label} — 허용범위 {spec.lo}~{spec.hi}, direction={spec.direction}. {spec.why}"
    for key, spec in guardrail_params.SPECS.items()
)

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
    "참고 자료로 sibling_buckets(같은 액션의 다른 환경/유형 후보들의 승률)와 by_campaign"
    "(이 후보가 합친 캠페인들의 개별 분해, 전역 후보에만 존재)가 주어지면, 이 조건에서만 "
    "특이하게 나온 값인지 여러 조건에서 재현되는지, 그리고 합쳐진 캠페인들이 실제로 같은 "
    "방향을 가리키는지 참고하되 — 이 재료가 promote/reject를 자동으로 정하지는 않습니다. "
    "반드시 아래 JSON만 응답하세요(다른 텍스트 없이): "
    '{"verdict": "promote" 또는 "reject", "principle": 승격 시 재사용 판단원칙 한 문장(reject면 빈 문자열), '
    '"rationale": 판정 근거(필수, 한국어), '
    '"param_suggestion": 선택 필드}. '
    "★param_suggestion은 **이 지혜가 아래 화이트리스트 파라미터 중 하나의 변경을 구체적으로 "
    "함의할 때만** 채우고, 아니면 아예 생략하세요(대부분 생략이 정상 — 억지로 만들지 마세요). "
    f"화이트리스트({len(guardrail_params.SPECS)}종, 이 안에서만 고를 수 있습니다):\n{_PARAM_KEYS_DESC}\n"
    'param_suggestion을 채울 때 형식: {"param": 위 화이트리스트 키 중 정확히 하나(자유 텍스트 '
    '금지 — 목록에 없는 값은 코드가 자동으로 미매핑 처리해 반영되지 않습니다), '
    '"scope": "unconditional" 또는 "conditional" 중 하나 — 이 지혜가 **항상**(요일·계절 등 '
    '조건과 무관하게) 적용돼야 한다고 판단하면 "unconditional", 특정 조건(주말·계절·출시창 '
    '등)에서만 유효하다고 판단하면 "conditional"입니다. 판단이 서지 않으면 "conditional"을 '
    '쓰세요(우기지 마세요 — scope가 unconditional이 아니면 코드가 이 제안을 자동으로 반영하지 '
    '않을 뿐, 지혜 자체가 버려지는 것은 아닙니다), '
    '"direction": "up"|"down"|"review" 중 하나, "note": 왜 그렇게 조정해야 하는지 한 문장}. '
    "★scope='conditional'이거나 param이 화이트리스트 밖이면 이 제안은 파라미터에 자동 반영되지 "
    "않습니다 — 전역 상수(화이트리스트 3종)에 조건부 지혜를 반영하면 「주말에만 맞는 지혜가 "
    "평일까지 막는」 문제가 생기기 때문입니다. 이 제안은 자동 적용되지 않고 Jino가 콘솔에서 "
    "승인할 때(그리고 승인 시 입력하는 값으로)만 반영되는 참고 신호일 뿐입니다. "
    "근거 없는 승격을 하지 말고, 표본이 얇거나 인과가 불명하면 reject하세요."
)

_SCHEMA = {
    "verdict": "promote|reject",
    "principle": "string",
    "rationale": "string",
    # 선택 필드(지혜가 파라미터 변경을 함의할 때만) — 없으면 생략. promote 시 judge_verdict_json에
    # 그대로 보존돼(파싱 dict 전체를 dump) P4 wisdom_apply가 param_change 제안 생성 여부를
    # 판정하는 재료로 쓴다(scope=='unconditional' ∧ param∈SPECS일 때만 제안 생성, B7).
    "param_suggestion?": {
        "param": "|".join(sorted(guardrail_params.SPECS)),
        "scope": "unconditional|conditional",
        "direction": "up|down|review",
        "note": "string",
    },
}


def _is_ripe(cand: OpsWisdomCandidate, now: datetime) -> bool:
    """숙성 게이트 — TTL 14일 경과 or occurrences≥3."""
    if (cand.occurrences or 0) >= _OCCURRENCE_GATE:
        return True
    return cand.first_seen_at is not None and cand.first_seen_at <= now - timedelta(days=_TTL_DAYS)


def _sibling_buckets(db: Session, cand: OpsWisdomCandidate) -> list[dict]:
    """같은 action의 다른 후보(버킷)들 — n/good/bad/win_rate. D-NAO-248 §1: 판사가 「이 조건
    에서만 특이한 값인지, 다른 환경/유형에서도 재현되는지」를 대조할 재료(occurrences 상위
    _MAX_SIBLINGS건, 프롬프트 비대화 방지). 자기 자신은 제외한다."""
    if db is None or not cand.action:
        return []
    rows = (
        db.query(OpsWisdomCandidate)
        .filter(OpsWisdomCandidate.action == cand.action, OpsWisdomCandidate.id != cand.id)
        .order_by(OpsWisdomCandidate.occurrences.desc())
        .limit(_MAX_SIBLINGS)
        .all()
    )
    siblings = []
    for r in rows:
        good = r.good_count or 0
        bad = r.bad_count or 0
        total = good + bad
        siblings.append({
            "signature": r.signature, "grain": r.grain,
            "campaign_type": r.campaign_type, "experiment_batch": r.experiment_batch,
            "env_bucket": json.loads(r.env_bucket_json) if r.env_bucket_json else {},
            "n": total, "good": good, "bad": bad,
            "win_rate": round(good / total, 3) if total else None,
        })
    return siblings


def _prompt(cand: OpsWisdomCandidate, now: datetime, db: Session | None = None) -> str:
    env = json.loads(cand.env_bucket_json) if cand.env_bucket_json else {}
    days_since = (now - cand.first_seen_at).days if cand.first_seen_at else None
    good = cand.good_count or 0
    bad = cand.bad_count or 0
    total = good + bad
    win_rate = round(good / total, 3) if total else None
    # D-NAO-248 §1: grain='global' 후보만 by_campaign 병기(이질성 가시화, 부록 Q2) — 레거시
    # 캠페인 grain 후보(grain=NULL)는 campaign_id 1개뿐이라 이미 by_campaign과 동치.
    by_campaign = None
    if cand.grain == "global" and cand.by_campaign_json:
        by_campaign = json.loads(cand.by_campaign_json)
    view = {
        "campaign_id": cand.campaign_id, "action": cand.action, "env_bucket": env,
        "observation": cand.observation, "occurrences": cand.occurrences,
        "good_count": good, "bad_count": bad, "win_rate": win_rate,
        "days_since_first_seen": days_since,
        "grain": cand.grain, "campaign_type": cand.campaign_type,
        "experiment_batch": cand.experiment_batch, "by_campaign": by_campaign,
        "sibling_buckets": _sibling_buckets(db, cand),
    }
    return (
        "아래는 지혜 승격 후보 1건입니다(JSON). good_count/bad_count는 이 조건에서 결과가 "
        "target 이상(good)/미만(bad)이었던 관찰 수, win_rate=good/(good+bad)입니다. "
        "by_campaign은 이 후보(전역 시그니처)가 합친 캠페인별 분해, sibling_buckets는 같은 "
        "액션의 다른 환경/유형 후보들입니다(참고 재료 — 자동 강제 아님). "
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
            res = invoke(_prompt(cand, now, db), system=_SYSTEM, schema=_SCHEMA,
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
