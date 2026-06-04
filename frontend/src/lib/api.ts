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
