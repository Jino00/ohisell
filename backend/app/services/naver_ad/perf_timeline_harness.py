# perf_timeline_harness.py — 광고 성과(사장님 뷰) ⑤BEP 구성 + ⑥개선 타임라인 하니스
# (성과뷰 Phase 3, docs/PLAN_naver-ad-performance-view.md §2 H3).
"""역할(Harness): "이 상한이 왜 그 숫자냐(⑤)" 와 "우리가 뭘 바꿨고 그 전후가 어땠나(⑥)" 두
질문의 재료를 SA들에서 모아 조립한다. **읽기 전용**(계획서 §0-1).

조합하는 SA(원칙18-6: SA끼리 직접 부르지 않는다 — 원료는 이 하니스가 모아서 넘긴다):
  · bep_breakdown           저장된 BEP 스냅샷 → 구성 표(조립만, 새 산식 없음)
  · bep_calculator          상품별 배송 구성(N배송 혼합비) — 스냅샷에 없는 유일한 칸
  · bid_ceiling_calculator  소재별 이익 CPC 상한(RPC ÷ BEP)
  · improvement_events      트랙 결정 카탈로그 ∪ 라이브 구조 변경 → 이벤트 목록
  · metrics_aggregator      일별 집계(창 전체를 **한 번에** — 아래 참조)
  · event_impact_scorer     이벤트별 전후 7일 + 정직 라벨(confounded·관찰 중) · DB 안 읽음
  · retro_rollup            방향 정밀도 요약(/retro-scorecard와 **같은 한 벌**)

★부하: 상한은 소재 1건당 RPC 사다리 조회가 붙는다. 라이브 실측(2026-07-28) 소재 링크는
  **계정 전체 54건**이라 캡을 두지 않았다. 이 수가 수백 단위로 늘면 캠페인 필터를 필수로
  돌리거나 광고그룹 단위 RPC 메모이즈를 넣어야 한다(지금 넣으면 쓰지 않는 복잡도).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import NaverRetroSignal
from app.services.naver_ad import (
    bep_breakdown,
    bep_calculator,
    bid_ceiling_calculator,
    event_impact_scorer,
    improvement_events,
    metrics_aggregator,
    retro_rollup,
)
from app.services.naver_ad.alert_humanizer import campaign_name as campaign_name_of
from app.utils.kst import kst_today

DEFAULT_TIMELINE_DAYS = 90
MAX_TIMELINE_DAYS = 365
# 방향 정밀도 요약의 조회 창. 타임라인 창(기본 90일)과 별개다 — 정밀도는 표본이 얇으면
# 숫자가 튀어서 더 넓게 본다(/retro-scorecard 기본 28일과 같은 값).
RETRO_WINDOW_DAYS = 28


def build_bep_breakdown(
    db: Session,
    *,
    campaign_id: str | None = None,
    only_actionable: bool = True,
    today: date | None = None,
) -> dict:
    """⑤BEP 구성 — 상품별로 "판매가에서 무엇을 빼면 얼마가 남고, 그래서 클릭당 얼마까지냐".

    ★값이 없는 칸은 None으로 나간다(원칙22). 원가 미입력 상품은 추정치로 채우지 않고
      "원가가 입력되지 않았습니다"라고 말한다(계획서 §6 Phase3 완료기준 4).
    """
    day = today or kst_today()
    links = bep_breakdown.product_ad_links(db, campaign_id=campaign_id)

    # 소재별 이익 상한(SA 출력 → SA 입력, 원칙18-8). 상품 한 줄로 접는 규칙은 SA가 갖는다.
    # ★RPC 사다리는 (광고그룹·캠페인)에만 의존하므로 그 단위로 메모이즈한다. 종전엔 소재마다
    #   계정 전체 90일 집계를 다시 돌려 라이브 규모(소재 54)에서 같은 쿼리를 54번 재실행했다
    #   (리뷰 P2-10). 소재→상품 BEP 조인만 소재별로 남는다.
    ceilings: dict[str, dict] = {}
    rpc_cache: dict[tuple[str, str], dict] = {}
    for ads in links.values():
        for ad in ads:
            if ad["ad_id"] in ceilings:
                continue
            key = (ad["adgroup_id"], ad["campaign_id"])
            if key not in rpc_cache:
                rpc_cache[key] = bid_ceiling_calculator.resolve_rpc(
                    db, ad["adgroup_id"], ad["campaign_id"], day
                )
            ceilings[ad["ad_id"]] = bid_ceiling_calculator.ceiling_from(
                db, ad["ad_id"], rpc_cache[key]
            )

    # 상품별 배송 구성 — 스냅샷이 nb_share를 저장하지 않아 조회 시점에 다시 구한다.
    # 주문 전량 1회 스캔(약 1만 행, bep_calculator 주석 참조)이라 호출당 한 번만 부른다.
    nb_shares = bep_calculator.delivery_composition(db)

    out = bep_breakdown.build(
        db, links, today=day, only_actionable=only_actionable,
        nb_shares=nb_shares, ceilings=ceilings,
    )
    out["campaign_id"] = campaign_id
    out["as_of"] = day.isoformat()
    return out


def _retro_summary(db: Session, today: date) -> dict:
    """방향 정밀도 요약 — "우리 판단이 얼마나 맞았나"(보드 전체 합산, d7 기준).

    ★정직 경계(ref 31): 이건 방향 정확도이지 이익 인과가 아니다. 화면 문구도 그렇게 쓴다.
    """
    cutoff = today - timedelta(days=RETRO_WINDOW_DAYS)
    rows = db.query(NaverRetroSignal).filter(NaverRetroSignal.asof_date >= cutoff).all()
    d7 = retro_rollup.board_rollup(rows, 7)
    precision = d7["precision_spenders"]
    if precision is None:
        sentence = "최근 채점된 판단이 아직 없어 정확도를 말할 수 없습니다."
    else:
        sentence = (
            f"최근 {RETRO_WINDOW_DAYS}일 동안 채점된 판단 {d7['n']}건 중 "
            f"방향이 맞은 것이 {round(precision * 100)}%입니다. "
            "이건 '방향이 맞았나'까지고, '그래서 이익이 늘었나'는 아닙니다."
        )
    return {"window_days": RETRO_WINDOW_DAYS, **d7, "sentence": sentence}


def build_timeline(
    db: Session,
    *,
    days: int = DEFAULT_TIMELINE_DAYS,
    campaign_id: str | None = None,
    today: date | None = None,
    catalog_path: Path | None = None,
) -> dict:
    """⑥개선 타임라인 — "우리가 뭘 바꿨고, 그 전후 7일은 어땠나".

    ★"개선됐습니다"라고 말하지 않는다(계획서 §3-3). 이벤트가 거의 매일 나와 전후 창이 겹치므로
      겹친 이벤트를 전부 표기하고, 사후 7일이 아직 안 지났으면 "관찰 중"이라고 쓴다.
    ★카탈로그 파일이 없어도 500이 아니다 — `catalog_available:false` + 라이브 변경만.
    """
    day = today or kst_today()
    collected = improvement_events.collect(
        db, days=days, campaign_id=campaign_id, today=day, catalog_path=catalog_path
    )
    events = collected["events"]

    # ★날짜 단위로 접는다(라이브 실측 반영, 2026-07-28): 같은 날 확정된 결정들은 전후 창이
    #   **완전히 동일**해서 이벤트마다 impact를 내면 똑같은 숫자가 수십 번 반복된다(30일에
    #   이벤트 100건·평균 겹침 34건). 화면에도 안 읽히고, 집계 쿼리도 4배 낭비다.
    #   → 마커는 날짜에 찍고, 그날 결정들은 그 카드 안에 목록으로 넣는다.
    by_day: dict[str, list[dict]] = {}
    for ev in events:
        row = {
            "ref_key": ev["ref_key"],
            "label_ko": ev.get("label_ko", ""),
            "detail_ko": ev.get("detail_ko", ""),
            "effective_confidence": ev.get("effective_confidence", "unknown"),
            "scope": ev.get("scope", "account"),
            "source": ev.get("source", "track"),
            "curated": bool(ev.get("curated")),
        }
        cid = ev.get("campaign_id")
        if cid:
            row["campaign_name"] = ev.get("campaign_name") or campaign_name_of(db, cid)
        by_day.setdefault(ev["effective_date"], []).append(row)

    # ★일별 집계를 **한 번에** 뽑아 SA에 넘긴다(원칙18-6 — SA가 다른 SA를 직접 부르지 않는다,
    #   계획서 §2가 든 예시가 정확히 `event_impact_scorer(events, daily_rows)`다).
    #   부수 효과로 쿼리도 창당 2회에서 전체 1회로 줄었다(리뷰 P2-9·P2-10).
    daily: dict[str, dict] = {}
    if by_day:
        span_from = date.fromisoformat(min(by_day)) - timedelta(days=event_impact_scorer.WINDOW_DAYS)
        span_to = day - timedelta(days=1)   # D-0은 확정 적재 전
        if span_to >= span_from:
            for row in metrics_aggregator.aggregate(
                db, span_from, span_to, grain="date", campaign_filter=campaign_id
            )["rows"]:
                daily[row["ad_date"]] = row

    # 겹침 판정용 — 이벤트 단위로 센다(날짜로 접었다고 겹침이 작아 보이면 안 된다).
    flat = [
        {"ref_key": e["ref_key"], "effective_date": d}
        for d, group in by_day.items() for e in group
    ]
    day_rows: list[dict] = []
    for eff in sorted(by_day):
        # impact는 그날 대표 1건으로만 계산한다(같은 날은 창이 같다).
        impact = event_impact_scorer.score(
            {"ref_key": f"day-{eff}", "effective_date": eff},
            daily=daily, all_events=flat, today=day,
            same_day_count=len(by_day[eff]),
        )
        day_rows.append({"date": eff, "events": by_day[eff], "impact": impact})

    return {
        "as_of": day.isoformat(),
        "days": days,
        # ★창을 «수»가 아니라 «날짜»로도 낸다(2026-09-03, PAO 캘린더 통일). 화면이 날짜
        #   두 칸을 보여주는데 응답에 날짜가 없으면 프론트가 as_of·days로 **지어내야** 하고,
        #   그 순간 「화면이 말하는 창」과 「서버가 쓴 창」이 두 벌이 된다. 여기서 낸다.
        #   값은 `improvement_events.collect`가 실제로 쓰는 경계 그대로다(since = day - days).
        "window": {
            "from": (day - timedelta(days=days)).isoformat(),
            "to": day.isoformat(),
        },
        "campaign_id": campaign_id,
        "catalog_available": collected["catalog_available"],
        "undated_catalog_count": collected["undated_catalog_count"],
        "event_count": len(events),
        "timeline": day_rows,
        "retro": _retro_summary(db, day),
        "data_note": (
            "여기 숫자는 '이 변경 전후 기간이 이랬다'는 관찰입니다. "
            "같은 기간에 다른 변경도 함께 있어서, 이 변경 때문이라고 단정할 수는 없습니다. "
            "매출은 네이버가 광고에 붙여 준 전환매출(직접+간접)이라 실제 주문보다 큽니다 — "
            "'오늘 한눈에'의 실주문 기준 숫자와는 정의가 다릅니다."
        ),
    }
