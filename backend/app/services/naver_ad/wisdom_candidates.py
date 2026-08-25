# wisdom_candidates.py — candidate_sa (D-NAO-54 P3 승격층, docs/PLAN_naver-ad-diary-wisdom.md §P3)
# 역할: 결과가 기입된 diary 행(execute/blocked + outcome_json에 d1|d7)을 스캔해 (캠페인×액션×
#   환경) 조건 시그니처로 반복 패턴 후보(ops_wisdom_candidates)를 뽑는다. 결과 방향은 시그니처가
#   아니라 good_count/bad_count로 세고 occurrences=good+bad로 정의한다(같은 조건의 good·bad를 한
#   후보에 모아 승률을 판단 — 리뷰 P2-2). 같은 시그니처 재등장은 방향 tally++·last_seen_at 갱신
#   (중복 entry id는 미가산), 신규는 후보 생성(observation은 규칙 기반 요약 — LLM 아님). cost=0/
#   결측 관찰은 중립이라 tally에 안 넣고 후보 생성/갱신도 skip(리뷰 P2-3). promoted/rejected
#   시그니처는 재수확하지 않지만, hidden은 재등장 시 pending으로 부활한다(리뷰 P2-1 — Ebbinghaus
#   재노출 강화). 읽기(diary·campaign_target_resolver) + wisdom_candidates 쓰기만(원칙18-1).
#   ★target_type=="search_term" 행은 outcome["d1"](캠페인 폴백)을 절대 소비하지 않는다
#   (D-NAO-178 금지선). 대신 outcome["d1_st"].status(stopped/leaking/ambiguous/no_data,
#   diary_outcome.py 참조)로 good/bad/skip을 판정한다 — S8(d1_st 소비 전환) 집행,
#   2026-08-25. `_outcome_window`/`_outcome_direction`(d1/d7 기반) 경로는 타지 않는다.
#
# ★D-NAO-248(2026-08-25, 부록 Q2 처분 (b′)) — 시그니처를 **전역**(campaign_id 미포함)으로
#   바꿨다. 옛 시그니처는 campaign_id가 선두라 표본이 캠페인 수만큼 쪼개졌다(§1: 4캠페인 합
#   91회 관찰이 45/38/5/3으로 갈려 전부 rejected — 근거가 더 두꺼운 쪽을 못 배웠다). 새
#   시그니처는 (campaign_type × action × 환경버킷[× experiment_batch]) 단위로 캠페인을 넘어
#   합산하고, 캠페인별 분해는 후보 행 «안»의 by_campaign_json에 병기한다(합산은 하되 이질성은
#   판사에게 보인다 — 부록 Q2). 경계(부록 Q3, 절대 안 섞임): ①campaign_type(SHOPPING/WEB_SITE/
#   BRAND_SEARCH — 같은 액션 이름이 다른 레버) ②experiment_batch(A/B·MOP열·대조군·홀드아웃).
#   상품군·BEP 수준은 경계가 «아니다»(이미 BEP 대비로 정규화됨 — 경계로 삼으면 원래 병이
#   재발한다). 두 «분리 버킷»이 있다: 실험배치 라벨(전역 풀과 절대 안 섞임) / fail-closed
#   미상(naver_campaign_settings 행이 없거나 campaign_type을 못 읽으면 — 전역 합산에 넣지
#   않고 캠페인 단위로 고립시킨다). 시그니처 접두사로 세 버킷을 구조적으로 분리한다:
#     "g|type|action|day|season|iphone|batch"  — 전역 풀(batch="") / 실험분리(batch=라벨)
#     "g?|campaign_id|action|day|season|iphone" — fail-closed 미상분리
#   두 접두사 모두 옛 시그니처(campaign_id 선두, 접두사 없음)와 **문자열이 겹칠 수 없다** —
#   기존 27건은 재수확 대상에 걸리지 않고 그대로 남는다(소급 재계산이 아니라 소급 재수확 —
#   같은 90일 일기 위에 새 grain의 새 행만 생긴다).
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverEntity, OpsDiaryEntry, OpsWisdomCandidate
from app.services.naver_ad import campaign_target_resolver
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 신형(전역) 시그니처 접두사 — 옛 시그니처(campaign_id 선두, 접두사 없음)와 절대 겹치지 않는다.
_GLOBAL_PREFIX = "g"    # g|campaign_type|action|day_class|season|iphone_window|experiment_batch
_UNKNOWN_PREFIX = "g?"  # g?|campaign_id|action|day_class|season|iphone_window (fail-closed 분리)

# 수확 대상 이벤트 — 실집행(execute)과 가드레일/구조 차단(blocked)만. reject(말미 stale 정리)·
# kill_switch는 제외(P2 리뷰 P3-2: reject는 blocked와 같은 제안의 2행이라 포함하면 이중계상).
HARVEST_EVENT_TYPES = ("execute", "blocked")

# 승격된 시그니처는 재수확 금지(tally조차 갱신 안 함). hidden은 제외 — 재등장하면 부활시킨다
# (리뷰 P2-1: 망각↔TTL 데드락 해소 + Ebbinghaus 재노출 강화).
#
# ★D-NAO-251 §4-① — 여기서 `rejected`가 빠졌다. 구판은 promoted/rejected를 함께 terminal로
#   묶어 **tally 갱신까지** 막았는데, 그게 「표본 부족」 기각을 자기충족 함정으로 만들었다:
#   판사가 *"45회 관찰이 단 이틀 안에 집중되어… 승격을 보류합니다"* 로 기각한 뒤 같은 조건으로
#   일주일에 818건이 더 쌓였으나 그 시그니처는 다시 볼 수 없었다. 「재현성 불명」이라 기각해
#   놓고 재현을 관측할 길을 코드가 닫아 버린 것이다.
#   promoted가 남아 있는 이유는 답이 달라야 하기 때문이다 — 승격 지혜의 사후 성적은 지혜
#   성적표가 «집행 결과»로 재는 것이지 관찰 tally로 뒤집을 것이 아니고, 승격↔기각 플립플롭은
#   브리핑 주입(`wisdom_apply.active_wisdom_prefix`)을 흔든다.
_TERMINAL_STATUSES = frozenset({"promoted"})

# ★D-NAO-251 §4-① 재개방 조건 — rejected 후보가 pending으로 «복귀»하는 문턱.
#   판정 시점 대비 배수(_REOPEN_MULTIPLE)와 절대 증분(_REOPEN_MIN_DELTA)을 **둘 다** 넘어야
#   한다: 배수만 두면 3회→6회 같은 잔챙이가 재심을 부르고, 절대값만 두면 표본이 큰 후보가
#   너무 자주 돌아온다.
#   ★이 세 숫자(2.0 · 5 · 2)는 근거 없는 초깃값이다(계약 §2-6·§8-3) — 「재심에서 판정이 바뀐
#   비율」을 잴 표면이 아직 없으므로 주기 감사의 재심 안건으로 남긴다.
_REOPEN_MULTIPLE = 2.0   # occurrences ≥ 판정시점 × 2.0
_REOPEN_MIN_DELTA = 5    # ∧ occurrences ≥ 판정시점 + 5
_MAX_REJUDGE = 2         # 재심 상한. 소진하면 다시 완전 terminal(tally도 멈춘다 — 행 비대 방지)


def _reopen_ready(cand, *, now=None) -> bool:
    """rejected 후보가 재개방 문턱을 넘었는가(순수 함수 — 테스트·리뷰가 여기만 보면 된다).

    기준선 `judged_occurrences`가 없으면 **재개방하지 않는다**(fail-closed) — 「어디서부터
    2배인지」를 모르는 채 여는 것은 문턱이 없는 것과 같다. 마이그레이션이 기존 판정분에
    현재 occurrences를 기준선으로 백필하므로, 기존 rejected는 «지금부터» 2배가 쌓여야 열린다
    (소급 재개방이 아니다 — 과거 판정 시점의 occurrences는 기록이 없어 복원 불가).
    """
    if (cand.rejudge_count or 0) >= _MAX_REJUDGE:
        return False
    base = cand.judged_occurrences
    if base is None:
        return False
    n = cand.occurrences or 0
    return n >= base * _REOPEN_MULTIPLE and n >= base + _REOPEN_MIN_DELTA

# 아이폰 출시 전후 ±N일을 launch_window로 본다(그 외 normal, offset None은 unknown).
_IPHONE_WINDOW_DAYS = 14

# ★D-NAO-248 §4-A(A2 관측 표면) — 소급 재수확 산출엔 이 라벨을 병기한다(계약 판단기준 원문:
#   "항상 소급 재수확 산출엔 「기존 재료의 재집계」 라벨을 병기한다"). harvest_candidates의
#   totals["note"]와 wisdom_scorecard의 후보 현황 블록이 **같은 문자열**을 써야 화면과 로그가
#   다른 말을 하지 않는다 — 그래서 상수로 한 곳에만 둔다.
RETRO_HARVEST_LABEL = "diary 90일 lookback 재집계 — 새 grain 신설이 곧 새 관찰 생성은 아니다"

# created_at(UTC) 스캔 하한 — diary_outcome가 60일까지만 결과를 채우므로(그 뒤 소급 없음)
# 여유를 둔 90일. 시그니처 dedup(entry id 중복 제외)이 있어 재스캔은 멱등이지만, 무한히 커지는
# 쿼리를 막는 안전 상한이다.
_HARVEST_LOOKBACK_DAYS = 90


def _day_class(entry: OpsDiaryEntry) -> str:
    """휴일 우선 → 주말 → 평일. is_kr_holiday True면 holiday(요일 무관), weekday 5/6=weekend,
    0~4=weekday, weekday None(스냅샷 결측)=unknown."""
    if entry.is_kr_holiday:
        return "holiday"
    if entry.weekday is None:
        return "unknown"
    return "weekend" if entry.weekday >= 5 else "weekday"


def _iphone_window(offset: int | None) -> str:
    """출시 오프셋 → 3버킷. None=unknown, |offset|≤14=launch_window, 그 외=normal
    (env 캐비어트 P2 §env: 미래 출시일 미등록 시 큰 양수 → normal로 흡수)."""
    if offset is None:
        return "unknown"
    return "launch_window" if abs(offset) <= _IPHONE_WINDOW_DAYS else "normal"


def _outcome_window(outcome: dict) -> dict | None:
    """d7 우선(정착 성숙), 없으면 d1. 둘 다 없으면 None(스캔 대상 아님)."""
    if outcome.get("d7"):
        return outcome["d7"]
    if outcome.get("d1"):
        return outcome["d1"]
    return None


def _outcome_direction(
    db: Session, entry: OpsDiaryEntry, window: dict, *, bep_cache: dict | None = None,
) -> str | None:
    """결과 방향 3분류. cost가 0/None/결측이면 'neutral'(돈 안 쓴 관찰 = 패턴 증거 아님 →
    tally 미기여·후보 skip, 리뷰 P2-3). 비용이 있으면 **손익분기(BEP) 대비** good/bad —
    good = roas_c >= 캠페인 bep_roas. 해석 실패/미확보(None)면 None 반환(후보 생성 skip,
    neutral과 구분).

    ★D-NAO-223(M3-b 축 ⓑ, 2026-08-22) — 기준자를 `target_roas` → `bep_roas`로 교정했다.
      `models.py`가 `target_roas = bep_roas x 공격성 배수`로 정의하므로 `target >= bep`이고,
      target을 기준자로 쓰면 **본전을 넘겨 실제로 총이익을 낸 구간(bep <= roas < target)이
      통째로 `bad`로 떨어진다.** 그 구간이 정확히 트랙 목표(D-NAO-59) 원문
      *"Roas는 떨어지지만 매출이 늘어서 총 이익이 늘어나는 경우"*가 사는 자리다.
      그리고 이 tally가 그대로 패턴 후보의 승률이 되어 **지혜 승격 게이트**로 올라가는데
      (`_observation`), 북극성 M5의 성공 정의는 「지혜 -> 총이익 기여 양수」다 —
      승격 게이트와 그 층의 성공 정의가 다른 것을 재고 있었다(ref 90 §3).
    ★`target_roas_override`가 개입하지 않는 것도 의도다 — 본전은 사람이 덮어쓰는 값이 아니다
      (`campaign_target_resolver.resolve_bep_roas` 참조).
    ★`bep_cache`: 회차 내 캠페인별 캐시. 없으면 이 호출 한정(기존 호출부 호환).
    """
    cost = window.get("cost")
    if not cost:  # 0·None·결측 모두 중립 — 돈을 안 쓴 관찰은 원칙 증거가 아님
        return "neutral"
    bep_cache = bep_cache if bep_cache is not None else {}
    if entry.campaign_id in bep_cache:
        bep = bep_cache[entry.campaign_id]
    else:
        try:
            bep = campaign_target_resolver.resolve_bep_roas(db, entry.campaign_id).get("bep_roas")
        except Exception as e:  # noqa: BLE001 — 해석 실패는 후보 생성 skip(부풀림 방지)
            log.warning("wisdom_candidates: BEP 해석 실패(후보 skip): campaign=%s: %s", entry.campaign_id, e)
            bep = None
        bep_cache[entry.campaign_id] = bep
    if bep is None:
        return None
    roas_c = window.get("roas_c")
    good = roas_c is not None and roas_c >= float(bep)
    return "good" if good else "bad"


# d1_st.status → 스킵 사유 totals 키. good('stopped')·bad('leaking')는 여기 없다(direction이
# 나오는 경우라 스킵 카운터가 필요 없다). status가 이 매핑에도 4값에도 없으면(코드가 미래에
# 새 값을 추가했는데 이 파일이 못 따라간 경우) fail-closed로 unknown_status에 떨어뜨린다 —
# 조용히 good/bad로 잘못 세는 것보다 스킵되는 쪽이 안전하다(원칙: 알려진 값만 소비).
_D1_ST_SKIP_COUNTER = {
    "ambiguous": "skipped_search_term_ambiguous",
    "no_data": "skipped_search_term_no_data",
}
_D1_ST_UNKNOWN_STATUS_COUNTER = "skipped_search_term_unknown_status"


def _search_term_direction(outcome: dict) -> tuple[str | None, str | None]:
    """검색어 제외 행 전용 방향 판정 — `outcome["d1_st"]["status"]`만 읽는다.

    ★금지선(D-NAO-178 원문 이유 그대로, S8 집행): 이 함수는 `outcome["d1"]`을 **절대** 읽지
      않는다. `_grain_and_target`의 campaign 폴백 탓에 검색어 행의 d1은 「그 캠페인 전체의
      하루 성과」이지 조치의 성적이 아니다(2026-08-13 라이브: d1 43,084원 vs 「골프」 30일
      31,411원). `_outcome_window`/`_outcome_direction`을 재사용하지 않는 것도 같은 이유다
      — 그 둘은 d1을 먹는다.
    ★두 번째 이유: 제외 조치의 성공 지표는 **비용 정지**다. 기존 ROAS 규칙
      (`_outcome_direction`)을 재사용하면 완벽한 성공(cost=0)이 'neutral'로 버려진다
      (diary_outcome.py d1_st 섹션 헤더가 이미 못 박은 설계 — status 문자열로만 판정한다).

    반환 (direction, skip_reason). direction이 not None이면 good/bad고 skip_reason은 None.
    direction이 None이면 skip_reason은 "absent"(d1_st 자체가 없음) 또는 totals의 완성된
    카운터 키(caller가 그대로 인덱싱한다 — 접두사를 다시 붙이지 않는다).
    """
    d1_st = outcome.get("d1_st")
    if not d1_st:
        return None, "absent"
    status = d1_st.get("status")
    if status == "stopped":
        return "good", None
    if status == "leaking":
        return "bad", None
    return None, _D1_ST_SKIP_COUNTER.get(status, _D1_ST_UNKNOWN_STATUS_COUNTER)


def _observation(campaign_id: str, action: str | None, env: dict, good_count: int, bad_count: int) -> str:
    """규칙 기반 요약문(LLM 아님) — 시그니처(조건)가 무엇을 묶는지 + 현재 good/bad 성적 한 줄.
    방향을 고정 서술하지 않고 tally로 서술한다(리뷰 P2-2: 조건 후보가 승률을 담는다).

    ★레거시(campaign_id 선두 시그니처) 전용 경로 — D-NAO-248 이후로도 이 함수 자체는 안 바뀐다
    (기존 27행이 재수확되지 않으니 이 함수가 다시 호출될 일은 없지만, 시그니처가 이 형태를
    다시 안 만들 뿐 함수 자체를 지우면 "레거시 요약문 경로가 깨졌다"는 신호를 잃는다)."""
    total = good_count + bad_count
    return (
        f"[패턴] 캠페인 {campaign_id}의 {action or '(액션미상)'} — "
        f"{env['day_class']}·{env['season'] or '계절미상'}·아이폰 {env['iphone_window']} 조건에서 "
        f"관찰 {total}회(good {good_count}/bad {bad_count})."
    )


def _observation_global(
    campaign_type: str | None, action: str | None, env: dict, good_count: int, bad_count: int,
    campaign_count: int, experiment_batch: str | None,
) -> str:
    """전역/실험분리 후보 요약문 — ★캠페인 ID를 담지 않는다(D-NAO-248). 캠페인별 분해는
    by_campaign_json이 담당한다. 이 수확은 «새로 배운 관찰»이 아니라 기존 90일 일기의
    재집계임을 호출부(diagnosis 등)가 옮길 수 있게 말미에 명시한다."""
    total = good_count + bad_count
    scope = f"실험배치 '{experiment_batch}'" if experiment_batch else "전역"
    return (
        f"[패턴·{scope}] {campaign_type or '(유형미상)'} 유형의 {action or '(액션미상)'} — "
        f"{env['day_class']}·{env['season'] or '계절미상'}·아이폰 {env['iphone_window']} 조건에서 "
        f"관찰 {total}회(good {good_count}/bad {bad_count}), 캠페인 {campaign_count}개에 걸침 "
        f"(기존 일기 재집계 — 새 관찰 아님)."
    )


def _observation_unknown(campaign_id: str, action: str | None, env: dict, good_count: int, bad_count: int) -> str:
    """fail-closed 미상분리 후보 요약문 — campaign_type/experiment_batch를 못 읽어 전역 풀에
    합류시키지 않고 캠페인 단위로 고립시킨 관찰이다."""
    total = good_count + bad_count
    return (
        f"[패턴·라벨미상] 캠페인 {campaign_id}의 {action or '(액션미상)'} — "
        f"{env['day_class']}·{env['season'] or '계절미상'}·아이폰 {env['iphone_window']} 조건에서 "
        f"관찰 {total}회(good {good_count}/bad {bad_count}) — campaign_type/실험배치 미상, "
        f"fail-closed 분리(전역 풀 미참여, 기존 일기 재집계)."
    )


def _campaign_boundary(
    db: Session, campaign_id: str, cache: dict,
) -> tuple[str | None, str | None, bool]:
    """캠페인의 전역 풀링 경계 축 (campaign_type, experiment_batch, known) — 회차 내 캐시
    (bep_cache와 동일 관례: 행마다 재조회하지 않는다).

    known=False(fail-closed)면 이 캠페인의 관찰은 전역 풀에 절대 넣지 않는다. 두 실패 모드
    (부록 Q3 집행 규칙 원문 그대로):
      ① naver_campaign_settings에 그 캠페인 행 자체가 없다 → 라벨 미상
      ② naver_entity(entity_type='campaign')에서 campaign_type을 못 읽는다 → 유형 미상
    행이 있고 experiment_batch가 NULL이면 그건 "실험 배치가 아니다"라는 확정값이라 전역
    풀에 참여한다(행 부재=미상 vs NULL=미상 아님 — 이 둘을 섞으면 안 된다).
    """
    if campaign_id in cache:
        return cache[campaign_id]
    settings = (
        db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id == campaign_id)
        .first()
    )
    if settings is None:
        result = (None, None, False)
        cache[campaign_id] = result
        return result
    entity = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id == campaign_id)
        .first()
    )
    campaign_type = (entity.campaign_type or None) if entity is not None else None
    known = campaign_type is not None
    result = (campaign_type, settings.experiment_batch, known)
    cache[campaign_id] = result
    return result


def _build_signature(
    entry: OpsDiaryEntry, env: dict, campaign_type: str | None, experiment_batch: str | None, known: bool,
) -> str:
    """신형(전역) 시그니처 조립. known=False면 fail-closed 미상분리 시그니처(캠페인 단위 고립)."""
    if known:
        batch = experiment_batch or ""
        return (
            f"{_GLOBAL_PREFIX}|{campaign_type}|{entry.action}"
            f"|{env['day_class']}|{env['season']}|{env['iphone_window']}|{batch}"
        )
    return (
        f"{_UNKNOWN_PREFIX}|{entry.campaign_id}|{entry.action}"
        f"|{env['day_class']}|{env['season']}|{env['iphone_window']}"
    )


def _bump_by_campaign(by_campaign_json: str | None, campaign_id: str, direction: str) -> dict:
    """캠페인별 good/bad 분해 dict를 1건 증분(호출부가 json.dumps한다). direction은 'good'/'bad'만."""
    by_campaign = json.loads(by_campaign_json) if by_campaign_json else {}
    bucket = by_campaign.setdefault(campaign_id, {"good": 0, "bad": 0})
    bucket[direction] = bucket.get(direction, 0) + 1
    return by_campaign


def harvest_candidates(db: Session, *, now: datetime | None = None) -> dict:
    """결과 기입된 diary 행에서 반복 패턴 후보를 수확(매일 wisdom_loop이 호출).

    행별 try/except + 유닛 증분 커밋(D-NAO-46② 쓰기락 교훈). 시그니처 dedup은 entry id 단위라
    재스캔이 카운트를 부풀리지 않는다(같은 행 무시).
    """
    now = now or kst_now()
    lower_utc = (now - timedelta(hours=9)) - timedelta(days=_HARVEST_LOOKBACK_DAYS)
    rows = (
        db.query(OpsDiaryEntry)
        .filter(
            OpsDiaryEntry.event_type.in_(HARVEST_EVENT_TYPES),
            OpsDiaryEntry.outcome_json.isnot(None),
            OpsDiaryEntry.created_at.isnot(None),
            OpsDiaryEntry.created_at >= lower_utc,
        )
        .all()
    )
    bep_cache: dict[str, object] = {}  # 회차 내 캠페인별 BEP 캐시(행마다 재해석하지 않는다)
    boundary_cache: dict[str, tuple] = {}  # 회차 내 캠페인별 (campaign_type, experiment_batch, known) 캐시
    totals = {"scanned": 0, "new": 0, "updated": 0, "revived": 0,
              "skipped_no_outcome": 0, "skipped_no_target": 0, "skipped_neutral": 0,
              "skipped_terminal": 0, "errors": 0,
              # ★D-NAO-251(증거보전) — 0이어도 키를 낸다(교훈 #318: 0건과 침묵은 다르다).
              #   skipped_terminal은 이제 promoted «만» 센다 — 아래 셋이 rejected의 행방이다.
              "rejected_tally_resumed": 0,      # 기각분에 증거가 다시 쌓임(문턱 미도달 포함)
              "reopened": 0,                    # 2배∧+5 도달 → pending 복귀(재심 대기)
              "skipped_rejudge_exhausted": 0,   # 재심 상한 소진 → 다시 완전 terminal
              "skipped_no_action": 0,           # action 미상 일기 행 → 후보 생성 안 함(§4-② ⓑ)
              # ★S8(D-NAO-178 해제, 2026-08-25) — search_term 행의 d1_st.status 소비 카운터.
              #   구 "skipped_search_term_grain"(통째로 skip)은 의미가 사라져 제거했다 — 지금은
              #   good/bad로 갈리는 행이 존재하므로 "전건 skip"을 뜻하는 이름을 남기면 거짓말이 된다.
              "skipped_search_term_no_d1_st": 0,   # d1_st 자체가 없음(아직 스윕 전 or unresolved)
              "skipped_search_term_ambiguous": 0,  # d1_st.status == ambiguous
              "skipped_search_term_no_data": 0,    # d1_st.status == no_data
              "skipped_search_term_unknown_status": 0,  # status가 알려진 4값 밖(fail-closed)
              "search_term_good": 0,  # d1_st.status == stopped (good 판정)
              "search_term_bad": 0,   # d1_st.status == leaking (bad 판정)
              # D-NAO-248: 전역 풀에 못 들어간(=분리된) 관찰 수. 카운터일 뿐 실패가 아니다.
              "separated_experiment": 0, "separated_unknown": 0,
              # 이 수확이 새 학습이 아니라 기존 90일 일기의 «재집계»임을 호출부가 화면에
              # 옮길 수 있게 하는 표지(부록 Q2: "소급 재계산이 아니라 소급 재수확").
              "note": RETRO_HARVEST_LABEL}
    for entry in rows:
        try:
            outcome = json.loads(entry.outcome_json) if entry.outcome_json else {}

            if entry.target_type == "search_term":
                # ★S8(D-NAO-178 해제) — d1(캠페인 폴백)은 절대 안 읽는다. d1_st.status만 소비
                #   (금지선·이유는 _search_term_direction 문서 참조).
                direction, skip_reason = _search_term_direction(outcome)
                if direction is None:
                    if skip_reason == "absent":
                        totals["skipped_search_term_no_d1_st"] += 1
                    else:
                        totals[skip_reason] += 1  # 이미 완성된 totals 키
                    continue
                totals["scanned"] += 1
                totals["search_term_good" if direction == "good" else "search_term_bad"] += 1
            else:
                window = _outcome_window(outcome) if outcome else None
                if window is None:
                    totals["skipped_no_outcome"] += 1
                    continue
                totals["scanned"] += 1
                direction = _outcome_direction(db, entry, window, bep_cache=bep_cache)
                if direction is None:
                    totals["skipped_no_target"] += 1
                    continue
                if direction == "neutral":  # cost=0 — tally·후보 미기여(리뷰 P2-3)
                    totals["skipped_neutral"] += 1
                    continue

            # ★D-NAO-251 §4-② ⓑ — action이 없는 일기 행으로는 후보를 만들지 않는다.
            #   action은 패턴의 «의미 축» 그 자체라, 미상인 채 후보가 되면 판사가 형제를
            #   찾을 수 없고(`wisdom_judge._sibling_buckets`가 action으로 매칭한다) 그 후보는
            #   대조군 없이 영원히 판정만 기다린다. prod 실재 사례: 후보 45번
            #   `g|SHOPPING|None|weekday|summer|normal|`.
            #   ★그리고 «만들지 않았다»가 침묵하지 않게 카운터로 센다(교훈 #318).
            if not entry.action:
                totals["skipped_no_action"] += 1
                continue

            env = {
                "day_class": _day_class(entry),
                "season": entry.season,
                "iphone_window": _iphone_window(entry.iphone_launch_offset_days),
            }
            # D-NAO-248: 전역 시그니처 — campaign_type × action × 환경버킷[× experiment_batch].
            # known=False(fail-closed)면 "g?|캠페인 단위" 분리 시그니처로 고립시킨다.
            campaign_type, experiment_batch, known = _campaign_boundary(
                db, entry.campaign_id, boundary_cache
            )
            signature = _build_signature(entry, env, campaign_type, experiment_batch, known)
            if not known:
                totals["separated_unknown"] += 1
            elif experiment_batch:
                totals["separated_experiment"] += 1

            cand = (
                db.query(OpsWisdomCandidate)
                .filter(OpsWisdomCandidate.signature == signature)
                .first()
            )
            if cand is not None:
                if cand.status in _TERMINAL_STATUSES:  # promoted — 판사 판정 완료(완전 terminal)
                    totals["skipped_terminal"] += 1
                    continue
                # ★D-NAO-251 §4-① — rejected는 재심 상한을 소진했을 때만 terminal이다.
                #   소진 전이면 아래로 흘러 tally가 계속 쌓인다(증거보전의 본체).
                if cand.status == "rejected" and (cand.rejudge_count or 0) >= _MAX_REJUDGE:
                    totals["skipped_rejudge_exhausted"] += 1
                    continue
                ids = json.loads(cand.source_entry_ids_json or "[]")
                if entry.id in ids:  # 같은 행 재스캔 — 부풀림·부활 금지
                    continue
                if cand.status == "hidden":  # 망각분 재등장 → 부활(Ebbinghaus 재노출, 리뷰 P2-1)
                    cand.status = "pending"
                    totals["revived"] += 1
                elif cand.status == "rejected":
                    # 기각분에 증거가 «다시 흐르기 시작»한 것 자체를 센다 — 재개방 문턱을
                    # 아직 못 넘었어도 이 카운터가 0이 아니면 배선은 살아 있다는 뜻이다
                    # (계약 §5 ①-b: 재료가 없어 문턱을 못 넘어도 배선 합격을 관측할 표면).
                    totals["rejected_tally_resumed"] += 1
                ids.append(entry.id)
                cand.source_entry_ids_json = json.dumps(ids)
                if direction == "good":
                    cand.good_count = (cand.good_count or 0) + 1
                else:
                    cand.bad_count = (cand.bad_count or 0) + 1
                cand.occurrences = (cand.good_count or 0) + (cand.bad_count or 0)  # good+bad 합
                by_campaign = _bump_by_campaign(cand.by_campaign_json, entry.campaign_id, direction)
                cand.by_campaign_json = json.dumps(by_campaign, ensure_ascii=False)
                if known:
                    cand.observation = _observation_global(
                        campaign_type, entry.action, env, cand.good_count or 0, cand.bad_count or 0,
                        len(by_campaign), experiment_batch,
                    )
                else:
                    cand.observation = _observation_unknown(
                        entry.campaign_id, entry.action, env, cand.good_count or 0, cand.bad_count or 0
                    )
                cand.last_seen_at = now
                # ★D-NAO-251 §4-① — 증거가 판정 시점의 2배(∧+5)에 닿으면 pending 복귀.
                #   판정을 코드가 뒤집는 게 아니다 — **같은 판사에게 다시 묻는 것**까지다
                #   (판정기 증식 금지, 북극성 §6-b M5). 재심 횟수는 여기서 올리지 않는다:
                #   판사가 실제로 다시 판정했을 때 `wisdom_judge`가 올린다(문을 연 것과
                #   실제로 재심한 것은 다르다 — 판사 호출이 실패하면 pending으로 남는다).
                if cand.status == "rejected" and _reopen_ready(cand):
                    cand.status = "pending"
                    totals["reopened"] += 1
                db.commit()
                totals["updated"] += 1
            else:
                good_count = 1 if direction == "good" else 0
                bad_count = 1 if direction == "bad" else 0
                by_campaign = {entry.campaign_id: {"good": good_count, "bad": bad_count}}
                if known:
                    observation = _observation_global(
                        campaign_type, entry.action, env, good_count, bad_count,
                        len(by_campaign), experiment_batch,
                    )
                else:
                    observation = _observation_unknown(
                        entry.campaign_id, entry.action, env, good_count, bad_count
                    )
                cand = OpsWisdomCandidate(
                    # ★전역/실험분리 후보의 campaign_id는 빈 문자열(캠페인 ID를 시그니처·요약문에
                    # 안 박는다는 원칙과 일관) — 미상분리 후보만 캠페인 단위라 실제 id를 남긴다.
                    signature=signature, campaign_id=(entry.campaign_id if not known else ""),
                    action=entry.action,
                    env_bucket_json=json.dumps(env, ensure_ascii=False),
                    observation=observation,
                    occurrences=1, good_count=good_count, bad_count=bad_count,
                    first_seen_at=now, last_seen_at=now,
                    source_entry_ids_json=json.dumps([entry.id]), status="pending",
                    importance=5, strength=7.0,
                    # ★grain은 «이 후보가 실제로 무엇을 묶었나»를 적는다 — known=False 후보는
                    #   시그니처가 `g?|{campaign_id}|…`라 캠페인 1개짜리다. 여기에 "global"을
                    #   적으면 컬럼이 시그니처와 «모순»되고(라벨은 전역, 실체는 캠페인 단위),
                    #   소비층이 grain으로 필터할 때 미상분리분이 전역 통계에 섞인다.
                    #   («왜» 분리됐는지는 `g?|` 접두사와 separated_unknown 카운터가 말한다.
                    #   값은 컬럼이 String(12)라 짧게 — 'campaign_unknown'(16자)은 SQLite에선
                    #   조용히 통과해도 PostgreSQL 이관 시 깨진다.)
                    grain=("global" if known else "campaign"),
                    campaign_type=campaign_type, experiment_batch=experiment_batch,
                    by_campaign_json=json.dumps(by_campaign, ensure_ascii=False),
                )
                db.add(cand)
                db.commit()
                totals["new"] += 1
        except Exception as e:  # noqa: BLE001 — 한 행 실패가 스윕을 못 죽인다
            db.rollback()
            totals["errors"] += 1
            log.exception("wisdom_candidates: 행 수확 실패(id=%s): %s", getattr(entry, "id", "?"), e)
    return totals
