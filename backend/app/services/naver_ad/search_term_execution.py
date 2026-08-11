# search_term_execution.py — 「사람이 실행한 제외」를 시스템의 1급 시민으로 등록 (D-NAO-173 P2-①,
#   docs/PLAN_search-term-exclusion-list.md §4-a)
#
# ## 이 모듈이 없으면 무슨 일이 벌어지나
# 이 리포엔 이미 채점→학습→지혜 사슬이 있다:
#   집행 → ops_diary_entries(execute) → diary_outcome(D+1·D+7 결과 소급) →
#   wisdom_candidates(조건별 good/bad 승률) → wisdom_judge → 지혜 → 주간 감사
# 그런데 이 스프린트의 제외는 **사람이 콘솔에서** 실행한다. 그러면 harness를 거치지 않으므로
# diary 행이 생기지 않고, 사슬 전체가 입력 0이 된다 — **열 번을 잘라도 시스템은 아무것도
# 배우지 않는다.** 「검사기는 있는데 아무도 안 부른다」의 또 한 번이다(교훈 #208 계열).
# 이 모듈이 그 한 칸을 메운다. 새 학습층을 만들지 않는다 — 기존 기계에 태우기만 한다.
#
# ## 금지선과의 관계
# 계약 §3의 금지선은 «**자동 제외 실행** 금지»다. 이 모듈은 **네이버에 쓰지 않는다** —
# 사람이 이미 한 일을 우리 원장과 일기에 적을 뿐이다. 실행과 기록을 같은 것으로 취급하면
# 「기록이 없어서 학습이 없다」는 상태가 금지선의 이름으로 영구화된다.
#
# ## 쓰기 범위
# `naver_search_term_exclusion` upsert + `ops_diary_entries` 1행. 그 외 아무것도 안 건드린다
# (입찰·예산·optimizer·auto_operate 미접촉).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverSearchTermDaily, NaverSearchTermExclusion
from app.services.naver_ad import diary
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# diary `action` 값 — ★wisdom_candidates의 시그니처가 `campaign_id|action|환경`이므로
# 이 문자열이 곧 **학습 단위의 이름**이다. 바꾸면 과거 시그니처와 갈라져 승률이 리셋된다.
DIARY_ACTION = "search_term_exclude"
# actor — diary 모델 주석의 허용값(daily/hourly/console/delegation/system) 중 «사람이 콘솔에서».
DIARY_ACTOR = "console"

# 재심사 백오프 — 기존 PX 상태기계 관례(cycle당 30일, 상한 90일)를 그대로 따른다.
# 여기서 새 정책을 만들지 않는다(같은 테이블에 두 가지 주기가 섞이면 재심사가 예측 불가해진다).
_REVIEW_BACKOFF_DAYS = 30
_REVIEW_BACKOFF_MAX = 90

# 근거 수치를 붙일 조회 창(제외 시점 비용 = 감사용 스냅샷). 리스트 생성기의 창과 같은 30일.
_COST_WINDOW_DAYS = 30


def _cost_last_30d(db: Session, adgroup_id: str, search_term: str, as_of: date) -> int:
    """제외 시점의 30일 비용 — `cost_at_exclusion`(감사 스냅샷)에 넣는다.

    ★나중에 성적표가 「전에 얼마 쓰던 검색어였나」를 이 값으로 되짚는다. 실행 시점에 안 박아
    두면 나중엔 그 검색어의 비용이 0이라 «원래 얼마였는지»를 복원할 수 없다."""
    frm = as_of - timedelta(days=_COST_WINDOW_DAYS - 1)
    return int(
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverSearchTermDaily.cost), 0))
        .filter(
            NaverSearchTermDaily.adgroup_id == adgroup_id,
            NaverSearchTermDaily.search_term == search_term,
            NaverSearchTermDaily.ad_date >= frm,
            NaverSearchTermDaily.ad_date <= as_of,
        )
        .scalar() or 0
    )


def record_execution(
    db: Session,
    *,
    campaign_id: str,
    adgroup_id: str,
    search_term: str,
    rationale: str,
    restrict_kwd_id: str | None = None,
    discovered: bool = False,
    now: datetime | None = None,
) -> dict:
    """사람이 콘솔에서 실행한 제외 1건을 등록한다(원장 upsert + 운영일기 execute 1행).

    Args:
        rationale: 왜 잘랐는가 — 리스트 생성기가 만든 근거 문장을 그대로 넘긴다(문구 정본은
            백엔드 SA 하나. 화면이 다시 쓰면 원장과 화면이 갈라진다).
        restrict_kwd_id: 라이브 대조로 이미 알아낸 id가 있으면 함께 저장(없으면 감시가 본문
            대조로 회수한다).
        discovered: True면 **라이브 대조가 스스로 발견**한 것(사람이 화면에서 알린 게 아니라).
            근거 문장에 그 출처를 남긴다 — 나중에 「누가 이 행을 만들었나」가 감사 대상이 된다.

    반환: {created|updated, exclusion_id, cost_at_exclusion, cycle, diary: bool}

    ★멱등: 같은 (adgroup_id, search_term)이 이미 excluded면 **일기를 또 쓰지 않는다**. 화면에서
      두 번 눌렀다고 학습 표본이 두 배가 되면 승률이 조작된다(wisdom_candidates는 entry 단위로
      센다). 재제외(restored/probation → excluded)만 cycle을 올리고 새 일기를 쓴다.
    """
    now = now or kst_now()
    today = now.date()

    row = (
        db.query(NaverSearchTermExclusion)
        .filter(
            NaverSearchTermExclusion.adgroup_id == adgroup_id,
            NaverSearchTermExclusion.search_term == search_term,
        )
        .first()
    )

    if row is not None and row.status == "excluded":
        # 이미 등록돼 있다 — id만 보강하고 조용히 끝낸다(일기 중복 금지).
        if restrict_kwd_id and not row.restrict_kwd_id:
            row.restrict_kwd_id = restrict_kwd_id
            db.commit()
        return {
            "result": "already_recorded", "exclusion_id": row.id,
            "cost_at_exclusion": row.cost_at_exclusion, "cycle": row.cycle, "diary": False,
        }

    cost = _cost_last_30d(db, adgroup_id, search_term, today)
    cycle = (row.cycle + 1) if row is not None else 1
    review_at = today + timedelta(days=min(_REVIEW_BACKOFF_DAYS * cycle, _REVIEW_BACKOFF_MAX))

    if row is None:
        row = NaverSearchTermExclusion(
            campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=search_term,
            restrict_kwd_id=restrict_kwd_id, status="excluded", cycle=cycle,
            excluded_at=now, last_transition_at=now, next_review_at=review_at,
            cost_at_exclusion=cost,
        )
        db.add(row)
        result = "created"
    else:
        row.status = "excluded"
        row.cycle = cycle
        row.excluded_at = now
        row.last_transition_at = now
        row.next_review_at = review_at
        row.cost_at_exclusion = cost
        if restrict_kwd_id:
            row.restrict_kwd_id = restrict_kwd_id
        # 재제외이므로 생존 상태는 «아직 안 봄»으로 되돌린다(옛 판정이 새 조치를 덮지 않게).
        row.live_state = None
        row.live_checked_at = None
        row.live_note = None
        result = "re_excluded"

    db.commit()
    # ★반환에 쓸 값을 **일기 쓰기 전에** 붙잡는다. 일기는 독립 세션·fail-open 계약이라 이 세션의
    #   상태에 관여하지 않아야 정상이지만, 그 전제에 기대어 커밋 후 ORM 속성을 다시 읽으면
    #   세션 수명이 조금만 달라져도(테스트 하니스·미래 리팩터) 등록이 통째로 실패한다.
    exclusion_id, cycle_out = row.id, row.cycle

    # ── 운영 일기 — 이 한 줄이 학습 사슬의 입구다 ──
    # fail-open(diary.write_diary_entry 계약): 일기 실패가 등록을 되돌리지 않는다. 다만 그때는
    # 이 조치가 학습에 안 잡히므로, 반환값에 diary=False로 **표면화**한다(조용한 누락 금지).
    source = "라이브 대조가 발견" if discovered else "화면에서 실행 보고"
    try:
        diary.write_diary_entry(
            db, "execute", campaign_id, actor=DIARY_ACTOR,
            target_type="search_term", target_id=search_term[:50], adgroup_id=adgroup_id,
            action=DIARY_ACTION,
            before_value=f"30일 광고비 {cost:,}원",
            after_value="제외(콘솔 수동)",
            rationale=f"{rationale} [등록 경위: {source}]",
            now=now,
        )
        wrote = True
    except Exception:  # noqa: BLE001 — diary는 fail-open이지만 여기서도 등록을 막지 않는다
        log.exception("[제외등록] 일기 기록 실패 — 등록은 유지, 학습 사슬엔 안 잡힘")
        wrote = False

    return {
        "result": result, "exclusion_id": exclusion_id, "cost_at_exclusion": cost,
        "cycle": cycle_out, "next_review_at": review_at.isoformat(), "diary": wrote,
    }


def detect_new_exclusions(
    db: Session, *, adgroup_ids: list[str] | None = None, now: datetime | None = None
) -> dict:
    """라이브 제외키워드를 읽어 **원장에 없는 제외를 스스로 발견**해 등록한다.

    ★왜 이 경로가 있나: 사람이 콘솔에서 자르고 화면에서 «실행했음»을 누르는 것을 잊으면, 그
      조치는 영원히 시스템 밖이다. 보고에 의존하는 경로는 이 리포에서 이미 한 번 실패했다
      (대행사 되돌림 2회 중 1회는 change_log에 행조차 없었다). **상태 대조가 사건 보고보다
      튼튼하다**는 같은 원리를 여기에도 쓴다.

    ⚠️**쇼핑에서 이 경로가 도는지는 실측 미해결이다**(2026-08-11): 쇼핑 광고그룹의
      `GET restricted-keywords`는 200이지만 0건이라, 「쇼핑 제외가 이 리소스로 되읽히는가」를
      아직 가르지 못했다(파워링크는 정상 조회됨). 되읽히지 않으면 이 함수는 쇼핑에서 아무것도
      못 찾고, 그때는 화면의 수동 기록 경로가 유일한 입구다. 그 사실을 반환값
      (`groups_with_zero`)으로 표면화한다 — 0건과 «못 읽음»이 같아 보이면 안 된다.

    Args:
        adgroup_ids: 대조할 광고그룹. 미지정이면 최근 30일 비용이 있는 그룹 전체(비싸다).
    """
    from app.services.naver_ad import naver_sa_writer  # noqa: PLC0415 — 지연 임포트

    now = now or kst_now()
    today = now.date()
    if adgroup_ids is None:
        frm = today - timedelta(days=_COST_WINDOW_DAYS)
        adgroup_ids = [
            r[0] for r in db.query(NaverSearchTermDaily.adgroup_id)
            .filter(
                NaverSearchTermDaily.ad_date >= frm,
                NaverSearchTermDaily.cost > 0,
                NaverSearchTermDaily.adgroup_id != "",
            )
            .distinct().all()
        ]

    known = {
        (r.adgroup_id, r.search_term)
        for r in db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.status == "excluded"
        ).all()
    }
    # 그룹 → 캠페인(원장에 campaign_id를 채우기 위해). 검색어 성과 테이블이 유일한 소스다.
    camp_of = dict(
        db.query(NaverSearchTermDaily.adgroup_id, NaverSearchTermDaily.campaign_id)
        .filter(NaverSearchTermDaily.adgroup_id.in_(adgroup_ids))
        .distinct().all()
    )

    recorded: list[dict] = []
    errors: list[str] = []
    groups_with_zero = 0
    unverifiable_groups = 0
    for adgroup_id in adgroup_ids:
        # ★쇼핑 그룹은 이 API가 제외 목록을 돌려주지 않는다(2026-08-11 실측: 콘솔 43건 vs
        #   API 0건). 그러니 여기서 «없다»를 근거로 아무 판단도 하지 않는다 — 자동 발견은
        #   구조적으로 파워링크 전용이고, 쇼핑의 유일한 입구는 화면 수동 기록이다.
        if naver_sa_writer.get_adgroup_type(adgroup_id) not in (None, "WEB_SITE"):
            unverifiable_groups += 1
            continue
        try:
            live = naver_sa_writer.get_restricted_keywords(adgroup_id)
        except Exception as e:  # noqa: BLE001 — 한 그룹 실패가 나머지를 지우지 않는다
            errors.append(f"{adgroup_id}: {type(e).__name__}: {e}")
            continue
        if not live:
            groups_with_zero += 1
            continue
        for entry in live:
            if entry.get("delFlag"):
                continue
            term = entry.get("keyword") or ""
            if not term or (adgroup_id, term) in known:
                continue
            out = record_execution(
                db, campaign_id=camp_of.get(adgroup_id, ""), adgroup_id=adgroup_id,
                search_term=term, restrict_kwd_id=entry.get("nccAdgroupRestrictKwdId"),
                rationale="라이브 제외키워드 목록에 있는데 우리 원장에 없어 등록했다",
                discovered=True, now=now,
            )
            recorded.append({"adgroup_id": adgroup_id, "search_term": term, **out})

    return {
        "scanned_groups": len(adgroup_ids),
        # 쇼핑이라 애초에 대조가 불가능해 건너뛴 그룹 — «찾은 게 없다»와 «볼 수 없다»를 가른다.
        "unverifiable_groups": unverifiable_groups,
        "groups_with_zero": groups_with_zero,
        "recorded": recorded,
        "errors": errors,
        "as_of": now.isoformat(),
    }
