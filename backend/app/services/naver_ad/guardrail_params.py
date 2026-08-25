# guardrail_params.py — 안전 봉투 파라미터의 «근거 있는 기준» 층 (D-NAO-172, P1)
#
# ══ 왜 만드는가 ══
# 봉투(±15%·쿨다운 2h·자동하향 3회/일·누적 2.0×)가 파이썬 상수로 박혀 있어, 바꾸려면 배포가
# 필요하고 «지금 무슨 값으로 돌고 있는지» 화면 어디에도 안 보였다. Jino 지시(2026-08-10):
#   *"안전봉투도 절대규칙이 아니라 조절할 수 있는 기준으로 만들자 … 기준은 정하데 기준도
#     상황에 따라서 바뀔 수 있는거지"*
#
# ══ 착수 근거가 된 실측 (2026-07-16~30, 차단 3,863건) ══
# 봉투가 실제로 막은 건 **86건 = 2.2%**뿐이다(쿨다운 36 · 일일상한 50). 1위는 소급채점 stale
# 2,138건(55%)이었다. 즉 **봉투는 병목이 아니었고, 푸는 것의 기대 이득은 작다.** 그래서 이 층은
# 「풀기 위한 장치」가 아니라 **①지금 값이 무엇인지 보이게 하고 ②조이는 쪽을 상황에 맞게
# 움직일 수 있게** 하는 것이 목적이다(풀기는 사람 승인 경로로만 — P3).
#
# ══ 3층 구조와 fail 방향 ══
#   코드 상수(최후 폴백)  ←  DB KV(`naver_account_settings.guardrail_params`)  ←  상황 조정(P2·P3)
# - KV 없음/파싱 실패/타입 불일치/범위 밖 → **그 항목만 코드 상수로 폴백**(fail-to-current).
#   fail-closed(0으로)도 fail-open(무제한)도 아니다 — 「모르면 지금까지 하던 대로」가
#   이 층의 유일하게 안전한 기본값이다. 봉투가 0이면 광고가 멈추고, 무제한이면 돈이 샌다.
# - `_PARAMS_FROM_DB = False` 한 줄로 전부 코드 상수로 원복(사고 시 되돌림 스위치).
#
# ══ 범위(min/max)가 이 파일의 핵심이다 ══
# DB에 값을 넣을 수 있다는 것은 **누군가 잘못 넣을 수 있다**는 뜻이다. 그래서 각 파라미터에
# 「아무리 조절해도 여기까지」를 박아 둔다. 이 범위는 배포로만 바뀐다 — DB가 자기 상한을
# 넓힐 수 없다는 것이 되먹임 차단의 마지막 층이다(첫 층은 「풀기는 사람 승인」).
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models import NaverAccountSettings, NaverChangeLog
from app.services.naver_ad import guardrail_gate
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

SETTINGS_KEY = "guardrail_params"

# ★D-NAO-248 §4-B(B7-5) — param_change NaverProposal이 SPECS 키를 **구조적으로** 실어 나르는
# target_type 값. rationale 자유텍스트 파싱을 승인 핸들러에 강제하지 않기 위해 기존 컬럼
# (target_type/target_id)에 담는다 — target_id에 SPECS 키 문자열을 그대로 쓴다.
# ★grep 확인(2026-08-25): 기존 target_type 값(campaign/adgroup/keyword/ad/search_term/account)
# 어디에도 "guardrail_param"은 없다 — 새 값이 기존 분기와 충돌하지 않는다.
# ★길이: "guardrail_param"=15자(NaverProposal.target_type String(20) 안), SPECS 키 중 최장
# "max_daily_auto_bid_downs"=25자(target_id String(50) 안).
TARGET_TYPE = "guardrail_param"

# ★되돌림 스위치 — False면 DB를 아예 읽지 않고 전부 코드 상수로 돈다(D-NAO-172).
#   `AD_BID_ROUTING_ENABLED`·`_GROUP_STEP_ALL_ADS`와 같은 관례: 사고 났을 때 한 줄로
#   원복된다는 믿음이 이런 스위치의 존재 이유다.
_PARAMS_FROM_DB: bool = True


class ParamSpec:
    """파라미터 1개의 «근거». 값 자체보다 이 메타가 이 설계의 산출물이다.

    Attributes:
        key: KV JSON의 키.
        default: 코드 상수(최후 폴백). guardrail_gate에서 가져온다 — 두 곳에 숫자를 적지 않는다.
        kind: 'decimal' | 'int'.
        lo/hi: **배포로만 바뀌는** 허용 범위. DB는 이 밖으로 못 나간다.
        label: 화면 표기.
        why: 이 값이 왜 이 값인가(현황판에 그대로 노출 — 「근거를 만들자」는 지시의 이행부).
        direction: 'tighten_down' = 값이 **작아지면** 조이는 것 / 'tighten_up' = 커지면 조이는 것.
            풀기/조이기 판정에 쓴다(P2·P3). 사람이 헷갈리는 축이라 데이터로 박아 둔다.
    """

    def __init__(self, key: str, default: Any, kind: str, lo: Any, hi: Any,
                 label: str, why: str, direction: str) -> None:
        self.key, self.default, self.kind = key, default, kind
        self.lo, self.hi = lo, hi
        self.label, self.why, self.direction = label, why, direction


# ★default는 guardrail_gate 상수를 **참조**한다(복사 금지) — 상수가 바뀌면 여기도 따라온다.
SPECS: dict[str, ParamSpec] = {
    # ★`max_change_pct`(±15% 스텝)는 **일부러 뺐다** — 적대 리뷰 P1-1(2026-08-10).
    #   ±15%는 게이트 전용이 아니라 스텝 «생성기» 7곳이 자기들끼리 재현한다:
    #   `proposal_writer._step_down_bid`(:175) · `_ad_step_bid`(:188) · bid_up step_cap(:309) ·
    #   `proposal_pipeline._build_expansion_bid_up` · `auto_operator._fire_vitality_revive` ·
    #   `_check_bid_up_conditions`(:612) — 전부 `_MAX_CHANGE_PCT`를 직접 import한다.
    #   `_clamp_step` 한 곳만 파라미터를 보게 하고 DB로 값을 내리면 나머지 생성기의 제안이
    #   게이트에서 전건 「변경폭 초과」로 죽고, 그것도 **`failed` 영구 종결**(재승인만 재시도)이라
    #   다음 회차에 저절로 회복되지 않는다. 하필 `_step_down_bid`가 **손실 하향** 산식이라
    #   **조이려고 값을 내리면 조이는 레버가 가장 먼저 죽는다.**
    #   ★아래 셋만 남긴 이유가 이것이다 — 전부 **게이트 전용**이라 생성기 중복이 없다.
    #   되살리려면 **생성기 전수를 먼저 배선**하고 정합 테스트를 `_clamp_step` 하나가 아니라
    #   생성기 전수로 확장할 것(P2가 스텝을 건드리므로 그때가 그 자리다).
    "cooldown_hours": ParamSpec(
        "cooldown_hours", guardrail_gate._COOLDOWN_HOURS, "int", 1, 24,
        "같은 유닛 쿨다운",
        "2시간(D-NAO-19). 진동 방어 담당 — 조이기 등급이 올라가도 이건 유지한다. "
        "2026-07 실측 36건만 물어 병목이 아니었다.",
        "tighten_up",
    ),
    "max_daily_auto_bid_downs": ParamSpec(
        "max_daily_auto_bid_downs", guardrail_gate._MAX_DAILY_AUTO_BID_DOWNS, "int", 1, 8,
        "자동 하향 일일 상한",
        "3회/일 = 하루 최대 −39%(0.85³). ★손실이 확인된 상품도 이 속도로만 내려가 «둔한» "
        "지점이다 — P2의 조이기 자동화가 여기를 등급별로 올린다. 상한 8회면 하루 −73%.",
        "tighten_up",
    ),
    "max_auto_up_multiple": ParamSpec(
        "max_auto_up_multiple", guardrail_gate._MAX_AUTO_UP_MULTIPLE, "decimal",
        Decimal("1.0"), Decimal("3.0"),
        "자동 상향 누적 상한",
        "사람/대행사가 정한 기준가의 2.0배. codex 적대 3R이 «기준점이 옛 고가에 머물러 "
        "자동화가 되돌려 올림» 구멍을 잡은 자리이고, 2026-08-10 적대 리뷰 P1도 정확히 이 "
        "브레이크를 무력화하는 결함이었다. BEP 하한은 부모 그룹 30일 집계라 개별 소재를 "
        "못 막으므로 **대체 브레이크가 없다.**",
        "tighten_down",
    ),
}


def _coerce(spec: ParamSpec, raw: Any) -> Any | None:
    """DB 원값 → 타입·범위 검증. 실패하면 None(호출부가 코드 상수로 폴백)."""
    try:
        if spec.kind == "decimal":
            val: Any = Decimal(str(raw))
        else:
            if isinstance(raw, bool):  # bool은 int의 하위형이라 먼저 걸러낸다
                return None
            val = int(raw)
    except (TypeError, ValueError, ArithmeticError, InvalidOperation):
        return None
    # NaN은 비교 자체가 InvalidOperation을 던진다 — 범위 검사보다 먼저 걸러낸다(적대 리뷰 P2-1).
    # `get_params`는 harness 핫패스에 있고 「설정 한 줄이 광고 집행 경로를 죽이면 안 된다」가
    # 이 층의 계약이다. UI 가드로 도달 불가여도 계약은 도달 가능성과 무관하게 성립해야 한다.
    if spec.kind == "decimal" and not val.is_finite():
        log.error("guardrail_params: %s=%r는 유한한 수가 아니다 — 코드 상수로 폴백", spec.key, raw)
        return None
    if val < spec.lo or val > spec.hi:
        log.error(
            "guardrail_params: %s=%s는 허용 범위 [%s, %s] 밖 — 코드 상수 %s로 폴백"
            "(범위는 배포로만 바뀐다: DB가 자기 상한을 넓힐 수 없다)",
            spec.key, val, spec.lo, spec.hi, spec.default,
        )
        return None
    return val


def _raw_overrides(db: Session) -> tuple[dict, Any]:
    """KV 원본 dict와 updated_at. 없거나 깨졌으면 ({}, None) — 조용히 넘어가지 않고 로그."""
    if not _PARAMS_FROM_DB:
        return {}, None
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == SETTINGS_KEY).first()
    if row is None or not row.value_json:
        return {}, None
    try:
        parsed = json.loads(row.value_json)
        if not isinstance(parsed, dict):
            raise ValueError(f"{SETTINGS_KEY}는 객체여야 하는데 {type(parsed).__name__}")
        return parsed, row.updated_at
    except (TypeError, ValueError) as exc:
        log.error("guardrail_params: %s 파싱 실패 — 전부 코드 상수로 폴백(%s: %s)",
                  SETTINGS_KEY, type(exc).__name__, exc)
        return {}, None


def get_params(db: Session) -> dict[str, Any]:
    """실행 경로가 쓰는 유효 파라미터 {key: 값}. **항상 전 키가 채워져 나온다.**

    개별 항목이 잘못돼도 그 항목만 코드 상수로 떨어지고 나머지는 DB 값을 쓴다 — 한 칸이
    깨졌다고 전부 되돌리면 «부분 실패가 전체 롤백»이 되어 오히려 예측이 어렵다.
    """
    overrides, _ = _raw_overrides(db)
    out: dict[str, Any] = {}
    for key, spec in SPECS.items():
        val = _coerce(spec, overrides[key]) if key in overrides else None
        out[key] = spec.default if val is None else val
    return out


def describe(db: Session) -> list[dict]:
    """봉투 현황판용 — 값 + **출처** + 근거 + 허용 범위.

    ★`source`가 이 함수의 존재 이유다: 「지금 무슨 값으로 돌고 있나」와 「그게 어디서 왔나」가
    같이 보여야 한다. 값만 보이면 DB를 고쳤는데 코드 상수가 이기고 있는 상태를 못 본다
    (이 리포가 반복해 데인 «기록됐다 ≠ 코드가 읽는다»의 봉투판).
    """
    overrides, updated_at = _raw_overrides(db)
    rows: list[dict] = []
    for key, spec in SPECS.items():
        raw = overrides.get(key)
        val = _coerce(spec, raw) if key in overrides else None
        from_db = val is not None
        rows.append({
            "key": key,
            "label": spec.label,
            "value": float(spec.default if val is None else val),
            "source": "db" if from_db else "code",
            "code_default": float(spec.default),
            "min": float(spec.lo),
            "max": float(spec.hi),
            "why": spec.why,
            "direction": spec.direction,
            # DB에 값이 있는데 source가 code면 «거부됨»이다 — 조용히 무시하지 않고 표면화한다.
            "rejected": key in overrides and not from_db,
            "updated_at": updated_at.isoformat() if (from_db and updated_at) else None,
        })
    return rows


class InvalidGuardrailParams(ValueError):
    """apply_params 검증 실패(본문 비-객체·미지원 키·범위 밖 값). 호출부(라우터)가 이걸
    HTTPException(400, str(e))로 변환한다 — 이 서비스 층은 FastAPI를 모른다(레이어 분리)."""


def apply_params(
    db: Session, body: Any, *, rationale: str,
    proposal_id: int | None = None, wisdom_id: int | None = None,
    merge: bool = False,
) -> dict:
    """봉투 파라미터 **검증 + KV 저장 + change_log 기록**의 단일 진실(B0, D-NAO-248 §4-B).

    ★`merge`가 저장 «범위»를 정한다 — 두 호출부의 맥락이 다르기 때문이다:
      · `merge=False`(기본, **PUT 경로**) — 전체 치환. 넘긴 키만 남고 나머지는 코드 상수로
        복귀한다. 사람이 **화면 전체를 보고 저장**하는 맥락이라 정당하다(기존 계약 불변).
      · `merge=True`(**승인 경로**) — 저장된 행에 그 키만 덮어쓴다. 승인은 «제안 한 건»의
        맥락이라 전체 치환을 쓰면 **사람이 따로 설정해 둔 다른 키가 조용히 코드 기본값으로
        되돌아간다.** 되돌아간 흔적은 화면 source가 'db'→'code'로 바뀌는 것뿐이라, 사람은
        자기가 안 만진 값이 바뀐 걸 못 쫓는다(회귀 테스트
        `test_param_change_approve_does_not_wipe_the_other_params`가 고정).
      ★병합 기준은 «저장된 행의 원문»이지 `get_params()`의 실효값이 아니다 — 실효값으로
        병합하면 사람이 한 번도 설정한 적 없는 키가 코드 기본값 그대로 DB에 박혀 `source`가
        'code'에서 'db'로 바뀐다(안 만졌는데 만진 것으로 보인다).

    ★기존 PUT /settings/guardrail-params 핸들러 본문을 그대로 옮긴 것이다 — 동작·400 조건·
    change_log 내용은 **완전히 동일**해야 한다(회귀 테스트 test_guardrail_params_p1.py가 고정).
    이제 콘솔 승인 핸들러(B1)도 같은 함수를 호출해 검증·기록을 복제하지 않는다 — 복제하면
    두 경로(PUT·승인)가 갈라진다(이 저장소가 반복해 데인 «표방↔실구현 괴리»의 새 버전이 된다).

    ★`db.commit()`을 하지 않는다 — `db.flush()`까지만 한다. 승인 경로(B1)가 「제안 상태 전이
    + 파라미터 적용」을 **한 트랜잭션**으로 묶어야 하므로(실패 시 상태 전이도 롤백), 커밋 시점은
    호출부 책임이다. PUT 핸들러는 이 함수 호출 직후 자신이 커밋한다(기존과 동일한 최종 결과).

    proposal_id/wisdom_id가 주어지면 change_log의 `rationale`에 그 좌표를 병기하고
    `proposal_id` 컬럼에도 심는다(B4가 요구하는 「제안→change_log」 조인 — wisdom_scorecard의
    `_change_rows_for`가 이미 `NaverChangeLog.proposal_id`를 그 방향으로 조회한다).

    반환: {"changed": bool, "change_log_id": int|None, "before": dict, "after": dict}.
    무변화(before==after)면 change_log를 만들지 않고 change_log_id=None(PUT의 기존 계약과 동일).
    """
    if not isinstance(body, dict):
        raise InvalidGuardrailParams("본문은 객체여야 합니다")
    unknown = set(body) - set(SPECS)
    if unknown:
        raise InvalidGuardrailParams(f"알 수 없는 파라미터: {sorted(unknown)}")
    cleaned: dict = {}
    for key, raw in body.items():
        spec = SPECS[key]
        val = _coerce(spec, raw)
        if val is None:
            raise InvalidGuardrailParams(
                f"{key}={raw!r}는 허용 범위 [{spec.lo}, {spec.hi}] 밖이거나 타입이 맞지 않습니다",
            )
        cleaned[key] = str(val)

    before = {k: str(v) for k, v in get_params(db).items()}
    row = db.query(NaverAccountSettings).filter(
        NaverAccountSettings.key == SETTINGS_KEY).first()
    to_store = cleaned
    if merge and row is not None:
        # 저장된 «원문»에 이번 키만 덮어쓴다. 원문이 깨졌으면(파싱 실패) 병합할 근거가 없으니
        # 이번 키만 남긴다 — 읽기 경로(`get_params`)가 깨진 행을 이미 코드 상수로 폴백시키므로
        # 사람이 잃는 값이 없다. SPECS 밖 키는 여기서 떨군다(옛 키가 영원히 따라다니지 않게).
        try:
            existing = json.loads(row.value_json) or {}
        except (TypeError, ValueError):
            existing = {}
        if isinstance(existing, dict):
            to_store = {k: v for k, v in existing.items() if k in SPECS}
            to_store.update(cleaned)
    if row is None:
        row = NaverAccountSettings(key=SETTINGS_KEY, value_json=json.dumps(to_store))
        db.add(row)
    else:
        row.value_json = json.dumps(to_store)
    db.flush()
    after = {k: str(v) for k, v in get_params(db).items()}

    change_log_id = None
    if before != after:
        now = kst_now()
        coords = []
        if proposal_id is not None:
            coords.append(f"proposal_id={proposal_id}")
        if wisdom_id is not None:
            coords.append(f"wisdom_id={wisdom_id}")
        full_rationale = f"{rationale} ({', '.join(coords)})" if coords else rationale
        log_row = NaverChangeLog(
            entity_type="account", entity_id="", campaign_id="",
            action="update_guardrail_params",
            before_value=json.dumps(before, ensure_ascii=False),
            after_value=json.dumps(after, ensure_ascii=False),
            rationale=full_rationale,
            proposal_id=proposal_id,
            # changed_at·executed_at 둘 다 KST 명시 — B-1 가드(D-NAO-169)가 30분 초과 어긋남을 거부한다.
            dry_run=False, executed_at=now, changed_at=now,
        )
        db.add(log_row)
        db.flush()
        change_log_id = log_row.id
    return {"changed": before != after, "change_log_id": change_log_id, "before": before, "after": after}
