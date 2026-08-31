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

from sqlalchemy import or_ as sa_or
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
_MAX_PER_RUN = 5         # 평시 회당 판사 상한
# ★D-NAO-251 §4-③ — 캐치업. 구판은 상한 5 × 1일 1회에 «따라잡기»가 없어, pending 17건이면
#   소화에 4일이 걸리고 크론이 하루 못 뜨면(08-24 prod 1h48m 다운 실전례) 그날 슬롯은 그냥
#   사라졌다. 그런데 **주기를 늘리는 것은 답이 아니다** — 재료 grain이 D-1이라 하루에 여러 번
#   판정할 이유가 없고, 북극성 §5-2가 *"주기를 부풀리는 것은 5,403배 오류와 같은 부류"*라고
#   못 박았다. 그래서 주기는 그대로 두고 **적체가 있을 때만 같은 회차 안에서 더 소화**한다.
#   ★15는 근거 없는 초깃값이다(계약 §2-6) — 상시 LLM 비용의 하드캡이자 주기 감사 재심 안건.
_MAX_PER_RUN_BACKLOG = 15  # 적체 시 회차 상한 = 하루 하드캡(크론이 1일 1회이므로 동치)
_MAX_SIBLINGS = 8        # condition_controls 상한(프롬프트 비대화 방지, occurrences desc)
_MAX_OTHER_TYPES = 4     # other_campaign_types 상한

# ★D-NAO-248 §4-B(B7-2) — param을 자유 텍스트가 아니라 SPECS 화이트리스트 enum으로 좁힌다.
#   판사가 무엇을 고를 수 있는지(키·이름·근거·범위)를 프롬프트에 그대로 실어 보낸다 — 코드
#   쪽 클램프(wisdom_apply._classify_param_suggestion)가 최종 판정이지만, 애초에 판사가
#   화이트리스트 밖을 고를 이유를 없앤다.
# ★D-NAO-281 — `SPECS.items()` 전건이 아니라 `llm_proposable_keys()`다. SPECS에 킬스위치가
#   등재되면서 「등재 = 판사가 제안 가능」이 되면, 엔진이 자기 킬스위치 해제를 제안하는 카드가
#   뜬다(계약 §5 「킬스위치 약화 금지」가 보는 방향과 반대).
_PARAM_KEYS_DESC = "\n".join(
    f"  - {key}: {guardrail_params.SPECS[key].label} — 허용범위 {guardrail_params.SPECS[key].lo}~"
    f"{guardrail_params.SPECS[key].hi}, direction={guardrail_params.SPECS[key].direction}. "
    f"{guardrail_params.SPECS[key].why}"
    for key in guardrail_params.llm_proposable_keys()
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
    '"scope": "unconditional" 또는 "conditional" 중 하나. ★이 필드가 묻는 것은 «이 지혜가 항상 '
    '참인가»가 아니라 **«이 파라미터를 전역으로 바꿨을 때, 이 후보가 다루지 않는 다른 조건들이 '
    '손해를 보는가»**입니다. 화이트리스트 3종은 요일·계절·출시창·캠페인유형을 가리지 않고 모든 '
    '집행에 걸리는 전역 상수이기 때문입니다. 재료의 sibling_buckets.condition_controls(조건 '
    '대조군 — 캠페인유형이 같고 실험배치가 없으며 환경 차원만 다른 형제, differs_in이 어느 '
    '차원이 다른지 알려줍니다)를 근거로 판단하세요: 대조군들이 같은 방향을 가리키면 전역 반영이 '
    '그 조건들을 해치지 않는다는 근거이므로 "unconditional"을 쓸 수 있습니다. 대조군이 반대 '
    '방향이거나, 대조군이 없거나, 판단이 서지 않으면 "conditional"입니다. '
    '★sibling_buckets.other_campaign_types는 대조군이 아닙니다 — 같은 액션 이름이라도 캠페인유형이 '
    '다르면 레버의 의미가 달라 승률을 직접 비교할 수 없습니다. 그러나 전역 상수는 그 유형들에도 '
    '걸립니다 — 즉 당신이 비교할 수 있는 범위는 이 파라미터가 실제로 미치는 범위보다 좁습니다. '
    '그 남는 불확실성이 크다고 보면 "conditional"을 쓰세요. '
    '★sibling_buckets.excluded_from_controls는 대조군에서 뺀 형제의 건수와 사유입니다(실험배치·'
    '레거시 grain·경계 미상) — 재료의 한계를 알리는 숫자이니 참고만 하고 근거로 쓰지 마세요. '
    '"unconditional"을 우기지 마세요 — scope가 unconditional이 아니면 코드가 이 제안을 자동으로 '
    '반영하지 않을 뿐, 지혜 자체가 버려지는 것은 아닙니다(브리핑에는 그대로 실립니다), '
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
        "param": "|".join(sorted(guardrail_params.llm_proposable_keys())),
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


def _sibling_row(r: OpsWisdomCandidate, differs_in: list[str]) -> dict:
    good = r.good_count or 0
    bad = r.bad_count or 0
    total = good + bad
    return {
        "signature": r.signature, "grain": r.grain,
        "campaign_type": r.campaign_type, "experiment_batch": r.experiment_batch,
        "env_bucket": json.loads(r.env_bucket_json) if r.env_bucket_json else {},
        "n": total, "good": good, "bad": bad,
        "win_rate": round(good / total, 3) if total else None,
        "differs_in": differs_in,
    }


def _sibling_buckets(db: Session, cand: OpsWisdomCandidate) -> dict:
    """같은 action의 형제 후보들을 «조건 대조군 여부»로 분류한다(D-NAO-248 §2, 계약
    「질문이 답을 막았다」의 재료 교정). 판사가 여태 묻던 「이 지혜가 항상 참인가」가 아니라
    「조건 대조군들이 같은 방향을 가리키는가」를 대조할 수 있게, 형제를 다음 넷으로 나눈다:

    - condition_controls: 후보와 캠페인유형이 같고·실험배치가 없으며·환경 차원만 다른 형제
      (진짜 조건 대조군, occurrences=n desc 상위 _MAX_SIBLINGS건).
    - other_campaign_types: 캠페인유형이 달라 비교 불가한 형제(대조군 아님, 상위 _MAX_OTHER_TYPES건).
    - excluded_from_controls: 대조군에서 뺀 «전수» 건수(실험배치·레거시 grain·경계미상·
      candidate_not_eligible) — 상한에 잘리기 전 전수 기준으로 센다(「창에 갇힌 숫자」가 되지
      않도록).
    - truncated: 상한(8·4)에 잘려 나간 건수.

    ★후보 자신이 grain != 'global'이거나 experiment_batch를 가지면(레거시·실험 후보) 대조군
    개념이 성립하지 않으므로 condition_controls는 항상 빈 리스트로 둔다(fail-closed, 규칙 0).
    그렇다고 그 형제들이 «어디에도 안 잡힌 채 사라지지» 않는다 — 규칙 1~3에 안 걸린 형제(=
    후보가 적격이었다면 조건 대조군이 됐을 형제)는 excluded_from_controls["candidate_not_eligible"]
    로 센다(P2-1: 「침묵」과 「0건」은 다르다 — 카운터가 있어야 침묵을 본다).
    db가 없거나 cand.action이 없으면 4키 전부 빈 값(리스트 []·카운터 0)인 dict를 돌려준다
    (None·빈 dict 금지 — 키 부재와 0건은 다르다).
    """
    empty: dict = {
        "condition_controls": [], "other_campaign_types": [],
        "excluded_from_controls": {
            "experiment_batch": 0, "legacy_grain": 0, "unknown_boundary": 0,
            "candidate_not_eligible": 0,
            # ★D-NAO-251 §4-② ⓐ — action이 없어 «형제 매칭 자체가 불가능»했던 건수.
            #   0이어도 키를 낸다(교훈 #318).
            "no_action": 0,
        },
        "truncated": {"condition_controls": 0, "other_campaign_types": 0},
    }
    if db is None:
        return empty
    if not cand.action:
        # ★D-NAO-251 §4-② ⓐ — 구판은 여기서 «전부 0»인 dict를 그대로 돌려줬다. 그러면
        #   판정문에 「대조군 없음」으로 보이는데, 실제로는 «대조를 시도조차 못 했다»였다
        #   (n=52 P2-1이 규칙 0 경로에 대해 고친 것과 **같은 모양의 두 번째 침묵**).
        #   그래서 「액션 미상이라 아무하고도 못 묶은」 형제 수를 세어 침묵을 값으로 바꾼다.
        #   ★action을 매칭 «값»으로 쓰지는 않는다 — 미상끼리 묶으면 서로 다른 액션이 가짜
        #   대조군이 되어 판사 재료가 오염된다(북극성 §7 학습 오염과 같은 결).
        empty["excluded_from_controls"]["no_action"] = (
            db.query(OpsWisdomCandidate)
            .filter(
                OpsWisdomCandidate.id != cand.id,
                sa_or(OpsWisdomCandidate.action.is_(None), OpsWisdomCandidate.action == ""),
            )
            .count()
        )
        return empty

    rows = (
        db.query(OpsWisdomCandidate)
        .filter(OpsWisdomCandidate.action == cand.action, OpsWisdomCandidate.id != cand.id)
        .all()
    )

    cand_env = json.loads(cand.env_bucket_json) if cand.env_bucket_json else {}
    cand_can_have_controls = cand.grain == "global" and cand.experiment_batch is None

    condition_controls: list[dict] = []
    other_campaign_types: list[dict] = []
    excluded = {
        "experiment_batch": 0, "legacy_grain": 0, "unknown_boundary": 0,
        "candidate_not_eligible": 0,
    }

    for r in rows:
        # 규칙 1 — 레거시(grain != 'global'): 전역 후보의 by_campaign 분해와 같은 일기 행을
        #   센다(중복 표본). signature가 "g?"로 시작하면 경계 미상 분리로 따로 센다.
        if r.grain != "global":
            if (r.signature or "").startswith("g?"):
                excluded["unknown_boundary"] += 1
            else:
                excluded["legacy_grain"] += 1
            continue
        # 규칙 2 — 실험 배치: 풀링 경계(계약 §2). 섞으면 학습 오염.
        if r.experiment_batch is not None:
            excluded["experiment_batch"] += 1
            continue
        # 규칙 3 — 캠페인유형이 다르면 대조군이 아니라 비교 불가 유형.
        if r.campaign_type != cand.campaign_type:
            other_campaign_types.append(_sibling_row(r, []))
            continue
        # 규칙 0 — 후보 자신이 대조군을 가질 수 없으면 규칙 4(condition_controls)를 적용하지
        #   않는다. 이 형제는 규칙 1~3 어디에도 안 걸리지 않았다(= 후보가 적격이었다면 조건
        #   대조군이 됐을 형제) — 어느 버킷에도 안 잡힌 채 버려지지 않도록 candidate_not_eligible로
        #   센다(P2-1: 「대조군 없음」과 「대조를 하지 않았다」는 다르다).
        if not cand_can_have_controls:
            excluded["candidate_not_eligible"] += 1
            continue
        # 규칙 4 — 나머지는 조건 대조군. differs_in = env_bucket 키 합집합 중 값이 다른 키.
        r_env = json.loads(r.env_bucket_json) if r.env_bucket_json else {}
        keys = set(cand_env.keys()) | set(r_env.keys())
        differs = sorted(k for k in keys if cand_env.get(k) != r_env.get(k))
        condition_controls.append(_sibling_row(r, differs))

    condition_controls.sort(key=lambda s: s["n"], reverse=True)
    other_campaign_types.sort(key=lambda s: s["n"], reverse=True)
    truncated = {
        "condition_controls": max(0, len(condition_controls) - _MAX_SIBLINGS),
        "other_campaign_types": max(0, len(other_campaign_types) - _MAX_OTHER_TYPES),
    }
    return {
        "condition_controls": condition_controls[:_MAX_SIBLINGS],
        "other_campaign_types": other_campaign_types[:_MAX_OTHER_TYPES],
        "excluded_from_controls": excluded,
        "truncated": truncated,
    }


def _prior_judgments_view(cand: OpsWisdomCandidate) -> list[dict] | None:
    """재심 재료 — 이전 판정들의 (시각·verdict·rationale·당시 표본) 압축. 없으면 None.

    ★None과 []는 다르다: None='이 후보는 처음 판정된다', []='이력 컬럼이 비어 있다'(구판에서
    판정됐으나 이력화 전이라 판정문만 있는 경우). 그래서 현재 판정문도 «직전 판정»으로 함께
    싣는다 — 재심 판사가 「무엇을 뒤집으려 하는지」를 보지 못하면 재심의 의미가 없다.
    """
    items: list[dict] = []
    for rec in json.loads(cand.prior_judgments_json or "[]"):
        try:
            v = json.loads(rec.get("verdict_json") or "{}")
        except (ValueError, TypeError):
            v = {}
        items.append({
            "judged_at": rec.get("judged_at"),
            "occurrences_at_judgment": rec.get("occurrences_at_judgment"),
            "verdict": v.get("verdict"), "rationale": v.get("rationale"),
        })
    if cand.judge_verdict_json:
        try:
            v = json.loads(cand.judge_verdict_json)
        except (ValueError, TypeError):
            v = {}
        items.append({
            "judged_at": cand.judged_at.isoformat() if cand.judged_at else None,
            "occurrences_at_judgment": cand.judged_occurrences,
            "verdict": v.get("verdict"), "rationale": v.get("rationale"),
        })
    if not items:
        return None
    now_n = cand.occurrences or 0
    base = cand.judged_occurrences
    for it in items:
        it["occurrences_now"] = now_n
    if base:
        items[-1]["evidence_growth"] = f"{base} → {now_n} (×{round(now_n / base, 2)})"
    return items


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
        # ★D-NAO-251 §4-① — 재심이면 «이전에 무엇을 근거로 기각했고, 그 뒤 표본이 얼마나
        #   늘었는지»를 재료로 준다. 재료만이고 판정 강제가 아니다(D-NAO-248 전례) — 판사가
        #   같은 이유로 다시 기각하는 것도 유효한 결과다.
        "prior_judgments": _prior_judgments_view(cand),
    }
    return (
        "아래는 지혜 승격 후보 1건입니다(JSON). good_count/bad_count는 이 조건에서 결과가 "
        "target 이상(good)/미만(bad)이었던 관찰 수, win_rate=good/(good+bad)입니다. "
        "by_campaign은 이 후보(전역 시그니처)가 합친 캠페인별 분해입니다. sibling_buckets는 "
        "같은 액션의 형제 후보를 condition_controls(조건 대조군 — 캠페인유형이 같고 실험배치가 "
        "없으며 환경 차원만 다른 형제, differs_in이 어느 차원이 다른지 알려줍니다)와 "
        "other_campaign_types(캠페인유형이 달라 비교 불가한 형제 — 대조군 아님)로 분류하고, "
        "excluded_from_controls(대조군에서 뺀 건수 — 실험배치·레거시 grain·경계미상·"
        "no_action[액션 미상이라 형제 매칭 자체가 불가])와 truncated(상한에 잘린 건수)를 함께 "
        "담습니다(참고 재료 — 자동 강제 아님). "
        "prior_judgments가 있으면 이 후보는 **재심**입니다 — 이전에 같은 후보를 판정한 근거와 "
        "그 뒤 표본이 얼마나 늘었는지(evidence_growth)를 담았습니다. 표본이 늘었다는 사실 자체가 "
        "승격 사유는 아닙니다. 이전 기각 사유가 «표본·기간 부족»이었다면 지금 그것이 해소됐는지 "
        "보고, 사유가 «방향이 틀렸다»였다면 늘어난 표본이 그 판단을 바꾸는지 보십시오. "
        "같은 이유로 다시 기각하는 것도 유효한 판정입니다. "
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
    ripe_all = [c for c in pending if _is_ripe(c, now)]
    # ★D-NAO-251 §4-② ⓓ — action 미상 후보는 대기열에서 뺀다. 형제 매칭이 원리적으로 불가라
    #   판사에게 «대조군 없음»만 보여 주는 판정을 부르고, 그 판정이 다시 terminal이 된다.
    #   수확층(§4-② ⓑ)이 이런 후보를 더는 안 만들고, 기존분은 마이그레이션이 hidden 처분한다 —
    #   이 필터는 그 둘 사이의 fail-closed 안전망이다. 뺐다는 사실은 카운터로 남긴다.
    skipped_no_action = sum(1 for c in ripe_all if not c.action)
    ripe_all = [c for c in ripe_all if c.action]
    # ★D-NAO-251 §4-③ — 적체가 있을 때만 회차 상한을 올린다(주기는 불변).
    cap = _MAX_PER_RUN_BACKLOG if len(ripe_all) > _MAX_PER_RUN else _MAX_PER_RUN
    ripe = ripe_all[:cap]

    totals = {
        "ripe": len(ripe), "promoted": 0, "rejected": 0, "skipped_llm": 0, "errors": 0,
        # ★D-NAO-251 — 적체가 침묵하지 않도록 이 회차의 «남긴 것»을 값으로 낸다(교훈 #318).
        "ripe_available": len(ripe_all),          # 이번 회차에 숙성해 있던 전건
        "cap_applied": cap,                       # 실제 적용된 회차 상한(5 또는 15)
        "backlog_remaining": max(0, len(ripe_all) - len(ripe)),  # 익일로 넘긴 건수
        "skipped_no_action": skipped_no_action,   # 대기열에서 뺀 action 미상 후보
        "rejudged": 0,                            # 재심(=이전 판정이 있던 후보)으로 판정한 건수
    }
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
            # ★D-NAO-251 §4-① — 판정 «전»의 판정문을 이력으로 밀어 넣고 나서 덮어쓴다.
            #   judge_verdict_json의 «형태»에 wisdom_writer.py:51·wisdom_apply.py:72가
            #   의존하므로 그 컬럼의 모양은 바꾸지 않는다(계약 §3).
            is_rejudge = cand.judged_at is not None or cand.judge_verdict_json is not None
            if cand.judge_verdict_json:
                prior = json.loads(cand.prior_judgments_json or "[]")
                prior.append({
                    "judged_at": cand.judged_at.isoformat() if cand.judged_at else None,
                    "occurrences_at_judgment": cand.judged_occurrences,
                    "verdict_json": cand.judge_verdict_json,
                })
                cand.prior_judgments_json = json.dumps(prior, ensure_ascii=False)
            cand.judge_verdict_json = json.dumps(parsed, ensure_ascii=False)
            cand.status = "promoted" if verdict == "promote" else "rejected"
            # 재개방 기준선을 «이번» 판정 시점으로 다시 찍는다 — 다음 재개방은 여기서부터 2배다.
            cand.judged_at = now
            cand.judged_occurrences = cand.occurrences or 0
            if is_rejudge:
                cand.rejudge_count = (cand.rejudge_count or 0) + 1
                totals["rejudged"] += 1
            db.commit()
            totals["promoted" if verdict == "promote" else "rejected"] += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_judge: 판정 반영 실패(sig=%s): %s", cand.signature, e)
    return totals
