# pooled_estimate_writer.py — [9] 계층 EB 풀링 산출 기록 SA (M2-a · D-NAO-214 · ref 65 S1-ⓑ)
# 역할(SA): hierarchical_pooling.pool_all을 키워드 grain 전수에 돌려 CTR/CVR/RPC 축소추정치를
#   naver_pooled_estimate_daily에 남긴다. **판정하지 않는다 — 추정치를 남길 뿐이다.**
#   자동 쓰기 경로에 연결되지 않는다(계약 §3 「신규 자동 쓰기 0건」).
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverPooledEstimateDaily
from app.services.naver_ad import hierarchical_pooling, proposal_pipeline
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# 집계 창 — proposal_pipeline의 기본 lookback과 같은 30일. 창을 행에 함께 적는 이유는
# 창 없는 추정치가 해석 불가이기 때문이다([[coverage-claims-need-their-window]]).
WINDOW_DAYS = 30

GRAIN_KEYWORD = "keyword"


def _empty() -> dict:
    return {"imp": 0, "clk": 0, "conv_cnt": 0, "conv_amt": 0}


def _raw(num: int, den: int) -> Decimal:
    """수축 전 관측값. 분모 0이면 0 — 가중치(n)도 0이라 결과에 영향이 없다(pool 공식과 동형)."""
    return (Decimal(num) / Decimal(den)) if den else Decimal("0")


def _keyword_rows(db: Session, date_from: date, date_to: date) -> list[tuple]:
    """키워드 grain 실적 집계.

    keyword_id='' sentinel(SHOPPING·BRAND_SEARCH의 그룹 단위 행)은 **제외**한다 — 그건 키워드가
    아니라 그룹이고, 섞으면 scope_key가 빈 문자열인 행이 캠페인마다 하나씩 생겨 UNIQUE
    (target_date, grain, scope_key)에서 서로를 덮어쓴다. 그룹 grain은 이 슬라이스 범위 밖이다.
    """
    return db.query(
        NaverAdDaily.keyword_id,
        NaverAdDaily.campaign_id,
        NaverAdDaily.adgroup_id,
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_cnt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= date_from,
        NaverAdDaily.ad_date <= date_to,
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        NaverAdDaily.keyword_id != "",
    ).group_by(
        NaverAdDaily.keyword_id, NaverAdDaily.campaign_id, NaverAdDaily.adgroup_id,
    ).all()


def write_pooled_estimates(db: Session, *, as_of: date | None = None) -> dict:
    """키워드 grain 전수 계층 풀링 산출 → naver_pooled_estimate_daily upsert.

    반환 dict는 수동 트리거 라우터 응답에 그대로 실린다(계약 §4 S1-② 라이브 증거의 출처).
    ★`complete=False`면 호출측(크론 잡)이 **raise 해야 한다** — 부분 적재를 success로 굳히는 것이
    교훈 #319·#321이 세 번 반복한 결함이다.
    """
    as_of = as_of or kst_now().date()
    # 어제까지를 창으로 쓴다 — 당일 행은 수집이 진행 중이라 분모가 계속 자란다(stale 아닌 «미완성»).
    window_to = as_of - timedelta(days=1)
    window_from = window_to - timedelta(days=WINDOW_DAYS - 1)

    agg = proposal_pipeline._precompute_aggregates(db, window_from, window_to)
    rows = _keyword_rows(db, window_from, window_to)

    result = {
        "as_of": as_of.isoformat(),
        "window_from": window_from.isoformat(),
        "window_to": window_to.isoformat(),
        "candidates": len(rows),
        "written": 0,
        "updated": 0,
        "skipped_no_signal": 0,
        "complete": False,
        "incomplete_reason": None,
    }

    existing = {
        r.scope_key: r
        for r in db.query(NaverPooledEstimateDaily).filter(
            NaverPooledEstimateDaily.target_date == as_of,
            NaverPooledEstimateDaily.grain == GRAIN_KEYWORD,
        ).all()
    }

    try:
        for keyword_id, campaign_id, adgroup_id, imp, clk, cnt_d, cnt_i, amt_d, amt_i in rows:
            keyword_row = {
                "imp": int(imp), "clk": int(clk),
                "conv_cnt": int(cnt_d) + int(cnt_i),
                "conv_amt": int(amt_d) + int(amt_i),
            }
            # 노출도 클릭도 없는 키워드는 raw도 prior 가중도 전부 0 — 상위 prior를 그대로 베낀
            # 행만 대량으로 쌓인다(원장 대비 정보 0). 남기지 않고 센다.
            if keyword_row["imp"] == 0 and keyword_row["clk"] == 0:
                result["skipped_no_signal"] += 1
                continue

            group_agg = agg["group"].get(adgroup_id, _empty())
            campaign_agg = agg["campaign"].get(campaign_id, _empty())
            account_agg = agg["account"]

            # ★한 번의 호출로 산출과 prior를 **같은 계산에서** 받는다(적대 리뷰 1R P2 채택).
            # 이전 판은 prior를 이 파일에서 재계산했는데, 그러면 pool_metric의 정의가 바뀌는 날
            # 저장된 prior만 옛 정의로 남아 수기 검산이 «맞다»면서 실제와 다른 값을 가리킨다.
            pooled, prior = hierarchical_pooling.pool_all_with_priors(
                keyword_row, group_agg, campaign_agg, account_agg,
            )

            values = dict(
                campaign_id=campaign_id or "",
                adgroup_id=adgroup_id or "",
                window_from=window_from,
                window_to=window_to,
                n_imp=keyword_row["imp"],
                n_clk=keyword_row["clk"],
                n_conv_cnt=keyword_row["conv_cnt"],
                n_conv_amt=keyword_row["conv_amt"],
                raw_ctr=_raw(keyword_row["clk"], keyword_row["imp"]),
                raw_cvr=_raw(keyword_row["conv_cnt"], keyword_row["clk"]),
                raw_rpc=_raw(keyword_row["conv_amt"], keyword_row["clk"]),
                prior_ctr=prior["ctr"],
                prior_cvr=prior["cvr"],
                prior_rpc=prior["rpc"],
                pooled_ctr=pooled["ctr"],
                pooled_cvr=pooled["cvr"],
                pooled_rpc=pooled["rpc"],
                shrink_k=hierarchical_pooling.SHRINK_K,
            )

            row = existing.get(keyword_id)
            if row is None:
                row = NaverPooledEstimateDaily(
                    target_date=as_of, grain=GRAIN_KEYWORD, scope_key=keyword_id, **values,
                )
                db.add(row)
                # 같은 회차에서 같은 keyword_id가 두 번 나오면(그룹 이동 등) 두 번째가 INSERT를
                # 또 시도해 UNIQUE에 걸린다 — **방금 add한 그 객체를** 등재해 다음 회에 UPDATE로
                # 흐르게 한다. 새 객체를 따로 만들어 넣으면 세션 밖 객체라 두 번째 값이 조용히
                # 버려지면서 카운터만 늘어난다(교훈 #318의 모양 — 한 사실을 두 곳에 쓰면서
                # 한쪽만 지속되면 나머지가 거짓을 기록한다).
                existing[keyword_id] = row
                result["written"] += 1
            else:
                for k, v in values.items():
                    setattr(row, k, v)
                result["updated"] += 1

        db.commit()
        result["complete"] = True
    except Exception as e:  # noqa: BLE001
        db.rollback()
        result["incomplete_reason"] = f"{type(e).__name__}: {e}"
        log.exception("pooled_estimate_writer 실패: %s", e)

    return result
