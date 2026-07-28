# campaign_roas_lines.py — 캠페인별 기준선(목표 ROAS · 손익분기 ROAS) 해석 SA (D-NAO-105 Phase 2).
"""역할(SA·단일 책임·읽기 전용): 캠페인 목록을 받아 {target_roas, bep_roas}만 돌려준다.
판정도 문장 조립도 하지 않는다.

★왜 별도 SA로 뽑았나(P2): Phase 1에서 이 로직은 perf_today_harness 안의 비공개 함수였다.
Phase 2에서 캠페인 상세(③)의 **차트 기준선**도 같은 값을 써야 하는데, 하니스가 다른 하니스를
import하면 흐름이 얽힌다(원칙18-6). 두 벌로 계산하면 같은 캠페인의 BEP선이 화면마다 달라진다
(계획서 R3 "화면 간 숫자 불일치"가 정확히 이 모양으로 터진다) — 그래서 한 곳으로 모은다.

★우선순위는 campaign_target_resolver와 동일하다: ①캠페인 override ②상품 가중 파생
③계정 기본값. 여기서 다르게 굴면 실행 게이트(BEP 미달 증액 금지)와 화면이 어긋난다.

★성능 계약: 계정 기본값은 주문 테이블 전체 집계라 **캠페인마다 부르면 안 된다**(46캠페인 ×
전체 스캔). 호출당 1회만 구해 폴백으로 나눠 쓴다.
"""
from __future__ import annotations

from decimal import Decimal
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverProductBep
from app.services.naver_ad import campaign_target_resolver


def resolve(
    db: Session, campaign_ids: list[str], *, mapped_campaign_ids: Iterable[str] = ()
) -> dict[str, dict[str, float | None]]:
    """{campaign_id: {"target_roas": float|None, "bep_roas": float|None}}.

    mapped_campaign_ids: 상품 매핑이 있는 캠페인 집합(호출부가 today_proxy_revenue에서 구해
      넘긴다 — 이 SA가 직접 부르면 SA간 직접호출이 된다, 원칙18-6/18-8). 매핑이 없는 캠페인은
      상품 가중 파생을 시도조차 하지 않는다(쿼리 낭비 + 항상 None).

    값이 None이면 **알 수 없음**이다 — 0으로 대체하지 않는다(원칙22). 원가 미입력 상품만 있는
    계정에서는 계정 기본값 자체가 None일 수 있고, 그때 화면은 "비교할 목표치가 없다"고 말해야지
    "목표 0배"라고 말하면 안 된다.
    """
    if not campaign_ids:
        return {}

    overrides = {
        s.campaign_id: s.target_roas_override
        for s in db.query(NaverCampaignSettings).all()
        if s.target_roas_override is not None
    }
    account_target = campaign_target_resolver.account_default_target_roas(db)
    account_bep = campaign_target_resolver.account_default_bep_roas(db)
    mapped = set(mapped_campaign_ids)

    out: dict[str, dict[str, float | None]] = {}
    for cid in campaign_ids:
        target: Decimal | None = overrides.get(cid)
        bep: Decimal | None = None
        if cid in mapped:
            if target is None:
                target = campaign_target_resolver.weighted_product_value_for_campaign(
                    db, cid, NaverProductBep.target_roas
                )
            bep = campaign_target_resolver.weighted_product_value_for_campaign(
                db, cid, NaverProductBep.bep_roas
            )
        if target is None:
            target = account_target
        if bep is None:
            bep = account_bep
        out[cid] = {
            "target_roas": round(float(target), 4) if target is not None else None,
            "bep_roas": round(float(bep), 4) if bep is not None else None,
        }
    return out
