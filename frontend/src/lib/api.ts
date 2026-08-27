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
  // revenue_rg_gross = 우리 gross 주문 원장(수집 대조용 진단값 — 매출 아님, D-CPP-49).
  ours: {
    revenue_3p: string; revenue_rg: string; revenue_total: string;
    revenue_rg_gross?: string;
  } | null;
  // RG 행이 «같은 축끼리의 비교»가 됐음을 알리는 플래그 — 드리프트 0을 「정합」으로 오독하지 않게.
  rg_same_axis?: boolean;
  drift: {
    abs_3p: string; abs_rg: string; abs_total: string;
    pct_3p: string | null; pct_rg: string | null; pct_total: string | null;
    // ★우리 gross 원장 vs 콘솔 net — 구 `*_rg`가 재던 수집 간극을 이어받은 칸(D-CPP-49).
    abs_rg_gross?: string; pct_rg_gross?: string | null;
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
  // ★D-CPP-35 버퍼 차단 사유. 이 둘이 없으면 백엔드가 거부해도 **화면이 아무 말도 안 한다**
  //   (적대 리뷰 P1: 응답엔 있는데 소비자가 안 읽어 사유가 한 칸 옆에서 다시 사라졌다).
  cost_buffers: string[];
  cost_guard_unavailable: string | null;
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
  // ★status='success'인데 덜 들어온 경우가 있다(부분수집, D-NAO-202). 초록만 보면 놓친다.
  error_message?: string | null;
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
  // ── D-22: 카드도 요약표와 같은 모집단을 말한다(같은 백엔드 경로에서 나온다) ──
  net_scope?: NetScope;
  net_floor_ad?: number;   // 손익을 못 잰 채 광고비만 반영된 금액
}

/**
 * 이 행의 순이익이 **무엇을 담고 있는지** (D-22, 2026-08-19).
 *  full    = 매출·원가·수수료·광고비까지 다 반영
 *  ad_only = 손익을 못 재는 구간이라 확정 비용인 **광고비만** 반영된 하한
 *  partial = 위 둘이 섞인 소계
 * 이 값을 화면에서 안 읽으면, 하한을 완전한 손익으로 오독한다.
 */
export type NetScope = "full" | "ad_only" | "partial";

// ── KPI 카드 근거 (계약 CONTRACT_kpi_evidence_page.md, 2026-08-23) ──────────────
// ★`deductions`의 값이 **null이면 「0원」이 아니라 「모른다」**이다 — RG·로켓1P 행은 분해
//   항목을 원래 다 갖고 있지 않다. 화면은 이 둘을 반드시 다르게 그려야 한다(0으로 그리면
//   「원가 0원」이라는 거짓말이 되고, 그게 순이익을 부풀려 보이게 한 실제 결함 모양이다).
export type KpiMetric = "revenue" | "net_profit" | "profit_rate" | "order_count";

export interface KpiEvidenceRow extends Record<string, unknown> {
  channel_id: number | null;
  channel_name: string;
  company: string | null;
  label: string;
  revenue: string;
  product_revenue: string | null;
  shipping_revenue: string | null;
  deductions: Record<string, string | null>;
  missing: string[];
  net_profit: string | null;
  net_scope: NetScope | null;
  net_floor_ad: string;
  net_basis_revenue: string;
  unmapped_revenue: string | null;
  residual: string | null;
  explains_net: boolean;
  order_count: number;
  counted_in_order_card: boolean;
  revenue_basis: string | null;
}

export interface KpiEvidence extends Record<string, unknown> {
  date_from: string;
  date_to: string;
  rocket_basis: RocketBasis;
  rows: KpiEvidenceRow[];
  deduction_keys: string[];
  deduction_totals: Record<string, string>;
  deduction_unknown_rows: Record<string, number>;
  totals: {
    revenue: string;
    net_profit: string;
    basis_revenue: string;
    floor_ad: string;
    profit_rate: string;
    order_count: number;
    residual: string;
    unmeasured_revenue: string;
  };
  checks: {
    revenue_matches: boolean;
    net_matches: boolean;
    order_count_matches: boolean;
    net_fully_explained: boolean;
    // 네 칸이 전부 자기 배지를 갖는다 — 이익률만 빠져 있던 비대칭을 2026-08-23에 닫았다.
    profit_rate_matches: boolean;
  };
  order_count_excluded: number;
  has_floor: boolean;
}

export function fetchKpiEvidence(
  dateFrom: string,
  dateTo: string,
  rocketBasis: RocketBasis,
): Promise<KpiEvidence> {
  return fetchApi<KpiEvidence>(
    `/api/dashboard/kpi/evidence?date_from=${dateFrom}&date_to=${dateTo}` +
      `&rocket_basis=${rocketBasis}`,
  );
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
  net_profit: number | null;  // null = 잴 것이 아무것도 없다(매출·광고비 둘 다 0)
  profit_rate: number | null;
  order_count: number;
  // ── 순이익이 무엇을 담고 있나 (D-22) ──
  net_scope?: NetScope;
  net_floor_ad?: number;       // 그중 「광고비만 반영된 하한」으로 들어간 광고비
  net_basis_revenue?: number;  // 이익률의 분모(= 손익을 실제로 잰 매출). 총매출과 다를 수 있다
  unmapped_revenue?: number;   // 원가를 못 붙인 제품매출 — 이익률을 위로 부풀린다
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
// cost_drift: 원가 정본 드리프트(2026-08-10 배선). `product_master.cost_price`가
//   «원가표 정본 + 알려진 버퍼»인 건수. 드리프트가 없으면 백엔드가 **null**을 준다.
//   ★다른 항목과 종류가 다르다 — 나머지는 «파이프라인이 살아 있나», 이건 «값이 맞나»다.
//     옛 매핑 엑셀을 올리면 177건이 조용히 되돌아오고 이익만 줄어든다(에러가 안 난다).
export interface SchedulerHealthCostDrift {
  count: number;                        // 버퍼가 얹힌 건수
  by_buffer: Record<string, number>;    // {버퍼라벨: 건수} — 많은 순
  sample: { internal_sku: string; product_name: string | null; cost_price: number; truth: number }[];
  ok: number;                           // 정본과 일치
  undetermined: number;                 // ★«정상»에 합치지 않는다 — 합치면 드리프트가 묻힌다
  source: string;                       // 어느 원가표로 판정했나 (파일명 + sha)
}
// disk_low: 디스크 여유 감시. ★백엔드는 2026-08-03 ENOSPC 사고 후 이걸 내내 주고 있었는데
//   **프론트에 타입도 배너 분기도 없었다** — healthy=false를 만들면서 화면은 조용했다
//   (2026-08-10 prod 실측: 93.8%로 unhealthy인데 배너 0건). 그래서 타입부터 세운다.
export interface SchedulerHealthDiskLow {
  path: string;
  state: string; // low
  used_percent: number;
  warn_percent: number;
  free_bytes: number;
  total_bytes: number;
  impact: string; // 돈/운영 영향 한글 라벨 (그대로 노출)
  reason?: string;
}
// vendor_item_conservation: 쿠팡 판매분석 두 축의 보존식(D-CPP-36). Σ옵션 GMV == 요약축 GMV.
//   ★신선도(data_stale)와 종류가 다르다 — 저쪽은 «언제 것인가», 이쪽은 «합이 맞는가»다.
//     제때 와도 합이 안 맞으면 두 축이 서로 다른 것을 세고 있다는 뜻이고, 그 상태로 옵션별
//     손익을 쓰면 조용히 틀린다. 대조 자체를 못 했으면 백엔드가 **null**을 준다.
//   `summary_only`는 «옵션 수집이 아직 안 온 날»이라 문제가 아니다(신선도가 이미 본다).
export interface SchedulerHealthConservation {
  window: { start: string; end: string };
  compared: number; // 양쪽에 데이터가 다 있어 실제로 비교한 (일자, 유형) 칸 수
  mismatch: {
    account_key: string;
    date: string;
    registration_type: string;
    option_gmv: number;
    summary_gmv: number;
    diff: number;
  }[];
  summary_only: { account_key: string; date: string; registration_type: string; summary_gmv: number }[];
  option_only: { account_key: string; date: string; registration_type: string; option_gmv: number }[];
}
// exclusion_survival: 검색어 제외 조치가 아직 걸려 있는가(D-NAO-173 P1-①, exclusion_survival.py의
//   survival_summary 반환 그대로). ★다른 항목과 또 다르다 — 나머지는 «우리 파이프라인»의 상태고,
//   이건 **네이버 콘솔에 우리가 건 조치**가 사라졌는가다. 대행사가 되돌린 사례가 2회 있었고
//   그중 1회는 change_log에 흔적조차 없었다(라이브 재조회로만 발견) — 그래서 배너가 유일한 감시망이다.
// exclusion_slots: 제외 슬롯이 «몇 칸 남았는가»(S6-a, ref 66 §5). 위 survival과 **반대 방향의
//   고장**이다 — 조치는 멀쩡히 걸려 있는데 더 걸 칸이 없다. 그룹당 70칸(네이버 제약)이고
//   70/70이면 그 그룹의 음의 레버가 소멸한다. 파이프라인도 값도 정상이라 다른 감시엔 안 잡힌다.
export interface SchedulerHealthExclusionSlots {
  cap: number;
  groups: number;
  exhausted: number;
  // ★«못 셌다»는 0이 아니다 — 이 칸을 안 보면 조회가 죽은 그룹이 «잔여 70칸»으로 보인다.
  unknown: number;
  stale: number;
  healthy: boolean;
  rows: {
    adgroup_id: string;
    campaign_id: string;
    name: string;
    state: string; // exhausted | unknown | stale | ok
    used: number | null; // null = 못 셌다
    cap: number;
    remaining: number | null;
    usage_pct: number | null;
    ours: number;
    agency: number;
    other_source: number;
    unattributed: number | null;
    exhaust_eta_days: number | null;
    // ★예상일을 못 낼 때 «왜»가 여기 온다 — 빈칸은 왜 비었는지 말하지 않는다.
    exhaust_eta_reason: string;
  }[];
  // 목록은 상한(20건)까지만 실린다 — 잘렸다는 사실이 숨지 않게 총계를 따로 받는다.
  rows_truncated: number;
  reclaim_note?: string;
}

export interface SchedulerHealthExclusionSurvival {
  monitored: number;
  alive: number;
  breached: {
    campaign_id: string | null;
    adgroup_id: string | null;
    search_term: string | null;
    live_state: string | null; // alive | missing | deleted | unknown
    live_note: string | null;
    excluded_at: string | null;
    cost_at_exclusion: number | null;
  }[];
  // 잘린 목록의 총계 — breached는 상한(20건)까지만 실린다. 구버전 백엔드 안전을 위해 optional.
  breached_total?: number;
  never_checked: number;
  // 그중 «대조 주기를 넘겼는데도 여태 안 본» 건수. 방금 실행한 제외는 여기 안 들어간다
  // (그걸 이상으로 세면 제외 한 건마다 다음 날까지 배너가 빨강이 된다).
  never_checked_due?: number;
  last_checked_at: string | null;
  stale_hours: number;
  stale: boolean;
  healthy: boolean;
  revert_howto: string;
  impact: string;
  as_of: string;
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
  disk_low?: SchedulerHealthDiskLow[];          // 구백엔드 안전을 위해 optional
  cost_drift?: SchedulerHealthCostDrift | null; // 구백엔드 안전을 위해 optional · 정상이면 null
  // 구백엔드 안전을 위해 optional · 대조 불가면 null · 정상이면 mismatch=[]
  vendor_item_conservation?: SchedulerHealthConservation | null;
  // 구백엔드 안전을 위해 optional · monitored=0(대상 없음)이어도 healthy=true로 온다
  // 백엔드는 요약 자체를 못 했을 때 명시적으로 null을 준다(cost_drift·vendor_item_conservation과 같은 관례).
  exclusion_survival?: SchedulerHealthExclusionSurvival | null;
  // 제외 슬롯 사용률(S6-a) — 구백엔드 안전을 위해 optional · 집계 불가면 null.
  exclusion_slots?: SchedulerHealthExclusionSlots | null;
  // 광고비 괴리(D-CPP-46) — 구백엔드 안전을 위해 optional · 대조 불가면 null.
  ad_cost_divergence?: SchedulerHealthAdCostDivergence | null;
  // 부분수집(D-NAO-204) — 주문 수집이 status='success'로 끝났는데 실제로는 덜 들어온 상태.
  // 구백엔드 안전을 위해 optional · 조회 불가면 null · 이상 없으면 [].
  partial_sync?: SchedulerHealthPartialSync[] | null;
  as_of: string;
}

// 부분수집 1건 = sync_log 1행. `detail`은 백엔드 원문 그대로다 — 여기서 요약하면
// «어느 날이 덜 들어왔나»가 사라지고, 그게 재수집 대상을 고르는 유일한 좌표다.
export interface SchedulerHealthPartialSync {
  sync_log_id: number;
  channel_id: number;
  channel_name: string;
  at: string | null;
  records_synced: number;
  detail: string;
}

// 쿠팡이 정산에서 뗀 광고비 ↔ 우리가 손익에서 뺀 광고비(D-CPP-46).
// ★`ratio`는 verdict가 ok일 때도 온다 — 임계에 다가가는 과정을 볼 수 있어야 한다.
//   `pipe_stopped`·`insufficient_data`에서는 분모가 없어 null이다.
export interface SchedulerHealthAdCostDivergence {
  window: { start: string | null; end: string | null };
  pa_spend: number;      // 옵션축 PA
  nonpa_spend: number;   // 계정축 비-PA(all_day_cost − day_cost)
  deducted: number;      // pa_spend + nonpa_spend = 우리가 손익에서 뺀 광고비
  settled: number;       // 쿠팡이 정산에서 공제한 광고비(sentinel 행)
  ratio: number | null;  // settled / deducted
  max_ratio: number;
  account_key: string;
  verdict: "ok" | "diverged" | "pipe_stopped" | "insufficient_data";
  reason?: string;
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
  // D-CPP-32: 수수료의 «근거 등급». 값만 있으면 화면이 실토할 수 없다.
  fee_rate?: string;            // 이 옵션에 적용한 요율(소수. 0.078 = 7.8%)
  fee_basis?: 'settled_rate' | 'default_rate';
  fee_base?: string;            // 과세표준 = 3P매출 − 반품차감 (매출과 다르다: 1P·RG 제외)
  settled_fee?: string;         // 참고: 창 안에 정산 «인식»된 실측(축이 달라 직접 비교 금지)
  settled_fee_rows?: number;
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
      // D-CPP-32: 수수료 근거 실토 — 요율을 아는 옵션/모르는 옵션과 그 금액
      fee_rate_known_options?: number;
      fee_rate_default_options?: number;
      fee_default_revenue?: string;    // 기본 7.8%로 «추정»한 과세표준
      fee_base_total?: string;         // 과세표준 합계(3P만)
      settled_fee_recognized?: string; // 창 안 정산 인식 실측(참고·축 다름)
      fee_check?: {                    // 전제 검증: 정산된 라인에서 계산==실측 인가
        checked_lines: number; computed: string; actual: string;
        diff: string; max_line_diff: string; max_line_excess?: string;
        refunded_lines_skipped?: number;
      };
      fee_base_clamped_options?: number;
      // D-CPP-33: 배송 수입(순이익에만 반영·매출 축 불변) · 납부세액 · 반품 억제 실토
      seller_shipping_3p?: string;
      shipping_income_3p?: string;
      shipment_count_3p?: number;
      payable_vat?: string;
      net_profit_pre_vat?: string;
      return_suppression?: {
        return_rows: number; deducted_rows: number;
        suppressed_excluded_rows: number; suppressed_orphan_rows: number;
      };
      // S3/S7(정합성 트랙): 매출 분해 — 쿠팡 판매분석 수동 대조용. revenue = revenue_3p + revenue_rg.
      revenue_3p?: string;            // 마켓플레이스(Wing) 3P 매출
      // ★D-CPP-49(계약 ⓑ): 원천이 gross 주문 원장 → **콘솔 net 옵션축**으로 바뀌었다.
      //   대시보드 「로켓그로스」 행과 같은 축이라 두 화면 숫자가 일치한다.
      revenue_rg?: string;            // 로켓그로스 매출(콘솔 net)
      revenue_rg_basis?: 'console_net';
      revenue_rg_gross?: string;      // 우리 gross 주문 원장 — **매출이 아니다**(수집 대조 진단값)
      rg_option_axis_days?: string | null;      // "16/16" — 옵션축이 창을 덮은 날짜 수
      rg_option_axis_complete?: boolean | null; // false면 revenue_rg는 부분치(빈 날은 0원 아닌 «미상»)
      rg_open_days?: number | null;             // 아직 콘솔이 안 닫은 날 수(오늘 등) — 경고가 아니라 사실
      net_profit_basis?: string;      // 순이익 날짜축 설명(D-9 투명화)
      // S7(D-14/D-16): RG 정산 비용 net_profit 플립 브리지 필드(계정 단위, 전액 차감)
      net_profit_pre_rg?: string;     // 플립 전 순이익
      rg_settlement_total?: string;   // RG 정산 총액(VAT後, 광고 포함) — 표시·검산용
      rg_settlement_deducted?: string; // ★net_profit에서 실제 차감된 값(= 총액 − 광고비, D-CPP-43)
      rg_ad_settlement?: string;      // 표시: 전액 중 광고분(D-16 라이브 조사)
      rg_non_ad_deducted?: string;    // 표시: 전액 중 광고 제외 브레이크다운
      rg_flip_status?: 'applied_ex_ad' | 'applied_full' | 'not_applied_no_data';  // D-CPP-43: ex_ad가 현재값
      ad_nonpa_deducted?: string;     // S5a/D-15: 비-PA(전체−집행) net_profit 추가 차감분
      // ── 정산공제의 축·근거 (CONTRACT_rg_sales_date_axis §4 ⓑⓒⓓⓔ, 2026-08-22) ──
      // sales_date = 그 창에 «판 것»에 붙는 공제 / recognition_date = 정산 주기 통짜(못 잰 경우)
      rg_settlement_axis?: 'sales_date' | 'recognition_date';
      rg_fee_basis?: 'settled_rate' | 'rate_unknown';
      rg_fee_rate?: string | number | null;      // 판매수수료 요율(비율 0~1, VAT 포함)
      rg_fee_coverage?: string | number | null;  // 0~1. 물류비 «단가»를 아는 매출의 비율
      rg_fee_unmapped_revenue?: string | number | null;  // 단가를 몰라 0으로 «안 채운» 매출
      rg_fee_reconcile?: {                       // 완결 주기에서 이 방식 vs 원장 실청구액
        cycle_from: string; cycle_to: string;
        computed: string; actual: string; diff: string; diff_pct: string | null;
      } | null;
    };
    by_option: OverviewAccountRow[];
  };
  ad: {
    summary: {
      ad_spend: string; impressions: number; clicks: number;
      conv_revenue: string; roas: string | null;
      // S5a/D-15: report/SALES vendor-level 권위값(쿠팡 광고센터 0.02% 일치). ad_spend는 옵션 rollup.
      // ★광고센터(report/SALES) 소스는 «광고주 단위»라 광고주 계정(오픽스)에만 적용된다.
      //   적용 안 되는 계정(오하이테크 등)에선 백엔드가 **null**을 준다 — 0이 아니다.
      //   0으로 주면 「측정된 0원」과 구별이 안 돼 화면이 «광고를 안 썼다»고 단정한다
      //   (2026-08-11 실제로 그 상태였다: 위쪽 0원 · 아래쪽 11,247원).
      ad_confirmed_applies?: boolean; // 이 계정에 광고센터 소스가 적용되나
      ad_confirmed_pa?: string | null;    // 집행(DELIVERED, 상품검색광고/PA) · 미적용이면 null
      ad_confirmed_total?: string | null; // 전체(ALL_DELIVERED, 비-PA 포함) · 미적용이면 null
      ad_confirmed_nonpa?: string | null; // 비-PA(전체−집행) = net_profit 추가 차감 · 미적용이면 null
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
      // D-CPP-43: 'applied_full'(광고 포함 전액 차감)은 폐기됐다. 옛 응답 호환으로 유니온엔 남긴다.
      flip_status?: 'applied_ex_ad' | 'applied_full' | 'not_applied_no_data';
      deducted?: string;             // ★net_profit에서 실제 차감된 값(= 광고 제외분, D-CPP-43)
      // ★2026-08-22 판매일 축 전환: `deducted`는 이제 **축을 탄다**. 어느 축인지 이 칸이 말한다 —
      //   안 밝히면 헤드라인(실제 차감)과 아래 계정 카드(정산 원장 축)가 근거 없이 갈린다.
      axis?: 'sales_date' | 'recognition_date';
      non_ad_deducted?: string;      // **원장 축**(정산 인식일) 광고 제외분 — 대조용(deducted와 다를 수 있다)
      ad_settlement?: string;        // 정산 광고비 = 광고센터 PA의 «공제». **차감 안 함**(표시 전용)
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
  /** 이 화면 범위 밖으로 «의도적으로 뺀» 광고비 = 1P(로켓배송). 매출이 orders에 없어 같이 못 센다. */
  excluded_ad_spend?: string;
  excluded_ad_conv?: string;
  /** 일별 폴백분(옵션 분해 없음). by_sell_type 합 + 이 값 = ad_spend. */
  ad_spend_unassigned?: string;
}

/** 판매유형별 분해 — 3P(Wing)와 2P(로켓그로스)는 쿠팡이 가져가는 몫이 두 배 넘게 다르다. */
export interface SalesSellTypeRow {
  /** "3P" | "2P" | null(미분류 — 일별 집계라 판매유형으로 못 가른 금액) */
  sell_type: string | null;
  channel_type: string;   // "Wing" | "로켓그로스" | "미분류"
  revenue: string; fee: string; cost: string;
  ad_spend: string; shipping: string;
  profit: string; profit_rate: string | null;
  conv_revenue: string; roas: string | null;
}

export interface SalesSummary {
  period: { from: string; to: string };
  ad_ref_date: string | null;
  summary: SalesSummaryData;
  by_sell_type?: SalesSellTypeRow[];
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

/** 쿠팡 운영 패널 매출 요약.
 *
 *  ★기간 규칙은 백엔드 `utils/date_range.py` 한 곳에 있다: **날짜 둘을 주면 그게 이기고**,
 *  없으면 `days` 프리셋을 쓴다. 한쪽만 주면 400이므로 여기서도 **둘 다 있을 때만** 날짜로
 *  보낸다 — 반쪽짜리 요청을 만들어 백엔드에 판정을 미루지 않는다.
 *  (네이버 쪽 `fetchNaverSalesSummary`와 같은 모양이다 — 두 입구가 갈라지지 않게.) */
export function fetchSalesSummary(
  company: string,
  days: number,
  dateFrom?: string | null,
  dateTo?: string | null,
): Promise<SalesSummary> {
  const q = dateFrom && dateTo
    ? `date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`
    : `days=${days}`;
  return fetchApi<SalesSummary>(
    `/api/coupang/ops/sales-summary?company=${encodeURIComponent(company)}&${q}`
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
  excluded_order_amount: string;      // 「연결 안 함」으로 정한 금액 — **미해결분**(원가 0이 아니다)
  confirmed_sku_count: number;
  /** ★「연결 안 함」으로 정한 SKU 수. **해결분이 아니라 미해결분**이다(2026-08-10 전환) —
   *  `coverage_pct`·`resolved_order_amount`에 들어가지 않는다. */
  excluded_sku_count: number;
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
  /** ★`excluded`는 **원가 0이 아니라 미상**이다(2026-08-10). 종전 `ignored`가 원가 0을
   *  뜻해 매칭 실패분이 전액 이익으로 잡혔다 — 그 해석은 없앴다. */
  cost_source?: "sellc" | "auto_map" | "excluded" | null;
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

// ── 로켓1P 열린 파이프라인 (계약 1P계산서 목표 S1·S2) ──
// ★백엔드에 pydantic 응답 모델이 없다(서비스가 plain dict, 라우터가 `_jsonify`로 Decimal→str).
//   그래서 이 인터페이스가 사실상 응답 계약이고 **수동 동기화**다 — 서비스 dict의 키를 바꾸면
//   여기도 같이 바꿔야 하고, 안 바꾸면 타입은 초록인데 화면이 undefined를 그린다.
//   금액은 전부 Decimal → **문자열**로 온다(정밀도 보존). 표시 직전에만 숫자로 바꿀 것.

/** 파이프라인 칸 키 — 백엔드 `rocket_pipeline.STAGE_*`와 1:1. */
export type RocketPipelineStageKey =
  | "await_confirm" | "await_ship" | "await_receive" | "await_payment";

/** ①②③ 칸 — PO 그레인. `amount` = 그 칸에 걸린 금액(VAT 포함 gross). */
export interface RocketPipelinePoStage {
  key: Exclude<RocketPipelineStageKey, "await_payment">;
  po_count: number;
  amount: string;
  /** 마지막 수집일에 갱신된 분 — 「지금 참인 상태」로 볼 수 있는 금액. */
  fresh_amount: string;
  /** 수집 창 밖이라 상태가 굳은 분 — 이미 처리됐을 수 있다. */
  stale_amount: string;
  stale_po_count: number;
  oldest_stale_synced_date: string | null;
}

/** ④ 지급 대기 — **계산서 그레인**이라 ①②③ 소계에 안 들어간다(더하면 이중계상). */
export interface RocketPipelinePaymentStage {
  key: "await_payment";
  invoice_count: number;
  amount: string;
  next_payment_date: string | null;
  last_payment_date: string | null;
}

export type RocketPipelineStage = RocketPipelinePoStage | RocketPipelinePaymentStage;

export function isPoStage(s: RocketPipelineStage): s is RocketPipelinePoStage {
  return s.key !== "await_payment";
}

export interface RocketPipelineRow {
  purchase_order_seq: number;
  status: string | null;
  status_label: string | null;
  po_date: string | null;
  receiving_finished_date: string | null;
  synced_date: string | null;
  order_qty: number;
  confirmed_qty: number;
  received_qty: number;
  shipped_qty: number;
  order_amount: string;
  confirmed_amount: string;
  shipped_amount: string;
  received_amount: string;
  /** max(발송, 입고) — 입고는 발송의 하한이다(ASN 미수집 PO를 「안 보냄」으로 읽지 않기 위해). */
  effective_shipped_amount: string;
  /** 입고는 있는데 ASN 라인 0건 = **발송 기록 없음**(안 보낸 것이 아니다). */
  asn_missing: boolean;
  unpriced_shipped_qty: number;
  invoice_seqs: number[];
  has_invoice: boolean;
  center_name: string | null;
  first_sku_name: string | null;
  sku_count: number;
  unshipped_raw: string;
  unreceived_raw: string;
  /** 이 칸에 계상된 금액. PO 총액이 아니다. */
  stage_amount: string;
  /** 마지막 수집일과 이 PO의 수집일이 다름 = 「지금 참인 상태」가 아닐 수 있다. */
  is_stale: boolean;
}

export interface RocketPipeline {
  as_of_kst: string;
  ship_window: {
    from: string | null; to: string | null;
    applies_to: "await_receive";
    /** 창 밖 발송이 섞인 PO 수 — 금액은 PO 전체 기준이라는 자백. */
    po_with_out_of_window_shipment: number;
  } | null;
  stages: RocketPipelineStage[];
  pre_invoice_subtotal: { amount: string; stages: string[] };
  /** 확정했는데 발송 없이 닫힘 = 영영 못 보내는 분. */
  closed_unshipped: { po_count: number; amount: string };
  /** ★발송 신고 > 쿠팡 인정 입고. `confirmed:false` — 소계에 넣으면 안 된다. */
  unexplained: {
    po_count: number; amount: string;
    oldest_po_date: string | null; newest_po_date: string | null;
    confirmed: false; reason: string;
  };
  clamp: {
    over_shipped: { po_count: number; amount: string };
    over_received: { po_count: number; amount: string };
    asn_missing: { po_count: number; received_amount: string };
  };
  /** ★실측 4종(RP/PA/RI/CI) 밖의 상태 코드 — 어느 칸에도 안 들어간 «모르는 돈». */
  unknown_status: { po_count: number; confirmed_amount: string; codes: string[] };
  unpriced_shipped_qty: number;
  last_collection_date_kst: string | null;
  freshness: {
    po_synced_at_kst: string | null;
    shipment_synced_at_kst: string | null;
    latest_shipped_date_kst: string | null;
    note: string;
  };
}

export interface RocketPipelineRows {
  stage: string;
  total_count: number;
  rows: RocketPipelineRow[];
  /** true면 목록이 잘렸다 — 조용한 절단 금지(화면이 말해야 한다). */
  truncated: boolean;
  last_collection_date_kst: string | null;
}

export interface RocketRiInvoice {
  invoice_seq: number;
  issue_date: string | null;
  payment_date: string | null;
  tax_invoice_confirmed_date: string | null;
  /** true=전송성공 표기 / false=**미표기**(실패 아님) / null=판별 불가. */
  tax_invoice_transmitted: boolean | null;
  payment_amount: string;
}

export interface RocketRiRow extends Omit<RocketPipelineRow, "stage_amount"> {
  invoices: RocketRiInvoice[];
  /** 번호는 있는데 정산행 미수집 = 「미발행」이 아니라 「모름」. */
  invoice_rows_missing: number[];
}

export interface RocketRiQueue {
  rows: RocketRiRow[];
  live_count: number;
  live_amount: string;
  stale_count: number;
  stale_amount: string;
  last_collection_date_kst: string | null;
  note: string;
}

export function fetchRocketPipeline(params: {
  shipFrom?: string | null; shipTo?: string | null;
} = {}): Promise<RocketPipeline> {
  const q = new URLSearchParams();
  if (params.shipFrom) q.set("ship_from", params.shipFrom);
  if (params.shipTo) q.set("ship_to", params.shipTo);
  const qs = q.toString();
  return fetchApi<RocketPipeline>(`/api/overview/rocket-pipeline${qs ? `?${qs}` : ""}`);
}

export function fetchRocketPipelineStage(
  stage: string,
  params: { shipFrom?: string | null; shipTo?: string | null } = {},
): Promise<RocketPipelineRows> {
  const q = new URLSearchParams();
  if (params.shipFrom) q.set("ship_from", params.shipFrom);
  if (params.shipTo) q.set("ship_to", params.shipTo);
  const qs = q.toString();
  return fetchApi<RocketPipelineRows>(
    `/api/overview/rocket-pipeline/stage/${encodeURIComponent(stage)}${qs ? `?${qs}` : ""}`,
  );
}

export function fetchRocketRiQueue(): Promise<RocketRiQueue> {
  return fetchApi<RocketRiQueue>("/api/overview/rocket-ri-queue");
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
   *  excluded = **연결 안 하기로 사람이 정함**(시키면 안 된다). ★손익에선 「모름」이고 원가 0원이 아니다 */
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
  /** ★`ignored`로 찍을 때 남은 사유 원문(excluded_top에만 온다). null = 사유 미기록.
   *  왜 필요한가: prod의 `ignored` 22건이 **전부** `'no suggestion or low score'`였다 —
   *  즉 «샘플·증정으로 결정»이 아니라 **이름 유사도 매칭 실패**를 같은 칸에 넣어 둔 것이다.
   *  화면이 사유를 안 보이면 매칭 실패가 «결정»으로 위장해 "그냥 두세요"로 안내된다. */
  excluded_note?: string | null;
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
  /** ★**팔린 옵션이 «안 팔린 날»에 쓴** 광고비. 원자는 판매행에서만 나오고 ad_no_sales는
   *  옵션 단위로 판정해서, 판정 그레인이 다른 그 사이로 새던 돈이다(창 07-31~08-06 435,916원).
   *  included 플래그는 ad_no_sales와 공유한다. */
  ad_no_sales_days: string;
  /** ★판매행은 있으나 **손익 부분집합에 못 들어간** 옵션의 광고비. is_full이면 정의상 0이고,
   *  0이 아닐 땐 순이익에 절대 넣지 않는다(부분집합 매출에서 전량 비용을 빼면 적자로 위조된다). */
  ad_uncosted: string;
  /** 위 셋의 합 = 「계정 총액과 사다리가 왜 다른가」의 답 전체. */
  ad_unattributed: string;
  /** ★구멍0 — **판매분석 롤링 창보다 앞선 날**의 광고비(D-CPP-38). 「안 팔림」이 아니라
   *  «관측 불가»다. 어떤 경우에도 차감하지 않는다. 이 통이 없으면 그 돈이 구멍2·3에 섞여
   *  「광고했는데 안 팔림」의 크기를 부풀린다(라이브 19,294,871 중 12,969,126이 이것). */
  ad_out_of_range: string;
  /** 구조적 두 통(구멍2·3)에 실제로 곱해진 비율. 종전엔 0 아니면 1이었다(절벽). */
  ad_deduct_share: string | null;
  /** 그 비율을 곱해 **실제로 차감된** 금액. `ad_spend` = `ad_attributed` + 이 값. */
  ad_folded_deducted: string;
  /** 원자에 붙은 **순수 귀속분**. `ad_spend`와 다르다(그건 차감된 구조적 몫을 품는다). */
  ad_attributed: string;
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
    excluded_skus: number;         // 「연결 안 함」으로 정한 것 — 시키면 안 된다(원가는 「모름」)
    loss_confirmed_skus: number;
    new_skus: number;              // ★이번 기간에 새로 팔리기 시작한 미연결 SKU
    new_our_revenue: string;
    new_sku_window_days: number;   // ★판정 지평(발주 첫 등장 기준). 숨은 기준을 두지 않는다.
    top: Rocket1PUncostedSku[];    // actionable만
    /** 「연결 안 함」으로 찍힌 SKU — 시키는 목록이 아니라 «그 결정이 아직 맞나» 재검토용. */
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
  ad_no_sales_days: string;        // 그날 팔린 옵션이되 **그날은** 판매행이 없던 옵션분. 하루 창이면 0
  ad_uncosted: string;             // 그날 팔렸지만 손익에 못 들어간 옵션분
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

// ── 로켓1P 손익 «근거 화면» (2026-08-07 설계, Jino: "우리 손익이 정말 실수 없이 나오는지
//   어떻게 확신할 수 있는지") ──
// ★verdict 3값: 판정할 수 없는 검사(B1 두 축 대사 등)는 pass가 아니라 undetermined다 —
//   거짓 초록 금지. 통과해도 좌·우변을 항상 싣는다(발견 0건과 실행 안 됨은 같은 숫자로 보인다).
export interface PnlAuditCheck {
  id: string; label: string;
  left: string | null; right: string | null; diff: string | null;
  unit: string; verdict: "pass" | "fail" | "undetermined"; note: string | null;
}
export interface PnlAuditLadder {
  basis: string | null; qty: number | null;
  revenue: string | null; cost: string | null; promo_burden: string | null;
  ad_spend: string | null; vat: string | null; net_profit: string | null;
  profit_rate: string | null; ad_no_sales: string; ad_no_sales_included: boolean;
  // ★A7(광고 원장 완결)의 좌변을 이루는 두 통 — 화면이 검사식을 재구성할 수 있어야 한다.
  ad_no_sales_days: string; ad_uncosted: string; ad_unattributed: string;
  // ★B3(옵션 축 ↔ 계정 확정액 대사)의 좌·우변 — 화면 pnl의 부분집합이라 여기도 항상 온다.
  ad_option_total: string; ad_account_total: string;
  cost_coverage: string | null; revenue_priced: string | null;
  blocked: { code: string; reason: string } | null;
}
export interface PnlAuditChecks {
  period: { from: string; to: string; vendor_id: string };
  ladder: PnlAuditLadder;
  checks: PnlAuditCheck[];
}
// ★원가 미상을 «none» 하나로 접지 않는다 — 할 일이 다르다: no_link(다리 자체가 없다 →
//   연결부터) / no_cost(다리는 있는데 원가가 없다) / unknown(다리·원가는 있는데 확정 방법이
//   기록에 없다) / excluded(연결 안 하기로 정함 — 원가는 「모름」) / manual·suggested(다리가 있고 원가도
//   붙은 경우의 확정 방법 — suggested=이름 유사도 자동, 사람 미확인).
export type PnlAuditCostSource =
  "manual" | "suggested" | "unknown" | "excluded" | "no_cost" | "no_link";
export interface PnlAuditAtom {
  date: string; option_id: string; sku_id: string | null; product_name: string | null;
  qty: number; consumer_revenue: string; our_revenue: string | null;
  unit_price: string | null; cost: string | null; unit_cost: string | null;
  ad_spend: string | null; promo_burden: string | null;
  net_profit: string | null; net_profit_upper: string | null;
  cost_source: PnlAuditCostSource;
}
export interface PnlAuditAtoms {
  period: { from: string; to: string };
  burden_known: boolean;
  // ★요청 파라미터 에코 — `totals`가 **필터 후** 행의 합이라, 무엇으로 걸렀는지 모르면
  //   부분합인지 전체합인지 알 수 없다.
  query: { sort: string; flt: string; option_id: string | null };
  count: number;   // 필터 후 반환 수
  total: number;   // 필터 전 원자 총수(목록 자체는 자르지 않는다)
  // ★잘림 계약: option_count > option_limit이면 화면 옵션 표가 잘려 A2·A7이 undetermined가
  //   된다 — 화면이 「검사 2개가 왜 사라졌는지」를 말할 수 있어야 한다.
  option_count: number; option_limit: number; option_table_truncated: boolean;
  // ★net_profit은 **아는 행만** 더한 값이고, 아는 행이 없으면 null이다(0이 아니다).
  // ★revenue_in_net/out_of_net — 순이익 합에 «반영된 매출»과 «빠진 매출»(부분집합을 전체로
  //   읽지 않게, subset-profit-overstates-margin 교훈). out_of_net_unknown은 빠진 행 중
  //   **우리 매출조차 모르는** 행 수 — 0으로 접으면 빠진 돈이 과소로 보인다.
  totals: { qty: number; net_profit: string | null;
            net_profit_known: number; net_profit_unknown: number;
            revenue_in_net: string | null; revenue_out_of_net: string | null;
            revenue_out_of_net_unknown: number };
  atoms: PnlAuditAtom[];
}
export interface PnlAuditAtomDetail {
  // ★화면이 보고 있는 창. atom.net_profit·burden_known·promo_burden이 **창-종속**이라,
  //   창을 모르면 응답만 보고 «모름이 숫자로 바뀌었는지» 판별할 수 없다(다른 두 엔드포인트와 같은 이유).
  period: { from: string; to: string };
  date: string; option_id: string;
  atom: {
    date: string; option_id: string; qty: number;
    our_revenue: string | null; cost: string | null; promo_burden: string | null;
    ad_spend: string | null; net_profit: string | null; net_profit_upper: string | null;
    burden_known: boolean;
  } | null;
  sales: {
    date: string; option_id: string; sku_id: string | null; product_name: string | null;
    qty: number; consumer_revenue: string | null; visitors: number | null;
    source: string; synced_at: string | null;
  } | null;
  unit_price: {
    purchase_order_seq: number; unit_purchase_price: string | null; order_qty: number | null;
    po_created_at: string | null; sibling_option_count: number; note: string;
  } | null;
  cost: {
    map: { product_number: string; internal_sku: string | null; status: string;
           match_method: string | null; note: string | null; updated_at: string | null } | null;
    master: { internal_sku: string; product_name: string | null; cost_price: string | null } | null;
  };
  // row_count = 접은 원천 행 수(유니크 키에 conv_option_id가 있어 한 (날짜,옵션)에 행이
  // 여럿일 수 있다 — 원자와 같은 방식으로 SUM한 것).
  ad: { report_date: string; ad_option_id: string; ad_spend: string | null;
        impressions: number | null; clicks: number | null; orders: number | null;
        sales_qty: number | null; row_count: number } | null;
  // ★null = 원천 테이블(프로모션·할인액) 중 하나라도 없어 «모름». 빈 배열과 다르다 — 빈
  //   배열은 «그날 걸린 프로모션이 없다»는 실측이고, null은 판정 자체가 불가능하다는 뜻이다.
  promos: Array<{ request_id: string; start_at: string | null; end_at: string | null;
                  discount_type: string | null; discount_value: string | null }> | null;
}

const _auditQ = (p: { from: string; to: string }) =>
  new URLSearchParams({ date_from: p.from, date_to: p.to });

export function fetchPnlAuditChecks(p: { from: string; to: string }): Promise<PnlAuditChecks> {
  return fetchApi<PnlAuditChecks>(`/api/coupang/ops/rocket/pnl-audit/checks?${_auditQ(p)}`);
}
export function fetchPnlAuditAtoms(p: {
  from: string; to: string; sort?: string; flt?: string; optionId?: string;
}): Promise<PnlAuditAtoms> {
  const q = _auditQ(p);
  if (p.sort) q.set("sort", p.sort);
  if (p.flt) q.set("flt", p.flt);
  if (p.optionId) q.set("option_id", p.optionId);
  return fetchApi<PnlAuditAtoms>(`/api/coupang/ops/rocket/pnl-audit/atoms?${q}`);
}
// ★from/to는 **화면이 보고 있는 창**이다 — 생략하면 안 된다. 창을 좁히면 분담금 «모름»
//   판정이 달라져 화면이 «—»로 그린 행에 숫자가 찍힌다(원자 파생의 창 종속성 계약).
export function fetchPnlAuditAtom(p: {
  date: string; optionId: string; from: string; to: string;
}): Promise<PnlAuditAtomDetail> {
  const q = new URLSearchParams({
    date: p.date, option_id: p.optionId, date_from: p.from, date_to: p.to,
  });
  return fetchApi<PnlAuditAtomDetail>(`/api/coupang/ops/rocket/pnl-audit/atom?${q}`);
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
  status: "confirmed" | "excluded";
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

/** 「연결 안 함」으로 정한다 — **사유 필수**.
 *
 *  ★왜 전용 함수인가(2026-08-10 적대 리뷰 P2-7): 화면이 `note`를 빼먹으면 백엔드가 422로
 *    거부해 **모든 제외 클릭이 조용히 실패**한다(화면엔 «❌ 제외 실패»만 뜬다). 백엔드
 *    테스트는 그걸 못 잡는다 — 백엔드는 정상 동작하기 때문이다. 계약을 여기 한 곳에 모아
 *    테스트가 물게 한다.
 *  ★`match_method='manual'`을 여기서 박는다 — 자동 계열을 넘기면 백엔드가 거부한다.
 */
export function excludeRocketCostMap(
  product_number: string, note: string,
): Promise<RocketMappingItem> {
  const reason = (note ?? "").trim();
  if (!reason) {
    return Promise.reject(new Error(
      "사유가 필요합니다 — 사유 없는 제외는 나중에 «결정»과 «매칭 실패»를 가를 수 없습니다",
    ));
  }
  return _postCostMap({
    product_number, status: "excluded", match_method: "manual", note: reason,
  });
}

/** 원가 매핑 확정. ★`status`가 **`"confirmed"`뿐**이라 여기로는 제외를 만들 수 없다.
 *
 *  ★왜 타입으로 막나(2026-08-10 적대 리뷰 2R F5): 화면이 `excludeRocketCostMap`을 안 쓰고
 *    이 함수로 되돌아가 `note` 없이 제외를 보내면 **모든 제외 클릭이 422로 죽는데** 테스트는
 *    전부 초록이었다(CommandCenter 렌더 테스트가 0건이라 호출부가 무방비다).
 *    테스트를 새로 쓰는 것보다 **컴파일이 막는 쪽**이 강하다 — 회귀가 코드로 불가능해진다.
 */
export function upsertRocketCostMap(body: {
  product_number: string;
  internal_sku?: string;
  status?: "confirmed";
  match_method?: string;
  note?: string;
}): Promise<RocketMappingItem> {
  return _postCostMap(body);
}

function _postCostMap(body: {
  product_number: string;
  internal_sku?: string;
  status?: "confirmed" | "excluded";
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
  // D-NAO-207: 광고비 중 상품에 실제로 붙은 몫 / 못 붙인 몫. 합 = ad_spend.
  ad_allocated?: string;
  ad_unallocated?: string;
}

/** D-NAO-207 — 상품에 못 붙인 몫. 표 맨 아래 한 행으로 낸다.
 *  ★정의가 «요약 − 배분합»(잔차)이라 새 누수 사유가 생겨도 반드시 여기 나타난다. */
export interface NaverUnallocated {
  ad_spend: string;      // 파워링크 + 디스플레이 + 원장 창 밖 + 오늘 프록시
  logistics: string;     // 상품ID 없는 라인만 든 패키지 + 클레임 회수비
  claim_income: string;
  claim_fee: string;
  profit: string;        // 대개 음수 — 비용만 있고 매출이 거의 없다
}

/** D-NAO-207 — 광고비 배분 진단. 배너가 이걸 읽어 «왜 못 붙였나»를 말한다. */
export interface NaverAdAlloc {
  ledger_from: string | null;   // 소재 원장(naver_ad_creative_daily) 보유 창
  ledger_to: string | null;
  uncovered_dates: string[];    // 조회 구간 중 원장이 없는 날 — 그 날은 배분이 원리적으로 0
  shopping_cost: string;        // 소재 원장의 SHOPPING 비용 합
  allocated: string;            // 그 중 상품에 붙은 몫
  unmapped_cost: string;        // 소재는 돌았는데 상품 매핑이 없다
  ambiguous_cost: string;       // ad_id가 두 상품에 매핑 → 고르지 않는다
  ambiguous_ads: number;
  // 광고는 나갔는데 이 기간 판매가 0건인 상품 — 미배분에 흡수되지만 덩어리로 숨기지 않는다.
  //   (적대 리뷰 1R P1이 드러낸 모집단 차이. 순수한 손실이라 몇 개·얼마인지 말한다.)
  no_sale_cost?: string;
  no_sale_products?: number;
}

/** D-NAO-207 — 화면 안의 자기 검산. closes=false면 표가 카드와 갈렸다는 뜻이다. */
export interface NaverReconciliation {
  sum_product_profit: string;
  unknown_cost_profit: string;   // 원가 미상이라 「—」로 비운 상품들의 몫
  unallocated_profit: string;
  summary_profit: string;
  residual: string;
  closes: boolean;
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
  unallocated?: NaverUnallocated;
  ad_alloc?: NaverAdAlloc;
  reconciliation?: NaverReconciliation;
}

export interface NaverSalesProductRow {
  product_name: string;
  platform_id: string;
  revenue: string; fee: string;
  // D-NAO-207: 상품 행이 매출총이익 → **순이익**으로 바뀌었다. 광고비(SHOPPING 실측 귀속분)와
  //   물류비(패키지 배분)가 붙고, 고객배송비가 매출에 들어간다. 총매출 축 = revenue_total.
  delivery_revenue?: string;
  revenue_total?: string;
  logistics?: string;
  ad_spend?: string;
  ad_allocated?: boolean;   // false면 이 상품엔 붙은 광고비가 0(파워링크만 돌았거나 원장 밖)
  // ★원가를 모르면 원가·이익·이익률이 전부 null이다 — «모름»을 0원으로 계산하지 않는다.
  //   (종전엔 0원 원가로 접혀 이익률 94~96%가 나왔다. 셋 중 하나라도 숫자로 남기면 그 카드가
  //    나머지를 부정한다 — 훑는 눈에는 큰 숫자가 이긴다.)
  cost: string | null;
  cost_known?: boolean;
  cost_unknown_kind?: "unmapped" | "zero_cost" | "ambiguous" | null;
  fee_actual?: boolean;  // 수수료가 전부 정산 실측이면 true (D-6)
  profit: string | null; profit_rate: string | null;
}

/** 기간 프리셋(days) 또는 임의 구간(dateFrom~dateTo). 둘 다 주면 **구간이 이긴다**(백엔드도 동일). */
export function fetchNaverSalesSummary(
  days: number,
  dateFrom?: string | null,
  dateTo?: string | null,
): Promise<NaverSalesSummary> {
  const q = dateFrom && dateTo
    ? `date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`
    : `days=${days}`;
  return fetchApi<NaverSalesSummary>(`/api/naver/ops/sales-summary?${q}`);
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
    /** 상한. 하위호환 키 — 점추정이 아니다(D-NAO-230). */
    factor: number;
    /**
     * D-NAO-230 안3 «구간 자»의 하한. **D-NAO-231: 후보 «선정»에는 안 쓴다** —
     * 실제 쓰기의 «크기»를 정하는 층만 쓴다(입찰 크기·증액 가드·확장 배분).
     * 이 응답의 보드는 전부 상한으로 뽑힌 것이다.
     */
    factor_low: number;
    /** 상한 = **모든 보드 판정(브레이크·액셀 양쪽)**이 쓰는 끝. */
    factor_high: number;
    /** 보정 전 점추정 원값(감사·병기용). */
    factor_point: number;
    source: string;
    window_from?: string;
    window_to?: string;
    window_revenue?: number;
    window_conv_amt?: number;
    /** ★D-NAO-234 — 하한의 근거. 하한이 실제로 실측값(0.827)일 때만 실린다.
     *  퇴화 구간 [1,1](보정계수 산출 불가)에는 **안 실린다** — 없는 근거를 화면이 말하지 않게. */
    factor_low_source?: string;
    /** 하한을 잰 창(KST). 계수는 창 없이 말하지 않는다(계약 §3-5). */
    factor_low_window?: string;
    /** 재현 문서 경로. */
    factor_low_evidence?: string;
    /** 하한에 붙박인 [미상] — 플러스스토어 라벨의 SA 소속 미확정(포함 시 1.067). */
    factor_low_caveat?: string;
    /** 창 4개에서 잰 하한의 변동폭 — 「고정값이 안 흔들린다」고 말하지 않기 위해. */
    factor_low_window_spread?: string;
    /** ★리뷰 P1-3 — 실측 기준선의 값(0.827). */
    factor_floor?: number;
    /** ★리뷰 P1-3 — 기준선이 구간의 «어느 끝»에 있는가. 점추정이 기준선보다 낮으면 "high"가 된다. */
    factor_floor_end?: "low" | "high";
  };
  account_bep_roas: number | null;
  account_target_roas: number | null;
  error?: string;
  boards: NaverAdDiagnosisBoards | null;
  /**
   * D-NAO-232 — 「액셀이 실행 게이트에서 얼마나 죽는가」 관측 전용(ref 94 §5·§6).
   * 보드 «선정»은 상한으로 끝난 뒤, 그 후보가 BEP 증액금지 게이트(하한 사용)를
   * 지나면 몇 건이 남는지를 센다. 재료가 없으면 null — 0으로 위장하지 않는다.
   */
  accel_gate: NaverAdAccelGate | null;
}

/** D-NAO-232 액셀 게이트 관측 페이로드. 금액은 원 단위 정수(반올림). */
export interface NaverAdAccelGateBucket {
  count: number;
  cost: number;
  conv_amt: number;
  /** 총이익 = (Σconv_amt × factor) ÷ bep_roas − Σcost (profit_scorecard.py:133 정본) */
  profit_high: number;
  profit_low: number;
}

export interface NaverAdAccelGate {
  /** 실제 게이트가 읽는 끝. 현재 'factor_high' — D-NAO-234 ⓐ로 «게이트 층»(통과/차단)이
   *  상한으로 옮겨졌다. 하한은 «크기 층»(얼마나 쓰나)만 쓴다. 값을 코드에 박지 말고 이 필드를
   *  읽어라 — 화면이 배포 동작과 반대를 말하는 재발을 그렇게 막는다(1R P1-3·2R P2-C).
   *  ★이 주석은 3R P2-4 상환이다: D-NAO-232 시절 문구('factor_low')가 그대로 남아 있었다. */
  gate_end: string;
  gate_note: string;
  /** 보드 창 기준 근사라는 자백 — 화면이 확정값처럼 보이면 안 된다(적대 리뷰 1R P2-2). */
  window_caveat: string;
  assumption: string;
  factor_low: number;
  factor_high: number;
  target_roas: number;
  /**
   * 'per_campaign' = 실제 게이트와 같은 자로 쟀다 / 'account_default' = 계정 기본값 하나로 쟀다.
   * 후자면 화면이 실제 게이트와 «다른 그룹»을 지목할 수 있다(적대 리뷰 1R P1-1).
   */
  target_roas_source: string;
  target_roas_min: number | null;
  target_roas_max: number | null;
  bep_roas: number;
  /** 세션 39(ref 93 §2)와 같은 보드 집합 — 비교 가능해야 하므로 primary. */
  accel_total: number;
  brake_total: number;
  /** 정지·재개 보드까지 포함한 확장 정의. */
  accel_total_ext: number;
  brake_total_ext: number;
  survive_low: number;
  survive_high: number;
  ratio_selection: number | null;
  ratio_after_gate_low: number | null;
  ratio_after_gate_high: number | null;
  buckets: {
    passing_both: NaverAdAccelGateBucket;
    blocked_low_only: NaverAdAccelGateBucket;
    blocked_both: NaverAdAccelGateBucket;
    /** roas_naver가 없어 판정 못 한 행. 「통과」로 세지 않는다(교훈 #123). */
    unmeasurable: number;
    /** 실제로 적용된 목표ROAS의 범위(캠페인별이면 벌어진다). */
    target_roas_min: number | null;
    target_roas_max: number | null;
  };
  by_board: Array<{
    board: string;
    total: number;
    blocked_low_only: number;
    blocked_both: number;
    unmeasurable: number;
  }>;
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

// ── 검색어 제외 «후보 리스트» + 조치 생존 감시 (D-NAO-173 P1, docs/PLAN_search-term-exclusion-list.md) ──
// 둘 다 읽기 전용 — 실행은 사람이 네이버 콘솔에서 한다(PLAN §3 금지선). 이 화면은
// 「왜 이걸 자르라는가」와 「어떻게 되돌리나」를 보여주는 것이 전부다.

/** 광고그룹에 상품이 여럿 붙었을 때 각 상품의 BEP(가장 낮은 값을 적용 — search_term_exclusion_list.py). */
export interface NaverExclusionCandidateBepProduct {
  channel_product_id: string;
  product_name: string | null;
  bep_roas: number;
}

export interface NaverExclusionCandidate {
  campaign_id: string;
  adgroup_id: string;
  search_term: string;
  source: string;
  imp: number;
  clk: number;
  cost: number;
  conv_purchase_cnt: number;
  conv_purchase_amt: number;
  roas: number;
  applied_bep: number;
  bep_source: string; // "product_bep_min" | "product_bep"
  bep_product_count: number;
  bep_products: NaverExclusionCandidateBepProduct[];
  min_cost: number;
  /** 상품 핵심어(화이트리스트) — 자동 발사에서는 걸러지지만 이 리스트에서는 **표시만** 한다. */
  whitelisted: boolean;
  loss_estimate: number | null;
  /** 백엔드 문장 그대로 렌더 — 프론트에서 새로 짓지 않는다. */
  reason: string;
  revert_howto: string;
  // 라우터가 붙이는 표시용 이름(SA는 순수, 이름 해석은 라우터 몫) — 매핑 없으면 null.
  campaign_name: string | null;
  adgroup_name: string | null;
}

export interface NaverExclusionBucket {
  terms: number;
  cost: number;
  why?: string;
}

export interface NaverExclusionListResponse {
  window: { from: string; to: string; days: number };
  maturity: {
    lag_days: number;
    excluded_from: string;
    excluded_to: string;
    excluded_terms: number;
    excluded_cost: number;
    why: string;
  };
  freshness: {
    latest_ad_date: string | null;
    latest_synced_at: string | null;
    as_of: string;
    lag_days: number | null;
  };
  totals: { terms: number; cost: number; conv_amt: number };
  gates: { min_click: number; round_cap: number };
  candidates: NaverExclusionCandidate[];
  candidate_cost: number;
  /** ★인덱스 시그니처가 붙어 있는 이유(D-NAO-176 적대 리뷰 P1): 종전엔 키가 고정 목록이고
   *  화면이 `BUCKET_ORDER` 하드코딩 배열로 돌아서, **백엔드가 버킷을 늘려도 TS가 침묵**했다.
   *  그 침묵으로 같은 결함이 세 번 났다(unverifiable · type_unknown_groups · already_excluded).
   *  이제 화면은 «응답에 있는 키 전부»를 그리고, 라벨 없는 키는 키 이름 그대로라도 보인다. */
  buckets: {
    already_excluded: NaverExclusionBucket;
    insufficient_sample: NaverExclusionBucket;
    bep_unknown: NaverExclusionBucket;
    powerlink_undecidable: NaverExclusionBucket;
    profitable: NaverExclusionBucket;
    capped_out: NaverExclusionBucket;
    maturity_excluded: NaverExclusionBucket;
  } & Record<string, NaverExclusionBucket | undefined>;
  revert_howto: string;
  generated_at: string;
}

export function getSearchTermExclusionList(params?: {
  days?: number;
  campaignId?: string;
  roundCap?: number;
  minClick?: number;
}): Promise<NaverExclusionListResponse> {
  const q = new URLSearchParams();
  if (params?.days != null) q.set("days", String(params.days));
  if (params?.campaignId) q.set("campaign_id", params.campaignId);
  if (params?.roundCap != null) q.set("round_cap", String(params.roundCap));
  if (params?.minClick != null) q.set("min_click", String(params.minClick));
  const qs = q.toString();
  return fetchApi<NaverExclusionListResponse>(
    `/api/naver/ad/search-term/exclusion-list${qs ? `?${qs}` : ""}`,
  );
}

export interface NaverExclusionSurvivalBreachRow {
  campaign_id: string | null;
  adgroup_id: string | null;
  search_term: string | null;
  live_state: string | null; // alive | missing | deleted | unknown
  live_note: string | null;
  excluded_at: string | null;
  cost_at_exclusion: number | null;
}

export interface NaverExclusionSurvival {
  monitored: number;
  alive: number;
  // ★라이브 대조가 **원리적으로 불가능한** 조치(쇼핑 광고그룹) 건수. 어긋남이 아니므로 배너를
  //   켜지 않지만, 이걸 화면이 안 그리면 감시 2건 중 1건이 미지인데 「모두 걸려 있음」이 뜬다.
  //   백엔드가 이 숫자를 따로 내보내는 이유가 그것이다(exclusion_survival.py 주석).
  unverifiable?: number;
  unverifiable_note?: string;
  breached: NaverExclusionSurvivalBreachRow[];
  // 잘린 목록의 총계 — breached는 상한(20건)까지만 실린다. 구버전 백엔드 안전을 위해 optional.
  breached_total?: number;
  never_checked: number;
  // 그중 «대조 주기를 넘겼는데도 여태 안 본» 건수. 방금 실행한 제외는 여기 안 들어간다
  // (그걸 이상으로 세면 제외 한 건마다 다음 날까지 배너가 빨강이 된다).
  never_checked_due?: number;
  last_checked_at: string | null;
  stale_hours: number;
  stale: boolean;
  healthy: boolean;
  revert_howto: string;
  impact: string;
  as_of: string;
}

export function getSearchTermExclusionSurvival(): Promise<NaverExclusionSurvival> {
  return fetchApi<NaverExclusionSurvival>("/api/naver/ad/search-term/exclusion-survival");
}

// ── 검색어 제외 실행 기록 + 성적표 (D-NAO-173 P2, docs/PLAN_search-term-exclusion-list.md §4-a) ──
// 시스템은 네이버에 쓰지 않는다 — 사람이 콘솔에서 실행한 것을 기록만 하고, 그 기록이
// diary→outcome→wisdom 학습 사슬의 입구가 된다(search_term_execution.py docstring 참조).

export type NaverSearchTermExecutionResultKind = "created" | "already_recorded" | "re_excluded";

export interface NaverSearchTermExecutionResult {
  result: NaverSearchTermExecutionResultKind;
  exclusion_id: number;
  cost_at_exclusion: number;
  cycle: number;
  next_review_at?: string;
  /** false면 이 조치가 학습 사슬에 안 잡힌다 — 화면에서 반드시 표면화해야 한다. */
  diary: boolean;
}

export function postSearchTermExecution(body: {
  campaignId: string;
  adgroupId: string;
  searchTerm: string;
  rationale: string;
}): Promise<NaverSearchTermExecutionResult> {
  return fetchApi<NaverSearchTermExecutionResult>("/api/naver/ad/search-term/executions", {
    method: "POST",
    body: JSON.stringify({
      campaign_id: body.campaignId,
      adgroup_id: body.adgroupId,
      search_term: body.searchTerm,
      rationale: body.rationale,
    }),
  });
}

export interface NaverSearchTermDetectRecorded extends NaverSearchTermExecutionResult {
  adgroup_id: string;
  search_term: string;
}

export interface NaverSearchTermDetectResult {
  scanned_groups: number;
  groups_with_zero: number;
  /** 쇼핑이라 애초에 API로 대조가 불가능한 그룹 — «찾은 게 없다»와 다르다. */
  unverifiable_groups?: number;
  /** 광고그룹 유형 조회 자체가 실패한 그룹 — 쇼핑(위)과도 «0건»과도 다르다. */
  type_unknown_groups?: number;
  /** 라이브엔 있는데 캠페인을 못 붙여 기록을 보류한 제외. */
  unattributable?: { adgroup_id: string; search_term: string }[];
  unattributable_count?: number;
  recorded: NaverSearchTermDetectRecorded[];
  errors: string[];
  as_of: string;
}

export function postSearchTermExecutionDetect(campaignId?: string): Promise<NaverSearchTermDetectResult> {
  const q = new URLSearchParams();
  if (campaignId) q.set("campaign_id", campaignId);
  const qs = q.toString();
  return fetchApi<NaverSearchTermDetectResult>(
    `/api/naver/ad/search-term/executions/detect${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
}

/** 검색어 grain 전후 대조(직접 효과) — 사후 창이 성숙 전이면 pending(판정하지 않음). */
export interface NaverSearchTermWindowStat {
  from: string | null;
  to: string | null;
  days: number;
  cost: number;
  clk: number;
  conv_purchase_cnt: number;
  conv_purchase_amt: number;
  cost_per_day: number;
  amt_per_day: number;
}

/** 캠페인 grain 전후 대조(부작용 — 볼륨 절멸이 났나). after_days가 0이면 after는 null. */
export interface NaverSearchTermCampaignWindowStat {
  from: string;
  to: string;
  days: number;
  cost: number;
  conv_amt: number;
  clk: number;
  cost_per_day: number;
  conv_amt_per_day: number;
  profit_contrib: number | null;
  profit_contrib_per_day: number | null;
}

export type NaverExclusionVerdict = "stopped" | "still_spending" | "pending" | "no_baseline";

export interface NaverSearchTermScorecardItem {
  exclusion_id: number;
  campaign_id: string | null;
  adgroup_id: string | null;
  search_term: string;
  excluded_at: string;
  cost_at_exclusion: number | null;
  live_state: string | null;
  applied_bep: number | null;
  before: NaverSearchTermWindowStat;
  after: NaverSearchTermWindowStat;
  after_days: number;
  verdict: NaverExclusionVerdict;
  /** 백엔드 문장 그대로 렌더 — 프론트에서 새로 짓지 않는다. */
  why: string;
  profit_recovered: number | null;
  campaign: {
    before: NaverSearchTermCampaignWindowStat;
    after: NaverSearchTermCampaignWindowStat | null;
  };
  // 라우터가 붙이는 표시용 이름 — 매핑 없으면 null.
  campaign_name: string | null;
  adgroup_name: string | null;
}

export interface NaverSearchTermScorecard {
  window_days: number;
  maturity_lag_days: number;
  mature_through: string;
  total: number;
  by_verdict: Record<NaverExclusionVerdict, number>;
  // ★판정된 것만 합산 — pending을 0원으로 세면 성과가 희석되고, 빼고 세면 부풀려진다.
  profit_recovered_judged: number;
  judged_count: number;
  // 판정은 났지만 BEP가 없어 회수액을 «못 낸» 건수. 합계가 이들을 0원으로 세므로, 이 숫자
  // 없이는 「회수액이 적다」와 「회수액을 못 잰다」가 화면에서 같아 보인다.
  profit_unknown_count?: number;
  pending_count: number;
  /** 콘솔에 이미 걸려 있던 것을 장부에 편입한 행 — 실행 시점을 몰라 전후 창을 못 잡으므로
   *  성적표가 판정하지 않는다. ★이 숫자가 없으면 「총 2건」이 「우리가 아는 제외가 2건뿐」으로
   *  읽히고 편입한 43건이 화면에서 통째로 증발한다(D-NAO-176). */
  imported_unjudgeable_count?: number;
  imported_unjudgeable_note?: string | null;
  items: NaverSearchTermScorecardItem[];
  as_of: string;
}

export function getSearchTermExclusionScorecard(windowDays?: number): Promise<NaverSearchTermScorecard> {
  const q = new URLSearchParams();
  if (windowDays != null) q.set("window_days", String(windowDays));
  const qs = q.toString();
  return fetchApi<NaverSearchTermScorecard>(
    `/api/naver/ad/search-term/exclusion-scorecard${qs ? `?${qs}` : ""}`,
  );
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
// D-NAO-249 §4-B(B1) — param_change 제안을 승인(approved)할 때는 applied_value가 **필수**다
// (없으면 서버 400). 반영될 값은 사람이 정한다 — 코드가 값을 발명하지 않는다. decidedBy·
// decisionNote는 옵션(미지정 시 서버가 "console"·자동 문구로 채운다). ★extra를 안 넘기면
// body는 예전과 완전히 같은 {status} 그대로다(param_change가 아닌 제안의 승인·반려 흐름은
// 1비트도 안 바뀐다 — 회귀 대상).
export function updateNaverProposalStatus(
  id: number,
  status: "approved" | "rejected",
  extra?: { appliedValue?: number; decidedBy?: string; decisionNote?: string },
): Promise<NaverAdProposal> {
  const body: Record<string, unknown> = { status };
  if (extra?.appliedValue !== undefined) body.applied_value = extra.appliedValue;
  if (extra?.decidedBy !== undefined) body.decided_by = extra.decidedBy;
  if (extra?.decisionNote !== undefined) body.decision_note = extra.decisionNote;
  return fetchApi<NaverAdProposal>(`/api/naver/ad/proposals/${id}/status`, {
    method: "POST",
    body: JSON.stringify(body),
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

// ── 지혜 성적표(M3-a, 계약 PLAN_naver-m3-wisdom-scorecard.md §4-A① · §4-B⑥) ──
// ★표본 0을 «좋은 성적»으로 렌더하지 말 것 — has_evidence=false면 evidence_gap을 보여준다.
//   빈 성적표를 무해하게 그리면 qi_grade=4 죽은 신호(2026-08-12)의 재발이다.
export interface NaverWisdomScorecardChange {
  change_log_id: number;
  changed_at: string | null;
  action: string;
  campaign_id: string;
  dry_run: boolean;
  outcome_legacy: string | null;   // 옛 자(효율 배율) — 불변 증거(§8-Q1)
  outcome_profit: string | null;   // 새 자(총이익 델타 부호)
  gave_before: number | null;
  gave_after: number | null;
  gave_delta: number | null;
  profit_before: number | null;
  profit_after: number | null;
  profit_delta: number | null;
  bep_source: string | null;       // product_bep / account_default / unavailable
}

export interface NaverWisdomScorecardRow {
  wisdom_id: number;
  wisdom_text: string;
  status: string;
  promoted_at: string | null;
  source_candidate_id: number;
  linked_proposals: {
    proposal_id: number;
    proposal_type: string;
    status: string;
    campaign_id: string;
    executed_change_log_id: number | null;
    // A7① 결정 메타(D-NAO-248 §4-A) — decided_at이 NULL이면 «아직 결정 안 됨»이 아니라
    // 컬럼 신설 «전»에 결정났을 수 있다(예: 2314는 07-26 rejected). 그때 decision_note는
    // 실제 사유가 아니라 "기록 없음(컬럼 신설 전)" 표지 문자열이다 — 지어낸 사유가 아니다.
    decided_at: string | null;
    decided_by: string | null;
    decision_note: string | null;
  }[];
  linked_proposal_count: number;
  // A7② — 이 지혜가 «지금» 전문가 브리핑 자유 텍스트에 실리는가. 「실린다」≠「효과가 났다」
  // (briefing_injection_note가 그 한계를 실어 나른다 — attribution.limitation과 같은 결).
  briefing_injected: boolean;
  briefing_injection_note: string;
  has_evidence: boolean;
  evidence_gap: string | null;
  changes_total: number;
  changes_scored_profit: number;
  verdicts: Record<string, number>;
  bep_sources: Record<string, number>;
  gave_before_sum: number | null;
  gave_after_sum: number | null;
  gave_delta_sum: number | null;
  gave_pairs: number;
  profit_before_sum: number | null;
  profit_after_sum: number | null;
  profit_delta_sum: number | null;   // 총이익 «금액» 합(원) — 계약 §4-A① "ad_profit 합"
  profit_pairs: number;
  profit_unavailable: number;        // 판정은 됐으나 렌즈 미기록으로 금액 산출불가인 행수
  profit_unjudged: number;           // 채점기가 표본 미달로 판정을 거부한 행 중 금액은 있는 것
  changes_executed: number;
  details: NaverWisdomScorecardChange[];
}

// ── 후보 현황(A2, D-NAO-248 §4-A) — 승격 «전» 지혜 후보(OpsWisdomCandidate) 파이프라인.
//   wisdom[] 은 이미 승격된 지혜만 보므로, 그 앞단(harvest_candidates가 매일 쌓는 행)은
//   여기서만 보인다. wisdom_id 필터와 무관하게 항상 전체가 실린다(후보는 특정 지혜 1건에
//   속하지 않는다 — 승격 전이라 1:1 링크가 없다).
export type NaverWisdomCandidateBucket =
  | "legacy"
  | "global_pool"
  | "separated_experiment"
  | "separated_unknown";

export interface NaverWisdomCandidateRow {
  candidate_id: number;
  signature: string;
  status: string;
  grain: string | null; // null=레거시(D-NAO-248 이전) / 'global'=신형
  bucket: NaverWisdomCandidateBucket;
  bucket_label: string;
  campaign_type: string | null;
  experiment_batch: string | null;
  action: string | null;
  occurrences: number;
  good_count: number;
  bad_count: number;
  campaign_count: number;
  by_campaign: Record<string, { good: number; bad: number }>;
  observation: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  // ★D-NAO-251(증거보전) — 기각 후 증거가 얼마나 더 쌓였는지·재심 여력이 남았는지.
  // judged_occurrences가 null이면 「아직 판정된 적 없음」이고 occurrences_since_judgment도
  // null이다(0이 아니다 — 0으로 내면 「판정 후 하나도 안 쌓임」과 구별이 안 된다).
  // 옵셔널 — 배포 순서상 이 필드가 아직 없는 응답이 올 수 있다.
  judged_at?: string | null;
  judged_occurrences?: number | null;
  occurrences_since_judgment?: number | null;
  rejudge_count?: number;
  reopen_ready?: boolean;
  prior_judgment_count?: number;
}

// ★D-NAO-251 §4-③ — 판사 대기열 적체. 「pending 17건인데 회당 5건이라 소화에 4일」이
// 어디에도 안 보이던 것이 이 계약이 고치는 결함 셋 중 하나다(교훈 #318).
// days_to_drain은 신규 후보 유입 0 가정 위의 값이라 응답이 그 가정을 스스로 밝힌다.
export interface NaverJudgeBacklog {
  pending_total: number;
  pending_ripe: number; // 판사에게 갈 자격이 있는 건수(TTL 14일 or occurrences≥3 ∧ action 있음)
  cap_next_run: number; // 다음 회차 상한(평시 5 / 적체 시 15)
  days_to_drain: number;
  cron: string;
  assumption: string;
}

// B7-6(D-NAO-248 §4-B) — 판사의 param_suggestion이 코드 클램프에서 어떻게 갈렸는지 세는
// 카운터. 4키 전부 **0이어도 실린다**(교훈 #318: 카운터가 있어야 침묵을 본다 — 「조용히
// 0건」과 「세는 코드가 죽어서 0건」을 값만 보고는 못 가른다).
export interface NaverParamGateCounts {
  unconditional_mapped: number; // scope=unconditional ∧ param∈SPECS → 제안 생성됨
  conditional_fallback: number; // 조건부(또는 scope 부재) → 제안 안 만듦
  unmapped_param: number;       // 파라미터 키가 화이트리스트 밖 → 제안 안 만듦
  no_suggestion: number;        // 판사가 파라미터 제안을 아예 안 냄(대부분 정상)
}

// C2 — 검색어 재료 현황(다른 세션 배선 중, D-NAO-249). ★백엔드에 아직 없을 수 있어 옵셔널로
// 둔다 — 없으면 그 섹션을 안 그린다(방어적 렌더).
export interface NaverSearchTermMaterialStatus {
  total: number;
  by_status: {
    stopped: number;
    leaking: number;
    ambiguous: number;
    no_data: number;
    absent: number;      // harvest가 «보는»(event_type·outcome_json 조건 통과) 행 중 d1_st 키만 없음
    unknown: number;
    // ★2026-08-25 신설 — harvest_candidates()의 자체 필터(event_type IN(execute,blocked) ∧
    // outcome_json IS NOT NULL) 밖에 있는 행(예: voided). d1_st가 채워지든 말든 harvest가
    // 원리적으로 안 본다 — absent(「채워지면 처리될 행」으로 읽힘)와 섞으면 부정직하다.
    not_harvestable: number;
    // ★S3 신설 — 복귀(재개방·복귀확정) 실험 행. d1_st가 **원리적으로 영원히** 안 채워진다
    // (제외 성적표의 자는 「비용 정지 = 성공」이라 복귀에 쓰면 부호가 뒤집힌다 — 복귀는
    // `probation` 축에서 총이익 기준으로 잰다). absent로 세면 「곧 올 것」이라는 그 이름의
    // 뜻이 거짓이 되므로 not_harvestable과 같은 이유로 따로 센다.
    return_experiment: number;
  };
  label: string;
}

// ── B5 대칭·탐색 관측(D-NAO-247 점화 계약) ──────────────────────────────────────
// ★[판정불능 예약] — 이 표면은 성과 판정을 하지 않는다. 실집행 0건이라 파라미터 변경의
//   행동·총이익 효과를 관측할 사건 자체가 없다. 「배선·관측의 증거」이지 「효과의 증거」가
//   아니다 — verdict_pending 문구를 화면에서 지우지 말 것.
export interface NaverGuardrailDirectionClassification {
  brake: number;   // 조이는 방향으로 바뀐 키 인스턴스 수
  accel: number;   // 푸는 방향으로 바뀐 키 인스턴스 수
  unchanged_or_unknown: number; // 값 불변 또는 방향 판정 불가(파싱 실패·키 누락)
  by_key: Record<string, { brake: number; accel: number }>;
  total_changes: number; // update_guardrail_params change_log «행 수»(키 인스턴스 합과는 다른 분모)
}

export interface NaverExplorationActorSnapshot {
  total: number;
  by_actor: Record<string, number>;
  explore_share: number | null;
  explore_total: number;
  explore_blocked: number;
  explore_blocked_rate: number | null;
}

export interface NaverExplorationSymmetry {
  window_days: number;
  boundary_changed_at: string | null;
  // 파라미터 변경이 한 번도 없으면 before/after가 둘 다 null이고 whole_window가 대신 채워진다
  // (창을 낭비하지 않되 «전/후»를 지어내지 않는다).
  before: NaverExplorationActorSnapshot | null;
  after: NaverExplorationActorSnapshot | null;
  whole_window: NaverExplorationActorSnapshot | null;
  note: string;
}

export interface NaverSymmetryReport {
  verdict_pending: string; // "[판정불능 예약] ..." — 성과 판정 없음을 명시하는 문구
  guardrail_direction: NaverGuardrailDirectionClassification;
  exploration: NaverExplorationSymmetry;
}

export interface NaverWisdomCandidateStatus {
  candidates_total: number;
  bucket_counts: Record<NaverWisdomCandidateBucket, number>;
  bucket_labels: Record<NaverWisdomCandidateBucket, string>;
  retro_harvest_label: string; // 「기존 재료 재집계」 라벨 — 새 배움이 아니라 기존 90일 일기의 재집계
  candidates: NaverWisdomCandidateRow[];
  // ★옵셔널로 둔다 — 다른 세션이 백엔드를 동시에 고치고 있어 배포 순서상 이 필드가 아직
  // 없는 응답이 올 수 있다. 렌더는 존재 여부로 분기(0건도 존재는 한다 — 별개 개념).
  param_gate?: NaverParamGateCounts;
  search_term_material?: NaverSearchTermMaterialStatus;
  judge_backlog?: NaverJudgeBacklog; // ★D-NAO-251 §4-③
  no_action?: NaverNoActionStatus; // ★D-NAO-251 §5 ②-b
}

// ★D-NAO-251 §5 ②-b — action 미상 후보 현황. action은 패턴의 «의미 축»이라 미상이면 형제
// 매칭이 원리적으로 불가하고, 판사에겐 「대조군 없음」만 보여 그 판정이 다시 terminal이 된다.
// 2026-08-26 08:45 회차가 실증: 후보 45는 11건 전승인데 「액션이 null(미상)이므로」 기각됐다.
// ★이 타입이 «나중에» 생긴 것 자체가 교훈이다 — 카운터를 만든 층(_sibling_buckets·harvest
// totals)과 «닿는» 층(API 응답·화면)은 다른 층이고, 합격기준이 지목한 것은 닿는 층이었다.
export interface NaverNoActionStatus {
  total: number;
  by_status: Record<string, number>;
  unresolved: number; // hidden·promoted가 아닌 채 남은 행 수 — 0이 아니면 처분이 덜 됐다
  candidates: { candidate_id: number; signature: string; status: string; occurrences: number }[];
  label: string;
}

export interface NaverWisdomScorecard {
  generated_at_kst: string;
  wisdom_total: number;
  wisdom_active: number;
  wisdom_with_evidence: number;
  candidate_status: NaverWisdomCandidateStatus;
  value_definition: {
    metric: string;
    formula: string;
    grain: string;
    verdict_rule: string;
    conversion_delay: { window: string; correction_applied: boolean | null; note: string };
    bep_coverage: {
      groups_total: number | null;
      groups_with_product_bep: number | null;
      ratio: number | null;
      note: string;
    };
    legacy_note: string;
  };
  attribution: { path: string; limitation: string };
  reflection_health: NaverReflectionHealth;
  wisdom: NaverWisdomScorecardRow[];
  // ★옵셔널 — 백엔드 배포 순서상 이 필드가 아직 없는 응답이 올 수 있다(다른 필드와 같은
  // 방어적 관례). B5(D-NAO-247 점화 계약) — 대칭·탐색 관측, 성과 판정 없음.
  symmetry_report?: NaverSymmetryReport;
}

// ── 반성 루프 상태(D-NAO-228, 계약 PLAN_naver-m5-reflection-visibility.md §5 ⓐ) ──
// ★왜 성적표 안에 있나: 성적표의 재료는 «반성»이 만든다. 반성이 도는지 화면에 없으면
//   성적표가 비었을 때 「지혜가 없어서」인지 「반성이 죽어서」인지 구분이 안 된다.
//   실제로 2026-07-18~08-22 결번 19일 동안 로그도 전부 'ok'였다(계약 §3).
// ★skipped_no_material을 «고장»으로 그리지 말 것 — 재료(실집행 일기)가 없는 날 반성이
//   안 도는 것은 정상이다(북극성 §5-2). L3 정지 중엔 이게 다수다.
// pending = 오늘 08:35 크론이 아직 안 왔다. 결번이 아니고 경고도 아니다(적대 리뷰 1R P1-2).
export type NaverReflectionDayState =
  | "ok"
  | "skipped_no_material"
  | "failed"
  | "unresolved"
  | "pending";

export interface NaverReflectionHealthDay {
  date: string;
  state: NaverReflectionDayState;
  source: "reflection_row" | "status_row" | "inferred" | "not_due";
  has_material: boolean;
  detail: string | null;
}

export interface NaverReflectionHealth {
  window: { start: string; end: string; days: number };
  last_success_kst: string | null;
  gap_days_since_success: number | null;
  missing_days: number;
  counts: Record<NaverReflectionDayState, number>;
  headline: string;
  days: NaverReflectionHealthDay[];
  evidence_gap: string;
  material_note: string;
}

export function fetchNaverWisdomScorecard(): Promise<NaverWisdomScorecard> {
  return fetchApi<NaverWisdomScorecard>("/api/naver/ad/wisdom-scorecard");
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

// ── 안전 봉투 파라미터 현황판 (D-NAO-172 P1) — GET/PUT /settings/guardrail-params ──
// source가 이 화면의 핵심: db(설정값)면 DB가 이기고 있는 것, code(기본값)면 코드 상수로
// 돈다는 뜻. rejected=true면 DB에 값은 있는데 타입·범위 밖이라 코드 상수로 조용히 폴백된 것.
export interface NaverGuardrailParam {
  key: string;
  label: string;
  value: number;
  source: "db" | "code";
  code_default: number;
  min: number;
  max: number;
  why: string;
  direction: "tighten_down" | "tighten_up";
  rejected: boolean;
  updated_at: string | null;
}
export interface NaverGuardrailRetroFreshness {
  latest_asof: string | null;
  expected_asof: string;
  stale: boolean;
  lag_days: number | null;
}
// ★D-NAO-262(#14) — 창 파라미터를 봉투 상한까지 늘렸을 때 그만큼의 원본 데이터가 실제로
// 있는가. `promoted`(봉투 승격 여부)와 `note`(원본 0행 등 재료 부재 사유)는 직교하는
// 사실이라 따로 둔다 — 한쪽이 다른 쪽을 덮으면 「봉투가 없다」와 「재료가 없다」가 뭉개진다.
export interface NaverGuardrailWindowCoverage {
  param_key: string | null;
  promoted: boolean;
  source: "expkeyword" | "shopping";
  label: string;
  ceiling_days: number;
  latest: string | null;
  window_from?: string;
  missing_days: number | null;
  covered: boolean;
  note: string | null;
}
export interface NaverGuardrailParamsResponse {
  params: NaverGuardrailParam[];
  from_db_enabled: boolean;
  // B3 되돌림 절차(D-NAO-249) — 스위치(_PARAMS_FROM_DB)의 존재·용법·되돌리는 절차를 서버가
  // 문장으로 실어 화면이 자기 설명을 하게 한다. ★프론트에서 문구를 새로 짓지 않는다.
  from_db_help: string;
  retro_freshness: NaverGuardrailRetroFreshness;
  window_coverage: NaverGuardrailWindowCoverage[];
}

export function getNaverGuardrailParams(): Promise<NaverGuardrailParamsResponse> {
  return fetchApi<NaverGuardrailParamsResponse>("/api/naver/ad/settings/guardrail-params");
}

// body는 {key: 값} — 넘긴 키만 남고 나머지는 코드 상수로 복귀(전체 치환). 범위 밖·타입
// 불일치는 400 + 한국어 메시지(그대로 표면화할 것 — 자체 문구로 갈아치우지 않는다).
export function putNaverGuardrailParams(
  values: Record<string, number>,
): Promise<NaverGuardrailParamsResponse> {
  return fetchApi<NaverGuardrailParamsResponse>("/api/naver/ad/settings/guardrail-params", {
    method: "PUT",
    body: JSON.stringify(values),
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
  /** 규칙 ⑤ — 「외부 변경」으로 감지됐지만 우리 실집행과 대조돼 되찾은 행의 근거.
   *  그 외에는 null. 주체는 사실 주장이라, 뒤집었으면 근거를 같이 보여준다. */
  actor_evidence: string | null;
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
  /** 규칙 ⑤로 「대행사」에서 「우리 자동화」로 되찾은 건수. 조용히 바꾸지 않는다. */
  reclaimed_ours: number;
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

// 쿠팡 6스트림 수집 신선도(전역 배너 전용). 자동 트리거 제거 후 '낡음/실패' 가시화 유일 경로.
// 'unknown' = 백엔드가 그 스트림의 상태를 **판정하지 못했다**(getter 예외). fresh와 절대
// 섞지 않는다 — 모르는 것을 괜찮다고 표시하면 침묵과 같다(2026-08-07 적대리뷰 P1).
// 'needs_login' = 계정 세션이 끊겨 **사람이 로그인하기 전까지 원리적으로 진행되지 않는다**
//   (2026-08-22 W1). in_flight보다 우선한다 — 종전엔 로그인이 끊긴 채 버튼을 누르면
//   requested=true라 「수집 중」으로 보였고, 그동안 아무도 Mac 앞으로 가지 않았다.
export type CollectionState =
  | "fresh" | "warn" | "critical" | "failed" | "in_flight" | "unknown" | "needs_login";
export interface CollectionStreamStatus {
  // ★rg_wing1/rg_wing2 추가(2026-08-22 W1): RG 정산이 전역 배너 대상이 아니어서
  //   「WING 로그인이 끊겼다」가 버튼을 눌러 실패해 봐야만 보였다.
  key: "ofix_sales" | "ofix_ad" | "ohitech_ad" | "supplier_hub" | "rg_wing1" | "rg_wing2";
  label: string;
  state: CollectionState;
  age_hours: number | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  /** 마지막 실패의 분류. 문구 매칭 대신 이 값으로 처방을 고른다(2026-08-22 W1). */
  last_error_kind?: string | null;
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

// ── 수입건 원장(landed cost, D-CPP-48) ──
// ★금액·수량은 Decimal 정밀도 보존을 위해 전부 **문자열**로 오간다. 숫자로 파싱해 표시하되
//   전송은 문자열 그대로 보낸다(파싱→재직렬화 왕복으로 정밀도를 잃지 않는다).
export type ImportShipmentStatus = "draft" | "confirmed";
export type ImportAllocationBasis = "amount" | "weight" | "volume" | "quantity";
export type ImportLineType = "product" | "material" | "unknown";
export type ImportDocType = "ci" | "pl" | "expense" | "etc";

export interface ImportShipmentListItem {
  id: number;
  hbl_no: string;
  declaration_no: string | null;
  declaration_date: string | null;
  eta: string | null;
  shipper_name: string | null;
  invoice_no: string | null;
  vessel: string | null;
  currency: string;
  fx_rate: string;
  declared_inv_value: string | null;
  customs_value_krw: string | null;
  carton_count: number | null;
  gross_weight_kg: string | null;
  cbm: string | null;
  allocation_basis: ImportAllocationBasis;
  status: ImportShipmentStatus;
  memo: string | null;
  confirmed_at: string | null;
  line_count: number;
  document_count: number;
}

export interface ImportCostLine {
  id?: number;
  seq: number;
  item_name: string;
  supply_amount: string;
  tax_amount: string;
  // ★기본값이 없다 — 매 줄 명시해야 한다(부가세 라인이 배부에 실수로 섞이면 원가가 부푼다).
  is_costing: boolean;
  // 이 비용 라인이 관세인가(D-CPP-50). true면 금액배부가 아니라 인보이스 라인의 duty_rate로 귀속된다.
  is_duty?: boolean;
  note?: string | null;
}

export interface ImportInvoiceLine {
  id?: number;
  seq: number;
  order_no?: string | null;
  item_name: string;
  quantity: string;
  unit_price_foreign: string;
  line_type: ImportLineType;
  internal_sku?: string | null;
  gross_weight_kg?: string | null;
  cbm?: string | null;
  // 품목별 관세율(D-CPP-50). "0.056"=5.6%. null="모름"(0%가 아니다 — 0으로 바꾸지 말 것).
  duty_rate?: string | null;
  // 확정 전엔 null이다 — 0으로 그리지 않는다(0=미계산 혼동 금지).
  goods_amount_krw?: string | null;
  allocated_cost_krw?: string | null;
  unit_cost_ex_vat?: string | null;
  unit_cost_inc_vat?: string | null;
}

export interface ImportPackingLine {
  id?: number;
  seq: number;
  item_name: string;
  quantity: string;
  carton_range?: string | null;
  qty_per_carton?: string | null;
  carton_count?: string | null;
  gross_weight_kg?: string | null;
  measure?: string | null;
  cbm?: string | null;
  remark?: string | null;
}

export interface ImportDocument {
  id: number;
  doc_type: ImportDocType;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  uploaded_at: string | null;
}

export interface ImportReconcileCheckRow {
  item: string;
  ci: string | null;
  pl: string | null;
  diff: string | null;
}

export interface ImportReconcileCheck {
  key: "quantity" | "invoice_total" | "allocation";
  label: string;
  status: "ok" | "mismatch" | "missing";
  passed: boolean;
  expected: string | null;
  actual: string | null;
  detail: string;
  rows: ImportReconcileCheckRow[];
}

export interface ImportReconcile {
  passed: boolean;
  checks: ImportReconcileCheck[];
}

export interface ImportAllocationLine {
  seq: number;
  item_name: string;
  quantity: string;
  goods_amount_krw: string;
  allocated_cost_krw: string;
  unit_cost_ex_vat: string;
  unit_cost_inc_vat: string;
  // 배부액 내역(D-CPP-50): 공통비 몫 / 관세 몫. 둘의 합 = allocated_cost_krw.
  allocated_common_krw?: string;
  allocated_duty_krw?: string;
}

export type ImportDutyMode = "by_rate" | "blended";

export interface ImportAllocation {
  basis: ImportAllocationBasis;
  pool_krw: string;
  allocated_total_krw: string;
  unallocated_krw: string;
  lines: ImportAllocationLine[];
  // 관세 귀속 방식(D-CPP-50). by_rate=라인 세율로 정확 귀속 / blended=세율 미입력이라 공통비에 섞여 배부(부정확).
  duty_mode?: ImportDutyMode;
  common_pool_krw?: string;
  duty_pool_krw?: string;
  // 라인 세율로 계산한 관세 총액 — duty_pool_krw(서류)와 대조해 세율 입력이 맞는지 검산한다.
  duty_computed_krw?: string;
  duty_check_diff?: string;
}

export interface ImportShipmentDetail extends ImportShipmentListItem {
  cost_lines: ImportCostLine[];
  invoice_lines: ImportInvoiceLine[];
  packing_lines: ImportPackingLine[];
  documents: ImportDocument[];
  reconcile: ImportReconcile;
  allocation: ImportAllocation | null;
  allocation_error: string;
  // ★참고값 — 배부에 쓰이지 않는다. ×1.1 규약과 실제 세액의 차이를 보여줄 뿐이다.
  actual_vat_krw: string | null;
}

export interface ImportConfirmResult {
  confirmed: boolean;
  reason: string;
  reconcile: ImportReconcile;
}

// ★확정 시 검산 미통과여도 200이다 — confirm_result.confirmed로 판단한다(4xx가 아니다).
export interface ImportShipmentConfirmResponse extends ImportShipmentDetail {
  confirm_result: ImportConfirmResult;
}

export interface ImportBasisComparisonLine {
  seq: number;
  item_name: string;
  allocated_cost_krw: string;
  unit_cost_ex_vat: string;
}

export interface ImportBasisComparisonEntry {
  basis: ImportAllocationBasis;
  available: boolean;
  reason: string;
  unallocated_krw?: string;
  lines: ImportBasisComparisonLine[];
}

export interface ImportBasisComparison {
  bases: ImportAllocationBasis[];
  comparison: ImportBasisComparisonEntry[];
}

export interface ImportShipmentInput {
  hbl_no: string;
  fx_rate: string;
  currency: string;
  declaration_no?: string | null;
  declaration_date?: string | null;
  eta?: string | null;
  shipper_name?: string | null;
  invoice_no?: string | null;
  vessel?: string | null;
  declared_inv_value?: string | null;
  customs_value_krw?: string | null;
  carton_count?: number | null;
  gross_weight_kg?: string | null;
  cbm?: string | null;
  allocation_basis: ImportAllocationBasis;
  memo?: string | null;
  cost_lines: ImportCostLine[];
  invoice_lines: ImportInvoiceLine[];
  packing_lines: ImportPackingLine[];
}

export function fetchImportShipments(params: {
  limit?: number;
  status?: ImportShipmentStatus;
} = {}): Promise<{ items: ImportShipmentListItem[]; count: number }> {
  const q = new URLSearchParams();
  if (params.limit) q.set("limit", String(params.limit));
  if (params.status) q.set("status", params.status);
  const qs = q.toString();
  return fetchApi(`/api/import-cost/shipments${qs ? `?${qs}` : ""}`);
}

export function fetchImportShipment(id: number): Promise<ImportShipmentDetail> {
  return fetchApi(`/api/import-cost/shipments/${id}`);
}

export function createImportShipment(body: ImportShipmentInput): Promise<ImportShipmentDetail> {
  return fetchApi("/api/import-cost/shipments", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateImportShipment(
  id: number,
  body: ImportShipmentInput,
): Promise<ImportShipmentDetail> {
  return fetchApi(`/api/import-cost/shipments/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteImportShipment(id: number): Promise<{ deleted: boolean; id: number }> {
  return fetchApi(`/api/import-cost/shipments/${id}`, { method: "DELETE" });
}

export function confirmImportShipment(id: number): Promise<ImportShipmentConfirmResponse> {
  return fetchApi(`/api/import-cost/shipments/${id}/confirm`, { method: "POST" });
}

export function reopenImportShipment(id: number): Promise<ImportShipmentDetail> {
  return fetchApi(`/api/import-cost/shipments/${id}/reopen`, { method: "POST" });
}

export function fetchImportBasisComparison(id: number): Promise<ImportBasisComparison> {
  return fetchApi(`/api/import-cost/shipments/${id}/basis-comparison`);
}

export function uploadImportDocument(
  id: number,
  docType: ImportDocType,
  file: File,
): Promise<ImportDocument> {
  const form = new FormData();
  form.append("file", file);
  return fetch(
    `${API_BASE}/api/import-cost/shipments/${id}/documents?doc_type=${docType}`,
    { method: "POST", body: form },
  ).then(async (res) => {
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`Upload error ${res.status}: ${detail}`);
    }
    return res.json();
  });
}

export function importDocumentDownloadUrl(shipmentId: number, docId: number): string {
  return downloadUrl(`/api/import-cost/shipments/${shipmentId}/documents/${docId}`);
}

// ── 서류 파싱(POST /api/import-cost/parse) — 폼 초안 생성용, 저장하지 않는다 ──
// ★header는 전부 optional — 못 읽은 키는 응답에 아예 없다(0으로 채우지 않는다).
// ★금액·수량은 전부 문자열(Decimal 정밀도) — carton_count만 number.
export interface ImportParseHeader {
  hbl_no?: string;
  declaration_no?: string;
  declaration_date?: string;
  eta?: string;
  shipper_name?: string;
  vessel?: string;
  invoice_no?: string;
  currency?: string;
  fx_rate?: string;
  declared_inv_value?: string;
  customs_value_krw?: string;
  carton_count?: number;
  gross_weight_kg?: string;
  cbm?: string;
}

export interface ImportParseInvoiceLine {
  seq: number;
  item_name: string;
  quantity: string;
  unit_price_foreign: string;
  order_no: string | null;
  line_type: "unknown";
  internal_sku: null;
  gross_weight_kg: string | null;
  cbm: string | null;
}

export interface ImportParsePackingLine {
  seq: number;
  carton_range: string | null;
  item_name: string;
  quantity: string;
  qty_per_carton: string | null;
  carton_count: string | null;
  gross_weight_kg: string | null;
  measure: string | null;
  cbm: string | null;
  remark: string | null;
}

export interface ImportParseCostLine {
  seq: number;
  item_name: string;
  supply_amount: string;
  tax_amount: string;
  is_costing: boolean;
  note: string | null;
}

export interface ImportParseResult {
  header: ImportParseHeader;
  invoice_lines: ImportParseInvoiceLine[];
  packing_lines: ImportParsePackingLine[];
  cost_lines: ImportParseCostLine[];
  errors: string[];
  warnings: string[];
}

// 셋 중 최소 하나는 있어야 한다(없으면 백엔드가 400) — 버튼 비활성화는 호출부 책임.
// ★expenseText가 있으면(사람이 직접 붙여넣은 것) expenseFile과 함께 보내도 서버가 텍스트를 우선한다.
export function parseImportDocuments(args: {
  ciPlFile?: File | null;
  plFile?: File | null;
  expenseFile?: File | null;
  expenseText?: string;
}): Promise<ImportParseResult> {
  const form = new FormData();
  if (args.ciPlFile) form.append("ci_pl_file", args.ciPlFile);
  if (args.plFile) form.append("pl_file", args.plFile);
  if (args.expenseFile) form.append("expense_file", args.expenseFile);
  if (args.expenseText) form.append("expense_text", args.expenseText);
  return fetch(`${API_BASE}/api/import-cost/parse`, { method: "POST", body: form }).then(
    async (res) => {
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`파싱 실패 ${res.status}: ${detail}`);
      }
      return res.json();
    },
  );
}

export function deleteImportDocument(
  shipmentId: number,
  docId: number,
): Promise<{ deleted: boolean; id: number }> {
  return fetchApi(`/api/import-cost/shipments/${shipmentId}/documents/${docId}`, {
    method: "DELETE",
  });
}

// ══════════════════════════════════════════════════════════════════
// 원가 메뉴 — D-CPP-53 / 계약 `docs/PLAN_cost-menu-standard-cost.md` (S1: 부자재 층)
//
// ★단가는 **`string | null`**이다 — 숫자로 좁히면 `null`(미입력)이 `0`으로 접힌다.
//   0=미입력 혼동이 기존 `cost_price` 스키마의 결함이고, 새 층에서 재생산하면 이 층을
//   만들 이유가 없다(계약 §2-7). 화면은 `null`을 「—」로 그린다.
// ══════════════════════════════════════════════════════════════════
export interface CostPriceShipmentRef {
  id: number;
  hbl_no: string;
  declaration_date: string | null;
  item_name: string;
  quantity: string | null;
}

/** 단가 행 ↔ 원장 라인 **조회 시점 재검사** (적대 리뷰 1R P1).
 *
 * ★단가는 연결 시점 값을 복사해 보존한다 — 그건 의도다(근거 보존). 결함은 그 보존값이
 * **어긋난 뒤에도** 아무 표시 없이 「최신 확정 로트 단가」 자리를 차지하던 것이다. 이 칸이
 * 그 자백이다: `ok=false`면 화면이 **왜** 어긋났는지 말하고, 그 행은 최신 단가에서 빠진다. */
export interface CostLedgerCheck {
  status:
    | "manual"
    | "ok"
    | "missing" // 원장 라인이 사라졌다(고아 행)
    | "unconfirmed" // 수입건 확정 해제(reopen) — 원장은 단가를 지웠다
    | "item_mismatch" // 같은 id가 다른 품목을 가리킨다(rowid 재사용)
    | "changed"; // 원장 재확정으로 값이 달라졌다
  ok: boolean;
  label: string;
  detail: string;
  counts_as_evidence: boolean;
  refreshable: boolean;
  ledger_unit_price_ex_vat: string | null;
  ledger_unit_price_inc_vat: string | null;
  ledger_item_name: string | null;
}

export interface CostMaterialPrice {
  id: number;
  material_id: number;
  source: "ledger" | "manual";
  import_invoice_line_id: number | null;
  supplier: string | null;
  /** 연결 «당시» 원장 품목명 — 지금 값과 다르면 라인 id가 재사용된 것이다. */
  linked_item_name: string | null;
  linked_shipment_id: number | null;
  unit_price_ex_vat: string | null;
  unit_price_inc_vat: string | null;
  effective_date: string | null;
  note: string | null;
  shipment: CostPriceShipmentRef | null;
  ledger_check: CostLedgerCheck;
}

export interface CostMaterial {
  id: number;
  name: string;
  unit: string | null;
  category: string | null;
  status: "unconfirmed" | "approved";
  excel_label: string | null;
  /** ★엑셀 원가 정본의 **참고값** — 단가가 «아니다»(계약 §3 금지선).
   *
   * prod 실측(2026-08-23): 단가 보유 종 **1/129** vs 참고값 보유 종 **128/129**. 이 칸이
   * 응답에 없던 동안 화면은 「원장 연결 또는 수동 입력 필요」라고만 말해 **가장 싼 길
   * (레시피 탭의 「채택」)을 감추고 사람을 더 비싼 일로 보냈다.**
   * ★`latest_price_*`와 절대 안 섞는다 — 참고값이 단가 자리에 앉으면 그게 §3 위반이다. */
  excel_ref_price: string | null;
  match_rule: string | null;
  form_factor: string | null;
  part: string | null;
  note: string | null;
  /** 근거로 «세는» 로트 수 = 원장 파생 + 재검사 통과분. 어긋난 행은 여기 안 들어간다. */
  lot_count: number;
  price_count: number;
  /** 원장과 어긋난 연결 수. 0이 아니면 화면이 「최신 단가에서 왜 빠졌나」를 말해야 한다. */
  stale_count: number;
  latest_price_ex_vat: string | null;
  latest_price_inc_vat: string | null;
  /** 이 «부가세 포함» 값이 저장된 값이 아니라 `ex × 1.1`로 만든 값인가 (D-CPP-62 S1). */
  latest_price_inc_derived: boolean;
  latest_price_source: "ledger" | "manual" | null;
  /** 적용된 채택 규칙 — 지금은 항상 `"latest"`(최신 로트). **FIFO가 아니다**(D-CPP-60). */
  price_rule: string;
  /** 관측 로트 구간(ex_vat) 하한 — ledger·유효분만. 재고 원장(C1) 가동 전이라 층1은 이
   *  구간 안에서 «최신 로트»만 고른다는 사실을 화면이 자백한다(계약 §4-⑥). */
  lot_price_min: string | null;
  lot_price_max: string | null;
  /** 구간에 «폭»이 있는가(로트 2건 이상 & 값이 다름). false면 구간을 지어내지 않는다. */
  lot_price_has_span: boolean;
  /** 채택은 원장 값인데 더 늦은 수동 입력이 있다(계약 §2-5 자백). */
  price_conflict: boolean;
  price_conflict_price_id: number | null;
  prices: CostMaterialPrice[];
  /** ★이 부자재가 «어느 제품에 들어가는가» (Jino 2026-08-24). 빈 배열은 «아직 어느
   *  레시피도 안 쓴다»는 **사실**이지 미상이 아니다. */
  used_by: CostMaterialUsage[];
  used_by_count: number;
}

/** 부자재 → 그 종을 쓰는 레시피 한 줄. */
export interface CostMaterialUsage {
  recipe_id: number;
  product_name: string;
  form_factor: string | null;
  /** ★승인 여부를 같이 싣는다 — 「들어간다」만으로는 **계산에 쓰이는지**를 모른다(계약 §2-2). */
  status: "draft" | "approved";
  quantity: string | null;
}

export interface CostLedgerSuggestion {
  line_id: number;
  item_name: string;
  material_id: number | null;
  reason: string;
  candidates: number[];
  ambiguous: boolean;
  unmatched: boolean;
}

export interface CostLedgerMaterialLine {
  line_id: number;
  shipment_id: number;
  hbl_no: string;
  declaration_date: string | null;
  item_name: string;
  /** `"material"`(부자재) | `"product"`(수입 완제품). 계약 D-CPP-61로 후자도 실린다. */
  line_type: string;
  quantity: string | null;
  unit_cost_ex_vat: string | null;
  unit_cost_inc_vat: string | null;
  allocated_cost_krw: string | null;
  linked_material_id: number | null;
  linked_material_name: string | null;
  linked_price_id: number | null;
  /** 이 라인이 속한 수입건의 «지금» 상태. `confirmed`가 아니면 원장은 단가를 지운 상태다. */
  shipment_status: string;
  /** 붙어 있는 단가 행의 재검사 결과(안 붙었으면 null). */
  linked_price_check: CostLedgerCheck | null;
  suggestion: CostLedgerSuggestion;
}

export interface CostSetting {
  key: string;
  value: string;
  confirmed: boolean;
  note: string | null;
  updated_at: string | null;
}

export function fetchCostMaterials(): Promise<{ items: CostMaterial[] }> {
  return fetchApi("/api/cost/materials");
}

/**
 * 원장 라인 목록.
 *
 * ★`includeProducts`가 참이면 수입 완제품(`product`) 라인도 함께 온다(계약 D-CPP-61).
 * 기본이 거짓인 이유는 prod에 `product` 라인이 150건이라 그냥 열면 부자재 8건이 그 안에
 * 파묻히기 때문이다. **이미 연결된 `product` 라인은 이 값과 무관하게 항상 온다** — 어긋난
 * 연결이 화면에서 사라지면 그게 1R P1-1이 고친 병의 재발이다.
 */
export function fetchCostLedgerMaterialLines(
  includeProducts = false,
): Promise<{
  items: CostLedgerMaterialLine[];
}> {
  return fetchApi(
    `/api/cost/ledger-material-lines${includeProducts ? "?include_products=true" : ""}`,
  );
}

export function fetchCostSettings(): Promise<{ items: CostSetting[] }> {
  return fetchApi("/api/cost/settings");
}

/** 원장 라인 → 부자재 종 **사람이 하는 확정**(계약 §5-2). 제안은 스스로 링크하지 않는다. */
export function linkCostLedgerPrice(
  materialId: number,
  importInvoiceLineId: number,
): Promise<{ linked_price_id: number; material: CostMaterial }> {
  return fetchApi(`/api/cost/materials/${materialId}/prices/link`, {
    method: "POST",
    body: JSON.stringify({ import_invoice_line_id: importInvoiceLineId }),
  });
}

/** 어긋난 원장 단가 행을 **원장 현재값으로 다시 맞춘다**(적대 리뷰 1R P1-2).
 *
 * 이게 없으면 환율 정정 후 재확정된 로트를 화면이 영영 못 따라간다 — 재연결은 유일 제약
 * 때문에 409이기 때문이다. 품목이 달라진 행은 백엔드가 거부한다(해제 후 사람이 재연결). */
export function refreshCostLedgerPrice(
  materialId: number,
  priceId: number,
): Promise<{ price_id: number; was: CostLedgerCheck; material: CostMaterial }> {
  return fetchApi(`/api/cost/materials/${materialId}/prices/${priceId}/refresh`, {
    method: "POST",
  });
}

export function deleteCostMaterialPrice(
  materialId: number,
  priceId: number,
): Promise<{ deleted: boolean; id: number; material: CostMaterial }> {
  return fetchApi(`/api/cost/materials/${materialId}/prices/${priceId}`, {
    method: "DELETE",
  });
}

export function createCostMaterial(body: {
  name: string;
  unit?: string | null;
  category?: string | null;
  match_rule?: string | null;
}): Promise<CostMaterial> {
  return fetchApi("/api/cost/materials", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchCostMaterial(
  materialId: number,
  body: Partial<Pick<CostMaterial, "name" | "unit" | "category" | "status" | "match_rule" | "excel_label" | "note">>,
): Promise<CostMaterial> {
  return fetchApi(`/api/cost/materials/${materialId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** 국내 구매 부자재 등 원장 파생이 불가한 종의 단가(계약 §4 하이브리드 ②). */
export function addCostManualPrice(
  materialId: number,
  body: {
    unit_price_ex_vat?: string | null;
    unit_price_inc_vat?: string | null;
    supplier?: string | null;
    effective_date?: string | null;
    note?: string | null;
  },
): Promise<{ price_id: number; material: CostMaterial }> {
  return fetchApi(`/api/cost/materials/${materialId}/prices`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ══════════════════════════════════════════════════════════════════
// 원가 메뉴 — 평가방법 확인·변경 이력 + 자동 갱신 (D-CPP-60)
// ══════════════════════════════════════════════════════════════════

/** 설정 변경 이력 한 줄 — **값이 안 바뀌어도** 「확인했다」는 사건으로 남는다(계약 §4-②). */
export interface CostSettingHistoryRow {
  id: number;
  key: string;
  old_value: string | null;
  new_value: string;
  old_confirmed: boolean | null;
  new_confirmed: boolean;
  actor: string | null;
  note: string | null;
  created_at: string | null;
}

export function fetchCostSettingHistory(): Promise<{ items: CostSettingHistoryRow[] }> {
  return fetchApi("/api/cost/settings/history");
}

/** 설정 1건 확인·변경. **값이 그대로여도** `value_changed:false`로 그 사실을 자백한다 —
 *  화면은 「값은 그대로 · 확인 기록 1건 추가」라고 말해야 한다(§2-6 침묵 금지). */
export function updateCostSetting(
  key: string,
  body: { value?: string; confirmed?: boolean; actor?: string | null; note?: string | null },
): Promise<CostSetting & { value_changed: boolean; confirmed_changed: boolean }> {
  return fetchApi(`/api/cost/settings/${key}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 단가 자동 갱신의 «사건»(§7-4) — 사람이 만든 짝(연결)의 반복만 한다. 값 갱신·미갱신·
 *  실패·대기 넷 다 이 모양이다. */
export interface CostAutoRefreshEntry {
  id: number;
  run_id: number;
  outcome: "linked" | "unchanged" | "failed" | "queued";
  material_id: number | null;
  material_name: string | null;
  price_id: number | null;
  import_invoice_line_id: number | null;
  hbl_no: string | null;
  item_name: string | null;
  old_price_ex_vat: string | null;
  new_price_ex_vat: string | null;
  message: string | null;
  created_at: string | null;
}

/** 자동 갱신 회전 1건. **`updated=0`이어도 행이 남는다** — 그게 「자동이 살아 있다」는
 *  유일한 증거다(§2-6). 목록이 통째로 비면 「한 번도 안 돌았다」다. */
export interface CostAutoRefreshRun {
  id: number;
  trigger: "event" | "cron" | "manual";
  started_at: string | null;
  finished_at: string | null;
  checked: number;
  updated: number;
  failed: number;
  queued: number;
  note: string | null;
  entries: CostAutoRefreshEntry[];
}

export function fetchCostAutoRefreshRuns(
  limit = 20,
): Promise<{ items: CostAutoRefreshRun[] }> {
  return fetchApi(`/api/cost/auto-refresh/runs?limit=${limit}`);
}

/** 「연결 대기」 큐 — 자동이 **안 건드리고** 사람에게 올린 라인(계약 §7-4 불변식: 첫 연결은
 *  영원히 사람이다. 이 큐에 «자동 연결» 버튼을 달지 않는다). */
export function fetchCostAutoRefreshQueue(): Promise<{ items: CostAutoRefreshEntry[] }> {
  return fetchApi("/api/cost/auto-refresh/queue");
}

/** 「지금 검사」 버튼 — 크론(일일 sweep)을 기다리지 않고 1회전을 즉시 돈다. */
export function runCostAutoRefreshNow(): Promise<{
  run_id: number;
  trigger: "manual";
  checked: number;
  updated: number;
  failed: number;
  queued: number;
}> {
  return fetchApi("/api/cost/auto-refresh/run", { method: "POST" });
}

// ══════════════════════════════════════════════════════════════════
// 원가 메뉴 S2 — 레시피·표준원가 (D-CPP-53 / 계약 A′ §5-3 탭2·탭3)
// ══════════════════════════════════════════════════════════════════

/** 표준원가 계산 내역 한 줄 — «계산되는 방법이 나오는» 화면의 원료(계약 §7 합격 4). */
export interface CostStandardLine {
  label: string;
  quantity: string | null;
  unit_price_ex_vat: string | null;
  unit_price_inc_vat: string | null;
  amount_ex_vat: string | null;
  amount_inc_vat: string | null;
  price_status: string;
  inc_derived: boolean;
  price_source: string | null;
  price_note: string | null;
  material_id: number | null;
  usable: boolean;
  /** ★엑셀 참고값 — **채택 전이라 단가가 아니다.** 합계(`std_cost_*`·`partial_*`)엔
   * 절대 안 들어간다(계약 §3 금지선). 화면은 별도 열로 그리되 합계에서 뺀다. */
  excel_ref_price: string | null;
}

/** ★`std_cost_*`가 `null`인 것과 `"0"`인 것은 다르다 — `reason`이 왜 없는지 말한다(§2-7). */
export interface CostStandard {
  computable: boolean;
  std_cost_ex_vat: string | null;
  std_cost_inc_vat: string | null;
  reason: string | null;
  unresolved: string[];
  partial_ex_vat: string | null;
  partial_inc_vat: string | null;
  line_count: number;
  lines: CostStandardLine[];
}

/** 원가표 품목 ↔ (상품명 × 폼팩터) 매칭의 **근거**. 제안이지 확정이 아니다. */
export interface CostRecipeMatch {
  match_reason: string | null;
  candidates: string[];
  cost_price_mode: string | null;
  cost_table_item: string | null;
  cost_table_section: string | null;
  excel_total_inc_vat: string | null;
  sku_count: number | null;
  option_count: number | null;
}

/** 원가표 항목 1건 — 사람이 고를 «목록»의 한 줄 (계약 §0-E-3, D-CPP-59). */
export interface CostTableItemRow {
  id: number;
  section: string;
  item_name: string;
  form_factor: string | null;
  recipe_kind: string;
  total_inc_vat: string | null;
  row_number: number | null;
  anomalies: string | null;
  line_count: number;
  /** ★가격 매칭이 걸린 항목 — **제안이지 확정이 아니다**(계약 §0-E-10-4). */
  suggested: boolean;
  picked: boolean;
}

export interface CostTableItemList {
  recipe_id: number;
  form_factor: string | null;
  /** 제안의 근거 — 화면이 「왜 이게 위에 있나」를 말할 수 있어야 제안을 확정으로 안 읽는다. */
  cost_price_mode: string | null;
  suggested_count: number;
  items: CostTableItemRow[];
}

/**
 * 픽 상태 — **네 상태를 갈라서** 받는다 (계약 합격 19).
 *
 * ★`none`(아직 아무도 안 봄)과 `absent`(사람이 없다고 확인함)를 가르는 것이 이 필드의
 * 존재 이유다. 둘을 한 모양으로 그리면 화면이 침묵을 판정으로 읽는다.
 */
export interface CostRecipePick {
  state: "picked" | "absent" | "pin_lost" | "pin_ambiguous" | "none";
  item_id: number | null;
  item_name: string | null;
  section: string | null;
  item_total_inc_vat: string | null;
  picked_at: string | null;
  absent_confirmed_at: string | null;
  absent_note: string | null;
}

export interface CostRecipe {
  id: number;
  product_name: string;
  form_factor: string | null;
  status: string; // draft | approved
  source: string;
  /** `"assembly"` | `"imported_goods"` — 픽이 원가표 항목에서 옮겨 온다(D-CPP-61). */
  recipe_kind: string;
  /**
   * 폼팩터를 «어떻게» 얻었나 — `"rule"` | `"fallback"` | null(출처 미상).
   *
   * ★저장된 값이 없으면 백엔드가 **파생**한다(`form_source_for`) — `bar`를 내는 양성
   * 규칙이 0개라 `form_factor === "bar"`는 필연적으로 폴백의 산물이기 때문이다. 그래서
   * 이미 있는 레시피도 재업로드 없이 「추정」을 말할 수 있다.
   */
  form_source: string | null;
  anomaly_flag: string | null;
  approved_at: string | null;
  match: CostRecipeMatch | null;
  line_count: number;
  link_count: number;
  standard: CostStandard;
  picked: CostRecipePick;
  links?: { internal_sku: string; status: string; source: string }[];
}

export interface CostBoardRow {
  internal_sku: string;
  product_name: string | null;
  recipe_id: number;
  recipe_product_name: string;
  form_factor: string | null;
  /**
   * 폼팩터를 «어떻게» 얻었나 — `"rule"`이면 규칙이 걸린 것, `"fallback"`이면 아무 규칙도
   * 안 걸려 `bar`로 **단정**한 것이다(계약 D-CPP-61 §4-Q2). `null`은 이 필드가 생기기
   * 전에 저장된 레시피 — 「모른다」이지 「규칙이 걸렸다」가 아니다.
   */
  form_source: string | null;
  /** `"assembly"` | `"imported_goods"` — 픽이 원가표 항목에서 옮겨 온다. */
  recipe_kind: string;
  recipe_status: string;
  link_status: string;
  std_cost_ex_vat: string | null;
  std_cost_inc_vat: string | null;
  /** 현 `product_master.cost_price` — **읽기 전용 대조값**이다(계약 §3 금지선). */
  current_cost_price: string | null;
  gap_pct: number | null;
  /** 엑셀 표준(원가표 품목 총액, VAT 포함) — **대조값**이다. 계산에 유입되지 않는다. */
  excel_total_inc_vat: string | null;
  /** 표준원가 ↔ 엑셀 표준의 격차 %. 둘 다 VAT 포함 축이라 축이 섞이지 않는다. */
  excel_gap_pct: number | null;
  reason: string | null;
}

export interface CostBoard {
  items: CostBoardRow[];
  sku_count: number;
  computed_count: number;
  uncomputed_count: number;
  recipe_count: number;
  approved_recipe_count: number;
}

export interface CostImportReportRow {
  product_name: string;
  form_factor: string | null;
  action: string;
  reason: string;
  sku_count: number;
  line_count?: number;
  anomaly_flag?: string | null;
}

export interface CostImportResult {
  recipes_created: number;
  recipes_updated: number;
  skipped_approved: number;
  unmatched: number;
  materials_seen: number;
  cost_table_recipes: number;
  cost_table_anomalies: string[];
  mapping_options: number;
  mapping_anomalies: string[];
  groups: number;
  report: CostImportReportRow[];
  /** 이번 업로드가 «움직인» 절반들. 한쪽만 올렸을 때 한 항목이다(D-CPP-56 후속). */
  updated_halves?: string[];
  /** 이번 업로드가 «손대지 않은» 것 — 조용한 반쪽 갱신을 막는 자백 필드. */
  untouched?: string[];
  /** 사람이 고른 픽이 붙어 있어 가격 매칭이 «건드리지 않은» 레시피 수 (D-CPP-59 · 합격 20). */
  skipped_pinned?: number;
  /** 저장된 원가표 항목 수 — 픽 목록의 모수다. 0이면 고를 것이 없다는 뜻이다. */
  cost_table_items?: number;
  /** 핀 재해석 결과. `lost`·`ambiguous`가 0이 아니면 **화면이 말해야 한다**(조용한 소실 금지). */
  pins?: { relinked: number; lost: number; ambiguous: number };
  /** 부자재 참고값 미러 리포트 (D-CPP-62 S1 → S2 화면 배선).
   *  `refreshed` = 파일 값으로 갱신된 종(옛값→새값) · `conflicted` = 파일이 한 종에
   *  두 값 이상을 말해 아무것도 안 고르고 보류한 종. **옵셔널** — 구버전 백엔드 응답 방어. */
  material_refs?: {
    refreshed: { name: string; old: string | null; new: string | null }[];
    refreshed_count: number;
    conflicted: { name: string; values: (string | null)[]; kept: string | null }[];
    conflicted_count: number;
  };
}

export function fetchCostRecipes(formFactor?: string): Promise<{ items: CostRecipe[] }> {
  const q = formFactor ? `?form_factor=${encodeURIComponent(formFactor)}` : "";
  return fetchApi(`/api/cost/recipes${q}`);
}

export function fetchCostRecipe(recipeId: number): Promise<CostRecipe> {
  return fetchApi(`/api/cost/recipes/${recipeId}`);
}

/** 두 엑셀 업로드 → 초안. **아무것도 승인하지 않고 단가도 만들지 않는다**(계약 §2-2·§3).
 *
 * ★`headers`를 **빈 객체로 덮는다** — `fetchApi`의 기본 `Content-Type: application/json`이
 * 그대로 가면 multipart 경계(boundary)가 안 붙어 서버가 파일을 못 읽는다.
 *
 * ★**한쪽만 보내도 된다**(Jino 2026-08-24). 안 고른 슬롯은 **아예 안 붙인다** —
 * 빈 문자열이나 빈 Blob을 붙이면 서버가 「올렸는데 파싱 실패」로 읽어 400이 난다. */
export function importCostRecipes(
  costFile: File | null,
  mappingFile: File | null,
): Promise<CostImportResult> {
  const form = new FormData();
  if (costFile) form.append("cost_file", costFile);
  if (mappingFile) form.append("mapping_file", mappingFile);
  return fetchApi("/api/cost/recipes/import", {
    method: "POST",
    headers: {},
    body: form,
  });
}

/** Jino가 눈으로 보고 누르는 확정(계약 §2-2). 이 순간부터 표준원가가 저장된다. */
export function approveCostRecipe(recipeId: number): Promise<CostRecipe> {
  return fetchApi(`/api/cost/recipes/${recipeId}/approve`, { method: "POST" });
}

export function unapproveCostRecipe(recipeId: number): Promise<CostRecipe> {
  return fetchApi(`/api/cost/recipes/${recipeId}/unapprove`, { method: "POST" });
}

/** 엑셀 참고값 → `manual` 단가로 **채택**(계약 §3이 허용한 유일한 유입 경로).
 *
 * ★이미 단가가 있는 종은 백엔드가 건너뛴다 — 원장 파생 단가를 엑셀로 덮지 않는다(§2-1). */
export function adoptCostExcelPrices(
  recipeId: number,
  note?: string,
): Promise<{
  adopted: string[];
  skipped_has_price: string[];
  skipped_no_ref: string[];
  recipe: CostRecipe;
}> {
  return fetchApi(`/api/cost/recipes/${recipeId}/adopt-excel-prices`, {
    method: "POST",
    body: JSON.stringify({ note: note ?? null }),
  });
}

/**
 * 이 레시피에 붙일 수 있는 **원가표 항목 전건 목록** (계약 합격 18 · D-CPP-59).
 *
 * ★개정 4 전까지 화면은 「후보 N건 — 사람이 고른다」고 말하면서 고를 길을 안 줬다.
 * 이 호출이 그 길이고, 지워지면 화면의 그 문장이 다시 거짓이 된다.
 */
export function fetchCostTableItems(recipeId: number): Promise<CostTableItemList> {
  return fetchApi(`/api/cost/recipes/${recipeId}/cost-table-items`);
}

/** 사람이 고른 원가표 항목을 구성으로 확정한다 — **재업로드 없이 즉시**(합격 18). */
export function pickCostTableItem(
  recipeId: number,
  itemId: number,
): Promise<{ recipe: CostRecipe }> {
  return fetchApi(`/api/cost/recipes/${recipeId}/pick-cost-table-item`, {
    method: "POST",
    body: JSON.stringify({ item_id: itemId }),
  });
}

/** 픽을 되돌린다 — 되돌릴 길이 없으면 사람이 고르기를 주저한다. */
export function unpickCostTableItem(recipeId: number): Promise<{ recipe: CostRecipe }> {
  return fetchApi(`/api/cost/recipes/${recipeId}/unpick-cost-table-item`, {
    method: "POST",
  });
}

/** 「원가표에 없음」을 사람이 **명시적으로** 확인한다 (합격 19 — 침묵과 구별되는 상태). */
export function confirmCostTableAbsent(
  recipeId: number,
  note?: string,
): Promise<{ recipe: CostRecipe }> {
  return fetchApi(`/api/cost/recipes/${recipeId}/confirm-cost-table-absent`, {
    method: "POST",
    body: JSON.stringify({ note: note ?? null }),
  });
}

export function fetchCostBoard(): Promise<CostBoard> {
  return fetchApi("/api/cost/board");
}

// ════════════════════════════════════════════════════════════════════
// RG(로켓그로스 2P) «자기 화면» — 옵션별 판매일 축 손익
// 계약 `docs/contracts/CONTRACT_2p_own_screens.md`(D-CPP-54) §1-A-2.
//
// ★새 계산은 없다. 백엔드 `rg_daily_pnl.rg_option_pnl()`이 대시보드 RG 행과 «같은 다섯 항»을
//   날짜×옵션 grain으로 분해해 줄 뿐이고, 여기선 그것을 그대로 받는다.
// ★`Decimal`은 문자열로 온다(이 저장소 관례) — 화면에서 `Number()`로 바꿔 쓴다.
// ════════════════════════════════════════════════════════════════════

/** 상품(옵션) 행 — 상품에 «붙일 수 있는» 것만 들어온다. 납부세액·보관비는 여기 없다. */
export interface RgOptionPnlRow {
  vendor_item_id: string;
  name: string | null;
  revenue: string;
  units_sold: number;
  order_count: number;
  fee_logistics: string | null;   // null = 물류비 단가를 모른다(0이 아니다)
  fee_sale_fee: string | null;    // null = 요율을 못 쟀다
  fee_total: string | null;
  cost: string | null;
  has_cost: boolean;
  ad_spend: string;
  net_profit: string | null;      // null = 원가 게이트 미달 또는 원장 축 폴백
}

/** 상품에 «못 붙이는» 것 — 0으로 채우지 않고 여기로 모아 자백한다. */
export interface RgAccountCommon {
  period_fees: string;             // 보관비·반품비 일할 — 판매일에 안 붙는다(계약 §8-5)
  payable_vat: string;             // 납부세액 — 계정 단위
  revenue_axis_gap: string;        // 요약축 − Σ옵션축. 0이 아닐 수 있다
  ad_unallocated: string;          // 어느 판매경로인지 모르는 광고비 — 대시보드 RG 행엔 안 실린다
  ad_unallocated_options: number;
  fee_axis_fallback_gap: string;   // 원장 축 폴백 창에서 옵션 분해가 못 덮은 몫
  cost_unmapped_revenue: string;
  fee_unmapped_revenue: string;
}

/** 보존식 — 이 화면이 대시보드와 «같은 말»을 하는지 코드가 스스로 대조한 결과. */
export interface RgConservation {
  // ★다섯 칸이 «함께» null이 된다 — 원가 커버리지 게이트 미달 창에서 백엔드가 그렇게 낸다
  //   (`rg_daily_pnl.py:194-249`). 적대 리뷰 2R P2: 앞의 두 칸만 non-nullable로 선언해 뒀더니
  //   **타입이 거짓말을 했고**, 그게 1R P1(「모름」을 「0원」으로 그린 결함)이 숨을 수 있었던
  //   자리다. 런타임은 `cell()`이 막지만 타입이 거짓이면 다음 호출자가 가드 없이 쓴다.
  options_net_sum: string | null;
  account_common_sum: string | null;
  computed_total_net: string | null;
  reference_net: string | null;    // 대시보드 RG 행이 낸 값(compute_rg_summary_row)
  diff: string | null;             // 0으로 숨기지 않는다
  // ★3상태다 — true(원 단위 일치) / false(어긋남) / **null(판정할 수 없다)**.
  //   원가 게이트가 미달인 창은 순이익 자체를 안 내므로 대조할 것이 없다. `boolean`으로
  //   선언해 두면 `null`이 falsy로 접혀 화면이 「어긋남」을 «단정»한다 — 실제로 그랬다
  //   (2026-08-23 라이브, 08-22 창). 「모름」과 「아니다」는 다른 말이다.
  ok: boolean | null;
}

export interface RgOptionPnlResponse {
  account: string;
  date_from: string;
  date_to: string;
  options: RgOptionPnlRow[];
  account_common: RgAccountCommon;
  conservation: RgConservation;
  // ── 자백 칸 (계약 §4 ⓔⓕ) ──
  commission_axis: string | null;
  rate: string | null;
  rate_basis: string | null;
  rate_cycles: string | null;
  fee_coverage: string | null;
  cost_coverage: string | null;
  option_axis_days: string | null;
  option_axis_complete: boolean;
  cost_trustworthy: boolean;
  fee_trustworthy: boolean;
  reconciliation: {
    cycle_from: string; cycle_to: string;
    computed: string; actual: string; diff: string; diff_pct: string | null;
  } | null;
  ad_spend_warning: string | null;  // vendor_id를 못 찾았을 때 «0이 아니라 미상»임을 말한다
}

/** RG 옵션별 손익. 기본 창은 백엔드가 KST 어제 단일일로 잡는다(Jino 원문 「어제 …」). */
export function fetchRgOptionPnl(
  account: string,
  dateFrom?: string,
  dateTo?: string,
): Promise<RgOptionPnlResponse> {
  const q = new URLSearchParams({ account });
  if (dateFrom) q.set("date_from", dateFrom);
  if (dateTo) q.set("date_to", dateTo);
  return fetchApi(`/api/coupang/rg/option-pnl?${q.toString()}`);
}

// ──────────────────────────────────────────────
// PAO 스코프 (D-NAO-244) — 「어떤 캠페인·광고그룹을 돌릴지 + 그 성과」
//
// Jino 원문 2026-08-24: *"ohisell에 PAO 메뉴를 만들어서 어떤 캠페인 - 광고그룹 을 돌릴지,
// 그 성과는 어떻게 나오는지 보여주는 대시보드를 같이 만들자"*
//
// ★gross_profit이 null이면 «0원»이 아니라 «모름»이다(profit_status='bep_unknown').
//   화면이 이걸 0으로 그리면 적자 그룹이 손익분기로 보인다 — 타입에서부터 갈라 둔다.

export type PaoScopeRole = "accel" | "boundary" | "brake";

export interface PaoScopeAdgroup {
  adgroup_id: string;
  name: string;
  status: string | null;
  /** 지금 엔진에 맡겨져 있는가(행이 있고 enabled) */
  in_scope: boolean;
  scope_role: PaoScopeRole | null;
  scope_enabled: boolean | null;
  cost: number;
  imp: number;
  clk: number;
  conv_amt: number;
  roas: number | null;
  bep_roas: number | null;
  /** ★있는 그대로(보정 없음). null = 모름(BEP 미해석) — 0원과 구분할 것 */
  gross_profit: number | null;
  /** 보정계수 구간 양끝을 적용한 값 — «얼마나 모르는지»를 화면이 같이 보이게 한다 */
  gross_profit_low: number | null;
  gross_profit_high: number | null;
  profit_status: "ok" | "bep_unknown";
}

export interface PaoScopeCampaign {
  campaign_id: string;
  name: string;
  campaign_type: string | null;
  optimizer: string;
  auto_operate: boolean;
  /** 스코프 행이 하나라도 있으면 true — 이때 캠페인 레벨 액션(예산)은 hold된다 */
  has_scope: boolean;
  scoped_count: number;
  adgroup_count: number;
  cost: number;
  imp: number;
  clk: number;
  conv_amt: number;
  roas: number | null;
  gross_profit: number | null;
  gross_profit_low: number | null;
  gross_profit_high: number | null;
  adgroups: PaoScopeAdgroup[];
}

export interface PaoScopeRoster {
  window: { date_from: string; date_to: string; days: number };
  /** ★단일 value가 아니라 «구간»이다 — 하나만 집어 들면 그게 사실처럼 읽힌다.
   *  하한 = inflowPath 「광고>」5종 근거 · 상한 = 채널 매출 전액을 광고 공으로 돌린 «가정» */
  correction_factor: { low: number; high: number; source: string | null };
  totals: Record<string, number | null>;
  campaigns: PaoScopeCampaign[];
}

export function fetchPaoScopeRoster(params: { campaignId?: string; days?: number } = {}): Promise<PaoScopeRoster> {
  const q = new URLSearchParams();
  if (params.campaignId) q.set("campaign_id", params.campaignId);
  if (params.days) q.set("days", String(params.days));
  const qs = q.toString();
  return fetchApi<PaoScopeRoster>(`/api/naver/ad/scope/roster${qs ? `?${qs}` : ""}`);
}

/** 스코프 행 upsert — 이 호출은 **엔진을 켜지 않는다**(auto_operate는 별도 스위치). */
export function putPaoScopeAdgroup(body: {
  campaign_id: string;
  adgroup_id: string;
  role: PaoScopeRole | null;
  enabled: boolean;
  memo?: string | null;
}): Promise<{ campaign_id: string; adgroup_id: string; role: PaoScopeRole | null; enabled: boolean; memo: string | null }> {
  return fetchApi(`/api/naver/ad/scope/adgroup`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** 스코프 행 삭제. ★마지막 행을 지우면 캠페인이 «전 그룹 대상»으로 돌아간다 —
 *  일부만 끄려면 삭제가 아니라 enabled=false다(결과가 정반대). */
export function deletePaoScopeAdgroup(
  campaignId: string,
  adgroupId: string,
): Promise<{ deleted: boolean; remaining_rows: number; campaign_now_unrestricted: boolean }> {
  const q = new URLSearchParams({ campaign_id: campaignId, adgroup_id: adgroupId });
  return fetchApi(`/api/naver/ad/scope/adgroup?${q.toString()}`, { method: "DELETE" });
}

// ─────────────────────────────────────────────────────────────────────────────
// OTAO 발주 로스터 — 계약 `CONTRACT_inventory_unified.md` §4 S1 (D-INV-1~4)
//
// ★3칸을 합산한 파생 총계 필드를 **여기에 만들지 않는다**(계약 §3-9 금지선). 합치는 순간
//   ②픽업 결정이 화면에서 사라진다. 백엔드도 그 필드를 안 준다 — 타입에도 두지 않는다.
// ─────────────────────────────────────────────────────────────────────────────

export interface OtaoRosterRow {
  product_code: string;
  /** 발주 누계 — 통관 원장이 덮는 창 «안»의 정본 발주분만 */
  ordered: number;
  /** 픽업 누계 — 통관 원장 × 품목명 사전 */
  picked: number;
  /** 예약 잔량 = ordered − picked. ★음수가 정상적으로 나온다(창 어긋남 신호) — 0으로 깎지 말 것 */
  reserved: number;
  /** 창보다 이른 발주분. 잔량 계산에서 «뺀» 몫이라 화면이 이것을 따로 자백해야 한다 */
  out_of_window_ordered: number;
  last_order_date: string | null;
  /** 이 SKU가 실린 정본 발주서 «건수»(발주일수가 아니다 — 같은 날 복수 발주가 실재한다) */
  order_count: number;
}

export interface OtaoRoster {
  /** ★true = 적재를 안 돌린 것. 「발주가 0이다」와 **다른 상태**라 0을 그리면 거짓말이 된다 */
  ledger_empty: boolean;
  /** 통관 원장이 덮기 시작하는 날. null = 원장 자체가 비어 있다 */
  window_start: string | null;
  rows: OtaoRosterRow[];
  totals: Record<string, number>;
  /** 상품코드에 못 붙은 원장 품목명 — 수량과 함께. 숨기면 그만큼이 발주 누락이다(계약 §2-9) */
  unmapped: { item_name: string; quantity: number }[];
  notes: string[];
  source: {
    orders_total: number;
    orders_authoritative: number;
    orders_superseded: number;
    last_order_date: string | null;
    name_map_total: number;
    name_map_resolved: number;
  };
}

export function fetchOtaoRoster(): Promise<OtaoRoster> {
  return fetchApi<OtaoRoster>("/api/otao-po/roster");
}

// ─────────────────────────────────────────────────────────────────────────────
// S3 — 채널 통합 판매 시계열 (계약 §4 S3 · 체인 `발주예측` n=6)
//
// ★축이 발주 로스터와 **다르다**: 여기는 `product_master.internal_sku`(OHI-…), 로스터는
//   발주서의 `product_code`(GAPIP…). 둘은 prod에서 0% 겹치고 다리가 아직 없다.
//   그래서 두 표를 같은 줄에 놓지 않는다 — 이으면 「말이 되는 것처럼 보이는 거짓 대비」다.
// ─────────────────────────────────────────────────────────────────────────────

export interface OtaoSalesChannel {
  key: string;
  label: string;
  company: string;
  sell_type: string;
  source_table: string;
  /** 이 채널의 SKU 다리. 틀리면 예외가 아니라 «0%»가 나온다 */
  bridge: string;
  rows: number;
  quantity: number;
  quantity_mapped: number;
  /** 취소·반품으로 «뺀» 몫. 조용히 빼지 않는다 */
  quantity_excluded: number;
  /** ★한 채널 상품 ID가 서로 다른 상품 여러 개를 가리켜 «안 붙인» 수량. 고르면 발주 오염이다 */
  quantity_ambiguous: number;
  /** ★수량 기준. null = 분모가 0이라 «잴 수 없음»(0%가 아니다) */
  mapping_rate: number | null;
  days_with_rows: number;
  /** ★false = 「판매 0」과 「데이터 없음」을 가를 근거가 이 채널엔 없다 */
  missing_day_evidence: boolean;
  days_collected_zero: string[];
  days_no_data: string[];
}

export interface OtaoSalesRow {
  internal_sku: string;
  product_name: string | null;
  total: number;
  by_channel: Record<string, number>;
  /** ★일별 판매수량. `OtaoSales.dates`와 **자리로** 대응한다 — 이게 「시계열」의 본체다 */
  series: number[];
}

export interface OtaoSales {
  window_start: string;
  window_end: string;
  days: number;
  /** 창의 날짜 축. `rows[*].series`가 이 배열과 자리로 대응한다 */
  dates: string[];
  channels: OtaoSalesChannel[];
  rows: OtaoSalesRow[];
  daily: { date: string; total: number; by_channel: Record<string, number> }[];
  /** 상품코드에 못 붙은 판매 수량(채널별). 숨기면 그만큼 수요가 사라진다 */
  unmapped: { channel: string; quantity: number }[];
  /** ★발주 축과의 다리 상태. overlap=0이면 두 축을 같은 줄에 놓을 수 없다는 뜻 */
  order_axis: {
    order_axis_codes: number;
    sales_axis_skus: number;
    overlap: number;
    order_codes_reached_by_name_map: number;
    note: string;
  };
  notes: string[];
}

export function fetchOtaoSales(days = 60): Promise<OtaoSales> {
  return fetchApi<OtaoSales>(`/api/otao-po/sales?days=${days}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// S2 — 정산 창(전월 20~당월 19) 픽업 «금액» (계약 §4 S2 · 체인 `발주예측` n=7)
//
// ★금액 단위는 **CNY**다. 과세금액(원)은 관세청이 세금을 매기는 값이라 OTAO 지급액이 아니고,
//   실송금 환율은 원장에 없어(prod 12/12 NULL) 원화 환산을 하지 않는다 — 하면 우리가 안 쓰는
//   환율로 지어낸 숫자가 된다.
// ★`reconciled: null`은 **「대조 불가」라는 상태 자체**다. `false`(불일치)로 접거나 `boolean`
//   으로 타입을 좁히면 화면이 없는 사실을 말하게 된다 — 지급액 원장이 이 저장소에 없다.
// ─────────────────────────────────────────────────────────────────────────────

export interface OtaoSettlementWindow {
  /** 지급월 `YYYY-MM` — 이 달 19일에 지급한다 */
  key: string;
  /** 창 시작 = 전월 20일 */
  start: string;
  /** 창 끝 = 당월 19일 = 지급일 */
  end: string;
  shipments: number;
  lines: number;
  product_quantity: number;
  product_amount_cny: number;
  /** ★부자재. 지급액엔 들어가고 S1의 «픽업 누계» 칸엔 안 들어간다 — 두 숫자가 다른 이유 */
  material_quantity: number;
  material_amount_cny: number;
  /** 미분류(`unknown`). 판매 SKU로 접지 않는다 */
  other_quantity: number;
  other_amount_cny: number;
  total_amount_cny: number;
  shipment_ids: number[];
  /** 아직 검산을 통과하지 못한 선적. 합계엔 들어 있고 화면이 그 사실을 말한다 */
  draft_shipment_ids: number[];
  /** 창 경계 ±2일 — 이 원장엔 OTAO 픽업일이 없어 창이 밀렸을 수 있다 */
  boundary_shipment_ids: number[];
  /** ★null = 「모른다」이지 「0원 지급」이 아니다 */
  payment_actual_cny: number | null;
  difference_cny: number | null;
  /** ★null = 대조 «불가» / true = 일치 / false = 불일치. 셋은 서로 다른 상태다 */
  reconciled: boolean | null;
}

export interface OtaoSettlement {
  /** true = 통관 원장이 비어 있다. 「픽업 0」과 **다른 상태** */
  ledger_empty: boolean;
  ledger_start: string | null;
  ledger_end: string | null;
  currency: string;
  windows: OtaoSettlementWindow[];
  /** 신고일이 없어 어느 창에도 못 넣은 라인 — 0으로 덮지 않는다 */
  unassigned: { lines: number; quantity: number; amount_cny: number | null };
  totals: Record<string, number | null>;
  reconciliation: {
    payments_supplied: number;
    windows_compared: number;
    windows_matched: number;
    matched_keys: string[];
    mismatched: { key: string; expected: string; actual: string; difference: string }[];
    /** ★"none" = 대조할 «대상»이 없다. "supplied" = 지급액을 받아서 실제로 대조했다 */
    source: string;
  };
  notes: string[];
}

export function fetchOtaoSettlement(): Promise<OtaoSettlement> {
  return fetchApi<OtaoSettlement>("/api/otao-po/settlement");
}

// ─────────────────────────────────────────────────────────────────────────────
// S4 파생 현재고 — 계약 `CONTRACT_inventory_unified.md` §4 S4 · 체인 `발주예측` n=8
//
// ★`number | null`을 `number`로 좁히지 마라. 이 응답의 null은 전부 «모른다»이고 「0」이 아니다:
//     sold_quantity   = null → 판매를 이 축에 «못 붙인다»(다리 부재). 0이면 재고가 부푼다.
//     derived_*       = null → 위 때문에 파생값이 «산출 불가».
//     baseline_*      = null → 스냅샷에 그 코드가 «없다». 「재고 0」이 아니다.
//     variance_*      = null → 실사 «미실시». 오차 0이 아니다.
//   `?? 0`을 한 줄이라도 쓰면 화면이 없는 사실을 말하게 된다.
// ─────────────────────────────────────────────────────────────────────────────

export interface OtaoStockRow {
  product_code: string;
  /** t0(가장 이른 스냅샷)의 **본사** 재고. null = 스냅샷에 없다 */
  baseline_quantity: number | null;
  /** 창고 역할별 분해 — own / material / channel / excluded / unknown. 합치지 않는다(계약 §1) */
  baseline_by_role: Record<string, number | null>;
  /** t0 «이후» 통관 원장 입고 */
  inbound_quantity: number | null;
  /** ★항상 null이다 — 발주축(GAPIP)과 판매축(internal_sku)을 잇는 표가 없다 */
  sold_quantity: number | null;
  derived_quantity: number | null;
  /** 파생이 null인 이유: 'baseline' | 'sold' */
  derived_blocked_by: string | null;
  /** ★현재고가 «아니다» — 판매를 안 뺀 상한일 뿐 */
  upper_bound_if_no_sales: number | null;
  counted_quantity: number | null;
  /** 그 코드를 «언제» 셌나 — 코드마다 다를 수 있다(나눠 세는 것이 현실 경로) */
  counted_at: string | null;
  /** ★어느 창고를 셌나. 없으면 「본사 스냅샷 ↔ 다른 창고 실사」가 «오차»로 둔갑한다 */
  counted_warehouse: string | null;
  counted_warehouse_role: string | null;
  /** true = 기준 창고(본사)가 아닌 곳을 센 것 — 그 차이는 오차가 아니라 다른 축이다 */
  counted_axis_mismatch: boolean;
  latest_snapshot_quantity: number | null;
  /** ★계약 §2-7C ④가 요구하는 숫자: ECOUNT가 말한 값 − 사람이 센 값 */
  variance_vs_snapshot: number | null;
  variance_pct: number | null;
  variance_vs_derived: number | null;
}

export interface OtaoStock {
  /** true = 스냅샷을 «찍은 적 없다». 「재고 0」과 다른 상태 */
  snapshot_empty: boolean;
  snapshot_count: number;
  baseline_at: string | null;
  latest_at: string | null;
  /** 가장 «최근» 실사 시각. null = 미실시 */
  counted_at: string | null;
  /** 가장 «이른» 실사 시각 — counted_at과 다르면 회차가 나뉜 것이다 */
  counted_from: string | null;
  /** 기준 창고(본사)가 아닌 곳을 센 코드들 */
  counted_axis_mismatches: string[];
  inbound_window_start: string | null;
  /** 판매를 못 붙이는 이유 원문 — 화면이 그대로 읽는다 */
  sold_unavailable_reason: string | null;
  rows: OtaoStockRow[];
  /** 계약 §1 창고 표에 없는 이름 — 본사 재고에 합치지 않았다 */
  unknown_warehouses: { warehouse: string; quantity: number | null }[];
  totals: Record<string, number | string[] | null>;
  notes: string[];
}

export function fetchOtaoStock(): Promise<OtaoStock> {
  return fetchApi<OtaoStock>("/api/otao-po/stock");
}
