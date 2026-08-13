# diary_outcome.py — outcome_backfill_sa (D-NAO-54 P2 해석층, docs/PLAN_naver-ad-diary-wisdom.md §P2)
# 역할: "한 일↔결과" 고리를 사후 소급으로 잇는다. 어제/그제/D-8 diary 행(execute/blocked/
#   reject/kill_switch)의 outcome_json에 D+1(완결 다음날 1일)·D+7(사후 7일) 실적을
#   naver_ad_daily에서 집계해 기입하고, 같은 target·같은 날 NaverRetroSignal이 있으면 채점
#   결과(board/direction/verdict)를 연결한다. 검색어 제외 행(target_type=="search_term")은
#   캠페인 폴백 d1과 별도로 검색어 grain 결과를 `d1_st`에 덧붙인다(D-NAO-178, 아래 섹션 참조).
#   Jino 문제의식("한 일만 적고 결과가 없다")의
#   직접 해소 지점. 읽기(diary·naver_ad_daily·retro) + diary 테이블 쓰기만(원칙18-1) —
#   제안 생성·실행 경로 접근 없음.
#
# ★완전성 금지(P1 리뷰 P3-4): diary는 best-effort(fail-open 소실 가능)라 "행 없음=아무 일도
#   없었음"이 아니다. 이 SA는 "남아 있는 diary 행"에만 결과를 소급 기입할 뿐, 전건 진실은
#   change_log가 소스다. avg_rank는 D-1 스윕 기준(스테일)·iphone offset은 caveat(P2 §env).
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverRetroSignal, NaverSearchTermDaily, OpsDiaryEntry
from app.services.naver_ad.bid_step_types import direction_of
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_ad.diagnosis import correction_factor
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 결과를 소급 채울 대상 이벤트(관찰 이벤트 observe는 제외 — 해석문 자기참조 방지).
EVENT_TYPES = ("execute", "blocked", "reject", "kill_switch")

# created_at(UTC) 스캔 하한 — 이보다 오래된 미완 행은 소급 대상에서 제외(무한 재스캔 방지).
_MAX_LOOKBACK_DAYS = 60

# ── 사후창 기입 문턱 (D-NAO-178) ────────────────────────────────────────────────
# 원료(naver_ad_daily·naver_search_term_daily)는 [T-3, T-1] 3일 창으로 매일 재수집되는데
# (scheduler_service.sync_naver_*_job) 재수집은 그 날짜 행을 통째로 delete 후 재삽입한다
# (ad_daily_ingest.py:42 · search_term_ingest.py:56) — 즉 **정정 이력이 원리적으로 남지 않는다.**
# 어떤 ad_date D의 마지막 수집 기회는 D+3의 07:40이고 d1_day = action_date+1이므로
# action_date+4의 07:40이 마지막이다. 그 뒤에 도는 08:35 스윕(age>=4)만이 «창이 닫힌 값»을 본다.
# 멱등 가드("d1" not in outcome)는 **첫 쓰기가 옳을 때만** 안전하므로 문턱을 창 종료에 맞춘다.
# (종전 age>=2는 첫 정정 직후 한 번 보고 굳었다 — 정정이 두 번 더 올 수 있는 시점.)
_OUTCOME_MIN_AGE_DAYS = 4

# ── d1_st(검색어 grain D+1, D-NAO-178) ──────────────────────────────────────────
# 필요 source가 끝내 안 오면 «모른다»를 명시 값으로 확정하는 마감. 창 종료(age 4) + EXPKEYWORD
# 보고서가 자동 생성되지 않아 생기는 「생성요청 → 다음 크론」 +1일 지연을 더한 하루 여유.
_ST_NO_DATA_AGE_DAYS = 5
_ST_SOURCES = ("shopping", "expkeyword")
# 필요 source 판정 창 — 원장 cost_at_exclusion과 같은 30일(§6-C).
_ST_HISTORY_DAYS = 30
# diary.target_id는 [:50]으로 잘려 저장된다 — 이 길이면 절단됐을 수 있으므로 접두 매칭.
_ST_TRUNC_LEN = 50


def _kst_date(created_at: datetime) -> date:
    """created_at은 UTC 저장([[sqlite-server-default-now-is-utc]]) → +9h 해서 KST 날짜."""
    return (created_at + timedelta(hours=9)).date()


def _grain_and_target(entry: OpsDiaryEntry) -> tuple[str, str | None]:
    """diary 행의 집계 grain. keyword/adgroup은 target_id 우선, 그 외(campaign·search_term·
    미지정)는 campaign 수준으로 폴백(PLAN §P2 대상 해상도).

    ★search_term의 campaign 폴백은 **의도적으로 남긴다**(D-NAO-178 금지선 2): 이미 기입된 d1은
    「조치 시점의 캠페인 배경 성과」로 읽고, 조치 자체의 성적은 `d1_st`가 담는다. 폴백을 여기서
    고치면 기존 d1의 의미가 소급으로 바뀌어 「언제 채점했나=무엇을 알고 채점했나」가 무너진다.
    """
    if entry.target_type == "keyword" and entry.target_id:
        return "keyword", entry.target_id
    if entry.target_type == "adgroup" and entry.target_id:
        return "adgroup", entry.target_id
    return "campaign", None


# ══════════════════════ d1_st — 검색어 grain D+1 (D-NAO-178) ══════════════════════
# 왜 있나: `_grain_and_target`이 search_term을 campaign으로 폴백하므로 검색어 제외 조치의 d1은
#   **그 캠페인 전체의 하루 성과**다(2026-08-13 라이브: diary 4371의 d1 cost 43,084원 vs 「골프」
#   30일 누적 31,411원). 조치의 진짜 성적을 검색어 grain 원료(NaverSearchTermDaily)로 따로 담는다.
# 판정은 ROAS 축이 아니라 status 4값이다 — 검색어 제외의 목적함수는 「손실 검색어 절단」이라
#   성공 지표가 **비용 정지**이고, 현행 roas 규칙을 재사용하면 완벽한 성공(cost=0)이 neutral로
#   버려진다(측정 대상과 규칙이 정반대).


def _escape_like(s: str) -> str:
    """LIKE 패턴의 리터럴화 — 검색어에 든 `%`·`_`가 와일드카드로 해석되는 것을 막는다.
    ★적대 리뷰 P1: 「20%할인」류 표기가 실제 검색어에 흔한데, 이스케이프가 없으면 50자 절단
    접두 매칭에서 **무관한 검색어의 비용**이 딸려 들어와 stopped가 leaking으로 뒤집힌다
    (§6-B의 「매칭 집합 = 진짜 비용의 상한」 전제 자체가 무너진다)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _st_term_clause(target_id: str):
    """검색어 매칭 조건. 50자 미만이면 정확 일치, 50자면 절단됐을 수 있으므로 접두 매칭(§6-B).
    정규화(공백·대소문자)는 하지 않는다 — 제외 후보가 같은 테이블에서 나왔으므로 문자열은 동일해야
    정상이고, 추측 정규화를 넣으면 「모르는 매칭 실패」가 조용히 「0=성공」이 된다."""
    if len(target_id) >= _ST_TRUNC_LEN:
        return NaverSearchTermDaily.search_term.like(_escape_like(target_id) + "%", escape="\\")
    return NaverSearchTermDaily.search_term == target_id


def _st_scope(db: Session, entry: OpsDiaryEntry, date_from: date, date_to: date):
    """(campaign, adgroup, 기간)으로 좁힌 기본 쿼리. 제외는 그룹 단위 장치이므로 범위도 그룹이다
    — campaign까지 넓히면 d1이 낸 것과 같은 오귀속이 된다."""
    return db.query(NaverSearchTermDaily).filter(
        NaverSearchTermDaily.campaign_id == entry.campaign_id,
        NaverSearchTermDaily.adgroup_id == entry.adgroup_id,
        NaverSearchTermDaily.ad_date >= date_from,
        NaverSearchTermDaily.ad_date <= date_to,
    )


def _st_required_sources(db: Session, entry: OpsDiaryEntry, action_date: date) -> list[str]:
    """필요 source = 조치 직전 30일에 이 (campaign, adgroup, term)이 실적을 낸 source 집합(§6-C).
    기왕력이 비면 어느 쪽으로 돈이 샜는지 모르므로 **두 source 모두** 필요로 본다."""
    rows = (
        # 창의 양끝을 포함하므로 -(N-1)이 정확히 N일이다 — 원장 `_cost_last_30d`
        # (search_term_execution.py: `as_of - (_COST_WINDOW_DAYS - 1)`)와 같은 산술을 쓴다.
        # 두 창이 하루라도 어긋나면 「원장이 본 기왕력」과 「판정이 본 기왕력」이 갈라진다.
        _st_scope(db, entry, action_date - timedelta(days=_ST_HISTORY_DAYS - 1), action_date)
        .filter(_st_term_clause(entry.target_id))
        .with_entities(NaverSearchTermDaily.source)
        .distinct()
        .all()
    )
    found = [s for s in _ST_SOURCES if s in {r[0] for r in rows}]
    return found or list(_ST_SOURCES)


def _st_source_view(db: Session, entry: OpsDiaryEntry, day: date, source: str) -> dict:
    """한 source의 그날 관측. `present`는 **이 검색어의 행 유무가 아니라 (campaign, adgroup)의
    그날 보고서 행 유무**다 — 검색어 행이 없는 것이야말로 「비용 0」의 의미이고, 보고서 자체가
    없는 것은 「아직 모른다」이기 때문이다(§2 판단기준 2: 0의 전제는 보고서 실재)."""
    scoped = _st_scope(db, entry, day, day).filter(NaverSearchTermDaily.source == source)
    if scoped.with_entities(NaverSearchTermDaily.id).first() is None:
        return {"present": False}

    matched = scoped.filter(_st_term_clause(entry.target_id)).all()
    view: dict = {
        "present": True,
        "imp": sum(int(r.imp or 0) for r in matched),
        "clk": sum(int(r.clk or 0) for r in matched),
        "cost": sum(int(r.cost or 0) for r in matched),
        "matched_terms": len({r.search_term for r in matched}),
    }
    if source == "shopping":
        # 전환은 shopping에만 기록한다 — expkeyword는 전환 귀속이 원리적으로 불가(SS0 §0.5)라
        # 0을 적으면 「전환 없었음」으로 오독된다. 두 source의 전환·ROAS 합산은 금지선 5.
        view["conv_amt"] = sum(int(r.conv_purchase_amt or 0) for r in matched)
    return view


def _st_window(db: Session, entry: OpsDiaryEntry, d1_day: date, age: int) -> dict | None:
    """검색어 grain D+1 관측 → `d1_st` dict. 필요 source의 보고서가 아직 없으면 **키를 쓰지 않고**
    None을 반환해 다음 스윕에서 재시도하고, 마감(age>=5)에 닿으면 `no_data`로 확정한다.
    ★`roas_c`는 담지 않는다(금지선 4) — 캠페인 판정 규칙(`roas_c >= target`)이 이 키를 실수로
    먹는 사고를 구조적으로 막는다. 판정은 `status` 문자열로만."""
    mode = "prefix50" if len(entry.target_id) >= _ST_TRUNC_LEN else "exact"
    base: dict = {"window": d1_day.isoformat(), "match": {"term": entry.target_id, "mode": mode}}

    if not entry.adgroup_id:
        # 그룹을 모르면 범위를 좁힐 수 없고, 캠페인으로 넓히는 것은 d1이 낸 오귀속 그 자체다.
        # 재시도해도 채워질 값이 아니므로 즉시 「모른다」로 확정한다.
        base["match"]["mode"] = "unresolved"
        base["by_source"] = {}
        base["required_sources"] = []
        base["cost_total"] = 0
        base["status"] = "no_data"
        return base

    required = _st_required_sources(db, entry, d1_day - timedelta(days=1))
    by_source = {s: _st_source_view(db, entry, d1_day, s) for s in _ST_SOURCES}
    missing = [s for s in required if not by_source[s]["present"]]

    base["by_source"] = by_source
    base["required_sources"] = required
    if missing:
        if age < _ST_NO_DATA_AGE_DAYS:
            return None  # 아직 올 수 있다 — 키를 안 쓰고 다음 스윕 재시도
        base["cost_total"] = 0
        base["status"] = "no_data"
        return base

    # 비용만 합산한다(§6-C) — present인 source 전부. 필요 source가 아니어도 그쪽으로 돈이 샜다면
    # 그건 leaking이고, 안 샜다면 0이라 합계를 바꾸지 않는다.
    present = [v for v in by_source.values() if v["present"]]
    cost_total = sum(int(v["cost"]) for v in present)
    matched_terms = sum(int(v["matched_terms"]) for v in present)
    base["match"]["matched_terms"] = matched_terms
    base["cost_total"] = cost_total

    if cost_total == 0:
        # 접두 매칭이 여럿을 잡아도 **매칭 집합의 비용 합이 진짜 비용의 상한**이므로, 상한이 0이면
        # 절단된 원문의 비용도 확실히 0이다 → 다의여도 stopped 판정이 성립한다(§6-B).
        base["status"] = "stopped"
    elif mode == "prefix50" and matched_terms > 1:
        base["status"] = "ambiguous"  # 누구 돈인지 모른다 — 「0」이 아니라 「모른다」로 표면화
    else:
        base["status"] = "leaking"
    return base


def _window_agg(
    db: Session, grain: str, target_id: str | None, campaign_id: str, date_from: date, date_to: date,
) -> tuple[int, int, int]:
    """사후창 (cost, clk, conv매출) 합계. retro_scorer._post_agg의 상세행 필터·conv 정의
    (직접+간접 매출)를 그대로 따르되 clk와 campaign grain을 확장한다(BACKFILL 센티넬 롤업
    행 제외 → 상세행과 이중계상 방지)."""
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0)
        + sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from, NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if grain == "keyword":
        q = q.filter(NaverAdDaily.keyword_id == target_id)
    elif grain == "adgroup":
        q = q.filter(NaverAdDaily.adgroup_id == target_id)
    else:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    row = q.one()
    return int(row[0]), int(row[1]), int(row[2])


def _window_metrics(
    db: Session, entry: OpsDiaryEntry, date_from: date, date_to: date, cf: float,
) -> dict:
    """한 사후창의 결과 metrics. roas_c = (conv매출/cost)×보정계수 — retro_scorer._judge와
    동일한 보정 ROAS 정의(cost=0이면 None)."""
    grain, target_id = _grain_and_target(entry)
    cost, clk, conv = _window_agg(db, grain, target_id, entry.campaign_id, date_from, date_to)
    roas_c = round((conv / cost) * cf, 4) if cost > 0 else None
    return {"cost": cost, "clk": clk, "conv": conv, "roas_c": roas_c}


def _retro_dict(sig: NaverRetroSignal) -> dict:
    """NaverRetroSignal → outcome_json['retro'] 서브딕(있는 값만). board/direction은 non-null."""
    d: dict = {"board": sig.board, "direction": sig.direction}
    if sig.verdict_d3 is not None:
        d["verdict_d3"] = sig.verdict_d3
    if sig.verdict_d7 is not None:
        d["verdict_d7"] = sig.verdict_d7
    return d


# diary action → retro direction (방향 일치 필터용, P2 리뷰 P3-3). 매핑 없는 action은 무필터.
# ★bid 계열(bid_up→up·bid_down→down)의 방향은 bid_step_types 레지스트리(단일 소스, IU-R R0)의
#   direction_of로 산출한다 — 종전 하드코딩 "up"/"down" 리터럴을 레지스트리로 이관(행위 불변:
#   같은 5개 키·같은 값 유지, growth_bid_up은 여전히 미매핑=무필터). 비-bid 액션(pause/update_bid/
#   set_user_lock)은 direction_of가 None이라 registry로 표현 불가 → 기존 매핑을 그대로 유지한다.
_ACTION_TO_DIRECTION = {"bid_up": direction_of("bid_up"), "bid_down": direction_of("bid_down"),
                        "pause": "pause", "update_bid": None, "set_user_lock": None}


def _find_retro(db: Session, target_id: str, asof: date, action: str | None) -> dict | None:
    """같은 target_id·같은 날(asof_date=diary 날짜) 채점 신호. P2 리뷰 P3-3: retro는 '제안
    방향' 추적이라 diary 행위와 동일 사건 보장이 없음 — action이 방향으로 매핑되면 direction
    일치 신호를 우선하고, 일치가 하나도 없으면 붙이지 않는다(오연결 방지). 매핑 불가 action은
    기존대로 verdict_d7 우선."""
    sigs = db.query(NaverRetroSignal).filter(
        NaverRetroSignal.target_id == target_id, NaverRetroSignal.asof_date == asof,
    ).order_by(NaverRetroSignal.id).all()
    if not sigs:
        return None
    want = _ACTION_TO_DIRECTION.get(action or "")
    if want is not None:
        sigs = [s for s in sigs if s.direction == want]
        if not sigs:
            return None
    best = next((s for s in sigs if s.verdict_d7 is not None), sigs[0])
    return _retro_dict(best)


def _backfill_row(db: Session, entry: OpsDiaryEntry, today: date, cf: float) -> dict:
    """한 diary 행의 outcome_json을 병합 갱신(기존 키 보존). 반환 = 이번에 채운 항목 카운트."""
    action_date = _kst_date(entry.created_at)
    age = (today - action_date).days
    outcome: dict = json.loads(entry.outcome_json) if entry.outcome_json else {}
    counts = {"d1": 0, "d7": 0, "retro": 0, "d1_st": 0, "d1_st_no_data": 0}
    d1_day = action_date + timedelta(days=1)

    # 사후 D+1(완결 다음날) 단일일. 문턱은 원료 정정 창이 닫히는 age 4(_OUTCOME_MIN_AGE_DAYS).
    if age >= _OUTCOME_MIN_AGE_DAYS and "d1" not in outcome:
        outcome["d1"] = _window_metrics(db, entry, d1_day, d1_day, cf)
        counts["d1"] = 1
    # 검색어 grain D+1 — d1과 공존하는 additive 키. d1은 한 글자도 건드리지 않는다.
    if (
        entry.target_type == "search_term"
        and entry.target_id
        and age >= _OUTCOME_MIN_AGE_DAYS
        and "d1_st" not in outcome
    ):
        st = _st_window(db, entry, d1_day, age)
        if st is not None:
            outcome["d1_st"] = st
            counts["d1_st"] = 1
            counts["d1_st_no_data"] = 1 if st["status"] == "no_data" else 0
    if age >= 8 and "d7" not in outcome:  # 사후 7일 = action_date+1..action_date+7
        outcome["d7"] = _window_metrics(
            db, entry, action_date + timedelta(days=1), action_date + timedelta(days=7), cf
        )
        counts["d7"] = 1
    if entry.target_id:
        desired = _find_retro(db, entry.target_id, action_date, entry.action)
        if desired is not None and desired != outcome.get("retro"):
            outcome["retro"] = desired
            counts["retro"] = 1

    if any(counts.values()):
        entry.outcome_json = json.dumps(outcome, ensure_ascii=False)
    return counts


def backfill_outcomes(db: Session, *, now: datetime | None = None) -> dict:
    """어제/그제/D-8 diary 행에 D+1/D+7 결과와 retro 채점을 소급 기입(매일 08:35 하니스 호출).

    행별 try/except로 한 행 실패가 스윕을 못 죽이고, 유닛 증분 커밋(D-NAO-46② 쓰기락 교훈)으로
    긴 쓰기락을 피한다. created_at 하한(60일)으로 미완 행 무한 재스캔을 막는다.
    """
    now = now or kst_now()
    today = now.date()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_MAX_LOOKBACK_DAYS)
    try:
        cf = float(correction_factor(db, today)["factor"])
    except Exception as e:  # noqa: BLE001 — 보정계수 실패는 1.0 폴백(roas_c만 무보정, 스윕 계속)
        log.warning("diary_outcome: 보정계수 산출 실패(cf=1.0 폴백): %s", e)
        cf = 1.0

    rows = db.query(OpsDiaryEntry).filter(
        OpsDiaryEntry.event_type.in_(EVENT_TYPES),
        OpsDiaryEntry.created_at.isnot(None),
        OpsDiaryEntry.created_at >= lower_utc,
    ).all()

    totals = {"d1_filled": 0, "d7_filled": 0, "retro_linked": 0,
              "d1_st_filled": 0, "d1_st_no_data": 0, "errors": 0}
    for entry in rows:
        try:
            c = _backfill_row(db, entry, today, cf)
            if any(c.values()):
                db.commit()  # 유닛 증분 커밋
            totals["d1_filled"] += c["d1"]
            totals["d7_filled"] += c["d7"]
            totals["retro_linked"] += c["retro"]
            totals["d1_st_filled"] += c["d1_st"]
            totals["d1_st_no_data"] += c["d1_st_no_data"]
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 스윕을 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("diary_outcome: 행 소급 실패(id=%s): %s", getattr(entry, "id", "?"), e)
    return totals
