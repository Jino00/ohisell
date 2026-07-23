# profit_scorecard.py — profit_scorecard_sa (P7 일일 이익 스코어카드, D-NAO-85/ref39 P7,
#   docs/PLAN_naver-ad-ex-expansion.md §4-1) — 관찰 전용, 실쓰기 0(네이버 API 호출도 0).
# 역할: 목적함수(D-NAO-59 총이익 절대액)를 매일 캠페인별로 표면화한다 — ROAS·매출·클릭만
#   보이고 총이익 절대액이 안 보여 매출 −52% 사태를 3일 뒤에야 사람이 발견한 사고(ref39)를
#   막는다. 대상 = auto_operate=True ∪ optimizer='ours' 캠페인(현재 03/04/17프로/P_Test).
#   ①어제(D-1) 캠페인별 이익+합계 ②최근 7일(D-7~D-1) 일평균 ③2026년 6월(06-01~06-30)
#   baseline 대비 증감%. 총이익 = 보정conv_amt(직+간접) ÷ bep_roas − cost.
#   bep_roas는 campaign_target_resolver의 캠페인-grain 우선순위(② 캠페인 매핑 상품 BEP →
#   ③ 계정 기본 BEP)를 재사용한다 — exploration._resolve_exploration_bep_roas의 그룹 우선순위
#   (①)를 뺀 캠페인 부분과 동형이나, 그 private 헬퍼를 직접 재사용하지 않고 이 모듈에
#   독립 구현한다(auto_operator._resolve_target_roas와 동일 이유 — 다른 모듈 내부구현 변경에
#   결합되지 않게). 해석 불가 캠페인은 숫자를 지어내지 않고 "BEP 미확인" 행으로 표기한다.
#   보정계수는 diagnosis.correction_factor 재사용(fail-open: source≠actual_revenue_ratio면
#   무보정 1.0 값을 쓰되 "무보정"으로 정직하게 표기 — 스코어카드는 관찰이라 계산을 죽이지
#   않는다). sentinel(__backfill__) 제외는 metrics_aggregator.aggregate가 내부에서 이미
#   처리(auto_operator._settlement_agg와 동일 필터, 2배 집계 방지) — 재구현하지 않고 그
#   기존 순수 SA를 그대로 재사용한다. 표면 = diary observe + Slack(bm_briefing.py
#   run_daily_briefing 관례 미러).
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverEntity, NaverProductBep
from app.services.naver_ad import campaign_target_resolver, metrics_aggregator, slack_notifier
from app.services.naver_ad.diagnosis import correction_factor
from app.services.naver_ad.diary import ACTOR_SYSTEM, write_diary_entry
from app.utils.kst import kst_now

log = logging.getLogger(__name__)

ACTION_PROFIT_SCORECARD = "profit_scorecard"  # diary observe action(vault_export 렌더 관례, bm_briefing 동형)

_LOOKBACK_7D_DAYS = 7  # 최근 7일 일평균 창(D-7~D-1)
# D-NAO-85 origin(ref39 P7): 매출 −52% 사태 발견의 기준이 된 "MOP 전 6월" — 상대창이 아니라
# 이 스프린트의 발단 자체가 가리키는 고정 달력월. 상대(rolling) baseline이 아님을 명시한다.
_JUNE_BASELINE_FROM = date(2026, 6, 1)
_JUNE_BASELINE_TO = date(2026, 6, 30)

_Q0 = Decimal("1")  # 원 단위 반올림(반올림 규칙은 metrics_aggregator._ratio와 동일 ROUND_HALF_UP)
_STATUS_OK = "ok"
_STATUS_BEP_UNKNOWN = "bep_unknown"


def _round_won(value: Decimal) -> int:
    return int(value.quantize(_Q0, rounding=ROUND_HALF_UP))


def _target_campaign_ids(db: Session) -> set[str]:
    """대상 캠페인 = auto_operate=True ∪ optimizer='ours'(D-NAO-13, 현재 03/04/17프로/P_Test).
    auto_operator._auto_operate_campaign_ids·proposal_writer._ours_campaign_ids와 동형 쿼리를
    이 모듈에 로컬 복제한다(원칙18 SA간 직접 호출 금지 — 값만 같은 쿼리로 재현하는 두 모듈의
    기존 관례를 그대로 잇는다. 셋째 지점에서 다시 만들면 셋 다 갈라질 위험이 있으나, 두 상수
    모두 원본이 NaverCampaignSettings 컬럼 자체라 정의가 바뀌면 모델 마이그레이션이 드러낸다)."""
    auto_ids = {
        r[0] for r in db.query(NaverCampaignSettings.campaign_id)
        .filter(NaverCampaignSettings.auto_operate.is_(True)).all()
    }
    ours_ids = {
        r[0] for r in db.query(NaverCampaignSettings.campaign_id)
        .filter(NaverCampaignSettings.optimizer == "ours").all()
    }
    return auto_ids | ours_ids


def _campaign_names(db: Session, campaign_ids: set[str]) -> dict[str, str]:
    """campaign_id → naver_entity.name (bm_briefing._campaign_names 관례 미러). 이름이 없으면
    매핑에서 빠지고 호출부가 campaign_id로 폴백한다."""
    if not campaign_ids:
        return {}
    return {
        e.entity_id: e.name
        for e in db.query(NaverEntity.entity_id, NaverEntity.name)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.entity_id.in_(campaign_ids))
        .all()
        if e.name
    }


def _campaign_bep_roas(db: Session, campaign_id: str) -> Decimal | None:
    """캠페인 BEP ROAS — campaign_target_resolver 캠페인-grain 우선순위(② 캠페인 매핑 상품
    BEP 가중평균 → ③ 계정 기본 BEP 매출가중). 매핑·계정 기본값 둘 다 없으면 None(=BEP 미확인,
    호출부가 "숫자 없음" 행으로 표기한다 — 추정 금지)."""
    bep = campaign_target_resolver.weighted_product_value_for_campaign(
        db, campaign_id, NaverProductBep.bep_roas,
    )
    if bep is not None and bep > 0:
        return bep
    bep = campaign_target_resolver.account_default_bep_roas(db)
    if bep is not None and bep > 0:
        return bep
    return None


def _window_profit(
    db: Session, campaign_id: str, date_from: date, date_to: date, *,
    factor: Decimal, bep_roas: Decimal,
) -> dict:
    """[date_from, date_to] 창의 일평균 총이익 절대액.

    metrics_aggregator.aggregate(grain="date")가 반환하는 rows는 sentinel(__backfill__) 제외 후
    naver_ad_daily 실적이 실제로 존재하는 날짜만 나온다 — 그 개수(관측일수)로 나눈다(달력일수로
    나누면 데이터 공백을 0원 매출로 오인해 일평균이 왜곡된다. §4-5 예산봉투의 "관측일수" 관례와
    동형). 관측일 0이면 avg_profit=None(데이터 없음 — 0원과 구분).

    총이익 = (Σconv_amt × factor) ÷ bep_roas − Σcost. bep_roas·factor는 창 전체에서 상수이므로
    "총이익 합 ÷ 관측일수"와 "일별 총이익의 평균"이 수학적으로 동일하다(분배법칙) — 창을 한 번만
    집계하면 되고 일별로 반복 나눗셈할 필요가 없다.
    """
    agg = metrics_aggregator.aggregate(db, date_from, date_to, grain="date", campaign_filter=campaign_id)
    observed_days = len(agg["rows"])
    if observed_days == 0:
        return {"observed_days": 0, "avg_profit": None, "total_cost": 0, "total_conv_amt": 0}
    totals = agg["totals"]
    conv_amt_corrected = Decimal(totals["conv_amt"]) * factor
    total_profit = conv_amt_corrected / bep_roas - Decimal(totals["cost"])
    return {
        "observed_days": observed_days,
        "avg_profit": _round_won(total_profit / observed_days),
        "total_cost": totals["cost"],
        "total_conv_amt": totals["conv_amt"],
    }


def _campaign_row(db: Session, campaign_id: str, name: str, yesterday: date, *, factor: Decimal) -> dict:
    """캠페인 1행. BEP 해석 불가면 숫자를 만들어내지 않고 사유만 남긴다(§4-1 '숫자 조작 금지')."""
    bep_roas = _campaign_bep_roas(db, campaign_id)
    if bep_roas is None:
        return {
            "campaign_id": campaign_id, "name": name, "status": _STATUS_BEP_UNKNOWN,
            "yesterday_profit": None, "avg7_profit": None, "june_avg_profit": None, "pct_vs_june": None,
        }

    yday = _window_profit(db, campaign_id, yesterday, yesterday, factor=factor, bep_roas=bep_roas)
    win7 = _window_profit(
        db, campaign_id, yesterday - timedelta(days=_LOOKBACK_7D_DAYS - 1), yesterday,
        factor=factor, bep_roas=bep_roas,
    )
    june = _window_profit(
        db, campaign_id, _JUNE_BASELINE_FROM, _JUNE_BASELINE_TO, factor=factor, bep_roas=bep_roas,
    )

    pct_vs_june = None
    if win7["avg_profit"] is not None and june["avg_profit"]:  # 0/None이면 비율 미정의
        pct_vs_june = round(
            (win7["avg_profit"] - june["avg_profit"]) / abs(june["avg_profit"]) * 100, 1,
        )

    return {
        "campaign_id": campaign_id, "name": name, "status": _STATUS_OK, "bep_roas": float(bep_roas),
        "yesterday_profit": yday["avg_profit"], "avg7_profit": win7["avg_profit"],
        "june_avg_profit": june["avg_profit"], "pct_vs_june": pct_vs_june,
    }


def _fmt_row(row: dict) -> str:
    """캠페인 1줄 요약 — bm_briefing._op_line 압축 관례 미러."""
    name = row["name"]
    if row["status"] == _STATUS_BEP_UNKNOWN:
        return f"- {name}: BEP 미확인(상품 원가 미매핑) — 이익 산출 불가"
    yday_s = f"{row['yesterday_profit']:+,}원" if row["yesterday_profit"] is not None else "데이터없음"
    avg7_s = f"{row['avg7_profit']:+,}원" if row["avg7_profit"] is not None else "데이터없음"
    june_s = f"{row['june_avg_profit']:+,}원" if row["june_avg_profit"] is not None else "데이터없음"
    pct_s = f"{row['pct_vs_june']:+.1f}%" if row["pct_vs_june"] is not None else "-"
    return f"- {name}: 어제 {yday_s} · 7일평균 {avg7_s} · 6월평균 {june_s} (대비 {pct_s})"


def _build_text(rows: list[dict], yesterday: date, factor_info: dict) -> str:
    total_yesterday = sum(
        r["yesterday_profit"] for r in rows if r.get("yesterday_profit") is not None
    )
    factor = factor_info["factor"]
    corrected = factor_info.get("source") == "actual_revenue_ratio"
    factor_note = f"{factor}(실측)" if corrected else f"{factor}(무보정 — source={factor_info.get('source')})"
    header = (
        f"{yesterday.isoformat()} 일일 이익 스코어카드(D-NAO-59 목적함수=총이익 절대액) — "
        f"어제 합계 {total_yesterday:+,}원 · 보정계수 {factor_note}"
    )
    if not rows:
        return header + "\n- 대상 캠페인 없음(auto_operate/optimizer=ours 없음)"
    lines = [_fmt_row(r) for r in sorted(rows, key=lambda r: r["name"])]
    return "\n".join([header, *lines])


def run_profit_scorecard(db: Session, now: datetime | None = None) -> dict:
    """P7 일일 이익 스코어카드 진입점 — diary(observe) + Slack, 관찰 전용(실쓰기 0).

    ★fail-open은 이 함수를 호출하는 스케줄러 잡(run_naver_profit_scorecard_job)이 담당한다
    — run_naver_diary_reflection_job/run_naver_wisdom_job과 동일 위치(08:35 reflection과
    08:45 wisdom 사이에 끼는 관찰 전용 잡)·동일 관례로 예외를 다시 던지지 않는다. 이 함수
    자체는 run_daily_briefing(bm_briefing.py)과 같은 계약으로 자기 방어 없이 예외를 그대로
    던진다. write_diary_entry는 그와 별개로 자체 fail-open(독립 세션·내부 try)이라 일기 기록
    실패가 이 함수의 반환을 막지 않는다.
    """
    n = now or kst_now()
    yesterday = n.date() - timedelta(days=1)

    factor_info = correction_factor(db, yesterday)
    factor = factor_info["factor"]

    campaign_ids = _target_campaign_ids(db)
    names = _campaign_names(db, campaign_ids)
    rows = [
        _campaign_row(db, cid, names.get(cid, cid), yesterday, factor=factor)
        for cid in sorted(campaign_ids)
    ]

    text = _build_text(rows, yesterday, factor_info)
    write_diary_entry(
        db, "observe", "", actor=ACTOR_SYSTEM, action=ACTION_PROFIT_SCORECARD, rationale=text, now=n,
    )
    slack_result = slack_notifier.notify_text(text, log_label="일일 이익 스코어카드")

    result = {
        "date": yesterday.isoformat(),
        "campaigns": len(rows),
        "bep_unknown": sum(1 for r in rows if r["status"] == _STATUS_BEP_UNKNOWN),
        "correction_factor_source": factor_info.get("source"),
        "slack_sent": bool(slack_result.get("sent")),
    }
    log.info("[P7] 일일 이익 스코어카드: %s", result)
    return result
