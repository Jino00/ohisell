# cold_start_bid_lane.py — Harness cold_start_bid_lane (CS 스프린트)
# 역할(Harness·원칙18-2·6): SA들을 조합해 "콜드 소재의 첫 입찰" 한 흐름을 만든다. SA끼리는 서로를
#   모른다 — 이 모듈이 SA1 출력(이익 상한)과 SA2 출력(시장가 사다리)을 SA3의 optional 파라미터로
#   유통시키고, 결과를 기존 naver_execution_harness 관문에 태운다.
#
#   SA1 bid_ceiling_calculator.compute_ceiling  → ceiling dict
#   SA2 market_bid_probe.load_today_ladder      → market dict
#   SA3 cold_start_bid_decider.decide_cold_start_bid(ceiling=…, market=…) → decision
#   → NaverProposal(status=approved, approval_source=cold_op) → naver_execution_harness.execute()
#
# ★쓰기 경로 신설 없음: 실집행은 반드시 기존 harness.execute()를 거친다(킬스위치·optimizer 하드
#   체크·쿨다운·BEP·스톱로스·일예산·일일캡 전량 그대로 적용). 이 모듈은 광고 API를 직접 부르지 않는다.
# ★예산은 건드리지 않는다(트랙 금지선) — 이 레인은 **입찰만** 만진다.
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import (
    NaverAdDaily, NaverAdgroupProduct, NaverCampaignSettings, NaverChangeLog, NaverEntity,
    NaverProposal,
)
from app.services.naver_ad import (
    bid_ceiling_calculator, cold_start_bid_decider, guardrail_params, market_bid_probe,
    naver_execution_harness,
)
from app.services.naver_ad.bid_step_types import (
    APPROVAL_SOURCE_COLD,  # 재수출 — 단일 소스는 bid_step_types(리뷰 P3-12: harness가 무의존 경로로 읽게)
    encode_cold_ceiling,
)
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP
from app.utils.kst import kst_now, kst_today

log = logging.getLogger(__name__)

# 승인원(APPROVAL_SOURCE_COLD)의 단일 소스는 bid_step_types — 위에서 재수출만 한다.
# 킬스위치 재확인·소재 UP 경계·면제 판정 대상으로 naver_execution_harness에 등록돼 있다.
PROPOSAL_TYPE_COLD = "bid_up_cold"
# change_log에서 "이 소재는 이미 CS가 발사했다"를 되찾는 rationale 접두(기존 [스파이럴복원] 관례).
RATIONALE_PREFIX = "[콜드첫입찰]"

# ── 콜드 판정 상수 ──────────────────────────────────────────────────
# 관측 창(일). 7일 = 기존 레인들의 수요 관측 창(visibility._DEMAND_WINDOW_DAYS)과 동일.
COLD_WINDOW_DAYS = 7
# 7일 노출 합계가 이 값 미만이면 "노출이 사실상 없다" = 콜드.
#   근거: visibility._EVIDENCE_MIN_DEMAND_IMP_7D(100)가 "증거 구매가 의미 있는 고수요" 하한이다.
#   그 절반(50)을 콜드 경계로 둔다 — 고수요 판정선까지 콜드로 잡으면 이미 잘 도는 그룹까지
#   CS가 건드리게 되므로 보수적으로 낮춘다.
COLD_MAX_IMP_7D = 50
# 한 회차에 발사할 최대 소재 수(라운드 캡). 첫 개방이라 보수적으로 시작한다.
MAX_PROPOSALS_PER_RUN = 5


def cold_start_dry_run(db: Session) -> bool:
    """이 레인의 dry-run 스위치 **실효값**. 도는 그 순간에 읽는다 (D-NAO-282 · 계약 P2-ⓑ).

    종전엔 스케줄러가 `os.getenv("NAVER_CS_DRY_RUN", "1") != "0"` 한 줄로 판정했다. 문제는
    두 가지였다 — ①바꾸려면 `.env` 수정 + **재시작** ②«지금 켜져 있는지»가 화면 어디에도
    안 보였다. 되돌림 스위치가 되돌리기 어렵고 보이지도 않으면 스위치가 아니다.

    ⇒ `guardrail_params` SPECS 키 `naver_cs_dry_run`으로 옮겼다. 우선순위 **DB > env > 코드
    기본값**이고, 폴백 규칙은 `get_switch` 한 곳에 있다(여기 다시 적지 않는다).

    ⚠️**env를 폐기하지 않았다.** prod `.env`에 `NAVER_CS_DRY_RUN=0`이 실재한다(2026-08-31
    실측 — 즉 이 레인은 지금 실쓰기 허용 상태다). env를 무시하고 코드 기본값(True)으로
    떨어뜨렸다면 배포 그 순간 레인이 dry-run으로 **조용히** 뒤집혔을 것이다. 「값을 옮기는 것」이
    「동작을 바꾸는 것」이 되는 자리라 층을 남긴다.
    """
    return guardrail_params.get_switch(db, "naver_cs_dry_run")


def _auto_campaigns(db: Session) -> list[str]:
    """optimizer='ours' ∧ auto_operate=1 캠페인 id 목록(이 레인의 유일한 발동 대상)."""
    return [
        r[0] for r in db.query(NaverCampaignSettings.campaign_id).filter(
            NaverCampaignSettings.optimizer == "ours",
            NaverCampaignSettings.auto_operate.is_(True),
        ).all()
    ]


def _already_fired_ad_ids(db: Session) -> set[str]:
    """CS가 **실제로 입찰을 바꾼** 소재 집합(첫 1회 제한의 상태 저장소).

    ★적대적 리뷰 P1-2 수정 — 종전 구현은 `rationale LIKE '[콜드첫입찰]%' AND dry_run=False`만
      봤는데, harness의 `_guard_failure`가 **제안 rationale을 그대로 앞에 붙여** 차단 행을
      남긴다(`"[콜드첫입찰] … [실행 불가] …"`, dry_run=False). 그래서 **가드에 막힌 시도나
      writer 예외까지 '첫 1회'를 소진**했다 — 원인을 고쳐도 그 소재는 영구히 대상에서 빠진다.
      "시도 1회"가 아니라 "성공 1회"여야 한다.

    판별을 제안 조인으로 바꾼다(텍스트 매칭보다 견고):
      NaverChangeLog.proposal_id → NaverProposal.proposal_type == 'bid_up_cold'
      ∧ dry_run=False            (dry-run 관측은 소진하지 않음 — 첫 회차는 항상 dry-run이다)
      ∧ after_value IS NOT NULL  (= 쓰기가 실제로 확정됨. 가드 차단·writer 예외 행은 NULL)
    """
    rows = db.query(NaverChangeLog.entity_id).join(
        NaverProposal, NaverProposal.id == NaverChangeLog.proposal_id,
    ).filter(
        NaverChangeLog.entity_type == "ad",
        NaverChangeLog.action == "update_bid",
        NaverChangeLog.dry_run.is_(False),
        NaverChangeLog.after_value.isnot(None),  # 실제 쓰기 확정분만
        NaverProposal.proposal_type == PROPOSAL_TYPE_COLD,
    ).all()
    return {r[0] for r in rows}


def _adgroup_imp_7d(db: Session, campaign_ids: list[str], today: date) -> dict[str, int]:
    """광고그룹별 7일 노출 합(콜드 판정용). naver_ad_daily에는 소재 grain이 없어 그룹으로 판정한다."""
    if not campaign_ids:
        return {}
    rows = db.query(
        NaverAdDaily.adgroup_id, sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0),
    ).filter(
        NaverAdDaily.ad_date >= today - timedelta(days=COLD_WINDOW_DAYS),
        NaverAdDaily.ad_date <= today - timedelta(days=1),
        NaverAdDaily.campaign_id.in_(campaign_ids),
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    ).group_by(NaverAdDaily.adgroup_id).all()
    return {r[0]: int(r[1]) for r in rows}


def _group_bids(db: Session, adgroup_ids: set[str]) -> dict[str, int]:
    """광고그룹 입찰가(naver_entity) — use_group_bid_amt=True 소재의 실효 입찰."""
    if not adgroup_ids:
        return {}
    rows = db.query(NaverEntity.entity_id, NaverEntity.bid_amt).filter(
        NaverEntity.entity_type == "adgroup",
        NaverEntity.entity_id.in_(adgroup_ids),
    ).all()
    return {r[0]: int(r[1] or 0) for r in rows}


def select_cold_ads(db: Session, today: date) -> list[dict]:
    """콜드 소재 선별.

    대상: optimizer='ours' ∧ auto_operate=1 캠페인 산하 소재(ad_id 보유·정지 아님) 중
      ① CS가 아직 실집행한 적 없고(첫 1회 제한), **그리고**
      ② (우리 change_log에 그 소재의 update_bid 실집행 이력이 없다  OR
          그 소재가 속한 그룹의 7일 노출 합 < COLD_MAX_IMP_7D)
    ②의 OR는 스프린트 지시 그대로다 — "한 번도 우리가 손대지 않은 소재"와 "손댔지만 노출이
      안 나오는 소재" 둘 다 콜드로 본다.

    반환: [{"ad_id","adgroup_id","campaign_id","current_bid","imp_7d","cold_reason"}]
    """
    campaign_ids = _auto_campaigns(db)
    if not campaign_ids:
        return []

    rows = db.query(
        NaverAdgroupProduct.ad_id, NaverAdgroupProduct.adgroup_id,
        NaverAdgroupProduct.campaign_id, NaverAdgroupProduct.ad_bid_amt,
        NaverAdgroupProduct.use_group_bid_amt, NaverAdgroupProduct.ad_user_lock,
    ).filter(
        NaverAdgroupProduct.campaign_id.in_(campaign_ids),
        NaverAdgroupProduct.ad_id.isnot(None),
    ).all()
    if not rows:
        return []

    fired = _already_fired_ad_ids(db)
    imp7 = _adgroup_imp_7d(db, campaign_ids, today)
    gbids = _group_bids(db, {r[1] for r in rows})
    # 우리가 소재 grain으로 입찰을 **실제로 바꾼** 이력(레인 무관 — CS 이외 경로 포함).
    # after_value IS NOT NULL = 쓰기 확정분만(리뷰 P1-2와 같은 이유 — 가드 차단·writer 예외 행이
    # "우리가 손댔다"로 오인되면 콜드가 아닌 것으로 잘못 판정된다).
    touched = {
        r[0] for r in db.query(NaverChangeLog.entity_id).filter(
            NaverChangeLog.entity_type == "ad",
            NaverChangeLog.action == "update_bid",
            NaverChangeLog.dry_run.is_(False),
            NaverChangeLog.after_value.isnot(None),
        ).all()
    }

    out: list[dict] = []
    for ad_id, adgroup_id, campaign_id, ad_bid, use_group, user_lock in rows:
        if user_lock is True:
            continue  # 정지 소재는 서빙하지 않음 — 입찰을 올릴 이유가 없다
        if ad_id in fired:
            continue  # 첫 1회 제한(이미 CS가 발사함)
        imp = imp7.get(adgroup_id, 0)
        never_touched = ad_id not in touched
        low_imp = imp < COLD_MAX_IMP_7D
        if not (never_touched or low_imp):
            continue
        current_bid = gbids.get(adgroup_id, 0) if use_group is True else int(ad_bid or 0)
        reasons = []
        if never_touched:
            reasons.append("우리 입찰 실집행 이력 없음")
        if low_imp:
            reasons.append(f"7일 노출 {imp}<{COLD_MAX_IMP_7D}")
        out.append({
            "ad_id": ad_id, "adgroup_id": adgroup_id, "campaign_id": campaign_id,
            "current_bid": current_bid, "imp_7d": imp, "cold_reason": "·".join(reasons),
        })
    return out


def run_cold_start_lane(
    db: Session, *, dry_run: bool = True, now: datetime | None = None,
    today: date | None = None, device: str = cold_start_bid_decider.DEFAULT_DEVICE,
    target_position: int = cold_start_bid_decider.DEFAULT_TARGET_POSITION,
    max_proposals: int = MAX_PROPOSALS_PER_RUN,
) -> dict:
    """CS 레인 1회차.

    dry_run=True(기본): 제안값을 산출·로그만 하고 **쓰기 없음**. 배포 후 첫 회차는 반드시 이 모드로
      돌려 제안값을 눈으로 확인한 뒤 실집행으로 전환한다(스프린트 지시 절차).
    dry_run=False: NaverProposal(approved) 생성 → naver_execution_harness.execute(dry_run=False).

    반환: {"candidates", "proposed", "executed", "failed", "not_viable", "held", "rows": [...]}
      rows = 소재별 판정 상세(브리핑·증거용 — dry-run 관측의 산출물).
    """
    now = now or kst_now()
    today = today or kst_today()
    result: dict = {
        "candidates": 0, "proposed": 0, "executed": 0, "failed": 0,
        "not_viable": 0, "held": 0, "dry_run": dry_run, "rows": [],
    }

    cold = select_cold_ads(db, today)
    result["candidates"] = len(cold)
    if not cold:
        log.info("cold_start_bid_lane: 콜드 소재 없음 — 종료")
        return result

    attempts = 0  # 라운드 캡 카운터(성공이 아니라 **시도** 기준 — 리뷰 P1-4)

    for c in cold:
        ceiling = bid_ceiling_calculator.compute_ceiling(
            db, c["ad_id"], c["adgroup_id"], c["campaign_id"], today
        )
        market = market_bid_probe.load_today_ladder(db, c["ad_id"], device, today)
        decision = cold_start_bid_decider.decide_cold_start_bid(
            ceiling=ceiling, market=market, current_bid=c["current_bid"],
            target_position=target_position, device=device,
        )
        row = {**c, **{k: decision[k] for k in (
            "decision", "target_bid", "reason", "ceiling_cpc", "market_bid", "ladder_min",
            "exposure_min", "rpc_source", "sample_clk", "confident",
        )}}
        result["rows"].append(row)

        if decision["decision"] == cold_start_bid_decider.DECISION_NOT_VIABLE:
            result["not_viable"] += 1
            log.warning(
                "cold_start_bid_lane: [경제성없음] ad=%s grp=%s — %s",
                c["ad_id"], c["adgroup_id"], decision["reason"],
            )
            continue
        if decision["decision"] != cold_start_bid_decider.DECISION_PROPOSE:
            result["held"] += 1
            log.info("cold_start_bid_lane: [보류] ad=%s — %s", c["ad_id"], decision["reason"])
            continue

        result["proposed"] += 1
        log.info(
            "cold_start_bid_lane: [제안] ad=%s %d원→%d원 — %s",
            c["ad_id"], c["current_bid"], decision["target_bid"], decision["reason"],
        )
        if dry_run:
            continue  # 관측만 — 제안 레코드도 만들지 않는다(dry-run은 순수 관측)
        # ★리뷰 P1-4: 라운드 캡은 **시도** 기준이어야 한다. 종전엔 executed(성공)를 셌는데,
        #   집행이 실패하면 카운터가 안 올라 캡이 영원히 안 걸렸다 — 가드가 전건을 막는 상황
        #   (리뷰 P1-1 같은)에서 콜드 소재 전량에 제안·change_log·첫1회 소진이 한 회차에 발생.
        if attempts >= max_proposals:
            result["held"] += 1
            log.info("cold_start_bid_lane: 라운드 캡(%d 시도) 도달 — 나머지 이월", max_proposals)
            continue
        attempts += 1

        proposal = NaverProposal(
            proposal_type=PROPOSAL_TYPE_COLD, target_type="ad", target_id=c["ad_id"],
            campaign_id=c["campaign_id"], adgroup_id=c["adgroup_id"],
            rationale=f"{RATIONALE_PREFIX} {decision['reason']}",
            # ★리뷰 P1-3/P2-B: 상한을 기계판독 마커로 실어 보낸다. bid_up_cold는 ±15% 완전
            #   면제라 이 상한이 유일한 가격 브레이크이고, harness가 쓰기 직전 이 마커로
            #   재검증한다(레인 계산을 불신하는 규약 — 탐색 explore_ceiling과 동형).
            #   ★마커에 담는 값은 **경제 상한(ceiling_cpc)** 이지 target_bid가 아니다.
            #     첫 판은 target_bid를 담았는데, target_bid = min(상한, 시장가)이므로 경계 검사
            #     `target_bid > ceiling`이 `X > X` = **항상 False**인 동어반복이었다(리뷰 P2-B).
            #     그러면 게이트가 실제로 거르는 것은 "마커 유무"뿐이고 레인의 클램프는 아무것도
            #     재검증되지 않는다. 상한을 담아야 "레인이 상한을 제대로 씌웠나"가 실질 단언이 된다
            #     (탐색도 `min(target, ceiling)` **뒤에** ceiling을 담는다 — 서로 다른 양).
            expected_effect=encode_cold_ceiling(
                "콜드 소재 첫 입찰 — 눈먼 래더(+10%/BM 프라이어) 대신 npla-estimate 실측 시장가와 "
                "이익 상한 중 낮은 쪽으로 직행. 되돌림=기존 스톱로스/BEP/손실고삐 백스톱, "
                "이후 입찰은 기존 레인(순위 서보·탐색UP)이 인수.",
                decision["ceiling_cpc"],
            ),
            status="pending", target_bid=decision["target_bid"],
        )
        db.add(proposal)
        db.flush()
        # D-NAO-244 스코프 검사 — 승인은 auto_operator.engine_approve 단일 문으로만(지연 import:
        # 순환 회피, 이 파일의 harness import 관례와 동형). 종전엔 스코프 밖 소재도 approved로
        # 커밋된 뒤 harness가 거부해 죽은 카드가 됐다(prod 실측 cold_op 4건).
        from app.services.naver_ad.auto_operator import engine_approve

        if not engine_approve(db, proposal, source=APPROVAL_SOURCE_COLD, now=now):
            result["held"] += 1
            continue
        try:
            naver_execution_harness.execute(db, proposal.id, dry_run=False, now=now)
            result["executed"] += 1
        except Exception as e:  # noqa: BLE001 — harness가 change_log/상태를 이미 확정
            result["failed"] += 1
            log.warning("cold_start_bid_lane: 실행 실패 proposal_id=%s: %s", proposal.id, e)

    log.info(
        "cold_start_bid_lane 완료(dry_run=%s): 후보 %d·제안 %d·집행 %d·실패 %d·경제성없음 %d·보류 %d",
        dry_run, result["candidates"], result["proposed"], result["executed"],
        result["failed"], result["not_viable"], result["held"],
    )
    return result


def collect_market_bids_daily(db: Session, today: date | None = None) -> dict:
    """일 1회 시장가 사다리 수집(SA2 배선) — 자동운영 캠페인 산하 소재 전량.

    기존 일별 수집 크론 안에서 호출된다. **예외를 밖으로 던지지 않는다**(호출부도 try/except로
    한 번 더 감싼다) — 이 수집이 실패해도 다른 수집이 죽으면 안 된다.
    """
    today = today or kst_today()
    try:
        campaign_ids = _auto_campaigns(db)
        if not campaign_ids:
            return {"ads": 0, "rows": 0, "floor_ads": 0, "devices": []}
        rows = db.query(
            NaverAdgroupProduct.ad_id, NaverAdgroupProduct.adgroup_id,
            NaverAdgroupProduct.campaign_id,
        ).filter(
            NaverAdgroupProduct.campaign_id.in_(campaign_ids),
            NaverAdgroupProduct.ad_id.isnot(None),
        ).all()
        return market_bid_probe.collect_daily(db, [tuple(r) for r in rows], today)
    except Exception as e:  # noqa: BLE001 — 격리(기존 수집 무영향)
        log.warning("cold_start_bid_lane: 시장가 수집 실패(격리, 기존 수집 무영향): %s", e)
        db.rollback()
        return {"ads": 0, "rows": 0, "floor_ads": 0, "devices": [], "error": str(e)}
