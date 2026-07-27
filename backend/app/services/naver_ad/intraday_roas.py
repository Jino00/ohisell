# intraday_roas.py — intraday_roas SA (단일 책임: 시간당 추정 ROAS·총이익 신호 산출, RL2/D-NAO-60)
"""장중(오늘) hh24 곡선의 시간당 전환건수(conv_cnt, RL1)와 상품 판매가로 누적 추정 ROAS·
추정 총이익을 산출하는 순수 read-only SA. RL3(순위 고삐 판정)가 이 신호를 소비해
"장중 loss면 순위 하향"을 결정한다(D-NAO-59 총이익 절대액 최대화 — ROAS 아님).

이 파일은 쓰기 0·실 네이버 API 호출 0 — DB 조회(adgroup_unit_price)와 순수 계산만 한다
(probe_signal.py와 동일 규율).

★ 정직 경계(docstring, 반드시 숙지 후 소비할 것 — RL3 배선 시 이 경계를 벗어나지 말 것):
① 다상품 광고그룹은 가중평균 판매가라 오차가 생긴다 — 단일상품 광고그룹(파워링크 대부분)은
   정확, 여러 상품이 매핑된 그룹만 근사치.
② 전환지연: 간접전환은 구매까지 ~1일 지연되므로 장중(오늘) 누적 conv_cnt는 실제보다 적게
   잡힌다 → 장중 추정 ROAS/총이익은 구조적으로 **과소추정**. RL3는 이 신호를 "보수적 하향"
   에만 쓰고, 진짜 판정은 D+1 정산(실 conv_amt)이 담당한다. 시간당 단발 값이 아니라 하루
   누적치로만 판단할 것(자정 리셋 전까지 계속 누적되며 뒤로 갈수록 신뢰도 상승).
③ 보정계수 불필요: unit_price는 네이버 convAmt(2.6× 과대 계상, D-NAO-7)가 아니라
   NaverProductBep.selling_price에서 나온다 — 이미 더 정직한 매출 신호이므로 D-NAO-7류
   correction_factor를 여기 곱하지 말 것.
   ★단, selling_price가 **항상** 실거래 가중가는 아니다(D-NAO-95): 주문 이력 0건인 신규
   상품은 product_channel_mapping에 사람이 적어 넣은 판매가(커머스API 실판매가)로 채워진다.
   그 상태에선 이 추정 ROAS가 "정가 기준"이라 할인·옵션가 편차만큼 낙관 쪽으로 틀어질 수
   있다(전환이 생기기 전까진 애초에 계산될 일이 없고, 주문이 쌓이면 실거래가로 자동 교체).
④ ccnt 귀속 미검증: RL1 라이브 관찰에서 hh24 conv_cnt가 naver_ad_daily 일별 그레인과
   직/간접 귀속이 다르게 보이는 사례가 있었다(824088: hh24=2 vs 일별=1). RL3 라이브에서
   D+1 정산 대조로 캘리브레이션 예정(원칙22 — "됐다"는 라이브 증거로만).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import NaverProductBep
from app.services.naver_ad.campaign_target_resolver import weighted_product_value_for_adgroup


def adgroup_unit_price(db: Session, adgroup_id: str) -> dict:
    """그 광고그룹 매핑 상품(들)의 매출가중 평균 판매가·공헌이익·bep_roas.

    campaign_target_resolver.weighted_product_value_for_adgroup 재사용(has_cost=True 상품만
    — 판매가·공헌이익·bep_roas가 모두 같은 원가 기반 BEP 모집단이어야 정합하고, 고삐는
    원가를 아는 상품에만 발동해야 한다). 매핑이 없거나 원가 미확인 상품뿐이면 price=None.

    반환: {"price": Decimal|None, "margin": Decimal|None, "bep_roas": Decimal|None, "source": str}
      source ∈ "product_bep"(매핑·산출 성공) / "unavailable"(매핑 없음/원가 미확인).
    """
    price = weighted_product_value_for_adgroup(db, adgroup_id, NaverProductBep.selling_price)
    if price is None:
        return {"price": None, "margin": None, "bep_roas": None, "source": "unavailable"}
    margin = weighted_product_value_for_adgroup(db, adgroup_id, NaverProductBep.contribution_margin)
    bep_roas = weighted_product_value_for_adgroup(db, adgroup_id, NaverProductBep.bep_roas)
    return {"price": price, "margin": margin, "bep_roas": bep_roas, "source": "product_bep"}


def estimated_intraday_roas(curve: list[dict], unit_price: Decimal | None) -> Decimal | None:
    """hh24 곡선 누적 추정 ROAS = (Σconv_cnt × unit_price) / Σcost.

    curve = naver_sa_ad_fetcher.fetch_entity_hh24 반환형
    [{"hour","imp","clk","cost","conv_cnt","avg_rank"}, ...]. unit_price가 None이거나
    Σcost ≤ 0이면 판정 불가로 None. conv_cnt 키가 없는 레거시 곡선도 .get(...,0)으로 방어.
    """
    if unit_price is None:
        return None
    total_cost = sum(Decimal(str(row.get("cost", 0) or 0)) for row in curve)
    if total_cost <= 0:
        return None
    total_conv = sum(int(row.get("conv_cnt", 0) or 0) for row in curve)
    revenue = Decimal(total_conv) * unit_price
    return round(revenue / total_cost, 4)


def estimated_intraday_profit(curve: list[dict], margin: Decimal | None) -> Decimal | None:
    """hh24 곡선 누적 추정 총이익 = Σconv_cnt × margin − Σcost (D-NAO-59 총이익 절대액 직접 지표).

    margin이 None이면 판정 불가로 None. Σcost는 전환 유무와 무관하게 항상 차감(비용은
    전환 여부와 별개로 발생) — cost=0이어도(예외적으로 곡선이 비어있지 않은 한) 계산은
    성립한다(estimated_intraday_roas와 달리 cost=0을 None으로 취급하지 않음: 총이익은
    비용 0·전환 0이면 0이 정답이지 판정불가가 아니다).
    """
    if margin is None:
        return None
    total_cost = sum(Decimal(str(row.get("cost", 0) or 0)) for row in curve)
    total_conv = sum(int(row.get("conv_cnt", 0) or 0) for row in curve)
    revenue = Decimal(total_conv) * margin
    return revenue - total_cost
