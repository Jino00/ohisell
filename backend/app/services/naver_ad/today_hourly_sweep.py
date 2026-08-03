# today_hourly_sweep.py — 당일 그룹 grain 시간별 축적 Harness (D-NAO-122)
# 역할: 오늘(진행 중) 활성 애드그룹의 hh24 곡선을 매시 재조회해 naver_adgroup_hourly_today에
#   적재한다. 수집 함수(fetch_entity_hh24)와 타깃 grain 규약(build_sweep_targets)은 기존 것을
#   그대로 쓰고, 신규는 당일 타깃 도출·매시 실행·전용 저장뿐이다.
#   전부 GET(관찰 전용) — 입찰/예산/상태에 손대지 않는다.
#
# ★왜 필요한가(2026-07-29 실측): naver_keyword_hourly는 전날치만 09:10에 일괄 적재라
#   "오늘 그룹별 성과"를 낼 수 없었다. naver_hourly_snapshot은 캠페인 grain인데 대상 캠페인
#   하나에 애드그룹이 60개 섞여 있어 개별 제품 대용치가 못 된다. 그래서 오늘 오전 입찰을
#   올리고도 효과를 당일에 볼 방법이 없었다 — 순위 서보·이탈 경보 등 관측 루프 전부가
#   이 구멍 위에 서 있다.
#
# ★전용 테이블을 쓰는 이유는 models.NaverAdgroupHourlyToday docstring에 전부 있다(요약:
#   naver_keyword_hourly의 완결 불변식을 깨면 auto_operator의 롤링 24h 흐름이 과소계상된다).
#
# ★타깃을 실행기(auto_operator)와 독립적으로 도출하는 이유: 실행기가 손댄 유닛만 저장하면
#   관측 집합이 실행 선택에 종속돼 선택 편향이 생긴다(오른 것만 관측되고 안 오른 것은 안 보임).
#
# ★진행 중인 시간 버킷은 저장하지 않는다: 부분 집계가 관측·학습에 섞이면 조용히 편향된다.
#   네이버 반영 지연(2026-07-29 12:24 실측 — 11시 버킷 아직 없음)으로 빠진 시간은 다음
#   실행의 교체 upsert가 자연 보완한다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import NaverAdgroupHourlyToday, NaverAdgroupProduct, NaverLearningState
from app.services.naver_ad import adgroup_product_freshness
from app.services.naver_ad.keyword_hourly_sweep import build_sweep_targets
from app.services.naver_ad.launch_rank_floor import (
    LAUNCH_WINDOW_DAYS,
    METRIC_TARGET_RANK,
    days_since_launch,
)
from app.services.naver_sa_ad_fetcher import fetch_entity_hh24
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# 킬스위치 — 매시 외부 API를 부르는 신규 잡이라 한 줄 배포로 끌 수 있게 둔다.
# 네이버가 /stats의 초당 rate limit 수치를 공개하지 않아(공식 문서 확인 안 됨) 라이브에서
# 429가 관측되면 즉시 False로 내린다.
TODAY_HOURLY_SWEEP_ENABLED = True

# 1실행당 hh24 콜 상한. 매시 실행이라 일 1회 스윕(3,500)과는 스케일이 다르다.
_CALL_CAP = 800

# 보존 기간 — 이 테이블은 "당일 관측"용이라 오래 들고 있을 이유가 없다(완결 이력은
# naver_keyword_hourly가 365일 보존). 창을 짧게 둬야 두 테이블을 헷갈려 쓰는 일이 준다.
_RETAIN_DAYS = 14


def _launch_window_targets(db: Session, today: date) -> dict[str, dict]:
    """출시창이 **아직 살아있는** 소재의 그룹 — 어제 노출 0인 콜드스타트 보강용.

    어제 naver_ad_daily만 보면 오늘 처음 도는 신제품 그룹이 영영 관측 밖에 남는다.
    출시창은 그 자리가 매출을 결정하는 구간이라(D-NAO-120) 관측 공백이 가장 비싼 곳이다.

    ★창 만료 필터(codex 리뷰 P2, 2026-07-29): launch_target_rank 상태 행은 21일이 지나도
      지워지지 않는다 — launch_rank_floor.floor_for가 days_since_launch로 무력화할 뿐이다.
      만료 필터가 없으면 옛 출시 그룹이 노출 0인 채로 영구히 매시 조회돼 콜 예산을 먹고
      현재 출시 그룹을 상한 밖으로 밀어낸다. 같은 판정 함수를 재사용해 기준을 하나로 유지.
      days_since_launch is None(아직 노출 이력 없음)은 **포함한다** — 이제 막 시작하는
      그룹이야말로 이 보강이 노리는 대상이다.
    """
    ad_ids = [
        r[0]
        for r in db.query(NaverLearningState.scope_key)
        .filter(
            NaverLearningState.scope == "entity",
            NaverLearningState.metric == METRIC_TARGET_RANK,
            NaverLearningState.current_value.isnot(None),
        )
        .all()
        if r[0]
    ]
    if not ad_ids:
        return {}
    rows = (
        db.query(NaverAdgroupProduct.adgroup_id, NaverAdgroupProduct.campaign_id)
        .filter(NaverAdgroupProduct.ad_id.in_(tuple(ad_ids)), adgroup_product_freshness.fresh_condition())
        .all()
    )
    out: dict[str, dict] = {}
    for adgroup_id, campaign_id in rows:
        if not adgroup_id or adgroup_id in out:
            continue
        days = days_since_launch(db, adgroup_id, today)
        if days is not None and days > LAUNCH_WINDOW_DAYS:
            continue  # 출시창 종료 — 더 이상 특별 대우하지 않는다
        out[adgroup_id] = {
            "entity_type": "adgroup",
            "entity_id": adgroup_id,
            "campaign_id": campaign_id or "",
            "campaign_type": "",  # 이 경로로는 알 수 없음(저장 컬럼일 뿐 분기에 안 씀)
        }
    return out


def build_today_targets(db: Session, today: date) -> list[dict]:
    """오늘 스윕할 애드그룹 타깃(DB 읽기만, API 호출 없음).

    ① 어제 naver_ad_daily의 **adgroup grain** imp>0 유닛(build_sweep_targets 재사용 —
       grain 판정·백필 sentinel 배제 규약을 한 곳에 유지)
    ② 출시창이 살아있는 소재의 그룹(①에 없는 콜드스타트 보강)

    ★키워드 grain(nkw-…)은 제외한다(Jino 확정 2026-07-29). 키워드 유닛은 수천 개 규모라
      매시 실행하면 콜이 폭증하고, 쇼핑 캠페인은 애초에 키워드 입찰이 없어 그룹이 실효 레버다.
    반환 순서는 안정적이다 — 회전은 rotate_for_hour가 맡는다(관심사 분리).
    """
    targets: dict[str, dict] = {}
    for t in build_sweep_targets(db, today - timedelta(days=1)):
        if t["entity_type"] != "adgroup":
            continue
        targets[t["entity_id"]] = {
            "entity_type": "adgroup",
            "entity_id": t["entity_id"],
            "campaign_id": t["campaign_id"],
            "campaign_type": t["campaign_type"],
        }
    for adgroup_id, t in _launch_window_targets(db, today).items():
        targets.setdefault(adgroup_id, t)  # ①이 있으면 ①이 이긴다(campaign_type 보유)
    return list(targets.values())


def rotate_for_hour(targets: list[dict], hour: int, cap: int) -> list[dict]:
    """콜 상한을 넘는 타깃 집합을 시각에 따라 회전시킨다(순수).

    ★왜(codex 리뷰 P2, 2026-07-29): 상한에서 그냥 자르면 정렬 순서가 매 실행 동일하므로
      상한 뒤쪽 그룹은 **영원히** 조회되지 않는다("다음 실행이 이어받는다"는 성립하지 않음).
      시각 기반 회전은 상태 저장 없이 하루 24회 실행으로 최대 24×cap개를 순환 커버한다.
      상한 이하이면 원본 순서 그대로(회전 없음 — 불필요한 뒤섞기 방지).
    """
    if cap <= 0 or len(targets) <= cap:
        return targets
    offset = (hour * cap) % len(targets)
    return (targets + targets)[offset : offset + cap]


def drop_incomplete_hour(hh_rows: list[dict], now: datetime) -> list[dict]:
    """진행 중인(그리고 방어적으로 미래의) 시간 버킷 제거 — 완결된 시간만 남긴다(순수).

    now.hour 버킷은 아직 쌓이는 중이라 imp/clk/avg_rank가 부분값이다. 저장하면 관측이
    "그 시간대는 원래 노출이 적다"고 조용히 오해한다.
    """
    return [h for h in hh_rows if int(h["hour"]) < now.hour]


def _replace_rows(db: Session, ad_date: date, target: dict, hh_rows: list[dict]) -> None:
    """(ad_date, adgroup_id) 기존 행 삭제 후 재삽입 — 교체 upsert(멱등)."""
    db.execute(delete(NaverAdgroupHourlyToday).where(
        NaverAdgroupHourlyToday.ad_date == ad_date,
        NaverAdgroupHourlyToday.adgroup_id == target["entity_id"],
    ))
    for h in hh_rows:
        db.add(NaverAdgroupHourlyToday(
            ad_date=ad_date, hour=h["hour"], adgroup_id=target["entity_id"],
            campaign_id=target.get("campaign_id") or "",
            campaign_type=target.get("campaign_type") or "",
            imp=h["imp"], clk=h["clk"], cost=h["cost"],
            conv_cnt=h.get("conv_cnt", 0), avg_rank=h.get("avg_rank"),
        ))


def sweep_adgroup_hourly_today(
    db: Session,
    *,
    today: date | None = None,
    now: datetime | None = None,
    fetch=None,
) -> dict:
    """매시 당일 스윕 — 타깃 도출 → 시각 회전 → 유닛별 1콜 → 미완결 시간 제거 → 교체 upsert.

    today/now 기본 = KST 현재. fetch 미주입 시 fetch_entity_hh24(테스트 주입, 원칙18-8).
    유닛 단위 fetch 실패는 log+skip(크론 체인을 죽이지 않음). 빈 응답·완결 버킷 0건은
    기존 행을 지우지 않고 skip한다 — 이 둘은 "오늘 아직 실적 없음"과 구분되지 않는데,
    지워버리면 앞서 모은 완결 시간까지 날아간다.
    """
    if not TODAY_HOURLY_SWEEP_ENABLED:
        log.info("today_hourly_sweep 비활성(킬스위치) — 건너뜀")
        return {"enabled": False, "targets": 0, "calls": 0, "rows": 0, "failed": 0, "skipped": 0}

    if fetch is None:
        fetch = fetch_entity_hh24
    if now is None:
        now = kst_now()
    if today is None:
        today = kst_today()

    calls = 0
    rows_written = 0
    failed = 0
    skipped = 0

    all_targets = build_today_targets(db, today)
    targets = rotate_for_hour(all_targets, now.hour, _CALL_CAP)
    for t in targets:
        calls += 1
        try:
            hh = fetch(t["entity_id"], today)
        except Exception as e:
            log.warning("today_hourly_sweep 유닛 실패 skip: entity=%s date=%s: %s",
                        t["entity_id"], today, e)
            failed += 1
            continue
        if not hh:
            # 당일엔 imp>0 보증이 없다(그 시각까지 노출 0인 그룹) — 정상 상태라 failed 아님.
            skipped += 1
            continue
        complete = drop_incomplete_hour(hh, now)
        if not complete:
            skipped += 1
            continue
        _replace_rows(db, today, t, complete)
        # 라이브 락 보유 창 최소화 — keyword_hourly_sweep과 동일 이유(유닛마다 커밋).
        db.commit()
        rows_written += len(complete)

    db.execute(delete(NaverAdgroupHourlyToday).where(
        NaverAdgroupHourlyToday.ad_date < today - timedelta(days=_RETAIN_DAYS)
    ))
    db.commit()

    result = {
        "enabled": True, "date": today.isoformat(), "hour": now.hour,
        "targets": len(all_targets), "swept": len(targets), "calls": calls,
        "rows": rows_written, "failed": failed, "skipped": skipped,
    }
    log.info("naver today_hourly sweep: %s%s", result,
             " (회전 적용 — 상한 초과)" if len(all_targets) > len(targets) else "")
    return result
