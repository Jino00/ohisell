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
        UniqueConstraint("channel_id", "order_number", "platform_product_id", name="uq_order_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_master.id"), nullable=True
    )
    order_number: Mapped[str] = mapped_column(String(100), nullable=False)
    platform_product_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
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
