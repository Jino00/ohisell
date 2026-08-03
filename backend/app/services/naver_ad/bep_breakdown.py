# bep_breakdown.py — SA ⑤BEP 구성 (성과뷰 Phase 3, docs/PLAN_naver-ad-performance-view.md §4-ⓓ)
"""역할(SA·단일 책임): "이 상품의 입찰 상한이 왜 그 숫자냐"에 답할 **구성 내역**을 조립한다.

★새 산식 금지(계획서 §4-ⓓ). 이 파일에는 BEP를 만드는 계산이 하나도 없다 — 전부
  bep_calculator가 이미 계산해 `naver_product_bep`에 남긴 값을 읽어 **되짚어 보여줄 뿐**이다.
  화면에 보이는 유일한 산술은 저장값끼리의 자명한 조합(수수료액 = 판매가×요율,
  세전잔액 = 판매가−수수료−원가−물류비)이고, 이는 산식이 아니라 **저장된 산식의 전개**다.

★"더해서 안 맞는" 함정을 화면에서 없앤다: 공헌이익은 (판매가−수수료−원가−물류비)를
  **÷1.1(VAT)** 한 값이다. 이 단계를 빼고 4개 숫자만 보여주면 사장님 화면에서 뺄셈이 안 맞고,
  안 맞는 표는 신뢰를 잃는다. 그래서 `pre_vat_margin`과 `vat_divisor`를 명시적으로 낸다.

★원칙 18-6/18-8: 다른 SA를 직접 부르지 않는다. 상품별 N배송 혼합비(bep_calculator)와
  소재별 이익 상한(bid_ceiling_calculator)은 **하니스가 계산해 optional 인자로 넘긴다.**
  안 넘어오면 그 칸은 값이 아니라 `None`(알 수 없음)으로 남는다 — 0으로 채우지 않는다(원칙22).

★원가 미입력(has_cost=False) 상품은 **추정치로 채우지 않는다**(계획서 §6 Phase3 완료기준 4).
  "상한 산출 불가 — 원가가 입력되지 않았습니다"라고 말하고 값은 전부 None으로 둔다.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverAdgroupProduct, NaverBidEstimateDaily, NaverProductBep
from app.services.naver_ad import adgroup_product_freshness
from app.services.naver_ad.alert_humanizer import clean_name, money

NAVER_CHANNEL_ID = 6
# 공헌이익 산출의 VAT 나눗수 — bep_calculator.VAT_DIVISOR와 **같은 값을 화면에 설명용으로
# 싣는다**. 계산에는 쓰지 않는다(계산은 이미 끝났다). 값이 갈리지 않도록 import해서 쓴다.
from app.services.naver_ad.bep_calculator import VAT_DIVISOR  # noqa: E402  (상수 단일 출처)

# 시장가 사다리에서 화면에 인용할 순위. 4위 = 사다리의 가장 낮은 층(가장 싼 노출 구간)이라
# "여기까지 내려와도 이 값"이라는 하한 감각을 준다(계획서 §4-ⓓ 예시 문장과 동일).
MARKET_BID_POSITION = 4
# 시장가 조회 창(일). 이보다 오래된 관측은 "지금 시장가"라고 부를 수 없다.
MARKET_BID_MAX_AGE_DAYS = 7


def product_ad_links(db: Session, *, campaign_id: str | None = None) -> dict[str, list[dict]]:
    """상품(channel_product_id) → 그 상품을 파는 소재 목록. 하니스가 상한 계산 대상을 정할 때 쓴다.

    반환: {mall_product_id: [{"ad_id", "adgroup_id", "campaign_id", "product_name"}, ...]}
    ad_id가 없는 매핑 행(소재 미확정)은 상한을 계산할 대상이 아니라 제외한다.
    """
    q = db.query(NaverAdgroupProduct).filter(adgroup_product_freshness.fresh_condition())
    if campaign_id:
        q = q.filter(NaverAdgroupProduct.campaign_id == campaign_id)
    out: dict[str, list[dict]] = {}
    for row in q.all():
        if not row.mall_product_id or not row.ad_id:
            continue
        out.setdefault(row.mall_product_id, []).append({
            "ad_id": row.ad_id,
            "adgroup_id": row.adgroup_id,
            "campaign_id": row.campaign_id,
            "product_name": row.product_name or "",
        })
    return out


def _market_bid(db: Session, ad_ids: list[str], today: date) -> dict:
    """상품의 4위 시장가 — **가장 최근 관측일**의 값 중 **가장 높은 값**(구속 조건).

    반환: {"bid": int|None, "device": str|None, "observed_on": str|None}

    ★최솟값이 아니라 최댓값이다(리뷰 P2-5 교정). 상한은 이미 최솟값(가장 빡빡한 소재)을 쓴다.
      시장가까지 최솟값으로 잡으면 **비교의 두 항이 서로 반대 방향으로 낙관/보수**가 되어
      "시장가 ≤ 상한(= 살 만하다)" 판정이 체계적으로 후하게 나온다. 실제로 그 순위를 사려면
      기기·소재 중 가장 비싼 쪽을 지불해야 하므로 최댓값이 실제 구속 조건이다.

    ★기기(MOBILE/PC)를 섞어 최솟값을 뽑던 것도 같은 문제였다 — PC가 싸다고 모바일 노출이
      그 값에 되는 게 아니다. 어느 기기 기준인지 함께 낸다.

    ★관측일을 함께 낸다. 최대 7일 전 값을 "지금 시장가"라고 부르면서 언제 본 값인지 숨기면
      화면이 없는 신선도를 주장하는 것이다(원칙22).

    is_floor 행은 제외한다 — 시세가 무의미하다는 표식이라 이걸 "시장가"라고 부르면 거짓이다.
    """
    empty = {"bid": None, "device": None, "observed_on": None}
    if not ad_ids:
        return empty
    rows = (
        db.query(NaverBidEstimateDaily.bid, NaverBidEstimateDaily.device,
                 NaverBidEstimateDaily.date)
        .filter(
            NaverBidEstimateDaily.ad_id.in_(ad_ids),
            NaverBidEstimateDaily.position == MARKET_BID_POSITION,
            NaverBidEstimateDaily.is_floor.is_(False),
            NaverBidEstimateDaily.date >= today - timedelta(days=MARKET_BID_MAX_AGE_DAYS),
        )
        .all()
    )
    seen = [(int(b), dev, d) for b, dev, d in rows if b]
    if not seen:
        return empty
    latest = max(d for _b, _dev, d in seen)
    on_latest = [(b, dev) for b, dev, d in seen if d == latest]
    bid, device = max(on_latest, key=lambda x: x[0])
    observed = latest.date() if hasattr(latest, "date") else latest
    return {"bid": bid, "device": device, "observed_on": observed.isoformat()}


def _pick_ceiling(ads: list[dict], ceilings: dict[str, dict] | None) -> tuple[int | None, str]:
    """상품 단위 상한 = 그 상품을 파는 소재들의 상한 중 **최솟값**(가장 보수적).

    상한은 소재(=광고그룹)마다 RPC가 달라 값이 갈린다. 상품 한 줄로 접어 보여줘야 하는 화면에서
    최댓값·평균을 쓰면 어떤 소재에서는 그 값이 이미 손해다 — 이 페이지의 목적(이익을 지키는
    상한)과 반대 방향으로 틀린다. 그래서 최솟값을 쓰고, 갈린다는 사실을 사유에 남긴다.

    반환: (ceiling|None, basis_ko). 산출된 소재가 하나도 없으면 (None, 사유).
    """
    if not ceilings:
        return None, "이 상품의 광고 실적 표본이 아직 계산되지 않았습니다."
    values: list[int] = []
    reasons: list[str] = []
    for ad in ads:
        info = ceilings.get(ad["ad_id"])
        if not info:
            continue
        if info.get("ceiling_cpc"):
            values.append(int(info["ceiling_cpc"]))
        elif info.get("reason"):
            reasons.append(str(info["reason"]))
    if not values:
        return None, (reasons[0] if reasons else "이 상품의 광고 실적 표본이 부족합니다.")
    basis = "이 상품을 파는 광고 한 곳 기준입니다."
    if len(values) > 1:
        basis = (
            f"이 상품은 광고 {len(values)}곳에서 팔고 있어, 그중 가장 빡빡한 쪽에 맞춘 값입니다."
        )
    return min(values), basis


def _device_label(device: str | None) -> str:
    return {"MOBILE": "모바일", "PC": "PC"}.get((device or "").upper(), "")


def _sentence(name: str, ceiling: int | None, market: dict, basis: str, blocked: str) -> str:
    """사장님 문장(D-NAO-103③: 무슨 일 → 숫자 근거 → 권하는 행동).

    ★"사면 손해입니다"라고 단정하지 않는다(원칙22 · 계획서 R1). 이 상한의 RPC는
      bid_ceiling_calculator가 **직접전환 매출만** 세어 만든 보수적 값이다(간접전환 제외 —
      그 모듈 _rpc_for 주석). 실제 매출은 이보다 클 수 있으므로, 상한을 넘었다는 것은
      "보수적으로 보면 넘는다"는 뜻이지 손실 확정이 아니다. 화면은 그 차이를 말해야 한다.
    """
    if blocked:
        return f"{name}: {blocked}"
    if ceiling is None:
        return f"{name}: 남는 선을 계산할 수 있지만, 클릭당 상한은 {basis}"
    head = f"{name}: 보수적으로 보면 클릭당 {money(ceiling)}까지가 남는 선입니다."
    bid = market.get("bid")
    if bid is None:
        return f"{head} 시장가는 최근에 관측된 값이 없습니다. {basis}"
    dev = _device_label(market.get("device"))
    where = f"{dev} 4위" if dev else "4위"
    when = market.get("observed_on") or ""
    tail = f"({when} 관측) " if when else ""
    if bid > ceiling:
        gap = bid - ceiling
        return (
            f"{head} {where} 시장가는 {money(bid)}이라 {money(gap)} 높습니다 {tail}— "
            f"이 순위를 사려면 남는 선을 넘습니다. {basis}"
        )
    return (
        f"{head} {where} 시장가는 {money(bid)}으로 그 선 안쪽입니다 {tail}. {basis}"
    )


def build(
    db: Session,
    links: dict[str, list[dict]],
    *,
    today: date,
    only_actionable: bool = True,
    nb_shares: dict[str, dict] | None = None,
    ceilings: dict[str, dict] | None = None,
) -> dict:
    """⑤BEP 구성 표.

    links     : product_ad_links() 결과(하니스가 넘긴다)
    nb_shares : bep_calculator.delivery_composition() 결과 — 없으면 nbaesong_share=None
    ceilings  : {ad_id: bid_ceiling_calculator.compute_ceiling(...)} — 없으면 ceiling_bid=None

    only_actionable=True: 광고에 실제로 연결된 상품만(links에 있는 것). False면 네이버 전 상품.
    """
    q = db.query(NaverProductBep).filter(NaverProductBep.channel_id == NAVER_CHANNEL_ID)
    if only_actionable:
        pids = list(links.keys())
        if not pids:
            return {
                "rows": [],
                "missing_cost_count": 0,
                "vat_divisor": float(VAT_DIVISOR),
                "data_note": "이 광고에 연결된 판매 상품이 없어 상한 근거를 보여줄 수 없습니다.",
            }
        q = q.filter(NaverProductBep.channel_product_id.in_(pids))

    rows: list[dict] = []
    missing_cost = 0
    for bep in q.all():
        pid = bep.channel_product_id
        ads = links.get(pid, [])
        name = clean_name(bep.product_name or (ads[0]["product_name"] if ads else "") or "이름 없음")
        sp = Decimal(str(bep.selling_price or 0))
        cost = Decimal(str(bep.cost_price or 0))
        rate = Decimal(str(bep.commission_rate or 0))
        logi = Decimal(str(bep.logistics_cost or 0))
        commission = (sp * rate).quantize(Decimal("1"))
        has_cost = bool(bep.has_cost)
        if not has_cost:
            missing_cost += 1

        nb_row = (nb_shares or {}).get(pid) or {}
        nb_share = nb_row.get("nb_share")
        nb_sample = nb_row.get("nb_sample")

        blocked = ""
        if not has_cost:
            blocked = "원가가 입력되지 않아 남는 선을 계산할 수 없습니다. 원가를 넣어 주세요."
        elif bep.bep_roas is None:
            blocked = "판매가에서 비용을 빼면 남는 게 없어(공헌이익 0 이하) 광고로 팔수록 손해입니다."

        ceiling, basis = (None, "")
        market: dict = {"bid": None, "device": None, "observed_on": None}
        borrowed = False
        if not blocked:
            ceiling, basis = _pick_ceiling(ads, ceilings)
            market = _market_bid(db, [a["ad_id"] for a in ads], today)
            # ★상한의 신뢰도를 버리지 않는다(리뷰 P2-4). RPC가 계정 층 폴백이면 그 상한은
            #   여러 상품이 섞인 평균에서 나온 것이라 **낙관(과대) 쪽으로** 틀린다 —
            #   화면이 무조건 "보수적인 값"이라고 말하면 그 행에서는 거짓이 된다.
            borrowed = any(
                (ceilings or {}).get(a["ad_id"], {}).get("rpc_source") == "account"
                for a in ads
            )
            if borrowed and ceiling is not None:
                basis = (
                    f"{basis} 다만 이 상품 자체의 광고 실적이 얇아 계정 평균으로 계산한 값이라 "
                    "실제보다 후하게 나왔을 수 있습니다."
                )

        rows.append({
            "product_name": name,
            "campaign_ids": sorted({a["campaign_id"] for a in ads}),
            "ad_count": len(ads),
            # ── 구성(전부 저장값. 화면에서 뺄셈이 맞도록 VAT 단계를 명시한다) ──
            "selling_price": int(sp),
            "commission_rate": float(rate) if rate else None,
            "commission_won": int(commission),
            "cost_price": int(cost) if has_cost else None,
            "logistics_cost": int(logi),
            "nbaesong_share": float(nb_share) if nb_share is not None else None,
            "nbaesong_sample": int(nb_sample) if nb_sample is not None else None,
            "pre_vat_margin": int(sp - commission - cost - logi) if has_cost else None,
            "contribution_margin": (
                int(Decimal(str(bep.contribution_margin or 0))) if has_cost else None
            ),
            # ── 결과 ──
            "bep_roas": float(bep.bep_roas) if bep.bep_roas is not None else None,
            "target_roas": float(bep.target_roas) if bep.target_roas is not None else None,
            "ceiling_bid": ceiling,
            "ceiling_basis": basis,
            "market_bid": market["bid"],
            "market_bid_device": market["device"],
            "market_bid_observed_on": market["observed_on"],
            "market_bid_position": MARKET_BID_POSITION if market["bid"] is not None else None,
            "blocked_reason": blocked,
            "ceiling_is_borrowed": borrowed,
            "sentence": _sentence(name, ceiling, market, basis, blocked),
        })

    # 상한을 세운 행 먼저(행동 가능한 것부터), 그다음 판매가 큰 순.
    rows.sort(key=lambda r: (r["ceiling_bid"] is None, -(r["selling_price"] or 0)))
    return {
        "rows": rows,
        "missing_cost_count": missing_cost,
        "vat_divisor": float(VAT_DIVISOR),
        "data_note": (
            "판매가·원가·수수료·물류비는 매일 다시 계산해 저장한 값입니다. "
            "공헌이익은 네 숫자를 뺀 뒤 부가세(÷1.1)를 걷어낸 금액입니다. "
            "클릭당 상한은 바로 이어진 주문만 세어 만든 값이라 대체로 보수적입니다 — "
            "다만 그 상품의 광고 실적이 얇아 계정 평균을 빌려 쓴 행은 반대로 후하게 나올 수 "
            "있고, 그런 행에는 따로 표시했습니다."
        ),
    }
