# keyword_volume_baseline.py — 검색량 «기준선» 시계열 적재 (D-NAO-186 ①)
"""역할(SA·단일 책임): 우리가 **실제로 돈을 쓰는 키워드**의 월검색량을 매일 한 행씩 쌓는다.
판정도 해석도 하지 않는다 — 값과 「왜 못 받았는지」만 남긴다.

★왜 기존 `keyword_volume_sync`로 부족한가(2026-08-18 prod 실측):
  ① **대상이 반대다** — 그 잡은 «저클릭 키워드»(30일 클릭<10)만 본다. 그래서 **비용이 실제로
     나가는 키워드가 구조적으로 대상 밖**이다. 최근 30일 클릭이 있는 키워드 **1,193개·비용
     4,070,471원**의 검색량을 우리는 한 번도 받은 적이 없다.
  ② **덮어쓰기라 기준선이 안 된다** — `NaverEntity.monthly_volume` 한 칸을 갱신하므로 오늘
     값이 지난주 값을 지운다. D-NAO-186이 이 적재를 「소급 불가」로 승인한 이유가 시계열인데,
     덮어쓰기 컬럼에는 시계열이 없다.
  ③ **커버리지가 사실상 0에 수렴** — `on` 키워드 90,253개 중 값이 있는 것 5,000개(5.5%).
     주 1회 × 1,000개라 전건 1회전에 **약 90주**가 걸린다.
  → 그 잡을 **고치지 않고 나란히 둔다**: 목적(3단 분류 입력)과 대상(저클릭)이 다르다.
    같은 함수를 양쪽 용도로 비틀면 둘 다 애매해진다.

★마감(D-NAO-186): 아이폰은 매년 9월 출시(3년 실측 9/22·9/20·9/19). 검색량 기준선 없이 다음
  출시를 맞으면 「수요가 움직였나 우리가 움직였나」를 **또** 못 가른다 — 작년 아이폰 17 때
  실제로 못 갈랐다. **분석은 미룰 수 있어도 적재는 못 미룬다.**

★이 모듈은 읽기(외부 API)+쓰기(우리 테이블)만 한다. 광고 설정은 건드리지 않는다.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity, NaverKeywordVolumeDaily
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.services.naver_sa_ad_fetcher import fetch_keyword_volumes_detailed
from app.utils.kst import kst_today

log = logging.getLogger(__name__)

# 「머리 키워드」의 정의 — 최근 이 창 안에서 **비용이 나갔거나 클릭이 있었던** 키워드.
# ★임계를 클릭>0이 아니라 «비용 또는 클릭»으로 잡는 이유: 클릭 0인데 비용이 잡히는 행이
#   원장에 존재할 수 있고(집계 그레인 차이), 우리가 알고 싶은 것은 «돈이 닿은 검색어»다.
_LOOKBACK_DAYS = 30
# 1회 실행 상한. 5개/콜이므로 4,000개 = 800콜(D-NAO-186 승인 예산 약 2,200콜 안).
# ★초판은 1,500이었고 적대 리뷰가 P1으로 잡았다: prod 12개월을 한 달씩 뒤로 밀며 실측하니
#   대상이 1,500을 넘는 창이 **6개**였고 그중 하나가 **직전 아이폰 출시 창(2025-09-22, 2,006건)**,
#   최대는 2,243건이었다. 이 슬라이스의 존재 이유가 「9월 출시 때 수요/우리 조치를 가르는
#   기준선」인데 **출시철에 정확히 약 25%가 잘려 나가고 그 구멍은 소급 불가**였다.
#   콜 예산 때문도 아니었다 — 2,243÷5 = 449콜로 승인분의 20%다.
# ★그래도 상한을 없애지 않는 이유: 상한이 없으면 원장이 이상해졌을 때(센티널 누수·대량
#   신규 유입) 콜이 무한정 나간다. 대신 ①실수요(최대 2,243)의 약 1.8배로 올리고
#   ②잘릴 때 **반드시 로그**를 남기며 ③잘린다면 **비용이 큰 것부터 남긴다**(가나다순 금지).
_DEFAULT_LIMIT = 4000


def head_keywords(db: Session, *, lookback_days: int = _LOOKBACK_DAYS,
                  today: date | None = None) -> list[str]:
    """최근 창에서 돈이 닿은 키워드의 **텍스트** 목록 — **30일 비용 내림차순**.

    원장은 `keyword_id`를 들고 있고 keywordstool은 **문자열**로 답하므로 `NaverEntity`로
    이름을 푼다. 이름을 못 찾은 id는 조용히 버리지 않고 호출부가 셀 수 있게 로그로 남긴다.
    ★`__backfill__` 센티널 배제 — 이 저장소는 공용 필터가 없어 집계 SQL마다 다시 적어야 하고,
      잊으면 에러 없이 조용히 틀린다(2026-08-18 하루 2회 발생).
    """
    today = today or kst_today()
    cutoff = today - timedelta(days=lookback_days)
    rows = (
        db.query(
            NaverAdDaily.keyword_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0).label("cost_sum"),
        )
        .filter(
            NaverAdDaily.ad_date >= cutoff,
            NaverAdDaily.keyword_id != "",
            NaverAdDaily.keyword_id != BACKFILL_SENTINEL_ADGROUP,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.keyword_id)
        .having(
            (sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0) > 0)
            | (sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0) > 0)
        )
        .all()
    )
    cost_by_id = {r[0]: int(r[1] or 0) for r in rows if r[0]}
    if not cost_by_id:
        return []

    # 여러 keyword_id가 같은 이름을 가리킬 수 있다(그룹마다 별개 엔티티) → 이름 단위로 합산.
    cost_by_name: dict[str, int] = {}
    resolved = 0
    kw_ids = list(cost_by_id)
    for chunk_start in range(0, len(kw_ids), 500):  # IN 절 길이 방어
        chunk = kw_ids[chunk_start:chunk_start + 500]
        for entity_id, name in (
            db.query(NaverEntity.entity_id, NaverEntity.name)
            .filter(NaverEntity.entity_type == "keyword",
                    NaverEntity.entity_id.in_(chunk))
            .all()
        ):
            if name and name.strip():
                cost_by_name[name.strip()] = (
                    cost_by_name.get(name.strip(), 0) + cost_by_id.get(entity_id, 0)
                )
                resolved += 1
    if resolved < len(kw_ids):
        log.info("keyword_volume_baseline: keyword_id %d개 중 이름 해석 %d개 "
                 "(나머지는 naver_entity에 없음 — 삭제된 키워드 등)", len(kw_ids), resolved)
    # ★비용 내림차순(동률은 이름순으로 결정적). 상한에 걸려 잘릴 때 **돈이 큰 쪽이 남아야**
    #   한다 — 초판은 `sorted(names)`(가나다순)이라 출시철에 무작위나 다름없이 잘렸다.
    return sorted(cost_by_name, key=lambda n: (-cost_by_name[n], n))


def sync_baseline(db: Session, *, limit: int = _DEFAULT_LIMIT,
                  today: date | None = None) -> dict:
    """머리 키워드의 오늘자 검색량 1행씩 적재(멱등 — 같은 날 재실행은 갱신).

    Returns: {"targeted", "fetched", "inserted", "updated", "unmatched", "truncated"}
    ★`unmatched`(요청했는데 응답에 없던 키워드)를 **버리지 말고 센다** — 0이 아닌 값이 계속
      나오면 그건 「검색량이 없다」가 아니라 「우리가 못 받고 있다」이고, 둘은 다른 문제다.
    """
    today = today or kst_today()
    targets = head_keywords(db, today=today)
    if not targets:
        return {"targeted": 0, "fetched": 0, "inserted": 0, "updated": 0,
                "unmatched": 0, "truncated": 0}

    # ★잘림은 반드시 로그로 표면화한다 — 「targeted=4000」만 돌려주면 「4,500 중 4,000」인지
    #   「정확히 4,000」인지 구분이 안 된다(발견 0건과 실행 안 됨이 같은 숫자로 보이는 부류).
    truncated = max(0, len(targets) - limit)
    if truncated:
        log.warning(
            "keyword_volume_baseline: 대상 %d개가 상한 %d를 넘어 %d개를 버린다 — "
            "버리는 쪽은 30일 비용이 작은 순이다. 이 값이 계속 0이 아니면 상한을 올려라"
            "(소급 불가라 버린 날은 영영 못 채운다).", len(targets), limit, truncated)
    targets = targets[:limit]
    volumes = fetch_keyword_volumes_detailed(targets)

    # ★날짜만으로 조회한다(요청 키 목록으로 좁히지 않는다) — 네이버가 요청과 다른 표기의
    #   `relKeyword`를 돌려주면(공백·정규화 차이) 그 키는 `existing`에 안 잡혀 INSERT로 가고,
    #   같은 날 재실행 시 UNIQUE 위반으로 **그 실행분 전체가 커밋 전에 날아간다**
    #   (`db.commit()`이 루프 뒤 1회). 적대 리뷰 P2-1 — 라이브 유발 조건은 미입증이나
    #   수정이 한 줄이고 잃는 것이 없다.
    existing = {
        row.keyword: row
        for row in db.query(NaverKeywordVolumeDaily)
        .filter(NaverKeywordVolumeDaily.measured_date == today)
        .all()
    }

    inserted = updated = 0
    for keyword, v in volumes.items():
        row = existing.get(keyword)
        if row is None:
            db.add(NaverKeywordVolumeDaily(
                measured_date=today, keyword=keyword,
                pc_volume=v["pc"], mobile_volume=v["mobile"], total_volume=v["total"],
                competition=v["competition"], is_below_threshold=v["below_threshold"],
            ))
            inserted += 1
        else:
            row.pc_volume = v["pc"]
            row.mobile_volume = v["mobile"]
            row.total_volume = v["total"]
            row.competition = v["competition"]
            row.is_below_threshold = v["below_threshold"]
            updated += 1
    db.commit()

    result = {
        "targeted": len(targets), "fetched": len(volumes),
        "inserted": inserted, "updated": updated,
        "unmatched": len(targets) - len(volumes),
        "truncated": truncated,
    }
    log.info("keyword_volume_baseline(%s): %s", today, result)
    return result
