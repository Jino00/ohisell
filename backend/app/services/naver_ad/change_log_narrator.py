# change_log_narrator.py — 변경 이력 1건 → "사람이 읽는 한 문장" SA (D-NAO-104 Phase 1).
"""역할(SA·단일 책임·읽기 전용): naver_change_log 행을 받아 **문장만** 만든다. 어떤 행을
보여줄지(필터·기간·actor)는 하니스/라우터가 정하고, 이 모듈은 "무슨 일이 있었는지"를
D-NAO-103 규칙대로 옮겨 적기만 한다.

D-NAO-103 3원칙을 이 파일이 지키는 방식:
  ① **ID 금지** — 캠페인·그룹·키워드는 naver_entity 이름으로(alert_humanizer). 소재(ad)는
     naver_entity에 행 자체가 없으므로(entity_sync는 campaign/adgroup/keyword만 적재)
     naver_adgroup_product의 상품명 → 소속 그룹명 순으로 폴백한다. 그래도 못 찾으면
     '상품 소재'라고만 쓴다 — **ID를 화면에 흘리느니 덜 구체적인 게 낫다**.
  ② **내부 용어 금지** — rationale의 `[탐색UP]`·`[shopping_group_bep]`·D-NAO 코드·
     `target_bid×10` 같은 표기는 한 글자도 내보내지 않는다. 사유는 아래 사전으로만 말한다.
  ③ **숫자 나열이 아니라 문장** — "무슨 일 → 숫자 → 왜" 순서 고정.

★차단(가드레일 거부)도 "오늘 한 일"이다(D-NAO-54 실측: 입찰 시도 4건 중 2건 차단인데 어느
  화면에도 안 떴다). 다만 **집행과 차단을 절대 같은 말로 쓰지 않는다** — 차단은 광고가
  바뀌지 않았다는 뜻이다. 쓰기 실패("모름")는 세 번째 상태로 따로 말한다(원칙22).

★이 모듈은 naver_ad의 다른 판정 SA를 import하지 않는다. 예외는 alert_humanizer 하나
  (계획서 §2 명시 — DB 쓰기·판정·흐름 개입이 전혀 없는 순수 변환기라 사실상 유틸리티).
"""
from __future__ import annotations

import json
import re
from typing import Protocol, Sequence

from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverChangeLog
from app.services.naver_ad import alert_humanizer

# ── 실행 3상태(라우터 `_execution_state`와 같은 의미. 여기선 문장 톤을 가르는 축) ──
STATE_EXECUTED = "executed"  # 광고가 실제로 바뀌었다(writer가 재조회 응답을 after에 실었다)
STATE_BLOCKED = "blocked"    # 사전 가드 거부 — writer를 부르지도 않았다(확실히 안 바뀜)
STATE_UNKNOWN = "unknown"    # 쓰기 실패 — PUT을 이미 보낸 뒤일 수 있어 반영 여부를 모른다

# 실패 rationale 접두사. naver_execution_harness의 상수와 같은 문자열이지만 **의도적으로
# 복제**한다 — 이 SA가 실행 하니스를 import하면 순수 변환기가 실행 경로에 묶인다(원칙18-6).
# 값이 갈라지면 테스트가 잡는다(test_naver_ad_performance_today.py::test_marker_constants_match).
GUARD_BLOCK_MARKER = "[실행 불가]"
WRITE_FAILURE_MARKER = "[실행 실패]"

# rationale 머리의 내부 태그 → 사람 말 사유. 태그는 각 레인이 자기 로그를 식별하려고 붙인
# 내부 표식이라 화면에 그대로 나가면 안 된다(D-NAO-103 ②). 모르는 태그는 사유를 **생략**한다
# — 지어내지 않는다.
_REASON_BY_TAG = {
    "탐색UP": "반응이 나오는 자리를 찾는 중입니다",
    "탐색UP·재가동": "예전에 반응이 있던 자리라 다시 시도합니다",
    "탐색": "반응이 나오는 자리를 찾는 중입니다",
    "콜드첫입찰": "새로 시작하는 광고의 첫 입찰입니다",
    "shopping_group_bep": "남는 선까지 입찰을 낮췄습니다",
    "shopping_group_growth": "성과가 좋아 더 키웁니다",
    "shopping_pause_candidates": "성과가 없어 정리합니다",
    "pause_candidates": "성과가 없어 정리합니다",
    "bleeding_keywords": "손해가 나고 있어 줄입니다",
    "starving_winners": "잘 팔리는데 노출이 모자라 늘립니다",
    "예산봉투": "그동안의 지출 규모에 맞춰 예산을 조정합니다",
    "예산페이싱": "예산이 일찍 바닥날 것 같아 늘립니다",
    "예산페이싱복원": "어제 늘렸던 예산을 원래대로 되돌립니다",
    "EX확장": "목표를 넘고 있어 노출을 넓힙니다",
    "시간당밴드": "노출 순위를 목표 구간으로 되돌립니다",
    "순위고삐": "노출 순위가 너무 높아 비용을 줄입니다",
    "스톱로스고삐": "전환 없이 지출이 커져 입찰을 줄입니다",
    "고삐전환": "손실이 커져 잠시 멈춥니다",
    "터미널정지": "회복 신호가 없어 정지합니다",
    "스파이럴복원": "급격히 나빠진 뒤 원래 값으로 되돌립니다",
    "클릭탐침": "클릭이 붙는지 확인하려고 조금 올려봅니다",
    "검색어제외": "성과가 없는 검색어를 제외합니다",
    "검색어승격": "성과가 좋은 검색어를 키웁니다",
    "제외": "성과가 없는 검색어를 제외합니다",
    "수동": "사람이 직접 지시했습니다",
}

# 가드레일 거부 사유(원문은 `스톱로스 도달 — 무전환 지출 873원 ≥ 상한 600원(D-NAO-20,
# target_bid×10)` 같은 내부 문자열) → 사람 말. **부분 문자열 매칭 순서가 계약이다** —
# 먼저 걸리는 규칙이 이긴다. 어느 것에도 안 걸리면 원문을 노출하지 않고 총칭으로 말한다
# (D-NAO 코드·변수명이 화면에 새는 것보다 덜 구체적인 편이 낫다).
_BLOCK_REASON_RULES: tuple[tuple[str, str], ...] = (
    ("스톱로스", "아직 전환이 없는데 지출이 한도에 닿아 더 올리지 않았습니다"),
    ("BEP 미달", "지금 성과가 손익분기 아래라 증액을 막았습니다"),
    ("BEP 검증 불가", "손익 판단에 필요한 값을 못 구해 증액을 보류했습니다"),
    ("일예산 상한", "오늘 예산을 이미 다 써서 올리지 않았습니다"),
    ("예산 증액폭", "한 번에 늘릴 수 있는 예산 폭을 넘어 막았습니다"),
    ("변경폭", "한 번에 바꿀 수 있는 폭을 넘어 막았습니다"),
    ("쿨다운", "최근에 이미 바꿔서 잠시 쉬는 중입니다"),
    ("일일 변경 건수", "하루에 바꿀 수 있는 횟수를 다 써서 멈췄습니다"),
    ("방향 불일치", "지시한 방향과 계산된 값이 어긋나 실행하지 않았습니다"),
    ("유효 범위 밖", "허용 범위를 벗어난 값이라 실행하지 않았습니다"),
    ("미확보", "판단에 필요한 현재 값을 못 구해 보류했습니다"),
)
_BLOCK_REASON_FALLBACK = "안전장치가 막았습니다"

_TAG_RE = re.compile(r"^\s*\[([^\]]{1,24})\]")


def _first_tag(rationale: str | None) -> str | None:
    """rationale 머리의 `[태그]` 1개. 없으면 None(사유 생략)."""
    m = _TAG_RE.match(rationale or "")
    return m.group(1) if m else None


def _loads(raw: str | None) -> dict | None:
    """before/after_value 파싱 — 쓰레기여도 예외 대신 None(라우터 `_loads_or_none`과 동형)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _bid_of(value: dict | None) -> int | None:
    """실효 입찰가. 그룹·키워드는 최상위 `bidAmt`, 소재는 `adAttr.bidAmt`(B1 실측 스키마)."""
    if not value:
        return None
    attr = value.get("adAttr")
    if isinstance(attr, dict) and isinstance(attr.get("bidAmt"), (int, float)):
        return int(attr["bidAmt"])
    raw = value.get("bidAmt")
    return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def _budget_of(value: dict | None) -> int | None:
    raw = (value or {}).get("dailyBudget")
    return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def _lock_of(value: dict | None) -> bool | None:
    raw = (value or {}).get("userLock")
    return bool(raw) if isinstance(raw, bool) else None


def _won(n: int | None) -> str:
    return f"{alert_humanizer.count(n)}원" if n is not None else "알 수 없는 금액"


def _eul_reul(word: str) -> str:
    """받침 유무로 목적격 조사 선택('입찰 조정'→을 / '검색어 제외'→를). 한글이 아니면 '을'.
    문장이 기계 번역체로 읽히면 D-NAO-103의 '문장으로 쓴다'가 무너지므로 조사까지 맞춘다."""
    if not word:
        return "을"
    code = ord(word[-1]) - 0xAC00
    if 0 <= code <= 11171:
        return "을" if code % 28 else "를"
    return "을"


def execution_state(row: NaverChangeLog) -> str:
    """행 1건의 3상태. 라우터 `_execution_state`와 같은 판별 규칙(after_value 존재 → 집행,
    실패 행은 rationale 접두사로 가드거부/쓰기실패를 가른다. **쓰기 실패를 먼저 본다** —
    제안 원문에 '[실행 불가]'가 들어 있으면 쓰기 실패가 차단으로 오판되고, 그게 정확히
    "반영됐을 수도 있는데 확실히 안 바뀜이라 단언"하는 거짓말이다)."""
    if row.after_value is not None:
        return STATE_EXECUTED
    rationale = row.rationale or ""
    if WRITE_FAILURE_MARKER in rationale:
        return STATE_UNKNOWN
    if GUARD_BLOCK_MARKER in rationale:
        return STATE_BLOCKED
    return STATE_UNKNOWN  # 접두사 없음 = 판별 불가 → 모름(보수)


# 방향(올림/내림)이라는 축이 있는 액션. 정지/재개·검색어 제외는 '올렸다/내렸다'가 성립하지
# 않는다 — 여기 없는 액션에 방향을 붙이면 화면이 없는 사실을 말하게 된다.
_DIRECTIONAL_ACTIONS = frozenset({
    "update_bid", "update_budget", "budget_up_pacing", "budget_down_pacing",
})


def change_direction(row: NaverChangeLog) -> int:
    """+1 상향 / -1 하향 / 0 방향 없음(또는 값 부재로 단언 불가).

    ★공개 함수다(D-NAO-105 Phase 2): 그룹 상태 배지가 "최근에 올렸나"를 판단할 때 쓴다.
    before/after 파싱 규약(소재는 adAttr.bidAmt)이 이 모듈에만 있으므로 여기 둔다 — 밖에서
    다시 파싱하면 소재-레벨 입찰(쇼핑의 실제 레버, 실측 96%)을 통째로 놓친다."""
    if row.action not in _DIRECTIONAL_ACTIONS:
        return 0
    before, after = _loads(row.before_value), _loads(row.after_value)
    if row.action == "update_bid":
        b, a = _bid_of(before), _bid_of(after)
    else:
        b, a = _budget_of(before), _budget_of(after)
    if b is None or a is None or a == b:
        return 0
    return 1 if a > b else -1


def _lock_text(v: bool) -> str:
    return "정지" if v else "가동"


#: 액션별로 **의미 있는 필드**가 무엇인지. 순서 탐색만으로는 안 된다(라이브 실측):
#: 캠페인 정지 행(`manual_emergency_stop`)의 payload에는 dailyBudget도 들어 있어서
#: "입찰→예산→정지" 순으로 찾으면 **"50,000원 → 50,000원"**이 표시된다 —
#: 광고를 멈춘 사건인데 화면은 "아무것도 안 바뀜"이라고 말한다.
_VALUE_FIELD_BY_ACTION: dict[str, str] = {
    "update_bid": "bid",
    "external_bid_change": "bid",
    "external_keyword_added": "bid",
    "external_keyword_removed": "bid",
    "update_budget": "budget",
    "budget_up_pacing": "budget",
    "budget_down_pacing": "budget",
    "set_user_lock": "lock",
    "manual_emergency_stop": "lock",
    "external_status_change": "lock",
}

_EXTRACTORS: dict[str, tuple] = {
    "bid": (_bid_of, _won),
    "budget": (_budget_of, _won),
    "lock": (_lock_of, _lock_text),
}


def changed_values(row: NaverChangeLog) -> tuple[str | None, str | None]:
    """(이전값, 이후값) 표시 문자열. 못 구하면 **None** — 0이나 빈칸으로 채우지 않는다.

    ★공개 함수인 이유는 change_direction과 같다: before/after 파싱 규약(소재는
    `adAttr.bidAmt`, 예산은 `dailyBudget`, 정지는 `userLock`)이 이 모듈에만 있고, 밖에서
    다시 파싱하면 소재-레벨 입찰(쇼핑의 실제 레버, 실측 96%)을 통째로 놓친다.

    ★한쪽만 있는 경우가 실제로 있다(entity_sync의 키워드 삭제 감지는 before만 채운다) —
    그때 없는 쪽은 None이다. 호출자가 '관측 공백'이라고 **말하게** 하려는 것이지,
    같은 값으로 메우거나 0으로 채우려는 게 아니다.

    ★모르는 액션은 **실제로 달라진 필드**를 고른다(순서상 첫 번째가 아니라). 안 그러면
    payload에 우연히 들어 있는 무변동 필드가 뽑혀 "안 바뀌었다"고 말하게 된다.
    """
    before, after = _loads(row.before_value), _loads(row.after_value)

    def rendered(kind: str) -> tuple[str | None, str | None]:
        extract, fmt = _EXTRACTORS[kind]
        b, a = extract(before), extract(after)
        return (fmt(b) if b is not None else None, fmt(a) if a is not None else None)

    field = _VALUE_FIELD_BY_ACTION.get(row.action)
    if field:
        return rendered(field)

    fallback: tuple[str | None, str | None] | None = None
    for kind in ("bid", "budget", "lock"):
        b, a = rendered(kind)
        if b is None and a is None:
            continue
        if b != a:
            return b, a  # 실제로 달라진 필드 — 이게 그 행이 말하려는 변화다
        if fallback is None:
            fallback = (b, a)
    return fallback or (None, None)


def block_reason(rationale: str | None) -> str:
    """가드 거부 사유를 사람 말로. 내부 원문(D-NAO 코드·변수명)은 절대 그대로 안 내보낸다.

    ★공개 함수다(D-NAO-105 Phase 2): 그룹 상태 배지(group_state_badge)의 '차단됨' 사유도
    같은 사전을 써야 한다. 두 벌로 번역하면 같은 차단이 화면마다 다른 말로 나온다."""
    text = rationale or ""
    tail = text.split(GUARD_BLOCK_MARKER, 1)[1] if GUARD_BLOCK_MARKER in text else text
    for needle, human in _BLOCK_REASON_RULES:
        if needle in tail:
            return human
    return _BLOCK_REASON_FALLBACK


class HasEntity(Protocol):
    """이름 해석에 필요한 최소 표면. NaverChangeLog·NaverAgencyOp 둘 다 만족한다 —
    「수정 사항」 화면이 두 원천을 한 표에 그리므로 이름 해석도 한 곳이어야 한다
    (두 벌로 해석하면 같은 소재가 화면마다 다른 이름으로 나온다)."""

    entity_type: str
    entity_id: str


def resolve_labels(db: Session, rows: Sequence[HasEntity]) -> dict[tuple[str, str], str]:
    """{(entity_type, entity_id): 표시 이름} 배치 해석(쿼리 수는 엔티티 종류 수로 유한).

    ★소재(ad)는 naver_entity에 없다 — naver_adgroup_product(소재 sync)의 상품명, 없으면
    소속 그룹 이름으로 폴백한다. 검색어(search_term)는 entity_id 자체가 검색어 텍스트다."""
    labels: dict[tuple[str, str], str] = {}
    by_type: dict[str, list[str]] = {}
    for r in rows:
        if r.entity_type and r.entity_id and r.entity_id != "__bulk__":
            by_type.setdefault(r.entity_type, []).append(r.entity_id)

    for etype, ids in by_type.items():
        if etype in ("campaign", "adgroup", "keyword"):
            for eid, name in alert_humanizer.entity_names(db, etype, ids).items():
                # entity_names의 폴백은 **ID**다 — ID가 그대로 화면에 나가는 것이 D-NAO-103①
                # 위반이므로 여기서 걸러낸다(호출부가 유형별 총칭으로 대체).
                if name and name != eid:
                    labels[(etype, eid)] = name
        elif etype == "search_term":
            for eid in ids:
                labels[(etype, eid)] = eid  # 검색어 자체가 사람이 읽는 말
        elif etype == "ad":
            ads = (
                db.query(NaverAdgroupProduct)
                .filter(NaverAdgroupProduct.ad_id.in_(list(dict.fromkeys(ids))))
                .all()
            )
            group_names = alert_humanizer.entity_names(
                db, "adgroup", [a.adgroup_id for a in ads if a.adgroup_id]
            )
            for a in ads:
                name = alert_humanizer.clean_name(a.product_name)
                if not name:
                    gname = group_names.get(a.adgroup_id or "")
                    name = gname if gname and gname != a.adgroup_id else ""
                if name and a.ad_id:
                    labels[("ad", a.ad_id)] = name
    return labels


_GENERIC_LABEL = {
    "campaign": "이 광고",
    "adgroup": "광고그룹",
    "keyword": "키워드",
    "ad": "상품 소재",
    "search_term": "검색어",
}


def _target_label(row: NaverChangeLog, labels: dict[tuple[str, str], str]) -> str:
    """대상 표시. **캠페인 자신이 대상이면 빈 문자열**이다 — 문장 머리에 이미 캠페인 이름이
    있어서 그대로 두면 '‘03. 아이폰_강화유리’ — ‘03. 아이폰_강화유리’ 하루 예산…'처럼 같은
    이름을 두 번 읽게 된다(라이브 실측)."""
    if row.entity_type == "campaign" and row.entity_id == row.campaign_id:
        return ""
    name = labels.get((row.entity_type, row.entity_id))
    if name:
        return f"‘{name}’"
    return _GENERIC_LABEL.get(row.entity_type, "광고")


def _core_phrase(row: NaverChangeLog, state: str) -> str:
    """행위 문구 — '무엇을 얼마에서 얼마로 어떻게 했다'. 값이 없으면 방향을 단언하지 않는다."""
    before, after = _loads(row.before_value), _loads(row.after_value)
    action = row.action

    if action == "update_bid":
        b, a = _bid_of(before), _bid_of(after)
        if state != STATE_EXECUTED:
            return "입찰 조정"
        if b is not None and a is not None and b != a:
            verb = "올렸습니다" if a > b else "내렸습니다"
            return f"입찰을 {_won(b)}에서 {_won(a)}으로 {verb}"
        if a is not None:
            return f"입찰을 {_won(a)}으로 맞췄습니다"
        return "입찰을 조정했습니다"

    if action in ("update_budget", "budget_up_pacing", "budget_down_pacing"):
        b, a = _budget_of(before), _budget_of(after)
        if state != STATE_EXECUTED:
            return "하루 예산 조정"
        if b is not None and a is not None and b != a:
            verb = "늘렸습니다" if a > b else "줄였습니다"
            return f"하루 예산을 {_won(b)}에서 {_won(a)}으로 {verb}"
        if a is not None:
            return f"하루 예산을 {_won(a)}으로 맞췄습니다"
        return "하루 예산을 조정했습니다"

    if action == "set_user_lock":
        lock = _lock_of(after)
        if state != STATE_EXECUTED:
            return "정지/재개 조정"
        if lock is True:
            return "광고를 멈췄습니다"
        if lock is False:
            return "광고를 다시 켰습니다"
        return "정지 상태를 바꿨습니다"

    if action in ("add_negative_keyword", "exclude_search_term"):
        if state != STATE_EXECUTED:
            return "검색어 제외"
        return "이 검색어에 광고가 나가지 않게 막았습니다"

    return "설정을 바꿨습니다" if state == STATE_EXECUTED else "설정 변경"


def narrate(
    db: Session, rows: list[NaverChangeLog], labels: dict[tuple[str, str], str] | None = None
) -> list[dict]:
    """행 목록 → 시간 오름차순 문장 목록.

    반환 1건: {at, time_label, state, campaign_name, sentence}. 문장 골격은
      "13:20 · 03. 아이폰_강화유리 — ‘12프로’ 입찰을 270원에서 300원으로 올렸습니다(반응이 …)."
    차단·모름은 같은 골격에 **다른 동사**를 쓴다 — 집행과 절대 같은 말로 쓰지 않는다.
    """
    if labels is None:
        labels = resolve_labels(db, rows)
    camp_names = alert_humanizer.entity_names(
        db, "campaign", [r.campaign_id for r in rows if r.campaign_id]
    )

    out: list[dict] = []
    for row in sorted(rows, key=lambda r: (r.changed_at is None, r.changed_at)):
        state = execution_state(row)
        cid = row.campaign_id or ""
        cname = camp_names.get(cid)
        if not cname or cname == cid:
            cname = None  # ID 폴백 금지(D-NAO-103①)
        target = _target_label(row, labels)
        phrase = _core_phrase(row, state)
        tag_reason = _REASON_BY_TAG.get(_first_tag(row.rationale) or "")

        prefix = f"{target} " if target else ""
        if state == STATE_EXECUTED:
            body = f"{prefix}{phrase}"
            if tag_reason:
                body += f"({tag_reason})"
            body += "."
        elif state == STATE_BLOCKED:
            body = (
                f"{prefix}{phrase}{_eul_reul(phrase)} 하려다 멈췄습니다 "
                f"— {block_reason(row.rationale)}."
            )
        else:
            body = (
                f"{prefix}{phrase}{_eul_reul(phrase)} 시도했지만 실제로 반영됐는지 "
                "확인되지 않았습니다 — 네이버 광고 화면에서 확인이 필요합니다."
            )

        time_label = row.changed_at.strftime("%H:%M") if row.changed_at else "시각 미상"
        head = f"{time_label} · {cname} — " if cname else f"{time_label} · "
        out.append({
            # ★행 id를 함께 준다(「수정 사항」 화면): 문장만 돌려주면 호출자가 어느 행의
            #   문장인지 되짚을 방법이 정렬 순서 재현밖에 없다 — 내부 정렬 키가 바뀌는 순간
            #   문장이 **다른 행에 붙는다**(조용한 오표시). 추가 키라 기존 소비자엔 무해하다.
            "id": row.id,
            "at": row.changed_at.isoformat() if row.changed_at else None,
            "time_label": time_label,
            "state": state,
            "campaign_id": cid or None,
            "campaign_name": cname,
            "sentence": head + body,
        })
    return out
