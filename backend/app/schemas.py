# schemas.py — Pydantic 스키마 (요청/응답 모델)
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ── Channel ──
class ChannelOut(BaseModel):
    id: int
    name: str
    code: str
    platform: str
    channel_type: str
    account_label: Optional[str] = None
    commission_rate: Decimal
    api_type: str

    model_config = {"from_attributes": True}


# ── ProductMaster ──
# ★cost_price는 음수일 수 없다(2026-08-06 적대 리뷰 P2) — 종전엔 스키마에도 DB에도 제약이
#   없어서 오입력 음수가 들어갈 수 있었고, 손익에서 원가가 음수면 이익을 **빼는 게 아니라
#   더한다.** 라이브 음수 0건이지만 방어선 자체가 없었다. 손익 쪽(naver_ops)은 양수 후보가
#   없으면 «모름»으로 처리해 이미 막았고, 여기서는 애초에 들어오지 못하게 한다.
class ProductCreate(BaseModel):
    internal_sku: str
    product_name: str
    cost_price: Decimal = Field(default=Decimal("0"), ge=0)
    category: Optional[str] = None
    memo: Optional[str] = None


class ProductUpdate(BaseModel):
    internal_sku: Optional[str] = None
    product_name: Optional[str] = None
    cost_price: Optional[Decimal] = Field(default=None, ge=0)
    category: Optional[str] = None
    memo: Optional[str] = None


class MappingCreate(BaseModel):
    channel_id: int
    channel_product_id: str
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Decimal = Decimal("0")


class MappingOut(BaseModel):
    id: int
    channel_id: int
    channel_name: Optional[str] = None
    channel_product_id: str
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Decimal
    is_active: bool
    mapping_source: str = "auto_sync"
    # 매핑을 새로 만들거나 고친 응답에만 채워진다 — 그 옵션ID의 **과거 미연결 주문 중 방금
    # 연결된 건수**(2026-08-04). 목록 조회 응답에는 없다(None). 화면이 "이었더니 과거 N건도
    # 원가가 붙었다"를 말할 수 있게 하는 값이라, 소급 연결의 사후 가시성 그 자체다.
    orders_linked: Optional[int] = None

    model_config = {"from_attributes": True}


# 단일 매핑 인라인 편집(상품 연관맵 트랙 S4, D-12) — 전부 optional(부분 갱신)
class MappingUpdate(BaseModel):
    channel_product_id: Optional[str] = None
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Optional[Decimal] = None
    is_active: Optional[bool] = None


# ── 상품 연결맵 매트릭스(내부옵션×채널, 트랙 S4 D-12) ──
class ConnCell(BaseModel):
    mapping_id: int
    channel_product_id: str
    channel_product_name: Optional[str] = None
    channel_sku: Optional[str] = None
    selling_price: Decimal
    is_active: bool
    mapping_source: str
    conflict: bool


class ConnChannel(BaseModel):
    channel_id: int
    channel_code: str
    channel_name: str
    platform: str
    sell_type: Optional[str] = None


class ConnRow(BaseModel):
    product_id: int
    internal_sku: str
    product_name: str
    cost_price: Decimal
    cells: dict[int, list[ConnCell]]  # channel_id → 셀 목록(JSON에선 키가 문자열)
    mapped_channel_count: int
    has_conflict: bool


class ConnectionMapOut(BaseModel):
    channels: list[ConnChannel]
    rows: list[ConnRow]
    total_products: int
    shown_products: int
    conflict_option_count: int


# ── 상품 연관맵 엑셀 마스터 적재 (상품 연관맵 트랙 S1) ──
class MappingIngestResult(BaseModel):
    products_created: int
    products_updated: int
    mappings_created: int
    mappings_updated: int
    mappings_conflicted: int
    orders_linked: int
    unknown_labels: list[str] = []
    duplicate_product_names: list[str] = []
    duplicate_channel_ids: list[str] = []
    mapping_conflicts: list[str] = []
    label_mismatches: list[str] = []


# ── 상품 연관맵 커버리지 리포트 (상품 연관맵 트랙 S2) ──
class UnmappedOption(BaseModel):
    option_id: str
    order_count: int


class ChannelCoverageOut(BaseModel):
    channel_id: int
    channel_code: str
    channel_name: str
    mapped_option_count: int
    order_option_count: int
    order_option_coverage: float
    unmapped_order_options: list[UnmappedOption]
    unmapped_order_options_truncated: int  # 응답에서 잘라낸 나머지 건수(0=전부 포함)
    total_orders: int
    unlinked_orders: int
    blank_option_id_orders: int  # 옵션ID 자체가 없는 주문(coverage=1.0이어도 문제 있을 수 있음)


class ProductOut(BaseModel):
    id: int
    internal_sku: str
    product_name: str
    cost_price: Decimal
    category: Optional[str] = None
    memo: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    mappings: list[MappingOut] = []

    model_config = {"from_attributes": True}


# ── Order ──
class OrderOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str = ""
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    order_number: str
    platform_product_id: str
    platform_product_name: Optional[str] = None
    quantity: int
    selling_price: Decimal
    shipping_cost: Optional[Decimal] = None  # 고객이 낸 배송비(의미 불변). None=배송비 포함 상품
    order_date: datetime
    status: str
    created_at: datetime
    # ── 배송 구분(Jino 지시 2026-07-28, 필드 추가 방식 — 기존 응답 형태 유지) ──
    # NULL = 판별 불가(네이버 주문 아님·raw_data 부재·JSON 잘림). 추정값 아님.
    delivery_attribute_type: Optional[str] = None   # ARRIVAL_GUARANTEE(N배송)/TODAY/NORMAL
    delivery_policy_type: Optional[str] = None      # 유료/무료/조건부무료
    shipping_fee_type: Optional[str] = None         # 선결제/무료 …
    logistics_company_id: Optional[str] = None      # N배송 물류사(PG 등)
    is_nbaesong: bool = False                       # 단일 판별자 결과
    shipping_cost_paid: Optional[Decimal] = None    # 우리가 지불한 배송비(건별 스냅샷)
    shipping_cost_net: Optional[Decimal] = None     # 실부담 = paid − COALESCE(수취,0) (파생값)

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    items: list[OrderOut]
    total: int
    page: int
    page_size: int


# ── 배송 구분 집계 (Jino 지시 2026-07-28) ──
class DeliveryBreakdownRow(BaseModel):
    """배송방식 × 배송비 부담 교차표 한 칸.

    두 축은 독립이다 — N배송이라고 항상 유료가 아니고, 일반배송에도 수취가 있다."""
    delivery_attribute_type: Optional[str] = None  # None = 판별 불가(백필 못 한 행)
    is_nbaesong: bool = False
    customer_paid: bool = False        # 고객이 배송비를 냈는가(shipping_cost > 0)
    orders: int = 0
    collected_total: Decimal = Decimal("0")   # 고객 수취 합
    paid_total: Decimal = Decimal("0")        # 우리 지불 합(판별된 행만)
    net_total: Decimal = Decimal("0")         # 실부담 합(clamp 없음 — 음수 가능)
    unresolved_paid: int = 0                  # 지불 배송비 미판별 건수(paid/net에서 제외됨)


class DeliveryBreakdownResponse(BaseModel):
    channel_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    rows: list[DeliveryBreakdownRow] = []
    total_orders: int = 0
    total_collected: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")
    unresolved_orders: int = 0  # delivery_attribute_type IS NULL 건수


# ── Sync ──
class SyncRequest(BaseModel):
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None


class SyncResult(BaseModel):
    channel_id: int
    channel_name: str = ""
    status: str
    new_orders: int = 0
    updated_orders: int = 0
    errors: list[str] = []


class SyncStatusOut(BaseModel):
    channel_id: int
    channel_name: str
    last_sync: Optional[datetime] = None
    status: Optional[str] = None
    records_synced: int = 0


# ── Ad Cost ──
class AdSpendDaily(BaseModel):
    date: str
    total_spend: int
    total_revenue_14d: Optional[int] = None


class AdSpendByOption(BaseModel):
    option_id: str
    product_name: Optional[str] = None
    total_spend: int
    total_revenue_14d: Optional[int] = None
    impressions: int = 0
    clicks: int = 0


# ── Profit Summary ──
class ProfitSummary(BaseModel):
    total_revenue: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    total_ad_spend: Decimal = Decimal("0")
    total_shipping: Decimal = Decimal("0")
    total_vat: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    order_count: int = 0


# ── Dashboard ──
class TrendPoint(BaseModel):
    date: str
    revenue: str
    product_revenue: str = "0"  # 제품매출 (selling_price × qty 합)
    shipping_revenue: str = "0"  # 배송비매출 (고객결제 배송비)
    cost: str
    commission: str
    ad_spend: str
    fixed_cost: str = "0"  # 월 고정비 일할 배분분(3PL 입고·보관·항공도선·합포장)
    unmapped_revenue: str = "0"  # 원가를 못 붙인 제품매출(표시 전용 — 순이익에는 영향 없음)
    shipping: str
    vat: str
    net_profit: str
    order_count: int


class DashboardKPI(BaseModel):
    total_revenue: str
    net_profit: str
    profit_rate: str
    order_count: int
    revenue_change_pct: Optional[float] = None
    profit_change_pct: Optional[float] = None


class GroupedSummaryRow(BaseModel):
    kind: str  # total | company | leaf
    company: Optional[str]
    label: str
    revenue: str
    product_revenue: str = "0"
    shipping_revenue: str = "0"
    ad_spend: str
    net_profit: Optional[str]  # 위탁(로켓배송) leaf/회사는 None
    profit_rate: Optional[str]
    order_count: int
    # ── 로켓배송 1P leaf에만 붙는다(다른 채널은 축이 하나뿐이라 None) ──
    revenue_basis: Optional[str] = None   # settlement(계산서) | sales(판매분석)
    cost_coverage: Optional[str] = None   # 0~1. 판매 축에서 원가가 붙은 매출의 비율
    promo_burden: Optional[str] = None    # 프로모션 분담금(판매 축에서만)


class GroupedTrendPoint(BaseModel):
    group: str
    company: Optional[str]
    date: str
    revenue: str
    product_revenue: str = "0"
    shipping_revenue: str = "0"
    ad_spend: str
    net_profit: Optional[str]


class ProductProfitRow(BaseModel):
    product_id: int
    product_name: str
    internal_sku: str
    revenue: str
    product_revenue: str = "0"
    shipping_revenue: str = "0"
    cost: str
    commission: str
    ad_spend: str
    shipping: str
    net_profit: str
    profit_rate: str
    quantity: int


# ── Settlement ──
class SettlementOut(BaseModel):
    id: int
    channel_id: int
    channel_name: str = ""
    settlement_date: str
    settlement_period_start: Optional[str] = None
    settlement_period_end: Optional[str] = None
    total_amount: str  # 정산총액 (제품정산 + 배송정산)
    product_amount: str = "0"  # 제품정산 = total_amount - shipping_fee
    commission: str
    net_amount: str
    shipping_fee: str  # 배송정산
    order_count: Optional[int] = None
    source: str
    memo: Optional[str] = None

    model_config = {"from_attributes": True}


class SettlementListResponse(BaseModel):
    items: list[SettlementOut]
    total: int
    page: int
    page_size: int


class SettlementUploadResult(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


class SettlementSummary(BaseModel):
    total_amount: str
    total_commission: str
    total_net: str
    total_shipping_fee: str
    count: int


# ── OAuth ──
class OAuthAuthUrl(BaseModel):
    auth_url: str
    mall_id: str


class OAuthStatus(BaseModel):
    status: str  # connected / expired / not_connected
    mall_id: Optional[str] = None
    expires_at: Optional[str] = None
    refresh_token_expires_at: Optional[str] = None
    message: Optional[str] = None


# ── Scheduler ──
class SchedulerJobOut(BaseModel):
    id: str
    name: str
    next_run_time: Optional[str] = None
    is_enabled: bool


class SchedulerStatusOut(BaseModel):
    is_running: bool
    jobs: list[SchedulerJobOut]


# ── Scheduler watchdog health (S5b S4) ──
class SchedulerJobVerdictOut(BaseModel):
    job_name: str
    state: str  # ok|disabled|failed|never_succeeded|stale
    age_sec: Optional[float] = None
    reason: str
    # 실패 잡 한 줄 요약(예외 클래스+메시지). 전체 traceback은 DB에만(누출 방지).
    error_summary: Optional[str] = None


class SchedulerCookieVerdictOut(BaseModel):
    account_key: str
    state: str  # stale
    age_days: Optional[float] = None
    status: Optional[str] = None  # green|red|unknown
    reason: str


class SchedulerDataVerdictOut(BaseModel):
    # 데이터 나이 감시(잡·쿠키 보고가 거짓말해도 최신 데이터 나이는 거짓말 못 함).
    name: str
    account_key: str
    state: str  # no_data | stale
    age_days: Optional[float] = None  # no_data면 None
    max_age_days: float
    impact: str  # 돈 영향 한글 라벨
    reason: str


class SchedulerDiskVerdictOut(BaseModel):
    # 디스크 여유 감시(2026-08-03 ENOSPC 사고). 잡·쿠키·데이터 나이가 전부 '이미 죽은 뒤'를
    # 보는 사후 지표인 반면, 이건 포화 전에 뜨는 유일한 사전 신호다.
    path: str
    state: str  # low
    used_percent: float
    warn_percent: float
    free_bytes: int
    total_bytes: int
    impact: str  # 돈/운영 영향 한글 라벨
    reason: str


class SchedulerHealthOut(BaseModel):
    healthy: bool
    scheduler_running: bool
    missing_jobs: list[str]
    failed: list[SchedulerJobVerdictOut]
    stale: list[SchedulerJobVerdictOut]
    never_succeeded: list[SchedulerJobVerdictOut]
    disabled: list[SchedulerJobVerdictOut]
    # fail-soft 잡(RG 정산·광고)의 쿠키 만료 직접 감시 — 며칠째 성공 못 한 쿠키.
    cookies_stale: list[SchedulerCookieVerdictOut] = []
    # 데이터 나이 감시 — 최신 row가 max_age_days를 넘겼거나 아예 없는(no_data) 파이프라인.
    data_stale: list[SchedulerDataVerdictOut] = []
    # 디스크 여유 감시 — 사용률이 warn_percent를 넘긴 마운트(포화 전 사전 경보).
    disk_low: list[SchedulerDiskVerdictOut] = []
    as_of: str
