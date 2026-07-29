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

    ★P1-1(D-NAO-104) additive 확장: `auto_operate`·`status_reason` 2개를 추가로 싣는다.
    기존 키는 하나도 바뀌지 않으므로 기존 소비자(커맨드 센터 `/campaigns`)는 무영향이다.
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

    settings_rows = db.query(NaverCampaignSettings).all()
    optimizer_by_campaign = {s.campaign_id: s.optimizer for s in settings_rows}
    # P1-1(D-NAO-104): 성과 뷰가 "우리가 자동으로 돌리는가"를 말하려면 optimizer만으로는
    # 부족하다 — optimizer='ours'인데 auto_operate=0인 상태가 실재한다(D-NAO-92의 03이
    # 정확히 그 모양이었다: 우리 소유인데 자동 레인은 정지). 설정 행이 없으면 False
    # (auto_operate의 모델 기본값과 동일 시맨틱 — 없는 것은 꺼진 것).
    auto_operate_by_campaign = {s.campaign_id: bool(s.auto_operate) for s in settings_rows}
    # UI2(D-NAO-65): loss 대응 정책. NULL/미설정은 그대로 None으로 실어 보낸다 —
    # 프론트가 '기본(고삐)'로 해석한다(전역 기본값 leash 불변식, 여기서 임의 정규화 금지:
    # 화면은 '미설정'과 '명시 leash'를 구분할 필요가 없고, NULL=leash 정규화는 쓰기 경로
    # (PUT /campaign-settings/loss-policy)의 책임이지 조회 SA가 할 일이 아니다).
    loss_policy_by_campaign = {s.campaign_id: s.loss_policy for s in settings_rows}

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
            # D-NAO-97 statusReason 원문. status(on/off)는 사람의 On/Off 스위치만 반영하므로
            # "켜져 있는데 왜 안 도는가"(예산 소진·상위 캠페인 OFF·검수 중)는 이 필드에만 있다.
            # 한글화는 소비자(성과 뷰) 몫 — 명부는 원문을 그대로 실어 보낸다(번역 레이어 단일화).
            "status_reason": c.status_reason,
            "cost": cost,
            "clk": int(p.clk or 0) if p else 0,
            "conv_amt": conv_amt,
            "roas_naver": round(conv_amt / cost, 4) if cost else None,
            "optimizer": optimizer_by_campaign.get(c.entity_id, "none"),
            "auto_operate": auto_operate_by_campaign.get(c.entity_id, False),
            "loss_policy": loss_policy_by_campaign.get(c.entity_id),  # NULL=콘솔이 '기본(고삐)'로 해석
            "window_days": days,
        })

    rows.sort(key=lambda r: r["cost"], reverse=True)
    return rows
