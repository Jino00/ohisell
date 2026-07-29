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
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models import NaverAdDaily, NaverAdgroupProduct, NaverProductBep
from app.services.naver_ad import bid_simulator, conversion_maturity
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

# ── D-NAO-119(2026-07-29): 하드 사다리 → 연속 수축(shrinkage) ──────────────────
# 종전 사다리의 구멍: 층 선택 게이트가 **클릭 수**에만 걸려 있었다. RPC 추정량의 분산을
# 지배하는 것은 클릭이 아니라 **전환 건수**인데 문턱이 클릭에 있어서, 전환 2건짜리 추정이
# confident=True로 확정되고 상위 층 폴백이 아예 발동하지 않았다.
#   실측(2026-07-29 09:25 KST, 갤럭시Z 폴드8울트라 grp-…070451363):
#   9일·클릭 100·직접전환 **2건**(33,600원) → RPC 336원 → 상한 160원.
#   같은 캠페인 90일 RPC는 2,283원 — 자기 층 값이 캠페인 평균의 15%.
#   신제품은 정의상 전부 이 함정에 빠진다(자기 이력이 없는 것이 정상 상태이므로).
#
# 교체 산식(비율 추정량의 표준 수축 — 상위 층을 유사 클릭으로 환산해 pooling):
#   RPC = (자기 전환매출 + prior_RPC × prior_clicks) / (자기 클릭 + prior_clicks)
#   prior_clicks = SHRINKAGE_K_CONV / prior_CVR      ("전환 K건어치의 클릭")
#
# ★왜 전환 건수가 아니라 클릭으로 가중하는가: "n=자기 전환 건수"로 가중하면 **클릭 1,000회에
#   전환 0건**인 그룹의 n이 0이 되어 상위 층을 100% 그대로 받는다. 안 팔린다는 강한 증거가
#   가중치 0으로 사라지는 것이다. 클릭 가중은 그 증거를 정직하게 반영하면서
#   (분자에 0원이 그대로 들어간다) 자기 데이터가 쌓일수록 자연히 자기 값으로 수렴한다.
#   Jino가 고른 다이얼의 의미("prior를 전환 20건어치로 본다")는 prior_clicks 환산으로 보존된다.
#
# SHRINKAGE_K_CONV=20 — Jino 확정(2026-07-29 09:34 KST, D-NAO-119). 신제품에 노출 기회를
#   충분히 주는 공격적 설정. 낮출수록 자기 데이터를 빨리 믿는다(보수적).
SHRINKAGE_K_CONV = Decimal("20")
# prior_CVR이 극단적으로 낮으면 prior_clicks가 발산해 자기 데이터가 영원히 안 이긴다.
# 다만 그 경우 prior_RPC 자체도 낮아 상한은 보수적으로 나오므로 과지출 위험은 없다 — 순수 fail-safe.
_MAX_PRIOR_CLICKS = Decimal("5000")
# prior 층에 **전환 건수**가 0이면 CVR 환산 자체가 불가능하다(수집 경로에 따라 amt만 있고
# cnt가 비는 행이 있다). 그때 수축을 통째로 끄면 이 변경이 조용히 무력화되어 종전 버그로
# 회귀한다 — 그래서 끄지 않고 기본 강도를 쓴다.
# 150 = 이 계정 관측 CVR(≈11~14%)에서 전환 20건이 나올 만한 클릭 수(20/0.13 ≈ 154).
# 즉 SHRINKAGE_K_CONV와 같은 다이얼을 CVR을 모를 때 근사한 값이지 새 다이얼이 아니다.
_DEFAULT_PRIOR_CLICKS = Decimal("150")
# 상위 층을 이 상품 가격대로 환산할 때의 클램프. 상위 층은 판매가가 다른 상품의 혼합이라
# (모듈 헤더 ★단서 참조) 그대로 옮기면 싼 상품은 과대·비싼 상품은 과소가 된다.
# 배율 자체가 극단이면 그 층이 이 상품을 대표하지 못한다는 뜻이므로 잘라낸다.
_PRICE_RATIO_MIN = Decimal("0.5")
_PRICE_RATIO_MAX = Decimal("2")

# ★전환 정착 보정 스위치 — **기본 OFF**(2026-07-29, D-NAO-119 부수).
# 이 배선(dd73a59)은 "깨끗한 코호트 21일 누적 후 재측정하고 배포 판단"으로 **보류 결정**돼 있다.
# 이 파일을 다른 이유로 배포할 때 그 보류 결정이 조용히 함께 실려 가는 것을 막는 것이 이 상수다.
# 보류 근거(2026-07-29 09:30 KST 라이브 곡선 실측):
#   m(d) = 1.0(d≤5) → 0.8333(d=6) → 0.7143(d=7) → 0.625(d=8~9)
#   정착 비율이 시간이 갈수록 **감소**하는 것은 정의상 불가능하다(누적량은 단조 증가).
#   그 결과 배수 1/m(d)가 최근(1.0)보다 **오래된 데이터(1.60)를 더 크게 부풀린다** —
#   "최근 며칠이 과소계상된다"는 이 보정의 원래 의도와 정반대 방향이다.
#   5/6·5/7·5/8 형태는 07-28에 확인된 4/7 퇴화와 같은 계열(코호트 이산 조합)이라
#   곡선 퇴화가 완전히 해소되지 않았음을 시사한다.
# 켜기 전 선결: compute_curve 퇴화 규명 → 배수 분포 재측정 → Jino 배포 판단.
MATURITY_CORRECTION_ENABLED = False


def _layer_stats(
    db: Session, today: date, *, adgroup_id: str | None = None, campaign_id: str | None = None,
) -> dict:
    """지정 grain의 [today-90, today-1] 관측 통계. 둘 다 None 필터면 계정 전체.

    반환: {"clk", "conv_cnt", "revenue", "rpc", "cvr", "aov"}
      rpc = 전환매출/클릭 · cvr = 전환건수/클릭 · aov = 전환매출/전환건수(주문당 매출).
      표본이 0이면 해당 파생값은 None(0으로 위장하지 않는다 — "관측 없음"과 "관측된 0"은 다르다).
    cvr·aov는 D-NAO-119 수축이 쓴다: cvr은 prior 가중치(유사 클릭) 환산에, aov는 상위 층을
    이 상품 가격대로 환산하는 배율(ceiling_from)에 쓰인다.

    ★전환매출 = **직접전환만**(conv_direct_amt). 간접전환 제외 = D-NAO-95(ref 40 §2)가 표준화한
      보수 규칙을 그대로 따른다: "간접전환 포함은 귀속이 느슨해 상한을 낙관 쪽으로 부풀린다"
      (네이버 convAmt가 실주문 대비 과대하다는 D-NAO-7/21 선례와 같은 방향의 편향).
      ※ 기존 visibility.evidence_ceiling은 direct+indirect를 쓴다 — **의도적으로 다르다.**
        그쪽은 "증거 구매 창"을 여는 완화 산식이고, 여기는 콜드 소재에 큰 폭 상향(±15% 완전 면제)을
        태우는 산식이라 더 보수적이어야 한다. 두 값이 다른 것은 버그가 아니라 설계다.
    backfill sentinel 행은 제외한다(naver_ad_daily 2배 계상 함정 — 메모리 naver-ad-data-cadence).

    ★전환 정착 보정(2026-07-28): 창의 각 날짜를 **성숙 시점 추정치로 환산**한 뒤 합산한다
      (conversion_maturity.maturity_multiplier = 1/m(d)). 종전엔 창 전체를 단일 SUM으로 더해서,
      아직 정착 중인 최근 며칠이 과소계상된 채로 RPC를 끌어내렸다. 수요가 평평하면 작은 편향이나
      **수요가 오르는 구간에서는 가장 뜨거운 최근 며칠이 가장 덜 집계돼** 상한을 계통적으로 낮춘다.
      특히 **신규 상품은 90일 창 안의 데이터가 전부 최근**이라 편향을 100% 맞는다 — 정작 공격적으로
      사야 할 신제품 런칭에서 상한이 가장 보수적으로 나오던 원인이다.
      곡선이 없으면(코호트 부족) 배수 1.0이라 **종전과 동일한 값**을 낸다(fail-safe).
      ★2026-07-29: MATURITY_CORRECTION_ENABLED로 게이트됨(기본 OFF, 사유는 상수 주석).
    """
    curve = conversion_maturity.load_curve(db) if MATURITY_CORRECTION_ENABLED else {}
    q = db.query(
        NaverAdDaily.ad_date,
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.clk), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_amt), 0),
        sqlfunc.coalesce(sqlfunc.sum(NaverAdDaily.conv_direct_cnt), 0),
    ).filter(
        NaverAdDaily.ad_date >= today - timedelta(days=RPC_WINDOW_DAYS),
        NaverAdDaily.ad_date <= today - timedelta(days=1),
        NaverAdDaily.adgroup_id != BACKFILL_SENTINEL_ADGROUP,
    )
    if adgroup_id is not None:
        q = q.filter(NaverAdDaily.adgroup_id == adgroup_id)
    if campaign_id is not None:
        q = q.filter(NaverAdDaily.campaign_id == campaign_id)
    clk = 0
    conv_cnt = 0
    revenue = Decimal(0)
    for ad_date, day_clk, day_direct, day_conv in q.group_by(NaverAdDaily.ad_date).all():
        clk += int(day_clk)
        conv_cnt += int(day_conv)
        d = ad_date.date() if isinstance(ad_date, datetime) else ad_date
        revenue += Decimal(int(day_direct)) * conversion_maturity.maturity_multiplier(
            curve, (today - d).days,
        )
    return {
        "clk": clk,
        "conv_cnt": conv_cnt,
        "revenue": revenue,
        "rpc": (revenue / Decimal(clk)) if clk > 0 else None,
        "cvr": (Decimal(conv_cnt) / Decimal(clk)) if clk > 0 else None,
        "aov": (revenue / Decimal(conv_cnt)) if conv_cnt > 0 else None,
    }


def _rpc_for(
    db: Session, today: date, *, adgroup_id: str | None = None, campaign_id: str | None = None,
) -> tuple[int, Decimal | None]:
    """(클릭 합, RPC) — _layer_stats의 종전 시그니처 호환 진입점."""
    s = _layer_stats(db, today, adgroup_id=adgroup_id, campaign_id=campaign_id)
    return s["clk"], s["rpc"]


def _prior_clicks(prior_cvr: Decimal | None) -> Decimal:
    """상위 층 신뢰도를 '유사 클릭 수'로 환산 — 전환 SHRINKAGE_K_CONV건이 나올 만한 클릭 수.

    CVR을 못 구하면(전환 건수 0) _DEFAULT_PRIOR_CLICKS — 수축을 끄지 않는다(상수 주석 참조).
    """
    if not prior_cvr or prior_cvr <= 0:
        return _DEFAULT_PRIOR_CLICKS
    return min(SHRINKAGE_K_CONV / prior_cvr, _MAX_PRIOR_CLICKS)


def _blend(own_revenue: Decimal, own_clk: int, prior_rpc: Decimal, prior_clicks: Decimal) -> Decimal:
    """수축 혼합 = (자기 전환매출 + prior_RPC × prior_clicks) / (자기 클릭 + prior_clicks)."""
    return (own_revenue + prior_rpc * prior_clicks) / (Decimal(own_clk) + prior_clicks)


def resolve_rpc(db: Session, adgroup_id: str, campaign_id: str, today: date) -> dict:
    """RPC — 자기 층(광고그룹) 실측과 상위 층(prior)을 **연속 수축**으로 섞어 낸다(D-NAO-119).

    prior 선택: 캠페인(clk ≥ 30) → 없으면 계정(clk ≥ 100). 둘 다 미달이면 prior 없음.
    prior 없음 + 자기 층도 미달 → rpc=None, source="none"(경제 근거 없음 — 호출부가 제안 보류).

    반환: {"rpc", "rpc_source", "sample_clk", "confident", "blend"}
      rpc_source ∈ {"adgroup","campaign","account","none"} — **가중치가 큰 쪽**(지배 출처).
        이 어휘는 종전과 같다(bep_breakdown이 "account"를 저신뢰 표시에 쓴다 — 깨면 안 된다).
      sample_clk : 지배 출처 층의 클릭 수.
      confident  : 자기 층이 지배하거나 prior가 캠페인 층이면 True. **계정 층 지배는 False** —
        하위 판정자(cold_start_bid_decider)의 LOW_CONFIDENCE_CEILING_FACTOR 할인을 살려둔다.
        K가 공격적일수록 이 안전핀이 중요하다.
      blend      : 가격 환산 재혼합용 원재료(ceiling_from 전용). prior가 없으면 None.
                   ★이 키는 ceiling_from이 반환 dict에서 **떼어낸다**(Decimal이 API 응답·
                   로깅으로 새 나가지 않도록).
    """
    own = _layer_stats(db, today, adgroup_id=adgroup_id)

    prior = _layer_stats(db, today, campaign_id=campaign_id)
    prior_source = "campaign"
    if prior["clk"] < MIN_CLK_CAMPAIGN:
        prior = _layer_stats(db, today)
        prior_source = "account"
        if prior["clk"] < MIN_CLK_ACCOUNT:
            prior = None
    # 매출이 아예 없는 층은 빌려올 경제 근거가 없다(전환 건수만 비는 경우는 _prior_clicks가 처리).
    if prior is not None and (prior["rpc"] is None or prior["rpc"] <= 0):
        prior = None

    if prior is None:
        # 상위 근거 없음 → 종전 동작 그대로(자기 층 단독 판정).
        if own["rpc"] is not None and own["clk"] >= MIN_CLK_ADGROUP:
            return {"rpc": own["rpc"], "rpc_source": "adgroup", "sample_clk": own["clk"],
                    "confident": True, "blend": None}
        return {"rpc": None, "rpc_source": "none", "sample_clk": own["clk"],
                "confident": False, "blend": None}

    prior_clicks = _prior_clicks(prior["cvr"])
    own_dominant = Decimal(own["clk"]) >= prior_clicks
    return {
        "rpc": _blend(own["revenue"], own["clk"], prior["rpc"], prior_clicks),
        "rpc_source": "adgroup" if own_dominant else prior_source,
        "sample_clk": own["clk"] if own_dominant else prior["clk"],
        "confident": own_dominant or prior_source == "campaign",
        "blend": {
            "own_revenue": own["revenue"], "own_clk": own["clk"], "own_conv": own["conv_cnt"],
            "prior_source": prior_source, "prior_rpc": prior["rpc"],
            "prior_aov": prior["aov"], "prior_clicks": prior_clicks,
        },
    }


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


def ceiling_from(db: Session, ad_id: str, rpc_info: dict) -> dict:
    """이미 구한 RPC로 상한만 낸다 — RPC 사다리를 **재사용**하고 싶을 때의 진입점.

    RPC는 (광고그룹·캠페인)에만 의존하는데 compute_ceiling은 소재마다 사다리를 다시 돈다.
    상품 여러 개·소재 수십 개를 한 화면에 그리는 호출부(성과뷰 ⑤)에서는 같은 계정 전체 집계가
    소재 수만큼 반복된다(라이브 54회 — 리뷰 P2-10). 호출부가 resolve_rpc를 캐시해 넘기면
    이 함수가 나머지(상품 BEP 조인 + affordable_ceiling)만 한다.
    ★산식은 compute_ceiling과 **같은 한 벌**이다 — 아래 compute_ceiling이 이 함수를 호출한다.

    ★D-NAO-119 가격 환산: 상위 층(prior)은 판매가가 서로 다른 상품의 혼합이라 그 RPC를
      그대로 빌려오면 이 상품 가격대와 어긋난다(모듈 헤더 ★단서가 "남은 과제"로 적어둔 바로 그것).
      여기서 판매가를 알 수 있으므로(= bep_roas × 공헌이익) prior을 이 상품 가격대로 환산해
      다시 섞는다. 환산은 resolve_rpc가 아니라 **여기서** 한다 — perf_timeline_harness가
      resolve_rpc를 (광고그룹,캠페인) 키로 캐시하는데 거기에 상품별 값을 넣으면 캐시가 깨진다.
    """
    bep = resolve_bep(db, ad_id)
    out = {
        "ad_id": ad_id, "ceiling_cpc": 0, "reason": "", "price_ratio": None,
        **{k: v for k, v in rpc_info.items() if k != "blend"},
        **{k: bep[k] for k in ("bep_roas", "contribution_margin", "product_name")},
    }
    if bep["bep_roas"] is None or bep["bep_roas"] <= 0:
        out["reason"] = "상품 BEP 없음(원가 미입력 등) — 경제 상한 산출 불가"
        return out
    if rpc_info["rpc"] is None:
        out["reason"] = (
            f"RPC 표본 부족(모든 층 미달, 최종 관측 clk={rpc_info['sample_clk']}) — 경제 상한 산출 불가"
        )
        return out

    rpc_used = rpc_info["rpc"]
    blend = rpc_info.get("blend")
    if blend and blend["prior_aov"] and blend["prior_aov"] > 0 and bep["contribution_margin"]:
        price = bep["bep_roas"] * bep["contribution_margin"]  # bep_roas = 판매가/공헌이익
        if price > 0:
            ratio = min(max(price / blend["prior_aov"], _PRICE_RATIO_MIN), _PRICE_RATIO_MAX)
            out["price_ratio"] = ratio
            rpc_used = _blend(
                blend["own_revenue"], blend["own_clk"],
                blend["prior_rpc"] * ratio, blend["prior_clicks"],
            )
            out["rpc"] = rpc_used

    ceiling = bid_simulator.affordable_ceiling(rpc_used, bep["bep_roas"])
    if ceiling <= 0:
        out["reason"] = (
            f"RPC {rpc_used:.0f}원 ÷ BEP {bep['bep_roas']} 가 최소입찰가 미만 — "
            "이 상품은 현 RPC로 어떤 입찰도 수익성 없음"
        )
        return out
    out["ceiling_cpc"] = ceiling
    return out


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
    return ceiling_from(db, ad_id, resolve_rpc(db, adgroup_id, campaign_id, today))
