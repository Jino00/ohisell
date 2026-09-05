# models.py — SQLAlchemy 모델 (ohisell 전체 테이블)
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Date, Float, ForeignKey, Index, Integer,
    LargeBinary, Numeric, String, Text, UniqueConstraint, event, false, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ──────────────────────────────────────────────
# 채널 (판매 채널 계정 단위)
# ──────────────────────────────────────────────
class Channel(Base):
    """판매 채널 계정 (쿠팡 Wing계정1, 로켓그로스계정2, 네이버, cafe24 등)"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # coupang / naver / cafe24
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="marketplace"
    )  # marketplace / own / consignment
    account_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 회사 그룹핑 (개인회사 오픽스 / 주식회사 오하이테크 / 주식회사 오하이)
    commission_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    api_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="excel"
    )  # hmac / oauth2_bcrypt / oauth2_code / excel
    api_config_key: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # .env 키 접두사
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sell_type: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )  # 3P / RG / 1P (네이버·cafe24는 None). 상품 연관맵 트랙 D-2/D-6
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    orders: Mapped[list[Order]] = relationship(back_populates="channel")
    settlements: Mapped[list[Settlement]] = relationship(back_populates="channel")
    channel_mappings: Mapped[list[ProductChannelMapping]] = relationship(
        back_populates="channel"
    )
    ad_costs: Mapped[list[AdCost]] = relationship(back_populates="channel")
    manual_revenues: Mapped[list[ManualRevenue]] = relationship(back_populates="channel")
    monthly_fixed_costs: Mapped[list[MonthlyFixedCost]] = relationship(back_populates="channel")


# ──────────────────────────────────────────────
# 상품 마스터 (통합 원가표)
# ──────────────────────────────────────────────
class ProductMaster(Base):
    """통합 상품 마스터 — 채널 무관 원가 기준"""

    __tablename__ = "product_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    channel_mappings: Mapped[list[ProductChannelMapping]] = relationship(
        back_populates="product"
    )
    ad_costs: Mapped[list[AdCost]] = relationship(back_populates="product")


# ──────────────────────────────────────────────
# 상품-채널 매핑
# ──────────────────────────────────────────────
class ProductChannelMapping(Base):
    """채널별 상품 매핑 — 같은 상품의 채널별 식별자 연결"""

    __tablename__ = "product_channel_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product_master.id"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False
    )
    channel_product_id: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # 채널 내 상품번호/옵션ID
    channel_product_name: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    channel_sku: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    selling_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mapping_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto_sync"
    )  # excel_master(엑셀 마스터 진실원천) / auto_sync(쿠팡 상품동기화 자동생성). 상품 연관맵 트랙 D-6
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    product: Mapped[ProductMaster] = relationship(back_populates="channel_mappings")
    channel: Mapped[Channel] = relationship(back_populates="channel_mappings")


# ──────────────────────────────────────────────
# 광고비
# ──────────────────────────────────────────────
class AdCost(Base):
    """채널별 광고비 (API / 엑셀 / ohi-ad-intelligence 연동)"""

    __tablename__ = "ad_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_master.id"), nullable=True
    )
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    ad_spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    ad_revenue: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="excel"
    )  # api / excel / ohi-ad-intelligence
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="ad_costs")
    product: Mapped[Optional[ProductMaster]] = relationship(back_populates="ad_costs")


# ──────────────────────────────────────────────
# 이익률 계산 캐시
# ──────────────────────────────────────────────
class ProfitReport(Base):
    """이익률 계산 결과 캐시"""

    __tablename__ = "profit_report"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "channel_id", "period_start", "period_end", "period_type",
            name="uq_profit_report_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product_master.id"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    period_end: Mapped[datetime] = mapped_column(Date, nullable=False)
    period_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="daily"
    )
    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_of_goods: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ad_spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    vat: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    profit_rate: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    product: Mapped[ProductMaster] = relationship()
    channel: Mapped[Channel] = relationship()


# ──────────────────────────────────────────────
# 기존 테이블 (하위 호환 유지)
# ──────────────────────────────────────────────
class Product(Base):
    """상품 (레거시 — product_master로 이전 예정)"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    inventory_records: Mapped[list[Inventory]] = relationship(
        back_populates="product"
    )


class Order(Base):
    """주문 (채널별 매출 데이터)"""

    __tablename__ = "orders"
    __table_args__ = (
        # 그레인 = 채널 원자 주문라인. 네이버는 productOrderId까지 포함해야 같은 주문+상품이
        # 2개 productOrderId로 분할될 때(부분취소/부분배송, 라이브 1.4%) 수량·매출이 누락되지 않는다.
        # 쿠팡/cafe24는 (order, product)가 이미 원자 그레인 → line_id="" 유지(동작 불변).
        UniqueConstraint(
            "channel_id", "order_number", "platform_product_id", "platform_order_line_id",
            name="uq_order_item",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_master.id"), nullable=True
    )
    order_number: Mapped[str] = mapped_column(String(100), nullable=False)
    platform_product_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # 채널 원자 주문라인 식별자 (네이버=productOrderId). 쿠팡/cafe24는 ""(빈값) — 그레인 분할 불필요.
    platform_order_line_id: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=""
    )
    platform_product_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )  # None이면 배송비 포함 상품
    order_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="delivered"
    )
    # 동기화 시 분류된 PG 결제유형 (예: naverpay_card, kcp_card, kakaopay)
    payment_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # 동기화 시 산출된 PG 수수료액 (profit_calculator는 합산만, 라인 단위)
    commission_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # ── 배송 구분(Jino 지시 2026-07-28) — raw_data JSON 안에만 있던 값을 조회 가능하게 영속화.
    #    판별·파싱은 services/order_delivery.py 한 곳에서만 한다(SA). 배송방식과 배송비 부담은
    #    독립 축이라 각각 저장한다. NULL = 판별 불가(네이버 주문 아님·raw_data 부재·JSON 잘림).
    # 원본 그대로: ARRIVAL_GUARANTEE(N배송) / TODAY / NORMAL …
    delivery_attribute_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    delivery_policy_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # 유료/무료/조건부무료
    shipping_fee_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)      # 선결제/무료 …
    logistics_company_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # N배송 물류사(PG 등)
    # ★우리가 지불한 배송비(건별 스냅샷). 단가는 코드 상수라 개정되면 과거 원가가 소급 왜곡된다
    #   → 주문 시점 판정 단가를 행에 박아 둔다. 고객 수취액은 기존 shipping_cost(의미 불변).
    #   실부담 = shipping_cost_paid − COALESCE(shipping_cost,0) → 조회 시 계산(중복 저장 금지).
    shipping_cost_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # ── 반품 배송 손익 (2026-08-03) ────────────────────────────────────────────
    # 반품 건은 매출에서 제외되지만(REVENUE_EXCLUDED) **배송 손익은 실제로 발생한다** —
    # 우리는 출고비와 회수비를 쓰고 고객에게 반품비를 받는다. 종전엔 셋 다 0으로 빠져
    # 있어 반품이 늘어도 이익이 반응하지 않았다.
    # ★귀속일을 결제일이 아니라 **반품 완료일**로 잡는 이유(Jino 지시 2026-08-03):
    #   결제일에 실으면 이미 마감해서 본 지난달 이익이 반품이 생길 때마다 바뀐다.
    # ★금액은 정액이 아니라 **건별 실측**(raw_data.return.claimDeliveryFeeDemandAmount).
    #   라이브 86건 실측: 5,000원 58건 / 미청구 20건 / 2,500원 4건 / 7,500원 2건 /
    #   N배송 2건은 원천이 명시적으로 `미청구(N배송)`. 정액을 쓰면 미청구 22건이 통째로 틀린다.
    # NULL = 반품 정보 없음(반품 아님·raw_data 부재). 0 = 미청구(실측된 0).
    return_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    return_fee_demand_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # 회수 택배사 — 라이브 86건 전부 HANJIN(N배송 반품도 품고가 아니라 한진이다).
    # 회수 단가가 택배사별로 갈리면 이 컬럼이 판별자가 된다.
    return_collect_company: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # ── 교환 배송 손익 (2026-08-04) ────────────────────────────────────────────
    # ★반품과 성격이 다르다: 교환 주문은 `exchanged` 상태이고 REVENUE_EXCLUDED에 **없다** —
    #   매출·원가·수수료·출고비가 이미 정상 계상돼 있다. 빠진 것은 셋뿐이다:
    #   ①고객에게 받은 교환 배송비 ②회수비 ③**재발송 출고비**(한 주문에 출고가 두 번
    #   일어났는데 엔진은 한 번만 센다). 그래서 교환은 여태 손익에 0회 등장했다.
    # ★귀속일 = 재배송 처리일(raw_data.exchange.reDeliveryOperationDate). 요청일이 아니다.
    # ★EXCHANGE_DONE만 값이 찬다 — REJECT 건은 회수도 재발송도 없다(order_delivery 참조).
    # NULL = 교환 손익 없음(교환 아님·거부됨·정보 없음). 0 = 미청구(실측된 0).
    exchange_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    exchange_fee_demand_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    exchange_collect_company: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # 지원액 필드명이 반품과 다르다(membershipsArrivalGuaranteeClaimSupportingAmount).
    # 라이브 전건 0 — N배송 교환이 0건이기 때문이다. 생기면 이 컬럼이 값을 받는다.
    exchange_fee_support_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    # ★네이버가 대신 부담하는 반품 배송비(claimDeliveryFeeSupportAmount). 라이브 실측:
    #   N배송 반품 2건이 `MEMBERSHIP_ARRIVAL_GUARANTEE` 유형으로 **5,500원 지원**, 일반배송은 0.
    #   N배송 반품에서 고객 청구가 0이었던 이유가 이것이다 — 안 받은 게 아니라 네이버가 낸다.
    #   (2026-08-03 정정: 지원 필드를 안 보고 "고객 미청구=우리 손실"로 읽었다.)
    return_fee_support_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="orders")
    product: Mapped[Optional[ProductMaster]] = relationship()

    @property
    def shipping_cost_net(self) -> Optional[Decimal]:
        """실부담 배송비(우리 지불 − 고객 수취). 지불 미판별이면 None(파생값, 저장 안 함)."""
        if self.shipping_cost_paid is None:
            return None
        return Decimal(str(self.shipping_cost_paid)) - Decimal(str(self.shipping_cost or 0))

    @property
    def is_nbaesong(self) -> bool:
        """N배송(도착보장) 주문인가 — 단일 판별자(deliveryAttributeType)."""
        return self.delivery_attribute_type == "ARRIVAL_GUARANTEE"


class SyncLog(Base):
    """동기화 실행 이력"""

    __tablename__ = "sync_log"

    # channel별 'running' 1건만 허용하는 partial unique index — 동시 동기화 방지의
    # check-then-create 레이스를 DB 레벨로 막는다(Codex 교차검증 2026-06-14 P1, task_dd560245).
    # status!='running' 행(success/error/partial)은 인덱스 대상이 아니라 채널당 무제한 누적 가능.
    # SQLite·PostgreSQL 둘 다 partial index 지원 → dialect별 where 절 모두 명시.
    __table_args__ = (
        Index(
            "uq_sync_log_one_running_per_channel",
            "channel_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    sync_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="orders"
    )  # orders / revenue / products
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running"
    )  # running / success / error / partial
    records_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date_from: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    date_to: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    channel: Mapped[Channel] = relationship()


class OAuthToken(Base):
    """OAuth2 토큰 저장 (네이버/cafe24)"""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), unique=True, nullable=False
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Bearer")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    refresh_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship()


class Settlement(Base):
    """정산 (채널별 정산금, 수수료)"""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    settlement_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    settlement_period_start: Mapped[Optional[datetime]] = mapped_column(
        Date, nullable=True
    )
    settlement_period_end: Mapped[Optional[datetime]] = mapped_column(
        Date, nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    order_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shipping_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="excel"
    )
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="settlements")


class Inventory(Base):
    """재고 입출고 이력"""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "in" (입고) | "out" (출고)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="inventory_records")


# ──────────────────────────────────────────────
# 수동 매출 입력 (로켓배송 등 API 미지원 채널)
# ──────────────────────────────────────────────
class ManualRevenue(Base):
    """채널별 일별 수동 매출 입력 — (channel_id, revenue_date) 유니크 → 재입력 시 덮어쓰기"""

    __tablename__ = "manual_revenue"
    __table_args__ = (
        UniqueConstraint("channel_id", "revenue_date", name="uq_manual_revenue_channel_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    revenue_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    gross_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="manual_revenues")


# ──────────────────────────────────────────────
# 월 고정비 (주문 축에 못 붙는 3PL 비용)
# ──────────────────────────────────────────────
class MonthlyFixedCost(Base):
    """채널별·월별·항목별 고정비 — 3PL 정산서의 입고비·보관료·항공도선료·합포장비.

    ★왜 별도 테이블인가: 이 비용들은 **재고·입고 기반이라 주문 축에 붙지 않는다**.
      손익 엔진은 "주문 1건 → 매출·수수료·배송비·원가"로 도는데 보관료·입고비는 그 축에
      자리가 없어서 여태 **아무 데도 안 잡히고 통째로 누락**돼 있었다(월 ~38만원).
    ★그레인이 월×항목인 이유(Jino 2026-08-04): 원천이 월 단위 정산서이고, 항목을 합쳐
      한 줄로 넣으면 "보관료만 급증" 같은 관찰을 영원히 못 한다. 소비할 땐 일할 배분한다.
    """

    __tablename__ = "monthly_fixed_cost"
    __table_args__ = (
        UniqueConstraint("channel_id", "year_month", "item", name="uq_monthly_fixed_cost"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-07"
    item: Mapped[str] = mapped_column(String(30), nullable=False)       # FIXED_COST_ITEMS 중 하나
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="monthly_fixed_costs")


# ──────────────────────────────────────────────
# 쿠팡 광고 리포트 (XLSX 업로드 상세 지표)
# ──────────────────────────────────────────────
class CoupangAdReport(Base):
    """쿠팡 pa_daily_keyword XLSX에서 추출한 날짜별 광고 성과 지표"""

    __tablename__ = "coupang_ad_report"
    __table_args__ = (
        UniqueConstraint("report_date", "sell_type", "vendor_id", name="uq_coupang_ad_report"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    sell_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 3P / 2P / Retail
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ad_spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sales_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 광고 옵션 그레인 (조망 결합축 — 광고측 옵션ID 보존)
# ──────────────────────────────────────────────
class CoupangAdOptionDaily(Base):
    """쿠팡 광고 XLSX의 옵션ID 단위 일별 성과 — 광고⨝상품⨝주문 3자 조인 광고축.

    트랙 D-9: coupang_ad_report(롤업)는 옵션ID를 버린다. 이 테이블이 옵션 그레인을 보존.
    ad_option_id([8] 광고집행 옵션ID)로 비용·노출·클릭이, conv_option_id([10] 전환매출 옵션ID)로
    매출·주문이 귀속된다. 둘은 보통 같지만 간접전환 시 갈릴 수 있어 분리 보존.
    ad_option_id ⨝ coupang_product_item.vendor_item_id ⨝ Order.platform_product_id 로 조인.
    """

    __tablename__ = "coupang_ad_option_daily"
    __table_args__ = (
        UniqueConstraint(
            "report_date", "vendor_id", "sell_type", "ad_option_id", "conv_option_id",
            name="uq_coupang_ad_option_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)
    sell_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 3P / 2P / Retail
    ad_option_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # [8] 비용·노출 귀속
    conv_option_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # [10] 매출·주문 귀속
    # 상품명([7]·[9]) — XLSX가 같은 행에 실어 오는 라벨. 1P Retail 옵션은 coupang_product_item
    # (3P product_sync 산물)에 없어 조인으로 못 붙인다 → 적재 시점에 보존한다.
    # nullable: 이 컬럼 추가(2026-08-03) 이전 행과, 이름이 빈 행이 있다. 표시는 옵션ID로 폴백.
    ad_product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    conv_product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ad_spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sales_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 상품 옵션 스냅샷 (조망 결합축)
# ──────────────────────────────────────────────
class CoupangProductItem(Base):
    """쿠팡 상품 옵션(vendorItemId) 단위 조망 스냅샷 — 광고⨝주문⨝상품 결합축.

    트랙 D-8: vendorItemId는 account(vendor_id) 귀속. product_sync가 계정별 upsert.
    Order.platform_product_id(문자열 옵션ID)·광고 XLSX 옵션ID와 vendor_item_id로 조인.
    """

    __tablename__ = "coupang_product_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )  # 옵션ID = 결합키
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)  # A01564720 등(소유 계정)
    seller_product_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    seller_product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    item_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    external_vendor_sku: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )
    sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    supply_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)  # 공급가(원가성)
    sale_agent_commission: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # 판매수수료%
    max_buy_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 등록재고
    amount_in_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 실시간재고
    on_sale: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 판매상태
    status_name: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 승인완료 등
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 로켓그로스 사이즈(skuInfo) — 보관비 CBM 토대 (P3/D-14). RG 상품조회(#1)에서만 채워짐.
    width_mm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 너비(mm)
    length_mm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 길이(mm)
    height_mm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 높이(mm)
    weight_g: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 중량(g)
    net_weight_g: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 순중량(g)
    cbm: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 6), nullable=True
    )  # 부피(m³) = w×l×h(mm)/1e9 — 보관비 단가 곱셈 기준
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 로켓그로스 (재고·RG주문 — P3/D-14)
# ──────────────────────────────────────────────
class CoupangRgInventory(Base):
    """로켓창고(쿠팡 풀필먼트) 옵션별 실재고 — 재고관리 + 결합축(D-8).

    명세: docs/references/05_coupang_rocketgrowth_api_specs.md §3 (rg/inventory/summaries).
    grain = vendor_item_id(옵션ID, 결합키). orderable_qty=주문가능 총수량, sold_30d=최근30일 판매수.
    ⚠️ 입고일/보관경과일은 공식 API에 없음(D-14) → 보관비 실측은 정산(P4), CBM 모델은 별도지표.
    """

    __tablename__ = "coupang_rg_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )  # 옵션ID = 결합키
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)  # 소유 계정
    external_sku_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    orderable_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 주문가능 총수량
    sold_30d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 최근30일 판매수량
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRgOrderItem(Base):
    """로켓그로스 주문 — 옵션 그레인 (향후 RG 매출). 기존 Order와 분리(이중계산 방지, D-14).

    명세: docs/references/05_coupang_rocketgrowth_api_specs.md §4·§5 (rg/orders).
    grain = (order_id, vendor_item_id). vendor_item_id로 광고·상품·재고 동일 결합축(D-8).
    ⚠️ 단가 필드 API 불일치(목록=unitSalesPrice, 단건=salesPrice) → Harness가 둘 다 방어.
    현재 RG 매출 희소 — 결합엔진(intelligence) 편입은 RG 매출 본격화 시.
    """

    __tablename__ = "coupang_rg_order_item"
    __table_args__ = (
        UniqueConstraint("order_id", "vendor_item_id", name="uq_coupang_rg_order_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # RG 주문번호
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 옵션ID = 결합키
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)  # 소유 계정
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    sales_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 판매수량
    unit_sales_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )  # 단가(unitSalesPrice|salesPrice)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # KRW 등
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 결제일시(ms epoch/ISO → datetime 정규화)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 RG 입고(inbound) — 발송→판매개시 리드타임 (트랙 RG-Replenishment S1, D-1)
# ──────────────────────────────────────────────
class CoupangRgInbound(Base):
    """로켓그로스 FC 입고 — 옵션 그레인. 발송→판매개시 리드타임 산출의 원천(트랙 D-1).

    출처: Wing 내부 API GET wing.coupang.com/tenants/rfm-inbound/data/inbound/search (세션쿠키).
    공식 Open API엔 입고 엔드포인트 없음(전수확인) → 이 기능 한해 Wing 내부 API 사용(D-1).
    grain = (account_key, inbound_id, vendor_item_id). 입고 1건(inbound_id)에 여러 옵션 포함.
    ★리드타임 = shipment_created_at(statusId 3=발송) → stowing_at(statusId 7=판매개시).
    입고건 식별자 필드명은 라이브 실응답으로 확정(raw_json 보관 → 파싱 미세조정). 표본 적음(반년 6건).
    """

    __tablename__ = "coupang_rg_inbound"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "inbound_id", "vendor_item_id", name="uq_coupang_rg_inbound"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    inbound_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # 입고건 식별자
    vendor_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 소유 계정
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 옵션ID = 결합키(D-8)
    sku_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cached_sku_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    requested_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 발송 요청수량
    received_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # FC 입고수량
    stowed_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 적치(판매가능)수량
    shipment_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # statusId 3 = 발송시점
    stowing_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )  # statusId 7 = 판매개시
    lead_time_days: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # 파생: stowing - shipment (일)
    inbound_created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # 입고 생성일(content.createdAt)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 옵션 원본(스키마 진화 대비)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 광고비 일별 (advertising.coupang.com Wing 내부 API)
# ──────────────────────────────────────────────
class CoupangAdCostDaily(Base):
    """advertising.coupang.com 광고 리포트 응답 — 일별 확정 광고비.

    day_cost: 해당 날짜 확정 광고비(원, report/SALES DELIVERED_AD_COST = 집행/PA).
    all_day_cost: 해당 날짜 전체 광고비(원, report/SALES ALL_DELIVERED_AD_COST = 비-PA 포함). 전체≥집행. (S5a/D-15)
    conv_sales: 해당 날짜 광고전환매출(원, report/SALES AD_ATTRIBUTED_SALES). ROAS=conv_sales/day_cost.
    month_cost: 구 report/cost 잔재(참고용). vendor_id: advertiser/adNodeId 키.

    적재 소스: Mac 페처가 report/SALES로 최근 7일 날짜별 확정값을 받아 ingest.
    같은 날짜를 다시 받으면 확정치로 교정(과거 partial 스냅샷 대체).
    """

    __tablename__ = "coupang_ad_cost_daily"
    __table_args__ = (
        UniqueConstraint("cost_date", "vendor_id", name="uq_coupang_ad_cost_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cost_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    day_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # S5a/D-15: all_day_cost = report/SALES ALL_DELIVERED_AD_COST(전체 광고비, 비-PA 포함).
    # day_cost(=DELIVERED_AD_COST, 집행/PA)와의 차 = 비-PA(브랜드/디스플레이 등). 전체≥집행.
    all_day_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_sales: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    month_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangAdEntitySnapshot(Base):
    """쿠팡 광고 설정 스냅샷 — diff의 "전" 값. (account, entity_type, entity_id)당 최신 1행.

    트랙 coupang-ad-change-log D-CAC-4. 원천 = `POST /marketing/tetris-api/campaigns`
    (오픽스·오하이테크 동일 엔드포인트, `groupList`에 광고그룹 중첩).

    ★settings_json에는 **설정 필드만** 담는다. 성과 필드(spentBudget·averageTimeBudgetUtilRate 등)를
      섞으면 매일 아침 "전 캠페인 수정됨"이 뜬다 — 실측: spentBudget이 20분에 6,501→6,592로 움직였다
      (네이버에서 소재 editTm이 상품 피드 재적용으로 전진해 ad_edit이 229:4로 오염된 것과 같은 함정).

    ★present: 마지막 전량 조회에 이 엔티티가 있었나. 삭제 판정용 — is_active=False(Off)와 구분해야 한다
      (Off는 목록에 남고, 삭제는 사라진다).

    ★settings_json은 **활성 조회(전체 필드)에서만** 갱신한다. 전량 조회는 id·name·isActive만 쓰므로
      그걸로 덮으면 설정이 통째로 날아간다(D-CAC-3: 내용 diff는 활성만).
    """

    __tablename__ = "coupang_ad_entity_snapshot"
    __table_args__ = (
        UniqueConstraint("account", "entity_type", "entity_id",
                         name="uq_coupang_ad_entity_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(12), nullable=False, index=True)  # ofix / ohitech
    entity_type: Mapped[str] = mapped_column(String(12), nullable=False)          # campaign / adgroup
    entity_id: Mapped[str] = mapped_column(String(24), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(24), nullable=False, default="", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 쿠팡이 준 updatedAt(UTC naive). 발생 시각 앵커 — 네이버 소재 editTm과 같은 역할.
    src_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CoupangAdChangeLog(Base):
    """쿠팡 광고 설정 변경 1건 — 「수정 사항」 쿠팡판의 표시 원천.

    ★쿠팡은 **모든 변경이 외부다**. 우리가 쿠팡 광고를 쓰는 경로가 없어서 네이버의
      naver_change_log(우리 실집행) 같은 원천이 원리적으로 안 생긴다. 전량이 스냅샷 diff 유래다.

    op: created / turned_on / turned_off / deleted / field_change
    occurred_at: 귀속 시각. time_basis가 'src'면 쿠팡 updatedAt(진짜 발생 시각),
      'detected'면 우리가 알아챈 시각이다 — ★모르는 걸 아는 척하지 않는다.
      (네이버에서 감지일로 귀속했다가 07-30 변경을 08-03으로 잡은 실사고가 있었다.)

    ★변경 주체는 기록하지 않는다 — isAgencyManaged는 '현재 관리 주체'지 '이번 변경을 누가 했나'가
      아니다. 네이버처럼 우리/대행사/Jino를 가르는 건 쿠팡에선 근거가 없다.

    유니크 키가 (account, entity_type, entity_id, op, field, occurred_at)인 이유: 같은 스냅샷을
      두 번 넣어도 행이 안 늘어야 한다(회차 재실행·백필 idempotent).
    """

    __tablename__ = "coupang_ad_change_log"
    __table_args__ = (
        UniqueConstraint("account", "entity_type", "entity_id", "op", "field", "occurred_at",
                         name="uq_coupang_ad_change_log"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(12), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(24), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(24), nullable=False, default="", index=True)
    entity_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    # field: field_change일 때만 채운다. 유니크 키에 들어가므로 NULL 대신 ''를 쓴다
    # (SQLite에서 NULL은 서로 달라 중복이 막히지 않는다).
    field: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    time_basis: Mapped[str] = mapped_column(String(10), nullable=False, default="detected")  # src/detected
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # ── 슬라이스2: 두 번째 원천(쿠팡 변경 이력 API) ──────────────────────────
    # 'snapshot' = 우리 스냅샷 diff / 'coupang' = POST tetris-api/change-history/events-simple.
    # ★겹치는 축(예산·목표ROAS·On/Off)에선 **coupang이 이긴다** — 쿠팡은 전/후 값과 정확한
    #   실행 시각을 주지만 우리 스냅샷은 updatedAt으로 시각만 안다.
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="snapshot")
    # 쿠팡 executionId(UUID). 같은 회차를 두 번 돌려도 행이 안 늘게 하는 자연 키.
    external_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 구조화된 부가정보 — 소재 변경의 옵션ID 목록(added/removed), VIID 개수 등.
    # ★before/after 두 칸으로는 "어떤 옵션이 붙었나"를 담을 수 없어서 따로 둔다.
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CoupangWingCookie(Base):
    """Wing 내부 API 세션쿠키 시크릿 — 계정별 (트랙 D-5, S1-b).

    Wing 내부 API는 셀러 로그인 세션쿠키 인증. Jino가 UI에 cURL 통째 붙여넣으면 쿠키/xsrf 추출·저장.
    ⚠️ 세션쿠키=민감정보 → cookie_blob·xsrf_token은 Fernet 암호문으로 저장(평문·로그 노출 금지).
    last_success_at = 일일 sync 성공 시각 = 쿠키 만료 측정기(302/401 발생 = 만료, status=red).
    D-5: 수동 붙여넣기로 시작 + 만료주기 측정 → 잦으면 자동화 추가.
    """

    __tablename__ = "coupang_wing_cookie"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )  # COUPANG_WING1 등
    cookie_blob: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Fernet 암호문(쿠키 전체)
    xsrf_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Fernet 암호문(x-xsrf-token)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default="unknown"
    )  # green/red/unknown
    last_error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # 마지막 실패의 **분류**(refresh_contract.KIND_*). last_error 문자열과 달리 기계가 읽는다.
    # ★왜 컬럼인가(2026-08-22, 계약 CONTRACT_collection_stability_s1 W1): 종전엔 「로그인 필요」의
    #   유일한 흔적이 last_error 안의 "[로그인 필요 …]" 문구였고 프론트가 그걸 문자열 매칭으로
    #   읽었다 — 문구를 바꾸면 화면이 조용히 깨지고, 무엇보다 «상태»가 아니라 «이벤트»라
    #   버튼을 눌러 실패해 봐야만 로그인 필요를 알 수 있었다(2026-08-22 각 계정 9회 재발견).
    #   None = 아직 분류된 실패가 없음(성공했거나 한 번도 실패 안 함).
    last_error_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 쿠키 저장 시각
    last_success_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # 마지막 sync 성공(=만료 측정)
    # last_success_at의 짝 — 마지막 실패 시각. Mac 페처가 실패를 보고할 때 찍고, 성공 시 클리어.
    # ★없으면 UI가 "실패"와 "아직 진행 중"을 구분 못 한다(같은 문구로 반복 실패 시 특히).
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 대시보드 "광고비 갱신" 버튼이 set. 이 값이 있으면 페처가 다음 폴링에서 headful fetch를 수행.
    # ★2026-07-27부터 claim은 이 값을 지우지 않는다(lease 방식) — 성공(mark_success) 또는
    #   재시도 소진/로그인필요(report_failure)에서만 소멸한다. refresh_contract.py 참조.
    refresh_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # lease(임대) 취득 시각 — "지금 페처가 이 요청을 붙잡고 일하는 중". TTL(기본 20분) 지나면
    # 만료로 보고 다른 폴이 재claim한다(데몬이 보고 없이 죽는 경우의 안전망).
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 이 요청으로 claim된 횟수(1-based). MAX_ATTEMPTS(3) 도달 후 실패하면 요청을 소멸시킨다.
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangVendorSummaryDaily(Base):
    """쿠팡 Wing 판매분석(vendor-summary) 공식 GMV — 일별×등록유형 (Wing 세션 자동화 트랙 S2, D-5).

    소스: m-wing.coupang.com /tenants/rfm-ss/api/business-insight/vendor-summary (ref 18).
    Mac 헤드풀 페처(tools/wing_browser_fetcher.py)가 브라우저측 fetch → prod push → 여기 적재.
    백엔드 requests 직접 호출 금지(cf_clearance 재생 불가, D-5) — ingest 수신 전용.

    grain: (summary_date, account_key, registration_type). registration_type:
      NORMAL=3P 마켓플레이스 / RFM=로켓그로스(RG) (ref 18). gmv=원 단위(쿠팡 공식 매출).
    용도: revenue_reconcile Harness가 우리 revenue_3p/revenue_rg와 닫힌일 드리프트% 대조(읽기전용).
    같은 날짜를 다시 받으면 확정치로 교체(snapshot upsert) — 준실시간 lastRefresh로 회전.
    """

    __tablename__ = "coupang_vendor_summary_daily"
    __table_args__ = (
        UniqueConstraint(
            "summary_date", "account_key", "registration_type",
            name="uq_coupang_vendor_summary_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 로그인 계정(COUPANG_WING1 등)
    registration_type: Mapped[str] = mapped_column(String(10), nullable=False)  # NORMAL(3P) / RFM(RG)
    gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 쿠팡 공식 GMV(원)
    units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 판매수량
    last_refresh: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # 쿠팡 lastRefreshTimestamp
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangVendorItemSalesDaily(Base):
    """쿠팡 Wing 판매분석 «일자×옵션» 정본 매출 축 (D-CPP-36, 2026-08-10).

    소스: m-wing.coupang.com /tenants/rfm-ss/api/business-insight/vi-detail-search
      (2026-08-10 라이브 정찰). 요약축(CoupangVendorSummaryDaily)과 **같은 세션·같은 회차**에
      수집한다 — 그래야 보존식(Σ옵션 == 요약)이 같은 시점을 비교한다.
      백엔드 requests 직접 호출 금지(cf_clearance 재생 불가, D-5) — ingest 수신 전용.

    grain: (account_key, sale_date, vendor_item_id). 같은 날짜를 다시 받으면 교체(snapshot upsert).
    vendor_item_id는 `CoupangRevenueFee.vendor_item_id`(수수료 요율 SoT)·`Order.platform_product_id`와
      **같은 문자열 옵션ID**다 → 옵션별 매출↔수수료↔주문이 캐스팅 없이 조인된다.
      ★`externalSkuIds`는 실측 전건 빈 배열이라 우리 SKU 코드로는 조인할 수 없다(창작 금지).

    gmv/units_sold 음수 허용: 쿠팡 GMV = 판매액 − 환불액이라 환불 초과일은 정당하게 음수다
      (요약축이 이미 같은 정책 — 그때 «비용은 0 이상» 가정을 복제해 45일 백필이 통째로 막혔다).

    이번 범위 밖: 이 축을 손익 엔진이 소비하는 재계산(종합조망 `wing_used` 판정은 요약축 그대로).
      UV/PV/검색량 등은 raw_metrics(JSON)에 원본만 보존한다 — 재조회가 봇 감지 때문에 비싸다.
    """

    __tablename__ = "coupang_vendor_item_sales_daily"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "sale_date", "vendor_item_id",
            name="uq_coupang_vendor_item_sales_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vendor_item_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 옵션ID = 결합키
    registration_type: Mapped[str] = mapped_column(String(10), nullable=False)  # NORMAL(3P) / RFM(RG)
    item_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    gmv: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 쿠팡 공식 GMV(원)
    units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_metrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # businessInsightsMetricsResponse 원본 JSON
    last_refresh: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 반품/취소 (순매출 차감 회계축 — P2)
# ──────────────────────────────────────────────
class CoupangReturnItem(Base):
    """쿠팡 반품/취소 옵션(vendorItemId) 단위 — 순매출 차감 결합축 (트랙 P2, D-3).

    명세: docs/references/03_coupang_returns_api_specs.md
    한 반품접수(receipt_id)에 여러 옵션이 포함되므로 (receipt_id, vendor_item_id) 그레인.
    vendor_item_id ⨝ orders.platform_product_id 로 조인 → 순매출 = 매출 − (cancel_count × 단가).
    withdrawn=True(반품철회)는 차감에서 제외. 시스템은 사실/지표 정리만(D-3, 전략판단 없음).
    """

    __tablename__ = "coupang_return_item"
    __table_args__ = (
        UniqueConstraint("receipt_id", "vendor_item_id", name="uq_coupang_return_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 취소(반품)접수번호
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 옵션ID = 결합키 (orders.platform_product_id 조인)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)  # 소유 계정 (A01564720 등)
    order_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 주문번호
    receipt_type: Mapped[str] = mapped_column(String(10), nullable=False)  # RETURN / CANCEL
    receipt_status: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # RETURNS_COMPLETED 등 진행상태
    cancel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 취소 수량(부분반품)
    purchase_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 원 주문 수량
    seller_product_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    seller_product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    vendor_item_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    fault_by_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # COUPANG/VENDOR/CUSTOMER/WMS/GENERAL
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    release_status: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # Y/N/S
    pre_refund: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 선환불 여부
    withdrawn: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # 반품철회 → 차감 제외
    requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )  # 접수시간(createdAt)
    modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangExchange(Base):
    """쿠팡 교환요청 — exchangeId 단위 운영 기록 (트랙 P2).

    명세: docs/references/03_coupang_returns_api_specs.md §2.
    교환은 순매출 차감 없음(상품 교체, 환불 아님 — 카탈로그 '회계 영향 작음'). 운영 가시성용.
    """

    __tablename__ = "coupang_exchange"
    __table_args__ = (
        UniqueConstraint("exchange_id", "vendor_item_id", name="uq_coupang_exchange"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vendor_item_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)
    order_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    exchange_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # RECEIPT/PROGRESS/SUCCESS/REJECT/CANCEL
    order_delivery_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    refer_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 접수경로
    vendor_item_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    exchange_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 정산 도메인 (회계 진짜 순이익 — P4, D-10/D-11)
# ──────────────────────────────────────────────
class CoupangRevenueFee(Base):
    """쿠팡 매출내역(revenue-history) 옵션 그레인 — 실제 적용 판매수수료율(serviceFeeRatio) 적재.

    명세: docs/references/04_coupang_fees_map.md §6-1.
    revenue-history는 거래(orderId) 단위 + items[] 옵션 중첩. 이 테이블은 옵션 1행으로 평탄화.
    grain = (order_id, vendor_item_id, recognition_date, sale_type). saleType=SALE/REFUND 모두 포함
    (REFUND는 음수 — 사실 그대로, D-3). service_fee_ratio ↔ coupang_product_item.sale_agent_commission
    비교가 D-10/D-11 수수료 감사의 축. vendor_item_id ⨝ 광고·상품·주문·반품 동일 결합축(D-8).
    delivery_fee_*는 주문(거래) 헤더값 — 한 주문의 모든 옵션 행에 반복(합산 시 order_id distinct).
    """

    __tablename__ = "coupang_revenue_fee"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "vendor_item_id", "recognition_date", "sale_type",
            name="uq_coupang_revenue_fee",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 주문번호
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 옵션ID = 결합키
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False)  # 소유 계정
    sale_type: Mapped[str] = mapped_column(String(20), nullable=False)  # SALE / REFUND 등
    sale_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 판매일
    recognition_date: Mapped[Optional[datetime]] = mapped_column(
        Date, nullable=True, index=True
    )  # 매출 인식일(조회 기준)
    settlement_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    final_settlement_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # sellerProductId
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    vendor_item_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sale_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    service_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    service_fee_vat: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    service_fee_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # ★실제 적용 판매수수료율(%) — D-10/D-11 비교축
    settlement_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    coupang_discount_coupon: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    seller_discount_coupon: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    downloadable_coupon: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    courantee_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    store_fee_discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    external_seller_sku_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 배송비(주문 헤더 — 옵션 행에 반복, 합산 시 order_id distinct)
    delivery_fee_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_fee_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_fee_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangSettlementPayout(Base):
    """쿠팡 지급내역(settlement-histories) 정산 단위 — 주/월 통장 지급액·서비스이용료·차감.

    명세: docs/references/04_coupang_fees_map.md §6-2. 응답은 JSON 배열 직접 반환(인식월 단위).
    grain = (vendor_id, settlement_type, settlement_date, revenue_recognition_date_from,
    revenue_recognition_date_to). sellerServiceFee(월 55k)·deductionAmount·finalAmount 회계 검증축.
    bank 정보(예금주/은행/계좌)는 PII라 저장하지 않음(보안 원칙). D-3 사실 정리만.
    """

    __tablename__ = "coupang_settlement_payout"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id", "settlement_type", "settlement_date",
            "revenue_recognition_date_from", "revenue_recognition_date_to",
            name="uq_coupang_settlement_payout",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    settlement_type: Mapped[str] = mapped_column(String(20), nullable=False)  # WEEKLY/MONTHLY 등
    settlement_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    revenue_recognition_year_month: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    revenue_recognition_date_from: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    revenue_recognition_date_to: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    total_sale: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    service_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    settlement_target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    settlement_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    last_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    pending_released_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    seller_discount_coupon: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    downloadable_coupon: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    dedicated_delivery_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    seller_service_fee: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )  # 판매자 서비스이용료(월 55k)
    courantee_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    courantee_customer_reward: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    deduction_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    debt_of_last_week: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    final_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )  # 최종 지급액
    store_fee_discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # DONE 등
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangFeeChangeLog(Base):
    """쿠팡 판매수수료율 이상 감사 로그 (트랙 D-13 자기기준선; D-11 기준선 교체).

    ⚠️ D-13: saleAgentCommission(등록율)이 라이브 전부 0(판매대행 수수료, 카테고리 판매수수료
    아님)이라 기준선으로 못 씀. 새 방식 = 각 옵션의 정착 실측율(service_fee_ratio history mode)을
    기준선으로, 같은 옵션이 기간 내 다른 율을 보이면 기록:
      change_type="rate_drift" → 율 변동/과오청구 의심. 자동 판단·자동 수용 안 함, Jino 수동 판정.
    컬럼 재사용: registered_ratio=기준선(정착율), observed_ratio=이탈율, reauthored_ratio=미사용(None;
    카테고리율 교차 P6 여지). grain=(vendor_item_id, observed_ratio, registered_ratio) 멱등.
    (구 D-11 legitimate·자동 sale_agent_commission 갱신·권위 재확인 로직은 폐기.)
    원칙18-9(피드백 루프)·D-3(시스템은 사실만, 판단은 Jino).
    """

    __tablename__ = "coupang_fee_change_log"
    __table_args__ = (
        UniqueConstraint(
            "vendor_item_id", "observed_ratio", "registered_ratio",
            name="uq_coupang_fee_change_log",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_item_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    account_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    vendor_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    registered_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # 감지 당시 DB 등록율(sale_agent_commission)
    observed_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # 실측율(revenue-history serviceFeeRatio)
    reauthored_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # 권위 재확인한 상품API saleAgentCommission
    change_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # legitimate / anomaly
    order_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # 감지된 거래
    recognition_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class CoupangCoupon(Base):
    """쿠팡 쿠폰 운영 현황 (P5) — 즉시할인쿠폰(fms) + 다운로드쿠폰(marketplace) 통합, couponId 그레인.

    명세: docs/references/06_coupang_coupon_api_specs.md §E #18·#15·#10.
    coupon_kind=INSTANT(즉시할인, 판매가 직접 할인) / DOWNLOAD(고객 다운로드 쿠폰).
    ★회계축 아님(D-3): 실제 셀러 부담 할인액은 정산(P4) revenue-history의 seller_discount_coupon에
      이미 실측 차감됨(04 §3). 이 테이블은 "어떤 쿠폰이 진행중인가" 운영 현황만(보조).
    grain=(account_key, coupon_kind, coupon_id). vendor_id 귀속(D-8).
    """

    __tablename__ = "coupang_coupon"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "coupon_kind", "coupon_id", name="uq_coupang_coupon"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)  # COUPANG_WING1 등
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    coupon_kind: Mapped[str] = mapped_column(String(12), nullable=False)  # INSTANT / DOWNLOAD
    coupon_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    contract_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    promotion_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 즉시=promotionName, 다운로드=title
    status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )  # 즉시: STANDBY/APPLIED/PAUSED/EXPIRED/DETACHED, 다운로드: couponStatus
    discount_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # 즉시 type(RATE/FIXED_WITH_QUANTITY/PRICE), 다운로드 typeOfDiscount(RATE/PRICE)
    discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)  # 할인율/할인액
    max_discount_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    wow_exclusive: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 즉시: 와우회원 전용
    applied_option_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 다운로드: 적용 옵션수
    usage_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)  # 다운로드: 사용량
    # ── 프로모션 손익 레이어(트랙 coupang-promo-pnl, D-CPP-3) ──
    # ★셀러 부담 실사용 할인액의 **권위값**. Wing 화면의 쿠폰별 "사용 금액"(예: 94177420 = 156,000원).
    #   위 usage_amount(다운로드쿠폰 '사용량')와는 다른 축이라 컬럼을 분리한다 — 의미를 겹치면
    #   나중에 어느 쪽이 우리 실부담인지 구분할 수 없다.
    #   ⚠️ 쿠팡 Open API(fms)에는 이 값이 없다(ref 06 §E 전수 대조) → coupon_sync가 채우지 않는다.
    #   ingest 경로(/coupon/used-amount/ingest)로만 들어오며, 출처는 used_amount_source로 항상 라벨링.
    used_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    used_amount_source: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # wing_ui | wing_api | manual (추정값 금지 — 출처 없는 값은 넣지 않는다)
    used_amount_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangCouponItem(Base):
    """쿠팡 즉시할인쿠폰 아이템 — 옵션(vendorItemId)별 쿠폰 적용 (P5, D-8 결합축).

    명세: docs/references/06_coupang_coupon_api_specs.md §E #20·#17.
    한 쿠폰(couponId)에 여러 옵션(vendorItemId)이 묶임. vendor_item_id ⨝ 광고·상품·주문·반품 동일축.
    grain=(account_key, coupon_item_id). couponItemId가 전역 식별자.
    """

    __tablename__ = "coupang_coupon_item"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "coupon_item_id", name="uq_coupang_coupon_item"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    coupon_item_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    coupon_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    vendor_item_id: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # 옵션ID = 결합키(D-8)
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # STANDBY/APPLIED/PAUSED/EXPIRED
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangCouponBudget(Base):
    """쿠팡 쿠폰 예산/계약 현황 (P5) — 계약서(#6) + 월별 예산(#4) 결합, (contract_id, target_month) 그레인.

    명세: docs/references/06_coupang_coupon_api_specs.md §E #4·#6.
    쿠폰 운영의 예산 토대(즉시할인쿠폰 분담율·월예산·사용액). 계약 메타(분담율·기간)도 함께 저장.
    target_month 없는 계약 메타만 있는 행은 target_month='' 으로 적재(계약 자체 현황).
    """

    __tablename__ = "coupang_coupon_budget"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "contract_id", "target_month",
            name="uq_coupang_coupon_budget",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    contract_id: Mapped[str] = mapped_column(String(20), nullable=False)
    target_month: Mapped[str] = mapped_column(String(7), nullable=False, default="")  # yyyy-MM 또는 ''(계약메타만)
    # 예산현황(#4)
    vendor_share_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)
    total_budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    used_budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # 계약 메타(#6)
    vendor_contract_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    seller_share_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)
    coupang_share_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)
    contract_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # CONTRACT_BASED 등
    contract_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    contract_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# P6 CS — 고객문의 현황 (운영 보조)
# ──────────────────────────────────────────────
class CoupangInquiry(Base):
    """쿠팡 고객문의 경량 적재 (P6 CS) — 미답변 현황 운영 지표.

    명세: docs/references/10_coupang_cs_api_specs.md #1(상품Q&A) + #3(CS이관).
    DB 그레인: (account_key, inquiry_type, inquiry_id) UNIQUE.
    answered=False 건이 운영 지표 핵심. answered=True 건도 보존(이력).
    """

    __tablename__ = "coupang_inquiry"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "inquiry_type", "inquiry_id",
            name="uq_coupang_inquiry",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    inquiry_type: Mapped[str] = mapped_column(String(20), nullable=False)  # online / call_center
    inquiry_id: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # ANSWERED/NOANSWER 등
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inquired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverSettlementDaily(Base):
    """네이버 일별 정산 내역 (커머스 API /v1/pay-settle/settle/daily, 트랙 N1).

    실측 정산금액·수수료 — 패널 정산 표시 + 이익 정밀화 다리(D-5).
    DB 그레인: settle_expect_date UNIQUE (정산 예정일 1행).
    """

    __tablename__ = "naver_settlement_daily"
    __table_args__ = (
        UniqueConstraint("settle_expect_date", name="uq_naver_settle_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settle_basis_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    settle_basis_end: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    settle_expect_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # 정산 예정일
    settle_complete_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    settle_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)        # 정산금액
    pay_settle_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)     # 결제정산(기준)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)     # 수수료정산(음수)
    benefit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)        # 혜택정산
    payholdback_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)    # 지급보류
    settle_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)                   # ACCOUNT/CHARGE_AMT
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverSettlementCase(Base):
    """네이버 건별 정산 내역 (커머스 API /v1/pay-settle/settle/case, 트랙 N1·D-6).

    productOrderId 그레인의 실측 수수료 — 이익 정밀화용. orders와 (order_id, product_id)로
    매칭해 주문시점 예상 수수료를 실측 수수료로 교체(하이브리드 폴백).
    수수료(commission) 필드는 네이버 응답 부호 그대로 보존(음수). 매칭 시 부호 반전.
    """

    __tablename__ = "naver_settlement_case"
    __table_args__ = (
        UniqueConstraint("product_order_id", name="uq_naver_settle_case"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_order_id: Mapped[str] = mapped_column(String(40), nullable=False)            # 상품주문번호(배송비/기타비용 번호 포함)
    order_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)        # 주문번호 (매칭 키)
    product_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)  # 상품번호 (매칭 키, DELIVERY는 NULL)
    product_order_type: Mapped[str] = mapped_column(String(20), nullable=False)          # PROD_ORDER/DELIVERY/EXTRAFEE/...
    settle_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)        # NORMAL_SETTLE_ORIGINAL/...
    product_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pay_settle_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)         # 결제정산금액(기준)
    total_pay_commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)      # 네이버페이 관리 수수료(음수)
    selling_interlock_commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 매출연동 수수료(음수)
    free_installment_commission: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)   # 무이자할부 수수료(음수)
    benefit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)            # 혜택 정산금액
    settle_expect_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)      # 정산예정금액(실측)
    pay_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)  # 결제일 (매칭/조회 그레인)
    settle_expect_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    settle_complete_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverClaimSettlementProbe(Base):
    """네이버 클레임(반품/교환) 주문의 정산 구조 관측 로그 — N배송 반품 회수비 프로브.

    ★존재 이유(2026-08-03): 반품 배송비 수입을 이익에 반영하려는데 **N배송(도착보장) 반품의
      정산 구조를 아무도 모른다**. 라이브 실측:
        - 비N배송(TODAY/NORMAL) 반품·교환 → productOrderType='DELIVERY' 행이 뜬다.
          금액은 5,000원 지배적(45/50건)·2,500원 3건·7,500원 2건 = 고객이 낸 반품비=우리 수입.
        - N배송 반품은 468건 중 2건뿐(2026072553216341 결제 07-25 / 2026072852172181 결제 07-28)
          이고, 둘 다 settleDecisionType 3유형 전부에서 정산 행 0건. 정산 성숙이 D+12라
          08-06·08-09경 떠야 정상.
        - 그 2건은 둘 다 비멤버십(deliveryDiscountAmount == 0) → 멤버십 N배송 반품 표본은 0건.
      가설("N배송 회수비는 다르고, 멤버십 반품은 네이버가 보상")은 표본이 없어 검증 불가다.
      그래서 검증하려 애쓰는 대신 **표본이 익는 순간 자동으로 포착되게** 한다.

    그레인 = 정산 API가 돌려준 행 그대로(관측 로그, append-only). 집계 그레인으로 미리 뭉치지
    않는 이유: 무엇이 신호인지 아직 모르는 단계라 원본 행을 남겨야 나중에 어떤 축으로든 다시
    볼 수 있다(뭉쳐 저장하면 되돌릴 수 없다).

    UNIQUE = (product_order_id, product_order_type, settle_type, observed_date)
    — 하루 여러 번 재실행해도 같은 행이 중복 적재되지 않는다.
    ★settle_decision_type 컬럼은 2026-08-03에 **삭제**했다(마이그 e7b2c9d4a610): orderId 단건
      조회는 periodType과 상호 배타라 settleDecisionType을 줄 수 없고, 응답 스키마에도 그 필드가
      없다(공식 스펙 확인). 유형 축은 응답의 settle_type이 대신한다.
    observed_date를 키에 **포함**하는 이유: 같은 상품주문의 정산 상태는 날짜에 따라 옮겨간다
    (UNSETTLED → SETTLED). 날짜를 빼면 그 전이가 덮여 사라지고, 넣으면 "언제 무엇으로 보였나"의
    시계열이 남는다 — 이 프로브의 목적이 바로 그 전이 관측이다.

    ★settle_type의 '' sentinel: SQLite/Postgres 모두 UNIQUE에서 NULL을 서로 distinct로 취급해
      중복을 못 막는다(CoupangRgSettlementFee.vendor_item_id와 같은 이유). 응답에 settleType이
      없으면 ''로 채운다. product_order_id도 같은 이유로 non-null ''.
    """

    __tablename__ = "naver_claim_settlement_probe"
    __table_args__ = (
        UniqueConstraint(
            "product_order_id", "product_order_type", "settle_type", "observed_date",
            name="uq_naver_claim_settlement_probe",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False, index=True)   # 네이버 orderId
    product_order_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")  # '' = 응답에 없음
    # 배송방식 — orders.delivery_attribute_type 원본 그대로(ARRIVAL_GUARANTEE=N배송).
    # NULL = 판별 불가(raw_data 부재·JSON 잘림) — 추정으로 채우지 않는다(order_delivery SA 계약).
    delivery_attribute_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    is_membership: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # deliveryDiscountAmount > 0
    order_status: Mapped[str] = mapped_column(String(20), nullable=False)               # returned / exchanged
    product_order_type: Mapped[str] = mapped_column(String(40), nullable=False)         # PROD_ORDER/DELIVERY/CONCESSION/…
    # 정산 상태 축 — NORMAL_SETTLE_ORIGINAL(일반) / NORMAL_SETTLE_BEFORE_CANCEL(정산 전 취소) /
    # NORMAL_SETTLE_AFTER_CANCEL / QUICK_SETTLE_* / QUANTITY_CANCEL_* ('' = 응답에 없음).
    settle_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pay_settle_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    settle_expect_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    pay_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    settle_expect_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    observed_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)   # 프로브 실행일(KST)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# RG 정산 수수료 (트랙 RG-Fee-Accounting S2)
# ──────────────────────────────────────────────
class CoupangRgSettlementFee(Base):
    """쿠팡 로켓그로스(RG) 정산 수수료 — 윙 내부 API(status/api) 수집.

    D-1: 수집 소스=윙 내부 API(세션쿠키). D-9: 판매수수료(B)+풀필먼트(J) 둘 다.
    D-10: 날짜 기준=매출인식일(recognition_date_from/to). D-11: 광고비 dedup 광고비 출처 구분.
    grain=(account_key, recognition_date_from, recognition_date_to, fee_type, vendor_item_id).
    ★vendor_item_id(S6, D-2): 옵션 단위 입자도. 두 출처가 공존한다 —
      - 계정 단위(status/api 수집, Phase 1): vendor_item_id='' (빈문자열 sentinel).
      - 옵션 단위(종류별 엑셀 수집, S6): vendor_item_id=실제 옵션ID.
      빈문자열 sentinel을 쓰는 이유: SQLite/Postgres 모두 unique 제약에서 NULL은 distinct로
      취급돼 중복을 막지 못함 → ''로 채워 grain·upsert·검산을 안정화(원칙22 라이브 안정성).
    ★검산(§8-1): 같은 (account, from, to, fee_type)에서 Σ(옵션 row amount, VAT前 할인적용가 A−B)
      == 계정 row의 엑셀 요약합계(VAT前). 계정 row(status/api) amount는 VAT후(최종비용)라
      Σ옵션(VAT前) + 요약세액 == 계정 row 가 성립(VAT gross-up). S7 net_profit 플립 시 사용.
    amount: 발생비용(f, D-10 — 이월 g 별도필드, 컴포넌트엔 미혼입). 취소/환급은 음수 허용(D-9).
      옵션 row(S6)는 **할인적용가(A−B), VAT前** (발생비용 A=gross 아님, status/api와 불일치 회피).
    fee_type(D-10 라이브 확정 2026-06-09): 'sale_fee'(판매수수료B),
      풀필먼트(J) 3컴포넌트=‘delivery’(배송비)·'warehousing'(입출고비)·'storage'(보관비),
      'return_shipping'·'return_handling'(반품), 'ad_sales'(광고비d, D-16 net_profit 전액 차감 포함).
      raw_type: API 원본 항목명. ★'delivery'=totalFulfillmentFeeDeductionAmount(배송비뿐, 합계 아님).
    """

    __tablename__ = "coupang_rg_settlement_fee"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "recognition_date_from", "recognition_date_to",
            "fee_type", "vendor_item_id",
            name="uq_coupang_rg_settlement_fee",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    recognition_date_from: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    recognition_date_to: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    fee_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # 옵션 단위 입자도(S6). 계정 단위(status/api)='', 옵션 단위(엑셀)=옵션ID. NOT NULL+default=''.
    vendor_item_id: Mapped[str] = mapped_column(String(30), nullable=False, server_default="", default="", index=True)
    raw_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    # ★S9(2026-08-03) 정산서가 직접 알려주는 청구 근거 — 추론 대체용. 옵션 row(엑셀)만 채워지고
    #   계정 row(status/api)·구 행은 NULL이다(그래서 nullable, 감사는 NULL이면 종전 경로로 폴백).
    #   왜 필요한가: 감사가 "청구 사이즈"와 "청구 주문수"를 **추론**했고 둘 다 오탐의 원인이었다.
    #   주문수는 `coupang_rg_order_item`(결제일 basis)을 매출인식일 정산주기에 맞춰 세다가
    #   창 불일치로 단가를 정수배 부풀렸고(오탐 4건, LESSONS #106), 사이즈는 금액 임계로 역추정했다.
    #   정산 엑셀 상세에는 **주문ID·판매수량·개별포장사이즈**가 그대로 있다(ref 17 §8-1) →
    #   같은 파일·같은 basis에서 읽으면 조인도 추론도 없다.
    billed_size_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    billed_order_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    billed_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 RG 상품별 실측 사이즈 (PRODUCT_SIZE_COMPARISON 보고서)
# ──────────────────────────────────────────────
class CoupangProductSize(Base):
    """쿠팡 물류센터 실측 사이즈 — Wing PRODUCT_SIZE_COMPARISON XLSX 수집.

    쿠팡이 입고 후 물류센터에서 실제 측정한 사이즈 등급을 저장.
    이 값이 배송비·입출고비 과금 기준이므로 anomaly 판단도 이 값 우선.
    grain=vendor_item_id (옵션 단위). 정산주기별 upsert(최신 덮어쓰기).
    """

    __tablename__ = "coupang_product_size"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_item_id: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    seller_product_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    sku_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    option_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 쿠팡 측정 사이즈 등급 (극소형·소형·중형·대형1·대형2·특대형)
    size_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 수집 출처 정산주기 (group_key: A01564720-2026-06-08-2026-06-14)
    source_group_key: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────
# 쿠팡 로켓배송(1P) 발주/납품/정산 (supplier.coupang.com — 트랙 rocket-1p S2)
# ──────────────────────────────────────────────
class CoupangRocketPurchaseOrder(Base):
    """쿠팡 로켓배송(1P) 발주(PO) — supplier.coupang.com 발주현황 (트랙 rocket-1p, D-9).

    소스: GET /po-web/app/purchase-order/list JSON(ref 20). 발주(①)+납품(②) 한 row에 공존.
    런타임 경계(D-1): supplier는 Akamai 봇방어 → 백엔드 requests 직접 호출 금지.
      Mac 헤드풀 CDP 페처가 page-context fetch(발주일=PURCHASE_ORDER_DATE)로 page 루프 수집 →
      raw JSON push → 백엔드 파서(clients/coupang/rocket_supplier.py)가 정규화 → 여기 적재.
    grain: purchase_order_seq(발주 PK). 같은 PO 재수신 시 확정치로 교체(snapshot upsert).

    금액(D-9 §6-1 ③: 발주/입고금액 = VAT 포함 gross):
      sum_of_order_amount = 발주금액(쿠팡이 발주한 금액) = D-3 매출 기준(발주일 인식).
      sum_of_receiving_amount = 실제 입고(납품)금액 → 발주↔납품 드리프트(D-5)는 row 내 비교.
      sum_of_vendor_confirmed_amount = 거래처(우리) 확인금액.
    vendor_payment_seqs(D-9 §6-1 ④): 이 PO에 매핑된 계산서번호(vendorPaymentInfoSeq) 리스트.
      관계 1PO↔N계산서(부분정산)·1계산서↔N PO(묶음). 발주↔정산 드리프트 조인키. JSON 저장.
    D-10: po_created_at(발주일)·status·receiving = 운영축(재고·발송 관제), order_amount = 돈축(종합조망).
      한 테이블이 양축을 다 먹임 — 메뉴 분리는 프론트 슬라이스(S5).
    """

    __tablename__ = "coupang_rocket_purchase_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order_seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # A01029796(계정축)
    # 금액 3종 (gross, 원 단위)
    sum_of_order_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # ★발주=D-3 매출
    sum_of_receiving_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 납품(입고)
    sum_of_vendor_confirmed_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 수량 3종
    order_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receiving_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vendor_confirmed_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 상태/물류 (운영축)
    purchase_order_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    purchase_order_status_description: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    purchase_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    center_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    center_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_sku_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    sku_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 날짜 (po_created_at=발주일 UTC=매출 인식일, D-3)
    po_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    expected_delivery_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ★실입고 시각(2026-08-06) — 원천 응답에 계속 오고 있었는데 파서가 버리고 있었다.
    #   왜 필요한가: `expected_delivery_date`는 **예정**이라 실제와 어긋난다(실측: 예정 08-04인
    #   PO들의 실입고 완료가 08-04·08-05로 갈렸다). 계산서는 "같은 날 입고분"을 묶어 며칠 뒤
    #   발행되므로(작성일−납품예정일 = −4~+7일), 계산서 축 일별 매출을 세우려면 **입고일이
    #   정본**이어야 한다. 미수금의 연령(aging)도 이 값 없이는 못 낸다.
    #   ⚠️입고 전 PO는 NULL이다 — 0이나 예정일로 접지 말 것(원칙22: 미수집 ≠ 없음).
    receiving_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    receiving_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    # 계산서 매핑(↔정산 드리프트 조인키) — vendorPaymentInfoSeq 리스트
    vendor_payment_seqs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketPurchaseOrderItem(Base):
    """쿠팡 로켓배송(1P) 발주상세 per-SKU 라인아이템 — supplier 발주상세 (트랙 rocket-1p, S4.5a/D-13).

    소스: GET /scm/purchase/order/get/{seq} SSR HTML의 Table[7](ref 20b §2). 1PO=N SKU(최대 50).
    런타임 경계(D-1): Mac 헤드풀 페처가 DOM Table[7] rows 추출 → raw push → 파서(위치 기반) 정규화 → 적재.
    grain: (purchase_order_seq, product_number). 같은 PO 재수신 시 **snapshot replace**(해당 PO 전 행
      삭제 후 재삽입 — SKU 제거 반영, 멱등). 자연키 불완전성 회피.

    용도(D-12→D-13): PO그레인(CoupangRocketPurchaseOrder)은 multi-SKU(61%) 원가분해 불가 →
      per-SKU 수량으로 1P net_profit cost 산정(S4.5c). cost = Σ(order_qty × product_master.cost_price
      [상품번호→internal_sku 매핑, S4.5b RocketProductCostMap]). 발주상세의 매입가는 쿠팡→우리 **매출**(원가 아님).
    금액(gross, ref 20b §2 검산): unit_purchase_price×order_qty = line_order_amount = line_supply_amount + line_vat.
    """

    __tablename__ = "coupang_rocket_purchase_order_item"
    __table_args__ = (
        UniqueConstraint("purchase_order_seq", "product_number", name="uq_rocket_po_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # ↔PO grain
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 계정축(페처 주입)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 발주상세 순번
    product_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # ★상품번호=브리지 키
    barcode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)  # EAN 또는 R-내부코드
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    purchase_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # 일반매입/직매입
    order_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 발주수량(원가 산정용)
    # 업체납품가능수량(발주상세 인덱스 5, ref 20b §2) — 우리가 확인한 납품 가능분.
    #   PO그레인 CoupangRocketPurchaseOrder.vendor_confirmed_qty(sumOfVendorConfirmedQty)의 per-SKU 판.
    #   nullable: 이 컬럼 신설 이전에 적재된 기존 행은 값 없음(백필 전까지 NULL).
    vendor_confirmed_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 금액(gross/net, 원 단위) — 쿠팡→우리 매입(매출), 우리 원가 아님(원가는 product_master)
    unit_purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 매입 단가
    line_order_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 라인 발주금액(gross)
    line_supply_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 라인 공급가(net)
    line_vat: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 라인 세액
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketSettlement(Base):
    """쿠팡 로켓배송(1P) 매입 정산 — supplier.coupang.com 매입정산 (트랙 rocket-1p, D-9).

    소스: GET /scm/settlement/general/purchase/account 폼-GET SSR HTML(JSON 아님 → DOM 파싱, ref 20).
    런타임 경계(D-1): Mac 헤드풀 페처가 DOM 테이블 추출 → raw rows push → 백엔드 파서 정규화 → 적재.
    grain: invoice_seq(계산서번호=vendorPaymentInfoSeq). 1계산서↔N PO(묶음 정산). snapshot upsert.

    금액(D-9 §6-1 ③): supply_amount=공급가액(net, VAT前), vat=부가가치세,
      payment_amount=지급예정금액(gross=공급+VAT, 검산 일치). 종합조망 매출은 gross 발주금액 사용,
      정산은 발주↔정산 드리프트(D-5) 표현용(vendor_payment_seqs 조인). 별도 비용라인 아님(D-5).
    """

    __tablename__ = "coupang_rocket_settlement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    supply_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 공급가(net)
    vat: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 부가세
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 지급예정(gross)
    issue_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)  # 작성일
    payment_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 지급일
    tax_invoice_confirmed_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 세금계산서 확정일
    settlement_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 정산유형(입고 등)
    bill_issue_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 발행유형(역발행 등)
    tax_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 과세유형
    first_payment_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 1차지급액
    second_payment_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 2차지급액
    # 전자세금계산서 전송성공 여부 — 정산 테이블 마지막(헤더명 빈) 링크 컬럼에서 파싱(ref 20 §4 #16).
    #   True='전송성공' 표기 / False='전송성공' **미표기** / None=셀 부재·미관측 토큰(판별 불가).
    #   ★False를 '전송실패'로 읽지 말 것. 실측 10행에서 False인 유일한 행은 세금계산서 확정일도
    #     '-'(미확정)이라, 관측된 사실은 "확정 전에는 상태 텍스트가 없다"까지다. '확정됐는데 미전송'
    #     표본은 0건 — 진짜 실패와 미발행을 구분하려면 별도 근거가 필요하다.
    tax_invoice_transmitted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketSettlementItem(Base):
    """쿠팡 로켓배송(1P) **계산서 라인** — supplier 입고상세내역 (트랙 coupang-promo-pnl, D-CPP-20).

    소스: GET `/scm/receive/detail?vendorPaymentInfoSeq={계산서번호}&cplbYn=N` 폼-GET SSR HTML.
      ★페이징이 `page`만으로는 안 돈다 — 폼의 **`totalCount`를 함께 실어야** 3페이지 이후가 온다
      (실측 2026-08-06: totalCount 없이는 20/48행에서 멈췄다). 페이지 크기 10.
    런타임 경계(D-1): Akamai → 백엔드 직접 fetch 금지. Mac 페처가 DOM rows push → 파서 정규화.
    grain: (invoice_seq, line_no). 같은 계산서 재수신 시 **snapshot replace**(그 계산서 전 행 삭제 후
      재삽입) — 라인 자연키가 불완전(같은 PO·SKU가 여러 줄일 수 있다)해서 upsert 대신 교체다.

    ★왜 만들었나 — **계산서 축 일별 매출의 귀속일을 실측으로 얻기 위해서다(D-CPP-20).**
      계산서 헤더만으로는 날짜를 못 정한다: 계산서 1건이 평균 5.8개 PO를 묶고, 작성일은 실입고일보다
      −4~+7일 흔들린다. PO 입고금액 비율로 배분하는 것도 근거가 없다 — 계산서 공급가 합계
      110,022,496원 ↔ 묶인 PO 입고액 합계 178,753,941원으로 **금액이 맞는 계산서가 0건**이다
      (1 PO가 여러 계산서에 걸리고, 계산서가 입고액 전부를 덮지도 않는다 = 그 차이가 미수금).
      이 화면은 **라인마다 입고일자와 금액이 직접 붙어 있어** 배분도 추정도 필요 없다.
      실증(계산서 30608513): 라인 48건 총단가 합 2,489,790원 = 계산서 지급예정금액과 **차이 0원**.

    kind: '발주'(입고) | '반출'(반품·차감). 반출은 금액이 음수로 온다 — 부호를 살려 담는다.
    received_at: 「입고/반출일자」. **이 값이 계산서 축 일별 매출의 귀속일이다.**
    """

    __tablename__ = "coupang_rocket_settlement_item"
    __table_args__ = (
        UniqueConstraint("invoice_seq", "line_no", name="uq_rocket_settle_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # ↔계산서 헤더
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 계정축(페처 주입)
    kind: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)          # 발주 | 반출
    purchase_order_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    sku_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    sku_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # ★귀속일 — 「입고/반출일자」. 날짜만 필요하지만 원천이 시각까지 주므로 그대로 담는다.
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    center_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tax_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    supply_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    vat: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    # 총 단가 = 공급가+세액. 계산서 헤더의 지급예정금액과 맞물리는 축이다(실측 차이 0원).
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketSalesDaily(Base):
    """쿠팡 로켓배송(1P) 옵션×일 소비자 판매 — supplier 애널리틱스>판매 분석 (트랙 coupang-promo-pnl, Phase 1).

    소스: `https://supplier.coupang.com/rpd/web-v2/basic/web-view?type=SALES_ANALYSIS` (BETA).
    런타임 경계(rocket-1p D-1과 동일): Akamai 봇방어 → 백엔드 직접 fetch 금지. Mac 헤드풀 CDP
      페처가 수집 → **우리 레코드 계약**으로 push → 파서(clients/coupang/rocket_promo.py) 정규화 → 적재.
    grain: (vendor_id, option_id, date). 같은 키 재수신 시 확정치 교체(snapshot upsert, 멱등).

    ★★revenue는 회계 매출이 아니다(D-CPP-2). 1P 회계 매출은 발주(납품)금액 축이 정본이다
      (rocket-1p D-3). 여기 revenue = **소비자 실현가**(쿠팡이 자체 마진으로 내린 판매가가 반영된
      금액)이며, 용도는 **광고 BEP ROAS의 분자 · 수요/전환 신호**로 한정한다. 종합조망 net_profit
      계산에 절대 결합하지 않는다 — 결합하면 1P 매출이 이중으로 잡힌다.

    sku_id = 발주 데이터의 product_number와 같은 키(실측 62178970) → RocketProductCostMap 브리지로
      원가에 닿는다. option_id는 쿠팡 옵션ID(판매분석 화면 표기).
    D-CPP-5: 판매분석은 BETA + "Basic 무료체험중" → 접근 차단이 조용히 오면 이 테이블이 멈춘다.
      수집기는 403/구독오류를 성공으로 접지 말 것(원칙22).
    """

    __tablename__ = "coupang_rocket_sales_daily"
    __table_args__ = (
        UniqueConstraint("vendor_id", "option_id", "date", name="uq_coupang_rocket_sales_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # A01029796(계정축)
    option_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 쿠팡 옵션ID
    sku_id: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, index=True
    )  # = 발주상세 product_number(원가 브리지 키)
    date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=False, index=True)  # 판매일(KST)
    qty: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )  # 판매수량
    revenue: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )  # ★소비자 실현가 기준 매출(회계축 아님 — 위 주석)
    visitors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 유입수(있으면)
    conversion_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 4), nullable=True
    )  # 전환율 — **0~1 소수**(3.52% → 0.0352). % 표기는 페처가 100으로 나눠 보낸다(PLAN §4)
    # ── 퍼널 지표(D-CPP-11, 2026-08-06) — 이미 받고 있던 응답에서 버리던 값이다. 추가 요청 0.
    #   ★날짜별 값임을 실측으로 확인했다: 같은 옵션의 08-04와 06-05가 서로 다르다(대조군).
    #   visitors(방문자) ⊂ page_views(조회) ⊃ orders(주문)이고 conversion_rate == orders/page_views
    #   (실측: 162/1540 = 0.10519… == pvToOrder). 즉 전환율은 이 둘의 몫이라 **검산이 가능하다.**
    #   ⚠️장바구니·검색량·SRP클릭/점유율은 담지 않았다 — 3개 날짜 전 옵션에서 값이 0이었다
    #     (프리미엄 데이터 2.0 등급 지표로 보인다). 0을 담으면 "관측 없음"과 "정말 0"이 영영
    #     구분되지 않는다. 등급이 올라가면 그때 재확인하고 추가할 것.
    page_views: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 조회(PV)
    orders: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)      # 주문수
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="sales_analysis",
        server_default="sales_analysis",
    )  # sales_analysis | excel (원천 라벨 — 폴백 경로 추적)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketOptionSku(Base):
    """쿠팡 1P 옵션ID ↔ 상품번호(SKU) 대응 — **손익 결합의 브리지**(트랙 ohitech-ad D-16).

    왜 별도 테이블인가: 이 대응은 **시간 불변**이다(2026-08-03 실측 — 244옵션 중 sku_id가 바뀐
      옵션 0건). 즉 매일 재수집할 이유가 없는 정적 사실인데, 지금까지는 `coupang_rocket_sales_daily`
      **일별 수집의 부산물로만** 존재했다. 판매분석은 BETA + "Basic 무료체험"(D-CPP-5, 2026-08-20
      종료 예정)이라 그 수집이 끊기면 **브리지가 통째로 사라지고 옵션별 손익이 조용히 멈춘다**.
      → 관측될 때마다 여기에 누적 보존하고, 손익 계산은 **이 테이블만** 읽는다. 수집이 끊겨도
      이미 아는 상품은 계속 산출되고, 새 상품만 "브리지 없음"으로 화면에 드러난다.
      (같은 실패 형태: D-NAO-41 "외부생성 리소스 의존은 조용히 끊긴다 → 자족 구조로".)

    grain: option_id(쿠팡 옵션ID·유니크). sku_id = 발주상세 product_number와 같은 키(실측 62178970)
      → RocketProductCostMap(상품번호→internal_sku) → ProductMaster.cost_price로 원가에 닿는다.
    ★sku_id는 NOT NULL이다 — 비어 있으면 브리지가 아니다(빈 행을 남겨 "매핑 있음"으로 오인시키지
      않는다). 원천이 externalSkuIds를 하나로 특정 못 하면 애초에 적재하지 않는다.
    ★option_id → sku_id는 1:1이지만 **sku_id → option_id는 1:N**이다(실측 최대 3, 활동 기간이
      실제로 겹친다). 그래서 손익 그레인은 옵션이 아니라 **SKU**다 — 매출·원가는 원래 상품번호
      그레인이고 광고비만 옵션 그레인이라, SKU로 올리면 **더하기(사실)**로 끝나고 안분(추정)이
      필요 없다. 옵션 축으로 내리면 매출·원가를 반드시 나눠야 하고 그건 추정이다.
    """

    __tablename__ = "coupang_rocket_option_sku"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    option_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    sku_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # = 발주 product_number
    vendor_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 관측 시점 라벨(감사용)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="sales_analysis", server_default="sales_analysis",
    )  # sales_analysis | backfill | manual — 끊긴 뒤에도 무엇이 어디서 왔는지 남긴다
    # ── 옵션 속성(D-CPP-11, 2026-08-06). **일별 테이블이 아니라 여기다** — 실측 근거:
    #   08-04와 06-05를 같은 옵션 22개로 대조했더니 아래 값이 **전부 동일**했다. 즉 판매분석이
    #   주는 이 필드들은 그 날짜의 상태가 아니라 **조회 시점의 현재값**이다. 일별 테이블에 넣으면
    #   6월 행에 오늘의 품절 상태가 찍혀 **가짜 시계열**이 된다(같은 응답의 metrics는 날짜별로
    #   다르므로 대조군이 성립한다). 그래서 최신값 1행 + 관측 시각으로 보존한다.
    #   ★현재값이라 롤링 조회창(약 2개월)과 무관하게 언제든 재취득할 수 있다 — 유실 위험 없음.
    brand_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # ' > ' 조인
    is_item_winner: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 바이박스 승자
    is_oos: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)          # 품절
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)     # 리뷰 수
    rating_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2), nullable=True)  # 평점 4.6
    # ★위 속성들을 **언제 본 값인지**. 없으면 최신값인지 반년 전 값인지 구분할 수 없다
    #   (last_observed_at은 브리지 관측 시각이라 의미가 다르다 — 속성이 안 와도 갱신된다).
    attrs_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketPromotion(Base):
    """쿠팡 로켓배송(1P) 프로모션 신청 — 공급자허브 '프로모션' 메뉴 (트랙 coupang-promo-pnl, Phase 1).

    소스: 공급자허브 프로모션 목록/상세(D-CPP-1: 자동 수집이 기본, 수기 입력은 폴백).
    grain: request_id(Request ID). 재수신 시 확정치 교체(snapshot upsert, 멱등).

    ★행사기간은 **초 단위**다(실례 Request 687878: 2026-07-24 00:01:00 ~ 07-26 23:59:59) —
      날짜로 뭉개면 프로모션 창 조인(Phase 2)이 하루씩 틀어진다. DateTime으로 보존한다.
    ★D-CPP-4: 분담금이 어떤 형태로 청구되는지 **미확정**이다(07월분 정산일이 9월). 매입정산
      (coupang_rocket_settlement)에 흔적 없음. 따라서 이 테이블은 **사실 기록일 뿐 비용 라인이
      아니다** — 어떤 손익 계산에도 자동 반영하지 않는다. 9월 정산서 도착 후 대사해서 확정.
    raw: 원본 응답 보존(스키마 드리프트·미매핑 필드 사후 복구용).

    ★unit_discount_amount(D-CPP-7, 2026-07-28 확정) — **프로모션당 단위 할인액(원), 수기 입력.**
      Jino 원문: "한 프로모션당 할인하는 가격이 하나로 정해지게 되어 있어. 그래서, 한 프로모션에
      제품은 여러개가 들어갈 수 있지만 할인 가격은 모두 같은게 맞아."
      왜 수기인가(라이브 실측 2026-07-28): 공급자허브 프로모션 목록/상세 API 응답에 **상품별·단위
      할인액 필드가 없다**(discountBudget=총예산, supplierFundRate=분담%, discountType=할인방식뿐).
      없는 필드를 추측해서 읽으면 조용히 틀린 값이 권위값 자리에 앉는다 → 페처는 이 칸을 절대
      건드리지 않고, `PATCH /rocket/promotion/{request_id}/unit-discount`로만 채운다.
      ⇒ **페처의 snapshot upsert가 이 칸을 덮어쓰지 않는다**(수기 입력이 재수집에 지워지면 안 됨).
    """

    __tablename__ = "coupang_rocket_promotion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    contract_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # 계약ID
    promotion_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 쿠폰명
    promotion_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 종류(즉시할인 등)
    status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)  # 초 단위
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 초 단위
    share_ratio: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 2), nullable=True
    )  # 고객할인 비용 분담비율(%) — 100 = 전액 셀러 부담
    discount_method: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )  # 할인방식(정액수량 / 할인액 등)
    discount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    unit_discount_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )  # ★수기 입력(D-CPP-7) — 프로모션당 단위 할인액. 아래 주석 참조
    # ★대상 SKU(=발주 product_number) 목록, **수기 입력**(Phase 2 손익 엔진).
    #   왜 수기인가: 프로모션 API 응답에 **적용 상품 목록이 없다**(2026-07-28 prod raw 실측 —
    #   detailCount=적용상품 '수'만 있고 배열 없음). 손익은 "이 창에서 어느 SKU가 팔렸나"를
    #   알아야 계산되는데, 이름 유사도·기간 겹침 같은 **추정 매핑은 금지**한다: 틀린 SKU를
    #   물면 손익·BEP ROAS가 통째로 틀리면서 어디서도 대사되지 않는다(원칙22).
    #   unit_discount_amount와 같이 페처가 손대지 않는 칸이라 재수집에 지워지지 않는다.
    target_sku_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)  # 예산
    settlement_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 정산일
    applied_product_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 적용상품 수
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 요청일
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 원본 보존
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangPromoDiscountItem(Base):
    """1P 프로모션 **상품별 개당 할인액** — 제안서 엑셀이 원천 (트랙 coupang-promo-pnl, D-CPP-10).

    왜 별도 테이블인가: `CoupangRocketPromotion.unit_discount_amount`(D-CPP-7)는 "프로모션당
      단일값"을 수기로 받는 1칸이었다. 제안서 엑셀을 자동으로 읽게 되면서 **라인 단위**(SKU별
      타입·값)로 보존한다 — 지금 표본은 전부 한 파일 안에서 값이 같지만, 총액으로 뭉개면
      나중에 SKU마다 다른 행사가 오면 조용히 평균값이 앉는다(네이버에서 "총액은 상쇄로 오류를
      숨긴다"가 실증됐다). 기존 수기 칸은 폴백으로 남긴다.

    grain: (request_id, product_number). 파일 재수신 시 그 프로모션의 항목을 **통째로 교체**한다
      (snapshot) — 제안서에서 빠진 SKU가 유령으로 남으면 안 되기 때문.

    ★discount_type/‌discount_value 의미:
      · '정액수량' → discount_value = **개당 할인액(원)**. 분담금 = 판매수량 × 값.
      · '정률'     → discount_value = **비율**(0.2 = 20%). 분담금 = 판매금액 × 값.
      두 축의 단위가 다르므로 합산 전에 반드시 타입을 본다.

    ★이 테이블은 "우리가 부담하기로 한 조건"이지 **쿠팡이 청구한 금액이 아니다**(D-CPP-4 미확정 —
      07월분 정산일이 9월). 손익에 싣는 값은 여기서 계산한 **추정 분담금**이고, 9월 정산서가
      오면 실제 청구액과 대사해서 확정한다.
    """

    __tablename__ = "coupang_promo_discount_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # → coupang_rocket_promotion
    product_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 1P 발주 상품번호
    discount_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 정액수량 | 정률
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source_file: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 원천 파일명(감사)
    file_mtime: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 파일 수정시각
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("request_id", "product_number", name="uq_promo_discount_item"),
    )


# ──────────────────────────────────────────────
# 쿠팡 로켓배송(1P) 발송(ASN) 쉽먼트 — "우리가 보낸 양" (트랙 rocket-1p, ref 45)
#
# ★왜 신설하나: 지금까지 저장된 건 **발주**(쿠팡이 시킨 양)와 **입고**(쿠팡이 받았다고 인정한 양)
#   둘뿐이었다. 그 사이 "보냈는데 안 잡힌 것"=미수금은 2026-08-05 일회성 정찰로만 8,033,970원을
#   찾아냈고(ref 45), 상시 수집이 없으면 다음 달에 같은 일이 생겨도 아무도 못 본다.
# ★수량 3종을 구분해서 읽어야 한다: 발주수량(쿠팡 요청) ≥ 납품수량(우리가 보냄) ≥ 입고수량(쿠팡 인정).
#   미수금 = 납품 − 입고. 단 **납품수량은 우리 신고값**이라 제3자 검증이 없다(ref 45 §8-1) —
#   그래서 박스 추적(집하·도착·하차)을 같이 저장한다. 그게 택배사·물류센터의 기록이다.
# ──────────────────────────────────────────────
class CoupangRocketShipment(Base):
    """1P 발송 쉽먼트 헤더 — grain = shipment_seq.

    소스: GET /ibs/shipment/{parcel|truck}/list (목록) + /ibs/shipment/{type}/{seq} (상세) SSR HTML.
      ★진입 경로 주의(ref 45 §1-1): `/ibs/asn/active`를 파라미터 없이 열면 대시보드로 리다이렉트된다.
      발주 상세의 「택배 쉽먼트」 링크(`?type=parcel&purchaseOrderSeq=`)로 오리진을 재무장한 뒤
      같은 탭에서 fetch해야 한다. 세션이 오래되면 fetch가 `Failed to fetch`로 죽는다(Akamai stale).
    ★쉽먼트 : 발주 = 1 : N. 목록의 「총 납품 수량」은 여러 발주의 합계라 발주별 수량이 아니다 —
      발주별·SKU별은 **상세에만** 있다(ref 45 §1-3, 착수 전 전제가 틀렸던 지점).
    status_code는 화면 라벨이 아니라 `data-status` 속성(READY/CONFIRMED/…)이다 — 한국어 라벨이
      바뀌어도 안 흔들린다.
    """

    __tablename__ = "coupang_rocket_shipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 계정축(페처 주입)
    shipment_type: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # PARCEL | TRUCK
    status_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    status_label: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 화면 표기(참고)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    carrier_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    center_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    origin_address: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    estimated_arrival_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    box_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 요약표(총 납품/총 입고). 상세를 아직 안 받은 쉽먼트는 total_received_qty가 None = **모름**이다.
    #   0으로 접으면 "쿠팡이 하나도 안 받았다"가 되어 미수금을 지어낸다.
    po_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sku_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_shipped_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_received_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketShipmentItem(Base):
    """1P 발송 라인 — 박스 × 발주 × SKU × 납품수량 × 입고수량.

    grain: (shipment_seq, line_no). 같은 쉽먼트 재수신 시 **snapshot replace**(전 행 삭제 후 재삽입)
      이므로 line_no만으로 충분하고 멱등이다.
      ★(shipment, box, po, sku)를 유니크로 걸지 않은 이유: 쿠팡 화면이 같은 조합을 두 줄로
      낼 여지가 있는데(박스 내 분할 등 미확인), 그때 유니크 위반으로 **수집 전체가 죽는다**.
      snapshot replace가 이미 중복 누적을 막으므로 자연키를 강제하지 않는다.
    ★「박스」 셀은 rowspan이라 둘째 행부터 없다 — 파서가 이월한다(ref 45 §10). 위치 기반으로
      읽으면 발주번호 자리에 SKU가 들어간다.
    """

    __tablename__ = "coupang_rocket_shipment_item"
    __table_args__ = (
        UniqueConstraint("shipment_seq", "line_no", name="uq_rocket_shipment_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    box_label: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)  # '박스 #1 PBL…'
    purchase_order_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    product_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # 발주상세와 같은 키
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    shipped_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 납품수량(우리 신고)
    # ★입고수량 = **ASN 화면이 말하는 입고**다. 미수금 확정에 그대로 쓰면 안 된다:
    #   이 화면은 **재발송분 입고를 못 본다**(2026-08-05 실측 52라인) → 미입고가 과대하게 나온다.
    #   그날 그 값만 믿고 계산한 미수금 14.0M이 입고 원장 재판정에서 8.03M으로 내려갔다
    #   (5,763,290원 과대, 교훈 #141). 확정 판정의 정본은 `/scm/receive/detail/download` 입고 원장이다.
    #   여기 값은 **상한(후보)**으로만 쓴다.
    received_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 입고수량(ASN 화면 기준)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketShipmentBox(Base):
    """1P 발송 박스 추적 — 집하·도착·하차. **제3자(택배사·물류센터) 기록**.

    ★이 표가 미수금 청구의 증거 등급을 정한다(ref 45 §4-1: 하차 확인 130라인 7,108,720원이
      1차 청구 권장분이었다). 납품수량은 우리 말이고, 하차 시각은 쿠팡·택배사 말이다 —
      "정말 보냈나"에 답할 수 있는 유일한 축이다.
    grain: (shipment_seq, box_label). snapshot replace.
    """

    __tablename__ = "coupang_rocket_shipment_box"
    __table_args__ = (
        UniqueConstraint("shipment_seq", "box_label", name="uq_rocket_shipment_box"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    box_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status_label: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    picked_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 집하
    arrived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)    # 도착
    unloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)   # 하차(★증거)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CoupangRocketPoChangeLog(Base):
    """1P 발주의 **관측된** 변화 1건 — 스냅샷 upsert가 버리던 «직전 값»의 기록.
    계약 `docs/contracts/CONTRACT_1p_po_status_history.md` (Jino 승인 2026-08-28 13:33 KST).

    ★발단(2026-08-28): `_upsert_po`가 snapshot upsert라 원장은 «현재 단면»만 갖는다. 그래서
      「①확인 대기가 왜 줄고 ②발송 대기가 왜 늘었나」에 아무도 답하지 못했고, 그 자리에서
      **「Jino가 확정했기 때문」이라는 근거 없는 인과 주장**이 나왔다. 실측은 그걸 반증했다 —
      오늘 발주 9건 중 8건이 **12:34 수집에서 처음 관측**됐고(10:14엔 없었다), 「처음부터 PA로
      왔는지」와 「그 사이 누가 확정했는지」를 우리 데이터는 **원리적으로 구분 못 한다.**

    ★★이 표의 규율은 하나다: **«우리가 본 것»만 적고 «실제로 일어난 것»을 주장하지 않는다.**
      · 모든 변화는 `prev_observed_at ~ observed_at` **구간**에 귀속된다. 시점을 단정하지 않는다.
      · `first_seen`은 **전이가 아니라 출현**이다 — 「PA로 처음 관측됨」 ≠ 「RP에서 PA로 바뀌는 것을 봄」.
        이 둘을 뭉개면 이 표가 고치려던 병을 이름만 바꿔 재생산한다.
      · 감지 시각을 발생 시각으로 귀속했다가 07-30 변경을 08-03으로 잡은 실사고가 이 저장소에
        이미 있다(`CoupangAdChangeLog` 주석) — 같은 실수를 스키마 층에서 막는다.

    ★해석 필드가 없다(계약 §3 금지선): 주체·원인·「취소」를 담는 칸을 두지 않는다.
      `RP→PA`는 **사실**이고 「우리가 확정했다」는 **해석**이다. 관측 코드만 적는다.

    event:
      `first_seen`   — 이 발주를 처음 봤다. `field`=''·`before_value`=NULL·`prev_observed_at`=NULL,
                       `after_value`에 그때의 상태 코드.
      `field_change` — 아래 8종 중 하나가 달라졌다. 필드마다 행 1개.

    기록 대상 8종(계약 §1이 못 박음 — 늘리지 않는다):
      `purchase_order_status` · 수량 3종(order_qty·receiving_qty·vendor_confirmed_qty)
      · 금액 3종(sum_of_order_amount·sum_of_receiving_amount·sum_of_vendor_confirmed_amount)
      수량·금액까지 넣는 이유: RP에서 납품가능수량을 깎으면 **상태는 그대로인데 ①금액이 준다** —
      상태 전이만으로는 「①이 줄어든 이유 3종(확정/감액/수집누락)」 중 감액이 통째로 안 보인다.

    ★**diff가 있을 때만 행을 만든다 — 그래서 재수신이 저절로 멱등이다.** 같은 값을 다시 받으면
      diff가 0이라 행이 안 생긴다. 유니크 제약은 그 위의 안전망이다(같은 회차 재실행 방어).
      prod 2,698건 중 CI 2,582건(96%)은 재관측해도 무변화라 diff-only가 볼륨의 자연 상한이다.

    ★소급 불가(계약 §7 전제 1): 원장에 직전 값이 없으므로 **배선한 날부터만** 쌓인다.
      「이력에 없음」을 「변화 없음」으로 읽으면 안 된다 — 화면이 그 시작일을 자백해야 한다.

    시각은 전부 **KST naive**(`kst_now()` — `synced_at`과 같은 규약). `server_default`를 쓰지
      않는 이유: SQLite `now()`는 UTC라 규약이 갈린다. ⚠️같은 행의 `po_created_at`은 UTC 저장이다.
    """

    __tablename__ = "coupang_rocket_po_change_log"
    __table_args__ = (
        # 같은 회차를 두 번 넣어도 행이 안 늘게. field는 NULL 대신 ''를 쓴다 —
        # SQLite에서 NULL은 서로 달라 중복이 안 막힌다(CoupangAdChangeLog와 같은 이유).
        UniqueConstraint("purchase_order_seq", "event", "field", "observed_at",
                         name="uq_coupang_rocket_po_change_log"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(16), nullable=False)          # first_seen | field_change
    field: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # ★구간의 양 끝 — 변화는 이 «사이»에 일어났다. 한쪽만 쓰면 시점 단정이 된다.
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # first_seen이면 NULL(직전 관측이 없다). field_change면 덮어쓰기 직전 그 행의 synced_at.
    prev_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class CoupangRocketPoIngestRound(Base):
    """발주 수집 «회차» 1건의 결과 — 이벤트를 **몇 건 버렸는지**가 화면에 닿는 유일한 길.
    계약 `docs/contracts/CONTRACT_1p_po_status_history.md` §4-8 (적대 리뷰 1R P1-1).

    ★왜 필요했나: 초판은 `changes_dropped`를 **로그와 페처 응답으로만** 냈다. 조회 API가
      원리적으로 못 읽으니, 이벤트 적재가 통째로 실패한 회차에도 화면은 「이번 수집에서는
      달라진 발주가 없습니다」를 **적극적으로 단언**했다(적대 리뷰가 재현: RP→PA 전이가
      실제로 있는데 화면은 「없습니다」). 침묵이 아니라 거짓말이라 더 나쁘다.
    ★★가장 유력한 발현 경로가 하필 이 저장소의 상습 사고다 — **코드가 마이그레이션보다 먼저
      배포되면** 매 회차 전량 drop이고 화면은 매번 「없습니다」다(`--migrate` 순서 강제의 이유).

    ★이벤트 표에 sentinel 행을 넣지 않고 별도 표로 둔 이유: 그 표는 «관측된 변화»만 담는
      read-only 파생이고(§3), 「적재가 실패했다」는 우리 파이프라인의 사실이지 발주의 사실이 아니다.

    grain: `observed_at`(회차 = 한 push = 한 시각) 유니크. 시각은 KST naive(`kst_now()`).
    """

    __tablename__ = "coupang_rocket_po_ingest_round"
    __table_args__ = (
        UniqueConstraint("observed_at", name="uq_coupang_rocket_po_ingest_round"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 이번 회차 PO 수
    changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # 적재된 이벤트
    # ★0이 아니면 화면이 「달라진 게 없다」고 말하면 안 된다 — 그게 이 컬럼의 존재 이유다.
    dropped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class CoupangRocketInvoiceConfirm(Base):
    """1P 「거래명세서확인」(RI→CI) 실행 명령 겸 **감사 레코드** (계약 CONTRACT_1p_invoice_confirm_write).

    ★이 표 1행 = 사실 1개다. 한 명령 = PO 1건 = POST 최대 1회(계약 §2). 배치 없음.
    ★★**되돌릴 수 없는 회계 확정**이라 정정 경로가 없다 — 사후 가시성·근거 보존이 그 자리를
      대신하는 전부다(전역 §1). 그래서 요청·사전판정·응답 body **원문**·사후 상태를 한 행에 남긴다.
      `response_body`를 버리고 success 불리언만 남기는 것은 계약 §3 금지선이다(실패 시 body가
      유일한 진단 재료 — supplier가 구조화 에러를 안 준다: `alert(data)`).

    state 전이 (재시도 없음 — 계약 §3 「자동 재시도 절대 금지」):
      pending  → claimed → succeeded | already_confirmed | failed | unknown
      · pending/claimed = «열린» 명령. 같은 PO에 열린 명령이 있으면 새 명령을 만들지 않는다.
      · succeeded         = HTTP 200 ∧ 응답 JSON `success == true`
      · already_confirmed = **사전 GET에 버튼이 없었다** → POST를 보내지 않았다(멱등성 미상 우회)
      · failed            = 응답이 명시적으로 `success == false` — 「안 일어났다」가 확정된 경우만
      · unknown           = 그 외 전부(Mac 미응답·TTL 만료·비200·JSON 판독 불능).
        ★unknown은 «POST가 나갔는지 모른다»는 뜻이라 **재수집 전 재실행을 잠근다**(계약 §4 S1-7).
          잠금 해제 판정은 이 표가 아니라 원장이 한다: 그 PO의 `synced_at > finished_at`이면
          재수집이 실상태를 확인한 것이므로 풀린다(별도 해제 쓰기 경로를 만들지 않는다 —
          해제를 사람·코드가 «선언»하면 그게 곧 재시도 우회로가 된다).

    grain: 이력 누적이라 purchase_order_seq에 unique를 걸지 않는다(같은 PO가 실패 후 다시 시도될
      수 있다 — 단 그 시도도 사람 클릭에서만 시작하고 사전 GET 게이트를 다시 통과한다).
    시각은 전부 **KST naive**(`kst_now()`) — 이 저장소 로켓 계열의 `synced_at`과 같은 규약이다.
      server_default를 쓰지 않는 이유: SQLite `now()`는 UTC라 규약이 갈린다.
    """

    __tablename__ = "coupang_rocket_invoice_confirm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    vendor_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 요청(사람 클릭) — 명령의 유일한 생성점
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    requested_note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 요청 시점에 화면이 보여 준 금액 — 사후에 «무엇을 보고 눌렀나»를 재구성하는 근거
    received_amount_at_request: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 임대(1회뿐 — 만료는 재임대가 아니라 unknown 종결)
    lease: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 사전 GET 게이트(계약 §2) — button_present | button_absent | fetch_failed
    precheck: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    precheck_http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # POST 결과 — body는 **원문 그대로**(자르되 넉넉히)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class RocketProductCostMap(Base):
    """쿠팡 로켓배송(1P) 원가 브리지 — 발주상세 상품번호 → product_master.internal_sku (트랙 rocket-1p, S4.5b/D-13).

    배경(ref 20b §3): 1P 발주상세의 상품번호/바코드는 우리 원가 마스터(product_master, 3P/RG 카탈로그)에
      0건 매칭(1P supplier 카탈로그 ≠ 3P Wing 카탈로그). 자동 조인 불가 → 일회성 수동 브리지 테이블 신설(A1).
    grain: product_number(1P 발주상세 상품번호, CoupangRocketPurchaseOrderItem.product_number와 동일 키). unique.
    용도(S4.5c): net_profit cost = Σ(po_item.order_qty × product_master.cost_price[internal_sku=이 매핑]).
      이 테이블은 product_number ↔ internal_sku 연결만 — cost_price는 product_master가 정본(회계 일관성, D-13).
    status:
      'confirmed' = internal_sku 채워짐 → 원가 산정 대상.
      'excluded'  = internal_sku 비움. **연결 안 함 · 재제안 방지 · 손익에서는 「모름」.**
        ★★뜻이 **하나**다(2026-08-10, 마이그 e4c7a1b8d206). 옛 이름 `ignored`는 세 모듈에서
          서로 다르게 읽혔다 — 두 곳은 «원가 0원·해결됨», 한 곳은 «원가 미상». 그래서
          2026-06-17 일괄 매핑이 «후보를 못 찾은» 22건을 그 값으로 찍자 두 엔진이 **원가 0원 =
          전액 이익**으로 셌다(90일 발주 실측 진짜 원가 3,311,826원). 「원가 0원」 해석은 없앴다.
        ★`excluded`는 **사람만** 쓴다 — 사유(note) 필수, 자동 매핑(match_method=suggested) 거부.
          「이 물건은 정말 원가가 0이다」는 원가 0원을 **등록**할 일이지 이 상태가 대신할 일이 아니다.
    읽기전용 원칙 외(이 테이블은 사용자 확정 입력) — 단 종합조망 net_profit은 S4.5c에서만 결합(S4.5b는 매핑만).
    """

    __tablename__ = "rocket_product_cost_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)  # ★브리지 키
    internal_sku: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)  # → product_master.internal_sku
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="confirmed")  # confirmed | excluded
    match_method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # manual | suggested
    barcode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 발주상세 캐시(라벨/감사)
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)  # 발주상세 캐시(라벨/감사)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ──────────────────────────────────────────────
# 스케줄러 상태
# ──────────────────────────────────────────────
class SchedulerState(Base):
    """스케줄러 작업 상태 영속 저장"""

    __tablename__ = "scheduler_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 워치독(스케줄러 잡 실패 탐지) — S1 추가. last_run_at=마지막 '성공' 의미 유지(D-F).
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # ok|error|missed
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 마지막 에러 traceback(≤2000자)
    last_status_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 마지막 상태 기록 시각(성공/실패 무관)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ══════════════════════════════════════════════════════════════════
# 네이버 SA 광고 최적화 트랙 (track_naver-ad-optimization, D-NAO)
# 계획서 docs/PLAN_naver-ad-optimization.md §2 데이터 모델.
# grain/컬럼 실측 근거: docs/references/21_naver_sa_stat_report_recon.md
# ══════════════════════════════════════════════════════════════════
class NaverAdDaily(Base):
    """네이버 SA 일별 광고 성과 — 통합 grain (D-NAO 계획서 §2).

    grain: (ad_date, campaign_id, adgroup_id, keyword_id).
      파워링크(WEB_SITE): keyword_id = nkw-... (키워드 단위).
      쇼핑(SHOPPING)·브랜드검색: keyword_id = '' sentinel (그룹 단위, AD 리포트 col4='-').
      기기(M/P)는 P0에서 롤업 합산(device 분리는 P1). '' sentinel = SQLite NULL-distinct 회피.
    소스: /stat-reports AD(imp/clk/cost/rank_sum) + AD_CONVERSION(직접1/간접2 전환수·매출) 조인.
    avg_rank = rank_sum / imp (0 노출이면 미정의). cost/conv_amt = 원 단위(VAT 별도).
    같은 날짜 재수집 시 확정치로 교체(snapshot upsert). D-NAO-9: 키워드 단위 일 판단은
    통계적 불가(0.88클릭/일) → 이 테이블은 누적 저장용, 판단은 7~30일 창 풀링.

    ★adgroup_id='__backfill__'(BACKFILL_SENTINEL_ADGROUP) 행은 회계 의미가 다르다 — 소스가
    /stats(캠페인 grain 장기 백필)라 conv_indirect_cnt/amt에 구매+장바구니 등 전환 액션
    **전량 합계**가 들어있고(액션 유형 분리 불가) cart_*=0이다. 상세 행은 구매만 conv_*,
    장바구니는 cart_*로 분리되므로, 둘을 함께 합산하면 분자가 이중가산된다.
    → 집계하는 SA는 sentinel 행을 명시적으로 제외할 것(제외 안 한 채 배포된 사례: D-NAO-58 CD1).
    """

    __tablename__ = "naver_ad_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "campaign_id", "adgroup_id", "keyword_id",
            name="uq_naver_ad_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # WEB_SITE/SHOPPING/BRAND_SEARCH
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    keyword_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")  # nkw-... / '' (쇼핑 그룹 단위)
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 원, VAT 별도
    rank_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # avg_rank = rank_sum/imp
    conv_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접전환 수(구매)
    conv_indirect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접전환 수(구매)
    conv_direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접전환 매출(원, 구매)
    conv_indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접전환 매출(원, 구매)
    # D-NAO-58 CD1(선행지표 데이터층): 장바구니(add_to_cart) 전환 — 구매(conv_*)와 별도 수집.
    # ★매출/ROAS/BEP 회계엔 절대 안 섞임(회계 코드는 conv_*만 읽음). cart_conversion_rate SA가
    # 상품별 장바구니→구매 전환율을 산출해 탐침 선행지표 가중에 쓴다. cart_*_amt는 리포트의
    # 전환가치 컬럼 원값 저장용일 뿐, 어떤 매출 합계에도 더해지지 않는다.
    cart_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접 장바구니 수
    cart_indirect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접 장바구니 수
    cart_direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접 장바구니 전환가치(원, 매출 아님)
    cart_indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접 장바구니 전환가치(원, 매출 아님)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverAdCreativeDaily(Base):
    """네이버 SA 일별 **소재(광고)** 성과 (D-NAO-140).

    grain: (ad_date, ad_id). 키워드는 롤업한다 — 파워링크는 한 소재가 여러 키워드로 노출되지만
    **우리가 조작하는 레버는 소재 하나**이고, 이 테이블의 존재 이유가 바로 제어 grain에 측정을
    맞추는 것이다(자동화가 소재 입찰을 바꾸면서 소재 성과를 못 보던 상태 = D-NAO-132 정지의
    구조적 원인 중 하나).

    ══ ★왜 `naver_ad_daily`에 컬럼을 더하지 않고 별도 테이블인가 ══
    그 테이블에 grain을 하나 더 얹으면 **기존 소비자가 전부 이중계상한다.** 2026-08-04에 실제로
    그 함정에 걸렸다 — 03 캠페인 08-03 소진을 69,912원이 아니라 139,824원(정확히 2배)으로 읽었다.
    센티넬 행이 `keyword_id`가 아니라 `adgroup_id` 칼럼에 사는 걸 몰라 필터가 안 먹었던 것이다.
    같은 테이블에 grain이 섞이면 이런 오독은 **합계가 안 맞을 때까지 안 보인다.** 그래서 분리한다.

    ══ 수집 비용 0 ══
    소스는 이미 매일 받고 있는 `AD`·`AD_CONVERSION` 보고서다 — **두 보고서 모두 컬럼 5가
    소재 ID**(2026-08-04 라이브 실측). 지금까지 집계하며 버리고 있었을 뿐이라 **추가 네이버
    API 콜이 0**이다. 같은 파서·같은 grain 키 함수를 쓴다(`naver_sa_ad_fetcher._grain_key`).

    ★~~**측정 전용이다** — 이 테이블을 읽고 광고를 조작하는 경로는 없다~~ **(2026-09-04 정정)**
    그 「별도 결정」이 **D-NAO-286**이다: `auto_operator._settlement_agg`의 소재(ad) 분기가 이
    테이블을 읽고, 그 값이 정착창 ROAS 검증·CPC 급등 DOWN·손실 고삐·표본 하한 게이트의
    원료가 된다(계약 `docs/contracts/CONTRACT_sample_floor_gate.md`). 종전 문장은 적대 리뷰
    P2-5가 「지금 거짓」이라 지적한 자리다 — 원문을 지우지 않고 취소선으로 남긴다.
    ★대조 계약: 같은 날 이 테이블의 합계는 `naver_ad_daily`의 같은 날 합계와 **일치해야 한다**
    (`ad_creative_daily_sync.reconcile`). 안 맞으면 둘 중 하나가 틀린 것이고, 그건 즉시 알아야 한다.
    """

    __tablename__ = "naver_ad_creative_daily"
    __table_args__ = (
        UniqueConstraint("ad_date", "ad_id", name="uq_naver_ad_creative_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    ad_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # nad-...
    # 소재가 그룹을 옮기면 하루 안에서도 달라질 수 있다 — 그날 관측된 값을 담는다(참고용).
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 원, VAT 별도
    rank_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # avg_rank = rank_sum/imp
    conv_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_indirect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 장바구니는 `naver_ad_daily`와 같은 규율 — 매출 합계에 절대 안 섞인다(선행지표 재료).
    cart_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cart_indirect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cart_direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cart_indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverProductBep(Base):
    """네이버 상품별 손익분기 ROAS — 자동 산출 (D-NAO-8, 계획서 §2).

    grain: (channel_id, channel_product_id). 네이버(ch6) 상품 단위.
    소스: product_channel_mapping.selling_price × product_master.cost_price ×
      실효 수수료율(naver_settlement_daily) → 공헌이익 → bep_roas = 판매가/공헌이익.
    target_roas = bep_roas × 공격성 배수(안전1.3/표준1.15/공격1.05, D-NAO-2).
    has_cost=False = 원가 미입력 상품(bep 미산출, 목록 노출용). 매일 재산출(snapshot 교체).
    참고 메모리: bep-roas-calculation-structure.
    """

    __tablename__ = "naver_product_bep"
    __table_args__ = (
        UniqueConstraint("channel_id", "channel_product_id", name="uq_naver_product_bep"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False, index=True)
    channel_product_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    product_master_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product_master.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    selling_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, default=0)  # 실효 수수료율(0~1)
    logistics_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 물류비(원)
    contribution_margin: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # 공헌이익(VAT후)
    bep_roas: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)  # 손익분기 ROAS(배수), 공헌이익<=0이면 None
    aggressiveness: Mapped[str] = mapped_column(String(12), nullable=False, default="standard")  # safe/standard/aggressive
    target_roas: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)  # bep_roas × 공격성 배수
    has_cost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 광고 의사결정 BEP에 쓴 수수료율 기준(행 단위). None=산출 전/폴백 미판정.
    #   delivery_case — N1(D-NAO-99) 기본. **그 상품의** 건별 정산 실측(주문관리+기저 매출연동)
    #                   + 1.5%p × N배송 혼합비. 상품 표본 ≥5건일 때.
    #   delivery_acct  — 위와 같은 실측이되 상품 표본이 얇아(<5건) 계정 실측을 쓴 행.
    #   ad_case        — D-NAO-57(B) 유형별 분해(매출연동 언디루션). 정산 표본이 상품에 귀속되지
    #                   않을 때의 계정 단일 요율 폴백. ★언디루션은 라이브에서 항등(ref 42 §6, N3).
    #   blended        — 전체 회계 실효율(|Σcomm|/(Σsettle+|Σcomm|)) 최종 폴백.
    commission_basis: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # ── D-NAO-283 (2026-09-01): 자가 «무엇으로» 만들어졌나를 행 단위로 남긴다 ──
    # 종전엔 폴백 상수와 실측이 같은 칸에 구분 없이 앉아 있어, 다음 세션이 「이 숫자가 실측인가
    # 추정인가」를 되물을 수 없었다(bep_calculator.py 머리말이 이미 부채로 적어 둔 구멍).
    #   판매가 price_basis:
    #     orders  — orders 실거래 단가 median (기본·가장 정직)
    #     mapping — product_channel_mapping.selling_price (사람이 손으로 넣은 값, D-NAO-95)
    #     meta    — naver_product_meta_current.discounted_price (커머스API 할인적용가, C10 09:55)
    #   물류비 logistics_basis:
    #     orders  — 그 상품 자기 주문 실측
    #     sibling — 같은 group_product_no 형제 주문 실측 (주문 0건 상품)
    #     default — 자기도 형제도 없음. 수취 0 · 수량 1 가정 = **측정이 아니라 «모름»**
    price_basis: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    logistics_basis: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverAdgroupProduct(Base):
    """네이버 쇼핑 광고그룹 ↔ 판매 상품 매핑 (D-NAO-57 A, 관찰성 sync).

    grain: (adgroup_id, mall_product_id). 소스: 쇼핑 소재 /ncc/ads의
    referenceData.mallProductId(type=SHOPPING_PRODUCT_AD) — naver_product_bep.channel_product_id와
    정확히 일치(라이브 실증). 한 그룹에 소재(상품)가 여럿일 수 있어 unique는 (adgroup, mall_product).
    ══ ★★계약: 이 테이블은 "현재 매핑"이 아니라 **역대 관측의 누적**이다 (2026-08-03) ══
    매일 07:45 `shopping_ad_product_sync`가 **관측 스코프**(campaign_roster.
    observation_campaign_ids — 최근 7일 광고비>0 ∪ settings 행, optimizer 무관) 쇼핑 캠페인의
    활성 그룹을 **upsert**한다. **삭제는 하지 않는다** — 2026-07-31 07:45 KST에 구 "스냅샷
    교체" 구현이 276행을 전량 날린 뒤, 정리 계층을 통째로 들어냈다(사고 경위와 그 판단 근거는
    `shopping_ad_product_sync` 모듈 docstring).

    ★★**stale 행이 누적된다 — 소비자는 그 사실을 알고 읽어야 한다.**
      그룹에서 빠진 상품, 삭제된 그룹, 옮겨간 소재의 행이 **영구히 남는다**. 이 테이블을
      "지금의 매핑"으로 그대로 믿으면 target ROAS·프록시 매출·예산 증액 판단이 옛 상품에
      끌려간다. 각 소비자는 자기 판단이 stale에 얼마나 민감한지 스스로 알고 써야 한다.

    ★**신선도 정책은 아직 없다**(2026-08-03 현재). `synced_at`(이번 회차에 관측된 행만 갱신)이
      유일한 단서지만, 그것만으로 창을 정할 수 없다 — 오래된 `synced_at`은 ⓐ실제로 사라진 행
      ⓑ네이버 부분 200으로 이번에 안 보인 행 ⓒget_ads 실패·시간 예산 이월로 **아직 안 본** 행을
      구분하지 못한다. 특히 커서 순회가 한 바퀴 도는 데 며칠이 걸릴 수 있어(정상 주기 실측
      미확보), 근거 없는 상수로 창을 잡으면 **살아 있는 매핑이 창 밖으로 밀려난다.**
      → 창을 정하려면 `shopping_ad_product_sync`가 로깅하는 `elapsed_s`로 **커서 한 바퀴 실측**이
        선행돼야 한다. **자동운영 재개 전 선행조건**(스코프 밖으로 분리된 항목).

    ★단 하나 지금 닫혀 있는 것: **같은 `ad_id`가 여러 행에 존재**할 수 있다는 문제
      (unique 키가 (adgroup_id, mall_product_id)뿐이라, 소재가 그룹 A→B로 옮기면 두 행이 남는다).
      `ad_id`를 **실행 레버**로 쓰는 경로는 `synced_at`이 가장 최근인 행 하나만 채택한다
      (`effective_bid` 참조) — 신선도 창과 무관한 판정이라 age-out 위험이 없다.
    campaign_target_resolver 우선순위 ②(상품 파생 target_roas)가 이 매핑을 소비한다.

    ★B1(스프린트 B, D-NAO-65): 소재-레벨 실효입찰 인식·저장. 각 SHOPPING_PRODUCT_AD 소재의
    Ad.adAttr({"bidAmt":N,"useGroupBidAmt":bool})·userLock을 additive nullable 컬럼으로 적재
    (grain 정확 일치 — 소재 1개 = (adgroup_id, mall_product_id) 1행). useGroupBidAmt=false 소재는
    소재 개별 bidAmt가 실효 입찰이고 그룹입찰을 무시한다(공식 apidoc, D-NAO-65 B 선행 실측). 기존
    행은 NULL(하위호환, backfill 불필요) — 다음 08:20 sync가 채운다. B1은 읽기 전용(파생·제어는 B2/B3).
    """

    __tablename__ = "naver_adgroup_product"
    __table_args__ = (
        UniqueConstraint("adgroup_id", "mall_product_id", name="uq_naver_adgroup_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    mall_product_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # = naver_product_bep.channel_product_id
    product_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # B1(D-NAO-65): 소재-레벨 입찰(additive nullable — 미수집/비쇼핑/adAttr부재 시 NULL).
    ad_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 소재 nccAdId
    ad_bid_amt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # adAttr.bidAmt(소재 개별 입찰)
    use_group_bid_amt: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # false면 소재 bidAmt가 실효
    ad_user_lock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 소재 userLock
    # D-NAO-127: 소재 외부 변경 탐지 앵커 — 네이버 소재 객체의 editTm 원문(마지막 수정 시각).
    # 라이브 실측(2026-07-29): UTC ISO8601 "2026-07-29T06:39:05.000Z" 형식으로 /ncc/ads 목록
    # 응답에 실려 온다(추가 GET 0). ★값 비교만으로는 "우리가 800으로 바꾼 걸 외부가 1,000으로
    # 되돌림"이 무변동으로 보이지만 editTm은 단조 전진하므로 편집 사실 자체가 남는다.
    # 문자열 원문 그대로 보관한다 — 판정은 동등비교뿐이라 파싱이 필요 없다(파싱은 표시용).
    ad_edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # D-NAO-137 S1: referenceData.APPLY_TM 원문(에폭 밀리초). ★**관측 적재 전용 — 판정 미사용.**
    # 왜 필요한가: `ad_edit_tm`은 대행사가 만져도 전진하지만 **네이버가 상품 피드를 재적용해도
    # 전진한다**(2026-08-03 실측: 전진 233건 중 229건이 피드 재적용이고 광고 설정은 무변동,
    # 실제 조작은 4건). 즉 editTm 단독으로는 신호 4 : 잡음 229를 못 가른다. 후보 판별자가
    # `editTm − APPLY_TM`(그날 표본에서 ≤120초 229건 : >1시간 4건으로 완전 분리)인데,
    # **APPLY_TM은 API가 현재값만 주므로 과거분 소급 수집이 원리적으로 불가능하다** — 지금부터
    # 적재하지 않으면 표본이 영영 n=1이다. 그래서 판정 배선보다 적재를 먼저 한다.
    # ★임계 120초를 여기에 굳히지 않는다: 표본 1일치에서 나온 경험값이고, `APPLY_TM`의 의미
    # 자체가 실측 추론이다(공식 스웨거에 필드는 있으나 설명문 없음). 판별자 배선은 관측을
    # 며칠 쌓은 뒤 별도 슬라이스(S2)에서 한다.
    # 기존 행은 NULL(하위호환·backfill 불가) — 다음 07:45 sync가 관측한 행부터 채운다.
    ad_apply_tm: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverBidEstimateDaily(Base):
    """네이버 쇼핑 소재별 시장가 사다리 일별 축적 (CS 스프린트 SA2, market_bid_probe).

    grain: (date, ad_id, device, position). 소스: /npla-estimate/average-position-bid/id
    (순위 1~4 필요 입찰가) + /npla-estimate/exposure-minimum-bid/id(최소노출입찰가).

    position: 1~4 = 평균 노출순위별 필요 입찰가. **0 = 최소노출입찰가**(별도 엔드포인트 값을
      같은 테이블 grain에 담기 위한 가상 position — 실제 API position이 아니다).
      ★position 5 이상은 API가 400으로 거부한다(라이브 실측) — 사다리는 1~4가 전부.
    is_floor: 시세 무의미 표식(50원 이하 관측 또는 순위별 차등 없음). True인 행의 bid를
      실제 시장가로 오인해 입찰에 쓰면 안 된다(market_bid_probe.detect_floor 참조).

    ★신규 테이블 = 신규 grain. naver_ad_daily(그룹/키워드 grain)는 건드리지 않는다.
    """

    __tablename__ = "naver_bid_estimate_daily"
    __table_args__ = (
        UniqueConstraint("date", "ad_id", "device", "position", name="uq_naver_bid_estimate_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)  # 관례: Date 컬럼도 Mapped[datetime](기존 테이블 동일)
    ad_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # nccAdId(소재)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    device: Mapped[str] = mapped_column(String(8), nullable=False)  # MOBILE/PC
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # 1~4 순위 / 0=최소노출입찰가
    bid: Mapped[int] = mapped_column(Integer, nullable=False)
    is_floor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverCampaignSettings(Base):
    """캠페인별 관리 주체·모드 (D-NAO-13). 진단·리포트는 전 캠페인, 제안·실행은 optimizer='ours'만.

    optimizer: none(수동, 기본)/ours(스마트스토어 직접)/mop(MOP 소유, 우리는 손 안 댐).
    한 캠페인 두 옵티마이저 동시 관리 금지 — execution_harness가 쓰기 직전 재검증.
    """

    __tablename__ = "naver_campaign_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    optimizer: Mapped[str] = mapped_column(String(8), nullable=False, default="none")  # none/ours/mop
    mode: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # growth/recovery/launch/defense
    target_roas_override: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    gamma: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 2), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # D-NAO-49: 자동 운영(auto_operator SA) 대상 스위치 — True인 캠페인만 일/시간당 레인이
    # 심사·집행한다. 킬스위치 = 이 플래그 OFF(Jino "04 자동운영 중지" → 즉시 UPDATE).
    auto_operate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # UI1(D-NAO-65): loss 대응 정책(캠페인별 예외 스위치). NULL/'leash'=기본(고삐-일일리셋,
    # DL 스프린트 구현·불변), 'stoploss_pause'=종전 하드 정지로 회귀. 전역 기본값은 여전히
    # 고삐라 additive nullable(기존 행 무영향·회귀 0). ★쓰기는 Router PUT /campaign-settings/
    # loss-policy 하나뿐 — 위임·자동 레인 어디서도 이 값을 바꾸지 않는다(§0 금지선).
    loss_policy: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # BP(D-NAO-102): 예산 페이싱 레인의 그날 기준 일예산(장중 증액 직전 값). ①증액 캡 =
    # base×2(같은 날 여러 번 증액해도 복리 금지) ②익일 00:05 원복 목표값. NULL=미시드
    # (BP 레인이 그날 첫 평가에서 현재 예산으로 시드 — 사람이 콘솔에서 바꾼 값도 이때 흡수).
    # ★쓰기 주체는 BP 레인(auto_operator._run_budget_pacing_lane) 하나뿐.
    base_daily_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # D-NAO-248 부록 Q3(풀링 경계 축 ⓑ): 실험 배치 라벨(A/B·MOP 열·대조군·홀드아웃). 지혜
    # 수확기(wisdom_candidates)의 전역 시그니처가 이 값을 못 봤다면 그 캠페인 관찰은 전역 풀에
    # 흡수되고, MOP 열 같은 대조군 관찰이 「우리 정책」 학습으로 오염된다. NULL=실험 배치 아님
    # (=전역 풀 참여 자격). 쓰기는 이 마이그레이션의 1행 시드(cmp-...8492582=MOP 열) 하나뿐 —
    # 이후 배치 지정은 사람이 콘솔/직접 UPDATE로 한다(자동 레인은 이 값을 안 씀).
    experiment_batch: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NaverAdgroupScope(Base):
    """자동운영 스코프의 «광고그룹» 축 (D-NAO-244). 캠페인 축(NaverCampaignSettings.auto_operate)
    아래에 한 단계를 더 두어, 캠페인 안 일부 광고그룹만 엔진에 맡기고 나머지는 손대지 않는 것을
    구조로 강제한다. Jino 원문 2026-08-24: *"우리 엔진의 스코프는 캠페인, 광고그룹 모두 포함해야해"*.

    ★결합 규칙은 «캠페인 마스터 ∧ 그룹 제한»이다 — 진리표(adgroup_scope.in_scope_now 단일 소스):

    | auto_operate | 이 캠페인의 스코프 행 | 그룹 g 판정 |
    |---|---|---|
    | OFF          | 무엇이든              | **전 그룹 OFF** (마스터 킬 불변 — 07-30 "모두 정지시켜줘"의 집행 경로) |
    | ON           | 없음                  | 전 그룹 ON (**기존 캠페인 행위 불변 — 소급 0**) |
    | ON           | 있음, g ∈ enabled     | ON |
    | ON           | 있음, g ∉ enabled     | **OFF** |

    「캠페인 OFF인데 그룹만 ON」은 지원하지 않는다 — 끄는 방향이 항상 이겨야(fail-safe) 하고,
    캠페인 OFF가 마스터 킬이 아니게 되는 순간 킬스위치의 의미가 흐려진다.

    ★행이 0개면 배포해도 행위 변화 0 — B3 카나리 게이트(D-NAO-282로 AD_BID_ROUTING_FALLBACK_CAMPAIGNS로 개명)가 세운
    「기본값은 아무것도 안 열림」 원칙을 테이블로 옮긴 것이다. 개시(행 삽입)는 별도 계약.

    role: accel/boundary/brake — «이 그룹에 무엇을 기대하는가»의 라벨. 판정과 역할별 가드가
    같은 것을 가리키게 하려고 데이터에 둔다. 엔진 산출 규칙 자체는 전역이고 이 라벨은
    판정·가드·화면용이지 입찰 로직의 분기가 아니다.
    """

    __tablename__ = "naver_adgroup_scope"
    __table_args__ = (
        UniqueConstraint("campaign_id", "adgroup_id", name="uq_naver_adgroup_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # accel(액셀·상향 기대) / boundary(경계·BEP 부근 볼륨 확장) / brake(브레이크·하향 기대)
    role: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    # False = 행은 남기되 잠시 끔(되돌리기 사다리의 첫 칸 — UPDATE 1문, 배포 불요)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NaverHourlySnapshot(Base):
    """시간별 캠페인 스냅샷 — 빠른 루프(관찰·페이싱) (D-NAO-4, 계획서 §2). 7일 롤링 보관.

    grain: (ad_date, campaign_id, snapshot_hour). 매시간 누적 지표(cost/clk/imp)를 스냅샷.
    소진율·페이싱·이상감지(P4)용. 직접 쓰기 금지(관찰만).
    """

    __tablename__ = "naver_hourly_snapshot"
    __table_args__ = (
        UniqueConstraint("ad_date", "campaign_id", "snapshot_hour", name="uq_naver_hourly_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)  # 수집 시각(KST)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)  # 대상 날짜(오늘)
    snapshot_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0~23 KST
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 당일 누적 비용
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    daily_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 소진율 계산용
    avg_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)  # D-NAO-46②: 캠페인 당일 누적 순위(avgRnk<=0→NULL)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverKeywordHourly(Base):
    """키워드/쇼핑그룹 grain 시간별 축적 — 일 1회 D-1 hh24 스윕, 영구 보존
    (D-NAO-46②, docs/PLAN_naver-ad-keyword-hourly-accrual.md §3).

    grain: (ad_date, entity_id, hour). WEB_SITE=키워드(nkw-…) entity_type='keyword',
    SHOPPING/BRAND_SEARCH=애드그룹(grp-…) entity_type='adgroup' — naver_ad_daily의
    keyword_id='' sentinel 규약과 동일 축. imp/clk/cost/conv_cnt는 hh24 breakdown 원본
    그대로(그 시간대 구간값 — 당일 누적 아님). avg_rank는 avgRnk<=0(무의미, 순위는
    1부터)이면 NULL. hh24 상세는 네이버가 최근 7일만 보존 — 이 테이블이 시간당 밴드
    관제(순위 2.5~4 유지)·학습 베이스라인의 유일한 영구 원료(ref 32 §4). 365일 롤링
    삭제(keyword_hourly_sweep.py).

    ★회계 불변(CD1 계승, D-NAO-60 RL1): conv_cnt는 시간당 전환 "건수"만(ccnt) — 매출
    금액(convAmt)은 hh24 breakdown에 없어(일별만 존재) 여기 못 받는다. 매출/BEP/ROAS
    등 회계 집계 코드는 이 컬럼을 절대 읽지 않는다(순수 관측 신호).
    """

    __tablename__ = "naver_keyword_hourly"
    __table_args__ = (
        UniqueConstraint("ad_date", "entity_id", "hour", name="uq_naver_keyword_hourly"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)  # 0~23 (name 라벨 파싱)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)  # keyword/adgroup
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # nkw-… / grp-…
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")  # keyword 행의 소속 그룹
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # WEB_SITE/SHOPPING/BRAND_SEARCH
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # 시간당 전환건수(ccnt, D-NAO-60 RL1) — 건수만·회계 미접촉
    avg_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # ⚠️UTC(sqlite-server-default-now-is-utc) — 시간계산엔 미사용


class NaverAdgroupHourlyToday(Base):
    """당일(진행 중) 애드그룹 시간별 성과 — 매시 재수집·교체 (D-NAO-122).

    grain: (ad_date, adgroup_id, hour). 소스는 naver_keyword_hourly와 같은
    /stats?breakdown=hh24이지만 **테이블을 분리한다.**

    ★왜 naver_keyword_hourly에 넣지 않는가(codex 리뷰 P1, 2026-07-29): 그 테이블은
      "행이 있다 = 그 날 하루가 통째로 스윕됐다"는 완결 불변식 위에 서 있고,
      auto_operator._exploration_yesterday_flow가 그 불변식으로 어제 clk 창 합을
      신뢰한다(그 함수 docstring ①). 당일 진행분을 같은 테이블에 쓰면 자정을 넘긴
      순간 그 행들이 "어제 행"이 되어 판정을 통과하는데, 마지막 시간(23시)은 아직
      완결되지 않아 구조적으로 빠져 있다 → 롤링 24h 흐름 과소계상 → 잠겨 있어야 할
      확장·입찰상향이 열린다. 변경 전에는 어제 행이 아예 없어 fail-toward-hold로
      안전하게 빠졌으므로, 섞는 것은 순수한 퇴행이다.
      완결 여부를 hour=23 존재로 판정할 수도 없다 — hh24는 실적 있는 시간대만
      반환하므로 23시 노출 0인 그룹은 완전 스윕 후에도 23시 행이 없다.
    ★같은 이유로 이 테이블을 읽는 코드는 "부분 데이터"임을 알고 읽어야 한다.
      과거 완결 데이터가 필요하면 naver_keyword_hourly를 읽는다(두 테이블을 암묵적으로
      섞지 않는다 — naver_bid_estimate_daily가 세운 "신규 grain=신규 테이블" 규약 계승).

    ★회계 불변(D-NAO-60 RL1 계승): conv_cnt는 건수만(ccnt) — 금액(convAmt)은 hh24에
      없다. 매출/BEP/ROAS 집계는 이 컬럼을 읽지 않는다.
    """

    __tablename__ = "naver_adgroup_hourly_today"
    __table_args__ = (
        UniqueConstraint("ad_date", "adgroup_id", "hour", name="uq_naver_adgroup_hourly_today"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)  # 0~23, 완결된 시간만
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # ⚠️UTC


class NaverChangeLog(Base):
    """변경 1건 전건 기록 — 쓰기 유일 초크포인트 + 피드백 루프 (D-NAO-12/14, 계획서 §2).

    predicted_*(실행 전 estimate) vs actual_*(D+7/14 실측) → outcome 판정 → 학습 환류.
    action 예: add_negative_keyword / update_bid / update_budget. outcome:
    improved/declined/neutral/executed(검증전). P0에서는 스키마만(쓰기는 P3).
    """

    __tablename__ = "naver_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # campaign/adgroup/keyword
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 3소스 근거 요약
    predicted_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # estimate 예측(클릭·비용·전환)
    verify_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # D+7/14 검증 예정일
    actual_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 실측 결과
    outcome: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # improved/declined/neutral/executed
    # ★D-NAO-223(M3-b) — 목적함수 정합 채점축. `outcome`(전/후 RPC 배율)과 **나란히** 둔다.
    #   왜 별도 컬럼인가: `outcome`의 분모가 클릭이라 「클릭·매출이 함께 줄어도 매출이 덜
    #   줄었으면 개선」이 된다(ref 90 §2 — improved 전건 4/4가 매출 감소, id 761은 매출
    #   −48.3%인데 「개선」). 트랙 목표(D-NAO-59)는 총이익 «절대액»이라 자를 바꿔야 하는데,
    #   기존 값을 덮으면 「교정 전 채점기가 무엇을 찍었나」라는 증거가 사라진다(교훈 #274) →
    #   §8-Q1 확정 = 소급 UPDATE 없이 «별도 컬럼»에만 새 식을 적는다. 옛 자와 새 자가 같은
    #   행에 나란히 남아 대조가 영구 보존된다.
    #   식(D-NAO-225): **총이익 델타** — (cf 보정 매출 / bep_roas) − 비용 의 전/후 비교.
    #   bep_roas는 본전 ROAS(공헌이익률의 역수)라 «매출/BEP»가 그 매출이 낳은 공헌이익이고,
    #   광고비를 빼면 총이익이다 = D-NAO-59가 최대화하라고 한 그 양.
    #   ★§8-Q5의 초기 확정값은 «GAVE 배율»이었으나 구현 실측이 재사용을 반증했다 — GAVE엔
    #   비용을 빼는 항이 없어 적자 대상의 지출을 줄인 조치(총이익 증가)를 「매출이 줄었다」는
    #   이유로 악화로 읽는다(ref 90 정본 4건 전부 총이익 증가인데 GAVE 배율은 3건 declined).
    #   GAVE 점수는 «크기» 축으로 gave_before/gave_after에 계속 남는다(Q5 재사용은 그 형태로 생존).
    #   ★새 문턱을 만들지 않았다 — ±10% 배율 밴드는 «부호 있는 양»에 옮길 수 없다(−70,827 →
    #   −130은 0.002배지만 실제로는 큰 개선이다). 부호 비교만 하고 노이즈 방어는 기존 모수게이트.
    outcome_profit: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # improved/declined/neutral
    gave_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 조치 «전» 창 GAVE
    gave_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 조치 «후» 창 GAVE
    # §4-B ⑥ 값 정확도 라벨의 «원료»: product_bep(상품BEP 확보) / account_default(계정 블렌디드
    #   근사) / unavailable. ⚠️이 컬럼 존재만으로 ⑥이 달성되는 게 아니다 — ⑥의 판정면은
    #   성적표 «산출물»이고 그건 M3-a 소관이다. 여기서는 원료를 버리지 않는 데까지만 한다.
    bep_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # P5: 실제 API쓰기 없이 기록만
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # P5: 실행 시도 시각(dry-run 포함)
    # D-NAO-147: **외부** 변경이 실제로 일어난 시각(KST). `external_*` 감지 행에만 채워진다.
    # ★왜 필요한가: `changed_at`은 두 가지가 섞인 컬럼이다 — 우리 실집행 행에서는 곧 발생
    #   시각이지만, entity_sync가 남기는 `external_*` 행에서는 **우리가 알아챈 시각**이다.
    #   실측(2026-08-04): 10:49:25에 꺼진 광고그룹이 change_log에 18:33:51로 남아, 화면이
    #   "감지 시각 — 실제로 언제 손댔는지는 기록이 없습니다"라고 말했다. 같은 순간
    #   `naver_entity.edit_tm`은 10:49를 갖고 있었는데도.
    # ★`changed_at`을 고치지 않고 컬럼을 따로 두는 이유: changed_at은 쿨다운·echo 대조창·
    #   학습 루프가 "우리가 언제 썼나"로 소비하는 축이다. 거기에 외부 발생 시각을 섞으면
    #   그 소비자들이 조용히 틀린다. 두 시각은 의미가 다르므로 칸도 다르다.
    # NULL = 시각 불명(우리 실집행 행·창 밖·editTm 미제공). 화면은 NULL이면 종전대로
    # changed_at을 쓰고 `time_basis='detected'`로 표시한다.
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# ── B-1: change_log 쓰기 가드 (D-NAO-169, 2026-08-10 · 교훈 #196·#209) ──────────
# 왜 여기(모델 이벤트)인가: `NaverChangeLog(...)` 생성 지점이 **17곳**이고 앞으로 더 는다.
# 호출부마다 규약을 지키게 하는 방식은 이미 실패했다 — 2026-08-10에 내가 임시 스크립트로
# 두 행을 쓰면서 ①없는 action 이름(`manual_loss_stop`)을 지어내고 ②`changed_at`에 네이버
# editTm의 **UTC** 시각을 그대로 넣었다. 둘 다 내 합격기준(쓰기 성공·라이브 PAUSED) 안에서는
# 전부 초록이었고, 발견은 **우연**이었다.
#
# ★두 검사의 강도가 다른 이유(이 repo의 «거부+자백» 관례):
#   - **시각 규약은 하드 거부**한다. 판별이 명확하고(두 칸을 대조), 라이브 위반이 **0건**이라
#     정상 경로를 깰 위험이 없다.
#   - **action 이름은 경고만** 한다. 하드 거부하려면 change_log에 실제로 도달하는 action
#     전수 인벤토리가 필요한데, 코드의 action 리터럴에는 `WriteResult.action`(쓰기 어댑터)이
#     섞여 있어 **확신할 수 없다.** 확신 없는 거부는 돈 경로를 깬다 —
#     못 세는 것을 세는 척하지 않는다.
KNOWN_CHANGE_LOG_ACTIONS: frozenset[str] = frozenset({
    # 우리 실집행(dry_run=False)
    "update_bid", "set_user_lock", "manual_emergency_stop", "update_budget",
    "budget_up_pacing", "budget_down_pacing", "exclude_search_term",
    "create_campaign", "create_adgroup", "create_ad",
    "optimizer_change", "auto_operate_change", "add_negative_keyword",
    "update_expert_delegation", "system_status_change", "set_loss_policy",
    "update_guardrail_params",  # D-NAO-172 P1 — 봉투 파라미터 콘솔 PUT
    "reset_auto_up_base",  # D-NAO-287 — 자동 상향 누적 상한의 «기준점» 사람 리셋(입찰 변경 아님)
    "adgroup_scope_change",  # D-NAO-244 — PAO 스코프 행 upsert/삭제(단건·H5 일괄 공통)
    # 외부 변경 «탐지»(우리 쓰기가 아니다 — 주체 판정에서 agency 쪽으로 간다)
    "external_bid_change", "external_status_change", "external_qi_change",
    "external_keyword_added", "external_keyword_removed",
    # 시뮬·페이싱(dry_run=True)
    "flight_pacing", "flight_pacing_silent", "daily_reflection",
})

# changed_at ↔ executed_at 허용 오차. 실제 사고는 **9시간**(UTC↔KST)이었고, 라이브에는
# 30분을 넘는 행이 하나도 없다(2026-08-10 실측). 넉넉히 잡아도 그 사고는 확실히 걸린다.
_CHANGE_LOG_MAX_TIME_SKEW_SECONDS = 30 * 60


def _validate_change_log(mapper, connection, target) -> None:  # noqa: ANN001 — SQLAlchemy 시그니처
    """`NaverChangeLog` insert 직전 검증. 시각 규약 위반은 거부, 미등록 action은 경고."""
    import logging

    log = logging.getLogger(__name__)

    action = (target.action or "").strip()
    if action and action not in KNOWN_CHANGE_LOG_ACTIONS:
        # ★경고이지 거부가 아니다(위 §강도 참조). 새 action을 **의도적으로** 추가할 땐
        #   KNOWN_CHANGE_LOG_ACTIONS에 같이 넣어 이 경고를 끈다 — 그게 «지어낸 이름»과
        #   «새로 만든 이름»을 가르는 유일한 신호다.
        log.warning(
            "change_log 미등록 action=%r (entity=%s dry_run=%s) — 오타·즉흥 작명이면 고치고, "
            "의도된 신설이면 models.KNOWN_CHANGE_LOG_ACTIONS에 추가할 것 (교훈 #196)",
            action, target.entity_id, target.dry_run,
        )

    changed_at = target.changed_at
    executed_at = target.executed_at
    if isinstance(changed_at, datetime) and isinstance(executed_at, datetime):
        skew = abs((changed_at - executed_at).total_seconds())
        if skew > _CHANGE_LOG_MAX_TIME_SKEW_SECONDS:
            raise ValueError(
                f"change_log 시각 규약 위반: changed_at={changed_at} 과 executed_at={executed_at} 가 "
                f"{skew / 3600:.1f}시간 어긋났다. **두 칸 모두 KST naive**여야 한다 — 네이버 API의 "
                f"editTm은 UTC(`...Z`)라 그대로 넣으면 9시간 어긋난다(2026-08-10 실사고, "
                f"change_log 5854·5855). `app.utils.kst.kst_now()`를 쓸 것. (action={action!r})"
            )


event.listen(NaverChangeLog, "before_insert", _validate_change_log)


class NaverProposal(Base):
    """제안 1건 (D-NAO 계획서 §2). 진단→제안 카드→Slack→(승인)→change_log.

    type 예: negative_keyword/bid_up/bid_down/budget/new_setup. status:
    pending/approved/executing/rejected/expired/failed(X1a T3 — executing=실쓰기 직전 내구
    클레임, 성공 시 approved 복원·잔존하면 크래시로 쓰기 결과 불확실이라 사람 조사 대상 /
    failed=실쓰기 실패 fail-closed 마킹, 자동 재시도 차단·재시도는 사람이 콘솔에서 재승인).
    P0에서는 스키마만(생성은 P2).
    adgroup_id: 실쓰기 대상 광고그룹(X1a T3) — restricted-keywords API가 adgroupId 필수
    (ref 27 §8-1)라 negative_keyword 제안 생성 시점에 확정 저장(실행 시점 재해석 없음).
    다른 제안 유형은 None.
    approval_source: 승인 출처 감사(X1a T5) — 'console'(사람이 콘솔에서 승인, T4) /
    'delegation'(E2 위임 자동승인, delegation_gate). NULL = 아직 승인된 적 없음(pending 등).
    반려로는 지우지 않는다(이력 보존 — 반려됐던 승인의 출처도 남겨둔다).
    """

    __tablename__ = "naver_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    proposal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # campaign/adgroup/keyword
    target_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    adgroup_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # X1a T3 실쓰기 대상
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 무엇을/왜 3근거
    expected_effect: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 예상효과
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    slack_ts: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    executed_change_log_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approval_source: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # X1a T5: console/delegation
    target_bid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # X1b: bid_up/bid_down/growth_bid_up의 목표 입찰가(원) — 실행자는 이 컬럼만 읽는다(rationale 텍스트 파싱 금지)
    target_lock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # X1b: pause/resume 제안의 목표 userLock(true=정지, false=재개)
    target_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # P1(D-NAO-42-f): budget_up/budget_down의 목표 일예산(원, dailyBudget) — 실행자는 이 컬럼만 읽는다(rationale 텍스트 파싱 금지)
    budget_auto_eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # P1(D-NAO-42-f): 라운드 봉투 분류(Jino "넣어" 2026-07-13) — True=자율(위임 시 자동)/False=회당 라운드 캡 초과분(승인대기)/None=예산제안 아님
    # D-NAO-248 §4-A — 다음 단계(「승인=적용」 사슬)가 쓸 자리만 먼저 둔다. 이번 스프린트는
    # 컬럼·모델만 추가하고 쓰기 로직은 만들지 않는다(전부 nullable, 기존 행 무영향).
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 승인/반려 확정 시각
    decided_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 결정 주체(사람 id/레인 이름)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 결정 근거 메모


class NaverRetroSignal(Base):
    """상설 소급 채점(retro scoring) — 진단 보드 as-of 스냅샷 1행
    (D-NAO-45, docs/PLAN_naver-ad-retro-scoring.md §3).

    매일 08:30 크론(retro_scoring_loop)이 diagnosis.build_diagnosis를 asof 시점으로 그대로
    리플레이해 지목된 타깃마다 1행을 남기고(retro_snapshotter), D+3/D+7에 naver_ad_daily
    사후 실적으로 방향을 채점한다(retro_scorer, verdict=correct/gray/wrong/no_spend).
    cf_asof/bep_asof/target_asof는 asof 시점 값으로 고정(스냅샷 시점 렌즈) — 나중에 계정
    보정계수·BEP가 바뀌어도 이 행의 판정 기준(채점 재현성)은 변하지 않는다.

    board(6종)→direction 매핑(ref 31 §1-a 고정): bleeding_keywords/shopping_group_bep=down,
    starving_winners/shopping_group_growth=up, pause_candidates/shopping_pause_candidates=pause.
    resume류는 제외(정지 중 = 사후 관측 불가, 정직 경계).

    **정직 경계(ref 31 — 원칙22)**: 이것은 "신호가 옳은 방향이었나"의 방향 정확도 계기판이지
    인과 성과 검증이 아니다(인과 승격은 카나리 몫). entity 상태(입찰가·on/off)와 product
    BEP/target은 이력이 없어 스냅샷 시점 현재값을 쓴다(알려진 한계, 실행 개입 거의 0인
    관찰 기간이라 영향 미미).
    """

    __tablename__ = "naver_retro_signal"
    __table_args__ = (
        UniqueConstraint("asof_date", "board", "target_id", name="uq_naver_retro_signal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 반드시 kst_now() 명시(server_default=func.now()는 UTC)
    asof_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    board: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # down/up/pause
    grain: Mapped[str] = mapped_column(String(8), nullable=False)  # keyword/adgroup
    target_id: Mapped[str] = mapped_column(String(50), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # 스냅샷 시점 고정 렌즈(채점 재현성)
    cf_asof: Mapped[float] = mapped_column(Float, nullable=False)
    bep_asof: Mapped[float] = mapped_column(Float, nullable=False)
    target_asof: Mapped[float] = mapped_column(Float, nullable=False)
    cost_asof: Mapped[int] = mapped_column(Integer, nullable=False)
    roas_c_asof: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # D+3 채점
    verdict_d3: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # correct/gray/wrong/no_spend
    scored_d3_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cost_post3: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    conv_post3: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    roas_c_post3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bleed_post3: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # D+7 채점
    verdict_d7: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    scored_d7_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cost_post7: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    conv_post7: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    roas_c_post7: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bleed_post7: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # D-NAO-72: GAVE 페널티 점수(ref 26 ⑤) — 방향 채점(verdict)과 병렬로 남기는 크기 축.
    #   S = min{(roas_c/bep_asof)^γ, 1} × (cf 보정 매출). roas_c가 bep_asof 이상이면 매출 전액,
    #   미달이면 γ에 비례해 감점 = "이 제안이 실제 총이익에 얼마나 기여했나"(D-NAO-59·D-NAO-1
    #   정식화). no_spend(cost_post=0)면 0. γ=naver_campaign_settings.gamma(캠페인 공격성
    #   다이얼, 없으면 DEFAULT_GAMMA=1). penalty·roas_ratio는 roas_c_post/bep_asof로 재구성
    #   가능하니 점수만 저장(최소 스키마). asof 렌즈(cf_asof/bep_asof/gamma) 그대로 = 채점 재현성.
    gave_score_d3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gave_score_d7: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class NaverRetroPacingScore(Base):
    """trigger_pacing 경보(저속·과속) 소급 채점 1건
    (D-NAO-45, docs/PLAN_naver-ad-retro-scoring.md §3).

    proposal_id(naver_proposals.id, UNIQUE) — rationale 텍스트를 정규식으로 파싱(ref 31
    스크립트 패턴 고정, retro_pacing_scorer)해 그날 캠페인 sentinel(BACKFILL_SENTINEL_ADGROUP)
    최종 소진과 대조한다. 파싱 실패는 verdict='unparsed'로 즉시 기록(재시도 무한루프 방지) —
    sentinel 최종치가 아직 없으면(당일 미확정) 행 자체를 만들지 않고 다음 날 재시도한다
    (kind/alert_hour 등은 이 두 경우 모두 확정 불가할 수 있어 nullable).

    후속(스코프 밖, PLAN §2 OUT): trigger_watch가 구조화 필드를 직접 쓰게 바뀌면 이 텍스트
    파싱은 대체 대상.
    """

    __tablename__ = "naver_retro_pacing_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    alert_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # 저속/과속(unparsed는 None)
    alert_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    spent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # rationale 파싱값
    final_cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)  # 버킷: ref 31 §2와 동일
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class NaverKeywordCandidate(Base):
    """발굴 키워드 후보 (D-NAO-10, 계획서 §2). keywordstool/쿠팡이식/검색어리포트 → 탐색 → 판정.

    P0에서는 스키마만(발굴은 P4).
    """

    __tablename__ = "naver_keyword_candidates"
    __table_args__ = (
        UniqueConstraint("keyword", "source", name="uq_naver_keyword_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # keywordstool/coupang/search_term
    monthly_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 월 검색량
    competition: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # 경쟁도
    explore_started_at: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 탐색 투입일
    explore_result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 탐색 성적
    verdict: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # promote/reject/pending
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverLearningState(Base):
    """자율학습 파라미터 상태 (D-NAO-14, 계획서 §3.5). verify_harness가 유일 쓰기 주체.

    scope: campaign/keyword_type/global/action_type/entity. metric 예: proposal_accuracy/
    estimate_bias/conv_delay/discovery_winrate/hour_weight/bep_accuracy/bid_rank_slope.
    P0에서는 스키마만(환류는 P3/P5).
    학습 경계: 파라미터만 조정, 가드레일 상수·권한 단계는 학습 대상 아님.

    ★scope 규약(scope_key 형태):
    - campaign: scope_key=campaign_id
    - keyword_type: scope_key=WEB_SITE/SHOPPING/BRAND_SEARCH
    - global: scope_key=상황버킷(day_N 등)
    - action_type: scope_key=proposal_type(expert_briefing 소급 정확도)
    - entity(IU-R R3, bid_rank_curve): scope_key="adgroup:<id>"/"keyword:<id>",
      metric="bid_rank_slope" — 유닛별 입찰→순위 반응 곡선 기울기(원/rank개선 1.0, 양수).
      current_value=기울기, sample_n=유효 관측쌍 수, confidence=적합도. rank_servo가
      response_prior로 소비(adgroup: prefix만).
    """

    __tablename__ = "naver_learning_state"
    __table_args__ = (
        UniqueConstraint("scope", "scope_key", "metric", name="uq_naver_learning_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # campaign/keyword_type/global
    scope_key: Mapped[str] = mapped_column(String(60), nullable=False, default="")  # 상황버킷 키
    metric: Mapped[str] = mapped_column(String(30), nullable=False)
    sample_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NaverConversionMaturitySnapshot(Base):
    """전환 성숙곡선 원료 적립 (D-NAO-14 학습루프3, Phase 6). naver_ad_daily는 upsert라 이력이
    안 남아(모델 docstring 참조) 이 테이블이 매일 "ad_date로부터 며칠째(days_since)에 얼마가
    찍혀 있었는지"를 별도 적립한다. grain: (ad_date, days_since) — 같은 ad_date라도 오늘 관측한
    days_since 값은 매일 1씩 증가하며 새 행으로 쌓인다(덮어쓰지 않음, 축적 자체가 목적).
    """

    __tablename__ = "naver_conversion_maturity_snapshot"
    __table_args__ = (
        UniqueConstraint("ad_date", "days_since", name="uq_naver_conv_maturity_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    days_since: Mapped[int] = mapped_column(Integer, nullable=False)
    direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverHourlyPatternHistory(Base):
    """요일×시간(168칸) 성과 분포 무기한 누적 (D-NAO-14 학습루프5, Phase 6). naver_hourly_snapshot
    은 7일 롤링 삭제(hourly_snapshot.py _RETAIN_DAYS)라 여러 주 누적이 불가능 — 이 테이블이
    매일 전날의 시간대별 순증분(hourly_pacing 계산)을 요일×시간 버킷에 무기한 합산한다.
    grain: (weekday, hour). weekday=Python date.weekday()(0=월~6=일).
    """

    __tablename__ = "naver_hourly_pattern_history"
    __table_args__ = (
        UniqueConstraint("weekday", "hour", name="uq_naver_hourly_pattern_history"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    clk_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_folded_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ══════════════════════════════════════════════════════════════════
# P2-S1 데이터 기반 (계획서 §P2-S1, D-NAO-16~21 실행 전제)
# 실측 근거: docs/references/22_naver_sa_p2s1_recon.md
# ══════════════════════════════════════════════════════════════════
class NaverEntity(Base):
    """네이버 SA 광고 엔티티 인벤토리 — 이름·상태·계층 (P2-S1).

    grain: (entity_type, entity_id). entity_type: campaign/adgroup/keyword.
    keyword 행은 **WEB_SITE(파워링크) 캠페인 소속만** 동기화 대상 — 원칙22 실측(2026-07-07):
    /ncc/keywords 전체 등록 키워드는 WEB_SITE 90,150 · SHOPPING 33 · BRAND_SEARCH 196
    (트랙 파일의 "파워링크 4,936개"는 최근 16일 AD 리포트에 노출이 찍힌 키워드 수 — 등록
    전체가 아니었음. 이 격차 자체가 D-NAO-18 죽은키워드 위생의 실제 스케일을 보여줌).
    SHOPPING은 AD 리포트에서 keyword_id='-'(그룹 단위)로만 집계되어 개별 키워드 진단 대상이
    아니므로 keyword 행 동기화 제외(campaign·adgroup 행은 전 유형 동기화).
    status는 userLock(true=OFF)+엔티티 status를 병합해 on/off/deleted로 정규화.
    monthly_volume/competition은 keywordstool 조회 결과(저클릭 키워드 대상, 선택적 갱신).
    """

    __tablename__ = "naver_entity"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_naver_entity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # campaign/adgroup/keyword
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    parent_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")  # adgroup→campaign_id / keyword→adgroup_id
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)  # 전 행 공통(조인 편의)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # WEB_SITE/SHOPPING/BRAND_SEARCH
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")  # 캠페인/그룹명 또는 키워드 텍스트
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="on")  # on/off/deleted
    # 네이버 statusReason 원문(D-NAO-97) — status가 'on'인데 실제로 안 도는 이유를 담는 유일한 필드
    # (CAMPAIGN_LIMITED_BY_BUDGET=일예산 소진 / CAMPAIGN_PAUSED=상위 캠페인 OFF / *_UNDER_REVIEW 등).
    # ★status(on/off)는 사람의 On/Off 스위치(userLock)만 반영한다 — 이 둘을 섞지 않는다.
    status_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bid_amt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그룹 기본가·키워드 개별입찰
    monthly_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # keywordstool PC+Mobile 합
    competition: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # low/mid/high
    volume_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    qi_grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 품질지수 1~7(D-NAO-46②, keyword 행만 — /ncc/keywords nccQi.qiGrade)
    # D-NAO-146: 네이버가 준 마지막 수정 시각(editTm) 원문. campaign/adgroup/**keyword** 전부
    # 채운다 — `/ncc/campaigns`·`/ncc/adgroups`·`/ncc/keywords` 응답에 모두 실려 온다(추가 GET 0.
    # 키워드는 D-NAO-147에서 확인: 표본 41/41 = 100%).
    # ★synced_at은 '우리가 본 시각'이고 이건 '네이버에서 실제로 바뀐 시각'이다 — 섞지 않는다.
    # 소재 grain(naver_adgroup_product.ad_edit_tm)과 같은 규약: 문자열 원문 보관, 파싱은 소비 지점.
    edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # D-NAO-148: 네이버가 준 **생성** 시각(regTm) 원문. campaign/adgroup/keyword 전부 응답에
    # 실려 온다(라이브 실측 46/46 · 96/96 · 41/41 = 100%, 추가 GET 0).
    # ★edit_tm과 성질이 다르다 — regTm은 **불변**이다(실측: 어제 정지된 그룹이 reg=2026-01-20 ·
    # edit=2026-08-04 10:49:25). 그래서 edit_tm에 금지된 소급 백필이 이 필드엔 성립한다
    # (LESSONS #119는 "마지막 수정만 남는다"가 전제였고, 생성 시각엔 그 전제가 없다).
    # 소비처는 신설 op의 occurred_at 하나뿐이다(bm_diff._REG_OPS · entity_sync 키워드 등록).
    reg_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # D-NAO-218(M2-b2): 기기별 입찰가중치(`/ncc/adgroups` pcNetworkBidWeight·mobileNetworkBidWeight,
    # 공식 Swagger 확정 range 10~500·기본 100 — adgroup 행에만 실린다, campaign/keyword는 항상 NULL).
    # 실효 입찰가 = 명목 bid_amt × (이 값/100). NULL = "아직 안 채워짐"과 100(=진짜 가중치 없음)을
    # 구분해야 하므로 기본값을 두지 않는다(소비처가 NULL/100을 각각 로깅 — ref 65 정정 #2·앵커 스펙).
    pc_bid_weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mobile_bid_weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverEntitySnapshot(Base):
    """대행사 포함 45캠페인 구조의 날짜별 history (SA-1, D-NAO-78 · BM 벤치마크 레이어).

    naver_entity는 upsert라 '현재 상태'만 남아 역사가 없다 → 이 테이블이 매일 아침(entity_sync
    완료 직후 체이닝) 캠페인·그룹 grain 구조를 스냅샷(0 GET, naver_entity DB만 읽음). 관찰 전용 — 네이버 API 쓰기 0.
    키워드 grain은 저장 안 함(90,150행/일 × 365 = 3,300만행/년 회피): 그룹 행에 키워드 집계
    (keyword_count·keyword_avg_bid)만 남기고 개별 키워드 변화는 이벤트(naver_agency_op, P2)로
    잡는다. optimizer는 naver_campaign_settings 조인(none=대행사 관찰 대상/ours/mop).

    Phase 1(이 커밋)은 name/status/optimizer/bid_amt/keyword_count/keyword_avg_bid만 채운다.
    daily_budget·extended_search(일별, P3)·negative_kw_count·ad_count(주간 deep, P3)는 additive
    nullable — 미수집 시 NULL(하위호환·backfill 불필요). 보존 400일 롤링(P6).
    entity_observed_at·p3_observed_at(D-NAO-93)은 필드 출처별 **관측** 시각 — bm_diff가 op_type별로
    맞는 change_log 대조창 상한을 잡는 데 쓴다(구 행 NULL이면 synced_at 폴백).
    """

    __tablename__ = "naver_entity_snapshot"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "entity_type", "entity_id", name="uq_naver_entity_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)  # KST 스냅샷 날짜(kst_today, ★UTC 아님)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)  # campaign/adgroup
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    parent_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")  # adgroup→campaign_id
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # WEB_SITE/SHOPPING/BRAND_SEARCH
    optimizer: Mapped[str] = mapped_column(String(8), nullable=False, default="none")  # none/ours/mop(대행사 구분)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="on")  # on/off/deleted
    # ── 구조 지표(SA-3 벤치마크 원료) ──
    daily_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 캠페인 dailyBudget(get_campaigns_full, P3)
    bid_amt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그룹 기본입찰
    extended_search: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # 그룹 확장검색 on/off(get_adgroups, P3)
    keyword_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그룹 활성 키워드 수(naver_entity 집계, WEB_SITE만 유효)
    keyword_avg_bid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그룹 키워드 평균 입찰(밴드 산출용)
    negative_kw_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 제외키워드 수(주간 deep GET, P3)
    ad_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 소재 수(주간 deep GET, P3)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # ★쓰기 시 kst_now 명시 주입(server_default는 UTC — 시간계산 미사용)
    # ── 필드 출처별 관측 시각(D-NAO-93 · bm_diff 대조창 앵커) ──
    # synced_at은 스냅샷 **복사** 시각이라 필드별 실관측 시각과 어긋난다: 입찰·상태·키워드집계는
    # 앞선 entity_sync가 본 값(실측상 D-1 관측)이고, 예산·확장검색은 스냅샷 시작 몇 분 뒤 P3 GET
    # 값이다. 둘 다 additive nullable — 구 행은 NULL이고 bm_diff가 synced_at으로 폴백(종전 동작
    # 그대로, backfill 불필요).
    entity_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # bid_amt/status/name/keyword_* 관측 시각(=NaverEntity.synced_at 복사, ★KST naive)
    p3_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # daily_budget/extended_search 관측 시각(P3 GET 직후 kst_now, ★KST naive)
    # D-NAO-146: NaverEntity.edit_tm 복사(네이버 editTm 원문). bm_diff가 이 값을 op의
    # occurred_at으로 승격한다 — 단 **직전 스냅샷 관측 ~ 이번 관측 창 안일 때만**.
    # ★관측 시각 짝은 entity_observed_at이다(edit_tm은 entity_sync의 GET에서 온다). 예산·확장검색은
    # 몇 분 뒤 P3 GET 값이라 그 사이의 변경은 창 밖으로 떨어져 NULL이 된다 — fail-closed가 맞다.
    # 구 행은 NULL(backfill 불가: editTm은 마지막 수정만 남아 소급하면 판정이 썩는다, LESSONS #119).
    edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # D-NAO-148: NaverEntity.reg_tm 복사(네이버 regTm 원문 = 생성 시각). bm_diff가 **신설 op**
    # (campaign_add·adgroup_add)의 occurred_at으로 승격한다 — 창 안일 때만.
    # ★창의 하한이 다르다: 신설 엔티티는 직전 스냅샷에 행이 **없으므로** 행별 하한을 못 얻는다.
    # 대신 직전 스냅샷 **배치**의 관측 시각(그 날 관측된 행들의 max)을 하한으로 쓴다 —
    # "그때는 없었다"가 곧 "그 이후에 생겼다"이기 때문. 상한은 다른 op와 같이 이 행의 관측 시각.
    # 창 밖(예: 부활·추적 개시로 2022년 regTm)은 NULL — 옛 생성 시각을 어제 사건으로 적지 않는다.
    reg_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class NaverAgencyOp(Base):
    """대행사(및 계정 전체 외부) 조작 이벤트 1건 (SA-2, D-NAO-78 · BM 벤치마크 레이어).

    naver_entity_snapshot의 D-1 vs D diff로 산출 — 결정적·리플레이 가능(bm_diff.py). 예외
    브리핑(P5)의 원료. 관찰 전용 — 네이버 API 쓰기 0(diff는 DB-to-DB).
    ★naver_change_log와 분리: change_log는 OUR 제안·집행의 피드백 루프(proposal_id·outcome·
    verify)에 묶여 있어, 45캠페인 대행사 노이즈를 섞으면 학습 쿼리가 오염된다(§2 결정).
    op_date+entity_id+op_type을 리플레이 키로 삼아 같은 날 재실행은 삭제-재생성(멱등).

    ★프로듀서가 둘이다(D-NAO-127): bm_diff(스냅샷 유래 campaign/adgroup grain) + ad_external_change
    (소재 grain, editTm 앵커). bm_diff의 멱등 재생성은 **자기 산출물만** 지운다(entity_type != 'ad')
    — 날짜 단위로 통째로 지우면 다른 프로듀서의 관측이 조용히 사라진다.
    """

    __tablename__ = "naver_agency_op"
    __table_args__ = (
        Index("ix_naver_agency_op_date_campaign", "op_date", "campaign_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    op_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)  # 조작 감지일(=오늘 스냅샷 날짜, KST)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now() 명시(server_default=UTC 회피)
    entity_type: Mapped[str] = mapped_column(String(10), nullable=False)  # campaign/adgroup/keyword
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    optimizer: Mapped[str] = mapped_column(String(8), nullable=False, default="none")  # 조작 주체 구분(대행사=none/mop, ours 자기변경 필터 대상)
    op_type: Mapped[str] = mapped_column(String(24), nullable=False)  # bid_change/status_flip/keyword_add/keyword_remove/campaign_add/adgroup_add/campaign_remove/adgroup_remove/negative_add/negative_remove/creative_change/budget_change/extended_toggle + (ad grain, D-NAO-127) bid_mode_flip/ad_edit
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 변화 크기(입찰 Δ%·예산 Δ%·키워드 증감 등 — 예외 랭킹용)
    is_exception: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 예외 브리핑에 올릴 이상치 여부(SA-2 필터 판정)
    # D-NAO-127: 그 조작이 **실제로 일어난** 시각(KST). 소스가 시각을 줄 때만 채운다 —
    # ad grain은 네이버 editTm으로 초 단위 확정. NULL = 일별 스냅샷 diff(campaign/adgroup grain)라
    # 시각 불명. op_date·detected_at은 '우리가 감지한' 시각이라 "언제 손댔나"에 답하지 못한다.
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ── D-NAO-139: 피드 재적용 판별(소재 grain 전용, 그 외 grain은 전부 NULL) ──
    # `ad_edit_tm`은 대행사가 만져도 전진하지만 **네이버가 상품 피드를 재적용해도 전진**한다.
    # 판별: 같은 상품의 소재가 **전량** 같은 초로 움직였으면 피드(사람은 하나씩 만진다).
    # 규칙·검증·실패 모드는 `services/naver_ad/feed_reapply.py` docstring.
    # ★**조회 시 계산이 아니라 탐지 시점에 계산해 저장**한다: `naver_adgroup_product`는 누적
    #   테이블이라 stale 행이 쌓이며 total이 계속 커지고, 그러면 과거 이벤트의 판정이 조회할
    #   때마다 흔들린다. 그래서 판정 근거 숫자(moved/total)까지 그 시점 값으로 굳혀 둔다.
    feed_verdict: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # feed/real/unknown
    feed_product_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 판별에 쓴 mall_product_id
    feed_moved: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그 창에서 함께 움직인 소재 수
    feed_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그 상품의 소재 수(판정 시점)
    # 군집 시작 시각 — 화면이 형제 줄을 한 줄로 접는 키. 08-04 실측으로 판별 창이 "같은 초"에서
    # 최대 수백 초로 넓어지면서 소재마다 시각이 달라졌고, 그래서 시각 자체로는 같은 사건을
    # 더 이상 묶을 수 없다(간격 실측 0·66·437·501초).
    feed_cluster_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 왜 그 판정이 나왔는지(feed_reapply.REASON_*). ★verdict만으로는 근거를 복원할 수 없다 —
    # 같은 `unknown`이라도 "소재가 1개뿐"과 "그 뒤 소재가 다시 수정돼 근거가 덮였다"는 전혀
    # 다른 상태이고, 같은 `real`이라도 "일부만 움직임"과 "추적 필드가 바뀜(가드)"은 다르다.
    feed_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)


class NaverChangeActorOverride(Base):
    """수정 주체 정정 1건 — 「수정 사항」 화면에서 사람이 단 주석.

    ★원천을 덮어쓰지 않는다. 주체는 두 원천(naver_change_log·naver_agency_op)에서
    **데이터로 자동 판정**하고(change_actor.py), 사람의 정정은 여기에 쌓아 읽을 때 겹친다.
    원천 컬럼에 써버리면 탐지 산출물과 사람 주석이 구분 불가가 되어 탐지 로직을 검증할 수
    없게 된다 — 그러면 "우리가 안 바꿨다"는 판정 자체를 못 믿는다.

    (source, source_id) = 원천 테이블 이름 + 그 테이블의 PK. FK를 걸지 않은 이유는
    ①원천이 둘이라 단일 FK로 표현 불가 ②bm_diff가 같은 날 재실행 시 자기 산출물을
    삭제-재생성하므로(멱등 리플레이) CASCADE면 정정이 조용히 사라진다. 고아 정정은
    조회 시 매칭 실패로 무시된다(무해).

    actor: ours(우리 자동화) / agency(대행사) / jino(Jino 본인). 화면 라벨과 1:1
    (change_actor.ACTOR_LABEL). ★코드베이스의 optimizer='mop'과 무관하다 — 그건 "제3자
    소유"라는 뜻이고 이 컬럼은 "누가 이 이벤트를 만들었나"다.
    """

    __tablename__ = "naver_change_actor_override"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_naver_change_actor_override"),
        Index("ix_naver_change_actor_override_source", "source", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(12), nullable=False)  # change_log/agency_op
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(12), nullable=False)  # ours/agency/jino
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now() 명시(server_default=UTC 회피)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now() 명시


class NaverBmBenchmark(Base):
    """대행사 구조↔성과 상관을 벤치마크化한 프라이어 1행 (SA-3, D-NAO-78 · BM 벤치마크 레이어).

    B-X(탐색 초기입찰)·SS4(승격 교차)·(향후)IU-R·L2가 optional 입력으로 소비한다 —
    전부 fail-open(부재 시 기존 동일, §0 금지선 4). 매일 아침(entity_sync 완료 직후 체이닝)
    재산출(bench_kind 단위 교체 upsert) — 최신 벤치마크만 유지한다(naver_product_bep snapshot 교체 관례 계승). 관찰 전용 —
    네이버 API 쓰기 0(DB만: naver_entity_snapshot + naver_ad_daily + naver_entity 조인 산출).

    ★저장소 택일(§9-2, P4 Opus 결정 = 혼용): keyword_verified/bid_band/group_structure는 값이
    구조(집합·[min,p50,max]·다차원)라 naver_learning_state의 단일 Numeric current_value에 안 맞아
    이 신규 테이블 value_json에 담는다. naver_learning_state는 verify_harness가 유일 쓰기 주체라
    (모델 docstring) 대행사 관찰값을 섞으면 학습 쿼리가 오염된다 — 관심사 분리. bid_rank_slope
    프라이어(향후, IU-R)는 단일 기울기라 반대로 naver_learning_state(scope=entity/metric=
    bid_rank_slope)에 써서 rank_servo가 기존 bid_rank_curve.load_response_priors로 무개조 소비한다
    (§2-b 혼용 — 배선 재사용). 이번 스코프는 slope 미산출(agency_op 이벤트 0건=원료 없음).

    bench_kind: keyword_verified(대행사 등록 키워드셋)/bid_band([min,p50,max])/group_structure
      (고성과 그룹 구조 요약). bench_key: campaign_type 버킷(WEB_SITE/SHOPPING/BRAND_SEARCH 등).
    value_json: 벤치마크 값(JSON 텍스트). sample_n/confidence: 표본 수·신뢰도(소비측 게이팅용).
    """

    __tablename__ = "naver_bm_benchmark"
    __table_args__ = (
        UniqueConstraint("bench_kind", "bench_key", name="uq_naver_bm_benchmark"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bench_kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # keyword_verified/bid_band/group_structure
    bench_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")  # campaign_type 등 버킷 키
    value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 벤치마크 값(JSON)
    sample_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now() 명시(server_default=UTC 회피)


class NaverSearchTermDaily(Base):
    """검색어 단위 일별 성과 — 쇼핑 SHOPPINGKEYWORD_DETAIL + 파워링크 EXPKEYWORD (P2-S1).

    grain: (ad_date, campaign_id, adgroup_id, search_term, source). 등록 키워드가 아닌
    **실제 검색된 텍스트**(쇼핑=상품 노출을 유발한 검색어, 파워링크=확장검색('-') 버킷이
    매칭한 검색어) — D-NAO-18③ 확장버킷 검색어 승격(성과 좋은 검색어를 정식 키워드로
    등록)의 원료.
    컬럼 실측(원칙22, docs/references/22): SHOPPINGKEYWORD_DETAIL 16컬럼 중 imp=col11·
    clk=col12·cost=col13(±1원 반올림)·rank_sum=col14 확정(prod naver_ad_daily 동일
    adgroup·동일 날짜 합계 대조: imp/clk 정확 일치, cost 1원 오차). EXPKEYWORD는 자동
    생성 안 됨 → POST /stat-reports로 생성 후 폴링(report_collector가 수행). avg_rank =
    rank_sum/imp.

    SS1(docs/PLAN_naver-ad-searchterm-ss.md §3): SHOPPINGKEYWORD_CONVERSION_DETAIL 전환을
    source='shopping' 행에 병합(conv_purchase_cnt/amt·conv_direct_cnt·cart_cnt/cart_amt).
    파워링크(source='expkeyword')는 전환 귀속 불가(§0.5 확정)라 전환 컬럼은 항상 0.
    """

    __tablename__ = "naver_search_term_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "campaign_id", "adgroup_id", "search_term", "source",
            name="uq_naver_search_term_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    search_term: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # shopping/expkeyword
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ── SS1(검색어 ROAS 레이어, docs/PLAN_naver-ad-searchterm-ss.md §3): 전환 병합 ──
    # SHOPPINGKEYWORD_CONVERSION_DETAIL을 source='shopping' 성과행에 병합(전부 additive).
    # conv_purchase_cnt/amt=직+간 합산(§1 제외 게이트=보수적 판정), conv_direct_cnt=직접만
    # (SS4 승격 신호). cart_cnt/amt=직+간 합산 장바구니(선행지표, ★매출 아님·회계 불활성).
    conv_purchase_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conv_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    conv_purchase_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cart_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cart_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverSearchTermDimDaily(Base):
    """쇼핑 검색어 리포트의 «버리던 세 축» 마진 적재 — 시간대·지역·매체 (D-NAO-198).

    grain: (ad_date, adgroup_id, dim_type, dim_value). 원료는 `NaverSearchTermDaily`와
    **같은 SHOPPINGKEYWORD_DETAIL 리포트**(추가 API 콜 0)이고, 그 테이블이 (일자×캠페인×
    그룹×검색어)로 뭉갤 때 버리는 col7/8/9를 축별로 살린다.

    ★왜 별도 테이블인가(D-NAO-182 근거 승계): 기존 `naver_search_term_daily`에 축을 더하면
    행이 수십 배가 되고, 성적표·SS레인·후보생성 등 **여러 소비자가 읽는 테이블의 의미가
    바뀐다**. 소비자를 건드리지 않으려면 옆에 두는 수밖에 없다.

    ★왜 결합(joint)이 아니라 마진인가(2026-08-18 실측): 결합 grain은 180일 3.43M행·**586MB**인데
    그중 **98.2%가 clk=0·cost=0인 «노출만 있는 칸»**이다. prod 디스크가 92%라(이 저장소는
    디스크 포화로 배치를 유실한 전력이 있다) 결합 전건은 못 싣는다. 그래서 **축별 마진은
    전건**(약 197MB) 싣고, 결합은 돈이 움직인 칸만 `NaverSearchTermDimCellDaily`에 따로 싣는다.
    ⚠️**버리는 것**: 노출만 있는 결합 칸의 «축 간 상호작용»은 저장되지 않고 180일 뒤 영구
    소실된다(축별 노출 자체는 이 표에 전건 남는다).

    dim_type — 'h'=시간대(col7, '00'~'23') · 'r'=지역(col8) · 'm'=매체(col9).
    ★**코드의 «뜻»은 여전히 미상이다**(D-NAO-198 «안 함»): 지역 코드 사전·매체 id 사전을
    만들지 않고 리포트가 준 코드값을 **그대로** 저장한다. 지역엔 `-1`이 섞여 나오는 날이 있다
    (뜻 미상 — 추정해서 채우지 않는다). ★매체 id는 **4~6자리 혼재**이고(`8753` 4자리 · 5자리 6종 ·
    6자리 30종, 172일 창 37종 실측), `MEDIA_TARGET` 블랙리스트의 media id와 **같은 코드 공간임이
    2026-08-19 실측으로 확정**됐다(D-NAO-201) — 「6자리」·「동일성 미확인」이라던 종전 서술은 둘 다
    틀렸다. 근거는 집합 교집합(정황)이 아니라 **인과 검정**이다: 블랙 (그룹,매체) 쌍 1,864건 중
    송출 실적이 있는 쌍은 1건뿐(imp 1)이고 그마저 블랙 등재 «당일»의 등재 전 노출인 반면, 같은
    그룹집합의 비-블랙 쌍은 197,835행·cost 41,101,626원이다. 코드 공간이 달랐다면 블랙 쌍도
    기저율로 송출됐어야 한다. 블랙리스트 원장은 `NaverAdgroupMediaBlack`.

    ★소실 시한: `SHOPPINGKEYWORD_DETAIL` 재생성 한도가 **정확히 180일**(day-180 BUILT ↔
    day-181 API 400/10004로 경계 실측 확정) — 매일 창이 굴러가 앞이 사라진다.
    """

    __tablename__ = "naver_search_term_dim_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "adgroup_id", "dim_type", "dim_value",
            name="uq_naver_search_term_dim_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    dim_type: Mapped[str] = mapped_column(String(1), nullable=False)   # h=시간대 r=지역 m=매체
    dim_value: Mapped[str] = mapped_column(String(20), nullable=False)  # 리포트 원본 코드 그대로
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverSearchTermDimCellDaily(Base):
    """위 세 축의 **결합** 셀 — 단 «돈이 움직인 칸»만 (D-NAO-198).

    grain: (ad_date, adgroup_id, hour_code, region_code, media_code).
    적재 조건: **clk > 0 또는 cost > 0**. 노출만 있는 칸(결합의 98.2%)은 싣지 않는다 —
    사유·대가는 `NaverSearchTermDimDaily` docstring 참조.

    ⚠️이 표만으로 «축별 합계»를 내면 틀린다(노출 칸이 빠져 있다). 축별 값은 반드시
    `naver_search_term_dim_daily`에서 읽는다. 이 표의 용도는 «비용이 난 자리의 축 간
    상호작용»(예: 어느 시간대·어느 매체에서 비용이 났나) 하나뿐이다.
    """

    __tablename__ = "naver_search_term_dim_cell_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "adgroup_id", "hour_code", "region_code", "media_code",
            name="uq_naver_search_term_dim_cell_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    hour_code: Mapped[str] = mapped_column(String(2), nullable=False)
    region_code: Mapped[str] = mapped_column(String(20), nullable=False)
    media_code: Mapped[str] = mapped_column(String(20), nullable=False)
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverAdgroupTargetCurrent(Base):
    """광고그룹 타겟팅 설정의 **현재 상태** 1행 — `/ncc/targets` 적재 (D-NAO-201, 축 A5·A6).

    grain: (adgroup_id). upsert — 이 표에 역사는 없다(역사는 `NaverAdgroupTargetChange`).

    ★왜 일일 스냅샷이 아니라 현재상태+변경이력인가: prod 디스크가 **92%**이고(이 저장소는
    디스크 포화로 배치를 유실한 전력이 있다) 타겟팅 설정은 거의 안 바뀐다 — 533그룹 실측에서
    `MEDIA_TARGET.editTm`이 2026년 이후인 것이 83건뿐이다. 매일 스냅샷을 쌓으면 1년에 100만 행이
    넘는데 그 대부분이 어제와 같은 값이다.

    ★**소급이 원리적으로 불가능하다**: `/ncc/targets`는 «지금»만 준다. 이미 적재된 172일 성과축
    (`NaverSearchTermDimDaily`)과의 교차는 전부 «지금의 설정» 기준이고, 개별 media가 언제
    등재됐는지는 **[미상]**이다 — `media_edit_tm`은 그 그룹 MEDIA_TARGET의 **마지막 수정 시각**
    이지 media 한 건의 등재 시각이 아니다. 이 표를 과거 성과에 붙일 땐 그 한계를 같이 적는다.

    ★A6(pc/mobile)는 **축으로서 퇴화했다**(D-NAO-201 Jino 결정 = 저장하되 퇴화로 기록):
    533그룹 실측에서 (pc=True, mobile=True)가 **525건(98.5%)**, (T,F) 4 · (F,T) 4다. 같은 응답에
    실려 오므로 저장 비용은 0이지만, 등급 교차의 재료로는 분산이 없다. 나중에 설정이 갈리면
    그때 살아난다.

    ★`/ncc/targets` 응답에 실재하는 `targetTp`는 **4종뿐**이다(533그룹 전수 실측):
    `MEDIA_TARGET` · `PC_MOBILE_TARGET` · `RESTRICT_KEYWORD_TARGET` · `NON_SEARCH_KEYWORD_TARGET`.
    `GENDER_TARGET`·`AGE_TARGET`·`TIME_WEEKLY_TARGET`·`REGIONAL_TARGET`은 **0건**이다 — ref 75
    §4-3의 「응답에 이미 포함돼 온다」는 Swagger 구조 추론이었고 실측과 어긋난다. 연령·성별은
    이 표면이 아니라 `/ncc/criterion`(D-NAO-197 ②)에서 와야 한다.
    """

    __tablename__ = "naver_adgroup_target_current"
    __table_args__ = (
        UniqueConstraint("adgroup_id", name="uq_naver_adgroup_target_current"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    # 프로브 결과 — 200이 아니면 나머지 필드는 «모름»이지 «없음»이 아니다(fail-closed).
    # 삭제된 광고그룹은 404 code:1018("No permission to access the resource")을 준다 —
    # 2026-08-19 실측: 404가 난 4그룹은 전부 naver_entity에서 status='deleted'였다.
    probe_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ── MEDIA_TARGET (A5) ──
    media_target_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    media_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    media_search: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # JSON 원문
    media_contents: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # JSON 원문
    media_white: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # JSON 원문
    black_media_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 정렬된 JSON 배열
    black_media_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    black_mediagroup_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_reg_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    media_edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # ── PC_MOBILE_TARGET (A6) ──
    pcm_target_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    pc: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    mobile: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    pcm_edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # ── RESTRICT_KEYWORD_TARGET (S6 제외 슬롯 사용량, D-NAO-264 · ref 66 §5-1) ──
    # 그 그룹에 **지금 걸려 있는 제외키워드 수**. 상한은 그룹당 70칸(ref 24·30 — 네이버 공식).
    # ★**nullable이 이 칸의 전부다**: `None` = 「셀 수 없었다」(프로브 비-200 또는 스키마 이상)
    #   이고 `0` = 「제외가 하나도 없다」다. 둘을 0으로 뭉개면 조회가 죽은 그룹이 **잔여 70칸의
    #   여유로운 초록**으로 보인다 — 이 계열 감시가 실제로 죽는 방식이다(교훈 #123).
    # ★원장(`NaverSearchTermExclusion`) 집계가 아니라 **라이브 count가 정본**이다(ref 66 §5-1).
    #   원장은 편입 누락·대행사 신규분만큼 적게 나오고, 그 차이 자체가 표면화 대상이다.
    restrict_keyword_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # ── 관측 메타 ──
    # 응답에 실재한 targetTp 목록(JSON). 「무엇이 없었나」를 나중에 되물을 수 있게 남긴다.
    target_types_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class NaverAdgroupMediaBlack(Base):
    """매체 블랙리스트 **현재 상태**를 조인 가능한 행으로 편 것 (D-NAO-201, 축 A5).

    grain: (adgroup_id, media_code). `NaverAdgroupTargetCurrent.black_media_json`과 같은 사실을
    담되, 이쪽은 **성과축과 조인하기 위한** 모양이다 — `naver_search_term_dim_daily`의
    `dim_type='m'` × `dim_value`가 상대편이고, 두 코드가 같은 공간임은 실측으로 확정됐다
    (그 근거는 `NaverSearchTermDimDaily` docstring).

    ★`media_code`는 **문자열**로 넣는다(`str(code)`). ⚠️단 이유를 정확히 적는다 — 2026-08-19에
    실측하니 **SQLite에서는 int로 넣어도 조인이 깨지지 않는다**(TEXT affinity가 저장 시 text로
    변환한다, `typeof()` 확인). 「숫자로 두면 조인이 조용히 0건이 된다」는 초판 서술은 **틀렸다**.
    진짜 이유는 둘이다: ①이 프로젝트의 DB 이행 목표가 PostgreSQL인데 거기선 VARCHAR 컬럼에
    int 바인딩이 **에러**가 난다 ②커밋 전 Python 측 비교(`b.media_code == d.dim_value`)는
    affinity의 보호를 못 받는다. ⇒ 이 규율을 지키는 변이 테스트는 SQLite에서 **원리적으로
    못 만든다**(변이 M1이 살아남는 이유가 결함이 아니라 affinity다 — 그 사실을 여기 남긴다).

    ★프로브가 200이 아닌 그룹의 행은 **지우지 않고 그대로 둔다**(fail-closed) — 조회 실패는
    「블랙이 사라졌다」가 아니라 「지금 못 본다」다. 그래서 이 표의 행은 «마지막으로 성공한
    관측»이고, 얼마나 묵었는지는 `observed_at`이 말한다. 한 번도 성공한 적 없는 그룹만
    행이 없는데, 그건 「블랙 0건」이 아니라 「모름」이다 —
    `NaverAdgroupTargetCurrent.probe_status`를 같이 봐야 갈린다.
    """

    __tablename__ = "naver_adgroup_media_black"
    __table_args__ = (
        UniqueConstraint("adgroup_id", "media_code", name="uq_naver_adgroup_media_black"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    media_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 이 그룹 MEDIA_TARGET의 마지막 수정 시각. ★media 한 건의 등재 시각이 아니다(위 docstring).
    source_edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverAdgroupTargetChange(Base):
    """타겟팅 설정이 **바뀐 순간만** 남기는 이벤트 원장 (D-NAO-201).

    grain: (adgroup_id, observed_at, field). 현재상태 표가 upsert라 역사를 잃는데, 「언제부터
    이랬나」는 성과 교차의 전제다 — 그래서 바뀐 것만 여기 쌓는다. 안 바뀐 날은 **행이 없다**
    (그래서 디스크 92%에서도 감당된다).

    ⚠️이 원장의 시작은 **최초 적재일**이다. 그 전의 변경은 존재하지 않는 게 아니라 **관측되지
    않았다** — 소급이 원리적으로 불가능하기 때문이다(API가 현재만 준다).
    """

    __tablename__ = "naver_adgroup_target_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    field: Mapped[str] = mapped_column(String(40), nullable=False)   # black_media / pc / mobile / media_type / probe_status …
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class NaverAdgroupCriterionCurrent(Base):
    """`/ncc/criterion/{ownerId}` 실설정 **현재 상태** (D-NAO-216, ref 65 S1-ⓐ 경로 정정).

    grain: (adgroup_id, criterion_type, dictionary_code). upsert — 역사는
    `NaverAdgroupCriterionChange`가 따로 쌓는다(같은 관례를 `NaverAdgroupTargetCurrent`
    /`NaverAdgroupTargetChange`에서 승계).

    ★**`NaverCriterionDaily`(D-NAO-203)와 다른 표다** — 그건 StatReport `CRITERION`
    **벌크 성과 리포트**(비용·클릭이 어느 세그먼트에서 났나)이고, 이 표는 **엔티티별 GET
    스윕**(`/ncc/criterion/{ownerId}`, 그룹마다 1콜)으로 얻는 **설정**(어느 세그먼트가
    타겟팅돼 있고 `bidWeight`가 몇인가)이다. 두 경로는 서로를 대체하지 않는다 — 성과 표는
    「무엇이 얼마나 팔렸나」를, 이 표는 「그 판매가 어떤 필터 아래서 났나」를 준다.

    ★**`naver_adgroup_target_current`(D-NAO-201)와도 다른 endpoint에서 왔다**:
    `/ncc/targets`는 매체·기기·제외키워드(MEDIA_TARGET·PC_MOBILE_TARGET·
    RESTRICT_KEYWORD_TARGET)를 주고, `bidWeight`는 그 응답에 **0건**이다(2026-08-21
    실측, `data/77_targets_surface/*.jsonl`). 연령·성별·요일시간 + `bidWeight`는
    `/ncc/criterion`에서만 온다 — ref 65가 08-17엔 이걸 한 경로로 잘못 적었던 것을
    D-NAO-216이 정정했다.

    ★★**C-0 함정(ref 58 §2) — 이 표에 들어오는 행은 전부 「진짜 설정」이어야 한다.**
    `/ncc/criterion` GET은 설정 안 된 축을 조회할 때마다 **기본값을 새로 합성**해
    돌려준다(예: 성별 미설정 그룹 → GNM/GNF/GNU 3행, `bidWeight=100`·`negative=false`,
    `regTm`=«방금 조회한 시각» — 1분 뒤 재조회하면 `regTm`이 다시 «지금»으로 찍힌다).
    ingest 층(`adgroup_criterion_ingest._apply_rows`)이 `is_synthetic=True`인 행을
    걸러내고 **진짜 설정만** 이 표에 쓴다 — 안 걸러내면 매 스윕이 「방금 누가 바꿨다」를
    변경 원장에 새기고, 이 표는 «타겟팅 설정»이 아니라 «오늘 우연히 합성된 기본값
    3~4천 행»으로 채워진다.

    criterion_type — 'AG'=연령 · 'GN'=성별 · 'SD'=요일·시간(2026-08-17 캡처 실측 3종).
    'AD'(관심사)는 이 GET 표면에서 **아직 관측되지 않았다** — StatReport 축엔 있지만
    (`NaverCriterionDaily` docstring) 그룹별 설정 GET에서 나온다는 보장은 없다. 실제로
    나오면 이 컬럼은 그대로 받는다(코드에서 화이트리스트로 막지 않는다).

    ⚠️`negative=true` 행의 `bid_weight`는 **제외 대상**에 붙은 값이라 실효 입찰 배율로
    읽으면 안 된다(2026-08-21 실측 각주 — fetcher docstring과 동일 경고).

    ★`campaign_id`는 스윕 대상 열거(`naver_entity`)에서 얻은 부가 정보이지 API 원값이
    아니다 — `/ncc/criterion` 응답엔 캠페인 식별자가 없다.
    """

    __tablename__ = "naver_adgroup_criterion_current"
    __table_args__ = (
        UniqueConstraint(
            "adgroup_id", "criterion_type", "dictionary_code",
            name="uq_naver_adgroup_criterion_current",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    criterion_type: Mapped[str] = mapped_column(String(4), nullable=False)
    dictionary_code: Mapped[str] = mapped_column(String(32), nullable=False)
    code_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    bid_weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    negative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    del_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reg_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class NaverAdgroupCriterionChange(Base):
    """criterion 설정이 **바뀐 순간만** 남기는 이벤트 원장 (D-NAO-216).

    grain: (adgroup_id, changed_at, criterion_type, dictionary_code, field). 값이 바뀐
    순간만 쌓는다 — 이 스윕의 존재 이유가 **대행사가 가중치를 건드린 것을 잡는 것**이라
    (계약 배경 문단) 「무엇이 언제 몇에서 몇으로 바뀌었나」가 이 표의 전부다.

    field는 최소 `bid_weight`·`negative`·`enable`(값 변화)과 `__row__`(신규 등장/삭제,
    old/new에 `null`/요약 문자열)를 쓴다 — `NaverAdgroupTargetChange`와 같은 관례
    (관측 메타뿐 아니라 등장·소멸 자체도 «변경»으로 남긴다).

    ⚠️이 원장의 시작은 **최초 적재일**이다. 그 전의 변경은 «없었다»가 아니라 «관측되지
    않았다» — API가 현재만 주므로 소급이 원리적으로 불가능하다(D-NAO-201과 동일 한계).
    """

    __tablename__ = "naver_adgroup_criterion_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    criterion_type: Mapped[str] = mapped_column(String(4), nullable=False)
    dictionary_code: Mapped[str] = mapped_column(String(32), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class NaverAdgroupCriterionProbe(Base):
    """그룹별 criterion 스윕 **결과 자체**의 기록 (D-NAO-216).

    grain: (adgroup_id) — unique, 그룹당 최신 1행(upsert, 역사 없음).

    ★★**이 표가 있는 이유**: `naver_adgroup_criterion_current`만 보면 「설정이 0건인
    그룹」과 「이번 스윕에서 조회 자체가 실패한 그룹」이 **똑같이 행 0개**로 보인다.
    그 둘을 가르지 못하면 실패를 「설정 없음」으로 **영구 기록**하게 된다(교훈 #318 —
    한 사실을 여러 표에 쓰면서 한쪽만 fail-closed면 나머지가 거짓을 영구 기록한다.
    `naver_adgroup_media_black`이 500 한 번에 「블랙이 사라졌다」를 9행 새겼던 그 사고와
    같은 모양). `probe_status`가 200이고 `row_count`가 0이면 **확인된 「설정 없음」**,
    `probe_status`가 200이 아니면 `row_count`는 신뢰하지 말고 **「모름」**으로 읽는다
    (실패 시 이전 값을 유지 — 덮어쓰지 않는다, ingest 층 참조).

    `row_count`는 **필터링 후**(C-0 합성 기본값 제거 후) 실제로 `naver_adgroup_criterion_current`
    에 반영된 행 수다 — 원응답의 raw 행 수가 아니다.
    """

    __tablename__ = "naver_adgroup_criterion_probe"
    __table_args__ = (
        UniqueConstraint("adgroup_id", name="uq_naver_adgroup_criterion_probe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    probe_status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class NaverCriterionDaily(Base):
    """연령·성별·관심사·요일시간(CRITERION) 성과 분해 — StatReport 벌크 경로 (D-NAO-203).

    grain: (ad_date, adgroup_id, criterion_code, device).

    ★**«엔티티별 GET 스윕»이 아니라 «리포트 1건»에서 온다.** `/ncc/criterion/{ownerId}` GET을
    광고그룹마다 도는 경로(1,013콜/일 감각)가 매트릭스의 기존 서술이었는데, 2026-08-19 실측으로
    StatReport `CRITERION` 벌크 경로가 **작동함이 확인**됐다(ref 75 ADS §4-4가 「존재 확인만,
    응답 스키마·성공 여부 미확인」으로 남긴 것을 실호출로 닫았다). ⇒ 하루 리포트 2건이면 끝난다.
    ★단 그 두 경로는 **다른 것**을 준다 — 이 표는 «성과 분해»이고, `/ncc/criterion` GET은
    «설정»(어느 세그먼트를 타겟팅하나 + bidWeight)이다. ref 73 #12의 bidWeight [미상]은
    이 표로 풀리지 않는다.

    ★★**축을 가로질러 합산하면 3중 계상된다.** AG(연령)·GN(성별)은 **같은 성과를 각각 100%
    분해**한 것이다 — 2026-08-17 실측: AG축 합계 imp 54,749·clk 540·cost 705,849 / GN축 합계
    54,749·540·705,851 / prod `naver_ad_daily` 계정 전체 705,847·540. 반드시
    `where criterion_type = 'AG'`처럼 **한 축으로 좁혀** 집계한다. D-NAO-194의 「fan-out을
    배분하지 않는다」와 같은 결.

    ★**이 축은 총이익에 닿는다** — 비용(이 표)과 전환매출(`NaverCriterionConvDaily`)이 같은
    grain에 있다. D-NAO-198(시간대·지역·매체)이 「전환·매출이 없어 총이익에 못 닿는다」로
    막혔던 지점을 넘는 첫 축이다.

    criterion_type — 'AG'=연령(12종) · 'GN'=성별(3종) · 'AD'=관심사(87종) · 'SD'=요일·시간.
    코드의 «뜻»은 추정하지 않는다 — `NaverCriterionDict`(1차 출처 `/ncc/criterion-dictionary`)에
    한글명이 있고, 2026-08-17 실측에서 리포트 코드 18종이 **전건 사전에 존재**했다(미상 0건).

    실측 분포(2026-08-17): AG 3,733행 · GN 1,549행 · AD 304행(= WEB_SITE 유형 비용 100%,
    쇼핑엔 관심사 타겟팅이 없다) · SD 16행(4,008원 — 매트릭스 #10의 「사실상 탈락」 재확인).

    ★**campaign_id를 싣지 않는다** — 이 리포트는 주지 않는다(7열: 일자·고객ID·`{그룹}~{코드}`·
    기기·노출·클릭·비용). 캠페인 유형 통제가 필요한 분석은 `naver_ad_daily`·`naver_entity`와
    조인해서 얻는다. ⚠️**유형 통제 없이 밴드를 비교하지 말 것**(ref 78 F20 — 유형 혼합 분모가
    A5 발견 하나를 죽였다).

    ★**소실 시한: 정확히 365일**(D-365 BUILT ↔ D-366 API 400 `{"code":10004}`로 경계 실측 확정,
    2026-08-19). 매일 창이 굴러가 앞이 사라진다 — 안 받은 날은 영구 소실이다.
    """

    __tablename__ = "naver_criterion_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "adgroup_id", "criterion_code", "device",
            name="uq_naver_criterion_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    criterion_type: Mapped[str] = mapped_column(String(2), nullable=False)   # AG/GN/AD/SD
    criterion_code: Mapped[str] = mapped_column(String(32), nullable=False)  # 리포트 원본 코드 그대로
    device: Mapped[str] = mapped_column(String(1), nullable=False)           # P=PC M=모바일
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverCriterionConvDaily(Base):
    """CRITERION 전환 분해 — 연령·성별·관심사 × 직접/간접 × 구매/장바구니 (D-NAO-203).

    grain: (ad_date, adgroup_id, criterion_code, device, conv_kind, conv_type).

    ★**계정 전환을 100% 분해한다** — 2026-08-17 AG축 합계와 prod `naver_ad_daily` 8값이
    자릿수까지 일치:
      (purchase, '1') 63건/952,200원 ≡ conv_direct_cnt/amt
      (purchase, '2') 14건/220,400원 ≡ conv_indirect_cnt/amt
      (add_to_cart, '1') 48건/986,900원 ≡ cart_direct_cnt/amt
      (add_to_cart, '2')  4건/ 95,500원 ≡ cart_indirect_cnt/amt
    ⇒ 이 등식이 적재 정합성의 **검산식**이다(합격기준 ⓑ). 어긋나면 파싱이 틀린 것이다.

    ★`NaverCriterionDaily`와 같은 중복분해 함정을 공유한다 — 축을 가로질러 더하지 말 것.

    conv_type — '1'=직접(전환 당일) · '2'=간접. conv_kind — 'purchase' · 'add_to_cart'.
    ⚠️`STCONV_COL_*`(SHOPPINGKEYWORD_CONVERSION_DETAIL, 15열)과 **컬럼 배치가 다르다**
    (기기 col10·직간접 col11·행동 col12 vs 여기 col3·col4·col5). 상수 재사용 금지.
    """

    __tablename__ = "naver_criterion_conv_daily"
    __table_args__ = (
        UniqueConstraint(
            "ad_date", "adgroup_id", "criterion_code", "device", "conv_kind", "conv_type",
            name="uq_naver_criterion_conv_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    criterion_type: Mapped[str] = mapped_column(String(2), nullable=False)
    criterion_code: Mapped[str] = mapped_column(String(32), nullable=False)
    device: Mapped[str] = mapped_column(String(1), nullable=False)
    conv_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # purchase / add_to_cart
    conv_type: Mapped[str] = mapped_column(String(1), nullable=False)   # 1=직접 2=간접
    conv_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conv_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverCriterionDict(Base):
    """criterion 코드 사전 — 1차 출처 `GET /ncc/criterion-dictionary/{type}` (D-NAO-203).

    ★**추정 등재 0건**이 이 표의 규약이다. 네이버가 `dictionaryCode`와 한글 `name`을 직접
    주므로 사전을 «만들» 필요가 없다(D-NAO-198의 「매체 코드 뜻 사전 안 만듦」과 상황이 다르다 —
    거긴 1차 출처가 아예 없었다). 사전에 없는 코드가 리포트에 나오면 **[미상]으로 두고**
    이 표에 추정 행을 넣지 않는다.

    적재 범위 = **AG(12) · GN(3) · AD(87) · SD(2,100)** = 2,202행. 리포트에 실제로 나오는
    네 type만 싣는다. RL(지역, 5,354종)은 CRITERION 리포트에 등장하지 않아 제외하고,
    RP·DV는 실물 0건이다(2026-08-19 실측 — ref 75가 「swagger에 코드값만 있고 설명 없음」으로
    남긴 RL·RP 중 RP는 사전 자체가 비어 있다).
    """

    __tablename__ = "naver_criterion_dict"
    __table_args__ = (
        UniqueConstraint("dictionary_code", name="uq_naver_criterion_dict"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dictionary_code: Mapped[str] = mapped_column(String(32), nullable=False)
    criterion_type: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class NaverSearchTermExclusion(Base):
    """파워링크 검색어 자동 제외 in-out 상태기계 1행 (스프린트 PX,
    docs/PLAN_naver-ad-powerlink-autoexclude.md §2). grain: (adgroup_id, search_term) — Unique.

    상태 전이(제외 = 사형이 아니라 상태 전이·주기 재심사, 전략 v2 §1④ in-out 생태계):
      excluded  — 자동 제외 실쓰기 완료(restrict_kwd_id 회수). next_review_at 도래 시 개방.
      probation — next_review_at 도래로 제외키워드를 개방(delete)한 뒤 재노출 관찰창(+14일).
      restored  — probation 만료 재판정에서 더는 §1 후보가 아님(성과 자가 교정, 행 보존=기억).
    cycle: 최초 1, 재제외마다 +1(백오프 next_review_at = today + min(30×cycle, 90)일). 행 재사용
    (restored/probation 행이 있는 (adgroup,term) 재제외 시 cycle 승계·upsert). restrict_kwd_id는
    개방(delete_restricted_keywords)에 필수라 성공 실쓰기의 WriteResult.created_ids에서 회수 저장.
    ★관측 전용 원장이 아니라 실쓰기와 짝(제외/개방 change_log와 1:1) — DB만 읽는 SA-1/2와 구분.
    """

    __tablename__ = "naver_search_term_exclusion"
    __table_args__ = (
        UniqueConstraint("adgroup_id", "search_term", name="uq_naver_search_term_exclusion"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    search_term: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    restrict_kwd_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 개방(delete)에 필수, WriteResult에서 회수
    # ★매칭 타입 원값 보존 (D-NAO-216 Q2-b, 계약 §8-Q2-b). SHOPPING의
    #   `RESTRICT_KEYWORD_TARGET.target[].type`(1/2)을 그대로 저장한다 — 1=exact(추정)·
    #   2=phrase(추정)이지만 Swagger에 의미 설명이 없어 **[미상]**이다(ref 77 §6). 그래서
    #   코드가 해석하지 않고 응답 원값을 문자열로만 보존한다(1/2 외 값이 와도 버리지 않고
    #   그대로 저장 — 로그는 채우는 쪽에서 남긴다).
    #   ★WEB_SITE 경로(`get_restricted_keywords`)의 `type`은 **다른 어휘**다
    #   (KEYWORD_PLUS_RESTRICT/EXP_SEARCH — 조회 카테고리 파라미터지 항목별 매칭 타입이
    #   아니다) — 그래서 이 칸은 SHOPPING 대조 경로(`exclusion_survival.check_survival`)
    #   에서만 채워진다. 두 어휘를 한 칸에 섞으면 나중에 「phrase 통합 후보를 이 칸으로
    #   고른다」(M2-c ⓒ, 70/70 도달 그룹의 무손실 배출구)가 오작동한다.
    match_type: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="excluded", index=True)  # excluded/probation/restored
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    excluded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now 주입(server_default 아님)
    last_transition_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 마지막 상태 전이 시각(KST)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)  # 재심사 개방 예정일(KST date)
    probation_until: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # probation 관찰창 종료일(KST date)
    cost_at_exclusion: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # 제외 시점 30d cost(감사)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # ⚠️UTC(server_default)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())  # ⚠️UTC
    # ── 조치 생존 감시(D-NAO-173 P1-①, docs/PLAN_search-term-exclusion-list.md §2-6) ──
    # ★이 세 칸이 없던 동안 «우리가 건 것이 아직 걸려 있는가»를 아무도 안 봤다. 대행사가 우리
    #   조치를 되돌린 게 2회(07-30 캠페인 재개 · 08-10 그룹 userLock 해제)이고 그중 한 번은
    #   우리 change_log에 흔적조차 없었다 — 즉 «되돌림 탐지»에 기대면 안 되고 **현재 상태를
    #   매일 대조**해야 한다. 대조 결과를 여기 적어 두면 헬스 배너가 라이브 API 없이 읽는다.
    # live_state: alive(라이브에 걸려 있음) / missing(사라짐) / deleted(delFlag=true 소프트삭제)
    #   / unknown(조회 실패·판별 불가 — fail-closed로 «이상»에 센다).
    # ★delFlag까지 봐야 한다: 존재 여부만 보면 소프트 삭제된 행을 «살아 있음»으로 오독한다.
    live_state: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)
    live_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # ★kst_now 주입
    live_note: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # unknown 사유·발견 경위
    # ── 이 행이 어디서 왔는가 (D-NAO-176) ──
    # ★NULL = 우리가 실행했거나 보고받은 조치(record_execution) — 일기가 있고 성적표가 판정한다.
    #   'console_import' = **콘솔에 이미 걸려 있던 것을 일괄 편입**한 행. 실행 시점을 모르므로
    #   전후 창을 잡을 수 없다 → 성적표가 판정하지 않고, 일기도 만들지 않는다(학습 입력 아님).
    #   감시 대상에는 들어간다 — 「우리가 아는 제외」의 목록이 현실과 같아야 하기 때문이다.
    # 이 구분이 없으면 편입 43건이 「오늘 실행한 조치 43건」으로 보이고, 13일 만의 진짜 표본
    # 1건(「골프」)이 그 안에 익사한다.
    source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    # ★콘솔이 보여준 «실제 제외 등록시각» (D-NAO-177). NULL = **모른다**(콘솔이 안 알려줬거나
    #   사람이 안 적었다) — 추정해서 채우지 않는다.
    #   `excluded_at`과 뜻이 다르다: 그쪽은 «장부가 이 행을 제외로 세운 시각»(편입분은 편입 시각)
    #   이고 void_execution의 일기 매칭 하한·생존 감시의 방치 판정이 그 뜻에 기대어 있다.
    #   실제 시각을 그 칸에 넣으면 2024년 날짜 하나로 일기 매칭 창이 1년 반으로 벌어지고
    #   편입 직후 전역 배너가 거짓 빨강이 된다. 그래서 칸을 나눈다.
    console_excluded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ── 제외 «임대» 등급 (S2, 계약 CONTRACT_ignition_readiness.md §4-B⑥) ──
    # ★왜 이 칸이 필요한가: `next_review_at`이 NULL인 행이 3,987/3,990인데, 그 NULL이
    #   «영구 제외»(무관 — 경쟁사 브랜드처럼 다시 볼 이유가 없다)인지 «보류»(미검증 — 판정
    #   근거가 없어서 아직 못 정했다)인지 **칸 하나로는 구분이 안 된다.** 두 뜻을 같은 NULL로
    #   두면 재개방 로직이 「영영 안 볼 것」과 「나중에 볼 것」을 못 가른다. 그래서 만료일과
    #   «왜 그 만료일인가»를 나눠 적는다 — 제외를 사형에서 **임대**로 바꾸는 것이 S2다.
    # 값: 무관(영구 NULL) / 광의(+90일) / 성과미달(+min(30×cycle,90)일) / 미검증(보류 NULL)
    #     / 오컷의심(즉시 도래 — 재심사 «대상 목록»에만 오르고 실행은 소유권 분리 후)
    # ★nullable인 이유(계약 §3 금지선): 신설 컬럼은 additive nullable만 — 구코드가 새 스키마
    #   위에서 그대로 돈다. 강제는 DB가 아니라 **코드 입구**(exclusion_grade.new_exclusion)와
    #   테스트가 진다. 백필 후에도 NULL이 남으면 그것이 곧 «분류 못 한 행»의 표면이다.
    grade: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)
    # 그 등급이 «어느 근거로» 붙었는가 — 백필 버킷 코드·생성 경로·규칙 이탈 사유.
    # ★계약 §4-C S2-a가 "수치가 [E]와 다르면 다른 이유가 함께 출력·기록돼 있다"를 요구한다.
    #   그 «기록»의 자리가 여기다 — 분포표만 있고 이유가 없으면 다음 세션이 숫자를 못 믿는다.
    grade_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


# ══════════════════════════════════════════════════════════════════
# 예측·전문가 스프린트 F1 — forecast_engine (D-NAO-24, docs/PLAN_naver-ad-forecast-expert.md §3)
# ══════════════════════════════════════════════════════════════════
class NaverForecastModel(Base):
    """예측 모델 원장 — grain별(계정/캠페인/그룹/키워드, F1은 campaign만) 게이트·성능 상태.

    gate_status: active(모델 가동)/fallback(데이터 부족, 예측 생략)/demoted(scorer가 최근
    성적 불량으로 강등, demoted_until까지 쿨다운 — forecast_gate.evaluate()가 쿨다운 중엔
    재평가로 즉시 덮어쓰지 않음). params_json = 모델 계수 스냅샷(최근 추세 지수감쇠 레벨,
    재현·감사용 — 모델 자체는 매일 재생성이라 다음날엔 값이 갱신됨. 요일 계절성은 백테스트
    실증으로 v1에서 제외, forecast_model_builder.py 모듈 docstring 참조).
    """

    __tablename__ = "naver_forecast_model"
    __table_args__ = (
        UniqueConstraint("grain", "scope_key", name="uq_naver_forecast_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)  # account/campaign/adgroup/keyword
    scope_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False, default="fallback")
    sample_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 게이트 판정에 쓰인 최근 활동일수
    recent_mape: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)  # 최근 스코어링 롤업(scorer 기록)
    demoted_until: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 강등 쿨다운 만료일
    params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NaverForecastDaily(Base):
    """일별 예측치 + 익일 백필된 실측·오차 (forecast_scorer의 원료이자 산출물).

    grain별(scope_key) target_date 하루 앞선 예측을 forecast_model_builder가 기록하고,
    다음날 forecast_scorer가 actual_*/mape_* 컬럼을 백필한다(생성 시엔 NULL). conv_amt는
    D-NAO-21 보정계수 미적용 원값(네이버 직+간접 합산) — 진단 화면의 보정 로직과는 별개
    파이프라인이라 이 테이블에서 보정을 적용하지 않는다(정직 경계, 추정 금지).
    """

    __tablename__ = "naver_forecast_daily"
    __table_args__ = (
        UniqueConstraint("target_date", "grain", "scope_key", name="uq_naver_forecast_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    pred_clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pred_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pred_cpc: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    pred_conv_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_clk: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_conv_amt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mape_clk: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    mape_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    mape_conv_amt: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverPooledEstimateDaily(Base):
    """[9] 계층 EB 풀링 산출 — CTR/CVR/RPC 축소추정치 (D-NAO-214 M2-a · ref 65 S1-ⓑ).

    `hierarchical_pooling.pool_all`이 매일 크론 1회전에서 산출한 값을 남긴다. 기존
    `naver_forecast_daily`를 **확장하지 않고 신설**했다 — 계약 §8-Q3의 기본값은 「확장 우선」이었으나
    착수 실측(2026-08-21)이 실격 사유를 냈다: `forecast_scorer.backfill`(forecast_scorer.py:55-57)은
    `target_date`가 맞고 `actual_clk IS NULL`인 행을 **grain을 가리지 않고 전부** 집어 백필한다.
    풀링 추정치를 그 테이블에 얹으면 스코어러가 그것을 예측으로 오인해 `pred_clk=0` 대비 MAPE를
    계산하고, 그 값이 `NaverForecastModel.recent_mape` → `gate_status` 강등으로 굴러간다 —
    **예측이 아니었던 행 때문에 진짜 예측 모델이 강등**된다. 계약 §2-2가 「신설보다 확장을 먼저
    실측한다」고 못박은 이유가 이것이다(취향이 아니라 실격).

    ★**이 테이블은 판정을 하지 않는다 — 추정치를 남길 뿐이다.** 소비는 M2-d(성적표 축) 이후이고,
    이 슬라이스에서 자동 쓰기 경로에 연결되지 않는다(계약 §3 「신규 자동 쓰기 0건」).

    ★**수기 검산이 가능하도록 원료를 함께 남긴다**(계약 §4 S1-② 합격기준: *"표본 키워드 1개에서
    «raw vs shrunk» 값이 공식 `(n·raw+K·prior)/(n+K)`과 수기 일치"*). 그래서 결과값만이 아니라
    분모 n(지표마다 다르다 — CTR은 imp, CVR·RPC는 clk)·raw·prior·K를 전부 컬럼으로 둔다.
    이 넷이 없으면 합격기준이 원리적으로 관측 불가가 된다.

    grain/scope_key 관례는 `naver_forecast_daily`와 같다(account/campaign/adgroup/keyword).
    conv_amt는 D-NAO-21 보정계수 **미적용** 원값(직접+간접) — `pooled_rpc`의 기존 관례와 같다.
    """

    __tablename__ = "naver_pooled_estimate_daily"
    __table_args__ = (
        UniqueConstraint("target_date", "grain", "scope_key", name="uq_naver_pooled_estimate_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)  # keyword/adgroup
    scope_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 집계 창 — 어떤 창의 실적으로 수축했는지가 값의 의미를 정한다(창 없는 숫자는 해석 불가).
    window_from: Mapped[datetime] = mapped_column(Date, nullable=False)
    window_to: Mapped[datetime] = mapped_column(Date, nullable=False)
    # 분모·분자 원값(수기 검산용) — conv_cnt/conv_amt는 직접+간접 합산.
    n_imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_conv_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_conv_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 수축 전 관측값(분모 0이면 0 — 가중치도 0이라 결과에 영향 없음)
    raw_ctr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    raw_cvr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    raw_rpc: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    # 이 행이 물려받은 상위 prior(= 그룹 레벨 pooled 값) — 검산 공식의 prior 항
    prior_ctr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    prior_cvr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    prior_rpc: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    # pool_all 산출(수축 후)
    pooled_ctr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    pooled_cvr: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    pooled_rpc: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    shrink_k: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ══════════════════════════════════════════════════════════════════
# 예측·전문가 스프린트 E1a — expert_desk 조언자 모드 (D-NAO-30/31/32,
# docs/PLAN_naver-ad-forecast-expert.md §8)
# ══════════════════════════════════════════════════════════════════
NAVER_EXPERT_VERDICTS = ("agree", "partial", "reject", "insufficient_evidence", "commentary")
NAVER_EXPERT_OUTCOMES = ("correct", "wrong", "unverifiable", "pending")
NAVER_EXPERT_SOURCES = ("local", "ava")
NAVER_EXPERT_RUN_STATUSES = ("ok", "degraded", "skipped", "failed")


class NaverExpertReviewRun(Base):
    """전문가(Ava) 배치 검토 1콜 = 1행(run 원장). 매일 08:05 pending 제안 전체를 묶어 배치
    검토(D-NAO-30) — verdict를 run_id로 이 원장에 묶어 provenance·프롬프트버전·재실행 이력을
    보존한다(codex 아웃사이드 보이스 반영). 재실행은 같은 as_of라도 새 run(덮어쓰기 아님).
    """

    __tablename__ = "naver_expert_review_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(30), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    briefing_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # claude 원응답(감사용)
    usage_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  # NAVER_EXPERT_RUN_STATUSES
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverExpertReview(Base):
    """전문가 평결 1건(run의 child). proposal_id NULL = 하루 총평(run당 1행이라 여러 run에
    걸쳐 NULL이 중복돼도 문제없음). checkable_prediction은 기각(reject) 평결에도 붙일 수
    있는 선택 필드(억지 예측 방지, codex 반영). C3 자문 경계: 이 테이블은 전문가의 의견을
    기록할 뿐 NaverProposal.status나 실행 상태를 절대 건드리지 않는다(D-3 관찰모드).
    이 경계는 지금도 유효하다 — 유일하게 승인된 소비 경로는 delegation_gate(X1a T5,
    D-NAO-25 — Jino가 유형 단위로 명시 위임한 경우만, naver_account_settings.expert_delegated_types).
    """

    __tablename__ = "naver_expert_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("naver_expert_review_run.id"), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("naver_proposals.id"), nullable=True)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)  # NAVER_EXPERT_VERDICTS (max: insufficient_evidence=21자)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4), nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checkable_prediction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pred_target_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pred_target_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    pred_metric: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    pred_direction: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    verify_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # NAVER_EXPERT_OUTCOMES
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="local")  # NAVER_EXPERT_SOURCES


# ══════════════════════════════════════════════════════════════════
# X1a T5 — E2 위임 스위치 (D-NAO-25 부분 게이트, docs/PLAN_naver-ad-execution-loop.md)
# ══════════════════════════════════════════════════════════════════
class NaverAccountSettings(Base):
    """계정 단위 설정 KV(X1a T5). key 예: 'expert_delegated_types'(E2 위임 스위치, D-NAO-25 —
    Jino가 유형 단위로 명시 위임한 proposal_type 집합을 JSON 배열로 저장. 기본 ∅ — 이 설정이
    없거나 파싱 실패하면 delegation_gate는 fail-closed로 빈 set 취급한다). 콘솔 PUT
    /api/naver/ad/settings/expert-delegation으로만 행사(사람 명시 조작, Jino만).
    """

    __tablename__ = "naver_account_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ══════════════════════════════════════════════════════════════════
# D-NAO-103 — 소재 CTR 경보 이력(발화 억제 상태) (VT3 개편)
# ══════════════════════════════════════════════════════════════════
class NaverCtrAlertLog(Base):
    """소재 CTR 경보의 **판정 이력** 1행 — "어제도 경보였나"를 알기 위한 유일한 상태(D-NAO-103).

    왜 필요한가: 개편 전 VT3는 만성 저CTR 그룹을 매일 재발화해(07-23~28 파워링크 5~8그룹/일)
    Jino가 읽기를 포기했다. "신규 진입만 개별 발화 · 만성은 주 1회 요약"으로 바꾸려면 전일
    **판정 집합**이 필요한데, ops_diary_entries의 브리핑 본문은 (a)억제된 건이 애초에 안 적히고
    (b)사람이 읽는 이름 표기로 바뀌어 ID 역파싱이 불가능하다 → 별도 이력 테이블.

    ★기록 대상은 **발화한 것이 아니라 판정된 것 전부**다. 억제된 만성 건을 안 남기면 다음날
    "전일 집합에 없음 = 신규"로 되살아나 억제가 매일 무효화된다(설계상 가장 쉬운 함정).

    grain: (as_of_date, campaign_id, adgroup_id). as_of_date = detect_ctr_alerts의 D0(=today−1),
    브리핑 실행일이 아니다(레인이 하루 걸러 돌아도 창 의미가 유지된다).
    streak_days = 연속 판정 일수(전일 행이 있으면 +1, 없으면 1) — 브리핑의 "N일째" 근거.
    notified = 그날 개별/주간 메시지에 실제로 실렸는지(관측용, 억제율 사후 확인).
    """

    __tablename__ = "naver_ctr_alert_log"
    __table_args__ = (
        UniqueConstraint("as_of_date", "campaign_id", "adgroup_id", name="uq_naver_ctr_alert_log"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)  # D0(=today−1, KST)
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    window: Mapped[str] = mapped_column(String(8), nullable=False, default="")  # W1/W3/W1+W3
    imp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_rank: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)
    expected_clk: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ⚠️UTC — server_default=func.now()는 UTC([[sqlite-server-default-now-is-utc]]). 날짜 판단은
    # 전부 as_of_date(KST 파생)로 한다 — created_at은 감사용 타임스탬프일 뿐 계산에 쓰지 않는다.
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ══════════════════════════════════════════════════════════════════
# D-NAO-54 P1 — 운영 일기(기록층) (docs/PLAN_naver-ad-diary-wisdom.md §P1)
# ══════════════════════════════════════════════════════════════════
class OpsDiaryEntry(Base):
    """운영 일기 1건 — "무엇을 했나(집행/차단/거부/킬스위치)"를 그 순간의 환경 스냅샷과 함께
    남기는 기록층(D-NAO-54 P1). Jino 문제의식: "한 일만 적지 말고 환경조건(휴일·계절·폰
    출시기간·요일)에 맞춰 결과를 학습". P2 해석층이 outcome_json에 D+1/D+7 결과를 소급 기입하고,
    P3가 3회 반복/TTL로 지혜 승격 후보를 뽑는다.

    기록 원리(이중 기록 금지): 실집행/가드레일 차단/킬스위치는 naver_execution_harness가,
    레인 고유 이벤트(일·시간당 레인의 hold=blocked, rejected_stale=reject)는 auto_operator가
    남긴다. 일기 쓰기 실패는 집행을 막지 않는다(diary.write_diary_entry fail-open — 독립 세션).

    event_type: execute|blocked|reject|kill_switch|observe.
    actor: daily|hourly|console|delegation|system (diary.actor_from_approval_source 파생).
    source_ref: 이 일기가 가리키는 naver_change_log.id(execute/blocked 시 연결, 그 외 None).
    """

    __tablename__ = "ops_diary_entries"
    __table_args__ = (
        Index("ix_ops_diary_entries_campaign_created", "campaign_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ⚠️UTC — server_default=func.now()는 UTC로 찍힌다([[sqlite-server-default-now-is-utc]] 교훈,
    # NaverChangeLog.changed_at과 동일 관례). 환경 스냅샷의 weekday/season은 KST now 기준이라
    # created_at(UTC)과 의미가 다른 별개 필드다(혼동 금지).
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # execute/blocked/reject/kill_switch/observe
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    adgroup_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # campaign/adgroup/keyword/search_term
    target_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor: Mapped[str] = mapped_column(String(12), nullable=False, default="system")  # daily/hourly/console/delegation/system
    action: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # update_bid/set_user_lock/bid_up 등
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # ── 환경 스냅샷(env_snapshot_sa) — 어떤 필드든 조회 실패 시 None ──
    weekday: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0=월 … 6=일 (KST now 기준)
    is_kr_holiday: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    season: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)  # spring/summer/autumn/winter
    iphone_launch_offset_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # +출시 후 경과/-출시 전
    spend_pacing_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 캠페인 당일 소진율(%)
    avg_rank: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # keyword 대상 최신 avg_rank
    # ── P2가 소급 기입(지금은 항상 None) ──
    outcome_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_ref: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # naver_change_log.id


class OpsWisdomCandidate(Base):
    """지혜 승격 후보 1건 — 결과 기입된 diary 행에서 뽑은 (캠페인×액션×환경버킷) 반복 조건 패턴
    (D-NAO-54 P3). 시그니처는 조건(campaign|action|day_class|season|iphone_window)만이고 결과
    방향은 시그니처에 넣지 않는다 — 같은 조건의 good/bad를 good_count/bad_count로 함께 세어
    '이 조건에서 이 액션의 성적'을 한 후보에 모은다(리뷰 P2-2). candidate_sa가 같은 시그니처
    재등장마다 방향에 따라 good_count/bad_count를 올리고, occurrences = good_count+bad_count로
    정의한다(중복 diary id는 미가산). TTL 14일 or occurrences≥3이면 독립 LLM 판사(judge_sa,
    자기평가 금지)가 승률·표본까지 보고 promote/reject한다. 미승격(pending) 후보는 Ebbinghaus
    망각(retention_sa가 soft-hide=status hidden), 승격분·지혜는 불망각(D-NAO-54 결정 4축).

    ★UTC 혼동 금지: created_at은 server_default(UTC)지만 first_seen_at/last_seen_at은 SA가
    KST now로 명시 기입한다 — 감쇠 Δt(경과일) 계산과 TTL 판정의 진실 소스라 두 시간계를 섞으면
    안 된다([[sqlite-server-default-now-is-utc]]).

    status: pending|promoted|rejected|hidden. 시그니처는 signature(unique)로 멱등 재수확한다 —
    promoted는 재수확 대상이 아니지만(판사가 이미 판정), hidden은 시그니처가 재등장하면
    pending으로 부활한다(Ebbinghaus 재노출 강화 — 연 1회 iphone_window 패턴이 해를 넘겨 누적됨).
    ★rejected도 2026-08-26(D-NAO-251)부터 재수확 «대상»이다 — 아래 재개방 4컬럼 주석 참조.

    ★D-NAO-248(2026-08-25, 부록 Q2 처분 (b′)) — 옛 시그니처(campaign_id 선두)는 표본을
    캠페인 수만큼 쪼갰다(§1: 4캠페인 합 91회가 45/38/5/3으로 갈려 전부 rejected). 이후
    harvest_candidates는 **전역 시그니처**(campaign_id 미포함, 접두사로 구분 — "g|"=전역/
    실험분리, "g?|"=fail-closed 미상분리)를 쓴다. grain/campaign_type/experiment_batch/
    by_campaign_json 4컬럼이 이 신형을 표시한다 — **기존 27행은 grain=NULL로 그대로 남는다**
    (소급 재계산 아님, 소급 «재수확»: 같은 90일 일기 위에 새 grain의 새 행만 생긴다).
    """

    __tablename__ = "ops_wisdom_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ⚠️UTC(server_default) — 감쇠·TTL은 first_seen_at/last_seen_at(KST 명시)로 판단(created_at 아님).
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    signature: Mapped[str] = mapped_column(String(200), nullable=False)  # 캠페인×액션×환경버킷 키
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    action: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    env_bucket_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 버킷 상세(day_class/season/…)
    observation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 규칙 기반 요약문(LLM 아님)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # = good_count+bad_count
    good_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 이 조건에서 결과 good 관찰 수
    bad_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 이 조건에서 결과 bad 관찰 수
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # KST 명시(TTL 기준)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # KST 명시(감쇠 Δt 기준)
    source_entry_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 기여 diary id 목록(중복 제외)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending/promoted/rejected/hidden
    judge_verdict_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 판사 응답(verdict/principle/rationale)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=5)  # Ebbinghaus 가중(0~10)
    strength: Mapped[float] = mapped_column(Float, nullable=False, default=7.0)  # Ebbinghaus 시상수(일수)
    # D-NAO-248 §1(끊김 1 수리) — 이 4컬럼이 있어야 grain='global' 신형 시그니처가 캠페인
    # 단위가 아니라 (유형×액션×환경[×실험배치]) 단위로 표본을 합칠 수 있다. 전부 nullable —
    # 기존 27행은 전부 NULL(=레거시 캠페인 grain, 손대지 않는다).
    # grain은 «이 후보가 실제로 무엇을 묶었나»다 — 시그니처와 반드시 일치한다.
    #   NULL='레거시'(D-NAO-248 이전, 캠페인 단위) / 'global'=전역 풀·실험배치 분리(`g|`) /
    #   'campaign'=fail-closed 미상분리(`g?|`, 캠페인 1개 단위. settings 행 부재나 유형 미상).
    #   ★'global'을 미상분리에도 붙이면 라벨과 실체가 모순이고 소비층이 grain으로 거를 때
    #   미상분리분이 전역 통계에 섞인다. 값은 String(12)에 맞춰 짧게 유지할 것.
    grain: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    campaign_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 경계 축 ⓐ(부록 Q3)
    experiment_batch: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)  # 경계 축 ⓑ — 있으면 전역 풀과 분리
    by_campaign_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 캠페인별 good/bad 분해(판사에게 이질성 병기)
    # ★D-NAO-251 §4-①(증거보전 — 재개방) — «판정이 증거 수집을 영구히 끝내던» 함정의 수리.
    #   구판은 promoted/rejected를 똑같이 terminal로 보고 tally 갱신까지 막았다. 그래서 판사가
    #   "45회 관찰이 단 이틀 안에 집중되어… 승격을 보류합니다"로 기각하면 그 시그니처는 **영원히
    #   표본이 부족한 채로** 남았다(실측: 그 뒤 일주일에 818건이 더 쌓였는데 다시 안 봤다).
    #   ⇒ rejected는 tally가 계속 흐르고, occurrences가 판정 시점의 2배(∧ +5 이상)에 닿으면
    #   pending으로 복귀해 **같은 판사**가 재심한다(판정기를 늘리지 않는다 — 북극성 §6-b M5).
    #   promoted는 여전히 완전 terminal — 승격↔기각 플립플롭이 브리핑 주입을 흔들기 때문이다.
    #   전부 nullable/default — 기존 48행은 마이그레이션이 judged_occurrences만 현재값으로 백필한다.
    judged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # KST 명시(마지막 판정 시각)
    judged_occurrences: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 판정 시점 occurrences(재개방 기준선)
    rejudge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 재심 횟수(상한 _MAX_REJUDGE)
    # 이전 판정문 이력(append-only JSON 배열). judge_verdict_json을 덮어쓰지 않고 여기 쌓는다 —
    # 그 컬럼의 «형태»에 wisdom_writer.py:51·wisdom_apply.py:72가 의존하므로 모양을 바꾸면
    # 소비층이 깨진다(계약 §3 「기존 판정문 삭제·덮어쓰기 금지」의 구현형).
    prior_judgments_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ux_ops_wisdom_candidates_signature", "signature", unique=True),
        Index("ix_ops_wisdom_candidates_status", "status"),
    )


class OpsWisdomEntry(Base):
    """승격된 지혜 1건 — 재사용 가능한 판단원칙(D-NAO-54 P3). writer_sa가 promoted 후보에서
    1:1(source_candidate_id unique)로 생성하고, Jino 보고는 정보성 NaverProposal(wisdom_promoted)로
    별도 낸다(지혜→실행 직접 쓰기 금지 = D-NAO-54 금지선).

    지혜는 감쇠하지 않는다(불망각) — retention_sa는 이 테이블을 건드리지 않는다. status는 후속
    사용자 철회 대비 여지(active|retired)만 둔다(P3에서 능동 retire 경로는 없음).
    """

    __tablename__ = "ops_wisdom_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    wisdom_text: Mapped[str] = mapped_column(Text, nullable=False)  # 판단원칙 한 문장(judge principle)
    source_candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)  # 멱등 키(unique)
    judge_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")  # active/retired
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # KST 명시
    # D-NAO-54 P4(소비층): 이 지혜가 param_suggestion을 담고 있어 param_change 제안을 냈다면
    # 그 NaverProposal.id를 여기 새긴다. wisdom_apply.propose_param_changes의 멱등 키 —
    # 같은 지혜로 param_change 제안을 1회만 생성한다(rationale 텍스트 매칭 대신 전용 추적).
    # None = 아직 제안 미생성(param_suggestion 없거나 아직 안 돎).
    param_proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ux_ops_wisdom_entries_source_candidate", "source_candidate_id", unique=True),
    )


class NaverKeywordVolumeDaily(Base):
    """키워드 월검색량의 **시계열** — D-NAO-186 ①(2026-08-17 19:32 Jino 승인)의 실체.

    ★왜 새 테이블인가(기존 `NaverEntity.monthly_volume`이 있는데도): 그 컬럼은 **덮어쓰기**라
      과거가 남지 않는다. D-NAO-186이 이 적재를 「소급 불가·마감 있음」으로 승인한 이유가
      정확히 «기준선(시계열)»인데, 덮어쓰기 컬럼은 기준선이 될 수 없다 — 오늘 값이 지난주
      값을 지운다. **켠 날이 관측 창의 시작**이므로 안 켠 날은 협상 불가로 사라진다.

    ★왜 대상을 넓히는가: 기존 `keyword_volume_sync`는 «저클릭 키워드»(30일 클릭<10)만 본다.
      그래서 **비용이 실제로 나가는 키워드가 구조적으로 대상 밖**이다 — 2026-08-18 prod 실측:
      최근 30일 클릭이 있는 키워드 **1,193개 · 비용 4,070,471원**의 검색량을 한 번도 받은 적이
      없다. 아이폰 출시(매년 9월) 때 «수요가 움직였나 우리가 움직였나»를 가르려면 필요한 것이
      바로 그 머리 키워드다(작년 아이폰 17 때 실제로 못 갈랐다 — D-NAO-186 마감 근거).
      콜 예산: 1,193개 ÷ 5개/콜 = 약 239콜/일(승인분 2,200콜 안).

    grain = (measured_date, keyword). ★`keyword`는 **텍스트**다 — `keyword_id`가 아니다.
      keywordstool은 문자열로 답하고, 같은 문자열이 여러 광고그룹에 걸리며, 검색량은 계정과
      무관한 **시장 수치**라 엔티티에 매달 값이 아니다.

    ⚠️`monthly_volume`은 «월» 검색량이라 일 단위로 크게 안 움직인다. 그래도 일 적재하는 이유는
      네이버가 이 값을 언제 어떻게 갱신하는지 `[미상]`이고, 출시 전후 급변을 놓치면 소급이
      불가능하기 때문이다 — 값이 안 변하면 행이 같을 뿐 손해가 없다.
    """

    __tablename__ = "naver_keyword_volume_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ★이 파일의 관례를 따른다 — 날짜 컬럼은 `Mapped[datetime]` + `Date`(models.py:146 등).
    measured_date: Mapped[datetime] = mapped_column(Date, nullable=False)  # KST 조회일
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)  # 검색어 텍스트(정규화 전 원문)
    # ★PC/모바일을 합치지 않고 나눠 둔다 — 기존 fetch_keyword_volumes는 합계만 돌려주는데,
    #   출시 효과는 기기별로 다르게 나타날 수 있고 합치면 되돌릴 수 없다.
    pc_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mobile_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competition: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # compIdx(높음/중간/낮음)
    # ★하한 sentinel 표기: keywordstool은 저검색량을 '< 10' 문자열로 준다. 그걸 5로 접은 행은
    #   여기 True — 「측정값 5」와 「10 미만이라는 것만 안다」를 구분해야 추세가 거짓말을 안 한다.
    is_below_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("measured_date", "keyword", name="uq_naver_keyword_volume_daily"),
        Index("ix_naver_keyword_volume_daily_kw", "keyword", "measured_date"),
    )


# ──────────────────────────────────────────────
# 네이버 커머스 상품 메타 (C10 — D-NAO-212 · 북극성 M1 ④)
# ──────────────────────────────────────────────
class NaverProductMetaCurrent(Base):
    """네이버 커머스 상품(채널상품)의 **현재 단면** 1행 — `POST /v1/products/search` 적재.

    grain: (channel_product_no). upsert — 이 표에 역사는 없다(역사는 `NaverProductMetaChange`).

    ★**소급이 원리적으로 불가능하다.** 상품 도메인 64 endpoint 전체에 변경-피드·변경 타임스탬프가
    없다(75건 전건 개봉 실측, 2026-08-19 — 층화 가용 0건). 즉 가격·재고·판매상태·카테고리의
    «변경 이력»은 어디에도 저장돼 있지 않고 API가 과거를 주지 않는다. ⇒ **폴링 개통일 = 관측 창의
    시작일**이다(검색량 기준선 D-NAO-186과 같은 성질). 이 표를 과거 성과에 붙일 땐 그 한계를 같이 적는다.

    ★`channel_product_no`는 **문자열**이다 — 조인 상대편인 `NaverAdgroupProduct.mall_product_id`와
    `NaverProductBep.channel_product_id`가 둘 다 `String(50)`이기 때문이다. SQLite는 affinity가
    덮어 주지만 이 저장소의 DB 이행 목표는 PostgreSQL이고 거기선 VARCHAR 컬럼에 int 바인딩이
    에러다(같은 판단의 근거 전문은 `NaverAdgroupMediaBlack` docstring).
    ⚠️단 `channelProductNo ≡ mall_product_id` **동일성 자체는 [미상]**이다 — 이 계약은 확정하지
    않고 조인율 %만 실측한다(교집합은 인과가 아니다).

    ★**필드 절삭 0** — 2026-08-21 00:0x 실응답 실측에서 `channelProducts[]` 하위 키가 **29종**으로
    확인됐고(기존 클라이언트는 그중 10종만 매핑해 19종을 버리고 있었다) 전부 컬럼으로 받는다.
    그래도 `raw_json`을 함께 보존한다 — 응답 스키마가 **항목마다 다르기 때문**이다(실측 8건 중
    3건은 26키·5건은 29키. 리뷰 포인트류 3종이 있는 항목과 없는 항목이 섞인다). 키 부재와 null을
    이 표는 구분하지 못하므로, 그 구분이 필요해지면 `raw_json`이 정본이다(교훈 #315 — 내 절삭이
    원격 전제를 깬 실사고).

    ★**누적판매·리뷰 «수»는 이 표면에 없다**(29키 전수 스캔, 2026-08-21). 계약 §0-4가 [약함]으로
    달아 둔 기대(`accumulateSaleCount`·`reviewCount`·`averageReviewScore`)는 실측에서 **매치 0**이었다.
    실린 `text_review_point`·`photo_video_review_point`·`regular_customer_point`는 리뷰 «수»가
    아니라 **판매자가 설정한 적립 포인트 액수**(정책 설정값)다 — 시계열 신호로 읽으면 오독이다.
    ⇒ 이 축이 주는 것은 「현재 단면」이고, 시계열은 폴링을 시작한 날부터만 쌓인다.

    ★`reg_date`·`modified_date`는 **문자열 원문 그대로** 둔다 — 파싱하면 타임존 가정이 섞이는데
    `modifiedDate`가 어떤 변경에 전진하는지 자체가 [미상]이라, 지금 단계에서 필요한 것은
    «값이 바뀌었는가»(문자열 비교)이지 «언제인가»가 아니다.
    """

    __tablename__ = "naver_product_meta_current"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_product_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    origin_product_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    group_product_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    display_status_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    channel_service_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    sale_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discounted_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mobile_discounted_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stock_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    category_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    whole_category_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    whole_category_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    manufacturer_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    delivery_fee: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    return_fee: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exchange_fee: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivery_attribute_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # 리뷰 «수»가 아니라 적립 포인트 액수다(위 docstring) — 이름에 point를 남겨 오독을 막는다.
    text_review_point: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    photo_video_review_point: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    regular_customer_point: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    manager_purchase_point: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    knowledge_shopping_registration: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    seller_tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 원문 배열 JSON
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # representativeImage.url

    reg_date: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 원문 문자열(파싱 안 함)
    modified_date: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # 동상

    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 응답 원문(키 부재/null 구분의 정본)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # ★`last_seen_at`이 정체한 상품 = 이번 응답에 없던 상품. 행을 지우지 않는다 —
    #   `productStatusTypes` 무필터 호출의 포함 범위(DELETE 포함 여부)가 [미상]이라(계약 §8 ⑤),
    #   「사라졌다」와 「이번엔 안 보였다」를 아직 못 가른다.
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("channel_product_no", name="uq_naver_product_meta_current"),
    )


class NaverProductMetaChange(Base):
    """상품 메타의 **변경분만** append (C10 — D-NAO-212).

    grain: (channel_product_no, observed_at). 변경이 있을 때만 행이 생긴다.

    ★**`observed_at`은 «폴링 시각»이지 «변경 시각»이 아니다.** 폴링 grain이 일 1회라 실제 변경
    시각은 ±1일 불확실하다. 이걸 숨기면 나중에 시간축 분석이 「가격 변경이 밴드 이동에
    선행한다」류를 오독한다 — 컬럼 주석으로 남기는 이유다.

    ★첫 회차는 전건 신규라 이 표가 **0행인 것이 정상**이다(신규 insert는 변경이 아니다).

    ★왜 전건 일일 스냅샷이 아닌가: prod 디스크가 93%(여유 7.5G, 2026-08-21 00:00 실측)이고,
    D-NAO-198이 같은 모양을 실측으로 기각했다(결합 전건 586MB 중 98.2%가 퇴화 행).
    """

    __tablename__ = "naver_product_meta_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_product_no: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)  # 폴링 시각(변경 시각 아님)
    # {필드명: [old, new]} — 키 부재는 null로 접힌다(구분이 필요하면 current.raw_json이 정본)
    changed_fields: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_naver_product_meta_change_cpn_at", "channel_product_no", "observed_at"),
    )


# ──────────────────────────────────────────────
# 수입건 원장 (landed cost) — D-CPP-48 / 계약 docs/PLAN_import-cost-ledger.md
#
# ★이 다섯 테이블은 **순수 추가**다. `product_master.cost_price`와 그 소비처 14곳을
#   한 줄도 건드리지 않는다(계약 §3 금지선). 원가 반영은 계약 C 몫이다.
# ──────────────────────────────────────────────
class ImportShipment(Base):
    """수입 1건(선적 1회) — 서류 3종(CI·PL·통관경비서)이 한 건을 이룬다.

    grain: HBL(House B/L) 1건. 8/18 실건 = `SETR2608170216`.

    ★`status`는 draft / confirmed 둘뿐이다. **검산 3종을 통과하지 못하면 confirmed가 될 수
    없다**(계약 §3) — 그 판정은 `services/import_cost/reconciler.py`가 하고 라우터가 강제한다.

    ★`fx_rate`는 **통관경비서의 신고환율**이다(8/18 = 209.88). 과세금액÷INV로 역산하면
    210.50이 나와 0.3% 어긋나는데 원인이 확인 안 됐다(과세가격이 CIF 기준이라 그럴 가능성).
    둘 중 하나를 못 박지 않으면 합격 판정이 결정적이지 않아 신고환율로 고정했다.
    `remittance_fx_rate`는 실송금 환율 자리인데 **이번 범위가 아니다** — 필드만 예약한다.
    """

    __tablename__ = "import_shipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hbl_no: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    declaration_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 신고번호
    declaration_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    eta: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    shipper_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vessel: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)  # 신고환율
    remittance_fx_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )  # 실송금 환율 — 이번 범위 밖(필드만 예약)
    declared_inv_value: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(16, 2), nullable=True
    )  # 경비서의 INV Value (외화) — 검산 ②의 기대값
    customs_value_krw: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(16, 2), nullable=True
    )  # 과세금액(원)

    carton_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gross_weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    cbm: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)

    allocation_basis: Mapped[str] = mapped_column(
        String(10), nullable=False, default="amount"
    )  # amount(기본·D-CPP-48) / weight / volume / quantity
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    cost_lines: Mapped[list[ImportCostLine]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )
    invoice_lines: Mapped[list[ImportInvoiceLine]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )
    packing_lines: Mapped[list[ImportPackingLine]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )
    documents: Mapped[list[ImportDocument]] = relationship(
        back_populates="shipment", cascade="all, delete-orphan"
    )


class ImportCostLine(Base):
    """통관경비서의 비용 한 줄 (8/18 실건 = 12줄).

    ★`is_costing`이 이 테이블의 전부다 — **배부 대상인가**를 가른다.
    부가세 라인은 `is_costing=False`다: 매입세액으로 공제받으므로 원가가 아니라 국가에 대한
    채권이기 때문이다. 단 **값은 버리지 않는다** — 「부가세 제외가 회계 정답」이 법령 조문으로
    확인되지 않았고(법인세법 시행령 §72에 「부가가치세」·「매입세액」·「관세」라는 단어가 없다),
    세무사 확인이 오면 플래그만 뒤집으면 되게 원본을 보존한다.
    """

    __tablename__ = "import_cost_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("import_shipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    supply_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    is_costing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # ★관세는 «배부»가 아니라 «귀속»이다(D-CPP-50) — 품목마다 세율이 다르기 때문이다
    #   (실측: cleaning kits 0% / 유리·필름 5.6%). NULL은 False로 읽는다(기존 행 호환).
    is_duty: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    shipment: Mapped[ImportShipment] = relationship(back_populates="cost_lines")

    __table_args__ = (
        UniqueConstraint("shipment_id", "seq", name="uq_import_cost_line_seq"),
    )


class ImportInvoiceLine(Base):
    """Commercial Invoice의 한 줄 = **배부를 받는 단위**이자 이 원장의 산출물.

    ★`line_type`이 「판매 SKU / 부자재」를 가른다(D-CPP-48 부수 확정).
    8/18 실건의 `cleaning kits` 2,400개는 Jino 확인 결과 **판매 상품이 아니라 우리 제품에
    들어가는 부품**이다. 부자재 라인의 실제 단가는 계약 A′(층1)의 부자재 마스터가 소비할 값이라
    여기서 «보존»한다 — 지금 엑셀에 「알콜솜 2EA 60원」처럼 수기로 박혀 있는 그 자리다.
    분류는 **사람이 확정한다**(계약 §2-4) — 매핑되면 자동 제안까지만.

    ★단가를 **두 값 다** 확정 저장한다(D-CPP-48 ②). `inc_vat = ex_vat × 1.1`은 손익 엔진
    규약(D-NAO-150)과 같은 모양이지 «실제로 낸 부가세»가 아니다 — 실제 세액은
    `ImportCostLine.tax_amount`에 원본 그대로 남는다. 자세한 이유는 allocator 모듈 docstring.
    """

    __tablename__ = "import_invoice_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("import_shipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    order_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price_foreign: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    # 판매 SKU / 부자재 / 미분류. 미분류를 판매 SKU로 접지 않는다(0=미입력 혼동 재생산 금지).
    line_type: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")
    internal_sku: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )  # product_master.internal_sku 문자열 참조 — FK를 걸지 않는다(계약 §3: 원가 층 미접촉)

    # 배부 기준 원료 (PL에서 온다). 없으면 그 기준으로는 배부 불가.
    gross_weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    cbm: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    # ★품목별 관세율(0.0560 = 5.6%). **NULL은 «모름»이지 0%가 아니다** — 하나라도 값이 있으면
    #   관세를 라인별로 귀속하고, 전부 NULL이면 종전대로 공통비에 섞어 배부한다(D-CPP-50).
    duty_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 4), nullable=True)

    # ── 계산 결과 (확정 저장) ──
    goods_amount_krw: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    allocated_cost_krw: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    unit_cost_ex_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    unit_cost_inc_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)

    shipment: Mapped[ImportShipment] = relationship(back_populates="invoice_lines")

    __table_args__ = (
        UniqueConstraint("shipment_id", "seq", name="uq_import_invoice_line_seq"),
    )


class ImportPackingLine(Base):
    """Packing List의 박스 라인 — **대조 전용**이다(배부를 직접 받지 않는다).

    ★CI와 **라인 분해가 다르다**: 8/18 실건에서 CI는 `Glass_Ip16 Pro 350` 한 줄인데 PL은
    `7-9번 박스 300` + `10번 박스 50`으로 나뉜다. 그래서 검산은 라인 대 라인이 아니라
    **품목명으로 묶은 합계**로 한다(reconciler 참조).
    """

    __tablename__ = "import_packing_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("import_shipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    carton_range: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "2-6"
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    qty_per_carton: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    carton_count: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    gross_weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3), nullable=True)
    measure: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "50.5*44*24"
    cbm: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    shipment: Mapped[ImportShipment] = relationship(back_populates="packing_lines")

    __table_args__ = (
        UniqueConstraint("shipment_id", "seq", name="uq_import_packing_line_seq"),
    )


class ImportDocument(Base):
    """원본 서류 보관 — 근거 보존(계약 §3 금지선: 파일 없이 저장 금지).

    ★파일 본문을 **DB에 담는다**. 파일시스템에 두면 배포·백업 경로가 갈라지고, 이 저장소는
    iCloud 위에 있어 파일시스템 메타데이터를 근거로 못 쓴다(원칙 23-A와 같은 이유).
    건당 3파일 · 합계 500KB 수준이라 볼륨이 문제되지 않는다.
    """

    __tablename__ = "import_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("import_shipment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ci / pl / expense / etc
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ★`deferred=True` — 이 컬럼을 **명시적으로 읽을 때만** SELECT한다(적대 리뷰 P2-1).
    #   목록 API가 `document_count`를 세려고 documents를 selectinload 하는데, 그때 파일 본문까지
    #   딸려 올라온다: limit 500 × 상한 20MB면 목록 조회 한 번에 수백 MB가 메모리로 온다.
    #   prod 디스크가 94%인 상황에서 이건 이론적 위험이 아니다. 다운로드 경로는 `doc.content`를
    #   직접 읽으므로 그때 지연 로드된다.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    shipment: Mapped[ImportShipment] = relationship(back_populates="documents")


# ──────────────────────────────────────────────
# 원가 메뉴 · 표준원가 (구성 × 로트 실적) — D-CPP-53 / 계약 docs/PLAN_cost-menu-standard-cost.md
#
# ★이 일곱 테이블은 **순수 추가**다. `product_master.cost_price`와 그 소비처를 한 줄도
#   건드리지 않는다(계약 §3 금지선) — A′는 읽고 대조만 하고, 덮어쓰기는 계약 C 몫이다.
#
# ★S1(이 슬라이스)이 실제로 쓰는 것은 `cost_material` · `cost_material_price` ·
#   `cost_setting` 셋이다. 나머지 넷(레시피·링크·표준원가)은 **스키마만 먼저 선다** —
#   계약 §6이 「테이블 + 마이그」를 S1에 몰아둔 이유는 DB 스키마 변경이 배포 순서를
#   강제하는 유일한 변경이라(`--migrate`), 슬라이스마다 마이그를 내보내면 그 순서 사고의
#   표면이 세 배가 되기 때문이다.
#
# ★미입력·미승인은 «없음»(NULL)이다 — 0으로 채우지 않는다(계약 §2-7). `cost_price`가
#   NOT NULL default 0이라 「0인가 미입력인가」를 못 가리는 것이 기존 스키마의 결함이고,
#   그걸 새 층에서 재생산하면 이 층을 만들 이유가 없다.
# ──────────────────────────────────────────────
class CostMaterial(Base):
    """부자재·구성요소 **1종**. grain = 「물건 한 종류」.

    ★`form_factor`·`part`는 **분류·필터용이지 단가 축이 아니다**(계약 §5-1 ★원단 결정).
    단가가 다른 원단은 «같은 물건의 다른 값»이 아니라 **다른 재단 규격의 다른 물건**이라
    별도 종으로 등록한다(플립 내부 원단 600 / 폴드 내부 원단 1,000 / 트라이폴드 1,800).
    두 필드를 단가 축으로 두면 `cost_material_price`의 grain이 «관측 1건»에서 벗어나
    한 종 아래 서로 다른 물건의 값이 섞인다.

    ★`status`는 `unconfirmed` / `approved` 둘뿐이다. **미승인 종의 단가는 표준원가 계산에
    쓰지 않는다**(계약 §2-2) — 추론을 확인분과 동일시한 것이 08-10 71건 사고다(교훈 #204).

    ★`excel_label`은 **참고 라벨일 뿐 키가 아니다.** cleaning kit는 원가 정본 엑셀에 대응
    항목이 없음이 실측으로 확인됐고(계약 §9-3), 그래도 단가는 원장에서 살기 때문에
    「엑셀 대응 불명」이 계산을 막지 않는다.
    """

    __tablename__ = "cost_material"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ★이름이 곧 매칭이 닿는 자리다(계약 §5-1 ★원단 결정 ③) — 그래서 유일하다.
    #   같은 이름 두 종이 서면 「어느 쪽에 단가가 붙었나」를 화면이 못 말한다.
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # ea / 매 / m 등
    # 부자재 / 원단 / 패키지 / finished_goods(매입 완제품 — 계약 §5-1 ★매입품 결정) 등
    category: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="unconfirmed", server_default="unconfirmed"
    )
    excel_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # ★★**참고값이지 단가가 아니다** (계약 §3 금지선, S2 신설).
    #   원가 정본 엑셀에 적혀 있던 숫자를 «그대로 보여주기 위해» 보관한다. 이 값은
    #   `cost_material_price` 행이 **아니므로** 표준원가 계산에 절대 쓰이지 않는다 —
    #   계산이 읽는 것은 오직 단가 행(`ledger` 파생 또는 Jino가 입력·승인한 `manual`)뿐이다.
    #   Jino가 화면에서 「엑셀 참고값 채택」을 누르면 **그때** 이 값이 `manual` 단가 행으로
    #   복사되고, 그 행의 `note`에 출처가 남는다. 그것이 「Jino가 입력·승인한 값」의 경로다.
    #   ⚠️이 컬럼을 계산 경로에서 읽는 코드가 생기면 그 순간 금지선을 넘는 것이다.
    excel_ref_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # 원장 품목명 매칭 힌트. **제안까지고 확정은 사람이다**(계약 §5-2).
    match_rule: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    form_factor: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    part: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)  # 내부/외부/후면/힌지
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list[CostMaterialPrice]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )


class CostMaterialPrice(Base):
    """단가 **관측 1건**. grain = 「어느 종의 값을, 어느 로트/입력에서 봤나」.

    ★출처는 두 경로뿐이다(계약 §4 채택안): `ledger`(계약 B 원장의 확정 라인에서 파생) ·
    `manual`(Jino가 화면에서 입력·확인한 값). **엑셀에서 단가가 자동 유입되는 경로는 없다**
    (계약 §3 금지선) — 엑셀 하드코딩 계수는 실측으로 반증됐고(평균 +7.3% 과소, ref 92),
    사람이 옮기는 경로가 08-07·08-10 사고 그 자체다.

    ★단가 두 값을 **연결 시점에 확정 저장**한다(계약 B 원칙 계승). 원장 라인의 값을 그대로
    복사한다 — 여기서 다시 산술하지 않는다. 원장이 재계산되면(reopen→confirm) 이 행은
    «그때 본 값»으로 남는다: 근거 보존이 이 테이블의 존재 이유다.

    ★`supplier`가 종이 아니라 **단가 행**에 붙는 이유(계약 §5-1 ★공급처): 같은 부자재를
    여러 곳에서 살 수 있고, 「이 단가가 어디서 산 값인가」를 단가 행이 못 말하면 추적이
    끊긴다. `note` 자유 텍스트로 흡수하지 않는 것은 공급처별 필터·대조가 구조로 가능해야
    하기 때문이다. FK·공급처 마스터는 만들지 않는다(문자열 하나).
    """

    __tablename__ = "cost_material_price"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("cost_material.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # ledger / manual
    # ledger일 때만 채워진다. FK는 걸되 원장 라인이 지워지면 단가 행도 같이 간다 —
    # 근거가 사라진 파생값이 남는 것이 stale 증거의 정의다.
    # ⚠**FK 선언만으로는 안 지켜진다**(적대 리뷰 1R P1-1 실증): 이 앱의 SQLite 연결에
    #   `PRAGMA foreign_keys=ON`이 없어 CASCADE가 강제되지 않고 단가 행이 **고아로 남는다.**
    #   전역 PRAGMA는 저장소 전체에 영향을 주므로 이번 슬라이스에서 켜지 않고(계약 §9-10 이월),
    #   대신 조회 시점 재검사가 고아를 「원장 라인 없음」으로 **표면화**한다.
    import_invoice_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("import_invoice_line.id", ondelete="CASCADE"), nullable=True, index=True
    )
    supplier: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # ★**저장 시점의 원장 신원 스냅샷** (적대 리뷰 1R P1-2). FK를 걸지 않는다 — 관계가 아니라
    #   «그때 무엇을 보고 복사했나»의 증거이고, 원장이 바뀌어도 따라 바뀌면 안 된다.
    #   `import_invoice_line_id`만으로는 부족하다: 계약 B `_replace_lines`가 라인을 지우고
    #   다시 넣으면 **SQLite rowid가 재사용**돼 같은 id가 다른 품목을 가리킨다(실증됨).
    #   재검사(`services/cost_menu/ledger_check.py`)가 대조하는 «왼쪽»이 이 두 칸이다.
    linked_item_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    linked_shipment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # ★nullable이다 — 「단가를 아직 모른다」와 「0원이다」는 다른 사실이다(계약 §2-7).
    unit_price_ex_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    unit_price_inc_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # 통관일/입력일
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    material: Mapped[CostMaterial] = relationship(back_populates="prices")
    # ★로트 좌표(HBL·통관일)가 화면에서 한 번에 보이게 하는 조인. `back_populates`를 걸지
    #   않는다 — `ImportInvoiceLine`(계약 B 소유)에 필드를 더하는 것은 «순수 추가»가 아니고,
    #   이 방향의 읽기만 있으면 충분하기 때문이다.
    invoice_line: Mapped[Optional[ImportInvoiceLine]] = relationship("ImportInvoiceLine")

    __table_args__ = (
        # ★한 원장 라인이 한 종에 두 번 붙지 않는다. SQL에서 NULL은 서로 같지 않으므로
        #   `manual` 행(라인 id가 NULL)은 이 제약에 걸리지 않는다 — 수동 입력은 여러 번 가능하다.
        UniqueConstraint(
            "material_id", "import_invoice_line_id", name="uq_cost_material_price_ledger_line"
        ),
    )


class CostTableItem(Base):
    """업로드된 **원가 정본(엑셀) 항목 1건** — 「섹션 × 품목」 (계약 §0-E-3, D-CPP-59).

    ★**왜 저장하나**: 개정 4 전까지 원가표 초안은 업로드 시점에 파싱해 매칭에만 쓰고
    **버려졌다**(`recipes.import_drafts`). 그래서 매칭이 실패한 레시피에 대해 화면이
    「후보 N건 — 사람이 고른다」고 말하면서도 **고를 목록이 시스템에 없었다.** 이 테이블이
    그 목록이다.

    ★**자연 키(section·item_name·form_factor)에 유니크를 걸지 않는다** — 계약 §9-9①의
    폴드 중복 정의(행 42 6,089.6 vs 행 105 4,604.6, **같은 이름**)가 원가표에 실재하기
    때문이다. 유니크를 걸면 파서가 «진짜 사실»을 못 싣고 한쪽을 조용히 버린다. 중복은
    숨기는 것이 아니라 픽 목록에 나란히 세워 사람이 처분한다.

    ★`total_inc_vat`은 **참고·대조값**이다(엑셀 「제품원가(+VAT)」). 계산이 읽는 것은
    `cost_material_price`뿐이므로 이 값이 표준원가에 유입될 경로는 없다(계약 §3).
    """

    __tablename__ = "cost_table_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    #: 조립형은 값이 있고 **수입 완제품·매입품은 NULL**이다 — 0이나 자리표시자로 채우지 않는다.
    form_factor: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, index=True)
    recipe_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="assembly", server_default="assembly"
    )
    total_inc_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    #: 엑셀 행 번호 — 동명 중복(§9-9①)을 사람이 원본과 대조할 수 있게 하는 유일한 좌표다.
    row_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anomalies: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lines: Mapped[list[CostTableItemLine]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def natural_key(self) -> str:
        """`section\\x1Fitem_name\\x1Fform_factor` — pin 스냅샷이 저장하는 문자열.

        구분자가 `\\x1F`(unit separator)인 이유: 품목명·섹션명에 공백·쉼표·슬래시가 흔해서
        흔한 구분자를 쓰면 키가 조용히 어긋난다.
        """

        return "\x1f".join([self.section, self.item_name, self.form_factor or ""])


class CostTableItemLine(Base):
    """원가표 항목의 구성 한 줄 — 「무엇이 몇 개」 (계약 §0-E-3).

    ★**JSON으로 접지 않는 이유**: 라인 단위 필터·대조가 구조로 불가해지기 때문이다
    (§5-1이 공급처를 `note`에 안 접은 것과 같은 논리).

    ★`ref_price`는 **참고값**이다 — `RecipeLineDraft.excel_ref_price`와 같은 지위이고,
    픽이 만든 `cost_recipe_line`에 단가로 유입되지 않는다(계약 §0-E-7 금지선).
    """

    __tablename__ = "cost_table_item_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("cost_table_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 부자재 종의 **표시 이름 원문**(`MaterialKey.display_name`) — 이 이름으로 `cost_material`에 잇는다.
    material_name: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    ref_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    source_column: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    item: Mapped[CostTableItem] = relationship(back_populates="lines")


class CostRecipe(Base):
    """구성 헤더 — grain은 **「상품명 × 폼팩터」**다(계약 §0-B, Jino가 키를 바꿨다).

    ★왜 SKU가 아닌가: Jino 원문 *"상품명이 같으면 들어가는 부품도 같거든"* — 승인 대상이
    944 옵션에서 88 상품명 × 폼팩터로 줄고, 승인된 원가가 상품명→옵션→채널 코드 매핑을 타고
    채널 코드 2,638개까지 전파된다. 폼팩터가 별도 축인 이유도 Jino 원문이다:
    *"플립, 폴드 제품은 하나의 상품명에서 단가가 달라져"*.

    ★`form_factor`의 NULL 규칙은 **원가표 축**의 것이다 — `RecipeDraft.form_factor`·
    `CostTableItem.form_factor`에서 수입 완제품·매입품이 «없음»(NULL)이고, 0이나
    자리표시자로 채우지 않는다(계약 §2-7).

    ★★**그러나 매핑에서 태어나는 이 레시피의 grain은 상품명×폼팩터를 유지한다**
    (계약 D-CPP-61 §8-2, Jino 2026-08-26 처분). 구판 문언은 「수입 완제품·매입품은 NULL」
    이라 적혀 있었는데 **코드는 한 번도 그렇게 동작한 적이 없고**(prod 100건 NULL 0건),
    실측이 그 문언을 반증했다 — 같은 수입 상품명 「오하이 카메라 렌즈 강화유리 보호필름
    1매입」이 **bar(id 88)·flip(id 89)·fold(id 90) 세 레시피로 실재**한다. 폰 모양이 다르면
    카메라 유리도 다른 물건이라 폼팩터별로 원장 품목·매수가 갈릴 수 있고, NULL로 병합하면
    그 구분이 사라진다. 문서가 실측에 진 자리라 문서를 고쳤다(코드 동작 변경 아님).

    ★`recipe_kind`는 **세 값**이다 — `assembly` / `imported_goods` / `purchased`.
    셋 다 산술은 같다(Σ, 매입 완제품은 그 퇴화형 1줄). 값을 가르는 것은 **단가의 출처**다:
      `assembly`       구성 부자재의 단가 합
      `imported_goods` 통관 원장 로트 단가          (환율·로트로 변한다)
      `purchased`      **원가표의 「상품원가」**      (사람이 파일에서 채택한다)

    ★초판은 *"kind 값은 산술이 실제로 갈릴 때만 늘린다"*며 두 값으로 두었다. 그 규칙 자체는
    옳았는데 **전제가 틀렸다** — 국내 매입품(케이스·거치대·셀카봉·그립톡·카드케이스)을
    `assembly`로 두면 「구성이 0줄인 조립품」이 되어 165개 SKU가 영영 「정본 없음」에 갇히고,
    `imported_goods`로 두면 있지도 않은 통관 원장을 기다린다. 갈리는 것은 산술이 아니라
    **어느 문으로 단가가 들어오는가**였고, 그건 실재하는 분기다.
    Jino 확정 2026-09-01 20:0x, 원문 *"매입품이라 매입가로"*. 정본 주석은
    `services/cost_menu/materials.PURCHASED_KIND`.

    ⚠️ S1은 이 테이블을 **만들기만** 한다. 파싱·승인·계산은 S2·S3다.
    """

    __tablename__ = "cost_recipe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    form_factor: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="draft", server_default="draft"
    )
    source: Mapped[str] = mapped_column(  # excel / manual
        String(10), nullable=False, default="manual", server_default="manual"
    )
    recipe_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="assembly", server_default="assembly"
    )
    # 파싱 이상(계약 §9-4·§9-9)을 초안에 실어 승인 단계에서 사람이 처분한다 — 자동 판정 금지.
    # ★폭은 원천인 `CostTableItem.anomalies`와 **같아야 한다**(둘 다 String(200)).
    #   2026-09-04까지 여기만 String(40)이었고 세 소비처가 `[:40]`으로 잘라 넣었다. 그 결과는
    #   표시 흐림이 아니라 **판정 변화**다 — 잘린 꼬리가 토큰을 반으로 끊으면
    #   `costHome.ts`의 `anomalyKinds()`가 「needs_manual_lin」을 내고, 그 줄은
    #   「구성 없음」에도 「모순」에도 **안 선다**(prod 실측: r81이 정확히 그 상태였다).
    anomaly_flag: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ── 개정 4 (D-CPP-59) — 사람이 고른 원가표 항목 ────────────────────────────
    # ★`picked_item_id`만 두지 않는 이유: 재업로드가 항목 «행»을 통째로 교체하므로 FK만
    #   믿으면 S1 적대 리뷰 1R P1(**rowid 재사용**으로 cleaning kit의 근거가 다른 품목으로
    #   바뀐 사고)이 그대로 재발한다. 자연 키 스냅샷을 함께 저장해 **조회 시점에 재검사**한다
    #   (S1의 처방을 그대로 계승 — 계약 §0-E-3).
    picked_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cost_table_item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: `section\x1Fitem_name\x1Fform_factor` — 사람이 고른 «그 항목»의 자연 키 원문.
    picked_item_key: Mapped[Optional[str]] = mapped_column(String(600), nullable=True)
    picked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # ── 「원가표에 없음」의 «명시» 처분 (계약 합격 19) ─────────────────────────
    # ★암묵 판정을 금지하는 칸이다: 이게 없으면 「사람이 보고 없다고 판정했다」와
    #   「아직 아무도 안 봤다」가 화면에서 같은 모습이 된다(§2-7의 같은 결).
    absent_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    absent_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: ★grain의 셋째 축 — **변형** (계약 D-CPP-67, Jino 승인 2026-09-02).
    #:
    #: 왜 생겼나: 「상품명 × 폼팩터」로는 못 가르는 묶음이 **92 SKU** 실재했다(ref 124).
    #: 같은 상품명·같은 폼팩터인데 **옵션이 구성을 바꾼다** — 「오하이 빛반사, 지문방지 매트
    #: 필름 3매 / fold」 하나에 외3 · 외3+내3 · +후면2 · +후면2+힌지2 **넷**이 들어 있었고,
    #: 태블릿은 같은 「2매」인데 **화면 크기**가 단가를 갈랐다(기본/13인치/울트라).
    #: `CostStandard`가 `recipe_id + price_rule` 유니크라 **레시피당 계산값이 하나**이므로,
    #: 축을 안 더하면 한 값밖에 못 담고 그 묶음은 영영 「그레인 불일치 — 보류」에 남는다.
    #:
    #: ★**키를 «바꾼» 것이 아니라 «더한» 것이다.** 위 두 Jino 원문은 그대로 산다 —
    #: *"상품명이 같으면 들어가는 부품도 같거든"*은 92건에서 «틀린» 게 아니라 «부족»했고,
    #: *"플립, 폴드 제품은 하나의 상품명에서 단가가 달라져"*로 폼팩터를 더했던 것과 같은 걸음이다.
    #:
    #: ★**빈 문자열은 「단일 그레인」이라는 사실이지 「모름」이 아니다.** NULL로 두지 않는 이유는
    #: 위 `form_factor` 주석과 같다 — SQL에서 NULL끼리는 같지 않아 유니크가 중복을 못 막는다.
    #: 갈리지 않은 레시피는 전건 `""`이고, 화면은 빈 변형을 **그리지 않는다**.
    variant: Mapped[str] = mapped_column(
        String(60), nullable=False, default="", server_default=""
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list[CostRecipeLine]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    picked_item: Mapped[Optional[CostTableItem]] = relationship(lazy="selectin")

    __table_args__ = (
        # ⚠️ SQL에서 NULL끼리는 같지 않다 — `form_factor`가 NULL인 행(수입 완제품·매입품)은
        #   이 제약이 **중복을 막지 못한다**. 상품명 단독 중복은 승인 화면이 판정한다.
        #   ★`variant`는 그 함정을 안 물려받는다 — NOT NULL `''`이라 NULL 비교가 없다.
        UniqueConstraint(
            "product_name", "form_factor", "variant", name="uq_cost_recipe_name_form_variant"
        ),
    )


class CostRecipeLine(Base):
    """구성 한 줄.

    ★`quantity`는 **매수**다(3매 제품 = 3 — 원가표의 「매입」 열이 이 값이다).
    Jino 확인(2026-08-22): *"필름을 대량으로 조아테크에서 구매해서 우리가 제품에 따라서
    1장제품도 만들고 2장 제품도 만들어."* 매수는 상품명에 인코딩돼 있으므로(계약 §0-B)
    **같은 원단·다른 매수 = 다른 상품명·다른 레시피**이지 별도 축이 아니다.

    ★수입 완제품 라인은 `material_id` 대신 `ledger_item_name`(원장 품목명)을 참조한다 —
    강화유리처럼 단가가 원장 라인에서 직접 파생되는 것은 `cost_material` 종을 거치지 않는다.
    **둘 중 정확히 하나만 채워진다**(판정은 서비스층 — 훅·DB 제약이 아니라 코드가 본다).
    """

    __tablename__ = "cost_recipe_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("cost_recipe.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cost_material.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    ledger_item_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    recipe: Mapped[CostRecipe] = relationship(back_populates="lines")
    # ★S2 신설 — 컬럼이 아니라 «관계»다(마이그레이션 대상 아님). 표준원가 계산이 라인마다
    #   종의 단가 이력을 봐야 하는데, FK만 있고 관계가 없으면 N+1 쿼리이거나 수동 조인이다.
    #   `back_populates`를 걸지 않는 이유: `CostMaterial` 쪽에서 「나를 쓰는 레시피 라인들」을
    #   보는 소비처가 아직 없고, 없는 방향을 미리 만들면 삭제 규칙(RESTRICT)과 얽힌다.
    material: Mapped[Optional[CostMaterial]] = relationship(lazy="selectin")


class CostRecipeLink(Base):
    """SKU(옵션) 1건이 어느 레시피에 닿는가 — grain = SKU.

    ★폼팩터를 **상품명이 아니라 옵션(기종)이 정하므로**(계약 §0-B: 한 상품명이 플립·폴드
    옵션을 함께 담는 실례가 매핑 정본에 있다) 상품명 층만으로는 도달이 안 된다. 이 층이
    그 분류를 담는다.

    ★`internal_sku`는 **문자열 참조**다 — FK를 걸지 않는다(계약 B와 같은 이유: 원가 층
    미접촉). 채널 코드 도달은 기존 `product_channel_mapping`을 **소비**한다.

    ⚠️경계: 이 계약은 링크 없는 SKU를 「미연결 — 계산 안 함」으로 **표면화까지만** 하고
    커버리지·충돌의 판정·수리는 하지 않는다 → 소관: `track_product-connection-map.md`.
    """

    __tablename__ = "cost_recipe_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_sku: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("cost_recipe.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="draft", server_default="draft"
    )
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual", server_default="manual"
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("internal_sku", "recipe_id", name="uq_cost_recipe_link_sku_recipe"),
    )


class CostStandard(Base):
    """표준원가 계산 결과 — grain = **레시피 1건**.

    ★SKU가 아니라 레시피인 이유(계약 §5-1): 같은 레시피의 SKU들은 값이 같다(Jino 원문 ①).
    SKU별 행 944벌은 근거 없는 복제다. SKU별 표시·`cost_price` 격차는 보드가 링크 조인으로
    계산해 **표시만** 한다(저장 안 함).

    ★`breakdown`은 라인별 단가×수량 내역(JSON 문자열)이다 — «계산되는 방법이 나오는» 화면의
    원료이자 근거 보존이다. 재계산은 **쓰기 시점**에 한다(로트 확정·레시피 승인·수동 단가
    입력·설정 변경) — 읽기 시점 계산은 계산 시점 값이 안 남아 채택하지 않는다(계약 §5-2).

    ★두 값(ex/inc)을 다 저장한다 — 기준이 바뀌어도 재계산 없이 표시만 갈아끼우기 위해서다.
    """

    __tablename__ = "cost_standard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("cost_recipe.id", ondelete="CASCADE"), nullable=False, index=True
    )
    price_rule: Mapped[str] = mapped_column(
        String(20), nullable=False, default="latest", server_default="latest"
    )
    # ★nullable — 「단가가 하나라도 미확정이라 계산 못 함」과 「0원」은 다른 사실이다(계약 §2-7).
    std_cost_ex_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    std_cost_inc_vat: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    breakdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("recipe_id", "price_rule", name="uq_cost_standard_recipe_rule"),
    )


class CostSetting(Base):
    """설정 1키.

    ★`confirmed`가 이 테이블의 전부다 — **「법정 기본값이라 이 값이다」와 「우리가 신고한
    값이 이것이다」는 다른 사실**이고, 그 차이가 화면에 안 보이면 시스템이 모르는 것을 아는
    척하게 된다(계약 §9-1). 초기 2행:
      · `valuation_method` = `fifo`, **confirmed=False** — 법인세법 시행령 §74④의 **무신고 시
        법정 기본값**일 뿐이다. 우리 신고 내역(재고자산 평가조정명세서 「③신고방법」 칸)은
        미확인이다. 「선입선출이 우리 신고 방법」이라고 확정 기재하는 것은 금지선(계약 §3).
      · `standard_price_rule` = `latest`, confirmed=True — 표준원가(층1)는 7.14의 표준원가법이지
        법정 재고 평가(§74·계약 C)가 아니라, «실제와 유사»하기만 하면 되고 최신 로트가
        실측상 실제에 가장 가깝다(계약 §4).
    """

    __tablename__ = "cost_setting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    # ★Boolean에 정수 리터럴을 쓰지 않는다(교훈 #341) — `false()`가 방언별로 옳게 컴파일된다.
    confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ─────────────────────────────────────────────────────────────────────────────
# 원가 설정 이력 · 단가 자동 갱신 (계약 `CONTRACT_cost_valuation_autorefresh.md` D-CPP-60)
#
# ★왜 세 테이블인가 — 계약 §7-3의 자동 3요소(사후 가시성·정정 경로·근거 보존)가 각각
#   «쌓이는 자리»를 요구하기 때문이다. 셋 중 하나라도 자리가 없으면 자동이 아니라 방치다.
# ─────────────────────────────────────────────────────────────────────────────


class CostSettingHistory(Base):
    """`cost_setting` 변경 이력 — **append 전용**(계약 §4-②).

    ★왜 이력이 따로 필요한가: `cost_setting`은 in-place로 갱신되는 단일 행이라 «지금 값»만
    안다. 그런데 평가방법은 **누가 언제 왜 바꿨나**가 곧 회계 근거다 — 법인세법 시행령 §74의
    「신고한 방법」이 나중에 확인되면 그 시점 전후를 갈라야 한다. 지금 값만 남기면 그 질문에
    영영 답할 수 없다.

    ★`old_*`를 함께 담는 이유: 새 값만 쌓으면 「무엇에서 무엇으로」가 행 사이 뺄셈이 되고
    첫 행은 원본을 모른다. 정정 경로(§7-3 ②)는 옛 값을 알아야 성립한다.
    """

    __tablename__ = "cost_setting_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    old_value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    new_value: Mapped[str] = mapped_column(String(200), nullable=False)
    old_confirmed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # ★Boolean에 정수 리터럴을 쓰지 않는다(교훈 #341).
    new_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: 누가. 이 앱엔 로그인이 없으므로 화면이 보내는 문자열이다(기본 `jino`).
    #: ★자동 경로는 이 테이블에 **쓰지 않는다** — 평가방법 변경은 사람만 한다(§3 금지선).
    actor: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class CostAutoRefreshRun(Base):
    """단가 자동 갱신 **1회전**. 이벤트(로트 확정 직후)든 크론 sweep이든 한 행이 남는다.

    ★**「변화 없음」도 행을 남긴다**(계약 §2-6 침묵 금지). `updated=0`인 행이 매일 쌓이는
    것이 「자동이 살아 있다」의 유일한 증거다 — 아무것도 안 남기면 «잘 돌았는데 바뀔 게
    없었다»와 «죽었다»가 화면에서 똑같이 보인다. 이 저장소가 반복 실측한 fail-open이다.
    """

    __tablename__ = "cost_auto_refresh_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: `event`(로트 확정 직후) / `cron`(일일 sweep) / `manual`(사람이 화면에서 지금 실행)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    #: 검사한 «후보 라인» 수.
    checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 자동이 **안 건드리고 큐로 올린** 라인 수(신규 짝 — 계약 §7-4).
    queued: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CostAutoRefreshEntry(Base):
    """자동 갱신 1회전 안의 **개별 사건** — 「무엇이 어떻게 바뀌었나」의 근거 보존(§7-3 ③).

    ★`old_*`/`new_*`를 둘 다 담는다. 같으면 **멱등 확인**이고 그것도 사건이다 —
    「검사했는데 안 바뀌었다」와 「검사 안 했다」는 다르다.
    """

    __tablename__ = "cost_auto_refresh_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cost_auto_refresh_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: `linked`(새 단가 행 생성) / `unchanged`(이미 있음) / `failed` / `queued`(사람 대기)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    material_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
    material_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    #: 만들어진(또는 이미 있던) 단가 행. 실패·대기면 없다.
    price_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    import_invoice_line_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
    #: ★좌표 — 「어느 로트에서 왔나」. FK가 아니라 **스냅샷 문자열**이다: 원장이 재적재되면
    #:  rowid가 재사용되므로(계약 A′ S1 적대 리뷰 1R P1-2 실증) id만으로는 추적이 끊긴다.
    hbl_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    item_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    old_price_ex_vat: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    new_price_ex_vat: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    #: 실패·대기 사유. **비워 두지 않는다** — 사유 없는 실패는 화면에서 침묵과 같다.
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CostRoundTripSnapshot(Base):
    """왕복 파일 1장을 내려보낸 **그 순간의 표** (계약 D-CPP-62 S3).

    ★**불변 증거물이지 데이터가 아니다.** S4의 3-방향 대조(스냅샷 ↔ 파일에 적혀 온 값 ↔
    지금 현재값)에서 «내가 받았을 때 무엇이 적혀 있었나»를 대는 쪽이다. `linked_item_name`
    (위 `cost_material_price`)이 「그때 무엇을 보고 복사했나」를 굳혀 두는 것과 같은 부류다.

    ★**행을 정규화한 테이블을 만들지 않고 `rows` JSON 한 칸에 담는다.** 이유 둘:
      ① 열 스펙이 진화하면 정규화 스키마가 따라 바뀌어야 하고, 그 순간 **구 스냅샷이
         마이그레이션 부채**가 된다. blob은 구판을 그대로 품는다 — `column_spec`을 같이
         담으므로 스냅샷이 **자기서술적**이다(구 파일을 업로드해도 라벨→키 매핑이 그 파일의
         스냅샷 안에 있다).
      ② 정규화된 스냅샷 행 테이블은 생김새가 **단가 이력의 두 번째 사본**이다. 누군가 반드시
         그걸 조회하기 시작하고, 그 순간 정본(`cost_material_price`)과 갈라진다.
         이 저장소가 반복해 밟은 병이 그것이다(D-CPP-60 · 직렬화기 두 벌).
      대가: material별 스냅샷 횡단 조회가 안 된다 — 그 질문의 정당한 답은 어차피 단가 원장이다.

    ★`content_hash`가 직전 스냅샷과 같으면 **새 행을 만들지 않고 같은 id를 재발급한다.**
      다운로드가 사실상 멱등이 되고, 성장이 「상태가 실제로 바뀐 횟수」에 묶이며,
      같은 상태를 두 번 받아도 S4에서 가짜 충돌이 안 난다.

    ★`created_at`을 **앱에서 KST로 명시 세팅**한다. `server_default=func.now()`는 이
      저장소에서 UTC다 — 파일에 찍히는 시각이 9시간 어긋나면 사람이 「어느 게 최신인가」를
      바로 그 자리에서 다시 묻게 된다(이 계약이 없애려는 질문이다).
    """

    __tablename__ = "cost_roundtrip_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 파일에 `CRT-{id}`로 찍힌다. S4가 이 값으로 스냅샷을 되찾는다.
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: 그 시점 열 스펙의 사본(key/label/file_label/editable, **순서 포함**).
    column_spec: Mapped[str] = mapped_column(Text, nullable=False)
    #: `[{"id": 12, "name": "...", ...}]` — 열 key → 정준화된 값. 없음은 null(0 아님).
    rows: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `column_spec` + `rows`의 sha256. 중복 발급 차단용.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class CostPurchasedPrice(Base):
    """**매입 완제품 SKU 1건의 매입가** — 계약 D-CPP-63 §0-F 결정 (a).

    ★★왜 레시피가 아니라 SKU 그레인인가 (이 테이블의 존재 이유):
    매입가는 **기종마다 다르다.** prod 실측(2026-08-28) — 레시피 84 「일미리 케이스」는
    51 SKU가 **922원 29개 / 2,400원 22개**, 레시피 88 「카메라 렌즈 1매입」은 26 SKU가
    **1,000 / 2,000 / 2,694** 세 값이다. 그런데 조립품 경로의 `cost_standard`는
    `recipe_id`+`price_rule` 유니크라 **레시피당 값이 하나**다 — 그 그레인에 매입가를 넣으면
    29개의 922원과 22개의 2,400원이 **하나의 거짓 숫자**로 뭉개진다.

    ★왜 레시피 경로를 재사용하지 않았나: 매입품의 `cost_recipe_line`은 **원래 0줄**이다
    (매입품 초안 50건 전건). 레시피·구성·표준원가는 이 물건들에게 처음부터 빈 껍데기였고,
    빈 껍데기를 가격 수만큼 쪼개는 안(계약 §0-F 안 b)은 없는 구조를 두 배로 만드는 일이다.
    ⇒ **저장소의 그레인을 데이터의 그레인에 맞춘다.**

    ★★경로 분리가 곧 금지선의 집행이다: 조립품(필름·액정)은 우리 레시피 계산이 정본이고
    파일 값이 닿으면 안 된다(Jino 2026-08-28 18:17 *"매입완제품만 보자. 나머지는 우리가
    했던 작업이 최신이야"*). 이 테이블이 조립품 경로와 **한 줄도 공유하지 않으므로** 그
    사고가 «규칙»이 아니라 «구조»로 막힌다.

    ★행은 쌓인다(덮어쓰지 않는다) — `cost_material_price`와 같은 결이다. 최신 1건이 현재
    값이고 과거 행은 근거로 남는다. 값이 왜 바뀌었는지를 나중에 되짚을 수 없으면 그것은
    원장이 아니라 그냥 칸이다.
    """

    __tablename__ = "cost_purchased_price"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: `product_master.internal_sku`. FK를 걸지 않는다 — `product_master`는 채널 수집이
    #: 다시 만드는 표라 FK가 걸리면 수집이 이 표를 인질로 잡는다(저장소 관례).
    internal_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: ★nullable이다 — 「단가를 아직 모른다」와 「0원이다」는 다른 사실이다(상속 금지선).
    #: 파일의 `1`원 자리표시자는 **여기 오지 않는다**(계약 §3: 1원 저장 금지).
    unit_price_inc_vat: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    #: ★부가세 포함값이 정본이다. Jino 확인(2026-08-28 18:27) *"포함 되어 있어."* —
    #: 파일 원가에는 관세·물류 등 부대비가 **이미 들어 있다.** 여기에 부대비를 다시 얹는
    #: 코드를 만들지 않는다(계약 §3 금지선 — 이중 계상은 조용히 틀리고 화면에서 안 보인다).
    #: 세전 값은 저장하지 않는다: 파생 규칙이 두 벌이 되는 자리다(§3 「로직 두 벌 금지」).

    #: 'file' = 원가 매핑 파일에서 왔다 / 'manual' = 사람이 화면에서 입력했다(공백 34건).
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    #: 어느 파일·어느 판에서 왔는지. 「08-07판」처럼 **사람이 읽는 좌표**를 남긴다 —
    #: 두 판의 열 구성이 달라 이 값이 없으면 나중에 어느 쪽을 읽었는지 못 되짚는다.
    source_file: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    #: 파일의 상품명 원문(제품+옵션 통합). 우리가 자른 값이 아니라 **그 행이 뭐라고 적혀
    #: 있었나**를 남긴다 — 매칭을 나중에 재현하려면 원문이 있어야 한다.
    source_product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    #: 사람이 확인 클릭한 시각. NULL이면 «제안»이지 확정이 아니다 — 계산은 확정만 읽는다
    #: (상속 금지선: 승인 없는 값을 계산에 쓰지 않는다).
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_cost_purchased_price_sku_created", "internal_sku", "created_at"),
    )


class CostPriceHistory(Base):
    """`product_master.cost_price` 변경 이력 — **append 전용** (계약 D-CPP-64 §4 S1-①).

    ★왜 필요한가: `cost_price`는 in-place로 갱신되는 단일 값이라 «지금 값»만 안다. 그런데
    이 계약의 궁극 목표 세 요소 중 둘(*"수정이 있을때 실수없이 잘 수정되어서"* · *"최신
    원가가 유지되게"*)은 **사후에 확인할 수 있어야** 성립한다. 2026-08-31 실측(ref 119 §3):
    쓰기 경로가 22개인데 `cost_price`를 만지는 **넷 다 이력이 안 남아** 「수정이 실수 없이
    됐나」에 답할 방법이 아예 없었다. `product_master.updated_at`은 20일째 정지해 있어
    대체재가 못 된다(ref 118 §2-1).

    ★`old_value`를 함께 담는 이유는 `CostSettingHistory`와 같다 — 새 값만 쌓으면 「무엇에서
    무엇으로」가 행 사이 뺄셈이 되고 첫 행은 원본을 모른다. 정정 경로가 옛 값을 알아야 한다.

    ★`internal_sku`가 정본 키다(`product_id`가 아니다). 상품 행이 지워져도 「그때 그 SKU의
    원가가 이렇게 움직였다」는 사실은 남아야 하기 때문이다 — FK를 걸지 않는 것도 같은 이유다.

    ★**in-place 수정·삭제 금지**(계약 §3-B). 고칠 수 있는 이력은 이력이 아니다.
    """

    __tablename__ = "cost_price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 기록 시점의 상품 행 id. 참고용이고 조회 키가 아니다(FK 없음 — 위 docstring).
    product_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: 신규 생성이면 **옛 값이 없다** — 0이 아니라 NULL이다(「없음 ≠ 0」, 계약 §3-B).
    old_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    new_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    #: **어느 문으로 들어왔나.** ref 119 §3의 경로 목록과 같은 어휘를 쓴다. 「값이 바뀌었나」
    #: 보다 «어디로 바뀌었나»가 이 계약의 질문이라(안 잠긴 문 찾기) 이 칸은 nullable이 아니다.
    #: 현재 어휘: `excel_upload` · `mapping_ingest` · `product_create` · `product_update`
    #: · `cutover`(S3에서 생긴다) · `auto`(S4 자동 추종).
    path: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    #: 누가. 이 앱엔 로그인이 없으므로 화면·경로가 보내는 문자열이다(자동 경로는 `system`).
    actor: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    #: **근거 좌표를 사람이 읽는 문장으로.** 「파일명·행」·「레시피 45 계산값」처럼 나중에
    #: 되짚을 수 있는 것을 적는다. 비면 화면이 「근거 없음」이라고 **말한다** — 빈칸으로
    #: 두지 않는다(계약 §3-B 「없음 ≠ 0」의 문장판).
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: ★서버 기본값은 **UTC**로 저장된다(SQLite `now()`). 화면이 KST로 환산한다 — 저장을
    #: KST로 바꾸지 않는 이유는 기존 이력 테이블 전부가 같은 규약이라서다(두 규약 금지).
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_cost_price_history_sku_created", "internal_sku", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# OTAO 발주 원장 (계약 `CONTRACT_inventory_unified.md` §4 S1 · 트랙 D-INV-1·3)
#
# 왜 «새» 테이블인가: 발주 축의 SKU 라인은 **어디에도 없었다.** ECOUNT 발주서조회 API는
# 헤더 그레인이라 `PROD_CD`를 안 준다(S0-a 판정, 1,622건 전건). SKU 단위 원천은 발주서
# PDF뿐인데 그 PDF는 Jino의 Google Drive 로컬 동기화 폴더에 있어 **prod가 못 읽는다.**
# 그래서 파싱 결과를 원장으로 «심는다» — 이 테이블이 S1 「발주 누계」 칸의 유일한 뿌리다.
#
# ★수입 원장(`import_invoice_line`)과 섞지 않는다: 저쪽은 계약 A′/B 소관이고 우리는 **읽기
#   전용 소비자**다(계약 §3-8). 이쪽은 본 계약 소유다. 둘의 만남은 «조인»이지 «병합»이 아니다.
# ─────────────────────────────────────────────────────────────────────────────


class OtaoPurchaseOrder(Base):
    """발주서(Purchasing order) PDF 한 장 = 한 행. **파일 단위**로 담는다.

    ★같은 `serial`이 여러 파일로 존재한다(실측: 121파일 → 고유 발주번호 66, 중복 보유 28건).
    그중 수량까지 다른 «개정본»이 4건 있었다(예: `20260107-2` 5,700 vs 1,300). 그래서 정본
    하나만 남기고 버리지 않고 **전부 담되 `is_authoritative`로 가른다** — 버리면 「왜 이 숫자인가」를
    나중에 아무도 못 되짚고, 개정 이력 자체가 인사이트이기 때문이다(근거 보존).

    **정본 규칙 (D-INV-3)** — 집계는 항상 `is_authoritative=True`만 센다:
      ① ECOUNT 사본(`source_kind='ecount'`)이 있으면 그것이 정본이다.
         근거는 Jino 목표 원문이다 — *"**ecount에 있는** 우리가 발주한 수량 … 대비해서"*.
         라이브 대조도 같은 답을 냈다: `20260107-2`의 통관 원장 실입고가 **정확히 1,300**으로
         ECOUNT 사본과 일치했고 5,700본과는 4,400 어긋났다.
      ② ECOUNT 사본이 없으면(2023~2024년분) 로컬 파일 중 `Revise_` 접두 또는 후행 사본.
         Jino 2026-08-25 23:13 *"revise가 있는건 개정본이겠지?"*
      ③ 중복은 항상 `serial`로 접는다. 파일명으로 접으면 최상위 18개가 연도 폴더와 중복 계상된다.

    ★`header_qty`·`total_amount`는 **검산 재료**다(PDF가 스스로 적어 둔 합계). 라인 합과
      어긋나면 파싱이 틀렸거나 문서가 특이한 것이므로, 그 자체를 저장해 두고 대조에 쓴다.
    """

    __tablename__ = "otao_purchase_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # "20260812-1" — 발주서 상단 `Serial No.`. 발주일이 앞 8자리에 박혀 있다.
    serial: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    order_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)

    # 'ecount' = ECOUNT에서 내려받은 사본(해시형 파일명) / 'local' = 우리가 만들어 보낸 원본
    source_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="local")
    # 발주 폴더 기준 상대경로. 절대경로를 넣지 않는다 — 계정·머신마다 다르다.
    source_file: Mapped[str] = mapped_column(String(300), nullable=False)
    # 파일 내용 해시. 같은 파일 재적재를 멱등으로 만든다.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # ★집계는 이것이 True인 행만 센다. 규칙은 클래스 docstring.
    is_authoritative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )
    # 정본이 아니면 왜 아닌지 한 줄 — 화면에서 「왜 이 숫자인가」를 되짚을 수 있어야 한다.
    supersede_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ── 문서가 스스로 적은 합계 (검산 재료, 라인 합과 대조한다) ──
    header_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    parsed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lines: Mapped[list["OtaoPurchaseOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # 같은 파일을 두 번 심지 않는다. serial은 중복이 정상이므로 unique를 걸지 않는다.
        UniqueConstraint("content_sha256", name="uq_otao_po_sha"),
    )


class OtaoPurchaseOrderLine(Base):
    """발주서의 품목 한 줄. ★`product_code`가 이 트랙 전체의 라벨 공간 정본이다.

    발주서에 `Product Code` 칸이 실재한다(`GAPIP15PR`·`PGAPIP16PR` …). 그래서 SKU 라벨을
    문자열 휴리스틱으로 «추론»할 필요가 없다 — **문서가 직접 적어 준다.**

    ★열 구성이 두 가지다(실측):
      A형 `Product Code | Product(한글) | 영문상품명 | Quantity | …` — `name_en`이 있다
      B형 `Product Code | Product(한글) | Quantity | …`            — `name_en`이 **없다**
    B형에서 `name_en`은 NULL이다. 없는 값을 한글명에서 지어내지 않는다 — 그게 매핑 오염이다.

    ★`name_en`이 값을 가질 때 그것은 통관 원장 `import_invoice_line.item_name`과 **글자 그대로
      같다**(예: `Glass_iP15 pro`·`Privacy Glass_iP16 Pro 2ea`). 즉 이 컬럼이 곧
      「원장 품목명 → 상품코드」 사전이고, D-INV-1이 본 계약 범위로 편입한 그 매핑의 재료다.

    ★수량 단위 주의: 이 품목군은 상품 자체가 「2매입」이다(Jino 2026-08-25 22:50). `quantity`는
      **상품 단위**(2매입 팩 수)이지 낱장 수가 아니다 — 라고 읽는 것이 자연스러우나 원장 쪽
      단위와의 일치는 **[미상]**이다. 화면은 단위를 명시하고, 두 해석을 합산하지 않는다.
    ★그리고 수량이 **50의 배수라는 보장은 없다**(D-INV-4): `20251210-1`에 12·395·420이 실재하고
      헤더 검산과 맞는다. 「50의 배수만 유효」 같은 필터를 넣으면 실제 발주를 버린다.
    """

    __tablename__ = "otao_purchase_order_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("otao_purchase_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    product_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name_ko: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2), nullable=True)

    order: Mapped[OtaoPurchaseOrder] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("order_id", "seq", name="uq_otao_po_line_seq"),
    )


class OtaoItemNameMap(Base):
    """통관 원장 품목명 → 상품코드 사전. **D-INV-1으로 본 계약 범위에 편입된 그 매핑이다.**

    왜 별도 테이블인가: `import_invoice_line.internal_sku`가 prod 실측 **0/158(0%)**이라
    픽업 누계를 SKU별로 셀 수 없었다. 그런데 그 컬럼을 우리가 채우는 것은 계약 A′ 소관이라
    금지선 8에 걸린다(§3-8). 그래서 **원장은 읽기 전용으로 두고 사전을 이쪽에 둔다.**

    ★A′가 나중에 `internal_sku`를 채우면 **그쪽이 정본이 되고 이 테이블은 검산 축으로 내려간다.**
      이원화를 영구화하지 않는다는 것이 D-INV-1의 조건이었다.

    **Jino 확정 규칙 3 (D-INV-2)** — 이것이 정본이고 추론이 아니다:
      1. `screen protector` 접미는 상품 구분이 아니다 — `Glass_Ip16 Pro` ≡ 같은 이름 + 그 접미
      2. 공용 표기 ≡ 단일 표기 — `For iP15/16/14Pro` ≡ `Glass_iP15 pro` (6.1" 필름 하나)
      3. `2ea` = 2매입 포장 — 상품의 포장 규격이지 별개 상품이 아니다

    ★`product_code`가 NULL이면 «아직 못 붙였다»는 뜻이고, 그 상태는 **화면에 「매핑 필요」로
      드러내야 한다**(계약 §2-9·§3-6). 조용히 빼면 발주 누락, 조용히 넣으면 발주 오염이다.
    """

    __tablename__ = "otao_item_name_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 원장에 적힌 그대로. 정규화 전 원문을 키로 둔다 — 정규화 규칙이 바뀌어도 출처가 남는다.
    raw_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    # NULL = 미확정. 0이나 빈 문자열로 대체하지 않는다(«모름»과 «없음»을 가른다).
    product_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)

    # 'exact_en'(발주서 영문명 일치) / 'exact_ko' / 'normalized'(규칙 3 적용 후 일치)
    # / 'manual'(사람이 확정) / 'unmatched'
    match_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="unmatched")
    # 어떤 발주서 라인을 근거로 붙였는지 — 되짚기용.
    evidence: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class OtaoStockSnapshot(Base):
    """ECOUNT 창고별 재고의 **읽기 전용 스냅샷 미러**. 계약 §4 S4 「초기 실사」 항의 재료.

    계약 `CONTRACT_inventory_unified.md` §4 S4 · 트랙 `track_inventory-management.md`
    · 체인 `발주예측` n=8.

    ## ★왜 이 테이블이 필요한가 — 같은 것을 이미 한 번 잃었다

    n=2(2026-08-25)가 ECOUNT에서 창고별 재고 1,391행을 받아 되감기를 실증했다
    (`재고(t) = 현재고 − Σ입고(>t) + Σ판매(>t)`, 188칸 중 음수 2칸). 그런데 그 스냅샷과
    크로스워크 스크립트가 **세션 스크래치패드에만 있었고 세션과 함께 사라졌다**
    (`docs/references/99_*.md` §9가 스스로 "비커밋"이라 적어 뒀다). n=8 실측(2026-08-27):
    저장소·prod 어디에도 사본이 없고 `inventory` 테이블은 **0행**이다.

    ⇒ 「188칸 중 음수 2칸」이라는 이 트랙의 핵심 실증이 **지금은 재현 불가**다. 관측을 원장에
    담지 않으면 관측은 주장으로만 남는다.

    ## ★정본은 ECOUNT다 — 이 테이블은 «정본이 아니다» (금지선 1)

    §3-1은 *"재고 정본 이원화 금지 — 이카운트·ohisell 양쪽에 자사창고 수량을 동시에 **쓰기**
    시작하는 것"*을 금지한다. 이 테이블은 그 금지에 걸리지 않는다:

      - **쓰기가 한 방향뿐이다** — ECOUNT → ohisell. 우리가 ECOUNT에 재고를 쓰는 경로는 없고
        만들지도 않는다(§3-2 자동 «실행» 금지와 같은 결).
      - **행을 고치지 않는다** — 스냅샷은 «그 시각에 그렇게 보였다»는 관측 기록이라 추후 정정
        대상이 아니다. 값이 달라지면 새 `snapshot_at`으로 **새 행**이 쌓인다.
      - **수량의 권위를 주장하지 않는다** — 화면은 항상 「ECOUNT 스냅샷(찍은 시각)」으로 부르고
        「자사 재고」라고 단정하지 않는다. Jino 원문이 그 한계를 이미 말했다:
        *"현재 본사 재고로 잡혀있는 수량들은 비슷한 수준이지 100%는 아니야"*(2026-08-25 18:08).

    ## ★창고를 합치지 않는다 (§1 창고 5개 표)

    같은 1,008개라도 「본사에 있는 것」과 「이미 쿠팡에 나가 있는 것」은 발주 판단에서 정반대
    의미다. 초판 실측이 **전 창고 합계**를 내서 틀렸던 자리다. 그래서 창고를 행 키에 넣고
    합계는 서비스 층이 «역할별로 갈라서» 만든다 — 이 테이블은 합치지 않는다.

    창고 의미는 데이터에 안 적혀 있고 Jino만 안다(§1 표가 원문 정본):
      본사(차감항 본체) · 본사-포장(부자재) · 쿠팡 제트배송(이미 채널에 나감) ·
      반품창고(미사용) · 아마존(미사용)

    ## 멱등성

    `(snapshot_at, warehouse_code, product_code)`가 유일하다. 같은 스냅샷을 두 번 적재해도
    행이 늘지 않는다. **다른 시각의 스냅샷은 서로 다른 행**이다 — 덮어쓰면 시계열이 사라지고,
    시계열이 없으면 S4의 「오차」를 원리적으로 못 잰다(오차는 두 시점 사이에서만 생긴다).
    """

    __tablename__ = "otao_stock_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 이 스냅샷을 «찍은» 시각(KST naive). 되감기의 t0가 되는 축이라 인덱스가 필수다.
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # ECOUNT 응답이 기준일자를 주면 담는다. 안 주면 NULL — `snapshot_at`으로 대체하지 않는다
    # (「찍은 시각」과 「기준일」은 다를 수 있고, 같다고 지어내면 되감기 창이 어긋난다).
    base_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # ECOUNT 창고코드. 코드가 없으면 이름을 그대로 넣는다 — 빈 문자열로 접지 않는다.
    warehouse_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    warehouse_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    # ECOUNT 품목코드. 이 트랙의 라벨 공간(`GAPIP…`)과 같은 축이라 발주·픽업에 바로 붙는다.
    product_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # ★Integer가 아니라 Numeric이다 — ECOUNT가 소수를 돌려줄 수 있고, 임의 반올림은
    # 「원장이 말한 값」을 우리가 바꾸는 것이다.
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)

    # 'ecount_api' | 'manual' — 사람이 센 실사값이 들어오면 출처가 갈려야 한다.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="ecount_api")
    # 응답 원문 1행. 컬럼으로 안 뽑은 필드를 나중에 되짚기 위해 남긴다.
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "snapshot_at",
            "warehouse_code",
            "product_code",
            name="uq_otao_stock_snapshot_grain",
        ),
    )


# ──────────────────────────────────────────────
# 파워링크(WEB_SITE) 텍스트 소재 — GET /ncc/ads가 버리던 절반
# 계약 docs/contracts/CONTRACT_ignition_readiness.md §4-A S5 · D-NAO-263
# ──────────────────────────────────────────────
class NaverAdCreativeText(Base):
    """파워링크 텍스트 소재의 **현재 단면** 1행 — `GET /ncc/ads` 응답의 `ad{}` 블록 적재.

    grain: (ad_id). upsert — 역사는 이 표에 없다(역사는 `NaverAdCreativeTextChange`).

    ★**왜 지금까지 DB 0행이었나**: `naver_sa_ad_fetcher.get_ads()`가 `referenceData.mallProductId`가
    없는 소재를 `continue`로 버린다(같은 파일, 쇼핑 매핑 전용으로 태어난 함수라 의도된 필터다).
    파워링크 소재는 `referenceData`가 **아예 None**이고 문안이 `ad{headline,description,pc,mobile}`에
    실려 오므로 **한 건도 남지 않았다**(ref 103 §5). 이 표가 그 절반을 받는다.

    ★**「쇼핑엔 키워드가 원리적으로 없다」의 짝**(D-NAO-255 · ref 102): 쇼핑 소재는
    `SHOPPING_PRODUCT_AD`라 별도 광고 제목 칸이 없고 상품명이 곧 광고 제목이라 광고 축에서
    손댈 수 없다. 반대로 파워링크는 **문안이 광고 자산 자체**라 상품명을 안 건드리고 고칠 수
    있다 — 계약 §7-3이 이 슬라이스를 「액셀 쪽 짝」으로 배정한 근거다.
    ⚠️단 이 표는 **적재만** 한다. 쓰기(문안 수정)는 계약 §1 「안 하는 것」 6이 점화 후 별도
    계약으로 미뤘다 — 이 표의 존재가 그 승인을 대신하지 않는다.

    ★`headline`은 대체키워드 구문을 **원문 그대로** 담는다(라이브 실측 2026-08-27:
    `"오하이 {keyword:갤럭시 지문방지필름}"`). 치환 후 문구가 아니라 **등록된 문안**이 이 표의
    대상이다 — 치환 결과는 검색어마다 달라 소재 grain에 담기지 않는다.

    ★`edit_tm`은 **문자열 원문**이다(UTC ISO8601 `"2023-09-18T04:35:35.000Z"`). 파싱하면 타임존
    가정이 섞이는데, 이 축에 지금 필요한 것은 «값이 바뀌었는가»이지 «언제인가»가 아니다
    (`NaverProductMetaCurrent.reg_date`와 같은 판단).

    ★소급 불가: `/ncc/ads`는 **현재값만** 준다. 즉 **수집 개통일 = 관측 창의 시작일**이고,
    그 전의 문안 변경은 어디에도 없다(C10·검색량 기준선 D-NAO-186과 같은 성질). 계약 §5가
    *"제목·태그는 콘솔에서 누가 만지는 순간 원복 좌표가 사라지므로 S5는 늦을수록 잃는다"*고
    적은 것이 이 뜻이다.
    """

    __tablename__ = "naver_ad_creative_text"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    adgroup_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # 캠페인 좌표는 `naver_entity`(adgroup 행)에서 채운다 — 응답엔 캠페인 id가 없다.
    campaign_id: Mapped[str] = mapped_column(String(60), nullable=False, default="", index=True)
    campaign_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # 타입은 원문 그대로 — 실측은 TEXT_45 하나지만 상수로 굳히지 않는다(다른 타입이 오면
    # 필터가 아니라 이 컬럼이 그 사실을 보여야 한다).
    ad_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    # 문안·링크는 Text — 대체키워드 구문 때문에 «표시 15/45자»보다 길 수 있다(자르면 원문이 손상된다).
    headline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pc_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pc_display: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mobile_final: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mobile_display: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    inspect_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    user_lock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    edit_tm: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # 컬럼으로 안 뽑은 키를 되짚기 위한 원문(`ad` 블록 + 소재 상태 필드). 키 부재와 null의
    # 구분이 필요하면 이쪽이 정본이다(교훈 #315).
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("ad_id", name="uq_naver_ad_creative_text_ad"),
        Index("ix_naver_ad_creative_text_ag", "adgroup_id", "last_seen_at"),
    )


class NaverAdCreativeTextChange(Base):
    """파워링크 문안의 **변경분만** append (S5 · D-NAO-263).

    grain: (ad_id, observed_at). 변경이 있을 때만 행이 생긴다.

    ★`observed_at`은 «폴링 시각»이지 «변경 시각»이 아니다 — 폴링이 일 1회라 실제 변경 시각은
    ±1일 불확실하다. 정확한 변경 시각을 원하면 `edit_tm`이 그 앵커다(단 네이버가 피드를
    재적용해도 전진할 수 있다 — D-NAO-137의 쇼핑 실측이 그랬다. 파워링크에서도 같은지는 [미상]).

    ★첫 회차는 전건 신규라 이 표가 **0행인 것이 정상**이다(신규 insert는 변경이 아니다).
    """

    __tablename__ = "naver_ad_creative_text_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ad_id: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # {필드명: [old, new]} — 키 부재는 null로 접힌다(구분이 필요하면 current.raw_json이 정본)
    changed_fields: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_naver_ad_creative_text_change_ad_at", "ad_id", "observed_at"),
    )
