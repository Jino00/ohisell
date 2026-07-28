# bid_rank_curve.py — 입찰→순위 반응 곡선 학습 SA (스프린트 IU-R R3, D-NAO-67 원리③).
#   역할(SA·순수 집계): NaverChangeLog(상향 스텝 실집행 = CURVE_SAMPLE_TYPES) × NaverKeywordHourly(변경 전/후 완결
#   버킷 순위)를 조인해 유닛별 (입찰변경→순위개선) 관측쌍을 만들고, 기울기(원/rank개선 1.0,
#   양수)를 적합해 NaverLearningState(scope="entity", metric="bid_rank_slope")에 저장한다.
#   서보(rank_servo)가 이 기울기를 response_prior로 소비해 콜드스타트 대신 "한 단 위 필요
#   증분"을 근접 산정한다(auto_operator.run_hourly_lane이 harness로 주입, 원칙18-6).
#
#   ★신규 테이블 없음(마이그레이션 0, PLAN §1-5 정정) — change_log × keyword_hourly 조인 집계.
#     관측쌍 원료는 매일 조인으로 재산출하므로 충분통계 영속이 불요하다(단일 Numeric로 충분).
#   ★조인 버킷 결정론(codex 왕복 P2): 변경 changed_at KST 시각 h → rank_before=버킷 h−1(변경 전
#     완결), rank_after=버킷 h+1(:20 변경이 섞인 부분버킷 h 제외 · 쿨다운 2h와 정합 — 다음 스텝은
#     최소 h+2라 h+1은 이 스텝의 순수 결과). 두 버킷 중 하나라도 avg_rank NULL/부재면 그 쌍 제외.
#     h=0이면 h−1은 전일 23시, h=23이면 h+1은 익일 0시(datetime 산술로 롤오버 처리).
#   ★인과 오염 제외(codex 엣지): 같은 유닛의 h−1~h+1 창(= [h_top−1h, h_top+2h))에 이 변경 외
#     다른 변경(update_bid/external_bid_change/external_status_change/set_user_lock)이 겹치면 그
#     관측쌍 제외(다른 원인이 순위를 흔들었을 수 있음).
#   ★실패 writer 배제(적합 함수 내부 규칙 — §codex 왕복 P2-5, 저장 플래그 없음): 실패 실쓰기는
#     after_value가 없고([실행 실패] 마커·outcome='failed') 순위 반영이 불확실하므로 관측쌍에서
#     제외한다. 저장 플래그를 만들지 않고 이 SA의 관측 빌드 규칙으로만 처리한다.
#   ★기울기 단위: 원/rank개선 1.0(양수 규약). 개선폭=rank_before−rank_after, 기울기=Δbid/개선폭,
#     개선폭>0 ∧ Δbid>0 쌍만 적합. 무반응(개선폭≤0) 쌍은 관측엔 남기되(오염 아님·제외 아님, 학습
#     유지) 양수 기울기 적합엔 미기여한다 — N스텝 무반응 hold 판정은 R1 harness 무영속 재구성
#     몫이라 R3는 slope만 낸다(PLAN §2 R3·§난제6).
#
#   ★적립 주체·시계: learning_loops.run_all이 08:10 크론에서 호출. NaverKeywordHourly가 D-1
#     스윕이라 원료 시계는 일 단위 — "첫 스텝 정교화는 익일 반영"이다(당일 변경의 h+1 버킷은
#     아직 미스윕이라 자연 제외되고 다음 날 조인에서 관측쌍화된다, PLAN §1-5).
#   ★stale 방지 이중 방어(codex R3 P1): 이번 스캔에 관측이 잡힌 유닛은 유효쌍<_MIN_PAIRS여도
#     skip해 보존하지 않고 **무효 마킹**(current_value=None·confidence=0)한다(쓰기 측). load_
#     response_priors는 confidence>0·sample_n≥_MIN_PAIRS·updated_at 신선(≤_PRIOR_MAX_AGE_DAYS)만
#     로드한다(읽기 측 — 슬라이드-아웃 stale 만료). 관측 0 유닛(스캔 미포함)만 자연 미갱신.
#   부수효과: DB read + NaverLearningState upsert(관측 유닛 전건 — 유효/무효 마킹).
from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import NaverChangeLog, NaverKeywordHourly, NaverLearningState, NaverProposal
from app.services.naver_ad.bid_step_types import CURVE_SAMPLE_TYPES
from app.services.naver_ad.naver_execution_harness import WRITE_FAILURE_MARKER
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

SCOPE = "entity"
METRIC = "bid_rank_slope"


def _utc_now() -> datetime:
    """naive UTC now — NaverLearningState.updated_at의 server_default/onupdate func.now()(sqlite
    UTC naive) 저장 규격과 동일 타임존으로 맞춘다(신선도 비교 정합·cross-metric max 오염 방지)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# 관측 윈도우 — 최근 N일의 rank-step 실집행만 곡선 원료로 삼는다(최근 반응성 반영). change_log는
# 무기한 축적이나 NaverKeywordHourly는 365일 롤링이므로 그 안이면 조인 가능. 30일=충분한 표본과
# 최근성의 절충(잠정 — R3 관측 후 튜닝 대상, §실측6).
_LOOKBACK_DAYS = 30
# 유닛별 유효(개선폭>0∧Δbid>0) 관측쌍 최소치 — 미만이면 slope=None(콜드스타트 폴백, 무근거 큰
# 점프 금지, PLAN §2 R3). 추천 3 잠정(§난제6·§실측 N스텝과 동일 감각).
_MIN_PAIRS = 3
# confidence 정규화 분모(estimate_calibrator SAMPLE_BUDGET 관례 동형) — 유효쌍 10개면 confidence=1.
_CONFIDENCE_SAMPLE_BUDGET = 10
# 서보 프라이어 신선도 만료(codex R3 P1-b) — 읽기 측 이중 방어. bid_rank_curve가 매일 도므로
# 건강한(=최근 30일 rank-step 있는) 유닛 행은 매일 갱신되고 updated_at이 새로 찍힌다. 관측이 30일
# 밖으로 밀려나 더는 스캔되지 않는 유닛의 stale slope는 이 만료로 읽기에서 자연 배제한다(쓰기 측
# 무효 마킹(P1-a)이 못 잡는 "슬라이드-아웃" 경로 방어). 쿨다운·학습 주기 대비 짧게 3일 잠정.
_PRIOR_MAX_AGE_DAYS = 3

# 인과 오염 판정 대상 액션(codex R3 P2 — "실제 변경"으로 좁힘):
#  · managed(우리 실집행): update_bid/set_user_lock은 **성공 실쓰기**(dry_run=False ∧ after_value
#    존재 ∧ outcome≠failed)만 오염으로 센다 — dry-run 이웃/실패 행은 순위를 실제로 흔들지 않으므로
#    정상 관측쌍을 오탐 배제하면 안 된다.
#  · external(외부 감지 관측): external_bid_change/external_status_change는 entity_sync가 dry_run=
#    False·after_value 채워 기록하는 실제 외부 변경 관측이라 무조건 오염(내용 필터 없이 유지).
_MANAGED_CONTAM_ACTIONS = frozenset({"update_bid", "set_user_lock"})
_EXTERNAL_CONTAM_ACTIONS = frozenset({"external_bid_change", "external_status_change"})

_Q4 = Decimal("0.0001")


def _bid_of(value_json: str | None) -> int | None:
    """change_log before/after_value(JSON) → bidAmt(int). 파싱 실패/키 부재 → None."""
    if not value_json:
        return None
    try:
        v = json.loads(value_json)
    except (ValueError, TypeError):
        return None
    bid = v.get("bidAmt") if isinstance(v, dict) else None
    try:
        return int(bid) if bid is not None else None
    except (ValueError, TypeError):
        return None


def _bucket_rank(ranks: dict, entity_id: str, dt: datetime) -> Decimal | None:
    """(entity_id, dt.date(), dt.hour) 완결 버킷의 avg_rank — 부재/NULL이면 None."""
    return ranks.get((entity_id, dt.date(), dt.hour))


def _load_hourly_ranks(db: Session, entity_ids: set[str], date_from: date, date_to: date) -> dict:
    """관여 유닛의 시간별 avg_rank를 벌크 적재 — {(entity_id, ad_date, hour): Decimal}.
    avg_rank NULL 버킷은 담지 않는다(부재와 동일 취급 = 조인 실패로 그 쌍 제외)."""
    if not entity_ids:
        return {}
    rows = (
        db.query(
            NaverKeywordHourly.entity_id, NaverKeywordHourly.ad_date,
            NaverKeywordHourly.hour, NaverKeywordHourly.avg_rank,
        )
        .filter(
            NaverKeywordHourly.entity_id.in_(tuple(entity_ids)),
            NaverKeywordHourly.ad_date >= date_from,
            NaverKeywordHourly.ad_date <= date_to,
            NaverKeywordHourly.avg_rank.isnot(None),
        )
        .all()
    )
    out: dict = {}
    for entity_id, ad_date, hour, avg_rank in rows:
        d = ad_date.date() if isinstance(ad_date, datetime) else ad_date
        out[(entity_id, d, int(hour))] = avg_rank
    return out


def _load_change_times(db: Session, entity_ids: set[str], start: datetime, end: datetime) -> dict:
    """오염 판정용 — 관여 유닛의 (변경시각, change_log id) 리스트를 벌크 적재.
    {entity_id: [(changed_at, id), ...]}. 오염 창보다 넓게(±2h 여유) 담아 경계 변경도 포착."""
    if not entity_ids:
        return {}
    rows = (
        db.query(NaverChangeLog.entity_id, NaverChangeLog.changed_at, NaverChangeLog.id)
        .filter(
            NaverChangeLog.entity_id.in_(tuple(entity_ids)),
            NaverChangeLog.changed_at >= start,
            NaverChangeLog.changed_at < end,
            or_(
                # 외부 감지 관측 — 무조건 오염(entity_sync가 실제 외부 변경만 기록).
                NaverChangeLog.action.in_(tuple(_EXTERNAL_CONTAM_ACTIONS)),
                # 우리 실집행 — 성공 실쓰기만 오염(dry-run·실패 이웃은 순위 미교란 → 오탐 배제).
                and_(
                    NaverChangeLog.action.in_(tuple(_MANAGED_CONTAM_ACTIONS)),
                    NaverChangeLog.dry_run.is_(False),
                    NaverChangeLog.after_value.isnot(None),
                    or_(NaverChangeLog.outcome.is_(None), NaverChangeLog.outcome != "failed"),
                ),
            ),
        )
        .all()
    )
    out: dict = {}
    for entity_id, changed_at, cid in rows:
        out.setdefault(entity_id, []).append((changed_at, cid))
    return out


def _is_contaminated(change_times: dict, entity_id: str, this_id: int, lo: datetime, hi: datetime) -> bool:
    """오염 창 [lo, hi)에 이 변경(this_id) 외 다른 변경이 겹치면 True(관측쌍 제외)."""
    for changed_at, cid in change_times.get(entity_id, ()):  # noqa: SIM110 — 조기 반환 가독성
        if cid == this_id:
            continue
        if lo <= changed_at < hi:
            return True
    return False


def build_observations(db: Session, *, today: date) -> dict:
    """rank-step 실집행 change_log × 시간별 순위 조인 → 유닛별 관측쌍 리스트.

    반환: {(entity_type, entity_id): [{"delta_bid": int, "improvement": Decimal}, ...]}.
    관측쌍은 성공 실쓰기(after_value 존재)·비오염·양 버킷 순위 존재 건만. 개선폭≤0(무반응)·
    Δbid≤0(이상쌍) 관측도 리스트엔 포함하되(학습 유지·오염 아님) 적합 단계에서 걸러진다.
    """
    window_end = datetime(today.year, today.month, today.day)  # 오늘 00:00(KST naive) — D-1까지만
    window_start = window_end - timedelta(days=_LOOKBACK_DAYS)

    # 상향 스텝 실집행 원료 — proposal join으로 proposal_type ∈ CURVE_SAMPLE_TYPES 필터(change_log엔
    # proposal_type 컬럼이 없어 proposal_id로 조인). 성공 실쓰기만(dry_run=False ∧ after_value 존재).
    # ★2026-07-28 정정: 종전엔 RANK_STEP_TYPES(={bid_up_servo, bid_up_rank})로 걸렀는데 그 둘은
    #   prod 실집행 0건이고, 실제로 실행된 bid_up_explore(219건)·bid_up·growth_bid_up이 전부 원료
    #   밖이라 slope가 0행이었다. 필터 의미를 "가드레일 rank-step"이 아니라 "곡선 표본"으로 분리
    #   (bid_step_types.CURVE_SAMPLE_TYPES 주석 참조). 조인 버킷·오염·실패 배제 규칙은 불변 —
    #   넓어진 것은 원료 타입 하나뿐이고, 관측쌍 품질 게이트(±버킷 존재·비오염·유효쌍 ≥ _MIN_PAIRS)는
    #   그대로라 표본이 늘어도 무근거 slope가 생기지는 않는다.
    rows = (
        db.query(NaverChangeLog, NaverProposal.proposal_type)
        .join(NaverProposal, NaverChangeLog.proposal_id == NaverProposal.id)
        .filter(
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
            NaverChangeLog.changed_at >= window_start,
            NaverChangeLog.changed_at < window_end,
            NaverProposal.proposal_type.in_(tuple(CURVE_SAMPLE_TYPES)),
        )
        .all()
    )
    if not rows:
        return {}

    entity_ids = {cl.entity_id for cl, _ in rows}
    # 버킷 조회 범위 — 관측 창 하루 앞뒤(h=0의 전일 23시·h=23의 익일 0시 롤오버 여유).
    ranks = _load_hourly_ranks(
        db, entity_ids,
        (window_start - timedelta(days=1)).date(), (window_end + timedelta(days=1)).date(),
    )
    # 오염 판정 범위 — 관측 창 ±2h(오염 창이 변경 시각 −1h~+2h이므로).
    change_times = _load_change_times(
        db, entity_ids, window_start - timedelta(hours=2), window_end + timedelta(hours=2),
    )

    observations: dict = {}
    for cl, _ptype in rows:
        # ★스캔된 유닛은 유효쌍 0개여도 키를 만든다(codex R3 2R P1) — 전 쌍이 버킷 부재/오염/
        # 파싱 실패로 탈락한 유닛도 run_daily가 무효 마킹 upsert(sample_n=0·confidence=0)하도록.
        # 빠뜨리면 전날까지 유효했던 slope가 신선도 창(3일) 안에서 계속 서보에 주입된다.
        observations.setdefault((cl.entity_type, cl.entity_id), [])
        # 실패 writer 배제(§codex P2-5 — 적합 함수 내부 규칙, 저장 플래그 없음). after_value NOT NULL
        # 필터가 이미 대부분 걸러내나, 성공 행에 실패 마커가 섞일 여지를 방어적으로 재확인한다.
        if cl.outcome == "failed" or WRITE_FAILURE_MARKER in (cl.rationale or ""):
            continue

        before_bid = _bid_of(cl.before_value)
        after_bid = _bid_of(cl.after_value)
        if before_bid is None or after_bid is None:
            continue

        h_top = datetime(cl.changed_at.year, cl.changed_at.month, cl.changed_at.day, cl.changed_at.hour)
        before_dt = h_top - timedelta(hours=1)  # 버킷 h−1
        after_dt = h_top + timedelta(hours=1)   # 버킷 h+1(부분버킷 h 제외)
        rank_before = _bucket_rank(ranks, cl.entity_id, before_dt)
        rank_after = _bucket_rank(ranks, cl.entity_id, after_dt)
        if rank_before is None or rank_after is None:
            continue

        # 인과 오염 — h−1 시작 ~ h+1 끝 = [h_top−1h, h_top+2h).
        if _is_contaminated(change_times, cl.entity_id, cl.id, before_dt, h_top + timedelta(hours=2)):
            continue

        key = (cl.entity_type, cl.entity_id)
        observations.setdefault(key, []).append({
            "delta_bid": after_bid - before_bid,
            "improvement": Decimal(rank_before) - Decimal(rank_after),  # 양수=순위 개선(작아짐)
        })
    return observations


def _fit_slope(pairs: list[dict]) -> tuple[Decimal | None, int, Decimal]:
    """관측쌍 → (기울기|None, 유효쌍수, confidence). 유효쌍(개선폭>0 ∧ Δbid>0)만 적합에 쓴다.

    무반응(개선폭≤0)·이상쌍(Δbid≤0)은 적합에서 배제(학습 유지 차원에서 관측엔 남았지만 양수
    기울기엔 기여 불가). 유효쌍 < _MIN_PAIRS(또는 방어적으로 slope≤0)면 **기울기 None·confidence
    0**을 낸다 — run_daily가 이를 무효 마킹 upsert에 써서 "min 3 pairs else None" 계약을 읽기까지
    강제한다(codex R3 P1). 추정량 = 유효쌍별 (Δbid/개선폭)의 **중앙값**(노이즈 1건 방어 — avg_rank
    오실레이션에 강건). 반환 튜플의 2번째(유효쌍수)는 무효일 때도 관측 근거로 sample_n에 저장된다.
    """
    ratios = [
        Decimal(p["delta_bid"]) / Decimal(p["improvement"])
        for p in pairs
        if p["improvement"] > 0 and p["delta_bid"] > 0
    ]
    n = len(ratios)
    if n < _MIN_PAIRS:
        return None, n, Decimal("0")  # 무효(콜드스타트 강제) — current_value는 run_daily가 None으로.
    slope = statistics.median(ratios)  # Decimal 리스트 → 중앙값(짝수개는 두 중앙값 평균)
    if not isinstance(slope, Decimal):
        slope = Decimal(str(slope))
    if slope <= 0:  # 방어(유효쌍 정의상 도달 불가하나 계약 명시)
        return None, n, Decimal("0")
    confidence = min(Decimal(1), Decimal(n) / Decimal(_CONFIDENCE_SAMPLE_BUDGET))
    return slope.quantize(_Q4), n, confidence.quantize(_Q4)


def _upsert_learning_state(
    db: Session, *, scope_key: str, value: Decimal | None, sample_n: int, confidence: Decimal, now_utc: datetime,
) -> NaverLearningState:
    """NaverLearningState(scope="entity", metric="bid_rank_slope") upsert — estimate_calibrator
    관례 동형(scope/scope_key/metric 3키 UNIQUE). value=None+confidence=0이면 무효 마킹(P1-a):
    current_value NULL이라 load_response_priors가 읽지 않는다.

    ★updated_at을 명시적으로 now_utc(UTC naive — server_default func.now() 규격과 동일)로 찍는다.
    onupdate=func.now()에 의존하면 값 변화가 없는 날(안정 슬로프) ORM이 UPDATE를 생략해 updated_at이
    갱신 안 될 수 있고, 그러면 매일 스캔되는 건강 유닛이 신선도 만료(P1-b)에 걸려 오배제된다 —
    "이 유닛을 오늘 스캔·재산정했다"는 시각으로 못박아 읽기 만료 기준을 신뢰 가능하게 만든다."""
    row = db.query(NaverLearningState).filter(
        NaverLearningState.scope == SCOPE, NaverLearningState.scope_key == scope_key,
        NaverLearningState.metric == METRIC,
    ).first()
    if row is None:
        row = NaverLearningState(scope=SCOPE, scope_key=scope_key, metric=METRIC)
        db.add(row)
    row.current_value = value
    row.sample_n = sample_n
    row.confidence = confidence
    row.updated_at = now_utc
    return row


def load_response_priors(db: Session) -> dict:
    """서보 소비용(harness read, 원칙18-6) — {scope_key: slope(Decimal)}. auto_operator.
    run_hourly_lane이 캠페인 순회 밖에서 1회 적재해(N+1 방지) 서보 유닛별 response_prior로
    rank_servo.decide_servo_step에 주입한다.

    ★읽기 측 이중 방어(codex R3 P1-b) — 아래 4조건을 모두 만족한 행만 로드한다:
      · current_value IS NOT NULL(무효 마킹 배제)
      · confidence > 0(무효 마킹 배제 — P1-a)
      · sample_n >= _MIN_PAIRS("min 3 pairs else None" 계약을 읽기에서 재검증)
      · updated_at >= now−_PRIOR_MAX_AGE_DAYS(슬라이드-아웃 stale 만료 — 30일 밖으로 밀려 더는
        스캔 안 되는 유닛의 옛 slope 배제). updated_at NULL은 SQL 비교 false로 자연 배제(fail-closed).
    updated_at은 UTC 저장이라 _utc_now()(naive UTC) 기준으로 비교한다."""
    stale_before = _utc_now() - timedelta(days=_PRIOR_MAX_AGE_DAYS)
    rows = (
        db.query(NaverLearningState.scope_key, NaverLearningState.current_value)
        .filter(
            NaverLearningState.scope == SCOPE, NaverLearningState.metric == METRIC,
            NaverLearningState.current_value.isnot(None),
            NaverLearningState.confidence > 0,
            NaverLearningState.sample_n >= _MIN_PAIRS,
            NaverLearningState.updated_at >= stale_before,
        )
        .all()
    )
    return {scope_key: value for scope_key, value in rows}


def run_daily(db: Session, *, today: date | None = None) -> dict:
    """매일 실행(learning_loops 08:10 스테이지) — rank-step 실집행 조인 → 유닛별 기울기 upsert.

    ★이번 스캔에서 관측이 잡힌 유닛은 전부 upsert한다(codex R3 P1-a). 유효쌍 ≥ _MIN_PAIRS면 유효
    slope로, 미만이면 **무효 마킹**(current_value=None·confidence=0)으로 — 과거 유효 slope가 표본
    미달 이후에도 서보에 계속 주입되는 stale 재사용을 쓰기에서 차단한다("skip해서 보존"하지 않는다).
    관측 0인 유닛(스캔에 안 걸림)은 자연히 미갱신되고, 그중 슬라이드-아웃 stale은 load_response_priors
    신선도 만료(P1-b)가 읽기에서 배제한다. 서보 첫 스텝 정교화는 익일 반영(NaverKeywordHourly D-1
    스윕, PLAN §1-5)."""
    today = today or kst_today()
    now_utc = _utc_now()
    observations = build_observations(db, today=today)
    upserted = 0
    invalidated = 0
    for (entity_type, entity_id), pairs in observations.items():
        slope, sample_n, confidence = _fit_slope(pairs)
        # scope_key = "adgroup:<id>" / "keyword:<id>"(entity_type ∈ {adgroup, keyword} — change_log
        # entity_type = proposal.target_type). 서보(load_response_priors)는 "adgroup:" prefix만 소비.
        _upsert_learning_state(
            db, scope_key=f"{entity_type}:{entity_id}",
            value=slope, sample_n=sample_n, confidence=confidence, now_utc=now_utc,
        )
        if slope is None:
            invalidated += 1
        else:
            upserted += 1
    db.commit()
    log.info(
        "bid_rank_curve run_daily: units=%d upserted=%d invalidated=%d",
        len(observations), upserted, invalidated,
    )
    return {"units": len(observations), "upserted": upserted, "invalidated": invalidated}
