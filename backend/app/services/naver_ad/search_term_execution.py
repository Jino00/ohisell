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

from sqlalchemy import func as sqlfunc, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import NaverSearchTermDaily, NaverSearchTermExclusion, OpsDiaryEntry
from app.services.naver_ad import diary, exclusion_grade
from app.utils.kst import KST, kst_now

log = logging.getLogger(__name__)

# diary `action` 값 — ★wisdom_candidates의 시그니처가 `campaign_id|action|환경`이므로
# 이 문자열이 곧 **학습 단위의 이름**이다. 바꾸면 과거 시그니처와 갈라져 승률이 리셋된다.
DIARY_ACTION = "search_term_exclude"
# ★이 원장에는 쓰기 주체가 **둘**이고 둘이 일기를 서로 다른 모양으로 쓴다:
#     record_execution(이 파일) : action="search_term_exclude" · target_id=검색어[:50]
#     SS레인/하네스             : action="exclude_search_term" · target_id=검색어 전문
#       (naver_execution_harness._ACTION_BY_PROPOSAL_TYPE[SEARCH_TERM_EXCLUDE_TYPE])
#   한쪽 모양만 보면 다른 쪽이 만든 행을 무효화할 때 일기가 살아남아 **학습이 계속 그 조치를
#   먹는다.** 가설이 아니다 — prod exclusion_id=1(아이패드종이필름)이 하네스 산 행이다
#   (2026-07-22 `_autofire_exclude`). 표기 분열 자체는 선행 결함이고 여기서 통합하지 않는다
#   (시그니처를 바꾸면 과거 승률이 리셋된다) — 무효화만 양쪽을 다 본다.
DIARY_ACTIONS = (DIARY_ACTION, "exclude_search_term")
# 일기 매칭의 시간 하한 여유 — 원장 커밋과 일기 쓰기 사이의 순서·시계 차이를 흡수한다.
# 주기(cycle) 간격은 최소 30일이라 1시간 여유로도 주기끼리는 확실히 갈린다.
_DIARY_MATCH_SLACK = timedelta(hours=1)
# actor — diary 모델 주석의 허용값(daily/hourly/console/delegation/system) 중 «사람이 콘솔에서».
DIARY_ACTOR = "console"

# ── 무효화(잘못 들어온 장부 행의 정정 경로, D-NAO-174 이월 P2) ──
# ★하드 삭제가 아니라 상태 전이인 이유: 이 행에는 짝인 일기 행이 있고 그게 학습 사슬의 입력이다.
#   행을 지우면 일기만 고아로 남아 «누가 왜 이 조치를 기록했나»를 영영 복원할 수 없다.
#   기존 소비자(성적표·생존 감시·SS레인·자동 발견)는 전부 status를 명시적으로 좁혀 읽으므로
#   (`== "excluded"` / `in ("excluded","probation")`) 새 상태는 자동으로 전부에서 빠진다 —
#   즉 «지워진 것처럼» 보이면서 감사 흔적은 남는다.
VOID_STATUS = "void"
# 일기 쪽 중화 — diary_outcome.EVENT_TYPES( execute/blocked/reject/kill_switch ) 밖의 값이라
# 소급 기입 대상에서 빠지고, 따라서 wisdom 후보로도 안 올라간다. 행 자체는 지우지 않는다.
VOIDED_EVENT_TYPE = "voided"

# 재심사 백오프 — 기존 PX 상태기계 관례(cycle당 30일, 상한 90일)를 그대로 따른다.
# 여기서 새 정책을 만들지 않는다(같은 테이블에 두 가지 주기가 섞이면 재심사가 예측 불가해진다).
# ★S2에서 «정의 자리»가 `exclusion_grade`로 옮겨졌다(값 불변). 같은 식이 세 파일에 세 벌로
#   흩어져 있었고 그중 하나는 cycle을 무시했다 — 등급별 만료 규칙을 그 위에 얹으면 갈라짐이
#   등급까지 번진다. 이름은 그대로 두어 기존 참조가 안 깨진다.
_REVIEW_BACKOFF_DAYS = exclusion_grade.REVIEW_BACKOFF_DAYS
_REVIEW_BACKOFF_MAX = exclusion_grade.REVIEW_BACKOFF_MAX

# 근거 수치를 붙일 조회 창(제외 시점 비용 = 감사용 스냅샷). 리스트 생성기의 창과 같은 30일.
_COST_WINDOW_DAYS = 30


class ExclusionInputError(ValueError):
    """장부 입구에서 거부된 입력. 라우터가 422로 옮긴다.

    ★왜 예외인가(조용한 저장 금지): 이 원장에는 **삭제 라우트가 없었고**(D-NAO-174 이월 P2),
      한 번 들어간 행은 배너·성적표·SS레인·학습 사슬에 영구히 남았다. 빈 값을 «기본값»으로
      받아 저장하면 그 행을 나중에 사람이 알아볼 수도, 지울 수도 없다. 입구에서 막는 것이
      들어온 뒤 지우는 것보다 싸다.
    """


def _require(value: str | None, field: str, *, max_len: int) -> str:
    """장부에 넣기 전 필수 문자열 1칸 검사 — 공백만 있는 값도 «없음»으로 친다."""
    v = (value or "").strip()
    if not v:
        raise ExclusionInputError(f"{field}가 비어 있다 — 장부에 넣을 수 없다")
    if len(v) > max_len:
        raise ExclusionInputError(f"{field}가 너무 길다({len(v)}자 > {max_len}자)")
    return v


# 콘솔 「제외 검색어」 탭의 등록시각 표기는 `2026.08.11 22:26`이다. 사람이 옮겨 적는 경로라
# ISO(`2026-08-11T22:26:00`)나 날짜만(`2024-12-26`)으로도 온다 — 전부 받는다. 파싱 못 하는
# 값은 **조용히 버리지 않고 그 행만 거부**한다(형식을 몰라 날짜가 사라지면 「모른다」와
# 「안 적었다」가 구분되지 않는다).
_CONSOLE_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
)
# 미래 시각 허용 여유 — 콘솔 시계와 우리 시계의 차이만 흡수한다(그 이상은 오타로 본다).
_CONSOLE_DT_FUTURE_SLACK = timedelta(minutes=10)
# 네이버 검색광고 이전 연도는 오타로 본다(연도 한 자 오타가 1900년대를 만든다).
_CONSOLE_DT_MIN_YEAR = 2010


def _parse_reg_tm(value: object, *, now: datetime) -> datetime | None:
    """네이버 제외키워드 행의 `regTm`(UTC ISO, 예 `2026-07-14T03:59:54.000Z`) → KST naive.

    ★이 값이 곧 «콘솔 등록시각»이다(D-NAO-179). 그래서 파워링크는 사람이 콘솔을 캡처하지
      않아도 `console_excluded_at`이 채워진다 — D-NAO-176이 「시점 미상」을 전제로 만들어졌던
      바로 그 칸이다.

    ★★**같은 규칙을 쓰되 처분이 다르다**(적대 리뷰 1R P1). 경계 판정은 아래
      `_parse_console_excluded_at`에 그대로 위임한다(규칙을 두 벌로 만들지 않는다) — 다만
      그쪽은 범위 밖 값을 `ExclusionInputError`로 **거부**하고 여기서는 **None(=모른다)**으로
      낮춘다. 입력원의 성격이 다르기 때문이다:
        · 사람이 콘솔 화면을 보고 옮겨 적는 값 → 오타면 그 행을 거부해 **고쳐 넣게** 해야 한다.
        · API가 준 값 → 고쳐 넣을 사람이 없다. 거부하면 그 키워드는 **매 스윕마다 같은 이유로
          거부되어 영구히 원장 밖**에 남는다. 보조 메타데이터 한 칸 때문에 「제외를 본다」는
          1급 목표가 깨지는 것이다.
      즉 **시각은 모를 수 있어도 «제외가 있다»는 사실 자체는 반드시 들어와야 한다.**
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        log.warning("[제외발견] regTm을 못 읽었다(%r) — console_excluded_at은 미상으로 둔다", value)
        return None
    try:
        return _parse_console_excluded_at(dt, now=now)
    except ExclusionInputError as e:
        log.warning(
            "[제외발견] regTm이 범위 밖이다(%r: %s) — console_excluded_at은 미상으로 두고 편입은 계속한다",
            value, e,
        )
        return None


def _parse_console_excluded_at(value: object, *, now: datetime) -> datetime | None:
    """콘솔이 보여준 «실제 제외 등록시각»을 KST naive로 정규화한다. 없으면 None(=모른다).

    ★None을 「지금」으로 메우지 않는 것이 이 함수의 전부다. 콘솔 화면에 등록시각이 있다는 걸
      알기 전(D-NAO-176)엔 편입분 전건이 「시점 미상」이었는데, 이제 아는 행과 모르는 행이
      섞인다. 모르는 행을 편입 시각으로 채우면 그 값이 **아는 값과 구분되지 않아** 나중에
      누구도 신뢰도를 되짚을 수 없다 — 이 리포가 세 번 연속 당한 「모르는 것을 아는 것으로
      센다」의 네 번째가 된다.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # tz-aware면 KST로 옮겨 naive화(이 원장의 시각 칸은 전부 KST naive다).
        dt = value.astimezone(KST).replace(tzinfo=None) if value.tzinfo else value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        dt = None
        for fmt in _CONSOLE_DT_FORMATS:
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ExclusionInputError(
                f"console_excluded_at을 못 읽었다({raw!r}) — 콘솔 표기 그대로(2026.08.11 22:26) 또는 "
                f"ISO(2026-08-11T22:26:00)로 주거나, 모르면 아예 빼라"
            )
    if dt.year < _CONSOLE_DT_MIN_YEAR:
        raise ExclusionInputError(
            f"console_excluded_at이 너무 과거다({dt.isoformat()}) — 연도 오타로 본다"
        )
    if dt > now + _CONSOLE_DT_FUTURE_SLACK:
        raise ExclusionInputError(
            f"console_excluded_at이 미래다({dt.isoformat()}) — 이미 걸려 있는 제외의 등록시각이"
            f" 미래일 수 없다"
        )
    return dt


def not_console_import() -> ColumnElement[bool]:
    """SQL에서 «편입분이 **아닌** 행»을 고르는 술어 — ★NULL 안전.

    `source != 'console_import'` 한 줄로 쓰고 싶어지지만, **우리가 실행한 행의 `source`는
    NULL**이다. SQL에서 `NULL != '...'`은 참이 아니라 NULL(=거짓)이므로 그 한 줄은 편입분이
    아니라 **정상 행을 통째로 지운다.** 파이썬 쪽 필터(`r.source != CONSOLE_IMPORT_SOURCE`)와
    같은 모양인데 결과가 정반대라 특히 위험해서 함수로 못 박는다.
    """
    return or_(
        NaverSearchTermExclusion.source.is_(None),
        NaverSearchTermExclusion.source != CONSOLE_IMPORT_SOURCE,
    )


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
    grade: str | None = None,
) -> dict:
    """사람이 콘솔에서 실행한 제외 1건을 등록한다(원장 upsert + 운영일기 execute 1행).

    Args:
        rationale: 왜 잘랐는가 — 리스트 생성기가 만든 근거 문장을 그대로 넘긴다(문구 정본은
            백엔드 SA 하나. 화면이 다시 쓰면 원장과 화면이 갈라진다).
        restrict_kwd_id: 라이브 대조로 이미 알아낸 id가 있으면 함께 저장(없으면 감시가 본문
            대조로 회수한다).
        discovered: True면 **라이브 대조가 스스로 발견**한 것(사람이 화면에서 알린 게 아니라).
            근거 문장에 그 출처를 남긴다 — 나중에 「누가 이 행을 만들었나」가 감사 대상이 된다.
        grade: 제외 «임대» 등급(계약 §4-B⑥). 기본 `성과미달` — 이 경로로 들어오는 컷은
            리스트 생성기의 근거 문장이 붙은 «창 내 확정 손해»라 그 등급의 정의 그대로다.
            **기본값이 곧 종전 동작이다**(만료일 = min(30×cycle,90), 하루도 안 옮긴다).
            텍스트 관련성 판단(`무관`·`광의`)은 자동 부여 근거가 없어 백필이 안 붙이고
            (계약 §4-B⑦), 사람이 판별했을 때만 여기로 넘어온다.

    반환: {result, exclusion_id, cost_at_exclusion, cycle, next_review_at, diary: bool}
      result ∈ {created, re_excluded, already_recorded}

    Raises:
        ExclusionInputError: campaign_id·adgroup_id·search_term·rationale 중 빈 값이 있을 때.
            ★fail-closed다: 빈 campaign_id로 저장되면 그 행의 학습 시그니처가
            `|search_term_exclude|…`로 시작해 캠페인 축을 잃고, 원장·일기 어디서도 어느
            캠페인의 조치였는지 복원할 수 없다. 종전엔 detect 경로가 `camp_of.get(id, "")`로
            바로 그 빈 값을 넣을 수 있었다(D-NAO-174 이월 P2).

    ★멱등: 같은 (adgroup_id, search_term)이 이미 excluded면 **일기를 또 쓰지 않는다**. 화면에서
      두 번 눌렀다고 학습 표본이 두 배가 되면 승률이 조작된다(wisdom_candidates는 entry 단위로
      센다). 재제외(restored/probation → excluded)만 cycle을 올리고 새 일기를 쓴다.
    """
    # ★입구 검증 — 조회·쓰기 어느 것보다 먼저. 뒤에서 하면 «검사는 있는데 이미 저장된» 꼴이 된다.
    campaign_id = _require(campaign_id, "campaign_id", max_len=50)
    adgroup_id = _require(adgroup_id, "adgroup_id", max_len=50)
    search_term = _require(search_term, "search_term", max_len=300)
    rationale = _require(rationale, "rationale", max_len=1000)

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
    # ★무효화된 행을 되살릴 땐 cycle을 승계하지 않는다. void는 «잘못 들어온 행»이라 애초에
    #   일어나지 않은 조치이고, 그 횟수를 세면 재심사 백오프(30일×cycle)가 실수 때문에 늘어난다.
    if row is not None and row.status == VOID_STATUS:
        cycle = 1
    else:
        cycle = (row.cycle + 1) if row is not None else 1
    # 등급이 만료일을 정한다(S2) — 기본 `성과미달`의 식은 종전 백오프와 글자 그대로 같다.
    grade = grade or exclusion_grade.GRADE_UNDERPERFORM
    grade_reason = f"record_execution — 사람 실행 보고(cycle={cycle})"
    review_at = exclusion_grade.default_next_review_at(grade, cycle=cycle, today=today)

    if row is None:
        row = exclusion_grade.new_exclusion(
            campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=search_term,
            restrict_kwd_id=restrict_kwd_id, cycle=cycle, now=now,
            cost_at_exclusion=cost, grade=grade, grade_reason=grade_reason,
        )
        db.add(row)
        result = "created"
    else:
        row.status = "excluded"
        row.cycle = cycle
        row.excluded_at = now
        row.last_transition_at = now
        exclusion_grade.set_grade(row, grade, cycle=cycle, today=today, reason=grade_reason)
        row.cost_at_exclusion = cost
        if restrict_kwd_id:
            row.restrict_kwd_id = restrict_kwd_id
        # ★출처를 «우리»로 되돌린다(D-NAO-177 적대 리뷰 P2). 편입분을 void한 뒤 우리가 진짜로
        #   실행하면 이 행은 이제 우리 조치인데, `source='console_import'`가 남아 있으면
        #   **일기는 써서 wisdom이 먹는데** 성적표·오늘 집계·Slack 브리핑에선 통째로 빠진다 —
        #   측정에서만 사라지는 조치가 생긴다. 이 함수가 곧 「우리가 했다」의 정의다.
        row.source = None
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
        # ★None 가능 — `무관`·`미검증` 등급은 만료일이 없다(영구·보류). 종전 기본값
        #   (`성과미달`)에서는 항상 날짜라 기존 호출부의 동작이 바뀌지 않는다.
        "cycle": cycle_out, "grade": grade,
        "next_review_at": review_at.isoformat() if review_at else None, "diary": wrote,
    }


CONSOLE_IMPORT_SOURCE = "console_import"


def import_console_exclusions(
    db: Session, *, rows: list[dict], now: datetime | None = None
) -> dict:
    """콘솔에 **이미 걸려 있는** 제외를 장부에 일괄 편입한다(원장 전용 — 일기 없음).

    ## 왜 `record_execution`을 재사용하지 않나 (이 함수의 존재 이유 전부)
    `record_execution`은 원장 행과 **diary `execute` 행을 동시에** 만든다. 그게 그 함수의
    목적이다 — 사람이 **방금** 실행한 조치를 학습 사슬에 태우는 것.
    그런데 여기 들어오는 것은 **시점을 모르는 과거 조치**다. 그 경로로 43건을 부으면
    「오늘 실행된 조치 43건」이라는 거짓 일기가 생기고, 각각 D+1 소급 대상이 되어
    **13일 만의 진짜 표본 1건(「골프」)이 쓰레기 43건에 익사한다.**
    그래서 검증(`_require`)만 공유하고 **일기 경로는 공유하지 않는다.**

    ## 무엇을 하고 무엇을 안 하나
    - 한다: 원장 upsert(`source='console_import'`) · 행 단위 성공/거부 보고 ·
      콘솔이 알려준 실제 등록시각을 **`console_excluded_at`에** 보존(D-NAO-177)
    - 안 한다: 일기 · 네이버 쓰기 · `cost_at_exclusion` 추정(콘솔이 안 주면 0) ·
      **실행 시점 추정** — 시각을 안 주면 `console_excluded_at`은 **NULL로 둔다.**
      `excluded_at`은 예나 지금이나 **편입 시각**이고(장부가 이 행을 세운 시각), 그래서
      성적표는 편입분을 판정하지 않는다(`source`로 가른다).

    ## 왜 시각을 `excluded_at`에 안 넣나 (D-NAO-177)
    D-NAO-176은 「콘솔이 제외 시점을 안 알려준다」를 전제로 만들었는데 그 전제가 틀렸다 —
    「제외 검색어」 탭에 `등록시각`이 있다. 그렇다고 그 값을 `excluded_at`에 넣으면
    ①`void_execution`의 일기 매칭 하한이 1년 반 벌어져 무관한 옛 일기를 붙잡고
    ②`exclusion_survival`이 편입 직후 전건을 「방치」로 세어 전역 배너가 거짓 빨강이 된다.
    뜻이 다른 값이므로 칸을 나눈다. **모르면 NULL** — 편입 시각으로 메우지 않는다.

    ## 왜 전체 롤백이 아니라 행 단위인가
    43건 중 1건이 오타라고 42건이 죽으면 사람이 그 1건을 찾느라 전체를 다시 붙여넣는다.
    거부는 **그 행만** 하고 사유를 돌려준다(어느 행이 왜 거부됐는지가 응답에 있다).

    Args:
        rows: [{campaign_id, adgroup_id, search_term, restrict_kwd_id?, note?,
                console_excluded_at?(콘솔이 보여준 등록시각 — 모르면 생략. `excluded_at`과
                이름이 다른 이유는 뜻이 다르기 때문이다: 그쪽은 장부가 세운 시각이다)}, ...]

    반환: {imported, skipped, rejected, dated, undated, results:[행별 결과], as_of}
    """
    now = now or kst_now()
    results: list[dict] = []
    imported = skipped = rejected = 0
    dated = 0  # 콘솔 등록시각을 «아는» 편입분. 나머지는 모르는 채로 들어간 것이다.

    for idx, raw in enumerate(rows):
        try:
            campaign_id = _require(raw.get("campaign_id"), "campaign_id", max_len=50)
            adgroup_id = _require(raw.get("adgroup_id"), "adgroup_id", max_len=50)
            search_term = _require(raw.get("search_term"), "search_term", max_len=300)
            console_at = _parse_console_excluded_at(raw.get("console_excluded_at"), now=now)
        except ExclusionInputError as e:
            rejected += 1
            results.append({"row": idx, "result": "rejected", "reason": str(e),
                            "search_term": (raw.get("search_term") or "")[:300]})
            continue

        existing = (
            db.query(NaverSearchTermExclusion)
            .filter(
                NaverSearchTermExclusion.adgroup_id == adgroup_id,
                NaverSearchTermExclusion.search_term == search_term,
            )
            .first()
        )
        if existing is not None and existing.status == "excluded":
            # 이미 아는 제외 — 상태·출처는 덮지 않는다. 특히 `source`를 덮으면 우리가 실행한
            # 행이 「편입분」으로 뒤바뀌어 성적표에서 조용히 사라진다.
            # ★단 **콘솔 등록시각은 비어 있으면 채운다**(D-NAO-177 적대 리뷰 P1-2). 종전엔
            #   이 분기가 준 값을 아무 신호 없이 버렸고, 이 칸을 채울 다른 입구가 아예 없어
            #   («한 번 already_known이면 영영 미상») 계약 목표 ①이 무력화됐다. 실제로 prod의
            #   「골프」가 이 케이스다 — 우리가 8/11 23:27에 보고한 행이고 콘솔 43건 목록에도
            #   들어 있어, 첫 붓기에서 콘솔 시각 22:26이 그대로 버려질 참이었다.
            #   ⚠️`excluded_at`·`source`·`status`는 그대로다 — 이건 «장부가 세운 시각»이 아니라
            #   «콘솔에 걸린 시각»을 적는 것이고, 그래서 성적표 판정 대상 여부도 안 바뀐다.
            filled = False
            if console_at is not None and existing.console_excluded_at is None:
                existing.console_excluded_at = console_at
                db.commit()
                filled = True
            skipped += 1
            results.append({"row": idx, "result": "already_known", "search_term": search_term,
                            "exclusion_id": existing.id, "source": existing.source,
                            # 준 값이 어떻게 처리됐는지 행마다 말한다(조용한 폐기 금지).
                            "console_excluded_at": (
                                existing.console_excluded_at.isoformat()
                                if existing.console_excluded_at else None
                            ),
                            "console_time_filled": filled})
            continue

        note = (raw.get("note") or "콘솔에 이미 걸려 있던 제외를 장부에 편입")[:200]
        # ★편입분의 등급은 «미검증»이다(계약 §4-B⑥·⑦) — 실행 시점도 근거도 모르는 과거
        #   조치라 판정 근거가 없다. 그리고 미검증의 기본 만료는 **보류(NULL)**이므로
        #   종전의 `next_review_at=None`과 글자 그대로 같다. 등급이 붙는 이유는 이 NULL이
        #   «영구»(무관)가 아니라 «보류»임을 다음 재개방 로직이 알아야 하기 때문이다.
        import_grade = exclusion_grade.GRADE_UNVERIFIED
        import_reason = "import_console_exclusions — 콘솔 편입(실행 시점·근거 미상)"
        if existing is None:
            row = exclusion_grade.new_exclusion(
                campaign_id=campaign_id, adgroup_id=adgroup_id, search_term=search_term,
                restrict_kwd_id=(raw.get("restrict_kwd_id") or None),
                cycle=1,
                # ★`excluded_at`은 편입 시각이지 실제 제외 시각이 아니다 — 이 칸의 뜻은
                #   「장부가 이 행을 제외로 세운 시각」이고 일기 매칭·방치 판정이 거기 기대어
                #   있다. 실제 시각은 옆 칸(`console_excluded_at`)에 넣고, 모르면 **NULL**이다.
                #   어느 쪽이든 이 행은 성적표 판정 대상에서 빠진다(source로 가른다).
                now=now, console_excluded_at=console_at,
                cost_at_exclusion=0, source=CONSOLE_IMPORT_SOURCE,
                grade=import_grade, grade_reason=import_reason,
            )
            row.live_note = note
            db.add(row)
            outcome = "imported"
        else:
            # restored/probation/void 행의 재편입 — 상태만 되돌리고 출처를 편입으로 바꾼다.
            row = existing
            row.status = "excluded"
            row.last_transition_at = now
            row.excluded_at = now
            exclusion_grade.set_grade(
                row, import_grade, cycle=row.cycle, today=now.date(), reason=import_reason,
            )
            row.source = CONSOLE_IMPORT_SOURCE
            row.live_state = None
            row.live_checked_at = None
            row.live_note = note
            # ★아는 값을 모르는 값으로 덮지 않는다: 시각 없이 다시 편입해도 전에 받아 둔
            #   콘솔 등록시각은 그대로 둔다(재편입이 정보를 «잃는» 방향으로 가면 안 된다).
            if console_at is not None:
                row.console_excluded_at = console_at
            if raw.get("restrict_kwd_id"):
                row.restrict_kwd_id = raw["restrict_kwd_id"]
            outcome = "imported"

        db.commit()
        imported += 1
        if row.console_excluded_at is not None:
            dated += 1
        results.append({"row": idx, "result": outcome, "search_term": search_term,
                        "exclusion_id": row.id,
                        "console_excluded_at": (
                            row.console_excluded_at.isoformat() if row.console_excluded_at else None
                        )})

    log.info(
        "[콘솔편입] 편입 %d(시각 아는 것 %d) · 이미앎 %d · 거부 %d (일기 0건)",
        imported, dated, skipped, rejected,
    )
    return {
        "imported": imported, "skipped": skipped, "rejected": rejected,
        # ★편입분 중 «실제 제외 시각을 아는 것»과 모르는 것을 갈라 낸다. 합쳐 세면 나중에
        #   「43건 다 시각이 있다」는 착시가 생기고, 그 착시 위에서 창을 자르면 거짓 판정이 된다.
        "dated": dated, "undated": imported - dated,
        "results": results, "as_of": now.isoformat(),
        # ★계약 금지선의 자기 증명 — 이 경로는 학습 사슬에 아무것도 넣지 않는다.
        "diary_written": 0,
    }


def void_execution(
    db: Session, *, exclusion_id: int, reason: str, now: datetime | None = None
) -> dict:
    """잘못 들어온 장부 행 1건을 무효화한다(원장 상태 전이 + 짝인 일기 행 중화).

    ★왜 이 경로가 필요한가: 종전엔 이 원장에 **정정 경로가 아예 없었다**. 오타 하나로 들어간
      행이 성적표·생존 감시 배너·SS레인·학습 사슬에 영구히 남았고, 남은 유일한 «수정»은 같은
      (adgroup, term)으로 재등록하는 것뿐이었다(그건 지우는 게 아니라 덮는 것이다).
      콘솔 제외 약 42건을 장부에 넣는 순간 이 부재가 실비용이 되므로 **넣기 전에** 만든다.

    무엇을 하나:
      ① 원장 행 → status=void. 모든 소비자가 status를 명시적으로 좁혀 읽으므로 배너·성적표
         ·SS레인·자동 발견 대조에서 전부 빠진다(행은 감사용으로 남는다).
      ② 짝인 일기 행 → event_type=voided. diary_outcome.EVENT_TYPES 밖이라 결과 소급 대상에서
         빠지고, 따라서 wisdom 후보로도 안 올라간다. **①만 하면 장부에선 사라졌는데 학습은
         계속 그 조치를 먹는다** — 「보이는 곳에서만 지운다」가 이 리포의 반복 실패 유형이다.

    ⚠️**이미 학습에 반영된 것은 되돌리지 못한다**: 일기 행에 outcome_json이 이미 채워졌다면
      wisdom 후보 승률에 이미 셈이 들어갔을 수 있다. 그건 이 함수가 못 지우므로 숨기지 않고
      `wisdom_may_have_counted`로 표면화한다(조용한 부분 실패 금지).
      ★그 값은 **3상**이다: True(셌을 수 있음) / False(아직 아님) / **None(확인 못 함)**.
      일기를 못 찾았거나 구분할 수 없을 때 False로 단언하면 «확인 안 함»이 «안 셌음»으로
      둔갑한다 — 이 리포가 반복해서 당한 모양이다(교훈 #123).

    반환: {result, exclusion_id, status, previous_status, diary_voided(int),
           wisdom_may_have_counted(bool|None), diary_note(str|None)}

    Raises:
        ExclusionInputError: 대상 행이 없거나 사유가 비었을 때.
    """
    reason = _require(reason, "reason", max_len=200)
    now = now or kst_now()

    row = db.get(NaverSearchTermExclusion, exclusion_id)
    if row is None:
        raise ExclusionInputError(f"장부에 exclusion_id={exclusion_id} 행이 없다")
    if row.status == VOID_STATUS:
        # 멱등 — 두 번 눌러도 같은 결과. 일기 중화는 이미 끝났으므로 다시 세지 않는다.
        # wisdom 여부는 **이번 호출이 확인한 것이 아니므로** False가 아니라 None이다.
        return {
            "result": "already_void", "exclusion_id": row.id, "status": row.status,
            "diary_voided": 0, "wisdom_may_have_counted": None,
            "diary_note": "이미 무효화된 행이다 — 이번 호출은 일기를 다시 확인하지 않았다",
        }

    prev_status = row.status
    row.status = VOID_STATUS
    row.last_transition_at = now
    # 생존 감시의 옛 판정이 무효 행에 남아 있으면 나중에 되살릴 때 그 판정이 새 조치를 덮는다.
    row.live_state = None
    row.live_checked_at = None
    row.live_note = f"무효화({prev_status}→{VOID_STATUS}): {reason}"[:200]

    # ── 짝인 일기 행 중화 ──
    # ①두 쓰기 주체의 표기를 모두 본다(DIARY_ACTIONS·전문/절단 target_id 양쪽).
    # ②**이번 주기의 일기만** 건드린다. excluded_at은 KST naive이고 created_at은 UTC이므로
    #   9시간을 빼고 비교한다([[sqlite-server-default-now-is-utc]]). 이게 없으면 1주기의
    #   «정당했던» 학습 표본까지 2주기 오등록 때문에 같이 죽는다.
    trunc = row.search_term[:50]
    since_utc = row.excluded_at - timedelta(hours=9) - _DIARY_MATCH_SLACK
    entries = (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.event_type == "execute",
            OpsDiaryEntry.action.in_(DIARY_ACTIONS),
            OpsDiaryEntry.adgroup_id == row.adgroup_id,
            OpsDiaryEntry.target_id.in_({trunc, row.search_term}),
            OpsDiaryEntry.created_at >= since_utc,
        )
        .all()
    )

    # ★target_id는 [:50]으로 잘려 저장되므로 앞 50자가 같은 두 검색어를 **구분할 수 없다.**
    #   그런 충돌이 있으면 남의 일기를 죽이느니 아무것도 안 건드리고 사람에게 넘긴다
    #   (그 경우 다른 행은 excluded로 멀쩡히 남아 화면 어디에도 이상이 안 보인다).
    ambiguous = any(
        other.search_term[:50] == trunc
        for other in db.query(NaverSearchTermExclusion).filter(
            NaverSearchTermExclusion.adgroup_id == row.adgroup_id,
            NaverSearchTermExclusion.id != row.id,
            NaverSearchTermExclusion.status != VOID_STATUS,
        ).all()
    )

    counted: bool | None
    if ambiguous:
        entries = []
        counted = None
        note = "앞 50자가 같은 다른 검색어가 있어 일기를 구분할 수 없다 — 중화하지 않았다"
    elif prev_status is not None and row.source == CONSOLE_IMPORT_SOURCE:
        # ★여기서만 «안 셌다»를 단언할 수 있다: 편입 경로(import_console_exclusions)는 애초에
        #   일기를 만들지 않는다(그 함수의 존재 이유이자 계약 금지선이고, 테스트가 지킨다).
        #   즉 이건 «못 찾음»이 아니라 «없음이 증명됨»이다. 43건 전부에 「확인하지 못했다」가
        #   붙으면 3상 설계의 의미가 희석된다.
        counted = False
        note = "콘솔 편입 행이라 애초에 일기가 없다 — 학습에 셌을 여지가 없다"
    elif not entries:
        # ★«일기가 없다»와 «일기를 못 찾았다»는 같은 0이다(교훈 #123). 확인하지 않은 것을
        #   False로 단언하지 않는다 — 모르면 None으로 내보내고 사유를 붙인다.
        counted = None
        note = "짝인 일기 행을 찾지 못했다 — 학습 반영 여부를 확인하지 못했다"
    else:
        counted = any(e.outcome_json is not None for e in entries)
        note = None
        for e in entries:
            e.event_type = VOIDED_EVENT_TYPE
            e.rationale = f"{e.rationale or ''} [무효화: {reason}]"[:1000]

    db.commit()
    log.info(
        "[제외무효화] id=%s %s→void 일기 %d행 중화 (학습 기반영=%s, 비고=%s) 사유=%s",
        exclusion_id, prev_status, len(entries), counted, note, reason,
    )
    return {
        "result": "voided", "exclusion_id": row.id, "status": VOID_STATUS,
        "previous_status": prev_status, "diary_voided": len(entries),
        # True=이미 학습에 셌을 수 있음 / False=아직 아님 / **None=확인 못 함**
        "wisdom_may_have_counted": counted,
        "diary_note": note,
    }


def detect_new_exclusions(
    db: Session, *, adgroup_ids: list[str] | None = None, now: datetime | None = None
) -> dict:
    """라이브 제외키워드를 읽어 **원장에 없는 제외를 스스로 발견**해 편입한다.

    ★편입은 **일기 없는 입구**(`import_console_exclusions`, `source='console_import'`)로 간다
      (D-NAO-179). 여기 걸리는 행은 정의상 «우리가 한 조치가 아니다» — 라이브에 있는데 우리
      원장에 없다는 게 그 뜻이다. 종전엔 `record_execution(discovered=True)`로 들어가 diary
      `execute` 행을 같이 만들었고, 그러면 남이 과거에 자른 것이 「오늘 우리가 한 조치」로
      학습 사슬에 실려 승률을 오염시킨다. 그리고 읽기가 두 타입 union으로 넓어진 지금
      (D-NAO-179) 그 경로엔 수백 건이 쏟아진다.
      대가를 알고 쓴다: 편입분은 **성적표 판정 대상이 아니다**(`source`로 가른다) — 남의 조치를
      우리 성적으로 세지 않겠다는 뜻이고, 그게 이 문을 고른 이유 그 자체다.

    ★왜 이 경로가 있나: 사람이 콘솔에서 자르고 화면에서 «실행했음»을 누르는 것을 잊으면, 그
      조치는 영원히 시스템 밖이다. 보고에 의존하는 경로는 이 리포에서 이미 한 번 실패했다
      (대행사 되돌림 2회 중 1회는 change_log에 행조차 없었다). **상태 대조가 사건 보고보다
      튼튼하다**는 같은 원리를 여기에도 쓴다.

    ★**쇼핑도 이 경로로 돈다**(D-NAO-180, 2026-08-17 해소). 2026-08-11엔 「쇼핑 광고그룹의
      `GET restricted-keywords`가 200/0건이라 되읽히는지 못 가른다」로 남아 있었다. 답은
      **되읽힌다 — 다른 리소스로**다: `/ncc/targets`의 `RESTRICT_KEYWORD_TARGET`이 같은 계정에서
      3,880건/116그룹을 돌려주고, 콘솔 캡처로 원장에 넣은 43건과 차집합 양쪽 0으로 일치했다.
      소스 선택은 `naver_sa_writer.get_live_exclusions`가 유형을 보고 한다.
      아직 소스를 모르는 유형(브랜드검색·플레이스 등)은 None으로 와서 `unverifiable_groups`에
      쌓인다 — 0건과 «못 읽음»이 같아 보이면 안 된다.

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
    pending_import: list[dict] = []
    errors: list[str] = []
    groups_with_zero = 0
    unverifiable_groups = 0
    type_unknown_groups = 0
    unattributable: list[dict] = []
    for adgroup_id in adgroup_ids:
        # ★유형이 라이브 소스를 고른다(D-NAO-180): 파워링크는 `restricted-keywords`,
        #   **쇼핑은 `/ncc/targets`**. 종전 주석은 「자동 발견은 구조적으로 파워링크 전용이고
        #   쇼핑의 유일한 입구는 화면 수동 기록」이라고 적혀 있었는데, 그 전제가 2026-08-17에
        #   무너졌다 — 안 보였던 것은 리소스 하나였지 쇼핑 제외가 아니다(3,880건/116그룹이
        #   읽히고 콘솔 캡처분 43건과 차집합 0). 그래서 쇼핑도 자동 발견에 들어온다.
        adgroup_type = naver_sa_writer.get_adgroup_type(adgroup_id)
        if adgroup_type is None:
            # ★«모른다»는 «WEB_SITE가 아니다»가 아니다(get_adgroup_type docstring이 직접 그렇게
            #   적어 뒀다). 종전 조건 `not in (None, "WEB_SITE")`는 모름을 **대조 가능 쪽**에
            #   넣었다 — 그러면 쇼핑 그룹이 유형 조회 500 한 번으로 restricted-keywords 경로를
            #   타고, 그 API는 쇼핑에서 200/0건이라 «제외 0건»으로 조용히 세어진다.
            #   exclusion_survival.py가 정확히 같은 결함으로 P1을 맞았는데(D-NAO-174) 같은
            #   규율이 이 파일엔 안 들어와 있었다. 모르면 «못 봤다» 쪽에 센다(fail-closed).
            type_unknown_groups += 1
            continue
        try:
            live = naver_sa_writer.get_live_exclusions(adgroup_id, adgroup_type)
        except Exception as e:  # noqa: BLE001 — 한 그룹 실패가 나머지를 지우지 않는다
            errors.append(f"{adgroup_id}: {type(e).__name__}: {e}")
            continue
        if live is None:
            # 소스를 아직 모르는 유형(브랜드검색·플레이스 등)만 여기 온다. «못 봤다»를 «0건»에
            # 섞지 않는다 — 그 혼동이 D-NAO-174에서 P1을 맞은 결함 그 자체다.
            unverifiable_groups += 1
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
            # ★캠페인을 못 붙이면 **등록하지 않되 세어 낸다.** 종전엔 `camp_of.get(id, "")`로
            #   빈 campaign_id가 원장·일기에 그대로 들어갔고(이월 P2), 그 행은 학습 시그니처에서
            #   캠페인 축을 잃는다. 그렇다고 조회 전에 그룹째 건너뛰면 «거기 제외가 있는데 우리가
            #   못 적었다»는 사실 자체가 안 보인다 — 그건 이 리포가 반복해서 당한 형태다.
            #   그래서 조회는 하고, 기록만 거부하고, 목록으로 표면화한다.
            if not camp_of.get(adgroup_id):
                unattributable.append({"adgroup_id": adgroup_id, "search_term": term})
                continue
            # ★일기를 만들지 않는 입구로 넣는다 (D-NAO-179). 여기 들어오는 행은 정의상
            #   **우리가 한 조치가 아니다** — 라이브에 있는데 우리 원장에 없다는 것이 그
            #   뜻이다. 그런데 종전 경로(`record_execution(discovered=True)`)는 원장과 함께
            #   diary `execute` 행을 만들었고, 그러면 남이 과거에 콘솔에서 자른 것이
            #   「오늘 우리가 실행한 조치」로 학습 사슬에 실린다.
            #   D-NAO-176이 그 위험을 이미 문장으로 적어 뒀다(import_console_exclusions
            #   독스트링: 43건을 부으면 진짜 표본 1건이 쓰레기 43건에 익사한다). 그 입구가
            #   있는데 이 경로만 옛 문으로 가고 있었다 — 타입 union으로 읽기를 넓히는 순간
            #   그 문으로 수백 건이 쏟아지므로 여기서 문을 바꾼다.
            #   ★`regTm`은 콘솔 등록시각 그 자체다 — **캡처 없이 시각까지 채워진다.**
            #     쇼핑도 마찬가지다(D-NAO-180): `/ncc/targets` 항목의 `date`(epoch)를
            #     `get_shopping_exclusions`가 같은 `regTm` 칸에 UTC ISO로 실어 보내므로 이
            #     줄은 유형을 몰라도 된다. epoch를 UTC로 읽는 것이 옳다는 근거는 라이브
            #     대조다(직접 만든 항목의 date가 같은 응답 editTm과 초 단위까지 일치).
            pending_import.append({
                "campaign_id": camp_of[adgroup_id],
                "adgroup_id": adgroup_id,
                "search_term": term,
                "restrict_kwd_id": entry.get("nccAdgroupRestrictKwdId"),
                "console_excluded_at": _parse_reg_tm(entry.get("regTm"), now=now),
                "note": (
                    "라이브 제외키워드 목록에 있는데 우리 원장에 없어 편입했다"
                    f" (type={entry.get('type') or '미상'})"
                ),
            })

    # ── 편입은 한 번에 (행 단위 성공/거부는 그 함수의 계약이다 — 1건 오타가 나머지를 안 죽인다) ──
    imported = skipped = rejected = 0
    if pending_import:
        out = import_console_exclusions(db, rows=pending_import, now=now)
        imported, skipped, rejected = out["imported"], out["skipped"], out["rejected"]
        for res in out["results"]:
            src = pending_import[res["row"]]
            if res["result"] == "rejected":
                errors.append(f"{src['adgroup_id']}/{src['search_term']}: {res['reason']}")
                continue
            recorded.append({
                "adgroup_id": src["adgroup_id"], "search_term": src["search_term"], **res,
            })

    return {
        "scanned_groups": len(adgroup_ids),
        # 편입 결과 — 「원장에 새로 선 행」과 「이미 알고 있던 행」을 가른다. ★일기는 0건이다
        # (이 경로는 학습 사슬에 태우지 않는다 — 우리가 한 조치가 아니기 때문이다).
        "imported": imported,
        "already_known": skipped,
        "rejected": rejected,
        # 라이브 소스를 아직 모르는 유형이라 건너뛴 그룹 — «찾은 게 없다»와 «볼 수 없다»를
        # 가른다. ★D-NAO-180 이후 쇼핑은 여기 안 들어온다(실제로 읽히므로). 이 수가 갑자기
        # 늘면 그건 유형이 늘었다는 뜻이지 「조용히 정상」이 아니다.
        "unverifiable_groups": unverifiable_groups,
        # 유형 조회 자체가 실패한 그룹 — 쇼핑(위)과도 다르다. «모름»을 0건에 섞지 않는다.
        "type_unknown_groups": type_unknown_groups,
        # 라이브엔 있는데 캠페인을 못 붙여 **등록을 보류한** 제외. 빈 campaign_id로 넣느니 안
        # 넣되, «못 적은 것이 있다»를 목록으로 남긴다(조용한 누락 금지).
        "unattributable": unattributable,
        "unattributable_count": len(unattributable),
        "groups_with_zero": groups_with_zero,
        "recorded": recorded,
        "errors": errors,
        "as_of": now.isoformat(),
    }
