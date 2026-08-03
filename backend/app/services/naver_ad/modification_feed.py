# modification_feed.py — 「수정 사항」 화면의 유일한 데이터 원천 (읽기 전용 하니스).
"""역할: 날짜 구간을 받아 **두 원천을 하나의 시간순 목록**으로 합친다.

  ① naver_change_log  — 우리 실집행 + 우리 시스템 내부 설정 + `external_*` 4종 외부 감지
  ② naver_agency_op   — 정의상 전부 외부(스냅샷 diff 유래 + 소재 editTm 앵커 유래)

★왜 합치나: 한쪽만 보면 그림이 반쪽이다. 대행사 조작은 grain에 따라 **다른 테이블**에
  들어간다(입찰·상태 diff는 ①의 external_*, 소재 editTm은 ②). 화면이 한쪽만 그리면
  "그날 아무도 안 만졌다"는 거짓 안심을 준다.

★날짜 귀속은 **실제 발생 시각 우선**이다(계약). agency_op의 `occurred_at`이 있으면 그것,
  없으면 감지일 `op_date`. 이게 왜 결정적인가: 2026-08-03 백필 36건은 `op_date=08-03`인데
  실제로는 **07-30에 일어난 일**이라, 감지일로 잡으면 07-30을 골랐을 때 한 건도 안 보인다
  (정확히 이 화면이 없애려는 문제 — 라이브 editTm을 손으로 대조하던 그 일).
  change_log에는 발생 시각 컬럼이 없다: 우리 실집행은 `changed_at`이 곧 발생 시각이지만
  `external_*` 감지 행은 entity_sync가 **알아챈** 시각이다. 그래서 행마다 `time_basis`로
  둘을 구분해 표시한다 — 모르는 걸 아는 척하지 않는다.

★네이버 API 쓰기 0. DB만 읽는다(원천 테이블은 이 경로에서 단 한 행도 바뀌지 않는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import NaverAgencyOp, NaverChangeLog
from app.services.naver_ad import alert_humanizer, change_actor, change_log_narrator, feed_reapply

FEED_VERDICT_FEED = feed_reapply.VERDICT_FEED

# ── 백필 마커 ────────────────────────────────────────────────────────
# 소급 백필 스크립트가 값 꼬리에 남기는 표식: `1890 [BACKFILL 2026-08-03: 소급 백필, …]`.
# ★화면에 "소급 백필"임이 드러나야 한다(정규 탐지와 신뢰도가 다르다) — 그래서 값에서
#   떼어내되 **버리지 않고** 플래그로 승격한다. 값에 붙은 채로 두면 "1890 [BACKFILL…]"이
#   그대로 표에 찍힌다(내부 표기 노출).
_BACKFILL_RE = re.compile(r"\s*\[BACKFILL\b[^\]]*\]\s*")

# ── 라벨 사전 ────────────────────────────────────────────────────────
# 모르는 코드는 **그대로 노출**한다 — 지어내지 않는다(원칙22).
_ENTITY_LABEL = {
    "campaign": "캠페인",
    "adgroup": "광고그룹",
    "keyword": "키워드",
    "ad": "소재",
    "search_term": "검색어",
}

_CHANGE_LOG_OP_LABEL = {
    "update_bid": "입찰가 변경",
    "update_budget": "일예산 변경",
    "budget_up_pacing": "일예산 증액(페이싱)",
    "budget_down_pacing": "일예산 원복(페이싱)",
    "set_user_lock": "정지·재개",
    "add_negative_keyword": "제외 키워드 추가",
    "exclude_search_term": "검색어 제외",
    "manual_emergency_stop": "긴급 정지",
    "optimizer_change": "관리주체 설정 변경",
    "update_expert_delegation": "위임 설정 변경",
    "flight_pacing": "예산 페이싱 관찰",
    "flight_pacing_silent": "예산 페이싱 관찰",
    "external_bid_change": "입찰가 변경",
    "external_status_change": "상태 변경",
    "external_keyword_added": "키워드 추가",
    "external_keyword_removed": "키워드 삭제",
}

_AGENCY_OP_LABEL = {
    "bid_change": "입찰가 변경",
    "budget_change": "일예산 변경",
    "status_flip": "상태 변경",
    "keyword_add": "키워드 추가",
    "keyword_remove": "키워드 삭제",
    "negative_add": "제외 키워드 추가",
    "negative_remove": "제외 키워드 삭제",
    "campaign_add": "캠페인 생성",
    "campaign_remove": "캠페인 삭제",
    "adgroup_add": "광고그룹 생성",
    "adgroup_remove": "광고그룹 삭제",
    "creative_change": "소재 변경",
    "extended_toggle": "확장검색 전환",
    "bid_mode_flip": "입찰 방식 전환",
    "ad_edit": "소재 수정",
}

#: 값을 '원'으로 읽어야 하는 agency_op op_type. 그 외는 단위를 붙이지 않는다
#: (붙이면 없는 사실을 말하게 된다 — 예: extended_toggle의 true/false).
_WON_OP_TYPES = frozenset({"bid_change", "budget_change"})

#: 이전값이 없을 때 화면이 쓸 말. **빈칸이나 0으로 채우지 않는다**(계약).
UNKNOWN_BEFORE = "이전값 불명(관측 공백)"
UNKNOWN_AFTER = "이후값 불명(관측 공백)"

TIME_BASIS_OCCURRED = "occurred"  # 실제 발생 시각
TIME_BASIS_DETECTED = "detected"  # 우리가 감지한 시각(실제 발생 시각 불명)

_TIME_NOTE = {
    TIME_BASIS_OCCURRED: "실제 발생 시각",
    TIME_BASIS_DETECTED: "감지 시각 — 실제로 언제 손댔는지는 기록이 없습니다",
}

_IN_CHUNK = 500  # SQLite 바인드 파라미터 상한 회피(구버전 999)

#: `until`(배타적 상한)에서 마지막 날짜를 되짚을 때 쓴다 — until은 종료일 **다음 날 자정**이다.
_ONE_MICRO = timedelta(microseconds=1)


@dataclass(frozen=True)
class _Candidate:
    """페이지를 고르기 위한 경량 후보 1건 — 큰 JSON 블롭을 안 읽고 정렬·집계한다.

    ★왜 2패스인가: change_log의 before/after는 소재 1건이 4KB짜리 JSON이다. 365일 구간을
      통째로 ORM으로 읽으면 수십 MB가 뜬다. 정렬·총계·주체 분포에 필요한 건 3~4컬럼뿐이라
      먼저 그것만 읽고, **화면에 나갈 페이지 분량만** 본체를 읽는다.
    """

    source: str
    source_id: int
    at: datetime
    time_basis: str
    auto_actor: str
    # D-NAO-139 — 소재 grain agency_op에만 채워진다. 그 외(change_log·다른 grain)는 None.
    feed_verdict: str | None = None
    feed_product_id: str | None = None
    # 접기로 이 행에 합쳐진 형제 행들(대표행에만 채워진다). 1이면 접힌 게 없다.
    feed_group_size: int = 1
    feed_group_ids: tuple[int, ...] = ()


def _effective_dt(occurred_at: datetime | None, op_date: date | None) -> tuple[datetime, str]:
    """agency_op 1건의 (귀속 시각, 근거). occurred_at 우선, 없으면 op_date 자정."""
    if occurred_at is not None:
        return occurred_at, TIME_BASIS_OCCURRED
    if op_date is not None:
        return datetime.combine(op_date, time.min), TIME_BASIS_DETECTED
    # 둘 다 없는 행은 스키마상 불가(op_date NOT NULL)지만, 있어도 화면에서 사라지면 안 된다.
    return datetime.min, TIME_BASIS_DETECTED


def _split_backfill(raw: str | None) -> tuple[str | None, bool]:
    """값에서 `[BACKFILL …]` 마커를 떼어낸다 → (값 또는 None, 백필 여부)."""
    if raw is None:
        return None, False
    marked = bool(_BACKFILL_RE.search(raw))
    cleaned = _BACKFILL_RE.sub("", raw).strip()
    return (cleaned or None), marked


def _won_or_raw(value: str | None, op_type: str) -> str | None:
    """입찰·예산 값이면 '2,330원'으로. 숫자가 아니면 원문 그대로(단위를 지어내지 않는다)."""
    if value is None:
        return None
    if op_type in _WON_OP_TYPES:
        try:
            return f"{alert_humanizer.count(int(float(value)))}원"
        except (TypeError, ValueError):
            return value
    return value


# ── 후보 수집(1패스) ──────────────────────────────────────────────────


def _change_log_candidates(
    db: Session,
    *,
    since: datetime,
    until: datetime | None,
    campaign_id: str | None,
    include_dry_run: bool,
    include_blocked: bool,
) -> list[_Candidate]:
    """change_log 후보. `include_blocked=False`(기본)면 **실제로 바뀐 행만** 남긴다.

    ★기본 제외가 계약이다(D-47-h 계승): 가드레일이 막힌 시도는 광고를 바꾸지 **않았다**.
      "수정 사항" 목록에 섞이면 하지도 않은 수정을 했다고 세게 된다. 다만 옵트인으로 볼 수는
      있어야 한다 — 가드레일이 일한 것도 우리가 한 일이다.
    ★판별 기준이 outcome이 아니라 after_value인 것은 이 코드베이스의 규약이다
      (naver_ad._execution_state · change_log_narrator.execution_state 동일).
    """
    q = db.query(
        NaverChangeLog.id,
        NaverChangeLog.changed_at,
        NaverChangeLog.action,
    ).filter(NaverChangeLog.changed_at >= since)
    if until is not None:
        q = q.filter(NaverChangeLog.changed_at < until)
    if campaign_id:
        q = q.filter(NaverChangeLog.campaign_id == campaign_id)
    if not include_dry_run:
        q = q.filter(NaverChangeLog.dry_run.is_(False))
    if not include_blocked:
        q = q.filter(NaverChangeLog.after_value.isnot(None))

    out: list[_Candidate] = []
    for cid, changed_at, action in q.all():
        actor = change_actor.classify_change_log(action)
        # 우리 실집행은 changed_at이 곧 "우리가 바꾼 순간"이다. external_* 감지 행은
        # entity_sync가 **알아챈** 시각이라 실제 발생 시각이 아니다 — 섞으면 "언제"에 거짓말한다.
        basis = (
            TIME_BASIS_DETECTED if actor == change_actor.ACTOR_AGENCY else TIME_BASIS_OCCURRED
        )
        out.append(
            _Candidate(
                source=change_actor.SOURCE_CHANGE_LOG,
                source_id=cid,
                at=changed_at or datetime.min,
                time_basis=basis,
                auto_actor=actor,
            )
        )
    return out


def _agency_op_candidates(
    db: Session,
    *,
    since: datetime,
    until: datetime | None,
    campaign_id: str | None,
) -> list[_Candidate]:
    """agency_op 후보. 날짜 조건을 **occurred_at 있음/없음으로 갈라** 건다.

    ★`coalesce(occurred_at, op_date) >= '2026-07-30 00:00:00'`으로 쓰면 안 된다: SQLite는
      Date를 `'2026-07-30'`, DateTime을 `'2026-07-30 11:28:36'` 문자열로 저장하므로 사전식
      비교에서 `'2026-07-30' < '2026-07-30 00:00:00'`이 **참**이다 → 시작일 당일의
      op_date-only 행(= 시각 불명 행)이 통째로 사라진다. 정확히 "모르는 걸 안 보이게" 만드는
      실패라 조건을 갈라 명시한다.
    """
    date_from = since.date()
    date_to = (until - _ONE_MICRO).date() if until is not None else None

    occurred_cond = [NaverAgencyOp.occurred_at.isnot(None), NaverAgencyOp.occurred_at >= since]
    date_cond = [NaverAgencyOp.occurred_at.is_(None), NaverAgencyOp.op_date >= date_from]
    if until is not None:
        occurred_cond.append(NaverAgencyOp.occurred_at < until)
        date_cond.append(NaverAgencyOp.op_date <= date_to)

    q = db.query(
        NaverAgencyOp.id, NaverAgencyOp.occurred_at, NaverAgencyOp.op_date,
        NaverAgencyOp.feed_verdict, NaverAgencyOp.feed_product_id,
    ).filter(or_(and_(*occurred_cond), and_(*date_cond)))
    if campaign_id:
        q = q.filter(NaverAgencyOp.campaign_id == campaign_id)

    out: list[_Candidate] = []
    for aid, occurred_at, op_date, verdict, product_id in q.all():
        at, basis = _effective_dt(occurred_at, op_date)
        out.append(
            _Candidate(
                source=change_actor.SOURCE_AGENCY_OP,
                source_id=aid,
                at=at,
                time_basis=basis,
                # 규칙 ③ — optimizer 컬럼을 보지 않는다(change_actor docstring의 용어 함정).
                auto_actor=change_actor.classify_agency_op(),
                feed_verdict=verdict,
                feed_product_id=product_id,
            )
        )
    return out


def _collapse_feed_reapply(candidates: list[_Candidate]) -> list[_Candidate]:
    """피드 재적용 형제들을 **한 줄로 접는다** (D-NAO-139).

    상품 하나의 피드가 재적용되면 그 상품의 소재가 전부 같은 초로 움직인다 — 화면엔 같은
    상품명이 N줄로 늘어서고, 사람은 "대행사가 N번 수정했다"로 읽는다(Jino가 08-01 12:27 건에서
    실제로 그렇게 읽었고 그게 이 기능의 출발점이다).

    접는 기준은 **(귀속 시각, 상품)**이다 — 같은 상품이 같은 초에 움직인 건 한 사건이다.
    op_type은 키에 넣지 않는다: 접기 대상은 `verdict == feed`인 행뿐이고, 입찰이 실제로
    바뀐 행은 정의상 `moved < total`이라 애초에 feed로 판정되지 않는다(그 상품의 다른 소재는
    그 초에 안 움직였으니까). 즉 이 그룹 안에 성격이 다른 사건이 섞일 길이 없다.

    ★대표행은 **가장 작은 source_id**로 고정한다(정렬·페이지가 흔들리지 않게). 접힌 형제
    id는 `feed_group_ids`로 남긴다 — 화면이 펼치거나 감사할 수 있어야 하고, "몇 건이 접혔나"를
    숫자로 말할 수 있어야 한다(조용한 truncation 금지).
    """
    groups: dict[tuple, list[_Candidate]] = {}
    passthrough: list[_Candidate] = []
    for c in candidates:
        if c.source == change_actor.SOURCE_AGENCY_OP and c.feed_verdict == FEED_VERDICT_FEED and c.feed_product_id:
            groups.setdefault((c.at, c.feed_product_id), []).append(c)
        else:
            passthrough.append(c)

    for members in groups.values():
        members.sort(key=lambda c: c.source_id)
        head = members[0]
        passthrough.append(
            replace(
                head,
                feed_group_size=len(members),
                feed_group_ids=tuple(m.source_id for m in members),
            )
        )
    return passthrough


# ── 본체 조립(2패스) ──────────────────────────────────────────────────


def _chunked_overrides(db: Session, keys: list[tuple[str, int]]) -> dict:
    """정정 배치 조회 — IN 목록을 잘라 바인드 파라미터 상한을 넘기지 않는다."""
    merged: dict = {}
    for i in range(0, len(keys), _IN_CHUNK):
        merged.update(change_actor.load_overrides(db, set(keys[i : i + _IN_CHUNK])))
    return merged


def _change_log_row(
    row: NaverChangeLog,
    labels: dict[tuple[str, str], str],
    camp_names: dict[str, str],
    sentence: str | None,
) -> dict:
    before, after = change_log_narrator.changed_values(row)
    b_val, b_backfill = _split_backfill(before)
    a_val, a_backfill = _split_backfill(after)
    return {
        "entity_type": row.entity_type,
        "entity_type_label": _ENTITY_LABEL.get(row.entity_type, row.entity_type),
        "entity_id": row.entity_id,
        "entity_name": labels.get((row.entity_type, row.entity_id)),
        "campaign_id": row.campaign_id or None,
        "campaign_name": camp_names.get(row.campaign_id or ""),
        "op_type": row.action,
        "op_label": _CHANGE_LOG_OP_LABEL.get(row.action, row.action),
        "before": b_val,
        "after": a_val,
        "execution_state": change_log_narrator.execution_state(row),
        "summary": sentence,
        "backfilled": b_backfill or a_backfill,
        "dry_run": row.dry_run,
    }


def _agency_op_row(
    row: NaverAgencyOp,
    labels: dict[tuple[str, str], str],
    camp_names: dict[str, str],
) -> dict:
    b_val, b_backfill = _split_backfill(row.before_value)
    a_val, a_backfill = _split_backfill(row.after_value)
    op_label = _AGENCY_OP_LABEL.get(row.op_type, row.op_type)
    name = labels.get((row.entity_type, row.entity_id))
    target = f"‘{name}’" if name else _ENTITY_LABEL.get(row.entity_type, row.entity_type)
    b_disp = _won_or_raw(b_val, row.op_type)
    a_disp = _won_or_raw(a_val, row.op_type)
    if b_disp and a_disp:
        sentence = f"{target} {op_label} — {b_disp} → {a_disp}."
    elif a_disp:
        sentence = f"{target} {op_label} — {a_disp}(이전 값은 기록이 없습니다)."
    else:
        sentence = f"{target} {op_label}."
    return {
        "entity_type": row.entity_type,
        "entity_type_label": _ENTITY_LABEL.get(row.entity_type, row.entity_type),
        "entity_id": row.entity_id,
        "entity_name": name,
        "campaign_id": row.campaign_id or None,
        "campaign_name": camp_names.get(row.campaign_id or ""),
        "op_type": row.op_type,
        "op_label": op_label,
        "before": b_disp,
        "after": a_disp,
        # agency_op은 우리 쓰기가 아니라 관측이다 — 실행 3상태 개념이 적용되지 않는다.
        "execution_state": None,
        "summary": sentence,
        "backfilled": b_backfill or a_backfill,
        "dry_run": False,
    }


def build(
    db: Session,
    *,
    since: datetime,
    until: datetime | None,
    campaign_id: str | None = None,
    actor: str | None = None,
    source: str | None = None,
    include_dry_run: bool = False,
    include_blocked: bool = False,
    include_feed_reapply: bool = True,
    collapse_feed_reapply: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """두 원천 합본 목록. 반환: {total, by_actor, feed_reapply, rows}.

    `actor` 필터는 **정정을 반영한 최종 주체** 기준이다(자동 판정 기준이 아니다) — 사람이
    "이건 내가 한 것"이라고 고쳤는데 '대행사' 필터에 계속 남아 있으면 정정이 무의미하다.
    `by_actor`는 actor 필터와 무관하게 **구간 전체 분포**를 준다(필터를 걸어도 전체가 보인다).

    ★D-NAO-139 피드 재적용 두 스위치(둘은 다른 일을 한다):
      - `collapse_feed_reapply`(기본 켬) — 같은 상품이 같은 초에 움직인 N줄을 **1줄로 접는다**.
        정보는 안 버린다(`feed_group_size`·`feed_group_ids`로 남는다).
      - `include_feed_reapply`(기본 켬) — 끄면 피드 재적용 행을 **목록에서 뺀다**. 대행사가
        실제로 만진 것만 보고 싶을 때 쓴다.
    ★두 스위치가 무엇을 얼마나 감췄는지는 반환의 `feed_reapply` 블록이 항상 말한다 —
      조용한 truncation 금지(숨긴 줄 수를 화면이 모르면 "그날 조용했다"는 거짓 안심이 된다).
    """
    candidates: list[_Candidate] = []
    if source in (None, change_actor.SOURCE_CHANGE_LOG):
        candidates += _change_log_candidates(
            db,
            since=since,
            until=until,
            campaign_id=campaign_id,
            include_dry_run=include_dry_run,
            include_blocked=include_blocked,
        )
    if source in (None, change_actor.SOURCE_AGENCY_OP):
        candidates += _agency_op_candidates(
            db, since=since, until=until, campaign_id=campaign_id
        )

    # ── D-NAO-139: 피드 재적용 처리는 **집계·페이지 계산 전에** 한다 ──
    #   나중에 걸면 total과 화면 줄 수가 어긋나고, 페이지마다 접힌 개수가 달라진다.
    feed_rows = [c for c in candidates if c.feed_verdict == FEED_VERDICT_FEED]
    feed_stats = {
        "verdict_rows": sum(1 for c in candidates if c.feed_verdict),
        "feed_rows": len(feed_rows),
        "hidden": 0,
        "collapsed_into": 0,
        "included": include_feed_reapply,
        "collapsed": collapse_feed_reapply,
    }
    if not include_feed_reapply:
        candidates = [c for c in candidates if c.feed_verdict != FEED_VERDICT_FEED]
        feed_stats["hidden"] = len(feed_rows)
    elif collapse_feed_reapply:
        before = len(candidates)
        candidates = _collapse_feed_reapply(candidates)
        feed_stats["collapsed_into"] = before - len(candidates)

    overrides = _chunked_overrides(db, [(c.source, c.source_id) for c in candidates])

    resolved: list[tuple[_Candidate, str, bool, str | None]] = []
    by_actor: dict[str, int] = {a: 0 for a in change_actor.ACTORS}
    for c in candidates:
        final, corrected, note = change_actor.resolve(
            c.auto_actor, overrides.get((c.source, c.source_id))
        )
        by_actor[final] = by_actor.get(final, 0) + 1
        resolved.append((c, final, corrected, note))

    if actor:
        resolved = [t for t in resolved if t[1] == actor]

    resolved.sort(key=lambda t: (t[0].at, t[0].source, t[0].source_id), reverse=True)
    total = len(resolved)
    page = resolved[offset : offset + limit]

    cl_ids = [c.source_id for c, *_ in page if c.source == change_actor.SOURCE_CHANGE_LOG]
    ao_ids = [c.source_id for c, *_ in page if c.source == change_actor.SOURCE_AGENCY_OP]
    cl_rows = (
        db.query(NaverChangeLog).filter(NaverChangeLog.id.in_(cl_ids)).all() if cl_ids else []
    )
    ao_rows = (
        db.query(NaverAgencyOp).filter(NaverAgencyOp.id.in_(ao_ids)).all() if ao_ids else []
    )
    cl_by_id = {r.id: r for r in cl_rows}
    ao_by_id = {r.id: r for r in ao_rows}

    # 이름 해석은 두 원천을 **한 번에** 한다(같은 소재가 표 안에서 두 이름을 갖지 않게).
    labels = change_log_narrator.resolve_labels(db, [*cl_rows, *ao_rows])
    camp_ids = {r.campaign_id for r in [*cl_rows, *ao_rows] if r.campaign_id}
    camp_names = {
        cid: nm
        for cid, nm in alert_humanizer.entity_names(db, "campaign", camp_ids).items()
        if nm and nm != cid  # ID 폴백 금지(D-NAO-103①) — 이름이 없으면 키를 안 넣는다
    }
    # 우리 쪽 행의 "사람이 읽는 한 문장"은 기존 서술기를 그대로 쓴다(두 벌로 쓰면 같은 변경이
    # 화면마다 다른 말이 된다). id로 되짚는다 — 정렬 순서에 기대지 않는다.
    sentences = {
        item["id"]: item["sentence"]
        for item in change_log_narrator.narrate(db, cl_rows, labels=labels)
    }

    rows: list[dict] = []
    for c, final, corrected, note in page:
        if c.source == change_actor.SOURCE_CHANGE_LOG:
            src_row = cl_by_id.get(c.source_id)
            if src_row is None:
                continue  # 1패스와 2패스 사이에 사라진 행(리플레이) — 지어내지 않는다
            body = _change_log_row(src_row, labels, camp_names, sentences.get(src_row.id))
        else:
            ao_row = ao_by_id.get(c.source_id)
            if ao_row is None:
                continue
            body = _agency_op_row(ao_row, labels, camp_names)
        rows.append(
            {
                "key": f"{c.source}:{c.source_id}",
                "source": c.source,
                "source_label": change_actor.SOURCE_LABEL.get(c.source, c.source),
                "source_id": c.source_id,
                "occurred_at": c.at.isoformat() if c.at != datetime.min else None,
                "occurred_date": c.at.date().isoformat() if c.at != datetime.min else None,
                "time_basis": c.time_basis,
                "time_note": _TIME_NOTE[c.time_basis],
                "actor": final,
                "actor_label": change_actor.ACTOR_LABEL.get(final, final),
                "actor_auto": c.auto_actor,
                "corrected": corrected,
                "correction_note": note,
                # 값이 없으면 **왜 없는지**를 말한다(빈칸·0 금지).
                "before_unknown": None if body["before"] is not None else UNKNOWN_BEFORE,
                "after_unknown": None if body["after"] is not None else UNKNOWN_AFTER,
                **body,
                # D-NAO-139 — 판정이 없는 행(다른 grain·매핑 결손)은 전부 None이다.
                # 라벨·근거 문장은 서버가 만든다: 판정 규칙이 화면마다 다른 말이 되지 않게.
                **_feed_fields(c, ao_by_id.get(c.source_id) if c.source == change_actor.SOURCE_AGENCY_OP else None),
            }
        )

    return {"total": total, "by_actor": by_actor, "feed_reapply": feed_stats, "rows": rows}


def _feed_fields(c: _Candidate, ao_row: NaverAgencyOp | None) -> dict:
    """행에 실을 피드 재적용 판정 필드. 판정이 없으면 전부 None(빈칸을 지어내지 않는다)."""
    if ao_row is None or not ao_row.feed_verdict:
        return {
            "feed_verdict": None, "feed_verdict_label": None, "feed_evidence": None,
            "feed_group_size": 1, "feed_group_ids": [],
        }
    verdict = feed_reapply.Verdict(
        ao_row.feed_verdict, ao_row.feed_product_id, ao_row.feed_moved, ao_row.feed_total
    )
    return {
        "feed_verdict": ao_row.feed_verdict,
        "feed_verdict_label": feed_reapply.VERDICT_LABEL.get(ao_row.feed_verdict, ao_row.feed_verdict),
        "feed_evidence": verdict.evidence(),
        "feed_group_size": c.feed_group_size,
        "feed_group_ids": list(c.feed_group_ids),
    }
