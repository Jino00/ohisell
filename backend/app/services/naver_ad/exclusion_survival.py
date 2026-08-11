# exclusion_survival.py — 조치 생존 감시 (D-NAO-173 P1-①,
#   docs/PLAN_search-term-exclusion-list.md §2-6·§5-2)
#
# 역할: 우리가 네이버에 건 **검색어 제외가 아직 걸려 있는가**를 라이브 재조회로 대조하고
#   결과를 naver_search_term_exclusion 행에 적는다. 헬스 배너는 이 결과를 DB에서만 읽는다
#   (배너 렌더 경로에서 네이버 API를 부르지 않는다 — 요청마다 외부 호출은 감시가 아니라 부하).
#
# ★왜 «이벤트 탐지»가 아니라 «상태 대조»인가:
#   대행사가 우리 조치를 되돌린 것이 2회다. 2026-07-30 캠페인 재개는 잡았지만, 2026-08-10
#   그룹 userLock 해제(우리 잠금 6시간 7분 뒤)는 **우리 change_log에 행 자체가 없었다.**
#   발견은 순전히 라이브 재조회 덕분이었다. 즉 되돌림을 «사건»으로 잡는 경로는 이미 한 번
#   조용히 실패했다 — 그래서 여기서는 사건을 찾지 않고 **현재 상태를 매일 대조**한다.
#
# ★delFlag까지 본다: 응답에 행이 존재하는 것만으로 «살아 있음»이라 하면 소프트 삭제된 제외를
#   걸려 있다고 오독한다(응답 실물: nccAdgroupRestrictKwdId/nccAdgroupId/keyword/type/
#   delFlag/regTm — 2026-08-11 라이브 확인).
#
# 쓰기 범위: 이 모듈이 쓰는 것은 **대조 결과 세 칸(live_state/live_checked_at/live_note)과,
#   본문 대조로 새로 알아낸 restrict_kwd_id**뿐이다. 네이버에 쓰지 않고(GET만), 제외 상태기계
#   (status/cycle/next_review_at)를 건드리지 않는다 — 감시가 조치를 바꾸면 감시가 아니다.
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverSearchTermExclusion
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 대조 대상 상태 — «지금 걸려 있어야 하는» 것만. probation은 우리가 **의도적으로 개방한**
# 관찰창이고 restored는 복귀 확정이라, 라이브에 없는 것이 정상이다. 셋을 한 잣대로 재면
# 정상 개방이 매일 «어긋남»으로 뜬다(알림 피로 = 이 계열 감시가 실제로 죽는 방식).
MONITORED_STATUS = "excluded"

# 대조 신선도 상한 — 하루 1회 잡(08:25) + 유실 1회 여유. 넘으면 «최근 대조 없음»으로 이상 처리한다.
# ★대조가 멈춘 것과 어긋남이 없는 것을 같은 초록으로 두면, 감시가 죽은 날부터 조용해진다.
STALE_HOURS = 30

# 헬스 응답에 싣는 어긋남 목록의 상한 — 프론트가 폴링마다 받는 payload라 통째로 부풀면 안 된다.
# 총계는 breached_total로 따로 낸다(잘렸다는 사실이 숨지 않게).
BREACH_SAMPLE_CAP = 20

STATE_ALIVE = "alive"
STATE_MISSING = "missing"
STATE_DELETED = "deleted"
STATE_UNKNOWN = "unknown"

# 되돌림·복구 경로(화면·API가 같은 문장을 쓴다 — 두 벌이 되면 갈라진다).
REVERT_HOWTO = (
    "네이버 검색광고 콘솔 > 해당 광고그룹 > 제외 키워드에서 삭제하면 즉시 복구된다"
    "(자동 개방은 이 스프린트 범위 밖 — 사람이 실행한다)."
)


def _norm(term: str | None) -> str:
    """키워드 본문 비교용 정규화 — 공백 제거 + casefold.

    라이브 응답의 keyword 표기가 우리 저장값과 공백·대소문자에서 갈릴 수 있다. 정확 일치를
    먼저 보고, 못 찾을 때만 이 정규화로 한 번 더 본다(정규화를 1순위로 두면 서로 다른 두
    검색어가 같은 것으로 뭉칠 수 있다)."""
    return (term or "").replace(" ", "").casefold()


def _classify(row: NaverSearchTermExclusion, live: list[dict]) -> tuple[str, str | None, str | None]:
    """제외 1행 × 그 그룹의 라이브 제외키워드 목록 → (live_state, note, discovered_id).

    순수 함수(DB·API 미접촉) — 판정 규칙을 테스트가 직접 겨눌 수 있게 분리한다.

    판정 순서:
      ① 우리가 가진 restrict_kwd_id로 조회 — 있으면 delFlag가 최종 판정(true면 deleted).
      ② id로 못 찾으면 **본문 정확 일치**로 조회. 살아 있는 행이 딱 하나면 alive + id 회수
         (콘솔 수동 제외는 우리가 회수한 id가 없으므로 이 경로가 기본값이다). 동명이 둘 이상이면
         **어느 행이 우리 것인지 판별 불가라 unknown**이다 — `naver_sa_writer.add_restricted_keywords`
         가 같은 모호성에서 fail-closed로 거부하는 것과 같은 규율이다(한쪽만 [0]으로 고르면
         같은 리포 안에서 규율이 갈라진다).
      ③ 정확 일치가 없고 **표기만 다른 유사 행**(공백·대소문자)이 있으면 unknown이다. 네이버
         제외키워드에서 「아이패드 종이필름」과 「아이패드종이필름」은 서로 다른 키워드이므로
         유사 일치를 alive로 세면 **우리 조치가 사라진 바로 그 경우에 초록**이 된다. 그리고
         그 id를 회수하면 더 나쁘다 — `restrict_kwd_id`의 유일한 실쓰기 소비자가 개방
         (`delete_restricted_keywords`)이라, **우리가 걸지 않은 제외를 나중에 우리가 지운다.**
         감시가 조치를 바꾸면 감시가 아니다(모듈 docstring 쓰기 범위 항).
      ④ 어느 쪽으로도 못 찾으면 missing(= 우리 조치가 사라졌다).
    """
    by_id = {e.get("nccAdgroupRestrictKwdId"): e for e in live if e.get("nccAdgroupRestrictKwdId")}

    if row.restrict_kwd_id:
        entry = by_id.get(row.restrict_kwd_id)
        if entry is not None:
            if entry.get("delFlag"):
                return STATE_DELETED, "라이브에 행은 있으나 delFlag=true(소프트 삭제) — 효력 없음", None
            return STATE_ALIVE, None, None

    # ② 본문 **정확** 일치 — 살아 있는(delFlag가 아닌) 행만.
    exact_alive = [
        e for e in live if not e.get("delFlag") and e.get("keyword") == row.search_term
    ]
    if len(exact_alive) > 1:
        return (
            STATE_UNKNOWN,
            f"같은 검색어가 라이브에 {len(exact_alive)}행 — 어느 행이 우리 것인지 판별 불가(판정 보류)",
            None,
        )
    if len(exact_alive) == 1:
        found_id = exact_alive[0].get("nccAdgroupRestrictKwdId")
        if row.restrict_kwd_id:
            note = (
                f"저장된 id({row.restrict_kwd_id})는 라이브에 없고 같은 검색어가 다른 id로 걸려 있다 "
                "— 누군가 지웠다 다시 걸었을 수 있다(id 갱신)"
            )
        else:
            note = "저장된 id가 없어 검색어 본문으로 대조해 확인했다(콘솔 수동 제외 추정, id 회수)"
        return STATE_ALIVE, note, found_id

    # 소프트 삭제된 **정확** 동명 행만 있으면 missing이 아니라 deleted다(원인이 다르다).
    if any(e.get("delFlag") and e.get("keyword") == row.search_term for e in live):
        return STATE_DELETED, "같은 검색어 행이 delFlag=true로만 남아 있다 — 효력 없음", None

    # ③ 표기만 다른 유사 행 — alive라 말하지 않는다(위 docstring ③). id도 회수하지 않는다.
    near = [
        e for e in live
        if not e.get("delFlag") and _norm(e.get("keyword")) == _norm(row.search_term)
    ]
    if near:
        return (
            STATE_UNKNOWN,
            f"정확히 같은 검색어는 없고 표기만 다른 행이 있다(라이브: {near[0].get('keyword')!r}) "
            "— 우리 제외인지 판별 불가(판정 보류·id 미회수)",
            None,
        )

    return STATE_MISSING, "라이브 제외키워드 목록에 없다 — 우리 조치가 사라졌다", None


def check_survival(db: Session, *, now: datetime | None = None) -> dict:
    """status='excluded' 전 행을 라이브 재조회로 대조하고 결과를 DB에 적는다(잡 진입점).

    네이버 API 호출은 **광고그룹당 1회**(GET /ncc/adgroups/{id}/restricted-keywords).
    한 그룹 조회가 실패해도 나머지 그룹은 계속 본다 — 한 그룹의 실패가 감시 전체를 지우면
    그 침묵이 «이상 없음»과 구별되지 않는다(교훈 #123).

    반환: {checked, alive, missing, deleted, unknown, groups, errors, as_of}
    """
    from app.services.naver_ad import naver_sa_writer  # noqa: PLC0415 — 지연 임포트(순수 테스트 편의)

    now = now or kst_now()
    rows = (
        db.query(NaverSearchTermExclusion)
        .filter(NaverSearchTermExclusion.status == MONITORED_STATUS)
        .all()
    )
    by_group: dict[str, list[NaverSearchTermExclusion]] = {}
    for r in rows:
        if not r.adgroup_id:
            continue
        by_group.setdefault(r.adgroup_id, []).append(r)

    counts = {STATE_ALIVE: 0, STATE_MISSING: 0, STATE_DELETED: 0, STATE_UNKNOWN: 0}
    errors: list[str] = []

    # 그룹 id가 없으면 대조 자체가 불가능하다 — 조용히 건너뛰면 그 행은 영원히 «한 번도 대조
    # 안 함»으로 남아 배너가 영구히 빨강이 된다. 판정 불가라고 적어 표면화한다(적대 리뷰 P2-6).
    for r in rows:
        if not r.adgroup_id:
            r.live_state = STATE_UNKNOWN
            r.live_checked_at = now
            r.live_note = "adgroup_id가 없어 라이브 대조 불가"
            counts[STATE_UNKNOWN] += 1

    for adgroup_id, group_rows in by_group.items():
        try:
            live = naver_sa_writer.get_restricted_keywords(adgroup_id)
        except Exception as e:  # noqa: BLE001 — 그룹 하나의 실패가 나머지 감시를 죽이지 않는다
            log.exception("[제외생존] 라이브 조회 실패 adgroup=%s", adgroup_id)
            errors.append(f"{adgroup_id}: {type(e).__name__}: {e}")
            for r in group_rows:
                # ★unknown으로 **덮어쓴다**. 어제의 alive를 남겨 두면 조회가 죽은 뒤에도
                #   화면이 계속 초록이다 — 모르는 것을 모른다고 표시하는 쪽이 fail-closed다.
                r.live_state = STATE_UNKNOWN
                r.live_checked_at = now
                r.live_note = f"라이브 조회 실패: {type(e).__name__}"
                counts[STATE_UNKNOWN] += 1
            continue

        for r in group_rows:
            state, note, found_id = _classify(r, live)
            r.live_state = state
            r.live_checked_at = now
            r.live_note = note
            if found_id and found_id != r.restrict_kwd_id:
                # 대조로 알아낸 id를 회수한다 — 이게 있어야 나중에 «개방(delete)»이 가능하다.
                r.restrict_kwd_id = found_id
            counts[state] += 1

    db.commit()
    return {
        "checked": sum(counts.values()),
        "groups": len(by_group),
        "alive": counts[STATE_ALIVE],
        "missing": counts[STATE_MISSING],
        "deleted": counts[STATE_DELETED],
        "unknown": counts[STATE_UNKNOWN],
        "errors": errors,
        "as_of": now.isoformat(),
    }


def survival_summary(
    db: Session, *, now: datetime | None = None, stale_hours: int = STALE_HOURS
) -> dict:
    """헬스 배너용 요약 — **DB만 읽는다**(네이버 API 호출 0).

    반환 키:
      monitored          — 대조 대상(status='excluded') 건수
      alive              — 걸려 있음
      breached           — 어긋난 행 목록(missing/deleted/unknown). 배너가 이걸 그대로 읽는다.
      never_checked      — 한 번도 대조된 적 없는 건수(NULL — alive와 다르다)
      never_checked_due  — 그중 **대조 주기를 넘긴** 건수. 이것만 이상으로 센다.
      last_checked_at/stale — 대조 자체가 멈췄는가
      healthy            — 위 판정의 단일 결론(빌더가 이 키만 보면 되게)

    ★«아직 안 돌았다»와 «멈췄다»를 가른다(적대 리뷰 P1-3): 방금 들어온 제외 행은 다음 08:25
      전까지 당연히 NULL인데, 그걸 곧장 이상으로 세면 **제외를 한 건 실행할 때마다 전역 배너가
      다음 날까지 빨강**이 된다. 계약 P2가 「Jino가 콘솔에서 20~50건 제외」이므로 그 상태는
      상시화되고, 상시 빨강은 이 감시가 실제로 죽는 방식이다. 그래서 «제외된 지 대조 주기보다
      오래됐는데도 여태 안 본 행»만 이상으로 센다.
    """
    now = now or kst_now()
    rows = (
        db.query(NaverSearchTermExclusion)
        .filter(NaverSearchTermExclusion.status == MONITORED_STATUS)
        .all()
    )
    breached_all = [r for r in rows if r.live_state in (STATE_MISSING, STATE_DELETED, STATE_UNKNOWN)]
    # 배너·헬스 응답에 실리는 목록이라 상한을 둔다(총계는 breached_total로 따로 낸다).
    breached = [
        {
            "campaign_id": r.campaign_id,
            "adgroup_id": r.adgroup_id,
            "search_term": r.search_term,
            "live_state": r.live_state,
            "live_note": r.live_note,
            "excluded_at": r.excluded_at.isoformat() if r.excluded_at else None,
            "cost_at_exclusion": r.cost_at_exclusion,
        }
        for r in breached_all[:BREACH_SAMPLE_CAP]
    ]
    checked_ats = [r.live_checked_at for r in rows if r.live_checked_at]
    last_checked = max(checked_ats) if checked_ats else None
    due_before = now - timedelta(hours=stale_hours)
    never_checked = [r for r in rows if r.live_checked_at is None]
    # 제외된 지 주기를 넘겼는데 아직 한 번도 안 본 행 = 진짜 방치. excluded_at이 없으면(이론상)
    # 판정 근거가 없으므로 방치로 센다(fail-closed).
    never_checked_due = [
        r for r in never_checked if r.excluded_at is None or r.excluded_at <= due_before
    ]
    # 대조가 멈췄는가 — 봐야 할 행이 있는데 마지막 대조가 주기를 넘겼을 때만. 아직 아무것도
    # 볼 필요가 없었다면(전부 방금 들어온 행) 멈춘 것이 아니다.
    stale = bool(never_checked_due) if last_checked is None else last_checked <= due_before
    return {
        "monitored": len(rows),
        "alive": sum(1 for r in rows if r.live_state == STATE_ALIVE),
        "breached": breached,
        "breached_total": len(breached_all),
        "never_checked": len(never_checked),
        "never_checked_due": len(never_checked_due),
        "last_checked_at": last_checked.isoformat() if last_checked else None,
        "stale_hours": stale_hours,
        "stale": stale,
        "healthy": not breached_all and not stale and not never_checked_due,
        "revert_howto": REVERT_HOWTO,
        "impact": (
            "우리가 건 제외가 사라지면 그 검색어에 광고비가 다시 흐른다. "
            "대행사가 우리 조치를 되돌린 사례가 2회 있었고 그중 1회는 로그에 흔적이 없었다."
        ),
        "as_of": now.isoformat(),
    }
