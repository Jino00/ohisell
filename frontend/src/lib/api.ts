// api.ts — Backend API 클라이언트
const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

export async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  // 204 No Content(예: DELETE) 또는 빈 본문은 res.json()이 던지므로 undefined로 처리.
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function syncRealtime(): Promise<Record<string, unknown>> {
  return fetchApi("/api/sync/realtime", { method: "POST" });
}

// ── 쿠팡 광고비 쿠키 상태 (전역 만료 경고용) ──
export interface AdCostCookieStatus {
  account: string;
  configured: boolean;
  status: string; // green | red | unknown | none
  last_saved_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  age_hours: number | null; // 마지막 push 이후 경과(로컬 페처 heartbeat)
  stale: boolean;           // push 끊김(페처 다운) — 배너 트리거
}

export function getAdCostCookieStatus(): Promise<AdCostCookieStatus> {
  return fetchApi<AdCostCookieStatus>("/api/coupang/ops/ad-cost/cookie/status");
}

// ── 쿠팡 광고비 "버튼 트리거" 갱신 (Akamai로 prod 직접 fetch 불가 → Jino Mac 페처가 가져옴) ──
// 버튼 클릭 → request-refresh로 요청 플래그 set → Mac 데몬이 감지·fetch·push →
// refresh-status의 last_success_at가 올라가면 갱신 완료.
export interface AdCostRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string; // green | red | unknown | none
  last_error: string | null;
}

export function requestAdCostRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/ad-cost/request-refresh", { method: "POST" });
}

export function getAdCostRefreshStatus(): Promise<AdCostRefreshStatus> {
  return fetchApi<AdCostRefreshStatus>("/api/coupang/ops/ad-cost/refresh-status");
}

// ── 쿠팡 매출 정합성 자동 대조 (Wing 세션 자동화 트랙 S2/S3) ──
// 우리 매출(revenue_3p/rg) vs 쿠팡 공식 GMV(판매분석 vendor-summary)의 닫힌일 드리프트%.
// 읽기전용(net_profit 불변). Decimal은 백엔드에서 문자열, GMV/일수는 숫자, pct는 official 0이면 null.
export interface RevenueReconcile {
  period: { from: string; to: string; closed_through: string | null; account?: string };
  has_closed_days: boolean;
  has_official: boolean;
  coverage: { expected_days: number; days_with_data: number; complete: boolean } | null;
  official: {
    gmv_3p: number;
    gmv_rg: number;
    gmv_total: number;
    days_with_data: number;
    last_refresh: string | null;
  } | null;
  ours: { revenue_3p: string; revenue_rg: string; revenue_total: string } | null;
  drift: {
    abs_3p: string; abs_rg: string; abs_total: string;
    pct_3p: string | null; pct_rg: string | null; pct_total: string | null;
  } | null;
  note: string;
}

// account "ALL"(또는 생략)이면 파라미터 미전달(전체 합산·참고치). reconcile API는 COUPANG_WING1/2만 허용.
export function fetchRevenueReconcile(
  from: string,
  to: string,
  account?: string,
): Promise<RevenueReconcile> {
  const params = new URLSearchParams({ from, to });
  if (account && account !== "ALL") params.set("account", account);
  return fetchApi<RevenueReconcile>(`/api/overview/revenue-reconcile?${params.toString()}`);
}

// ── Wing 판매분석(vendor-summary) "갱신 버튼" — 광고비 버튼과 동일 패턴 ──
// 클릭 → request-refresh 플래그 set → Mac Wing 데몬(com.ohisell.wing)이 fetch·push →
// refresh-status의 last_success_at가 올라가면 갱신 완료.
export interface WingVendorSummaryRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string; // green | red | unknown | none
  last_error: string | null;
}

export function requestWingVendorSummaryRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/wing/vendor-summary/request-refresh", { method: "POST" });
}

export function getWingVendorSummaryRefreshStatus(): Promise<WingVendorSummaryRefreshStatus> {
  return fetchApi<WingVendorSummaryRefreshStatus>(
    "/api/coupang/ops/wing/vendor-summary/refresh-status",
  );
}

// ── Wing RG 정산 "갱신 버튼" — vendor-summary 갱신 버튼과 동일 패턴 ──
export interface WingRgSettlementRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string; // green | red | unknown | none
  last_error: string | null;
}

export function requestWingRgSettlementRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/wing/rg-settlement/request-refresh", { method: "POST" });
}

export function getWingRgSettlementRefreshStatus(): Promise<WingRgSettlementRefreshStatus> {
  return fetchApi<WingRgSettlementRefreshStatus>(
    "/api/coupang/ops/wing/rg-settlement/refresh-status",
  );
}

// 일별 광고비(coupang_ad_cost_daily, Mac 페처가 채움) 날짜 범위 조회.
export function getCoupangAdCostDaily(
  start: string,
  end: string,
): Promise<{ costs: { date: string; day_cost: number }[] }> {
  return fetchApi(`/api/coupang/ops/ad-cost?start=${start}&end=${end}`);
}

export async function uploadFile(path: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Upload error ${res.status}: ${detail}`);
  }
  return res.json();
}

export function downloadUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// ── Types ──
export interface Channel {
  id: number;
  name: string;
  code: string;
  platform: string;
  channel_type: string;
  account_label: string | null;
  commission_rate: number;
  api_type: string;
}

export interface Mapping {
  id: number;
  channel_id: number;
  channel_name: string | null;
  channel_product_id: string;
  channel_product_name: string | null;
  channel_sku: string | null;
  selling_price: number;
  is_active: boolean;
}

export interface Product {
  id: number;
  internal_sku: string;
  product_name: string;
  cost_price: number;
  category: string | null;
  memo: string | null;
  created_at: string;
  updated_at: string;
  mappings: Mapping[];
}

// ── 상품 연결맵 (트랙 S4, D-12) ──
export interface ConnCell {
  mapping_id: number;
  channel_product_id: string;
  channel_product_name: string | null;
  channel_sku: string | null;
  selling_price: number;
  is_active: boolean;
  mapping_source: string; // excel_master | manual | auto_sync
  conflict: boolean;
}
export interface ConnChannel {
  channel_id: number;
  channel_code: string;
  channel_name: string;
  platform: string;
  sell_type: string | null; // 3P | RG | 1P | null
}
export interface ConnRow {
  product_id: number;
  internal_sku: string;
  product_name: string;
  cost_price: number;
  cells: Record<string, ConnCell[]>; // channel_id(문자열) → 셀 목록
  mapped_channel_count: number;
  has_conflict: boolean;
}
export interface ConnectionMap {
  channels: ConnChannel[];
  rows: ConnRow[];
  total_products: number;
  shown_products: number;
  conflict_option_count: number;
}

export function fetchConnectionMap(q?: string, limit?: number): Promise<ConnectionMap> {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (limit != null) params.set("limit", String(limit));
  const qs = params.toString();
  return fetchApi<ConnectionMap>(`/api/products/connection-map${qs ? `?${qs}` : ""}`);
}

// 단일 매핑 인라인 편집(부분 갱신). 옵션ID 충돌 시 409 → Error throw.
export interface MappingPatch {
  channel_product_id?: string;
  channel_product_name?: string | null;
  channel_sku?: string | null;
  selling_price?: number;
  is_active?: boolean;
}
export function updateMapping(
  productId: number,
  mappingId: number,
  patch: MappingPatch,
): Promise<Mapping> {
  return fetchApi<Mapping>(`/api/products/${productId}/mappings/${mappingId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// ── 매핑 커버리지(트랙 S2) — 연결맵 화면 상단 배지 ──
export interface ChannelCoverage {
  channel_id: number;
  channel_code: string;
  channel_name: string;
  mapped_option_count: number;
  order_option_count: number;
  order_option_coverage: number; // 0~1
  unmapped_order_options: { option_id: string; order_count: number }[];
  unmapped_order_options_truncated: number;
  total_orders: number;
  unlinked_orders: number;
  blank_option_id_orders: number;
}
export function fetchMappingCoverage(limit = 50): Promise<ChannelCoverage[]> {
  return fetchApi<ChannelCoverage[]>(`/api/products/mapping-coverage?limit=${limit}`);
}

// 연결맵 마스터 엑셀 업로드 결과(무결성 배지 포함)
export interface MappingIngestResult {
  products_created: number;
  products_updated: number;
  mappings_created: number;
  mappings_updated: number;
  mappings_conflicted: number;
  orders_linked: number;
  unknown_labels: string[];
  duplicate_product_names: string[];
  duplicate_channel_ids: string[];
  mapping_conflicts: string[];
  label_mismatches: string[];
}

// ── 통합 손익 대조원장 (트랙 S5, GET /api/products/pnl-reconciliation) ──
// 금액 필드는 백엔드가 Decimal→str로 직렬화(_pnl_jsonify)하므로 전부 string.
export interface PnlComponent {
  channel: string;
  component: string;
  authoritative_total: string;
  allocated_to_sku: string;
  allocated_by_sku: Record<string, string>;
  residuals: Record<string, string>;
  conservation_diff: string;
  conservation_ok: boolean;
  date_basis: string;
}
export interface PnlLedgerWarning {
  [key: string]: unknown;
}
export interface PnlSkuRow {
  internal_sku: string;
  channels: Record<string, Record<string, string>>;
  net_profit_allocated_only: string;
}
export interface PnlReconciliation {
  period: { from: string; to: string; account?: string };
  ledger: {
    components: PnlComponent[];
    conservation_ok: boolean;
    sku_conflicts: string[];
    warnings: PnlLedgerWarning[];
  };
  by_sku: PnlSkuRow[];
  summary: {
    reconciled_net_profit: string;
    net_profit_allocated_total: string;
    account_adjustment_residual: string;
    trustworthy: boolean;
  };
}
export function fetchPnlReconciliation(
  from: string,
  to: string,
  account?: string,
): Promise<PnlReconciliation> {
  const params = new URLSearchParams({ from, to });
  if (account) params.set("account", account);
  return fetchApi<PnlReconciliation>(`/api/products/pnl-reconciliation?${params.toString()}`);
}

// ── Order Types ──
export interface OrderItem {
  id: number;
  channel_id: number;
  channel_name: string;
  product_id: number | null;
  product_name: string | null;
  order_number: string;
  platform_product_id: string;
  platform_product_name: string | null;
  quantity: number;
  selling_price: number;
  shipping_cost: number | null;
  order_date: string;
  status: string;
  created_at: string;
}

export interface OrderListResponse {
  items: OrderItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SyncResult {
  channel_id: number;
  channel_name: string;
  status: string;
  new_orders: number;
  updated_orders: number;
  errors: string[];
}

export interface SyncStatus {
  channel_id: number;
  channel_name: string;
  last_sync: string | null;
  status: string | null;
  records_synced: number;
}

export interface ProfitSummary {
  total_revenue: number;
  total_cost: number;
  total_commission: number;
  total_ad_spend: number;
  total_shipping: number;
  total_vat: number;
  net_profit: number;
  order_count: number;
}

// ── Dashboard Types (Sprint 3) ──
export interface TrendItem extends Record<string, unknown> {
  date: string;
  revenue: number;
  product_revenue?: number;  // 제품매출
  shipping_revenue?: number; // 배송비매출
  cost: number;
  commission: number;
  ad_spend: number;
  shipping: number;
  vat: number;
  net_profit: number;
  order_count: number;
}

export interface KpiData extends Record<string, unknown> {
  total_revenue: number;
  net_profit: number;
  profit_rate: number;
  order_count: number;
  revenue_change_pct: number;
  profit_change_pct: number;
}

// 회사 > leaf 계층 그룹 요약 (kind: total | company | leaf)
export interface GroupedSummaryRow extends Record<string, unknown> {
  kind: string;
  company: string | null;
  label: string;
  revenue: number;
  product_revenue?: number;
  shipping_revenue?: number;
  ad_spend: number;
  net_profit: number | null;  // null = 위탁(로켓배송) leaf/회사
  profit_rate: number | null;
  order_count: number;
}

export interface GroupedTrendPoint extends Record<string, unknown> {
  group: string;
  company: string | null;
  date: string;
  revenue: number;
  product_revenue?: number;
  shipping_revenue?: number;
  ad_spend: number;
  net_profit: number | null;  // null = 위탁(로켓배송) 그룹
}

export interface ProductRanking extends Record<string, unknown> {
  product_id: number;
  product_name: string;
  internal_sku: string;
  revenue: number;
  product_revenue?: number;
  shipping_revenue?: number;
  cost: number;
  commission: number;
  ad_spend: number;
  shipping: number;
  net_profit: number;
  profit_rate: number;
  quantity: number;
}

// ── Settlement Types (Sprint 3) ──
export interface SettlementItem {
  id: number;
  channel_id: number;
  channel_name: string;
  settlement_date: string;
  total_amount: number;
  product_amount?: number;  // 제품정산 (= total_amount - shipping_fee)
  commission: number;
  net_amount: number;
  shipping_fee: number;
  memo: string | null;
  created_at: string;
}

export interface SettlementListResponse {
  items: SettlementItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SettlementSummary {
  total_amount: number;
  total_commission: number;
  total_net: number;
  total_shipping_fee: number;
  count: number;
}

export interface UploadResult {
  imported: number;
  skipped: number;
  errors: string[];
}

// ── Scheduler Types (Sprint 3) ──
export interface SchedulerJob {
  id: string;
  name: string;
  next_run_time: string | null;
  is_enabled: boolean;
}

export interface SchedulerStatus {
  is_running: boolean;
  jobs: SchedulerJob[];
}

// ── 쿠팡 광고 리포트 ──
export interface CoupangAdReportRow {
  sell_type: string;
  impressions: number;
  clicks: number;
  ad_spend: number;
  orders: number;
  sales_qty: number;
  conversion_revenue: number;
  ctr: number;
  cvr: number;
  roas: number;
}

export interface CoupangAdReportResponse {
  date_from: string;
  date_to: string;
  total: CoupangAdReportRow;
  items: CoupangAdReportRow[];
}

export async function fetchCoupangAdReport(dateFrom: string, dateTo: string): Promise<CoupangAdReportResponse> {
  return fetchApi<CoupangAdReportResponse>(
    `/api/ads/coupang/report?date_from=${dateFrom}&date_to=${dateTo}`
  );
}

export async function uploadCoupangAdReport(file: File) {
  return uploadFile("/api/ads/coupang/upload", file);
}

// ──────────────────────────────────────────────
// 종합 조망 (Command Center) — P7. 금액은 백엔드가 문자열(Decimal)로 직렬화.
// ──────────────────────────────────────────────
export interface OverviewAccountRow {
  vendor_item_id: string;
  name: string;
  revenue: string;
  return_deduction: string;
  service_fee: string;
  service_fee_vat: string;
  total_fee: string;
  ad_spend: string;
  cost: string;
  has_cost: boolean;
  net_profit: string;
}
export interface OverviewAdRow {
  vendor_item_id: string;
  name: string;
  ad_spend: string;
  impressions: number;
  clicks: number;
  conv_revenue: string;
  roas: string | null;
  ctr: string | null;
}
export interface OverviewProductRow {
  vendor_item_id: string;
  name: string;
  order_count: number;
  order_qty: number;
  return_qty: number;
  return_rate: string | null;
  stock: number | null;
  on_sale: boolean | null;
  status_name: string | null;
  sale_price: string;
  in_master: boolean;
}
export interface RgSettlementByAccount {
  account_key: string;
  total: string;
  sale_fee: string;
  fulfillment: string;   // 풀필먼트(J) = 배송+입출고+보관 (D-10 라이브 검증)
  delivery: string;      // 풀필먼트 세부 — 배송비
  warehousing: string;   // 풀필먼트 세부 — 입출고비
  storage: string;       // 풀필먼트 세부 — 보관비
  return_fee: string;
  ad_sales: string;      // D-11 광고비(d), 표시만(중복주의)
  other: string;         // reconcile 잔액(정상=0, legacy/미지 fee_type)
}

export interface OverviewResponse {
  period: { from: string; to: string };
  account: {
    summary: {
      revenue: string; return_deduction: string; service_fee: string;
      service_fee_vat: string; total_fee: string; ad_spend: string;
      cost: string; net_profit: string;
      cost_covered_options: number; option_count: number;
      // S3/S7(정합성 트랙): 매출 분해 — 쿠팡 판매분석 수동 대조용. revenue = revenue_3p + revenue_rg.
      revenue_3p?: string;            // 마켓플레이스(Wing) 3P 매출
      revenue_rg?: string;            // 로켓그로스 매출(gross·취소 미차감, D-11)
      net_profit_basis?: string;      // 순이익 날짜축 설명(D-9 투명화)
      // S7(D-14/D-16): RG 정산 비용 net_profit 플립 브리지 필드(계정 단위, 전액 차감)
      net_profit_pre_rg?: string;     // 플립 전 순이익
      rg_settlement_total?: string;   // ★net_profit에서 실제 차감된 RG 총액(VAT後, 광고 포함)
      rg_ad_settlement?: string;      // 표시: 전액 중 광고분(D-16 라이브 조사)
      rg_non_ad_deducted?: string;    // 표시: 전액 중 광고 제외 브레이크다운
      rg_flip_status?: 'applied_full' | 'not_applied_no_data';
      ad_nonpa_deducted?: string;     // S5a/D-15: 비-PA(전체−집행) net_profit 추가 차감분
    };
    by_option: OverviewAccountRow[];
  };
  ad: {
    summary: {
      ad_spend: string; impressions: number; clicks: number;
      conv_revenue: string; roas: string | null;
      // S5a/D-15: report/SALES vendor-level 권위값(쿠팡 광고센터 0.02% 일치). ad_spend는 옵션 rollup.
      ad_confirmed_pa?: string;       // 집행(DELIVERED, 상품검색광고/PA)
      ad_confirmed_total?: string;    // 전체(ALL_DELIVERED, 비-PA 포함)
      ad_confirmed_nonpa?: string;    // 비-PA(전체−집행) = net_profit 추가 차감
      ad_basis?: string;
    };
    by_option: OverviewAdRow[];
  };
  product: {
    summary: {
      option_count: number; order_count: number; order_qty: number; return_qty: number;
    };
    by_option: OverviewProductRow[];
  };
  rg_settlement?: {
    summary: {
      total: string; has_data: boolean; note: string;
      flip_status?: 'applied_full' | 'not_applied_no_data';  // S7 플립 상태(D-16)
      deducted?: string;             // ★S7 net_profit에서 실제 차감된 RG 총액(광고 포함)
      non_ad_deducted?: string;      // 표시: 전액 중 광고 제외 브레이크다운
      ad_settlement?: string;        // D-16 RG정산 광고비(전액 중 광고분)
      ad_xlsx_rg_overlap?: string;   // 광고비 XLSX RG(2P)분(현재 0, 미래 겹침 감시용)
    };
    by_account: RgSettlementByAccount[];
  };
  // S2(트랙 revenue-wing-truth, D-1/D-9 A안): 닫힌 과거일 정본 매출(Wing GMV) 오버레이.
  // 읽기전용 — account.summary.revenue·net_profit 불변. 닫힌일=Wing 정본, 당일=주문기반.
  revenue_canonical?: {
    applicable: boolean;          // 윈도우에 닫힌 과거일 존재
    closed_through: string | null;
    coverage: { expected_days: number; days_with_data: number; complete: boolean; last_refresh: string | null } | null;
    summary: {
      canonical_3p: string; canonical_rg: string; canonical_total: string;
      closed_3p: string; closed_rg: string; open_3p: string; open_rg: string;
      our_closed_3p: string; our_closed_rg: string;
      factor_3p: string; factor_rg: string;
      wing_used: boolean;          // true=Wing GMV로 실제 정본화(complete). false=주문기반 폴백
      apportion_residual: string;  // 옵션 귀속 잔차(정상=0)
    };
    by_option: Record<string, string>;
    note: string;
  };
}

export async function fetchCommandCenter(
  from: string,
  to: string,
  account?: string  // COUPANG_WING1|COUPANG_WING2 — 생략/"ALL"=전체(S1, 정합성 트랙)
): Promise<OverviewResponse> {
  const acc = account && account !== "ALL" ? `&account=${encodeURIComponent(account)}` : "";
  return fetchApi<OverviewResponse>(
    `/api/overview/command-center?from=${from}&to=${to}${acc}`
  );
}

// ── 쿠팡 운영 패널 — 매출 현황 ───────────────────────────────────

export interface SalesSummaryData {
  revenue: string; fee: string; cost: string;
  ad_spend: string; shipping: string;
  profit: string; profit_rate: string | null;
  profit_excl_ad?: string; profit_rate_excl_ad?: string | null;
  cost_coverage?: number; fee_actual_ratio?: number;
  ad_today?: string | null; ad_today_synced_at?: string | null;
  rg_fulfillment?: string;
  conv_revenue: string; roas: string | null;
}

export interface SalesSummary {
  period: { from: string; to: string };
  ad_ref_date: string | null;
  summary: SalesSummaryData;
  by_product: SalesProductRow[];
}

export interface SalesProductRow {
  product_name: string;
  option_name: string;
  channel_type: string;
  revenue: string; fee: string; cost: string;
  ad_spend: string; shipping: string;
  profit: string; profit_rate: string | null;
  conv_revenue: string; roas: string | null;
}

export function fetchSalesSummary(company: string, days: number): Promise<SalesSummary> {
  return fetchApi<SalesSummary>(
    `/api/coupang/ops/sales-summary?company=${encodeURIComponent(company)}&days=${days}`
  );
}

// ── 쿠팡 RG 발송관제 ──────────────────────────────────────────────

export interface ReplenishmentItem {
  vendor_item_id: string;
  product_name?: string | null;
  item_name: string;
  status: "reorder_now" | "ok" | "well_stocked" | "insufficient_data";
  confidence?: "ok" | "low";
  reason?: string;
  current_stock?: number;
  in_transit_qty?: number;          // 발송중(아직 판매개시 안 된 파이프라인 물량, D-13)
  in_transit_fresh?: boolean;       // 발송중 수치가 신선한 쿠키 기반인지(stale면 0 취급)
  effective_stock?: number;         // 유효재고 = 현재고 + 발송중 (추천 역산 기준)
  expected_stowing_at?: string | null;  // 발송중 물량 판매개시 예정일
  daily_base_rate?: number;
  lead_p90?: number;
  days_to_safety?: number;
  ship_by_date?: string | null;
  recommend_qty?: number;
}

export interface ReplenishmentSummary {
  total: number;
  reorder_now: number;
  ok: number;
  well_stocked: number;
  insufficient_data: number;
  low_confidence: number;
}

export interface ReplenishmentInTransitMeta {
  fresh?: boolean;                  // 발송중 데이터가 신선한 Wing 쿠키 기반인지(만료 시 false)
  last_fetch_at?: string | null;    // 마지막 입고 sync 성공 시각
  total_in_transit_qty?: number;    // 전체 발송중 합계
}

export interface ReplenishmentPlan {
  generated_at: string;
  account_key: string | null;
  target_days: number;
  trust_days: number;
  in_transit_meta?: ReplenishmentInTransitMeta;
  summary: ReplenishmentSummary;
  items: ReplenishmentItem[];
}

export function fetchReplenishmentPlan(company = "ALL", targetDays = 7): Promise<ReplenishmentPlan> {
  // company가 ALL이면 전체 계정, 아니면 그 회사의 RG 재고 계정으로 백엔드가 필터.
  const companyParam = company && company !== "ALL" ? `&company=${encodeURIComponent(company)}` : "";
  return fetchApi<ReplenishmentPlan>(
    `/api/coupang/ops/replenishment-plan?target_days=${targetDays}${companyParam}`
  );
}

// ── 쿠팡 운영 패널 — 상품 목록·쓰기 ─────────────────────────────

export interface ProductItem {
  vendor_item_id: string;
  item_name: string;
  seller_product_name: string;
  account_key: string;
  sale_price: string | null;
  stock: number | null;
  on_sale: boolean | null;
  status_name: string | null;
}

export type OpsResult = Record<string, unknown>;

export function fetchProductItems(): Promise<ProductItem[]> {
  return fetchApi<ProductItem[]>("/api/coupang/ops/products/items");
}

function opsGet(path: string, params: Record<string, string | number | boolean | undefined>): Promise<OpsResult> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) q.set(k, String(v));
  }
  return fetchApi<OpsResult>(`/api/coupang/ops${path}?${q.toString()}`, { method: "PUT" });
}

export function opsUpdateQuantity(vid: number, quantity: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  return opsGet(`/products/items/${vid}/quantity`, { quantity, account_key: accountKey, dry_run: dryRun, confirm });
}

export function opsUpdatePrice(vid: number, price: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  return opsGet(`/products/items/${vid}/price`, { price, account_key: accountKey, dry_run: dryRun, confirm });
}

export function opsUpdateBasePrice(vid: number, originalPrice: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  return opsGet(`/products/items/${vid}/base-price`, { original_price: originalPrice, account_key: accountKey, dry_run: dryRun, confirm });
}

export function opsResumeSale(vid: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  return opsGet(`/products/items/${vid}/sale/resume`, { account_key: accountKey, dry_run: dryRun, confirm });
}

export function opsStopSale(vid: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  return opsGet(`/products/items/${vid}/sale/stop`, { account_key: accountKey, dry_run: dryRun, confirm });
}

export function opsExpireInstantCoupon(couponId: number, accountKey: string, dryRun: boolean, confirm?: string): Promise<OpsResult> {
  const q = new URLSearchParams({ account_key: accountKey, dry_run: String(dryRun) });
  if (confirm) q.set("confirm", confirm);
  return fetchApi<OpsResult>(`/api/coupang/ops/coupons/instant/${couponId}/expire?${q.toString()}`, { method: "PUT" });
}

// ──────────────────────────────────────────────
// 로켓배송(1P) — 돈 축 종합조망 블록 (트랙 rocket-1p S4/S4.5)
// ──────────────────────────────────────────────
export interface RocketCostCoverage {
  has_cost: boolean;
  cost: string;                       // Decimal → string
  coverage_pct: number;               // resolved / window_total (0~1)
  detail_order_amount: string;        // 발주상세 수집된 금액
  unmapped_order_amount: string;      // 발주상세 있으나 매핑 無 금액
  confirmed_sku_count: number;
  ignored_sku_count: number;
  unmapped_sku_count: number;
  pos_with_detail_count: number;
  pos_without_detail_count: number;   // 발주상세 미수집 PO 수
  note: string;
}

export interface RocketOverview {
  period: { from: string; to: string; vendor_id?: string };
  channel: string;                    // "COUPANG_ROCKET"
  revenue: string;                    // 발주 gross (Decimal → string)
  receiving_amount: string;           // 납품 gross
  order_qty: number;
  po_count: number;
  no_date_po_count: number;
  ad_spend: string;
  cost: string;
  has_cost: boolean;
  net_profit: string;
  net_profit_basis: string;
  cost_coverage: RocketCostCoverage;
  drift: {
    settled_amount: string;           // Decimal → string (발주↔정산 대조)
    drift_amount: string;
    drift_pct: string | null;
    settlement_invoice_count: number;
    note: string;
  };
}

export function fetchRocketOverview(from: string, to: string): Promise<RocketOverview> {
  return fetchApi<RocketOverview>(`/api/overview/rocket-overview?from=${from}&to=${to}`);
}

// ── 로켓배송(1P) 원가 매핑 (S4.5b) ──
export interface RocketUnmappedItem {
  product_number: string;
  product_name: string | null;
  barcode: string | null;
  total_order_qty: number;
  po_count: number;
  suggestions: { internal_sku: string; score: number; product_name: string; cost_price: number | null }[];
}

export interface RocketMappingItem {
  product_number: string;
  internal_sku: string;
  status: "confirmed" | "ignored";
  match_method: string;
  product_name: string | null;
  barcode: string | null;
  note: string | null;
  cost_price: number | null;
  created_at: string;
  updated_at: string;
}

export function fetchRocketCostMapUnmapped(
  limit = 200, suggest = true,
): Promise<RocketUnmappedItem[]> {
  return fetchApi<RocketUnmappedItem[]>(
    `/api/coupang/ops/rocket/cost-map/unmapped?limit=${limit}&suggest=${suggest}`,
  );
}

export function fetchRocketCostMap(): Promise<RocketMappingItem[]> {
  return fetchApi<RocketMappingItem[]>("/api/coupang/ops/rocket/cost-map");
}

export function upsertRocketCostMap(body: {
  product_number: string;
  internal_sku?: string;
  status?: "confirmed" | "ignored";
  match_method?: string;
  note?: string;
}): Promise<RocketMappingItem> {
  return fetchApi<RocketMappingItem>("/api/coupang/ops/rocket/cost-map", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteRocketCostMap(productNumber: string): Promise<{ deleted: number }> {
  return fetchApi<{ deleted: number }>(
    `/api/coupang/ops/rocket/cost-map/${encodeURIComponent(productNumber)}`,
    { method: "DELETE" },
  );
}

// ── 로켓배송(1P) 갱신 버튼 (S5, Wing 패턴 복제) ──
export interface RocketRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string;
  last_error: string | null;
}

export function requestRocketRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/rocket/request-refresh", { method: "POST" });
}

export function getRocketRefreshStatus(): Promise<RocketRefreshStatus> {
  return fetchApi<RocketRefreshStatus>("/api/coupang/ops/rocket/refresh-status");
}

// ── 오하이테크(1P) 광고비 갱신 버튼 (S3, 트랙 D-11 — adcost/rocket 패턴) ──
// 광고비는 Akamai로 prod 직접 fetch 불가(D-4) → Jino Mac poll 데몬이 가져옴. 버튼 클릭 →
// request-refresh 플래그 set → 데몬이 claim·fetch·push → refresh-status.last_success_at 변화로 완료 감지.
export interface OhitechAdRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string;
  last_error: string | null;
}

export function requestOhitechAdRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/rocket/ad-cost/request-refresh", { method: "POST" });
}

export function getOhitechAdRefreshStatus(): Promise<OhitechAdRefreshStatus> {
  return fetchApi<OhitechAdRefreshStatus>("/api/coupang/ops/rocket/ad-cost/refresh-status");
}

// ── 네이버 운영 패널 — 매출 현황 ─────────────────────────────────

export interface NaverSalesSummaryData {
  revenue: string;            // 공급가 매출(상품+배송)
  revenue_vat_incl: string;   // VAT 포함(소비자 결제) 병기
  product_revenue: string; delivery_revenue: string;  // 공급가 상품매출 / 고객배송비 매출
  fee: string; cost: string;
  ad_spend: string;           // 광고비(이미 공급가)
  logistics: string;          // 한진 물류비(공급가)
  shipment_count: number;     // 물리배송 건수(packageNumber)
  profit: string; profit_rate: string | null;
  supply_basis?: boolean;     // 전 금액 공급가(VAT 제외) 기준
  sa_conv_revenue: string; sa_ad_spend: string; sa_roas: string | null;
  sa_conv_from: string | null; sa_conv_to: string | null;
  fee_settled_lines: number; fee_est_lines: number;  // 실측/예상 수수료 라인 수 (D-6)
}

export interface NaverSalesSummary {
  period: { from: string; to: string };
  ad_ref_date: string | null;
  summary: NaverSalesSummaryData;
  by_product: NaverSalesProductRow[];
}

export interface NaverSalesProductRow {
  product_name: string;
  platform_id: string;
  revenue: string; fee: string; cost: string;  // 공급가(VAT 제외) 기준
  fee_actual?: boolean;  // 수수료가 전부 정산 실측이면 true (D-6)
  profit: string; profit_rate: string | null;
}

export function fetchNaverSalesSummary(days: number): Promise<NaverSalesSummary> {
  return fetchApi<NaverSalesSummary>(`/api/naver/ops/sales-summary?days=${days}`);
}

// ── GFA(디스플레이) 광고비 현황·업로드 ───────────────────────────
export interface GfaStatus {
  has_data: boolean;
  date_from: string | null;
  date_to: string | null;
  days: number;
  total_spend: number;
}

export function fetchGfaStatus(): Promise<GfaStatus> {
  return fetchApi<GfaStatus>("/api/ad-costs/gfa/status");
}

export function uploadGfaCsv(file: File) {
  return uploadFile("/api/ad-costs/gfa/upload", file);
}

// ── 네이버 정산 (N1) ─────────────────────────────────────────────
export interface NaverSettlementRow {
  settle_expect_date: string;
  settle_basis_start: string | null;
  settle_basis_end: string | null;
  settle_complete_date: string | null;
  settle_amount: string; pay_settle_amount: string;
  commission_amount: string; benefit_amount: string; payholdback_amount: string;
  settle_method: string | null;
}
export interface NaverSettlement {
  period: { from: string; to: string };
  summary: {
    settle_amount: string; pay_settle_amount: string;
    commission_amount: string; benefit_amount: string; payholdback_amount: string;
  };
  rows: NaverSettlementRow[];
}
export function fetchNaverSettlement(days: number): Promise<NaverSettlement> {
  return fetchApi<NaverSettlement>(`/api/naver/ops/settlement?days=${days}`);
}
export function syncNaverSettlement(days: number): Promise<{ synced: number; date_from: string; date_to: string }> {
  return fetchApi(`/api/naver/ops/settlement/sync?days=${days}`, { method: "POST" });
}

export interface NaverInquiryRow {
  inquiry_no: number;
  category: string;
  title: string;
  inquiry_content: string;
  inquiry_date: string;
  answered: boolean;
  answer_content: string;
  answer_date: string;
  order_id: string;
  product_no: string;
  product_name: string;
  product_order_option: string;
  customer_name: string;
  customer_id: string;
}
export interface NaverInquiries {
  period: { from: string; to: string };
  total: number;
  unanswered: number;
  rows: NaverInquiryRow[];
}
export function fetchNaverInquiries(days: number, answered?: boolean): Promise<NaverInquiries> {
  const p = answered === undefined ? "" : `&answered=${answered}`;
  return fetchApi<NaverInquiries>(`/api/naver/ops/inquiries?days=${days}${p}`);
}

export interface NaverChannelProduct {
  channel_product_no: number;
  name: string;
  status_type: string;
  sale_price: number | null;
  discounted_price: number | null;
  stock_quantity: number | null;
  category: string;
  image_url: string;
  reg_date: string;
  modified_date: string;
}

export interface NaverProductItem {
  origin_product_no: number;
  group_product_no: number | null;
  channel_products: NaverChannelProduct[];
}

export interface NaverProductList {
  total_elements: number;
  total_pages: number;
  page: number;
  contents: NaverProductItem[];
}

export function fetchNaverProducts(status?: string, page = 1, size = 500): Promise<NaverProductList> {
  const p = status ? `&status=${status}` : "";
  return fetchApi<NaverProductList>(`/api/naver/ops/products?page=${page}&size=${size}${p}`);
}

export interface NaverSellerChannel {
  channel_no: number;
  channel_type: string;
  name: string;
  url: string;
  talktalk_id: string;
}

export interface NaverSellerInfo {
  account_id: string;
  account_uid: string;
  grade: string;
  channels: NaverSellerChannel[];
}

export function fetchNaverSellerInfo(): Promise<NaverSellerInfo> {
  return fetchApi<NaverSellerInfo>("/api/naver/ops/seller");
}

// ── N6. 발주/발송 처리 (쓰기 — dry_run+confirm) ──────────────────────
export interface NaverPendingOrder {
  product_order_id: string;
  order_id: string;
  product_name: string;
  quantity: number;
  orderer_name: string;
  receiver_name: string;
  shipping_due_date: string;
  expected_delivery_company: string;
  expected_delivery_method: string;
  package_number: string;
  shipping_memo: string;
  place_order_status: string;
  order_date: string;
}

export interface NaverPendingOrders {
  awaiting_place: NaverPendingOrder[];     // 발주확인 대기
  awaiting_dispatch: NaverPendingOrder[];  // 발송 대기
}

export function fetchNaverPendingOrders(days = 14): Promise<NaverPendingOrders> {
  return fetchApi<NaverPendingOrders>(`/api/naver/ops/orders/pending?days=${days}`);
}

// dry_run=true면 would_send만, false면 naver 응답
export interface NaverWriteResult {
  dry_run: boolean;
  action: "confirm" | "dispatch" | "delay";
  count?: number;
  would_send?: Record<string, unknown>;
  naver?: Record<string, unknown>;
}

export function naverConfirmOrders(productOrderIds: string[], dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/orders/confirm?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_ids: productOrderIds }),
  });
}

export interface NaverDispatchItem {
  product_order_id: string;
  delivery_method: string;
  delivery_company_code?: string;
  tracking_number?: string;
  dispatch_date?: string;
}

export function naverDispatchOrders(items: NaverDispatchItem[], dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/orders/dispatch?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export function naverDelayOrder(
  payload: {
    product_order_id: string;
    dispatch_due_date: string;
    delayed_dispatch_reason: string;
    dispatch_delayed_detailed_reason?: string;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/orders/delay?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// 택배사 코드 — 국내 주요 (전체는 API 문서, 그 외는 직접 입력)
export const NAVER_DELIVERY_COMPANIES: { code: string; name: string }[] = [
  { code: "CJGLS", name: "CJ대한통운" },
  { code: "HANJIN", name: "한진택배" },
  { code: "HYUNDAI", name: "롯데택배" },
  { code: "KGB", name: "로젠택배" },
  { code: "EPOST", name: "우체국택배" },
  { code: "KDEXP", name: "경동택배" },
  { code: "CVSNET", name: "GSPostbox택배" },
  { code: "CUPARCEL", name: "CU편의점택배" },
  { code: "DAESIN", name: "대신택배" },
  { code: "NONGHYUP", name: "농협택배" },
];

// 배송방법 코드 — DELIVERY만 택배사+송장 필수
export const NAVER_DELIVERY_METHODS: { code: string; name: string }[] = [
  { code: "DELIVERY", name: "택배·등기·소포" },
  { code: "DIRECT_DELIVERY", name: "직접 전달" },
  { code: "QUICK_SVC", name: "퀵서비스" },
  { code: "VISIT_RECEIPT", name: "방문 수령" },
  { code: "NOTHING", name: "배송 없음" },
];

export const NAVER_DELAY_REASONS: { code: string; name: string }[] = [
  { code: "PRODUCT_PREPARE", name: "상품 준비 중" },
  { code: "CUSTOMER_REQUEST", name: "고객 요청" },
  { code: "CUSTOM_BUILD", name: "주문 제작" },
  { code: "RESERVED_DISPATCH", name: "예약 발송" },
  { code: "OVERSEA_DELIVERY", name: "해외 배송" },
  { code: "ETC", name: "기타" },
];

// ── N7. 클레임 (취소/반품/교환) — wave 1 취소 ──────────────────────
export interface NaverClaim {
  product_order_id: string;
  order_id: string;
  claim_type: string;     // CANCEL / RETURN / EXCHANGE
  claim_status: string;   // CANCEL_REQUEST 등
  last_changed_type: string;
  product_order_status: string;
  product_name: string;
  quantity: number;
  orderer_name: string;
}

export interface NaverClaims {
  claims: NaverClaim[];
}

export function fetchNaverClaims(days = 14): Promise<NaverClaims> {
  return fetchApi<NaverClaims>(`/api/naver/ops/claims?days=${days}`);
}

export function naverApproveCancel(productOrderId: string, dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/cancel/approve?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId }),
  });
}

export function naverRequestCancel(
  payload: { product_order_id: string; cancel_reason: string; cancel_detailed_reason?: string; cancel_quantity?: number | null },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/cancel/request?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export const NAVER_CANCEL_REASONS: { code: string; name: string }[] = [
  { code: "INTENT_CHANGED", name: "구매 의사 취소" },
  { code: "COLOR_AND_SIZE", name: "색상 및 사이즈 변경" },
  { code: "WRONG_ORDER", name: "다른 상품 잘못 주문" },
  { code: "PRODUCT_UNSATISFIED", name: "서비스 불만족" },
  { code: "DELAYED_DELIVERY", name: "배송 지연" },
  { code: "SOLD_OUT", name: "상품 품절" },
  { code: "INCORRECT_INFO", name: "상품 정보 상이" },
];

// ── N7 wave 2 반품 (Return) ──────────────────────────────────────
// 반품 요청 사유 (requestReturnClaimReason enum, 실측)
export const NAVER_RETURN_REASONS: { code: string; name: string }[] = [
  { code: "INTENT_CHANGED", name: "구매 의사 취소" },
  { code: "COLOR_AND_SIZE", name: "색상 및 사이즈 변경" },
  { code: "WRONG_ORDER", name: "다른 상품 잘못 주문" },
  { code: "PRODUCT_UNSATISFIED", name: "서비스 불만족" },
  { code: "DELAYED_DELIVERY", name: "배송 지연" },
  { code: "SOLD_OUT", name: "상품 품절" },
  { code: "DROPPED_DELIVERY", name: "배송 누락" },
  { code: "BROKEN", name: "상품 파손" },
  { code: "INCORRECT_INFO", name: "상품 정보 상이" },
  { code: "WRONG_DELIVERY", name: "오배송" },
  { code: "WRONG_OPTION", name: "색상 등 다른 상품 잘못 배송" },
];

// 반품 보류 유형 (holdbackClassType enum, 실측 — ★EXTRAFEEE 철자 원문대로)
export const NAVER_RETURN_HOLDBACK_TYPES: { code: string; name: string }[] = [
  { code: "RETURN_DELIVERYFEE", name: "반품 배송비 청구" },
  { code: "EXTRAFEEE", name: "추가 비용 청구" },
  { code: "RETURN_DELIVERYFEE_AND_EXTRAFEEE", name: "반품 배송비 + 추가 비용 청구" },
  { code: "RETURN_PRODUCT_NOT_DELIVERED", name: "반품 상품 미입고" },
  { code: "ETC", name: "기타 사유" },
  { code: "SELLER_CONFIRM_NEED", name: "판매자 확인 필요" },
  { code: "PURCHASER_CONFIRM_NEED", name: "구매자 확인 필요" },
  { code: "SELLER_REMIT", name: "판매자 직접 송금" },
  { code: "ETC2", name: "기타" },
];

// 반품 수거 배송 방법 (deliveryMethod enum, 실측 — RETURN_* 포함)
export const NAVER_COLLECT_DELIVERY_METHODS: { code: string; name: string }[] = [
  { code: "DELIVERY", name: "택배·등기·소포" },
  { code: "RETURN_DELIVERY", name: "일반 반품 택배" },
  { code: "RETURN_DESIGNATED", name: "지정 반품 택배" },
  { code: "RETURN_INDIVIDUAL", name: "직접 반송" },
  { code: "VISIT_RECEIPT", name: "방문 수령" },
  { code: "DIRECT_DELIVERY", name: "직접 전달" },
  { code: "QUICK_SVC", name: "퀵서비스" },
  { code: "NOTHING", name: "배송 없음" },
];

export function naverApproveReturn(productOrderId: string, dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/return/approve?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId }),
  });
}

export function naverRejectReturn(
  productOrderId: string,
  rejectReturnReason: string,
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/return/reject?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId, reject_return_reason: rejectReturnReason }),
  });
}

export function naverHoldbackReturn(
  payload: {
    product_order_id: string;
    holdback_class_type: string;
    holdback_return_detail_reason: string;
    extra_return_fee_amount?: number | null;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/return/holdback?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function naverReleaseReturnHoldback(productOrderId: string, dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/return/holdback/release?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId }),
  });
}

export function naverRequestReturn(
  payload: {
    product_order_id: string;
    return_reason: string;
    collect_delivery_method: string;
    collect_delivery_company?: string;
    collect_tracking_number?: string;
    return_quantity?: number | null;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/return/request?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── N7 wave 3 교환 (Exchange) ────────────────────────────────────
// 교환 보류 유형은 반품과 동일(NAVER_RETURN_HOLDBACK_TYPES 재사용),
// 재배송 배송방법은 수거와 동일(NAVER_COLLECT_DELIVERY_METHODS 재사용).
export function naverApproveExchangeCollect(productOrderId: string, dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/exchange/collect/approve?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId }),
  });
}

export function naverDispatchExchange(
  payload: {
    product_order_id: string;
    re_delivery_method?: string;
    re_delivery_company?: string;
    re_delivery_tracking_number?: string;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/exchange/dispatch?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function naverHoldbackExchange(
  payload: {
    product_order_id: string;
    holdback_class_type: string;
    holdback_exchange_detail_reason: string;
    extra_exchange_fee_amount?: number | null;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/exchange/holdback?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function naverReleaseExchangeHoldback(productOrderId: string, dryRun = true): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/exchange/holdback/release?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId }),
  });
}

export function naverRejectExchange(
  productOrderId: string,
  rejectExchangeReason: string,
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/claims/exchange/reject?dry_run=${dryRun}`, {
    method: "POST",
    body: JSON.stringify({ product_order_id: productOrderId, reject_exchange_reason: rejectExchangeReason }),
  });
}

// ── N8 상품 판매상태 변경 (트랙 D-11) ────────────────────────────
// 판매중/품절/판매중지 3상태만. 가격(salePrice) 안 보냄 → 가격 손실 위험 0.
// 위험 상태(DELETE 등)는 타입으로 차단 (codex P2-3).
export type NaverProductStatus = "SALE" | "OUTOFSTOCK" | "SUSPENSION";

export const NAVER_PRODUCT_STATUS_OPTIONS: { code: NaverProductStatus; label: string }[] = [
  { code: "SALE", label: "판매중" },
  { code: "OUTOFSTOCK", label: "품절" },
  { code: "SUSPENSION", label: "판매중지" },
];

export function naverChangeProductStatus(
  payload: {
    origin_product_no: number;
    status_type: NaverProductStatus;
    stock_quantity?: number | null;
    sale_start_date?: string;
    sale_end_date?: string;
  },
  dryRun = true,
): Promise<NaverWriteResult> {
  return fetchApi<NaverWriteResult>(`/api/naver/ops/products/change-status?dry_run=${dryRun}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// claimStatus 코드 → 한글 라벨 (표시용, 네이버 실측 enum)
export const NAVER_CLAIM_STATUS_LABELS: Record<string, string> = {
  CANCEL_REQUEST: "취소 요청", CANCELING: "취소 처리중", CANCEL_DONE: "취소 완료", CANCEL_REJECT: "취소 철회",
  RETURN_REQUEST: "반품 요청", EXCHANGE_REQUEST: "교환 요청", COLLECTING: "수거 처리중", COLLECT_DONE: "수거 완료",
  EXCHANGE_REDELIVERING: "교환 재배송중", RETURN_DONE: "반품 완료", EXCHANGE_DONE: "교환 완료",
  RETURN_REJECT: "반품 철회", EXCHANGE_REJECT: "교환 철회",
  PURCHASE_DECISION_HOLDBACK: "구매확정 보류", PURCHASE_DECISION_REQUEST: "구매확정 요청",
  PURCHASE_DECISION_HOLDBACK_RELEASE: "구매확정 보류해제",
  ADMIN_CANCELING: "직권취소 처리중", ADMIN_CANCEL_DONE: "직권취소 완료", ADMIN_CANCEL_REJECT: "직권취소 철회",
};

// ── 네이버 SA 광고 리포트 (P1, track_naver-ad-optimization) ──
export interface NaverAdKpis {
  imp: number;
  clk: number;
  cost: number;
  rank_sum: number;
  conv_direct_cnt: number;
  conv_indirect_cnt: number;
  conv_direct_amt: number;
  conv_indirect_amt: number;
  conv_cnt: number;
  conv_amt: number;
  ctr: number | null;
  cpc: number | null;
  avg_rank: number | null;
  roas_naver: number | null;
  roas_direct: number | null;
}

export interface NaverAdRoas3Col {
  cost: number;
  naver: { revenue: number; roas: number | null };
  direct: { revenue: number; roas: number | null };
  actual_order: { revenue: number; roas: number | null; order_count: number; note: string };
}

export interface NaverAdTrendRow extends NaverAdKpis {
  ad_date: string;
}

export interface NaverAdDrilldownRow extends NaverAdKpis {
  ad_date?: string;
  campaign_id?: string;
  campaign_type?: string;
  adgroup_id?: string;
  keyword_id?: string;
}

export interface NaverAdHourlyRow {
  hour: number;
  cost: number;
  clk: number;
  imp: number;
}

export interface NaverAdCompare {
  period: { from: string; to: string };
  kpis: NaverAdKpis;
  roas_3col: NaverAdRoas3Col;
  deltas_pct: Record<string, number | null>;
}

export interface NaverAdReport {
  period: { from: string; to: string };
  grain: string;
  kpis: NaverAdKpis;
  roas_3col: NaverAdRoas3Col;
  trend: NaverAdTrendRow[];
  rows: NaverAdDrilldownRow[] | NaverAdHourlyRow[];
  hourly_meta?: { ad_date: string | null; total_cost: number; clamped: number };
  compare?: NaverAdCompare;
}

export type NaverAdGrain = "date" | "campaign" | "adgroup" | "keyword" | "hour";

export function fetchNaverAdReport(params: {
  dateFrom: string;
  dateTo: string;
  grain: NaverAdGrain;
  compareFrom?: string;
  compareTo?: string;
  campaignId?: string;
}): Promise<NaverAdReport> {
  const q = new URLSearchParams({
    date_from: params.dateFrom,
    date_to: params.dateTo,
    grain: params.grain,
  });
  if (params.compareFrom) q.set("compare_from", params.compareFrom);
  if (params.compareTo) q.set("compare_to", params.compareTo);
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  return fetchApi<NaverAdReport>(`/api/naver/ad/report?${q.toString()}`);
}

export interface NaverAdBepRow {
  channel_product_id: string;
  product_master_id: number | null;
  product_name: string | null;
  selling_price: number | null;
  cost_price: number | null;
  commission_rate: number | null;
  logistics_cost: number | null;
  contribution_margin: number | null;
  bep_roas: number | null;
  aggressiveness: string | null;
  target_roas: number | null;
  has_cost: boolean;
}

export interface NaverAdBepList {
  total: number;
  actionable: number;
  rows: NaverAdBepRow[];
}

export function fetchNaverAdBep(params?: {
  onlyActionable?: boolean;
  sort?: "bep_roas" | "target_roas" | "selling_price" | "contribution_margin";
  desc?: boolean;
  limit?: number;
}): Promise<NaverAdBepList> {
  const q = new URLSearchParams();
  if (params?.onlyActionable) q.set("only_actionable", "true");
  if (params?.sort) q.set("sort", params.sort);
  if (params?.desc) q.set("desc", "true");
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return fetchApi<NaverAdBepList>(`/api/naver/ad/bep${qs ? `?${qs}` : ""}`);
}

// ── 네이버 SA 광고 진단 보드 (P2-S2, track_naver-ad-optimization) ──
export interface NaverAdDiagnosisKeywordRow {
  campaign_id: string;
  adgroup_id: string;
  keyword_id: string;
  imp: number;
  clk: number;
  cost: number;
  conv_amt: number;
  roas_naver: number | null;
  roas_corrected: number | null;
  avg_daily_clk?: number;
}

export interface NaverAdDiagnosisExpansionBucket {
  cost: number;
  clk: number;
  imp: number;
  conv_amt: number;
  roas_naver: number | null;
  roas_corrected: number | null;
  web_site_total_cost: number;
  cost_share: number | null;
}

export interface NaverAdDiagnosisShoppingGroupRow {
  campaign_id: string;
  adgroup_id: string;
  cost: number;
  conv_amt: number;
  roas_naver: number | null;
  roas_corrected: number | null;
}

export interface NaverAdDiagnosisExclusionCandidateRow {
  campaign_id: string;
  adgroup_id: string;
  search_term: string;
  source: string;
  cost: number;
  clk: number;
  imp: number;
}

export interface NaverAdDiagnosisKeywordTriage {
  total: number;
  judgeable: number;
  growth_candidate: number;
  dead: number;
  volume_unchecked: number;
}

export interface NaverAdDiagnosisViciousCycleRow {
  campaign_id: string;
  recent_roas_corrected: number | null;
  prior_roas_corrected: number | null;
  recent_daily_clk: number;
  prior_daily_clk: number;
}

export interface NaverAdDiagnosisBoards {
  bleeding_keywords: NaverAdDiagnosisKeywordRow[];
  starving_winners: NaverAdDiagnosisKeywordRow[];
  expansion_bucket: NaverAdDiagnosisExpansionBucket;
  shopping_group_bep: NaverAdDiagnosisShoppingGroupRow[];
  exclusion_candidates: NaverAdDiagnosisExclusionCandidateRow[];
  keyword_triage: NaverAdDiagnosisKeywordTriage;
  vicious_cycle: NaverAdDiagnosisViciousCycleRow[];
}

export interface NaverAdDiagnosis {
  window: { date_from: string; date_to: string };
  correction_factor: {
    factor: number;
    source: string;
    window_from?: string;
    window_to?: string;
    window_revenue?: number;
    window_conv_amt?: number;
  };
  account_bep_roas: number | null;
  account_target_roas: number | null;
  error?: string;
  boards: NaverAdDiagnosisBoards | null;
}

export function fetchNaverAdDiagnosis(params?: {
  dateFrom?: string;
  dateTo?: string;
}): Promise<NaverAdDiagnosis> {
  const q = new URLSearchParams();
  if (params?.dateFrom) q.set("date_from", params.dateFrom);
  if (params?.dateTo) q.set("date_to", params.dateTo);
  const qs = q.toString();
  return fetchApi<NaverAdDiagnosis>(`/api/naver/ad/diagnosis${qs ? `?${qs}` : ""}`);
}

// ── 네이버 SA 광고 최적화 콘솔 (P2-S3b, track_naver-ad-optimization) ──
export type NaverExpertVerdict = "agree" | "partial" | "reject" | "insufficient_evidence" | "commentary";

export interface NaverExpertVerdictSummary {
  verdict: NaverExpertVerdict;
  confidence: number | null;
  as_of: string;
  run_id: number;
}

export interface NaverAdProposal {
  id: number;
  created_at: string | null;
  proposal_type: string;
  target_type: string;
  target_id: string;
  campaign_id: string;
  adgroup_id: string | null;
  rationale: string | null;
  expected_effect: string | null;
  status: string;
  slack_ts: string | null;
  executed_change_log_id: number | null;
  // D-NAO-47: 실행 목표값 — "입찰 인상" 카드가 *얼마로* 올리는지 화면에 없던 결함(스펙 §1-6).
  target_bid: number | null;
  target_lock: boolean | null;
  target_budget: number | null;
  budget_auto_eligible: boolean | null;
  /** 백엔드가 주는 정보성/실행형 구분. ★프론트에서 유형 문자열로 재분류하지 말 것 —
   *  백엔드에 유형이 추가되면 조용히 드리프트한다. */
  informational: boolean;
  expert_verdict: NaverExpertVerdictSummary | null;
  // X1a T4 — 콘솔 실행 버튼 활성화 여부(naver_execution_harness.real_write_blocker).
  executable: boolean;
  not_executable_reason: string | null;
  // X1a T5 — 승인 경로(콘솔 사람 승인 vs Ava 위임 자동승인, D-NAO-25). 승인 전(pending)이거나
  // 구버전 데이터는 null.
  approval_source: "console" | "delegation" | null;
}

export interface NaverAdProposalList {
  rows: NaverAdProposal[];
}

export function fetchNaverAdProposals(params?: {
  status?: string;
  dateFrom?: string;
  dateTo?: string;
  campaignId?: string;
  limit?: number;
}): Promise<NaverAdProposalList> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.dateFrom) q.set("date_from", params.dateFrom);
  if (params?.dateTo) q.set("date_to", params.dateTo);
  if (params?.campaignId) q.set("campaign_id", params.campaignId);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return fetchApi<NaverAdProposalList>(`/api/naver/ad/proposals${qs ? `?${qs}` : ""}`);
}

// X1a T4 — 콘솔 승인/반려 상태 전이.
export function updateNaverProposalStatus(
  id: number,
  status: "approved" | "rejected",
): Promise<NaverAdProposal> {
  return fetchApi<NaverAdProposal>(`/api/naver/ad/proposals/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

// X1a T4 — 콘솔 실행 버튼(실쓰기, naver_execution_harness.execute(dry_run=False)).
export interface NaverProposalExecuteResult {
  change_log_id: number;
  outcome: string;
  before: unknown;
  after: unknown;
  proposal: NaverAdProposal;
}

export function executeNaverProposal(id: number): Promise<NaverProposalExecuteResult> {
  return fetchApi<NaverProposalExecuteResult>(`/api/naver/ad/proposals/${id}/execute`, {
    method: "POST",
  });
}

// E1a T8 — 전문가(Ava) 검토 패널
export interface NaverExpertReview {
  id: number;
  run_id: number;
  as_of: string | null;
  proposal_id: number | null;
  verdict: NaverExpertVerdict;
  confidence: number | null;
  reasoning: string | null;
  checkable_prediction: string | null;
  pred_target_type: string | null;
  pred_target_id: string | null;
  pred_metric: string | null;
  pred_direction: string | null;
  verify_date: string | null;
  outcome: string | null;
  source: string;
}

export interface NaverExpertReviewList {
  rows: NaverExpertReview[];
}

export function fetchNaverExpertReviews(params?: {
  asOf?: string;
  proposalId?: number;
  limit?: number;
}): Promise<NaverExpertReviewList> {
  const q = new URLSearchParams();
  if (params?.asOf) q.set("as_of", params.asOf);
  if (params?.proposalId != null) q.set("proposal_id", String(params.proposalId));
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return fetchApi<NaverExpertReviewList>(`/api/naver/ad/expert-reviews${qs ? `?${qs}` : ""}`);
}

export interface NaverExpertScorecard {
  sample_n: number;
  accuracy: number | null;
  label: string | null;
}

export function fetchNaverExpertScorecard(): Promise<NaverExpertScorecard> {
  return fetchApi<NaverExpertScorecard>("/api/naver/ad/expert-scorecard");
}

export type NaverAdOptimizer = "none" | "ours" | "mop";
export type NaverAdCampaignMode = "growth" | "recovery" | "launch" | "defense";

export interface NaverAdCampaignSettings {
  campaign_id: string;
  optimizer: NaverAdOptimizer;
  mode: NaverAdCampaignMode | null;
  target_roas_override: number | null;
  memo: string | null;
  updated_at: string | null;
}

export interface NaverAdCampaignSettingsList {
  rows: NaverAdCampaignSettings[];
}

export function fetchNaverCampaignSettings(params?: {
  campaignId?: string;
}): Promise<NaverAdCampaignSettingsList> {
  const q = new URLSearchParams();
  if (params?.campaignId) q.set("campaign_id", params.campaignId);
  const qs = q.toString();
  return fetchApi<NaverAdCampaignSettingsList>(`/api/naver/ad/campaign-settings${qs ? `?${qs}` : ""}`);
}

export function putNaverCampaignSettings(body: {
  campaignId: string;
  optimizer: NaverAdOptimizer;
  mode?: NaverAdCampaignMode | null;
  targetRoasOverride?: number | null;
  memo?: string | null;
}): Promise<NaverAdCampaignSettings> {
  return fetchApi<NaverAdCampaignSettings>("/api/naver/ad/campaign-settings", {
    method: "PUT",
    body: JSON.stringify({
      campaign_id: body.campaignId,
      optimizer: body.optimizer,
      mode: body.mode ?? null,
      target_roas_override: body.targetRoasOverride ?? null,
      memo: body.memo ?? null,
    }),
  });
}

// X1a T5 — E2 위임 스위치(D-NAO-25): Ava가 agree 평결 + 가드레일을 통과한 제안 유형만
// 08:05 크론에서 사람 승인 없이 자동 승인·실행되도록 켜고 끈다. 스위치 행사자는 Jino뿐.
export interface NaverExpertDelegationSettings {
  delegated_types: string[];
  delegable_types: string[];
}

export function getNaverExpertDelegation(): Promise<NaverExpertDelegationSettings> {
  return fetchApi<NaverExpertDelegationSettings>("/api/naver/ad/settings/expert-delegation");
}

export function putNaverExpertDelegation(delegatedTypes: string[]): Promise<NaverExpertDelegationSettings> {
  return fetchApi<NaverExpertDelegationSettings>("/api/naver/ad/settings/expert-delegation", {
    method: "PUT",
    body: JSON.stringify({ delegated_types: delegatedTypes }),
  });
}

// 대시보드 미니 스프린트 T1/T2 — 엔진 파이프라인 5단계 라이브 증거 상태 + optimizer 커버리지
// (dashboard_overview.py 응답과 1:1 대응, PLAN_naver-ad-dashboard-mini.md §1 T1).
export interface NaverDashboardEngineStage {
  key: string;
  name: string;
  last_evidence_at: string | null;
  status: "ok" | "stale" | "none";
  detail: string;
}

export interface NaverDashboardOptimizerCoverage {
  window_days: number;
  ours_cost: number;
  mop_cost: number;
  none_cost: number;
  total_cost: number;
  ours_ratio: number;
}

export interface NaverDashboardOverview {
  engine_stages: NaverDashboardEngineStage[];
  optimizer_coverage: NaverDashboardOptimizerCoverage;
}

export function getNaverDashboardOverview(): Promise<NaverDashboardOverview> {
  return fetchApi<NaverDashboardOverview>("/api/naver/ad/dashboard-overview");
}

// ── D-NAO-47 커맨드 센터 API ──
// ★응답 키는 rows다(items 아님). 기존 /proposals·/bep·/expert-reviews와 같은 관례.

export interface NaverChangeLogRow {
  id: number;
  changed_at: string | null;
  entity_type: string;
  entity_id: string;
  campaign_id: string;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  rationale: string | null;
  outcome: string | null;
  dry_run: boolean;
  proposal_id: number | null;
  executed_at: string | null;
}

export interface NaverChangeLogResponse { total: number; rows: NaverChangeLogRow[] }

/** 변경 이력. ★include_dry_run 기본 false — "우리 조작 N회"는 실집행만 센다(D-47-h). */
export async function fetchNaverChangeLog(params: {
  campaign_id?: string; action?: string; days?: number;
  include_dry_run?: boolean; limit?: number; offset?: number;
} = {}): Promise<NaverChangeLogResponse> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/change-log?${q.toString()}`);
}

export interface NaverRawKeywordRow {
  entity_id: string; name: string; parent_id: string; campaign_id: string;
  campaign_type: string; status: string; bid_amt: number | null;
  monthly_volume: number | null; competition: string | null; synced_at: string | null;
}

export async function fetchNaverRawKeywords(params: {
  q?: string; campaign_id?: string; status?: string; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawKeywordRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/keywords?${s.toString()}`);
}

export interface NaverRawSearchTermRow {
  ad_date: string | null; campaign_id: string; adgroup_id: string;
  search_term: string; source: string; imp: number; clk: number; cost: number;
}

export async function fetchNaverRawSearchTerms(params: {
  q?: string; campaign_id?: string; days?: number; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawSearchTermRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/search-terms?${s.toString()}`);
}

export interface NaverRawHourlyRow {
  ad_date: string | null; snapshot_hour: number; snapshot_at: string | null;
  campaign_id: string; campaign_type: string; cost: number; clk: number; imp: number;
  daily_budget: number | null;
  /** ★예산이 없거나 0이면 null — "소진율 0%"가 아니라 "알 수 없음"이다. */
  spend_ratio: number | null;
}

export async function fetchNaverRawHourly(params: {
  campaign_id?: string; days?: number; limit?: number; offset?: number;
} = {}): Promise<{ total: number; rows: NaverRawHourlyRow[] }> {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) s.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/raw/hourly?${s.toString()}`);
}

// ── D-NAO-47 — 상설 소급 채점 성적표(D-NAO-45, /retro-scorecard) ──
// 실제 응답 형태는 backend/app/routers/naver_ad.py:707 retro_scorecard()를 grep해 대조함
// (계획서 초안엔 형태가 없었다 — "실제에 맞춰라" 지시에 따른 실측 배선).
export interface NaverRetroBoardRollup {
  n: number;
  correct: number;
  gray: number;
  wrong: number;
  no_spend: number;
  precision_spenders: number | null;
  bleed_sum: number;
}

export interface NaverRetroBoardHorizons {
  d3: NaverRetroBoardRollup;
  d7: NaverRetroBoardRollup;
}

/** pacing 롤업: kind("저속"/"과속"/"unparsed") → verdict("correct"/"partial"/"false_alarm"/"unparsed") → 건수. */
export type NaverRetroPacingRollup = Record<string, Record<string, number>>;

/** kind → verdict → 그 버킷의 **평균 최종 소진율**(0~1 분수). final_ratio가 전부 NULL이면 null.
 *  ★D-NAO-47에서 추가. "저속 correct 769건"은 '경보가 맞았다'까지고, **"평균 최종 소진율
 *  4.9%"라야 "하루가 끝나도 일예산의 4.9%만 썼다 = 만성 저소진이 실재한다"는 증거**가 된다
 *  — D-NAO-45 정정(trigger_pacing은 노이즈 아님 → 접지 말고 롤업)의 핵심 숫자. */
export type NaverRetroPacingRatioRollup = Record<string, Record<string, number | null>>;

export interface NaverRetroScorecard {
  window_days: number;
  boards: Record<string, NaverRetroBoardHorizons>;
  pacing: NaverRetroPacingRollup;
  pacing_final_ratio: NaverRetroPacingRatioRollup;
}

export function fetchNaverRetroScorecard(days?: number): Promise<NaverRetroScorecard> {
  const q = days != null ? `?days=${days}` : "";
  return fetchApi<NaverRetroScorecard>(`/api/naver/ad/retro-scorecard${q}`);
}
