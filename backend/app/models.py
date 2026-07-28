# models.py — SQLAlchemy 모델 (ohisell 전체 테이블)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, DateTime, Date, Float, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, func, text,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="orders")
    product: Mapped[Optional[ProductMaster]] = relationship()


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
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RocketProductCostMap(Base):
    """쿠팡 로켓배송(1P) 원가 브리지 — 발주상세 상품번호 → product_master.internal_sku (트랙 rocket-1p, S4.5b/D-13).

    배경(ref 20b §3): 1P 발주상세의 상품번호/바코드는 우리 원가 마스터(product_master, 3P/RG 카탈로그)에
      0건 매칭(1P supplier 카탈로그 ≠ 3P Wing 카탈로그). 자동 조인 불가 → 일회성 수동 브리지 테이블 신설(A1).
    grain: product_number(1P 발주상세 상품번호, CoupangRocketPurchaseOrderItem.product_number와 동일 키). unique.
    용도(S4.5c): net_profit cost = Σ(po_item.order_qty × product_master.cost_price[internal_sku=이 매핑]).
      이 테이블은 product_number ↔ internal_sku 연결만 — cost_price는 product_master가 정본(회계 일관성, D-13).
    status:
      'confirmed' = internal_sku 채워짐 → 원가 산정 대상.
      'ignored'   = internal_sku 비움(원가 제외: 샘플/증정/원가 없음). 미매핑 목록에서 제외(재제안 방지).
    읽기전용 원칙 외(이 테이블은 사용자 확정 입력) — 단 종합조망 net_profit은 S4.5c에서만 결합(S4.5b는 매핑만).
    """

    __tablename__ = "rocket_product_cost_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)  # ★브리지 키
    internal_sku: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)  # → product_master.internal_sku
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="confirmed")  # confirmed | ignored
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
    # D-NAO-57(B): 광고 의사결정 BEP에 쓴 수수료율 기준 — ad_case(정산 유형별 실측 분해=주문관리+
    # 매출연동 언디루션) / blended(기존 전체 회계 실효율 폴백). None=산출 전/폴백 미판정.
    commission_basis: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NaverAdgroupProduct(Base):
    """네이버 쇼핑 광고그룹 ↔ 판매 상품 매핑 (D-NAO-57 A, 관찰성 sync).

    grain: (adgroup_id, mall_product_id). 소스: 쇼핑 소재 /ncc/ads의
    referenceData.mallProductId(type=SHOPPING_PRODUCT_AD) — naver_product_bep.channel_product_id와
    정확히 일치(라이브 실증). 한 그룹에 소재(상품)가 여럿일 수 있어 unique는 (adgroup, mall_product).
    optimizer='ours' 쇼핑 캠페인의 활성 그룹만 매일 08:20 sync가 스냅샷 교체(그룹 단위).
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
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # P5: 실제 API쓰기 없이 기록만
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # P5: 실행 시도 시각(dry-run 포함)


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
    bid_amt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 그룹 기본가·키워드 개별입찰
    monthly_volume: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # keywordstool PC+Mobile 합
    competition: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # low/mid/high
    volume_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    qi_grade: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 품질지수 1~7(D-NAO-46②, keyword 행만 — /ncc/keywords nccQi.qiGrade)
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


class NaverAgencyOp(Base):
    """대행사(및 계정 전체 외부) 조작 이벤트 1건 (SA-2, D-NAO-78 · BM 벤치마크 레이어).

    naver_entity_snapshot의 D-1 vs D diff로 산출 — 결정적·리플레이 가능(bm_diff.py). 예외
    브리핑(P5)의 원료. 관찰 전용 — 네이버 API 쓰기 0(diff는 DB-to-DB).
    ★naver_change_log와 분리: change_log는 OUR 제안·집행의 피드백 루프(proposal_id·outcome·
    verify)에 묶여 있어, 45캠페인 대행사 노이즈를 섞으면 학습 쿼리가 오염된다(§2 결정).
    op_date+entity_id+op_type을 리플레이 키로 삼아 같은 날 재실행은 삭제-재생성(멱등).
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
    op_type: Mapped[str] = mapped_column(String(24), nullable=False)  # bid_change/status_flip/keyword_add/keyword_remove/campaign_add/adgroup_add/campaign_remove/adgroup_remove/negative_add/negative_remove/creative_change/budget_change/extended_toggle
    before_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 변화 크기(입찰 Δ%·예산 Δ%·키워드 증감 등 — 예외 랭킹용)
    is_exception: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 예외 브리핑에 올릴 이상치 여부(SA-2 필터 판정)


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
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="excluded", index=True)  # excluded/probation/restored
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    excluded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # ★kst_now 주입(server_default 아님)
    last_transition_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 마지막 상태 전이 시각(KST)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)  # 재심사 개방 예정일(KST date)
    probation_until: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)  # probation 관찰창 종료일(KST date)
    cost_at_exclusion: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")  # 제외 시점 30d cost(감사)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())  # ⚠️UTC(server_default)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())  # ⚠️UTC


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
    promoted/rejected는 재수확 대상이 아니지만(판사가 이미 판정), hidden은 시그니처가 재등장하면
    pending으로 부활한다(Ebbinghaus 재노출 강화 — 연 1회 iphone_window 패턴이 해를 넘겨 누적됨).
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
