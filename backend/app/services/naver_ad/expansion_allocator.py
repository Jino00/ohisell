# expansion_allocator.py — 확장 압력 그룹 배분 순수 SA (스프린트 EX, D-NAO-85 §4-2 "배분")
# 역할(SA·순수·DB 읽기만): 확장 모드 캠페인(expansion_pressure 판정 통과)의 활성 쇼핑 애드그룹을
#   3층 우선순위로 랭킹하고, 그룹별 자기 증거(정착창 보정ROAS)로 채택/제외를 판정하며, 한계 정지
#   근사(marginal_stop)를 부착해 캠페인당 상위 EX_DAILY_GROUP_CAP 그룹을 낸다. **판정·랭킹만** 하고
#   실행/제안 생성은 하지 않는다(집행=Phase 3 harness가 기존 bid_up 파이프라인 재사용).
#
#   ★SA간 직접 호출 금지(원칙18-6): 이 SA는 expansion_pressure를 import하지 않는다. Phase 3
#     harness(proposal_pipeline)가 expansion_pressure.judge_campaign_pressure 출력(pressure dict)을
#     이 SA의 `pressure` 파라미터로 전달한다(허브가 SA 출력을 다른 SA 입력으로 중계, 원칙18-8).
#     response_priors(bid_rank_curve.load_response_priors 출력)도 harness가 전달한다.
#   ★import 정책: models + 최말단 순수 SA(gave_score) + diagnosis(correction_factor) + 동급 관측 SA
#     (visibility.evidence_window·ctr_alert.detect_ctr_alerts)만 import한다. auto_operator/
#     expansion_pressure는 import하지 않는다(순환·직접호출 금지). 정착창·밴드·표본 상수는 로컬
#     복제하고 출처를 명시하며, test_naver_expansion.py가 exploration/auto_operator 원본과의 정합을
#     별도로 고정한다(exploration 정합 테스트 관례 미러).
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverEntity, NaverKeywordHourly
from app.services.naver_ad import ctr_alert, diagnosis, gave_score, visibility
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

# ── 정착창 상수(출처: auto_operator._SETTLEMENT_WINDOW_START_DAYS/_END_DAYS = 8/2) ──
# expansion_pressure와 동일 창(캠페인 갭과 그룹 자기증거를 같은 창에서 판정 — 판정 기준 정합).
# 로컬 복제·테스트 고정(exploration 관례 미러).
_SETTLEMENT_WINDOW_START_DAYS = 8  # 출처: auto_operator._SETTLEMENT_WINDOW_START_DAYS
_SETTLEMENT_WINDOW_END_DAYS = 2    # 출처: auto_operator._SETTLEMENT_WINDOW_END_DAYS
# 수요 관측 창(일) — 7일 [오늘-7, 오늘-1](완결일만). visibility._DEMAND_WINDOW_DAYS(7)와 동일.
_DEMAND_WINDOW_DAYS = 7

# ── 배분 상수(PLAN §4-2 확정) ──
# 캠페인당 하루 확장 발사 그룹 상한(출처: auto_operator._VITALITY_DAILY_CAP=5 관례 미러).
EX_DAILY_GROUP_CAP = 5
# 2층(고수요·밴드밖) 판별 7일 노출 하한(출처: visibility._EVIDENCE_MIN_DEMAND_IMP_7D=100·VF 가시 임계).
EX_HIGH_DEMAND_IMP = 100
# 자기 증거 표본 하한(출처: exploration._MIN_CLICK_FOR_EXPLORATION=10 — 핫셋/정착 ROAS 판정 경계).
# 로컬 복제·테스트 고정.
_OWN_SAMPLE_CLK = 10
# 이익 스팟 밴드(출처: exploration._EXPLORATION_BAND_LOW/HIGH = 2.5/4.0). 로컬 복제·테스트 고정.
_BAND_LOW = Decimal("2.5")   # 과열 경계(진입 금지) — 밴드 상단
_BAND_HIGH = Decimal("4.0")  # 밴드 하단(진입 목표)
# ── 한계 정지 근사(marginal_stop, D-NAO-59 꼭짓점) — ★보수 프록시(정밀 한계ROAS 산출 아님) ──
# 설계 결정(PLAN §4-2 "확신 없으면 보수 프록시로 단순화·근사임을 명시"): bid_rank_curve의
# bid_rank_slope는 "원/rank개선 1.0"(양수) 단위라, 이것만으로 그룹의 한계 ROAS(입찰 1원 추가 시
# Δ매출/Δ비용)를 정밀 산출하려면 rank→CTR→전환 경제까지 필요한데 그 재료가 콜드 그룹엔 없다.
# 추정을 정밀인 척 포장하지 않고 **보수 근사**를 쓴다: "slope 프라이어가 존재(=이미 여러 번
# 스텝됐고 rank 반응을 학습 중) ∧ 자기 보정ROAS가 BEP에 근접(bep×EX_MARGINAL_STOP_RATIO 미만)"이면
# 추가 입찰(=CPC 상승)이 한계 ROAS를 BEP 아래로 밀 위험이 커진 꼭짓점 근처로 보고 marginal_stop=True.
# slope 프라이어가 없으면 정지 판정 불가 → marginal_stop=False(관측이 목적 — 배제하지 않고 스텝은
# 기존 래더 클램프에 맡김, §4-2). 자기 ROAS 미측정(표본 미달)도 marginal_stop 불가 → False.
EX_MARGINAL_STOP_RATIO = Decimal("1.1")


def _settlement_window(today: date) -> tuple[date, date]:
    """정착창 [오늘-8, 오늘-2] — auto_operator._settlement_window 로컬 복제(정합은 테스트 고정)."""
    return (
        today - timedelta(days=_SETTLEMENT_WINDOW_START_DAYS),
        today - timedelta(days=_SETTLEMENT_WINDOW_END_DAYS),
    )


def _group_settlement_agg(db: Session, adgroup_id: str, date_from: date, date_to: date) -> dict:
    """정착창 내 광고그룹 grain (clk, cost, conv_amt) 집계 — auto_operator._settlement_agg의
    adgroup grain 집계를 그대로 복제한다(conv_amt=직+간접, sentinel 행 제외 = 2배 집계 방지)."""
    clk, cost, direct, indirect = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.cost), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
            sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_indirect_amt), 0),
        )
        .filter(
            NaverAdDaily.ad_date >= date_from,
            NaverAdDaily.ad_date <= date_to,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    return {"clk": int(clk), "cost": int(cost), "conv_amt": int(direct) + int(indirect)}


def _weighted_rank_7d(db: Session, adgroup_id: str, today: date) -> Decimal | None:
    """최근 7일 [오늘-7, 오늘-1] imp-가중 avg_rank(NaverKeywordHourly, 쇼핑 그룹 grain).
    exploration._exploration_weighted_rank의 imp-가중 규약을 복제한다 — avg_rank NULL 버킷은
    쿼리에서 제외되고, 관측 버킷 imp 합이 0이면 None(관측 근거 없음)."""
    win_from = today - timedelta(days=_DEMAND_WINDOW_DAYS)
    win_to = today - timedelta(days=1)
    rows = (
        db.query(NaverKeywordHourly.avg_rank, NaverKeywordHourly.imp)
        .filter(
            NaverKeywordHourly.entity_id == adgroup_id,
            NaverKeywordHourly.ad_date >= win_from,
            NaverKeywordHourly.ad_date <= win_to,
            NaverKeywordHourly.avg_rank.isnot(None),
        )
        .all()
    )
    rank_imp = sum(int(imp) for _r, imp in rows)
    if rank_imp <= 0:
        return None
    weighted = sum(Decimal(str(r)) * int(imp) for r, imp in rows)
    return weighted / Decimal(rank_imp)


def _demand_imp_7d(db: Session, adgroup_id: str, today: date) -> int:
    """최근 7일 [오늘-7, 오늘-1] 노출 합(NaverAdDaily, sentinel 제외) — 2층 고수요 판별용
    (visibility.evidence_window의 demand_imp_7d 산출과 동일 소스·창)."""
    win_from = today - timedelta(days=_DEMAND_WINDOW_DAYS)
    win_to = today - timedelta(days=1)
    (imp_sum,) = (
        db.query(sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.imp), 0))
        .filter(
            NaverAdDaily.ad_date >= win_from,
            NaverAdDaily.ad_date <= win_to,
            NaverAdDaily.adgroup_id == adgroup_id,
            NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
        )
        .one()
    )
    return int(imp_sum)


def _active_shopping_adgroups(db: Session, campaign_id: str) -> list[str]:
    """캠페인의 활성(on) 쇼핑 애드그룹 id(오름차순) — exploration_candidates의 base 필터 재현
    (캠페인 on∧SHOPPING → 그 소속 adgroup on∧SHOPPING). 캠페인 행 부재/off/비SHOPPING → []
    (fail-closed·방어적 타입 체크)."""
    campaign_row = (
        db.query(NaverEntity)
        .filter(
            NaverEntity.entity_type == "campaign",
            NaverEntity.entity_id == campaign_id,
            NaverEntity.status == "on",
        )
        .first()
    )
    if campaign_row is None or (campaign_row.campaign_type or "") != "SHOPPING":
        return []
    rows = (
        db.query(NaverEntity.entity_id)
        .filter(
            NaverEntity.campaign_id == campaign_id,
            NaverEntity.entity_type == "adgroup",
            NaverEntity.status == "on",
            NaverEntity.campaign_type == "SHOPPING",
        )
        .order_by(NaverEntity.entity_id.asc())
        .all()
    )
    return [r[0] for r in rows]


def _classify_tier(
    db: Session, adgroup_id: str, campaign_id: str, today: date,
    weighted_rank: Decimal | None, imp_7d: int, own_clk: int,
) -> tuple[int | None, str]:
    """그룹을 3층 우선순위로 분류(PLAN §4-2 확정 순서) — (tier|None, 사유). None=어느 층에도
    안 들어 후보에서 제외. 층 판정은 위→아래 우선(1층이면 3층 조건 무관).
      1층(밴드내+순위 여유): 2.5 < weighted_rank ≤ 4.0 — 2.5 방향 이동 여지.
      2층(고수요·밴드밖): weighted_rank **관측됨** ∧ > 4.0 ∧ 7일 노출 ≥ EX_HIGH_DEMAND_IMP.
      3층(증거창): visibility.evidence_window 활성(고수요·표본미달·예산여유·클릭미달).
    ★순위 미상(weighted_rank None — 시간별 관측 부재)은 2층이 아니다: "밴드 밖(rank>4.0)"은
      순위를 실제로 관측했다는 근거가 있어야 한다(§4-2 "∧ rank > 4.0"). 순위 미상·고수요·표본미달
      그룹은 3층 증거창이 담당한다(관측 근거 없이 밴드밖 단정 금지 — 두 층의 역할 분리).
    ★evidence_window는 3층 후보에서만 조회(1·2층이면 미조회 — 불필요 DB 접근 회피)."""
    if weighted_rank is not None and _BAND_LOW < weighted_rank <= _BAND_HIGH:
        return 1, f"1층(밴드내 순위여유, 순위 {float(weighted_rank):.2f}∈({_BAND_LOW},{_BAND_HIGH}])"
    if weighted_rank is not None and weighted_rank > _BAND_HIGH and imp_7d >= EX_HIGH_DEMAND_IMP:
        return 2, f"2층(고수요·밴드밖, 순위 {float(weighted_rank):.2f}>{_BAND_HIGH}·7일노출 {imp_7d}≥{EX_HIGH_DEMAND_IMP})"
    # ★과열밴드 fail-closed(codex P2-1): 순위가 **관측됐고** ≤ 2.5(과열 경계)면 증거창 평가 전에
    # 후보 제외한다. exploration 밴드 의미론(rank≤2.5 = 과열밴드 진입 금지·상향 여지 없음)과 정합 —
    # 이 fail-closed가 없으면 과열 그룹이 표본미달·고수요 조건만으로 evidence tier로 흘러 채택돼
    # "상향 여지 없는 자리를 더 밀어올리는" 모순이 생긴다. 순위 미상(None)은 과열 단정 불가 →
    # 아래 증거창이 판정(관측 근거 없이 과열 배제도 안 함).
    if weighted_rank is not None and weighted_rank <= _BAND_LOW:
        return None, f"후보 제외 — 과열밴드(순위 {float(weighted_rank):.2f}≤{_BAND_LOW}, 상향 여지 없음·fail-closed)"
    ev = visibility.evidence_window(db, adgroup_id, campaign_id, today, settlement_clk=own_clk)
    if ev["active"]:
        return 3, f"3층(증거창 활성 — {ev['reason']})"
    return None, "후보 제외 — 어느 층에도 미해당(밴드밖·저수요·증거창 비활성)"


def allocate_expansion(
    db: Session, campaign_id: str, *, today: date, pressure: dict,
    response_priors: dict | None = None,
) -> list[dict]:
    """확장 압력을 그룹에 배분(순수·판정/랭킹만, PLAN §4-2). 반환: 상위 EX_DAILY_GROUP_CAP개
      [{adgroup_id, rank_tier(1|2|3), weighted_rank: Decimal|None, own_clk: int,
        judged_by("own"|"campaign_prior"), marginal_stop: bool, reason: str}, ...].

    입력 pressure = expansion_pressure.judge_campaign_pressure 출력(harness가 전달, 원칙18-8).
      expansion_mode=False면 [](배분 없음). bep_roas는 pressure에서 상속(재해석 안 함 — 판정
      기준 정합). response_priors = bid_rank_curve.load_response_priors 출력(harness 전달, optional).

    파이프라인:
      1. 후보 = 활성 쇼핑 애드그룹. CTR 경보 그룹(ctr_alert.detect_ctr_alerts)은 제외 —
         소재 문제 그룹에 UP 무의미(§6 04 사례). ★택1: DB 재조회로 여기서 제외(출력에 노출 아님).
      2. 3층 랭킹(_classify_tier). 어느 층에도 안 들면 제외.
      3. 그룹 판정: 자기 정착창 clk ≥ _OWN_SAMPLE_CLK ∧ cost>0 → 자기 보정ROAS ≥ BEP면 채택
         (judged_by="own"), < BEP면 **제외**(자기 증거의 반박 존중). 표본 미달 → 캠페인 프라이어로
         채택(judged_by="campaign_prior" — pressure가 이미 갭 게이트를 통과했으므로 캠페인 여유 상속.
         ★hierarchical_pooling.shrink 대신 명시적 폴백 분기 채택 — 자기 표본이 얕아(clk<10) 축소
         추정할 raw ROAS 자체가 신뢰 불가하므로 과설계를 피하고 캠페인 여유를 직접 상속, §4-2 허용).
      4. marginal_stop 근사(위 EX_MARGINAL_STOP_RATIO 주석의 보수 프록시).
      5. 랭킹 정렬: 층 오름차순 → 층 내부 정착창 conv_amt 내림차순(볼륨 우선·D-NAO-59 정합) →
         동률 adgroup_id 오름차순(결정성) → 상위 EX_DAILY_GROUP_CAP 컷.
    """
    if not pressure or not pressure.get("expansion_mode"):
        return []
    bep_roas = pressure.get("bep_roas")
    if bep_roas is None or Decimal(str(bep_roas)) <= 0:
        return []  # 방어 — 확장 모드면 BEP 존재하나 fail-closed(분모 없음)
    bep_roas = Decimal(str(bep_roas))
    response_priors = response_priors or {}

    window_from, window_to = _settlement_window(today)

    # CTR 경보 그룹(캠페인 1회 조회) — 소재 처방 대상은 확장 후보에서 제외.
    # ★판정 시각 정합(codex P2-2): detect_ctr_alerts는 now.date()−1을 as_of로 삼으므로(기본값
    # kst_now()면 allocate_expansion(today=…)과 어긋나 재현 비결정) now에 today 기반 시각을 명시
    # 전달한다 — now.date()==today면 ctr as_of = today−1로, 이 배분의 정착창/수요창(as_of=today−1)과
    # 동일 완결일을 본다(재현 결정성).
    ctr_now = datetime.combine(today, datetime.min.time())
    ctr_alerted = {
        a["adgroup_id"]
        for a in ctr_alert.detect_ctr_alerts(db, campaign_id, now=ctr_now).get("alerts", [])
    }

    # 보정계수(자기 그룹 보정ROAS 판정용) — 확장 모드면 actual_revenue_ratio 보장(pressure 게이트②)
    # 이지만, 폴백 대비 명시적으로 확인한다. 부재면 factor=None → 전 그룹 campaign_prior 상속.
    factor_info = diagnosis.correction_factor(db, today - timedelta(days=1))
    factor = (
        Decimal(str(factor_info["factor"]))
        if factor_info.get("source") == "actual_revenue_ratio" else None
    )

    records: list[dict] = []
    for adgroup_id in _active_shopping_adgroups(db, campaign_id):
        if adgroup_id in ctr_alerted:
            continue  # 소재 문제 그룹 제외(UP 무의미)

        agg = _group_settlement_agg(db, adgroup_id, window_from, window_to)
        own_clk = agg["clk"]
        weighted_rank = _weighted_rank_7d(db, adgroup_id, today)
        imp_7d = _demand_imp_7d(db, adgroup_id, today)

        tier, tier_reason = _classify_tier(
            db, adgroup_id, campaign_id, today, weighted_rank, imp_7d, own_clk,
        )
        if tier is None:
            continue

        # 그룹 판정 — 자기 증거(clk≥10∧cost>0) vs 캠페인 프라이어 상속.
        own_ratio: Decimal | None = None
        if own_clk >= _OWN_SAMPLE_CLK and agg["cost"] > 0:
            # ★자기 표본 충분 fail-closed(codex P2-3): 자기 보정ROAS로 채택/반박을 판정해야 하는데
            # 보정계수가 unavailable(factor None)이면 검증 불가 → **제외**한다. campaign_prior로
            # 폴백하면 below-BEP 제외 게이트를 우회(검증 불가인데 확장)하는 구멍이 된다. 표본 미달
            # 그룹의 prior 폴백은 pressure 게이트②가 이미 계정 보정계수를 검증한 문맥이라 안전하지만,
            # 자기 표본이 있는 그룹은 그 자기 증거를 검증할 보정계수가 없으면 확장을 금지한다(fail-closed).
            if factor is None:
                continue
            scored = gave_score.compute_gave_score(
                revenue=Decimal(agg["conv_amt"]) * factor, cost=agg["cost"], bep_roas=bep_roas,
            )
            own_ratio = scored["roas_ratio"]  # = 자기 보정ROAS / BEP
            if own_ratio is not None and own_ratio < Decimal(1):
                continue  # 자기 보정ROAS < BEP — 자기 증거의 반박 존중, 제외
            judged_by = "own"
            judge_reason = f"자기 채택(정착 clk {own_clk}·보정ROAS/BEP {own_ratio})"
        else:
            judged_by = "campaign_prior"
            judge_reason = (
                f"캠페인 프라이어 상속(표본 미달 clk {own_clk}<{_OWN_SAMPLE_CLK} — "
                f"캠페인 갭 {pressure.get('roas_ratio')} 상속)"
            )

        # marginal_stop 보수 근사 — 자기 ROAS 측정 가능(own)이고 slope 프라이어 존재 ∧ BEP 근접일 때만.
        marginal_stop = False
        if judged_by == "own" and own_ratio is not None:
            slope = response_priors.get(f"adgroup:{adgroup_id}")
            if slope is not None and own_ratio < EX_MARGINAL_STOP_RATIO:
                marginal_stop = True
                judge_reason += (
                    f" · marginal_stop(slope 프라이어 존재∧자기ROAS BEP 근접<{EX_MARGINAL_STOP_RATIO}"
                    " — 한계 정지 근사)"
                )

        records.append({
            "adgroup_id": adgroup_id,
            "rank_tier": tier,
            "weighted_rank": weighted_rank,
            "own_clk": own_clk,
            "judged_by": judged_by,
            "marginal_stop": marginal_stop,
            "reason": f"{tier_reason} · {judge_reason}",
            "_conv_amt": agg["conv_amt"],  # 정렬 전용(반환 전 제거)
        })

    # 층 오름차순 → 층 내부 conv_amt 내림차순(볼륨 우선) → 동률 adgroup_id 오름차순(결정성).
    records.sort(key=lambda r: (r["rank_tier"], -r["_conv_amt"], r["adgroup_id"]))
    out = records[:EX_DAILY_GROUP_CAP]
    for r in out:
        r.pop("_conv_amt", None)
    return out
