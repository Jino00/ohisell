# models.py — SQLAlchemy 모델 (ohisell 전체 테이블)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Date, ForeignKey, Integer, Numeric,
    String, Text, UniqueConstraint, func,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="orders")
    product: Mapped[Optional[ProductMaster]] = relationship()


class SyncLog(Base):
    """동기화 실행 이력"""

    __tablename__ = "sync_log"

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

    day_cost: 해당 날짜 확정 광고비(원, report/SALES DELIVERED_AD_COST).
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
    conv_sales: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    month_cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # 쿠키 저장 시각
    last_success_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )  # 마지막 sync 성공(=만료 측정)
    # 대시보드 "광고비 갱신" 버튼이 set, Mac 페처 데몬이 claim(=None)으로 소비.
    # 이 값이 있으면 다음 폴링에서 페처가 headful fetch를 1회 수행한다(버튼 트리거 방식).
    refresh_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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


# ──────────────────────────────────────────────
# RG 정산 수수료 (트랙 RG-Fee-Accounting S2)
# ──────────────────────────────────────────────
class CoupangRgSettlementFee(Base):
    """쿠팡 로켓그로스(RG) 정산 수수료 — 윙 내부 API(status/api) 수집.

    D-1: 수집 소스=윙 내부 API(세션쿠키). D-9: 판매수수료(B)+풀필먼트(J) 둘 다.
    D-10: 날짜 기준=매출인식일(recognition_date_from/to). D-11: 광고비 dedup 광고비 출처 구분.
    grain=(account_key, recognition_date_from, recognition_date_to, fee_type).
    Phase 1: 계정 단위 대조뷰용(vendor_item_id 없음). Phase 2(S6): vendor_item_id 컬럼 추가.
    amount: 발생비용(f, D-10). 취소/환급은 음수 허용(D-9).
    fee_type: 'sale_fee'(판매수수료B), 'fulfillment'(풀필먼트J), 'storage'(보관비),
              'return'(반품비), 'other'(기타). raw_type: API 원본 항목명.
    """

    __tablename__ = "coupang_rg_settlement_fee"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "recognition_date_from", "recognition_date_to", "fee_type",
            name="uq_coupang_rg_settlement_fee",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    recognition_date_from: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    recognition_date_to: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    fee_type: Mapped[str] = mapped_column(String(30), nullable=False)
    raw_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
