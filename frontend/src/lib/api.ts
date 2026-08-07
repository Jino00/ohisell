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

/** 스케줄러 잡 즉시 실행. 「지금 기준」 버튼이 시간별 스냅샷 잡을 당겨 쓴다. */
export function triggerSchedulerJob(jobId: string): Promise<Record<string, unknown>> {
  return fetchApi(`/api/scheduler/trigger/${jobId}`, { method: "POST" });
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
  last_error_at: string | null; // 마지막 실패 시각(last_success_at의 짝) — 성공 시 클리어
  age_hours: number | null; // 마지막 push 이후 경과(로컬 페처 heartbeat)
  stale: boolean;           // push 끊김(페처 다운) — 배너 트리거
  refresh_cron_enabled: boolean | null; // 갱신 크론 on/off(null=행 없음) — false면 쿠키 재설정은 헛수고
}

export function getAdCostCookieStatus(): Promise<AdCostCookieStatus> {
  return fetchApi<AdCostCookieStatus>("/api/coupang/ops/ad-cost/cookie/status");
}

// ── 쿠팡 광고비 "버튼 트리거" 갱신 (Akamai로 prod 직접 fetch 불가 → Jino Mac 페처가 가져옴) ──
// 버튼 클릭 → request-refresh로 요청 플래그 set → Mac 데몬이 감지·fetch·push →
// refresh-status의 last_success_at가 올라가면 갱신 완료, last_error_at이 올라가면 갱신 실패.
export interface AdCostRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string; // green | red | unknown | none
  last_error: string | null;
  last_error_at: string | null; // 페처 실패 보고 시각 — 이게 변하면 실패(성공만 기다리면 215초 헛대기)
  // ── 갱신 요청 lease 계약(2026-07-27, PLAN_coupang-claim-retry-lease) ──
  // 버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청. requested=true인 동안은 아직
  // 끝나지 않은 것(재시도 대기 포함) — 실패 판정은 requested=false가 된 뒤에 한다.
  attempt_count: number;   // 이번 요청으로 시도한 횟수(0~3)
  max_attempts: number;    // 상한(3)
  claimed_at: string | null;
  in_flight: boolean;      // 지금 페처가 잡고 일하는 중(임대 유효)
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
// refresh-status의 last_success_at가 올라가면 갱신 완료, last_error_at이 올라가면 갱신 실패.
export interface WingVendorSummaryRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string; // green | red | unknown | none
  last_error: string | null;
  last_error_at: string | null; // 페처 실패 보고 시각 — 이게 변하면 실패(성공만 기다리면 215초 헛대기)
  // ── 갱신 요청 lease 계약(2026-07-27, PLAN_coupang-claim-retry-lease) ──
  // 버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청. requested=true인 동안은 아직
  // 끝나지 않은 것(재시도 대기 포함) — 실패 판정은 requested=false가 된 뒤에 한다.
  attempt_count: number;   // 이번 요청으로 시도한 횟수(0~3)
  max_attempts: number;    // 상한(3)
  claimed_at: string | null;
  in_flight: boolean;      // 지금 페처가 잡고 일하는 중(임대 유효)
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
  last_error_at: string | null; // 페처 실패 보고 시각 — 이게 변하면 실패(성공만 기다리면 215초 헛대기)
  // ── 갱신 요청 lease 계약(2026-07-27, PLAN_coupang-claim-retry-lease) ──
  // 버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청. requested=true인 동안은 아직
  // 끝나지 않은 것(재시도 대기 포함) — 실패 판정은 requested=false가 된 뒤에 한다.
  attempt_count: number;   // 이번 요청으로 시도한 횟수(0~3)
  max_attempts: number;    // 상한(3)
  claimed_at: string | null;
  in_flight: boolean;      // 지금 페처가 잡고 일하는 중(임대 유효)
}

// ★버튼 큐는 계정 차원(2026-07-27): WING1(오픽스)·WING2(오하이테크) 데몬이 각자 자기 큐만 본다.
// accountKey 생략 시 WING1 — 기존 호출부 하위호환.
export function requestWingRgSettlementRefresh(
  accountKey = "COUPANG_WING1",
): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi(
    `/api/coupang/ops/wing/rg-settlement/request-refresh?account_key=${encodeURIComponent(accountKey)}`,
    { method: "POST" },
  );
}

export function getWingRgSettlementRefreshStatus(
  accountKey = "COUPANG_WING1",
): Promise<WingRgSettlementRefreshStatus> {
  return fetchApi<WingRgSettlementRefreshStatus>(
    `/api/coupang/ops/wing/rg-settlement/refresh-status?account_key=${encodeURIComponent(accountKey)}`,
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
  // 매핑을 새로 잇거나 고친 응답에만 온다 — 그 옵션ID의 **과거 미연결 주문 중 방금 연결된
  // 건수**(2026-08-04). 목록 조회에는 없다(undefined). 이었는데 과거가 안 붙던 결함의 증거.
  orders_linked?: number | null;
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
  conflict: boolean; // 이 옵션ID를 나눠 가진 마스터들의 원가가 다름 = 이중귀속 위험
  shared: boolean; // 나눠 가졌지만 원가가 같음 = 금액 영향 없음(리스팅 공유)
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
  has_shared: boolean;
}
export interface ConnectionMap {
  channels: ConnChannel[];
  rows: ConnRow[];
  total_products: number;
  shown_products: number;
  conflict_option_count: number; // 원가가 갈리는 조합 = 진짜 위험
  shared_option_count: number; // 원가가 같아 금액 영향이 없는 공유 조합
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
  // ★net_profit_allocated_only는 cost_known=false면 **원가가 빠진 값**이라 과대다.
  //   금액 자체는 보존법칙(Σ귀속+잔차==총계) 때문에 그대로 두고, 화면이 「—」로 비운다.
  net_profit_allocated_only: string;
  cost_known?: boolean;
  cost_unknown_revenue?: string;   // 원가 미상 라인의 제품매출(표시 전용)
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
    // 원가 미상 — scoped=false면 «없음»이 아니라 «이 계정 조회에선 판정 안 함»이다(쿠팡 전용 조회).
    cost_unknown_skus?: number;
    cost_unknown_revenue?: string;
    cost_unknown_scoped?: boolean;
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
  fixed_cost?: number;        // 월 고정비 일할 배분분
  unmapped_revenue?: number;  // 원가를 못 붙인 제품매출(표시 전용 — 순이익엔 영향 없음)
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
  // ── 로켓배송 1P leaf에만 붙는다(다른 채널은 축이 하나뿐) ──
  revenue_basis?: string;   // "settlement"(계산서) | "sales"(판매분석)
  cost_coverage?: number;   // 0~1. 판매 축에서 원가가 붙은 매출의 비율
  promo_burden?: number | null;  // null = 원천 미배포(=모름). 0과 다르다
}

// 로켓배송 1P 매출 축 — 계산서(회계 정본) vs 판매분석(운영 지표). **택일**이다.
export type RocketBasis = "settlement" | "sales";

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

// ── 파이프라인 헬스 (GET /api/scheduler/health) — 전역 헬스 배너용 ──
// 잡·쿠키·데이터 나이 감시를 한 응답으로 통합. healthy:false면 Layout 배너가 표면화.
// (배경: 쿠키 만료를 정확히 보고했으나 상설 배너가 없어 RG 정산 26일 침묵 방치 — PLAN §2a)
export interface SchedulerHealthJob {
  job_name: string;
  state: string;
  age_sec?: number;
  reason?: string;
  error_summary?: string;
}
export interface SchedulerHealthCookieStale {
  account_key: string;
  state: string;
  age_days: number;
  status: string; // red | amber ...
  reason?: string;
}
// data_stale: 병렬 세션이 백엔드에 추가 중 — 구백엔드엔 없으므로 optional 필드로 소비.
export interface SchedulerHealthDataStale {
  name: string;
  account_key: string;
  state: "stale" | "no_data";
  age_days: number | null; // no_data면 null
  max_age_days?: number;
  impact: string;          // 돈 영향 한글 라벨 (그대로 노출)
  reason?: string;
}
export interface SchedulerHealth {
  healthy: boolean;
  scheduler_running: boolean;
  missing_jobs: string[];
  failed: SchedulerHealthJob[];
  stale: SchedulerHealthJob[];
  never_succeeded: SchedulerHealthJob[];
  disabled: SchedulerHealthJob[]; // 정상(의도적 비활성) — 문제로 세지 않음
  cookies_stale: SchedulerHealthCookieStale[];
  data_stale?: SchedulerHealthDataStale[]; // 구백엔드 안전을 위해 optional
  as_of: string;
}

export function getSchedulerHealth(): Promise<SchedulerHealth> {
  return fetchApi<SchedulerHealth>("/api/scheduler/health");
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
  /** ★이 행의 전환매출·ROAS가 무슨 축인가 — **행마다 다르다.**
   *  3P·2P는 우리가 소비자에게 직접 팔아 전환매출 = 우리 매출이지만,
   *  Retail(1P)은 쿠팡이 사입해 자기 가격으로 팔아 전환매출 = **쿠팡 매출**이다.
   *  그래서 두 행의 ROAS는 **비교 대상이 아니다** — 나란히 놓고 크기를 견주면 결론이 뒤집힌다. */
  roas_basis?: "our_revenue" | "consumer_price" | "mixed";
  roas_basis_label?: string;
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

// ── 쿠팡 RG 청구액 감사 (S8, D-17) — 읽기 전용 스크리닝 화면용 ───────
// ★프론트는 charged_*(전 주기 청구총액)를 order_count로 다시 나누지 않는다.
//   단가는 백엔드가 낸 per_unit_delivery/per_unit_warehousing을 그대로 쓴다
//   (그 나눗셈이 2026-08-03에 규명된 오탐 4건의 원인이었다 — rg_fee_audit.py 상단 주석).

export interface RgFeeAuditPeriodDetail {
  date_from: string;
  date_to: string;
  delivery: number | null;
  warehousing: number | null;
  order_count: number;
  quantity: number;
  judged: boolean;
  per_unit_delivery: number | null;
  per_unit_warehousing: number | null;
  implied_size_delivery: string | null;
  flags: string[];
}

export interface RgFeeAuditFloor {
  delivery?: number | null;
  warehousing?: number | null;
}

export interface RgFeeAuditItem {
  vendor_item_id: string;
  product_name: string | null;
  item_name: string;
  width_mm: number | null;
  length_mm: number | null;
  height_mm: number | null;
  weight_g: number | null;
  // 전 주기 청구총액(분자 아님 — judged_*가 단가의 분자).
  charged_delivery: number | null;
  charged_warehousing: number | null;
  // 판정에 실제로 쓰인 금액 합(단가의 분자).
  judged_delivery: number | null;
  judged_warehousing: number | null;
  size_type: string | null;
  size_source: "settlement_billed" | "coupang_measured" | "registered_dims" | null;
  billed_size_type: string | null;
  measured_size_type: string | null;
  divisor_source: string | null; // "settlement" | "order_table" | "order_table+settlement"
  billed_vs_measured_size_diff: boolean;
  per_unit_delivery: number | null;
  per_unit_warehousing: number | null;
  floor: RgFeeAuditFloor | null;
  implied_size_delivery: string | null;
  quantity: number | null;
  order_count: number | null;
  periods_total: number;
  periods_judged: number;
  periods_unmatched: number;
  periods_flagged: number;
  period_detail: RgFeeAuditPeriodDetail[];
  flags: string[];
}

export interface RgFeeAuditSummary {
  total_options: number;
  flagged: number;
  size_mismatch_high: number;
  billed_size_vs_amount_mismatch: number;
  measured_vs_billed_mismatch: number;
  billed_vs_measured_size_diff: number;
  divisor_from_settlement: number;
  below_floor: number;
  missing_dims: number;
  unit_unknown: number;
  oversize: number;
  coverage_partial: number;
  coverage_none: number;
  clean_but_period_outlier: number;
}

export interface RgFeeAudit {
  generated_at: string;
  account_key: string | null;
  date_from: string | null;
  date_to: string | null;
  summary: RgFeeAuditSummary;
  items: RgFeeAuditItem[];
  disclaimer: string;
}

export function fetchRgFeeAudit(
  company = "ALL",
  dateFrom?: string | null,
  dateTo?: string | null,
): Promise<RgFeeAudit> {
  // company가 ALL/전체면 account_key 미지정(전체 계정) — replenishment-plan과 동일 관례.
  const params = new URLSearchParams();
  if (company && company !== "ALL" && company !== "전체") params.set("company", company);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();
  return fetchApi<RgFeeAudit>(`/api/coupang/ops/rg/fee-audit${qs ? `?${qs}` : ""}`);
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
  ad_options?: RocketAdOptions;
  sku_pnl?: RocketSkuPnl;
}

export interface RocketAdOptionItem {
  option_id: string;
  product_name?: string | null;  // 컬럼 추가(2026-08-03) 이전 적재분은 null → 표에선 옵션ID로 폴백
  ad_spend: string;              // Decimal → string
  impressions: number;
  clicks: number;
  conversion_revenue: string;    // Decimal → string
}

export interface RocketAdOptions {
  options: RocketAdOptionItem[];
  option_count: number;
  shown: number;
  reconciliation: {
    option_sum: string;
    account_total: string;
    diff: string;
    diff_pct: string;
    basis: string;
  };
}

// 상품(SKU)별 손익 — 표시 전용(D-16). net_profit/cost는 **모를 때 null**이다(0이 아니다):
//   원가 매핑이 없거나 그 기간 발주가 없으면 계산하지 않는다. 이유는 profit_basis가 말한다.
export interface RocketSkuPnlItem {
  sku_id: string;
  product_name?: string | null;
  option_count: number;
  ad_spend: string;
  clicks: number;
  conversion_revenue: string;
  revenue: string;
  order_qty: number;
  cost: string | null;
  // 원가 출처(D-19). sellc=등록원가(쿠팡 SKU코드 정확일치, 정본) / auto_map=이름 유사도 자동매핑(추정)
  cost_source?: "sellc" | "auto_map" | "ignored" | null;
  net_profit: string | null;
  profit_basis: string;
}

export interface RocketSkuPnl {
  skus: RocketSkuPnlItem[];
  sku_count: number;
  shown: number;
  coverage: {
    ad_total: string;
    ad_bridged: string;
    ad_bridged_pct: string | null;
    ad_with_revenue: string;
    ad_with_revenue_pct: string | null;
    ad_with_cost: string;
    ad_with_cost_pct: string | null;
    ad_cost_sellc: string;
    ad_cost_sellc_pct: string | null;
    ad_cost_auto: string;
    ad_cost_auto_pct: string | null;
    ad_unbridged: string;
    unbridged_option_count: number;
    note: string;
  };
}

export function fetchRocketOverview(from: string, to: string): Promise<RocketOverview> {
  return fetchApi<RocketOverview>(`/api/overview/rocket-overview?from=${from}&to=${to}`);
}

// ──────────────────────────────────────────────
// 로켓배송(1P) 통합 대사 — 발주·납품·거래명세서 단계·계산서를 상품 한 표로 (조회 전용)
// ★null = **모름**이지 0이 아니다(원칙22). 화면에서 0으로 렌더하지 말 것 — 미상은 "—"로.
//   특히 received_qty/drift_qty의 null은 "멀티SKU 발주라 상품별 입고를 쪼갤 근거가 없다"는 뜻이다.
// ──────────────────────────────────────────────
export interface ReconStatusRow {
  status: string | null;                 // CI/PA/RP …
  status_description: string | null;     // 거래명세서확인 / 발주확정 / 거래처확인요청
  is_settled_stage: boolean;             // true=입고 끝난 단계(드리프트가 진짜 신호)
  po_count: number;
  order_qty: number;
  received_qty: number;
  order_amount: string;                  // Decimal → string
  receiving_amount: string;
  drift_po_count: number;
}

export interface ReconInvoiceSummary {
  po_without_invoice_count: number;      // 아직 계산서에 안 묶인 발주
  mapped_invoice_count: number;
  invoice_missing_row_count: number;     // 번호는 있는데 정산행 미수집(≠미발행)
  invoice_unconfirmed_count: number;     // 세금계산서 확정일 없음
  invoice_not_marked_transmitted_count: number; // 전송 '미표기'(실패 아님)
  settled_amount: string;
  note: string;
}

export interface ReconDetailCoverage {
  pos_with_detail_count: number;
  pos_without_detail_count: number;      // 발주상세(SKU) 미수집 PO
  // 0~1 분수. null=윈도우 PO 0건(0%가 아님). ★Decimal이라 JSON에선 문자열("0.0036")로 온다.
  po_coverage_pct: string | null;
  line_count: number;
  sku_count: number;
  lines_missing_confirmed_qty: number;
  note: string;
}

export interface ReconSummary {
  po_count: number;
  order_qty: number;
  received_qty: number;
  unreceived_qty: number;
  order_amount: string;
  receiving_amount: string;
  unreceived_amount: string;
  drift_po_count: number;                // 전체(입고 전 단계의 당연한 불일치 포함)
  drift_po_count_settled_stage: number;  // ★핵심: 거래명세서확인인데 발주≠입고
  settled_stage_po_count: number;
  no_date_po_count: number;
  by_status: ReconStatusRow[];
  invoice: ReconInvoiceSummary;
  detail_coverage: ReconDetailCoverage;
}

export interface ReconSkuRow {
  product_number: string;
  product_name: string | null;
  barcode: string | null;
  po_count: number;
  line_count: number;
  order_qty: number;
  order_amount: string;
  confirmed_qty: number;                 // 납품가능(업체확인) — 미수집 라인은 빠져 있다
  confirmed_missing_lines: number;
  // ★보낸 수량(발송/ASN) — PO×SKU 그레인이 원천에 그대로 있어 귀속 추정이 필요 없다.
  //   null = 발송 기록 없음(="모름"). 0으로 접으면 미수집 기간이 미수금처럼 보인다.
  shipped_qty: number | null;
  shipment_received_qty: number | null;  // 같은 원천의 SKU별 입고(멀티SKU도 쪼개진다)
  unreceived_shipped_qty: number | null; // 보냈는데 안 잡힌 수량 = 미수금 후보
  shipment_covered_po_count: number;     // 이 SKU의 발주 중 발송 기록이 있는 건수
  received_qty: number | null;           // ★단일SKU 발주 귀속분만. null=귀속 불가(모름)
  received_attributable_po_count: number;
  received_unattributable_po_count: number;
  attributable_order_qty: number | null;
  drift_qty: number | null;              // 귀속 가능분 발주−입고(입고 전 단계 포함=참고값). null=산출 불가
  // ★진짜 신호 — 입고 완료 단계(CI·RI) 귀속분만. null=판정 근거 없음(0 아님). 빨강은 이 값에만.
  drift_qty_settled_stage: number | null;
  // 수량이 상쇄돼 0이어도 건수가 >0이면 불일치는 실재한다(요약 타일과 같은 단위).
  drift_po_count_settled_stage: number;
  settled_stage_attributable_po_count: number;
  invoice_count: number;
  // ↓ 계산서 카운터는 **이 SKU가 속한 발주** 기준 — 요약 타일(기간 전체 중복 제거)과 분모가 다르다.
  //   계산서 1건이 멀티SKU 발주에 걸리면 SKU마다 잡히므로 행 합계 > 요약. 같은 이름이지만 다른 수다.
  po_without_invoice_count: number;
  invoice_missing_row_count: number;
  invoice_unconfirmed_count: number;
  invoice_not_marked_transmitted_count: number;
}

export interface RocketRecon {
  period: { from: string; to: string; vendor_id?: string };
  channel: string;
  summary: ReconSummary;
  filters: {
    drift_only: boolean;
    unconfirmed_only: boolean;
    sku_count_total: number;
    sku_count_shown: number;
    // 발주≠입고를 판정할 근거가 없는 SKU 수 — drift_only가 '정상이라서'가 아니라 '몰라서' 제외한다.
    sku_count_unknown_drift: number;
  };
  skus: ReconSkuRow[];
  note: string;
}

export interface ReconInvoice {
  invoice_seq: number;
  found: boolean;                        // false=번호만 있고 정산행 미수집
  issue_date: string | null;
  payment_date: string | null;
  tax_invoice_confirmed_date: string | null;
  tax_invoice_transmitted: boolean | null; // null/false=미표기(실패 아님)
  payment_amount: string | null;
  supply_amount: string | null;
  vat: string | null;
  bill_issue_type: string | null;
  settlement_type: string | null;
}

export interface ReconSkuPoRow {
  product_name: string | null;
  purchase_order_seq: number;
  po_created_date: string | null;        // 발주일(KST)
  expected_delivery_date: string | null;
  status: string | null;
  status_description: string | null;
  is_settled_stage: boolean;
  center_name: string | null;
  purchase_type: string | null;
  sku_count: number;
  po_order_qty: number;                  // PO 전체(이 SKU만의 값이 아니다)
  po_received_qty: number;
  po_order_amount: string;
  po_receiving_amount: string;
  po_drift_qty: number;
  line_order_qty: number;                // 이 SKU 라인
  line_confirmed_qty: number | null;     // null=미수집(0 아님)
  line_order_amount: string;
  unit_purchase_price: string;
  invoices: ReconInvoice[];
}

export interface RocketReconSku {
  period: { from: string; to: string; vendor_id?: string };
  product_number: string;
  product_name: string | null;
  po_count: number;
  rows: ReconSkuPoRow[];
  note: string;
}

export function fetchRocketRecon(params: {
  from: string; to: string; driftOnly?: boolean; unconfirmedOnly?: boolean;
}): Promise<RocketRecon> {
  const q = new URLSearchParams({ from: params.from, to: params.to });
  if (params.driftOnly) q.set("drift_only", "true");
  if (params.unconfirmedOnly) q.set("unconfirmed_only", "true");
  return fetchApi<RocketRecon>(`/api/overview/rocket-recon?${q.toString()}`);
}

export function fetchRocketReconSku(
  productNumber: string, from: string, to: string,
): Promise<RocketReconSku> {
  const q = new URLSearchParams({ from, to });
  return fetchApi<RocketReconSku>(
    `/api/overview/rocket-recon/sku/${encodeURIComponent(productNumber)}?${q.toString()}`,
  );
}

// ── 로켓1P 공용: 창 신선도 (2026-08-06 적대 리뷰 P1) ──
// ★판매분석은 당일·전일치를 주지 않는다 → "최근 7일"을 열면 늘 5일치만 들어온다.
//   그걸 모르고 주간 비교를 하면 이번 주가 **항상** 20% 낮게 나온다. days_no_data가 0이 아닌 것
//   자체는 정상일 수 있고, stale=true여야 수집 정지를 의심한다(경보가 거짓말하면 경보가 죽는다).
export interface WindowFreshness {
  days_expected: number;
  days_with_data: number;
  days_no_data: number;
  data_as_of: string | null;
  lag_days: number | null;
  stale: boolean;
  note: string;
}

// ── 로켓1P 매출 두 축 대조 (S2, 2026-08-06) ──
// ★★두 매출을 **더하지 말 것**. consumer_revenue는 쿠팡이 고객에게 판 금액(=쿠팡의 매출)이고
//   our_revenue는 판매수량×납품단가(=우리 매출)다. 같은 물건이라 더하면 이중계상이다.
// ★null = **모름**이지 0이 아니다. our_revenue가 null인 옵션은 납품단가를 못 붙인 것이지
//   공짜로 준 게 아니다 — 화면은 반드시 "—"로 그린다.
export interface Rocket1PRevenueOption {
  option_id: string;
  sku_id: string | null;
  product_name: string | null;
  qty: number;
  consumer_revenue: string;        // 쿠팡가 기준(쿠팡의 매출)
  our_revenue: string | null;      // 납품가 기준(우리 매출) — null=단가 미상
  unit_price: string | null;
  visitors: number | null;
  our_share: string | null;        // our ÷ consumer (0~1)
  ad_spend: string | null;
  roas: string | null;             // ★우리 매출 기준
  /** 손익분기 RoAS = 매출 ÷ (매출−원가−분담금). 실제 RoAS가 이보다 **낮으면 적자**다.
   *  null = 원가/분담금을 모르거나 **공헌이익이 0 이하**(어떤 RoAS로도 흑자가 안 된다). */
  bep_roas: string | null;
  // ── 손익(2026-08-07) ── null=모름이지 0이 아니다.
  cost: string | null;             // 등록원가 × 판매수량 — null=원가 미등록 SKU
  unit_cost: string | null;
  promo_burden: string | null;     // null=분담금 원천 미수집(제안서 대기)
  net_profit: string | null;       // 우리 매출−원가−분담금−광고비−납부세액
  profit_rate: string | null;      // net ÷ our_revenue (0~1)
  /** 원가 미상이지만 **원가가 얼마든 적자**인 경우의 상한(음수일 때만 옴). 0으로 접힌 «모름»과
   *  «확정 적자»는 다르다 — 후자는 지금 피가 나고 있다는 뜻이다. */
  net_profit_upper: string | null;
}

/** 원가를 아직 못 붙인 SKU — "이걸 등록하면 손익이 완성된다"는 작업 목록이다. */
export interface Rocket1PUncostedSku {
  sku_id: string | null;
  product_name: string | null;
  qty: number;
  our_revenue: string | null;      // 납품단가까지 모르면 null
  consumer_revenue: string;
  loss_confirmed: boolean;         // 원가가 얼마든 적자(광고비가 매출을 넘었다)
  /** ★무엇을 해야 하는가. 「원가 등록」 하나로 뭉뚱그리면 사용자가 헛일을 한다 —
   *  no_link = 쿠팡 상품번호 ↔ 내부 SKU **연결**이 없다(원가를 넣어도 안 붙는다)
   *  no_cost = 연결은 있는데 그 내부 SKU에 원가가 없다
   *  excluded = 원가 제외로 **이미 결정**됨(시키면 안 된다) */
  reason: "no_link" | "no_cost" | "excluded";
  /** ★**최근에 새로 나온** 상품인가(판별자 = 발주 첫 등장일, 지평은 new_sku_window_days).
   *  「안 팔리던 게 이제 팔린다」와 「새로 나왔다」는 다르고, 후자만 매핑이 급하다.
   *  새 폰이 나올 때마다 생기고 나오자마자 매출 1위가 된다(실측: 신규 3개가 미연결 매출의 56%). */
  is_new: boolean;
  first_sold_at: string | null;
  first_po_at: string | null;
  /** 첫 판매일이 **관측 시작일**과 같다 = 그 전은 판매분석 롤링창 밖이라 모른다.
   *  이때는 is_new를 단정하지 않는다(발주 이력이 창 안이면 예외). */
  first_sold_at_bounded: boolean;
}

/** 손익 블록. ★basis='costed_subset'이면 **원가 확인분만** 더한 값이다(창 전체가 아니다). */
export interface Rocket1PPnl {
  basis: "full" | "costed_subset" | null;
  qty: number | null;
  revenue: string | null;
  cost: string | null;
  promo_burden: string | null;
  ad_spend: string | null;         // 옵션 그레인(Billboard) · 부분집합이면 그만큼만
  vat: string | null;
  net_profit: string | null;
  profit_rate: string | null;
  /** ★그 창에 **판매행이 없는** 옵션의 광고비. included=false면 위 순이익에 **안 들어있다** —
   *  귀속할 판매가 없어 부분집합에 섞지 않지만, 안 보이면 없는 돈이 되므로 항상 싣는다. */
  ad_no_sales: string;
  ad_no_sales_included: boolean;
  ad_account_total: string;        // 계정 총액(report/SALES) — 사다리 광고비와 왜 다른지 대조용
  ad_option_total: string;         // 옵션 합계(Billboard, 판매 없는 옵션 포함)
  cost_coverage: string | null;    // 원가 붙은 매출 ÷ 납품단가 붙은 매출 (분담금과 무관)
  revenue_priced: string | null;
  promo_burden_known: boolean;
  /** 손익을 못 내는 이유. ★화면이 추측하지 않는다 — 우선순위가 틀리면 엉뚱한 작업을 시킨다. */
  blocked: { code: string; reason: string } | null;
  uncosted: {
    skus: number; qty: number;
    our_revenue: string;           // known분만 합산(모르는 것을 0으로 더하지 않는다)
    our_revenue_partial: boolean;  // true면 위 합계에 «단가도 모름» SKU가 빠져 있다
    actionable_skus: number;       // 사람이 조치하면 해소되는 것(link+cost)
    link_missing_skus: number;     // ★쿠팡 상품번호 ↔ 내부 SKU 연결이 없다
    cost_missing_skus: number;     // 연결은 있는데 원가가 없다
    excluded_skus: number;         // 이미 "제외"로 결정 — 시키면 안 된다
    loss_confirmed_skus: number;
    new_skus: number;              // ★이번 기간에 새로 팔리기 시작한 미연결 SKU
    new_our_revenue: string;
    new_sku_window_days: number;   // ★판정 지평(발주 첫 등장 기준). 숨은 기준을 두지 않는다.
    top: Rocket1PUncostedSku[];    // actionable만
    /** 「원가 제외」로 이미 결정된 SKU — 시키는 목록이 아니라 «그 결정이 아직 맞나» 재검토용. */
    excluded_top: Rocket1PUncostedSku[];
    excluded_our_revenue: string;
  };
  note: string;
}

/** 일별 손익 한 줄. ★`our_revenue`(전량)와 `pnl_revenue`(원가 확인분)는 **분모가 다르다** —
 *  이익률은 후자 기준이다. 원가가 안 붙은 날은 cost·net이 null(0이 아니다). */
export interface Rocket1PDaily {
  date: string;
  qty: number;
  consumer_revenue: string;
  our_revenue: string | null;      // 전량(납품가) — 매출 축
  ad_spend_all: string;            // 그날 **전량** 광고비(Billboard)
  // ── 여기부터 전부 **원가 확인분** 축 — 위 전량 값과 분모가 다르다 ──
  pnl_revenue: string | null;      // 이익률 분모
  pnl_qty: number | null;
  cost: string | null;
  promo_burden: string | null;
  ad_spend: string | null;         // 손익에 들어간 옵션분만
  ad_no_sales: string;             // 그날 광고는 돌았는데 판매행이 없는 옵션분(순이익 미포함)
  vat: string | null;
  net_profit: string | null;
  profit_rate: string | null;
  cost_coverage: string | null;    // ★그날 기준. 창 평균이 아니다.
}

export interface Rocket1PRevenue {
  period: { from: string; to: string; vendor_id?: string };
  totals: {
    // ★판매분석이 그 창을 안 덮으면 전부 null이다 — 0이 아니라 **관측 불가**다.
    qty: number | null;
    consumer_revenue: string | null;
    our_revenue: string | null;
    settlement_revenue: string | null;
    ad_spend: string;              // 다른 원천이라 항상 실측
    our_share: string | null;
    roas: string | null;
  };
  pnl: Rocket1PPnl;
  daily: Rocket1PDaily[];
  coverage: {
    sales_data_covered: boolean;
    sales_data_from: string | null; sales_data_to: string | null;
    qty_axis: number; qty_all: number | null; qty_priced: number | null;
    priced_pct: string | null; options_unpriced: number; note: string;
  };
  freshness: WindowFreshness;
  ad_reconciliation: {
    option_sum: string; account_total: string; diff: string; basis: string;
  };
  option_count: number;
  shown: number;
  options: Rocket1PRevenueOption[];
  axes_note: string;
}

export function fetchRocket1PRevenue(params: {
  from: string; to: string; limit?: number;
}): Promise<Rocket1PRevenue> {
  const q = new URLSearchParams({ from: params.from, to: params.to });
  if (params.limit) q.set("limit", String(params.limit));
  return fetchApi<Rocket1PRevenue>(`/api/overview/rocket-1p-revenue?${q.toString()}`);
}

// ── 로켓1P 유입·전환 퍼널 (S3, 2026-08-06) ──
// ★모든 비율은 **합계의 몫**이다(Σ주문/Σ조회). 일별 비율의 평균이 아니다.
// ★null = 모름이지 0이 아니다. position은 기간 중앙값 대비 **서술**이지 권고가 아니며,
//   판정에 쓰인 임계값은 thresholds에 전부 실려 온다(숨은 기준 금지).
export type FunnelPosition =
  | "views_high_cvr_high" | "views_high_cvr_low"
  | "views_low_cvr_high" | "views_low_cvr_low" | "low_sample";

export interface Rocket1PFunnelOption {
  option_id: string;
  sku_id: string | null;
  product_name: string | null;
  visitors: number | null;
  page_views: number | null;
  orders: number | null;
  qty: number;
  consumer_revenue: string;
  views_per_visitor: string | null;   // 조회 ÷ 방문자 (탐색 깊이)
  cvr: string | null;                 // 주문 ÷ 조회 (쿠팡 「구매전환율」과 같은 정의)
  units_per_order: string | null;
  days: number;
  days_missing: number;
  is_oos: boolean | null;
  rating_count: number | null;
  rating_score: string | null;        // 리뷰 0건이면 null(0점이 아니다)
  brand_name: string | null;
  category_path: string | null;
  attrs_observed_at: string | null;
  position: FunnelPosition;
}

export interface Rocket1PFunnel {
  period: { from: string; to: string; vendor_id?: string };
  totals: {
    visitors: number; page_views: number; orders: number; qty: number;
    consumer_revenue: string;
    views_per_visitor: string | null; cvr: string | null; units_per_order: string | null;
  };
  thresholds: {
    min_page_views: number;
    median_cvr: string | null;
    median_page_views: string | null;
    eligible_options: number;
    note: string;
  };
  coverage: { option_days_missing_metrics: number; note: string };
  freshness: WindowFreshness;
  option_count: number;
  shown: number;
  options: Rocket1PFunnelOption[];
  omitted_fields: string[];
}

export function fetchRocket1PFunnel(params: {
  from: string; to: string; limit?: number; minPageViews?: number;
}): Promise<Rocket1PFunnel> {
  const q = new URLSearchParams({ from: params.from, to: params.to });
  if (params.limit) q.set("limit", String(params.limit));
  if (params.minPageViews != null) q.set("min_page_views", String(params.minPageViews));
  return fetchApi<Rocket1PFunnel>(`/api/overview/rocket-1p-funnel?${q.toString()}`);
}

// ── 쿠팡 프로모션 손익 레이어 (트랙 coupang-promo-pnl Phase 2) ──
// ★읽기 전용 신규 API. 종합조망 net_profit 회계는 이 블록과 무관하게 그대로다.
// ★null = **모름**이지 0이 아니다(원칙22). 화면에서 0으로 렌더하지 말 것 — 미상은 "—"로.
export interface PromoSkuRow {
  sku_id: string;
  product_name: string | null;
  option_ids: string[];
  sales_days: number;
  qty: number;
  realized_revenue: string;             // 소비자 실현가(회계 매출 아님, D-CPP-2)
  realized_unit_price: string | null;
  supply_unit_price: string | null;     // 납품 단가(최신 발주)
  supply_price_po_seq: number | null;   // 그 단가가 온 발주번호(창 이후 발주일 수 있다 — 대사용)
  supply_revenue: string | null;
  cost_price: string | null;
  cost: string | null;
  funding: string | null;               // 분담금 = 판매량 × 개당 할인액
  unit_contribution: string | null;     // 납품단가 − 원가 − 개당 분담금
  bep_ad_spend: string | null;
  bep_roas: string | null;
  resolved: boolean;
  unresolved_reasons: string[];
}

export interface PromoTotals {
  qty: number;                          // 손익에 들어간 분(해결된 SKU)
  qty_all: number;                      // 대상 SKU 전체 판매량
  realized_revenue: string;
  realized_revenue_all: string;
  supply_revenue: string | null;
  cost: string | null;
  funding: string | null;
  ad_cost: string | null;               // null = 옵션 귀속 불가(0 아님)
  net_profit: string | null;
  bep_ad_spend: string | null;          // 이 값을 넘는 광고비 = 적자
  bep_roas: string | null;              // ★진짜 BEP ROAS
  // ★"최악값"이 아니다 — **해결된 SKU만의** 하한이다. 미해결 SKU가 적자였다면 참값은 이 밑이고,
  //   경계일 과대 포함도 이 값을 낙관 방향으로 민다. 반드시 "≥ …(미해결 N건 제외)"로 렌더할 것.
  net_profit_lower_bound_resolved_only: string | null;
  lower_bound_excludes_unresolved: boolean;
  resolved_sku_count: number;
  unresolved_sku_ids: string[];
  unresolved_qty: number;
  qty_is_lower_bound: boolean;          // ★창 부분 커버 — 수량·손익은 확정값이 아니라 하한
  not_started: boolean;                 // 창 전체 미도래(시작 전/집계 전) — 결손이 아니다
  basis: string;
}

export interface PromoPnlCard {
  request_id: string;
  vendor_id: string;
  promotion_name: string | null;
  promotion_type: string | null;
  status: string | null;
  start_at: string | null;
  end_at: string | null;
  share_ratio: string | null;
  budget_amount: string | null;
  applied_product_count: number | null;
  unit_discount_amount: string | null;
  unit_discount_missing: boolean;
  target_sku_ids: string[];
  target_sku_missing: boolean;
  window: {
    from: string; to: string; days: number;
    window_days: number; covered_days: number;
    missing_days: string[];             // 수집 결손(메울 수 있는 구멍)
    pending_days: string[];             // 아직 확정 전 — 결손이 아니다
    data_through: string;
    sales_through: string | null;       // 판매 데이터가 실제로 닿은 마지막 날
    in_flight: boolean;
    complete: boolean;
    null_sku_rows: number;
  } | null;
  window_basis: string;
  skus: PromoSkuRow[];
  totals: PromoTotals | null;
  ad: {
    available: boolean;
    attributed: string | null;
    attributed_partial: string | null;  // 부분 귀속분(참고용 — 확정 순이익에 쓰지 않는다)
    by_option: Record<string, string>;
    ad_days_covered: number;
    ad_days_judged: number;             // pending 제외한 판정 대상 일수
    ad_days_total: number;
    option_vs_account_ratio: Record<string, string>;
    options_with_spend: number;
    options_total: number;
    account_window_spend: string;       // 계정 전체 Retail — **상한 프록시**
    basis: string;
  } | null;
  blockers: string[];
}

export interface PromoFreshness {
  today: string;
  window: { from: string; to: string; days: number };
  window_days_basis: string;
  latest_date: string | null;
  stale_days: number | null;
  missing_count: number;
  missing_dates: { date: string; days_until_expiry: number }[];
  urgent_count: number;
  subscription: {
    free_trial_end: string | null;
    days_left: number | null;
    warn: boolean;
    expired: boolean;
    basis: string;
  };
}

export interface RgCouponRow {
  coupon_id: string;
  account_key: string;
  promotion_name: string | null;
  status: string | null;
  discount_type: string | null;
  discount: string | null;
  start_at: string | null;
  end_at: string | null;
  option_count: number;
  used_amount: string | null;
  used_amount_source: string | null;
  used_amount_pending: boolean;         // ★true = 미수집(0이 아님)
}

export interface PromoPnlResponse {
  vendor_id: string | null;
  promotions: PromoPnlCard[];
  promotion_count: number;
  freshness: PromoFreshness;
  rg_coupons: {
    coupons: RgCouponRow[]; count: number; pending_count: number;
    window: { from: string | null; to: string | null } | null;
    window_note: string | null;
    account_key: string | null;
    limit: number;
    note: string;
  };
  accounting_note: string;
}

export function fetchRocketPromoPnl(limit = 20): Promise<PromoPnlResponse> {
  return fetchApi<PromoPnlResponse>(`/api/overview/rocket-promo-pnl?limit=${limit}`);
}

/** 프로모션 수기 입력(개당 할인액·대상 SKU). 보낸 키만 갱신된다 — 한쪽이 다른 쪽을 지우지 않는다. */
export function patchPromotionManual(
  requestId: string,
  body: { unit_discount_amount?: string | number | null; target_sku_ids?: string[] | null },
): Promise<{
  request_id: string;
  unit_discount_amount: string | null;
  target_sku_ids: string[] | null;
  promotion_name: string | null;
  applied_product_count: number | null;
}> {
  return fetchApi(
    `/api/coupang/ops/rocket/promotion/${encodeURIComponent(requestId)}/unit-discount`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

// ── 로켓배송(1P) 원가 매핑 (S4.5b) ──
export interface RocketUnmappedItem {
  product_number: string;
  product_name: string | null;
  barcode: string | null;
  total_order_qty: number;
  po_count: number;
  suggestions: {
    internal_sku: string; score: number; product_name: string; cost_price: number | null;
    /** ★이 내부 SKU를 **이미 쓰고 있는 상품번호 수**. 0=전용, 1+=기종 공용 원가.
     *  이름이 특정 기종을 지목해도 실제로는 공용일 수 있다(라이브: `OHI-TGLASS-IP17PRO`에
     *  붙은 12개가 아이폰12~16) — 이름만 보고 "다른 기종 원가"로 오해하는 걸 막는다. */
    already_mapped_count: number;
  }[];
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
  last_error_at: string | null; // 페처 실패 보고 시각 — 이게 변하면 실패(성공만 기다리면 180초 헛대기)
  // ── 갱신 요청 lease 계약(2026-07-27, PLAN_coupang-claim-retry-lease) ──
  // 버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청. requested=true인 동안은 아직
  // 끝나지 않은 것(재시도 대기 포함) — 실패 판정은 requested=false가 된 뒤에 한다.
  attempt_count: number;   // 이번 요청으로 시도한 횟수(0~3)
  max_attempts: number;    // 상한(3)
  claimed_at: string | null;
  in_flight: boolean;      // 지금 페처가 잡고 일하는 중(임대 유효)
}

export function requestRocketRefresh(): Promise<{ requested: boolean; requested_at: string }> {
  return fetchApi("/api/coupang/ops/rocket/request-refresh", { method: "POST" });
}

export function getRocketRefreshStatus(): Promise<RocketRefreshStatus> {
  return fetchApi<RocketRefreshStatus>("/api/coupang/ops/rocket/refresh-status");
}

// ── 오하이테크(1P) 광고비 갱신 버튼 (S3, 트랙 D-11 — adcost/rocket 패턴) ──
// 광고비는 Akamai로 prod 직접 fetch 불가(D-4) → Jino Mac poll 데몬이 가져옴. 버튼 클릭 →
// request-refresh 플래그 set → 데몬이 claim·fetch·push → refresh-status.last_success_at 변화로 완료 감지
// (last_error_at 변화면 실패).
export interface OhitechAdRefreshStatus {
  requested: boolean;
  requested_at: string | null;
  last_success_at: string | null;
  status: string;
  last_error: string | null;
  last_error_at: string | null; // 페처 실패 보고 시각 — 이게 변하면 실패(성공만 기다리면 헛대기)
  // ── 갱신 요청 lease 계약(2026-07-27, PLAN_coupang-claim-retry-lease) ──
  // 버튼 1회 = 성공하거나 3회 실패할 때까지 살아있는 요청. requested=true인 동안은 아직
  // 끝나지 않은 것(재시도 대기 포함) — 실패 판정은 requested=false가 된 뒤에 한다.
  attempt_count: number;   // 이번 요청으로 시도한 횟수(0~3)
  max_attempts: number;    // 상한(3)
  claimed_at: string | null;
  in_flight: boolean;      // 지금 페처가 잡고 일하는 중(임대 유효)
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
  // ★원가 미상 투명화 — 요약 이익은 미상분 원가가 **빠진 채** 계산된다(그만큼 과대 가능).
  //   미상은 두 종류이고 조치가 다르다: unmapped=매핑을 이어야 / zero_cost=원가를 입력해야.
  cost_unknown_products?: number;
  cost_unknown_revenue?: string;    // 그 상품들의 공급가 매출
  cost_unknown_unmapped?: number;
  cost_unknown_zero_cost?: number;
  cost_unknown_ambiguous?: number;   // 활성 매핑이 여럿인데 원가가 갈림 → 중복 매핑 정리 필요
  // 반품·교환 배송 손익(귀속=클레임 **완료일**, 주문일 아님). 종전엔 이 패널에 통째로 없어서
  // 반품이 늘어도 이익이 반응하지 않았다. claim_net은 부호가 양수일 수도 있다 —
  // 반품비 청구가 출고+회수비를 넘는 건이 있다(반품의 진짜 손실은 매출을 잃는 쪽이다).
  claim_count?: number;
  claim_income?: string; claim_cost?: string; claim_net?: string;
}

/** 오늘(days=0) 광고비의 출처. 다른 기간에선 null.
 *  kind=today_snapshot → 검색광고 당일 누적(as_of 시각 기준) · today_no_snapshot → 아직 없음.
 *  scope=search_only → 디스플레이(GFA·ADVoost)는 실차감이라 당일치가 없어 빠져 있다. */
export interface AdBasis {
  // kind=period → **기간 탭**(7·15·30일). `ad_costs`는 D+1이라 오늘 행이 없어서, 창이 오늘을
  //   포함하면 오늘 **검색광고** 당일 누적을 더한다(today_search_added). 디스플레이는 못 더한다
  //   — 비즈머니 실차감이 유일 경로인데 D−1까지만 주고, 당일 총액은 상품별 분해가 없다.
  //   즉 이 합계는 «검색은 오늘까지 + 디스플레이는 어제까지»인 혼합 축이다.
  kind: "today_snapshot" | "today_no_snapshot" | "period";
  today_search_added?: string;
  today_search_source?: "today_snapshot" | "ad_costs" | "pending";
  today_display_missing?: boolean;
  as_of: string | null;
  scope: "search_only";
  basis?: "day_max";     // 캠페인별 당일 최대 누적 합(최신 배치가 아니다 — 원천이 후퇴하기 때문)
  // ★당일 어느 슬롯에도 값이 없는 상태. 이때의 0은 "0원"이 아니라 «모름»이고, 그대로 이익에
  //   넣으면 이익이 과대로 보인다. **원인(미집계인지 실제 0원인지)은 구분할 수 없다.**
  pending?: boolean;
  regressed_by?: string;  // 최신 조회가 관측 최대치보다 낮은 금액(0이면 후퇴 없음)
  latest_cost?: string;   // 후퇴한 최신 조회값 — 화면이 "지금 원천은 이만큼만 준다"를 말할 때 쓴다
}

export interface NaverSalesSummary {
  period: { from: string; to: string };
  ad_ref_date: string | null;
  ad_basis: AdBasis | null;
  summary: NaverSalesSummaryData;
  by_product: NaverSalesProductRow[];
}

export interface NaverSalesProductRow {
  product_name: string;
  platform_id: string;
  revenue: string; fee: string;
  // ★원가를 모르면 원가·이익·이익률이 전부 null이다 — «모름»을 0원으로 계산하지 않는다.
  //   (종전엔 0원 원가로 접혀 이익률 94~96%가 나왔다. 셋 중 하나라도 숫자로 남기면 그 카드가
  //    나머지를 부정한다 — 훑는 눈에는 큰 숫자가 이긴다.)
  cost: string | null;
  cost_known?: boolean;
  cost_unknown_kind?: "unmapped" | "zero_cost" | "ambiguous" | null;
  fee_actual?: boolean;  // 수수료가 전부 정산 실측이면 true (D-6)
  profit: string | null; profit_rate: string | null;
}

export function fetchNaverSalesSummary(days: number): Promise<NaverSalesSummary> {
  return fetchApi<NaverSalesSummary>(`/api/naver/ops/sales-summary?days=${days}`);
}

// ── GFA(디스플레이) 광고비 현황·업로드 ───────────────────────────
export interface GfaSpan {
  has_data: boolean;
  date_from: string | null;
  date_to: string | null;
  days: number;
  total_spend: number;
}
/** ★신선도 판정 근거 — **수집기**다(데이터가 아니다).
 *
 *  `ad_costs`의 '행 없음'은 「그날 소진 0」과 「수집 실패」를 **겸한다**. 그래서 날짜로 판정하면
 *  반드시 한쪽으로 틀린다 — 소스별로 보면 소진 0인 날을 사고로 오탐(거짓 빨강), 계열 합집합으로
 *  보면 형제 소스가 죽어도 초록(거짓 초록). "우리가 물어봤는가"는 그것과 독립이라 안 틀린다. */
export interface GfaCollectionHealth {
  job_name: string;
  registered: boolean;
  enabled: boolean | null;
  last_success_at: string | null;
  last_status: string | null;
  last_status_at: string | null;
  last_error: string | null;
  age_hours: number | null;
  stale: boolean;      // ★배지는 이것으로만 판정한다
  reason: string;      // 왜 그렇게 판정했는지 — 화면이 말한다
}

/** 최상위 date_from/date_to = 자동+수동 합산 축 전체. **사실 진술이지 판정 근거가 아니다.**
 *  auto = 비즈머니 실차감 API 자동 적재(매일 07:10 KST) · manual = 수동 CSV.
 *  ★배지 초록/빨강은 `collection.stale`에서만 나온다. */
export interface GfaStatus extends GfaSpan {
  auto: GfaSpan;
  manual: GfaSpan;
  by_source: (GfaSpan & { source: string })[];
  collection?: GfaCollectionHealth;
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

// UI3(D-NAO-65, DL2 GATE P3① 후속): 바닥 대기(at-floor 무액션) 관찰 행 — 관찰 전용(실행 없음).
export interface NaverAdDiagnosisFloorWaitRow {
  campaign_id: string;
  adgroup_id: string;
  keyword_id: string | null;
  target_type: "adgroup" | "keyword";
  current_bid: number;
  effective_bid: number;
  effective_source: string;
  cost: number;
  clk: number;
  conv_amt: number;
  has_conv: boolean;
  roas_corrected: number | null;
  chronic_cpc: number | null;
  stop_loss_amount: number;
  window_days: number;
  reason: string;
  reason_label: string;
}

export interface NaverAdDiagnosisBoards {
  bleeding_keywords: NaverAdDiagnosisKeywordRow[];
  starving_winners: NaverAdDiagnosisKeywordRow[];
  expansion_bucket: NaverAdDiagnosisExpansionBucket;
  shopping_group_bep: NaverAdDiagnosisShoppingGroupRow[];
  exclusion_candidates: NaverAdDiagnosisExclusionCandidateRow[];
  keyword_triage: NaverAdDiagnosisKeywordTriage;
  vicious_cycle: NaverAdDiagnosisViciousCycleRow[];
  // UI3(D-NAO-65): 바닥 대기 관찰 보드(관찰 전용) — 백엔드 boards의 additive 필드.
  floor_wait_units?: NaverAdDiagnosisFloorWaitRow[];
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
  /** 대상 사람 이름(D-NAO-54, Jino 2026-07-18) — keyword=키워드텍스트 / adgroup·campaign명.
   *  naver_entity.name 해석. 없으면 null → 프론트가 target_id 폴백. */
  target_name: string | null;
  /** 소속 캠페인명(맥락). 없으면 null. */
  campaign_name: string | null;
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
  /** D-NAO-54 P4: 결정 전용 유형(param_change) — 승인해도 자동 적용 없음(적용은 수동).
   *  ★프론트는 이 파생값으로만 분기(유형 문자열 재분류 금지). 승인 Confirm 문안과 실행버튼
   *  비노출을 이 값으로 결정한다. 정보성도 실행형도 아닌 제3 분기. */
  decision_only: boolean;
  /** 실행 액션(add_negative_keyword/update_bid/set_user_lock/update_budget) — 백엔드 파생값
   *  (harness._ACTION_BY_PROPOSAL_TYPE). ★실행 Confirm 문안은 이 값으로 분기한다. 프론트가
   *  proposal_type으로 액션을 재추론하면 틀린 액션명이 뜬다(정보성 유형은 null). */
  action: string | null;
  expert_verdict: NaverExpertVerdictSummary | null;
  // X1a T4 — 콘솔 실행 버튼 활성화 여부(naver_execution_harness.real_write_blocker).
  executable: boolean;
  not_executable_reason: string | null;
  // X1a T5 — 승인 경로(콘솔 사람 승인 vs Ava 위임 자동승인, D-NAO-25). 승인 전(pending)이거나
  // 구버전 데이터는 null.
  approval_source: "console" | "delegation" | null;
}

export interface NaverAdProposalList {
  /** ★limit과 무관한 전체 건수(D-NAO-47). 페이지 길이(rows.length)를 건수로 쓰지 말 것 —
   *  limit에 따라 달라지는 틀린 숫자가 된다. */
  total: number;
  /** 현재 실쓰기 개방된 액션 목록(코드 배포로만 변경). ★배너의 "현재 개방" 표시는 이 값을
   *  쓴다 — 하드코딩 라벨("제외키워드")이 개방 순서 진행과 어긋나던 결함 재발 방지. 백엔드
   *  파생값(harness.open_executable_actions, 이중 방벽 교집합)이라 프론트가 추론하지 않는다. */
  open_actions: string[];
  rows: NaverAdProposal[];
}

export function fetchNaverAdProposals(params?: {
  status?: string;
  dateFrom?: string;
  dateTo?: string;
  campaignId?: string;
  /** ★true=정보성만 / false=실행형만 / 생략=전부.
   *  목록은 created_at DESC인데 정보성 경보(trigger_pacing)가 실행형보다 훨씬 자주 생성된다.
   *  prod 실측(2026-07-17): pending 107건 = trigger_pacing 102(07-16) + bid_up 5(07-15)라
   *  **limit=100이면 bid_up이 한 건도 안 나온다**. 받은 페이지를 !informational로 거르면
   *  "지금 결정할 제안이 없습니다"가 뜬다 — 5건이 결정을 기다리는데.
   *  **실행형이 필요하면 informational:false로 질의한다**(limit 올리기는 임시방편). */
  informational?: boolean;
  limit?: number;
}): Promise<NaverAdProposalList> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.dateFrom) q.set("date_from", params.dateFrom);
  if (params?.dateTo) q.set("date_to", params.dateTo);
  if (params?.campaignId) q.set("campaign_id", params.campaignId);
  if (params?.informational !== undefined) q.set("informational", String(params.informational));
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
// D-NAO-65 UI2 — 캠페인별 loss 대응 정책. leash=고삐(전역 기본값) / stoploss_pause=하드 정지 회귀.
// 백엔드는 NULL도 반환한다(미설정) → 프론트가 '기본(고삐)'로 해석(effectiveLossPolicy).
export type NaverLossPolicy = "leash" | "stoploss_pause";

export interface NaverAdCampaignSettings {
  campaign_id: string;
  optimizer: NaverAdOptimizer;
  mode: NaverAdCampaignMode | null;
  target_roas_override: number | null;
  memo: string | null;
  /** D-NAO-65 UI2 — loss 대응 정책. null=미설정(기본 고삐). _serialize_settings가 실어줌. */
  loss_policy: NaverLossPolicy | null;
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

/** 모드·공격성·override·memo 설정(전체 치환).
 *  ★optimizer는 optional이고 **생략하면 백엔드가 기존 값을 보존**한다. 관리주체를 바꾸려면
 *  `putNaverCampaignOptimizer`를 쓸 것 — 여기로 optimizer를 보내면 1층 스위치의 확인창
 *  (원본 MOP 미차단 경고)을 우회하고, stale 버퍼가 스위치 변경을 덮어쓴다(codex[P1]). */
export function putNaverCampaignSettings(body: {
  campaignId: string;
  optimizer?: NaverAdOptimizer;
  mode?: NaverAdCampaignMode | null;
  targetRoasOverride?: number | null;
  memo?: string | null;
}): Promise<NaverAdCampaignSettings> {
  return fetchApi<NaverAdCampaignSettings>("/api/naver/ad/campaign-settings", {
    method: "PUT",
    body: JSON.stringify({
      campaign_id: body.campaignId,
      ...(body.optimizer !== undefined ? { optimizer: body.optimizer } : {}),
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
  /** 대상 사람 이름(D-NAO-54, Jino 2026-07-18) — adgroup="17E" / keyword=키워드텍스트 /
   *  campaign=캠페인명. naver_entity.name 해석 결과. 없으면 null → 프론트가 'type id' 폴백. */
  entity_name: string | null;
  /** 소속 캠페인명(대상이 adgroup/keyword일 때 맥락). 없으면 null. */
  campaign_name: string | null;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  rationale: string | null;
  outcome: string | null;
  /** 우리 실집행 시도의 3-상태(D-NAO-54) — bool이 아닌 이유가 있다.
   *   "executed" 광고가 실제로 바뀜 / "blocked" 가드레일이 막음(확실히 안 바뀜) /
   *   "unknown"  쓰기 예외 — PUT을 이미 보낸 뒤일 수 있어 **반영 여부 모름** /
   *   null       이 개념이 적용 안 되는 행(외부 감지·내부 설정·dry-run).
   *  ★"unknown"을 "blocked"로 그리면 안 된다: WriteVerificationError는 "bidAmt는 반영됐으나
   *  useGroupBidAmt 미전환"에서도 뜬다 — 네이버엔 우리 입찰가가 들어가 있다(원칙22). */
  execution_state: "executed" | "blocked" | "unknown" | null;
  dry_run: boolean;
  proposal_id: number | null;
  executed_at: string | null;
}

export interface NaverChangeLogResponse {
  total: number;
  /** total 중 실제로 광고가 바뀐 건수. actor=ours+include_blocked에서만 채워진다(그 외 null).
   *  ★total만 쓰면 "우리가 한 일" 카드가 차단 시도를 집행으로 센다. */
  executed_total: number | null;
  rows: NaverChangeLogRow[];
}

/** 변경 이력. ★include_dry_run 기본 false — "우리 조작 N회"는 실집행만 센다(D-47-h). */
export async function fetchNaverChangeLog(params: {
  campaign_id?: string; action?: string;
  /** ★ours=우리 실집행만 / external=외부 변경 감지만 / all=전부(기본).
   *  "우리 조작 N회"를 셀 땐 **반드시 ours**다 — change_log에는 external_bid_change 등
   *  외부 변경 감지가 섞여 있어(prod 실측: dry_run=False 15건이 전부 외부 감지) 필터 없이
   *  세면 우리가 아무것도 안 했는데 "15회"라고 표시된다(codex[P2] 2026-07-17). */
  actor?: "all" | "ours" | "external";
  /** ★date_from/date_to의 폴백일 뿐이다(D-NAO-54). "지금부터 N일 전"이라 '당일만'·'어제만'
   *  같은 닫힌 구간을 표현할 수 없다 — 화면 프리셋은 date_from/date_to를 쓴다. */
  days?: number;
  /** KST 날짜 YYYY-MM-DD, 양끝 **포함**. 반드시 date_to와 함께(한쪽만 주면 422). */
  date_from?: string;
  date_to?: string;
  include_dry_run?: boolean;
  /** actor=ours에서 가드레일 차단 시도도 포함(기본 false). 행의 executed로 구분해 그린다.
   *  ★기본 false가 계약이다: 1층 "우리 조작 N회"는 실집행만 센다(D-47-h). */
  include_blocked?: boolean;
  limit?: number; offset?: number;
} = {}): Promise<NaverChangeLogResponse> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/change-log?${q.toString()}`);
}

// ══════════════════════════════════════════════════════════════════
// 「수정 사항」 화면 — naver_change_log ∪ naver_agency_op 합본 + 주체 정정
// ★`fetchNaverChangeLog`(단일 원천)와 다른 엔드포인트다. 대행사 조작은 grain에 따라
//   **다른 테이블**에 들어가므로(입찰·상태 diff는 change_log의 external_*, 소재 editTm은
//   agency_op) 한쪽만 보면 "그날 아무도 안 만졌다"는 거짓 안심을 준다.
// ══════════════════════════════════════════════════════════════════

/** 우리 자동화 / 대행사 / Jino. ★'MOP'라는 말은 화면 어디에도 쓰지 않는다 — 코드베이스의
 *  `optimizer='mop'`은 "제3자 소유"라는 뜻이라 Jino가 말하는 "MOP=우리 시스템"과 정반대다. */
export type NaverModificationActor = "ours" | "agency" | "jino";

export interface NaverModificationRow {
  /** `"change_log:1122"` — 두 원천의 id가 겹치므로 원천을 접두한 합성 키다(React key·정정 대상). */
  key: string;
  source: "change_log" | "agency_op";
  source_label: string;
  source_id: number;
  /** 귀속 시각(KST). agency_op은 occurred_at 우선 — 백필 36건은 감지일이 08-03이지만
   *  실제로는 07-30 일이라, 감지일로 잡으면 07-30을 골랐을 때 안 보인다. */
  occurred_at: string | null;
  occurred_date: string | null;
  /** "occurred"=실제 발생 시각 / "detected"=우리가 알아챈 시각(실제로 언제 손댔는지 모름). */
  time_basis: "occurred" | "detected";
  time_note: string;
  /** 정정을 반영한 **최종** 주체. */
  actor: NaverModificationActor;
  actor_label: string;
  /** 데이터로 자동 판정한 주체(정정이 있어도 지워지지 않는다 — 판정이 옳았는지 봐야 한다). */
  actor_auto: NaverModificationActor;
  corrected: boolean;
  correction_note: string | null;
  entity_type: string;
  entity_type_label: string;
  entity_id: string;
  entity_name: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  op_type: string;
  op_label: string;
  /** 표시용 이전/이후 값. **null이면 모른다는 뜻**이고 사유가 *_unknown에 온다 —
   *  빈칸이나 0으로 채우지 않는다(백필 36건 중 31건은 이전값이 아예 없다). */
  before: string | null;
  after: string | null;
  before_unknown: string | null;
  after_unknown: string | null;
  /** 우리 쓰기의 3상태. agency_op 행은 우리 쓰기가 아니라 관측이라 항상 null. */
  execution_state: "executed" | "blocked" | "unknown" | null;
  summary: string | null;
  /** 소급 백필로 들어온 행(정규 탐지가 아니라 신뢰도가 다르다). */
  backfilled: boolean;
  dry_run: boolean;
  /** D-NAO-139 — 소재 편집이 「네이버의 상품 피드 재적용」인지 「사람의 실조작」인지.
   *  판정 대상이 아닌 행(다른 grain·매핑 결손)은 전부 null이다.
   *   "feed"    상품의 소재가 **전량** 같은 초로 움직임 = 피드 재적용(사람 손 아님)
   *   "real"    일부만 움직임 = 사람이 그 소재를 골라 만졌다
   *   "unknown" 소재가 1개뿐이라 구조로 못 가름 */
  feed_verdict: "feed" | "real" | "unknown" | null;
  feed_verdict_label: string | null;
  /** 그 판정의 근거 한 문장(서버가 만든다 — 규칙이 화면마다 다른 말이 되지 않게). */
  feed_evidence: string | null;
  /** 접기로 이 줄에 합쳐진 형제 수. 1이면 접힌 게 없다. */
  feed_group_size: number;
  /** 접힌 형제들의 source_id(감사·펼치기용 — 접었다고 버리지 않는다). */
  feed_group_ids: number[];
}

/** D-NAO-139 — 피드 재적용을 얼마나 접었고 숨겼는지. **항상 온다**(조용한 truncation 금지). */
export interface NaverModificationFeedReapply {
  /** 판정이 붙은 행 수(소재 grain). */
  verdict_rows: number;
  /** 그중 피드 재적용으로 판별된 행 수. */
  feed_rows: number;
  /** 숨기기로 목록에서 뺀 행 수. */
  hidden: number;
  /** 접기로 사라진 줄 수(5줄→1줄이면 4). */
  collapsed_into: number;
  included: boolean;
  collapsed: boolean;
}

export interface NaverModificationResponse {
  total: number;
  /** 구간 전체 주체 분포 — actor 필터를 걸어도 전체가 보인다. */
  by_actor: Record<NaverModificationActor, number>;
  /** D-NAO-139 — 피드 재적용을 얼마나 접고 숨겼는지(항상 온다). */
  feed_reapply: NaverModificationFeedReapply;
  rows: NaverModificationRow[];
}

export async function fetchNaverModifications(params: {
  /** KST 날짜 YYYY-MM-DD, 양끝 **포함**. 반드시 둘을 함께(한쪽만 주면 422). */
  date_from?: string;
  date_to?: string;
  days?: number;
  campaign_id?: string;
  actor?: NaverModificationActor;
  source?: "change_log" | "agency_op";
  include_dry_run?: boolean;
  /** 가드레일이 막아 **실제로는 안 바뀐** 시도도 포함(기본 false — 안 바뀐 걸 수정으로 세면 거짓). */
  include_blocked?: boolean;
  /** D-NAO-139 — 피드 재적용 행 포함(기본 true). false면 사람이 만진 것만 남는다. */
  include_feed_reapply?: boolean;
  /** D-NAO-139 — 같은 상품이 같은 초에 움직인 N줄을 1줄로 접는다(기본 true). */
  collapse_feed_reapply?: boolean;
  limit?: number;
  offset?: number;
} = {}): Promise<NaverModificationResponse> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/modifications?${q.toString()}`);
}

/** 수정 1건의 주체를 정정한다. ★원천 테이블은 건드리지 않는다 — 정정 전용 테이블에만 쌓인다.
 *  `actor: null`은 정정을 지우고 자동 판정으로 되돌린다(오타 정정이 영구화되지 않게). */
export function putNaverModificationActor(
  source: "change_log" | "agency_op",
  sourceId: number,
  actor: NaverModificationActor | null,
  note?: string | null,
): Promise<{ source: string; source_id: number; actor: string | null; corrected: boolean }> {
  return fetchApi(`/api/naver/ad/modifications/${source}/${sourceId}/actor`, {
    method: "PUT",
    body: JSON.stringify({ actor, note: note ?? null }),
  });
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


// ── D-NAO-48 캠페인 명부 + 관리주체 스위치 ──

export interface NaverCampaignRosterRow {
  campaign_id: string;
  /** ★캠페인 이름. 이게 없어서 그동안 화면에 내부 ID가 그대로 노출됐다(MOP UX 리뷰에서
   *  "베끼면 안 되는 것"으로 꼽은 항목). naver_entity에 있던 걸 이제 명부 SA가 준다. */
  name: string;
  campaign_type: string;
  status: string;
  cost: number;
  clk: number;
  conv_amt: number;
  /** 광고비 0이면 null — 'ROAS 0배'가 아니라 '알 수 없음'. */
  roas_naver: number | null;
  optimizer: NaverAdOptimizer;
  /** D-NAO-65 UI2 — loss 대응 정책. null=미설정 → 콘솔이 '기본(고삐)'로 해석. */
  loss_policy: NaverLossPolicy | null;
  /** D-NAO-104 P1-1(additive) — 자동 운영 레인 대상인가. optimizer와 **다른 축**이다
   *  (우리 소유인데 자동 레인만 꺼둔 상태가 실재한다). */
  auto_operate: boolean;
  /** D-NAO-97 statusReason 원문(ELIGIBLE/CAMPAIGN_PAUSED/CAMPAIGN_LIMITED_BY_BUDGET…).
   *  한글화는 성과 뷰(백엔드)가 하고, 여기선 원문 그대로 온다. null=미수집. */
  status_reason: string | null;
  window_days: number;
}

export function fetchNaverCampaignRoster(params: {
  days?: number; campaign_type?: string; optimizer?: NaverAdOptimizer;
} = {}): Promise<{ total: number; rows: NaverCampaignRosterRow[] }> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  const qs = q.toString();
  return fetchApi(`/api/naver/ad/campaigns${qs ? `?${qs}` : ""}`);
}

/** 관리주체만 바꾼다(D-NAO-48).
 *  ★putNaverCampaignSettings를 쓰지 말 것 — 그건 **전체 치환**이라 optimizer만 보내면
 *  mode·target_roas_override·gamma·memo가 전부 null로 날아간다. 이 엔드포인트는 optimizer
 *  외 필드를 건드리지 않는다. */
export function putNaverCampaignOptimizer(body: {
  campaignId: string; optimizer: NaverAdOptimizer;
}): Promise<NaverAdCampaignSettings> {
  return fetchApi<NaverAdCampaignSettings>("/api/naver/ad/campaign-settings/optimizer", {
    method: "PUT",
    body: JSON.stringify({ campaign_id: body.campaignId, optimizer: body.optimizer }),
  });
}

/** loss 대응 정책만 바꾼다(D-NAO-65 UI2). optimizer 스위치와 동형의 전용 엔드포인트 —
 *  전체 치환 PUT을 쓰면 mode·override·gamma가 null로 날아간다(D-NAO-53 교훈). 이 엔드포인트는
 *  loss_policy 외 필드를 건드리지 않는다. 백엔드는 loss_policy를 leash|stoploss_pause만 받는다
 *  (naver_ad.py _VALID_LOSS_POLICIES). NULL→leash 정규화·change_log 기록은 백엔드 책임. */
export function putNaverCampaignLossPolicy(
  campaignId: string,
  lossPolicy: NaverLossPolicy,
): Promise<NaverAdCampaignSettings> {
  return fetchApi<NaverAdCampaignSettings>("/api/naver/ad/campaign-settings/loss-policy", {
    method: "PUT",
    body: JSON.stringify({ campaign_id: campaignId, loss_policy: lossPolicy }),
  });
}

// 쿠팡 4스트림 수집 신선도(전역 배너 전용). 자동 트리거 제거 후 '낡음/실패' 가시화 유일 경로.
// 'unknown' = 백엔드가 그 스트림의 상태를 **판정하지 못했다**(getter 예외). fresh와 절대
// 섞지 않는다 — 모르는 것을 괜찮다고 표시하면 침묵과 같다(2026-08-07 적대리뷰 P1).
export type CollectionState =
  | "fresh" | "warn" | "critical" | "failed" | "in_flight" | "unknown";
export interface CollectionStreamStatus {
  key: "ofix_sales" | "ofix_ad" | "ohitech_ad" | "supplier_hub";
  label: string;
  state: CollectionState;
  age_hours: number | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
}
export interface CollectionStatus {
  streams: CollectionStreamStatus[];
  as_of: string;
}
export function getCollectionStatus(): Promise<CollectionStatus> {
  return fetchApi<CollectionStatus>("/api/coupang/ops/collection-status");
}

// ── 광고 성과(사장님 뷰) — D-NAO-104 Phase 1 (docs/PLAN_naver-ad-performance-view.md §4-ⓐ) ──
// ★이 응답의 한국어 문자열은 **백엔드가 이미 D-NAO-103 규칙으로 조립한 것**이다(ID·내부 용어
//   없음, 문장). 프론트는 그대로 렌더한다 — 여기서 문장을 다시 만들면 표기 규칙이 두 벌이 된다.
// ★null은 전부 "알 수 없음"이다. 0으로 대체하거나 `?? 0`으로 삼키지 말 것(원칙22).
export interface NaverPerformanceCampaignCard {
  /** 화면 미표시(딥링크·title 속성 전용). D-NAO-103①: 사람에겐 이름만 보여준다. */
  campaign_id: string;
  name: string;
  type_label: string;          // 쇼핑검색 / 파워링크 …
  status_label: string;        // 정상 노출 중 / 정지됨 / 오늘 예산을 다 써서 멈춤 …
  review_label: string | null; // "검수 중"(정상 노출과 배타가 아닌 별도 축)
  managed_by_label: string;    // 우리가 자동으로 운영 / 대행사가 운영 / 직접 관리…
  auto_operate: boolean;
  spend_today: number;
  daily_budget: number | null; // null = 일예산 미설정(무제한)
  spend_ratio: number | null;  // 분수(0~1). 일예산 없으면 null
  imp_today: number;
  clk_today: number;
  /** 상한 프록시 매출(그 상품의 전체 판매액). 상품 매핑이 없으면 null. */
  revenue_today_proxy: number | null;
  /** 상한 프록시 ROAS. null = 알 수 없음 — **0.00배로 렌더 금지**(파워링크가 항상 이 경우). */
  roas_today_proxy: number | null;
  roas_unknown_reason: string | null;
  target_roas: number | null;
  bep_roas: number | null;
  shared_product_count: number; // 여러 캠페인이 공유해 매출을 나눠 계상한 상품 수
  active_today: boolean;
  verdict_sentence: string;
  // ★출처 라벨(D-NAO-105): 같은 칸에 오늘=실주문 프록시와 과거=네이버 확정치가 번갈아 들어온다.
  //   무엇을 보고 있는지는 **백엔드가 정한 라벨**이 말한다 — 프론트가 다시 판단하지 않는다.
  source: "today_proxy" | "settling" | "confirmed";
  source_label: string;   // 오늘 추정 / 확정 중 / 확정
  roas_label: string;     // 오늘 ROAS(추정) / ROAS(확정 중) / ROAS(확정)
  revenue_label: string;  // 오늘 매출(추정) / 전환매출(확정 중) / 전환매출(확정)
}

export type NaverPerformanceActionState = "executed" | "blocked" | "unknown";

export interface NaverPerformanceActionItem {
  at: string | null;
  time_label: string;
  state: NaverPerformanceActionState;
  campaign_id: string | null;
  campaign_name: string | null;
  sentence: string;
}

export interface NaverPerformanceToday {
  as_of: string;
  date: string;
  data_note: string;
  campaigns: NaverPerformanceCampaignCard[];
  totals: { spend_today: number; campaigns_active_today: number; campaigns_total: number };
  today_actions: {
    executed_count: number;
    blocked_count: number;
    unknown_count: number;
    items: NaverPerformanceActionItem[];
    /** 0건일 때만 채워진다 — 0을 숨기지 않고 왜 0인지 말한다(D-47-h). */
    quiet_reason: string | null;
  };
}

export function fetchNaverPerformanceToday(): Promise<NaverPerformanceToday> {
  return fetchApi<NaverPerformanceToday>("/api/naver/ad/performance/today");
}

// ── 광고 성과 Phase 2 — 날짜 선택·비교·캠페인 상세·예산 (D-NAO-105, 계획서 §4-ⓑⓒ) ──
// ★날짜에 따라 숫자의 **출처가 다르다**: 오늘=실주문 상한 프록시 / 과거=네이버 확정 전환매출.
//   `source_label`·`roas_label`·`revenue_label`은 백엔드가 정한 라벨이다 — 프론트가 다시
//   판단하지 않는다(표기 규칙이 두 벌이 되면 갈라진다).

/** today_proxy=오늘 추정 · settling=확정 중(간접전환 유입 중) · confirmed=확정 */
export type NaverPerformanceSource = "today_proxy" | "settling" | "confirmed";

export interface NaverPerformanceCampaignOption {
  campaign_id: string;  // select의 value 전용 — 사람이 읽는 자리엔 절대 안 나간다
  name: string;
  type_label: string;
  managed_by_label: string;
  cost_30d: number;
}

export interface NaverPerformanceCampaignOptions {
  campaigns: NaverPerformanceCampaignOption[];
  window_days: number;
}

export function fetchNaverPerformanceCampaignOptions(): Promise<NaverPerformanceCampaignOptions> {
  return fetchApi<NaverPerformanceCampaignOptions>("/api/naver/ad/performance/campaigns");
}

/** 날짜 일반화 응답. Phase 1의 `/today`와 같은 모양 + 출처 라벨이 더 붙는다. */
export interface NaverPerformanceDay extends NaverPerformanceToday {
  is_today: boolean;
  source: NaverPerformanceSource;
  source_label: string;
  campaign_filter: string | null;
  /** 과거 날짜인데 확정 기록이 한 줄도 없을 때만 채워진다 — "0원 집행"과 "수집 안 됨"은 다르다. */
  data_gap_note: string | null;
}

export function fetchNaverPerformanceDay(
  params: { date?: string; campaignId?: string } = {},
): Promise<NaverPerformanceDay> {
  const q = new URLSearchParams();
  if (params.date) q.set("date", params.date);
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  const qs = q.toString();
  return fetchApi<NaverPerformanceDay>(`/api/naver/ad/performance/day${qs ? `?${qs}` : ""}`);
}

/** 증감. `pct`는 **분수**(0.12=+12%) — `pctFromFraction` 계약. 한쪽이라도 모르면 둘 다 null. */
export interface NaverPerformanceDelta {
  abs: number | null;
  pct: number | null;
}

export interface NaverPerformanceDayMetrics {
  spend: number;
  imp: number;
  clk: number;
  revenue: number | null;
  roas: number | null;
}

export interface NaverPerformanceCompareSide {
  date: string;
  source: NaverPerformanceSource;
  source_label: string;
  data_note: string;
  totals: NaverPerformanceDayMetrics & { revenue_unknown_campaigns: number };
}

export type NaverPerformanceCompareMetric = "spend" | "imp" | "clk" | "revenue" | "roas";

export interface NaverPerformanceCompareRow {
  campaign_id: string;
  name: string;
  type_label: string;
  base: NaverPerformanceDayMetrics;
  against: NaverPerformanceDayMetrics;
  deltas: Record<NaverPerformanceCompareMetric, NaverPerformanceDelta>;
}

export interface NaverPerformanceCompare {
  base: NaverPerformanceCompareSide;
  against: NaverPerformanceCompareSide;
  deltas: Record<NaverPerformanceCompareMetric, NaverPerformanceDelta>;
  campaign_filter: string | null;
  rows: NaverPerformanceCompareRow[];
  /** 오늘(프록시) vs 과거(확정)처럼 **정의가 다른** 값끼리의 비교일 때만 채워진다. */
  mixed_source_note: string | null;
  /** 한쪽이 아직 전환 정착 중일 때. 정의 불일치는 아니다(둘 다 확정치). */
  settling_note: string | null;
  empty_reason: string | null;
}

export function fetchNaverPerformanceCompare(
  base: string, against: string, campaignId?: string,
): Promise<NaverPerformanceCompare> {
  const q = new URLSearchParams({ base, against });
  if (campaignId) q.set("campaign_id", campaignId);
  return fetchApi<NaverPerformanceCompare>(`/api/naver/ad/performance/compare?${q}`);
}

/** 상태 내부 코드. 화면에는 `state_label`만 쓴다(D-NAO-103②).
 *  `observed` = 우리가 운영하지 않는 광고 → 성과 사실만 진술(능동 관리 문장 금지). */
export type NaverPerformanceGroupState =
  | "expanding" | "watching" | "hold" | "blocked" | "observed";

export interface NaverPerformanceGroup {
  adgroup_id: string;   // title 속성 전용
  name: string;
  state: NaverPerformanceGroupState;
  state_label: string;  // 확장 중 / 관망 / 증액 보류 / 차단됨
  reason_sentence: string;
  cost: number;
  imp: number;
  clk: number;
  conv_amt: number;
  roas: number | null;
}

export interface NaverPerformanceSeriesPoint {
  date: string;
  cost: number;
  imp: number;
  clk: number;
  conv_amt: number;
  /** 네이버 확정 기준(직+간접). 광고비 0이면 null — 0으로 그리지 않는다. */
  roas: number | null;
  avg_rank: number | null;
}

export interface NaverPerformanceCampaignDetail {
  campaign_id: string;
  name: string;
  type_label: string;
  managed_by_label: string;
  managed_by_us: boolean;
  /** 우리가 운영하지 않는 광고일 때만 채워진다 — 성과를 우리 조치로 읽지 않도록. */
  managed_note: string | null;
  window: { from: string; to: string; days: number };
  change_window_days: number;
  lines: { target_roas: number | null; bep_roas: number | null };
  series: NaverPerformanceSeriesPoint[];
  series_note: string;
  groups: NaverPerformanceGroup[];
  totals: { cost: number; conv_amt: number; roas: number | null; imp: number; clk: number };
}

export function fetchNaverPerformanceCampaign(
  campaignId: string, days = 30,
): Promise<NaverPerformanceCampaignDetail> {
  return fetchApi<NaverPerformanceCampaignDetail>(
    `/api/naver/ad/performance/campaign/${encodeURIComponent(campaignId)}?days=${days}`,
  );
}

export interface NaverPerformanceBudgetPoint {
  hour: number;
  cost: number;      // 그 시각까지의 **누적**
  hour_cost: number; // 그 한 시간 지출(차분)
  spend_ratio: number | null;
  imp: number;
  clk: number;
}

export interface NaverPerformanceBudgetCurve {
  campaign_id: string;
  campaign_name: string;
  daily_budget: number | null;
  spend_total: number;
  spend_ratio: number | null;
  points: NaverPerformanceBudgetPoint[];
  /** 예산을 다 써서 광고가 멈춘 시각들(음영 구간). 빈 배열 = 멈춘 적 없음.
   *  트리거는 "증분 0이 2시간 연속", 예산 귀속은 멈추기 **직전** 소진율 ≥90%가 근거다. */
  blackout_hours: number[];
  blackout_sentence: string | null;
  /** 멈추긴 했는데 예산 탓이라 단언할 근거가 없는 구간(소진율 60~90%). 원인을 말하지 않는다. */
  stall_sentence: string | null;
}

export interface NaverPerformanceBudget {
  date: string;
  is_today: boolean;
  curves: NaverPerformanceBudgetCurve[];
  budget_changes: (NaverPerformanceActionItem & { hour: number | null })[];
  /** 빈 배열은 정상이다 — 왜 비었는지 말한다(D-47-h). */
  budget_changes_empty_reason: string | null;
  data_note: string;
}

export function fetchNaverPerformanceBudget(
  params: { date?: string; campaignId?: string } = {},
): Promise<NaverPerformanceBudget> {
  const q = new URLSearchParams();
  if (params.date) q.set("date", params.date);
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  const qs = q.toString();
  return fetchApi<NaverPerformanceBudget>(`/api/naver/ad/performance/budget${qs ? `?${qs}` : ""}`);
}

// ── ⑤ BEP 구성 — 성과뷰 Phase 3 (D-NAO-104, 계획서 §4-ⓓ) ──────────────
// ★새 산식 없음 — bep_breakdown.py는 매일 저장된 naver_product_bep 값을 되짚어 보여줄 뿐이다.
//   화면 산술은 저장값끼리의 자명한 조합(수수료액=판매가×요율, 세전잔액=판매가−수수료−원가−
//   물류비)이고, 공헌이익은 그걸 ÷vat_divisor 한 값이다(뺄셈이 안 맞아 보이는 이유를 화면에서
//   설명해야 한다 — 컴포넌트 쪽 요구사항 참조).
export interface NaverPerformanceBepRow {
  product_name: string;
  /** ★화면에 절대 렌더 금지(내부값) — ad_count로만 개수를 보여준다. */
  campaign_ids: string[];
  ad_count: number;
  selling_price: number;
  /** 분수(0~1). pctFromFraction 계약. */
  commission_rate: number | null;
  commission_won: number;
  /** null = 원가 미입력 — 0으로 렌더하지 않는다. */
  cost_price: number | null;
  logistics_cost: number;
  /** 분수(0~1). */
  nbaesong_share: number | null;
  nbaesong_sample: number | null;
  /** 판매가−수수료−원가−물류비. null = 원가 미입력이라 산출 불가. */
  pre_vat_margin: number | null;
  /** pre_vat_margin ÷ vat_divisor. */
  contribution_margin: number | null;
  bep_roas: number | null;
  target_roas: number | null;
  /** null = 상한 산출 불가(blocked_reason 또는 ceiling_basis에 사유). */
  ceiling_bid: number | null;
  /** true = 이 행의 ceiling_bid가 이 상품 자체 표본이 아니라 **계정 평균을 빌려** 계산된
   *  값이라 실제보다 후하게(낙관적으로) 나왔을 수 있다. marketBidTone에서 이 값이 true면
   *  good/bad 색 판정을 건너뛰고 중립(idle)으로 렌더한다(근거 없는 확신을 색으로 주지 않는다). */
  ceiling_is_borrowed: boolean;
  /** 마크다운 `**` 포함 가능 — stripBoldMarkers로 정제 후 렌더할 것. */
  ceiling_basis: string;
  /** 최근 관측일의 **최댓값**(구속 조건) — 최솟값이 아니다. 그 순위를 사려면 기기·소재 중
   *  가장 비싼 쪽을 지불해야 실제로 닿는다(bep_breakdown.py `_market_bid` 참고). */
  market_bid: number | null;
  /** market_bid를 낸 기기. "MOBILE" | "PC" | null. */
  market_bid_device: string | null;
  /** market_bid를 관측한 날짜(YYYY-MM-DD). 최대 7일 전일 수 있다 — 화면에 반드시 함께
   *  표기해 "지금" 값처럼 보이지 않게 한다(원칙22). */
  market_bid_observed_on: string | null;
  market_bid_position: number | null;
  /** "" = 문제 없음. 비어있지 않으면 상한을 계산할 수 없었던 이유. */
  blocked_reason: string;
  sentence: string;
}

export interface NaverPerformanceBepBreakdown {
  rows: NaverPerformanceBepRow[];
  missing_cost_count: number;
  vat_divisor: number;
  data_note: string;
  campaign_id: string | null;
  as_of: string;
}

export function fetchNaverPerformanceBepBreakdown(
  params: { campaignId?: string; onlyActionable?: boolean } = {},
): Promise<NaverPerformanceBepBreakdown> {
  const q = new URLSearchParams();
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  if (params.onlyActionable !== undefined) {
    q.set("only_actionable", params.onlyActionable ? "true" : "false");
  }
  const qs = q.toString();
  return fetchApi<NaverPerformanceBepBreakdown>(
    `/api/naver/ad/performance/bep-breakdown${qs ? `?${qs}` : ""}`,
  );
}

// ── ⑥ 개선 타임라인 — 성과뷰 Phase 3 (D-NAO-104, 계획서 §4-ⓔ) ──────────
// ★인과 주장 없음 — perf_timeline_harness.build_timeline은 "이 변경 전후 기간이 이랬다"는
//   관찰만 낸다. sentence/data_note는 백엔드가 쓴 문장을 그대로 렌더한다(정직 규약은
//   백엔드에 있다 — 프론트가 다시 쓰지 않는다).
export interface NaverPerformanceTimelineEvent {
  /** ★화면에 렌더 금지 — React key 용도로만 쓴다. */
  ref_key: string;
  label_ko: string;
  /** 빈 문자열일 수 있다. */
  detail_ko: string;
  effective_confidence: "commit" | "assumed" | "log" | "unknown";
  scope: "account" | "campaign";
  source: "track" | "change_log";
  curated: boolean;
  /** campaign_id가 있을 때만 존재. */
  campaign_name?: string;
}

export interface NaverPerformanceTimelineWindow {
  days: number;
  /** 달력상 일수(days) 중 실제로 적재된 행이 있었던 날짜 수. days_with_data === 0이면
   *  cost/conv_amt/roas는 전부 null(측정된 0이 아니라 데이터 없음 — 원칙22). */
  days_with_data: number;
  /** null = 측정 안 됨(0이 아니다 — 원칙22). */
  cost: number | null;
  conv_amt: number | null;
  roas: number | null;
}

export interface NaverPerformanceTimelineImpact {
  pre: NaverPerformanceTimelineWindow;
  post: NaverPerformanceTimelineWindow & { complete: boolean };
  /** 최대 5개까지만 — 전체 개수는 confounded_count로 따로 나간다. */
  confounded_with: string[];
  /** ★다른 날의 변경만 센다(같은 날 함께 확정된 변경은 same_day_count로 따로 낸다). */
  confounded_count: number;
  /** 그날 함께 확정된 변경 수(자기 자신 포함). > 1이면 그 결정들을 서로 떼어 볼 수 없다는 뜻. */
  same_day_count: number;
  sentence: string;
}

export interface NaverPerformanceTimelineDay {
  date: string;
  events: NaverPerformanceTimelineEvent[];
  /** null = 날짜 파싱 실패로 전후 비교를 못 낸 것. */
  impact: NaverPerformanceTimelineImpact | null;
}

export interface NaverPerformanceTimeline {
  as_of: string;
  days: number;
  campaign_id: string | null;
  /** false여도 에러가 아니다 — 트랙 결정 목록 없이 라이브 변경만 나온다는 뜻. */
  catalog_available: boolean;
  undated_catalog_count: number;
  event_count: number;
  /** 날짜 오름차순. */
  timeline: NaverPerformanceTimelineDay[];
  retro: {
    window_days: number;
    n: number;
    correct: number;
    gray: number;
    wrong: number;
    no_spend: number;
    precision_spenders: number | null;
    bleed_sum: number | null;
    sentence: string;
  };
  data_note: string;
}

export function fetchNaverPerformanceTimeline(
  params: { days?: number; campaignId?: string } = {},
): Promise<NaverPerformanceTimeline> {
  const q = new URLSearchParams();
  if (params.days !== undefined) q.set("days", String(params.days));
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  const qs = q.toString();
  return fetchApi<NaverPerformanceTimeline>(
    `/api/naver/ad/performance/timeline${qs ? `?${qs}` : ""}`,
  );
}

// ── D-NAO-140 S2: 소재(광고)별 성과 ──
// ★이 축이 없으면 캠페인 평균이 적자 소재를 가린다(2026-08-03 실측: 캠페인 03 ROAS 2.07~3.26
//   인데 그 안의 소재 하나는 0.61 — 3일 10.4만원 써서 6.4만원).
export interface NaverCreativeRow {
  ad_id: string;
  campaign_id: string;
  /** 이름이 없으면 null — 화면이 ID 폴백을 스스로 정한다(ID를 이름 자리에 넣지 않는다). */
  campaign_name: string | null;
  adgroup_id: string;
  adgroup_name: string | null;
  mall_product_id: string | null;
  product_name: string | null;
  /** 소재 개별 입찰. use_group_bid_amt=true면 그룹 입찰이 실효라 이 값은 참고용. */
  bid_amt: number | null;
  use_group_bid_amt: boolean | null;
  /** 이 구간에서 성과가 관측된 날 수(구간 길이와 다를 수 있다). */
  days: number;
  imp: number;
  clk: number;
  cost: number;
  conv: number;
  rev: number;
  /** 비용 0이면 null — 0으로 적으면 '수익이 0'으로 읽힌다. */
  roas: number | null;
  bep_roas: number | null;
  bep_gap: number | null;
  /** ★3상태: 넘음/미달/**판정불가(null)**. BEP를 모르면 판정하지 않는다. */
  verdict: "above" | "below" | null;
}

export interface NaverCreativeResponse {
  window: { since: string; until: string };
  total: number;
  /** 페이지·필터와 무관한 구간 전체 합계 — 한 페이지만 보고 '이게 전부'로 읽지 않게. */
  totals: { ads: number; cost: number; rev: number; clk: number };
  rows: NaverCreativeRow[];
}

export function fetchNaverCreatives(params: {
  date_from?: string;
  date_to?: string;
  days?: number;
  campaign_id?: string;
  sort?: "cost" | "imp" | "clk";
  limit?: number;
  offset?: number;
} = {}): Promise<NaverCreativeResponse> {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v != null) q.set(k, String(v)); });
  return fetchApi(`/api/naver/ad/creatives?${q.toString()}`);
}

// ──────────────────────────────────────────────
// 쿠팡 광고 설정 변경 이력 (트랙 coupang-ad-change-log, D-CAC-3/4)
// ★네이버와 결정적으로 다른 점: 쿠팡은 **모든 변경이 외부다**(우리가 쿠팡 광고를 쓰는 경로가
//   없다). 그래서 '주체' 축이 아예 없다 — isAgencyManaged는 현재 관리 주체지 변경 주체가 아니다.
// ──────────────────────────────────────────────
export type CoupangAdAccount = "ofix" | "ohitech";
export type CoupangAdChangeOp =
  | "created" | "turned_on" | "turned_off" | "deleted" | "field_change"
  // 소재(광고 상품) 축. ads_changed = 쿠팡이 준 개수 변화(VIID),
  // ads_added/removed = 우리 스냅샷이 잡은 증감(쿠팡 이벤트에 못 붙었을 때).
  | "ads_changed" | "ads_added" | "ads_removed";

/** 소재 변경에 붙는 옵션ID 목록. ★없을 수 있다 — 과거 이벤트는 쿠팡이 개수만 줬다. */
export interface CoupangAdChangeDetail {
  count?: number;
  added?: number;
  removed?: number;
  /** 목록이 붙었으면 그게 added인지 removed인지. */
  options_of?: "added" | "removed";
  options?: { vendor_item_id: string | null; item_name: string }[];
  truncated?: boolean;
}

export interface CoupangAdChangeRow {
  id: number;
  account: CoupangAdAccount;
  entity_type: "campaign" | "adgroup" | "ad";
  entity_id: string;
  campaign_id: string;
  entity_name: string;
  op: CoupangAdChangeOp;
  /** field_change일 때만. 그 외에는 null. */
  field: string | null;
  before_value: string | null;
  after_value: string | null;
  /** KST ISO. time_basis에 따라 의미가 다르다 — 아래 참조. */
  occurred_at: string;
  /**
   * "src" = 쿠팡이 준 updatedAt = **진짜 발생 시각**.
   * "detected" = 우리가 알아챈 시각(쿠팡이 시각을 안 준 경우).
   * ★화면은 이 둘을 반드시 구분해 보여야 한다. 네이버에서 감지일로 귀속했다가
   *   07-30 변경을 08-03으로 잡은 실사고가 있었다.
   */
  time_basis: "src" | "detected";
  detected_at: string | null;
  /** "coupang" = 쿠팡 변경 이력 API(전/후 값·실행 시각) / "snapshot" = 우리 스냅샷 diff. */
  source: "coupang" | "snapshot";
  /** 소재 변경의 옵션ID 목록 등. 없으면 null. */
  detail: CoupangAdChangeDetail | null;
}

export interface CoupangAdChangesResponse {
  period: { from: string; to: string; tz: "KST" };
  account: CoupangAdAccount | null;
  count: number;
  /** 마지막으로 설정을 관측한 시각(KST). null이면 아직 한 번도 안 봤다 — 신선도 표면화. */
  last_observed_at: string | null;
  items: CoupangAdChangeRow[];
}

export function fetchCoupangAdChanges(params: {
  /** KST 날짜 YYYY-MM-DD, 양끝 포함. */
  from?: string;
  to?: string;
  account?: CoupangAdAccount;
} = {}): Promise<CoupangAdChangesResponse> {
  const q = new URLSearchParams();
  if (params.from) q.set("from", params.from);
  if (params.to) q.set("to", params.to);
  if (params.account) q.set("account", params.account);
  const qs = q.toString();
  return fetchApi<CoupangAdChangesResponse>(
    `/api/coupang/ops/ad-changes${qs ? `?${qs}` : ""}`,
  );
}
