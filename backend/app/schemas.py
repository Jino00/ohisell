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
    conflict: bool  # 이 옵션ID를 나눠 가진 마스터들의 원가가 다름 = 이중귀속 위험
    shared: bool = False  # 나눠 가졌지만 원가가 같음 = 금액 영향 없음


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
    has_shared: bool = False


class ConnectionMapOut(BaseModel):
    channels: list[ConnChannel]
    rows: list[ConnRow]
    total_products: int
    shown_products: int
    conflict_option_count: int
    shared_option_count: int = 0


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
    # ★D-CPP-35 버퍼 차단. 이 둘이 여기 없으면 FastAPI가 응답에서 **조용히 지운다**
    #   (2026-08-10 cost_drift가 정확히 그렇게 사라졌다 — ref 54 §9 P1-1).
    cost_buffers: list[str] = []
    cost_guard_unavailable: str | None = None


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
    # ★`status='success'`인데 실제로는 덜 들어온 경우가 있다(부분수집, D-NAO-202). 그 사실은
    #   `sync_log.error_message`에만 있었고 이 스키마가 안 실어서 **화면이 초록만 봤다**.
    #   status와 함께 내보내야 «성공»의 진짜 의미가 화면에 닿는다.
    error_message: Optional[str] = None


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
    # ── D-22: 카드와 요약표가 **같은 모집단**을 말하게 하는 필드 ──
    # 종전엔 카드가 로켓1P를 통째로 안 태워서, 표를 고치면 카드와 값이 갈라졌다.
    net_scope: Optional[str] = None   # full | partial (하한이 섞였나)
    net_floor_ad: str = "0"           # 손익을 못 잰 채 광고비만 반영된 금액


class KpiEvidenceRow(BaseModel):
    """근거 페이지의 채널 1줄 — 카드 숫자를 이 행들이 합쳐 만든다.

    ★`deductions`의 값이 **None이면 「0원」이 아니라 「모른다」**이다(RG·로켓1P는 분해 항목을
      원래 다 갖고 있지 않다). 화면은 이 둘을 반드시 다르게 그려야 한다 — 0으로 그리면
      「원가 0원」이라는 거짓말이 되고, 그게 순이익을 부풀려 보이게 한 실제 결함 모양이다.
    """
    channel_id: Optional[int] = None
    channel_name: str = ""
    company: Optional[str] = None
    label: str = ""
    revenue: str
    product_revenue: Optional[str] = None
    shipping_revenue: Optional[str] = None
    deductions: dict[str, Optional[str]]
    missing: list[str] = []
    net_profit: Optional[str] = None
    net_scope: Optional[str] = None
    net_floor_ad: str = "0"
    net_basis_revenue: str = "0"
    unmapped_revenue: Optional[str] = None
    residual: Optional[str] = None
    explains_net: bool = False
    order_count: int = 0
    counted_in_order_card: bool = True
    revenue_basis: Optional[str] = None


class KpiEvidenceTotals(BaseModel):
    revenue: str
    net_profit: str
    basis_revenue: str
    floor_ad: str
    profit_rate: str
    order_count: int
    residual: str
    unmeasured_revenue: str


class KpiEvidenceChecks(BaseModel):
    revenue_matches: bool
    net_matches: bool
    order_count_matches: bool
    net_fully_explained: bool


class KpiEvidence(BaseModel):
    """`GET /dashboard/kpi/evidence` 응답 — KPI 카드 4칸의 근거 한 벌.

    ★여기 칸을 지우면 화면의 검산·자백이 **조용히** 사라진다(교훈 #321·#223: 서비스층은
      정직하게 계산했는데 `response_model`이 HTTP 경계에서 키를 지워 배너가 통째로 숨었다).
      필드를 빼기 전에 `frontend/src/pages/KpiEvidence.tsx`를 볼 것.
    """
    date_from: str
    date_to: str
    rocket_basis: str
    rows: list[KpiEvidenceRow]
    deduction_keys: list[str]
    deduction_totals: dict[str, str]
    deduction_unknown_rows: dict[str, int]
    totals: KpiEvidenceTotals
    checks: KpiEvidenceChecks
    order_count_excluded: int = 0
    has_floor: bool = False


class GroupedSummaryRow(BaseModel):
    kind: str  # total | company | leaf
    company: Optional[str]
    label: str
    revenue: str
    product_revenue: str = "0"
    shipping_revenue: str = "0"
    ad_spend: str
    net_profit: Optional[str]  # 잴 것이 아무것도 없을 때만 None
    profit_rate: Optional[str]
    order_count: int
    # ── 순이익이 무엇을 담고 있는지 (D-22, 2026-08-19) ──
    # ★이 세 필드를 여기서 빼면 화면의 경고가 통째로 사라진다(교훈 #321: response_model이
    #   서비스층에서 만든 키를 HTTP 경계에서 지운 사고). 필드를 지우기 전에 프론트를 볼 것.
    net_scope: Optional[str] = None        # full | ad_only | partial
    net_floor_ad: str = "0"                # 그중 「광고비만 반영된 하한」으로 들어간 광고비
    net_basis_revenue: str = "0"           # 이익률의 분모(= 손익을 실제로 잰 매출)
    unmapped_revenue: str = "0"            # 원가를 못 붙인 제품매출 — 이익률을 위로 부풀린다
    # ── 로켓배송 1P leaf에만 붙는다(다른 채널은 축이 하나뿐이라 None) ──
    revenue_basis: Optional[str] = None   # settlement(계산서) | sales(판매분석) | console_net(RG)
    cost_coverage: Optional[str] = None   # 0~1. 판매 축에서 원가가 붙은 매출의 비율
    promo_burden: Optional[str] = None    # 프로모션 분담금(판매 축에서만)
    # ── 로켓그로스(RG) leaf에만 붙는다 (D-CPP-47) ──
    # ★위 경고(교훈 #321)가 여기에도 그대로다. 이 네 필드는 RG 행이 **자기 신뢰도를 스스로 말하는**
    #   칸이라, 여기서 빠지면 서비스층이 정직하게 계산해도 화면엔 안 뜬다.
    #   실제로 D-CPP-47 초판이 이걸 빠뜨려 적대 리뷰 P1으로 잡혔다.
    #   `profit_calculator._LEAF_PASSTHROUGH`와 **짝**이다 — 한쪽만 고치면 여전히 안 나온다.
    option_axis_days: Optional[str] = None       # 옵션축이 창을 덮은 일수 "16/16"
    ad_unallocated: Optional[str] = None         # 카탈로그에 없는 옵션에 쓰인 광고비(행에 안 실린 돈)
    ad_unallocated_options: Optional[int] = None
    units_sold: Optional[int] = None             # 판매수량 — `order_count`(주문 건수)와 뜻이 다르다
    # ── RG 정산공제가 «어느 축이고 무엇을 근거로 하는가» (CONTRACT_rg_sales_date_axis §4 ⓒⓓⓔ) ──
    # ★바로 위 경고가 여기에도 그대로다. 이 칸들이 빠지면 서비스층이 실측 요율·커버리지·보존식
    #   차이를 정직하게 계산해도 **화면엔 실측과 「못 잼」이 같은 얼굴로 뜬다.**
    commission_axis: Optional[str] = None            # sales_date(판매일) — 종전은 정산 인식일 통짜
    commission_basis: Optional[str] = None           # settled_rate(실측) | rate_unknown(못 잼)
    commission_rate: Optional[str] = None            # 판매수수료 요율 %(VAT 포함)
    commission_rate_cycles: Optional[str] = None     # 그 요율을 잰 완결 정산주기 범위
    commission_logistics: Optional[str] = None       # 수량×단가(입출고)
    commission_sale_fee: Optional[str] = None        # 매출×요율(판매수수료)
    commission_period: Optional[str] = None          # 보관비·반품비 — 판매일에 안 붙는 기간비용
    fee_coverage: Optional[str] = None               # 0~1. 물류비 «단가»를 아는 매출의 비율
    fee_unmapped_revenue: Optional[str] = None       # 단가를 몰라 물류비를 0으로 «안 채운» 매출
    settlement_reconcile_cycle: Optional[str] = None    # 보존식을 잰 완결 주기
    settlement_reconcile_computed: Optional[str] = None
    settlement_reconcile_actual: Optional[str] = None   # 같은 주기의 원장 실청구액
    settlement_reconcile_diff: Optional[str] = None     # ★숨겨 0으로 만들지 않는다
    settlement_reconcile_pct: Optional[str] = None


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
    # 원가 정본 드리프트 — `product_master.cost_price`가 «원가표 정본 + 알려진 버퍼»인 건수.
    # 정상이면 None. ★★이 줄이 없으면 서비스층이 판정을 내도 **response_model이 응답에서
    #   지워버린다** — 2026-08-10 적대 리뷰 P1-1이 정확히 그 상태를 잡았다. 배선을 만들면서
    #   HTTP 경계에서 스스로 끊어 놓았고, 프론트는 키가 없으니 조용히 «이상 없음»으로 보였다.
    #   그래서 이 필드는 **TestClient로 실제 호출해 단언하는 테스트**와 짝이다
    #   (`test_cost_drift_wiring.py::test_health_route_actually_returns_cost_drift`).
    #   dict로 두는 이유: 배너가 쓰는 모양이 바뀌어도 스키마를 따라 고칠 필요가 없고,
    #   구조를 못 박는 일은 프론트 타입과 배선 테스트가 한다.
    cost_drift: dict | None = None
    # 쿠팡 판매분석 보존식(Σ옵션 GMV == 요약축 GMV) 대조 결과. 정상이면 mismatch=[], 대조 자체를
    # 못 했으면 None. ★위 cost_drift 주석의 사고를 그대로 되풀이하지 않으려고 **먼저** 넣었다 —
    # 서비스층이 판정을 내도 이 줄이 없으면 response_model이 응답에서 지운다. 짝이 되는 테스트는
    # `test_vendor_item_axis.py::test_health_route_actually_returns_conservation`.
    vendor_item_conservation: dict | None = None
    # 조치 생존 — 우리가 건 검색어 제외가 라이브에 아직 걸려 있나(D-NAO-173 P1-①). 정상이면
    # healthy=true·breached=[], 대조 자체를 못 했으면 None. ★위 두 필드와 같은 이유로 여기
    # 선언이 필수다(선언 없으면 서비스층 판정을 response_model이 지운다). 짝이 되는 테스트는
    # `test_exclusion_survival.py::test_health_route_actually_returns_exclusion_survival`.
    exclusion_survival: dict | None = None
    # 광고비 괴리 — 쿠팡이 정산에서 뗀 광고비 ↔ 우리가 손익에서 뺀 광고비(D-CPP-46).
    # `verdict`가 ok/diverged/pipe_stopped/insufficient_data. 대조 자체를 못 했으면 None.
    # ★위 세 필드와 **같은 이유로** 이 줄이 필수다: 선언이 없으면 서비스층이 판정을 내도
    #   response_model이 HTTP 응답에서 조용히 지운다(2026-08-10 적대 리뷰 P1-1이 잡은 사고,
    #   그때는 서비스층 dict까지만 보는 배선 테스트 6건이 전부 살아 있었다). 짝이 되는 테스트는
    #   `test_ad_cost_divergence.py::test_health_route_actually_returns_ad_cost_divergence`.
    ad_cost_divergence: dict | None = None
    # 부분수집 — 주문 수집이 status='success'로 끝났는데 실제로는 덜 들어온 상태(D-NAO-202/204).
    # 이상 없으면 [], 조회 자체를 못 했으면 None.
    # ★★위 네 필드와 **같은 이유로** 이 줄이 필수다 — 그리고 나는 이 주석 네 개를 다 읽고도
    #   같은 실패를 했다(2026-08-19 적대 리뷰 P1): 서비스층은 판정을 냈는데 여기 선언이 없어
    #   response_model이 HTTP 응답에서 `partial_sync`를 지웠고, 프론트는 `?? []`로 받아
    #   조용해졌다. 부분수집이 유일한 이상이면 배너가 **통째로 숨는다** — 이 필드가 막으려던
    #   사고(2026-08-18 주문 23건·356,100원이 success인 채 사라짐)를 한 층 아래에서 재현한 것이다.
    #   짝이 되는 테스트는 `test_health_partial_sync.py::test_health_route_actually_returns_partial_sync`
    #   (**TestClient로 실제 호출**한다 — 서비스층 dict만 보는 테스트로는 원리적으로 못 잡는다).
    partial_sync: list[dict] | None = None
    as_of: str


# ── RG(로켓그로스) 상품(옵션) 단위 일별 손익 — GET /api/coupang/rg/option-pnl
#    (D-CPP-54, CONTRACT_2p_own_screens §1-A-4). 이 라우터는 `rg_option_pnl()`의 반환을
#    그대로 옮겨 담는다 — 계산은 안 한다(계약 §3 금지선).
#
# ★★교훈 #319·#321·#223의 재발 지점: `response_model`이 선언 안 된 키를 응답에서 조용히
#   지운다. 아래는 서비스층 반환의 **모든 키**를 빠짐없이 선언한다 — 짝이 되는 테스트
#   `test_rg_pnl_http.py`가 하나라도 빠지면 죽도록 만든다.
class RgOptionPnlRow(BaseModel):
    vendor_item_id: str
    name: Optional[str] = None
    revenue: Decimal
    units_sold: int
    order_count: int
    fee_logistics: Optional[Decimal] = None
    fee_sale_fee: Optional[Decimal] = None
    fee_total: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    has_cost: bool
    ad_spend: Decimal
    net_profit: Optional[Decimal] = None


class RgAccountCommon(BaseModel):
    period_fees: Decimal
    payable_vat: Optional[Decimal] = None
    revenue_axis_gap: Decimal
    ad_unallocated: Decimal
    ad_unallocated_options: int
    fee_axis_fallback_gap: Decimal
    cost_unmapped_revenue: Decimal
    fee_unmapped_revenue: Decimal


class RgConservation(BaseModel):
    options_net_sum: Optional[Decimal] = None
    account_common_sum: Optional[Decimal] = None
    computed_total_net: Optional[Decimal] = None
    reference_net: Optional[Decimal] = None
    diff: Optional[Decimal] = None
    ok: Optional[bool] = None


class RgReconciliation(BaseModel):
    """`rg_sales_date_fees._reconcile`의 원시 반환 — 여기서는 str화하지 않고 그대로 받는다
    (`rg_option_pnl`이 `fees["reconciliation"]`을 그대로 넘기기 때문. `rg_channel_pnl`처럼
    `_reconcile_fields`로 접두사·str화하지 않은 것이 이 모듈의 실제 동작이다 — 스키마가
    그 동작을 고치지 않고 그대로 옮긴다, 계약 §3 「기존 파일 동작 변경 금지」와 같은 결)."""
    cycle_from: str
    cycle_to: str
    computed: Decimal
    actual: Decimal
    diff: Decimal
    diff_pct: Optional[Decimal] = None


class RgOptionPnlResponse(BaseModel):
    options: list[RgOptionPnlRow]
    account_common: RgAccountCommon
    commission_axis: str
    rate: Optional[Decimal] = None
    rate_basis: Optional[str] = None
    rate_cycles: Optional[str] = None
    fee_coverage: Optional[Decimal] = None
    cost_coverage: Optional[Decimal] = None
    option_axis_days: str
    option_axis_complete: bool
    cost_trustworthy: bool
    fee_trustworthy: bool
    reconciliation: Optional[RgReconciliation] = None
    conservation: RgConservation
    # ── HTTP 경계에서 추가한 메타(서비스층 계산이 아니라 요청 에코 + vendor_id 자백) ──
    account: str
    date_from: str
    date_to: str
    # vendor_id를 못 찾으면 광고비를 「0원」이 아니라 「모름」으로 자백한다(추정 금지 — 위 지시).
    ad_spend_warning: Optional[str] = None
