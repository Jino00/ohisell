# models.py — SQLAlchemy 모델 (ohisell 전체 테이블)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, DateTime, Date, ForeignKey, Index, Integer, Numeric,
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
    # 대시보드 "광고비 갱신" 버튼이 set, Mac 페처 데몬이 claim(=None)으로 소비.
    # 이 값이 있으면 다음 폴링에서 페처가 headful fetch를 1회 수행한다(버튼 트리거 방식).
    refresh_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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
    conv_direct_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접전환 수
    conv_indirect_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접전환 수
    conv_direct_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 직접전환 매출(원)
    conv_indirect_amt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 간접전환 매출(원)
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
    calculated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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


class NaverProposal(Base):
    """제안 1건 (D-NAO 계획서 §2). 진단→제안 카드→Slack→(승인)→change_log.

    type 예: negative_keyword/bid_up/bid_down/budget/new_setup. status:
    pending/approved/rejected/expired. P0에서는 스키마만(생성은 P2).
    """

    __tablename__ = "naver_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    proposal_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # campaign/adgroup/keyword
    target_id: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    campaign_id: Mapped[str] = mapped_column(String(50), nullable=False, default="", index=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 무엇을/왜 3근거
    expected_effect: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 예상효과
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    slack_ts: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    executed_change_log_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


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

    scope: campaign/keyword_type/global. metric 예: proposal_accuracy/estimate_bias/
    conv_delay/discovery_winrate/hour_weight/bep_accuracy. P0에서는 스키마만(환류는 P3/P5).
    학습 경계: 파라미터만 조정, 가드레일 상수·권한 단계는 학습 대상 아님.
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
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
