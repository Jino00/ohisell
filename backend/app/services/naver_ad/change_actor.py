# change_actor.py — "이 수정, 누가 했나" 판정 SA (「수정 사항」 화면).
"""역할(SA·단일 책임·읽기 전용): 변경 이벤트 1건 → **주체**. 규칙이 여기 한 곳에만 있다.

★왜 한 곳인가: 주체 판정이 라우터·화면·집계에 각자 복제되면 규칙이 바뀔 때 조용히 어긋나고,
  그때 화면은 "대행사가 했다"고 **단언**한다. 그건 사실 주장이라 틀리면 사람이 엉뚱한 곳에
  전화한다. EXECUTION_ACTIONS를 하드코딩하지 않고 파생시킨 것과 같은 원칙.

★★용어 함정(중요): 이 코드베이스의 `optimizer='mop'`은 **"제3자 소유, 우리가 안 건드림"**
  이라는 뜻이고, Jino가 말하는 "MOP = 우리 시스템"과 **정반대**다. 그래서
  ①화면 어디에도 'MOP'라는 말을 쓰지 않는다 ②`NaverAgencyOp.optimizer`를 주체 판정에
  **쓰지 않는다** — 그 컬럼은 "누가 이 이벤트를 만들었나"가 아니라 "그 캠페인의 관리주체
  설정"이다(모델 docstring: 조작 주체 구분이라 적혀 있지만 실제 값은 캠페인 설정에서 복사된다).
  주체를 그 컬럼으로 판정하면 "우리가 관리하는 캠페인을 대행사가 만졌다"가 **우리 것**으로 뒤집힌다.

── 판정 5규칙 (기본값은 대행사, 아래가 적용 순서다) ───────────────────────
  ① change_log의 `external_*` 4종(EXTERNAL_DETECTION_ACTIONS) = 외부가 바꾼 걸 우리가 관측 → agency
  ② change_log의 나머지(우리 실집행·우리 시스템 내부 설정)                     → ours
  ③ agency_op 전량(정의상 외부. 스냅샷 diff·소재 editTm 앵커 둘 다)            → agency
  ⑤ ①③으로 agency가 된 행도 **우리 실집행과 대조되면** 되찾는다              → ours
     (탐지가 하루 1회라 우리가 낮에 한 일이 다음날 아침 "외부 변경"으로 보인다 — 아래 상세)
  ④ 정정 테이블(naver_change_actor_override)에 값이 있으면 **그것이 이긴다**
그래서 "우리가 수정한 게 아니면 대행사"라는 Jino의 기본값이 데이터 쪽에서도 기본값이다.
Jino 본인 조작은 드물어 자동 판정 대상이 아니다 — ④로 사람이 단다.

★rationale 문자열로 Jino를 자동 추정하지 않는다. prod에는 `manual_emergency_stop` 처럼
  rationale에 "Jino 직접 지시"가 적힌 행이 실제로 있지만(2026-07-30 5건), 그건 **우리 시스템이
  Jino 지시를 받아 집행한 것**이지 Jino가 네이버 콘솔에서 직접 만진 것이 아니다. 둘을 섞으면
  "우리 자동화가 한 일"의 집계가 조용히 줄어든다. 문자열 추측은 판정이 아니라 점(占)이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverChangeActorOverride, NaverChangeLog
from app.services.naver_ad import naver_execution_harness

# ── 주체 코드 ────────────────────────────────────────────────────────
ACTOR_OURS = "ours"
ACTOR_AGENCY = "agency"
ACTOR_JINO = "jino"

#: 정정으로 지정 가능한 값(= 화면 드롭다운). 자동 판정도 이 안에서만 나온다.
ACTORS: tuple[str, ...] = (ACTOR_OURS, ACTOR_AGENCY, ACTOR_JINO)

#: 화면 라벨. ★'MOP'라는 말은 쓰지 않는다(위 용어 함정).
ACTOR_LABEL: dict[str, str] = {
    ACTOR_OURS: "우리 자동화",
    ACTOR_AGENCY: "대행사",
    ACTOR_JINO: "Jino",
}

# ── 원천 코드 ────────────────────────────────────────────────────────
SOURCE_CHANGE_LOG = "change_log"
SOURCE_AGENCY_OP = "agency_op"
SOURCES: tuple[str, ...] = (SOURCE_CHANGE_LOG, SOURCE_AGENCY_OP)

SOURCE_LABEL: dict[str, str] = {
    SOURCE_CHANGE_LOG: "변경 이력",
    SOURCE_AGENCY_OP: "외부 조작 관측",
}


def classify_change_log(action: str | None) -> str:
    """규칙 ①② — change_log 행의 자동 주체."""
    if action and action in naver_execution_harness.EXTERNAL_DETECTION_ACTIONS:
        return ACTOR_AGENCY
    return ACTOR_OURS


def classify_agency_op() -> str:
    """규칙 ③ — naver_agency_op은 정의상 전부 외부다(bm_diff·ad_external_change 둘 다).

    인자를 받지 않는 것이 계약이다: 여기서 `optimizer`를 보고 싶어지는 순간이 바로
    위 용어 함정에 빠지는 순간이라, 애초에 볼 수 없게 만든다.
    """
    return ACTOR_AGENCY


def classify(source: str, *, action: str | None = None) -> str:
    """원천별 자동 판정 진입점(규칙 ①②③). 모르는 원천은 보수적으로 대행사(기본값)."""
    if source == SOURCE_CHANGE_LOG:
        return classify_change_log(action)
    return classify_agency_op()


# ── 규칙 ⑤ — 우리 실집행과 대조되면 되찾는다 ─────────────────────────
# 왜 필요한가 (2026-08-09 라이브 실증):
#   2026-07-30 10:50에 **우리가** Jino 지시로 캠페인 5개를 정지했다(`manual_emergency_stop`).
#   entity_sync는 하루 1회(07:37)만 도는데, 다음날 아침 스냅샷이 "꺼져 있네"를 보고
#   `external_status_change`로 적었고 규칙 ①이 그걸 **대행사**로 보냈다. prod 화면에서
#   04·15·P 캠페인 3줄이 그렇게 "대행사가 껐다"로 서 있었다.
#   즉 규칙 ①의 전제("external_* = 외부가 바꾼 것")가 **탐지 지연** 때문에 깨진다.

# ★되찾기는 증거로만 한다. 문자열 추측·시간 근접만으로는 안 되고 셋이 다 맞아야 한다:
#   ①같은 대상(entity_id) ②같은 축(status/bid) ③**같은 결과값**. 값까지 같아야 하는 이유는,
#   우리가 끈 것을 대행사가 되켠 경우(값이 반대)를 우리 것으로 삼키면 안 되기 때문이다.

# ★창은 시각의 근거에 따라 다르다 — 이게 정확도의 대부분이다:
#   · `detected`(우리가 알아챈 시각만 아는 행) = 36시간. 탐지가 하루 1회라 실제 간극이
#     24시간에 육박하고(이번 사례 20.8시간), 크론이 밀려도 견디게 여유를 뒀다.
#   · `occurred`(네이버 editTm에서 온 진짜 발생 시각) = 10분. 언제 일어났는지 아는데도
#     36시간을 열어두면, 우리가 어제 넣은 값과 우연히 같은 값을 오늘 대행사가 넣었을 때
#     우리 것으로 뺏는다. 아는 만큼만 좁힌다.

# ★못 찾으면 현행 유지(=대행사)다. fail-safe 방향은 "덜 되찾기"다 — 크론이 이틀 밀리면
#   되찾지 못할 뿐, 틀린 주장을 하지는 않는다.
# ★사람 정정(규칙 ④)이 이것보다 위다. 순서: ①②③ → ⑤ → ④.


AXIS_STATUS = "status"
AXIS_BID = "bid"

#: 축별로 "우리 실집행"에 해당하는 change_log action. 여기에 없는 action은 증거가 되지 않는다.
OURS_ACTIONS_BY_AXIS: dict[str, frozenset[str]] = {
    AXIS_STATUS: frozenset({"set_user_lock", "manual_emergency_stop"}),
    AXIS_BID: frozenset({"update_bid"}),
}

#: 시각 근거별 매칭 창(위 docstring 참조). 키는 modification_feed의 TIME_BASIS_* 값.
EVIDENCE_WINDOW: dict[str, timedelta] = {
    "detected": timedelta(hours=36),
    "occurred": timedelta(minutes=10),
}

_OPS_LABEL = {
    "set_user_lock": "정지·재개",
    "manual_emergency_stop": "긴급 정지",
    "update_bid": "입찰가 변경",
}


@dataclass(frozen=True)
class OursExecution:
    """증거 1건 — 우리가 실제로 집행한 변경."""

    change_log_id: int
    at: datetime
    action: str
    #: `axis_value()`가 준 (축, 정규값). 축이 값에 묶여 있어 축이 다르면 애초에 같을 수 없다.
    value: tuple[str, object]


def axis_value(axis: str | None, raw: str | None) -> tuple[str, object] | None:
    """행의 after 값을 축의 **정규형** `(축, 값)`으로. 모르면 None(= 증거로 못 씀).

    ★왜 축을 값에 묶어 돌려주나(적대 리뷰 2026-08-09): 파이썬에서 `True == 1`이라,
      값만 비교하면 **입찰 1원**과 **상태 정지**가 같은 값이 된다. 축이 값의 일부면
      그 충돌이 구조적으로 불가능해지고, "같은 축이어야 한다"가 비교식 안에 들어간다
      (규칙을 지키는 코드와 규칙을 검사하는 코드가 갈라지지 않는다).

    ★원천마다 방언이 다르다: change_log는 JSON(`{"userLock": true}` 또는 엔티티 통짜),
      agency_op은 평문(`'off'`, `'1500'`). 소재(ad)의 입찰은 한 겹 더 안쪽(`adAttr` 문자열)에
      있다. 여기서 전부 흡수해 비교는 한 가지 형태로만 한다 — 방언 분기가 호출자로 새면
      "값이 같다"의 정의가 화면마다 달라진다.
    ★status는 bool(정지됨)로 접는다: `userLock=true` ⇔ `'off'`.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    payload: object | None = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return None

    if axis == AXIS_STATUS:
        paused: bool | None = None
        if isinstance(payload, dict):
            if "userLock" in payload:
                paused = bool(payload["userLock"])
        else:
            low = text.lower()
            if low in ("off", "paused", "false"):
                paused = True
            elif low in ("on", "eligible", "true"):
                paused = False
        return None if paused is None else (AXIS_STATUS, paused)

    if axis == AXIS_BID:
        bid: int | None = None
        if isinstance(payload, dict):
            if payload.get("bidAmt") is not None:
                bid = _as_int(payload["bidAmt"])
            else:
                # 소재(ad) — 실효 입찰은 adAttr 안(문자열 또는 dict)에 있다(D-NAO-B1).
                attr = payload.get("adAttr")
                if isinstance(attr, str):
                    try:
                        attr = json.loads(attr)
                    except (ValueError, TypeError):
                        attr = None
                if isinstance(attr, dict) and attr.get("bidAmt") is not None:
                    bid = _as_int(attr["bidAmt"])
        else:
            bid = _as_int(text)
        return None if bid is None else (AXIS_BID, bid)

    return None


def _as_int(value: object) -> int | None:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def load_ours_executions(
    db: Session, entity_ids: set[str], *, axes: set[str], since: datetime, until: datetime
) -> dict[tuple[str, str], list[OursExecution]]:
    """규칙 ⑤의 원료 — {(entity_id, axis): [우리 실집행…]}.

    ★`dry_run=False` **그리고** `after_value is not None`만 증거다: 가드레일이 막아 실제로는
      안 바뀐 시도는 광고를 바꾸지 않았으므로 외부 변경을 설명하지 못한다(이 화면이
      `include_blocked`로 가르는 것과 같은 기준).
    ★`axes`로 action을 좁히는 건 성능이 아니라 **로드량** 때문이다(적대 리뷰 P2-2): 우리
      실집행 행의 `after_value`는 소재 하나가 4KB짜리 엔티티 JSON이라, 365일 구간에서
      전 축을 다 끌어오면 `_Candidate` 2패스가 피하려던 «수십 MB»를 다른 쿼리로 되살린다.
      상태 변경만 감지된 구간에서 입찰 집행 수천 건을 읽을 이유가 없다.
    """
    if not entity_ids or not axes:
        return {}
    actions = tuple(
        a for axis, group in OURS_ACTIONS_BY_AXIS.items() if axis in axes for a in group
    )
    if not actions:
        return {}
    out: dict[tuple[str, str], list[OursExecution]] = {}
    ids = list(entity_ids)
    for i in range(0, len(ids), 500):  # SQLite 바인드 상한 회피
        rows = (
            db.query(
                NaverChangeLog.id,
                NaverChangeLog.changed_at,
                NaverChangeLog.action,
                NaverChangeLog.entity_id,
                NaverChangeLog.after_value,
            )
            .filter(
                NaverChangeLog.entity_id.in_(ids[i : i + 500]),
                NaverChangeLog.action.in_(actions),
                NaverChangeLog.dry_run.is_(False),
                NaverChangeLog.after_value.isnot(None),
                NaverChangeLog.changed_at >= since,
                NaverChangeLog.changed_at <= until,
            )
            .all()
        )
        for cid, changed_at, action, entity_id, after_value in rows:
            if changed_at is None or not entity_id:
                continue
            for axis, actions in OURS_ACTIONS_BY_AXIS.items():
                if action not in actions:
                    continue
                value = axis_value(axis, after_value)
                if value is None:
                    continue  # 값을 못 읽으면 증거가 아니다(추측 금지)
                out.setdefault((entity_id, axis), []).append(
                    OursExecution(change_log_id=cid, at=changed_at, action=action, value=value)
                )
    return out


def reclaim_ours(
    auto_actor: str,
    *,
    axis: str | None,
    entity_id: str,
    at: datetime,
    time_basis: str,
    after_value: str | None,
    executions: dict[tuple[str, str], list[OursExecution]],
) -> tuple[str, str | None]:
    """규칙 ⑤ — (주체, 근거 문장). 증거가 없으면 입력을 **그대로** 돌려준다."""
    if auto_actor != ACTOR_AGENCY or not axis or not entity_id:
        return auto_actor, None
    observed = axis_value(axis, after_value)
    if observed is None:
        return auto_actor, None
    window = EVIDENCE_WINDOW.get(time_basis)
    if window is None:
        return auto_actor, None
    best: OursExecution | None = None
    for ours in executions.get((entity_id, axis), ()):
        if ours.value != observed:
            continue
        if not (at - window <= ours.at <= at):
            continue
        if best is None or ours.at > best.at:
            best = ours  # 가장 가까운 집행을 근거로 삼는다
    if best is None:
        return auto_actor, None
    label = _OPS_LABEL.get(best.action, best.action)
    return ACTOR_OURS, (
        f"우리 실집행과 대조됨 — {best.at:%m-%d %H:%M} {label}"
        f"(변경 이력 #{best.change_log_id}, 같은 대상·같은 값)"
    )


def load_overrides(
    db: Session, keys: set[tuple[str, int]]
) -> dict[tuple[str, int], NaverChangeActorOverride]:
    """규칙 ④의 원료 — {(source, source_id): 정정행} 배치 조회(N+1 금지).

    ★쿼리는 원천 개수만큼(≤2)이다. 튜플 IN은 SQLite에서 지원되지만 백엔드마다 계획이
    달라 원천별 `source_id IN (...)`으로 나눈다 — 호출자가 limit을 강제하므로 목록은 유한하다.
    """
    if not keys:
        return {}
    out: dict[tuple[str, int], NaverChangeActorOverride] = {}
    by_source: dict[str, list[int]] = {}
    for src, sid in keys:
        by_source.setdefault(src, []).append(sid)
    for src, ids in by_source.items():
        rows = (
            db.query(NaverChangeActorOverride)
            .filter(
                NaverChangeActorOverride.source == src,
                NaverChangeActorOverride.source_id.in_(ids),
            )
            .all()
        )
        for r in rows:
            out[(r.source, r.source_id)] = r
    return out


def resolve(
    auto_actor: str, override: NaverChangeActorOverride | None
) -> tuple[str, bool, str | None]:
    """규칙 ④ — (최종 주체, 정정됨 여부, 메모).

    ★정정이 자동 판정과 **같은 값**이어도 corrected=True다. 사람이 "확인했다"고 표시한 것과
    아무도 안 본 것은 다른 상태이고, 화면이 그 둘을 같게 그리면 검토 이력이 사라진다.
    """
    if override is None:
        return auto_actor, False, None
    return override.actor, True, override.note
