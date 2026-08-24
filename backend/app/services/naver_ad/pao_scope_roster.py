# pao_scope_roster.py — PAO 스코프 대시보드 하니스 (D-NAO-244)
#
# Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
# 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
#
# 두 질문에 한 화면으로 답한다:
#   ①어떤 캠페인·광고그룹을 엔진이 돌리는가 — naver_adgroup_scope(역할·enabled) + 캠페인 축
#   ②그 성과는 어떻게 나오는가 — 광고비·클릭·전환·ROAS·**총이익**
#
# ★기존 부품 재조합이다(신규 산식 0):
#   · 집계        metrics_aggregator.aggregate(grain="adgroup")  — campaign_id·adgroup_id 동시 반환
#   · 이름        NaverEntity(entity_type='adgroup')             — perf_campaign_harness 관례
#   · BEP 사다리  exploration.resolve_exploration_bep_roas       — 그룹→캠페인→계정 (공개 래퍼)
#   · 보정계수    diagnosis.correction_factor                    — profit_scorecard와 같은 소스
#   · 총이익 산식 (Σconv_amt × factor) ÷ bep_roas − Σcost        — profit_scorecard._window_profit과 동일
#
# ★「BEP 해석 불가면 숫자를 만들어내지 않는다」(profit_scorecard §4-1 '숫자 조작 금지')를 그대로
#   따른다 — gross_profit=None + profit_status 사유. 0원과 «모름»은 다른 값이다.
#
# ★왜 «횡단»이 필요했나: perf_campaign_harness.build_campaign은 캠페인 «1개»만 처리한다.
#   「어떤 캠페인-광고그룹을 돌릴지」는 여러 캠페인을 나란히 놓고 고르는 질문이라 그 함수로는
#   원리적으로 답이 안 된다. 상태 배지(group_state_badge)는 캠페인 상세의 소관으로 남긴다 —
#   이 화면의 질문은 「엔진이 뭘 하고 있나」가 아니라 「무엇을 맡길까」다.
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import NaverCampaignSettings, NaverEntity
from app.services.naver_ad import adgroup_scope, exploration, metrics_aggregator
from app.services.naver_ad.diagnosis import correction_factor
from app.utils.kst import kst_today

DEFAULT_WINDOW_DAYS = 21
MAX_WINDOW_DAYS = 180

# BEP를 해석 못 한 행의 사유 — 화면이 «0원»으로 그리지 않게 하는 라벨
PROFIT_STATUS_OK = "ok"
PROFIT_STATUS_BEP_UNKNOWN = "bep_unknown"


def _round_won(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _clean(name: str | None) -> str:
    """캠페인·그룹 이름 앞머리의 표시용 기호(●○◎)와 공백을 털어낸다."""
    return (name or "").lstrip("●○◎ ").strip()


def _profit(conv_amt: int, cost: int, *, factor: Decimal, bep_roas: Decimal | None) -> tuple[int | None, str]:
    """총이익 절대액 — profit_scorecard와 같은 산식.

    bep_roas가 없으면 (None, 사유)를 돌려준다. **추정하지 않는다** — 원가 미확인 상품의 이익을
    지어내면 그 숫자가 그대로 판정에 쓰인다.
    """
    if bep_roas is None or bep_roas <= 0:
        return None, PROFIT_STATUS_BEP_UNKNOWN
    corrected = Decimal(conv_amt) * factor
    return _round_won(corrected / bep_roas - Decimal(cost)), PROFIT_STATUS_OK


def _profit_band(conv_amt: int, cost: int, *, bep_roas: Decimal | None,
                 factor_low: Decimal, factor_high: Decimal) -> dict:
    """★총이익을 «하나»가 아니라 «있는 그대로 + 구간»으로 낸다 (Jino 지시 2026-08-24).

    Jino 원문: *"보정계수(1.3016)를 왜 쓰는거야? 있는 그대로를 보여줘야 하는거 아니야?"*

    ## 왜 단일값이 위험했나

    보정계수는 `실주문매출 ÷ 네이버 convAmt`인데, **분자에 광고 귀속 조인이 없다**
    (`channel_id==6` 필터뿐, `diagnosis.correction_factor` 주석이 자백). 즉 이 계수는
    **「네이버 채널 매출 100%를 광고가 견인했다」는 가정과 수학적으로 동치**다 — 광고를 안
    켰어도 팔렸을 매출까지 광고 공으로 돌린다. 그래서 D-NAO-230이 점추정을 버리고 구간으로
    바꿨고, ref 93 §1 행 9는 «표시 전용» 소비처를 **「구간 양끝 병기」 1순위**로 지정했다.

    ★초판이 `factor_high`(상한) 하나만 실었다. 상한은 「후보 선정·게이트」용 끝인데 표시에
    쓰면 총이익이 **가장 낙관적으로** 보인다 — 실제로 TPU 21일이 무보정 −864,081원인데
    상한으로는 +557,591원이 되어 **부호가 뒤집혔다**.

    ## 무엇을 내나

      profit        — ★**있는 그대로**(보정 없음). 네이버가 준 convAmt를 그대로 쓴 값.
      profit_low    — × factor_low  (D-NAO-234 하한 — inflowPath 「광고>」5종 ÷ direct 근거)
      profit_high   — × factor_high (상한 — 채널 매출 전액 귀속 «가정»)

    셋을 다 실어야 화면이 «얼마나 모르는지»를 같이 보일 수 있다. 하나만 실으면 그 하나가
    사실처럼 읽힌다.
    """
    raw, status = _profit(conv_amt, cost, factor=Decimal(1), bep_roas=bep_roas)
    low, _ = _profit(conv_amt, cost, factor=factor_low, bep_roas=bep_roas)
    high, _ = _profit(conv_amt, cost, factor=factor_high, bep_roas=bep_roas)
    return {
        "gross_profit": raw,            # ★있는 그대로가 기본값이다
        "gross_profit_low": low,
        "gross_profit_high": high,
        "profit_status": status,
    }


def build_roster(
    db: Session,
    *,
    campaign_id: str | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> dict:
    """캠페인 × 광고그룹 횡단 로스터.

    campaign_id를 주면 그 캠페인만, 안 주면 창 안에 집행이 있었던 전 캠페인.
    창은 D-0(오늘) 제외 — 당일 전환이 정착 전이라 총이익이 과소로 보인다(창 관례).
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    today = today or kst_today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)

    # 보정계수 — profit_scorecard와 같은 소스(fail-open: 산출 불가면 1.0 무보정)
    # ★구간 양끝을 «둘 다» 가져온다(Jino 지시 2026-08-24 — 표시엔 단일 보정값을 쓰지 않는다).
    #   하한 = D-NAO-234(inflowPath 「광고>」5종 ÷ direct 근거) · 상한 = 채널 매출 전액 귀속 가정.
    try:
        factor_info = correction_factor(db, date_to)
        factor_low = Decimal(str(factor_info.get("factor_low") or 1))
        factor_high = Decimal(str(factor_info.get("factor_high") or 1))
        factor_source = factor_info.get("source")
    except Exception:  # noqa: BLE001 — 보정계수 실패가 로스터 전체를 죽이지 않는다
        factor_low = factor_high = Decimal(1)
        factor_source = "unavailable"

    agg = metrics_aggregator.aggregate(
        db, date_from, date_to, grain="adgroup", campaign_filter=campaign_id
    )
    rows = [r for r in agg["rows"] if r.get("adgroup_id")]

    campaign_ids = sorted({r["campaign_id"] for r in rows})
    if campaign_id and campaign_id not in campaign_ids:
        campaign_ids.append(campaign_id)  # 창 안 집행이 없어도 스코프는 보여야 한다

    # 이름 — 캠페인·광고그룹 한 번에
    entities = {
        (e.entity_type, e.entity_id): e
        for e in db.query(NaverEntity)
        .filter(NaverEntity.entity_type.in_(("campaign", "adgroup")))
        .all()
    }
    settings = {
        s.campaign_id: s
        for s in db.query(NaverCampaignSettings)
        .filter(NaverCampaignSettings.campaign_id.in_(campaign_ids))
        .all()
    } if campaign_ids else {}
    scope_map = adgroup_scope.scope_rows_for_campaigns(db, campaign_ids)

    by_campaign: dict[str, list[dict]] = {}
    for r in rows:
        by_campaign.setdefault(r["campaign_id"], []).append(r)

    # ★BEP 사다리의 «요청 단위» 메모(적대 리뷰 P2-2 상환) — 407그룹 × 3 tier 반복 조회로
    #   prod 실측 10.1초가 나왔다. 캠페인·계정 tier는 그룹마다 같은 값이라 한 번만 구하면 된다.
    #   사다리 자체는 exploration에 한 벌로 두고 여기선 메모만 넘긴다(두 벌이 되면 갈라진다).
    #   수명은 이 함수 호출 하나 — 다음 요청은 새 dict라 stale BEP를 물고 있지 않는다.
    bep_cache: dict = {}

    campaigns: list[dict] = []
    for cid in campaign_ids:
        s = settings.get(cid)
        optimizer = s.optimizer if s else "none"          # 설정 행 없음 = none(harness와 같은 시맨틱)
        auto_operate = bool(s.auto_operate) if s else False
        scope_rows = scope_map.get(cid, [])
        scope_by_group = {sr["adgroup_id"]: sr for sr in scope_rows}

        adgroups: list[dict] = []
        c_cost = c_clk = c_imp = c_conv_amt = 0
        c_profit_sum = c_profit_low_sum = c_profit_high_sum = 0
        c_profit_known = False
        for r in by_campaign.get(cid, []):
            gid = r["adgroup_id"]
            cost = int(r.get("cost") or 0)
            conv_amt = int(r.get("conv_amt") or 0)
            bep = exploration.resolve_exploration_bep_roas(db, cid, gid, cache=bep_cache)
            band = _profit_band(conv_amt, cost, bep_roas=bep,
                                factor_low=factor_low, factor_high=factor_high)
            sr = scope_by_group.get(gid)
            e = entities.get(("adgroup", gid))
            adgroups.append({
                "adgroup_id": gid,
                "name": _clean(e.name if e else None) or "이름 없는 그룹",
                "status": (e.status if e else None),
                # ★스코프 — 이 화면의 첫 질문("무엇을 맡길까")에 답하는 필드
                "in_scope": bool(sr and sr["enabled"]),
                "scope_role": (sr or {}).get("role"),
                "scope_enabled": (sr or {}).get("enabled"),
                "cost": cost,
                "imp": int(r.get("imp") or 0),
                "clk": int(r.get("clk") or 0),
                "conv_amt": conv_amt,
                "roas": r.get("roas_naver"),
                "bep_roas": float(bep) if bep is not None else None,
                **band,
            })
            c_cost += cost
            c_imp += int(r.get("imp") or 0)
            c_clk += int(r.get("clk") or 0)
            c_conv_amt += conv_amt
            if band["gross_profit"] is not None:
                c_profit_sum += band["gross_profit"]
                c_profit_low_sum += band["gross_profit_low"]
                c_profit_high_sum += band["gross_profit_high"]
                c_profit_known = True

        # 창 안 집행이 없는 스코프 그룹도 보여준다 — 0은 «없는 것»이 아니다(D-47-h).
        for sr in scope_rows:
            if sr["adgroup_id"] in {a["adgroup_id"] for a in adgroups}:
                continue
            e = entities.get(("adgroup", sr["adgroup_id"]))
            adgroups.append({
                "adgroup_id": sr["adgroup_id"],
                "name": _clean(e.name if e else None) or "이름 없는 그룹",
                "status": (e.status if e else None),
                "in_scope": bool(sr["enabled"]),
                "scope_role": sr["role"],
                "scope_enabled": sr["enabled"],
                "cost": 0, "imp": 0, "clk": 0, "conv_amt": 0, "roas": None,
                "bep_roas": None,
                "gross_profit": None, "gross_profit_low": None, "gross_profit_high": None,
                "profit_status": PROFIT_STATUS_BEP_UNKNOWN,
            })

        adgroups.sort(key=lambda g: (-g["cost"], g["name"]))
        ce = entities.get(("campaign", cid))
        campaigns.append({
            "campaign_id": cid,
            "name": _clean(ce.name if ce else None) or "이름 없는 광고",
            "campaign_type": (ce.campaign_type if ce else None),
            "optimizer": optimizer,
            "auto_operate": auto_operate,
            # ★스코프 행이 있으면 이 캠페인은 「일부 그룹만 맡긴 상태」다 —
            #   그때 캠페인 레벨 액션(예산)은 hold된다(그룹 귀속 불가).
            "has_scope": bool(scope_rows),
            "scoped_count": sum(1 for sr in scope_rows if sr["enabled"]),
            "adgroup_count": len(adgroups),
            "cost": c_cost, "imp": c_imp, "clk": c_clk, "conv_amt": c_conv_amt,
            "roas": (round(c_conv_amt / c_cost, 2) if c_cost else None),
            "gross_profit": (c_profit_sum if c_profit_known else None),
            "gross_profit_low": (c_profit_low_sum if c_profit_known else None),
            "gross_profit_high": (c_profit_high_sum if c_profit_known else None),
            "adgroups": adgroups,
        })

    campaigns.sort(key=lambda c: (not c["has_scope"], -c["cost"], c["name"]))

    return {
        "window": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days": days,
        },
        # ★단일 "value"를 없앴다 — 화면이 하나만 집어 들면 그게 사실처럼 읽힌다.
        "correction_factor": {
            "low": float(factor_low), "high": float(factor_high), "source": factor_source,
        },
        "totals": agg["totals"],
        "campaigns": campaigns,
    }
