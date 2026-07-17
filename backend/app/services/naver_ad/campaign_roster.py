# campaign_roster.py — 캠페인 명부 SA (D-NAO-48). 단일 책임: "우리 계정의 캠페인은 뭐가
# 있고, 각각 누가(우리/원본MOP/수동) 돌리며, 최근 성과는 어떤가"를 한 번에 답한다.
#
# 왜 필요한가(실측): 관리주체 스위치를 광고 옆에 달려면 **캠페인 이름**이 있어야 하는데
# API 어디에도 없었다 — 콘솔은 report(grain=campaign)+campaign-settings+diagnosis 3-call을
# 병합하는데 report가 이름을 주지 않아 화면에 `cmp-a001-02-000000008492582`가 그대로
# 노출된다(MOP UX 리뷰에서 "베끼면 안 되는 것"으로 꼽은 내부 ID 노출을 우리가 하고 있었음).
# 이름·광고종류·상태는 naver_entity에 있다.
#
# 쓰기 없음(읽기 전용). 관리주체 변경은 기존 PUT /campaign-settings가 유일 경로이고
# 실행 게이트는 naver_execution_harness의 optimizer=='ours' 하드체크다(D-NAO-13).
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverCampaignSettings, NaverEntity
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_today

DEFAULT_WINDOW_DAYS = 30


def build(db: Session, *, days: int = DEFAULT_WINDOW_DAYS, today: date | None = None) -> list[dict]:
    """캠페인 명부 — entity(이름·종류·상태) ⨝ ad_daily(최근 N일 성과) ⨝ settings(관리주체).

    창 관례(다른 SA와 동일 — 어긋나면 화면끼리 숫자가 안 맞는다):
      · **오늘(D-0) 제외**: naver_ad_daily가 아직 확정 적재 전일 수 있다. ad_report·
        diagnosis·dashboard_overview._optimizer_coverage 전부 D-1 확정치 기준.
      · **BACKFILL_SENTINEL_ADGROUP 제외**: campaign_backfill이 심는 그룹 롤업 행이라
        실단위 행과 같이 합치면 **광고비가 이중집계**된다(2배 함정 — proposal_pipeline·
        account_diagnosis 등도 동일하게 제외).
      · **설정 행이 없으면 optimizer='none'**: naver_execution_harness._resolve_optimizer와
        동일 시맨틱. 여기서 다르게 굴면 화면이 "우리가 돌린다"는데 실행 게이트는 막는
        모순이 생긴다(단일 진실).

    광고비 0인 캠페인도 포함한다 — 0은 '없는 것'이 아니다. 정지됐거나 아직 집행 전인
    캠페인에도 관리주체를 지정할 수 있어야 카나리를 확대한다(D-47-h와 같은 정신).
    roas_naver는 광고비 0이면 **None**(0.0이 아니다 — '알 수 없음'이지 'ROAS 0배'가 아님).
    """
    today = today or kst_today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)

    perf = {
        r.campaign_id: r
        for r in db.query(
            NaverAdDaily.campaign_id.label("campaign_id"),
            func.sum(NaverAdDaily.cost).label("cost"),
            func.sum(NaverAdDaily.clk).label("clk"),
            func.sum(NaverAdDaily.conv_direct_amt).label("conv_direct_amt"),
            func.sum(NaverAdDaily.conv_indirect_amt).label("conv_indirect_amt"),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .group_by(NaverAdDaily.campaign_id)
        .all()
    }

    optimizer_by_campaign = {
        s.campaign_id: s.optimizer for s in db.query(NaverCampaignSettings).all()
    }

    rows: list[dict] = []
    campaigns = (
        db.query(NaverEntity)
        .filter(NaverEntity.entity_type == "campaign", NaverEntity.status != "deleted")
        .all()
    )
    for c in campaigns:
        p = perf.get(c.entity_id)
        cost = int(p.cost or 0) if p else 0
        # 네이버 기준 ROAS = (직접+간접 전환매출)/광고비 — D-NAO-7의 1열과 동일 정의.
        # (직접만·실주문 대조 2열은 이 명부의 책임이 아니다 — ad_report가 3열을 담당.)
        conv_amt = (int(p.conv_direct_amt or 0) + int(p.conv_indirect_amt or 0)) if p else 0
        rows.append({
            "campaign_id": c.entity_id,
            "name": c.name,
            "campaign_type": c.campaign_type,
            "status": c.status,
            "cost": cost,
            "clk": int(p.clk or 0) if p else 0,
            "conv_amt": conv_amt,
            "roas_naver": round(conv_amt / cost, 4) if cost else None,
            "optimizer": optimizer_by_campaign.get(c.entity_id, "none"),
            "window_days": days,
        })

    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows
