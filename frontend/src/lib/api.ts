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
