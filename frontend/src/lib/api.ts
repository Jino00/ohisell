// api.ts — Backend API 클라이언트
const API_BASE = "http://localhost:8000";

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
