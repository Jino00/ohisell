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
  return res.json();
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
export interface OverviewResponse {
  period: { from: string; to: string };
  account: {
    summary: {
      revenue: string; return_deduction: string; service_fee: string;
      service_fee_vat: string; total_fee: string; ad_spend: string;
      cost: string; net_profit: string;
      cost_covered_options: number; option_count: number;
    };
    by_option: OverviewAccountRow[];
  };
  ad: {
    summary: {
      ad_spend: string; impressions: number; clicks: number;
      conv_revenue: string; roas: string | null;
    };
    by_option: OverviewAdRow[];
  };
  product: {
    summary: {
      option_count: number; order_count: number; order_qty: number; return_qty: number;
    };
    by_option: OverviewProductRow[];
  };
}

export async function fetchCommandCenter(
  from: string,
  to: string
): Promise<OverviewResponse> {
  return fetchApi<OverviewResponse>(
    `/api/overview/command-center?from=${from}&to=${to}`
  );
}

// ── 쿠팡 운영 패널 — 매출 현황 ───────────────────────────────────

export interface SalesSummaryData {
  revenue: string; fee: string; cost: string;
  ad_spend: string; shipping: string;
  profit: string; profit_rate: string | null;
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

// ── 네이버 운영 패널 — 매출 현황 ─────────────────────────────────

export interface NaverSalesSummaryData {
  revenue: string; fee: string; cost: string;
  ad_spend: string; shipping: string;
  profit: string; profit_rate: string | null;
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
  revenue: string; fee: string; cost: string;
  shipping: string;
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
