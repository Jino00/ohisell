"""날짜별 «당시 관할» 재구성 — PAO 밴드 판정의 단일 소스.

Jino 원문 2026-08-29: *"전체/PAO가 돌리는광고/PAO가 돌리지 않는광고/ 이렇게 나눠줄 수 있어?"*
그리고 정의: *"광고캠페인을 통째로 가져올 수도 있고, 광고그룹만도 가져올 수 있잖아. 내가
말하는 PAO가 돌린다는 의미는 이런 케이스를 말해"* / 방식: **「방법 B — 날짜별 실제 담당」**.

# 왜 «현재 관할 소급»이 아니라 «당시 관할»인가

현재 상태로 과거를 나누면 「지금 맡은 것들의 과거 성과」라는 **다른 질문**에 답한다. 실측이
그 차이를 극단적으로 보여준다(2026-08-29 기준 최근 30일):

| 방식 | PAO 밴드 광고비 |
|---|---|
| `optimizer='ours'` 캠페인 소급 | 7,607,618원 (39.1%) |
| 현재 그룹 스코프 소급 | 2,170,514원 (11.1%) |
| **당시 관할(이 모듈)** | **0원** — 07-30 10:48에 관할이 끊겼다 |

★현재 스코프는 **2026-08-29 00:25에 만들어졌다.** 그걸 30일에 투영하면 화면이 「지난 달
PAO가 217만원 굴렸다」고 말하는데, 그 30일 중 29.5일은 관할이 0이었다.

# 관할의 축은 «셋»이다 — 진리표만으로는 부족하다

    optimizer == 'ours'  ∧  auto_operate  ∧  광고그룹 진리표(D-NAO-244)

셋 다 코드가 실제로 본다: `proposal_writer._ours_campaign_ids`(D-NAO-13, 제안 대상) ·
`auto_operator`(D-NAO-49, 자동 운영 마스터) · `adgroup_scope`(D-NAO-244, 그룹 축). ∧ 결합의
선례는 `cold_start_bid_lane._auto_campaigns`가 이미 세워 뒀다.

★**실증**: 2026-07-30 10:48 ~ 08-29 12:53 구간은 `auto_operate`가 ON인 채였는데
`optimizer='none'`이었다. 진리표만 보면 이 **한 달이 통째로** 「관할」로 잘못 잡힌다.

# 되감기(rewind)로 재구성한다

현재 상태에서 출발해 `naver_change_log`를 최신→과거로 훑으며 `before_value`로 되돌린다.
각 이벤트가 구간의 경계가 되고, 구간마다 상태 하나가 확정된다.

# 모르는 것은 «모름»으로 남긴다 (0으로 뭉개지 않는다)

세 가지가 unknown이다:
1. **이력 시작 이전** — `naver_change_log`의 최초 행보다 앞선 날짜. 되돌릴 근거가 없다.
2. **해석불가 이벤트보다 앞선 날짜(그 캠페인만)** — 뒤집을 수 없는 이벤트를 만나면 그
   지점보다 과거는 신뢰할 수 없다. 조용히 건너뛰면 **틀린 상태로 계속 되감게** 된다.
3. (별도 밴드) **전환일** — 하루 중간에 관할이 바뀐 날. `naver_ad_daily`가 하루 통짜라
   시각 분할이 원리적으로 불가하다. 임의 배정은 임의 오차를 «정확한 숫자»의 얼굴로 내보낸다.

`before_value`/`after_value` 포맷이 **비일관**이라 파서가 세 형태를 다 받는다(실측):
스칼라 문자열(`'none'`·`'true'`) · JSON(`'{"optimizer": "ours", "auto_operate": true}'`) ·
scope 문자열(`'role=None enabled=True'`).

# 시각축

`naver_change_log.changed_at`은 **KST**다. 같은 이벤트의 `naver_campaign_settings.updated_at`은
**UTC**라 9시간 차가 난다(실측) — 섞으면 경계일이 통째로 어긋나므로 이 모듈은 `changed_at`만
쓴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date as date_cls, datetime

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdgroupScope, NaverCampaignSettings, NaverChangeLog

# ── 밴드 ────────────────────────────────────────────────────────────────────
BAND_PAO = "pao"                # 그날 PAO가 실제로 맡고 있던 광고그룹
BAND_NOT_PAO = "not_pao"        # 그날 PAO가 안 맡고 있던 광고그룹
BAND_TRANSITION = "transition"  # 그날 관할이 «중간에» 바뀐 캠페인 — 어느 밴드에도 안 더한다
BAND_UNKNOWN = "unknown"        # 되돌릴 근거가 없는 구간
BANDS = (BAND_PAO, BAND_NOT_PAO, BAND_TRANSITION, BAND_UNKNOWN)

BAND_LABEL = {
    BAND_PAO: "PAO가 돌린 광고",
    BAND_NOT_PAO: "PAO가 안 돌린 광고",
    BAND_TRANSITION: "담당이 바뀐 날",
    BAND_UNKNOWN: "모름(기록 없음)",
}

# 관할을 움직이는 change_log 액션 — 이 셋만 본다.
ACTION_OPTIMIZER = "optimizer_change"
ACTION_AUTO_OPERATE = "auto_operate_change"
ACTION_SCOPE = "adgroup_scope_change"
OWNERSHIP_ACTIONS = (ACTION_OPTIMIZER, ACTION_AUTO_OPERATE, ACTION_SCOPE)

OPTIMIZER_OURS = "ours"
_VALID_OPTIMIZERS = frozenset({"none", "ours", "mop"})

# 설정 행이 아예 없을 때의 기본값 — `campaign_roster`·`adgroup_scope._auto_operate`와 같은 규격
# (행 부재도 False = fail-closed). 두 곳이 다르면 재구성이 라이브와 갈라진다.
_DEFAULT_OPTIMIZER = "none"
_DEFAULT_AUTO_OPERATE = False

_ENABLED_RE = re.compile(r"enabled\s*=\s*(true|false|1|0)", re.IGNORECASE)


@dataclass(frozen=True)
class CampaignState:
    """한 시점의 캠페인 관할 상태.

    scope: {adgroup_id: enabled}. **빈 dict = 스코프 행 없음**(진리표의 「행 없음」 칸)이고,
    값이 있는데 전부 False면 「행 있음, 전부 disabled」라 전 그룹 OFF다 — 둘은 다른 상태다.
    """

    optimizer: str = _DEFAULT_OPTIMIZER
    auto_operate: bool = _DEFAULT_AUTO_OPERATE
    scope: tuple[tuple[str, bool], ...] = ()

    def scope_map(self) -> dict[str, bool]:
        return dict(self.scope)


def group_in_scope(state: CampaignState, adgroup_id: str) -> bool:
    """D-NAO-244 진리표 — `adgroup_scope.blocked_by_scope`의 «시점 상태» 판본.

    | auto_operate | 스코프 행 | 그룹 g |
    |---|---|---|
    | OFF | 무엇이든 | OFF (마스터 킬) |
    | ON  | 없음     | ON (전 그룹) |
    | ON  | 있음, g ∈ enabled | ON |
    | ON  | 있음, g ∉ enabled | OFF |
    """
    if not state.auto_operate:
        return False
    scope = state.scope_map()
    if not scope:  # 행 없음 → 전 그룹 ON
        return True
    return bool(scope.get(adgroup_id, False))


def is_pao_managed(state: CampaignState, adgroup_id: str) -> bool:
    """세 축의 ∧ — 이 함수가 「PAO가 돌린다」의 정의다."""
    return state.optimizer == OPTIMIZER_OURS and group_in_scope(state, adgroup_id)


# ── 값 파서 (포맷 3종) ──────────────────────────────────────────────────────


def _parse_optimizer(value: str | None) -> tuple[dict, bool]:
    """optimizer_change의 before/after → 적용할 필드 dict, 해석 성공 여부.

    None = 설정 행이 없던 상태(기본값으로 되돌린다). JSON이면 auto_operate도 같이 실려 온다.
    """
    if value is None:
        return {"optimizer": _DEFAULT_OPTIMIZER, "auto_operate": _DEFAULT_AUTO_OPERATE}, True
    s = str(value).strip()
    if not s:
        return {"optimizer": _DEFAULT_OPTIMIZER, "auto_operate": _DEFAULT_AUTO_OPERATE}, True
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            return {}, False
        if not isinstance(d, dict):
            return {}, False
        out: dict = {}
        opt = d.get("optimizer")
        if isinstance(opt, str) and opt in _VALID_OPTIMIZERS:
            out["optimizer"] = opt
        if "auto_operate" in d:
            out["auto_operate"] = bool(d["auto_operate"])
        return (out, True) if out else ({}, False)
    if s in _VALID_OPTIMIZERS:
        return {"optimizer": s}, True
    return {}, False


def _parse_auto_operate(value: str | None) -> tuple[dict, bool]:
    """auto_operate_change의 before/after → 적용할 필드 dict, 해석 성공 여부."""
    if value is None:
        return {"auto_operate": _DEFAULT_AUTO_OPERATE}, True
    s = str(value).strip().lower()
    if not s:
        return {"auto_operate": _DEFAULT_AUTO_OPERATE}, True
    if s in ("true", "1"):
        return {"auto_operate": True}, True
    if s in ("false", "0"):
        return {"auto_operate": False}, True
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            return {}, False
        if isinstance(d, dict) and "auto_operate" in d:
            return {"auto_operate": bool(d["auto_operate"])}, True
    return {}, False


def _parse_scope(value: str | None) -> tuple[bool | None, bool]:
    """adgroup_scope_change의 before/after → (enabled | None=행 없음), 해석 성공 여부."""
    if value is None:
        return None, True  # 행이 없던 상태
    s = str(value).strip()
    if not s or s.lower() in ("none", "null"):
        return None, True
    m = _ENABLED_RE.search(s)
    if m:
        return m.group(1).lower() in ("true", "1"), True
    if s.startswith("{"):
        try:
            d = json.loads(s)
        except (ValueError, TypeError):
            return None, False
        if isinstance(d, dict) and "enabled" in d:
            return bool(d["enabled"]), True
    return None, False


def _apply(state: CampaignState, action: str, value: str | None, adgroup_id: str | None):
    """상태에 «그 값이었던 것으로» 되돌리기 1회. → (새 상태, 해석 성공 여부)"""
    if action == ACTION_OPTIMIZER:
        fields, ok = _parse_optimizer(value)
        return (replace(state, **fields) if ok and fields else state), ok
    if action == ACTION_AUTO_OPERATE:
        fields, ok = _parse_auto_operate(value)
        return (replace(state, **fields) if ok and fields else state), ok
    if action == ACTION_SCOPE:
        if not adgroup_id:
            return state, False
        enabled, ok = _parse_scope(value)
        if not ok:
            return state, False
        scope = state.scope_map()
        if enabled is None:
            scope.pop(adgroup_id, None)  # 그 시점엔 행이 없었다
        else:
            scope[adgroup_id] = enabled
        return replace(state, scope=tuple(sorted(scope.items()))), True
    return state, False


# ── 재구성 ──────────────────────────────────────────────────────────────────


class OwnershipTimeline:
    """캠페인별 관할 구간 + 날짜별 밴드 판정. 요청당 1회 만들고 재사용한다."""

    def __init__(
        self,
        *,
        segments: dict[str, list[tuple[datetime | None, CampaignState]]],
        transition_dates: dict[str, set[date_cls]],
        unknown_before: dict[str, date_cls],
        history_start: date_cls | None,
        unparsable_count: int,
        unparsable_samples: list[dict],
    ):
        # segments[campaign] = [(valid_from|None, state), ...] — valid_from 내림차순.
        # None = 「이력 시작까지 거슬러 이 상태」.
        self._segments = segments
        self._transition_dates = transition_dates
        self._unknown_before = unknown_before
        self.history_start = history_start
        self.unparsable_count = unparsable_count
        self.unparsable_samples = unparsable_samples

    # ── 판정 ──
    def state_at(self, campaign_id: str, on: date_cls) -> CampaignState:
        """그 날짜의 관할 상태. 전환일이 아닌 날은 하루 안에서 유일하다."""
        segs = self._segments.get(campaign_id)
        if not segs:
            return CampaignState()
        probe = datetime.combine(on, datetime.min.time()).replace(hour=12)
        for valid_from, state in segs:  # 최신 구간부터
            if valid_from is None or valid_from <= probe:
                return state
        return CampaignState()

    def band(self, on: date_cls, campaign_id: str, adgroup_id: str) -> str:
        """이 (날짜, 캠페인, 광고그룹)이 어느 밴드인가."""
        if self.history_start is None or on < self.history_start:
            return BAND_UNKNOWN
        cutoff = self._unknown_before.get(campaign_id)
        if cutoff is not None and on < cutoff:
            return BAND_UNKNOWN
        if on in self._transition_dates.get(campaign_id, ()):
            return BAND_TRANSITION
        state = self.state_at(campaign_id, on)
        return BAND_PAO if is_pao_managed(state, adgroup_id) else BAND_NOT_PAO

    # ── 진단(화면이 «왜 모름인가»를 말할 수 있게) ──
    def diagnostics(self) -> dict:
        return {
            "history_start": self.history_start.isoformat() if self.history_start else None,
            "unparsable_events": self.unparsable_count,
            "unparsable_samples": self.unparsable_samples,
            "campaigns_with_unknown_tail": {
                cid: d.isoformat() for cid, d in sorted(self._unknown_before.items())
            },
            "transition_days": {
                cid: sorted(d.isoformat() for d in days)
                for cid, days in sorted(self._transition_dates.items())
                if days
            },
        }


def _current_states(db: Session) -> dict[str, CampaignState]:
    """지금 이 순간의 관할 상태(되감기의 출발점)."""
    scope_by_campaign: dict[str, dict[str, bool]] = {}
    for cid, agid, enabled in db.query(
        NaverAdgroupScope.campaign_id, NaverAdgroupScope.adgroup_id, NaverAdgroupScope.enabled
    ).all():
        scope_by_campaign.setdefault(cid, {})[agid] = bool(enabled)

    states: dict[str, CampaignState] = {}
    for cid, optimizer, auto_operate in db.query(
        NaverCampaignSettings.campaign_id,
        NaverCampaignSettings.optimizer,
        NaverCampaignSettings.auto_operate,
    ).all():
        states[cid] = CampaignState(
            optimizer=(optimizer or _DEFAULT_OPTIMIZER),
            auto_operate=bool(auto_operate),
            scope=tuple(sorted(scope_by_campaign.get(cid, {}).items())),
        )
    # 설정 행 없이 스코프 행만 있는 캠페인도 상태를 갖는다(기본값 + 스코프).
    for cid, scope in scope_by_campaign.items():
        states.setdefault(cid, CampaignState(scope=tuple(sorted(scope.items()))))
    return states


def build(db: Session) -> OwnershipTimeline:
    """`naver_change_log`를 되감아 캠페인별 관할 구간을 만든다.

    ★되감기 순서가 규율이다 — 최신 이벤트부터 `before_value`로 되돌려야 구간 경계가 맞는다.
    해석 못 한 이벤트를 만나면 **그 캠페인의 그 지점보다 과거를 전부 unknown으로** 표시한다:
    뒤집지 못한 채 계속 되감으면 틀린 상태를 «확정»으로 내보내게 된다.
    """
    # ★이력 시작점은 «관할 액션의 최초»가 아니라 «로그 전체의 최초»다.
    #   관할 액션의 최초(prod 실측 07-12)를 쓰면, 되감기로 «알 수 있는» 07-11~07-12 구간까지
    #   모름으로 밀어 버린다 — 그 구간은 첫 이벤트를 되돌리면 확정된다.
    #   ⚠️ 이 선택이 기대는 가정: 「로그가 돌기 시작한 시점부터 관할 변경도 함께 기록됐다」.
    #   관할 기록만 나중에 배선됐다면 그 사이 구간을 «안다»고 잘못 말하게 된다. prod 실측은
    #   로그 최초 2026-07-11 07:37 · 첫 관할 이벤트 07-12로 하루 차이라 이 가정이 성립한다.
    history_start_dt = db.query(sqlfunc.min(NaverChangeLog.changed_at)).scalar()
    history_start = history_start_dt.date() if history_start_dt else None

    events = (
        db.query(
            NaverChangeLog.changed_at,
            NaverChangeLog.campaign_id,
            NaverChangeLog.entity_id,
            NaverChangeLog.action,
            NaverChangeLog.before_value,
        )
        .filter(NaverChangeLog.action.in_(OWNERSHIP_ACTIONS))
        .order_by(NaverChangeLog.changed_at.desc(), NaverChangeLog.id.desc())
        .all()
    )

    states = _current_states(db)
    segments: dict[str, list[tuple[datetime | None, CampaignState]]] = {
        cid: [(None, st)] for cid, st in states.items()
    }
    transition_dates: dict[str, set[date_cls]] = {}
    unknown_before: dict[str, date_cls] = {}
    unparsable_count = 0
    unparsable_samples: list[dict] = []

    for changed_at, campaign_id, entity_id, action, before_value in events:
        if not campaign_id or changed_at is None:
            unparsable_count += 1
            if len(unparsable_samples) < 5:
                unparsable_samples.append(
                    {"action": action, "reason": "campaign_id 또는 changed_at 없음"}
                )
            continue

        transition_dates.setdefault(campaign_id, set()).add(changed_at.date())

        cur_segs = segments.setdefault(campaign_id, [(None, CampaignState())])
        # 이 이벤트 시각부터가 «직후 상태»의 구간이다.
        _, state_after = cur_segs[-1]
        cur_segs[-1] = (changed_at, state_after)

        if campaign_id in unknown_before:
            # 이 캠페인은 이미 더 나중의 해석불가 이벤트 때문에 과거가 못 믿을 상태다.
            # 구간 경계는 계속 그어 두되(전환일 표기는 유효) 상태 되감기는 의미가 없다.
            cur_segs.append((None, state_after))
            continue

        state_before, ok = _apply(state_after, action, before_value, entity_id)
        if not ok:
            unparsable_count += 1
            if len(unparsable_samples) < 5:
                unparsable_samples.append(
                    {
                        "action": action,
                        "campaign_id": campaign_id,
                        "changed_at": changed_at.isoformat(),
                        "before_value": (str(before_value)[:120] if before_value is not None else None),
                    }
                )
            # 이 시각보다 «과거»는 못 믿는다. 이벤트 당일까지 포함해 unknown으로 민다.
            unknown_before[campaign_id] = changed_at.date()
            cur_segs.append((None, state_after))
            continue

        cur_segs.append((None, state_before))

    return OwnershipTimeline(
        segments=segments,
        transition_dates=transition_dates,
        unknown_before=unknown_before,
        history_start=history_start,
        unparsable_count=unparsable_count,
        unparsable_samples=unparsable_samples,
    )
