# ctr_alert.py — 소재 CTR 경보 SA (스프린트 VT3, D-NAO-82② §4.1).
#   역할(순수 SA·DB read-only): auto_operate=1 캠페인의 광고그룹(adgroup) grain에서 "입찰로
#   못 푸는" 소재 CTR 문제를 감지한다. 밴드(순위≤4.0) 안에 있는데도 클릭이 거의 없으면 —
#   순위는 이미 충분히 좋은데 소재(썸네일·가격·리뷰)가 클릭을 못 끌어내는 구간이라, 입찰을
#   더 올려도 헛돈다(순위가 이미 밴드 안이라 올릴 여지도 없음). 이 모듈은 네이버 API·DB에
#   절대 쓰지 않는다(관찰 전용) — 브리핑·래더 skip은 전부 harness(auto_operator) 몫
#   (원칙18 — SA 독립·단일 책임, SA 간 직접 호출 금지·harness가 SA간 정보를 유통하는 허브).
#
#   ★스코프 판단: 함수는 campaign_id 하나를 받아 그 캠페인만 채점한다(vitality_signal의
#   "전 계정 자체 순회"와 다른 선택) — 호출부 두 곳(일 레인 브리핑=auto_operate 캠페인
#   목록을 순회하며 캠페인당 1회 호출·탐색 래더=이미 캠페인 loop 안에서 캠페인당 1회 호출)
#   모두 "캠페인 1개" 단위로 필요해서다(PLAN §4.1 "캠페인당 경보 산출은 순회 밖 1회").
#   전 계정 자체 순회로 만들면 탐색 래더 호출부가 매 캠페인마다 전 계정을 다시 스캔하는
#   불필요한 중복이 생긴다. 그래도 auto_operate=1 검증은 이 SA 자신이 한다(caller를
#   못 믿는 vitality_signal 관례 계승 — 스코프 보증이 호출부 조합이 아니라 SA 자신에게 있다).
#
#   ★창 설계 근거(PLAN §4.1 착수 전 실측): 04(SHOPPING) 그룹별 rolling 3일(07-19~21) 확정치는
#   그룹마다 클릭 1~3씩 존재해 "3일 클릭0"만으론 오늘 하루의 가뭄을 다음날 아침(as_of=D-1)에도
#   못 잡는다 → 최근 1일 창(W1)을 3일 창(W3)과 병행 평가한다(창별 독립·OR, 하나라도 발화하면
#   경보). W1은 "클릭0"만 보고(단일일은 기대클릭 비교가 표본 통계적으로 불안정), W3만 트레일링
#   CTR 대비 −80%↓ 분기를 추가로 본다(누적 표본이 있어야 "기대치 대비 부족"을 말할 수 있음).
#
#   ★D-NAO-103 통계 필터(2026-07-28 개편): "클릭0"은 그 자체로 이상이 아니다 — 애초에 클릭이
#   기대됐을 때만 이상이다. 만성 저CTR 그룹(파워링크 0.14~0.47%·154일)은 하루 노출 수백에도
#   기대클릭이 1 미만이라 클릭0이 통계적으로 평범한 날이 태반인데, 구 로직은 그걸 매일
#   경보로 올렸다(07-23~28 5~8그룹/일 반복 발화 → Jino 읽기 포기). 이제 W1·W3 둘 다
#   기대클릭(=트레일링 CTR × 창 노출) ≥2 를 요구하고, 트레일링 표본이 아예 없으면 비발화한다.
#   트레일링 CTR 창도 구 30일에서 **가용 전 기간**으로 넓혔다(_trailing_agg_by_group 주석).
#   발화 억제(신규 진입만 개별 발화·만성은 주간 요약)는 이 SA가 아니라 브리핑 harness 몫이다 —
#   이 SA의 산출물은 탐색 래더 skip 게이트도 함께 쓰므로 만성 건도 계속 판정돼야 한다.
#
#   ★grain 주의: naver_ad_daily는 파워링크(WEB_SITE)는 keyword_id 단위, 쇼핑/브랜드검색은
#   adgroup 단위(keyword_id='' sentinel)로 쌓인다. 이 SA는 adgroup_id로 group_by하므로
#   파워링크 그룹도 소속 키워드 행이 자동으로 그 그룹에 롤업된다(별도 분기 불필요).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

# ── 경보 상수(PLAN §4.1 명문) ──
_MIN_IMP = 200                                  # W1·W3 공통 노출 하한
_RANK_BAND_TOP = Decimal("4.0")                 # "입찰로 못 푸는" 판정 근거 — 순위 ≤4.0(밴드 내)만 경보
_W3_WINDOW_DAYS = 3                             # rolling 3일(D0-2..D0)
_EXPECTED_CLK_RATIO = Decimal("0.2")            # 기대클릭의 20%(=−80%↓) 미만이면 발화
_MIN_EXPECTED_CLK_FOR_RATIO = Decimal("5")      # 기대클릭 <5면 −80% 분기 발화 금지(소표본 오탐 방지)
# D-NAO-103 통계 필터: "클릭0"이 이례적이려면 애초에 클릭이 기대됐어야 한다. 만성 저CTR
# 그룹(파워링크 0.14~0.47%)은 하루 노출 수백이어도 기대클릭이 1 미만이라 클릭0이 통계적으로
# 평범하다 — 그걸 매일 경보로 올린 것이 07-23~28 반복 발화의 실체. 기대클릭 <2 = 정보 없음 → 억제.
_MIN_EXPECTED_CLK_FOR_ZERO = Decimal("2")
_TRAILING_CTR_GAP_DAYS = 2                      # W3(D0-2..D0)와 겹치지 않게 D0-3까지에서 끝냄
_GROUP_TRAILING_MIN_IMP = 1000                  # 그룹 트레일링 표본 하한(미달=캠페인 CTR 폴백)

# ── D-1 부분적재 가드(VT3 codex 1R P2-1) ──
_PARTIAL_LOAD_LOOKBACK_DAYS = 3                  # 직전 창 길이(vitality_signal._PARTIAL_LOAD_LOOKBACK_DAYS 동일)
_PARTIAL_LOAD_MIN_RATIO = Decimal("0.5")         # D0 행수/직전 창 일평균 < 0.5 = 부분적재 의심


def _to_date(d) -> date:
    """SQLite가 ad_date를 str로 돌려주는 환경 방어(vitality_signal._to_date와 동형 재구현 —
    SA간 직접 import 금지, 원칙18-6)."""
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def _is_auto_operate_campaign(db: Session, campaign_id: str) -> bool:
    """스코프 자체 검증(caller를 못 믿는 vitality_signal 관례 계승) — auto_operate=1이
    아니면 무조건 경보 0(호출부가 실수로 non-auto 캠페인을 넘겨도 이 SA가 막는다)."""
    row = (
        db.query(NaverCampaignSettings.auto_operate)
        .filter(NaverCampaignSettings.campaign_id == campaign_id)
        .first()
    )
    return bool(row and row[0])


def _partial_load_suspected(db: Session, campaign_id: str, d0: date) -> tuple[bool, str | None]:
    """부분적재 의심 가드(VT3 codex 1R P2-1) — 캠페인 스코프. D0(=as_of=D-1)의 실적재 행수가
    직전 3일 일평균의 절반 미만이면 D0 신호가 절반만 적재된 상태로 의심(W1=최근 1일이 통째로
    왜곡 → 클릭0 오탐) → 그 캠페인 경보 전체 억제(빈 alerts 반환은 호출부에서).

    vitality_signal._partial_load_suspected(:313~353)·anomaly_feed.freshness_partial_load와
    동일 로직의 **3번째 경량 재구현**이다(SA간 직접 import 금지, 원칙18-6 — 출처 명시). 차이:
    여기선 campaign_id로 스코프한다(ctr_alert가 캠페인 1개 단위 판정이라). 근거: 부분적재는 D-1
    sync가 절반만 돌고 죽은 경우 — 행 자체가 사라지므로 행수 비교로 잡힌다(CTR 가뭄은 행수
    유지·clk만 하락이라 이와 직교, 오검출 없음). baseline 없으면(직전 창 데이터 0일) 가드
    미적용(추정 금지 — vitality와 동일 False). sentinel 행 제외.

    반환: (partial 여부, 사유 문자열|None)."""
    window_from = d0 - timedelta(days=_PARTIAL_LOAD_LOOKBACK_DAYS)
    window_to = d0 - timedelta(days=1)
    as_of_count = int(
        db.query(sqlfunc.count(NaverAdDaily.id))
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date == d0,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .scalar() or 0
    )
    daily_counts = dict(
        db.query(NaverAdDaily.ad_date, sqlfunc.count(NaverAdDaily.id))
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= window_from, NaverAdDaily.ad_date <= window_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date)
        .all()
    )
    days_with_data = len(daily_counts)
    if days_with_data == 0:
        return False, None  # baseline 없음 = 비교 불가(추정 금지)
    baseline_avg = Decimal(sum(daily_counts.values())) / Decimal(days_with_data)
    if baseline_avg == 0:
        return False, None
    ratio = Decimal(as_of_count) / baseline_avg
    if ratio < _PARTIAL_LOAD_MIN_RATIO:
        return True, (
            f"D0({d0.isoformat()}) 부분적재 의심 — 캠페인 {campaign_id} 행수 {as_of_count} vs "
            f"직전 {days_with_data}일 평균 {float(round(baseline_avg, 1))}행"
            f"({float(round(ratio * 100, 1))}%) → 경보 억제"
        )
    return False, None


def _window_daily_by_group(
    db: Session, campaign_id: str, start: date, end: date,
) -> dict[str, dict[date, dict]]:
    """[start,end] 일별×그룹 (imp,clk,rank_sum) — sentinel 제외 1쿼리. W1(단일일)·W3(3일 합)
    둘 다 이 원료에서 유도한다(중복 쿼리 방지)."""
    rows = (
        db.query(
            NaverAdDaily.ad_date, NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.rank_sum), 0),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date >= start,
            NaverAdDaily.ad_date <= end,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.ad_date, NaverAdDaily.adgroup_id)
        .all()
    )
    out: dict[str, dict[date, dict]] = {}
    for d, gid, imp, clk, rsum in rows:
        out.setdefault(gid, {})[_to_date(d)] = {
            "imp": int(imp), "clk": int(clk), "rank_sum": int(rsum),
        }
    return out


def _trailing_agg_by_group(db: Session, campaign_id: str, end: date) -> dict[str, dict]:
    """(…, end] 그룹별 (imp,clk) 합 — 트레일링 CTR 원료(rank_sum 불필요).

    D-NAO-103: 하한 없는 **가용 전 기간**이다(구 30일 창 폐기). 이유 — 기대클릭 필터가
    새로 판정의 중심이 되면서 트레일링 CTR의 표본 안정성이 곧 오탐률이 됐다. 만성 저CTR
    그룹은 30일로 자르면 그룹 표본 하한(1,000노출)에 못 미쳐 캠페인 CTR로 폴백되는데,
    캠페인 평균은 그 그룹보다 높아 기대클릭이 부풀고 → "이례적"이 아닌 날도 발화한다."""
    rows = (
        db.query(
            NaverAdDaily.adgroup_id,
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        )
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.ad_date <= end,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.adgroup_id)
        .all()
    )
    return {gid: {"imp": int(imp), "clk": int(clk)} for gid, imp, clk in rows}


def _campaign_type(db: Session, campaign_id: str) -> str:
    """캠페인 유형(SHOPPING/WEB_SITE/BRAND_SEARCH) — 브리핑 문구 분기용(D-NAO-103).
    naver_ad_daily 자기 소스에서 읽는다(이 SA는 naver_entity를 안 본다 — 원료 단일화).
    미상이면 ''(호출부가 "유형 미상 = 쇼핑 전용 문구 금지"로 안전측 해석)."""
    row = (
        db.query(NaverAdDaily.campaign_type)
        .filter(
            NaverAdDaily.campaign_id == campaign_id,
            NaverAdDaily.campaign_type != "",
        )
        .first()
    )
    return str(row[0]) if row else ""


def _avg_rank(rank_sum: int, imp: int) -> Decimal | None:
    # VT3 codex 1R P2-3: imp>0인데 rank_sum≤0이면 순위 소스 누락(순위 미상)이므로 None 반환 —
    # rank 0.0을 "최상위 순위"로 오독해 과열밴드(≤2.5)로 오탐 발화하는 것 차단. rank None은
    # _w1_alert/_w3_alert에서 이미 비발화 처리(순위 미상=경보 발화 금지, 추정 금지).
    if imp <= 0 or rank_sum <= 0:
        return None
    return Decimal(rank_sum) / Decimal(imp)


def _group_trailing_ctr(
    trailing: dict[str, dict], gid: str, campaign_ctr: Decimal | None,
) -> Decimal | None:
    """그룹 표본(임프)≥1,000이면 그룹 자체 CTR, 미달이면 캠페인 CTR 폴백, 그것도 없으면 None
    (호출부가 None을 "기대클릭 게이트 생략(clk=0 분기만)"으로 처리)."""
    tr = trailing.get(gid, {"imp": 0, "clk": 0})
    if tr["imp"] >= _GROUP_TRAILING_MIN_IMP:
        return Decimal(tr["clk"]) / Decimal(tr["imp"])
    return campaign_ctr


def _w1_alert(
    gid: str, campaign_id: str, day_row: dict | None, trailing_ctr: Decimal | None,
) -> dict | None:
    """W1(최근 1일): imp≥200 ∧ clk=0 ∧ avg_rank≤4.0 ∧ 기대클릭≥2(D-NAO-103).

    기대클릭 = trailing_ctr × 당일 imp. 트레일링 CTR을 못 구하면(그룹·캠페인 둘 다 표본 0)
    "이례적인지" 자체를 말할 수 없으므로 비발화(추정 금지 — 구 동작은 클릭0만으로 발화했다)."""
    if day_row is None or day_row["imp"] < _MIN_IMP or day_row["clk"] != 0:
        return None
    rank = _avg_rank(day_row["rank_sum"], day_row["imp"])
    if rank is None or rank > _RANK_BAND_TOP:
        return None
    expected_clk = (trailing_ctr * Decimal(day_row["imp"])) if trailing_ctr is not None else None
    if expected_clk is None or expected_clk < _MIN_EXPECTED_CLK_FOR_ZERO:
        return None
    rank_f = float(round(rank, 2))
    expected_f = float(round(expected_clk, 1))
    return {
        "campaign_id": campaign_id, "adgroup_id": gid, "window": "W1",
        "imp": day_row["imp"], "clk": 0, "avg_rank": rank_f, "expected_clk": expected_f,
        "reason": (
            f"W1 최근1일 노출{day_row['imp']}·클릭0(기대클릭{expected_f})·순위{rank_f}"
            "(밴드 내≤4.0) — 입찰로 못 푸는 소재 CTR 문제 — 사람 처방 대상"
        ),
    }


def _w3_alert(
    gid: str, campaign_id: str, by_date: dict[date, dict], trailing_ctr: Decimal | None,
) -> dict | None:
    """W3(rolling 3일): 누적 imp≥200 ∧ avg_rank≤4.0 ∧ (clk=0 ∧ 기대클릭≥2 | clk≤기대클릭×0.2).

    기대클릭 = trailing_ctr × 창 imp. expected_clk<5면 −80% 분기 비활성(소표본 오탐 방지),
    expected_clk<2면 clk=0 분기도 비활성(D-NAO-103 통계 필터 — 클릭0이 평범한 구간)."""
    imp = sum(v["imp"] for v in by_date.values())
    if imp < _MIN_IMP:
        return None
    clk = sum(v["clk"] for v in by_date.values())
    rank_sum = sum(v["rank_sum"] for v in by_date.values())
    rank = _avg_rank(rank_sum, imp)
    if rank is None or rank > _RANK_BAND_TOP:
        return None

    expected_clk = (trailing_ctr * Decimal(imp)) if trailing_ctr is not None else None
    if expected_clk is None:
        return None  # 트레일링 표본 전무 = 이례성 판정 불가(추정 금지)
    ratio_fired = (
        expected_clk >= _MIN_EXPECTED_CLK_FOR_RATIO
        and Decimal(clk) <= expected_clk * _EXPECTED_CLK_RATIO
    )
    zero_fired = clk == 0 and expected_clk >= _MIN_EXPECTED_CLK_FOR_ZERO
    if not zero_fired and not ratio_fired:
        return None

    rank_f = float(round(rank, 2))
    expected_f = float(round(expected_clk, 1))
    alert = {
        "campaign_id": campaign_id, "adgroup_id": gid, "window": "W3",
        "imp": imp, "clk": clk, "avg_rank": rank_f, "expected_clk": expected_f,
    }
    detail = f"클릭{clk}(기대클릭{expected_f}"
    detail += " 대비 −80%↓)" if (ratio_fired and clk > 0) else ")"
    alert["reason"] = (
        f"W3 최근3일 누적 노출{imp}·{detail}·순위{rank_f}(밴드 내≤4.0) — "
        "입찰로 못 푸는 소재 CTR 문제 — 사람 처방 대상"
    )
    return alert


def detect_ctr_alerts(db: Session, campaign_id: str, *, now: datetime | None = None) -> dict:
    """소재 CTR 경보 산출(PLAN §4.1). 순수 SA·read-only. 캠페인 1개 스코프(위 헤더 근거 주석).

    as_of=D0=today−1(최근 완료일, 코드베이스 as_of=D-1 관례 — naver_ad_daily는 당일 진행중
    부분치를 포함하지 않는다). 반환: {"alerts": [{campaign_id, campaign_type, adgroup_id,
    window(W1|W3), imp, clk, avg_rank, expected_clk, reason}, ...]}. 경보 없으면 {"alerts": []}.
    D-NAO-103: expected_clk는 이제 **항상** 있다(없으면 애초에 발화하지 않는다)."""
    if not _is_auto_operate_campaign(db, campaign_id):
        return {"alerts": []}

    now = now or kst_now()
    d0 = now.date() - timedelta(days=1)

    # VT3 codex 1R P2-1: D0(=as_of) 부분적재 가드 — D0 행수가 직전 3일 일평균의 절반 미만이면
    # W1(최근 1일)이 통째로 왜곡되므로(클릭0 오탐) 이 캠페인 경보 전체 억제. baseline 없으면 미적용.
    partial, partial_reason = _partial_load_suspected(db, campaign_id, d0)
    if partial:
        log.warning("ctr_alert: %s", partial_reason)
        return {"alerts": []}

    w3_start = d0 - timedelta(days=_W3_WINDOW_DAYS - 1)  # D0-2
    daily = _window_daily_by_group(db, campaign_id, w3_start, d0)

    tr_end = d0 - timedelta(days=_TRAILING_CTR_GAP_DAYS + 1)  # D0-3(가용 전 기간 ~ D0-3]
    trailing = _trailing_agg_by_group(db, campaign_id, tr_end)
    campaign_trailing_imp = sum(v["imp"] for v in trailing.values())
    campaign_trailing_clk = sum(v["clk"] for v in trailing.values())
    campaign_ctr = (
        Decimal(campaign_trailing_clk) / Decimal(campaign_trailing_imp)
        if campaign_trailing_imp > 0 else None
    )
    campaign_type = _campaign_type(db, campaign_id)

    alerts: list[dict] = []
    for gid, by_date in daily.items():
        trailing_ctr = _group_trailing_ctr(trailing, gid, campaign_ctr)
        w1 = _w1_alert(gid, campaign_id, by_date.get(d0), trailing_ctr)
        if w1 is not None:
            alerts.append(w1)
        w3 = _w3_alert(gid, campaign_id, by_date, trailing_ctr)
        if w3 is not None:
            alerts.append(w3)
    for a in alerts:
        a["campaign_type"] = campaign_type  # 브리핑 문구 분기(쇼핑 전용 "탐색 래더" 문구 차단)

    log.info(
        "ctr_alert: detect_ctr_alerts campaign=%s as_of=%s 경보=%s",
        campaign_id, d0.isoformat(), len(alerts),
    )
    return {"alerts": alerts}
