# bid_ceiling_calculator.py — SA1 bid_ceiling_calculator (CS 스프린트, 단일 책임: 이익 상한 산출)
# 역할(SA·순수 계산 + DB 읽기만): 콜드 소재의 "이 값을 넘게 주고 클릭을 사면 손해"인 CPC 상한을
#   근거 라벨·표본크기와 함께 낸다. 광고 API 호출 없음.
#
# ★수식 정합 확인(코드 대조 완료 — 스프린트 지시의 검증 요구사항):
#   지시받은 산식 = 최대 허용 CPC = CVR × 공헌이익(원/개).
#   기존 코드의 정의:
#     bep_calculator.calculate_bep: contribution = (판매가 − 수수료 − 원가 − 물류비) / 1.1
#                                   bep_roas     = 판매가 / contribution
#     bid_simulator.affordable_ceiling(rpc, roas) = rpc / roas   (70~100,000원·10원 단위 내림)
#   대조: RPC = 판매가 × CVR 이므로
#         affordable_ceiling = (판매가 × CVR) / (판매가 / 공헌이익) = CVR × 공헌이익.
#   → 이 모듈은 산식을 새로 만들지 않고 기존 bid_simulator.affordable_ceiling을 그대로
#     재사용한다(정의 중복 = 미래의 불일치 사고).
#
#   ★단, 위 소거는 **RPC의 매출이 BEP 행과 같은 상품에서 나올 때만** 엄밀히 성립한다
#     (적대적 리뷰 P2-8). 수량>1도 VAT도 문제없다 — RPC/판매가 = 클릭당 판매 수량이고, 지불·수취가
#     모두 부가세포함이라 공헌이익의 ÷1.1과 소거된다. 문제는 **상품 혼합**이다:
#     아래 사다리는 표본이 없으면 campaign → account로 내려가는데, 콜드 소재는 정의상 자기 실적이
#     없어 그 폴백 층에 떨어지는 것이 **정상 경로**다. 그 층의 RPC는 판매가가 서로 다른 여러 상품의
#     혼합이라 "판매가가 소거된다"는 유도가 그대로 서지 않는다(싼 상품이 비싼 상품과 같은 캠페인에
#     있으면 상한이 과대 = 과지출 방향).
#     → 완화책: `confident` 라벨을 계정 층에서 False로 내려보내고, cold_start_bid_decider가
#       그 층 상한에 보수 계수(LOW_CONFIDENCE_CEILING_FACTOR)를 곱한다. 근본 해소(상품 단위
#       RPC 좁히기)는 남은 과제다.
#   RPC 형태를 채택한 이유: CVR·객단가를 따로 추정하면 "전환 1건이 몇 개인가"(수량) 모호성이
#   생기는데, RPC(=매출/클릭)는 그 분해 자체가 필요 없다. 기존 코드도 같은 이유로 RPC를 쓴다
#   (affordable_ceiling docstring 참조).
#
# ★"CVR 사다리"의 실제 구현 형태(지시와의 차이 — 데이터 제약):
#   지시는 ①소재 자기이력 → ②그룹/캠페인 → ③상품라인 → ④캠페인 블렌디드 → ⑤폴백을 요구했으나,
#   **naver_ad_daily에는 소재(ad) grain이 없다**(컬럼: campaign_id/adgroup_id/keyword_id뿐 —
#   라이브 스키마 실측). 소재 자기이력은 원리적으로 조회 불가다. 그래서 실제 사다리는
#   ①광고그룹 → ②캠페인 → ③계정 이며, 이는 기존 bid_simulator.pooled_rpc의 계층
#   (키워드→그룹→캠페인→계정)에서 조회 불가능한 층만 뺀 것과 같다.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverAdgroupProduct, NaverProductBep
from app.services.naver_ad import bid_simulator
from app.services.naver_ad.campaign_backfill import BACKFILL_SENTINEL_ADGROUP

log = logging.getLogger(__name__)

# RPC 관측 창(일) — visibility._CAMPAIGN_RPC_WINDOW_D(90)와 같은 값·같은 근거(ref38 §1·2:
# 90일 실측 전환단가가 순위와 무관하게 평평). 두 모듈이 같은 상수를 쓰는 것은 의도된 정합이다.
RPC_WINDOW_DAYS = 90
# 각 층의 최소 표본(클릭). 이보다 적으면 그 층의 RPC는 신뢰 불가 → 다음(상위) 층으로 내려간다.
#   그룹 층 10 = exploration._MIN_CLICK_FOR_EXPLORATION(핫셋/표본미달 경계)와 동일 출처.
#   캠페인 층 30 = visibility._MIN_CAMPAIGN_CLK_FOR_RPC와 동일 출처.
MIN_CLK_ADGROUP = 10
MIN_CLK_CAMPAIGN = 30
MIN_CLK_ACCOUNT = 100  # 계정 층까지 내려왔는데 이마저 미달이면 경제 근거 없음으로 본다.

NAVER_CHANNEL_ID = 6


def _rpc_for(
    db: Session, today: date, *, adgroup_id: str | None = None, campaign_id: str | None = None,
) -> tuple[int, Decimal | None]:
    """지정 grain의 [today-90, today-1] 클릭 합과 RPC(= 전환매출 / 클릭). 둘 다 None 필터면 계정 전체.

    ★전환매출 = **직접전환만**(conv_direct_amt). 간접전환 제외 = D-NAO-95(ref 40 §2)가 표준화한
      보수 규칙을 그대로 따른다: "간접전환 포함은 귀속이 느슨해 상한을 낙관 쪽으로 부풀린다"
      (네이버 convAmt가 실주문 대비 과대하다는 D-NAO-7/21 선례와 같은 방향의 편향).
      ※ 기존 visibility.evidence_ceiling은 direct+indirect를 쓴다 — **의도적으로 다르다.**
        그쪽은 "증거 구매 창"을 여는 완화 산식이고, 여기는 콜드 소재에 큰 폭 상향(±15% 완전 면제)을
        태우는 산식이라 더 보수적이어야 한다. 두 값이 다른 것은 버그가 아니라 설계다.
    backfill sentinel 행은 제외한다(naver_ad_daily 2배 계상 함정 — 메모리 naver-ad-data-cadence).
    """
    q = db.query(
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
    ).filter(
        NaverAdDaily.ad_date >= today - timedelta(days=RPC_WINDOW_DAYS),
        NaverAdDaily.ad_date <= today - timedelta(days=1),
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if adgroup_id is not None:
        q = q.filter(NaverAdDaily.adgroup_id == adgroup_id)
    if campaign_id is not None:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    clk, direct = q.one()
    clk = int(clk)
    if clk <= 0:
        return 0, None
    return clk, Decimal(int(direct)) / Decimal(clk)


def resolve_rpc(db: Session, adgroup_id: str, campaign_id: str, today: date) -> dict:
    """RPC 출처 사다리 — 표본이 충분한 **가장 가까운** 층의 RPC를 채택하고 그 출처를 라벨로 반환.

    ① adgroup   (clk ≥ 10)   — 이 소재가 속한 광고그룹의 실측
    ② campaign  (clk ≥ 30)   — 같은 캠페인(= 같은 상품 라인) 실측
    ③ account   (clk ≥ 100)  — 계정 전체 실측(최후)
    전부 미달 → rpc=None, source="none"(경제 근거 없음 — 호출부가 제안 보류).

    반환: {"rpc": Decimal|None, "rpc_source": str, "sample_clk": int, "confident": bool}
      confident: 그룹/캠페인 층에서 잡혔으면 True. 계정 층 폴백은 **False**(그 소재 고유의
      경제성이 아니라 계정 평균을 빌려 쓴 것 — 이 신뢰도로 공격적 입찰을 하면 안 된다는 신호).
    """
    clk, rpc = _rpc_for(db, today, adgroup_id=adgroup_id)
    if rpc is not None and clk >= MIN_CLK_ADGROUP:
        return {"rpc": rpc, "rpc_source": "adgroup", "sample_clk": clk, "confident": True}

    clk, rpc = _rpc_for(db, today, campaign_id=campaign_id)
    if rpc is not None and clk >= MIN_CLK_CAMPAIGN:
        return {"rpc": rpc, "rpc_source": "campaign", "sample_clk": clk, "confident": True}

    clk, rpc = _rpc_for(db, today)
    if rpc is not None and clk >= MIN_CLK_ACCOUNT:
        return {"rpc": rpc, "rpc_source": "account", "sample_clk": clk, "confident": False}

    return {"rpc": None, "rpc_source": "none", "sample_clk": clk, "confident": False}


def resolve_bep(db: Session, ad_id: str) -> dict:
    """소재 → 상품 BEP(공헌이익·bep_roas). naver_adgroup_product.mall_product_id로 조인.

    반환: {"bep_roas": Decimal|None, "contribution_margin": Decimal|None,
           "mall_product_id": str|None, "product_name": str}
    """
    row = (
        db.query(NaverAdgroupProduct, NaverProductBep)
        .outerjoin(
            NaverProductBep,
            (NaverProductBep.channel_product_id == NaverAdgroupProduct.mall_product_id)
            & (NaverProductBep.channel_id == NAVER_CHANNEL_ID),
        )
        .filter(NaverAdgroupProduct.ad_id == ad_id)
        .first()
    )
    if row is None:
        return {"bep_roas": None, "contribution_margin": None, "mall_product_id": None, "product_name": ""}
    ap, bep = row
    if bep is None or bep.bep_roas is None:
        return {"bep_roas": None, "contribution_margin": None,
                "mall_product_id": ap.mall_product_id, "product_name": ap.product_name or ""}
    return {
        "bep_roas": Decimal(str(bep.bep_roas)),
        "contribution_margin": Decimal(str(bep.contribution_margin)),
        "mall_product_id": ap.mall_product_id,
        "product_name": bep.product_name or ap.product_name or "",
    }


def compute_ceiling(db: Session, ad_id: str, adgroup_id: str, campaign_id: str, today: date) -> dict:
    """콜드 소재의 이익 CPC 상한 = affordable_ceiling(RPC, bep_roas)  (= CVR × 공헌이익, 위 증명).

    반환 dict:
      ceiling_cpc:   int  — 유효 입찰 규격(10원 단위 내림). 0 = 상한 못 세움.
      rpc / rpc_source / sample_clk / confident  — 근거와 신뢰도(호출부까지 반드시 전달).
      bep_roas / contribution_margin / product_name
      reason: 상한을 못 세운 사유(ceiling_cpc>0이면 "").

    ★confident=False(계정 층 폴백)여도 상한 자체는 낸다 — 다만 라벨을 그대로 실어 보내고,
      제안 여부는 호출부(cold_start_bid_decider)가 판단한다. 표본 빈약이 조용히 묻히면
      "신뢰도 낮은 상한으로 공격적 입찰"이 되므로 라벨은 절대 떨어뜨리지 않는다.
    """
    bep = resolve_bep(db, ad_id)
    rpc_info = resolve_rpc(db, adgroup_id, campaign_id, today)
    out = {
        "ad_id": ad_id, "ceiling_cpc": 0, "reason": "",
        **rpc_info, **{k: bep[k] for k in ("bep_roas", "contribution_margin", "product_name")},
    }
    if bep["bep_roas"] is None or bep["bep_roas"] <= 0:
        out["reason"] = "상품 BEP 없음(원가 미입력 등) — 경제 상한 산출 불가"
        return out
    if rpc_info["rpc"] is None:
        out["reason"] = (
            f"RPC 표본 부족(모든 층 미달, 최종 관측 clk={rpc_info['sample_clk']}) — 경제 상한 산출 불가"
        )
        return out

    ceiling = bid_simulator.affordable_ceiling(rpc_info["rpc"], bep["bep_roas"])
    if ceiling <= 0:
        out["reason"] = (
            f"RPC {rpc_info['rpc']:.0f}원 ÷ BEP {bep['bep_roas']} 가 최소입찰가 미만 — "
            "이 상품은 현 RPC로 어떤 입찰도 수익성 없음"
        )
        return out
    out["ceiling_cpc"] = ceiling
    return out
