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

from app.models import NaverAccountSettings
from app.services.naver_ad import guardrail_gate

log = logging.getLogger(__name__)

SETTINGS_KEY = "guardrail_params"

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
